# FFrames vs HyperFrames - five-composition side-by-side

Each of the five standard compositions rendered from the SAME props
(`repro/props.py`) at 1080x1920, 30 fps, 60 frames, through Ren's own
HyperFrames path (`hyperframes_render.render_one_card_frames`, pinned
`hyperframes@0.8.70`) and through the FFrames path
(`fframes_render.render_one_card_frames`: props resolved -> IR lowering ->
scene.json -> `ren-graphics-fframes` on Metal). Both sides are the
adapted frames the carriage receives (premultiplied overlays; FullFrameCard
flattened over its `#1A1A2E` ground). Renders ran one at a time under the
verify lock.

Each image: HyperFrames left, FFrames right, frame 45 (settled), cropped
to the union of both frames' ink and composited over the same mid-grey so
dark outlines and shadows stay visible.

## What is measured, and why not mean pixel error

The first version of this page reported mean absolute error over the
whole frame. A caption frame is ~98% transparent, so that number stayed
under 1 while the words were on the wrong lines in the wrong order. Every
check below is taken inside the text and is built to fail on the defects
that review found (`repro/metrics.py`):

| check | what it catches |
|---|---|
| `words` | glyph boxes per word in reading order (lines from the row profile, words from column gaps > 0.2 of the line's ink height); count and order must match one to one, `offset` is the worst centre offset |
| `word IoU` | glyph-mask IoU inside each HyperFrames word box: same word, same place, same weight |
| `ink IoU` | IoU of the alpha > 128 masks |
| `coverage` | bright fill pixels / text bbox (glyph weight) |
| `outline` | opaque dark pixels touching the text (alpha > 200, luma < 60) |
| `shadow` | soft dark pixels (8 < alpha <= 200, luma < 60) |
| `accent` | fill pixels in the active-word colour |
| `opacity` | mean alpha FFrames / HyperFrames over the union (catches fades) |
| `premult` | every channel <= alpha in the FFrames frame |

## This head (frame 45; frame 3, mid-entrance, in `metrics.json`)

| composition | words HF/FF | order | worst offset | word IoU min / mean | ink IoU | outline HF/FF | shadow HF/FF | accent HF/FF | opacity | premult |
|---|---|---|---|---|---|---|---|---|---|---|
| SubtitleOverlay | 9 / 9 | match | 0.5px | 0.87 / 0.92 | 0.97 | 26010 / 25326 | 41732 / 43626 | 2430 / 2489 | 1.00 | yes |
| TimedTextOverlay | 5 / 5 | match | 0.5px | 0.97 / 0.98 | 0.97 | 0 / 0 | 22527 / 24136 | - | 1.01 | yes |
| FullFrameCard | 10 / 10 | match | 0.5px | 0.90 / 0.95 | 0.98 | (none drawn) | (none drawn) | - | 0.99 | opaque |
| MotionGraphics | 6 / 6 | match | 1.5px | 0.82 / 0.85 | 0.87 | 0 / 0 | 16477 / 17525 | - | 1.01 | yes |
| PostHeader | 17 / 16 | see below | - | 0.80 / 0.93 | 0.91 | 0 / 0 | 0 / 0 | - | 1.00 | yes |

Frame 3 (inside every entrance ramp): opacity ratio 1.00-1.01 for all
five, and the subtitle's first word carries the accent and the entry scale
on both sides (word IoU 0.90 / 0.92).

## The previous head (cd85e710), same props, same checks

`metrics_previous_head.json` - the negative control: the checks fail it on
every composition.

| composition | words HF/FF | word IoU mean | ink IoU | outline FF | shadow FF | accent FF |
|---|---|---|---|---|---|---|
| SubtitleOverlay | 9 / 6 | 0.07 | 0.08 | 0 | 0 | 0 |
| TimedTextOverlay | 5 / 5 | 0.00 | 0.00 | - | 0 | - |
| FullFrameCard | 10 / 7 | 0.00 | 0.00 | - | - | - |
| MotionGraphics | 6 / 5 | 0.00 | 0.00 | - | 0 | - |
| PostHeader | 17 / 1 | 0.05 | 0.16 | - | - | - |

## What changed to get here

- **Face and weight.** usvg cannot apply a variable font's `wght` axis
  and drew Montserrat-Variable's default instance, Thin 100, for every
  weight. The adapter now cuts a static instance per (file, weight) with
  fontTools under a unique family name (`library/graphics_ir/fonts.py`),
  and resolves the face the HyperFrames template would load: the
  project's `fontFile`, else the bundled Montserrat. No system fallback.
- **Layout.** Every node was drawn twice (once as a root, once through
  its group), which is the "world above hello" picture; only roots draw
  now. The lowering lays text out with each template's own CSS numbers -
  the caption's flex-wrap of inline-block words with their 0.24em
  margin, TimedText's `max-content`/`pre-line` box with `line-height:
  normal`, the card's 20px column, the MotionGraphics anchor stack, the
  PostHeader account row - measured with kerned advances from the same
  static face. Bounds are CSS boxes; the renderer puts the baseline where
  CSS does (half-leading, typo or hhea ascent).
- **Style carried from the IR.** `text_shadow` (CSS list: the caption's
  8-direction outline plus drop shadow, the 0 4px 12px shadow),
  `text_transform`, `letter_spacing`, the active-word `color` animation,
  shape `corner_radius`, the badge's two paths, and transforms pivoting on
  the box centre (the caption's 0.95 -> 1 entry scale).
- **Images.** FFrames resolves `<image href>` only against preloaded
  media, so the avatar never drew; image nodes are now decoded and
  preloaded.

## Differences that remain

- **PostHeader word count 17 / 16** is a 1px kerning difference, not a
  layout one: FFrames sets "for" 1px closer to "frame" in the hook, so the
  6px gap that splits them in HyperFrames is 5px and the segmenter joins
  them. Every word box still overlaps at IoU >= 0.80.
- **MotionGraphics "TITLE"** (uppercase, 3px tracking) sits 1px lower and
  is 1-2px wider on the right; the other runs match to 1px.
- **Outline and shadow** pixel counts differ by 2-7%: usvg's Gaussian
  blur and antialiasing are not Chrome's. The outline is drawn the way the
  template draws it (eight zero-blur offset shadows), not as a stroke.
- **FullFrameCard** HyperFrames shows a few hundred dark fringe pixels
  where antialiased glyph edges meet the dark ground; the template draws
  no outline or shadow there, and neither engine does.
- Not covered by these props: MotionGraphics arms other than the text
  arms (`progress_bar`, `frame_accents` and the data arms still lower to
  nothing), `clip` (mask entrance) which the reference evaluator steps at
  half-progress where GSAP interpolates, `flip`'s 3D rotation, and an SVG
  avatar (the preloader decodes raster images only).

## Real-use arms: list_build and stat_callout (FF vs Remotion)

Census G2's first lane (`lower_motion_graphics` now draws `list_build`
and `stat_callout`): the same checks as above, but the reference is
**Remotion** (the production engine for these arms) on **real
geo-podcast props**, not the synthetic five. Props are the project's own
`4_06_render_motion_graphics/motion_graphics/mg_*_props.json` files,
copied byte-identical: `list_build` (4 items, middle_left, 410x480,
96f @ 23.976fps, fade/fade) and `stat_callout` ("20%" + label,
top_centre, 852x480, 84f, fade/fade). FFrames through
`fframes_render.render_one_card_frames`; Remotion through
`remotion render src/index.ts MotionGraphics --sequence` (system Chrome,
output under /tmp per the dotted-path quirk). SSIM on both frames
composited over mid-grey; ink IoU on alpha > 128 masks
(`metrics_real_arms.json`, `MG_real_arms_curves.png`).

Each image: Remotion left, FFrames right, settled frame, cropped to the
union of both frames' ink and composited over the same mid-grey.

| arm (frame) | ink IoU | SSIM | ink FF/RM | opacity FF/RM | premult FF |
|---|---|---|---|---|---|
| list_build f80 (settled) | 0.80 | 0.970 | 9545 / 10611 | 0.90 | yes |
| list_build f6 (element fade) | 0.86 | 0.998 | 1513 / 1748 | 0.91 | yes |
| list_build f25 (item 1 mid-reveal) | 0.81 | 0.993 | 3375 / 3862 | 0.91 | yes |
| list_build f50 (item 2 mid-reveal) | 0.85 | 0.986 | 7060 / 7748 | 0.90 | yes |
| stat_callout f70 (settled) | 0.80 | 0.951 | 26855 / 29473 | 0.93 | yes |
| stat_callout f4 (element fade, both under threshold) | - (both blank at > 128) | 0.962 | 0 / 0 | - | yes |
| stat_callout f40 (settled) | 0.80 | 0.951 | 26855 / 29473 | 0.93 | yes |

Reading:

- Same class as the census MG triangle: identical words in identical
  order on identical lines (`MG_*_side_by_side.png`); the gap is fringe
  plus ~7-10% less ink on the FFrames side (opacity ratio 0.90-0.93,
  the same ratio the census measured FF-vs-RM on title_lockup).
  FFrames-ready; the systematic ink shortfall is follow-up measurement,
  not a layout defect.
- The stagger is exact: per-frame ink curves step at the same frames on
  both engines (list starts 0/22/44/66 with the 60%-window cubic reveal;
  stat fade 0-8, hold, fade 76-84), and per-frame IoU holds 0.80-0.86
  across both arms' whole lives, dipping only at reveal edges.
- Remotion frames are straight alpha, so no premultiplied claim is made
  about them; the FFrames frames are the adapted (premultiplied) ones.
- Not covered: `typewriter` on stat_callout (whole runs, as the existing
  text arms), `data.stage_offsets` anchoring (equal-share path measured;
  the anchored path is unit-tested through the evaluator only).

## Reproduction

```
python repro/render.py <Composition> hf|ff <out_dir>   # under the verify lock, one at a time
python repro/view.py <out_dir> <Composition> <dest.png>
python repro/metrics.py <out_dir>
```

Run from the repository root with `PYTHONPATH=.` and `bin/vep`; the
binary is built by `scripts/build_fframes_runtime.sh`.

## S2 arms-d (G2): comparison_bars, counter_roll, step_counter, pointer_annotation, context_stamp, website_panel

Extended in `../s2-arms-d/`: FFrames lowerings for the last group of
MotionGraphics arms, each compared against Remotion on nine synthetic
props (no real card uses these arms) with full-life curves. Settled
ink IoU 0.91-1.00 except the counter/step figures (0.63-0.75 on
identical per-frame values - proportional figures vs Remotion's
tabular-nums, an IR gap recorded there). Three bugs found and fixed by
the comparison (pointer 80px is the border box, side-anchored bars
shrink-wrap the widest label, glow never enters layout).

## S2 real-use arms (G2): subject_emblem, review_panel, beat_accent

Extended in `../s2-arms-real-b/`: FFrames lowerings for the second group
of real-use MotionGraphics arms, each compared against Remotion on real
geo-podcast tight props plus two synthetic props (typewriter entrance,
centre-anchored card). Settled ink IoU 0.98-1.00 on every case with
full-life curves; two bugs found and fixed by the comparison (review
rows are border-box, 453 not 457; `box-shadow` blur maps at half into
`DropShadowFilter`). Open follow-ups are recorded there (1px vertical
phase on integer-origin small text, tabular-nums, caret blink, exit
typewriter, ellipsis clipping, co-live beat stacks).
