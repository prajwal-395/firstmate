import hashlib
import json

import pytest

from library.tools.model_qualification import (
    QualificationError,
    evaluate_record,
)


def _resources(end_to_end_ms):
    return {
        "cold_start_ms": 200.0,
        "steady_state_ms": 100.0,
        "decode_preprocess_ms": 20.0,
        "inference_ms": 50.0,
        "postprocess_serialize_ms": 10.0,
        "end_to_end_ms": end_to_end_ms,
        "peak_rss_mb": 800.0,
        "memory_pressure": "normal",
        "cpu_utilization_pct": 60.0,
        "gpu_utilization_pct": None,
        "ane_utilization_pct": None,
        "throughput_source_hours_per_wall_hour": 2.0,
        "useful_evidence_per_compute_second": 4.0,
        "resolve_open_concurrency": "passed",
    }


def _record(tmp_path, *, decision="adopt", mode="speed_only", speed=2.2,
            held_out_regression=False):
    manifest_bytes = b'{"dataset":"fixture"}'
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(manifest_bytes)
    reports = []
    reports_by_version = {"baseline": [], "candidate": []}
    for version, role, revision in (
            ("baseline", "baseline", "rev-base"),
            ("candidate", "candidate", "rev-candidate")):
        for split in ("calibration", "held_out"):
            data = {
                "format": "ren_evaluation_report/1",
                "revision": "eval-rev-1",
                "version": {"id": version, "role": role,
                            "models": {"detector": revision}},
                "modality": "object_detection",
                "split": split,
                "metrics": {"recall": {"value": 0.8, "unit": "ratio",
                                          "dimension": "quality"}},
                "evidence": [],
                "selection_basis": ["calibration"],
            }
            path = tmp_path / f"{version}-{split}.json"
            raw = json.dumps(data, sort_keys=True).encode()
            path.write_bytes(raw)
            rel = path.name
            ref = {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(),
                   "split": split}
            reports.append(ref)
            reports_by_version[version].append(rel)

    record = {
        "format": "ren_model_qualification/1",
        "revision": "engine-rev",
        "capability": "visual.detect",
        "hardware": {"machine": "M5 Pro", "unified_memory_gb": 24,
                     "os_revision": "macOS-test", "resolve_revision": "Resolve-test"},
        "dataset": {"manifest_id": "frozen-set-1", "manifest_path": manifest_path.name,
                    "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                    "inputs_deterministic": True,
                    "splits": ["calibration", "held_out"]},
        "baseline": {"id": "baseline", "adapter_id": "adapter-base",
                     "model_revision": "rev-base", "publisher_revision": "pub-base",
                     "report_ids": reports_by_version["baseline"]},
        "candidate": {"id": "candidate", "adapter_id": "adapter-new",
                       "model_revision": "rev-candidate",
                       "publisher_revision": "pub-candidate",
                       "report_ids": reports_by_version["candidate"]},
        "comparison": {
            "quality": [
                {"metric": "recall", "split": "calibration", "baseline": 0.8,
                 "candidate": 0.8, "unit": "ratio", "direction": "higher_is_better",
                 "max_regression": 0.01},
                {"metric": "actual_ren_task_utility", "split": "calibration",
                 "baseline": 0.6, "candidate": 0.6, "unit": "ratio",
                 "direction": "higher_is_better", "max_regression": 0.01},
                {"metric": "recall", "split": "held_out", "baseline": 0.8,
                 "candidate": 0.5 if held_out_regression else 0.8, "unit": "ratio",
                 "direction": "higher_is_better", "max_regression": 0.01},
            ],
            "resources": {"baseline": _resources(220.0),
                          "candidate": _resources(220.0 / speed)},
            "equal_compute": [{"metric": "recall", "split": "calibration",
                               "baseline": 0.8, "candidate": 0.8,
                               "unit": "ratio", "budget": "2 seconds",
                               "direction": "higher_is_better", "max_regression": 0.01}],
            "equal_quality": [{"metric": "end_to_end_ms", "split": "calibration",
                               "baseline": 220.0, "candidate": 100.0,
                               "unit": "ms", "budget": "recall >= 0.8",
                               "direction": "lower_is_better", "max_regression": 0}],
            "resource_regressions": [],
        },
        "adoption": {"decision": decision, "mode": mode,
                     "execution_policy": "on_demand",
                     "rationale": "Measured against the incumbent",
                     "selection_splits": ["calibration"]},
        "regression_risks": ["small calibration sample"],
        "unsupported_cases": ["multi-object masks"],
        "reports": reports,
    }
    path = tmp_path / "qualification.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


def test_speed_only_adoption_requires_two_times_e2e_gain(tmp_path):
    result = evaluate_record(_record(tmp_path), tmp_path)

    assert result["passed"]
    assert result["end_to_end_speedup"] == pytest.approx(2.2)
    assert result["resource_deltas"]["end_to_end_ms"] < 0


def test_speed_only_regression_fails_but_resource_deltas_remain_separate(tmp_path):
    result = evaluate_record(_record(tmp_path, speed=1.8), tmp_path)

    assert not result["passed"]
    assert "2x" in result["failures"][0]
    assert result["resource_deltas"]["end_to_end_ms"] < 0
    assert result["selection_quality_regressions"] == []


def test_held_out_regression_is_reported_but_does_not_select(tmp_path):
    result = evaluate_record(_record(tmp_path, held_out_regression=True), tmp_path)

    assert result["passed"]
    assert result["quality_regressions"] == ["recall"]
    assert result["selection_quality_regressions"] == []


def test_calibration_regression_blocks_adoption(tmp_path):
    record = _record(tmp_path)
    record["comparison"]["quality"][0]["candidate"] = 0.7

    result = evaluate_record(record, tmp_path)

    assert not result["passed"]
    assert result["selection_quality_regressions"] == ["recall"]


def test_held_out_split_cannot_be_used_to_select(tmp_path):
    record = _record(tmp_path)
    record["adoption"]["selection_splits"] = ["held_out"]

    with pytest.raises(QualificationError, match="training/calibration only"):
        evaluate_record(record, tmp_path)


def test_always_on_requires_downstream_utility_gain(tmp_path):
    record = _record(tmp_path)
    record["adoption"]["execution_policy"] = "always_on"

    result = evaluate_record(record, tmp_path)

    assert not result["passed"]
    assert any("downstream utility" in failure for failure in result["failures"])


def test_adoption_requires_complete_resource_measurements(tmp_path):
    record = _record(tmp_path)
    record["comparison"]["resources"]["candidate"]["decode_preprocess_ms"] = None

    result = evaluate_record(record, tmp_path)

    assert not result["passed"]
    assert any("decode_preprocess_ms" in failure for failure in result["failures"])


def test_model_revision_must_match_frozen_report(tmp_path):
    record = _record(tmp_path)
    record["candidate"]["model_revision"] = "not-in-reports"

    with pytest.raises(QualificationError, match="does not match its report revision"):
        evaluate_record(record, tmp_path)
