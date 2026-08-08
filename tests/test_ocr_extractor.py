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

def test_bbox_iou():
    box1 = (0.0, 0.0, 0.5, 0.5)
    box2 = (0.25, 0.25, 0.5, 0.5)
    iou = bbox_iou(box1, box2)
    assert 0 < iou < 1.0

    box3 = (0.6, 0.6, 0.2, 0.2)
    assert bbox_iou(box1, box3) == 0.0

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
    
def test_extract_text_from_frame(mock_easyocr):
    mock_easyocr.readtext.return_value = [
        ([[10, 10], [60, 10], [60, 30], [10, 30]], 'TEST', 0.95)
    ]
    
    extractor = OCRExtractor()
    extractor.reader = mock_easyocr
    
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    detections = extractor.extract_text_from_frame(frame)
    
    assert len(detections) == 1
    assert detections[0].text == 'TEST'
    assert detections[0].bbox == (10, 10, 50, 20)
    assert detections[0].bbox_normalized == (0.1, 0.1, 0.5, 0.2)

def test_ocr_result_serialization(tmp_path):
    res = OCRResult(
        video_path="test.mp4",
        frame_count=100,
        resolution=(1920, 1080),
        sample_fps=1.0,
        detections=[
            TrackedText("TEST", 0.9, [(0.0, 1.0)], (10, 10, 50, 20), (0.1, 0.1, 0.5, 0.2), True)
        ],
        raw_frame_detections={
            0: [TextDetection("TEST", 0.9, (10, 10, 50, 20), (0.1, 0.1, 0.5, 0.2))]
        }
    )
    
    res.save(str(tmp_path))
    loaded = OCRResult.load(str(tmp_path))
    
    assert loaded.video_path == "test.mp4"
    assert len(loaded.detections) == 1
    assert loaded.detections[0].text == "TEST"
    assert loaded.raw_frame_detections[0][0].text == "TEST"
