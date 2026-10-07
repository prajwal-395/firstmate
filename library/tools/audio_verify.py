"""Ren-native verification of a rendered mix against the audio-chain intent.

Resolve executes the mix; Ren judges it.  This module reuses the existing
measurement passes - `render_qa.measure_lufs` for integrated LUFS and true
peak, `speech_loudness.measure_range` for per-stem loudness,
`room_tone.spectrum_db` for coarse spectral balance, and
`render_qa.find_digital_zero_runs` for silence/dropout spans.  No second
loudness implementation is added here.

Targets come from the intent (`audio_chain.AudioChainTargets`) or from
project data the caller supplies - never hardcoded in this module.  When
the intent declares no target for a property, that property is not
measured here.  When the intent declares band targets but no tolerance,
the bands are reported without a pass/fail verdict.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from library.tools.audio_chain import AudioChainIntent
from library.tools.render_qa import (
    DEFAULT_LUFS_TARGET,
    DEFAULT_LUFS_TOLERANCE,
    DEFAULT_TRUE_PEAK_CEILING_DBTP,
    find_digital_zero_runs,
    measure_lufs,
)
from library.tools.speech_loudness import measure_range, separation_delivered_db


@dataclass
class AudioFinding:
    name: str
    passed: Optional[bool]
    measured: Any
    target: Any
    detail: str


def _probe_duration(path: str) -> Optional[float]:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=30,
                             check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return float(res.stdout.strip())
    except (TypeError, ValueError):
        return None


def _spectral_balance(path: str, sample_rate: int = 48000) -> list:
    import numpy as np
    from library.tools.room_tone import decode_mono, spectrum_db

    samples, rate = decode_mono(path, sample_rate)
    arr = np.asarray(list(samples), dtype=np.float64)
    if arr.size == 0:
        return []
    window = arr[:min(arr.size, rate * 10)]
    return spectrum_db(window, rate)


def _measure_stem_lufs(stem_path: str) -> Optional[float]:
    duration = _probe_duration(stem_path)
    if duration is None or duration <= 0:
        return None
    reading = measure_range(stem_path, 0.0, duration)
    if not reading.get("measured"):
        return None
    return reading.get("integrated_lufs")


def verify_mix(
    intent: AudioChainIntent,
    mix_path: str,
    speech_stem: Optional[str] = None,
    bed_stem: Optional[str] = None,
    music_path: Optional[str] = None,
    music_automation: Optional[Sequence[dict]] = None,
    music_offset_seconds: Optional[float] = None,
    sample_rate: int = 48000,
) -> list[AudioFinding]:
    """Measure a rendered mix against the intent's declared targets.

    Returns a list of `AudioFinding`, one per property the intent declares
    a target for.  Properties with no declared target are not measured
    here - the caller can measure them separately if needed.
    """
    findings: list[AudioFinding] = []
    targets = intent.targets
    mix_exists = os.path.isfile(mix_path)

    if targets.integrated_lufs is not None:
        if not mix_exists:
            findings.append(AudioFinding(
                name="lufs", passed=False, measured=None,
                target=targets.integrated_lufs,
                detail=f"the mix file is not on disk: {mix_path}"))
        else:
            tolerance = (targets.lufs_tolerance
                         if targets.lufs_tolerance is not None
                         else DEFAULT_LUFS_TOLERANCE)
            result = measure_lufs(mix_path, targets.integrated_lufs,
                                  tolerance, DEFAULT_TRUE_PEAK_CEILING_DBTP)
            measured_lufs = None
            if isinstance(result.value, dict):
                measured_lufs = result.value.get("input_i")
            lufs_passed = (measured_lufs is not None
                           and abs(measured_lufs - targets.integrated_lufs)
                           <= tolerance)
            findings.append(AudioFinding(
                name="lufs", passed=lufs_passed, measured=measured_lufs,
                target=targets.integrated_lufs,
                detail=f"integrated {measured_lufs:.2f} LUFS vs target "
                       f"{targets.integrated_lufs:.1f} "
                       f"(+/- {tolerance:.1f})"))

    if targets.true_peak_ceiling_dbTP is not None:
        if not mix_exists:
            findings.append(AudioFinding(
                name="true_peak", passed=False, measured=None,
                target=targets.true_peak_ceiling_dbTP,
                detail=f"the mix file is not on disk: {mix_path}"))
        else:
            result = measure_lufs(mix_path, DEFAULT_LUFS_TARGET,
                                  DEFAULT_LUFS_TOLERANCE,
                                  targets.true_peak_ceiling_dbTP)
            measured_tp = None
            if isinstance(result.value, dict):
                measured_tp = result.value.get("input_tp")
            peak_passed = (measured_tp is not None
                           and measured_tp <= targets.true_peak_ceiling_dbTP)
            findings.append(AudioFinding(
                name="true_peak", passed=peak_passed, measured=measured_tp,
                target=targets.true_peak_ceiling_dbTP,
                detail=f"true peak {measured_tp:.2f} dBTP vs ceiling "
                       f"{targets.true_peak_ceiling_dbTP:.1f} dBTP"))

    if targets.separation_db is not None:
        separation = _measure_separation(
            mix_path, speech_stem, bed_stem,
            music_path, music_automation, music_offset_seconds)
        if separation is None:
            findings.append(AudioFinding(
                name="separation", passed=False, measured=None,
                target=targets.separation_db,
                detail="speech/bed separation could not be measured - "
                       "provide stems or music_automation"))
        else:
            passed = separation >= targets.separation_db
            findings.append(AudioFinding(
                name="separation", passed=passed, measured=separation,
                target=targets.separation_db,
                detail=f"speech sits {separation:.1f} dB above the bed "
                       f"(target {targets.separation_db:.1f} dB)"))

    if targets.spectral_band_targets_db is not None:
        if not mix_exists:
            findings.append(AudioFinding(
                name="spectral_balance", passed=False, measured=None,
                target=targets.spectral_band_targets_db,
                detail=f"the mix file is not on disk: {mix_path}"))
        else:
            findings.append(_check_spectral_balance(
                mix_path, targets.spectral_band_targets_db,
                targets.spectral_tolerance_db, sample_rate))

    if targets.max_silence_seconds is not None:
        if not mix_exists:
            findings.append(AudioFinding(
                name="silence", passed=False, measured=None,
                target=targets.max_silence_seconds,
                detail=f"the mix file is not on disk: {mix_path}"))
        else:
            findings.append(_check_silence(
                mix_path, targets.max_silence_seconds, sample_rate))

    return findings


def _measure_separation(
    mix_path: str,
    speech_stem: Optional[str],
    bed_stem: Optional[str],
    music_path: Optional[str],
    music_automation: Optional[Sequence[dict]],
    music_offset_seconds: Optional[float],
) -> Optional[float]:
    if speech_stem and bed_stem:
        speech_lufs = _measure_stem_lufs(speech_stem)
        bed_lufs = _measure_stem_lufs(bed_stem)
        return separation_delivered_db(speech_lufs, bed_lufs)
    if music_path and music_automation and music_offset_seconds is not None:
        from library.tools.render_qa import measure_speech_above_bed
        result = measure_speech_above_bed(
            mix_path, music_path, music_automation, music_offset_seconds)
        value = result.value
        if isinstance(value, dict) and "windows" in value:
            margins = [w.get("margin_db") for w in value["windows"]
                       if w.get("margin_db") is not None]
            if margins:
                return min(margins)
        return None
    return None


def _check_spectral_balance(
    mix_path: str,
    band_targets: list,
    tolerance: Optional[float],
    sample_rate: int,
) -> AudioFinding:
    measured_bands = _spectral_balance(mix_path, sample_rate)
    if not measured_bands:
        return AudioFinding(
            name="spectral_balance", passed=False, measured=None,
            target=band_targets,
            detail="the mix could not be decoded for spectral measurement")
    measured_rounded = [round(b, 1) if b != float("-inf") else None
                        for b in measured_bands]
    if tolerance is None:
        return AudioFinding(
            name="spectral_balance", passed=None,
            measured=measured_rounded,
            target=band_targets,
            detail="no spectral tolerance declared - band measurements "
                   "reported without a pass/fail verdict")
    misses = []
    for i, (actual, target_db) in enumerate(
            zip(measured_bands, band_targets)):
        if actual == float("-inf"):
            misses.append(f"band {i} is silent (target {target_db:.1f} dB)")
        elif abs(actual - target_db) > tolerance:
            misses.append(
                f"band {i} measures {actual:.1f} dB "
                f"(target {target_db:.1f} +/- {tolerance:.1f} dB)")
    if misses:
        return AudioFinding(
            name="spectral_balance", passed=False,
            measured=measured_rounded,
            target=band_targets,
            detail="; ".join(misses))
    return AudioFinding(
        name="spectral_balance", passed=True,
        measured=measured_rounded,
        target=band_targets,
        detail=f"all {len(measured_bands)} bands within +/- "
               f"{tolerance:.1f} dB of target")


def _check_silence(
    mix_path: str,
    max_silence_seconds: float,
    sample_rate: int,
) -> AudioFinding:
    try:
        runs = find_digital_zero_runs(mix_path, sample_rate,
                                      min_seconds=0.01)
    except (OSError, subprocess.SubprocessError) as exc:
        return AudioFinding(
            name="silence", passed=False, measured=None,
            target=max_silence_seconds,
            detail=f"ffmpeg did not run: {exc}")
    longest = max((r[2] for r in runs), default=0.0)
    if longest > max_silence_seconds:
        return AudioFinding(
            name="silence", passed=False,
            measured=round(longest, 3),
            target=max_silence_seconds,
            detail=f"longest digital-zero span is {longest:.3f}s "
                   f"(max {max_silence_seconds:.3f}s)")
    return AudioFinding(
        name="silence", passed=True,
        measured=round(longest, 3),
        target=max_silence_seconds,
        detail=f"longest digital-zero span is {longest:.3f}s "
               f"(max {max_silence_seconds:.3f}s)")
