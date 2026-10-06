"""Fairlight dynamics planning: declared compression, never a default.

The defect these tests pin: the chain intent carried EQ with nowhere
for dynamics or limiter processing to come from - and the engine must
not invent them, because a default threshold, ratio or release would
be taste. Now 5.02 asks the mix engineer for a `dynamics_plan` over
the measured speech (opt-in like cleanup: nulls plan nothing),
validates every number through the intent schema with refusals that
travel the post-bridge retry path, and attaches the declared entries
to the intent. Fixtures are stdlib `wave` WAVs under `tmp_path` where
a decode runs; the pure resolve cases need no audio at all.
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
    resolve_dynamics_plan,
    resolve_fairlight_chain,
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
        "dialogue_compression": {
            "threshold_db": -20.0, "ratio": 2.0,
            "attack_ms": 10.0, "release_ms": 100.0,
            "why": "blocks peak at -18 LUFS against a -42 dBFS floor",
        },
        "master_limiter_release_ms": 50.0,
        "why": "the voice swings 6 dB block to block; hold it together",
    }
    plan.update(overrides)
    return plan


def _data(source, dynamics=None):
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
    if dynamics is not None:
        data["dynamics_plan"] = dynamics
    return data


# ── the resolve ──────────────────────────────────────────────────────

def test_valid_answer_resolves_verbatim():
    resolved = resolve_dynamics_plan({"dynamics_plan": _answer()}, -1.0)
    assert resolved["dialogue_compression"]["threshold_db"] == -20.0
    assert resolved["dialogue_compression"]["ratio"] == 2.0
    assert resolved["dialogue_compression"]["knee_db"] == 0.0
    assert resolved["dialogue_compression"]["makeup_gain_db"] == 0.0
    assert resolved["master_limiter_release_ms"] == 50.0


def test_knee_and_makeup_ride_explicit_or_default():
    plan = _answer()
    plan["dialogue_compression"]["knee_db"] = 6.0
    plan["dialogue_compression"]["makeup_gain_db"] = None
    resolved = resolve_dynamics_plan({"dynamics_plan": plan}, -1.0)
    assert resolved["dialogue_compression"]["knee_db"] == 6.0
    assert resolved["dialogue_compression"]["makeup_gain_db"] == 0.0


def test_an_out_of_range_ratio_is_refused_for_retry():
    plan = _answer()
    plan["dialogue_compression"]["ratio"] = 50.0
    with pytest.raises(RenRefusal, match="cannot be delivered"):
        resolve_dynamics_plan({"dynamics_plan": plan}, -1.0)


def test_an_out_of_range_release_is_refused_for_retry():
    plan = _answer(master_limiter_release_ms=5000.0)
    with pytest.raises(RenRefusal, match="cannot be delivered"):
        resolve_dynamics_plan({"dynamics_plan": plan}, -1.0)


def test_a_missing_reason_is_refused():
    plan = _answer(why="  ")
    with pytest.raises(RenRefusal, match="no reason"):
        resolve_dynamics_plan({"dynamics_plan": plan}, -1.0)
    compression = _answer()["dialogue_compression"]
    compression["why"] = ""
    with pytest.raises(RenRefusal, match="no reason"):
        resolve_dynamics_plan(
            {"dynamics_plan": _answer(
                dialogue_compression=compression)}, -1.0)


def test_an_unknown_key_is_refused():
    plan = _answer(attack_curve="vintage")
    with pytest.raises(RenRefusal):
        resolve_dynamics_plan({"dynamics_plan": plan}, -1.0)


def test_a_non_numeric_threshold_is_refused():
    plan = _answer()
    plan["dialogue_compression"]["threshold_db"] = "low"
    with pytest.raises(RenRefusal, match="not a number"):
        resolve_dynamics_plan({"dynamics_plan": plan}, -1.0)


def test_no_answer_means_no_default_compressor():
    resolved = resolve_dynamics_plan({}, -1.0)
    assert resolved["dialogue_compression"] is None
    assert resolved["master_limiter_release_ms"] is None


# ── the attach ───────────────────────────────────────────────────────

def test_declared_entries_join_the_intent():
    dynamics = resolve_dynamics_plan({"dynamics_plan": _answer()}, -1.0)
    chain = resolve_fairlight_chain(
        {"cleanup_context": {"sources": []}, "audio_spine": {},
         "a_roll_assignments": []},
        -14.0, -1.0, dynamics)
    validate_intent(chain["intent"])
    dialogue = [t for t in chain["intent"]["tracks"]
                if t["role"] == "dialogue"]
    assert len(dialogue) == 1
    assert dialogue[0]["dynamics"]["threshold_db"] == -20.0
    assert dialogue[0]["dynamics"]["ratio"] == 2.0
    assert "eq" not in dialogue[0]
    master = [t for t in chain["intent"]["tracks"]
              if t["role"] == "master"]
    assert len(master) == 1
    assert master[0]["limiter"] == {"ceiling_dbTP": -1.0,
                                    "release_ms": 50.0}
    assert chain["intent"]["targets"]["true_peak_ceiling_dbTP"] == -1.0


def test_undeclared_entries_leave_tracks_and_targets_alone():
    dynamics = resolve_dynamics_plan({}, -1.0)
    chain = resolve_fairlight_chain(
        {"cleanup_context": {"sources": []}, "audio_spine": {},
         "a_roll_assignments": []},
        -14.0, -1.0, dynamics)
    validate_intent(chain["intent"])
    assert chain["intent"]["tracks"] == []
    assert chain["intent"]["targets"]["integrated_lufs"] == -14.0


def test_post_bridge_carries_declared_dynamics(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech(source)
    result = resolve_audio_mix(_data(source, _answer()))
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    dialogue = [t for t in chain["intent"]["tracks"]
                if t["role"] == "dialogue"]
    assert len(dialogue) == 1
    assert dialogue[0]["dynamics"]["threshold_db"] == -20.0
    assert "eq" in dialogue[0]
    master = [t for t in chain["intent"]["tracks"]
              if t["role"] == "master"]
    assert master[0]["limiter"]["ceiling_dbTP"] == pytest.approx(
        result["audio_mix_spec"]["delivery_true_peak_ceiling_dbtp"])


def test_post_bridge_without_an_answer_plans_no_processing(tmp_path):
    source = str(tmp_path / "speech.wav")
    _speech(source)
    result = resolve_audio_mix(_data(source))
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    assert all(t.get("dynamics") is None and t.get("limiter") is None
               for t in chain["intent"]["tracks"])
