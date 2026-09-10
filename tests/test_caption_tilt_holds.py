"""Tilt -3840 is large and correct: the arithmetic of a small box.

The captain reads the V4 caption row's `Tilt = -3840, Pan = 0` as a
symptom. It is the placement the box needs: Resolve moves a clip in
fractions of the clip's own size (`tight_box.placement_for_box`),

    tilt = -dy * (full_h / canvas_h),

so a 240px-tall box needs x8 where a full-frame clip needs x1. Large
is inherent to the scaling, not evidence of a bug - and Resolve holds
Pan/Tilt to four times the timeline dimensions (measured 2026-09-09:
+-4320 Pan / +-7680 Tilt on 1080x1920), so -3840 is well inside what
it holds. The previously seen -7680 sat exactly on the ceiling; the
read-back gate (`overlay_placement.apply_placement_transform`) holds
these rather than clamping them.

What would be a defect is a placement that does not land: outside the
frame, or past what Resolve holds. These pin the landing, not the
number - every caption box must hold, sit fully in frame, and
round-trip through `canvas_offset` back to the union it was measured
from.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from library.tools.tight_box import (
    canvas_offset,
    placement_for_box,
    placement_holds,
)

FULL_W, FULL_H = 1080, 1920


def _bottom_box(canvas_w, canvas_h, union_h):
    """A bottom-anchored caption box: centred horizontally, union bottom
    at the safe-area bottom (40px), canvas padded around it."""
    union_bottom = FULL_H - 40.0
    canvas_top = union_bottom - union_h - 16.0  # PAD_TOP
    return (canvas_w, canvas_h,
            FULL_W / 2.0, canvas_top + canvas_h / 2.0)


def test_tilt_magnitude_is_canvas_scaling_not_a_bug():
    """The ratio tilt/-dy is exactly full_h/canvas_h: a half-height box
    needs x2, a quarter-height box x4, wherever it sits."""
    for canvas_h in (960.0, 480.0, 240.0):
        canvas_cy = 960.0 + 500.0
        placement = placement_for_box(540.0, canvas_h, 540.0, canvas_cy,
                                      FULL_W, FULL_H)
        dy = canvas_cy - FULL_H / 2.0
        assert placement["tilt"] / -dy == pytest.approx(
            FULL_H / canvas_h)


def test_a_3840_tilt_is_a_half_height_box_one_frame_down():
    """The number the captain is reading: a 480px canvas whose centre
    sits 960px below frame centre needs exactly -3840, and holds."""
    placement = placement_for_box(540.0, 480.0, 540.0, 960.0 + 960.0,
                                  FULL_W, FULL_H)
    assert placement["tilt"] == pytest.approx(-3840.0)
    assert placement["pan"] == pytest.approx(0.0)
    assert placement_holds(placement, FULL_W, FULL_H) == ""


def test_a_real_bottom_caption_box_holds_and_sits_in_frame():
    """The reel-09 shape: an 870x220 canvas over a bottom-anchored
    union. Tilt is in the thousands, inside +-7680, and the canvas it
    names sits fully inside the delivery frame."""
    canvas_w, canvas_h, cx, cy = _bottom_box(870.0, 220.0, union_h=168.0)
    placement = placement_for_box(canvas_w, canvas_h, cx, cy,
                                  FULL_W, FULL_H)
    assert abs(placement["tilt"]) > 1000  # large, as the captain saw
    assert placement_holds(placement, FULL_W, FULL_H) == ""
    box = _box(canvas_w, canvas_h, placement)
    ox, oy = canvas_offset(box)
    assert ox >= 0 and oy >= 0
    assert ox + canvas_w <= FULL_W and oy + canvas_h <= FULL_H
    # And it round-trips: the offset recovers the canvas centre the
    # placement was computed from, to the pixel.
    assert ox + canvas_w / 2.0 == pytest.approx(cx, abs=1.0)
    assert oy + canvas_h / 2.0 == pytest.approx(cy, abs=1.0)


def test_a_past_ceiling_tilt_is_still_refused():
    """The gate did not move: a box needing more than Resolve holds
    (the old -7929 case) is still named, not shipped."""
    placement = placement_for_box(348.0, 146.0, 540.0, 960.0 + 603.0,
                                  FULL_W, FULL_H)
    assert placement["tilt"] < -7680
    reason = placement_holds(placement, FULL_W, FULL_H)
    assert "Tilt" in reason and "7680" in reason


def _box(canvas_w, canvas_h, placement):
    from library.tools.tight_box import TightBox
    return TightBox(width=int(canvas_w), height=int(canvas_h), props={},
                    placement=placement, union_w=canvas_w,
                    union_h=canvas_h, full_width=FULL_W,
                    full_height=FULL_H)
