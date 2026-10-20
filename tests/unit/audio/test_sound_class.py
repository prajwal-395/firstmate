"""The sound classes a clip measured, and the ones it did not.

Every project here is hand-made under `tmp_path`: no real footage, no
model, no project (§8).

The defect these tests name is the one the events alone cannot catch.
A clip whose sound events were measured and found no laughter is a
MEASURED ABSENCE of laughter. A clip whose sound events were never
measured has NO ANSWER about laughter. The reader that gives those two
the same answer tells an editor that there is no laughter in a clip
nobody listened to.
"""

from __future__ import annotations

import pytest

from library.tools import audio_interval_evidence, sound_class

METHOD = "panns-cnn14-decisionlevelmax"


def _summary(events=None, method=METHOD, duration=10.0):
    return {"sound_event_method": method,
            "sound_events": list(events or []),
            "duration": duration}


def _event(label, start, end, confidence=0.8):
    return {"label": label, "start": start, "end": end,
            "confidence": confidence}


def test_a_class_with_a_measured_event_reads_as_present():
    reading = sound_class.class_reading(
        _summary([_event("Applause", 1.0, 2.0)]))

    entry = reading["classes"]["applause"]
    assert entry["state"] == sound_class.STATE_PRESENT
    assert entry["labels"] == ["Applause"]
    assert entry["seconds"] == 1.0


def test_a_class_nothing_was_measured_in_reads_as_absent_not_empty():
    reading = sound_class.class_reading(_summary([_event("Applause", 1.0, 2.0)]))

    entry = reading["classes"]["laughter"]
    assert entry["state"] == sound_class.STATE_ABSENT
    assert entry["events"] == []
    assert entry["seconds"] == 0.0


def test_a_clip_nobody_measured_reads_as_uncertain_for_every_class():
    reading = sound_class.class_reading(
        _summary([], method="unmeasured: no checkpoint on this machine"))

    assert reading["measured"] is False
    assert reading["reason"] == "unmeasured: no checkpoint on this machine"
    for name in sound_class.CLASS_NAMES:
        entry = reading["classes"][name]
        assert entry["state"] == sound_class.STATE_UNCERTAIN
        assert entry["events"] == []


def test_a_label_the_roster_does_not_cover_is_still_reported():
    reading = sound_class.class_reading(
        _summary([_event("Silence", 0.0, 4.0)]))

    assert reading["unclassified"]["labels"] == ["Silence"]
    assert reading["unclassified"]["events"] == 1
    assert reading["classes"]["impact"]["state"] == sound_class.STATE_ABSENT


def test_the_roster_names_only_labels_the_producer_really_emits():
    panns = pytest.importorskip(
        "panns_inference", reason="the sound-event producer is not installed")
    emitted = set(panns.labels)
    for entry in sound_class.SOUND_CLASSES:
        for label in entry.labels:
            assert label in emitted, (
                f"{entry.name} names {label!r}, which the producer does "
                f"not emit")


def test_a_music_change_is_read_off_the_music_spans():
    changes = sound_class.music_changes(
        _summary([_event("Music", 2.0, 5.0, 0.9)]))

    assert [change["kind"] for change in changes] == [
        sound_class.CHANGE_STARTED, sound_class.CHANGE_STOPPED]
    assert changes[0]["at_seconds"] == 2.0
    assert changes[1]["at_seconds"] == 5.0


def test_music_that_runs_to_the_end_of_the_clip_has_no_measured_stop():
    changes = sound_class.music_changes(
        _summary([_event("Music", 0.0, 10.0, 0.9)], duration=10.0))

    assert changes == [], \
        "the recording does not say the music stopped at the end of it"


def test_music_that_starts_at_the_first_instant_has_no_measured_start():
    changes = sound_class.music_changes(
        _summary([_event("Music", 0.0, 4.0, 0.9)], duration=10.0))

    assert [change["kind"] for change in changes] == [
        sound_class.CHANGE_STOPPED]
    assert changes[0]["at_seconds"] == 4.0


def test_a_gap_between_two_music_spans_is_two_changes():
    changes = sound_class.music_changes(
        _summary([_event("Music", 2.0, 5.0, 0.9),
                  _event("Music", 7.0, 9.0, 0.9)], duration=10.0))

    assert [(change["kind"], change["at_seconds"]) for change in changes] == [
        (sound_class.CHANGE_STARTED, 2.0),
        (sound_class.CHANGE_STOPPED, 5.0),
        (sound_class.CHANGE_STARTED, 7.0),
        (sound_class.CHANGE_STOPPED, 9.0)]


def test_a_clip_nobody_measured_reports_no_music_change():
    changes = sound_class.music_changes(
        _summary([], method="unmeasured: no checkpoint on this machine"))

    assert changes == [], \
        "the absence of a measurement is not the measurement that the music never changed"


def test_a_measured_event_reaches_the_interval_evidence_with_its_uncertainty():
    rows, coverage = audio_interval_evidence.sound_interval_evidence(
        _summary([_event("Applause", 1.0, 2.0, 0.9)]))

    assert len(rows) == 1
    row = rows[0]
    assert row["domain"] == "sound"
    assert row["kind"] == "sound_event"
    assert row["label"] == "Applause"
    assert row["value"]["sound_class"] == "applause"
    assert row["uncertainty"]["kind"] == "resolution_bound"
    assert row["uncertainty"]["boundary_seconds"] > 0
    assert coverage["kind"] == "complete"


def test_an_unmeasured_clip_reaches_no_interval_evidence_and_says_so():
    rows, coverage = audio_interval_evidence.sound_interval_evidence(
        _summary([], method="unmeasured: no checkpoint on this machine"))

    assert rows == []
    assert coverage["kind"] == "unavailable"


def test_a_class_reading_over_a_span_is_a_measured_absence_too():
    reading = sound_class.class_reading(
        _summary([_event("Applause", 1.0, 2.0)]),
        source_start=5.0, source_end=8.0)

    assert reading["classes"]["applause"]["state"] == \
        sound_class.STATE_ABSENT
    assert reading["classes"]["applause"]["events"] == []


def test_a_class_reading_over_a_span_sees_the_events_inside_it():
    reading = sound_class.class_reading(
        _summary([_event("Applause", 5.5, 6.0)]),
        source_start=5.0, source_end=8.0)

    assert reading["classes"]["applause"]["state"] == \
        sound_class.STATE_PRESENT
    assert reading["classes"]["applause"]["labels"] == ["Applause"]


def test_the_roster_is_one_enumeration():
    assert sound_class.CLASS_NAMES == (
        "impact", "applause", "laughter", "music", "environmental")
    assert sound_class.CLASS_STATES == ("present", "absent", "uncertain")
