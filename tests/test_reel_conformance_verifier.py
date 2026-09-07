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
    check_caption_hangs,
    check_mixed_speakers,
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
    CaptionsUnavailable,
    check_caption_reference,
    check_duplicate_placements,
    check_format,
    check_item_count,
    check_picture_holes,
    check_plan_describes_timeline,
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

    def test_detects_multiple_gaps(self):
        """Plant three gaps matching Reel 01's pattern from the audit.

        Reel 01 has four clips alternating V2/V1/V2/V1, each placed one
        frame short, creating a one-frame gap at frames 589, 1075, 1140.
        Each track has two clips with a gap between them.
        """
        items = (
            # V2 track: Craig clips at [0,589) and [1076,1140) - gap at 589
            _item("video", 2, 0, 589, speaker="Craig"),
            _item("video", 2, 1076, 1140, speaker="Craig"),
            # V1 track: Akshita clips at [590,1075) and [1141,1269) - gap at 1075
            _item("video", 1, 590, 1075),
            _item("video", 1, 1141, 1269),
        )
        findings = check_picture_holes("Reel 01", items)
        # Three global gaps: 589, 1075, 1140
        assert len(findings) == 3
        assert all(f.finding_class == FindingClass.F1 for f in findings)
        gap_frames = {f.detail["frame"] for f in findings}
        assert 589 in gap_frames
        assert 1075 in gap_frames
        assert 1140 in gap_frames

    def test_no_gap_reports_nothing(self):
        """Contiguous clips produce no findings."""
        items = (
            _item("video", 1, 0, 594),
            _item("video", 1, 594, 1080),
        )
        findings = check_picture_holes("Reel 01", items)
        assert len(findings) == 0

    def test_covered_gap_reports_nothing(self):
        """A gap on V1 is completely covered by a clip on V2."""
        items = (
            _item("video", 1, 0, 100),
            _item("video", 1, 200, 300),
            _item("video", 2, 80, 220), # Covers the 100-200 gap perfectly
        )
        findings = check_picture_holes("Reel 02", items)
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

    def test_no_audio_gap(self):
        items = (
            _item("audio", 1, 0, 594),
            _item("audio", 1, 594, 1080),
        )
        findings = check_audio_holes("Reel 01", items)
        assert len(findings) == 0

    def test_audio_gap_covered_by_other_track_reports_nothing(self):
        """A gap on A1 is not an error if A2 covers it."""
        items = (
            _item("audio", 1, 0, 589),
            _item("audio", 1, 654, 1075),
            _item("audio", 2, 589, 654),  # Fills the A1 gap completely
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
        actual = (_item("video", 2, 24, 47, name="akshita_01"),)
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
        actual = (_item("video", 2, 24, 48, name="akshita_01"),)
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        assert len(findings) == 0


    def test_pairs_on_start_frame_not_list_position(self):
        """The regression this exists to prevent, and it is the whole bug.

        `check_caption_duration` used to read `actual_captions[i]` under a
        comment saying it matched on start frame. That only agrees with
        itself while both lists are the same length. Measured on the
        captain's nineteen reels, 832 cards were planned and 763 placed,
        so after each reel's first unplaced card every remaining pair
        compared one card's plan against a DIFFERENT card's item - 701
        findings and r(planned, placed) = 0.027, which reads as a
        placement defect and is not one.

        Here card 2 is planned and never placed. Index pairing would
        compare card 2's 24 frames against card 3's item and card 3
        against nothing, inventing a delta on a card that is correct.
        """
        planned = (
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="first", speaker="Akshita", frames=24),
            PlannedCaption(start_seconds=2.0, end_seconds=3.0,
                           text="never placed", speaker="Akshita", frames=24),
            PlannedCaption(start_seconds=3.0, end_seconds=4.5,
                           text="third", speaker="Craig", frames=36),
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

    def test_unplaced_card_is_not_absorbed_into_a_shift(self):
        """A plan longer than the timeline reports every missing card."""
        planned = tuple(
            PlannedCaption(start_seconds=float(n), end_seconds=n + 1.0,
                           text=f"card {n}", speaker="Akshita", frames=24)
            for n in range(5))
        actual = (_item("video", 2, 0, 24, name="a"),)
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        f14 = [f for f in findings if f.finding_class == FindingClass.F14]
        assert len(f14) == 4, "four planned cards have no item"
        assert [f.detail["caption_index"] for f in f14] == [1, 2, 3, 4]

    def test_an_item_is_claimed_once(self):
        """Two cards cannot both pair with the same placed item."""
        planned = (
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="one", speaker="Akshita", frames=24),
            PlannedCaption(start_seconds=1.0, end_seconds=2.0,
                           text="two", speaker="Akshita", frames=24),
        )
        actual = (_item("video", 2, 24, 48, name="a"),)
        findings = check_caption_duration("Reel 01", planned, actual, FPS)
        f14 = [f for f in findings if f.finding_class == FindingClass.F14]
        assert len(f14) == 1


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

    def test_speakers_on_numbered_tracks_mapped_correctly(self):
        """Timeline tracks Video 1 and Video 2 are mapped to speakers using the plan."""
        planned = (
            _placement(1, 0.0, 27.30, "Akshita"),
            _placement(2, 0.0, 27.30, "Craig"),
        )
        actual = (
            # No speaker name on the items, simulating bare "Video 1" and "Video 2" tracks
            _item("video", 1, 0, int(27.30 * FPS), speaker="Video 1"),
            _item("video", 2, 0, int(27.32 * FPS), speaker="Video 2"),
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
            _row(0.0, 10.0, "Akshita", "hello world", item_id="uid-1"),
            # Straddling segment - NO resolve_item_id, NO caption
            _row(10.0, 18.0, "Craig", "this straddles a cut"),
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

    def test_row_reaching_past_the_reel_is_clipped_not_dropped(self):
        """A row that starts before the reel still counts inside it.

        This is the case F5 was built for and used to skip: it mapped the
        row's own start through `reel_time`, got None because the row
        begins outside the reel, and `continue`d. Every reel has two such
        rows by construction, at its two boundaries. On the captain's
        nineteen approved reels that dropped 51 rows carrying 364.7s of
        in-reel overlap and reported 29.2s of the 170.1s uncaptioned.
        """
        segments = [
            # Begins 5s BEFORE the reel and runs 5s into it, uncaptioned.
            _row(5.0, 15.0, "Craig",
                 "a row the reel starts in the middle of"),
        ]
        keep_ranges = [(10.0, 20.0)]

        findings = check_caption_coverage(
            "Reel 03", segments, [], keep_ranges, FPS)
        f5 = [f for f in findings if f.finding_class == FindingClass.F5]
        assert len(f5) == 1, "the part inside the reel is what F5 measures"
        assert f5[0].detail["straddling_seconds"] == pytest.approx(5.0, abs=0.05)

    def test_row_an_interior_cut_runs_through_counts_only_what_plays(self):
        """A cut inside a row removes seconds; they are not speech.

        Mapping the two raw endpoints was wrong even when it returned
        numbers - the row mapped to one contiguous reel interval spanning
        the removed take, so seconds the builder had cut out counted as
        speech that needed a caption.
        """
        segments = [
            _row(0.0, 30.0, "Akshita",
                 "a row with a bad take taken out of its middle"),
        ]
        # 10s removed from the middle: the reel plays 20s of this row.
        keep_ranges = [(0.0, 10.0), (20.0, 30.0)]

        findings = check_caption_coverage(
            "Reel 04", segments, [], keep_ranges, FPS)
        f5 = [f for f in findings if f.finding_class == FindingClass.F5]
        assert len(f5) == 1
        assert f5[0].detail["straddling_seconds"] == pytest.approx(
            20.0, abs=0.05), "the cut-out 10s is not uncaptioned speech"

    def test_row_the_reel_does_not_play_is_not_counted(self):
        """"Not in this reel" stays a real answer, not a clipped zero."""
        segments = [
            _row(100.0, 110.0, "Craig",
                 "somewhere else in the episode entirely"),
        ]
        findings = check_caption_coverage(
            "Reel 05", segments, [], [(0.0, 20.0)], FPS)
        assert findings == []

    def test_fully_captioned_no_findings(self):
        """All speech covered by captions produces no findings."""
        segments = [
            _row(0.0, 10.0, "Akshita", "hello world", item_id="uid-1"),
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
            # Straddling segment - no resolve_item_id, speaking throughout
            _row(155.81, 179.97, "Craig",
                 "bunch of terms blogs and whatnot"),
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
            _row(461.26, 477.18, "Akshita",
                 "Yeah so ranking number one on Google"),
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
            _row(155.81, 179.97, "Craig", "this is bound", item_id="uid-1"),
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
            _item("video", 2, 0, int(5 * FPS),
                  name="caption_block_0.mov",
                  source_file="/overlays/caption_block_0.mov"),
            _item("video", 2, int(5 * FPS), int(10 * FPS),
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
            _item("video", 2, 0, int(5 * FPS),
                  name="sub_synced_akshita_body0_0-1000_abc123.mov",
                  source_file=shared_source),
            _item("video", 2, int(5 * FPS), int(10 * FPS),
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

    def test_the_plan_can_be_derived_at_all(self):
        """Would have FAILED on main: `placements` takes (ranges, clips,
        fps) since the frame-exact rewrite and this call site passed two,
        so every plan derivation raised TypeError."""
        plan = self._derive(self._moment())
        assert plan.placements

    def test_the_derived_plan_carries_the_closer_as_its_last_range(self):
        plan = self._derive(self._moment(cta=(468.0, 476.0)))
        assert plan.keep_ranges[-1] == (468.0, 476.0)
        assert plan.keep_ranges[0] == (600.0, 660.0)

    def test_the_derived_plan_lists_the_closers_own_placements(self):
        """F4 compares planned item count to what is on the timeline. A
        plan that omits the closer reports a correct build as defective."""
        without = self._derive(self._moment())
        with_cta = self._derive(self._moment(cta=(468.0, 476.0)))
        assert (len(with_cta.placements)
                == len(without.placements) + 2), "one per picture track"
        assert with_cta.placements[-1].source_in == pytest.approx(476.0 - 8.0,
                                                                  abs=0.1)

    def test_plan_seconds_counts_the_closer(self):
        """`check_plan_length` and `render_check.check_duration` both read
        this; short by the closer's length, every rendered reel fails."""
        plan = self._derive(self._moment(cta=(468.0, 476.0)))
        assert plan.plan_seconds == pytest.approx(68.0)

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

    def test_the_body_only_call_is_unchanged(self):
        """Same check, no ranges: exactly what every reel got before."""
        segments = [
            _row(470.0, 480.0, "Craig", "x"),
        ]
        assert check_boundary_speech(
            "Reel 01", span_start=600.0, span_end=660.0,
            transcript_segments=segments) == []

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

    def test_a_bad_take_seam_is_not_treated_as_a_reel_boundary(self):
        """`keep_ranges` also carries the seams a bad-take cut leaves
        inside the body. Those were never boundaries F8 looked at, and a
        reel with no closer must measure exactly what it measured before
        a closer could exist."""
        from library.tools.reel_conformance_verifier import verify_reel
        plan = _plan(span_start=100.0, span_end=160.0,
                     keep_ranges=((100.0, 120.0), (125.0, 160.0)))
        segments = [
            _row(118.0, 127.0, "Craig",
                 "a sentence straddling the bad-take seam"),
        ]
        result = verify_reel(plan, _timeline(), transcript_segments=segments)
        assert plan.call_to_action is None
        assert [f for f in result.findings
                if f.finding_class == FindingClass.F8] == []

    def test_one_shared_cta_serves_two_reels_in_the_derived_plan(self):
        """Six spoken CTAs closing sixteen reels: the same master range
        is re-derived for each reel, at each reel's own record offset."""
        shared = (468.0, 476.0)
        first = self._derive(self._moment(cta=shared, start=600.0, end=660.0))
        second = self._derive(
            self._moment(cta=shared, start=700.0, end=745.0, number=2))
        assert first.keep_ranges[-1] == second.keep_ranges[-1] == shared
        assert (first.placements[-1].source_in
                == pytest.approx(second.placements[-1].source_in))
        assert (first.placements[-1].record_seconds
                != second.placements[-1].record_seconds)
        assert first.placements[-1].record_seconds == pytest.approx(60.0,
                                                                    abs=0.05)
        assert second.placements[-1].record_seconds == pytest.approx(45.0,
                                                                     abs=0.05)


# ── NO-REFERENCE: the empty expected side ────────────────────────────

class TestNoReferenceRefusesRatherThanSkipping:
    """The vacuous caption gate, and why it is a class of its own.

    Measured 2026-09-05 on the captain's nineteen: the verifier reported
    captions expected/actual as 0/28, 0/39 ... 0/762. It expected zero
    caption cards, found seven hundred and sixty-two, and PASSED - so F2,
    F5, F6 and F7 were all vacuous. The guard was
    `if plan.captions and timeline.caption_items:` and `plan.captions`
    was `()` on every run the verifier had ever made, because
    `reel_subtitles.py` was a parallel module whose output never entered
    the plan the verifier reads.

    The plan side is now derived, but that is not what makes this safe -
    a derivation can break again, and the captioner is moving into step
    4.01. What makes it safe is that the EMPTINESS is now the finding.
    """

    def test_cards_on_the_timeline_with_none_in_the_plan_refuses(self):
        findings = check_caption_reference(
            "Reel 01 - test", (),
            (_item("video", 3, 0, 24), _item("video", 3, 24, 48)))

        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.NO_REFERENCE
        assert findings[0].severity == "error"
        assert findings[0].detail["planned"] == 0
        assert findings[0].detail["actual"] == 2

    def test_cards_in_the_plan_with_none_on_the_timeline_refuses(self):
        """The same hole seen from the other side.

        A plan that asked for cards the build never placed is a reel
        shipped without its captions, and it was equally silent.
        """
        findings = check_caption_reference(
            "Reel 01 - test",
            (PlannedCaption(0.0, 1.0, "hello there", "Akshita", 24),), ())

        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.NO_REFERENCE
        assert findings[0].detail["direction"] == "plan_only"

    def test_a_reel_nobody_captioned_is_not_a_defect(self):
        """`--skip-captions` builds a watchable timeline on purpose."""
        assert check_caption_reference("Reel 01 - test", (), ()) == []

    def test_both_sides_present_is_left_to_f2(self):
        assert check_caption_reference(
            "Reel 01 - test",
            (PlannedCaption(0.0, 1.0, "hello there", "Akshita", 24),),
            (_item("video", 3, 0, 24),)) == []

    def test_could_not_be_asked_is_not_the_same_as_has_none(self):
        """The branch that stops the gate going quiet when the producer moves.

        `_derive_planned_captions` used to import `reel_subtitles` inside
        `except ImportError: return ()`. That module is being deleted -
        the captain ruled it should never have existed and its work
        belongs in step 4.01 - and on the day it went the plan side would
        have become permanently empty, the caption gate would have gone
        back to expecting zero cards while finding hundreds, and every
        test here would still have passed.

        So "could not be asked" is now its own answer and it REFUSES,
        while "has none" stays clean.
        """
        findings = check_caption_reference(
            "Reel 01 - test", (), (),
            unavailable="spine_for_reel is not available in "
                        "library.tools.reel_spine")

        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.NO_REFERENCE
        assert findings[0].severity == "error"
        assert findings[0].detail["direction"] == "unavailable"
        assert "reel_spine" in findings[0].message

    def test_an_unavailable_reference_disables_nothing_silently(self):
        """F2, F5, F6 and F7 must not run on a guess either."""
        result = verify_reel(
            _plan(captions=()),
            _timeline(caption_items=tuple(
                _item("video", 3, i * 24, (i + 1) * 24) for i in range(28))))
        vacuous = [f for f in result.findings
                   if f.finding_class == FindingClass.NO_REFERENCE]
        assert len(vacuous) == 1

        unavailable = verify_reel(
            _plan(captions=()),
            _timeline(caption_items=tuple(
                _item("video", 3, i * 24, (i + 1) * 24) for i in range(28))))
        assert unavailable.errors

    def test_the_deleted_parallel_module_is_not_imported(self):
        """The verifier asks the PIPELINE, not a module beside it.

        Named rather than left to a grep, because the import was LAZY and
        inside a swallowing except - so its absence broke no test and its
        presence broke no test either.
        """
        import inspect

        from library.tools import reel_conformance_verifier as verifier

        source = inspect.getsource(verifier)
        assert "import reel_captions" not in source
        assert "from library.tools.reel_subtitles import" not in source
        assert verifier.REEL_SPINE_PRODUCER[0] == "library.tools.reel_spine"
        # Step 4.01 is reached THROUGH THE REGISTRY, not by importing its
        # module: `Operation.run` resolves the step's own function and
        # never wraps it, so the verifier runs 4.01's code rather than a
        # copy of its rule. Importing the step directly would work and
        # would be the second implementation the registry exists to stop.
        assert verifier.SUBTITLE_PLAN_OPERATION == "subtitles.plan"
        assert "from library.steps.step_4_01" not in source

    def test_captions_unavailable_is_raised_not_returned(self):
        """An empty tuple reads exactly like a --skip-captions reel."""
        from library.tools.reel_conformance_verifier import (
            _derive_planned_captions)

        with pytest.raises(CaptionsUnavailable):
            _derive_planned_captions(None, [(0.0, 10.0)], None, FPS)

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

    def test_a_boundary_in_the_silence_inside_a_row_is_not_a_cut(self):
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

    def test_the_narrowing_is_counted_and_reported(self):
        """A check that looks at less than it used to and does not say so
        reports a clean reel and tells nobody what it declined to look at.
        """
        segments = [_row(22.04, 47.23, "Craig", "two utterances",
                         speaking=((22.04, 24.281), (45.409, 47.23)))]
        findings = check_boundary_speech(
            "Reel 01", span_start=0.125, span_end=44.74,
            transcript_segments=segments)
        warnings = [f for f in findings if f.severity == "warning"]
        assert len(warnings) == 1
        assert warnings[0].detail["in_row_between_words"] == 1

    def test_a_boundary_inside_a_word_is_still_a_cut(self):
        """The gate must still fail on the thing it exists for."""
        segments = [_row(781.75, 804.15, "Craig",
                         "else is broken and that's why",
                         speaking=((781.75, 781.911), (781.971, 782.051),
                                   (782.071, 804.15)))]
        findings = check_boundary_speech(
            "Reel 07", span_start=782.03, span_end=819.13,
            transcript_segments=segments)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].detail["boundary"] == "start"
        assert errors[0].detail["word"]["start"] == pytest.approx(781.971)

    def test_the_finding_names_the_word_and_its_duration(self):
        """This transcript contains a "well" of 19.04s and another of
        34.47s, where the aligner stretched one word across the silence
        before the speaker resumed.  A reader who sees the duration can
        tell a cut sentence from a stretched alignment.
        """
        segments = [_row(155.81, 179.97, "Craig", "... now well that's",
                         speaking=((155.81, 160.714), (160.754, 179.790),
                                   (179.830, 179.97)))]
        findings = check_boundary_speech(
            "Reel 02", span_start=117.79, span_end=179.46,
            transcript_segments=segments)
        errors = [f for f in findings if f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].detail["word"]["duration_seconds"] == pytest.approx(
            19.036, abs=0.001)
        assert "19.04s long" in errors[0].message

    def test_a_row_with_no_word_timings_falls_back_and_says_so(self):
        """A transcript that stopped carrying word timings must not
        silently switch this check off (AGENTS.md 10.4)."""
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

    def test_a_row_with_no_word_timings_is_measured_on_its_envelope(self):
        segments = [_row(0.0, 8.0, "Craig", "no timings", words=())]
        findings = check_caption_coverage(
            "Reel 05", segments, [], [(0.0, 40.0)], FPS)
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        assert errors[0].detail["straddling_seconds"] == pytest.approx(8.0, abs=0.05)
        assert warnings[0].detail["rows_without_word_timings"] == 1

    def test_a_row_the_reel_does_not_play_is_not_an_envelope_row(self):
        """The fallback is reported for rows the reel PLAYS.  Counting
        every wordless row in the episode reported the same number on all
        nineteen reels and meant nothing on any of them."""
        segments = [_row(100.0, 110.0, "Craig", "elsewhere", words=())]
        findings = check_caption_coverage(
            "Reel 05", segments, [], [(0.0, 40.0)], FPS)
        assert findings == []

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
                         speaking=((0.0, 25.0),))]
        result = verify_reel(plan, timeline, transcript_segments=segments)
        f5 = [f for f in result.findings
              if f.finding_class == FindingClass.F5 and f.severity == "error"]
        assert len(f5) == 1, "the plan's card is not on screen; the placed one is"
        assert f5[0].detail["straddling_seconds"] == pytest.approx(24.0, abs=0.1)


class TestPlanMismatchRefusesF4:
    """F4 reported a RE-DERIVED plan's disagreement with the timeline as
    clips the builder had dropped.

    `_derive_plan_from_master` recomputes the picture plan with today's
    `reel_build`.  `MIN_TAKE_SECONDS` was removed from it in 890a61b at
    22:58 on 2026-09-05; the captain's nineteen were built at 14:46 the
    same day.  Today's cut rule finds retakes the build never cut, so the
    derived plan lays down a different number of frames - and F4 read
    that as "planned 6 picture items, found 4 on the timeline", ten
    findings across five reels, none of them a build defect.
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

    def test_the_refusal_names_both_frame_counts(self):
        plan = _plan(span_end=60.0)
        timeline = _timeline(video_items=(_item("video", 1, 0, 599),))
        findings = check_plan_describes_timeline(
            "Reel 03", plan.keep_ranges, timeline.total_frames, FPS)
        assert len(findings) == 1
        assert findings[0].detail["planned_frames"] == 1439
        assert findings[0].detail["timeline_frames"] == 599
        assert findings[0].detail["delta_frames"] == -840

    def test_a_plan_that_describes_the_timeline_lets_f4_run(self):
        """The gate is not weakened: a clip dropped from UNDER another
        one loses no frames, so the plan still describes the timeline and
        F4 is still asked."""
        plan = _plan(span_end=int(25 * FPS) / FPS,
                     placements=(_placement(1, 0.0, 25.0, "Akshita"),
                                 _placement(2, 0.0, 25.0, "Craig")))
        timeline = _timeline(video_items=(_item("video", 1, 0, int(25 * FPS)),))
        result = verify_reel(plan, timeline)
        classes = {f.finding_class for f in result.findings
                   if f.severity == "error"}
        assert FindingClass.PLAN_MISMATCH not in classes
        assert FindingClass.F4 in classes

    def test_the_frame_count_is_the_builders_own_arithmetic(self):
        """No tolerance is chosen because none is needed.  `placements`
        lays down ``round(end * fps) - round(start * fps)`` per range;
        `plan_seconds * fps` is a different number - 491.5 against 491
        here - and comparing THAT would need a band.  Measured on the
        nineteen, the frame-exact counts are EQUAL on all fourteen reels
        whose item counts agree and differ on all five that do not.
        """
        ranges = ((0.0, 10.0), (20.0, 30.5))
        exact = sum(round(b * FPS) - round(a * FPS) for a, b in ranges)
        assert exact == 491
        assert sum(b - a for a, b in ranges) * FPS == pytest.approx(
            491.51, abs=0.01)
        assert check_plan_describes_timeline("R", ranges, 491, FPS) == []
        assert check_plan_describes_timeline("R", ranges, 492, FPS) != []



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

    def test_check_mixed_speakers(self):
        """F17: One card carries two speakers (microphone bleed)."""
        # Craig's mic picks up Akshita's first words as bleed.
        # Craig speaks 1.0 -> 2.0 ("here"), Akshita speaks 2.0 -> 3.0.
        segments = [
            _row(1.0, 2.0, "Craig", "here",
                 words=({"word": "here", "start": 1.0, "end": 2.0,
                         "timed": True},)),
            _row(2.0, 3.0, "Akshita", "yeah so ranking tells google",
                 words=({"word": "yeah", "start": 2.0, "end": 3.0,
                         "timed": True},)),
        ]
        keep_ranges = [(0.0, 10.0)]

        # Defective card spans 1.0 to 3.0, mixing both
        cards = [
            _caption_card(1.0, 3.0, "here yeah so ranking tells google"),
        ]
        findings = check_mixed_speakers(
            "Reel 05", cards, segments, keep_ranges, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.F17

        # Correct cards (one speaker each)
        cards_correct = [
            _caption_card(1.0, 2.0, "here"),
            _caption_card(2.0, 3.0, "yeah so ranking tells google"),
        ]
        findings_correct = check_mixed_speakers(
            "Reel 05", cards_correct, segments, keep_ranges, FPS)
        assert len(findings_correct) == 0

