"""The draw gain measured 2026-09-17 on rendered pixels.

On 2026-09-11 the renderer drew gain 1.0 (sixteen synthetic plates,
`resolve_transform.MEASURED_OVERLAY_CASES`, pinned at explicit
`draw_gain=1.0` in `tests/test_resolve_transform.py`). On 2026-09-17
the same build (Studio 21.1.0.14) on the captain's machine drew gain
2.0 on every timeline class measured - 1080x1920 custom reels,
1080x1920 custom scratch, 3840x2160 custom scratch, 3840x2160
project-default scratch - both axes, four content classes. This file
derives that gain from the measurements rather than restating it,
and pins the ground truth the rebuild gate checks: declared row
0.8451 converts to stored Tilt -917, the captain's own hand value.

How each cell was measured (all "set a known value and render" -
never a captured value; see `resolve_transform`'s module docstring
for the full table):

- captions: Tilt -888 rendered canvas centre y 1404 and hand-set
  Tilt -917 rendered y 1418.5 (gallery stills of Reel 01 frame 20,
  row-profile correlation 1.0000 at the predicted shift);
- motion graphics: live stored Tilt 1296 on a 920x480 canvas and Pan
  583.7838 on a 296x480 canvas drawing the pinned centres [540, 312]
  and [860, 960] (the captain's own pins, read off Reel 26);
- picture: 3840x2160 footage at fit on a scratch 1080x1920 timeline,
  Tilt 100 moving 63 px, Tilt 200 moving 127 px, Pan 100 moving
  200 px (gallery stills, cross-correlation 0.9997 / 0.91);
- 3840x2160: native 3840x2160 plate at Tilt -400 moving 800 px, and
  a 904x480 caption drawing at 2x the law's shift, on a
  project-default scratch timeline.

If the renderer ever draws gain 1.0 again, THESE tests fail - that
is the alarm, and the repair is a fresh rendered-pixel calibration
of `resolve_transform.FALLBACK_DRAW_GAIN`, never arithmetic.
"""

import pytest

from library.tools.resolve_transform import (
    FALLBACK_DRAW_GAIN,
    drawn_centre,
    fit_base_scale,
    shift_px,
    units_for_shift,
)


def _solved_gain(shift_px_measured, units, clip_dim, frame_dim,
                 base=1.0):
    """The gain a (shift, units) pair needs under the law's geometry."""
    return shift_px_measured / (units * (clip_dim / frame_dim) * base)


def test_the_measured_caption_points_solve_gain_two():
    # (stored Tilt, rendered canvas centre y) on 904x480 in 1080x1920.
    for tilt, centre_y in ((-888.0, 1404.0), (-917.0, 1418.5)):
        shift = centre_y - 960.0
        assert _solved_gain(shift, -tilt, 480, 1920) == pytest.approx(
            2.0, abs=0.01)


def test_the_measured_motion_graphics_points_solve_gain_two():
    # (stored units, drawn shift) horizontally and vertically.
    assert _solved_gain(960.0 - 312.0, 1296.0, 480, 1920) == \
        pytest.approx(2.0, abs=0.01)
    assert _solved_gain(860.0 - 540.0, 583.7837837837837, 296,
                        1080) == pytest.approx(2.0, abs=0.01)


def test_the_measured_picture_probe_solves_gain_two():
    base = fit_base_scale(3840, 2160, 1080, 1920)
    # Tilt 100 -> 63 px, Tilt 200 -> 127 px (linear), Pan 100 -> 200.
    assert _solved_gain(63.0, 100.0, 2160, 1920, base) == \
        pytest.approx(2.0, abs=0.05)
    assert _solved_gain(127.0, 200.0, 2160, 1920, base) == \
        pytest.approx(2.0, abs=0.05)
    assert _solved_gain(200.0, 100.0, 3840, 1080, base) == \
        pytest.approx(2.0, abs=0.05)


def test_the_measured_4k_plate_solves_gain_two():
    # Native 3840x2160 plate, Tilt -400 moves 800 on 3840x2160.
    assert _solved_gain(800.0, 400.0, 2160, 2160) == pytest.approx(
        2.0, abs=0.01)


def test_gain_one_solves_no_measured_case():
    """The discriminator: under gain 1.0 every cell above misses."""
    assert _solved_gain(1418.5 - 960.0, 917.0, 480, 1920) != \
        pytest.approx(1.0, abs=0.05)
    assert _solved_gain(960.0 - 312.0, 1296.0, 480, 1920) != \
        pytest.approx(1.0, abs=0.05)
    base = fit_base_scale(3840, 2160, 1080, 1920)
    assert _solved_gain(200.0, 100.0, 3840, 1080, base) != \
        pytest.approx(1.0, abs=0.05)


def test_the_declared_row_converts_to_the_captains_hand_value():
    """The ground truth the rebuild gate checks: caption_row 0.8451 is
    delivery row 1623, canvas centre y 1418.5 on a 904x480 canvas, and
    the conversion must store Tilt -917 - his hand value, exactly -
    not the -1834 the unmeasured law produces."""
    assert units_for_shift(960.0 - 1418.5, 480, 1920) == pytest.approx(
        -917.0, abs=0.5)
    assert drawn_centre(904, 480, 1080, 1920, 0.0, -917.0) == \
        pytest.approx((540.0, 1418.5), abs=0.5)


def test_the_default_gain_is_the_fallback_gain():
    assert FALLBACK_DRAW_GAIN == pytest.approx(2.0)
    assert units_for_shift(960.0 - 1418.5, 480, 1920) == pytest.approx(
        units_for_shift(960.0 - 1418.5, 480, 1920, 1.0,
                        FALLBACK_DRAW_GAIN))
    assert shift_px(-917.0, 480, 1920) == pytest.approx(
        shift_px(-917.0, 480, 1920, 1.0, FALLBACK_DRAW_GAIN))


def test_the_inverse_round_trips_at_the_measured_gain():
    for value in (-1836.0, -917.0, -888.0, 0.0, 583.78):
        for clip_dim, frame_dim, base in ((480, 1920, 1.0),
                                          (296, 1080, 1.0),
                                          (2160, 1920, 0.28125)):
            back = units_for_shift(
                shift_px(value, clip_dim, frame_dim, base),
                clip_dim, frame_dim, base)
            assert back == pytest.approx(value, abs=1e-9)
