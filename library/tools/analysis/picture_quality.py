"""Per-sample objective picture measurements, measured from decoded pixels.

Why this exists
---------------
`usable_ranges` used to be asserted as `[[0, full_duration]]` for every
clip while `usable_ranges_method` said `unmeasured` right beside it.  The
B-roll selector reads that cell to decide which two and a half seconds of
a clip to cut, so on the 2026-08-26 run of project 001 it chose
`clip_006` 1.65-4.15s - a whip pan out of a moving car window - and the
master at 25.0s is motion-blurred asphalt with no legible subject.  The
selector's reasoning was sound; nothing had measured the window.

`vision_pipeline_v3._compute_usable_ranges` already derives ranges from
temporal-index signals, but step 1.03 runs BEFORE step 1.04 in the DAG,
so on a first run there is no temporal index and every clip came out
unmeasured.  This module needs nothing but the video file, so it measures
on the first run.

What it measures
----------------
`sample_picture_quality` returns per-sample Laplacian variance (focus/blur
signal), luma mean and percentiles, near-black/near-white pixel fractions,
a low-gradient median-residual noise proxy, and normalized difference from
the preceding sample. Methods, units, and limits are documented beside
`_sample_measurements`. `measurements_in_interval` queries timestamped rows
over a half-open interval. These are image measurements, not aesthetic
judgments. The motion-blur proxy pairs movement and relative softness; it
does not identify shutter blur versus focus loss or a scene cut.

The verdict is RELATIVE to the clip's own reference sharpness (a high
percentile of its own samples), with an ABSOLUTE floor underneath it.
Absolute-only cannot work: a flat asphalt shot is legitimately less
textured than a brick wall, and one threshold cannot serve both.
Relative-only cannot work either: a clip that is blurred end to end has a
low reference and would rate its own mush as normal.  Together they
answer "is this stretch soft for THIS clip, and is it soft in absolute
terms as well".

What it cannot resolve
----------------------
- It cannot tell motion blur from a missed focus pull from a genuinely
  low-texture subject.  The reason it records is `soft_picture`, which is
  what it actually measured, and not a cause it did not.
- At `SAMPLE_RATE_HZ` = 5 the samples are 0.2s apart, so a soft window
  shorter than 0.2s can fall entirely between two samples and is
  invisible.
- `MIN_SOFT_RUN_S` = 0.6 needs three consecutive soft samples, so the
  shortest window it is guaranteed to report is 0.6s.  Anything shorter
  may land on only two samples and be dropped.  That floor is deliberate:
  a single soft frame in a pan is not a reason to fence off footage.
- It does not identify a subject, composition, or obstruction. Subject
  position is measured separately by the face track in step 1.04 and
  `subject_framing`; this sampler does not invent those boxes.
- The noise value is a low-gradient high-pass residual in decoded,
  compressed pixels. It is not sensor-noise ground truth.
- Clipping uses luma <=4 and >=251 on the decoded 8-bit grayscale image.

Cost
----
Decoding is the whole cost.  Measured on project 001 (17 clips, 807s of
1080p footage) on a cold page cache: 2.79s per clip, 0.059x realtime.
The vision pass beside it costs ~140s per clip of model time.
[why](docs/RULE_EVIDENCE.md#usable-ranges-were-the-whole-clip)

Run it standalone against a clip:

    python3 -m library.tools.analysis.picture_quality <video> [--quality]
        [--start S --end S]


Rules relocated from AGENTS.md 10.3
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.3 keeps the headline
and points here.

**`usable_ranges` is a measurement, and an absent one is EMPTY - never the whole clip.**
[why](docs/RULE_EVIDENCE.md#usable-ranges-were-the-whole-clip)
- `[]` with method `unmeasured` means nobody looked; `[]` with method `deterministic_v1` means the clip was measured and none of it is usable. `usable_ranges_summary` renders the two differently and neither as a blank cell.
- The signals that measured it are named in `usable_ranges_signals`. `library/tools/analysis/picture_quality.py` is the one that needs only the video file, so it is the one that works on a first run - 1.03 runs BEFORE 1.04, so the temporal-index rules have nothing to read until a re-run.
- `picture_quality.py` samples at 5 Hz, reports runs of 0.6s or longer. **State that bound when you report a verdict.**
"""

import argparse
import json
import subprocess
import sys

import cv2
import numpy as np

from library.tools import shared_decode

# ── Sampling ────────────────────────────────────────────────────────
#
# 5 Hz matches the rate step 1.04 already samples face presence at, so
# the two tracks are directly comparable when both exist.
SAMPLE_RATE_HZ = 5.0

# The SHORT side is bounded, never the height, so a portrait clip and a
# landscape clip are measured at the same detail scale.  The absolute
# floor below is tied to this geometry; changing one means recalibrating
# the other.
SAMPLE_SHORT_SIDE_PX = 180

# Below this many samples there is not enough of the clip to establish a
# reference percentile, and a reference from three frames is noise.
MIN_SHARPNESS_SAMPLES = 10

# ── Verdict ─────────────────────────────────────────────────────────
#
# The clip's own reference sharpness. A high percentile rather than the
# max: one specular highlight should not set the bar for the clip.
SHARPNESS_REFERENCE_PERCENTILE = 90

# A sample is soft below this fraction of the clip's reference.
SHARPNESS_RELATIVE_FRACTION = 0.20

# ...and never counted soft above this absolute value, whatever the
# clip's own reference says.  Calibrated on project 001 at
# SAMPLE_SHORT_SIDE_PX: see the calibration table in
# tests/unit/picture/test_picture_quality.py.
SHARPNESS_ABSOLUTE_FLOOR = 80.0

# Consecutive soft time before it is worth fencing off.
MIN_SOFT_RUN_S = 0.6

# What the measurement saw, not what caused it.
SOFT_PICTURE_REASON = "soft_picture"

# The name this signal reports itself as in `usable_ranges_signals`.
SIGNAL_NAME = "picture_sharpness"

FFPROBE_TIMEOUT_S = 30
FFMPEG_TIMEOUT_S = 600


class PictureQualityUnavailable(RuntimeError):
    """The clip could not be sampled, so nothing was measured.

    Raised rather than returning an empty track: "no soft ranges found"
    and "never looked" are different answers and the caller has to be
    able to tell them apart.
    """


def _display_dimensions(video_path):
    """The (width, height) ffmpeg's filter chain will see.

    Rotation side data is applied, because autorotate runs before the
    filter chain - the same reading `face_sample_dimensions` in step 1.04
    makes.  Raises rather than assuming a shape.
    """
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "v:0", str(video_path)],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=FFPROBE_TIMEOUT_S, check=False,
    )
    if result.returncode != 0:
        raise PictureQualityUnavailable(
            f"ffprobe could not read {video_path}")
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except json.JSONDecodeError as exc:
        raise PictureQualityUnavailable(
            f"ffprobe returned unreadable JSON for {video_path}") from exc
    if not streams:
        raise PictureQualityUnavailable(f"no video stream in {video_path}")

    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise PictureQualityUnavailable(f"no frame size for {video_path}")

    rotation = 0
    for side_data in stream.get("side_data_list") or []:
        if "rotation" in side_data:
            rotation = int(side_data["rotation"])
            break
    if rotation == 0:
        try:
            rotation = int((stream.get("tags") or {}).get("rotate", 0))
        except (TypeError, ValueError):
            rotation = 0
    if abs(rotation) in (90, 270):
        width, height = height, width

    return width, height


def _sample_dimensions(width, height, short_side=SAMPLE_SHORT_SIDE_PX):
    """Even (width, height) with the short side bounded, aspect kept."""
    target = min(short_side, min(width, height))
    if width <= height:
        out_w, out_h = float(target), target * height / float(width)
    else:
        out_h, out_w = float(target), target * width / float(height)
    def even(value):
        # Several ffmpeg filters and codecs require even dimensions.
        return max(2, round(value / 2.0) * 2)

    return even(out_w), even(out_h)


def laplacian(frame):
    """The 4-neighbour Laplacian of one greyscale frame, `(h-2, w-2)`.

    Public because `library/tools/region_stats.py` reads the same
    operator inside a region mask rather than over the whole frame."""
    return (-4.0 * frame[1:-1, 1:-1]
            + frame[:-2, 1:-1] + frame[2:, 1:-1]
            + frame[1:-1, :-2] + frame[1:-1, 2:])


def _laplacian_variance(frame):
    """Variance of the 4-neighbour Laplacian over one greyscale frame."""
    return round(float(laplacian(frame).var()), 3)


def _sample_measurements(frame, previous=None):
    """Objective per-frame pixel readings; no aesthetic verdicts.

    Luma is full-range 8-bit after ffmpeg's gray conversion. The clipping
    fractions use <= 4 and >= 251 to allow a small codec/rounding margin.
    The noise reading is a robust MAD estimate of the 3x3-median high-pass
    residual, restricted to low-gradient pixels; it is a texture/noise
    proxy in compressed footage, not a sensor-noise estimate. Motion is the
    mean absolute difference from the preceding sampled frame, normalized
    to 0..1. Pairing that with focus exposes motion-associated softness but
    does not claim to identify its optical cause.
    """
    image = np.asarray(frame, dtype=np.uint8)
    luma = image.astype(np.float32)
    residual = luma - cv2.medianBlur(image, 3).astype(np.float32)
    gy = cv2.Sobel(image, cv2.CV_32F, 0, 1, ksize=3)
    gx = cv2.Sobel(image, cv2.CV_32F, 1, 0, ksize=3)
    flat = (gx * gx + gy * gy) < (12.0 * 12.0)
    residual_values = residual[flat]
    if residual_values.size:
        median = float(np.median(residual_values))
        noise_sigma = 1.4826 * float(np.median(np.abs(residual_values - median)))
    else:
        noise_sigma = None
    return {
        "focus_laplacian_variance": _laplacian_variance(luma),
        "luma_mean": round(float(luma.mean()), 3),
        "luma_p05": round(float(np.percentile(luma, 5)), 3),
        "luma_p95": round(float(np.percentile(luma, 95)), 3),
        "underexposed_pixel_fraction": round(float(np.mean(image <= 4)), 6),
        "overexposed_pixel_fraction": round(float(np.mean(image >= 251)), 6),
        "low_gradient_residual_sigma_luma": (
            round(noise_sigma, 3) if noise_sigma is not None else None),
        "frame_difference_luma_fraction": (
            round(float(np.mean(np.abs(luma - previous.astype(np.float32)))) / 255.0, 6)
            if previous is not None else None),
    }


def sample_picture_quality(video_path, rate_hz=SAMPLE_RATE_HZ,
                           short_side=SAMPLE_SHORT_SIDE_PX,
                           start_s=0.0, end_s=None):
    """Decode and measure focus, exposure, clipping, residual texture and motion.

    Each sample has a timestamp in seconds from the displayed clip start.
    Its pixel geometry is the decoded display frame after autorotation and
    aspect-preserving scale. Spatial positions elsewhere in the pipeline use
    normalized display-frame coordinates (origin top-left); this sampler
    emits frame-wide values and does not invent a subject or occlusion box.

    The frames come from the shared decode (`library/tools/shared_decode`):
    the VI-03 source sampler measures the same frames of the same source,
    and one decode of them serves both lanes.  The bytes are the bytes
    this sampler produced before, and they are read from disk rather than
    held in memory - a whole-source sample is gigabytes.
    """
    if start_s < 0 or (end_s is not None and end_s <= start_s):
        raise ValueError("picture-quality interval must satisfy 0 <= start < end")
    width, height = _display_dimensions(video_path)
    out_w, out_h = _sample_dimensions(width, height, short_side)
    recipe = shared_decode.Recipe(rate_hz=rate_hz, width=out_w, height=out_h,
                                  start_s=start_s, end_s=end_s)
    try:
        decoded = shared_decode.sampled_frames(video_path, recipe)
    except shared_decode.SharedDecodeUnavailable as exc:
        raise PictureQualityUnavailable(str(exc)) from exc
    n_frames = decoded.frame_count
    if n_frames == 0:
        raise PictureQualityUnavailable(f"ffmpeg decoded no frames from {video_path}")
    frames = decoded.open()
    samples = []
    for index, frame in enumerate(frames):
        row = {"time_s": round(start_s + index / rate_hz, 3)}
        row.update(_sample_measurements(frame, frames[index - 1] if index else None))
        samples.append(row)
    reference = float(np.percentile(
        [row["focus_laplacian_variance"] for row in samples],
        SHARPNESS_REFERENCE_PERCENTILE))
    threshold = max(SHARPNESS_ABSOLUTE_FLOOR,
                    SHARPNESS_RELATIVE_FRACTION * reference)
    for row in samples:
        motion = row["frame_difference_luma_fraction"]
        softness = max(0.0, 1.0 - row["focus_laplacian_variance"] / threshold)
        row["motion_blur_proxy"] = (
            round(motion * softness, 6) if motion is not None else None)
    return {
        "method": "picture_quality_pixels_v1",
        "sample_rate_hz": rate_hz,
        "sample_short_side_px": min(out_w, out_h),
        "coordinate_system": "display_frame_top_left_normalized_xy",
        "display_width_px": width,
        "display_height_px": height,
        "sampled_interval_s": [start_s, end_s],
        "motion_blur_proxy_method": (
            "frame_difference_luma_fraction * relative_softness_below_clip_threshold"),
        "measurements": samples,
    }


def measurements_in_interval(track, start_s, end_s):
    """Return samples whose timestamps fall in the half-open [start, end) interval."""
    if end_s <= start_s or not track or track.get("method") != "picture_quality_pixels_v1":
        return []
    return [sample for sample in track["measurements"]
            if start_s <= sample["time_s"] < end_s]


def sample_sharpness(video_path, rate_hz=SAMPLE_RATE_HZ,
                     short_side=SAMPLE_SHORT_SIDE_PX):
    """Sharpness per sample for one clip.

    Returns ``{"sample_rate_hz", "sample_short_side_px", "values"}``.
    Raises :class:`PictureQualityUnavailable` when the clip cannot be
    decoded at all.

    The frames come from the shared decode, one frame at a time: the
    whole sample used to be one in-memory float32 buffer, which for an
    82-minute source was gigabytes.
    """
    width, height = _display_dimensions(video_path)
    out_w, out_h = _sample_dimensions(width, height, short_side)
    recipe = shared_decode.Recipe(rate_hz=rate_hz, width=out_w, height=out_h)
    try:
        decoded = shared_decode.sampled_frames(video_path, recipe)
    except shared_decode.SharedDecodeUnavailable as exc:
        raise PictureQualityUnavailable(str(exc)) from exc
    if decoded.frame_count == 0:
        raise PictureQualityUnavailable(
            f"ffmpeg decoded no frames from {video_path}")
    frames = decoded.open()
    return {
        "sample_rate_hz": rate_hz,
        "sample_short_side_px": min(out_w, out_h),
        "values": [_laplacian_variance(frame.astype(np.float32))
                   for frame in frames],
    }


def sharpness_threshold(values):
    """The softness threshold for one clip's own sharpness samples.

    ``None`` when there are too few samples to establish a reference.
    """
    if len(values) < MIN_SHARPNESS_SAMPLES:
        return None
    reference = float(np.percentile(np.asarray(values, dtype=float),
                                    SHARPNESS_REFERENCE_PERCENTILE))
    return max(SHARPNESS_ABSOLUTE_FLOOR,
               SHARPNESS_RELATIVE_FRACTION * reference)


def soft_picture_ranges(track, duration, min_run_s=MIN_SOFT_RUN_S):
    """Runs of soft picture in ``track``, as unusable-range dicts.

    Returns ``None`` when the track cannot carry a verdict - too few
    samples, or no duration to bound the ranges with.  ``[]`` means
    measured and nothing soft found, and the two are not the same answer.
    """
    duration = float(duration or 0)
    if not track or duration <= 0:
        return None
    values = track.get("values") or []
    rate = float(track.get("sample_rate_hz") or 0)
    if rate <= 0:
        return None
    threshold = sharpness_threshold(values)
    if threshold is None:
        return None

    min_samples = max(1, int(min_run_s * rate))
    ranges = []
    run_start = None
    for i in range(len(values) + 1):
        if i < len(values) and values[i] < threshold:
            if run_start is None:
                run_start = i
            continue
        if run_start is not None:
            if i - run_start >= min_samples:
                start = run_start / rate
                if start < duration:
                    ranges.append({
                        "start": round(start, 3),
                        "end": round(min(i / rate, duration), 3),
                        "reason": SOFT_PICTURE_REASON,
                    })
            run_start = None
    return ranges


def measure_soft_picture(video_path, duration):
    """Soft-picture ranges for a clip, measured off the file.

    Returns ``None`` when nothing could be measured - an unreadable
    clip, or one too short to establish a reference - so the caller
    reports the absence instead of asserting the whole clip is fine.
    """
    try:
        track = sample_sharpness(video_path)
    except PictureQualityUnavailable:
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    return soft_picture_ranges(track, duration)


def _main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("video")
    parser.add_argument("--duration", type=float, default=None,
                        help="clip duration in seconds (probed if omitted)")
    parser.add_argument("--json", action="store_true",
                        help="emit the whole sharpness track, not a summary")
    parser.add_argument("--quality", action="store_true",
                        help="emit per-sample pixel measurements")
    parser.add_argument("--start", type=float, help="interval start in seconds")
    parser.add_argument("--end", type=float, help="interval end in seconds (exclusive)")
    args = parser.parse_args(argv)
    if (args.start is None) != (args.end is None):
        parser.error("--start and --end must be supplied together")

    duration = args.duration
    if duration is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", args.video],
            capture_output=True, text=True, encoding="utf-8",
            timeout=FFPROBE_TIMEOUT_S, check=False)
        duration = float(probe.stdout.strip() or 0)

    try:
        quality = sample_picture_quality(
            args.video, start_s=args.start or 0.0, end_s=args.end)
    except PictureQualityUnavailable as exc:
        print(json.dumps({"error": str(exc)}))
        return 1

    track = {"sample_rate_hz": quality["sample_rate_hz"],
             "sample_short_side_px": quality["sample_short_side_px"],
             "values": [row["focus_laplacian_variance"]
                        for row in quality["measurements"]]}
    ranges = soft_picture_ranges(track, duration)
    payload = {
        "video": args.video,
        "duration_s": duration,
        "sample_rate_hz": track["sample_rate_hz"],
        "sample_short_side_px": track["sample_short_side_px"],
        "samples": len(track["values"]),
        "sharpness_threshold": sharpness_threshold(track["values"]),
        "soft_picture_ranges": ranges,
    }
    if args.json:
        payload["values"] = track["values"]
    if args.quality:
        payload["picture_quality"] = quality
    if args.start is not None:
        payload["picture_quality_interval"] = {
            "start_s": args.start, "end_s": args.end,
            "measurements": measurements_in_interval(
                quality, args.start, args.end),
        }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
