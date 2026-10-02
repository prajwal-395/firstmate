"""Redoing the transitions in one region, and proving nothing else moved.

Punch list item 9: "redo this 20-second portion" must not reconsider the
whole edit.  `transitions.splice` resolves only the cuts a region owns -
the cuts INTO the blocks it touches, plus the end slot when it touches
the last block - and splices them into the stored `transition_spec`.
"""

import copy

import pytest

from library.steps.step_4_02_plan_transitions.post_bridge import (
    resolve_transitions,
    splice_region_transitions,
)
from library.tools import region as region_mod
from library.tools import scope as scope_mod
from library.tools.plan_splice import SpliceRefused

FPS = 30.0


def _spine():
    blocks = []
    for pos, word in ((1, "one"), (2, "two"), (3, "three"), (4, "four")):
        start = (pos - 1) * 2.0
        blocks.append({
            "position": pos, "block_type": "speech", "clip_id": "clip_001",
            "source_start": start, "source_end": start + 2.0,
            "timeline_start": start, "timeline_end": start + 2.0,
            "word_timestamps": [{"word": word, "source_start": start,
                                 "source_end": start + 2.0}],
        })
    return {"structure": blocks, "frame_rate": FPS}


def _at(cut, ttype="cross_dissolve", frames=12):
    return {"cut_point_position": cut, "type": ttype,
            "duration_frames": frames, "rationale": "a join"}


def _region(start, end):
    return scope_mod.region(region_mod.Region(None, start, end))


def _splice(creative, stored, region):
    return splice_region_transitions(creative, _spine(), {}, stored, region,
                                     project_fps=FPS)


@pytest.fixture
def stored():
    return resolve_transitions([_at(2), _at(3), _at(4), _at("end",
                                "fade_to_black", 30)],
                               _spine(), {}, frame_rate=FPS,
                               music_analysis={})


def _by_cut(spec, cut):
    return [t for t in spec if t["cut_into_position"] == cut]


def test_a_region_redo_leaves_every_transition_outside_it_untouched(stored):
    """The regression the punch list asks for: the cut into block 3 is
    re-planned, and the cuts into 2 and 4 and the end come back
    byte-identical."""
    before = copy.deepcopy(stored)

    out = _splice([_at(3, "dip_to_black", 8)], stored, _region(4.0, 6.0))

    spec = out["transition_spec"]
    for cut in (2, 4, "end"):
        assert _by_cut(spec, cut) == _by_cut(before, cut)
    (inside,) = _by_cut(spec, 3)
    assert inside["duration_frames"] == 8
    assert out["splice"]["outside_unchanged"] is True
    assert out["splice"]["positions"] == [3]
    assert stored == before


def test_a_region_replans_to_the_same_entry_the_whole_plan_gave_it(stored):
    """Ids count within their cut: re-resolving one cut reproduces it,
    id included, where a run-global counter renumbered it `trans_001`."""
    out = _splice([_at(3)], stored, _region(4.0, 6.0))
    assert out["transition_spec"] == stored


def test_a_region_on_the_last_block_owns_the_end_slot(stored):
    out = _splice([_at(4)], stored, _region(6.0, 8.0))
    assert _by_cut(out["transition_spec"], "end") == []
    assert _by_cut(out["transition_spec"], 3) == _by_cut(stored, 3)


def test_a_fresh_transition_outside_the_region_is_refused(stored):
    with pytest.raises(SpliceRefused, match="not in the region"):
        _splice([_at(3), _at(4)], stored, _region(4.0, 6.0))


def test_a_region_touching_no_block_is_refused(stored):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        _splice([], stored, _region(60.0, 70.0))
