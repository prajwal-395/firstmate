import pytest

from library.tools.diarization_eval import (
    DiarizationEvaluationError,
    diarization_metrics,
    evaluate_record,
    scorecard_report,
)


def test_overlap_is_scored_as_missed_speaker_time():
    result = diarization_metrics(
        [{"start": 0, "end": 2, "speaker": "a"},
         {"start": 1, "end": 2, "speaker": "b"}],
        [{"start": 0, "end": 2, "speaker": "x"}],
        collar_seconds=0,
    )
    assert result["status"] == "measured"
    assert result["speaker_mapping"] == {"a": "x"}
    assert result["miss_seconds"] == 1.0
    assert result["der"] == 0.333333


def test_wrong_speaker_after_change_reports_confusion_and_missed_change():
    result = diarization_metrics(
        [{"start": 0, "end": 1, "speaker": "a"},
         {"start": 1, "end": 2, "speaker": "b"}],
        [{"start": 0, "end": 2, "speaker": "x"}],
        collar_seconds=0,
    )
    assert result["confusion_seconds"] == 1.0
    assert result["speaker_changes"]["missed"] == 1


def test_uncertainty_and_probabilities_are_preserved_without_calibration_claim():
    result = diarization_metrics(
        [{"start": 0, "end": 2, "speaker": "a"}],
        [{"start": 0, "end": 2, "speakers": ["x"], "uncertain": True,
          "speaker_probabilities": {"x": 0.61}}],
        collar_seconds=0,
    )
    assert result["uncertain_estimate_turns"] == 1
    assert result["probabilities"] == {
        "present": True,
        "calibration": "not assessed; raw model probabilities are not assumed comparable",
    }


def test_evaluation_requires_independent_agent_annotations():
    record = {"sample_id": "s1", "agent_annotations": [
        {"annotator_id": "agent-a"}], "adjudicated_reference": [],
        "prediction": [], "split": "calibration", "annotation_agreement": {},
        "model": {"id": "ecapa"}}
    with pytest.raises(DiarizationEvaluationError, match="two independent"):
        evaluate_record(record)


def test_probability_must_belong_to_a_hypothesized_speaker():
    with pytest.raises(DiarizationEvaluationError, match="invalid speaker probability"):
        diarization_metrics(
            [{"start": 0, "end": 2, "speaker": "a"}],
            [{"start": 0, "end": 2, "speaker": "x",
              "speaker_probabilities": {"y": 0.6}}],
            collar_seconds=0)


def test_agent_label_agreement_is_recorded_before_tie_breaking():
    turn = [{"start": 0, "end": 2, "speaker": "a"}]
    record = {
        "sample_id": "s1", "split": "calibration",
        "agent_annotations": [
            {"annotator_id": "agent-a", "model_family": "family-a",
             "blind": True, "model_output_hidden": True, "turns": turn},
            {"annotator_id": "agent-b", "model_family": "family-b",
             "blind": True, "model_output_hidden": True, "turns": turn}],
        "tie_breaker": {"annotator_id": "agent-c", "model_family": "family-c",
                         "blind": True, "model_output_hidden": True},
        "adjudicated_reference": turn, "prediction": turn,
        "model": {"id": "ecapa"},
    }
    result = evaluate_record(record)
    assert result["annotation_agreement"]["mean_pairwise_der"] == 0.0


def test_scorecard_carries_mode_precision_license_and_runtime_split():
    result = diarization_metrics(
        [{"start": 0, "end": 2, "speaker": "a"}],
        [{"start": 0, "end": 2, "speaker": "x"}], collar_seconds=0)
    measurements = {
        "latency_cold_seconds": 4.0, "latency_warm_seconds": 2.0,
        "peak_memory_mb": 512.0, "decode_seconds": 0.1,
        "inference_seconds": 1.8, "serialize_seconds": 0.1,
        "end_to_end_cost_usd": 0.0,
    }
    report = scorecard_report(
        revision="abc123", version_id="sortformer-streaming",
        role="candidate", model_id="sortformer", model_revision="sha-1",
        precision="fp16", mode="streaming", split="held_out",
        license_class="NVIDIA Open Model License", adoption_decision="research-only",
        sample_results=[result], measurements=measurements,
        evidence=[{"id": "fixture", "sha256": "0" * 64}])
    assert report["metrics"]["mean_der"]["value"] == 0.0
    assert report["diarization"]["precision"] == "fp16"
    assert report["diarization"]["runtime_measurements"] == measurements
    assert report["selection_basis"] == []
