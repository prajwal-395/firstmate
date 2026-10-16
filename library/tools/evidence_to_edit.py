"""EVAL-04 first-slice runner: evidence -> plan -> edit graph -> reviewable output.

This module executes the evidence-to-edit chain without Resolve, model
passes, or media reads. It consults the Project Model through the real
evidence APIs - typed evidence queries, bounded acquisition planning,
and edit-point signals - and records which evidence justified each
editorial decision. Anything the evidence does not support is reported
as absent work, never invented: a run whose evidence is missing ends
with explicitly incomplete stages and missing metrics rather than
guessed cuts, zero costs, or fabricated human scores.

The acceptance gate stays in
:mod:`library.tools.editorial_task_benchmark`: this runner assembles a
report in that shape and refuses to fill fields it did not measure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

from library.tools import (
    edit_graph,
    editorial_task_benchmark,
    evidence_query,
    query_directed_acquisition,
)
from library.tools.edit_point_planner import generate_edit_point_candidates

EVIDENCE_SOURCES = ("project_model", "evidence_query", "bounded_acquisition",
                    "edit_point_signals")
"""The evidence Ren must proactively consult, in consultation order."""

GRAPH_STAGES = ("evidence", "plan", "edit_graph", "render", "verify")
"""The end-to-end stages a run reports on."""

DIALOGUE_PLANS = (
    ("speech_attributed", [
        {"predicate": "measurement", "measurement_type": "speech.word"},
        {"predicate": "measurement", "measurement_type": "person.voice_turn"},
    ]),
    ("multicam_sync", [
        {"predicate": "measurement", "measurement_type": "sync.multicam_offset"},
    ]),
    ("speaker_camera", [
        {"predicate": "measurement", "measurement_type": "event.speaking_span"},
        {"predicate": "measurement", "measurement_type": "person.face_frame"},
    ]),
    ("scene_structure", [
        {"predicate": "measurement", "measurement_type": "video.scene_boundary"},
    ]),
    ("sound_energy", [
        {"predicate": "measurement", "measurement_type": "audio.sound_event"},
    ]),
)
"""Query plans behind a dialogue/multicam cut: attributed speech, sync,
scene structure, and sound energy. Only measured speech words can still
place speech blocks; scene and sound rows corroborate the span."""

VISUAL_ACTION_PLANS = (
    # These typed queries only answer verified geometric candidates;
    # unverified event candidates remain hypotheses and cannot ground a cut.
    ("action_drink_from_cup", [
        {"predicate": "event_span", "event": "drink_from_cup"},
    ]),
    ("action_pick_up_put_down", [
        {"predicate": "event_span", "event": "pick_up_put_down"},
    ]),
)
VISUAL_PLANS = (
    ("objects", [
        {"predicate": "measurement", "measurement_type": "object.detection"},
        {"predicate": "measurement", "measurement_type": "object.visibility"},
    ]),
    ("agents", [
        {"predicate": "measurement", "measurement_type": "person.face_frame"},
        {"predicate": "event_span", "event": "on_screen"},
    ]),
    ("scene_structure", [
        {"predicate": "measurement", "measurement_type": "video.scene_boundary"},
    ]),
    ("sound_energy", [
        {"predicate": "measurement", "measurement_type": "audio.sound_event"},
    ]),
)
"""Query plans behind a visual action/object cut: objects, who is seen,
scene structure, and sound energy. Scene and sound rows are measured
without model passes, so visual action beats can ground on them."""

PLANS_BY_TASK_TYPE = {
    "dialogue_multicam": DIALOGUE_PLANS,
    "visual_action": VISUAL_ACTION_PLANS + VISUAL_PLANS,
    "visual_object": VISUAL_PLANS,
}

# Evidence that can justify proposing a source interval for each task type.
# Corroborating measurements (scene boundaries, sound energy, sync) remain
# useful facts, but cannot stand in for the subject of the edit.
REQUIRED_SELECTION_PLANS = {
    "dialogue_multicam": frozenset({"speech_attributed"}),
    "visual_action": frozenset({"action_drink_from_cup",
                                 "action_pick_up_put_down"}),
    "visual_object": frozenset({"objects"}),
}

# Pinned scorer artefacts the runner consumes when they exist. EVAL-02/03
# scorers have not landed on this base, so the check below reports them
# pending rather than substituting guessed values.
EXTERNAL_SCORER_GLOBS = {
    "EVAL-02": ("docs/benchmarks/*eval02*.md",
                "library/tools/visual_retrieval_benchmark.py"),
    "EVAL-03": ("docs/benchmarks/*eval03*.md",
                "library/tools/temporal_event_benchmark.py"),
}


def load_brief(path: Path) -> dict[str, Any]:
    """Read one frozen brief file."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validate_briefs(briefs: list[dict[str, Any]]) -> list[str]:
    """Return structural errors for frozen briefs, in task order."""
    errors: list[str] = []
    for index, task in enumerate(briefs):
        task_name = task.get("id", f"tasks[{index}]")
        errors.extend(editorial_task_benchmark.validate_brief(
            task_name, task.get("brief")))
    return errors


def check_brief_windows(brief: dict[str, Any],
                        source_durations: dict[str, float]) -> list[str]:
    """Refuse tasking windows the corpus manifest cannot contain.

    Brief windows are editorial tasking, not measurements; the one thing
    they must not do is exceed the source duration the EVAL-01 manifest
    measured. A window past the end of its source is a tasking error,
    reported here rather than silently clipped downstream.
    """
    errors: list[str] = []
    task_name = brief.get("id", "brief")
    for row in (brief.get("brief") or {}).get(
            "expected_source_moments", []) + (brief.get("brief") or {}).get(
            "cut_ranges", []):
        source_id = row.get("source_id")
        duration = source_durations.get(source_id)
        if duration is None:
            errors.append(f"{task_name}: source {source_id!r} has no "
                          f"measured duration in the corpus manifest")
        elif row.get("end_seconds", 0) > duration:
            errors.append(f"{task_name}: window {row.get('start_seconds')}-"
                          f"{row.get('end_seconds')}s exceeds measured "
                          f"source duration {duration}s for {source_id}")
    return errors


def _ledger_row(decision: str, rationale: str, evidence_ids: list[str],
                evidence_source: str) -> dict[str, Any]:
    return {"decision": decision, "rationale": rationale,
            "evidence_ids": evidence_ids, "evidence_source": evidence_source}


def _evidence_revision(model: dict[str, Any]) -> str:
    """Identify the exact model snapshot used when no store revision exists."""
    if model.get("revision") is not None:
        return str(model["revision"])
    encoded = json.dumps(model, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def consult_model(model: dict[str, Any]) -> dict[str, Any]:
    """Record the Project Model consultation itself as evidence.

    The model dict is read, never written: the row names the model id and
    revision the run decided from, so a later replay can reconstruct the
    same evidence.
    """
    return _ledger_row(
        f"consult Project Model {model.get('id', 'unidentified')}",
        f"revision {model.get('revision', 'unknown')}: "
        f"{len(model.get('observations', []))} observations, "
        f"{len(model.get('signals', []))} signals available to the run",
        [f"project_model:{model.get('id', 'unidentified')}"],
        "project_model")


def _scope_for(model: dict[str, Any],
               moment: dict[str, Any]) -> dict[str, Any]:
    """The query scope for one moment, clock-pinned when the model allows.

    An unscoped window reads every source clock in the model, so a query
    about one take would answer with another take's evidence. When the
    model names the moment source's clock, the scope pins to it; models
    without clock mappings keep the unscoped window.
    """
    scope: dict[str, Any] = {"start_s": moment["start_seconds"],
                             "end_s": moment["end_seconds"]}
    source_id = moment["source_id"]
    asset_ids = set()
    for asset in model.get("assets") or []:
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
            continue
        metadata = asset.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        source_ids = metadata.get("source_ids") or []
        clip_ids = metadata.get("clip_ids") or []
        if (asset["id"] == source_id
                or Path(str(asset.get("uri") or "")).stem == source_id
                or source_id in source_ids
                or source_id in clip_ids):
            asset_ids.add(asset["id"])
    clocks = []
    for clock in model.get("clocks") or []:
        if not isinstance(clock, dict):
            continue
        domain = clock.get("domain") or {}
        if (domain.get("asset_id") == source_id
                or domain.get("asset_id") in asset_ids):
            clocks.append(clock)
    # A source can expose more than one clock. Pin only when the model
    # identifies one unambiguously; pinning the asset still excludes every
    # other take from the query.
    if len(asset_ids) == 1:
        scope["asset_id"] = next(iter(asset_ids))
    if len(clocks) == 1 and clocks[0].get("id"):
        scope["clock_id"] = clocks[0]["id"]
    return scope


def consult_moment(model: dict[str, Any], moment: dict[str, Any],
                   task_types: list[str], *,
                   acquisition_budget_seconds: float = 0.0,
                   duration_seconds: float,
                   evidence_revision: str | None = None
                   ) -> tuple[list[dict[str, Any]],
                                                    dict[str, Any]]:
    """Query the evidence behind one expected moment and plan acquisition.

    Returns the ledger rows and a per-moment answer summary. Queries run
    against the supplied model only; acquisition is PLANNED
    (:func:`plan_query_acquisition` is pure) and never executed here, so
    the first slice spends nothing. A zero budget records the deferral
    explicitly instead of pretending the evidence was acquired.
    """
    rows: list[dict[str, Any]] = []
    evidence_revision = evidence_revision or _evidence_revision(model)
    present_intervals: list[dict[str, Any]] = []
    statuses: dict[str, str] = {}
    observations = {str(obs.get("id")): obs
                    for obs in model.get("observations", [])}
    plans = [plan for task_type in task_types
             for plan in PLANS_BY_TASK_TYPE.get(task_type, ())]
    seen: set[str] = set()
    for plan_name, predicates in plans:
        if plan_name in seen:
            continue
        seen.add(plan_name)
        scope = _scope_for(model, moment)
        plan = evidence_query.compile_plan(list(predicates), scope=scope)
        answer = evidence_query.evidence_for(model, plan)
        query_result_id = "evidence-query:" + hashlib.sha256(
            json.dumps({"revision": evidence_revision, "plan": plan,
                        "answer": answer}, sort_keys=True,
                       separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24]
        statuses[plan_name] = answer["status"]
        results = answer.get("results", [])
        evidence_ids = sorted({evidence_id for row in results
                               for evidence_id in row.get("evidence_ids", [])})
        # Empty queries still have an addressable result record. Keep that
        # query reference in the decision ledger; it is never promoted to an
        # EditOperation observation link.
        ledger_evidence_ids = evidence_ids or [query_result_id]
        detail = "; ".join(answer.get("reasons", [])) or \
            f"{len(results)} joined interval(s)"
        rows.append(_ledger_row(
            f"query {plan_name} over {moment['source_id']} "
            f"{scope['start_s']}-{scope['end_s']}s",
            f"{answer['status']}: {detail}",
            ledger_evidence_ids, "evidence_query") | {
                "query_result_id": query_result_id,
                "evidence_revision": evidence_revision})
        for row in results:
            interval = row.get("interval", {})
            measurements = []
            for evidence_id in row.get("evidence_ids", []):
                observation = observations.get(str(evidence_id))
                if observation is None:
                    continue
                native = (observation.get("payload") or {}).get(
                    "evidence_contract") or {}
                measurements.append({
                    "evidence_id": str(evidence_id),
                    "asset_id": observation.get("asset_id"),
                    "measurement_type": native.get("measurement_type"),
                    "value": native.get("value"),
                    "interval": observation.get("interval"),
                    "provenance_id": observation.get("provenance_id"),
                })
            present_intervals.append({
                "plan": plan_name,
                "clock_id": interval.get("clock_id"),
                "start_seconds": interval.get("start_s", scope["start_s"]),
                "end_seconds": interval.get("end_s", scope["end_s"]),
                "evidence_ids": row.get("evidence_ids", []),
                "query_result_id": query_result_id,
                "evidence_revision": evidence_revision,
                "measurements": measurements,
                "coverage": row.get("coverage", []),
                "freshness": row.get("freshness", []),
            })
        acquisition = query_directed_acquisition.plan_query_acquisition(
            answer, plan, source_digest=moment["source_id"],
            duration_seconds=duration_seconds,
            producer_budgets={lane: 90.0
                              for lane in plan.get("required_lanes", ())},
            max_cost_seconds=acquisition_budget_seconds)
        rows.append(_ledger_row(
            f"bounded acquisition for {plan_name}: {acquisition['action']}",
            acquisition.get("reason", "acquisition step planned") +
            (f"; estimated {acquisition.get('estimated_cost_seconds')}s"
             if "estimated_cost_seconds" in acquisition else ""),
            [f"acquisition:{plan_name}:{acquisition['action']}",
             f"project_model:{model.get('id', 'unidentified')}"],
            "bounded_acquisition"))
    # M10 owns these relation candidates. Carry them through as read-only
    # hypotheses so the editor can suggest an angle without treating LR-ASD
    # output as a verified speaker-to-camera mapping.
    voice_observation_ids = {
        row["evidence_id"]
        for item in present_intervals if item["plan"] == "speech_attributed"
        for row in item["measurements"]
        if row["measurement_type"] == "person.voice_turn"
    }
    voice_entities = {
        link["entity_id"] for link in model.get("identity_links", [])
        if link.get("observation_id") in voice_observation_ids
        and isinstance(link.get("entity_id"), str)
    }
    voice_track_by_entity = {
        link["entity_id"]: measurement["value"]["voice_track"]
        for item in present_intervals if item["plan"] == "speech_attributed"
        for measurement in item["measurements"]
        if measurement["measurement_type"] == "person.voice_turn"
        and isinstance(measurement.get("value"), dict)
        and isinstance(measurement["value"].get("voice_track"), str)
        for link in model.get("identity_links", [])
        if link.get("observation_id") == measurement["evidence_id"]
        and isinstance(link.get("entity_id"), str)
    }
    speaker_camera_candidates = [
        relation for relation in model.get("relations", [])
        if relation.get("predicate") == "speaking_person_candidate"
        and relation.get("status") == "candidate"
        and relation.get("subject_id") in voice_entities
    ]
    return rows, {"statuses": statuses, "present_intervals": present_intervals,
                  "speaker_camera_candidates": speaker_camera_candidates,
                  "speaker_track_by_entity": voice_track_by_entity,
                  "clocks": model.get("clocks", []),
                  "assets": model.get("assets", [])}


def consult_edit_points(model: dict[str, Any]) -> tuple[list[dict[str, Any]],
                                                       list[dict[str, Any]]]:
    """Derive edit-point candidates from measured signals.

    Candidates suggest cut positions; they never place cuts. A model with
    no analysed signals yields no candidates and says so in the ledger.
    """
    model_with_signals = dict(model)
    model_with_signals.setdefault("signals", [])
    candidates = generate_edit_point_candidates(model_with_signals)
    supporting = []
    for candidate in candidates:
        ids = candidate.get("measurement_ids") or []
        supporting.append(ids[0] if ids else "signal:none")
    row = _ledger_row(
        f"derive edit-point candidates from {len(model_with_signals['signals'])} "
        f"signal(s)",
        f"{len(candidates)} candidate(s); each names its supporting "
        f"measurement ids" if candidates else
        "no analysed signals, so no candidates; cuts cannot be "
        "signal-grounded on this model",
        supporting or [f"project_model:{model.get('id', 'unidentified')}"],
        "edit_point_signals")
    return [row], candidates


def _tick_seconds(point: dict[str, Any]) -> float | None:
    """An edit-point candidate as timeline seconds.

    Candidates carry millisecond source ticks on their own clock
    (:data:`evidence_query.TICKS_PER_SECOND`); a candidate that already
    carries ``tick_s`` names seconds directly. ``None`` means the point
    has no usable position and can ground nothing.
    """
    if isinstance(point.get("tick_s"), (int, float)):
        return float(point["tick_s"])
    tick = point.get("tick")
    if isinstance(tick, bool) or not isinstance(tick, (int, float)):
        return None
    return float(tick) / evidence_query.TICKS_PER_SECOND


def _dialogue_exchange(answer: dict[str, Any], start: float, end: float
                       ) -> dict[str, Any] | None:
    """Return one measured, alternating two-turn exchange in the brief.

    A word overlap alone is not a dialogue edit. Both turns must have
    explicit speaker ids and queried word evidence; otherwise the caller
    abstains instead of emitting an isolated word or an ungrounded angle.
    """
    observations: dict[str, dict[str, Any]] = {}
    for present in answer.get("present_intervals", []):
        if present["plan"] != "speech_attributed":
            continue
        for measurement in present["measurements"]:
            if measurement["measurement_type"] not in {
                    "speech.word", "person.voice_turn"}:
                continue
            observations[measurement["evidence_id"]] = measurement

    turns = []
    for evidence_id, measurement in observations.items():
        if measurement["measurement_type"] != "person.voice_turn":
            continue
        value = measurement["value"]
        interval = measurement["interval"]
        if not isinstance(value, dict) or not isinstance(
                value.get("voice_track"), str):
            continue
        turn_start = interval["start"] / evidence_query.TICKS_PER_SECOND
        turn_end = interval["end"] / evidence_query.TICKS_PER_SECOND
        if turn_end <= start or turn_start >= end:
            continue
        turns.append({"id": evidence_id, "speaker": value["voice_track"],
                      "start": max(start, turn_start),
                      "end": min(end, turn_end),
                      "clock_id": interval.get("clock_id")})
    turns.sort(key=lambda row: (row["start"], row["end"], row["speaker"]))

    exchanges = []
    for question, response in pairwise(turns):
        gap = response["start"] - question["end"]
        if (question["speaker"] == response["speaker"] or gap < 0
                or gap > 12.0):
            continue
        members = [question, response]
        evidence = []
        for evidence_id, measurement in observations.items():
            interval = measurement["interval"]
            item_start = interval["start"] / evidence_query.TICKS_PER_SECOND
            item_end = interval["end"] / evidence_query.TICKS_PER_SECOND
            if measurement["measurement_type"] == "speech.word":
                if any(item_start < turn["end"] and item_end > turn["start"]
                       for turn in members):
                    evidence.append(measurement)
            elif evidence_id in {turn["id"] for turn in members}:
                evidence.append(measurement)
        if not any(row["measurement_type"] == "speech.word"
                   for row in evidence):
            continue
        exchanges.append({"start_seconds": question["start"],
                          "end_seconds": response["end"],
                          "evidence": evidence,
                          "speakers": [question["speaker"],
                                       response["speaker"]],
                          "turns": [question, response]})
    if not exchanges:
        return None
    exchanges.sort(key=lambda row: (
        -(row["end_seconds"] - row["start_seconds"]),
        row["start_seconds"]))
    return exchanges[0]


def _speaker_camera_mapping(answer: dict[str, Any],
                            speakers: list[str]) -> dict[str, Any]:
    """Map speakers to a camera only when M7 links voice to visible face.

    A co-occurring face is insufficient: the speaking-span measurement
    must carry both the voice and face-track identities, and a measured
    face frame must overlap that span on the same camera asset.
    """
    evidence: dict[str, dict[str, Any]] = {}
    for present in answer.get("present_intervals", []):
        if present["plan"] != "speaker_camera":
            continue
        rows = present["measurements"]
        faces = [row for row in rows
                 if row["measurement_type"] == "person.face_frame"]
        for row in rows:
            if row["measurement_type"] != "event.speaking_span":
                continue
            value = row["value"]
            if not isinstance(value, dict) or not value.get("face_track"):
                continue
            interval = row["interval"]
            face_evidence = [
                face for face in faces
                if face["asset_id"] == row["asset_id"]
                and face["interval"]["clock_id"] == interval["clock_id"]
                and face["interval"]["start"] < interval["end"]
                and face["interval"]["end"] > interval["start"]]
            if not face_evidence:
                continue
            voice_track = value.get("voice_track")
            if voice_track in speakers:
                evidence.setdefault(voice_track, {})[
                    str(row["asset_id"])] = {
                        "face_track": value["face_track"],
                        "evidence_ids": [row["evidence_id"], *[
                            face["evidence_id"] for face in face_evidence]]}
    if all(speaker in evidence for speaker in speakers):
        return {"status": "mapped", "speaker_to_camera": {
            speaker: evidence[speaker] for speaker in speakers}}

    candidates = answer.get("speaker_camera_candidates", [])
    candidate_rows = []
    for relation in candidates:
        interval = relation["interval"]
        clock = next((row for row in answer.get("clocks", [])
                      if row["id"] == interval["clock_id"]), None)
        asset_id = (clock or {}).get("domain", {}).get("asset_id")
        asset = next((row for row in answer.get("assets", [])
                      if row["id"] == asset_id), None)
        candidate_rows.append({
            "speaker_entity_id": relation["subject_id"],
            "speaker_track_id": answer.get("speaker_track_by_entity", {}).get(
                relation["subject_id"]),
            "visible_person_entity_id": relation["object_id"],
            "camera_asset_id": asset_id,
            "camera_uri": (asset or {}).get("uri"),
            "source_clock_id": interval["clock_id"],
            "source_start": interval["start"],
            "source_end": interval["end"],
            "candidate_status": relation["status"],
            "raw_lr_asd_score": relation["confidence"],
            "evidence_ids": relation["evidence_ids"],
        })
    if candidate_rows:
        return {"status": "candidate", "speaker_to_camera_candidates":
                candidate_rows,
                "reason": "M10 LR-ASD candidates are suggestions, not "
                          "verified speaker-to-camera mappings"}
    return {"status": "abstained",
            "reason": "no measured voice-to-visible-face mapping or "
                      "speaker-to-camera candidate for every exchange speaker"}


# ── Multicamera framing, switch planning, continuity, scoring (F06) ────
#
# The shared source-time reference arrives through clock-pinned query
# scopes and M6 offsets (PR 2610); speaker-to-angle mapping arrives
# through the face-backed check above or read-only M10 LR-ASD
# candidates. What remains is everything a switch decision needs:
# what each camera shows, when a switch is word-safe, whether the
# cut preserves continuity, and how the plan scores against labels.

MIN_FRAMING_SAMPLES = 4
"""Face samples per camera in the span before composition is measured."""

FACE_CLOSE_WIDTH = 0.12
FACE_MEDIUM_WIDTH = 0.04
"""Provisional shot-scale boundaries on median face width as a fraction
of the image width (close >= 0.12, medium >= 0.04, else wide). Set from
detector geometry, not from footage: the blind camera-identity
packet (blind-packet.md) carries the wide/medium/close labels these
calibrate against when they land."""

MIN_SWITCH_GAP_S = 0.12
"""A switch window narrower than this cannot hold a cut without
risking a clipped word edge, so the planner emits no_switch."""

SWITCH_SCORE_TOLERANCE_S = 0.5
"""A predicted speaker change within this of the reference change
counts as a timing hit; the absolute error is still reported."""


def _face_geometry(value: Any) -> tuple[float, float] | None:
    """Median-ready (width, centre-x) from one face_frame value.

    The value is ``{"face_index", "face", "hands"}``; ``face`` is None
    for a measured absence and otherwise carries a normalized
    ``box`` [x1, y1, x2, y2]. Anything else is not a measurement.
    """
    if not isinstance(value, dict):
        return None
    face = value.get("face")
    if not isinstance(face, dict):
        return None
    box = face.get("box")
    if (not isinstance(box, (list, tuple)) or len(box) != 4
            or not all(isinstance(edge, (int, float))
                       and not isinstance(edge, bool) for edge in box)):
        return None
    x1, _, x2, _ = (float(edge) for edge in box)
    if not 0.0 <= x1 < x2 <= 1.0:
        return None
    return (x2 - x1, (x1 + x2) / 2.0)


def _face_scale(width: float) -> str:
    if width >= FACE_CLOSE_WIDTH:
        return "close"
    if width >= FACE_MEDIUM_WIDTH:
        return "medium"
    return "wide"


def _frame_side(position: float) -> str:
    from library.tools.subject_framing import CENTRE_DEADBAND

    if position < 0.5 - CENTRE_DEADBAND:
        return "left"
    if position > 0.5 + CENTRE_DEADBAND:
        return "right"
    return "centre"


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def measure_camera_framing(answer: dict[str, Any], start_s: float,
                           end_s: float) -> dict[str, Any]:
    """Measure shot composition per camera over one grounded span.

    Reads only queried ``person.face_frame`` measurements overlapping
    the span. A camera with too few samples is ``unknown``, never
    assigned a scale; zero faces on enough samples is a measured
    ``no_face`` scale (an empty chair or an off-screen speaker), not
    an inference about what is there.
    """
    samples: dict[str, list[dict[str, Any]]] = {}
    for present in answer.get("present_intervals", []):
        if present.get("plan") != "speaker_camera":
            continue
        for row in present.get("measurements", []):
            if row.get("measurement_type") != "person.face_frame":
                continue
            if row.get("asset_id") is None:
                continue
            interval = row.get("interval") or {}
            item_start = interval.get("start")
            item_end = interval.get("end")
            if not isinstance(item_start, (int, float)) or not isinstance(
                    item_end, (int, float)):
                continue
            item_start /= evidence_query.TICKS_PER_SECOND
            item_end /= evidence_query.TICKS_PER_SECOND
            if item_end <= start_s or item_start >= end_s:
                continue
            samples.setdefault(str(row.get("asset_id")), []).append(row)
    cameras: dict[str, Any] = {}
    evidence_ids: list[str] = []
    face_rows: list[dict[str, Any]] = []
    for asset_id, rows in sorted(samples.items()):
        row_ids = [str(row.get("evidence_id")) for row in rows]
        evidence_ids.extend(row_ids)
        face_rows.extend(rows)
        if len(rows) < MIN_FRAMING_SAMPLES:
            cameras[asset_id] = {
                "status": "unknown",
                "reason": f"only {len(rows)} face sample(s) in the span, "
                          f"need {MIN_FRAMING_SAMPLES}",
                "sample_count": len(rows),
                "evidence_ids": row_ids}
            continue
        geometries = [_face_geometry(row.get("value")) for row in rows]
        present = [geometry for geometry in geometries
                   if geometry is not None]
        fraction = len(present) / len(rows)
        if not present:
            cameras[asset_id] = {
                "status": "measured", "face_scale": "no_face",
                "subject_position": "unclear",
                "sample_count": len(rows), "face_fraction": 0.0,
                "median_face_width": None, "median_center_x": None,
                "evidence_ids": row_ids}
            continue
        widths = [geometry[0] for geometry in present]
        centres = [geometry[1] for geometry in present]
        median_width = _median(widths)
        median_centre = _median(centres)
        cameras[asset_id] = {
            "status": "measured", "face_scale": _face_scale(median_width),
            "subject_position": _frame_side(median_centre),
            "sample_count": len(rows),
            "face_fraction": round(fraction, 4),
            "median_face_width": round(median_width, 4),
            "median_center_x": round(median_centre, 4),
            "evidence_ids": row_ids}
    return {"cameras": cameras,
            "evidence_ids": sorted(set(evidence_ids)),
            "measurements": face_rows}


def framing_compatibility(
        framing: dict[str, Any]) -> dict[str, Any]:
    """Pairwise framing verdicts over measured cameras.

    ``compatible`` needs two measured single views that differ in
    scale or side - the shot/reverse-shot a dialogue switch wants.
    ``same_framing`` is compatible but unmotivated: the switch shows
    the speaker without changing the picture. ``incompatible``
    refuses: the target angle shows no face in the span. Anything
    unmeasured is ``unknown``.
    """
    cameras = framing.get("cameras", {})
    measured = {asset: row for asset, row in cameras.items()
                if row.get("status") == "measured"}
    pairs = []
    assets = sorted(cameras)
    for index, asset_a in enumerate(assets):
        for asset_b in assets[index + 1:]:
            row_a = cameras[asset_a]
            row_b = cameras[asset_b]
            if asset_a not in measured or asset_b not in measured:
                missing = [asset for asset in (asset_a, asset_b)
                           if asset not in measured]
                pairs.append({
                    "camera_a": asset_a, "camera_b": asset_b,
                    "verdict": "unknown",
                    "reason": f"unmeasured framing on {', '.join(missing)}",
                    "evidence_ids": []})
                continue
            if row_a.get("face_scale") == "no_face" or row_b.get(
                    "face_scale") == "no_face":
                empty = [asset for asset, row in
                         ((asset_a, row_a), (asset_b, row_b))
                         if row.get("face_scale") == "no_face"]
                pairs.append({
                    "camera_a": asset_a, "camera_b": asset_b,
                    "verdict": "incompatible",
                    "reason": f"{', '.join(empty)} shows no face in "
                              f"the span: nothing to cut to",
                    "evidence_ids": sorted(set(
                        row_a.get("evidence_ids", [])
                        + row_b.get("evidence_ids", [])))})
                continue
            if (row_a.get("face_scale") == row_b.get("face_scale")
                    and row_a.get("subject_position") == row_b.get(
                        "subject_position")):
                verdict = "same_framing"
                reason = ("both angles show a "
                          f"{row_a.get('face_scale')} "
                          f"{row_a.get('subject_position')} subject: "
                          f"a switch changes nothing in the picture")
            else:
                verdict = "compatible"
                reason = (f"{asset_a} {row_a.get('face_scale')}/"
                          f"{row_a.get('subject_position')} against "
                          f"{asset_b} {row_b.get('face_scale')}/"
                          f"{row_b.get('subject_position')}")
            pairs.append({
                "camera_a": asset_a, "camera_b": asset_b,
                "verdict": verdict, "reason": reason,
                "evidence_ids": sorted(set(row_a.get("evidence_ids", [])
                                           + row_b.get("evidence_ids", [])))})
    if len(assets) < 2:
        note = ("single measured angle" if assets
                else "no camera samples in the span")
        return {"pairs": pairs, "note": f"{note}: nothing to switch between",
                "evidence_ids": framing.get("evidence_ids", [])}
    return {"pairs": pairs, "evidence_ids": framing.get("evidence_ids", [])}


def normalize_camera_preferences(
        value: Any) -> dict[str, Any]:
    """Validate an optional brief ``camera_preferences`` declaration.

    Accepted shape: ``{"preferred_camera": str, "avoid_cameras": [str],
    "mode": "prefer"|"require"}``, every field optional. Absence means
    no preference. Anything else is a tasking error and refuses: a
    silently half-read preference would steer switches unasked.
    """
    if value is None:
        return {"status": "no_preference"}
    if not isinstance(value, dict):
        raise TypeError("camera_preferences must be an object")
    preferred = value.get("preferred_camera")
    avoided = value.get("avoid_cameras", [])
    mode = value.get("mode", "prefer")
    if preferred is not None and not isinstance(preferred, str):
        raise ValueError("camera_preferences.preferred_camera must be "
                         "an asset id string")
    if (not isinstance(avoided, list) or not all(
            isinstance(entry, str) for entry in avoided)):
        raise ValueError("camera_preferences.avoid_cameras must be a "
                         "list of asset id strings")
    if mode not in ("prefer", "require"):
        raise ValueError("camera_preferences.mode must be 'prefer' or "
                         "'require'")
    if preferred is None and not avoided:
        return {"status": "no_preference"}
    return {"status": mode, "preferred_camera": preferred,
            "avoid_cameras": list(avoided)}


def _reference_offsets(answer: dict[str, Any]) -> dict[str, float]:
    """M6 offsets by clock id from queried sync measurements."""
    offsets: dict[str, float] = {}
    for present in answer.get("present_intervals", []):
        if present.get("plan") != "multicam_sync":
            continue
        for row in present.get("measurements", []):
            if row.get("measurement_type") != "sync.multicam_offset":
                continue
            value = row.get("value")
            interval = row.get("interval") or {}
            if not isinstance(value, dict):
                continue
            offset = value.get("offset_to_reference_seconds")
            clock_id = interval.get("clock_id")
            if isinstance(offset, (int, float)) and isinstance(
                    clock_id, str):
                offsets[clock_id] = float(offset)
    return offsets


def _word_spans(answer: dict[str, Any], start_s: float,
                end_s: float) -> list[tuple[float, float]]:
    spans = []
    for present in answer.get("present_intervals", []):
        if present.get("plan") != "speech_attributed":
            continue
        for row in present.get("measurements", []):
            if row.get("measurement_type") != "speech.word":
                continue
            interval = row.get("interval") or {}
            item_start = interval.get("start")
            item_end = interval.get("end")
            if not isinstance(item_start, (int, float)) or not isinstance(
                    item_end, (int, float)):
                continue
            item_start /= evidence_query.TICKS_PER_SECOND
            item_end /= evidence_query.TICKS_PER_SECOND
            if item_end > start_s and item_start < end_s:
                spans.append((item_start, item_end))
    spans.sort()
    return spans


def _camera_uri(answer: dict[str, Any], asset_id: str | None) -> str | None:
    if asset_id is None:
        return None
    for asset in answer.get("assets", []):
        if isinstance(asset, dict) and asset.get("id") == asset_id:
            uri = asset.get("uri")
            return str(uri) if uri else None
    return None


def _refuse_switch(base: dict[str, Any],
                   words: list[tuple[float, float]], reason: str,
                   confidence: str = "high") -> dict[str, Any]:
    """A ``no_switch`` entry sharing the turn-change base row."""
    refused = {**base, "recommended_camera": "no_switch",
               "recommended_camera_asset_id": None,
               "no_switch_reason": reason,
               "confidence": confidence}
    refused["continuity"] = continuity_for_switch(
        refused, words, None)
    return refused


def plan_camera_switches(
        exchange: dict[str, Any], answer: dict[str, Any],
        camera_selection: dict[str, Any], framing: dict[str, Any],
        preferences: dict[str, Any],
        start_s: float, end_s: float) -> list[dict[str, Any]]:
    """Propose one switch entry per speaker change in the exchange.

    The safe window is the word gap around the change: from the last
    word end at or before the boundary to the first word start at or
    after it. A recommendation names a mapped camera only; candidates
    and abstentions, tight gaps, avoided cameras, and faceless target
    angles all yield ``no_switch`` with the reason. Consecutive turns
    by one speaker need no entry: no change, no switch.
    """
    turns = exchange.get("turns", [])
    if len(turns) < 2:
        return []
    words = _word_spans(answer, start_s, end_s)
    offsets = _reference_offsets(answer)
    verdict_by_pair = {}
    for pair in framing_compatibility(framing).get("pairs", []):
        verdict_by_pair[(pair["camera_a"], pair["camera_b"])] = pair
        verdict_by_pair[(pair["camera_b"], pair["camera_a"])] = pair
    mapped = camera_selection.get(
        "speaker_to_camera", {}) if camera_selection.get(
            "status") == "mapped" else {}
    switches = []
    for outgoing, incoming in pairwise(turns):
        if outgoing.get("speaker") == incoming.get("speaker"):
            continue
        change = float(incoming.get("start", start_s))
        clock_id = incoming.get("clock_id")
        earlier = [word_end for _, word_end in words if word_end <= change]
        later = [word_start for word_start, _ in words if word_start >= change]
        window_start = max(earlier) if earlier else float(
            outgoing.get("start", start_s))
        window_end = min(later) if later else float(
            incoming.get("end", end_s))
        window_start = max(window_start, start_s)
        window_end = min(window_end, end_s)
        evidence_ids = list(exchange.get("evidence", []))
        evidence_ids = [row.get("evidence_id")
                        if isinstance(row, dict) else row
                        for row in evidence_ids]
        hold_end = end_s
        offset = offsets.get(clock_id) if isinstance(clock_id, str) else None
        base: dict[str, Any] = {
            "speaker_change_seconds": round(change, 3),
            "source_clock_id": clock_id,
            "safe_switch_start_seconds": round(window_start, 3),
            "safe_switch_end_seconds": round(window_end, 3),
            "reference_change_seconds": (
                round(change + offset, 3) if offset is not None else None),
            "reference_window": (
                [round(window_start + offset, 3),
                 round(window_end + offset, 3)]
                if offset is not None else None),
            "outgoing_speaker": outgoing.get("speaker"),
            "incoming_speaker": incoming.get("speaker"),
            "acceptable_hold_end_seconds": round(hold_end, 3),
            "evidence_ids": sorted({str(item) for item in evidence_ids
                                    if item})}

        if window_end - window_start < MIN_SWITCH_GAP_S:
            switches.append(_refuse_switch(
                base, words,
                f"switch gap {window_end - window_start:.3f}s under "
                f"{MIN_SWITCH_GAP_S}s: cutting here risks a clipped word"))
            continue
        options = mapped.get(incoming.get("speaker"), {})
        if not options:
            if camera_selection.get("status") == "candidate":
                reason = ("incoming speaker has a candidate suggestion, "
                          "not a verified mapping: LR-ASD output cannot "
                          "justify an angle change")
            else:
                reason = ("incoming speaker has no mapped camera: "
                          "an angle change would be a guess")
            switches.append(_refuse_switch(base, words, reason))
            continue
        choice = None
        avoided = preferences.get("avoid_cameras", [])
        preferred = preferences.get("preferred_camera")
        for asset_id in sorted(options):
            if asset_id in avoided and preferences.get(
                    "status") == "require":
                continue
            choice = asset_id
            if asset_id == preferred:
                break
        if choice is None:
            switches.append(_refuse_switch(base, words, 
                "every mapped camera for the incoming speaker is "
                "avoided by a required camera preference"))
            continue
        if preferred is not None and choice != preferred and (
                preferred in options
                and preferred not in avoided):
            choice = preferred
        target_framing = (framing.get("cameras", {}) or {}).get(choice, {})
        if target_framing.get("face_scale") == "no_face":
            switches.append(_refuse_switch(base, words, 
                f"{choice} shows no face in the span: nothing to cut to"))
            continue
        outgoing_options = mapped.get(outgoing.get("speaker"), {})
        outgoing_cam = next(iter(sorted(outgoing_options)), None)
        pair = verdict_by_pair.get((outgoing_cam, choice)) if (
            outgoing_cam) else None
        verdict = (pair or {}).get("verdict")
        if verdict == "incompatible":
            switches.append(_refuse_switch(base, words, 
                f"framing refuses the switch: {pair.get('reason')}"))
            continue
        uri = _camera_uri(answer, choice)
        switch = {
            **base, "recommended_camera": uri or choice,
            "recommended_camera_asset_id": choice,
            "no_switch_reason": None,
            "framing_verdict": verdict,
            "confidence": ("high" if verdict == "compatible"
                           and target_framing.get("status") == "measured"
                           else "medium"),
            "evidence_ids": sorted(set(base["evidence_ids"])
                                   | set(target_framing.get(
                                       "evidence_ids", [])))}
        switch["continuity"] = continuity_for_switch(
            switch, words, verdict)
        switches.append(switch)
    return switches


def continuity_for_switch(
        switch: dict[str, Any], words: list[tuple[float, float]],
        framing_verdict: str | None) -> dict[str, Any]:
    """The five continuity flags for one proposed switch.

    Each flag is True, False, or None (unmeasured). Eyeline and the
    dialogue axis have no measurement behind a candidate switch, so
    they report None with the reason rather than a convenient True.
    """
    breaks: list[str] = []
    if switch.get("recommended_camera") == "no_switch":
        return {"subject_correct": None,
                "dialogue_continuous": None,
                "eyeline_continuous": None,
                "axis_continuous": None,
                "framing_compatible": None,
                "continuity_breaks": [("no switch proposed: continuity "
                                       "is vacuous")],
                "evidence": switch.get("no_switch_reason", "")}
    window = (switch.get("safe_switch_start_seconds"),
              switch.get("safe_switch_end_seconds"))
    clipped = any(start < window[1] and end > window[0]
                  and (start < window[0] < end
                       or start < window[1] < end)
                  for start, end in words
                  if isinstance(start, (int, float))
                  and isinstance(end, (int, float)))
    dialogue_continuous: bool | None = not clipped
    if clipped:
        breaks.append("switch window clips a measured word edge")
    if framing_verdict == "compatible" or framing_verdict == "same_framing":
        framing_compatible: bool | None = True
    elif framing_verdict == "incompatible":
        framing_compatible = False
        breaks.append("target angle framing is incompatible")
    else:
        framing_compatible = None
    return {"subject_correct": True,
            "subject_correct_basis": "recommended camera is the mapped "
                                     "camera of the incoming speaker",
            "dialogue_continuous": dialogue_continuous,
            "eyeline_continuous": None,
            "eyeline_basis": "no eyeline measurement behind a "
                             "candidate switch",
            "axis_continuous": None,
            "axis_basis": "no camera-geometry measurement behind a "
                          "candidate switch",
            "framing_compatible": framing_compatible,
            "continuity_breaks": breaks,
            "evidence": f"word-safe window {window[0]}-{window[1]}s; "
                        f"framing verdict {framing_verdict}"}


def score_multicam_switches(
        predicted: list[dict[str, Any]] | None,
        reference: dict[str, Any] | list[dict[str, Any]] | None,
        tolerance_s: float = SWITCH_SCORE_TOLERANCE_S) -> dict[str, Any]:
    """Score switch timing and subject correctness against labels.

    Both sides are switch lists in the blind-packet shape
    (``speaker_change_seconds``, ``safe_switch_start_seconds``,
    ``safe_switch_end_seconds``, ``recommended_camera``). Timing is a
    hit when the predicted change falls inside the reference safe
    window or within ``tolerance_s`` of the reference change.
    Subject correctness compares recommended cameras, with
    ``no_switch`` agreement counting. References without switch
    labels (window-style agent edits) are unscorable, said plainly.
    """
    if reference is None:
        return {"status": "unscorable",
                "reason": "no reference labels supplied"}
    if isinstance(reference, dict):
        ref_switches = reference.get("switches")
        if ref_switches is None:
            return {"status": "unscorable",
                    "reason": "reference carries no switch labels "
                              "(window-style edit, not switch labels)"}
    else:
        ref_switches = reference
    if not isinstance(ref_switches, list) or not ref_switches:
        return {"status": "unscorable",
                "reason": "reference switch list is empty"}
    predicted = predicted or []
    rows = []
    matched_refs: set[int] = set()
    for pred in predicted:
        change = pred.get("speaker_change_seconds")
        best_index = None
        best_error = None
        for index, ref in enumerate(ref_switches):
            if index in matched_refs:
                continue
            ref_change = ref.get("speaker_change_seconds")
            if not isinstance(change, (int, float)) or not isinstance(
                    ref_change, (int, float)):
                continue
            error = abs(float(change) - float(ref_change))
            if best_error is None or error < best_error:
                best_error = error
                best_index = index
        if best_index is None:
            rows.append({"predicted_index": len(rows), "reference_index": None,
                         "timing_hit": False, "timing_error_s": None,
                         "subject_correct": False})
            continue
        matched_refs.add(best_index)
        ref = ref_switches[best_index]
        inside = (isinstance(ref.get("safe_switch_start_seconds"),
                             (int, float))
                  and isinstance(ref.get("safe_switch_end_seconds"),
                                 (int, float))
                  and ref["safe_switch_start_seconds"] <= change
                  <= ref["safe_switch_end_seconds"])
        hit = bool(inside or (best_error is not None
                              and best_error <= tolerance_s))
        pred_cam = pred.get("recommended_camera")
        ref_cam = ref.get("recommended_camera")
        rows.append({"predicted_index": len(rows),
                     "reference_index": best_index,
                     "timing_hit": hit, "timing_error_s": round(
                         best_error, 3) if best_error is not None else None,
                     "subject_correct": pred_cam == ref_cam,
                     "predicted_camera": pred_cam,
                     "reference_camera": ref_cam})
    timing_hits = sum(1 for row in rows if row["timing_hit"])
    subject_hits = sum(1 for row in rows if row["subject_correct"])
    errors = [row["timing_error_s"] for row in rows
              if row["timing_error_s"] is not None]
    return {"status": "scored",
            "predicted_switch_count": len(predicted),
            "reference_switch_count": len(ref_switches),
            "timing_hits": timing_hits,
            "timing_accuracy": (timing_hits / len(ref_switches)
                                if ref_switches else 0.0),
            "subject_correct_count": subject_hits,
            "subject_accuracy": (subject_hits / len(rows) if rows else 0.0),
            "mean_abs_timing_error_s": (
                round(sum(errors) / len(errors), 3) if errors else None),
            "unmatched_reference_count": len(ref_switches) - len(matched_refs),
            "rows": rows}


def _word_rows_from_answers(
        answers_by_moment: dict[str, dict[str, Any]],
        moments: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Project fresh, queried speech-word measurements into spine rows.

    The evidence query already filters by source clock, interval, freshness,
    and the required speech-word/voice-turn join. Reuse only those returned
    word measurements to build speech blocks; a raw transcript elsewhere in
    the Project Model must not silently support a cut.
    """
    source_by_moment = {moment["id"]: moment["source_id"]
                        for moment in moments}
    rows: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str]] = set()
    for moment_id, answer in answers_by_moment.items():
        source_id = source_by_moment[moment_id]
        for present in answer["present_intervals"]:
            if present["plan"] != "speech_attributed":
                continue
            for measurement in present["measurements"]:
                if measurement["measurement_type"] != "speech.word":
                    continue
                value = measurement["value"]
                if not isinstance(value, dict) or not isinstance(
                        value.get("text"), str) or not value["text"].strip():
                    continue
                evidence_id = measurement["evidence_id"]
                key = (source_id, evidence_id)
                if key in seen:
                    continue
                seen.add(key)
                interval = measurement["interval"]
                rows.setdefault(source_id, []).append({
                    "word": value["text"],
                    "source_start": (interval["start"] /
                                     evidence_query.TICKS_PER_SECOND),
                    "source_end": (interval["end"] /
                                   evidence_query.TICKS_PER_SECOND),
                    "evidence_id": evidence_id,
                })
    for source_rows in rows.values():
        source_rows.sort(key=lambda row: row["source_start"])
    return rows


def ground_cuts(brief: dict[str, Any], task_types: list[str],
                answers_by_moment: dict[str, dict[str, Any]],
                edit_points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]],
                                                           list[dict[str, Any]]]:
    """Narrow brief cut ranges to what the evidence supports.

    A cut survives only where a present evidence interval overlaps its
    range; the surviving span is the overlap, never wider than the brief
    asked for. Ranges with no supporting evidence become abstentions with
    a named reason. Edit-point candidates choose the exact cut position
    inside a surviving span when one falls there; otherwise the span
    edge stands and says so.
    """
    cuts: list[dict[str, Any]] = []
    abstentions: list[dict[str, Any]] = []
    preferences = normalize_camera_preferences(
        (brief.get("brief") or {}).get("camera_preferences"))
    for cut_range in (brief.get("brief") or {}).get("cut_ranges", []):
        moment_id = cut_range.get("moment_id")
        answer = answers_by_moment.get(moment_id, {})
        dialogue_exchange = None
        if "dialogue_multicam" in task_types:
            dialogue_exchange = _dialogue_exchange(
                answer, cut_range["start_seconds"], cut_range["end_seconds"])
            if dialogue_exchange is None:
                abstentions.append({"moment_id": moment_id,
                                    "reason": "no complete exchange with "
                                              "two distinct measured speaker "
                                              "turns and queried word evidence",
                                    "required_plans": [["speech_attributed"]]})
                continue
        required_plans = [REQUIRED_SELECTION_PLANS.get(
            task_type, frozenset()) for task_type in task_types]
        required_plans = [plans for plans in required_plans if plans]
        plan_overlaps: dict[str, list[dict[str, Any]]] = {}
        for evidence in answer.get("present_intervals", []):
            if not any(evidence["plan"] in plans for plans in required_plans):
                continue
            start, end = evidence["start_seconds"], evidence["end_seconds"]
            left = max(start, cut_range["start_seconds"])
            right = min(end, cut_range["end_seconds"])
            if right > left:
                plan_overlaps.setdefault(evidence["plan"], []).append(
                    {**evidence, "start_seconds": left,
                     "end_seconds": right})
        compatible = []
        for plan_group in required_plans:
            available = [item for plan in plan_group
                         for item in plan_overlaps.get(plan, [])]
            if not available:
                compatible = []
                break
            compatible.append(available)
        intersections = ([(item["start_seconds"], item["end_seconds"],
                           [item]) for item in compatible[0]]
                         if compatible else [])
        for group in compatible[1:]:
            intersections = [
                (max(left, item["start_seconds"]),
                 min(right, item["end_seconds"]), records + [item])
                for left, right, records in intersections for item in group
                if min(right, item["end_seconds"])
                > max(left, item["start_seconds"])]
        intersections = [row for row in intersections if row[1] > row[0]]
        if not intersections:
            abstentions.append({"moment_id": moment_id,
                                "reason": "required selection evidence is "
                                          "unavailable or has no shared "
                                          "interval in the brief range",
                                "required_plans": [sorted(plans)
                                                   for plans in required_plans]})
            continue
        intersections.sort(key=lambda row: (-(row[1] - row[0]), row[0]))
        start, end, selected = intersections[0]
        if dialogue_exchange is not None:
            start = dialogue_exchange["start_seconds"]
            end = dialogue_exchange["end_seconds"]
            exchange_measurements = dialogue_exchange["evidence"]
            exchange_ids = {row["evidence_id"]
                            for row in exchange_measurements}
            evidence_intervals = [
                item for item in answer.get("present_intervals", [])
                if exchange_ids.intersection(item.get("evidence_ids", []))]
            selected = [{"evidence_ids": [row["evidence_id"]
                                           for row in exchange_measurements],
                         "measurements": exchange_measurements,
                         "plan": "speech_attributed",
                         "clock_id": next((item["clock_id"]
                                           for item in answer.get(
                                               "present_intervals", [])
                                           if item["plan"] ==
                                           "speech_attributed"), None),
                         "query_result_ids": []}]
            camera_selection = _speaker_camera_mapping(
                answer, dialogue_exchange["speakers"])
            if camera_selection["status"] == "mapped":
                camera_evidence_ids = sorted({
                    evidence_id
                    for camera in camera_selection["speaker_to_camera"].values()
                    for asset in camera.values()
                    for evidence_id in asset["evidence_ids"]})
                camera_measurements = [row for item in answer.get(
                    "present_intervals", [])
                    if item["plan"] == "speaker_camera"
                    for row in item["measurements"]
                    if row["evidence_id"] in camera_evidence_ids]
                selected[0]["evidence_ids"].extend(camera_evidence_ids)
                selected[0]["measurements"].extend(camera_measurements)
                evidence_intervals.extend(
                    item for item in answer.get("present_intervals", [])
                    if item["plan"] == "speaker_camera"
                    and set(camera_evidence_ids).intersection(
                        item.get("evidence_ids", [])))
            framing = measure_camera_framing(answer, start, end)
            selected[0]["evidence_ids"].extend(framing.get(
                "evidence_ids", []))
            selected[0]["measurements"].extend(framing.get(
                "measurements", []))
            switch_plan = plan_camera_switches(
                dialogue_exchange, answer, camera_selection, framing,
                preferences, start, end)
            selected[0]["query_result_ids"] = sorted({
                item["query_result_id"] for item in evidence_intervals
                if item.get("query_result_id")})
            selected[0]["evidence_revision"] = next(
                (item["evidence_revision"] for item in evidence_intervals
                 if item.get("evidence_revision")), None)
        else:
            camera_selection = None
            framing = {"cameras": {}, "evidence_ids": []}
            switch_plan = []
        if any(not item["evidence_ids"] or not item["measurements"]
               for item in selected):
            abstentions.append({"moment_id": moment_id,
                                "reason": "required selection evidence "
                                          "could not be resolved to source "
                                          "measurements",
                                "required_plans": [sorted(plans)
                                                   for plans in required_plans]})
            continue
        evidence_ids = sorted({evidence_id for item in selected
                               for evidence_id in item["evidence_ids"]})
        query_result_ids = sorted({query_id for item in selected
                                   for query_id in item.get(
                                       "query_result_ids",
                                       [item.get("query_result_id")])
                                   if query_id})
        measurements = [measurement for item in selected
                        for measurement in item["measurements"]]
        clocks = {item["clock_id"] for item in selected if item["clock_id"]}
        matching_points = [point for point in edit_points
                           if (not clocks or point.get("clock_id") in clocks)
                           and start < (_tick_seconds(point) or 0) < end]
        matching_points.sort(key=lambda point: _tick_seconds(point))
        ticks = [_tick_seconds(point) for point in matching_points]
        point_evidence_ids = sorted({measurement_id
                                     for point in matching_points
                                     for measurement_id in
                                     point.get("measurement_ids", [])})
        cuts.append({"moment_id": moment_id,
                     "source_id": cut_range["source_id"],
                     "start_seconds": start, "end_seconds": end,
                     "reason": {"creative_rationale": cut_range.get("reason", ""),
                                "factual_evidence": measurements,
                                "evidence_ids": evidence_ids,
                                "query_result_ids": query_result_ids,
                                "evidence_revision": selected[0].get(
                                    "evidence_revision"),
                                "evidence_plans": sorted({item["plan"]
                                                           for item in selected}),
                                "dialogue_exchange": ({
                                    "speakers": dialogue_exchange["speakers"],
                                    "complete_turn_count": 2}
                                    if dialogue_exchange else None),
                                "camera_selection": camera_selection,
                                "framing": framing if dialogue_exchange else None,
                                "switch_plan": switch_plan if dialogue_exchange else [],
                                "camera_preferences": preferences,
                                "technical_measurements": [
                                    item for item in measurements
                                    if item["measurement_type"] not in {
                                        "speech.word", "person.voice_turn"}
                                    and item.get("value") is not None],
                                "edit_point_verification": {
                                    "status": "verified" if ticks else
                                    "no_signal_candidate",
                                    "tick_seconds": ticks[0] if ticks else None,
                                    "measurement_ids": point_evidence_ids}},
                     "cut_tick_s": ticks[0] if ticks else None,
                     "point_verification": {
                         "status": "verified" if ticks else "no_signal_candidate",
                         "measurement_ids": point_evidence_ids},
                     "signal_grounded": bool(ticks)})
    return cuts, abstentions


def state_for_cuts(task_id: str, task_types: list[str],
                   cuts: list[dict[str, Any]],
                   word_rows_by_source: dict[str, list[dict[str, Any]]]
                   ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Build edit-graph state from grounded cuts.

    Dialogue cuts carrying measured word rows become speech blocks with
    their real timings; dialogue cuts without word-level evidence cannot
    become speech blocks (the spine contract allows empty timings only
    for non-speech blocks), so they are reported as ungraphable rather
    than emitted as hollow speech. Visual cuts become action beats,
    which carry no word timings by contract. The graph is derived from
    this state via :func:`edit_graph.build_graph`.
    """
    ungraphable: list[dict[str, Any]] = []
    blocks: list[dict[str, Any]] = []
    passages: list[dict[str, Any]] = []
    assignments: list[dict[str, Any]] = []
    visual = any(task_type.startswith("visual_") for task_type in task_types)
    position = 0
    timeline = 0.0
    for cut in cuts:
        words = [row for row in word_rows_by_source.get(cut["source_id"], [])
                 if row.get("source_start", 0) >= cut["start_seconds"]
                 and row.get("source_end", 0) <= cut["end_seconds"]]
        if not words and not visual:
            ungraphable.append({"moment_id": cut["moment_id"],
                                "reason": "no measured word timings inside "
                                          "the grounded span"})
            continue
        position += 1
        duration = cut["end_seconds"] - cut["start_seconds"]
        clip_id = f"eval04:{task_id}:{cut['moment_id']}"
        block = {
            "position": position,
            "block_type": "action" if visual else "speech",
            "clip_id": clip_id,
            "source_start": cut["start_seconds"],
            "source_end": cut["end_seconds"],
            "timeline_start": timeline, "timeline_end": timeline + duration,
            "word_timestamps": words,
            "alignment_method": "search" if words else None}
        blocks.append(block)
        if words:
            passages.append({
                "position": position, "clip_id": clip_id,
                "source_start": cut["start_seconds"],
                "source_end": cut["end_seconds"],
                "start_time": timeline, "end_time": timeline + duration,
                "text": " ".join(row.get("word", "") for row in words)})
            assignments.append({
                "spine_block_position": position, "clip_id": clip_id,
                "source_start": cut["start_seconds"],
                "source_end": cut["end_seconds"],
                "timeline_start": timeline,
                "timeline_end": timeline + duration})
        timeline += duration
    state = {"capability_outputs": {
        "spine.mesh": {"audio_spine": {"structure": blocks}},
        "speech.enrich": {"speech_sequence": {"body_sequence": passages}},
        "aroll.assign": {"a_roll_assignments": {"a_roll_assignments":
                                                assignments}}}}
    return state, ungraphable


def write_reviewable_output(output_root: Path, task_id: str, run_name: str,
                            graph: dict[str, Any], cuts: list[dict[str, Any]],
                            ledger: list[dict[str, Any]],
                            edit_operations: list[dict[str, Any]] | None = None
                            ) -> dict[str, str]:
    """Write the reviewable edit artefacts under the output root.

    The edit graph, the evidence ledger, and a cut-list timeline in OTIO
    shape land under ``<task_id>/<run_name>/``; returned paths are
    relative to the output root so the report stays relocatable. All
    writes stay inside the root: a run name that escapes it is refused.
    """
    run_dir = (output_root / task_id / run_name).resolve()
    try:
        run_dir.relative_to(output_root.resolve())
    except ValueError:
        raise ValueError(f"run {run_name!r} escapes the output root") from None
    run_dir.mkdir(parents=True, exist_ok=True)
    timeline = {"OTIO_SCHEMA": "Timeline.1", "name": f"{task_id}/{run_name}",
                "tracks": [{"kind": "Video", "clips": [
                    {"name": cut["moment_id"],
                     "source": cut["source_id"],
                     "source_range": [cut["start_seconds"],
                                      cut["end_seconds"]],
                     "reason": cut.get("reason", {})}
                    for cut in cuts]}]}
    artefacts = {"edit-graph.json": graph,
                 "evidence-ledger.json": ledger,
                 "edit.otio.json": timeline}
    if edit_operations is not None:
        artefacts["edit-operations.json"] = edit_operations
    for filename, payload in artefacts.items():
        (run_dir / filename).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    return {filename: str(Path(task_id) / run_name / filename)
            for filename in artefacts}


def external_scorer_status(repo_root: Path) -> dict[str, dict[str, str]]:
    """Report which in-flight scorer benchmarks can be consumed.

    EVAL-02/03/05/06/07 are in flight: where a pinned scorer artefact has
    landed this function names it; the rest are pending with an explicit
    reason. Nothing is inferred from a missing scorer.
    """
    import glob as _glob

    status: dict[str, dict[str, str]] = {}
    for name, patterns in EXTERNAL_SCORER_GLOBS.items():
        found = [match for pattern in patterns
                 for match in _glob.glob(str(repo_root / pattern))]
        if found:
            status[name] = {"state": "available",
                            "detail": "; ".join(sorted(found))}
        else:
            status[name] = {"state": "pending",
                            "detail": f"no pinned {name} scorer artefact "
                                      f"on this base; task-level EVAL-04 "
                                      f"measurements stand alone"}
    return status


def _contained_file(root: Path, value: str) -> bool:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return path.is_file()


def assemble_run(task: dict[str, Any], split: str,
                 ledger: list[dict[str, Any]],
                 reviewable_output: str,
                 stages: dict[str, str],
                 metrics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assemble one validator-shaped run; unmeasured metrics stay absent.

    ``metrics`` carries only measured scores. Human revision distance and
    preference arrive only with a blinded human reference edit; until
    then they are omitted and the acceptance check fails closed with
    explicit missing-metric reasons.
    """
    run: dict[str, Any] = {"split": split, "stages": stages,
                           "reviewable_output": reviewable_output,
                           "evidence_decisions": ledger}
    if metrics is not None:
        run["metrics"] = metrics
    return run


def run_task(task: dict[str, Any], model: dict[str, Any], *,
             split: str, run_name: str, output_root: Path,
             source_durations: dict[str, float],
             word_rows_by_source: dict[str, list[dict[str, Any]]] | None = None,
             acquisition_budget_seconds: float = 0.0,
             metrics: dict[str, Any] | None = None,
             render_output: str | None = None,
             verify_passed: bool | None = None,
             source_ids: list[str] | None = None) -> tuple[dict[str, Any],
                                                        dict[str, Any]]:
    """Run one brief end to end and return its task entry plus diagnostics.

    ``render_output`` names a rendered file under the output root (the
    Resolve slice supplies it; the first slice leaves it absent) and
    ``verify_passed`` records whether the deterministic render gate
    passed on it. Both stay absent until actually produced: a run that
    never rendered reports those stages incomplete rather than passing
    them on the strength of the plan alone.
    """
    task_id = task["id"]
    task_types = task.get("task_types", [])
    brief_errors = editorial_task_benchmark.validate_brief(task_id,
                                                           task.get("brief"))
    window_errors = check_brief_windows(task, source_durations)
    if brief_errors or window_errors:
        raise ValueError("; ".join(brief_errors + window_errors))
    ledger = [consult_model(model)]
    answers_by_moment: dict[str, dict[str, Any]] = {}
    evidence_revision = _evidence_revision(model)
    brief_body = dict(task.get("brief") or {})
    if source_ids is not None:
        wanted = set(source_ids)
        brief_body = {**brief_body,
            "expected_source_moments": [
                moment for moment in brief_body.get("expected_source_moments", [])
                if moment.get("source_id") in wanted],
            "cut_ranges": [
                cut for cut in brief_body.get("cut_ranges", [])
                if cut.get("source_id") in wanted]}
    scoped_task = dict(task, brief=brief_body)
    for moment in brief_body.get("expected_source_moments", []):
        rows, answer = consult_moment(
            model, moment, task_types,
            acquisition_budget_seconds=acquisition_budget_seconds,
            duration_seconds=source_durations[moment["source_id"]],
            evidence_revision=evidence_revision)
        ledger.extend(rows)
        answers_by_moment[moment["id"]] = answer
    edit_rows, edit_points = consult_edit_points(model)
    ledger.extend(edit_rows)
    cuts, abstentions = ground_cuts(scoped_task, task_types,
                                    answers_by_moment, edit_points)
    for abstention in abstentions:
        ledger.append(_ledger_row(
            f"abstain on {abstention['moment_id']}",
            abstention["reason"] + "; required plans: " +
            json.dumps(abstention["required_plans"], sort_keys=True),
            [f"evidence_query:unavailable:{abstention['moment_id']}",
             f"project_model:{model.get('id', 'unidentified')}"],
            "evidence_query"))
    word_rows = (word_rows_by_source if word_rows_by_source is not None else
                 _word_rows_from_answers(
                     answers_by_moment,
                     brief_body.get("expected_source_moments", [])))
    state, ungraphable = state_for_cuts(
        task_id, task_types, cuts, word_rows)
    graph = edit_graph.build_graph(state)
    edit_operations = []
    from library.tools.project_model_editorial import edit_graph_node_to_operation
    for node in graph.get("nodes", []):
        clip_id = (node.get("payload") or {}).get("clip_id")
        cut = next((row for row in cuts
                    if clip_id == f"eval04:{task_id}:{row['moment_id']}"), None)
        if cut is None:
            continue
        reason = cut["reason"]
        node = dict(node, evidence_ids=reason["evidence_ids"],
                    query_result_id=(reason["query_result_ids"][0]
                                     if reason["query_result_ids"] else ""),
                    evidence_revision=reason["evidence_revision"],
                    creative_rationale=reason["creative_rationale"])
        edit_operations.append(edit_graph_node_to_operation(node))
    artefacts = write_reviewable_output(output_root, task_id, run_name,
                                        graph, cuts, ledger, edit_operations)
    rendered = (render_output is not None
                and _contained_file(output_root, render_output))
    if render_output is not None and not rendered:
        raise ValueError(f"render output {render_output!r} is missing or "
                         f"outside the output root")
    stages = {"evidence": "complete" if ledger else "incomplete",
              "plan": "complete" if cuts or abstentions else "incomplete",
              "edit_graph": "complete" if graph.get("node_count") else
              "incomplete",
              "render": "complete" if rendered else "incomplete",
              "verify": "complete" if verify_passed is not None and rendered
              else "incomplete"}
    reviewable = render_output if rendered else artefacts["edit.otio.json"]
    run = assemble_run(task, split, ledger, reviewable, stages, metrics)
    diagnostics = {"cuts": cuts, "abstentions": abstentions,
                   "ungraphable": ungraphable,
                   "scoped_sources": source_ids,
                   "graph_node_count": graph.get("node_count", 0),
                   "edit_operations": edit_operations,
                   "external_scorers": external_scorer_status(
                       Path(__file__).resolve().parents[2])}
    return ({"id": task_id, "brief": task.get("brief"),
             "task_types": task_types,
             "human_reference": {"path": "", "blinded": False},
             "runs": [run]}, diagnostics)


def main(argv: list[str] | None = None) -> int:
    """Run briefs against a Project Model file and validate the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brief", action="append", required=True,
                        help="frozen brief JSON (repeatable)")
    parser.add_argument("--model", required=True,
                        help="Project Model JSON the run consults")
    parser.add_argument("--durations", required=True,
                        help="JSON mapping source_id to measured duration "
                             "seconds (EVAL-01 corpus manifest)")
    parser.add_argument("--split", required=True,
                        choices=["training", "calibration", "held_out"])
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--acquisition-budget-seconds", type=float, default=0.0)
    parser.add_argument("--word-rows", default=None,
                        help="JSON mapping source_id to measured word-timing "
                             "rows; cuts without word-level evidence never "
                             "become speech blocks")
    parser.add_argument("--sources", default=None,
                        help="comma-separated source_ids to run; moments and "
                             "cuts on other sources are excluded from this "
                             "run so one brief can repeat per split")
    args = parser.parse_args(argv)
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    durations = json.loads(Path(args.durations).read_text(encoding="utf-8"))
    word_rows = (json.loads(Path(args.word_rows).read_text(encoding="utf-8"))
                 if args.word_rows else None)
    only_sources = (args.sources.split(",") if args.sources else None)
    tasks = []
    for brief_path in args.brief:
        task = load_brief(Path(brief_path))
        entry, _ = run_task(
            task, model, split=args.split, run_name=args.run_name,
            output_root=args.output_root, source_durations=durations,
            word_rows_by_source=word_rows,
            acquisition_budget_seconds=args.acquisition_budget_seconds,
            source_ids=only_sources)
        tasks.append(entry)
    report = {"tasks": tasks}
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    result = editorial_task_benchmark.validate_report(
        report, output_root=args.output_root)
    result["scores"] = editorial_task_benchmark.aggregate_scores(report)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
