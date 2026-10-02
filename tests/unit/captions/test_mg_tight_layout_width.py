"""The tight motion-graphics render is the SAME DRAWING as full frame.

Python floors the canvas at the full-frame usable width plus pads and
stamps `layoutWidth`; the composition caps centre stacks at it - so the
effective tight container EQUALS the full-frame one (captain 2026-09-21).
Measurements and the ruling: docs/evidence/mg_tight_box.md#layout-width.
"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.mg_tight_box import (  # noqa: E402
    MG_PAD,
    tighten_motion_graphics_props,
)
from library.tools.tight_box import canvas_offset  # noqa: E402

FULL_W = 1080
FULL_H = 1920
SAFE = {"top": 120, "right": 120, "bottom": 320, "left": 90}
USABLE = FULL_W - SAFE["left"] - SAFE["right"]
assert USABLE == 870

COMPOSITION = os.path.join(
    PROJECT_ROOT, "remotion-subtitles", "src", "compositions",
    "MotionGraphics", "index.tsx")


def _el(element, anchor="bottom_centre", row=0, runs=None,
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
        elements = [_el(kind, anchor="middle_centre")]
        if kind == "comparison_bars":
            elements[0]["data"] = {"values": [30, 70]}
        box = tighten_motion_graphics_props(_props(elements))
        assert box is not None, kind
        assert box.props["layoutWidth"] == USABLE, kind
        assert _effective_container(box) == USABLE, kind

    # stat_callout drew 366x225 where the full frame drew 540x165: with
    # the measured copy its container now equals the full-frame 870px.
    box = tighten_motion_graphics_props(_props([
        _el("stat_callout", anchor="middle_centre",
            runs=_wrap_copy("stat_callout"))]))
    assert box is not None
    assert box.width >= USABLE + 2 * MG_PAD
    assert _effective_container(box) == USABLE

    # Short copy floors at 966 (870 + 2*48): the size win is capped near
    # the caption box - the accepted cost; losing the tight path is not.
    box = tighten_motion_graphics_props(_props(
        [_el("title_lockup", anchor="middle_centre",
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
    box = tighten_motion_graphics_props(_props(
        [_el("title_lockup", anchor="middle_centre")]))

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
        box = tighten_motion_graphics_props(_props(
            [_el("title_lockup", anchor=anchor)]))
        assert box is not None, anchor
        assert "layoutWidth" not in box.props, anchor
        assert box.width < USABLE + 2 * MG_PAD, anchor


def test_mixed_segment_pins_the_side_ink():
    """Bottom-centre copy beside a bottom-left panel: the floor spans
    the layout width, but the canvas origin stays pinned to the
    union's left edge - the side stack's screen position is exactly
    what the union says, as before."""
    box = tighten_motion_graphics_props(_props([
        _el("title_lockup", anchor="bottom_centre",
            runs=[{"text": "Hi there", "type_role": "supporting"}]),
        _el("lower_third", anchor="bottom_left", row=1),
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
    box, refusal = tighten_motion_graphics_props_with_reason(_props([
        _el("title_lockup", anchor="top_centre"),
        _el("lower_third", anchor="bottom_left"),
    ]))
    assert box is None
    assert refusal is not None
    assert refusal.reason == "covers_frame"
