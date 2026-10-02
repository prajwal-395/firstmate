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
from library.steps.step_3_02_select_broll.post_bridge import (
    resolve_broll,
    splice_region_broll,
)
import json
from library.tools import operations
from library.tools.sfx_library import load_sfx_catalog
from library.steps.step_4_02_plan_transitions.post_bridge import (
    resolve_transitions,
    splice_region_transitions,
)


REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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


# --------------------------------------------------------------------------
# From test_region_broll_splice.py
#
# Redoing one region's cutaways, and proving nothing else moved.
#
# Punch list item 9: "redo this 20-second portion" must not reconsider the
# whole edit.  `broll.splice` resolves only the region's fresh cutaways and
# splices them into the stored selections; each test below turns red if
# the splice reaches past the region, or lets two cutaways share V2.

def _block_2(position, start, clip="clip_a"):
    return {"position": position, "block_type": "speech", "clip_id": clip,
            "timeline_start": start, "timeline_end": start + 4.0}


SPINE = {"structure": [_block_2(0, 0.0), _block_2(1, 4.0), _block_2(2, 8.0)],
         "frame_rate": 30.0}

CATALOG_2 = [{"clip_id": cid, "path": f"/footage/{cid}.MOV",
            "duration_seconds": 45.0, "width": 1080, "height": 1920,
            "rotation": 0}
           for cid in ("clip_a", "clip_b", "clip_c")]


def _cut(position, clip="clip_b", **extra):
    return {"clip_id": clip, "spine_block_position": position,
            "selection_rationale": "a cutaway", **extra}


def _splice(creative, stored_2, region, interjections=None):
    return splice_region_broll(creative, CATALOG_2, [], SPINE, stored_2, region,
                               b_roll_interjections=interjections)


@pytest.fixture
def stored_2():
    return resolve_broll(
        [_cut(2)],
        [{"clip_id": "clip_c", "over_spine_block_position": 0,
          "timeline_start": 1.0, "timeline_end": 2.5}],
        CATALOG_2, [], [], SPINE, target_resolution=(1080, 1920),
    )


def test_a_region_redo_leaves_every_cutaway_outside_it_untouched(stored_2):
    """The regression the punch list asks for: block 1 gains a cutaway,
    and block 2's cutaway and block 0's interjection come back
    byte-identical."""
    before = copy.deepcopy(stored_2)

    out = _splice([_cut(1, "clip_c")], stored_2, _region(4.0, 8.0))

    outside = [a for a in out["b_roll_assignments"]
               if a["spine_block_position"] != 1]
    assert outside == before["b_roll_assignments"]
    assert out["b_roll_interjections"] == before["b_roll_interjections"]
    (inside,) = [a for a in out["b_roll_assignments"]
                 if a["spine_block_position"] == 1]
    assert inside["clip_id"] == "clip_c"
    assert out["splice"]["outside_unchanged"] is True
    assert stored_2 == before


def test_an_empty_region_plan_removes_that_regions_cutaway(stored_2):
    out = _splice([], stored_2, _region(8.0, 12.0))
    assert out["b_roll_assignments"] == []
    assert out["b_roll_interjections"] == stored_2["b_roll_interjections"]


def test_a_region_interjection_is_placed_around_a_kept_cutaway(stored_2):
    """V2 is one track: a fresh interjection over block 1 that asks for
    time block 2's kept cutaway already holds is trimmed to the free
    window, never laid over it."""
    out = _splice([], stored_2, _region(4.0, 8.0), interjections=[
        {"clip_id": "clip_c", "over_spine_block_position": 1,
         "timeline_start": 6.0, "timeline_end": 9.0}])
    (fresh,) = [i for i in out["b_roll_interjections"]
                if i["over_spine_block_position"] == 1]
    assert fresh["timeline_end"] <= 8.0


def test_a_fresh_cutaway_colliding_with_a_kept_one_is_refused(stored_2):
    """Block 0's kept interjection reaches into block 1 here, so a full
    cutaway on block 1 would overlap it."""
    reaching = copy.deepcopy(stored_2)
    reaching["b_roll_interjections"][0].update(timeline_start=3.0,
                                               timeline_end=5.0)
    with pytest.raises(SpliceRefused, match="overlap"):
        _splice([_cut(1, "clip_c")], reaching, _region(4.0, 8.0))


def test_a_fresh_cutaway_outside_the_region_is_refused(stored_2):
    with pytest.raises(SpliceRefused, match="not in the region"):
        _splice([_cut(1, "clip_c"), _cut(2, "clip_c")], stored_2,
                _region(4.0, 8.0))


def test_a_region_touching_no_block_is_refused_2(stored_2):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        _splice([], stored_2, _region(60.0, 70.0))


# --------------------------------------------------------------------------
# From test_region_sfx_splice.py
#
# Redoing the sound in one region, and proving nothing else moved.
#
# Punch list item 9: "redo this 20-second portion" must not reconsider the
# whole edit.  `sfx.splice` places only the region's fresh sounds and
# splices them into the stored `sfx_spec`; each test below turns red if the
# splice reaches past the region.

IMPACT = "test_impact.wav"
RISER = "test_riser.wav"


def _load_post_bridge():
    spec = importlib.util.spec_from_file_location(
        "s404_post_bridge_under_test",
        os.path.join(REPO, "library/steps/step_4_04_plan_sfx/post_bridge.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pb = _load_post_bridge()


@pytest.fixture
def catalog(tmp_path):
    lib = tmp_path / "sfx_library"
    lib.mkdir()
    entries = []
    for name, duration, shape in ((IMPACT, 0.3, "punchy"),
                                  (RISER, 1.0, "swelling")):
        (lib / name).write_bytes(b"RIFF....WAVEfmt ")
        entries.append({
            "file": name, "path": str(lib / name),
            "folder_category": "Accents",
            "description": f"a {shape} test sound",
            "technical": {"basic": {"duration": duration},
                          "energy_profile": {"envelope_shape": shape}},
            "transient_offset_sec": 0.0,
        })
    (lib / "sfx_index.json").write_text(json.dumps(entries))
    return load_sfx_catalog(str(lib))


def _block_3(position, start, duration=4.0):
    return {"position": position, "block_type": "speech",
            "clip_id": "clip_001", "source_start": start,
            "source_end": start + duration, "timeline_start": start,
            "timeline_end": start + duration, "duration_seconds": duration,
            "word_timestamps": [], "alignment_method": "whisperx"}


SPINE_2 = {"structure": [_block_3(0, 0.0), _block_3(1, 4.0), _block_3(2, 8.0)]}


def _sound(position, sfx_id=IMPACT, volume_db=-10):
    return {"spine_block_position": position, "sfx_id": sfx_id,
            "volume_db": volume_db, "rationale": "the hit"}


@pytest.fixture
def stored_spec(catalog):
    return pb.resolve_sfx([_sound(0), _sound(1), _sound(2)], SPINE_2,
                          catalog=catalog)


def test_a_region_redo_leaves_every_sound_outside_it_untouched(
        stored_spec, catalog):
    """The regression the punch list asks for: block 1 gets a layered
    re-plan, and the sounds on blocks 0 and 2 come back byte-identical."""
    before = json.loads(json.dumps(stored_spec))

    out = pb.splice_region_sfx(
        [_sound(1, RISER, -14), _sound(1, IMPACT, -6)], SPINE_2, stored_spec,
        _region(4.0, 8.0), catalog=catalog)

    merged = out["sfx_spec"]["sfx_list"]
    assert [s for s in merged if s["spine_block_position"] != 1] == \
        [s for s in before["sfx_list"] if s["spine_block_position"] != 1]
    inside = [s for s in merged if s["spine_block_position"] == 1]
    assert sorted(s["volume_db"] for s in inside) == [-14.0, -6.0]
    assert out["splice"]["outside_unchanged"] is True
    assert out["splice"]["replaced"] == {"1": {"before": 1, "after": 2}}
    assert out["sfx_spec"]["fairlight_preset"] == before["fairlight_preset"]
    assert stored_spec == before


def test_a_region_replans_to_the_same_labels_the_whole_plan_gave_it(
        stored_spec, catalog):
    """Block-local labels: re-placing block 1 alone reproduces the entry
    the whole-plan pass gave it, label included. Under the old run-global
    counter it came back `sfx_001` and collided with block 0's sound -
    and labels are compile_manifest's track-allocation key."""
    out = pb.splice_region_sfx([_sound(1)], SPINE_2, stored_spec,
                               _region(4.0, 8.0), catalog=catalog)
    assert out["sfx_spec"]["sfx_list"] == stored_spec["sfx_list"]
    labels = [s["label"] for s in out["sfx_spec"]["sfx_list"]]
    assert len(set(labels)) == len(labels)


def test_an_empty_region_plan_removes_that_regions_sounds(
        stored_spec, catalog):
    out = pb.splice_region_sfx([], SPINE_2, stored_spec, _region(4.0, 8.0),
                               catalog=catalog)
    assert [s["spine_block_position"]
            for s in out["sfx_spec"]["sfx_list"]] == [0, 2]


def test_a_splice_reaching_past_its_region_is_refused(stored_spec, catalog):
    for fresh, region, said in (
            ([_sound(1), _sound(2)], (4.0, 8.0), "not in the region"),
            ([], (60.0, 70.0), "touches no spine block")):
        with pytest.raises(SpliceRefused, match=said):
            pb.splice_region_sfx(fresh, SPINE_2, stored_spec,
                                 _region(*region), catalog=catalog)


def test_sfx_splice_is_a_region_only_operation():
    op = operations.get("sfx.splice")
    assert op.supports(_region(4.0, 8.0))
    assert not op.supports(scope_mod.project())


# --------------------------------------------------------------------------
# From test_region_transition_splice.py
#
# Redoing the transitions in one region, and proving nothing else moved.
#
# Punch list item 9: "redo this 20-second portion" must not reconsider the
# whole edit.  `transitions.splice` resolves only the cuts a region owns -
# the cuts INTO the blocks it touches, plus the end slot when it touches
# the last block - and splices them into the stored `transition_spec`.

FPS = 30.0


def _spine_2():
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


def _splice_2(creative, stored_3, region):
    return splice_region_transitions(creative, _spine_2(), {}, stored_3, region,
                                     project_fps=FPS)


@pytest.fixture
def stored_3():
    return resolve_transitions([_at(2), _at(3), _at(4), _at("end",
                                "fade_to_black", 30)],
                               _spine_2(), {}, frame_rate=FPS,
                               music_analysis={})


def _by_cut(spec, cut):
    return [t for t in spec if t["cut_into_position"] == cut]


def test_a_region_redo_leaves_every_transition_outside_it_untouched(stored_3):
    """The regression the punch list asks for: the cut into block 3 is
    re-planned, and the cuts into 2 and 4 and the end come back
    byte-identical."""
    before = copy.deepcopy(stored_3)

    out = _splice_2([_at(3, "dip_to_black", 8)], stored_3, _region(4.0, 6.0))

    spec = out["transition_spec"]
    for cut in (2, 4, "end"):
        assert _by_cut(spec, cut) == _by_cut(before, cut)
    (inside,) = _by_cut(spec, 3)
    assert inside["duration_frames"] == 8
    assert out["splice"]["outside_unchanged"] is True
    assert out["splice"]["positions"] == [3]
    assert stored_3 == before


def test_a_region_replans_to_the_same_entry_the_whole_plan_gave_it(stored_3):
    """Ids count within their cut: re-resolving one cut reproduces it,
    id included, where a run-global counter renumbered it `trans_001`."""
    out = _splice_2([_at(3)], stored_3, _region(4.0, 6.0))
    assert out["transition_spec"] == stored_3


def test_a_region_on_the_last_block_owns_the_end_slot(stored_3):
    out = _splice_2([_at(4)], stored_3, _region(6.0, 8.0))
    assert _by_cut(out["transition_spec"], "end") == []
    assert _by_cut(out["transition_spec"], 3) == _by_cut(stored_3, 3)


def test_a_fresh_transition_outside_the_region_is_refused(stored_3):
    with pytest.raises(SpliceRefused, match="not in the region"):
        _splice_2([_at(3), _at(4)], stored_3, _region(4.0, 6.0))


def test_a_region_touching_no_block_is_refused_3(stored_3):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        _splice_2([], stored_3, _region(60.0, 70.0))


# --------------------------------------------------------------------------
# From test_region_vfx_splice.py
#
# Redoing the effects in one region, and proving nothing else moved.
#
# Punch list item 9: "redo this 20-second portion" must not reconsider the
# whole edit.  `vfx.splice` re-resolves only the blocks a region touches
# and splices them into the stored `enhancement_spec`; each test below
# turns red if the splice reaches past the region.

def _load_post_bridge_2():
    spec = importlib.util.spec_from_file_location(
        "s403_post_bridge_under_test",
        os.path.join(REPO, "library/steps/step_4_03_plan_vfx/post_bridge.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pb_2 = _load_post_bridge_2()


def _block_4(position, start, duration=2.0):
    return {"position": position, "block_type": "speech", "clip_id": "c1",
            "source_start": 0.0, "source_end": duration,
            "timeline_start": start, "timeline_end": start + duration,
            "duration_seconds": duration}


SPINE_3 = {"structure": [_block_4(0, 0.0), _block_4(1, 2.0), _block_4(2, 4.0)]}


def _shake(position, amount=0.01):
    return {"target_block_position": position, "effect_type": "screen_shake",
            "params": {"shake_x": amount}, "rationale": "a hit"}


def _drift(position):
    return {"target_block_position": position, "effect_type": "slow_zoom_in",
            "params": {"zoom_start": 1.0, "zoom_end": 1.1},
            "rationale": "life on a static hold"}


@pytest.fixture
def stored_spec_2():
    plan = [_shake(0), _shake(1), _shake(2)]
    return {"visual_effects": pb_2.resolve_vfx(plan, SPINE_3),
            "planning_basis": {"proposed": 3}}


def test_a_region_redo_leaves_every_effect_outside_it_untouched(stored_spec_2):
    """The regression the punch list asks for: block 1 is re-planned with
    a different effect, and blocks 0 and 2 come back byte-identical."""
    before = json.loads(json.dumps(stored_spec_2))

    out = pb_2.splice_region_vfx([_drift(1)], SPINE_3, stored_spec_2,
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
    assert stored_spec_2 == before


def test_a_region_replans_to_the_same_ids_the_whole_plan_gave_it(
        stored_spec_2):
    """Block-local ids: re-resolving block 1 alone reproduces exactly the
    entry the whole-plan pass gave it. Under the old run-global counter
    the region's effect came back as `vfx_001` and collided with block 0's."""
    out = pb_2.splice_region_vfx([_shake(1)], SPINE_3, stored_spec_2,
                               _region(2.0, 4.0))
    assert out["enhancement_spec"]["visual_effects"] == \
        stored_spec_2["visual_effects"]


def test_an_empty_region_plan_removes_that_regions_effects(stored_spec_2):
    """Stillness is an answer. Inferring the region from what came back
    would have kept block 1's old shake."""
    out = pb_2.splice_region_vfx([], SPINE_3, stored_spec_2, _region(2.0, 4.0))
    positions = [e["target_block_position"]
                 for e in out["enhancement_spec"]["visual_effects"]]
    assert positions == [0, 2]
    assert out["splice"]["outside_unchanged"] is True


def test_a_fresh_effect_outside_the_region_is_refused(stored_spec_2):
    with pytest.raises(SpliceRefused, match="not in the region"):
        pb_2.splice_region_vfx([_drift(1), _drift(2)], SPINE_3, stored_spec_2,
                             _region(2.0, 4.0))


def test_a_region_touching_no_block_is_refused_4(stored_spec_2):
    with pytest.raises(SpliceRefused, match="touches no spine block"):
        pb_2.splice_region_vfx([], SPINE_3, stored_spec_2, _region(60.0, 70.0))
