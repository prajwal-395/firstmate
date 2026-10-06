"""An OTIO-imported item that reports no media pool item is re-placed.

Measured live on Resolve 21.1, 2026-10-06 (PR 1853's first live run): an
`importSourceClips: False` import plays the pooled file, yet every placed
item reads `GetMediaPoolItem()` None - even after save and reload - so
the freeze-tail inheritance, the crop aiming and the comp mapping refuse
on an imported reel. Resolve has no `SetMediaPoolItem`, so the repair is
the append path: the unlinked item is deleted and the already-pooled clip
is placed at the recorded span. Nothing is imported, so the pool gains no
ROOT copy.
"""

import pytest

from library.tools.reel_build import pool_items_for
from library.tools.reel_otio_placement import (
    OtioPlacementRefused, PlacementRecorder, _Spec, relink_imported_items)
from library.tools.timeline_layout import TrackPlan, TrackSpec
from tests.resolve_double import FakeTimeline, FakeTimelineItem, make_project

PATH = "/media/shot.mov"
PLAN = TrackPlan(
    video_tracks=[TrackSpec(index=1, media_type="video", role="proof",
                            name="Proof")],
    audio_tracks=[])


def _world(pooled=True):
    project = make_project(width=1920, height=1080, frame_rate=24)
    pool = project.GetMediaPool()
    if pooled:
        pool.ImportMedia([PATH])
    timeline = FakeTimeline("Proof", project)
    project.adopt(timeline)
    project.SetCurrentTimeline(timeline)
    timeline._ensure_track("video", 1)
    return project, pool, timeline


def _unlinked(timeline):
    item = FakeTimelineItem(
        "shot.mov", timeline, start=0, duration=48, left_offset=0,
        has_media=False)
    assert item.GetMediaPoolItem() is None
    timeline._ensure_track("video", 1)[1].append(item)
    return item


def _recorder(pool):
    recorder = PlacementRecorder(pool)
    recorder.specs.append(_Spec(
        path=PATH, track=1, media_type=1,
        source_in=0, source_out=48, record=0))
    return recorder


def _root_files(pool):
    return sorted(
        clip.GetClipProperty("File Path") or ""
        for clip in pool.GetRootFolder().GetClipList() or ())


def test_unlinked_item_is_replaced_with_the_pooled_clip():
    _project, pool, timeline = _world()
    _unlinked(timeline)
    before = _root_files(pool)
    repaired = relink_imported_items(
        pool, timeline, _recorder(pool), PLAN, "Proof")
    assert repaired == ["V1@0 shot.mov"]
    items = timeline.GetItemListInTrack("video", 1) or []
    assert len(items) == 1
    assert (items[0].GetStart(), items[0].GetEnd()) == (0, 48)
    assert items[0].GetMediaPoolItem().GetClipProperty(
        "File Path") == PATH
    assert len(pool_items_for(pool, PATH)) == 1
    assert _root_files(pool) == before


def test_unlinked_item_with_nothing_pooled_refuses():
    _project, pool, timeline = _world(pooled=False)
    _unlinked(timeline)
    with pytest.raises(OtioPlacementRefused, match="not in the media pool"):
        relink_imported_items(pool, timeline, _recorder(pool), PLAN, "Proof")


def test_linked_items_are_left_alone():
    _project, pool, timeline = _world()
    pool.AppendToTimeline([{
        "mediaPoolItem": pool_items_for(pool, PATH)[0],
        "startFrame": 0, "endFrame": 48, "mediaType": 1,
        "trackIndex": 1, "recordFrame": 0}])
    appends = list(pool.append_calls)
    deletes = timeline.deletes
    assert relink_imported_items(
        pool, timeline, _recorder(pool), PLAN, "Proof") == []
    assert pool.append_calls == appends
    assert timeline.deletes == deletes
