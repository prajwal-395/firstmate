"""VI-008: a geometric candidate is not a relation.

The engine measures hands (M3, per frame) and objects (the
request-driven object lane, or 1.06's tracks).  The GEOMETRY between
them is a CANDIDATE: a hand joint inside an object's box is contact,
and a contact sustained for a second is possession - but the geometry
is a proximity rule over imprecise signals, so the engine keeps the two
apart and says which is which.

* A candidate whose measured joint lands INSIDE the object's box
  clears the predicate's published evidence bar
  (`relation_classes.PREDICATES`) and becomes a CONFIRMED relation:
  `touching`, or `holding` when the contact is sustained for
  `HOLDING_MIN_SECONDS`.
* A candidate whose joint only NEARS the box is AMBIGUOUS.  It stays a
  candidate, and only an ambiguous candidate is worth a VLM call: the
  crop around the hand and the object is put to the local model with the
  statement the predicate makes, and a "yes" confirms it.  A clear
  candidate is never shown to the model - the geometry already cleared
  the bar, and a model call on it would buy nothing.

Relations are TIME-BOUNDED: every edge carries the interval it was
measured over, so "the person is holding the cup" is answered for a
span of the footage, never as a permanent attribute of the person.

The distinction is preserved all the way to the query: a relation query
answers `present` from CONFIRMED relations only, and a candidate is
reported as a candidate (`evidence_query`'s `relation_presence`).
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from itertools import pairwise
from pathlib import Path
from typing import Any

from library.tools import relation_classes

CANDIDATE_BASIS = "hand_object_geometry"
"""The basis a geometric candidate rests on: measured hands and measured
object boxes, and nothing else."""

CONFIRMATION_GEOMETRY = "measured_geometry"
"""The confirmation basis of a relation the geometry itself cleared:
the measured joint landed inside the box and the contact was measured
over the edge's own interval."""

CONFIRMATION_VLM = "vlm_crop"
"""The confirmation basis of a relation the local model confirmed: the
crop around the hand and the object was judged and answered yes."""

HAND_JOINT_MIN_CONFIDENCE = 0.35
"""The floor every hand measurement in this engine has used
(`event_spans.HAND_JOINT_MIN_CONFIDENCE`)."""

HAND_RADIUS = 0.12
"""How near, in frame widths, a joint may sit to the object's box and
still be a contact candidate. The same radius `relation_classes`
derives physical relations with."""

MIN_SAMPLES = 1
"""A contact needs this many hit frames.

ONE, because the published evidence bar for `touching` is a measured
hand joint inside the other entity's box - a contact is one measured
frame of contact - while the bar for `holding` is the SUSTAINED
contact, and the sustain is measured over the edge's own interval
rather than by counting hits.  A lane that needs two hits to call a
contact cannot measure a contact that lasts one frame, and calls it
nothing at all.
"""

CORROBORATION_MIN_HITS = 2
"""A contact needs this many MEASURED FRAMES to be CORROBORATED, and
only a corroborated one is settled by the geometry alone.

The engine's own M10 lane holds a one-sample relation to be a glance
and not a look (`relationships.HOLDING_MIN_FRAMES`); the same reading
applies here.  A single measured joint inside a detector's box is one
measurement, and one measurement of a small object next to a hand is
exactly what a detector's false positive looks like.  So a candidate
whose geometry is not corroborated is AMBIGUOUS, and an ambiguous
candidate is the one the model is asked about.

The count is of FRAMES, not of hits: three detector boxes on ONE frame
are one measurement three times over, and corroboration means a second
look at a later moment.
"""

MAX_GAP_TICKS = 2000
"""How far apart two hits may be and still be one contact."""

MAX_FRAME_DRIFT_TICKS = 600
"""How far, in ticks, a hand measurement may sit from an object
detection and still be the same moment: M3 measures at 2 Hz and the
object lane samples at 1 Hz."""

CROP_MARGIN = 0.3
"""The margin, as a fraction of the frame, around the hand-and-object
region the crop shows."""

MAX_FRAMES_PER_CANDIDATE = 3
"""The crops shown for one candidate."""

PROMPT_VERSION = 1
PROMPT = (
    "These are {n} crops of one video, in time order, about {gap} "
    "seconds apart. Each crop shows the region around a person's hand "
    "and one object. Statement: \"{statement}\". Is the statement true "
    "in these crops - in at least one crop, or across the sequence of "
    "crops? Judge only what is visible. Reply with JSON only: "
    "{{\"answer\": \"yes\" or \"no\", "
    "\"frame\": <1-based number of the clearest crop, or null>, "
    "\"reason\": \"<one short sentence>\"}}")
"""Pre-registered verbatim. Changing a word is a new PROMPT_VERSION:
verdicts recorded under the old wording are not verdicts under this
one."""

MAX_TOKENS = 120


def _slug(text: str) -> str:
    """A safe token for an id."""
    return "".join(char if char.isalnum() else "_"
                   for char in str(text).strip().lower())[:40] or "object"


def _norm_box(box: list[float], width: Any, height: Any) -> list[float] | None:
    """A pixel box as a normalised one, or None when it cannot be."""
    try:
        w = float(width)
        h = float(height)
        x1, y1, x2, y2 = (float(value) for value in box)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return [x1 / w, y1 / h, x2 / w, y2 / h]


def _distance_to_box(point: tuple[float, float],
                     box: list[float]) -> float:
    """The distance from a point to a box; 0 inside it."""
    x1, y1, x2, y2 = box
    dx = max(x1 - point[0], 0.0, point[0] - x2)
    dy = max(y1 - point[1], 0.0, point[1] - y2)
    return (dx * dx + dy * dy) ** 0.5


def object_boxes(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Every measured object box the model carries.

    Two producers write them and both are read here: the
    REQUEST-DRIVEN detections (the object lane that answers a named
    object on request) and the 1.06 object TRACKS.  A person region is
    not an object - a hand inside it is the person's own body - so the
    region tracks are left out.
    """
    from library.tools.analysis import object_grounding

    boxes: list[dict[str, Any]] = []
    for observation in model.get("observations") or []:
        if not isinstance(observation, dict):
            continue
        if observation.get("modality") != "object_detection":
            continue
        value = object_grounding.value_of(observation)
        if not value or value.get("kind") != "detection":
            continue
        raw = [float(v) for v in (value.get("box") or [])]
        if len(raw) != 4:
            continue
        width = value.get("frame_width")
        height = value.get("frame_height")
        if raw and max(raw) > 1.5:
            # A box in pixels: the detectors write a normalised one, and
            # a pixel box is normalised here so the two meet.
            norm = _norm_box(raw, width, height)
            pixels = raw
        else:
            norm = raw
            pixels = ([raw[0] * float(width), raw[1] * float(height),
                       raw[2] * float(width), raw[3] * float(height)]
                      if width and height else [])
        if norm is None:
            continue
        interval = observation.get("interval") or {}
        clock_id = interval.get("clock_id")
        start = interval.get("start")
        if not isinstance(clock_id, str) or not isinstance(start, int):
            continue
        phrase = (value.get("query") or value.get("phrase")
                  or value.get("label") or "")
        boxes.append({
            "kind": "detection",
            "clock_id": clock_id,
            "tick": start,
            "end_tick": interval.get("end"),
            "box": norm,
            "box_pixels": pixels,
            "frame_width": value.get("frame_width"),
            "frame_height": value.get("frame_height"),
            "phrase": str(phrase),
            "detector": value.get("detector"),
            "observation_id": observation.get("id"),
            "asset_id": observation.get("asset_id"),
            "provenance_id": observation.get("provenance_id"),
        })
    entity_kind = {row["id"]: row.get("kind")
                   for row in model.get("entities") or []
                   if isinstance(row, dict)}
    entity_label = {row["id"]: row.get("label")
                    for row in model.get("entities") or []
                    if isinstance(row, dict)}
    for track in model.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        entity_id = track.get("entity_id")
        if entity_kind.get(entity_id) != "object":
            continue
        if relation_classes._is_region_track(track):
            continue
        for tick, box in relation_classes._object_boxes(track):
            boxes.append({
                "kind": "track",
                "clock_id": track.get("clock_id"),
                "tick": tick,
                "end_tick": tick + 1,
                "box": box,
                "phrase": str(entity_label.get(entity_id) or ""),
                "observation_id": None,
                "entity_id": entity_id,
                "asset_id": None,
            })
    return boxes


def hand_frames(model: dict[str, Any]) -> list[dict[str, Any]]:
    """The measured hand landmarks, each with the person they belong to.

    A hand whose owner the model cannot name contributes nothing: a
    relation with no subject is not a measurement.
    """
    frames = []
    for row in relation_classes._hand_bearing_observations(model):
        person_id = relation_classes._face_person(model, row)
        if person_id is None:
            continue
        interval = row.get("interval") or {}
        clock_id = interval.get("clock_id")
        start = interval.get("start")
        end = interval.get("end")
        if not isinstance(clock_id, str) or not isinstance(start, int):
            continue
        joints = []
        for hand in (row.get("payload") or {}).get("hands") or []:
            for joint in (hand.get("joints") or {}).values():
                try:
                    x, y, confidence = (float(value) for value in joint)
                except (TypeError, ValueError):
                    continue
                if confidence < HAND_JOINT_MIN_CONFIDENCE:
                    continue
                joints.append((x, y))
        if not joints:
            continue
        frames.append({
            "observation_id": row.get("id"),
            "clock_id": clock_id,
            "tick": (start + (end - 1 if isinstance(end, int) else start)) // 2,
            "joints": joints,
            "person_id": person_id,
            "asset_id": row.get("asset_id"),
        })
    return frames


def derive_candidates(model: dict[str, Any], *,
                      hand_radius: float = HAND_RADIUS,
                      min_samples: int = MIN_SAMPLES,
                      max_gap_ticks: int = MAX_GAP_TICKS,
                      max_frame_drift_ticks: int = MAX_FRAME_DRIFT_TICKS,
                      ) -> list[dict[str, Any]]:
    """Geometric `touching`/`holding` candidates, one row per contact.

    A hit is a measured hand joint within `hand_radius` of an object
    box measured on the same clock within `max_frame_drift_ticks`.  A
    joint INSIDE the box is a `clear` hit; a joint only NEAR the box is
    an `ambiguous` one - and only an ambiguous hit needs a model to
    judge it.

    Runs of hits group into one candidate, and a run that lasts at least
    `HOLDING_MIN_SECONDS` is a `holding` (possession); a shorter one is
    a `touching` (a reach).  Every candidate is TIME-BOUNDED: it
    carries the interval it was measured over.
    """
    if hand_radius <= 0 or min_samples < 1 or max_gap_ticks <= 0:
        raise ValueError("relation candidate thresholds must be positive")
    boxes = object_boxes(model)
    hands = hand_frames(model)
    hits: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for box in boxes:
        for hand in hands:
            if hand["clock_id"] != box["clock_id"]:
                continue
            if abs(hand["tick"] - box["tick"]) > max_frame_drift_ticks:
                continue
            best = None
            best_joint = None
            for joint in hand["joints"]:
                distance = _distance_to_box(joint, box["box"])
                if best is None or distance < best:
                    best = distance
                    best_joint = joint
            if best is None or best > hand_radius:
                continue
            key = (hand["person_id"], str(box.get("phrase") or ""))
            hits.setdefault(key, []).append({
                "tick": box["tick"],
                "person_id": hand["person_id"],
                "hand_observation_id": hand["observation_id"],
                "object_observation_id": box.get("observation_id"),
                "object_entity_id": box.get("entity_id"),
                "object_phrase": str(box.get("phrase") or ""),
                "provenance_id": box.get("provenance_id"),
                "clock_id": box["clock_id"],
                "asset_id": hand.get("asset_id") or box.get("asset_id"),
                "distance": round(float(best), 4),
                "clear": best <= 0.0,
                "joint": list(best_joint) if best_joint else None,
                "object_box_pixels": box.get("box_pixels"),
                "frame_width": box.get("frame_width"),
                "frame_height": box.get("frame_height"),
            })
    candidates: list[dict[str, Any]] = []
    for (person_id, phrase), rows in sorted(hits.items()):
        rows.sort(key=lambda row: row["tick"])
        groups: list[list[dict[str, Any]]] = []
        for row in rows:
            if (groups and row["tick"] - groups[-1][-1]["tick"]
                    <= max_gap_ticks):
                groups[-1].append(row)
            else:
                groups.append([row])
        for group in groups:
            if len(group) < min_samples:
                continue
            candidates.append(_candidate_row(person_id, phrase, group))
    candidates.sort(key=lambda row: row["id"])
    return candidates


def _candidate_row(person_id: str, phrase: str,
                   group: list[dict[str, Any]]) -> dict[str, Any]:
    """One candidate row: a geometric contact, not yet a relation."""
    start = group[0]["tick"]
    end = group[-1]["tick"] + 1
    inside = any(row["clear"] for row in group)
    frames = len({row["tick"] for row in group})
    corroborated = frames >= CORROBORATION_MIN_HITS
    strength = "clear" if (inside and corroborated) else "ambiguous"
    predicate = ("holding"
                 if (end - start) >= relation_classes.HOLDING_MIN_SECONDS * 1000
                 else "touching")
    evidence = sorted({ref for row in group
                       for ref in (row["hand_observation_id"],
                                   row["object_observation_id"])
                       if ref})
    return {
        "id": (f"relation-candidate-{predicate}-{person_id}-"
               f"{_slug(phrase)}-{start}-{end}"),
        "subject_id": person_id,
        "predicate": predicate,
        "object_id": None,
        "interval": {"clock_id": group[0]["clock_id"],
                     "start": start, "end": end},
        "confidence": None,
        "provenance_id": group[0].get("provenance_id"),
        "evidence_ids": evidence,
        "status": "candidate",
        "certainty": "inferred",
        "relation_class": "physical",
        "geometry": {
            "basis": CANDIDATE_BASIS,
            "strength": strength,
            "hits": len(group),
            "frames": frames,
            "inside_box": inside,
            "corroborated": corroborated,
            "object_phrase": phrase,
            "object_entity_id": group[0].get("object_entity_id"),
            "asset_id": group[0].get("asset_id"),
            "distances": [row["distance"] for row in group],
            "hit_ticks": [row["tick"] for row in group],
            "hits_detail": [
                {"tick": row["tick"],
                 "joint": row.get("joint"),
                 "object_box_pixels": row.get("object_box_pixels"),
                 "frame_width": row.get("frame_width"),
                 "frame_height": row.get("frame_height")}
                for row in group],
        },
    }


def statement_for(candidate: dict[str, Any]) -> str:
    """The claim a candidate puts to the model.

    The statement is the PREDICATE's claim, in words the picture can
    answer: what is true of the hand and the object in the crop.
    """
    phrase = (candidate.get("geometry") or {}).get("object_phrase") or "object"
    verb = "holding" if candidate.get("predicate") == "holding" else "touching"
    return f"a person's hand is {verb} the {phrase}"


def confirm_by_evidence_bar(candidates: list[dict[str, Any]],
                            ) -> list[dict[str, Any]]:
    """The candidates whose GEOMETRY cleared the predicate's bar.

    A measured joint inside the object's box is the evidence bar
    `relation_classes.PREDICATES` publishes for `touching` and
    `holding`.  Such a candidate is a CONFIRMED relation, and it says
    so: its confirmation names the basis it was confirmed on.
    """
    confirmed = []
    for candidate in candidates:
        meta = candidate.get("geometry") or {}
        if meta.get("strength") != "clear":
            continue
        confirmed.append({**candidate,
                          "status": "verified",
                          "confirmation": {"basis": CONFIRMATION_GEOMETRY,
                                           "hits": meta.get("hits"),
                                           "source": "the measured hand "
                                                     "joint landed inside "
                                                     "the measured object box"}})
    return confirmed


def ambiguous_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The candidates that need a model to judge them."""
    return [candidate for candidate in candidates
            if (candidate.get("geometry") or {}).get("strength") == "ambiguous"]


def confirmed_relations(candidates: list[dict[str, Any]],
                        verdicts: list[dict[str, Any]] | None = None,
                        ) -> list[dict[str, Any]]:
    """The CONFIRMED relations among the candidates.

    A candidate is confirmed when its geometry cleared the published
    bar, or when the local model answered yes to its crop.  A candidate
    the model answered no to is NOT a relation - and neither is one
    nobody judged.
    """
    verdicts_by_id = {verdict.get("candidate_id"): verdict
                      for verdict in (verdicts or [])}
    relations = []
    for candidate in candidates:
        meta = candidate.get("geometry") or {}
        if meta.get("strength") == "clear":
            relations.append({**candidate,
                              "status": "verified",
                              "confirmation": {
                                  "basis": CONFIRMATION_GEOMETRY,
                                  "hits": meta.get("hits"),
                                  "source": "the measured hand joint landed "
                                            "inside the measured object box"}})
            continue
        verdict = verdicts_by_id.get(candidate.get("id"))
        if not verdict or verdict.get("answer") != "yes":
            continue
        relations.append({**candidate,
                          "status": "verified",
                          "confirmation": {
                              "basis": CONFIRMATION_VLM,
                              "answer": verdict.get("answer"),
                              "reason": verdict.get("reason"),
                              "frame_t": verdict.get("frame_t"),
                              "frames": verdict.get("frames"),
                              "model": verdict.get("model"),
                              "prompt_version": verdict.get("prompt_version"),
                          }})
    return relations


def rejected_candidates(candidates: list[dict[str, Any]],
                        verdicts: list[dict[str, Any]] | None = None,
                        ) -> list[dict[str, Any]]:
    """The candidates the model answered NO to.

    They stay candidates, and they carry the verdict that refused
    them: a reader can see that the question was asked and answered.
    """
    verdicts_by_id = {verdict.get("candidate_id"): verdict
                      for verdict in (verdicts or [])}
    rejected = []
    for candidate in candidates:
        meta = candidate.get("geometry") or {}
        if meta.get("strength") == "clear":
            continue
        verdict = verdicts_by_id.get(candidate.get("id"))
        if not verdict or verdict.get("answer") != "no":
            continue
        rejected.append({**candidate,
                         "verification": {
                             "answer": verdict.get("answer"),
                             "reason": verdict.get("reason"),
                             "frame_t": verdict.get("frame_t"),
                             "frames": verdict.get("frames"),
                             "model": verdict.get("model"),
                             "prompt_version": verdict.get("prompt_version"),
                         }})
    return rejected


def verdict_key(statement: str, frame_times: list[float],
                model_id: str) -> str:
    """The key a relation verdict is recorded under.

    The family is in the key, so a relation verdict can never be read
    as a span verdict or an interval verdict.
    """
    import hashlib

    raw = json.dumps(["relation", PROMPT_VERSION, model_id,
                      statement.strip().lower(),
                      [round(float(t), 3) for t in frame_times]])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _source_seconds(candidate: dict[str, Any],
                    hits: list[int]) -> list[float]:
    """The source seconds a candidate's hits stand for."""
    clock = (candidate.get("interval") or {}).get("clock_id")
    rate = 1000.0
    if isinstance(clock, str) and clock.endswith("ms"):
        rate = 1000.0
    return [round(tick / rate, 3) for tick in hits]


def crop_region(candidate: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """The source-pixel region a candidate's crop shows.

    The region is the union of the measured object box and the measured
    hand joints, widened by `CROP_MARGIN` of the frame, so the model
    sees the hand and the thing it may be touching - and not the whole
    room.  A candidate whose boxes were not measured in source pixels
    has no region: its crop is the whole frame, and the caller says so.
    """
    meta = candidate.get("geometry") or {}
    detail = meta.get("hits_detail") or []
    for hit in detail:
        box = hit.get("object_box_pixels")
        width = hit.get("frame_width")
        height = hit.get("frame_height")
        if not box or not width or not height:
            continue
        try:
            x1, y1, x2, y2 = (float(value) for value in box)
            frame_w = float(width)
            frame_h = float(height)
        except (TypeError, ValueError):
            continue
        joint = hit.get("joint")
        if joint:
            jx, jy = float(joint[0]) * frame_w, float(joint[1]) * frame_h
            x1, x2 = min(x1, jx), max(x2, jx)
            y1, y2 = min(y1, jy), max(y2, jy)
        margin_x = frame_w * CROP_MARGIN
        margin_y = frame_h * CROP_MARGIN
        x1 = max(0.0, x1 - margin_x)
        y1 = max(0.0, y1 - margin_y)
        x2 = min(frame_w, x2 + margin_x)
        y2 = min(frame_h, y2 + margin_y)
        crop_w = max(2, round(x2 - x1))
        crop_h = max(2, round(y2 - y1))
        crop_x = max(0, min(frame_w - crop_w, round(x1)))
        crop_y = max(0, min(frame_h - crop_h, round(y1)))
        return crop_w, crop_h, crop_x, crop_y
    return None


def verify_ambiguous(candidates: list[dict[str, Any]], *,
                      source_files: dict[str, str],
                      workdir: str | Path | None = None,
                      model: Any = None,
                      extractor: Any = None,
                      memory_root: Path | None = None,
                      progress: Any = None) -> list[dict[str, Any]]:
    """Put the AMBIGUOUS candidates to the local model, one crop each.

    The crop is the region around the hand and the object, at the
    candidate's own hit times, so the model judges the pixels the
    geometry was unsure about - not the whole frame, and not a moment
    the geometry never saw.  A clear candidate is never shown: its
    geometry already cleared the bar.

    Every verdict carries the candidate it belongs to, so a relation
    that was confirmed by a model says which crop was shown and what
    the model said about it.
    """
    from library.tools import span_verification, vision_model

    verdicts: list[dict[str, Any]] = []
    model_id = vision_model.MODEL_ID
    extract = extractor or _extract_crops
    work = Path(workdir) if workdir else Path(tempfile.mkdtemp(
        prefix="ren-relation-verify-"))
    work.mkdir(parents=True, exist_ok=True)
    if model is None:
        model = vision_model.get_model()
    for index, candidate in enumerate(candidates):
        meta = candidate.get("geometry") or {}
        asset_id = meta.get("asset_id")
        source_file = source_files.get(str(asset_id))
        if not source_file:
            verdicts.append({"candidate_id": candidate.get("id"),
                             "answer": "unparsed",
                             "reason": "the asset's media is unavailable",
                             "frames": []})
            continue
        times = _source_seconds(candidate, meta.get("hit_ticks") or [])
        times = times[:MAX_FRAMES_PER_CANDIDATE]
        if not times:
            continue
        statement = statement_for(candidate)
        key = verdict_key(statement, times, model_id)
        recorded = span_verification.read_verdicts(_digest_of(asset_id),
                                                    memory_root)
        verdict = recorded.get(key)
        if verdict is not None:
            verdicts.append({**verdict, "candidate_id": candidate.get("id"),
                             "cached": True})
            if progress is not None:
                progress(index, len(candidates), verdicts[-1])
            continue
        crops = extract(source_file, times, work, index, candidate)
        if not crops:
            verdicts.append({"candidate_id": candidate.get("id"),
                             "answer": "unparsed",
                             "reason": "no crop could be extracted",
                             "frames": times})
            continue
        reply = model.analyze_images(
            [str(path) for path in crops],
            PROMPT.format(n=len(crops), statement=statement,
                          gap=round(_gap_seconds(times), 1)),
            max_tokens=MAX_TOKENS)
        parsed = span_verification.parse_verdict(reply)
        verdict = {**parsed,
                   "candidate_id": candidate.get("id"),
                   "statement": statement,
                   "frames": times,
                   "frame_t": (times[parsed["frame"] - 1]
                               if parsed.get("frame")
                               and 1 <= parsed["frame"] <= len(times) else None),
                   "model": model_id,
                   "prompt_version": PROMPT_VERSION,
                   "reply": reply,
                   "crops": [str(path) for path in crops]}
        recorded[key] = verdict
        span_verification.write_verdicts(_digest_of(asset_id), recorded,
                                         memory_root)
        verdicts.append({**verdict, "cached": False})
        if progress is not None:
            progress(index, len(candidates), verdict)
    return verdicts


def _digest_of(asset_id: Any) -> str:
    """The source digest an asset id carries.

    The M8 verdict store is keyed by the SOURCE, so the asset's id has
    to give its digest back: the asset id is `asset-<digest>`.
    """
    text = str(asset_id or "")
    if text.startswith("asset-"):
        return text.split("-", 1)[1]
    return text


def _gap_seconds(times: list[float]) -> float:
    if len(times) < 2:
        return 0.0
    gaps = [b - a for a, b in pairwise(times)]
    return sum(gaps) / len(gaps)


def _extract_crops(source_file: str, times: list[float], work: Path,
                   index: int,
                   candidate: dict[str, Any] | None = None) -> list[Path]:
    """The crops of one candidate: the frame at each of its times.

    The crop is the region around the measured hand and the measured
    object (`crop_region`), scaled down.  A candidate with no measured
    region is shown whole, and the caller can see that it was.
    """
    out = []
    region = crop_region(candidate)
    for number, moment in enumerate(times):
        path = work / f"candidate-{index:03d}-{number:02d}.jpg"
        if region:
            crop_w, crop_h, crop_x, crop_y = region
            filters = (f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                       "scale=640:-1")
        else:
            filters = "scale=640:-1"
        command = [
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss",
            f"{float(moment):.3f}", "-i", str(source_file),
            "-frames:v", "1", "-vf", filters, str(path),
        ]
        result = subprocess.run(command, capture_output=True,
                                encoding="utf-8", check=False)
        if result.returncode != 0 or not path.is_file():
            continue
        out.append(path)
    return out


def relation_rows(model: dict[str, Any], *, relation_type: str | None = None,
                  object_phrase: str | None = None,
                  person_id: str | None = None,
                  ) -> list[dict[str, Any]]:
    """The model's relation rows, filtered.

    `object_phrase` matches the relation's own object label - the
    phrase the object lane answered - and never a guess.
    """
    rows = []
    wanted = (str(object_phrase).strip().lower()
              if object_phrase else None)
    labels = {row.get("id"): row.get("label")
              for row in model.get("entities") or []
              if isinstance(row, dict)}
    for row in model.get("relations") or []:
        if not isinstance(row, dict):
            continue
        if relation_type and row.get("predicate") != relation_type:
            continue
        if person_id and row.get("subject_id") != person_id:
            continue
        if wanted:
            # The object a relation is about is named by the ENTITY it
            # names: the relation row carries no phrase of its own.
            label = str(labels.get(row.get("object_id")) or "").strip().lower()
            if label != wanted:
                continue
        rows.append(row)
    return rows


def object_entity_id(asset_id: str, phrase: str) -> str:
    """The entity id an object phrase carries on one asset.

    The object lane answers a QUESTION about a name; the model holds an
    ENTITY for the thing that answered it.  The id names the asset, so
    two assets' "pen" are two things.
    """
    suffix = str(asset_id or "").split("-", 1)[-1][:12] or "asset"
    return f"object-{_slug(phrase)}-{suffix}"


def file_relations(project: Any, model: dict[str, Any],
                   rows: dict[str, Any], *,
                   reason: str = "verified relational understanding",
                   ) -> dict[str, Any]:
    """File the relation rows - candidates, confirmed relations and the
    interpretations that record what confirmed them - in the model.

    A candidate is filed as a candidate and a confirmed relation as a
    confirmed relation: the two are different rows, and a reader of the
    model can tell them apart.  The object the relation is about gets
    its entity, because a relation names two participants and the model
    holds no entity for a thing nobody asked about yet.
    """
    from library.tools import project_model_store

    project = Path(project)
    revision, _current = project_model_store.load_project_model(project)
    known = {row.get("id") for row in model.get("entities") or []
             if isinstance(row, dict)}
    changes: list[dict[str, Any]] = []
    for entity in rows.get("entities") or []:
        if entity["id"] in known:
            continue
        changes.append({"collection": "entities", "id": entity["id"],
                        "operation": "append", "record": entity})
        known.add(entity["id"])
    for relation in rows.get("relations") or []:
        changes.append({"collection": "relations", "id": relation["id"],
                        "operation": "append", "record": relation})
    for interpretation in rows.get("interpretations") or []:
        changes.append({"collection": "interpretations",
                        "id": interpretation["id"],
                        "operation": "append", "record": interpretation})
    if not changes:
        return {"revision": revision, "filed": 0, "reason": "nothing to file"}
    new_revision = project_model_store.commit_project_model_records(
        project, changes, expected_revision=revision, reason=reason)
    return {"revision": new_revision, "filed": len(changes),
            "reason": reason}


def main(argv: list[str] | None = None) -> int:
    """Derive, verify and file the relations a project's evidence holds.

        python3 -m library.tools.relation_verification <project> \
            --object pen --at 80-100 --verify
    """
    import argparse
    import json

    from library.tools.project_model_cli import resolve_project_root

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.relation_verification",
        description=("Derive time-bounded relation candidates from the "
                     "measured hands and the measured objects, verify the "
                     "ambiguous ones with the local model, and file the "
                     "candidates and the confirmed relations."))
    parser.add_argument("project")
    parser.add_argument(
        "--object", action="append", default=[],
        help="the object phrase to relate a person to (repeatable)")
    parser.add_argument(
        "--at", default="",
        help="the window to read, as START-END seconds")
    parser.add_argument(
        "--verify", action="store_true",
        help="put the ambiguous candidates to the local model")
    parser.add_argument(
        "--no-record", action="store_true",
        help="derive and report without filing the rows in the model")
    parser.add_argument("--memory-root", default=None)
    args = parser.parse_args(argv)

    from library.tools import project_model_store

    project = resolve_project_root(args.project)
    _revision, model = project_model_store.load_project_model(project)
    candidates = derive_candidates(model)
    phrases = {str(phrase).strip().lower() for phrase in args.object}
    if phrases:
        candidates = [candidate for candidate in candidates
                      if str((candidate.get("geometry") or {}).get(
                          "object_phrase") or "").strip().lower() in phrases]
    window = _window(args.at)
    if window:
        start, end = window
        candidates = [candidate for candidate in candidates
                      if candidate["interval"]["end"] > start
                      and candidate["interval"]["start"] < end]
    verdicts: list[dict[str, Any]] = []
    if args.verify:
        assets = {row.get("id"): row.get("uri")
                  for row in model.get("assets") or []
                  if isinstance(row, dict)}
        verdicts = verify_ambiguous(
            ambiguous_candidates(candidates),
            source_files={key: value for key, value in assets.items()
                          if isinstance(value, str)})
    confirmed = confirmed_relations(candidates, verdicts)
    rejected = rejected_candidates(candidates, verdicts)
    rows = rows_to_file(candidates, confirmed, rejected)
    filed = {"revision": None, "filed": 0, "reason": "recording was declined"}
    if not args.no_record:
        filed = file_relations(project, model, rows)
    print(json.dumps({
        "project": str(project),
        "candidates": len(candidates),
        "ambiguous": len(ambiguous_candidates(candidates)),
        "confirmed": len(confirmed),
        "rejected": len(rejected),
        "relations": rows["details"],
        "verdicts": verdicts,
        "filed": filed,
    }, indent=2, default=str))
    return 0


def _window(at: str) -> tuple[int, int] | None:
    """The window the request asked for, in ticks."""
    if not at:
        return None
    parts = str(at).replace(":", "-").split("-")
    if len(parts) != 2:
        raise SystemExit("--at takes START-END seconds")
    return (int(float(parts[0]) * 1000), int(float(parts[1]) * 1000))


RELATION_FIELDS = ("id", "subject_id", "predicate", "object_id", "interval",
                   "confidence", "provenance_id", "evidence_ids", "status",
                   "certainty", "relation_class")
"""The fields the Project Model's relation row carries, and no others.

The schema denies unknown fields, so a relation cannot carry the
candidate's geometry or the verdict that confirmed it.  The geometry
lives on the candidate until it is filed, and the VERDICT lives in an
interpretation row that names the relation it is about."""


def conforming_relation(row: dict[str, Any]) -> dict[str, Any]:
    """One relation row, with only the fields the schema allows."""
    out = {key: row.get(key) for key in RELATION_FIELDS
           if key in row and row.get(key) is not None}
    out.setdefault("confidence", None)
    if out.get("provenance_id") is None:
        out["provenance_id"] = ""
    return out


def confirmation_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The interpretation rows that record what confirmed a relation.

    The relation row says a relation is VERIFIED; the interpretation row
    says WHY: which crop was shown, what the model answered, and which
    basis confirmed it.  A reader of the model can therefore tell a
    relation the geometry settled from one the model judged.
    """
    out = []
    for row in rows:
        confirmation = row.get("confirmation")
        if not confirmation:
            continue
        out.append({
            "id": f"interp-relation-confirmation-{row['id']}",
            "kind": "relation_confirmation",
            "author": "library.tools.relation_verification",
            "objective": None,
            "policy_id": None,
            "evidence_ids": list(row.get("evidence_ids") or []),
            "scope": {
                "relation_id": row["id"],
                "predicate": row.get("predicate"),
                "basis": confirmation.get("basis"),
                "answer": confirmation.get("answer"),
                "reason": confirmation.get("reason"),
                "frames": confirmation.get("frames"),
                "frame_t": confirmation.get("frame_t"),
                "model": confirmation.get("model"),
                "prompt_version": confirmation.get("prompt_version"),
            },
            "uncertainty": None,
            "certainty": "interpreted",
        })
    return out


def rows_to_file(candidates: list[dict[str, Any]],
                 confirmed: list[dict[str, Any]],
                 rejected: list[dict[str, Any]],
                 *, provenance_id: str = "") -> dict[str, Any]:
    """The rows to file: every candidate, every confirmed relation, and
    the interpretation that says what confirmed it.

    A confirmed relation is a DIFFERENT row from the candidate it came
    from: the candidate keeps its own id, and the relation carries its
    own - so the model holds both and a reader can tell them apart.
    """
    rejected_by_id = {row["id"]: row for row in rejected}
    relations: list[dict[str, Any]] = []
    interpretations: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    for candidate in candidates:
        geometry = candidate.get("geometry") or {}
        object_id = object_entity_id(geometry.get("asset_id") or "",
                                     geometry.get("object_phrase") or "object")
        row = {**candidate, "object_id": object_id,
               "provenance_id": provenance_id or candidate.get("provenance_id")}
        if candidate["id"] in rejected_by_id:
            row = {**row, "verification":
                   rejected_by_id[candidate["id"]]["verification"]}
        relations.append(row)
    for relation in confirmed:
        geometry = relation.get("geometry") or {}
        object_id = object_entity_id(geometry.get("asset_id") or "",
                                     geometry.get("object_phrase") or "object")
        relations.append({**relation,
                          "id": relation["id"].replace("relation-candidate-",
                                                       "relation-"),
                          "object_id": object_id,
                          "provenance_id": provenance_id
                          or relation.get("provenance_id")})
    for relation in relations:
        geometry = relation.get("geometry") or {}
        object_id = relation.get("object_id")
        if object_id and not any(row["id"] == object_id for row in entities):
            entities.append({"id": object_id, "kind": "object",
                             "label": geometry.get("object_phrase"),
                             "object_class_id": None,
                             "identity_scope": "asset"})
    interpretations.extend(confirmation_rows(relations))
    return {
        "relations": [conforming_relation(row) for row in relations],
        "interpretations": interpretations,
        "entities": entities,
        "details": [{"id": row["id"], "status": row.get("status"),
                     "geometry": row.get("geometry"),
                     "confirmation": row.get("confirmation"),
                     "verification": row.get("verification")}
                    for row in relations],
    }


if __name__ == "__main__":
    raise SystemExit(main())
