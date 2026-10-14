"""VI-12 acceptance: answered queries do no work; resolvable gaps refine."""

from library.tools.query_directed_acquisition import (
    execute_query_acquisition,
    plan_query_acquisition,
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
