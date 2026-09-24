"""compile_manifest must be able to see the vision analysis.

It read `semantic_data["semantic_analysis"]["clips"]` - a key nothing
writes - so the lookup was empty on every run, `neural_engine_directives`
shipped as `{}`, and handheld iPhone A-roll went out unstabilised. The
second half of the break was `compute_neural_directives` reading `tags`,
`keywords` and `description`, which neither vision schema produces.
"""
import os
from unittest.mock import patch

import pytest

from library.steps.step_5_04_compile_manifest.step import compile_manifest

SOURCE = os.path.abspath(__file__)


def _inputs(semantic):
    return {
        "semantic_analysis": semantic,
        "audio_spine": {
            "structure": [{
                "block_type": "speech", "position": 1, "clip_id": "clip_001",
                "source_start": 2.417, "source_end": 12.417,
                "timeline_start": 0.0, "timeline_end": 10.0,
                "content": {"clip_id": "clip_001"},
            }],
            "frame_rate": 30.0,
        },
        # The catalog keys clips as clip_XXX while step_1_03 keys its
        # documents by file stem - the join semantic_index performs.
        "clip_catalog": [{"clip_id": "clip_001", "path": SOURCE,
                          "width": 1080, "height": 1920}],
        "a_roll_assignments": [{
            "spine_block_position": 1, "clip_id": "clip_001",
            "source_file": SOURCE, "video_in": 2.417, "video_out": 12.417,
            "timeline_start": 0.0, "timeline_end": 10.0,
        }],
        "b_roll_assignments": [], "transition_spec": [],
        "enhancement_spec": [], "sfx_spec": [],
        "color_grade_spec": {}, "audio_mix_spec": {},
    }


def _directives(semantic):
    inputs = _inputs(semantic)
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        manifest = compile_manifest("dummy")
    return manifest["neural_engine_directives"]


def test_handheld_footage_in_the_v3_schema_is_stabilised():
    """v3 measures exactly this - camera[].mode "handheld" plus a
    stability verdict - and there was no path from it to a directive."""
    directives = _directives({"semantic_analysis_documents": [{
        "clip_id": "clip_001",
        "scene": [{"start": 0, "end": 10, "location": "street",
                   "type": "exterior"}],
        "camera": [{"start": 0, "end": 10, "framing": "medium",
                    "mode": "handheld", "stability": "shaky",
                    "movement": "walking"}],
        "actions": [], "objects": [],
        "assessment": {"content_type": "person_talking_to_camera",
                       "camera_stability": "shaky"},
    }]})
    assert any(d.get("stabilize") for d in directives.values())


def test_a_locked_off_camera_is_not_stabilised():
    directives = _directives({"semantic_analysis_documents": [{
        "clip_id": "clip_001",
        "analysis": {"motion": "The camera remains stationary on a tripod "
                               "throughout the sequence."},
    }]})
    assert not any(d.get("stabilize") for d in directives.values())


def test_documents_that_join_to_nothing_fail_loudly():
    """A key-name mismatch has to raise, not compile to an empty dict."""
    with pytest.raises(ValueError, match="none of them joined"):
        _directives({"semantic_analysis_documents": [
            {"clip_id": "a_clip_from_another_project",
             "analysis": {"motion": "handheld"}},
        ]})


def test_no_semantic_analysis_at_all_is_not_an_error():
    assert _directives({}) == {}
