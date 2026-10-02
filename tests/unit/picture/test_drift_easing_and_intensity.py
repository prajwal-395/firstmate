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

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
from library.tools import treatment_verify as tv
from library.tools.fusion.comp_builder import build_effect_comp, normalize_effects
from library.tools.fusion.effects import _reset_counters, fx
from library.tools.fusion.nodes import EASING_FUNCTIONS, BezierSpline

CLIP_DUR = 600

#: The source frame every build and verify call below states. The
#: builders take no default frame, so each call states it - the same
#: numbers the removed defaults carried.
SOURCE_RES = (1080, 1920)

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
    comp = build_effect_comp(dict(effects), CLIP_DUR,
                             source_res=SOURCE_RES)
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


def test_stronger_gaps_build():
    """The amplitude options the captain chooses between - each a full
    comp, not a number in a table. Refused before; built now."""
    for end in (1.06, 1.08, 1.10):
        effects = {"zoom_start": 1.0, "zoom_end": end, "vignette": False}
        normalize_effects(effects, True)  # as apply_fusion_comps does
        comp = build_effect_comp(dict(effects), CLIP_DUR,
                             source_res=SOURCE_RES)
        assert "Transform1Size" in comp
        verdict = tv.verify_drift(
            {"zoom_start": 1.0, "zoom_end": end}, CLIP_DUR,
            source_res=SOURCE_RES)
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
            CLIP_DUR, source_res=SOURCE_RES)
    except ValueError as exc:
        assert "1.16" in str(exc)
    else:
        raise AssertionError("a 1.16 drift built a comp")
