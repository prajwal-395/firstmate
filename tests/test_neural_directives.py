"""Stabilization is plan-requested, never keyword-decided.

compile_manifest used to set `neural_engine_directives[*].stabilize`
off a keyword match (`_UNSTABLE_CAMERA_WORDS`) against vision prose -
the stability summary, camera prose, scene text and assessment
keywords - so Ren stabilized clips nobody asked it to (captain,
2026-09-24). Now a shaky-worded clip compiles to NO stabilize
directive unless an `enhancement_spec.visual_effects` entry with
`effect_type == "stabilize"` (step 4.03 plan_vfx) covers it, and a
requested one still reaches the build's neural applicator.
"""
import os
from unittest.mock import patch

import pytest

from library.steps.step_5_04_compile_manifest.step import compile_manifest
from library.steps.step_4_03_plan_vfx.post_bridge import (
    STABILIZE_EFFECT,
    resolve_vfx,
)

SOURCE = os.path.abspath(__file__)

SHAKY_V3_DOC = {
    "clip_id": "clip_001",
    "scene": [{"start": 0, "end": 10, "location": "street",
               "type": "exterior"}],
    "camera": [{"start": 0, "end": 10, "framing": "medium",
                "mode": "handheld", "stability": "shaky",
                "movement": "walking"}],
    "actions": [], "objects": [],
    "assessment": {"content_type": "person_talking_to_camera",
                   "camera_stability": "shaky",
                   "keywords": ["handheld", "speaker"]},
}


def _inputs(semantic, enhancement_spec=None):
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
        "enhancement_spec": enhancement_spec
        if enhancement_spec is not None else [],
        "sfx_spec": [],
        "color_grade_spec": {}, "audio_mix_spec": {},
    }


def _directives(semantic, enhancement_spec=None):
    inputs = _inputs(semantic, enhancement_spec)
    with patch("library.steps.step_5_04_compile_manifest.step.load",
               side_effect=lambda out_dir, filename: inputs):
        manifest = compile_manifest("dummy")
    return manifest["neural_engine_directives"]


def _stabilize_request(position=1, start=0.0, end=10.0):
    return {"visual_effects": [{
        "target_block_position": position,
        "timeline_start": start, "timeline_end": end,
        "effect_type": "stabilize", "params": {},
        "rationale": "handheld walk, visibly shaky",
    }]}


def test_shaky_worded_clip_is_not_stabilized_without_a_plan_request():
    """The proof's first half: every word that used to trigger the
    keyword match is still in this document, and no directive follows."""
    directives = _directives(
        {"semantic_analysis_documents": [dict(SHAKY_V3_DOC)]})
    assert not any(d.get("stabilize") for d in directives.values())


def test_shaky_worded_clip_is_stabilized_when_the_plan_requests_it():
    """The proof's second half: the same shaky document plus one plan
    entry stabilizes the placed clip, through the build's neural path."""
    directives = _directives(
        {"semantic_analysis_documents": [dict(SHAKY_V3_DOC)]},
        _stabilize_request())
    assert directives.get("speech_1", {}).get("stabilize") is True


def test_a_locked_off_camera_is_not_stabilised():
    directives = _directives({"semantic_analysis_documents": [{
        "clip_id": "clip_001",
        "analysis": {"motion": "The camera remains stationary on a tripod "
                               "throughout the sequence."},
    }]})
    assert not any(d.get("stabilize") for d in directives.values())


def test_plan_vfx_resolves_a_stabilize_entry_to_the_neural_route():
    """The plan vocabulary the compile reads: `stabilize` resolves with
    `route == "neural_engine"`, never as a Fusion comp."""
    spine = {"structure": [{
        "position": 1, "timeline_start": 0.0, "timeline_end": 10.0,
    }]}
    resolved = resolve_vfx([{
        "target_block_position": 1, "effect_type": "stabilize",
        "params": {}, "rationale": "handheld walk, visibly shaky",
    }], spine)
    assert len(resolved) == 1
    entry = resolved[0]
    assert entry["effect_type"] == STABILIZE_EFFECT
    assert entry["route"] == "neural_engine"
    assert (entry["timeline_start"], entry["timeline_end"]) == (0.0, 10.0)


def test_documents_that_join_to_nothing_fail_loudly():
    """A key-name mismatch has to raise, not compile to an empty dict."""
    with pytest.raises(ValueError, match="none of them joined"):
        _directives({"semantic_analysis_documents": [
            {"clip_id": "a_clip_from_another_project",
             "analysis": {"motion": "handheld"}},
        ]})


def test_no_semantic_analysis_at_all_is_not_an_error():
    assert _directives({}) == {}
