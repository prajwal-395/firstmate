"""Zoom emphasis carries independent, anchored ramps into Fusion keyframes."""

from __future__ import annotations

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

HANDOFF = (
    Path(__file__).resolve().parents[1] / "library/steps/step_4_03_plan_vfx/handoff.md"
)
FPS = 30.0


def _spine(end_seconds=10.0):
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
        _spine(end_seconds),
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
        resolve_vfx([plan], _spine(), frame_rate=FPS)

    plan = _plan()[0]
    del plan["anchor_end"]
    with pytest.raises(PunchTimingRefused, match="anchor_end"):
        resolve_vfx([plan], _spine(), frame_rate=FPS)
