# `library.tools.tight_box` - the history behind its contract

This is the module docstring of `library/tools/tight_box.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The drawn bounds of a caption card, and where they land in Resolve.

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
```

## Feasibility (2026-09-08)

Moved from the module docstring of `tests/unit/captions/test_tight_box.py`.

```text
A caption card is mostly transparent canvas. Render only the ink.

Today every subtitle segment renders at the full delivery frame
(1080x1920) - two million pixels per frame to draw a caption occupying
a few percent of them. That is slow to render, heavy on disk, and fixes
the position at render time, so repositioning means re-rendering.

A tight box renders only the drawn bounds - the union of the segment's
cards, bottom-anchored exactly as the composition lays them out - and
lands on the Resolve timeline as a small clip placed at an offset
(`Scaling=1` for native pixels, then Pan/Tilt). Smaller, faster, and
MOVABLE after the fact.

Feasibility, measured 2026-09-08 on a scratch Resolve project (never
the captain's):
- `ImportMedia` takes a smaller-than-timeline ProRes mov and places it.
- Per-clip `Scaling=1` draws it at native pixels, centred. 0 and 2 fit
  the image to the frame; 3 stretches it full-frame.
- Pan/Tilt move it in measured output pixels: shift_x = Pan *
  (placed_W / timeline_W), shift_y = -Tilt * (placed_H / timeline_H).
- `ImportMedia` of N PNG frames yields ONE pool item whose File Path
  reads `seq_[0001-0005].png`, and it places with a frame duration.
- `timeline.CreateCompoundClip` exists and returns an object.

So both halves of the captain's note are real: the bounds are knowable
at plan time (the fitter already measures every word in pixels), and
Resolve accepts frames directly.
```
