"""A Ken Burns drift eases in and out, and may travel further than 0.04.

Two asks from the captain on reel 09 (2026-09-10): the 1.00 -> 1.04 drift
gap is "barely noticeable" (amplitude), and the drift should "smooth ease
in and out" (curve). Both are judged by the captain, so this file pins
the mechanism, not the choice: the ramp is an eased curve baked into the
serialized comp, stronger gaps build, and the proof is sampled off the
comp text - what Resolve HOLDS - never off the spline object's flags.

Each test fails on the pre-change code: the old ramp was a three-point
linear spline (mid defaulted to the midpoint, so exactly a straight
line), anything past 1.04 was refused at comp build, and `verify_drift`
sampled only the endpoints - which a linear ramp and a cubic ease share.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
from library.tools import treatment_verify as tv
from library.tools.fusion.comp_builder import build_effect_comp, normalize_effects
from library.tools.fusion.effects import _reset_counters, fx
from library.tools.fusion.nodes import EASING_FUNCTIONS, BezierSpline

CLIP_DUR = 600

#: The four drifts the captain approved for reel 09
#: (`reel_motion_09.json`): which shots drift and in which direction is
#: NOT this lane's to change, so the test pins all four exactly.
REEL_09_PLAN = [
    {"target_block_position": 0, "effect_type": "ken_burns",
     "params": {"zoom_start": 1.0, "zoom_end": 1.04},
     "rationale": "closes on the question rather than sitting back"},
    {"target_block_position": 1, "effect_type": "ken_burns",
     "params": {"zoom_start": 1.0, "zoom_end": 1.04},
     "rationale": "starts on the argument and ends on the statistic"},
    {"target_block_position": 4, "effect_type": "ken_burns",
     "params": {"zoom_start": 1.04, "zoom_end": 1.0},
     "rationale": "a pull back moves with that widening"},
    {"target_block_position": 6, "effect_type": "ken_burns",
     "params": {"zoom_start": 1.0, "zoom_end": 1.04},
     "rationale": "an invitation leans in"},
]


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _drift_curve(start=1.0, end=1.04):
    """The Size curve as Resolve holds it: parsed off the comp text."""
    effects = {"zoom_start": start, "zoom_mid": (start + end) / 2.0,
               "zoom_end": end, "vignette": False}
    comp = build_effect_comp(dict(effects), CLIP_DUR)
    curves = tv.evaluate_comp(comp, CLIP_DUR)
    sizes = {n: v for n, v in curves.items() if n.endswith("Size")}
    assert sizes, "drift built no Size spline"
    return comp, max(sizes.values(), key=lambda v: max(v) - min(v))


def test_the_ramp_is_not_a_straight_line_in_what_resolve_holds():
    """Quarter-frame samples leave the linear chord: the ease is baked
    into the serialized keys, not carried in flags Resolve ignores."""
    _, values = _drift_curve()
    played = len(values)
    first, last = values[0], values[-1]
    span = last - first
    assert span > 0
    worst = max(
        abs(values[f] - (first + span * f / (played - 1)))
        for f in range(played))
    assert worst > tv.EASED_THRESHOLD, (
        f"max deviation from linear is {worst:.6f}: the ramp is straight")
    quarter = values[played // 4]
    linear_quarter = first + span * 0.25
    assert abs(quarter - linear_quarter) > tv.EASED_THRESHOLD


def test_endpoints_and_midpoint_cannot_tell_ease_from_linear():
    """The trap this evidence exists for: a symmetric ease passes
    through the midpoint, so endpoints-plus-mid read identical for a
    ramp and a Sine. Only the off-centre samples carry the shape."""
    _, values = _drift_curve()
    played = len(values)
    first, last = values[0], values[-1]
    # The middle rendered frame sits within a pixel of the linear
    # midpoint (discrete frames never land exactly on t=0.5) while the
    # off-centre samples deviate forty times further - that ratio is
    # the whole reason endpoints cannot carry the shape.
    assert abs(values[played // 2] - (first + last) / 2.0) < 2e-4


def test_verify_drift_reports_the_shape_it_did_not_gate():
    """`passed` still comes from the endpoints alone; the curve travels
    beside it, reported never enforced."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_end": 1.04}, CLIP_DUR)
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is True
    assert verdict["eased"] is True
    assert verdict["max_linear_deviation"] > tv.EASED_THRESHOLD
    assert verdict["start_value"] < verdict["mid_value"] < verdict["end_value"]


def test_a_linear_drift_reports_uneased_but_still_passes():
    """Easing evidence must never fail correct output: an explicitly
    linear ramp moves, passes, and says it is not eased."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_end": 1.04, "zoom_easing": "Linear"},
        CLIP_DUR)
    assert verdict["passed"] is True
    assert verdict["eased"] is False
    assert verdict["max_linear_deviation"] < tv.EASED_THRESHOLD


def test_the_undo_receipt_carries_the_shape():
    final, row = tv.verify_and_undo_drift(
        {"zoom_start": 1.0, "zoom_end": 1.04}, CLIP_DUR,
        played_frames=None)
    assert final == {"zoom_start": 1.0, "zoom_end": 1.04}
    assert row["undone"] is False
    assert row["eased"] is True
    assert row["max_linear_deviation"] > tv.EASED_THRESHOLD


def test_an_unknown_easing_name_raises_instead_of_substituting():
    """A curve nobody named is taste invented on the plan's behalf -
    refuse it naming the vocabulary."""
    _reset_counters()
    try:
        fx.zoom(CLIP_DUR, start=1.0, mid=1.02, end=1.04,
                easing="Bounce")
    except ValueError as exc:
        assert "Bounce" in str(exc)
    else:
        raise AssertionError("unknown easing built a spline")


def test_a_punch_keeps_its_three_points():
    """`zoom_emphasis` is a peak, not a ramp: an off-midpoint `mid`
    keeps the three-point spline, easing untouched."""
    _reset_counters()
    block = fx.zoom(CLIP_DUR, start=1.0, mid=1.04, end=1.03)
    splines = [n for n in block.nodes if isinstance(n, BezierSpline)]
    assert len(splines) == 1
    assert len(splines[0].keyframes) == 3
    assert max(kf.value for kf in splines[0].keyframes) == 1.04


def test_linearize_writes_absolute_handle_points():
    """Resolve reads handles as absolute (frame, value) points - the
    convention DaVinci's own presets ship. Relative deltas would land
    near frame zero at near-zero values."""
    spline = BezierSpline("FadeSize")
    spline.add_key(0, 1.0, flags={"Linear": True})
    spline.add_key(12, 0.0, flags={"Linear": True})
    spline.linearize()
    first, second = spline.keyframes
    assert first.rh == (4.0, 1.0 - 1.0 / 3.0), first.rh
    assert second.lh == (8.0, 0.0 + 1.0 / 3.0), second.lh


def test_stronger_gaps_build():
    """The amplitude options the captain chooses between - each a full
    comp, not a number in a table. Refused before; built now."""
    for end in (1.06, 1.08, 1.10):
        effects = {"zoom_start": 1.0, "zoom_end": end, "vignette": False}
        normalize_effects(effects, True)  # as apply_fusion_comps does
        comp = build_effect_comp(dict(effects), CLIP_DUR)
        assert "Transform1Size" in comp
        verdict = tv.verify_drift(
            {"zoom_start": 1.0, "zoom_end": end}, CLIP_DUR)
        assert verdict["passed"] is True
        assert verdict["eased"] is True
    from library.tools.fusion.nodes import MAX_ANIMATED_ZOOM

    assert MAX_ANIMATED_ZOOM == 1.15


def test_a_runaway_zoom_is_still_refused():
    """The ceiling moved, it did not leave: a 1.16 peak is a typo, not
    a look, and still refuses by name."""
    try:
        build_effect_comp(
            {"zoom_start": 1.0, "zoom_end": 1.16, "vignette": False},
            CLIP_DUR)
    except ValueError as exc:
        assert "1.16" in str(exc)
    else:
        raise AssertionError("a 1.16 drift built a comp")


def test_reel_09_approved_directions_drift_eased():
    """The captain's four drifts, resolved as the reel resolves them:
    same shots, same directions, now eased. Shots 2, 3 and 5 carry no
    entry and stay still - asserted by their absence."""
    spine = _spine(0, 1, 2, 3, 4, 5, 6)
    resolved = resolve_vfx(REEL_09_PLAN, spine)
    assert [v["target_block_position"] for v in resolved] == [0, 1, 4, 6]
    for spec in resolved:
        params = dict(spec["params"])
        normalized = dict(params)
        normalize_effects(normalized, True)
        # The verdict judges the whole keyed span: a rendered window
        # much shorter than the span flattens any ease toward linear,
        # so the shape is read where the ramp actually plays.
        verdict = tv.verify_drift(normalized, CLIP_DUR,
                                  played_frames=None)
        assert verdict["passed"] is True, spec
        assert verdict["eased"] is True, spec
    directions = {
        v["target_block_position"]:
        "in" if v["params"]["zoom_end"] > v["params"]["zoom_start"] else "out"
        for v in resolved}
    assert directions == {0: "in", 1: "in", 4: "out", 6: "in"}


def test_all_easing_names_build_a_drift():
    """Every name in the vocabulary reaches a comp; the default is the
    Sine ease-in-out the captain asked for."""
    from library.tools.fusion.effects import DRIFT_EASING

    assert DRIFT_EASING == "Sine"
    for name in sorted(EASING_FUNCTIONS):
        effects = {"zoom_start": 1.0, "zoom_end": 1.04,
                   "zoom_easing": name, "vignette": False}
        normalize_effects(effects, True)  # as apply_fusion_comps does
        comp = build_effect_comp(dict(effects), CLIP_DUR)
        assert "Transform1Size" in comp, name
