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


def test_acceptance_runner_requires_observed_receipt_and_first_attempt():
    def driver(instruction, _task):
        return {
            "instruction": instruction,
            "receipt": {"status": "verified"},
            "checks": {"target_property_readback": True},
            "collateral_changes": [],
            "benchmark": {"attempts": 1},
            "discovery": {
                "operation": _task.operation,
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
