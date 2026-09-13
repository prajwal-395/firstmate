"""Look at what a visual treatment drew, before the captain has to.

A treatment is applied to a picture that already existed, so the only
honest question is a BEFORE/AFTER one: at the same timecode, what did
the picture look like with the treatment and without it? This module
answers it deterministically, off the comp itself - no Resolve, no
model, no GPU. The comp builder is Resolve-free by design, so the check
runs anywhere: in CI, in the applier subprocess, in a skill.

What it measures, per treatment key (``tv_power_head`` / ``tv_power_tail``):
- which played frames differ from neutral (the treatment's own splines
  evaluated over the frames the timeline really renders, with Fusion's
  flat-extrapolation semantics - see
  ``library/tools/fusion/transition_frames.value_at``);
- whether every changed frame sits inside the treatment's declared
  window (``outside_window`` - the played_window defect class: keys past
  the rendered range held flat across the whole clip);
- whether the treatment drew anything at all (``drew_nothing`` - a
  switch-off keyed at source frames that never play is an animation
  nobody will ever see, and silence about it is the gate-that-cannot-
  fail again);
- whether the animation settles to neutral where it claims to
  (``never_settles`` - a head on a clip shorter than its own animation
  plays collapsed for the whole clip);
- and, reported never gated, how much picture the treatment keeps
  (``min_kept_fraction``) - "the picture changed by N% in this region"
  is a measurement; "badly framed" is a judgement (AGENTS.md 10.5), and
  this module never makes one.

The model's reading, where a harness can be shown one, is the skill's
half (``library/skills/verify_treatment``): recorded, never enforced
(AGENTS.md 10.4). This module is the half that can carry a verdict.

The undo is ``remove_treatment``: dropping the key (and its timing)
rebuilds the exact untreated bytes, which the applier does when a
verdict fails - and the proof is string equality, not an assertion.

The same before/after question is asked of a Ken Burns drift by
``verify_drift``: a planned ``slow_zoom_in`` / ``slow_zoom_out`` whose
``zoom_start`` equals its ``zoom_end`` resolves (values are taste and
are never bounded at plan time) and reaches ``fx.zoom``, which builds
an empty block - so the manifest records drift the viewer never sees.
Sampling the Transform Size spline at the first and last rendered
frames says whether the framing actually moved; a drift that did not
is undone by ``remove_drift`` the same way a failing treatment is.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

#: The treatment keys this module can verify. One enumeration: a key
#: spelled twice is this repository's dominant bug class (AGENTS.md 10.1).
TREATMENT_KEYS = ("tv_power_head", "tv_power_tail")

#: Values are serialized to six decimal places; the smallest step any
#: eased ramp takes is 0.010926. Same epsilon `transition_frames` uses,
#: so "changed" means the same thing in both modules.
EPSILON = 1e-6

#: The zoom/pan keys a Ken Burns drift travels as. One enumeration
#: beside `TREATMENT_KEYS`: a key spelled twice is this repository's
#: dominant bug class (AGENTS.md 10.1).
DRIFT_KEYS = ("zoom_start", "zoom_mid", "zoom_end",
              "pan_start", "pan_end")

#: Drift metadata that shapes the curve but arms nothing on its own:
#: `zoom_easing` with no zoom values draws an empty block, so a still
#: shot carrying it must NOT arm the gate - but where a drift IS armed
#: the verdict must judge the curve the comp actually renders, easing
#: included. Read wherever the drift is built or undone, never for
#: arming.
DRIFT_CURVE_KEYS = ("zoom_easing",)

#: A drift whose Size curve never leaves the straight line between its
#: endpoints by more than this reads as linear to the viewer: 0.001 Size
#: units is about a pixel at 1080p. Below it the curve IS a ramp for
#: every purpose the captain judges; above it the drift eases.
EASED_THRESHOLD = 1e-3


class UnknownTreatment(ValueError):
    """A treatment key this module has no window or neutral map for."""


def timing_for(key: str, effects: dict) -> Dict[str, int]:
    """This treatment's resolved frame counts, overrides applied."""
    if key in ("tv_power_head", "tv_power_tail"):
        from library.tools.tv_power import switch_shape

        # ONE shape, two directions: the window a head is checked over
        # is the length of the same animation the tail draws, so the
        # two windows cannot fall out of step with each other.
        timing = dict(switch_shape())
        timing.update(effects.get(f"{key}_timing") or {})
        return timing
    raise UnknownTreatment(
        f"treatment {key!r} is not one of {list(TREATMENT_KEYS)} - "
        f"no declared window to check it against.")


def treatment_total(key: str, effects: dict) -> int:
    """How many frames this treatment's animation spans, by declaration.

    Sums the phase LENGTHS only: the head's timing carries
    ``collapse_crop`` (a depth, not a length), and adding a crop
    fraction to a frame count would move the window every time the
    look changes.
    """
    return sum(int(v) for k, v in timing_for(key, effects).items()
               if k.endswith("_frames"))


def treatment_window(key: str, effects: dict, first: int,
                     last: int) -> Tuple[int, int, str]:
    """(window_start, window_end, neutral_end) in comp frames.

    The window is where the treatment is ALLOWED to change the picture.
    ``neutral_end`` is which end must read neutral: a head opens TO
    neutral (its end), a tail closes FROM neutral (its start) - the same
    half-decided neutral ``transition_frames`` uses, without naming any
    nodes.
    """
    total = treatment_total(key, effects)
    if key == "tv_power_head":
        return (first, first + total, "end")
    if key == "tv_power_tail":
        return (max(first, last - total), last, "start")
    raise UnknownTreatment(
        f"treatment {key!r} is not one of {list(TREATMENT_KEYS)}.")


def remove_treatment(effects: dict, key: str) -> Dict[str, Any]:
    """Drop a treatment and its timing: the undo.

    Returns a new dict; the input is untouched. Rebuilding from the
    result yields the exact untreated bytes - proven by string equality
    in `tests/test_treatment_verify.py`, not asserted.
    """
    return {k: v for k, v in (effects or {}).items()
            if k != key and k != f"{key}_timing"}


def _played_horizon(clip_dur: int, effects: dict,
                    played_frames: Optional[int]) -> Tuple[int, int, str]:
    """(first, last, horizon) the animation is checked over.

    ``played_frames`` is how many frames the timeline really renders -
    the timeline item's own duration, which the applier reads off the
    placed clip. Without it the source span stands in, and the check
    SAYS so: a horizon assumed from the source span is blind to the
    pool-fps vs timeline-fps mismatch that parks an end-anchored
    animation past everything rendered.
    """
    from library.tools.fusion.played_window import played_range

    src_in = (effects or {}).get("source_in_frame")
    src_out = (effects or {}).get("source_out_frame")
    first, last = played_range(clip_dur, src_in, src_out)
    if played_frames is None:
        return first, last, "assumed_from_source_span"
    return first, min(last, max(first, int(played_frames) - 1)), "rendered"


def build_treated(effects: dict, clip_dur: int,
                  *, source_res: tuple,
                  played_frames: Optional[int] = None) -> str:
    """The comp with the treatment, as the applier would write it.

    ``source_res`` is REQUIRED - the source clip's own frame, the same
    value the renderer reads off the MediaPoolItem. There is no
    fallback size: ``build_effect_comp`` raises ``MissingSourceFrame``
    on a missing one rather than sizing a canvas by guess.
    """
    from library.tools.fusion.comp_builder import build_effect_comp

    return build_effect_comp(dict(effects), clip_dur,
                             source_res=source_res,
                             played_frames=played_frames)


def build_untreated(effects: dict, key: str, clip_dur: int,
                    *, source_res: tuple,
                    played_frames: Optional[int] = None) -> str:
    """The comp with this treatment undone - the BEFORE half."""
    return build_treated(remove_treatment(effects, key), clip_dur,
                         source_res=source_res,
                         played_frames=played_frames)


def build_alone(key: str, effects: dict, clip_dur: int,
                *, source_res: tuple,
                played_frames: Optional[int] = None) -> str:
    """This treatment's nodes and nothing else.

    ``source_res`` is REQUIRED, as in ``build_treated``: the alone
    comp is built by the same builder and sizes the same canvas.

    The treatment builders are self-contained blocks (their internal
    wiring is asserted in `tests/test_tv_power.py`), so the treatment's
    drawn effect is its own splines' deviation from neutral. Building it
    alone attributes every evaluated curve to the key under check -
    no node-name matching, which collides when one clip carries both
    halves (a single-clip reel names both crops ``PowerCrop``).
    """
    alone = {k: v for k, v in (effects or {}).items()
             if k in (key, f"{key}_timing",
                      "source_in_frame", "source_out_frame")}
    alone[key] = (effects or {}).get(key, True)
    return build_treated(alone, clip_dur, source_res=source_res,
                         played_frames=played_frames)


def evaluate_comp(comp_text: str, played: int) -> Dict[str, List[float]]:
    """Every animated spline's value on each rendered frame.

    Reuses `transition_frames` flat-extrapolation semantics: what Fusion
    holds outside the key range is the nearest key's value, not zero.
    """
    from library.tools.fusion.transition_frames import (
        parse_splines, value_at)

    return {name: [value_at(keys, f) for f in range(played)]
            for name, keys in parse_splines(comp_text).items()}


def neutral_value(spline_name: str) -> Optional[float]:
    """What this spline reads where the treatment draws nothing.

    By name suffix, the same way the comp names them: the deflection
    band's Height rests at 1 (the whole frame shows), Transform size at
    1, gains and blends at their pass-through.
    None means "cannot tell" - a curve nothing here judges is reported,
    never gated, because a gate that fails correct output is worthless
    (AGENTS.md 10.4).

    ``Top``/``tom`` were the switch animation's Crop edges, which rested
    at 0. That construction never drew - Fusion's Crop has no such
    inputs (`library/tools/fusion/tool_inputs.py`) - and the block is now
    a masked band whose one animated term is ``Height``.
    """
    name = spline_name or ""
    if name.endswith("Height"):
        return 1.0
    if name.endswith("Size"):
        return 1.0
    if name.endswith(("Gain", "Blend")):
        # Gain rests at 1.0 (pass-through); a Merge Blend that carries
        # an effect rests wherever the block leaves it, so only the
        # power blocks' own Gain splines - named *Gain - are gated.
        return 1.0 if name.endswith("Gain") else None
    return None


def check_window(curves: Dict[str, List[float]], window: Tuple[int, int],
                 played: int) -> Dict[str, Any]:
    """Every changed frame sits inside the declared window.

    `curves` maps spline name to per-frame values over the `played`
    rendered frames. Returns the verdict; `outside_window` names the
    frames that changed where the treatment promised it would not.
    Splines with no known neutral are abstained from, never failed.
    """
    w0, w1 = window
    outside: List[int] = []
    judged = 0
    for name, values in (curves or {}).items():
        neutral = neutral_value(name)
        if neutral is None:
            continue
        judged += 1
        for f in range(min(played, len(values))):
            if abs(values[f] - neutral) > EPSILON and not (w0 <= f <= w1):
                if f not in outside:
                    outside.append(f)
    outside.sort()
    return {
        "passed": not outside,
        "outside_window": outside,
        "window": [w0, w1],
        "splines_judged": judged,
    }


def verify_treatment(effects: dict, key: str, clip_dur: int,
                     played_frames: Optional[int] = None,
                     *, source_res: tuple) -> Dict[str, Any]:
    """The before/after verdict for one treatment on one clip.

    Builds the comp with and without the key, evaluates the treatment's
    own splines over the rendered frames, and gates three ways:
    - ``outside_window``: changed frames outside the declared window;
    - ``drew_nothing``: the key armed nodes but no frame changed (an
      animation keyed past everything rendered);
    - ``never_settles``: the animation is still drawn on the last frame
      it claims to leave neutral (a ramp longer than its clip).
    `passed` is False when any fires. Measurements that inform but never
    gate - `min_kept_fraction`, `max_gain` - travel alongside, because
    "the picture changed by N%" is the model's evidence, not its verdict.
    """
    t0 = time.perf_counter()
    if key not in TREATMENT_KEYS:
        raise UnknownTreatment(
            f"treatment {key!r} is not one of {list(TREATMENT_KEYS)}.")

    from library.tools.fusion.played_window import TransitionLongerThanTheClip

    refused: Optional[Exception] = None

    def _refusal_verdict(exc: Exception) -> Dict[str, Any]:
        # The builder refuses a ramp the clip has no room for, by name
        # (`played_window`). A head on a 10-frame clip never reaches
        # neutral - the whole clip would play collapsed - so the
        # refusal IS the never_settles verdict, receiptable here and
        # undone by the caller.
        return {
            "treatment": key,
            "passed": False,
            "failure": "never_settles",
            "armed_nothing": False,
            "played_frames": played_frames,
            "horizon": ("rendered" if played_frames is not None
                        else "assumed_from_source_span"),
            "window": [],
            "changed_frames": [],
            "changed_count": 0,
            "outside_window": [],
            "drew_nothing": False,
            "settles": False,
            "min_kept_fraction": 0.0,
            "max_gain": 1.0,
            "splines_judged": 0,
            "detail": str(exc),
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
        }

    try:
        treated = build_treated(effects, clip_dur, source_res=source_res,
                                played_frames=played_frames)
    except TransitionLongerThanTheClip as exc:
        treated, refused = None, exc
    try:
        untreated = build_untreated(effects, key, clip_dur,
                                    source_res=source_res,
                                    played_frames=played_frames)
    except TransitionLongerThanTheClip as exc:
        untreated, refused = None, exc
    if treated is None and untreated is not None:
        # Removing this key clears the refusal: the over-long ramp is
        # this key's, so the verdict is its to carry.
        return _refusal_verdict(refused)
    if treated is None or untreated is None:
        # Both refuse: another key's ramp is over-long too, so the
        # full-comp comparison cannot attribute anything. This key is
        # judged on its own nodes below - and if those refuse as well,
        # the verdict is still its to carry.
        try:
            alone_probe = build_alone(key, effects or {}, clip_dur,
                                      source_res=source_res,
                                      played_frames=played_frames)
        except TransitionLongerThanTheClip as exc:
            return _refusal_verdict(exc)
        treated, untreated = alone_probe, ""
    if treated == untreated:
        # The key armed no nodes at all (all-zero timing returns an
        # empty block). Nothing to undo: the untreated bytes already
        # ship. Said, not failed - an empty plan entry is the planner's
        # business, and this gate judges the picture.
        return {
            "treatment": key,
            "passed": True,
            "failure": None,
            "armed_nothing": True,
            "played_frames": played_frames,
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
        }

    first, last, horizon = _played_horizon(clip_dur, effects or {},
                                           played_frames)
    played = (last - first + 1) if played_frames is None else int(
        played_frames)
    played = max(1, played)
    w0, w1, neutral_end = treatment_window(key, effects or {}, first,
                                           last)

    alone = build_alone(key, effects or {}, clip_dur,
                        source_res=source_res,
                        played_frames=played_frames)
    curves = evaluate_comp(alone, played)

    judged = {n: v for n, v in curves.items()
              if neutral_value(n) is not None}
    # Frame f changed when any judged spline leaves neutral there.
    changed = sorted({
        f for f in range(played)
        for n, v in judged.items()
        if f < len(v) and abs(v[f] - neutral_value(n)) > EPSILON
    })

    window_check = check_window(curves, (w0, w1), played)

    drew_nothing = not changed
    if neutral_end == "end":
        settles = all(
            abs(curves[n][played - 1] - neutral_value(n)) <= EPSILON
            for n in judged if len(curves[n]) >= played)
    else:
        settles = all(
            abs(curves[n][0] - neutral_value(n)) <= EPSILON
            for n in judged if curves[n])

    failure = None
    if window_check["outside_window"]:
        failure = "outside_window"
    elif drew_nothing:
        failure = "drew_nothing"
    elif not settles:
        failure = "never_settles"

    kept = _kept_series(curves, played)
    gains = [v[f] for n, v in judged.items() if n.endswith("Gain")
             for f in range(min(played, len(v)))]

    return {
        "treatment": key,
        "passed": failure is None,
        "failure": failure,
        "armed_nothing": False,
        "played_frames": played,
        "horizon": horizon,
        "window": [w0, w1],
        "changed_frames": changed,
        "changed_count": len(changed),
        "outside_window": window_check["outside_window"],
        "drew_nothing": drew_nothing,
        "settles": settles,
        "min_kept_fraction": round(min(kept), 4) if kept else 1.0,
        "max_gain": round(max(gains), 4) if gains else 1.0,
        "splines_judged": window_check["splines_judged"],
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
    }


def verify_and_undo(effects: dict, clip_dur: int,
                    played_frames: Optional[int],
                    *, source_res: tuple
                    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Check each armed treatment; drop the ones that fail. The undo.

    Returns `(final_effects, rows)`: `final_effects` is what the
    applier builds the comp from, `rows` record one line per armed key
    - what it measured, whether it passed, and whether it was undone.
    A failing key is removed and the picture returns to what it was;
    the row says which key went and why. Never raises for a bad
    treatment: a render must survive its decorations, loudly (the rows
    are receipted) rather than either silently or not at all.

    ``source_res`` is REQUIRED all the same: it is not a treatment
    and a missing frame is not a bad value. The applier reads it off
    the MediaPoolItem and refuses where Resolve will not state one,
    so this layer never has to guess a canvas size.
    """
    final = dict(effects or {})
    rows: List[Dict[str, Any]] = []
    for key in TREATMENT_KEYS:
        if not (effects or {}).get(key):
            continue
        verdict = verify_treatment(final, key, clip_dur,
                                   played_frames=played_frames,
                                   source_res=source_res)
        undone = not verdict.get("passed")
        if undone:
            final = remove_treatment(final, key)
        rows.append({
            "treatment": key,
            "passed": bool(verdict.get("passed")),
            "failure": verdict.get("failure"),
            "undone": undone,
            "played_frames": verdict.get("played_frames"),
            "horizon": verdict.get("horizon"),
            "window": verdict.get("window"),
            "changed_count": verdict.get("changed_count"),
            "min_kept_fraction": verdict.get("min_kept_fraction"),
            "detail": verdict.get("detail"),
            "elapsed_seconds": verdict.get("elapsed_seconds"),
        })
    return final, rows


def drift_armed(effects: Optional[dict]) -> bool:
    """Whether this clip carries any drift key at all."""
    return any(k in (effects or {}) for k in DRIFT_KEYS)


def remove_drift(effects: dict) -> Dict[str, Any]:
    """Drop a drift and nothing else: the undo.

    Returns a new dict; the input is untouched. A drift that drew
    nothing built an empty zoom block, so rebuilding from the result
    yields the exact undrifted bytes - proven by string equality in
    `tests/test_drift_draws.py`, not asserted.
    """
    return {k: v for k, v in (effects or {}).items()
            if k not in DRIFT_KEYS + DRIFT_CURVE_KEYS}


def _drift_values(effects: dict) -> Tuple[float, float, float, Any]:
    """(zoom_start, zoom_mid, zoom_end, pan_end), read as `fx.zoom` reads
    them: every zoom defaulting to 1.0, the pan defaulting to absent."""
    params = effects or {}
    start = params.get("zoom_start", 1.0)
    mid = params.get("zoom_mid", 1.0)
    end = params.get("zoom_end", 1.0)
    return start, mid, end, params.get("pan_end")


def verify_drift(effects: dict, clip_dur: int,
                 played_frames: Optional[int] = None,
                 *, source_res: tuple) -> Dict[str, Any]:
    """The before/after verdict for one clip's Ken Burns drift.

    Builds the drift's own comp, evaluates its Transform Size spline
    over the rendered frames, and samples the framing at the first and
    last of them: a drift moved when those two values differ. Gates
    one way:

    - ``drew_nothing``: drift keys are armed but the picture never
      moves - equal zooms with no pan (``fx.zoom`` builds an empty
      block), or values describing motion the comp carries no Size
      animation for (a name the renderer does not dispatch on).

    A constant reframe (all three zooms equal but off 1.0, or a static
    ``pan_end`` recentre) PASSES with ``motion_over_time`` False: the
    picture changed, so failing it would be a gate failing correct
    output (AGENTS.md 10.4). The promise of motion unkept is receipted
    for the model to read, never enforced here.

    Where the drift moves, the verdict also carries its SHAPE, sampled
    off the serialized comp - the bytes Resolve holds, never the spline
    object's flags: ``mid_value`` (the Size at the middle rendered
    frame), ``max_linear_deviation`` (how far any rendered frame sits
    from the straight line between the endpoints) and ``eased``
    (whether that deviation clears ``EASED_THRESHOLD``). A linear ramp
    and a cubic ease share endpoints, so endpoints alone cannot tell
    them apart; the shape is REPORTED, never gated.

    Callers pass the plan's effects BEFORE `normalize_effects`: the
    normalizer injects all-1.0 zoom defaults where nothing was armed,
    and those defaults are byte-identical to an explicit no-motion
    plan - judging them would fail every still shot.
    """
    t0 = time.perf_counter()
    if not drift_armed(effects):
        return {
            "treatment": "drift",
            "passed": True,
            "failure": None,
            "armed_nothing": True,
            "motion_over_time": False,
            "played_frames": played_frames,
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
        }

    # Judge the bytes the renderer writes: the applier runs
    # `normalize_effects` before building (defaulting `zoom_mid` to the
    # midpoint), so a manifest carrying only start/end still animates
    # through the middle. Verifying un-normalized values would fail a
    # drift the renderer draws.
    from library.tools.fusion.comp_builder import normalize_effects

    normalized = dict(effects or {})
    normalize_effects(normalized, True)
    start, mid, end, pan_end = _drift_values(normalized)
    if start == mid == end:
        if start != 1.0 or pan_end is not None:
            return {
                "treatment": "drift",
                "passed": True,
                "failure": None,
                "armed_nothing": False,
                "motion_over_time": False,
                "played_frames": played_frames,
                "start_value": float(start),
                "end_value": float(start),
                "changed_count": 0,
                "detail": ("a constant reframe, not motion: it draws "
                           "but the framing never moves"),
                "elapsed_seconds": round(time.perf_counter() - t0, 3),
            }
        return {
            "treatment": "drift",
            "passed": False,
            "failure": "drew_nothing",
            "armed_nothing": False,
            "motion_over_time": False,
            "played_frames": played_frames,
            "start_value": 1.0,
            "end_value": 1.0,
            "changed_count": 0,
            "detail": ("drift keys are armed but every zoom is 1.0 "
                       "with no pan: fx.zoom builds an empty block, "
                       "so 0 rendered frames move"),
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
        }

    first, last, horizon = _played_horizon(clip_dur, effects or {},
                                           played_frames)
    played = (last - first + 1) if played_frames is None else int(
        played_frames)
    played = max(1, played)

    alone = {k: v for k, v in normalized.items()
             if k in DRIFT_KEYS + DRIFT_CURVE_KEYS
             + ("source_in_frame", "source_out_frame")}
    comp = build_treated(alone, clip_dur, source_res=source_res,
                         played_frames=played_frames)
    curves = evaluate_comp(comp, played)
    sizes = {n: v for n, v in curves.items() if n.endswith("Size")}
    if not sizes:
        return {
            "treatment": "drift",
            "passed": False,
            "failure": "drew_nothing",
            "armed_nothing": False,
            "motion_over_time": False,
            "played_frames": played,
            "horizon": horizon,
            "start_value": None,
            "end_value": None,
            "changed_count": 0,
            "detail": ("the values describe motion but the comp "
                       "carries no Size animation for it"),
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
        }

    # The alone comp carries one zoom block; take the widest spline so
    # a second Size curve elsewhere can never mask a flat drift.
    name = max(sizes, key=lambda n: max(sizes[n]) - min(sizes[n]))
    values = sizes[name]
    rendered = min(played, len(values))
    first_value = values[0]
    last_value = values[rendered - 1]
    changed = [f for f in range(rendered)
               if abs(values[f] - first_value) > EPSILON]
    motion = abs(last_value - first_value) > EPSILON
    # The shape between the endpoints, off the serialized keys - what
    # Resolve holds. The straight line is between the first and last
    # RENDERED values, so a ramp cut short by the pool-to-timeline
    # mismatch is still judged on what plays.
    mid_value = values[rendered // 2]
    if rendered > 1:
        span = last_value - first_value
        max_linear_deviation = max(
            abs(values[f] - (first_value + span * f / (rendered - 1)))
            for f in range(rendered))
    else:
        max_linear_deviation = 0.0
    return {
        "treatment": "drift",
        "passed": motion,
        "failure": None if motion else "drew_nothing",
        "armed_nothing": False,
        "motion_over_time": motion,
        "played_frames": played,
        "horizon": horizon,
        "start_value": round(first_value, 6),
        "end_value": round(last_value, 6),
        "mid_value": round(mid_value, 6),
        "max_linear_deviation": round(max_linear_deviation, 6),
        "eased": bool(max_linear_deviation > EASED_THRESHOLD),
        "changed_count": len(changed),
        "changed_frames": changed,
        "detail": (None if motion else
                   "the Size spline is flat over everything rendered"),
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
    }


def verify_and_undo_drift(effects: dict, clip_dur: int,
                          played_frames: Optional[int],
                          *, source_res: tuple
                          ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """Check an armed drift; drop it when it drew nothing. The undo.

    Returns `(final_effects, row)`: `row` is None where no drift key
    is armed, so the applier writes no receipt for a still shot. A
    failing drift is removed and the picture returns to what it was;
    the row says so. Never raises for bad values: a render must
    survive its decorations, loudly rather than either silently or
    not at all.
    """
    if not drift_armed(effects):
        return dict(effects or {}), None
    verdict = verify_drift(dict(effects or {}), clip_dur,
                           played_frames=played_frames,
                           source_res=source_res)
    undone = not verdict.get("passed")
    final = remove_drift(effects) if undone else dict(effects or {})
    row = {
        "treatment": "drift",
        "passed": bool(verdict.get("passed")),
        "failure": verdict.get("failure"),
        "undone": undone,
        "played_frames": verdict.get("played_frames"),
        "horizon": verdict.get("horizon"),
        "start_value": verdict.get("start_value"),
        "end_value": verdict.get("end_value"),
        "mid_value": verdict.get("mid_value"),
        "max_linear_deviation": verdict.get("max_linear_deviation"),
        "eased": verdict.get("eased"),
        "changed_count": verdict.get("changed_count"),
        "motion_over_time": verdict.get("motion_over_time"),
        "detail": verdict.get("detail"),
        "elapsed_seconds": verdict.get("elapsed_seconds"),
    }
    return final, row


def _kept_series(curves: Dict[str, List[float]],
                  played: int) -> List[float]:
    """Fraction of picture height the deflection band keeps, per frame.

    The band's ``Height`` IS the kept fraction: the mask shows the
    picture inside it and the black Background covers everything else.
    Where several bands are present (a single-clip reel carries both the
    switch-on and the switch-off), the tightest wins - the picture is
    only as visible as the narrowest slot in front of it.
    """
    bands = [n for n in curves if n.endswith("Height")]
    if not bands:
        return [1.0] * played
    out = []
    for f in range(played):
        kept = min((_clamped(curves, n, f) for n in bands), default=1.0)
        out.append(kept)
    return out


def _clamped(curves: Dict[str, List[float]], name: str, f: int) -> float:
    """One band curve at frame `f`, clamped to 0..1."""
    values = curves.get(name) or []
    if not values:
        return 1.0
    value = values[f] if f < len(values) else values[-1]
    return max(0.0, min(1.0, float(value)))


#: The head frames the captain judges a power-on opening on: the
#: strike (0), inside the hold (2), the hold-to-ramp edge (5), the
#: ramp open (10), and the bloom settled (18).  One enumeration, so a
#: report and a test cannot sample different frames.
HEAD_JUDGE_FRAMES = (0, 2, 5, 10, 18)


def sample_head_frames(effects: dict, clip_dur: int,
                       frames=HEAD_JUDGE_FRAMES,
                       played_frames: Optional[int] = None,
                       *, source_res: tuple
                       ) -> List[Dict[str, Any]]:
    """What the switch-on draws at the judged frames: decoded, not asserted.

    Builds the head's own comp (the bytes the renderer writes), evaluates
    its splines, and reports per frame how much picture height the crop
    keeps and how hot the gain spike is.  The head is a Crop plus a
    BrightnessContrast and nothing else, so those two numbers ARE the
    opening - no Resolve, no footage, no model needed.  A variant that
    changes the look changes this table; a variant that does not, does
    not.
    """
    alone = build_alone("tv_power_head", effects or {}, clip_dur,
                        source_res=source_res,
                        played_frames=played_frames)
    need = max(frames) + 1 if frames else 1
    played = max(int(played_frames) if played_frames else need, need)
    curves = evaluate_comp(alone, played)
    kept = _kept_series(curves, played)
    names = [n for n in curves if n.endswith("Gain")]
    rows = []
    for f in frames:
        gain = (max(curves[n][f] for n in names)
                if names and all(len(curves[n]) > f for n in names)
                else 1.0)
        rows.append({
            "frame": int(f),
            "kept_fraction": round(kept[f] if f < len(kept) else 1.0, 4),
            "gain": round(float(gain), 4),
        })
    return rows
