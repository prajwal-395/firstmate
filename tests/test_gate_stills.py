"""The gate-stills loop, pinned against fake Resolve objects.

A reel lane's visual gate was always the same hand-written loop - make
the reel's timeline current, move the playhead to each named frame,
gallery-grab it, put the entry timeline and playhead back (batch 5's
`/tmp/fm-batch5/gate_stills.py`, three edits then one run per reel).
These tests drive the real `gate_stills` against fakes (the module
duck-types the application, so no Resolve is needed), pinning what that
script proved in production: exact-name lookup, per-frame positioning
with read-back, one grab per frame, out-of-range refusal, and entry
restore.
"""

from __future__ import annotations

import contextlib
import struct
import zlib

import pytest

from library.tools import gate_stills
from library.tools.gate_stills import (
    find_timeline,
    grab_reel_stills,
    still_filename,
)
from tests.resolve_double import FakeProject, FakeTimeline


def _minimal_png(width: int, height: int) -> bytes:
    """A valid PNG with the given dimensions, stdlib only."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"\x00" * (width * 3 + 1) * height
    chunks = b""
    for kind, data in ((b"IHDR", ihdr),
                       (b"IDAT", zlib.compress(raw)),
                       (b"IEND", b"")):
        chunks += (struct.pack(">I", len(data)) + kind + data
                   + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    return b"\x89PNG\r\n\x1a\n" + chunks


def _reel(name, **knobs):
    """A timeline starting at absolute frame 100, running at 24 fps."""
    return FakeTimeline(name, start_frame=100, end_frame=600, frame_rate=24,
                        timecode="00:00:04:04", **knobs)


@pytest.fixture
def no_lease(monkeypatch):
    """The lease as a no-op: these tests pin the grab loop, not the lock."""
    monkeypatch.setattr(gate_stills, "resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())


@pytest.fixture
def grab_png(monkeypatch):
    """`grab_still` as a PNG writer, recording every destination."""
    calls = []

    def fake(timeline, project, destination):
        from pathlib import Path
        calls.append(Path(destination).name)
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        Path(destination).write_bytes(_minimal_png(1920, 1080))
        return None

    monkeypatch.setattr(gate_stills, "grab_still", fake)
    return calls


def _project():
    reel = _reel("Reel 21 - my-website")
    other = _reel("Reel 14 - more-content")
    entry = _reel("Pipeline_Edit")
    return FakeProject([entry, reel, other], current=entry), entry, reel


def test_produces_stills_for_named_frames_of_named_reel(
        no_lease, grab_png, tmp_path):
    project, entry, _reel = _project()
    report = grab_reel_stills(
        project, "Reel 21 - my-website", [20, 500 - 1], tmp_path, "reel21")
    assert report["ok"] is True
    assert [s["reel_frame"] for s in report["stills"]] == [20, 499]
    # One discard warm-up after the timeline switch, then one grab per
    # frame - the warm-up is what the first-grab measurement requires.
    assert grab_png == [".warmup_reel21.png",
                        "reel21_f000020.png", "reel21_f000499.png"]
    for still in report["stills"]:
        assert still["timecode_set"] == still["timecode_read"]
        assert still["width"] == 1920 and still["height"] == 1080
        assert still["bytes"] > 0
        assert still["path"].endswith(still_filename("reel21",
                                                     still["reel_frame"]))
    # The entry timeline and its playhead are back.
    assert project.GetCurrentTimeline() is entry
    assert entry.GetCurrentTimecode() == "00:00:04:04"
    assert report["restored"] == {"timeline": "Pipeline_Edit", "ok": True}
    # The warm-up grab is discarded, never banked.
    assert not list(tmp_path.glob(".warmup_*"))


def test_timeline_name_is_exact_never_prefix(no_lease, grab_png, tmp_path):
    short = _reel("Reel 1")
    long = _reel("Reel 14")
    entry = _reel("Pipeline_Edit")
    project = FakeProject([entry, short, long], current=entry)
    report = grab_reel_stills(project, "Reel 1", [0], tmp_path, "reel1")
    assert report["ok"] is True
    assert project.set_calls[0] == "Reel 1"  # made current, never Reel 14
    assert project.GetCurrentTimeline() is entry  # and put back
    assert find_timeline(project, "Reel") is None
    missing = grab_reel_stills(project, "Reel", [0], tmp_path, "reelX")
    assert missing["ok"] is False
    assert "exactly named 'Reel'" in missing["error"]
    assert missing["stills"] == []


def test_an_out_of_range_frame_is_refused_by_name(
        no_lease, grab_png, tmp_path):
    project, _entry, _reel = _project()
    report = grab_reel_stills(
        project, "Reel 21 - my-website", [10, 500, -1], tmp_path, "reel21")
    assert report["ok"] is False
    assert [s["reel_frame"] for s in report["stills"]] == [10]
    assert {f["reel_frame"] for f in report["failed"]} == {500, -1}
    assert all("outside" in f["reason"] for f in report["failed"])
    assert "black still" in report["failed"][0]["reason"]
    assert "500" in report["error"] or "-1" in report["error"]


def test_a_playhead_that_will_not_land_fails_the_still(
        no_lease, grab_png, tmp_path):
    stuck = _reel("Reel 09 - stuck", stuck_playhead=True)
    project = FakeProject([stuck], current=stuck)
    report = grab_reel_stills(project, "Reel 09 - stuck", [50],
                              tmp_path, "reel9")
    assert report["ok"] is False
    assert report["stills"] == []
    assert report["failed"][0]["reel_frame"] == 50
    assert "would not land" in report["failed"][0]["reason"]
    # Nothing grabbed: a still of the wrong frame is never banked.
    assert list(tmp_path.glob("reel9_*.png")) == []


def test_a_rate_less_timeline_refuses_before_anything_moves(
        no_lease, grab_png, tmp_path):
    reel = FakeTimeline("Reel 21 - my-website", start_frame=100,
                        end_frame=600, settings={"timelineFrameRate": ""})
    project = FakeProject([reel], current=reel)
    report = grab_reel_stills(project, "Reel 21 - my-website", [10],
                              tmp_path, "reel21")
    assert report["ok"] is False
    assert "no frame rate" in report["error"]
    assert grab_png == []
