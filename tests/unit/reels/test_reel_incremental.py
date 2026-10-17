"""A small plan change touches only what changed.

The incremental path duplicates the approved final into staging and
patches only the overlay bytes that differ, instead of re-placing the
whole reel. Anything the diff cannot prove safe refuses with
`IncrementalRefused` and the caller falls back to the full rebuild.

These tests drive the fake Resolve double (no live connection): a
full build places the "approved" reel, a re-rendered lower third
forms the new plan, and the patched staging must match a fresh full
build of the new plan item by item.
"""
from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from library.tools import reel_incremental as incr
from library.tools import timeline_shadow as shadow
from library.tools.reel_incremental import IncrementalRefused
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import make_project
from tests.scenarios.test_reel_build import (
    FakeMoment,
    _caption,
    _master_clips,
    _transcript,
    _world,
)


def _segments(tmp_path, tag):
    first = tmp_path / f"lt1_{tag}.mov"
    first.write_bytes(b"lower-third-one-" + tag.encode())
    second = tmp_path / f"lt2_{tag}.mov"
    second.write_bytes(b"lower-third-two-" + tag.encode())
    return [
        {"overlay_path": str(first), "timeline_start": 2.0,
         "timeline_end": 2.0 + 48 / 23.976,
         "total_frames": 48, "segment_id": "lt-1"},
        {"overlay_path": str(second), "timeline_start": 8.0,
         "timeline_end": 8.0 + 48 / 23.976,
         "total_frames": 48, "segment_id": "lt-2",
         "tight_box": {"width": 400, "height": 200,
                       "placement": {"scaling": 0.5, "pan": 100.0,
                                     "tilt": -200.0}}},
    ]


def _build(tmp_path, segments, program_channels=None, **options):
    from library.tools.reel_build import build_reel_timeline

    timeline, pool, project = _world()
    record = build_reel_timeline(
        project, FakeMoment(), _master_clips(), [_caption(2.0, 4.0)],
        23.976, 1080, 1920, str(tmp_path), _transcript(),
        lower_third_segments=list(segments),
        master_timeline=None,
        program_channels=dict(program_channels or {"1": 1, "2": 1}),
        **options, timeline_name="Reel 99 - incremental")
    return timeline, pool, project, record


def _prepared(tmp_path, segments, program_channels=None, **options):
    from library.tools.reel_build import prepare_reel_timeline

    return prepare_reel_timeline(
        FakeMoment(), _master_clips(), [_caption(2.0, 4.0)], 23.976,
        1080, 1920, str(tmp_path), _transcript(),
        timeline_name="Reel 99 - incremental",
        lower_third_segments=list(segments),
        master_timeline=None,
        program_channels=dict(program_channels or {"1": 1, "2": 1}), **options)


def _inventory(timeline):
    """{(media, index): sorted [(start, end, source)]} for every row."""
    out = {}
    for media in ("video", "audio"):
        try:
            count = timeline.GetTrackCount(media) or 0
        except Exception:
            continue
        for index in range(1, count + 1):
            rows = []
            for item in timeline.GetItemListInTrack(media, index) or []:
                try:
                    pool_item = item.GetMediaPoolItem()
                    source = (pool_item.GetClipProperty("File Path")
                              if pool_item is not None else "")
                except Exception:
                    source = ""
                rows.append((item.GetStart(), item.GetEnd(), source))
            out[(media, index)] = sorted(rows)
    return out


def test_lower_third_bytes_only_patch_matches_full_rebuild(tmp_path):
    """Same end state: patched staging vs a fresh full build, item by item."""
    old_segments = _segments(tmp_path, "v1")
    _final, _pool, project, _record = _build(tmp_path, old_segments)

    new_segments = _segments(tmp_path, "v2")
    # Same spans, re-rendered bytes (different files, like a
    # content-keyed re-render).
    for new, old in zip(new_segments, old_segments):
        assert new["timeline_start"] == old["timeline_start"]
        assert new["total_frames"] == old["total_frames"]
        assert new["overlay_path"] != old["overlay_path"]

    from library.tools.reel_build import build_reel_timeline
    _ref_timeline, _ref_pool, _ref_project, _ref_record = _build(
        tmp_path, new_segments)
    _ref_project.SetCurrentTimeline(_ref_timeline)

    prepared = _prepared(tmp_path, new_segments)
    from library.tools import reel_rebuild_need as _need
    recorded = _need.carried_digest_live(project, _final)
    assert recorded
    with assume_sole_writer("incremental-equivalence"):
        record = incr.build_staged_incremental(
            project=project, staging_name="Reel 99 - incremental (rebuild staging)",
            final_name=_final.GetName(), recorded_carried=recorded,
            prepared=prepared, overlay_placements=None,
            explainer_segments=None, semantic_segments=None,
            lower_third_segments=new_segments, look=None, grade_cdl=None,
            power_grade=None, motion=None, overlay_intent=None,
            suppressions=[], fps=23.976, width=1080,
            height=1920, project_folder=str(tmp_path), draw_gain=1.0,
            resolve_app=None, single_reel_scope=True,
            placement_mode="append", transcript=_transcript())
    staging = None
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if (candidate is not None and candidate.GetName()
                == "Reel 99 - incremental (rebuild staging)"):
            staging = candidate
    assert staging is not None
    assert _inventory(staging) == _inventory(_ref_timeline)
    assert record["placements"] == _ref_record["placements"]
    assert [(row["index"], row["name"]) for row in record["track_plan"]["video_tracks"]] == [
        (row["index"], row["name"]) for row in _ref_record["track_plan"]["video_tracks"]]
    assert record["overlay_placements"] == _ref_record["overlay_placements"]


def test_added_and_removed_overlays_patch_to_match(tmp_path):
    """An added span is placed and a removed span is deleted, like the
    full build's fresh staging."""
    old_segments = _segments(tmp_path, "v1")
    final, _pool, project, _record = _build(tmp_path, old_segments)

    added_path = tmp_path / "lt3_v2.mov"
    added_path.write_bytes(b"lower-third-three")
    new_segments = [old_segments[0], {
        "overlay_path": str(added_path), "timeline_start": 12.0,
        "timeline_end": 12.0 + 48 / 23.976,
        "total_frames": 48, "segment_id": "lt-3"}]

    from library.tools.reel_build import build_reel_timeline
    _ref_timeline, _ref_pool, _ref_project, _ref_record = _build(
        tmp_path, new_segments)

    prepared = _prepared(tmp_path, new_segments)
    from library.tools import reel_rebuild_need as _need
    recorded = _need.carried_digest_live(project, final)
    assert recorded
    with assume_sole_writer("incremental-add-remove"):
        record = incr.build_staged_incremental(
            project=project, staging_name="Reel 99 - incremental (rebuild staging)",
            final_name=final.GetName(), recorded_carried=recorded,
            prepared=prepared, overlay_placements=None,
            explainer_segments=None, semantic_segments=None,
            lower_third_segments=new_segments, look=None,
            grade_cdl=None, power_grade=None, motion=None,
            overlay_intent=None, suppressions=[],
            fps=23.976, width=1080, height=1920,
            project_folder=str(tmp_path), draw_gain=1.0,
            resolve_app=None, single_reel_scope=True,
            placement_mode="append", transcript=_transcript())
    staging = None
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if (candidate is not None and candidate.GetName()
                == "Reel 99 - incremental (rebuild staging)"):
            staging = candidate
    assert staging is not None
    assert _inventory(staging) == _inventory(_ref_timeline)


def test_footage_change_refuses(tmp_path):
    old_segments = _segments(tmp_path, "v1")
    final, _pool, project, _record = _build(tmp_path, old_segments)
    prepared = _prepared(tmp_path, old_segments)
    moved = [dict(place, snapped_record=place["snapped_record"] + 24)
             for place in prepared.placements_list]
    prepared = dataclasses.replace(prepared, placements_list=moved)
    from library.tools import reel_rebuild_need as _need
    recorded = _need.carried_digest_live(project, final)
    with assume_sole_writer("incremental-refuse"):
        with pytest.raises(IncrementalRefused, match="ootage"):
            incr.build_staged_incremental(
                project=project, staging_name="Reel 99 - incremental (rebuild staging)",
                final_name=final.GetName(), recorded_carried=recorded,
                prepared=prepared, overlay_placements=None,
                explainer_segments=None, semantic_segments=None,
                lower_third_segments=old_segments, look=None,
                grade_cdl=None, power_grade=None, motion=None,
                overlay_intent=None, suppressions=[],
                fps=23.976, width=1080, height=1920,
                project_folder=str(tmp_path), draw_gain=1.0,
                resolve_app=None, single_reel_scope=True,
                placement_mode="append", transcript=_transcript())
    assert all(project.GetTimelineByIndex(i).GetName() !=
               "Reel 99 - incremental (rebuild staging)"
               for i in range(1, project.GetTimelineCount() + 1))


def test_caption_change_refuses(tmp_path):
    old_segments = _segments(tmp_path, "v1")
    final, _pool, project, _record = _build(tmp_path, old_segments)
    prepared = _prepared(tmp_path, old_segments)
    prepared = dataclasses.replace(
        prepared,
        subtitle_segments=[_caption(3.0, 5.0)])
    from library.tools import reel_rebuild_need as _need
    recorded = _need.carried_digest_live(project, final)
    with assume_sole_writer("incremental-refuse"):
        with pytest.raises(IncrementalRefused, match="[Cc]aption"):
            incr.build_staged_incremental(
                project=project, staging_name="Reel 99 - incremental (rebuild staging)",
                final_name=final.GetName(), recorded_carried=recorded,
                prepared=prepared, overlay_placements=None,
                explainer_segments=None, semantic_segments=None,
                lower_third_segments=old_segments, look=None,
                grade_cdl=None, power_grade=None, motion=None,
                overlay_intent=None, suppressions=[],
                fps=23.976, width=1080, height=1920,
                project_folder=str(tmp_path), draw_gain=1.0,
                resolve_app=None, single_reel_scope=True,
                placement_mode="append", transcript=_transcript())


def test_otio_mode_refuses(tmp_path):
    old_segments = _segments(tmp_path, "v1")
    final, _pool, project, _record = _build(tmp_path, old_segments)
    prepared = _prepared(tmp_path, old_segments)
    with assume_sole_writer("incremental-refuse"):
        with pytest.raises(IncrementalRefused, match="append"):
            incr.build_staged_incremental(
                project=project, staging_name="Reel 99 - incremental (rebuild staging)",
                final_name=final.GetName(), recorded_carried="does-not-match",
                prepared=prepared, overlay_placements=None,
                explainer_segments=None, semantic_segments=None,
                lower_third_segments=old_segments, look=None,
                grade_cdl=None, power_grade=None, motion=None,
                overlay_intent=None, suppressions=[],
                fps=23.976, width=1080, height=1920,
                project_folder=str(tmp_path), draw_gain=1.0,
                resolve_app=None, single_reel_scope=True,
                placement_mode="otio", transcript=_transcript())


def test_missing_carried_record_refuses(tmp_path):
    old_segments = _segments(tmp_path, "v1")
    final, _pool, project, _record = _build(tmp_path, old_segments)
    prepared = _prepared(tmp_path, old_segments)
    with assume_sole_writer("incremental-refuse"):
        with pytest.raises(IncrementalRefused, match="[Dd]rift"):
            incr.build_staged_incremental(
                project=project, staging_name="Reel 99 - incremental (rebuild staging)",
                final_name=final.GetName(), recorded_carried="",
                prepared=prepared, overlay_placements=None,
                explainer_segments=None, semantic_segments=None,
                lower_third_segments=old_segments, look=None,
                grade_cdl=None, power_grade=None, motion=None,
                overlay_intent=None, suppressions=[],
                fps=23.976, width=1080, height=1920,
                project_folder=str(tmp_path), draw_gain=1.0,
                resolve_app=None, single_reel_scope=True,
                placement_mode="append", transcript=_transcript())


@pytest.mark.parametrize("field,value,reason", [
    ("ledger_rows", [{"op": "edit_program"}], "edit-ledger"),
])
def test_unsupported_plan_refuses_before_any_timeline_read(field, value, reason):
    prepared = SimpleNamespace(freeze_tail=None, post_header=None, ledger_rows=[])
    setattr(prepared, field, value)
    class UnreachableProject:
        def GetTimelineCount(self):
            pytest.fail("unsupported plan must refuse before reading or duplicating timelines")
    with pytest.raises(IncrementalRefused, match=reason):
        incr.build_staged_incremental(
            project=UnreachableProject(), staging_name="staging", final_name="final",
            recorded_carried="x", prepared=prepared, overlay_placements=None,
            explainer_segments=None, semantic_segments=None, lower_third_segments=None,
            look=None, grade_cdl=None, power_grade=None, motion=None,
            overlay_intent=None, suppressions=[], fps=24, width=1080, height=1920,
            project_folder="", draw_gain=1, resolve_app=None, single_reel_scope=True,
            placement_mode="append", transcript={})


def test_native_hold_and_header_lower_third_patch_matches_full_build(tmp_path, monkeypatch):
    from library.tools import edit_patch, project_model_store
    header_path = tmp_path / "header.mov"
    header_path.write_bytes(b"fixed header")
    header = {"overlay_path": str(header_path), "timeline_start": 0.0,
              "total_frames": 240, "segment_id": "post-header"}
    monkeypatch.setattr("library.tools.reel_post_header.plan_for_reel",
                        lambda *a, **k: SimpleNamespace(segments=[header], as_dict=lambda: {"segments": [header]}))
    original_world = _world
    def long_sources():
        timeline, pool, project = original_world()
        for item in pool.GetRootFolder().GetClipList() or ():
            item.SetClipProperty("Frames", "100000")
        return timeline, pool, project
    monkeypatch.setattr(__import__(__name__, fromlist=["_world"]), "_world", long_sources)
    options = {"ending": {"tail_hold": "freeze", "tail_element": "tv_power_tail"}}
    old_segments = _segments(tmp_path, "old")
    final, _, project, _ = _build(tmp_path, old_segments, **options)
    new_segments = _segments(tmp_path, "new")
    reference, _, _, _ = _build(tmp_path, new_segments, **options)
    prepared = _prepared(tmp_path, new_segments, **options)
    assert prepared.freeze_tail is not None
    from library.tools import reel_rebuild_need as need
    recorded = need.carried_digest_live(project, final)
    coverage_calls = []
    from library.tools import reel_build
    actual_coverage = reel_build.verify_motion_screen_coverage
    def coverage(*a, **k):
        coverage_calls.append(True)
        return actual_coverage(*a, **k)
    monkeypatch.setattr(reel_build, "verify_motion_screen_coverage", coverage)
    monkeypatch.setattr(reel_build, "aim_picture_row", lambda *a, **k: 0)
    monkeypatch.setattr("library.tools.tv_frame.screen_window_rect", lambda *a, **k: (0, 0, 1080, 1920))
    def forbidden(*a, **k):
        pytest.fail("an incremental placement must not reload or transact the Project Model")
    monkeypatch.setattr(edit_patch, "apply_live_patch", forbidden)
    monkeypatch.setattr(project_model_store, "load_project_model", forbidden)
    with assume_sole_writer("hold and header incremental"):
        record = incr.build_staged_incremental(
            project=project, staging_name="patched", final_name=final.GetName(),
            recorded_carried=recorded, prepared=prepared, overlay_placements=None,
            explainer_segments=None, semantic_segments=None, lower_third_segments=new_segments,
            look={"origin": "fixture"}, grade_cdl=None, power_grade=None, motion=None, overlay_intent=None,
            suppressions=[], fps=23.976, width=1080, height=1920,
            project_folder=str(tmp_path), draw_gain=1, resolve_app=None,
            single_reel_scope=True, placement_mode="append", transcript=_transcript())
    patched = project.GetCurrentTimeline()
    assert _inventory(patched) == _inventory(reference)
    def properties(timeline):
        return sorted((kind, row, item.GetStart(), item.GetSourceStartFrame(),
                       item.GetSourceEndFrame(), item.GetSpeed(),
                       tuple(sorted(item.GetProperty().items())))
                      for kind in ("video", "audio")
                      for row in range(1, timeline.GetTrackCount(kind) + 1)
                      for item in timeline.GetItemListInTrack(kind, row) or ())
    assert properties(patched) == properties(reference)
    assert record["freeze_placement"]["reused"]
    assert record["placement_check"]["passed"]
    assert coverage_calls == [True]


def test_card_sharing_overlay_row_is_retained_and_row_inventory_is_read_once(tmp_path, monkeypatch):
    from collections import Counter
    from tests.resolve_double import FakeTimeline, make_pool_clip
    project = make_project("shared row")
    timeline = project.adopt(FakeTimeline("shared", project=project))
    timeline._ensure_track("video", 1)
    project.SetCurrentTimeline(timeline)
    pool = project.GetMediaPool()
    card_path = tmp_path / "end-card.mov"
    overlay_path = tmp_path / "lower.mov"
    card_path.write_bytes(b"card")
    overlay_path.write_bytes(b"lower")
    card_item = make_pool_clip("end-card.mov", path=str(card_path))
    lower_item = make_pool_clip("lower.mov", path=str(overlay_path))
    pool.AppendToTimeline([
        {"mediaPoolItem": card_item, "startFrame": 0, "endFrame": 48,
         "mediaType": 1, "trackIndex": 1, "recordFrame": 240},
        {"mediaPoolItem": lower_item, "startFrame": 0, "endFrame": 48,
         "mediaType": 1, "trackIndex": 1, "recordFrame": 0}])
    row = SimpleNamespace(index=1)
    plan = SimpleNamespace(material={"card_role": "motion_graphics", "mg_spans": [(0, 48)],
                                     "card_spans": [(240, 288)]},
                           rows_for_role=lambda role: [row])
    card = SimpleNamespace(placement="tail", reel_start_frame=240, source_frames=48)
    card.rendered_path = str(card_path)
    reads = Counter()
    original = timeline.GetItemListInTrack
    def counted(kind, index):
        reads[kind, index] += 1
        return original(kind, index)
    monkeypatch.setattr(timeline, "GetItemListInTrack", counted)
    inventory = incr._RowInventory(timeline)
    fixed = incr._check_cards_identical(inventory, plan, [card])
    diff = incr.diff_overlay_kind(inventory, "speaker lower third", [1], [{
        "overlay_path": str(overlay_path), "timeline_start": 0, "total_frames": 48}],
        24, exclude_ids=fixed)
    assert not diff.replace and not diff.added and not diff.removed
    assert len(fixed) == 1 and reads["video", 1] == 1
