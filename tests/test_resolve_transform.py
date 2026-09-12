"""One model of Resolve's Pan/Tilt, and a test that fails if it splits.

The engine held TWO models of one Resolve behaviour and neither had
ever been checked against a picture it had produced:

- `tight_box` carried a `DRAW_GAIN_1080x1920 = 2.0` that does not
  exist, halving every tight overlay placement it computed;
- `reel_framing.delivered_picture` and `reel_look.punch_in_properties`
  read the SAME property as frame pixels, which is right on Pan at the
  reels' geometry by coincidence, wrong on Tilt by 3.16x, and wrong in
  SIGN on Tilt as well.

Correcting either constant on its own would have left the structural
fault standing, so both paths now call
`library/tools/resolve_transform.py` and this module is the gate on
that. Three things are pinned here:

1. the law reproduces every case that was MEASURED on rendered pixels,
   rather than restating the formula;
2. the overlay path and the picture path agree, where their geometries
   coincide, to the pixel;
3. no call site re-implements the arithmetic - each is driven and
   compared against the law called directly.
"""

import pytest

from library.tools.reel_framing import delivered_picture
from library.tools.resolve_transform import (
    MEASURED_OVERLAY_CASES,
    MEASURED_PICTURE_CASES,
    NATIVE_BASE_SCALE,
    ResolveTransformError,
    drawn_centre,
    drawn_origin,
    fit_base_scale,
    pan_tilt_for_centre,
    shift_px,
    units_for_shift,
)
from library.tools.tight_box import (
    canvas_offset,
    canvas_screen_origin,
    placement_for_box,
)


# ── 1. The law is DERIVED from the measurements, not asserted ────────

def test_the_gain_solved_from_every_measured_overlay_case_is_one():
    """`G` solved per case, never assumed. Any case that needs a gain
    other than 1 fails here, and 2.0 fails on all fourteen that move."""
    solved = []
    for clip_w, clip_h, frame_w, frame_h, pan, tilt, x0, y0 in \
            MEASURED_OVERLAY_CASES:
        if pan:
            shift = (x0 + clip_w / 2.0) - frame_w / 2.0
            solved.append(shift / (pan * (clip_w / frame_w)))
        if tilt:
            shift = frame_h / 2.0 - (y0 + clip_h / 2.0)
            solved.append(shift / (tilt * (clip_h / frame_h)))
    assert len(solved) == 12, "every case that actually moves is solved"
    for gain in solved:
        assert gain == pytest.approx(1.0, abs=0.005)


def test_the_law_reproduces_every_measured_overlay_case():
    for clip_w, clip_h, frame_w, frame_h, pan, tilt, x0, y0 in \
            MEASURED_OVERLAY_CASES:
        got = drawn_origin(clip_w, clip_h, frame_w, frame_h, pan, tilt)
        assert got == pytest.approx((x0, y0), abs=1.0), (
            f"{clip_w}x{clip_h} at pan {pan} tilt {tilt}")


def test_the_law_reproduces_every_measured_picture_case():
    """Including that the user ZOOM does not enter the shift.

    Cases at zoom 1.0 and 2.307 give the same pixels per unit; a model
    that multiplied by zoom would miss the 2.307 rows by 130%.
    """
    for src_w, src_h, frame_w, frame_h, _zoom, pan, tilt, cx, cy in \
            MEASURED_PICTURE_CASES:
        base = fit_base_scale(src_w, src_h, frame_w, frame_h)
        got = drawn_centre(src_w, src_h, frame_w, frame_h, pan, tilt, base)
        assert got == pytest.approx((cx, cy), abs=0.5), (
            f"pan {pan} tilt {tilt} at zoom {_zoom}")


def test_the_two_measured_units_on_the_reels_geometry():
    """The numbers the picture half was wrong by, stated outright."""
    base = fit_base_scale(3840, 2160, 1080, 1920)
    assert base == pytest.approx(0.28125, abs=1e-9)
    assert shift_px(1.0, 3840, 1080, base) == pytest.approx(1.0, abs=1e-9)
    assert shift_px(1.0, 2160, 1920, base) == pytest.approx(0.31640625,
                                                            abs=1e-9)


def test_the_inverse_is_the_same_law():
    for value in (-2592.0, -870.0, 0.0, 1.5, 1167.568):
        for clip_dim, frame_dim, base in ((480, 1920, 1.0),
                                          (296, 1080, 1.0),
                                          (2160, 1920, 0.28125)):
            back = units_for_shift(shift_px(value, clip_dim, frame_dim, base),
                                   clip_dim, frame_dim, base)
            assert back == pytest.approx(value, abs=1e-9)


def test_a_zero_dimension_raises_rather_than_dividing():
    with pytest.raises(ResolveTransformError):
        shift_px(100.0, 0, 1920)
    with pytest.raises(ResolveTransformError):
        fit_base_scale(3840, 2160, 1080, 0)


# ── 2. The two paths agree ───────────────────────────────────────────

def test_the_overlay_path_and_the_picture_path_draw_the_same_pixels():
    """The gate on the structural fault.

    A clip the size of the frame is the one geometry both paths can
    describe: the overlay path carries it at `Scaling=1` (base 1) and
    the picture path fits it (fit is also 1), so for any Pan/Tilt they
    must report the SAME rectangle. Under the two old models they did
    not: the overlay path doubled the shift and the picture path took
    Tilt for pixels with the sign reversed, so a Tilt of 100 put the
    same clip 200px up on one path and 100px DOWN on the other.
    """
    frame_w, frame_h = 1080, 1920
    for pan, tilt in ((0.0, 0.0), (100.0, 0.0), (0.0, 100.0),
                      (-250.0, 480.0)):
        overlay = canvas_screen_origin(frame_w, frame_h,
                                       {"scaling": 1, "pan": pan,
                                        "tilt": tilt},
                                       frame_w, frame_h)
        picture = delivered_picture(frame_w, frame_h, frame_w, frame_h,
                                    {"ZoomX": 1.0, "ZoomY": 1.0,
                                     "Pan": pan, "Tilt": tilt})
        assert (round(overlay[0]), round(overlay[1])) == \
            (picture.left, picture.top), (
            f"the overlay path and the picture path disagree at "
            f"pan {pan} tilt {tilt}")


def test_both_paths_are_the_law_and_not_a_second_copy_of_it():
    """Each call site's answer IS `resolve_transform`'s answer.

    Driven against the law called directly, so re-deriving the
    arithmetic inside either module - the way both wrong models came
    to exist - fails here rather than at the next rebuild.
    """
    frame_w, frame_h = 1080, 1920
    # The overlay path, at native scale.
    for canvas_w, canvas_h, pan, tilt in ((840, 480, 0.0, -1740.0),
                                          (296, 480, 1167.568, 0.0),
                                          (724, 480, 0.0, 5184.0),
                                          (772, 540, 0.7, 2197.333)):
        placement = {"scaling": 1, "pan": pan, "tilt": tilt}
        assert canvas_screen_origin(canvas_w, canvas_h, placement,
                                    frame_w, frame_h) == \
            drawn_origin(canvas_w, canvas_h, frame_w, frame_h, pan, tilt,
                         NATIVE_BASE_SCALE)
        assert placement_for_box(canvas_w, canvas_h, 540.0, 1176.0,
                                 frame_w, frame_h) == {
            "scaling": 1,
            "pan": pan_tilt_for_centre(canvas_w, canvas_h, frame_w,
                                       frame_h, 540.0, 1176.0)[0],
            "tilt": pan_tilt_for_centre(canvas_w, canvas_h, frame_w,
                                        frame_h, 540.0, 1176.0)[1]}
    # The picture path, at the fit.
    for src_w, src_h, pan, tilt in ((3840, 2160, 46.341, 0.25),
                                    (3840, 2160, -12.0, 0.0),
                                    (1920, 1080, 100.0, -100.0)):
        base = fit_base_scale(src_w, src_h, frame_w, frame_h)
        picture = delivered_picture(src_w, src_h, frame_w, frame_h,
                                    {"ZoomX": 1.0, "ZoomY": 1.0,
                                     "Pan": pan, "Tilt": tilt})
        cx, cy = drawn_centre(src_w, src_h, frame_w, frame_h, pan, tilt,
                              base)
        assert (picture.left + picture.right) / 2.0 == pytest.approx(
            cx, abs=1.0)
        assert (picture.top + picture.bottom) / 2.0 == pytest.approx(
            cy, abs=1.0)


def test_the_box_file_reader_and_the_placer_share_one_origin():
    """`canvas_offset` is `drawn_origin` rounded, and nothing else."""
    from library.tools.tight_box import TightBox

    box = TightBox(width=920, height=480, props={},
                   placement={"scaling": 1, "pan": 0.0, "tilt": 2592.0},
                   union_w=920.0, union_h=480.0,
                   full_width=1080, full_height=1920)
    assert canvas_offset(box) == (80, 72), (
        "measured on an exported still of Reel 26: a 920x480 graphic "
        "stored at Tilt 2592 is found at frame rows 72..552")
    ox, oy = drawn_origin(920, 480, 1080, 1920, 0.0, 2592.0)
    assert canvas_offset(box) == (round(ox), round(oy))


# ── 3. What the two wrong models would have answered ─────────────────

def test_the_retired_models_are_gone_and_would_have_failed_here():
    """Named so a reader can see the size of what was corrected.

    Not a test of dead code - both numbers are computed here from the
    law, so they track it. It exists because "half" and "3.16x short"
    are the two errors the captain actually sees, and a reader should
    be able to check them without re-deriving anything.
    """
    import library.tools.tight_box as tight_box

    assert not hasattr(tight_box, "DRAW_GAIN_1080x1920")
    assert not hasattr(tight_box, "draw_gain")

    # The overlay error: the caption row 1380 needs Tilt -864, and the
    # retired gain computed -432, which draws the card 108px high.
    correct = placement_for_box(840, 480, 540.0, 1176.0, 1080, 1920)
    assert correct["tilt"] == pytest.approx(-864.0, abs=0.01)
    halved = canvas_screen_origin(840, 480,
                                  {"scaling": 1, "pan": 0.0,
                                   "tilt": correct["tilt"] / 2.0},
                                  1080, 1920)
    assert halved[1] == pytest.approx(936.0 - 108.0, abs=0.5)

    # The picture error: reading Tilt as frame pixels under-applies a
    # vertical aim by 1 / 0.31640625 = 3.1605x, and reverses it.
    base = fit_base_scale(3840, 2160, 1080, 1920)
    assert 1.0 / shift_px(1.0, 2160, 1920, base) == pytest.approx(3.1605,
                                                                  abs=0.001)
    assert drawn_centre(3840, 2160, 1080, 1920, 0.0, 100.0, base)[1] < 960.0, (
        "positive Tilt moves the picture UP; the retired model moved "
        "it down")
