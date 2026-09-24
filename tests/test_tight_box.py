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
import shutil
import subprocess
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
    RAIL_HEADROOM_FRACTION,
    TRAILING_MARGIN_PX,
    TightBox,
    TightBoxClipsInk,
    constant_caption_box,
    grow_to_hold_rail,
    grow_to_minimum,
    ink_touches_edge,
    ink_touches_edge_frames,
    placement_for_box,
    placement_limits,
    tighten_subtitle_props,
)

NEEDS_FFMPEG = shutil.which("ffmpeg") is None
FFMPEG_REASON = "needs ffmpeg; runs in CI, which installs it (AGENTS.md 9)"

FULL_W = 1080
FULL_H = 1920
SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}

#: The 2026-09-11 draw gain, pinned wherever a test below reproduces a
#: measurement from that calibration. The renderer draws
#: `resolve_transform.FALLBACK_DRAW_GAIN` today; these tests say what
#: it drew then, so a reader can tell history from the current truth
#: (which `tests/test_draw_gain_measured.py` derives from rendered
#: pixels rather than restating).
HISTORY_GAIN = 1.0


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








def test_tilt_puts_canvas_bottom_where_full_canvas_put_content():
    """The tight canvas bottom edge lands pad-below the full content
    bottom, and Tilt is the measured Resolve unit for that shift."""
    props = _props()
    box = tighten_subtitle_props(
        props, draw_gain=HISTORY_GAIN)
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
                          full_w=1080, full_h=1920,
                              draw_gain=HISTORY_GAIN)
    assert p["scaling"] == 1
    assert p["pan"] == pytest.approx(200, abs=3)
    assert p["tilt"] == pytest.approx(300, abs=3)






def test_emphasis_words_widen_the_box():
    plain = _props(cards=[_card("very small", 0, 20)])
    emph = _props(cards=[_card("very small", 0, 20, emphasis=("small",))])
    assert (tighten_subtitle_props(emph).union_w
            > tighten_subtitle_props(plain).union_w)






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
















def test_predictor_floors_canvas_to_caption_max_width():
    """The predictor's canvas never narrows past the wrap width.

    Below `captionMaxWidth` the render would rewrap onto more lines
    than the box planned for - which is exactly the failure that
    makes per-segment prediction unsafe. A two-letter card measures
    far narrower than the 840px wrap, so without the floor this box
    ships ~100px wide and the render wraps taller and clips.
    """
    box = tighten_subtitle_props(_props(cards=[_card("hi", 0, 20)]))
    assert box.width == 840
    assert box.props["style"]["captionMaxWidth"] == 840








def test_constant_canvas_refuses_a_wrap_the_frame_cannot_hold():
    """THE input that breaks it: a project declaring `captionMaxWidth`
    2000 on a 1080-wide frame needs a 2064-wide canvas, which leaves
    the frame. Clamping would cut ink, so the box refuses and the
    caller carries the card full canvas instead."""
    props = _props(style=_style(captionMaxWidth=2000))
    with pytest.raises(TightBoxClipsInk):
        constant_caption_box(props)






def _guard_frames(d, size, rects):
    """PNG frames with known ink rectangles, PIL only."""
    from PIL import Image, ImageDraw
    os.makedirs(d, exist_ok=True)
    for i, rect in enumerate(rects):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        if rect is not None:
            x0, y0, x1, y1 = rect
            ImageDraw.Draw(img).rectangle(
                [x0, y0, x1 - 1, y1 - 1], fill=(255, 255, 255, 255))
        img.save(os.path.join(d, f"frame-{i:02d}.png"))
    return sorted(os.path.join(d, f) for f in os.listdir(d))


def test_edge_guard_passes_interior_ink(tmp_path):
    paths = _guard_frames(str(tmp_path / "ok"), (904, 480),
                          [(302, 200, 602, 280)] * 3)
    guard = ink_touches_edge_frames(paths, 904, 480)
    assert guard.touches_edge is False
    assert guard.empty is False
    assert guard.frames == 3


def test_edge_guard_fires_on_edge_ink(tmp_path):
    paths = _guard_frames(str(tmp_path / "edge"), (904, 480),
                          [(302, 200, 602, 280), (100, 100, 904, 200)])
    guard = ink_touches_edge_frames(paths, 904, 480)
    assert guard.touches_edge is True
    assert guard.border_max >= 32


def test_edge_guard_names_an_empty_render(tmp_path):
    paths = _guard_frames(str(tmp_path / "blank"), (904, 480),
                          [None, None])
    guard = ink_touches_edge_frames(paths, 904, 480)
    assert guard.touches_edge is False
    assert guard.empty is True


