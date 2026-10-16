"""Validate the frozen multimodal fixture manifest without reading footage."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from library.tools import editorial_eval_corpus

FORMAT = "ren_multimodal_eval_dataset/1"
SPLITS = {"calibration", "held_out"}


class DatasetManifestError(ValueError):
    """The fixture manifest is stale, ambiguous, or leaks split data."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_file(root: Path, relative: Any, description: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise DatasetManifestError(f"{description} path must be a non-empty string")
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        raise DatasetManifestError(f"{description} path must remain in the dataset directory")
    resolved = (root / path).resolve()
    if root.resolve() not in resolved.parents:
        raise DatasetManifestError(f"{description} path escapes the dataset directory")
    return resolved


def _read_json(path: Path, description: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetManifestError(f"cannot read {description} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetManifestError(f"{description} must be a JSON object")
    return value


def validate_manifest(path: Path) -> dict:
    """Verify immutable fixture and source manifest references and intervals."""
    path = Path(path).resolve()
    manifest = _read_json(path, "multimodal dataset manifest")
    if manifest.get("format") != FORMAT:
        raise DatasetManifestError(f"expected {FORMAT}")
    if manifest.get("frozen_inputs") is not True:
        raise DatasetManifestError("frozen_inputs must be true")
    source_ref = manifest.get("source_manifest")
    if not isinstance(source_ref, dict):
        raise DatasetManifestError("source_manifest reference is required")
    source_path = _safe_file(path.parent, source_ref.get("path"), "source manifest")
    try:
        source_digest = _sha256(source_path)
    except OSError as exc:
        raise DatasetManifestError(f"cannot checksum source manifest: {exc}") from exc
    if source_digest != source_ref.get("sha256"):
        raise DatasetManifestError("source manifest checksum mismatch")
    source_manifest = _read_json(source_path, "source manifest")
    source_validation = editorial_eval_corpus.validate(source_manifest)
    if not source_validation["valid"]:
        raise DatasetManifestError(
            "source corpus is invalid: " + "; ".join(source_validation["errors"]))
    sources = {row["source_id"]: row for row in source_manifest["sources"]}

    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise DatasetManifestError("cases must be a non-empty list")
    case_ids: set[str] = set()
    covered_splits: set[str] = set()
    fixture_digests: dict[str, str] = {}
    checked_cases = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise DatasetManifestError(f"cases[{index}] must be an object")
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id or case_id in case_ids:
            raise DatasetManifestError(f"cases[{index}] has a missing or duplicate case_id")
        case_ids.add(case_id)
        split = case.get("split")
        if split not in SPLITS:
            raise DatasetManifestError(f"{case_id}: split must be calibration or held_out")
        covered_splits.add(split)
        fixture = case.get("fixture")
        if not isinstance(fixture, dict):
            raise DatasetManifestError(f"{case_id}: fixture reference is required")
        fixture_path = _safe_file(path.parent, fixture.get("path"), f"{case_id} fixture")
        try:
            fixture_digest = _sha256(fixture_path)
        except OSError as exc:
            raise DatasetManifestError(f"{case_id}: cannot checksum fixture: {exc}") from exc
        if fixture_digest != fixture.get("sha256"):
            raise DatasetManifestError(f"{case_id}: fixture checksum mismatch")
        fixture_data = _read_json(fixture_path, f"{case_id} fixture")
        if fixture_data.get("split") != split:
            raise DatasetManifestError(f"{case_id}: fixture split disagrees with case split")
        fixture_digests[str(fixture["path"])] = fixture_digest

        intervals = case.get("source_intervals")
        if not isinstance(intervals, list) or not intervals:
            raise DatasetManifestError(f"{case_id}: at least one actual source interval is required")
        for interval in intervals:
            if not isinstance(interval, dict):
                raise DatasetManifestError(f"{case_id}: source interval must be an object")
            source_id = interval.get("source_id")
            source = sources.get(source_id)
            if source is None:
                raise DatasetManifestError(f"{case_id}: unknown source {source_id!r}")
            if source["split"] != split:
                raise DatasetManifestError(f"{case_id}: source {source_id} is in another split")
            start, end = interval.get("start_seconds"), interval.get("end_seconds")
            if (isinstance(start, bool) or isinstance(end, bool)
                    or not isinstance(start, (int, float)) or not isinstance(end, (int, float))
                    or not math.isfinite(start) or not math.isfinite(end)
                    or start < 0 or end <= start or end > source["duration_seconds"]):
                raise DatasetManifestError(f"{case_id}: interval is invalid or exceeds source duration")
        if case.get("annotation_state") != "agent_labels_pending":
            raise DatasetManifestError(f"{case_id}: annotations must remain pending until blind review")
        if split == "held_out" and case.get("held_out_access") != "sealed_until_one_final_report":
            raise DatasetManifestError(f"{case_id}: held-out access must remain sealed")
        checked_cases.append({"case_id": case_id, "split": split,
                              "source_intervals": len(intervals),
                              "fixture_sha256": fixture_digest})
    if covered_splits != SPLITS:
        raise DatasetManifestError("calibration and held_out cases are both required")

    protocol = manifest.get("annotation_protocol", {})
    if (protocol.get("status") != "pending_agent_labels"
            or protocol.get("label_type") != "agent"
            or protocol.get("independent_blind_labellers") != 2
            or protocol.get("labeller_model_families_must_differ") is not True
            or protocol.get("third_tie_breaker_for_disagreements") is not True
            or protocol.get("agreement_is_recorded") is not True
            or protocol.get("prediction_blinding") is not True
            or protocol.get("held_out_labels_tuning_access") is not False):
        raise DatasetManifestError("agent annotation protocol is incomplete or exposes held-out labels")
    return {
        "valid": True,
        "dataset_id": manifest.get("dataset_id"),
        "source_count": source_validation["source_count"],
        "source_split_counts": source_validation["split_source_counts"],
        "case_count": len(cases),
        "fixture_checksums": fixture_digests,
        "cases": checked_cases,
        "annotation_state": protocol["status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        report = validate_manifest(args.manifest)
    except DatasetManifestError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
