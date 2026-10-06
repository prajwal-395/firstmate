"""Fairlight chain plan: the dialogue EQ the spectra ask for.

The defect these tests pin: `audio_chain.validate_intent` had no
producer - the typed intent and the Ren-native verifier landed (#1796)
with nothing emitting an intent from measured analysis, so no run
could plan a Fairlight chain from its own footage. Now the dialogue
noise record's spectra derive the bus EQ (high-pass + one peaking
cut, every number's source recorded), and 5.02 attaches the validated
intent to `audio_mix_spec`. Nothing here is hardcoded: the high-pass
is the analyzer's own band edge above the below-room run, the cut is
minus the measured above-median excess, Q is the hump's measured
width. Fixtures are stdlib `wave` WAVs under `tmp_path`; spectra need
numpy only, no model, no Resolve. The attach tests decode and loudnorm
through ffmpeg like every other 5.02 test.
"""
import math
import os
import random
import struct
import sys
import wave

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_5_02_audio_mix.mix import cleanup_context
from library.steps.step_5_02_audio_mix.post_bridge import resolve_audio_mix
from library.tools.audio_chain import validate_intent
from library.tools.dialogue_cleanup import (
    plan_dialogue_eq,
    source_spectra,
)

SR = 8000
EDGES = [20.0, 80.0, 250.0, 700.0, 2000.0, 5000.0, 10000.0, 15000.0,
         20000.0]
INF = float("-inf")


def _write_wav(path, samples, rate=SR):
    packed = struct.pack(f"<{len(samples)}h",
                         *(int(max(-1.0, min(1.0, v)) * 32767.0)
                           for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(packed)


import random as _random


def _rumble_with_words(path, total=4.0):
    """60 Hz rumble plus a broadband room bed everywhere; words 0.5-1.5
    carry 440 Hz over a 150 Hz formant shoulder, each word edge ramped
    10 ms (a hard on/off would splash transient energy across every
    band - real takes rest on continuous room). The room owns 20-80 Hz
    (rumble far above the word transient's share of that band); the
    words own 80-250 and 250-700 Hz. So the derived high-pass must sit
    exactly on the analyzer's 80 Hz edge - predicted from the
    fixture's construction, not from the code."""
    rng = random.Random(1234)
    samples = []
    for i in range(int(total * SR)):
        t = i / SR
        # Loud rumble on purpose: the room must dominate the bottom
        # band so the word transient's share of it stays negligible -
        # otherwise the fixture cannot tell a below-room run from
        # word energy, and neither could the planner.
        v = (0.3 * math.sin(2 * math.pi * 60.0 * t)
             + 0.003 * (rng.random() * 2.0 - 1.0))
        if 0.5 <= t < 1.5:
            attack = min(1.0, (t - 0.5) / 0.01)
            release = min(1.0, (1.5 - t) / 0.01)
            envelope = min(attack, release)
            v += envelope * (0.12 * math.sin(2 * math.pi * 440.0 * t)
                             + 0.03 * math.sin(2 * math.pi * 150.0 * t))
        samples.append(v)
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


def _row(speech, floor, source="s.wav", weight=1.0):
    return {"source_file": source, "speech_bands_db": list(speech),
            "floor_bands_db": list(floor), "weight_seconds": weight}


# ── the pure planner on synthetic spectra ────────────────────────────
#
# The shared fixture: band excesses [0, 8, 15, 4, 0, 0] dB over the
# same floor. Median 2 dB; the 250-700 Hz band exceeds it by 13.

_FLOOR = [-40.0, -50.0, -60.0, -60.0, -60.0, -70.0, -70.0, -70.0]
_SPEECH = [-40.0, -42.0, -45.0, -56.0, -60.0, -70.0, INF, INF]


def test_hp_lands_on_the_band_edge_above_the_below_median_run():
    out = plan_dialogue_eq([_row(_SPEECH, _FLOOR)], EDGES)
    hp = [b for b in out["bands"] if b["type"] == "high_pass"]
    assert len(hp) == 1
    assert hp[0]["frequency_hz"] == 80.0
    assert 'below' in out["basis"]["high_pass"]
    assert 'room' in out["basis"]["high_pass"]


def test_cut_is_minus_the_above_median_excess_with_measured_q():
    out = plan_dialogue_eq([_row(_SPEECH, _FLOOR)], EDGES)
    cuts = [b for b in out["bands"] if b["type"] == "peaking"]
    assert len(cuts) == 1
    assert cuts[0]["frequency_hz"] == pytest.approx(round((250.0 * 700.0) ** 0.5, 1))
    assert cuts[0]["gain_db"] == pytest.approx(-13.0)
    # The hump run is bands 1-3 (above-median 6/13/2): Q is the
    # band center over that measured width, 80-2000 Hz.
    assert cuts[0]["q"] == pytest.approx(
        round((250.0 * 700.0) ** 0.5, 1) / (2000.0 - 80.0), rel=0.01)
    validate_intent({"tracks": [{"track": "dialogue",
                                 "role": "dialogue", "eq": out["bands"]}],
                     "targets": {}})


def test_no_hp_where_speech_starts_above_the_center():
    speech = [-30.0, -42.0, -45.0, -56.0, -60.0, -70.0, INF, INF]
    out = plan_dialogue_eq([_row(speech, _FLOOR)], EDGES)
    assert [b for b in out["bands"] if b["type"] == "high_pass"] == []
    assert 'median' in out["basis"]["high_pass"]


def test_no_cut_on_a_flat_spectrum():
    speech = [-30.0, -40.0, -50.0, -50.0, -50.0, -60.0, -60.0, -60.0]
    out = plan_dialogue_eq([_row(speech, _FLOOR)], EDGES)
    assert [b for b in out["bands"] if b["type"] == "peaking"] == []
    assert 'flat' in out["basis"]["peaking"]


def test_hp_stops_at_the_speech_hump_own_band():
    # The hump sits low (80-250 Hz) with the median reached again
    # above it: the below-median run still stops at the hump's own
    # band instead of crossing into speech.
    speech = [-42.0, -35.0, -61.0, -55.0, -55.0, -65.0, INF, INF]
    out = plan_dialogue_eq([_row(speech, _FLOOR)], EDGES)
    hp = [b for b in out["bands"] if b["type"] == "high_pass"]
    assert [b["frequency_hz"] for b in hp] == [80.0]
    cuts = [b for b in out["bands"] if b["type"] == "peaking"]
    assert len(cuts) == 1
    assert cuts[0]["frequency_hz"] == pytest.approx(round((80.0 * 250.0) ** 0.5, 1))
    assert cuts[0]["gain_db"] == pytest.approx(-10.0)
    validate_intent({"tracks": [{"track": "dialogue",
                                 "role": "dialogue", "eq": out["bands"]}],
                     "targets": {}})


def test_an_unappliable_cut_is_dropped_never_clamped():
    speech = [-40.0, -20.0, -10.0, -50.0, -60.0, -70.0, INF, INF]
    out = plan_dialogue_eq([_row(speech, _FLOOR)], EDGES)
    assert [b for b in out["bands"] if b["type"] == "peaking"] == []
    assert len(out["dropped"]) == 1
    assert 'never' in out["basis"]["peaking"]
    assert 'clamp' in out["basis"]["peaking"]
    # The high-pass survives the dropped cut: one refusal drops one
    # band, never the plan.
    assert [b for b in out["bands"] if b["type"] == "high_pass"] != []


def test_bus_excess_weights_sources_by_played_speech():
    flat_six = [-34.0, -44.0, -54.0, -54.0, -54.0, -64.0, INF, INF]
    light = plan_dialogue_eq(
        [_row(_SPEECH, _FLOOR, "a.wav", 3.0),
         _row(flat_six, _FLOOR, "b.wav", 1.0)], EDGES)
    heavy = plan_dialogue_eq(
        [_row(_SPEECH, _FLOOR, "a.wav", 1.0),
         _row(flat_six, _FLOOR, "b.wav", 3.0)], EDGES)
    gain = lambda out: next(
        b["gain_db"] for b in out["bands"] if b["type"] == "peaking")
    # Source b's flat 6 dB excess dilutes source a's hump: the more
    # b weighs, the shallower the derived cut.
    assert gain(light) < gain(heavy) < 0.0


# ── the spectra off a real decode ────────────────────────────────────

def test_spectra_derive_the_80hz_hp_from_measured_rumble(tmp_path):
    source = str(tmp_path / "rumble.wav")
    _rumble_with_words(source)
    spectra = source_spectra(source, [(0.0, 4.0)], [(0.5, 1.5)],
                             (1.5, 4.0))
    assert spectra["absences"] == []
    assert spectra["speech_seconds"] == pytest.approx(1.0)
    assert spectra["spectrum_edges_hz"] == EDGES
    out = plan_dialogue_eq(
        [dict(spectra, weight_seconds=spectra["speech_seconds"])],
        spectra["spectrum_edges_hz"])
    hp = [b for b in out["bands"] if b["type"] == "high_pass"]
    assert len(hp) == 1
    assert hp[0]["frequency_hz"] == 80.0
    cuts = [b for b in out["bands"] if b["type"] == "peaking"]
    assert len(cuts) == 1
    # The words own 80-250 and 250-700 Hz; the cut sits on one of them.
    assert 80.0 <= cuts[0]["frequency_hz"] <= 700.0
    # The cut equals the measured hump above the median, recomputed
    # here from the measured spectra - the relationship, not a number.
    excess = [s - f for s, f in zip(spectra["speech_bands_db"],
                                    spectra["floor_bands_db"])
              if s != INF and f != INF]
    ordered = sorted(excess)
    median = (ordered[len(ordered) // 2] if len(ordered) % 2 else
              (ordered[len(ordered) // 2 - 1]
               + ordered[len(ordered) // 2]) / 2.0)
    assert cuts[0]["gain_db"] == pytest.approx(
        -(max(excess) - median), abs=0.15)


def test_spectra_state_the_floor_absence(tmp_path):
    source = str(tmp_path / "rumble.wav")
    _rumble_with_words(source)
    spectra = source_spectra(source, [(0.0, 4.0)], [(0.5, 1.5)], None)
    assert spectra["floor_bands_db"] == [INF] * 8
    assert any('floor' in a for a in spectra["absences"])


# ── the post-bridge attaches the validated intent ────────────────────

def _chain_data(source):
    return {
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


def test_post_bridge_attaches_the_derived_chain(tmp_path):
    source = str(tmp_path / "rumble.wav")
    _rumble_with_words(source)
    result = resolve_audio_mix(_chain_data(source))
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    dialogue = [t for t in chain["intent"]["tracks"]
                if t["role"] == "dialogue"]
    assert len(dialogue) == 1
    hp = [b for b in dialogue[0]["eq"] if b["type"] == "high_pass"]
    assert [b["frequency_hz"] for b in hp] == [80.0]
    # Targets are the delivery numbers the step resolved, and the
    # ceiling travels as a target only: limiter processing waits for
    # the executor slice, so no track carries one.
    assert chain["intent"]["targets"]["integrated_lufs"] == pytest.approx(
        result["audio_mix_spec"]["delivery_lufs_target"])
    assert chain["intent"]["targets"]["true_peak_ceiling_dbTP"] == \
        pytest.approx(
            result["audio_mix_spec"]["delivery_true_peak_ceiling_dbtp"])
    assert all(t.get("limiter") is None
               for t in chain["intent"]["tracks"])
    assert chain["unplanned"] == []
    assert 'executor' in chain


def test_post_bridge_plans_no_dialogue_eq_without_a_floor(tmp_path):
    source = str(tmp_path / "dense.wav")
    samples = []
    for i in range(int(4.0 * SR)):
        t = i / SR
        if 0.5 <= t < 1.5:
            samples.append(0.6 * math.sin(2 * math.pi * 440.0 * t))
        else:
            samples.append(0.0)
    _write_wav(source, samples)
    result = resolve_audio_mix(_chain_data(source))
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    assert [t for t in chain["intent"]["tracks"]
            if t["role"] == "dialogue"] == []
    assert len(chain["unplanned"]) == 1
    assert chain["unplanned"][0]["source_file"] == source
    # The delivery targets still travel: the verifier judges the
    # render against them with or without an EQ plan.
    assert chain["intent"]["targets"]["integrated_lufs"] is not None


def _two_clip_spine():
    return {"structure": [
        {"position": "hook", "block_type": "hook", "clip_id": "clip_1",
         "content": {"clip_id": "clip_1", "word_timestamps": [
             {"word": "hello", "source_start": 0.5,
              "source_end": 1.5}]}},
        {"position": "body", "block_type": "body", "clip_id": "clip_2",
         "content": {"clip_id": "clip_2", "word_timestamps": [
             {"word": "world", "source_start": 0.5,
              "source_end": 1.5}]}},
    ]}


def _two_clip_assignments(gone, survivor):
    return [
        {"spine_block_position": "hook", "video_segments": [{
            "clip_id": "clip_1", "source_file": gone,
            "video_in": 0.0, "video_out": 4.0}]},
        {"spine_block_position": "body", "video_segments": [{
            "clip_id": "clip_2", "source_file": survivor,
            "video_in": 0.0, "video_out": 4.0}]},
    ]


def test_post_bridge_keeps_the_chain_when_one_source_is_unreadable(
        tmp_path):
    """A file that vanishes between the measure pass and the plan still
    leaves the bus chain standing on the surviving source - one bad
    source is named in `unplanned`, never a sunk run."""
    gone = str(tmp_path / "gone.wav")
    survivor = str(tmp_path / "rumble.wav")
    _rumble_with_words(gone)
    _rumble_with_words(survivor)
    context = cleanup_context(
        _two_clip_spine(), _two_clip_assignments(gone, survivor))
    assert len(context["sources"]) == 2
    os.remove(gone)
    data = {
        "audio_spine": _two_clip_spine(),
        "a_roll_assignments": _two_clip_assignments(gone, survivor),
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
    chain = result["audio_mix_spec"]["fairlight_chain"]
    validate_intent(chain["intent"])
    dialogue = [t for t in chain["intent"]["tracks"]
                if t["role"] == "dialogue"]
    assert len(dialogue) == 1
    assert [s["source_file"] for s in chain["unplanned"]] == [gone]
