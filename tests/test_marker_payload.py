"""The `customData` envelope, and the parts of the capture button that
need no Resolve.

The Resolve half - GrabStill, ExportStills, AddMarker,
UpdateMarkerCustomData - is proved against a real running Resolve in
`tests/test_marker_capture_against_resolve.py`.  Nothing here stands in
for it: a fake that answers what the test just told it is what left four
of `marker_feedback`'s assumptions wrong and green for months.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools import marker_payload  # noqa: E402
from library.tools.marker_capture import (  # noqa: E402
    CaptureError,
    Playhead,
    _project_root_above,
    read_playhead,
    still_filename,
    timecode_to_frames,
    unused_path,
)


# ── The envelope ────────────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["kind", "writer", "writer_version",
                                     "id", "at"])
def test_a_record_missing_a_required_key_raises(missing):
    record = {"kind": "still", "writer": "w", "writer_version": 1,
              "id": "r", "at": "t"}
    record.pop(missing)
    with pytest.raises(ValueError):
        marker_payload.merge_record(marker_payload.new_envelope(), record)


# ── Nothing is destroyed ────────────────────────────────────────────


@pytest.mark.parametrize("raw,reason_fragment", [
    ("a path I typed by hand", "not JSON"),
    ('{"someone": "else"}', "not a vep.marker/1 envelope"),
    ('{"schema": "vep.marker/99", "records": []}', "does not understand"),
])
def test_customdata_this_module_did_not_write_is_kept_not_overwritten(
        raw, reason_fragment):
    env = marker_payload.parse(raw)
    assert marker_payload.is_envelope(env)
    assert reason_fragment in env["foreign_reason"]
    assert env["foreign"] in (raw, json.loads(raw) if raw.startswith("{") else raw)
    # And it survives a merge and a write - which is the whole point.
    marker_payload.merge_record(env, {
        "kind": "still", "writer": "w", "writer_version": 1,
        "id": "r", "at": "t", "path": "a.png"})
    assert "foreign" in marker_payload.parse(marker_payload.dumps(env))


# ── A path the captain typed ────────────────────────────────────────


# Not under a home directory: a test source may not carry one
# (tests/test_tests_never_reach_real_projects.py), and these are shapes to
# match rather than files to open.
@pytest.mark.parametrize("text,expected", [
    ("/vol/refs/Desktop/ref.png", ["/vol/refs/Desktop/ref.png"]),
    ("look at file:///vol/refs/a.jpg please", ["/vol/refs/a.jpg"]),
    ("see /vol/refs/a.png and /vol/refs/b.png", ["/vol/refs/a.png",
                                                 "/vol/refs/b.png"]),
    ("twice /vol/refs/a.png /vol/refs/a.png", ["/vol/refs/a.png"]),
    ("the shot before this one is better", []),
    ("notes/relative.md is not absolute", []),
    ("/vol/refs/no_extension_here", []),
    ("ends a sentence /vol/refs/a.png.", ["/vol/refs/a.png"]),
])
def test_typed_paths_are_matched_conservatively(text, expected):
    assert marker_payload.typed_paths(text) == expected


# ── Timecode ────────────────────────────────────────────────────────


@pytest.mark.parametrize("timecode,fps,expected", [
    ("00:00:00:00", 30.0, 0),
    ("00:00:01:10", 30.0, 40),
    ("01:00:00:00", 30.0, 108000),
    ("00:01:00:00", 24.0, 1440),
    ("00:00:01:00", 23.976, 24),
])
def test_non_drop_timecode_to_frames(timecode, fps, expected):
    assert timecode_to_frames(timecode, fps) == expected


def test_drop_frame_timecode_drops_two_frames_a_minute_except_every_tenth():
    # 29.97 drop-frame: 00:01:00;02 is the frame after 00:00:59;29.
    assert timecode_to_frames("00:00:59;29", 29.97) == 1799
    assert timecode_to_frames("00:01:00;02", 29.97) == 1800
    # The tenth minute keeps its two frames.
    assert timecode_to_frames("00:10:00;00", 29.97) == 17982


def test_a_semicolon_is_what_says_drop_frame_not_the_rate():
    """Resolve allows a 29.97 timeline running non-drop, and reads back
    a `:`.  Deciding from the rate alone would be wrong for it."""
    assert timecode_to_frames("00:01:00:00", 29.97) == 1800
    assert timecode_to_frames("00:01:00;02", 29.97) == 1800


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


# ── Which project the timeline belongs to ───────────────────────────


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


def test_the_last_playable_frame_is_accepted():
    playhead = read_playhead(_Bounds("00:00:05:28"))     # frame 178 of 0..178
    assert playhead.marker_key == 178


def test_a_playhead_past_the_end_is_refused_rather_than_grabbed():
    with pytest.raises(CaptureError, match="past the end"):
        read_playhead(_Bounds("00:00:08:00"))


def test_the_bound_is_measured_against_a_moved_origin():
    """`GetEndFrame` is absolute, the same space as `GetStartFrame`."""
    moved = dict(start_tc="01:00:00:00", start=108000, end=108179)
    assert read_playhead(_Bounds("01:00:05:28", **moved)).marker_key == 178
    with pytest.raises(CaptureError, match="past the end"):
        read_playhead(_Bounds("01:00:06:00", **moved))
