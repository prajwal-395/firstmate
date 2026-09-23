"""The touchup gate: what routes, what pays, what refuses.

`library/tools/reel_touchup.py` is the path from a structured change
to `composed_edit.apply_composed_edit`.  These tests drive the
QUALIFICATION - the gate the whole task turns on - plus the composed
path end to end, against the same fake Resolve the composed-edit
tests use (`tests/composed_edit_harness.py`).  Nothing here reaches a
real project or a real Resolve.

What is pinned:

- the five ops qualify to the right class: move / swap_pixels /
  add_overlay / remove_overlay are `composed`; retime is
  `composed_with_rederivation`;
- two structural exclusions: adds to a comp-bearing row (V1/V2)
  refuse, and a move across rows refuses - both name what to state
  instead, before anything is staged;
- the cost statements carry the measured numbers and never the old
  spike ratio: no "50x", no "~112s rebuild";
- anything unclassifiable refuses with its reason: unknown ops,
  vacating continuous rows, undeclared treatments, collisions,
  graded swaps, comp-carrying swaps, manifest mismatches;
- every producer of a refused comp is accounted for: a drawing comp
  refuses the swap, Resolve's own empty auto composition still
  qualifies, an unreadable graph refuses fail-closed;
- grades ride from the approved timeline: a graded retime or move
  keeps its nodes through the composition;
- the `composed` class runs through `apply_composed_edit` with the
  null rederiver and verifies by re-reading the track.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import composed_edit as ce  # noqa: E402
from library.tools import reel_read  # noqa: E402
from library.tools import reel_touchup as tu  # noqa: E402
from tests.composed_edit_harness import build_reel  # noqa: E402


def _tracks(timeline):
    return reel_read.read_tracks(timeline)


def _v4_first_record(timeline):
    tracks = _tracks(timeline)
    for track in tracks:
        if track["type"] == "video" and int(track["index"]) == 4:
            return int(track["clips"][0]["record_in"])
    raise AssertionError("the fixture stopped carrying V4")


# ── The composed class ───────────────────────────────────────────────


def test_move_overlay_qualifies_composed(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "move", "row": "V4", "item": 0, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    assert len(qualification.changes) == 1
    change = qualification.changes[0]
    assert change.record_frame == 1300
    assert not change.played_length_changes
    assert qualification.moves[0]["from_row"] == "V4"


def test_remove_overlay_rewrites_the_row_minus_one(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    assert [ (r["row"], r["item_index"]) for r in
             qualification.removals] == [("V4", 1)]
    # The row is re-placed whole minus the removed item: two
    # zero-length rewrites, no length change anywhere.
    assert len(qualification.changes) == 2
    assert not any(c.played_length_changes
                   for c in qualification.changes)


def test_add_overlay_qualifies_composed_with_declared_treatment(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "add_overlay", "row": "V4", "media": "/lab/card.mov",
         "record": 1300, "duration": 40, "properties": {}}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    assert len(qualification.insertions) == 1


def test_swap_pixels_qualifies_composed(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": "/lab/card.mov"}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    assert len(qualification.removals) == 1
    assert len(qualification.insertions) == 1


# ── The length-changing class ────────────────────────────────────────


def test_retime_qualifies_with_rederivation_and_says_so(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED_WITH_REDERIVATION
    assert "NOT a quick refresh" in qualification.cost_statement
    assert any(c.played_length_changes
               for c in qualification.changes)


def test_retime_to_its_own_length_is_a_noop(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 479}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    assert qualification.changes == []


# ── The cost statements carry measurements, never the old ratio ──────


def test_cost_statements_carry_no_spike_ratio(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    cheap = tu.qualify(_tracks(timeline), {
        "reel": 1, "edits": [{"op": "move", "row": "V4", "item": 0,
                              "to_row": "V4", "to_record": 1300}]})
    costly = tu.qualify(_tracks(timeline), {
        "reel": 1, "edits": [{"op": "retime", "row": "V1", "item": 0,
                              "duration": 492}]})
    for statement in (cheap.cost_statement, costly.cost_statement):
        assert "50x" not in statement
        assert "112" not in statement
    assert "8.9-25.9" in cheap.cost_statement
    assert "19.4-67.1" in cheap.cost_statement
    assert "44.1" in costly.cost_statement


# ── Refusals ─────────────────────────────────────────────────────────


def test_empty_edits_refuse(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline), {"reel": 1, "edits": []})
    assert "names no edits" in str(refusal.value)


def test_unknown_op_refuses_and_names_the_five(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [{"op": "dissolve"}]})
    message = str(refusal.value)
    assert "'dissolve'" in message
    for op in ("move", "swap_pixels", "add_overlay",
               "remove_overlay", "retime"):
        assert op in message


def test_move_from_a_continuous_row_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    for row in ("V1", "V2", "A1"):
        with pytest.raises(tu.TouchupRefused) as refusal:
            tu.qualify(_tracks(timeline),
                       {"reel": 1, "edits": [
                           {"op": "move", "row": row, "item": 0,
                            "to_row": "V4", "to_record": 1300}]})
        assert "continuous program" in str(refusal.value)


def test_remove_from_a_continuous_row_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "remove_overlay", "row": "A1",
                        "item": 0}]})
    assert "continuous program" in str(refusal.value)


def test_move_to_a_row_the_reel_does_not_have_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "move", "row": "V4", "item": 0,
                        "to_row": "V9", "to_record": 1300}]})
    assert "no such row" in str(refusal.value)


def test_move_onto_an_occupied_span_refuses_before_anything(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    first = _v4_first_record(timeline)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "move", "row": "V4", "item": 0,
                        "to_row": "V4", "to_record": first + 100}]})
    assert "collides" in str(refusal.value)


def test_add_overlay_without_properties_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "add_overlay", "row": "V4",
                        "media": "/lab/card.mov", "record": 1300,
                        "duration": 40}]})
    assert "declares no `properties`" in str(refusal.value)


def test_add_overlay_onto_an_occupied_span_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    first = _v4_first_record(timeline)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "add_overlay", "row": "V4",
                        "media": "/lab/card.mov", "record": first,
                        "duration": 40, "properties": {}}]})
    assert "collides" in str(refusal.value)


def test_swap_on_a_comp_carrying_item_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "swap_pixels", "row": "V1", "item": 0,
                        "media": "/lab/opener.mov"}]})
    assert "drawing" in str(refusal.value)


def test_swap_names_a_missing_item_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "swap_pixels", "row": "V4", "item": 9,
                        "media": "/lab/card.mov"}]})
    assert "no item at V4[9]" in str(refusal.value)


def test_retime_to_zero_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "retime", "row": "V1", "item": 0,
                        "duration": 0}]})
    assert "not a trim" in str(refusal.value)


def test_overlapping_plan_refuses(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    first = _v4_first_record(timeline)
    # Two adds at the same free span: each is free against the live
    # read, but together they collide.
    spec = {"reel": 1, "edits": [
        {"op": "add_overlay", "row": "V4", "media": "/lab/card.mov",
         "record": 1300, "duration": 40, "properties": {}},
        {"op": "add_overlay", "row": "V4", "media": "/lab/card.mov",
         "record": 1310, "duration": 40, "properties": {}}]}
    assert first  # the fixture still carries V4; silence unused warnings
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline), spec)
    assert "overlaps on V4" in str(refusal.value)


def test_pool_item_for_a_file_not_on_disk_refuses_without_a_pool():
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.pool_item_for_path(None, "/lab/does-not-exist.mov")
    assert "not on disk" in str(refusal.value)


# ── The manifest check, for the length-changing class ────────────────


def _manifest_for(tracks, per_clip_labels):
    manifest_tracks = {}
    for track in tracks:
        row = ("V" if str(track["type"]).lower().startswith("v")
               else "A") + str(int(track["index"]))
        if not row.startswith("V"):
            continue
        manifest_tracks[row] = {
            "clips": [{"source_file": c["source_file"],
                       "label": f"{row}-{i}"}
                      for i, c in enumerate(track["clips"])]}
    return {"tracks": manifest_tracks,
            "fusion_effects": {"per_clip": {
                label: {} for label in per_clip_labels}}}


def test_diverged_manifest_refuses_and_names_rebuild(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    tracks = _tracks(timeline)
    manifest = _manifest_for(tracks, [])
    manifest["tracks"]["V1"]["clips"] = manifest["tracks"]["V1"][
        "clips"][:1]
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.check_manifest_matches(manifest, tracks)
    message = str(refusal.value)
    assert "V1" in message
    assert "build-reels" in message


# ── The null rederiver is not a bypass ───────────────────────────────


def test_null_rederiver_refuses_a_trim_defensively(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    changes = ce.plan_ripple(_tracks(timeline), 1069, 13)
    assert any(c.played_length_changes for c in changes)
    assert tu._NullRederiver("test").reachable_reason(changes) is not None


def test_null_rederiver_passes_a_pure_shift(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    changes = [c for c in ce.plan_ripple(_tracks(timeline), 1069, 13)
               if not c.played_length_changes and c.row == "V4"]
    assert changes
    rederiver = tu._NullRederiver("test")
    assert rederiver.reachable_reason(changes) is None
    receipt = rederiver.rederive(changes)
    assert receipt["ran"] is True and receipt["ok"] is True
    assert receipt["comp_pass"] == "skipped"


# ── The composed class through the module, verified by re-read ──────


def _null(tmp_path):
    return tu._NullRederiver("test: nothing to re-derive")


def test_move_runs_through_composed_edit_and_verifies(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "move", "row": "V4", "item": 0, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool,
        changes=qualification.changes, comp_dir=str(tmp_path / "c"),
        withheld_dir=str(tmp_path / "w"), rederiver=_null(tmp_path))
    assert receipt.verified["landed"] == 1
    after = reel_read.read_tracks(timeline)
    v4 = next(t for t in after
              if t["type"] == "video" and int(t["index"]) == 4)
    starts = sorted(c["record_in"] for c in v4["clips"])
    assert 1300 in starts
    assert len(starts) == 3


def test_remove_runs_through_composed_edit_and_verifies(tmp_path):
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    # The composition only deletes what it re-places, so the target
    # is pre-deleted on the staging copy first - and the plan's
    # positional indexes are re-keyed off the fresh read, the same
    # order `apply_touchup` runs.
    pre = tu._pre_delete_removed(timeline, qualification.removals)
    assert pre["asked"] == 1
    changes = tu._rekey_changes(_tracks(timeline), qualification)
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        comp_dir=str(tmp_path / "c"),
        withheld_dir=str(tmp_path / "w"), rederiver=_null(tmp_path))
    assert receipt.verified["landed"] == 2
    after = reel_read.read_tracks(timeline)
    v4 = next(t for t in after
              if t["type"] == "video" and int(t["index"]) == 4)
    assert len(v4["clips"]) == 2


def test_length_change_without_a_generator_still_refuses(tmp_path):
    """The gate's class must agree with the module's refusal."""
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert (qualification.gate_class
            == tu.COMPOSED_WITH_REDERIVATION)
    with pytest.raises(ce.CompRederivationUnreachable):
        ce.apply_composed_edit(
            timeline=timeline, media_pool=pool,
            changes=qualification.changes,
            comp_dir=str(tmp_path / "c"),
            withheld_dir=str(tmp_path / "w"), rederiver=None)
    assert timeline.delete_calls == []


class _StubFolder:
    """The one pool lookup the touchup needs: clips by full path."""

    def __init__(self, clips):
        self._clips = list(clips)

    def GetClipList(self):
        return list(self._clips)

    def GetSubFolderList(self):
        return []


class _StubPool:
    def __init__(self, clips):
        self._folder = _StubFolder(clips)

    def GetRootFolder(self):
        return self._folder


def test_resolve_insertions_carries_the_old_treatment(tmp_path):
    """A swap declares the replaced item's own transform, not a guess."""
    from tests.composed_edit_harness import FakeMediaPoolItem

    timeline, _pool, _media = build_reel(tmp_path)
    pixels = tmp_path / "rerender.mov"
    pixels.write_bytes(b"fake-rendered-overlay")
    spec = {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": str(pixels)}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    stub = _StubPool([FakeMediaPoolItem(str(pixels), frames=200)])
    resolved = tu._resolve_insertions(stub, timeline,
                                      qualification.insertions)
    assert len(resolved) == 1
    assert dict(resolved[0].properties), (
        "the carried treatment is empty - the swap would place at "
        "identity beside treated neighbours")


def test_swap_runs_through_composed_edit_and_verifies(tmp_path):
    from tests.composed_edit_harness import FakeMediaPoolItem

    timeline, pool, _media = build_reel(tmp_path)
    pixels = tmp_path / "rerender.mov"
    pixels.write_bytes(b"fake-rendered-overlay")
    spec = {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": str(pixels)}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    stub = _StubPool(
        [FakeMediaPoolItem(str(pixels), frames=200)])
    insertions = tu._resolve_insertions(stub, timeline,
                                        qualification.insertions)
    tu._pre_delete_removed(timeline, qualification.removals)
    changes = tu._rekey_changes(_tracks(timeline), qualification)
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool,
        changes=changes, insertions=insertions,
        comp_dir=str(tmp_path / "c"),
        withheld_dir=str(tmp_path / "w"), rederiver=_null(tmp_path))
    assert receipt.verified["landed"] == 1
    after = reel_read.read_tracks(timeline)
    v4 = next(t for t in after
              if t["type"] == "video" and int(t["index"]) == 4)
    assert len(v4["clips"]) == 3


def test_remove_plus_move_on_one_row_rekeys_and_verifies(tmp_path):
    """Indexes planned before the pre-delete are re-seated after it."""
    timeline, pool, _media = build_reel(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1},
        {"op": "move", "row": "V4", "item": 2, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    tu._pre_delete_removed(timeline, qualification.removals)
    changes = tu._rekey_changes(_tracks(timeline), qualification)
    # V4[2]'s move was planned at index 2; the pre-delete of V4[1]
    # re-seated it to 1, and the rekey says so.
    moved = [c for c in changes if c.record_frame == 1300]
    assert len(moved) == 1 and moved[0].item_index == 1
    receipt = ce.apply_composed_edit(
        timeline=timeline, media_pool=pool, changes=changes,
        comp_dir=str(tmp_path / "c"),
        withheld_dir=str(tmp_path / "w"), rederiver=_null(tmp_path))
    after = reel_read.read_tracks(timeline)
    v4 = next(t for t in after
              if t["type"] == "video" and int(t["index"]) == 4)
    assert len(v4["clips"]) == 2
    assert 1300 in [c["record_in"] for c in v4["clips"]]


def test_two_edits_on_one_item_refuse(tmp_path):
    """A rewrite and a ripple shift of one item is a double place."""
    timeline, _pool, _media = build_reel(tmp_path)
    # Retime V4[0] (+10f) ripples a SHIFT onto V4[1..2]; removing
    # V4[1] in the same spec rewrites V4[2] back to its old span.
    # Both plans claim V4[2] - the gate refuses rather than placing
    # it twice.
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V4", "item": 0, "duration": 50},
        {"op": "remove_overlay", "row": "V4", "item": 1}]}
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline), spec)
    assert "same item" in str(refusal.value)


def test_pre_delete_refuses_when_the_target_is_not_there(tmp_path):
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupError) as refusal:
        tu._pre_delete_removed(
            timeline, [{"row": "V4", "item_index": 0,
                        "record_frame": 4242, "duration": 40,
                        "why": "test"}])
    assert "does not hold" in str(refusal.value)
    assert timeline.delete_calls == []


# ── Structural exclusions: named, before anything is staged ───────────


def test_add_to_a_comp_row_refuses_and_names_rebuild(tmp_path):
    """A new item on V1/V2 would land with no treatment comp.

    Every clip on a comp-bearing row carries one, and a newly placed
    item has no manifest spec for the pass to key one to - so the
    null path would sail it through comp-less beside treated
    neighbours, and it would render looking like a choice. Excluded
    by name: the rebuild plans the new clip with its treatment.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    for row in ("V1", "V2"):
        with pytest.raises(tu.TouchupRefused) as refusal:
            tu.qualify(_tracks(timeline),
                       {"reel": 1, "edits": [
                           {"op": "add_overlay", "row": row,
                            "media": "/lab/card.mov", "record": 2000,
                            "duration": 24, "properties": {}}]})
        message = str(refusal.value)
        assert row in message
        assert "build-reels" in message
    # The overlay rows stay servable: the refusal is about the row,
    # not about adds.
    qualified = tu.qualify(_tracks(timeline),
                           {"reel": 1, "edits": [
                               {"op": "add_overlay", "row": "V4",
                                "media": "/lab/card.mov", "record": 1300,
                                "duration": 40, "properties": {}}]})
    assert qualified.gate_class == tu.COMPOSED


def test_cross_row_move_refuses_and_names_remove_add(tmp_path):
    """The composition addresses a delete by (row, position).

    A move keyed by its target row but its source position would
    capture whatever sits at that position on the target row -
    deleting a bystander while duplicating the moved item. So the
    gate refuses the cross-row move before anything is staged, and
    names the two edits that state the carrying explicitly.
    """
    timeline, _pool, _media = build_reel(tmp_path)
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "move", "row": "V4", "item": 0,
                        "to_row": "V3", "to_record": 1300}]})
    message = str(refusal.value)
    assert "across rows" in message
    assert "remove_overlay" in message and "add_overlay" in message
    # Same-row repositioning - the move the gate serves - is untouched.
    qualified = tu.qualify(_tracks(timeline),
                           {"reel": 1, "edits": [
                               {"op": "move", "row": "V4", "item": 0,
                                "to_row": "V4", "to_record": 1300}]})
    assert qualified.gate_class == tu.COMPOSED


# ── Every producer of a refused comp, accounted for ──────────────────


def test_swap_on_an_empty_auto_comp_still_qualifies(tmp_path):
    """Resolve gives every plain item its own empty composition.

    That deterministically-produced comp draws nothing, so it is not
    a treatment and the swap refusal written against builder comps
    must not catch it - a gate that failed this correct output would
    be no better than one that cannot fail (AGENTS.md 10.4).
    """
    from tests.composed_edit_harness import (
        FakeComp, FakeTool, covering_window)

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].comps = [FakeComp({
        "MediaIn1": FakeTool("MediaIn", covering_window(40, 0, 200)),
        "MediaOut1": FakeTool("MediaOut", {}),
        "AudioDisplay1": FakeTool("AudioDisplay", {})})]
    assert timeline.rows["V4"][0].GetFusionCompCount() == 1

    qualification = tu.qualify(_tracks(timeline),
                               {"reel": 1, "edits": [
                                   {"op": "swap_pixels", "row": "V4",
                                    "item": 0,
                                    "media": "/lab/card.mov"}]})
    assert qualification.gate_class == tu.COMPOSED


def test_swap_on_an_unreadable_comp_refuses(tmp_path):
    """Fail closed: an unreadable graph is not evidence of an empty one."""
    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].comps = [object()]  # answers no getter
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu.qualify(_tracks(timeline),
                   {"reel": 1, "edits": [
                       {"op": "swap_pixels", "row": "V4", "item": 0,
                        "media": "/lab/card.mov"}]})
    assert "drawing" in str(refusal.value)


def test_graded_swap_refuses_at_resolve(tmp_path):
    """An Insertion cannot take a grade from an item being deleted.

    The V3 overlay carries no comp, so it passes qualify - and the
    resolve then refuses its 8-node grade by name, before anything
    is staged. The legitimate producer - Resolve's own default
    single node - is what every qualifying swap test above rides on.
    """
    from tests.composed_edit_harness import FakeMediaPoolItem

    timeline, _pool, _media = build_reel(tmp_path)
    timeline.rows["V4"][0].nodes = 8  # graded, but no drawing comp
    pixels = tmp_path / "graded-swap.mov"
    pixels.write_bytes(b"fake-rendered-overlay")
    spec = {"reel": 1, "edits": [
        {"op": "swap_pixels", "row": "V4", "item": 0,
         "media": str(pixels)}]}
    qualification = tu.qualify(_tracks(timeline), spec)
    assert qualification.gate_class == tu.COMPOSED
    stub = _StubPool([FakeMediaPoolItem(str(pixels), frames=200)])
    with pytest.raises(tu.TouchupRefused) as refusal:
        tu._resolve_insertions(stub, timeline,
                               qualification.insertions)
    assert "colour grade" in str(refusal.value)


# ── Grades ride from the approved timeline ───────────────────────────


class _Rederiver(ce.CompRederiver):
    """Re-keys covering comps for length-changed comp items, as the
    builder's pass does (it reads played lengths off the live items)."""

    def __init__(self, timeline):
        self.timeline = timeline

    def reachable_reason(self, changes):
        return None

    def rederive(self, changes):
        from tests.composed_edit_harness import FakeItem, covering_window
        for change in changes:
            if not (change.played_length_changes and change.comp_count):
                continue
            row = self.timeline.rows[change.row]
            item = next(i for i in row
                        if i.GetStart() == change.record_frame)
            item.comps = FakeItem(
                item.mpi, item.start, item.duration, item.left_offset,
                comp_windows=[covering_window(
                    item.duration, item.left_offset,
                    item.mpi.frames)]).comps
        return {"ran": True, "ok": True}

    def expects_comp(self, row, record_frame):
        return False


def _staged_pair(tmp_path):
    """Approved + staging timelines with a pool bound to the staging.

    Mirrors `_edit_staged`: the plan qualifies off the staging read,
    the grades resolve off the approved reel, the composition runs on
    the staging copy.
    """
    from tests.composed_edit_harness import FakeMediaPool, duplicate
    approved, _pool, media = build_reel(tmp_path)
    staged = duplicate(approved)
    return approved, staged, FakeMediaPool(staged), media


def test_grade_sources_map_by_pre_edit_span(tmp_path):
    approved, staged, _pool, _media = _staged_pair(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(staged), spec)
    changes = tu._rekey_changes(_tracks(staged), qualification)
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    head = next(c for c in changes
                if (c.row, c.item_index) == ("V1", 0))
    assert grades[("V1", 0)] is approved.rows["V1"][0]
    assert head.previous_record == approved.rows["V1"][0].GetStart()

    spec = {"reel": 1, "edits": [
        {"op": "remove_overlay", "row": "V4", "item": 1},
        {"op": "move", "row": "V4", "item": 2, "to_row": "V4",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(staged), spec)
    tu._pre_delete_removed(staged, qualification.removals)
    changes = tu._rekey_changes(_tracks(staged), qualification)
    # The rekey re-points the move at its re-seated change object.
    assert qualification.moves[0]["change"] in changes
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    moved = next(c for c in changes if c.record_frame == 1300)
    assert moved.item_index == 1  # re-seated past the pre-delete
    assert grades[(moved.row, moved.item_index)] is \
        approved.rows["V4"][2]


def test_retime_keeps_its_grade_through_the_qualified_plan(tmp_path):
    """A graded retime without the carry renders de-graded: 8 nodes in,
    one node out. The wiring carries each re-placed item's grade from
    the approved reel and the restore judges it by read-back."""
    approved, staged, pool, _media = _staged_pair(tmp_path)
    spec = {"reel": 1, "edits": [
        {"op": "retime", "row": "V1", "item": 0, "duration": 492}]}
    qualification = tu.qualify(_tracks(staged), spec)
    assert qualification.gate_class == tu.COMPOSED_WITH_REDERIVATION
    tu._pre_delete_removed(staged, qualification.removals)
    changes = tu._rekey_changes(_tracks(staged), qualification)
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    receipt = ce.apply_composed_edit(
        timeline=staged, media_pool=pool, changes=changes,
        comp_dir=str(tmp_path / "c"), withheld_dir=str(tmp_path / "w"),
        rederiver=_Rederiver(staged), grade_sources=grades)

    head = next(i for i in staged.rows["V1"] if i.GetStart() == 590)
    assert head.GetDuration() == 492
    assert head.GetNumNodes() == 8, "the retimed head lost its grade"
    assert head.GetProperty("ZoomX") == 2.307
    assert receipt.rederived["verified"]["checked"] == 1
    # The approved reel stands untouched behind the staging.
    assert approved.rows["V1"][0].GetDuration() == 479
    assert approved.rows["V1"][0].GetNumNodes() == 8


def test_move_of_a_graded_overlay_keeps_its_grade(tmp_path):
    approved, staged, pool, _media = _staged_pair(tmp_path)
    approved.rows["V3"][0].nodes = 8
    staged.rows["V3"][0].nodes = 8
    spec = {"reel": 1, "edits": [
        {"op": "move", "row": "V3", "item": 0, "to_row": "V3",
         "to_record": 1300}]}
    qualification = tu.qualify(_tracks(staged), spec)
    assert qualification.gate_class == tu.COMPOSED
    changes = tu._rekey_changes(_tracks(staged), qualification)
    grades = tu._grade_sources_for(approved, changes,
                                   qualification.moves)
    ce.apply_composed_edit(
        timeline=staged, media_pool=pool, changes=changes,
        comp_dir=str(tmp_path / "c"), withheld_dir=str(tmp_path / "w"),
        rederiver=tu._NullRederiver("test"), grade_sources=grades)
    moved = next(i for i in staged.rows["V3"]
                 if i.GetStart() == 1300)
    assert moved.GetNumNodes() == 8, "the moved overlay lost its grade"
