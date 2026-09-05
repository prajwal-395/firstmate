"""Cutting an approved reel, bad takes removed, both speakers in sync.

The captain: "remove the bad takes out so that the timelines of the reels
are the finished cut". A retake is easy to see and hard to prove, and
every loose rule tried against the sixteen approved reels removed REAL
content - so the tests that matter are the ones pinning what must NOT be
cut.
"""

from __future__ import annotations

import pytest

from library.tools.reel_build import (
    DURATION_RATIO,
    MIN_TAKE_SECONDS,
    REEL_RESOLUTION,
    keep_ranges,
    placements,
    redundant_takes,
    suspected_takes,
)
from library.tools.timeline_ingest import TimelineClip


def _seg(speaker, text, start, end, uid="u"):
    return {"speaker": speaker, "text": text, "timeline_start": start,
            "timeline_end": end, "source_file": "/m/a.MXF",
            "source_start": start, "source_end": end, "resolve_item_id": uid}


def _tx(*segments):
    return {"segments": list(segments)}


AUDIT_1 = ("So last week we ran an audit on a client and their SEO team had "
           "stuffed all their keywords with H1 tags")
AUDIT_2 = ("So we ran an audit last week on a client where an SEO team "
           "stuffed all the H1 tags with keywords")


# ── What IS cut ──────────────────────────────────────────────────────

def test_a_reworded_retake_is_cut_and_the_later_take_kept():
    """A retake exists because the first was flubbed - reel 02's first
    says "stuffed all their keywords with H1 tags", which is backwards."""
    tx = _tx(_seg("Akshita", AUDIT_1, 10.0, 15.0),
             _seg("Akshita", AUDIT_2, 18.0, 22.5, "u2"))
    cuts = redundant_takes(0.0, 60.0, tx)
    assert len(cuts) == 1
    assert cuts[0].dropped_start == 10.0
    assert cuts[0].kept_start == 18.0


# ── What must NOT be cut ─────────────────────────────────────────────

def test_a_question_and_its_answer_are_not_a_retake():
    """An answer echoes the question's words. Cutting on vocabulary alone
    deletes the question."""
    tx = _tx(_seg("Craig", "what kind of content works best on AI platforms",
                  10.0, 15.0),
             _seg("Akshita", "the best content is content that answers "
                             "specific questions on AI platforms", 16.0, 21.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []


def test_mic_bleed_cannot_cut_the_other_speakers_line():
    """Akshita's words land on Craig's track through his mic, so BOTH
    sides of a pair can read as Craig. Same-speaker alone is not enough -
    the duration and symmetry tests carry it."""
    tx = _tx(_seg("Craig", "what has been the biggest mind blowing thing to you",
                  10.0, 15.0),
             _seg("Craig", "the most mind blowing thing", 16.0, 17.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []


def test_a_short_phrase_inside_a_longer_sentence_is_not_a_retake():
    """Containment alone reads 1.00 here and would delete the shorter."""
    tx = _tx(_seg("Akshita", "make sure you are writing about that", 10.0, 13.0),
             _seg("Akshita", "make sure you are writing about why you are "
                             "better than a competitor today", 14.0, 19.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []


def test_a_fragment_is_never_kept_over_a_full_line():
    """Reel 06 would have dropped 4.3s to keep a 0.5s fragment."""
    tx = _tx(_seg("Akshita", "it is going to start hallucinating because it is "
                             "confused about what you actually do", 10.0, 14.3),
             _seg("Akshita", "confused about what you actually do hallucinating",
                  15.0, 15.5, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []
    assert MIN_TAKE_SECONDS > 0.5


def test_takes_far_apart_are_not_paired():
    tx = _tx(_seg("Akshita", AUDIT_1, 10.0, 15.0),
             _seg("Akshita", AUDIT_2, 300.0, 304.5, "u2"))
    assert redundant_takes(0.0, 600.0, tx) == []


def test_a_near_miss_is_reported_not_cut():
    """Everything the cut rule is unsure of becomes a MARKER."""
    tx = _tx(_seg("Akshita", "make sure you are writing about that", 10.0, 13.0),
             _seg("Akshita", "make sure you are writing about why you are "
                             "better than a competitor today", 14.0, 19.0, "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []
    assert suspected_takes(0.0, 60.0, tx), "a near miss must still be reported"


def test_the_duration_ratio_is_bounded():
    assert DURATION_RATIO >= 1.0


# ── Keep ranges and sync ─────────────────────────────────────────────

def _clip(track, speaker, tl_start, tl_end, src_in=100.0):
    return TimelineClip(
        resolve_item_id=f"{speaker}-{tl_start}", track_type="video",
        track_index=track, track_name=speaker, speaker=speaker,
        source_file="/m/a.MXF", source_in=src_in,
        source_out=src_in + (tl_end - tl_start),
        source_in_frame=int(src_in * 24), source_out_frame=int(src_in * 24) + 1,
        source_frames=100000, timeline_start=tl_start, timeline_end=tl_end,
        name="clip")


def test_a_cut_removes_its_span_from_the_reel():
    from library.tools.reel_build import Cut
    cut = Cut(20.0, 25.0, "dropped", 26.0, 31.0, "kept", "Akshita", 0.9, 0.8)
    assert keep_ranges(0.0, 60.0, [cut]) == [(0.0, 20.0), (25.0, 60.0)]


def test_no_cut_leaves_the_span_whole():
    assert keep_ranges(0.0, 60.0, []) == [(0.0, 60.0)]


def test_both_tracks_shift_by_the_same_amount():
    """This is what stops a cut sliding one speaker against the other."""
    from library.tools.reel_build import Cut
    cut = Cut(20.0, 25.0, "d", 26.0, 31.0, "k", "Akshita", 0.9, 0.8)
    ranges = keep_ranges(0.0, 60.0, [cut])
    clips = [_clip(1, "Akshita", 0.0, 60.0), _clip(2, "Craig", 0.0, 60.0)]
    spots = placements(ranges, clips)
    by_track = {}
    for spot in spots:
        by_track.setdefault(spot["track_index"], []).append(round(spot["record"], 3))
    assert by_track[1] == by_track[2], "the two tracks must land identically"


def test_a_reel_closes_the_gap_a_cut_leaves():
    from library.tools.reel_build import Cut
    cut = Cut(20.0, 25.0, "d", 26.0, 31.0, "k", "Akshita", 0.9, 0.8)
    ranges = keep_ranges(0.0, 60.0, [cut])
    spots = placements(ranges, [_clip(1, "Akshita", 0.0, 60.0)])
    assert spots[0]["record"] == 0.0
    assert spots[1]["record"] == pytest.approx(20.0), "the second range butts up"
    assert sum(s["source_out"] - s["source_in"] for s in spots) == pytest.approx(55.0)


# ── The resolution that would otherwise be silently wrong ────────────

def test_the_reel_resolution_is_vertical_and_explicit():
    """The PROJECT default is 3840x2160 and only the existing timelines
    override it, so a timeline created through the API inherits the
    horizontal default - a silent wrong answer, not an error."""
    assert REEL_RESOLUTION == (1080, 1920)


# ── A reel must carry BOTH speakers' audio ───────────────────────────
#
# Measured 2026-09-04: the first sixteen reels were built with two video
# tracks and ONE audio track. Akshita's V1 clips carried their sound to
# A1; Craig's V2 clips had nowhere to put theirs, so Resolve placed his
# picture and discarded his audio while returning True. Sixteen reels
# reported success and played with one speaker silent.

def test_a_reel_declares_one_audio_track_per_picture_track():
    """The defect in one line: a new timeline has ONE audio track, and
    nothing asked for a second."""
    from library.tools.reel_build import required_tracks
    clips = [_clip(1, "Akshita", 0.0, 20.0), _clip(2, "Craig", 21.0, 40.0)]
    assert required_tracks(clips) == {"video": 2, "audio": 2}


def test_a_single_speaker_reel_still_declares_its_audio_track():
    from library.tools.reel_build import required_tracks
    assert required_tracks([_clip(1, "Akshita", 0.0, 20.0)]) == {
        "video": 1, "audio": 1}


def test_the_track_count_is_never_left_at_the_default():
    """Would have FAILED before the fix: the builder added video tracks
    and left audio at whatever CreateEmptyTimeline gives."""
    from library.tools.reel_build import required_tracks
    clips = [_clip(1, "Akshita", 0.0, 20.0), _clip(2, "Craig", 21.0, 40.0)]
    tracks = required_tracks(clips)
    assert tracks["audio"] == tracks["video"], (
        "one audio track per picture track, or a speaker plays silent")


def test_every_speaker_in_the_plan_has_a_track_to_land_on():
    clips = [_clip(1, "Akshita", 0.0, 20.0), _clip(2, "Craig", 21.0, 40.0)]
    from library.tools.reel_build import required_tracks
    spots = placements([(0.0, 40.0)], clips)
    needed = max(s["track_index"] for s in spots)
    assert required_tracks(clips)["audio"] >= needed


def test_the_audio_layout_is_read_off_the_master():
    """Every source carries FOUR audio channels, so a linked append
    spreads them across whatever tracks exist. The master already says
    which speaker owns which track."""
    from library.tools.reel_build import audio_layout
    clips = [_clip(1, "Akshita", 0.0, 20.0), _clip(2, "Craig", 21.0, 40.0)]
    layout = audio_layout(clips)
    assert set(layout) == {1, 2}
    assert all(paths for paths in layout.values())


def test_a_stray_is_an_item_whose_source_does_not_belong_to_its_track():
    """Would have FAILED before the fix: Akshita's audio was on A1 AND
    A2, mixed under Craig's."""
    from library.tools.reel_build import strays

    class Item:
        def __init__(self, path): self._p = path
        def GetMediaPoolItem(self): return self
        def GetClipProperty(self, key): return self._p

    class TL:
        def GetItemListInTrack(self, kind, index):
            return {1: [Item("/m/akshita.MXF")],
                    2: [Item("/m/craig.MXF"), Item("/m/akshita.MXF")]}[index]

    layout = {1: {"/m/akshita.MXF"}, 2: {"/m/craig.MXF"}}
    found = strays(TL(), layout)
    assert len(found) == 1, "the duplicate of Akshita on A2 is the stray"

def test_reel_contiguous_placement():
    """
    Two contiguous master clips must snap exactly with no uncovered frames
    between them. This tests the F3 fix (floating point rounding error snapping).
    """
    from library.tools.reel_build import calculate_snapped_records
    from unittest.mock import MagicMock
    
    # Clip 1: 0 to 5.1 seconds
    # Clip 2: 5.1 to 10.0 seconds
    clip1 = MagicMock()
    clip1.track_type = "video"
    clip2 = MagicMock()
    clip2.track_type = "video"
    
    placements_list = [
        {"clip": clip1, "source_in": 0.0, "source_out": 5.1, "record": 0.0, "track_index": 1},
        {"clip": clip2, "source_in": 5.1, "source_out": 10.0, "record": 5.1, "track_index": 1},
    ]
    
    # Run the function
    calculate_snapped_records(placements_list, 23.976)
    
    p1 = placements_list[0]
    p2 = placements_list[1]
    
    # First clip starts at 0, ends at int(round(5.1 * 23.976)) = 122
    dur1 = int(round(p1["source_out"] * 23.976)) - int(round(p1["source_in"] * 23.976))
    
    # Check that second clip snapped exactly to end of first
    assert p1["snapped_record"] == 0
    assert p2["snapped_record"] == p1["snapped_record"] + dur1


def test_build_reel_timeline_places_each_clip_exactly_once():
    """build_reel_timeline must call AppendToTimeline once per planned
    placement, at the planned record frame, on the planned track, with the
    planned source in/out.

    This is the test that was MISSING: nothing exercised the function that
    actually talks to Resolve. The 19 tests above cover the pure helpers
    (keep_ranges, placements, redundant_takes, strays, audio_layout) and
    stop at the boundary where Resolve begins. So a doubled placement
    loop, a basename pool match, and a wrong record frame all sailed
    through because the function that commits them was never called.
    """
    from unittest.mock import MagicMock

    from library.tools.reel_build import (
        build_reel_timeline,
        calculate_snapped_records,
        keep_ranges,
        placements as compute_placements,
        redundant_takes,
    )

    fps = 23.976

    # --- Build the inputs ---
    moment = MagicMock()
    moment.timeline_name = "Test Reel"
    moment.timeline_start = 0.0
    moment.timeline_end = 20.0
    moment.number = 1

    # 4 master clips, each 5 seconds, all on video track 1.
    master_clips = []
    for i in range(4):
        c = MagicMock()
        c.source_file = f"/footage/clip_{i}.MXF"
        c.track_type = "video"
        c.track_index = 1
        c.source_in = i * 5.0
        c.source_out = (i + 1) * 5.0
        c.source_start = i * 5.0
        c.source_end = (i + 1) * 5.0
        c.timeline_start = i * 5.0
        c.timeline_end = (i + 1) * 5.0
        c.speaker = "speaker_a"
        master_clips.append(c)

    transcript = {"segments": []}

    # --- Compute the PLAN so we can assert the Resolve calls match it ---
    ranges = keep_ranges(0.0, 20.0, redundant_takes(0.0, 20.0, transcript))
    planned = compute_placements(ranges, master_clips)
    calculate_snapped_records(planned, fps)

    # --- Wire up a fake Resolve ---
    project = MagicMock()
    pool = MagicMock()
    project.GetMediaPool.return_value = pool

    timeline_mock = MagicMock()
    timeline_mock.GetUniqueId.return_value = "test-uid"
    timeline_mock.GetTrackCount.return_value = 3
    pool.CreateEmptyTimeline.return_value = timeline_mock
    project.GetCurrentTimeline.return_value = timeline_mock

    root_folder = MagicMock()
    pool.GetRootFolder.return_value = root_folder

    # Each clip is found in the pool by FULL PATH (not basename).
    pool_item_by_path = {}
    pool_items_list = []
    for c in master_clips:
        pi = MagicMock()
        pi.GetClipProperty.side_effect = lambda prop, path=c.source_file: (
            path if prop == "File Path"
            else str(fps) if prop == "FPS"
            else ""
        )
        pool_item_by_path[c.source_file] = pi
        pool_items_list.append(pi)

    root_folder.GetClipList.return_value = pool_items_list
    root_folder.GetSubFolderList.return_value = []

    # --- Run ---
    build_reel_timeline(
        project, moment, master_clips, [],
        fps, 1080, 1920, "/tmp/test_project", transcript,
    )

    # --- Assert: one call per placement, not N*N ---
    calls = pool.AppendToTimeline.call_args_list
    assert len(calls) == len(planned), (
        f"Expected {len(planned)} AppendToTimeline calls (one per placement), "
        f"got {len(calls)}"
    )

    # --- Assert: each call carries the planned arguments ---
    for i, (call, plan) in enumerate(zip(calls, planned)):
        # AppendToTimeline is called as pool.AppendToTimeline([{...}])
        clip_spec = call[0][0][0]  # first positional arg, first list element

        expected_pool_item = pool_item_by_path[plan["clip"].source_file]
        pool_fps = fps  # all clips report the same FPS in this test

        expected_start = round(plan["source_in"] * pool_fps)
        expected_end = round(plan["source_out"] * pool_fps)
        expected_record = plan["snapped_record"]
        expected_track = plan["track_index"]
        expected_media_type = 1  # all video

        assert clip_spec["mediaPoolItem"] is expected_pool_item, (
            f"Placement {i}: wrong pool item"
        )
        assert clip_spec["startFrame"] == expected_start, (
            f"Placement {i}: startFrame {clip_spec['startFrame']} != {expected_start}"
        )
        assert clip_spec["endFrame"] == expected_end, (
            f"Placement {i}: endFrame {clip_spec['endFrame']} != {expected_end}"
        )
        assert clip_spec["recordFrame"] == expected_record, (
            f"Placement {i}: recordFrame {clip_spec['recordFrame']} != {expected_record}"
        )
        assert clip_spec["trackIndex"] == expected_track, (
            f"Placement {i}: trackIndex {clip_spec['trackIndex']} != {expected_track}"
        )
        assert clip_spec["mediaType"] == expected_media_type, (
            f"Placement {i}: mediaType {clip_spec['mediaType']} != {expected_media_type}"
        )

    # --- Assert: no uncovered frames between contiguous placements ---
    for i in range(len(planned) - 1):
        this_call = calls[i][0][0][0]
        next_call = calls[i + 1][0][0][0]
        this_dur = this_call["endFrame"] - this_call["startFrame"]
        this_end = this_call["recordFrame"] + this_dur
        assert this_end == next_call["recordFrame"], (
            f"Gap between placement {i} and {i+1}: "
            f"clip {i} ends at frame {this_end}, "
            f"clip {i+1} starts at frame {next_call['recordFrame']}"
        )