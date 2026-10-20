import pytest

from library.tools.adaptive_frame_sampling import (
    edit_critical_events_from_index,
    plan_adaptive_frames,
)


def _reasons(plan):
    return {reason for row in plan["selection_reasons"].values()
            for reason in row}


def test_scene_speech_text_and_entity_edges_get_local_samples_and_gaps():
    plan = plan_adaptive_frames(
        20, 24, {"scene_boundaries": [{"timestamp": 7.0}],
                "speech_regions": [{"start": 4.0, "end": 5.0}],
                "ocr_changes": [{"timestamp": 11.0}],
                "entity_transitions": [{"time": 15.0}]},
        budget_frames=30)

    reasons = [reason for row in plan["selection_reasons"].values()
               for reason in row]
    assert {"scene_boundary", "speech_boundary", "text_change",
            "entity_transition"} <= set(reasons)
    assert plan["examined_frame_count"] == plan["selected_frame_count"]
    assert plan["unexamined_intervals"]
    assert plan["coverage_status"] == "sampled"
    assert "no exhaustive absence" in plan["claim_policy"]


def test_retrieval_candidate_refines_action_interval_with_budget_and_query():
    plan = plan_adaptive_frames(
        60, 30, query="show me the hand gesture", budget_frames=8,
        retrieval_candidates=[{"start": 31, "end": 33, "score": .9,
                               "evidence_type": "visual_retrieval"}],
        existing_sample_times=[0, 10])

    selected = plan["selected_source_times"]
    assert len(selected) <= 8
    assert any(31 <= t <= 33 for t in selected)
    assert plan["query"] == "show me the hand gesture"
    assert plan["remaining_budget"] == 8 - len(selected)
    assert plan["retrieval_candidates"][0]["score"] == .9


def test_static_activity_does_not_add_peak_samples_and_invalid_inputs_refuse():
    quiet = {"bins": [{"start": 0, "end": 20, "signals": {
        "motion": {"normalized": .1}, "novelty": {"normalized": .2}}}]}
    plan = plan_adaptive_frames(20, 25, activity_map=quiet, budget_frames=20)

    assert not any("_peak" in reason for reasons in
                   plan["selection_reasons"].values() for reason in reasons)
    try:
        plan_adaptive_frames(20, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero fps must refuse")


def test_camera_and_subject_motion_are_separate_only_with_reliable_geometry():
    values = [{"time": 3.0, "translation_x": .5, "translation_y": .1,
               "subject_energy": .4}]
    unqualified = plan_adaptive_frames(
        10, 25, {"camera_motion_decomposition": {"values": values}},
        budget_frames=10)
    qualified = plan_adaptive_frames(
        10, 25, {"camera_motion_decomposition": {
            "geometry_status": "reliable", "values": values}},
        budget_frames=10)

    assert not any("geometry_" in reason for reasons in
                   unqualified["selection_reasons"].values() for reason in reasons)
    qualified_reasons = {reason for reasons in
                         qualified["selection_reasons"].values() for reason in reasons}
    assert {"geometry_camera_motion", "geometry_subject_motion"} <= qualified_reasons


def test_a_speech_boundary_keeps_a_band_on_both_sides():
    """A boundary that falls between two sampled frames is a boundary
    nobody saw, and a speech start is the boundary a cut sits on most."""
    plan = plan_adaptive_frames(
        20, 24, {"speech_regions": [{"start": 4.0, "end": 5.0}]},
        budget_frames=30)

    assert {"speech_boundary", "speech_boundary_approach",
            "speech_boundary_aftermath"} <= _reasons(plan)
    near = [t for t in plan["selected_source_times"] if 3.0 <= t <= 6.0]
    assert len(near) >= 3


def test_scene_complexity_raises_the_density_where_the_footage_changed():
    """A fast-cut region is sampled denser than a static one. Complexity
    is measured from the transitions the index carries, not chosen."""
    cuts = [{"timestamp": float(t)} for t in (1.0, 1.5, 2.0, 2.5, 3.0)]
    plan = plan_adaptive_frames(
        40, 25, {"scene_boundaries": cuts}, budget_frames=40)

    busy = [t for t in plan["selected_source_times"] if t < 8.0]
    quiet = [t for t in plan["selected_source_times"] if t > 20.0]
    assert len(busy) > len(quiet)
    assert plan["scene_complexity"]["complex_bins"] >= 1
    assert "scene_complexity" in _reasons(plan)


def test_an_edit_critical_event_keeps_the_coverage_its_type_declares():
    """The band is the tolerance the event's own type declares, and the
    plan says what resolution it actually reached."""
    events = [{"event_type": "speech_start_end", "start": 4.0, "end": 5.0}]
    plan = plan_adaptive_frames(
        20, 24, {"speech_regions": [{"start": 4.0, "end": 5.0}]},
        budget_frames=30, edit_critical_events=events)

    band = next(row for row in plan["fidelity_bands"]
                if row["event_type"] == "speech_start_end")
    assert band["tolerance_seconds"] == 0.04
    assert band["boundaries"] == 2
    assert band["samples_selected"] >= 2
    assert band["achieved_resolution_seconds"] <= band["source_frame_period_seconds"]
    assert "fidelity_speech_start_end" in _reasons(plan)


def test_a_boundary_the_source_cannot_resolve_says_so():
    """A tolerance finer than one frame is the SOURCE's to resolve. The
    plan says the tolerance was not met rather than implying it was."""
    events = [{"event_type": "scene_transition", "start": 7.0, "end": 7.0}]
    plan = plan_adaptive_frames(
        20, 24, {"scene_boundaries": [{"timestamp": 7.0}]},
        budget_frames=30, edit_critical_events=events)

    band = next(row for row in plan["fidelity_bands"]
                if row["event_type"] == "scene_transition")
    assert band["tolerance_met"] is False
    assert band["achieved_resolution_seconds"] == pytest.approx(
        band["source_frame_period_seconds"])


def test_many_boundaries_keep_an_even_spread():
    """A busy type is thinned UNIFORMLY: the type keeps boundaries
    across the clip, not only the earliest ones."""
    regions = [{"start": float(t), "end": float(t) + 0.5}
               for t in range(0, 100)]
    events = edit_critical_events_from_index({"speech_regions": regions})
    plan = plan_adaptive_frames(
        100, 25, {"speech_regions": regions}, budget_frames=20,
        edit_critical_events=events)

    band = next(row for row in plan["fidelity_bands"]
                if row["event_type"] == "speech_start_end")
    assert band["thinning"] > 1
    assert band["boundaries_sampled"] < band["boundaries"]
    kept = sorted({t for t in plan["selected_source_times"]
                   if "fidelity_speech_start_end" in
                   plan["selection_reasons"][str(t)]})
    assert kept[0] < 20.0, "the spread kept only the earliest boundaries"
    assert kept[-1] > 80.0, "the spread kept only the earliest boundaries"
