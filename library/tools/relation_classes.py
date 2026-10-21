"""Lane 3: relation classes with different evidence bars.

Temporal edges are computable from clock-aligned intervals;
physical edges need spatial/track evidence and stay candidates
until verified; semantic edges are never derived here - a model
interpretation owns them (report lane 3, Relation row). Every edge
cites the observations it rests on; a span with no backing
observation is skipped, never bridged.

**The predicate vocabulary is PUBLISHED here.** `PREDICATES` names every
relation predicate the engine knows, the class it belongs to, the
evidence bar it must clear and the lane that derives it. A predicate
that is not in the table is not a relation the model may carry: the
Project Model validator refuses it (see `project_model.validate`).
The table is the answer to "which typed relationships does Ren hold",
not a private tuple in a deriver.

The seven predicates the grounding programme names (B-10) are
`touching, holding, looking_at, speaking, entering, leaving,
interacting`; `co_present` and `pointing` are the two the engine
already measured.
"""
from __future__ import annotations

import os
from collections import defaultdict
from math import hypot
from typing import Any

from library.tools.identity_reassociation import interval_iou, source_clock_ids

RELATION_CLASSES = ("temporal", "physical", "semantic")

PREDICATES: dict[str, dict[str, Any]] = {
    "co_present": {
        "relation_class": "temporal",
        "participants": "any two entities",
        "evidence": "track samples sharing ticks on one clock",
        "derived_by": "library.tools.relation_classes."
                      "derive_temporal_relations",
        "certainty": "measured",
        "semantics": "the two entities are present in the frame at the "
                     "same time; it says nothing about what they did "
                     "to each other",
    },
    "touching": {
        "relation_class": "physical",
        "participants": "person and object, or person and person",
        "evidence": "a measured hand joint inside the other entity's "
                    "box, or two measured hands in contact",
        "derived_by": "library.tools.relation_classes."
                      "derive_physical_relations",
        "certainty": "inferred",
        "semantics": "contact: the two entities touch. A contact that "
                     "lasts is a `holding`, not a longer `touching`",
    },
    "holding": {
        "relation_class": "physical",
        "participants": "person and object",
        "evidence": "a measured contact sustained for at least "
                    "HOLDING_MIN_SECONDS",
        "derived_by": "library.tools.relation_classes."
                      "derive_physical_relations",
        "certainty": "inferred",
        "semantics": "possession: the person keeps contact with the "
                     "object rather than merely reaching it",
    },
    "looking_at": {
        "relation_class": "physical",
        "participants": "person and person",
        "evidence": "a measured head-pose ray meeting the other "
                    "person's measured region",
        "derived_by": "library.tools.project_model_evidence."
                      "_migrate_relationships (M10)",
        "certainty": "inferred",
        "semantics": "the subject's turned head faces the object; it "
                     "does not claim the object looks back",
    },
    "pointing": {
        "relation_class": "physical",
        "participants": "person and person",
        "evidence": "a measured extended hand whose ray falls within "
                    "POINTING_MAX_ANGLE_DEG of the other person's face",
        "derived_by": "library.tools.project_model_evidence."
                      "_migrate_relationships (M10)",
        "certainty": "inferred",
        "semantics": "the subject's hand aims at the object",
    },
    "speaking": {
        "relation_class": "temporal",
        "participants": "person and person",
        "evidence": "a measured voice turn of the subject overlapping "
                    "a measured presence of the object",
        "derived_by": "library.tools.relation_classes."
                      "derive_speaking_relations",
        "certainty": "inferred",
        "semantics": "the subject speaks while the object is present. "
                     "It is a co-occurrence, NOT an addressee claim: "
                     "who is spoken to is not measured here",
    },
    "speaking_person_candidate": {
        "relation_class": "temporal",
        "participants": "speaker and person",
        "evidence": "a measured alignment between a speaker's words "
                    "and a person's voice turn",
        "derived_by": "library.tools.cross_modal_grounding",
        "certainty": "inferred",
        "semantics": "the diarised speaker may be this person; a "
                     "candidate until a voice match verifies it",
    },
    "entering": {
        "relation_class": "physical",
        "participants": "entity and scene",
        "evidence": "a measured track whose first sample falls inside "
                    "the scene's interval",
        "derived_by": None,
        "certainty": "measured",
        "semantics": "the subject comes into the object's space. The "
                     "vocabulary is published; no lane derives it yet, "
                     "because the model carries no scene entity",
    },
    "leaving": {
        "relation_class": "physical",
        "participants": "entity and scene",
        "evidence": "a measured track whose last sample falls inside "
                    "the scene's interval",
        "derived_by": None,
        "certainty": "measured",
        "semantics": "the subject goes out of the object's space. The "
                     "vocabulary is published; no lane derives it yet, "
                     "because the model carries no scene entity",
    },
    "interacting": {
        "relation_class": "semantic",
        "participants": "person and person",
        "evidence": "an interpretation-level reading of measured "
                    "co-presence and contact",
        "derived_by": None,
        "certainty": "interpreted",
        "semantics": "the two people act on each other. A model "
                     "interpretation owns this edge; the engine never "
                     "derives it, and it is never verified",
    },
}
"""Every relation predicate the engine knows, with its class, its
evidence bar, the lane that derives it and what it means."""

HOLDING_MIN_SECONDS = 1.0
"""A contact sustained this long is a `holding` (possession) rather
than a `touching` (a reach). The review's own bar for holding is
"> 1 s"; the engine measures the contact, so the bar is a stated
constant rather than a model's opinion."""

TEMPORAL_PREDICATES = tuple(name for name, row in PREDICATES.items()
                            if row["relation_class"] == "temporal")

PHYSICAL_PREDICATES = tuple(name for name, row in PREDICATES.items()
                            if row["relation_class"] == "physical")

SEMANTIC_PREDICATES = tuple(name for name, row in PREDICATES.items()
                            if row["relation_class"] == "semantic")


def predicate_class(predicate: str) -> str | None:
    """The class a published predicate belongs to, or None when the
    predicate is not published."""
    row = PREDICATES.get(predicate)
    return row["relation_class"] if row else None


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


def _transformed(tick: int, transform: dict[str, Any]) -> int | None:
    """One tick of a transform's `from` clock, in its `to` clock."""
    for segment in transform.get("segments") or []:
        source = segment.get("from") or {}
        target = segment.get("to") or {}
        try:
            start = int(source["start"])
            end = int(source["end"])
            to_start = int(target["start"])
        except (KeyError, TypeError, ValueError):
            continue
        if not start <= tick < end:
            continue
        slope = segment.get("slope") or {}
        try:
            numerator = float(slope.get("numerator", 1))
            denominator = float(slope.get("denominator", 1))
        except (TypeError, ValueError):
            continue
        if denominator == 0:
            continue
        return int(round(to_start + (tick - start) * numerator / denominator))
    return None


def derive_speaking_relations(model: dict[str, Any], *,
                               presence_modalities: tuple[str, ...] =
                               ("face", "face_frame", "region_track",
                                "region_frame")
                               ) -> list[dict[str, Any]]:
    """`speaking` edges: the subject speaks while the object is present.

    The subject is the person a measured voice turn belongs to (a
    verified identity link on the turn). The object is another person
    whose measured presence overlaps the turn - on the same camera, or
    on another camera of the same conversation, placed on the speaker's
    clock by the model's own measured time transform.

    The edge is a CO-OCCURRENCE, not an addressee claim: who is spoken
    to is not measured here, and the edge says so in its own semantics.

    Edges stay candidates with inferred certainty - the voice turn and
    the presence are measured, the relation between them is inferred.
    """
    entities = {row["id"]: row.get("kind")
                for row in model.get("entities") or []
                if isinstance(row, dict)}
    owners: dict[str, set[str]] = defaultdict(set)
    for link in model.get("identity_links") or []:
        if not isinstance(link, dict):
            continue
        if link.get("status") != "verified":
            continue
        entity_id = link.get("entity_id")
        observation_id = link.get("observation_id")
        if not isinstance(entity_id, str) or not isinstance(observation_id, str):
            continue
        if entities.get(entity_id) != "person":
            continue
        owners[observation_id].add(entity_id)
    presence: dict[str, list[tuple[int, int, str, str]]] = defaultdict(list)
    for row in model.get("observations") or []:
        if not isinstance(row, dict):
            continue
        if row.get("modality") not in presence_modalities:
            continue
        interval = row.get("interval") or {}
        clock_id = interval.get("clock_id")
        if not isinstance(clock_id, str):
            continue
        for entity_id in owners.get(row.get("id"), ()):  # linked person
            presence[clock_id].append(
                (int(interval["start"]), int(interval["end"]), entity_id,
                 str(row.get("id"))))
    # A second camera of the same conversation: its presence lands on
    # the speaker's clock through the model's own measured transform.
    into: dict[str, list[tuple[int, int, str, str]]] = defaultdict(list)
    for transform in model.get("time_transforms") or []:
        if not isinstance(transform, dict):
            continue
        to_clock = transform.get("to_clock")
        from_clock = transform.get("from_clock")
        if not isinstance(to_clock, str) or not isinstance(from_clock, str):
            continue
        for start, end, entity_id, observation_id in presence.get(from_clock, ()):
            mapped_start = _transformed(start, transform)
            mapped_end = _transformed(max(start, end - 1), transform)
            if mapped_start is None or mapped_end is None:
                continue
            into[to_clock].append((mapped_start, mapped_end + 1, entity_id,
                                    observation_id))
    for clock_id, rows in presence.items():
        into[clock_id].extend(rows)
    relations = []
    for row in model.get("observations") or []:
        if not isinstance(row, dict) or row.get("modality") != "voice_turn":
            continue
        interval = row.get("interval") or {}
        clock_id = interval.get("clock_id")
        if not isinstance(clock_id, str):
            continue
        speakers = owners.get(row.get("id"), set())
        if len(speakers) != 1:
            # No voice, or an ambiguous voice: no subject, no edge.
            continue
        speaker = next(iter(speakers))
        start, end = int(interval["start"]), int(interval["end"])
        for other_start, other_end, other, other_obs in into.get(clock_id, ()):
            if other == speaker:
                continue
            if other_end <= start or other_start >= end:
                continue
            relations.append({
                "id": (f"relation-speaking-{speaker}-{other}-"
                       f"{clock_id}-{start}-{end}"),
                "subject_id": speaker,
                "predicate": "speaking",
                "object_id": other,
                "interval": {"clock_id": clock_id,
                             "start": start, "end": end},
                "confidence": None,
                "provenance_id": row.get("provenance_id"),
                "evidence_ids": sorted({row["id"], other_obs}),
                "status": "candidate",
                "certainty": "inferred",
                "relation_class": "temporal",
            })
    relations.sort(key=lambda row: row["id"])
    return relations


def derive_physical_relations(
        model: dict[str, Any], *,
        hand_radius: float = 0.12,
        min_samples: int = 2,
        max_gap_ticks: int = 2000,
        max_face_drift_ticks: int = 500) -> list[dict[str, Any]]:
    """Hand-object contact edges from measured landmarks and boxes.

    A contact hit is a confident hand joint inside ``hand_radius`` of
    an object box on the same clock within ``max_face_drift_ticks``.
    Consecutive hits group into one edge; a group shorter than
    ``min_samples`` is dropped. The hand's owner is the person the
    hand-bearing observation links to - an unlinked or ambiguously
    linked row contributes no subject, so no edge is invented for it.
    Edges stay candidates with inferred certainty: proximity is
    measured, contact is inferred.

    A group that lasts at least ``HOLDING_MIN_SECONDS`` is a
    `holding` (possession); a shorter one is a `touching` (a reach).
    """
    if hand_radius <= 0 or min_samples < 1 or max_gap_ticks <= 0:
        raise ValueError("physical relation thresholds must be positive")
    from library.tools.identity_reassociation import observation_index
    backing_index = observation_index(model)
    entity_kind = {row["id"]: row.get("kind")
                   for row in model.get("entities") or []
                   if isinstance(row, dict)}
    face_rows = _hand_bearing_observations(model)
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
        if _is_region_track(track):
            # A person instance mask, not an object: a hand inside it is
            # the person's own body, never a contact with a thing.
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


def _is_region_track(track: dict[str, Any]) -> bool:
    """Whether a track is a person instance mask rather than an object.

    M3d writes a person's region as a track whose samples carry
    ``geometry.kind == "region"``. The migration files an unowned
    region under an entity of kind ``object`` (it is an asset-local
    instance, not a durable person), so the kind alone does not tell a
    body from a thing - the geometry does.
    """
    for sample in track.get("samples") or []:
        if not isinstance(sample, dict):
            continue
        geometry = sample.get("geometry") or {}
        if isinstance(geometry, dict) and geometry.get("kind") == "region":
            return True
    return False


def _hand_bearing_observations(model: dict[str, Any]) -> list[dict[str, Any]]:
    """The observations that carry measured hand landmarks.

    M3 measures the hands per FRAME, and the migration writes them on
    a `face_frame` row; M3b's face spans carry a `face` row. A model
    that hands its landmarks on either shape is read, so a migration
    that drops or renames the row does not silently empty the
    physical relations.
    """
    rows = []
    for row in model.get("observations") or []:
        if not isinstance(row, dict):
            continue
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("modality") not in ("face_frame", "face"):
            continue
        if not payload.get("hands"):
            continue
        rows.append(row)
    return rows


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
    predicate = ("holding"
                 if (end - start) >= HOLDING_MIN_SECONDS * 1000
                 else "touching")
    return [{
        "id": (f"relation-{predicate}-{person_id}-"
               f"{track.get('entity_id')}-{start}-{end}"),
        "subject_id": person_id,
        "predicate": predicate,
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
                    iou_threshold: float = 0.5,
                    predicates: tuple[str, ...] = ("touching",),
                    entity_kinds: dict[str, str] | None = None) -> dict[str, Any]:
    """Precision/recall of physical edges against labeled contacts.

    ``entity_labels`` maps model entity ids to blind-label persistent
    ids; the map is built at scoring time by inspecting overlaps, so
    the labellers never see model internals. A labeled contact is
    recalled when a matching edge on the same clock spans it at
    ``iou_threshold``.

    ``predicates`` names the prediction predicates that count as a
    contact; the family score covers all of them, and
    ``per_predicate`` reports each one on its own, so a reader can see
    which typed relation carried the score. ``entity_kinds`` maps
    model entity ids to their kind; with it, a prediction is only
    matched to a label whose person/object roles agree, so a
    person-to-person contact is never scored as a false positive
    against a person-to-object label.
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
                "predicate": contact.get("predicate"),
            })
    physical = [rel for rel in relations
                if isinstance(rel, dict)
                and rel.get("relation_class") == "physical"]

    def _kind_pair(entity_a: Any, entity_b: Any) -> tuple[Any, Any] | None:
        if entity_kinds is None:
            return None
        return (entity_kinds.get(entity_a), entity_kinds.get(entity_b))

    def _label_kind_pairs() -> set[tuple[Any, Any]]:
        if entity_kinds is None:
            return set()
        reverse = {label: entity for entity, label
                   in entity_labels.items()}
        pairs: set[tuple[Any, Any]] = set()
        for contact in labeled:
            person_entity = reverse.get(contact.get("person"))
            object_entity = reverse.get(contact.get("object"))
            if person_entity is None or object_entity is None:
                pairs.add((None, None))
                continue
            pairs.add((entity_kinds.get(person_entity),
                       entity_kinds.get(object_entity)))
        return pairs

    def _score(predicate_subset: list[dict[str, Any]]) -> dict[str, Any]:
        label_pairs = _label_kind_pairs()
        comparable: list[dict[str, Any]] = []
        for rel in predicate_subset:
            pair = _kind_pair(rel.get("subject_id"), rel.get("object_id"))
            if pair is None or pair == (None, None) or pair in label_pairs:
                comparable.append(rel)
        matched_labels: set[int] = set()
        matched_predicted: set[int] = set()
        for label_index, contact in enumerate(labeled):
            person = contact.get("person")
            obj = contact.get("object")
            clock_id = contact.get("clock_id")
            span = (int(contact["start_tick"]), int(contact["end_tick"]))
            for pred_index, rel in enumerate(comparable):
                if pred_index in matched_predicted:
                    continue
                subject = entity_labels.get(rel.get("subject_id"))
                obj_label = entity_labels.get(rel.get("object_id"))
                if subject != person or obj_label != obj:
                    continue
                interval = rel.get("interval") or {}
                if interval.get("clock_id") != clock_id:
                    continue
                if interval_iou((interval["start"], interval["end"]),
                                span) >= iou_threshold:
                    matched_labels.add(label_index)
                    matched_predicted.add(pred_index)
                    break
        precision = (len(matched_predicted) / len(comparable)
                     if comparable else None)
        recall = len(matched_labels) / len(labeled) if labeled else None
        return {"true_positives": len(matched_labels),
                "predicted": len(comparable), "labeled": len(labeled),
                "precision": precision, "recall": recall}

    family = [rel for rel in physical
              if rel.get("predicate") in predicates]
    per_predicate = {predicate: _score([rel for rel in physical
                                        if rel.get("predicate") == predicate])
                     for predicate in predicates}
    result = _score(family)
    result["per_predicate"] = per_predicate
    return result


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
