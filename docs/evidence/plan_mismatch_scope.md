# PLAN-MISMATCH scopes to picture

Test: `tests/unit/picture/test_picture_qa.py`.

PLAN-MISMATCH scopes to picture: reel 13's one-frame refusal.

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
