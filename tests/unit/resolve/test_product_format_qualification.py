"""The product-format qualification: what a real reel may now be routed for.

The AXI-010..023 scenarios were earned on a 1920x1080 landscape fixture
at 24fps with one or two clips.  A Ren reel is portrait 1080x1920 at
23.976fps, dense, with linked video+audio, rendered caption cards,
transforms and retiming - so production routing refused every real reel,
and the punch list's "trimming is not approved for a linked
video+audio+caption target" and "clip.replace is not approved for real
use" were the same refusal wearing two names.

These tests read the RECORDED evidence and the registry it produced, and
hold the boundary in place:

* every pair the product-format run earned is production-enabled, and
  its row names the product format;
* a real reel's MEASURED state satisfies those predicates, so the
  router does not refuse the reel;
* the evidence is a live measurement, not a hand-set flag: the coverage
  is the measured state, and the report carries the environment, the
  semantic boundary and the preservation checks.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import axi_qualification, edit_algebra
from library.tools.execution_router import _qualification_matches

ROOT = Path(__file__).resolve().parents[3]
REPORT_DIR = ROOT / "tests" / "live" / "reports" / "product-format-qualification"

#: The pairs this lane qualified in the product format.
PRODUCT_FORMAT_PAIRS = (
    ("broll.insert", "resolve_axi"),
    ("clip.insert", "resolve_axi"),
    ("clip.replace", "edit_patch"),
    ("clip.delete", "edit_patch"),
)

#: The reel-level dimensions a real reel measures as: portrait,
#: 23.976, dense, every row populated, a multi-item timeline.
REEL_LEVEL = {
    "project_settings": "portrait_1080x1920",
    "frame_rate": "23.976",
    "timeline_density": "dense",
    "track_layout": "video+audio",
    "track_occupancy": "all_populated",
    "timeline_structure": "multi_item_timeline",
}


def _records() -> dict:
    return json.loads(
        axi_qualification.REGISTRY_PATH.read_text(encoding="utf-8"))["records"]


def _report(operation: str, backend: str) -> dict:
    path = REPORT_DIR / f"{operation}__{backend}.json"
    assert path.is_file(), f"the product-format evidence is missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _evidence() -> dict:
    path = REPORT_DIR / "product-format-evidence.json"
    assert path.is_file(), f"the product-format evidence is missing: {path}"
    return json.loads(path.read_text(encoding="utf-8"))


def _measured_state(evidence: dict, operation: str, backend: str) -> dict:
    """The state the pair's own case was measured in."""
    for case in evidence.get("results") or ():
        if case.get("operation") != operation or case.get("backend") != backend:
            continue
        state = (case.get("qualification_state") or {}).get("state") or {}
        if state:
            return state
    return {}


def test_every_earned_pair_is_production_enabled():
    """The product-format run made the pair routable, and not by hand."""
    records = _records()
    for operation, backend in PRODUCT_FORMAT_PAIRS:
        key = f"{operation}::{backend}"
        assert key in records, f"{key} has no recorded evidence"
        entry = edit_algebra.status(operation)
        assert entry is not None
        row = entry.for_backend(backend)
        assert row is not None, f"{operation} has no {backend} executor"
        assert row.production_enabled, (
            f"{key} is not production enabled: the evidence was imported "
            "but the row did not enable it")


def test_every_earned_row_names_the_product_format():
    """The row covers the state the reel is in, not the fixture's."""
    records = _records()
    for operation, backend in PRODUCT_FORMAT_PAIRS:
        coverage = (records[f"{operation}::{backend}"]
                    .get("coverage") or {}).get("dimensions") or {}
        assert "portrait_1080x1920" in (coverage.get("project_settings") or []), (
            f"{operation}::{backend} does not cover the portrait reel")
        assert "23.976" in (coverage.get("frame_rate") or []), (
            f"{operation}::{backend} does not cover 23.976fps")
        assert "dense" in (coverage.get("timeline_density") or []), (
            f"{operation}::{backend} does not cover a dense timeline")


def test_a_real_reel_satisfies_the_recorded_predicates():
    """The refusal the punch list named is gone for a real reel.

    The state is the MEASURED one the evidence was earned in: the
    reel-level dimensions are the product format, and the item-level
    ones are the target the operation addressed.
    """
    evidence = _evidence()
    for operation, backend in PRODUCT_FORMAT_PAIRS:
        entry = edit_algebra.status(operation)
        row = entry.for_backend(backend)
        assert row.qualification_predicates, (
            f"{operation}::{backend} carries no predicate to satisfy")
        measured = _measured_state(evidence, operation, backend)
        assert measured, f"{operation}::{backend} earned no measured state"
        for field, value in REEL_LEVEL.items():
            assert measured.get(field) == value, (
                f"{operation}::{backend} was measured with {field}="
                f"{measured.get(field)!r}, not {value!r}")
        inspected = {"inspected": True, "state": measured}
        assert _qualification_matches(row, inspected), (
            f"{operation}::{backend} would still refuse the reel it was "
            "qualified on: "
            + str([predicate for predicate in row.qualification_predicates
                   if not _matches(predicate, measured)]))


def _matches(predicate: dict, state: dict) -> bool:
    field = predicate.get("field")
    if field not in state:
        return False
    actual = state[field]
    if predicate.get("operator") == "equals":
        return actual == predicate.get("value")
    return actual in (predicate.get("value") or ())


def test_the_evidence_is_a_measurement_and_not_a_flag():
    """Every report carries the environment, the boundary and the checks."""
    for operation, backend in PRODUCT_FORMAT_PAIRS:
        report = _report(operation, backend)
        assert report["schema"] == axi_qualification.REPORT_SCHEMA
        assert report["result"] == "passed"
        assert report["evidence"].strip()
        assert report["environment"]["resolve_version"]
        assert report["environment"]["platform"]
        assert report["semantic_boundary"]["applies_to"]
        assert report["semantic_boundary"]["does_not_apply_to"]
        assert report["preservation_checks"], (
            f"{operation}::{backend} carries no preservation check")
        assert all(check.get("result") == "passed"
                   for check in report["preservation_checks"])
        assert report["coverage"]["source"] in {"measured",
                                                "derived_from_v1_evidence"}
        for field in axi_qualification.PREDICATE_FIELDS:
            assert field in report["coverage"]["dimensions"], (
                f"{operation}::{backend} names no {field} coverage")


def test_the_report_is_importable_as_it_stands():
    """The importer accepts the report, so the evidence is well formed."""
    from library.tools.axi_qualification import QualificationReportError

    for operation, backend in PRODUCT_FORMAT_PAIRS:
        report = _report(operation, backend)
        pair, record = axi_qualification.parse_result(report)
        assert pair == (operation, backend)
        assert record["result"] == "passed"
        assert record["coverage"]["dimensions"]


def test_a_refusal_earns_nothing():
    """A refused scenario is a recorded outcome, never a qualification."""
    from library.tools import axi_qualification

    refused = {
        "schema": axi_qualification.REPORT_SCHEMA,
        "operation_id": "clip.trim",
        "backend": "resolve_axi",
        "result": "failed",
        "evidence": "a live run that refused",
    }
    pair, record = axi_qualification.parse_result(refused)
    assert pair == ("clip.trim", "resolve_axi")
    assert record == {"evidence": "a live run that refused",
                      "result": "failed"}
    with pytest.raises(axi_qualification.QualificationReportError):
        broken = dict(refused, result="passed")
        axi_qualification.parse_result(broken)
