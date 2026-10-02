"""One positioning rule, and a stored value judged against INTENT.

Pan/Tilt move a clip by a fraction of its OWN canvas, not of the frame
(`library/tools/resolve_transform.py`); the numbers below are measured off
exported stills of "Podcast (field test)" on 2026-09-11, and a value is
verified against intent because a read-back passes an off-frame Tilt.
History: docs/evidence/overlay_position.md#one-positioning-rule.
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

#: The 2026-09-11 draw gain: every measurement this file reproduces
#: comes from that calibration's stills (see HISTORY_GAIN in
#: test_tight_box.py). Today's gain is proven separately
#: (`tests/test_draw_gain_measured.py`) and by the rebuild gate.
HISTORY_GAIN = 1.0
#: Reel 13 @854, measured: 840x480 canvas, ink rows 279..432, cols 47..775.
TIGHT_CANVAS = (840.0, 480.0)
TIGHT_INK = (47.0, 279.0, 775.0, 432.0)
TIGHT_TILT = -1740.0
#: Where that caption is measured to draw on an exported still.
MEASURED_TIGHT_INK_ROWS = (1434.0, 1587.0)
#: Reel 13 @567, measured: full-frame canvas, ink rows 1415..1572.
FULL_INK = (272.0, 1415.0, 800.0, 1572.0)
#: Reel 30's first graphic: a 724x480 canvas stored at Tilt 5184, whose
#: whole canvas draws at frame rows -576..-96.  Its ink fills the
#: canvas from row 40 down, so nothing of it reaches row 0.
OFF_FRAME_CANVAS = (724.0, 480.0)
OFF_FRAME_TILT = 5184.0
OFF_FRAME_INK = (0.0, 40.0, 724.0, 440.0)
MEASURED_OFF_FRAME_CANVAS_ROWS = (-576.0, -96.0)


def _p(tilt, pan=0.0):
    return {"scaling": 1, "pan": pan, "tilt": tilt}


def test_the_rule_predicts_the_exported_still_from_both_carriages():
    """The one relation, checked against pixels off a real timeline."""
    ox, oy = canvas_screen_origin(*TIGHT_CANVAS, _p(TIGHT_TILT), *FRAME, draw_gain=HISTORY_GAIN)
    assert oy == pytest.approx(1155.0, abs=0.5), (
        "the canvas top measured on the exported still is row 1155")
    assert ox == pytest.approx(120.0, abs=0.5)
    box = ink_screen_box(*TIGHT_CANVAS, _p(TIGHT_TILT), TIGHT_INK, *FRAME, draw_gain=HISTORY_GAIN)
    assert (box[1], box[3]) == pytest.approx(MEASURED_TIGHT_INK_ROWS, abs=1.0)

    # Two Inspector numbers, one screen row - the captain's question:
    # full-frame needs 0, tight needs -1740, computed from DIFFERENT
    # canvases and landing within tolerance of each other.
    tight = ink_screen_box(*TIGHT_CANVAS, _p(TIGHT_TILT), TIGHT_INK, *FRAME, draw_gain=HISTORY_GAIN)
    full = ink_screen_box(*FRAME, None, FULL_INK, *FRAME, draw_gain=HISTORY_GAIN)
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
            placement = placement_for_box(canvas_w, canvas_h, cx, cy, *FRAME, draw_gain=HISTORY_GAIN)
            ox, oy = canvas_screen_origin(canvas_w, canvas_h, placement,
                                          *FRAME, draw_gain=HISTORY_GAIN)
            assert ox + canvas_w / 2.0 == pytest.approx(cx, abs=1e-6)
            assert oy + canvas_h / 2.0 == pytest.approx(cy, abs=1e-6)


def test_intent_accepts_both_carriages_and_refuses_the_off_frame_one_a_readback_passes():
    """One intent, two carriages accepted, the off-frame value refused."""
    intent = FULL_INK  # the caption row, in frame pixels
    assert verify_ink_against_intent(*TIGHT_CANVAS, _p(TIGHT_TILT),
                                     TIGHT_INK, intent, *FRAME, draw_gain=HISTORY_GAIN) == ""
    assert verify_ink_against_intent(*FRAME, None, FULL_INK, intent,
                                     *FRAME, draw_gain=HISTORY_GAIN) == ""
    ox, oy = canvas_screen_origin(*OFF_FRAME_CANVAS, _p(OFF_FRAME_TILT),
                                  *FRAME, draw_gain=HISTORY_GAIN)
    assert (oy, oy + OFF_FRAME_CANVAS[1]) == pytest.approx(
        MEASURED_OFF_FRAME_CANVAS_ROWS, abs=1.0), (
        "the still of that frame contains no part of this artefact")
    reason = verify_ink_against_intent(*OFF_FRAME_CANVAS,
                                       _p(OFF_FRAME_TILT), OFF_FRAME_INK,
                                       intent, *FRAME, draw_gain=HISTORY_GAIN)
    assert reason, "Tilt 5184 on a 480 canvas must not pass this intent"
    assert "ENTIRELY OUTSIDE THE FRAME" in reason
    assert "5184" not in reason, (
        "the reason must name the PICTURE, not re-state the number")

    # Through the placer itself: `_intent_reason` is handed the value
    # Resolve HELD - identical to the value set, which is exactly the
    # case a read-back passes.
    draw_intent = {"canvas": TIGHT_CANVAS, "frame": FRAME,
                   "ink_in_canvas": TIGHT_INK, "intent_box": FULL_INK}
    good = _intent_reason(draw_intent, _p(TIGHT_TILT),
                          {"Pan": 0.0, "Tilt": TIGHT_TILT}, draw_gain=HISTORY_GAIN)
    assert good == ""
    off_frame_intent = dict(draw_intent, canvas=OFF_FRAME_CANVAS,
                            ink_in_canvas=OFF_FRAME_INK)
    off_frame = _intent_reason(off_frame_intent, _p(OFF_FRAME_TILT),
                               {"Pan": 0.0, "Tilt": OFF_FRAME_TILT}, draw_gain=HISTORY_GAIN)
    assert "off by" in off_frame and "ENTIRELY OUTSIDE THE FRAME" in off_frame






