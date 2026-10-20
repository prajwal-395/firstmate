"""The placed word boundary, checked against the recording it claims.

Every project here is hand-made under `tmp_path`: no real footage, no
model, no project (§8). The recording is synthesised as 16-bit mono
PCM, so what the module hears is exactly what the test put there.

The defect these tests name is the one a forced aligner cannot see: a
boundary the ALIGNER placed because the text said a word was there,
sitting where the recording carries nothing.
"""
from __future__ import annotations

import struct
import wave
from pathlib import Path

import pytest

from library.tools import alignment_quality

RATE = 16000


def _write_wav(path: Path, segments: list[tuple[float, float, float]],
               tail: float = 0.5) -> Path:
    """A recording whose amplitude is `level` over each `(start, end)`.

    Anything not covered by a segment is digital silence. `tail` is how
    much silence follows the last segment.
    """
    total = int(RATE * (max(end for _s, end, _l in segments) + tail))
    samples = [0] * total
    for start, end, level in segments:
        amplitude = 10 ** (level / 20.0)
        for index in range(int(start * RATE), min(int(end * RATE), total)):
            samples[index] = int(max(-1.0, min(1.0, amplitude)) * 32767)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def _aligned(*words: tuple[str, float, float]) -> dict:
    return {"segments": [{"words": [
        {"word": word, "start": start, "end": end}
        for word, start, end in words]}]}


def test_a_word_the_recording_carries_nothing_for_is_reported_and_moved():
    recording = _write_wav(Path("speech.wav"), [
        (0.0, 0.5, -12.0),
        (1.5, 2.0, -12.0),
    ])
    aligned = _aligned(("hello", 0.10, 0.40), ("world", 1.00, 1.30))

    record = alignment_quality.verify_alignment(aligned, str(recording))

    assert record["status"] == "measured"
    assert record["words"]["silent"] == 1
    contradicted = record["contradicted"][0]
    assert contradicted["word"] == "world"
    assert contradicted["start"] == 1.0
    assert contradicted["seconds"] == 0.3
    assert contradicted["reason"] == \
        "the_recording_carries_no_signal_at_the_placed_span"
    assert len(aligned["segments"][0]["words"]) == 2, \
        "a contradicted word is kept: the recording cannot say it was not said"


def test_a_contradicted_word_keeps_its_length_when_it_moves():
    recording = _write_wav(Path("speech.wav"), [
        (0.0, 0.5, -12.0),
        (1.5, 2.0, -12.0),
    ])
    aligned = _aligned(("hello", 0.10, 0.40), ("world", 1.00, 1.30))

    record = alignment_quality.verify_alignment(aligned, str(recording))

    assert record["words"]["repaired"] == 1
    repair = record["repairs"][0]
    assert repair["word"] == "world"
    assert repair["from_start"] == 1.0
    # The signal's edge is located to the analysis frame grid, so the
    # repaired boundary sits within one frame of the signal's start.
    assert repair["to_start"] == pytest.approx(1.5, abs=0.02)
    assert (repair["to_end"] - repair["to_start"]) == \
        pytest.approx(0.30, abs=0.02)
    moved = aligned["segments"][0]["words"][1]
    assert moved["start"] == pytest.approx(1.5, abs=0.02)


def test_a_contradicted_word_with_nothing_supported_nearby_is_left_alone():
    recording = _write_wav(Path("speech.wav"), [(0.0, 0.5, -12.0)])
    aligned = _aligned(("hello", 0.10, 0.40), ("world", 3.00, 3.30))

    record = alignment_quality.verify_alignment(aligned, str(recording))

    assert record["words"]["silent"] == 1
    assert record["words"]["repaired"] == 0
    assert record["not_applied"][0]["reason"] == \
        "no_supported_span_within_the_search_limit"
    assert aligned["segments"][0]["words"][1]["start"] == 3.0, \
        "a boundary moved onto something hours away is a guess"


def test_a_quiet_span_is_reported_as_quiet_and_nothing_is_decided():
    recording = _write_wav(Path("speech.wav"), [
        (0.0, 0.5, -12.0),
        (1.5, 2.0, -60.0),
    ])
    aligned = _aligned(("hello", 0.10, 0.40), ("world", 1.60, 1.90))

    record = alignment_quality.verify_alignment(aligned, str(recording))

    assert record["words"]["quiet"] == 1
    assert record["words"]["silent"] == 0
    assert record["words"]["repaired"] == 0
    assert not record["contradicted"], \
        "a quiet word and a gap look the same to an energy measurement"
    assert aligned["segments"][0]["words"][1]["start"] == 1.60


def test_a_check_that_repairs_nothing_still_reports_what_it_found():
    recording = _write_wav(Path("speech.wav"), [
        (0.0, 0.5, -12.0),
        (1.5, 2.0, -12.0),
    ])
    aligned = _aligned(("world", 1.00, 1.30))

    record = alignment_quality.verify_alignment(aligned, str(recording),
                                                 repair=False)

    assert record["words"]["repaired"] == 0
    assert len(record["not_applied"]) == 1
    assert not record["repairs"]
    assert aligned["segments"][0]["words"][0]["start"] == 1.0, \
        "a check that was asked not to repair changes nothing"


def test_a_recording_with_no_dynamic_range_is_unverified_and_repairs_nothing():
    recording = _write_wav(Path("room.wav"), [(0.0, 2.0, -40.0)], tail=0.0)
    aligned = _aligned(("hello", 0.10, 0.40))

    record = alignment_quality.verify_alignment(aligned, str(recording))

    assert record["status"] == "unverified"
    assert record["reason"] == "insufficient_dynamic_range"
    assert record["words"].get("silent") is None, \
        "an unverified reading publishes no word counts it did not measure"
    assert not record["repairs"]
    assert aligned["segments"][0]["words"][0]["end"] == 0.40


def test_an_unreadable_recording_is_unverified_and_says_why():
    aligned = _aligned(("hello", 0.10, 0.40))

    record = alignment_quality.verify_alignment(aligned, "no-such-file.wav")

    assert record["status"] == "unverified"
    assert record["reason"] == "audio_unreadable"
    assert record["detail"]


def test_no_recording_at_all_is_unverified_rather_than_clean():
    aligned = _aligned(("hello", 0.10, 0.40))

    record = alignment_quality.verify_alignment(aligned, None)

    assert record["status"] == "unverified"
    assert record["reason"] == "audio_unavailable"


def test_an_alignment_with_no_timed_words_is_unverified_and_says_why():
    record = alignment_quality.verify_alignment({"segments": []}, None)

    assert record["status"] == "unverified"
    assert record["reason"] == "no_aligned_words"


def test_the_reading_follows_the_recording_and_not_a_fixed_level():
    quiet = _write_wav(Path("quiet.wav"), [(0.0, 0.5, -40.0)])
    loud = _write_wav(Path("loud.wav"), [(0.0, 0.5, -6.0)])

    quiet_record = alignment_quality.verify_alignment(
        _aligned(("hello", 0.10, 0.40)), str(quiet))
    loud_record = alignment_quality.verify_alignment(
        _aligned(("hello", 0.10, 0.40)), str(loud))

    assert quiet_record["levels"]["speech_dbfs"] < \
        loud_record["levels"]["speech_dbfs"]
    assert quiet_record["words"]["corroborated"] == 1
    assert loud_record["words"]["corroborated"] == 1, \
        "the reading follows the recording's own signal, not a fixed level"


def test_the_hybrid_record_carries_the_quality_and_its_absence_means_not_checked():
    from library.tools import heard_speech, hybrid_transcription

    spoken = heard_speech.HeardSpeech(
        words=[], sentences=[], text="", engine={"name": "test"})
    language = heard_speech.HeardLanguage(language="en", confidence=0.9)
    quality = {"status": "measured", "method": alignment_quality.METHOD,
               "words": {"total": 1}}

    with_quality = hybrid_transcription.hybrid_record(
        {"segments": []}, spoken, language, [], alignment_quality=quality)
    without = hybrid_transcription.hybrid_record(
        {"segments": []}, spoken, language, [])

    assert with_quality["alignment_quality"] == quality
    assert "alignment_quality" not in without, \
        "a caller that never ran the check must not publish one"
