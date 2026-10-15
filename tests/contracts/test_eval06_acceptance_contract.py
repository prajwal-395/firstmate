"""Acceptance gate for EVAL-06's measured held-out identity evidence."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "docs" / "benchmarks" / "2026-10-14-object-continuity-benchmark.json"
EVIDENCE = ROOT / "library" / "tools" / "project_model_evidence.py"


def test_held_out_identity_results_replace_the_unmeasured_evidence_gap():
    report = json.loads(RESULT.read_text(encoding="utf-8"))
    note = EVIDENCE.read_text(encoding="utf-8")

    assert report["schema"] == "object-continuity-score-v1"
    assert report["annotation"]["split"] == "held_out"
    assert report["annotation"]["tuning_access"] is False
    assert set(report["annotation"]["source_ids"]) >= {"LC4930", "LCATL0011"}
    assert report["annotation"]["review_status"]
    assert report["annotation"]["access_audit_sha256"]
    assert report["hard_case_coverage"]["complete"] is True
    assert report["cost"]["wall_seconds"] > 0
    assert report["cost"]["peak_rss_mb"] > 0
    assert report["cost"]["frames_processed"] > 0
    assert isinstance(report["cost"]["failures"], list)
    assert report["producer"]["name"]
    assert report["producer"]["revision"]
    assert "threshold" in report["producer"]
    assert report["producer"]["sampling_schedule"]
    assert report["by_class"]
    required_metrics = {
        "idf1", "fragmentations", "state_transition_accuracy",
        "state_transitions_unscored",
        "cross_clip_identity_accuracy", "cross_clip_abstentions",
    }
    assert all(required_metrics <= result.keys()
               for result in report["by_class"].values())
    assert any(result["cross_clip_entities"] > 0
               for result in report["by_class"].values())
    assert sum(result.get("state_transitions_scored", 0)
               + result["state_transitions_unscored"]
               for result in report["by_class"].values()) > 0
    assert (report["prediction_abstentions"] > 0
            or any(result["cross_clip_abstentions"] > 0
                   for result in report["by_class"].values())
            or report["cost"]["failures"])
    assert "No cross-clip object re-identification is measured" not in note
    assert "2026-10-14-object-continuity-benchmark.json" in note
