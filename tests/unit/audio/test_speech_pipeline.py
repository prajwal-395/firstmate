import sys
import json
import os
import pytest
from unittest.mock import MagicMock, patch
import numpy as np


# Now import the module to test
from library.tools.analysis.speech_advanced_pipeline import (
    analyze_prosody,
    analyze_speech_advanced
)

@pytest.fixture
def mock_sound():
    sound = MagicMock()
    sound.duration = 2.0
    return sound

@pytest.fixture
def sample_speech_regions():
    return [
        {"start": 0.0, "end": 1.0, "words": [{"word": "hello"}, {"word": "world"}]},
        {"start": 1.5, "end": 2.5, "words": [{"word": "test"}, {"word": "test"}]}
    ]

def _install_mock_parselmouth(monkeypatch, duration, pitch_by_time=None,
                             intensity_by_time=None):
    """A parselmouth stub whose pitch and intensity are functions of time.

    The pitch and intensity objects are distinct mocks, so a
    "Get value at time" call is answered by the function for the object
    it was asked on - the real analyzer asks both.
    """
    pitch_obj = MagicMock(name="pitch")
    intensity_obj = MagicMock(name="intensity")

    def side_effect(obj, action, *args, **kwargs):
        if action == "To Pitch":
            return pitch_obj
        if action == "To Intensity":
            return intensity_obj
        if action == "Get value at time":
            t = args[0]
            if obj is pitch_obj:
                return pitch_by_time(t) if pitch_by_time else float("nan")
            return intensity_by_time(t) if intensity_by_time else float("nan")
        if action in ("Get jitter (local)", "Get shimmer (local)", "Get mean"):
            return float("nan")
        return MagicMock()

    mock_praat = MagicMock()
    mock_praat.call.side_effect = side_effect
    mock_parselmouth = MagicMock()
    mock_parselmouth.praat = mock_praat
    mock_s = MagicMock()
    mock_s.duration = duration
    mock_parselmouth.Sound.return_value = mock_s
    monkeypatch.setitem(sys.modules, "parselmouth", mock_parselmouth)
    monkeypatch.setitem(sys.modules, "parselmouth.praat", mock_praat)


def test_analyze_prosody_no_parselmouth(monkeypatch):
    """A missing parselmouth raises; it does not return a soft error.

    This used to assert the soft return `{"method": None, "error":
    "parselmouth not installed"}` - and that dict was then written to
    `<clip>_prosody.json` like any other result, so step 1.05 counted
    seventeen files as seventeen profiles and reported success in 0.1s.
    See tests/unit/audio/test_prosody.py.
    """
    from library.tools.analysis.speech_advanced_pipeline import (
        ProsodyUnavailable,
    )
    # Set to None to guarantee ImportError
    monkeypatch.setitem(sys.modules, 'parselmouth', None)

    with pytest.raises(ProsodyUnavailable) as excinfo:
        analyze_prosody("dummy.wav")
    assert "praat-parselmouth" in str(excinfo.value)

def test_speaking_rate_calculation(sample_speech_regions):
    """Test speaking rate extraction from speech regions."""
    def side_effect(obj, action, *args, **kwargs):
        if action == "Get value at time":
            return float('nan')
        if action in ["Get jitter (local)", "Get shimmer (local)", "Get mean"]:
            return float('nan')
        return MagicMock()

    mock_praat = MagicMock()
    mock_praat.call.side_effect = side_effect
    
    mock_parselmouth = MagicMock()
    mock_parselmouth.praat = mock_praat
    
    mock_s = MagicMock()
    mock_s.duration = 0.02
    mock_parselmouth.Sound.return_value = mock_s
    
    with patch.dict(sys.modules, {'parselmouth': mock_parselmouth, 'parselmouth.praat': mock_praat}):
        result = analyze_prosody("dummy.wav", sample_speech_regions)
    
    assert "speaking_rate" in result
    sr = result["speaking_rate"]
    assert sr["total_words"] == 4
    assert sr["total_speech_seconds"] == 2.0
    assert sr["words_per_minute"] == 120.0

def test_voice_quality_reports_hnr_without_register_label():
    """No HNR value produces a vocal-register word (issue #263).

    The old hnr > 20 / > 10 ladder read "breathy" on 16 of 17 clips of
    project 001 - outdoor iPhone audio where HNR measures ambient
    noise, not the voice. Every measured HNR below is one of those
    recorded values (clip_017's 15.6 included); each must come back as
    a number with no label beside it.
    """
    # Measured HNR across project 001, from the issue.
    issue_hnr_values = [
        -1.4, 0.7, 8.0, 2.3, 0.2, 3.7, 4.5, -0.5, 5.4, 4.4, 6.3,
        6.6, 8.1, 6.3, 6.1, 7.7, 15.6,
    ]

    def create_mock_call(hnr_value):
        def side_effect(obj, action, *args, **kwargs):
            if action == "Get mean":
                return hnr_value
            elif action == "Get value at time":
                return float('nan')
            elif action == "Get jitter (local)":
                return 0.005
            elif action == "Get shimmer (local)":
                return 0.02
            return MagicMock()
        return side_effect

    mock_praat = MagicMock()
    mock_parselmouth = MagicMock()
    mock_parselmouth.praat = mock_praat

    mock_s = MagicMock()
    mock_s.duration = 0.02
    mock_parselmouth.Sound.return_value = mock_s

    with patch.dict(sys.modules, {'parselmouth': mock_parselmouth, 'parselmouth.praat': mock_praat}):
        for hnr_value in issue_hnr_values:
            mock_praat.call.side_effect = create_mock_call(hnr_value)
            res = analyze_prosody("dummy.wav")
            vq = res["voice_quality"]
            assert "quality_assessment" not in vq, (
                f"HNR {hnr_value} dB produced a register label: {vq!r}")
            assert vq["hnr_db"] == round(hnr_value, 1)

        # A measured 0.0 dB is a number, not an absence.
        mock_praat.call.side_effect = create_mock_call(0.0)
        res_zero = analyze_prosody("dummy.wav")
        assert res_zero["voice_quality"]["hnr_db"] == 0.0
        assert "quality_assessment" not in res_zero["voice_quality"]

        # Unmeasured HNR reads as None, still with no label.
        for unmeasured in (None, float("nan")):
            mock_praat.call.side_effect = create_mock_call(unmeasured)
            res = analyze_prosody("dummy.wav")
            assert res["voice_quality"]["hnr_db"] is None
            assert "quality_assessment" not in res["voice_quality"]


# ── The contours cover the whole clip ────────────────────────────────
#
# The saved contours were truncated - pitch at 30 s, intensity at 60 s -
# so every passage past the caps read as unvoiced against them. Over
# 001's eleven chosen passages the pitch contour covered 22.1% and the
# intensity contour 51.7% of the chosen speech; body3 @ 63.1 s, body4 @
# 100.5 s and body10 @ 119.2 s on clip_011 were outside both caps
# (docs/PROSODY_MEASURED.md section 2a). The measurement loops always
# ran to the clip's end; the caps only truncated the saved copy.


def test_saved_contours_cover_the_full_clip_past_the_old_caps(monkeypatch):
    """A 65 s clip saves a pitch contour past 30 s and an intensity
    contour past 60 s, reaching the clip's end - the caps are gone."""
    _install_mock_parselmouth(
        monkeypatch, 65.0,
        pitch_by_time=lambda t: 100.0,
        intensity_by_time=lambda t: 60.0)

    result = analyze_prosody("dummy.wav")

    pitch_contour = result["pitch_contour_10ms"]
    # Past the old 30 s cap (3000 samples) and reaching the clip's end.
    assert len(pitch_contour) > 3000
    assert pitch_contour[-1]["time"] >= 64.99
    assert all(row["f0_hz"] == 100.0 for row in pitch_contour)
    intensity_contour = result["intensity_contour_50ms"]
    # Past the old 60 s cap (1200 samples) and reaching the clip's end.
    assert len(intensity_contour) > 1200
    assert intensity_contour[-1]["time"] >= 64.95
    assert all(row["db"] == 60.0 for row in intensity_contour)


# ── The aggregates are per passage ───────────────────────────────────
#
# pitch_stats and speaking_rate were whole-clip numbers, so two
# passages with different delivery produced one number for both and
# "which of two restatements does he land better" was unanswerable
# (docs/PROSODY_MEASURED.md section 2b). Each passage row now carries
# its own aggregates, measured over its own span.


def test_aggregates_are_per_passage_not_per_clip(monkeypatch):
    """Two passages at 100 Hz/120 WPM and 200 Hz/240 WPM produce two
    different per-passage rows; the clip-level aggregate is neither."""
    _install_mock_parselmouth(
        monkeypatch, 3.0,
        pitch_by_time=lambda t: 100.0 if t < 1.0 else 200.0,
        intensity_by_time=lambda t: 60.0)
    regions = [
        {"start": 0.0, "end": 1.0,
         "words": [{"word": "a"}, {"word": "b"}]},
        {"start": 2.0, "end": 3.0,
         "words": [{"word": "c"}, {"word": "d"}, {"word": "e"},
                   {"word": "f"}]},
    ]

    result = analyze_prosody("dummy.wav", regions)

    passages = result["passage_prosody"]
    assert len(passages) == 2
    assert passages[0]["pitch_stats"]["mean_f0_hz"] == 100.0
    assert passages[0]["pitch_stats"]["median_f0_hz"] == 100.0
    assert passages[0]["pitch_stats"]["voicing_percentage"] == 100.0
    assert passages[1]["pitch_stats"]["mean_f0_hz"] == 200.0
    assert passages[1]["pitch_stats"]["voicing_percentage"] == 100.0
    assert passages[0]["speaking_rate"]["words_per_minute"] == 120.0
    assert passages[0]["speaking_rate"]["total_words"] == 2
    assert passages[1]["speaking_rate"]["words_per_minute"] == 240.0
    assert passages[1]["speaking_rate"]["total_words"] == 4
    assert passages[0]["median_intensity_db"] == 60.0
    assert passages[1]["median_intensity_db"] == 60.0

    # The clip-level row is the whole-clip aggregate: neither passage's
    # number, and a third thing - which is why it could not answer a
    # per-passage question.
    assert 100.0 < result["pitch_stats"]["mean_f0_hz"] < 200.0
    assert result["speaking_rate"]["words_per_minute"] == 180.0


def test_passage_prosody_empty_when_no_speech_regions(monkeypatch):
    """A clip with no speech regions yields an empty per-passage list,
    not a missing one - the same rule the per-word table follows."""
    _install_mock_parselmouth(monkeypatch, 0.02)

    result = analyze_prosody("dummy.wav")

    assert result["passage_prosody"] == []
