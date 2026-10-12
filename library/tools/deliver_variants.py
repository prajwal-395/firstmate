"""`ren deliver` variant renders: one file per differing platform spec. One enumeration.

The track this module belongs to
-------------------------------
Plan item T16 of the carefree-workflow plan: `ren deliver <project>`
delivers every approved reel. `deliver_all.delivery_jobs` plans the jobs
through `deliverable_set` - one job per distinct render the requested
platforms require - but only the first job ever rendered: every further
job landed in the receipt as a named-missing row ("the reframe path" did
not exist). This module IS that path: each further job is reframed off
the rendered master picture with `reframe.py`, QC'd against its own spec
by the caller, and recorded in the deliver ledger under its own key, so
a differing per-platform spec produces a rendered variant instead of a
missing row.

What renders what
-----------------
The master (jobs[0], the project's own platform) is Resolve-rendered by
`reel_deliver.deliver_reel` - the explicit verb, the ONLY path that
renders a reel. Variants never touch Resolve: they are ffmpeg
derivations of the master file, so one Resolve pass serves every
platform. The audio stream is copied untouched - the picture is
reframed, not the sound - which is correct because every registry row
shares the -14 LUFS target the master was normalized to.

Where the crop aims
-------------------
Subject-aware when a face-centre track is supplied (the `face_center_x`
track `step_1_04_temporal_index` emits, over the MASTER's seconds at
`sample_rate_hz`), centred otherwise. The centred default is the honest
one `reframe.py` documents: a reel timeline's clip placement lives in
Resolve, and no file-backed join maps it to the per-clip temporal-index
files, so aiming anywhere else would invent a position. The report says
which crop ran and why - `crop` is `"subject_followed"` or `"centred"`,
and `crop_reason` carries the grounds.

What this module refuses
-----------------------
A variant spec this ffmpeg path cannot satisfy is refused, never shipped
wrong: a non-H264 codec (the path encodes libx264), a sidecar caption
carriage (the burned-in captions travel in the picture; no sidecar
writer exists on this path), and non-positive target dimensions. An
unreadable master probe refuses the same way - a variant of an
unmeasured file is not a variant.

What this module does not do
----------------------------
It does not select the spec, run QC, write the ledger or publish
receipts - `deliver_all` owns the batch and calls this per variant job.
It does not honour `requires_master_picture` (rendering the 16:9 master
in Resolve even when nobody requested it): jobs[0] is whatever the
project asked for first, and the reframe works in both directions, but a
vertical master reframed to 16:9 crops top and bottom where a true
master render would not. That refinement lands when a project needs it,
with a declaration that feeds it.

`encoding="utf-8"` on every captured pipe (AGENTS.md 9).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence

#: The face-centre sample rate the variant crop expects: the 5 Hz
#: `face_center_x` track `step_1_04_temporal_index` emits. A supplied
#: track at any other rate is still honoured (the geometry reads the
#: rate it is given); this names the rate the batch asks for.
VARIANT_FACE_RATE_HZ = 5.0


def variant_ledger_key(timeline_name: str, serves: Sequence[str]) -> str:
    """The deliver-ledger key for one reel's variant job.

    The master keeps the bare timeline name (the key every ledger entry
    already carries); a variant keys on the timeline AND the platform
    group it serves, so `tiktok` and `master` variants of one reel skip
    and re-render independently. The separator is `" :: "` - never
    `"+"`, which already joins platforms inside `serves`.
    """
    return f"{timeline_name} :: {'+'.join(s for s in serves if s)}"


def variant_file_name(timeline_name: str, naming: str, ext: str,
                      serves: Sequence[str]) -> str:
    """The output filename for one reel's variant job.

    The master name (`reel_deliver.render_file_name`) with the served
    platforms inserted before the final extension -
    `Reel 01 - pricing.master.mp4` - so a variant never collides with
    the master or a sibling variant, whatever the project's naming
    declares (a literal naming with no placeholders still gains the
    suffix). A name with no extension gains the suffix at the end.
    """
    from library.tools.reel_deliver import render_file_name

    suffix = "+".join(s for s in serves if s) or "variant"
    master = render_file_name(timeline_name, naming or "{timeline}.{ext}",
                              ext or "mp4")
    stem, dot, extension = master.rpartition(".")
    if dot and stem and extension and "/" not in extension:
        return f"{stem}.{suffix}.{extension}"
    return f"{master}.{suffix}"


def variant_extension(spec, fallback: str = "mp4") -> str:
    """The file extension a variant spec's container ships in."""
    from library.tools.reel_deliver import CONTAINER_EXTENSIONS

    container = (getattr(spec, "container", "") or "").strip()
    if not container:
        return fallback
    return CONTAINER_EXTENSIONS.get(container.lower(), container.lower())


def probe_master(master_path: str, timeout: int = 60) -> dict:
    """`(width, height, fps, duration_seconds)` the master file carries.

    One ffprobe call: the video stream's dimensions and average frame
    rate beside the format duration. Refuses (RuntimeError) when the
    file is missing or unmeasurable - a variant of an unmeasured file
    is not a variant. `fps` falls back across `avg_frame_rate` then
    `r_frame_rate`; `0/0` and absent rates read as unmeasurable, never
    as zero.
    """
    if not master_path or not os.path.isfile(master_path):
        raise RuntimeError(
            f"variant reframe needs the rendered master file, got: "
            f"{master_path!r}")
    try:
        completed = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries",
              "stream=width,height,avg_frame_rate,r_frame_rate:format=duration",
             "-of", "json", master_path],
            capture_output=True, check=False, encoding="utf-8",
            errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(
            f"ffprobe could not read the master file {master_path}: "
            f"{exc}") from None
    try:
        payload = json.loads(completed.stdout or "{}")
    except ValueError:
        raise RuntimeError(
            f"ffprobe returned unreadable JSON for {master_path}") from None
    streams = payload.get("streams") or []
    stream = streams[0] if streams else {}
    width, height = stream.get("width"), stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise TypeError(
            f"ffprobe found no video dimensions in {master_path}")
    fps = _parse_rate(stream.get("avg_frame_rate")) or _parse_rate(
        stream.get("r_frame_rate"))
    if not fps or fps <= 0:
        raise RuntimeError(
            f"ffprobe found no usable frame rate in {master_path}")
    duration = None
    try:
        duration = float((payload.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    if not duration or duration <= 0:
        raise RuntimeError(
            f"ffprobe found no usable duration in {master_path}")
    return {"width": width, "height": height, "fps": fps,
            "duration_seconds": duration}


def _parse_rate(text) -> float | None:
    """An ffprobe `num/den` rate (or plain float) as frames per second."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    try:
        if "/" in raw:
            num, _, den = raw.partition("/")
            rate = float(num) / float(den)
        else:
            rate = float(raw)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return rate if rate > 0 else None


def check_variant_spec(spec) -> None:
    """Refuse a variant spec this ffmpeg path cannot satisfy.

    Raises ValueError naming the half that does not fit: a non-H264
    codec, a sidecar caption carriage, or non-positive target
    dimensions. Every registry row today passes; the guard pins the
    path's limits so a future row ships wrong nowhere silently.
    """
    codec = (getattr(spec, "codec", "") or "").strip()
    if codec.upper() != "H264":
        raise ValueError(
            f"variant reframe encodes libx264 (H.264) and cannot satisfy "
            f"platform {getattr(spec, 'platform', '?')!r}'s codec "
            f"{codec!r}")
    carriage = (getattr(spec, "caption_format", "") or "").strip()
    if carriage and carriage != "burned_in":
        raise ValueError(
            f"variant reframe carries the master's burned-in captions in "
            f"the picture and cannot satisfy platform "
            f"{getattr(spec, 'platform', '?')!r}'s caption carriage "
            f"{carriage!r} - no sidecar writer exists on this path")
    width, height = getattr(spec, "width", 0), getattr(spec, "height", 0)
    if not width or not height or width <= 0 or height <= 0:
        raise ValueError(
            f"variant reframe needs positive target dimensions for "
            f"platform {getattr(spec, 'platform', '?')!r}, got "
            f"{width!r}x{height!r}")


def render_variant(*, master_path: str, spec, output_path: str,
                   face_centers: Sequence | None = None,
                   sample_rate_hz: float = VARIANT_FACE_RATE_HZ,
                   fps: float = 0.0,
                   sendcmd_path: str = "",
                   work_dir: str = "") -> dict:
    """Reframe the rendered master file to one variant spec's frame.

    Returns `{"output_path", "width", "height", "fps", "fps_source",
    "crop", "crop_reason", "crop_windows", "audio", "master",
    "sendcmd_path"}`. `face_centers` is the subject's horizontal centre
    over the MASTER's seconds at `sample_rate_hz` (None means no track:
    the crop stays centred and says so). `fps` overrides the output
    frame rate; the default is the variant spec's own rate. Raises
    (RuntimeError/ValueError) on any failure: a variant file that was
    not produced is not a quiet success, and the caller records it as a
    failed variant, never as a missing row.
    """
    from library.tools import reframe as _reframe

    check_variant_spec(spec)
    probed = probe_master(master_path)
    out_fps, fps_source = _output_fps(spec, fps, probed["fps"])
    target_width, target_height = spec.width, spec.height

    if face_centers is not None:
        track = [None if v is None else float(v) for v in face_centers]
        windows = _reframe.subject_crop_path(
            probed["width"], probed["height"],
            target_width, target_height, track,
            sample_rate_hz=float(sample_rate_hz or 5.0))
        crop, reason = (
            "subject_followed",
            ("the caller supplied a face-centre track over "
             "the master seconds, so the crop follows the "
             "measured subject"),
        )
    else:
        windows = _centred_windows(probed, target_width, target_height,
                                   out_fps)
        crop, reason = (
            "centred",
            ("no face-centre track for this reel timeline "
             "(its clip placement lives in Resolve, and no "
             "file-backed join maps it to the per-clip "
             "temporal-index files), so the crop is centred - "
             "the honest default, not a guessed position"),
        )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)),
                exist_ok=True)
    if not sendcmd_path:
        base = work_dir or os.path.dirname(os.path.abspath(output_path))
        os.makedirs(base, exist_ok=True)
        stem = os.path.splitext(os.path.basename(output_path))[0]
        sendcmd_path = os.path.join(base, stem + "_crop_cmds.txt")
    framed = _reframe.reframe_video(
        master_path, output_path, windows,
        target_width, target_height, fps=out_fps,
        sendcmd_path=sendcmd_path)
    return {
        "output_path": framed["output_path"],
        "width": target_width, "height": target_height,
        "fps": out_fps, "fps_source": fps_source,
        "crop": crop, "crop_reason": reason,
        "crop_windows": framed["crop_windows"],
        "audio": ("copied_from_master - the picture is reframed, not "
                  "the sound; every registry row shares the -14 LUFS "
                  "target the master was normalized to"),
        "master": probed,
        "sendcmd_path": framed["sendcmd_path"],
    }


def _centred_windows(probed: dict, target_width: int,
                     target_height: int, out_fps: float) -> list:
    """One centred window per output frame over the master duration."""
    from library.tools import reframe as _reframe

    count = max(1, round(probed["duration_seconds"] * out_fps))
    single = _reframe.subject_crop_path(
        probed["width"], probed["height"],
        target_width, target_height, [], sample_rate_hz=out_fps)[0]
    return [single] * count


def _output_fps(spec, override: float, probed: float) -> tuple:
    """The variant's frame rate and where it came from, as `(fps, source)`."""
    try:
        wanted = float(override or 0.0)
    except (TypeError, ValueError):
        wanted = 0.0
    if wanted > 0:
        return wanted, "caller_override"
    try:
        spec_rate = float(getattr(spec, "fps", 0.0) or 0.0)
    except (TypeError, ValueError):
        spec_rate = 0.0
    if spec_rate > 0:
        return spec_rate, "variant_spec"
    return probed, "master_probe"


__all__ = [
    "VARIANT_FACE_RATE_HZ",
    "check_variant_spec",
    "probe_master",
    "render_variant",
    "variant_extension",
    "variant_file_name",
    "variant_ledger_key",
]
