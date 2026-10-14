# Reel 22 native span-shortfall policy measurement

Step 7 of the native-fidelity plan was measured against the same references and frames as [step 1](2026-10-13-native-static-transforms-reel22.md). The test model carries Reel 22's two as-recorded shortfalls: Craig's first A-roll source holds 255 frames for a 256-frame timeline span, and the Akshita motion-graphic card holds 83 source frames for an 84-frame span. The captain selected `freeze_last_frame` (2026-10-13): the execute path extracts each shortfall's last source frame with ffmpeg, and the adapter (`expand_span_freezes`) places the still over the missing timeline frames - on V2 above the A-roll so linked dialogue keeps full duration, on the overlay's own track otherwise. The receipt records the policy, both shortfalls and both generated freezes. The freeze render completed at 1,255 frames, mastered to delivery loudness (-14.18 LUFS).

## Whole-reel results

| comparison | baseline step 1 SSIM-Y | freeze render SSIM-Y | baseline step 1 PSNR-y | freeze render PSNR-y |
|---|---:|---:|---:|---:|
| deliverable, 1,183 frames | 0.6664 | 0.6664 | 20.36 | 20.36 |
| fresh Resolve, 1,254 frames | 0.5037 | 0.5038 | 14.23 | 14.23 |

Per-200-frame segments, SSIM-Y / PSNR-y:

| frames | deliverable | fresh Resolve |
|---|---:|---:|
| 0000-0199 | 0.679241 / 20.2705 | 0.486525 / 13.3181 |
| 0200-0399 | 0.669923 / 20.4030 | 0.484981 / 14.0434 |
| 0400-0599 | 0.658232 / 20.3114 | 0.478070 / 13.9068 |
| 0600-0799 | 0.663430 / 20.2493 | 0.484269 / 14.0084 |
| 0800-0999 | 0.660486 / 20.3581 | 0.491525 / 14.6539 |
| 1000-1182 / 1000-1199 | 0.667325 / 20.5607 | 0.512862 / 14.3520 |
| 1200-1253 | n/a | 0.816611 / 18.2885 |

## Frame-level proof (freeze vs step 1, 1,255 frames)

- Frame 255 (0-indexed; Craig's last timeline frame) is the visible hold: SSIM-Y 0.53 against step 1, which played the hand-patched 256th source frame there. Extracted stills `stills-freeze/freeze_f255.png` vs `after_f255.png` differ strongly (max channel delta 255/233/244).
- The Akshita card hold lands at frame 339 on V4; the static card's consecutive frames are near-identical, so the frozen frame reproduces the intended picture (frame 83 region stills are pixel-identical, max delta 0).
- 255 frames differ at SSIM-Y 0.99, all inside 245-499: the x264 encode window (keyint 250 plus ratecontrol lookahead) around the one changed content frame, not content changes. Frames 0-244 and 500-1254 are SSIM-Y 1.000000.
- Total duration is unchanged at 1,255 frames and every later cut sits on its original frame: a one-frame shift anywhere would read as a strong mismatch across all following frames, which the per-frame log rules out.

An earlier candidate render (policy recorded, holds not yet implemented) was pixel-identical to step 1 on all 1,255 frames; the freeze implementation above supersedes it.

Scratch model, plan, receipt, render and per-frame logs were kept under `/tmp/vep-nf-shortfall/`.
