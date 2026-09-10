import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

# Mock heavy mlx_vlm dependency before importing vision_pipeline_v3
mlx_mock = MagicMock()
mlx_mock.load.return_value = (MagicMock(), MagicMock())
mlx_mock.generate.return_value = MagicMock(text="[]")
mlx_prompt_utils = MagicMock()
mlx_prompt_utils.apply_chat_template.return_value = "prompt"
mlx_mock.prompt_utils = mlx_prompt_utils

# Now we can import the pipeline safely
from library.tools.analysis import vision_pipeline_v3 as vp

@pytest.fixture(autouse=True)
def mock_mlx_functions():
    with patch("library.tools.analysis.vision_pipeline_v3.load", mlx_mock.load), \
         patch("library.tools.analysis.vision_pipeline_v3.generate", mlx_mock.generate), \
         patch("library.tools.analysis.vision_pipeline_v3.apply_chat_template", mlx_prompt_utils.apply_chat_template):
        yield


@pytest.fixture
def sample_temporal_index():
    return {
        "duration_s": 10.0,
        "scene_boundaries": [{"timestamp": 3.5}, {"timestamp": 7.2}, {"timestamp": 0.1}],
        "speech_regions": [{"start": 1.0, "end": 2.5, "text": "hello"}],
        # The shape step 1.04 really writes. This fixture used to say
        # `"camera_motion": {"residual": [...]}` - a key nothing has ever
        # written - so the test passed against a signal the pipeline
        # never read. See library/tools/camera_stability.py.
        "camera_motion_decomposition": {
            "sample_rate_hz": 5,
            "values": [{"translation_x": 0.0, "translation_y": 0.0,
                        "zoom_factor": 1.0, "residual": r}
                       for r in [0.01, 0.015, 0.012] * 4],
        },
    }

def test_merge_objects():
    objects = [
        {"label": "dog", "appearances": [[1, 3], [5, 6]]},
        {"label": "Dog", "appearances": [[2, 4]], "details": "brown"},
        {"label": "cat", "appearances": [[8, 10]]},
        {"label": "dog", "appearances": [[100, 105]]} # Way past clip duration
    ]
    merged = vp.merge_objects(objects, max_duration=20)
    
    dog = next(o for o in merged if o["label"] == "dog")
    assert dog["details"] == "brown"
    # [1, 3] and [2, 4] overlap, should merge to [1, 4]
    # [5, 6] remains. [100, 105] is discarded since > max_duration*2 (40)
    assert dog["appearances"] == [[1, 4], [5, 6]]
    
    cat = next(o for o in merged if o["label"] == "cat")
    assert cat["appearances"] == [[8, 10]]

def test_get_scene_boundaries(sample_temporal_index):
    boundaries = vp.get_scene_boundaries(sample_temporal_index)
    assert boundaries == [3.5, 7.2] # 0.1 is skipped (<= 0.5)

@patch("subprocess.run")
def test_extract_frames(mock_run, tmp_path):
    clip = tmp_path / "video.mp4"
    cache = tmp_path / "cache"
    
    def side_effect(*args, **kwargs):
        # args[0] is the command list, the last element is the output path.
        # A successful ffmpeg writes a real file: an empty one is a
        # capture that did not happen (see marker_capture's WHEN THE
        # ROUTE FAILS) and extract_frames skips it.
        cmd = args[0]
        out_path = Path(cmd[-1])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\xff\xd8\xff\xe0" + b"frame")
        return MagicMock(returncode=0)
        
    mock_run.side_effect = side_effect
    
    # 10 second clip, 5 second interval -> 0s, 5s, 9.9s (since duration-0.1 is 9.9)
    # n_frames = max(1, ceil(10/5)) + 1 = 2 + 1 = 3 frames
    frames = vp.extract_frames(clip, duration=10.0, cache_dir=cache, interval_s=5)
    
    assert len(frames) == 3
    assert frames[0]["timestamp"] == 0.0
    assert frames[1]["timestamp"] == 5.0
    assert frames[2]["timestamp"] == 9.9
    assert mock_run.call_count == 3

def test_find_detail_ranges():
    coarse_objects = [
        {"role": "background", "appearances": [[1, 2], [5, 6]]}, # total 2s < 15s limit
        {"role": "primary_subject", "appearances": [[0, 10]]} # skipped due to role
    ]
    
    ranges = vp.find_detail_ranges(coarse_objects, duration=20.0)
    # [1,2] -> [0, 4] padded by 2s
    # [5,6] -> [3, 8] padded by 2s
    # they merge because gap is < 5s (3 <= 4 + 5)
    # merged -> [0, 8]
    assert ranges == [(0.0, 8.0)]

def test_compute_deterministic_assessment(sample_temporal_index):
    transcript = "hello"
    assessment = vp.compute_deterministic_assessment(sample_temporal_index, transcript)
    
    assert assessment["speech_present"] is True
    assert assessment["speech_coverage"] == 0.15 # 1.5s / 10.0s
    # residual mean ~0.0123, under half a grid step of the block search
    assert assessment["camera_stability"] == "stable"
    assert assessment["camera_stability_method"] == "optical_flow_residual"
    # No motion scale and no picture sample, so nothing measured the
    # ranges - and an unmeasured clip claims nothing, not everything.
    assert assessment["usable_ranges_method"] == "unmeasured"  # only 12 motion samples < 30
    assert assessment["usable_ranges"] == []
    assert assessment["unusable_ranges"] == []
    assert assessment["usable_ranges_signals"] == []


def test_a_failed_assessment_call_asserts_no_subject_visibility():
    """`[]` would say "the subject appears nowhere", and nothing looked.

    Same defect as `usable_ranges: [[0, duration]]`, inverted: an answer
    written where the pass that would have produced it did not run.
    """
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (None, "", 0.0)

    assessment, _ = vp.analyze_assessment(
        analyzer, "clip.mov", 10.0,
        {"speech_present": None, "camera_stability": "unknown"})

    assert assessment["content_type"] == "unknown"
    assert assessment["primary_subject_visible"] is None
    assert assessment["usable_ranges_method"] == "unmeasured"
    assert assessment["usable_ranges"] == []


def test_a_successful_assessment_call_keeps_the_model_ranges():
    analyzer = MagicMock()
    analyzer.analyze_with_retry.return_value = (
        {"content_type": "scenery", "primary_subject_visible": [[0, 9]]},
        "", 0.0)

    assessment, _ = vp.analyze_assessment(
        analyzer, "clip.mov", 10.0, {"camera_stability": "unknown"},
        soft_picture_ranges=[])

    assert assessment["content_type"] == "scenery"
    assert assessment["primary_subject_visible"] == [[0, 9]]
    assert assessment["usable_ranges_method"] == "deterministic_v1"
    assert assessment["usable_ranges"] == [[0, 10.0]]

def test_parse_json_array():
    # Valid JSON
    assert vp.parse_json_array('[{"test": 1}]') == [{"test": 1}]
    # Markdown wrapped
    assert vp.parse_json_array('```json\n[{"test": 1}]\n```') == [{"test": 1}]
    # Broken JSON array but fixable
    assert vp.parse_json_array('[{"test": 1},]') == [{"test": 1}]
