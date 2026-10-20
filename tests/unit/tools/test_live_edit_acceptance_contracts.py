import importlib.util
import sys
from pathlib import Path


def _load_live_module(name):
    path = Path(__file__).resolve().parents[2] / "live" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


editorial_acceptance = _load_live_module("editorial_acceptance")
edit_phase_benchmark = _load_live_module("edit_phase_benchmark")


def test_acceptance_tasks_are_the_ten_ordinary_editorial_requests():
    assert len(editorial_acceptance.TASKS) == 10
    assert all(task.instruction and task.operation and task.production_entry
               for task in editorial_acceptance.TASKS)
    from tests.live.evaluation_levels import catalog

    levels = {row["level"]: row for row in catalog()}
    assert set(levels) == {1, 2, 3, 4}
    assert levels[4]["evidence_status"] == "not_measured_no_editorial_brief_scorer"


def test_acceptance_runner_requires_observed_receipt_and_first_attempt():
    operation_by_instruction = {
        task.instruction: task.operation
        for task in editorial_acceptance.TASKS
    }

    def driver(instruction, context):
        assert set(context) == {"project", "timeline", "available_capabilities"}
        return {
            "instruction": instruction,
            "receipt": {"status": "verified"},
            "checks": {"target_property_readback": True},
            "collateral_changes": [],
            "benchmark": {"attempts": 1},
            "discovery": {
                "operation": operation_by_instruction[instruction],
                "discovered_from_request": True,
            },
            "backend_selection": {
                "selected": "resolve_axi",
                "selection_correct": True,
            },
        }

    report = editorial_acceptance.run_tasks(driver)
    assert report["task_count"] == 10
    assert report["passed"] == 10
    assert report["evaluation"]["level"] == 2
    assert report["evaluation"]["agent_receives_expected_operation"]
    assert all(row["evaluation_level"] == 2 for row in report["results"])


def test_blind_requests_hide_scoring_keys_and_include_compounds():
    seen = []

    def driver(instruction, context):
        seen.append((instruction, context))
        # This sample callback intentionally has no oracle access. It records
        # a safe refusal until an actual agent supplies an operation plan.
        return {"receipt": {"status": "refused"}}

    report = editorial_acceptance.run_blind_requests(driver)
    assert len(seen) == 12
    assert all(set(context) == {
        "project", "timeline", "capability_discovery"
    } for _, context in seen)
    assert all("operation" not in context and "target" not in context
               for _, context in seen)
    assert report["evaluation"]["level"] == 3
    assert report["evaluation"]["compound_request_count"] == 2
    assert report["task_count"] == 12


def test_reliability_metrics_preserve_missing_evidence_as_not_measured():
    from tests.live import editorial_reliability_metrics

    metrics = editorial_reliability_metrics.summarize([
        {"id": "unmeasured", "status": "fail", "checks": {},
         "collateral_changes": []},
    ])
    assert metrics["rates"]["target_resolution"] == {
        "status": "not_measured", "value": None,
        "measured": 0, "total": 1, "not_measured": 1,
    }
    assert metrics["latency_seconds"]["status"] == "not_measured"
    assert metrics["latency_seconds"]["mean_instruction_to_verified"] is None


def test_phase_benchmark_keeps_retries_unrelated_work_and_missing_phases():
    measurement = edit_phase_benchmark.record_edit("simple_move", {
        "phases": {phase: 0.1 for phase in edit_phase_benchmark.PHASES},
        "attempts": 2, "full_rebuilds": 0, "items_touched": 1,
        "model_records_touched": 0, "unrelated_operations": 1,
        "render_seconds": 0, "model_seconds": 0, "total_seconds": 2,
    })
    assert measurement["retries"] == 1
    assert measurement["unrelated_operations"] == 1
    assert measurement["complete"]

    missing = edit_phase_benchmark.record_edit("trim", {})
    assert not missing["complete"]
    assert "missing or invalid timing: verification" in missing["errors"]
