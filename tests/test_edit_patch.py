"""An EditPatch commits against the generation it was planned on, or not at all."""

from __future__ import annotations

import pytest

from library.tools import edit_patch
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


def test_a_stale_patch_rebases_only_past_disjoint_patches(world):
    project, timeline, item, store, base = world
    _apply(_patch("markers", base.generation, [
        {"op": "marker.add", "frame": 40, "color": "Blue", "name": "m"}],
        ["markers"], [[40, 41]]), project, timeline, store)

    disjoint = _patch("transform", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.2}], ["picture_transform"], [[0, 48]])
    with pytest.raises(edit_patch.StalePatch) as stale:
        _apply(disjoint, project, timeline, store)
    assert stale.value.rebase_possible
    moved = edit_patch.rebase(disjoint, store)
    assert _apply(moved.to_dict(), project, timeline,
                  store)["status"] == "committed"

    overlapping = _patch("marker-too", base.generation, [
        {"op": "marker.delete", "frame": 40}], ["markers"], [[40, 41]])
    with pytest.raises(edit_patch.StalePatch, match="overlapping"):
        edit_patch.rebase(overlapping, store)


@pytest.mark.parametrize("operations,domains,spans,why", [
    ([{"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
     ["captions"], [[0, 10]], "does not declare"),
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
