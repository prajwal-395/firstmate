# S2 real-b: FFrames lowerings for subject_emblem, review_panel, beat_accent

Census G2, second group of real-use arms (`subject_emblem` 4 cards,
`beat_accent` 2, `review_panel` 1 in the geo-podcast project). Each arm
is lowered in `library/graphics_ir/lowering.py`, quoted piece by piece
from `DrawnElement` in
`remotion-subtitles/src/compositions/MotionGraphics/index.tsx`, and
compared against Remotion frame by frame below. Verdict: **all three
FFrames-ready** - ink IoU 0.94-1.00 across every frame of every case,
same reading order, same wraps, same stars.

## Cases

Four real tight props from the geo-podcast project
(`pipeline_output/steps/4_06_render_motion_graphics/motion_graphics/`,
read-only, referenced by name) plus two synthetic props
(`repro/synth_props.py`) for the paths no real card takes:

| case | props | arm path exercised |
|---|---|---|
| `35ad5429` | real, `Aa` + `HUMAN-WRITTEN` label, scale/fade, 96f, top_centre, 846x480 | plate + mark + label + scale entrance |
| `0ad89bd3` | real, `$` mark only, fade/fade, 48f, middle_left, 296x480 | mark-only, no label |
| `a6292208` | real, two-row panel, cut/fade, 72f, top_right, 756x554 | full card: header, stars, rows, bodies |
| `058e1d98` | real, footprint 0.5, cut/fade, 10f, centre, 156x480 | growing/fading disc |
| `synth_emblem_typewriter` | synth, `NEW` + label, typewriter/fade, 40f, centre, 540x960 | reveal layers + caret |
| `synth_review_centre` | synth, two-row panel, cut/cut, 30f, centre, 1080x1920 | definite-width card at a centre anchor |

## Method

FFrames: `fframes_render.render_one_card_frames` after `resolve_props`
(bundled Montserrat, the face Remotion loads). Remotion: the CLI
sequence render from 4.06's `post_bridge`
(`npx remotion render MotionGraphics <dir> --props ... --image-format
png --sequence`), output to /tmp (G8: dotted paths). Renders ran one at
a time under the verify lock.

Per frame: ink IoU on alpha>128 masks; SSIM/PSNR on both frames
composited over mid-grey; opacity ratio over the alpha union;
premultiplied check on the FF frame; full-life curves (ink px, mean
alpha, IoU) in `metrics.json`.

**Compositing trap, stated so the next lane does not repeat it:** the
Remotion sequence PNGs are STRAIGHT alpha while the FFrames frames are
PREMULTIPLIED. Compositing both as premultiplied paints every
translucent Remotion pixel too bright (a half-opacity `#7CFC00` read as
yellow instead of green) and corrupts every SSIM/PSNR that touches a
fade, a shadow or an antialiased edge. The comparison composites each
side in its own format. The first version of these numbers had the bug;
the images showed it (plate colour), and `metrics.json` holds the
recomputed values.

## Settled frames

| case (frame) | ink IoU | SSIM | PSNR | opacity FF/RM | premult |
|---|---|---|---|---|---|
| emblem `35ad5429` f48 | 0.987 | 0.994 | 37.3 dB | 0.995 | yes |
| emblem `0ad89bd3` f24 | 0.998 | 0.992 | 37.7 dB | 1.001 | yes |
| review `a6292208` f36 | 1.000 | 0.939 | 20.8 dB | 1.000 | yes |
| beat `058e1d98` f0/f1 | 0.988 / 0.978 | 0.998 / 0.998 | 45.7 / 48.2 dB | 1.021 / 0.996 | yes |
| emblem typewriter f20 (settled) | 0.981 | 0.992 | 32.4 dB | 0.991 | yes |
| review centre f15 | 0.9999 | 0.993 | 30.9 dB | 1.001 | yes |

Curves (in `metrics.json`): review IoU >= 0.9999 on every frame of both
cases with identical ink counts; emblem IoU >= 0.976 with ink within
2%; beat IoU 0.94-1.0, mean 0.986 (the floor is faint mid-life frames at
the alpha>128 threshold, the same threshold class the census notes).

## What the pictures say

- The emblem is the same object on both sides: same 200px plate, same
  mark size and seat, same label (`sbs_35ad5429_f48.png`). The label
  wraps where the stack wraps; the overflowing `HALLUCINATED`-class
  marks overflow symmetrically, centred in the element box the way the
  stretched mark div centres its text.
- The review card breaks lines identically (`Walked in / on a Tuesday`
  on both sides), draws the same stars (partial 4.8 fifth clipped, not
  glyphed), the same avatar discs with initials, the same 453px card
  (`sbs_a6292208_f36.png`).
- The beat disc is the same size at the same place with the same glow
  (`sbs_058e1d98_f1.png`); its per-frame size/position/opacity tracks
  hold over its 10-frame life.
- The typewriter mark arrives character by character with the caret
  (`sbs_synth_emblem_typewriter_f10_reveal.png`).
- The centre-anchored card sits at the container's left edge, as a
  definite-width flex item does (`sbs_synth_review_centre_f15.png`,
  IoU 0.9999) - the placement rule the lowering quotes.

## Two bugs the comparison found and fixed in this lane

- **Review rows are border-box.** The row div carries `box-sizing:
  border-box`, so its 2px top rule lives INSIDE the 145px row height.
  The first lowering (and `mg_tight_box.review_panel_size`, which quotes
  the same table) added the rule on top and drew 147px rows, a 457px
  card against Remotion's 453. Both now quote 145 (`lowering.py:
  _mg_review_card_size`, `mg_tight_box.py: review_panel_size`).
- **`box-shadow` blur maps at half.** The card's `0 10px 30px` shadow
  fell off slower in FFrames (alpha 56 vs 44 at 15px out, 35 vs 10 at
  30px). Halving the radius into `DropShadowFilter` (30 -> 15) lands on
  73/74, 43/44, 11/10. `filter: drop-shadow()` still maps 1:1 (the
  StagedScene parity gate passes on it), so the halving lives in the
  lowering with the measurement cited, and `DropShadowFilter`'s docstring
  records the source-function distinction. Applies to the plate's 32px
  (in as 16) and the beat glow's 24px (in as 12) too.

## Open follow-ups (measured, not blocking)

- **1px vertical phase on integer-origin small text.** On the real
  review card every text row sits 1px low in FFrames (uniform dy=-1 by
  band cross-correlation; ink IoU still 1.0000). The same content at
  half-integer origins (synth centre, card top 733.5; centre-on-small
  variant, top 50.5) aligns at (0,0). Characterised as a raster-phase
  interaction (resvg vs Chrome glyph snapping), same class as census G5;
  needs renderer/raster expertise, deliberately untouched here.
- **Score `tabular-nums`.** The score band wants dx=+1: the arm sets
  `fontVariantNumeric: tabular-nums`, which the IR cannot express.
  Fringe, one pixel.
- **Typewriter caret blink is per layer, and `exit: typewriter` does not
  conceal.** The caret's ~15Hz blink is baked per reveal layer rather
  than per frame, and an exit typewriter draws as a fade - the same
  limitation `lower_full_frame_card` already carries. No real card uses
  either path (fade/scale/cut in the wild).
- **No-row and palette-less panels lower to nothing**, mirroring the
  arm's null; `resolve_plan` already refuses both upstream.
- **Ellipsis overflow is unclipped in FFrames** (name/label/author set
  `nowrap` + `ellipsis`; the lowering emits the full text). Every real
  and repro string fits; a longer one would spill rather than clip.
- **A beat co-live with other stack elements is approximate**: its
  translate track re-pins against the static stack box while Remotion
  re-lays the container per frame. Real stacks hold one element.

## Reproduction

Real props are read from the geo-podcast project (paths above); synth
props from `repro/synth_props.py`. FF side: `resolve_props` then
`render_one_card_frames("MotionGraphics", ...)`. RM side: the 4.06 CLI
sequence recipe above, output outside any dotted path. Metrics: ink IoU
(alpha>128), SSIM/PSNR with per-format compositing (straight RM,
premultiplied FF), opacity ratio, full-life curves - `metrics.json`.
