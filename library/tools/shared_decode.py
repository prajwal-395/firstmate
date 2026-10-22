"""One decode per (source, recipe): the shared sampled-frame cache.

PERF-007 ("Avoid redundant media decoding"): several analysis lanes
measure the SAME source frames with the SAME geometry and each ran its
own decode.  On the geo-podcast sources the picture sampler (step 1.03's
soft-picture pass, `library/tools/analysis/picture_quality.py`) and the
VI-03 image-defects sampler (step 1.04's source sampler,
`library/tools/analysis/image_defects.py`) both decoded every source in
full, at 5 Hz, as 180 px-short-side greyscale - the same frames, twice.
A whole 4K source costs minutes per decode (measured 2026-10-21: 120 s
of `LC4932.MXF` took 3 m 29 s to decode once), so the second decode was
pure repetition.

**What may be shared, and what may not.**  A decoded frame is only
reusable by another lane when the SOURCE FRAME and the PIXEL GEOMETRY
are the same, so a cache entry is keyed by BOTH:

* the source's content identity (`footage_identity.fingerprint`: size
  plus a digest of both file ends), so a re-recorded file never serves
  another file's frames; and
* the RECIPE - the rate, the frame size, the pixel format and the
  interval - so two lanes share only when they asked for the same
  frames.

That second half is the rule the punchlist states: an artifact that
depends on analysis parameters is not a cacheable artifact.  M2's
I-frame sample (384 px JPEG at the source's keyframe cadence), M3d's
own 1152 px I-frame pass, the VLM's full-resolution coarse frames and
the detector's 2 fps JPEGs are DIFFERENT recipes: their frames are not
the same frames, so they keep their own decodes.  Only a recipe that
matches byte for byte shares.

**The decode is one command, not two.**  The two lanes that share
wrote their frames with almost the same command; the only difference
was `scale`'s range flags, and the decoded BYTES are identical either
way (measured on `LC4932.MXF` and on a scratch copy of it: the two
commands' outputs hash the same).  So the command lives here, once, and
both lanes read what it produced - the outputs stay byte-identical to
what each lane produced before.

**The frames are read, not held.**  A whole-source sample at 5 Hz and
180 px is 288 kB per second of source - 1.4 GB for an 82-minute file.
The old picture sampler held all of it in memory as one buffer; the
shared cache writes it once to disk and every reader maps it, so the
peak memory of a lane is one frame rather than the whole sample.

**The cache is bounded and removable.**  `REN_SHARED_DECODE=off` turns
sharing off (each lane decodes its own, which is what the benchmark's
independent arm measures).  The entries are registered with the
existing cache lifecycle (`kind="decoded_frame"`), so the budget and
eviction that already govern decoded frames govern these.

`docs/benchmarks/2026-10-21-shared-decode.md` has the inventory of the
lanes, the measurements and the before/after.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

CACHE_DIRNAME = "shared-decode"
"""The directory, under the memory root, that holds the shared frames."""

ENV_DISABLE = "REN_SHARED_DECODE"
"""Set to `off`/`0`/`no`/`false` to decode independently again."""

SCHEMA = "ren_shared_decode/1"
FFMPEG_TIMEOUT_S = 3600
TEMP_PREFIX = "ren-shared-decode-"

DECODE_PRODUCER_VERSION = "ren-shared-decode-producer/1"
"""The decode semantics' own version: bump when :func:`_command` changes.

``SCHEMA`` versions the manifest SHAPE; this versions what the bytes
MEAN - the ffmpeg command that produced them.  It is part of the cache
key, so a command change misses rather than serving another command's
frames.  Built under :mod:`library.tools.cache_keys`, the one module
that constructs cache keys.
"""

_TEMP_DIRS: list[str] = []


class SharedDecodeUnavailable(RuntimeError):
    """The source could not be decoded, so no frames are available.

    Raised instead of returning an empty frame set: "the source has no
    frames" and "the source was never decoded" are different answers,
    and each caller has its own way of saying so.
    """


@dataclass(frozen=True)
class Recipe:
    """The exact decode a lane is asking for.

    Two lanes share a decode only when their recipes are equal.  Every
    field here changes the pixels, so every field belongs in the key:
    the sampling rate, the frame size, the pixel format and the
    interval.
    """

    rate_hz: float
    width: int
    height: int
    pixel_format: str = "gray"
    start_s: float | None = None
    end_s: float | None = None

    def __post_init__(self) -> None:
        if float(self.rate_hz) <= 0:
            raise ValueError("a decode recipe needs a positive rate")
        if int(self.width) <= 0 or int(self.height) <= 0:
            raise ValueError("a decode recipe needs a positive frame size")
        if self.start_s is not None and self.start_s < 0:
            raise ValueError("a decode recipe cannot start before zero")
        if (self.end_s is not None and self.start_s is not None
                and self.end_s <= self.start_s):
            raise ValueError("a decode recipe needs a positive interval")

    @property
    def signature(self) -> str:
        """The decode, named. Two recipes share only when this matches."""
        parts = [f"fps={float(self.rate_hz):g}",
                 f"scale={int(self.width)}x{int(self.height)}",
                 f"pix={self.pixel_format}"]
        if self.start_s:
            parts.append(f"ss={float(self.start_s):g}")
        if self.end_s is not None:
            parts.append(f"t={float(self.end_s - (self.start_s or 0.0)):g}")
        return ",".join(parts)

    @property
    def frame_bytes(self) -> int:
        """Bytes one frame of this recipe occupies."""
        return int(self.width) * int(self.height)


@dataclass
class SharedFrames:
    """A decoded sample on disk, and what it cost to get it."""

    path: str
    frame_count: int
    width: int
    height: int
    rate_hz: float
    cache_hit: bool
    decode_seconds: float
    bytes_written: int
    signature: str
    content_digest: str = ""
    tail_bytes: int = 0
    """Bytes of a partial trailing frame the decoder left behind.

    A lane that REFUSED a partial frame (`image_defects`) reads this and
    refuses the same way; a lane that ignored the tail
    (`picture_quality`) sees the same frames it saw before.
    """

    def open(self) -> np.memmap:
        """The frames, mapped - one frame's memory, not the whole sample."""
        return np.memmap(self.path, dtype=np.uint8, mode="r",
                          shape=(self.frame_count, self.height, self.width))


def enabled() -> bool:
    """Whether sharing is on. `REN_SHARED_DECODE=off` turns it off."""
    raw = os.environ.get(ENV_DISABLE)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "off", "no", "false"}


def cache_root(root: os.PathLike | str | None = None) -> Path:
    """Where the shared frames live.

    The memory root, so the frames sit beside the other per-source
    measurements and follow the same project scoping: a project's frames
    are private to it, and a run outside a project uses the machine
    store.
    """
    if root is not None:
        return Path(root).expanduser() / CACHE_DIRNAME
    from library.tools import source_memory
    return Path(source_memory.memory_root()) / CACHE_DIRNAME


def recipe_configuration(recipe: Recipe) -> dict:
    """What a recipe asks for, in the canonical component vocabulary.

    The sampling recipe (rate and interval), the preprocessing (frame
    size and pixel format - everything between the stored bytes and the
    measured pixels), and the producer semantics (whose command
    decodes it, and which version of that command).  Two recipes that
differ in any of them have not decoded the same frames.
    """
    from library.tools import cache_keys

    payload: dict = {}
    payload.update(cache_keys.sampling_component(
        rate_hz=float(recipe.rate_hz), start_s=recipe.start_s,
        end_s=recipe.end_s))
    payload.update(cache_keys.preprocessing_component(
        width=int(recipe.width), height=int(recipe.height),
        pixel_format=recipe.pixel_format, decoder="ffmpeg"))
    payload.update(cache_keys.producer_component(
        producer_id="shared-decode",
        producer_version=DECODE_PRODUCER_VERSION))
    return payload


def _key(content_digest: str, recipe: Recipe) -> str:
    from library.tools import cache_keys

    return cache_keys.observation_key(
        source_identity=content_digest, model="ffmpeg-decode",
        configuration=recipe_configuration(recipe),
        analysis_version=DECODE_PRODUCER_VERSION).signature()


def purge(source_path: str, recipe: Recipe, *,
          root: os.PathLike | str | None = None) -> bool:
    """Remove the cached frames of one source and recipe.

    The way an operator frees the disk the shared decode took, without
    touching any other source's frames.  Returns whether anything was
    there to remove.
    """
    from library.tools import footage_identity
    try:
        digest = str(
            footage_identity.fingerprint(source_path)["content_digest"])
    except OSError:
        return False
    entry = _entry_dir(cache_root(root), digest, recipe)
    if not entry.is_dir():
        return False
    shutil.rmtree(entry, ignore_errors=True)
    return True


def _entry_dir(root: Path, content_digest: str, recipe: Recipe) -> Path:
    key = _key(content_digest, recipe)
    return root / key[:2] / key


def _command(source_path: str, recipe: Recipe, out_path: Path) -> list[str]:
    """The one command this recipe decodes with.

    The command is the whole contract: two lanes that share a recipe
    read the bytes this produced, so the bytes each lane used to
    produce on its own have to be the bytes this produces.  That is
    measured, not assumed - `docs/benchmarks/2026-10-21-shared-decode.md`.
    """
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
    if recipe.start_s:
        command += ["-ss", str(recipe.start_s)]
    command += ["-i", str(source_path)]
    if recipe.end_s is not None:
        command += ["-t", str(recipe.end_s - (recipe.start_s or 0.0))]
    command += ["-vf",
                (f"fps={float(recipe.rate_hz):g},"
                 f"scale={int(recipe.width)}:{int(recipe.height)}"),
                "-pix_fmt", recipe.pixel_format,
                "-f", "rawvideo", str(out_path)]
    return command


def _decode_to(source_path: str, recipe: Recipe,
               out_path: Path) -> tuple[int, int]:
    """Decode the source into `out_path`; the frames it wrote, or raise.

    Returns `(frame_count, tail_bytes)`.  A partial trailing frame is
    truncated, exactly as the lanes that read a rawvideo buffer always
    did - the bytes of the whole frames are untouched.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        _command(source_path, recipe, out_path),
        capture_output=True, encoding="utf-8", errors="replace",
        timeout=FFMPEG_TIMEOUT_S, check=False)
    if result.returncode != 0:
        detail = (result.stderr or "").strip()[-400:]
        raise SharedDecodeUnavailable(
            f"ffmpeg could not decode {source_path} at {recipe.signature}: "
            f"{detail}")
    written = out_path.stat().st_size if out_path.exists() else 0
    frame_count, tail = divmod(written, recipe.frame_bytes)
    if frame_count <= 0:
        raise SharedDecodeUnavailable(
            f"ffmpeg decoded no whole frames from {source_path} at "
            f"{recipe.signature}")
    if tail:
        with open(out_path, "r+b") as handle:
            handle.truncate(frame_count * recipe.frame_bytes)
    return frame_count, tail


def _register(frames_path: Path, content_digest: str, recipe: Recipe,
              frame_count: int, outcome: str = "miss") -> None:
    """Tell the cache lifecycle about the entry, so it can be evicted."""
    try:
        from library.tools import cache_keys, cache_lifecycle
        cache_lifecycle.register_cache_artifact_from_environment(
            str(frames_path), kind="decoded_frame",
            producer_key=cache_keys.producer_key(
                "shared-decode", content_digest, "ffmpeg-decode",
                DECODE_PRODUCER_VERSION, recipe.signature),
            regeneration_method="shared_decode: ffmpeg sampled frames",
            source_available=True, compatible=True)
        cache_lifecycle.record_cache_metric(
            task="shared-decode", kind="decoded_frame", outcome=outcome,
            elapsed_seconds=0.0, bytes_used=frames_path.stat().st_size,
            artifact_id=cache_lifecycle.cache_artifact_id_from_environment(
                str(frames_path), "decoded_frame"))
    except (OSError, ValueError, KeyError, TypeError):
        # The lifecycle is a ledger, not a gate: a failure to record an
        # entry must not fail the measurement that produced it.
        return


def sampled_frames(source_path: str, recipe: Recipe, *,
                   root: os.PathLike | str | None = None,
                   content_digest: str | None = None) -> SharedFrames:
    """The source's frames for this recipe - decoded once, shared after.

    The first caller decodes; every later caller with the same recipe
    reads what it wrote.  With sharing off, each caller decodes its own
    copy, which is the independent-decode arm the benchmark measures.
    """
    started = time.perf_counter()
    if content_digest is None:
        from library.tools import footage_identity
        try:
            content_digest = str(
                footage_identity.fingerprint(source_path)["content_digest"])
        except OSError as exc:
            raise SharedDecodeUnavailable(
                f"{source_path} could not be fingerprinted: {exc}") from exc

    if not enabled():
        return _independent_frames(source_path, recipe, content_digest,
                                   started)

    base = cache_root(root)
    entry = _entry_dir(base, content_digest, recipe)
    frames_path = entry / "frames.raw"
    manifest_path = entry / "frames.json"
    existing = _read_manifest(manifest_path, frames_path, recipe,
                              content_digest)

    entry.mkdir(parents=True, exist_ok=True)
    lock_path = entry / "decode.lock"
    with _exclusive(lock_path):
        # Another writer may have filled the entry while we waited.
        existing = _read_manifest(manifest_path, frames_path, recipe,
                                  content_digest)
        if existing is not None:
            return _hit(existing, frames_path, recipe, content_digest, started,
                        source_path)
        scratch = entry / f".frames.{os.getpid()}.raw"
        try:
            with _ledger(source_path, recipe, calls=1):
                frame_count, tail = _decode_to(source_path, recipe, scratch)
            os.replace(scratch, frames_path)
        finally:
            if scratch.exists():
                scratch.unlink()
        manifest = {
            "schema": SCHEMA,
            "content_digest": content_digest,
            "key": _key(content_digest, recipe),
            "producer_version": DECODE_PRODUCER_VERSION,
            "signature": recipe.signature,
            "recipe": {"rate_hz": float(recipe.rate_hz),
                       "width": int(recipe.width),
                       "height": int(recipe.height),
                       "pixel_format": recipe.pixel_format,
                       "start_s": recipe.start_s,
                       "end_s": recipe.end_s},
            "frame_count": frame_count,
            "frame_bytes": recipe.frame_bytes,
            "tail_bytes": tail,
            "bytes": frames_path.stat().st_size,
            "created_at": time.time(),
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=1, sort_keys=True) + "\n",
            encoding="utf-8")
    _register(frames_path, content_digest, recipe, frame_count)
    return SharedFrames(
        path=str(frames_path), frame_count=frame_count,
        width=int(recipe.width), height=int(recipe.height),
        rate_hz=float(recipe.rate_hz), cache_hit=False,
        decode_seconds=round(time.perf_counter() - started, 3),
        bytes_written=frames_path.stat().st_size,
        signature=recipe.signature, content_digest=content_digest,
        tail_bytes=tail)


def _independent_frames(source_path: str, recipe: Recipe,
                        content_digest: str, started: float) -> SharedFrames:
    """The uncached path: this lane's own decode, kept out of the cache."""
    scratch = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    _TEMP_DIRS.append(str(scratch))
    frames_path = scratch / "frames.raw"
    with _ledger(source_path, recipe, calls=1):
        frame_count, tail = _decode_to(source_path, recipe, frames_path)
    return SharedFrames(
        path=str(frames_path), frame_count=frame_count,
        width=int(recipe.width), height=int(recipe.height),
        rate_hz=float(recipe.rate_hz), cache_hit=False,
        decode_seconds=round(time.perf_counter() - started, 3),
        bytes_written=frames_path.stat().st_size,
        signature=recipe.signature, content_digest=content_digest,
        tail_bytes=tail)


def _read_manifest(manifest_path: Path, frames_path: Path,
                   recipe: Recipe, content_digest: str) -> dict | None:
    """The entry's manifest, when it is still the entry we asked for."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if manifest.get("schema") != SCHEMA:
        return None
    if manifest.get("signature") != recipe.signature:
        return None
    if manifest.get("producer_version", DECODE_PRODUCER_VERSION) != \
            DECODE_PRODUCER_VERSION:
        # Another command's frames: re-decode rather than serve them.
        return None
    if manifest.get("content_digest") != content_digest:
        # Filed under another source's identity: never serve it as this
        # source's frames, whatever the directory key said.
        return None
    if manifest.get("key", _key(content_digest, recipe)) != \
            _key(content_digest, recipe):
        return None
    try:
        size = frames_path.stat().st_size
    except OSError:
        return None
    if size != manifest.get("bytes"):
        return None
    if not manifest.get("frame_count"):
        return None
    return manifest


def _hit(manifest: dict, frames_path: Path, recipe: Recipe,
         content_digest: str, started: float,
         source_path: str) -> SharedFrames:
    _register(frames_path, content_digest, recipe,
              int(manifest.get("frame_count") or 0), outcome="hit")
    with _ledger(source_path, recipe, calls=0, cache_hits=1):
        pass
    return SharedFrames(
        path=str(frames_path), frame_count=int(manifest["frame_count"]),
        width=int(recipe.width), height=int(recipe.height),
        rate_hz=float(recipe.rate_hz), cache_hit=True,
        decode_seconds=round(time.perf_counter() - started, 3),
        bytes_written=int(manifest.get("bytes") or 0),
        signature=recipe.signature, content_digest=content_digest,
        tail_bytes=int(manifest.get("tail_bytes") or 0))


def _ledger(source_path: str, recipe: Recipe, *, calls: int,
            cache_hits: int = 0):
    """The decode, in the run's own ledger.

    A shared decode is still a decode of that source, and the ledger is
    where a reader learns that a lane did not decode anything because
    another lane's frames were already there.
    """
    from library.tools import perf_ledger
    return perf_ledger.media_decode(
        source_path, purpose="shared_decode.sampled_frames",
        media_kind="video", access_pattern="sequential",
        calls=calls, cache_hits=cache_hits,
        decoded_source_s=None, backend="ffmpeg",
        recipe=recipe.signature)


class _exclusive:
    """An exclusive lock on one cache entry, for the writer's turn.

    Two lanes that want the same frames at the same time must not both
    decode them; the second one waits and then reads what the first
    wrote.  The lock is a file lock, so it dies with the process that
    holds it.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle = None

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+")
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc) -> None:
        import fcntl
        if self.handle is not None:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None


@atexit.register
def _drop_scratch() -> None:
    """The uncached frames are scratch: they go when the process does."""
    while _TEMP_DIRS:
        shutil.rmtree(_TEMP_DIRS.pop(), ignore_errors=True)
