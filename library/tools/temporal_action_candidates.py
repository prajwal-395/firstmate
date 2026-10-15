"""Deterministic, measurement preserving candidates for hand/object actions.

The rules consume Project Model M3 hand/face samples and tracked object
boxes. They emit candidate events only; they never turn missing samples
into a negative answer or replace the measurements with a VLM judgement.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from itertools import pairwise
from math import hypot
from typing import Any

RULES = {
    "drink_from_cup": {"object_terms": ("cup", "mug"), "needs_mouth": True},
    "pick_up_put_down": {"object_terms": (), "needs_mouth": False},
    "hand_object_contact": {"object_terms": (), "needs_mouth": False},
    "hand_to_face": {"object_terms": (), "needs_mouth": False},
    "pointing": {"object_terms": (), "needs_mouth": False},
}


def _pointing_candidates(face_rows: list[dict[str, Any]], *,
                         min_extension: float = .12,
                         max_gap_ticks: int = 1000) -> list[dict[str, Any]]:
    """Nominate sustained pointing gestures from confident hand landmarks.

    Coordinates are normalized by the upstream M3 contract. This only
    proposes spans: an extended index finger may point at anything, so the
    candidate carries no target identity or verified action claim.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in face_rows:
        grouped[row["interval"]["clock_id"]].append(row)
    proposals = []
    for clock_id, rows in grouped.items():
        rows.sort(key=lambda row: row["interval"]["start"])
        hits = []
        for row in rows:
            for hand in row["payload"].get("hands") or []:
                joints = hand.get("joints") or {}
                wrist = joints.get("wrist")
                tip = next((joint for name, joint in joints.items()
                            if name.lower() in ("index_tip", "indextip")), None)
                if not wrist or not tip or wrist[2] < .35 or tip[2] < .35:
                    continue
                if hypot(float(tip[0]) - float(wrist[0]),
                         float(tip[1]) - float(wrist[1])) < min_extension:
                    continue
                hits.append(row)
                break
        groups = []
        for row in hits:
            tick = row["interval"]["start"]
            if groups and tick - groups[-1][-1]["interval"]["start"] <= max_gap_ticks:
                groups[-1].append(row)
            else:
                groups.append([row])
        for group in groups:
            if len(group) < 2:
                continue
            start, end = group[0]["interval"]["start"], group[-1]["interval"]["end"]
            proposals.append({
                "id": f"event-candidate-pointing-{clock_id}-{start}-{end}",
                "interval": {"clock_id": clock_id, "start": start, "end": end},
                "kind": "pointing", "participant_ids": [],
                "status": "candidate", "certainty": "inferred",
                "evidence_ids": sorted({row["id"] for row in group}),
                "confidence": None, "state_changes": [],
                "phases": {"preparation": None,
                           "execution": {"start": start, "end": end},
                           "completion": None},
            })
    return sorted(proposals, key=lambda row: (row["interval"]["start"], row["id"]))


def nominate_hand_motion_intervals(frames: list[dict[str, Any]], *,
                                  motion_threshold: float = .08,
                                  max_gap_seconds: float = 2.0,
                                  padding_seconds: float = .5) -> list[dict[str, Any]]:
    """Cheap L0/L1 nomination from sampled M3 hand landmarks.

    The output is a search region, never a positive or negative action
    answer. Gaps and absent frames remain uncovered.
    """
    if motion_threshold <= 0 or max_gap_seconds <= 0 or padding_seconds < 0:
        raise ValueError("motion thresholds must be positive and padding non-negative")
    samples = []
    for frame in frames:
        t = float(frame["t"])
        points = [(float(x), float(y))
                  for hand in frame.get("hands") or []
                  for x, y, confidence in (hand.get("joints") or {}).values()
                  if float(confidence) >= .35]
        if points:
            samples.append((t, points))
    intervals = []
    for (left_t, left_points), (right_t, right_points) in pairwise(samples):
        if right_t - left_t > max_gap_seconds:
            continue
        movement = max(hypot(x1 - x2, y1 - y2)
                       for x1, y1 in left_points for x2, y2 in right_points)
        if movement < motion_threshold:
            continue
        start = max(0.0, left_t - padding_seconds)
        end = right_t + padding_seconds
        if intervals and start <= intervals[-1]["end_s"]:
            intervals[-1]["end_s"] = max(intervals[-1]["end_s"], end)
            intervals[-1]["source_times"].append(right_t)
        else:
            intervals.append({"start_s": start, "end_s": end,
                              "source_times": [left_t, right_t]})
    return intervals


def detect_named_object_in_intervals(intervals: list[dict[str, Any]], *,
                                     frames_by_interval: list[list[str]],
                                     object_name: str,
                                     detector: str = "owlv2",
                                     detect_image=None) -> list[dict[str, Any]]:
    """Run the named-object detector only on already nominated frames.

    ``detect_image`` is injectable for tests. OWLv2 detections nominate
    boxes for tracking; detector misses never veto later evidence.
    """
    if len(intervals) != len(frames_by_interval):
        raise ValueError("each nominated interval needs its sampled frame paths")
    if not object_name.strip():
        raise ValueError("object_name must be a named object class")
    if detect_image is None:
        from library.tools.analysis.open_vocabulary_detect import detect_image
    detections = []
    for interval, paths in zip(intervals, frames_by_interval):
        for path in paths:
            for box in detect_image(detector, path, [object_name]):
                detections.append({"interval": interval, "frame": path,
                                   "object_name": object_name,
                                   "box": list(box.box), "score": box.score,
                                   "detector": box.detector})
    return detections


def _box_center(box: list[float]) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _normalized_box(box: list[float], resolution: list[int]) -> list[float] | None:
    if len(box) != 4 or len(resolution) != 2 or min(resolution) <= 0:
        return None
    width, height = resolution
    return [box[0] / width, box[1] / height,
            box[2] / width, box[3] / height]


def _near(point: tuple[float, float], box: list[float], radius: float = .12) -> bool:
    x1, y1, x2, y2 = box
    dx = max(x1 - point[0], 0.0, point[0] - x2)
    dy = max(y1 - point[1], 0.0, point[1] - y2)
    return hypot(dx, dy) <= radius


def _hand_to_face_candidates(face_rows: list[dict[str, Any]], *,
                             max_gap_ticks: int = 1000,
                             max_span_ticks: int = 4000) -> list[dict[str, Any]]:
    """Nominate hand-to-face spans from measured face and hand landmarks."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in face_rows:
        grouped[row["interval"]["clock_id"]].append(row)
    proposals = []
    for clock_id, rows in grouped.items():
        rows.sort(key=lambda row: row["interval"]["start"])
        contacts = []
        for row in rows:
            face = row["payload"].get("face") or {}
            box = face.get("box")
            lips = face.get("outer_lips") or []
            near = False
            for hand in row["payload"].get("hands") or []:
                for x, y, confidence in (hand.get("joints") or {}).values():
                    if confidence < .35:
                        continue
                    point = (float(x), float(y))
                    if (any(hypot(point[0] - lx, point[1] - ly) <= .09
                            for lx, ly in lips)
                            or box and _near(point, box, .05)):
                        near = True
            if near:
                contacts.append(row)
        groups = []
        for row in contacts:
            tick = row["interval"]["start"]
            if (groups
                    and tick - groups[-1][-1]["interval"]["start"] <= max_gap_ticks
                    and tick - groups[-1][0]["interval"]["start"] <= max_span_ticks):
                groups[-1].append(row)
            else:
                groups.append([row])
        for group in groups:
            if len(group) < 2:
                continue
            start = group[0]["interval"]["start"]
            end = group[-1]["interval"]["end"]
            proposals.append({
                "id": f"event-candidate-hand_to_face-{clock_id}-{start}-{end}",
                "interval": {"clock_id": clock_id, "start": start, "end": end},
                "kind": "hand_to_face", "participant_ids": [],
                "status": "candidate", "certainty": "inferred",
                "evidence_ids": sorted({row["id"] for row in group}),
                "confidence": None, "state_changes": [],
            })
    return sorted(proposals,
                  key=lambda row: (row["interval"]["start"], row["id"]))


def candidates_from_model(model: dict[str, Any], *, rule: str = "drink_from_cup",
                          top_k: int | None = None) -> list[dict[str, Any]]:
    """Return typed Project Model Event records supported by measured rows.

    Object and pose evidence remain observations. Candidate ``evidence_ids``
    point back to those measurements, and uncertainty is represented by a
    measured confidence score only when the inputs contain enough temporal
    samples to satisfy the rule.
    """
    if rule not in RULES:
        raise ValueError(f"unknown temporal action rule: {rule}")
    observations = model["observations"]
    entity_ids = {row["id"] for row in model["entities"]}
    tracks_by_entity = {row["id"]: row for row in model["tracks"]}
    object_by_id = {row["id"]: row for row in model["entities"]
                    if row.get("kind") == "object"}
    object_terms = RULES[rule]["object_terms"]
    object_tracks = {}
    for track_id, track in tracks_by_entity.items():
        entity = object_by_id.get(track.get("entity_id"))
        if entity is None:
            continue
        label = (str(entity.get("label") or "") + " " +
                 str(entity.get("object_class_id") or "")).lower()
        if object_terms and not any(term in label for term in object_terms):
            continue
        samples = sorted(track["samples"], key=lambda row: row["tick"])
        if len(samples) >= 2:
            object_tracks[track_id] = (track, entity, samples)

    face_rows = [row for row in observations if row["modality"] == "face_frame"]
    if rule == "hand_to_face":
        proposals = _hand_to_face_candidates(face_rows)
        return proposals[:top_k] if top_k is not None else proposals
    if rule == "pointing":
        proposals = _pointing_candidates(face_rows)
        return proposals[:top_k] if top_k is not None else proposals
    object_rows: dict[tuple[str, int], dict] = {}
    for row in observations:
        if row["modality"] == "object_track_sample":
            key = (row["payload"]["track_id"], row["interval"]["start"])
            object_rows[key] = row
    by_clock_time: dict[str, list[dict]] = defaultdict(list)
    for row in face_rows:
        by_clock_time[row["interval"]["clock_id"]].append(row)
    for rows in by_clock_time.values():
        rows.sort(key=lambda row: row["interval"]["start"])
    face_times_by_clock = {
        clock: [row["interval"]["start"] for row in rows]
        for clock, rows in by_clock_time.items()
    }

    proposals = []
    for track_id, (track, entity, samples) in object_tracks.items():
        clock_id = track["clock_id"]
        samples_with_boxes = []
        for sample in samples:
            geometry = sample.get("geometry") or {}
            box = geometry.get("bbox_pixels")
            if box is None:
                continue
            resolution = [0, 0]
            # Object observations carry their measured source dimensions.
            object_row = object_rows.get((track_id, sample["tick"]))
            dims = str((object_row or {}).get("payload", {}).get(
                "coordinate_space", ""))
            try:
                resolution = [int(dims.rsplit("_", 1)[1].split("x")[0]),
                              int(dims.rsplit("x", 1)[1])]
            except (ValueError, IndexError):
                pass
            normalized = _normalized_box(box, resolution)
            if normalized:
                samples_with_boxes.append((sample, normalized))
        if len(samples_with_boxes) < 2:
            continue
        hits = []
        for sample, box in samples_with_boxes:
            tick = sample["tick"]
            object_row = object_rows.get((track_id, tick))
            if object_row is None:
                continue
            refs = [object_row["id"]]
            hand_near, mouth_near = False, False
            face_clock_rows = by_clock_time.get(clock_id, [])
            center = bisect_left(face_times_by_clock.get(clock_id, []), tick)
            nearby_faces = [row for row in face_clock_rows[
                max(0, center - 2):center + 2]
                if abs(row["interval"]["start"] - tick) <= 500]
            for face_row in nearby_faces:
                payload = face_row["payload"]
                face = payload.get("face") or {}
                refs.append(face_row["id"])
                for hand in payload.get("hands") or []:
                    for x, y, confidence in (hand.get("joints") or {}).values():
                        if confidence >= .35 and _near((x, y), box):
                            hand_near = True
                            break
                lips = face.get("outer_lips") or []
                if lips and any(_near((x, y), box, .1) for x, y in lips):
                    mouth_near = True
            hits.append({"sample": sample, "box": box, "refs": refs,
                         "hand_near": hand_near, "mouth_near": mouth_near})
        if rule == "hand_object_contact":
            groups = []
            current = []
            for hit in hits:
                tick = hit["sample"]["tick"]
                if hit["hand_near"]:
                    if (current and tick - current[-1]["sample"]["tick"]
                            > 2000):
                        groups.append(current)
                        current = []
                    current.append(hit)
                elif current:
                    groups.append(current)
                    current = []
            if current:
                groups.append(current)
            for group in groups:
                if len(group) < 2:
                    continue
                left, right = group[0]["sample"]["tick"], group[-1]["sample"]["tick"] + 1
                proposals.append({
                    "id": f"event-candidate-hand_object_contact-{track_id}-{left}-{right}",
                    "interval": {"clock_id": clock_id, "start": left, "end": right},
                    "kind": rule, "participant_ids": [entity["id"]],
                    "status": "candidate", "certainty": "inferred",
                    "evidence_ids": sorted({ref for hit in group for ref in hit["refs"]}),
                    "confidence": None, "state_changes": [],
                    "phases": {"preparation": None,
                               "execution": {"start": left, "end": right},
                               "completion": None},
                })
            continue
        # Require an ordered object-state transition. A single moved box near
        # a hand or face is not enough to claim an action.
        for before, middle, after in zip(hits, hits[1:], hits[2:]):
            times = [row["sample"]["tick"] for row in (before, middle, after)]
            if times[2] - times[0] > 4000 or any(
                    right - left > 2000
                    for left, right in pairwise(times)):
                continue
            _, by = _box_center(before["box"])
            _, my = _box_center(middle["box"])
            _, ay = _box_center(after["box"])
            raised = by - my >= .025
            lowered = ay - my >= .025
            hand_contact = any(row["hand_near"] for row in
                               (before, middle, after))
            if not hand_contact or not raised or not lowered:
                continue
            if (rule == "drink_from_cup"
                    and not any(row["mouth_near"] for row in (middle, after))):
                continue
            refs = sorted({reference for row in (before, middle, after)
                           for reference in row["refs"]})
            if not refs:
                continue
            left = times[0]
            right = times[2] + 1
            proposals.append({
                "id": f"event-candidate-{rule}-{track_id}-{left}-{right}",
                "interval": {"clock_id": clock_id, "start": left, "end": right},
                "kind": rule,
                "participant_ids": [entity["id"]] if entity["id"] in entity_ids else [],
                "status": "candidate", "certainty": "inferred",
                "evidence_ids": refs, "confidence": None,
                "state_changes": [],
            })
    proposals.sort(key=lambda row: (row["interval"]["start"], row["id"]))
    return proposals[:top_k] if top_k is not None else proposals


def query_status(*, analysed_intervals: list[dict[str, int]],
                 start_tick: int, end_tick: int) -> str:
    """Coverage-aware query state: missing temporal coverage never means absent."""
    if start_tick >= end_tick:
        raise ValueError("query interval must be non-empty")
    covered = sorted((row["start"], row["end"]) for row in analysed_intervals)
    cursor = start_tick
    for left, right in covered:
        if right <= cursor:
            continue
        if left > cursor:
            return "not-yet-analysed"
        cursor = max(cursor, right)
        if cursor >= end_tick:
            return "analysed"
    return "not-yet-analysed"
