"""Redoing one region's cutaways, and proving nothing else moved.

Punch list item 9: "redo this 20-second portion" must not reconsider the
whole edit.  `broll.splice` resolves only the region's fresh cutaways and
splices them into the stored selections; each test below turns red if
the splice reaches past the region, or lets two cutaways share V2.
"""

import copy

import pytest

from library.steps.step_3_02_select_broll.post_bridge import (
    resolve_broll,
    splice_region_broll,
)
from library.tools import operations
from library.tools import region as region_mod
from library.tools import scope as scope_mod
from library.tools.plan_splice import SpliceRefused


def _block(position, start, clip="clip_a"):
    return {"position": position, "block_type": "speech", "clip_id": clip,
            "timeline_start": start, "timeline_end": start + 4.0}


SPINE = {"structure": [_block(0, 0.0), _block(1, 4.0), _block(2, 8.0)],
         "frame_rate": 30.0}

CATALOG = [{"clip_id": cid, "path": f"/footage/{cid}.MOV",
            "duration_seconds": 45.0, "width": 1080, "height": 1920,
            "rotation": 0}
           for cid in ("clip_a", "clip_b", "clip_c")]


def _cut(position, clip="clip_b", **extra):
    return {"clip_id": clip, "spine_block_position": position,
            "selection_rationale": "a cutaway", **extra}


def _region(start, end):
    return scope_mod.region(region_mod.Region(None, start, end))


def _splice(creative, stored, region, interjections=None):
    return splice_region_broll(creative, CATALOG, [], SPINE, stored, region,
                               b_roll_interjections=interjections)


@pytest.fixture
def stored():
    return resolve_broll(
        [_cut(2)],
        [{"clip_id": "clip_c", "over_spine_block_position": 0,
          "timeline_start": 1.0, "timeline_end": 2.5}],
        CATALOG, [], [], SPINE, target_resolution=(1080, 1920),
    )


def test_a_region_redo_leaves_every_cutaway_outside_it_untouched(stored):
    """The regression the punch list asks for: block 1 gains a cutaway,
    and block 2's cutaway and block 0's interjection come back
    byte-identical."""
    before = copy.deepcopy(stored)

    out = _splice([_cut(1, "clip_c")], stored, _region(4.0, 8.0))

    outside = [a for a in out["b_roll_assignments"]
               if a["spine_block_position"] != 1]
    assert outside == before["b_roll_assignments"]
    assert out["b_roll_interjections"] == before["b_roll_interjections"]
    (inside,) = [a for a in out["b_roll_assignments"]
                 if a["spine_block_position"] == 1]
    assert inside["clip_id"] == "clip_c"
    assert out["splice"]["outside_unchanged"] is True
    assert stored == before


def test_an_empty_region_plan_removes_that_regions_cutaway(stored):
    out = _splice([], stored, _region(8.0, 12.0))
    assert out["b_roll_assignments"] == []
    assert out["b_roll_interjections"] == stored["b_roll_interjections"]


def test_a_region_interjection_is_placed_around_a_kept_cutaway(stored):
    """V2 is one track: a fresh interjection over block 1 that asks for
    time block 2's kept cutaway already holds is trimmed to the free
    window, never laid over it."""
    out = _splice([], stored, _region(4.0, 8.0), interjections=[
        {"clip_id": "clip_c", "over_spine_block_position": 1,
         "timeline_start": 6.0, "timeline_end": 9.0}])
    (fresh,) = [i for i in out["b_roll_interjections"]
                if i["over_spine_block_position"] == 1]
    assert fresh["timeline_end"] <= 8.0


def test_a_fresh_cutaway_colliding_with_a_kept_one_is_refused(stored):
    """Block 0's kept interjection reaches into block 1 here, so a full
    cutaway on block 1 would overlap it."""
    reaching = copy.deepcopy(stored)
    reaching["b_roll_interjections"][0].update(timeline_start=3.0,
                                               timeline_end=5.0)
    with pytest.raises(SpliceRefused, match="overlap"):
        _splice([_cut(1, "clip_c")], reaching, _region(4.0, 8.0))


def test_a_fresh_cutaway_outside_the_region_is_refused(stored):
    with pytest.raises(SpliceRefused, match="not in the region"):
        _splice([_cut(1, "clip_c"), _cut(2, "clip_c")], stored,
                _region(4.0, 8.0))


def test_a_region_touching_no_block_is_refused(stored):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        _splice([], stored, _region(60.0, 70.0))


def test_broll_splice_is_a_region_only_operation():
    op = operations.get("broll.splice")
    assert op.supports(_region(4.0, 8.0))
    assert not op.supports(scope_mod.project())
    assert op.run.__name__ == "splice_region_broll"
