"""Unit tests for the frozen-artifact event-caption scorer (SEM-015)."""

import copy

import pytest

from library.tools.event_caption_benchmark import (
    FORMAT,
    BenchmarkError,
    score_events,
)


def _event(event_id, source_id, action, start, end, frames=None):
    evidence = ([{
        "source_id": source_id,
        "interval": {"start_seconds": start, "end_seconds": end},
        "frame_times_seconds": frames,
    }] if frames is not None else [])
    return {
        "event_id": event_id,
        "source_id": source_id,
        "interval": {"start_seconds": start, "end_seconds": end},
        "action": action,
        "entities": [],
        "relations": [],
        "evidence": evidence,
        "provenance": {"method": "agent-label"},
    }


def _doc(kind, events):
    return {"format": FORMAT, "kind": kind, "events": events}


@pytest.fixture
def matched_pair():
    references = _doc("references", [
        _event("r1", "src-a", "lifts cup", 1.2, 2.7),
    ])
    predictions = _doc("predictions", [
        _event("p1", "src-a", "Lifts, cup!", 1.0, 2.5, frames=[1.5, 2.0]),
    ])
    return references, predictions


def test_match_scores_recall_precision_and_timestamp_errors(matched_pair):
    references, predictions = matched_pair
    result = score_events(references, predictions)
    assert result["reference_events"] == 1
    assert result["predicted_events"] == 1
    assert result["matched_events"] == 1
    assert result["event_recall"] == 1.0
    assert result["description_precision"] == 1.0
    assert result["unsupported_claim_rate"] == 0.0
    assert result["evidence_coverage"] == 1.0
    assert result["timestamp_start_mae_seconds"] == pytest.approx(0.2)
    assert result["timestamp_end_mae_seconds"] == pytest.approx(0.2)
    (match,) = result["matches"]
    assert match["prediction_id"] == "p1"
    assert match["reference_id"] == "r1"
    assert match["iou"] == pytest.approx(1.3 / 1.7)


def test_action_mismatch_is_not_a_match(matched_pair):
    references, predictions = matched_pair
    predictions["events"][0]["action"] = "opens door"
    result = score_events(references, predictions)
    assert result["matched_events"] == 0
    assert result["event_recall"] == 0.0
    assert result["description_precision"] == 0.0


def test_distant_interval_is_not_a_match(matched_pair):
    references, predictions = matched_pair
    predictions["events"][0]["interval"] = {
        "start_seconds": 20.0, "end_seconds": 22.0}
    predictions["events"][0]["evidence"][0]["frame_times_seconds"] = [20.5]
    result = score_events(references, predictions)
    assert result["matched_events"] == 0
    assert result["event_recall"] == 0.0


def test_cross_source_predictions_never_match(matched_pair):
    references, predictions = matched_pair
    predictions["events"][0]["source_id"] = "src-b"
    predictions["events"][0]["evidence"][0]["source_id"] = "src-b"
    result = score_events(references, predictions)
    assert result["matched_events"] == 0


def test_evidence_free_prediction_counts_as_unsupported(matched_pair):
    references, predictions = matched_pair
    predictions["events"][0]["evidence"] = []
    result = score_events(references, predictions)
    assert result["matched_events"] == 1
    assert result["unsupported_claim_rate"] == 1.0
    assert result["evidence_coverage"] == 0.0


def test_out_of_span_evidence_counts_as_unsupported(matched_pair):
    references, predictions = matched_pair
    predictions["events"][0]["evidence"][0][
        "frame_times_seconds"] = [9.0]
    result = score_events(references, predictions)
    assert result["unsupported_claim_rate"] == 1.0


def test_each_prediction_matches_at_most_once():
    references = _doc("references", [
        _event("r1", "src-a", "lifts cup", 1.0, 3.0),
    ])
    predictions = _doc("predictions", [
        _event("p1", "src-a", "lifts cup", 1.0, 2.0, frames=[1.5]),
        _event("p2", "src-a", "lifts cup", 2.0, 3.0, frames=[2.5]),
    ])
    result = score_events(references, predictions)
    assert result["matched_events"] == 1
    assert result["event_recall"] == 1.0
    assert result["description_precision"] == pytest.approx(0.5)


def test_empty_predictions_score_zero_without_dividing():
    references = _doc("references", [
        _event("r1", "src-a", "lifts cup", 1.0, 3.0),
    ])
    result = score_events(references, _doc("predictions", []))
    assert result["matched_events"] == 0
    assert result["event_recall"] == 0.0
    assert result["description_precision"] is None
    assert result["unsupported_claim_rate"] is None


def test_wrong_format_is_refused(matched_pair):
    references, predictions = matched_pair
    broken = copy.deepcopy(predictions)
    broken["format"] = "something-else/9"
    with pytest.raises(BenchmarkError):
        score_events(references, broken)


def test_prediction_without_evidence_list_is_refused(matched_pair):
    references, predictions = matched_pair
    broken = copy.deepcopy(predictions)
    del broken["events"][0]["evidence"]
    with pytest.raises(BenchmarkError):
        score_events(references, broken)


def test_inverted_interval_is_refused(matched_pair):
    references, predictions = matched_pair
    broken = copy.deepcopy(predictions)
    broken["events"][0]["interval"] = {
        "start_seconds": 3.0, "end_seconds": 3.0}
    with pytest.raises(BenchmarkError):
        score_events(references, broken)
