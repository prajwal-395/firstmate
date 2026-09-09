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
    REEL_RESOLUTION,
    ReelBuildError,
    keep_ranges,
    place_overlay_segments,
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
    # DURATION_RATIO is what refuses this, and it is the guard the module
    # docstring actually claims the protection for: 4.3s against 0.5s is
    # a ratio of 8.6. `MIN_TAKE_SECONDS` was removed 2026-09-05 and this
    # case is unaffected by that, which is the point of asserting the
    # surviving guard here rather than the removed one.
    assert 4.3 / 0.5 > DURATION_RATIO


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
    spots = placements(ranges, clips, 23.976)
    by_track = {}
    for spot in spots:
        by_track.setdefault(spot["track_index"], []).append(round(spot["record"], 3))
    assert by_track[1] == by_track[2], "the two tracks must land identically"


def test_a_reel_closes_the_gap_a_cut_leaves():
    from library.tools.reel_build import Cut
    cut = Cut(20.0, 25.0, "d", 26.0, 31.0, "k", "Akshita", 0.9, 0.8)
    ranges = keep_ranges(0.0, 60.0, [cut])
    spots = placements(ranges, [_clip(1, "Akshita", 0.0, 60.0)], 23.976)
    assert spots[0]["record"] == 0.0
    assert spots[1]["snapped_record"] == 480, "the second range butts up"
    assert sum((s["source_out"] - s["source_in"]) * 23.976 for s in spots) == pytest.approx(1320)


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
#
# The helpers that first closed this (`required_tracks`, `audio_layout`,
# `strays`) are gone: the track plan (`timeline_layout.plan_layout`)
# owns row counts and names now, and stream enforcement reads every
# placed audio item back. Their coverage lives in
# `tests/test_reel_build_sop_conformance.py`, which drives the real
# `build_reel_timeline` against fake Resolve and reads the timeline
# back through the verifier.

def test_reel_contiguous_placement_exact_frames():
    """
    Two contiguous ranges must snap exactly with no uncovered frames between them.
    This tests the exact frame arithmetic in placements.
    """
    from library.tools.reel_build import placements
    from unittest.mock import MagicMock
    
    # Range 1: 0 to 5.1 seconds
    # Range 2: 5.1 to 10.0 seconds
    clip1 = MagicMock(timeline_start=0.0, timeline_end=5.1, source_in=100.0, track_index=1, speaker="A")
    clip2 = MagicMock(timeline_start=5.1, timeline_end=10.0, source_in=200.0, track_index=1, speaker="A")
    
    ranges = [(0.0, 5.1), (5.1, 10.0)]
    
    placements_list = placements(ranges, [clip1, clip2], 23.976)
    
    p1 = placements_list[0]
    p2 = placements_list[1]
    
    dur1 = int(round(p1["source_out"] * 23.976)) - int(round(p1["source_in"] * 23.976))
    
    assert p1["snapped_record"] == 0
    assert p2["snapped_record"] == p1["snapped_record"] + dur1


def test_build_reel_timeline_places_each_clip_exactly_once():
    """build_reel_timeline must call AppendToTimeline once per planned
    placement, at the planned record frame, on the planned track, with the
    planned source in/out.

    This is the test that was MISSING: nothing exercised the function that
    actually talks to Resolve. The 19 tests above cover the pure helpers
    (keep_ranges, placements, redundant_takes) and stop at the boundary
    where Resolve begins. So a doubled placement loop, a basename pool
    match, and a wrong record frame all sailed through because the
    function that commits them was never called.
    """
    from unittest.mock import MagicMock

    from library.tools.reel_build import (
        build_reel_timeline,
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
    moment.call_to_action = None

    # 4 master clips, each 5 seconds, all on video track 1.
    master_clips = []
    for i in range(4):
        c = MagicMock()
        c.source_file = f"/footage/clip_{i}.MXF"
        c.track_type = "video"
        c.track_index = 1
        c.track_name = "Angle 1"
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
    planned = compute_placements(ranges, master_clips, fps)

    # --- Wire up a fake Resolve ---
    project = MagicMock()
    pool = MagicMock()
    project.GetMediaPool.return_value = pool

    timeline_mock = MagicMock()
    timeline_mock.GetUniqueId.return_value = "test-uid"
    # One angle: the plan mints V1/A1, so the fixture reports exactly
    # those. Reads back nothing - the link pass and the occupancy
    # sweep iterate empty rows, which the faithful fake in
    # test_reel_build_sop_conformance.py covers instead.
    timeline_mock.GetTrackCount.side_effect = lambda mt: (
        1 if mt in ("video", "audio") else 0)
    timeline_mock.GetItemListInTrack.return_value = []
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
def test_reel_05_boundary_rounding():
    """
    Test the exact rounding boundary from Reel 05 that produced the 1-frame hole.
    Frame 841 at 23.976 fps corresponds to ~35.0767 seconds.
    """
    from library.tools.reel_build import placements
    from unittest.mock import MagicMock
    
    # 841 frames at 23.976 fps is exactly 841 / 23.976 = 35.07674341007674 seconds.
    boundary_time = 841 / 23.976
    clip1 = MagicMock(timeline_start=0.0, timeline_end=boundary_time, source_in=100.0, track_index=1, speaker="A")
    clip2 = MagicMock(timeline_start=boundary_time, timeline_end=boundary_time + 10.0, source_in=200.0, track_index=1, speaker="A")
    
    ranges = [(0.0, boundary_time), (boundary_time, boundary_time + 10.0)]
    
    placements_list = placements(ranges, [clip1, clip2], 23.976)
    
    p1 = placements_list[0]
    p2 = placements_list[1]
    
    # Check that second clip snapped exactly to end of first
    dur1 = int(round(p1["source_out"] * 23.976)) - int(round(p1["source_in"] * 23.976))
    assert p1["snapped_record"] == 0
    assert dur1 == 841
    assert p2["snapped_record"] == 841


# ── The closing CTA, from anywhere in the episode ────────────────────
#
# The captain's format closes every reel on a genuinely spoken call to
# action, and this episode says about six of them in nineteen minutes.
# While a moment was ONE contiguous master window those two requirements
# could not both be met and the batch came out at three reels.  A moment
# now carries a second range - its closer - which `reel_ranges` appends
# LAST and `placements` lays down at the running offset like any other.
#
# Nothing is copied or synthesised to let six CTAs close sixteen reels:
# the same real clip is placed again, which is an ordinary editing move.


def _moment(start, end, cta=None, number=1, slug="topic"):
    from library.tools.reel_proposal import CallToAction, ReelMoment
    return ReelMoment(
        number=number, slug=slug, reason="a complete exchange",
        timeline_start=start, timeline_end=end,
        call_to_action=(CallToAction(timeline_start=cta[0],
                                     timeline_end=cta[1])
                        if cta else None))


def test_a_moment_with_no_cta_plays_only_its_body():
    """The field is additive: every reel built before it behaves the same."""
    from library.tools.reel_build import reel_ranges
    assert reel_ranges(_moment(100.0, 160.0), _tx()) == [(100.0, 160.0)]


def test_the_cta_range_is_appended_after_the_body():
    from library.tools.reel_build import reel_ranges
    ranges = reel_ranges(_moment(600.0, 660.0, cta=(468.06, 476.5)), _tx())
    assert ranges == [(600.0, 660.0), (468.06, 476.5)], (
        "the closer is laid down LAST, however far away it is on the master")


def test_a_distant_cta_clip_lands_last_on_the_reel():
    """The done-check: build the placements and prove the CTA is the final
    clip, at the record frame the body's length puts it at.

    Craig's closer at 468.06s is EARLIER on the master than this reel's
    body at 600-660s, which is the case that cannot be expressed by
    subtracting from one window - `keep_ranges` only ever removes.
    """
    from library.tools.reel_build import placements, reel_ranges

    fps = 24000 / 1001
    body = _clip(1, "Akshita", 600.0, 660.0, src_in=600.0)
    closer = _clip(2, "Craig", 460.0, 480.0, src_in=460.0)

    ranges = reel_ranges(_moment(600.0, 660.0, cta=(468.0, 476.0)), _tx())
    spots = placements(ranges, [body, closer], fps)

    assert len(spots) == 2, "the body clip and the closer, once each"
    assert spots[0]["clip"] is body
    assert spots[-1]["clip"] is closer, "the CTA plays LAST on the reel"

    # The closer starts exactly where the body ends: no hole, no overlap.
    body_frames = (int(round(660.0 * fps)) - int(round(600.0 * fps)))
    assert spots[0]["snapped_record"] == 0
    assert spots[-1]["snapped_record"] == body_frames

    # And it plays the master seconds the plan named, not the body's.
    assert spots[-1]["source_in"] == pytest.approx(468.0, abs=1 / fps)
    assert spots[-1]["source_out"] == pytest.approx(476.0, abs=1 / fps)

    # The reel is exactly body + closer long, with nothing between them.
    reel_frames = sum(int(round(b * fps)) - int(round(a * fps))
                      for a, b in ranges)
    last_len = (int(round(spots[-1]["source_out"] * fps))
                - int(round(spots[-1]["source_in"] * fps)))
    assert spots[-1]["snapped_record"] + last_len == reel_frames


def test_one_shared_cta_range_closes_two_different_reels():
    """Six spoken CTAs must be able to close sixteen reels.

    Two reels whose bodies are nowhere near each other close on the SAME
    seconds of the episode. Nothing about the second build differs from
    the first except where the closer lands, because the reels differ in
    length - which is the whole proof that the range is reused rather
    than owned by one reel.
    """
    from library.tools.reel_build import placements, reel_ranges

    fps = 24000 / 1001
    shared = (468.0, 476.0)
    closer = _clip(2, "Craig", 460.0, 480.0, src_in=460.0)

    first_body = _clip(1, "Akshita", 100.0, 160.0, src_in=100.0)
    second_body = _clip(1, "Akshita", 700.0, 745.0, src_in=700.0)

    first = placements(
        reel_ranges(_moment(100.0, 160.0, cta=shared, number=1), _tx()),
        [first_body, closer], fps)
    second = placements(
        reel_ranges(_moment(700.0, 745.0, cta=shared, number=2), _tx()),
        [second_body, closer], fps)

    for spots in (first, second):
        assert spots[-1]["clip"] is closer, "both reels close on the same clip"
        assert spots[-1]["source_in"] == pytest.approx(468.0, abs=1 / fps)
        assert spots[-1]["source_out"] == pytest.approx(476.0, abs=1 / fps)

    # Same source seconds, different record frames - the clip is PLACED
    # again, not copied, and each reel puts it after its own body.
    assert (first[-1]["snapped_record"]
            == int(round(160.0 * fps)) - int(round(100.0 * fps)))
    assert (second[-1]["snapped_record"]
            == int(round(745.0 * fps)) - int(round(700.0 * fps)))
    assert first[-1]["snapped_record"] != second[-1]["snapped_record"]


def test_a_cta_survives_the_bad_takes_being_cut_from_the_body():
    """Cutting a retake out of the body must not disturb the closer - it
    moves earlier by exactly the cut's length and plays the same seconds."""
    from library.tools.reel_build import Cut, placements, reel_ranges

    fps = 24000 / 1001
    tx = _tx(_seg("Akshita", AUDIT_1, 120.0, 125.0),
             _seg("Akshita", AUDIT_2, 128.0, 132.5, "u2"))
    ranges = reel_ranges(_moment(100.0, 160.0, cta=(468.0, 476.0)), tx)

    assert ranges[:-1] == [(100.0, 120.0), (125.0, 160.0)], "the retake is cut"
    assert ranges[-1] == (468.0, 476.0), "the closer is untouched and last"

    closer = _clip(2, "Craig", 460.0, 480.0, src_in=460.0)
    spots = placements(ranges, [_clip(1, "Akshita", 100.0, 160.0,
                                      src_in=100.0), closer], fps)
    assert spots[-1]["clip"] is closer
    kept = sum(int(round(b * fps)) - int(round(a * fps))
               for a, b in ranges[:-1])
    assert spots[-1]["snapped_record"] == kept


def test_the_cta_range_is_not_scanned_for_retakes():
    """The closer is placed WHOLE. A retake scan silently shortening a
    passage the captain approved is worse than a repetition in it."""
    from library.tools.reel_build import reel_ranges
    tx = _tx(_seg("Craig", AUDIT_1, 468.0, 473.0),
             _seg("Craig", AUDIT_2, 474.0, 478.5, "u2"))
    ranges = reel_ranges(_moment(100.0, 160.0, cta=(468.0, 480.0)), tx)
    assert ranges[-1] == (468.0, 480.0)


def test_a_moment_that_cannot_carry_a_cta_reads_as_having_none():
    """An older plan, or a stand-in, has no `call_to_action` at all."""
    from unittest.mock import MagicMock
    from library.tools.reel_build import cta_range, reel_ranges
    old = MagicMock(spec=["timeline_start", "timeline_end"])
    old.timeline_start, old.timeline_end = 10.0, 40.0
    assert cta_range(old) is None
    assert reel_ranges(old, _tx()) == [(10.0, 40.0)]


def test_a_closer_must_present_two_real_numbers():
    """A bare MagicMock answers every attribute with a truthy mock, and
    `float()` of one is 1.0 - so a stand-in that never mentioned a CTA
    read as closing on the single second 1.00-1.00, and every reel built
    through one raised "the closer runs 1.00-1.00s, under a frame"."""
    from unittest.mock import MagicMock
    from library.tools.reel_build import cta_range, reel_ranges
    stand_in = MagicMock()
    stand_in.timeline_start, stand_in.timeline_end = 10.0, 40.0
    assert cta_range(stand_in) is None
    assert reel_ranges(stand_in, _tx()) == [(10.0, 40.0)]


def test_a_closer_overlapping_its_own_body_is_refused_at_build_time():
    """`validate_proposal` refuses this when the proposal is WRITTEN, but
    the plan is a file the captain edits and `read_proposal` does not
    re-run validation. Without a refusal here the reel plays those
    seconds twice and `reel_time` maps them to the first copy only,
    leaving the second silently uncaptioned."""
    from library.tools.reel_build import ReelBuildError, reel_ranges
    with pytest.raises(ReelBuildError, match="play those seconds twice"):
        reel_ranges(_moment(600.0, 660.0, cta=(610.0, 620.0)), _tx())


def test_a_sub_frame_closer_is_refused_rather_than_dropped():
    from library.tools.reel_build import ReelBuildError, reel_ranges
    with pytest.raises(ReelBuildError, match="under a frame"):
        reel_ranges(_moment(600.0, 660.0, cta=(468.0, 468.01)), _tx())


def test_a_word_ending_exactly_on_a_range_end_is_inside_it():
    """The closing word of every range - and so of every closer - ends
    EXACTLY on the range end that `snap_to_speech` produced."""
    from library.tools.reel_build import reel_time
    ranges = [(600.0, 660.0), (468.0, 471.2)]
    assert reel_time(471.2, ranges) is None, "half-open, for a word START"
    assert reel_time(471.2, ranges, at_end=True) == pytest.approx(63.2)
    assert reel_time(660.0, ranges, at_end=True) == pytest.approx(60.0)


def test_a_range_start_is_still_exclusive_at_the_end_reading():
    """`at_end` must not make a range's START belong to the range before
    it, or a word would be timed into a passage it is not in."""
    from library.tools.reel_build import reel_time
    assert reel_time(0.0, [(0.0, 20.0)], at_end=True) is None
    assert reel_time(0.0, [(0.0, 20.0)]) == 0.0


def test_a_finely_segmented_retake_is_cut():
    """Reel 03: three takes of one sentence, none of them long.

    `MIN_TAKE_SECONDS = 1.5` skipped any pair where either side was
    shorter than that, so a retake WhisperX segmented into sub-second
    pieces was never even scored. Reel 03 of the captain's approved
    nineteen played "search didn't change, the question changed, whoever
    AI understands best gets the answer" three times inside forty
    seconds, and `redundant_takes` returned an empty list for it while
    the proposal's own detector reported the repeat at similarity 1.0.

    Both sides here are well under the old floor and their durations
    agree, which is exactly the shape the floor uniquely blocked.
    """
    line = ("search didn't change the question changed and whoever AI "
            "understands best gets the answer")
    tx = _tx(_seg("Akshita", line, 10.0, 10.9),
             _seg("Akshita", line, 11.0, 12.0, "u2"))

    cuts = redundant_takes(0.0, 60.0, tx)

    assert len(cuts) == 1, cuts
    # The LATER take is kept - a retake exists because the first was
    # flubbed - so the cut removes the first.
    assert cuts[0].dropped_start == 10.0
    assert cuts[0].kept_start == 11.0


def test_a_short_pair_of_different_lines_is_still_left_alone():
    """Removing the floor did not make every short pair a take.

    The content-word test is what separates a retake from two short
    turns, and it still does: back-channel and two different short lines
    share no content vocabulary and are not paired.
    """
    tx = _tx(_seg("Craig", "so what are the seven modules doing", 10.0, 10.8),
             _seg("Craig", "and where does the score come from", 11.0, 11.9,
                  "u2"))
    assert redundant_takes(0.0, 60.0, tx) == []



def test_intra_turn_repetition_is_cut():
    """Reel 03's span 301.2-341.3 with its three takes.
    
    The uncovered case is a single speaker turn that contains its own repetition,
    where the transcriber did not split it into two segments and no second window exists.
    """
    line = "search didn't change the question changed whoever AI understands best gets the answer"
    # Three takes inside a single segment
    seg = _seg("Akshita", f"{line} {line} {line}", 301.2, 341.3)
    
    words_list = line.split()
    words = []
    t = 301.2
    for _ in range(3):
        for w in words_list:
            words.append({"word": w, "start": t, "end": t + 0.5})
            t += 0.5
        t += 5.0  # gap
    seg["words"] = words
    
    tx = _tx(seg)
    cuts = redundant_takes(300.0, 350.0, tx)
    
    # It should cut at least something, meaning the intra-turn repetition is found
    assert len(cuts) > 0
    assert cuts[0].dropped_start == 301.2



# ── Overlay placement is video-only and judged ────────────────────────

class _FakeItem:
    def __init__(self, uid="pool-item-1"):
        self._uid = uid


class _FakePool:
    """Records what the placer asked Resolve to do."""

    def __init__(self, append_result=None):
        self.appended = []
        self.append_result = (
            append_result if append_result is not None else [{"placed": True}])

    class _EmptyFolder:
        """A pool with nothing in it yet.

        `place_overlay_segments` asks the pool for the file BEFORE
        importing it (`reel_build.pool_item_for`), because re-importing
        what is already there is how the field-test project's unplaced
        bin reached 1,210 items. An empty pool means every segment here
        still takes the import path these tests are about.
        """

        def GetClipList(self):
            return []

        def GetSubFolderList(self):
            return []

    def GetRootFolder(self):
        return self._EmptyFolder()

    def ImportMedia(self, paths):
        return [_FakeItem()]

    def AppendToTimeline(self, clips):
        self.appended.extend(clips)
        return self.append_result


class _FakeTimeline:
    def GetUniqueId(self):
        return "timeline-1"

    def GetName(self):
        return "fake reel"


class _FakeProject:
    def __init__(self, timeline):
        self._timeline = timeline
        self.set_calls = 0

    def SetCurrentTimeline(self, timeline):
        self.set_calls += 1

    def GetCurrentTimeline(self):
        return self._timeline


def _segment(path="/renders/vox_test_00.mov", start=9.092, frames=60):
    return {"overlay_path": path, "timeline_start": start,
            "timeline_end": start + frames / 23.976, "total_frames": frames}


def _placer(pool=None, segments=None, track=6):
    timeline = _FakeTimeline()
    project = _FakeProject(timeline)
    pool = pool if pool is not None else _FakePool()
    place_overlay_segments(
        pool, project, timeline, "fake reel", 24000 / 1001,
        segments if segments is not None else [_segment()],
        track, kind="semantic visual", check="F22")
    return pool, project


def test_overlay_append_is_video_only_on_the_named_track():
    """R09's first vox build placed nothing on V6: the append carried
    the overlay's silent audio stream because no mediaType was passed.
    Every other video append in reel_build passes mediaType 1, and so
    must this one - on the track index the caller named, at the reel
    frame the plan computed."""
    pool, _ = _placer()
    assert len(pool.appended) == 1
    clip = pool.appended[0]
    assert clip["mediaType"] == 1
    assert clip["trackIndex"] == 6
    assert clip["startFrame"] == 0
    assert clip["endFrame"] == 60
    assert clip["recordFrame"] == 218  # round(9.092 * 24000/1001)


def test_a_refused_append_is_raised_not_skipped():
    """AppendToTimeline returns nothing on a refusal instead of raising,
    and the old loop carried on - so the record claimed two visuals and
    the timeline carried none until F22 refused the build. A refusal
    raises here, where the cause still points at the append."""
    pool = _FakePool(append_result=[])
    with pytest.raises(ReelBuildError, match="would not place"):
        _placer(pool=pool)


def test_a_refused_import_is_raised_not_skipped():
    """Same shape one call earlier: an overlay file Resolve will not
    import is a refused build, not a reel that quietly loses a visual."""

    class _NoImport(_FakePool):
        def ImportMedia(self, paths):
            return []

    with pytest.raises(ReelBuildError, match="would not import"):
        _placer(pool=_NoImport())
