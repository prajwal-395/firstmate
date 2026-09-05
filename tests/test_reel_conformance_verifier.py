"""Tests for the reel conformance verifier.

Every finding class from the audit (F1 through F11, plus plan quality
gates) is exercised.  Each test plants a specific defect and asserts the
verifier detects it - a verifier that has never been shown a defect it
detects is not evidence.

Fixtures are built from the audit's recorded numbers so the tool is
proven to CATCH each finding class without requiring Resolve.

``tests/test_reel_conformance_verifier.py``.
"""

from __future__ import annotations

import json
import pytest

from library.tools.reel_conformance_verifier import (
    Finding,
    FindingClass,
    PlannedCaption,
    PlannedPlacement,
    ReelPlan,
    ReelResult,
    ReelTimeline,
    TimelineItem,
    VerificationReport,
    check_audio_holes,
    check_boundary_speech,
    check_caption_coverage,
    check_caption_duration,
    check_caption_overlaps,
    check_duplicate_placements,
    check_format,
    check_item_count,
    check_picture_holes,
    check_plan_length,
    check_plan_picture_continuity,
    check_plan_speakers,
    check_short_captions,
    check_subtitle_styling,
    format_findings,
    format_table,
    hash_snapshot_dict,
    verify_reel,
)


# ── Helpers ──────────────────────────────────────────────────────────

FPS = 24000 / 1001  # 23.976 exact


def _item(track_type: str, track_index: int,
          start_frame: int, end_frame: int,
          source_file: str = "/m/a.MXF",
          speaker: str = "Akshita",
          name: str = "clip") -> TimelineItem:
    """Build a synthetic timeline item for testing."""
    return TimelineItem(
        track_type=track_type,
        track_index=track_index,
        start_frame=start_frame,
        end_frame=end_frame,
        duration_frames=end_frame - start_frame,
        source_start_frame=0,
        source_end_frame=end_frame - start_frame,
        source_file=source_file,
        speaker=speaker,
        name=name,
    )


def _placement(track: int, record: float, dur: float,
               speaker: str = "Akshita",
               source_file: str = "/m/a.MXF") -> PlannedPlacement:
    """Build a synthetic planned placement."""
    return PlannedPlacement(
        track_index=track,
        speaker=speaker,
        record_seconds=record,
        source_in=0.0,
        source_out=dur,
        source_file=source_file,
    )


def _caption_card(start: float, end: float, text: str,
                  speaker: str = "Akshita") -> dict:
    """Build a synthetic caption card dict."""
    frames = max(int(round((end - start) * FPS)), 2)
    return {
        "reel_start": start,
        "reel_end": end,
        "text": text,
        "speaker": speaker,
        "frames": frames,
    }


def _plan(reel_name: str = "Reel 01 - test",
          reel_number: int = 1,
          plan_seconds: float = 60.0,
          span_start: float = 0.0,
          span_end: float = 60.0,
          placements: tuple = (),
          captions: tuple = (),
          cuts: tuple = (),
          keep_ranges: tuple = ()) -> ReelPlan:
    """Build a synthetic reel plan."""
    return ReelPlan(
        reel_name=reel_name,
        reel_number=reel_number,
        plan_seconds=plan_seconds,
        plan_frames=round(plan_seconds * FPS, 1),
        span_start=span_start,
        span_end=span_end,
        placements=placements,
        captions=captions,
        cuts=cuts,
        keep_ranges=keep_ranges or ((span_start, span_end),),
    )


def _timeline(reel_name: str = "Reel 01 - test",
              video_items: tuple = (),
              audio_items: tuple = (),
              caption_items: tuple = (),
              total_frames: int = 0,
              markers: dict = None) -> ReelTimeline:
    """Build a synthetic reel timeline."""
    return ReelTimeline(
        reel_name=reel_name,
        fps=FPS,
        total_frames=total_frames or (
            max((i.end_frame for i in video_items), default=0)),
        video_items=video_items,
        audio_items=audio_items,
        caption_items=caption_items,
        markers=markers or {},
    )


# ── F1: Picture holes ───────────────────────────────────────────────

class TestF1PictureHoles:
    """F1 - ENCODING: one-frame black holes at every cut."""

    def test_detects_one_frame_gap(self):
        """Plant a one-frame gap between two contiguous clips.

        The audit found this on all 16 reels: each clip placed one frame
        short, leaving frame 589 empty on Reel 01.
        """
        items = (
            _item("video", 1, 0, 589),      # ends at 589
            _item("video", 1, 590, 1075),    # starts at 590 - gap at 589
        )
        findings = check_picture_holes("Reel 01", items)
        assert len(findings) == 1
        f = findings[0]
        assert f.finding_class == FindingClass.F1
        assert f.severity == "error"
        assert f.detail["gap_frames"] == 1
        assert f.detail["frame"] == 589

    def test_detects_multiple_gaps(self):
        """Plant three gaps matching Reel 01's pattern from the audit.

        Reel 01 has four clips alternating V2/V1/V2/V1, each placed one
        frame short, creating a one-frame gap at frames 589, 1075, 1140.
        Each track has two clips with a gap between them.
        """
        items = (
            # V2 track: Craig clips at [0,589) and [1076,1140) - gap at 589
            _item("video", 2, 0, 589, speaker="Craig"),
            _item("video", 2, 590, 1140, speaker="Craig"),
            # V1 track: Akshita clips at [590,1075) and [1141,1269) - gap at 1075
            _item("video", 1, 0, 1075),
            _item("video", 1, 1076, 1269),
        )
        findings = check_picture_holes("Reel 01", items)
        # Two gaps: one on V2 at 589, one on V1 at 1075
        assert len(findings) == 2
        assert all(f.finding_class == FindingClass.F1 for f in findings)
        gap_frames = {f.detail["frame"] for f in findings}
        assert 589 in gap_frames
        assert 1075 in gap_frames

    def test_no_gap_reports_nothing(self):
        """Contiguous clips produce no findings."""
        items = (
            _item("video", 1, 0, 594),
            _item("video", 1, 594, 1080),
        )
        findings = check_picture_holes("Reel 01", items)
        assert len(findings) == 0

    def test_two_frame_gap(self):
        """The audit found one two-frame gap on reel 02 at frame 141."""
        items = (
            _item("video", 1, 0, 141),
            _item("video", 1, 143, 300),
        )
        findings = check_picture_holes("Reel 02", items)
        assert len(findings) == 1
        assert findings[0].detail["gap_frames"] == 2


# ── F1 audio: Silent holes ──────────────────────────────────────────

class TestF1AudioHoles:
    """F1 - ENCODING: audio holes mirror picture holes."""

    def test_detects_audio_gap(self):
        """Plant a one-frame gap in audio, matching the picture gap."""
        items = (
            _item("audio", 1, 0, 589),
            _item("audio", 1, 590, 1075),
        )
        findings = check_audio_holes("Reel 01", items)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F1
        assert findings[0].detail["type"] == "audio"

    def test_no_audio_gap(self):
        items = (
            _item("audio", 1, 0, 594),
            _item("audio", 1, 594, 1080),
        )
        findings = check_audio_holes("Reel 01", items)
        assert len(findings) == 0


# ── F2: Caption duration ────────────────────────────────────────────

class TestF2CaptionDuration:
    """F2 - ENCODING: caption cards placed one frame short."""

    def test_detects_one_frame_short(self):
        """Plant a caption that is one frame shorter than planned.

        The audit found 567 of 575 cards had this: placed duration was
        planned frames minus one.
        """
        planned = (PlannedCaption(
            start_seconds=1.0, end_seconds=2.0,
            text="hello world", speaker="Akshita", frames=24),)
        actual = (_item("video", 3, 24, 47, name="akshita_01"),)
        # actual duration = 47 - 24 = 23, planned = 24, delta = -1
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F2
        assert findings[0].detail["delta"] == -1

    def test_exact_match_reports_nothing(self):
        """Caption placed at exactly planned duration produces no finding."""
        planned = (PlannedCaption(
            start_seconds=1.0, end_seconds=2.0,
            text="hello world", speaker="Akshita", frames=24),)
        actual = (_item("video", 3, 24, 48, name="akshita_01"),)
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        assert len(findings) == 0


# ── F3: Master-inherited holes ──────────────────────────────────────

class TestF3MasterHoles:
    """F3 - PLANNING: holes inherited from the master (warning, not error)."""

    def test_master_hole_is_warning(self):
        """Plant a large hole matching a known master hole.

        The audit found Reel 16 has a 100-frame hole at 29.2s that is
        faithfully reproduced from the master.  This should be a WARNING,
        not an error - it is the plan's fault.
        """
        items = (
            _item("video", 1, 0, 700),
            _item("video", 1, 800, 1858),   # 100-frame gap at 700
        )
        master_holes = [{"frame": 60512, "length": 99}]
        findings = check_picture_holes("Reel 16", items, master_holes)
        # The 100-frame gap approximates the 99-frame master hole
        assert len(findings) == 1
        f = findings[0]
        assert f.finding_class == FindingClass.F3
        assert f.severity == "warning"
        assert f.detail["inherited"] is True

    def test_small_hole_not_attributed_to_master(self):
        """A one-frame gap is too small to be the 99-frame master hole."""
        items = (
            _item("video", 1, 0, 589),
            _item("video", 1, 590, 1075),
        )
        master_holes = [{"frame": 60512, "length": 99}]
        findings = check_picture_holes("Reel 01", items, master_holes)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F1  # not F3
        assert findings[0].severity == "error"


# ── F4: Item count and per-speaker duration ─────────────────────────

class TestF4ItemCount:
    """F4 - ENCODING: clips silently dropped."""

    def test_detects_missing_clip(self):
        """Plant a reel where one clip is missing.

        The audit found Reel 09 planned 7 items but placed only 6.
        """
        planned = (
            _placement(1, 0.0, 25.0, "Akshita"),
            _placement(2, 0.0, 13.0, "Craig"),
            _placement(1, 13.0, 12.0, "Akshita"),
            _placement(2, 13.0, 12.0, "Craig"),
            _placement(1, 25.0, 13.0, "Akshita"),
            _placement(2, 25.0, 12.7, "Craig"),
            _placement(1, 38.0, 12.8, "Akshita"),
        )
        actual = (
            _item("video", 1, 0, int(25 * FPS)),
            _item("video", 2, 0, int(13 * FPS), speaker="Craig"),
            _item("video", 1, int(13 * FPS), int(25 * FPS)),
            # Craig clip dropped at record 13.0
            _item("video", 1, int(25 * FPS), int(38 * FPS)),
            _item("video", 2, int(25 * FPS), int(37.7 * FPS), speaker="Craig"),
            _item("video", 1, int(38 * FPS), int(50.8 * FPS)),
        )
        findings = check_item_count("Reel 09", planned, actual, FPS)
        # Should find item count mismatch
        count_findings = [f for f in findings
                          if "planned" in f.message and "picture items" in f.message]
        assert len(count_findings) == 1
        assert count_findings[0].detail["expected"] == 7
        assert count_findings[0].detail["actual"] == 6

    def test_detects_speaker_duration_mismatch(self):
        """Plant a reel where Craig is missing significant duration.

        The audit found Reel 09 Craig planned 25.69s but got 13.43s.
        """
        planned = (
            _placement(1, 0.0, 50.0, "Akshita"),
            _placement(2, 0.0, 25.0, "Craig"),
        )
        # Craig is only half his planned duration
        actual = (
            _item("video", 1, 0, int(50 * FPS)),
            _item("video", 2, 0, int(13 * FPS), speaker="Craig"),
        )
        findings = check_item_count("Reel 09", planned, actual, FPS)
        craig_findings = [f for f in findings if "Craig" in f.message]
        assert len(craig_findings) >= 1
        assert craig_findings[0].detail["speaker"] == "Craig"

    def test_matching_counts_no_findings(self):
        """Equal item counts and durations produce no findings."""
        planned = (
            _placement(1, 0.0, 25.0, "Akshita"),
            _placement(2, 0.0, 25.0, "Craig"),
        )
        actual = (
            _item("video", 1, 0, int(25 * FPS)),
            _item("video", 2, 0, int(25 * FPS), speaker="Craig"),
        )
        findings = check_item_count("Reel 01", planned, actual, FPS)
        assert len(findings) == 0


# ── F5: Caption coverage ────────────────────────────────────────────

class TestF5CaptionCoverage:
    """F5 - PLANNING: uncaptioned speech from straddling segments."""

    def test_detects_straddling_uncaptioned_speech(self):
        """Plant a straddling segment (no resolve_item_id) with no caption.

        The audit found 66.7s of speech from straddling segments with no
        caption over it.
        """
        segments = [
            # Bound segment - has caption coverage
            {"timeline_start": 0.0, "timeline_end": 10.0,
             "resolve_item_id": "uid-1", "speaker": "Akshita",
             "text": "hello world"},
            # Straddling segment - NO resolve_item_id, NO caption
            {"timeline_start": 10.0, "timeline_end": 18.0,
             "resolve_item_id": None, "speaker": "Craig",
             "text": "this straddles a cut"},
        ]
        # Only one caption covering the bound segment
        captions = [_caption_card(0.0, 10.0, "hello world")]
        keep_ranges = [(0.0, 20.0)]

        findings = check_caption_coverage(
            "Reel 02", segments, captions, keep_ranges, FPS)
        assert len(findings) >= 1
        f5 = [f for f in findings if f.finding_class == FindingClass.F5]
        assert len(f5) == 1
        assert f5[0].detail["straddling_seconds"] > 7.0

    def test_fully_captioned_no_findings(self):
        """All speech covered by captions produces no findings."""
        segments = [
            {"timeline_start": 0.0, "timeline_end": 10.0,
             "resolve_item_id": "uid-1", "speaker": "Akshita",
             "text": "hello world"},
        ]
        captions = [_caption_card(0.0, 10.0, "hello world")]
        keep_ranges = [(0.0, 10.0)]

        findings = check_caption_coverage(
            "Reel 01", segments, captions, keep_ranges, FPS)
        assert len(findings) == 0


# ── F6: Caption overlap ─────────────────────────────────────────────

class TestF6CaptionOverlap:
    """F6 - PLANNING: overlapping caption cards on one track."""

    def test_detects_overlapping_cards(self):
        """Plant two cards that overlap in time.

        The audit found 15 overlapping pairs - mic bleed captioned under
        both speakers on the same track.
        """
        cards = [
            _caption_card(18.7, 20.1, "and a lot of big companies",
                          speaker="Craig"),
            _caption_card(18.7, 20.1, "and a lot of big companies",
                          speaker="Akshita"),  # same time = overlap
        ]
        findings = check_caption_overlaps("Reel 09", cards, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F6
        assert findings[0].detail["overlap_frames"] > 0

    def test_non_overlapping_no_findings(self):
        """Sequential cards produce no findings."""
        cards = [
            _caption_card(0.0, 1.0, "first card"),
            _caption_card(1.0, 2.0, "second card"),
        ]
        findings = check_caption_overlaps("Reel 01", cards, FPS)
        assert len(findings) == 0


# ── F7: Short caption cards ─────────────────────────────────────────

class TestF7ShortCaptions:
    """F7 - PLANNING: caption cards shorter than 0.5s minimum."""

    def test_detects_very_short_card(self):
        """Plant a 2-frame card - about 83ms at 23.976fps.

        The audit found 29 cards under 0.5s, 7 under 3 frames.
        """
        cards = [
            _caption_card(5.0, 5.083, "short"),  # ~2 frames
        ]
        findings = check_short_captions("Reel 03", cards, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F7
        assert findings[0].detail["duration_seconds"] < 0.5

    def test_normal_duration_no_findings(self):
        """A 1-second card is above the minimum."""
        cards = [
            _caption_card(5.0, 6.0, "normal duration"),
        ]
        findings = check_short_captions("Reel 01", cards, FPS)
        assert len(findings) == 0


# ── F8: Boundary speech ─────────────────────────────────────────────

class TestF8BoundarySpeech:
    """F8 - PLANNING: reel boundaries cut through invisible speech."""

    def test_detects_end_boundary_cutting_speech(self):
        """Plant a straddling segment cut by the reel END boundary.

        The audit found 11 boundaries that cut through real sentences
        that snap_to_speech cannot see (straddling segments).
        """
        segments = [
            # Straddling segment - no resolve_item_id
            {"timeline_start": 155.81, "timeline_end": 179.97,
             "resolve_item_id": None, "speaker": "Craig",
             "text": "bunch of terms blogs and whatnot"},
        ]
        # Reel END at 179.46 cuts through segment [155.81, 179.97]
        findings = check_boundary_speech(
            "Reel 02", span_start=112.0, span_end=179.46,
            transcript_segments=segments)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F8
        assert findings[0].detail["boundary"] == "end"

    def test_detects_start_boundary_cutting_speech(self):
        """Plant a straddling segment cut by the reel START boundary."""
        segments = [
            {"timeline_start": 461.26, "timeline_end": 477.18,
             "resolve_item_id": None, "speaker": "Akshita",
             "text": "Yeah so ranking number one on Google"},
        ]
        # Reel START at 463.44 cuts through segment [461.26, 477.18]
        findings = check_boundary_speech(
            "Reel 04", span_start=463.44, span_end=546.25,
            transcript_segments=segments)
        assert len(findings) == 1
        assert findings[0].detail["boundary"] == "start"

    def test_bound_segment_not_reported(self):
        """A bound segment (has resolve_item_id) should not be F8."""
        segments = [
            {"timeline_start": 155.81, "timeline_end": 179.97,
             "resolve_item_id": "uid-1", "speaker": "Craig",
             "text": "this is bound"},
        ]
        findings = check_boundary_speech(
            "Reel 02", span_start=112.0, span_end=179.46,
            transcript_segments=segments)
        assert len(findings) == 0


# ── F9: Duplicate placements ────────────────────────────────────────

class TestF9DuplicatePlacements:
    """F9 - ENCODING: duplicate items at the same record position.

    Found 2026-09-04: a placement loop that runs once per clip PER CLIP
    produces N*N placements.  A 4-clip reel gets 16 items at 4 unique
    positions.  build_reel_timeline has zero tests, so this verifier is
    the only thing that catches it.
    """

    def test_detects_4x4_duplicate_placements(self):
        """Plant the exact N*N bug: 4 clips produce 16 items.

        A placement loop that iterates `for clip in clips: for clip in
        clips:` places each clip at every position, giving 4*4=16 items
        at 4 record positions.  Each position has 4 duplicates.
        """
        # 4 unique positions, each with 4 items = 16 total
        items = []
        positions = [0, int(10 * FPS), int(20 * FPS), int(30 * FPS)]
        speakers = ["Akshita", "Craig", "Akshita", "Craig"]
        durations = [int(10 * FPS), int(10 * FPS), int(10 * FPS),
                     int(10 * FPS)]
        for pos, dur in zip(positions, durations):
            for speaker in speakers:  # the bug: inner loop over all clips
                items.append(_item(
                    "video", 1, pos, pos + dur,
                    speaker=speaker, name=f"clip-at-{pos}"))

        findings = check_duplicate_placements("Reel 01", tuple(items), FPS)

        # Should find duplicates at all 4 positions
        dup_findings = [f for f in findings
                        if f.detail.get("count", 0) > 1]
        assert len(dup_findings) == 4
        for f in dup_findings:
            assert f.finding_class == FindingClass.F9
            assert f.severity == "error"
            assert f.detail["count"] == 4

        # Should also detect the N*N signature
        signature_findings = [f for f in findings
                              if "placement loop" in f.message]
        assert len(signature_findings) == 1
        assert signature_findings[0].detail["total_items"] == 16
        assert signature_findings[0].detail["unique_positions"] == 4

    def test_detects_simple_duplicate(self):
        """Two items at the same frame on the same track is a duplicate."""
        items = (
            _item("video", 1, 0, int(10 * FPS), name="clip-a"),
            _item("video", 1, 0, int(10 * FPS), name="clip-b"),  # dup
        )
        findings = check_duplicate_placements("Reel 03", items, FPS)
        dup_findings = [f for f in findings
                        if f.detail.get("count", 0) > 1]
        assert len(dup_findings) == 1
        assert dup_findings[0].detail["count"] == 2
        assert dup_findings[0].detail["frame"] == 0

    def test_different_tracks_same_position_not_duplicate(self):
        """V1 and V2 at the same frame is normal (two speakers), not F9."""
        items = (
            _item("video", 1, 0, int(25 * FPS), speaker="Akshita"),
            _item("video", 2, 0, int(25 * FPS), speaker="Craig"),
        )
        findings = check_duplicate_placements("Reel 01", items, FPS)
        assert len(findings) == 0

    def test_no_duplicates_no_findings(self):
        """Distinct items at distinct positions produce no findings."""
        items = (
            _item("video", 1, 0, int(10 * FPS)),
            _item("video", 1, int(10 * FPS), int(20 * FPS)),
            _item("video", 2, 0, int(10 * FPS), speaker="Craig"),
            _item("video", 2, int(10 * FPS), int(20 * FPS), speaker="Craig"),
        )
        findings = check_duplicate_placements("Reel 01", items, FPS)
        assert len(findings) == 0

# ── F10: Format mismatch ────────────────────────────────────────────

class TestF10Format:
    """F10 - ENCODING: timeline format must be 1080x1920 at master fps.

    This project has a documented history of a correct vertical timeline
    rendering out landscape while every structural check passed.
    """

    def test_detects_landscape_resolution(self):
        """1920x1080 (landscape) is wrong for a vertical short."""
        findings = check_format("Reel 01", 1920, 1080, FPS, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F10
        assert findings[0].severity == "error"
        assert "1920x1080" in findings[0].message
        assert "1080x1920" in findings[0].message

    def test_detects_wrong_width(self):
        """Width mismatch alone is a defect."""
        findings = check_format("Reel 02", 720, 1920, FPS, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F10
        assert "720x1920" in findings[0].message

    def test_detects_fps_mismatch(self):
        """A reel at 30fps when the master is 23.976 stutters."""
        findings = check_format("Reel 03", 1080, 1920, 30.0, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F10
        assert "fps" in findings[0].message.lower()

    def test_detects_both_resolution_and_fps(self):
        """Both wrong = two findings."""
        findings = check_format("Reel 04", 1920, 1080, 30.0, FPS)
        assert len(findings) == 2
        classes = {f.detail.get("actual_width") or f.detail.get("actual_fps")
                   for f in findings}
        # One about resolution, one about fps
        assert all(f.finding_class == FindingClass.F10 for f in findings)

    def test_correct_format_no_findings(self):
        """1080x1920 at the master's fps is correct."""
        findings = check_format("Reel 01", 1080, 1920, FPS, FPS)
        assert len(findings) == 0

    def test_correct_at_exact_ntsc(self):
        """Both at 24000/1001 exactly is correct."""
        findings = check_format("Reel 01", 1080, 1920,
                                24000 / 1001, 24000 / 1001)
        assert len(findings) == 0


# ── F11: Subtitle styling ───────────────────────────────────────────

class TestF11SubtitleStyling:
    """F11 - ENCODING: per-speaker subtitle styling is the diarization
    signal.  These tests check the OUTPUT (cards on the timeline), not
    the styling configuration.

    The segment naming convention puts the speaker in the filename:
      sub_<timeline>_<speaker>_<block>_<span>_<digest>.mov

    A check that reads the config instead of the output would pass when
    the config holds two distinct styles but the builder applied ONE of
    them to every card.  That is the precise failure this check exists
    to catch.
    """

    def _akshita_cap(self, start, end, idx=0):
        """A caption card attributed to Akshita via filename."""
        return _item(
            "video", 3, start, end,
            name=f"sub_synced_akshita_body{idx}_0-1000_abc123.mov",
            source_file=f"/overlays/sub_synced_akshita_body{idx}_0-1000_abc123.mov",
        )

    def _craig_cap(self, start, end, idx=0):
        """A caption card attributed to Craig via filename."""
        return _item(
            "video", 3, start, end,
            name=f"sub_synced_craig_body{idx}_1000-2000_def456.mov",
            source_file=f"/overlays/sub_synced_craig_body{idx}_1000-2000_def456.mov",
        )

    def test_detects_all_cards_same_speaker_with_two_video_speakers(self):
        """Builder applied one speaker's style to ALL cards.  Video has
        two speakers, but every caption is attributed to Akshita.  This
        is the core defect: config might say two styles, but the OUTPUT
        shows only one."""
        captions = (
            self._akshita_cap(0, int(3 * FPS), 0),
            self._akshita_cap(int(3 * FPS), int(6 * FPS), 1),
            self._akshita_cap(int(6 * FPS), int(9 * FPS), 2),
        )
        videos = (
            _item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
            _item("video", 2, 0, int(10 * FPS), speaker="Craig"),
        )
        findings = check_subtitle_styling("Reel 01", captions, videos)
        assert len(findings) >= 1
        f11s = [f for f in findings if f.finding_class == FindingClass.F11]
        assert any("diarization" in f.message.lower() for f in f11s)
        assert all(f.severity == "error" for f in f11s)

    def test_detects_unattributed_caption_cards(self):
        """Caption cards with no speaker in their filename cannot be
        verified for per-speaker styling.  The check must SAY SO rather
        than returning clean."""
        captions = (
            _item("video", 3, 0, int(5 * FPS),
                  name="caption_block_0.mov",
                  source_file="/overlays/caption_block_0.mov"),
            _item("video", 3, int(5 * FPS), int(10 * FPS),
                  name="caption_block_1.mov",
                  source_file="/overlays/caption_block_1.mov"),
        )
        videos = (
            _item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
        )
        findings = check_subtitle_styling("Reel 01", captions, videos)
        assert len(findings) >= 1
        assert any(f.finding_class == FindingClass.F11 and
                   "no speaker attribution" in f.message
                   for f in findings)

    def test_detects_shared_overlay_source(self):
        """Two speakers' cards use the SAME overlay file.  Even though
        the filenames carry different speaker slugs, sharing a source
        file means identical rendering."""
        shared_source = "/overlays/sub_synced_shared_body0_0-1000_abc123.mov"
        captions = (
            _item("video", 3, 0, int(5 * FPS),
                  name="sub_synced_akshita_body0_0-1000_abc123.mov",
                  source_file=shared_source),
            _item("video", 3, int(5 * FPS), int(10 * FPS),
                  name="sub_synced_craig_body0_1000-2000_def456.mov",
                  source_file=shared_source),
        )
        videos = (
            _item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
            _item("video", 2, 0, int(10 * FPS), speaker="Craig"),
        )
        findings = check_subtitle_styling("Reel 01", captions, videos)
        shared_findings = [f for f in findings
                          if "share" in f.message.lower()]
        assert len(shared_findings) >= 1
        assert shared_findings[0].severity == "error"

    def test_two_speakers_different_overlays_no_findings(self):
        """Two speakers with distinct overlay files is correct."""
        captions = (
            self._akshita_cap(0, int(5 * FPS), 0),
            self._craig_cap(int(5 * FPS), int(10 * FPS), 0),
        )
        videos = (
            _item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
            _item("video", 2, 0, int(10 * FPS), speaker="Craig"),
        )
        findings = check_subtitle_styling("Reel 01", captions, videos)
        assert len(findings) == 0

    def test_no_captions_on_timeline_no_f11(self):
        """No caption items at all is not an F11 styling defect - it is
        a coverage gap (F5) that other checks catch."""
        videos = (
            _item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
        )
        findings = check_subtitle_styling("Reel 01", (), videos)
        assert len(findings) == 0

    def test_single_speaker_video_single_speaker_captions_clean(self):
        """A reel with only one speaker on video and one speaker's
        captions is not a diarization failure - there is only one
        speaker to style."""
        captions = (
            self._akshita_cap(0, int(5 * FPS), 0),
            self._akshita_cap(int(5 * FPS), int(10 * FPS), 1),
        )
        videos = (
            _item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
        )
        findings = check_subtitle_styling("Reel 01", captions, videos)
        assert len(findings) == 0



# ── Plan quality: Length ─────────────────────────────────────────────

class TestPlanQualityLength:
    """Plan quality gate: reel length within 45-90s guidance."""

    def test_too_short(self):
        """A 30s reel is under the 45s minimum."""
        findings = check_plan_length("Reel 01", 30.0)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.PQ_LENGTH
        assert "under" in findings[0].message

    def test_too_long(self):
        """A 120s reel is over the 90s maximum."""
        findings = check_plan_length("Reel 01", 120.0)
        assert len(findings) == 1
        assert "over" in findings[0].message

    def test_within_range_no_findings(self):
        """A 60s reel is within the guidance."""
        findings = check_plan_length("Reel 01", 60.0)
        assert len(findings) == 0


# ── Plan quality: Speakers ───────────────────────────────────────────

class TestPlanQualitySpeakers:
    """Plan quality gate: both speakers with real turns."""

    def test_single_speaker(self):
        """Only one speaker should trigger a finding."""
        placements = (
            _placement(1, 0.0, 50.0, "Akshita"),
        )
        findings = check_plan_speakers("Reel 01", placements)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.PQ_SPEAKERS

    def test_two_speakers_both_real(self):
        """Both speakers with real turns should pass."""
        placements = (
            _placement(1, 0.0, 25.0, "Akshita"),
            _placement(2, 0.0, 25.0, "Craig"),
        )
        findings = check_plan_speakers("Reel 01", placements)
        assert len(findings) == 0

    def test_speaker_too_short(self):
        """A speaker with < 2s is not a real turn."""
        placements = (
            _placement(1, 0.0, 50.0, "Akshita"),
            _placement(2, 0.0, 1.0, "Craig"),
        )
        findings = check_plan_speakers("Reel 01", placements)
        assert len(findings) == 1


# ── Plan quality: Picture continuity ─────────────────────────────────

class TestPlanQualityPicture:
    """Plan quality gate: no picture holes in the plan span."""

    def test_detects_gap_in_master(self):
        """Plant a gap in the master coverage inside the reel span."""
        master_items = [
            {"timeline_start": 0.0, "timeline_end": 30.0,
             "track_index": 1},
            {"timeline_start": 34.0, "timeline_end": 60.0,
             "track_index": 1},
        ]
        findings = check_plan_picture_continuity(
            "Reel 16", 0.0, 60.0, master_items)
        assert len(findings) >= 1
        assert findings[0].finding_class == FindingClass.PQ_PICTURE
        assert findings[0].detail["gap_frames"] > 0

    def test_continuous_no_findings(self):
        """Continuous master coverage produces no findings."""
        master_items = [
            {"timeline_start": 0.0, "timeline_end": 30.0,
             "track_index": 1},
            {"timeline_start": 30.0, "timeline_end": 60.0,
             "track_index": 1},
        ]
        findings = check_plan_picture_continuity(
            "Reel 01", 0.0, 60.0, master_items)
        assert len(findings) == 0


# ── Integration: verify_reel ─────────────────────────────────────────

class TestVerifyReel:
    """End-to-end test of verify_reel with planted defects."""

    def test_clean_reel_passes(self):
        """A reel with no defects should produce no errors."""
        plan = _plan(
            placements=(
                _placement(1, 0.0, 25.0, "Akshita"),
                _placement(2, 0.0, 25.0, "Craig"),
            ),
        )
        timeline = _timeline(
            video_items=(
                _item("video", 1, 0, int(25 * FPS)),
                _item("video", 2, 0, int(25 * FPS), speaker="Craig"),
            ),
            audio_items=(
                _item("audio", 1, 0, int(25 * FPS)),
                _item("audio", 2, 0, int(25 * FPS), speaker="Craig"),
            ),
        )
        result = verify_reel(plan, timeline)
        errors = [f for f in result.findings if f.severity == "error"]
        assert len(errors) == 0

    def test_reel_with_holes_reports_f1(self):
        """A reel with one-frame gaps reports F1."""
        plan = _plan(
            placements=(
                _placement(1, 0.0, 24.6, "Akshita"),
                _placement(1, 24.6, 25.0, "Akshita"),
            ),
        )
        timeline = _timeline(
            video_items=(
                _item("video", 1, 0, 589),
                _item("video", 1, 590, 1075),    # gap at 589
            ),
            audio_items=(),
        )
        result = verify_reel(plan, timeline)
        f1_findings = [f for f in result.findings
                       if f.finding_class == FindingClass.F1]
        assert len(f1_findings) >= 1

    def test_reel_with_multiple_defects(self):
        """A reel with holes AND missing clips reports both F1 and F4."""
        plan = _plan(
            placements=(
                _placement(1, 0.0, 25.0, "Akshita"),
                _placement(2, 0.0, 25.0, "Craig"),
                _placement(1, 25.0, 25.0, "Akshita"),
            ),
        )
        timeline = _timeline(
            video_items=(
                _item("video", 1, 0, 589),
                _item("video", 1, 590, 1075),  # gap at 589, Craig missing
            ),
            audio_items=(),
        )
        result = verify_reel(plan, timeline)
        classes = {f.finding_class for f in result.findings
                   if f.severity == "error"}
        assert FindingClass.F1 in classes
        assert FindingClass.F4 in classes


# ── Output formats ───────────────────────────────────────────────────

class TestOutputFormats:
    """Test human-readable and machine-readable output."""

    def _make_report(self) -> VerificationReport:
        plan = _plan(
            placements=(
                _placement(1, 0.0, 25.0, "Akshita"),
                _placement(2, 0.0, 25.0, "Craig"),
            ),
        )
        timeline = _timeline(
            video_items=(
                _item("video", 1, 0, 589),
                _item("video", 1, 590, 1075),
            ),
            audio_items=(),
        )
        result = verify_reel(plan, timeline)
        return VerificationReport(
            project_name="Podcast (field test)",
            master_timeline="GEO Podcast - Synced",
            reel_results=[result],
            read_only_proof={"before": "abc123", "after": "abc123",
                             "identical": True},
            plan_source="DERIVED from master (no --plan provided)",
        )

    def test_json_output_serializable(self):
        """The JSON output must be valid JSON and include plan_source."""
        report = self._make_report()
        data = report.as_dict()
        serialized = json.dumps(data, indent=2)
        loaded = json.loads(serialized)
        assert loaded["project"] == "Podcast (field test)"
        assert "summary" in loaded
        assert isinstance(loaded["reels"], list)
        assert loaded["plan_source"] == "DERIVED from master (no --plan provided)"

    def test_table_output_readable(self):
        """The table output must contain the reel name and numbers."""
        report = self._make_report()
        table = format_table(report)
        assert "Reel 01" in table
        assert "TOTAL" in table

    def test_findings_output_by_class(self):
        """Findings output must group by class."""
        report = self._make_report()
        output = format_findings(report)
        assert "F1" in output or "F4" in output


# ── Read-only proof ──────────────────────────────────────────────────

class TestReadOnlyProof:
    """Test the read-only proof mechanism."""

    def test_identical_hashes(self):
        """Same data produces same hash."""
        data = {"clips": [{"start": 0, "end": 100}]}
        h1 = hash_snapshot_dict(data)
        h2 = hash_snapshot_dict(data)
        assert h1 == h2

    def test_different_data_different_hash(self):
        """Different data produces different hash."""
        d1 = {"clips": [{"start": 0, "end": 100}]}
        d2 = {"clips": [{"start": 0, "end": 101}]}
        assert hash_snapshot_dict(d1) != hash_snapshot_dict(d2)


# ── CLI ──────────────────────────────────────────────────────────────

class TestCLI:
    """Test the CLI entry point."""

    def test_help_exits_zero(self):
        """--help should work without Resolve."""
        from library.tools.reel_conformance_verifier import main
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_missing_required_args_exits_nonzero(self):
        """No arguments exits with error because --project and --master
        are required.  A verifier that silently passes when it has
        nothing to check is worse than no verifier."""
        from library.tools.reel_conformance_verifier import main
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code != 0

    def test_requires_resolve_connection(self):
        """Calling with valid args but no Resolve exits 2."""
        from library.tools.reel_conformance_verifier import run_verification
        import io
        out = io.StringIO()
        code = run_verification(
            project_name="Nonexistent Project",
            master_name="Nonexistent Timeline",
            out=out,
        )
        # Should exit 2 (fatal) because Resolve is not running in CI
        assert code == 2

