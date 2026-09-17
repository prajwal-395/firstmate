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
    the placement-derived growth, which always goes AWAY from the
    anchor: bottom-anchored cards keep their bottom inset exactly, and
    the top absorbs the growth, so the ink does not move.
    """
    box = tighten_subtitle_props(_props())
    assert box.props["style"]["safeArea"]["bottom"] == PAD_BOTTOM
    assert box.props["style"]["safeArea"]["right"] == PAD_X
    assert box.props["style"]["safeArea"]["left"] == PAD_X
    assert box.props["style"]["safeArea"]["top"] >= PAD_TOP
    # The two-card union (127.3px) measures 180 tall and ships it:
    # its own placement needs Tilt -6258, inside the rail with margin
    # to spare. Derived per graphic in `grow_to_hold_rail`.
    assert box.height == 180


def test_centered_captions_need_no_pan():
    """Every card is centred, so the union is centred: dx is zero."""
    box = tighten_subtitle_props(_props())
    assert box.placement["scaling"] == 1
    assert box.placement["pan"] == 0


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


def test_top_positioned_captions_anchor_from_the_top():
    props = _props(style=_style(position="top"))
    box = tighten_subtitle_props(
        props, draw_gain=HISTORY_GAIN)
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
                          full_w=1080, full_h=1920,
                              draw_gain=HISTORY_GAIN)
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
                          full_w=3840, full_h=2160,
                              draw_gain=HISTORY_GAIN)
    assert p["scaling"] == 1
    assert p["pan"] == pytest.approx(200, abs=3)
    assert p["tilt"] == pytest.approx(300, abs=3)


def test_taller_union_gives_taller_canvas():
    one_line = _props(cards=[_card("hi there", 0, 20)])
    # A long card wraps onto many lines; past the placement-derived
    # floor the union is the tallest card, so it stands taller than a
    # single-line segment. Neither rides the constant: one line ships
    # its 170 measured rows untouched (Tilt -6679, inside the rail),
    # the tall card its 1284 (already inside at -52).
    many = _props(cards=[
        _card(("today I have a big announcement to make about the brand "
               "template and it will change everything ") * 4, 0, 60),
    ])
    assert (tighten_subtitle_props(many).height
            > tighten_subtitle_props(one_line).height)
    assert tighten_subtitle_props(one_line).height == 170
    assert tighten_subtitle_props(many).height == 1284


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


def test_small_box_is_floored_at_its_own_placement():
    """The third option past tight-vs-full: a small union renders on
    the canvas ITS placement needs, not a constant. The two-letter
    card measures 170 tall and ships it - its own placement needs
    Tilt -6679, inside the 4x-law rail with a thousand units to
    spare - so no growth happens at all. Growth past the measured
    size happens only past the headroom target (see the unit tests),
    and the margin it grows into is `RAIL_HEADROOM_FRACTION`, stated
    and pinned below."""
    box = tighten_subtitle_props(_props(cards=[_card("hi", 0, 20)]))
    assert box.height == 170
    assert box.height % 2 == 0
    assert abs(box.placement["tilt"]) <= 6912.0
    assert abs(box.placement["tilt"]) <= (
        placement_limits(1080, 1920)[1] * (1 - RAIL_HEADROOM_FRACTION))


def test_placement_formula_reproduces_the_captains_live_numbers():
    """The brief's table pins the inversion: a 152-tall canvas centred
    600px below frame centre needs Tilt -7578.9 on 1080x1920 - which
    is why the small boxes never landed in 2026-09-10 (the rail then
    read 3840, so every one of them pinned) and why
    `MIN_CANVAS_HEIGHT` exists. Against the widened 4x-law rail that
    same -7578.9 sits just inside 7680 - history, not gate: the floor
    is derived per graphic now, and the gate answers off
    `MEASURED_RAILS`."""
    p = placement_for_box(canvas_w=900, canvas_h=152,
                          canvas_cx=540, canvas_cy=960 + 600,
                          full_w=1080, full_h=1920,
                              draw_gain=HISTORY_GAIN)
    assert p["tilt"] == pytest.approx(-7578.9, abs=0.5)
    grown = placement_for_box(canvas_w=900, canvas_h=480,
                              canvas_cx=540, canvas_cy=960 + 600,
                              full_w=1080, full_h=1920,
                                  draw_gain=HISTORY_GAIN)
    assert abs(grown["tilt"]) <= 3400


def test_rail_headroom_is_a_stated_ten_percent():
    """The margin is deliberate and pinned: 10% inside whatever rail
    `placement_limits` returns. Against the 4x-law 7680 Tilt rail the
    target is 6912 - re-examined on the widening, not inherited (see
    `RAIL_HEADROOM_FRACTION`: the old 160-band argument retired with
    the old rail; the binary search is exact to ~1 unit). A change
    here must argue from the measurement, not pick a new number."""
    assert RAIL_HEADROOM_FRACTION == 0.10
    assert 7680.0 * (1 - RAIL_HEADROOM_FRACTION) == 6912.0


def test_derived_floor_grows_only_as_far_as_the_tilt_needs():
    """The bottom-anchored caption shape: 848 wide, 112 measured, ink
    hanging at content top 1540 on 1080x1920. 164 misses the 6912
    target (-6954); 166 lands it (-6859). The floor is the smallest
    even height that holds - grown only far enough, never to the
    480 constant."""
    grown, top_extra = grow_to_hold_rail(
        848, 112, "bottom", 540.0, 1540 - PAD_TOP + 112 / 2.0,
        1080, 1920, 1920,
            draw_gain=HISTORY_GAIN)
    assert (grown, top_extra) == (166, 54)
    assert grown % 2 == 0
    # And one step down really does miss: minimality is measured,
    # not asserted by construction.
    from library.tools.resolve_transform import pan_tilt_for_centre
    origin_y = (1540 - PAD_TOP + 112 / 2.0) - 112 / 2.0
    below_extra = (grown - 2) - 112
    _, tilt_below = pan_tilt_for_centre(
        848, grown - 2, 1080, 1920,
        540.0, origin_y - below_extra + (grown - 2) / 2.0,
            draw_gain=HISTORY_GAIN)
    assert abs(tilt_below) > 6912.0


def test_derived_floor_keeps_the_anchor_split():
    """Growth still goes AWAY from the anchor, exactly the
    `grow_to_minimum` split: bottom grows above, top grows below,
    middle splits - so the ink does not move, whatever the height."""
    # Bottom: the whole growth above.
    grown, top_extra = grow_to_hold_rail(
        848, 112, "bottom", 540.0, 1540 - PAD_TOP + 112 / 2.0,
        1080, 1920, 1920,
            draw_gain=HISTORY_GAIN)
    assert top_extra == grown - 112
    # Top: nothing above (mirror geometry: content top row 140).
    grown, top_extra = grow_to_hold_rail(
        848, 112, "top", 540.0, 140 - PAD_TOP + 112 / 2.0,
        1080, 1920, 1920,
            draw_gain=HISTORY_GAIN)
    assert (grown, top_extra) == (204, 0)
    # Middle: the split. Centred ink needs no Tilt at all, so the
    # measured height ships untouched.
    grown, top_extra = grow_to_hold_rail(
        840, 292, "middle", 540.0, 960.0, 1080, 1920, 1920,
            draw_gain=HISTORY_GAIN)
    assert (grown, top_extra) == (292, 0)


def test_derived_floor_falls_back_to_the_constant():
    """Refusing to shrink is always safe: an unmeasured frame, or a
    Pan no height growth can fix, lands exactly where
    `grow_to_minimum` would have put it - the current behaviour."""
    assert grow_to_hold_rail(
        848, 112, "bottom", 540.0, 1580.0, 1080, 1080, 1080,
        draw_gain=HISTORY_GAIN) == \
        grow_to_minimum(112, "bottom", 1080) == (480, 368)
    # Pan 4644 the 4320 rail cannot hold: height growth moves Tilt
    # only, so the constant is the only honest answer here and the
    # downstream gate still refuses it to full canvas, as today.
    assert grow_to_hold_rail(
        200, 112, "bottom", 1400.0, 1580.0, 1080, 1920, 1920,
        draw_gain=HISTORY_GAIN) == \
        grow_to_minimum(112, "bottom", 1920) == (480, 368)


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


def test_constant_canvas_is_the_structural_bound():
    """904 for the captain's 840px wrap: `captionMaxWidth + 2*PAD_X`
    for the shadow, plus the trailing word margin the wrap counts on
    every word span including the last. The number is derived from
    the props, never fitted to a sample."""
    box = constant_caption_box(_props())
    assert isinstance(box, TightBox)
    assert box.width == 840 + 2 * PAD_X + TRAILING_MARGIN_PX == 904
    assert box.height == MIN_CANVAS_HEIGHT == 480
    assert box.props["width"] == 904
    assert box.props["height"] == 480
    assert box.props["style"]["captionMaxWidth"] == 840


def test_constant_canvas_derives_from_the_props_not_the_project():
    """A different wrap width derives a different canvas by the same
    rule - the bound is structural, not the 904 of one project."""
    for max_width, expected in ((840, 904), (600, 664), (900, 964)):
        props = _props(style=_style(captionMaxWidth=max_width))
        box = constant_caption_box(props)
        assert box.width == expected


def test_constant_canvas_never_drops_below_the_structural_bound():
    """For every wrap width the canvas clears the ink bound -
    `captionMaxWidth + 2*PAD_X` - with the trailing margin to spare.
    A canvas below it re-lays-out the card it carries."""
    for max_width in (200, 400, 600, 840, 900, 1000):
        props = _props(style=_style(captionMaxWidth=max_width))
        box = constant_caption_box(props)
        assert box.width % 2 == 0
        assert box.width >= max_width + 2 * PAD_X


def test_constant_canvas_refuses_a_wrap_the_frame_cannot_hold():
    """THE input that breaks it: a project declaring `captionMaxWidth`
    2000 on a 1080-wide frame needs a 2064-wide canvas, which leaves
    the frame. Clamping would cut ink, so the box refuses and the
    caller carries the card full canvas instead."""
    props = _props(style=_style(captionMaxWidth=2000))
    with pytest.raises(TightBoxClipsInk):
        constant_caption_box(props)


def test_constant_placement_is_arithmetic_from_the_anchor():
    """Centred cards on a centred canvas: pan 0 on every position.
    Vertically the canvas edge sits one pad past the anchored card
    edge - bottom cards hang the canvas bottom 36px below the row,
    top cards hang its top 16px above it, centred cards sit centred."""
    bottom = constant_caption_box(_props(),
        draw_gain=HISTORY_GAIN)
    assert bottom.placement["scaling"] == 1
    assert bottom.placement["pan"] == 0
    assert bottom.placement["tilt"] == pytest.approx(-1744.0)
    assert bottom.props["style"]["safeArea"]["bottom"] == PAD_BOTTOM

    top = constant_caption_box(_props(style=_style(position="top")),
        draw_gain=HISTORY_GAIN)
    assert top.placement["pan"] == 0
    assert top.props["style"]["safeArea"]["top"] == PAD_TOP

    center = constant_caption_box(
        _props(style=_style(position="center")),
            draw_gain=HISTORY_GAIN)
    assert center.placement["pan"] == 0
    assert center.placement["tilt"] == pytest.approx(0.0)


def test_constant_box_needs_geometry_like_the_component():
    """No subtitles means no box; no safeArea/captionMaxWidth refuses,
    exactly as the component refuses to place by a literal."""
    assert constant_caption_box(_props(cards=[])) is None
    style = _style()
    del style["safeArea"]
    with pytest.raises(ValueError, match="safeArea"):
        constant_caption_box(_props(style=style))
    style = _style()
    del style["captionMaxWidth"]
    with pytest.raises(ValueError, match="captionMaxWidth"):
        constant_caption_box(_props(style=style))


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


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_edge_guard_reads_a_mov_off_the_alpha_plane(tmp_path):
    """The video half of the guard: one ffmpeg decode, no transient
    PNGs, interior ink passes and edge ink fires."""
    clean = str(tmp_path / "clean.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black@0:s=904x480:d=1:r=30,format=rgba",
         "-f", "lavfi", "-i", "color=white:s=300x80:d=1:r=30,format=rgba",
         "-filter_complex", "[0][1]overlay=302:200,format=rgba",
         "-frames:v", "5", "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", clean],
        check=True)
    guard = ink_touches_edge(clean, 904, 480)
    assert (guard.touches_edge, guard.empty, guard.frames) == \
        (False, False, 5)
    edge = str(tmp_path / "edge.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=black@0:s=904x480:d=1:r=30,format=rgba",
         "-f", "lavfi", "-i", "color=white:s=804x80:d=1:r=30,format=rgba",
         "-filter_complex", "[0][1]overlay=100:0,format=rgba",
         "-frames:v", "5", "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", edge],
        check=True)
    guard = ink_touches_edge(edge, 904, 480)
    assert guard.touches_edge is True
