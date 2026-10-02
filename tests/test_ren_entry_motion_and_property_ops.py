"""Ren in the 1326/1327 shape: entry-motion and property-set operations.

The absorbed row (`vep-build-the-entry-motion-and-property-op`) asked
which half of the system owns small changes - the reel half
(touch-reel) or the main-edit half (region operations).  The rebuild
answers it by mechanism rather than by picking a half: an operation
declared in the effect vocabulary is reachable through compose
regardless of which half implements it.  Both operations below are
owned by `build_reels` and route through the touchup's own
stage-conform-write-verify-promote path - no new bespoke path, no
command-line verb, no composer branch.

What each one is:

- `reel.entry_motion` animates a placed overlay element in (and out)
  with an authored Fusion fade (`fusion.comp_builder` over the
  `fade_in_frames`/`fade_out_frames` keys, the dispatch the comp pass
  reads), imported onto the staged item and conformed by the pass's
  own `comp_media_window.conform_item` - without rebuilding the reel
  that carries it.
- `reel.set_properties` writes a property mapping onto an
  already-placed clip with `composed_edit.set_properties`, judged by
  read-back - without deleting and re-placing it.

The 1327 pattern, and nothing else: each declares its `Operation`
under the owning step, its effect DERIVES from the requirement
vocabulary (owning node `build_reels`, so the same effect its
siblings `reel.build` and `reel.touchup` carry), each has a real step
body, the SKILL.md is regenerated from the registry, and the tests
assert registration, vocabulary membership and reachability through
compose.

The finding this shape produces rather than bends around: both
operations land on an effect that already has a route
(`state.verify_reels.reel_build`, whose representative stays
`reel.build`).  Their declarations are NOT bent to steer which route
compose picks - selection between equivalent routes is the open
problem `vep-ren-two-routes-one-goal-no-basis-to-choose` owns, and
`test_the_representative_stays_the_rebuild` pins that this lane does
not pre-empt it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composer as C  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from library.tools import requirements as R  # noqa: E402
from library.tools.timeline_transcript import transcript_path  # noqa: E402
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


def test_step_bodies_refuse_malformed_specs():
    """The same contract `touch_reel` keeps: no project folder, no
    mapping, or nothing to do all raise before Resolve is touched."""
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


def test_step_bodies_refuse_each_others_edits():
    """One body, one kind.  A spec naming another kind through this
    body is addressed to the wrong operation - `reel.touchup` runs
    mixed kinds together, so nothing servable is refused."""
    from library.steps.step_7_01_build_reels.step import (
        animate_entry, set_clip_properties)

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


def test_set_properties_qualifies_composed(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "set_properties", "row": "V4", "item": 0,
         "properties": {"ZoomX": 1.5, "Opacity": 80.0}}]})
    assert qualification.gate_class == tu.COMPOSED
    assert len(qualification.in_place) == 1
    entry = qualification.in_place[0]
    assert entry["kind"] == "set_properties"
    assert (entry["row"], entry["item_index"]) == ("V4", 0)
    assert entry["properties"] == {"ZoomX": 1.5, "Opacity": 80.0}
    # No delete, no place: the composition plan is empty.
    assert qualification.changes == []
    assert qualification.insertions == []
    assert qualification.removals == []


@pytest.mark.parametrize("properties,match", [
    ({}, "names no properties"),
    ("ZoomX", "a `properties` mapping"),
])
def test_set_properties_refuses_what_it_cannot_write(tmp_path, properties,
                                                     match):
    """A key `set_properties` would silently skip - read-only, None, a
    placeholder - refuses loudly instead, before anything is staged."""
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused, match=match):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "set_properties", "row": "V4", "item": 0,
             "properties": properties}]})
    with pytest.raises(tu.TouchupRefused):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "set_properties", "row": "V4", "item": 9,
             "properties": {"ZoomX": 1.5}}]})


def test_entry_motion_qualifies_composed(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    qualification = tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
        {"op": "entry_motion", "row": "V4", "item": 0,
         "fade_in_frames": 6, "fade_out_frames": 6}]})
    assert qualification.gate_class == tu.COMPOSED
    assert len(qualification.in_place) == 1
    entry = qualification.in_place[0]
    assert entry["kind"] == "entry_motion"
    assert (entry["fade_in_frames"], entry["fade_out_frames"]) == (6, 6)
    assert entry["duration"] == 40
    assert qualification.changes == []
    assert qualification.insertions == []
    assert qualification.removals == []


def test_entry_motion_refuses_an_animation_that_is_nothing(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused, match="animates nothing"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 0, "fade_out_frames": 0}]})
    with pytest.raises(tu.TouchupRefused, match="negative"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": -4}]})


def test_entry_motion_refuses_a_ramp_longer_than_its_clip(tmp_path):
    """A ramp that never reaches neutral would hold across the whole
    clip (`fusion.played_window`) - refused with the numbers, not
    drawn."""
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused, match="one frame more"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 20, "fade_out_frames": 20}]})
    # Exactly filling the clip still leaves no neutral frame.
    with pytest.raises(tu.TouchupRefused):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "V4", "item": 0,
             "fade_in_frames": 40}]})


@pytest.mark.parametrize("row", ["V1"])
def test_entry_motion_refuses_the_comp_bearing_rows(tmp_path, row):
    """V1/V2 treatments belong to the comp pass, which plans them
    whole at build time - the same boundary `add_overlay` keeps.  A
    second treatment the recorded manifest does not know would be
    dropped silently by the next re-derivation."""
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused, match="comp-bearing row"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": row, "item": 0,
             "fade_in_frames": 6}]})
    with pytest.raises(tu.TouchupRefused, match="audio"):
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": [
            {"op": "entry_motion", "row": "A1", "item": 0,
             "fade_in_frames": 6}]})


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


# ── Executing through the registry refuses without the caller's spec ──


def _satisfying_project(tmp_path) -> str:
    """Every requirement `build_reels` declares, satisfied - so the
    only thing left to refuse on is the caller-supplied argument."""
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    state = {"project_folder": str(tmp_path), "step_outputs": {}}
    state[R._FORCE] = {
        "resolve_scripting": True,
        "face_detector": True,
        "reel_build_libraries": True,
    }
    (tmp_path / "pipeline_data.json").write_text(
        json.dumps(state), encoding="utf-8")
    path = transcript_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "segments": [], "segment_count": 0,
        "derived_from": {"duration_seconds": 0.0}}), encoding="utf-8")
    from library.tools.reel_proposal import (
        Approval, ReelMoment, proposal_path, write_proposal)
    write_proposal(
        proposal_path(tmp_path),
        [ReelMoment(number=1, slug="a-witness", reason="a moment",
                    timeline_start=10.0, timeline_end=40.0,
                    approval=Approval.APPROVED)],
        {"derived_from": {"duration_seconds": 60.0}})
    (tmp_path / "project.yaml").write_text(
        "name: fixture\nresolve:\n"
        "  project_name: Fixture Project\n"
        "  timeline_name: Fixture Timeline\n", encoding="utf-8")
    return str(tmp_path)
