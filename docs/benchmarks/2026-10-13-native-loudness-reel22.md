# Native mastering: Reel 22 (2026-10-13)

The native FilmCraft export now runs through the same mastering helper as
`ren deliver`. It resolves the project's delivery spec, normalizes an
audio-bearing encoded export, retains the unmastered render as
`.pre-master`, and records the input/output LUFS, true peak, loudness
range, target and limiter data in the execution receipt. Audio-only WAV
exports and video exports with no audio stream record why mastering was
skipped.

## What the shipped Reel 22 says

`Reel 22 - a-score-is-not-a-fix.deliver.json` records a `static_gain_true_peak_limiter`
master: target -14 LUFS (±1 LU), ceiling -1 dBTP, 0.5 dB limiter headroom,
and final limiter target -1.5 dBTP. The shared mastering implementation
uses static gain followed by a 4x oversampled true-peak limiter, encodes AAC
at 192 kb/s, and re-measures the encoded output. Reel 22's delivered file
measures -14.21 LUFS, -1.04 dBTP and 6.7 LU LRA. The project delivery spec
resolves to the same -14/-1 values.

The only other shipped video export in the readable project `exports/` is
Reel 26. It measures -25.00 LUFS, -8.61 dBTP and 8.9 LU LRA; its delivery
sidecar has no mastering record. The measured integrated loudness spread
between these two exports is 10.79 LU, and the true-peak spread is 7.57 dB.
The project spec still resolves to -14 LUFS / -1 dBTP, matching Reel 22's
recorded mastering target. Reel 26 is outside that target, consistent with
its missing mastering record. The export folder contains only these two
shipped reel videos; Reel 22 has the native before/after measurement, and
Reel 26 serves as the other-deliverable cross-check.

## Reel 22 measurements

The native input is the step 1 AFTER render. The mastered output below was
made from that file with the same target and ceiling as the delivered
sidecar. LUFS, true peak and LRA use FFmpeg `loudnorm` measurements.

| Reel 22 audio | Integrated LUFS | True peak | LRA |
|---|---:|---:|---:|
| Shipped deliverable | -14.21 | -1.04 dBTP | 6.7 LU |
| Native step 1 AFTER, before mastering | -27.16 | -8.03 dBTP | 11.1 LU |
| Native after declared mastering | -14.63 | -1.32 dBTP | 10.6 LU |

The native output is within the recorded ±1 LU tolerance and below the
-1 dBTP ceiling. Its LRA remains 3.9 LU wider than the deliverable. The
declared delivery process is static gain and peak limiting, so it does not
close that measured dynamics difference; no unrecorded compressor was
added to force the LRA to match.

## Picture comparison (step 1 method and references)

The comparison uses the same 1183-frame shipped deliverable and 1254-frame
fresh Resolve render as step 1, with the same per-frame SSIM-Y / PSNR-y
filters and 200-frame segments. The mastered file's compressed video
stream hash equals the step 1 AFTER stream
(`ec9a54432d5249c0fabb3465120cab08a2b17febff9d2a1c3879e4ff2260acbe`),
so the step 1 stills at f100, f300 and f329 are pixel-identical too.

| Comparison | SSIM-Y mean | PSNR-y mean | 200-frame SSIM-Y segments |
|---|---:|---:|---|
| Native mastered vs deliverable | 0.6664 | 20.36 dB | 0.679/0.670/0.658/0.663/0.660/0.667 |
| Native mastered vs fresh Resolve | 0.5037 | 14.23 dB | 0.487/0.485/0.478/0.484/0.492/0.513/0.817 (endcard) |

These match step 1 AFTER exactly because mastering copies the video stream.
The audio normalization changes no picture pixels.

## Validation and limits

- Targeted selection with the private `ren_native` build: 172 passed,
  no skips. The broader `--loop` selection would collect 5813 tests.
- Enforcing Ruff config: clean for the changed Python files.
- A two-second synthetic export test exercises both the native-treatment
  and untreated export paths, verifies the declared target, and checks that
  the unmastered source is retained.
- The six tests that initially skipped without the extension all pass with
  the private build: `test_filmcraft_plan_equals_the_adapter_projection`,
  `test_filmcraft_edit_rows_match_the_adapter_verdicts`,
  `test_filmcraft_refusal_carries_the_adapter_code`,
  `test_ramp_execute_refuses_before_mutation`,
  `test_execute_needs_the_pinned_cli`, and
  `test_execute_preconform_end_to_end_uses_cache_on_second_run`. The last
  exposed the no-audio export case; it now passes with the explicit skip
  reason in its receipt.
- Reel 26 is the only other shipped video export and has a deliverable
  measurement; its native before/after render is not part of this fixture.
  Reel 22's measured audio mastering and unchanged-picture comparison are
  complete.
  An exhaustive media inventory of `exports/` found only the Reel 22 and
  Reel 26 final MP4s. The Reel 30 and Reel 40 MOVs are named motion-graphics
  assets, not reel deliverables; the remaining files are stills or evidence.
