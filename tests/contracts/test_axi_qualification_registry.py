"""Defect-naming contracts for imported AXI qualification evidence."""
from __future__ import annotations

import json

import pytest

from library.tools import axi_qualification, edit_algebra


def _passed_report(operation_id="transition", backend="reel_touchup"):
    return {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": operation_id,
        "backend": backend,
        "result": "passed",
        "evidence": "tests/live/results/transition.json",
        "resolve_version": "21.1.0",
        "project_conditions": ["isolated qualification project"],
        "supported_media_types": ["video"],
        "expected_state": {"transition": "cross_dissolve"},
        "actual_state": {"transition": "cross_dissolve"},
        "preservation_checks": [{"name": "unrelated clips", "result": "passed"}],
        "preservation_limits": ["normal video clips"],
        "visual_verification": ["rendered transition verified"],
        "audio_verification": ["not_applicable: silent fixture"],
        "recovery_status": "passed",
    }


def test_transition_with_registered_executor_is_not_reported_as_planning_only():
    status = edit_algebra.status("transition")

    assert status.for_backend("reel_touchup") is not None
    assert status.implementation_status == "implemented_unqualified"


def test_every_operation_status_agrees_with_its_registered_backend_rows():
    for status in edit_algebra.OP_STATUS.values():
        assert status.defined is True
        assert status.compilable is bool(status.backends)
        if status.backends:
            assert status.implementation_status != "planning_only"
            assert all(row.compilable for row in status.backends)


def test_builtin_adapter_pairs_are_declared_in_the_algebra():
    from library.tools import edit_execution_adapters, resolve_axi_core

    edit_execution_adapters.register_builtin_edit_executors()
    for operation_id, backend in resolve_axi_core.registered_edit_executors():
        status = edit_algebra.status(operation_id)

        assert status is not None
        assert status.for_backend(backend) is not None


def test_live_evidence_import_enables_only_its_exact_operation_backend(tmp_path):
    report_path = tmp_path / "transition-result.json"
    registry_path = tmp_path / "registry.json"
    report_path.write_text(json.dumps(_passed_report()), encoding="utf-8")

    pair = axi_qualification.import_result(report_path, registry_path)
    records = axi_qualification.load_qualification_records(registry_path)

    assert pair == ("transition", "reel_touchup")
    assert set(records) == {pair}
    assert records[pair]["production_enabled"] is True


@pytest.mark.parametrize("field", [
    "resolve_version", "project_conditions", "supported_media_types",
    "expected_state", "actual_state", "preservation_checks",
    "preservation_limits", "visual_verification", "audio_verification",
    "recovery_status",
])
def test_import_refuses_a_pass_missing_required_evidence(field):
    report = _passed_report()
    report[field] = []

    with pytest.raises(axi_qualification.QualificationReportError):
        axi_qualification.parse_result(report)


def test_failed_live_result_cannot_produce_a_qualified_record():
    report = {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": "transition", "backend": "reel_touchup",
        "result": "failed", "evidence": "tests/live/results/transition.json",
    }

    pair, record = axi_qualification.parse_result(report)

    assert pair == ("transition", "reel_touchup")
    assert record["result"] == "failed"
    assert "production_enabled" not in record


def test_production_enablement_is_not_accepted_as_an_importer_input():
    report = _passed_report()
    report["production_enabled"] = False

    pair, record = axi_qualification.parse_result(report)

    assert pair == ("transition", "reel_touchup")
    assert "production_enabled" not in record


def test_registered_dispatch_refuses_a_backend_other_than_the_router_choice():
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _execute_registered_backend

    with pytest.raises(ValueError, match="but router selected 'reel_touchup'"):
        _execute_registered_backend(
            backend="resolve_axi", plan={"routing": {"backend": "reel_touchup"}},
            request=SimpleNamespace(), preparation=None,
            compiled=SimpleNamespace(operations=()), generation=None,
            snapshot={}, owner="contract-test")
