"""VI-12 acceptance: answered queries do no work; resolvable gaps refine."""

from library.tools.query_directed_acquisition import (
    execute_query_acquisition,
    plan_query_acquisition,
    query_with_acquisition,
)


def _plan(status, *, missing=()):
    return ({"status": status, "required_missing_lanes": list(missing)},
            {"required_lanes": ["m7"],
             "scope": {"start_s": 12, "end_s": 18}})


def test_answered_result_is_no_run_and_does_not_call_producers():
    answer, query = _plan("present")
    plan = plan_query_acquisition(answer, query, source_digest="digest-fixture",
                                  duration_seconds=60,
                                  available_lanes=["m7"],
                                  producer_budgets={"m7": 60})
    calls = []
    report = execute_query_acquisition(
        plan, cached=lambda **kw: calls.append(("cache", kw)),
        producers={"m7": lambda **kw: calls.append(("producer", kw))})

    assert plan["action"] == "no_run"
    assert report["action"] == "no_run"
    assert calls == []


def test_not_yet_analysed_schedules_only_required_lane_and_interval():
    answer, query = _plan("not_yet_analysed", missing=["m7", "m1"])
    plan = plan_query_acquisition(
        answer, query, source_digest="digest-fixture", duration_seconds=60,
        available_lanes=["m7"],
        producer_budgets={"m7": 20}, max_cost_seconds=5)

    assert plan["action"] == "refine"
    assert plan["required_lanes"] == ["m7"]
    assert plan["steps"] == [{"lane": "m7",
                              "interval": {"start_s": 12.0, "end_s": 18.0},
                              "cost_budget_seconds": 2.0,
                              "order": ["cached", "targeted"]}]
    assert plan["deferred_lanes"] == []


def test_windowed_producer_plan_expands_to_exact_model_window_and_cost():
    answer, query = _plan("not_yet_analysed", missing=["m4"])
    query["required_lanes"] = ["m4"]
    plan = plan_query_acquisition(
        answer, query, source_digest="digest-fixture", duration_seconds=60,
        available_lanes=["m4"], producer_budgets={"m4": 90},
        window_seconds_by_lane={"m4": 10},
        producer_cost_per_window_seconds={"m4": 90}, max_cost_seconds=90)
    assert plan["steps"] == [{
        "lane": "m4", "interval": {"start_s": 10.0, "end_s": 20.0},
        "cost_budget_seconds": 90.0, "order": ["cached", "targeted"]}]

    query["scope"] = {"start_s": 5, "end_s": 25}
    plan = plan_query_acquisition(
        answer, query, source_digest="digest-fixture", duration_seconds=60,
        available_lanes=["m4"], producer_budgets={"m4": 270},
        window_seconds_by_lane={"m4": 10},
        producer_cost_per_window_seconds={"m4": 90}, max_cost_seconds=270)
    assert [step["interval"] for step in plan["steps"]] == [
        {"start_s": 0.0, "end_s": 10.0},
        {"start_s": 10.0, "end_s": 20.0},
        {"start_s": 20.0, "end_s": 30.0},
    ]


def test_cached_evidence_is_tried_before_targeted_model_and_metrics_are_kept():
    answer, query = _plan("not_yet_analysed", missing=["m7"])
    plan = plan_query_acquisition(answer, query, source_digest="digest-fixture",
                                  duration_seconds=60,
                                  available_lanes=["m7"],
                                  producer_budgets={"m7": 60})
    order = []

    def cached(**kwargs):
        order.append("cached")

    def acquire(**kwargs):
        order.append("targeted")
        return {"cost_seconds": 1.25, "peak_rss_gb": 7.5,
                "model_version": "local-v1", "samples": 4,
                "coverage": "complete", "observations": ["obs-1"]}

    report = execute_query_acquisition(plan, cached=cached,
                                       producers={"m7": acquire})
    assert order == ["cached", "cached", "targeted"]
    assert report["steps"][0]["status"] == "acquired"
    assert report["steps"][0]["model_version"] == "local-v1"
    assert report["cost_seconds"] == 1.25
    assert report["peak_rss_gb"] == 7.5


def test_inconclusive_query_without_named_resolvable_ambiguity_is_no_run():
    answer, query = _plan("inconclusive")
    plan = plan_query_acquisition(answer, query, source_digest="digest-fixture",
                                  duration_seconds=60,
                                  available_lanes=["m7"],
                                  producer_budgets={"m7": 60})
    assert plan["action"] == "no_run"


def test_specific_ambiguity_and_budget_are_enforced_before_model_call():
    answer, query = _plan("inconclusive")
    plan = plan_query_acquisition(
        answer, query, source_digest="digest-fixture", duration_seconds=60,
        resolvable_ambiguities=[{"lane": "m7", "start_s": 15, "end_s": 16}],
        available_lanes=["m7"], producer_budgets={"m7": 60},
        max_cost_seconds=0.5, max_peak_rss_gb=8)
    assert plan["action"] == "no_run"
    assert "budget" in plan["reason"]


def test_runtime_rss_over_ceiling_is_deferred_and_not_counted_as_acquired():
    answer, query = _plan("not_yet_analysed", missing=["m7"])
    plan = plan_query_acquisition(answer, query, source_digest="digest-fixture",
                                  duration_seconds=60,
                                  available_lanes=["m7"],
                                  producer_budgets={"m7": 60})
    report = execute_query_acquisition(
        plan, cached=lambda **kwargs: None,
        producers={"m7": lambda **kwargs: {
            "cost_seconds": 1, "peak_rss_gb": 8.1,
            "model_version": "local-v1", "observations": ["bad"]}})
    assert report["steps"][0]["status"] == "deferred"
    assert report["steps"][0]["reason"] == "producer exceeded RSS or query cost budget"


def test_query_acquires_one_scoped_lane_refreshes_model_and_changes_answer(tmp_path):
    import copy
    import json
    from pathlib import Path

    from library.tools import evidence_contract, evidence_query, project_model_store

    model = json.loads(Path(
        "tests/fixtures/project_model_e2e/expected-project-model.json"
    ).read_text(encoding="utf-8"))
    scope = {"asset_id": "asset-ada", "clock_id": "clock-ada",
             "start_s": 0, "end_s": 2}
    query = evidence_query.compile_plan(
        [{"predicate": "event_span", "event": "speaking",
          "participant_id": "person-ada"}], scope=scope)
    before = evidence_query.evidence_for(model, query)
    assert before["status"] == evidence_query.NOT_YET_ANALYSED
    assert before["required_missing_lanes"] == ["m7"]

    calls = []
    acquired_model = {}
    project_model_store.commit_project_model(
        tmp_path, model, expected_revision=0, reason="fixture before query")

    def producer(*, lane, start_s, end_s, budget_seconds, max_peak_rss_gb):
        calls.append((lane, start_s, end_s))
        record = evidence_contract.new_measurement(
            asset_id="asset-ada", clock_id="clock-ada",
            start_tick=round(start_s * 24), end_tick=round(end_s * 24),
            measurement_type="event.speaking_span",
            value={"predicate": "speaking", "voice_track": "voice-ada",
                   "face_track": "face-ada"},
            method="fixture-event-spans-v1",
            provenance_id="prov-fixture-source-m7",
            target_entity_id="person-ada",
            coverage=evidence_contract.CoverageKind.COMPLETE,
            coverage_samples=1)
        acquired_model["record"] = evidence_contract.to_legacy_observation(
            record, "obs-acquired-speaking", "event_span")
        acquired_model["record"]["payload"]["evidence_contract"]["freshness"] = "fresh"
        acquired_model["record"]["payload"]["evidence_contract"]["producer_version"] = "fixture-event-spans-v1"
        return {"cost_seconds": 0.01, "peak_rss_gb": 0.1,
                "producer_version": "fixture-event-spans-v1"}

    def refresh(current):
        updated = copy.deepcopy(current)
        updated["observations"].append(acquired_model["record"])
        project_model_store.commit_project_model(
            tmp_path, updated, expected_revision=1,
            reason="bounded query acquisition")
        return project_model_store.load_project_model(tmp_path)[1]

    result = query_with_acquisition(
        model, query, source_digest="fixture-source-ada", duration_seconds=2,
        cached=lambda **kwargs: None, producers={"m7": producer},
        refresh_model=refresh, max_cost_seconds=1,
        available_lanes=["m7"], producer_budgets={"m7": 1})

    assert result["answer"]["status"] == evidence_query.NOT_YET_ANALYSED
    assert result["acquisition_plan"]["steps"] == [{
        "lane": "m7", "interval": {"start_s": 0.0, "end_s": 2.0},
        "cost_budget_seconds": 1.0, "order": ["cached", "targeted"]}]
    assert calls == [("m7", 0.0, 2.0)]
    assert result["requery"]["status"] == evidence_query.PRESENT
    assert result["edit_ready"] is True


def test_query_with_acquisition_skips_when_current_answer_is_sufficient():
    import json
    from pathlib import Path

    from library.tools import evidence_query

    model = json.loads(Path(
        "tests/fixtures/project_model_e2e/expected-project-model.json"
    ).read_text(encoding="utf-8"))
    # A terminal measurement query does not authorize more evidence.
    plan = evidence_query.compile_plan([{
        "predicate": "measurement", "measurement_type": "person.face_box"}],
        scope={"asset_id": "asset-ada", "clock_id": "clock-ada",
               "start_s": 0, "end_s": 2})
    calls = []
    result = query_with_acquisition(
        model, plan, source_digest="fixture-source-ada", duration_seconds=2,
        cached=lambda **kwargs: calls.append("cache"),
        producers={"m3": lambda **kwargs: calls.append("producer")},
        refresh_model=lambda value: calls.append("refresh"),
        max_cost_seconds=1, available_lanes=["m3"],
        producer_budgets={"m3": 1})
    assert result["acquisition"]["action"] == "no_run"
    assert result["requery"] is None
    assert calls == []


def test_production_m4_acquisition_merges_one_fixture_window_and_requeries(tmp_path):
    import json
    from pathlib import Path

    from library.tools import (
        evidence_query,
        project_model_acquisition,
        project_model_store,
        source_memory,
    )

    model = json.loads(Path(
        "tests/fixtures/project_model_e2e/expected-project-model.json"
    ).read_text(encoding="utf-8"))
    model["clocks"][0]["ticks_per_second"] = 1000
    model["clocks"][0]["frame_rate"] = None
    asset = model["assets"][0]
    digest = asset["content_digest"]
    plan = evidence_query.compile_plan([{
        "predicate": "measurement",
        "measurement_type": "video.scene_segment",
    }], scope={"asset_id": asset["id"], "clock_id": "clock-ada",
               "start_s": 0, "end_s": 2})
    assert evidence_query.evidence_for(model, plan)["status"] == "not_yet_analysed"
    project_model_store.commit_project_model(
        tmp_path, model, expected_revision=0, reason="fixture before acquisition")
    windows = []

    def acquire_window(*, start_s, end_s, source_file):
        windows.append((start_s, end_s))
        return {"measured": True, "scenes": [{
            "start": start_s, "end": end_s, "location": "a sunlit room",
            "type": "interior", "lighting": "natural",
            "notable_features": ["window"]}]}

    result = project_model_acquisition.query_project(
        tmp_path, model, plan, source_digest=digest,
        source_file="/synthetic/fixture.mp4", duration_seconds=2,
        max_cost_seconds=90, memory_root=tmp_path / "memory",
        window_analyzer=acquire_window)

    assert result["answer"]["status"] == "not_yet_analysed"
    assert result["acquisition_plan"]["steps"] == [{
        "lane": "m4", "interval": {"start_s": 0.0, "end_s": 2.0},
        "cost_budget_seconds": 90.0, "order": ["cached", "targeted"]}]
    assert windows == [(0.0, 2.0)]
    memory = source_memory.read_scenes(digest, tmp_path / "memory")
    assert memory["window_coverage"] == [{
        "start_s": 0.0, "end_s": 2.0, "status": "complete",
        "producer_version": project_model_acquisition.PRODUCER_VERSION,
        "source_digest": digest}]
    revision, updated = project_model_store.load_project_model(tmp_path)
    assert revision == 2
    assert next(row for row in updated["provenance"]
                if row["id"] == f"prov-{digest}-m4")["producer_version"] == (
                    project_model_acquisition.PRODUCER_VERSION)
    assert evidence_query.evidence_for(updated, plan)["status"] == "present"
    assert result["requery"]["status"] == "present"
    assert result["edit_ready"] is True

    rows, provenance = project_model_acquisition._m4_rows(
        updated, digest, plan["scope"], asset_id=asset["id"],
        clock_id="clock-ada", duration=2, memory_root=tmp_path / "memory")
    assert project_model_store.merge_evidence_records(
        tmp_path, observations=rows, provenance=provenance,
        expected_revision=revision) == revision
    assert len(project_model_store.load_project_model(tmp_path)[1]["observations"]) == len(updated["observations"])


def test_production_m4_acquisition_skips_when_answer_is_already_present(tmp_path):
    import json
    from pathlib import Path

    from library.tools import evidence_query, project_model_acquisition

    model = json.loads(Path(
        "tests/fixtures/project_model_e2e/expected-project-model.json"
    ).read_text(encoding="utf-8"))
    asset = model["assets"][0]
    plan = evidence_query.compile_plan([{
        "predicate": "measurement", "measurement_type": "person.face_box"}],
        scope={"asset_id": asset["id"], "clock_id": "clock-ada",
               "start_s": 0, "end_s": 2})
    calls = []
    result = project_model_acquisition.query_project(
        tmp_path, model, plan, source_digest=asset["content_digest"],
        source_file="/unused.mp4", duration_seconds=2,
        max_cost_seconds=90, memory_root=tmp_path / "memory",
        window_analyzer=lambda **kwargs: calls.append(kwargs))
    assert result["acquisition"]["action"] == "no_run"
    assert result["requery"] is None
    assert calls == []
