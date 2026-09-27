"""Scoped regional motion tracks and advisory framing evidence.

This module samples only time ranges selected by a caller.  The temporal
index step supplies Gemma action ranges and its existing largest-face boxes;
Farneback flow then finds local moving regions outside those face boxes.  The
regions are deliberately not classified as hands or bodies: they are motion
candidates whose meaning remains for a person or a later vision decision.
"""

from __future__ import annotations

import json
import math
import re
import subprocess


SCHEMA_VERSION = 1
SAMPLE_RATE_HZ = 10
SAMPLE_WIDTH = 640
SAMPLE_HEIGHT = 360
DYNAMIC_CROP_DRIFT_THRESHOLD = 0.08
_MIN_COMPONENT_AREA = 24
_MAX_COMPONENT_FRACTION = 0.45
_MAX_TRACKS_PER_SPAN = 24
_MOTION_ACTION = re.compile(
    r"\b(?:gestur\w*|wave\w*|point\w*|reach\w*|raise\w*|lift\w*|"
    r"mov\w*|swing\w*|shak\w*|nod\w*|turn\w*|lean\w*|shift\w*|"
    r"walk\w*|run\w*|pick\w*|hold(?:ing)?\s+up|touch\w*|clap\w*|"
    r"type\w*|writ\w*|smil\w*|laugh\w*|frown\w*|blink\w*|wink\w*|"
    r"shrug\w*|stand(?:s|ing)?\s+up|sit(?:s|ting)?\s+down)\b",
    re.IGNORECASE,
)


def action_candidate_spans(semantic_document: dict | None,
                           duration: float) -> list[dict]:
    """Return the time-bounded spans Gemma actually labeled with an action.

    Untimed or malformed action rows are ignored rather than widened to a
    whole ten-second model window. Generic speech/posture labels do not
    select this motion pass; a physical-motion, gesture, or facial-change
    cue must appear in the label. Adjacent rows with the same source are
    merged, while preserving every label that selected the resulting range.
    """
    if not isinstance(semantic_document, dict):
        return []

    rows = []
    for window in semantic_document.get("actions") or []:
        if not isinstance(window, dict):
            continue
        for action in window.get("actions") or []:
            if not isinstance(action, dict):
                continue
            labels = [
                str(action.get(key) or "").strip()
                for key in ("action", "body_language")
            ]
            labels = [label for label in labels if label]
            if not labels or not any(_MOTION_ACTION.search(label)
                                     for label in labels):
                continue
            try:
                start = float(action["start"])
                end = float(action["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end):
                continue
            start = max(0.0, start)
            end = min(float(duration), end)
            if end <= start:
                continue
            rows.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "selected_by": "gemma_action",
                "action_labels": labels,
            })

    rows.sort(key=lambda row: (row["start"], row["end"]))
    merged = []
    for row in rows:
        if merged and row["start"] <= merged[-1]["end"] + 0.05:
            previous = merged[-1]
            previous["end"] = max(previous["end"], row["end"])
            previous["action_labels"] = sorted(set(
                previous["action_labels"] + row["action_labels"]))
        else:
            merged.append(row)
    return merged


def split_at_scene_boundaries(spans: list[dict],
                              scene_boundaries: list[dict] | None) -> list[dict]:
    """Split selected spans at the existing detector's boundaries.

    This consumes the current scene detector output and does not alter its
    threshold, sampling, or event list. No optical-flow pair crosses a cut
    already present in that list.
    """
    cuts = sorted({
        float(row["time"])
        for row in scene_boundaries or []
        if isinstance(row, dict)
        and row.get("type") != "start"
        and isinstance(row.get("time"), (int, float))
    })
    result = []
    for span in spans:
        start, end = float(span["start"]), float(span["end"])
        inside = [cut for cut in cuts if start < cut < end]
        edges = [start, *inside, end]
        for left, right in zip(edges, edges[1:]):
            if right - left < 1.0 / SAMPLE_RATE_HZ:
                continue
            result.append({**span, "start": round(left, 3),
                           "end": round(right, 3)})
    return result


def _valid_box(box) -> bool:
    return (
        isinstance(box, (list, tuple)) and len(box) == 4
        and all(isinstance(value, (int, float))
                and math.isfinite(float(value)) for value in box)
        and float(box[2]) > float(box[0])
        and float(box[3]) > float(box[1])
    )


def face_box_at(face_presence: dict | None, time_s: float,
                max_gap_s: float = 0.22) -> list[float] | None:
    """Interpolate the existing largest-face boxes at one sample time."""
    if not isinstance(face_presence, dict):
        return None
    boxes = face_presence.get("face_boxes")
    rate = face_presence.get("sample_rate_hz")
    if not isinstance(boxes, list) or not isinstance(rate, (int, float)):
        return None
    if float(rate) <= 0:
        return None

    sample = max(0.0, float(time_s)) * float(rate)
    low = int(math.floor(sample))
    high = int(math.ceil(sample))
    low_box = boxes[low] if low < len(boxes) and _valid_box(boxes[low]) else None
    high_box = (boxes[high] if high < len(boxes)
                and _valid_box(boxes[high]) else None)

    if low_box is not None and high_box is not None:
        if low == high:
            return [float(value) for value in low_box]
        fraction = sample - low
        return [round(float(a) + (float(b) - float(a)) * fraction, 6)
                for a, b in zip(low_box, high_box)]

    candidates = []
    for index in range(max(0, low - 1), min(len(boxes), high + 2)):
        box = boxes[index]
        sample_time = index / float(rate)
        if _valid_box(box) and abs(sample_time - time_s) <= max_gap_s:
            candidates.append((abs(sample_time - time_s), box))
    if not candidates:
        return None
    return [float(value) for value in min(candidates, key=lambda row: row[0])[1]]


def _display_aspect_ratio(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
         "-of", "json",
         video_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe could not read video geometry: {video_path}")
    streams = json.loads(result.stdout).get("streams") or []
    if not streams:
        raise RuntimeError(f"No video stream found: {video_path}")
    stream = streams[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Video has no display dimensions: {video_path}")
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
    return width / float(height)


def _active_rect(aspect_ratio: float) -> tuple[float, float, float, float]:
    """Content rectangle in the letterboxed 640x360 analysis canvas."""
    if aspect_ratio >= SAMPLE_WIDTH / SAMPLE_HEIGHT:
        width = float(SAMPLE_WIDTH)
        height = width / aspect_ratio
    else:
        height = float(SAMPLE_HEIGHT)
        width = height * aspect_ratio
    return ((SAMPLE_WIDTH - width) / 2.0,
            (SAMPLE_HEIGHT - height) / 2.0, width, height)


def _canvas_box(box: list[float], active_rect) -> tuple[int, int, int, int]:
    left, top, width, height = active_rect
    x1 = int(round(left + box[0] * width))
    y1 = int(round(top + box[1] * height))
    x2 = int(round(left + box[2] * width))
    y2 = int(round(top + box[3] * height))
    return (max(0, min(SAMPLE_WIDTH - 1, x1)),
            max(0, min(SAMPLE_HEIGHT - 1, y1)),
            max(1, min(SAMPLE_WIDTH, x2)),
            max(1, min(SAMPLE_HEIGHT, y2)))


def _source_box(box: tuple[int, int, int, int], active_rect) -> list[float]:
    left, top, width, height = active_rect
    x1, y1, x2, y2 = box
    return [
        round(max(0.0, min(1.0, (x1 - left) / width)), 4),
        round(max(0.0, min(1.0, (y1 - top) / height)), 4),
        round(max(0.0, min(1.0, (x2 - left) / width)), 4),
        round(max(0.0, min(1.0, (y2 - top) / height)), 4),
    ]


def _decode_frames(video_path: str, start: float,
                   end: float) -> list:
    import numpy as np

    duration = float(end) - float(start)
    vf = (f"setpts=PTS-STARTPTS,fps={SAMPLE_RATE_HZ},"
          f"scale={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}:"
          "force_original_aspect_ratio=decrease,"
          f"pad={SAMPLE_WIDTH}:{SAMPLE_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
          "format=gray")
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-ss", f"{start:.3f}", "-i", video_path,
         "-t", f"{duration:.3f}", "-vf", vf, "-an", "-f", "rawvideo",
         "-pix_fmt", "gray", "-"],
        capture_output=True, timeout=max(120, int(duration * 10)),
        check=False,
    )
    frame_size = SAMPLE_WIDTH * SAMPLE_HEIGHT
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"regional motion decode failed: {detail}")
    count = len(result.stdout) // frame_size
    if count == 0:
        raise RuntimeError("regional motion decode produced no frames")
    raw = np.frombuffer(result.stdout[:count * frame_size], dtype=np.uint8)
    return list(raw.reshape(count, SAMPLE_HEIGHT, SAMPLE_WIDTH))


def _box_center(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _box_union(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    return [round(min(box[0] for box in boxes), 4),
            round(min(box[1] for box in boxes), 4),
            round(max(box[2] for box in boxes), 4),
            round(max(box[3] for box in boxes), 4)]


def _center_drift(observations: list[dict]) -> float:
    """Return the 90th-percentile center excursion from the track median.

    Haar boxes and thresholded flow components can produce brief outliers.
    A maximum would let one bad detection decide whether the whole span
    needs a dynamic crop, so use a robust percentile for both face and
    local-motion trajectories.
    """
    centers = [
        _box_center(row["box"])
        for row in observations if _valid_box(row.get("box"))
    ]
    if len(centers) < 2:
        return 0.0
    median_x = sorted(point[0] for point in centers)[len(centers) // 2]
    median_y = sorted(point[1] for point in centers)[len(centers) // 2]
    excursions = sorted(math.hypot(x - median_x, y - median_y)
                        for x, y in centers)
    percentile_index = min(len(excursions) - 1,
                           math.ceil(0.9 * len(excursions)) - 1)
    return excursions[percentile_index]


def _framing_suggestions(face_observations: list[dict], tracks: list[dict],
                         span: dict) -> tuple[dict, list[dict]]:
    regions = []
    if face_observations:
        regions.append({
            "region_id": "largest_face",
            "region_type": "face",
            "box": _box_union([row["box"] for row in face_observations]),
            "start": face_observations[0]["time"],
            "end": face_observations[-1]["time"],
        })
    for track in tracks:
        observations = track["observations"]
        regions.append({
            "region_id": track["track_id"],
            "region_type": "motion_candidate",
            "box": track["envelope"],
            "start": observations[0]["time"],
            "end": observations[-1]["time"],
        })

    envelope = _box_union([row["box"] for row in regions if row["box"]])
    face_drift = _center_drift(face_observations)
    motion_drifts = [
        _center_drift(track["observations"]) for track in tracks
    ]
    motion_drift = max(motion_drifts, default=0.0)
    measured = bool(face_observations)
    if not measured:
        recommendation = "insufficient_face_boxes"
        dynamic = None
        reason = (
            "Regional motion was measured, but the existing face-box track "
            "has no boxes in this span; a fused crop recommendation cannot "
            "be made."
        )
    else:
        # Local motion changes what a static crop must preserve, but a
        # gesture by itself does not mean the crop should chase it. Dynamic
        # reframing follows the face track; the fused motion envelope and
        # keep-clear rows tell Ren whether the action still fits that crop.
        dynamic = face_drift >= DYNAMIC_CROP_DRIFT_THRESHOLD
        recommendation = ("consider_dynamic_crop" if dynamic
                          else "no_dynamic_crop_needed")
        reason = (
            f"Largest-face p90 center drift is {face_drift:.1%}; the "
            f"greatest local-motion p90 center drift is {motion_drift:.1%}; "
            f"the face-reposition threshold is "
            f"{DYNAMIC_CROP_DRIFT_THRESHOLD:.0%}. Keep the fused face and "
            "local-motion envelope inside any crop; gesture motion alone "
            "does not trigger face-follow reframing."
        )

    crop = {
        "recommendation": recommendation,
        "dynamic_crop_needed": dynamic,
        "source_range": [round(float(span["start"]), 3),
                         round(float(span["end"]), 3)],
        "content_envelope": envelope,
        "preserve_region_ids": [row["region_id"] for row in regions],
        "face_center_drift": round(face_drift, 4) if measured else None,
        "local_motion_center_drift": round(motion_drift, 4),
        "reposition_threshold": DYNAMIC_CROP_DRIFT_THRESHOLD,
        "reason": reason,
    }
    keep_clear = [
        {
            "region_id": region["region_id"],
            "region_type": region["region_type"],
            "source_range": [region["start"], region["end"]],
            "box": region["box"],
            "suggestion": (
                "Keep captions, lower-thirds, and other critical graphics "
                "outside this measured occupied region."
            ),
        }
        for region in regions if region["box"] is not None
    ]
    return crop, keep_clear


def _track_components(flow, valid_mask, excluded_mask, timestamp: float,
                      active_rect, cv2, np) -> list[dict]:
    residual = flow.copy()
    valid = valid_mask & ~excluded_mask
    if int(valid.sum()) < _MIN_COMPONENT_AREA:
        return []
    background = np.median(residual[valid], axis=0)
    residual[:, :, 0] -= float(background[0])
    residual[:, :, 1] -= float(background[1])
    magnitude = np.linalg.norm(residual, axis=2)
    values = magnitude[valid]
    threshold = max(1.25, float(np.percentile(values, 95)))
    mask = ((magnitude >= threshold) & valid).astype(np.uint8) * 255
    kernel = np.ones((3, 3), dtype=np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_CLOSE, np.ones((7, 7), dtype=np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    found = []
    for component_id in range(1, count):
        x, y, width, height, area = (int(value)
                                     for value in stats[component_id])
        if area < _MIN_COMPONENT_AREA:
            continue
        if area > SAMPLE_WIDTH * SAMPLE_HEIGHT * _MAX_COMPONENT_FRACTION:
            continue
        box = _source_box((x, y, x + width, y + height), active_rect)
        component = labels[y:y + height, x:x + width] == component_id
        roi = magnitude[y:y + height, x:x + width][component]
        if not roi.size:
            continue
        p90 = float(np.percentile(roi, 90))
        mean = float(roi.mean())
        if mean < 1.0 or p90 < 1.75:
            continue
        found.append({
            "time": round(timestamp, 3),
            "box": box,
            "mean_motion_px": round(mean, 3),
            "p90_motion_px": round(p90, 3),
            "area_px": area,
            "score": mean * math.sqrt(area),
        })
    return sorted(found, key=lambda row: row["score"], reverse=True)[:12]


def _iou(first, second) -> float:
    x1, y1 = max(first[0], second[0]), max(first[1], second[1])
    x2, y2 = min(first[2], second[2]), min(first[3], second[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if intersection <= 0:
        return 0.0
    area_a = (first[2] - first[0]) * (first[3] - first[1])
    area_b = (second[2] - second[0]) * (second[3] - second[1])
    return intersection / max(1e-9, area_a + area_b - intersection)


def _link_detections(tracks: list[dict], detections: list[dict],
                     frame_index: int) -> None:
    available = set(range(len(tracks)))
    for detection in detections:
        candidates = []
        for index in available:
            previous = tracks[index]["observations"][-1]
            gap = frame_index - previous["frame_index"]
            if gap > 3:
                continue
            overlap = _iou(previous["box"], detection["box"])
            first_center = _box_center(previous["box"])
            second_center = _box_center(detection["box"])
            distance = math.hypot(first_center[0] - second_center[0],
                                  first_center[1] - second_center[1])
            if overlap >= 0.02 or distance <= 0.14:
                candidates.append((0 if overlap >= 0.02 else 1,
                                   -overlap, distance, index))
        if candidates:
            _, _, _, index = min(candidates)
            tracks[index]["observations"].append({
                key: detection[key]
                for key in ("time", "box", "mean_motion_px",
                            "p90_motion_px", "area_px")
            } | {"frame_index": frame_index})
            available.remove(index)
        else:
            tracks.append({"observations": [{
                key: detection[key]
                for key in ("time", "box", "mean_motion_px",
                            "p90_motion_px", "area_px")
            } | {"frame_index": frame_index}]})


def analyze_frames(frames: list, start: float, face_presence: dict,
                   aspect_ratio: float, action_span: dict) -> dict:
    """Analyze already-decoded 640x360 grayscale frames (also used by tests)."""
    import cv2
    import numpy as np

    active_rect = _active_rect(float(aspect_ratio))
    x, y, width, height = active_rect
    valid_mask = np.zeros((SAMPLE_HEIGHT, SAMPLE_WIDTH), dtype=bool)
    valid_mask[int(round(y)):int(round(y + height)),
               int(round(x)):int(round(x + width))] = True

    face_observations = []
    for index in range(len(frames)):
        time_s = float(start) + index / SAMPLE_RATE_HZ
        box = face_box_at(face_presence, time_s)
        if box is not None:
            face_observations.append({"time": round(time_s, 3), "box": box})

    tracks = []
    face_motion = []
    for index, (before, after) in enumerate(zip(frames, frames[1:])):
        flow = cv2.calcOpticalFlowFarneback(
            before, after, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        pair_time = float(start) + (index + 1) / SAMPLE_RATE_HZ
        face_before = face_box_at(face_presence,
                                  float(start) + index / SAMPLE_RATE_HZ)
        face_after = face_box_at(face_presence, pair_time)
        excluded_mask = np.zeros(valid_mask.shape, dtype=bool)
        face_rects = [box for box in (face_before, face_after) if box]
        for face_box in face_rects:
            bx1, by1, bx2, by2 = _canvas_box(face_box, active_rect)
            pad_x = max(4, int((bx2 - bx1) * 0.18))
            pad_y = max(4, int((by2 - by1) * 0.18))
            excluded_mask[max(0, by1 - pad_y):min(SAMPLE_HEIGHT, by2 + pad_y),
                          max(0, bx1 - pad_x):min(SAMPLE_WIDTH, bx2 + pad_x)] = True
        residual = flow.copy()
        usable = valid_mask & ~excluded_mask
        if int(usable.sum()) > 0:
            background = np.median(residual[usable], axis=0)
            residual[:, :, 0] -= float(background[0])
            residual[:, :, 1] -= float(background[1])
            magnitude = np.linalg.norm(residual, axis=2)
            for face_box in face_rects:
                bx1, by1, bx2, by2 = _canvas_box(face_box, active_rect)
                roi = magnitude[by1:by2, bx1:bx2]
                if roi.size:
                    face_motion.append(round(float(roi.mean()), 3))

        detections = _track_components(
            flow, valid_mask, excluded_mask, pair_time, active_rect, cv2, np)
        _link_detections(tracks, detections, index + 1)

    tracks = [track for track in tracks
              if len(track["observations"]) >= 2]
    tracks = sorted(
        tracks,
        key=lambda track: (
            max(row["p90_motion_px"] for row in track["observations"])
            * math.sqrt(len(track["observations"])),
            len(track["observations"]),
            -track["observations"][0]["time"],
        ),
        reverse=True,
    )[:_MAX_TRACKS_PER_SPAN]
    tracks.sort(key=lambda track: (
        track["observations"][0]["time"],
        _box_center(track["observations"][0]["box"])[0],
        _box_center(track["observations"][0]["box"])[1],
    ))

    normalized_tracks = []
    for index, track in enumerate(tracks, start=1):
        observations = [{key: row[key] for key in (
            "time", "box", "mean_motion_px", "p90_motion_px", "area_px")}
            for row in track["observations"]]
        normalized_tracks.append({
            "track_id": f"motion_region_{index:02d}",
            "kind": "hand_body_motion_candidate",
            "classification": "motion_only_unclassified",
            "envelope": _box_union([row["box"] for row in observations]),
            "source_start": observations[0]["time"],
            "source_end": observations[-1]["time"],
            "peak_p90_motion_px": round(max(
                row["p90_motion_px"] for row in observations), 3),
            "observations": observations,
        })

    crop, keep_clear = _framing_suggestions(
        face_observations, normalized_tracks, action_span)
    return {
        "selected_by": action_span["selected_by"],
        "action_labels": action_span["action_labels"],
        "source_start": round(float(action_span["start"]), 3),
        "source_end": round(float(action_span["end"]), 3),
        "measurement": {
            "method": "farneback_background_compensated",
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "resolution": [SAMPLE_WIDTH, SAMPLE_HEIGHT],
            "background_compensation": "median x/y flow per pair",
            "component_threshold": (
                "max(1.25 px, p95 residual flow magnitude per pair)"),
            "minimum_component_area_px": _MIN_COMPONENT_AREA,
            "maximum_tracks_per_span": _MAX_TRACKS_PER_SPAN,
            "frame_count": len(frames),
            "pair_count": max(0, len(frames) - 1),
        },
        "face_track": {
            "track_id": "largest_face",
            "source": "temporal_index.face_presence.face_boxes",
            "detection_method": "existing_largest_face_track",
            "observations": face_observations,
            "mean_flow_px": round(
                float(np.mean(face_motion)), 3) if face_motion else None,
        },
        "motion_tracks": normalized_tracks,
        "crop_suggestion": crop,
        "keep_clear_suggestions": keep_clear,
    }


def analyze_candidate_span(video_path: str, action_span: dict,
                          face_presence: dict,
                          aspect_ratio: float | None = None) -> dict:
    """Decode and analyze one selected span; never decodes outside it."""
    start, end = float(action_span["start"]), float(action_span["end"])
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        raise ValueError("regional motion span must have finite start < end")
    frames = _decode_frames(video_path, start, end)
    return analyze_frames(frames, start, face_presence,
                          aspect_ratio if aspect_ratio is not None
                          else _display_aspect_ratio(video_path), action_span)


def compact_span(span: dict) -> dict:
    """Drop per-sample trajectories for a prompt-facing decision view."""
    face = span["face_track"]
    motion = span["motion_tracks"]
    return {
        "selected_by": span["selected_by"],
        "action_labels": span["action_labels"],
        "source_range": [span["source_start"], span["source_end"]],
        "motion_sample_rate_hz": span["measurement"]["sample_rate_hz"],
        "resolution": span["measurement"]["resolution"],
        "face": {
            "box_envelope": _box_union([
                row["box"] for row in face["observations"]]),
            "observed_samples": len(face["observations"]),
            "mean_flow_px": face["mean_flow_px"],
        },
        "motion_regions": [{
            "track_id": track["track_id"],
            "kind": track["kind"],
            "classification": track["classification"],
            "source_range": [track["source_start"], track["source_end"]],
            "envelope": track["envelope"],
            "peak_p90_motion_px": track["peak_p90_motion_px"],
            "observed_samples": len(track["observations"]),
        } for track in motion],
        "crop_suggestion": span["crop_suggestion"],
        "keep_clear_suggestions": span["keep_clear_suggestions"],
    }


def build_analysis(video_path: str, duration: float,
                   semantic_document: dict | None,
                   face_presence: dict,
                   scene_boundaries: list[dict] | None = None,
                   cached: dict | None = None) -> dict:
    """Measure action-selected ranges, reusing an exact versioned result."""
    selected = split_at_scene_boundaries(
        action_candidate_spans(semantic_document, duration), scene_boundaries)
    cached = cached if isinstance(cached, dict) else {}
    if (cached.get("schema_version") == SCHEMA_VERSION
            and cached.get("selected_spans") == selected
            and isinstance(cached.get("spans"), list)):
        return cached

    aspect_ratio = _display_aspect_ratio(video_path) if selected else None
    spans = [analyze_candidate_span(
        video_path, span, face_presence, aspect_ratio=aspect_ratio)
        for span in selected]
    return {
        "schema_version": SCHEMA_VERSION,
        "sample_rate_hz": SAMPLE_RATE_HZ,
        "resolution": [SAMPLE_WIDTH, SAMPLE_HEIGHT],
        "selection_method": (
            "time-bounded Gemma actions[] labels with a movement, gesture, "
            "or facial-change cue"),
        "selected_spans": selected,
        "spans": spans,
        "measurement_status": "measured" if spans else "no_candidates",
        "reason": (None if spans else
                   "No time-bounded Gemma action labels selected a span; "
                   "regional motion was not run over the footage."),
    }


def compact_analysis(analysis: dict) -> list[dict]:
    return [compact_span(span) for span in analysis.get("spans", [])]
