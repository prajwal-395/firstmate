# Resolve Pan/Tilt units

`tests/unit/resolve/test_resolve_transform.py` and
`tests/unit/reels/test_reel_build_gate.py` pin the measured conversion and its
callers. `library/tools/resolve_transform.py` is the one law and the
authoritative source for the measurement cells.

## The incident

Two code paths had different models of the same Resolve properties.
The overlay path used a hard-coded draw gain; the picture path treated
Pan/Tilt as frame pixels. Both were checked against captured values
instead of setting a known value and measuring the rendered result.
That left the overlay placement off by a factor of two and made picture
Tilt depend on the fit axis.

The shared law is `shift_px = value * (clip_dim / frame_dim) *
base_scale * draw_gain`. The clip's own dimensions and Resolve's base
scale explain geometry; the measured draw gain describes the renderer.
User zoom is not part of the unit conversion.

The 2026-10-14 scratch probe measured gain 1.0: with a full-frame 1080x1920
plate, Tilt -200 moved the plate 200 px down and Pan +/-100 moved markers
by +/-100 px. This reproduces the earlier 2026-09-11 measurement. The
2026-09-17 gain-2 record is contradicted by the rendered-pixel probe and is
treated as historical evidence, not the active calibration. Builds use
`draw_gain_probe`'s value when it measures the active renderer; otherwise
the fallback is 1.0. Each translated transform records both gain and source.
Historical gain-2 caption fixtures pass gain 2.0 explicitly.

The gain probe was repeated in a scratch Resolve project whose project
resolution was set to 3840x2160, with an empty custom 1080x1920 timeline and
`timelineInputResMismatchBehavior=scaleToFit`. The probe again measured 1.0:
Tilt -200 moved the bars down 200 px (file rows 283..403, rendered rows
483..603). So the 2x project-to-timeline dimension ratio does not explain the
gain-2 record for this custom timeline configuration. This does not qualify an
inherited-size timeline; its resolution-change behavior remains a separate
case.

## Scratch measurement and supported cases

All measurements below used a labelled grid in a disposable Resolve project.
The active project, timeline and page were recorded and restored; the scratch
project was deleted. Position is measured in output pixels, with positive
Resolve Tilt moving the picture upward. Let `W,H` be output dimensions and
`sw,sh` be the clip dimensions. The measured base scale is:

| Timeline input mismatch behavior | Base scale x | Base scale y | Pan 100 / Tilt 100 displacement for 1920x1080 into 1080x1920 |
|---|---:|---:|---:|
| `scaleToFit` | 0.5625 | 0.5625 | (+100, -31.25) px |
| `scaleToCrop` | 1.7778 | 1.7778 | (+316, -100) px |
| `centerCrop` | 1.0 | 1.0 | (+177.5, -56.5) px |
| `stretch` | 0.5625 | 1.7778 | (+100, -100) px |

The model is `scaleToFit = min(W/sw,H/sh)`, `scaleToCrop = max(...)`,
`centerCrop = 1`, and `stretch = (W/sw,H/sh)`. The measured behavior is
timeline-scoped: setting the Project value alone did not change an existing
timeline. Stretch is refused by the native bridge because its current native
scale field is uniform.

With `ZoomGang=false`, ZoomX=1.5 and ZoomY=0.75 changed the two axes
independently. Flips reflected the grid across the corresponding frame axis.
At zoom 1.5, setting either anchor coordinate to 100 moved the centered marker
by 50 px on that axis, confirming the unrotated anchor term
`(1-z)*anchor`. A 30 degree rotation retained the frame center to under
0.5 px with the default anchor. CropLeft=50 and CropRetain=false produced no
measurable image change in this setup. Crop semantics therefore remain
unqualified and native translation refuses nonzero crop. Rotation and
off-center anchors also remain refused in native translation because their
combined order and pivot are not fully established.

## Native pixel parity

Resolve and FilmCraft rendered the same labelled ProRes grid source at 1080x1920
with Pan=100, Tilt=100, unit zoom, default anchor, no crop and no rotation.
The target marker centroid in Resolve was (639.50, 860.00); FilmCraft was
(640.44, 860.00), a delta of (+0.94, 0.00) px. The four corner marker
centroids differed by at most 1 px. This is within the grid rasterization
tolerance and validates native position translation for the measured base
scale. It does not establish pixel parity for crop, nonuniform zoom, rotation,
off-center anchors or stretch.

## Reel 22 predicted correction

For the paused hybrid comparison's Pan=-26.8085, Tilt=1836 and crop canvas
846x480 in a 1080x1920 output, the old gain-2 calculation gives a translated
position of (498, 42) px. The measured gain-1 calculation gives (519, 501) px.
The correction is (+21, +459) px. The hybrid render itself was not rerun, so
the separately observed (+22, -220) px lane delta is not claimed to be fully
explained by this corrected prediction.

The rebuild's caption row converts to the captain's held Tilt value of -917
and places its centre at y=1418.5.

## Historical 2x mismatch

The 2026-10-01 Reel 22 investigation recorded a sweep using fallback draw gain
2.0 while the build calibration was 4.0. That ratio makes a recomputed Pan/Tilt
value half the value produced by the build, matching the observed direction of
the 2x discrepancy. The Oct 2 fix in commit
[`6c67529`](https://github.com/prajwal-395/video_editing_pilot/commit/6c67529b9973ae88f1b59c48bc3981fda7109565)
documents the path: when Resolve could not read the entry playhead, the probe
returned fallback 2.0 instead of measuring 4.0. It now measures on its scratch
timeline and leaves the unreadable entry playhead alone. This explains the
Reel 22 sweep/build mismatch; it does not identify the historical writer of
the persistent Reel 09/13 timeline changes. The activation loop and project
resolution leads remain unproven as causes of those saved changes.
