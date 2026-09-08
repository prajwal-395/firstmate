import pytest
import sys
from unittest.mock import MagicMock
import numpy as np

# Mock the entire libraries before importing the pipeline module
mock_madmom = MagicMock()
mock_librosa = MagicMock()
mock_essentia = MagicMock()
mock_essentia_standard = MagicMock()

# Wire them so that module attributes match sys.modules
mock_essentia.standard = mock_essentia_standard

MOCKED_MODULES = {
    'madmom': mock_madmom,
    'madmom.features': MagicMock(),
    'madmom.features.beats': MagicMock(),
    'madmom.features.downbeats': MagicMock(),
    'librosa': mock_librosa,
    'essentia': mock_essentia,
    'essentia.standard': mock_essentia_standard,
}

from library.tools.analysis.music_pipeline import analyze_tempo_beats, analyze_key

from unittest.mock import patch

@pytest.fixture(autouse=True)
def mock_sys_modules():
    with patch.dict(sys.modules, MOCKED_MODULES):
        yield


@pytest.fixture
def mock_madmom_setup():
    MOCKED_MODULES['madmom.features.beats'].RNNBeatProcessor = MagicMock()
    MOCKED_MODULES['madmom.features.beats'].DBNBeatTrackingProcessor = MagicMock()
    MOCKED_MODULES['madmom.features.downbeats'].RNNDownBeatProcessor = MagicMock()
    MOCKED_MODULES['madmom.features.downbeats'].DBNDownBeatTrackingProcessor = MagicMock()
    
    MOCKED_MODULES['madmom.features.beats'].DBNBeatTrackingProcessor.return_value.return_value = np.array([0.5, 1.0, 1.5, 2.0, 2.5])
    MOCKED_MODULES['madmom.features.downbeats'].DBNDownBeatTrackingProcessor.return_value.return_value = np.array([
        (0.5, 1), (1.0, 2), (1.5, 3), (2.0, 4), (2.5, 1)
    ])
    yield MOCKED_MODULES['madmom']

@pytest.fixture
def mock_librosa_setup():
    MOCKED_MODULES['librosa'].load = MagicMock(return_value=(np.array([0.0]*1000), 22050))
    MOCKED_MODULES['librosa'].beat.beat_track = MagicMock(return_value=(np.array([120.0]), np.array([0.5, 1.0, 1.5, 2.0, 2.5])))
    yield MOCKED_MODULES['librosa']

@pytest.fixture
def mock_essentia_setup():
    MOCKED_MODULES['essentia.standard'].MonoLoader = MagicMock()
    MOCKED_MODULES['essentia.standard'].MonoLoader.return_value.return_value = np.array([0.0]*1000)
    
    MOCKED_MODULES['essentia.standard'].KeyExtractor = MagicMock()
    MOCKED_MODULES['essentia.standard'].KeyExtractor.return_value.return_value = ("C", "major", 0.95)
    
    yield MOCKED_MODULES['essentia.standard']

def test_analyze_tempo_beats_madmom(mock_madmom_setup, mock_librosa_setup):
    """Test tempo extraction using madmom."""
    result = analyze_tempo_beats("dummy.wav")
    assert result["method"] == "madmom-rnn-dbn"
    assert result["bpm"] == 120.0
    assert result["beat_count"] == 5
    assert result["downbeats"] == [0.5, 2.5]
    assert result["tempo_stable"] is True

def test_analyze_tempo_beats_librosa_fallback(mock_madmom_setup, mock_librosa_setup):
    """Test tempo extraction falling back to librosa when madmom fails."""
    MOCKED_MODULES['madmom.features.beats'].RNNBeatProcessor.side_effect = ImportError("Mock missing madmom")
    
    result = analyze_tempo_beats("dummy.wav")
    assert result["method"] == "librosa-beat-track"
    assert result["bpm"] == 120.0
    assert result["beat_count"] == 5
    assert result["downbeats"] == [0.5, 2.5]

def test_analyze_tempo_beats_all_fail(mock_madmom_setup, mock_librosa_setup):
    """Test fallback when both madmom and librosa fail."""
    MOCKED_MODULES['madmom.features.beats'].RNNBeatProcessor.side_effect = ImportError("Mock missing madmom")
    MOCKED_MODULES['librosa'].load.side_effect = Exception("Audio load failed")
    
    result = analyze_tempo_beats("dummy.wav")
    assert result["method"] == None
    assert result["bpm"] == None
    assert len(result["beats"]) == 0

def test_analyze_key_success(mock_essentia_setup):
    """Test key extraction using essentia."""
    result = analyze_key("dummy.wav")
    assert result["key"] == "C"
    assert result["scale"] == "major"
    assert result["strength"] == 0.95
    assert result["key_label"] == "C major"

def test_analyze_key_fallback(mock_essentia_setup):
    """Test key extraction fallback when essentia is not available."""
    MOCKED_MODULES['essentia.standard'].MonoLoader.side_effect = ImportError("Mock missing essentia")
    
    result = analyze_key("dummy.wav")
    assert result.get("key") is None
    assert result.get("scale") is None
