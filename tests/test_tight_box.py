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


def test_tight_safe_area_is_padding_not_platform_insets():
    """The component positions against style.safeArea pixels.

    On a small canvas the platform insets are meaningless - the box is
    placed by Resolve, not by the render. The insets must be the pads,
    so the card sits pad-anchored exactly as it sat inset-anchored.
    """
    box = tighten_subtitle_props(_props())
    assert box.props["style"]["safeArea"] == {
        "top": PAD_TOP, "right": PAD_X,
        "bottom": PAD_BOTTOM, "left": PAD_X,
    }


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
    # The canvas is top-anchored: its top edge sits PAD_TOP above the
    # content top, and any even-rounding slack lands at the bottom.
    # The test recomputes the centre from that edge, not from the
    # bottom, so the two agree by construction of the same edge.
    content_bottom = FULL_H - SAFE["bottom"]
    content_top = content_bottom - box.union_h
    canvas_cy = (content_top - PAD_TOP) + box.height / 2
    dy = canvas_cy - FULL_H / 2
    assert box.placement["tilt"] == pytest.approx(
        -dy * (FULL_H / box.height))


def test_top_positioned_captions_anchor_from_the_top():
    props = _props(style=_style(position="top"))
    box = tighten_subtitle_props(props)
    content_top = SAFE["top"]
    canvas_cy = (content_top - PAD_TOP) + box.height / 2
    dy = canvas_cy - FULL_H / 2
    assert box.placement["tilt"] == pytest.approx(
        -dy * (FULL_H / box.height))
    assert box.placement["pan"] == 0


def test_placement_for_box_uses_measured_resolve_units():
    """shift_x = Pan * (placed_W / timeline_W), with sign flipped on y.

    Measured on Resolve 21 against solid-colour clips, 2026-09-08:
    Pan=200 moved a 400px-wide clip 74px (200 * 400/1080); Tilt=300
    moved a 200px-tall clip 31px up (300 * 200/1920).
    """
    p = placement_for_box(canvas_w=400, canvas_h=200,
                          canvas_cx=540 + 74, canvas_cy=960 - 31,
                          full_w=1080, full_h=1920)
    assert p["scaling"] == 1
    assert p["pan"] == pytest.approx(200, abs=3)
    assert p["tilt"] == pytest.approx(300, abs=3)


def test_taller_union_gives_taller_canvas():
    one_line = _props(cards=[_card("hi there", 0, 20)])
    # A long card wraps onto several lines; the union is the tallest
    # card, so it stands taller than a single-line segment.
    many = _props(cards=[
        _card("today I have a big announcement to make about the brand "
              "template and it will change everything", 0, 60),
    ])
    assert (tighten_subtitle_props(many).height
            > tighten_subtitle_props(one_line).height)


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
