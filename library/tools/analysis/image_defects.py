"""Deterministic, bounded measurements of common image defects.

These are measurements of decoded pixels, not diagnoses of camera or
optical causes. In particular, Laplacian sharpness is only a sharpness
proxy: it cannot identify focus error, motion blur, or low texture.

The decoder emits one downscaled frame at a time so memory use is bounded
by frame size, independent of source duration. Temporal measurements are
sampled; events shorter than the sampling period can be missed.

Run ``python3 -m library.tools.analysis.image_defects <source> --json``
to measure and store one source under its content digest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

import numpy as np

from library.tools import shared_decode

SAMPLE_RATE_HZ = 5.0
SAMPLE_SHORT_SIDE_PX = 180
BLACK_LUMA_MAX = 0.02
BLACK_LUMA_P99_MAX = 0.05
# Clipping rails on the decoded 8-bit grayscale image. The small
# codec/rounding margin matches `picture_quality._sample_measurements`;
# these are near-black/near-white pixel fractions after decode, not an
# exposure-compensation recommendation.
CLIP_BLACK_LUMA_MAX = 4
CLIP_WHITE_LUMA_MIN = 251
FROZEN_MAD_MAX = 0.001
FLICKER_RELATIVE_STEP = 0.08
BLOCK_SIZE = 8
METHOD = "image_defects_v1"
METHOD_PARAMETERS = {
    "black_mean_luma_max": BLACK_LUMA_MAX,
    "black_p99_luma_max": BLACK_LUMA_P99_MAX,
    "clipping_black_luma_max": CLIP_BLACK_LUMA_MAX,
    "clipping_white_luma_min": CLIP_WHITE_LUMA_MIN,
    "frozen_mean_absolute_difference_max": FROZEN_MAD_MAX,
    "flicker_luma_step_min": FLICKER_RELATIVE_STEP,
    "flicker_alternating_steps": 3,
    "compression_block_size_px": BLOCK_SIZE,
}


class ImageDefectsUnavailable(RuntimeError):
    """The source could not be sampled, so no defect verdict is available."""


def _dimensions(path: str) -> tuple[int, int]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration:stream_tags=rotate:stream_side_data=rotation:format=duration",
         "-of", "json", path], capture_output=True, encoding="utf-8",
        errors="replace", timeout=30, check=False)
    if probe.returncode:
        raise ImageDefectsUnavailable("ffprobe could not inspect source")
    try:
        stream = (json.loads(probe.stdout).get("streams") or [])[0]
        width, height = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ImageDefectsUnavailable("source has no readable video size") from exc
    if width <= 0 or height <= 0:
        raise ImageDefectsUnavailable("source has invalid video dimensions")
    side_data = stream.get("side_data_list") or []
    rotation = next((float(item["rotation"]) for item in side_data
                     if item.get("rotation") is not None), 0.0)
    if not rotation:
        try:
            rotation = float((stream.get("tags") or {}).get("rotate", 0) or 0)
        except (TypeError, ValueError):
            rotation = 0.0
    if abs(rotation) in (90, 270):
        width, height = height, width
    try:
        duration = float(stream.get("duration") or
                         json.loads(probe.stdout).get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return width, height, duration


def _sample_size(width: int, height: int) -> tuple[int, int]:
    scale = min(1.0, SAMPLE_SHORT_SIDE_PX / min(width, height))
    return max(4, int(width * scale) // 2 * 2), max(4, int(height * scale) // 2 * 2)


def _decode(path: str, rate_hz: float):
    """Yield decoded frames without retaining the source-sized frame list.

    The frames come from the shared decode
    (`library/tools/shared_decode`): the picture sampler measures the
    same frames of the same source, so one decode of them serves both
    lanes.  The bytes are the bytes this lane produced before - the two
    commands differed only in scale's range flags, which do not change
    the decoded greyscale - and a partial trailing frame still refuses,
    as it did.
    """
    if rate_hz <= 0:
        raise ValueError("rate_hz must be positive")
    width, height, duration = _dimensions(path)
    out_w, out_h = _sample_size(width, height)
    recipe = shared_decode.Recipe(rate_hz=rate_hz, width=out_w, height=out_h)
    try:
        decoded = shared_decode.sampled_frames(path, recipe)
    except shared_decode.SharedDecodeUnavailable as exc:
        raise ImageDefectsUnavailable(f"frame decode failed: {exc}") from exc
    if decoded.tail_bytes:
        raise ImageDefectsUnavailable("decoder returned a partial frame")
    frames = decoded.open()
    count = 0
    for frame in frames:
        count += 1
        yield (frame, out_w, out_h, count, duration)
    if not count:
        raise ImageDefectsUnavailable("ffmpeg decoded no frames or failed")


def _blockiness(frame: np.ndarray) -> float:
    """8-pixel boundary contrast excess over neighboring non-boundaries."""
    f = frame.astype(np.float32) / 255.0
    dx = np.abs(np.diff(f, axis=1))
    dy = np.abs(np.diff(f, axis=0))
    x_boundary = np.arange(1, frame.shape[1]) % BLOCK_SIZE == 0
    y_boundary = np.arange(1, frame.shape[0]) % BLOCK_SIZE == 0
    xb = float(dx[:, x_boundary].mean()) if x_boundary.any() else 0.0
    xn = float(dx[:, ~x_boundary].mean()) if (~x_boundary).any() else 0.0
    yb = float(dy[y_boundary, :].mean()) if y_boundary.any() else 0.0
    yn = float(dy[~y_boundary, :].mean()) if (~y_boundary).any() else 0.0
    return round(max(0.0, (xb - xn + yb - yn) / 2), 6)


def _clipping_fractions(frame: np.ndarray) -> tuple[float, float]:
    """Near-black and near-white pixel fractions of one sampled frame.

    Returns ``(underexposed_fraction, overexposed_fraction)`` in 0..1.
    A clipping proxy on decoded pixels: it records highlight/shadow
    loss at the rails, not a diagnosis of exposure error or a
    recommendation. Luma omits color-channel clipping.
    """
    pixels = frame.size
    if pixels <= 0:
        return 0.0, 0.0
    under = round(float(np.mean(frame <= CLIP_BLACK_LUMA_MAX)), 6)
    over = round(float(np.mean(frame >= CLIP_WHITE_LUMA_MIN)), 6)
    return under, over


def measure_track(track: dict, *, duration_s: float | None = None) -> dict:
    """Return per-sample metrics and sampled intervals for defect signals."""
    frames = track["frames"]
    rate = float(track["sample_rate_hz"])
    if rate <= 0 or not frames:
        raise ValueError("track requires frames and positive sample rate")
    lum = np.asarray([float(f.mean()) / 255.0 for f in frames])
    diff = np.asarray([float(np.abs(frames[i].astype(np.int16) -
                                    frames[i-1].astype(np.int16)).mean()) / 255.0
                       for i in range(1, len(frames))])
    black = [i for i, frame in enumerate(frames)
             if lum[i] <= BLACK_LUMA_MAX and
             float(np.percentile(frame, 99)) / 255.0 <= BLACK_LUMA_P99_MAX]
    frozen = [i + 1 for i, value in enumerate(diff) if value <= FROZEN_MAD_MAX]
    # Flicker is a sustained alternating luminance change, with enough
    # amplitude to avoid calling ordinary compression noise flicker.
    steps = np.diff(lum)
    flicker = []
    for i in range(2, len(steps)):
        window = steps[i-2:i+1]
        if (np.all(np.abs(window) >= FLICKER_RELATIVE_STEP)
                and np.all(window[:-1] * window[1:] < 0)):
            flicker.append(i + 1)
    # Temporal noise is a robust residual after removing global exposure
    # change between adjacent frames; it is not a sensor-noise diagnosis.
    noise = []
    for i in range(1, len(frames)):
        a = frames[i-1].astype(np.float32) / 255.0
        b = frames[i].astype(np.float32) / 255.0
        residual = b - a - float((b-a).mean())
        noise.append(round(float(np.mean(np.abs(residual))), 6))
    sharpness = []
    under_exposed, over_exposed = [], []
    for frame in frames:
        f = frame.astype(np.float32)
        lap = -4*f[1:-1,1:-1] + f[:-2,1:-1] + f[2:,1:-1] + f[1:-1,:-2] + f[1:-1,2:]
        sharpness.append(round(float(lap.var()), 3))
        under, over = _clipping_fractions(frame)
        under_exposed.append(under)
        over_exposed.append(over)
    max_end = len(frames) / rate
    end = min(max_end, float(duration_s)) if duration_s is not None else max_end
    return {
        "method": METHOD,
        "method_parameters": METHOD_PARAMETERS,
        "sampling": {"rate_hz": rate, "sample_count": len(frames),
                     "sample_size": list(track.get("sample_size", [int(frames[0].shape[1]), int(frames[0].shape[0])])),
                     "coverage": "sampled", "interval_s": [0.0, round(end, 3)]},
        "signals": {
            "temporal_noise": {"values": noise, "sample_rate_hz": rate,
                               "meaning": "global-luma-compensated temporal residual"},
            "compression_blockiness": {"values": [_blockiness(f) for f in frames],
                                        "sample_rate_hz": rate,
                                        "meaning": "8-pixel boundary contrast excess proxy"},
            "clipping_underexposed_fraction": {
                "values": under_exposed, "sample_rate_hz": rate,
                "meaning": "fraction of decoded pixels at luma <=4; "
                           "shadow-loss proxy, not an exposure recommendation"},
            "clipping_overexposed_fraction": {
                "values": over_exposed, "sample_rate_hz": rate,
                "meaning": "fraction of decoded pixels at luma >=251; "
                           "highlight-loss proxy, not an exposure recommendation"},
            "frozen_frame_sample_indices": frozen,
            "black_frame_sample_indices": black,
            "flicker_sample_indices": flicker,
            "sharpness_proxy_laplacian_variance": sharpness,
        },
        "limits": {"minimum_temporal_resolution_s": round(1.0/rate, 3),
                   "sharpness_is_focus_or_blur_cause": False},
    }


def measure_source(path: str, duration_s: float | None = None) -> dict:
    """Stream a source and return deterministic measures in O(frame) memory."""
    from library.tools.heavy_work_lock import heavy_work_lock

    # Source decode is serialized as bounded CPU and RAM work.
    with heavy_work_lock("vep-vi03", profile="temporal.index:image_defects"):
        return _measure_source_locked(path, duration_s)


def _measure_source_locked(path: str, duration_s: float | None) -> dict:
    rate = SAMPLE_RATE_HZ
    noise, block, sharp = [], [], []
    under_exposed, over_exposed = [], []
    black, frozen, flicker = [], [], []
    previous = None
    out_w = out_h = count = 0
    probed_duration = None
    lum, steps = [], []
    for frame, out_w, out_h, count, probed_duration in _decode(path, rate):
        current_luma = float(frame.mean()) / 255.0
        lum.append(current_luma)
        if (current_luma <= BLACK_LUMA_MAX and
                float(np.percentile(frame, 99)) / 255.0 <= BLACK_LUMA_P99_MAX):
            black.append(count - 1)
        block.append(_blockiness(frame))
        f = frame.astype(np.float32)
        lap = -4*f[1:-1,1:-1] + f[:-2,1:-1] + f[2:,1:-1] + f[1:-1,:-2] + f[1:-1,2:]
        sharp.append(round(float(lap.var()), 3))
        under, over = _clipping_fractions(frame)
        under_exposed.append(under)
        over_exposed.append(over)
        if previous is not None:
            a = previous.astype(np.float32) / 255.0
            b = f / 255.0
            delta = b - a
            d = float(np.abs(delta).mean())
            noise.append(round(float(np.mean(np.abs(delta - float(delta.mean())))), 6))
            if d <= FROZEN_MAD_MAX:
                frozen.append(count - 1)
            steps.append(current_luma - lum[-2])
            if (len(steps) >= 3 and
                all(abs(v) >= FLICKER_RELATIVE_STEP for v in steps[-3:]) and
                all(steps[-3+i] * steps[-2+i] < 0 for i in range(2))):
                flicker.append(count - 1)
        previous = frame
    if not count:
        raise ImageDefectsUnavailable("source yielded no frames")
    declared_duration = (float(duration_s) if duration_s is not None
                         else probed_duration)
    end = min(count / rate, declared_duration) if declared_duration else count / rate
    return {
        "method": METHOD,
        "method_parameters": METHOD_PARAMETERS,
        "sampling": {"rate_hz": rate, "sample_count": count,
                     "sample_size": [out_w, out_h], "coverage": "sampled",
                     "interval_s": [0.0, round(end, 3)]},
        "signals": {
            "temporal_noise": {"values": noise, "sample_rate_hz": rate,
                               "meaning": "global-luma-compensated temporal residual"},
            "compression_blockiness": {"values": block, "sample_rate_hz": rate,
                                        "meaning": "8-pixel boundary contrast excess proxy"},
            "clipping_underexposed_fraction": {
                "values": under_exposed, "sample_rate_hz": rate,
                "meaning": "fraction of decoded pixels at luma <=4; "
                           "shadow-loss proxy, not an exposure recommendation"},
            "clipping_overexposed_fraction": {
                "values": over_exposed, "sample_rate_hz": rate,
                "meaning": "fraction of decoded pixels at luma >=251; "
                           "highlight-loss proxy, not an exposure recommendation"},
            "frozen_frame_sample_indices": frozen,
            "black_frame_sample_indices": black,
            "flicker_sample_indices": flicker,
            "sharpness_proxy_laplacian_variance": sharp,
        },
        "limits": {"minimum_temporal_resolution_s": round(1.0/rate, 3),
                   "sharpness_is_focus_or_blur_cause": False},
    }


def build_source_image_defects(path: str, content_digest: str,
                               root=None, duration_s: float | None = None) -> dict:
    """Measure and persist a source-memory VI-03 slot."""
    from library.tools import source_memory

    record = measure_source(path, duration_s)
    record["content_digest"] = content_digest
    record["source_file"] = str(path)
    source_memory.write_image_defects(content_digest, record, root)
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure sampled image defects for one source file")
    parser.add_argument("source")
    parser.add_argument("--duration", type=float)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from library.tools import footage_identity

    digest = footage_identity.fingerprint(args.source)["content_digest"]
    record = build_source_image_defects(args.source, digest,
                                        duration_s=args.duration)
    if args.json:
        print(json.dumps(record, indent=2, sort_keys=True))
    else:
        print(f"VI-03 {digest[:12]}: {record['sampling']['sample_count']} "
              f"samples at {record['sampling']['rate_hz']:g} Hz; "
              "sharpness is a proxy, not a focus or blur-cause diagnosis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
