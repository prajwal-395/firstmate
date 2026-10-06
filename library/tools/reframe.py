"""Subject-aware reframing: crop a source into a target aspect, following the subject.

The track this module belongs to
-------------------------------
Capability 18 of the captain's complete-stack spec (2026-10-06): Delivery +
adaptation. This is the reframing slice: taking a master (16:9) and producing
a 9:16 or 1:1 social variant whose crop window FOLLOWS THE SUBJECT, so a
speaker standing in the left third of the frame is not beheaded by a dead-
centre crop.

What Ren already has, and what this builds on
---------------------------------------------
The subject geometry is measured, not invented: `step_1_04_temporal_index`
samples the largest detected face at 5 Hz and records `face_center_x`
(0.0 = left edge, 1.0 = right) and `face_width` (a fraction of the source
width) per clip. `library/tools/subject_framing.py` reduces that per clip
and owns the plausibility bounds (`MIN_PLAUSIBLE_CX`, `MAX_PLAUSIBLE_CX`)
and the `CENTRE_DEADBAND` that stops a crop churning for a subject already
near the centre. This module consumes that track and turns it into a crop
window per frame; it does not re-detect faces and does not invent a position
the track does not carry.

What was wrong before
--------------------
A social crop was a dead-centre crop: ffmpeg `crop=iw/2:ih` (or Resolve's
own centre conform) takes the middle of the frame, and a speaker in the left
third loses the left side of their head. The pipeline had the subject's
measured position in hand and was not using it for the crop. This module
uses it.

Two deliberate limits
---------------------
**The track is HORIZONTAL.** `face_center_x` is a per-clip horizontal centre;
no vertical position is recorded, so the crop follows the subject left-right
and sits at the vertical centre when the target aspect leaves a vertical
choice. A vertical track would be a measurement, not a heuristic, and none
exists yet.

**Silence is a real answer.** A stretch with no usable detection holds the
last known position rather than jumping to centre: a subject who steps out
of frame for a moment does not yank the crop across the picture. A stretch
with NO detection at all leaves the crop centred, which is the honest
default when the footage cannot support a position.

How the crop reaches the pixels
-------------------------------
The crop path is a list of :class:`CropWindow`, one per sample. The ffmpeg
application writes them as a `sendcmd` file (``sendcmd=f=<path>``) and runs
ffmpeg's `crop` filter under it, so the window moves smoothly with the
subject. The command-file writer is pure and unit-tested; the ffmpeg run is
the only heavy half and is proven on a fixture.

Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module. They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline and
points here.

**Subject position comes from `face_center_x`, not from the vision pass.**
`vision_pipeline_v3` measures shot size, identity and time ranges - never a position - and `object_segmentation`/`ocr_extraction` produce boxes (1.06 runs matte-triggered; 1.07 is wired and deselected by default).
`step_1_04_temporal_index.compute_face_presence` emits the horizontal centre of the largest detected face at 5Hz; `library/tools/subject_framing.py` reduces it per clip and returns a POSITION; `compile_manifest._conform_fields` owns the one copy of the geometry that turns it into a pan.
- The join is `subject_centers_by_clip`, and it reads the per-clip index FILES, not pipeline state. An earlier spelling walked the in-state `full_indices` mapping while step 1.04 emits a LIST, so it returned `{}` on every real run while its tests - all fed invented mappings - passed. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)
- **None means "frame centred" - do not replace it with a fabricated 0.5.**
  That None is two facts - measured-centred and unmeasurable - and
  :func:`subject_center_reading` tells them apart. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
`compute_face_presence` records `face_width` beside `face_center_x`; `subject_framing.subject_box` reduces both, and `SUBJECT_HEADROOM` is how much clear space the subject needs on each side.
- **No zoom both fills the frame and holds an over-wide subject** - every zoom below fill leaves bars - so `_conform_fields` SYNTHESISES the missing picture: shrink the source until the subject fits and put the same frame again, scaled to cover and blurred, behind it. That is the `framing_backdrop` route, drawn by `fx.subject_backdrop` and dispatched on `backdrop_picture_scale`.

`SUBJECT_HEADROOM` is how much clear space the subject needs on each side. [why](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)
- `_conform_fields` SYNTHESISES missing picture via the `framing_backdrop` route (`fx.subject_backdrop`).
- A backdrop clip carries **no `framing_pan_x`**.
- **The verdict is carried by the PLAN check**, `manifest_validator`'s P8. `render_qa.measure_face_intact` is the render-side backstop and is weaker on purpose: a face cropped hard enough stops being detectable at all.
- An explicit `framing_pan_x` still outranks the measurement, and the clip then keeps its crop with `subject_safe_zoom` recorded so P8 can say what that cost.
- `tests/unit/picture/test_subject_framing.py`.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

try:
    from library.tools.subject_framing import (
        CENTRE_DEADBAND,
        MAX_PLAUSIBLE_CX,
        MIN_PLAUSIBLE_CX,
    )
except ImportError:  # imported as a top-level module from library/tools
    from subject_framing import (  # type: ignore[no-redef]
        CENTRE_DEADBAND,
        MAX_PLAUSIBLE_CX,
        MIN_PLAUSIBLE_CX,
    )


@dataclass(frozen=True)
class CropWindow:
    """One crop window in source pixels.

    ``x``/``y`` is the top-left corner, ``w``/``h`` the size. The window
    always has the target aspect and always fits inside the source, so
    ``0 <= x`` and ``x + w <= source_width`` (and likewise vertically).
    """

    x: float
    y: float
    w: float
    h: float


def crop_window_size(source_width: float, source_height: float,
                     target_width: float, target_height: float
                     ) -> Tuple[float, float]:
    """The largest window of the target aspect that fits inside the source.

    The window keeps the target's aspect exactly and is as large as the
    source allows, so a 1920x1080 source reframed to 9:16 yields a
    607.5x1080 window (full height, narrow width) and to 1:1 a 1080x1080
    window. The dimension the source is too short in becomes the window's
    full extent; the other is cropped.
    """
    if source_width <= 0 or source_height <= 0:
        raise ValueError(
            f"source dimensions must be positive, got "
            f"{source_width}x{source_height}")
    if target_width <= 0 or target_height <= 0:
        raise ValueError(
            f"target dimensions must be positive, got "
            f"{target_width}x{target_height}")
    target_aspect = target_width / target_height
    source_aspect = source_width / source_height
    if source_aspect >= target_aspect:
        # The source is wider than the target: the window is full height and
        # cropped on the sides.
        win_h = source_height
        win_w = source_height * target_aspect
    else:
        # The source is taller than the target: the window is full width and
        # cropped top and bottom.
        win_w = source_width
        win_h = source_width / target_aspect
    return win_w, win_h


def _plausible(value: Optional[float]) -> Optional[float]:
    """A detection inside the plausibility bounds, else None.

    The same bounds `subject_framing` applies per clip: a face hard against
    the frame edge is usually a background extra or a false positive on a
    bright rectangle, and aiming a crop at it is how a crop jerks to the
    edge on a non-subject.
    """
    if value is None:
        return None
    try:
        cx = float(value)
    except (TypeError, ValueError):
        return None
    if not (MIN_PLAUSIBLE_CX <= cx <= MAX_PLAUSIBLE_CX):
        return None
    return cx


def _fill_gaps(centers: Sequence[Optional[float]]) -> List[float]:
    """Forward-fill, then back-fill, the None gaps.

    A subject who is undetected for a moment does not move: the crop holds
    the last known position. A leading run of Nones (before the first
    detection) is back-filled from the first detection, so the crop starts
    on the subject rather than at centre and then jumps.
    """
    plausible = [_plausible(v) for v in centers]
    first: Optional[float] = None
    for cx in plausible:
        if cx is not None:
            first = cx
            break
    filled: List[float] = []
    last: Optional[float] = None
    for i, cx in enumerate(plausible):
        if cx is not None:
            last = cx
            filled.append(cx)
        elif last is not None:
            filled.append(last)  # interior gap: hold the subject still
        elif first is not None:
            filled.append(first)  # leading gap: start on the subject
        else:
            filled.append(0.5)  # no detection anywhere: centred
    return filled


def _smooth(values: Sequence[float], window: int) -> List[float]:
    """A centered moving average over ``window`` samples.

    The face track is noisy - a 5 Hz cascade with false positives - and a
    crop that tracks every sample jitters. The average smooths it. The
    window is clamped to at least 1 and at most the length, and the edges
    use a shrinking window so the first and last samples still get a
    smoothed value rather than a raw one.
    """
    n = len(values)
    if n == 0:
        return []
    window = max(1, min(int(window), n))
    half = window // 2
    out: List[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        chunk = values[lo:hi]
        out.append(sum(chunk) / float(len(chunk)))
    return out


def subject_crop_path(
        source_width: float,
        source_height: float,
        target_width: float,
        target_height: float,
        face_centers: Sequence[Optional[float]],
        sample_rate_hz: float,
        smooth_seconds: float = 0.5,
        ) -> List[CropWindow]:
    """One crop window per sample, following the subject.

    ``face_centers`` is the per-sample horizontal centre of the subject
    (0.0 = left, 1.0 = right), the `face_center_x` track
    `step_1_04_temporal_index` emits, at ``sample_rate_hz``. ``None`` means
    "no usable detection this sample".

    The window keeps the target aspect, is positioned to keep the subject
    near the horizontal centre (inside `CENTRE_DEADBAND` of centre the crop
    does not move, so a centred subject does not churn), is clamped inside
    the source, and is smoothed over ``smooth_seconds`` so it does not
    jitter. Vertically the window is centred when the target aspect leaves
    a vertical choice; there is no vertical subject track to follow.
    """
    if sample_rate_hz <= 0:
        raise ValueError(f"sample_rate_hz must be positive, got {sample_rate_hz}")
    win_w, win_h = crop_window_size(
        source_width, source_height, target_width, target_height)
    if not face_centers:
        # No track at all: one centred window, which is the honest default
        # when the footage cannot support a position.
        return [_centred_window(source_width, source_height, win_w, win_h)]

    filled = _fill_gaps(face_centers)
    window = max(1, int(round(smooth_seconds * sample_rate_hz)))
    smoothed = _smooth(filled, window)

    max_x = max(0.0, source_width - win_w)
    max_y = max(0.0, source_height - win_h)
    y = max_y / 2.0  # vertical centre; no vertical track to follow
    out: List[CropWindow] = []
    for cx in smoothed:
        # Inside the deadband the subject is centred enough; moving would be
        # visible churn for nothing (the same rule `subject_framing` applies
        # per clip).
        if abs(cx - 0.5) < CENTRE_DEADBAND:
            x = max_x / 2.0
        else:
            x = cx * source_width - win_w / 2.0
        x = max(0.0, min(max_x, x))
        out.append(CropWindow(x=x, y=y, w=win_w, h=win_h))
    return out


def _centred_window(source_width, source_height, win_w, win_h) -> CropWindow:
    max_x = max(0.0, source_width - win_w)
    max_y = max(0.0, source_height - win_h)
    return CropWindow(x=max_x / 2.0, y=max_y / 2.0, w=win_w, h=win_h)


def assemble_face_track(segments: Sequence[Tuple[float, float, dict]],
                        sample_rate_hz: float,
                        duration_seconds: float) -> List[Optional[float]]:
    """A per-sample subject-centre track over a whole video, from its clips.

    ``segments`` is one ``(timeline_start, source_start, face_presence)``
    per clip, where ``timeline_start`` is the second the clip begins on the
    timeline (the master's clock), ``source_start`` the source second that
    plays at ``timeline_start``, and ``face_presence`` the clip's
    ``face_presence`` block - ``face_center_x`` indexed by the clip's own
    SOURCE second (0 = the clip's source start), the same clock
    `subject_framing.subject_center_x` reads.

    A segment owns the timeline range its track covers -
    ``[timeline_start, timeline_start + len(centers)/rate)`` - so two
    clips never claim the same master second. The result has one entry per
    sample over ``duration_seconds`` at ``sample_rate_hz``: the subject's
    horizontal centre at that master second, or ``None`` where the playing
    clip has no usable detection.

    This is the bridge that lets the crop path run on a real project: the
    spine and the manifest name each clip's timeline placement and source
    range, and this turns them into the track `subject_crop_path` reads.
    """
    if sample_rate_hz <= 0:
        raise ValueError(f"sample_rate_hz must be positive, got {sample_rate_hz}")
    if duration_seconds <= 0:
        raise ValueError(
            f"duration_seconds must be positive, got {duration_seconds}")
    total = int(round(duration_seconds * sample_rate_hz))
    track: List[Optional[float]] = [None] * total
    for timeline_start, source_start, face_presence in segments:
        centers = (face_presence or {}).get("face_center_x") or []
        if not centers:
            continue
        segment_duration = len(centers) / float(sample_rate_hz)
        segment_end = timeline_start + segment_duration
        for i in range(total):
            t = i / sample_rate_hz
            if not (timeline_start <= t < segment_end):
                continue
            source_second = source_start + (t - timeline_start)
            idx = int(source_second * sample_rate_hz)
            if idx < 0 or idx >= len(centers):
                continue
            value = centers[idx]
            if value is None:
                continue
            try:
                cx = float(value)
            except (TypeError, ValueError):
                continue
            if MIN_PLAUSIBLE_CX <= cx <= MAX_PLAUSIBLE_CX:
                track[i] = cx
    return track


def write_sendcmd(crop_path: Sequence[CropWindow], path: str,
                  fps: float) -> str:
    """Write the ffmpeg `sendcmd` file for a crop path, and return its path.

    Each line is ``<start> <end> crop w:h:x:y;`` - the interval the command
    applies to, the `crop` filter, and its arguments in pixels. The window
    is held constant across each interval and updated at the interval
    boundary, which is how a smooth per-frame crop reaches ffmpeg's `crop`
    filter. The windows are rounded to whole pixels because ffmpeg's `crop`
    takes integers.
    """
    if fps <= 0:
        raise ValueError(f"fps must be positive, got {fps}")
    lines: List[str] = []
    n = len(crop_path)
    for i, win in enumerate(crop_path):
        start = i / fps
        end = (i + 1) / fps
        x, y = int(round(win.x)), int(round(win.y))
        w, h = int(round(win.w)), int(round(win.h))
        lines.append(f"{start:.6f} {end:.6f} crop w={w}:h={h}:x={x}:y={y};")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return path


def reframe_video(input_path: str, output_path: str,
                  crop_path: Sequence[CropWindow], target_width: int,
                  target_height: int, fps: float,
                  sendcmd_path: str = "") -> dict:
    """Apply a crop path to a video, producing the reframed file.

    The crop path is written as a `sendcmd` file and ffmpeg's `crop` filter
    runs under it, so the window follows the subject. The output is scaled
    to the target dimensions. The audio stream is copied untouched - the
    picture is reframed, not the sound.

    Returns a dict with the output path, the sendcmd path and the command
    ffmpeg ran. Raises on a ffmpeg failure: a reframed file that was not
    produced is not a quiet success.
    """
    if not crop_path:
        raise ValueError("crop_path is empty - nothing to reframe")
    if not os.path.exists(input_path):
        raise RuntimeError(f"input video not found: {input_path}")
    sendcmd_path = sendcmd_path or os.path.splitext(output_path)[0] \
        + "_crop_cmds.txt"
    write_sendcmd(crop_path, sendcmd_path, fps)
    cmd = [
        "ffmpeg", "-nostdin", "-y", "-i", input_path,
        "-vf", f"sendcmd=f={sendcmd_path},"
                f"crop=w={target_width}:h={target_height}:x=0:y=0,"
                f"scale={target_width}:{target_height}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-c:a", "copy", output_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=1800,
                          check=False)
    if proc.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(
            f"reframe failed for {input_path}: {proc.stderr[-2000:]}")
    return {
        "output_path": output_path,
        "sendcmd_path": sendcmd_path,
        "command": cmd,
        "crop_windows": len(crop_path),
    }
