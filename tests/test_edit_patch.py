"""An EditPatch commits against the generation it was planned on, or not at all."""

from __future__ import annotations

from dataclasses import replace

import pytest

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
                                  store=store)


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
    with pytest.raises(edit_patch.StalePatch,
                       match=r"both write \['marker', 40\]"):
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
    monkeypatch.setitem(
        capabilities.PATCH_SEMANTICS, "reel.touchup",
        replace(capabilities.PATCH_SEMANTICS["reel.touchup"],
                temporal_effect="ripple"))
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
