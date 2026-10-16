"""Lane 3: relation classes with different evidence bars.

Temporal edges are computable from clock-aligned intervals;
physical edges need spatial/track evidence and stay candidates
until verified; semantic edges are never derived here - a model
interpretation owns them (report lane 3, Relation row). Every edge
cites the observations it rests on; a span with no backing
observation is skipped, never bridged.
"""
from __future__ import annotations

import os
from collections import defaultdict
from math import hypot
from typing import Any

from library.tools.identity_reassociation import interval_iou, source_clock_ids

RELATION_CLASSES = ("temporal", "physical", "semantic")

TEMPORAL_PREDICATES = ("co_present",)

PHYSICAL_PREDICATES = ("touching",)


def _normalised_box(box: list[float],
                    dims: list[int]) -> list[float] | None:
    if len(box) != 4 or len(dims) != 2 or min(dims) <= 0:
        return None
    width, height = dims
    return [box[0] / width, box[1] / height,
            box[2] / width, box[3] / height]


def _near(point: tuple[float, float], box: list[float],
          radius: float) -> bool:
    x1, y1, x2, y2 = box
    dx = max(x1 - point[0], 0.0, point[0] - x2)
    dy = max(y1 - point[1], 0.0, point[1] - y2)
    return hypot(dx, dy) <= radius


def _dims_of(coordinate_space: str) -> list[int] | None:
    try:
        tail = str(coordinate_space).rsplit("_", 1)[1]
        width, height = tail.split("x")
        dims = [int(width), int(height)]
    except (ValueError, IndexError):
        return None
    return dims if min(dims) > 0 else None


def _object_boxes(track: dict[str, Any]) -> list[tuple[int, list[float]]]:
    """(tick, normalised box) samples of one object track."""
    rows = []
    for sample in track.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        tick = sample.get("tick")
        geometry = sample.get("geometry") or {}
        if not isinstance(tick, int):
            continue
        box = geometry.get("bbox_pixels")
        if box is not None:
            dims = _dims_of(str(sample.get("coordinate_space") or ""))
            normalised = (_normalised_box(list(box), dims)
                          if dims else None)
            if normalised is not None:
                rows.append((tick, normalised))
            continue
        box = geometry.get("bbox")
        if (isinstance(box, list) and len(box) == 4
                and all(isinstance(v, (int, float)) for v in box)):
            rows.append((tick, [float(v) for v in box]))
    return sorted(rows)


def _observation_at(observations: list[dict[str, Any]], tick: int,
                    clock_id: str) -> dict[str, Any] | None:
    for row in observations:
        interval = row.get("interval") or {}
        if (interval.get("clock_id") == clock_id
                and interval.get("start") <= tick < interval.get("end")):
            return row
    return None


def derive_temporal_relations(model: dict[str, Any], *,
                             min_shared_ticks: int = 1) -> list[dict[str, Any]]:
    """Co-presence edges for entities sharing sample ticks on one clock.

    Each edge cites the two observations covering a shared tick, so a
    tick with no backing observation contributes no evidence and no
    edge. Certainty is measured (shared samples are measured) and
    status stays candidate: sparse samples cannot establish continuous
    co-presence.
    """
    if min_shared_ticks < 1:
        raise ValueError("min_shared_ticks must be positive")
    from library.tools.identity_reassociation import observation_index
    backing = observation_index(model)
    track_observations: dict[str, list[dict[str, Any]]] = {}
    for track in model.get("tracks") or []:
        if isinstance(track, dict) and track.get("id"):
            names = {track.get("id"), track.get("local_track_id")} - {None}
            rows = [row for name in names for row in backing.get(name, [])]
            rows.sort(key=lambda row: row["interval"]["start"])
            track_observations[track["id"]] = rows
    relations = []
    tracks = [t for t in model.get("tracks") or [] if isinstance(t, dict)]
    for left_index, left in enumerate(tracks):
        for right in tracks[left_index + 1:]:
            if left.get("clock_id") != right.get("clock_id"):
                continue
            clock_id = left["clock_id"]
            left_ticks = {s["tick"] for s in left.get("samples") or []
                          if isinstance(s, dict)
                          and isinstance(s.get("tick"), int)}
            right_ticks = {s["tick"] for s in right.get("samples") or []
                           if isinstance(s, dict)
                           and isinstance(s.get("tick"), int)}
            shared = sorted(left_ticks & right_ticks)
            if len(shared) < min_shared_ticks:
                continue
            evidence: list[str] = []
            for tick in shared:
                left_obs = _observation_at(
                    track_observations.get(left["id"], []), tick, clock_id)
                right_obs = _observation_at(
                    track_observations.get(right["id"], []), tick, clock_id)
                if left_obs is None or right_obs is None:
                    continue
                evidence.extend([left_obs["id"], right_obs["id"]])
            if not evidence:
                continue
            left_entity = str(left.get("entity_id"))
            right_entity = str(right.get("entity_id"))
            if left_entity == right_entity:
                continue
            start, end = shared[0], shared[-1] + 1
            relations.append({
                "id": (f"relation-co_present-{left['id']}-"
                       f"{right['id']}-{start}-{end}"),
                "subject_id": left_entity,
                "predicate": "co_present",
                "object_id": right_entity,
                "interval": {"clock_id": clock_id,
                             "start": start, "end": end},
                "confidence": None,
                "provenance_id": left.get("provenance_id"),
                "evidence_ids": sorted(set(evidence)),
                "status": "candidate",
                "certainty": "measured",
                "relation_class": "temporal",
            })
    relations.sort(key=lambda row: row["id"])
    return relations


def _face_person(model: dict[str, Any],
                 face_row: dict[str, Any]) -> str | None:
    owners = {link.get("entity_id")
              for link in model.get("identity_links") or []
              if isinstance(link, dict)
              and link.get("observation_id") == face_row.get("id")
              and link.get("status") == "verified"
              and isinstance(link.get("entity_id"), str)}
    if len(owners) != 1:
        return None
    return next(iter(owners))


def derive_physical_relations(
        model: dict[str, Any], *,
        hand_radius: float = 0.12,
        min_samples: int = 2,
        max_gap_ticks: int = 2000,
        max_face_drift_ticks: int = 500) -> list[dict[str, Any]]:
    """Hand-object touching edges from measured landmarks and boxes.

    A contact hit is a confident hand joint inside ``hand_radius`` of
    an object box on the same clock within ``max_face_drift_ticks``.
    Consecutive hits group into one edge; a group shorter than
    ``min_samples`` is dropped. The hand's owner is the person the
    face observation links to - an unlinked or ambiguously linked
    face row contributes no subject, so no edge is invented for it.
    Edges stay candidates with inferred certainty: proximity is
    measured, contact is inferred.
    """
    if hand_radius <= 0 or min_samples < 1 or max_gap_ticks <= 0:
        raise ValueError("physical relation thresholds must be positive")
    from library.tools.identity_reassociation import observation_index
    backing_index = observation_index(model)
    entity_kind = {row["id"]: row.get("kind")
                   for row in model.get("entities") or []
                   if isinstance(row, dict)}
    face_rows = [row for row in model.get("observations") or []
                 if isinstance(row, dict)
                 and row.get("modality") == "face_frame"
                 and isinstance(row.get("payload"), dict)]
    by_clock: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in face_rows:
        by_clock[row["interval"]["clock_id"]].append(row)
    for rows in by_clock.values():
        rows.sort(key=lambda row: row["interval"]["start"])
    relations = []
    for track in model.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if entity_kind.get(track.get("entity_id")) != "object":
            continue
        clock_id = track.get("clock_id")
        boxes = _object_boxes(track)
        if not boxes:
            continue
        names = {track.get("id"), track.get("local_track_id")} - {None}
        backing = [row for name in names
                   for row in backing_index.get(name, [])]
        backing.sort(key=lambda row: row["interval"]["start"])
        hits: list[dict[str, Any]] = []
        for tick, box in boxes:
            obj_obs = _observation_at(backing, tick, clock_id)
            if obj_obs is None:
                continue
            for face_row in by_clock.get(clock_id, []):
                if (abs(face_row["interval"]["start"] - tick)
                        > max_face_drift_ticks):
                    continue
                person_id = _face_person(model, face_row)
                if person_id is None:
                    continue
                touched = any(
                    confidence >= 0.35
                    and _near((float(x), float(y)), box, hand_radius)
                    for hand in face_row["payload"].get("hands") or []
                    for x, y, confidence
                    in (hand.get("joints") or {}).values())
                if touched:
                    hits.append({"tick": tick, "person_id": person_id,
                                 "face_id": face_row["id"],
                                 "object_obs_id": obj_obs["id"]})
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for hit in sorted(hits, key=lambda row: (row["person_id"],
                                                 row["tick"])):
            person = hit["person_id"]
            if (grouped[person]
                    and hit["tick"] - grouped[person][-1]["tick"]
                    > max_gap_ticks):
                group = grouped.pop(person)
                relations.extend(_touching_edge(track, person, group,
                                               min_samples))
                grouped[person] = [hit]
            else:
                grouped[person].append(hit)
        for person, group in grouped.items():
            relations.extend(_touching_edge(track, person, group,
                                            min_samples))
    relations.sort(key=lambda row: row["id"])
    return relations


def _touching_edge(track: dict[str, Any], person_id: str,
                   group: list[dict[str, Any]],
                   min_samples: int) -> list[dict[str, Any]]:
    if len(group) < min_samples:
        return []
    start = group[0]["tick"]
    end = group[-1]["tick"] + 1
    evidence = sorted({ref for hit in group
                       for ref in (hit["face_id"],
                                   hit["object_obs_id"])})
    return [{
        "id": (f"relation-touching-{person_id}-"
               f"{track.get('entity_id')}-{start}-{end}"),
        "subject_id": person_id,
        "predicate": "touching",
        "object_id": str(track.get("entity_id")),
        "interval": {"clock_id": track.get("clock_id"),
                     "start": start, "end": end},
        "confidence": None,
        "provenance_id": track.get("provenance_id"),
        "evidence_ids": evidence,
        "status": "candidate",
        "certainty": "inferred",
        "relation_class": "physical",
    }]


def score_relations(relations: list[dict[str, Any]],
                    labels: dict[str, Any],
                    entity_labels: dict[str, str], *,
                    iou_threshold: float = 0.5) -> dict[str, Any]:
    """Precision/recall of touching edges against labeled contacts.

    ``entity_labels`` maps model entity ids to blind-label persistent
    ids; the map is built at scoring time by inspecting overlaps, so
    the labellers never see model internals. A labeled contact is
    recalled when a touching edge on the same clock spans it at
    ``iou_threshold``.
    """
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0, 1]")
    clock_by_source = source_clock_ids(labels.get("model") or {})
    labeled = list(labels.get("contacts") or [])
    for window in labels.get("windows") or []:
        source = os.path.basename(str(window["source_file"]))
        for contact in window.get("contacts") or []:
            start = (round(float(contact["start_seconds"]) * 1000)
                     if "start_seconds" in contact
                     else int(contact["start_tick"]))
            end = (round(float(contact["end_seconds"]) * 1000)
                   if "end_seconds" in contact
                   else int(contact["end_tick"]))
            labeled.append({
                "person": contact.get("person_id", contact.get("person")),
                "object": contact.get("object_id", contact.get("object")),
                "clock_id": contact.get("clock_id")
                            or clock_by_source.get(source),
                "start_tick": start, "end_tick": end,
            })
    predicted = [rel for rel in relations
                 if isinstance(rel, dict)
                 and rel.get("relation_class") == "physical"
                 and rel.get("predicate") == "touching"]
    matched_labels: set[int] = set()
    matched_predicted: set[int] = set()
    for label_index, contact in enumerate(labeled):
        person = contact.get("person")
        obj = contact.get("object")
        clock_id = contact.get("clock_id")
        span = (int(contact["start_tick"]), int(contact["end_tick"]))
        for pred_index, rel in enumerate(predicted):
            if pred_index in matched_predicted:
                continue
            if (entity_labels.get(rel.get("subject_id")) != person
                    or entity_labels.get(rel.get("object_id")) != obj):
                continue
            interval = rel.get("interval") or {}
            if interval.get("clock_id") != clock_id:
                continue
            if interval_iou((interval["start"], interval["end"]),
                            span) >= iou_threshold:
                matched_labels.add(label_index)
                matched_predicted.add(pred_index)
                break
    precision = (len(matched_predicted) / len(predicted)
                 if predicted else None)
    recall = len(matched_labels) / len(labeled) if labeled else None
    return {"true_positives": len(matched_labels),
            "predicted": len(predicted), "labeled": len(labeled),
            "precision": precision, "recall": recall}


def score_pickups(events: list[dict[str, Any]],
                  labels: dict[str, Any],
                  entity_labels: dict[str, str], *,
                  iou_threshold: float = 0.5) -> dict[str, Any]:
    """Recall of pick-up events against labeled pick-up intervals."""
    if not 0 < iou_threshold <= 1:
        raise ValueError("iou_threshold must be in (0, 1]")
    clock_by_source = source_clock_ids(labels.get("model") or {})
    labeled = list(labels.get("pickups") or [])
    for window in labels.get("windows") or []:
        source = os.path.basename(str(window["source_file"]))
        for pickup in window.get("pickups") or []:
            start = (round(float(pickup["start_seconds"]) * 1000)
                     if "start_seconds" in pickup
                     else int(pickup["start_tick"]))
            end = (round(float(pickup["end_seconds"]) * 1000)
                   if "end_seconds" in pickup
                   else int(pickup["end_tick"]))
            labeled.append({
                "object": pickup.get("object_id", pickup.get("object")),
                "clock_id": pickup.get("clock_id") or clock_by_source.get(source),
                "start_tick": start, "end_tick": end,
            })
    predicted = [event for event in events
                 if isinstance(event, dict)
                 and event.get("kind") == "pick_up"]
    matched_labels: set[int] = set()
    matched_predicted: set[int] = set()
    for label_index, pickup in enumerate(labeled):
        obj = pickup.get("object")
        clock_id = pickup.get("clock_id")
        span = (int(pickup["start_tick"]), int(pickup["end_tick"]))
        for pred_index, event in enumerate(predicted):
            if pred_index in matched_predicted:
                continue
            participants = [entity_labels.get(pid, pid)
                            for pid in event.get("participant_ids") or []]
            if obj not in participants:
                continue
            interval = event.get("interval") or {}
            if interval.get("clock_id") != clock_id:
                continue
            if interval_iou((interval["start"], interval["end"]),
                            span) >= iou_threshold:
                matched_labels.add(label_index)
                matched_predicted.add(pred_index)
                break
    recall = len(matched_labels) / len(labeled) if labeled else None
    precision = (len(matched_predicted) / len(predicted)
                 if predicted else None)
    return {"true_positives": len(matched_labels),
            "predicted": len(predicted), "labeled": len(labeled),
            "precision": precision, "recall": recall}
