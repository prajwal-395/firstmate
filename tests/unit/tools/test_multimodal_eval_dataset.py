import json
import shutil
from pathlib import Path

import pytest

from library.tools.multimodal_eval_dataset import (
    DatasetManifestError,
    validate_manifest,
)

MANIFEST = Path("docs/benchmarks/2026-10-15-multimodal-eval-manifest.json")
BENCHMARKS = MANIFEST.parent


def test_frozen_suite_inherits_rights_splits_and_actual_intervals():
    report = validate_manifest(MANIFEST)

    assert report["valid"]
    assert report["source_count"] == 24
    assert report["source_split_counts"] == {
        "training": 3, "calibration": 11, "held_out": 10}
    assert report["case_count"] == 6
    assert {row["split"] for row in report["cases"]} == {"calibration", "held_out"}
    assert sum(row["source_intervals"] for row in report["cases"]) == 9
    assert report["annotation_state"] == "pending_agent_labels"


def _copy_suite(tmp_path):
    suite = json.loads(MANIFEST.read_text(encoding="utf-8"))
    references = {suite["source_manifest"]["path"]}
    references.update(case["fixture"]["path"] for case in suite["cases"])
    for relative in references:
        shutil.copy2(BENCHMARKS / relative, tmp_path / relative)
    shutil.copy2(MANIFEST, tmp_path / MANIFEST.name)
    return tmp_path / MANIFEST.name


def test_fixture_checksum_drift_is_refused(tmp_path):
    manifest_path = _copy_suite(tmp_path)
    fixture = tmp_path / "eval04-brief-visual-action-object-calibration.json"
    fixture.write_text(fixture.read_text(encoding="utf-8") + " ", encoding="utf-8")

    with pytest.raises(DatasetManifestError, match="checksum mismatch"):
        validate_manifest(manifest_path)


def test_cross_split_source_reference_is_refused(tmp_path):
    manifest_path = _copy_suite(tmp_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["cases"][0]["source_intervals"][0]["source_id"] = "IMG_1818"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DatasetManifestError, match="another split"):
        validate_manifest(manifest_path)


def test_out_of_bounds_source_interval_is_refused(tmp_path):
    manifest_path = _copy_suite(tmp_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["cases"][0]["source_intervals"][0]["end_seconds"] = 999
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DatasetManifestError, match="exceeds source duration"):
        validate_manifest(manifest_path)


def test_held_out_never_becomes_tunable(tmp_path):
    manifest_path = _copy_suite(tmp_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["annotation_protocol"]["held_out_labels_tuning_access"] = True
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(DatasetManifestError, match="annotation protocol"):
        validate_manifest(manifest_path)
