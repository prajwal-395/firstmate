"""The sound-event benchmark scores what AUD-056 requires and nothing else.

Onset/offset errors are measured on pairs matched at IoU >= 0.5;
a near-miss overlap must not launder edge accuracy. Matching is
label-constrained and one-to-one, so sharing a label with no overlap
is a miss plus a false alarm. Empty denominators report None rather
than zero, and the module never names a winner between versions.
"""
import json

import pytest

from library.tools.analysis import sound_event_benchmark as seb


def _source(**overrides):
    doc = {
        "source_id": "s1",
        "duration_seconds": 10.0,
        "proposals": [],
        "reference_events": [],
        "hard_negatives": [],
    }
    doc.update(overrides)
    return doc


def _doc(*sources, split="calibration"):
    return {"schema_version": 1, "split": split, "sources": list(sources)}


def _proposal(pid, label, start, end, confidence=0.8):
    return {"proposal_id": pid, "label": label, "start": start, "end": end,
            "confidence": confidence, "proposal_method": "m", "model_id": "m"}


def _ref(eid, label, start, end, rarity="common"):
    return {"event_id": eid, "label": label, "start": start, "end": end,
            "rarity": rarity,
            "provenance": {"annotator_id": "agent:family-a",
                           "review_status": "reviewed",
                           "evidence_ref": "s1:1.0-2.0"}}


# ── temporal IoU ───────────────────────────────────────────────────

def test_iou_containment_overlap_and_disjoint():
    assert seb.temporal_iou((1.0, 2.0), (1.0, 2.0)) == pytest.approx(1.0)
    assert seb.temporal_iou((1.0, 3.0), (2.0, 4.0)) == pytest.approx(1.0 / 3.0)
    assert seb.temporal_iou((1.0, 2.0), (2.0, 3.0)) == 0.0
    assert seb.temporal_iou((1.0, 2.0), (5.0, 6.0)) == 0.0


# ── matching ───────────────────────────────────────────────────────

def test_matching_is_label_constrained_and_one_to_one():
    proposals = [_proposal("p1", "Door slam", 1.0, 2.0),
                 _proposal("p2", "door SLAM", 1.1, 1.9),
                 _proposal("p3", "Music", 1.1, 1.9)]
    refs = [_ref("e1", "Door slam", 1.05, 1.95)]
    matches = seb.match_events(proposals, refs)
    assert len(matches) == 1
    # Best IoU wins: p1 (0.9) overlaps more than p2 (0.889).
    assert matches[0][0]["proposal_id"] == "p1"
    assert matches[0][2] > 0.5


def test_zero_overlap_is_a_miss_plus_a_false_alarm_not_a_match():
    scored = seb.score_source(_source(
        proposals=[_proposal("p1", "Cough", 5.0, 6.0)],
        reference_events=[_ref("e1", "Cough", 1.0, 2.0)]))
    assert scored["n_matched_pairs"] == 0
    cell = scored["by_iou"]["0.5"]
    assert (cell["true_positives"], cell["false_positives"],
            cell["false_negatives"]) == (0, 1, 1)
    assert cell["precision"] == 0.0
    assert cell["recall"] == 0.0
    assert cell["f1"] is None


def test_matching_tie_break_is_deterministic():
    proposals = [_proposal("p2", "Clap", 1.0, 2.0),
                 _proposal("p1", "Clap", 1.0, 2.0)]
    refs = [_ref("e1", "Clap", 1.0, 2.0)]
    first = [(p["proposal_id"], r["event_id"], i)
             for p, r, i in seb.match_events(proposals, refs)]
    second = [(p["proposal_id"], r["event_id"], i)
              for p, r, i in seb.match_events(list(reversed(proposals)), refs)]
    assert first == second == [("p1", "e1", 1.0)]


# ── boundary errors ────────────────────────────────────────────────

def test_boundary_errors_measured_on_primary_matches_only():
    scored = seb.score_source(_source(
        proposals=[_proposal("p1", "Music", 0.3, 2.0),
                    _proposal("p2", "Music", 7.0, 7.6)],
        reference_events=[_ref("e1", "Music", 0.32, 1.92),
                           _ref("e2", "Music", 3.5, 7.7)]))
    # p1/e1 IoU is high; p2/e2 overlaps slightly but under 0.5.
    onset = scored["onset_error_seconds"]["absolute"]
    offset = scored["offset_error_seconds"]["absolute"]
    assert onset["n"] == 1
    assert onset["mean"] == pytest.approx(abs(0.3 - 0.32))
    assert offset["mean"] == pytest.approx(abs(2.0 - 1.92))
    # Signed errors keep direction: early onset is negative.
    assert scored["onset_error_seconds"]["signed"]["mean"] < 0


def test_no_primary_match_reports_null_boundary_errors():
    scored = seb.score_source(_source(
        proposals=[_proposal("p1", "Music", 0.0, 0.5)],
        reference_events=[_ref("e1", "Music", 5.0, 6.0)]))
    assert scored["onset_error_seconds"]["absolute"]["mean"] is None
    assert scored["offset_error_seconds"]["absolute"]["n"] == 0


# ── rare slice and hard negatives ──────────────────────────────────

def test_rare_recall_counts_only_rare_and_nonstandard():
    scored = seb.score_source(_source(
        proposals=[_proposal("p1", "Glass break", 1.0, 1.5)],
        reference_events=[
            _ref("e1", "Glass break", 1.0, 1.5, rarity="rare"),
            _ref("e2", "Music", 2.0, 3.0, rarity="common"),
            _ref("e3", "Siren", 4.0, 5.0, rarity="nonstandard"),
        ]))
    assert scored["n_rare_references"] == 2
    assert scored["rare_recall_at_0_5"] == pytest.approx(0.5)
    assert scored["per_label"]["Glass break"]["rarity"] == ["rare"]


def test_hard_negative_rejection_counts_same_label_overlap_only():
    scored = seb.score_source(_source(
        proposals=[_proposal("p1", "Laughter", 3.1, 3.9),
                    _proposal("p2", "Music", 3.1, 3.9)],
        reference_events=[],
        hard_negatives=[
            {"negative_id": "n1", "label": "Laughter",
             "start": 3.0, "end": 4.0, "reason": "cough mistaken for laugh"},
            {"negative_id": "n2", "label": "Sneeze",
             "start": 3.0, "end": 4.0, "reason": "nothing sneezes here"},
        ]))
    assert scored["hard_negatives"] == {"n": 2, "rejected": 1,
                                        "rejection_rate": pytest.approx(0.5)}


# ── validation ─────────────────────────────────────────────────────

def test_validation_refuses_bad_splits_ids_intervals_and_provenance():
    with pytest.raises(ValueError):
        seb.score_document(_doc(_source(), split="training"))
    with pytest.raises(ValueError):
        seb.score_document(_doc(_source(), _source()))
    with pytest.raises(ValueError):
        seb.score_document(_doc(_source(
            proposals=[_proposal("p1", "X", 2.0, 1.0)])))
    bad = _ref("e1", "X", 1.0, 2.0)
    del bad["provenance"]["annotator_id"]
    with pytest.raises(ValueError):
        seb.score_document(_doc(_source(reference_events=[bad])))
    bad_rarity = _ref("e1", "X", 1.0, 2.0, rarity="everyday")
    with pytest.raises(ValueError):
        seb.score_document(_doc(_source(reference_events=[bad_rarity])))


def test_empty_denominators_report_none_not_zero():
    scored = seb.score_source(_source())
    cell = scored["by_iou"]["0.5"]
    assert cell["precision"] is None
    assert cell["recall"] is None
    assert cell["f1"] is None
    assert scored["rare_recall_at_0_5"] is None
    assert scored["hard_negatives"]["rejection_rate"] is None


# ── aggregates ─────────────────────────────────────────────────────

def test_document_aggregates_by_condition_without_ranking():
    doc = _doc(
        _source(source_id="overlap", duration_seconds=10.0,
                conditions=["overlapping_speech_music"],
                proposals=[_proposal("p1", "Music", 1.0, 2.0)],
                reference_events=[_ref("e1", "Music", 1.0, 2.0)]),
        _source(source_id="quiet", duration_seconds=10.0,
                conditions=["quiet_room"],
                proposals=[_proposal("p2", "Music", 1.0, 2.0)],
                reference_events=[_ref("e2", "Music", 5.0, 6.0)]),
    )
    result = seb.score_document(doc)
    assert result["overall_at_0_5"]["true_positives"] == 1
    assert result["overall_at_0_5"]["recall"] == pytest.approx(0.5)
    assert result["by_condition"]["overlapping_speech_music"]["recall"] == 1.0
    assert result["by_condition"]["quiet_room"]["recall"] == 0.0
    assert "winner" not in json.dumps(result)


def test_document_pools_boundary_errors_across_sources():
    doc = _doc(
        _source(source_id="a", duration_seconds=10.0,
                proposals=[_proposal("p1", "Clap", 1.0, 2.0)],
                reference_events=[_ref("e1", "Clap", 1.1, 2.2)]),
        _source(source_id="b", duration_seconds=10.0,
                proposals=[_proposal("p2", "Clap", 3.0, 4.0)],
                reference_events=[_ref("e2", "Clap", 3.3, 4.1)]),
    )
    result = seb.score_document(doc)
    pooled = result["overall_boundary_errors"]
    assert pooled["onset_absolute"]["n"] == 2
    assert pooled["onset_absolute"]["mean"] == pytest.approx((0.1 + 0.3) / 2)
    assert pooled["offset_absolute"]["mean"] == pytest.approx((0.2 + 0.1) / 2)
    for scored in result["sources"]:
        assert "boundary_samples" not in scored


def test_cli_writes_scored_json(tmp_path):
    src = tmp_path / "input.json"
    out = tmp_path / "scored.json"
    src.write_text(json.dumps(_doc(_source(
        proposals=[_proposal("p1", "Clap", 1.0, 2.0)],
        reference_events=[_ref("e1", "Clap", 1.0, 2.0)]))), encoding="utf-8")
    assert seb.main([str(src), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["overall_at_0_5"]["f1"] == 1.0


def test_cli_refuses_unreadable_input():
    with pytest.raises(SystemExit):
        seb.main(["/nonexistent/input.json"])
