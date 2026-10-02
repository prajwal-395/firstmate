"""Ren's entry-motion and property-set operations (`reel.entry_motion`,
`reel.set_properties`): owned by `build_reels`, routed through the
touchup's stage-conform-write-verify-promote path, written in place and
judged by re-read. Design history: `docs/evidence/reel_touchup.md`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composer as C  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from tests.composed_edit_harness import (  # noqa: E402
    FakeComp, FakeTool, build_reel, covering_window, duplicate,
    media_pool,
)

#: The effect both operations derive - the same requirement their
#: owning node's siblings carry.  One node, one effect, four routes.
REEL_GOAL = "state.verify_reels.reel_build"

NEW_OPERATIONS = ("reel.entry_motion", "reel.set_properties")


# ── Registration: the 1327 shape ──────────────────────────────────────


# ── Effect and requires: derived, in the vocabulary ───────────────────


# ── Reachability through compose, without steering it ─────────────────


def test_the_representative_stays_the_rebuild():
    """The forbidden move, pinned as a refusal to make it.

    Two new siblings share `build_reels`' effect, so compose has four
    routes to one goal.  Bending a declaration to steer the choice -
    making one operation's effect differ from another's - is exactly
    what 1327 forbade, and choosing between equivalent routes is the
    sibling lane's open problem, not this one's.  The representative
    stays the rebuild."""
    assert C.representative("build_reels") == "reel.build"
    assert C.compose(REEL_GOAL).operations == ("reel.build",)


# ── The step bodies refuse what is malformed ──────────────────────────


def test_step_bodies_refuse_malformed_specs_and_each_others_edits():
    """The contract `touch_reel` keeps: no project folder, no mapping, or
    nothing to do raise before Resolve is touched. And one body, one
    kind - `reel.touchup` runs mixed kinds, so nothing servable is
    refused."""
    from library.steps.step_7_01_build_reels.step import (
        animate_entry, set_clip_properties)

    for body in (animate_entry, set_clip_properties):
        with pytest.raises(ValueError, match="project_folder"):
            body("", {"reel": 1, "edits": [
                {"op": "set_properties", "row": "V4", "item": 0,
                 "properties": {"ZoomX": 1.5}}]})
        with pytest.raises(TypeError, match="spec mapping"):
            body("/project", ["not", "a", "mapping"])
        with pytest.raises(ValueError, match="at least one edit"):
            body("/project", {"reel": 1, "edits": []})

    motion = {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6}]}
    props = {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]}
    with pytest.raises(ValueError, match="entry_motion"):
        set_clip_properties("/project", motion)
    with pytest.raises(ValueError, match="set_properties"):
        animate_entry("/project", props)


# ── The gate: what routes, what refuses ───────────────────────────────


def _tracks(timeline):
    return reel_read.read_tracks(timeline)


def test_in_place_kinds_qualify_composed_with_no_delete_or_place(tmp_path):
    """Both in-place kinds qualify COMPOSED and the composition plan is
    empty: no change, insertion or removal."""
    timeline, _pool, _media = build_reel(tmp_path)
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5, "Opacity": 80.0}}]})
    assert qualification.gate_class == tu.COMPOSED
    (entry,) = qualification.in_place
    assert entry["kind"] == "set_properties"
    assert (entry["row"], entry["item_index"]) == ("V4", 0)
    assert entry["properties"] == {"ZoomX": 1.5, "Opacity": 80.0}
    assert (qualification.changes, qualification.insertions,
            qualification.removals) == ([], [], [])

    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6, "fade_out_frames": 6}]})
    assert qualification.gate_class == tu.COMPOSED
    (entry,) = qualification.in_place
    assert entry["kind"] == "entry_motion"
    assert (entry["fade_in_frames"], entry["fade_out_frames"]) == (6, 6)
    assert entry["duration"] == 40
    assert (qualification.changes, qualification.insertions,
            qualification.removals) == ([], [], [])


def test_an_in_place_edit_it_cannot_write_refuses_before_staging(tmp_path):
    """Each row refuses loudly, before anything is staged, rather than
    being skipped: a property mapping that is empty or not a mapping, an
    item that is not there, an animation that is nothing or negative, a
    ramp with no neutral frame (it would hold across the clip,
    `fusion.played_window`), and the comp-bearing or audio rows - V1/V2
    treatments belong to the comp pass, and a second treatment the
    recorded manifest does not know is dropped by the next
    re-derivation."""
    timeline, _pool, _media = build_reel(tmp_path)
    props = {"op": "set_properties", "row": "V4", "item": 0}
    motion = {"op": "entry_motion", "row": "V4", "item": 0}
    rows = [
        (dict(props, properties={}), "names no properties"),
        (dict(props, properties="ZoomX"), "a `properties` mapping"),
        (dict(props, item=9, properties={"ZoomX": 1.5}), None),
        (dict(motion, fade_in_frames=0, fade_out_frames=0),
         "animates nothing"),
        (dict(motion, fade_in_frames=-4), "negative"),
        (dict(motion, fade_in_frames=20, fade_out_frames=20),
         "one frame more"),
        # Exactly filling the clip still leaves no neutral frame.
        (dict(motion, fade_in_frames=40), None),
        (dict(motion, row="V1", fade_in_frames=6), "comp-bearing row"),
        (dict(motion, row="A1", fade_in_frames=6), "audio"),
    ]
    for edit, match in rows:
        with pytest.raises(tu.TouchupRefused, match=match):
            tu.qualify(_tracks(timeline), {"reel": 1, "edits": [edit]})


def test_entry_motion_refuses_an_item_that_already_carries_a_comp(
        tmp_path):
    """Stacking a second treatment beside one the manifest does not
    know is how a re-derivation drops work silently - so the gate
    refuses it the way `swap_pixels` refuses a comp-carrying swap."""
    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn", covering_window(40, 0, 200)),
        "Transform1": FakeTool("Transform", {"Size": 1.0})})]
    with pytest.raises(tu.TouchupRefused, match="drawing comp"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 6}]})
    # Resolve's own empty auto composition still qualifies: it draws
    # nothing, so it is not a treatment - the 10.4 direction.
    timeline.rows["V4"][0].comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn", covering_window(40, 0, 200)),
        "MediaOut1": FakeTool("MediaOut", {}),
        "AudioDisplay1": FakeTool("AudioDisplay", {})})]
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6}]})
    assert qualification.gate_class == tu.COMPOSED


def test_one_source_item_means_one_edit(tmp_path):
    """Two edits addressing the same item refuse by position - the
    existing rule, extended to the in-place kinds.  State those as
    two touchups."""
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused, match="same item"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "set_properties", "row": "V4", "item": 0,
             "properties": {"ZoomX": 1.5}},
            {"op": "move", "row": "V4", "item": 0, "to_row": "V4",
             "to_record": 1300}]})
    with pytest.raises(tu.TouchupRefused, match="same item"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 6},
            {"op": "set_properties", "row": "V4", "item": 0,
             "properties": {"ZoomX": 1.5}}]})


def test_a_rewrite_shadowed_by_an_in_place_write_is_pruned(tmp_path):
    """A remove re-places every kept item on its row - but an item
    another edit writes onto in place needs no re-place: the
    composition's capture reads the staged item after the write, so
    pruning the rewrite loses nothing and avoids the churn."""
    timeline, _pool, _media = build_reel(tmp_path)
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1},
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]})
    assert qualification.gate_class == tu.COMPOSED
    assert [(c.row, c.item_index) for c in qualification.changes] == [
        ("V4", 2)]
    assert len(qualification.in_place) == 1


# ── The write: onto staging, judged by re-read ────────────────────────


def _staged_pair(tmp_path):
    approved = build_reel(tmp_path)[0]
    staged = duplicate(approved)
    return approved, staged, media_pool(staged)


def test_property_sets_write_with_no_delete_and_no_place(tmp_path):
    """The property-set half of the row: `SetProperty` with read-back,
    and the delete/place machinery never runs - the calls that would
    destroy and rebuild the row stay empty."""
    _approved, staged, _pool = _staged_pair(tmp_path)
    before = staged.rows["V4"][0].GetProperty("ZoomX")
    assert before != 1.5
    qualification = tu.qualify(_tracks(staged), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]})
    applied = tu._apply_in_place(staged, qualification,
                                 str(tmp_path / "comps"))
    assert staged.rows["V4"][0].GetProperty("ZoomX") == 1.5
    assert staged.delete_calls == []
    assert _pool.append_calls == []
    assert applied["properties"][0]["properties"] == {"ZoomX": 1.5}
    assert applied["entry_motion"] == []


def test_a_property_that_will_not_read_back_refuses(tmp_path):
    """The write is judged by re-read, never by `SetProperty`'s
    return: an item whose read-back disagrees refuses mid-flight,
    with the staging left standing and the approved reel untouched."""
    _approved, staged, _pool = _staged_pair(tmp_path)
    original = staged.rows["V4"][0].GetProperty

    def _lying_get(key=None):
        current = dict(original())
        current["ZoomX"] = 999.0
        return current if key is None else current.get(key)

    staged.rows["V4"][0].GetProperty = _lying_get
    qualification = tu.qualify(_tracks(staged), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5}}]})
    with pytest.raises(tu.TouchupError, match="did not take"):
        tu._apply_in_place(staged, qualification,
                           str(tmp_path / "comps"))


def test_entry_motion_imports_a_drawing_comp_with_a_covering_window(
        tmp_path):
    """The entry-motion half: a builder-authored fade lands on the
    staged item, draws something, and its MediaIn covers every frame
    the item plays - the conform the comp pass runs on its own
    imports, read off the re-read rather than the return value."""
    _approved, staged, _pool = _staged_pair(tmp_path)
    item = staged.rows["V4"][0]
    assert item.GetFusionCompCount() == 0
    qualification = tu.qualify(_tracks(staged), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6, "fade_out_frames": 6}]})
    applied = tu._apply_in_place(staged, qualification,
                                 str(tmp_path / "comps"))
    assert item.GetFusionCompCount() == 1
    assert staged.delete_calls == []
    assert _pool.append_calls == []
    record = applied["entry_motion"][0]
    assert record["fade_in_frames"] == 6
    assert Path(record["comp"]).is_file()
    # The authored comp really is a fade over the played window.
    text = Path(record["comp"]).read_text(encoding="utf-8")
    assert "BezierSpline" in text
    assert record["window"]["repaired"] is False
    assert record["window"]["reason"] is None
    # And the re-read agrees the new comp draws something.
    tools = item.GetFusionCompByIndex(1).GetToolList(False)
    reg_ids = sorted(t.GetAttrs("TOOLS_RegID") for t in tools.values())
    assert "Merge" in reg_ids
    assert reel_read.comp_draws_something({"tools": reg_ids}) is True
