"""The placement pass asks Resolve for the timeline's units ONCE.

The defect these guard, measured on the scratch copy's reel 13 (the
critical-path profile, docs/benchmarks/2026-10-21-critical-path.md):
the deferred placement pass spent 154.8 s of its 155 s phase inside
per-overlay reads. Every overlay's read-back built its OWN transform
read context, and each context re-read the current timeline, both
timelines' identities and both resolutions - about nineteen Resolve
calls - before it read ONE item's transform. The values read back were
right; the context that established their units was rebuilt per item.

The pass now opens ONE context for the whole reel. The per-read cursor
check stays (a cursor that moved refuses at the item that saw it), the
batch's resolution check happens once at the end, and the values read
back are the same values.
"""

import inspect

import pytest

from library.tools.overlay_placement import apply_placement_transform
from library.tools.reel_otio_placement import (
    OtioPlacementRefused,
    PlacementRecorder,
    run_deferred,
)
from tests.resolve_double import (
    FakeTimeline,
    FakeTimelineItem,
    make_pool_clip,
    make_project,
)

SOURCE = "/m/source.mov"


def _placement(index: int) -> dict:
    """A placement that is this overlay's own, so a mis-targeted item
    is visible in the values that landed."""
    return {"scaling": 1.0, "pan": 100.0 + index, "tilt": -80.0 - index,
            "zoom_x": 1.0, "zoom_y": 1.0}


class _Counter:
    """How many times the engine asked Resolve for something."""

    def __init__(self):
        self.settings = 0
        self.current_timeline = 0
        self.item_lists = 0
        self.starts = 0


def _counting(project, timeline, counter):
    """Count the reads that establish the timeline's units."""
    real_setting = timeline.GetSetting
    real_current = project.GetCurrentTimeline
    real_list = timeline.GetItemListInTrack

    def setting(key=None):
        counter.settings += 1
        return real_setting(key)

    def current():
        counter.current_timeline += 1
        return real_current()

    def item_list(kind, index):
        counter.item_lists += 1
        return real_list(kind, index)

    timeline.GetSetting = setting
    project.GetCurrentTimeline = current
    timeline.GetItemListInTrack = item_list
    for item in list(real_list("video", 1) or []):
        real_start = item.GetStart

        def start(*args, _original=real_start, **kwargs):
            counter.starts += 1
            return _original(*args, **kwargs)

        item.GetStart = start
    return counter


def _world(tmp_path, overlays, *, counter=None, project=None):
    """A recorded placement of `overlays` overlays, and the timeline.

    The overlays are recorded the way the placers record them - a
    `RecordingTimeline` defers the transform, and `run_deferred` runs
    the deferred calls on the real timeline.
    """
    project = project or make_project("Deferred")
    pool = project.GetMediaPool()
    pool.GetRootFolder()._clips.append(
        make_pool_clip("source.mov", path=SOURCE))
    recorder = PlacementRecorder(pool)
    timeline = FakeTimeline(
        "deferred", project=project,
        settings={"timelineResolutionWidth": "1080",
                  "timelineResolutionHeight": "1920"})
    project.adopt(timeline)
    project.SetCurrentTimeline(timeline)
    for index in range(overlays):
        recorder.AppendToTimeline([{
            "mediaPoolItem": recorder.pooled(SOURCE),
            "startFrame": 10, "endFrame": 30, "mediaType": 1,
            "trackIndex": 1, "recordFrame": index * 48}])
        item = FakeTimelineItem("source.mov", timeline, start=index * 48,
                                 duration=20)
        item.properties = {"Pan": 0.0, "Tilt": 0.0, "ZoomX": 1.0,
                           "ZoomY": 1.0}
        timeline.add_item("video", 1, item)
        apply_placement_transform(
            recorder.timeline, 1, index * 48, _placement(index),
            label=f"overlay {index}", resolve_project=project,
            project_folder=str(tmp_path), timeline_name="deferred")
    if counter is not None:
        _counting(project, timeline, counter)
    return project, recorder, timeline


def _run(project, recorder, timeline, tmp_path):
    """Run the deferred pass, with the project where the pass takes it.

    The `resolve_project` argument is what lets the pass open ONE read
    context; a caller that does not pass it keeps the per-item context,
    and the pass still runs.
    """
    kwargs = {}
    if "resolve_project" in inspect.signature(run_deferred).parameters:
        kwargs["resolve_project"] = project
    return run_deferred(timeline, recorder, project_folder=str(tmp_path),
                        project="Deferred", timeline_name="deferred",
                        **kwargs)


def test_the_number_of_overlays_does_not_change_how_often_the_engine_asks(
        tmp_path):
    """The context is built once for the reel, not once per overlay.

    Before: every overlay's read-back re-read the current timeline and
    both resolutions, so the count grew with the reel. Now the count is
    the same for one overlay and for nine.
    """
    one = _Counter()
    project, recorder, timeline = _world(tmp_path, 1, counter=one)
    _run(project, recorder, timeline, tmp_path)
    nine = _Counter()
    project, recorder, timeline = _world(tmp_path, 9, counter=nine)
    _run(project, recorder, timeline, tmp_path)
    assert nine.settings == one.settings, (
        f"the pass asked Resolve for the timeline's resolution "
        f"{nine.settings} times for nine overlays and {one.settings} times "
        f"for one: the context is still being built per overlay")


def test_one_overlay_still_asks_resolve_for_the_units(tmp_path):
    """The shared context is not the absence of one.

    A pass that read the units zero times would pass the test above for
    the wrong reason: the read-back would judge values in units nobody
    established. The units are still asked for, and still asked for
    before the first overlay is judged.
    """
    counter = _Counter()
    project, recorder, timeline = _world(tmp_path, 3, counter=counter)
    _run(project, recorder, timeline, tmp_path)
    assert counter.settings > 0
    assert counter.current_timeline > 0


def test_the_values_the_pass_reads_back_are_the_values_it_wrote(tmp_path):
    """The shared context changes no placement.

    Every overlay lands the placement it was given - each one its own,
    so an overlay that landed on its neighbour would show - and none is
    reported as refused: the read-back judges the same values it judged
    when each one built its own context.
    """
    project, recorder, timeline = _world(tmp_path, 4)
    _run(project, recorder, timeline, tmp_path)
    for index, item in enumerate(
            timeline.GetItemListInTrack("video", 1)):
        placement = _placement(index)
        assert item.GetProperty("Pan") == placement["pan"]
        assert item.GetProperty("Tilt") == placement["tilt"]
        assert item.GetProperty("ZoomX") == placement["zoom_x"]
        assert item.GetProperty("ZoomY") == placement["zoom_y"]


def test_the_row_is_asked_for_once_for_the_pass_not_once_per_placement(
        tmp_path):
    """The reel's items are fetched once, and named by position.

    Before: every placement asked Resolve for the row's items and then
    walked it, reading each item's start frame until it found its own -
    so a row of thirty placements read 1 + 2 + ... + 30 start frames,
    twice over. The pass now indexes the row once.
    """
    counter = _Counter()
    project, recorder, timeline = _world(tmp_path, 12, counter=counter)
    _run(project, recorder, timeline, tmp_path)
    assert counter.starts <= 4 * 12 + 4, (
        f"the pass read {counter.starts} start frames for twelve "
        f"placements: the row is still being walked per placement")
    assert counter.item_lists <= 12 + 3, (
        f"the pass asked Resolve for the row's items {counter.item_lists} "
        f"times for twelve placements: the row is still being fetched per "
        f"placement")


def test_a_cursor_that_moves_under_the_pass_is_reported_not_ignored(
        tmp_path, capsys):
    """The per-read cursor check survives the shared context.

    A context that is built once must not let a moved cursor pass: the
    read that sees the move refuses, exactly as it did when every read
    built its own context.
    """
    other = FakeTimeline("somewhere else")
    project = make_project("Deferred")
    project, recorder, timeline = _world(tmp_path, 2, project=project)
    real_current = project.GetCurrentTimeline
    calls = {"n": 0}

    def moved():
        calls["n"] += 1
        # The cursor moves AFTER the pass established its context.
        return other if calls["n"] > 2 else real_current()

    project.GetCurrentTimeline = moved
    try:
        _run(project, recorder, timeline, tmp_path)
    except OtioPlacementRefused:
        # The batch's own check may refuse the reel as well: the cursor
        # that moved is not the one the context established.
        pass
    printed = capsys.readouterr()
    assert "transform units could not be established" in printed.err, (
        "the cursor moved under the pass and the pass said nothing: "
        f"{printed.err!r}")


def test_a_resolution_that_changes_under_the_pass_refuses_the_reel(
        tmp_path):
    """A batch whose units changed cannot be judged, so it refuses.

    The context was established against one resolution and the reel was
    read against another: the read-backs are in units that no longer
    describe the timeline, so the pass refuses rather than report a
    placement it cannot judge.
    """
    project, recorder, timeline = _world(tmp_path, 3)
    real_current = project.GetCurrentTimeline
    calls = {"n": 0}

    def moved():
        calls["n"] += 1
        if calls["n"] > 2:
            timeline._settings["timelineResolutionWidth"] = "2160"
        return real_current()

    project.GetCurrentTimeline = moved
    with pytest.raises(OtioPlacementRefused):
        _run(project, recorder, timeline, tmp_path)
