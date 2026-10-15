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
import json
import sys
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
    "visual_action": VISUAL_PLANS,
    "visual_object": VISUAL_PLANS,
}

# Evidence that can justify proposing a source interval for each task type.
# Corroborating measurements (scene boundaries, sound energy, sync) remain
# useful facts, but cannot stand in for the subject of the edit.
REQUIRED_SELECTION_PLANS = {
    "dialogue_multicam": frozenset({"speech_attributed"}),
    "visual_action": frozenset({"action"}),
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
    for clock in model.get("clocks") or []:
        if not isinstance(clock, dict):
            continue
        domain = clock.get("domain") or {}
        if domain.get("asset_id") == moment["source_id"] and clock.get("id"):
            scope["clock_id"] = clock["id"]
            scope["asset_id"] = moment["source_id"]
            break
    return scope


def consult_moment(model: dict[str, Any], moment: dict[str, Any],
                   task_types: list[str], *,
                   acquisition_budget_seconds: float = 0.0,
                   duration_seconds: float) -> tuple[list[dict[str, Any]],
                                                    dict[str, Any]]:
    """Query the evidence behind one expected moment and plan acquisition.

    Returns the ledger rows and a per-moment answer summary. Queries run
    against the supplied model only; acquisition is PLANNED
    (:func:`plan_query_acquisition` is pure) and never executed here, so
    the first slice spends nothing. A zero budget records the deferral
    explicitly instead of pretending the evidence was acquired.
    """
    rows: list[dict[str, Any]] = []
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
        statuses[plan_name] = answer["status"]
        results = answer.get("results", [])
        evidence_ids = [evidence_id for row in results
                        for evidence_id in row.get("evidence_ids", [])]
        if not evidence_ids:
            evidence_ids = [f"evidence_query:{plan_name}",
                            f"project_model:{model.get('id', 'unidentified')}"]
        detail = "; ".join(answer.get("reasons", [])) or \
            f"{len(results)} joined interval(s)"
        rows.append(_ledger_row(
            f"query {plan_name} over {moment['source_id']} "
            f"{scope['start_s']}-{scope['end_s']}s",
            f"{answer['status']}: {detail}",
            evidence_ids, "evidence_query"))
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
    return rows, {"statuses": statuses, "present_intervals": present_intervals}


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
    for cut_range in (brief.get("brief") or {}).get("cut_ranges", []):
        moment_id = cut_range.get("moment_id")
        answer = answers_by_moment.get(moment_id, {})
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
                                "evidence_plans": sorted({item["plan"]
                                                           for item in selected}),
                                "technical_measurements": [
                                    item for item in measurements
                                    if item.get("value") is not None],
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
                            ledger: list[dict[str, Any]]) -> dict[str, str]:
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
    brief_body = dict(task.get("brief") or {})
    if source_ids is not None:
        wanted = set(source_ids)
        brief_body = dict(brief_body, **{
            "expected_source_moments": [
                moment for moment in brief_body.get("expected_source_moments", [])
                if moment.get("source_id") in wanted],
            "cut_ranges": [
                cut for cut in brief_body.get("cut_ranges", [])
                if cut.get("source_id") in wanted]})
    scoped_task = dict(task, brief=brief_body)
    for moment in brief_body.get("expected_source_moments", []):
        rows, answer = consult_moment(
            model, moment, task_types,
            acquisition_budget_seconds=acquisition_budget_seconds,
            duration_seconds=source_durations[moment["source_id"]])
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
    artefacts = write_reviewable_output(output_root, task_id, run_name,
                                        graph, cuts, ledger)
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
