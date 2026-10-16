# EVAL-04 true-peak fallback - 2026-10-15

## Resolve API finding

The scratch project's live `Resolve.GetFairlightPresets()` call returned an
empty mapping. The scripting API stub declares that preset listing and
`Project.ApplyFairlightPresetToCurrentTimeline(presetName)` are available;
the API search has no limiter control or threshold-setting method. The
configured `Pipeline_Master_Limiter` preset therefore cannot be applied on
this Resolve instance. The Resolve control inventory independently records
no Fairlight EQ, dynamics, compressor, limiter, gate, or track FX script
commands (`docs/RESOLVE_CONTROL_PROBE.md`).

Ren's supported fallback is the measured post-render master in
`library/tools/master_loudness.py`, already called by step 6.01's
`_export_timeline`. It leaves Resolve's raw render in scratch, measures it,
applies static gain followed by a four-times-oversampled true-peak limiter,
encodes the delivery file, and measures that file again. The source is never
modified. Step 6.01 returns the mastered path to downstream render QA.

## Scratch render

Rendered LCATL0012.MXF from 102s to 116s on an isolated `Ren Scratch - EVAL04
truepeak 20261015` project, at 1920x1080 and 23.976 fps. The test timeline
used Resolve's default source channel mapping; it did not reproduce the
separate +15.68 dB OTIO clip-gain setup from the calibration note in
`docs/benchmarks/2026-10-15-eval04-evidence-to-edit.md`.

| File | Integrated loudness | True peak |
|---|---:|---:|
| Resolve raw render | -20.38 LUFS | -5.67 dBTP |
| Ren mastered output | -14.26 LUFS | -1.49 dBTP |

The delivery master reached the -14 LUFS target within its 1 LU tolerance and
cleared the -1 dBTP ceiling. `verify_render` passed all eight checks on the
mastered file: loudness/true peak, black frames, freeze frames, silence under
picture, resolution, frame rate, duration, and audio stream.

The exact +15.68 dB OTIO-gain case from the calibration note remains a
different input mix. This scratch render establishes that the existing Ren
mastering path can deliver true-peak-safe output on the same source exchange;
the separate calibration values above remain evidence for that earlier
unmastered render.

## Gain-case re-render (2026-10-15, after PR 2603)

Re-rendered the same LCATL0012.MXF 102.0-116.0s exchange on an isolated
`Ren Scratch - EVAL04 truepeak gain` project (1920x1080, 23.976 fps, one
video and one audio item, read back; scratch created, opened, restored and
deleted through the sanctioned `resolve-axi project scratch` verbs, captain
projects untouched). The live probe on that scratch project returned
`Resolve.GetFairlightPresets() == {}` and
`ApplyFairlightPresetToCurrentTimeline("Pipeline_Master_Limiter") == False`,
and the scripting API search still has no limiter or threshold-setting
method - only preset listing and application. Resolve cannot expose a
scriptable limiter, so the Ren mastering fallback below is the supported
path. The source was never modified.

The unity Resolve render measured -29.64 LUFS / -11.20 dBTP, reproducing
the calibration note's -29.68 / -11.20 to the digit on true peak. The
OTIO `delivery_loudness` gain for this render is therefore +15.64 dB
(target -14 minus measured; PR 2603's +15.68 dB came from its -29.68
measurement). An OTIO clip gain is a static dB gain applied at placement,
so the hot master was produced with that same static gain post-render
(ffmpeg `volume=+15.64dB`, AAC 192k, picture copied). The encode path
differs from Resolve's own hot render, so the overshoot magnitude differs;
the gate outcome is identical.

| File | Integrated loudness | True peak | `verify_render` |
|---|---:|---:|---|
| Resolve raw render (unity) | -29.64 LUFS | -11.20 dBTP | loudness fail (expected pre-gain) |
| Hot with +15.64 dB static gain | -14.30 LUFS | -0.07 dBTP | FAIL: true peak 0.93 dB over the -1.0 ceiling, all 7 other checks pass |
| Ren mastered output (1 pass) | -14.54 LUFS | -1.48 dBTP | PASS all eight checks, no issues |

PR 2603's own Resolve hot render with its +15.68 dB OTIO gain measured
-14.08 LUFS / +4.40 dBTP - the same failure (loudness reached, true peak
over ceiling), larger overshoot from Resolve's encode path with its
per-channel mapping. In both cases the Ren fallback
(`master_loudness.normalize_to_delivery`, static gain plus
four-times-oversampled true-peak limiter, re-measured after encode) brings
the delivery file inside -14 LUFS +/-1 and at or below -1.0 dBTP: here
-14.54 / -1.48 with `verify_render` fully passing. The render path now
meets the true-peak target as well as loudness through the supported
fallback; no Resolve limiter preset or API control is required.
