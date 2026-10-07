"""Fairlight gate planning: declared gating, never a default.

The defect these tests pin: the chain intent carried EQ, dynamics and
a limiter ceiling with nowhere for a gate to come from - and the
engine must not invent one, because a default threshold, ratio or
hold would be taste. Now 5.02 asks the mix engineer for a
`gate_plan` over the measured room (opt-in like cleanup: a missing
plan or a null gate plans nothing), validates every number through
the intent schema with refusals that travel the post-bridge retry
path, and attaches the declared gate to the intent.
"""
import math
import os
import struct
import sys
import wave

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_5_02_audio_mix.mix import cleanup_context
from library.steps.step_5_02_audio_mix.post_bridge import (
    resolve_audio_mix,
    resolve_gate_plan,
)
from library.tools.audio_chain import validate_intent
from library.tools.ren_refusal import RenRefusal

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


def _speech(path, total=4.0):
    samples = []
    for i in range(int(total * SR)):
        t = i / SR
        if 0.5 <= t < 1.5:
            samples.append(0.6 * math.sin(2 * math.pi * 440.0 * t))
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


def _answer(**overrides):
    plan = {
        "dialogue_gate": {
            "threshold_db": -38.0, "ratio": 0.5,
            "attack_ms": 1.0, "release_ms": 100.0,
            "hold_ms": 200.0, "range_db": 24.0,
            "why": "room sits at -42 dBFS; open 4 dB above it",
        },
        "why": "the room is audible between words",
    }
    plan.update(overrides)
    return plan


def _data(source, gate=None):
    data = {
        "audio_spine": _spine(),
        "a_roll_assignments": _assignments(source),
        "cleanup_context": cleanup_context(_spine(),
                                           _assignments(source)),
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
    if gate is not None:
        data["gate_plan"] = gate
    return data


# ── the resolve ──────────────────────────────────────────────────────

def test_valid_answer_resolves_verbatim():
    resolved = resolve_gate_plan({"gate_plan": _answer()})
    gate = resolved["dialogue_gate"]
    assert gate["threshold_db"] == -38.0
    assert gate["ratio"] == 0.5
    assert gate["hold_ms"] == 200.0
    assert gate["range_db"] == 24.0


def test_an_out_of_range_ratio_is_refused_for_retry():
    plan = _answer()
    plan["dialogue_gate"]["ratio"] = 5.0
    with pytest.raises(RenRefusal, match="cannot be delivered"):
        resolve_gate_plan({"gate_plan": plan})


def test_a_missing_reason_is_refused():
    plan = _answer(why="  ")
    with pytest.raises(RenRefusal, match="no reason"):
        resolve_gate_plan({"gate_plan": plan})
    entry = _answer()["dialogue_gate"]
    entry["why"] = ""
    with pytest.raises(RenRefusal, match="no reason"):
        resolve_gate_plan(
            {"gate_plan": _answer(dialogue_gate=entry)})


def test_an_unknown_key_is_refused():
    plan = _answer(sidechain="bus-2")
    with pytest.raises(RenRefusal):
        resolve_gate_plan({"gate_plan": plan})


def test_a_non_numeric_hold_is_refused():
    plan = _answer()
    plan["dialogue_gate"]["hold_ms"] = "long"
    with pytest.raises(RenRefusal, match="not a number"):
        resolve_gate_plan({"gate_plan": plan})


def test_no_answer_means_no_default_gate():
    resolved = resolve_gate_plan({})
    assert resolved["dialogue_gate"] is None


def test_null_gate_means_no_gate():
    resolved = resolve_gate_plan(
        {"gate_plan": _answer(dialogue_gate=None)})
    assert resolved["dialogue_gate"] is None


# ── the attach ───────────────────────────────────────────────────────

def test_post_bridge_carries_a_declared_gate(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech(source)
    result = resolve_audio_mix(_data(source, _answer()))
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    dialogue = [t for t in chain["intent"]["tracks"]
                if t["role"] == "dialogue"]
    assert len(dialogue) == 1
    assert dialogue[0]["gate"]["threshold_db"] == -38.0
    assert dialogue[0]["gate"]["hold_ms"] == 200.0


def test_post_bridge_without_an_answer_plans_no_gate(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech(source)
    result = resolve_audio_mix(_data(source))
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    assert all(t.get("gate") is None
               for t in chain["intent"]["tracks"])
