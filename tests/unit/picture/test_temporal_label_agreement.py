"""Unit tests for blind multi-agent label agreement (no models, no footage)."""

import copy

import pytest

from library.tools import temporal_label_agreement as tla


def _event(event_id, cls="waving", start=1.0, end=2.0, identity="person-1"):
    return {
        "event_id": event_id, "event_class": cls, "identity_id": identity,
        "start": start, "end": end,
        "contact_evidence": "hand moves side to side",
        "state_change_evidence": None,
        "completeness": "complete",
        "evidence_ref": f"frame@{start}s",
    }


def _pass(annotator, family, events, negatives=None):
    return {
        "source_id": "IMG_1816", "duration_seconds": 188.578333,
        "annotator_id": annotator, "model_family": family,
        "events": events, "hard_negatives": negatives or [],
    }


def test_validate_pass_accepts_good_pass():
    doc = _pass("agent-a-1", "family-a", [_event("e1")])
    assert tla.validate_pass(doc) == {"out_of_vocabulary_classes": []}


def test_validate_pass_reports_out_of_vocabulary_class():
    doc = _pass("agent-a-1", "family-a", [_event("e1", cls="dancing")])
    assert tla.validate_pass(doc) == {"out_of_vocabulary_classes": ["dancing"]}


def test_validate_pass_rejects_bad_rows():
    base = _pass("agent-a-1", "family-a", [_event("e1")])
    bad = copy.deepcopy(base)
    bad["events"][0]["end"] = 0.5
    with pytest.raises(tla.LabelAgreementError):
        tla.validate_pass(bad)
    bad = copy.deepcopy(base)
    bad["events"][0]["contact_evidence"] = "  "
    with pytest.raises(tla.LabelAgreementError):
        tla.validate_pass(bad)
    bad = copy.deepcopy(base)
    bad["events"].append(_event("e1"))
    with pytest.raises(tla.LabelAgreementError):
        tla.validate_pass(bad)


def test_pairwise_agreement_is_one_for_identical_passes():
    events = [_event("e1"), _event("e2", start=5.0, end=6.0)]
    first = _pass("agent-a-1", "family-a", copy.deepcopy(events))
    second = _pass("agent-b-1", "family-b", copy.deepcopy(events))
    report = tla.pairwise_agreement(first, second)
    assert report["agreed_events"] == 2
    assert report["agreement_f1"] == pytest.approx(1.0)
    assert report["label_origin"] == "agent"


def test_pairwise_agreement_refuses_same_annotator_or_source():
    doc = _pass("agent-a-1", "family-a", [_event("e1")])
    with pytest.raises(tla.LabelAgreementError):
        tla.pairwise_agreement(doc, copy.deepcopy(doc))
    other = copy.deepcopy(doc)
    other["source_id"] = "LCATL0012"
    with pytest.raises(tla.LabelAgreementError):
        tla.pairwise_agreement(doc, other)


def test_pairwise_agreement_reports_class_confusion():
    first = _pass("agent-a-1", "family-a", [_event("e1", cls="nodding")])
    second = _pass("agent-b-1", "family-b", [_event("e2", cls="head_shake")])
    report = tla.pairwise_agreement(first, second)
    assert report["agreed_events"] == 0
    assert len(report["class_confusion"]) == 1
    assert report["class_confusion"][0]["pass_b_class"] == "head_shake"


def test_adjudicate_merges_agreement_to_mean_boundaries():
    first = _pass("agent-a-1", "family-a", [_event("e1", start=1.0, end=2.0)])
    second = _pass("agent-b-1", "family-b", [_event("e2", start=1.2, end=2.2)])
    tie = _pass("agent-c-1", "family-c", [_event("e3", start=1.1, end=2.1)])
    result = tla.adjudicate(first, second, tie)
    assert result["agreed_events"] == 1
    row = result["adjudicated_events"][0]
    assert (row["start"], row["end"]) == (pytest.approx(1.1), pytest.approx(2.1))
    assert row["provenance"]["review_status"] == "adjudicated"
    assert row["label_origin"] == "agent"
    assert "agent-a-1" in row["provenance"]["annotator_id"]
    assert "agent-b-1" in row["provenance"]["annotator_id"]


def test_adjudicate_uses_tiebreaker_for_disputes():
    first = _pass("agent-a-1", "family-a", [
        _event("e1"), _event("e2", start=10.0, end=11.0)])
    second = _pass("agent-b-1", "family-b", [_event("e3")])
    tie = _pass("agent-c-1", "family-c", [
        _event("e4"), _event("e5", start=10.1, end=11.1)])
    result = tla.adjudicate(first, second, tie)
    assert result["agreed_events"] == 1
    assert result["tiebreak_kept"] == 1
    assert result["tiebreak_dropped"] == 0
    assert len(result["adjudicated_events"]) == 2


def test_adjudicate_drops_events_the_tiebreaker_does_not_match():
    first = _pass("agent-a-1", "family-a", [
        _event("e1"), _event("e2", start=50.0, end=51.0)])
    second = _pass("agent-b-1", "family-b", [_event("e3")])
    tie = _pass("agent-c-1", "family-c", [_event("e4")])
    result = tla.adjudicate(first, second, tie)
    assert result["tiebreak_dropped"] == 1
    assert len(result["adjudicated_events"]) == 1


def test_adjudicate_refuses_non_blind_panel():
    first = _pass("agent-a-1", "family-a", [_event("e1")])
    second = _pass("agent-b-1", "family-a", [_event("e2")])
    tie = _pass("agent-a-1", "family-b", [_event("e3")])
    with pytest.raises(tla.LabelAgreementError):
        tla.adjudicate(first, second, tie)
    tie2 = _pass("agent-c-1", "family-a", [_event("e3")])
    with pytest.raises(tla.LabelAgreementError):
        tla.adjudicate(first, second, tie2)
