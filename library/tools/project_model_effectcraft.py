"""Unit 7: EffectCraft adapter over the canonical Project Model.

Projects person/object masks and stable Ren effects from a Project Model
document into a pinned EffectCraft composition request, and ingests the
driver's render receipt (output, keyframes, alpha, identity mapping, and
the render's frame map) back into the model.

Ren owns the projection and the receipt types here. ``.ecproj`` files are
disposable engine working artifacts attached to a revision, never Ren's
project model (plan Units 1-2 own the contract and the store; Unit 3 owns
the evidence/identity migration; this module owns ONLY the EffectCraft
half of Unit 7). The pinned headless driver crate
(``native/ren-effectcraft``) executes the request; this module never
reimplements it and never reaches the EffectCraft UI.

Fail-closed rules:

- A Ren effect whose ``class_id`` is not in ``VERIFIED_EFFECT_MAP``
  and not a ``move.*``/fade transform class is refused with
  :class:`UnknownEffect`. A parameter the mapped EffectCraft effect
  does not declare is refused with :class:`UnknownParameter`; a
  value of the wrong JSON kind with :class:`ParameterType`. Nothing
  unknown is passed through to the engine.
- A mask that cannot be bound to exactly one footage layer is refused
  with :class:`UnprojectableMask`. A static normalized box projects
  through ``layer.addMask``; any other projectable representation -
  animated ``ellipse``/``rect`` keyframes, normalized ``face_bbox_track``
  samples, ``rle`` pixel frames - projects ONLY through a rasterized
  lossless matte sequence supplied as ``mattes[mask_id]`` (see
  :func:`write_matte_sequence`), imported as footage, conformed to the
  comp clock and bound with ``layer.setTrackMatte``. A non-static mask
  with no supplied matte, a matte frame the mask cannot cover, and an
  unknown representation are all refused, never guessed. An effect whose
  ``target_id`` names no projected clip use or mask is refused with
  :class:`UnprojectableEffectTarget`.
- A receipt whose engine revision, version, dimensions, frame count or
  alpha do not match the projection is refused with
  :class:`ReceiptMismatch`; the model is left unchanged.

Read-only on live project data: the only inputs are the model dict (or a
model JSON path) and explicit caller-supplied specs. The only writes are
the returned request/projection/model dicts and this module's own CLI
``--out`` paths. Proven on fixtures, never by rewriting a live project.

Effect map policy: ``VERIFIED_EFFECT_MAP`` carries ONLY Ren effect
classes measured against the pinned EffectCraft registry AND against
Ren's own Fusion renders. A new mapping lands only with a registry
measurement, a two-engine pixel measurement under a declared
tolerance, and a test that pins it; the driver's live
``list_effects`` registry remains the authority at run time and
anything it does not declare is refused there as well.
"""

from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import struct
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # titles wiring only; resolved by the caller
    from library.tools.safe_area import SafeAreaInsets

try:
    from library.tools.project_model import SCHEMA_VERSION, validate_project_model
    from library.tools.project_model_effectcraft_moves import (
        MOVE_CLASS_IDS,
        UnprojectableMove,
        move_comp_frames,
        project_move,
    )
    from library.tools.fusion.effects import (
        fade_blend_spline,
        sample_held_linear,
        sample_smooth_bezier,
    )
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools.project_model import SCHEMA_VERSION, validate_project_model
    from tools.project_model_effectcraft_moves import (
        MOVE_CLASS_IDS,
        UnprojectableMove,
        move_comp_frames,
        project_move,
    )
    from tools.fusion.effects import (
        fade_blend_spline,
        sample_held_linear,
        sample_smooth_bezier,
    )

ADAPTER_VERSION = 1
"""Version of this projection/ingest contract (``details.adapter_version``)."""

PINNED_EFFECTCRAFT_REV = "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
"""Immutable upstream revision qualified by the EffectCraft capability
scout. Sole pin source beside ``native/ren-effectcraft/src/lib.rs``."""

PINNED_EFFECTCRAFT_VERSION = "0.7.0"
"""Engine version the pinned CLI must report (driver's ``info`` gate)."""

REQUEST_VERSION = 1
RECEIPT_VERSION = 1
"""Driver request/receipt versions this adapter speaks."""

GRADE_PSNR_FLOOR_DB = 24.0
"""A projected grade must render within this PSNR of Ren's Fusion grade.

Measured 2026-10-10 on the EC Coverage stills (pinned CLI, 1920x1080):
single-axis laws render bit-identical to 1 LSB (gain 2.0 inf,
contrast -0.5 inf, brightness 0.67 inf, saturation 1.5 inf,
pastel combined inf); the floor binds only the gain-boosted-primaries
desaturation corner (26.7 dB, max 71 LSB in pure-primary bars), where
Fusion's BrightnessContrast and ``ec.color.ociocdl`` gamut-map
differently. Typical content scores 35 dB and up.
"""

SAT_PSNR_FLOOR_DB = 30.0
"""A projected saturation must render within this PSNR of Ren's.

Measured: desaturation 0.5 renders 35.1 dB (1 LSB everywhere but the
pure-primary bars, max 16 LSB there), boost 1.5 bit-identical, pastel
boost 1.83 at 41.3 dB.
"""

FADE_FLAT_MAX_ABS = 2.0
"""A projected fade flattened over black must match Ren's Merge blend
within this many LSBs. Measured at most 1.25 (alpha-quantization dust)
across blends 0.25/0.5/0.75, the real-ramp endpoint bit-identical, and
the real ``fade_to_black`` mid-ramp at 1.25.
"""

VERIFIED_EFFECT_MAP: dict[str, dict[str, Any]] = {
    "blur.gaussian": {
        "effect": "ec.blur.gaussian",
        "params": {"blurriness": "blurriness"},
    },
    "color.saturation": {
        "effect": "ec.color.ociocdl",
        "params": {"saturation": "saturation"},
        # Ren saturation is multiplicative with 1.0 neutral, and so is
        # the OCIO CDL saturation the pinned effect applies: the
        # identity is the scale conversion, not taste. Measured
        # strictly more exact than the previous huesaturation affine
        # (desaturation 35.1 dB vs 26 dB, boost exact where the affine
        # under-boosted by the wrong scale), so the affine is retired.
    },
    "color.grade": {
        "effect": "ec.color.ociocdl",
        # One Ren BrightnessContrast node is one CDL-style machine:
        # gain multiplies (slope), brightness adds (offset), contrast
        # pivots about mid grey (slope plus offset), saturation scales
        # chroma. The slope COMBINES gain and contrast, so no per-param
        # rename expresses it: ``project`` builds all seven engine
        # params from the whole parameter dict at once.
        "readable": ("gain", "contrast", "brightness", "saturation"),
        "project": "grade",
    },
}
"""Ren effect class to pinned EffectCraft effect mapping.

Each entry is measured against the pinned registry AND against Ren's
own Fusion renders (see ``tests/qualification/
test_effectcraft_coverage_proof.py``: every law below renders the same
chart still through both engines and compares pixels). No other Ren
class is mapped: anything without a measured law raises
:class:`UnknownEffect`, with the measured miss attached where the
sweep ran (see :data:`REFUSED_EFFECT_COVERAGE`).

Surveyed and deliberately unmapped: every Fusion effect family Ren's
pipeline draws that missed the mapping floor is named in
:data:`REFUSED_EFFECT_COVERAGE` with its measured numbers (glow tops
out at 19.9 dB, grain has no consistent intensity law, defocus blurs
at 21.0 dB, vignette breaks at full strength, flash diverges on
saturated primaries, zoom_blur's streak never approaches; tv_power and
the subject backdrop have no counterpart at all). Beyond the Fusion
families: the motion-graphics roster states dimensions, never values;
built-in Fusion clip effects and the CDL / subject-gated grades are
Resolve-side; native speed effects and stabilization travel other
routes; overlays, captions, timed text and bookends are layers and
text, not effects. Graphics IR's blur and drop-shadow filters share
concepts with ``ec.blur.gaussian`` but are a different vocabulary
(Graphics IR filter nodes, not model effects) and are not minted as
model effects by any producer, so no mapping claims them.

The plan_vfx toolkit (``slow_zoom_in/out``, ``zoom_emphasis``,
``screen_shake``, ``cut_in``) is NOT in this map either, but it is not
unmapped: ``move.*`` model classes project through
:mod:`library.tools.project_model_effectcraft_moves` onto keyframed
``transform/scale`` and ``transform/position`` layer keys - moves are
Fusion-transform vocabulary, and EffectCraft carries them as transform
animation, not as engine effects. Anything in that module that cannot
meet the declared tolerance keeps refusing with
:class:`UnprojectableMove <library.tools.project_model_effectcraft_moves.UnprojectableMove>`.

Fades are the same shape: ``fade.opacity`` and
``transition.fade_to_black`` project through :func:`_project_fade_effect`
onto keyframed ``transform/opacity`` layer keys (opacity = blend * 100,
sampled off the exact Fusion blend splines), refusing with
:class:`UnprojectableFade` where no exact ramp exists.
"""

REFUSED_EFFECT_COVERAGE: dict[str, str] = {
    "glow.soft": (
        "measured 2026-10-10: the closest fit (intensity 5, threshold "
        "20, radius 150 on ec.stylize.glow) reaches only 19.9 dB PSNR "
        "against Ren's SoftGlow(5, 0.3, 50) - different bloom "
        "algorithms, below the 35 dB mapping floor"
    ),
    "grain.film": (
        "measured 2026-10-10: no consistent intensity law - matching "
        "Ren's grain std needs EC intensity 1.84x power at power 0.25 "
        "but 3.67x at power 1.0 (Ren is sublinear from clipping, EC "
        "linear), and Ren adds light (mean +2.6/+13.7) where EC grain "
        "is zero-mean"
    ),
    "defocus.lens": (
        "measured 2026-10-10: the pinned registry declares no defocus "
        "effect, and the closest blurs top out at 21.0 dB PSNR "
        "(gaussian blurriness 8) against Ren's Defocus 3.0 - different "
        "kernels, below the 35 dB mapping floor"
    ),
    "vignette.ellipse": (
        "measured 2026-10-10: amount = 100 * blend fits one point at "
        "37.5 dB but full strength reaches only 25.5 dB, and the soft "
        "axis has no consistent law (best angleOfView drifts 40/60/80 "
        "across soft 0.15/0.35/0.6) - different falloff geometry"
    ),
    "transition.zoom_blur": (
        "measured 2026-10-10: the composite tops out at 10.5 dB PSNR "
        "and the blur leg alone at 10.1 dB (Ren's DirectionalBlur "
        "0.75 smears hundreds of pixels; the EC streak profile never "
        "approaches it), though the zoom leg alone is exact at "
        "50-67 dB - the recipe's Size and Length animate together, so "
        "the family refuses as one"
    ),
    "transition.defocus": (
        "measured 2026-10-10: same tool gap as defocus.lens (best "
        "21.0 dB); the ramp has no exact blur to ride on"
    ),
    "transition.flash": (
        "measured 2026-10-10: the best composite (one ec.color.ociocdl "
        "with offset = 0.67 * blend, saturation = 1 + 0.83 * blend) "
        "reaches 35.4 dB at half ramp but only 22.8 dB at full - "
        "saturated primaries gamut-map differently (red bar renders "
        "(255, 108, 108) vs (255, 156, 156))"
    ),
    "tv_power": (
        "Ren's old-TV switch is a PowerBand/PowerDot/PowerDecay "
        "composite with no counterpart in the pinned registry (no "
        "effect id matches)"
    ),
    "backdrop.subject": (
        "the subject backdrop is a branching Transform+Blur+Merge "
        "composite with a fixed Blur size - structure, not one effect, "
        "so projecting only its blur step would not be the picture; "
        "no single pinned effect carries it"
    ),
}
"""Ren effect classes surveyed and deliberately left unmapped.

Every family Ren's pipeline actually draws that is not natively
projected (``VERIFIED_EFFECT_MAP``, ``move.*`` transforms, fade
opacity keys) is named here with its MEASURED reason: each entry
cites the pinned-CLI sweep numbers that missed the mapping floor, or
the registry absence where no counterpart exists at all.
:func:`project_to_request` raises :class:`UnknownEffect` for these
with the reason attached, exactly as for an unknown class - the
catalog names the refusal, it never authorizes a projection. A new
Ren effect family lands either here (with its measured reason) or in
the native projection (with its conversion proof).
"""

MASK_SHAPES = ("ellipse", "rect")
"""Mask representation kinds passed through to ``layer.addMask``."""


def refusal_reason(class_id: str) -> str | None:
    """The surveyed reason a Ren effect class refuses, if it has one.

    Returns the :data:`REFUSED_EFFECT_COVERAGE` entry for a surveyed
    but unmapped class, else None for a class the survey never met
    (which refuses with the generic unknown-effect message).
    """
    return REFUSED_EFFECT_COVERAGE.get(class_id)


JSON_KINDS = ("null", "boolean", "number", "string", "array", "object")
"""JSON value-kind vocabulary shared with the driver's registry check."""


class EffectCraftAdapterError(ValueError):
    """Base refusal for the EffectCraft projection/ingest boundary."""


class UnknownEffect(EffectCraftAdapterError):
    """A Ren effect class with no verified EffectCraft mapping."""


class UnknownParameter(EffectCraftAdapterError):
    """A parameter the mapped EffectCraft effect does not declare."""


class ParameterType(EffectCraftAdapterError):
    """A parameter value of the wrong JSON kind for the mapped effect."""


class UnprojectableMask(EffectCraftAdapterError):
    """A mask with no bindable layer or no expressible representation."""


class UnprojectableEffectTarget(EffectCraftAdapterError):
    """An effect whose target is no projected clip use or mask."""


def _project_move_effect(effect: dict[str, Any],
                         layers: dict[str, dict[str, str]],
                         mask_layers: dict[str, str],
                         model: dict[str, Any], comp: CompSpec,
                         operations: list[dict[str, Any]],
                         effect_layers: dict[str, dict[str, Any]]) -> None:
    """Project one ``move.*`` effect into transform keyframe operations.

    Moves are per-clip Transforms, not engine effects: the target must
    be a projected clip use (a mask has no picture of its own to
    move), and the effect must carry no model keyframes (the curve is
    baked from its params; keyframes would double-drive it).
    """
    target = effect["target_id"]
    layer = None
    for use in model["clip_uses"]:
        if use["id"] == target:
            layer = next(
                (name for name, bound in layers.items()
                 if bound["clip_use"] == target),
                None,
            )
    if layer is None:
        if target in mask_layers:
            raise UnprojectableMove(
                f"move {effect['id']!r} ({effect['class_id']}) targets "
                f"mask {target!r}: moves are per-clip Transforms, and "
                f"a mask has no picture of its own to move"
            )
        raise UnprojectableEffectTarget(
            f"effect {effect['id']!r} target {target!r} is no projected "
            "clip use or mask"
        )
    if effect.get("keyframes"):
        raise UnprojectableMove(
            f"move {effect['id']!r} carries model keyframes with no "
            f"parameter for them to animate: the move's curve is baked "
            f"from its params, so keyframes are refused rather than "
            f"double-driven"
        )
    interval = effect["interval"]
    ticks = _ticks_per_second(model, interval["clock_id"])
    first, last = move_comp_frames(interval, ticks, comp.frame_rate,
                                   effect["id"])
    fresh = project_move(
        class_id=effect["class_id"], effect_id=effect["id"],
        parameters=effect["parameters"], layer=layer,
        first_frame=first, last_frame=last,
        comp_frames=comp.expected_frames(),
        comp_width=comp.width, comp_height=comp.height,
        frame_rate=comp.frame_rate)
    operations.extend(fresh)
    effect_layers[effect["id"]] = {
        "layer": layer,
        "move": effect["class_id"],
        "paths": sorted({op["path"] for op in fresh}),
        "frames": [first, last],
    }


class EmptyProjection(EffectCraftAdapterError):
    """A model with no masks or effects to project: nothing to render."""


class ReceiptMismatch(EffectCraftAdapterError):
    """A receipt that does not match its projection; model left unchanged."""


@dataclass
class CompSpec:
    """The EffectCraft composition to project into.

    The Project Model carries no canvas: width, height, frame rate and
    duration are the caller's explicit render intent, recorded on the
    receipt so the ingest can verify the render against them.
    """

    name: str = "Main"
    width: int = 96
    height: int = 64
    frame_rate: float = 10.0
    duration_seconds: float = 0.5

    def expected_frames(self) -> int:
        frames = int(round(self.duration_seconds * self.frame_rate))
        if frames <= 0:
            raise EffectCraftAdapterError(
                f"comp {self.name!r} must render at least one frame: "
                f"duration {self.duration_seconds}s at {self.frame_rate}fps"
            )
        return frames


@dataclass
class RenderSpec:
    """One render the driver should produce from the composition."""

    format: str = "rgba_png_sequence"
    output: str = "comp_#####.png"
    start_seconds: float | None = None
    end_seconds: float | None = None

    def validated(self) -> dict[str, Any]:
        if self.format not in ("rgba_png_sequence", "prores4444_alpha"):
            raise EffectCraftAdapterError(
                f"unknown EffectCraft render format {self.format!r}: "
                "expected rgba_png_sequence or prores4444_alpha"
            )
        if self.format == "rgba_png_sequence" and "#" not in self.output:
            raise EffectCraftAdapterError(
                "PNG sequence output must include # frame-number placeholders: "
                f"{self.output!r}"
            )
        return {
            "format": self.format,
            "comp": "",
            "output": self.output,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
        }


@dataclass
class Projection:
    """A validated EffectCraft request plus what ingest needs to read it back."""

    request: dict[str, Any]
    identity_map: dict[str, Any]
    losses: list[dict[str, str]] = field(default_factory=list)
    comp: CompSpec = field(default_factory=CompSpec)
    renders: list[RenderSpec] = field(default_factory=list)
    project_id: str = ""


def fixture_catalog() -> dict[str, dict[str, str]]:
    """EffectCraft registry snapshot for tests: pinned-verified entries only.

    Mirrors the live ``list_effects`` shape the driver reads (effect id to
    parameter name to JSON value kind) for exactly the effects in
    ``VERIFIED_EFFECT_MAP``. Production passes the live registry fetched
    from the driver instead; anything absent there is refused at run time.
    """
    return {
        "ec.blur.gaussian": {"blurriness": "number"},
        "ec.color.ociocdl": {
            "slopeRed": "number", "slopeGreen": "number",
            "slopeBlue": "number", "offsetRed": "number",
            "offsetGreen": "number", "offsetBlue": "number",
            "saturation": "number",
        },
    }


def _json_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def _convert_param(entry: dict[str, Any], ren_param: str, value: Any,
                   effect_id: str) -> Any:
    """Apply a mapping's scale conversion, if it declares one.

    Most params pass through untouched. A non-numeric or non-finite
    value under a conversion is refused, never scaled into a NaN the
    engine would read as zero.
    """
    affine = entry.get("affine", {})
    if ren_param not in affine:
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterType(
            f"invalid value type for {ren_param!r} on Ren effect "
            f"{effect_id!r}: a scaled parameter needs a number, got "
            f"{_json_kind(value)}"
        )
    mult, add = affine[ren_param]
    converted = float(value) * float(mult) + float(add)
    if not math.isfinite(converted):
        raise ParameterType(
            f"invalid value for {ren_param!r} on Ren effect "
            f"{effect_id!r}: {value!r} scales off the finite range"
        )
    return converted


GRADE_READABLE = ("gain", "contrast", "brightness", "saturation")
"""Parameter names a ``color.grade`` effect is drawn from.

Absent axes read neutral (1.0/0.0/0.0/1.0), the same reading the
subject-gated grade gives an absent key: neutrality draws nothing,
so there is nothing to substitute. An effect carrying no axis at all
is refused - a grade that moves nothing is a producer bug, not a
projection.
"""

GRADE_NEUTRAL = {"gain": 1.0, "contrast": 0.0, "brightness": 0.0,
                 "saturation": 1.0}
"""Neutral per grade axis: what an absent axis reads."""


def _project_grade_params(parameters: Any, effect_id: str
                          ) -> dict[str, Any]:
    """One Ren BrightnessContrast node as one OCIO CDL parameter set.

    Laws (measured 2026-10-10 on the EC Coverage stills, pinned CLI):
    gain multiplies (slope = gain), brightness adds
    (offset = brightness), contrast pivots about mid grey
    (slope factor 1 + contrast, offset -0.5 * contrast), saturation
    scales chroma identically on both sides. The slope combines gain
    AND contrast, and the offset combines brightness AND contrast, so
    the law reads the whole dict at once: slope = gain * (1 + contrast)
    and offset = brightness - 0.5 * contrast, per channel. Proven
    within :data:`GRADE_PSNR_FLOOR_DB` (single-axis laws bit-identical
    to 1 LSB; the combined law exact on in-gamut content).
    """
    if not isinstance(parameters, dict) or not parameters:
        raise EffectCraftAdapterError(
            f"effect {effect_id!r} (color.grade) carries no parameters: "
            "a grade that moves nothing is refused, never projected "
            "neutral by default"
        )
    for name in parameters:
        if name not in GRADE_READABLE:
            raise UnknownParameter(
                f"unknown parameter {name!r} for Ren effect class "
                f"'color.grade' (effect {effect_id!r})"
            )
    values: dict[str, float] = {}
    for name in GRADE_READABLE:
        raw = parameters.get(name, GRADE_NEUTRAL[name])
        if (isinstance(raw, bool) or not isinstance(raw, (int, float))
                or not math.isfinite(float(raw))):
            raise ParameterType(
                f"invalid value type for {name!r} on Ren effect "
                f"{effect_id!r}: a grade axis needs a finite number, "
                f"got {_json_kind(raw)}"
            )
        values[name] = float(raw)
    slope = round(values["gain"] * (1.0 + values["contrast"]), 6)
    offset = round(values["brightness"] - 0.5 * values["contrast"], 6)
    if not math.isfinite(slope) or not math.isfinite(offset):
        raise ParameterType(
            f"invalid value for color.grade on Ren effect "
            f"{effect_id!r}: the slope/offset compose off the finite "
            "range"
        )
    return {
        "slopeRed": slope, "slopeGreen": slope, "slopeBlue": slope,
        "offsetRed": offset, "offsetGreen": offset, "offsetBlue": offset,
        "saturation": values["saturation"],
    }


FADE_CLASS_IDS = ("fade.opacity", "transition.fade_to_black")
"""Model effect classes carried as opacity keyframes, not engine effects.

A fade is a Merge-blend animation over black, and EffectCraft carries
it as ``transform/opacity`` keys: opacity = blend * 100, flattened
over black within :data:`FADE_FLAT_MAX_ABS` (measured). Like moves,
fades take no effect slot and need no registry entry.
"""


class UnprojectableFade(ValueError):
    """A fade with no exact opacity-keyframe projection.

    The message names the reason (mask target, carried keyframes, an
    empty fade that draws nothing, a ramp longer than its span, bad
    params). Nothing unmappable is approximated.
    """


def _require_fade_frames(value: Any, effect_id: str, class_id: str,
                         name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) param {name!r} is not a "
            f"whole frame count: {value!r}")
    if value < 0:
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) param {name!r} is "
            f"{value}: a negative ramp has no length")
    return value


def _fade_blend_dense(class_id: str, effect_id: str,
                      parameters: dict[str, Any],
                      first_frame: int, last_frame: int) -> dict[int, float]:
    """Blend per comp frame in [first_frame, last_frame], 0..1.

    ``fade.opacity`` samples :func:`fade_blend_spline` - the exact
    spline ``fx.fade`` keys - through :func:`sample_smooth_bezier`,
    because the in-ramp and out-ramp carry different handles and no
    assumed symmetry reproduces them. ``transition.fade_to_black``
    samples its recipe's linearized two-key ramp piecewise-linearly,
    which is what ``linearize`` forces Resolve to render; the
    endpoints (1.0 to 0.0 tail, 0.0 to 1.0 head) are the recipe's own,
    pinned by a test that reads them off the built block.
    """
    span = last_frame - first_frame + 1
    if class_id == "fade.opacity":
        for name in parameters:
            if name not in ("fade_in_frames", "fade_out_frames"):
                raise UnprojectableFade(
                    f"fade {effect_id!r} ({class_id}) param {name!r} is "
                    "read by nothing; a fade is drawn from "
                    "fade_in_frames and fade_out_frames")
        if "fade_in_frames" not in parameters \
                and "fade_out_frames" not in parameters:
            raise UnprojectableFade(
                f"fade {effect_id!r} ({class_id}) names neither "
                "fade_in_frames nor fade_out_frames: which end ramps "
                "is the fade's decision and has no default")
        fade_in = _require_fade_frames(
            parameters.get("fade_in_frames", 0), effect_id, class_id,
            "fade_in_frames")
        fade_out = _require_fade_frames(
            parameters.get("fade_out_frames", 0), effect_id, class_id,
            "fade_out_frames")
        if fade_in <= 0 and fade_out <= 0:
            raise UnprojectableFade(
                f"fade {effect_id!r} ({class_id}) ramps nowhere: "
                "fx.fade builds an empty block for it, so there is "
                "nothing to project")
        if fade_in > span or fade_out > span:
            raise UnprojectableFade(
                f"fade {effect_id!r} ({class_id}) ramp "
                f"({max(fade_in, fade_out)} frames) is longer than its "
                f"{span}-frame span: it never reaches neutral")
        spline = fade_blend_spline(first_frame, last_frame,
                                   fade_in, fade_out)
        try:
            dense = sample_smooth_bezier(spline.keyframes, first_frame,
                                         last_frame)
        except ValueError as exc:
            raise UnprojectableFade(
                f"fade {effect_id!r} ({class_id}) curve refused: "
                f"{exc}") from exc
        return {frame: round(value, 6) for frame, value in dense.items()}

    for name in parameters:
        if name not in ("duration_frames", "direction"):
            raise UnprojectableFade(
                f"fade {effect_id!r} ({class_id}) param {name!r} is "
                "read by nothing; a transition fade is drawn from "
                "duration_frames and direction")
    if "duration_frames" not in parameters:
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) has no "
            "params.duration_frames: the ramp length is the fade's "
            "decision and has no default")
    duration = parameters["duration_frames"]
    if isinstance(duration, bool) or not isinstance(duration, int):
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) param 'duration_frames' "
            f"is not a whole frame count: {duration!r}")
    direction = parameters.get("direction", "tail")
    if direction not in ("head", "tail"):
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) direction "
            f"{direction!r} is not 'head' or 'tail'")
    if duration < 1:
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) duration {duration} "
            "ramps nowhere")
    if duration > span:
        raise UnprojectableFade(
            f"fade {effect_id!r} ({class_id}) duration {duration} is "
            f"longer than its {span}-frame span: it never reaches "
            "neutral")
    if direction == "tail":
        held = {last_frame - duration: 1.0, last_frame: 0.0}
    else:
        held = {first_frame: 0.0, first_frame + duration: 1.0}
    return sample_held_linear(held, first_frame, last_frame)


def _project_fade_effect(effect: dict[str, Any],
                         layers: dict[str, dict[str, str]],
                         mask_layers: dict[str, str],
                         model: dict[str, Any], comp: CompSpec,
                         operations: list[dict[str, Any]],
                         effect_layers: dict[str, dict[str, Any]]) -> None:
    """Project one fade effect into opacity keyframe operations.

    Fades are per-clip opacity animation, not engine effects: the
    target must be a projected clip use (a mask has no picture of its
    own to fade), and the effect must carry no model keyframes (the
    ramp is baked from its params; keyframes would double-drive it).
    Frames outside the ramps hold 100 (fully on), mirroring the Merge
    holds, so a fade on one span never dims the rest of the comp.
    """
    target = effect["target_id"]
    layer = None
    for use in model["clip_uses"]:
        if use["id"] == target:
            layer = next(
                (name for name, bound in layers.items()
                 if bound["clip_use"] == target),
                None,
            )
    if layer is None:
        if target in mask_layers:
            raise UnprojectableFade(
                f"fade {effect['id']!r} ({effect['class_id']}) targets "
                f"mask {target!r}: fades are per-clip opacity, and "
                f"a mask has no picture of its own to fade"
            )
        raise UnprojectableEffectTarget(
            f"effect {effect['id']!r} target {target!r} is no projected "
            "clip use or mask"
        )
    if effect.get("keyframes"):
        raise UnprojectableFade(
            f"fade {effect['id']!r} carries model keyframes with no "
            f"parameter for them to animate: the ramp is baked from "
            f"its params, so keyframes are refused rather than "
            f"double-driven"
        )
    if not isinstance(effect.get("parameters"), dict):
        raise UnprojectableFade(
            f"fade {effect['id']!r} ({effect['class_id']}) parameters "
            f"are not a mapping: {effect.get('parameters')!r}")
    interval = effect["interval"]
    ticks = _ticks_per_second(model, interval["clock_id"])
    # The touch rule is shared with moves (the same frame owns the same
    # picture); only the refusal names the fade.
    first, last = move_comp_frames(interval, ticks, comp.frame_rate,
                                   effect["id"], error=UnprojectableFade)
    if first < 0 or last >= comp.expected_frames():
        raise UnprojectableFade(
            f"fade {effect['id']!r} frames [{first}, {last}] do not fit "
            f"comp frames [0, {comp.expected_frames() - 1}]")
    dense = _fade_blend_dense(effect["class_id"], effect["id"],
                              effect["parameters"], first, last)
    ops: list[dict[str, Any]] = []
    if first > 0:
        ops.append({"op": "keyframe", "layer": layer,
                    "path": "transform/opacity",
                    "time": 0.0, "value": 100.0})
        if first - 1 > 0:
            ops.append({"op": "keyframe", "layer": layer,
                        "path": "transform/opacity",
                        "time": (first - 1) / comp.frame_rate,
                        "value": 100.0})
    for frame in range(first, last + 1):
        ops.append({"op": "keyframe", "layer": layer,
                    "path": "transform/opacity",
                    "time": frame / comp.frame_rate,
                    "value": round(dense[frame], 6) * 100.0})
    if last < comp.expected_frames() - 1:
        if last + 1 < comp.expected_frames() - 1:
            ops.append({"op": "keyframe", "layer": layer,
                        "path": "transform/opacity",
                        "time": (last + 1) / comp.frame_rate,
                        "value": 100.0})
        ops.append({"op": "keyframe", "layer": layer,
                    "path": "transform/opacity",
                    "time": (comp.expected_frames() - 1) / comp.frame_rate,
                    "value": 100.0})
    operations.extend(sorted(ops, key=lambda op: op["time"]))
    effect_layers[effect["id"]] = {
        "layer": layer,
        "fade": effect["class_id"],
        "paths": ["transform/opacity"],
        "frames": [first, last],
    }


def _clocks(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {clock["id"]: clock for clock in model["clocks"]}


def _ticks_per_second(model: dict[str, Any], clock_id: str) -> int:
    clocks = _clocks(model)
    if clock_id not in clocks:
        raise EffectCraftAdapterError(f"unknown clock {clock_id!r}")
    tps = clocks[clock_id]["ticks_per_second"]
    if tps <= 0:
        raise EffectCraftAdapterError(f"clock {clock_id!r} has no positive tick rate")
    return int(tps)


def _tick_to_seconds(model: dict[str, Any], clock_id: str, tick: int) -> float:
    return float(tick) / _ticks_per_second(model, clock_id)


def _layer_name(asset_uri: str) -> str:
    name = asset_uri.strip().replace("\\", "/").split("/")[-1]
    if not name:
        raise EffectCraftAdapterError(f"asset uri {asset_uri!r} names no importable file")
    return name


def _mask_box_pixels(mask: dict[str, Any], comp: CompSpec) -> list[float]:
    """A mask's static normalized box as EffectCraft pixel rect [x,y,w,h].

    Refuses animated masks: distinct keyframes would need a mask-rect
    animation path the pinned CLI has no measured vocabulary for, and
    projecting only the first frame would silently mistime the mask.
    """
    representation = mask["representation"]
    if not isinstance(representation, dict):
        raise UnprojectableMask(
            f"mask {mask['id']!r} representation is not a normalized box "
            "(RLE/URI masks have no EffectCraft rect projection)"
        )
    kind = representation.get("kind")
    if kind not in MASK_SHAPES:
        raise UnprojectableMask(
            f"mask {mask['id']!r} kind {kind!r} is not a projectable shape "
            f"(expected one of {MASK_SHAPES})"
        )
    boxes = [representation.get("normalized_bbox")]
    keyframes = representation.get("keyframes") or []
    boxes.extend(item.get("bbox") for item in keyframes)
    if any(not isinstance(box, list) or len(box) != 4 for box in boxes):
        raise UnprojectableMask(
            f"mask {mask['id']!r} has no complete [x, y, w, h] normalized box"
        )
    first = boxes[0]
    if any(
        any(abs(a - b) > 1e-9 for a, b in zip(first, box)) for box in boxes[1:]
    ):
        raise UnprojectableMask(
            f"mask {mask['id']!r} is animated across keyframes; the pinned "
            "EffectCraft CLI has no measured mask-rect animation path"
        )
    try:
        x, y, w, h = (float(v) for v in first)
    except (TypeError, ValueError) as exc:
        raise UnprojectableMask(
            f"mask {mask['id']!r} box is not numeric: {first!r}"
        ) from exc
    if not all(math.isfinite(v) for v in (x, y, w, h)):
        raise UnprojectableMask(f"mask {mask['id']!r} box is not finite: {first!r}")
    if w <= 0 or h <= 0:
        raise UnprojectableMask(f"mask {mask['id']!r} box has no area: {first!r}")
    return [x * comp.width, y * comp.height, w * comp.width, h * comp.height]


TRACK_MATTE_KINDS = ("alpha", "alphaInverted", "luma", "lumaInverted")
"""Track-matte kinds the pinned driver accepts on ``set_track_matte``."""


@dataclass
class MatteSequence:
    """A rasterized mask at the comp's frame clock.

    ``files`` are lossless RGBA PNGs, white with the mask in the alpha
    channel, ``count`` consecutive comp frames starting at
    ``first_frame``. ``item_name`` is the EffectCraft footage name the
    numbered stills import under (``{stem}_[00000-00004].png``), computed
    from the pinned engine's sequence naming, not guessed per run.
    """

    mask_id: str
    stem: str
    first_frame: int
    count: int
    files: list[str] = field(default_factory=list)
    item_name: str = ""
    width: int = 0
    height: int = 0

    def as_json(self) -> dict[str, Any]:
        return {
            "mask_id": self.mask_id,
            "stem": self.stem,
            "first_frame": self.first_frame,
            "count": self.count,
            "files": list(self.files),
            "item_name": self.item_name,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> MatteSequence:
        return cls(
            mask_id=str(raw["mask_id"]),
            stem=str(raw["stem"]),
            first_frame=int(raw["first_frame"]),
            count=int(raw["count"]),
            files=[str(p) for p in raw["files"]],
            item_name=str(raw["item_name"]),
            width=int(raw["width"]),
            height=int(raw["height"]),
        )


def _mask_is_static(mask: dict[str, Any]) -> bool:
    """Whether the mask is one unchanging box for ``layer.addMask``.

    Structural only: kind, box presence and keyframe equality. Numeric
    validation stays in :func:`_mask_box_pixels`, which raises the
    precise refusal for a static-shaped but non-numeric box.
    """
    representation = mask["representation"]
    if not isinstance(representation, dict):
        return False
    if representation.get("kind") not in MASK_SHAPES:
        return False
    base = representation.get("normalized_bbox")
    if not isinstance(base, list) or len(base) != 4:
        return False
    for item in representation.get("keyframes") or []:
        box = item.get("bbox") if isinstance(item, dict) else None
        if not isinstance(box, list) or len(box) != 4:
            return False
        try:
            if any(abs(a - b) > 1e-9 for a, b in zip(base, box)):
                return False
        except TypeError:
            return True
    return True


def _valid_normalized_box(box: Any, mask_id: str) -> list[float]:
    if not isinstance(box, list) or len(box) != 4:
        raise UnprojectableMask(
            f"mask {mask_id!r} has no complete [x, y, w, h] normalized box"
        )
    try:
        values = [float(v) for v in box]
    except (TypeError, ValueError) as exc:
        raise UnprojectableMask(
            f"mask {mask_id!r} box is not numeric: {box!r}"
        ) from exc
    if not all(math.isfinite(v) for v in values):
        raise UnprojectableMask(f"mask {mask_id!r} box is not finite: {box!r}")
    x, y, w, h = values
    if w <= 0 or h <= 0:
        raise UnprojectableMask(f"mask {mask_id!r} box has no area: {box!r}")
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0
            and x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9):
        raise UnprojectableMask(
            f"mask {mask_id!r} box {box!r} is not normalized to the frame; "
            "pixel-space tracks have no exact rasterization without a "
            "declared frame size, so they are refused"
        )
    return values


def _mask_comp_frames(mask: dict[str, Any], model: dict[str, Any],
                      comp: CompSpec) -> tuple[int, int]:
    """Comp frames the mask interval covers, from the model clocks.

    Frame ``i`` plays ``[i / fps, (i + 1) / fps)``; the matte covers
    every frame the mask's half-open tick interval touches. Exact
    rational arithmetic: a frame the interval only touches at its edge
    is still covered, never rounded away.
    """
    if not math.isfinite(comp.frame_rate) or comp.frame_rate <= 0:
        raise EffectCraftAdapterError(
            f"comp {comp.name!r} frame rate must be positive: {comp.frame_rate!r}"
        )
    interval = mask["interval"]
    tps = _ticks_per_second(model, interval["clock_id"])
    fps = Fraction(comp.frame_rate)
    start, end = int(interval["start"]), int(interval["end"])
    if end <= start:
        raise UnprojectableMask(
            f"mask {mask['id']!r} interval is empty; nothing to rasterize"
        )
    first = math.floor(Fraction(start) * fps / tps)
    last = math.ceil(Fraction(end) * fps / tps) - 1
    if last < first:
        raise UnprojectableMask(
            f"mask {mask['id']!r} covers no comp frame at {comp.frame_rate}fps"
        )
    return first, last


def _sample_tick(frame: int, tps: int, fps: Fraction) -> int:
    """The mask-clock tick a comp frame samples: its opening edge."""
    return math.floor(Fraction(frame * tps) / fps)


def _bbox_states(representation: dict[str, Any], mask_id: str,
                 tps: int) -> list[tuple[int, list[float]]]:
    """Keyframe (tick, box) states for an animated box or face track."""
    kind = representation.get("kind")
    if kind in MASK_SHAPES:
        base = _valid_normalized_box(
            representation.get("normalized_bbox"), mask_id)
        states = [(_key_tick(item, mask_id), _valid_normalized_box(
            item.get("bbox"), mask_id)) for item in
            (representation.get("keyframes") or [])]
        if not states:
            return [(0, base)]
        return sorted(states, key=lambda s: s[0])
    if kind == "face_bbox_track":
        samples = representation.get("samples") or []
        if not samples:
            raise UnprojectableMask(
                f"mask {mask_id!r} face track carries no samples"
            )
        states = []
        for sample in samples:
            if not isinstance(sample, dict):
                raise UnprojectableMask(
                    f"mask {mask_id!r} face track sample is not a mapping"
                )
            try:
                tick = math.floor(float(sample["t"]) * tps)
            except (TypeError, ValueError, KeyError) as exc:
                raise UnprojectableMask(
                    f"mask {mask_id!r} face track sample has no numeric time"
                ) from exc
            states.append((tick, _valid_normalized_box(
                sample.get("box"), mask_id)))
        return sorted(states, key=lambda s: s[0])
    raise UnprojectableMask(
        f"mask {mask_id!r} kind {kind!r} has no matte rasterization "
        f"(expected one of {MASK_SHAPES} or face_bbox_track)"
    )


def _key_tick(item: Any, mask_id: str) -> int:
    if not isinstance(item, dict):
        raise UnprojectableMask(
            f"mask {mask_id!r} keyframe is not a mapping")
    try:
        return int(item["tick"])
    except (TypeError, ValueError, KeyError) as exc:
        raise UnprojectableMask(
            f"mask {mask_id!r} keyframe has no integer tick") from exc


def _lerp_box(states: list[tuple[int, list[float]]], tick: int,
              mask_id: str) -> list[float]:
    """Piecewise-linear box at a tick; outside the span is refused."""
    if tick < states[0][0] or tick > states[-1][0]:
        raise UnprojectableMask(
            f"mask {mask_id!r} has no keyframe covering tick {tick}; "
            "a matte frame there would be invented, so it is refused"
        )
    for (tick_a, box_a), (tick_b, box_b) in itertools.pairwise(states):
        if tick_a <= tick <= tick_b:
            if tick_b == tick_a:
                return list(box_a)
            alpha = (tick - tick_a) / (tick_b - tick_a)
            return [a + (b - a) * alpha for a, b in zip(box_a, box_b)]
    return list(states[-1][1])


def _decode_rle(rle: str, width: int, height: int, mask_id: str) -> bytes:
    """Decode Ren's ``"start length,..."`` RLE into alpha bytes.

    The same flat format ``analysis.object_segmentation.decode_rle``
    reads for 1.06 subject masks, decoded here without the analysis
    dependency: runs outside the frame are refused, never clipped.
    """
    total = width * height
    flat = bytearray(total)
    if rle:
        for part in rle.split(","):
            try:
                start_s, length_s = part.strip().split()
                start, length = int(start_s), int(length_s)
            except ValueError as exc:
                raise UnprojectableMask(
                    f"mask {mask_id!r} RLE run {part!r} is not "
                    "'start length'") from exc
            if start < 0 or length < 0 or start + length > total:
                raise UnprojectableMask(
                    f"mask {mask_id!r} RLE run {part!r} leaves the "
                    f"{width}x{height} frame; refusing rather than clipping"
                )
            flat[start:start + length] = b"\x01" * length
    return bytes(b * 255 for b in flat)


def _rasterize_box(kind: str, box: list[float], width: int,
                   height: int) -> bytes:
    """Binary alpha for one normalized box: pixel centers decide."""
    x, y, w, h = box
    alpha = bytearray(width * height)
    if kind == "rect":
        for py in range(height):
            center_y = (py + 0.5) / height
            if not (y <= center_y < y + h):
                continue
            for px in range(width):
                center_x = (px + 0.5) / width
                if x <= center_x < x + w:
                    alpha[py * width + px] = 255
    else:
        cx, cy, rx, ry = x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0
        for py in range(height):
            center_y = (py + 0.5) / height
            dy = (center_y - cy) / ry
            for px in range(width):
                center_x = (px + 0.5) / width
                dx = (center_x - cx) / rx
                if dx * dx + dy * dy <= 1.0:
                    alpha[py * width + px] = 255
    return bytes(alpha)


def _write_rgba_png(path: Path, width: int, height: int,
                    alpha: bytes) -> None:
    """Lossless white RGBA PNG carrying the mask in its alpha channel.

    Standard library only: 8-bit color type 6, no filtering, so the
    matte writer never depends on an imaging stack version.
    """
    if len(alpha) != width * height:
        raise EffectCraftAdapterError("matte alpha does not fill its frame")
    rows = b"".join(
        b"\x00" + b"".join(
            b"\xff\xff\xff" + alpha[row * width + col:row * width + col + 1]
            for col in range(width)
        )
        for row in range(height)
    )

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )


def write_matte_sequence(
    mask: dict[str, Any],
    model: dict[str, Any],
    comp: CompSpec,
    out_dir: str | Path,
    *,
    stem: str | None = None,
) -> MatteSequence:
    """Rasterize a non-static mask into a lossless matte PNG sequence.

    One file per comp frame the mask interval touches, at the comp's
    width, height and frame clock, so EffectCraft can carry the mask as
    a track matte with no approximation. Animated boxes interpolate
    linearly between keyframes; RLE frames hold Ren's own held-matte
    semantic (latest frame at or before the sample). Any frame the
    model cannot cover - outside the keyframe span, before the first
    RLE frame, RLE dims off the comp, pixel-space boxes - is refused
    with :class:`UnprojectableMask`, not filled by a guess.
    """
    validate_project_model(model)
    mask_id = mask["id"]
    representation = mask["representation"]
    if not isinstance(representation, dict):
        raise UnprojectableMask(
            f"mask {mask_id!r} representation is not a mapping; "
            "only animated boxes, face tracks and RLE frames rasterize"
        )
    if comp.width <= 0 or comp.height <= 0:
        raise EffectCraftAdapterError("matte frame dimensions must be positive")
    first, last = _mask_comp_frames(mask, model, comp)
    tps = _ticks_per_second(model, mask["interval"]["clock_id"])
    fps = Fraction(comp.frame_rate)

    kind = representation.get("kind")
    if _mask_is_static(mask):
        # One unchanging box: every frame rasterizes the same state, so
        # no keyframe lookup can fail. This is what the addMask fast
        # path draws; the matte carries identical pixels per frame.
        base = _valid_normalized_box(
            representation.get("normalized_bbox"), mask_id)
        box = _rasterize_box(kind, base, comp.width, comp.height)
        alphas = [box for _ in range(first, last + 1)]
    elif kind == "rle":
        try:
            width, height = int(representation["width"]), int(
                representation["height"])
        except (TypeError, ValueError, KeyError) as exc:
            raise UnprojectableMask(
                f"mask {mask_id!r} RLE frame has no integer dimensions"
            ) from exc
        if width != comp.width or height != comp.height:
            raise UnprojectableMask(
                f"mask {mask_id!r} RLE is {width}x{height} but the comp is "
                f"{comp.width}x{comp.height}; resampling would approximate, "
                "so it is refused"
            )
        raw_frames = representation.get("frames") or []
        if not raw_frames:
            raise UnprojectableMask(
                f"mask {mask_id!r} RLE carries no frames")
        frames = sorted(
            ((_key_tick(item, mask_id),
              _rle_string(item, mask_id)) for item in raw_frames),
            key=lambda f: f[0])
        alphas = []
        for frame in range(first, last + 1):
            tick = _sample_tick(frame, tps, fps)
            covered = [run for run in frames if run[0] <= tick]
            if not covered:
                raise UnprojectableMask(
                    f"mask {mask_id!r} has no RLE frame at or before tick "
                    f"{tick} (comp frame {frame}); refusing rather than "
                    "inventing one"
                )
            alphas.append(_decode_rle(covered[-1][1], width, height, mask_id))
    elif isinstance(kind, str):
        states = _bbox_states(representation, mask_id, tps)
        alphas = [
            _rasterize_box(
                kind if kind in MASK_SHAPES else "rect",
                _lerp_box(states, _sample_tick(frame, tps, fps), mask_id),
                comp.width, comp.height)
            for frame in range(first, last + 1)
        ]
    else:
        raise UnprojectableMask(
            f"mask {mask_id!r} kind {kind!r} has no matte rasterization"
        )

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    name_stem = stem or f"{mask_id}_matte"
    files: list[str] = []
    for offset in range(last - first + 1):
        path = directory / f"{name_stem}_{first + offset:05d}.png"
        _write_rgba_png(path, comp.width, comp.height,
                        alphas[offset])
        files.append(str(path))
    return MatteSequence(
        mask_id=mask_id,
        stem=name_stem,
        first_frame=first,
        count=last - first + 1,
        files=files,
        item_name=f"{name_stem}_[{first:05d}-{last:05d}].png",
        width=comp.width,
        height=comp.height,
    )


def _rle_string(item: Any, mask_id: str) -> str:
    if not isinstance(item, dict) or not isinstance(item.get("rle"), str):
        raise UnprojectableMask(
            f"mask {mask_id!r} RLE frame carries no RLE string")
    return item["rle"]


def project_to_request(
    model: dict[str, Any],
    comp: CompSpec,
    renders: list[RenderSpec],
    *,
    effect_map: dict[str, dict[str, Any]] | None = None,
    catalog: dict[str, dict[str, str]] | None = None,
    media_root: str | Path | None = None,
    project_id: str | None = None,
    mattes: dict[str, MatteSequence] | None = None,
    titles: dict[str, Any] | None = None,
    titles_fps: float | None = None,
    titles_safe_area: SafeAreaInsets | None = None,
    titles_comp_start_seconds: float = 0.0,
) -> Projection:
    """Project model masks and effects into an EffectCraft driver request.

    Returns the driver-compatible request dict (the exact serde shape
    ``native/ren-effectcraft`` speaks: ``op``-tagged operations,
    snake_case render formats), the identity map ingest stores on the
    receipt, and the loss report of what the engine could not represent.
    Raises instead of passing anything unknown through.

    ``titles`` is an optional titles document for
    :mod:`library.tools.project_model_effectcraft_titles` (audit G3):
    ``timed_text_overlay`` moments and text-bearing motion-graphics
    entries projected into ``AddText``/``Keyframe`` ops appended after
    the footage work. It needs ``titles_fps`` (the plan fps the title
    frames were resolved at) and ``titles_safe_area`` (a
    ``SafeAreaInsets``, resolved by the caller against the comp frame);
    without both, titles are refused rather than placed by a default.
    """
    validate_project_model(model)
    mapping = VERIFIED_EFFECT_MAP if effect_map is None else effect_map
    registry = fixture_catalog() if catalog is None else catalog
    if not renders:
        raise EffectCraftAdapterError("at least one render spec is required")
    if not model["masks"] and not model["effects"] and titles is None:
        raise EmptyProjection("model has no masks or effects to project")

    model_id = model["id"]
    request_id = project_id or f"{model_id}-effectcraft"
    operations: list[dict[str, Any]] = []
    losses: list[dict[str, str]] = []
    layers: dict[str, dict[str, str]] = {}
    mask_layers: dict[str, str] = {}
    matte_records: dict[str, dict[str, Any]] = {}
    effect_layers: dict[str, dict[str, Any]] = {}
    entity_layers: dict[str, list[str]] = {}

    operations.append({
        "op": "new_comp",
        "name": comp.name,
        "width": comp.width,
        "height": comp.height,
        "frame_rate": comp.frame_rate,
        "duration": comp.duration_seconds,
    })

    assets = {asset["id"]: asset for asset in model["assets"]}
    for use in sorted(model["clip_uses"], key=lambda u: u["id"]):
        asset = assets.get(use["asset_id"])
        if asset is None:
            raise EffectCraftAdapterError(
                f"clip use {use['id']!r} names unknown asset {use['asset_id']!r}"
            )
        layer = _layer_name(asset["uri"])
        if layer in layers:
            continue
        path = str(Path(media_root) / asset["uri"]) if media_root else asset["uri"]
        operations.append({"op": "import_footage", "path": path})
        operations.append({"op": "add_footage_layer", "footage": layer})
        layers[layer] = {"clip_use": use["id"], "asset": asset["id"]}
        for entity_id in use.get("entity_links", []):
            entity_layers.setdefault(entity_id, [])
            if layer not in entity_layers[entity_id]:
                entity_layers[entity_id].append(layer)

    for mask in sorted(model["masks"], key=lambda m: m["id"]):
        candidates = entity_layers.get(mask["entity_id"], [])
        if len(candidates) != 1:
            raise UnprojectableMask(
                f"mask {mask['id']!r} entity {mask['entity_id']!r} binds to "
                f"{len(candidates)} footage layers; exactly one is required"
            )
        layer = candidates[0]
        if _mask_is_static(mask):
            rect = _mask_box_pixels(mask, comp)
            kind = mask["representation"]["kind"]
            operations.append({
                "op": "add_mask",
                "layer": layer,
                "shape": kind,
                "rect": rect,
            })
            mask_layers[mask["id"]] = layer
            continue
        sequence = (mattes or {}).get(mask["id"])
        if sequence is None:
            raise UnprojectableMask(
                f"mask {mask['id']!r} is not a static box; rasterize it with "
                "write_matte_sequence and pass it as mattes[mask_id] - "
                "the pinned CLI has no animated-mask path, so projecting "
                "only one frame would silently mistime it"
            )
        _require_matte(sequence, mask, model, comp)
        matte_layer = sequence.item_name
        operations.append({"op": "import_footage", "path": sequence.files[0]})
        operations.append({"op": "interpret_footage",
                           "frame_rate": comp.frame_rate})
        add_layer: dict[str, Any] = {"op": "add_footage_layer",
                                     "footage": matte_layer}
        if sequence.first_frame > 0:
            add_layer["time"] = sequence.first_frame / comp.frame_rate
        operations.append(add_layer)
        operations.append({"op": "set_track_matte", "layer": layer,
                           "matte": matte_layer, "kind": "alpha"})
        mask_layers[mask["id"]] = layer
        matte_records[mask["id"]] = sequence.as_json()

    effect_index_per_layer: dict[str, int] = {}
    for effect in sorted(model["effects"], key=lambda e: e["id"]):
        if effect["class_id"] in MOVE_CLASS_IDS:
            # A move is transform animation, not an engine effect: it
            # takes no effect slot and needs no registry entry (the
            # driver's keyframe path carries it). Anything it cannot
            # express exactly refuses with UnprojectableMove.
            _project_move_effect(effect, layers, mask_layers, model,
                                 comp, operations, effect_layers)
            continue
        if effect["class_id"] in FADE_CLASS_IDS:
            # A fade is opacity animation, not an engine effect: the
            # driver's keyframe path carries it on transform/opacity.
            # Anything it cannot express exactly refuses with
            # UnprojectableFade.
            _project_fade_effect(effect, layers, mask_layers, model,
                                 comp, operations, effect_layers)
            continue
        entry = mapping.get(effect["class_id"])
        if entry is None:
            message = (
                f"Ren effect class {effect['class_id']!r} (effect "
                f"{effect['id']!r}) has no verified EffectCraft mapping; "
                "refusing rather than passing it through"
            )
            surveyed = refusal_reason(effect["class_id"])
            if surveyed is not None:
                message += f". Surveyed and deliberately unmapped: {surveyed}"
            raise UnknownEffect(message)
        effectcraft_id = entry["effect"]
        spec = registry.get(effectcraft_id)
        if spec is None:
            raise UnknownEffect(
                f"EffectCraft effect {effectcraft_id!r} (Ren effect "
                f"{effect['id']!r}) is not in the registry snapshot"
            )
        target = effect["target_id"]
        layer = None
        for use in model["clip_uses"]:
            if use["id"] == target:
                layer = next(
                    (name for name, bound in layers.items()
                     if bound["clip_use"] == target),
                    None,
                )
        if layer is None and target in mask_layers:
            layer = mask_layers[target]
        if layer is None:
            raise UnprojectableEffectTarget(
                f"effect {effect['id']!r} target {target!r} is no projected "
                "clip use or mask"
            )
        engine_params: dict[str, Any] = {}
        if entry.get("project") == "grade":
            # The slope combines gain AND contrast and the offset
            # combines brightness AND contrast, so the law reads the
            # whole parameter dict at once rather than renaming
            # param-by-param. Unknown and empty params already refuse
            # inside the projector; only the JSON kinds are checked
            # here, against the live snapshot like every other entry.
            engine_params = _project_grade_params(
                effect["parameters"], effect["id"])
            for engine_param, value in engine_params.items():
                if engine_param not in spec:
                    raise UnknownParameter(
                        f"unknown parameter {engine_param!r} for EffectCraft "
                        f"effect {effectcraft_id!r} (effect {effect['id']!r})"
                    )
                actual, expected = _json_kind(value), spec[engine_param]
                if actual != expected:
                    raise ParameterType(
                        f"invalid value type for {engine_param!r} on "
                        f"EffectCraft effect {effectcraft_id!r}: expected "
                        f"{expected}, got {actual} (effect {effect['id']!r})"
                    )
        else:
            param_map = entry.get("params", {})
            for ren_param, value in effect["parameters"].items():
                if ren_param not in param_map:
                    raise UnknownParameter(
                        f"unknown parameter {ren_param!r} for Ren effect class "
                        f"{effect['class_id']!r} (effect {effect['id']!r})"
                    )
                engine_param = param_map[ren_param]
                if engine_param not in spec:
                    raise UnknownParameter(
                        f"unknown parameter {engine_param!r} for EffectCraft "
                        f"effect {effectcraft_id!r} (effect {effect['id']!r})"
                    )
                actual, expected = _json_kind(value), spec[engine_param]
                if actual != expected:
                    raise ParameterType(
                        f"invalid value type for {engine_param!r} on EffectCraft "
                        f"effect {effectcraft_id!r}: expected {expected}, got "
                        f"{actual} (effect {effect['id']!r})"
                    )
                engine_params[engine_param] = _convert_param(
                    entry, ren_param, value, effect["id"])
        effect_index_per_layer[layer] = effect_index_per_layer.get(layer, 0) + 1
        index = effect_index_per_layer[layer]
        operations.append({
            "op": "apply_effect",
            "layer": layer,
            "effect_id": effectcraft_id,
            "params": engine_params,
        })
        effect_layers[effect["id"]] = {
            "layer": layer,
            "effect_index": index,
            "effectcraft_id": effectcraft_id,
        }
        keyframes = effect.get("keyframes", [])
        if keyframes and len(engine_params) != 1:
            raise EffectCraftAdapterError(
                f"effect {effect['id']!r} keyframes name no parameter and the "
                f"mapped effect declares {len(engine_params)}; refusing an "
                "ambiguous keyframe target"
            )
        interval = effect["interval"]
        clock_id = interval["clock_id"]
        for key in sorted(keyframes, key=lambda k: k["tick"]):
            if not (interval["start"] <= key["tick"] <= interval["end"]):
                raise EffectCraftAdapterError(
                    f"effect {effect['id']!r} keyframe at tick {key['tick']} "
                    "lies outside its interval; refusing to retime it"
                )
            (engine_param,) = tuple(engine_params) or (None,)
            ren_param = next(
                (name for name, target in param_map.items()
                 if target == engine_param),
                None,
            )
            value = key["value"]
            if ren_param is not None:
                value = _convert_param(entry, ren_param, value,
                                       effect["id"])
            operations.append({
                "op": "keyframe",
                "layer": layer,
                "path": f"effects/#{index}/{engine_param}",
                "time": _tick_to_seconds(model, clock_id, key["tick"]),
                "value": value,
            })

    if titles is not None:
        # Audit G3: titles ride the same request after the footage work,
        # so one comp can carry picture and copy. The import is local so
        # this module never depends on the roster at import time.
        try:
            from library.tools import project_model_effectcraft_titles as _titles
        except ImportError:  # imported as ``tools.*`` from inside library/
            from tools import project_model_effectcraft_titles as _titles
        if titles_fps is None or titles_safe_area is None:
            raise EffectCraftAdapterError(
                "titles need titles_fps and titles_safe_area: title "
                "frames resolve at the plan's fps against the comp's "
                "safe area, and neither has a default"
            )
        projection = _titles.project_titles(
            titles, comp, titles_fps, titles_safe_area,
            comp_start_seconds=titles_comp_start_seconds)
        operations.extend(projection.operations)
        losses.extend(projection.losses)
        title_records = [record.as_json()
                         for record in projection.titles]
    else:
        title_records = []

    render_dicts = []
    for render in renders:
        payload = render.validated()
        payload["comp"] = comp.name
        render_dicts.append(payload)

    request = {
        "version": REQUEST_VERSION,
        "project_id": request_id,
        "operations": operations,
        "renders": render_dicts,
    }
    identity_map = {
        "layers": layers,
        "masks": mask_layers,
        "mattes": matte_records,
        "effects": effect_layers,
        "entities": entity_layers,
        "titles": title_records,
    }
    return Projection(
        request=request,
        identity_map=identity_map,
        losses=losses,
        comp=comp,
        renders=list(renders),
        project_id=request_id,
    )


def _require_matte(sequence: MatteSequence, mask: dict[str, Any],
                   model: dict[str, Any], comp: CompSpec) -> None:
    """Refuse a matte that is not exactly this mask at this comp clock."""
    mask_id = mask["id"]
    if sequence.mask_id != mask_id:
        raise UnprojectableMask(
            f"matte {sequence.mask_id!r} does not belong to mask {mask_id!r}")
    if sequence.width != comp.width or sequence.height != comp.height:
        raise UnprojectableMask(
            f"matte for mask {mask_id!r} is {sequence.width}x{sequence.height} "
            f"but the comp is {comp.width}x{comp.height}")
    first, last = _mask_comp_frames(mask, model, comp)
    if sequence.first_frame != first or sequence.count != last - first + 1:
        raise UnprojectableMask(
            f"matte for mask {mask_id!r} covers frames "
            f"{sequence.first_frame}.."
            f"{sequence.first_frame + sequence.count - 1} but the mask "
            f"covers comp frames {first}..{last}")
    if len(sequence.files) != sequence.count:
        raise UnprojectableMask(
            f"matte for mask {mask_id!r} names {len(sequence.files)} files "
            f"for {sequence.count} frames")
    if not sequence.files or not sequence.item_name:
        raise UnprojectableMask(
            f"matte for mask {mask_id!r} names no importable sequence")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReceiptMismatch(message)


def ingest_receipt(
    model: dict[str, Any],
    projection: Projection,
    receipt: dict[str, Any],
    *,
    input_revision: str,
    render_id: str | None = None,
    output_digest: str | None = None,
    recorded_at: str | None = None,
    qa: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Ingest a driver receipt back into a copy of the model.

    Appends the render clock, render output asset, frame map (comp clock
    to render clock), render entry and receipt (with identity mapping,
    alpha, dimensions and losses). Refuses mismatched receipts without
    touching the model.

    `qa` is Cascade Lane C2: caller-measured QA fields (e.g. the reel
    hearing verdicts) stored under the receipt's `details.qa`. Absent by
    default, and the ingest is byte-identical without it; a non-mapping
    `qa` refuses the ingest rather than recording a guess.
    """
    validate_project_model(model)
    _require(receipt.get("version") == RECEIPT_VERSION,
             f"receipt version {receipt.get('version')!r} is not {RECEIPT_VERSION}")
    _require(receipt.get("project_id") == projection.project_id,
             "receipt project does not match its projection")
    _require(receipt.get("upstream_revision") == PINNED_EFFECTCRAFT_REV,
             "receipt upstream revision is not the pinned EffectCraft revision")
    _require(receipt.get("engine_version") == PINNED_EFFECTCRAFT_VERSION,
             "receipt engine version is not the pinned EffectCraft version")
    _require(isinstance(receipt.get("renders"), list) and receipt["renders"],
             "receipt carries no renders")
    _require(len(receipt["renders"]) == len(projection.renders),
             "receipt render count does not match its projection")
    if qa is not None and not isinstance(qa, dict):
        raise ReceiptMismatch(
            f"ingest qa must be a mapping, got {type(qa).__name__}: "
            f"refusing, not guessing")

    comp = projection.comp
    expected_frames = comp.expected_frames()
    for position, render in enumerate(receipt["renders"]):
        _require(render.get("has_alpha") is True,
                 f"render {position} has no alpha; refusing a flattened ingest")
        _require(render.get("width") == comp.width and render.get("height") == comp.height,
                 f"render {position} dimensions do not match comp {comp.name!r}")
        _require(render.get("frames") == expected_frames,
                 f"render {position} frame count does not match the comp's "
                 f"expected {expected_frames} frames")

    render_key = render_id or f"render-ec-{comp.name.lower()}"
    clock_key = f"clock-ec-{comp.name.lower()}"
    map_key = f"map-ec-{comp.name.lower()}"
    receipt_key = f"receipt-ec-{comp.name.lower()}"
    asset_key = f"asset-ec-{comp.name.lower()}"
    stamp = recorded_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    updated = copy.deepcopy(model)
    comp_tps = 1000
    render_tps = max(1, int(round(comp.frame_rate)))
    updated["clocks"].append({
        "id": clock_key,
        "domain": {"kind": "timeline", "timeline_id": comp.name},
        "ticks_per_second": comp_tps,
        "frame_rate": {"numerator": render_tps, "denominator": 1},
    })
    render_clock = f"clock-render-{comp.name.lower()}"
    updated["clocks"].append({
        "id": render_clock,
        "domain": {"kind": "render", "render_id": render_key},
        "ticks_per_second": render_tps,
        "frame_rate": {"numerator": render_tps, "denominator": 1},
    })

    comp_ticks = int(round(comp.duration_seconds * comp_tps))
    from_interval = {"clock_id": clock_key, "start": 0, "end": comp_ticks}
    to_interval = {"clock_id": render_clock, "start": 0, "end": expected_frames}
    if comp_tps == render_tps:
        kind, rate = "one_to_one", {"numerator": 1, "denominator": 1}
    else:
        import math as _math
        divisor = _math.gcd(render_tps, comp_tps)
        kind = "retimed"
        rate = {"numerator": render_tps // divisor, "denominator": comp_tps // divisor}
    updated["frame_maps"].append({
        "id": map_key,
        "from_clock": clock_key,
        "to_clock": render_clock,
        "segments": [{
            "from": from_interval,
            "to": to_interval,
            "mapping_kind": kind,
            "rate": rate,
            "source_clip_use": None,
        }],
    })

    first_path = str(receipt["renders"][0]["path"])
    updated["assets"].append({
        "id": asset_key,
        "kind": "render",
        "uri": first_path,
        "content_digest": output_digest,
        "metadata": {
            "renderer": "effectcraft",
            "width": comp.width,
            "height": comp.height,
            "frames": expected_frames,
        },
        "provenance": [f"prov-ec-{comp.name.lower()}"],
    })
    updated["renders"].append({
        "id": render_key,
        "input_revision": input_revision,
        "output_asset_id": asset_key,
        "settings": {
            "renderer": "effectcraft",
            "comp": comp.name,
            "width": comp.width,
            "height": comp.height,
            "frame_rate": comp.frame_rate,
            "expected_frames": expected_frames,
        },
        "receipt_id": receipt_key,
        "frame_map_id": map_key,
    })
    updated["receipts"].append({
        "id": receipt_key,
        "status": "success",
        "renderer": "effectcraft",
        "input_revision": input_revision,
        "output_asset_id": asset_key,
        "details": {
            "adapter_version": ADAPTER_VERSION,
            "engine_version": receipt["engine_version"],
            "upstream_revision": receipt["upstream_revision"],
            "project_file": str(receipt.get("project_file", "")),
            "comp": comp.name,
            "width": comp.width,
            "height": comp.height,
            "frames": expected_frames,
            "has_alpha": all(r.get("has_alpha") for r in receipt["renders"]),
            "renders": [
                {
                    "path": str(r["path"]),
                    "width": r["width"],
                    "height": r["height"],
                    "frames": r["frames"],
                    "has_alpha": r["has_alpha"],
                }
                for r in receipt["renders"]
            ],
            "identity_map": projection.identity_map,
            "losses": projection.losses,
            **({"qa": dict(qa)} if qa is not None else {}),
        },
    })
    updated["provenance"].append({
        "id": f"prov-ec-{comp.name.lower()}",
        "producer": "project_model_effectcraft",
        "producer_version": str(ADAPTER_VERSION),
        "source_digest": None,
        "recorded_at": stamp,
    })
    validate_project_model(updated)
    return updated


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EffectCraftAdapterError(f"cannot read JSON {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    project = sub.add_parser("project", help="project a model JSON into a driver request")
    project.add_argument("model", type=Path)
    project.add_argument("--out", type=Path, required=True)
    project.add_argument("--project-id", default=None)
    project.add_argument("--comp", default="Main")
    project.add_argument("--width", type=int, default=96)
    project.add_argument("--height", type=int, default=64)
    project.add_argument("--fps", type=float, default=10.0)
    project.add_argument("--duration", type=float, default=0.5)
    project.add_argument("--render-output", default="comp_#####.png")
    project.add_argument("--render-format", default="rgba_png_sequence")
    project.add_argument("--media-root", default=None)
    project.add_argument("--matte", action="append", default=[],
                         help="matte sidecar JSON from the matte command; "
                              "repeatable, one per non-static mask")
    project.add_argument("--titles", type=Path, default=None,
                         help="titles document JSON (timed_text props and/or "
                              "resolved motion moments, audit G3); needs "
                              "--titles-fps")
    project.add_argument("--titles-fps", type=float, default=None,
                         help="plan fps the title frames were resolved at")
    project.add_argument("--project-folder", default=None,
                         help="project root for the safe-area policy")

    matte = sub.add_parser("matte", help="rasterize one mask to a matte sequence")
    matte.add_argument("model", type=Path)
    matte.add_argument("--mask", required=True)
    matte.add_argument("--out-dir", type=Path, required=True)
    matte.add_argument("--sidecar", type=Path, required=True)
    matte.add_argument("--stem", default=None)
    matte.add_argument("--comp", default="Main")
    matte.add_argument("--width", type=int, default=96)
    matte.add_argument("--height", type=int, default=64)
    matte.add_argument("--fps", type=float, default=10.0)
    matte.add_argument("--duration", type=float, default=0.5)

    ingest = sub.add_parser("ingest", help="ingest a driver receipt into a model copy")
    ingest.add_argument("model", type=Path)
    ingest.add_argument("--projection", type=Path, required=True)
    ingest.add_argument("--receipt", type=Path, required=True)
    ingest.add_argument("--out", type=Path, required=True)
    ingest.add_argument("--input-revision", required=True)
    ingest.add_argument("--render-id", default=None)
    ingest.add_argument("--output-digest", default=None)

    args = parser.parse_args(argv)
    try:
        if args.command == "project":
            model = _read_json(args.model)
            comp = CompSpec(name=args.comp, width=args.width, height=args.height,
                            frame_rate=args.fps, duration_seconds=args.duration)
            renders = [RenderSpec(format=args.render_format, output=args.render_output)]
            mattes = {}
            for sidecar in args.matte:
                raw = _read_json(Path(sidecar))
                sequence = MatteSequence.from_json(raw["matte"])
                mattes[sequence.mask_id] = sequence
            titles = (_read_json(args.titles) if args.titles else None)
            titles_safe = None
            if titles is not None:
                try:
                    from library.tools.safe_area import resolve_safe_area
                except ImportError:
                    from tools.safe_area import resolve_safe_area
                titles_safe = resolve_safe_area(
                    args.project_folder, width=args.width,
                    height=args.height)
            projection = project_to_request(
                model, comp, renders,
                media_root=args.media_root, project_id=args.project_id,
                mattes=mattes, titles=titles,
                titles_fps=args.titles_fps, titles_safe_area=titles_safe)
            args.out.write_text(json.dumps({
                "projection": {
                    "request": projection.request,
                    "identity_map": projection.identity_map,
                    "losses": projection.losses,
                    "project_id": projection.project_id,
                    "comp": {"name": comp.name, "width": comp.width,
                             "height": comp.height, "frame_rate": comp.frame_rate,
                             "duration_seconds": comp.duration_seconds},
                    "renders": [{"format": r.format, "output": r.output,
                                 "start_seconds": r.start_seconds,
                                 "end_seconds": r.end_seconds}
                                for r in projection.renders],
                },
            }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"project_id": projection.project_id,
                              "operations": len(projection.request["operations"]),
                              "losses": projection.losses}))
        elif args.command == "matte":
            model = _read_json(args.model)
            comp = CompSpec(name=args.comp, width=args.width, height=args.height,
                            frame_rate=args.fps, duration_seconds=args.duration)
            mask = next((m for m in model["masks"] if m["id"] == args.mask),
                        None)
            if mask is None:
                raise EffectCraftAdapterError(
                    f"model has no mask {args.mask!r}")
            sequence = write_matte_sequence(mask, model, comp, args.out_dir,
                                            stem=args.stem)
            args.sidecar.write_text(json.dumps(
                {"matte": sequence.as_json()}, indent=2,
                ensure_ascii=False) + "\n", encoding="utf-8")
            print(json.dumps({"mask": sequence.mask_id,
                              "files": len(sequence.files),
                              "item": sequence.item_name}))
        else:
            model = _read_json(args.model)
            stored = _read_json(args.projection)["projection"]
            comp_raw = stored["comp"]
            comp = CompSpec(name=comp_raw["name"], width=comp_raw["width"],
                            height=comp_raw["height"], frame_rate=comp_raw["frame_rate"],
                            duration_seconds=comp_raw["duration_seconds"])
            renders = [RenderSpec(format=r["format"], output=r["output"],
                                  start_seconds=r["start_seconds"],
                                  end_seconds=r["end_seconds"])
                       for r in stored["renders"]]
            projection = Projection(request=stored["request"],
                                    identity_map=stored["identity_map"],
                                    losses=stored.get("losses", []),
                                    comp=comp, renders=renders,
                                    project_id=stored["project_id"])
            receipt = _read_json(args.receipt)
            updated = ingest_receipt(model, projection, receipt,
                                     input_revision=args.input_revision,
                                     render_id=args.render_id,
                                     output_digest=args.output_digest)
            args.out.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
            print(json.dumps({"render": updated["renders"][-1]["id"],
                              "frame_map": updated["frame_maps"][-1]["id"]}))
    except EffectCraftAdapterError as exc:
        parser.exit(1, f"effectcraft adapter refused: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
