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
 because that is what the render draws. The staged-rule lower third
 (`data.construction == "staged_rule"`) is measured the same way off
 `StagedLowerThird`: the name and title runs in their own roles, the
 rule a twelfth of the display size thick a seventh of it below the
 name - every number the construction derives, derived here too.

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
- A canvas wider or taller than the delivery frame: refused outright
  with `TightBoxClipsInk` by the bound shared with the caption path
  (`tight_box.refuse_canvas_larger_than_frame`), so no tight file is
  ever bigger than the frame it draws on.

How the box lands in Resolve
----------------------------
The tight props keep every element, its local timing and its anchor,
and replace only the canvas (`width`/`height`) and the insets
(`safeArea` becomes the pads, plus the minimum-height growth where a
single zone allowed one - always away from the anchored edge, so the
ink does not move). Anchored stacks hug canvas edges, so
they sit pad-anchored on the small canvas exactly as they sat
inset-anchored on the full one; chrome positions itself from the
insets absolutely, so it is consistent wherever the union put it; a
middle-only stack centres on the small canvas, which is the placement.
`placement_for_box` inverts the measured Resolve relation (see
`tight_box.py`), so the canvas centre lands on the union centre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from library.tools.tight_box import (
    TightBox,
    TightBoxClipsInk,
    TightBoxMismatch,
    grow_to_minimum,
    placement_for_box,
    placement_holds,
    refuse_canvas_larger_than_frame,
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


def _js_round(value: float) -> int:
    """JavaScript `Math.round`: half up, as the composition computes it.

    Python's `round` is banker's (half to even) and would disagree with
    the render by a pixel wherever the composition rounds a .5 up.
    """
    return int(math.floor(float(value) + 0.5))


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


def _staged_lower_third_size(element: dict, scale: float,
                               project_folder: str,
                               _cache: dict) -> tuple:
    """(width, height) of the staged-rule lower-third construction.

    Read off `StagedLowerThird` in the composition, not estimated: the
    NAME run drawn in its own type role (display in every plan
    `speaker_identity` writes - name first, title second), the RULE a
    twelfth of the DISPLAY size thick sitting a seventh of it below the
    name, and the TITLE wiped out along the rule where a second run is
    present. The rule stretches to the column width
    (`alignSelf: stretch`), so it is as wide as the wider run, and the
    column is exactly name + gap + rule + gap + title - no panel, no
    padding, no plate.

    What the build animation adds stays inside the final lockup: the
    rule scales out from the aligned edge, both wipes uncover by
    `clipPath` inset, and the name rises at most 35% of the display
    size from below (`nameLift`) - ~20px at scale 1, inside `MG_PAD`
    the way the slide travel and the text-shadow blur already are.
    The `draw` entrance scales 0.85..1 about the element's own centre
    with a <=4px blur: strictly inside the final bounds. So the union
    over the segment is the finished lockup, measured here.

    Runs past the second draw nothing (the construction reads
    `runs[0]` and `runs[1]` only), so they contribute nothing. No runs
    at all draws nothing - the composition returns null - which is an
    empty size, not an unknown one.
    """
    runs = element.get("runs") or []
    if not runs:
        return (0.0, 0.0)
    name = runs[0]
    title = runs[1] if len(runs) > 1 else None
    name_w = _run_width(name.get("text", ""),
                        name.get("type_role", "supporting"),
                        scale, project_folder, _cache)
    name_h = _run_line_height(name.get("type_role", "supporting"), scale)
    # Derived from the DISPLAY type whatever the runs' roles, exactly
    # as the construction derives them - see `StagedLowerThird`.
    display_size = TYPE_SIZE["display"] * scale
    rule_thickness = max(2, _js_round(display_size / 12))
    rule_gap = max(2, _js_round(display_size / 7))
    width = name_w
    height = name_h + rule_gap + rule_thickness
    if title is not None:
        title_w = _run_width(title.get("text", ""),
                             title.get("type_role", "supporting"),
                             scale, project_folder, _cache)
        title_h = _run_line_height(title.get("type_role", "supporting"),
                                   scale)
        width = max(width, title_w)
        height += rule_gap + title_h
    return width, height


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
    # A `lower_third` the plan asked to be CONSTRUCTED is a different
    # drawing - a rule, a masked name and a wiped title, sized off the
    # display type rather than a padded panel - and
    # `_staged_lower_third_size` models it. Any OTHER construction
    # value falls through to the padded-panel arm below, because that
    # is what the composition draws for it: its `lower_third` arm only
    # special-cases `construction === "staged_rule"` and keeps the
    # flat attribution block for everything else. A future
    # construction that draws a third thing needs its own arm here in
    # the same pass that adds it to the composition - modelling it as
    # either of these two would be the guess `None` exists to refuse.
    if (kind == "lower_third"
            and (element.get("data") or {}).get("construction")
            == "staged_rule"):
        return _staged_lower_third_size(element, scale, project_folder,
                                        _cache)
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


def separable_groups(elements: Sequence[dict]) -> list[list[dict]]:
    """One segment's elements, split into groups that can each be tight.

    The captain, on Reel 26, on the one full-canvas file two animations
    had been rendered into, 2026-09-11::

        "this was a full frame compostie render of two different
         animations, see if you can make it so that its two different
         tighbox animations that are layered on seperate rows on the
         timeline"

    He is describing the refusal above, from the outside.  Reel 26's
    segment carries a `title_lockup` at ``top_centre`` and a
    `subject_emblem` at ``middle_right``; their spans touch, so
    `motion_graphics_plan.plan_segments` clustered them into one segment,
    and a middle zone beside another zone is exactly the case
    `tighten_motion_graphics_props` returns None on - ``top: 50%``
    centres on the CANVAS, so on a small canvas the middle stack centres
    on the wrong frame.  One segment, one full 1080x1920 render, two
    million pixels a frame for two small graphics.

    Nothing was baked: the two are separate entries in the plan with
    their own anchors, timings and copy, and the only thing joining them
    was the cluster.  So the answer is to stop joining them.  This
    returns the partition - middle-anchored copy in one group,
    everything else in the other - and each group is then its own
    segment, its own tight box, and its own timeline row where they
    overlap in time.

    ONE group back means no split helps: a segment already inside one
    zone family is already tightenable (or already refused for a reason
    a split cannot fix, like `frame_accents` spanning by design).  The
    caller keeps its single segment and nothing changes.
    """
    items = list(elements or [])
    if len(items) < 2:
        return [items] if items else []
    copy = [e for e in items if e.get("element") not in SELF_POSITIONING]
    zones = {_vertical_zone(e.get("anchor", "")) for e in copy}
    if "middle" not in zones or len(zones) < 2:
        return [items]
    middle, rest = [], []
    for element in items:
        if (element.get("element") not in SELF_POSITIONING
                and _vertical_zone(element.get("anchor", "")) == "middle"):
            middle.append(element)
        else:
            # Chrome positions itself from the insets absolutely, so it
            # is consistent wherever the union puts it; it rides with
            # the edge-anchored group rather than forcing a third.
            rest.append(element)
    return [group for group in (rest, middle) if group]


@dataclass(frozen=True)
class TightRefusal:
    """Why one segment's props stay full canvas, in the caller's words.

    `reason` is a stable machine-readable code - the build-time guard
    (`check_motion_graphics_files`) matches on it, so a new code is a
    new contract. `element` names the element kind that caused it
    (comma-joined where the union is joint work, None where no one
    element did). `detail` carries the numbers for a human.
    """

    reason: str
    element: Optional[str]
    detail: str = ""

    def message(self) -> str:
        """The one line the artefact sidecar and the step record carry."""
        who = f" ({self.element})" if self.element else ""
        return f"{self.reason}{who} - {self.detail}".rstrip(" -")


#: Refusal reasons that PASS the build-time guard by saying so: the
#: union genuinely is the delivery frame (`frame_accents` at four
#: corners), or the project declared full-canvas carrying and the
#: tighten path was never asked (`geometry_full_declared`, recorded by
#: the render step, not by the tighten path). Every other recorded
#: reason is counted in the conformance census but does not fail the
#: build: it names real ink the engine could not bound (an asset file
#: nothing measures, a run wider than the frame), and failing a build
#: on planned content would punish the plan for the engine's reach.
#: What FAILS the build is a full-canvas artefact with NO recorded
#: reason - the silence this file's refusals used to ship as.
BY_DESIGN_REASONS = frozenset({
    "frame_accents_span_by_design",
    "geometry_full_declared",
})

#: Sibling of each `<name>_props.json`: whether the artefact was
#: tightened or refused, and on a refusal the reason and the element.
TIGHTNESS_SIDECAR_SUFFIX = "_tightness.json"


def _tighten_impl(props: dict,
                  project_folder: str = "",
                  timeline_size: tuple[int, int] | None = None,
                  ) -> tuple[Optional[TightBox], Optional[TightRefusal]]:
    """The tight canvas for one segment's full-canvas MG props, or None.

    Returns None when the segment draws nothing, when its union is
    effectively the frame (corner accents, asset elements of unknown
    geometry, middle mixed with another zone, or raw coverage), so the
    caller keeps the full-canvas path. Raises where the props carry no
    safe area, exactly as the composition refuses to place by a
    literal - and raises `TightBoxClipsInk` where the predicted canvas
    is wider or taller than the delivery frame, by the same shared
    bound the caption path refuses on
    (`tight_box.refuse_canvas_larger_than_frame`): a tight file
    bigger than the frame it draws on is never produced.

    A placement Resolve cannot hold RAISES `TightBoxMismatch`, never a
    clamped graphic: the small canvas needs large Pan/Tilt (frame size
    over box size), and past four times the timeline dimensions
    Resolve pins the value while reporting success - the captain's
    captions at -7680 and the motion graphics on huge X.
    `timeline_size` defaults to the props' own delivery frame (the
    timeline is built at it); the caller passes it explicitly where it
    knows better. The cost is stated where it belongs: that one
    segment renders full-canvas, at full-canvas bytes. An overlay the
    captain cannot see is worth more disk.
    """
    elements = props.get("elements") or []
    if not elements:
        return None, TightRefusal(
            reason="no_elements",
            element=None,
            detail="the segment plans no elements, so there is no union "
                   "to bound")

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
        return None, TightRefusal(
            reason="frame_accents_span_by_design",
            element="frame_accents",
            detail="four corner accents union to the safe box, which is "
                   "the frame wearing a smaller name")

    copy = [e for e in elements if e.get("element") not in SELF_POSITIONING]
    zones = {_vertical_zone(e.get("anchor", "")) for e in copy}
    if "middle" in zones and len(zones) > 1:
        middle_kinds = sorted({
            str(e.get("element", ""))
            for e in copy
            if _vertical_zone(e.get("anchor", "")) == "middle"})
        return None, TightRefusal(
            reason="middle_zone_mixed",
            element=",".join(middle_kinds) or None,
            detail="a middle-anchored stack beside another vertical zone: "
                   "`top: 50%` centres on the canvas, so on a small canvas "
                   "the stack centres on the wrong frame")

    _cache: dict = {}
    absolute_zones: set = set()

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
            if kind in ASSET_ELEMENTS:
                refusal = TightRefusal(
                    reason="asset_geometry_unknown",
                    element=kind or None,
                    detail=f"a {kind} element's height comes from a "
                           f"project-supplied file nothing measures - "
                           f"bounding an unknown aspect would be forcing "
                           f"the win")
            else:
                refusal = TightRefusal(
                    reason="unmeasurable_element",
                    element=kind or None,
                    detail=f"a {kind} element has no measuring arm - "
                           f"refused rather than bounded around a guess")
            return None, refusal
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
            absolute.append((x0, y0, x1, y0 + bar_h, kind))
            absolute_zones.add(_vertical_zone(anchor))
            continue
        entry = stacks.setdefault(
            anchor, {"zone": _vertical_zone(anchor),
                     "horizontal": _horizontal(anchor), "rows": []})
        entry["rows"].append(
            (element.get("row", 0), w, h, kind))

    if not stacks and not absolute:
        return None, TightRefusal(
            reason="nothing_drawn",
            element=None,
            detail="every element draws nothing (unknown keys return "
                   "null in the composition), so there is no union")

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

    for x0, y0, x1, y1, _kind in absolute:
        _grow(x0, y0, x1, y1)

    for anchor, stack in stacks.items():
        # Rows keep their plan order; the widths only size the
        # container. The container holds every element the segment ever
        # shows at this anchor, so the union over the segment is the
        # whole stack - a frame showing a subset shows a subset of this
        # rect (top/bottom subsets share the hung edge; a middle subset
        # centres inside it).
        ordered = sorted(stack["rows"], key=lambda m: m[0])
        stack_w = max(w for _, w, _, _ in ordered)
        stack_h = (sum(h for _, _, h, _ in ordered)
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

    # Which element kinds built the union, for the refusal below to
    # name: the stacks and the absolute rects both carry their kind.
    union_kinds = sorted({
        kind for stack in stacks.values()
        for _, _, _, kind in stack["rows"]
    } | {kind for _, _, _, _, kind in absolute})

    if union is None:
        return None, TightRefusal(
            reason="nothing_drawn",
            element=None,
            detail="the measured elements union to no area")
    union_w = union[2] - union[0]
    union_h = union[3] - union[1]
    if union_w <= 0 or union_h <= 0:
        return None, TightRefusal(
            reason="nothing_drawn",
            element=None,
            detail=f"the union is {union_w:.0f}x{union_h:.0f} - no area "
                   f"to bound")

    canvas_w = _ceil_even(union_w + 2 * MG_PAD)
    measured_h = _ceil_even(union_h + 2 * MG_PAD)

    # The floor that keeps the placement inside Resolve's rail
    # (`tight_box.MIN_CANVAS_HEIGHT`) - but ONLY where growing cannot
    # move the ink. Anchored stacks hug canvas edges, so a top+bottom
    # mix re-lays-out on a taller canvas: growing there would break
    # the union this placement is computed from, and motion graphics
    # have no probe/verify loop to catch it. A single zone grows away
    # from its edge (top grows below, bottom above, middle splits);
    # anything mixed keeps its measured size and the clamp gate below
    # still refuses what Resolve cannot hold.
    all_zones = {stack["zone"] for stack in stacks.values()} | set(
        absolute_zones)
    grown_below = 0
    top_extra = 0
    canvas_h = measured_h
    if len(all_zones) == 1:
        (zone,) = tuple(all_zones)
        canvas_h, top_extra = grow_to_minimum(measured_h, zone, full_h)
        grown_below = canvas_h - measured_h - top_extra

    # The frame bound the caption path refuses on, shared rather than
    # re-spelled: a predicted canvas bigger than the delivery frame is
    # a refusal (full-canvas fallback at the caller), never a file.
    # The refusal rides on the exception, so the caller's fallback can
    # still say which element and which union caused it.
    try:
        refuse_canvas_larger_than_frame(
            canvas_w, canvas_h, full_w, full_h,
            f"predicted motion-graphics union ({union[0]:.0f},"
            f"{union[1]:.0f})-({union[2]:.0f},{union[3]:.0f})")
    except TightBoxClipsInk as exc:
        exc.refusal = TightRefusal(
            reason="canvas_larger_than_frame",
            element=",".join(union_kinds) or None,
            detail=f"predicted canvas {canvas_w}x{canvas_h} on a "
                   f"{full_w}x{full_h} frame from union "
                   f"({union[0]:.0f},{union[1]:.0f})-({union[2]:.0f},"
                   f"{union[3]:.0f})")
        raise

    if canvas_w * canvas_h >= FULL_FRAME_COVERAGE * full_w * full_h:
        coverage = canvas_w * canvas_h / (full_w * full_h)
        return None, TightRefusal(
            reason="covers_frame",
            element=",".join(union_kinds) or None,
            detail=f"the padded union is {coverage:.0%} of the frame - "
                   f"marginal pixel savings are not worth the placement "
                   f"risk")

    canvas_cx = union[0] - MG_PAD + canvas_w / 2.0
    canvas_cy = union[1] - (MG_PAD + top_extra) + canvas_h / 2.0
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
        mismatch = TightBoxMismatch(
            f"tight motion-graphics box needs Pan "
            f"{placement['pan']:.1f} / Tilt {placement['tilt']:.1f}: "
            f"{reason} - this graphic cannot ride a small box, and "
            f"stays full-canvas.")
        mismatch.refusal = TightRefusal(
            reason="placement_unholdable",
            element=",".join(union_kinds) or None,
            detail=f"Pan {placement['pan']:.1f} / Tilt "
                   f"{placement['tilt']:.1f} on a "
                   f"{held_against[0]}x{held_against[1]} timeline: "
                   f"{reason}")
        raise mismatch

    tight_props = dict(props)
    tight_props["width"] = canvas_w
    tight_props["height"] = canvas_h
    tight_props["safeArea"] = {
        "top": MG_PAD + top_extra, "right": MG_PAD,
        "bottom": MG_PAD + grown_below, "left": MG_PAD,
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
    ), None


# ─── What the artefact says about itself ─────────────────────────────
#
# A `*_props.json` at the full delivery frame used to be
# indistinguishable from one that was never a tighten candidate - the
# props carry only `width`/`height`, no reason - which is how sixteen
# full-frame lower thirds sat in the captain's project unnoticed. So
# every rendered overlay artefact now carries a sibling sidecar
# (`<stem>_tightness.json` beside `<stem>_props.json`) saying whether
# it was tightened or refused, and on a refusal the reason and the
# element. The sidecar is written AFTER the drawing digest is computed
# and never enters it: it describes the carrying, not the picture, so
# it must not break reuse keys.

TIGHTNESS_SIDECAR_VERSION = 1


def tightness_record(box: Optional[TightBox],
                     refusal: Optional[TightRefusal],
                     full_width: int, full_height: int) -> dict:
    """The sidecar record for one rendered artefact, as JSON-safe dict.

    `box` is the tighten result (None where the artefact stayed full
    canvas) and `refusal` is why - one of the two is always present:
    a tight artefact needs no reason and a full one without a reason
    is exactly what the build-time guard refuses.
    """
    if box is not None:
        return {
            "version": TIGHTNESS_SIDECAR_VERSION,
            "outcome": "tight",
            "reason": "",
            "element": None,
            "detail": (f"union {box.union_w:.0f}x{box.union_h:.0f} "
                       f"renders at {box.width}x{box.height}"),
            "width": box.width,
            "height": box.height,
            "placement": box.placement,
        }
    reason = refusal.reason if refusal is not None else ""
    element = refusal.element if refusal is not None else None
    detail = (refusal.detail if refusal is not None and refusal.detail
              else (refusal.message() if refusal is not None else ""))
    return {
        "version": TIGHTNESS_SIDECAR_VERSION,
        "outcome": "full",
        "reason": reason,
        "element": element,
        "detail": detail,
        "width": full_width,
        "height": full_height,
        "placement": None,
    }


def sidecar_path_for(props_path: str) -> str:
    """The sidecar path beside one props file."""
    if props_path.endswith("_props.json"):
        return props_path[:-len("_props.json")] + TIGHTNESS_SIDECAR_SUFFIX
    if props_path.endswith(".json"):
        return props_path[:-len(".json")] + TIGHTNESS_SIDECAR_SUFFIX
    return props_path + TIGHTNESS_SIDECAR_SUFFIX


def check_motion_graphics_files(props_paths: Sequence[str],
                                full_w: int, full_h: int,
                                ) -> tuple[list, dict]:
    """Refuse every full-canvas artefact that has not declared why.

    Returns `(errors, census)`. `errors` is empty where every
    full-canvas props file carries a sidecar naming its reason - a
    legitimate full-frame graphic passes BY SAYING SO
    (`BY_DESIGN_REASONS`), and any other recorded reason passes
    counted but not failed (it names real ink the engine could not
    bound; failing a build on planned content would punish the plan
    for the engine's reach). What fails is a full-canvas artefact with
    NO recorded reason - missing sidecar, unreadable sidecar, empty
    reason, or a sidecar that disagrees with the props beside it -
    because that is the silence sixteen lower thirds shipped as.

    Tight artefacts need no sidecar: their size says the outcome. A
    sidecar that claims `full` beside tight props (or `tight` beside
    full props, or any other size mismatch) is stale, and fails like
    an undeclared one - a record that cannot be believed is worse
    than no record.

    `census` is JSON-safe: total / tight / full_by_design /
    full_with_reason / full_undeclared counts, `by_reason` tallies,
    and the undeclared and full file names. Deterministic and cheap -
    JSON reads only, no renders - so it runs on every build.
    """
    import json as _json
    import os as _os

    errors: list = []
    by_reason: dict = {}
    full_files: dict = {}
    undeclared_files: list = []
    tight = 0
    full_by_design = 0
    full_with_reason = 0

    for props_path in props_paths:
        name = _os.path.basename(props_path)
        try:
            with open(props_path, encoding="utf-8") as handle:
                props = _json.load(handle)
        except (OSError, ValueError) as exc:
            errors.append(
                f"{name}: cannot be checked - props unreadable ({exc})")
            undeclared_files.append(name)
            continue
        width = props.get("width")
        height = props.get("height")
        sidecar_path = sidecar_path_for(props_path)
        sidecar = None
        try:
            with open(sidecar_path, encoding="utf-8") as handle:
                sidecar = _json.load(handle)
        except (OSError, ValueError):
            sidecar = None
        if width != full_w or height != full_h:
            if (isinstance(sidecar, dict)
                    and sidecar.get("outcome") == "full"):
                errors.append(
                    f"{name}: props are {width}x{height} but the "
                    f"tightness sidecar claims a full-canvas refusal "
                    f"({sidecar.get('reason')!r}) - stale sidecar")
                undeclared_files.append(name)
            else:
                tight += 1
            continue
        # Full canvas: the sidecar must say why.
        if not isinstance(sidecar, dict):
            errors.append(
                f"{name}: full-canvas {width}x{height} overlay with no "
                f"tightness sidecar - undeclared full canvas cannot "
                f"ship")
            undeclared_files.append(name)
            continue
        if sidecar.get("outcome") != "full":
            errors.append(
                f"{name}: full-canvas {width}x{height} props beside a "
                f"sidecar claiming {sidecar.get('outcome')!r} - stale "
                f"sidecar")
            undeclared_files.append(name)
            continue
        if (sidecar.get("width") != width
                or sidecar.get("height") != height):
            errors.append(
                f"{name}: full-canvas {width}x{height} props beside a "
                f"sidecar sized {sidecar.get('width')}x"
                f"{sidecar.get('height')} - stale sidecar")
            undeclared_files.append(name)
            continue
        reason = sidecar.get("reason") or ""
        if not reason:
            errors.append(
                f"{name}: full-canvas {width}x{height} overlay whose "
                f"sidecar names no reason - undeclared full canvas "
                f"cannot ship")
            undeclared_files.append(name)
            continue
        by_reason[reason] = by_reason.get(reason, 0) + 1
        full_files[name] = {
            "reason": reason,
            "element": sidecar.get("element"),
            "detail": sidecar.get("detail") or "",
        }
        if reason in BY_DESIGN_REASONS:
            full_by_design += 1
        else:
            full_with_reason += 1

    census = {
        "total": tight + full_by_design + full_with_reason
                 + len(undeclared_files),
        "tight": tight,
        "full_by_design": full_by_design,
        "full_with_reason": full_with_reason,
        "full_undeclared": len(undeclared_files),
        "by_reason": by_reason,
        "undeclared_files": sorted(undeclared_files),
        "full_files": full_files,
    }
    return errors, census


def check_motion_graphics_dir(mg_dir: str, full_w: int,
                              full_h: int) -> tuple[list, dict]:
    """The directory walk over `check_motion_graphics_files`.

    A missing directory is not an error - a project that rendered no
    motion graphics has nothing to declare - and yields an empty
    census. Anything on disk gets checked.
    """
    import glob as _glob
    import os as _os

    if not mg_dir or not _os.path.isdir(mg_dir):
        return [], {"total": 0, "tight": 0, "full_by_design": 0,
                    "full_with_reason": 0, "full_undeclared": 0,
                    "by_reason": {}, "undeclared_files": [],
                    "full_files": {}}
    paths = sorted(_glob.glob(
        _os.path.join(mg_dir, "*_props.json")))
    return check_motion_graphics_files(paths, full_w, full_h)


def tighten_motion_graphics_props(
        props: dict,
        project_folder: str = "",
        timeline_size: tuple[int, int] | None = None,
        ) -> Optional[TightBox]:
    """The tight canvas for one segment's full-canvas MG props, or None.

    The box half of `_tighten_impl` - every refusal reason the other
    half returns is documented there. Raises exactly as it always has.
    """
    box, _refusal = _tighten_impl(
        props, project_folder, timeline_size=timeline_size)
    return box


def tighten_motion_graphics_props_with_reason(
        props: dict,
        project_folder: str = "",
        timeline_size: tuple[int, int] | None = None,
        ) -> tuple[Optional[TightBox], Optional[TightRefusal]]:
    """The tight canvas AND why not, where the caller records the why.

    Returns `(box, None)` where the segment tightens and
    `(None, refusal)` where it stays full canvas, so a fallback is
    always a NAMED fallback - the artefact sidecar (`tightness_record`)
    and the build-time guard (`check_motion_graphics_files`) both read
    the refusal. `TightBoxClipsInk` / `TightBoxMismatch` still raise,
    carrying their refusal as `.refusal`.
    """
    return _tighten_impl(props, project_folder,
                         timeline_size=timeline_size)
