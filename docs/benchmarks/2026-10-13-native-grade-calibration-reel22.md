# Reel 22 grade calibration (2026-10-13)

This calibration follows the grade regression measured in
[`2026-10-13-native-grade-move-reel22.md`](2026-10-13-native-grade-move-reel22.md).
The regression is a duplicate grade term, not an implicit camera transform:
the fixture's execute model has only two `transform.scale` effects and 32
`transform.position` effects, with no `color.grade` or `color.lut`. The
project separately declares `style.series_look: v04_teal_split`, and the
native delivery composites that declared look once. The failed isolation
model added two clip-level `color.grade` effects with contrast 0.12 on top
of that project look.

## Measurement

The whole-reel comparison uses the step-1 method and the same shipped
deliverable. With only the project look applied, Reel 22 is at SSIM-Y
**0.6664** and PSNR-y **20.36 dB**. Adding the two clip-level contrast
effects while retaining the project look produces SSIM-Y **0.5965** and
PSNR-y **18.68 dB**. All six 200-frame segments fall by 0.06-0.08 SSIM-Y.
That is a 0.0699 SSIM-Y regression, so the clip-level grade does not pass
the required 0.6664 threshold. The move is present in both comparisons;
the move-only control measures 0.6441, isolating this regression to the
grade stack.

The inputs are `tri_f100.png`, `tri_f300.png`, and `tri_f329.png` from the
step-1 fixture's `stills/` directory. The triptychs are 1080x640, with the
AFTER render in the middle 360-pixel panel and the deliverable at right.
The following channel means use identical panel-relative crops (RGB,
8-bit), away from captions and frame edges. These are picture diagnostics,
not a claim that an RGB mean alone predicts SSIM.

| frame / crop | AFTER RGB mean | deliverable RGB mean | AFTER minus deliverable |
|---|---:|---:|---:|
| 100 / face | 105.73, 88.04, 79.59 | 109.74, 91.26, 87.11 | -4.01, -3.22, -7.52 |
| 100 / shirt | 118.63, 78.65, 68.74 | 127.31, 81.46, 76.65 | -8.68, -2.81, -7.91 |
| 300 / face | 107.49, 77.32, 62.59 | 110.35, 79.35, 68.62 | -2.86, -2.03, -6.03 |
| 300 / shirt | 91.66, 71.09, 63.95 | 97.87, 72.42, 68.65 | -6.21, -1.33, -4.70 |
| 329 / face | 100.90, 74.27, 61.28 | 103.11, 76.59, 67.40 | -2.21, -2.32, -6.12 |
| 329 / shirt | 83.01, 61.35, 54.02 | 87.88, 62.66, 59.38 | -4.87, -1.31, -5.36 |

The stable negative blue-channel difference across these matched crops is
consistent with the deliverable retaining a warmer image than the AFTER
render. It does not justify increasing saturation or adding another
contrast pass: the measured duplicate pass worsens whole-reel similarity.

## Cause and disposition

The project look resolves to scalar gain 0.9967, contrast 0.12,
brightness 0.005 and saturation 1.12. The isolated clip effects each add
another contrast 0.12 through the first-order `eq` proxy. Applying that
proxy after the global look yields approximately 1.25 effective contrast
on A-roll, while the current global pass is already the single project
look application. The order also differs from Resolve: the proxy is
timeline-global before clip spans, whereas Resolve applies the clip grade
before the timeline look. The increased contrast and order mismatch
explain the measured drop; the RGB samples also show the residual color
difference is not repaired by repeating the contrast term.

Both source streams report H.264, 3840x2160, `yuv420p`, limited range,
BT.709 matrix, transfer and primaries, at 24000/1001 fps. The fixture
receipt records `preconform.enabled: false`; the engine conform is the
measured 1:1 center crop, with no color operation. The execute model has
no `color.lut` operation, so there is no declared camera LUT or
log-to-Rec.709 conversion to stack with the project look. The BT.709 tags
do not establish whether a camera look was baked before encoding, but they
show that this path does not need an explicit log decode. No default
grade node is introduced by the model path. The evidence points to the
explicit duplicate project/clip contrast as the cause. The project look
remains applied once; model
`color.grade` effects remain refused as `effect_not_projected` until a
clip-aware composition can preserve Resolve's order and beat the existing
0.6664 result.

No grade execution change is shipped from this calibration. The prior
refusal remains in force, and the Reel 22 baseline stays at SSIM-Y 0.6664
against the deliverable. Glow and vignette remain refused under their
existing measured coverage reasons (19.9 dB and 25.5 dB respectively).
