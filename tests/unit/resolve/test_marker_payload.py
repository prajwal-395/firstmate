"""The `customData` envelope, and the parts of the capture button that
need no Resolve.

The Resolve half - GrabStill, ExportStills, AddMarker,
UpdateMarkerCustomData - is proved against a real running Resolve in
`tests/qualification/test_marker_capture_against_resolve.py`.  Nothing here stands in
for it: a fake that answers what the test just told it is what left four
of `marker_feedback`'s assumptions wrong and green for months.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from library.tools import marker_payload  # noqa: E402
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
