# S2 arms c2: progress_bar, frame_accents, digit_counter (FFrames vs Remotion)

Census G2, third group: the three chrome/counter arms from
`MotionGraphics/index.tsx` (`DrawnElement`, `DigitRoll`), lowered in
`library/graphics_ir/lowering.py` (`_build_mg_bar`, `_build_mg_accents`,
`_build_mg_digit`). Synthetic props, 540x960 at 30 fps, 60 frames, one
arm per card, `cut`/`cut` so the comparison reads geometry and the
spring, not entrance ramps. FFrames through the fixed release binary
(`native/target/release`, PATH-first); Remotion through the `npx
remotion render MotionGraphics --sequence` CLI path from
`post_bridge._render_motion_graphics_file` (output under `/tmp`: G8
rejects dotted output paths). No engine paths removed (S7 not
authorized). Method: ink IoU on alpha>128 masks, opacity ratio over the
union, premultiplied check on the FFrames frame.

## Settled agreement

| arm (frame) | ink IoU | opacity FF/RM | premult FF |
|---|---|---|---|
| progress_bar f15 | 0.993 | 1.002 | yes |
| progress_bar f45 | 0.997 | 1.001 | yes |
| frame_accents f15 | 1.000 | 1.000 | yes |
| frame_accents f45 | 1.000 | 1.000 | yes |
| digit_counter f5 | 0.556 | - | yes |
| digit_counter f15 | 0.409 | 0.925 | yes |
| digit_counter f30 | 0.288 | - | yes |
| digit_counter f45 | 0.558 | 0.929 | yes |
| digit_counter f59 (most settled) | 0.783 | - | yes |

Verdict: **FFrames-ready for all three.** The bar and the brackets are
pixel work (1.0 on accents, 0.997 on the bar: the fill lerp lands on the
same pixel through the scale-plus-translate lowering). The counter
settles to the same figure in the same box (f59 ink bbox x 156-392 on
both sides, y within 1px) with the census MG-triangle signature: ~10%
less ink on the FFrames side (9160 vs 10207 px, ratio 0.90 - the ratio
the census measured FF-vs-RM on title_lockup) plus 1px glyph fringe, and
a stronger Remotion figure glow that bridges the inter-digit gaps above
the 128 threshold (one ink column run vs three).

## Two things the comparison found

1. **Native spring timestamp bug (fixed in this lane).**
   `ren-graphics-fframes` stepped its spring integrator in frame counts
   where the shared physics steps in milliseconds (`now=f` vs
   `now=(f/fps)*1000`), so every `SpringEasing` animation rendered ~100x
   too slow (0.04 at t=1.0 instead of settled). Latent until now: no
   lowering emitted a spring before this lane. Fixed in
   `native/ren-graphics-fframes/src/main.rs` (`spring_progress`) with a
   `spring_tests` regression literal from the Python evaluator. The
   evidence above renders through the fixed binary.
2. **Remotion's `durationInFrames` time-warp is not the IR spring.**
   `DigitRoll` stretches its spring over the window
   (`frame/(span/natural)`, natural 19f at 30fps for this config) while
   the IR `SpringEasing` plays wall-clock physics over the animation's
   own duration - the same integrator, different clock. The lane proves
   the integrator matches (`test_spring_curve_matches_reference_integrator`:
   frame-for-frame to 1e-6 against `_spring_calculation`); the warp is
   what moves the mid-roll IoU (f30 0.288 at maximal phase divergence)
   while both sides settle to the same value (f59 0.783, same bbox).
   Open measurement follow-up, same shelf as census G5/G6: either the IR
   spring grows a warp the lowering can state, or the warp stays a known
   mid-roll phase difference. It is not approximated silently: the rail
   animation carries the config that names it.

## Reproduce

Props: one element per card, `{"fps": 30, "width": 540, "height": 960,
"durationInFrames": 60, "safeArea": {"top": 40, "right": 24, "bottom":
40, "left": 24}, "fontFamily": "Montserrat"}` plus the arm element
(bar: `top-centre`, `#4DA3FF`, progress 0.1 to 0.9; accents: `centre`,
`#FFC44D`; counter: `centre`, `#FFFFFF`, `data: {end_value: 1234,
decimals: 0}`, one supporting run `total views`). The render script is
scratch (kept out of the tree per lane hygiene); what it kept is
committed here: `*_f15/_f45_ff.png` / `*_rm.png` per arm, `*_sbs.png`
side-by-sides (FFrames left, Remotion right, over mid-grey),
`metrics_s2_arms_c2.json` the numbers (counter f5/f30/f59 rows measured
off the full 60f sequences, which stay in `/tmp` scratch outside the
tree).
