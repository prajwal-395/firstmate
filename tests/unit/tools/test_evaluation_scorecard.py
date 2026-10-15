import hashlib
import json

import pytest

from library.tools.evaluation_scorecard import (
    ScorecardError,
    build_scorecard,
)


def _report(version, role, modality, split, metrics, **extra):
    return {
        "format": "ren_evaluation_report/1",
        "revision": "abc123",
        "version": {"id": version, "role": role, "models": {"model": "m@1"}},
        "modality": modality,
        "split": split,
        "metrics": metrics,
        "evidence": [],
        **extra,
    }


def _metric(value, dimension="quality", unit="ratio"):
    return {"value": value, "dimension": dimension, "unit": unit}


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_preserves_modality_split_and_missing_dimensions_without_blending(tmp_path):
    baseline = _write(tmp_path / "baseline.json", _report(
        "v1", "baseline", "speech", "calibration",
        {"wer": _metric(0.2), "p50": _metric(120, "latency", "ms")}))
    candidate = _write(tmp_path / "candidate.json", _report(
        "v2", "candidate", "visual_retrieval", "held_out",
        {"recall_at_5": _metric(0.8)}))

    result = build_scorecard([candidate, baseline])

    assert result["versions"]["v1"]["modalities"]["speech"]["calibration"]["metrics"]["wer"]["value"] == 0.2
    assert result["versions"]["v2"]["modalities"]["visual_retrieval"]["held_out"]["metrics"]["recall_at_5"]["value"] == 0.8
    assert result["versions"]["v1"]["unmeasured"]["speech"] == ["memory", "cost"]
    assert "blended_score" not in result
    assert result["aggregation"] == "modality_and_split_only; no_blended_score_or_version_ranking"
    assert len(result["sources"]) == 2


def test_accepts_retrieval_speech_action_audio_and_editorial_reports(tmp_path):
    modalities = ("retrieval", "speech", "action", "audio", "editorial")
    reports = [_write(tmp_path / f"{name}.json", _report(
        "accepted-1", "accepted", name, "calibration",
        {"quality": _metric(0.75)})) for name in modalities]

    result = build_scorecard(reports)

    assert set(result["versions"]["accepted-1"]["modalities"]) == set(modalities)
    assert all(result["versions"]["accepted-1"]["modalities"][name]["calibration"]["metrics"]
               for name in modalities)


def test_held_out_data_cannot_be_declared_as_selection_basis(tmp_path):
    report = _write(tmp_path / "held.json", _report(
        "v1", "candidate", "speech", "held_out", {"wer": _metric(0.2)},
        selection_basis=["held_out"]))
    with pytest.raises(ScorecardError, match="held-out data cannot select"):
        build_scorecard([report])


def test_evidence_checksum_is_verified_and_frozen_source_is_checksummed(tmp_path):
    evidence = tmp_path / "labels.json"
    evidence.write_text('{"labels": []}', encoding="utf-8")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    report = _write(tmp_path / "report.json", _report(
        "v1", "accepted", "action", "calibration", {"f1": _metric(0.7)},
        evidence=[{"id": "labels", "path": evidence.name, "sha256": digest}],
        selection_basis=["calibration"]))

    result = build_scorecard([report])

    row = result["versions"]["v1"]["modalities"]["action"]["calibration"]
    assert row["evidence_checksums"] == {"labels": digest}
    assert result["sources"][0]["sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    evidence.write_text("changed", encoding="utf-8")
    with pytest.raises(ScorecardError, match="checksum mismatch"):
        build_scorecard([report])


def test_rejects_duplicate_version_modality_split_and_inconsistent_model_versions(tmp_path):
    one = _write(tmp_path / "one.json", _report(
        "v1", "candidate", "audio", "calibration", {"f1": _metric(0.7)}))
    two = _write(tmp_path / "two.json", _report(
        "v1", "candidate", "audio", "calibration", {"f1": _metric(0.8)}))
    with pytest.raises(ScorecardError, match="duplicate report"):
        build_scorecard([one, two])

    two_data = _report("v1", "candidate", "audio", "held_out", {"f1": _metric(0.8)})
    two_data["version"]["models"] = {"model": "m@2"}
    _write(two, two_data)
    with pytest.raises(ScorecardError, match="inconsistent role or model versions"):
        build_scorecard([one, two])
