"""The disk half of `marker_feedback` - no Resolve, and no fake of one.

Everything here runs against real files in `tmp_path`. The Resolve half
is proved against a real running Resolve in
`tests/test_marker_feedback_against_resolve.py`; nothing in this file
stands in for it, because a fake that returns what the test just told it
is what left the module's four wrong assumptions green for months.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import marker_payload  # noqa: E402
from library.tools.marker_feedback import (  # noqa: E402
    PULL_FILE_SUFFIX,
    MarkerNote,
    _pulled_identities,
    _render,
    frames_to_timecode,
    note_identity,
    pulled_files,
    read_attachments,
)
from library.tools.project_layout import (  # noqa: E402
    AREAS, Area, Kind, ProjectLayout, ProjectLayoutViolation, WRITABLE_KINDS,
)


def _note(**kw) -> MarkerNote:
    base = dict(source="timeline_marker", name="Q", note="n", text="Q\n\nn",
                frame=10, timecode="00:00:00:10", frame_in_timeline_space=10)
    base.update(kw)
    return MarkerNote(**base)


# ── Timecode ────────────────────────────────────────────────────────


@pytest.mark.parametrize("frame,fps,expected", [
    (0, 30.0, "00:00:00:00"),
    (29, 30.0, "00:00:00:29"),
    (30, 30.0, "00:00:01:00"),
    (108000, 30.0, "01:00:00:00"),   # Resolve's default start timecode
    (108007, 30.0, "01:00:00:07"),
    (1440, 24.0, "00:01:00:00"),
])
def test_timecode_of_an_absolute_frame(frame, fps, expected):
    assert frames_to_timecode(frame, fps) == expected


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


def test_colour_is_not_part_of_identity():
    """The captain chose typed notes over colour codes: recolouring a
    marker must not make it look like a new note."""
    assert note_identity(_note(color="Blue")) == note_identity(
        _note(color="Red"))


# ── Where the record lives ──────────────────────────────────────────


def test_the_area_is_outside_pipeline_output_and_writable():
    spec = AREAS[Area.MARKER_FEEDBACK]
    assert not spec.relpath.startswith("pipeline_output"), (
        "everything under pipeline_output/ is Kind.OUTPUT - reproducible by "
        "a re-run. A typed note is destroyed by one."
    )
    assert spec.kind is Kind.CAPTURED
    assert spec.kind in WRITABLE_KINDS


def test_a_re_run_area_and_the_captured_area_are_different_places(tmp_path):
    layout = ProjectLayout(tmp_path)
    captured = layout.write_dir(Area.MARKER_FEEDBACK)
    assert layout.read_dir(Area.OUTPUT_ROOT) not in captured.parents
    assert captured.parent == layout.root


def test_the_layout_still_refuses_the_bare_project_root(tmp_path):
    layout = ProjectLayout(tmp_path)
    with pytest.raises(ProjectLayoutViolation):
        layout.assert_writable(tmp_path / "loose.json")
    assert layout.assert_writable(tmp_path / "marker_feedback" / "a.json")


def test_the_readme_states_that_a_re_run_must_not_delete_it(tmp_path):
    text = ProjectLayout(tmp_path).describe()
    assert "marker_feedback/" in text
    assert "re-run" in text


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


def test_an_unreadable_pull_file_is_skipped_not_fatal(tmp_path):
    layout = ProjectLayout(tmp_path)
    _write_pull(layout, "Edit.20260828T090000Z", "Edit", [{"note": "a"}])
    layout.write_path(
        Area.MARKER_FEEDBACK, "Edit.broken" + PULL_FILE_SUFFIX
    ).write_text("{not json", encoding="utf-8")
    assert len(pulled_files(str(tmp_path), "Edit")) == 1


def test_no_pull_directory_is_not_an_error(tmp_path):
    assert pulled_files(str(tmp_path)) == []


# ── Attachments ─────────────────────────────────────────────────────
#
# The Resolve half - a still really landing on a real marker - is proved
# in `tests/test_marker_capture_against_resolve.py`. What is checked here
# is the reader's own arithmetic against real files on disk.


def _still_envelope(relpath, **extra):
    env = marker_payload.new_envelope()
    marker_payload.merge_record(env, dict({
        "kind": marker_payload.KIND_STILL, "writer": "capture_frame",
        "writer_version": 1, "id": "still_x", "at": "t",
        "path": relpath,
    }, **extra))
    return env


def test_a_written_attachment_resolves_against_the_project(tmp_path):
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


def test_a_written_attachment_whose_file_is_gone_is_reported_absent(tmp_path):
    got = read_attachments(
        "", "", _still_envelope("marker_feedback/stills/gone.png"),
        str(tmp_path))
    assert got[0].exists is False
    assert got[0].path == "marker_feedback/stills/gone.png"


def test_a_path_the_captain_typed_is_still_read(tmp_path):
    """The channel that worked before this button, unchanged."""
    typed = tmp_path / "ref.png"
    typed.write_bytes(b"png")
    got = read_attachments(
        "GRADE", f"compare against {typed}", {}, str(tmp_path))
    assert [(a.origin, a.kind, a.exists) for a in got] == \
        [("note_text", "typed_path", True)]
    assert got[0].resolved_path == str(typed)


def test_a_typed_path_in_the_name_field_counts_too(tmp_path):
    typed = tmp_path / "in_the_name.jpg"
    got = read_attachments(str(typed), "", {}, str(tmp_path))
    assert [a.origin for a in got] == ["note_text"]
    assert got[0].exists is False


def test_the_captain_typing_the_path_the_button_wrote_is_not_two_things(
        tmp_path):
    relpath = "marker_feedback/stills/a.png"
    still = tmp_path / relpath
    still.parent.mkdir(parents=True)
    still.write_bytes(b"png")
    got = read_attachments(
        "", f"see {still}", _still_envelope(relpath), str(tmp_path))
    assert [a.origin for a in got] == ["custom_data"]


def test_without_a_project_folder_a_relative_path_is_not_invented():
    got = read_attachments("", "", _still_envelope("stills/a.png"), None)
    assert got[0].resolved_path == ""
    assert got[0].exists is False


def test_prose_is_not_mistaken_for_a_path():
    assert read_attachments(
        "", "the cut lands a beat early - hold it six frames", {}, None) == []


# ── What an attachment does to identity ─────────────────────────────


def _attachment(path):
    return asdict(read_attachments("", "", _still_envelope(path), None)[0])


def test_a_note_that_has_gained_a_still_is_a_note_worth_collecting_again():
    plain = _note()
    withstill = _note(attachments=[_attachment("marker_feedback/stills/a.png")])
    assert note_identity(plain) != note_identity(withstill)


def test_a_typed_path_does_not_change_identity_twice():
    """It is already in the note text, which identity reads."""
    text = "see /vol/refs/a.png"
    typed = _note(note=text,
                  attachments=[asdict(a) for a in
                               read_attachments("", text, {}, None)])
    assert note_identity(typed) == note_identity(_note(note=text))


def test_a_pull_file_written_before_attachments_existed_still_matches(tmp_path):
    """Old records carry no `attachments` key. A note with none must read
    as already collected, or every previously-pulled note comes back."""
    layout = ProjectLayout(tmp_path)
    _write_pull(layout, "Edit.20260828T090000Z", "Edit", [{
        "source": "timeline_marker", "name": "Q", "note": "n",
        "frame_in_timeline_space": 10,
    }])
    seen = _pulled_identities(str(tmp_path), "Edit")
    assert note_identity(_note()) in seen


# ── What the reader prints ──────────────────────────────────────────


def test_a_note_with_an_attachment_reads_differently_from_one_without():
    plain = _render([_note(note="why is this here")])
    assert "ATTACHED" not in plain

    shown = _render([_note(
        note="why is this here",
        attachments=[dict(_attachment("marker_feedback/stills/a.png"),
                          resolved_path="/p/001/marker_feedback/stills/a.png",
                          exists=True)])])
    assert "ATTACHED [still from capture_frame]: " \
           "/p/001/marker_feedback/stills/a.png" in shown
    assert "NOT ON DISK" not in shown


def test_a_missing_attachment_says_so_rather_than_disappearing():
    shown = _render([_note(attachments=[dict(
        _attachment("marker_feedback/stills/a.png"),
        resolved_path="/p/001/marker_feedback/stills/a.png", exists=False)])])
    assert "NOT ON DISK" in shown


def test_a_typed_attachment_is_labelled_as_typed():
    shown = _render([_note(
        note="see /vol/refs/ref.png",
        attachments=[asdict(a) for a in
                     read_attachments("", "see /vol/refs/ref.png", {}, None)])])
    assert "ATTACHED [typed by hand]: /vol/refs/ref.png" in shown


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


class _MarkerSurface:
    """AddMarker/GetMarkers with Resolve's measured 6-argument form."""

    def __init__(self, start=0, end=2000, left=0, duration=2000):
        self._markers = {}
        self._start, self._end = start, end
        self._left, self._duration = left, duration
        self.arity = None

    def GetStartFrame(self):
        return self._start

    def GetEndFrame(self):
        return self._end

    def GetLeftOffset(self):
        return self._left

    def GetDuration(self):
        return self._duration

    def GetName(self):
        return "Reel 13 - x"

    def AddMarker(self, key, color, name, note, duration, custom=""):
        self.arity = 6 if custom else 5
        self._markers[int(key)] = {"color": color, "name": name,
                                   "note": note, "duration": duration,
                                   "customData": custom}
        return True

    def GetMarkers(self):
        return dict(self._markers)


def test_a_reply_record_names_its_writer_and_what_it_answers():
    record = _mf.reply_record(answers="Reel_13:abc", answers_text="the words")
    assert record["kind"] == _mf.REPLY_RECORD_KIND
    assert record["writer"] == _mf.REPLY_WRITER
    assert record["answers"] == "Reel_13:abc"
    assert record["answers_text"] == "the words"
    marker_payload.merge_record(marker_payload.new_envelope(), record)


def test_reply_custom_data_merges_into_a_foreign_payload():
    """A marker's customData may already carry another writer's records."""
    data = _mf.reply_custom_data(existing="not json at all",
                                 answers="Reel_13:abc")
    envelope = marker_payload.parse(data)
    assert envelope["foreign"] == "not json at all"
    assert len(_mf.reply_records_in(data)) == 1


def test_a_reply_marker_carries_its_record_and_reads_it_back():
    """Remove the stamping and this marker is indistinguishable from
    a question the captain typed."""
    timeline = _MarkerSurface()
    payload = _mf.reply_custom_data(answers="Reel_13:abc")
    landed = _mf.place_reply_marker(timeline, 100, "Green", "reply: done",
                                    "we did it", custom_data=payload)
    assert timeline.arity == 6
    assert landed["custom_data"] == payload
    assert _mf.reply_records_in(landed["custom_data"])[0]["answers"] \
        == "Reel_13:abc"


def test_a_clip_reply_marker_carries_its_record_too():
    item = _MarkerSurface()
    payload = _mf.reply_custom_data(answers="Reel_09:def")
    landed = _mf.place_reply_clip_marker(item, 100, "Green", "reply: done",
                                         "we did it", custom_data=payload)
    assert _mf.reply_records_in(landed["custom_data"])[0]["answers"] \
        == "Reel_09:def"


def test_a_reply_with_nothing_to_record_stays_a_five_argument_call():
    """A caller with nothing to say must not be made to say an empty
    envelope - and every existing caller keeps working unchanged."""
    timeline = _MarkerSurface()
    _mf.place_reply_marker(timeline, 100, "Green", "reply", "x")
    assert timeline.arity == 5


def test_a_customdata_that_does_not_read_back_RAISES():
    """Judged by what Resolve returns, like every other field."""

    class Dropping(_MarkerSurface):
        def AddMarker(self, key, color, name, note, duration, custom=""):
            super().AddMarker(key, color, name, note, duration, "")
            return True

    with pytest.raises(_mf.MarkerWriteError) as refused:
        _mf.place_reply_marker(Dropping(), 100, "Green", "r", "x",
                               custom_data=_mf.reply_custom_data(answers="a"))
    assert "customData" in str(refused.value)


def test_a_foreign_writers_record_is_not_read_as_our_reply():
    envelope = marker_payload.new_envelope()
    marker_payload.merge_record(envelope, {
        "kind": _mf.REPLY_RECORD_KIND, "writer": "somebody_else",
        "writer_version": 1, "id": "x", "at": "2026-09-11T00:00:00Z"})
    assert _mf.reply_records_in(marker_payload.dumps(envelope)) == []
