import math
from collections import Counter
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


def _stream_row(person, index, track_type):
    name = person if track_type == "video" else f"{person} CH1"
    clip = SimpleNamespace(track_type=track_type, track_index=index,
                           track_name=name, speaker=name,
                           source_file=f"/{person}.MXF")
    return {**_placement(name, track_type=track_type), "clip": clip}


def _mic_bleed_cases():
    craig = _placement("Craig")
    akshita = _placement("Akshita")
    measured = _transcript(("Akshita", 103.0, 106.0,
                            "the clearer microphone owns this line"))
    measured["mic_bleed_resolution"] = [_decision()]

    channel_akshita = _stream_row("Akshita", 1, "audio")
    channel_craig = _stream_row("Craig", 2, "audio")
    pictures = [_stream_row("Akshita", 1, "video")["clip"],
                _stream_row("Craig", 2, "video")["clip"]]

    return [
        pytest.param(
            [craig, akshita], measured, None,
            {"/Craig.MXF": 2, "/Akshita.MXF": 1},
            [("Craig", ["Akshita"], int(3 * FPS), round(6 * FPS))],
            id="split-only-the-losing-mic"),
        pytest.param(
            [craig], _transcript(
                ("Craig", 103.0, 106.0, "a distinct Craig sentence"),
                ("Akshita", 103.0, 106.0,
                 "a distinct Akshita sentence")), None,
            {"/Craig.MXF": 1}, [], id="keep-genuine-overlap"),
        pytest.param(
            [_placement("Craig", track_type="video")],
            _transcript(("Akshita", 103.0, 106.0, "Akshita speaks")),
            None, {"/Craig.MXF": 1}, [], id="never-mute-picture"),
        pytest.param(
            [channel_akshita, channel_craig],
            _transcript(("Akshita", 100.0, 109.0,
                         "Akshita holds the turn")), pictures,
            {"/Akshita.MXF": 1, "/Craig.MXF": 1},
            [("Craig", ["Akshita"], 0, math.ceil(9 * FPS))],
            id="resolve-channel-row-through-its-picture-angle"),
    ]


@pytest.mark.parametrize(
    ("placements", "transcript", "master_clips", "kept_counts",
     "expected_suppressions"),
    _mic_bleed_cases(),
)
def test_mic_bleed_only_suppresses_the_losing_audio_angle(
        placements, transcript, master_clips, kept_counts,
        expected_suppressions):
    """See `docs/evidence/mic_bleed_audio.md` for the measured incident."""
    kept, suppressed = suppress_mic_bleed_audio(
        placements, transcript, FPS, master_clips=master_clips)

    kept_by_source = Counter(item["clip"].source_file for item in kept)
    assert dict(kept_by_source) == kept_counts
    assert [(entry["speaker"], entry["speaking_speakers"],
             entry["record_start_frame"], entry["record_end_frame"])
            for entry in suppressed] == expected_suppressions

    if expected_suppressions and expected_suppressions[0][2] == int(3 * FPS):
        craig_parts = [part for part in kept if part["speaker"] == "Craig"]
        [craig] = [item for item in placements if item["speaker"] == "Craig"]
        akshita = next(item for item in placements
                       if item["speaker"] == "Akshita")
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
        assert akshita in kept
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
    elif master_clips:
        assert next(item for item in placements
                    if item["speaker"] == "Akshita CH1") in kept
