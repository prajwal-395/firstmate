"""The drawn bounds of a caption card, and where they land in Resolve.

A subtitle segment today renders at the full delivery frame (1080x1920):
two million pixels per frame to draw a caption occupying a few percent
of them. The position is baked in at render time, so repositioning
means re-rendering.

A tight box renders only what the segment draws - the union of its
cards, anchored exactly as the composition anchors them - and the small
clip is placed at an offset on the Resolve timeline instead. Smaller,
faster, and MOVABLE after the fact, which is the part that changes how
the captain works.

Why the bounds are knowable BEFORE rendering
--------------------------------------------
The composition (`SubtitleOverlay/index.tsx`) lays cards out
deterministically from the props: a flex box bounded by
`captionMaxWidth`, words as inline-blocks with a 0.24em right margin,
`lineHeight: 1.2`, `padding: 24px <outline>px`, emphasis words at
1.14em. Step 4.01 already measures every word in pixels with the same
face the render loads (`CaptionFitter`), so the same measurement
replicates the layout - greedy wrap, per-line widths, per-card heights
- without drawing anything. What is replicated here is READ from the
two sources of truth, not re-chosen:

- `WORD_GAP_EM` / `EMPHASIS_SCALE`: `AnimatedWord.tsx` and step 4.01.
- `CaptionFitter.word_width`: the face, the weight, the size.

The wrap counts the trailing gap of every word INCLUDING the last,
where step 4.01's grouper counts gaps between words only. The render
puts `marginRight` on every word span, so the last word's margin is
real layout width that can force a wrap the grouper did not plan. The
box follows the render, not the plan: a box that fits the plan while
the render wraps taller clips ink.

Why the pads are what they are
------------------------------
Beyond the laid-out glyph boxes the render paints:

- the outline: `outlineWidth` px in all eight shadow directions
  (boldest style: 18px);
- the drop shadow `0 10px 20px`: ~20px blur on every side, shifted
  10px down, so ~30px below the text;
- nothing else: the entry animation scales 0.95 INTO place and never
  exceeds the laid-out box.

`PAD_X` / `PAD_TOP` / `PAD_BOTTOM` clear the shadow plus a margin, and
dominate the 2% edge margin `subtitle_qa` fails ink within.

How the box lands in Resolve
----------------------------
Measured on Resolve 21 against solid-colour clips, 2026-09-08, on a
scratch project:

- a smaller-than-timeline clip auto-scales to FIT by default;
- per-clip `Scaling=1` (Crop) draws it at NATIVE pixels, centred;
  0 and 2 fit, 3 stretches full-frame;
- `Pan`/`Tilt` then move it: shift_x = Pan * (placed_W /
  timeline_W), shift_y = -Tilt * (placed_H / timeline_H). Pan=200
  moved a 400px-wide clip on a 1080 timeline 74px right; Tilt=300
  moved a 200px-tall clip on a 1920 timeline 31px up.

`placement_for_box` inverts that relation, so a canvas whose edges are
known in full-frame coordinates yields the three SetProperty values
that put it there. Every SetProperty is still judged by its return
value at placement time - this module computes, Resolve disposes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from library.steps.step_4_01_plan_subtitles.step import (
    EMPHASIS_SCALE,
    WORD_GAP_EM,
    CaptionFitter,
)
from library.tools.render_fonts import measurable_font_path

# Beyond the flex-box edge: shadow blur (~20px) plus margin. The
# component's own horizontal padding (exactly the outline) is INSIDE
# the box already, so this only has to clear what the shadow paints
# outside it.
PAD_X = 24
# The shadow reaches ~10px above the text (20px blur over a shadow
# offset 10px down). Single-sided, because nothing hangs above a card.
PAD_TOP = 16
# The shadow reaches ~30px below the text (10px offset + 20px blur).
PAD_BOTTOM = 36

# What the component puts above and below the type inside the card:
# `padding: 24px <outline>px`.
CARD_VERTICAL_PADDING = 48

# The composition's line height: `lineHeight: "1.2"`.
LINE_HEIGHT_EM = 1.2


def _ceil_even(value: float) -> int:
    """Round up to an even int, so no codec clips half a pixel row."""
    return int(math.ceil(value / 2.0)) * 2


@dataclass(frozen=True)
class TightBox:
    """A segment's tight canvas, its props, and its Resolve placement."""

    width: int
    height: int
    props: dict
    placement: dict
    union_w: float
    union_h: float
    full_width: int
    full_height: int


def _normalise(word: str) -> str:
    import re

    return re.sub(r"^[^\w']+|[^\w']+$", "", word.lower())


def _wrap_card(text_words: list[str], emphasis: set[str],
               word_width_fn, gap: float,
               max_width: float) -> list[tuple[float, bool]]:
    """Greedy wrap replicating the flex container: (line_width, has_emphasis).

    Every word carries its trailing 0.24em margin INTO the wrap
    decision, because the render's `marginRight` is on every span and
    the last word's margin is what forces the wrap the plan misses.
    `word_width_fn` measures one bare word; the gap is added here, so
    emphasis scaling (a per-word property) and the margin (a layout
    property) stay in their own lanes.
    """
    lines: list[tuple[float, bool]] = []
    current_w = 0.0
    current_emph = False
    for word in text_words:
        width = word_width_fn(word) + gap
        if current_w > 0 and current_w + width > max_width:
            lines.append((current_w, current_emph))
            current_w, current_emph = 0.0, False
        current_w += width
        current_emph = current_emph or _normalise(word) in emphasis
    if current_w > 0 or not lines:
        lines.append((current_w, current_emph))
    return lines


def _fitter_for_style(style: dict, max_width: float,
                      project_folder: str = "") -> CaptionFitter:
    """The same face the render loads, or the same fallback 4.01 uses."""
    font_path = measurable_font_path(
        style.get("fontFamily", ""),
        style.get("fontFile"),
        project_folder or None,
    )
    return CaptionFitter(
        font_path=font_path,
        font_size=style.get("fontSize", 58),
        font_weight=style.get("fontWeight", 800),
        usable_width=max_width,
        outline_width=style.get("outlineWidth", 0),
    )


def placement_for_box(canvas_w: float, canvas_h: float,
                      canvas_cx: float, canvas_cy: float,
                      full_w: int, full_h: int) -> dict:
    """The SetProperty values putting a native-pixel clip's centre where
    the full-frame coordinates say.

    The inverse of the measured relation: shift_x = Pan * (placed_W /
    timeline_W), shift_y = -Tilt * (placed_H / timeline_H), with the
    clip at Scaling=1 so placed pixels are canvas pixels.
    """
    dx = canvas_cx - full_w / 2.0
    dy = canvas_cy - full_h / 2.0
    return {
        "scaling": 1,
        "pan": dx * (full_w / canvas_w),
        "tilt": -dy * (full_h / canvas_h),
    }


def tighten_subtitle_props(props: dict,
                           project_folder: str = "") -> Optional[TightBox]:
    """The tight canvas for one segment's full-canvas props, or None.

    Returns None when the segment draws nothing (no subtitles), so the
    caller keeps the full-canvas path rather than rendering an empty
    box. Raises where the style carries no geometry, exactly as the
    component refuses to place by a literal.
    """
    subtitles = props.get("subtitles") or []
    if not subtitles:
        return None

    style = props.get("style") or {}
    if not style.get("safeArea"):
        raise ValueError(
            "subtitle props carry no style.safeArea - the component "
            "refuses to place without it, and so does the box.")
    if not style.get("captionMaxWidth"):
        raise ValueError(
            "subtitle props carry no style.captionMaxWidth - the wrap "
            "width is unknown, so no bound can be computed.")

    full_w = int(props.get("width", 0))
    full_h = int(props.get("height", 0))
    safe = style["safeArea"]
    max_width = float(style["captionMaxWidth"])
    position = style.get("position") or "bottom"

    fitter = _fitter_for_style(style, max_width, project_folder)

    union_w = 0.0
    union_h = 0.0
    for card in subtitles:
        fit = float(card.get("fitScale") or 1.0)
        font_size = float(style.get("fontSize", 58)) * fit
        # The fitter measures at the STYLE size; a shrunk card draws
        # every word and every gap smaller, so the measurement scales
        # with it.
        gap = WORD_GAP_EM * font_size
        emphasis = {_normalise(e) for e in card.get("emphasisWords") or []}
        text_words = [w.get("word", "") for w in card.get("words") or []]
        if not text_words:
            text_words = str(card.get("text", "")).split()

        def bare_width(word: str, _emphasis=emphasis, _fit=fit) -> float:
            scale = (EMPHASIS_SCALE
                     if _normalise(word) in _emphasis else 1.0)
            return fitter.word_width(word) * _fit * scale

        lines = _wrap_card(text_words, emphasis, bare_width, gap,
                           max_width)

        card_w = max((w for w, _ in lines), default=0.0)
        card_h = sum(
            LINE_HEIGHT_EM * font_size * (EMPHASIS_SCALE if emph else 1.0)
            for _, emph in lines) + CARD_VERTICAL_PADDING
        union_w = max(union_w, card_w)
        union_h = max(union_h, card_h)

    canvas_w = _ceil_even(union_w + 2 * PAD_X)
    canvas_h = _ceil_even(union_h + PAD_TOP + PAD_BOTTOM)

    # The content box in full-frame coordinates. Horizontally every
    # card is centred, so the union is centred. Vertically the union
    # is anchored at the edge `position` names, exactly as the
    # component anchors each card.
    if position == "top":
        content_top = float(safe["top"])
        content_bottom = content_top + union_h
    elif position == "center":
        content_top = full_h / 2.0 - union_h / 2.0
        content_bottom = full_h / 2.0 + union_h / 2.0
    else:
        content_bottom = full_h - float(safe["bottom"])
        content_top = content_bottom - union_h

    canvas_top = content_top - PAD_TOP
    canvas_cx = full_w / 2.0
    canvas_cy = canvas_top + canvas_h / 2.0

    placement = placement_for_box(
        canvas_w, canvas_h, canvas_cx, canvas_cy, full_w, full_h)

    tight_style = dict(style)
    tight_style["safeArea"] = {
        "top": PAD_TOP, "right": PAD_X,
        "bottom": PAD_BOTTOM, "left": PAD_X,
    }
    # captionMaxWidth is DELIBERATELY unchanged: it is the wrap width,
    # and the canvas is wider than it everywhere, so every card wraps
    # exactly as on the full frame.
    tight_props = dict(props)
    tight_props["width"] = canvas_w
    tight_props["height"] = canvas_h
    tight_props["style"] = tight_style

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
