"""Room-tone continuity: the dropout the mix measures, and the fill it
plans.

The defect these tests pin: a source that carries audible room can hold
digital-silence spans inside the ranges the edit plays - the stretches
where the room drops out after a dialogue edit. Before this, nothing
detected them and the mix shipped with the holes. Now 5.02's measure
pass reports each dropout on the source's cleanup_context record, and
the post-bridge plans one fill per dropout from the source's measured
room - a source with dropouts but no measured floor plans nothing,
because filling from silence is the defect `room_tone` exists to
remove. Fixtures are stdlib `wave` WAVs under `tmp_path`; no model, no
Resolve, no ffmpeg.
"""
import json
import math
import os
import struct
import sys
import wave

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_5_02_audio_mix.mix import (
    cleanup_context,
    plan_room_tone_fills,
)
from library.steps.step_5_02_audio_mix.post_bridge import resolve_audio_mix
from library.steps.step_5_04_compile_manifest.step import (
    _source_to_timeline_frames,
    _v1_clip_playing,
)

SR = 8000


def _write_wav(path, samples, rate=SR):
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(packed)


def _speech_with_dropout(path, dropout=(2.5, 3.0), total=4.0):
    """Speech 0.5-1.5, room tone elsewhere, one digital-silence dropout
    inside the played range."""
    samples = []
    for i in range(int(total * SR)):
        t = i / SR
        if 0.5 <= t < 1.5:
            samples.append(0.6 * math.sin(2 * math.pi * 440.0 * t))
        elif dropout[0] <= t < dropout[1]:
            samples.append(0.0)
        else:
            samples.append(0.004 * math.sin(2 * math.pi * 120.0 * t))
    _write_wav(path, samples)


def _spine(clip_id="clip_1"):
    return {"structure": [{
        "position": "hook", "block_type": "hook",
        "clip_id": clip_id,
        "content": {
            "clip_id": clip_id,
            "word_timestamps": [
                {"word": "hello", "source_start": 0.5,
                 "source_end": 1.5},
            ],
        },
    }]}


def _assignments(source, clip_id="clip_1"):
    return [{
        "spine_block_position": "hook",
        "video_segments": [{
            "clip_id": clip_id,
            "source_file": source,
            "video_in": 0.0, "video_out": 4.0,
        }],
    }]


def _cleanup(source):
    return cleanup_context(_spine(), _assignments(source))


# ── the measure pass ─────────────────────────────────────────────────

def test_measure_reports_the_dropout_in_the_played_range(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_with_dropout(source)
    row = _cleanup(source)["sources"][0]
    assert len(row["dropouts"]) == 1
    assert row["dropouts"][0]["start"] == pytest.approx(2.5, abs=0.01)
    assert row["dropouts"][0]["end"] == pytest.approx(3.0, abs=0.01)
    # The floor still measured - the source carries audible room.
    assert row["floor"]["level_dbfs"] is not None


def test_measure_reports_no_dropout_where_none_exists(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_with_dropout(source, dropout=(2.5, 2.5))  # no gap
    row = _cleanup(source)["sources"][0]
    assert row["dropouts"] == []


# ── the fill plan ────────────────────────────────────────────────────

def test_plan_room_tone_fills_plans_one_fill_per_dropout(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_with_dropout(source)
    context = _cleanup(source)
    plans = plan_room_tone_fills(context)
    assert len(plans) == 1
    assert plans[0]["source_file"] == source
    assert len(plans[0]["dropouts"]) == 1
    assert plans[0]["floor"]["level_dbfs"] is not None


def test_plan_room_tone_fills_plans_nothing_without_a_floor(tmp_path):
    """A source whose only pauses are digital silence has dropouts but
    no measured room - it plans no fill, because filling from silence
    is the defect room_tone exists to remove."""
    source = str(tmp_path / "dense.wav")
    # Digital silence everywhere except the word: the floor refuses.
    samples = []
    for i in range(int(4.0 * SR)):
        t = i / SR
        if 0.5 <= t < 1.5:
            samples.append(0.6 * math.sin(2 * math.pi * 440.0 * t))
        else:
            samples.append(0.0)
    _write_wav(source, samples)
    context = _cleanup(source)
    row = context["sources"][0]
    assert row["floor"] == {}
    assert row["dropouts"]  # the silence is still reported
    assert plan_room_tone_fills(context) == []


# ── the post-bridge carries the plan ─────────────────────────────────

def test_post_bridge_carries_room_tone_continuity(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech_with_dropout(source)
    context = _cleanup(source)
    data = {
        "audio_spine": _spine(),
        "a_roll_assignments": _assignments(source),
        "cleanup_context": context,
        "bed_measurements": {"measured": False},
        "mix_windows": [],
        "music_ducking_plan": {
            "enabled": False, "duck_db": None, "release_ms": None,
            "why": "no music bed"},
        "audio_delivery_plan": {
            "dialogue_target_lufs": None,
            "true_peak_ceiling_dbtp": None,
            "why": "defaults"},
    }
    result = resolve_audio_mix(data)
    continuity = result["audio_mix_spec"]["room_tone_continuity"]
    assert len(continuity["sources"]) == 1
    assert continuity["sources"][0]["source_file"] == source
    assert len(continuity["sources"][0]["dropouts"]) == 1


# ── the build's source->timeline mapping ───────────────────────────────

def _clip(**overrides):
    clip = {
        "source_file": "/footage/A.MOV",
        "source_in": 10.0, "source_out": 20.0,
        "timeline_in": 0.0, "timeline_out": 10.0,
    }
    clip.update(overrides)
    return clip


def test_v1_clip_playing_finds_the_clip_that_plays_the_span():
    clips = [_clip(), _clip(source_file="/footage/B.MOV",
                            source_in=30.0, source_out=40.0)]
    host = _v1_clip_playing(clips, "/footage/A.MOV", 12.0, 14.0)
    assert host is not None
    assert host["source_file"] == "/footage/A.MOV"


def test_v1_clip_playing_returns_none_when_no_clip_plays_the_span():
    clips = [_clip()]
    # A dropout past the clip's own source range is not played by it.
    assert _v1_clip_playing(clips, "/footage/A.MOV", 25.0, 28.0) is None
    # A dropout in a source no clip plays is not played at all.
    assert _v1_clip_playing(clips, "/footage/Z.MOV", 1.0, 2.0) is None


def test_source_to_timeline_frames_maps_a_one_to_one_edit():
    clip = _clip(source_in=10.0, source_out=20.0,
                  timeline_in=0.0, timeline_out=10.0)
    tl_in, tl_out, frame_in, frame_out = _source_to_timeline_frames(
        clip, 12.0, 14.0, fps=25.0)
    assert tl_in == pytest.approx(2.0)
    assert tl_out == pytest.approx(4.0)
    assert frame_in == 50
    assert frame_out == 100


def test_source_to_timeline_frames_maps_a_retimed_segment_by_its_ratio():
    # A segment played at half speed: 10 source seconds over 5 timeline
    # seconds. The dropout maps by the ratio, not by a 1:1 assumption.
    clip = _clip(source_in=10.0, source_out=20.0,
                  timeline_in=0.0, timeline_out=5.0)
    tl_in, tl_out, _, _ = _source_to_timeline_frames(
        clip, 12.0, 14.0, fps=25.0)
    assert tl_in == pytest.approx(1.0)
    assert tl_out == pytest.approx(2.0)
