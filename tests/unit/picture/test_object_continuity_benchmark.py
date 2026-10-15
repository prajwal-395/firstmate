import hashlib
import json

from pytest import approx

from library.tools.object_continuity_benchmark import _maximum_assignment, run, score


def _seal(annotations):
    annotations.update({"source_ids": ["held-out-a", "held-out-b"],
                        "access_audit_id": "audit-eval06-001"})
    payload = json.dumps({"observations": annotations["observations"],
                          "hard_cases": annotations.get("hard_cases", [])}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False)
    annotations["label_set_sha256"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return annotations


def test_scores_identity_state_occlusion_and_cross_clip_abstention():
    annotations = _seal({
        "schema": "object-continuity-v1",
        "split": "held_out",
        "tuning_access": False,
        "annotation_id": "test-labels-v1",
        "label_set_sha256": "a" * 64,
        "annotator_id": "independent-reviewer",
        "review_status": "independently_reviewed",
        "observations": [
            {"clip_id": "a", "frame_id": "0", "time_s": 0,
             "entity_id": "person-1", "class": "person", "box": [0, 0, .4, .8],
             "visibility": "visible", "state": "seated"},
            {"clip_id": "a", "frame_id": "1", "time_s": 1,
             "entity_id": "person-1", "class": "person", "box": None,
             "visibility": "occluded", "state": "seated"},
            {"clip_id": "a", "frame_id": "2", "time_s": 2,
             "entity_id": "person-1", "class": "person", "box": [0, 0, .4, .8],
             "visibility": "visible", "state": "standing"},
            {"clip_id": "b", "frame_id": "0", "time_s": 0,
             "entity_id": "person-1", "class": "person", "box": [0, 0, .4, .8],
             "visibility": "visible", "state": "standing"},
        ],
    })
    predictions = {
        "observations": [
            {"clip_id": "a", "frame_id": "0", "entity_id": "track-a",
             "class": "person", "box": [0, 0, .4, .8], "state": "seated",
             "cross_clip_identity": "linked"},
            {"clip_id": "a", "frame_id": "2", "entity_id": "track-a",
             "class": "person", "box": [0, 0, .4, .8], "state": "standing",
             "cross_clip_identity": "linked"},
            {"clip_id": "b", "frame_id": "0", "entity_id": "track-b",
             "class": "person", "box": [0, 0, .4, .8], "state": "standing",
             "cross_clip_identity": "linked"},
            {"clip_id": "b", "frame_id": "1", "entity_id": "person-2",
             "class": "person", "status": "abstained"},
        ],
    }

    result = score(annotations, predictions)
    person = result["by_class"]["person"]
    assert result["visibility_counts"] == {"visible": 3, "occluded": 1}
    assert result["prediction_abstentions"] == 1
    assert person["idf1"] == 2 / 3  # one identity split across two clip tracks
    assert person["visibility_counts"] == {"visible": 3, "occluded": 1}
    assert person["prediction_abstentions"] == 1
    assert person["fragmentations"] == 0  # the missed occluded frame is ignored
    assert person["state_transition_accuracy"] == 1.0
    assert person["cross_clip_entities"] == 1
    assert person["cross_clip_identity_accuracy"] == 0.0
    assert person["cross_clip_abstentions"] == 0


def test_unmatched_visible_observation_is_a_cross_clip_abstention():
    annotations = _seal({"schema": "object-continuity-v1", "split": "held_out",
                   "tuning_access": False, "annotation_id": "test-labels-v1",
                   "label_set_sha256": "a" * 64,
                   "annotator_id": "independent-reviewer",
                   "review_status": "independently_reviewed", "observations": [
        {"clip_id": clip, "frame_id": "0", "time_s": 0, "entity_id": "cup",
         "class": "cup", "box": [0, 0, .2, .2], "visibility": "visible",
         "state": "on_table"}
        for clip in ("a", "b")
    ]})
    predictions = {"observations": [
        {"clip_id": "a", "frame_id": "0", "entity_id": "cup-a", "class": "cup",
         "box": [0, 0, .2, .2], "state": "on_table"},
        {"clip_id": "b", "frame_id": "0", "entity_id": "cup-b", "class": "cup",
         "status": "abstained"},
    ]}

    result = score(annotations, predictions)["by_class"]["cup"]
    assert result["cross_clip_identity_accuracy"] is None
    assert result["cross_clip_abstentions"] == 1


def test_explicit_cross_clip_link_abstention_is_not_scored_as_wrong_id():
    annotations = _seal({"schema": "object-continuity-v1", "split": "held_out",
        "tuning_access": False, "annotation_id": "abstention-labels",
        "annotator_id": "reviewer", "review_status": "annotator-reviewed",
        "observations": [
            {"clip_id": clip, "frame_id": "0", "time_s": 0,
             "entity_id": "cup", "class": "cup", "box": [0, 0, .2, .2],
             "visibility": "visible", "state": "on_table"}
            for clip in ("a", "b")
        ],
    })
    predictions = {"observations": [
        {"clip_id": clip, "frame_id": "0", "entity_id": f"track-{clip}",
         "class": "cup", "box": [0, 0, .2, .2], "state": "on_table",
         "cross_clip_identity": "abstained"}
        for clip in ("a", "b")
    ]}
    result = score(annotations, predictions)["by_class"]["cup"]
    assert result["cross_clip_identity_accuracy"] is None
    assert result["cross_clip_abstentions"] == 1


def test_visible_gap_fragmentation_and_identity_switch_are_counted():
    annotations = _seal({"schema": "object-continuity-v1", "split": "held_out",
        "tuning_access": False, "annotation_id": "fragment-labels",
        "annotator_id": "reviewer", "review_status": "annotator-reviewed",
        "observations": [
            {"clip_id": "a", "frame_id": str(frame), "time_s": frame,
             "entity_id": "cup", "class": "cup", "box": [0, 0, .2, .2],
             "visibility": "visible", "state": "on_table"}
            for frame in range(3)
        ],
    })
    predictions = {"observations": [
        {"clip_id": "a", "frame_id": "0", "entity_id": "track-a",
         "class": "cup", "box": [0, 0, .2, .2], "cross_clip_identity": "abstained"},
        {"clip_id": "a", "frame_id": "2", "entity_id": "track-b",
         "class": "cup", "box": [0, 0, .2, .2], "cross_clip_identity": "abstained"},
    ]}
    result = score(annotations, predictions)["by_class"]["cup"]
    assert result["fragmentations"] == 1
    assert result["identity_switches"] == 0  # the intervening frame was missed


def test_tuning_split_is_refused():
    annotations = {"schema": "object-continuity-v1", "split": "calibration",
                   "tuning_access": True, "observations": []}
    try:
        score(annotations, {"observations": []})
    except ValueError as exc:
        assert "held_out" in str(exc)
    else:
        raise AssertionError("held-out scorer accepted tuning labels")


def test_identity_assignment_maximizes_correct_id_pairs_not_track_count():
    assert _maximum_assignment({("gt-a", "pred-a"): 10,
                                 ("gt-a", "pred-b"): 1,
                                 ("gt-b", "pred-a"): 1}) == 10


def test_hard_case_coverage_requires_occlusion_and_same_class_lookalikes():
    annotations = _seal({"schema": "object-continuity-v1", "split": "held_out",
        "tuning_access": False, "annotation_id": "hard-case-labels",
        "annotator_id": "reviewer", "review_status": "annotator-reviewed",
        "observations": [
            {"clip_id": "a", "frame_id": "0", "time_s": 0,
             "entity_id": "cup-a", "class": "cup", "box": [0, 0, .2, .2],
             "visibility": "visible", "state": "on_table"},
            {"clip_id": "a", "frame_id": "0", "time_s": 0,
             "entity_id": "cup-b", "class": "cup", "box": [.3, 0, .5, .2],
             "visibility": "visible", "state": "on_table"},
            {"clip_id": "a", "frame_id": "1", "time_s": 1,
             "entity_id": "cup-a", "class": "cup", "box": None,
             "visibility": "occluded", "state": "on_table"},
        ],
        "hard_cases": [
            {"kind": "lookalike_pair", "clip_id": "a", "frame_id": "0",
             "entity_ids": ["cup-a", "cup-b"]},
            {"kind": "occlusion", "clip_id": "a", "frame_id": "1",
             "entity_id": "cup-a"},
        ],
    })
    result = score(annotations, {"observations": []})
    assert result["hard_case_coverage"] == {
        "occlusion_cases": 1, "lookalike_pairs": 1, "complete": True,
    }


def test_run_preserves_measured_cost_and_failures(tmp_path):
    annotations = _seal({"schema": "object-continuity-v1", "split": "held_out",
                   "tuning_access": False, "annotation_id": "test-labels-v1",
                   "label_set_sha256": "a" * 64,
                   "annotator_id": "independent-reviewer",
                   "review_status": "reviewed_by_annotator", "observations": []})
    predictions = {"observations": [], "cost": {
        "scope": "two held-out clips, four sampled frames",
        "wall_seconds": 1.2, "peak_rss_mb": 80,
        "frames_requested": 4, "frames_processed": 3,
        "failures": [{"clip_id": "b", "frame_id": "1", "reason": "decode"}],
    }, "producer": {"name": "detector-tracker", "revision": "model-sha",
                    "threshold": 0.5, "sampling_schedule": "1fps"}}
    labels_path, prediction_path = tmp_path / "labels.json", tmp_path / "predictions.json"
    audit_path = tmp_path / "access-audit.json"
    output_path = tmp_path / "score.json"
    labels_path.write_text(json.dumps(annotations), encoding="utf-8")
    prediction_path.write_text(json.dumps(predictions), encoding="utf-8")
    audit = {"audit_id": annotations["access_audit_id"],
             "label_set_sha256": annotations["label_set_sha256"],
             "tuning_access": False,
             "access_policy": "sealed_held_out_no_tuning",
             "custody_path": str(labels_path.resolve()),
             "custodian": "test-custodian", "sealed_at": "2026-10-14T00:00:00Z"}
    audit_path.write_text(json.dumps(audit), encoding="utf-8")

    result = run(labels_path, prediction_path, output_path, audit_path)
    assert result["cost"]["wall_seconds"] == 1.2
    assert result["cost"]["frames_processed"] == 3
    assert result["annotation"]["split"] == "held_out"
    assert result["annotation"]["tuning_access"] is False
    assert result["cost"]["failures"] == predictions["cost"]["failures"]
    assert result["cost"]["model_pass_seconds_per_processed_frame"] == approx(0.4)
    assert result["annotation"]["access_audit_sha256"] == hashlib.sha256(
        audit_path.read_bytes()
    ).hexdigest()
    assert json.loads(output_path.read_text(encoding="utf-8")) == result
