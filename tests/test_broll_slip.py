"""Rung 7 (K1, CT3.3): a b-roll source slip is a plan value.

"Slip the second b-roll shot 1s later in its source, keep its
position and duration on the timeline" had no plan spelling: the
window chooser owned the source range outright. `slip_seconds` (the
request stating seconds) / `slip_frames` (stating frames, E3) shift
the chosen window at the same timeline position and duration. Both
stated must agree; a slip past the file's ends refuses with the
bounds rather than clamping onto unplayed media.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_3_02_select_broll.post_bridge import (
    resolve_broll,
)

SPINE = {"structure": [
    {"position": 1, "block_type": "speech", "clip_id": "clip_a",
     "timeline_start": 0.0, "timeline_end": 4.0},
], "frame_rate": 30.0}

CATALOG = [
    {"clip_id": "clip_a", "path": "/footage/a.MOV",
     "duration_seconds": 45.0, "width": 1920, "height": 1080,
     "rotation": 0},
    {"clip_id": "clip_b", "path": "/footage/b.MOV",
     "duration_seconds": 45.0, "width": 1920, "height": 1080,
     "rotation": 0},
]


def _resolve(creative):
    return resolve_broll(
        list(creative), [], CATALOG, [], [], SPINE,
        target_resolution=(1080, 1920))


def test_a_stated_slip_shifts_source_at_the_same_timeline():
    """CT3.3's shape: 1 s later in source, timeline untouched."""
    plain = _resolve([{"clip_id": "clip_b", "spine_block_position": 1}])
    slipped = _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                         "slip_seconds": 1.0}])
    (base,) = plain["b_roll_assignments"]
    (entry,) = slipped["b_roll_assignments"]
    assert entry["video_in"] == pytest.approx(base["video_in"] + 1.0)
    assert entry["video_out"] == pytest.approx(base["video_out"] + 1.0)
    assert entry["timeline_start"] == base["timeline_start"]
    assert entry["timeline_end"] == base["timeline_end"]
    assert entry["slip_seconds"] == pytest.approx(1.0)
    assert base["slip_seconds"] == 0.0


def test_slip_frames_match_slip_seconds_and_disagreement_refuses():
    seconds = _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                         "slip_seconds": 1.0}])
    frames = _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                        "slip_frames": 30}])
    assert frames["b_roll_assignments"][0]["video_in"] == pytest.approx(
        seconds["b_roll_assignments"][0]["video_in"])
    with pytest.raises(ValueError, match="ambiguous spec"):
        _resolve([{"clip_id": "clip_b", "spine_block_position": 1,
                   "slip_seconds": 1.0, "slip_frames": 60}])
