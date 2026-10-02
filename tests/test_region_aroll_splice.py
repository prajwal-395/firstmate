"""Re-assigning one region's A-roll, and proving nothing else moved.

Punch list item 9: "redo this 20-second portion" must not reconsider the
whole edit.  After a region's spine blocks are re-anchored (a corrected
source range), `aroll.splice` re-assigns only those blocks; each test
below turns red if the splice reaches past the region.
"""

import copy
import importlib.util
import os

import pytest

from library.tools import region as region_mod
from library.tools import scope as scope_mod
from library.tools.plan_splice import SpliceRefused

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_step():
    spec = importlib.util.spec_from_file_location(
        "s301_step_under_test",
        os.path.join(REPO, "library/steps/step_3_01_assign_aroll/step.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


step = _load_step()

CATALOG = [{"clip_id": cid, "source_file": f"/footage/{cid}.mov",
            "duration_seconds": 60.0, "width": 1080, "height": 1920,
            "frame_rate": 30.0}
           for cid in ("clip_001", "clip_002")]


def _block(position, start, src, block_type="speech", clip="clip_001",
           duration=4.0):
    return {"position": position, "block_type": block_type,
            "clip_id": clip, "source_start": src,
            "source_end": src + duration, "timeline_start": start,
            "timeline_end": start + duration, "duration_seconds": duration}


def _spine():
    return {"structure": [_block(0, 0.0, 0.0, "hook"),
                          _block(1, 4.0, 10.0),
                          _block(2, 8.0, 20.0)]}


def _region(start, end):
    return scope_mod.region(region_mod.Region(None, start, end))


@pytest.fixture
def stored():
    return step.assign_a_roll(_spine(), CATALOG, 1080, 1920)


def _segment(assignments, position):
    (entry,) = [a for a in assignments["a_roll_assignments"]
                if a["spine_block_position"] == position]
    return entry["video_segments"][0]


def test_a_region_reassignment_leaves_every_other_block_untouched(stored):
    """The regression the punch list asks for: block 1 is re-anchored to
    another take, and blocks 0 and 2 - their random `link_group_id`s
    included - come back byte-identical."""
    before = copy.deepcopy(stored)
    spine = _spine()
    spine["structure"][1].update(clip_id="clip_002", source_start=30.0,
                                 source_end=34.0)

    out = step.splice_region_aroll(spine, CATALOG, stored,
                                   _region(4.0, 8.0))

    for position in (0, 2):
        assert _segment(out, position) == _segment(before, position)
    assert _segment(out, 1)["clip_id"] == "clip_002"
    assert _segment(out, 1)["video_in"] == 30.0
    assert out["hook_assignment"] == before["hook_assignment"]
    assert out["splice"]["outside_unchanged"] is True
    assert stored == before


def test_a_region_on_the_hook_reassigns_the_hook(stored):
    spine = _spine()
    spine["structure"][0].update(clip_id="clip_002")
    out = step.splice_region_aroll(spine, CATALOG, stored, _region(0.0, 4.0))
    assert out["hook_assignment"]["clip_id"] == "clip_002"
    assert _segment(out, 1) == _segment(stored, 1)


def test_a_block_that_moved_on_the_timeline_is_refused(stored):
    """A longer block moves every block after it: a re-plan, not a splice."""
    spine = _spine()
    spine["structure"][1].update(timeline_end=8.5, source_end=14.5,
                                 duration_seconds=4.5)
    with pytest.raises(SpliceRefused, match="move blocks"):
        step.splice_region_aroll(spine, CATALOG, stored, _region(4.0, 8.0))


def test_a_region_touching_no_block_is_refused(stored):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        step.splice_region_aroll(_spine(), CATALOG, stored,
                                 _region(60.0, 70.0))
