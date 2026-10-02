"""`marker_capture` driven against a REAL running DaVinci Resolve.

Same bargain as `tests/qualification/test_marker_feedback_against_resolve.py`: every
test here builds its OWN throwaway timeline, named for this pytest
process, and deletes it again.  Nothing touches a timeline that was
already there, the timeline Resolve had open is restored afterwards, and
the gallery is put back to the still count it was found with.

The project folder is always `tmp_path`, passed to `capture` explicitly.
The button's own project DISCOVERY walks up from the footage on the
timeline, and the footage this test borrows is the captain's - so letting
it discover would write a test's stills into a real project.  That walk
is proved on real directories in `tests/unit/resolve/test_markers.py`.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import uuid

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from library.tools.marker_capture import (  # noqa: E402
    CREATED_MARKER_NAME,
    CaptureError,
    capture,
    project_folder_from_timeline,
    read_playhead,
)
from library.tools.marker_feedback import (  # noqa: E402
    ResolveUnavailable,
    connect_resolve,
    pull,
    read_notes,
)
from library.tools.marker_payload import (  # noqa: E402
    KIND_STILL,
    attachments_of,
    is_envelope,
    records_of,
)

RESOLVE_NEEDED = (
    "DaVinci Resolve is not running with a project whose media pool has a "
    "video clip, so there is nothing real to grab a frame from"
)

CLIP_IN, CLIP_LEN = 100, 120
#: The frame the playhead is parked on, as an OFFSET into the scratch
#: timeline. Set through `_tc` at the timeline's own rate: the scratch
#: timeline inherits the open project's rate, and a fixed "00:00:01:10"
#: is frame 40 only at 30fps - at 23.976 it is 34, the capture then
#: creates a new marker beside the typed one, and the typed-text test
#: failed on whichever project happened to be open (2026-09-25).
CAPTURE_KEY = 40


def _png_size(path) -> tuple:
    """(width, height) from the PNG header, with no image library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


@pytest.fixture(scope="module")
def resolve_project(resolve_session):
    """The live instance, LEASED - this file is a writer nobody counted.

    These tests create timelines, switch the current one and delete
    them.  That is a `RESOLVE_CURSOR` operation
    (`concurrency_routing`), and it ran beside the captain and beside
    other lanes with nothing between them: four full-suite runs at once
    wedged at 0% CPU holding a Resolve handle on 2026-09-11.
    `resolve_session` skips this module, naming the holder, rather than
    waiting for one.
    """
    try:
        resolve = connect_resolve()
    except ResolveUnavailable as exc:
        pytest.skip(f"{RESOLVE_NEEDED} ({exc})")
    project = resolve.GetProjectManager().GetCurrentProject()
    if not project:
        pytest.skip(RESOLVE_NEEDED)
    clips = [
        c for c in (project.GetMediaPool().GetRootFolder().GetClipList() or [])
        if (c.GetClipProperty("Type") or "").startswith("Video")
        and int(c.GetClipProperty("Frames") or 0) > CLIP_IN + CLIP_LEN
    ]
    if not clips:
        pytest.skip(RESOLVE_NEEDED)
    # Prefer a clip whose own resolution is NOT the delivery format, so
    # "the still is the conformed frame" has something to discriminate
    # against. Which clips this borrowed project happens to hold is not
    # something a test may assume, so it is a preference, not a filter.
    delivery = (f'{project.GetSetting("timelineResolutionWidth")}x'
                f'{project.GetSetting("timelineResolutionHeight")}')
    clips.sort(key=lambda c: (c.GetClipProperty("Resolution") or "") == delivery)
    previous = project.GetCurrentTimeline()
    yield resolve, project, clips[0]
    if previous:
        project.SetCurrentTimeline(previous)


@pytest.fixture
def scratch(resolve_project):
    """A throwaway timeline, current, with the playhead on a known frame."""
    resolve, project, pool_clip = resolve_project
    media_pool = project.GetMediaPool()
    name = f"pytest_capture_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    timeline = media_pool.CreateEmptyTimeline(name)
    assert timeline, "Resolve declined to create the scratch timeline"
    project.SetCurrentTimeline(timeline)
    assert media_pool.AppendToTimeline([{
        "mediaPoolItem": pool_clip, "startFrame": CLIP_IN,
        "endFrame": CLIP_IN + CLIP_LEN - 1, "mediaType": 1, "trackIndex": 1,
    }]), "Resolve declined to place the scratch clip"
    assert timeline.SetCurrentTimecode(
        _tc(timeline.GetStartFrame() + CAPTURE_KEY, timeline)) is True
    try:
        yield resolve, project, timeline
    finally:
        media_pool.DeleteTimelines([timeline])


@pytest.fixture
def gallery_unchanged(resolve_project):
    """Fails the test if a capture left a still behind in the gallery."""
    _, project, _ = resolve_project
    gallery = project.GetGallery()
    album = gallery.GetCurrentStillAlbum()
    before = len(album.GetStills() or [])
    yield
    assert len(album.GetStills() or []) == before, (
        "the capture left a still in the gallery - repeated use would "
        "leave the captain a gallery to tidy"
    )


# ── The playhead ────────────────────────────────────────────────────


def _tc(absolute_frame, timeline):
    fps = int(round(float(timeline.GetSetting("timelineFrameRate"))))
    f, s = absolute_frame % fps, (absolute_frame // fps) % 60
    m, h = (absolute_frame // (fps * 60)) % 60, absolute_frame // (fps * 3600)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def test_the_playhead_really_goes_past_the_end_and_says_nothing(scratch):
    """`SetCurrentTimecode` returns True for a timecode the timeline does
    not have and the playhead READS BACK there - so the refusal has to be
    computed, not taken off a return value (§5)."""
    _, _, timeline = scratch
    beyond = "00:01:00:00"
    assert timeline.SetCurrentTimecode(beyond) is True
    assert timeline.GetCurrentTimecode() == beyond
    with pytest.raises(CaptureError, match="past the end"):
        read_playhead(timeline)


def test_a_capture_past_the_end_writes_nothing_at_all(
        scratch, tmp_path, gallery_unchanged):
    """Resolve grabs a BLACK still there. Nothing on disk, no marker."""
    _, project, timeline = scratch
    assert timeline.SetCurrentTimecode("00:01:00:00") is True
    with pytest.raises(CaptureError, match="past the end"):
        capture(timeline, project, tmp_path)
    assert not (tmp_path / "marker_feedback").exists()
    assert not (timeline.GetMarkers() or {})


# ── The still ───────────────────────────────────────────────────────


def test_the_still_carries_the_grade(scratch, tmp_path, gallery_unchanged):
    """Two grabs of one frame, a CDL apart. Identical bytes would mean the
    still is the ungraded source and the button is worth much less."""
    _, project, timeline = scratch
    before = capture(timeline, project, tmp_path).still.path.read_bytes()

    item = timeline.GetItemListInTrack("video", 1)[0]
    assert item.SetCDL({
        "NodeIndex": "1", "Slope": "1 1 1", "Offset": "0.15 -0.05 -0.05",
        "Power": "1 1 1", "Saturation": "0",
    }) is True
    after = capture(timeline, project, tmp_path).still.path.read_bytes()

    assert before != after, "the CDL did not reach the exported still"


# ── The marker ──────────────────────────────────────────────────────


def _envelope(timeline, key) -> dict:
    raw = timeline.GetMarkerCustomData(int(key))
    parsed = json.loads(raw)
    assert is_envelope(parsed), raw
    return parsed


def test_typed_text_survives_a_capture_onto_it(
        scratch, tmp_path, gallery_unchanged):
    """The one thing that must never break: the captain's own words."""
    _, project, timeline = scratch
    typed_name = "GRADE"
    typed_note = "we're too green here - compare the shot before"
    assert timeline.AddMarker(
        CAPTURE_KEY, "Red", typed_name, typed_note, 1, "") is True

    result = capture(timeline, project, tmp_path)

    assert result.marker_created is False
    marker = timeline.GetMarkers()[CAPTURE_KEY]
    assert marker["name"] == typed_name
    assert marker["note"] == typed_note
    assert marker["color"] == "Red"
    assert attachments_of(_envelope(timeline, CAPTURE_KEY))[0]["path"] == \
        result.still_relpath


def test_the_playhead_inside_a_long_marker_updates_that_marker(
        scratch, tmp_path, gallery_unchanged):
    """`UpdateMarkerCustomData` takes only the START frame, and a second
    marker on top of the captain's would be a change they did not make."""
    _, project, timeline = scratch
    assert timeline.AddMarker(30, "Green", "LONG", "spans this", 20, "") is True
    assert timeline.SetCurrentTimecode(_tc(timeline.GetStartFrame() + CAPTURE_KEY, timeline)) is True   # inside

    result = capture(timeline, project, tmp_path)

    assert result.marker_created is False
    assert result.marker_key == 30
    assert sorted(int(k) for k in timeline.GetMarkers()) == [30]
    assert timeline.GetMarkers()[30]["duration"] == 20
    assert records_of(_envelope(timeline, 30), KIND_STILL)


# ── What the reader sees ────────────────────────────────────────────


# ── Discovery, without writing anywhere it finds ────────────────────


def test_the_project_is_measured_off_the_footage_or_honestly_absent(scratch):
    """A capture with no folder given must never guess one."""
    _, project, timeline = scratch
    found = project_folder_from_timeline(timeline)
    if found is None:
        with pytest.raises(CaptureError, match="which pipeline project"):
            capture(timeline, project)
    else:
        assert (found / "project.yaml").is_file()
