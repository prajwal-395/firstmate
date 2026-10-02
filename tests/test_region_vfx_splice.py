"""Redoing the effects in one region, and proving nothing else moved.

Punch list item 9: "redo this 20-second portion" must not reconsider the
whole edit.  `vfx.splice` re-resolves only the blocks a region touches
and splices them into the stored `enhancement_spec`; each test below
turns red if the splice reaches past the region.
"""

import importlib.util
import json
import os

import pytest

from library.tools import region as region_mod
from library.tools import scope as scope_mod
from library.tools.plan_splice import SpliceRefused

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_post_bridge():
    spec = importlib.util.spec_from_file_location(
        "s403_post_bridge_under_test",
        os.path.join(REPO, "library/steps/step_4_03_plan_vfx/post_bridge.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pb = _load_post_bridge()


def _block(position, start, duration=2.0):
    return {"position": position, "block_type": "speech", "clip_id": "c1",
            "source_start": 0.0, "source_end": duration,
            "timeline_start": start, "timeline_end": start + duration,
            "duration_seconds": duration}


SPINE = {"structure": [_block(0, 0.0), _block(1, 2.0), _block(2, 4.0)]}


def _shake(position, amount=0.01):
    return {"target_block_position": position, "effect_type": "screen_shake",
            "params": {"shake_x": amount}, "rationale": "a hit"}


def _drift(position):
    return {"target_block_position": position, "effect_type": "slow_zoom_in",
            "params": {"zoom_start": 1.0, "zoom_end": 1.1},
            "rationale": "life on a static hold"}


def _region(start, end):
    return scope_mod.region(region_mod.Region(None, start, end))


@pytest.fixture
def stored_spec():
    plan = [_shake(0), _shake(1), _shake(2)]
    return {"visual_effects": pb.resolve_vfx(plan, SPINE),
            "planning_basis": {"proposed": 3}}


def test_a_region_redo_leaves_every_effect_outside_it_untouched(stored_spec):
    """The regression the punch list asks for: block 1 is re-planned with
    a different effect, and blocks 0 and 2 come back byte-identical."""
    before = json.loads(json.dumps(stored_spec))

    out = pb.splice_region_vfx([_drift(1)], SPINE, stored_spec,
                               _region(2.0, 4.0))

    effects = out["enhancement_spec"]["visual_effects"]
    outside = [e for e in effects if e["target_block_position"] != 1]
    assert outside == [e for e in before["visual_effects"]
                       if e["target_block_position"] != 1]
    inside = [e for e in effects if e["target_block_position"] == 1]
    assert [e["effect_type"] for e in inside] == ["slow_zoom_in"]
    assert out["splice"]["outside_unchanged"] is True
    assert out["splice"]["replaced"] == {"1": {"before": 1, "after": 1}}
    # The whole-plan basis is the stored one; the region's is reported.
    assert out["enhancement_spec"]["planning_basis"] == {"proposed": 3}
    assert out["splice"]["region_planning_basis"]["proposed"] == 1
    # And the stored spec handed in was not mutated.
    assert stored_spec == before


def test_a_region_replans_to_the_same_ids_the_whole_plan_gave_it(
        stored_spec):
    """Block-local ids: re-resolving block 1 alone reproduces exactly the
    entry the whole-plan pass gave it. Under the old run-global counter
    the region's effect came back as `vfx_001` and collided with block 0's."""
    out = pb.splice_region_vfx([_shake(1)], SPINE, stored_spec,
                               _region(2.0, 4.0))
    assert out["enhancement_spec"]["visual_effects"] == \
        stored_spec["visual_effects"]


def test_an_empty_region_plan_removes_that_regions_effects(stored_spec):
    """Stillness is an answer. Inferring the region from what came back
    would have kept block 1's old shake."""
    out = pb.splice_region_vfx([], SPINE, stored_spec, _region(2.0, 4.0))
    positions = [e["target_block_position"]
                 for e in out["enhancement_spec"]["visual_effects"]]
    assert positions == [0, 2]
    assert out["splice"]["outside_unchanged"] is True


def test_a_fresh_effect_outside_the_region_is_refused(stored_spec):
    with pytest.raises(SpliceRefused, match="not in the region"):
        pb.splice_region_vfx([_drift(1), _drift(2)], SPINE, stored_spec,
                             _region(2.0, 4.0))


def test_a_region_touching_no_block_is_refused(stored_spec):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        pb.splice_region_vfx([], SPINE, stored_spec, _region(60.0, 70.0))
