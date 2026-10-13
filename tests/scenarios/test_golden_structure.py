"""Media-free checks for the deterministic structure behind golden edits.

The complete media-producing workflows stay in their companion scenario
files. These checks exercise the planning seam and recipe boundaries without
encoding source clips, overlays, previews, or exports.
"""

from __future__ import annotations

import json
from itertools import pairwise

from tests.scenarios import golden, golden_edit


def test_monologue_speech_and_cutaway_recipe_is_structurally_sound():
    recipe = golden_edit.MONOLOGUE
    talking = recipe.clips[0]
    assert talking.name == "talk"
    assert [clip.name for clip in recipe.clips[1:]] == ["broll_1", "broll_2"]
    assert len(recipe.lines) == 3
    windows = [
        (start, recipe.words("talk")[index]["end"])
        for index, (start, _text) in enumerate(recipe.lines)
    ]
    assert all(left[1] <= right[0] for left, right in pairwise(windows))
    assert windows[0][0] < windows[1][0] < windows[2][0]

    answers = json.loads(golden_edit.ANSWERS.read_text(encoding="utf-8"))
    (planner_cutaway,) = answers["select_broll"]["b_roll_interjections"]
    (whiteboard,) = answers["select_broll"]["broll_creative"]
    assert (planner_cutaway["clip_id"],
            planner_cutaway["over_spine_block_position"]) == ("clip_001", 2)
    assert (whiteboard["clip_id"],
            whiteboard["spine_block_position"]) == ("clip_002", 3)

    from library.steps.step_3_02_select_broll.post_bridge import resolve_broll

    spine = {"frame_rate": 30.0, "structure": [
        {"position": 2, "block_type": "speech", "clip_id": "clip_003",
         "timeline_start": 3.6, "timeline_end": 4.6},
        {"position": 3, "block_type": "speech", "clip_id": "clip_003",
         "timeline_start": 7.45, "timeline_end": 9.0},
    ]}
    catalog = [
        {"clip_id": clip_id, "path": f"/{clip_id}.mov",
         "duration_seconds": 5.0, "width": 1920, "height": 1080,
         "rotation": 0}
        for clip_id in ("clip_001", "clip_002", "clip_003")
    ]
    decisions = resolve_broll(
        answers["select_broll"]["broll_creative"],
        answers["select_broll"]["b_roll_interjections"],
        catalog, [], [], spine, target_resolution=(1080, 1920))
    (cover,) = decisions["b_roll_assignments"]
    (cutaway,) = decisions["b_roll_interjections"]
    assert cover["clip_id"] == "clip_002"
    assert cover["timeline_start"] == 7.45
    assert (cutaway["assigned_clip"]["clip_id"], cutaway["timeline_start"],
            cutaway["timeline_end"]) == ("clip_001", 3.6, 4.6)


def test_conversation_post_bridge_proposes_body_and_measured_closer():
    from library.tools.operations import load_step_module

    recipe = golden.CONVERSATION
    transcript = {
        "derived_from": {"duration_seconds": recipe.seconds},
        "segments": [
            {
                "timeline_start": line.first,
                "timeline_end": line.last,
                "speaker": line.speaker,
                "text": line.text,
                "resolve_item_id": f"item-{index}",
            }
            for index, line in enumerate(recipe.lines)
        ],
    }
    post_bridge = load_step_module(
        "step_3_04_select_reels", "post_bridge.py")
    result = post_bridge.resolve(
        golden.conversation_answer(recipe),
        {"timeline_transcript": transcript})["reel_selection"]

    (moment,) = result["moments"]
    assert moment["approval"] == "proposed"
    assert moment["speakers"] == ["SpeakerOne", "SpeakerTwo"]
    assert moment["call_to_action"]["speaker"] == "SpeakerOne"
    assert moment["call_to_action"]["text"] == recipe.lines[4].text
