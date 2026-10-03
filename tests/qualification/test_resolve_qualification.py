"""The Resolve behavioural contract, qualified against live Resolve.

The contract functions in ``tests.resolve_double`` are run offline by
``test_resolve_double_contract.py`` and here against a scratch Resolve
project. The captain's open project is saved and left before the first
write; qualification imports its source clip into a new project, then
restores the captain's exact project and timeline and deletes the scratch.

It drives the running Resolve, so it never runs by default: like every
``resolve_session`` test it skips unless ``REN_LIVE_RESOLVE=1``
(``tests/conftest.py``), set only inside a granted borrow-resolve window. Without a running Resolve and an accessible video file in the
open project, the live cases skip before switching projects.
"""

from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools.eval_harness import (
    SCRATCH_PREFIX,
    resolve_bracket_end,
    resolve_bracket_start,
)
from tests.resolve_double import CONTRACT_CHECKS

RESOLVE_NEEDED = (
    "DaVinci Resolve is not running with a project whose media pool has an "
    "accessible video clip, so there is nothing real to qualify"
)

CLIP_IN, CLIP_LEN = 100, 120


def _source_clip(project):
    """Read a usable file path without changing the captain's project."""
    root = project.GetMediaPool().GetRootFolder()
    folders = [root]
    clips = []
    while folders:
        folder = folders.pop()
        clips.extend(folder.GetClipList() or [])
        folders.extend(folder.GetSubFolderList() or [])
    for clip in clips:
        try:
            clip_type = clip.GetClipProperty("Type") or ""
            frames = int(clip.GetClipProperty("Frames") or 0)
            path = clip.GetClipProperty("File Path") or ""
        except (AttributeError, TypeError, ValueError):
            continue
        if (
            str(clip_type).startswith("Video")
            and frames > CLIP_IN + CLIP_LEN
            and path
            and os.path.isfile(path)
        ):
            return path
    return None


@pytest.fixture(scope="module")
def resolve_project(resolve_session):
    """A scratch project with one imported clip; captain project untouched."""
    from library.tools.marker_feedback import ResolveUnavailable, connect_resolve

    try:
        resolve = connect_resolve()
    except ResolveUnavailable as exc:
        pytest.skip(f"{RESOLVE_NEEDED} ({exc})")
    manager = resolve.GetProjectManager()
    captain = manager.GetCurrentProject()
    if not captain or not captain.GetCurrentTimeline():
        pytest.skip(RESOLVE_NEEDED)
    source_path = _source_clip(captain)
    if source_path is None:
        pytest.skip(RESOLVE_NEEDED)

    scratch_name = (
        f"{SCRATCH_PREFIX}resolve-qualification-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    )
    saved = resolve_bracket_start(scratch_name)
    try:
        # The scripting connection is now on the empty scratch project.
        resolve = connect_resolve()
        project = resolve.GetProjectManager().GetCurrentProject()
        imported = project.GetMediaPool().ImportMedia([source_path]) or []
        if not imported:
            pytest.skip(
                "Resolve could not import the read-only source clip "
                "into its scratch project"
            )
        pool_clip = imported[0]
        yield resolve, project, pool_clip
    finally:
        restored = resolve_bracket_end(scratch_name, saved)
        assert restored.get("project_restored"), (
            f"captain project was not restored: {restored}"
        )
        assert restored.get("timeline_restored"), (
            f"captain timeline was not restored: {restored}"
        )
        assert restored.get("scratch_deleted") is not False, (
            f"scratch project was not deleted: {restored}"
        )


def _all_timelines(project):
    return [
        project.GetTimelineByIndex(index)
        for index in range(1, project.GetTimelineCount() + 1)
    ]


@pytest.fixture
def live_contract(resolve_project):
    """Give one shared contract check a clean scratch project."""
    _, project, pool_clip = resolve_project
    settings = {
        key: project.GetSetting(key)
        for key in (
            "timelineResolutionWidth",
            "timelineResolutionHeight",
            "timelineFrameRate",
        )
    }
    try:
        yield project, pool_clip
    finally:
        timelines = _all_timelines(project)
        if timelines:
            deleted = project.GetMediaPool().DeleteTimelines(timelines)
            assert deleted is not False, "Resolve declined contract cleanup"
        pool_clip.DeleteMarkersByColor("All")
        assert project.SetSettings(settings) is not False, (
            "Resolve declined to restore scratch project settings"
        )


@pytest.mark.parametrize(
    "name,check",
    [pytest.param(name, check, id=name) for name, check in CONTRACT_CHECKS],
)
def test_same_resolve_contract_as_the_offline_double(live_contract, name, check):
    """Each case calls the very same check used by the offline contract."""
    project, pool_clip = live_contract
    check(project, pool_clip)


@pytest.fixture
def scratch(resolve_project):
    """A disposable current timeline in the scratch project."""
    _, project, pool_clip = resolve_project
    media_pool = project.GetMediaPool()
    before = project.GetCurrentTimeline()
    name = f"pytest_qual_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    timeline = media_pool.CreateEmptyTimeline(name)
    assert timeline, "Resolve declined to create the scratch timeline"
    project.SetCurrentTimeline(timeline)
    appended = media_pool.AppendToTimeline(
        [
            {
                "mediaPoolItem": pool_clip,
                "startFrame": CLIP_IN,
                "endFrame": CLIP_IN + CLIP_LEN - 1,
                "mediaType": 1,
                "trackIndex": 1,
            }
        ]
    )
    assert appended, "Resolve declined to place the scratch clip"
    try:
        yield project, pool_clip, timeline
    finally:
        pool_clip.DeleteMarkersByColor("All")
        assert media_pool.DeleteTimelines([timeline]) is not False, (
            "Resolve declined to delete the scratch timeline"
        )
        if before:
            project.SetCurrentTimeline(before)


def test_pull_all_reads_every_scratch_timeline_without_switching(
    tmp_path, resolve_project, scratch
):
    """The 2026-09-19 readback leaves Resolve's cursor where it began."""
    from library.tools.marker_feedback import _pull_all, pulled_files

    _, project, _ = resolve_project
    _, _, timeline = scratch
    open_before = project.GetCurrentTimeline().GetName()
    assert timeline.AddMarker(0, "Blue", "feedback", "pull-all probe", 1, "") is True
    try:
        assert _pull_all(str(tmp_path), project) == 0
        names = {payload.get("timeline") for _, payload in pulled_files(str(tmp_path))}
        assert timeline.GetName() in names
        assert len(names) == project.GetTimelineCount()
        assert project.GetCurrentTimeline().GetName() == open_before
    finally:
        timeline.DeleteMarkerAtFrame(0)


def test_reply_past_the_end_is_refused_by_ren(scratch):
    """Resolve accepts this marker; Ren's bounds check must refuse it."""
    from library.tools.marker_feedback import MarkerWriteError, place_reply_marker

    project, _, timeline = scratch
    end = int(timeline.GetEndFrame())
    with pytest.raises(MarkerWriteError):
        place_reply_marker(
            timeline, end + 50, "Green", "past the end", "must not land",
            project=project, idempotency_key="qualification:reply-past-end")
    assert end + 50 - int(timeline.GetStartFrame()) not in (timeline.GetMarkers() or {})


def test_clip_reply_outside_the_played_range_is_refused(scratch):
    """Neither edge of a clip marker range is clamped by Resolve."""
    from library.tools.marker_feedback import (
        MarkerWriteError,
        place_reply_clip_marker,
    )

    project, _, timeline = scratch
    item = timeline.GetItemListInTrack("video", 1)[0]
    first = int(item.GetLeftOffset())
    for key in (first - 1, first + int(item.GetDuration())):
        with pytest.raises(MarkerWriteError):
            place_reply_clip_marker(
                item, key, "Green", "outside", "must not land",
                project=project,
                idempotency_key=f"qualification:clip-reply-outside:{key}")
        assert key not in (item.GetMarkers() or {})


def test_answered_clip_question_is_removed_by_readback(scratch):
    """A missing marker is confirmed by the next read, not a return value."""
    from library.tools.marker_feedback import (
        place_reply_clip_marker,
        remove_clip_marker,
    )

    project, _, timeline = scratch
    item = timeline.GetItemListInTrack("video", 1)[0]
    key = int(item.GetLeftOffset()) + 9
    assert item.AddMarker(key, "Blue", "feedback", "asked", 1, "") is True
    try:
        assert remove_clip_marker(
            item, key, project=project,
            idempotency_key="qualification:clip-question-delete") is True
        assert key not in (item.GetMarkers() or {})
        assert remove_clip_marker(
            item, key, project=project,
            idempotency_key="qualification:clip-question-delete-again") \
            is False
        place_reply_clip_marker(
            item, key, "Green", "reply: done", "answered",
            project=project,
            idempotency_key="qualification:clip-reply")
        assert (item.GetMarkers() or {})[key]["color"] == "Green"
    finally:
        item.DeleteMarkerAtFrame(key)


def test_timeline_frames_are_read_from_the_real_start_and_end(scratch):
    """A single clip's played window supplies the timeline frame range."""
    _, _, timeline = scratch
    start, end = int(timeline.GetStartFrame()), int(timeline.GetEndFrame())
    assert end - start + 1 == CLIP_LEN
