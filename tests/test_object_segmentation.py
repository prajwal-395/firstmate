import pytest
import numpy as np
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from library.tools.analysis.object_segmentation import (
    encode_rle,
    decode_rle,
    find_match_cut_candidates,
    SegmentationResult,
    TrackedObject,
    ObjectSegmenter
)

def test_rle_encoding_decoding():
    # Create a simple binary mask
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[2:5, 3:7] = 1
    mask[8, 8] = 1
    
    # Encode
    rle = encode_rle(mask)
    
    # Decode
    decoded = decode_rle(rle, (10, 10))
    
    # Verify exact match
    assert np.array_equal(mask, decoded)

def test_find_match_cut_candidates():
    shape = (100, 100)
    
    # Create objects with perfectly overlapping masks on specific frames
    mask1 = np.zeros(shape, dtype=np.uint8)
    mask1[40:60, 40:60] = 1 # 20x20 square in middle
    rle1 = encode_rle(mask1)
    
    mask2 = np.zeros(shape, dtype=np.uint8)
    mask2[40:60, 40:60] = 1
    rle2 = encode_rle(mask2)
    
    mask3 = np.zeros(shape, dtype=np.uint8)
    mask3[10:30, 10:30] = 1 # completely different location
    rle3 = encode_rle(mask3)
    
    obj_a = TrackedObject(
        object_id="obj_a", label="A", category="X", frames=[1, 2],
        masks_rle={1: rle1, 2: rle3}, bboxes={}, avg_area_ratio=0.1
    )
    obj_b = TrackedObject(
        object_id="obj_b", label="B", category="Y", frames=[10, 11],
        masks_rle={10: rle2, 11: rle3}, bboxes={}, avg_area_ratio=0.1
    )
    
    res_a = SegmentationResult("a.mp4", 100, shape, 2.0, [obj_a])
    res_b = SegmentationResult("b.mp4", 100, shape, 2.0, [obj_b])
    
    candidates = find_match_cut_candidates(res_a, res_b, method="iou")
    
    # Frame 1 and 10 overlap perfectly (mask1 and mask2) -> score 1.0
    # Frame 2 and 11 overlap perfectly (mask3 and mask3) -> score 1.0
    assert len(candidates) == 2
    assert candidates[0].score == 1.0
    assert (candidates[0].frame_a == 1 and candidates[0].frame_b == 10) or (candidates[0].frame_a == 2 and candidates[0].frame_b == 11)

@patch('library.tools.analysis.object_segmentation.build_sam2_video_predictor')
@patch('library.tools.analysis.object_segmentation.build_sam2')
@patch('library.tools.analysis.object_segmentation.SAM2AutomaticMaskGenerator')
@patch('library.tools.analysis.object_segmentation.torch')
@patch('subprocess.run')
@patch('cv2.imread')
def test_segment_clip(mock_imread, mock_run, mock_torch, mock_generator, mock_build_sam2, mock_build_predictor):
    # Mocking SAM 2 setup
    mock_pred_instance = MagicMock()
    mock_build_predictor.return_value = mock_pred_instance
    mock_gen_instance = MagicMock()
    mock_generator.return_value = mock_gen_instance
    
    segmenter = ObjectSegmenter()
    # Force _ensure_loaded success
    segmenter._predictor = mock_pred_instance
    segmenter._generator = mock_gen_instance
    
    # Mock file extraction and reading
    mock_run.return_value = MagicMock(returncode=0)
    mock_image = np.zeros((100, 100, 3), dtype=np.uint8)
    mock_imread.return_value = mock_image
    
    # Mock generator returning one mask
    mock_gen_instance.generate.return_value = [
        {"segmentation": np.ones((100, 100), dtype=bool), "area": 10000}
    ]
    
    # Mock predictor init and propagate
    mock_pred_instance.init_state.return_value = "state"
    mock_pred_instance.add_new_mask.return_value = (None, [1], [None])
    
    # Propagate yields (frame_idx, obj_ids, mask_logits)
    import torch
    # Output logic requires logits > 0.0, so simulate positive logits
    mock_logits = torch.ones((1, 100, 100))
    mock_pred_instance.propagate_in_video.return_value = [
        (0, [1], mock_logits.unsqueeze(0)),
        (1, [1], mock_logits.unsqueeze(0))
    ]
    
    with tempfile.TemporaryDirectory() as td:
        # Create some fake extracted frames so glob finds them
        Path(td, "00000.jpg").touch()
        Path(td, "00001.jpg").touch()
        
        with patch('tempfile.TemporaryDirectory', return_value=MagicMock(__enter__=MagicMock(return_value=td))):
            result = segmenter.segment_clip("fake.mp4")
            
            assert result.frame_count == 2
            assert result.resolution == (100, 100)
            assert len(result.objects) == 1
            obj = result.objects[0]
            assert obj.object_id == "obj_1"
            assert obj.frames == [0, 1]
            assert 0 in obj.masks_rle
            assert 1 in obj.masks_rle
            assert obj.avg_area_ratio > 0

def test_save_and_load():
    obj = TrackedObject(
        object_id="obj_1", label="test", category="unknown",
        frames=[0], masks_rle={0: "0 10"}, bboxes={0: (0,0,10,10)},
        avg_area_ratio=0.5
    )
    result = SegmentationResult(
        video_path="test.mp4", frame_count=10, resolution=(100, 100),
        sample_fps=2.0, objects=[obj]
    )
    
    with tempfile.TemporaryDirectory() as td:
        result.save(td)
        loaded = SegmentationResult.load(td, "test")
        
        assert loaded.video_path == result.video_path
        assert loaded.frame_count == result.frame_count
        assert loaded.resolution == result.resolution
        assert len(loaded.objects) == 1
        
        l_obj = loaded.objects[0]
        assert l_obj.object_id == obj.object_id
        assert l_obj.masks_rle == obj.masks_rle
        assert l_obj.bboxes == obj.bboxes
