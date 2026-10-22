"""A01: refinement candidates reach execution through the Q01 contract.

Defect-naming tests only (cheap: stored plans and stubs, no model,
no Resolve, no media). Each test names the defect it pins; the A01
measurement itself is ``test_recorded_plans_before_and_after``,
which runs the refinement loop over recorded candidates and audits
the same recorded decisions before the loop (candidates consumed by
nothing, as the engine carries them today) and after grounding them
through the loop's Q01 references, with denominators.
"""

from __future__ import annotations

import copy

import pytest

from library.tools import edit_evidence_contract as contract
from library.tools import semantic_refinement as refine
from library.tools import source_memory
from library.tools.semantic_window_planner import (
    activity_map_from_temporal_index,
    plan_semantic_windows,
)


def _candidate(start, end, priority=0.8, reason="unmeasured_activity"):
    return {"start": float(start), "end": float(end),
            "priority": priority, "reason": reason}


def _plan(candidates):
    return {"schema_version": 1, "method": "adaptive-semantic-windows-v1",
            "task": "general", "window_seconds": 10.0,
            "window_starts_s": [0.0, 30.0, 60.0],
            "refinement_candidates": candidates,
            "unseen_event_policy": "no interpolation"}


def _temporal_index():
    return {
        "scene_boundaries": [{"timestamp": 22.0}, {"timestamp": 90.0}],
        "speech_regions": [{"start": 50.0, "end": 54.0}],
    }


def _analyzer(scenes_by_window=None, fail_on=()):
    calls = []

    def analyze(start_s, end_s):
        calls.append((start_s, end_s))
        if (start_s, end_s) in fail_on:
            raise RuntimeError("the model call failed")
        scenes = (scenes_by_window or {}).get(
            (start_s, end_s),
            [{"start": start_s + 1.0, "end": start_s + 3.0,
               "location": "kitchen", "type": "interior"}])
        return {"measured": True, "scenes": scenes, "boundaries": []}

    analyze.calls = calls
    return analyze


def test_the_sparse_discovery_pass_is_never_replanned():
    plan = _plan([_candidate(20, 25), _candidate(50, 55,
                                                reason="activity_peak")])
    frozen = copy.deepcopy(plan)

    run = refine.run_refinement(
        plan, temporal_index=_temporal_index(),
        budget_model_calls=4, analyze=_analyzer(),
        feed_back=lambda *args: None,
        content_digest="digest-test-aaa")

    assert plan == frozen
    assert run["window_seconds"] == 10.0
    assert run["task"] == "general"


def test_unmeasured_time_is_ranked_by_uncertainty_not_activity():
    [row] = refine.rank_refinement_candidates(
        _plan([_candidate(20, 25, priority=0.9,
                          reason="unmeasured_activity")]))

    assert row["signals"]["uncertainty"] == 1.0
    assert row["signals"]["activity"] == 0.0


def test_measured_activity_is_ranked_by_activity_not_uncertainty():
    [row] = refine.rank_refinement_candidates(
        _plan([_candidate(50, 55, priority=0.7, reason="activity_peak")]))

    assert row["signals"]["activity"] == 0.7
    assert row["signals"]["uncertainty"] == 0.0


def test_a_scene_boundary_marks_the_candidate_it_falls_in():
    first, second = refine.rank_refinement_candidates(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(40, 45, reason="activity_peak")]),
        temporal_index=_temporal_index())

    assert first["start"] == 20.0
    assert first["signals"]["scene_change"] == 1.0
    assert second["signals"]["scene_change"] == 0.0


def test_speech_cover_marks_the_candidate_it_overlaps():
    [row] = refine.rank_refinement_candidates(
        _plan([_candidate(50, 55, priority=0.1, reason="activity_peak")]),
        temporal_index=_temporal_index())

    assert row["signals"]["speech"] == pytest.approx(0.8)


def test_an_unasked_query_claims_no_relevance():
    [unasked] = refine.rank_refinement_candidates(
        _plan([_candidate(50, 55, reason="activity_peak")]),
        temporal_index=_temporal_index(),
        speech_texts=[{"start": 50.0, "end": 54.0,
                       "text": "open the box now"}])

    assert unasked["signals"]["query_relevance"] == 0.0
    assert unasked["query_asked"] is False

    [asked] = refine.rank_refinement_candidates(
        _plan([_candidate(50, 55, reason="activity_peak")]),
        temporal_index=_temporal_index(),
        speech_texts=[{"start": 50.0, "end": 54.0,
                       "text": "open the box now"}],
        query_terms=["box", "absent-term"])

    assert asked["signals"]["query_relevance"] == pytest.approx(0.5)
    assert asked["query_asked"] is True


def test_only_high_value_candidates_earn_a_model_call():
    ranked = refine.rank_refinement_candidates(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(40, 45, priority=0.01,
                          reason="activity_peak")]),
        temporal_index=_temporal_index())
    analyzer = _analyzer()

    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(40, 45, priority=0.01,
                          reason="activity_peak")]),
        temporal_index=_temporal_index(),
        budget_model_calls=4, value_threshold=0.3,
        analyze=analyzer, feed_back=lambda *args: None,
        content_digest="digest-test-aaa")

    assert analyzer.calls == [(20.0, 25.0)]
    assert [row["start"] for row in run["analyzed"]] == [20.0]
    skipped = {row["start"]: row["skip_reason"] for row in run["skipped"]}
    assert skipped[40.0] == refine.SKIP_BELOW_THRESHOLD
    assert ranked  # ranking is the basis the selection reads


def test_a_failed_window_is_a_skip_not_an_aborted_run():
    analyzer = _analyzer(fail_on=[(20.0, 25.0)])

    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(50, 55, reason="activity_peak")]),
        temporal_index=_temporal_index(),
        budget_model_calls=4, value_threshold=0.0,
        analyze=analyzer, feed_back=lambda *args: None,
        content_digest="digest-test-aaa")

    assert analyzer.calls == [(20.0, 25.0), (50.0, 55.0)]
    assert [row["start"] for row in run["analyzed"]] == [50.0]
    failed = [row for row in run["skipped"]
              if row["skip_reason"] == refine.SKIP_ANALYSIS_FAILED]
    assert [row["start"] for row in failed] == [20.0]
    assert "RuntimeError" in failed[0]["failure"]


def test_no_analyzer_records_no_analyzer_not_silence():
    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak")]),
        temporal_index=_temporal_index(),
        budget_model_calls=4, value_threshold=0.0,
        analyze=None, content_digest="digest-test-aaa")

    assert run["stop_reason"] == refine.StopReason.ANALYZER_UNAVAILABLE.value
    assert run["model_calls"] == 0
    assert [row["skip_reason"] for row in run["skipped"]] == [
        refine.SKIP_NO_ANALYZER]


def test_an_empty_plan_needs_no_refinement():
    run = refine.run_refinement(
        _plan([]), budget_model_calls=4, analyze=_analyzer(),
        content_digest="digest-test-aaa")

    assert run["stop_reason"] == refine.StopReason.NO_CANDIDATES.value
    assert run["model_calls"] == 0
    assert run["resolved_fraction"] == 1.0


def test_budget_leftovers_stay_listed_never_analyzed():
    analyzer = _analyzer()
    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(50, 55, reason="activity_peak"),
               _candidate(70, 75, priority=0.66,
                          reason="activity_peak")]),
        temporal_index=_temporal_index(),
        budget_model_calls=1, value_threshold=0.0,
        analyze=analyzer, feed_back=lambda *args: None,
        content_digest="digest-test-aaa")

    assert run["stop_reason"] == refine.StopReason.BUDGET_EXHAUSTED.value
    assert analyzer.calls == [(20.0, 25.0)]
    leftovers = [row for row in run["skipped"]
                 if row["skip_reason"] == refine.SKIP_BUDGET_EXHAUSTED]
    assert [row["start"] for row in leftovers] == [50.0, 70.0]


def test_an_already_covered_candidate_is_not_reanalyzed():
    analyzer = _analyzer()
    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(50, 55, reason="activity_peak")]),
        temporal_index=_temporal_index(),
        budget_model_calls=4, value_threshold=0.0,
        analyzed_windows=[[0.0, 30.0]],
        analyze=analyzer, feed_back=lambda *args: None,
        content_digest="digest-test-aaa")

    assert analyzer.calls == [(50.0, 55.0)]
    skipped = {row["start"]: row["skip_reason"] for row in run["skipped"]}
    assert skipped[20.0] == refine.SKIP_ALREADY_COVERED
    assert run["stop_reason"] == refine.StopReason.RESOLVED.value
    assert run["resolved_fraction"] == 1.0


def test_discovered_events_reach_the_source_memory_index(tmp_path):
    filed = []

    def feed_back(start_s, end_s, scenes, measured):
        filed.append((start_s, end_s, scenes, measured))
        source_memory.merge_scene_window(
            "digest-test-aaa", "/tmp/source-test.mp4",
            start_s=start_s, end_s=end_s, scenes=scenes,
            producer_version="semantic-refinement/1",
            source_digest="digest-test-aaa", measured=measured,
            root=tmp_path)

    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak")]),
        temporal_index=_temporal_index(),
        budget_model_calls=4, value_threshold=0.0,
        analyze=_analyzer(), feed_back=feed_back,
        content_digest="digest-test-aaa")

    assert filed and filed[0][:2] == (20.0, 25.0) and filed[0][3] is True
    assert run["filed_windows"] == 1
    stored = source_memory.read_scenes("digest-test-aaa", root=tmp_path)
    assert stored is not None
    assert [scene["start"] for scene in stored["scenes"]] == [21.0]
    coverage = [row for row in stored["window_coverage"]
                if row["start_s"] == 20.0 and row["end_s"] == 25.0]
    assert coverage and coverage[0]["status"] == "complete"


def test_a_refined_scene_is_complete_but_never_autonomous():
    reference = refine.reference_for_refined_event(
        {"start": 21.0, "end": 23.0, "location": "kitchen"},
        "refined-scene:21000:23000:0",
        content_digest="digest-test-aaa")

    assert reference.certainty == "inferred"
    assert reference.uncertainty == refine.REFINED_SCENE_UNCERTAINTY
    observations = {reference.event_id: {
        "id": reference.event_id, "asset_id": "asset-test-aaa",
        "interval": {"clock_id": "clock-test-aaa",
                     "start": 21000, "end": 23000}}}
    assert contract.audit_reference(reference, observations) == []
    gate = contract.gate_autonomy(
        [reference], autonomy="autonomous", observations=observations)
    assert gate["verdict"] == "needs_approval"


def test_a_refined_boundary_files_as_measured():
    reference = refine.reference_for_refined_event(
        {"timestamp": 22.0}, "refined-boundary:22000",
        content_digest="digest-test-aaa")

    assert reference.certainty == "measured"
    assert reference.precision == contract.PRECISION_TICK
    observations = {reference.event_id: {
        "id": reference.event_id, "asset_id": "asset-test-aaa",
        "interval": {"clock_id": "clock-test-aaa",
                     "start": 22000, "end": 22001}}}
    assert contract.audit_reference(reference, observations) == []


def test_the_loop_consumes_the_planners_own_candidates():
    plan = plan_semantic_windows(
        100, activity_map_from_temporal_index(100, None))
    assert plan["refinement_candidates"]

    run = refine.run_refinement(
        plan, budget_model_calls=2, value_threshold=0.0,
        analyze=_analyzer(), feed_back=lambda *args: None,
        content_digest="digest-test-aaa")

    assert run["candidates"] == len(plan["refinement_candidates"])
    assert run["model_calls"] == 2
    assert run["stop_reason"] == refine.StopReason.BUDGET_EXHAUSTED.value
    assert run["references"]
    for reference in run["references"]:
        problems = contract.audit_reference(
            reference, run["observations"])
        assert problems == []


# ── The A01 measurement: recorded plans, before vs after ─────────────


def _recorded_decisions(run):
    decisions = []
    references_by_decision = {}
    for reference in run["references"]:
        decision_id = f"cut-{reference['event_id']}"
        decisions.append({"decision_id": decision_id,
                          "evidence_ids": [reference["event_id"]]})
        references_by_decision[decision_id] = [reference]
    # One decision the refinement never touched: the engine's own
    # note-id grounding, which stays ungrounded in both columns.
    decisions.append({"decision_id": "revise-note",
                      "evidence_ids": ["revision-note:7"]})
    references_by_decision["revise-note"] = []
    return decisions, references_by_decision


def test_recorded_plans_before_and_after():
    run = refine.run_refinement(
        _plan([_candidate(20, 25, reason="activity_peak"),
               _candidate(50, 55, reason="activity_peak")]),
        temporal_index={
            "scene_boundaries": [{"timestamp": 22.0}],
            "speech_regions": [{"start": 20.0, "end": 25.0},
                               {"start": 50.0, "end": 55.0}],
        },
        budget_model_calls=4, value_threshold=0.0,
        analyze=_analyzer(), feed_back=lambda *args: None,
        content_digest="digest-test-aaa")
    decisions, references_by_decision = _recorded_decisions(run)

    report = refine.audit_before_after(
        decisions, run["observations"], {}, references_by_decision)

    before, after = report["before"], report["after"]
    assert before["proposed_edits"] == after["proposed_edits"] == 3
    assert before["grounded_edits"] == 0
    assert before["complete_references"] == 0
    assert after["grounded_edits"] == 2
    assert after["complete_references"] == 2
    assert after["inferred_references"] == 2
    reaching = report["refinement_candidates_reaching_execution"]
    assert reaching["before"] == 0
    assert reaching["after"] == 2
    rows = {row["decision_id"]: row for row in after["decisions"]}
    assert rows["revise-note"]["grounded"] is False
