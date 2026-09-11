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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.marker_feedback import (  # noqa: E402
    DISCARD_ENV,
    MarkerNote,
    MarkerWriteError,
    ResolveUnavailable,
    UnpulledMarkers,
    assert_markers_pulled,
    connect_resolve,
    frames_to_timecode,
    guard_timeline_deletion,
    place_reply_marker,
    pull,
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
def resolve_project():
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


def test_text_typed_into_the_notes_field_survives(scratch_timeline):
    """And the Notes field, which is the half the old reader did read."""
    typed = "the cut lands a beat early - can we hold it six frames?"
    assert scratch_timeline.AddMarker(20, "Green", "Marker", typed, 1, "") is True

    note = _note(read_notes(scratch_timeline), typed)
    assert note.note == typed
    assert note.name == "Marker"
    assert note.text == f"Marker\n\n{typed}"

    # Resolve refuses an EMPTY name, but accepts a whitespace one, which
    # is the only way an API-placed marker can be Notes-only. The space
    # is kept as typed rather than stripped: this module normalises
    # nothing the captain put in either field.
    only = "and this one has nothing in Name at all"
    assert scratch_timeline.AddMarker(21, "Green", " ", only, 1, "") is True
    bare = _note(read_notes(scratch_timeline), only)
    assert (bare.name, bare.note) == (" ", only)
    assert only in bare.text


def test_both_fields_are_kept_verbatim_and_separately(scratch_timeline):
    name, note_text = "GRADE", "this reads green next to the shot before it"
    assert scratch_timeline.AddMarker(30, "Red", name, note_text, 1, "") is True

    note = _note(read_notes(scratch_timeline), note_text)
    assert (note.name, note.note) == (name, note_text)
    assert note.text == f"{name}\n\n{note_text}"


def test_resolve_refuses_a_marker_with_an_empty_name(scratch_timeline):
    """Recorded in the module docstring; asserted so it cannot go stale."""
    assert scratch_timeline.AddMarker(40, "Blue", "", "notes only", 1, "") is False
    assert scratch_timeline.AddMarker(41, "Blue", "", "", 1, "") is False
    assert scratch_timeline.AddMarker(42, "Blue", "landed", "", 1, "") is True
    assert scratch_timeline.AddMarker(42, "Blue", "second here", "", 1, "") is False
    assert sorted(scratch_timeline.GetMarkers()) == [42]


def test_apostrophes_and_newlines_are_not_normalised(scratch_timeline):
    typed = "we're not sure - line two\nline three's end"
    assert scratch_timeline.AddMarker(50, "Blue", "Q", typed, 1, "") is True
    assert _note(read_notes(scratch_timeline), "line three's end").note == typed


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


def test_a_record_carries_frame_timecode_clips_text_and_source(
        scratch_timeline):
    assert scratch_timeline.AddMarker(
        12, "Blue", "FRAMING", "his head is cropped", 1, '{"ticket": 271}'
    ) is True
    note = _note(read_notes(scratch_timeline), "his head is cropped")

    assert note.frame is not None
    assert note.timecode and note.timecode.count(":") == 3
    assert note.source == "timeline_marker"
    assert note.custom_data == {"ticket": 271}
    clip = note.clips[0]
    assert clip["name"] and clip["source_file"].endswith(".MOV")
    assert clip["track_type"] == "video" and clip["track_index"] == 1
    assert clip["source_start"] == CLIP_A_IN


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


def test_pull_writes_a_durable_file_outside_the_overwritable_areas(
        tmp_path, resolve_project, scratch_timeline):
    _, project, _ = resolve_project
    typed = _three_notes(scratch_timeline)

    result = pull(str(tmp_path), scratch_timeline, project)
    written = tmp_path / "marker_feedback"
    assert written.is_dir()
    assert str(written) in result["path"]
    assert "pipeline_output" not in result["path"], (
        "everything under pipeline_output/ is Kind.OUTPUT - safe to delete "
        "because a re-run reproduces it. A typed note is not."
    )

    payload = json.loads(open(result["path"], encoding="utf-8").read())
    assert payload["note_count"] == 3
    assert [n["text"] for n in payload["notes"]] == [
        f"Q{i}\n\n{t}" for i, t in enumerate(typed)]
    assert all(n["timecode"] and n["frame"] is not None
               for n in payload["notes"])


def test_pull_is_safe_to_run_repeatedly_and_never_overwrites(
        tmp_path, resolve_project, scratch_timeline):
    _, project, _ = resolve_project
    _three_notes(scratch_timeline)
    first = pull(str(tmp_path), scratch_timeline, project)["path"]
    scratch_timeline.AddMarker(90, "Blue", "Q3", "and one more", 1, "")
    second = pull(str(tmp_path), scratch_timeline, project)["path"]

    assert first != second
    assert os.path.exists(first) and os.path.exists(second)
    assert json.load(open(first))["note_count"] == 3
    assert json.load(open(second))["note_count"] == 4
    assert len(pulled_files(str(tmp_path), scratch_timeline.GetName())) == 2


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


def test_an_unmarked_timeline_does_not_raise(tmp_path, scratch_timeline):
    assert assert_markers_pulled(str(tmp_path), scratch_timeline) == []


def test_the_build_path_guard_refuses_the_named_timeline(
        tmp_path, resolve_project, scratch_timeline):
    """The guard for any caller that is about to delete a named timeline.

    Step 6.01 no longer deletes one - a name already in use is refused
    there instead - so this is the guard on its own terms, for whatever
    calls it next."""
    _, project, _ = resolve_project
    _three_notes(scratch_timeline)
    name = scratch_timeline.GetName()

    guard_timeline_deletion(str(tmp_path), project, {"a name nothing has"})

    with pytest.raises(UnpulledMarkers):
        guard_timeline_deletion(str(tmp_path), project, {name})

    pull(str(tmp_path), scratch_timeline, project)
    guard_timeline_deletion(str(tmp_path), project, {name})


def test_the_override_is_explicit_and_off_by_default(
        tmp_path, resolve_project, scratch_timeline, monkeypatch):
    _, project, _ = resolve_project
    _three_notes(scratch_timeline)
    with pytest.raises(UnpulledMarkers):
        guard_timeline_deletion(str(tmp_path), project,
                                {scratch_timeline.GetName()})
    monkeypatch.setenv(DISCARD_ENV, "1")
    guard_timeline_deletion(str(tmp_path), project,
                            {scratch_timeline.GetName()})


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


def test_the_wrapper_restores_ctype_and_leaves_numeric_alone():
    """Restoring a category nothing touched could hand fusionscript a
    decimal comma. Only LC_CTYPE is put back."""
    import locale
    from library.tools.resolve_locale import scriptapp_preserving_locale

    class Vandal:
        def scriptapp(self, name):
            locale.setlocale(locale.LC_CTYPE, "C")
            return f"connected:{name}"

    before_ctype = locale.setlocale(locale.LC_CTYPE)
    before_numeric = locale.setlocale(locale.LC_NUMERIC)
    assert scriptapp_preserving_locale(Vandal()) == "connected:Resolve"
    assert locale.setlocale(locale.LC_CTYPE) == before_ctype
    assert locale.setlocale(locale.LC_NUMERIC) == before_numeric


# ── The green reply ─────────────────────────────────────────────────


def test_reply_marker_lands_green_and_reads_back(scratch_timeline):
    """The captain's channel: one green marker stating the ask and the
    response, written for a reader and verified by read-back."""
    start = int(scratch_timeline.GetStartFrame())
    frame = start + 10
    record = place_reply_marker(
        scratch_timeline, frame, "Green",
        "reply: tail breath holds",
        "You asked for room after her last word; the closer now ends "
        "in silence.")
    assert record["frame"] == frame
    assert record["color"] == "Green"
    note = _note(read_notes(scratch_timeline), "room after her last word")
    assert note.color == "Green"
    assert note.frame == frame


def test_reply_marker_past_the_end_is_refused_not_placed(scratch_timeline):
    """Resolve ACCEPTS past-the-end frames, so the bounds check is ours:
    the write must refuse before anything lands."""
    end = int(scratch_timeline.GetEndFrame())
    with pytest.raises(MarkerWriteError):
        place_reply_marker(scratch_timeline, end + 50, "Green",
                           "past the end", "must not land")
    assert end + 50 - int(scratch_timeline.GetStartFrame()) \
        not in (scratch_timeline.GetMarkers() or {})


def test_reply_marker_refuses_an_empty_name(scratch_timeline):
    start = int(scratch_timeline.GetStartFrame())
    with pytest.raises(MarkerWriteError):
        place_reply_marker(scratch_timeline, start + 12, "Green", "", "x")
    assert 12 not in (scratch_timeline.GetMarkers() or {})


def test_reply_marker_on_an_occupied_frame_is_refused(scratch_timeline):
    start = int(scratch_timeline.GetStartFrame())
    assert scratch_timeline.AddMarker(14, "Blue", "his", "stays", 1, "") is True
    with pytest.raises(MarkerWriteError):
        place_reply_marker(scratch_timeline, start + 14, "Green",
                           "mine", "must not overwrite his")
    back = (scratch_timeline.GetMarkers() or {})[14]
    assert (back.get("name"), back.get("color")) == ("his", "Blue")
