"""One positioning rule, and a stored value judged against INTENT.

The captain, 2026-09-11: *"to get the subtitles to the same absolute
positioning on the y-axis in the reel, the actual y-axis value in the
inspector tab is sometimes 0 sometimes -870. so there is something else
at play that is affecting the positioning"*.

He is right, and the rule is one sentence: **Pan/Tilt move a clip by a
fraction of its OWN canvas, not of the frame** - the shift is
``value * (canvas_dim / frame_dim) * draw_gain`` - so an overlay already
rendered full-frame is in position at 0 while the identical caption
rendered on a 480-tall tight canvas needs Tilt -870 to reach the same
screen row.  Both numbers are honest; the Inspector number is only
readable with the clip's own resolution beside it.

The measurements pinned here were read off the live project
"Podcast (field test)" on 2026-09-11:

- Reel 13 caption @854 (tight, 840x480, stored Tilt -870.0): its ink
  sits at canvas rows 279..432, and a correlation scan of the artefact
  against a still EXPORTED from that timeline finds the canvas top at
  frame row 1155 and the ink at 1435..1587 - against the 1434..1587 the
  rule predicts, and a nominal caption row bottom of 1589.
- Reel 13 caption @567 (full-frame, 1080x1920, stored Tilt 0.0): ink at
  frame rows 1415..1572, drawn 1:1.
- Reel 28 caption @419 (tight, 840x480, stored Tilt -1700.0, computed
  under the pre-#960 single-gain relation): the same rule puts its ink
  at 1920..2011, and an exported still of that frame shows no caption
  at all.

The last one is why verification is against INTENT and not against a
read-back: -1700 reads back as exactly -1700, so every gate that asks
"did Resolve hold what I set" passes it.
"""

import pytest

from library.tools.overlay_placement import _intent_reason
from library.tools.tight_box import (
    INTENT_TOLERANCE_PX,
    canvas_screen_origin,
    ink_screen_box,
    placement_for_box,
    verify_ink_against_intent,
)

FRAME = (1080, 1920)
#: Reel 13 @854, measured: 840x480 canvas, ink rows 279..432, cols 47..775.
TIGHT_CANVAS = (840.0, 480.0)
TIGHT_INK = (47.0, 279.0, 775.0, 432.0)
TIGHT_TILT = -870.0
#: Where that caption is measured to draw on an exported still.
MEASURED_TIGHT_INK_ROWS = (1434.0, 1587.0)
#: Reel 13 @567, measured: full-frame canvas, ink rows 1415..1572.
FULL_INK = (272.0, 1415.0, 800.0, 1572.0)
#: Reel 28 @419, the stale single-gain carriage: same 840x480 canvas,
#: stored Tilt -1700.0, and its own ink measured at canvas rows
#: 350..441 - which the rule puts at frame rows 1920..2011, entirely
#: below the frame.  The exported still of that frame shows no caption.
STALE_TILT = -1700.0
STALE_INK = (47.0, 350.0, 775.0, 441.0)
MEASURED_STALE_INK_ROWS = (1920.0, 2011.0)


def _p(tilt, pan=0.0):
    return {"scaling": 1, "pan": pan, "tilt": tilt}


def test_the_rule_predicts_the_exported_still():
    """The one relation, checked against pixels off a real timeline."""
    ox, oy = canvas_screen_origin(*TIGHT_CANVAS, _p(TIGHT_TILT), *FRAME)
    assert oy == pytest.approx(1155.0, abs=0.5), (
        "the canvas top measured on the exported still is row 1155")
    assert ox == pytest.approx(120.0, abs=0.5)
    box = ink_screen_box(*TIGHT_CANVAS, _p(TIGHT_TILT), TIGHT_INK, *FRAME)
    assert (box[1], box[3]) == pytest.approx(MEASURED_TIGHT_INK_ROWS, abs=1.0)


def test_full_frame_needs_zero_and_tight_needs_minus_870():
    """Two Inspector numbers, one screen row - the captain's question.

    Not a tautology: the two are computed from DIFFERENT canvases and
    land within the tolerance of each other.
    """
    tight = ink_screen_box(*TIGHT_CANVAS, _p(TIGHT_TILT), TIGHT_INK, *FRAME)
    full = ink_screen_box(*FRAME, None, FULL_INK, *FRAME)
    assert full == pytest.approx(FULL_INK), (
        "a full-frame artefact with no transform draws 1:1")
    assert abs(tight[3] - full[3]) < INTENT_TOLERANCE_PX, (
        "the same caption row, reached from two carriages")
    assert tight[3] != full[3], (
        "and reached by different stored numbers, so this is a real "
        "comparison and not the same arithmetic twice")


def test_placement_for_box_and_canvas_screen_origin_round_trip():
    """The forward rule and its inverse are the SAME rule."""
    for canvas_w, canvas_h in ((840.0, 480.0), (1080.0, 1920.0),
                               (772.0, 540.0), (484.0, 480.0)):
        for cx, cy in ((540.0, 1395.0), (436.0, 512.0), (540.0, 960.0)):
            placement = placement_for_box(canvas_w, canvas_h, cx, cy, *FRAME)
            ox, oy = canvas_screen_origin(canvas_w, canvas_h, placement,
                                          *FRAME)
            assert ox + canvas_w / 2.0 == pytest.approx(cx, abs=1e-6)
            assert oy + canvas_h / 2.0 == pytest.approx(cy, abs=1e-6)


def test_intent_accepts_both_carriages_and_refuses_the_stale_one():
    """One intent, two carriages accepted, the stale carriage refused."""
    intent = FULL_INK  # the caption row, in frame pixels
    assert verify_ink_against_intent(*TIGHT_CANVAS, _p(TIGHT_TILT),
                                     TIGHT_INK, intent, *FRAME) == ""
    assert verify_ink_against_intent(*FRAME, None, FULL_INK, intent,
                                     *FRAME) == ""
    box = ink_screen_box(*TIGHT_CANVAS, _p(STALE_TILT), STALE_INK, *FRAME)
    assert (box[1], box[3]) == pytest.approx(MEASURED_STALE_INK_ROWS,
                                             abs=1.0)
    reason = verify_ink_against_intent(*TIGHT_CANVAS, _p(STALE_TILT),
                                       STALE_INK, intent, *FRAME)
    assert reason, "Tilt -1700 on a 480 canvas must not pass this intent"
    assert "ENTIRELY OUTSIDE THE FRAME" in reason
    assert "-1700" not in reason, (
        "the reason must name the PICTURE, not re-state the number")


def test_a_readback_cannot_see_what_intent_sees():
    """The defect the read-back is blind to, through the placer itself.

    `_intent_reason` is handed the value Resolve HELD - identical to
    the value set, which is exactly the case a read-back passes.
    """
    draw_intent = {"canvas": TIGHT_CANVAS, "frame": FRAME,
                   "ink_in_canvas": TIGHT_INK, "intent_box": FULL_INK}
    good = _intent_reason(draw_intent, _p(TIGHT_TILT),
                          {"Pan": 0.0, "Tilt": TIGHT_TILT})
    assert good == ""
    stale_intent = dict(draw_intent, ink_in_canvas=STALE_INK)
    stale = _intent_reason(stale_intent, _p(STALE_TILT),
                           {"Pan": 0.0, "Tilt": STALE_TILT})
    assert "off by" in stale and "ENTIRELY OUTSIDE THE FRAME" in stale


def test_no_draw_intent_leaves_the_placer_exactly_as_it_was():
    assert _intent_reason(None, _p(TIGHT_TILT), {"Tilt": TIGHT_TILT}) == ""


def test_an_unreadable_intent_is_reported_never_skipped():
    """A verification that declines to run is the gate that cannot fail."""
    reason = _intent_reason({"canvas": TIGHT_CANVAS}, _p(TIGHT_TILT),
                            {"Tilt": TIGHT_TILT})
    assert "NOT verified" in reason


def test_the_gain_is_scoped_to_the_frame_it_was_measured_on():
    """A geometry nobody probed keeps the scratch relation, not 2x."""
    unprobed = (1920, 1080)
    ox, oy = canvas_screen_origin(840.0, 480.0, _p(-870.0), *unprobed)
    assert oy == pytest.approx(1080 / 2.0 - 240.0 + 870.0 * (480 / 1080.0),
                               abs=1e-6)
