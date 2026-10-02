"""A planned drift must move the framing, and prove it off the comp.

`resolve_vfx` checks drift VALUES never - a magnitude is taste, so no
bound, no clamp, no substitution. The degenerate case rides along
with that ruling: a reasoned `slow_zoom_in` with equal zooms resolves
as PLANNED, reaches `fx.zoom`, and builds an empty block. The manifest
records motion the viewer never sees, and nothing anywhere says so -
the TV switch-off all over again, 0 of 72 frames changed, no error.

`treatment_verify.verify_drift` is the deterministic half that looks:
it samples the Transform Size spline at the first and last rendered
frames, and a drift that did not move is undone by `remove_drift`
the way a failing treatment is - loudly, receipted, in the applier.
"""
from __future__ import annotations
import sys
from pathlib import Path
import pytest
from library.steps.step_4_03_plan_vfx.post_bridge import resolve_vfx
from library.tools.fusion.comp_builder import build_effect_comp
from library.tools.punch_timing import (
    MAX_PUNCH_RAMP_SECONDS,
    MIN_PUNCH_RAMP_SECONDS,
    PunchTimingRefused,
    ramp_duration_frames,
)


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import treatment_verify as tv

CLIP_DUR = 600
PLAYED = 72
#: The source frame every verify call below states. The verify layer
#: takes no default frame, so each call states it - the same numbers
#: the removed builder defaults carried.
SOURCE_RES = (1080, 1920)
"""The TV switch-off's own frame count: a 3-second window of 30 fps
pool footage cut onto a 24000/1001 reel timeline."""


def _spine(*positions):
    return {"structure": [
        {"position": p, "block_type": "speech",
         "timeline_start": float(i * 5), "timeline_end": float(i * 5 + 5)}
        for i, p in enumerate(positions)
    ]}


def _reasoned_drift(**params):
    return {"target_block_position": 1, "effect_type": "slow_zoom_in",
            "params": dict(params),
            "rationale": "a locked hold that goes dead under the point"}


def test_an_equal_zoom_drift_resolves_as_planned_but_builds_neutral():
    """The shipped defect, characterized with old APIs alone.

    Values are never bounded at plan time, so equal zooms resolve -
    and the renderer builds an empty zoom block, byte-identical to no
    drift at all. This passes before and after: it is the defect the
    gate below exists to catch.
    """
    resolved = resolve_vfx(
        [_reasoned_drift(zoom_start=1.0, zoom_end=1.0)], _spine(1, 2))
    assert len(resolved) == 1
    neutral = build_effect_comp({"vignette": False}, CLIP_DUR,
                                source_res=SOURCE_RES)
    drawn = build_effect_comp(
        dict(resolved[0]["params"], vignette=False), CLIP_DUR,
        source_res=SOURCE_RES)
    assert drawn == neutral


def test_equal_zoom_drift_fails_the_draw_gate():
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0},
        CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
    assert verdict["passed"] is False
    assert verdict["failure"] == "drew_nothing"
    assert verdict["changed_count"] == 0


def test_a_push_in_moves_start_to_end():
    """The pixels proof: first vs last rendered frame, off the comp."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.0, "zoom_mid": 1.02, "zoom_end": 1.04,
         "source_in_frame": 0, "source_out_frame": 90},
        CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
    assert verdict["passed"] is True
    assert verdict["motion_over_time"] is True
    assert verdict["end_value"] > verdict["start_value"]
    assert verdict["changed_count"] > 0


def test_an_anchored_cut_in_draws_inside_its_window():
    """A word-anchored punch returns to neutral outside its word span."""
    verdict = tv.verify_drift(
        {"zoom_start": 1.15, "zoom_mid": 1.15, "zoom_end": 1.15,
         "effect_window_frames": [6, 11]},
        CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)

    assert verdict["passed"] is True
    assert verdict["changed_frames"] == list(range(6, 12))
    assert verdict["start_value"] == 1.0
    assert verdict["end_value"] == 1.0
    assert verdict["motion_over_time"] is True


def test_undo_restores_byte_identical_comp():
    """Removing a failed drift returns the exact undrifted bytes."""
    effects = {"zoom_start": 1.0, "zoom_mid": 1.0, "zoom_end": 1.0,
               "tv_power_head": True}
    final, row = tv.verify_and_undo_drift(
        effects, CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
    assert row["undone"] is True
    assert row["failure"] == "drew_nothing"
    assert final == {"tv_power_head": True}
    assert (build_effect_comp(final, CLIP_DUR, played_frames=PLAYED, source_res=SOURCE_RES)
            == build_effect_comp({"tv_power_head": True}, CLIP_DUR,
                                 played_frames=PLAYED, source_res=SOURCE_RES))


# --------------------------------------------------------------------------
# From test_drift_easing_and_intensity.py
#
# A Ken Burns drift eases in and out, and may travel further than 0.04.
#
# Two asks from the captain on reel 09 (2026-09-10): the 1.00 -> 1.04 drift
# gap is "barely noticeable" (amplitude), and the drift should "smooth ease
# in and out" (curve). Both are judged by the captain, so this file pins
# the mechanism, not the choice: the ramp is an eased curve baked into the
# serialized comp, stronger gaps build, and the proof is sampled off the
# comp text - what Resolve HOLDS - never off the spline object's flags.
#
# Each test fails on the pre-change code: the old ramp was a three-point
# linear spline (mid defaulted to the midpoint, so exactly a straight
# line), anything past 1.04 was refused at comp build, and `verify_drift`
# sampled only the endpoints - which a linear ramp and a cubic ease share.

sys.path.insert(0, str(REPO))

from library.tools.fusion.comp_builder import normalize_effects
from library.tools.fusion.effects import _reset_counters, fx
from library.tools.fusion.nodes import EASING_FUNCTIONS, BezierSpline


#: The source frame every build and verify call below states. The
#: builders take no default frame, so each call states it - the same
#: numbers the removed defaults carried.

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


# --------------------------------------------------------------------------
# From test_kinetic_motion_reason.py
#
# Kinetic motion applies only where a per-shot reason is stated.
#
# Captain's ruling 2026-09-08: motion on a static shot is allowed WHEN THE
# MODEL CAN STATE WHY THAT SHOT WANTS IT, and is never a blanket rule or a
# default. A push that arrives because every static shot gets a push is
# exactly what this refuses - the same boundary as the standing "no house
# look" rule: the reasoning must be per-shot and stated, and a shot with no
# reason gets no motion.
#
# The mechanical half is in step 4.03's post-bridge: a drift entry
# (`slow_zoom_in` / `slow_zoom_out` - the moves that put life on a static
# hold) whose `rationale` is missing or blank is DROPPED with reason
# `no_stated_reason`, recorded in `planning_basis` like every other drop.
# The engine half already holds: `inject_default_ken_burns` is gone and
# the shared static checker watches creative-policy bridge code for its
# reintroduction.
#
# Scope is deliberate: the ruling is about motion on static shots, not
# about emphasis effects. A `zoom_emphasis` on material anchors is a
# different decision, with independent ramp timing and no drift rationale.

sys.path.insert(0, str(REPO))

from library.tools.vfx_plan_basis import DROP_REASONS


def _drift(position=1, **extra):
    entry = {"target_block_position": position,
             "effect_type": "slow_zoom_in",
             "params": {"zoom_start": 1.0, "zoom_end": 1.03}}
    entry.update(extra)
    return entry


def test_drift_without_a_stated_reason_gets_no_motion():
    """THE refusal input: a drift entry with params but no `rationale`
    key must not get motion."""
    dropped = []
    resolved = resolve_vfx([_drift()], _spine(1, 2), dropped=dropped)
    assert resolved == []
    assert len(dropped) == 1
    assert dropped[0].reason == "no_stated_reason"
    assert dropped[0].target_block_position == 1


def test_drift_with_a_stated_reason_resolves():
    """The ruling allows motion where the model states why - a reasoned
    entry is untouched by the refusal."""
    dropped = []
    resolved = resolve_vfx(
        [_drift(rationale="a six-second locked hold that goes dead")],
        _spine(1, 2), dropped=dropped)
    assert len(resolved) == 1
    assert resolved[0]["effect_type"] == "slow_zoom_in"
    assert dropped == []


def test_emphasis_without_rationale_is_outside_this_refusal():
    """The ruling governs motion on static shots, not emphasis. A
    `zoom_emphasis` with its own anchors and ramp decisions resolves
    without a drift rationale - this pins the boundary so that refusal
    cannot creep."""
    dropped = []
    resolved = resolve_vfx(
        [{"target_block_position": 1, "effect_type": "zoom_emphasis",
          "params": {"zoom_start": 1.0, "zoom_mid": 1.04,
                     "zoom_end": 1.0, "zoom_in_seconds": 0.67,
                     "zoom_out_seconds": 0.67},
          "anchor": {"frame": 60}, "anchor_end": {"frame": 90}}],
        _spine(1, 2), dropped=dropped)
    assert len(resolved) == 1
    assert dropped == []


# --------------------------------------------------------------------------
# From test_punch_timing.py
#
# Zoom emphasis carries independent, anchored ramps into Fusion keyframes.

HANDOFF = (
    Path(__file__).resolve().parents[3] / "library/steps/step_4_03_plan_vfx/handoff.md"
)
FPS = 30.0


def _spine_2(end_seconds=10.0):
    return {
        "structure": [
            {
                "position": 1,
                "block_type": "speech",
                "timeline_start": 0.0,
                "timeline_end": end_seconds,
            }
        ]
    }


def _plan(in_seconds=0.67, out_seconds=0.67, peak=90, release=120):
    return [
        {
            "target_block_position": 1,
            "effect_type": "zoom_emphasis",
            "params": {
                "zoom_start": 1.0,
                "zoom_mid": 1.15,
                "zoom_end": 1.0,
                "zoom_in_seconds": in_seconds,
                "zoom_out_seconds": out_seconds,
            },
            "anchor": {"frame": peak},
            "anchor_end": {"frame": release},
        }
    ]


def _resolved(
    in_seconds=0.67, out_seconds=0.67, peak=90, release=120, end_seconds=10.0
):
    return resolve_vfx(
        _plan(in_seconds, out_seconds, peak, release),
        _spine_2(end_seconds),
        frame_rate=FPS,
    )[0]


def _comp(vfx):
    start_frame = round(vfx["timeline_start"] * FPS)
    end_boundary = round(vfx["timeline_end"] * FPS)
    return build_effect_comp(
        dict(
            vfx["params"],
            effect_window_frames=[start_frame, end_boundary - 1],
            vignette=False,
        ),
        clip_dur=300,
        source_res=(3840, 2160),
    )


@pytest.mark.parametrize(
    ("value", "reason"),
    [(0.66, "whiplash"), (1.81, "indistinguishable")],
)
def test_a_ramp_outside_the_measured_band_is_refused(value, reason):
    with pytest.raises(PunchTimingRefused) as excinfo:
        ramp_duration_frames(value, movement="zoom-in", frame_rate=FPS)
    assert reason in excinfo.value.render()


def test_both_measured_edges_are_valid_and_converted_once_to_frames():
    assert (
        ramp_duration_frames(MIN_PUNCH_RAMP_SECONDS, movement="zoom-in", frame_rate=FPS)
        == 20
    )
    assert (
        ramp_duration_frames(
            MAX_PUNCH_RAMP_SECONDS, movement="zoom-out", frame_rate=FPS
        )
        == 54
    )


def test_the_hold_is_derived_from_peak_and_release_anchors():
    vfx = _resolved(in_seconds=0.67, out_seconds=1.8, peak=90, release=120)
    params = vfx["params"]
    assert params["zoom_in_duration_frames"] == 20
    assert params["zoom_release_offset_frames"] == 50
    assert params["zoom_out_duration_frames"] == 54
    assert (
        params["zoom_release_offset_frames"] - params["zoom_in_duration_frames"]
    ) == 30
    assert "hold_seconds" not in params
    assert "hold_frames" not in params
    assert not any("hold" in key for key in params)


def test_reversing_the_two_ramp_durations_reverses_the_drawn_curve():
    fast_in_slow_out = _resolved(0.67, 1.8)
    slow_in_fast_out = _resolved(1.8, 0.67)
    first = _comp(fast_in_slow_out)
    second = _comp(slow_in_fast_out)

    assert first != second
    assert "[70] = { 1.0" in first
    assert "[36] = { 1.0" in second
    assert "[90] = { 1.15" in first and "[90] = { 1.15" in second
    assert "[174] = { 1.0" in first
    assert "[140] = { 1.0" in second


def test_treatment_verifier_builds_the_timed_punch_it_will_judge():
    from library.tools.treatment_verify import verify_drift

    fast = _resolved(0.67, 0.67)
    slow = _resolved(1.8, 1.8)

    def verify(vfx):
        start = round(vfx["timeline_start"] * FPS)
        end = round(vfx["timeline_end"] * FPS)
        return verify_drift(
            dict(vfx["params"], effect_window_frames=[start, end - 1]),
            clip_dur=300,
            played_frames=300,
            source_res=(3840, 2160),
        )

    fast_result = verify(fast)
    slow_result = verify(slow)
    assert fast_result["passed"] and fast_result["motion_over_time"]
    assert slow_result["passed"] and slow_result["motion_over_time"]
    assert fast_result["changed_frames"] != slow_result["changed_frames"]
    assert fast_result["changed_count"] < slow_result["changed_count"]


@pytest.mark.parametrize(
    ("peak", "release", "end_seconds", "message"),
    [
        (10, 60, 5.0, "before the block starts"),
        (90, 140, 5.0, "past the block boundary"),
    ],
)
def test_a_ramp_that_does_not_fit_its_picture_span_is_refused(
    peak, release, end_seconds, message
):
    with pytest.raises(PunchTimingRefused) as excinfo:
        _resolved(0.67, 0.67, peak, release, end_seconds)
    assert message in excinfo.value.render()


def test_release_cannot_precede_the_peak():
    with pytest.raises(PunchTimingRefused, match="precedes its peak"):
        _resolved(0.67, 0.67, peak=120, release=110)


def test_zoom_emphasis_refuses_missing_timing_and_anchors():
    plan = _plan()[0]
    plan["params"].pop("zoom_out_seconds")
    with pytest.raises(PunchTimingRefused, match="zoom_out_seconds"):
        resolve_vfx([plan], _spine_2(), frame_rate=FPS)

    plan = _plan()[0]
    del plan["anchor_end"]
    with pytest.raises(PunchTimingRefused, match="anchor_end"):
        resolve_vfx([plan], _spine_2(), frame_rate=FPS)
