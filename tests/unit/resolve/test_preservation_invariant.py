"""Preservation is an execution invariant: the named thing changes, nothing else does.

Each test names the defect it removes. The defects are the ones the
engine's own scans could not see: a grade that moved on another clip, a
timeline marker that changed, a row that was renamed, media that was
re-pointed.
"""
from __future__ import annotations

import copy

import pytest

from library.tools import preservation_invariant as invariant


def _clip(unique_id, record_in=0, record_out=50, **extra):
    clip = {
        "unique_id": unique_id,
        "name": unique_id,
        "track_type": "video",
        "track_index": 1,
        "track_name": "V1",
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": record_in,
        "source_out_frame": record_out,
        "left_offset": 0,
        "right_offset": 0,
        "speed": 100,
        "enabled": True,
        "clip_color": "",
        "flags": [],
        "transform": {"ZoomX": 1.0, "Pan": 0.0, "Volume": 0.0},
        "color": {"cdl": {}, "color_group": "", "luts": []},
        "fusion": {"comp_count": 0, "comp_names": []},
        "keyframes": {},
        "markers": [],
        "media_pool_item_id": f"pool-{unique_id}",
        "source_file": f"/media/{unique_id}.mov",
    }
    clip.update(extra)
    return clip


def _snapshot(clips, *, tracks=None, timeline_markers=None, end_frame=100):
    if tracks is None:
        tracks = [{"type": "video", "index": 1, "name": "V1",
                   "clips": list(clips)}]
    return {
        "timeline": "Reel 01",
        "project": "Podcast",
        "fps": 24.0,
        "width": 1920,
        "height": 1080,
        "start_frame": 0,
        "end_frame": end_frame,
        "mode": "quick",
        "tracks": tracks,
        "markers": {"timeline": list(timeline_markers or []), "notes": []},
    }


def _scope(operations, domains=("timeline_structure",), spans=((0, 50),)):
    return invariant.scope_for(operations, conflict_domains=domains,
                               affected_spans=spans)


def _judge(before, after, operations, domains=("timeline_structure",),
           spans=((0, 50),)):
    scope = _scope(operations, domains, spans)
    return invariant.invariant(scope=scope, before=before,
                               operations=operations, after=after)


# ── The unrelated changes the engine's own scans could not see ─────────


def test_a_grade_that_moved_on_another_clip_is_collateral():
    """Defect: a write that re-grades an untouched clip read as preserved."""
    before = _snapshot([_clip("c1"), _clip("c2", record_in=50,
                                            record_out=100)])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][1]["color"]["cdl"] = {"Slope": "1.0"}

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}],
        domains=("picture_transform",))

    assert verdict.status == "violated"
    unrelated = verdict.unrelated
    assert [(row["target"], row["domain"]) for row in unrelated] == [
        ("clip:c2", "color")]
    assert unrelated[0]["field"] == "color.cdl"


def test_a_transform_that_moved_on_another_clip_is_collateral():
    """Defect: a picture change on an untouched clip read as preserved."""
    before = _snapshot([_clip("c1"), _clip("c2", record_in=50,
                                            record_out=100)])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][1]["transform"]["ZoomX"] = 2.0

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}], domains=("picture_transform",))

    assert verdict.status == "violated"
    assert verdict.unrelated[0]["field"] == "transform.ZoomX"


def test_a_mix_that_moved_on_another_clip_is_collateral():
    """Defect: an audio change on an untouched clip read as preserved."""
    audio = _clip("a1", track_type="audio", track_index=1,
                  transform={"Volume": 0.0, "Pan": 0.0})
    before = _snapshot([_clip("c1")], tracks=[
        {"type": "video", "index": 1, "name": "V1", "clips": [_clip("c1")]},
        {"type": "audio", "index": 1, "name": "A1", "clips": [audio]}])
    after = copy.deepcopy(before)
    after["tracks"][1]["clips"][0]["transform"]["Volume"] = -6.0

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}], domains=("picture_transform",))

    assert verdict.status == "violated"
    assert verdict.unrelated[0]["domain"] == "audio_mix"


def test_a_timeline_marker_that_changed_is_collateral():
    """Defect: the timeline's own markers were never compared at all."""
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["markers"]["timeline"] = [
        {"frame": 10, "color": "Red", "name": "note", "note": "", "duration": 1,
         "custom_data": ""}]

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}], domains=("picture_transform",))

    assert verdict.status == "violated"
    assert verdict.unrelated[0]["field"] == "markers.timeline"
    assert verdict.unrelated[0]["domain"] == "markers"


def test_a_renamed_row_is_collateral():
    """Defect: a row rename is invisible to a clip-only scan."""
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["name"] = "V1 renamed"

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}], domains=("picture_transform",))

    assert verdict.status == "violated"
    assert verdict.unrelated[0]["target"] == "track:video:1"


def test_media_that_was_re_pointed_on_another_clip_is_collateral():
    """Defect: a clip re-pointed at different media read as preserved."""
    before = _snapshot([_clip("c1"), _clip("c2", record_in=50,
                                            record_out=100)])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][1]["media_pool_item_id"] = "pool-other"
    after["tracks"][0]["clips"][1]["source_file"] = "/media/other.mov"

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}], domains=("picture_transform",))

    assert verdict.status == "violated"
    assert verdict.unrelated[0]["domain"] == invariant.SOURCE_IDENTITY


# ── The authorized changes are not collateral ──────────────────────────


def test_the_clip_an_edit_names_may_change():
    """Defect: the edit's own target was reported as unrelated."""
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5

    verdict = _judge(before, after, [
        {"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
         "value": 1.5}], domains=("picture_transform",))

    assert verdict.status == "preserved"
    assert verdict.unrelated == ()


def test_a_clip_the_edit_placed_is_not_collateral():
    """Defect: the clip an insert created read as an unrelated addition."""
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    placed = _clip("c2", record_in=50, record_out=100)
    after["tracks"][0]["clips"].append(placed)
    after["end_frame"] = 100

    verdict = _judge(before, after, [
        {"op": "clip.insert", "unique_id": "c2", "track_type": "video",
         "track_index": 1, "record_in": 50, "record_out": 100}])

    assert verdict.status == "preserved"
    assert verdict.unrelated == ()


def test_a_ripple_that_moves_the_tail_is_not_collateral():
    """Defect: the clips a ripple shifted read as unrelated changes."""
    before = _snapshot([_clip("c1"), _clip("c2", record_in=50,
                                            record_out=100)])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][1]["record_in"] = 60
    after["tracks"][0]["clips"][1]["record_out"] = 110
    after["end_frame"] = 110

    verdict = _judge(before, after, [
        {"op": "clip.ripple", "unique_id": "c1", "delta_frames": 10}],
        spans=((0, 100),))

    assert verdict.status == "preserved"
    assert verdict.unrelated == ()


def test_an_operation_may_re_point_its_own_target():
    """Defect: replacing a clip's media read as collateral."""
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["media_pool_item_id"] = "pool-new"
    after["tracks"][0]["clips"][0]["source_file"] = "/media/new.mov"

    verdict = _judge(before, after, [
        {"op": "clip.replace", "unique_id": "c1", "track_type": "video",
         "track_index": 1, "record_in": 0, "record_out": 50}])

    assert verdict.status == "preserved"
    assert verdict.unrelated == ()


def test_a_marker_the_edit_added_is_not_collateral():
    """Defect: the marker an edit added read as an unrelated change."""
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["markers"]["timeline"] = [
        {"frame": 10, "color": "Red", "name": "note", "note": "", "duration": 1,
         "custom_data": ""}]

    verdict = _judge(before, after, [
        {"op": "marker.add", "frame": 10, "color": "Red", "name": "note"}],
        domains=("markers",))

    assert verdict.status == "preserved"
    assert verdict.unrelated == ()


# ── The expected delta is computed before the write ────────────────────


def test_the_expected_delta_names_what_the_operations_promised():
    """Defect: the receipt carried no expected delta to compare against."""
    scope = _scope([{"op": "clip.set_property", "unique_id": "c1",
                     "key": "ZoomX", "value": 1.5}],
                   domains=("picture_transform",))
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5

    verdict = invariant.invariant(scope=scope, before=before, after=after,
                                  operations=[
                                      {"op": "clip.set_property",
                                       "unique_id": "c1", "key": "ZoomX",
                                       "value": 1.5}])

    assert verdict.expected[0]["target"] == "clip:c1"
    assert verdict.expected[0]["domain"] == "picture_transform"
    assert verdict.expected[0]["fields"] == ["transform.ZoomX"]
    assert verdict.expected_not_observed == ()


def test_the_expected_delta_is_the_operations_not_the_read_back():
    """Defect: the expectation was read off the outcome it judged."""
    scope = _scope([{"op": "clip.set_property", "unique_id": "c1",
                     "key": "ZoomX", "value": 1.5}],
                   domains=("picture_transform",))
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 9.9

    verdict = invariant.invariant(scope=scope, before=before, after=after,
                                  operations=[
                                      {"op": "clip.set_property",
                                       "unique_id": "c1", "key": "ZoomX",
                                       "value": 1.5}])

    assert verdict.expected[0]["fields"] == ["transform.ZoomX"]
    assert verdict.expected_not_observed == ()


# ── The guarantee that cannot be established ───────────────────────────


def test_an_operation_with_no_declared_scope_refuses_the_guarantee():
    """Defect: an operation nothing declares was allowed to touch the
    timeline, and no unrelated change could be told from its own."""
    scope = invariant.scope_for(
        [{"op": "no.such.operation", "unique_id": "c1"}],
        conflict_domains=("timeline_structure",), affected_spans=((0, 50),))
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)

    verdict = invariant.invariant(scope=scope, before=before, after=after,
                                  operations=[
                                      {"op": "no.such.operation",
                                       "unique_id": "c1"}])

    assert verdict.status == "unprovable"
    assert "no.such.operation" in verdict.unprovable_reason


def test_a_domain_the_snapshot_cannot_see_is_reported_not_claimed():
    """Defect: a guarantee over state the read cannot see read as proven."""
    scope = _scope([{"op": "caption.style", "unique_id": "c1"}],
                   domains=("captions",))
    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)

    verdict = invariant.invariant(scope=scope, before=before, after=after,
                                  operations=[{"op": "caption.style",
                                               "unique_id": "c1"}])

    assert verdict.unobservable_domains == ("captions",)


def test_the_pre_edit_snapshot_is_the_recorded_head():
    """Defect: the pre-edit state was whatever the caller happened to hold."""
    from library.tools import timeline_shadow as shadow

    store = shadow.ShadowStore()
    with pytest.raises(invariant.PreservationUnprovable):
        invariant.pre_edit_snapshot(store=store, project="Podcast",
                                    timeline_id="no-such-timeline")


# ── The invariant is wired into the write path ─────────────────────────


def test_a_transaction_that_renamed_a_row_does_not_commit():
    """Defect: a write that renamed a row was reported as committed, and
    the clip-only scan could not see a row at all."""
    from library.tools import transaction_orchestration as orchestration

    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["name"] = "V1 renamed"

    receipt = orchestration.verify_readback(
        [{"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
          "value": 1.5}],
        [{"index": 0, "op": "clip.set_property", "returned": True}],
        after, base=before, affected_spans=((0, 50),),
        conflict_domains=("picture_transform",))

    assert receipt["preservation"]["status"] == "violated"
    assert receipt["status"] == "verification_failed"
    assert receipt["preservation"]["unrelated_changes"][0]["target"] == \
        "track:video:1"


def test_a_transaction_that_only_changed_its_target_commits():
    """Defect: the invariant refused the edit's own target."""
    from library.tools import transaction_orchestration as orchestration

    before = _snapshot([_clip("c1")])
    after = copy.deepcopy(before)
    after["tracks"][0]["clips"][0]["transform"]["ZoomX"] = 1.5

    receipt = orchestration.verify_readback(
        [{"op": "clip.set_property", "unique_id": "c1", "key": "ZoomX",
          "value": 1.5}],
        [{"index": 0, "op": "clip.set_property", "returned": True}],
        after, base=before, affected_spans=((0, 50),),
        conflict_domains=("picture_transform",),
        acceptance_evidence={0: {"operation_readback_matches_request": True}})

    assert receipt["preservation"]["status"] == "preserved"
    assert receipt["status"] == "committed"


def test_the_adapter_refuses_a_write_that_changed_something_else():
    """Defect: the adapter's read-back accepted a write that changed a
    row it was never told to change."""
    from library.tools import edit_execution_adapters as adapters

    with pytest.raises(RuntimeError) as excinfo:
        adapters._refuse_collateral("clip.trim", {
            "status": "violated",
            "unrelated_changes": [{"target": "track:video:1",
                                   "field": "name",
                                   "domain": "timeline_structure"}]})

    assert "track:video:1.name" in str(excinfo.value)


def test_the_adapter_accepts_a_write_that_changed_only_its_target():
    """Defect: the adapter refused the edit it was asked to make."""
    from library.tools import edit_execution_adapters as adapters

    adapters._refuse_collateral("clip.trim", {
        "status": "preserved", "unrelated_changes": []})


# ── Preservation across repeated editing cycles ────────────────────────


def test_preservation_holds_across_repeated_editing_cycles(tmp_path):
    """Defect: preservation was only ever proved on one isolated change,
    so the collateral a LATER cycle did to an EARLIER cycle's approved
    result went unseen."""
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

    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    first = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    second = place_clip(timeline, make_pool_clip("b.mov"), 48, 95)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")

    def apply(patch_id, generation, operations, domains, spans):
        patch = {"id": patch_id, "project": "Podcast", "timeline": "Reel 09",
                 "base_generation": generation, "capability": "reel.touchup",
                 "affected_spans": spans, "conflict_domains": domains,
                 "operations": operations}
        return edit_patch.apply_patch(
            patch, resolve=FakeResolve(project), project=project,
            timeline=timeline, store=store, project_folder=store.path.parent)

    def clip_state(snapshot, unique_id):
        return next(clip for track in snapshot["tracks"]
                    for clip in track["clips"]
                    if str(clip["unique_id"]) == unique_id)

    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        cycle_one = apply(
            "cycle-1", base.generation,
            [{"op": "clip.set_property", "unique_id": first.GetUniqueId(),
              "key": "ZoomX", "value": 1.5}],
            ["picture_transform"], [(0, 47)])
        assert cycle_one["status"] == "committed", cycle_one
        assert cycle_one["preservation"]["status"] == "preserved"

        cycle_two = apply(
            "cycle-2", cycle_one["generation"],
            [{"op": "clip.set_property", "unique_id": second.GetUniqueId(),
              "key": "Pan", "value": 0.25}],
            ["picture_transform"], [(48, 95)])
        assert cycle_two["status"] == "committed", cycle_two
        assert cycle_two["preservation"]["status"] == "preserved"

        # The first cycle's approved result survived the second cycle.
        after_one = clip_state(cycle_one["readback"], first.GetUniqueId())
        after_two = clip_state(cycle_two["readback"], first.GetUniqueId())
        assert after_one["transform"]["ZoomX"] == 1.5
        assert after_two["transform"] == after_one["transform"]

        cycle_three = apply(
            "cycle-3", cycle_two["generation"],
            [{"op": "clip.trim", "unique_id": first.GetUniqueId(),
              "in_frame": 0, "out_frame": 40}],
            ["timeline_structure"], [(0, 40)])
        assert cycle_three["status"] == "committed", cycle_three
        assert cycle_three["preservation"]["status"] == "preserved"

        # The second cycle's approved result survived the third cycle.
        after_three = clip_state(cycle_three["readback"],
                                 second.GetUniqueId())
        assert after_three["transform"]["Pan"] == 0.25
