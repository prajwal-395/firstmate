import copy

import pytest

from library.tools.analysis.temporal_event_benchmark import (
    evaluate,
    m7_m8_proposals,
)


@pytest.fixture
def held_out_sample():
    return {
        "schema_version": 1,
        "split": "held_out",
        "sources": [
            {
                "source_id": "source-a",
                "duration_seconds": 12,
                "proposal_generation_seconds": 0.03,
                "proposals": [
                    {
                        "proposal_id": "p1",
                        "event_class": "reach",
                        "identity_id": "person-1",
                        "start": 1.0,
                        "end": 3.0,
                        "proposal_method": "geometric",
                        "verifier": {
                            "decision": "yes",
                            "seconds": 0.4,
                            "model_id": "offline-test",
                        },
                    },
                    {
                        "proposal_id": "p2",
                        "event_class": "reach",
                        "identity_id": "person-2",
                        "start": 5.0,
                        "end": 7.0,
                        "proposal_method": "geometric",
                        "verifier": {
                            "decision": "no",
                            "seconds": 0.2,
                            "model_id": "offline-test",
                        },
                    },
                    {
                        "proposal_id": "p3",
                        "event_class": "pick_up",
                        "identity_id": None,
                        "start": 8.0,
                        "end": 9.0,
                        "proposal_method": "geometric",
                        "verifier": {
                            "decision": "abstain",
                            "seconds": 0.1,
                            "model_id": "offline-test",
                        },
                    },
                ],
                "events": [
                    {
                        "event_id": "e1",
                        "event_class": "reach",
                        "identity_id": "person-1",
                        "start": 1.2,
                        "end": 2.8,
                        "contact_evidence": "hand approaches cup",
                        "state_change_evidence": None,
                        "completeness": "complete",
                        "provenance": {
                            "annotator_id": "reviewer-1",
                            "review_status": "adjudicated",
                            "evidence_ref": "frame:1.5",
                        },
                    },
                    {
                        "event_id": "e2",
                        "event_class": "pick_up",
                        "identity_id": "person-1",
                        "start": 8.0,
                        "end": 9.0,
                        "contact_evidence": "hand grips mug",
                        "state_change_evidence": "mug leaves table",
                        "completeness": "partial",
                        "provenance": {
                            "annotator_id": "reviewer-1",
                            "review_status": "reviewed",
                            "evidence_ref": "frame:8.5",
                        },
                    },
                ],
                "hard_negatives": [
                    {
                        "negative_id": "n1",
                        "event_class": "reach",
                        "identity_id": "person-2",
                        "start": 5.0,
                        "end": 7.0,
                        "reason": "hand gesture without target contact",
                    },
                ],
            }
        ],
    }


@pytest.fixture
def custodian_manifest():
    return {
        "held_out_access": {
            "status": "sealed_custodian_only",
            "tuning_access": False,
            "label_payloads_in_repository": False,
        },
        "sources": [
            {"source_id": "source-a", "split": "held_out"},
            {"source_id": "source-b", "split": "held_out"},
        ],
    }


def test_reports_proposal_and_verified_quality_separately(
    held_out_sample, custodian_manifest
):
    report = evaluate(held_out_sample, corpus_manifest=custodian_manifest)
    assert report["proposal"]["precision"] == pytest.approx(2 / 3)
    assert report["proposal"]["recall"] == 1
    assert report["verified"]["precision"] == 1
    assert report["verified"]["recall"] == pytest.approx(1 / 2)
    assert report["verified"]["identity_accuracy"] == 1
    assert report["verifier"]["abstain"] == 1
    assert report["verifier"]["abstention_rate"] == pytest.approx(1 / 3)
    assert report["verifier"]["seconds"] == pytest.approx(0.7)
    assert report["proposal_generation"]["seconds"] == pytest.approx(0.03)
    assert report["hard_negative_verifier"]["rejection_rate"] == 1
    assert report["by_event_class"]["pick_up"]["verified"]["recall"] == 0
    assert report["verified"]["onset_error_mean_seconds"] == pytest.approx(0.2)
    assert report["verified"]["offset_error_mean_seconds"] == pytest.approx(0.2)


def test_reports_repeated_event_recall_and_directional_event_classes(
    held_out_sample, custodian_manifest
):
    source = held_out_sample["sources"][0]
    second = copy.deepcopy(source["events"][0])
    second.update(
        event_id="e1-repeat", start=3.0, end=4.0, repeat_group_id="wave-1",
        event_class="waving",
    )
    first = copy.deepcopy(source["events"][0])
    first.update(event_class="waving", repeat_group_id="wave-1")
    source["events"][0] = first
    source["events"].append(second)
    source["proposals"].append(
        {
            "proposal_id": "p-repeat",
            "event_class": "waving",
            "identity_id": "person-1",
            "start": 3.0,
            "end": 4.0,
            "proposal_method": "m7_geometry:hand_raise",
            "verifier": {"decision": "yes", "seconds": 0.1, "model_id": "test"},
        }
    )

    report = evaluate(held_out_sample, corpus_manifest=custodian_manifest)

    assert report["repeated_events"]["group_count"] == 1
    assert report["repeated_events"]["gold_instances"] == 2
    assert report["repeated_events"]["proposal_recall"] == 0.5
    assert report["repeated_events"]["verified_recall"] == 0.5
    assert report["by_event_class"]["waving"]["proposal"]["recall"] == 0.5


def test_scores_boundary_refinement_separately_from_event_detection(
    held_out_sample, custodian_manifest
):
    source = held_out_sample["sources"][0]
    source["proposals"].append(
        {
            "proposal_id": "refined-p1",
            "event_class": "reach",
            "identity_id": "person-1",
            "start": 1.2,
            "end": 2.8,
            "proposal_method": "test_refiner",
            "stage": "boundary_refinement",
            "refines_proposal_id": "p1",
            "verifier": {"decision": "not_run", "seconds": None, "model_id": "none"},
        }
    )

    report = evaluate(held_out_sample, corpus_manifest=custodian_manifest)

    assert report["event_detection"]["predictions"] == 3
    assert report["boundary_refinement"]["predictions"] == 1
    assert report["boundary_refinement"]["temporal_iou_mean"] == 1.0
    assert report["verified"]["predictions"] == 1
    assert report["verified_boundary_refinement"]["predictions"] == 0


def test_quality_scoring_refuses_non_held_out_split(
    held_out_sample, custodian_manifest
):
    held_out_sample["split"] = "calibration"
    with pytest.raises(ValueError, match="held_out"):
        evaluate(held_out_sample, corpus_manifest=custodian_manifest)


def test_quality_scoring_requires_manifest_custodian_release(
    held_out_sample, custodian_manifest
):
    custodian_manifest["held_out_access"]["status"] = "labels_not_created"
    with pytest.raises(ValueError, match="custodian control"):
        evaluate(held_out_sample, corpus_manifest=custodian_manifest)


def test_rejects_event_without_contact_or_state_change_evidence(
    held_out_sample, custodian_manifest
):
    row = held_out_sample["sources"][0]["events"][0]
    row["contact_evidence"] = None
    row["state_change_evidence"] = ""
    with pytest.raises(ValueError, match="evidence"):
        evaluate(held_out_sample, corpus_manifest=custodian_manifest)


def test_one_prediction_cannot_claim_two_human_events(
    held_out_sample, custodian_manifest
):
    held_out_sample["sources"][0]["events"].append(
        {
            "event_id": "e3",
            "event_class": "reach",
            "identity_id": "person-1",
            "start": 1.0,
            "end": 2.0,
            "contact_evidence": "contact",
            "state_change_evidence": None,
            "completeness": "uncertain",
            "provenance": {
                "annotator_id": "reviewer-1",
                "review_status": "reviewed",
                "evidence_ref": "frame:1.5",
            },
        }
    )
    report = evaluate(held_out_sample, corpus_manifest=custodian_manifest)
    assert report["proposal"]["true_positives"] == 2
    assert report["proposal"]["false_negatives"] == 1


def test_identity_is_scored_separately_from_localization(
    held_out_sample, custodian_manifest
):
    row = held_out_sample["sources"][0]["proposals"][0]
    row["identity_id"] = "wrong-person"
    report = evaluate(held_out_sample, corpus_manifest=custodian_manifest)
    assert report["proposal"]["recall"] == 1
    assert report["proposal"]["identity_accuracy"] == 0


def test_matching_never_crosses_source_boundaries(held_out_sample, custodian_manifest):
    source_b = {
        "source_id": "source-b",
        "duration_seconds": 12,
        "proposals": [],
        "events": [
            {
                "event_id": "e3",
                "event_class": "reach",
                "identity_id": "person-9",
                "start": 1.2,
                "end": 2.8,
                "contact_evidence": "hand approaches cup",
                "state_change_evidence": None,
                "completeness": "complete",
                "provenance": {
                    "annotator_id": "reviewer-1",
                    "review_status": "adjudicated",
                    "evidence_ref": "frame:1.5",
                },
            }
        ],
        "hard_negatives": [],
    }
    held_out_sample["sources"].append(source_b)
    report = evaluate(held_out_sample, corpus_manifest=custodian_manifest)
    assert report["proposal"]["true_positives"] == 2
    assert report["proposal"]["false_negatives"] == 1


def test_matching_maximizes_cardinality_before_overlap_quality(
    held_out_sample, custodian_manifest
):
    held_out_sample = copy.deepcopy(held_out_sample)
    source = held_out_sample["sources"][0]
    template = source["proposals"][0]
    source["proposals"] = [
        {
            **template,
            "proposal_id": "wide",
            "event_class": "reach",
            "start": 0,
            "end": 10,
        },
        {
            **template,
            "proposal_id": "only-left",
            "event_class": "reach",
            "start": 0,
            "end": 4,
        },
    ]
    event_template = source["events"][0]
    source["events"] = [
        {
            **event_template,
            "event_id": "left",
            "event_class": "reach",
            "start": 0,
            "end": 4,
        },
        {
            **event_template,
            "event_id": "right",
            "event_class": "reach",
            "start": 6,
            "end": 10,
        },
    ]
    report = evaluate(
        held_out_sample, iou_threshold=0.4, corpus_manifest=custodian_manifest
    )
    assert report["proposal"]["true_positives"] == 2


def test_m7_m8_adapter_keeps_unmapped_verdict_as_abstention():
    m7 = {
        "candidates": {
            "object_pickup": {
                "spans": [
                    {
                        "face_track": "face-1",
                        "start": 1.0,
                        "end": 2.0,
                        "frames": [{"t": 1.0, "d": 0}, {"t": 1.5, "d": 1}],
                    },
                    {
                        "face_track": "face-2",
                        "start": 3.0,
                        "end": 4.0,
                        "frames": [{"t": 3.0, "d": 0}, {"t": 3.5, "d": 1}],
                    },
                ]
            }
        }
    }
    m8 = {
        "verdicts": {
            "key": {
                "statement": "picks up a cup",
                "frames": [1.0, 1.5],
                "answer": "yes",
                "seconds": 0.7,
                "model": "gemma-local",
            }
        }
    }
    result = m7_m8_proposals(
        m7,
        m8,
        source_id="source-a",
        duration_seconds=5,
        class_by_predicate={"object_pickup": "pick_up"},
        statement_by_predicate={"object_pickup": "picks up a cup"},
    )
    assert result["proposals"][0]["verifier"]["decision"] == "yes"
    assert result["proposals"][1]["verifier"]["decision"] == "not_run"
    assert result["proposals"][1]["verifier"]["seconds"] is None
    assert result["proposals"][0]["proposal_method"] == "m7_geometry:object_pickup"
