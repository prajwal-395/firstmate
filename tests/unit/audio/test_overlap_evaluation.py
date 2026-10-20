"""Speaker separation DURING overlapping speech, scored.

Every project here is hand-made under `tmp_path`: no real footage, no
model, no project (§8).

The defect these tests name is that the two halves of the overlap work
never met. `overlapped_speech` MEASURES the seconds two voices sound
at once, as crosstalk between camera tracks. `diarization_eval` SCORES
speaker attribution, from turn rows. Nothing turned the first into the
second, so the question the punch list asks - did the separation work
while two people talked over each other - had no way to be asked.

And a whole-file DER would have buried the answer: most of its
denominator is the seconds where one person spoke, which every
diarizer gets right.
"""

from __future__ import annotations

from library.tools import diarization_eval, overlapped_speech, speech_measurements


def _reference():
    return [
        {"start": 0.0, "end": 5.0, "speakers": ["A"]},
        {"start": 5.0, "end": 8.0, "speakers": ["A", "B"]},
        {"start": 8.0, "end": 10.0, "speakers": ["B"]},
    ]


def _estimate_missing_the_second_voice():
    return [
        {"start": 0.0, "end": 5.0, "speakers": ["A"]},
        {"start": 5.0, "end": 8.0, "speakers": ["A"]},
        {"start": 8.0, "end": 10.0, "speakers": ["B"]},
    ]


def test_the_measured_crosstalk_becomes_rows_the_overlap_scorer_reads():
    spans = [overlapped_speech.OverlapSpan(1.0, 2.0, ("cam1", "cam2")),
             overlapped_speech.OverlapSpan(4.0, 5.0, ("cam2", "cam3"))]

    rows = overlapped_speech.as_turn_rows(spans)

    assert rows == [
        {"start": 1.0, "end": 2.0, "speakers": ["cam1", "cam2"]},
        {"start": 4.0, "end": 5.0, "speakers": ["cam2", "cam3"]},
    ]
    scored = diarization_eval.overlap_metrics(rows, rows)
    assert scored["status"] == "measured"


def test_a_track_with_no_person_is_left_out_rather_than_named():
    spans = [overlapped_speech.OverlapSpan(1.0, 2.0, ("cam1", "cam2"))]

    rows = overlapped_speech.as_turn_rows(
        spans, speakers_by_track={"cam1": "Ann"})

    assert rows == [{"start": 1.0, "end": 2.0, "speakers": ["Ann"]}], \
        "a voice with no name is not a voice with an invented one"


def test_a_transcript_that_never_measured_crosstalk_scores_no_rows():
    assert overlapped_speech.as_turn_rows_of({}) == []
    document = {"overlapped_speech": {
        "measured": True,
        "spans": [{"start": 1.0, "end": 2.0, "voices": ["cam1", "cam2"]}]}}
    assert overlapped_speech.as_turn_rows_of(document) == [
        {"start": 1.0, "end": 2.0, "speakers": ["cam1", "cam2"]}]


def test_the_overlap_scorer_scores_the_overlap_and_leaves_the_easy_seconds_out():
    reference = _reference()
    estimate = _estimate_missing_the_second_voice()

    whole = diarization_eval.diarization_metrics(reference, estimate)
    overlap = diarization_eval.overlap_metrics(reference, estimate)

    assert overlap["der"] == 0.5
    assert overlap["overlap_regions"] == 1
    assert overlap["overlap_seconds"] == 3.0
    assert overlap["scored_speaker_seconds"] < \
        whole["scored_speaker_seconds"], \
        "the whole-file DER spends its denominator on the seconds every " \
        "diarizer gets right"


def test_a_reference_with_no_overlap_scores_nothing_and_says_so():
    reference = [{"start": 0.0, "end": 5.0, "speakers": ["A"]}]

    overlap = diarization_eval.overlap_metrics(reference, reference)

    assert overlap["status"] == "no_scored_speech"
    assert overlap["der"] is None
    assert overlap["overlap_regions"] == 0


def test_the_single_speaker_scorer_names_where_the_overlap_aware_one_lives():
    reference = [{"start": 0.0, "end": 5.0, "speaker": "A"},
                 {"start": 2.0, "end": 4.0, "speaker": "B"}]

    result = speech_measurements.diarization_der(reference, reference)

    assert result["status"] == "unsupported_overlap"
    assert "diarization_eval" in result["overlap_aware_scorer"]
