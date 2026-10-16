"""Validate and gate a frozen model qualification record.

This is an explicit qualification command, not part of ordinary unit or model
analysis runs. It consumes the versioned record contract and never runs a
model. Accuracy and resource deltas are reported independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from library.tools.evaluation_scorecard import ScorecardError, build_scorecard

FORMAT = "ren_model_qualification/1"
DECISIONS = {"adopt", "retain_baseline", "research_only",
             "conditional_fallback", "reject"}
MODES = {"speed_only", "accuracy_oriented", "utility_gain", "research"}
SPLITS = {"training", "calibration", "held_out"}
DIMENSIONS = {"higher_is_better", "lower_is_better"}
SPEED_ONLY_MIN = 2.0


class QualificationError(ValueError):
    """A record is incomplete, unsafe, or fails an adoption criterion."""


def _mapping(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise QualificationError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QualificationError(f"{name} must be a non-empty string")
    return value


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise QualificationError(f"{name} must be a finite number")
    return float(value)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_report_refs(record: dict, base_dir: Path) -> list[dict]:
    reports = record.get("reports")
    if not isinstance(reports, list) or not reports:
        raise QualificationError("reports must list frozen evaluation reports")
    verified = []
    resolved_paths = []
    seen = set()
    for index, ref in enumerate(reports):
        ref = _mapping(ref, f"reports[{index}]")
        relative = Path(_text(ref.get("path"), f"reports[{index}].path"))
        if relative.is_absolute() or ".." in relative.parts:
            raise QualificationError("report paths must stay inside the record directory")
        path = (base_dir / relative).resolve()
        expected = _text(ref.get("sha256"), f"reports[{index}].sha256").lower()
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise QualificationError("report SHA-256 must contain 64 hex digits")
        try:
            actual = _checksum(path)
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QualificationError(f"cannot read frozen report {path}: {exc}") from exc
        if actual != expected:
            raise QualificationError(f"report checksum mismatch: {relative}")
        if report.get("format") != "ren_evaluation_report/1":
            raise QualificationError(f"{relative}: expected ren_evaluation_report/1")
        if report.get("split") != ref.get("split"):
            raise QualificationError(f"{relative}: report split does not match its reference")
        if ref.get("split") not in SPLITS:
            raise QualificationError(f"{relative}: unknown report split")
        if str(relative) in seen:
            raise QualificationError(f"duplicate report path: {relative}")
        seen.add(str(relative))
        resolved_paths.append(path)
        verified.append({"path": str(relative), "sha256": actual,
                         "split": ref["split"]})
    try:
        scorecard = build_scorecard(resolved_paths)
    except ScorecardError as exc:
        raise QualificationError(f"frozen evaluation reports refused: {exc}") from exc
    for role in ("baseline", "candidate"):
        implementation = _mapping(record.get(role), role)
        version_id = _text(implementation.get("id"), f"{role}.id")
        row = scorecard["versions"].get(version_id)
        if row is None:
            raise QualificationError(f"{role} has no frozen evaluation reports")
        expected_role = "baseline" if role == "baseline" else "candidate"
        if row["role"] != expected_role:
            raise QualificationError(f"{role} reports must use version role {expected_role!r}")
        revision = _text(implementation.get("model_revision"), f"{role}.model_revision")
        if revision not in row["models"].values():
            raise QualificationError(f"{role}.model_revision does not match its report revision")
    return verified


def evaluate_record(record: dict, base_dir: Path = Path(".")) -> dict:
    """Return a deterministic qualification verdict and independent deltas."""
    record = _mapping(record, "record")
    if record.get("format") != FORMAT:
        raise QualificationError(f"expected {FORMAT}")
    _text(record.get("revision"), "revision")
    _text(record.get("capability"), "capability")
    hardware = _mapping(record.get("hardware"), "hardware")
    _text(hardware.get("machine"), "hardware.machine")
    if "m5 pro" not in hardware["machine"].lower():
        raise QualificationError("qualification hardware must identify the M5 Pro")
    if _finite(hardware.get("unified_memory_gb"), "hardware.unified_memory_gb") != 24:
        raise QualificationError("qualification hardware must have 24 GB unified memory")
    _text(hardware.get("os_revision"), "hardware.os_revision")
    _text(hardware.get("resolve_revision"), "hardware.resolve_revision")
    dataset = _mapping(record.get("dataset"), "dataset")
    _text(dataset.get("manifest_id"), "dataset.manifest_id")
    manifest_path = Path(_text(dataset.get("manifest_path"), "dataset.manifest_path"))
    if manifest_path.is_absolute() or ".." in manifest_path.parts:
        raise QualificationError("dataset manifest path must stay inside the record directory")
    digest = _text(dataset.get("manifest_sha256"), "dataset.manifest_sha256").lower()
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise QualificationError("dataset.manifest_sha256 must contain 64 hex digits")
    if dataset.get("inputs_deterministic") is not True:
        raise QualificationError("deterministic evaluation inputs are required")
    manifest_file = (base_dir / manifest_path).resolve()
    try:
        manifest_digest = _checksum(manifest_file)
    except OSError as exc:
        raise QualificationError(f"cannot read frozen dataset manifest: {exc}") from exc
    if manifest_digest != digest:
        raise QualificationError("frozen dataset manifest checksum mismatch")
    splits = dataset.get("splits")
    if (not isinstance(splits, list) or not {"calibration", "held_out"}.issubset(splits)
            or any(split not in SPLITS for split in splits)):
        raise QualificationError("dataset must separate calibration and held_out splits")

    for key in ("baseline", "candidate"):
        implementation = _mapping(record.get(key), key)
        for field in ("id", "adapter_id", "model_revision", "publisher_revision"):
            _text(implementation.get(field), f"{key}.{field}")
        refs = implementation.get("report_ids")
        if not isinstance(refs, list) or not refs:
            raise QualificationError(f"{key}.report_ids must identify measured reports")

    verified_reports = _verify_report_refs(record, base_dir)
    report_paths = {row["path"] for row in verified_reports}
    for key in ("baseline", "candidate"):
        if not set(record[key]["report_ids"]).issubset(report_paths):
            raise QualificationError(f"{key}.report_ids must refer to checksummed reports")
    if not {"calibration", "held_out"}.issubset({row["split"] for row in verified_reports}):
        raise QualificationError("frozen reports must include calibration and held_out")

    comparison = _mapping(record.get("comparison"), "comparison")
    quality = comparison.get("quality")
    if not isinstance(quality, list) or not quality:
        raise QualificationError("comparison.quality must include fidelity comparisons")
    quality_deltas = []
    quality_regressions = []
    equal_compute_regressions = []
    for index, row in enumerate(quality):
        row = _mapping(row, f"comparison.quality[{index}]")
        name = _text(row.get("metric"), f"quality[{index}].metric")
        split = row.get("split")
        if split not in SPLITS:
            raise QualificationError(f"quality[{index}].split is unknown")
        direction = row.get("direction")
        if direction not in DIMENSIONS:
            raise QualificationError(f"quality[{index}].direction must be directional")
        before = _finite(row.get("baseline"), f"quality[{index}].baseline")
        after = _finite(row.get("candidate"), f"quality[{index}].candidate")
        tolerance = _finite(row.get("max_regression"), f"quality[{index}].max_regression")
        if tolerance < 0:
            raise QualificationError(f"quality[{index}].max_regression cannot be negative")
        delta = after - before if direction == "higher_is_better" else before - after
        regressed = delta < -tolerance
        quality_deltas.append({"metric": name, "split": split, "delta": delta,
                               "unit": _text(row.get("unit"), f"quality[{index}].unit"),
                               "regressed": regressed})
        if regressed:
            quality_regressions.append(name)

    for key in ("equal_compute", "equal_quality"):
        rows = comparison.get(key)
        if not isinstance(rows, list) or not rows:
            raise QualificationError(f"comparison.{key} must be measured")
        for index, row in enumerate(rows):
            row = _mapping(row, f"comparison.{key}[{index}]")
            _text(row.get("metric"), f"{key}[{index}].metric")
            _finite(row.get("baseline"), f"{key}[{index}].baseline")
            _finite(row.get("candidate"), f"{key}[{index}].candidate")
            _text(row.get("unit"), f"{key}[{index}].unit")
            _text(row.get("budget"), f"{key}[{index}].budget")
            direction = row.get("direction")
            if direction not in DIMENSIONS:
                raise QualificationError(f"{key}[{index}].direction must be directional")
            tolerance = _finite(row.get("max_regression"),
                                f"{key}[{index}].max_regression")
            if tolerance < 0:
                raise QualificationError("max_regression cannot be negative")
            if key == "equal_compute":
                split = row.get("split")
                if split not in SPLITS:
                    raise QualificationError(f"equal_compute[{index}].split is unknown")
                before, after = float(row["baseline"]), float(row["candidate"])
                delta = after - before if direction == "higher_is_better" else before - after
                if delta < -tolerance:
                    equal_compute_regressions.append({
                        "metric": row["metric"], "split": split,
                        "regressed": True})

    resources = _mapping(comparison.get("resources"), "comparison.resources")
    resource_fields = {
        "cold_start_ms", "steady_state_ms", "decode_preprocess_ms", "inference_ms",
        "postprocess_serialize_ms", "end_to_end_ms", "peak_rss_mb", "memory_pressure",
        "cpu_utilization_pct", "gpu_utilization_pct", "ane_utilization_pct",
        "throughput_source_hours_per_wall_hour", "useful_evidence_per_compute_second",
        "resolve_open_concurrency",
    }
    for role in ("baseline", "candidate"):
        metrics = _mapping(resources.get(role), f"resources.{role}")
        missing = resource_fields - set(metrics)
        if missing:
            raise QualificationError(
                f"resources.{role} is missing measurements: {sorted(missing)}")
        for name in ("cold_start_ms", "steady_state_ms", "decode_preprocess_ms",
                     "inference_ms", "postprocess_serialize_ms", "end_to_end_ms",
                     "peak_rss_mb", "throughput_source_hours_per_wall_hour",
                     "useful_evidence_per_compute_second"):
            value = metrics.get(name)
            if value is not None and _finite(value, f"resources.{role}.{name}") < 0:
                raise QualificationError(f"resources.{role}.{name} cannot be negative")
        for name in ("cpu_utilization_pct", "gpu_utilization_pct", "ane_utilization_pct"):
            value = metrics.get(name)
            if value is not None and not 0 <= _finite(value, f"resources.{role}.{name}") <= 100:
                raise QualificationError(f"resources.{role}.{name} must be between 0 and 100")
        if metrics.get("memory_pressure") not in {"normal", "warn", "critical", "unknown"}:
            raise QualificationError(f"resources.{role}.memory_pressure is invalid")
        if metrics.get("resolve_open_concurrency") not in {
                "passed", "failed", "not_run", "not_supported"}:
            raise QualificationError(f"resources.{role}.resolve_open_concurrency is invalid")
    resource_deltas = {}
    for name in ("cold_start_ms", "steady_state_ms", "decode_preprocess_ms",
                 "inference_ms", "postprocess_serialize_ms", "end_to_end_ms",
                 "peak_rss_mb", "cpu_utilization_pct", "gpu_utilization_pct",
                 "ane_utilization_pct", "throughput_source_hours_per_wall_hour",
                 "useful_evidence_per_compute_second"):
        before, after = resources["baseline"].get(name), resources["candidate"].get(name)
        resource_deltas[name] = (None if before is None or after is None else after - before)
    concurrency_ready = all(
        resources[role]["resolve_open_concurrency"] == "passed"
        for role in ("baseline", "candidate"))
    resource_regressions = []
    for name, delta in resource_deltas.items():
        if delta is None:
            continue
        if name in {"cold_start_ms", "steady_state_ms", "decode_preprocess_ms",
                    "inference_ms", "postprocess_serialize_ms", "end_to_end_ms",
                    "peak_rss_mb"} and delta > 0:
            resource_regressions.append({"resource": name, "delta": delta,
                                         "direction": "higher_cost"})
        elif (name in {"throughput_source_hours_per_wall_hour",
                       "useful_evidence_per_compute_second"} and delta < 0):
            resource_regressions.append({"resource": name, "delta": delta,
                                         "direction": "lower_throughput"})
    pressure_rank = {"normal": 0, "warn": 1, "critical": 2}
    before_pressure = resources["baseline"]["memory_pressure"]
    after_pressure = resources["candidate"]["memory_pressure"]
    if (before_pressure in pressure_rank and after_pressure in pressure_rank
            and pressure_rank[after_pressure] > pressure_rank[before_pressure]):
        resource_regressions.append({"resource": "memory_pressure",
                                     "baseline": before_pressure,
                                     "candidate": after_pressure,
                                     "direction": "worse_pressure"})

    adoption = _mapping(record.get("adoption"), "adoption")
    decision = adoption.get("decision")
    if decision not in DECISIONS:
        raise QualificationError(f"adoption.decision must be one of {sorted(DECISIONS)}")
    _text(adoption.get("rationale"), "adoption.rationale")
    mode = adoption.get("mode")
    if mode not in MODES:
        raise QualificationError(f"adoption.mode must be one of {sorted(MODES)}")
    execution_policy = adoption.get("execution_policy")
    if execution_policy not in {"always_on", "on_demand"}:
        raise QualificationError("adoption.execution_policy must be always_on or on_demand")
    selection_splits = adoption.get("selection_splits")
    if (not isinstance(selection_splits, list) or not selection_splits
            or any(split not in {"training", "calibration"} for split in selection_splits)):
        raise QualificationError("adoption.selection_splits may use training/calibration only")
    if "calibration" not in selection_splits:
        raise QualificationError("adoption must name calibration as a selection split")
    if "held_out" in selection_splits:
        raise QualificationError("held_out is report-only and cannot select a model")
    if not set(selection_splits).issubset(set(splits)):
        raise QualificationError("selection split is not in dataset manifest")

    speedup = None
    base_e2e = resources["baseline"].get("end_to_end_ms")
    candidate_e2e = resources["candidate"].get("end_to_end_ms")
    if base_e2e is not None and candidate_e2e is not None and candidate_e2e > 0:
        speedup = base_e2e / candidate_e2e
    failures = []
    selected_regressions = [row["metric"] for row in quality_deltas
                            if row["regressed"] and row["split"] in selection_splits]
    selected_regressions.extend(row["metric"] for row in equal_compute_regressions
                                if row["split"] in selection_splits)
    if decision == "adopt" and selected_regressions:
        failures.append("quality regression exceeds an agreed per-metric tolerance")
    if decision == "adopt" and mode == "speed_only":
        if speedup is None or speedup < SPEED_ONLY_MIN:
            failures.append(f"speed-only adoption needs at least {SPEED_ONLY_MIN:g}x end-to-end improvement")
        if selected_regressions:
            failures.append("speed-only adoption cannot pass with a fidelity regression")
    if decision == "adopt":
        required_measurements = (
            "cold_start_ms", "steady_state_ms", "decode_preprocess_ms", "inference_ms",
            "postprocess_serialize_ms", "end_to_end_ms", "peak_rss_mb",
            "throughput_source_hours_per_wall_hour", "useful_evidence_per_compute_second",
        )
        missing_roles = [f"{role}.{name}" for role in ("baseline", "candidate")
                         for name in required_measurements
                         if resources[role][name] is None]
        if missing_roles:
            failures.append("adoption requires complete baseline/candidate resource measurements: "
                            + ", ".join(missing_roles))
        if any(resources[role]["memory_pressure"] == "unknown"
               for role in ("baseline", "candidate")):
            failures.append("adoption requires measured memory-pressure state")
    if decision == "adopt" and mode == "accuracy_oriented" and not any(
            row["delta"] > 0 and row["split"] == "calibration" for row in quality_deltas):
        failures.append("accuracy-oriented adoption needs a measured calibration improvement")
    task_utility = [row for row in quality_deltas
                    if row["metric"] == "actual_ren_task_utility"
                    and row["split"] in selection_splits]
    if decision == "adopt" and not task_utility:
        failures.append("adoption requires actual Ren task-utility measurement on calibration")
    if decision == "adopt" and execution_policy == "always_on" and not any(
            row["metric"] == "downstream_utility" and row["split"] == "calibration"
            and row["delta"] > 0 for row in quality_deltas):
        failures.append("always-on analysis requires measurable downstream utility gain")
    if decision == "adopt" and not concurrency_ready:
        failures.append("adoption requires baseline and candidate Resolve-open concurrency runs")

    risks = record.get("regression_risks")
    unsupported = record.get("unsupported_cases")
    if not isinstance(risks, list) or not all(isinstance(row, str) for row in risks):
        raise QualificationError("regression_risks must be a list of strings")
    if not isinstance(unsupported, list) or not all(isinstance(row, str) for row in unsupported):
        raise QualificationError("unsupported_cases must be a list of strings")
    return {
        "format": "ren_model_qualification_result/1",
        "decision": decision,
        "passed": not failures,
        "failures": failures,
        "quality_deltas": quality_deltas,
        "quality_regressions": quality_regressions,
        "equal_compute_regressions": equal_compute_regressions,
        "selection_quality_regressions": sorted(set(selected_regressions)),
        "resource_deltas": resource_deltas,
        "end_to_end_speedup": speedup,
        "execution_policy": execution_policy,
        "resolve_open_concurrency_ready": concurrency_ready,
        "resource_regressions": resource_regressions,
        "regression_risks": risks,
        "unsupported_cases": unsupported,
        "verified_reports": verified_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", required=True, type=Path,
                        help="ren_model_qualification/1 JSON record")
    parser.add_argument("--out", type=Path, help="write machine-readable verdict JSON")
    args = parser.parse_args()
    try:
        record = json.loads(args.record.read_text(encoding="utf-8"))
        result = evaluate_record(record, args.record.resolve().parent)
    except (OSError, json.JSONDecodeError, QualificationError) as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
