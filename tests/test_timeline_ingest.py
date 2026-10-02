"""A rough cut that already exists, read off a LIVE Resolve timeline.

History: docs/evidence/resolve_test_history.md#test_timeline_ingest.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from library.tools import external_inputs, timeline_ingest
from library.tools.project_layout import ProjectLayout
from library.tools.timeline_ingest import TimelineIngestError
from tests.resolve_double import (
    FakeProject,
    FakeProjectManager,
    FakeTimeline,
)
from tests.resolve_double import (
    TimelineItemSpec as FakeItem,
)


def _item(
    name,
    start,
    end,
    source_start,
    source_end,
    path,
    uid,
    left_offset=None,
    pool_frame_count=240,
):
    return FakeItem(
        name,
        start,
        end,
        path=path,
        left_offset=source_start if left_offset is None else left_offset,
        uid=uid,
        source_start_frame=source_start,
        source_end_frame=source_end,
        pool_frame_count=pool_frame_count,
        has_media=path is not None,
    )


def _timeline(name, tracks, end_frame=63694, frame_rate="23.976"):
    return FakeTimeline(
        name,
        end_frame=end_frame,
        frame_rate=frame_rate,
        video={key: value for key, value in tracks.items() if key[0] == "video"},
        audio={key: value for key, value in tracks.items() if key[0] == "audio"},
    )


def _real_media(tmp_path, name, seconds):
    """A real, decodable video+audio file.

    Video as well as audio because `verify_against_media` measures with
    the pipeline's own `extract_metadata`, which is the catalog's probe
    and wants a video stream - the same thing real footage has.
    """
    import subprocess

    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size=64x64:rate=24:duration={seconds}",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            "-c:v",
            "mpeg4",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def _short_timeline(tmp_path):
    """Two clips inside a real 10s file, for the verification paths."""
    media = _real_media(tmp_path, "take.mp4", 10.0)
    return FakeTimeline(
        "GEO Podcast - Synced",
        end_frame=63694,
        frame_rate="23.976",
        video={
            ("video", 1): (
                "Akshita",
                [
                    _item("take.mp4", 0, 48, 24, 72, media, "uid-a1"),
                    _item("take.mp4", 48, 96, 96, 144, media, "uid-a2"),
                ],
            ),
            ("video", 2): (
                "Craig",
                [
                    _item("take.mp4", 96, 144, 168, 216, media, "uid-c1"),
                ],
            ),
        },
    )


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
        ("video", 1): (
            "Akshita",
            [
                _item("LC4930.MXF", 594, 1080, 3151, 3637, a, "uid-a1"),
                # left_offset disagrees by one frame, as measured
                _item(
                    "LC4930.MXF", 1145, 1280, 4971, 5106, a, "uid-a2", left_offset=4972
                ),
            ],
        ),
        ("video", 2): (
            "Craig",
            [
                _item("LCATL0011.MXF", 0, 594, 2285, 2879, b, "uid-c1"),
            ],
        ),
        ("audio", 1): (
            "Akshita CH1",
            [
                _item("LC4930.MXF", 594, 1080, 3151, 3637, a, "uid-aa1"),
            ],
        ),
    }
    return _timeline(name, tracks)


# ── Reading ──────────────────────────────────────────────────────────


def test_speaker_comes_from_the_track_and_is_never_invented(tmp_path):
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "Podcast (field test)"
    )
    assert snap.speakers() == ["Akshita", "Craig", "Akshita CH1"]  # video first
    assert {c.speaker for c in snap.picture_clips()} == {"Akshita", "Craig"}
    # A speaker map renames a track without inventing one.
    snap = timeline_ingest.snapshot_timeline(
        _two_speaker_timeline(tmp_path), "P", speaker_map={"Akshita CH1": "Akshita"}
    )
    audio = [c for c in snap.clips if c.track_type == "audio"]
    assert audio[0].speaker == "Akshita"
    # An unnamed track yields no speaker rather than a made-up one.
    (a,) = _media(tmp_path, "x.mov")
    tl = _timeline(
        "T", {("video", 1): ("", [_item("x.mov", 0, 10, 0, 10, a, "uid-x")])}
    )
    assert timeline_ingest.snapshot_timeline(tl, "P").clips[0].speaker is None


def test_source_frames_are_used_never_source_times(tmp_path):
    """FILE-RELATIVE frames, not TIMECODE-ABSOLUTE times.

    This test replaces one that asserted the opposite. The original
    reasoning - "the time pair agrees with the frame pair" - was measured
    on the field test's only source file with a zero start timecode, so
    it could not see the disagreement. Over all 167 clips, the time pair
    put 91 of them past the end of their own file.
    """
    timeline = _two_speaker_timeline(tmp_path)
    for kind in ("video", "audio"):
        for index in range(1, timeline.GetTrackCount(kind) + 1):
            for item in timeline.GetItemListInTrack(kind, index):
                item.start_tc_frames = 123981  # LCATL0013's real start TC

    snap = timeline_ingest.snapshot_timeline(timeline, "P")
    first = snap.picture_clips()[0]
    # file-relative: frame 3151 of the media
    assert first.source_in == pytest.approx(3151 * 1001 / 24000, abs=1e-9)
    # and emphatically NOT the timecode-absolute reading
    assert first.source_in < 1000.0
    # Nor GetLeftOffset(): also file-relative, but one frame out on about
    # a third of real items.
    second = snap.picture_clips()[1]
    assert second.source_in == pytest.approx(4971 * 1001 / 24000, abs=1e-9)
    assert second.source_in != pytest.approx(4972 * 1001 / 24000, abs=1e-9)


def test_a_range_outside_its_own_file_is_reported(tmp_path):
    """The check that would have caught the defect on day one."""
    media = _real_media(tmp_path, "short.mp4", 2.0)
    tl = _timeline(
        "T",
        {
            ("video", 1): (
                "V",
                [_item("short.mp4", 0, 4800, 0, 4800, media, "uid-long")],
            )
        },
    )
    snap = timeline_ingest.snapshot_timeline(tl, "P")
    complaints = timeline_ingest.verify_against_media(snap)
    # Both halves fire: the exact frame bound and the ffprobe duration.
    assert len(complaints) == 2
    assert all("uid-long" in c for c in complaints)
    assert any("frames in it" in c for c in complaints)
    assert any("2.00s long" in c for c in complaints)
    # A file ffprobe cannot read is not a verified range.
    snap = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    assert any("cannot measure" in c
               for c in timeline_ingest.verify_against_media(snap))


def test_a_snapshot_with_a_bad_range_is_never_supplied(tmp_path):
    """Supplying it would be supplying a false ground truth."""
    media = _real_media(tmp_path, "short.mp4", 2.0)
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    tl = _timeline(
        "T",
        {
            ("video", 1): (
                "V",
                [_item("short.mp4", 0, 4800, 0, 4800, media, "uid-long")],
            )
        },
    )
    snap = timeline_ingest.snapshot_timeline(tl, "P")
    with pytest.raises(TimelineIngestError) as excinfo:
        timeline_ingest.write_external(str(project), snap)
    assert "false ground truth" in str(excinfo.value)


def test_a_clip_with_no_media_pool_item_is_skipped_not_invented(tmp_path):
    tl = _timeline(
        "T", {("video", 1): ("V", [_item("Text+", 0, 10, 0, 10, None, "uid-gen")])}
    )
    assert timeline_ingest.snapshot_timeline(tl, "P").clips == ()


def test_a_frame_rate_that_is_not_a_number_is_refused(tmp_path):
    tl = _two_speaker_timeline(tmp_path)
    tl._frame_rate = ""
    with pytest.raises(TimelineIngestError) as e:
        timeline_ingest.snapshot_timeline(tl, "P")
    assert "timelineFrameRate" in str(e.value)


# ── The naming trap ──────────────────────────────────────────────────


def test_a_project_is_addressed_by_its_exact_name_and_never_opened():
    """`Podcast` is the captain's untouchable original: a near name is
    refused with the near miss named, never prefix-matched onto it, and
    nothing opens a project that is not already open."""
    target = FakeProject("Podcast (field test)")
    names = ["Podcast", "Podcast (field test)"]
    pm = FakeProjectManager(target, project_names=names)
    assert timeline_ingest.resolve_project_exactly(pm, "Podcast (field test)") is target

    with pytest.raises(TimelineIngestError) as e:
        timeline_ingest.resolve_project_exactly(pm, "Podcast (Copy)")
    message = str(e.value)
    assert "Podcast (Copy)" in message
    assert "Podcast" in message
    assert "guess" in message.lower()

    pm = FakeProjectManager(FakeProject("Podcast"), project_names=names)
    with pytest.raises(TimelineIngestError) as e:
        timeline_ingest.resolve_project_exactly(pm, "Podcast (field test)")
    assert "never opens a project" in str(e.value)


def test_a_timeline_is_addressed_exactly_too(tmp_path):
    tl = _two_speaker_timeline(tmp_path)
    project = FakeProject("P", [tl])
    assert timeline_ingest.timeline_named(project, "GEO Podcast - Synced") is tl
    with pytest.raises(TimelineIngestError):
        timeline_ingest.timeline_named(project, "Main Edit")


# ── What it emits must pass the REAL checks ──────────────────────────


def _context(tmp_path):
    return external_inputs.Context(project_folder=tmp_path, state={})


def test_the_spoken_order_is_the_timeline_order(tmp_path):
    snap = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    seq = timeline_ingest.to_speech_sequence(snap)
    starts = [s["timeline_start"] for s in seq["segments"]]
    assert starts == sorted(starts)
    assert [s["speaker"] for s in seq["segments"]] == ["Craig", "Akshita", "Akshita"]
    # The chain links agree with the order.
    segments = seq["segments"]
    assert segments[0]["previous_segment_id"] is None
    assert segments[-1]["next_segment_id"] is None
    for a, b in pairwise(segments):
        assert a["next_segment_id"] == b["segment_id"]
        assert b["previous_segment_id"] == a["segment_id"]


def test_written_files_load_and_verify_through_external_inputs(tmp_path):
    """End to end: what this writes is what `load` accepts."""
    project = tmp_path / "project"
    project.mkdir()
    ProjectLayout(str(project)).ensure()
    snap = timeline_ingest.snapshot_timeline(
        _short_timeline(tmp_path), "Podcast (field test)"
    )
    written = timeline_ingest.write_external(str(project), snap)
    assert set(written) == {"a_roll_assignments", "speech_sequence"}

    supplied = external_inputs.load(str(project))
    assert set(supplied) == {"a_roll_assignments", "speech_sequence"}
    assert "timeline_ingest" in supplied["speech_sequence"].source
    assert "GEO Podcast - Synced" in supplied["speech_sequence"].source
    # It refuses to supply a key it does not build.
    with pytest.raises(TimelineIngestError):
        timeline_ingest.write_external(str(project), snap, keys=("assembly_manifest",))


# ── Structural comparison, which authorises a deletion ───────────────


def test_a_copy_with_fresh_item_ids_still_compares_equal(tmp_path):
    """Duplicating a timeline reassigns every UniqueId. If the comparison
    read them it would call every copy different and be useless for the
    one question it exists to answer."""
    a = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    other = _two_speaker_timeline(tmp_path, name="GEO Podcast - Backup")
    for kind in ("video", "audio"):
        for index in range(1, other.GetTrackCount(kind) + 1):
            for item in other.GetItemListInTrack(kind, index):
                item._uid = "fresh-" + item._uid
    b = timeline_ingest.snapshot_timeline(other, "P")
    assert timeline_ingest.compare_structure(a, b) == []
    # Any real difference is reported rather than summarised.
    other = _two_speaker_timeline(tmp_path, name="Different")
    other.GetItemListInTrack("video", 1)[0]._duration = 9999
    b = timeline_ingest.snapshot_timeline(other, "P")
    differences = timeline_ingest.compare_structure(a, b)
    assert differences
    assert any("clip[" in d for d in differences)


# ── The clock ────────────────────────────────────────────────────────


def test_the_exact_ntsc_rate_is_used_not_the_reported_one(tmp_path):
    """Resolve reports 23.976 and computes at 24000/1001. A timeline
    position derived from the reported rate sits on a different clock
    from the source times read off the same item."""
    snap = timeline_ingest.snapshot_timeline(_two_speaker_timeline(tmp_path), "P")
    assert snap.reported_fps == 23.976
    assert snap.fps == pytest.approx(24000 / 1001, abs=1e-12)
    first = snap.picture_clips()[0]
    assert first.timeline_start == pytest.approx(594 * 1001 / 24000, abs=1e-12)


# ── The silent case: in-bounds for ffprobe, wrong audio entirely ─────


def test_a_frame_range_past_the_pools_own_count_is_caught(tmp_path):
    """The check the DURATION check cannot make.

    Root cause of the 2026-09-04 defect: source ranges were read with
    `GetSourceStartTime()`, which is timecode-absolute, so every clip was
    displaced by its media's start timecode. For `LC4932.MXF` that is
    512.9s inside a 4941s file - so 74 of 167 clips asked for a real,
    in-bounds range holding COMPLETELY DIFFERENT speech. They extracted
    cleanly, weighed the right number of bytes, and passed every
    output-shaped guard. Comparing frame numbers to the file's own frame
    count is what finds them.
    """
    media = _real_media(tmp_path, "take.mp4", 10.0)
    # reads to source frame 200 of a file the pool says holds 100
    item = _item("take.mp4", 0, 48, 152, 200, media, "uid-x", pool_frame_count=100)
    tl = _timeline("T", {("video", 1): ("V", [item])})
    snap = timeline_ingest.snapshot_timeline(tl, "P")
    assert snap.clips[0].source_frames == 100
    complaints = timeline_ingest.verify_against_media(snap)
    assert any("only 100 frames" in c for c in complaints)


# ── PLAYED length, not source length ─────────────────────────────────


def test_source_out_is_the_played_end_not_the_reported_one(tmp_path):
    """Measured 2026-09-04: on 50 of the field test's 167 clips,
    `GetSourceEndFrame() - GetSourceStartFrame()` and the clip's TIMELINE
    duration disagree by exactly one frame, in both directions. The
    timeline duration is what Resolve renders - the same PLAYED-versus-
    SOURCE distinction `fusion/played_window.py` states for comp time.

    Anything that lays spans end to end by source length while
    positioning them by timeline gaps drifts one frame per disagreement.
    """
    media = _real_media(tmp_path, "take.mp4", 10.0)
    # timeline slot is 48 frames; Resolve reports a 49-frame source range
    item = _item("take.mp4", 0, 48, 24, 73, media, "uid-x")
    tl = _timeline("T", {("video", 1): ("V", [item])})
    clip = timeline_ingest.snapshot_timeline(tl, "P").clips[0]

    played = clip.source_out - clip.source_in
    assert played == pytest.approx(48 * 1001 / 24000, abs=1e-9)
    assert played != pytest.approx(49 * 1001 / 24000, abs=1e-9)
    # the raw reading is still recoverable
    assert clip.source_out_frame == 73
    assert clip.source_length_disagrees is True

    # So a track telescopes to its timeline exactly: spans + gaps equal
    # the last clip's end, to the sample. Before this rule the field-test
    # track came out 41.7ms - exactly one frame - short.
    items = [
        _item("take.mp4", 0, 48, 24, 73, media, "a"),  # +1 frame
        _item("take.mp4", 96, 144, 96, 143, media, "b"),  # -1 frame
        _item("take.mp4", 144, 192, 168, 216, media, "c"),  # exact
    ]
    tl = _timeline("T", {("video", 1): ("V", items)})
    clips = sorted(
        timeline_ingest.snapshot_timeline(tl, "P").clips, key=lambda c: c.timeline_start
    )
    total, cursor = 0.0, 0.0
    for c in clips:
        gap = c.timeline_start - cursor
        if gap > 0:
            total += gap
        total += c.source_out - c.source_in
        cursor = c.timeline_end
    assert total == pytest.approx(clips[-1].timeline_end, abs=1e-12)
