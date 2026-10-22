"""A one-clip retime is scoped: the passage, and the clock's tail.

A retime is a CLOCK change: the passage plays at another rate, so it
occupies a different number of record frames and every placement after
it moves by the difference.  The scoped route used to refuse that,
naming the interval it could not place - which left the whole reel to
the full rebuild for a change that touches one clip.

These tests pin what the scoped route does with that change:

- the passage is re-placed at its new length, and the items the clock
  moved are re-placed at their new start - nothing else is touched;
- the patched staging matches a fresh full build of the same plan,
  item for item, so the scoped route and the rebuild place the SAME
  reel;
- the captions that rode the clock are re-placed with it, and the ones
  that did not are reused;
- a retime this route cannot scope - one that crosses a clip, and so
  changes how many items a row carries - still refuses, by name.

The measurement of what the scoped route COSTS is in
`test_reel_scoped_measure.py`: the writes and the seconds, before and
after, on the same plan.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from library.tools import reel_incremental as incr
from library.tools.reel_clock import rated_range
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import make_project
from tests.scenarios.test_reel_build import (
    FakeMoment,
    FakeTimeline,
    _clip_2,
    _media_properties,
)

FPS = 23.976
TIMELINE_NAME = "Reel 99 - scoped retime"
STAGING_NAME = "Reel 99 - scoped retime (rebuild staging)"

#: The rate the middle passage plays at in the retimed plan.
RATE = 1.1


def _clips():
    """Three angles, so a retime on the middle one leaves a tail behind."""
    return [
        _clip_2("video", 1, "SpeakerOne", "SpeakerOne",
                "/m/speakerone.MXF", 0.0, 10.0),
        _clip_2("video", 2, "SpeakerTwo", "SpeakerTwo",
                "/m/speakertwo.MXF", 10.0, 20.0),
        _clip_2("video", 3, "SpeakerThree", "SpeakerThree",
                "/m/speakerthree.MXF", 20.0, 30.0),
        _clip_2("audio", 1, "SpeakerOne CH1", "SpeakerOne",
                "/m/speakerone.MXF", 0.0, 10.0),
        _clip_2("audio", 2, "SpeakerTwo CH1", "SpeakerTwo",
                "/m/speakertwo.MXF", 10.0, 20.0),
        _clip_2("audio", 3, "SpeakerThree CH1", "SpeakerThree",
                "/m/speakerthree.MXF", 20.0, 30.0),
    ]


def _world():
    timeline = FakeTimeline()
    paths = ["/m/speakerone.MXF", "/m/speakertwo.MXF",
             "/m/speakerthree.MXF", "/m/cap.mov", "/m/cap_moved.mov"]
    project = make_project(width=1080, height=1920, frame_rate=FPS)
    pool = project.GetMediaPool()
    pool.next_timeline = timeline
    pool.audio_channels = (1,)
    pool.media_properties = {path: _media_properties(path) for path in paths}
    pool.ImportMedia(paths)
    return timeline, pool, project


def _transcript():
    return {"segments": []}


def _plain_ranges():
    return [(0.0, 10.0), (10.0, 20.0), (20.0, 30.0)]


def _retimed_ranges():
    """The middle passage plays at 110%; the reel after it moves."""
    return [(0.0, 10.0),
            rated_range(10.0, 20.0, [(10.0, 20.0, RATE)]),
            (20.0, 30.0)]


def _retimed_ranges_at(rate):
    """The same plan at another rate."""
    return [(0.0, 10.0),
            rated_range(10.0, 20.0, [(10.0, 20.0, rate)]),
            (20.0, 30.0)]


def _caption(start, end, name="cap", path="/m/cap.mov"):
    return {
        "overlay_path": path,
        "timeline_start": start,
        "timeline_end": end,
        "source_in_frame": 0,
        "segment_id": name,
    }


def _write_caption(tmp_path, name, payload):
    path = pathlib.Path(tmp_path) / name
    path.write_bytes(payload)
    return str(path)


def _build(tmp_path, captions, ranges):
    from library.tools.reel_build import build_reel_timeline

    timeline, pool, project = _world()
    record = build_reel_timeline(
        project, FakeMoment(), _clips(), list(captions), FPS, 1080, 1920,
        str(tmp_path), _transcript(), master_timeline=None,
        program_channels={"1": 1, "2": 1, "3": 1},
        timeline_name=TIMELINE_NAME, ranges=ranges)
    return timeline, pool, project, record


def _prepared(tmp_path, captions, ranges):
    from library.tools.reel_build import prepare_reel_timeline

    return prepare_reel_timeline(
        FakeMoment(), _clips(), list(captions), FPS, 1080, 1920,
        str(tmp_path), _transcript(), timeline_name=TIMELINE_NAME,
        master_timeline=None, program_channels={"1": 1, "2": 1, "3": 1},
        ranges=ranges)


def _staging(project, name=STAGING_NAME):
    for index in range(1, project.GetTimelineCount() + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate is not None and candidate.GetName() == name:
            return candidate
    return None


def _patch(project, final, prepared, tmp_path):
    from library.tools import reel_rebuild_need as _need

    recorded = _need.carried_digest_live(project, final)
    assert recorded
    with assume_sole_writer("scoped-retime"):
        return incr.build_staged_incremental(
            project=project, staging_name=STAGING_NAME,
            final_name=final.GetName(), recorded_carried=recorded,
            prepared=prepared, overlay_placements=None,
            explainer_segments=None, semantic_segments=None,
            lower_third_segments=None, look=None, grade_cdl=None,
            power_grade=None, motion=None, overlay_intent=None,
            suppressions=[], fps=FPS, width=1080, height=1920,
            project_folder=str(tmp_path), draw_gain=1.0, resolve_app=None,
            single_reel_scope=True, placement_mode="append",
            transcript=_transcript())


def _inventory(timeline):
    """{(media, index): sorted [(start, end, source, speed)]} per row."""
    out = {}
    for media in ("video", "audio"):
        try:
            count = timeline.GetTrackCount(media) or 0
        except Exception:
            continue
        for index in range(1, count + 1):
            rows = []
            for item in timeline.GetItemListInTrack(media, index) or []:
                pool_item = item.GetMediaPoolItem()
                source = (pool_item.GetClipProperty("File Path")
                          if pool_item is not None else "")
                rows.append((item.GetStart(), item.GetEnd(), source,
                             round(float(item.GetSpeed()["Percentage"]), 6)))
            out[(media, index)] = sorted(rows)
    return out


def _scenario(tmp_path):
    """The old reel, the new plan, and the captions that ride the clock.

    The caption over the first passage does not move - it plays before
    the retime.  The caption over the third passage DOES move: the clock
    change shortens the reel, so the last clip - and the card over it -
    starts earlier.
    """
    tmp = pathlib.Path(tmp_path)
    early = _write_caption(tmp, "cap_early.mov", b"early-card")
    late_old = _write_caption(tmp, "cap_late_old.mov", b"late-card-old")
    late_new = _write_caption(tmp, "cap_late_new.mov", b"late-card-new")
    shift = (1.0 - 1.0 / RATE) * 10.0  # the reel is shorter by this much
    old_captions = [
        _caption(1.0, 3.0, "cap-early", early),
        _caption(21.0, 23.0, "cap-late", late_old),
    ]
    new_captions = [
        _caption(1.0, 3.0, "cap-early", early),
        _caption(21.0 - shift, 23.0 - shift, "cap-late", late_new),
    ]
    return old_captions, new_captions


def test_a_one_clip_retime_is_patched_and_matches_a_full_build(tmp_path):
    """The patched staging equals a fresh full build of the same plan.

    The passage is re-placed at its new length and plays at its rate;
    the tail the clock moved is re-placed at its new start.  The result
    must be the build's own result, item for item.
    """
    old_captions, new_captions = _scenario(tmp_path)

    final, _pool, project, _record = _build(
        tmp_path, old_captions, _plain_ranges())
    prepared = _prepared(tmp_path, new_captions, _retimed_ranges())

    from library.tools.reel_build import build_reel_timeline
    _ref_timeline, _ref_pool, ref_project = _world()
    build_reel_timeline(
        ref_project, FakeMoment(), _clips(), list(new_captions), FPS,
        1080, 1920, str(tmp_path), _transcript(), master_timeline=None,
        program_channels={"1": 1, "2": 1, "3": 1},
        timeline_name=TIMELINE_NAME, ranges=_retimed_ranges())

    _patch(project, final, prepared, tmp_path)
    staging = _staging(project)
    assert staging is not None, "the scoped route staged nothing"
    assert _inventory(staging) == _inventory(_ref_timeline)


def test_the_scoped_retime_leaves_the_untouched_reel_alone(tmp_path):
    """The passage and the clock's tail are re-placed; the rest is not.

    The first passage plays before the retime, so its item is the one
    the duplicate already carried - the same media at the same frames.
    """
    old_captions, new_captions = _scenario(tmp_path)
    final, _pool, project, _record = _build(
        tmp_path, old_captions, _plain_ranges())
    prepared = _prepared(tmp_path, new_captions, _retimed_ranges())
    _patch(project, final, prepared, tmp_path)
    staging = _staging(project)
    assert staging is not None

    first = [row for row in staging.GetItemListInTrack("video", 1) or []]
    assert len(first) == 1
    assert (first[0].GetStart(), first[0].GetEnd()) == (0, 240), (
        "the passage before the retime moved, and it should not have")

    retimed = [row for row in staging.GetItemListInTrack("video", 2) or []]
    assert len(retimed) == 1
    assert (retimed[0].GetStart(), retimed[0].GetEnd()) == (240, 458), (
        "the retimed passage did not take its new length")
    assert round(float(retimed[0].GetSpeed()["Percentage"]), 3) == 110.0, (
        "the retimed passage does not play at its rate")

    tail = [row for row in staging.GetItemListInTrack("video", 3) or []]
    assert len(tail) == 1
    assert (tail[0].GetStart(), tail[0].GetEnd()) == (458, 697), (
        "the clock did not move the tail to where the plan puts it")


def test_the_scope_names_the_passage_and_the_moved_items(tmp_path):
    """The record says what the retime touched: one passage, one tail.

    A scope nobody can read is a scope nobody can measure, and the
    point of the scoped route is that the work is proportional to the
    change.
    """
    old_captions, new_captions = _scenario(tmp_path)
    final, _pool, project, _record = _build(
        tmp_path, old_captions, _plain_ranges())
    prepared = _prepared(tmp_path, new_captions, _retimed_ranges())
    record = _patch(project, final, prepared, tmp_path)

    scope = record["change_scope"]
    assert scope["items"] >= 1
    assert "retime" in scope["reason"]
    retime = record["footage_retime"]
    assert retime["delta_frames"] == -22
    assert len(retime["replaced"]) == 2, (
        "the passage is on a picture row and a speech row")
    assert len(retime["moved"]) == 2, (
        "the tail is on a picture row and a speech row")
    assert retime["reused"] == 2, (
        "the passage before the retime is reused, on a picture row and a "
        "speech row")


def test_a_retime_that_crosses_a_clip_still_refuses(tmp_path):
    """A retime that splits a clip is a shape this route does not claim.

    The passage's boundary falls inside a clip, so the row's item COUNT
    changes.  The scoped route refuses by name, and the full rebuild
    places the reel.
    """
    old_captions, new_captions = _scenario(tmp_path)
    final, _pool, project, _record = _build(
        tmp_path, old_captions, _plain_ranges())
    # The passage covers the first clip AND the first two seconds of the
    # second: the second clip is split by the passage's edge.
    crossing = [rated_range(0.0, 20.0, [(8.0, 20.0, RATE)]),
                (20.0, 30.0)]
    prepared = _prepared(tmp_path, new_captions, crossing)
    with assume_sole_writer("scoped-retime-crossing"):
        with pytest.raises(incr.IncrementalRefused, match="retime|clip"):
            _patch(project, final, prepared, tmp_path)


def test_a_retime_that_CHANGES_the_rate_is_also_scoped(tmp_path):
    """The passage was already retimed; the captain changed the rate.

    The passage still occupies fewer frames, the tail still moves, and
    the item still plays the same source - so the scoped route re-places
    it and the reel it produces is the rebuild's reel.
    """
    old_captions, new_captions = _scenario(tmp_path)
    final, _pool, project, _record = _build(
        tmp_path, old_captions, _retimed_ranges_at(1.05))
    prepared = _prepared(tmp_path, new_captions, _retimed_ranges_at(1.1))

    from library.tools.reel_build import build_reel_timeline
    _ref_timeline, _ref_pool, ref_project = _world()
    build_reel_timeline(
        ref_project, FakeMoment(), _clips(), list(new_captions), FPS,
        1080, 1920, str(tmp_path), _transcript(), master_timeline=None,
        program_channels={"1": 1, "2": 1, "3": 1},
        timeline_name=TIMELINE_NAME, ranges=_retimed_ranges_at(1.1))

    _patch(project, final, prepared, tmp_path)
    staging = _staging(project)
    assert staging is not None
    assert _inventory(staging) == _inventory(_ref_timeline)


def test_the_retime_scope_names_the_passage_and_its_reach():
    """The scope of a retime is the passage plus the frames it moves."""
    from library.tools.reel_retime_scope import (
        RetimeShapeError,
        passages_from_placements,
    )

    placements = [
        {"rate": 1.1, "snapped_record": 0, "record_frames": 87,
         "source_in": 0.0, "source_out": 4.0, "clip": object()},
    ]
    passages = passages_from_placements(placements, 24.0)
    assert len(passages) == 1
    assert passages[0].rate == 1.1
    assert passages[0].span == (0, 87)

    # Two clips under one rate is a shape this route does not scope.
    with pytest.raises(RetimeShapeError, match="more than one clip"):
        passages_from_placements([
            {"rate": 1.1, "snapped_record": 0, "record_frames": 87,
             "source_in": 0.0, "source_out": 4.0, "clip": object()},
            {"rate": 1.1, "snapped_record": 87, "record_frames": 87,
             "source_in": 4.0, "source_out": 8.0, "clip": object()},
        ], 24.0)
