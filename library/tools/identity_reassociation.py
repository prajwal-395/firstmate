"""Lane 3: persistent identity over clip-local tracks.

A tracker names instances per clip (``local_track_id``); a durable
entity (``identity_scope`` asset < capture_group < project <
cross_project) is what a query cites across time. This module keeps
the two apart:

- :func:`reassociate_tracks` proposes candidate re-identification
  links across occlusion gaps. Candidates only: a gap is unmeasured
  coverage, never a continuity verdict, so nothing here verifies.
- :func:`promote_identity_scope` widens an entity's scope only on
  verified, evidence-backed links.
- :func:`score_identity_switches` counts fragmentation against
  blind agent labels (the lane 3 measurement).

No second model: links and scopes ride the existing Project Model
records (report lane 3, Entity/ObjectTrack/IdentityLink row).
"""
from __future__ import annotations

import os
from typing import Any

IDENTITY_SCOPES = ("asset", "capture_group", "project", "cross_project")

SCOPE_RANK = {scope: rank for rank, scope in enumerate(IDENTITY_SCOPES)}

REIDENTIFICATION_METHOD = "reidentification"


def interval_iou(left: tuple[int, int], right: tuple[int, int]) -> float:
    """Temporal IoU of two half-open tick spans."""
    start = max(left[0], right[0])
    end = min(left[1], right[1])
    if end <= start:
        return 0.0
    union = max(left[1], right[1]) - min(left[0], right[0])
    if union <= 0:
        return 0.0
    return (end - start) / union


def track_span(track: dict[str, Any]) -> tuple[str, int, int] | None:
    """(clock_id, first_tick, last_tick_exclusive) of a track's samples."""
    ticks = [sample["tick"] for sample in track.get("samples") or []
             if isinstance(sample, dict)
             and isinstance(sample.get("tick"), int)]
    if not ticks:
        return None
    clock_id = track.get("clock_id")
    if not isinstance(clock_id, str) or not clock_id:
        return None
    return (clock_id, min(ticks), max(ticks) + 1)


def observation_index(
        model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Backing observations keyed by stamped track name, tick-ordered."""
    index: dict[str, list[dict[str, Any]]] = {}
    for row in model.get("observations") or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        name = payload.get("track_id")
        if isinstance(name, str) and name:
            index.setdefault(name, []).append(row)
    for rows in index.values():
        rows.sort(key=lambda row: row["interval"]["start"])
    return index


def sample_observations(model: dict[str, Any],
                        track: dict[str, Any]) -> list[dict[str, Any]]:
    """Observations backing one track's samples, in tick order.

    Matches the payload ``track_id`` the writers stamp against both
    the model track id and the tracker's local id, because the 1.06
    migration stamps the model id while M3d stamps the local one.
    """
    names = {track.get("id"), track.get("local_track_id")} - {None}
    index = observation_index(model)
    rows = [row for name in names for row in index.get(name, [])]
    rows.sort(key=lambda row: row["interval"]["start"])
    return rows


def _entity_class(model: dict[str, Any], entity_id: str) -> tuple[str, str | None]:
    entity = next((row for row in model.get("entities") or []
                   if isinstance(row, dict) and row.get("id") == entity_id),
                  None)
    if entity is None:
        return ("other", None)
    return (str(entity.get("kind") or "other"),
            entity.get("object_class_id"))


def reassociate_tracks(model: dict[str, Any], *,
                       max_gap_ticks: int = 2000,
                       max_center_distance: float = 0.20,
                       verify: bool = False,
                       supporting_observation_ids: list[str] | None = None,
                       provenance_id: str | None = None) -> dict[str, Any]:
    """Propose re-identification links across occlusion gaps.

    Two tracks of the same entity kind and object class on one clock,
    with disjoint spans separated by a positive gap of at most
    ``max_gap_ticks``, are considered only when their endpoint boxes
    remain within ``max_center_distance``. Both endpoint observations
    support a re-identification candidate; proximity is not treated as
    verification. Cross-clock and cross-class pairs are never linked
    here - without a measured transform (lane 2) or a shared class
    there is no basis.

    **A candidate may carry no support at all.** Passing an explicit
    empty ``supporting_observation_ids`` says "this is a proposal with
    no evidence behind it yet", and the link records exactly that. The
    default (no argument) keeps the endpoint observations as the
    candidate's support, which is what a proximity-based proposal has.
    Only a VERIFIED link must carry support, and the Project Model
    validator refuses a verified re-identification without it.
    """
    if max_gap_ticks <= 0 or max_center_distance <= 0:
        raise ValueError("re-identification thresholds must be positive")
    support = list(supporting_observation_ids or [])
    no_support_yet = supporting_observation_ids is not None and not support
    if verify and not support:
        raise ValueError("verified re-identification requires supporting observations")
    observation_ids = {row["id"] for row in model.get("observations") or []
                       if isinstance(row, dict)}
    if any(ref not in observation_ids for ref in support):
        raise ValueError("re-identification cites unknown supporting observations")
    spanned = [(track, track_span(track)) for track in model.get("tracks") or []
               if isinstance(track, dict)]
    spanned = [(track, span) for track, span in spanned if span is not None]
    backing_index = observation_index(model)
    links: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for earlier, earlier_span in spanned:
        for later, later_span in spanned:
            if earlier is later:
                continue
            earlier_clock, earlier_start, earlier_end = earlier_span
            later_clock, later_start, later_end = later_span
            if earlier_clock != later_clock:
                continue
            if later_start < earlier_end:
                continue
            gap = later_start - earlier_end
            if gap <= 0 or gap > max_gap_ticks:
                continue
            earlier_kind, earlier_class = _entity_class(
                model, str(earlier.get("entity_id")))
            later_kind, later_class = _entity_class(
                model, str(later.get("entity_id")))
            pair = (str(earlier.get("id")), str(later.get("id")))
            if pair in seen:
                continue
            seen.add(pair)
            if earlier_kind != later_kind or earlier_class != later_class:
                skipped.append({"earlier": pair[0], "later": pair[1],
                                "reason": "class-or-kind-mismatch"})
                continue
            if earlier.get("entity_id") == later.get("entity_id"):
                continue
            earlier_samples = [sample for sample in earlier.get("samples") or []
                               if isinstance(sample, dict)
                               and isinstance(sample.get("tick"), int)]
            later_samples = [sample for sample in later.get("samples") or []
                             if isinstance(sample, dict)
                             and isinstance(sample.get("tick"), int)]
            earlier_sample = max(earlier_samples,
                                 key=lambda sample: sample["tick"])
            later_sample = min(later_samples,
                               key=lambda sample: sample["tick"])
            earlier_box = _normalised_sample_box(earlier_sample)
            later_box = _normalised_sample_box(later_sample)
            if earlier_box is None or later_box is None:
                skipped.append({"earlier": pair[0], "later": pair[1],
                                "reason": "missing-endpoint-geometry"})
                continue
            earlier_center = ((earlier_box[0] + earlier_box[2]) / 2,
                              (earlier_box[1] + earlier_box[3]) / 2)
            later_center = ((later_box[0] + later_box[2]) / 2,
                            (later_box[1] + later_box[3]) / 2)
            distance = ((earlier_center[0] - later_center[0]) ** 2
                        + (earlier_center[1] - later_center[1]) ** 2) ** 0.5
            if distance > max_center_distance:
                skipped.append({"earlier": pair[0], "later": pair[1],
                                "reason": "endpoint-geometry-too-distant"})
                continue
            anchor_names = {name for name in
                              (later.get("id"), later.get("local_track_id"))
                              if isinstance(name, str) and name}
            earlier_names = {name for name in
                             (earlier.get("id"), earlier.get("local_track_id"))
                             if isinstance(name, str) and name}
            later_anchors = [row for name in anchor_names
                             for row in backing_index.get(name, [])
                             if row["interval"]["start"] <= later_sample["tick"]
                             < row["interval"]["end"]]
            earlier_anchors = [row for name in earlier_names
                               for row in backing_index.get(name, [])
                               if row["interval"]["start"] <= earlier_sample["tick"]
                               < row["interval"]["end"]]
            if not earlier_anchors or not later_anchors:
                skipped.append({"earlier": pair[0], "later": pair[1],
                                "reason": "missing-endpoint-observation"})
                continue
            earlier_anchor = min(earlier_anchors,
                                 key=lambda row: row["interval"]["start"])
            anchor = min(later_anchors,
                         key=lambda row: row["interval"]["start"])
            link_id = (f"link-reid-{pair[0]}-{pair[1]}")
            links.append({
                "id": link_id,
                "entity_id": earlier.get("entity_id"),
                "observation_id": anchor["id"],
                "confidence": None,
                "method": REIDENTIFICATION_METHOD,
                "status": "verified" if verify else "candidate",
                "provenance_id": (provenance_id
                                  or anchor.get("provenance_id")),
                "supporting_observation_ids": ([] if no_support_yet else
                                              sorted({*support, earlier_anchor["id"],
                                                      anchor["id"]})),
                "track_id": later.get("id"),
                "scope": {"clock_id": earlier_clock,
                          "start": earlier_start, "end": later_end},
                "uncertainty": None,
            })
    links.sort(key=lambda link: link["id"])
    skipped.sort(key=lambda row: (row["earlier"], row["later"]))
    return {"links": links, "skipped": skipped}


def promote_identity_scope(model: dict[str, Any], entity_id: str,
                           target_scope: str, *,
                           supporting_observation_ids: list[str]) -> dict[str, Any]:
    """Widen one entity's identity scope on verified evidence.

    Promotion requires a verified identity link for the entity whose
    supporting observations are all known: scope widens by proof,
    never by assertion. The entity dict in the passed model is
    updated and returned. Narrowing, no-op rewrites and legacy
    ``None`` scopes without proof are refused.
    """
    if target_scope not in SCOPE_RANK:
        raise ValueError(f"unknown identity scope {target_scope!r}")
    if not supporting_observation_ids:
        raise ValueError("scope promotion requires supporting observations")
    observation_ids = {row["id"] for row in model.get("observations") or []
                       if isinstance(row, dict)}
    if not set(supporting_observation_ids) <= observation_ids:
        raise ValueError("scope promotion cites unknown observations")
    entity = next((row for row in model.get("entities") or []
                   if isinstance(row, dict) and row.get("id") == entity_id),
                  None)
    if entity is None:
        raise ValueError(f"unknown entity {entity_id!r}")
    current = entity.get("identity_scope")
    if (current is not None and current not in SCOPE_RANK):
        raise ValueError(f"entity carries unknown scope {current!r}")
    current_rank = SCOPE_RANK.get(current, -1)
    if SCOPE_RANK[target_scope] <= current_rank:
        raise ValueError("scope promotion must widen the current scope")
    verified = [link for link in model.get("identity_links") or []
                if isinstance(link, dict)
                and link.get("entity_id") == entity_id
                and link.get("status") == "verified"
                and set(supporting_observation_ids) <= set(
                    link.get("supporting_observation_ids") or [])]
    if not verified:
        raise ValueError("scope promotion requires a verified identity link "
                         "carrying the supporting observations")
    entity["identity_scope"] = target_scope
    return entity


def source_clock_ids(model: dict[str, Any]) -> dict[str, str]:
    assets = {row["id"]: os.path.basename(row["uri"])
              for row in model.get("assets") or []
              if isinstance(row, dict) and isinstance(row.get("uri"), str)}
    return {assets[clock["domain"]["asset_id"]]: clock["id"]
            for clock in model.get("clocks") or []
            if isinstance(clock, dict)
            and clock.get("domain", {}).get("asset_id") in assets}


def _normalised_sample_box(sample: dict[str, Any]) -> list[float] | None:
    geometry = sample.get("geometry") or {}
    box = geometry.get("bbox")
    if (isinstance(box, list) and len(box) == 4
            and all(isinstance(value, (int, float)) for value in box)):
        return [float(value) for value in box]
    box = geometry.get("bbox_pixels")
    coordinate_space = str(sample.get("coordinate_space") or "")
    try:
        width, height = (int(value) for value in
                         coordinate_space.rsplit("_", 1)[1].split("x"))
    except (ValueError, IndexError):
        return None
    if (not isinstance(box, list) or len(box) != 4
            or width <= 0 or height <= 0):
        return None
    return [float(box[0]) / width, float(box[1]) / height,
            float(box[2]) / width, float(box[3]) / height]


def _box_iou(left: list[float], right: list[float]) -> float:
    x1, y1 = max(left[0], right[0]), max(left[1], right[1])
    x2, y2 = min(left[2], right[2]), min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
    right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def score_identity_switches(model: dict[str, Any],
                            labels: dict[str, Any], *,
                            iou_threshold: float = 0.5,
                            max_sample_distance_ticks: int = 500) -> dict[str, Any]:
    """Count local-track switches after matching labeled boxes per frame.

    Labels name a source basename and give normalized ``[x1,y1,x2,y2]``
    boxes at exact source seconds. At each labeled instant, a greedy
    one-to-one maximum-IoU match assigns model tracks; misses are not
    filled from neighboring frames. The report compares raw local track
    IDs with the Project Model's candidate/verified re-identification
    links, exposing both potential continuity and the label-checkable
    risk of a wrong link.
    """
    if not 0 < iou_threshold <= 1 or max_sample_distance_ticks < 0:
        raise ValueError("invalid identity scoring thresholds")
    clock_ids = source_clock_ids(model)
    tracks = [track for track in model.get("tracks") or []
              if isinstance(track, dict)]
    entity_kinds = {entity["id"]: entity.get("kind")
                    for entity in model.get("entities") or []
                    if isinstance(entity, dict) and entity.get("id")}
    linked_entity = {link["track_id"]: link["entity_id"]
                     for link in model.get("identity_links") or []
                     if isinstance(link, dict)
                     and link.get("method") == REIDENTIFICATION_METHOD
                     and link.get("status") in ("candidate", "verified")
                     and link.get("track_id")}
    reidentified_entities = set(linked_entity.values())
    linked_identity_by_track = {
        track["id"]: linked_entity.get(
            track["id"], track.get("entity_id")
            if track.get("entity_id") in reidentified_entities else
            track.get("local_track_id") or track["id"])
        for track in tracks if track.get("id")
    }
    per_label: list[dict[str, Any]] = []
    switches_before = switches_after = misses = observations = 0
    for entry in labels.get("identities") or []:
        label_id = str(entry["label_id"])
        source = str(entry["source_file"])
        kind = entry.get("kind")
        clock_id = clock_ids.get(os.path.basename(source))
        if clock_id is None:
            raise ValueError(f"no Project Model clock for labeled source {source!r}")
        last_before = last_after = None
        assigned = []
        for frame in sorted(entry.get("frames") or [],
                            key=lambda row: row["time_seconds"]):
            tick = round(float(frame["time_seconds"]) * 1000)
            gt_box = [float(value) for value in frame["bbox"]]
            candidates = []
            for track in tracks:
                if track.get("clock_id") != clock_id:
                    continue
                if kind is not None and entity_kinds.get(track.get("entity_id")) != kind:
                    continue
                for sample in track.get("samples") or []:
                    sample_tick = sample.get("tick")
                    if (not isinstance(sample_tick, int)
                            or abs(sample_tick - tick) > max_sample_distance_ticks):
                        continue
                    predicted_box = _normalised_sample_box(sample)
                    if predicted_box is not None:
                        candidates.append((_box_iou(gt_box, predicted_box),
                                           str(track.get("id")),
                                           str(track.get("local_track_id")
                                               or track.get("id")),
                                           str(linked_identity_by_track[
                                               track["id"]]),
                                           str(track.get("entity_id"))))
            observations += 1
            if not candidates:
                misses += 1
                continue
            iou, track_id, local_id, linked_id, entity_id = max(candidates)
            if iou < iou_threshold:
                misses += 1
                continue
            if last_before is not None and local_id != last_before:
                switches_before += 1
            if last_after is not None and linked_id != last_after:
                switches_after += 1
            last_before, last_after = local_id, linked_id
            assigned.append({"time_seconds": float(frame["time_seconds"]),
                             "track_id": track_id, "local_track_id": local_id,
                             "linked_identity": linked_id,
                             "entity_id": entity_id, "iou": iou})
        per_label.append({"label_id": label_id, "source_file": source,
                          "matched_frames": len(assigned),
                          "frames": assigned})
    return {"switches_before": switches_before,
            "switches_after": switches_after,
            "misses": misses, "observations": observations,
            "per_label": per_label}
