"""An EditPatch commits against the generation it was planned on, or not at all.
"""
from __future__ import annotations
from dataclasses import replace
import pytest
import time
from library.tools import capabilities, edit_patch, patch_algebra
from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from library.processes.edit_video import run_pipeline as runner
from library.tools import edit_input_digest as digest
from library.tools import run_control
import shutil
import subprocess
from library.tools import dirty_regions as d
from library.tools import render_qa as q


@pytest.fixture
def world(tmp_path):
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        yield project, timeline, item, store, base


def _patch(pid, base, operations, domains, spans, **extra):
    return {"id": pid, "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base, "capability": "reel.touchup",
            "affected_spans": spans, "conflict_domains": domains,
            "operations": operations, **extra}


def _apply(patch, project, timeline, store):
    return edit_patch.apply_patch(patch, resolve=FakeResolve(project),
                                  project=project, timeline=timeline,
                                  store=store,
                                  project_folder=store.path.parent)


def test_a_patch_commits_and_becomes_the_next_generation(world):
    project, timeline, item, store, base = world
    patch = _patch("p1", base.generation, [
        {"op": "marker.add", "frame": 12, "color": "Blue", "name": "beat"},
        {"op": "clip.set_enabled", "unique_id": item.GetUniqueId(),
         "enabled": False},
    ], ["markers", "timeline_structure"], [[0, 48]],
        preconditions=[{"kind": "marker_absent", "frame": 12}],
        postconditions=[{"kind": "duration_unchanged"}])
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "committed", receipt
    assert receipt["generation"] == base.generation + 1
    head = store.head("Podcast", base.timeline_id)
    assert head.source == shadow.PATCH and head.patch["id"] == "p1"
    assert head.receipt["status"] == "committed"
    assert item.GetClipEnabled() is False
    # A retry of the same id is answered from the record, not re-applied.
    assert _apply(patch, project, timeline, store) == receipt
    assert store.head("Podcast", base.timeline_id).generation == \
        receipt["generation"]


def test_reusing_a_patch_id_with_different_contents_refuses(world):
    project, timeline, _item, store, base = world
    first = _patch("stable-id", base.generation, [
        {"op": "marker.add", "frame": 12, "color": "Blue", "name": "beat"}],
        ["markers"], [[12, 13]])
    _apply(first, project, timeline, store)

    changed = _patch("stable-id", base.generation, [
        {"op": "marker.add", "frame": 13, "color": "Blue", "name": "beat"}],
        ["markers"], [[13, 14]])
    with pytest.raises(edit_patch.PatchRefused,
                       match="already committed with different contents"):
        _apply(changed, project, timeline, store)
    assert 12 in timeline.markers and 13 not in timeline.markers


def test_live_patch_retry_is_idempotent_and_changed_reuse_refuses(world):
    project, timeline, _item, store, _base = world
    operation = {"op": "marker.add", "frame": 12, "color": "Blue",
                 "name": "beat"}
    kwargs = {
        "project": project, "timeline": timeline,
        "capability": "reel.touchup", "operations": [operation],
        "conflict_domains": ("markers",),
        "idempotency_key": "stable-live-patch-id",
        "preconditions": [{"kind": "marker_absent", "frame": 12}],
        "postconditions": [{"kind": "duration_unchanged"}],
        "store": store,
    }
    receipt = edit_patch.apply_live_patch(**kwargs)
    assert receipt["status"] == "committed", receipt
    assert edit_patch.apply_live_patch(**kwargs) == receipt
    assert store.head("Podcast", receipt["readback"]["timeline_id"]
                      ).generation == receipt["generation"]

    changed = {**kwargs, "operations": [{**operation, "frame": 13}],
               "preconditions": [{"kind": "marker_absent", "frame": 13}]}
    with pytest.raises(edit_patch.PatchRefused,
                       match="already used with different contents"):
        edit_patch.apply_live_patch(**changed)
    assert 12 in timeline.markers and 13 not in timeline.markers


def test_a_clip_insert_places_a_pool_clip_and_commits_as_a_generation(world):
    """Defect: the reel build's placement bypassed EditPatch, so the most
    common edit Ren makes produced no generation receipt and could not be
    rebased. A `clip.insert` through `apply_live_patch` places the clip and
    commits a verified generation."""
    project, timeline, _item, store, base = world
    pool_clip = make_pool_clip("card.mov")
    project.GetMediaPool().GetRootFolder()._clips.append(pool_clip)
    operation = {
        "op": "clip.insert",
        "unique_id": "card:Reel 09:head-card",
        "track_type": "video",
        "track_index": 1,
        "record_in": 100,
        "record_out": 148,
        "media_pool_item": str(pool_clip.GetUniqueId()),
        "source_in": 0,
        "source_out": 48,
    }
    receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability="reel.build",
        operations=[operation], conflict_domains=("timeline_structure",),
        idempotency_key="reel-build:Reel 09:card:head-card",
        store=store)
    assert receipt["status"] == "committed", receipt
    assert receipt["generation"] == base.generation + 1
    head = store.head("Podcast", receipt["readback"]["timeline_id"])
    placed = [c for c in shadow.clips(store.snapshot(head))
              if c["record_in"] == 100 and c["record_out"] == 148]
    assert len(placed) == 1


def test_a_clip_set_property_addresses_a_just_placed_clip_by_its_client_id(world):
    """Defect: a caption's `clip.set_property` follows its `clip.insert` in
    one patch, but the caller cannot know the Resolve-assigned unique id
    before the insert runs. The apply loop must resolve the client-chosen
    id to the Resolve-assigned one, or the transform write addresses a
    clip that is not on the timeline."""
    project, timeline, _item, store, base = world
    pool_clip = make_pool_clip("caption.mov")
    project.GetMediaPool().GetRootFolder()._clips.append(pool_clip)
    client_id = "caption:0:sub_block_0"
    operations = [
        {"op": "clip.insert", "unique_id": client_id,
         "track_type": "video", "track_index": 2,
         "record_in": 10, "record_out": 40,
         "media_pool_item": str(pool_clip.GetUniqueId()),
         "source_in": 0, "source_out": 30},
        {"op": "clip.set_property", "unique_id": client_id,
         "key": "Tilt", "value": -870.0},
    ]
    receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability="render.build",
        operations=operations,
        conflict_domains=("timeline_structure", "picture_transform"),
        idempotency_key="render-build:caption:0:sub_block_0",
        store=store, project_folder=store.path.parent)
    assert receipt["status"] == "committed", receipt
    head = store.head("Podcast", receipt["readback"]["timeline_id"])
    placed = [c for c in shadow.clips(store.snapshot(head))
              if c["record_in"] == 10 and c["record_out"] == 40]
    assert len(placed) == 1
    assert placed[0]["transform"]["Tilt"] == -870.0


def test_a_write_that_answers_true_and_changes_nothing_fails_by_readback(
        world, monkeypatch):
    project, timeline, item, store, base = world
    monkeypatch.setattr(type(item), "SetProperty", lambda self, k, v: True)
    receipt = _apply(_patch("p1", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.4}], ["picture_transform"], [[0, 48]]),
        project, timeline, store)
    assert receipt["status"] == "verification_failed"
    assert "ZoomX=1.0" in receipt["operations"][0]["failure"]
    # The shadow records what Resolve holds, not what was planned.
    assert receipt["generation"] == base.generation + 1


def test_clip_marker_writes_commit_as_generations(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    marker = {"unique_id": uid, "frame": 12, "color": "Green",
              "name": "reply", "note": "answered", "duration": 3,
              "custom_data": "reply-record"}
    added = _apply(_patch("clip-marker-add", base.generation, [
        {"op": "clip_marker.add", **marker}], ["markers"], [[0, 48]],
        preconditions=[{"kind": "clip_marker_absent", "unique_id": uid,
                        "frame": 12}]), project, timeline, store)
    assert added["status"] == "committed", added
    back = item.GetMarkers()[12]
    assert back == {"color": "Green", "name": "reply", "note": "answered",
                    "duration": 3, "customData": "reply-record"}

    deleted = _apply(_patch("clip-marker-delete", added["generation"], [
        {"op": "clip_marker.delete", "unique_id": uid, "frame": 12}],
        ["markers"], [[0, 48]], preconditions=[{
            "kind": "clip_marker_present", "unique_id": uid, **marker,
        }]), project, timeline, store)
    assert deleted["status"] == "committed", deleted
    assert 12 not in item.GetMarkers()


def test_custom_data_marker_delete_keeps_timeline_and_clip_targets_distinct(
        world, monkeypatch):
    from library.tools import marker_resolution

    project, timeline, item, store, _base = world
    monkeypatch.setenv("REN_SHADOW_DB", str(store.path))
    assert item.AddMarker(12, "Blue", "question", "answer me", 1,
                          "clip-record")
    outcome = marker_resolution.delete_marker_by_custom_data(
        project, timeline, item, "clip-record",
        idempotency_key="clip-custom-data-delete")
    assert outcome["removed"] is True, outcome
    assert 12 not in item.GetMarkers()
    assert 12 not in timeline.GetMarkers()


def test_a_local_delete_retries_once_only_if_the_item_still_reads_back(world):
    project, timeline, item, store, base = world
    original = timeline.DeleteClips
    calls = []

    def flaky(items, ripple):
        calls.append((list(items), ripple))
        if len(calls) == 1:
            return False
        return original(items, ripple)

    timeline.DeleteClips = flaky
    receipt = _apply(_patch("delete-flaky", base.generation, [
        {"op": "clip.delete", "unique_id": item.GetUniqueId()}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert receipt["status"] == "committed", receipt
    assert len(calls) == 2
    assert all(ripple is False for _, ripple in calls)
    assert all(len(items) == 1 for items, _ in calls)


def test_smart_reframe_is_a_measured_transform_patch(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    before = {key: item.GetProperty().get(key)
              for key in edit_patch.SMART_REFRAME_KEYS}

    def reframe():
        item._props["Pan"] = 1.5
        return True

    item.SmartReframe = reframe
    patch = _patch("smart-reframe", base.generation, [{
        "op": "clip.smart_reframe", "unique_id": uid,
        "keys": list(edit_patch.SMART_REFRAME_KEYS), "before": before,
    }], ["picture_transform"], [[0, 48]],
        capability="reel.touchup", preconditions=[{
        "kind": "clip_transform_equals", "unique_id": uid,
        "properties": before,
    }])
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "committed", receipt
    assert item.GetProperty("Pan") == 1.5


def test_smart_reframe_true_without_a_transform_change_fails_readback(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    before = {key: item.GetProperty().get(key)
              for key in edit_patch.SMART_REFRAME_KEYS}
    item.SmartReframe = lambda: True
    patch = _patch("smart-reframe-noop", base.generation, [{
        "op": "clip.smart_reframe", "unique_id": uid,
        "keys": list(edit_patch.SMART_REFRAME_KEYS), "before": before,
    }], ["picture_transform"], [[0, 48]],
        capability="reel.touchup", preconditions=[{
        "kind": "clip_transform_equals", "unique_id": uid,
        "properties": before,
    }])
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "verification_failed"
    assert "unchanged" in receipt["operations"][0]["failure"]


def test_a_hand_edit_since_the_base_refuses_and_is_recorded(world):
    project, timeline, _item, store, base = world
    timeline.AddMarker(30, "Red", "captain", "", 1)
    with pytest.raises(edit_patch.StalePatch) as stale:
        _apply(_patch("p1", base.generation, [
            {"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
            ["markers"], [[0, 10]]), project, timeline, store)
    assert stale.value.observed and not stale.value.rebase_possible
    head = store.head("Podcast", base.timeline_id)
    assert head.source == shadow.OBSERVED
    assert 5 not in timeline.markers


def test_a_stale_patch_rebases_past_patches_whose_writes_do_not_meet(world):
    project, timeline, _item, store, base = world
    _apply(_patch("markers", base.generation, [
        {"op": "marker.add", "frame": 40, "color": "Blue", "name": "m"}],
        ["markers"], [[40, 41]]), project, timeline, store)

    # Same domain, overlapping spans, another frame: it commutes, where
    # "both touch markers here" used to refuse it.
    commuting = _patch("marker-20", base.generation, [
        {"op": "marker.add", "frame": 20, "color": "Red", "name": "n"}],
        ["markers"], [[0, 48]])
    with pytest.raises(edit_patch.StalePatch) as stale:
        _apply(commuting, project, timeline, store)
    assert stale.value.rebase_possible
    moved = edit_patch.rebase(commuting, store)
    assert _apply(moved.to_dict(), project, timeline,
                  store)["status"] == "committed"

    meeting = _patch("marker-too", base.generation, [
        {"op": "marker.delete", "frame": 40}], ["markers"], [[40, 41]])
    # The pair also meets on ('marker', 40) under `exclusive`, but the
    # ripple branch is the temporal pre-check and answers first: the
    # delete's frame address is past where the add's patch ripples from.
    with pytest.raises(edit_patch.StalePatch,
                       match=r"a frame does not move with the cut"):
        edit_patch.rebase(meeting, store)


def test_the_same_write_merges_and_a_different_one_is_a_lost_update(world):
    project, timeline, item, store, base = world

    def zoom(pid, value):
        return _patch(pid, base.generation, [
            {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
             "key": "ZoomX", "value": value}], ["picture_transform"],
            [[0, 48]], capability="reel.set_properties")

    _apply(zoom("first", 1.2), project, timeline, store)
    assert _apply(edit_patch.rebase(zoom("same", 1.2), store).to_dict(),
                  project, timeline, store)["status"] == "committed"
    with pytest.raises(edit_patch.StalePatch, match="lost update"):
        edit_patch.rebase(zoom("other", 1.4), store)


def test_a_frame_addressed_write_past_a_ripple_conflicts(world, monkeypatch):
    _project, _timeline, item, store, base = world
    snap = store.snapshot(base)
    specs = tuple(
        replace(spec, execution=replace(
            spec.execution,
            patch=replace(spec.execution.patch, temporal_effect="ripple")))
        if spec.id == "reel.touchup" else spec
        for spec in capabilities.all())
    monkeypatch.setattr(capabilities, "all", lambda: specs)
    ripple = _patch("cut", base.generation, [
        {"op": "marker.add", "frame": 10, "color": "Blue", "name": "c"}],
        ["markers"], [[10, 20]])
    marker = _patch("m", base.generation, [
        {"op": "marker.add", "frame": 30, "color": "Red", "name": "m"}],
        ["markers"], [[30, 31]])
    by_id = _patch("z", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.2}], ["picture_transform"], [[0, 48]])
    assert patch_algebra.compose(ripple, marker, snap).verdict == \
        patch_algebra.CONFLICT
    assert patch_algebra.compose(ripple, by_id, snap).verdict == \
        patch_algebra.REBASE


def test_a_clip_addressed_write_past_a_ripple_rebases_through_carry(world):
    """Defect: a ripple edit composed with a later frame-addressed edit
    conflicts even when the later edit's target is a clip that did move
    - `rebase` was dead code, reachable only by monkeypatching the
    registry, because no capability declared ripple. Now `reel.touchup`
    declares it and `carry` re-spans the later patch onto the head.
    """
    project, timeline, item, store, base = world
    ripple = _patch("cut", base.generation, [
        {"op": "marker.add", "frame": 10, "color": "Blue", "name": "c"}],
        ["markers"], [[10, 20]])
    receipt = _apply(ripple, project, timeline, store)
    assert receipt["status"] == "committed", receipt

    by_id = _patch("z", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.2}], ["picture_transform"], [[0, 48]])
    moved, reason = patch_algebra.carry(
        edit_patch.EditPatch.from_dict(by_id), store, "Podcast",
        base.timeline_id)
    assert moved is not None, reason
    assert moved.affected_spans == ((0, 48),)
    assert _apply(moved, project, timeline, store)["status"] == "committed"
    assert item.GetProperty("ZoomX") == 1.2

    marker = _patch("m", base.generation, [
        {"op": "marker.add", "frame": 30, "color": "Red", "name": "m"}],
        ["markers"], [[30, 31]])
    moved, reason = patch_algebra.carry(
        edit_patch.EditPatch.from_dict(marker), store, "Podcast",
        base.timeline_id)
    assert moved is None
    assert "a frame does not move with the cut" in reason


def test_compose_and_carry_stay_under_a_millisecond_per_op_pair(world):
    """Defect: the scheduler's critical path - `compose` and `carry` -
    costing more than the Resolve hold they plan. The gap's performance
    requirement: < 1 ms per op pair, pure Python, 1000 composed pairs.
    """
    project, timeline, item, store, base = world
    snap = store.snapshot(base)
    ripple = _patch("cut", base.generation, [
        {"op": "marker.add", "frame": 10, "color": "Blue", "name": "c"}],
        ["markers"], [[10, 20]])
    laters = [_patch(f"m{i}", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.2}], ["picture_transform"], [[0, 48]])
        for i in range(1000)]

    start = time.monotonic()
    for later in laters:
        patch_algebra.compose(ripple, later, snap)
    per_compose = (time.monotonic() - start) / len(laters)
    assert per_compose < 0.001, per_compose

    assert _apply(ripple, project, timeline, store)["status"] == "committed"
    start = time.monotonic()
    for later in laters:
        moved, reason = patch_algebra.carry(
            edit_patch.EditPatch.from_dict(later), store, "Podcast",
            base.timeline_id)
        assert moved is not None, reason
    per_carry = (time.monotonic() - start) / len(laters)
    assert per_carry < 0.001, per_carry


def test_a_declaration_looser_than_its_operations_is_caught():
    loose = {"reel.set_properties": capabilities.PatchSemantics(
        operations=("clip.set_property", "marker.add"),
        conflict_domains=("picture_transform",),
        temporal_effect="local", merge_semantics="commutative")}
    found = "\n".join(patch_algebra.problems(
        loose, capability_ids={"reel.set_properties"}))
    assert "lost update" in found
    assert "writes 'markers', which it does not declare" in found


@pytest.mark.parametrize("operations,domains,spans,why", [
    ([{"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
     ["picture_transform"], [[0, 10]], "does not declare"),
    ([{"op": "marker.add", "frame": 50, "color": "Blue", "name": "x"}],
     ["markers"], [[0, 10]], "outside the declared spans"),
    ([{"op": "clip.delete", "unique_id": "nobody"}],
     ["timeline_structure"], [[0, 48]], "not exactly once"),
])
def test_a_patch_that_says_less_than_it_does_is_refused_before_resolve(
        world, operations, domains, spans, why):
    project, timeline, _item, store, base = world
    with pytest.raises(edit_patch.PatchRefused, match=why):
        _apply(_patch("p", base.generation, operations, domains, spans),
               project, timeline, store)
    assert store.head("Podcast", base.timeline_id).generation == \
        base.generation


@pytest.mark.parametrize("capability,why", [
    ("reel.set_properties", "beyond what"),
    ("footage.scan", "declares no patch semantics"),
])
def test_a_patch_may_not_say_more_than_its_capability_declares(
        world, capability, why):
    project, timeline, _item, store, base = world
    with pytest.raises(edit_patch.PatchRefused, match=why):
        _apply(_patch("p", base.generation, [
             {"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
             ["markers"], [[0, 10]], capability=capability),
             project, timeline, store)


# --------------------------------------------------------------------------
# The edit algebra's patch-executor ops.
#
# The algebra (library/tools/edit_algebra.py) owns the union vocabulary;
# edit_patch.OPERATIONS is a view over it. These tests pin the view and
# the new ops' commit-and-invert behaviour against the double.

def test_the_patch_view_is_the_algebra_patch_executor_ops():
    """Defect: a patch op the algebra does not declare, or an algebra op
    with a native apply the view drops - the two would drift apart."""
    from library.tools import edit_algebra
    view = set(edit_patch.OPERATIONS)
    assert view == set(edit_algebra.patch_op_ids())
    for op_id in view:
        assert edit_algebra.get(op_id) is not None


def test_track_add_delete_and_rename_commit_and_invert(world):
    """Defect: a track row that cannot be added, deleted and renamed in
    place - the review's `track ops` would have no precise reversible op."""
    project, timeline, _item, store, base = world

    added = _apply(_patch("track-add", base.generation, [
        {"op": "track.add", "track_type": "video", "name": "Overlay"}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert added["status"] == "committed", added
    assert timeline.GetTrackName("video", 2) == "Overlay"

    renamed = _apply(_patch("track-rename", added["generation"], [
        {"op": "track.rename", "track_type": "video", "track_index": 2,
         "name": "Graphics"}], ["timeline_structure"], [[0, 48]]),
        project, timeline, store)
    assert renamed["status"] == "committed", renamed
    assert timeline.GetTrackName("video", 2) == "Graphics"

    # The inverse of a rename carries the previous name.
    unrenamed = _apply(_patch("track-unrename", renamed["generation"], [
        {"op": "track.rename", "track_type": "video", "track_index": 2,
         "name": "Overlay"}], ["timeline_structure"], [[0, 48]]),
        project, timeline, store)
    assert unrenamed["status"] == "committed", unrenamed
    assert timeline.GetTrackName("video", 2) == "Overlay"

    deleted = _apply(_patch("track-delete", unrenamed["generation"], [
        {"op": "track.delete", "track_type": "video", "track_index": 2}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert deleted["status"] == "committed", deleted
    assert timeline.GetTrackCount("video") == 1


def test_track_add_that_does_not_read_back_fails_verification(world):
    """Defect: a track add whose read-back shows no new row would commit
    a lie if the verifier passed it."""
    project, timeline, _item, store, base = world
    original = timeline.AddTrack

    def refused(kind, name):
        assert kind == "video"
        return False

    timeline.AddTrack = refused
    receipt = _apply(_patch("track-add-refused", base.generation, [
        {"op": "track.add", "track_type": "video", "name": "Overlay"}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert receipt["status"] == "verification_failed"
    assert timeline.GetTrackCount("video") == 1
    timeline.AddTrack = original


def test_track_rename_and_reorder_commit_and_invert(world):
    """Defect: a track row that cannot be renamed and reordered in place -
    the review's `track ops` would have no precise reversible op. The
    listing reads back as declared, and the inverse restores the prior
    layout."""
    project, timeline, _item, store, base = world

    # A second, empty row: Resolve destroys a row's clips with the row,
    # so a reorder moves an empty one.
    added = _apply(_patch("track-reorder-add", base.generation, [
        {"op": "track.add", "track_type": "video", "name": "Overlay"}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert added["status"] == "committed", added
    assert timeline.GetTrackName("video", 2) == "Overlay"

    renamed = _apply(_patch("track-reorder-rename", added["generation"], [
        {"op": "track.rename", "track_type": "video", "track_index": 2,
         "name": "Graphics"}], ["timeline_structure"], [[0, 48]]),
        project, timeline, store)
    assert renamed["status"] == "committed", renamed

    # The listing reads back as declared: Graphics above V1.
    reordered = _apply(_patch("track-reorder-move", renamed["generation"], [
        {"op": "track.reorder", "track_type": "video", "from_index": 2,
         "to_index": 1, "name": "Graphics"}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert reordered["status"] == "committed", reordered
    assert timeline.GetTrackName("video", 1) == "Graphics"
    assert timeline.GetTrackName("video", 2) == "Video 1"
    # A track op commits in under 5 s (the gap's performance requirement).
    assert reordered["exclusive_hold_seconds"] < 5.0

    # The inverse restores the prior layout.
    restored = _apply(_patch("track-reorder-back", reordered["generation"], [
        {"op": "track.reorder", "track_type": "video", "from_index": 1,
         "to_index": 2, "name": "Graphics"}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert restored["status"] == "committed", restored
    assert timeline.GetTrackName("video", 1) == "Video 1"
    assert timeline.GetTrackName("video", 2) == "Graphics"


def test_track_reorder_refuses_a_row_that_carries_clips(world):
    """Defect: reordering a row whose clips DeleteTrack would destroy - the
    op refuses rather than deleting the clips to move the row."""
    project, timeline, item, store, base = world
    receipt = _apply(_patch("track-reorder-clips", base.generation, [
        {"op": "track.reorder", "track_type": "video", "from_index": 1,
         "to_index": 2, "name": "Video 1"}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert receipt["status"] == "verification_failed"
    assert receipt["stopped"]["op"] == "track.reorder"
    assert timeline.GetTrackCount("video") == 1
    assert timeline.GetItemListInTrack("video", 1) == [item]


def test_clip_link_and_unlink_commit(world):
    """Defect: clips that cannot be linked and unlinked in place - the
    review's `link/unlink` would have no precise reversible op."""
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    linked = _apply(_patch("clip-link", base.generation, [
        {"op": "clip.link", "unique_id": uid, "with_unique_ids": []}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert linked["status"] == "committed", linked
    assert timeline.link_calls[-1][1] is True

    unlinked = _apply(_patch("clip-unlink", linked["generation"], [
        {"op": "clip.unlink", "unique_id": uid}],
        ["timeline_structure"], [[0, 48]]), project, timeline, store)
    assert unlinked["status"] == "committed", unlinked
    assert timeline.link_calls[-1][1] is False


# --------------------------------------------------------------------------
# From test_edit_input_digest.py
#
# Edit-step input digests: declared, stamped, and reported - never skipped.
#
# History: docs/evidence/resolve_test_history.md#test_edit_input_digest.

PILOT_ROOT = Path(__file__).resolve().parents[3]
STEPS_ROOT = PILOT_ROOT / "library" / "steps"


def _manifest(step_dir_name):
    with open(STEPS_ROOT / step_dir_name / "manifest.json",
              encoding="utf-8") as f:
        return json.load(f)


def _inputs(**overrides):
    base = {
        "timeline_transcript": {
            "measurement": {"segments": [
                {"speaker": "A", "text": "hello", "start": 0.0, "end": 1.0},
                {"speaker": "B", "text": "hi", "start": 1.0, "end": 2.0},
            ]},
        },
        "creative_direction": {"narrative_theme": "craft"},
        "creative_brief": "make three reels",
        "project_context": "context map",
        "brand_constraints": {"palette": ["#000000"]},
    }
    base.update(overrides)
    return base


def _select_reels_output():
    return {
        "turns": [{"speaker": "A", "start": 0.0, "end": 1.0}],
        "reel_candidates": [{"start": 0.0, "end": 45.0}],
        "length_guidance_seconds": [45, 90],
        "picture_holes": [],
        "lead_speaker": "A",
        "answering_speaker": "B",
        "who_leads_was_inferred": "A asks more",
        "reel_selection": {"moments": [], "considered": []},
    }


# ── The declaration ─────────────────────────────────────────────────


def test_select_reels_declares_what_it_reads():
    """19 of geo-podcast's last 20 run_history entries re-ran select_reels.

    The digest narrows the manifest's inputs to what reaches the bridge
    or the prompt. `audio_spine` is deliberately NOT among them: the
    manifest's own input description says nothing reads it - the DAG edge
    that carries it only orders this step after the spine - and hashing
    it would report "changed" on runs the model saw identically.
    """
    spec = digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")
    assert spec == {
        "inputs": ["timeline_transcript", "creative_direction",
                   "creative_brief", "project_context",
                   "brand_constraints"],
        "context": ["turns", "reel_candidates", "length_guidance_seconds",
                    "picture_holes", "lead_speaker", "answering_speaker",
                    "who_leads_was_inferred"],
        "include_code": True,
    }


# ── The digest ──────────────────────────────────────────────────────

def _spec():
    return digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")


def _step_dir():
    return STEPS_ROOT / "step_3_04_select_reels"


def test_digest_moves_when_an_upstream_value_moves():
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    changed = _inputs()
    changed["timeline_transcript"]["measurement"]["segments"][0]["text"] = \
        "hello, edited"
    after = digest.compute_digest(
        spec, changed, _select_reels_output(), _step_dir())
    assert before["digest"] != after["digest"]
    assert before["inputs_digest"] != after["inputs_digest"]
    assert before["code_digest"] == after["code_digest"]


def test_digest_moves_when_the_prompt_moves_but_code_identity_would_not():
    """The departure from code_identity, pinned: the handoff prompt is
    executable for a model step, so editing it moves the digest even
    though code_identity (which excludes .md as prose) would not see it."""
    import shutil
    import tempfile

    from library.tools import code_identity
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "step_3_04_select_reels"
        shutil.copytree(_step_dir(), clone,
                        ignore=shutil.ignore_patterns("__pycache__"))
        spec = _spec()
        before = digest.compute_digest(
            spec, _inputs(), _select_reels_output(), clone)
        code_before = code_identity.step_code_hash(str(clone))
        (clone / "handoff.md").write_text(
            (clone / "handoff.md").read_text(encoding="utf-8")
            + "\n\nAlso prefer owls.\n")
        after = digest.compute_digest(
            spec, _inputs(), _select_reels_output(), clone)
        assert code_before == code_identity.step_code_hash(str(clone))
        assert before["digest"] != after["digest"]
        assert before["code_digest"] != after["code_digest"]
        assert before["inputs_digest"] == after["inputs_digest"]


def test_a_model_answer_is_not_an_input():
    """Changing only the model's own answer must NOT move the digest -
    hashing one would report 'changed' on every re-run by construction."""
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    output = _select_reels_output()
    output["reel_selection"] = {"moments": [{"slug": "new-answer"}],
                                "considered": []}
    after = digest.compute_digest(spec, _inputs(), output, _step_dir())
    assert before["digest"] == after["digest"]


# ── The comparison: report, never act ───────────────────────────────


# ── The stamp in the run record ─────────────────────────────────────

def _begin(tmp_path, previous=None):
    project = tmp_path / "proj"
    project.mkdir()
    if previous is not None:
        (project / run_control.RUN_STATUS_FILE).write_text(
            json.dumps(previous), encoding="utf-8")
    run_control.begin_run_status(
        str(project), "single-step select_reels, manual LLM",
        ["select_reels"], argv=["--step", "select_reels"],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})
    return project


def test_stamp_helper_reports_and_never_skips(tmp_path, capsys):
    """The runner helper stamps, prints the comparison, and returns None -
    there is no skip path because none was built. The second identical run
    prints 'identical' while stamping again."""
    project = _begin(tmp_path)
    impl = {"manifest": _manifest("step_3_04_select_reels"),
            "step_dir": _step_dir(), "type": "hybrid"}
    runner._stamp_step_input_digest(
        str(project), "select_reels", impl, _inputs(),
        _select_reels_output())
    first_out = capsys.readouterr().err
    assert "no prior stamp" in first_out

    # A second run carries the first run's stamp as its baseline.
    run_control.begin_run_status(
        str(project), "single-step select_reels, manual LLM",
        ["select_reels"], argv=["--step", "select_reels"],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})
    assert runner._stamp_step_input_digest(
        str(project), "select_reels", impl, _inputs(),
        _select_reels_output()) is None
    second_out = capsys.readouterr().err
    assert "identical inputs" in second_out
    record = run_control.read_run_status(str(project))
    assert record["step_input_digests"]["select_reels"]["digest"]


# --------------------------------------------------------------------------
# From test_dirty_regions.py
#
# A touch's dirty block, and the scoped render QA that reads it.
#
# Two defects this pins: a span stated wrong for an op (a move that dirties
# only one end re-checks the wrong seconds), and a scoped read whose findings
# differ from the whole-file read over the same frames - the scoped verdict
# must be the whole verdict, restricted.

F0, FPS = 86400, 30.0


def _clip(a, b):
    return {"record_in": F0 + round(a * FPS), "duration": round((b - a) * FPS)}


TRACKS = [
    {"type": "video", "index": 4,
     "clips": [_clip(8.2, 9.0), _clip(14.5, 15.0), _clip(19.0, 19.5)]},
    {"type": "audio", "index": 1, "clips": [_clip(3.2, 3.6)]},
]


def _dirty(*edits, end=600):
    return d.dirty_from_spec({"edits": list(edits)}, TRACKS,
                             first_frame=F0, end_frame=F0 + end, fps=FPS)


def _cores(dirty):
    return [(s["domain"], s["core_start_seconds"], s["core_end_seconds"])
            for s in dirty["dirty_spans"]]


def test_each_op_dirties_its_own_span_and_domain():
    dirty = _dirty({"op": "swap_pixels", "row": "V4", "item": 1},
                   {"op": "set_properties", "row": "A1", "item": 0})
    assert not dirty["whole_reel"]
    assert dirty["dirty_domains"] == ["picture", "audio"]
    assert _cores(dirty) == [("audio", 3.2, 3.6), ("picture", 14.5, 15.0)]
    span = dirty["dirty_spans"][1]
    assert span["start_seconds"] == 14.5 - d.HANDLE_SECONDS
    assert span["end_seconds"] == 15.0 + d.HANDLE_SECONDS
    # A move dirties where it left and where it landed.
    dirty = _dirty({"op": "move", "row": "V4", "item": 0,
                    "to_record": F0 + 360})
    assert _cores(dirty) == [("picture", 8.2, 9.0), ("picture", 12.0, 12.8)]
    # A retime dirties from the cut to the end on both domains: 15f
    # grown to 45f, the reel ends a second later than it did.
    dirty = _dirty({"op": "retime", "row": "V4", "item": 1,
                    "duration": 45})
    assert _cores(dirty) == [("audio", 14.5, 21.0), ("picture", 14.5, 21.0)]


def test_what_cannot_be_bounded_is_the_whole_reel():
    assert _dirty({"op": "mystery", "row": "V4", "item": 0})["whole_reel"]
    assert _dirty({"op": "swap_pixels", "row": "V4", "item": 9})["whole_reel"]
    assert d.dirty_from_spec({"edits": []}, TRACKS, first_frame=None,
                             end_frame=None, fps="")["whole_reel"]
    # A receipt from before the dirty block is never "nothing changed".
    assert d.read_dirty([{"final": "Reel 01"}])["whole_reel"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_scoped_findings_equal_the_whole_file_read_over_the_same_frames(
        tmp_path):
    # Black 8.0-9.5s, a freeze 14.0-16.0s and digital silence 3.0-4.0s.
    video = str(tmp_path / "synth.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=20",
         "-f", "lavfi", "-i", "sine=f=440:r=48000:d=20",
         "-filter_complex",
         ("[0:v]drawbox=c=black:t=fill:enable='between(t,8,9.5)',split[p][q];"
          "[p][q]freezeframes=first=420:last=480:replace=420[v];"
          "[1:a]volume=enable='between(t,3,4)':volume=0[a]"),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video],
        check=True, capture_output=True, encoding="utf-8")
    dirty = _dirty({"op": "set_properties", "row": "V4", "item": 0},
                   {"op": "swap_pixels", "row": "V4", "item": 1},
                   {"op": "set_properties", "row": "A1", "item": 0})
    scoped, skipped = q.run_scoped_render_qa(video, dirty)
    assert skipped == []
    got = {r.metric: [(round(f["start"], 3), round(f["end"], 3))
                      for f in r.value] for r in scoped}

    silence = q.measure_silence_under_picture(video).value
    whole = {
        "black_frames": q.detect_black_frames(video).value,
        "freeze_frames": q.detect_freeze_frames(video).value,
        "silence_under_picture": [
            r for r in silence["by_level"]["digital_zero"]["where"]
            if r["seconds_under_picture"] >= silence["minimum_run_seconds"]],
    }
    for metric, domains in q.SCOPED_DETECTORS.items():
        cores = [(s[2], s[3]) for s in d.spans_for(dirty, domains)]
        want = [(round(r["start"], 3), round(r["end"], 3))
                for r in whole[metric]
                if any(r["start"] < hi and r["end"] > lo for lo, hi in cores)]
        assert len(got[metric]) == len(want), (metric, got, want)
        for (gs, ge), (ws, we) in zip(got[metric], want):
            assert gs == ws, (metric, got, want)
            # A freeze running past the span's end is reported up to it:
            # it runs AT LEAST that far.
            assert ge == we or (metric == "freeze_frames" and ge <= we), (
                metric, got, want)
    assert got["black_frames"] and got["freeze_frames"] \
        and got["silence_under_picture"], json.dumps(got)


def _encode(path, colour, seconds):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color={colour}:s=64x112:r=30:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, encoding="utf-8")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_re_delivered_file_is_watched_afresh(tmp_path):
    """`deliver-reel` writes a reel to the same name every time: keyed by
    the stem alone, the re-delivered reel was shown the previous render's
    strips."""
    from library.tools import render_watch as rw
    video = tmp_path / "Reel 01.mp4"

    def drawn():
        rw.watch_video(str(video), str(tmp_path / "f"),
                       str(tmp_path / "w.json"), "t")
        record = json.loads((tmp_path / "w.json").read_text("utf-8"))
        return {(tmp_path / "f" / row["file"]).read_bytes()
                for row in rw.draw_watch_strips(
                    str(video), str(tmp_path / "f"))["rows"]} \
            if record["watched"] else set()

    _encode(video, "red", 3)
    before = drawn()
    _encode(video, "blue", 3)
    assert drawn().isdisjoint(before)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_scoped_watch_is_the_whole_watch_restricted(tmp_path):
    from library.tools import render_watch as rw
    video = tmp_path / "reel.mp4"
    _encode(video, "green", 20)
    receipt = tmp_path / "touch.json"
    receipt.write_text(json.dumps({
        "final": "Reel 01", "started": "2000-01-01T00:00:00+00:00",
        "dirty": _dirty({"op": "set_properties", "row": "V4", "item": 1})}),
        "utf-8")
    whole = rw.watch_video(str(video), str(tmp_path / "f"),
                           str(tmp_path / "a.json"), "t")["record"]
    scoped = rw.watch_video(str(video), str(tmp_path / "f"),
                            str(tmp_path / "b.json"), "t",
                            dirty_receipts=[str(receipt)])["record"]
    assert (whole["strips"], scoped["strips"]) == (3, 1)
    assert scoped["not_rewatched"] == ["0.000-8.000s", "16.000-20.000s"]

    receipt.write_text(json.dumps({
        "final": "Reel 01", "started": "2000-01-01T00:00:00+00:00",
        "dirty": _dirty({"op": "set_properties", "row": "A1", "item": 0})}),
        "utf-8")
    with pytest.raises(rw.NoPictureChanged):
        rw.watch_video(str(video), str(tmp_path / "f"),
                       str(tmp_path / "c.json"), "t",
                       dirty_receipts=[str(receipt)])


def test_grade_node_set_commits_and_the_inverse_restores(world):
    """A node-grade op that answers True and changes nothing is caught.

    The defect: `SetCDL` returning True is not evidence the grade landed
    (`color_page_grade.py` measured a CDL that returned True and moved
    zero pixels). Without a `GetCDL` re-read, a silently-failed node
    write would be recorded as committed. This test applies a
    `grade.node_set`, asserts the read-back carries the new value,
    then applies the inverse and asserts the prior value is restored.
    """
    project, timeline, item, store, base = world
    item.nodes = 2
    uid = item.GetUniqueId()
    new_cdl = {
        "slope_r": 1.6, "slope_g": 1.6, "slope_b": 1.6,
        "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
        "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
        "saturation": 1.0,
    }
    old_cdl = {
        "slope_r": 1.0, "slope_g": 1.0, "slope_b": 1.0,
        "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
        "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
        "saturation": 1.0,
    }
    set_op = {"op": "grade.node_set", "unique_id": uid,
              "node_index": 2, "params": new_cdl}
    receipt = _apply(_patch("grade-node-set", base.generation,
                            [set_op], ["color"], [[0, 48]]),
                     project, timeline, store)
    assert receipt["status"] == "committed", receipt
    back = item.GetCDL()
    assert back["Slope"] == "1.6000 1.6000 1.6000"

    inverse_op = {"op": "grade.node_set", "unique_id": uid,
                  "node_index": 2, "params": old_cdl}
    inverse = _apply(_patch("grade-node-set-inverse", receipt["generation"],
                            [inverse_op], ["color"], [[0, 48]]),
                     project, timeline, store)
    assert inverse["status"] == "committed", inverse
    restored = item.GetCDL()
    assert restored["Slope"] == "1.0000 1.0000 1.0000"


def test_grade_node_set_refuses_an_out_of_range_node(world):
    """The Graph API has no AddNode: a node index past the count refuses.

    The defect: without the range check, `SetCDL` on a non-existent
    node would either raise inside Resolve with no diagnostic or
    silently write nothing, and the patch would record a committed
    grade that does not exist.
    """
    project, timeline, item, store, base = world
    item.nodes = 2
    uid = item.GetUniqueId()
    cdl = {
        "slope_r": 1.6, "slope_g": 1.6, "slope_b": 1.6,
        "offset_r": 0.0, "offset_g": 0.0, "offset_b": 0.0,
        "power_r": 1.0, "power_g": 1.0, "power_b": 1.0,
        "saturation": 1.0,
    }
    bad_op = {"op": "grade.node_set", "unique_id": uid,
              "node_index": 5, "params": cdl}
    receipt = _apply(_patch("grade-node-bad", base.generation,
                            [bad_op], ["color"], [[0, 48]]),
                     project, timeline, store)
    assert receipt["status"] == "verification_failed"
    assert "outside 1..2" in receipt["stopped"]["raised"]


def test_retime_segments_splits_and_sets_each_step(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    patch = _patch("retime-segments", base.generation, [{
        "op": "retime.segments", "unique_id": uid,
        "segments": [
            {"percent": 50.0, "record_frame": 0},
            {"percent": 100.0, "record_frame": 24},
        ],
    }], ["timeline_structure"], [[0, 48]])
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "committed", receipt
    pieces = timeline.GetItemListInTrack("video", 1)
    assert len(pieces) == 2
    speeds = sorted(p.GetSpeed()["Percentage"] for p in pieces)
    assert speeds == [50.0, 100.0]


def test_retime_segments_wrong_speed_fails_verification(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    patch = _patch("retime-segments-bad", base.generation, [{
        "op": "retime.segments", "unique_id": uid,
        "segments": [
            {"percent": 50.0, "record_frame": 0},
            {"percent": 75.0, "record_frame": 24},
        ],
    }], ["timeline_structure"], [[0, 48]])
    original_set_speed = type(item).SetSpeed

    def wrong_speed(self, speed):
        result = original_set_speed(self, speed)
        if isinstance(speed, dict) and speed.get("Percentage") == 75.0:
            self._speed = 100.0
        return result

    type(item).SetSpeed = wrong_speed
    try:
        receipt = _apply(patch, project, timeline, store)
    finally:
        type(item).SetSpeed = original_set_speed
    assert receipt["status"] == "verification_failed"
    assert "speed=100.0" in receipt["operations"][0]["failure"]


def test_picture_keyframe_writes_and_reads_back(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    patch = _patch("picture-keyframe", base.generation, [{
        "op": "picture.keyframe", "unique_id": uid, "param": "ZoomX",
        "keyframes": [
            {"frame": 0, "value": 1.0, "interpolation": "linear"},
            {"frame": 24, "value": 2.0, "interpolation": "linear"},
        ],
    }], ["picture_transform"], [[0, 48]])
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "committed", receipt
    kfs = item.GetKeyframes("ZoomX")
    assert len(kfs) == 2
    frames = sorted(kf["frame"] for kf in kfs)
    assert frames == [0, 24]
    values = {kf["frame"]: kf["value"] for kf in kfs}
    assert values[0] == 1.0
    assert values[24] == 2.0


def test_picture_keyframe_wrong_value_fails_verification(world):
    project, timeline, item, store, base = world
    uid = item.GetUniqueId()
    patch = _patch("picture-keyframe-bad", base.generation, [{
        "op": "picture.keyframe", "unique_id": uid, "param": "ZoomX",
        "keyframes": [
            {"frame": 0, "value": 1.0, "interpolation": "linear"},
            {"frame": 24, "value": 2.0, "interpolation": "linear"},
        ],
    }], ["picture_transform"], [[0, 48]])
    original_set_keyframe = type(item).SetKeyframe

    def wrong_keyframe(self, key, frame, value, interpolation="linear"):
        result = original_set_keyframe(self, key, frame, value, interpolation)
        if frame == 24:
            self._keyframes[key][-1]["value"] = 99.0
        return result

    type(item).SetKeyframe = wrong_keyframe
    try:
        receipt = _apply(patch, project, timeline, store)
    finally:
        type(item).SetKeyframe = original_set_keyframe
    assert receipt["status"] == "verification_failed"
    assert "value=99.0" in receipt["operations"][0]["failure"]
