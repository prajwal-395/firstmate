# `library.tools.mg_tight_box` - the history behind its contract

This is the module docstring of `library/tools/mg_tight_box.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The drawn union of a motion-graphics segment, and where it lands.

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
 a run declaring `uppercase` is measured uppercased with its 3px letter
 spacing because that is what the render draws - anything else is
 measured in the case the plan stated it. The staged-rule lower third
 (`data.construction == "staged_rule"`) is measured the same way off
 `StagedLowerThird`: the name and title runs in their own roles, the
 rule a twelfth of the display size thick a seventh of it below the
 name - every number the construction derives, derived here too.

Where the estimate may be wrong, and why that is safe
-----------------------------------------------------
Heights are sums of line boxes and widths are single-line measurements
- which holds only where no arm constrains the width. A
centre-horizontal stack DOES: it sets BOTH left and right insets
(`anchorStyle` in the composition), so its container is the canvas
minus the insets and copy wraps to the canvas. On a small canvas the
copy wraps where the full frame did not - a stat_callout drew 366x225
tight where the full frame drew 540x165, a quote_card 856x193 against
986x132 (measured 2026-09-16; the crop-probe path below exists because
of those two). Chromium shaping and PIL shaping differ by a few
pixels either way. Both errors land in `MG_PAD`, which at 48px also
clears the largest entrance/exit motion (`slide` travels 40px), the
12px text-shadow blur, and the glitch jitter with its drop-shadow
chain. A box with slack is a smaller win; a box that clips ink is a
defect. The estimate errs toward slack.

How the tight render stays the SAME DRAWING (captain 2026-09-21)
---------------------------------------------------------------
Fix the wrapping at tight size: the copy must wrap to the width it
would have had at full frame, while the canvas stays tight. Two
halves, one in each language:

- Python (`_tighten_impl`) floors the canvas: where a centre stack is
  present the canvas spans the full-frame usable width plus the pads,
  so the container is never NARROWER than at full frame. The canvas
  origin stays pinned to the union edge, so side-anchored ink in a
  mixed segment does not move.
- the composition caps the container: a centre stack carries
  `maxWidth: layoutWidth` (the full-frame usable width, set only on
  tight props), so the container is never WIDER than at full frame
  either - which is what an over-wide predicted union would
  otherwise draw. Full-frame props carry no `layoutWidth`, so the
  full render is pixel-identical.

`layoutWidth` present means the canvas was floored, and equals the
full-frame usable width. Side-anchored stacks set one inset and size
to their content, so the union-sized canvas already holds their
layout and they carry no cap. Elements of fixed geometry
(`FIXED_GEOMETRY`) never consult the container width - explicit
sizes, clamped bodies - and do not trigger the floor on their own.

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
  small canvas IS the placement - and stay tight. A top+bottom mix
  carrying centre-anchored copy never reaches this refusal through
  the planner: `separable_groups` splits it into two tight rows
  first, because the layout-width floor would span its tall union at
  the full-frame width and trip the coverage backstop below. A
  combined one handed here directly still refuses there, as the
  backstop.
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
```
