import math
from types import SimpleNamespace

import pytest

from library.tools.reel_build import suppress_mic_bleed_audio


FPS = 24000 / 1001


def _placement(speaker, track_type="audio"):
    duration = round(10 * FPS) / FPS
    return {
        "clip": SimpleNamespace(
            track_type=track_type,
            source_file=f"/{speaker}.MXF",
        ),
        "source_in": 10.0,
        "source_out": 10.0 + duration,
        "record": 0.0,
        "snapped_record": 0,
        "speaker": speaker,
        "master": (100.0, 100.0 + duration),
    }


def _decision(speaker="Craig", start=103.0, end=106.0, status="dropped"):
    return {
        "status": status,
        "dropped_speaker": speaker,
        "start": start,
        "end": end,
        "passage": "the clearer microphone owns this line",
    }


def _transcript(*speakers):
    return {"segments": [
        {"speaker": speaker, "timeline_start": start,
         "timeline_end": end, "text": text}
        for speaker, start, end, text in speakers
    ]}


def test_measured_bleed_splits_only_the_losing_mic_placement():
    craig = _placement("Craig")
    akshita = _placement("Akshita")

    transcript = _transcript(("Akshita", 103.0, 106.0,
                              "the clearer microphone owns this line"))
    transcript["mic_bleed_resolution"] = [_decision()]
    kept, suppressed = suppress_mic_bleed_audio(
        [craig, akshita], transcript, FPS)

    craig_parts = [part for part in kept if part["speaker"] == "Craig"]
    akshita_parts = [part for part in kept if part["speaker"] == "Akshita"]
    assert len(craig_parts) == 2
    assert craig_parts[0]["master"][0] == pytest.approx(100.0)
    assert craig_parts[0]["master"][1] <= 103.0
    assert craig_parts[0]["source_in"] == pytest.approx(10.0)
    assert craig_parts[0]["source_out"] == pytest.approx(
        craig_parts[0]["master"][1] - 90.0)
    assert craig_parts[1]["master"][0] >= 106.0
    assert craig_parts[1]["master"][1] == pytest.approx(
        craig["master"][1])
    assert craig_parts[1]["source_in"] == pytest.approx(
        craig_parts[1]["master"][0] - 90.0)
    assert craig_parts[1]["source_out"] == pytest.approx(
        craig["source_out"])
    assert craig_parts[1]["snapped_record"] == round(6 * FPS)
    assert akshita_parts == [akshita]
    assert suppressed == [{
        "speaker": "Craig",
        "speaking_speakers": ["Akshita"],
        "source_file": "/Craig.MXF",
        "passage": "the clearer microphone owns this line",
        "master_start": 100 + int(3 * FPS) / FPS,
        "master_end": 100 + math.ceil(6 * FPS) / FPS,
        "record_start_frame": int(3 * FPS),
        "record_end_frame": round(6 * FPS),
    }]


def test_overlapping_speakers_keep_both_microphones():
    original = _placement("Craig")

    kept, suppressed = suppress_mic_bleed_audio(
        [original],
        _transcript(
            ("Craig", 103.0, 106.0, "a distinct Craig sentence"),
            ("Akshita", 103.0, 106.0, "a distinct Akshita sentence")),
        FPS)

    assert kept == [original]
    assert suppressed == []


def test_mic_bleed_decision_does_not_change_picture_placement():
    original = _placement("Craig", track_type="video")

    kept, suppressed = suppress_mic_bleed_audio(
        [original],
        _transcript(("Akshita", 103.0, 106.0, "Akshita speaks")), FPS)

    assert kept == [original]
    assert suppressed == []
