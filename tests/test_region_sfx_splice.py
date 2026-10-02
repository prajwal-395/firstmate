"""Redoing the sound in one region, and proving nothing else moved.

Punch list item 9: "redo this 20-second portion" must not reconsider the
whole edit.  `sfx.splice` places only the region's fresh sounds and
splices them into the stored `sfx_spec`; each test below turns red if the
splice reaches past the region.
"""

import importlib.util
import json
import os

import pytest

from library.tools import operations
from library.tools import region as region_mod
from library.tools import scope as scope_mod
from library.tools.plan_splice import SpliceRefused
from library.tools.sfx_library import load_sfx_catalog

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


def _block(position, start, duration=4.0):
    return {"position": position, "block_type": "speech",
            "clip_id": "clip_001", "source_start": start,
            "source_end": start + duration, "timeline_start": start,
            "timeline_end": start + duration, "duration_seconds": duration,
            "word_timestamps": [], "alignment_method": "whisperx"}


SPINE = {"structure": [_block(0, 0.0), _block(1, 4.0), _block(2, 8.0)]}


def _sound(position, sfx_id=IMPACT, volume_db=-10):
    return {"spine_block_position": position, "sfx_id": sfx_id,
            "volume_db": volume_db, "rationale": "the hit"}


def _region(start, end):
    return scope_mod.region(region_mod.Region(None, start, end))


@pytest.fixture
def stored_spec(catalog):
    return pb.resolve_sfx([_sound(0), _sound(1), _sound(2)], SPINE,
                          catalog=catalog)


def test_a_region_redo_leaves_every_sound_outside_it_untouched(
        stored_spec, catalog):
    """The regression the punch list asks for: block 1 gets a layered
    re-plan, and the sounds on blocks 0 and 2 come back byte-identical."""
    before = json.loads(json.dumps(stored_spec))

    out = pb.splice_region_sfx(
        [_sound(1, RISER, -14), _sound(1, IMPACT, -6)], SPINE, stored_spec,
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
    out = pb.splice_region_sfx([_sound(1)], SPINE, stored_spec,
                               _region(4.0, 8.0), catalog=catalog)
    assert out["sfx_spec"]["sfx_list"] == stored_spec["sfx_list"]
    labels = [s["label"] for s in out["sfx_spec"]["sfx_list"]]
    assert len(set(labels)) == len(labels)


def test_an_empty_region_plan_removes_that_regions_sounds(
        stored_spec, catalog):
    out = pb.splice_region_sfx([], SPINE, stored_spec, _region(4.0, 8.0),
                               catalog=catalog)
    assert [s["spine_block_position"]
            for s in out["sfx_spec"]["sfx_list"]] == [0, 2]


def test_a_splice_reaching_past_its_region_is_refused(stored_spec, catalog):
    for fresh, region, said in (
            ([_sound(1), _sound(2)], (4.0, 8.0), "not in the region"),
            ([], (60.0, 70.0), "touches no spine block")):
        with pytest.raises(SpliceRefused, match=said):
            pb.splice_region_sfx(fresh, SPINE, stored_spec,
                                 _region(*region), catalog=catalog)


def test_sfx_splice_is_a_region_only_operation():
    op = operations.get("sfx.splice")
    assert op.supports(_region(4.0, 8.0))
    assert not op.supports(scope_mod.project())
