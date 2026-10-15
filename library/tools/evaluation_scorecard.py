"""Aggregate frozen, modality-specific evaluation reports into a scorecard.

The scorecard deliberately does not rank versions or combine metrics. In
particular, held-out results are carried through for reporting only; the
selection basis must name calibration data exclusively.

Input format (one JSON file per modality report)::

    {"format":"ren_evaluation_report/1", "revision":"...",
     "version":{"id":"candidate-1", "role":"candidate",
                "models":{"vision":"model@rev"}},
     "modality":"visual_retrieval", "split":"calibration",
     "metrics":{"recall_at_5":{"value":0.8,"unit":"ratio",
                                  "dimension":"quality"}},
     "evidence":[{"id":"corpus", "path":"corpus.json",
                   "sha256":"<sha256>"}],
     "selection_basis":["calibration"]}

``path`` for evidence is relative to the report file. Hashes are verified
before aggregation. A report should be frozen before a held-out run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

FORMAT = "ren_evaluation_report/1"
SCORECARD_FORMAT = "ren_intelligence_scorecard/1"
DIMENSIONS = ("quality", "latency", "memory", "cost")
ROLES = {"baseline", "candidate", "accepted"}
SPLITS = {"training", "calibration", "held_out"}


class ScorecardError(ValueError):
    """An evaluation report cannot safely be included in a scorecard."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScorecardError(f"{name} must be a non-empty string")
    return value


def _load_report(path: Path) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScorecardError(f"cannot read report {path}: {exc}") from exc
    if not isinstance(report, dict) or report.get("format") != FORMAT:
        raise ScorecardError(f"{path}: expected {FORMAT}")
    _require_string(report.get("revision"), f"{path}: revision")
    version = report.get("version")
    if not isinstance(version, dict):
        raise ScorecardError(f"{path}: version must be an object")
    _require_string(version.get("id"), f"{path}: version.id")
    if version.get("role") not in ROLES:
        raise ScorecardError(f"{path}: version.role must be one of {sorted(ROLES)}")
    models = version.get("models")
    if not isinstance(models, dict) or not models or any(
            not isinstance(k, str) or not isinstance(v, str) or not v
            for k, v in models.items()):
        raise ScorecardError(f"{path}: version.models must map model names to versions")
    _require_string(report.get("modality"), f"{path}: modality")
    if report.get("split") not in SPLITS:
        raise ScorecardError(f"{path}: split must be one of {sorted(SPLITS)}")
    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ScorecardError(f"{path}: metrics must be an object")
    for name, metric in metrics.items():
        if not isinstance(name, str) or not isinstance(metric, dict):
            raise ScorecardError(f"{path}: each metric must be a named object")
        if "value" not in metric or metric.get("dimension") not in DIMENSIONS:
            raise ScorecardError(
                f"{path}: metric {name!r} needs value and dimension in {DIMENSIONS}")
        if (isinstance(metric["value"], bool)
                or not isinstance(metric["value"], (int, float))
                or not math.isfinite(metric["value"])):
            raise ScorecardError(f"{path}: metric {name!r} value must be a finite number")
        if not isinstance(metric.get("unit"), str) or not metric["unit"]:
            raise ScorecardError(f"{path}: metric {name!r} needs a unit")

    selection_basis = report.get("selection_basis", [])
    if not isinstance(selection_basis, list) or any(
            split not in {"training", "calibration"} for split in selection_basis):
        raise ScorecardError(
            f"{path}: selection_basis may contain training/calibration only; held-out data cannot select a version")
    evidence_rows = report.get("evidence", [])
    if not isinstance(evidence_rows, list):
        raise ScorecardError(f"{path}: evidence must be a list")
    checksums: dict[str, str] = {}
    for item in evidence_rows:
        if not isinstance(item, dict):
            raise ScorecardError(f"{path}: evidence entries must be objects")
        evidence_id = _require_string(item.get("id"), f"{path}: evidence.id")
        expected = _require_string(item.get("sha256"), f"{path}: evidence.sha256")
        if (len(expected) != 64
                or any(char not in "0123456789abcdef" for char in expected.lower())):
            raise ScorecardError(f"{path}: evidence SHA-256 must be 64 hexadecimal characters")
        if evidence_id in checksums:
            raise ScorecardError(f"{path}: duplicate evidence id {evidence_id!r}")
        evidence_path = item.get("path")
        if evidence_path is not None:
            resolved = (path.parent / _require_string(evidence_path, "evidence.path")).resolve()
            try:
                actual = sha256_file(resolved)
            except OSError as exc:
                raise ScorecardError(f"{path}: cannot hash evidence {resolved}: {exc}") from exc
            if actual != expected:
                raise ScorecardError(f"{path}: checksum mismatch for evidence {evidence_id!r}")
        checksums[evidence_id] = expected
    return report, checksums


def build_scorecard(report_paths: list[Path]) -> dict[str, Any]:
    """Build a reproducible scorecard from frozen report files."""
    if not report_paths:
        raise ScorecardError("at least one report is required")
    versions: dict[str, dict[str, Any]] = {}
    sources = []
    seen: set[tuple[str, str, str]] = set()
    for path in sorted((p.resolve() for p in report_paths), key=str):
        report, evidence_checksums = _load_report(path)
        version = report["version"]
        version_id = version["id"]
        row = versions.setdefault(version_id, {
            "role": version["role"], "models": version["models"],
            "modalities": {}, "unmeasured": {},
        })
        if row["role"] != version["role"] or row["models"] != version["models"]:
            raise ScorecardError(f"version {version_id!r} has inconsistent role or model versions")
        modality, split = report["modality"], report["split"]
        key = (version_id, modality, split)
        if key in seen:
            raise ScorecardError(f"duplicate report for {version_id}/{modality}/{split}")
        seen.add(key)
        row["modalities"].setdefault(modality, {})[split] = {
            "revision": report["revision"],
            "metrics": report["metrics"],
            "selection_basis": report.get("selection_basis", []),
            "evidence_checksums": evidence_checksums,
        }
        sources.append({"path": str(path), "sha256": sha256_file(path)})

    for version in versions.values():
        for modality, splits in version["modalities"].items():
            dimensions = {metric["dimension"] for split_row in splits.values()
                          for metric in split_row["metrics"].values()}
            version["unmeasured"][modality] = [d for d in DIMENSIONS if d not in dimensions]
    return {
        "format": SCORECARD_FORMAT,
        "dimensions": list(DIMENSIONS),
        "aggregation": "modality_and_split_only; no_blended_score_or_version_ranking",
        "selection_policy": "selection_basis is limited to training/calibration; held_out is report-only",
        "versions": versions,
        "sources": sources,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", type=Path, required=True,
                        help="frozen ren_evaluation_report/1 JSON; repeatable")
    parser.add_argument("--out", type=Path, help="write scorecard JSON here (stdout by default)")
    args = parser.parse_args()
    try:
        scorecard = build_scorecard(args.report)
    except ScorecardError as exc:
        parser.error(str(exc))
    rendered = json.dumps(scorecard, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
