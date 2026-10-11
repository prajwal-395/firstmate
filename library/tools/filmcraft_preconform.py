"""Pre-conform camera sources for the native FilmCraft path.

Why this exists: the native Reel 22 export spends ~75-100% of its time in
FilmCraft's ingest of the 4K MXF A-roll (a fixed ~150-400 s open/scan per
file plus ~0.1-0.3 s per 4K frame, ~43x slower than ffmpeg on the same
bytes). Transcoding only the used source ranges to edit resolution and
frame rate as ProRes once, then exporting from those, measured a 12-31x
win on a 120-frame range. See
``data/vep-fc-native-speed-profile/report.md`` sections 2-4.

What this stage does, and does not do:

- For each used source range (plus handles on both ends) it writes one
  edit-resolution ProRes file under a caller-chosen cache directory, named
  by the source content fingerprint, the frame range and the spec, so an
  unchanged range is never transcoded twice.
- The caller places the pre-conformed files instead of the camera
  originals. The Project Model and the Resolve swap-back keep referring
  to the ORIGINAL source frames: every record carries the exact
  preconform-frame to source-frame map (1:1 at equal frame rates, an
  exact rational otherwise), and ``apply_to_media_map`` returns the
  swap-back record next to the rewritten import map.
- It never touches the captain's media (sources are opened read-only by
  ffmpeg) and never picks the cache directory itself: the caller passes
  one under the project (``pipeline_output/scratch/preconform``), never
  under the captain's folders.

Failure shape: anything inexact refuses with ``PreconformError`` naming
the range - a short source, a frame-count mismatch after transcode, a
missing binary. A cache entry is valid when its sidecar identity still
matches (size, mtime, inode plus sampled content windows, all recorded
at transcode time); anything else meets the exact ffprobe frame-count
check, which stays the arbiter. ``_fast_validate`` states exactly what
would have to slip past both.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

#: Handles on each end of a used range, in source frames. Covers trims and
#: retime overshoot without re-transcoding; a use that moves further out
#: plans a new range instead of stretching this one silently.
HANDLE_FRAMES = 24

#: Bytes sampled from the head and tail of a source for its fingerprint.
#: A full sha256 of a 50-60 GB MXF is not a workable cache key, so the
#: fingerprint binds the byte length plus the head/tail sample digests.
_FINGERPRINT_SAMPLE_BYTES = 8 * 1024 * 1024

#: The engine conform policy this module matches (center 1:1 crop, no
#: resample, measured 2026-10-11). Part of the cache key: if a pin move
#: re-measures the policy, bump this so stale pixels never pass as valid.
CONFORM_POLICY_VERSION = 1

#: Cheap revalidation of a cache entry: size, identity and a digest of
#: sampled content windows, all recorded in the `.json` sidecar at
#: transcode time. A fast hit skips the full ffprobe frame-count decode
#: (tens of seconds on a reel proxy); any mismatch falls back to that
#: exact check, which stays the arbiter.
_SIDECAR_SAMPLE_BYTES = 64 * 1024
_SIDECAR_SAMPLE_WINDOWS = 3

_SSIM_ALL_RE = re.compile(r"All:\s*([0-9.]+)")
_PSNR_AVERAGE_RE = re.compile(r"average:([0-9.inf]+)")


class PreconformError(Exception):
    """A pre-conform range that cannot be built exactly."""


@dataclass(frozen=True)
class PreconformSpec:
    """The edit resolution and frame rate a pre-conform targets."""

    width: int
    height: int
    fps_num: int
    fps_den: int
    prores_profile: int = 2
    handle_frames: int = HANDLE_FRAMES
    with_audio: bool = True

    def validate(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise PreconformError(
                f"pre-conform size must be positive, got {self.width}x{self.height}"
            )
        if self.fps_num <= 0 or self.fps_den <= 0:
            raise PreconformError(
                f"pre-conform frame rate must be positive, got {self.fps_num}/{self.fps_den}"
            )
        if self.prores_profile not in (0, 1, 2, 3):
            raise PreconformError(
                f"pre-conform ProRes profile must be 0-3 (proxy/lt/standard/hq), "
                f"got {self.prores_profile}"
            )
        if self.handle_frames < 0:
            raise PreconformError(
                f"pre-conform handles must be non-negative, got {self.handle_frames}"
            )

    def key(self) -> str:
        """Stable cache key fragment for this spec."""
        audio = "a1" if self.with_audio else "a0"
        return (
            f"{self.width}x{self.height}_{self.fps_num}x{self.fps_den}"
            f"_p{self.prores_profile}_{audio}"
        )


@dataclass(frozen=True)
class PreconformRecord:
    """One transcoded range, and how its frames map back to the source."""

    asset_id: str
    source_path: str
    fingerprint: str
    source_fps_num: int
    source_fps_den: int
    source_start_frame: int
    source_end_frame: int
    spec_key: str
    spec_fps_num: int
    spec_fps_den: int
    path: str
    frames: int
    audio: bool
    cache_hit: bool

    def source_frame_of(self, preconform_frame: int) -> Fraction:
        """Exact original source frame for a pre-conform frame.

        1:1 when the spec rate equals the source rate; an exact rational
        otherwise. Out-of-range frames refuse rather than extrapolate.
        """
        if not 0 <= preconform_frame < self.frames:
            raise PreconformError(
                f"pre-conform frame {preconform_frame} is outside "
                f"0..{self.frames} for {self.asset_id}"
            )
        rate = Fraction(self.source_fps_num, self.source_fps_den) / Fraction(
            self.spec_fps_num, self.spec_fps_den
        )
        return Fraction(self.source_start_frame) + Fraction(preconform_frame) * rate

    def source_frame_exact(self, preconform_frame: int) -> int:
        """Integer original source frame, refusing fractional mappings."""
        mapped = self.source_frame_of(preconform_frame)
        if mapped.denominator != 1:
            raise PreconformError(
                f"pre-conform frame {preconform_frame} of {self.asset_id} maps to "
                f"source frame {mapped}, which is not integral; the rates "
                f"{self.source_fps_num}/{self.source_fps_den} and "
                f"{self.spec_fps_num}/{self.spec_fps_den} do not divide exactly"
            )
        return int(mapped)

    def to_swapback_record(self) -> dict:
        """The Resolve swap-back half: original source plus the map back."""
        return {
            "asset_id": self.asset_id,
            "original_path": self.source_path,
            "original_fingerprint": self.fingerprint,
            "original_range_frames": [self.source_start_frame, self.source_end_frame],
            "original_fps": [self.source_fps_num, self.source_fps_den],
            "preconform_path": self.path,
            "preconform_frames": self.frames,
            "preconform_fps": [self.spec_fps_num, self.spec_fps_den],
        }


def source_fingerprint(source: Path) -> str:
    """Content fingerprint of a source file without hashing all of it."""
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise PreconformError(f"cannot fingerprint {source}: {exc}") from exc
    if size <= 0:
        raise PreconformError(f"cannot fingerprint {source}: empty file")
    digest = hashlib.sha256()
    digest.update(f"size:{size}".encode("ascii"))
    try:
        with source.open("rb") as stream:
            head = stream.read(_FINGERPRINT_SAMPLE_BYTES)
            digest.update(b"head:")
            digest.update(head)
            if size > _FINGERPRINT_SAMPLE_BYTES:
                stream.seek(max(0, size - _FINGERPRINT_SAMPLE_BYTES))
                digest.update(b"tail:")
                digest.update(stream.read(_FINGERPRINT_SAMPLE_BYTES))
    except OSError as exc:
        raise PreconformError(f"cannot fingerprint {source}: {exc}") from exc
    return digest.hexdigest()[:32]


def used_range_with_handles(
    use_start_frame: int,
    use_end_frame: int,
    handle_frames: int,
    total_frames: int,
) -> tuple[int, int]:
    """Expand a used source range by handles, clamped to the media."""
    if use_end_frame <= use_start_frame:
        raise PreconformError(
            f"used range {use_start_frame}..{use_end_frame} is empty"
        )
    if use_start_frame < 0 or use_end_frame > total_frames:
        raise PreconformError(
            f"used range {use_start_frame}..{use_end_frame} lies outside "
            f"a {total_frames}-frame source"
        )
    return (
        max(0, use_start_frame - handle_frames),
        min(total_frames, use_end_frame + handle_frames),
    )


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Union overlapping or abutting ranges so one source transcodes once."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if end <= start:
            raise PreconformError(f"range {start}..{end} is empty")
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def cache_path(
    cache_dir: Path, fingerprint: str, start_frame: int, end_frame: int, spec: PreconformSpec
) -> Path:
    """Deterministic cache file for a fingerprinted range under a spec."""
    return (
        cache_dir
        / f"preconform-{fingerprint[:16]}-{start_frame}-{end_frame}"
        f"-{spec.key()}-c{CONFORM_POLICY_VERSION}.mov"
    )


def _sample_digest(path: Path, size: int) -> str:
    """Content digest of fixed windows: the whole file when small, else
    head, middle and tail windows. Deterministic given the size, which
    is compared alongside, so there is no small/large ambiguity."""
    digest = hashlib.sha256()
    span = _SIDECAR_SAMPLE_BYTES * _SIDECAR_SAMPLE_WINDOWS
    with path.open("rb") as stream:
        if size <= span:
            digest.update(stream.read())
            return digest.hexdigest()
        offsets = (0, size // 2, size - _SIDECAR_SAMPLE_BYTES)
        for offset in offsets:
            stream.seek(offset)
            digest.update(stream.read(_SIDECAR_SAMPLE_BYTES))
    return digest.hexdigest()


def _write_sidecar(target: Path, record: PreconformRecord) -> None:
    """The audit sidecar plus the revalidation identity: stat and
    sampled content digest taken after the file is closed."""
    stat = target.stat()
    size = stat.st_size
    target.with_suffix(".json").write_text(
        json.dumps(
            {
                "asset_id": record.asset_id,
                "source_path": record.source_path,
                "fingerprint": record.fingerprint,
                "source_range_frames": [record.source_start_frame, record.source_end_frame],
                "source_fps": [record.source_fps_num, record.source_fps_den],
                "spec_key": record.spec_key,
                "frames": record.frames,
                "audio": record.audio,
                "size_bytes": size,
                "mtime_ns": stat.st_mtime_ns,
                "dev": stat.st_dev,
                "ino": stat.st_ino,
                "sample_digest": _sample_digest(target, size),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _fast_validate(target: Path, *, fingerprint: str, start_frame: int,
                   end_frame: int, spec_key: str, frames: int) -> bool:
    """Whether the cache file is unchanged since its sidecar was
    written, without decoding it.

    Every check must pass: the sidecar names this exact fingerprinted
    range under this spec with this frame count, and the file's size,
    mtime, device/inode and sampled content still match what the
    transcode recorded. Anything missing or different - including a
    sidecar from before this identity existed - answers False, and the
    caller falls back to the exact ffprobe frame-count check.

    What would slip past: only a deliberate same-size modification that
    preserves the mtime and leaves the three sampled windows
    byte-identical while changing the frame count or the bytes
    elsewhere. Truncation, replacement, growth and any edit touching a
    sampled window all fail here and meet the exact check instead.
    """
    try:
        sidecar = json.loads(target.with_suffix(".json").read_text(encoding="utf-8"))
        if not isinstance(sidecar, dict):
            return False
        if (sidecar.get("fingerprint") != fingerprint
                or sidecar.get("source_range_frames") != [start_frame, end_frame]
                or sidecar.get("spec_key") != spec_key
                or sidecar.get("frames") != frames):
            return False
        stat = target.stat()
        if (sidecar.get("size_bytes") != stat.st_size
                or sidecar.get("mtime_ns") != stat.st_mtime_ns
                or sidecar.get("dev") != stat.st_dev
                or sidecar.get("ino") != stat.st_ino):
            return False
        return sidecar.get("sample_digest") == _sample_digest(target, stat.st_size)
    except (OSError, ValueError):
        return False


def _ffprobe_frames(path: Path, ffprobe: str = "ffprobe") -> int:
    done = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    if done.returncode != 0:
        raise PreconformError(f"ffprobe could not read {path}: {done.stderr.strip()[-300:]}")
    try:
        return int(json.loads(done.stdout)["streams"][0]["nb_read_frames"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise PreconformError(f"ffprobe gave no frame count for {path}: {exc}") from exc


def _source_size(source: Path, ffprobe: str = "ffprobe") -> tuple[int, int]:
    done = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise PreconformError(f"ffprobe could not size {source}: {done.stderr.strip()[-300:]}")
    try:
        stream = json.loads(done.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise PreconformError(f"ffprobe gave no size for {source}: {exc}") from exc


def conform_filter(
    source_width: int, source_height: int, spec: PreconformSpec
) -> str | None:
    """Edit-resolution conform matching the engine's measured policy.

    Measured 2026-10-11 on Reel 22 media (3840x2160 in a 1080x1920
    sequence): the engine does NOT resample - it centers a 1080x1920
    window of the source pixels (SSIM 0.94 for the exact center window
    vs 0.41-0.46 for every scale-based conform). So a source larger than
    the spec in both dimensions center-crops with no scaling; matching
    dimensions need no filter at all. Anything else (a source smaller
    than the spec in some dimension, where the engine's upscale policy
    is unmeasured) refuses rather than guessing a resample the gate
    would then have to catch.
    """
    if source_width <= 0 or source_height <= 0:
        raise PreconformError(
            f"source size must be positive, got {source_width}x{source_height}"
        )
    if source_width == spec.width and source_height == spec.height:
        return None
    if source_width >= spec.width and source_height >= spec.height:
        return f"crop={spec.width}:{spec.height}"
    raise PreconformError(
        f"source {source_width}x{source_height} is smaller than the "
        f"{spec.width}x{spec.height} spec in some dimension; the engine's "
        f"upscale policy there is unmeasured, so this range refuses rather "
        f"than guessing a resample"
    )


def _source_has_audio(source: Path, ffprobe: str = "ffprobe") -> bool:
    done = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        return False
    try:
        return bool(json.loads(done.stdout)["streams"])
    except (ValueError, KeyError, TypeError):
        return False


def preconform_source(
    source: Path,
    *,
    asset_id: str,
    source_fps_num: int,
    source_fps_den: int,
    use_start_frame: int,
    use_end_frame: int,
    total_frames: int,
    spec: PreconformSpec,
    cache_dir: Path,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> PreconformRecord:
    """Transcode one used source range (plus handles) to ProRes, cached.

    The source is opened read-only; the output and its ``.json`` sidecar
    land in ``cache_dir``. A valid cache entry is reused without running
    ffmpeg. The returned record maps every pre-conform frame back onto the
    original source frames, so the Project Model never learns the proxy.
    """
    spec.validate()
    if source_fps_num <= 0 or source_fps_den <= 0:
        raise PreconformError(
            f"source frame rate must be positive, got {source_fps_num}/{source_fps_den}"
        )
    if not source.is_file():
        raise PreconformError(f"source is not a file: {source}")
    start, end = used_range_with_handles(
        use_start_frame, use_end_frame, spec.handle_frames, total_frames
    )
    fingerprint = source_fingerprint(source)
    target = cache_path(cache_dir, fingerprint, start, end, spec)
    spec_rate = Fraction(spec.fps_num, spec.fps_den)
    source_rate = Fraction(source_fps_num, source_fps_den)
    if spec_rate == source_rate:
        frames = end - start
    else:
        scaled = Fraction(end - start) * spec_rate / source_rate
        if scaled.denominator != 1:
            raise PreconformError(
                f"range {start}..{end} at {source_fps_num}/{source_fps_den} does not "
                f"conform to {spec.fps_num}/{spec.fps_den} in whole frames ({scaled})"
            )
        frames = int(scaled)
    def _hit() -> PreconformRecord:
        return PreconformRecord(
            asset_id=asset_id,
            source_path=str(source),
            fingerprint=fingerprint,
            source_fps_num=source_fps_num,
            source_fps_den=source_fps_den,
            source_start_frame=start,
            source_end_frame=end,
            spec_key=spec.key(),
            spec_fps_num=spec.fps_num,
            spec_fps_den=spec.fps_den,
            path=str(target),
            frames=frames,
            audio=spec.with_audio and _source_has_audio(source, ffprobe),
            cache_hit=True,
        )

    if target.is_file():
        if _fast_validate(target, fingerprint=fingerprint, start_frame=start,
                          end_frame=end, spec_key=spec.key(), frames=frames):
            return _hit()
        try:
            cached_frames = _ffprobe_frames(target, ffprobe)
        except PreconformError:
            cached_frames = -1
        if cached_frames == frames:
            # A sidecar from before the revalidation identity existed:
            # the frames check out, so upgrade it and the next run is fast.
            _write_sidecar(target, _hit())
            return _hit()
        target.unlink()
    cache_dir.mkdir(parents=True, exist_ok=True)
    start_seconds = start * source_fps_den / source_fps_num
    source_width, source_height = _source_size(source, ffprobe)
    filters: list[str] = []
    conform = conform_filter(source_width, source_height, spec)
    if conform is not None:
        filters.append(conform)
    if spec_rate != source_rate:
        filters.append(f"fps={spec.fps_num}/{spec.fps_den}")
    filters.append("format=yuv422p10le")
    video_filter = ",".join(filters)
    audio = spec.with_audio and _source_has_audio(source, ffprobe)
    argv = [
        ffmpeg,
        "-v",
        "error",
        "-ss",
        f"{start_seconds:.6f}",
        "-i",
        str(source),
        "-frames:v",
        str(frames),
        "-vf",
        video_filter,
        "-c:v",
        "prores_ks",
        "-profile:v",
        str(spec.prores_profile),
    ]
    if audio:
        argv += ["-c:a", "pcm_s16le"]
    else:
        argv += ["-an"]
    argv.append(str(target))
    done = subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8", timeout=3600, check=False
    )
    if done.returncode != 0 or not target.is_file():
        raise PreconformError(
            f"ffmpeg could not pre-conform {source} range {start}..{end}: "
            f"{done.stderr.strip()[-500:]}"
        )
    measured = _ffprobe_frames(target, ffprobe)
    if measured != frames:
        target.unlink(missing_ok=True)
        raise PreconformError(
            f"pre-conform of {source} range {start}..{end} produced {measured} "
            f"frames, expected {frames}; refusing rather than mapping a short file"
        )
    record = PreconformRecord(
        asset_id=asset_id,
        source_path=str(source),
        fingerprint=fingerprint,
        source_fps_num=source_fps_num,
        source_fps_den=source_fps_den,
        source_start_frame=start,
        source_end_frame=end,
        spec_key=spec.key(),
        spec_fps_num=spec.fps_num,
        spec_fps_den=spec.fps_den,
        path=str(target),
        frames=frames,
        audio=audio,
        cache_hit=False,
    )
    _write_sidecar(target, record)
    return record


def apply_to_media_map(
    media_map: dict[str, Path], records: list[PreconformRecord]
) -> tuple[dict[str, Path], dict]:
    """Point imports at pre-conformed files, keeping the swap-back record.

    ``media_map`` maps asset ids to camera-original paths (what the
    Project Model's ``source_interval`` ranges refer to). The returned map
    points the same asset ids at the pre-conformed files for the FilmCraft
    import; the returned swap-back document maps each asset back onto its
    original path and source range for the Resolve finish.
    """
    by_asset = {record.asset_id: record for record in records}
    swapped = dict(media_map)
    swapback: dict[str, dict] = {}
    for asset_id, record in by_asset.items():
        if asset_id not in media_map:
            raise PreconformError(
                f"pre-conform record for {asset_id} names an asset with no import"
            )
        swapped[asset_id] = Path(record.path)
        swapback[asset_id] = record.to_swapback_record()
    return swapped, {"preconformed": swapback}


def measure_fidelity(
    reference: Path, candidate: Path, ffmpeg: str = "ffmpeg"
) -> dict[str, float]:
    """SSIM (All) and PSNR average of a candidate against a reference."""
    for path in (reference, candidate):
        if not path.is_file():
            raise PreconformError(f"fidelity input is not a file: {path}")
    done = subprocess.run(
        [
            ffmpeg,
            "-v",
            "info",
            "-i",
            str(reference),
            "-i",
            str(candidate),
            "-lavfi",
            "ssim;[0:v][1:v]psnr",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        check=False,
    )
    if done.returncode != 0:
        raise PreconformError(
            f"ffmpeg fidelity comparison failed: {done.stderr.strip()[-500:]}"
        )
    stderr = done.stderr
    ssim_match = _SSIM_ALL_RE.search(stderr)
    psnr_match = _PSNR_AVERAGE_RE.search(stderr)
    if ssim_match is None or psnr_match is None:
        raise PreconformError(
            f"ffmpeg fidelity output carried no SSIM/PSNR summary: {stderr[-500:]}"
        )
    psnr_text = psnr_match.group(1)
    return {
        "ssim_all": float(ssim_match.group(1)),
        "psnr_average_db": float("inf") if psnr_text == "inf" else float(psnr_text),
    }
