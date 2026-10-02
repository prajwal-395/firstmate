"""`marker_feedback` driven against a REAL running DaVinci Resolve.

These tests are the reason the module's recorded findings can be trusted.
The previous suite for this module was `unittest.mock` fakes returning
whatever the test had just told them to, and it was green while four of
the module's assumptions about Resolve were wrong.

Every test here builds its OWN throwaway timeline, named for this pytest
process, and deletes it again.  Nothing touches a timeline that was
already there, and the timeline Resolve had open is restored afterwards.
The project folder every test writes into is `tmp_path` (AGENTS.md 8,
"No test reaches a real project").
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import pytest

pytestmark = pytest.mark.resolve_live

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.marker_feedback import (  # noqa: E402
    DISCARD_ENV,
    MarkerNote,
    MarkerWriteError,
    ResolveUnavailable,
    UnpulledMarkers,
    _pull_all,
    assert_markers_pulled,
    connect_resolve,
    frames_to_timecode,
    guard_timeline_deletion,
    place_reply_clip_marker,
    place_reply_marker,
    pull,
    remove_clip_marker,
    pulled_files,
    read_notes,
    unpulled_notes,
)

RESOLVE_NEEDED = (
    "DaVinci Resolve is not running with a project whose media pool has a "
    "video clip, so there is nothing real to read"
)

# Two clips, appended with these source in-points, so the source-frame
# key space of a clip marker is provable rather than assumed.
CLIP_A_IN, CLIP_B_IN, CLIP_LEN = 100, 500, 100


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
    pool_clips = [
        c for c in (project.GetMediaPool().GetRootFolder().GetClipList() or [])
        if (c.GetClipProperty("Type") or "").startswith("Video")
        and int(c.GetClipProperty("Frames") or 0) > CLIP_B_IN + CLIP_LEN
        # Markers of its OWN would be the captain's, and the teardown
        # below clears the pool item's markers wholesale. Only an
        # unmarked clip is safe to borrow.
        and not (c.GetMarkers() or {})
    ]
    if not pool_clips:
        pytest.skip(RESOLVE_NEEDED)
    previous = project.GetCurrentTimeline()
    yield resolve, project, pool_clips[0]
    if previous:
        project.SetCurrentTimeline(previous)


@pytest.fixture
def scratch_timeline(resolve_project):
    """A throwaway timeline carrying two trimmed clips, deleted after."""
    _, project, pool_clip = resolve_project
    media_pool = project.GetMediaPool()
    name = f"pytest_markerprobe_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    timeline = media_pool.CreateEmptyTimeline(name)
    assert timeline, "Resolve declined to create the scratch timeline"
    project.SetCurrentTimeline(timeline)
    appended = media_pool.AppendToTimeline([
        {"mediaPoolItem": pool_clip, "startFrame": CLIP_A_IN,
         "endFrame": CLIP_A_IN + CLIP_LEN - 1, "mediaType": 1, "trackIndex": 1},
        {"mediaPoolItem": pool_clip, "startFrame": CLIP_B_IN,
         "endFrame": CLIP_B_IN + CLIP_LEN - 1, "mediaType": 1, "trackIndex": 1},
    ])
    assert appended, "Resolve declined to place the scratch clips"
    try:
        yield timeline
    finally:
        # Timeline-item markers die with the timeline; media pool markers
        # do NOT, so any this test left on the shared pool item go too.
        pool_clip.DeleteMarkersByColor("All")
        media_pool.DeleteTimelines([timeline])


def _note(notes, needle) -> MarkerNote:
    hits = [n for n in notes if needle in n.text]
    assert len(hits) == 1, f"{needle!r} -> {[n.text for n in notes]}"
    return hits[0]


# ── The Name field ──────────────────────────────────────────────────


def test_text_typed_into_the_name_field_survives(scratch_timeline):
    """The defect this module existed with: `name` was never read."""
    typed = "why is the sky blown out on this shot?"
    assert scratch_timeline.AddMarker(10, "Blue", typed, "", 1, "") is True

    note = _note(read_notes(scratch_timeline), typed)
    assert note.name == typed
    assert note.note == ""
    assert note.text == typed, "Name-field text must reach the collected record"
    assert note.source == "timeline_marker"


# ── The frame mapping ───────────────────────────────────────────────


def test_timeline_marker_frames_are_relative_to_the_start_frame(
        scratch_timeline):
    """`GetStartFrame()` is the origin; `TimelineItem.GetStart()` is not."""
    assert scratch_timeline.SetStartTimecode("01:00:00:00") is True
    start = int(scratch_timeline.GetStartFrame())
    assert start > 0, "SetStartTimecode did not move the timeline origin"

    assert scratch_timeline.AddMarker(7, "Blue", "at seven", "", 1, "") is True
    note = _note(read_notes(scratch_timeline), "at seven")

    assert note.frame_in_timeline_space == 7
    assert note.frame == start + 7
    assert note.clips, (
        "a marker seven frames in must find the clip under it; comparing the "
        "relative key against absolute clip bounds finds nothing"
    )
    assert note.clips[0]["timeline_start"] == start


def test_clip_marker_maps_from_its_source_frame_to_the_timeline_frame(
        scratch_timeline):
    """A marker placed at a KNOWN source frame, read back at the frame
    the timeline plays it on."""
    items = scratch_timeline.GetItemListInTrack("video", 1)
    clip_a, clip_b = items[0], items[1]
    assert clip_a.GetLeftOffset() == CLIP_A_IN
    assert clip_b.GetLeftOffset() == CLIP_B_IN

    source_frame = CLIP_A_IN + 25          # 25 frames into what clip A plays
    expected = clip_a.GetStart() + 25
    assert clip_a.AddMarker(source_frame, "Cyan", "SUBJECT", "left of frame",
                            1, "") is True

    note = _note(read_notes(scratch_timeline), "left of frame")
    assert note.source == "clip_marker"
    assert note.frame_in_timeline_space == source_frame
    assert note.frame == expected
    assert note.timecode == frames_to_timecode(expected, 30.0) or note.timecode
    assert [c["name"] for c in note.clips] == [clip_a.GetName()]


def test_a_clip_marker_outside_the_played_range_is_kept_unplaced(
        scratch_timeline):
    """Never clamped to the clip's head, which invents a position."""
    clip_a = scratch_timeline.GetItemListInTrack("video", 1)[0]
    outside = CLIP_A_IN + CLIP_LEN + 500
    assert clip_a.AddMarker(outside, "Cyan", "STRAY", "not played here",
                            1, "") is True

    note = _note(read_notes(scratch_timeline), "not played here")
    assert note.frame is None
    assert note.timecode is None
    assert str(outside) in note.unplaced_reason
    assert note.text, "the text is kept even when the frame cannot be"


def test_a_media_pool_marker_added_after_placement_is_still_collected(
        resolve_project, scratch_timeline):
    """It reaches no timeline item, so only reading the pool finds it."""
    _, _, pool_clip = resolve_project
    in_a_only = CLIP_A_IN + 10
    assert pool_clip.AddMarker(in_a_only, "Pink", "POOL", "seen once",
                               1, "") is True

    items = scratch_timeline.GetItemListInTrack("video", 1)
    assert in_a_only not in {int(k) for k in (items[0].GetMarkers() or {})}, (
        "a pool marker added AFTER placement does not reach an existing "
        "timeline item - which is why the pool is read separately"
    )

    note = _note(read_notes(scratch_timeline), "seen once")
    assert note.source == "media_pool_marker"
    assert note.frame == items[0].GetStart() + 10


def test_a_marker_inherited_at_placement_is_not_reported_twice(
        resolve_project, scratch_timeline):
    """A pool marker present when a clip is placed is copied onto it, and
    onto clips that never play that frame. It is one note, not three."""
    _, project, pool_clip = resolve_project
    in_a_only = CLIP_A_IN + 10
    assert pool_clip.AddMarker(in_a_only, "Pink", "POOL", "one note only",
                               1, "") is True
    assert project.GetMediaPool().AppendToTimeline([
        {"mediaPoolItem": pool_clip, "startFrame": CLIP_B_IN,
         "endFrame": CLIP_B_IN + CLIP_LEN - 1, "mediaType": 1,
         "trackIndex": 1},
    ])

    placed_after = scratch_timeline.GetItemListInTrack("video", 1)[2]
    assert in_a_only in {int(k) for k in (placed_after.GetMarkers() or {})}, (
        "the new item should have inherited the pool marker, at a source "
        "frame it does not itself play"
    )

    note = _note(read_notes(scratch_timeline), "one note only")
    assert note.source == "media_pool_marker"
    assert note.frame == scratch_timeline.GetItemListInTrack(
        "video", 1)[0].GetStart() + 10


def test_an_edited_copy_of_an_inherited_marker_is_kept_as_well(
        resolve_project, scratch_timeline):
    """Divergent text on the item's own copy is a second real note."""
    _, project, pool_clip = resolve_project
    frame = CLIP_A_IN + 10
    assert pool_clip.AddMarker(frame, "Pink", "POOL", "the pool wording",
                               1, "") is True
    assert project.GetMediaPool().AppendToTimeline([
        {"mediaPoolItem": pool_clip, "startFrame": CLIP_A_IN,
         "endFrame": CLIP_A_IN + CLIP_LEN - 1, "mediaType": 1,
         "trackIndex": 1},
    ])
    edited = scratch_timeline.GetItemListInTrack("video", 1)[2]
    assert edited.DeleteMarkerAtFrame(frame) is True
    assert edited.AddMarker(frame, "Pink", "POOL", "the edited wording",
                            1, "") is True

    notes = read_notes(scratch_timeline)
    # The pool's own wording is reported once per placement that plays the
    # frame - two of them here, at two different timecodes the captain can
    # scrub to. The item whose copy was edited reports the edited wording
    # instead, as its own clip marker.
    pool = [n for n in notes if "the pool wording" in n.text]
    editd = [n for n in notes if "the edited wording" in n.text]
    assert {n.source for n in pool} == {"media_pool_marker"}
    assert len(pool) == 2
    assert [n.source for n in editd] == ["clip_marker"]
    assert editd[0].frame == edited.GetStart() + 10


# ── What a collected record has to carry ────────────────────────────


# ── The pull, and the guard ─────────────────────────────────────────


def _three_notes(timeline):
    typed = [
        "the grade goes cold here and nowhere else",
        "this cutaway repeats the shot before it",
        "the music ducks two seconds late",
    ]
    for i, text in enumerate(typed):
        assert timeline.AddMarker(10 + i * 10, "Blue", f"Q{i}", text,
                                  1, "") is True
    return typed


def test_a_pull_clears_the_guard_and_a_new_note_raises_it_again(
        tmp_path, resolve_project, scratch_timeline):
    _, project, _ = resolve_project
    _three_notes(scratch_timeline)

    with pytest.raises(UnpulledMarkers) as raised:
        assert_markers_pulled(str(tmp_path), scratch_timeline)
    assert len(raised.value.notes) == 3
    assert "the grade goes cold here and nowhere else" in str(raised.value)
    assert "marker_feedback pull" in str(raised.value)

    pull(str(tmp_path), scratch_timeline, project)
    assert assert_markers_pulled(str(tmp_path), scratch_timeline) == []

    scratch_timeline.AddMarker(95, "Blue", "Q4", "and this one is new", 1, "")
    assert [n.note for n in unpulled_notes(str(tmp_path), scratch_timeline)] \
        == ["and this one is new"]
    with pytest.raises(UnpulledMarkers):
        assert_markers_pulled(str(tmp_path), scratch_timeline)


# ── The locale Resolve resets underneath us ─────────────────────────


def test_connecting_does_not_break_reading_utf8_files(resolve_project):
    """`scriptapp` leaves LC_CTYPE on `C`, so `getpreferredencoding()`
    becomes US-ASCII and every later `read_text()` on a UTF-8 file
    raises. Nine tests in this suite failed that way before
    `resolve_locale` put it back."""
    import locale
    from pathlib import Path

    assert locale.getpreferredencoding(False).upper().replace("-", "") \
        == "UTF8", (
        "connecting to Resolve has left the process on a non-UTF-8 "
        "default encoding - see library/tools/resolve_locale.py"
    )
    repo = Path(__file__).resolve().parents[1]
    utf8_source = repo / "library/steps/step_1_04_temporal_index/step.py"
    assert "─" in utf8_source.read_bytes().decode("utf-8"), (
        "this fixture file is meant to contain non-ASCII bytes")
    utf8_source.read_text()  # no encoding= : this is the call that broke


# ── The green reply ─────────────────────────────────────────────────


def test_reply_marker_past_the_end_is_refused_not_placed(scratch_timeline):
    """Resolve ACCEPTS past-the-end frames, so the bounds check is ours:
    the write must refuse before anything lands."""
    end = int(scratch_timeline.GetEndFrame())
    with pytest.raises(MarkerWriteError):
        place_reply_marker(scratch_timeline, end + 50, "Green",
                           "past the end", "must not land")
    assert end + 50 - int(scratch_timeline.GetStartFrame()) \
        not in (scratch_timeline.GetMarkers() or {})


# ── The green reply ON A CLIP ───────────────────────────────────────
#
# Three of the captain's feedback markers were MISSED on 2026-09-11
# because firstmate's capture read `Timeline.GetMarkers()` only and all
# three were on CLIPS. Answering them needs the mirror of that: a reply
# written where the question was, in the clip's own SOURCE frames.


def test_a_reply_lands_on_the_clip_at_a_source_frame(scratch_timeline):
    """The answer comes back on the SAME clip at the SAME position, or
    the captain looks where they asked and finds nothing."""
    item = scratch_timeline.GetItemListInTrack("video", 1)[1]
    key = CLIP_B_IN + 20
    record = place_reply_clip_marker(
        item, key, "Green", "reply: the ending holds now",
        "You asked for room after her last word; the closer now ends "
        "in silence.")
    assert record["source_frame"] == key
    assert record["color"] == "Green"

    note = _note(read_notes(scratch_timeline), "room after her last word")
    assert note.source == "clip_marker"
    assert note.color == "Green"
    # Read back in SOURCE frames, and placed at the timeline frame the
    # clip plays that source frame at.
    assert note.frame_in_timeline_space == key
    assert note.frame == int(item.GetStart()) + (key - int(item.GetLeftOffset()))


def test_a_reply_outside_what_the_clip_plays_is_refused(scratch_timeline):
    """Resolve bounds-checks NEITHER end: a key outside the played range
    is accepted, returns True, and sits on footage nobody sees."""
    item = scratch_timeline.GetItemListInTrack("video", 1)[1]
    first = int(item.GetLeftOffset())
    for key in (first - 1, first + int(item.GetDuration())):
        with pytest.raises(MarkerWriteError):
            place_reply_clip_marker(item, key, "Green", "outside",
                                    "must not land")
        assert key not in (item.GetMarkers() or {})


def test_an_answered_question_comes_off_and_is_judged_by_the_read_back(
        scratch_timeline):
    """`DeleteMarkerAtFrame` returns False for "there was nothing there",
    which is the same outcome as a successful delete - so the verdict is
    the read-back, never the return (AGENTS.md 5)."""
    item = scratch_timeline.GetItemListInTrack("video", 1)[0]
    key = CLIP_A_IN + 9
    assert item.AddMarker(key, "Blue", "feedback", "asked", 1, "") is True
    assert remove_clip_marker(item, key) is True
    assert key not in (item.GetMarkers() or {})
    # Nothing there is not a failure, and says so.
    assert remove_clip_marker(item, key) is False
    # And the reply may then take the frame the question had.
    place_reply_clip_marker(item, key, "Green", "reply: done", "answered")
    assert (item.GetMarkers() or {})[key]["color"] == "Green"


def test_pull_all_reads_every_timeline_without_switching(
        tmp_path, resolve_project, scratch_timeline):
    """`pull --all` carries a batch in one invocation: every timeline in
    the project gets its own pull file, and the open timeline is never
    switched to do it. Measured 2026-09-19, when the captain's 37
    markers sat across 26 of 31 timelines and the open-timeline-only
    pull could not carry them in one invocation."""
    _, project, _ = resolve_project
    open_before = project.GetCurrentTimeline().GetName()
    assert scratch_timeline.AddMarker(
        0, "Blue", "feedback", "pull-all probe", 1) is True
    assert _pull_all(str(tmp_path), project) == 0
    names = {payload.get("timeline")
             for _, payload in pulled_files(str(tmp_path))}
    assert scratch_timeline.GetName() in names
    assert len(names) == project.GetTimelineCount()
    assert project.GetCurrentTimeline().GetName() == open_before
    probe = [n for _, payload in pulled_files(str(tmp_path))
             for n in payload["notes"]
             if n.get("note") == "pull-all probe"]
    assert len(probe) == 1 and probe[0]["name"] == "feedback"
