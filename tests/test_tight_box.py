"""A caption card is mostly transparent canvas. Render only the ink.

Today every subtitle segment renders at the full delivery frame
(1080x1920) - two million pixels per frame to draw a caption occupying
a few percent of them. That is slow to render, heavy on disk, and fixes
the position at render time, so repositioning means re-rendering.

A tight box renders only the drawn bounds - the union of the segment's
cards, bottom-anchored exactly as the composition lays them out - and
lands on the Resolve timeline as a small clip placed at an offset
(`Scaling=1` for native pixels, then Pan/Tilt). Smaller, faster, and
MOVABLE after the fact.

Feasibility, measured 2026-09-08 on a scratch Resolve project (never
the captain's):
- `ImportMedia` takes a smaller-than-timeline ProRes mov and places it.
- Per-clip `Scaling=1` draws it at native pixels, centred. 0 and 2 fit
  the image to the frame; 3 stretches it full-frame.
- Pan/Tilt move it in measured output pixels: shift_x = Pan *
  (placed_W / timeline_W), shift_y = -Tilt * (placed_H / timeline_H).
- `ImportMedia` of N PNG frames yields ONE pool item whose File Path
  reads `seq_[0001-0005].png`, and it places with a frame duration.
- `timeline.CreateCompoundClip` exists and returns an object.

So both halves of the captain's note are real: the bounds are knowable
at plan time (the fitter already measures every word in pixels), and
Resolve accepts frames directly.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.tight_box import (  # noqa: E402
    MIN_CANVAS_HEIGHT,
    PAD_BOTTOM,
    PAD_TOP,
    PAD_X,
    TightBox,
    placement_for_box,
    tighten_subtitle_props,
)

FULL_W = 1080
FULL_H = 1920
SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


def _style(**over):
    base = {
        "fontFamily": "Montserrat",
        "fontSize": 58,
        "fontWeight": 800,
        "fontColor": "#FFFFFF",
        "accentColor": "#FBF0B8",
        "outlineColor": "#000000",
        "outlineWidth": 4,
        "position": "bottom",
        "safeArea": dict(SAFE),
        "captionMaxWidth": 840,
    }
    base.update(over)
    return base


def _card(text, start, end, emphasis=(), fit=1.0):
    words = text.split()
    n = len(words)
    span = end - start
    return {
        "text": text,
        "startFrame": start,
        "endFrame": end,
        "emphasisWords": list(emphasis),
        "words": [
            {"word": w,
             "startFrame": start + span * i // n,
             "endFrame": start + span * (i + 1) // n}
            for i, w in enumerate(words)
        ],
        "fitScale": fit,
    }


def _props(style=None, cards=None):
    style = style or _style()
    cards = cards if cards is not None else [
        _card("and so my very", 0, 21),
        _card("very small", 21, 42, emphasis=("small",)),
    ]
    return {
        "subtitles": cards,
        "fps": 30,
        "width": FULL_W,
        "height": FULL_H,
        "durationInFrames": 60,
        "style": style,
    }


def test_tight_canvas_is_smaller_than_full_frame():
    box = tighten_subtitle_props(_props())
    assert isinstance(box, TightBox)
    assert 0 < box.width < FULL_W
    assert 0 < box.height < FULL_H


def test_canvas_dimensions_are_even():
    box = tighten_subtitle_props(_props())
    assert box.width % 2 == 0
    assert box.height % 2 == 0


def test_wrapping_basis_is_unchanged_so_layout_matches_full_canvas():
    """The tight render must wrap exactly like the full-canvas one.

    captionMaxWidth is what the flex container wraps against. If the
    tight props changed it, cards would break onto different lines and
    the box would fit a layout the full render never drew.
    """
    props = _props()
    box = tighten_subtitle_props(props)
    assert box.props["style"]["captionMaxWidth"] == \
        props["style"]["captionMaxWidth"]
    assert box.props["width"] == box.width
    assert box.props["height"] == box.height


def test_subtitles_and_timing_pass_through_untouched():
    props = _props()
    box = tighten_subtitle_props(props)
    assert box.props["subtitles"] == props["subtitles"]
    assert box.props["durationInFrames"] == props["durationInFrames"]
    assert box.props["fps"] == props["fps"]


def test_tight_safe_area_is_padding_plus_rail_growth():
    """The component positions against style.safeArea pixels.

    On a small canvas the platform insets are meaningless - the box is
    placed by Resolve, not by the render. The insets are the pads plus
    the minimum-height growth, which always goes AWAY from the anchor:
    bottom-anchored cards keep their bottom inset exactly, and the top
    absorbs the growth, so the ink does not move.
    """
    box = tighten_subtitle_props(_props())
    assert box.props["style"]["safeArea"]["bottom"] == PAD_BOTTOM
    assert box.props["style"]["safeArea"]["right"] == PAD_X
    assert box.props["style"]["safeArea"]["left"] == PAD_X
    assert box.props["style"]["safeArea"]["top"] >= PAD_TOP
    assert box.height >= MIN_CANVAS_HEIGHT


def test_centered_captions_need_no_pan():
    """Every card is centred, so the union is centred: dx is zero."""
    box = tighten_subtitle_props(_props())
    assert box.placement["scaling"] == 1
    assert box.placement["pan"] == 0


def test_tilt_puts_canvas_bottom_where_full_canvas_put_content():
    """The tight canvas bottom edge lands pad-below the full content
    bottom, and Tilt is the measured Resolve unit for that shift."""
    props = _props()
    box = tighten_subtitle_props(props)
    # The canvas origin is the content edge minus the RENDERED top
    # inset (pads plus any minimum-height growth above the anchor),
    # read off the props the render draws from rather than assumed.
    content_bottom = FULL_H - SAFE["bottom"]
    content_top = content_bottom - box.union_h
    canvas_top = (content_top
                  - box.props["style"]["safeArea"]["top"])
    canvas_cy = canvas_top + box.height / 2
    dy = canvas_cy - FULL_H / 2
    # No gain term: the shift is the clip-size proportion and nothing
    # else (`library/tools/resolve_transform.py`, re-measured on 16
    # rendered plates 2026-09-11).
    assert box.placement["tilt"] == pytest.approx(
        -dy * (FULL_H / box.height))


def test_top_positioned_captions_anchor_from_the_top():
    props = _props(style=_style(position="top"))
    box = tighten_subtitle_props(props)
    content_top = SAFE["top"]
    canvas_top = (content_top
                  - box.props["style"]["safeArea"]["top"])
    canvas_cy = canvas_top + box.height / 2
    dy = canvas_cy - FULL_H / 2
    assert box.placement["tilt"] == pytest.approx(
        -dy * (FULL_H / box.height))
    assert box.placement["pan"] == 0


def test_placement_for_box_uses_measured_resolve_units():
    """Pan/Tilt move a native-pixel clip by its OWN size proportion.

    The 2026-09-08 scratch measurement - Pan=200 moves a 400px-wide
    clip 74px on a 1080 timeline, Tilt=300 moves a 200px-tall clip
    31px on a 1920 one - is the relation, re-confirmed on 16 rendered
    plates across two builds and four processes on 2026-09-11. The
    "2x draw gain" briefly recorded between those two measurements
    was calibrated against a captured Pan/Tilt rather than one it had
    set, and is gone.
    """
    p = placement_for_box(canvas_w=400, canvas_h=200,
                          canvas_cx=540 + 74, canvas_cy=960 - 31,
                          full_w=1080, full_h=1920)
    assert p["scaling"] == 1
    assert p["pan"] == pytest.approx(200, abs=3)
    assert p["tilt"] == pytest.approx(300, abs=3)


def test_placement_for_box_is_one_relation_at_every_frame_size():
    """And it is NOT scoped to a frame: the same proportion answers
    3840x2160, because it is geometry rather than a constant somebody
    measured on one timeline."""
    p = placement_for_box(canvas_w=400, canvas_h=200,
                          canvas_cx=1920 + 200 * 400 / 3840,
                          canvas_cy=1080 - 300 * 200 / 2160,
                          full_w=3840, full_h=2160)
    assert p["scaling"] == 1
    assert p["pan"] == pytest.approx(200, abs=3)
    assert p["tilt"] == pytest.approx(300, abs=3)


def test_taller_union_gives_taller_canvas():
    one_line = _props(cards=[_card("hi there", 0, 20)])
    # A long card wraps onto many lines; past the minimum-height
    # floor the union is the tallest card, so it stands taller than a
    # single-line segment (both would tie at the floor).
    many = _props(cards=[
        _card(("today I have a big announcement to make about the brand "
               "template and it will change everything ") * 4, 0, 60),
    ])
    assert (tighten_subtitle_props(many).height
            > tighten_subtitle_props(one_line).height)
    assert tighten_subtitle_props(one_line).height == MIN_CANVAS_HEIGHT


def test_emphasis_words_widen_the_box():
    plain = _props(cards=[_card("very small", 0, 20)])
    emph = _props(cards=[_card("very small", 0, 20, emphasis=("small",))])
    assert (tighten_subtitle_props(emph).union_w
            > tighten_subtitle_props(plain).union_w)


def test_fit_scale_shrinks_the_measurement():
    full = _props(cards=[_card("announcement", 0, 20, fit=1.0)])
    shrunk = _props(cards=[_card("announcement", 0, 20, fit=0.5)])
    assert (tighten_subtitle_props(shrunk).union_w
            < tighten_subtitle_props(full).union_w)


def test_no_subtitles_means_no_box():
    props = _props(cards=[])
    assert tighten_subtitle_props(props) is None


def test_missing_geometry_refuses_like_the_component():
    """The component throws without safeArea/captionMaxWidth rather
    than placing by a literal. The box must refuse for the same input."""
    style = _style()
    del style["safeArea"]
    with pytest.raises(ValueError, match="safeArea"):
        tighten_subtitle_props(_props(style=style))
    style = _style()
    del style["captionMaxWidth"]
    with pytest.raises(ValueError, match="captionMaxWidth"):
        tighten_subtitle_props(_props(style=style))


def test_pads_cover_shadow_blur_outline_and_qa_margin():
    """The shadow paints 20px blur + 10px down-offset outside the glyph
    box; the boldest style outlines 18px; QA fails ink within 2% of an
    edge. Pads must clear all three, or the box clips what it keeps."""
    assert PAD_X >= 20 + 2
    assert PAD_BOTTOM >= 30 + 2
    assert PAD_TOP >= 10 + 2


def test_small_box_is_floored_at_the_minimum_canvas_height():
    """The third option past tight-vs-full: a small union renders on
    a 480-tall canvas, not a 152-tall one, so the placing Tilt stays
    inside Resolve's rail (see `MIN_CANVAS_HEIGHT`)."""
    box = tighten_subtitle_props(_props(cards=[_card("hi", 0, 20)]))
    assert box.height == MIN_CANVAS_HEIGHT
    assert box.height % 2 == 0
    assert abs(box.placement["tilt"]) <= 3400


def test_placement_formula_reproduces_the_captains_live_numbers():
    """The brief's table pins the inversion: a 152-tall canvas centred
    600px below frame centre needs Tilt -7578.9 on 1080x1920 - twice
    the measured 3840 rail, which is why the small boxes never landed
    even correctly computed, and why `MIN_CANVAS_HEIGHT` exists."""
    p = placement_for_box(canvas_w=900, canvas_h=152,
                          canvas_cx=540, canvas_cy=960 + 600,
                          full_w=1080, full_h=1920)
    assert p["tilt"] == pytest.approx(-7578.9, abs=0.5)
    grown = placement_for_box(canvas_w=900, canvas_h=480,
                              canvas_cx=540, canvas_cy=960 + 600,
                              full_w=1080, full_h=1920)
    assert abs(grown["tilt"]) <= 3400
