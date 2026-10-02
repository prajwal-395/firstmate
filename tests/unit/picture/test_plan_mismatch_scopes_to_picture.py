"""PLAN-MISMATCH scopes to picture: reel 13's one-frame refusal.

The plan check compares the picture extent (V1/V2), never the whole
timeline: a caption rounding one frame past exact picture is not a
different plan, while a dropped or lengthened picture clip still refuses.
History and verdict: `docs/evidence/plan_mismatch_scope.md`.
"""

from __future__ import annotations

from library.tools.reel_conformance_verifier import (
    FindingClass,
    PlannedPlacement,
    ReelPlan,
    ReelTimeline,
    TimelineItem,
    check_plan_describes_timeline,
    verify_reel,
)


FPS = 24000 / 1001  # the reel timeline rate, exact

# The reel-13 shape, reduced to one range: a 79.354s span whose
# per-range framing and whose closing caption's per-edge rounding land
# on different integers. The numbers below ARE the R5 report's numbers.
RANGE_START = 10.0
RANGE_END = 89.354
REEL_END_SECONDS = RANGE_END - RANGE_START  # 79.354
PLAN_KEEP_RANGES = ((RANGE_START, RANGE_END),)
PLANNED_FRAMES = 1902
CAPTION_RECORD_END = 1903


def _picture(start_frame: int, end_frame: int) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=1,
        start_frame=start_frame, end_frame=end_frame,
        duration_frames=end_frame - start_frame,
        source_start_frame=0, source_end_frame=end_frame - start_frame,
        source_file="/m/a.MXF", speaker="Akshita", name="clip",
    )


def _caption(start_frame: int, end_frame: int) -> TimelineItem:
    return TimelineItem(
        track_type="video", track_index=3,
        start_frame=start_frame, end_frame=end_frame,
        duration_frames=end_frame - start_frame,
        source_start_frame=0, source_end_frame=end_frame - start_frame,
        source_file="/s/seg.mov", speaker="Akshita", name="seg",
    )


def _plan() -> ReelPlan:
    return ReelPlan(
        reel_name="Reel 13 - the-accounting-firm",
        reel_number=13,
        plan_seconds=REEL_END_SECONDS,
        plan_frames=REEL_END_SECONDS * FPS,
        span_start=RANGE_START,
        span_end=RANGE_END,
        placements=(PlannedPlacement(
            track_index=1, speaker="Akshita", record_seconds=0.0,
            source_in=0.0, source_out=REEL_END_SECONDS,
            source_file="/m/a.MXF"),),
        captions=(),
        # The fixture carries no caption reference set, so the caption
        # layer is REFUSED (NO_REFERENCE) rather than graded - these
        # tests own the length gate only, and a second gate's noise
        # must not decide them.
        captions_unavailable="fixture carries no caption reference set",
        keep_ranges=PLAN_KEEP_RANGES,
    )


def _error_classes(result) -> set:
    return {f.finding_class for f in result.findings
            if f.severity == "error"}


def test_a_caption_tail_past_exact_picture_does_not_refuse_the_plan():
    """The regression: picture tiles to the frame, caption rounds one
    past it, timeline carries 1903 - and the plan still describes the
    reel, so F4 must run rather than be refused.

    FAILS before the fix (the whole-timeline scope refuses with the R5
    numbers); PASSES after (the picture scope agrees at 1902).
    """
    timeline = ReelTimeline(
        reel_name="Reel 13 - the-accounting-firm",
        fps=FPS,
        total_frames=CAPTION_RECORD_END,
        picture_frames=PLANNED_FRAMES,
        video_items=(_picture(0, PLANNED_FRAMES),),
        audio_items=(),
        caption_items=(_caption(0, CAPTION_RECORD_END),),
    )
    result = verify_reel(_plan(), timeline)
    classes = _error_classes(result)
    assert FindingClass.PLAN_MISMATCH not in classes, (
        "picture tiles the plan exactly - a one-frame caption rounding "
        f"tail is not a different plan: "
        f"{[(f.finding_class, f.message) for f in result.errors]}")
    assert FindingClass.F4 not in classes, (
        "with the plan describing the timeline, F4 runs and the "
        "single placed clip matches the single placement")


def test_a_picture_a_frame_short_or_long_still_refuses():
    """The gate keeps its teeth both ways: the scoping moves which extent
    is graded, never how exact the grading is. +1 is the mixed-rate
    last-clip shape, the one alternative reading of reel 13's +1."""
    for delta in (-1, 1):
        findings = check_plan_describes_timeline(
            "Reel 13", PLAN_KEEP_RANGES, PLANNED_FRAMES + delta, FPS)
        assert len(findings) == 1
        assert findings[0].finding_class == FindingClass.PLAN_MISMATCH
        assert findings[0].detail["delta_frames"] == delta
