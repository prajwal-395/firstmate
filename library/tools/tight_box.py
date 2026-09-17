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
from library.tools.resolve_transform import (
    FALLBACK_DRAW_GAIN,
    NATIVE_BASE_SCALE,
    drawn_origin,
    pan_tilt_for_centre,
)

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

# The smallest canvas height a tight box may ship WITHOUT asking the
# placement, in pixels.
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
# h=152 needed 7579 and did not - the cliff sat at h=270.4 against
# the 3840 rail read on the captain's own 1080x1920 timeline
# 2026-09-10 (probes at -3400/-3840 held exactly; -4000 and past
# returned True and pinned at -3840). That reading does not reproduce
# - the rail is the 4x law (see `MEASURED_RAILS`) - but the shape of
# the argument survives it: a smaller canvas needs a larger Tilt, and
# the floor is where the graphic's own Tilt stops. What that buys in
# bytes is SMALLER than it sounds, measured the same day on Reel 09's
# own files: full-frame captions cost 198MB for 19, the floor
# estimates 158MB - the ink dominates and transparent margins were
# always cheap, so the floor returns about a fifth of the caption
# disk, not most of it. Tight still wins on movability and render
# time; the disk is a bonus, stated honestly.
#
# Since 2026-09-13 this constant is the FALLBACK floor, not the floor:
# `grow_to_hold_rail` derives each graphic's floor from that graphic's
# own required Pan/Tilt against the measured rail (see below), and a
# graphic whose placement cannot be derived or verified lands here, as
# before. What still rides this constant directly: the structural
# caption canvas (`constant_caption_box`, 904x480 for the captain's
# wrap), and the single-zone motion-graphics box in
# `mg_tight_box.tighten_motion_graphics_props` (its caller is owned by
# an in-flight PR and cannot pass the geometry yet - the follow-on
# wires it to `grow_to_hold_rail` with a one-line call change, and the
# per-graphic numbers for that path are computed in the PR report).
# `placement_holds` and the placement read-back both survive below,
# unchanged, and stay the final judge on every path: the derived floor
# never weakens them.
#
# DERIVED, not chosen, and derived at ONE frame: it is the canvas height
# that kept the worst caption's Tilt inside the rail read on a
# 1080x1920 timeline. Both numbers it comes from are properties of that
# geometry, so this floor is knowledge about vertical delivery. It is
# left as it is rather than re-derived per frame because on any other
# frame `placement_limits` returns None and `placement_holds` refuses
# every tight placement outright (see `MEASURED_RAILS`), so the floor
# has nothing left to protect there. Re-derive it in the same pass that
# measures a second frame's rail, not before - a floor computed from an
# unmeasured rail is the guess this module refuses to make.
# (The 3840 it was derived against is history - see `MEASURED_RAILS` -
# but the constant outlives the reading as the fallback, which is why
# it keeps its value and its name.)
MIN_CANVAS_HEIGHT = 480


#: Fraction of Resolve's measured Pan/Tilt rail a tight placement must
#: stay inside BEFORE the canvas stops growing.
#:
#: Re-examined on the widening (2026-09-13), not inherited: the number
#: stays 0.10 and the argument changes, because the old argument
#: belonged to the old rail.
#:
#: RETIRED: clearing the 160-wide unprobed band (holds at 3840, pins
#: at 4000). That band was measured at the OLD clamp, which does not
#: reproduce - the 4x law was found by binary search on the exact
#: float round-trip (holds -7680 exactly, clamps past it), so the
#: unprobed band is ~1 unit and any margin clears it. Sizing 768
#: against the 160 would be arguing with a ghost.
#:
#: What 0.10 is argued from now, against the 7680 Tilt rail:
#:
#: - below, the search is exact to ~1 unit and float round-trip noise
#:   is ~1e-3 (the placer's own read-back tolerance is 0.5). 768
#:   clears both by orders of magnitude; nothing finer buys safety,
#:   it only buys canvas.
#: - above, the margin IS canvas height on every graphic, and 10% is
#:   what turns the widening into the drops the captain approved it
#:   for (top-anchored lockups 388 -> 218). A larger margin eats that
#:   win with no measurement demanding it: the rail is probed densely
#:   (holds at -6000, -7000, -7680 exactly), so the target 6912 sits
#:   inside verified territory, not beyond it.
#: - outside the margin's scope entirely: the human-interference
#:   hypothesis (UNVERIFIED, see the rail history above). If
#:   concurrent human use can move the clamp, no fixed fraction
#:   covers it, 10% or otherwise - the guard there is knowing when
#:   the captain is in the app, not headroom. The margin is sized
#:   against measurement uncertainty, and says so.
RAIL_HEADROOM_FRACTION = 0.10


def _split_growth(extra: int, anchor: str) -> int:
    """The growth that goes ABOVE the ink, given `extra` new rows.

    The same contract `grow_to_minimum` grows under, spelled once so
    the derived floor (`grow_to_hold_rail`) cannot drift from it:
    `"top"` grows below (zero above), `"middle"` splits it,
    everything else (`"bottom"`) grows above. The ink hangs from the
    anchor edge, so it stays pixel-identical - which is why the
    correspondence read-off and `verify_frames` still pass on a grown
    canvas.
    """
    if anchor == "top":
        return 0
    if anchor == "middle":
        return extra // 2
    return extra


def grow_to_hold_rail(canvas_w: float, measured_h: int, anchor: str,
                      centre_x: float, centre_y: float,
                      full_w: int, full_h: int,
                      ceiling: int,
                      draw_gain: float = FALLBACK_DRAW_GAIN
                      ) -> tuple[int, int]:
    """Grow a measured canvas only as far as its placement needs.

    Returns `(new_h, top_extra)` in the same contract as
    `grow_to_minimum`: `new_h` is even, and `top_extra` rows of the
    growth go above the ink so anchored ink does not move.

    `(centre_x, centre_y)` is the canvas centre in full-frame pixels
    AT `measured_h` - the true tight size, before any growth - and the
    growth is applied away from `anchor` exactly as `grow_to_minimum`
    applies it, so the centre at a grown height is known analytically:
    the origin shifts up by the above-ink share of the growth while
    the height adds half-rows below it. No fixed point, no
    chicken-and-egg: Tilt(h) falls monotonically as h grows (the
    centre drifts toward frame centre AND the units-per-pixel shrink),
    so one upward pass finds the smallest even height whose Tilt sits
    `RAIL_HEADROOM_FRACTION` inside the measured rail.

    Anything that cannot be derived falls back to `grow_to_minimum`
    (the constant floor - refusing to shrink is always safe):

    - an unmeasured frame (`placement_limits` None): shrinking on a
      guessed rail is the guess this module refuses to make;
    - a Pan the rail cannot hold: height growth moves Tilt only, so a
      width-driven refusal stays refused downstream, as today;
    - growth past `ceiling` (the delivery frame height): the canvas
      cannot outgrow the frame it draws on.

    The placement this derives from is PROVISIONAL - the position the
    box is computed at before the render exists. The correspondence
    gate (`resolve_placement_from_correspondence`) and
    `placement_holds` stay the final judge on what ships: a graphic
    whose final placement the rail cannot hold still falls back to
    full canvas, exactly as today. The floor only ever SHRINKS the
    canvas toward the ink; it never approves a placement.

    The floor is a POLICY, not the mechanism: it answers "how tall
    must this canvas be to place", and the callers ask it in one
    place each. If size and position turn out to be separable in
    Resolve (captain's Text+ probe, 2026-09-13 - a separate
    investigation), this function becomes the identity - return the
    measured height, no floor at all - with no change to the call
    path. Nothing here bakes grow-to-fit deeper than the constant it
    replaces.
    """
    if ceiling <= 0:
        return measured_h, 0
    limits = placement_limits(full_w, full_h)
    if limits is None:
        return grow_to_minimum(measured_h, anchor, ceiling)
    pan_limit, tilt_limit = limits
    pan = pan_tilt_for_centre(
        canvas_w, measured_h, full_w, full_h, centre_x, centre_y,
        NATIVE_BASE_SCALE, draw_gain)[0]
    if abs(pan) > pan_limit:
        return grow_to_minimum(measured_h, anchor, ceiling)
    origin_y = centre_y - measured_h / 2.0
    target = tilt_limit * (1.0 - RAIL_HEADROOM_FRACTION)
    grown_h = _ceil_even(max(measured_h, 0))
    while grown_h <= ceiling:
        top_extra = _split_growth(grown_h - measured_h, anchor)
        cy = origin_y - top_extra + grown_h / 2.0
        _, tilt = pan_tilt_for_centre(canvas_w, grown_h, full_w,
                                      full_h, centre_x, cy,
                                      NATIVE_BASE_SCALE, draw_gain)
        if abs(tilt) <= target:
            return grown_h, top_extra
        grown_h += 2
    return grow_to_minimum(measured_h, anchor, ceiling)


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
    #: The draw gain the placement was computed under
    #: (`resolve_transform.FALLBACK_DRAW_GAIN`, and that module for how
    #: it was measured and when it must be re-measured). `canvas_offset`
    #: inverts the placement through this gain, so a box read back off
    #: a sidecar stamped under another gain would paste its file in the
    #: wrong place - `restore_reused_placement` refuses such a sidecar
    #: rather than serving it. Tests pinning the 2026-09-11
    #: calibration pass 1.0 explicitly.
    gain: float = FALLBACK_DRAW_GAIN


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
                      full_w: int, full_h: int,
                      draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    """The SetProperty values putting a native-pixel clip's centre where
    the full-frame coordinates say.

    The inverse of the one measured relation
    (`resolve_transform.pan_tilt_for_centre`), with the clip at
    Scaling=1 so the placed pixels are canvas pixels and the base
    scale is native. `draw_gain` is the renderer's measured draw gain
    (see `resolve_transform`); callers pinning the 2026-09-11
    calibration pass 1.0 explicitly.
    """
    pan, tilt = pan_tilt_for_centre(canvas_w, canvas_h, full_w, full_h,
                                    canvas_cx, canvas_cy,
                                    NATIVE_BASE_SCALE, draw_gain)
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
                         full_w: int, full_h: int,
                         draw_gain: float = FALLBACK_DRAW_GAIN) -> tuple:
    """Where a STORED Pan/Tilt actually puts a canvas, in frame pixels.

    The strict inverse of `placement_for_box`, and the reason it
    exists: a stored Pan/Tilt means nothing on its own.  Pan/Tilt move
    a clip by a fraction of its OWN canvas, not of the frame - the
    shift is `value * (canvas_dim / frame_dim)` at native scale, times
    the renderer's measured draw gain (`resolve_transform`) - so an
    overlay already rendered full-frame is in position at 0 while
    the same caption on a 480-tall tight canvas needs Tilt -870 to
    reach the row Tilt -1740 reached under the 2026-09-11 gain.
    Both are honest; the Inspector number is only
    readable with the clip's own resolution beside it.

    `placement` None is a full-canvas clip: no transform, so the
    canvas sits centred at native pixels, which for a full-frame
    canvas is the frame itself.

    Returns `(x0, y0)`, the canvas's top-left in frame coordinates.
    """
    return drawn_origin(canvas_w, canvas_h, full_w, full_h,
                        float((placement or {}).get("pan") or 0.0),
                        float((placement or {}).get("tilt") or 0.0),
                        NATIVE_BASE_SCALE, None, None, draw_gain)


def ink_screen_box(canvas_w: float, canvas_h: float,
                   placement: dict | None,
                   ink_in_canvas: tuple,
                   full_w: int, full_h: int,
                   draw_gain: float = FALLBACK_DRAW_GAIN) -> tuple:
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
                                  full_w, full_h, draw_gain)
    x0, y0, x1, y1 = (float(v) for v in ink_in_canvas)
    return (ox + x0, oy + y0, ox + x1, oy + y1)


def verify_ink_against_intent(canvas_w: float, canvas_h: float,
                              placement: dict | None,
                              ink_in_canvas: tuple,
                              intent_box: tuple,
                              full_w: int, full_h: int,
                              tolerance_px: float = INTENT_TOLERANCE_PX,
                              draw_gain: float = FALLBACK_DRAW_GAIN,
                              ) -> str:
    """Whether a stored placement DRAWS where intent says, as a reason.

    `intent_box` is `(x0, y0, x1, y1)` in delivery-frame pixels: where
    this artefact's ink is meant to land.  Returns `""` when it does,
    else the reason, naming the error in FRAME PIXELS.

    A read-back judges "did Resolve hold the number I set", which is
    true of a number computed under a superseded relation just as it
    is of a correct one - the seventeen motion graphics stored at
    Tilt 5184 read back 5184 and were reported placed, and every one
    of them drew entirely off the top of the frame (rows -576..-96
    under the 2026-09-11 gain, measured then; further off under
    today's).  This judges the
    PICTURE the stored value produces against what the overlay was
    for, so a stale carriage is refused and a mixed one is not: a
    full-frame overlay at 0 and a tight overlay at -870 both verify
    against the same intent, because they draw in the same place
    (under the 2026-09-11 gain the tight number was -1740 for the
    same row - see `resolve_transform`).
    """
    got = ink_screen_box(canvas_w, canvas_h, placement, ink_in_canvas,
                         full_w, full_h, draw_gain)
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
                           project_folder: str = "",
                           draw_gain: float = FALLBACK_DRAW_GAIN
                           ) -> TightBox | None:
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

    # The floor this graphic's own placement needs
    # (`grow_to_hold_rail`), not the constant: at the measured height
    # the canvas top sits one pad past the content edge, so the centre
    # then is known and the growth goes away from the anchor, leaving
    # the content box above exactly where it is - only the canvas
    # origin shifts, by exactly the growth above it.
    anchor = {"top": "top", "center": "middle"}.get(position, "bottom")
    canvas_h, top_extra = grow_to_hold_rail(
        canvas_w, measured_h, anchor, full_w / 2.0,
        content_top - PAD_TOP + measured_h / 2.0,
        full_w, full_h, full_h, draw_gain)
    grown_below = canvas_h - measured_h - top_extra
    pad_top = PAD_TOP + top_extra
    pad_bottom = PAD_BOTTOM + grown_below

    canvas_top = content_top - pad_top
    canvas_cx = full_w / 2.0
    canvas_cy = canvas_top + canvas_h / 2.0

    placement = placement_for_box(
        canvas_w, canvas_h, canvas_cx, canvas_cy, full_w, full_h,
        draw_gain)

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
        gain=draw_gain,
    )


# ─── Constant boxes: rendered at the structural bound, placed by ──────
# arithmetic. The caption path renders these natively - no probe, no
# crop, no correspondence read-off (see the module docstring).

def constant_caption_box(props: dict,
                         draw_gain: float = FALLBACK_DRAW_GAIN
                         ) -> TightBox | None:
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
        canvas_w, canvas_h, canvas_cx, canvas_cy, full_w, full_h,
        draw_gain)
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
        gain=draw_gain,
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
# The rail is `4 x timeline width` on Pan and `4 x timeline height`
# on Tilt - measured 2026-09-13 on Resolve Studio 21.1.0.14 by binary
# search on the exact float round-trip, in a SCRATCH project, at three
# geometries and two clip sizes, and it does not depend on the clip's
# own size or move with the item's zoom (probed at 0.25, 0.5, 1, 2 and
# 4x):
#
#:     timeline      clip        Pan rail   Tilt rail
#:     1080x1920     904x480         4320        7680
#:     1080x1920     1080x1920       4320        7680
#:     1920x1080     904x480         7680        4320
#:     1920x1080     1080x1920       7680        4320
#:     3840x2160     904x480        15360        8640
#:     3840x2160     1080x1920      15360        8640
#
# Reproduced on the captain's own project against its own caption
# artefact, on the RETIRED Reel 26 at 1080x1920: Pan 4320, Tilt 7680.
# The captain's hand probe the same day confirms it: bounds are 4x the
# dimension, flipping with orientation (1080x1920 gives 4320x7680,
# 1920x1080 gives 7680x4320). A probe under the floor PR (2026-09-13)
# set Tilt -5000 on a 1080x1920 scratch timeline and read back -5000.0
# exactly - no pin where the old row said the clamp was.
#
# HISTORY, kept because deleting it would invite re-derivation: the
# vertical row read (3840, 3840) before 2026-09-13, and 37 of the
# captain's captions rode onto that clamp (read off the live timeline
# 2026-09-10, Resolve 21, project "Podcast (field test)", both Reel 09
# timelines at 1080x1920: items asking Tilt -4316..-7579 all read back
# exactly -3840.0, while values at or below 3840 round-tripped). The
# earlier "four times the timeline dimensions" figure (7680) was
# calibrated to miss it - its gate refused exactly one card (-7692.8)
# and let 37 ride onto the clamp. The captain widened the row back to
# the measured 4x law on 2026-09-13 ("yeah widen it").
#
# THE ANOMALY, recorded and UNVERIFIED: nothing measurable explains
# why 2026-09-10 read 3840 on the same geometry that reads 7680
# before and since - not the clip size, not the zoom, not the
# project-level resolution (3840x2160, while the reel timelines carry
# custom 1080x1920 and the rails follow the CUSTOM settings). The
# captain's own leading hypothesis, 2026-09-13: "that one failure
# might just be because i might've been messing around in davinci as
# well when that test went off". Nobody has reproduced a
# human-induced clamp, so this stays a hypothesis, and it matters
# beyond this file: if concurrent human use can move the clamp, no
# rail number is safe on its own and the real guard is knowing when
# the captain is in the app (the lease in `resolve_lock.py`, and the
# window discipline around it) - not headroom.
#: The geometries the rail above was actually MEASURED at, keyed
#: `(timeline_w, timeline_h)` to `(Pan rail, Tilt rail)`.
#:
#: A NUMBER per geometry, not a formula: one geometry cannot tell
#: `4 x width` from a constant, so each row is knowledge about THAT
#: frame and a guess about every other - which is why an unmeasured
#: geometry gets NO row and `placement_holds` refuses every tight
#: placement on it. That refusal routes the caller to its full-canvas
#: fallback, which carries no transform at all and so cannot be
#: clamped. Measuring a new frame means probing it the way 2026-09-13
#: probed these - set a value, read it back, find where the round
#: trip stops - and adding a row here, stamped with the build below.
#:
#: Guessing HIGH is the dangerous direction, and both readings of the
#: evidence guess high somewhere: the 4x law is itself one campaign on
#: one build, and the 2026-09-10 incident is the standing proof that a
#: confident number can be wrong.
MEASURED_RAILS: dict = {
    # Pan = 4 x width, Tilt = 4 x height. Captain's call 2026-09-13
    # ("yeah widen it"): was (3840.0, 3840.0), see the history above.
    (1080, 1920): (4320.0, 7680.0),
    (1920, 1080): (7680.0, 4320.0),
}

#: The Resolve build each rail row above was measured on, keyed
#: `(timeline_w, timeline_h)`.
#:
#: The captain's condition on the widening (2026-09-13): do not swap
#: one hardcoded number for another that can go stale the same way.
#: Both failures in this file's history were a constant standing in
#: for a measurement. The stamp is the cheap half of that condition -
#: a row whose build is unknown is a row nobody may trust, and the
#: follow-on (a preflight that reads the live build with
#: `resolve.GetVersionString` - proven in
#: `step_6_01_render/probe_resolve_capabilities.py` - and refuses the
#: row on mismatch) is a small wired diff exactly because the stamp
#: is data, not prose.
#:
#: What is NOT here, and why: no runtime probe, and no refusal on an
#: unknown build inside this change. The floor is derived where there
#: is usually no Resolve to ask - render steps run headless, and
#: refusing on an unknown build would turn every headless run
#: full-canvas, which is the feature deleted, not guarded. Probing at
#: render time would need live Resolve plus the lease plus scratch
#: writes on every run for a number that moves, at most, per build.
#: Both were judged disproportionate inside this change; the stamp
#: keeps the follow-on honest instead of pretending the number is
#: timeless.
MEASURED_RAIL_BUILDS: dict = {
    (1080, 1920): "21.1.0.14",
    (1920, 1080): "21.1.0.14",
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
                f"{timeline_w}x{timeline_h} timeline (4x-law rail, "
                f"binary-searched 2026-09-13 - see "
                f"tight_box.MEASURED_RAILS; refused silently past it)")
    if tilt is not None and abs(tilt) > tilt_limit:
        return (f"Tilt {tilt:.1f} exceeds +- {tilt_limit:.0f} on a "
                f"{timeline_w}x{timeline_h} timeline (4x-law rail, "
                f"binary-searched 2026-09-13 - see "
                f"tight_box.MEASURED_RAILS; refused silently past it)")
    return ""


def restore_reused_placement(sidecar: dict, props: dict,
                             timeline_size: tuple[int, int] | None,
                             draw_gain: float = FALLBACK_DRAW_GAIN
                             ) -> TightBox:
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

    The sidecar must also name the draw gain its placement was
    computed under (`resolve_transform.FALLBACK_DRAW_GAIN`), and that
    gain must be today's. A placement computed under another gain
    draws on another row however cleanly it reads back - every
    pre-2026-09-17 sidecar carries twice the Tilt its artefact needs
    under today's renderer - so it is REFUSED last, after the
    carriage, row and rail gates above: the caller re-renders
    measured over the same file.
    """
    from library.tools.overlay_mode import OVERLAY_CARRIAGE

    try:
        union = sidecar["union"]
        stamped_gain = float(sidecar.get("draw_gain", 1.0))
        box = TightBox(
            width=int(sidecar["width"]),
            height=int(sidecar["height"]),
            props={},
            placement=sidecar["placement"],
            union_w=float(union["x1"] - union["x0"]),
            union_h=float(union["y1"] - union["y0"]),
            full_width=int(props.get("width", 0)),
            full_height=int(props.get("height", 0)),
            gain=stamped_gain,
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
    if stamped_gain != draw_gain:
        raise TightBoxMismatch(
            f"box sidecar was placed under draw gain {stamped_gain}, "
            f"not today's {draw_gain}: the renderer draws each unit "
            f"by a different amount than when that placement was "
            f"computed, so serving it would land the canvas off its "
            f"row however cleanly it reads back - re-rendering "
            f"measured over the same file rather than shipping it.")
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
                     container: str = "frames",
                     draw_gain: float = FALLBACK_DRAW_GAIN) -> TightBox:
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

    # The floor this graphic's own placement needs
    # (`grow_to_hold_rail`), not the constant. Grown away from the
    # anchor, so the union sits at the same canvas offset it would
    # have: the render draws the probe layout translated, which is
    # exactly what `resolve_placement_from_correspondence` and
    # `verify_frames` prove before the box ships. The centre passed
    # down is the provisional one at the measured height - union minus
    # pads - which the placement below recomputes at the grown height.
    position = str(style.get("position") or "bottom")
    anchor = {"top": "top", "center": "middle"}.get(position, "bottom")
    canvas_h, top_extra = grow_to_hold_rail(
        canvas_w, measured_h, anchor,
        union.x0 - PAD_X + canvas_w / 2.0,
        union.y0 - PAD_TOP + measured_h / 2.0,
        full_w, full_h, full_h, draw_gain)
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
        full_w, full_h, draw_gain)

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
        gain=draw_gain,
    )


def resolve_placement_from_correspondence(
        probe_union: InkUnion, tight_union: InkUnion,
        canvas_w: int, canvas_h: int,
        full_w: int, full_h: int,
        timeline_size: tuple[int, int] | None = None,
        draw_gain: float = FALLBACK_DRAW_GAIN) -> dict:
    """Where the tight canvas sits, read off the two renders.

    The tight layout is the probe layout translated (measured constant
    across all 26 frames of the pilot segment), so the canvas origin is
    the probe union minus the tight union - no pads, no centring
    assumption. An origin that leaves the delivery frame, or a
    transform Resolve cannot hold (`timeline_size` bounds Pan/Tilt at
    the measured 4x-law rail - see `MEASURED_RAILS`), raises
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
        full_w, full_h, draw_gain)
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
                           timeline_size: tuple[int, int] | None = None,
                           draw_gain: float = FALLBACK_DRAW_GAIN
                           ) -> TightBox:
    """The same box with correspondence placement. `verify_frames`
    proves the translation it records."""
    import dataclasses

    return dataclasses.replace(
        box,
        placement=resolve_placement_from_correspondence(
            probe_union, tight_union,
            box.width, box.height, box.full_width, box.full_height,
            timeline_size, draw_gain),
        gain=draw_gain,
    )


def canvas_offset(box: TightBox) -> tuple[int, int]:
    """Where the tight canvas sits in full-frame pixels: the inverse of
    `placement_for_box`, so the file reader and the Resolve placer agree
    on one origin. Integer-exact: the placement floats round-trip.

    Inverted through the box's own gain: a placement computed under
    another gain pastes its file in the wrong place however cleanly it
    reads back, which is why the box carries the gain it was computed
    under and the restore path refuses a sidecar stamped with another.
    """
    ox, oy = drawn_origin(box.width, box.height,
                          box.full_width, box.full_height,
                          box.placement["pan"], box.placement["tilt"],
                          NATIVE_BASE_SCALE, None, None, box.gain)
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

