"""FI-12 acceptance: calibrated synthetic audio produces stable measurements."""
from __future__ import annotations

import wave

import numpy as np
import pytest

from library.tools import audio_quality, source_memory

RATE = 48_000


def _write_stereo_fixture(path):
    seconds = np.arange(RATE * 2, dtype=np.float64) / RATE
    left = np.zeros(RATE * 2, dtype=np.float64)
    right = np.zeros(RATE * 2, dtype=np.float64)
    left[:RATE] = 0.25 * np.sin(2 * np.pi * 440 * seconds[:RATE])
    right[:RATE] = 0.50 * np.sin(2 * np.pi * 220 * seconds[:RATE])
    # Deterministic room-noise segment on channel two, at a known amplitude.
    rng = np.random.default_rng(12012)
    right[RATE:] = rng.uniform(-0.01, 0.01, RATE)
    interleaved = np.column_stack([left, right])
    pcm = np.rint(interleaved * 32768).clip(-32768, 32767).astype("<i2")
    pcm[RATE * 2 - 100, 0] = 32767
    pcm[RATE * 2 - 99, 0] = -32768
    pcm[RATE * 2 - 98, 0] = -32767
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())


def test_known_signal_fixture_measures_units_clipping_noise_and_silence(
        tmp_path):
    source = tmp_path / "fi12-known-signals.wav"
    memory_root = tmp_path / "memory"
    _write_stereo_fixture(source)

    result = audio_quality.measure_audio_quality(str(source), root=memory_root)

    assert result["channel_count"] == 2
    left, right = result["channels"]
    assert left["integrated_lufs"] == pytest.approx(-15.5, abs=1.5)
    assert right["integrated_lufs"] == pytest.approx(-9.5, abs=1.5)
    assert right["true_peak_dbtp"] == pytest.approx(-6.0, abs=1.5)
    assert left["clipping"]["clipped_sample_count"] == 3
    assert left["clipping"]["sample_count"] == RATE * 2
    assert left["clipping"]["clipped_fraction"] == pytest.approx(3 / (RATE * 2))
    assert left["intervals"][0]["spectral_centroid_hz"] == pytest.approx(440, abs=2)
    assert right["intervals"][0]["spectral_centroid_hz"] == pytest.approx(220, abs=2)
    assert right["noise_floor_dbfs"] == pytest.approx(-44.7, abs=2.0)
    assert right["snr_db"] == pytest.approx(35.7, abs=3.0)
    assert left["intervals"][0]["tonal_peak_hz"] == pytest.approx(440, abs=2)
    assert left["intervals"][0]["speech_band_energy_fraction"] == pytest.approx(1.0, abs=0.02)
    assert left["intelligibility_proxies"]["interpretation"].startswith("frequency coverage proxy")
    assert left["silence_intervals"]
    assert left["silence_intervals"][0]["start_seconds"] == pytest.approx(1.0)
    assert result["method"].startswith("ffmpeg-pcm-s16le/48kHz")

    # The source memory slot persists once, by content identity, and returns
    # the same measurement rather than silently recomputing it.
    stored = source_memory.read_audio_quality(result["content_digest"], memory_root)
    assert {key: value for key, value in stored.items()
            if key != "evidence_envelope"} == result
    source_memory.validate_evidence_envelope(stored["evidence_envelope"])
    reused = audio_quality.measure_audio_quality(str(source), root=memory_root)
    assert reused == result

    # A digest match alone cannot return a pre-v2 record that lacks the new
    # interval proxies; method-version mismatch forces one fresh measurement.
    stored["method"] = audio_quality.METHOD.replace("FI-12-v2", "FI-12-v1")
    source_memory.write_audio_quality(result["content_digest"], stored, memory_root)
    upgraded = audio_quality.measure_audio_quality(str(source), root=memory_root)
    assert upgraded["method"] == audio_quality.METHOD
    assert "tonal_peak_hz" in upgraded["channels"][0]["intervals"][0]


def test_audio_quality_slot_refuses_digest_mismatch(tmp_path):
    with pytest.raises(ValueError, match="digest does not match"):
        source_memory.write_audio_quality("expected", {"content_digest": "other"},
                                          tmp_path)
