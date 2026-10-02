"""A caption card is mostly transparent canvas. Render only the ink.

A tight box renders only the drawn bounds, bottom-anchored exactly as the
composition lays them out, and lands as a small clip placed by
`Scaling=1` plus Pan/Tilt. Feasibility measurements:
docs/evidence/tight_box.md#feasibility-2026-09-08.
"""
import os
import shutil
import sys
import pytest
import subprocess


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.tight_box import (  # noqa: E402
    TightBoxClipsInk,
    constant_caption_box,
    ink_touches_edge_frames,
    placement_for_box,
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
#: (which `tests/unit/resolve/test_draw_gain_measured.py` derives from rendered
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


def test_the_box_wraps_like_the_full_canvas_on_even_dimensions():
    """The tight render must wrap exactly like the full-canvas one:
    captionMaxWidth is what the flex container wraps against, and the
    canvas never narrows past it (a two-letter card would otherwise ship
    ~100px wide and the render would rewrap taller and clip)."""
    props = _props()
    box = tighten_subtitle_props(props)
    assert box.width % 2 == 0
    assert box.height % 2 == 0
    assert box.props["style"]["captionMaxWidth"] == \
        props["style"]["captionMaxWidth"]
    assert box.props["width"] == box.width
    assert box.props["height"] == box.height
    box = tighten_subtitle_props(_props(cards=[_card("hi", 0, 20)]))
    assert box.width == 840
    assert box.props["style"]["captionMaxWidth"] == 840


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


def test_edge_guard_passes_interior_ink_fires_on_edge_ink_names_empty(
        tmp_path):
    paths = _guard_frames(str(tmp_path / "ok"), (904, 480),
                          [(302, 200, 602, 280)] * 3)
    guard = ink_touches_edge_frames(paths, 904, 480)
    assert guard.touches_edge is False
    assert guard.empty is False
    assert guard.frames == 3

    paths = _guard_frames(str(tmp_path / "edge"), (904, 480),
                          [(302, 200, 602, 280), (100, 100, 904, 200)])
    guard = ink_touches_edge_frames(paths, 904, 480)
    assert guard.touches_edge is True
    assert guard.border_max >= 32

    paths = _guard_frames(str(tmp_path / "blank"), (904, 480),
                          [None, None])
    guard = ink_touches_edge_frames(paths, 904, 480)
    assert guard.touches_edge is False
    assert guard.empty is True


# --------------------------------------------------------------------------
# From test_tight_box_measured.py
#
# The shared pixel primitives and the gates around them.
#
# `ink_union_of_frames` reads the drawn union off decoded frames - the
# measurement the motion-graphics path still binds from
# (`measure_mg_union` in `mg_tight_box.py`). The caption probe path
# that once sized boxes from that union (`tighten_measured`, the
# correspondence read-off, `verify_frames`) is retired: the caption
# path renders the constant structural canvas and guards it with
# ink-touches-edge instead (see `tight_box`'s module docstring for
# that history, and `test_tight_box.py` for the constant path).
#
# What stays under test here: the union primitive itself, the
# Pan/Tilt rail gate (`placement_holds` / `placement_limits`) every
# tight placement ships through, and the reuse cache
# (`restore_reused_placement`) the caption renderer restores sidecars
# through.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.tight_box import (
    TightBoxMismatch,
    ink_union_of_frames,
    placement_holds,
    restore_reused_placement,
)


def _frame(path, size, rects):
    """One RGBA frame: transparent canvas with opaque white rects."""
    from PIL import Image
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for (x0, y0, x1, y1) in rects:
        for x in range(x0, x1):
            for y in range(y0, y1):
                img.putpixel((x, y), (255, 255, 255, 255))
    img.save(path)
    return path


def _frames(tmp_path, name, size, per_frame_rects):
    d = os.path.join(str(tmp_path), name)
    os.makedirs(d, exist_ok=True)
    paths = []
    for i, rects in enumerate(per_frame_rects):
        paths.append(_frame(os.path.join(d, f"f_{i:03d}.png"), size, rects))
    return paths


def _props_2(position="bottom"):
    return {
        "subtitles": [{"text": "measured words"}],
        "fps": 30,
        "width": FULL_W,
        "height": FULL_H,
        "durationInFrames": 3,
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "position": position,
            "safeArea": {"top": 120, "right": 120,
                         "bottom": 320, "left": 90},
            "captionMaxWidth": 840,
        },
    }


def test_the_union_spans_ink_across_frames_and_blank_measures_nothing(
        tmp_path):
    paths = _frames(tmp_path, "blank", (FULL_W, FULL_H), [[], [], []])
    assert ink_union_of_frames(paths) is None
    paths = _frames(tmp_path, "two", (FULL_W, FULL_H),
                    [[(100, 1500, 300, 1560)], [(200, 1400, 400, 1480)], []])
    union = ink_union_of_frames(paths)
    assert union is not None
    assert (union.x0, union.y0, union.x1, union.y1) == (100, 1400, 400, 1560)


def test_the_rail_gate_holds_only_what_was_probed_and_names_the_axis():
    """1920x1080 was PROBED on 2026-09-13 and carries its own row (Pan
    7680, Tilt 4320 - 4x width and height); it is NOT the vertical row's
    numbers carried across, which on Tilt would sit above the real 4320.
    A frame nobody probed REFUSES rather than borrowing another's row.
    One predicate for every path that ships a placement."""
    from library.tools.tight_box import placement_limits

    assert placement_limits(1920, 1080) == (7680.0, 4320.0)
    assert placement_limits(1920, 1080) != placement_limits(1080, 1920)
    assert placement_holds({"pan": 0.0, "tilt": -100.0}, 1920, 1080) == ""
    assert placement_holds({"pan": 7000.0, "tilt": -4000.0}, 1920, 1080) == ""
    reason = placement_holds({"pan": 0.0, "tilt": -4400.0}, 1920, 1080)
    assert "1920x1080" in reason and "4320" in reason
    reason = placement_holds({"pan": 7700.0, "tilt": 0.0}, 1920, 1080)
    assert "1920x1080" in reason and "7680" in reason

    assert placement_limits(1080, 1080) is None
    reason = placement_holds({"pan": 0.0, "tilt": -100.0}, 1080, 1080)
    assert "1080x1080" in reason and "never been measured" in reason

    assert placement_holds({"pan": 0.0, "tilt": -100.0}, 1080, 1920) == ""
    assert placement_holds(None, 1080, 1920) == ""
    assert "Tilt -7929.0" in placement_holds(
        {"pan": 0.0, "tilt": -7929.0}, 1080, 1920)
    assert "Pan 9188.0" in placement_holds(
        {"pan": 9188.0, "tilt": 0.0}, 1080, 1920)


def test_reused_placement_restores_when_it_holds():
    """A sidecar whose placement Resolve can hold re-gates clean:
    reuse keeps its win."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    sidecar = {
        "width": 840,
        "height": 146,
        "placement": {"scaling": 1, "pan": 0.0, "tilt": -100.0},
        "carriage": OVERLAY_CARRIAGE,
        "draw_gain": 2.0,
        "safe_area": {"top": 120, "right": 120,
                      "bottom": 320, "left": 90},
        "union": {"x0": 100, "y0": 1200, "x1": 400, "y1": 1294},
    }
    restored = restore_reused_placement(sidecar, _props_2(), (1080, 1920))
    assert restored.placement == {"scaling": 1, "pan": 0.0, "tilt": -100.0}
    assert (restored.width, restored.height) == (840, 146)
    assert (restored.union_w, restored.union_h) == (300.0, 94.0)


def test_a_reused_placement_that_cannot_be_trusted_is_refused():
    """Each refusal sends the caller to a fresh measured render:
    a placement Resolve would clamp (the captain's predictor-era
    captions); a superseded carriage (`tight-480-2` carried HALF the Tilt
    - the caption drew ~108px high); no carriage stamp at all (the
    -1744.0/-1700.0 ledger sidecars); a malformed sidecar; and a
    placement computed under a superseded draw gain (the -1836 gate-build
    caption boxes)."""
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    safe = {"top": 120, "right": 120, "bottom": 320, "left": 90}
    gain_props = _props_2()
    gain_props["style"]["safeArea"] = dict(safe, bottom=297)
    cases = [
        ({"width": 348, "height": 146,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -7929.0},
          "carriage": OVERLAY_CARRIAGE, "draw_gain": 2.0, "safe_area": safe,
          "union": {"x0": 382, "y0": 1501, "x1": 682, "y1": 1595}},
         _props_2(), "no longer holds"),
        ({"width": 840, "height": 480,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -432.0},
          "carriage": "tight-480-2",
          "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596}},
         _props_2(), "superseded carriage"),
        ({"width": 840, "height": 480,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -850.0},
          "union": {"x0": 126, "y0": 1416, "x1": 937, "y1": 1596}},
         _props_2(), "superseded carriage"),
        ({"width": 348}, _props_2(), None),
        ({"width": 904, "height": 480,
          "placement": {"scaling": 1, "pan": 0.0, "tilt": -1836.0},
          "carriage": OVERLAY_CARRIAGE, "safe_area": dict(safe, bottom=297),
          "union": {"x0": 88, "y0": 1179, "x1": 992, "y1": 1659}},
         gain_props, "draw gain"),
    ]
    for sidecar, props, match in cases:
        with pytest.raises(TightBoxMismatch, match=match):
            restore_reused_placement(sidecar, props, (1080, 1920))


# --------------------------------------------------------------------------
# From test_mg_tight_box.py
#
# The drawn union of a motion-graphics segment, and where it lands.
#
# A motion-graphics segment today renders at the full delivery frame
# (1080x1920): two million pixels per frame to draw a title occupying a
# few percent of them. The position is baked in at render time, so
# repositioning means re-rendering. Captions already render as tight
# boxes (`library/tools/tight_box.py`, PR 725: 2.3-2.8x faster, a third
# the bytes) - this is the same mechanism for the other overlay kind.
#
# The question here is purely geometric: the union of what a
# `MotionGraphics` composition actually draws. Chrome spans the frame by
# design - four corner accents sit at four corners, so their union IS the
# frame - and the honest answer for such a composition is that tight-box
# buys nothing: `tighten_motion_graphics_props` returns None and the
# caller keeps the full-canvas path rather than forcing a win that is
# not there.
#
# What is bounded, in full-frame coordinates, per element
# (`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`):
#
# - `progress_bar`: an absolute strip, left..right safe insets, 12px tall
#   plus its glow, at the top or bottom inset. Exact.
# - `frame_accents`: four 80px squares at the four safe corners. Exact -
#   and their union is the whole safe box, so alone or with a bar they
#   cover the frame and there is no box.
# - Anchored copy (`title_lockup`, `quote_card`, `lower_third`,
#   `context_stamp`, `stat_callout`, `subject_emblem`, `counter_roll`,
#   `digit_counter`, `list_build`, `comparison_bars`, `step_counter`,
#   `beat_accent`, `pointer_annotation`): stacked by anchor in row order
#   inside one flex container per anchor. Text is measured with the same
#   Montserrat face the render loads (`CaptionFitter`), sizes and weights
#   read from the composition's own tables, and every estimate is an
#   OVER-estimate - a box that clips ink is a defect, a box with slack is
#   merely a smaller win.
#
# What forces the full canvas rather than a wrong box:
#
# - Asset elements (`channel_bug`, `website_panel`): their height comes
#   from a project-supplied file nothing measures. Bounding an unknown
#   aspect would be forcing the win, so they refuse it.
# - A middle-anchored stack beside another vertical zone: `top: 50%` is
#   relative to the CANVAS, so on a small canvas the stack centres on the
#   wrong frame. Top+bottom mixes are exact (both edges are canvas
#   edges); anything with middle mixed in is not, and falls back.
# - Unknown element keys draw nothing (the composition returns null for
#   them), so they are ignored - and a segment of nothing but unknowns
#   has no union to bound.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.mg_tight_box import (  # noqa: E402
    check_motion_graphics_files,
    sidecar_path_for,
    tighten_motion_graphics_props,
    tighten_motion_graphics_props_with_reason,
)


def _el(element, anchor="bottom_centre", row=0,
        runs=None, duration=60, start=0, **over):
    el = {
        "element": element,
        "anchor": anchor,
        "row": row,
        "runs": runs if runs is not None else [
            {"text": "and so my very", "type_role": "supporting"},
        ],
        "color": "#FFDD55",
        "entrance": "fade",
        "exit": "fade",
        "startFrame": start,
        "durationFrames": duration,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": None,
        "emphasis": None,
    }
    el.update(over)
    return el


def _props_3(elements, width=FULL_W, height=FULL_H, safe=None):
    total = max((e["startFrame"] + e["durationFrames"] for e in elements),
                default=60)
    return {
        "elements": elements,
        "fps": 30,
        "width": width,
        "height": height,
        "safeArea": dict(safe or SAFE),
        "durationInFrames": total,
    }


def _staged(name="Craig Lucie", title="CEO Lucie Content",
            anchor="bottom_left", **over):
    """A staged-rule lower third, the shape `speaker_identity` writes."""
    runs = [{"text": name, "type_role": "display"}]
    if title is not None:
        runs.append({"text": title, "type_role": "supporting"})
    el = _el("lower_third", anchor=anchor, runs=runs)
    el["data"] = {
        "construction": "staged_rule",
        "speaker": "Craig",
        "colour_basis": "pipeline.speaker_subtitle_styles['Craig']"
                        ".accentColor",
    }
    el.update(over)
    return el


def test_unknown_elements_are_ignored_beside_real_ones():
    # Mid-frame, so the box the test compares is one Resolve can
    # hold: a bottom-anchored box this small needs Tilt past the
    # clamp and is refused (see below), which would leave nothing
    # to compare.
    title = _el("title_lockup", anchor="middle_centre")
    with_unknown = tighten_motion_graphics_props(
        _props_3([title, _el("tracked_label", anchor="middle_centre")]))
    without = tighten_motion_graphics_props(_props_3([title]))
    assert with_unknown is not None and without is not None
    assert (with_unknown.width, with_unknown.height) == \
        (without.width, without.height)


def test_a_small_box_far_from_centre_rides_the_minimum_canvas():
    """Each needed Tilt past what Resolve holds on its measured canvas
    (the captain's ordinary bottom title: -8758). Grown away from its
    edge to the 480 floor, the ink does not move and the placement holds
    inside the 3840 rail - including the sixteen constructed (staged)
    lower thirds that used to render the whole delivery frame."""
    from library.tools.tight_box import placement_holds
    for element in (_el("progress_bar", anchor="bottom_centre"),
                    _el("title_lockup"),
                    _el("pointer_annotation", anchor="top_left",
                        footprint=0.5),
                    _staged()):
        box, refusal = tighten_motion_graphics_props_with_reason(
            _props_3([element]))
        assert refusal is None and box is not None, element["element"]
        assert box.height == 480
        assert 0 < box.width < FULL_W
        assert placement_holds(box.placement, FULL_W, FULL_H) == ""
        assert abs(box.placement["tilt"]) <= 3400


def test_top_and_bottom_mix_stays_tight():
    # Side-anchored on both edges: the union-sized canvas already
    # holds the layout, so no layout-width floor applies and the mix
    # stays tight. A CENTRE-anchored title in the same mix spans the
    # layout width over nearly the frame height and covers the frame
    # instead - see test_centre_tall_mix_falls_back_to_full_canvas in
    # test_mg_tight_layout_width.py.
    props = _props_3([
        _el("title_lockup", anchor="top_left"),
        _el("lower_third", anchor="bottom_left"),
    ])
    box = tighten_motion_graphics_props(props)
    assert box is not None
    assert 0 < box.width < FULL_W
    assert 0 < box.height < FULL_H


def test_left_anchored_element_sits_left_of_centre():
    from library.tools.tight_box import canvas_offset
    box = tighten_motion_graphics_props(
        _props_3([_el("lower_third", anchor="bottom_left")]))
    assert box is not None
    ox, _ = canvas_offset(box)
    assert ox + box.width / 2 < FULL_W / 2


def test_missing_safe_area_or_a_canvas_wider_than_the_frame_refuses():
    props = _props_3([_el("title_lockup")])
    del props["safeArea"]
    with pytest.raises(ValueError, match="safeArea"):
        tighten_motion_graphics_props(props)
    # The 1262x480 file on the captain's project: a display run with no
    # wrap bound measured wider than the frame; it refuses like the
    # caption path's `TightBoxClipsInk` rather than shipping the file.
    from library.tools.tight_box import TightBoxClipsInk
    props = _props_3([_el(
        "title_lockup", anchor="top_centre",
        runs=[{"text": "A COMPLETELY DIFFERENT SYSTEM",
               "type_role": "display"}])])
    with pytest.raises(TightBoxClipsInk, match="1262x480"):
        tighten_motion_graphics_props(props)


def test_every_refusal_names_itself():
    """No silent None survives: each structural fallback returns its
    reason and the element that caused it, which is what the artefact
    sidecar and the build-time guard both read."""
    cases = [
        ([], "no_elements", None),
        ([_el("frame_accents", anchor="top_left")],
         "frame_accents_span_by_design", "frame_accents"),
        ([_el("title_lockup", anchor="top_centre"),
          _el("context_stamp", anchor="centre")],
         "middle_zone_mixed", "context_stamp"),
        ([_el("channel_bug", anchor="top_right", footprint=0.15,
              asset="brand/bug.png")],
         "asset_geometry_unknown", "channel_bug"),
        ([_el("tracked_label")], "nothing_drawn", None),
    ]
    for elements, reason, element in cases:
        box, refusal = tighten_motion_graphics_props_with_reason(
            _props_3(elements))
        assert box is None
        assert refusal is not None
        assert refusal.reason == reason
        assert refusal.element == element
        assert refusal.message()


def _write_props(path, width=FULL_W, height=FULL_H, elements=None):
    import json
    props = _props_3(elements if elements is not None
                   else [_el("title_lockup", anchor="middle_centre")],
                   width=width, height=height)
    path.write_text(json.dumps(props), encoding="utf-8")
    return str(path)


def _write_sidecar(props_path, outcome="full",
                   reason="frame_accents_span_by_design",
                   element="frame_accents", width=FULL_W, height=FULL_H):
    import json
    sidecar = {"version": 1, "outcome": outcome, "reason": reason,
               "element": element, "detail": reason,
               "width": width, "height": height, "placement": None}
    with open(sidecar_path_for(props_path), "w",
              encoding="utf-8") as handle:
        json.dump(sidecar, handle)
    return sidecar_path_for(props_path)


def test_guard_refuses_undeclared_reasonless_or_stale_full_canvases(tmp_path):
    props_path = _write_props(tmp_path / "mg_x_props.json")
    errors, census = check_motion_graphics_files(
        [props_path], FULL_W, FULL_H)
    assert len(errors) == 1
    assert "no tightness sidecar" in errors[0]
    assert census["full_undeclared"] == 1
    assert census["undeclared_files"] == ["mg_x_props.json"]
    # An empty reason and a stale sidecar are refused too.
    no_reason = _write_props(tmp_path / "mg_noreason_props.json")
    _write_sidecar(no_reason, reason="")
    stale = _write_props(tmp_path / "mg_stale_props.json",
                         width=512, height=480)
    _write_sidecar(stale, width=FULL_W, height=FULL_H)
    errors, census = check_motion_graphics_files(
        [no_reason, stale], FULL_W, FULL_H)
    assert len(errors) == 2
    assert census["full_undeclared"] == 2


def test_top_anchored_graphic_places_at_the_measured_value():
    """A top-anchored 480-tall canvas at the 120px safe inset is Tilt
    2592 at gain 1.0 - the value an exported still of Reel 26 finds on
    screen - on a 966-wide layout-floored canvas at x 57. Measurement:
    docs/evidence/mg_tight_box.md#the-top-anchored-2592."""
    from library.tools.tight_box import canvas_offset
    box = tighten_motion_graphics_props(
        _props_3([_el("title_lockup", anchor="top_centre")]),
        timeline_size=(FULL_W, FULL_H), draw_gain=1.0)
    assert box is not None
    assert box.height == 480
    assert box.placement == {"scaling": 1, "pan": 0.0, "tilt": 2592.0}
    # And the origin is the layout edge minus pads, so the file reader and
    # the Resolve placer agree on one placement, not two halves of
    # one: a top-anchored union at the 120px safe inset sits its
    # 966-wide floored canvas at x 57.
    assert canvas_offset(box) == (57, 72)


def _list_build_runs():
    # Reel 24's marked list, verbatim: a display-first ladder over a
    # supporting rest.
    return [
        {"text": "YouTube", "type_role": "display"},
        {"text": "Instagram", "type_role": "supporting"},
        {"text": "TikTok", "type_role": "supporting"},
    ]


def test_list_build_mixed_roles_share_one_row_height():
    """Reels 19/24/25, 2026-09-19: the items of one enumeration are
    peers and share one size, so a display-first / supporting-rest
    plan stacks every row at the lead item's height. Under the old
    per-role sum the Reel 24 list stacked 190px against 242px for the
    same texts all in the lead role; the ladder read as mis-sized
    bullets. Weights stay per-run by design (the roster's `never`
    keeps the weight contrast), so widths may still differ by weight -
    that half is pinned by the coverage test below.
    """
    from library.tools.mg_tight_box import _element_size
    cache: dict = {}
    mixed = {"element": "list_build", "runs": _list_build_runs()}
    unified = {"element": "list_build",
               "runs": [{"text": r["text"], "type_role": "display"}
                        for r in _list_build_runs()]}
    mixed_w, mixed_h = _element_size(mixed, 1.0, "", 10 ** 9, cache)
    unified_w, unified_h = _element_size(unified, 1.0, "", 10 ** 9, cache)
    assert mixed_h == unified_h


# --------------------------------------------------------------------------
# From test_mg_tight_box_measured.py
#
# The measured tight binding for motion graphics: bound from pixels.
#
# `mg_tight_box.tighten_measured_mg_with_reason` sizes the canvas from a
# MEASURED ink union - never from the text and the style, the way the
# predicted path does and gets wrong. `measure_mg_union` reads that
# union off a rendered file, and `verify_measured_crop` proves the
# tight crop IS its probe's region rather than asserting it.
#
# Synthetic and small; the ffmpeg tests need it (CI installs it,
# AGENTS.md 9). Nothing reaches Resolve or a real project.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import mg_tight_box as mgt  # noqa: E402
from library.tools.tight_box import (  # noqa: E402
    InkUnion,
    canvas_offset,
    crop_probe_to_tight,
)


FULL_W_2, FULL_H_2 = 1080, 1920
SAFE_2 = {"top": 120, "right": 120, "bottom": 804, "left": 90}


def _props_4(elements):
    return {
        "elements": elements,
        "fps": 24,
        "width": FULL_W_2,
        "height": FULL_H_2,
        "safeArea": dict(SAFE_2),
        "durationInFrames": 84,
    }


def _lower_third(anchor="bottom_left"):
    return {
        "element": "lower_third",
        "anchor": anchor,
        "row": 0,
        "runs": [{"text": "Akshita Gorti", "type_role": "display"},
                 {"text": "AI @ Lucie Content",
                  "type_role": "supporting"}],
        "color": "#FFB8D4",
        "entrance": "draw",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 84,
        "footprint": None,
        "data": {"construction": "staged_rule"},
    }


# The Reel 01 Akshita lower third's measured union, all 84 frames.
AKSHITA_UNION = InkUnion(x0=90, y0=784, x1=602, y1=906,
                         inked_frames=83)


def test_measured_union_binds_to_padded_canvas_at_union_centre():
    """512x122 of ink becomes a 608x480 canvas whose centre is the
    union centre: 608 = 512 + 2*48, and 480 is the rail floor a
    single bottom zone grows to, above the ink."""
    box, refusal = mgt.tighten_measured_mg_with_reason(
        _props_4([_lower_third()]), AKSHITA_UNION)
    assert refusal is None
    assert (box.width, box.height) == (608, 480)
    ox, oy = canvas_offset(box)
    assert (ox, oy) == (42, 474)
    # The growth went above the ink (a bottom zone grows upward), so
    # the canvas centre is NOT the union centre - but the INK still
    # lands on the union: pads plus growth place it there exactly.
    from library.tools.tight_box import ink_screen_box  # noqa: E402
    ink_in_canvas = (48.0, 48.0 + 262.0, 48.0 + 512.0,
                     48.0 + 262.0 + 122.0)
    assert ink_screen_box(box.width, box.height, box.placement,
                          ink_in_canvas, FULL_W_2, FULL_H_2) == pytest.approx(
        (90.0, 784.0, 602.0, 906.0))
    assert box.props["width"] == 608
    assert box.props["height"] == 480
    assert box.props["safeArea"] == {
        "top": 48 + 262, "right": 48, "bottom": 48, "left": 48}


def test_a_union_that_cannot_bind_is_refused_by_name():
    """Empty ink is a refusal, not a box; a union wider than the frame,
    one at the frame edge with no pad to give, and a timeline whose rail
    nobody probed all refuse rather than placing against a guess."""
    box, refusal = mgt.tighten_measured_mg_with_reason(
        _props_4([_lower_third()]),
        InkUnion(x0=10, y0=10, x1=10, y1=10, inked_frames=0))
    assert box is None
    assert refusal.reason == "nothing_drawn"
    for error, union, kwargs, reason in (
        (TightBoxClipsInk,
         InkUnion(x0=0, y0=0, x1=2000, y1=100, inked_frames=10), {},
         "canvas_larger_than_frame"),
        (TightBoxMismatch, AKSHITA_UNION, {"timeline_size": (640, 480)},
         "placement_unholdable"),
        (TightBoxMismatch,
         InkUnion(x0=0, y0=784, x1=602, y1=906, inked_frames=10), {},
         "pads_leave_frame"),
    ):
        with pytest.raises(error) as excinfo:
            mgt.tighten_measured_mg_with_reason(
                _props_4([_lower_third()]), union, **kwargs)
        assert excinfo.value.refusal.reason == reason


def _encode_mov(png_paths, mov_path, width, height):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24",
         "-i", os.path.join(os.path.dirname(png_paths[0]),
                            "shot-%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", mov_path],
        check=True)


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_measure_crop_verify_roundtrip_on_synthetic_pixels(tmp_path):
    """The whole chain on pixels this test drew: measure the union,
    cut the canvas around it, prove the crop is its probe's region -
    with the before/after/offset record the run reports."""
    from PIL import Image

    full_w, full_h = 1080, 1920
    for index in range(4):
        frame = Image.new("RGBA", (full_w, full_h), (0, 0, 0, 0))
        pixels = frame.load()
        for x in range(90, 90 + 120):
            for y in range(784, 784 + 62):
                pixels[x, y] = (255, 255, 255, 255)
        frame.save(str(tmp_path / f"shot-{index:04d}.png"))
    full_mov = str(tmp_path / "full.mov")
    _encode_mov([str(tmp_path / "shot-0000.png")], full_mov,
                full_w, full_h)

    union = mgt.measure_mg_union(full_mov)
    assert (union.x0, union.y0, union.x1, union.y1) == (90, 784, 210, 846)

    props = {"elements": [dict(_lower_third(), anchor="bottom_left")],
             "width": full_w, "height": full_h,
             "safeArea": dict(SAFE_2), "durationInFrames": 4}
    box, refusal = mgt.tighten_measured_mg_with_reason(props, union)
    assert refusal is None
    assert (box.width, box.height) == (216, 480)

    tight_mov = str(tmp_path / "tight.mov")
    crop_probe_to_tight(full_mov, tight_mov, box)
    report = mgt.verify_measured_crop(union, tight_mov, box)
    assert report["before"] == [full_w, full_h]
    assert report["after"] == [216, 480]
    assert report["offset"] == [42, 414]
    assert report["union"] == [90, 784, 210, 846]


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_blank_probe_measures_no_union(tmp_path):
    """A file that drew nothing binds nothing: None, not a guess."""
    from PIL import Image

    for index in range(2):
        Image.new("RGBA", (120, 80), (0, 0, 0, 0)).save(
            str(tmp_path / f"blank-{index:04d}.png"))
    blank_mov = str(tmp_path / "blank.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24",
         "-i", str(tmp_path / "blank-%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", blank_mov],
        check=True)
    assert mgt.measure_mg_union(blank_mov) is None


def _title_lockup(anchor="top_centre"):
    """Reel 01's surviving full-frame graphic, as the plan wrote it:
    one display run, top-anchored - the element kind PR 1190 wired
    nothing through."""
    return {
        "element": "title_lockup",
        "anchor": anchor,
        "row": 0,
        "runs": [{"text": "A COMPLETELY DIFFERENT SYSTEM",
                  "type_role": "display"}],
        "color": "#aabbcc",
        "entrance": "scale",
        "exit": "fade",
        "startFrame": 0,
        "durationFrames": 84,
        "footprint": None,
        "data": {},
    }


# The stale title lockup's drawn union, measured off its own
# full-canvas render: 660x131 of ink at offset (193, 120). The
# PREDICTED path sized this same copy to a 1262x480 canvas and refused
# it as `canvas_larger_than_frame` - the refusal the quarantined
# sidecar still carries - while the pixels bind cleanly.
TITLE_UNION = InkUnion(x0=193, y0=120, x1=853, y1=251,
                       inked_frames=84)


@pytest.mark.skipif(NEEDS_FFMPEG, reason=FFMPEG_REASON)
def test_bind_probe_tight_crops_a_title_lockup_probe(tmp_path):
    """The shared binder step 4.06 calls on a predicted refusal, end
    to end on synthetic title pixels: the entry leaves pointing at a
    verified 756x480 tight crop, not at the full-canvas probe."""
    from PIL import Image

    for index in range(3):
        frame = Image.new("RGBA", (FULL_W_2, FULL_H_2), (0, 0, 0, 0))
        pixels = frame.load()
        for x in range(193, 853):
            for y in range(120, 251):
                pixels[x, y] = (255, 255, 255, 255)
        frame.save(str(tmp_path / f"shot-{index:04d}.png"))
    full_mov = str(tmp_path / "mg_geo-podcast_probe.mov")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-framerate", "24",
         "-i", str(tmp_path / "shot-%04d.png"),
         "-c:v", "qtrle", "-pix_fmt", "argb", full_mov],
        check=True)

    planned = {"props": _props_4([_title_lockup()])}
    rendered = {"overlay_path": full_mov, "segment_id": "mg_probe",
                "geometry": "full", "tight_fallback": "canvas_larger",
                "tight_box": None}
    mgt.bind_probe_tight("Reel 01", planned, rendered, FULL_W_2, FULL_H_2)

    assert rendered["geometry"] == "tight"
    assert rendered["tight_fallback"] == ""
    assert rendered["overlay_path"].endswith("_tight.mov")
    assert rendered["tight_box"]["width"] == 756
    assert rendered["tight_box"]["height"] == 480
    assert rendered["tight_report"]["before"] == [FULL_W_2, FULL_H_2]
    assert rendered["tight_report"]["after"] == [756, 480]
    assert rendered["tight_report"]["offset"] == [145, 72]
    assert os.path.isfile(
        full_mov[:-len(".mov")] + "_tight_props.json")


# --------------------------------------------------------------------------
# From test_mg_tight_layout_width.py
#
# The tight motion-graphics render is the SAME DRAWING as full frame.
#
# Python floors the canvas at the full-frame usable width plus pads and
# stamps `layoutWidth`; the composition caps centre stacks at it - so the
# effective tight container EQUALS the full-frame one (captain 2026-09-21).
# Measurements and the ruling: docs/evidence/mg_tight_box.md#layout-width.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.mg_tight_box import (  # noqa: E402
    MG_PAD,
)

USABLE = FULL_W - SAFE["left"] - SAFE["right"]
assert USABLE == 870

COMPOSITION = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "compositions",
    "MotionGraphics", "index.tsx")


def _el_2(element, anchor="bottom_centre", row=0, runs=None,
        duration=60, start=0, **over):
    el = {
        "element": element,
        "anchor": anchor,
        "row": row,
        "runs": runs if runs is not None else [
            {"text": "and so my very", "type_role": "supporting"},
        ],
        "color": "#FFDD55",
        "entrance": "fade",
        "exit": "fade",
        "startFrame": start,
        "durationFrames": duration,
        "timelineProgressStart": 0.0,
        "timelineProgressEnd": 0.5,
        "footprint": None,
        "emphasis": None,
    }
    el.update(over)
    return el


def _props_5(elements, width=FULL_W, height=FULL_H, safe=None):
    total = max((e["startFrame"] + e["durationFrames"] for e in elements),
                default=60)
    return {
        "elements": elements,
        "fps": 30,
        "width": width,
        "height": height,
        "safeArea": dict(safe or SAFE),
        "durationInFrames": total,
    }


def _effective_container(box) -> float:
    """The width copy actually wraps to on the tight canvas.

    The canvas minus the insets, capped at `layoutWidth` exactly as
    the composition caps the centre container (`maxWidth` with auto
    margins). Before the fix there is no `layoutWidth` key, so this
    raises KeyError - which is the point: a test that only checked
    clipping would have passed before the fix and proves nothing.
    """
    container = box.width - 2 * MG_PAD
    return min(container, box.props["layoutWidth"])


def _wrap_copy(kind: str):
    """Copy modest enough to hold single-line inside the full-frame
    usable width (the predictor models single lines and refuses past
    the frame), but laid out through a centre container - which is
    what the floor and the cap preserve. Per kind, because display
    type at stat_callout's drawn scale is far wider per character."""
    if kind == "stat_callout":
        return [
            {"text": "RECORD YEAR", "type_role": "display"},
            {"text": "revenue doubled since spring",
             "type_role": "supporting"},
        ]
    if kind == "quote_card":
        return [
            {"text": "We doubled revenue in a single year",
             "type_role": "supporting"},
        ]
    return [
        {"text": "A SINGLE YEAR", "type_role": "display"},
        {"text": "everything changed", "type_role": "supporting"},
    ]


# ── the two named kinds, plus the structurally identified third ──────

# ── the mechanism, for every centre copy kind ─────────────────────────

def test_every_centre_copy_kind_lays_out_at_the_full_layout_width():
    """Whichever the scout's third was, it is covered: every
    centre-anchored copy kind stamps the full usable width and lays
    out at exactly it."""
    kinds = ["title_lockup", "quote_card", "lower_third",
             "context_stamp", "stat_callout", "counter_roll",
             "digit_counter", "list_build", "comparison_bars",
             "step_counter", "subject_emblem"]
    for kind in kinds:
        elements = [_el_2(kind, anchor="middle_centre")]
        if kind == "comparison_bars":
            elements[0]["data"] = {"values": [30, 70]}
        box = tighten_motion_graphics_props(_props_5(elements))
        assert box is not None, kind
        assert box.props["layoutWidth"] == USABLE, kind
        assert _effective_container(box) == USABLE, kind

    # stat_callout drew 366x225 where the full frame drew 540x165: with
    # the measured copy its container now equals the full-frame 870px.
    box = tighten_motion_graphics_props(_props_5([
        _el_2("stat_callout", anchor="middle_centre",
            runs=_wrap_copy("stat_callout"))]))
    assert box is not None
    assert box.width >= USABLE + 2 * MG_PAD
    assert _effective_container(box) == USABLE

    # Short copy floors at 966 (870 + 2*48): the size win is capped near
    # the caption box - the accepted cost; losing the tight path is not.
    box = tighten_motion_graphics_props(_props_5(
        [_el_2("title_lockup", anchor="middle_centre",
             runs=[{"text": "Hi", "type_role": "supporting"}])]))
    assert box is not None
    assert box.width == USABLE + 2 * MG_PAD == 966
    assert _effective_container(box) == USABLE


def test_overwide_union_is_capped_not_carried(monkeypatch):
    """The other direction: a predicted union WIDER than the usable
    width would lay its container wider than full frame and unwrap
    copy the full frame wrapped. The composition cap binds instead,
    so the effective container is still exactly the usable width."""
    import library.tools.mg_tight_box as mgt
    monkeypatch.setattr(mgt, "_run_width",
                        lambda *args, **kwargs: 950.0)
    box = tighten_motion_graphics_props(_props_5(
        [_el_2("title_lockup", anchor="middle_centre")]))

    assert box is not None
    assert box.width - 2 * MG_PAD > USABLE
    assert box.props["layoutWidth"] == USABLE
    assert _effective_container(box) == USABLE


# ── what keeps the small canvas ───────────────────────────────────────

def test_side_anchored_copy_keeps_the_small_canvas():
    """One inset sizes to content: the union-sized canvas already
    holds the layout, so there is no cap and no floor - the win
    stands."""
    for anchor in ("bottom_left", "bottom_right", "top_left"):
        box = tighten_motion_graphics_props(_props_5(
            [_el_2("title_lockup", anchor=anchor)]))
        assert box is not None, anchor
        assert "layoutWidth" not in box.props, anchor
        assert box.width < USABLE + 2 * MG_PAD, anchor


def test_mixed_segment_pins_the_side_ink():
    """Bottom-centre copy beside a bottom-left panel: the floor spans
    the layout width, but the canvas origin stays pinned to the
    union's left edge - the side stack's screen position is exactly
    what the union says, as before."""
    box = tighten_motion_graphics_props(_props_5([
        _el_2("title_lockup", anchor="bottom_centre",
            runs=[{"text": "Hi there", "type_role": "supporting"}]),
        _el_2("lower_third", anchor="bottom_left", row=1),
    ]))
    assert box is not None
    assert box.props["layoutWidth"] == USABLE
    assert _effective_container(box) == USABLE
    ox, _oy = canvas_offset(box)
    assert ox == SAFE["left"] - MG_PAD == 42


def test_centre_tall_mix_falls_back_to_full_canvas():
    """A top-centre title over a bottom-anchored panel unions to
    nearly the frame height; floored at the layout width that union
    covers the frame, so the segment renders full     canvas. That IS
    the same drawing - the fallback the coverage backstop exists
    for - at full-canvas bytes. The planner no longer produces such
    a combined segment (`separable_groups` splits the tall mix into
    two tight rows first); this pins the renderer's backstop for one
    handed it directly. Failing the build on planned content
    would punish the plan for the engine's reach, so this passes
    counted, not failed (see `check_motion_graphics_files`)."""
    from library.tools.mg_tight_box import (
        tighten_motion_graphics_props_with_reason,
    )
    box, refusal = tighten_motion_graphics_props_with_reason(_props_5([
        _el_2("title_lockup", anchor="top_centre"),
        _el_2("lower_third", anchor="bottom_left"),
    ]))
    assert box is None
    assert refusal is not None
    assert refusal.reason == "covers_frame"
