"""Import measured AXI qualification results without hand-set status flags.

Live qualification reports are keyed by one algebra operation and one backend.
The registry stores the observations; ``edit_algebra`` derives qualified and
production-enabled state only when the report passed and all evidence fields
are complete.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REGISTRY_PATH = Path(__file__).resolve().with_name("axi_qualification_records.json")
REPORT_SCHEMA = "ren-axi-qualification-result/v1"
REGISTRY_SCHEMA = "ren-axi-qualification-registry/v1"

_IDENTITY_REQUIRED = ("operation_id", "backend", "result", "evidence")
_REQUIRED = (
    "operation_id", "backend", "result", "evidence", "resolve_version",
    "project_conditions", "supported_media_types", "expected_state",
    "actual_state", "preservation_checks", "visual_verification",
    "preservation_limits", "audio_verification", "recovery_status",
)


class QualificationReportError(ValueError):
    """A result report is malformed, incomplete, or names an unknown pair."""


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def parse_result(report: dict[str, Any]) -> tuple[tuple[str, str], dict[str, Any]]:
    """Validate one result report and return its pair and evidence record."""
    if report.get("schema") != REPORT_SCHEMA:
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
    missing_evidence = [key for key in _REQUIRED if key not in report]
    if missing_evidence:
        raise QualificationReportError(
            "qualification report is missing: " + ", ".join(missing_evidence))
    for key in _REQUIRED:
        if key in {"result", "operation_id", "backend"}:
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
    checks = report["preservation_checks"]
    if not isinstance(checks, list) or not checks or any(
            not isinstance(check, dict) or check.get("result") != "passed"
            for check in checks):
        raise QualificationReportError(
            "all preservation checks must record result=passed")
    if report["recovery_status"] not in {"passed", "not_required"}:
        raise QualificationReportError(
            "recovery_status must be passed or explicitly not_required")
    record = {
        "evidence": report["evidence"],
        "resolve_version": report["resolve_version"],
        "media_types": report["supported_media_types"],
        "timeline_conditions": report["project_conditions"],
        "expected_state": report["expected_state"],
        "actual_state": report["actual_state"],
        "preservation_checks": report["preservation_checks"],
        "preservation_limits": report["preservation_limits"],
        "visual_verification": report["visual_verification"],
        "audio_verification": report["audio_verification"],
        "recovery": report["recovery_status"],
        "result": "passed",
    }
    return (op_id, backend), record


def _read_registry(path: Path) -> dict:
    if not path.exists():
        return {"schema": REGISTRY_SCHEMA, "records": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != REGISTRY_SCHEMA or not isinstance(
            payload.get("records"), dict):
        raise QualificationReportError(f"invalid qualification registry: {path}")
    return payload


def import_result(report_path: Path, registry_path: Path = REGISTRY_PATH) -> tuple[str, str]:
    """Import a completed result, replacing only its exact op/backend row."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    pair, record = parse_result(report)
    from library.tools import edit_algebra

    status = edit_algebra.status(pair[0])
    if status is None or status.for_backend(pair[1]) is None:
        raise QualificationReportError(
            f"no registered implementation exists for {pair[0]} on {pair[1]}")
    payload = _read_registry(registry_path)
    key = f"{pair[0]}::{pair[1]}"
    payload["records"][key] = record
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return pair


def load_qualification_records(path: Path = REGISTRY_PATH) -> dict:
    """Return evidence-complete rows in the shape consumed by edit_algebra."""
    payload = _read_registry(path)
    records = {}
    for key, record in payload["records"].items():
        if not isinstance(record, dict) or record.get("result") != "passed":
            continue
        try:
            op_id, backend = key.split("::", 1)
        except ValueError as exc:
            raise QualificationReportError(f"invalid operation/backend key {key!r}") from exc
        required = (
            "evidence", "resolve_version", "media_types", "timeline_conditions",
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
            "resolve_version": record["resolve_version"],
            "media_types": tuple(record["media_types"]),
            "timeline_conditions": tuple(record["timeline_conditions"]),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import one completed Resolve AXI qualification result")
    parser.add_argument("--import-result", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    args = parser.parse_args(argv)
    op_id, backend = import_result(args.import_result, args.registry)
    print(f"Imported qualification result for {op_id} on {backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
