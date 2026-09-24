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
    check_motion_graphics_dir,
    check_motion_graphics_files,
    sidecar_path_for,
    tighten_motion_graphics_props,
    tighten_motion_graphics_props_with_reason,
    tightness_record,
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


def test_channel_bug_forces_full_canvas():
    props = _props([_el("channel_bug", anchor="top_right",
                        footprint=0.15, asset="brand/bug.png")])
    assert tighten_motion_graphics_props(props) is None


def test_middle_stack_mixed_with_top_falls_back():
    props = _props([
        _el("title_lockup", anchor="top_centre"),
        _el("context_stamp", anchor="centre"),
    ])
    assert tighten_motion_graphics_props(props) is None


def test_top_and_bottom_mix_stays_tight():
    # Side-anchored on both edges: the union-sized canvas already
    # holds the layout, so no layout-width floor applies and the mix
    # stays tight. A CENTRE-anchored title in the same mix spans the
    # layout width over nearly the frame height and covers the frame
    # instead - see test_centre_tall_mix_falls_back_to_full_canvas in
    # test_mg_tight_layout_width.py.
    props = _props([
        _el("title_lockup", anchor="top_left"),
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


def test_missing_safe_area_refuses_like_the_component():
    props = _props([_el("title_lockup")])
    del props["safeArea"]
    with pytest.raises(ValueError, match="safeArea"):
        tighten_motion_graphics_props(props)


def test_canvas_wider_than_the_frame_refuses():
    """The 1262x480 file on the captain's project: a display run with
    no wrap bound measures wider than the usable frame, and the pads
    push the canvas past the delivery width. The caption path refuses
    that with `TightBoxClipsInk` (`tight_box.py`); this path produced
    the file instead. Remove the bound and this input ships a canvas
    wider than the frame again - the union itself hangs off both
    frame edges (centred 1166px ink on a 1080 frame)."""
    from library.tools.tight_box import TightBoxClipsInk
    props = _props([_el(
        "title_lockup", anchor="top_centre",
        runs=[{"text": "A COMPLETELY DIFFERENT SYSTEM",
               "type_role": "display"}])])
    with pytest.raises(TightBoxClipsInk, match="1262x480"):
        tighten_motion_graphics_props(props)


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


def test_staged_lower_third_tightens():
    """The sixteen full-frame files on the captain's project: every
    constructed lower third refused as a class, so every one rendered
    the delivery frame. Modelled off the construction's own drawing,
    it is a small bottom-anchored box Resolve holds inside its rail."""
    from library.tools.tight_box import placement_holds
    box, refusal = tighten_motion_graphics_props_with_reason(
        _props([_staged()]))
    assert refusal is None
    assert box is not None
    assert 0 < box.width < FULL_W
    assert box.height == 480
    assert placement_holds(box.placement, FULL_W, FULL_H) == ""


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
            _props(elements))
        assert box is None
        assert refusal is not None
        assert refusal.reason == reason
        assert refusal.element == element
        assert refusal.message()


def _write_props(path, width=FULL_W, height=FULL_H, elements=None):
    import json
    props = _props(elements if elements is not None
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


def test_guard_refuses_an_undeclared_full_canvas(tmp_path):
    props_path = _write_props(tmp_path / "mg_x_props.json")
    errors, census = check_motion_graphics_files(
        [props_path], FULL_W, FULL_H)
    assert len(errors) == 1
    assert "no tightness sidecar" in errors[0]
    assert census["full_undeclared"] == 1
    assert census["undeclared_files"] == ["mg_x_props.json"]


def test_guard_refuses_an_empty_reason_and_a_stale_sidecar(tmp_path):
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
    2592, and that is the value a still finds on screen.

    Measured 2026-09-11 on the captain's own Reel 26: a 920x480
    graphic stored at Tilt 2592 is located at frame rows 72..552 in an
    exported still (MSE 51 against ~40 700 five pixels either side).
    The halved 1296 this test used to demand draws it at row 396, and
    the 5184 that five reels carry draws it at -576, entirely off the
    top - which is what the captain sees on seventeen graphics today.
    The pipeline must compute 2592 itself: no hand correction, no
    halving at the call site.

    Pinned as history at explicit gain 1.0: the renderer drew that
    gain on 2026-09-11. Under today's measured gain the same graphic
    stores 1296 for the identical rows (see
    `tests/test_draw_gain_measured.py`).

    Since the layout-width floor (captain 2026-09-21) this top-centre
    graphic ships on a 966-wide canvas instead of the union-sized
    one: the canvas spans the full-frame usable width plus the pads
    so the copy wraps as at full frame. The union is centred, so the
    canvas stays centred - pan 0, tilt 2592, the values above - and
    only the origin moves: the canvas edge sits at 57, one pad past
    the layout edge (540 - 870/2 - 48), instead of one pad past the
    union edge (348).
    """
    from library.tools.tight_box import canvas_offset
    box = tighten_motion_graphics_props(
        _props([_el("title_lockup", anchor="top_centre")]),
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
