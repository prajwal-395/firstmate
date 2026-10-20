"""Ren's typed events as the held-out benchmark's dataset, and the
custodian gate that says whether they can be scored.

The defect each test names:

* a proposal written under an event class the benchmark's vocabulary
  does not name, so a prediction is scored against a class that means
  something else;
* an event the benchmark's vocabulary cannot name DROPPED in silence,
  so a class of action never reaches the score;
* an unjudged proposal written as a NEGATIVE answer, so an event
  nobody checked is counted as a wrong one;
* a quality number produced from Ren's own predictions while the
  corpus's held-out labels sit unreleased, which is the split policy's
  exact prohibition.
"""

import json
from pathlib import Path

import pytest

from library.tools import temporal_event_eval, temporal_events
from library.tools.analysis import temporal_event_benchmark
from library.tools.temporal_events import Boundary, TemporalEvent, TemporalEventIndex

REPO = Path(__file__).resolve().parents[3]
CORPUS_MANIFEST = (REPO / "docs" / "benchmarks"
                    / "2026-10-15-editorial-eval-corpus.json")


def _event(event_id, *, action="opening", status="candidate",
           start=1.0, end=2.0, subjects=(), labels=(),
           producer="vision_model"):
    return TemporalEvent(
        event_id=event_id,
        event_type="action_state_change",
        action=action,
        action_text="she opens the box",
        asset_id="clip:clip_001",
        clip_id="clip_001",
        source_file="/tmp/clip_001.mp4",
        clock_id="clock:clip_001",
        start_seconds=start,
        end_seconds=end,
        onset=Boundary(start, "estimated", 0.05, "measured"),
        intermediate_states=(),
        completion=Boundary(end, "estimated", 0.05, "measured"),
        subjects=subjects,
        subject_labels=labels,
        status=status,
        certainty="inferred",
        producer=producer,
    )


def _index(events):
    return TemporalEventIndex(
        events=temporal_events.assign_occurrences(events),
        clip_durations={"clip_001": 20.0})


# ── The dataset is well formed ──────────────────────────────────────


def test_a_typed_action_becomes_a_proposal_in_the_benchmarks_vocabulary():
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1", subjects=("person_1",))]))
    proposal = dataset["sources"][0]["proposals"][0]
    assert proposal["event_class"] == "opening"
    assert proposal["identity_id"] == "person_1"
    assert proposal["start"] == 1.0
    assert proposal["end"] == 2.0
    assert proposal["proposal_method"] == "vision_model"
    assert proposal["proposal_id"]


def test_every_proposal_class_is_inside_the_benchmarks_vocabulary():
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1", action="hand_to_mouth"),
                _event("e2", action="drink", start=5.0, end=6.0)]))
    for source in dataset["sources"]:
        for proposal in source["proposals"]:
            assert proposal["event_class"] in temporal_event_benchmark.EVENT_CLASSES


def test_an_action_the_benchmark_cannot_name_is_reported_not_dropped():
    """A class of action that never reaches the score is a silent
    hole in the measurement."""
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1", action="position_change")]))
    assert dataset["sources"][0]["proposals"] == []
    assert len(dataset["unmapped_actions"]) == 1
    assert dataset["unmapped_actions"][0]["event_id"] == "e1"
    assert "no event class" in dataset["unmapped_actions"][0]["reason"]


def test_an_unjudged_proposal_is_not_run_and_never_a_negative():
    """Nobody checked this event.  Writing it as a wrong answer would
    count it against the score."""
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1", status="candidate")]))
    verdict = dataset["sources"][0]["proposals"][0]["verifier"]
    assert verdict["decision"] == "not_run"


def test_a_verified_event_is_a_yes():
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1", status="verified", producer="project_model")]))
    verdict = dataset["sources"][0]["proposals"][0]["verifier"]
    assert verdict["decision"] == "yes"
    assert verdict["model_id"] == "project_model"


def test_an_action_class_outside_both_vocabularies_is_refused():
    """A mapping table that names a class the benchmark does not know
    must fail at the table, not at scoring time."""
    with pytest.raises(KeyError):
        temporal_event_eval.benchmark_class("box_opening")


# ── The custodian gate ──────────────────────────────────────────────


def _manifest():
    with open(CORPUS_MANIFEST, encoding="utf-8") as handle:
        return json.load(handle)


def test_the_shipped_corpus_has_not_released_its_held_out_labels():
    """The corpus says so in its own manifest: the labels are not
    created, and no held-out quality number may be produced."""
    manifest = _manifest()
    assert manifest["held_out_access"]["status"] == "labels_not_created"


def test_the_readiness_report_names_the_custodian_gate():
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1")]), split="held_out")
    readiness = temporal_event_eval.dataset_readiness(dataset, _manifest())
    assert readiness["ready"] is False
    assert readiness["held_out_labels_released"] is False
    assert any("custodian" in reason for reason in readiness["reasons"])


def test_the_benchmark_refuses_to_score_against_the_unreleased_corpus():
    """The refusal is the benchmark's own, not a wrapper's: a quality
    number cannot be produced from predictions the custodian has not
    judged."""
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1")]), split="held_out")
    with pytest.raises(ValueError) as caught:
        temporal_event_benchmark.evaluate(dataset, 0.5,
                                         corpus_manifest=_manifest())
    assert "custodian" in str(caught.value)


def test_a_dataset_that_declares_the_calibration_split_is_not_scored():
    """Ren's own predictions are calibration material.  The split is
    the caller's declaration, and a calibration split is not scored
    as held-out quality."""
    dataset = temporal_event_eval.build_dataset(
        _index([_event("e1")]), split="calibration")
    readiness = temporal_event_eval.dataset_readiness(dataset, _manifest())
    assert readiness["ready"] is False
    assert any("split" in reason for reason in readiness["reasons"])
