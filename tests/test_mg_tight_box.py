"""The drawn union of a motion-graphics segment, and where it lands.

A motion-graphics segment today renders at the full delivery frame
(1080x1920): two million pixels per frame to draw a title occupying a
few percent of them. The position is baked in at render time, so
repositioning means re-rendering. Captions already render as tight
boxes (`library/tools/tight_box.py`, PR 725: 2.3-2.8x faster, a third
the bytes) - this is the same mechanism for the other overlay kind.

The question here is purely geometric: the union of what a
`MotionGraphics` composition actually draws. Chrome spans the frame by
design - four corner accents sit at four corners, so their union IS the
frame - and the honest answer for such a composition is that tight-box
buys nothing: `tighten_motion_graphics_props` returns None and the
caller keeps the full-canvas path rather than forcing a win that is
not there.

What is bounded, in full-frame coordinates, per element
(`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`):

- `progress_bar`: an absolute strip, left..right safe insets, 12px tall
  plus its glow, at the top or bottom inset. Exact.
- `frame_accents`: four 80px squares at the four safe corners. Exact -
  and their union is the whole safe box, so alone or with a bar they
  cover the frame and there is no box.
- Anchored copy (`title_lockup`, `quote_card`, `lower_third`,
  `context_stamp`, `stat_callout`, `subject_emblem`, `counter_roll`,
  `digit_counter`, `list_build`, `comparison_bars`, `step_counter`,
  `beat_accent`, `pointer_annotation`): stacked by anchor in row order
  inside one flex container per anchor. Text is measured with the same
  Montserrat face the render loads (`CaptionFitter`), sizes and weights
  read from the composition's own tables, and every estimate is an
  OVER-estimate - a box that clips ink is a defect, a box with slack is
  merely a smaller win.

What forces the full canvas rather than a wrong box:

- Asset elements (`channel_bug`, `website_panel`): their height comes
  from a project-supplied file nothing measures. Bounding an unknown
  aspect would be forcing the win, so they refuse it.
- A middle-anchored stack beside another vertical zone: `top: 50%` is
  relative to the CANVAS, so on a small canvas the stack centres on the
  wrong frame. Top+bottom mixes are exact (both edges are canvas
  edges); anything with middle mixed in is not, and falls back.
- Unknown element keys draw nothing (the composition returns null for
  them), so they are ignored - and a segment of nothing but unknowns
  has no union to bound.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.mg_tight_box import (  # noqa: E402
    MG_PAD,
    tighten_motion_graphics_props,
)

FULL_W = 1080
FULL_H = 1920
SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}


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


def _props(elements, width=FULL_W, height=FULL_H, safe=None):
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


def test_no_elements_means_no_box():
    assert tighten_motion_graphics_props(_props([])) is None


def test_unknown_elements_draw_nothing_so_no_box():
    props = _props([_el("tracked_label")])
    assert tighten_motion_graphics_props(props) is None


def test_unknown_elements_are_ignored_beside_real_ones():
    # Mid-frame, so the box the test compares is one Resolve can
    # hold: a bottom-anchored box this small needs Tilt past the
    # clamp and is refused (see below), which would leave nothing
    # to compare.
    title = _el("title_lockup", anchor="middle_centre")
    with_unknown = tighten_motion_graphics_props(
        _props([title, _el("tracked_label", anchor="middle_centre")]))
    without = tighten_motion_graphics_props(_props([title]))
    assert with_unknown is not None and without is not None
    assert (with_unknown.width, with_unknown.height) == \
        (without.width, without.height)


def test_frame_accents_alone_cover_the_frame():
    props = _props([_el("frame_accents", anchor="top_left")])
    assert tighten_motion_graphics_props(props) is None


def test_accents_plus_progress_bar_cover_the_frame():
    props = _props([
        _el("frame_accents", anchor="top_left"),
        _el("progress_bar", anchor="bottom_centre"),
    ])
    assert tighten_motion_graphics_props(props) is None


def test_bottom_progress_bar_rides_the_minimum_canvas():
    """Was refused: a bottom progress bar is a thin strip far from the
    frame centre, needing Tilt past what Resolve holds on a 140-tall
    canvas. The floor grows the single-zone strip to 480 (away from
    its edge, so the ink does not move), and it places at about
    -1900 - inside the 3840 rail with headroom."""
    box = tighten_motion_graphics_props(
        _props([_el("progress_bar", anchor="bottom_centre")]))
    assert box is not None
    assert box.height == 480
    assert abs(box.placement["tilt"]) <= 3400


def test_top_progress_bar_rides_the_minimum_canvas():
    """The top case of the same fix: a thin strip at the top inset
    needed Tilt +11433 on its measured canvas; grown below its edge
    to 480, it places inside the rail."""
    box = tighten_motion_graphics_props(
        _props([_el("progress_bar", anchor="top_centre")]))
    assert box is not None
    assert box.height == 480
    assert abs(box.placement["tilt"]) <= 3400


def test_single_title_is_much_smaller_than_full_frame():
    box = tighten_motion_graphics_props(
        _props([_el("title_lockup", anchor="middle_centre")]))
    assert box is not None
    assert 0 < box.width < FULL_W
    assert 0 < box.height < FULL_H
    assert box.width * box.height < 0.25 * FULL_W * FULL_H


def test_canvas_dimensions_are_even():
    box = tighten_motion_graphics_props(
        _props([_el("title_lockup", anchor="middle_centre")]))
    assert box.width % 2 == 0
    assert box.height % 2 == 0


def test_tight_safe_area_is_padding_plus_rail_growth():
    box = tighten_motion_graphics_props(
        _props([_el("title_lockup", anchor="middle_centre")]))
    # middle grows symmetrically: both insets share the growth, and
    # the canvas ships at the floor.
    assert box.height == 480
    assert box.props["safeArea"]["left"] == MG_PAD
    assert box.props["safeArea"]["right"] == MG_PAD
    assert (box.props["safeArea"]["top"]
            == box.props["safeArea"]["bottom"] >= MG_PAD)


def test_elements_timing_and_frame_pass_through_untouched():
    props = _props([
        _el("title_lockup", row=0, start=0, duration=40),
        _el("quote_card", anchor="top_centre", row=0, start=30, duration=40),
    ])
    box = tighten_motion_graphics_props(props)
    assert box is not None
    assert box.props["elements"] == props["elements"]
    assert box.props["durationInFrames"] == props["durationInFrames"]
    assert box.props["fps"] == props["fps"]
    assert box.props["width"] == box.width
    assert box.props["height"] == box.height


def test_channel_bug_forces_full_canvas():
    props = _props([_el("channel_bug", anchor="top_right",
                        footprint=0.15, asset="brand/bug.png")])
    assert tighten_motion_graphics_props(props) is None


def test_website_panel_forces_full_canvas():
    props = _props([_el("website_panel", footprint=0.8,
                        asset="brand/page.png")])
    assert tighten_motion_graphics_props(props) is None


def test_middle_stack_mixed_with_top_falls_back():
    props = _props([
        _el("title_lockup", anchor="top_centre"),
        _el("context_stamp", anchor="centre"),
    ])
    assert tighten_motion_graphics_props(props) is None


def test_all_middle_stays_tight():
    props = _props([
        _el("context_stamp", anchor="centre"),
        _el("stat_callout", anchor="middle_right"),
    ])
    box = tighten_motion_graphics_props(props)
    assert box is not None
    assert box.width * box.height < 0.5 * FULL_W * FULL_H


def test_top_and_bottom_mix_stays_tight():
    props = _props([
        _el("title_lockup", anchor="top_centre"),
        _el("lower_third", anchor="bottom_left"),
    ])
    box = tighten_motion_graphics_props(props)
    assert box is not None
    assert 0 < box.width < FULL_W
    assert 0 < box.height < FULL_H


def test_bottom_anchored_title_rides_the_minimum_canvas():
    """The captain's case at its simplest: an ordinary
    bottom-anchored title needed Tilt -8758 on its measured canvas,
    past what Resolve holds. Grown above its edge to 480, it places
    inside the rail - refused nowhere, clamped nowhere."""
    box = tighten_motion_graphics_props(_props([_el("title_lockup")]))
    assert box is not None
    assert box.height == 480
    assert abs(box.placement["tilt"]) <= 3400


def test_small_off_centre_box_rides_the_minimum_canvas():
    """The captain's motion graphics on huge X: a small box far from
    the frame centre overflowed Tilt on its measured canvas. Grown
    below its top edge, both axes hold - the gate below still watches
    both, and still refuses what even the grown canvas cannot hold."""
    from library.tools.tight_box import placement_holds
    box = tighten_motion_graphics_props(
        _props([_el("pointer_annotation", anchor="top_left",
                    footprint=0.5)]))
    assert box is not None
    assert box.height == 480
    assert placement_holds(box.placement, FULL_W, FULL_H) == ""
    assert abs(box.placement["tilt"]) <= 3400


def test_left_anchored_element_sits_left_of_centre():
    from library.tools.tight_box import canvas_offset
    box = tighten_motion_graphics_props(
        _props([_el("lower_third", anchor="bottom_left")]))
    assert box is not None
    ox, _ = canvas_offset(box)
    assert ox + box.width / 2 < FULL_W / 2


def test_right_anchored_element_sits_right_of_centre():
    from library.tools.tight_box import canvas_offset
    box = tighten_motion_graphics_props(
        _props([_el("lower_third", anchor="bottom_right")]))
    assert box is not None
    ox, _ = canvas_offset(box)
    assert ox + box.width / 2 > FULL_W / 2


def test_comparison_bars_span_the_width_but_stay_short():
    box = tighten_motion_graphics_props(_props([
        _el("comparison_bars",
            runs=[{"text": "alpha", "type_role": "micro"},
                  {"text": "beta", "type_role": "micro"}],
            data={"values": [30, 70]}),
    ]))
    assert box is not None
    assert box.height < FULL_H / 2
    assert box.width * box.height < 0.5 * FULL_W * FULL_H


def test_footprint_scales_the_estimate():
    # Mid-frame, so both boxes are ones Resolve can hold.
    small = tighten_motion_graphics_props(_props(
        [_el("title_lockup", anchor="middle_centre", footprint=0.5)]))
    big = tighten_motion_graphics_props(_props(
        [_el("title_lockup", anchor="middle_centre", footprint=2.0)]))
    assert small is not None and big is not None
    assert big.width > small.width
    assert big.union_h > small.union_h


def test_missing_safe_area_refuses_like_the_component():
    props = _props([_el("title_lockup")])
    del props["safeArea"]
    with pytest.raises(ValueError, match="safeArea"):
        tighten_motion_graphics_props(props)


def test_pads_cover_slide_and_shadow():
    """Slide moves 40px, text shadows blur 12px, glitch jitters ~6px:
    the pad must clear the largest plus margin, or the box clips ink
    mid-entrance."""
    assert MG_PAD >= 40 + 8


def test_top_anchored_graphic_places_at_the_measured_value():
    """A top-anchored 480-tall canvas at the 120px safe inset is Tilt
    2592, and that is the value a still finds on screen.

    Measured 2026-09-11 on the captain's own Reel 26: a 920x480
    graphic stored at Tilt 2592 is located at frame rows 72..552 in an
    exported still (MSE 51 against ~40 700 five pixels either side).
    The halved 1296 this test used to demand draws it at row 396, and
    the 5184 that five reels carry draws it at -576, entirely off the
    top - which is what the captain sees on seventeen graphics today.
    The pipeline must compute 2592 itself: no hand correction, no
    halving at the call site.
    """
    from library.tools.tight_box import canvas_offset
    box = tighten_motion_graphics_props(
        _props([_el("title_lockup", anchor="top_centre")]),
        timeline_size=(FULL_W, FULL_H))
    assert box is not None
    assert box.height == 480
    assert box.placement == {"scaling": 1, "pan": 0.0, "tilt": 2592.0}
    # And the origin is the union minus pads, so the file reader and
    # the Resolve placer agree on one placement, not two halves of
    # one: a top-anchored union at the 120px safe inset sits its
    # 480-tall canvas at y 72.
    assert canvas_offset(box) == (348, 72)
