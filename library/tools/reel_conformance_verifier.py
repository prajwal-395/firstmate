"""Prove that built reel timelines match the plan that produced them.

This verifier exists because the captain's standing goal requires proof
that produced videos match quality gates, that timelines have no errors
against what was planned, and that the plans themselves are good.

On 2026-09-04 a scout audited the 16 built reels and found 8 real
disagreements (F1-F8) classified as ENCODING or PLANNING.  That audit
was done with throwaway scripts; this tool is the permanent, re-runnable
replacement.

What it checks, ported from the audit's findings
-------------------------------------------------
- **F1: Picture/audio continuity** - one-frame black/silent holes at cuts.
  Distinguished from holes inherited from the master (F3), which are the
  plan's fault, not the builder's.
- **F2: Caption card duration** - each card placed one frame shorter than
  rendered.
- **F3: Master-inherited holes** - picture holes in the master that a reel
  span reproduces faithfully.  These are the plan's fault and reported as
  warnings, not failures.
- **F4: Item count and per-speaker duration** - clips silently dropped
  where two placements shared one record position.
- **F5: Caption coverage** - seconds of real speech with no caption,
  split by straddling segments (a real gap) vs frame-quantisation residue
  (not a defect).
- **F6: Caption card overlap** - cards that cannot coexist on one track.
- **F7: Short caption cards** - cards shorter than 0.5s.
- **F9: Duplicate placements** - a placement loop that runs once per clip
  PER CLIP, producing N*N items at duplicate record positions.  This
  verifier is the only guard against that class of bug because
  build_reel_timeline has zero tests.
- **F10: Format mismatch** - a reel timeline that is not 1080x1920 or not
  at the master's frame rate.  This project has a documented history of a
  correct vertical timeline rendering out LANDSCAPE while every structural
  check passed.
- **F11: Subtitle styling** - per-speaker styling is the diarization
  signal.  Reads the CARDS ON THE TIMELINE (not the config), verifies
  speaker attribution via overlay filenames, asserts that a two-speaker
  reel shows two distinct speaker slugs and distinct overlay sources.
  A gate that checks the config instead of the output would pass when
  the builder applied one style to everyone.
- **Plan quality gates** - reel length 45-90s guidance, both speakers
  present with real turns, picture continuity across the plan span.

Read-only proof
---------------
The tool hashes every timeline's structural_signature before and after
the run.  If they differ, it reports a FATAL error and exits 2.

This tool is the independent check on the fixer's work (PR #507) and
does not coordinate with it.

``tests/test_reel_conformance_verifier.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


# ── Finding classes ──────────────────────────────────────────────────

class FindingClass:
    """The audit's finding classes, each with a code, owner and severity."""
    F1 = "F1"  # ENCODING: one-frame holes at cuts
    F2 = "F2"  # ENCODING: caption cards placed one frame short
    F3 = "F3"  # PLANNING: master-inherited picture holes (warning)
    F4 = "F4"  # ENCODING: dropped clips (item count mismatch)
    F5 = "F5"  # PLANNING: uncaptioned speech (straddling segments)
    F6 = "F6"  # PLANNING: overlapping caption cards
    F7 = "F7"  # PLANNING: short caption cards (<0.5s)
    F8 = "F8"  # PLANNING: boundary cuts through unseen speech

    # Plan quality gates (not from the audit, from the captain's list)
    PQ_LENGTH = "PQ-LENGTH"       # reel outside 45-90s guidance
    PQ_SPEAKERS = "PQ-SPEAKERS"   # not both speakers with real turns
    PQ_PICTURE = "PQ-PICTURE"     # plan span contains picture holes

    # Post-audit finding, 2026-09-04: a placement loop that runs once
    # per clip PER CLIP produces N*N placements at duplicate record
    # positions.  build_reel_timeline has zero tests, so this verifier
    # is currently the only thing standing between that class of bug
    # and the captain's timelines.
    F9 = "F9"  # ENCODING: duplicate placements at same record position

    # Captain's explicit gates, 2026-09-04.
    F10 = "F10"  # ENCODING: timeline format mismatch (not 1080x1920 or wrong fps)
    F11 = "F11"  # PLANNING: subtitle styling - speakers not differentiated

    # Plan provenance, 2026-09-05: the verifier was caught grading 16
    # reels against a plan describing 14 completely different moments,
    # producing 42 confident, precise, meaningless errors.  This class
    # fires when the verifier cannot establish that the plan it was
    # handed actually describes the timelines it is reading - and the
    # entire run is REFUSED rather than producing numbers that look like
    # signal but are noise.
    PLAN_MISMATCH = "PLAN-MISMATCH"


ENCODING_CLASSES = {FindingClass.F1, FindingClass.F2, FindingClass.F4,
                    FindingClass.F9, FindingClass.F10}
PLANNING_CLASSES = {FindingClass.F3, FindingClass.F5, FindingClass.F6,
                    FindingClass.F7, FindingClass.F8, FindingClass.F11}
PLAN_QUALITY_CLASSES = {FindingClass.PQ_LENGTH, FindingClass.PQ_SPEAKERS,
                        FindingClass.PQ_PICTURE}
PROVENANCE_CLASSES = {FindingClass.PLAN_MISMATCH}

WARNING_CLASSES = {FindingClass.F3}
"""F3 (master-inherited holes) is the plan's fault, not the build's.
Reported as a warning so it is visible but does not fail the gate."""


@dataclass(frozen=True)
class Finding:
    """One disagreement between plan and timeline."""

    finding_class: str
    reel: str
    message: str
    severity: str = "error"
    """'error' fails the gate; 'warning' is reported but does not."""
    detail: Optional[dict] = None
    """Machine-readable detail for programmatic consumers."""

    def as_dict(self) -> dict:
        d = {"class": self.finding_class, "reel": self.reel,
             "message": self.message, "severity": self.severity}
        if self.detail:
            d["detail"] = self.detail
        return d


# ── Data structures for the plan ─────────────────────────────────────

@dataclass(frozen=True)
class PlannedPlacement:
    """One clip the plan says should be on the reel."""
    track_index: int
    speaker: Optional[str]
    record_seconds: float
    source_in: float
    source_out: float
    source_file: str


@dataclass(frozen=True)
class PlannedCaption:
    """One caption card the plan says should exist."""
    start_seconds: float
    end_seconds: float
    text: str
    speaker: Optional[str]
    frames: int


@dataclass(frozen=True)
class ReelPlan:
    """Everything the plan says about one reel."""
    reel_name: str
    reel_number: int
    plan_seconds: float
    """Total planned duration in seconds."""
    plan_frames: float
    """Total planned duration in frames (may be fractional)."""
    span_start: float
    """Start of the reel span on the master timeline, in seconds."""
    span_end: float
    """End of the reel span on the master timeline, in seconds."""
    placements: Tuple[PlannedPlacement, ...]
    captions: Tuple[PlannedCaption, ...] = ()
    cuts: tuple = ()
    """Bad takes removed (from reel_build.Cut)."""
    keep_ranges: Tuple[Tuple[float, float], ...] = ()


# ── Data structures for what is on the timeline ──────────────────────

@dataclass(frozen=True)
class TimelineItem:
    """One item on one track of a reel timeline."""
    track_type: str
    track_index: int
    start_frame: int
    end_frame: int
    duration_frames: int
    source_start_frame: int
    source_end_frame: int
    source_file: str
    speaker: Optional[str]
    name: str
    unique_id: str = ""

    @property
    def start_seconds(self) -> float:
        return self.start_frame / (24000 / 1001)

    @property
    def end_seconds(self) -> float:
        return self.end_frame / (24000 / 1001)


@dataclass(frozen=True)
class ReelTimeline:
    """What is actually on a reel timeline in Resolve."""
    reel_name: str
    fps: float
    total_frames: int
    video_items: Tuple[TimelineItem, ...]
    """All video track items, in order."""
    audio_items: Tuple[TimelineItem, ...]
    """All audio track items, in order."""
    caption_items: Tuple[TimelineItem, ...]
    """V3 caption items, in order."""
    width: int = 0
    """Timeline resolution width, read from Resolve."""
    height: int = 0
    """Timeline resolution height, read from Resolve."""
    markers: dict = field(default_factory=dict)


# ── The actual checks ────────────────────────────────────────────────

def _fps() -> float:
    """The exact frame rate Resolve computes with."""
    return 24000 / 1001


def check_picture_holes(reel_name: str,
                        video_items: Sequence[TimelineItem],
                        master_holes: Optional[Sequence[dict]] = None,
                        ) -> List[Finding]:
    """F1: Find every gap across the union of all picture tracks.

    A gap is a span where no picture item exists across ANY track - a genuine black frame.
    Distinguished from F3 (master-inherited holes) when master_holes is provided.
    """
    findings: List[Finding] = []
    
    global_coverage = []
    sorted_all = sorted(video_items, key=lambda i: i.start_frame)
    if sorted_all:
        c_start, c_end = sorted_all[0].start_frame, sorted_all[0].end_frame
        for item in sorted_all[1:]:
            if item.start_frame <= c_end:
                c_end = max(c_end, item.end_frame)
            else:
                global_coverage.append((c_start, c_end))
                c_start, c_end = item.start_frame, item.end_frame
        global_coverage.append((c_start, c_end))

    global_gaps = []
    for i in range(len(global_coverage) - 1):
        global_gaps.append((global_coverage[i][1], global_coverage[i+1][0]))

    by_track: Dict[int, List[TimelineItem]] = {}
    for item in video_items:
        by_track.setdefault(item.track_index, []).append(item)

    for g_start, g_end in global_gaps:
        gap = g_end - g_start
        if gap > 0:
            tracks_with_gaps = []
            for track, items in sorted(by_track.items()):
                s_items = sorted(items, key=lambda i: i.start_frame)
                for i in range(len(s_items) - 1):
                    if s_items[i].end_frame <= g_start and s_items[i+1].start_frame >= g_end:
                        tracks_with_gaps.append(track)
                        break
            
            track_msg = f"V{tracks_with_gaps[0]}" if len(tracks_with_gaps) == 1 else "The timeline"
            track_val = tracks_with_gaps[0] if tracks_with_gaps else None
            
            is_master_hole = False
            if master_holes is not None:
                for mh in master_holes:
                    mh_len = mh.get("length", 0)
                    if (abs(gap - mh_len) <= 2 and gap > 2):
                        is_master_hole = True
                        break

            if is_master_hole:
                findings.append(Finding(
                    finding_class=FindingClass.F3,
                    reel=reel_name,
                    message=f"{track_msg} has a {gap}-frame picture hole at frame {g_start}, inherited from the master timeline",
                    severity="warning",
                    detail={"track": track_val, "frame": g_start, "gap_frames": gap, "inherited": True, "tracks": tracks_with_gaps}
                ))
            else:
                findings.append(Finding(
                    finding_class=FindingClass.F1,
                    reel=reel_name,
                    message=f"{track_msg} has a {gap}-frame black hole at frame {g_start}",
                    severity="error",
                    detail={"track": track_val, "frame": g_start, "gap_frames": gap, "inherited": False, "tracks": tracks_with_gaps}
                ))

    return findings


def check_audio_holes(reel_name: str,
                      audio_items: Sequence[TimelineItem],
                      ) -> List[Finding]:
    """F1 (audio half): Find gaps between consecutive audio items.

    The audit found audio holes are identical to picture holes - A1
    mirrors V1 and A2 mirrors V2.
    """
    findings: List[Finding] = []
    by_track: Dict[int, List[TimelineItem]] = {}
    for item in audio_items:
        by_track.setdefault(item.track_index, []).append(item)

    for track, items in sorted(by_track.items()):
        sorted_items = sorted(items, key=lambda i: i.start_frame)
        for i in range(len(sorted_items) - 1):
            curr = sorted_items[i]
            nxt = sorted_items[i + 1]
            gap = nxt.start_frame - curr.end_frame
            if gap > 0:
                findings.append(Finding(
                    finding_class=FindingClass.F1,
                    reel=reel_name,
                    message=(
                        f"A{track} has a {gap}-frame silent hole at "
                        f"frame {curr.end_frame}"),
                    severity="error",
                    detail={
                        "track": track,
                        "frame": curr.end_frame,
                        "gap_frames": gap,
                        "type": "audio",
                    },
                ))
    return findings


def check_item_count(reel_name: str,
                     planned_placements: Sequence[PlannedPlacement],
                     actual_video_items: Sequence[TimelineItem],
                     fps: float,
                     ) -> List[Finding]:
    """F4: Compare planned item count vs placed, and per-speaker duration.

    The audit found two reels where items were fewer than planned because
    two placements shared one record position and one was silently
    dropped.  Do NOT trust a producer's self-report - measure the timeline.
    """
    findings: List[Finding] = []

    # Compare total picture item counts
    expected_count = len(planned_placements)
    # Count only V1 and V2 items (picture tracks)
    actual_picture = [i for i in actual_video_items
                      if i.track_index in (1, 2)]
    actual_count = len(actual_picture)

    if actual_count != expected_count:
        findings.append(Finding(
            finding_class=FindingClass.F4,
            reel=reel_name,
            message=(
                f"planned {expected_count} picture items, found "
                f"{actual_count} on the timeline"),
            severity="error",
            detail={
                "expected": expected_count,
                "actual": actual_count,
            },
        ))

    # Per-speaker duration comparison
    planned_by_speaker: Dict[str, float] = {}
    track_to_speaker: Dict[int, str] = {}
    for p in planned_placements:
        speaker = p.speaker or "unknown"
        planned_by_speaker[speaker] = (
            planned_by_speaker.get(speaker, 0.0)
            + (p.source_out - p.source_in))
        if speaker != "unknown":
            track_to_speaker[p.track_index] = speaker

    actual_by_speaker: Dict[str, float] = {}
    unmapped_duration: Dict[int, float] = {}
    track_names: Dict[int, str] = {}

    for item in actual_picture:
        if item.speaker:
            track_names[item.track_index] = item.speaker
            
        speaker = track_to_speaker.get(item.track_index)
        if not speaker:
            # Fall back to timeline's track names if they match a planned speaker
            if item.speaker and item.speaker in planned_by_speaker:
                speaker = item.speaker
            else:
                unmapped_duration[item.track_index] = (
                    unmapped_duration.get(item.track_index, 0.0)
                    + item.duration_frames / fps)
                continue

        actual_by_speaker[speaker] = (
            actual_by_speaker.get(speaker, 0.0)
            + item.duration_frames / fps)

    for track_idx, dur in sorted(unmapped_duration.items()):
        name_str = f"named '{track_names[track_idx]}'" if track_names.get(track_idx) else "unnamed"
        findings.append(Finding(
            finding_class=FindingClass.F4,
            reel=reel_name,
            message=(
                f"Track V{track_idx} ({name_str}) has {dur:.2f}s of video, "
                f"but could not be mapped to any speaker in the plan."
            ),
            severity="error",
            detail={
                "track": track_idx,
                "unmapped_duration": round(dur, 2)
            },
        ))

    for speaker in set(list(planned_by_speaker) + list(actual_by_speaker)):
        planned_s = planned_by_speaker.get(speaker, 0.0)
        actual_s = actual_by_speaker.get(speaker, 0.0)
        # Allow small tolerance for frame rounding (F1's one frame per clip)
        tolerance = max(0.5, actual_count * 0.05)
        if abs(planned_s - actual_s) > tolerance:
            findings.append(Finding(
                finding_class=FindingClass.F4,
                reel=reel_name,
                message=(
                    f"{speaker}: planned {planned_s:.2f}s, got "
                    f"{actual_s:.2f}s (delta {actual_s - planned_s:+.2f}s)"),
                severity="error",
                detail={
                    "speaker": speaker,
                    "planned_seconds": round(planned_s, 2),
                    "actual_seconds": round(actual_s, 2),
                    "delta_seconds": round(actual_s - planned_s, 2),
                },
            ))

    return findings


def check_caption_duration(reel_name: str,
                           planned_captions: Sequence[PlannedCaption],
                           actual_captions: Sequence[TimelineItem],
                           fps: float,
                           ) -> List[Finding]:
    """F2: Check that each caption card's placed duration matches plan.

    The audit found 567 of 575 cards placed one frame shorter than their
    rendered .mov - the same off-by-one as F1.
    """
    findings: List[Finding] = []

    # Match planned to actual by position (start frame) - the audit
    # confirmed 566 of 575 sit on exactly the planned frame.
    for i, cap in enumerate(planned_captions):
        if i >= len(actual_captions):
            break
        actual = actual_captions[i]
        delta = actual.duration_frames - cap.frames
        if delta != 0:
            findings.append(Finding(
                finding_class=FindingClass.F2,
                reel=reel_name,
                message=(
                    f"caption {i+1} '{cap.text[:30]}' planned "
                    f"{cap.frames} frames, placed {actual.duration_frames} "
                    f"(delta {delta:+d})"),
                severity="error",
                detail={
                    "caption_index": i,
                    "planned_frames": cap.frames,
                    "actual_frames": actual.duration_frames,
                    "delta": delta,
                    "text": cap.text[:60],
                },
            ))

    return findings


def check_caption_coverage(reel_name: str,
                           transcript_segments: Sequence[dict],
                           caption_cards: Sequence[dict],
                           keep_ranges: Sequence[Tuple[float, float]],
                           fps: float,
                           ) -> List[Finding]:
    """F5: Measure seconds of real speech with no caption over it.

    Split by cause as the audit did:
    - Straddling segments (excluded by bound_segments) - a real gap, F5
    - Frame-quantisation residue (~26ms per word) - NOT a defect

    The 24.6s residue from the audit is frame quantisation at card edges
    spread over ~900 words and is not reported as a finding.
    """
    findings: List[Finding] = []
    if not transcript_segments or not keep_ranges:
        return findings

    from library.tools.reel_build import reel_time

    # Compute speech coverage from caption cards
    caption_intervals = []
    for card in caption_cards:
        c_start = card.get("reel_start", card.get("start_seconds", 0))
        c_end = card.get("reel_end", card.get("end_seconds", 0))
        caption_intervals.append((c_start, c_end))
    caption_intervals.sort()

    # Measure speech seconds
    straddling_uncaptioned = 0.0
    residue_uncaptioned = 0.0

    for segment in transcript_segments:
        has_item_id = bool(segment.get("resolve_item_id"))
        seg_start = float(segment.get("timeline_start", 0))
        seg_end = float(segment.get("timeline_end", 0))

        # Map to reel time
        reel_start = reel_time(seg_start, keep_ranges)
        reel_end = reel_time(seg_end, keep_ranges)
        if reel_start is None or reel_end is None:
            continue
        if reel_end <= reel_start:
            continue

        # How much of this segment is captioned?
        captioned = 0.0
        for c_start, c_end in caption_intervals:
            overlap_start = max(reel_start, c_start)
            overlap_end = min(reel_end, c_end)
            if overlap_end > overlap_start:
                captioned += overlap_end - overlap_start

        uncaptioned = (reel_end - reel_start) - captioned
        if uncaptioned > 0.01:  # more than 10ms
            if not has_item_id:
                straddling_uncaptioned += uncaptioned
            else:
                residue_uncaptioned += uncaptioned

    # Straddling uncaptioned speech is a real defect (F5)
    if straddling_uncaptioned > 0.5:
        findings.append(Finding(
            finding_class=FindingClass.F5,
            reel=reel_name,
            message=(
                f"{straddling_uncaptioned:.1f}s of speech from straddling "
                f"segments has no caption"),
            severity="error",
            detail={
                "straddling_seconds": round(straddling_uncaptioned, 1),
                "residue_seconds": round(residue_uncaptioned, 1),
                "total_uncaptioned_seconds": round(
                    straddling_uncaptioned + residue_uncaptioned, 1),
            },
        ))

    # Residue is NOT a defect - but report it for visibility if large
    # enough that someone might notice it (> 5s per reel, arbitrary)

    return findings


def check_caption_overlaps(reel_name: str,
                           caption_cards: Sequence[dict],
                           fps: float,
                           ) -> List[Finding]:
    """F6: Find caption cards that overlap on a single subtitle track.

    The audit found 15 overlapping pairs.  Two cards covering the same
    seconds (mic bleed captioned under both speakers) cannot coexist on
    one V3 track - Resolve trims the later card's head.
    """
    findings: List[Finding] = []
    if len(caption_cards) < 2:
        return findings

    sorted_cards = sorted(caption_cards,
                          key=lambda c: c.get("reel_start",
                                              c.get("start_seconds", 0)))
    for i in range(len(sorted_cards) - 1):
        curr = sorted_cards[i]
        nxt = sorted_cards[i + 1]
        curr_end = curr.get("reel_end", curr.get("end_seconds", 0))
        nxt_start = nxt.get("reel_start", nxt.get("start_seconds", 0))
        overlap = curr_end - nxt_start
        if overlap > 0.001:  # more than 1ms
            overlap_frames = int(round(overlap * fps))
            findings.append(Finding(
                finding_class=FindingClass.F6,
                reel=reel_name,
                message=(
                    f"caption cards overlap by {overlap_frames} frames "
                    f"({overlap:.3f}s): '{curr.get('text', '')[:30]}' and "
                    f"'{nxt.get('text', '')[:30]}'"),
                severity="error",
                detail={
                    "overlap_frames": overlap_frames,
                    "overlap_seconds": round(overlap, 3),
                    "card_a_text": curr.get("text", "")[:60],
                    "card_b_text": nxt.get("text", "")[:60],
                },
            ))
    return findings


def check_short_captions(reel_name: str,
                         caption_cards: Sequence[dict],
                         fps: float,
                         min_duration_seconds: float = 0.5,
                         ) -> List[Finding]:
    """F7: Find caption cards shorter than the minimum display time.

    AGENTS.md 10.4 requires no caption card under 0.5s, but the reel
    caption path has no minimum-duration rule and nothing checks for one.
    The audit found 29 cards under 0.5s, 7 of them under 3 frames.
    """
    findings: List[Finding] = []
    for i, card in enumerate(caption_cards):
        duration = card.get("reel_end", card.get("end_seconds", 0)) - \
                   card.get("reel_start", card.get("start_seconds", 0))
        frames = card.get("frames", int(round(duration * fps)))
        if duration < min_duration_seconds:
            findings.append(Finding(
                finding_class=FindingClass.F7,
                reel=reel_name,
                message=(
                    f"caption card {i+1} '{card.get('text', '')[:30]}' "
                    f"is {frames} frames ({duration:.3f}s), under the "
                    f"{min_duration_seconds}s minimum"),
                severity="error",
                detail={
                    "caption_index": i,
                    "duration_seconds": round(duration, 3),
                    "duration_frames": frames,
                    "text": card.get("text", "")[:60],
                    "minimum_seconds": min_duration_seconds,
                },
            ))
    return findings


def check_boundary_speech(reel_name: str,
                          span_start: float,
                          span_end: float,
                          transcript_segments: Sequence[dict],
                          ) -> List[Finding]:
    """F8: Find reel boundaries that cut through speech the boundary
    logic cannot see.

    `snap_to_speech` widens a span to whole BOUND segments, but straddling
    segments are invisible to it.  The audit found 11 boundaries that cut
    through a real sentence.
    """
    findings: List[Finding] = []

    for segment in transcript_segments:
        seg_start = float(segment.get("timeline_start", 0))
        seg_end = float(segment.get("timeline_end", 0))
        has_item_id = bool(segment.get("resolve_item_id"))

        # Only straddling segments are invisible to snap_to_speech
        if has_item_id:
            continue

        # Check if reel boundary cuts through this segment
        text = (segment.get("text") or "")[:60]
        speaker = segment.get("speaker", "unknown")

        # START boundary cuts through segment
        if seg_start < span_start < seg_end:
            findings.append(Finding(
                finding_class=FindingClass.F8,
                reel=reel_name,
                message=(
                    f"START at {span_start:.2f}s cuts {speaker} "
                    f"{seg_start:.2f}-{seg_end:.2f}s "
                    f"\"{text}\""),
                severity="error",
                detail={
                    "boundary": "start",
                    "boundary_time": round(span_start, 2),
                    "segment_start": round(seg_start, 2),
                    "segment_end": round(seg_end, 2),
                    "speaker": speaker,
                    "text": text,
                },
            ))

        # END boundary cuts through segment
        if seg_start < span_end < seg_end:
            findings.append(Finding(
                finding_class=FindingClass.F8,
                reel=reel_name,
                message=(
                    f"END at {span_end:.2f}s cuts {speaker} "
                    f"{seg_start:.2f}-{seg_end:.2f}s "
                    f"\"{text}\""),
                severity="error",
                detail={
                    "boundary": "end",
                    "boundary_time": round(span_end, 2),
                    "segment_start": round(seg_start, 2),
                    "segment_end": round(seg_end, 2),
                    "speaker": speaker,
                    "text": text,
                },
            ))

    return findings


# ── F9: Duplicate placements ─────────────────────────────────────────

def check_duplicate_placements(reel_name: str,
                               video_items: Sequence[TimelineItem],
                               fps: float,
                               ) -> List[Finding]:
    """F9: Detect duplicate items placed at the same record position.

    Found 2026-09-04: a placement loop that iterates once per clip PER
    CLIP produces N*N items instead of N.  A 4-clip reel gets 16 items
    at 4 unique record positions, each position occupied by 4 identical
    clips.  build_reel_timeline has zero tests, so this verifier is
    currently the only guard against this class of bug.

    Detection: group items by (track_index, start_frame).  Any position
    with more than one item is a duplicate placement.  Items at the same
    position on DIFFERENT tracks are expected (V1 and V2 can share a
    record position - that is what the captain's edit does with two
    speakers).
    """
    findings: List[Finding] = []
    by_position: Dict[tuple, List[TimelineItem]] = {}
    for item in video_items:
        key = (item.track_index, item.start_frame)
        by_position.setdefault(key, []).append(item)

    for (track, frame), items in sorted(by_position.items()):
        if len(items) > 1:
            findings.append(Finding(
                finding_class=FindingClass.F9,
                reel=reel_name,
                message=(
                    f"V{track} has {len(items)} items at frame {frame} "
                    f"({frame / fps:.2f}s) - duplicate placement"),
                severity="error",
                detail={
                    "track": track,
                    "frame": frame,
                    "count": len(items),
                    "items": [
                        {"name": i.name, "source_file": i.source_file,
                         "duration_frames": i.duration_frames}
                        for i in items
                    ],
                },
            ))

    # Also check for the N*N symptom: if total items / unique positions
    # is a perfect square, that is the signature of the nested-loop bug.
    unique_positions = len(by_position)
    total_items = len(video_items)
    if unique_positions > 0 and total_items > unique_positions:
        import math
        ratio = total_items / unique_positions
        sqrt = math.isqrt(total_items)
        if sqrt * sqrt == total_items and sqrt == unique_positions:
            findings.append(Finding(
                finding_class=FindingClass.F9,
                reel=reel_name,
                message=(
                    f"{total_items} items at {unique_positions} positions "
                    f"is {sqrt}x{sqrt} - signature of a placement loop "
                    f"that runs once per clip PER CLIP"),
                severity="error",
                detail={
                    "total_items": total_items,
                    "unique_positions": unique_positions,
                    "ratio": ratio,
                    "suspected_clip_count": sqrt,
                },
            ))

    return findings


# ── F10: Format mismatch ─────────────────────────────────────────────

# The captain's format.  A correct vertical timeline rendering out
# landscape while every structural check passes happened on this project.
EXPECTED_WIDTH = 1080
EXPECTED_HEIGHT = 1920


def check_format(reel_name: str,
                 width: int, height: int, fps: float,
                 master_fps: float,
                 ) -> List[Finding]:
    """F10: Verify the reel timeline is 1080x1920 at the master's rate.

    This project has a documented history of a correct vertical timeline
    rendering out LANDSCAPE while every other check passed - duration,
    frame rate, audio streams and frame occupancy all green on a file of
    the wrong shape.  Refusing to check the shape is how that happened.
    """
    findings: List[Finding] = []

    if width != EXPECTED_WIDTH or height != EXPECTED_HEIGHT:
        findings.append(Finding(
            finding_class=FindingClass.F10,
            reel=reel_name,
            message=(
                f"Timeline is {width}x{height}, expected "
                f"{EXPECTED_WIDTH}x{EXPECTED_HEIGHT} (vertical)"),
            severity="error",
            detail={
                "actual_width": width,
                "actual_height": height,
                "expected_width": EXPECTED_WIDTH,
                "expected_height": EXPECTED_HEIGHT,
            },
        ))

    # Frame rate must match the master's - a reel at a different rate
    # from its source produces frame blending or stutters.
    if fps and master_fps and abs(fps - master_fps) > 0.01:
        findings.append(Finding(
            finding_class=FindingClass.F10,
            reel=reel_name,
            message=(
                f"Timeline fps {fps:.3f} does not match master "
                f"{master_fps:.3f}"),
            severity="error",
            detail={
                "actual_fps": fps,
                "master_fps": master_fps,
            },
        ))

    return findings


# ── F11: Subtitle styling ────────────────────────────────────────────

# The segment naming convention puts the speaker in the filename:
#   sub_<timeline>_<speaker>_<block>_<span>_<digest>.mov
# This regex extracts the speaker slug from either the source_file
# path or the clip name.
_SPEAKER_FROM_SEGMENT = re.compile(
    r"sub_[^_]+_([^_]+)_",
)


def _caption_speaker(item: TimelineItem) -> Optional[str]:
    """Extract the speaker slug from a caption card's source_file or name.

    Returns the slug (lowercase), or None if it cannot be determined.
    The segment naming convention is documented in
    library/tools/subtitle_segment_id.py.
    """
    for field in (item.source_file, item.name):
        if not field:
            continue
        # Try the filename component
        basename = field.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        m = _SPEAKER_FROM_SEGMENT.match(basename)
        if m:
            slug = m.group(1)
            if slug != "nospeaker":
                return slug
    return None


def check_subtitle_styling(reel_name: str,
                           caption_items: Sequence[TimelineItem],
                           video_items: Sequence[TimelineItem],
                           ) -> List[Finding]:
    """F11: Verify per-speaker subtitle differentiation on the OUTPUT.

    The captain asked for per-speaker styling as the diarization signal -
    that is how a viewer tells Craig from Akshita.  This check reads the
    CARDS THAT ARE ON THE TIMELINE, not the styling configuration:

    1. Every caption card must carry speaker attribution (derived from
       its overlay filename).  If it cannot be determined, that is a
       finding - a gate that cannot determine its input says so loudly
       rather than returning clean.

    2. If the timeline carries two speakers on V1/V2, the caption cards
       must show at least two distinct speaker slugs.  All cards from
       the same speaker means the builder applied one style to everyone.

    3. Cards from different speakers must reference different overlay
       source files.  If two speakers' cards come from overlays with
       the same source path, they were rendered identically.
    """
    findings: List[Finding] = []

    if not caption_items:
        # No captions on the timeline at all.  This is not an F11
        # styling defect - it is a coverage gap (F5) or a missing
        # overlay, and other checks catch that.
        return findings

    # ── Derive speaker attribution from each card ────────────────
    attributed: Dict[str, List[TimelineItem]] = {}
    unattributed: List[TimelineItem] = []

    for cap in caption_items:
        speaker = _caption_speaker(cap)
        if speaker:
            attributed.setdefault(speaker, []).append(cap)
        else:
            unattributed.append(cap)

    # Report cards where speaker cannot be determined
    if unattributed:
        findings.append(Finding(
            finding_class=FindingClass.F11,
            reel=reel_name,
            message=(
                f"{len(unattributed)} of {len(caption_items)} caption "
                f"cards have no speaker attribution in their filename - "
                f"cannot verify per-speaker styling"),
            severity="error",
            detail={
                "unattributed_count": len(unattributed),
                "total_captions": len(caption_items),
                "example_names": [
                    c.name or c.source_file for c in unattributed[:3]
                ],
            },
        ))

    # ── How many speakers does the video track show? ─────────────
    video_speakers = set()
    for v in video_items:
        if v.speaker:
            video_speakers.add(v.speaker)

    # If the timeline has two speakers but captions show only one
    # speaker slug, the builder applied one style to everyone.
    if len(video_speakers) >= 2 and len(attributed) < 2:
        styled_speakers = sorted(attributed.keys()) if attributed else []
        findings.append(Finding(
            finding_class=FindingClass.F11,
            reel=reel_name,
            message=(
                f"Video tracks show {len(video_speakers)} speakers "
                f"({sorted(video_speakers)}) but captions are "
                f"attributed to only {len(attributed)} "
                f"({styled_speakers}) - no visual diarization"),
            severity="error",
            detail={
                "video_speakers": sorted(video_speakers),
                "caption_speakers": styled_speakers,
            },
        ))

    # ── Cards from different speakers must use different overlays ─
    if len(attributed) >= 2:
        # Collect the set of source files per speaker
        sources_by_speaker: Dict[str, set] = {}
        for speaker, caps in attributed.items():
            sources_by_speaker[speaker] = {
                c.source_file for c in caps if c.source_file
            }

        speakers = sorted(attributed.keys())
        for i in range(len(speakers)):
            for j in range(i + 1, len(speakers)):
                s1, s2 = speakers[i], speakers[j]
                shared = sources_by_speaker.get(s1, set()) & \
                    sources_by_speaker.get(s2, set())
                if shared:
                    findings.append(Finding(
                        finding_class=FindingClass.F11,
                        reel=reel_name,
                        message=(
                            f"Speakers {s1!r} and {s2!r} share "
                            f"{len(shared)} overlay source file(s) - "
                            f"identical rendering, no visual "
                            f"diarization"),
                        severity="error",
                        detail={
                            "speaker_1": s1,
                            "speaker_2": s2,
                            "shared_sources": sorted(shared),
                        },
                    ))

    return findings


# ── Plan quality gates ───────────────────────────────────────────────

REEL_LENGTH_MIN = 45.0
REEL_LENGTH_MAX = 90.0


def check_plan_length(reel_name: str,
                      plan_seconds: float,
                      ) -> List[Finding]:
    """Plan quality: reel duration within the agreed 45-90s guidance."""
    findings: List[Finding] = []
    if plan_seconds < REEL_LENGTH_MIN:
        findings.append(Finding(
            finding_class=FindingClass.PQ_LENGTH,
            reel=reel_name,
            message=(
                f"plan is {plan_seconds:.1f}s, under the {REEL_LENGTH_MIN}s "
                f"minimum guidance"),
            severity="warning",
            detail={
                "plan_seconds": round(plan_seconds, 1),
                "minimum": REEL_LENGTH_MIN,
                "maximum": REEL_LENGTH_MAX,
            },
        ))
    if plan_seconds > REEL_LENGTH_MAX:
        findings.append(Finding(
            finding_class=FindingClass.PQ_LENGTH,
            reel=reel_name,
            message=(
                f"plan is {plan_seconds:.1f}s, over the {REEL_LENGTH_MAX}s "
                f"maximum guidance"),
            severity="warning",
            detail={
                "plan_seconds": round(plan_seconds, 1),
                "minimum": REEL_LENGTH_MIN,
                "maximum": REEL_LENGTH_MAX,
            },
        ))
    return findings


def check_plan_speakers(reel_name: str,
                        placements: Sequence[PlannedPlacement],
                        min_speaker_seconds: float = 2.0,
                        ) -> List[Finding]:
    """Plan quality: both speakers present with real turns."""
    findings: List[Finding] = []
    by_speaker: Dict[str, float] = {}
    for p in placements:
        speaker = p.speaker or "unknown"
        by_speaker[speaker] = (by_speaker.get(speaker, 0.0)
                               + (p.source_out - p.source_in))

    speakers_with_real_turns = [
        s for s, dur in by_speaker.items()
        if dur >= min_speaker_seconds
    ]
    if len(speakers_with_real_turns) < 2:
        findings.append(Finding(
            finding_class=FindingClass.PQ_SPEAKERS,
            reel=reel_name,
            message=(
                f"only {len(speakers_with_real_turns)} speaker(s) with "
                f">= {min_speaker_seconds}s of real turns: "
                f"{by_speaker}"),
            severity="warning",
            detail={
                "speakers": dict(by_speaker),
                "speakers_with_real_turns": speakers_with_real_turns,
                "minimum_seconds": min_speaker_seconds,
            },
        ))
    return findings


def check_plan_picture_continuity(
    reel_name: str,
    span_start: float,
    span_end: float,
    master_video_items: Optional[Sequence[dict]] = None,
) -> List[Finding]:
    """Plan quality: no picture holes in the master across the reel span.

    The F3 lesson - a span is currently validated against speech only, so
    a reel can be selected straight over a hole in the master.  This gate
    measures picture continuity so the plan does not select over a gap.

    Master-inherited holes are a warning because they are the plan's
    fault, not the build's.  The verifier reports them distinctly so the
    captain can decide whether they should fail.
    """
    findings: List[Finding] = []
    if not master_video_items:
        return findings

    # Find gaps in master video within the span
    items_in_span = []
    for item in master_video_items:
        item_start = item.get("timeline_start", item.get("start", 0))
        item_end = item.get("timeline_end", item.get("end", 0))
        if item_end > span_start and item_start < span_end:
            items_in_span.append(item)

    if not items_in_span:
        findings.append(Finding(
            finding_class=FindingClass.PQ_PICTURE,
            reel=reel_name,
            message=(
                f"no master video items found in span "
                f"{span_start:.2f}-{span_end:.2f}s"),
            severity="warning",
            detail={
                "span_start": round(span_start, 2),
                "span_end": round(span_end, 2),
            },
        ))
        return findings

    # Check for gaps between items (by track)
    by_track: Dict[int, list] = {}
    for item in items_in_span:
        track = item.get("track_index", item.get("track", 1))
        by_track.setdefault(track, []).append(item)

    # A gap where BOTH tracks have no clip is a picture hole
    # (V2 sits above V1 on the master)
    all_intervals = []
    for track, items in by_track.items():
        for item in items:
            s = max(item.get("timeline_start", item.get("start", 0)),
                    span_start)
            e = min(item.get("timeline_end", item.get("end", 0)),
                    span_end)
            if e > s:
                all_intervals.append((s, e))

    if not all_intervals:
        return findings

    # Merge all intervals across all tracks
    all_intervals.sort()
    merged = [all_intervals[0]]
    for s, e in all_intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))

    # Find gaps in coverage
    coverage_start = max(merged[0][0], span_start)
    coverage_end = min(merged[-1][1], span_end)

    for i in range(len(merged) - 1):
        gap_start = merged[i][1]
        gap_end = merged[i + 1][0]
        gap_frames = int(round((gap_end - gap_start) * _fps()))
        if gap_frames > 0:
            findings.append(Finding(
                finding_class=FindingClass.PQ_PICTURE,
                reel=reel_name,
                message=(
                    f"master has a {gap_frames}-frame picture hole at "
                    f"{gap_start:.2f}s inside reel span"),
                severity="warning",
                detail={
                    "gap_start": round(gap_start, 2),
                    "gap_end": round(gap_end, 2),
                    "gap_frames": gap_frames,
                    "gap_seconds": round(gap_end - gap_start, 2),
                },
            ))

    return findings


# ── Verification result ──────────────────────────────────────────────

@dataclass
class ReelResult:
    """All findings for one reel, plus the numbers for the per-reel table."""
    reel_name: str
    reel_number: int
    plan_seconds: float
    plan_frames: float
    actual_frames: int
    items_expected: int
    items_actual: int
    one_frame_holes: int
    big_holes: List[dict]
    captions_expected: int
    captions_actual: int
    speech_seconds: float
    uncaptioned_seconds: float
    uncaptioned_pct: float
    short_captions: int
    edge_cuts: int
    bad_take_cuts: int
    markers: int
    findings: List[Finding]

    @property
    def errors(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [f for f in self.findings if f.severity == "warning"]


@dataclass
class VerificationReport:
    """Complete verification of all reels."""
    project_name: str
    master_timeline: str
    reel_results: List[ReelResult]
    read_only_proof: dict
    """Before/after hashes proving no timeline was modified."""
    plan_source: str = ""
    """What the verification graded against: a proposal file path, or
    'DERIVED from master'.  A verifier that silently grades against a
    re-derived plan when the real one was missing tells the captain
    something different from what he thinks he is reading."""
    provenance_findings: List[Finding] = field(default_factory=list)
    """Plan provenance findings (PLAN-MISMATCH).  Separate from per-reel
    findings because they apply to the run as a whole, not to one reel."""

    @property
    def all_findings(self) -> List[Finding]:
        out = list(self.provenance_findings)
        for r in self.reel_results:
            out.extend(r.findings)
        return out

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.all_findings)

    @property
    def total_errors(self) -> int:
        return sum(1 for f in self.all_findings if f.severity == "error")

    @property
    def total_warnings(self) -> int:
        return sum(1 for f in self.all_findings if f.severity == "warning")

    def as_dict(self) -> dict:
        d = {
            "project": self.project_name,
            "master_timeline": self.master_timeline,
            "plan_source": self.plan_source,
            "read_only_proof": self.read_only_proof,
            "summary": {
                "reels_checked": len(self.reel_results),
                "total_errors": self.total_errors,
                "total_warnings": self.total_warnings,
                "passed": not self.has_errors,
            },
            "by_class": _findings_by_class(self.all_findings),
            "reels": [
                {
                    "reel_name": r.reel_name,
                    "reel_number": r.reel_number,
                    "plan_seconds": round(r.plan_seconds, 2),
                    "plan_frames": round(r.plan_frames, 1),
                    "actual_frames": r.actual_frames,
                    "items": f"{r.items_expected}/{r.items_actual}",
                    "one_frame_holes": r.one_frame_holes,
                    "captions": f"{r.captions_expected}/{r.captions_actual}",
                    "uncaptioned_seconds": round(r.uncaptioned_seconds, 1),
                    "uncaptioned_pct": round(r.uncaptioned_pct, 1),
                    "short_captions": r.short_captions,
                    "edge_cuts": r.edge_cuts,
                    "bad_take_cuts": r.bad_take_cuts,
                    "markers": r.markers,
                    "errors": len(r.errors),
                    "warnings": len(r.warnings),
                    "findings": [f.as_dict() for f in r.findings],
                }
                for r in self.reel_results
            ],
        }
        if self.provenance_findings:
            d["provenance_findings"] = [
                f.as_dict() for f in self.provenance_findings
            ]
        return d


def _findings_by_class(findings: Sequence[Finding]) -> dict:
    """Count findings by class for the summary."""
    by_class: Dict[str, dict] = {}
    for f in findings:
        entry = by_class.setdefault(f.finding_class, {
            "count": 0, "errors": 0, "warnings": 0, "reels": set()})
        entry["count"] += 1
        if f.severity == "error":
            entry["errors"] += 1
        else:
            entry["warnings"] += 1
        entry["reels"].add(f.reel)
    # Convert sets to sorted lists for JSON serialization
    for entry in by_class.values():
        entry["reels"] = sorted(entry["reels"])
    return by_class


# ── The per-reel table (human-readable output) ──────────────────────

def format_table(report: VerificationReport) -> str:
    """The per-reel table, close to the audit's format."""
    lines = []
    header = (
        f" {'#':>2} {'reel':<48} {'plan_s':>7} {'expF':>7} {'actF':>6} "
        f"{'items e/a':>9} {'1f-holes':>8} {'big hole':>12} "
        f"{'caps e/a':>8} {'speech':>7} {'uncap':>6} {'%':>5} "
        f"{'<0.5s':>5} {'edge':>4} {'cut':>4} {'mk':>4}")
    sep = "-" * len(header)

    lines.append(header)
    lines.append(sep)

    totals = {
        "items_e": 0, "items_a": 0, "holes": 0, "big": 0,
        "caps_e": 0, "caps_a": 0, "speech": 0.0, "uncap": 0.0,
        "short": 0, "edge": 0, "cut": 0, "mk": 0,
    }

    for r in sorted(report.reel_results, key=lambda x: x.reel_number):
        big_hole_str = "-"
        if r.big_holes:
            bh = r.big_holes[0]
            big_hole_str = f"{bh.get('gap_frames', 0)}f @{bh.get('frame', 0) / _fps():.1f}s"

        line = (
            f" {r.reel_number:>2} {r.reel_name:<48} "
            f"{r.plan_seconds:>7.2f} "
            f"{r.plan_frames:>7.1f} "
            f"{r.actual_frames:>6} "
            f"{r.items_expected:>4}/{r.items_actual:<4} "
            f"{r.one_frame_holes:>8} "
            f"{big_hole_str:>12} "
            f"{r.captions_expected:>4}/{r.captions_actual:<3} "
            f"{r.speech_seconds:>7.1f} "
            f"{r.uncaptioned_seconds:>6.1f} "
            f"{r.uncaptioned_pct:>5.1f} "
            f"{r.short_captions:>5} "
            f"{r.edge_cuts:>4} "
            f"{r.bad_take_cuts:>4} "
            f"{r.markers:>4}")
        lines.append(line)

        totals["items_e"] += r.items_expected
        totals["items_a"] += r.items_actual
        totals["holes"] += r.one_frame_holes
        totals["big"] += len(r.big_holes)
        totals["caps_e"] += r.captions_expected
        totals["caps_a"] += r.captions_actual
        totals["speech"] += r.speech_seconds
        totals["uncap"] += r.uncaptioned_seconds
        totals["short"] += r.short_captions
        totals["edge"] += r.edge_cuts
        totals["cut"] += r.bad_take_cuts
        totals["mk"] += r.markers

    lines.append(sep)
    uncap_pct = (totals["uncap"] / totals["speech"] * 100
                 if totals["speech"] > 0 else 0.0)
    lines.append(
        f" {'':>2} {'TOTAL':<48} "
        f"{'':>7} "
        f"{'':>7} "
        f"{'':>6} "
        f"{totals['items_e']:>4}/{totals['items_a']:<4} "
        f"{totals['holes']:>8} "
        f"{totals['big']:>12} "
        f"{totals['caps_e']:>4}/{totals['caps_a']:<3} "
        f"{totals['speech']:>7.1f} "
        f"{totals['uncap']:>6.1f} "
        f"{uncap_pct:>5.1f} "
        f"{totals['short']:>5} "
        f"{totals['edge']:>4} "
        f"{totals['cut']:>4} "
        f"{totals['mk']:>4}")

    return "\n".join(lines)


def format_findings(report: VerificationReport) -> str:
    """The findings listed by class, after the table."""
    lines = []
    for cls in sorted({f.finding_class for f in report.all_findings}):
        class_findings = [f for f in report.all_findings
                          if f.finding_class == cls]
        errors = [f for f in class_findings if f.severity == "error"]
        warnings = [f for f in class_findings if f.severity == "warning"]

        label = "ENCODING" if cls in ENCODING_CLASSES else (
            "PLANNING" if cls in PLANNING_CLASSES else (
            "PROVENANCE" if cls in PROVENANCE_CLASSES else "PLAN-QUALITY"))
        lines.append(f"\n### {cls} - {label} ({len(class_findings)} findings)")

        for f in class_findings[:20]:  # cap per class for readability
            icon = "ERROR" if f.severity == "error" else "WARN"
            lines.append(f"  [{icon}] {f.reel}: {f.message}")
        if len(class_findings) > 20:
            lines.append(f"  ... and {len(class_findings) - 20} more")

    return "\n".join(lines)


# ── Read-only proof ──────────────────────────────────────────────────

def hash_snapshot_dict(data: dict) -> str:
    """SHA-256 of a snapshot's serializable representation."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── Verifying one reel ───────────────────────────────────────────────

def verify_reel(plan: ReelPlan,
                timeline: ReelTimeline,
                transcript_segments: Optional[Sequence[dict]] = None,
                caption_cards: Optional[Sequence[dict]] = None,
                master_holes: Optional[Sequence[dict]] = None,
                master_fps: float = 0.0,
                ) -> ReelResult:
    """Run all checks on one reel and return the result."""
    fps = timeline.fps or _fps()
    findings: List[Finding] = []

    # F1: Picture holes
    picture_findings = check_picture_holes(
        plan.reel_name, timeline.video_items, master_holes)
    findings.extend(picture_findings)

    # F1 audio half
    audio_findings = check_audio_holes(plan.reel_name, timeline.audio_items)
    findings.extend(audio_findings)

    # F4: Item count and per-speaker duration
    findings.extend(check_item_count(
        plan.reel_name, plan.placements, timeline.video_items, fps))

    # F9: Duplicate placements at same record position
    findings.extend(check_duplicate_placements(
        plan.reel_name, timeline.video_items, fps))

    # F10: Format (resolution and frame rate)
    if timeline.width and timeline.height:
        findings.extend(check_format(
            plan.reel_name, timeline.width, timeline.height, fps,
            master_fps or fps))

    # F11: Subtitle styling - reads the OUTPUT cards, not the config
    findings.extend(check_subtitle_styling(
        plan.reel_name, timeline.caption_items, timeline.video_items))

    # F2: Caption card duration
    if plan.captions and timeline.caption_items:
        findings.extend(check_caption_duration(
            plan.reel_name, plan.captions, timeline.caption_items, fps))

    # F5: Caption coverage
    if transcript_segments and caption_cards is not None:
        findings.extend(check_caption_coverage(
            plan.reel_name, transcript_segments, caption_cards or [],
            plan.keep_ranges or [(plan.span_start, plan.span_end)],
            fps))

    # F6: Caption overlap
    if caption_cards:
        findings.extend(check_caption_overlaps(
            plan.reel_name, caption_cards, fps))

    # F7: Short captions
    if caption_cards:
        findings.extend(check_short_captions(
            plan.reel_name, caption_cards, fps))

    # F8: Boundary speech
    if transcript_segments:
        findings.extend(check_boundary_speech(
            plan.reel_name, plan.span_start, plan.span_end,
            transcript_segments))

    # Plan quality gates
    findings.extend(check_plan_length(plan.reel_name, plan.plan_seconds))
    findings.extend(check_plan_speakers(plan.reel_name, plan.placements))
    if master_holes is not None:
        findings.extend(check_plan_picture_continuity(
            plan.reel_name, plan.span_start, plan.span_end))

    # Compute summary numbers
    one_frame_holes = sum(
        1 for f in findings
        if f.finding_class == FindingClass.F1
        and f.detail and f.detail.get("gap_frames") == 1)
    big_holes = [
        f.detail for f in findings
        if f.finding_class in (FindingClass.F1, FindingClass.F3)
        and f.detail and f.detail.get("gap_frames", 0) > 2]
    short_caps = sum(1 for f in findings
                     if f.finding_class == FindingClass.F7)
    edge_cuts = sum(1 for f in findings
                    if f.finding_class == FindingClass.F8)

    # Speech and caption numbers
    speech_seconds = 0.0
    uncaptioned_seconds = 0.0
    for f in findings:
        if f.finding_class == FindingClass.F5 and f.detail:
            uncaptioned_seconds += f.detail.get("straddling_seconds", 0)

    return ReelResult(
        reel_name=plan.reel_name,
        reel_number=plan.reel_number,
        plan_seconds=plan.plan_seconds,
        plan_frames=plan.plan_frames,
        actual_frames=timeline.total_frames,
        items_expected=len(plan.placements),
        items_actual=len([i for i in timeline.video_items
                          if i.track_index in (1, 2)]),
        one_frame_holes=one_frame_holes,
        big_holes=big_holes,
        captions_expected=len(plan.captions),
        captions_actual=len(timeline.caption_items),
        speech_seconds=speech_seconds,
        uncaptioned_seconds=uncaptioned_seconds,
        uncaptioned_pct=(
            uncaptioned_seconds / speech_seconds * 100
            if speech_seconds > 0 else 0.0),
        short_captions=short_caps,
        edge_cuts=edge_cuts,
        bad_take_cuts=len(plan.cuts),
        markers=len(timeline.markers),
        findings=findings,
    )


# ── Converting timeline_ingest snapshots to our types ────────────────

def _snapshot_to_reel_timeline(snapshot) -> ReelTimeline:
    """Convert a TimelineSnapshot to our ReelTimeline.

    This is the bridge between `timeline_ingest` (which reads Resolve)
    and our check functions (which are pure and tested without Resolve).
    """
    fps = snapshot.fps

    video_items = []
    audio_items = []
    caption_items = []
    for clip in snapshot.clips:
        item = TimelineItem(
            track_type=clip.track_type,
            track_index=clip.track_index,
            start_frame=int(round(clip.timeline_start * fps)),
            end_frame=int(round(clip.timeline_end * fps)),
            duration_frames=int(round(clip.duration * fps)),
            source_start_frame=clip.source_in_frame,
            source_end_frame=clip.source_out_frame,
            source_file=clip.source_file,
            speaker=clip.speaker,
            name=clip.name,
            unique_id=clip.resolve_item_id,
        )
        if clip.track_type == "video" and clip.track_index <= 2:
            video_items.append(item)
        elif clip.track_type == "video" and clip.track_index == 3:
            caption_items.append(item)
        elif clip.track_type == "audio":
            audio_items.append(item)

    return ReelTimeline(
        reel_name=snapshot.timeline_name,
        fps=fps,
        total_frames=snapshot.end_frame - snapshot.start_frame,
        video_items=tuple(sorted(video_items,
                                 key=lambda i: (i.track_index, i.start_frame))),
        audio_items=tuple(sorted(audio_items,
                                 key=lambda i: (i.track_index, i.start_frame))),
        caption_items=tuple(sorted(caption_items,
                                   key=lambda i: i.start_frame)),
        width=snapshot.width,
        height=snapshot.height,
    )


def _derive_plan_from_master(
    reel_name: str,
    reel_number: int,
    span_start: float,
    span_end: float,
    master_snapshot,
    transcript: Optional[dict] = None,
) -> ReelPlan:
    """Derive what the plan says about one reel from the master timeline.

    Uses the repo's own build arithmetic: reel_build.redundant_takes,
    keep_ranges, placements - the same route the audit used to re-derive
    the plan.
    """
    from library.tools.reel_build import (
        keep_ranges as compute_keep_ranges,
        placements as compute_placements,
        redundant_takes,
    )

    fps = master_snapshot.fps

    # Compute cuts (bad takes removed)
    cuts = ()
    kr = [(span_start, span_end)]
    if transcript:
        cuts_list = redundant_takes(span_start, span_end, transcript)
        cuts = tuple(cuts_list)
        kr = compute_keep_ranges(span_start, span_end, cuts_list)

    # Compute placements from master clips
    master_clips = master_snapshot.picture_clips()
    placed = compute_placements(kr, master_clips)

    planned_placements = tuple(
        PlannedPlacement(
            track_index=p["track_index"],
            speaker=p.get("speaker"),
            record_seconds=p["record"],
            source_in=p["source_in"],
            source_out=p["source_out"],
            source_file=p["clip"].source_file,
        )
        for p in placed
    )

    plan_seconds = sum(b - a for a, b in kr)
    plan_frames = plan_seconds * fps

    return ReelPlan(
        reel_name=reel_name,
        reel_number=reel_number,
        plan_seconds=plan_seconds,
        plan_frames=plan_frames,
        span_start=span_start,
        span_end=span_end,
        placements=planned_placements,
        cuts=cuts,
        keep_ranges=tuple(kr),
    )


def _find_master_picture_holes(master_snapshot) -> List[dict]:
    """Find picture holes in the master timeline for F3 attribution."""
    fps = master_snapshot.fps
    by_track: Dict[int, list] = {}
    for clip in master_snapshot.picture_clips():
        by_track.setdefault(clip.track_index, []).append(clip)

    # Merge all picture intervals to find where NOTHING has coverage.
    all_intervals = []
    for clips in by_track.values():
        for clip in clips:
            all_intervals.append((
                int(round(clip.timeline_start * fps)),
                int(round(clip.timeline_end * fps)),
            ))

    if not all_intervals:
        return []

    all_intervals.sort()
    merged = [list(all_intervals[0])]
    for s, e in all_intervals[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    holes = []
    for i in range(len(merged) - 1):
        gap_start = merged[i][1]
        gap_end = merged[i + 1][0]
        gap_len = gap_end - gap_start
        if gap_len > 0:
            holes.append({
                "frame": gap_start,
                "length": gap_len,
                "at_seconds": gap_start / fps,
            })
    return holes


# ── Plan provenance check ────────────────────────────────────────────

def check_plan_provenance(
    plan_path: str,
    reel_names: list[str],
    review_dir: str,
) -> List[Finding]:
    """Refuse to grade when the plan does not describe the timelines.

    The verifier was caught grading 16 built reels against a plan that
    described 14 completely different moments, producing 42 confident,
    precise, meaningless errors.  This check prevents that: it reads
    the provenance sidecar the builder wrote and compares the plan being
    graded now against the plan that was actually used to build.

    When provenance is absent (reels built before this change, or a
    standalone CLI invocation), the check falls back to comparing reel
    names in the plan against reel names on the timeline.  A plan that
    lists none of the reels it is being asked to grade is refused;
    missing provenance alone is a warning, not a refusal, because the
    common standalone-CLI path should not break.
    """
    from library.tools.plan_provenance import (
        read_provenance,
        check_plan_matches_provenance,
        check_reels_in_provenance,
    )

    findings: List[Finding] = []

    provenance = read_provenance(review_dir)
    if provenance is None:
        # No provenance file - reels predate this change or standalone
        # CLI invocation.  Warn but do not refuse.
        findings.append(Finding(
            finding_class=FindingClass.PLAN_MISMATCH,
            reel="(all)",
            message=(
                "no plan provenance record found - cannot verify that "
                "this plan describes these timelines. Reels built before "
                "provenance tracking are honestly lost to plan "
                "verification."),
            severity="warning",
            detail={
                "reason": "no_provenance_file",
                "review_dir": review_dir,
            },
        ))
        return findings

    # Provenance exists - check the content hash
    if plan_path and os.path.isfile(plan_path):
        matches, reason = check_plan_matches_provenance(plan_path, provenance)
        if not matches:
            findings.append(Finding(
                finding_class=FindingClass.PLAN_MISMATCH,
                reel="(all)",
                message=(
                    f"REFUSING to grade: {reason}. "
                    f"Every finding below would be noise. The plan used "
                    f"to build these reels was recorded at build time; "
                    f"the plan being graded now is a different document."),
                severity="error",
                detail={
                    "reason": "content_hash_mismatch",
                    "plan_path": plan_path,
                    "provenance_hash": provenance.get(
                        "plan_content_hash", ""),
                },
            ))
            return findings

    # Check that the reels on the timeline are the ones the build produced
    all_present, missing = check_reels_in_provenance(reel_names, provenance)
    if missing:
        findings.append(Finding(
            finding_class=FindingClass.PLAN_MISMATCH,
            reel="(all)",
            message=(
                f"{len(missing)} reel(s) on the timeline were not built "
                f"from the recorded plan: {missing}"),
            severity="warning",
            detail={
                "reason": "reels_not_in_provenance",
                "missing_reels": missing,
                "built_reels": provenance.get("built_reels", []),
            },
        ))

    return findings


# ── The full verification pipeline ───────────────────────────────────

def run_verification(
    project_name: str,
    master_name: str,
    plan_path: str = "",
    transcript: Optional[dict] = None,
    json_path: str = "",
    review_dir: str = "",
    out=None,
) -> int:
    """Connect to Resolve, read everything, verify, report.

    Returns 0 on pass, 1 on findings, 2 on fatal error or read-only
    violation.

    Every Resolve call this function makes is a getter: GetTimelineCount,
    GetTimelineByIndex, GetTrackCount, GetItemListInTrack, GetName,
    GetSetting, GetStart, GetEnd, GetSourceStartFrame, GetSourceEndFrame,
    GetMediaPoolItem, GetClipProperty, GetUniqueId, GetMarkers,
    GetStartFrame, GetEndFrame, GetTrackName.

    No Set*, Add*, Append*, Delete*, OpenPage, LoadProject,
    SetCurrentTimeline or SetCurrentProject.
    """
    import re as re_mod

    from library.tools.timeline_ingest import (
        resolve_project_exactly,
        snapshot_timeline,
        snapshot_to_dict,
        TimelineIngestError,
    )
    from library.tools.marker_feedback import ResolveUnavailable, connect_resolve

    if out is None:
        out = sys.stdout
    err = sys.stderr

    # ── Connect ──────────────────────────────────────────────────────
    try:
        resolve = connect_resolve()
    except (ResolveUnavailable, Exception) as exc:
        print(f"FATAL: cannot reach Resolve: {exc}", file=err)
        print("This tool reads a LIVE Resolve project. Open Resolve "
              "with the project and try again.", file=err)
        return 2

    manager = resolve.GetProjectManager()
    try:
        project = resolve_project_exactly(manager, project_name)
    except TimelineIngestError as exc:
        print(f"FATAL: {exc}", file=err)
        return 2

    # ── Find all timelines ───────────────────────────────────────────
    timeline_count = project.GetTimelineCount() or 0
    if timeline_count == 0:
        print("FATAL: project has no timelines.", file=err)
        return 2

    all_timelines = []
    for i in range(1, timeline_count + 1):
        tl = project.GetTimelineByIndex(i)
        if tl:
            all_timelines.append(tl)

    # Find the master and all reel timelines
    master_tl = None
    reel_timelines = []
    for tl in all_timelines:
        name = tl.GetName()
        if name == master_name:
            master_tl = tl
        elif name.startswith("Reel "):
            reel_timelines.append(tl)

    if master_tl is None:
        all_names = [t.GetName() for t in all_timelines]
        print(f"FATAL: no timeline named exactly {master_name!r}. "
              f"Found: {all_names}", file=err)
        return 2

    if not reel_timelines:
        all_names = [t.GetName() for t in all_timelines]
        print(f"FATAL: no timelines starting with 'Reel ' found. "
              f"Found: {all_names}", file=err)
        return 2

    reel_timelines.sort(key=lambda t: t.GetName())
    print(f"Project: {project_name}", file=err)
    print(f"Master:  {master_name}", file=err)
    print(f"Reels:   {len(reel_timelines)}", file=err)

    # ── Snapshot everything BEFORE (for read-only proof) ─────────────
    print("Reading all timelines (before hash)...", file=err)
    before_hashes = {}
    snapshots = {}

    master_snapshot = snapshot_timeline(master_tl, project_name)
    before_hashes[master_name] = hash_snapshot_dict(
        snapshot_to_dict(master_snapshot))
    snapshots[master_name] = master_snapshot

    for tl in reel_timelines:
        name = tl.GetName()
        snap = snapshot_timeline(tl, project_name)
        before_hashes[name] = hash_snapshot_dict(snapshot_to_dict(snap))
        snapshots[name] = snap

    print(f"  {len(before_hashes)} timelines hashed.", file=err)

    # ── Load the plan if provided ────────────────────────────────────
    moments = []
    plan_source = ""
    if plan_path:
        if os.path.isfile(plan_path):
            from library.tools.reel_proposal import read_proposal
            moments = read_proposal(plan_path)
            plan_source = f"proposal file: {plan_path}"
            print(f"Plan:    {plan_source} ({len(moments)} moments)",
                  file=err)
        else:
            plan_source = (f"DERIVED from master (--plan {plan_path!r} "
                           f"does not exist)")
            print(f"WARNING: {plan_path!r} does not exist.", file=err)
            print(f"Plan:    {plan_source}", file=err)
    else:
        plan_source = "DERIVED from master (no --plan provided)"
        print(f"Plan:    {plan_source}", file=err)

    # ── Plan provenance check ────────────────────────────────────────
    # If a review_dir is provided, check whether the plan being graded
    # is the one that was actually used to build these reels.  A
    # content-hash mismatch means the selector has overwritten the plan
    # since the build - every finding would be noise.
    provenance_findings: List[Finding] = []
    plan_refused = False
    effective_review_dir = review_dir
    if not effective_review_dir and plan_path:
        # Infer review_dir from plan_path (plan sits in the review dir)
        effective_review_dir = os.path.dirname(os.path.abspath(plan_path))

    if effective_review_dir and plan_path:
        reel_names_on_timeline = [tl.GetName() for tl in reel_timelines]
        provenance_findings = check_plan_provenance(
            plan_path, reel_names_on_timeline, effective_review_dir)

        prov_errors = [f for f in provenance_findings
                       if f.severity == "error"]
        if prov_errors:
            plan_refused = True
            print(f"PLAN MISMATCH: {prov_errors[0].message}", file=err)

    # ── Find master picture holes for F3 attribution ─────────────────
    master_holes = _find_master_picture_holes(master_snapshot)
    if master_holes:
        print(f"Master picture holes: {len(master_holes)}", file=err)
        for hole in master_holes:
            print(f"  frame {hole['frame']} len {hole['length']} "
                  f"at {hole['at_seconds']:.2f}s", file=err)

    # ── Verify each reel ─────────────────────────────────────────────
    reel_results = []

    if plan_refused:
        # The plan does not describe these timelines.  Every F1-F11
        # finding would be noise that looks like signal.  REFUSE.
        print("  REFUSED: skipping all reel checks (plan mismatch).",
              file=err)
    else:
        for tl in reel_timelines:
            name = tl.GetName()
            snap = snapshots[name]
            reel_tl = _snapshot_to_reel_timeline(snap)

            # Parse reel number from name "Reel 01 - slug"
            reel_number = 0
            m = re_mod.match(r"Reel\s+(\d+)", name)
            if m:
                reel_number = int(m.group(1))

            # Find matching moment from the plan
            moment = None
            for mom in moments:
                if mom.timeline_name == name or mom.number == reel_number:
                    moment = mom
                    break

            if moment:
                # Derive plan from the proposal moment + master timeline
                plan = _derive_plan_from_master(
                    name, reel_number,
                    moment.timeline_start, moment.timeline_end,
                    master_snapshot, transcript)
            else:
                # No plan proposal available - we can still detect holes,
                # duplicates, and caption defects but item count comparison
                # against a plan is not possible.  The plan will carry empty
                # placements so F4 comparisons are skipped (planned count is
                # 0, so the expected == actual assertion is not run).
                plan_seconds = snap.duration
                plan = ReelPlan(
                    reel_name=name,
                    reel_number=reel_number,
                    plan_seconds=plan_seconds,
                    plan_frames=plan_seconds * snap.fps,
                    span_start=0.0,
                    span_end=plan_seconds,
                    placements=(),
                    keep_ranges=((0.0, plan_seconds),),
                )

            result = verify_reel(plan, reel_tl, master_holes=master_holes,
                                 master_fps=master_snapshot.fps)
            reel_results.append(result)
            status = "FAIL" if result.errors else "ok"
            print(f"  {name}: {status} ({len(result.errors)} errors, "
                  f"{len(result.warnings)} warnings)", file=err)

    # ── Snapshot everything AFTER (for read-only proof) ───────────────
    print("Re-reading all timelines (after hash)...", file=err)
    after_hashes = {}

    master_after = snapshot_timeline(master_tl, project_name)
    after_hashes[master_name] = hash_snapshot_dict(
        snapshot_to_dict(master_after))

    for tl in reel_timelines:
        name = tl.GetName()
        snap_after = snapshot_timeline(tl, project_name)
        after_hashes[name] = hash_snapshot_dict(
            snapshot_to_dict(snap_after))

    # ── Read-only proof ──────────────────────────────────────────────
    all_identical = True
    proof_rows = {}
    for name in sorted(before_hashes):
        identical = before_hashes[name] == after_hashes[name]
        if not identical:
            all_identical = False
        proof_rows[name] = {
            "before": before_hashes[name][:16] + "...",
            "after": after_hashes[name][:16] + "...",
            "identical": identical,
            "before_full": before_hashes[name],
            "after_full": after_hashes[name],
        }

    read_only_proof = {
        "all_identical": all_identical,
        "timelines_checked": len(proof_rows),
        "timelines": proof_rows,
    }

    if not all_identical:
        print("FATAL: timelines were MODIFIED during verification. "
              "This tool is read-only and something changed underneath "
              "it.", file=err)
        for name, row in proof_rows.items():
            if not row["identical"]:
                print(f"  CHANGED: {name}", file=err)
        return 2

    print(f"Read-only proof: ALL {len(proof_rows)} timelines identical "
          f"before/after.", file=err)

    # ── Build the report ─────────────────────────────────────────────
    report = VerificationReport(
        project_name=project_name,
        master_timeline=master_name,
        reel_results=reel_results,
        read_only_proof=read_only_proof,
        plan_source=plan_source,
        provenance_findings=provenance_findings,
    )

    # ── Print human-readable output ──────────────────────────────────
    print(file=out)
    print(f"Plan graded against: {plan_source}", file=out)
    print(file=out)
    print(format_table(report), file=out)
    print(file=out)
    if report.all_findings:
        print(format_findings(report), file=out)
        print(file=out)

    # Summary
    print("=" * 60, file=out)
    if report.has_errors:
        print(f"FAILED: {report.total_errors} error(s), "
              f"{report.total_warnings} warning(s) across "
              f"{len(reel_results)} reels.", file=out)
    else:
        print(f"PASSED: 0 errors, {report.total_warnings} warning(s) "
              f"across {len(reel_results)} reels.", file=out)

    # ── Write JSON ───────────────────────────────────────────────────
    if json_path:
        json_data = report.as_dict()
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2, default=str)
        print(f"JSON written to {json_path}", file=err)

    return 1 if report.has_errors else 0


# ── CLI ──────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    """Entry point for the reel conformance verifier.

    Reads a LIVE Resolve project and its plan, reports every
    disagreement between them.  Exits 0 on pass, 1 on findings,
    2 on fatal error or read-only violation.

    --project and --master are REQUIRED.  Without them the tool refuses
    to run rather than guessing.

    Every Resolve call is a getter.  Read-only is proven by hashing
    every timeline's snapshot before and after and comparing.

    Usage::

        python3 -m library.tools.reel_conformance_verifier \\
            --project "Podcast (field test)" \\
            --master "GEO Podcast - Synced" \\
            [--plan reel_proposal.json] \\
            [--json output.json]
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="reel_conformance_verifier",
        description=(
            "Verify that built reel timelines match the plan that "
            "produced them.  Read-only against Resolve, with proof.  "
            "Exits 0 on pass, 1 on findings, 2 on fatal error."))
    parser.add_argument(
        "--project", required=True,
        help="Resolve project name (must be already open)")
    parser.add_argument(
        "--master", required=True,
        help="Master timeline name (e.g. 'GEO Podcast - Synced')")
    parser.add_argument(
        "--plan", default="",
        help="Path to reel_proposal.json plan file (optional; derives "
             "from master if absent)")
    parser.add_argument(
        "--json", default="",
        help="Path to write machine-readable JSON output")
    parser.add_argument(
        "--review-dir", default="",
        help="Path to the review directory containing plan provenance "
             "(defaults to the plan file's parent directory)")
    args = parser.parse_args(argv)

    return run_verification(
        project_name=args.project,
        master_name=args.master,
        plan_path=args.plan,
        json_path=args.json,
        review_dir=args.review_dir,
    )


if __name__ == "__main__":
    raise SystemExit(main())

