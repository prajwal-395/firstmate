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

def test_analyze_prosody_no_parselmouth(monkeypatch):
    """A missing parselmouth raises; it does not return a soft error.

    This used to assert the soft return `{"method": None, "error":
    "parselmouth not installed"}` - and that dict was then written to
    `<clip>_prosody.json` like any other result, so step 1.05 counted
    seventeen files as seventeen profiles and reported success in 0.1s.
    See tests/test_prosody_failure_is_loud.py.
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
