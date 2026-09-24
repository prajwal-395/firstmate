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


# ── What an attachment does to identity ─────────────────────────────


def _attachment(path):
    return asdict(read_attachments("", "", _still_envelope(path), None)[0])


def test_a_note_that_has_gained_a_still_is_a_note_worth_collecting_again():
    plain = _note()
    withstill = _note(attachments=[_attachment("marker_feedback/stills/a.png")])
    assert note_identity(plain) != note_identity(withstill)


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
    record = _mf.reply_record(answers="Reel_13:9f2c4a1b7e5d03aa", answers_text="the words")
    assert record["kind"] == _mf.REPLY_RECORD_KIND
    assert record["writer"] == _mf.REPLY_WRITER
    assert record["answers"] == "Reel_13:9f2c4a1b7e5d03aa"
    assert record["answers_text"] == "the words"
    marker_payload.merge_record(marker_payload.new_envelope(), record)


def test_a_foreign_writers_record_is_not_read_as_our_reply():
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


def test_a_prose_answers_is_refused():
    """Reel 04's pink verdict: `answers="R04 blue feedback"`."""
    with pytest.raises(ValueError, match="must be a durable note identity"):
        _mf.reply_record(answers="R04 blue feedback")


def test_a_frame_locator_answers_is_refused():
    """Reel 14's green (`... @162`) and Reel 29's (`...#clip_marker@22`)."""
    with pytest.raises(ValueError, match="must be a durable note identity"):
        _mf.reply_record(answers="Reel 14 - why-ai-trusts-youtube@162")
    with pytest.raises(ValueError, match="must be a durable note identity"):
        _mf.reply_record(
            answers="Reel 29 - salvage#clip_marker@22")


def test_an_empty_answers_records_no_key():
    record = _mf.reply_record(answers_text="the words")
    assert "answers" not in record
    assert record["answers_text"] == "the words"


def test_a_durable_identity_answers_survives():
    """The legitimate producer: `feedback_ledger.durable_identity`."""
    from library.tools.feedback_ledger import durable_identity
    identity = durable_identity("Reel 14", "feedback\n\nno value prop")
    record = _mf.reply_record(answers=identity, answers_text="x")
    assert record["answers"] == identity
    marker_payload.merge_record(marker_payload.new_envelope(), record)


def test_an_answers_anchor_is_recorded_verbatim():
    anchor = {"source_file": "/f/LC4932.MXF", "source_frame": 1101}
    record = _mf.reply_record(answers="Reel_14:9f2c4a1b7e5d03aa",
                              answers_anchor=anchor)
    assert record["answers_anchor"] == anchor
    assert record["answers_anchor"] is not anchor
    data = _mf.reply_custom_data(
        answers="Reel_14:9f2c4a1b7e5d03aa", answers_anchor=anchor)
    assert _mf.reply_records_in(data)[0]["answers_anchor"] == anchor


def test_a_malformed_answers_anchor_is_refused():
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
