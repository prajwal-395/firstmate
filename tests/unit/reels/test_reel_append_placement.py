"""A complete plan gets one inventory and bounded, targeted repairs."""
from collections import Counter

import pytest

from library.tools import edit_patch, project_model_store
from library.tools.reel_append_placement import (
    append_recorded, compare_inventory, verify_or_repair,
)
from library.tools.reel_otio_placement import PlacementRecorder
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import FakeTimeline, make_pool_clip, make_project


def _world():
    project = make_project("Placement")
    timeline = project.adopt(FakeTimeline("planned", project=project))
    for kind in ("video", "audio"):
        for row in (1, 2):
            timeline._ensure_track(kind, row)
    project.SetCurrentTimeline(timeline)
    pool = project.GetMediaPool()
    pool.audio_channels = (1,)
    source = make_pool_clip("source.mov", path="/m/source.mov")
    pool.GetRootFolder()._clips.append(source)
    expected = [{"track_type": "video", "track_index": row,
                 "source_file": "/m/source.mov", "source_in": 10,
                 "source_out": 30, "record_in": 0, "record_out": 20,
                 "media_pool_item": str(source.GetUniqueId())}
                for row in (1, 2)]
    return project, timeline, pool, source, expected


def _append(pool, source, row):
    return pool.AppendToTimeline([{
        "mediaPoolItem": source, "startFrame": 10, "endFrame": 30,
        "mediaType": 1, "trackIndex": row, "recordFrame": 0}])


def test_false_success_repairs_only_the_missing_row_once(monkeypatch):
    project, timeline, pool, source, expected = _world()
    _append(pool, source, 1)
    reads = Counter()
    original = timeline.GetItemListInTrack
    def counted(kind, row):
        reads[(kind, row)] += 1
        return original(kind, row)
    monkeypatch.setattr(timeline, "GetItemListInTrack", counted)
    with assume_sole_writer("one final inventory"):
        result, inventory = verify_or_repair(project, timeline, expected)
    assert result["passed"] and result["repair_attempted"]
    assert len(inventory) == 2
    assert reads == {("video", 1): 1, ("video", 2): 2,
                     ("audio", 1): 1, ("audio", 2): 1}


def test_a_failed_repair_never_becomes_an_append_retry_loop(monkeypatch):
    project, timeline, pool, source, expected = _world()
    _append(pool, source, 1)
    calls = []
    monkeypatch.setattr(pool, "AppendToTimeline", lambda infos: calls.append(infos))
    with assume_sole_writer("bounded repair"):
        result, _ = verify_or_repair(project, timeline, expected)
    assert not result["passed"] and result["repair_attempted"]
    assert len(calls) == 1
    assert len(result["missing"]) == 1


def test_unexpected_media_refuses_without_deleting_or_appending(monkeypatch):
    project, timeline, pool, source, expected = _world()
    _append(pool, source, 1)
    def forbidden(*args, **kwargs):
        pytest.fail("unexpected media does not authorize a write")
    monkeypatch.setattr(pool, "AppendToTimeline", forbidden)
    monkeypatch.setattr(timeline, "DeleteClips", forbidden)
    expected[0]["source_in"] = 9
    with assume_sole_writer("unexpected data"):
        result, _ = verify_or_repair(project, timeline, expected)
    assert not result["passed"] and not result["repair_attempted"]
    assert result["unexpected"]


def test_inventory_counts_duplicates_and_checks_every_link_member():
    row = {"track_type": "video", "track_index": 1,
           "source_file": "/m/a.mov", "source_in": 0, "source_out": 20,
           "record_in": 0, "record_out": 20}
    a = {**row, "unique_id": "a", "linked_ids": ["b"]}
    b = {**row, "track_index": 2, "unique_id": "b", "linked_ids": []}
    result = compare_inventory([row, row], [a])
    assert not result["passed"] and len(result["missing"]) == 1
    result = compare_inventory([row, {**row, "track_index": 2}],
                               [a, b], link_groups=[["a", "b"]])
    assert result["broken_links"] == ["b"]


def test_complete_picture_plan_uses_one_append_and_no_patch_model_transactions(monkeypatch):
    from types import SimpleNamespace

    project, _timeline, pool, source, _expected = _world()
    recorder = PlacementRecorder(pool)
    for row in (1, 2):
        recorder.AppendToTimeline([{
            "mediaPoolItem": recorder.pooled("/m/source.mov"),
            "startFrame": 10, "endFrame": 30, "mediaType": 1,
            "trackIndex": row, "recordFrame": 0}])
    plan = SimpleNamespace(
        video_tracks=[SimpleNamespace(media_type="video", index=row,
                                      name=f"Picture {row}") for row in (1, 2)],
        audio_tracks=[], speech_rows=lambda: [])
    calls = []
    append = pool.AppendToTimeline
    def counted(infos):
        calls.append(len(infos))
        return append(infos)
    def forbidden(*args, **kwargs):
        pytest.fail("a full placement pass must not start per-item transactions")
    monkeypatch.setattr(pool, "AppendToTimeline", counted)
    monkeypatch.setattr(edit_patch, "apply_live_patch", forbidden)
    monkeypatch.setattr(project_model_store, "load_project_model", forbidden)
    with assume_sole_writer("compiled plan"):
        timeline, _record, expected = append_recorded(
            project, recorder, "compiled", plan, 23.976, 1080, 1920, "", None)
        result, _ = verify_or_repair(project, timeline, expected, fps=23.976)
    assert calls == [2]
    assert result["passed"] and not result["repair_attempted"]


def test_property_repair_uses_first_inventory_handles_and_reads_only_changed_row(monkeypatch, tmp_path):
    project, timeline, pool, source, expected = _world()
    for row in (1, 2):
        _append(pool, source, row)
    expected[0]["properties"] = {"Pan": 42.0}
    reads = Counter()
    original = timeline.GetItemListInTrack
    def counted(kind, row):
        reads[(kind, row)] += 1
        return original(kind, row)
    monkeypatch.setattr(timeline, "GetItemListInTrack", counted)
    monkeypatch.setattr(pool, "AppendToTimeline", lambda _: pytest.fail("property repair must not append"))
    with assume_sole_writer("targeted property repair"):
        result, _ = verify_or_repair(project, timeline, expected, project_folder=str(tmp_path))
    assert result["passed"] and result["repair_attempted"]
    assert reads == {("video", 1): 2, ("video", 2): 1, ("audio", 1): 1, ("audio", 2): 1}


def test_final_inventory_offline_check_refuses_without_another_timeline_walk():
    project, timeline, pool, source, expected = _world()
    _append(pool, source, 1)
    with assume_sole_writer("online inventory"):
        result, _ = verify_or_repair(project, timeline, expected[:1], resolve_app=object())
    assert not result["passed"] and not result["repair_attempted"]
    assert result["media_errors"] == ["media is missing at /m/source.mov"]


def test_retimed_sources_use_the_existing_direct_placer():
    from types import SimpleNamespace
    from library.tools.reel_append_placement import supports
    prepared = SimpleNamespace(subtitle_segments=[], placements_list=[{"rate": 2.0}])
    assert not supports(prepared, None)


def test_empty_row_pruning_reuses_the_final_inventory(monkeypatch):
    project, timeline, pool, source, expected = _world()
    _append(pool, source, 1)
    reads = Counter()
    original = timeline.GetItemListInTrack
    def counted(kind, row):
        reads[kind, row] += 1
        return original(kind, row)
    monkeypatch.setattr(timeline, "GetItemListInTrack", counted)
    deleted = []
    with assume_sole_writer("single inventory occupancy"):
        result, _ = verify_or_repair(project, timeline, expected[:1],
                                     deleted_tracks=deleted, prune_empty_rows=True)
    assert result["passed"]
    assert reads == {("video", 1): 1, ("video", 2): 1, ("audio", 1): 1, ("audio", 2): 1}
    assert {(row["media_type"], row["index"]) for row in deleted} == {
        ("video", 2), ("audio", 1), ("audio", 2)}
