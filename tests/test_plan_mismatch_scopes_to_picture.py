"""PLAN-MISMATCH scopes to picture: reel 13's one-frame refusal.

Found 2026-09-08 by the full reel rebuild (R5: 17 clean, 3 held):
reel 13 `the-accounting-firm` was REFUSED with "plan 1902f vs timeline
1903f (+1f, +0.04s)" - not promoted, the approved timeline untouched.

VERDICT, established before anything was changed (planner, placer, or
check - the check): the planner and the placer are both correct and the
check compared layers it has no common arithmetic for.

- Planner: the re-derived plan lays 1902 frames for keep ranges
  [(10.0, 89.354)] at 24000/1001 - exactly what
  `reel_build.placements` tiles for picture (the shared per-range
  ``round(end * fps) - round(start * fps)`` arithmetic). Correct.
- Placer: the picture tiles exactly, and the closing caption sits at
  the frames `frame_utils.span_frames` computes - the SAME arithmetic
  F2 grades it against (PR 691 made placer and duration gate agree "by
  construction"). Clamping the caption to the picture would break that
  exactness and move the refusal to F2, not remove it. Correct.
- Check: `check_plan_describes_timeline` held that picture plan
  against `timeline.total_frames` - Resolve's GetEndFrame minus
  GetStartFrame over ALL tracks, V3 captions included. Captions are
  laid by per-edge rounding on reel seconds while picture is laid by
  per-range sums on master seconds, and the two projections of the
  same seconds diverge by a frame at unlucky fractions: a 79.354s
  range lays 1902 picture frames while a caption closing exactly at
  the reel end records to frame 1903. The check's docstring overclaims
  ("the same integer arithmetic the builder ran") - true for picture
  only. Wrong side.

The fix scopes the actual side to the picture extent (V1/V2 to the
timeline origin, carried on `ReelTimeline.picture_frames`). Frame-exact
throughout: no tolerance is widened, none added. A genuinely dropped
or lengthened picture clip still moves the picture extent by its
frames and still refuses - the gate keeps refusing the real defect,
which the tests below pin from both directions.
"""

from __future__ import annotations

from library.tools.frame_utils import span_frames
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


def test_the_two_layers_disagree_by_one_frame_on_reel13s_numbers():
    """The mechanism, pinned with the check's own arithmetic.

    A single 79.354s keep range lays PLANNED_FRAMES picture frames,
    while a caption closing exactly at the reel end - the modal shape
    once the boundary snap parks range edges on word ends - records to
    CAPTION_RECORD_END by the shared `span_frames` both the placer and
    F2 count with. Held against the all-tracks extent, the exact R5
    refusal falls out: 1902 vs 1903, +1 frame, +0.04s.

    Green before and after the fix: it documents the divergence the
    scoping removes, and the refusal the old scope produced.
    """
    planned = sum(round(end * FPS) - round(start * FPS)
                  for start, end in PLAN_KEEP_RANGES)
    assert planned == PLANNED_FRAMES

    record_start, record_end = span_frames(0.0, REEL_END_SECONDS, FPS)
    assert (record_start, record_end) == (0, CAPTION_RECORD_END)

    findings = check_plan_describes_timeline(
        "Reel 13", PLAN_KEEP_RANGES, record_end, FPS)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_class == FindingClass.PLAN_MISMATCH
    assert finding.detail["planned_frames"] == PLANNED_FRAMES
    assert finding.detail["timeline_frames"] == CAPTION_RECORD_END
    assert finding.detail["delta_frames"] == 1
    assert "+1 frames" in finding.message
    assert "+0.04s" in finding.message


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


def test_a_dropped_picture_clip_still_refuses():
    """The gate keeps its teeth downward: picture a frame short of the
    plan is still not the plan that built the reel.

    Unaffected by the fix - passes before and after. The scoping moves
    which extent is graded, never how exact the grading is.
    """
    findings = check_plan_describes_timeline(
        "Reel 13", PLAN_KEEP_RANGES, PLANNED_FRAMES - 1, FPS)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.PLAN_MISMATCH
    assert findings[0].detail["delta_frames"] == -1


def test_a_genuinely_longer_picture_still_refuses():
    """The gate keeps its teeth upward: picture a frame LONGER than the
    plan - the mixed-rate last-clip shape, the one alternative reading
    of reel 13's +1 - still refuses, now naming the picture extent.

    Direct check: green before and after (the arithmetic is untouched).
    """
    findings = check_plan_describes_timeline(
        "Reel 13", PLAN_KEEP_RANGES, PLANNED_FRAMES + 1, FPS)
    assert len(findings) == 1
    assert findings[0].finding_class == FindingClass.PLAN_MISMATCH
    assert findings[0].detail["delta_frames"] == 1


def test_a_genuinely_longer_picture_still_refuses_through_verify_reel():
    """The wired gate still refuses real picture excess end to end:
    picture runs to 1903 with no caption tail anywhere, so the scope
    cannot explain it away and PLAN-MISMATCH fires.

    Green after the fix (red before it only because the picture-scoped
    wiring did not exist yet - the same refusal through the old scope
    is pinned by the test above).
    """
    timeline = ReelTimeline(
        reel_name="Reel 13 - the-accounting-firm",
        fps=FPS,
        total_frames=PLANNED_FRAMES + 1,
        picture_frames=PLANNED_FRAMES + 1,
        video_items=(_picture(0, PLANNED_FRAMES + 1),),
        audio_items=(),
        caption_items=(),
    )
    result = verify_reel(_plan(), timeline)
    assert FindingClass.PLAN_MISMATCH in _error_classes(result)
