"""The fallback draw gain matches rendered-pixel measurements.

Calibration evidence is in `docs/evidence/pan_tilt_units.md`.
"""

import pytest

from library.tools.resolve_transform import (
    FALLBACK_DRAW_GAIN,
    drawn_centre,
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
