"""Defect-naming contracts for imported AXI qualification evidence."""
from __future__ import annotations

import json
from pathlib import Path

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
        "qualification_predicates": [
            {"field": "resolve_version", "operator": "equals",
             "value": "21.1.0"},
        ],
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
    "recovery_status", "qualification_predicates",
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


def test_a_version_written_with_the_product_label_imports_as_the_version():
    """One reading of the version, whatever notation the report used.

    Resolve reports a bare dotted version; a report may carry the product
    label. The two name the same build, and the recorded condition is
    compared against a runtime that reports the bare one - so the import
    stores the version in the reading the comparison happens in.
    """
    labelled = _passed_report()
    labelled["resolve_version"] = "DaVinci Resolve 21.1.0.14"
    labelled["qualification_predicates"] = [
        {"field": "resolve_version", "operator": "equals",
         "value": "DaVinci Resolve 21.1.0.14"}]

    bare = _passed_report()
    bare["resolve_version"] = "21.1.0.14"
    bare["qualification_predicates"] = [
        {"field": "resolve_version", "operator": "equals",
         "value": "21.1.0.14"}]

    _pair, labelled_record = axi_qualification.parse_result(labelled)
    _pair, bare_record = axi_qualification.parse_result(bare)

    assert labelled_record["resolve_version"] == "21.1.0.14"
    assert labelled_record == bare_record


def test_a_one_of_version_predicate_imports_in_the_same_reading():
    report = _passed_report()
    report["qualification_predicates"] = [
        {"field": "resolve_version", "operator": "one_of",
         "value": ["DaVinci Resolve 21.1.0.14", "21.1.0.15"]}]

    _pair, record = axi_qualification.parse_result(report)

    assert record["qualification_predicates"][0]["value"] == [
        "21.1.0.14", "21.1.0.15"]


def test_every_promoted_row_is_derived_from_a_committed_evidence_report():
    """The registry stores observations; the reports are the observations.

    A promoted row whose evidence report is not in the repository is a
    promotion nobody can read back: the registry says a record exists
    and the report it came from is somewhere else. Every production
    enabled row must re-import from its committed report to the record
    the registry holds - so the registry cannot drift from the evidence.
    """
    directory = (Path(__file__).resolve().parents[1] / "live" / "reports"
                 / "registry")
    reports = {path.stem: path for path in directory.glob("*.json")}
    assert reports, "the registry holds no evidence report"

    registry = json.loads(
        (Path(__file__).resolve().parents[2] / "library" / "tools"
         / "axi_qualification_records.json").read_text(encoding="utf-8"))
    stored = registry["records"]

    promoted = {
        key for key, record in stored.items()
        if record.get("result") == "passed"
    }
    assert promoted, "the registry promotes nothing"

    for key in sorted(promoted):
        op_id, backend = key.split("::", 1)
        stem = f"{op_id}__{backend}"
        assert stem in reports, (
            f"{op_id} on {backend} is promoted with no committed evidence "
            f"report named {stem}.json under {directory}")
        report_path = reports[stem]
        parsed = json.loads(report_path.read_text(encoding="utf-8"))
        assert parsed["operation_id"] == op_id
        assert parsed["backend"] == backend
        assert parsed["result"] == "passed"
        _pair, record = axi_qualification.parse_result(parsed)
        assert record == stored[key], (
            f"{op_id} on {backend}: the committed report and the registry "
            f"row disagree")


def test_registered_dispatch_refuses_a_backend_other_than_the_router_choice():
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _execute_registered_backend

    with pytest.raises(ValueError, match="but router selected 'reel_touchup'"):
        _execute_registered_backend(
            backend="resolve_axi", plan={"routing": {"backend": "reel_touchup"}},
            request=SimpleNamespace(), preparation=None,
            compiled=SimpleNamespace(operations=()), generation=None,
            snapshot={}, owner="contract-test")
