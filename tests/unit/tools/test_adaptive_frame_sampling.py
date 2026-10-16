from library.tools.adaptive_frame_sampling import plan_adaptive_frames


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
