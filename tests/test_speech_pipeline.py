import sys
import json
import os
import pytest
from unittest.mock import MagicMock, patch
import numpy as np

# Mock heavy dependencies BEFORE importing the module
sys.modules['transformers'] = MagicMock()
sys.modules['speechbrain'] = MagicMock()
sys.modules['torchaudio'] = MagicMock()

# Mock parselmouth
mock_parselmouth = MagicMock()
mock_praat = MagicMock()
mock_parselmouth.praat = mock_praat
sys.modules['parselmouth'] = mock_parselmouth
sys.modules['parselmouth.praat'] = mock_praat

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
    """Test error handling when parselmouth is not installed."""
    # Set to None to guarantee ImportError
    monkeypatch.setitem(sys.modules, 'parselmouth', None)
    
    result = analyze_prosody("dummy.wav")
    assert result["method"] is None
    assert result["error"] == "parselmouth not installed"

def test_analyze_prosody_success(sample_speech_regions):
    """Test successful prosody analysis with mocked parselmouth."""
    def side_effect(obj, action, *args, **kwargs):
        if action == "To Pitch":
            return MagicMock()
        elif action == "Get value at time":
            if len(args) > 1 and args[1] == "Hertz":
                return 150.0
            elif len(args) > 1 and args[1] == "cubic":
                return 60.0
            return 0.0
        elif action == "To PointProcess (periodic, cc)":
            return MagicMock()
        elif action == "Get jitter (local)":
            return 0.005
        elif action == "Get shimmer (local)":
            return 0.02
        elif action == "To Harmonicity (cc)":
            return MagicMock()
        elif action == "Get mean":
            return 25.0
        elif action == "To Intensity":
            return MagicMock()
        return MagicMock()

    mock_praat = MagicMock()
    mock_praat.call.side_effect = side_effect
    
    mock_parselmouth = MagicMock()
    mock_parselmouth.praat = mock_praat
    
    mock_s = MagicMock()
    mock_s.duration = 0.05
    mock_parselmouth.Sound.return_value = mock_s
    
    with patch.dict(sys.modules, {'parselmouth': mock_parselmouth, 'parselmouth.praat': mock_praat}):
        result = analyze_prosody("dummy.wav", sample_speech_regions)
    
    assert result["method"] == "parselmouth-praat"
    assert "pitch_stats" in result
    assert result["pitch_stats"]["mean_f0_hz"] == 150.0
    
    assert "voice_quality" in result
    assert result["voice_quality"]["hnr_db"] == 25.0
    assert result["voice_quality"]["quality_assessment"] == "clear"

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

def test_analyze_speech_advanced_file_output(tmp_path):
    """Test that analyze_speech_advanced writes the correct JSON output."""
    output_dir = tmp_path / "output"
    audio_path = "test_audio.wav"
    
    with patch('library.tools.analysis.speech_advanced_pipeline.analyze_prosody') as mock_analyze:
        mock_analyze.return_value = {"method": "mocked"}
        
        result = analyze_speech_advanced(
            audio_path=audio_path,
            output_dir=str(output_dir),
            clip_id="test_clip"
        )
        
        assert result["clip_id"] == "test_clip"
        assert result["audio_file"] == audio_path
        assert result["prosody"] == {"method": "mocked"}
        
        output_file = output_dir / "test_clip_prosody.json"
        assert output_file.exists()
        
        with open(output_file, "r") as f:
            data = json.load(f)
            assert data["clip_id"] == "test_clip"
            assert "analysis_time_s" in data

def test_voice_quality_categories():
    """Test the categorization logic for voice quality."""
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
        # Test "clear" (> 20)
        mock_praat.call.side_effect = create_mock_call(25.0)
        res1 = analyze_prosody("dummy.wav")
        assert res1["voice_quality"]["quality_assessment"] == "clear"
        
        # Test "slightly_breathy" (10-20)
        mock_praat.call.side_effect = create_mock_call(15.0)
        res2 = analyze_prosody("dummy.wav")
        assert res2["voice_quality"]["quality_assessment"] == "slightly_breathy"
        
        # Test "breathy" (< 10)
        mock_praat.call.side_effect = create_mock_call(5.0)
        res3 = analyze_prosody("dummy.wav")
        assert res3["voice_quality"]["quality_assessment"] == "breathy"
