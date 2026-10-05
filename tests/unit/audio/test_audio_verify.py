"""The audio verifier measures generated signals against intent targets.

These tests use short synthetic WAVs (tone, noise, silence gap) with
known properties - no real footage, no Resolve, no model.
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

from library.tools.audio_chain import AudioChainIntent, AudioTargets
from library.tools.audio_verify import verify_mix

SR = 8000


def _write_wav(path, samples, rate=SR):
    packed = struct.pack(
        f"<{len(samples)}h",
        *(int(max(-1.0, min(1.0, v)) * 32767.0) for v in samples))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(packed)


def _tone(path, frequency=1000.0, amplitude=0.5, seconds=2.0):
    samples = []
    for i in range(int(seconds * SR)):
        t = i / SR
        samples.append(amplitude * math.sin(2 * math.pi * frequency * t))
    _write_wav(path, samples)


def _tone_with_silence(path, frequency=1000.0, amplitude=0.5,
                       seconds=2.0, silence_start=0.8, silence_end=1.3):
    samples = []
    for i in range(int(seconds * SR)):
        t = i / SR
        if silence_start <= t < silence_end:
            samples.append(0.0)
        else:
            samples.append(amplitude * math.sin(2 * math.pi * frequency * t))
    _write_wav(path, samples)


def _intent(**target_kwargs):
    return AudioChainIntent(targets=AudioTargets(**target_kwargs))


def test_tone_lufs_passes_against_measured_target(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.5)
    findings = verify_mix(_intent(integrated_lufs=-14.0), mix)
    lufs_finding = next(f for f in findings if f.name == "lufs")
    assert lufs_finding.measured is not None
    measured = lufs_finding.measured
    assert isinstance(measured, (int, float))
    assert -30.0 < measured < -3.0


def test_lufs_fails_when_target_is_wrong(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.5)
    findings = verify_mix(_intent(integrated_lufs=-20.0), mix)
    lufs_finding = next(f for f in findings if f.name == "lufs")
    assert not lufs_finding.passed


def test_lufs_passes_when_target_matches(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.5)
    findings = verify_mix(_intent(integrated_lufs=-20.0), mix)
    lufs_finding = next(f for f in findings if f.name == "lufs")
    measured = lufs_finding.measured
    findings2 = verify_mix(_intent(integrated_lufs=measured), mix)
    lufs_finding2 = next(f for f in findings2 if f.name == "lufs")
    assert lufs_finding2.passed


def test_true_peak_fails_when_ceiling_is_exceeded(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.99)
    findings = verify_mix(
        _intent(true_peak_ceiling_dbTP=-1.0), mix)
    tp_finding = next(f for f in findings if f.name == "true_peak")
    assert not tp_finding.passed


def test_true_peak_passes_when_under_ceiling(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.5)
    findings = verify_mix(
        _intent(true_peak_ceiling_dbTP=-1.0), mix)
    tp_finding = next(f for f in findings if f.name == "true_peak")
    assert tp_finding.passed


def test_peak_only_intent_does_not_judge_loudness(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.5)
    findings = verify_mix(
        _intent(true_peak_ceiling_dbTP=-1.0), mix)
    names = [f.name for f in findings]
    assert "true_peak" in names
    assert "lufs" not in names


def test_lufs_only_intent_does_not_judge_peak(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, amplitude=0.5)
    findings = verify_mix(
        _intent(integrated_lufs=-14.0), mix)
    names = [f.name for f in findings]
    assert "lufs" in names
    assert "true_peak" not in names


def test_silence_detected_in_gap(tmp_path):
    mix = str(tmp_path / "gap.wav")
    _tone_with_silence(mix, silence_start=0.8, silence_end=1.3)
    findings = verify_mix(_intent(max_silence_seconds=0.1), mix)
    silence_finding = next(f for f in findings if f.name == "silence")
    assert not silence_finding.passed
    assert silence_finding.measured >= 0.4


def test_silence_passes_without_gap(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix)
    findings = verify_mix(_intent(max_silence_seconds=0.1), mix)
    silence_finding = next(f for f in findings if f.name == "silence")
    assert silence_finding.passed


def test_separation_measured_from_stems(tmp_path):
    speech = str(tmp_path / "speech.wav")
    bed = str(tmp_path / "bed.wav")
    _tone(speech, amplitude=0.5)
    _tone(bed, amplitude=0.05)
    findings = verify_mix(
        _intent(separation_db=5.0),
        str(tmp_path / "mix.wav"),
        speech_stem=speech,
        bed_stem=bed,
    )
    sep_finding = next(f for f in findings if f.name == "separation")
    assert sep_finding.measured is not None
    assert sep_finding.measured > 10.0


def test_separation_fails_when_target_not_met(tmp_path):
    speech = str(tmp_path / "speech.wav")
    bed = str(tmp_path / "bed.wav")
    _tone(speech, amplitude=0.5)
    _tone(bed, amplitude=0.4)
    findings = verify_mix(
        _intent(separation_db=15.0),
        str(tmp_path / "mix.wav"),
        speech_stem=speech,
        bed_stem=bed,
    )
    sep_finding = next(f for f in findings if f.name == "separation")
    assert not sep_finding.passed


def test_spectral_balance_measured(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, frequency=1000.0)
    findings = verify_mix(
        _intent(spectral_band_targets_db=[-60.0] * 8), mix)
    spec_finding = next(f for f in findings if f.name == "spectral_balance")
    assert spec_finding.measured is not None
    assert len(spec_finding.measured) == 8


def test_spectral_balance_fails_when_target_is_wrong(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, frequency=1000.0)
    findings = verify_mix(
        _intent(spectral_band_targets_db=[0.0] * 8,
                spectral_tolerance_db=3.0), mix)
    spec_finding = next(f for f in findings if f.name == "spectral_balance")
    assert not spec_finding.passed


def test_spectral_balance_reports_without_verdict_when_no_tolerance(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix, frequency=1000.0)
    findings = verify_mix(
        _intent(spectral_band_targets_db=[0.0] * 8), mix)
    spec_finding = next(f for f in findings if f.name == "spectral_balance")
    assert spec_finding.passed is None
    assert "without a pass/fail verdict" in spec_finding.detail


def test_missing_mix_file_returns_finding(tmp_path):
    findings = verify_mix(
        _intent(integrated_lufs=-14.0),
        str(tmp_path / "nonexistent.wav"),
    )
    assert len(findings) == 1
    assert findings[0].name == "lufs"
    assert not findings[0].passed


def test_no_targets_returns_no_findings(tmp_path):
    mix = str(tmp_path / "tone.wav")
    _tone(mix)
    findings = verify_mix(_intent(), mix)
    assert findings == []
