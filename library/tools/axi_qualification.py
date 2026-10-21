"""Import measured AXI qualification results without hand-set status flags.

Live qualification reports are keyed by one algebra operation and one
backend. The registry stores the observations; ``edit_algebra`` derives
qualified and production-enabled state only when the report passed and all
evidence fields are complete.

A report states the DIMENSIONS its evidence was earned under
(`library/tools/axi_qualification_dimensions.py`), and the predicates are
derived from that coverage - so a claim can never name a dimension value
the measurement cannot produce, and can never reach past the evidence
that backs it. The report also states its ENVIRONMENT (the Resolve and
the machine it ran on) and its SEMANTIC BOUNDARY (what the evidence
applies to, and what it does not), in machine-readable form.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from library.tools import axi_qualification_dimensions as dimensions

REGISTRY_PATH = Path(__file__).resolve().with_name("axi_qualification_records.json")
REPORT_SCHEMA = "ren-axi-qualification-result/v2"
LEGACY_REPORT_SCHEMA = "ren-axi-qualification-result/v1"
COMPOUND_REPORT_SCHEMA = "ren-axi-compound-qualification-result/v2"
REGISTRY_SCHEMA = "ren-axi-qualification-registry/v1"

#: Every field a recorded qualification predicate may name. It is the
#: dimension enumeration, not a second list: a field that is not a
#: dimension is not a qualification boundary.
PREDICATE_FIELDS = frozenset(dimensions.DIMENSIONS)

#: The ONE reading of a Resolve version.
#:
#: Resolve's own ``GetVersionString`` reports a bare dotted version
#: ("21.1.0.14"); a qualification record may carry the product label
#: ("DaVinci Resolve 21.1.0.14"). Both name the same build, and a
#: predicate compared against a label the runtime never emits can never be
#: satisfied - so the comparison happens on the VERSION, and the product
#: label is decoration. Measured 2026-10-20: every promoted operation
#: (``clip.delete``, ``clip.set_property``) was refused in production
#: routing with "resolve_version is '21.1.0.14', not 'DaVinci Resolve
#: 21.1.0.14'", which is a statement about two notations and not about the
#: timeline the evidence was earned on.
_RESOLVE_VERSION_LABEL = re.compile(
    r"^\s*(?:DaVinci\s+Resolve(?:\s+Studio)?\b\s*)?", re.IGNORECASE)


def canonical_resolve_version(value: Any) -> str:
    """The Resolve version in the one form a recorded predicate can compare.

    The bare dotted version, with the product label removed. A version
    that is already bare comes back unchanged; an empty value stays empty
    so a missing version is still a missing version.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return _RESOLVE_VERSION_LABEL.sub("", text).strip()


def canonical_predicate(predicate: dict[str, Any]) -> dict[str, Any]:
    """One predicate with its version value in the canonical reading."""
    if predicate.get("field") != "resolve_version":
        return dict(predicate)
    canonical = dict(predicate)
    operator = canonical.get("operator")
    if operator == "one_of" and isinstance(canonical.get("value"), list):
        canonical["value"] = [canonical_resolve_version(item)
                              for item in canonical["value"]]
    else:
        canonical["value"] = canonical_resolve_version(canonical.get("value"))
    return canonical

_IDENTITY_REQUIRED = ("operation_id", "backend", "result", "evidence")
_COMPOUND_IDENTITY_REQUIRED = ("scenario", "backend", "result", "evidence")

#: Where a coverage came from. `measured` is a run that measured the
#: dimensions. `derived_from_v1_evidence` is a coverage worked out from
#: an earlier report - usable, and marked, because the run that
#: re-measures it under the varied configurations has not happened yet.
COVERAGE_SOURCES = frozenset({"measured", "derived_from_v1_evidence"})
_REQUIRED = (
    "operation_id", "backend", "result", "evidence", "resolve_version",
    "environment", "semantic_boundary", "coverage",
    "project_conditions", "supported_media_types", "expected_state",
    "actual_state", "preservation_checks", "preservation_limits",
    "visual_verification", "audio_verification", "recovery_status",
)
_COMPOUND_REQUIRED = tuple(
    key for key in _REQUIRED if key not in {"operation_id"})



class QualificationReportError(ValueError):
    """A result report is malformed, incomplete, or names an unknown pair."""


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def _environment(report: dict[str, Any]) -> dict:
    """The ENVIRONMENT the evidence was earned in.

    A qualification is a claim about a machine and a Resolve version, so
    both are recorded as fields rather than as prose: a reader has to be
    able to tell "proven on Resolve 21" from "proven on Resolve 18".
    """
    environment = report["environment"]
    if not isinstance(environment, dict):
        raise QualificationReportError(
            "environment must be an object naming the Resolve and the "
            "machine the evidence was earned on")
    for key in ("resolve_version", "platform"):
        value = environment.get(key)
        if not isinstance(value, str) or not value.strip():
            raise QualificationReportError(
                f"environment must name a non-empty {key}")
    return {"resolve_version": environment["resolve_version"],
            "platform": environment["platform"]}


def _semantic_boundary(report: dict[str, Any]) -> dict:
    """The SEMANTIC BOUNDARY of the evidence.

    What the evidence applies to, and what it does not. Both halves are
    required: a boundary that lists only what it covers reads as if it
    covered everything else.
    """
    boundary = report["semantic_boundary"]
    if not isinstance(boundary, dict):
        raise QualificationReportError(
            "semantic_boundary must be an object naming what the evidence "
            "applies to and what it does not")
    for key in ("applies_to", "does_not_apply_to"):
        values = boundary.get(key)
        if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value.strip() for value in values):
            raise QualificationReportError(
                f"semantic_boundary must list non-empty {key} statements")
    return {"applies_to": list(boundary["applies_to"]),
            "does_not_apply_to": list(boundary["does_not_apply_to"])}


def _coverage(report: dict[str, Any]) -> dict:
    """The COVERAGE the evidence establishes, per dimension.

    Every dimension is named, with the values the evidence exercised. A
    dimension the evidence never exercised is not "unknown": it is
    ABSENT, and a record that leaves it out is a record that does not
    say where its own boundary is.
    """
    coverage = report["coverage"]
    if not isinstance(coverage, dict):
        raise QualificationReportError(
            "coverage must be an object naming the dimension values the "
            "evidence exercised")
    measured = coverage.get("dimensions")
    if not isinstance(measured, dict):
        raise QualificationReportError(
            "coverage must carry a dimensions map")
    missing = [field for field in dimensions.DIMENSIONS
               if field not in measured]
    if missing:
        raise QualificationReportError(
            "coverage does not name these dimensions: "
            + ", ".join(missing))
    unknown = [field for field in measured
               if not dimensions.is_dimension(field)]
    if unknown:
        raise QualificationReportError(
            "coverage names fields that are not dimensions: "
            + ", ".join(sorted(unknown)))
    for field, values in measured.items():
        if not isinstance(values, list) or not values:
            raise QualificationReportError(
                f"coverage for {field} must list the values exercised")
        for value in values:
            if not dimensions.value_in_vocabulary(field, value):
                raise QualificationReportError(
                    f"coverage for {field} names {value!r}, which is not a "
                    "value that dimension can measure")
    configurations = coverage.get("configurations", [])
    if not isinstance(configurations, list) or not all(
            isinstance(value, str) and value.strip()
            for value in configurations):
        raise QualificationReportError(
            "coverage configurations must be a list of non-empty names")
    uncovered = coverage.get("uncovered", {})
    if not isinstance(uncovered, dict) or any(
            not dimensions.is_dimension(field) or not isinstance(values, list)
            for field, values in uncovered.items()):
        raise QualificationReportError(
            "coverage uncovered must map dimensions to lists")
    source = coverage.get("source", "measured")
    if source not in COVERAGE_SOURCES:
        raise QualificationReportError(
            "coverage source must be one of: "
            + ", ".join(sorted(COVERAGE_SOURCES)))
    normalized = {field: list(values) for field, values in measured.items()}
    if "resolve_version" in normalized:
        normalized["resolve_version"] = [
            canonical_resolve_version(value)
            for value in normalized["resolve_version"]]
    return {"dimensions": normalized,
            "configurations": list(configurations),
            "uncovered": {field: list(values)
                          for field, values in uncovered.items()},
            "source": source}


def _predicates(report: dict[str, Any],
                coverage: dict[str, Any]) -> list[dict]:
    """The predicates the evidence supports.

    A report may declare its own predicates, and then they must not reach
    past the coverage: a claim wider than the evidence is refused. A
    report that declares none gets the coverage itself, which is the
    whole of what the evidence establishes.
    """
    declared = report.get("qualification_predicates")
    if declared is None:
        return dimensions.predicates_for(coverage["dimensions"])
    if not isinstance(declared, list) or not declared:
        raise QualificationReportError(
            "qualification_predicates must be non-empty machine-readable "
            "predicates")
    measured = coverage["dimensions"]
    for predicate in declared:
        if (not isinstance(predicate, dict)
                or set(predicate) != {"field", "operator", "value"}):
            raise QualificationReportError(
                "each qualification predicate must be a field, an operator "
                "and a value")
        predicate = canonical_predicate(predicate)
        field = predicate["field"]
        if dimensions.is_identity_field(field):
            raise QualificationReportError(
                f"a qualification predicate must not name {field!r}: it is "
                "an identity, not a state, and naming it restricts the "
                "operation to the place the evidence was earned")
        if not dimensions.is_dimension(field):
            raise QualificationReportError(
                f"{field!r} is not a qualification dimension")
        if predicate["operator"] not in {"equals", "one_of"}:
            raise QualificationReportError(
                "a qualification predicate operator must be equals or one_of")
        values = ([predicate["value"]] if predicate["operator"] == "equals"
                  else predicate["value"])
        if (not isinstance(values, list) or not values
                or not all(isinstance(value, str) for value in values)):
            raise QualificationReportError(
                "a qualification predicate must name non-empty string values")
        for value in values:
            if not dimensions.value_in_vocabulary(field, value):
                raise QualificationReportError(
                    f"the {field} predicate names {value!r}, which is not a "
                    "value that dimension can measure")
        covered = measured.get(field, ())
        if field == "resolve_version":
            # Coverage records the Resolve release line (for example,
            # 21.1.0), while reports and runtime predicates may include
            # the build suffix (21.1.0.14). A line covers its dotted
            # builds; matching only the prefix plus a dot avoids treating
            # 21.1.01 or 21.10 as members of 21.1.0.
            outside = [value for value in values
                       if not any(value == version
                                  or value.startswith(version + ".")
                                  for version in covered)]
        else:
            outside = [value for value in values if value not in covered]
        if outside:
            raise QualificationReportError(
                f"the {field} predicate claims {outside} which the coverage "
                "does not record as exercised")
    return [dict(predicate) for predicate in declared]


def parse_result(report: dict[str, Any]) -> tuple[tuple[str, str], dict[str, Any]]:
    """Validate one result report and return its pair and evidence record."""
    schema = report.get("schema")
    if schema == COMPOUND_REPORT_SCHEMA:
        return _parse_compound_result(report)
    if schema == LEGACY_REPORT_SCHEMA:
        raise QualificationReportError(
            f"report schema {LEGACY_REPORT_SCHEMA} predates the explicit "
            f"boundary format; re-run the evidence and report it as "
            f"{REPORT_SCHEMA}, which states its environment, its semantic "
            "boundary and the dimensions it covers")
    if schema != REPORT_SCHEMA:
        raise QualificationReportError(f"expected report schema {REPORT_SCHEMA}")
    missing = [key for key in _IDENTITY_REQUIRED if key not in report]
    if missing:
        raise QualificationReportError(
            "qualification report is missing: " + ", ".join(missing))
    if report["result"] not in {"passed", "failed"}:
        raise QualificationReportError("result must be passed or failed")
    op_id, backend = report["operation_id"], report["backend"]
    if not isinstance(op_id, str) or not op_id.strip() or not isinstance(
            backend, str) or not backend.strip():
        raise QualificationReportError("operation_id and backend must be non-empty strings")
    if not isinstance(report["evidence"], str) or not report["evidence"].strip():
        raise QualificationReportError("evidence must name the recorded run result")
    if report["result"] == "failed":
        return (op_id, backend), {
            "evidence": report["evidence"], "result": "failed"}
    return (op_id, backend), _evidence_record(report, _REQUIRED)


def _parse_compound_result(report: dict[str, Any]) -> tuple[tuple[str, str],
                                                            dict[str, Any]]:
    """Validate one COMPOUND transaction qualification report.

    A compound transaction is qualified by the same evidence rules as a
    single operation: the same environment, the same semantic boundary,
    the same coverage, the same predicates. Only the identity differs -
    it names a declared scenario and a backend.
    """
    missing = [key for key in _COMPOUND_IDENTITY_REQUIRED if key not in report]
    if missing:
        raise QualificationReportError(
            "compound qualification report is missing: " + ", ".join(missing))
    if report["result"] not in {"passed", "failed"}:
        raise QualificationReportError("result must be passed or failed")
    scenario, backend = report["scenario"], report["backend"]
    if not isinstance(scenario, str) or not scenario.strip() or not isinstance(
            backend, str) or not backend.strip():
        raise QualificationReportError(
            "scenario and backend must be non-empty strings")
    from library.tools import axi_transaction

    declared = axi_transaction.COMPOUND_SCENARIOS.get(scenario)
    if declared is None:
        raise QualificationReportError(
            f"{scenario!r} is not a declared compound transaction; the "
            f"declared shapes are {sorted(axi_transaction.COMPOUND_SCENARIOS)}")
    if backend not in declared.backends:
        raise QualificationReportError(
            f"the {scenario!r} compound transaction has no path on "
            f"{backend!r}; it declares {list(declared.backends)}")
    if not isinstance(report["evidence"], str) or not report["evidence"].strip():
        raise QualificationReportError("evidence must name the recorded run result")
    if report["result"] == "failed":
        return (scenario, backend), {
            "evidence": report["evidence"], "result": "failed"}
    return (scenario, backend), _evidence_record(report, _COMPOUND_REQUIRED)


def _evidence_record(report: dict[str, Any], required: tuple[str, ...]
                     ) -> dict[str, Any]:
    """Validate the evidence fields every qualification report carries."""
    missing_evidence = [key for key in required if key not in report]
    if missing_evidence:
        raise QualificationReportError(
            "qualification report is missing: " + ", ".join(missing_evidence))
    for key in required:
        if key in {"result", "operation_id", "backend", "scenario"}:
            continue
        if not _nonempty(report[key]):
            raise QualificationReportError(
                f"qualification report has no {key} evidence")
    if not isinstance(report["expected_state"], dict) or not isinstance(
            report["actual_state"], dict):
        raise QualificationReportError("expected_state and actual_state must be objects")
    if not isinstance(report["resolve_version"], str):
        raise QualificationReportError("resolve_version must be a string")
    for key in ("project_conditions", "supported_media_types",
                "preservation_limits", "visual_verification",
                "audio_verification"):
        if not isinstance(report[key], list) or not all(
                isinstance(value, str) and value.strip()
                for value in report[key]):
            raise QualificationReportError(f"{key} must be a list of non-empty strings")
    environment = _environment(report)
    boundary = _semantic_boundary(report)
    coverage = _coverage(report)
    predicates = _predicates(report, coverage)
    checks = report["preservation_checks"]
    if not isinstance(checks, list) or not checks or any(
            not isinstance(check, dict) or check.get("result") != "passed"
            for check in checks):
        raise QualificationReportError(
            "all preservation checks must record result=passed")
    if report["recovery_status"] not in {"passed", "not_required"}:
        raise QualificationReportError(
            "recovery_status must be passed or explicitly not_required")
    return {
        "evidence": report["evidence"],
        # The version is stored in the ONE reading a predicate compares
        # against, so the record and the runtime that must satisfy it
        # cannot disagree about a product label.
        "resolve_version": canonical_resolve_version(report["resolve_version"]),
        "environment": environment,
        "semantic_boundary": boundary,
        "coverage": coverage,        "media_types": report["supported_media_types"],
        "timeline_conditions": report["project_conditions"],
        "qualification_predicates": [canonical_predicate(predicate)
                                     for predicate in predicates],
        "expected_state": report["expected_state"],
        "actual_state": report["actual_state"],
        "preservation_checks": report["preservation_checks"],
        "preservation_limits": report["preservation_limits"],
        "visual_verification": report["visual_verification"],
        "audio_verification": report["audio_verification"],
        "recovery": report["recovery_status"],
        "result": "passed",
    }


def _read_registry(path: Path) -> dict:
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "records": {},
                "compound_records": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != REGISTRY_SCHEMA or not isinstance(
            payload.get("records"), dict):
        raise QualificationReportError(f"invalid qualification registry: {path}")
    payload.setdefault("compound_records", {})
    return payload


def import_result(report_path: Path, registry_path: Path = REGISTRY_PATH) -> tuple[str, str]:
    """Import a completed result, replacing only its exact op/backend row."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    pair, record = parse_result(report)
    payload = _read_registry(registry_path)
    key = f"{pair[0]}::{pair[1]}"
    if report.get("schema") == COMPOUND_REPORT_SCHEMA:
        payload["compound_records"][key] = record
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return pair
    from library.tools import edit_algebra

    status = edit_algebra.status(pair[0])
    if status is None or status.for_backend(pair[1]) is None:
        raise QualificationReportError(
            f"no registered implementation exists for {pair[0]} on {pair[1]}")
    payload["records"][key] = record
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pair


def load_qualification_records(path: Path = REGISTRY_PATH) -> dict:
    """Return evidence-complete rows in the shape consumed by edit_algebra."""
    payload = _read_registry(path)
    return _complete_rows(payload["records"])


def load_compound_records(path: Path = REGISTRY_PATH) -> dict:
    """Return the evidence-complete COMPOUND transaction rows."""
    payload = _read_registry(path)
    return _complete_rows(payload.get("compound_records") or {})


def _complete_rows(rows: Mapping[str, Any]) -> dict[tuple[str, str], dict]:
    """The evidence-complete rows of one registry section."""
    records = {}
    for key, record in rows.items():
        if not isinstance(record, dict) or record.get("result") != "passed":
            continue
        try:
            op_id, backend = key.split("::", 1)
        except ValueError as exc:
            raise QualificationReportError(f"invalid operation/backend key {key!r}") from exc
        required = (
            "evidence", "resolve_version", "environment", "semantic_boundary",
            "coverage", "media_types", "timeline_conditions",
            "qualification_predicates",
            "expected_state", "actual_state", "preservation_checks",
            "preservation_limits", "visual_verification", "audio_verification",
            "recovery",
        )
        if any(not _nonempty(record.get(name)) for name in required):
            continue
        if not all(check.get("result") == "passed"
                   for check in record["preservation_checks"]):
            continue
        records[(op_id, backend)] = {
            "result": record["result"],
            "evidence": (record["evidence"],),
            # A record written before the version had one reading still
            # reads in it: the predicate is compared against a runtime
            # that reports the bare version, so the label is removed here
            # rather than left to fail the comparison forever.
            "resolve_version": canonical_resolve_version(
                record["resolve_version"]),
            "environment": dict(record["environment"]),
            "semantic_boundary": dict(record["semantic_boundary"]),
            "coverage": dict(record["coverage"]),
            "media_types": tuple(record["media_types"]),
            "timeline_conditions": tuple(record["timeline_conditions"]),
            "qualification_predicates": tuple(
                canonical_predicate(predicate)
                for predicate in record["qualification_predicates"]),
            "expected_state": record["expected_state"],
            "actual_state": record["actual_state"],
            "preservation_checks": record["preservation_checks"],
            "preservation_limits": tuple(record["preservation_limits"]),
            "visual_verification": tuple(record["visual_verification"]),
            "audio_verification": tuple(record["audio_verification"]),
            "recovery": record["recovery"],
            # Enablement is derived only from imported passing, complete evidence.
            "production_enabled": True,
        }
    return records


def audit(path: Path = REGISTRY_PATH) -> dict:
    """Every registered row, and whether its boundary is explicit.

    A row is production-eligible when its predicates name dimensions the
    measurement can produce and its evidence carries an environment, a
    semantic boundary and a coverage. This reports the rows that are not,
    so a record that reads as qualified while naming a state nobody can
    reach is visible instead of silent.

    A row whose evidence FAILED is not such a record: it is not
    qualified, and it does not read as qualified either. It is reported
    under `failed`, so the audit still shows it and the eligibility count
    still means "a claim that cannot be taken".
    """
    payload = _read_registry(path)
    rows = []
    failed = []
    for key, record in sorted(payload["records"].items()):
        entry = {"key": key, "production_eligible": False, "problems": [],
                 "needs_live_remeasurement": False}
        if not isinstance(record, dict):
            entry["problems"].append("the row is not an object")
            rows.append(entry)
            continue
        if record.get("result") != "passed":
            failed.append({"key": key,
                           "evidence": record.get("evidence") or "",
                           "reason": "the evidence did not pass"})
            continue
        for name in ("environment", "semantic_boundary", "coverage"):
            if not _nonempty(record.get(name)):
                entry["problems"].append(f"no {name}")
        coverage = record.get("coverage") or {}
        if coverage.get("source") != "measured":
            entry["needs_live_remeasurement"] = True
        for predicate in record.get("qualification_predicates") or ():
            field = predicate.get("field")
            if dimensions.is_identity_field(field):
                entry["problems"].append(
                    f"the {field} predicate names an identity, not a state")
                continue
            if not dimensions.is_dimension(field):
                entry["problems"].append(
                    f"{field!r} is not a qualification dimension")
                continue
            values = ([predicate.get("value")]
                      if predicate.get("operator") == "equals"
                      else predicate.get("value") or ())
            for value in values:
                if not dimensions.value_in_vocabulary(field, value):
                    entry["problems"].append(
                        f"the {field} predicate names {value!r}, which the "
                        "measurement cannot produce")
        entry["production_eligible"] = not entry["problems"]
        rows.append(entry)
    return {"schema": "ren-axi-qualification-audit/v1", "rows": rows,
            "failed": failed,
            "eligible": sum(row["production_eligible"] for row in rows),
            "ineligible": sum(not row["production_eligible"] for row in rows),
            "needs_live_remeasurement": sum(
                row["needs_live_remeasurement"] for row in rows)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import one completed Resolve AXI qualification result")
    parser.add_argument("--import-result", type=Path)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--audit", action="store_true",
                        help="report every registered row and whether its "
                             "boundary is explicit; exits non-zero when a "
                             "row claims a qualification it cannot keep")
    args = parser.parse_args(argv)
    if args.audit:
        report = audit(args.registry)
        print(json.dumps(report, indent=2, sort_keys=True))
        # The audit is a GATE: it exits non-zero when a row that claims a
        # qualification cannot keep it. A row whose evidence failed is
        # not that - it qualified nothing in the first place.
        return 1 if report["ineligible"] else 0
    if args.import_result is None:
        parser.error("--import-result is required unless --audit is given")
    op_id, backend = import_result(args.import_result, args.registry)
    print(f"Imported qualification result for {op_id} on {backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
