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

## Reproduction

```
python repro/render.py <Composition> hf|ff <out_dir>   # under the verify lock, one at a time
python repro/view.py <out_dir> <Composition> <dest.png>
python repro/metrics.py <out_dir>
```

Run from the repository root with `PYTHONPATH=.` and `bin/vep`; the
binary is built by `scripts/build_fframes_runtime.sh`.
