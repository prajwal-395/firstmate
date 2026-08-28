"""`marker_capture` driven against a REAL running DaVinci Resolve.

Same bargain as `tests/test_marker_feedback_against_resolve.py`: every
test here builds its OWN throwaway timeline, named for this pytest
process, and deletes it again.  Nothing touches a timeline that was
already there, the timeline Resolve had open is restored afterwards, and
the gallery is put back to the still count it was found with.

The project folder is always `tmp_path`, passed to `capture` explicitly.
The button's own project DISCOVERY walks up from the footage on the
timeline, and the footage this test borrows is the captain's - so letting
it discover would write a test's stills into a real project.  That walk
is proved on real directories in `tests/test_marker_payload.py`.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
CAPTURE_TC = "00:00:01:10"        # 40 frames in at 30fps
CAPTURE_KEY = 40


def _png_size(path) -> tuple:
    """(width, height) from the PNG header, with no image library."""
    header = path.read_bytes()[:24]
    assert header[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    return struct.unpack(">II", header[16:24])


@pytest.fixture(scope="module")
def resolve_project():
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
    assert timeline.SetCurrentTimecode(CAPTURE_TC) is True
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


def test_the_playhead_reads_back_as_the_frame_the_marker_lands_on(scratch):
    _, _, timeline = scratch
    playhead = read_playhead(timeline)
    assert playhead.timecode == CAPTURE_TC
    assert playhead.marker_key == CAPTURE_KEY
    assert playhead.absolute_frame == playhead.start_frame + CAPTURE_KEY

    # And that key really is where Resolve puts a marker.
    assert timeline.AddMarker(
        playhead.marker_key, "Blue", "probe", "", 1, "") is True
    assert sorted(int(k) for k in timeline.GetMarkers()) == [CAPTURE_KEY]
    note = [n for n in read_notes(timeline) if n.text == "probe"][0]
    assert note.frame == playhead.absolute_frame
    assert note.timecode == CAPTURE_TC


def _tc(absolute_frame, timeline):
    fps = int(round(float(timeline.GetSetting("timelineFrameRate"))))
    f, s = absolute_frame % fps, (absolute_frame // fps) % 60
    m, h = (absolute_frame // (fps * 60)) % 60, absolute_frame // (fps * 3600)
    return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"


def test_the_end_frame_is_absolute_and_bounds_the_playable_range(scratch):
    """What the past-the-end refusal is measured against."""
    _, _, timeline = scratch
    start, end = int(timeline.GetStartFrame()), int(timeline.GetEndFrame())
    item = timeline.GetItemListInTrack("video", 1)[0]
    assert end - start == int(item.GetDuration())
    assert int(item.GetEnd()) - int(item.GetStart()) == int(item.GetDuration())

    last = end - 1
    assert timeline.SetCurrentTimecode(_tc(last, timeline)) is True
    assert read_playhead(timeline).marker_key == last - start


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


def test_a_moved_timeline_origin_does_not_move_the_marker(scratch):
    """`GetStartFrame()` is the origin for a key and not for the playhead."""
    _, _, timeline = scratch
    assert timeline.SetStartTimecode("01:00:00:00") is True
    assert timeline.SetCurrentTimecode("01:00:01:10") is True
    playhead = read_playhead(timeline)
    assert playhead.marker_key == CAPTURE_KEY
    assert playhead.absolute_frame == int(timeline.GetStartFrame()) + CAPTURE_KEY


# ── The still ───────────────────────────────────────────────────────


def test_the_still_is_the_conformed_frame(scratch, tmp_path,
                                          gallery_unchanged):
    """It comes out at the DELIVERY resolution.

    Where the borrowed clip's own shape differs from the delivery format -
    which the fixture prefers - that is also the whole discrimination
    against the raw source frame, and the message names both so a failure
    says which one arrived.  `test_the_still_carries_the_grade` proves the
    same point a second way, needing no shape difference at all.
    """
    _, project, timeline = scratch
    result = capture(timeline, project, tmp_path)

    assert result.still.path.is_file()
    assert result.still.path.stat().st_size > 0
    still = "x".join(str(n) for n in _png_size(result.still.path))
    delivery = (f'{timeline.GetSetting("timelineResolutionWidth")}x'
                f'{timeline.GetSetting("timelineResolutionHeight")}')
    source = timeline.GetItemListInTrack("video", 1)[0] \
        .GetMediaPoolItem().GetClipProperty("Resolution")
    assert still == delivery, (
        f"the still is {still}, the timeline delivers {delivery} and the "
        f"source clip is {source}: this is not the frame the captain is "
        f"looking at"
    )


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


def test_nothing_export_stills_wrote_unasked_reaches_the_project(
        scratch, tmp_path, gallery_unchanged):
    """`ExportStills` writes a PowerGrade `.drx` sidecar unasked, and a
    name of its own choosing (`<prefix>_1.1.1.png`). Everything it wrote
    other than the one PNG is accounted for and discarded - asserted as
    that invariant rather than as "a .drx exists", which would be this
    test insisting Resolve keep a habit nobody wants."""
    _, project, timeline = scratch
    result = capture(timeline, project, tmp_path)

    stills_dir = result.still.path.parent
    assert [p.name for p in stills_dir.iterdir()] == [result.still.path.name]

    exported = result.still.exported_names
    pngs = [n for n in exported if n.endswith(".png")]
    assert pngs, f"ExportStills wrote no PNG: {exported}"
    assert set(result.still.discarded) == set(exported) - {pngs[0]}


def test_the_still_lives_outside_pipeline_output(
        scratch, tmp_path, gallery_unchanged):
    """`marker_feedback/` is CAPTURED, and a re-run must not reach it."""
    _, project, timeline = scratch
    result = capture(timeline, project, tmp_path)
    assert "pipeline_output" not in str(result.still.path)
    assert result.still_relpath.startswith("marker_feedback/stills/")


# ── The marker ──────────────────────────────────────────────────────


def _envelope(timeline, key) -> dict:
    raw = timeline.GetMarkerCustomData(int(key))
    parsed = json.loads(raw)
    assert is_envelope(parsed), raw
    return parsed


def test_a_frame_with_no_marker_gets_one(scratch, tmp_path, gallery_unchanged):
    _, project, timeline = scratch
    assert not (timeline.GetMarkers() or {})

    result = capture(timeline, project, tmp_path)

    assert result.marker_created is True
    assert result.marker_key == CAPTURE_KEY
    markers = timeline.GetMarkers()
    assert sorted(int(k) for k in markers) == [CAPTURE_KEY]
    assert markers[CAPTURE_KEY]["name"] == CREATED_MARKER_NAME

    records = records_of(_envelope(timeline, CAPTURE_KEY), KIND_STILL)
    assert len(records) == 1
    assert records[0]["path"] == result.still_relpath
    assert records[0]["timeline_frame"] == result.playhead.absolute_frame
    assert records[0]["timecode"] == CAPTURE_TC
    assert records[0]["id"] == result.record_id


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


def test_a_second_capture_appends_rather_than_replacing(
        scratch, tmp_path, gallery_unchanged):
    _, project, timeline = scratch
    first = capture(timeline, project, tmp_path)
    assert timeline.SetCurrentTimecode(CAPTURE_TC) is True
    second = capture(timeline, project, tmp_path)

    assert second.marker_created is False
    assert first.still.path != second.still.path
    assert first.still.path.is_file() and second.still.path.is_file()
    paths = [r["path"] for r in attachments_of(_envelope(timeline, CAPTURE_KEY))]
    assert paths == [first.still_relpath, second.still_relpath]
    assert first.envelope_id == second.envelope_id, "the marker's id is stable"


def test_the_playhead_inside_a_long_marker_updates_that_marker(
        scratch, tmp_path, gallery_unchanged):
    """`UpdateMarkerCustomData` takes only the START frame, and a second
    marker on top of the captain's would be a change they did not make."""
    _, project, timeline = scratch
    assert timeline.AddMarker(30, "Green", "LONG", "spans this", 20, "") is True
    assert timeline.SetCurrentTimecode(CAPTURE_TC) is True   # frame 40, inside

    result = capture(timeline, project, tmp_path)

    assert result.marker_created is False
    assert result.marker_key == 30
    assert sorted(int(k) for k in timeline.GetMarkers()) == [30]
    assert timeline.GetMarkers()[30]["duration"] == 20
    assert records_of(_envelope(timeline, 30), KIND_STILL)


def test_customdata_the_button_did_not_write_is_kept(
        scratch, tmp_path, gallery_unchanged):
    _, project, timeline = scratch
    foreign = "written by something else"
    assert timeline.AddMarker(
        CAPTURE_KEY, "Blue", "N", "", 1, foreign) is True

    capture(timeline, project, tmp_path)

    envelope = _envelope(timeline, CAPTURE_KEY)
    assert envelope["foreign"] == foreign
    assert records_of(envelope, KIND_STILL)


# ── What the reader sees ────────────────────────────────────────────


def test_the_reader_surfaces_the_still_as_a_path_it_can_open(
        scratch, tmp_path, gallery_unchanged):
    _, project, timeline = scratch
    typed = "why is this cut here"
    assert timeline.AddMarker(CAPTURE_KEY, "Blue", "Q", typed, 1, "") is True
    result = capture(timeline, project, tmp_path)

    note = [n for n in read_notes(timeline, str(tmp_path))
            if n.note == typed][0]
    assert len(note.attachments) == 1
    attachment = note.attachments[0]
    assert attachment["kind"] == KIND_STILL
    assert attachment["origin"] == "custom_data"
    assert attachment["exists"] is True
    assert attachment["resolved_path"] == str(result.still.path)
    assert note.name == "Q" and note.note == typed


def test_pull_records_the_attachment_where_a_re_render_cannot_reach_it(
        scratch, tmp_path, gallery_unchanged):
    _, project, timeline = scratch
    assert timeline.AddMarker(CAPTURE_KEY, "Blue", "Q", "look", 1, "") is True
    result = capture(timeline, project, tmp_path)

    written = pull(str(tmp_path), timeline, project)
    payload = json.loads(
        (tmp_path / "marker_feedback" /
         os.path.basename(written["path"])).read_text(encoding="utf-8"))
    notes = [n for n in payload["notes"] if n["note"] == "look"]
    assert len(notes) == 1
    assert notes[0]["attachments"][0]["path"] == result.still_relpath
    assert notes[0]["attachments"][0]["exists"] is True


def test_a_path_the_captain_typed_is_read_off_a_real_marker(
        scratch, tmp_path):
    """The channel that worked before the button, unchanged."""
    _, _, timeline = scratch
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 40)
    assert timeline.AddMarker(
        10, "Blue", "REF", f"make it look like {reference}", 1, "") is True

    note = [n for n in read_notes(timeline, str(tmp_path))
            if n.name == "REF"][0]
    assert [(a["origin"], a["exists"]) for a in note.attachments] == \
        [("note_text", True)]
    assert note.attachments[0]["resolved_path"] == str(reference)


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
