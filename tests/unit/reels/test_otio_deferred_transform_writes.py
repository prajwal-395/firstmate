"""The deferred transform pass writes what the item does not already carry.

The defects these guard, both measured on one build (the latency scout,
2026-10-21): the OTIO placement pass wrote all 455 transform properties
of a build (113 items x {ZoomX, ZoomY, Tilt, Pan}) at ~0.17 s each,
whether or not the item already carried the value, and it asked Resolve
for the same track's item list once per SPEC, so the cost grew with
specs x items.

The skip is only ever decided by a READ-BACK, never by an assumption: a
property already at the wanted value is not changed by writing it, so
skipping it leaves the timeline identical - but only a read can prove the
value is already there.
"""

from collections import Counter

import pytest

from library.tools.reel_otio_placement import PlacementRecorder, run_deferred
from tests.resolve_double import (
    FakeTimeline,
    FakeTimelineItem,
    make_pool_clip,
    make_project,
)

SOURCE = "/m/source.mov"


def _world(tmp_path, *, rows):
    """A recorded placement and the timeline it is replayed onto.

    `rows` is one entry per spec: `(track, record, properties, item)`.
    The item is what the import put on the track; its transform
    properties are what the import carried.
    """
    project = make_project("Deferred")
    pool = project.GetMediaPool()
    pool.GetRootFolder()._clips.append(
        make_pool_clip("source.mov", path=SOURCE))
    recorder = PlacementRecorder(pool)
    timeline = FakeTimeline("deferred", project=project)
    placed = []
    for track, record, properties, item in rows:
        recorder.AppendToTimeline([{
            "mediaPoolItem": recorder.pooled(SOURCE),
            "startFrame": 10, "endFrame": 30, "mediaType": 1,
            "trackIndex": track, "recordFrame": record}])
        recorder.specs[-1].properties.update(properties)
        if not any(placed_item is item for placed_item in placed):
            placed.append(item)
            timeline.add_item("video", track, item)
    return project, recorder, timeline


def _item(**props):
    item = FakeTimelineItem("source.mov", None, start=0, duration=48)
    item.properties = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0}
    item.properties.update(props)
    return item


def _run(recorder, timeline, tmp_path):
    return run_deferred(timeline, recorder, project_folder=str(tmp_path),
                        project="Deferred", timeline_name="deferred")


def test_a_property_the_item_already_carries_is_not_written_again(
        tmp_path):
    """The no-op write: the import carried the value, so writing it
    again costs ~0.17 s and changes nothing."""
    item = _item(Pan=40.0, ZoomX=1.25)
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 40.0, "Tilt": -1720.0, "ZoomX": 1.25, "ZoomY": 1.0},
         item),
    ])
    _run(recorder, timeline, tmp_path)
    assert item.property_writes == [("Tilt", -1720.0)]
    assert item.GetProperty("Pan") == 40.0
    assert item.GetProperty("Tilt") == -1720.0


def test_a_value_the_item_does_not_carry_is_written(tmp_path):
    """The skip must never cost a write the item needs."""
    item = _item(Pan=40.0)
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 41.0}, item),
    ])
    _run(recorder, timeline, tmp_path)
    assert item.property_writes == [("Pan", 41.0)]
    assert item.GetProperty("Pan") == 41.0


def test_a_property_that_cannot_be_read_is_written(tmp_path):
    """An unreadable property is not a property already carried."""
    item = _item()

    def unreadable(key=None):
        if key is None:
            return {}
        raise RuntimeError("the property is not readable")

    item.GetProperty = unreadable
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 40.0}, item),
    ])
    _run(recorder, timeline, tmp_path)
    assert item.property_writes == [("Pan", 40.0)]


def test_a_property_that_is_not_a_number_is_never_skipped(tmp_path):
    """`Scaling` is a dictionary in Resolve: it cannot be compared, so it
    is always written."""
    item = _item()
    item.properties["Scaling"] = {"X": 1.0, "Y": 1.0}
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Scaling": 1.0}, item),
    ])
    _run(recorder, timeline, tmp_path)
    assert item.property_writes == [("Scaling", 1.0)]


def test_the_item_list_is_read_once_per_track_and_not_once_per_spec(
        tmp_path, monkeypatch):
    """The O(specs x items) shape: the same track's items were fetched
    again for every spec that named it."""
    reads = Counter()
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 40.0}, _item()),
        (1, 48, {"Pan": 40.0}, _item()),
        (2, 96, {"Pan": 40.0}, _item()),
    ])
    original = timeline.GetItemListInTrack

    def counted(kind, index):
        reads[(kind, index)] += 1
        return original(kind, index)

    monkeypatch.setattr(timeline, "GetItemListInTrack", counted)
    _run(recorder, timeline, tmp_path)
    assert reads == {("video", 1): 1, ("video", 2): 1}


def test_a_handle_that_was_given_the_property_is_never_asked_whether_the_item_carries_it(
        tmp_path):
    """Resolve's write handle echoes back what it was given, so a read on
    it cannot prove what the item carries: the write happens."""
    class _Echoing(FakeTimelineItem):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._given = {}

        def GetProperty(self, key=None):
            if key is not None and key in self._given:
                return self._given[key]
            return super().GetProperty(key)

        def SetProperty(self, key, value):
            self._given[key] = value
            return super().SetProperty(key, value)

    item = _Echoing("source.mov", None, start=0, duration=48)
    item.properties = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0, "ZoomY": 1.0}
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 40.0}, item),
        (1, 0, {"Pan": 40.0}, item),
    ])
    _run(recorder, timeline, tmp_path)
    assert item.property_writes == [("Pan", 40.0), ("Pan", 40.0)]
    # The second round asked nothing: the handle had been given Pan.
    assert recorder.profile["transform_writes"]["read_backs"] == 1


def test_the_no_op_fraction_is_measured_on_the_profile(tmp_path):
    """The number that decides the skip is measured, not guessed."""
    item = _item(Pan=40.0)
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 40.0, "Tilt": -1720.0}, item),
    ])
    _run(recorder, timeline, tmp_path)
    assert recorder.profile["transform_writes"] == {
        "applied": 1, "skipped": 1, "read_backs": 2}


def test_a_refused_write_still_refuses_the_build(tmp_path):
    """Skipping a write must not turn a refusal into a silent pass."""
    item = _item()
    item.refuse_properties = frozenset({"Pan"})
    _project, recorder, timeline = _world(tmp_path, rows=[
        (1, 0, {"Pan": 40.0}, item),
    ])
    with pytest.raises(Exception) as raised:
        _run(recorder, timeline, tmp_path)
    assert "Pan" in str(raised.value)
