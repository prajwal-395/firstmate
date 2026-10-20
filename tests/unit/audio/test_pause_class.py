"""The silence between two words: what it carries, and what it does not.

Every project here is hand-made under `tmp_path`: no real footage, no
model, no project (§8).

The defect these tests name is the one a measured gap cannot catch by
itself. `speech_take.pauses_between` measures a 40 ms hesitation and a
4 s silence the same way - a duration - and refuses to judge either,
which was right while nothing could judge them. The judge has to come
from measurements, and a verdict that says "safe" without saying what
was measured is a claim nobody can check.
"""
from __future__ import annotations

from library.tools import pause_class, speech_take


def _words(text, start, gaps=None):
    """The words of `text`, with a silence after the words `gaps` names."""
    gaps = gaps or {}
    out = []
    cursor = start
    for index, token in enumerate(text.split()):
        out.append({"word": token, "start": round(cursor, 3),
                    "end": round(cursor + 0.4, 3)})
        cursor = round(cursor + 0.4 + gaps.get(index, 0.0), 3)
    return out


def _utt(index, text, start, gaps=None, speaker=None):
    words = _words(text, start + 0.1, gaps)
    row = {"start": start, "end": words[-1]["end"] + 0.1, "text": text,
           "words": words, "utterance_index": index}
    if speaker is not None:
        row["speaker"] = speaker
    return row


def _classify(utterances, **evidence):
    kept, dropped = speech_take.utterance_spans(
        {"utterances": utterances})
    assert dropped == []
    pauses = speech_take.pauses_between(kept)
    return pause_class.classify_pauses(pauses, utterances=kept, **evidence)


def test_a_gap_with_a_disfluency_right_after_it_is_not_one_to_close():
    rows = _classify([_utt(0, "so we um shipped it", 10.0, gaps={1: 0.4})])

    assert len(rows) == 1
    row = rows[0]
    assert pause_class.MARKER_DISFLUENCY_LEAD in row["markers"]
    assert row["verdict"] == pause_class.LINGUISTICALLY_IMPORTANT
    assert row["evidence"][pause_class.MARKER_DISFLUENCY_LEAD]["token"] == "um"


def test_a_gap_between_two_sentences_is_not_one_to_close():
    rows = _classify([_utt(0, "we shipped it.", 10.0),
                      _utt(1, "and it worked", 12.0)])

    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "utterance_gap"
    assert pause_class.MARKER_CLAUSE_FINAL in row["markers"]
    assert row["verdict"] == pause_class.LINGUISTICALLY_IMPORTANT


def test_a_gap_where_the_voice_changes_is_not_one_to_close():
    rows = _classify([_utt(0, "we shipped it", 10.0, speaker="a"),
                      _utt(1, "and it worked", 12.0, speaker="b")],
                     speakers={0: "a", 1: "b"})

    assert len(rows) == 1
    row = rows[0]
    assert pause_class.MARKER_TURN_CHANGE in row["markers"]
    assert row["verdict"] == pause_class.LINGUISTICALLY_IMPORTANT
    assert row["evidence"][pause_class.MARKER_TURN_CHANGE] == \
        {"speaker_before": "a", "speaker_after": "b"}


def test_a_gap_a_measured_event_occupies_is_not_one_to_close():
    rows = _classify([_utt(0, "we shipped it", 10.0),
                      _utt(1, "and it worked", 12.0)],
                     sound_events=[{"start": 11.8, "end": 12.2,
                                    "label": "Applause"}])

    assert len(rows) == 1
    row = rows[0]
    assert pause_class.MARKER_EVENT_OCCUPIED in row["markers"]
    assert row["verdict"] == pause_class.LINGUISTICALLY_IMPORTANT


def test_a_gap_nothing_was_measured_in_is_one_to_close():
    rows = _classify([_utt(0, "we shipped it and it worked", 10.0,
                           gaps={1: 0.4})],
                     sound_events=[])

    assert len(rows) == 1
    row = rows[0]
    assert row["markers"] == []
    assert row["verdict"] == pause_class.EDIT_SAFE
    assert row["coverage"]["sound_events"] == pause_class.COVERAGE_MEASURED


def test_a_gap_nobody_looked_in_is_undecided_and_not_one_to_close():
    rows = _classify([_utt(0, "we shipped it and it worked", 10.0,
                           gaps={1: 0.4})])

    assert len(rows) == 1
    row = rows[0]
    assert row["markers"] == []
    assert row["verdict"] == pause_class.UNDECIDED
    assert row["coverage"]["sound_events"] == \
        pause_class.COVERAGE_UNMEASURED


def test_a_gap_between_two_utterances_needs_to_know_whose_voice_it_is():
    rows = _classify([_utt(0, "we shipped it", 10.0),
                      _utt(1, "and it worked", 12.0)],
                     sound_events=[])

    assert len(rows) == 1
    row = rows[0]
    assert row["markers"] == []
    assert row["verdict"] == pause_class.UNDECIDED
    assert row["coverage"]["speaker_attribution"] == \
        pause_class.COVERAGE_UNMEASURED


def test_a_gap_between_two_utterances_of_one_voice_is_one_to_close():
    rows = _classify([_utt(0, "we shipped it", 10.0),
                      _utt(1, "and it worked", 12.0)],
                     sound_events=[], speakers={0: "a", 1: "a"})

    assert len(rows) == 1
    row = rows[0]
    assert row["markers"] == []
    assert row["verdict"] == pause_class.EDIT_SAFE


def test_a_verdict_of_safe_is_only_offered_for_the_gaps_that_carry_nothing():
    rows = _classify([_utt(0, "so we um shipped it and it worked", 10.0,
                           gaps={1: 0.4})],
                     sound_events=[])

    closable = pause_class.edit_safe_pauses(rows)
    assert closable == []
    assert all(row["verdict"] != pause_class.EDIT_SAFE for row in rows)


def test_the_reading_names_the_measurements_that_did_not_run():
    rows = _classify([_utt(0, "we shipped it and it worked", 10.0,
                           gaps={1: 0.4})])

    row = rows[0]
    assert "sound_events" in row["basis"]
    assert row["coverage"]["sound_events"] == \
        pause_class.COVERAGE_UNMEASURED


def test_a_voice_track_gives_each_utterance_its_speaker():
    kept, _ = speech_take.utterance_spans({"utterances": [
        _utt(0, "we shipped it", 10.0), _utt(1, "and it worked", 12.0)]})
    tracks = [{"track_id": "voice_001", "spans": [[10.0, 11.0]]},
              {"track_id": "voice_002", "spans": [[12.0, 13.0]]}]

    speakers = pause_class.speakers_from_voice_tracks(kept, tracks)

    assert speakers == {0: "voice_001", 1: "voice_002"}


def test_voice_tracks_that_touch_nothing_measure_no_speaker():
    kept, _ = speech_take.utterance_spans({"utterances": [
        _utt(0, "we shipped it", 10.0)]})
    tracks = [{"track_id": "voice_001", "spans": [[90.0, 91.0]]}]

    assert pause_class.speakers_from_voice_tracks(kept, tracks) is None
    assert pause_class.speakers_from_voice_tracks(kept, []) is None
    assert pause_class.speakers_from_voice_tracks(kept, None) is None


def test_the_markers_are_one_enumeration():
    assert pause_class.PAUSE_MARKERS == (
        "disfluency_lead", "clause_final", "turn_change", "event_occupied")
    assert pause_class.EDIT_SAFETY == (
        "edit_safe", "linguistically_important", "undecided")
