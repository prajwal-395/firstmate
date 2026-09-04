"""A rough cut that already exists, read off a LIVE Resolve timeline.

`external_inputs.WITHDRAWN` used to rule this out, and its reason -
"reading it means copying that database and opening it as SQLite ... and
nothing maps its clips back onto a typed pipeline key" - is true of a
CLOSED project and false of a live one.  This module is the producer that
distinction allows, so the tests hold it to the standard the withdrawal
was protecting: what it emits must PASS the real checks in
`external_inputs`, not merely look right.

Resolve is faked here rather than driven.  The fakes return what the real
proxies were measured to return on the GEO Podcast field test
(2026-09-04), including the one-frame disagreement between
`GetLeftOffset()` and `GetSourceStartFrame()` that
`test_source_times_are_used_never_left_offset` exists to pin.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import external_inputs, timeline_ingest
from library.tools.external_inputs import ExternalStateError
from library.tools.project_layout import ProjectLayout
from library.tools.timeline_ingest import TimelineIngestError


# ── Fakes that answer the way Resolve was measured to answer ─────────

class FakePoolItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, key=None):
        props = {"File Path": self._path, "FPS": 23.976}
        return props if key is None else props.get(key)


class FakeItem:
    def __init__(self, name, start, end, src_in, src_out, path,
                 uid, left_offset=None):
        self._n, self._s, self._e = name, start, end
        self._si, self._so, self._uid = src_in, src_out, uid
        self._pool = FakePoolItem(path) if path else None
        # Resolve disagrees with itself here by a frame on about a third
        # of real items; the fake reproduces it so the module's choice
        # of the time pair is actually exercised.
        self._left = left_offset if left_offset is not None else src_in

    def GetName(self): return self._n
    def GetStart(self): return self._s
    def GetEnd(self): return self._e
    def GetDuration(self): return self._e - self._s
    def GetUniqueId(self): return self._uid
    def GetMediaPoolItem(self): return self._pool
    def GetSourceStartFrame(self): return self._si
    def GetSourceEndFrame(self): return self._so
    def GetLeftOffset(self): return self._left
    # Resolve computes at the exact NTSC rate, not the reported one.
    def GetSourceStartTime(self): return self._si * 1001 / 24000
    def GetSourceEndTime(self): return self._so * 1001 / 24000


class FakeTimeline:
    def __init__(self, name, tracks, end_frame=63694, fps="23.976",
                 width="1080", height="1920"):
        self._name, self._tracks = name, tracks
        self._end, self._fps = end_frame, fps
        self._w, self._h = width, height

    def GetName(self): return self._name
    def GetStartFrame(self): return 0
    def GetEndFrame(self): return self._end

    def GetSetting(self, key):
        return {"timelineFrameRate": self._fps,
                "timelineResolutionWidth": self._w,
                "timelineResolutionHeight": self._h}.get(key, "")

    def GetTrackCount(self, kind):
        return len([k for k in self._tracks if k[0] == kind])

    def GetTrackName(self, kind, index):
        return self._tracks[(kind, index)][0]

    def GetItemListInTrack(self, kind, index):
        return self._tracks[(kind, index)][1]


class FakeProjectManager:
    def __init__(self, listed, current):
        self._listed, self._current = listed, current

    def GetProjectListInCurrentFolder(self): return list(self._listed)
    def GetCurrentProject(self): return self._current


class FakeProject:
    def __init__(self, name, timelines=()):
        self._name, self._timelines = name, list(timelines)

    def GetName(self): return self._name
    def GetTimelineCount(self): return len(self._timelines)
    def GetTimelineByIndex(self, i): return self._timelines[i - 1]


def _media(tmp_path, *names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"\x00" * 64)
        out.append(str(p))
    return out


def _two_speaker_timeline(tmp_path, name="GEO Podcast - Synced"):
    a, b = _media(tmp_path, "LC4930.MXF", "LCATL0011.MXF")
    tracks = {
        ("video", 1): ("Akshita", [
            FakeItem("LC4930.MXF", 594, 1080, 3151, 3637, a, "uid-a1"),
            # left_offset disagrees by one frame, as measured
            FakeItem("LC4930.MXF", 1145, 1280, 4971, 5106, a, "uid-a2",
                     left_offset=4972),
        ]),
        ("video", 2): ("Craig", [
            FakeItem("LCATL0011.MXF", 0, 594, 2285, 2879, b, "uid-c1"),
        ]),
        ("audio", 1): ("Akshita CH1", [
            FakeItem("LC4930.MXF", 594, 1080, 3151, 3637, a, "uid-aa1"),
        ]),
    }
    return FakeTimeline(name, tracks)


# ── Reading ──────────────────────────────────────────────────────────

def test_it_reads_the_ground_truth_off_every_clip(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "Podcast (field test)")
    assert snap.timeline_name == "GEO Podcast - Synced"
    assert snap.reported_fps == 23.976
    assert (snap.width, snap.height) == (1080, 1920)
    assert len(snap.clips) == 4
    first = snap.picture_clips()[0]
    assert first.source_file.endswith("LC4930.MXF")
    assert first.resolve_item_id == "uid-a1"
    # exactly what Resolve returned for this item on 2026-09-04
    assert first.source_in == pytest.approx(131.42295833333333, abs=1e-9)


def test_speaker_comes_from_the_track(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "Podcast (field test)")
    assert snap.speakers() == ["Akshita", "Craig", "Akshita CH1"]  # video first
    assert {c.speaker for c in snap.picture_clips()} == {"Akshita", "Craig"}


def test_a_speaker_map_renames_a_track_without_inventing_one(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P",
        speaker_map={"Akshita CH1": "Akshita"})
    audio = [c for c in snap.clips if c.track_type == "audio"]
    assert audio[0].speaker == "Akshita"


def test_an_unnamed_track_yields_no_speaker_rather_than_a_made_up_one(tmp_path):
    a, = _media(tmp_path, "x.mov")
    tl = FakeTimeline("T", {("video", 1): ("", [
        FakeItem("x.mov", 0, 10, 0, 10, a, "uid-x")])})
    snap = timeline_ingest.snapshot_timeline(tl, "P")
    assert snap.clips[0].speaker is None


def test_source_times_are_used_never_left_offset(tmp_path):
    """The measured one-frame disagreement must resolve to the time pair."""
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    second = snap.picture_clips()[1]
    assert second.source_in == pytest.approx(4971 * 1001 / 24000, abs=1e-9)
    assert second.source_in != pytest.approx(4972 * 1001 / 24000, abs=1e-9)


def test_a_clip_with_no_media_pool_item_is_skipped_not_invented(tmp_path):
    tl = FakeTimeline("T", {("video", 1): ("V", [
        FakeItem("Text+", 0, 10, 0, 10, None, "uid-gen")])})
    assert timeline_ingest.snapshot_timeline(tl, "P").clips == ()


def test_a_frame_rate_that_is_not_a_number_is_refused(tmp_path):
    tl = _two_speaker_timeline(tmp_path)
    tl._fps = ""
    with pytest.raises(TimelineIngestError) as e:
        timeline_ingest.snapshot_timeline(tl, "P")
    assert "timelineFrameRate" in str(e.value)


# ── The naming trap ──────────────────────────────────────────────────

def test_the_exact_project_name_is_required():
    target = FakeProject("Podcast (field test)")
    pm = FakeProjectManager(["Podcast", "Podcast (field test)"], target)
    assert timeline_ingest.resolve_project_exactly(
        pm, "Podcast (field test)") is target


def test_a_near_name_is_refused_and_the_near_miss_is_named():
    """`Podcast` is the captain's untouchable original. A prefix match
    on the field-test name must never land there."""
    pm = FakeProjectManager(["Podcast", "Podcast (field test)"],
                            FakeProject("Podcast (field test)"))
    with pytest.raises(TimelineIngestError) as e:
        timeline_ingest.resolve_project_exactly(pm, "Podcast (Copy)")
    message = str(e.value)
    assert "Podcast (Copy)" in message
    assert "Podcast" in message
    assert "guess" in message.lower()


def test_it_refuses_to_open_a_project_that_is_not_already_open():
    pm = FakeProjectManager(["Podcast", "Podcast (field test)"],
                            FakeProject("Podcast"))
    with pytest.raises(TimelineIngestError) as e:
        timeline_ingest.resolve_project_exactly(pm, "Podcast (field test)")
    assert "never opens a project" in str(e.value)


def test_a_timeline_is_addressed_exactly_too(tmp_path):
    tl = _two_speaker_timeline(tmp_path)
    project = FakeProject("P", [tl])
    assert timeline_ingest.timeline_named(project,
                                          "GEO Podcast - Synced") is tl
    with pytest.raises(TimelineIngestError):
        timeline_ingest.timeline_named(project, "Main Edit")


# ── What it emits must pass the REAL checks ──────────────────────────

def _context(tmp_path):
    return external_inputs.Context(project_folder=tmp_path, state={})


def test_a_roll_assignments_passes_the_real_check(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    value = timeline_ingest.to_a_roll_assignments(snap)
    said = external_inputs.CHECKS["a_roll_assignments"](
        value, _context(tmp_path))
    assert "3 assignments" in said


def test_speech_sequence_passes_the_real_check(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    value = timeline_ingest.to_speech_sequence(snap)
    said = external_inputs.CHECKS["speech_sequence"](
        value, _context(tmp_path))
    assert "3 segments" in said


def test_the_spoken_order_is_the_timeline_order(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    seq = timeline_ingest.to_speech_sequence(snap)
    starts = [s["timeline_start"] for s in seq["segments"]]
    assert starts == sorted(starts)
    assert [s["speaker"] for s in seq["segments"]] == [
        "Craig", "Akshita", "Akshita"]


def test_the_chain_links_agree_with_the_order(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    segments = timeline_ingest.to_speech_sequence(snap)["segments"]
    assert segments[0]["previous_segment_id"] is None
    assert segments[-1]["next_segment_id"] is None
    for a, b in zip(segments, segments[1:]):
        assert a["next_segment_id"] == b["segment_id"]
        assert b["previous_segment_id"] == a["segment_id"]


def test_written_files_load_and_verify_through_external_inputs(tmp_path):
    """End to end: what this writes is what `load` accepts."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "Podcast (field test)")
    written = timeline_ingest.write_external(str(project), snap)
    assert set(written) == {"a_roll_assignments", "speech_sequence"}

    supplied = external_inputs.load(str(project))
    assert set(supplied) == {"a_roll_assignments", "speech_sequence"}
    assert "timeline_ingest" in supplied["speech_sequence"].source
    assert "GEO Podcast - Synced" in supplied["speech_sequence"].source


def test_it_refuses_to_supply_a_key_it_does_not_build(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    with pytest.raises(TimelineIngestError):
        timeline_ingest.write_external(str(project), snap,
                                       keys=("assembly_manifest",))


# ── Structural comparison, which authorises a deletion ───────────────

def test_two_reads_of_one_timeline_compare_equal(tmp_path):
    a = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    b = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    assert timeline_ingest.compare_structure(a, b) == []


def test_a_copy_with_fresh_item_ids_still_compares_equal(tmp_path):
    """Duplicating a timeline reassigns every UniqueId. If the comparison
    read them it would call every copy different and be useless for the
    one question it exists to answer."""
    a = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    other = _two_speaker_timeline(tmp_path, name="GEO Podcast - Backup")
    for (_kind, _i), (_n, items) in other._tracks.items():
        for item in items:
            item._uid = "fresh-" + item._uid
    b = timeline_ingest.snapshot_timeline(other, "P")
    assert timeline_ingest.compare_structure(a, b) == []


def test_any_difference_is_reported_rather_than_summarised(tmp_path):
    a = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    other = _two_speaker_timeline(tmp_path, name="Different")
    other._tracks[("video", 1)][1][0]._e = 9999
    b = timeline_ingest.snapshot_timeline(other, "P")
    differences = timeline_ingest.compare_structure(a, b)
    assert differences
    assert any("clip[" in d for d in differences)


def test_a_differing_clip_count_is_a_difference(tmp_path):
    a = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    other = _two_speaker_timeline(tmp_path, name="Short")
    other._tracks[("video", 1)][1].pop()
    b = timeline_ingest.snapshot_timeline(other, "P")
    assert any("clip_count" in d
               for d in timeline_ingest.compare_structure(a, b))


# ── The clock ────────────────────────────────────────────────────────

def test_the_exact_ntsc_rate_is_used_not_the_reported_one(tmp_path):
    """Resolve reports 23.976 and computes at 24000/1001. A timeline
    position derived from the reported rate sits on a different clock
    from the source times read off the same item."""
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P")
    assert snap.reported_fps == 23.976
    assert snap.fps == pytest.approx(24000 / 1001, abs=1e-12)
    first = snap.picture_clips()[0]
    assert first.timeline_start == pytest.approx(594 * 1001 / 24000, abs=1e-12)


def test_an_exact_rate_is_left_alone():
    for rate in (24.0, 25.0, 30.0, 50.0, 60.0):
        assert timeline_ingest.exact_frame_rate(rate) == rate


def test_every_ntsc_rate_maps_to_its_rational():
    assert timeline_ingest.exact_frame_rate(29.97) == pytest.approx(30000 / 1001)
    assert timeline_ingest.exact_frame_rate(59.94) == pytest.approx(60000 / 1001)
