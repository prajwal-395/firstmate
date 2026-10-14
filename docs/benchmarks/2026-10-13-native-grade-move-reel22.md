# Move composition (grade refused): Reel 22 native before/after (2026-10-13)

Steps 2 and 3 of the native-fidelity push (report §6.2-6.3): the
projected move (zoom) wired into the FilmCraft render through the
native picture keys; the projected grade (contrast/CDL) stays refused
with a measured reason (below). BEFORE = step 1 AFTER
(`reel22-after.mp4`, SSIM-Y 0.6664 vs deliverable, grade+zoom PROJECT
but uncomposed). AFTER = same 1255-frame timeline rendered with this
branch's code (move path wired, grades refused): picture-identical to
BEFORE, proving no regression.

## Method (step 1 method exactly)

- Same references: shipped deliverable (1183f) and fresh Resolve render
  (1254f). Same whole-reel measures: SSIM-Y/PSNR-y means over 1183f
  (deliverable) and 1254f (fresh), per-200f segments, worst frames.
- Model for AFTER picture: step 1 `reel22-model-after-exec.json`
  (33 uses, 34 static effects), overlay URIs retargeted by basename to
  the fixture `step1/overlays/`, A-roll MXF paths unchanged, rendered
  via README path A (`project-model ... --execute --export
  --sequence-size 1080x1920`) with this branch's code.
- Move execution proven in isolation (synthetic
  `move.zoom_emphasis` 1.0/1.018/1.0 on the fixture model's first use):
  BEFORE losses 1 `effect_owned_by_effectcraft`, AFTER losses EMPTY,
  25+ `transform.scale` keys (100.0 to 101.8 to 100.0) executed
  in-engine. Grade isolation measured with invented
  `color.grade` contrast 0.12 spans (below).
- BEFORE numbers are the fixture bundle's recorded step 1 AFTER.

## Whole-reel numbers

| comparison | BEFORE SSIM-Y | AFTER SSIM-Y | BEFORE PSNR-y | AFTER PSNR-y |
|---|---|---|---|---|
| native vs deliverable (1183f) | 0.6664 | **0.6664** | 20.36 | **20.36** |
| native vs fresh (1254f) | 0.5037 | **0.5037** | 14.23 | **14.23** |

AFTER re-rendered with this branch's code is numerically identical to
BEFORE (same means to 4 decimals), meeting the no-regression bar
(AFTER at 0.6664 vs deliverable).

Isolation probes (same method, same references, models with invented
effects on top of step 1; NOT the shipped AFTER):
- grade+move (2 contrast-0.12 spans + 1 zoom): 0.5965 / 18.68 vs
  deliverable (-0.07 SSIM). Grade-driven.
- move-only (1 zoom): 0.6441 / 19.23 vs deliverable (-0.022);
  0.5156 / 14.09 vs fresh (+0.012).

## What executes vs remains refused

- Move: `move.zoom_emphasis` with scale-only projection lowers to
  `transform.scale` keys and executes in-engine (proven in isolation;
  `native_move_effect_ids` in receipt). Non-scale moves stay with
  EffectCraft as before (projection losses say so).
- Grade: `color.grade` stays refused as `effect_not_projected` (same
  code as before this lane). Precisely: the per-clip
  BrightnessContrast 0.12 proxy (`eq` contrast 1.12), stacked on the
  declared project look globally (`v04_teal_split`: contrast 1.12,
  saturation 1.12, brightness 0.005, gain 0.9966), totals ~1.25
  contrast on A-roll and moves the picture away from the deliverable
  by measured 0.07 SSIM-Y reel-wide (all six segments down 0.06-0.08).
  The proxy is first-order only and applies in the wrong order
  (timeline-global before per-clip spans; Resolve stacks per-clip
  before timeline). Until the proxy is calibrated against Resolve's
  BrightnessContrast + series-look stack, grades refuse rather than
  regress. Glow/vignette remain refused with their surveyed reasons
  (19.9/25.5 dB).
- Projection losses: step-1 model (no grade/move effects) losses EMPTY
  before and after (no regression). Synthetic move-only model:
  BEFORE 1 loss, AFTER EMPTY. Synthetic grade+move model: BEFORE
  2 losses (1 move + 2 grades), AFTER 1 loss (grades refused, move
  lowered) under the shipped code.
- Receipt-loss fix: the execute receipt read BEFORE losses when
  A-roll audio streams were re-measured; fixed to re-project the
  lowered model.

## What changed in the product

- `library/tools/project_model_cli.py`: `_project_model_compositions`
  (scale-only moves via `project_move` to `transform.scale` keys,
  fail-closed on keys outside clip bounds), `_model_with_native_moves`
  (lowering), `plan_filmcraft` + `execute_filmcraft` wired, receipt
  carries `native_move_effect_ids`. No grade-span compositing:
  `needs_treatments` covers declared grade/bezel only, so move-only
  runs export straight from the engine with no mezzanine pass.
- `library/tools/native_effects_composite.py`: unchanged from main
  (grade-span/zoom-span plumbing removed; moves execute in-engine,
  not via `zoompan`).
- `tests/unit/context/test_project_model_cli.py`:
  `test_native_composition_projects_measured_zoom_and_leaves_grade_refused`.

## Heavy-run resources

- AFTER render (step-1 model, this branch: plan + import 33 +
  placements + 34 static + H264 delivery 1255f + mastering):
  exclusive lock-held, output 72 MB, video-identical to fixture AFTER.
- Isolation renders (grade+move 79 MB, move-only 72 MB) + 3x comparisons
  (SSIM+PSNR over ~1200f): lock-held.
- Plans, unit tests, comparisons parsing: light, no lock.
