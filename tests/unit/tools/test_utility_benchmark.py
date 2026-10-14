from library.tools import utility_benchmark


def test_every_utility_phase_has_explicit_measurement_fields():
    row = utility_benchmark._phase(
        "final_edit_execution", status="unmeasured",
        detail={"reason": "Resolve is unavailable in this run"})

    assert tuple(phase for phase in utility_benchmark.PHASES) == (
        "cold_ingest", "warm_ingest", "first_query", "subsequent_queries",
        "edit_preparation", "final_edit_execution")
    assert row == {
        "phase": "final_edit_execution",
        "status": "unmeasured",
        "wall_s": None,
        "peak_rss_mb": None,
        "model_invocations_by_model": None,
        "cache_hits": None,
        "detail": {"reason": "Resolve is unavailable in this run"},
    }


def test_measured_phase_keeps_zero_distinct_from_unmeasured():
    row = utility_benchmark._phase(
        "subsequent_queries", status="measured", wall_s=0.0,
        peak_rss_mb=100.0, model_invocations_by_model={}, cache_hits=0)

    assert row["wall_s"] == 0.0
    assert row["cache_hits"] == 0
    assert row["model_invocations_by_model"] == {}
