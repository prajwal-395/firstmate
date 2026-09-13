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

Why the box is MEASURED, not predicted
--------------------------------------
`tighten_subtitle_props` below predicts the union from PIL word widths.
Measured on the field test (Reel 12, 2026-09-09) the prediction clips:
10 of 12 pilot segments drew ink outside their predicted boxes, because
the PIL fitter under-measures against the Chromium renderer - one card
the box laid out as a single 840px line rendered as two lines 638px
wide, with ink 25px above the box top across 33 frames. Asking one
engine to predict another's glyph metrics is the defect; the render
path therefore never sizes a canvas from this predictor. The predictor
stays for planning-time estimates; nothing that reaches a timeline is
sized by it.

Why the canvas is CONSTANT, not measured per segment
----------------------------------------------------
Caption ink has a STRUCTURAL bound, so the canvas does not need
predicting OR measuring per segment. The composition lays every card
out inside `maxWidth: captionMaxWidth` (border-box, so the outline
padding is inside it), centred in a `width: 100%` flex container. A
canvas WIDER than `captionMaxWidth` therefore binds no wrap: every
card wraps exactly as on the full frame, whatever the segment says.
The canvas only has to clear what the render paints outside the
laid-out box - the shadow (`PAD_X` / `PAD_TOP` / `PAD_BOTTOM`) - plus
the trailing word margin the wrap decision counts on every word span
including the last (`TRAILING_MARGIN_PX`). `constant_caption_box`
derives that canvas from the props: for the captain's 840px wrap
width it is 904x480, and it contained the ink of all 521 measured
caption segments with at least 6px to spare on every side.

Why the placement is ARITHMETIC, not a correspondence
-----------------------------------------------------
On a canvas wider than `captionMaxWidth` the layout is the full-frame
layout translated by a KNOWN offset: horizontally every card is
centred, so the centred canvas sits centred (`pan` 0); vertically the
cards hang from the edge `position` names, so the canvas edge sits one
pad past the anchored card edge. `constant_caption_box` computes that
origin from the props - no probe render, no read-off. The retired
measured path (a full-canvas probe, `tighten_measured`,
`resolve_placement_from_correspondence`, `verify_frames`) proved the
translation per segment at ~7s a segment in probe renders plus ~10MB
of transient PNGs; the constant canvas makes the translation true by
construction, and the one remaining guard proves the premise instead
of the conclusion.

Why ONE guard remains: ink-touches-edge, off the alpha plane
------------------------------------------------------------
The arithmetic is exact only while the ink stays inside the canvas.
Ink that TOUCHES the canvas edge is the one unrecoverable failure -
clipped pixels cannot be fixed by repositioning, because the pixels
are gone - so `ink_touches_edge` reads the rendered file's own alpha
plane and the caller carries the card full canvas on a hit. A miss
costs one ffmpeg decode (~0.1s a segment, 70x cheaper than the probe
it replaces) and writes no transient files. Ink well inside the edge
needs no verdict: the pads are planning margins, and the canvas that
holds the structural bound holds every segment that fits it.
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

That scratch relation was RIGHT, and this module no longer keeps
its own copy of it: `library/tools/resolve_transform.py` is the ONE
model of this Resolve behaviour, for overlays and for picture alike,
and every function below calls it. The 2026-09-11 "draw gain of 2"
was an arithmetic error - it calibrated against a CAPTURED Pan/Tilt
rather than one it had set itself, and paired a real still with a
number that was by then half the value in force. Re-measured on 16
rendered plates across two builds and four processes, the gain is 1
on both axes at every canvas size, so the constant and its
`draw_gain` accessor are gone.

`placement_for_box` inverts that relation, so a canvas whose edges are
known in full-frame coordinates yields the three SetProperty values
that put it there. Every SetProperty is still judged by its return
value at placement time - this module computes, Resolve disposes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from library.steps.step_4_01_plan_subtitles.step import (
    EMPHASIS_SCALE,
    WORD_GAP_EM,
    CaptionFitter,
)
from library.tools.qa.subtitle_qa import ALPHA_INK_THRESHOLD
from library.tools.render_fonts import measurable_font_path
from library.tools.resolve_transform import drawn_origin, pan_tilt_for_centre

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

# The trailing word margin the wrap decision counts but the plan does
# not: every word span carries `marginRight: 0.24em` INTO the wrap,
# including the last word of a line, whose margin draws nothing but
# still occupies layout width (~14px at 58px type). A line filled
# exactly to `captionMaxWidth` therefore lays out up to ~14px wider
# than the widest ink the plan measured. The structural canvas bound
# carries 16px past `captionMaxWidth + 2*PAD_X` for it (ceiled even):
# for the captain's 840px wrap width the constant canvas is 904 wide.
TRAILING_MARGIN_PX = 16

# The composition's line height: `lineHeight: "1.2"`.
LINE_HEIGHT_EM = 1.2

# The smallest canvas height a tight box may ship, in pixels.
#
# A tight box is small and placing it needs a large Pan/Tilt value -
# tilt scales as full_h / canvas_h - and Resolve silently pins
# Pan/Tilt while returning success (the captain's 37 off-frame
# captions, 2026-09-09). Full-frame artefacts need no transform, so
# the project defaulted to full canvas to make the clamp
# unreachable.
#
# The measured numbers point at a third option: the single three-line
# caption at h=300 needed only Tilt 3366 and landed correctly, while
# h=152 needed 7579 and did not - the cliff sits at h=270.4 against
# the 3840 rail, verified live on the captain's own 1080x1920 timeline
# 2026-09-10 (probes at -3400/-3840 hold exactly; -4000 and past
# return True and pin at -3840). A canvas floored at 480 keeps every
# caption inside that rail (the smallest box needs ~2400, worst cases
# stay under 3400) while carrying a quarter of the frame's pixels.
# What that buys in bytes is SMALLER than it sounds, measured the
# same day on Reel 09's own files: full-frame captions cost 198MB
# for 19, the floor estimates 158MB - the ink dominates and
# transparent margins were always cheap, so the floor returns about
# a fifth of the caption disk, not most of it. Tight still wins on
# movability and render time; the disk is a bonus, stated honestly.
# The floor is applied AWAY from the anchor (bottom-anchored cards
# grow upward), so the ink does not move: the correspondence
# read-off and `verify_frames` prove that per segment, and a segment
# that fails the proof still falls back to full canvas. `placement_holds`
# and the placement read-back both survive below, unchanged.
#
# DERIVED, not chosen, and derived at ONE frame: it is the canvas height
# that keeps the worst caption's Tilt inside the 3840 rail measured on a
# 1080x1920 timeline. Both numbers it comes from are properties of that
# geometry, so this floor is knowledge about vertical delivery. It is
# left as it is rather than re-derived per frame because on any other
# frame `placement_limits` returns None and `placement_holds` refuses
# every tight placement outright (see `MEASURED_RAILS`), so the floor
# has nothing left to protect there. Re-derive it in the same pass that
# measures a second frame's rail, not before - a floor computed from an
# unmeasured rail is the guess this module refuses to make.
MIN_CANVAS_HEIGHT = 480


def grow_to_minimum(canvas_h: int, anchor: str,
                    ceiling: int) -> tuple[int, int]:
    """Enforce `MIN_CANVAS_HEIGHT`, returning `(new_h, top_extra)`.

    `anchor` names the edge the ink hangs from - `"top"`, `"bottom"`
    or `"middle"` - and the growth goes everywhere else, so anchored
    ink stays pixel-identical: `"bottom"` grows above (`top_extra` is
    the whole growth), `"top"` grows below (zero), `"middle"` splits
    it. `ceiling` is the delivery frame height: a frame shorter than
    the floor cannot grow past itself. Both numbers stay even, so no
    codec clips half a pixel row.
    """
    floor = min(MIN_CANVAS_HEIGHT, ceiling) if ceiling > 0 else canvas_h
    if canvas_h >= floor:
        return canvas_h, 0
    grown = _ceil_even(max(floor, canvas_h))
    extra = grown - canvas_h
    if anchor == "top":
        return grown, 0
    if anchor == "middle":
        return grown, extra // 2
    return grown, extra


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

    The inverse of the one measured relation
    (`resolve_transform.pan_tilt_for_centre`), with the clip at
    Scaling=1 so the placed pixels are canvas pixels and the base
    scale is native.
    """
    pan, tilt = pan_tilt_for_centre(canvas_w, canvas_h, full_w, full_h,
                                    canvas_cx, canvas_cy)
    return {"scaling": 1, "pan": pan, "tilt": tilt}


#: How far the ink of a correctly placed overlay may sit from the
#: position intent names, in DELIVERY-FRAME pixels.
#:
#: Measured 2026-09-11 on Reel 13, whose 27 caption overlays are the
#: only set on this project verified correct against an exported still:
#: their ink bottoms land 1572..1594 against a nominal caption row of
#: 1589, a spread of 22px, because the shadow reach and each card's
#: own rounding move the ink a few pixels per card. The nominal row
#: is the INK row the captain's hand corrections put ink on - not the
#: card design bottom (1920 - the 320 safe-area bottom inset -
#: `CAPTION_LIFT_PX` = 1599), which sits 10px below it: measured ink
#: rides ~10px above the card bottom, and that offset is what the
#: corrected lift accounts for. 24 clears that spread
#: and nothing else: the defect this exists to catch misses by
#: HUNDREDS (the seventeen motion graphics carrying Tilt 5184 on a
#: 480-tall canvas draw at frame rows -576..-96, entirely off the top
#: - measured 2026-09-11, and a still of that frame finds no match
#: anywhere in it), so the band never has to be argued about.
INTENT_TOLERANCE_PX = 24.0


def canvas_screen_origin(canvas_w: float, canvas_h: float,
                         placement: dict | None,
                         full_w: int, full_h: int) -> tuple:
    """Where a STORED Pan/Tilt actually puts a canvas, in frame pixels.

    The strict inverse of `placement_for_box`, and the reason it
    exists: a stored Pan/Tilt means nothing on its own.  Pan/Tilt move
    a clip by a fraction of its OWN canvas, not of the frame - the
    shift is `value * (canvas_dim / frame_dim)` at native scale - so
    an overlay already rendered full-frame is in position at 0 while
    the same caption on a 480-tall tight canvas needs Tilt -1740 to
    reach the same screen row.  Both are honest; the Inspector number is only
    readable with the clip's own resolution beside it.

    `placement` None is a full-canvas clip: no transform, so the
    canvas sits centred at native pixels, which for a full-frame
    canvas is the frame itself.

    Returns `(x0, y0)`, the canvas's top-left in frame coordinates.
    """
    return drawn_origin(canvas_w, canvas_h, full_w, full_h,
                        float((placement or {}).get("pan") or 0.0),
                        float((placement or {}).get("tilt") or 0.0))


def ink_screen_box(canvas_w: float, canvas_h: float,
                   placement: dict | None,
                   ink_in_canvas: tuple,
                   full_w: int, full_h: int) -> tuple:
    """Where an artefact's own ink lands on screen, in frame pixels.

    `ink_in_canvas` is `(x0, y0, x1, y1)` in the artefact's OWN pixels
    - what `ink_union_of_frames` measures, or `(0, 0, w, h)` for an
    artefact whose whole canvas is the subject.

    This is the one quantity two overlays of DIFFERENT carriage can be
    compared on.  A full-frame artefact at Pan/Tilt 0 returns its ink
    unchanged; a tight artefact returns its ink translated by
    `canvas_screen_origin`.  Comparing the two clips' STORED numbers
    instead is meaningless, which is the mistake this replaces.
    """
    ox, oy = canvas_screen_origin(canvas_w, canvas_h, placement,
                                  full_w, full_h)
    x0, y0, x1, y1 = (float(v) for v in ink_in_canvas)
    return (ox + x0, oy + y0, ox + x1, oy + y1)


def verify_ink_against_intent(canvas_w: float, canvas_h: float,
                              placement: dict | None,
                              ink_in_canvas: tuple,
                              intent_box: tuple,
                              full_w: int, full_h: int,
                              tolerance_px: float = INTENT_TOLERANCE_PX,
                              ) -> str:
    """Whether a stored placement DRAWS where intent says, as a reason.

    `intent_box` is `(x0, y0, x1, y1)` in delivery-frame pixels: where
    this artefact's ink is meant to land.  Returns `""` when it does,
    else the reason, naming the error in FRAME PIXELS.

    A read-back judges "did Resolve hold the number I set", which is
    true of a number computed under a superseded relation just as it
    is of a correct one - the seventeen motion graphics stored at
    Tilt 5184 read back 5184 and were reported placed, and every one
    of them draws entirely off the top of the frame (rows -576..-96,
    measured 2026-09-11).  This judges the
    PICTURE the stored value produces against what the overlay was
    for, so a stale carriage is refused and a mixed one is not: a
    full-frame overlay at 0 and a tight overlay at -870 both verify
    against the same intent, because they draw in the same place.
    """
    got = ink_screen_box(canvas_w, canvas_h, placement, ink_in_canvas,
                         full_w, full_h)
    want = tuple(float(v) for v in intent_box)
    dx = ((got[0] + got[2]) - (want[0] + want[2])) / 2.0
    dy = ((got[1] + got[3]) - (want[1] + want[3])) / 2.0
    if abs(dx) <= tolerance_px and abs(dy) <= tolerance_px:
        return ""
    off_frame = (got[1] >= full_h or got[3] <= 0
                 or got[0] >= full_w or got[2] <= 0)
    return (f"draws at ({got[0]:.0f},{got[1]:.0f})-({got[2]:.0f},"
            f"{got[3]:.0f}) but intent is ({want[0]:.0f},{want[1]:.0f})-"
            f"({want[2]:.0f},{want[3]:.0f}): off by {dx:+.0f},{dy:+.0f}px "
            f"on a {full_w}x{full_h} frame from a {canvas_w:.0f}x"
            f"{canvas_h:.0f} canvas"
            + (" - ENTIRELY OUTSIDE THE FRAME" if off_frame else ""))


def tighten_subtitle_props(props: dict,
                           project_folder: str = "") -> TightBox | None:
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

    canvas_w = _ceil_even(max(union_w + 2 * PAD_X, float(max_width)))
    measured_h = _ceil_even(union_h + PAD_TOP + PAD_BOTTOM)

    # The floor that keeps the placement inside Resolve's rail (see
    # `MIN_CANVAS_HEIGHT`): grown away from the anchor, so the content
    # box in full-frame coordinates does not move - only the canvas
    # origin shifts, by exactly the growth above it.
    anchor = {"top": "top", "center": "middle"}.get(position, "bottom")
    canvas_h, top_extra = grow_to_minimum(measured_h, anchor, full_h)
    grown_below = canvas_h - measured_h - top_extra
    pad_top = PAD_TOP + top_extra
    pad_bottom = PAD_BOTTOM + grown_below

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

    canvas_top = content_top - pad_top
    canvas_cx = full_w / 2.0
    canvas_cy = canvas_top + canvas_h / 2.0

    placement = placement_for_box(
        canvas_w, canvas_h, canvas_cx, canvas_cy, full_w, full_h)

    tight_style = dict(style)
    tight_style["safeArea"] = {
        "top": pad_top, "right": PAD_X,
        "bottom": pad_bottom, "left": PAD_X,
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


# ─── Constant boxes: rendered at the structural bound, placed by ──────
# arithmetic. The caption path renders these natively - no probe, no
# crop, no correspondence read-off (see the module docstring).

def constant_caption_box(props: dict) -> TightBox | None:
    """The constant tight canvas for one segment's full-canvas props.

    Returns None when the segment draws nothing (no subtitles), so the
    caller keeps the full-canvas path rather than rendering an empty
    box. Raises where the style carries no geometry, exactly as the
    component refuses to place by a literal, and `TightBoxClipsInk`
    where the structural canvas would leave the delivery frame (a
    wrap width the frame cannot hold) or `TightBoxMismatch` where
    Resolve cannot hold the arithmetic placement - both are the
    caller's full-canvas fallback, never a clamped box.

    The width is the STRUCTURAL bound - `captionMaxWidth + 2*PAD_X +
    TRAILING_MARGIN_PX`, ceiled even - so no wrap the full frame drew
    can rewrap on it, whatever the segment says. The height is
    `MIN_CANVAS_HEIGHT`, the Pan/Tilt rail guard, which makes vertical
    clipping unreachable while costing nothing measurable. Placement
    is arithmetic from the same anchor the composition lays out from:
    cards are centred, so the canvas sits centred (pan 0); the card
    stack hangs from the edge `position` names, so the canvas edge
    sits one pad past that edge.
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
    position = str(style.get("position") or "bottom")

    canvas_w = _ceil_even(max_width + 2 * PAD_X + TRAILING_MARGIN_PX)
    floor = min(MIN_CANVAS_HEIGHT, full_h) if full_h > 0 else \
        MIN_CANVAS_HEIGHT
    canvas_h = _ceil_even(floor)
    refuse_canvas_larger_than_frame(
        canvas_w, canvas_h, full_w, full_h,
        f"structural caption bound ({max_width:.0f}px wrap)")

    anchor = {"top": "top", "center": "middle"}.get(position, "bottom")
    if anchor == "top":
        canvas_top = float(safe["top"]) - PAD_TOP
        pad_top, pad_bottom = PAD_TOP, canvas_h - PAD_TOP
    elif anchor == "middle":
        canvas_top = full_h / 2.0 - canvas_h / 2.0
        pad_top, pad_bottom = canvas_h // 2, canvas_h - canvas_h // 2
    else:
        canvas_top = (full_h - float(safe["bottom"])
                      + PAD_BOTTOM - canvas_h)
        pad_top, pad_bottom = canvas_h - PAD_BOTTOM, PAD_BOTTOM
    canvas_cx = full_w / 2.0
    canvas_cy = canvas_top + canvas_h / 2.0

    placement = placement_for_box(
        canvas_w, canvas_h, canvas_cx, canvas_cy, full_w, full_h)
    reason = placement_holds(placement, full_w, full_h)
    if reason:
        raise TightBoxMismatch(
            f"constant {canvas_w}x{canvas_h} caption canvas: {reason} - "
            f"this caption cannot ride the constant box, and stays "
            f"full-canvas.")

    tight_style = dict(style)
    tight_style["safeArea"] = {
        "top": pad_top, "right": PAD_X,
        "bottom": pad_bottom, "left": PAD_X,
    }
    # captionMaxWidth is DELIBERATELY unchanged: it is the wrap width,
    # and the canvas is wider than it by construction, so every card
    # wraps exactly as on the full frame.
    tight_props = dict(props)
    tight_props["width"] = canvas_w
    tight_props["height"] = canvas_h
    tight_props["style"] = tight_style

    return TightBox(
        width=canvas_w,
        height=canvas_h,
        props=tight_props,
        placement=placement,
        # The structural CONTENT bound the canvas guarantees, not a
        # measurement: the wrap width plus the trailing margin, and
        # the canvas minus the pads. Nothing in production reads
        # these off a constant box; the edge guard proves the fit.
        union_w=max_width + TRAILING_MARGIN_PX,
        union_h=float(canvas_h - PAD_TOP - PAD_BOTTOM),
        full_width=full_w,
        full_height=full_h,
    )


@dataclass(frozen=True)
class EdgeGuard:
    """What the ink-touches-edge guard saw in one rendered file.

    `touches_edge` names ink (alpha at or above the QA ink threshold)
    on the outermost pixel row or column: the render laid out past
    the canvas, so pixels are gone and the card falls back to full
    canvas. `empty` names a file that drew nothing anywhere.
    `border_max` is the highest alpha on the border, so a near miss
    says how near.
    """

    touches_edge: bool
    empty: bool
    frames: int
    border_max: int


def _borders_touch(alpha) -> tuple[bool, int]:
    """Whether a 2-D alpha plane inks its outermost row/column."""
    import numpy as np

    plane = np.asarray(alpha)
    border = np.zeros_like(plane, dtype=bool)
    border[0, :] = True
    border[-1, :] = True
    border[:, 0] = True
    border[:, -1] = True
    inked = plane[border] >= ALPHA_INK_THRESHOLD
    peak = plane[border].max(initial=0)
    return bool(inked.any()), int(peak)


def ink_touches_edge(mov_path: str, width: int, height: int) -> EdgeGuard:
    """The one guard on a natively-rendered caption file: does ink
    touch the canvas edge, read off the alpha plane.

    Decodes the mov through one ffmpeg pipe (no transient PNGs) and
    checks every frame's outermost row and column at the QA ink
    threshold. A hit means the render laid out past the canvas the
    arithmetic assumed - clipped ink no repositioning can recover -
    so the caller carries the card full canvas instead. An
    UNDECODABLE file raises `TightBoxMismatch`: absent evidence is
    not empty evidence, and a file that cannot be read cannot ship.
    """
    import subprocess

    import numpy as np

    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", mov_path,
             "-pix_fmt", "rgba", "-f", "rawvideo", "-"],
            capture_output=True, timeout=600, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TightBoxMismatch(
            f"cannot decode {mov_path} for the edge guard: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        raise TightBoxMismatch(
            f"cannot decode {mov_path} for the edge guard: "
            f"{(result.stderr or b'').decode('utf-8', 'replace')[-300:]}")
    frame_bytes = width * height * 4
    raw = result.stdout
    if len(raw) % frame_bytes != 0:
        raise TightBoxMismatch(
            f"edge guard decoded {len(raw)} bytes from {mov_path}, not "
            f"a whole number of {width}x{height} frames: the file is "
            f"not the render the box assumed.")
    frames = len(raw) // frame_bytes
    if frames == 0:
        raise TightBoxMismatch(
            f"edge guard decoded no frames from {mov_path}.")
    touches, peak, any_ink = False, 0, False
    for index in range(frames):
        plane = np.frombuffer(
            raw[index * frame_bytes:(index + 1) * frame_bytes],
            dtype=np.uint8).reshape(height, width, 4)[:, :, 3]
        if bool((plane >= ALPHA_INK_THRESHOLD).any()):
            any_ink = True
        hit, border_peak = _borders_touch(plane)
        touches = touches or hit
        peak = max(peak, border_peak)
    return EdgeGuard(touches_edge=touches, empty=not any_ink,
                     frames=frames, border_max=peak)


def ink_touches_edge_frames(frame_paths: list[str],
                            width: int, height: int) -> EdgeGuard:
    """The same guard for a PNG sequence: no decode, the frames ARE
    the artefact. An UNREADABLE frame raises, exactly as above."""
    import numpy as np
    from PIL import Image

    if not frame_paths:
        raise TightBoxMismatch(
            "edge guard was handed no frames.")
    touches, peak, any_ink = False, 0, False
    for path in frame_paths:
        try:
            with Image.open(path) as im:
                plane = np.asarray(im.convert("RGBA"))[:, :, 3]
        except OSError as exc:
            raise TightBoxMismatch(
                f"edge guard cannot decode {path}: {exc}") from exc
        if plane.shape != (height, width):
            raise TightBoxMismatch(
                f"edge guard frame {path} is "
                f"{plane.shape[1]}x{plane.shape[0]}, not the "
                f"{width}x{height} box.")
        if bool((plane >= ALPHA_INK_THRESHOLD).any()):
            any_ink = True
        hit, border_peak = _borders_touch(plane)
        touches = touches or hit
        peak = max(peak, border_peak)
    return EdgeGuard(touches_edge=touches, empty=not any_ink,
                     frames=len(frame_paths), border_max=peak)

# Resolve holds Pan/Tilt to a rail it does not report, and refuses
# silently: setting beyond returns True and reads back the clamp.
# READ OFF THE LIVE TIMELINE, 2026-09-10, Resolve 21, project "Podcast
# (field test)", both Reel 09 timelines at 1080x1920: 37 caption items
# asking for Tilt -4316..-7579 all read back exactly -3840.0, while
# every value asked at or below 3840 round-tripped to the last digit.
# The earlier "four times the timeline dimensions" figure (7680) was
# calibrated to miss it - its gate refused exactly one card (-7692.8)
# and let 37 ride onto the clamp. A NUMBER, not a formula: one
# geometry cannot tell `2x height` from a constant, so this is scoped
# to the measured 1080x1920 frame and anything else re-probes.
MEASURED_PAN_TILT_RAIL = 3840.0

#: The geometries the rail above was actually MEASURED at, keyed
#: `(timeline_w, timeline_h)`.
#:
#: One row, because one geometry is all anyone has probed. The comment
#: above says plainly that a single geometry cannot tell `2 x height`
#: from a constant, and 1080x1920 is the only frame the 37 captions were
#: read off - so 3840 is knowledge about THAT frame and a guess about
#: every other.
#:
#: `placement_limits` used to return it for any timeline it was handed,
#: which is how a shape assumption hides inside a function that takes the
#: shape as an argument: a 1920x1080 delivery would have been gated
#: against a rail nobody measured there, and if the true rail is
#: `2 x height` it is 2160 - so captions would have ridden onto a silent
#: clamp exactly as the captain's 37 did. Guessing HIGH is the dangerous
#: direction, and both readings of the evidence guess high somewhere.
#:
#: An unmeasured geometry therefore gets NO rail, and `placement_holds`
#: refuses every tight placement on it. That is not a loss of capability:
#: the refusal routes the caller to its full-canvas fallback, which
#: carries no transform at all and so cannot be clamped. Measuring a new
#: frame means probing it the way 2026-09-10 probed this one - set a
#: known Pan/Tilt, read it back, find where the round-trip stops - and
#: adding a row here.
MEASURED_RAILS: dict = {
    (1080, 1920): (MEASURED_PAN_TILT_RAIL, MEASURED_PAN_TILT_RAIL),
}


def placement_limits(timeline_w: int, timeline_h: int):
    """The largest |Pan| and |Tilt| Resolve holds, or None if unmeasured.

    Measured per GEOMETRY (`MEASURED_RAILS`). `None` means nobody has
    probed this frame, and the caller must refuse a transform rather
    than gate it against another frame's number.
    """
    return MEASURED_RAILS.get((int(timeline_w), int(timeline_h)))


def placement_holds(placement: dict | None,
                    timeline_w: int, timeline_h: int) -> str:
    """Whether Resolve can HOLD a computed placement, as a reason or "".

    Returns "" when the placement holds (None is a full-canvas clip:
    no transform, nothing to refuse), else the reason it does not -
    the Pan/Tilt Resolve would silently clamp, named with the limit.
    One predicate for every path that ships a placement: the fresh
    caption render below, the motion-graphics box
    (`library/tools/mg_tight_box.py`, which computed placements with
    no gate at all), and the caption reuse cache
    (`render_one_segment` restores sidecar placements verbatim, so a
    pre-gate or foreign-format sidecar otherwise ships unchecked).
    """
    if not placement:
        return ""
    limits = placement_limits(timeline_w, timeline_h)
    if limits is None:
        return (f"Resolve's Pan/Tilt rail has never been measured on a "
                f"{timeline_w}x{timeline_h} timeline, and the rail "
                f"measured at "
                f"{'x'.join(str(v) for v in sorted(MEASURED_RAILS)[0])} "
                f"is not evidence about this one (see "
                f"tight_box.MEASURED_RAILS). Refusing the transform "
                f"rather than placing against a guess - the caller "
                f"carries this card full canvas, which needs none.")
    pan_limit, tilt_limit = limits
    pan = placement.get("pan")
    tilt = placement.get("tilt")
    if pan is not None and abs(pan) > pan_limit:
        return (f"Pan {pan:.1f} exceeds +- {pan_limit:.0f} on a "
                f"{timeline_w}x{timeline_h} timeline (measured "
                f"2026-09-10, refused silently past it)")
    if tilt is not None and abs(tilt) > tilt_limit:
        return (f"Tilt {tilt:.1f} exceeds +- {tilt_limit:.0f} on a "
                f"{timeline_w}x{timeline_h} timeline (measured "
                f"2026-09-10, refused silently past it)")
    return ""


def restore_reused_placement(sidecar: dict, props: dict,
                             timeline_size: tuple[int, int] | None) -> TightBox:
    """A reuse-cache sidecar as a TightBox, re-gated, or refused.

    The reuse hit restores the placement the fresh render measured -
    but the gate the fresh render passed is not the gate this run
    passes: the sidecar may predate it (predictor-era files), or the
    delivery format may have changed since. So the restored placement
    is checked against TODAY's timeline before it ships, exactly as
    `resolve_placement_from_correspondence` checks a fresh one.
    Raises `TightBoxMismatch` where it no longer holds: the caller
    falls through to a fresh measured render, which carries the card
    full canvas instead. A missing or malformed sidecar raises the
    same way - never an assumed placement.

    The sidecar must also name the carriage it was computed under
    (`overlay_mode.OVERLAY_CARRIAGE`), and that carriage must be the
    current one. A placement computed under a superseded carriage is
    geometrically wrong however cleanly it reads back - Reel 28's 18
    tight captions were placed under a superseded caption row and
    draw ~220px below the declared one - so it is REFUSED here
    rather than served: the caller re-renders measured. A sidecar
    with no carriage field predates the stamp and is refused the
    same way.

    The sidecar must also name the row it was measured for
    (`safe_area`, the frame-relative insets the probe laid out from),
    and that row must be today's. The tight filename is deliberately
    row-invariant (one file per pixels - the same card on two rows
    cuts byte-identical canvases), so the filename cannot carry this
    check; without it a row change would reuse a placement measured
    for the old row, which reads back clean inside every rail and
    draws the caption on the wrong row. A stamp that is missing or
    moved is REFUSED the same way as a superseded carriage: the
    caller re-renders measured over the same file.
    """
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    try:
        union = sidecar["union"]
        box = TightBox(
            width=int(sidecar["width"]),
            height=int(sidecar["height"]),
            props={},
            placement=sidecar["placement"],
            union_w=float(union["x1"] - union["x0"]),
            union_h=float(union["y1"] - union["y0"]),
            full_width=int(props.get("width", 0)),
            full_height=int(props.get("height", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TightBoxMismatch(
            f"box sidecar is unreadable ({exc}); re-rendering measured "
            f"rather than assuming") from exc
    carriage = sidecar.get("carriage") if isinstance(sidecar, dict) else None
    if carriage != OVERLAY_CARRIAGE:
        raise TightBoxMismatch(
            f"box sidecar names carriage {carriage!r}, not the current "
            f"{OVERLAY_CARRIAGE!r}: a placement from a superseded "
            f"carriage draws off the frame however cleanly it reads "
            f"back - re-rendering measured rather than shipping it.")
    row = sidecar.get("safe_area") if isinstance(sidecar, dict) else None
    today = ((props.get("style") or {}).get("safeArea")
             if isinstance(props, dict) else None)
    if row != today:
        raise TightBoxMismatch(
            f"box sidecar was positioned for safeArea {row!r}, not "
            f"today's {today!r}: the caption row moved, so the "
            f"restored placement would draw on the old row however "
            f"cleanly it reads back - re-rendering measured over the "
            f"same file rather than shipping it.")
    if timeline_size is not None:
        reason = placement_holds(box.placement, *timeline_size)
        if reason:
            raise TightBoxMismatch(
                f"reused placement no longer holds: {reason} - "
                f"re-rendering measured rather than shipping a "
                f"clamped one.")
    return box

class TightBoxClipsInk(ValueError):
    """The measured union plus pads leaves the delivery frame.

    Clamping would cut ink, so the box refuses instead. Full-canvas QA
    keeps legit ink 2% inside the frame, so only a broken measurement
    can trip this - which is exactly when refusing is correct.
    """


def refuse_canvas_larger_than_frame(canvas_w: int, canvas_h: int,
                                    full_w: int, full_h: int,
                                    detail: str) -> None:
    """The one frame bound every tight path shares, owned here.

    A canvas wider or taller than the delivery frame refuses with
    `TightBoxClipsInk` rather than clamping ink away. The callers -
    `tighten_measured` below and `tighten_motion_graphics_props` in
    `mg_tight_box.py` - convert the refusal to a full-canvas fallback;
    what must never happen is a tight file bigger than the frame it
    draws on. `detail` names the union that needed the canvas, so the
    message says whose ink did not fit.
    """
    if canvas_w > full_w or canvas_h > full_h:
        raise TightBoxClipsInk(
            f"{detail} needs {canvas_w}x{canvas_h} on a "
            f"{full_w}x{full_h} frame: clamping would cut ink, so "
            f"there is no tight box.")


class TightBoxMismatch(ValueError):
    """The tight output is not the probe crop: cut-off text on a
    timeline. Raised, never warned past - see `verify_frames`."""


@dataclass(frozen=True)
class InkUnion:
    """What the probe render actually drew, in full-frame pixels.

    `x1`/`y1` are EXCLUSIVE, PIL `getbbox` convention. `inked_frames`
    counts the frames that drew anything, so a caller can tell a
    mostly-paused segment from a blank one.
    """

    x0: int
    y0: int
    x1: int
    y1: int
    inked_frames: int


def ink_union_of_frames(frame_paths: list[str]) -> InkUnion | None:
    """The union of drawn bounds across decoded probe frames, or None.

    None means no frame drew anything: there is nothing to bound, so
    the caller keeps the full-canvas path. An UNREADABLE frame raises
    rather than reading as blank - absent evidence is not empty
    evidence.
    """
    from PIL import Image

    x0 = y0 = None
    x1 = y1 = None
    inked = 0
    for path in frame_paths:
        try:
            with Image.open(path) as im:
                bbox = im.convert("RGBA").getchannel("A").getbbox()
        except OSError as exc:
            raise TightBoxMismatch(
                f"probe frame {path} cannot be decoded: {exc}") from exc
        if bbox is None:
            continue
        inked += 1
        left, top, right, bottom = bbox
        x0 = left if x0 is None else min(x0, left)
        y0 = top if y0 is None else min(y0, top)
        x1 = right if x1 is None else max(x1, right)
        y1 = bottom if y1 is None else max(y1, bottom)
    if x0 is None:
        return None
    return InkUnion(x0=x0, y0=y0, x1=x1, y1=y1, inked_frames=inked)


def extract_frames(mov_path: str, dest_dir: str) -> list[str]:
    """Every frame of a probe mov as PNGs, in order. Raises loudly -
    a probe that cannot be decoded fails the segment, never a box
    measured off half its frames."""
    import os
    import subprocess

    os.makedirs(dest_dir, exist_ok=True)
    pattern = os.path.join(dest_dir, "probe-%04d.png")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", mov_path, pattern],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TightBoxMismatch(
            f"cannot decode probe {mov_path}: {exc}") from exc
    if result.returncode != 0:
        raise TightBoxMismatch(
            f"cannot decode probe {mov_path}: "
            f"{(result.stderr or '').strip()[-300:]}")
    paths = sorted(name for name in os.listdir(dest_dir)
                   if name.endswith(".png"))
    if not paths:
        raise TightBoxMismatch(
            f"probe {mov_path} decoded to no frames")
    return [os.path.join(dest_dir, name) for name in paths]


def tighten_measured(props: dict, union: InkUnion,
                     container: str = "frames") -> TightBox:
    """The tight canvas SIZE for a MEASURED ink union, with provisional
    placement.

    The canvas is the union expanded by the pads - widened to
    `captionMaxWidth` where narrower, so the card wraps exactly as on
    the probe (the flex container is `width: 100%`: a narrower canvas
    would rewrap). That widening binds ONLY where the tight output is
    RE-RENDERED (the `frames` container): there the wrap can change,
    so the canvas must keep the probe's wrap basis. The `video`
    container CROPS the probe instead - the wrap is already drawn and
    cannot change - so the canvas is the ink plus pads, and the
    widening would only push a narrow off-centre caption off the
    frame's right edge (measured 2026-09-12: 25 caption segments at
    840-wide canvases with origin x in 241..371, every one leaving
    the 1080-wide frame horizontally). The canvas that would leave
    the delivery frame raises `TightBoxClipsInk` rather than clamping
    ink away.

    The placement is PROVISIONAL: the composition re-centers content
    in the narrower canvas, so the final origin is read off the two
    renders (`resolve_placement_from_correspondence`) once the tight
    output exists. `captionMaxWidth` passes through unchanged.
    """
    from library.tools.overlay_mode import CONTAINERS
    subtitles = props.get("subtitles") or []
    if not subtitles:
        return None

    style = props.get("style") or {}
    max_width = style.get("captionMaxWidth")
    if not max_width:
        raise ValueError(
            "subtitle props carry no style.captionMaxWidth - the wrap "
            "basis is unknown, so no measured box can claim the layout.")

    if container not in CONTAINERS:
        raise ValueError(
            f"unknown overlay container {container!r}; known: "
            f"{list(CONTAINERS)}. The canvas width binds on it: only "
            f"`video` crops the probe, so only it narrows to the ink.")

    full_w = int(props.get("width", 0))
    full_h = int(props.get("height", 0))

    union_w = float(union.x1 - union.x0)
    union_h = float(union.y1 - union.y0)
    if container == "video":
        canvas_w = _ceil_even(union_w + 2 * PAD_X)
    else:
        canvas_w = _ceil_even(max(union_w + 2 * PAD_X, float(max_width)))
    measured_h = _ceil_even(union_h + PAD_TOP + PAD_BOTTOM)

    # The floor that keeps the placement inside Resolve's rail (see
    # `MIN_CANVAS_HEIGHT`). Grown away from the anchor, so the union
    # sits at the same canvas offset it would have: the render draws
    # the probe layout translated, which is exactly what
    # `resolve_placement_from_correspondence` and `verify_frames`
    # prove before the box ships.
    position = str(style.get("position") or "bottom")
    anchor = {"top": "top", "center": "middle"}.get(position, "bottom")
    canvas_h, top_extra = grow_to_minimum(measured_h, anchor, full_h)
    grown_below = canvas_h - measured_h - top_extra
    pad_top = PAD_TOP + top_extra
    pad_bottom = PAD_BOTTOM + grown_below
    refuse_canvas_larger_than_frame(
        canvas_w, canvas_h, full_w, full_h,
        f"measured ink ({union.x0},{union.y0})-({union.x1},{union.y1})")

    # Provisional origin: union minus pads. Replaced by correspondence
    # once the tight render exists - see the module docstring.
    provisional = placement_for_box(
        canvas_w, canvas_h,
        union.x0 - PAD_X + canvas_w / 2.0,
        union.y0 - pad_top + canvas_h / 2.0,
        full_w, full_h)

    tight_style = dict(style)
    tight_style["safeArea"] = {
        "top": pad_top, "right": PAD_X,
        "bottom": pad_bottom, "left": PAD_X,
    }
    tight_props = dict(props)
    tight_props["width"] = canvas_w
    tight_props["height"] = canvas_h
    tight_props["style"] = tight_style

    return TightBox(
        width=canvas_w,
        height=canvas_h,
        props=tight_props,
        placement=provisional,
        union_w=union_w,
        union_h=union_h,
        full_width=full_w,
        full_height=full_h,
    )


def resolve_placement_from_correspondence(
        probe_union: InkUnion, tight_union: InkUnion,
        canvas_w: int, canvas_h: int,
        full_w: int, full_h: int,
        timeline_size: tuple[int, int] | None = None) -> dict:
    """Where the tight canvas sits, read off the two renders.

    The tight layout is the probe layout translated (measured constant
    across all 26 frames of the pilot segment), so the canvas origin is
    the probe union minus the tight union - no pads, no centring
    assumption. An origin that leaves the delivery frame, or a
    transform Resolve cannot hold (`timeline_size` bounds Pan/Tilt at
    the measured 3840 rail, 2026-09-10), raises
    `TightBoxMismatch`: the renders disagree about the layout, or the
    layout is unplaceable, and the segment is refused either way.
    """
    ox = probe_union.x0 - tight_union.x0
    oy = probe_union.y0 - tight_union.y0
    if ox < 0 or oy < 0 or ox + canvas_w > full_w \
            or oy + canvas_h > full_h:
        raise TightBoxMismatch(
            f"correspondence puts a {canvas_w}x{canvas_h} canvas at "
            f"({ox},{oy}) on a {full_w}x{full_h} frame: the tight "
            f"render laid out differently from the probe, so there is "
            f"no placement that lands it.")
    placement = placement_for_box(
        canvas_w, canvas_h,
        ox + canvas_w / 2.0, oy + canvas_h / 2.0,
        full_w, full_h)
    if timeline_size is not None:
        reason = placement_holds(placement, *timeline_size)
        if reason:
            raise TightBoxMismatch(
                f"correspondence needs Pan {placement['pan']:.1f} / "
                f"Tilt {placement['tilt']:.1f}: {reason} - this "
                f"caption cannot ride a small box, and stays "
                f"full-canvas.")
    return placement


def finalize_box_placement(box: TightBox, probe_union: InkUnion,
                           tight_union: InkUnion,
                           timeline_size: tuple[int, int] | None = None
                           ) -> TightBox:
    """The same box with correspondence placement. `verify_frames`
    proves the translation it records."""
    import dataclasses

    return dataclasses.replace(
        box,
        placement=resolve_placement_from_correspondence(
            probe_union, tight_union,
            box.width, box.height, box.full_width, box.full_height,
            timeline_size),
    )


def canvas_offset(box: TightBox) -> tuple[int, int]:
    """Where the tight canvas sits in full-frame pixels: the inverse of
    `placement_for_box`, so the file reader and the Resolve placer agree
    on one origin. Integer-exact: the placement floats round-trip."""
    ox, oy = drawn_origin(box.width, box.height,
                          box.full_width, box.full_height,
                          box.placement["pan"], box.placement["tilt"])
    return (int(round(ox)), int(round(oy)))


def verify_frames(full_paths: list[str], tight_paths: list[str],
                  box: TightBox, max_diff: int = 4,
                  min_iou: float = 0.99,
                  max_centroid: float = 1.0) -> dict:
    """The tight output IS the probe crop, frame by frame, or it raises.

    Each tight frame is pasted onto a blank full-size canvas at
    `canvas_offset` and compared against the probe frame: worst channel
    difference, ink-maks IoU and ink centroid offset, in PR 725's
    evidence shape. Any breach raises `TightBoxMismatch` - a box that
    clips ink FAILS, never warns. NumPy throughout: a 1080x1920
    per-pixel Python loop over hundreds of segments is hours.
    """
    import numpy as np
    from PIL import Image

    if len(full_paths) != len(tight_paths):
        raise TightBoxMismatch(
            f"frame count differs: {len(full_paths)} probe frames vs "
            f"{len(tight_paths)} tight frames - nothing lines up.")
    ox, oy = canvas_offset(box)
    worst_diff = 0
    worst_iou = 1.0
    worst_centroid = 0.0
    for full_path, tight_path in zip(full_paths, tight_paths):
        with Image.open(full_path) as im:
            full = np.asarray(im.convert("RGBA")).astype(np.int16)
        with Image.open(tight_path) as im:
            tight = np.asarray(im.convert("RGBA")).astype(np.int16)
        if (full.shape[1], full.shape[0]) != (box.full_width,
                                              box.full_height):
            raise TightBoxMismatch(
                f"probe frame {full_path} is "
                f"{full.shape[1]}x{full.shape[0]}, not the "
                f"{box.full_width}x{box.full_height} the box was "
                f"measured on.")
        if (tight.shape[1], tight.shape[0]) != (box.width, box.height):
            raise TightBoxMismatch(
                f"tight frame {tight_path} is "
                f"{tight.shape[1]}x{tight.shape[0]}, not the "
                f"{box.width}x{box.height} box.")
        canvas = np.zeros_like(full)
        canvas[oy:oy + box.height, ox:ox + box.width] = tight
        diff = np.abs(full - canvas).max()
        worst_diff = max(worst_diff, int(diff))
        full_ink = full[:, :, 3] >= ALPHA_INK_THRESHOLD
        tight_ink = canvas[:, :, 3] >= ALPHA_INK_THRESHOLD
        inter = np.logical_and(full_ink, tight_ink).sum()
        union = np.logical_or(full_ink, tight_ink).sum()
        iou = float(inter) / float(union) if union else 1.0
        worst_iou = min(worst_iou, iou)
        if full_ink.any() and tight_ink.any():
            full_c = np.argwhere(full_ink).mean(axis=0)
            tight_c = np.argwhere(tight_ink).mean(axis=0)
            off = float(np.sqrt(((full_c - tight_c) ** 2).sum()))
            worst_centroid = max(worst_centroid, off)
        if worst_diff > max_diff or worst_iou < min_iou \
                or worst_centroid > max_centroid:
            raise TightBoxMismatch(
                f"tight output is not the probe crop at {full_path}: "
                f"max channel diff {worst_diff} (allows {max_diff}), "
                f"ink IoU {worst_iou:.4f} (needs {min_iou}), ink "
                f"centroid offset {worst_centroid:.2f}px (allows "
                f"{max_centroid}) - cut-off text stays off the timeline.")
    return {
        "frames": len(full_paths),
        "max_diff": worst_diff,
        "min_iou": worst_iou,
        "max_centroid": worst_centroid,
    }


def verify_movs(full_mov: str, tight_mov: str, box: TightBox,
                work_dir: str) -> dict:
    """Decode both movs and `verify_frames` them. Extraction failures
    raise: an unverifiable pair is not a passing pair."""
    import os

    full_dir = os.path.join(work_dir, "verify_full")
    tight_dir = os.path.join(work_dir, "verify_tight")
    return verify_frames(
        extract_frames(full_mov, full_dir),
        extract_frames(tight_mov, tight_dir),
        box,
    )


def crop_probe_to_tight(probe_mov: str, overlay_path: str,
                        box: TightBox) -> tuple[int, int]:
    """Crop the probe render to the tight canvas, as `qtrle` RGBA with alpha.

    The tight output IS the probe crop - by construction, not by a second
    render - so the re-render rasterization difference that failed the
    verify gate as `max channel diff 5` on 90 historical cards cannot
    occur. Since 2026-09-12 there is no generation loss at all: the crop
    re-encodes as QuickTime Animation, which is LOSSLESS over the 8-bit
    RGBA a Chromium render produces, where ProRes 4444 previously cost a
    measured channel diff of 3 inside the gate's 4. The codec belongs to
    `library/tools/overlay_carriage.py`; this path only says WHERE to
    cut. Measured on this crop, five runs each: 0.164 s against ProRes's
    0.367 s, and 2.21x fewer bytes - the caption path pays nothing for
    the change because it was already re-encoding here. The crop origin
    is `canvas_offset`, the inverse of the placement the box ships, so
    the file and the Resolve transform agree on one origin by
    construction.

    The probe's audio (Remotion's silent track) is copied through
    untouched; the video rate is the probe's own (no `-r`: no fps
    conversion). Raises `TightBoxMismatch` where ffmpeg cannot deliver -
    an undecodable probe, an out-of-frame origin, a failed encode: the
    caller carries the card full canvas instead, with zero additional
    renders. A crop that cannot be cut is refused, never approximated.
    """
    import os
    import subprocess

    from library.tools.overlay_carriage import OVERLAY_ENCODE_ARGS

    ox, oy = canvas_offset(box)
    if ox < 0 or oy < 0 \
            or ox + box.width > box.full_width \
            or oy + box.height > box.full_height:
        raise TightBoxMismatch(
            f"crop of a {box.width}x{box.height} canvas at ({ox},{oy}) "
            f"leaves a {box.full_width}x{box.full_height} probe: the "
            f"measured layout is unplaceable, so there is no tight "
            f"carrying of it.")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", probe_mov,
             "-vf", f"crop={box.width}:{box.height}:{ox}:{oy}",
             *OVERLAY_ENCODE_ARGS,
             "-c:a", "copy",
             overlay_path],
            capture_output=True, text=True, encoding="utf-8", timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TightBoxMismatch(
            f"cannot crop probe {probe_mov}: {exc}") from exc
    if result.returncode != 0:
        raise TightBoxMismatch(
            f"cannot crop probe {probe_mov}: "
            f"{(result.stderr or '').strip()[-300:]}")
    if not os.path.isfile(overlay_path):
        raise TightBoxMismatch(
            f"crop of probe {probe_mov} reported success but "
            f"{overlay_path} is not on disk")
    return ox, oy

