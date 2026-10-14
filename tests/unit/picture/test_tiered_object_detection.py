from library.tools.analysis import open_vocabulary_detect as detector


def test_vision_is_always_consulted_and_owl_promotes_only_for_named_object(
        monkeypatch):
    calls = []

    def fake_detect(name, image, phrases, *, score_threshold):
        calls.append(name)
        return [detector.Detection(phrases[0], [.1, .1, .4, .4], .8,
                                   detector=name)]

    monkeypatch.setattr(detector, "detect_image", fake_detect)
    rows = detector.detect_image_tiered(
        "frame.jpg", ["laptop"], minimum_fidelity=.2,
        required_output_evidence_types=frozenset({
            "object.foreground_candidate"}),
    )

    assert calls == [detector.VISION_FOREGROUND]
    assert rows.plan.plan_id == "vision_nominations"
    assert rows.detections[0].tier_id == "fast"
    assert rows.detections[0].promoted_because is None

    calls.clear()
    rows = detector.detect_image_tiered(
        "frame.jpg", ["laptop"], named_object_requested=True,
        minimum_fidelity=.2)

    assert calls == [detector.OWLV2]
    assert rows.plan.plan_id == "owlv2_named_queries"
    assert [row.tier_id for row in rows.detections] == ["high_fidelity"]
    assert rows.detections[-1].promoted_because is None

    calls.clear()
    rows = detector.detect_image_tiered(
        "frame.jpg", ["laptop"], named_object_requested=True,
        minimum_fidelity=.85,
        required_output_evidence_types=frozenset({
            "object.foreground_candidate", "object.detection"}),
    )

    assert calls == [detector.VISION_FOREGROUND, detector.OWLV2]
    assert rows.plan.plan_id == "vision_then_owlv2"
    assert rows.detections[-1].promoted_because == "named_object_or_query_need"


def test_edit_critical_inconclusive_object_query_promotes_only_named_windows(
        monkeypatch):
    intervals = []

    def producer(*, lane, start_s, end_s, budget_seconds, max_peak_rss_gb):
        intervals.append((lane, start_s, end_s))
        return {"measured": True, "cost_seconds": 1,
                "peak_rss_gb": .1, "model_version": "owl-test"}

    result = detector.acquire_query_detections(
        {"status": "inconclusive"},
        {"scope": {"start_s": 21, "end_s": 28}},
        source_digest="sha256:source", duration_seconds=60,
        ambiguity_windows=[{"start_s": 23, "end_s": 25}],
        cached=lambda **kwargs: None, producer=producer,
        max_cost_seconds=20, cost_budget_seconds=10,
        window_seconds=10,
    )

    assert result["action"] == "executed"
    assert intervals == [("object_detection", 20.0, 30.0)]
    assert result["steps"][0]["status"] == "acquired"


def test_query_acquisition_refuses_unbounded_detection_promotion():
    called = []
    result = detector.acquire_query_detections(
        {"status": "not_yet_analysed"}, {"scope": {}},
        source_digest="sha256:source", duration_seconds=60,
        ambiguity_windows=[], cached=lambda **kwargs: None,
        producer=lambda **kwargs: called.append(kwargs), max_cost_seconds=20,
        cost_budget_seconds=10, window_seconds=10,
    )

    assert result["action"] == "no_run"
    assert called == []


def test_empty_cheap_result_is_not_reported_as_absence(monkeypatch):
    monkeypatch.setattr(detector, "detect_image", lambda *args, **kwargs: [])

    result = detector.detect_image_tiered(
        "frame.jpg", ["laptop"], minimum_fidelity=.2,
        required_output_evidence_types=frozenset({
            "object.foreground_candidate"}),
    )

    assert result.status == "not_yet_analysed"
    assert result.detections == ()


def test_measured_plan_planner_refuses_a_quality_bar_no_plan_meets():
    import pytest

    from library.tools.analyzer_providers import ProviderSelectionError

    with pytest.raises(ProviderSelectionError, match="no measured object_detection plan"):
        detector.select_execution_plan(
            detector.OBJECT_DETECTION_PLANS,
            fidelity_metric="named_object_box_recall",
            minimum_fidelity=.95,
        )


def test_high_recall_named_query_selects_owlv2_alone():
    plan = detector.select_execution_plan(
        detector.OBJECT_DETECTION_PLANS,
        fidelity_metric="named_object_box_recall", minimum_fidelity=.85,
        required_output_evidence_types=frozenset({"object.detection"}),
    )

    assert plan.plan_id == "owlv2_named_queries"
    assert plan.time_seconds == 145.55284595899866 / 40


def test_vision_is_selected_when_its_nomination_output_meets_the_task():
    plan = detector.select_execution_plan(
        detector.OBJECT_DETECTION_PLANS,
        fidelity_metric="named_object_box_recall", minimum_fidelity=.2,
        required_output_evidence_types=frozenset({
            "object.foreground_candidate"}),
    )

    assert plan.plan_id == "vision_nominations"
