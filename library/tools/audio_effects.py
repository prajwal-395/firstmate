"""Plan-declared dialogue effects delivered as processed audio stems.

Resolve's scripting API does not expose an EQ/de-esser chain that survives a
timeline rebuild.  These effects therefore run on the exact played speech
range and the resulting stem is placed through the same OTIO path as the
existing dialogue-cleanup stems.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
from typing import Any

import numpy as np
from scipy.io import wavfile
from scipy.signal import istft, stft


class AudioOperationError(ValueError):
    """A requested audio operation cannot be planned or delivered."""


OPERATION_KEYS = {
    "high_pass": frozenset({"type", "frequency_hz", "why"}),
    "equalizer": frozenset({"type", "frequency_hz", "gain_db", "q", "why"}),
    "de_ess": frozenset({
        "type", "frequency_hz", "reduction_db", "threshold_dbfs",
        "q", "attack_ms", "release_ms", "why",
    }),
    "dereverb": frozenset({"type", "strength", "why"}),
}


def validate_operations(operations: Any) -> list[dict]:
    """Return checked operations, refusing unknown or unread plan fields."""
    if not isinstance(operations, list):
        raise AudioOperationError("audio operations must be a list")
    checked = []
    for index, raw in enumerate(operations):
        label = f"audio operation {index}"
        if not isinstance(raw, dict):
            raise AudioOperationError(f"{label} is not an object")
        kind = raw.get("type")
        if not isinstance(kind, str) or kind not in OPERATION_KEYS:
            raise AudioOperationError(
                f"{label} names unknown type {kind!r}; choose one of "
                f"{sorted(OPERATION_KEYS)}")
        extra = set(raw) - OPERATION_KEYS[kind]
        missing = OPERATION_KEYS[kind] - set(raw)
        if extra or missing:
            raise AudioOperationError(
                f"{label} ({kind}) has unread keys {sorted(extra)} or is "
                f"missing required keys {sorted(missing)}")
        why = raw.get("why")
        if not isinstance(why, str) or not why.strip():
            raise AudioOperationError(f"{label} ({kind}) needs a reason")
        op = dict(raw)
        if kind == "high_pass":
            op["frequency_hz"] = _number(
                op["frequency_hz"], f"{label} frequency", 20, 1000)
        elif kind == "equalizer":
            op["frequency_hz"] = _number(
                op["frequency_hz"], f"{label} frequency", 20, 20000)
            op["gain_db"] = _number(op["gain_db"], f"{label} gain", -24, 24)
            op["q"] = _number(op["q"], f"{label} Q", 0.1, 12)
        elif kind == "de_ess":
            op["frequency_hz"] = _number(
                op["frequency_hz"], f"{label} frequency", 2000, 12000)
            op["reduction_db"] = _number(
                op["reduction_db"], f"{label} reduction", 0.1, 18)
            op["threshold_dbfs"] = _number(
                op["threshold_dbfs"], f"{label} threshold", -72, 0)
            op["q"] = _number(op["q"], f"{label} Q", 0.2, 12)
            op["attack_ms"] = _number(
                op["attack_ms"], f"{label} attack", 0.01, 2000)
            op["release_ms"] = _number(
                op["release_ms"], f"{label} release", 0.01, 2000)
        else:
            op["strength"] = _number(
                op["strength"], f"{label} strength", 0.05, 1.0)
        checked.append(op)
    return checked


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AudioOperationError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or not low <= number <= high:
        raise AudioOperationError(
            f"{label} {number!r} is outside {low:g}..{high:g}")
    return number


def ffmpeg_filter(operation: dict) -> str:
    """FFmpeg filter for a validated non-WPE operation."""
    op = validate_operations([operation])[0]
    kind = op["type"]
    if kind == "high_pass":
        return f"highpass=f={op['frequency_hz']:.6g}"
    if kind == "equalizer":
        return (f"equalizer=f={op['frequency_hz']:.6g}:"
                f"width_type=q:width={op['q']:.6g}:g={op['gain_db']:.6g}")
    if kind == "de_ess":
        threshold = 10 ** (op["threshold_dbfs"] / 20)
        return (
            "adynamicequalizer="
            f"threshold={threshold:.9g}:dfrequency={op['frequency_hz']:.6g}:"
            f"dqfactor={op['q']:.6g}:tfrequency={op['frequency_hz']:.6g}:"
            f"tqfactor={op['q']:.6g}:attack={op['attack_ms']:.6g}:"
            f"release={op['release_ms']:.6g}:ratio=1:"
            f"range={op['reduction_db']:.6g}:mode=cutabove:auto=disabled")
    raise AudioOperationError(f"{kind!r} is not an FFmpeg filter operation")


def apply_operations(input_path: str, output_path: str,
                     operations: Any) -> dict:
    """Apply a declared chain to a WAV, preserving the input as evidence."""
    plan = validate_operations(operations)
    if not os.path.isfile(input_path):
        raise AudioOperationError(f"audio input is missing: {input_path}")
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise AudioOperationError("audio operation output must differ from input")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    if not plan:
        shutil.copy2(input_path, output_path)
        return {"input_path": input_path, "output_path": output_path,
                "operations": [], "delivered": True}

    with tempfile.TemporaryDirectory(
            prefix="audio-ops-", dir=os.path.dirname(os.path.abspath(output_path))) as temp:
        current = input_path
        for index, op in enumerate(plan):
            final = index == len(plan) - 1
            next_path = output_path if final else os.path.join(temp, f"{index}.wav")
            if op["type"] == "dereverb":
                _apply_wpe(current, next_path, op["strength"])
            else:
                _run_filter(current, next_path, ffmpeg_filter(op))
            current = next_path
    return {"input_path": input_path, "output_path": output_path,
            "operations": plan, "delivered": True}


def _run_filter(input_path: str, output_path: str, audio_filter: str) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", input_path,
         "-af", audio_filter, "-c:a", "pcm_s24le", output_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, check=False,
    )
    if proc.returncode != 0 or not os.path.isfile(output_path):
        raise AudioOperationError(
            f"audio filter failed: {proc.stderr[-1200:]}")


def dereverberate_spectrum(spectrum: np.ndarray, *, strength: float,
                           taps: int = 10, delay: int = 3,
                           iterations: int = 3) -> np.ndarray:
    """Single-channel weighted-prediction-error late-reverb suppression.

    `spectrum` has shape frequency-by-frame. Strength scales the predicted
    late reverberant component, leaving the current frame's direct sound in
    place. This is the offline WPE formulation used for speech dereverberation.
    """
    if spectrum.ndim != 2:
        raise AudioOperationError("dereverb spectrum must be frequency x frame")
    if taps < 1 or delay < 1 or iterations < 1:
        raise AudioOperationError("dereverb taps, delay and iterations must be positive")
    result = np.array(spectrum, dtype=np.complex128, copy=True)
    for frequency in range(result.shape[0]):
        observed = result[frequency].copy()
        frames = observed.size
        delayed = np.zeros((taps, frames), dtype=np.complex128)
        for tap in range(taps):
            shift = delay + tap
            if shift < frames:
                delayed[tap, shift:] = observed[:-shift]
        estimate = observed.copy()
        for _ in range(iterations):
            power = np.maximum(np.abs(estimate) ** 2, 1e-10)
            weighted = delayed / power[None, :]
            covariance = weighted @ delayed.conj().T
            cross = weighted @ observed.conj()
            regularizer = max(float(np.trace(covariance).real) / taps, 1e-10) * 1e-6
            covariance.flat[::taps + 1] += regularizer
            try:
                prediction = np.conj(np.linalg.solve(covariance, cross)) @ delayed
            except np.linalg.LinAlgError:
                prediction = np.zeros_like(observed)
            estimate = observed - prediction
        result[frequency] = observed - strength * (observed - estimate)
    return result


def _apply_wpe(input_path: str, output_path: str, strength: float) -> None:
    sample_rate, raw = wavfile.read(input_path)
    if raw.ndim != 1:
        raise AudioOperationError("dereverb expects the mono stem made by the build")
    if sample_rate < 16000:
        raise AudioOperationError(
            f"dereverb needs at least 16 kHz audio, got {sample_rate}")
    if np.issubdtype(raw.dtype, np.integer):
        scale = float(max(abs(np.iinfo(raw.dtype).min), np.iinfo(raw.dtype).max))
        signal = raw.astype(np.float64) / scale
    else:
        signal = raw.astype(np.float64)
    nperseg = 512 if sample_rate >= 32000 else 256
    hop = nperseg // 4
    _, _, spectrum = stft(
        signal, fs=sample_rate, nperseg=nperseg, noverlap=nperseg - hop,
        boundary="zeros", padded=True)
    processed = dereverberate_spectrum(spectrum, strength=strength)
    _, result = istft(
        processed, fs=sample_rate, nperseg=nperseg,
        noverlap=nperseg - hop, input_onesided=True, boundary=True)
    result = result[:signal.size]
    peak = float(np.max(np.abs(result))) if result.size else 0.0
    if not np.isfinite(peak):
        raise AudioOperationError("dereverb produced non-finite samples")
    if peak > 1.0:
        result = result / peak
    wavfile.write(output_path, sample_rate, result.astype(np.float32))
