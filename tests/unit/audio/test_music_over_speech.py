"""The music-over-speech override: a named span where the bed is allowed
over the voice.

The defect this module exists for: the speech-above-bed gate fails a
render whose bed sits under the voice by less than the declared
separation, and there was no way to tell it "here, deliberately, I want
the bed over the voice" - a comic beat, a swell.  Without the override
the captain's only choices were to fail the build or to silence the
check.

The override is an explicit instruction in a timeline note or edit
request naming a span - the same path every other override uses.  A
note that does not explicitly ask builds nothing; a record whose scope
cannot be resolved is refused, never guessed at.
"""
import pytest

from library.tools import music_over_speech
from library.tools.music_over_speech import (
    MusicOverSpeechError,
    overrides_from_timeline_notes,
    span_is_overridden,
    validate_overrides,
)


def _note(text, **fields):
    note = {"note_id": "note-1", "text": text, "typed": text}
    note.update(fields)
    return note


def _span_override(scope=None, **fields):
    record = {
        "format": "music_over_speech_override/1",
        "override_id": "note-1",
        "source": "timeline_note",
        "instruction": "let the music sit over the speech here",
        "scope": scope or {"kind": "span", "timeline": "(the master timeline)",
                          "start_seconds": 0.0, "end_seconds": 4.0},
    }
    record.update(fields)
    return record


class TestOverridesFromTimelineNotes:

    def test_an_explicit_request_naming_a_span_builds_a_record(self):
        notes = {"notes": [_note(
            "let the music sit over the speech here",
            at_region="12.5..18.0", on_timeline="(the master timeline)")]}
        records = overrides_from_timeline_notes(notes)
        assert len(records) == 1
        record = records[0]
        assert record["override_id"] == "note-1"
        assert record["source"] == "timeline_note"
        assert record["scope"]["kind"] == "span"
        assert record["scope"]["start_seconds"] == 12.5
        assert record["scope"]["end_seconds"] == 18.0

    def test_a_note_that_does_not_ask_builds_nothing(self):
        """The stabilization model: a measurement or a passing mention
        grants no permission.  Only an explicit request creates a record."""
        notes = {"notes": [
            _note("the music is too loud under the speech"),
            _note("I want the speech clear over the bed"),
            _note("great mix, ship it"),
        ]}
        assert overrides_from_timeline_notes(notes) == []

    def test_a_negated_request_builds_nothing(self):
        notes = {"notes": [_note("do not let the music over the speech")]}
        assert overrides_from_timeline_notes(notes) == []

    def test_a_whole_video_request_covers_every_window(self):
        notes = {"notes": [_note("music over the speech for the whole video")]}
        records = overrides_from_timeline_notes(notes)
        assert records[0]["scope"]["kind"] == "whole_video"

    def test_an_explicit_request_without_a_span_is_refused(self):
        """The captain named no span and no whole video, so there is
        nothing to exempt.  Guessing a scope would silently exempt the
        wrong stretch."""
        notes = {"notes": [_note("let the music sit over the speech")]}
        with pytest.raises(MusicOverSpeechError):
            overrides_from_timeline_notes(notes)

    def test_an_explicit_request_without_a_note_id_is_refused(self):
        notes = {"notes": [_note(
            "let the music sit over the speech here",
            at_region="1.0..2.0", on_timeline="(the master timeline)",
            note_id="")]}
        with pytest.raises(MusicOverSpeechError):
            overrides_from_timeline_notes(notes)

    def test_an_edit_request_is_sourced_as_such(self):
        notes = {"notes": [_note(
            "music over the speech here",
            at_region="1.0..2.0", on_timeline="(the master timeline)",
            typed_operations=[{"owner": "audio_mix",
                               "op": "music_over_speech"}])]}
        records = overrides_from_timeline_notes(notes)
        assert records[0]["source"] == "edit_request"


class TestValidateOverrides:

    def test_a_well_formed_record_passes(self):
        assert validate_overrides([_span_override()]) == [_span_override()]

    def test_a_record_with_no_supported_format_is_refused(self):
        with pytest.raises(MusicOverSpeechError):
            validate_overrides([_span_override(format="something/else")])

    def test_a_record_with_no_instruction_is_refused(self):
        with pytest.raises(MusicOverSpeechError):
            validate_overrides([_span_override(instruction="")])

    def test_a_record_with_no_explicit_request_is_refused(self):
        """A record that no longer carries the captain's explicit words
        is not an override - the instruction is the authorization."""
        with pytest.raises(MusicOverSpeechError):
            validate_overrides([_span_override(
                instruction="the music is over the speech")])

    def test_a_span_with_an_empty_range_is_refused(self):
        with pytest.raises(MusicOverSpeechError):
            validate_overrides([_span_override(scope={
                "kind": "span", "start_seconds": 4.0, "end_seconds": 4.0})])

    def test_a_record_with_no_scope_is_refused(self):
        with pytest.raises(MusicOverSpeechError):
            validate_overrides([_span_override(scope={"kind": "clip"})])

    def test_none_validates_to_empty(self):
        assert validate_overrides(None) == []


class TestSpanIsOverridden:

    def test_a_window_inside_the_span_is_overridden(self):
        overrides = [_span_override()]
        assert span_is_overridden(overrides, 1.0, 3.0) == "note-1"

    def test_a_window_outside_the_span_is_not_overridden(self):
        overrides = [_span_override()]
        assert span_is_overridden(overrides, 5.0, 7.0) == ""

    def test_a_whole_video_override_covers_any_window(self):
        overrides = [_span_override(scope={"kind": "whole_video"})]
        assert span_is_overridden(overrides, 100.0, 200.0) == "note-1"

    def test_a_window_overlapping_the_span_edge_is_overridden(self):
        """The captain named a stretch; a window that even partly sits
        inside it is exempt, so the gate cannot fail something they meant
        to exempt."""
        overrides = [_span_override()]
        assert span_is_overridden(overrides, 3.0, 6.0) == "note-1"

    def test_no_overrides_means_nothing_is_overridden(self):
        assert span_is_overridden([], 0.0, 4.0) == ""
        assert span_is_overridden(None, 0.0, 4.0) == ""
