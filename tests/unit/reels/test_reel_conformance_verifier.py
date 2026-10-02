"""Tests for the reel conformance verifier.

Every finding class from the audit (F1 through F11, plus plan quality
gates) is exercised.  Each test plants a specific defect and asserts the
verifier detects it - a verifier that has never been shown a defect it
detects is not evidence.

Fixtures are built from the audit's recorded numbers so the tool is
proven to CATCH each finding class without requiring Resolve.

``tests/unit/reels/test_reel_conformance_verifier.py``.
"""
from __future__ import annotations
import json
import pytest
from library.tools.reel_conformance_verifier import (
    check_caption_hangs,
    check_caption_slugs,
    check_mixed_speakers,
    check_placed_caption_hangs,
    FindingClass,
    PlannedCaption,
    PlannedPlacement,
    ReelPlan,
    ReelTimeline,
    TimelineItem,
    check_audio_holes,
    check_boundary_speech,
    check_caption_coverage,
    check_caption_duration,
    check_caption_overlaps,
    check_caption_reference,
    check_duplicate_placements,
    check_format,
    check_item_count,
    check_picture_holes,
    check_plan_length,
    check_plan_picture_continuity,
    check_plan_speakers,
    check_short_captions,
    check_subtitle_styling,
    verify_reel,
)
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from library.tools import operations, reel_look
from library.tools import reel_conformance_verifier as verifier
from library.tools.reel_proposal import Approval, ReelMoment, write_proposal
from library.tools.reel_quality_bar import BarReport
from library.tools.timeline_ingest import TimelineClip, TimelineSnapshot
from library.tools.timeline_transcript import transcript_path
import shutil
import subprocess
from library.tools.reel_build import OffsetRefused, verify_cover_clip
from library.tools import reel_semantic_visual as sem
from library.tools.reel_conformance_verifier import (
    check_semantic_visuals,
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


def _row(start: float, end: float, speaker: str = "Craig",
         text: str = "some speech", item_id=None,
         words: tuple | None = None,
         speaking: tuple | None = None) -> dict:
    """A transcript row, with the word timings the checks measure.

    `speaking` is the stretches this speaker is actually audible, as
    (start, end) pairs; each becomes one word.  Omitted, the row speaks
    for its whole envelope, which is the shape a fixture wants when the
    envelope and the speech are the same thing.

    `words=()` builds a row with NO word timings at all - the shape a
    transcript takes when alignment produced none, and the case both F5
    and F8 must fall back on and REPORT rather than skip.
    """
    if words is None:
        spans = speaking if speaking is not None else ((start, end),)
        words = tuple({"word": f"w{i}", "start": a, "end": b, "timed": True}
                      for i, (a, b) in enumerate(spans))
    return {
        "timeline_start": start,
        "timeline_end": end,
        "resolve_item_id": item_id,
        "speaker": speaker,
        "text": text,
        "words": list(words),
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

        # ... and a V1 gap another track covers completely is no hole.
        covered = (
            _item("video", 1, 0, 100),
            _item("video", 1, 200, 300),
            _item("video", 2, 80, 220),
        )
        assert check_picture_holes("Reel 02", covered) == []


# ── F1 audio: Silent holes ──────────────────────────────────────────

class TestF1AudioHoles:
    """F1 - ENCODING: audio holes mirror picture holes."""

    def test_detects_audio_gap(self):
        """Plant a one-frame gap in audio."""
        items = (
            _item("audio", 1, 0, 589),
            _item("audio", 1, 590, 1075),
        )
        findings = check_audio_holes("Reel 01", items)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F1
        assert findings[0].detail["type"] == "audio"
        assert findings[0].detail["gap_frames"] == 1
        assert findings[0].detail["track"] == 1
        assert "A1 has a 1-frame silent hole" in findings[0].message


# ── F2: Caption duration ────────────────────────────────────────────

class TestF2CaptionDuration:
    """F2 - ENCODING: caption cards placed one frame short."""


    def test_pairs_on_start_frame_not_list_position(self):
        """F2 pairs a card with its placed item on START FRAME, never list position.

        Card 2 is planned and never placed: index pairing would invent a delta on
        card 3. History: docs/evidence/reel_conformance_verifier.md.
        """
        planned = (
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="first", speaker="Akshita", frames=24,
                           block_position="body_1"),
            PlannedCaption(start_seconds=2.0, end_seconds=3.0,
                           text="never placed", speaker="Akshita", frames=24,
                           block_position="body_2"),
            PlannedCaption(start_seconds=3.0, end_seconds=4.5,
                           text="third", speaker="Craig", frames=36,
                           block_position="body_3"),
        )
        actual = (
            _item("video", 2, 24, 48, name="a"),    # 24f at frame 24
            _item("video", 2, 72, 108, name="c"),   # 36f at frame 72
        )
        findings = check_caption_duration("Reel 01", planned, actual, FPS)

        f2 = [f for f in findings if f.finding_class == FindingClass.F2]
        f14 = [f for f in findings if f.finding_class == FindingClass.F14]
        assert f2 == [], (
            "cards 1 and 3 are placed at exactly their planned length; "
            "index pairing invents a delta on card 3")
        assert len(f14) == 1, "the unplaced card is the finding"
        assert f14[0].detail["text"] == "never placed"

    def test_an_item_is_claimed_once(self):
        """Two cards cannot both pair with the same placed item."""
        planned = (
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="one", speaker="Akshita", frames=24,
                           block_position="body_1"),
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="two", speaker="Akshita", frames=24,
                           block_position="body_2"),
        )
        actual = (_item("video", 2, 24, 48, name="a"),)
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        f14 = [f for f in findings if f.finding_class == FindingClass.F14]
        assert len(f14) == 1


class TestF2SegmentGranularity:
    """F2/F14 pair at the SEGMENT granularity the builder places (one item per
    block), not per card. History: docs/evidence/reel_conformance_verifier.md.
    """

    def _block_item(self, start_s: float, end_s: float,
                    name: str = "seg") -> TimelineItem:
        """One placed item exactly as the builder places a block segment:
        record at the block's first card start, spanning to its last."""
        start = int(round(start_s * FPS))
        end = int(round(end_s * FPS))
        return _item("video", 3, start, end, name=name)

    def test_fresh_build_with_multi_card_blocks_is_clean(self):
        """The rebuild's shape: two cards in one block, one in the next,
        placed as two block-spanning items, draws nothing - and a block
        placed short still draws its F2."""
        planned = (
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="first card", speaker="Akshita",
                           frames=round(1.0 * FPS),
                           block_position="body_1"),
            PlannedCaption(start_seconds=2.0, end_seconds=3.5,
                           text="second card", speaker="Akshita",
                           frames=round(1.5 * FPS),
                           block_position="body_1"),
            PlannedCaption(start_seconds=5.0, end_seconds=6.0,
                           text="third card", speaker="Akshita",
                           frames=round(1.0 * FPS),
                           block_position="body_2"),
        )
        actual = (
            self._block_item(1.0, 3.5),
            self._block_item(5.0, 6.0),
        )
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        assert findings == [], (
            "a faithful per-block placement must draw no finding; got "
            f"{[(f.finding_class, f.message) for f in findings]}")

        # Segment granularity must not swallow a real defect: block 1
        # placed half a second short still fails, with the segment delta.
        short = (self._block_item(1.0, 3.0),)
        f2 = [f for f in check_caption_duration("Reel 01", planned[:2],
                                                short, FPS)
              if f.finding_class == FindingClass.F2]
        assert len(f2) == 1
        assert f2[0].detail["delta"] == -round(0.5 * FPS)
        assert f2[0].detail["card_count"] == 2


class TestF2AbuttingBlocks:
    """F2 on blocks that abut EXACTLY in seconds (reel 07): spans are rounded per
    edge, so abutting blocks abut and the gate stays exact.
    History: docs/evidence/reel_conformance_verifier.md.
    """

    # Reel 07 block 22/23 boundary, full precision from the props files.
    AK22 = (60.125, 62.374)
    AK23 = (62.374, 63.809)

    def _planned(self):
        return (
            PlannedCaption(start_seconds=60.125, end_seconds=61.200,
                           text="first card of block 22", speaker="Akshita",
                           frames=round((61.200 - 60.125) * FPS),
                           block_position="22"),
            PlannedCaption(start_seconds=61.200, end_seconds=62.374,
                           text="second card of block 22", speaker="Akshita",
                           frames=round((62.374 - 61.200) * FPS),
                           block_position="22"),
            PlannedCaption(start_seconds=62.374, end_seconds=63.100,
                           text="number one on google because you",
                           speaker="Akshita",
                           frames=round((63.100 - 62.374) * FPS),
                           block_position="23"),
            PlannedCaption(start_seconds=63.100, end_seconds=63.809,
                           text="have may have optimized your services.",
                           speaker="Akshita",
                           frames=round((63.809 - 63.100) * FPS),
                           block_position="23"),
        )

    def test_abutting_blocks_draw_no_f2(self):
        """Per-edge spans of abutting blocks abut: no overlap, no F2.

        Old arithmetic expected round(duration) - 54 and 34 - against
        per-edge placed spans of 53 and 35, drawing two F2s on a
        faithful build.  After the fix both sides read the per-edge
        span and this is clean.
        """
        planned = self._planned()
        actual = (
            _item("video", 3, 1442, 1495, name="block22"),
            _item("video", 3, 1495, 1530, name="block23"),
        )
        assert actual[0].end_frame <= actual[1].start_frame, (
            "the placed spans must not overlap")
        findings = check_caption_duration("Reel 07", planned, actual, FPS)
        assert findings == [], (
            "abutting blocks placed on abutting spans must draw no "
            f"finding; got {[(f.finding_class, f.message) for f in findings]}")


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


# ── F4: Item count and per-speaker duration ─────────────────────────

class TestF4ItemCount:
    """F4 - ENCODING: clips silently dropped."""


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


# ── F5: Caption coverage ────────────────────────────────────────────

class TestF5CaptionCoverage:
    """F5 - PLANNING: uncaptioned speech from straddling segments."""


    def test_f5_counts_only_the_seconds_the_reel_plays(self):
        """Two rows of one invariant: a row reaching past the reel is
        CLIPPED, not dropped, and a row an interior cut runs through
        counts only what plays - the cut-out take is not uncaptioned
        speech. Both were wrong while F5 mapped the row's raw endpoints
        (docs/evidence/reel_conformance_verifier.md)."""
        # Begins 5s BEFORE the reel and runs 5s into it; five 2s words,
        # so the row crosses the boundary and no word does.
        past = [_row(5.0, 15.0, "Craig", "a row the reel starts in",
                     speaking=tuple((float(i), float(i + 2))
                                    for i in range(5, 15, 2)))]
        # Fifteen 2s words; a 10s take is removed from the middle.
        cut = [_row(0.0, 30.0, "Akshita", "a row with a take taken out",
                    speaking=tuple((float(i), float(i + 2))
                                   for i in range(0, 30, 2)))]
        for segments, keep, expected in (
                (past, [(10.0, 20.0)], 5.0),
                (cut, [(0.0, 10.0), (20.0, 30.0)], 20.0)):
            f5 = [f for f in check_caption_coverage(
                      "Reel 03", segments, [], keep, FPS)
                  if f.finding_class == FindingClass.F5]
            assert len(f5) == 1
            assert f5[0].detail["straddling_seconds"] == pytest.approx(
                expected, abs=0.05)

    def test_a_stretched_word_is_excluded_and_honest_speech_still_fails(self):
        """Reel 10's shape: one 34.13s word, otherwise card-covered.

        Craig's "audits" (732.69-766.82) is a single word the aligner
        stretched across Akshita's whole story. Her cards leave 2.6s of
        gaps; counting the word reported those pauses as Craig talking
        with no caption and failed the reel. The word is excluded and
        the narrowing is REPORTED, never silent.
        """
        segments = [
            # No binding, one 34.13s word: the stretched residue itself.
            _row(732.69, 766.819, "Craig", "audits"),
            _row(733.02, 760.0, "Akshita", "her story", item_id="uid-a",
                 speaking=tuple((float(i), float(i + 1))
                                for i in range(733, 760))),
            _row(762.0, 766.5, "Akshita", "her story",
                 item_id="uid-b",
                 speaking=((762.0, 763.0), (763.0, 764.0),
                           (764.0, 765.0), (765.0, 766.5))),
        ]
        # Cards are in REEL seconds (master minus the range start
        # 720.75); the words above are in master seconds, as rows are.
        captions = [_caption_card(12.27, 39.25, "her story"),
                    _caption_card(41.25, 45.75, "her story")]
        keep_ranges = [(720.75, 771.001)]

        findings = check_caption_coverage(
            "Reel 10", segments, captions, keep_ranges, FPS)
        errors = [f for f in findings if f.severity == "error"]
        assert errors == [], (
            "pauses between another speaker's cards are not Craig's speech")
        warnings = [f for f in findings if f.severity == "warning"]
        assert any(w.detail.get("stretched_words") == 1
                   for w in warnings), (
            "the excluded word must be reported, not skipped quietly")

        # The gate is not weakened: straddling speech with honest word
        # timings and nothing on screen over it is still an error.
        segments = [
            _row(732.69, 736.0, "Craig", "audits indeed yes",
                 speaking=((732.69, 733.2), (733.4, 734.1),
                           (734.3, 736.0))),
        ]
        findings = check_caption_coverage(
            "Reel 10", segments, [], [(720.75, 771.001)], FPS)
        errors = [f for f in findings
                  if f.finding_class == FindingClass.F5
                  and f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].detail["straddling_seconds"] == pytest.approx(
            2.9, abs=0.05)


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


# ── F8: Boundary speech ─────────────────────────────────────────────


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


# ── F10: Format mismatch ────────────────────────────────────────────

class TestF10Format:
    """F10 - ENCODING: timeline format must be 1080x1920 at master fps.

    This project has a documented history of a correct vertical timeline
    rendering out landscape while every structural check passed.
    """


    def test_f10_grades_the_declared_frame_and_says_when_none_was(self):
        """Three rows of one invariant: F10 grades against the DECLARED
        frame and rate, never a hard-coded vertical, and an undeclared
        frame is a warning that says it was NOT CHECKED - never a pass.

        Breaks if `expected_width`/`expected_height` default to vertical
        (a declared horizontal reel refused for being what it asked for,
        AGENTS.md 10.4) or if the no-frame case returns `[]`.
        """
        # A reel at 30fps when the master is 23.976 stutters.
        fps = check_format("Reel 03", 1080, 1920, 30.0, FPS,
                           expected_width=1080, expected_height=1920)
        assert [f.finding_class for f in fps] == [FindingClass.F10]
        assert "fps" in fps[0].message.lower()

        assert check_format("Reel 01", 1920, 1080, FPS, FPS,
                            expected_width=1920, expected_height=1080) == []
        landscape = check_format("Reel 01", 1920, 1080, FPS, FPS,
                                 expected_width=1080, expected_height=1920)
        assert [f.severity for f in landscape] == ["error"]

        unchecked = check_format("Reel 01", 1080, 1920, FPS, FPS)
        assert [f.finding_class for f in unchecked] == [FindingClass.F10]
        assert unchecked[0].severity == "warning"
        assert "not checked" in unchecked[0].message
        assert unchecked[0].detail["expected_width"] is None


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

    def test_f11_refuses_output_that_cannot_prove_two_styles(self):
        """Three ways the PLACED cards fail to carry the diarization
        signal, each an F11 the check must say rather than pass:
        one speaker's style on every card of a two-speaker video, cards
        with no speaker in their name, and two speakers sharing one
        overlay file (identical rendering whatever the names say)."""
        two = (_item("video", 1, 0, int(10 * FPS), speaker="Akshita"),
               _item("video", 2, 0, int(10 * FPS), speaker="Craig"))

        def cap(name, start, end, source=None):
            return _item("video", 3, start, end, name=name,
                         source_file=source or f"/overlays/{name}")

        one_style = tuple(
            cap(f"sub_synced_akshita_body{i}_0-1000_abc123.mov",
                int(3 * i * FPS), int(3 * (i + 1) * FPS))
            for i in range(3))
        f11 = [f for f in check_subtitle_styling("Reel 01", one_style, two)
               if f.finding_class == FindingClass.F11]
        assert any("diarization" in f.message.lower() for f in f11)
        assert f11 and all(f.severity == "error" for f in f11)

        unattributed = (cap("caption_block_0.mov", 0, int(5 * FPS)),
                        cap("caption_block_1.mov", int(5 * FPS),
                            int(10 * FPS)))
        assert any(f.finding_class == FindingClass.F11
                   and "no speaker attribution" in f.message
                   for f in check_subtitle_styling(
                       "Reel 01", unattributed, two[:1]))

        shared = "/overlays/sub_synced_shared_body0_0-1000_abc123.mov"
        sharing = (cap("sub_synced_akshita_body0_0-1000_abc123.mov",
                       0, int(5 * FPS), shared),
                   cap("sub_synced_craig_body0_1000-2000_def456.mov",
                       int(5 * FPS), int(10 * FPS), shared))
        shared_findings = [f for f in check_subtitle_styling(
            "Reel 01", sharing, two) if "share" in f.message.lower()]
        assert shared_findings and shared_findings[0].severity == "error"

    def test_current_provenance_name_attributes_the_speaker_not_clip_id(self):
        """Current caption names put speaker before the source clip id.

        The old verifier skipped a timeline token and read the following
        UUID as the speaker, so Reel 17's three placed Akshita cards were
        attributed to source clip ``b191411a-...``.
        """
        from library.tools.reel_conformance_verifier import _caption_speaker

        item = _item(
            "video", 3, 0, int(FPS),
            name=("sub_akshita_b191411a-d2bf-4549-a09b_"
                  "3135634-3141184_f32a24c3.mov"),
            source_file=("/overlays/sub_akshita_b191411a-d2bf-4549-a09b_"
                         "3135634-3141184_f32a24c3.mov"),
        )
        assert _caption_speaker(item) == "akshita"


# ── Plan quality: Length ─────────────────────────────────────────────

class TestPlanQualityLength:
    """Plan quality: reel length against the 45-90s PREFERENCE.

    Reported, never enforced. The brief says "no fixed target but
    preferably between 45-90 seconds" and "No hard cap"; see
    `reel_quality_bar`'s docstring for the reading and the ten reels
    this failed before 2026-09-06.
    """

    def test_outside_the_band_is_a_WARNING_not_an_error(self):
        """The severity is the whole repair, so it is pinned.

        `reel_quality_bar.QB_ABSURD_LENGTH` is the length ERROR now."""
        for seconds in (30.0, 120.0):
            findings = check_plan_length("Reel 01", seconds)
            assert [f.severity for f in findings] == ["warning"]


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


# ── Plan quality: Picture continuity ─────────────────────────────────


# ── Integration: verify_reel ─────────────────────────────────────────

class TestVerifyReel:
    """End-to-end test of verify_reel with planted defects."""

    def test_clean_reel_passes(self):
        """A reel with no defects should produce no errors.

        The plan's span is the timeline's own length in frames: a clean
        reel is one whose plan describes it, and a plan that lays down a
        different number of frames is refused by
        `check_plan_describes_timeline` before F4 is asked anything.
        """
        plan = _plan(
            span_end=int(25 * FPS) / FPS,
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


    def test_reel_with_multiple_defects(self):
        """A reel with holes AND missing clips reports both F1 and F4.

        Craig's V2 placement sits UNDER Akshita's V1, so dropping it
        loses a clip without shortening the reel - the plan still
        describes the timeline's length, F4 is still asked, and it still
        reports the drop.  That is the case `check_plan_describes_
        timeline` must not swallow.
        """
        plan = _plan(
            span_end=1075 / FPS,
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


# ── Read-only proof ──────────────────────────────────────────────────


# ── CLI ──────────────────────────────────────────────────────────────

class TestCLI:
    """Test the CLI entry point."""


    def test_requires_resolve_connection(self, monkeypatch):
        """Calling with valid args but no Resolve exits 2."""
        from library.tools import marker_feedback
        from library.tools.reel_conformance_verifier import run_verification
        import io

        def unreachable():
            raise marker_feedback.ResolveUnavailable("Resolve is not running")

        # Unreachable by construction: on a machine with Resolve the real
        # connect would open the live instance (tests/conftest.py).
        monkeypatch.setattr(marker_feedback, "connect_resolve", unreachable)
        out = io.StringIO()
        code = run_verification(
            project_name="Nonexistent Project",
            master_name="Nonexistent Timeline",
            out=out,
        )
        assert code == 2


    def test_unmapped_track_distinct_finding(self):
        """A track that cannot be mapped to a speaker is reported distinctly."""
        planned = (
            _placement(1, 0.0, 27.30, "Akshita"),
        )
        actual = (
            _item("video", 1, 0, int(27.30 * FPS), speaker="Akshita"),
            _item("video", 2, 0, 120, speaker="UnknownSpeaker"), # Not in plan!
        )
        findings = check_item_count("Reel 01", planned, actual, FPS)
        unmapped_findings = [f for f in findings if "could not be mapped" in f.message]
        assert len(unmapped_findings) == 1
        assert unmapped_findings[0].detail["track"] == 2
        assert unmapped_findings[0].detail["unmapped_duration"] == round(120 / FPS, 2)


# ── The plan the verifier grades against ─────────────────────────────
#
# `_derive_plan_from_master` re-derives what a reel SHOULD be, using the
# builder's own arithmetic, and every other check is measured against
# it. Nothing exercised it, so when PR #524 gave `reel_build.placements`
# a required `fps` and did not update the call here, the verifier raised
# `TypeError` on every run - and `verify_built_reels` turned that into
# "Reel conformance verifier failed to run", which is how the build gate
# reported a verifier that could not start.

def _master_clip(**kw):
    from library.tools.timeline_ingest import TimelineClip

    base = dict(resolve_item_id="uid-1", track_type="video", track_index=1,
                track_name="V1", speaker="Craig", source_file="/m/a.MXF",
                source_in=100.0, source_out=110.0, source_in_frame=2400,
                source_out_frame=2640, source_frames=100000,
                timeline_start=10.0, timeline_end=20.0, name="a.MXF")
    base.update(kw)
    return TimelineClip(**base)


def _master_snapshot(*clips, fps=24000 / 1001):
    from library.tools.timeline_ingest import TimelineSnapshot

    return TimelineSnapshot(
        project_name="scratch", timeline_name="master", fps=fps,
        reported_fps=24.0, width=1080, height=1920, start_frame=0,
        end_frame=int(round(2656.0 * fps)), clips=tuple(clips))


def test_the_plan_can_be_derived_from_the_master():
    from library.tools.reel_conformance_verifier import _derive_plan_from_master

    master = _master_snapshot(
        _master_clip(),
        _master_clip(resolve_item_id="uid-2", track_index=2, track_name="V2",
                     speaker="Akshita", source_file="/m/b.MXF",
                     timeline_start=14.0, timeline_end=22.0))
    plan = _derive_plan_from_master("Reel 01 - x", 1, 12.0, 21.0, master)

    assert plan.reel_name == "Reel 01 - x"
    assert plan.keep_ranges == ((12.0, 21.0),)
    assert plan.plan_seconds == 9.0
    # Both tracks are placed, each trimmed to the part inside the span.
    assert {p.source_file for p in plan.placements} == {"/m/a.MXF", "/m/b.MXF"}
    v1 = next(p for p in plan.placements if p.source_file == "/m/a.MXF")
    assert v1.record_seconds == 0.0
    # 2s into the clip, to within a frame: PR #524 made `placements`
    # land each clip on a whole frame, which is why it needs `fps`.
    assert abs(v1.source_in - 102.0) < 1 / master.fps
# ── A reel that closes on a CTA from elsewhere in the episode ────────
#
# The verifier RE-DERIVES the plan from the master, so a reel built with
# a closing CTA range verifies as defective unless the derivation knows
# about it: the built timeline carries picture items the derived plan
# never listed (F4, a hard error, on a correct build) and `plan_seconds`
# is short by the closer's length everywhere it is reported or compared.

class TestClosingCallToAction:

    @staticmethod
    def _master():
        """A master with continuous picture: Akshita on V1, Craig on V2."""
        from library.tools.timeline_ingest import TimelineClip, TimelineSnapshot

        def clip(track, speaker, start, end, source):
            return TimelineClip(
                resolve_item_id=f"{speaker}-{start}", track_type="video",
                track_index=track, track_name=speaker, speaker=speaker,
                source_file=source, source_in=start,
                source_out=end,
                source_in_frame=int(start * FPS),
                source_out_frame=int(end * FPS),
                source_frames=200000, timeline_start=start,
                timeline_end=end, name=f"{speaker} {start}")

        return TimelineSnapshot(
            project_name="P", timeline_name="Master",
            fps=FPS, reported_fps=24.0, width=3840, height=2160,
            start_frame=0, end_frame=int(1200 * FPS),
            clips=(clip(1, "Akshita", 0.0, 1200.0, "/m/ak.MXF"),
                   clip(2, "Craig", 0.0, 1200.0, "/m/cr.MXF")))

    @staticmethod
    def _moment(cta=None, start=600.0, end=660.0, number=1):
        from library.tools.reel_proposal import CallToAction, ReelMoment
        return ReelMoment(
            number=number, slug="retrieval", reason="a complete exchange",
            timeline_start=start, timeline_end=end,
            call_to_action=(CallToAction(timeline_start=cta[0],
                                         timeline_end=cta[1])
                            if cta else None))

    def _derive(self, moment):
        from library.tools.reel_build import cta_range
        from library.tools.reel_conformance_verifier import (
            _derive_plan_from_master)
        return _derive_plan_from_master(
            "Reel 01 - retrieval", 1,
            moment.timeline_start, moment.timeline_end,
            self._master(), {"segments": []},
            call_to_action=cta_range(moment))


    def test_the_plan_can_be_derived_with_a_project_folder(self, tmp_path):
        """The card branch of `_derive_plan_from_master` (moment + project_folder)
        runs: a branch nothing runs breaks silently (PR #1095's required width/height).
        History: docs/evidence/reel_conformance_verifier.md.
        """
        from library.tools.reel_conformance_verifier import (
            _derive_plan_from_master)

        plan = _derive_plan_from_master(
            "Reel 01 - retrieval", 1, 600.0, 660.0, self._master(),
            {"segments": []}, moment=self._moment(),
            project_folder=str(tmp_path))
        assert plan.placements, "the derivation still produces a plan"
        assert plan.cards == (), "nothing is declared, so no card is planned"

    def test_the_derived_plan_lists_the_closers_own_placements(self):
        """F4 compares planned item count to what is on the timeline. A
        plan that omits the closer reports a correct build as defective."""
        without = self._derive(self._moment())
        with_cta = self._derive(self._moment(cta=(468.0, 476.0)))
        assert (len(with_cta.placements)
                == len(without.placements) + 2), "one per picture track"
        assert with_cta.placements[-1].source_in == pytest.approx(476.0 - 8.0,
                                                                  abs=0.1)


    def test_a_recorded_head_trim_survives_re_derivation_in_play_order(
            self, tmp_path):
        """Re-derivation applies the recorded `span_retime` trims at the build's seam,
        and the early-master CTA still closes the reel (Reel 16, 2026-09-18).
        """
        def _timed(tokens, start):
            words, cursor = [], start
            for token in tokens:
                words.append({"word": token, "start": cursor,
                              "end": round(cursor + 0.4, 3),
                              "timed": True})
                cursor = round(cursor + 0.5, 3)
            return words

        transcript = {"segments": [
            _row(468.0, 476.0, "Craig", "check it out",
                 words=_timed(["check", "it", "out"], 468.0)),
            # The inherited CTA ending carries its trailing silence only
            # to the next spoken word. Keep that boundary adjacent here;
            # otherwise the synthetic gap to the body at 600s becomes a
            # 124-second ending breath once exact placements retain all
            # three CTA anchor words.
            _row(476.01, 476.41, "Craig", "and",
                 words=_timed(["and"], 476.01)),
            _row(600.0, 660.0, "Craig",
                 "what you are saying reverts back if your brand is "
                 "mentioned here today",
                 words=_timed(
                     ["what", "you", "are", "saying", "reverts", "back",
                      "if", "your", "brand", "is", "mentioned", "here",
                      "today"], 600.0)),
        ]}
        external = tmp_path / "external"
        external.mkdir(exist_ok=True)
        (external / "captain_edits.json").write_text(
            json.dumps({"key": "captain_edits", "source": "test",
                        "value": [{
                            "kind": "span_retime",
                            "anchor_phrase": (
                                "if your brand is mentioned here"),
                            "edge": "head",
                            "reason": "reel 16 re-pin"}]}),
            encoding="utf-8")
        from library.tools.reel_conformance_verifier import (
            _derive_plan_from_master)
        plan = _derive_plan_from_master(
            "Reel 01 - retrieval", 1, 600.0, 660.0, self._master(),
            transcript,
            call_to_action=(468.0, 476.0),
            moment=self._moment(cta=(468.0, 476.0)),
            project_folder=str(tmp_path))
        assert [r[0] for r in plan.keep_ranges] == pytest.approx(
            [603.0, 468.0], abs=0.05)
        assert [r[1] for r in plan.keep_ranges] == pytest.approx(
            [660.0, 476.0], abs=0.05)

    def test_the_closers_own_boundaries_are_checked_for_cut_speech(self):
        """F8 tested only the body's two boundaries. The closer's are the
        ones that decide whether the reel ends on a finished sentence."""
        segments = [
            _row(470.0, 480.0, "Craig",
                 "jump on lucycontent.com and dm us"),
        ]
        findings = check_boundary_speech(
            "Reel 01", span_start=600.0, span_end=660.0,
            transcript_segments=segments,
            ranges=[(600.0, 660.0), (468.0, 476.0)])
        assert len(findings) == 1
        assert findings[0].detail["boundary"] == "end"
        assert findings[0].detail["boundary_time"] == pytest.approx(476.0)


    def test_dead_master_time_between_the_ranges_is_not_a_picture_hole(self):
        """The reel never plays those seconds, so a gap there is not a
        gap in the reel - but a gap INSIDE a range still is."""
        master_items = [
            {"timeline_start": 0.0, "timeline_end": 200.0, "track_index": 1},
            {"timeline_start": 600.0, "timeline_end": 700.0, "track_index": 1},
        ]
        assert check_plan_picture_continuity(
            "Reel 01", 600.0, 660.0, master_items,
            ranges=[(600.0, 660.0), (100.0, 108.0)]) == []

        holed = check_plan_picture_continuity(
            "Reel 01", 600.0, 660.0, master_items,
            ranges=[(600.0, 660.0), (190.0, 610.0)])
        assert holed and holed[0].finding_class == FindingClass.PQ_PICTURE


# ── NO-REFERENCE: the empty expected side ────────────────────────────

class TestNoReferenceRefusesRatherThanSkipping:
    """An EMPTY expected caption side is a NO_REFERENCE finding, never a vacuous pass.
    History (0/762 on the nineteen): docs/evidence/reel_conformance_verifier.md.
    """


    def test_could_not_be_asked_is_not_the_same_as_has_none(self):
        """"Could not be asked" REFUSES; "has none" stays clean, because
        `--skip-captions` builds a watchable timeline on purpose.
        History: docs/evidence/reel_conformance_verifier.md.
        """
        assert check_caption_reference("Reel 01 - test", (), ()) == []
        findings = check_caption_reference(
            "Reel 01 - test", (), (),
            unavailable="spine_for_reel is not available in "
                        "library.tools.reel_spine")

        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.NO_REFERENCE
        assert findings[0].severity == "error"
        assert findings[0].detail["direction"] == "unavailable"
        assert "reel_spine" in findings[0].message


    def test_verify_reel_refuses_the_vacuous_case_end_to_end(self):
        """The exact shape of the live run: 0 expected, many found."""
        result = verify_reel(
            _plan(captions=()),
            _timeline(caption_items=tuple(
                _item("video", 3, i * 24, (i + 1) * 24) for i in range(28))))

        refusals = [f for f in result.findings
                    if f.finding_class == FindingClass.NO_REFERENCE]
        assert len(refusals) == 1
        assert result.errors, "an empty reference set must FAIL the gate"


# ── The three instrument defects found reading the nineteen ─────────
#
# Every test in this section FAILS on the code before the fix beside it.
# The findings they describe were measured on the captain's nineteen
# approved reels, which is why the numbers in them are the real ones.

class TestF8MeasuresWordsNotRowEnvelopes:
    """F8 asked whether a boundary sat inside a ROW; it claims to ask
    whether the boundary cuts SPEECH, and a straddling row is not speech.

    Measured on the nineteen: 16 findings, of which 13 landed in a
    silence between two words of the same row.
    """

    def test_f8_reads_words_and_falls_back_to_the_envelope_out_loud(self):
        """Reel 01's END, exactly as it is on the captain's timeline.

        Row 22.04-47.23s is one WhisperX segment of Craig's isolated
        track carrying two separate utterances - "this is a completely
        different system right" to 24.28s, then "so give me an example
        of that difference" from 45.41s.  Twenty-one seconds of that row
        is Craig silent.  The reel ends at 44.74s, inside the silence and
        0.67s before he speaks again, and nothing is cut.
        """
        segments = [_row(22.04, 47.23, "Craig",
                         "this is a completely different system right so "
                         "give me an example of that difference",
                         speaking=((22.04, 24.281), (45.409, 47.23)))]
        findings = check_boundary_speech(
            "Reel 01", span_start=0.125, span_end=44.74,
            transcript_segments=segments)
        errors = [f for f in findings if f.severity == "error"]
        assert errors == [], "the boundary lands in a silence, not in speech"

        # A row with NO word timings must not silently switch the check
        # off: the envelope answers, and the fallback is reported.
        segments = [_row(470.0, 480.0, "Craig", "no timings", words=())]
        findings = check_boundary_speech(
            "Reel 01", span_start=460.0, span_end=476.0,
            transcript_segments=segments)
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        assert len(errors) == 1, "the envelope still answers when it is all there is"
        assert errors[0].detail["word"] is None
        assert warnings[0].detail["rows_without_word_timings"] == 1


class TestF5MeasuresSpeechNotRowSpan:
    """F5's message, its class docstring and AGENTS.md all say it counts
    SECONDS OF SPEECH.  It counted row span minus caption coverage, and a
    row's span is mostly not speech: on the nineteen that reported 95.1s
    uncaptioned where the placed cards leave 6.3s.
    """

    def test_only_the_words_count_not_the_row_envelope(self):
        """A row 40s wide carrying 2s of speech is 2s of speech."""
        segments = [_row(0.0, 40.0, "Craig", "two seconds of talking",
                         speaking=((0.0, 1.0), (39.0, 40.0)))]
        findings = check_caption_coverage(
            "Reel 05", segments, [], [(0.0, 40.0)], FPS)
        f5 = [f for f in findings if f.severity == "error"]
        assert len(f5) == 1
        assert f5[0].detail["straddling_seconds"] == pytest.approx(2.0, abs=0.05)


    def test_f5_reads_the_cards_on_the_timeline_not_the_plans(self):
        """F5 answers "what plays with nothing on screen", and only a
        card that was placed is on screen.  The plan's cards are
        RE-DERIVED at verification time: on the nineteen, 832 derived
        against 763 placed, differing on every reel.  This is the rule
        F11 already applies - read the output, not the request.
        """
        plan = _plan(
            span_end=int(25 * FPS) / FPS,
            placements=(_placement(1, 0.0, 25.0, "Akshita"),),
            captions=(PlannedCaption(start_seconds=0.0, end_seconds=25.0,
                                     text="a card the plan wanted",
                                     speaker="Craig", frames=599),),
        )
        timeline = _timeline(
            video_items=(_item("video", 1, 0, int(25 * FPS)),),
            # One card, and it covers a single second of the reel.
            caption_items=(_item("video", 3, 0, int(1 * FPS),
                                 name="sub_reel-01_craig_one-second.mov"),),
        )
        segments = [_row(0.0, 25.0, "Craig", "talking the whole time",
                          speaking=tuple((float(i), float(i + 1))
                                         for i in range(25)))]
        result = verify_reel(plan, timeline, transcript_segments=segments)
        f5 = [f for f in result.findings
              if f.finding_class == FindingClass.F5 and f.severity == "error"]
        assert len(f5) == 1, "the plan's card is not on screen; the placed one is"
        assert f5[0].detail["straddling_seconds"] == pytest.approx(24.0, abs=0.1)


class TestPlanMismatchRefusesF4:
    """A re-derived plan that lays down a different number of frames is
    PLAN_MISMATCH, not F4 dropped clips.
    History: docs/evidence/reel_conformance_verifier.md.
    """

    def test_a_plan_of_a_different_length_refuses_f4(self):
        plan = _plan(span_end=60.0,
                     placements=(_placement(1, 0.0, 25.0, "Akshita"),
                                 _placement(1, 25.0, 25.0, "Akshita")))
        timeline = _timeline(video_items=(_item("video", 1, 0, 599),))
        result = verify_reel(plan, timeline)
        classes = {f.finding_class for f in result.findings
                   if f.severity == "error"}
        assert FindingClass.PLAN_MISMATCH in classes
        assert FindingClass.F4 not in classes, (
            "a plan that is not this reel's cannot say a clip was dropped")


# ── F15: Caption card hangs past its speech ──────────────────────────

class TestCaptionHangs:
    """F15: Prove the verifier catches a card whose duration far exceeds
    its speech.  Measured across 19 reels' current plans: 0 firings."""

    def test_check_caption_hangs(self):
        """F15: A card hangs for 239 frames - nearly 10 SECONDS."""
        # 1329 frames = 55.375s, 1568 frames = 65.333s
        # Duration is ~10s. Text is "yeah so ranking tells google" (5 words).
        cards = [
            _caption_card(55.375, 65.333, "yeah so ranking tells google"), # hangs!
        ]
        findings = check_caption_hangs("Reel 05", cards, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F15

        # A correct card: 5 words, ~2 seconds duration
        cards_correct = [
            _caption_card(55.375, 57.375, "yeah so ranking tells google"),
        ]
        findings_correct = check_caption_hangs("Reel 05", cards_correct, FPS)
        assert len(findings_correct) == 0


class TestF17SequentialTurnVsSimultaneousTalkover:
    """F17 claims a card MIXES two speakers. Reel 5 of the rebuild
    (2026-09-08) proves time overlap is not mixing: Akshita's card
    'recommend you or your brand.' (412.63-413.85s) carries only her
    words, but Craig's overlapping onset 'about' (412.77-414.03s) shares
    the same seconds, so the check failed a card no regrouping can fix -
    every sub-span of it overlaps Craig too. Only a SEQUENTIAL turn
    (one speaker's words then the other's, disjoint in time) is a card
    defect, because only that can be regrouped apart."""

    def _reel5_segments(self):
        return [
            _row(412.63, 413.85, "Akshita", "recommend you or your brand.",
                 item_id="uid-a",
                 words=tuple(
                     {"word": w, "start": a, "end": b, "timed": True}
                     for w, (a, b) in {
                         "recommend": (412.63, 412.99),
                         "you": (413.01, 413.17),
                         "or": (413.37, 413.43),
                         "your": (413.45, 413.57),
                         "brand.": (413.59, 413.85),
                     }.items())),
            _row(412.77, 414.03, "Craig", "about",
                 words=({"word": "about", "start": 412.77, "end": 414.03,
                          "timed": True},)),
        ]

    def test_simultaneous_talkover_is_not_an_error(self):
        """The reel-5 card, exactly as derived: single-speaker words over
        simultaneous speech. No regrouping separates two voices sounding
        at once, so failing it fails correct output (AGENTS.md 10.4)."""
        segments = self._reel5_segments()
        keep_ranges = [(342.038, 413.851)]
        cards = [_caption_card(412.63 - 342.038, 413.85 - 342.038,
                               "recommend you or your brand.")]
        findings = check_mixed_speakers(
            "Reel 05", cards, segments, keep_ranges, FPS)
        errors = [f for f in findings if f.severity == "error"]
        assert errors == [], "a single-speaker card over talk-over is not mixing"


# ── F18/F19/F20: transition elements laid over a cut ─────────────────
#
# Added 2026-09-07 with `library/tools/transition_overlay.py`. Until
# then `_snapshot_to_reel_timeline` bucketed video into V1-V2 picture and
# V3 captions with an implicit `else: pass`, so an item on V4 was on the
# captain's timeline and in NO check - not read as a duplicate, not read
# as a hole, not read at all. Each of these is proved in both directions.

from library.tools.reel_conformance_verifier import (  # noqa: E402
    OVERLAY_TRACK,
    check_overlay_caption_coverage,
    check_transition_overlays,
    check_unclassified_video,
)


def _overlay_item(start: int, frames: int, name: str = "bumper.mov",
                  track: int = OVERLAY_TRACK) -> TimelineItem:
    return _item("video", track, start, start + frames,
                 source_file="/brand/bumper.mov", speaker=None, name=name)


def _planned_overlay(seam: int, record: int, frames: int) -> dict:
    return {"seam_index": seam, "record_frame": record,
            "duration_frames": frames, "track_index": OVERLAY_TRACK}


def test_f18_catches_a_plan_and_a_timeline_that_disagree():
    """Both directions: a declaration that silently draws nothing is
    indistinguishable from no declaration, and an element no plan wrote
    is not the plan's."""
    for planned, placed, says in (
            ([_planned_overlay(1, 240, 36)], [], "is not on V"),
            ([], [_overlay_item(240, 36)], "no plan wrote")):
        findings = check_transition_overlays("Reel 01", planned, placed, FPS)
        assert [f.finding_class for f in findings] == [FindingClass.F18], says
        assert says in findings[0].message
        assert findings[0].severity == "error"


def test_f18_pairs_by_record_frame_not_by_list_index():
    """F2 was paired by list index while its comment claimed start frame,
    and produced 701 findings that read as a placement defect and were
    not one. Two planned elements, the FIRST unplaced: the second must
    still match its own item rather than being compared to the first's.
    """
    findings = check_transition_overlays(
        "Reel 01",
        [_planned_overlay(1, 100, 36), _planned_overlay(3, 400, 36)],
        [_overlay_item(400, 36)], FPS)
    assert len(findings) == 1
    assert findings[0].detail["record_frame"] == 100


def test_f19_reports_a_video_item_on_a_track_nothing_grades():
    """The anti-silent-drop half. Before this, an item here was
    discarded by the bucketing and read as coverage."""
    findings = check_unclassified_video(
        "Reel 01", [_item("video", 7, 0, 48, name="mystery")], FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F19]
    assert "V7" in findings[0].message
    assert findings[0].severity == "error"


def test_every_video_row_reaches_a_bucket_a_check_reads():
    """The bucketing, end to end from a snapshot.

    A classified track reaches its OWN bucket and an unknown one reaches
    `unclassified_items` (F19) rather than vanishing; and a packed row is
    read by NAME, not slot - V4 named "Semantic" is a semantic visual,
    not a transition element no plan wrote (the F18/F22 misgrade the SOP
    proof caught live)."""
    from library.tools.reel_conformance_verifier import (
        _snapshot_to_reel_timeline)

    class _Clip:
        def __init__(self, track_index, track_name=None):
            self.track_type = "video"
            self.track_index = track_index
            if track_name is not None:
                self.track_name = track_name
            self.timeline_start, self.timeline_end = 0.0, 2.0
            self.duration = 2.0
            self.source_in_frame, self.source_out_frame = 0, 48
            self.source_file, self.speaker = "/x.mov", None
            self.name, self.resolve_item_id = f"v{track_index}", ""

    def read(clips):
        snapshot = type("_Snapshot", (), dict(
            fps=FPS, timeline_name="Reel 01", start_frame=0, end_frame=48,
            width=1080, height=1920, clips=clips))
        return _snapshot_to_reel_timeline(snapshot())

    by_slot = read([_Clip(n) for n in (1, 3, 4, 5, 6, 7)])
    for bucket in ("video_items", "caption_items", "overlay_items",
                   "explainer_items", "semantic_items"):
        assert len(getattr(by_slot, bucket)) == 1, bucket
    assert [i.track_index for i in by_slot.unclassified_items] == [7]

    packed = read([_Clip(1, "A-Roll"), _Clip(3, "Subtitles"),
                   _Clip(4, "Semantic")])
    assert len(packed.video_items) == 1
    assert len(packed.caption_items) == 1
    assert len(packed.overlay_items) == 0
    assert len(packed.semantic_items) == 1
    assert packed.unclassified_items == ()


def test_f20_reports_the_caption_seconds_an_element_covers():
    findings = check_overlay_caption_coverage(
        "Reel 01", [_overlay_item(100, 30)],
        [_item("video", 3, 90, 115, name="card A")], FPS)
    assert [f.finding_class for f in findings] == [FindingClass.F20]
    assert findings[0].severity == "warning"
    assert findings[0].detail["covered_frames"] == 15


# ── F2/F14 refuse without a recorded caption baseline ────────────

class TestCaptionProvenanceGate:
    """`verify_reel` must not grade F2/F14 against a re-derived grouping.

    The moment-plan hash can match while the caption grouping has drifted
    underneath it (39 cards derived where 28 were placed) - grading that
    drift reads as a placement defect. So F2/F14 grade ONLY against the
    recorded baseline, and refuse with NO_REFERENCE otherwise.
    """

    def _gradeable(self):
        """A reel whose cards WOULD draw F2s if graded: every placed item
        is one frame short of its plan, the audit's own off-by-one."""
        name = "Reel 01 - provenance-gate"
        frames = 24
        captions = (
            PlannedCaption(start_seconds=0.0, end_seconds=1.0,
                           text="one two", speaker="Akshita",
                           frames=frames, block_position="body_1"),
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="three four", speaker="Akshita",
                           frames=frames, block_position="body_2"),
        )
        placed = (
            _item("video", 3, 0, frames - 1, name="card one two"),
            _item("video", 3, frames, 2 * frames - 1,
                  name="card three four"),
        )
        plan = _plan(reel_name=name, plan_seconds=2.0,
                     span_start=0.0, span_end=2.0,
                     placements=(_placement(1, 0.0, 2.0),),
                     captions=captions,
                     keep_ranges=((0.0, 2.0),))
        timeline = _timeline(
            reel_name=name,
            video_items=(_item("video", 1, 0, 2 * frames),),
            audio_items=(_item("audio", 1, 0, 2 * frames),),
            caption_items=placed,
            total_frames=2 * frames)
        return name, plan, timeline

    @staticmethod
    def _duration_findings(findings):
        return [f for f in findings
                if f.finding_class in (FindingClass.F2, FindingClass.F14)]

    @staticmethod
    def _provenance_refusals(findings):
        return [f for f in findings
                if f.finding_class == FindingClass.NO_REFERENCE
                and f.detail.get("check") == "F2/F14"]

    def test_refuses_without_provenance_and_grades_against_a_match(self):
        """No record of what was placed: refuse, do not grade."""
        _, plan, timeline = self._gradeable()
        result = verify_reel(plan, timeline, caption_provenance=None)
        assert self._duration_findings(result.findings) == []
        refusals = self._provenance_refusals(result.findings)
        assert len(refusals) == 1
        assert refusals[0].severity == "error"
        assert refusals[0].detail["graded"] is False

        # The gate refuses the unknown, not the known: a matching record
        # grades normally, and the planted off-by-one draws its F2s.
        from library.tools.plan_provenance import caption_content_hash

        name, plan, timeline = self._gradeable()
        provenance = {"caption_hashes": {
            name: caption_content_hash(plan.captions)}}
        result = verify_reel(plan, timeline,
                             caption_provenance=provenance)
        assert self._provenance_refusals(result.findings) == []
        duration = self._duration_findings(result.findings)
        assert len(duration) == len(plan.captions)
        assert all(f.finding_class == FindingClass.F2 for f in duration)
        assert all(f.detail["delta"] == -1 for f in duration)


# ── F6 grades the cards that were built, not the re-derived plan ───

class TestF6GradesPlacedCards:
    """`verify_reel` grades F6 against the PLACED items, not the re-derived plan.
    History: docs/evidence/reel_conformance_verifier.md.
    """

    @staticmethod
    def _reel(plan_captions, placed):
        name = "Reel 01 - f6-placed"
        plan = _plan(reel_name=name, plan_seconds=2.0,
                     span_start=0.0, span_end=2.0,
                     placements=(_placement(1, 0.0, 2.0),),
                     captions=plan_captions,
                     keep_ranges=((0.0, 2.0),))
        timeline = _timeline(
            reel_name=name,
            video_items=(_item("video", 1, 0, 48),),
            audio_items=(_item("audio", 1, 0, 48),),
            caption_items=placed,
            total_frames=48)
        return verify_reel(plan, timeline)

    @staticmethod
    def _f6(findings):
        return [f for f in findings
                if f.finding_class == FindingClass.F6]

    def test_overlapping_plan_beside_clean_placement_draws_no_f6(self):
        """The planner's overlap was never placed: no F6 on the reel."""
        plan_captions = (
            PlannedCaption(start_seconds=0.0, end_seconds=1.0,
                           text="one two", speaker="Akshita",
                           frames=24, block_position="body_1"),
            PlannedCaption(start_seconds=0.5, end_seconds=1.5,
                           text="three four", speaker="Craig",
                           frames=24, block_position="body_1"),
        )
        placed = (
            _item("video", 3, 0, 24, name="card one two"),
            _item("video", 3, 24, 48, name="card three four"),
        )
        result = self._reel(plan_captions, placed)
        assert self._f6(result.findings) == []


# ── F15, pointed at what was placed ──────────────────────────────────

def _placed_caption(start_frame: int, duration_frames: int,
                    name: str) -> TimelineItem:
    """One caption item as read off a timeline, for F15-placed tests."""
    return _item("video", 3, start_frame, start_frame + duration_frames,
                 source_file="/captions/overlay.mov", speaker="",
                 name=name)


def _planned_card(start: float, end: float, text: str,
                  block: str = "body_7") -> dict:
    """One plan-side caption card dict, carrying its spine block."""
    card = _caption_card(start, end, text)
    card["block_position"] = block
    return card


class TestPlacedCaptionHangs:
    """F15 grades the PLACED item, not the plan card.

    Reel 05's card at frame 1329 ("yeah so ranking tells google") was
    correct in the plan and 239 frames long on the timeline. The old
    wiring graded the plan card and passed a ten-second hang under a
    green verdict. These fixtures replay that shape: a short correct
    plan beside a placed item that hangs.
    """

    HANG_START = 1329
    HANG_FRAMES = 239  # the real placed duration off reel 05

    def _reel05(self):
        plan = [_planned_card(55.375, 57.2, "yeah so ranking tells google")]
        hang = _placed_caption(
            self.HANG_START, self.HANG_FRAMES,
            "sub_reel-05-the-audit-that-was-eye-o_akshita_"
            "yeah-so-ranking-tells-google_05383e4d.mov")
        return plan, hang


    def test_a_placed_hang_is_an_F15(self):
        """The new wiring fires on the same fixture."""
        plan, hang = self._reel05()
        findings = check_placed_caption_hangs("Reel 05", plan, [hang], FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F15
        assert findings[0].detail["placed_frames"] == self.HANG_FRAMES


# ── F16: caption names break on a word boundary ──────────────────────

class TestCaptionSlugFragments:
    """F16: no placed caption name breaks mid-word.

    The three names below are verbatim off reel 05's timeline
    (2026-09-08): the timeline slug cut at thirty characters mid-word,
    like every other card on that reel.
    """
    REEL = "Reel 05 - the-audit-that-was-eye-opening"

    REAL_NAMES = [
        (440, 40,
         "sub_reel-05-the-audit-that-was-eye-o_akshita_"
         "it-they-were-being-invisible-o_54079ecd.mov"),
        (482, 56,
         "sub_reel-05-the-audit-that-was-eye-o_akshita_"
         "their-services-were-not-being-_825fef8a.mov"),
        (546, 36,
         "sub_reel-05-the-audit-that-was-eye-o_akshita_"
         "the-same-way-that-they-envisio_c270b8f1.mov"),
        (1616, 12,
         "sub_reel-05-the-audit-that-was-eye-o_craig_"
         "here-yeah-so-ranking-tells-goo_e896ede1.mov"),
        (1628, 11,
         "sub_reel-05-the-audit-that-was-eye-o_akshita_"
         "ranking-tells-google-that-you-_1cbbb2da.mov"),
        (1837, 43,
         "sub_reel-05-the-audit-that-was-eye-o_akshita_"
         "yourself-the-lucy-visibility-s_979c0be4.mov"),
    ]

    def _items(self, rows):
        return [_placed_caption(start, duration, name)
                for start, duration, name in rows]

    def test_reel05s_real_names_are_flagged(self):
        findings = check_caption_slugs(self.REEL, self._items(self.REAL_NAMES))
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F16
        assert findings[0].detail["items"] == len(self.REAL_NAMES)

        # A previous producer's name from ANOTHER reel placed here is the
        # old overwrite made visible, and is still flagged.
        items = self._items([(100, 40, "sub_reel-01-geography_akshita_"
                                       "body-1_10000-11000_ab12cd34.mov")])
        assert check_caption_slugs(self.REEL, items) != []


class TestRecordedPinsAreReadBeforeThePlanIsGraded:
    """The CLI derives the project folder BEFORE reading recorded pins, so it
    grades the plan the build placed (Reels 09/26/28 PLAN-MISMATCH, 2026-09-12).
    History: docs/evidence/reel_conformance_verifier.md.
    """


    @pytest.mark.parametrize("reader, what", [
        ("_apply_recorded_pins(", "the captain's recorded closer pins"),
    ])
    def test_the_folder_is_derived_before_every_reader(self, reader, what):
        """The pin on the defect: source order inside run_verification.

        Each of these answers EMPTY on an empty folder rather than
        raising, so a derivation that lands after one of them is
        silent. Asserted on the source rather than by driving the whole
        verifier, which needs a live Resolve. It can fail - move the
        derivation back below any reader and this goes red.
        """
        import inspect
        from library.tools.reel_conformance_verifier import run_verification

        body = inspect.getsource(run_verification)
        derive = body.index("if not project_folder and plan_path:")
        assert derive < body.index(reader), (
            f"run_verification derives project_folder AFTER it reads "
            f"{what}, so every CLI run grades without it")


# --------------------------------------------------------------------------
# From test_reel_f7_placed_floor.py
#
# F7 fails every PLACED caption card and A/V item under the readability
# floor (`MIN_CAPTION_DISPLAY_SECONDS`), with no last-of-block exemption.
#
# History: `docs/evidence/reel_conformance_f7.md` (test_reel_f7_placed_floor.py).

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import reel_conformance_verifier
from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS
from library.tools.reel_conformance_verifier import (
    check_short_av_items,
)


def _placed_card(frames, text="yeah.", last_of_own_block=True):
    """A placed caption card dict as `verify_reel` builds it off the
    timeline - plus, when asked, the block keys that used to exempt it.

    A one-card block's only card is trivially its last, which is the
    shape every flash card in the report takes.
    """
    card = {"reel_start": 0.0,
            "reel_end": frames / FPS,
            "text": text,
            "speaker": None,
            "frames": frames}
    if last_of_own_block:
        card["block_position"] = "7"
        card["block_end"] = frames / FPS
    return card


def _errors(findings):
    return [f for f in findings if f.severity == "error"]


def test_flash_cards_last_of_their_own_block_now_fail():
    """R02 card 0 "yeah." 3 frames and R19 card 32 "yeah." 2 frames, each
    the last card of its own block - the exemption that used to hold them."""
    for reel, frames in (("Reel 02 - seo-that-hurts-your-ai-ranking", 3),
                         ("Reel 19 - can-you-game-ai", 2)):
        findings = check_short_captions(reel, [_placed_card(frames)], FPS)
        errors = _errors(findings)
        assert len(errors) == 1, [f.message for f in findings]
        assert errors[0].finding_class == FindingClass.F7
        assert errors[0].detail["duration_frames"] == frames


def test_a_correct_length_card_still_passes():
    findings = check_short_captions(
        "Reel 01", [_placed_card(int(round(1.0 * FPS)),
                                   text="a full second of text")], FPS)
    assert findings == []


def _item_2(track_type, track_index, duration_frames, name="clip"):
    return reel_conformance_verifier.TimelineItem(
        track_type=track_type, track_index=track_index,
        start_frame=0, end_frame=duration_frames,
        duration_frames=duration_frames,
        source_start_frame=0, source_end_frame=duration_frames,
        source_file="/m/a.MXF", speaker="Akshita", name=name)


def test_fragment_av_slivers_fail_against_the_same_floor():
    """The report's keep-range slivers: R02's 3f "their keywords" video
    chirp and R04's 5f "size." audio blip are under the same 12-frame
    floor as the flash cards."""
    findings = check_short_av_items(
        "Reel 02",
        [_item_2("video", 1, 3, name="their keywords"),
         _item_2("video", 1, 600, name="a real take")],
        [_item_2("audio", 1, 5, name="size."),
         _item_2("audio", 1, 600, name="a real take")],
        FPS)
    errors = _errors(findings)
    assert len(errors) == 2, [f.message for f in findings]
    assert all(f.finding_class == FindingClass.F7 for f in errors)
    assert {f.detail["duration_frames"] for f in errors} == {3, 5}


def test_verify_reel_grades_the_placed_card_not_the_plan():
    """End to end: the plan asks for a full-length card, the timeline
    carries a 2-frame flash - F7 must fail the flash, not pass the plan.
    """
    from library.tools.reel_conformance_verifier import (
        PlannedCaption,
        PlannedPlacement,
        ReelPlan,
        ReelTimeline,
        TimelineItem,
        verify_reel,
    )
    plan = ReelPlan(
        reel_name="Reel 19 - can-you-game-ai", reel_number=19,
        plan_seconds=30.0, plan_frames=round(30.0 * FPS, 1),
        span_start=0.0, span_end=30.0,
        placements=(PlannedPlacement(
            track_index=1, speaker="Akshita", record_seconds=0.0,
            source_in=0.0, source_out=30.0,
            source_file="/m/a.MXF"),),
        captions=(PlannedCaption(
            start_seconds=0.0, end_seconds=1.0, text="yeah.",
            speaker="Akshita", frames=int(round(1.0 * FPS)),
            block_position="7", block_end_seconds=1.0),),
        keep_ranges=((0.0, 30.0),))
    picture = TimelineItem(
        track_type="video", track_index=1, start_frame=0,
        end_frame=int(round(30.0 * FPS)),
        duration_frames=int(round(30.0 * FPS)),
        source_start_frame=0,
        source_end_frame=int(round(30.0 * FPS)),
        source_file="/m/a.MXF", speaker="Akshita", name="take")
    flash = TimelineItem(
        track_type="video", track_index=3, start_frame=0, end_frame=2,
        duration_frames=2, source_start_frame=0, source_end_frame=2,
        source_file="/m/a.MXF", speaker="Akshita", name="yeah.")
    timeline = ReelTimeline(
        reel_name="Reel 19 - can-you-game-ai", fps=FPS,
        total_frames=int(round(30.0 * FPS)),
        video_items=(picture,), audio_items=(picture,), caption_items=(flash,))
    result = verify_reel(plan, timeline)
    f7 = [f for f in result.findings
          if f.finding_class == FindingClass.F7
          and f.severity == "error"]
    assert len(f7) == 1, [f.message for f in result.findings]
    assert f7[0].detail["duration_frames"] == 2


# --------------------------------------------------------------------------
# From test_reel_verifier_timeline_units.py
#
# The pipeline verifier restores Pan/Tilt units without moving Resolve.
#
# Resolve scales Pan and Tilt returned through a non-current timeline handle
# by the current timeline's dimensions over the read timeline's dimensions.
# This models the exact Reel 24 TV-window geometry and exercises the real
# `reel.verify` operation against a read-only Resolve stand-in.

MASTER = "GEO Podcast - Synced"
FINAL = "Reel 24 - why-ai-trusts-youtube"
STAGING = FINAL + " (rebuild staging)"
SOURCE = "/media/LC4932.MXF"
WINDOW = (56.106, 530.6365, 1022.967, 1829.827)
LOOK = {
    "asset": "TV 4k.png", "punch_in": 2.3,
    # The live project scales the portrait frame to 0.9298. This gives
    # the same effective 2.138585 punch-in recorded by Reel 24's build.
    "scale": 0.9298, "power": {},
}


class _Timeline:
    def __init__(self, name, width, height):
        self.name = name
        self.width = width
        self.height = height

    def GetName(self):
        return self.name

    def GetSetting(self, key):
        return {
            "timelineResolutionWidth": str(self.width),
            "timelineResolutionHeight": str(self.height),
        }.get(key, "")


class _Project:
    def __init__(self, timelines, current):
        self.timelines = timelines
        self.current = current

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetCurrentTimeline(self):
        return self.current


def _snapshot(timeline, transform=None):
    clips = ()
    if timeline.GetName() == STAGING:
        clips = (TimelineClip(
            resolve_item_id="uid-lc4932",
            track_type="video", track_index=1, track_name="Akshita",
            speaker="Akshita", source_file=SOURCE,
            source_in=0.0, source_out=10.0,
            source_in_frame=0, source_out_frame=240,
            source_frames=1000,
            timeline_start=0.0, timeline_end=10.0,
            name="LC4932.MXF", transform=dict(transform or {})),)
    width, height = timeline.width, timeline.height
    return TimelineSnapshot(
        project_name="Mock Project", timeline_name=timeline.GetName(),
        fps=FPS, reported_fps=23.976,
        width=width, height=height,
        start_frame=0, end_frame=240, clips=clips)


def _focused_f12_result(plan, timeline, **kwargs):
    """Run the production F12 check while leaving unrelated gates neutral."""
    findings = verifier.check_delivered_framing(
        plan.reel_name, timeline.video_items, timeline.width,
        timeline.height, source_sizes=kwargs["source_sizes"],
        declared_intent=kwargs["declared_intent"],
        declared_crop_factor=kwargs["declared_crop_factor"],
        cards=plan.cards, look=kwargs["look"],
        draw_gain=kwargs["draw_gain"])
    return verifier.ReelResult(
        reel_name=plan.reel_name, reel_number=24,
        plan_seconds=10.0, plan_frames=round(10.0 * FPS),
        actual_frames=240, items_expected=1, items_actual=1,
        one_frame_holes=0, big_holes=[], captions_expected=0,
        captions_actual=0, speech_seconds=0.0,
        uncaptioned_seconds=0.0, uncaptioned_pct=0.0,
        short_captions=0, edge_cuts=0, bad_take_cuts=0,
        markers=0, findings=findings)


def test_reel_verify_operation_corrects_noncurrent_transform_read(
        tmp_path, capsys):
    """A quarter-size current timeline used to turn a valid aim into F12."""
    properties = reel_look.punch_in_properties(
        LOOK, SimpleNamespace(center_x=0.5023, center_y=0.3132),
        3840, 2160, 1080, 1920, window=WINDOW, draw_gain=1.0)
    assert properties["ZoomX"] == pytest.approx(2.138585, abs=0.000001)
    assert properties["Tilt"] == pytest.approx(-696.041, abs=0.001)

    # Resolve reads the target timeline's transforms in current-timeline
    # units. With a 270x480 current timeline, both axes come back at 1/4.
    raw_transform = dict(properties)
    raw_transform["Pan"] *= 0.25
    raw_transform["Tilt"] *= 0.25
    raw_snapshot = _snapshot(_Timeline(STAGING, 1080, 1920), raw_transform)
    raw_timeline = verifier._snapshot_to_reel_timeline(raw_snapshot)
    with patch.object(reel_look, "screen_window_rect_for",
                      return_value=WINDOW):
        raw_findings = verifier.check_delivered_framing(
            STAGING, raw_timeline.video_items, 1080, 1920,
            source_sizes={SOURCE: {"width": 3840, "height": 2160}},
            declared_intent=0.0, declared_crop_factor=1.0,
            look=LOOK, draw_gain=1.0)
    assert len(raw_findings) == 1
    assert "bottom 165.8px" in raw_findings[0].message

    current = _Timeline("Probe 270x480", 270, 480)
    master = _Timeline(MASTER, 3840, 2160)
    staging = _Timeline(STAGING, 1080, 1920)
    project = _Project([master, staging, current], current)
    resolve = MagicMock()
    resolve.GetProjectManager.return_value = MagicMock()

    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    plan_path = review / "reel_proposals_v2.json"
    write_proposal(
        plan_path,
        [ReelMoment(
            number=24, slug="why-ai-trusts-youtube",
            reason="Exercise the built Reel 24 framing.",
            timeline_start=0.0, timeline_end=10.0,
            approval=Approval.APPROVED)],
        {"derived_from": {"duration_seconds": 10.0}})
    (tmp_path / "pipeline_output").mkdir(exist_ok=True)
    record = {
        "resolve_project_name": "Mock Project",
        "master_timeline_name": MASTER,
        "plan_path": str(plan_path),
        "timelines_built": [STAGING],
        "staged_timelines": {FINAL: STAGING},
        # This is the gain recorded by Reel 24's build log.
        "draw_gain_calibration": {"gain": 1.0},
    }
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path),
        "step_outputs": {"build_reels": {"reel_build": record}},
    }), encoding="utf-8")
    transcript = transcript_path(tmp_path)
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text(json.dumps({"segments": []}), encoding="utf-8")

    def snapshot_for(timeline, _project_name):
        transform = (raw_transform if timeline.GetName() == STAGING else None)
        return _snapshot(timeline, transform)

    # A pass promotes in this operation; isolate the read-only verifier
    # from that separate Resolve write.
    with patch("library.tools.marker_feedback.connect_resolve",
               return_value=resolve), \
            patch("library.tools.timeline_ingest.resolve_project_exactly",
                  return_value=project), \
            patch("library.tools.timeline_ingest.snapshot_timeline",
                  side_effect=snapshot_for), \
            patch.object(verifier, "_catalog_source_sizes",
                         return_value={SOURCE: {
                             "width": 3840, "height": 2160,
                             "rotation": 0}}), \
            patch("library.tools.delivery_format.resolve_delivery_format",
                  return_value=(1080, 1920)), \
            patch.object(reel_look, "resolve_look", return_value=LOOK), \
            patch.object(reel_look, "screen_window_rect_for",
                         return_value=WINDOW), \
            patch.object(verifier, "_declared_framing",
                         return_value=(0.0, 1.0)), \
            patch("library.tools.reel_quality_bar.judge",
                  return_value=BarReport()), \
            patch.object(verifier, "verify_reel",
                         side_effect=_focused_f12_result), \
            patch("library.tools.reel_build.promote_staged_reels",
                  return_value={"promoted": [FINAL],
                                "organised": None, "markers": {}}), \
            patch("library.tools.reel_build."
                  "sweep_all_reels_informational") as sweep, \
            patch("library.tools.versions.store.record_reel_promotion",
                  return_value={"committed": False,
                                "reason": "offline verifier test"}):
            result = operations.get("reel.verify").execute(str(tmp_path))

    assert result.completed, result.error
    assert sweep.call_args.kwargs["draw_gain"] == 1.0
    output = capsys.readouterr().err
    assert "restored Pan x4, Tilt x4 to 1080x1920" in output
    report_path = tmp_path / "pipeline_output" / "review" / \
        "conformance_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    findings = [finding for reel in report["reels"]
                for finding in reel["findings"]]
    assert not [finding for finding in findings
                if finding["finding_class"] == "F12"]
    assert report["read_only_proof"]["all_identical"] is True


# --------------------------------------------------------------------------
# From test_reel_cover_clip.py
#
# The cutaway cover check: unplaced source placed on the master clock.
#
# Covers `verify_cover_clip` in `library/tools/reel_build.py` - the
# external-input check (AGENTS.md 3) that lets a reaction cutaway show
# listening picture the master never carried. The sync is DERIVED from
# the nearest placed clip of the same file, never asserted, and every
# other claim (bounds, disjointness, transcript silence, a locked
# static shot) is checked too.
#
# Media fixtures are generated into `tmp_path` with ffmpeg - no test
# reaches a real project. The static shot is a `color=` source (a
# locked camera, by construction); the moving shot is `testsrc2` (real
# motion, by construction); the black shot is `color=black`.

_SECTION_3_MARK = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason=(
        "ffmpeg/ffprobe is not available here, so the cover fixtures "
        "cannot be built or measured. Runs anywhere ffmpeg and "
        "ffprobe are on PATH - the CI runner installs them and "
        "AGENTS.md 9 requires them for any real run."
    ),
)


def _render(path, src, duration=4):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"{src}:d={duration}",
         "-pix_fmt", "yuv420p", str(path)],
        check=True)


def _placed(source_file, source_in, source_out, master_start):
    return TimelineClip(
        resolve_item_id=f"placed-{source_in}",
        track_type="video", track_index=1, track_name="Akshita",
        speaker="Akshita", source_file=str(source_file),
        source_in=source_in, source_out=source_out,
        source_in_frame=int(source_in * 24),
        source_out_frame=int(source_out * 24),
        source_frames=int(4.0 * 24),
        timeline_start=master_start,
        timeline_end=master_start + (source_out - source_in),
        name="clip", transform={"ZoomX": 2.0})


def _transcript(words=()):
    return {"segments": [
        {"speaker": "Akshita", "timeline_start": 0.0,
         "words": [{"word": w[2], "start": w[0], "end": w[1]}
                   for w in words]}]}


@_SECTION_3_MARK
def test_cover_derives_master_span_from_the_neighbour(tmp_path):
    """Slope-1 continuation: the placed clip ends src 2.0 at master
    100.0, so src 2.5-3.5 lands master 100.5-101.5 - derived, and the
    neighbour's row, name, speaker and framing travel with it."""
    media = tmp_path / "cam.mxf"
    _render(media, "color=c=0x808080:s=160x120:r=24")
    clip = verify_cover_clip(str(media), 2.5, 3.5,
                             [_placed(media, 0.0, 2.0, 98.0)],
                             _transcript(), FPS, require_face=False)
    assert clip.timeline_start == pytest.approx(100.5)
    assert clip.timeline_end == pytest.approx(101.5)
    assert (clip.track_index, clip.track_name, clip.speaker) == (
        1, "Akshita", "Akshita")
    assert clip.transform == {"ZoomX": 2.0}, \
        "the same camera keeps the same crop"
    assert clip.source_file == str(media)


@_SECTION_3_MARK
def test_cover_refuses_every_claim_it_cannot_check(tmp_path):
    """Each refusal names its reason: no placed clip of the file to sync
    from (sync would be asserted), the speaker talking inside the derived
    span (a cutaway mid-sentence is no reaction), a moving camera, and -
    under the production default - no face reading on a flat grey card."""
    media = tmp_path / "cam.mxf"
    other = tmp_path / "other.mxf"
    moving = tmp_path / "moving.mp4"
    _render(media, "color=c=0x808080:s=160x120:r=24")
    _render(other, "color=c=0x808080")
    _render(moving, "testsrc2=s=160x120:r=24")
    cases = (
        (media, [_placed(other, 0.0, 2.0, 98.0)], _transcript(), {},
         "no sync basis"),
        (media, [_placed(media, 0.0, 2.0, 98.0)],
         _transcript(words=[(100.7, 101.0, "mm-hm")]), {}, "mid-sentence"),
        (moving, [_placed(moving, 0.0, 2.0, 98.0)], _transcript(),
         {"require_face": False}, "camera moved"),
        (media, [_placed(media, 0.0, 2.0, 98.0)], _transcript(), {},
         "no face (check|reads)"),
    )
    for source, placed, transcript, kwargs, match in cases:
        with pytest.raises(OffsetRefused, match=match):
            verify_cover_clip(str(source), 2.5, 3.5, placed, transcript,
                              FPS, **kwargs)


# --------------------------------------------------------------------------
# From test_reel_semantic_visual.py
#
# F22: semantic visuals, graded against the record the build wrote.
#
# Both directions, mirroring F21 beside it. A check that only catches an
# absence reads as coverage while an out-of-band append walks past it
# (AGENTS.md 10.4).
#
# The planning half - the request the model answers, and what an
# unanswered request builds - is tested below against
# `library/tools/reel_semantic_visual.py` without Resolve and without a
# model: a missing answer file must build nothing and say
# `awaiting_model_answer`, never block and never invent.

def _item_3(start, frames, track=sem.SEMANTIC_TRACK, name="vox_reel_09_00.mov"):
    return TimelineItem(
        track_type="video", track_index=track,
        start_frame=start, end_frame=start + frames,
        duration_frames=frames,
        source_start_frame=0, source_end_frame=frames,
        source_file="/x.mov", speaker=None, name=name,
        unique_id=f"id-{start}")


def _record(*segments, basis=sem.PLANNED):
    return {"reel": "Reel 09", "basis": basis,
            "entries": [{"element": "subject_emblem"}],
            "dropped": [],
            "segments": [{"timeline_start": s, "total_frames": f,
                          "timeline_end": s + f / FPS,
                          "elements": ["subject_emblem"]}
                         for s, f in segments]}


# ── F22 passes what is right ─────────────────────────────────────────

# ── F22 fails what is wrong, in both directions ──────────────────────

def test_f22_fails_what_is_wrong_in_both_directions():
    start = int(round(4.901 * FPS))
    table = [
        # planned, but the timeline does not carry it
        ([], _record((4.901, 75)), "no item"),
        # an item no record accounts for: the out-of-band append
        ([_item_3(100, 50)], _record(basis=sem.AWAITING_MODEL_ANSWER), None),
        ([_item_3(start, 74)], _record((4.901, 75)), "74 frames"),
        ([_item_3(start, 75), _item_3(start + 10, 75)],
         _record((4.901, 75), (5.5, 75)), "overlap"),
    ]
    for items, record, says in table:
        findings = check_semantic_visuals("Reel 09", items, record, FPS)
        assert FindingClass.F22 in [f.finding_class for f in findings], says
        if says:
            assert any(says in f.message for f in findings), says
    # ... and passes what is right
    start = int(round(4.901 * FPS))
    findings = check_semantic_visuals(
        "Reel 09", [_item_3(start, 75)], _record((4.901, 75)), FPS)
    assert findings == []


def test_promotion_replaces_the_previous_final_record(tmp_path):
    """A rebuild records under the staging name and promotion renames
    it to the final one - but the previous build's record is already
    there under the final name. Renaming beside it leaves TWO records
    for one reel (live catch on Reel 09: a stale `awaiting_model_answer`
    beside the new `planned`), and `record_for_reel` reads the first,
    so the next verifier grades the promoted timeline against the
    absence. Promotion replaces; it does not shelve beside."""

    project = tmp_path / "proj"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    final = "Reel 09 - your-website-is-only-20-percent"
    staging = final + " (rebuild staging)"
    (review / sem.PLAN_FILENAME).write_text(
        json.dumps({"format": "semantic_visual_plans/1", "plans": [
            {"reel": final, "basis": sem.AWAITING_MODEL_ANSWER,
             "entries": [], "dropped": [], "segments": []},
            {"reel": staging, "basis": sem.PLANNED,
             "entries": [{"element": "subject_emblem"}], "dropped": [],
             "segments": []}]}),
        encoding="utf-8")
    sem.rename_record_reels(str(project), {staging: final})
    stored = json.loads(
        (review / sem.PLAN_FILENAME).read_text(encoding="utf-8"))
    kept = [p for p in stored["plans"] if p["reel"] == final]
    assert len(kept) == 1
    assert kept[0]["basis"] == sem.PLANNED


# ── An unanswered ask builds nothing and says so ─────────────────────

class _Moment:
    number = 9
    timeline_name = "Reel 09 - your-website-is-only-20-percent"


def _ranges():
    return [(631.12, 693.3)]


def test_no_answer_file_builds_nothing_and_names_its_basis(tmp_path):
    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    segments, record = sem.build_for_reel(
        _Moment(), {"structure": []}, _ranges(), str(project),
        fps=FPS, width=1080, height=1920)
    assert segments == []
    assert record["basis"] == sem.AWAITING_MODEL_ANSWER
    assert record["reel"] == _Moment.timeline_name


# ── The ask is the pipeline's own planning surface ───────────────────

def test_the_request_carries_the_reel_spine_and_the_roster():
    spine = {"structure": [{
        "position": 1, "block_type": "speech",
        "timeline_start": 0.0, "timeline_end": 4.0,
        "content": {"text": "we spent a lot of money on the website"},
    }]}
    context = sem.bridge_context(spine, "", FPS)
    assert "money" in context["timeline_context_toon"]
    assert "subject_emblem" in context["motion_elements_toon"]
    assert "anchor_phrase" in sem.handoff_text()


# ── The project reaches the renderer ─────────────────────────────────
#
# `build_for_reel` drove `motion_graphics.render_segment` without the
# project, so `render_one_segment` resolved the geometry against
# nothing and every reel graphic rendered full canvas even on a
# project declaring `motion_graphics_overlay_geometry: tight` - while
# the master pass beside it rendered tight. The project is forwarded
# so the declaration is read live, per render.


class _CapturedRender:
    """The render operation, recording what the build handed it."""

    def __init__(self):
        self.calls = []

    def run(self, planned, out_dir, **kwargs):
        self.calls.append((planned, out_dir, kwargs))
        return {
            "overlay_path": f"{kwargs.get('segment_name')}.mov",
            "timeline_start": 0.0,
            "timeline_end": 1.0,
            "total_frames": 24,
            "elements": ["title_lockup"],
        }


class _Resolved:
    def __init__(self):
        self.moments = [{"element": "title_lockup"}]
        self.proposed = 1
        self.dropped = []


def test_build_for_reel_forwards_the_project_to_the_render(
        tmp_path, monkeypatch):
    import library.tools.motion_graphics_plan as mg
    import library.tools.operations as operations
    import library.tools.reel_spine as reel_spine

    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "pipeline_output" / "llm_responses").mkdir(parents=True)
    (project / "pipeline_output" / "llm_responses"
     / "reel_semantic_09.json").write_text(
        json.dumps({"motion_graphics_plan": [{"element": "title_lockup"}]}),
        encoding="utf-8")
    captured = _CapturedRender()
    monkeypatch.setattr(
        reel_spine, "spine_for_reel", lambda *a, **k: {"structure": []})
    monkeypatch.setattr(
        mg, "resolve_plan", lambda *a, **k: _Resolved())
    monkeypatch.setattr(
        mg, "plan_segments",
        lambda *a, **k: [{"index": 0, "props": {}}])
    monkeypatch.setattr(
        operations, "get", lambda name: captured)

    segments, _record = sem.build_for_reel(
        _Moment(), {"structure": []}, _ranges(), str(project),
        fps=FPS, width=1080, height=1920)

    assert len(segments) == 1
    assert len(captured.calls) == 1
    _planned, _out_dir, kwargs = captured.calls[0]
    assert kwargs.get("project_folder") == str(project)
