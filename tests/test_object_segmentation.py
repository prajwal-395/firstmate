import pytest
import numpy as np
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from library.tools.analysis.object_segmentation import (
    encode_rle,
    decode_rle,
    find_match_cut_candidates,
    first_frame_face_box,
    normalize_face_box,
    FACE_SEED_CATEGORY,
    FACE_SEED_LABEL,
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

@patch('library.tools.analysis.object_segmentation.build_sam2_video_predictor_hf')
@patch('library.tools.analysis.object_segmentation.build_sam2_hf')
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


class TestNormalizeFaceBox:
    """The detection rect travels normalized between the steps that measure
    it and the one that prompts SAM 2 with it (issue #268)."""

    def test_normal_rect_is_normalized(self):
        assert normalize_face_box(20, 30, 40, 40, 100, 100) == [
            0.2, 0.3, 0.6, 0.7]

    def test_overflowing_rect_is_clamped_not_squeezed(self):
        assert normalize_face_box(-10, -5, 60, 60, 100, 100) == [
            0.0, 0.0, 0.5, 0.55]

    def test_degenerate_or_outside_rects_carry_no_box(self):
        assert normalize_face_box(10, 10, 0, 20, 100, 100) is None
        assert normalize_face_box(10, 10, -5, 20, 100, 100) is None
        assert normalize_face_box(200, 200, 10, 10, 100, 100) is None
        assert normalize_face_box(10, 10, 20, 20, 0, 100) is None
        assert normalize_face_box("x", 10, 20, 20, 100, 100) is None


class TestFirstFrameFaceBox:
    """The seeder reads one sample of the `face_boxes` track (issue #268)."""

    def test_present_box_is_scaled_to_frame_pixels(self):
        presence = {"sample_rate_hz": 5,
                    "face_boxes": [[0.2, 0.3, 0.6, 0.7], None]}
        assert first_frame_face_box(presence, 100, 100) == (20.0, 30.0, 60.0, 70.0)
        assert first_frame_face_box(presence, 100, 100, sample_index=1) is None

    def test_absences_read_as_no_seed(self):
        assert first_frame_face_box({}, 100, 100) is None
        assert first_frame_face_box({"face_boxes": []}, 100, 100) is None
        assert first_frame_face_box({"face_boxes": [None]}, 100, 100) is None
        assert first_frame_face_box(
            {"face_boxes": [[0.2, 0.2, 0.6, 0.6]]}, 100, 100,
            sample_index=5) is None

    def test_a_block_predating_face_boxes_invents_no_y(self):
        """Center plus width cannot place the box vertically.

        The legacy track names no y, so a seed built from it would fabricate
        one. The honest answer is no seed, and the caller declines.
        """
        legacy = {"sample_rate_hz": 5,
                  "values": [1.0],
                  "face_center_x": [0.4],
                  "face_width": [0.4]}
        assert first_frame_face_box(legacy, 100, 100) is None

    def test_degenerate_after_clamping_is_no_seed(self):
        presence = {"face_boxes": [[2.0, 2.0, 3.0, 3.0]]}
        assert first_frame_face_box(presence, 100, 100) is None


@patch('library.tools.analysis.object_segmentation.build_sam2_video_predictor_hf')
@patch('library.tools.analysis.object_segmentation.build_sam2_hf')
@patch('library.tools.analysis.object_segmentation.SAM2AutomaticMaskGenerator')
@patch('library.tools.analysis.object_segmentation.torch')
@patch('subprocess.run')
@patch('cv2.imread')
def test_segment_clip_seeds_single_target_from_face_box(
        mock_imread, mock_run, mock_torch, mock_generator, mock_build_sam2,
        mock_build_predictor):
    """Issue #268: a face box seeds one deliberate target, never ten blobs."""
    mock_pred_instance = MagicMock()
    mock_build_predictor.return_value = mock_pred_instance
    mock_gen_instance = MagicMock()
    mock_generator.return_value = mock_gen_instance

    mock_run.return_value = MagicMock(returncode=0)
    mock_image = np.zeros((100, 100, 3), dtype=np.uint8)
    mock_imread.return_value = mock_image

    mock_pred_instance.init_state.return_value = "state"
    mock_pred_instance.add_new_points_or_box.return_value = (None, [1], [None])

    import torch
    mock_logits = torch.ones((1, 100, 100))
    mock_pred_instance.propagate_in_video.return_value = [
        (0, [1], mock_logits.unsqueeze(0)),
        (1, [1], mock_logits.unsqueeze(0))
    ]

    with tempfile.TemporaryDirectory() as td:
        Path(td, "00000.jpg").touch()
        Path(td, "00001.jpg").touch()

        with patch('tempfile.TemporaryDirectory', return_value=MagicMock(__enter__=MagicMock(return_value=td))):
            result = ObjectSegmenter().segment_clip(
                "fake.mp4", face_box=[0.2, 0.3, 0.6, 0.7])

            # The blob machinery is never run: no generate, no mask prompts.
            mock_gen_instance.generate.assert_not_called()
            mock_pred_instance.add_new_mask.assert_not_called()
            # One box prompt, in frame pixels, on frame 0.
            mock_pred_instance.add_new_points_or_box.assert_called_once()
            _, kwargs = mock_pred_instance.add_new_points_or_box.call_args
            assert kwargs["frame_idx"] == 0
            assert kwargs["obj_id"] == 1
            np.testing.assert_allclose(
                np.asarray(kwargs["box"], dtype=float), [20.0, 30.0, 60.0, 70.0])

            assert len(result.objects) == 1
            obj = result.objects[0]
            assert obj.object_id == "obj_1"
            assert obj.label == FACE_SEED_LABEL
            assert obj.category == FACE_SEED_CATEGORY
            assert obj.frames == [0, 1]
            assert "face_seeded" in (result.seed_note or "")


@patch('library.tools.analysis.object_segmentation.build_sam2_video_predictor_hf')
@patch('library.tools.analysis.object_segmentation.SAM2AutomaticMaskGenerator')
@patch('subprocess.run')
def test_segment_clip_require_face_declines_before_models_or_ffmpeg(
        mock_run, mock_generator, mock_build_predictor):
    """Issue #268: no face means no masks, not a tracked car window.

    The decline returns before ffmpeg extracts a frame and before SAM 2
    loads, and says why on the result.
    """
    result = ObjectSegmenter().segment_clip("fake.mp4", require_face=True)

    assert result.objects == []
    assert (result.seed_note or "").startswith("declined:")
    mock_run.assert_not_called()
    mock_build_predictor.assert_not_called()
    mock_generator.assert_not_called()


def test_segment_clip_rejects_a_malformed_face_box():
    """A caller bug fails loudly here, not as a squeezed seed in SAM 2."""
    with pytest.raises(ValueError):
        ObjectSegmenter().segment_clip("fake.mp4", face_box=[0.1, 0.2])
    with pytest.raises(ValueError):
        ObjectSegmenter().segment_clip("fake.mp4", face_box="face")


def test_save_and_load_roundtrips_seed_note():
    obj = TrackedObject(
        object_id="obj_1", label="face_seeded_subject", category="person",
        frames=[0], masks_rle={0: "0 10"}, bboxes={0: (0, 0, 10, 10)},
        avg_area_ratio=0.5
    )
    result = SegmentationResult(
        video_path="test.mp4", frame_count=10, resolution=(100, 100),
        sample_fps=2.0, objects=[obj], seed_note="declined: no face box")

    with tempfile.TemporaryDirectory() as td:
        result.save(td)
        assert SegmentationResult.load(td, "test").seed_note == \
            "declined: no face box"


def test_load_without_seed_note_reads_as_unseeded_history():
    """Files written before seed_note existed carry no key; that is old
    output, not a decline, so it loads as None rather than failing."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td, "old_segmentation.json")
        out.write_text(
            '{"video_path": "old.mp4", "frame_count": 1, '
            '"resolution": [10, 10], "sample_fps": 2.0, "objects": []}')
        assert SegmentationResult.load(td, "old").seed_note is None


class TestStepPassesFaceBoxesThrough:
    """The step is the connection: a map opts the run into face seeding."""

    def _catalog(self, tmp_path):
        clip = tmp_path / "clip_001.mp4"
        clip.write_bytes(b"fake")
        return ([str(clip)],
                [{"clip_id": "clip_001", "path": str(clip)}])

    def test_face_map_reaches_segment_clip(self, tmp_path):
        from library.steps.step_1_06_object_segmentation import step as s106
        raw, catalog = self._catalog(tmp_path)
        box = [0.2, 0.2, 0.6, 0.6]
        made = SegmentationResult(
            video_path=str(raw[0]), frame_count=2, resolution=(10, 10),
            sample_fps=2.0, objects=[], seed_note="declined: no face box")

        with patch.object(s106, "get_segmenter") as mock_get:
            mock_get.return_value.segment_clip.return_value = made
            out = s106.run_step(
                raw, catalog, str(tmp_path / "out"),
                face_boxes_by_clip={"clip_001": box})

        _, kwargs = mock_get.return_value.segment_clip.call_args
        assert kwargs["face_box"] == box
        assert kwargs["require_face"] is True
        assert out["object_segmentation"][0]["seed_note"] == \
            "declined: no face box"

    def test_no_map_keeps_the_legacy_call(self, tmp_path):
        from library.steps.step_1_06_object_segmentation import step as s106
        raw, catalog = self._catalog(tmp_path)
        made = SegmentationResult(
            video_path=str(raw[0]), frame_count=2, resolution=(10, 10),
            sample_fps=2.0, objects=[], seed_note="blob_seeded: 1 largest")

        with patch.object(s106, "get_segmenter") as mock_get:
            mock_get.return_value.segment_clip.return_value = made
            s106.run_step(raw, catalog, str(tmp_path / "out"))

        _, kwargs = mock_get.return_value.segment_clip.call_args
        assert "face_box" not in kwargs
        assert "require_face" not in kwargs
