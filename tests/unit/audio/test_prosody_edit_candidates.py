from library.tools.prosody_edit_candidates import emphasis_edit_candidates


def test_emphasis_candidates_keep_transcript_spans_and_explain_acoustic_support():
    words = [
        {"word": "we", "source_start": 4.0, "source_end": 4.12},
        {"word": "launch", "source_start": 4.13, "source_end": 4.71},
    ]
    profile = {"prosody": {"word_prosody": [
        {"word": "we", "start": 4.0, "end": 4.12,
         "emphasis": 0.1, "terms": 3},
        {"word": "launch", "start": 4.13, "end": 4.71,
         "emphasis": 0.9, "terms": 2, "f0_rel_semitones": 2.4,
         "loud_rel_db": 1.2, "dur_ratio": None},
    ]}}

    result = emphasis_edit_candidates(words, profile)

    assert len(result) == 1
    assert result[0]["word"] == "launch"
    assert (result[0]["source_start"], result[0]["source_end"]) == (4.13, 4.71)
    assert result[0]["certainty"] == 0.667
    assert result[0]["evidence"]["pitch_semitones"] == 2.4
    assert "editorial review required" in result[0]["interpretation"]


def test_repeated_words_match_in_order_and_missing_timing_is_not_invented():
    words = [
        {"word": "right", "source_start": 1.0, "source_end": 1.2},
        {"word": "right"},
    ]
    profile = {"prosody": {"word_prosody": [
        {"word": "right", "start": 1.0, "emphasis": 0.7, "terms": 3},
        {"word": "right", "start": 2.0, "emphasis": 0.8, "terms": 3},
    ]}}

    result = emphasis_edit_candidates(words, profile)

    assert len(result) == 1
    assert result[0]["source_start"] == 1.0


def test_no_transcription_or_emotional_claim_when_measurement_is_absent():
    assert emphasis_edit_candidates(
        [{"word": "maybe", "source_start": 0, "source_end": 0.2}],
        {"prosody": {"word_prosody": []}},
    ) == []
