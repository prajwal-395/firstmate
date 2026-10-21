"""Defect-naming tests for the provenance a report carries.

Each test names a defect it would catch:

- a differently scoped report OVERWRITES the one already at the path,
  and the older evidence is gone;
- a same scoped report replaces the older one without saying what it
  superseded;
- a report with no provenance is accepted as a report;
- an evidence kind outside the vocabulary is accepted;
- a field nobody measured reads as a measured zero.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import report_provenance as rp


def _report(measurement: str, *, passed: int = 1,
            fixture: str = "a fixture", level: int = 2) -> dict:
    """A report in one scope, carrying its provenance."""
    payload = {
        "schema": "ren-example-report/1",
        "measurement": measurement,
        "passed": passed,
    }
    return rp.stamp(
        payload,
        evidence_kind="live-qualification",
        fixture=fixture,
        producer="tests/unit/tools/test_report_provenance.py",
        scope={"measurement": measurement,
               "evaluation_level": level,
               "task_set": "task-a,task-b",
               "fixture": fixture})


def test_a_differently_scoped_report_does_not_overwrite_the_one_already_there(
        tmp_path: Path) -> None:
    """Two measurements of two things are not two versions of one thing."""
    target = tmp_path / "report.json"
    first = _report("ordinary_request_acceptance", passed=1)
    rp.write_report(target, first)

    second = _report("blind_editorial_requests", passed=7, level=3)
    written = rp.write_report(target, second)

    assert written != target, "a differently scoped report was written over " \
        "the one already there"
    assert json.loads(target.read_text(encoding="utf-8"))["passed"] == 1, \
        "the older evidence was replaced by a measurement of another scope"
    assert json.loads(written.read_text(encoding="utf-8"))["passed"] == 7


def test_two_measurements_that_share_a_name_both_stand(tmp_path: Path) -> None:
    """Two scopes of one measurement do not collide on one file name."""
    target = tmp_path / "report.json"
    first = _report("ordinary_request_acceptance", fixture="seeded")
    rp.write_report(target, first)

    second = _report("ordinary_request_acceptance", fixture="unavailable")
    written = rp.write_report(target, second)

    assert written != target
    assert written.exists(), "the second scope's report was not written"
    assert json.loads(written.read_text(encoding="utf-8"))["provenance"][
        "fixture"] == "unavailable"


def test_a_same_scoped_report_records_what_it_superseded(tmp_path: Path) -> None:
    """A re-run of the same measurement says which run it replaced."""
    target = tmp_path / "report.json"
    first = _report("ordinary_request_acceptance", passed=1)
    rp.write_report(target, first)

    second = _report("ordinary_request_acceptance", passed=6)
    written = rp.write_report(target, second)

    assert written == target
    payload = json.loads(written.read_text(encoding="utf-8"))
    superseded = payload["provenance"].get("supersedes") or []
    assert superseded, "the replacement does not say what it replaced"
    assert superseded[-1]["source_commit"] == \
        first["provenance"]["source_commit"]


def test_a_report_with_no_provenance_is_refused(tmp_path: Path) -> None:
    """A report nobody can trace to a run is not read as a report."""
    target = tmp_path / "report.json"
    target.write_text(json.dumps({"schema": "ren-example-report/1"}),
                      encoding="utf-8")
    with pytest.raises(rp.ProvenanceError) as caught:
        rp.read_report(target)
    assert "provenance" in str(caught.value)


def test_an_evidence_kind_outside_the_vocabulary_is_refused() -> None:
    """A kind nobody agreed on cannot widen what a report may claim."""
    with pytest.raises(rp.ProvenanceError):
        rp.header(evidence_kind="it-seemed-fine",
                  evaluation_schema="ren-example-report/1")


def test_a_field_nobody_measured_reads_not_recorded_and_never_zero() -> None:
    """An unmeasured field is a reading about the report, not a value."""
    block = rp.header(evidence_kind="live-qualification",
                      evaluation_schema="ren-example-report/1")
    assert block["resolve_version"] == rp.NOT_RECORDED
    assert block["run_timestamp"] != rp.NOT_RECORDED, \
        "the time a report was produced IS measured"
    assert block["source_commit"] != rp.NOT_RECORDED, \
        "the revision a report was produced from IS known"


def test_two_measurements_in_different_scopes_have_different_keys() -> None:
    """The scope is what makes one number and another comparable."""
    first = _report("ordinary_request_acceptance", fixture="seeded")
    second = _report("ordinary_request_acceptance", fixture="not seeded")
    assert rp.scope_key(first) != rp.scope_key(second)


def test_a_record_of_a_run_carries_its_provenance() -> None:
    """A task record names the revision and the run that produced it."""
    from library.tools.editorial_acceptance import record as record_module

    record = record_module.stamp_record(
        record_module.new_record(task_id="t1", brief_id="b1",
                                 instruction="do the thing"),
        evidence_kind="live-qualification",
        fixture="the substrate",
        scope={"measurement": "editorial_task_record",
               "evaluation_level": 4,
               "task_set": "b1",
               "fixture": "the substrate"})
    assert not record_module.validate(record), record_module.validate(record)
    assert record["provenance"]["evidence_kind"] == "live-qualification"


def test_the_acceptance_runner_stamps_the_report_it_writes() -> None:
    """The brief acceptance report says what it measured and how."""
    from library.tools.editorial_acceptance import runner

    report = runner.run_acceptance([])
    provenance = report["provenance"]
    assert provenance["evidence_kind"] == "editorial-acceptance"
    assert provenance["scope"]["measurement"] == "editorial_brief_acceptance"
    assert provenance["scope"]["evaluation_level"] == 4
    assert not rp.validate(provenance), rp.validate(provenance)


def test_an_imported_qualification_says_where_it_came_from(
        tmp_path: Path) -> None:
    """A qualification is traceable to the report that earned it."""
    from library.tools import axi_qualification

    source = {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": "transition",
        "backend": "reel_touchup",
        "result": "failed",
        "evidence": "tests/live/reports/registry/example.json",
    }
    source_path = tmp_path / "example.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    registry = tmp_path / "registry.json"

    axi_qualification.import_result(source_path, registry)

    payload = json.loads(registry.read_text(encoding="utf-8"))
    assert payload["provenance"]["evidence_kind"] == "live-qualification"
    assert payload["imported"][0]["source_report"] == "example.json"
    assert payload["imported"][0]["source_commit"], \
        "the imported row names no revision"


def test_an_imported_row_keeps_the_shape_the_router_reads(
        tmp_path: Path) -> None:
    """A row that gained a field would no longer re-import from its report."""
    from library.tools import axi_qualification

    source = {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": "transition",
        "backend": "reel_touchup",
        "result": "failed",
        "evidence": "tests/live/reports/registry/example.json",
    }
    source_path = tmp_path / "example.json"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    registry = tmp_path / "registry.json"

    axi_qualification.import_result(source_path, registry)

    payload = json.loads(registry.read_text(encoding="utf-8"))
    row = payload["records"]["transition::reel_touchup"]
    assert set(row) == {"evidence", "result"}, sorted(row)
