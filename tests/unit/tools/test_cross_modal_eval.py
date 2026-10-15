"""The scorer consumes frozen labels and outputs only; it never runs models."""

import copy
import json
from pathlib import Path

import pytest

from library.tools.cross_modal_eval import (
    check_corpus_manifest,
    predictions_from_project_model,
    score,
)


def _relation(
    subject="utterance-1",
    predicate="spoken_by",
    object_id="person-1",
    certainty="measured",
    provenance=("prov-speech", "prov-face"),
):
    return {
        "subject_id": subject,
        "predicate": predicate,
        "object_id": object_id,
        "certainty": certainty,
        "provenance_ids": list(provenance),
        "evidence_provenance_ids": list(provenance),
    }


def _prediction(**overrides):
    row = {
        "subject_id": "utterance-1",
        "predicate": "spoken_by",
        "object_id": "person-1",
        "certainty": "measured",
        "evidence_provenance_ids": ["prov-face"],
    }
    row.update(overrides)
    return row


@pytest.fixture
def record():
    return {
        "benchmark_id": "cross-modal-v1",
        "split": "held_out",
        "tuning_access": False,
        "annotation_state": "sealed_custodian_only",
        "domain": "unfamiliar_outdoor_vlog",
        "source_ids": ["source-a", "source-b"],
        "executor": "cross-modal-grounding",
        "executor_version": "v1",
        "cost": {"elapsed_seconds": 12.0, "model_passes": 0},
        "cases": [
            {
                "case_id": "speech-1",
                "modality": "speech_person",
                "open_set": False,
                "gold": [_relation()],
                "predictions": [_prediction()],
            },
            {
                "case_id": "scene-1",
                "modality": "scene_category",
                "open_set": True,
                "gold": [],
                "predictions": [],
            },
        ],
    }


def _by_modality(result):
    return {row["modality"]: row for row in result["scorecards"]}


def test_reports_relation_quality_abstention_provenance_certainty_and_cost(record):
    result = score(record)

    cards = _by_modality(result)
    speech, scene = cards["speech_person"], cards["scene_category"]
    assert (speech["precision"], speech["recall"], speech["f1"]) == (1, 1, 1)
    assert speech["predicted_relations"] == 1
    assert speech["predicted_relation_instances"] == 1
    assert speech["matched_relations_with_supported_provenance"] == 1
    assert (
        (speech["certainty_accuracy_by_predicted_label"]["measured"]["accuracy"]) == 1
    )
    assert scene["open_set_abstention_rate"] == 1
    assert result["cost"]["seconds_per_source"] == 6


def test_empty_denominators_report_null_not_zero(record):
    record["cases"] = [
        {
            "case_id": "scene-1",
            "modality": "scene_category",
            "open_set": False,
            "gold": [],
            "predictions": [],
        }
    ]

    card = _by_modality(score(record))["scene_category"]

    assert (card["precision"], card["recall"], card["f1"]) == (None, None, None)
    assert card["open_set_abstention_rate"] is None


def test_refuses_calibration_or_unsealed_held_out_labels(record):
    for field, value in (("split", "calibration"), ("annotation_state", "not_created")):
        invalid = copy.deepcopy(record)
        invalid[field] = value
        with pytest.raises(ValueError):
            score(invalid)


def test_refuses_tuning_access_missing_provenance_or_bad_certainty(record):
    invalid = copy.deepcopy(record)
    invalid["tuning_access"] = True
    with pytest.raises(ValueError):
        score(invalid)
    invalid = copy.deepcopy(record)
    invalid["cases"][0]["predictions"][0]["evidence_provenance_ids"] = []
    with pytest.raises(ValueError):
        score(invalid)
    invalid = copy.deepcopy(record)
    invalid["cases"][0]["gold"][0]["certainty"] = "guessed"
    with pytest.raises(ValueError):
        score(invalid)
    invalid = copy.deepcopy(record)
    invalid["cases"][0]["modality"] = "vibes"
    with pytest.raises(ValueError):
        score(invalid)


def test_counts_wrong_relation_and_non_abstention_on_open_set(record):
    record["cases"][1]["predictions"].append(
        _prediction(
            subject="scene-1",
            predicate="category",
            object_id="unknown",
            certainty="inferred",
            evidence_provenance_ids=["prov-scene"],
        )
    )

    scene = _by_modality(score(record))["scene_category"]

    assert scene["false_positive"] == 1
    assert scene["open_set_correct_abstentions"] == 0
    assert scene["open_set_abstention_rate"] == 0


def _manifest(*held_out):
    return {
        "held_out_access": {
            "tuning_access": False,
            "label_payloads_in_repository": False,
        },
        "sources": [{"source_id": sid, "split": "held_out"} for sid in held_out],
    }


def test_manifest_check_refuses_sources_not_held_out(record):
    record["source_ids"] = ["source-a", "source-c"]

    with pytest.raises(ValueError, match="not held out"):
        score(record, _manifest("source-a", "source-b"))
    with pytest.raises(ValueError, match="tuning access"):
        score(
            record,
            {
                "held_out_access": {
                    "tuning_access": True,
                    "label_payloads_in_repository": False,
                },
                "sources": [],
            },
        )

    cards = _by_modality(
        score(
            dict(record, source_ids=["source-a", "source-b"]),
            _manifest("source-a", "source-b"),
        )
    )
    assert cards["speech_person"]["precision"] == 1


def test_eval01_manifest_holds_out_the_baseline_slice():
    manifest = json.loads(
        Path("docs/benchmarks/2026-10-15-editorial-eval-corpus.json").read_text(
            encoding="utf-8"
        )
    )
    held_out = {
        row["source_id"] for row in manifest["sources"] if row["split"] == "held_out"
    }

    assert {"LC4930", "LCATL0011"} <= held_out
    check_corpus_manifest({"source_ids": ["LC4930", "LCATL0011"]}, manifest)


def test_labels_pending_scores_abstention_only_and_records_predictions():
    record = {
        "benchmark_id": "cross-modal-v1",
        "split": "held_out",
        "tuning_access": False,
        "annotation_state": "labels_pending_custodian",
        "domain": "podcast_interview",
        "source_ids": ["source-a"],
        "executor": "cross-modal-grounding",
        "executor_version": "v1",
        "cost": {"elapsed_seconds": 2.0, "model_passes": 0},
        "cases": [
            {
                "case_id": "speech",
                "modality": "speech_person",
                "open_set": True,
                "gold": [],
                "predictions": [_prediction()],
            },
            {
                "case_id": "scene",
                "modality": "scene_category",
                "open_set": True,
                "gold": [],
                "predictions": [],
            },
        ],
    }

    cards = _by_modality(score(record))

    assert cards["speech_person"]["predicted_relations"] == 1
    assert cards["speech_person"]["predicted_relation_instances"] == 1
    assert (
        cards["speech_person"]["precision"],
        cards["speech_person"]["recall"],
        cards["speech_person"]["f1"],
    ) == (None, None, None)
    assert cards["speech_person"]["false_positive"] is None
    assert cards["speech_person"]["open_set_abstention_rate"] == 0
    assert cards["scene_category"]["open_set_abstention_rate"] == 1


def test_labels_pending_refuses_closed_cases_or_any_gold():
    base = {
        "benchmark_id": "cross-modal-v1",
        "split": "held_out",
        "tuning_access": False,
        "annotation_state": "labels_pending_custodian",
        "domain": "podcast_interview",
        "source_ids": ["source-a"],
        "executor": "cross-modal-grounding",
        "executor_version": "v1",
        "cost": {"elapsed_seconds": 2.0, "model_passes": 0},
    }
    closed = dict(
        base,
        cases=[
            {
                "case_id": "speech",
                "modality": "speech_person",
                "open_set": False,
                "gold": [],
                "predictions": [],
            }
        ],
    )
    with pytest.raises(ValueError, match="open-set"):
        score(closed)
    guarded = dict(
        base,
        cases=[
            {
                "case_id": "speech",
                "modality": "speech_person",
                "open_set": True,
                "gold": [_relation()],
                "predictions": [],
            }
        ],
    )
    with pytest.raises(ValueError, match="empty gold"):
        score(guarded)


def test_exporter_maps_grounding_relations_keeping_provenance_and_certainty():
    model = {
        "relations": [
            {
                "subject_id": "speaker-0",
                "predicate": "speaks_as",
                "object_id": "person-0",
                "certainty": "inferred",
                "evidence_ids": ["utterance-0", "turn-0"],
                "provenance_id": "prov-a",
                "status": "verified",
            },
            {
                "subject_id": "speaker-0",
                "predicate": "refers_to",
                "object_id": "object-cup",
                "certainty": "inferred",
                "evidence_ids": ["utterance-0", "object-observation"],
                "provenance_id": "prov-a",
                "status": "candidate",
            },
            {
                "subject_id": "speaker-0",
                "predicate": "invented_by_a_model",
                "object_id": "person-0",
                "certainty": "inferred",
                "evidence_ids": ["utterance-0"],
                "provenance_id": "prov-a",
                "status": "candidate",
            },
        ],
        "observations": [
            {
                "id": "report-0",
                "modality": "cross_modal_report",
                "payload": {"result": "disagreement"},
            },
        ],
    }

    grouped = predictions_from_project_model(model)

    assert set(grouped) == {"speech_person", "speech_reference"}
    spoken = grouped["speech_person"][0]
    assert (spoken["subject_id"], spoken["predicate"], spoken["object_id"]) == (
        "speaker-0",
        "speaks_as",
        "person-0",
    )
    assert spoken["certainty"] == "inferred"
    assert spoken["evidence_provenance_ids"] == ["turn-0", "utterance-0"]

    # The exported predictions validate against the scorer's own contract.
    record = {
        "benchmark_id": "cross-modal-v1",
        "split": "held_out",
        "tuning_access": False,
        "annotation_state": "labels_pending_custodian",
        "domain": "podcast_interview",
        "source_ids": ["source-a"],
        "executor": "cross-modal-grounding",
        "executor_version": "v1",
        "cost": {"elapsed_seconds": 1.0, "model_passes": 0},
        "cases": [
            {
                "case_id": f"held-out-{modality}",
                "modality": modality,
                "open_set": True,
                "gold": [],
                "predictions": [
                    {
                        k: row[k]
                        for k in (
                            "subject_id",
                            "predicate",
                            "object_id",
                            "certainty",
                            "evidence_provenance_ids",
                        )
                    }
                    for row in rows
                ],
            }
            for modality, rows in grouped.items()
        ],
    }
    cards = _by_modality(score(record))
    assert cards["speech_person"]["predicted_relations"] == 1
    assert cards["speech_reference"]["predicted_relations"] == 1


def test_repeated_instances_of_one_relation_count_once(record):
    record["cases"][0]["predictions"] = [_prediction(), _prediction()]

    speech = _by_modality(score(record))["speech_person"]

    assert speech["predicted_relation_instances"] == 2
    assert speech["predicted_relations"] == 1
    assert (speech["precision"], speech["recall"], speech["f1"]) == (1, 1, 1)


def test_frozen_baseline_input_rescores_to_the_frozen_report():
    root = Path("docs/benchmarks")
    record = json.loads(
        (root / "eval09-cross-modal-held-out-input.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (root / "2026-10-15-editorial-eval-corpus.json").read_text(encoding="utf-8")
    )
    expected = json.loads(
        (root / "eval09-cross-modal-held-out-report.json").read_text(encoding="utf-8")
    )

    assert score(record, manifest) == expected
