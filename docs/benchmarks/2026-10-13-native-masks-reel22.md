# Masks: Reel 22 native before/after (2026-10-13)

Step 4 of the native-fidelity push (report section 6.4): close
`UnprojectableMask` for reel clips with reel-clip segmentation (MU-15
object tracks or M3d person-instance region masks), projected into the
model and executed on the native path.

Measured answer: **neither source fits, because Reel 22's masked
elements are not persons or objects.** The step cannot close its gap as
scoped; this note reports the measured reason instead of forcing a
mask, per the lane brief. BEFORE (step 1 AFTER) == AFTER by
construction: the step 1 execute model carries no masks and no entity
links, so the wired mask path emits zero commands and the native
render is bit-identical to step 1 AFTER.

## Method (step 1 method exactly, minus the render)

- Same references as step 1: shipped deliverable (1183f) and fresh
  Resolve render (1254f); same whole-reel SSIM-Y/PSNR-y.
- No re-render was run: `plan` on the step 1 execute model on this
  branch yields `mask_commands: []`, `losses: []`, 34 picture effects
  (identical to step 1), so the engine command stream — and therefore
  the render — is unchanged. The numbers below are step 1's reference
  results, re-asserted by the empty plan rather than re-measured.
- Resolve side is read-only evidence: the three Reel 22 Fusion comps
  on disk (`7_01_build_reels/fusion_comps/Reel_22_*`), never opened in
  Resolve, no timelines touched.
- M3d side is measured reads only: both Reel 22 sources' `regions.json` +
  sidecars under the shared source-memory root, seeds decoded at the
  two A-roll source starts and polygonized by the shipping
  `_mask_contour_paths` (the 0.92-IoU gate the execute path enforces).

## What Reel 22's masks are (Fusion comps, read-only)

| clip | comp MediaIn | masks | what they drive |
|---|---|---|---|
| Akshita A-roll (LC4932, reel 256-1184) | V01 comp | `Ellipse1` EllipseMask (full-frame 3840x2160, W=H=1.0, SoftEdge 0.3, Invert 1) | `Background1` vignette, merged at Blend 0.35 over SoftGlow output |
| Craig A-roll (LCATL0013, reel 0-256) | V02 comp | same `Ellipse1` vignette, plus `PowerBandMask1` RectangleMask with animated height (BezierSpline 0.02 over 18f, tv_power band switch) | vignette as above; band drives `PowerBand1` background merge |
| MG overlay | V04 comp | none | — |

No person matte, no object matte, no tracking target anywhere in the
three comps. MU-15 object tracking likewise has nothing to seed: Reel
22 carries no cutaway/object overlays (MGs are ProRes files with
alpha). The masked elements are analytic grade/power shapes owned by
steps 2+3 (grade/move composition), and the native `mask.add` operation
is a clip opacity matte — a different operation Resolve never applies
here. Projecting a person matte onto either A-roll clip would add
geometry the deliverable never drew, reducing fidelity.

## What M3d supplies (quality is not the limit)

Both A-roll windows hold exactly one face-linked person track:

| window | alive tracks | person track | face link | seed agreement (IoU) | matte coverage |
|---|---|---|---|---|---|
| Craig LCATL0013 @3629.876s (tick 3629876ms) | region_599 + region_721 (corner bg, no face) | region_599, box [0.27,0.18,0.70,0.79] | face_001 @0.996 | **0.9797** (3 polygons) | 0.1405 |
| Akshita LC4932 @3644.307s (tick 3644307ms) | region_041 alone | region_041, box [0.11,0.15,0.68,1.00] | face_001 @0.99 | **0.9698** (4 polygons) | 0.1342 |

Both seeds clear the execute path's 0.92 polygon-agreement gate with
margin. Segmentation quality does not block this step; fit does.

## Whole-reel numbers (before == after)

| comparison | BEFORE (step 1 AFTER) SSIM-Y | AFTER SSIM-Y | BEFORE PSNR-y | AFTER PSNR-y |
|---|---|---|---|---|
| native vs deliverable (1183f) | 0.6664 | **0.6664** | 20.36 | **20.36** |
| native vs fresh (1254f) | 0.5037 | **0.5037** | 14.23 | **14.23** |

No per-200f segments or stills were re-taken: identical command
stream, identical pixels.

## What changed in the product (this lane)

- Person-segmentation masks project onto FilmCraft (`translate_filmcraft_masks`
  in `library/tools/project_model_cli.py` + `mask_add`/`mask_track`
  adapter ops in `native/ren-filmcraft`): only `sam2_rle_track` /
  `region_rle` kinds bound to the exact source clock with a linked
  entity and a seed covering the clip start; seed polygon must hold
  0.92 raster IoU or the run refuses; face boxes stay a named
  projection loss. Wired into both `plan` and `execute` (fail-closed
  pre-execution check plus post-placement commands).
- `region_rle` resolves from an explicit `memory_root` when given
  (tests), defaulting to the machine source-memory root in production.
- Tests: mask projection/agreement/refusal rows in
  `tests/unit/context/test_project_model_cli.py` (31 passed), adapter
  `mask_add`/`mask_track` mapping rows in `ren-filmcraft` (passed on
  this branch head before this note).

## Left open (not this lane)

- Vignette ellipse + power-band rectangle execution belongs to steps
  2+3 composition: they are grade/power parameters, not segmentations,
  and need a native vignette/band counterpart, not a person matte.
- If a future reel carries a genuine person-masked element (subject
  grade, behind-subject plan), the path built here executes it; Reel
  22 is not that reel.
