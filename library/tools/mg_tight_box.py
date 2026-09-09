"""The drawn union of a motion-graphics segment, and where it lands.

A motion-graphics segment today renders at the full delivery frame
(1080x1920): two million pixels per frame to draw a title occupying a
few percent of them. Captions already render as tight boxes
(`library/tools/tight_box.py`, PR 725) - this is the same mechanism
for the other overlay kind: compute the UNION of what the composition
actually draws, render only that, and place the small clip on the
Resolve timeline at an offset (`Scaling=1` for native pixels, then
Pan/Tilt, via `placement_for_box`).

The question is purely geometric, and for some compositions the honest
answer is that tight-box buys nothing. Chrome spans the frame BY
DESIGN: four corner accents sit at four corners, so their union IS the
frame, and a corner accent plus a progress bar unions to most of it.
Those compositions return None and the caller keeps the full-canvas
path rather than forcing a win that is not there.

What is read, and from where
----------------------------
Every number below is READ from the composition
(`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`), not
re-chosen. Type sizes and weights are its `TYPE_SIZE` / `TYPE_WEIGHT`
tables; the row gap is its `STACK_GAP_PX`; the bar, accent, plate and
glow sizes are the literals in each element's arm. Text is measured
with the same Montserrat face the render loads (`CaptionFitter`), and
`display` runs are measured uppercased with their 3px letter spacing
because that is what the render draws.

Where the estimate may be wrong, and why that is safe
-----------------------------------------------------
Copy elements do not wrap - no arm constrains their width, so a long
run overflows rather than breaking - which means heights are sums of
line boxes and widths are single-line measurements. Chromium shaping
and PIL shaping differ by a few pixels either way. Both errors land in
`MG_PAD`, which at 48px also clears the largest entrance/exit motion
(`slide` travels 40px), the 12px text-shadow blur, and the glitch
jitter with its drop-shadow chain. A box with slack is a smaller win;
a box that clips ink is a defect. The estimate errs toward slack.

What falls back to the full canvas, and why each is a refusal and not
a guess
-----------------------------------------------
- `frame_accents`, alone or with anything: four brackets at four
  corners span the safe box by design. Bounding them tightly would
  still render most of the frame while taking on placement risk.
- Asset elements (`channel_bug`, `website_panel`): their height comes
  from a project-supplied file nothing measures. Bounding an unknown
  aspect would be forcing the win.
- A middle-anchored stack beside another vertical zone: `top: 50%`
  centres on the CANVAS, so on a small canvas the stack centres on the
  wrong frame. Top+bottom mixes are exact (both edges are canvas
  edges, exactly as the caption box is); anything with middle mixed in
  is not. All-middle segments are self-consistent - centring on the
  small canvas IS the placement - and stay tight.
- Unknown element keys draw nothing (the composition returns null),
  so they are ignored; a segment of nothing but unknowns has no union.
- A canvas covering `FULL_FRAME_COVERAGE` of the frame: the backstop
  for compositions the structural rules do not name. Marginal pixel
  savings are not worth the placement risk.

How the box lands in Resolve
----------------------------
The tight props keep every element, its local timing and its anchor,
and replace only the canvas (`width`/`height`) and the insets
(`safeArea` becomes the pads). Anchored stacks hug canvas edges, so
they sit pad-anchored on the small canvas exactly as they sat
inset-anchored on the full one; chrome positions itself from the
insets absolutely, so it is consistent wherever the union put it; a
middle-only stack centres on the small canvas, which is the placement.
`placement_for_box` inverts the measured Resolve relation (see
`tight_box.py`), so the canvas centre lands on the union centre.
"""

from __future__ import annotations

import math
from typing import Optional

from library.tools.tight_box import (
    TightBox,
    TightBoxMismatch,
    placement_for_box,
    placement_holds,
)

# Beyond the estimated ink on every side: the slide entrance travels
# 40px, text shadows blur 12px, the glitch jitter moves ~6px carrying a
# drop-shadow chain, and PIL-vs-Chromium shaping differs by a few px.
# Slack is a smaller win; clipping is a defect.
MG_PAD = 48

# A canvas this large relative to the frame is not a win worth the
# placement risk. The structural rules above catch the compositions
# that span by design; this catches the ones nobody named.
FULL_FRAME_COVERAGE = 0.7

# The composition's own tables, read not chosen.
TYPE_SIZE = {"display": 56, "supporting": 36, "micro": 24}
TYPE_WEIGHT = {"display": 900, "supporting": 700, "micro": 600}
STACK_GAP_PX = 24
LINE_HEIGHT = 1.1
LIST_LINE_HEIGHT = 1.3
DISPLAY_LETTER_SPACING = 3

# Element literals, each from its own arm of the composition.
BAR_HEIGHT = 12
BAR_GLOW = 16
ACCENT_SIZE = 80
EMBLEM_PLATE = 200
BEAT_SIZE = 120
POINTER_SIZE = 80
POINTER_GLOW = 16

SELF_POSITIONING = ("progress_bar", "frame_accents")
ASSET_ELEMENTS = ("channel_bug", "website_panel")

# Column gaps and paddings per text arm: (gap_between_runs,
# extra_width, extra_height) at scale 1.
COLUMN_CHROME = {
    "title_lockup": (12, 0, 0),
    "quote_card": (16, 36, 0),
    "lower_third": (8, 72, 48),
    "context_stamp": (0, 32, 16),
    "stat_callout": (12, 0, 0),
    "counter_roll": (8, 0, 0),
    "digit_counter": (8, 0, 0),
    "list_build": (12, 20, 0),
    "comparison_bars": (16, 0, 0),
    "step_counter": (8, 0, 0),
}


def _ceil_even(value: float) -> int:
    """Round up to an even int, so no codec clips half a pixel row."""
    return int(math.ceil(value / 2.0)) * 2


def _fitter_for_size(size: float, weight: int,
                     project_folder: str = ""):
    """The same Montserrat face the render loads, at one size/weight."""
    from library.steps.step_4_01_plan_subtitles.step import CaptionFitter
    from library.tools.render_fonts import measurable_font_path
    try:
        font_path = measurable_font_path(
            "Montserrat", None, project_folder or None)
    except Exception:  # noqa: BLE001 - no face, estimate instead
        font_path = ""
    return CaptionFitter(
        font_path=font_path or "",
        font_size=max(1, int(round(size))),
        font_weight=weight,
        usable_width=10 ** 9,
    )


def _run_width(text: str, type_role: str, scale: float,
               project_folder: str, _cache: dict) -> float:
    """One run's drawn width, over-estimated. Display runs render
    uppercased with 3px letter spacing, so they are measured that way."""
    size = TYPE_SIZE.get(type_role, TYPE_SIZE["supporting"]) * scale
    weight = TYPE_WEIGHT.get(type_role, TYPE_WEIGHT["supporting"])
    key = (round(size, 3), weight)
    fitter = _cache.get(key)
    if fitter is None:
        fitter = _fitter_for_size(size, weight, project_folder)
        _cache[key] = fitter
    drawn = text.upper() if type_role == "display" else text
    width = fitter.word_width(drawn)
    if type_role == "display":
        width += DISPLAY_LETTER_SPACING * max(0, len(drawn) - 1)
    return width


def _run_line_height(type_role: str, scale: float) -> float:
    size = TYPE_SIZE.get(type_role, TYPE_SIZE["supporting"]) * scale
    return size * LINE_HEIGHT


def _column_size(element: dict, scale: float, project_folder: str,
                 _cache: dict) -> tuple:
    """(width, height) of a stacked-runs column, over-estimated."""
    runs = element.get("runs") or []
    widths = [_run_width(r.get("text", ""), r.get("type_role", "supporting"),
                         scale, project_folder, _cache) for r in runs]
    width = max(widths, default=0.0)
    height = sum(
        _run_line_height(r.get("type_role", "supporting"), scale)
        for r in runs)
    gap, extra_w, extra_h = COLUMN_CHROME.get(
        element.get("element", ""), (12, 0, 0))
    if len(runs) > 1:
        height += gap * (len(runs) - 1)
    return width + extra_w * scale, height + extra_h * scale


def _format_counter(element: dict) -> str:
    """The figure the counter arms draw, as drawn (end value held)."""
    data = element.get("data") or {}
    end_val = data.get("end_value", 100)
    prefix = data.get("prefix", "") if isinstance(
        data.get("prefix"), str) else ""
    suffix = data.get("suffix", "") if isinstance(
        data.get("suffix"), str) else ""
    decimals = data.get("decimals", 0) if isinstance(
        data.get("decimals"), (int, float)) else 0
    try:
        number = (f"{float(end_val):.{int(decimals)}f}"
                  if decimals > 0 else f"{round(float(end_val)):,}")
    except (TypeError, ValueError):
        number = str(end_val)
    return f"{prefix}{number}{suffix}"


def _element_size(element: dict, scale: float, project_folder: str,
                  usable_width: float, _cache: dict) -> Optional[tuple]:
    """(width, height) one element's ink fits in, or None if unknown.

    None is a refusal, not a zero: an element of unknown size forces
    the full canvas rather than a box drawn around a guess.
    """
    kind = element.get("element", "")

    if kind in ASSET_ELEMENTS:
        return None
    if kind == "progress_bar":
        return usable_width + 2 * BAR_GLOW, BAR_HEIGHT * scale + 2 * BAR_GLOW
    if kind == "frame_accents":
        side = ACCENT_SIZE * scale
        return side, side
    if kind == "beat_accent":
        side = BEAT_SIZE * scale
        return side, side
    if kind == "pointer_annotation":
        side = POINTER_SIZE * scale + 2 * POINTER_GLOW
        return side, side
    if kind == "subject_emblem":
        runs = element.get("runs") or []
        mark = next((r for r in runs if r.get("type_role") == "display"),
                    None)
        label = [r for r in runs if r.get("type_role") != "display"]
        plate = EMBLEM_PLATE * scale
        mark_w = (_run_width(mark.get("text", ""), "display", scale,
                             project_folder, _cache)
                  if mark else 0.0)
        label_w, label_h = _column_size(
            {"element": "stat_callout", "runs": label}, scale * 0.8,
            project_folder, _cache) if label else (0.0, 0.0)
        return (max(plate, mark_w, label_w),
                plate + 16 * scale + label_h + (16 * scale if label else 0))
    if kind in ("counter_roll", "digit_counter"):
        figure = _format_counter(element)
        fig_size = TYPE_SIZE["display"] * scale * 1.5
        fig_w = _run_width(figure, "display", scale * 1.5, project_folder,
                           _cache)
        fig_h = fig_size * 1.0
        label_w, label_h = _column_size(
            {"element": "stat_callout",
             "runs": element.get("runs") or []},
            scale * 0.7, project_folder, _cache) \
            if element.get("runs") else (0.0, 0.0)
        gap = 8 * scale if element.get("runs") else 0.0
        return max(fig_w, label_w), fig_h + gap + label_h
    if kind == "step_counter":
        data = element.get("data") or {}
        position = data.get("position", 1)
        total = data.get("total", 1)
        num_w = _run_width(str(position), "display", scale, project_folder,
                           _cache)
        of_w = _run_width(f"of {total}", "micro", scale, project_folder,
                          _cache)
        num_h = TYPE_SIZE["display"] * scale * 1.0
        label_w, label_h = _column_size(
            {"element": "stat_callout",
             "runs": element.get("runs") or []},
            scale * 0.7, project_folder, _cache) \
            if element.get("runs") else (0.0, 0.0)
        gap = 8 * scale if element.get("runs") else 0.0
        return max(num_w + 4 * scale + of_w, label_w), num_h + gap + label_h
    if kind == "comparison_bars":
        # The bar track is width:100%: the full usable width wherever
        # the stack sits. Height is labels plus tracks.
        runs = element.get("runs") or []
        data = element.get("data") or {}
        values = data.get("values") if isinstance(
            data.get("values"), list) else []
        count = min(len(runs), len(values)) or len(runs)
        height = 0.0
        for run in runs[:max(count, 0)]:
            height += _run_line_height(run.get("type_role", "micro"), scale)
            height += 4 * scale + 12 * scale
        if count > 1:
            height += 16 * scale * (count - 1)
        return usable_width, height
    if kind == "list_build":
        runs = element.get("runs") or []
        width = 0.0
        height = 0.0
        for run in runs:
            size = TYPE_SIZE.get(run.get("type_role", "supporting"),
                                 TYPE_SIZE["supporting"]) * scale
            width = max(width, _run_width(
                run.get("text", ""), run.get("type_role", "supporting"),
                scale, project_folder, _cache) + 20 * scale)
            height += size * LIST_LINE_HEIGHT
        if len(runs) > 1:
            height += 12 * scale * (len(runs) - 1)
        return width, height
    if kind in COLUMN_CHROME:
        return _column_size(element, scale, project_folder, _cache)
    # Unknown keys draw nothing (the composition returns null), so they
    # contribute no rect at all. An empty size, not an unknown one.
    return (0.0, 0.0)


def _vertical_zone(anchor: str) -> str:
    anchor = str(anchor or "")
    if anchor.startswith("top"):
        return "top"
    if anchor.startswith("bottom"):
        return "bottom"
    return "middle"


def _horizontal(anchor: str) -> str:
    anchor = str(anchor or "")
    if anchor.endswith("left"):
        return "left"
    if anchor.endswith("right"):
        return "right"
    return "centre"


def tighten_motion_graphics_props(props: dict,
                                   project_folder: str = "",
                                   timeline_size: tuple[int, int] | None = None,
                                   ) -> Optional[TightBox]:
    """The tight canvas for one segment's full-canvas MG props, or None.

    Returns None when the segment draws nothing, when its union is
    effectively the frame (corner accents, asset elements of unknown
    geometry, middle mixed with another zone, or raw coverage), so the
    caller keeps the full-canvas path. Raises where the props carry no
    safe area, exactly as the composition refuses to place by a
    literal.

    A placement Resolve cannot hold is ALSO None, not a clamped
    graphic: the small canvas needs large Pan/Tilt (frame size over
    box size), and past four times the timeline dimensions Resolve
    pins the value while reporting success - the captain's captions
    at -7680 and the motion graphics on huge X. `timeline_size`
    defaults to the props' own delivery frame (the timeline is built
    at it); the caller passes it explicitly where it knows better.
    The cost is stated where it belongs: that one segment renders
    full-canvas, at full-canvas bytes. An overlay the captain cannot
    see is worth more disk.
    """
    elements = props.get("elements") or []
    if not elements:
        return None

    safe = props.get("safeArea")
    if not safe:
        raise ValueError(
            "motion-graphics props carry no safeArea - the composition "
            "refuses to place without it, and so does the box.")

    full_w = int(props.get("width", 0))
    full_h = int(props.get("height", 0))
    if full_w <= 0 or full_h <= 0:
        raise ValueError(
            "motion-graphics props carry no delivery frame to bound "
            f"against (width={props.get('width')!r}, "
            f"height={props.get('height')!r}).")
    usable_width = max(
        0.0, full_w - float(safe["left"]) - float(safe["right"]))

    # Chrome spans by design: four brackets at four corners union to
    # the safe box, which with pads is most of the frame. That is not
    # a bound worth placing - it is the frame wearing a smaller name.
    if any(e.get("element") == "frame_accents" for e in elements):
        return None

    copy = [e for e in elements if e.get("element") not in SELF_POSITIONING]
    zones = {_vertical_zone(e.get("anchor", "")) for e in copy}
    if "middle" in zones and len(zones) > 1:
        return None

    _cache: dict = {}

    # Chrome rects are absolute. Anchored copy shares one flex
    # container per ANCHOR, laid out in row order against each other's
    # real heights - so a stack's union is its rows plus the gaps, hung
    # from the edge its zone names. Keyed by anchor, because two
    # centre stacks with different widths are two containers.
    stacks: dict = {}
    absolute: list = []

    for element in elements:
        kind = element.get("element", "")
        scale = element.get("footprint") or 0
        scale = float(scale) if scale and scale > 0 else 1.0
        size = _element_size(element, scale, project_folder, usable_width,
                             _cache)
        if size is None:
            return None
        w, h = size
        if w <= 0 or h <= 0:
            continue
        anchor = element.get("anchor", "")
        if kind == "progress_bar":
            x0 = float(safe["left"]) - BAR_GLOW
            x1 = full_w - float(safe["right"]) + BAR_GLOW
            bar_h = BAR_HEIGHT * scale + 2 * BAR_GLOW
            if str(anchor).startswith("top"):
                y0 = float(safe["top"]) - BAR_GLOW
            else:
                y0 = full_h - float(safe["bottom"]) - bar_h + BAR_GLOW
            absolute.append((x0, y0, x1, y0 + bar_h))
            continue
        entry = stacks.setdefault(
            anchor, {"zone": _vertical_zone(anchor),
                     "horizontal": _horizontal(anchor), "rows": []})
        entry["rows"].append(
            (element.get("row", 0), w, h))

    if not stacks and not absolute:
        return None

    union = None

    def _grow(x0: float, y0: float, x1: float, y1: float) -> None:
        nonlocal union
        if union is None:
            union = [x0, y0, x1, y1]
        else:
            union[0] = min(union[0], x0)
            union[1] = min(union[1], y0)
            union[2] = max(union[2], x1)
            union[3] = max(union[3], y1)

    for x0, y0, x1, y1 in absolute:
        _grow(x0, y0, x1, y1)

    for anchor, stack in stacks.items():
        # Rows keep their plan order; the widths only size the
        # container. The container holds every element the segment ever
        # shows at this anchor, so the union over the segment is the
        # whole stack - a frame showing a subset shows a subset of this
        # rect (top/bottom subsets share the hung edge; a middle subset
        # centres inside it).
        ordered = sorted(stack["rows"], key=lambda m: m[0])
        stack_w = max(w for _, w, _ in ordered)
        stack_h = (sum(h for _, _, h in ordered)
                   + STACK_GAP_PX * (len(ordered) - 1))
        horizontal = stack["horizontal"]
        if horizontal == "left":
            x0 = float(safe["left"])
        elif horizontal == "right":
            x0 = full_w - float(safe["right"]) - stack_w
        else:
            x0 = full_w / 2.0 - stack_w / 2.0
        zone = stack["zone"]
        if zone == "top":
            y0 = float(safe["top"])
        elif zone == "bottom":
            y0 = full_h - float(safe["bottom"]) - stack_h
        else:
            y0 = full_h / 2.0 - stack_h / 2.0
        _grow(x0, y0, x0 + stack_w, y0 + stack_h)

    if union is None:
        return None
    union_w = union[2] - union[0]
    union_h = union[3] - union[1]
    if union_w <= 0 or union_h <= 0:
        return None

    canvas_w = _ceil_even(union_w + 2 * MG_PAD)
    canvas_h = _ceil_even(union_h + 2 * MG_PAD)

    if canvas_w * canvas_h >= FULL_FRAME_COVERAGE * full_w * full_h:
        return None

    canvas_cx = union[0] - MG_PAD + canvas_w / 2.0
    canvas_cy = union[1] - MG_PAD + canvas_h / 2.0
    placement = placement_for_box(
        canvas_w, canvas_h, canvas_cx, canvas_cy, full_w, full_h)

    # The clamp gate the caption path already has
    # (`resolve_placement_from_correspondence`): a transform Resolve
    # cannot hold is refused here, at render time, where the
    # full-canvas fallback still exists - never at placement time,
    # where the file is already small and the only options are a
    # clamped graphic or a loud report.
    held_against = timeline_size or (full_w, full_h)
    reason = placement_holds(placement, *held_against)
    if reason:
        raise TightBoxMismatch(
            f"tight motion-graphics box needs Pan "
            f"{placement['pan']:.1f} / Tilt {placement['tilt']:.1f}: "
            f"{reason} - this graphic cannot ride a small box, and "
            f"stays full-canvas.")

    tight_props = dict(props)
    tight_props["width"] = canvas_w
    tight_props["height"] = canvas_h
    tight_props["safeArea"] = {
        "top": MG_PAD, "right": MG_PAD,
        "bottom": MG_PAD, "left": MG_PAD,
    }

    return TightBox(
        width=canvas_w,
        height=canvas_h,
        props=tight_props,
        placement=placement,
        union_w=union_w,
        union_h=union_h,
        full_width=full_w,
        full_height=full_h,
    )
