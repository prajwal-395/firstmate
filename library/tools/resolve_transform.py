"""ONE model of Resolve's per-clip Pan/Tilt, for overlays and picture alike.

The engine used to hold two. `tight_box` modelled the overlay
transform one way - with a `draw_gain` constant that does not exist -
and `reel_framing.delivered_picture` / `reel_look.punch_in_properties`
modelled the SAME Resolve property a different way for picture clips
(1 unit = 1 frame pixel). Neither had ever been checked against a
picture a value it set had produced, and both were wrong: the overlay
path by a factor of two on every tight placement, the picture path by
`fit` on whichever axis the fit is not bound to - right on Pan by
coincidence at the reels' geometry and wrong on Tilt by 3.16x.

Two modules disagreeing about one Resolve behaviour is the structural
fault; correcting one constant would have left it standing. So the law
lives here once and both paths call it.

The law, measured
-----------------
    shift_px = value * (clip_dim / frame_dim) * base_scale

`clip_dim` is the clip's OWN pixel width/height (an overlay canvas, a
source frame), `frame_dim` the delivery frame's. `base_scale` is the
scale Resolve draws the clip at BEFORE the user zoom:

- `NATIVE_BASE_SCALE` (1.0) when the clip is at `Scaling=1` (Crop):
  native pixels, centred. Every tight overlay is placed this way.
- `fit_base_scale(...)` = `min(FW/sw, FH/sh)` when it is at the
  project's `scaleToFit` mismatch default. Every picture clip is.

Positive Tilt moves the clip UP; positive Pan moves it RIGHT. **The
user `ZoomX`/`ZoomY` does not enter it** - measured: Pan 100 moved the
picture 100 px at zoom 1.0 and at zoom 2.307 alike (09-11 calibration;
200 px under today's gain - the numbers below are the zoom proof,
not the gain).

There is NO "draw gain". `MEASURED_OVERLAY_CASES` and
`MEASURED_PICTURE_CASES` below are the evidence, and
`tests/unit/resolve/test_resolve_transform.py` re-derives the law from them rather
than restating it: 16 synthetic plates rendered to 16-bit TIFF and
measured as drawn rectangles, over canvas heights 200/480/960/1920 and
widths 200..1080, across two independent builds and four separate
processes (`ZZ Positioning Truth (scratch)`, Resolve Studio 21.1.0.14,
2026-09-11), plus five picture cases on a 3840x2160 plate under the
project's real `scaleToFit`. Confirmed on the captain's own reels from
stills exported the same day: a 296x480 graphic stored at Pan 1167.568
is found exactly 320 px right of centre, and `1167.568 * (296/1080) *
1 = 320.0`.

Why the two wrong models both survived review: each was calibrated
against a CAPTURED Pan/Tilt rather than one it had set itself. A
constant of this kind is only measurable by SETTING a known value and
RENDERING. Never calibrate one against a capture again.

The draw gain, measured twice
-----------------------------
The law above carries an explicit `draw_gain`: the ratio between what
the renderer draws per unit and what the 2026-09-11 calibration
measured. It defaults to 1.0, which reproduces every 09-11 case
exactly, and every caller that does not pass it keeps that behaviour.

`FALLBACK_DRAW_GAIN` (2.0) is the 2026-09-17 rendered-pixel
calibration on the captain's machine, same Resolve build
(Studio 21.1.0.14), and it disagrees with the 09-11 one. Both
calibrations set known values and rendered; the newer one is the
default because it describes every timeline class in the project
today - 1080x1920 custom reels, 1080x1920 custom scratch, 3840x2160
custom scratch and 3840x2160 project-default scratch, both axes,
captions, motion graphics, picture and native plates:

- caption 904x480, Tilt -888 -> canvas centre y 1404, and hand-set
  Tilt -917 -> y 1418.5 (two independent points, slope exactly 0.5;
  row-profile correlation 1.0000 at the predicted shift, 0.63 at the
  row the old law computes, -0.03 at the normal-reference reading);
- motion graphics 920x480 at Tilt 1296 and 296x480 at Pan 583.78
  drawing the pinned centres [540, 312] and [860, 960] (the captain's
  own pins, both axes, slope 2x the law on each);
- picture 3840x2160 at fit on a scratch 1080x1920 timeline: Tilt 100
  moves 63 px and Tilt 200 moves 127 px (linear), Pan 100 moves
  200 px - 2x the law's 31.6 / 63.3 / 100 on those cells;
- caption 904x480 on a scratch 3840x2160 timeline and a native
  3840x2160 plate on a project-default 3840x2160 timeline: both draw
  at 2x the law.

Drawn sizes are 1:1 throughout (caption ink 121 rows in the file,
120 in the render), so the error is in the conversion term, not the
draw. The 09-11 tables below stand as the gain-1.0 record - tests
pin them by passing `draw_gain=1.0` explicitly - and the newer
calibration is what the functions compute by default.

A stored value that reads back correctly proves nothing about the
gain - only rendered pixels discriminate, which is why the standing
verification is a still, not a read-back: the Reel 01 gate
(`caption_row` 0.8451 must render its caption centre at y 1418.5
with stored Tilt -917, the captain's own hand value) fails the
moment this gain stops describing the renderer, in either
direction. If it ever reads 1.0 again, change this constant back
with a fresh rendered-pixel calibration, never by arithmetic.
"""

from __future__ import annotations

import math

#: What Resolve draws a clip at when it is at `Scaling=1` (Crop):
#: native pixels, centred. Not a tuning knob - the identity.
NATIVE_BASE_SCALE = 1.0

#: The measured overlay/native cases: `(clip_w, clip_h, frame_w,
#: frame_h, pan, tilt, drawn_x0, drawn_y0)` - the clip's top-left in
#: delivery-frame pixels, read off rendered TIFFs. Every one is at
#: `Scaling=1`, so `base_scale` is 1.
MEASURED_OVERLAY_CASES = (
    (840, 480, 1080, 1920, 0.0, 0.0, 120.0, 720.0),
    (840, 480, 1080, 1920, 0.0, -432.0, 120.0, 828.0),
    (840, 480, 1080, 1920, 0.0, 432.0, 120.0, 612.0),
    (840, 480, 1080, 1920, 0.0, -1728.0, 120.0, 1152.0),
    (296, 480, 1080, 1920, 0.0, 0.0, 392.0, 720.0),
    (296, 480, 1080, 1920, 1167.568, 0.0, 712.0, 720.0),
    (296, 480, 1080, 1920, 0.0, 480.0, 392.0, 600.0),
    (920, 480, 1080, 1920, 0.0, 2592.0, 80.0, 72.0),
    (920, 480, 1080, 1920, 0.0, 480.0, 80.0, 600.0),
    (1080, 480, 1080, 1920, 0.0, 480.0, 0.0, 600.0),
    (540, 960, 1080, 1920, 0.0, 480.0, 270.0, 240.0),
    (200, 200, 1080, 1920, 0.0, 480.0, 440.0, 810.0),
    (1080, 1920, 1080, 1920, 0.0, 0.0, 0.0, 0.0),
    # A full-frame plate at Tilt 480: its bounding box clips, so it was
    # measured by its surviving rows (0..1439) - up exactly 480 px.
    (1080, 1920, 1080, 1920, 0.0, 480.0, 0.0, -480.0),
    (480, 480, 1080, 1920, 0.0, 480.0, 300.0, 600.0),
)

#: The measured picture cases: `(source_w, source_h, frame_w, frame_h,
#: zoom, pan, tilt, drawn_cx, drawn_cy)` - where a mark at the source
#: centre lands. Under `scaleToFit`, so `base_scale` is the fit. The
#: zoom column is carried to pin that it does NOT enter the shift:
#: cases at zoom 1.0 and 2.307 give the same pixels per unit.
MEASURED_PICTURE_CASES = (
    (3840, 2160, 1080, 1920, 1.000, 0.0, 0.0, 540.0, 960.0),
    (3840, 2160, 1080, 1920, 2.307, 100.0, 0.0, 640.0, 960.0),
    (3840, 2160, 1080, 1920, 2.307, 0.0, 100.0, 540.0, 928.0),
    (3840, 2160, 1080, 1920, 1.000, 100.0, 100.0, 639.9, 928.0),
    (3840, 2160, 1080, 1920, 2.307, 249.0, 0.0, 789.0, 960.0),
)


#: The FALLBACK draw gain: the value a run uses when it cannot
#: calibrate the renderer itself (see `library/tools/draw_gain_probe.py`).
#: Measured 2.0 on rendered pixels 2026-09-17 (calibration table and
#: procedure in the module docstring). It is renderer STATE, not
#: geometry: the same build drew gain 1.0 on 2026-09-11, so a build
#: that can reach the renderer measures per run and passes what it
#: measured explicitly, and a run that silently fell back would be the
#: defect this exists to stop - the probe logs which source the run
#: used and reports a measured-against-fallback disagreement as a
#: finding. Never derive it by arithmetic from an observed
#: misplacement - re-measure it by setting known values and rendering.
FALLBACK_DRAW_GAIN = 2.0

#: Pan/Tilt values in pre-metadata recorded overrides are understood as
#: values measured against the original gain-1.0 transform law. New
#: records carry their own `recorded_draw_gain`.
LEGACY_OVERRIDE_DRAW_GAIN = 1.0


class ResolveTransformError(ValueError):
    """A transform reading needs positive dimensions."""


def _positive(*values) -> None:
    for value in values:
        if not value or float(value) <= 0:
            raise ResolveTransformError(
                f"the Resolve transform law needs positive dimensions; "
                f"got {values!r}.")


def fit_base_scale(clip_w, clip_h, frame_w, frame_h) -> float:
    """The scale Resolve draws a mismatched clip at under `scaleToFit`.

    The project setting every reel and every master build runs under
    (`timelineInputResMismatchBehavior = scaleToFit`, verified
    identical in the captain's project and the scratch).
    """
    _positive(clip_w, clip_h, frame_w, frame_h)
    return min(float(frame_w) / float(clip_w), float(frame_h) / float(clip_h))


def shift_px(value: float, clip_dim: float, frame_dim: float,
             base_scale: float = NATIVE_BASE_SCALE,
             draw_gain: float = FALLBACK_DRAW_GAIN) -> float:
    """How far one Pan/Tilt unit count moves the clip, in frame pixels.

    Unsigned: the caller applies the axis sense (positive Pan right,
    positive Tilt up) through `drawn_origin` / `drawn_centre`, so the
    sign lives in exactly one place.

    `draw_gain` is the renderer's measured draw per unit over the
    2026-09-11 calibrated law (`FALLBACK_DRAW_GAIN`, and the module
    docstring for how it was measured and when it must be
    re-measured). A build that calibrated the renderer passes what it
    measured (`library/tools/draw_gain_probe.py`); callers pinning the
    09-11 calibration pass 1.0 explicitly; everyone else takes the
    fallback default.
    """
    _positive(clip_dim, frame_dim)
    return float(value) * (float(clip_dim) / float(frame_dim)) * float(
        base_scale) * float(draw_gain)


def units_for_shift(shift: float, clip_dim: float, frame_dim: float,
                    base_scale: float = NATIVE_BASE_SCALE,
                    draw_gain: float = FALLBACK_DRAW_GAIN) -> float:
    """The Pan/Tilt unit count that draws `shift` frame pixels.

    The strict inverse of :func:`shift_px`, and the reason both live
    here: a placer that inverts the law by hand is the second model.
    """
    _positive(clip_dim, frame_dim, base_scale)
    return float(shift) / ((float(clip_dim) / float(frame_dim))
                           * float(base_scale) * float(draw_gain))


def rebase_draw_gain(value: float, recorded_gain: float,
                     build_gain: float) -> float:
    """Preserve a recorded Pan/Tilt pixel shift at this build's gain.

    Transform overrides store the Resolve unit value plus the draw gain
    under which that value was chosen. The target timeline and clip
    geometry are unchanged, so the shared forward/inverse law reduces to
    `value * recorded_gain / build_gain`. This helper owns that conversion
    so an override and the picture geometry cannot invent separate laws.
    """
    values = (value, recorded_gain, build_gain)
    if any(not math.isfinite(float(part)) for part in values):
        raise ResolveTransformError(
            f"draw-gain rebasing needs finite values; got {values!r}")
    _positive(recorded_gain, build_gain)
    return float(value) * float(recorded_gain) / float(build_gain)


def drawn_centre(clip_w: float, clip_h: float,
                 frame_w: float, frame_h: float,
                 pan: float = 0.0, tilt: float = 0.0,
                 base_scale: float = NATIVE_BASE_SCALE,
                 draw_gain: float = FALLBACK_DRAW_GAIN) -> tuple:
    """Where a stored Pan/Tilt puts the clip's CENTRE, in frame pixels.

    Positive Pan moves right, positive Tilt moves up.
    """
    _positive(clip_w, clip_h, frame_w, frame_h)
    return (float(frame_w) / 2.0
            + shift_px(pan, clip_w, frame_w, base_scale, draw_gain),
            float(frame_h) / 2.0
            - shift_px(tilt, clip_h, frame_h, base_scale, draw_gain))


def drawn_origin(clip_w: float, clip_h: float,
                 frame_w: float, frame_h: float,
                 pan: float = 0.0, tilt: float = 0.0,
                 base_scale: float = NATIVE_BASE_SCALE,
                 drawn_w: float = None, drawn_h: float = None,
                 draw_gain: float = FALLBACK_DRAW_GAIN) -> tuple:
    """Where a stored Pan/Tilt puts the clip's TOP-LEFT, in frame pixels.

    `drawn_w`/`drawn_h` are the size the clip is DRAWN at where that
    differs from `clip_w`/`clip_h` - a picture clip under `scaleToFit`
    and a user zoom draws `source * fit * zoom` wide while its Pan
    still moves it by `source/frame * fit`. They default to the native
    size, which is the overlay case.
    """
    centre_x, centre_y = drawn_centre(clip_w, clip_h, frame_w, frame_h,
                                      pan, tilt, base_scale, draw_gain)
    width = float(clip_w if drawn_w is None else drawn_w)
    height = float(clip_h if drawn_h is None else drawn_h)
    return (centre_x - width / 2.0, centre_y - height / 2.0)


def pan_tilt_for_centre(clip_w: float, clip_h: float,
                        frame_w: float, frame_h: float,
                        centre_x: float, centre_y: float,
                        base_scale: float = NATIVE_BASE_SCALE,
                        draw_gain: float = FALLBACK_DRAW_GAIN) -> tuple:
    """The `(pan, tilt)` that put the clip's centre where asked.

    The inverse of :func:`drawn_centre`. Round-trips exactly.
    """
    _positive(clip_w, clip_h, frame_w, frame_h)
    return (units_for_shift(float(centre_x) - float(frame_w) / 2.0,
                            clip_w, frame_w, base_scale, draw_gain),
            units_for_shift(float(frame_h) / 2.0 - float(centre_y),
                            clip_h, frame_h, base_scale, draw_gain))
