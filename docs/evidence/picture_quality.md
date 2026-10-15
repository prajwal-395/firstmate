# Picture quality: usable ranges are measured

Tests: `tests/unit/picture/test_picture_quality.py`.

Two things are under test here.

The NON-NEGOTIABLE, and the reason the issue was raised: when nothing
measured a clip, `usable_ranges` must not claim the whole clip.  It used
to read `[[0, full_duration]]` beside `usable_ranges_method: "unmeasured"`
- one field contradicting the three next to it - and the B-roll selector
reads that field to decide which 2.5 seconds of a clip to cut.  On the
2026-08-26 run of project 001 it cut `clip_006` 1.65-4.15s, a whip pan out
of a moving car window, and the master at 25.0s is motion-blurred asphalt.

The MEASUREMENT that replaces the assertion: a sharpness estimate sampled
straight off the video file, which needs no temporal index and therefore
works on a first run - see `library/tools/analysis/picture_quality.py` for
what its sampling rate can and cannot resolve.

Calibration on project 001's own footage
────────────────────────────────────────
Sampled at 5 Hz with the short side bounded to 180px, 17 clips / 807s:

    clip      file           dur     thr   soft   soft ranges (s)
    clip_006  IMG_1811.MOV  22.87   166.3  16.6%  0.0-2.0, 2.8-3.6, 7.2-8.2
    clip_008  IMG_1813.MOV   9.07   512.9  48.5%  3.8-6.2, 6.8-7.8, 8.0-9.0
    clip_007  IMG_1812.MOV 139.13   352.6  15.5%  16 ranges
    clip_011  IMG_1816.MOV 188.58   206.6   2.8%  5 ranges
    ...8 of the 17 come back with no soft range at all.

Verified by eye against extracted frames: clip_006 @3.15s (the source
frame behind the master's 25.0s) and clip_008 @5.0s are both unreadable
motion blur and both fall inside a reported range; clip_006 @5.0s and
clip_008 @2.0s are sharp and legible and neither does.  Cost: 2.79s per
clip, 0.059x realtime.

Objective per-sample measurements
---------------------------------
`sample_picture_quality` adds time-addressable pixel readings. It samples the
display-oriented grayscale frame at 5 Hz after aspect-preserving resize to a
180 px short side. `measurements_in_interval` uses a half-open interval; the
standalone command accepts `--quality --start S --end S` and only decodes that
window.

| Field | Method and unit | Limit |
|---|---|---|
| `focus_laplacian_variance` | Variance of the 4-neighbour Laplacian, in squared 8-bit luma levels | Texture, resizing and codec artifacts affect the value; it is not a lens MTF measurement. |
| `luma_mean`, `luma_p05`, `luma_p95` | Mean and percentiles in decoded 8-bit grayscale levels | Luma omits color-channel clipping. |
| `underexposed_pixel_fraction`, `overexposed_pixel_fraction` | Pixel fractions at luma <=4 and >=251 | These are near-black/near-white fractions after decode, not an exposure-compensation recommendation. |
| `low_gradient_residual_sigma_luma` | 1.4826 x MAD of the 3x3 median-filter residual, on pixels with Sobel gradient magnitude below 12 | Texture and compression remain in the residual; this is not sensor noise ground truth. |
| `frame_difference_luma_fraction` | Mean absolute luma difference from the preceding sampled frame, divided by 255 | Includes subject motion, camera motion, lighting changes and cuts. First sample of a requested interval has no predecessor and is `null`. |
| `motion_blur_proxy` | Frame-difference fraction multiplied by softness below the clip/interval Laplacian threshold | A proxy only; it does not distinguish shutter blur from defocus, cuts or other changes. |

Every row carries `time_s`; `sampled_interval_s` records the decoded window.
The coordinate metadata names the display-oriented frame space. The metrics
are image properties and make no aesthetic claim. Existing face position and
width come from the separate 1.04 face track (`face_center_x` and
`face_width`, normalized to the frame); this sampler does not infer subject
identity, occlusion or obstruction from frame-wide statistics.

The new readings are exposed by the Python API and the standalone CLI. They are
not used as editorial judgments or hidden replacements for the existing
sharpness decision. The existing calibrated soft-picture threshold continues
to govern only `usable_ranges`.

Held-out read, once, with no tuning: the first 15 seconds of raw podcast
`LCATL0014.MXF` were sampled at 5 Hz (75 frames) with the method above. Main
had no exposure, clipping, residual-noise, or motion-associated softness
fields; this branch recorded them independently. Median focus Laplacian
variance was 353.523, median luma was 41.519/255, median underexposed fraction
was 0.000000, median overexposed fraction was 0.001424 (maximum 0.001510),
median low-gradient residual sigma was 0.000 luma levels, median frame
difference was 0.006792, and median motion-blur proxy was 0.000000. This is a
measurement report, not threshold calibration; no setting was changed from
these held-out values.
