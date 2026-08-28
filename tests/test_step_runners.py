"""Step 1.02 catalogs footage from ffprobe metadata.

This file used to carry six more tests, all of which reported a result
they had not measured (issue #249):

* `test_temporal_index` and `test_prosody_analysis` guarded an import
  with `pytest.skip`.  `build_temporal_index` resolves, so the first ran
  and its whole body was `pass`; `analyze_prosody` is not in step 1.05 at
  all - it lives in `library/tools/analysis/speech_advanced_pipeline.py` -
  so the second skipped in every environment, always has, and its body
  was `pass` too.
* `test_assign_aroll`, `test_render_subtitles`, `test_render_motion_graphics`
  and `test_creative_cohesion` wrapped an import and a call in
  `except Exception: pass`.  None of the four symbols exists, so all four
  reported PASS - a green dot for a step nothing had touched, which is
  the same defect as the skips and reads worse.

They are deleted rather than repaired, because the steps they named are
covered where the coverage can be honest:

* 1.04 - `tests/test_step_stdout_contract.py`, `tests/test_face_sample_aspect.py`,
  `tests/test_face_presence_position.py`, `tests/test_subject_framing.py`
* 1.05 - `tests/test_prosody_failure_is_loud.py`, `tests/test_prosody_view.py`
* 4.05 - `tests/test_subtitle_style.py`, `tests/test_subtitle_emphasis.py`,
  `tests/test_step_stdout_contract.py`
* 4.06 - `tests/test_motion_graphics_empty_render.py`,
  `tests/test_motion_graphics_template.py`
* 5.03 - `tests/test_creative_cohesion.py`, `tests/test_cohesion_gates_fire.py`,
  `tests/test_cohesion_application.py`

Step 3.01 (`assign_aroll`) is the one that is left with no direct test.
Saying so is the honest outcome: writing one is its own piece of work and
is not what removing a false green is for.

`tests/test_no_unfailable_tests.py` is what stops all six shapes coming
back.
"""
from unittest.mock import patch

from library.steps.step_1_02_catalog_footage.step import catalog_footage


@patch("library.steps.step_1_02_catalog_footage.step.extract_metadata")
@patch("os.path.isfile")
def test_catalog_footage(mock_isfile, mock_extract):
    mock_isfile.return_value = True
    mock_extract.return_value = {
        "duration_seconds": 10.0,
        "width": 1920,
        "height": 1080,
        "frame_rate": 30.0,
        "video_codec": "h264",
        "audio_codec": "aac",
        "audio_channels": 2,
        "audio_sample_rate": 48000,
        "creation_time": "2023-01-01T00:00:00Z",
        "rotation": 0,
        "pixel_format": "yuv420p",
        "has_audio": True,
    }
    inputs = [{"path": "/mock/path.mov", "filename": "path.mov", "extension": ".mov", "size_bytes": 1000, "clip_id": "clip_001"}]
    output = catalog_footage(inputs)
    assert "clip_catalog" in output
    assert len(output["clip_catalog"]) == 1
    assert output["clip_catalog"][0]["clip_id"] == "clip_001"
