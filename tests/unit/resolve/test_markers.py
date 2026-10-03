"""Markers on the master: located by measurement, additive, reversible.

The captain's ruling of 2026-09-07 is "Let the pipeline add markers
only", so the two things this file has to pin are that the location is a
MEASUREMENT rather than a name match, and that a marker this writes can
be taken off again without touching one the captain typed.
"""
from __future__ import annotations
from pathlib import Path
import pytest
from library.tools import marker_payload
from library.tools.master_markers import (
    STATE_MARKER_COLOURS,
    MarkerRefused,
    Region,
    SourceWindow,
    assert_no_collision,
    coverage_regions,
    is_ours,
    locate_on_master,
    marker_for,
)
from library.tools.resolve_organization import CURRENT, EARLIER
import json
import os
import sys
from dataclasses import asdict
import types


CAM = "/footage/LC4930.MXF"
OTHER = "/footage/LC4931.MXF"


def master_at(tl_start, src_in, src_out, path=CAM):
    return SourceWindow(path, src_in, src_out, tl_start)


# ------------------------------------------------------ locating a reel


def test_a_reel_is_located_by_source_overlap_not_by_name():
    """The reel's own timeline name never enters this: the master item
    carries the offset and the overlap is a fact about the footage."""
    master = [master_at(594, 3151, 3637)]
    reel = [SourceWindow(CAM, 3151, 3630, 0)]
    assert locate_on_master(reel, master) == [(594, 1073)]
    # Same file, a part the master never played: nowhere.
    assert locate_on_master([SourceWindow(CAM, 9000, 9100, 0)], master) == []
    # A partial overlap is clipped to what the master plays.
    master = [master_at(100, 1000, 1100)]
    assert locate_on_master([SourceWindow(CAM, 1050, 1200, 0)], master) == [
        (150, 200)]


def test_reels_sharing_footage_become_ONE_region_naming_both():
    """21 of the field test's reels end on the same call-to-action and
    map to one master frame. Resolve keeps one marker per frame, so a
    per-reel marker cannot express this and a per-region one can."""
    regions = coverage_regions([
        ("Reel A", CURRENT, [(100, 200)]),
        ("Reel B", EARLIER, [(150, 250)]),
        ("Reel C", CURRENT, [(900, 1000)]),
    ])
    assert [(r.start, r.end) for r in regions] == [(100, 250), (900, 1000)]
    assert regions[0].reels == (("Reel A", CURRENT), ("Reel B", EARLIER))
    assert regions[1].reels == (("Reel C", CURRENT),)


# ------------------------------------------------------------- markers


def a_marker(state=CURRENT, start=100, end=200):
    return marker_for(Region(start, end, (("Reel 01", state),)), 63694)


def test_a_marker_carries_the_state_colour_and_names_its_reels():
    marker = marker_for(
        Region(100, 250, (("Reel A", CURRENT), ("Reel B", EARLIER))), 63694)
    assert marker.frame == 100 and marker.duration == 150
    assert marker.colour == STATE_MARKER_COLOURS[CURRENT]
    assert "Reel A" in marker.note and "Reel B" in marker.note
    assert EARLIER in marker.note, "a colour must not hide the other state"
    assert marker.name == "2 reels use this"
    # A region past the end of the master refuses.
    with pytest.raises(MarkerRefused) as refused:
        marker_for(Region(63000, 70000, (("Reel", CURRENT),)), 63694)
    assert "outside the master" in str(refused.value)


def test_a_marker_the_captain_typed_is_never_ours():
    """The whole basis of removal being exact."""
    assert is_ours("") is False
    assert is_ours("check this bit") is False
    assert is_ours("{not json") is False
    foreign = marker_payload.merge_record(marker_payload.new_envelope(), {
        "kind": "still", "writer": "capture_frame", "writer_version": 1,
        "id": "still_x", "at": "2026-09-07T00:00:00+00:00"})
    assert is_ours(marker_payload.dumps(foreign)) is False


def test_writing_onto_the_captains_marker_refuses_and_onto_ours_replaces():
    existing = {100: {"customData": "captain's own note"}}
    with pytest.raises(MarkerRefused) as refused:
        assert_no_collision([a_marker(start=100, end=200)], existing)
    assert "already has a marker" in str(refused.value)
    # A second run must REPLACE its own markers, or it can never re-run.
    existing = {100: {"customData": a_marker().custom_data}}
    assert assert_no_collision([a_marker(start=100, end=200)], existing) is None


FORBIDDEN = (
    "MoveClips(", "SetClipColor(", "ClearClipColor(", "SetMetadata(",
    "SetName(", "DeleteTimelines(", "DeleteClips(", "SetSetting(",
    "AppendToTimeline(", "DeleteClipsInTimeline(", "SetStartFrame(",
    "AddTrack(", "DeleteTrack(", "SetTrackName(", "Render(",
    "AddRenderJob(", "ImportFusionComp(", "SetCurrentTimecode(",
)


def test_nothing_here_can_change_the_master_except_its_markers():
    """The captain authorised markers and nothing else. Asserted of the
    SOURCE, because a reviewer cannot see a call that is not there."""
    for module in ("library/tools/master_markers.py",
                   "library/tools/execution/mark_master.py"):
        source = Path(module).read_text(encoding="utf-8")
        executable = "".join(source.split('"""')[::2])
        for call in FORBIDDEN:
            assert call not in executable, (
                f"{module} calls {call} - the master is read-only except "
                f"for markers (the captain's ruling of 2026-09-07)")
    # The executor adds and removes by frame, never by colour - deleting
    # by colour would take the captain's own green markers.
    assert "AddMarker(" in executable
    assert "DeleteMarkerAtFrame(" in executable
    assert "DeleteMarkersByColor(" not in executable


# --------------------------------------------------------------------------
# From test_marker_feedback_records.py
#
# The disk half of `marker_feedback` - no Resolve, and no fake of one.
#
# Everything here runs against real files in `tmp_path`. The Resolve half
# is proved against a real running Resolve in
# `tests/qualification/test_marker_feedback_against_resolve.py`; nothing in this file
# stands in for it, because a fake that returns what the test just told it
# is what left the module's four wrong assumptions green for months.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.marker_feedback import (  # noqa: E402
    PULL_FILE_SUFFIX,
    MarkerNote,
    frames_to_timecode,
    note_identity,
    pulled_files,
    read_attachments,
)
from library.tools.project_layout import (  # noqa: E402
    AREAS, Area, Kind, ProjectLayout, WRITABLE_KINDS,
)


def _note(**kw) -> MarkerNote:
    base = dict(source="timeline_marker", name="Q", note="n", text="Q\n\nn",
                frame=10, timecode="00:00:00:10", frame_in_timeline_space=10)
    base.update(kw)
    return MarkerNote(**base)


# ── Timecode ────────────────────────────────────────────────────────


def test_timecode_is_absent_rather_than_guessed():
    """A timecode nobody can scrub to is worse than an admitted absence."""
    assert frames_to_timecode(None, 30.0) is None
    assert frames_to_timecode(10, 0.0) is None


# ── Identity, which is what the guard compares ──────────────────────


def test_identity_is_the_text_and_where_it_was_typed():
    a = _note()
    assert note_identity(a) == note_identity(_note(frame=999, timecode="x"))
    assert note_identity(a) != note_identity(_note(note="something else"))
    assert note_identity(a) != note_identity(_note(name="different"))
    assert note_identity(a) != note_identity(_note(source="clip_marker"))
    # A note that has gained a still is worth collecting again.
    assert note_identity(_note()) != note_identity(
        _note(attachments=[_attachment("marker_feedback/stills/a.png")]))
    # The captain chose typed notes over colour codes: recolouring a
    # marker must not make it look like a new note.
    assert note_identity(_note(color="Blue")) == note_identity(
        _note(color="Red"))


# ── Where the record lives ──────────────────────────────────────────


def test_the_area_is_outside_pipeline_output_and_writable(tmp_path):
    spec = AREAS[Area.MARKER_FEEDBACK]
    assert not spec.relpath.startswith("pipeline_output"), (
        "everything under pipeline_output/ is Kind.OUTPUT - reproducible by "
        "a re-run. A typed note is destroyed by one."
    )
    assert spec.kind is Kind.CAPTURED
    assert spec.kind in WRITABLE_KINDS
    layout = ProjectLayout(tmp_path)
    captured = layout.write_dir(Area.MARKER_FEEDBACK)
    assert layout.read_dir(Area.OUTPUT_ROOT) not in captured.parents
    assert captured.parent == layout.root


# ── Reading pull files back ─────────────────────────────────────────


def _write_pull(layout, name, timeline, notes):
    path = layout.write_path(Area.MARKER_FEEDBACK, name + PULL_FILE_SUFFIX)
    path.write_text(json.dumps({
        "format": "marker_feedback/1", "timeline": timeline,
        "note_count": len(notes), "notes": notes,
    }), encoding="utf-8")
    return path


def test_pull_files_are_listed_oldest_first_and_filtered_by_timeline(tmp_path):
    layout = ProjectLayout(tmp_path)
    _write_pull(layout, "Edit.20260828T090000Z", "Edit", [{"note": "a"}])
    _write_pull(layout, "Edit.20260828T100000Z", "Edit", [{"note": "b"}])
    _write_pull(layout, "Other.20260828T110000Z", "Other", [{"note": "c"}])

    every = pulled_files(str(tmp_path))
    assert [p.name for p, _ in every] == [
        "Edit.20260828T090000Z.markers.json",
        "Edit.20260828T100000Z.markers.json",
        "Other.20260828T110000Z.markers.json",
    ]
    assert [payload["timeline"] for _, payload in
            pulled_files(str(tmp_path), "Edit")] == ["Edit", "Edit"]
    # An unreadable pull file is skipped, not fatal.
    layout.write_path(
        Area.MARKER_FEEDBACK, "Edit.broken" + PULL_FILE_SUFFIX
    ).write_text("{not json", encoding="utf-8")
    assert len(pulled_files(str(tmp_path), "Edit")) == 2


# ── Attachments ─────────────────────────────────────────────────────
#
# The Resolve half - a still really landing on a real marker - is proved
# in `tests/qualification/test_marker_capture_against_resolve.py`. What is checked here
# is the reader's own arithmetic against real files on disk.


def _still_envelope(relpath, **extra):
    env = marker_payload.new_envelope()
    marker_payload.merge_record(env, dict({
        "kind": marker_payload.KIND_STILL, "writer": "capture_frame",
        "writer_version": 1, "id": "still_x", "at": "t",
        "path": relpath,
    }, **extra))
    return env


def test_attachments_resolve_against_the_project_or_report_absence(tmp_path):
    relpath = "marker_feedback/stills/Edit.f000040.png"
    still = tmp_path / relpath
    still.parent.mkdir(parents=True)
    still.write_bytes(b"png")

    got = read_attachments("", "", _still_envelope(relpath), str(tmp_path))
    assert len(got) == 1
    assert got[0].kind == "still"
    assert got[0].origin == "custom_data"
    assert got[0].resolved_path == str(still)
    assert got[0].exists is True
    assert got[0].writer == "capture_frame"

    # A written attachment whose file is gone is reported absent.
    got = read_attachments(
        "", "", _still_envelope("marker_feedback/stills/gone.png"),
        str(tmp_path))
    assert got[0].exists is False
    assert got[0].path == "marker_feedback/stills/gone.png"

    # A path the captain typed - the channel that worked before the
    # capture button - is still read.
    typed = tmp_path / "ref.png"
    typed.write_bytes(b"png")
    got = read_attachments(
        "GRADE", f"compare against {typed}", {}, str(tmp_path))
    assert [(a.origin, a.kind, a.exists) for a in got] == \
        [("note_text", "typed_path", True)]
    assert got[0].resolved_path == str(typed)


# ── What an attachment does to identity ─────────────────────────────


def _attachment(path):
    return asdict(read_attachments("", "", _still_envelope(path), None)[0])


# ── What the reader prints ──────────────────────────────────────────


# ── A reply of ours says so, mechanically ─────────────────────────
#
# Measured 2026-09-11 on `lucie/geo-podcast`: every marker on every reel
# carried an EMPTY `customData`, our own green replies included. So
# nothing downstream could tell a question the captain typed from an
# answer we wrote back, and a reader that has to guess guesses on
# colour - which this module deliberately has no vocabulary for. The
# record below is the one thing that CAN be stated mechanically, and it
# also carries what the reply answers, so the question survives its own
# marker being deleted.

from library.tools import marker_feedback as _mf  # noqa: E402


def test_a_reply_record_names_its_writer_and_what_it_answers():
    record = _mf.reply_record(answers="Reel_13:9f2c4a1b7e5d03aa", answers_text="the words")
    assert record["kind"] == _mf.REPLY_RECORD_KIND
    assert record["writer"] == _mf.REPLY_WRITER
    assert record["answers"] == "Reel_13:9f2c4a1b7e5d03aa"
    assert record["answers_text"] == "the words"
    marker_payload.merge_record(marker_payload.new_envelope(), record)
    # A foreign writer's record of the same kind is not read as ours.
    envelope = marker_payload.new_envelope()
    marker_payload.merge_record(envelope, {
        "kind": _mf.REPLY_RECORD_KIND, "writer": "somebody_else",
        "writer_version": 1, "id": "x", "at": "2026-08-28T00:00:00Z"})
    assert _mf.reply_records_in(marker_payload.dumps(envelope)) == []


# ── `answers` is single-writer and well-formed ──────────────────────
#
# Measured on live reels: sometimes absent, sometimes prose ("R04 blue
# feedback"), sometimes a stale frame locator ("Reel 14 - ... @162",
# "...#clip_marker@22"). A field used all three ways invites the
# mechanical count that has now failed twice, so the single writer -
# `reply_record` - refuses everything but empty and the durable
# identity, and prose stays in `answers_text` where it is display only.


def test_answers_is_empty_or_a_durable_identity_and_nothing_else():
    """Reel 04's pink verdict (`answers="R04 blue feedback"`), Reel 14's
    green (`... @162`) and Reel 29's (`...#clip_marker@22`) are refused,
    and so is a malformed `answers_anchor`."""
    for bad in ("R04 blue feedback",
                "Reel 14 - why-ai-trusts-youtube@162",
                "Reel 29 - salvage#clip_marker@22"):
        with pytest.raises(ValueError, match="must be a durable note identity"):
            _mf.reply_record(answers=bad)
    good = "Reel_14:9f2c4a1b7e5d03aa"
    for bad in ({"source_file": "/f/a.mov"},
                {"source_frame": 3},
                {"source_file": "", "source_frame": 3},
                {"source_file": "/f/a.mov", "source_frame": -1},
                {"source_file": "/f/a.mov", "source_frame": True},
                {"source_file": "/f/a.mov", "source_frame": "3"},
                "not-a-mapping"):
        with pytest.raises(ValueError, match="answers_anchor"):
            _mf.reply_record(answers=good, answers_anchor=bad)


def test_the_legitimate_answers_shapes_are_recorded():
    """Empty records no key; the legitimate producer is
    `feedback_ledger.durable_identity`; an anchor is recorded verbatim
    (a copy) and survives the customData round trip."""
    from library.tools.feedback_ledger import durable_identity

    record = _mf.reply_record(answers_text="the words")
    assert "answers" not in record
    assert record["answers_text"] == "the words"

    identity = durable_identity("Reel 14", "feedback\n\nno value prop")
    record = _mf.reply_record(answers=identity, answers_text="x")
    assert record["answers"] == identity
    marker_payload.merge_record(marker_payload.new_envelope(), record)

    anchor = {"source_file": "/f/LC4932.MXF", "source_frame": 1101}
    record = _mf.reply_record(answers="Reel_14:9f2c4a1b7e5d03aa",
                              answers_anchor=anchor)
    assert record["answers_anchor"] == anchor
    assert record["answers_anchor"] is not anchor
    data = _mf.reply_custom_data(
        answers="Reel_14:9f2c4a1b7e5d03aa", answers_anchor=anchor)
    assert _mf.reply_records_in(data)[0]["answers_anchor"] == anchor


# --------------------------------------------------------------------------
# From test_marker_payload.py
#
# The `customData` envelope, and the parts of the capture button that
# need no Resolve.
#
# The Resolve half - GrabStill, ExportStills, AddMarker,
# UpdateMarkerCustomData - is proved against a real running Resolve in
# `tests/qualification/test_marker_capture_against_resolve.py`.  Nothing here stands in
# for it: a fake that answers what the test just told it is what left four
# of `marker_feedback`'s assumptions wrong and green for months.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools.marker_capture import (  # noqa: E402
    CaptureError,
    read_playhead,
    timecode_to_frames,
    unused_path,
)


# ── The envelope ────────────────────────────────────────────────────


def test_a_record_missing_a_required_key_raises():
    full = {"kind": "still", "writer": "w", "writer_version": 1,
            "id": "r", "at": "t"}
    for missing in full:
        record = {k: v for k, v in full.items() if k != missing}
        with pytest.raises(ValueError):
            marker_payload.merge_record(marker_payload.new_envelope(), record)


# ── Nothing is destroyed ────────────────────────────────────────────


def test_customdata_this_module_did_not_write_is_kept_not_overwritten():
    for raw, reason_fragment in (
        ("a path I typed by hand", "not JSON"),
        ('{"someone": "else"}', "not a vep.marker/1 envelope"),
        ('{"schema": "vep.marker/99", "records": []}', "does not understand"),
    ):
        env = marker_payload.parse(raw)
        assert marker_payload.is_envelope(env)
        assert reason_fragment in env["foreign_reason"]
        assert env["foreign"] in (
            raw, json.loads(raw) if raw.startswith("{") else raw)
        # And it survives a merge and a write - which is the whole point.
        marker_payload.merge_record(env, {
            "kind": "still", "writer": "w", "writer_version": 1,
            "id": "r", "at": "t", "path": "a.png"})
        assert "foreign" in marker_payload.parse(marker_payload.dumps(env)), raw


# ── A path the captain typed ────────────────────────────────────────


# Not under a home directory: a test source may not carry one
# (tests/tooling/test_tests_never_reach_real_projects.py), and these are shapes to
# match rather than files to open.
def test_typed_paths_are_matched_conservatively():
    rows = (
        ("/vol/refs/Desktop/ref.png", ["/vol/refs/Desktop/ref.png"]),
        ("look at file:///vol/refs/a.jpg please", ["/vol/refs/a.jpg"]),
        ("see /vol/refs/a.png and /vol/refs/b.png", ["/vol/refs/a.png",
                                                     "/vol/refs/b.png"]),
        ("twice /vol/refs/a.png /vol/refs/a.png", ["/vol/refs/a.png"]),
        ("the shot before this one is better", []),
        ("notes/relative.md is not absolute", []),
        ("/vol/refs/no_extension_here", []),
        ("ends a sentence /vol/refs/a.png.", ["/vol/refs/a.png"]),
    )
    got = {text: marker_payload.typed_paths(text) for text, _ in rows}
    assert got == dict(rows)


# ── Timecode ────────────────────────────────────────────────────────


def test_timecode_to_frames():
    """Non-drop at several rates; 29.97 drop-frame drops two frames a
    minute except every tenth; and a semicolon is what says drop-frame,
    not the rate - Resolve allows a 29.97 timeline running non-drop and
    reads back a `:`."""
    rows = (
        ("00:00:00:00", 30.0, 0),
        ("00:00:01:10", 30.0, 40),
        ("01:00:00:00", 30.0, 108000),
        ("00:01:00:00", 24.0, 1440),
        ("00:00:01:00", 23.976, 24),
        # 00:01:00;02 is the frame after 00:00:59;29.
        ("00:00:59;29", 29.97, 1799),
        ("00:01:00;02", 29.97, 1800),
        ("00:10:00;00", 29.97, 17982),
        ("00:01:00:00", 29.97, 1800),
    )
    got = [(tc, fps, timecode_to_frames(tc, fps)) for tc, fps, _ in rows]
    assert got == list(rows)


def test_an_unreadable_timecode_raises_rather_than_guessing():
    with pytest.raises(CaptureError):
        timecode_to_frames("", 30.0)
    with pytest.raises(CaptureError):
        timecode_to_frames("01:00:00", 30.0)


# ── The still's name ────────────────────────────────────────────────


def test_two_captures_inside_one_second_do_not_overwrite(tmp_path):
    """The stamp has one-second resolution, so the name alone is not
    enough - a second click on the same frame must not eat the first."""
    first = tmp_path / "t.f000040.20260828T231204Z.png"
    assert unused_path(first) == first
    first.write_bytes(b"one")
    second = unused_path(first)
    assert second != first and not second.exists()
    second.write_bytes(b"two")
    assert unused_path(first).name.endswith("-3.png")
    assert first.read_bytes() == b"one"


# ── The bounds on the playhead ──────────────────────────────────────
#
# What Resolve DOES - clamping silently, and grabbing a black still past
# the end - is measured in `marker_capture`'s docstring and asserted
# against a real Resolve in `test_marker_capture_against_resolve.py`.
# What is exercised here is this module's own arithmetic on the four
# numbers Resolve hands it, with those numbers named rather than mocked
# out of a call that was never made.


class _Bounds:
    """Four readings off a timeline, and nothing else."""

    def __init__(self, current, start_tc="00:00:00:00", start=0, end=179):
        self._current, self._start_tc = current, start_tc
        self._start, self._end = start, end

    def GetSetting(self, key):
        return "30.0" if key == "timelineFrameRate" else ""

    def GetCurrentTimecode(self):
        return self._current

    def GetStartTimecode(self):
        return self._start_tc

    def GetStartFrame(self):
        return self._start

    def GetEndFrame(self):
        return self._end

    def GetName(self):
        return "bounds"


def test_the_playhead_bound_is_the_last_playable_frame_from_any_origin():
    """The last playable frame is accepted, one past it is refused rather
    than grabbed, and `GetEndFrame` is absolute - the same space as
    `GetStartFrame` - so the bound holds against a moved origin."""
    assert read_playhead(_Bounds("00:00:05:28")).marker_key == 178  # of 0..178
    with pytest.raises(CaptureError, match="past the end"):
        read_playhead(_Bounds("00:00:08:00"))
    moved = dict(start_tc="01:00:00:00", start=108000, end=108179)
    assert read_playhead(_Bounds("01:00:05:28", **moved)).marker_key == 178
    with pytest.raises(CaptureError, match="past the end"):
        read_playhead(_Bounds("01:00:06:00", **moved))


# --------------------------------------------------------------------------
# From test_marker_resolution.py
#
# The other end of the captain's timeline notes: resolve, verify, clear.
#
# History: docs/evidence/resolve_test_history.md#test_marker_resolution.

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import marker_resolution as mr
from tests.resolve_double import FakeTimeline, make_project

# ── Notes ─────────────────────────────────────────────────────────


def _timeline_note(**kw):
    note = {
        "note_id": "reel-09:timeline_marker:1674",
        "source": "timeline_marker",
        "name": "Marker 2",
        "note": "the a-roll row needs to be 2 rows, one for akshita and one for craig",
        "text": "Marker 2\n\nthe a-roll row needs to be 2 rows, one for akshita and one for craig",
        "frame": 108000 + 1674,
        "frame_in_timeline_space": 1674,
        "timecode": "01:00:55:24",
        "timeline": "reel-09",
        "pull_file": "reel-09.json",
        "collected_at": "2026-09-09T00:00:00+00:00",
        "outcome": "routed",
        "steps": ["render"],
    }
    note.update(kw)
    return note


def _taste_note(**kw):
    note = _timeline_note(
        note_id="reel-09:clip_marker:528",
        source="clip_marker",
        name="Marker 1",
        note="this clip segment doesn't add anything and makes it look choppy",
        text="Marker 1\n\nthis clip segment doesn't add anything and makes it look choppy",
        frame=528,
        frame_in_timeline_space=528,
        outcome="routed",
        steps=["mesh_spine"],
    )
    note.update(kw)
    return note


def _timeline_with(note):
    key = note["frame_in_timeline_space"]
    timeline = FakeTimeline("Reel 09", start_frame=108000)
    timeline.AddMarker(key, "Red", note["name"], note["note"], 1, "")
    project = make_project("Podcast", timelines=[timeline], current=timeline)
    timeline._resolution_project = project
    return timeline


def _timeline_with_forbidden_delete(note):
    timeline = _timeline_with(note)
    timeline.explode_marker_delete_frames.add(note["frame_in_timeline_space"])
    return timeline


def _timeline_with_refused_delete(note):
    timeline = _timeline_with(note)
    timeline.refuse_marker_delete_frames.add(note["frame_in_timeline_space"])
    return timeline


# ── The record ────────────────────────────────────────────────────


def test_record_preserves_the_captains_words_verbatim(tmp_path):
    note = _timeline_note()
    record = mr.record_resolution(
        str(tmp_path),
        note,
        mr.STATUS_DECLINED,
        action="decline: the row split is the captain's call",
        rationale="one row is what the build produced; not changing it unseen",
    )
    assert record["name"] == note["name"]
    assert record["note"] == note["note"]
    assert record["text"] == note["text"]
    path = mr.resolution_path(str(tmp_path), note["note_id"])
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["text"] == note["text"]


# ── The checks carry a verdict ────────────────────────────────────


def test_unknown_check_is_refused():
    with pytest.raises(mr.UnknownCheck):
        mr.verify("framing_looks_fine", {"a_roll_video_rows": 2})


# ── Verified fixes clear; everything else stays ───────────────────


def test_verified_fix_clears_the_timeline_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    note = _timeline_note()
    timeline = _timeline_with(note)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="split the a-roll onto two rows",
        rationale="rebuilt with one row per angle",
        check=mr.CHECK_A_ROLL_ROWS,
        measured={"a_roll_video_rows": 2},
        timeline=timeline,
        resolve_project=timeline._resolution_project,
    )
    record = result["record"]
    assert result["marker_touched"] is True
    assert record["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert record["marker_removed"] is True
    assert record["marker_still_present"] is False
    assert record["text"] == note["text"]
    assert record["evidence"]["a_roll_video_rows"] == 2
    assert timeline.GetMarkers() == {}
    # The durable record shows the evidence that cleared it.
    on_disk = mr.read_resolution(str(tmp_path), note["note_id"])
    assert on_disk["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert on_disk["evidence"]["a_roll_video_rows"] == 2


def test_only_a_verified_fix_clears_a_marker(tmp_path, monkeypatch):
    """A decline never clears, even beside a passing check; a taste note
    is unverifiable; a failed check is addressed-unverified. Each keeps
    its marker - the delete is forbidden on the fake and would raise."""
    rows = (
        (_timeline_note(), dict(
            action="decline: the single row is deliberate for this reel",
            rationale="the captain's layout note is about the series template",
            check=mr.CHECK_A_ROLL_ROWS, measured={"a_roll_video_rows": 2}),
         mr.STATUS_DECLINED, None),
        (_taste_note(), dict(action="recut the segment tighter",
                             rationale="removed the choppy passage"),
         mr.STATUS_UNVERIFIABLE, None),
        (_timeline_note(), dict(
            action="split the a-roll onto two rows", rationale="rebuilt",
            check=mr.CHECK_A_ROLL_ROWS, measured={"a_roll_video_rows": 1}),
         mr.STATUS_ADDRESSED_UNVERIFIED, 1),
    )
    for index, (note, kwargs, status, rows_measured) in enumerate(rows):
        monkeypatch.setenv(
            "REN_SHADOW_DB", str(tmp_path / str(index) / "shadow.sqlite3"))
        timeline = _timeline_with_forbidden_delete(note)
        if "check" in kwargs:
            kwargs["timeline"] = timeline
            kwargs["resolve_project"] = timeline._resolution_project
        result = mr.resolve_note(str(tmp_path / str(index)), note, **kwargs)
        record = result["record"]
        assert record["status"] == status
        assert result["marker_touched"] is False
        assert record["marker_removed"] is False
        assert len(timeline.GetMarkers()) == 1
        if rows_measured is not None:
            assert record["evidence"]["a_roll_video_rows"] == rows_measured
        if status == mr.STATUS_DECLINED:
            assert record["marker_still_present"] is True


# ── Failure keeps the words ───────────────────────────────────────


def test_failed_removal_keeps_the_words_and_says_the_marker_is_there(
        tmp_path, monkeypatch):
    monkeypatch.setenv("REN_SHADOW_DB", str(tmp_path / "shadow.sqlite3"))
    note = _timeline_note()
    timeline = _timeline_with_refused_delete(note)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="split the a-roll onto two rows",
        rationale="rebuilt with one row per angle",
        check=mr.CHECK_A_ROLL_ROWS,
        measured={"a_roll_video_rows": 2},
        timeline=timeline,
        resolve_project=timeline._resolution_project,
    )
    record = result["record"]
    assert record["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert record["text"] == note["text"]
    assert record["evidence"]["a_roll_video_rows"] == 2
    assert record["marker_removed"] is False
    assert record["marker_still_present"] is True
    assert len(timeline.GetMarkers()) == 1
    on_disk = mr.read_resolution(str(tmp_path), note["note_id"])
    assert on_disk["text"] == note["text"]
    assert on_disk["marker_still_present"] is True


def test_stale_frame_refuses_the_deletion(tmp_path):
    note = _timeline_note()
    # The timeline was rebuilt since: another note sits at that frame now.
    timeline = FakeTimeline("Reel 09", start_frame=108000)
    timeline.AddMarker(
        1674, "Red", "Marker 9", "something the captain typed since", 1, ""
    )
    timeline.explode_marker_delete_frames.add(1674)
    project = make_project("Podcast", timelines=[timeline], current=timeline)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="split the a-roll onto two rows",
        rationale="rebuilt",
        check=mr.CHECK_A_ROLL_ROWS,
        measured={"a_roll_video_rows": 2},
        timeline=timeline,
        resolve_project=project,
    )
    record = result["record"]
    assert record["status"] == mr.STATUS_RESOLVED_VERIFIED
    assert record["marker_removed"] is False
    assert record["marker_still_present"] is True
    assert "stale frame" in record["removal"]["reason"]
    assert len(timeline.GetMarkers()) == 1


# ── A "done" claim is backed by the artefact, or it is not done ───
#
# `declared_element_reaches_reels` is the check for the commonest
# instruction the captain gives - "apply this to all of the reels" -
# and the one that failed on 2026-09-11: the mechanism landed, the
# engine's capability was reported as the project's state, and six of
# eight reels did not have it. Remove the check and a note like that
# has no mechanical proof at all, so `verifiability_of` returns False
# and a worker's assertion is the only thing on offer.


def _survey(tmp_path, **reels):
    """A real `reel_divergence` survey over fake reel snapshots."""
    from library.tools import reel_divergence as rd

    (tmp_path / "project.yaml").write_text(
        "effect:\n"
        "  full_frame_elements:\n"
        "    - element: full_frame_clip\n"
        "      placement: tail\n"
        "      asset: /shared/logo_reveal.mov\n"
        "      reason: captain\n",
        encoding="utf-8",
    )

    class Clip:
        def __init__(self, name):
            self.name = name
            self.track_type = "video"
            self.track_name = "V1"
            self.source_file = ""

    class Snap:
        def __init__(self, names):
            self.clips = [Clip(n) for n in names]

    return rd.survey(
        str(tmp_path), {reel: Snap(names) for reel, names in reels.items()}
    )


def test_a_declaration_absent_from_one_reel_or_never_read_FAILS(tmp_path):
    passed, evidence = mr.verify(
        mr.CHECK_DECLARATION_REACHES,
        {
            "divergence": _survey(
                tmp_path, **{"Reel 01": ["a.mxf"], "Reel 26": ["logo_reveal.mov"]}
            ),
            "declaration": "full_frame_elements",
        },
    )
    assert passed is False
    assert "Reel 01" in evidence["reason"]
    # A measurement that never looked refuses rather than passing.
    for missing in (
        {"declaration": "full_frame_elements"},
        {"divergence": _survey(tmp_path)},
    ):
        passed, evidence = mr.verify(mr.CHECK_DECLARATION_REACHES, missing)
        assert passed is False
        assert "never read" in evidence["reason"]


def test_a_marker_is_NOT_cleared_when_the_reels_do_not_back_the_claim(tmp_path):
    """The 2026-09-11 failure, refused where it would have cleared."""
    note = _timeline_note()
    timeline = _timeline_with(note)
    result = mr.resolve_note(
        str(tmp_path),
        note,
        action="declared the card for every reel",
        rationale="PR 995 landed the mechanism",
        check=mr.CHECK_DECLARATION_REACHES,
        measured={
            "divergence": _survey(tmp_path, **{"Reel 01": ["a.mxf"]}),
            "declaration": "full_frame_elements",
        },
        timeline=timeline,
    )
    assert result["record"]["status"] == mr.STATUS_ADDRESSED_UNVERIFIED
    assert result["marker_touched"] is False
    assert len(timeline.GetMarkers()) == 1


# --------------------------------------------------------------------------
# From test_markers_read_recorded_generation.py
#
# `resolve-axi markers` reads a recorded generation by default and does
# not fall back to querying the current timeline when no generation exists.
#
# No Resolve: the project identity and read facade are patched.

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from library.tools import marker_feedback  # noqa: E402
from library.tools import resolve_axi  # noqa: E402
from library.tools.resolve_axi import cmd_markers  # noqa: E402


class _ScratchTimeline:
    """A fresh scratch timeline: empty, and already odd about it.

    `GetStartFrame` answers None (the call the listing used to make
    unconditionally), `GetSetting` raises (the call `_timeline_fps`
    used to make outside any guard).
    """

    def __init__(self, markers=None, start=None):
        self._markers = dict(markers or {})
        self._start = start

    def GetName(self):
        return "ren-exec-scratch-001"

    def GetMarkers(self):
        return dict(self._markers)

    def GetStartFrame(self):
        return self._start

    def GetSetting(self, key):
        raise RuntimeError("no settings on a scratch timeline")

    def GetTrackCount(self, track_type):
        return 0

    def GetItemListInTrack(self, track_type, index):
        return []


# ── read_notes: list what can be listed, refuse the rest ────────────

def test_an_empty_scratch_timeline_lists_cleanly():
    """No markers, no start frame, no rate: zero notes, no crash - and
    the start frame is never even asked for, because there is nothing
    to place with it."""
    timeline = _ScratchTimeline()
    marker_feedback.read_notes(timeline)


def test_markers_with_an_unreported_start_raise_a_named_refusal():
    """Timeline markers exist but the start frame - their origin -
    will not report: a named refusal, not a bare TypeError."""
    timeline = _ScratchTimeline(
        markers={10: {"color": "Green", "name": "note",
                      "note": "look", "duration": 1, "customData": ""}},
        start=None)
    with pytest.raises(marker_feedback.TimelineUnreadableError) as excinfo:
        marker_feedback.read_notes(timeline)
    message = str(excinfo.value)
    assert "ren-exec-scratch-001" in message
    assert "start frame" in message
    assert "TypeError" not in message


# ── cmd_markers: a missing generation never falls back to live Resolve ──

def _args(**kwargs):
    base = {"project": "", "timeline": "ren-exec-scratch-001",
            "plane": "", "full": False}
    base.update(kwargs)
    return types.SimpleNamespace(**base)


def _patched_cmd(monkeypatch):
    monkeypatch.setattr(resolve_axi, "_connect", lambda: object())
    monkeypatch.setattr(
        resolve_axi, "_lease",
        lambda exclusive: _NullContext())
    project = types.SimpleNamespace(GetName=lambda: "Scratch Project")
    monkeypatch.setattr(resolve_axi, "_project",
                        lambda resolve, name: project)
    monkeypatch.setattr(
        resolve_axi, "_target_timeline",
        lambda project, name: (_ for _ in ()).throw(
            AssertionError("timeline structure read live")))


class _NullContext:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_cmd_markers_requires_a_recorded_generation(monkeypatch, capsys):
    from library.tools import timeline_read

    _patched_cmd(monkeypatch)
    def missing(*_args, **_kwargs):
        raise timeline_read.ShadowError("no recorded generation")
    monkeypatch.setattr(timeline_read, "read_any", missing)

    assert cmd_markers(_args()) == 1
    out = capsys.readouterr().out
    assert "error:" in out
    assert "no recorded generation" in out
    assert "Traceback" not in out
