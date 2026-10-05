"""The audio-chain intent refuses impossible values and accepts valid ones.

These tests drive the real validation functions on synthetic inputs -
no model, no Resolve, no real footage.
"""

import pytest

from library.tools.audio_chain import (
    AudioChainError,
    validate_eq_band,
    validate_gate,
    validate_intent,
    validate_limiter,
    validate_dynamics,
    validate_send,
    validate_track_chain,
    validate_targets,
)


def test_valid_intent_passes():
    intent = validate_intent({
        "tracks": [{
            "track": "A1",
            "role": "dialogue",
            "eq": [{
                "type": "peaking",
                "frequency_hz": 250.0,
                "gain_db": -2.0,
                "q": 1.2,
            }],
            "dynamics": {
                "threshold_db": -18.0,
                "ratio": 3.0,
                "attack_ms": 10.0,
                "release_ms": 100.0,
            },
            "limiter": {
                "ceiling_dbTP": -1.0,
                "release_ms": 50.0,
            },
            "sends": [{
                "target": "Bus 1",
                "level_db": -6.0,
            }],
        }],
        "targets": {
            "integrated_lufs": -14.0,
            "lufs_tolerance": 1.0,
            "true_peak_ceiling_dbTP": -1.0,
            "separation_db": 12.0,
        },
    })
    assert len(intent.tracks) == 1
    track = intent.tracks[0]
    assert track.track == "A1"
    assert track.role == "dialogue"
    assert len(track.eq) == 1
    assert track.eq[0].type == "peaking"
    assert track.eq[0].frequency_hz == 250.0
    assert track.eq[0].gain_db == -2.0
    assert track.eq[0].q == 1.2
    assert track.dynamics is not None
    assert track.dynamics.threshold_db == -18.0
    assert track.limiter is not None
    assert track.limiter.ceiling_dbTP == -1.0
    assert len(track.sends) == 1
    assert track.sends[0].target == "Bus 1"
    assert intent.targets.integrated_lufs == -14.0
    assert intent.targets.separation_db == 12.0


def test_empty_intent_passes():
    intent = validate_intent({})
    assert intent.tracks == []
    assert intent.targets.integrated_lufs is None


def test_unknown_eq_band_type_refused():
    with pytest.raises(AudioChainError, match="unknown type"):
        validate_eq_band({"type": "unknown", "frequency_hz": 100,
                          "gain_db": 0, "q": 1})


def test_eq_frequency_out_of_range_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_eq_band({"type": "peaking", "frequency_hz": 10,
                          "gain_db": 0, "q": 1})


def test_eq_gain_out_of_range_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_eq_band({"type": "peaking", "frequency_hz": 100,
                          "gain_db": 30, "q": 1})


def test_eq_q_out_of_range_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_eq_band({"type": "peaking", "frequency_hz": 100,
                          "gain_db": 0, "q": 0.01})


def test_dynamics_threshold_out_of_range_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_dynamics({"threshold_db": 5, "ratio": 2,
                           "attack_ms": 10, "release_ms": 100})


def test_dynamics_ratio_below_one_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_dynamics({"threshold_db": -20, "ratio": 0.5,
                           "attack_ms": 10, "release_ms": 100})


def test_gate_ratio_above_one_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_gate({"threshold_db": -40, "ratio": 2,
                       "attack_ms": 1, "release_ms": 50,
                       "hold_ms": 10, "range_db": 20})


def test_limiter_ceiling_above_zero_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_limiter({"ceiling_dbTP": 1.0, "release_ms": 50})


def test_send_level_out_of_range_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_send({"target": "Bus 1", "level_db": 20})


def test_unknown_track_role_refused():
    with pytest.raises(AudioChainError, match="unknown role"):
        validate_track_chain({"track": "A1", "role": "unknown"})


def test_non_numeric_eq_value_refused():
    with pytest.raises(AudioChainError, match="must be a number"):
        validate_eq_band({"type": "peaking", "frequency_hz": "low",
                          "gain_db": 0, "q": 1})


def test_spectral_target_out_of_range_refused():
    with pytest.raises(AudioChainError, match="outside"):
        validate_targets({"spectral_band_targets_db": [10.0]})


def test_non_object_intent_refused():
    with pytest.raises(AudioChainError, match="must be an object"):
        validate_intent("not a dict")
