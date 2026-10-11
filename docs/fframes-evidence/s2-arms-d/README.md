# S2 arms-d: FFrames lowerings for comparison_bars, counter_roll, step_counter, pointer_annotation, context_stamp, website_panel

Census G2, last group of MotionGraphics arms. No real geo-podcast card
uses any of the six, so every case is synthetic - but each quotes a
real plan shape (copy, data payload, anchor, entrance) rather than a
toy. Each arm is lowered in `library/graphics_ir/lowering.py`, quoted
piece by piece from `DrawnElement` in
`remotion-subtitles/src/compositions/MotionGraphics/index.tsx` (plus
the HyperFrames pins where the two disagree), and compared against
Remotion frame by frame below. Verdict: **all six FFrames-ready** -
settled ink IoU 0.91-1.00, same reading order, same wraps, same values
per frame; the counter/step gap is glyph advances only (below).

## Cases

| case | props | arm path exercised |
|---|---|---|
| `stamp` | `Austin, Texas - 6:40 AM`, micro on blue, fade/fade, 60f, top_centre, 1080x1920 | pill + centred copy + box shadow |
| `bars_equal_share` | 3 items + values, fade/fade, 72f, middle_left | equal-share stagger, cubic growth, share-of-max widths |
| `bars_anchored` | 2 items + values + `stage_offsets` [0.0, 1.0], fade/fade, 60f, middle_left | plan-anchored starts, entrance-ramp window |
| `counter` | 0 to 1000 + label, fade/fade, 60f, centre | baked figure layers, 1.5x figure + 0.7 label |
| `counter_affix` | 1.5 to 4.8, `$`/`M`, 1 decimal, cut/cut, 48f, centre | affixes, decimals, grouping |
| `step` | 2 of 5 + label, slide/fade, 60f, bottom_centre | number row, faded "of" span, label |
| `pointer` | ring, scale/fade, 60f, middle_right | stroked circle, outer glow |
| `website` | address + capture, fade/fade, 60f, centre | chrome, address bar + rule, contained shot |
| `website_no_address` | capture only, fade/fade, 60f, centre | no bar, no rule when no copy stated |

Synthetic props: `repro/synth_props.py`. The website capture is
`repro/capture.png` (made by `repro/make_capture.py`) - a
project-supplied still, the way `brand_assets/` supplies one.

## Method

FFrames: `resolve_props` (bundled Montserrat, the face Remotion loads)
then `render_one_card_frames("MotionGraphics", ...)` - the adapted
premultiplied pixels the carriage receives. Remotion: the 4.06 CLI
sequence recipe (`npx remotion render MotionGraphics <dir> --props ...
--image-format png --sequence`), output to /tmp (census G8: dotted
paths). Both sides carry production's 1-based canonical frame names.
Renders ran one at a time under the verify lock.

The website asset reaches each side the way production reaches it:
absolute path for FFrames (`resolve_props` keeps absolute files as
given), staged under `public/brand/` for Remotion
(`remotion_brand_linker` copies it there; removed afterwards). Same
pixels, two address forms - the only props difference between the
sides.

Per frame: ink IoU on alpha>128 masks; SSIM/PSNR on both frames
composited over mid-grey **in each side's own alpha format**
(premultiplied FF, straight RM - the trap real-b states); opacity
ratio over the alpha union; premultiplied check on the FF frame;
full-life curves (ink px, mean alpha, IoU) in `metrics.json`.
`repro/compare.py` is the comparison; `repro/render.py` both renders.

## Settled frames

| case (frame) | ink IoU | SSIM | PSNR | opacity FF/RM | premult |
|---|---|---|---|---|---|
| stamp f30 | 0.9998 | 0.998 | 37.2 dB | 1.007 | yes |
| bars equal-share f36 | 0.943 | 0.999 | 45.5 dB | 0.990 | yes |
| bars anchored f30 | 0.915 | 1.000 | 49.7 dB | 0.953 | yes |
| counter f30 | 0.721 | 0.996 | 37.5 dB | 0.940 | yes |
| counter affix f24 | 0.751 | 0.996 | 36.7 dB | 0.957 | yes |
| step f30 | 0.633 | 0.998 | 42.2 dB | 0.937 | yes |
| pointer f30 | 0.938 | 0.999 | 50.0 dB | 0.955 | yes |
| website f30 | 0.992 | 0.967 | 25.5 dB | 1.014 | yes |
| website no-address f30 | 0.991 | 0.988 | 30.7 dB | 1.005 | yes |

Curves (in `metrics.json`): stamp IoU 1.000 on all 60 frames; bars
mean 0.94 (floor 0.89 inside the entrance ramp - the threshold class
the census notes); pointer mean 0.92 (floor 0.27 mid-scale-entrance,
same ramp class); counter mean 0.70/0.72 with stable held ends (0.82
on the held `$4.8M`); step mean 0.68. The stagger is exact - bars ink
curves step at the same frames on both engines.

## What the pictures say

- The stamp is the same pill on both sides: same 4px radius, same
  padding, same copy (`sbs_stamp_f30.png`, IoU 0.9998).
- The bars sit in the same place at the same widths: side-anchored
  tracks resolve against the widest label (~292px, not the 960px
  usable width a centre stack spans), fills at their share of the max
  (`sbs_bars_equal_share_f36.png`).
- The counter shows the same figure on the same frame (`947` at f30,
  `$2.2M` on both sides at f3): the roll curve, the 80% span and the
  held end all agree. What differs is advances - see below.
- The step row reads `2 of 5` over its label on both sides, the "of"
  span faded, bottom-aligned within 2px of the template's baseline
  row (`sbs_step_f30.png`).
- The pointer is the same 80px ring at the same anchor, same stroke,
  same outer glow (`sbs_pointer_f30.png`).
- The website panel carries the same chrome, address bar and rule,
  with the capture contained at its file's aspect
  (`sbs_website_f30.png`, IoU 0.992).

## Three bugs the comparison found and fixed in this lane

- **The pointer's 80px is the border box, not the content box.**
  Tailwind's preflight (imported by the Remotion app's `index.css`)
  sizes the ring div border-box, so the 8px border lives INSIDE the
  80 (measured 80x80 ink on the Remotion side, against 96x96 drawn
  content-box). The lowering and `mg_tight_box` both quote 80 now.
  HyperFrames has no such reset and draws 96 - a genuine RM-vs-HF
  divergence on bordered arms, recorded here, not repaired here.
- **Side-anchored bars shrink-wrap.** The `width: 100%` track on a
  side-anchored stack resolves against the widest label, while a
  centre stack spans the insets. The lowering's natural width is the
  widest unwrapped label (centre stacks never consult it) and
  `mg_tight_box` is anchor-aware the same way. First rendering drew
  964px tracks against Remotion's 300.
- **Glow never enters layout.** The first pointer fix kept the 16px
  glow inside the layout box and pinned it right 16px off
  (right-anchored ink at 924 vs 940). Box-shadow spreads no layout -
  the shared `MG_PAD` carries the spill the way it carries every text
  shadow - so layout boxes exclude glows on both sides now.

## Open follow-ups (measured, not blocking)

- **Counter/step figures are proportional in FFrames, tabular in
  Remotion.** The arms set `fontVariantNumeric: tabular-nums`, which
  the IR cannot express. Measured on the affix roll: Remotion holds
  282px on every frame (`$d.dM`, every figure the same width) while
  FFrames steps 241-278px with the digits, so a mid-roll figure sits
  up to ~20px off-centre relative to Remotion and IoU reads 0.63-0.75
  on identical values. Same family as real-b's score-tabular note;
  needs an IR spelling, deliberately untouched here. Values were
  verified frame by frame (`$2.2M` on both sides at f3, held `$4.8M`
  stable at 0.82) - the gap is advances only, never the figure.
- **Stamp ink is white in FFrames, plan-coloured in Remotion.**
  HyperFrames recolors the pill runs to white explicitly; Remotion
  leaves `Runs` in the plan's colour on the plan's own colour -
  invisible wherever the two meet. The lowering quotes HyperFrames
  and the vocabulary's readability contract. IoU is unaffected (alpha
  masks agree at 0.9998); the pixels differ by design, and the
  Remotion side reads as the bug.
- **The pointer's inset glow has no IR spelling.** The arm draws
  `box-shadow` outer plus `inset` twin; only the outer is drawn.
  Fringe on a glow, same class as a text shadow.
- **Square capture corners over the rounded panel.** The shot is a
  plain image node; the panel clips it with `overflow: hidden` plus
  its 16px radius, which the IR cannot round. Fringe at the four
  corners.
- **The address bar never ellipsises in FFrames** (single line, may
  spill where the template clips) - same class as real-b's review
  ellipsis note. Every stated address fits.
- **The step "of" span is bottom-aligned, the template
  baseline-aligned.** Measured 1px on the span, 3px on the number -
  same class as real-b's 1px vertical phase. Needs face-metric
  baselines, deliberately untouched here.
- **Counter/step label columns carry no inter-run gap in the
  lowering** (a `Runs` fragment draws none) while `mg_tight_box`
  budgets the shared 8px column gap: the tight canvas errs toward
  slack by design, and the slack is documented here rather than
  trimmed.
- **A roll's figure count scales the scene**: one timed layer per
  distinct figure (~70 for a 0-1000 roll over 60f). Short cards only;
  a minute-long roll would want a sparser baking.

## Reproduction

```
bin/vep docs/fframes-evidence/s2-arms-d/repro/make_capture.py \
  docs/fframes-evidence/s2-arms-d/repro/capture.png
lockf <state>/vep-verify.lock bin/vep \
  docs/fframes-evidence/s2-arms-d/repro/render.py <out_dir>
bin/vep docs/fframes-evidence/s2-arms-d/repro/compare.py <out_dir>
```

`render.py` writes `<case>_ff/` + `<case>_rm/` per case (1-based
canonical names); `compare.py` writes `metrics.json` plus one
`sbs_<case>_<frame>.png` per case at the settled frame. Only the
repro scripts, the capture, the side-by-sides, `metrics.json` and this
page are committed - the full sequences re-render from the scripts.
