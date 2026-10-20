"""Temporal questions over typed events: ordinals, order, repetition, cause.

The defect each test names:

* an ordinal reference resolved to the NEAREST plausible event, so
  "the second time" answers with the first;
* a reference that cannot be resolved answered anyway, so a question
  with no answer reads as answered;
* a part of the reference that could not be read dropped silently, so
  the answer looks sharper than it is;
* a causal relationship presented as measured, so an inference reaches
  an edit as a fact;
* a duration reported without the precision of its own edges, so a
  guessed length reads as a measured one.
"""

import pytest

from library.tools import temporal_events, temporal_query
from library.tools.temporal_events import Boundary, TemporalEvent


def _event(event_id, start, end, *, action="opening", action_text="",
           clip_id="clip_001", event_type="action_state_change",
           subjects=(), objects=()):
    return TemporalEvent(
        event_id=event_id,
        event_type=event_type,
        action=action,
        action_text=action_text,
        asset_id=f"clip:{clip_id}",
        clip_id=clip_id,
        source_file=f"/tmp/{clip_id}.mp4",
        clock_id=f"clock:{clip_id}",
        start_seconds=start,
        end_seconds=end,
        onset=Boundary(start, "estimated", 0.05, "measured"),
        intermediate_states=(),
        completion=Boundary(end, "estimated", 0.05, "measured"),
        subjects=subjects,
        subject_labels=(),
        objects=objects,
        status="candidate",
        certainty="inferred",
    )


def _index(events):
    return temporal_events.TemporalEventIndex(
        events=temporal_events.assign_occurrences(events))


def _two_openings():
    return _index([
        _event("a", 1.0, 2.0, action_text="she opens the box"),
        _event("b", 5.0, 6.0, action_text="she opens the box again"),
    ])


# ── An ordinal reference resolves to ONE event ─────────────────────


def test_the_second_time_resolves_to_the_second_opening():
    report = temporal_query.resolve_ordinal_reference(
        _two_openings(), "the second time she opens the box")
    assert report["status"] == "resolved"
    assert report["event"]["event_id"] == "b"
    assert report["event"]["occurrence"] == 2


def test_the_first_time_resolves_to_the_first_opening():
    report = temporal_query.resolve_ordinal_reference(
        _two_openings(), "the first time she opens the box")
    assert report["status"] == "resolved"
    assert report["event"]["event_id"] == "a"


def test_the_last_time_resolves_to_the_last_opening():
    report = temporal_query.resolve_ordinal_reference(
        _two_openings(), "the last time she opens the box")
    assert report["status"] == "resolved"
    assert report["event"]["event_id"] == "b"


def test_a_reference_with_no_such_repetition_is_unresolved_and_says_why():
    """Only one opening happened; the second one does not exist.  The
    answer must say so rather than offer the first."""
    index = _index([_event("a", 1.0, 2.0,
                            action_text="she opens the box")])
    report = temporal_query.resolve_ordinal_reference(
        index, "the second time she opens the box")
    assert report["status"] == "unresolved"
    assert report["event"] is None
    assert any("no group holds" in reason for reason in report["reasons"])


def test_a_reference_that_matches_nothing_is_unresolved():
    index = _index([_event("a", 1.0, 2.0,
                            action_text="she opens the box")])
    report = temporal_query.resolve_ordinal_reference(
        index, "the second time she opens the crate")
    assert report["status"] == "unresolved"
    assert report["event"] is None
    assert any("no event matches" in reason for reason in report["reasons"])


def test_a_pronoun_the_resolver_cannot_resolve_is_reported():
    """"she" names no person this resolver can turn into an identity.
    Dropping that silently would make the answer look sharper than it
    is."""
    reference = temporal_query.parse_ordinal_reference(
        "the second time she opens the box")
    assert reference.subjects == ("she",)
    assert any("pronoun" in note for note in reference.unresolved)


def test_a_reference_naming_no_action_reports_that():
    reference = temporal_query.parse_ordinal_reference("the second time")
    assert reference.action is None
    assert any("no action class" in note for note in reference.unresolved)


def test_a_reference_matching_two_groups_is_ambiguous():
    """The same action happens in two clips; the question does not say
    which, so the answer must not pick one."""
    index = _index([
        _event("a", 1.0, 2.0, action_text="she opens the box"),
        _event("b", 5.0, 6.0, action_text="she opens the box"),
        _event("c", 1.0, 2.0, action_text="she opens the box",
               clip_id="clip_002"),
        _event("d", 5.0, 6.0, action_text="she opens the box",
               clip_id="clip_002"),
    ])
    report = temporal_query.resolve_ordinal_reference(
        index, "the second time she opens the box")
    assert report["status"] == "ambiguous"
    assert report["event"] is None
    assert len(report["groups_considered"]) == 2


# ── Order, duration, repetition ───────────────────────────────────


def test_the_order_is_total_and_stable():
    events = [_event("b", 5.0, 6.0), _event("a", 1.0, 2.0),
              _event("c", 5.0, 6.0)]
    order = temporal_query.order_events(events)
    assert [row["order"] for row in order] == [1, 2, 3]
    assert [row["event_id"] for row in order] == ["a", "b", "c"]


def test_a_duration_carries_the_precision_of_its_own_edges():
    event = _event("a", 1.0, 2.0)
    rows = temporal_query.durations([event])
    assert rows[0]["duration_seconds"] == pytest.approx(1.0)
    assert rows[0]["duration_uncertainty_seconds"] == pytest.approx(0.05)


def test_a_duration_whose_edges_were_never_measured_says_so():
    event = TemporalEvent(
        event_id="a", event_type="action_state_change", action="opening",
        action_text="", asset_id="clip:clip_001", clip_id="clip_001",
        source_file=None, clock_id="clock:clip_001", start_seconds=1.0,
        end_seconds=2.0,
        onset=Boundary(1.0, "estimated", None, "precision unknown"),
        intermediate_states=(),
        completion=Boundary(2.0, "estimated", None, "precision unknown"))
    rows = temporal_query.durations([event])
    assert rows[0]["duration_uncertainty_seconds"] is None


def test_a_repetition_is_counted_and_a_single_event_is_not_a_repetition():
    events = [_event("a", 1.0, 2.0), _event("b", 5.0, 6.0),
              _event("c", 9.0, 10.0, action="closing",
                     event_type="scene_transition")]
    groups = temporal_query.repetition_groups(events)
    by_action = {group["action"]: group for group in groups}
    assert by_action["opening"]["count"] == 2
    assert by_action["opening"]["is_repetition"] is True
    assert by_action["closing"]["count"] == 1
    assert by_action["closing"]["is_repetition"] is False


# ── Causality is inferred, never measured ──────────────────────────


def test_precedence_between_events_is_measured():
    events = [_event("a", 1.0, 2.0), _event("b", 5.0, 6.0)]
    relations = temporal_query.temporal_relations(events)
    assert relations == [{
        "relation": "precedes",
        "from_event_id": "a",
        "to_event_id": "b",
        "certainty": "measured",
        "basis": "the two events' intervals on their clock",
    }]


def test_a_causal_candidate_is_marked_inferred_and_names_its_measurement():
    """Two events that share a person and follow one another are a
    REASON to look at them together.  They are not a cause, and the
    row must never let them be read as one."""
    events = [_event("a", 1.0, 2.0, subjects=("person_1",)),
              _event("b", 5.0, 6.0, subjects=("person_1",))]
    candidates = temporal_query.causal_candidates(events)
    assert len(candidates) == 1
    row = candidates[0]
    assert row["certainty"] == "inferred"
    assert row["relation"] == "may_cause"
    assert row["shared"] == ["person_1"]
    assert "no cause was measured" in row["basis"]


def test_events_that_share_nothing_are_not_causal_candidates():
    events = [_event("a", 1.0, 2.0, subjects=("person_1",)),
              _event("b", 5.0, 6.0, subjects=("person_2",))]
    assert temporal_query.causal_candidates(events) == []


def test_overlapping_events_overlap_rather_than_precede():
    events = [_event("a", 1.0, 4.0), _event("b", 2.0, 3.0)]
    relations = temporal_query.temporal_relations(events)
    assert relations[0]["relation"] == "overlaps"
    assert relations[0]["certainty"] == "measured"


# ── Free text reaches the same events ──────────────────────────────


def test_a_plain_description_finds_the_same_events():
    index = _two_openings()
    found = temporal_query.search_events(index, "she opens the box")
    assert {row["event_id"] for row in found["events"]} == {"a", "b"}
