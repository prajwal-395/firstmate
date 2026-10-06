"""Tests for the global picture plan (Gap 10).

The defect this prevents: A-roll assignment is deterministic (maps each
block to the spine's clip reference) and B-roll selection is per-block
LLM. Neither reasons globally about picture. A gesture at second 34
cannot be matched to a side-angle clip covering it, because no
cross-clip, cross-measurement join exists.

The fidelity test: given a gesture at second 34 and a side-angle clip
covering it, the A-roll plan uses the side angle for the block
containing second 34.
"""
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.picture_plan import build_picture_plan, select_shot_for_block


def _make_catalog():
    """A catalog with a main A-roll clip and a side-angle clip."""
    return [
        {
            "clip_id": "clip_main",
            "source_file": "/footage/main.mp4",
            "duration_seconds": 60.0,
            "width": 1920,
            "height": 1080,
            "frame_rate": 30.0,
        },
        {
            "clip_id": "clip_side",
            "source_file": "/footage/side.mp4",
            "duration_seconds": 60.0,
            "width": 1920,
            "height": 1080,
            "frame_rate": 30.0,
        },
        {
            "clip_id": "clip_wide",
            "source_file": "/footage/wide.mp4",
            "duration_seconds": 60.0,
            "width": 1920,
            "height": 1080,
            "frame_rate": 30.0,
        },
    ]


def _make_semantic():
    """Semantic analysis with a gesture at second 34 on the side angle."""
    return {
        "clip_main": {
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 60, "location": "studio", "type": "interview"}],
            "camera": [{"start": 0, "end": 60, "framing": "medium", "stability": "stable"}],
            "assessment": {
                "content_type": "interview",
                "speech_present": True,
                "usable_portions": "0-60",
            },
            "actions": [
                {
                    "window": [0, 60],
                    "actions": [
                        {
                            "start": 0,
                            "end": 60,
                            "action": "person sitting talking",
                            "body_language": "neutral posture",
                            "speech_cue": "speaking",
                        }
                    ],
                }
            ],
        },
        "clip_side": {
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 60, "location": "studio", "type": "interview"}],
            "camera": [{"start": 0, "end": 60, "framing": "medium", "stability": "stable"}],
            "assessment": {
                "content_type": "interview",
                "speech_present": True,
                "usable_portions": "0-60",
            },
            "actions": [
                {
                    "window": [0, 60],
                    "actions": [
                        {
                            "start": 30,
                            "end": 40,
                            "action": "person gesturing at screen",
                            "body_language": "hand raised, pointing at screen, expressive",
                            "speech_cue": "speaking with emphasis",
                        }
                    ],
                }
            ],
        },
        "clip_wide": {
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 60, "location": "studio", "type": "interview"}],
            "camera": [{"start": 0, "end": 60, "framing": "wide", "stability": "stable"}],
            "assessment": {
                "content_type": "interview",
                "usable_portions": "0-60",
            },
            "actions": [
                {
                    "window": [0, 60],
                    "actions": [
                        {
                            "start": 0,
                            "end": 60,
                            "action": "wide shot of studio",
                            "body_language": "neutral",
                            "speech_cue": None,
                        }
                    ],
                }
            ],
        },
    }


def _make_spine():
    """A spine with a speech block containing second 34."""
    return {
        "structure": [
            {
                "position": 1,
                "block_type": "hook",
                "clip_id": "clip_main",
                "source_start": 0.0,
                "source_end": 10.0,
                "timeline_start": 0.0,
                "timeline_end": 10.0,
            },
            {
                "position": 2,
                "block_type": "speech",
                "clip_id": "clip_main",
                "source_start": 30.0,
                "source_end": 40.0,
                "timeline_start": 10.0,
                "timeline_end": 20.0,
            },
            {
                "position": 3,
                "block_type": "speech",
                "clip_id": "clip_main",
                "source_start": 50.0,
                "source_end": 60.0,
                "timeline_start": 20.0,
                "timeline_end": 30.0,
            },
        ]
    }


def test_gesture_matched_angle_is_chosen():
    """The fidelity test: a gesture at second 34 and a side-angle clip
    covering it -> the A-roll plan uses the side angle for the block
    containing second 34.

    Today the A-roll is the same file as the speech (clip_main). The
    global picture plan should select clip_side for block 2 because it
    covers the gesture at second 34.
    """
    catalog = _make_catalog()
    semantic = _make_semantic()
    spine = _make_spine()

    plan = build_picture_plan(spine, catalog, semantic)

    # Block 2 contains second 34 (source_start=30, source_end=40)
    block_2 = next(b for b in plan["blocks"] if b["spine_block_position"] == 2)

    # The side angle should be selected because it covers the gesture
    assert block_2["selected_clip_id"] == "clip_side", (
        f"Expected clip_side (gesture coverage) but got {block_2['selected_clip_id']}. "
        f"Scores: {block_2['candidates'][0]['scores']}"
    )

    # The gesture coverage score should be > 0 for the selected clip
    assert block_2["candidates"][0]["scores"]["gesture_coverage"] > 0


def test_spine_clip_used_when_no_gesture():
    """When no clip covers a gesture, the spine's own clip is used."""
    catalog = _make_catalog()
    semantic = _make_semantic()
    spine = _make_spine()

    plan = build_picture_plan(spine, catalog, semantic)

    # Block 1 (hook, 0-10s) has no gesture coverage on any clip
    block_1 = next(b for b in plan["blocks"] if b["spine_block_position"] == 1)
    assert block_1["selected_clip_id"] == "clip_main"

    # Block 3 (50-60s) has no gesture coverage on any clip
    block_3 = next(b for b in plan["blocks"] if b["spine_block_position"] == 3)
    assert block_3["selected_clip_id"] == "clip_main"


def test_variety_across_blocks():
    """The plan considers the whole edit: variety is optimized globally,
    not per-block."""
    catalog = _make_catalog()
    semantic = _make_semantic()
    spine = _make_spine()

    plan = build_picture_plan(spine, catalog, semantic)

    # The plan should use more than one clip across the edit
    unique_clips = plan["summary"]["unique_clips_used"]
    assert unique_clips >= 2, (
        f"Expected at least 2 unique clips for variety, got {unique_clips}"
    )


def test_select_shot_for_block_entry_point():
    """The per-block entry point selects the best shot given the edit so far."""
    catalog = _make_catalog()
    semantic = _make_semantic()
    spine = _make_spine()

    block_2 = spine["structure"][1]
    result = select_shot_for_block(block_2, catalog, semantic)

    assert result["selected_clip_id"] == "clip_side"
    assert result["candidates"][0]["scores"]["gesture_coverage"] > 0


def test_speech_free_clip_cannot_override_a_speech_block():
    """A clip the semantic pass measured no speech in is not an angle
    of the speaker, so it may not override the spine's clip on a speech
    block - whatever its gesture coverage.  The A-roll over a speech
    block is the speaker's picture; a speech-free clip is B-roll, and
    B-roll belongs on its own track (step 3.02).
    """
    catalog = _make_catalog()
    semantic = _make_semantic()
    # The side angle becomes a B-roll cutaway: same gesture coverage,
    # but the semantic pass measured no speech in it.
    semantic["clip_side"]["assessment"]["speech_present"] = False

    plan = build_picture_plan(_make_spine(), catalog, semantic)

    block_2 = next(b for b in plan["blocks"] if b["spine_block_position"] == 2)
    assert block_2["selected_clip_id"] == "clip_main"
    assert [c["clip_id"] for c in block_2["candidates"]] == ["clip_main"]


def test_no_candidates_falls_back_to_spine_clip():
    """When no candidates have semantic descriptions, the spine's clip is used."""
    catalog = _make_catalog()
    semantic = {}  # No semantic analysis
    spine = _make_spine()

    plan = build_picture_plan(spine, catalog, semantic)

    for block_plan in plan["blocks"]:
        assert block_plan["selected_clip_id"] == "clip_main"
        assert block_plan["reason"] == "no candidates"


def test_plan_summary_statistics():
    """The plan reports aggregate statistics."""
    catalog = _make_catalog()
    semantic = _make_semantic()
    spine = _make_spine()

    plan = build_picture_plan(spine, catalog, semantic)

    summary = plan["summary"]
    assert summary["total_blocks"] == 3
    assert summary["gesture_matched_blocks"] == 1
    assert summary["spine_clip_used"] == 2
    assert summary["unique_clips_used"] == 2
