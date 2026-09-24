import pytest
import numpy as np
from unittest.mock import patch, MagicMock
from library.tools.analysis.ocr_extractor import (
    OCRExtractor, TextDetection, TrackedText, OCRResult, Track, bbox_iou
)
import os

@pytest.fixture
def mock_easyocr():
    with patch('library.tools.analysis.ocr_extractor.easyocr.Reader') as mock_reader:
        instance = mock_reader.return_value
        yield instance

def test_track_merging():
    det1 = TextDetection("EXIT", 0.9, (10, 10, 50, 20), (0.1, 0.1, 0.5, 0.2))
    track = Track(det1, 1.0)
    
    # Add detection within 2 seconds
    det2 = TextDetection("EXIT", 0.95, (12, 11, 48, 19), (0.12, 0.11, 0.48, 0.19))
    track.add(det2, 2.5)
    
    result = track.to_tracked_text()
    assert result.text == "EXIT"
    assert len(result.appearances) == 1
    assert result.appearances[0] == (1.0, 2.5)
    assert result.is_static == True

    # Add detection after 3 seconds (gap > 2.0)
    det3 = TextDetection("EXIT", 0.8, (10, 10, 50, 20), (0.1, 0.1, 0.5, 0.2))
    track.add(det3, 6.0)
    
    result2 = track.to_tracked_text()
    assert len(result2.appearances) == 2
    assert result2.appearances[0] == (1.0, 2.5)
    assert result2.appearances[1] == (6.0, 6.0)
    
