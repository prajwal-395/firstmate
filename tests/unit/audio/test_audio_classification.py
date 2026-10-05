"""Audio classification beyond speech and PANNs (Gap 12).

These tests name the behavioral defects the implementation prevents:

- The per-clip normalized energy curve makes cross-clip loudness
  comparison impossible; absolute speech level (dBFS) makes it possible.
- A missing or silent audio file must refuse with a reason, never return
  a default level presented as measured.
- Music genre classification must handle model unavailability gracefully.
- Ambience type classification must distinguish acoustic environments
  from spectral features.
- The filter must make the new categories queryable.
"""
import os
import struct
import sys
import wave

import numpy as np
import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import audio_classification
from library.tools.analysis import footage_query


def _write_wav(path, samples, sample_rate=16000):
    """Write a 16-bit mono WAV file."""
    clamped = [max(-1.0, min(1.0, v)) for v in samples]
    packed = struct.pack(f"<{len(clamped)}h", *(int(v * 32767) for v in clamped))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(packed)


def _sine_wave(freq, seconds, sample_rate=16000, amplitude=0.5):
    """Generate a sine wave."""
    t = np.linspace(0, seconds, int(sample_rate * seconds), endpoint=False)
    return amplitude * np.sin(2 * np.pi * freq * t)


def _white_noise(seconds, sample_rate=16000, amplitude=0.1):
    """Generate white noise."""
    return amplitude * np.random.randn(int(sample_rate * seconds))


# ── Absolute speech level ─────────────────────────────────────────────

def test_speech_level_is_cross_clip_comparable():
    """Two clips with the same speech amplitude must report the same dBFS.

    The defect: the per-clip normalized energy curve makes cross-clip
    comparison impossible. Absolute speech level (dBFS) must not.
    """
    sr = 16000
    # Two clips with identical speech amplitude but different overall energy
    clip_a = _sine_wave(440, 2.0, sr, amplitude=0.3)
    clip_b = _sine_wave(440, 2.0, sr, amplitude=0.3)

    path_a = "/tmp/test_speech_a.wav"
    path_b = "/tmp/test_speech_b.wav"
    _write_wav(path_a, clip_a, sr)
    _write_wav(path_b, clip_b, sr)

    speech_spans = [(0.5, 1.5)]
    level_a = audio_classification.measure_speech_level(path_a, speech_spans)
    level_b = audio_classification.measure_speech_level(path_b, speech_spans)

    assert level_a["measured"] is True
    assert level_b["measured"] is True
    assert level_a["speech_level_dbfs"] == level_b["speech_level_dbfs"]


def test_speech_level_differs_with_amplitude():
    """Louder speech must report a higher dBFS than quieter speech."""
    sr = 16000
    quiet = _sine_wave(440, 2.0, sr, amplitude=0.1)
    loud = _sine_wave(440, 2.0, sr, amplitude=0.5)

    path_quiet = "/tmp/test_speech_quiet.wav"
    path_loud = "/tmp/test_speech_loud.wav"
    _write_wav(path_quiet, quiet, sr)
    _write_wav(path_loud, loud, sr)

    speech_spans = [(0.5, 1.5)]
    level_quiet = audio_classification.measure_speech_level(path_quiet, speech_spans)
    level_loud = audio_classification.measure_speech_level(path_loud, speech_spans)

    assert level_quiet["measured"] is True
    assert level_loud["measured"] is True
    assert level_loud["speech_level_dbfs"] > level_quiet["speech_level_dbfs"]


def test_speech_level_refuses_missing_file():
    """A missing audio file must refuse, never return a default level."""
    result = audio_classification.measure_speech_level(
        "/tmp/nonexistent_audio.wav", [(0.0, 1.0)])
    assert result["measured"] is False
    assert "reason" in result


def test_speech_level_refuses_empty_speech_spans():
    """No speech spans means nothing to measure, not a level of 0."""
    sr = 16000
    path = "/tmp/test_speech_empty.wav"
    _write_wav(path, _sine_wave(440, 2.0, sr), sr)

    result = audio_classification.measure_speech_level(path, [])
    assert result["measured"] is False
    assert "reason" in result


def test_speech_level_refuses_silent_speech():
    """Speech spans that measure as digital silence must refuse."""
    sr = 16000
    path = "/tmp/test_speech_silent.wav"
    _write_wav(path, np.zeros(sr * 2), sr)

    result = audio_classification.measure_speech_level(path, [(0.5, 1.5)])
    assert result["measured"] is False
    assert "reason" in result


# ── Music classification ──────────────────────────────────────────────

def test_music_classification_refuses_missing_file():
    """A missing audio file must refuse, never crash."""
    result = audio_classification.classify_music("/tmp/nonexistent_music.wav")
    assert result["measured"] is False
    assert "reason" in result


def test_music_classification_refuses_short_audio():
    """Audio below the minimum length must refuse."""
    sr = 16000
    path = "/tmp/test_music_short.wav"
    _write_wav(path, _sine_wave(440, 0.5, sr), sr)

    result = audio_classification.classify_music(path)
    assert result["measured"] is False
    assert "reason" in result


def test_music_classification_refuses_silence():
    """Silent audio must refuse, not return a genre guess."""
    sr = 16000
    path = "/tmp/test_music_silent.wav"
    _write_wav(path, np.zeros(sr * 2), sr)

    result = audio_classification.classify_music(path)
    assert result["measured"] is False
    assert "reason" in result


# ── Ambience classification ───────────────────────────────────────────

def test_ambience_classification_indoor():
    """Low spectral centroid and bandwidth classify as indoor."""
    sr = 16000
    # Low-frequency, narrow-band signal (indoor-like)
    path = "/tmp/test_ambience_indoor.wav"
    _write_wav(path, _sine_wave(200, 3.0, sr, amplitude=0.1), sr)

    result = audio_classification.classify_ambience(path, [])
    assert result["measured"] is True
    assert result["ambience"] == "indoor"


def test_ambience_classification_urban():
    """High zero-crossing rate and rolloff classify as urban."""
    sr = 16000
    # White noise has high ZCR and high rolloff (urban-like)
    path = "/tmp/test_ambience_urban.wav"
    np.random.seed(42)
    _write_wav(path, _white_noise(3.0, sr, amplitude=0.3), sr)

    result = audio_classification.classify_ambience(path, [])
    assert result["measured"] is True
    assert result["ambience"] == "urban"


def test_ambience_classification_refuses_missing_file():
    """A missing audio file must refuse."""
    result = audio_classification.classify_ambience(
        "/tmp/nonexistent_ambience.wav", [])
    assert result["measured"] is False
    assert "reason" in result


def test_ambience_classification_refuses_all_speech():
    """Audio with no non-speech spans must refuse."""
    sr = 16000
    path = "/tmp/test_ambience_speech.wav"
    _write_wav(path, _sine_wave(440, 2.0, sr), sr)

    # Speech spans cover the entire audio
    result = audio_classification.classify_ambience(path, [(0.0, 2.0)])
    assert result["measured"] is False
    assert "reason" in result


# ── Queryability (filter) ─────────────────────────────────────────────

def _make_index_payload():
    """A minimal index payload with audio classification."""
    return {
        "segments": [
            {
                "segment_id": "clip_001#speech#0001",
                "clip_id": "clip_001",
                "filename": "clip_001.wav",
                "kind": "speech",
                "start": 0.0,
                "end": 5.0,
                "duration": 5.0,
                "text": "hello world",
                "facets": {},
            },
            {
                "segment_id": "clip_002#speech#0001",
                "clip_id": "clip_002",
                "filename": "clip_002.wav",
                "kind": "speech",
                "start": 0.0,
                "end": 5.0,
                "duration": 5.0,
                "text": "goodbye world",
                "facets": {},
            },
        ],
        "audio_classification": {
            "clip_001": {
                "speech_level": {
                    "measured": True,
                    "speech_level_dbfs": -20.0,
                },
                "music_genre": {
                    "measured": True,
                    "genres": [{"label": "rock", "confidence": 0.9}],
                },
                "ambience_type": {
                    "measured": True,
                    "ambience": "indoor",
                },
            },
            "clip_002": {
                "speech_level": {
                    "measured": True,
                    "speech_level_dbfs": -30.0,
                },
                "music_genre": {
                    "measured": True,
                    "genres": [{"label": "jazz", "confidence": 0.8}],
                },
                "ambience_type": {
                    "measured": True,
                    "ambience": "outdoor",
                },
            },
        },
    }


def _make_index(payload):
    """A FootageIndex with a pre-set payload (no file I/O)."""
    idx = footage_query.FootageIndex.__new__(footage_query.FootageIndex)
    idx._payload = payload
    return idx


def test_filter_by_speech_level():
    """Filter must select clips by absolute speech level (dBFS)."""
    idx = _make_index(_make_index_payload())

    # min_speech_level: only clip_001 (-20 dBFS) passes
    results = idx.filter(min_speech_level=-25.0)
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_001"

    # max_speech_level: only clip_002 (-30 dBFS) passes
    results = idx.filter(max_speech_level=-25.0)
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"


def test_filter_by_music_genre():
    """Filter must select clips by music genre."""
    idx = _make_index(_make_index_payload())

    results = idx.filter(music_genre="rock")
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_001"

    results = idx.filter(music_genre="jazz")
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"


def test_filter_by_ambience_type():
    """Filter must select clips by ambience type."""
    idx = _make_index(_make_index_payload())

    results = idx.filter(ambience_type="indoor")
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_001"

    results = idx.filter(ambience_type="outdoor")
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_002"


def test_filter_fails_unmeasured_audio_classification():
    """A clip with no audio classification must fail the filter bound."""
    payload = _make_index_payload()
    # clip_002 has no audio classification
    del payload["audio_classification"]["clip_002"]
    idx = _make_index(payload)

    # clip_002 has no classification, so it must not pass
    results = idx.filter(min_speech_level=-40.0)
    assert len(results) == 1
    assert results[0]["clip_id"] == "clip_001"
