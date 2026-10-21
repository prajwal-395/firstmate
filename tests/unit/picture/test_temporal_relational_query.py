"""Questions that ask about a RELATION between events (VI-006).

The defect each test names:

* a question about what happened BEFORE, AFTER or WHILE an event
  answered with the events that merely LOOK like the ones asked for,
  so a temporal question was answered by word similarity;
* an anchor that matches several events answered with the nearest one,
  so "the laptop opens" meant whichever opening the reader liked;
* the SECOND time answered with the first, because the ordinal was
  read off the wording rather than off the events;
* an event whose MEASURED state change says the lid opened matched by
  its prose alone, so a question about an action depended on how a
  lane worded the event;
* a lid that went from open to CLOSED read as an opening, because the
  value it left was read as well as the value it reached;
* a relation reported without the measurement it rests on, so an
  inferred answer read as a measured one.
"""

import json
from pathlib import Path

from library.tools import temporal_events, temporal_query
from library.tools.temporal_events import Boundary, StateChange, TemporalEvent

FIXTURE = (Path(__file__).resolve().parents[3] / "tests" / "fixtures"
           / "vi006_event_model.json")


def _event(event_id, start, end, *, action="opening", action_text="",
           clip_id="clip_001", event_type="action_state_change",
           subjects=(), objects=(), object_labels=(), state_changes=()):
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
        object_labels=object_labels,
        state_changes=state_changes,
        status="candidate",
        certainty="inferred",
    )


def _index(events):
    return temporal_events.TemporalEventIndex(
        events=temporal_events.assign_occurrences(events))


def _laptop_openings():
    """Two openings of the laptop, with a speech either side."""
    return _index([
        _event("reach", 1.0, 2.0, action="reach",
               action_text="she reaches for the laptop"),
        _event("open_1", 4.0, 4.6, action_text="she lifts the laptop lid"),
        _event("speech_1", 5.0, 7.0, action=None,
               action_text="and that is the whole plan",
               event_type="speech_start_end"),
        _event("open_2", 20.0, 20.5, action_text="she lifts the laptop lid"),
        _event("speech_2", 20.2, 21.4, action=None,
               action_text="and the screen comes up",
               event_type="speech_start_end"),
        _event("speech_3", 22.0, 23.4, action=None,
               action_text="right, back to the slide",
               event_type="speech_start_end"),
    ])


# ── A relational question names the events in the relation ─────────


def test_what_happened_before_the_second_opening_is_the_events_before_it():
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(),
        "what happened before the second time she opens the laptop")
    assert report["status"] == "resolved"
    assert report["relation"] == "before"
    assert {row["event_id"] for row in report["events"]} == {
        "reach", "open_1", "speech_1"}


def test_what_happened_after_the_second_opening_excludes_what_overlaps_it():
    """The speech that overlaps the opening is not AFTER it.  An
    answer that included it would be answering a different question."""
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(),
        "what happened after the second time she opens the laptop")
    assert report["status"] == "resolved"
    assert {row["event_id"] for row in report["events"]} == {"speech_3"}


def test_what_was_while_the_second_opening_is_what_shares_its_time():
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(),
        "what was happening while the second time she opens the laptop")
    assert report["status"] == "resolved"
    assert {row["event_id"] for row in report["events"]} == {"speech_2"}


def test_the_anchor_of_a_relational_question_is_the_second_opening():
    """The relation does not decide WHICH event is the anchor: the
    ordinal does.  The first opening is not the anchor here."""
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(),
        "what happened before the second time she opens the laptop")
    assert report["anchor_event"]["event_id"] == "open_2"
    assert report["anchor_event"]["occurrence"] == 2


def test_a_relation_is_reported_as_measured_and_names_its_measurement():
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(),
        "what happened before the second time she opens the laptop")
    assert report["relations"]
    for row in report["relations"]:
        assert row["certainty"] == "measured"
        assert "interval" in row["basis"]


def test_an_event_on_another_clock_is_never_related():
    """Two events can share a moment in time and still not be related:
    they happened on different sources."""
    events = [
        _event("open", 4.0, 5.0, action_text="she opens the laptop"),
        _event("elsewhere", 1.0, 2.0, clip_id="clip_002", action="closing",
               action_text="she closes the laptop"),
    ]
    report = temporal_query.resolve_relational_reference(
        _index(events), "what happened before she opens the laptop")
    assert report["status"] == "resolved"
    assert report["events"] == []


# ── An anchor that is ambiguous is REFUSED ─────────────────────────


def test_an_anchor_matching_two_events_is_refused_rather_than_guessed():
    """Two openings and no ordinal: the question does not say which
    one it means, so the answer must not pick the first."""
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(), "what happened before she opens the laptop")
    assert report["status"] == "ambiguous"
    assert report["events"] == []
    assert any("does not say which" in reason
               for reason in report["reasons"])


def test_a_question_naming_no_relation_is_not_a_relational_question():
    report = temporal_query.resolve_relational_reference(
        _laptop_openings(), "the second time she opens the laptop")
    assert report["status"] == "unresolved"
    assert report["relation"] is None
    assert any("no relation" in reason for reason in report["reasons"])


# ── The anchor is resolved on EVENT EVIDENCE ───────────────────────


def test_a_measured_state_change_answers_a_question_about_the_action():
    """The lid went from closed to open: that IS an opening, whatever
    the event's own words are.  An answer that needed the prose would
    depend on how a lane worded the event."""
    event = _event("lid", 4.0, 4.6, action=None,
                   action_text="the lid state transition",
                   objects=("object_1",), object_labels=("laptop",),
                   state_changes=(StateChange(entity_id="object_1",
                                             property="lid",
                                             before="closed", after="open",
                                             evidence_ids=("obs_1",)),))
    index = _index([event])
    found = temporal_query.search_events(index, "she opens the laptop")
    assert {row["event_id"] for row in found["events"]} == {"lid"}


def test_a_lid_that_reached_closed_is_not_an_opening():
    """The DIRECTION matters: the value it reached is what names the
    action, not the value it left."""
    event = _event("lid", 4.0, 4.6, action=None,
                   action_text="the lid state transition",
                   objects=("object_1",), object_labels=("laptop",),
                   state_changes=(StateChange(entity_id="object_1",
                                             property="lid",
                                             before="open", after="closed",
                                             evidence_ids=("obs_1",)),))
    index = _index([event])
    found = temporal_query.search_events(index, "she opens the laptop")
    assert found["events"] == []


def test_the_whole_answer_carries_the_relational_resolution():
    answer = temporal_query.answer(
        _laptop_openings(),
        "what happened while the second time she opens the laptop")
    assert answer["relational"]["status"] == "resolved"
    assert answer["relational"]["relation"] == "while"
    assert {row["event_id"] for row in answer["relational"]["events"]} == {
        "speech_2"}
    assert {row["event_id"] for row in answer["events"]} >= {"speech_2"}


# ── The labelled corpus resolves ──────────────────────────────────


def test_every_labelled_question_in_the_fixture_resolves():
    """The fixture is the labelled corpus the measurement scores.  A
    question in it that does not resolve is a question the surface
    cannot answer, and it must fail here rather than in the report."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    events = []
    entities = fixture.get("entities") or {}
    for row in fixture["events"]:
        subjects = tuple(row.get("subjects") or ())
        objects = tuple(row.get("objects") or ())
        events.append(TemporalEvent(
            event_id=row["event_id"], event_type=row["event_type"],
            action=row.get("action"),
            action_text=str(row.get("action_text") or ""),
            asset_id=f"clip:{fixture['clip_id']}",
            clip_id=fixture["clip_id"], source_file=None,
            clock_id=fixture["clock_id"],
            start_seconds=float(row["start_seconds"]),
            end_seconds=float(row["end_seconds"]),
            onset=Boundary(float(row["start_seconds"]), "estimated", 0.05,
                           "the fixture's own edge"),
            intermediate_states=(),
            completion=Boundary(float(row["end_seconds"]), "estimated", 0.05,
                                "the fixture's own edge"),
            subjects=subjects,
            subject_labels=tuple(str((entities.get(s) or {}).get("label")
                                     or "") for s in subjects),
            objects=objects,
            object_labels=tuple(str((entities.get(o) or {}).get("label")
                                     or "") for o in objects),
            participant_ids=tuple(row.get("participant_ids") or ()),
            observed_participant_ids=tuple(
                row.get("observed_participant_ids") or ()),
            inferred_participant_ids=tuple(
                row.get("inferred_participant_ids") or ()),
            evidence_ids=tuple(row.get("evidence_ids") or ()),
            status=str(row.get("status") or "candidate"),
            certainty=str(row.get("certainty") or "inferred"),
            state_changes=tuple(
                StateChange(entity_id=str(change.get("entity_id") or ""),
                            property=str(change.get("property") or ""),
                            before=change.get("from"),
                            after=change.get("to"),
                            evidence_ids=tuple(change.get("evidence_ids")
                                               or ()),
                            certainty=str(change.get("certainty")
                                          or "measured"))
                for change in (row.get("state_changes") or [])),
        ))
    index = temporal_events.TemporalEventIndex(
        events=temporal_events.assign_occurrences(events))
    for labelled in fixture["queries"]:
        relation = labelled.get("relation")
        expected_anchor = labelled["anchor_event_id"]
        expected_related = set(labelled.get("expected_event_ids") or ())
        answer = temporal_query.answer(index, labelled["query"])
        if relation is None:
            resolved = answer["resolution"]
            anchor = (resolved.get("event") or {}).get("event_id")
            related = {row["event_id"] for row in answer["events"]}
        else:
            resolved = answer["relational"]
            anchor = (resolved.get("anchor_event") or {}).get("event_id")
            related = {row["event_id"] for row in resolved["events"]}
        assert resolved["status"] == "resolved", labelled["query"]
        assert anchor == expected_anchor, labelled["query"]
        assert related == expected_related, labelled["query"]
