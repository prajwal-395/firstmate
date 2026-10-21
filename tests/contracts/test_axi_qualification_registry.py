"""Defect-naming contracts for imported AXI qualification evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import axi_qualification, edit_algebra
from library.tools import axi_qualification_dimensions as dimensions


def _coverage(**overrides) -> dict:
    """A coverage that names every dimension, with measured values."""
    measured = {field: ["video"] for field in dimensions.DIMENSIONS}
    measured.update({
        "resolve_version": ["21.1.0"],
        "media_type": ["video"],
        "clip_composition": ["single_video_clip"],
        "linked_audio_configuration": ["no_linked_audio"],
        "track_layout": ["video"],
        "track_occupancy": ["all_populated"],
        "timeline_density": ["light"],
        "timeline_structure": ["single_item_timeline"],
        "project_settings": ["landscape_1920x1080"],
        "frame_rate": ["24"],
        "existing_effects": ["none"],
    })
    measured.update(overrides)
    return {"dimensions": measured,
            "configurations": ["populated_1080p24"],
            "uncovered": {}}


def _passed_report(operation_id="transition", backend="reel_touchup"):
    return {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": operation_id,
        "backend": backend,
        "result": "passed",
        "evidence": "tests/live/results/transition.json",
        "resolve_version": "21.1.0",
        "environment": {"resolve_version": "21.1.0", "platform": "darwin"},
        "semantic_boundary": {
            "applies_to": ["one transition at one named cut"],
            "does_not_apply_to": ["a cut that carries a grade"],
        },
        "coverage": _coverage(),
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


def _committed_evidence_reports() -> dict:
    """Every committed evidence report, indexed by the pair it qualifies.

    A report is EVIDENCE when the importer accepts it: it states its
    environment, its semantic boundary, its coverage and its
    predicates. A report the importer refuses is a document, not
    evidence.
    """
    directory = Path(__file__).resolve().parents[1] / "live" / "reports"
    reports: dict = {}
    for path in sorted(directory.glob("**/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            pair, record = axi_qualification.parse_result(payload)
        except axi_qualification.QualificationReportError:
            continue
        reports.setdefault(pair, []).append((path, record))
    return reports


def test_every_promoted_row_is_derived_from_a_committed_evidence_report():
    """The registry stores observations; the reports are the observations.

    A promoted row whose evidence report is not in the repository is a
    promotion nobody can read back: the registry says a record exists
    and the report it came from is somewhere else. Every production
    enabled row must re-import from a committed report to the record
    the registry holds - so the registry cannot drift from the evidence.

    The report that backs a row is not always the one in the registry
    directory: the product-format lane commits its own evidence beside
    its own reports, and a promotion names the evidence it was imported
    from. So the search is over every committed evidence report, and
    the test says WHICH report backs each row - a mapping a reader can
    check, not a count.
    """
    reports = _committed_evidence_reports()
    assert reports, "the repository holds no committed evidence report"

    registry = json.loads(
        (Path(__file__).resolve().parents[2] / "library" / "tools"
         / "axi_qualification_records.json").read_text(encoding="utf-8"))
    stored = registry["records"]

    promoted = {
        key for key, record in stored.items()
        if record.get("result") == "passed"
    }
    assert promoted, "the registry promotes nothing"

    backing = {}
    for key in sorted(promoted):
        op_id, backend = key.split("::", 1)
        matches = [path for path, record
                   in reports.get((op_id, backend), [])
                   if record == stored[key]]
        assert matches, (
            f"{op_id} on {backend} is promoted with no committed evidence "
            f"report that re-imports to the row the registry holds")
        backing[key] = str(matches[0].relative_to(
            Path(__file__).resolve().parents[1]))
    assert backing, "no promoted row is backed by a committed report"


def test_registered_dispatch_refuses_a_backend_other_than_the_router_choice():
    from types import SimpleNamespace

    from library.tools.resolve_axi_core import _execute_registered_backend

    with pytest.raises(ValueError, match="but router selected 'reel_touchup'"):
        _execute_registered_backend(
            backend="resolve_axi", plan={"routing": {"backend": "reel_touchup"}},
            request=SimpleNamespace(), preparation=None,
            compiled=SimpleNamespace(operations=()), generation=None,
            snapshot={}, owner="contract-test")


# ── The boundary is explicit, and it is a state ──────────────────────


def test_a_predicate_may_not_name_the_project_the_evidence_was_earned_in():
    """A project is not a state.

    A predicate that names a project restricts an operation to the place
    the evidence was earned, so the operation is qualified for a
    fixture and for nothing else. That is not a qualification boundary,
    and it is what the three shipped records did.
    """
    report = _passed_report()
    report["qualification_predicates"] = [
        {"field": "project_name", "operator": "equals",
         "value": "Ren Qualification"}]

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="identity"):
        axi_qualification.parse_result(report)


def test_a_predicate_may_not_name_a_value_the_measurement_cannot_produce():
    """A claim the runtime can never satisfy is a closed route.

    The shipped records carried values like "seed_aroll_plus_placed_cover"
    and "video + audio": prose the measurement never emits. The route
    read as open and could never be taken.
    """
    report = _passed_report()
    report["qualification_predicates"] = [
        {"field": "clip_composition", "operator": "equals",
         "value": "seed_aroll_plus_placed_cover"}]

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="that dimension can measure"):
        axi_qualification.parse_result(report)


def test_a_predicate_may_not_reach_past_the_coverage_that_backs_it():
    """The coverage is the evidence; the predicate is the claim.

    A claim wider than the evidence is refused at the door, so a record
    cannot promise a state nobody ran.
    """
    report = _passed_report()
    report["qualification_predicates"] = [
        {"field": "media_type", "operator": "one_of",
         "value": ["video", "audio"]}]

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="coverage"):
        axi_qualification.parse_result(report)


def test_a_report_must_name_every_dimension_it_covers():
    """A dimension the coverage leaves out is a boundary it does not have."""
    report = _passed_report()
    coverage = report["coverage"]
    coverage["dimensions"].pop("existing_effects")

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="existing_effects"):
        axi_qualification.parse_result(report)


def test_a_report_must_state_its_environment():
    """The Resolve and the machine are part of the claim."""
    report = _passed_report()
    report["environment"] = {"resolve_version": "21.1.0"}

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="platform"):
        axi_qualification.parse_result(report)


def test_a_report_must_state_both_halves_of_its_semantic_boundary():
    """A boundary that lists only what it covers covers everything else."""
    report = _passed_report()
    report["semantic_boundary"] = {"applies_to": ["one transition"]}

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="does_not_apply_to"):
        axi_qualification.parse_result(report)


def test_the_report_schema_that_predates_the_boundaries_is_refused():
    """The old format carried no boundary, so it cannot qualify anything."""
    report = _passed_report()
    report["schema"] = axi_qualification.LEGACY_REPORT_SCHEMA

    with pytest.raises(axi_qualification.QualificationReportError,
                       match="predates"):
        axi_qualification.parse_result(report)


def test_the_derived_predicates_are_the_coverage():
    """A report that declares no predicate gets the whole of its coverage."""
    report = _passed_report()

    _pair, record = axi_qualification.parse_result(report)

    fields = {predicate["field"] for predicate in
              record["qualification_predicates"]}
    assert fields == set(dimensions.DIMENSIONS)


def test_every_registered_record_names_only_measurable_predicates():
    """The shipped registry is held to the same vocabulary as a new report.

    A record whose predicate names a state the measurement cannot
    produce reads as qualified and can never be routed. The audit is the
    GATE: a row whose evidence passed has to keep the qualification it
    claims. A row whose evidence FAILED is reported under `failed` - it
    qualifies nothing, and it is not a broken boundary.
    """
    audit = axi_qualification.audit()

    assert audit["ineligible"] == 0, audit["rows"]
    for row in audit["rows"]:
        assert row["production_eligible"], row


def _registry_with(record: dict, tmp_path) -> Path:
    """A one-row registry, for the audit to read."""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(
        {"schema": axi_qualification.REGISTRY_SCHEMA,
         "records": {"clip.delete::resolve_axi": record},
         "compound_records": {}}), encoding="utf-8")
    return path


def test_a_row_whose_evidence_failed_is_not_qualified_and_not_ineligible(
        tmp_path):
    """A failed attempt is an honest outcome, not a broken boundary.

    The two are different facts: a row whose evidence did not pass
    qualifies nothing and says so, while a row whose evidence PASSED
    and whose boundary is broken claims a qualification it cannot keep.
    Counting them as one would refuse the registry for the row that had
    already refused itself, and would hide the rows that need fixing.
    """
    path = _registry_with(
        {"result": "failed",
         "evidence": "a run that did not pass"}, tmp_path)

    audit = axi_qualification.audit(path)

    assert audit["rows"] == []
    assert len(audit["failed"]) == 1
    assert audit["failed"][0]["key"] == "clip.delete::resolve_axi"
    assert audit["ineligible"] == 0
    assert audit["eligible"] == 0


def test_the_audit_names_a_row_whose_predicate_names_a_place(tmp_path):
    """A predicate that names a project is a pointer to the test."""
    record = _passed_report("clip.delete", "resolve_axi")
    record["qualification_predicates"] = [
        {"field": "project_name", "operator": "equals",
         "value": "Ren Qualification"}]
    path = _registry_with(record, tmp_path)

    audit = axi_qualification.audit(path)

    assert audit["ineligible"] == 1
    assert any("identity" in problem
               for problem in audit["rows"][0]["problems"])


def test_the_audit_is_a_gate_that_can_fail(tmp_path):
    """`--audit` exits non-zero when a row cannot keep its qualification.

    A gate that cannot fail is not a gate: the command a reader runs to
    ask "is the registry honest?" has to answer with the exit code as
    well as the printout.
    """
    record = _passed_report("clip.delete", "resolve_axi")
    record["qualification_predicates"] = [
        {"field": "timeline_name", "operator": "equals",
         "value": "fm-scratch qualify"}]
    path = _registry_with(record, tmp_path)

    assert axi_qualification.main(["--audit", "--registry", str(path)]) == 1
    assert axi_qualification.main(["--audit"]) == 0
