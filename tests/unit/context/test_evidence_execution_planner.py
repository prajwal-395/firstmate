from library.tools.analyzer_providers import (
    CapabilityGraph,
    CapabilityNode,
    EvidenceAvailable,
    EvidenceNeed,
    ExecutionProfile,
    MeasuredCost,
    MeasuredQuality,
    TaskRequest,
    default_capability_graph,
    execute_project_model_plan,
    plan_execution,
    targeted_producer_catalog,
)

BENCH = "planner-fixture-v1"


def _node(capability, *, output="object.detection", quality=.9, cost=4):
    return CapabilityNode(
        capability, frozenset({"frame", "object_query"}), frozenset({output}),
        frozenset({"named_object"}), quality=(MeasuredQuality(
            BENCH, "recall", quality, 10),),
        costs=(MeasuredCost(BENCH, "fixture-scope", cost, 128),), producer_id=capability,
    )


def _request(**changes):
    values = {"task": "named_object", "needs": (EvidenceNeed(
        "object.detection", "recall", .8),), "benchmark_id": BENCH,
        "budget_seconds": 10, "project_id": "fi01", "input_evidence": frozenset({
            "frame", "object_query"}), "cost_scope_id": "fixture-scope"}
    values.update(changes)
    return TaskRequest(**values)


def test_selects_cheapest_measured_capability_meeting_quality():
    graph = CapabilityGraph((_node("cheap", quality=.5, cost=1),
                             _node("qualified", quality=.9, cost=4),
                             _node("slow", quality=.95, cost=8)))

    plan = plan_execution(graph, _request())

    assert plan.status == "ready"
    assert [row.capability_id for row in plan.producers] == ["qualified"]
    assert plan.predicted_cost_seconds == 4


def test_sufficient_project_model_cache_runs_nothing():
    plan = plan_execution(CapabilityGraph((_node("producer"),)), _request(), (
        EvidenceAvailable("object.detection", "recall", .9, "project-model", "fi01"),))

    assert plan.status == "ready"
    assert plan.producers == ()
    assert plan.predicted_cost_seconds == 0
    assert plan.reused_cache[0].source == "project-model"


def test_cross_project_cache_is_ignored_without_explicit_authorisation():
    cache = EvidenceAvailable("object.detection", "recall", .9,
                              "project-model", "another-project")
    plan = plan_execution(CapabilityGraph((_node("producer"),)), _request(), (cache,))

    assert plan.status == "ready"
    assert plan.reused_cache == ()
    assert plan.producers[0].capability_id == "producer"
    authorised = plan_execution(
        CapabilityGraph((_node("producer"),)),
        _request(authorise_cross_project=True), (cache,))
    assert authorised.producers == ()
    assert authorised.reused_cache == (cache,)


def test_budget_excess_refuses_before_invocation():
    plan = plan_execution(CapabilityGraph((_node("producer", cost=11),)),
                          _request(budget_seconds=10))

    assert plan.status == "budget_exceeded"
    assert plan.producers[0].capability_id == "producer"
    assert "do not invoke" in plan.stopping_conditions[0]


def test_executor_does_not_invoke_budget_exceeded_producer(tmp_path):
    from library.tools.analyzer_providers import ProducerContext

    calls = []
    node = CapabilityNode(
        "producer", frozenset({"frame", "object_query"}),
        frozenset({"object.detection"}), frozenset({"named_object"}),
        quality=(MeasuredQuality(BENCH, "recall", .9, 10),),
        costs=(MeasuredCost(BENCH, "fixture-scope", 4, 128),),
        invoke=lambda context: calls.append(context) or {},
    )
    graph = CapabilityGraph((node,))
    plan = plan_execution(graph, _request(budget_seconds=3))

    result = execute_project_model_plan(
        graph, plan, ProducerContext(tmp_path, "fixture.mp4", "fixture-digest",
                                     10, "laptop"))

    assert result["status"] == "budget_exceeded"
    assert calls == []


def test_missing_measured_quality_abstains_instead_of_guessing():
    graph = CapabilityGraph((CapabilityNode(
        "unmeasured", frozenset(), frozenset({"object.detection"}),
        frozenset({"named_object"}),),))

    plan = plan_execution(graph, _request())

    assert plan.status == "abstain"
    assert plan.missing_evidence == ("object.detection",)


def test_profile_is_policy_over_the_same_graph():
    graph = CapabilityGraph((_node("producer"),))
    fast = plan_execution(graph, _request(profile=ExecutionProfile.FAST_DISCOVERY))
    precision = plan_execution(graph, _request(profile=ExecutionProfile.PRECISION_EXECUTION))

    assert fast.producers == precision.producers
    assert fast.producers[0].capability_id == "producer"
    assert fast.escalation_conditions != precision.escalation_conditions


def test_refuses_implicit_cross_project_scope():
    plan = plan_execution(CapabilityGraph((_node("producer"),)),
                          _request(project_id=""))

    assert plan.status == "abstain"
    assert "cross-project" in plan.reason


def test_targeted_catalog_exposes_producers_without_inventing_quality_or_cost():
    catalog = targeted_producer_catalog()

    assert {row.capability_id for row in catalog} >= {
        "object_detection", "ocr_selected_frames", "region_analysis",
        "mask_analysis", "speech_span_refinement", "m4_scene_window"}
    assert all(not row.quality and not row.costs for row in catalog)


def test_selected_producer_merges_incrementally_into_project_model(tmp_path):
    import json
    from pathlib import Path

    from library.tools import project_model_store
    from library.tools.analyzer_providers import ProducerContext

    model = json.loads(Path("tests/fixtures/project_model_e2e/expected-project-model.json")
                       .read_text(encoding="utf-8"))
    project_model_store.commit_project_model(
        tmp_path, model, expected_revision=0, reason="planner execution fixture")
    graph = CapabilityGraph((CapabilityNode(
        "producer", frozenset({"frame", "object_query"}),
        frozenset({"object.detection"}), frozenset({"named_object"}),
        quality=(MeasuredQuality(BENCH, "recall", .9, 10),),
        costs=(MeasuredCost(BENCH, "fixture-scope", 4, 128),),
        producer_id="fixture-detector",
        invoke=lambda context: {"provenance": [{
            "id": "prov-planner-execution", "producer": "fixture detector",
            "producer_version": "1", "source_digest": None,
            "recorded_at": "2026-10-14T00:00:00Z"}], "answer": "found"},
    ),))
    plan = plan_execution(graph, _request())

    result = execute_project_model_plan(
        graph, plan, ProducerContext(tmp_path, "fixture.mp4", "fixture-digest",
                                     10, "laptop"))

    revision, merged = project_model_store.load_project_model(tmp_path)
    assert result["status"] == "executed"
    assert result["steps"][0]["status"] == "merged"
    assert result["steps"][0]["answer"] == "found"
    assert revision == 2
    assert any(row["id"] == "prov-planner-execution" for row in merged["provenance"])


def test_measured_analyzer_plans_adapt_into_graph_nodes():
    from library.tools.analysis.open_vocabulary_detect import OBJECT_DETECTION_PLANS
    from library.tools.analyzer_providers import nodes_from_measured_plans

    nodes = nodes_from_measured_plans(
        capability_id="object_detection",
        benchmark_id="object-detector-tiering-2026-10-14",
        cost_scope_id="object-detector-tiering-2026-10-14:40-frames-148-boxes",
        measured_runs=40,
        tasks=frozenset({"named_object"}),
        input_evidence=frozenset({"frame", "object_query"}),
        plans=OBJECT_DETECTION_PLANS)

    graph = CapabilityGraph(nodes)
    request = _request(
        benchmark_id="object-detector-tiering-2026-10-14",
        needs=(EvidenceNeed("object.detection", "named_object_box_recall", .85),),
        budget_seconds=200,
        cost_scope_id="object-detector-tiering-2026-10-14:40-frames-148-boxes")
    plan = plan_execution(graph, request)

    assert plan.status == "ready"
    assert plan.producers[0].capability_id == "object_detection:owlv2_named_queries"
    assert plan.producers[0].interval == request.interval


def test_default_graph_contains_measured_detector_route_and_unmeasured_catalog():
    graph = default_capability_graph()
    request = _request(benchmark_id="object-detector-tiering-2026-10-14",
                       needs=(EvidenceNeed("object.detection",
                                          "named_object_box_recall", .85),),
                       budget_seconds=200,
                       interval=(70.0, 80.0),
                       cost_scope_id="object-detector-tiering-2026-10-14:40-frames-148-boxes")
    plan = plan_execution(graph, request)

    assert plan.status == "ready"
    assert plan.producers[0].capability_id == "object_detection:owlv2_named_queries"
    assert 145 < plan.predicted_cost_seconds < 147
    assert "ocr_selected_frames" in graph.nodes
    assert graph.nodes["ocr_selected_frames"].quality == ()

