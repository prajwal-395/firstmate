"""Unit 7c: plan_vfx toolkit moves projected onto EffectCraft transforms.

Maps the moves Ren's VFX planner emits - ``slow_zoom_in``,
``slow_zoom_out``, ``zoom_emphasis``, ``screen_shake``, ``cut_in``
(``library/steps/step_4_03_plan_vfx/post_bridge.py`` ``TOOLKIT_PARAMETERS``,
plus the ``ken_burns`` direction spelling it resolves and the ``push_in``
alias) - onto keyframed EffectCraft layer transforms, deriving the exact
curves from Ren's Fusion implementation rather than approximating them:

- zoom curves come from ``library/tools/fusion/effects.py``
  ``zoom_size_curve`` - the same held-dict builder ``fx.zoom`` keys its
  BezierSpline from, sampled piecewise-linearly at every frame
  (``sample_held_linear``) because both engines linearize baked splines;
- shake offsets come from ``shake_center_curve`` - the same seeded
  ``Random(42)`` draws ``fx.shake`` keys.

Model effects carrying these moves use ``move.*`` class ids, minted
by :func:`mint_move_effect` / :func:`mint_move_effects` below from
accepted plan_vfx toolkit entries (``resolve_vfx`` post-bridge output -
the plan_vfx spellings are bridge vocabulary and never reach the model
unresolved; ``ken_burns``/``push_in`` resolve at the bridge)::

    plan_vfx                model class_id          EffectCraft keys
    slow_zoom_in/out        move.slow_zoom_in/out   transform/scale (+position for pan_end)
    zoom_emphasis / push_in move.zoom_emphasis      transform/scale
    screen_shake            move.screen_shake       transform/position
    cut_in                  move.cut_in             transform/scale (constant)
    ken_burns               move.slow_zoom_in/out   (direction resolved at the bridge)

Coordinate laws (measured 2026-10-10 against the pinned CLI):

- Ren ``Size`` S (1.0 neutral) renders as EffectCraft
  ``transform/scale`` [S*100, S*100, 100]: scale is percent, 100
  neutral, about the footage-center anchor and the comp-center
  position, which are the defaults for a new footage layer - so zoom
  about the frame center needs no anchor or position keys, and a
  scale-150 render keeps the footage centroid pixel-exact.
- Ren ``Center`` (cx, cy) is normalized to Fusion's bottom-left
  origin (``library/tools/fusion/tool_inputs.py``); EffectCraft
  ``transform/position`` is pixels from the top-left. The projection
  is [cx*W, (1-cy)*H]: a +[16, +8]px position key moves the rendered
  centroid exactly +16 right and +8 down, and (0.5, 0.5) lands on the
  measured default [W/2, H/2].
- Dense per-frame keys with the engine's default Linear interpolation
  reproduce the baked curve exactly at frame sample times (a 100->200
  ramp over 0.4s reads back exactly 150.0 at 0.2s).

Tolerances (declared, tested in ``tests/unit/context/``
``test_project_model_effectcraft_moves.py`` and proven through the
pinned CLI in ``tests/qualification/test_effectcraft_moves_proof.py``):

- ``SCALE_TOLERANCE_PCT`` (1e-6 percent): every projected scale key
  equals the Fusion comp's serialized Size key at that frame times
  100 within this.
- ``POSITION_TOLERANCE_PX`` (1e-6 px): every projected position key
  equals the mapped Fusion Center within this.

Fail-closed: anything without an exact mapping keeps refusing with
:class:`UnprojectableMove` - a mask target (moves are per-clip
Transforms), a move carrying model ``keyframes`` (no parameter for
them to animate), an off-midpoint full-clip punch (smooth Bezier
handles no Linear bake can express), a zero shake (Fusion draws
nothing), non-numeric or unknown params. Rotation is never keyed:
the toolkit emits no rotation, and inventing one would be taste.

Mint ordering: Unit 4 editorial migration FIRST, the mint against the
migrated model, projection after. :func:`mint_move_effects` reads the
target clip use's id and timeline interval off the migrated model, so
a ``move.*`` effect always names a post-migration stable clip-use id -
minting a pre-migration id would refuse at projection as
``UnprojectableEffectTarget``
(``project_model_effectcraft.py:202-205``). Step 4.03 calls the mint
on its own assignments through
:func:`mint_step_moves <library.steps.step_4_03_plan_vfx.post_bridge.mint_step_moves>`
(which migrates with the Unit-4 functions in
``project_model_editorial.clip_uses_from_assignments``), so pipeline
runs mint with the same ids the migration would. The mint itself refuses
(do not mint) exactly the ``UnprojectableMove`` cases above; anything
it cannot judge without comp geometry (interval-to-frame fit, punch
ramp fit) stays the projection's refusal.
"""

from __future__ import annotations

import math
from typing import Any

try:
    from library.tools.fusion.effects import (
        DRIFT_EASING,
        sample_held_linear,
        shake_center_curve,
        zoom_size_curve,
    )
except ImportError:  # imported as ``tools.*`` from inside library/
    from tools.fusion.effects import (
        DRIFT_EASING,
        sample_held_linear,
        shake_center_curve,
        zoom_size_curve,
    )

MOVE_CLASS_IDS = (
    "move.slow_zoom_in",
    "move.slow_zoom_out",
    "move.zoom_emphasis",
    "move.screen_shake",
    "move.cut_in",
)
"""Model effect classes this module projects (plan_vfx spellings never
reach the model; ``ken_burns``/``push_in`` resolve at the bridge)."""

MOVE_PARAMS: dict[str, tuple[str, ...]] = {
    # Drift across the move interval, plus an optional static re-centre.
    "move.slow_zoom_in": ("zoom_start", "zoom_end", "pan_end",
                          "zoom_easing"),
    "move.slow_zoom_out": ("zoom_start", "zoom_end", "pan_end",
                           "zoom_easing"),
    # Punch to a peak and settle back; the hold is derived between the
    # separately timed ramps, exactly as the bridge resolves them.
    "move.zoom_emphasis": ("zoom_start", "zoom_mid", "zoom_end",
                           "zoom_in_duration_frames",
                           "zoom_release_offset_frames",
                           "zoom_out_duration_frames",
                           "zoom_easing"),
    # Impact offsets as a fraction of frame width per axis, decaying.
    "move.screen_shake": ("shake_x", "shake_y", "shake_decay_frames"),
    # Constant reframe held for the move interval.
    "move.cut_in": ("zoom_start", "zoom_mid", "zoom_end", "pan_end",
                    "zoom_easing"),
}
"""Readable parameter names per move: the values the plan chose, checked
by name here and never defaulted, bounded or substituted - except
``zoom_mid``, which the renderer defaults to the start/end midpoint
(``comp_builder.normalize_effects``) and so does this projection."""

SCALE_TOLERANCE_PCT = 1e-6
"""A projected scale key must equal its Fusion Size key times 100
within this many percent."""

POSITION_TOLERANCE_PX = 1e-6
"""A projected position key must equal its mapped Fusion Center
within this many pixels."""


class UnprojectableMove(ValueError):
    """A move with no exact EffectCraft transform projection.

    The message names the reason (mask target, carried keyframes,
    legacy punch, zero shake, bad param). Nothing unmappable is
    approximated.
    """


def _require_number(value: Any, class_id: str, effect_id: str,
                    name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param {name!r} is not a "
            f"number: {value!r}")
    if not math.isfinite(float(value)):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param {name!r} is not "
            f"finite: {value!r}")
    return float(value)


def _require_frame_count(value: Any, class_id: str, effect_id: str,
                         name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param {name!r} is not a "
            f"whole frame count: {value!r}")
    if value < minimum:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param {name!r} is "
            f"{value}, below the {minimum}-frame minimum")
    return value


def _check_params(class_id: str, effect_id: str,
                  parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) parameters are not a "
            f"mapping: {parameters!r}")
    readable = MOVE_PARAMS[class_id]
    for name in parameters:
        if name not in readable:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) param {name!r} is "
                f"read by nothing; {class_id} is drawn from "
                f"{', '.join(readable)}")
    return parameters


def _read_pan_end(value: Any, class_id: str,
                       effect_id: str) -> tuple[float, float] | None:
    """A static Center re-centre as (cx, cy), or None when absent."""
    if value is None:
        return None
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(float(v)) for v in value)):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param 'pan_end' is not a "
            f"normalized (x, y) center: {value!r}")
    return (float(value[0]), float(value[1]))


def _neutral_scale() -> list[float]:
    return [100.0, 100.0, 100.0]


def _neutral_position(width: int, height: int) -> list[float]:
    return [width / 2.0, height / 2.0]


def _center_to_position(center: tuple[float, float], width: int,
                        height: int) -> list[float]:
    """Fusion normalized bottom-left Center to EffectCraft top-left px."""
    cx, cy = center
    return [cx * width, (1.0 - cy) * height]


def move_comp_frames(interval: dict[str, Any], ticks_per_second: int,
                     frame_rate: float, effect_id: str, *,
                     error: type[ValueError] = UnprojectableMove
                     ) -> tuple[int, int]:
    """Comp frames a move interval touches, first..last inclusive.

    Frame ``i`` plays ``[i / fps, (i + 1) / fps)``; the move covers
    every frame its half-open tick interval touches - the same touch
    rule the matte rasterizer uses, so a move and a mask on one
    interval agree on which frames they own. The fade projector shares
    this touch rule; ``error`` names the refusal it raises (moves keep
    the default).
    """
    refused = error
    if not math.isfinite(frame_rate) or frame_rate <= 0:
        raise refused(
            f"effect {effect_id!r} needs a positive comp frame rate, "
            f"got {frame_rate!r}")
    if ticks_per_second <= 0:
        raise refused(
            f"effect {effect_id!r} clock has no positive tick rate")
    try:
        start, end = int(interval["start"]), int(interval["end"])
    except (TypeError, ValueError, KeyError) as exc:
        raise refused(
            f"effect {effect_id!r} interval has no integer start/end") from exc
    if end <= start:
        raise refused(
            f"effect {effect_id!r} interval is empty; nothing to bake")
    from fractions import Fraction
    fps = Fraction(frame_rate)
    first = math.floor(Fraction(start) * fps / ticks_per_second)
    last = math.ceil(Fraction(end) * fps / ticks_per_second) - 1
    if last < first:
        raise refused(
            f"effect {effect_id!r} covers no comp frame at "
            f"{frame_rate}fps")
    return first, last


def project_move(*, class_id: str, effect_id: str,
                 parameters: dict[str, Any], layer: str,
                 first_frame: int, last_frame: int,
                 comp_frames: int, comp_width: int, comp_height: int,
                 frame_rate: float) -> list[dict[str, Any]]:
    """Bake one move effect into EffectCraft ``keyframe`` operations.

    ``first_frame``/``last_frame`` are the comp frames the move's
    interval touches (inclusive); ``comp_frames`` the comp's total.
    Values are the Fusion curve's own per-frame samples: scale keys
    carry the serialized Size times 100, position keys the Center
    mapped to top-left pixels. Frames outside the move interval hold
    neutral (100 / comp center), mirroring the Fusion window holds,
    so a punch on one span never leaks onto the rest of the comp.
    """
    if class_id not in MOVE_PARAMS:
        raise UnprojectableMove(
            f"effect {effect_id!r} class {class_id!r} is not a mapped "
            f"move (expected one of {', '.join(MOVE_CLASS_IDS)})")
    params = _check_params(class_id, effect_id, parameters)
    if first_frame < 0 or last_frame >= comp_frames or last_frame < first_frame:
        raise UnprojectableMove(
            f"move {effect_id!r} frames [{first_frame}, {last_frame}] "
            f"do not fit comp frames [0, {comp_frames - 1}]")

    if class_id in ("move.slow_zoom_in", "move.slow_zoom_out",
                    "move.cut_in"):
        ops = _project_zoom_move(
            class_id=class_id, effect_id=effect_id, params=params,
            layer=layer, first_frame=first_frame, last_frame=last_frame,
            comp_frames=comp_frames, comp_width=comp_width,
            comp_height=comp_height, frame_rate=frame_rate)
    elif class_id == "move.zoom_emphasis":
        ops = _project_punch(
            effect_id=effect_id, params=params, layer=layer,
            first_frame=first_frame, last_frame=last_frame,
            comp_frames=comp_frames, frame_rate=frame_rate)
    else:
        ops = _project_shake(
            effect_id=effect_id, params=params, layer=layer,
            first_frame=first_frame, last_frame=last_frame,
            comp_frames=comp_frames, comp_width=comp_width,
            comp_height=comp_height, frame_rate=frame_rate)
    # Deterministic order for checked-in requests: keys run by path,
    # then by time. The engine sorts keys by time itself; this is for
    # review, not semantics.
    return sorted(ops, key=lambda op: (op["path"], op["time"]))


def _keyframe_op(layer: str, path: str, frame: int, frame_rate: float,
                 value: Any) -> dict[str, Any]:
    return {"op": "keyframe", "layer": layer, "path": path,
            "time": frame / frame_rate, "value": value}


def _boundary_keys(layer: str, path: str, neutral: list[float],
                   first_frame: int, last_frame: int, comp_frames: int,
                   frame_rate: float) -> list[dict[str, Any]]:
    """Neutral holds around a move window, mirroring Fusion's.

    Holds sit at comp frame 0 and one frame off each window edge so
    the move snaps in and out instead of ramping in from frame 0 -
    the same placement ``fx.zoom`` uses for its 1.0 holds.
    """
    ops = []
    if first_frame > 0:
        ops.append(_keyframe_op(layer, path, 0, frame_rate, neutral))
        if first_frame - 1 > 0:
            ops.append(_keyframe_op(layer, path, first_frame - 1,
                                    frame_rate, neutral))
    if last_frame < comp_frames - 1:
        if last_frame + 1 < comp_frames - 1:
            ops.append(_keyframe_op(layer, path, last_frame + 1,
                                    frame_rate, neutral))
        ops.append(_keyframe_op(layer, path, comp_frames - 1, frame_rate,
                                neutral))
    return ops


def _project_zoom_move(*, class_id: str, effect_id: str,
                       params: dict[str, Any], layer: str,
                       first_frame: int, last_frame: int,
                       comp_frames: int, comp_width: int,
                       comp_height: int,
                       frame_rate: float) -> list[dict[str, Any]]:
    for name in ("zoom_start", "zoom_end"):
        if name not in params:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) has no "
                f"params.{name}: the zoom's resting scales are the "
                f"planner's decision and have no default")
        _require_number(params[name], class_id, effect_id, name)
    start = float(params["zoom_start"])
    end = float(params["zoom_end"])
    mid = float(params["zoom_mid"]) if "zoom_mid" in params else (start + end) / 2.0
    if "zoom_mid" in params:
        _require_number(params["zoom_mid"], class_id, effect_id,
                        "zoom_mid")
    easing = params.get("zoom_easing", DRIFT_EASING)
    if not isinstance(easing, str):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param 'zoom_easing' is "
            f"not a string: {easing!r}")
    pan = _read_pan_end(params.get("pan_end"), class_id, effect_id)

    count = last_frame - first_frame + 1
    try:
        kind, payload = zoom_size_curve(
            first=0, last=count - 1, start=start, mid=mid, end=end,
            easing=easing)
    except ValueError as exc:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) refused by the zoom "
            f"curve: {exc}") from exc
    if kind == "legacy_punch":
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) is an off-midpoint punch "
            f"with no independent timing: its smooth Bezier peak has "
            f"no Linear keyframe expression, so it is refused rather "
            f"than baked flat")

    ops: list[dict[str, Any]] = []
    if kind == "static":
        if payload != 1.0 or pan is not None:
            ops.extend(_boundary_keys(layer, "transform/scale",
                                      _neutral_scale(), first_frame,
                                      last_frame, comp_frames,
                                      frame_rate))
            if payload != 1.0:
                value = [payload * 100.0, payload * 100.0, 100.0]
                ops.append(_keyframe_op(layer, "transform/scale",
                                        first_frame, frame_rate, value))
                if last_frame != first_frame:
                    ops.append(_keyframe_op(layer, "transform/scale",
                                            last_frame, frame_rate,
                                            value))
    else:
        assert isinstance(payload, dict)
        dense = sample_held_linear(payload, 0, count - 1)
        ops.extend(_boundary_keys(layer, "transform/scale",
                                  _neutral_scale(), first_frame,
                                  last_frame, comp_frames, frame_rate))
        for local in range(count):
            size = round(dense[local], 6)
            ops.append(_keyframe_op(
                layer, "transform/scale", first_frame + local,
                frame_rate, [size * 100.0, size * 100.0, 100.0]))
    if pan is not None and pan != (0.5, 0.5):
        ops.extend(_boundary_keys(layer, "transform/position",
                                  _neutral_position(comp_width,
                                                    comp_height),
                                  first_frame, last_frame, comp_frames,
                                  frame_rate))
        value = _center_to_position(pan, comp_width, comp_height)
        ops.append(_keyframe_op(layer, "transform/position", first_frame,
                                frame_rate, value))
        if last_frame != first_frame:
            ops.append(_keyframe_op(layer, "transform/position",
                                    last_frame, frame_rate, value))
    return ops


def _project_punch(*, effect_id: str, params: dict[str, Any],
                   layer: str, first_frame: int, last_frame: int,
                   comp_frames: int, frame_rate: float) -> list[dict[str, Any]]:
    class_id = "move.zoom_emphasis"
    for name in ("zoom_start", "zoom_mid", "zoom_end"):
        if name not in params:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) has no params.{name}: "
                f"timing alone does not move the picture")
        _require_number(params[name], class_id, effect_id, name)
    for name in ("zoom_in_duration_frames",
                 "zoom_out_duration_frames"):
        if name not in params:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) has no params.{name}: "
                f"the in and out ramps are independent decisions with "
                f"no default")
    if "zoom_release_offset_frames" not in params:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) has no "
            f"params.zoom_release_offset_frames: the hold is derived "
            f"between peak and release, never declared")
    in_frames = _require_frame_count(params["zoom_in_duration_frames"],
                                     class_id, effect_id,
                                     "zoom_in_duration_frames",
                                     minimum=1)
    out_frames = _require_frame_count(params["zoom_out_duration_frames"],
                                      class_id, effect_id,
                                      "zoom_out_duration_frames",
                                      minimum=1)
    release = _require_frame_count(params["zoom_release_offset_frames"],
                                   class_id, effect_id,
                                   "zoom_release_offset_frames",
                                   minimum=0)
    easing = params.get("zoom_easing", DRIFT_EASING)
    if not isinstance(easing, str):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param 'zoom_easing' is "
            f"not a string: {easing!r}")

    count = last_frame - first_frame + 1
    try:
        kind, payload = zoom_size_curve(
            first=0, last=count - 1,
            start=float(params["zoom_start"]),
            mid=float(params["zoom_mid"]),
            end=float(params["zoom_end"]),
            easing=easing,
            zoom_in_duration_frames=in_frames,
            zoom_release_offset_frames=release,
            zoom_out_duration_frames=out_frames)
    except ValueError as exc:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) refused by the zoom "
            f"curve: {exc}") from exc
    assert kind == "baked" and isinstance(payload, dict)
    dense = sample_held_linear(payload, 0, count - 1)
    ops = _boundary_keys(layer, "transform/scale", _neutral_scale(),
                         first_frame, last_frame, comp_frames,
                         frame_rate)
    for local in range(count):
        size = round(dense[local], 6)
        ops.append(_keyframe_op(
            layer, "transform/scale", first_frame + local, frame_rate,
            [size * 100.0, size * 100.0, 100.0]))
    return ops


def _project_shake(*, effect_id: str, params: dict[str, Any],
                   layer: str, first_frame: int, last_frame: int,
                   comp_frames: int, comp_width: int, comp_height: int,
                   frame_rate: float) -> list[dict[str, Any]]:
    class_id = "move.screen_shake"
    if not any(name in params for name in ("shake_x", "shake_y")):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) names neither shake_x "
            f"nor shake_y: an axis nothing moves is neutral, not a "
            f"strength, and a shake moving no axis draws nothing")
    x = float(params.get("shake_x", 0.0))
    y = float(params.get("shake_y", 0.0))
    for name, got in (("shake_x", x), ("shake_y", y)):
        if name in params:
            _require_number(params[name], class_id, effect_id, name)
    if x <= 0 and y <= 0:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) has no positive "
            f"amplitude (shake_x={x!r}, shake_y={y!r}): Fusion draws "
            f"nothing for it, so there is nothing to project")
    decay = params.get("shake_decay_frames")
    if decay is not None:
        decay = _require_frame_count(decay, class_id, effect_id,
                                     "shake_decay_frames", minimum=1)

    count = last_frame - first_frame + 1
    centers = shake_center_curve(count, x, y, decay)
    ops = _boundary_keys(layer, "transform/position",
                         _neutral_position(comp_width, comp_height),
                         first_frame, last_frame, comp_frames,
                         frame_rate)
    for local, center in enumerate(centers):
        ops.append(_keyframe_op(
            layer, "transform/position", first_frame + local,
            frame_rate,
            _center_to_position(center, comp_width, comp_height)))
    return ops


# ── Producer: plan_vfx toolkit entries to ``move.*`` model effects ──

PLAN_EFFECT_TO_MOVE_CLASS = {
    "slow_zoom_in": "move.slow_zoom_in",
    "slow_zoom_out": "move.slow_zoom_out",
    "zoom_emphasis": "move.zoom_emphasis",
    "screen_shake": "move.screen_shake",
    "cut_in": "move.cut_in",
}
"""Accepted plan_vfx toolkit entry to minted model class.

Exactly the post-bridge spellings: the bridge resolves ``push_in``
to ``zoom_emphasis`` and ``ken_burns`` to the drift its own params
describe, filters params to ``TOOLKIT_PARAMETERS`` names and derives
the punch frame counts, so the mint sees only these five names - and
a contract test pins this map's keys to ``TOOLKIT_PARAMETERS``. A
non-motion entry (native speed, stabilize, built-in) is refused here,
never skipped: it travels another route, and a misrouted entry must
not vanish.
"""

PLAN_SECONDS_SPELLINGS = ("zoom_in_seconds", "zoom_out_seconds")
"""Plan words superseded by the bridge's derivation.

The plan states punch timing in seconds; the bridge derives
``zoom_in_duration_frames`` / ``zoom_release_offset_frames`` /
``zoom_out_duration_frames`` from the anchors and carries both
spellings on the resolved entry. The mint carries the derivation -
the seconds are the same decision in plan words, not a second
timing, so keeping both would double-drive the ramp.
"""

ZOOM_FAMILY = ("move.slow_zoom_in", "move.slow_zoom_out", "move.cut_in")
"""Move classes sharing the start/mid/end zoom vocabulary."""


def _mint_params(class_id: str, effect_id: str, params: Any,
                 raw_params: Any) -> dict[str, Any]:
    """The exact params a minted move carries.

    Resolved params minus the superseded seconds spellings, plus any
    ``MOVE_PARAMS`` name the raw plan stated that the bridge filtered
    for Fusion (``zoom_easing``: the drift curve reads it, the toolkit
    does not advertise it, so the bridge strips it and the mint
    recovers it). Unknown names refuse via :func:`_check_params` -
    the bridge already filtered to toolkit names, so anything left
    unreadable here is a caller-built entry, refused rather than
    minted half-read.
    """
    if not isinstance(params, dict):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) parameters are not a "
            f"mapping: {params!r}")
    final = {k: v for k, v in params.items()
             if k not in PLAN_SECONDS_SPELLINGS}
    if raw_params is not None:
        if not isinstance(raw_params, dict):
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) raw plan params are "
                f"not a mapping: {raw_params!r}")
        for name in MOVE_PARAMS[class_id]:
            if name in raw_params and name not in final:
                final[name] = raw_params[name]
    return _check_params(class_id, effect_id, final)


def _mint_check_zoom(class_id: str, effect_id: str,
                     params: dict[str, Any]) -> None:
    """Param-level checks the zoom projector runs, minus comp geometry.

    Presence, numeric kinds, the re-centre shape and the curve-kind
    decision (an off-midpoint punch is values-only - ``zoom_size_curve``
    answers ``legacy_punch`` with no frame window) are all judged here
    so the mint refuses what the projection would refuse. Calling it
    with a throwaway two-frame window is exact: the kind decision never
    reads the window.
    """
    for name in ("zoom_start", "zoom_end"):
        if name not in params:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) has no "
                f"params.{name}: the zoom's resting scales are the "
                f"planner's decision and have no default")
        _require_number(params[name], class_id, effect_id, name)
    start = float(params["zoom_start"])
    end = float(params["zoom_end"])
    mid = float(params["zoom_mid"]) if "zoom_mid" in params else (start + end) / 2.0
    if "zoom_mid" in params:
        _require_number(params["zoom_mid"], class_id, effect_id,
                        "zoom_mid")
    easing = params.get("zoom_easing", DRIFT_EASING)
    if not isinstance(easing, str):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param 'zoom_easing' is "
            f"not a string: {easing!r}")
    _read_pan_end(params.get("pan_end"), class_id, effect_id)
    try:
        kind, _ = zoom_size_curve(
            first=0, last=1, start=start, mid=mid, end=end,
            easing=easing)
    except ValueError as exc:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) refused by the zoom "
            f"curve: {exc}") from exc
    if kind == "legacy_punch":
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) is an off-midpoint punch "
            f"with no independent timing: its smooth Bezier peak has "
            f"no Linear keyframe expression, so it is refused rather "
            f"than minted flat")


def _mint_check_punch(effect_id: str, params: dict[str, Any]) -> None:
    """Punch param checks minus the frame-window fit (projection's)."""
    class_id = "move.zoom_emphasis"
    for name in ("zoom_start", "zoom_mid", "zoom_end"):
        if name not in params:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) has no params.{name}: "
                f"timing alone does not move the picture")
        _require_number(params[name], class_id, effect_id, name)
    for name in ("zoom_in_duration_frames",
                 "zoom_out_duration_frames"):
        if name not in params:
            raise UnprojectableMove(
                f"move {effect_id!r} ({class_id}) has no params.{name}: "
                f"the in and out ramps are independent decisions with "
                f"no default")
    if "zoom_release_offset_frames" not in params:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) has no "
            f"params.zoom_release_offset_frames: the hold is derived "
            f"between peak and release, never declared")
    _require_frame_count(params["zoom_in_duration_frames"],
                         class_id, effect_id,
                         "zoom_in_duration_frames", minimum=1)
    _require_frame_count(params["zoom_out_duration_frames"],
                         class_id, effect_id,
                         "zoom_out_duration_frames", minimum=1)
    _require_frame_count(params["zoom_release_offset_frames"],
                         class_id, effect_id,
                         "zoom_release_offset_frames", minimum=0)
    easing = params.get("zoom_easing", DRIFT_EASING)
    if not isinstance(easing, str):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) param 'zoom_easing' is "
            f"not a string: {easing!r}")


def _mint_check_shake(effect_id: str, params: dict[str, Any]) -> None:
    """Shake param checks: a shake moving no axis draws nothing."""
    class_id = "move.screen_shake"
    if not any(name in params for name in ("shake_x", "shake_y")):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) names neither shake_x "
            f"nor shake_y: an axis nothing moves is neutral, not a "
            f"strength, and a shake moving no axis draws nothing")
    x = float(params.get("shake_x", 0.0))
    y = float(params.get("shake_y", 0.0))
    for name, got in (("shake_x", x), ("shake_y", y)):
        if name in params:
            _require_number(params[name], class_id, effect_id, name)
    if x <= 0 and y <= 0:
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) has no positive "
            f"amplitude (shake_x={x!r}, shake_y={y!r}): Fusion draws "
            f"nothing for it, so there is nothing to mint")
    if params.get("shake_decay_frames") is not None:
        _require_frame_count(params["shake_decay_frames"], class_id,
                             effect_id, "shake_decay_frames", minimum=1)


def _mint_check_interval(effect_id: str, interval: Any) -> dict[str, Any]:
    """The clip's timeline interval, structurally - mapping owned below."""
    if not isinstance(interval, dict):
        raise UnprojectableMove(
            f"move {effect_id!r} interval is not a mapping: "
            f"{interval!r}")
    clock_id = interval.get("clock_id")
    if not isinstance(clock_id, str) or not clock_id:
        raise UnprojectableMove(
            f"move {effect_id!r} interval names no clock")
    try:
        start, end = int(interval["start"]), int(interval["end"])
    except (TypeError, ValueError, KeyError) as exc:
        raise UnprojectableMove(
            f"move {effect_id!r} interval has no integer start/end") from exc
    if end <= start:
        raise UnprojectableMove(
            f"move {effect_id!r} interval is empty; nothing to bake")
    return {"clock_id": clock_id, "start": start, "end": end}


def mint_move_effect(*, effect_type: str, params: dict[str, Any],
                     target_clip_use: str,
                     timeline_interval: dict[str, Any],
                     effect_id: str, clip_use_ids: Any,
                     mask_ids: Any, raw_params: Any = None,
                     keyframes: Any = ()) -> dict[str, Any]:
    """Mint one ``move.*`` model effect from an accepted toolkit entry.

    ``effect_type`` is the POST-bridge spelling (aliases resolved,
    params filtered, punch frame counts derived); ``raw_params`` is
    the plan's own params mapping for ``zoom_easing`` recovery (see
    :func:`_mint_params`). ``target_clip_use`` must be a
    post-migration clip-use id from ``clip_use_ids`` and
    ``timeline_interval`` that use's timeline interval - the mint
    attaches both verbatim and checks the shape, never the clock.
    ``keyframes`` must stay empty: anything carrying model keyframes
    is refused, mirroring the projection.

    Refuses (does not mint) exactly the ``UnprojectableMove`` cases:
    an unmapped plan type, a mask target, carried keyframes, an
    off-midpoint full-clip punch, a zero shake - plus the projector's
    own presence/kind refusals, checked here with the same messages.
    An unknown clip-use id refuses as ``UnprojectableEffectTarget``
    from the projection boundary (``project_model_effectcraft`` owns
    that name). Interval-to-frame and ramp-window fits stay the
    projection's refusal: they need comp geometry the mint is never
    given.
    """
    try:
        from library.tools.project_model_effectcraft import (
            UnprojectableEffectTarget,
        )
    except ImportError:  # imported as ``tools.*`` from inside library/
        from tools.project_model_effectcraft import (
            UnprojectableEffectTarget,
        )
    class_id = PLAN_EFFECT_TO_MOVE_CLASS.get(effect_type)
    if class_id is None:
        raise UnprojectableMove(
            f"move {effect_id!r} plan type {effect_type!r} is not a "
            f"mapped move (expected one of "
            f"{', '.join(sorted(PLAN_EFFECT_TO_MOVE_CLASS))}): "
            f"resolve aliases at the bridge and route non-motion "
            f"effects elsewhere")
    if target_clip_use in set(mask_ids or ()):
        raise UnprojectableMove(
            f"move {effect_id!r} ({class_id}) targets "
            f"mask {target_clip_use!r}: moves are per-clip "
            f"Transforms, and a mask has no picture of its own to move"
        )
    if target_clip_use not in set(clip_use_ids or ()):
        raise UnprojectableEffectTarget(
            f"effect {effect_id!r} target {target_clip_use!r} is no "
            f"projected clip use or mask"
        )
    if keyframes:
        raise UnprojectableMove(
            f"move {effect_id!r} carries model keyframes with no "
            f"parameter for them to animate: the move's curve is baked "
            f"from its params, so keyframes are refused rather than "
            f"minted double-driven"
        )
    final = _mint_params(class_id, effect_id, params, raw_params)
    if class_id == "move.zoom_emphasis":
        _mint_check_punch(effect_id, final)
    elif class_id == "move.screen_shake":
        _mint_check_shake(effect_id, final)
    else:
        _mint_check_zoom(class_id, effect_id, final)
    return {
        "id": effect_id,
        "class_id": class_id,
        "target_id": target_clip_use,
        "interval": _mint_check_interval(effect_id, timeline_interval),
        "parameters": final,
        "keyframes": [],
    }


def mint_move_effects(*, resolved_entries: list[dict[str, Any]],
                      model: dict[str, Any],
                      targets: dict[str, str],
                      raw_params_by_vfx: dict[str, Any] | None = None,
                      ) -> list[dict[str, Any]]:
    """Mint one model effect per accepted toolkit entry, in entry order.

    ``resolved_entries`` are ``resolve_vfx`` post-bridge entries (each
    with ``vfx_id``, ``target_block_position``, ``effect_type`` and
    ``params``); ``model`` is the POST-migration project model the
    effects land in; ``targets`` maps ``str(target_block_position)``
    to the block's migrated clip-use id, whose timeline interval the
    minted effect carries. ``raw_params_by_vfx`` optionally maps
    ``vfx_id`` to the plan's own params for ``zoom_easing`` recovery.
    Minted ids are ``move-<vfx_id>``: stable and unique per entry.

    A block naming no clip use, or a use the model does not carry,
    refuses as ``UnprojectableEffectTarget`` - the mint pins to
    post-migration ids and never invents a target. Non-toolkit entries
    refuse as ``UnprojectableMove`` (see :data:`PLAN_EFFECT_TO_MOVE_CLASS`):
    pass only motion entries and route everything else elsewhere.
    """
    try:
        from library.tools.project_model_effectcraft import (
            UnprojectableEffectTarget,
        )
    except ImportError:  # imported as ``tools.*`` from inside library/
        from tools.project_model_effectcraft import (
            UnprojectableEffectTarget,
        )
    uses = {use["id"]: use for use in model.get("clip_uses", [])}
    mask_ids = {mask["id"] for mask in model.get("masks", [])}
    raw_by_vfx = raw_params_by_vfx or {}
    minted: list[dict[str, Any]] = []
    for entry in resolved_entries:
        vfx_id = entry["vfx_id"]
        block = str(entry["target_block_position"])
        if block not in targets:
            raise UnprojectableEffectTarget(
                f"move move-{vfx_id} block {block!r} names no clip "
                f"use: mint against post-migration clip-use ids"
            )
        use_id = targets[block]
        if use_id in mask_ids:
            raise UnprojectableMove(
                f"move move-{vfx_id} targets mask {use_id!r}: moves "
                f"are per-clip Transforms, and a mask has no picture "
                f"of its own to move"
            )
        use = uses.get(use_id)
        if use is None:
            raise UnprojectableEffectTarget(
                f"move move-{vfx_id} target {use_id!r} is no migrated "
                f"clip use"
            )
        interval = use.get("timeline_interval")
        if not isinstance(interval, dict):
            raise UnprojectableEffectTarget(
                f"move move-{vfx_id} target {use_id!r} carries no "
                f"timeline interval"
            )
        minted.append(mint_move_effect(
            effect_type=entry["effect_type"],
            params=entry.get("params", {}),
            target_clip_use=use_id,
            timeline_interval=interval,
            effect_id=f"move-{vfx_id}",
            clip_use_ids=set(uses),
            mask_ids=mask_ids,
            raw_params=raw_by_vfx.get(vfx_id),
            keyframes=entry.get("keyframes", ()),
        ))
    return minted
