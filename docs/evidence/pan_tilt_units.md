# Resolve Pan/Tilt units

`tests/test_resolve_transform.py` and
`tests/test_draw_gain_measured.py` pin the measured conversion and its
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

The 2026-09-11 rendered plate calibration measured gain 1.0. A later
rendered-pixel calibration on the same Resolve build measured gain 2.0
across the reel, scratch, and project-default timeline classes used in
the project. The default conversion therefore follows the later
measurement, while tests keep the earlier table explicit at gain 1.0.
The rebuild's caption row converts to the captain's held Tilt value of
-917 and places its centre at y=1418.5.
