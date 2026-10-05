"""Typed audio-chain intent for Fairlight track/bus/master processing.

A planner emits this; a Fairlight UI executor (a later lane) consumes it.
No executor is registered here, and the intent is deliberately NOT in
`edit_algebra.ALGEBRA` - the algebra is being ported to Rust.  Registration
will need: a `resolve.audio.chain` op (domain `audio_mix`) whose `apply`
runs the chain through the Fairlight UI executor, a `verify` that calls
`audio_verify.verify_mix`, and an `inverse` that restores the previous
chain state per track.

The intent carries two halves: the PROCESSING each track/bus/master
receives (EQ, dynamics, gate, limiter, sends) and the TARGETS the
finished mix should measure (loudness, true peak, separation, spectral
balance, silence).  The verifier (`library/tools/audio_verify.py`) reads
the targets from here - they are never hardcoded in the verifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


class AudioChainError(ValueError):
    """A requested audio-chain value cannot be planned or delivered."""


EQ_BAND_TYPES = ("low_shelf", "high_shelf", "peaking", "low_pass",
                 "high_pass", "notch")

TRACK_ROLES = ("dialogue", "music", "sfx", "master", "bus")


@dataclass
class EQBand:
    type: str
    frequency_hz: float
    gain_db: float
    q: float


@dataclass
class Dynamics:
    threshold_db: float
    ratio: float
    attack_ms: float
    release_ms: float
    knee_db: float = 0.0
    makeup_gain_db: float = 0.0


@dataclass
class Gate:
    threshold_db: float
    ratio: float
    attack_ms: float
    release_ms: float
    hold_ms: float
    range_db: float


@dataclass
class Limiter:
    ceiling_dbTP: float
    release_ms: float


@dataclass
class Send:
    target: str
    level_db: float
    pre_fade: bool = False


@dataclass
class TrackChain:
    track: str
    role: str
    eq: list[EQBand] = field(default_factory=list)
    dynamics: Optional[Dynamics] = None
    gate: Optional[Gate] = None
    limiter: Optional[Limiter] = None
    sends: list[Send] = field(default_factory=list)


@dataclass
class AudioTargets:
    integrated_lufs: Optional[float] = None
    lufs_tolerance: Optional[float] = None
    true_peak_ceiling_dbTP: Optional[float] = None
    separation_db: Optional[float] = None
    spectral_band_targets_db: Optional[list[float]] = None
    spectral_tolerance_db: Optional[float] = None
    max_silence_seconds: Optional[float] = None


@dataclass
class AudioChainIntent:
    tracks: list[TrackChain] = field(default_factory=list)
    targets: AudioTargets = field(default_factory=AudioTargets)


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioChainError(f"{label} must be a number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise AudioChainError(f"{label} must be finite, got {value!r}")
    if not low <= number <= high:
        raise AudioChainError(
            f"{label} {number!r} is outside {low:g}..{high:g}")
    return number


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AudioChainError(f"{label} must be a non-empty string")
    return value.strip()


def validate_eq_band(raw: Any, index: int = 0) -> EQBand:
    label = f"EQ band {index}"
    if not isinstance(raw, dict):
        raise AudioChainError(f"{label} is not an object")
    kind = raw.get("type")
    if kind not in EQ_BAND_TYPES:
        raise AudioChainError(
            f"{label} names unknown type {kind!r}; choose one of "
            f"{list(EQ_BAND_TYPES)}")
    return EQBand(
        type=kind,
        frequency_hz=_number(raw.get("frequency_hz"),
                               f"{label} frequency_hz", 20.0, 20000.0),
        gain_db=_number(raw.get("gain_db"), f"{label} gain_db",
                        -24.0, 24.0),
        q=_number(raw.get("q"), f"{label} q", 0.1, 20.0),
    )


def validate_dynamics(raw: Any, index: int = 0) -> Dynamics:
    label = f"dynamics {index}"
    if not isinstance(raw, dict):
        raise AudioChainError(f"{label} is not an object")
    return Dynamics(
        threshold_db=_number(raw.get("threshold_db"),
                              f"{label} threshold_db", -72.0, 0.0),
        ratio=_number(raw.get("ratio"), f"{label} ratio", 1.0, 20.0),
        attack_ms=_number(raw.get("attack_ms"), f"{label} attack_ms",
                          0.01, 2000.0),
        release_ms=_number(raw.get("release_ms"), f"{label} release_ms",
                            0.01, 2000.0),
        knee_db=_number(raw.get("knee_db", 0.0), f"{label} knee_db",
                        0.0, 24.0),
        makeup_gain_db=_number(raw.get("makeup_gain_db", 0.0),
                               f"{label} makeup_gain_db", -24.0, 24.0),
    )


def validate_gate(raw: Any, index: int = 0) -> Gate:
    label = f"gate {index}"
    if not isinstance(raw, dict):
        raise AudioChainError(f"{label} is not an object")
    return Gate(
        threshold_db=_number(raw.get("threshold_db"),
                              f"{label} threshold_db", -72.0, 0.0),
        ratio=_number(raw.get("ratio"), f"{label} ratio", 0.0, 1.0),
        attack_ms=_number(raw.get("attack_ms"), f"{label} attack_ms",
                          0.01, 2000.0),
        release_ms=_number(raw.get("release_ms"), f"{label} release_ms",
                            0.01, 2000.0),
        hold_ms=_number(raw.get("hold_ms"), f"{label} hold_ms",
                        0.0, 5000.0),
        range_db=_number(raw.get("range_db"), f"{label} range_db",
                         0.0, 72.0),
    )


def validate_limiter(raw: Any, index: int = 0) -> Limiter:
    label = f"limiter {index}"
    if not isinstance(raw, dict):
        raise AudioChainError(f"{label} is not an object")
    return Limiter(
        ceiling_dbTP=_number(raw.get("ceiling_dbTP"),
                              f"{label} ceiling_dbTP", -10.0, 0.0),
        release_ms=_number(raw.get("release_ms"), f"{label} release_ms",
                            0.01, 2000.0),
    )


def validate_send(raw: Any, index: int = 0) -> Send:
    label = f"send {index}"
    if not isinstance(raw, dict):
        raise AudioChainError(f"{label} is not an object")
    pre_fade = raw.get("pre_fade", False)
    if not isinstance(pre_fade, bool):
        raise AudioChainError(f"{label} pre_fade must be a boolean")
    return Send(
        target=_string(raw.get("target"), f"{label} target"),
        level_db=_number(raw.get("level_db"), f"{label} level_db",
                         -60.0, 12.0),
        pre_fade=pre_fade,
    )


def validate_track_chain(raw: Any, index: int = 0) -> TrackChain:
    label = f"track chain {index}"
    if not isinstance(raw, dict):
        raise AudioChainError(f"{label} is not an object")
    role = raw.get("role")
    if role not in TRACK_ROLES:
        raise AudioChainError(
            f"{label} names unknown role {role!r}; choose one of "
            f"{list(TRACK_ROLES)}")
    eq_raw = raw.get("eq", [])
    if not isinstance(eq_raw, list):
        raise AudioChainError(f"{label} eq must be a list")
    dynamics_raw = raw.get("dynamics")
    gate_raw = raw.get("gate")
    limiter_raw = raw.get("limiter")
    sends_raw = raw.get("sends", [])
    if not isinstance(sends_raw, list):
        raise AudioChainError(f"{label} sends must be a list")
    return TrackChain(
        track=_string(raw.get("track"), f"{label} track"),
        role=role,
        eq=[validate_eq_band(b, i) for i, b in enumerate(eq_raw)],
        dynamics=(validate_dynamics(dynamics_raw, index)
                  if dynamics_raw is not None else None),
        gate=validate_gate(gate_raw, index) if gate_raw is not None else None,
        limiter=(validate_limiter(limiter_raw, index)
                 if limiter_raw is not None else None),
        sends=[validate_send(s, i) for i, s in enumerate(sends_raw)],
    )


def validate_targets(raw: Any) -> AudioTargets:
    if raw is None:
        return AudioTargets()
    if not isinstance(raw, dict):
        raise AudioChainError("targets must be an object")
    spectral = raw.get("spectral_band_targets_db")
    if spectral is not None:
        if not isinstance(spectral, list):
            raise AudioChainError(
                "targets spectral_band_targets_db must be a list")
        for i, level in enumerate(spectral):
            if isinstance(level, bool) or not isinstance(level, (int, float)):
                raise AudioChainError(
                    f"spectral_band_targets_db[{i}] must be a number")
            if not -120.0 <= float(level) <= 0.0:
                raise AudioChainError(
                    f"spectral_band_targets_db[{i}] {level!r} is outside "
                    f"-120..0 dB")
    return AudioTargets(
        integrated_lufs=(float(raw["integrated_lufs"])
                         if raw.get("integrated_lufs") is not None else None),
        lufs_tolerance=(float(raw["lufs_tolerance"])
                        if raw.get("lufs_tolerance") is not None else None),
        true_peak_ceiling_dbTP=(
            float(raw["true_peak_ceiling_dbTP"])
            if raw.get("true_peak_ceiling_dbTP") is not None else None),
        separation_db=(float(raw["separation_db"])
                       if raw.get("separation_db") is not None else None),
        spectral_band_targets_db=(list(spectral)
                                  if spectral is not None else None),
        spectral_tolerance_db=(
            float(raw["spectral_tolerance_db"])
            if raw.get("spectral_tolerance_db") is not None else None),
        max_silence_seconds=(
            float(raw["max_silence_seconds"])
            if raw.get("max_silence_seconds") is not None else None),
    )


def validate_intent(raw: Any) -> AudioChainIntent:
    """Return a checked AudioChainIntent, refusing impossible values."""
    if not isinstance(raw, dict):
        raise AudioChainError("audio chain intent must be an object")
    tracks_raw = raw.get("tracks", [])
    if not isinstance(tracks_raw, list):
        raise AudioChainError("tracks must be a list")
    return AudioChainIntent(
        tracks=[validate_track_chain(t, i) for i, t in enumerate(tracks_raw)],
        targets=validate_targets(raw.get("targets")),
    )
