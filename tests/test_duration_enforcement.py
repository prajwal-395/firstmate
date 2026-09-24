"""Tests for duration enforcement in review_rough_cut and creative_cohesion.

The zone is the PROJECT's declaration, and there is no longer a fallback.
`get_target_duration_zone` used to answer `(54.0, 60.0, 66.0)` whenever
nothing declared a target - and nothing ever did, because no state key
and no DAG edge carried `project_config` to any step.  So the captain's
own `target_duration_seconds: 60` in project 001's project.yaml governed
nothing, and every one of these gates ran against a minute the pipeline
made up.  `load_pipeline_state` now reads the declaration and the runner
broadcasts it; with nothing declared, a gate reports that it did not
check rather than judging the cut against an invented length.

`DECLARES_60` below is 001's own declaration.
"""
import json
import pytest

from library.steps.step_3_03_review_rough_cut.step import (
    check_total_duration,
    run_mechanical_checks,
)
from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion


# 001's own project.yaml declaration: 60 seconds, so the zone is 54-66.
DECLARES_60 = {"project_config": {"target_duration_seconds": 60}}


# --- step_3_03 total duration tests ---

class TestCheckTotalDuration:
    """check_total_duration hard-fails when duration is outside target zone."""

    def test_within_target_passes(self):
        """60s actual vs 60s target = passes."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 60}]
        result = check_total_duration(a_rolls, {}, DECLARES_60)
        assert result["passed"] is True
        assert result["actual_duration_seconds"] == 60.0

    def test_over_threshold_fails(self):
        """67s actual vs 60s target (max is 66), should fail."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 67}]
        result = check_total_duration(a_rolls, {}, DECLARES_60)
        assert result["passed"] is False

    def test_nothing_declared_is_reported_not_judged(self):
        """The gate used to be handed 54-66s whatever a project declared.

        A gate with nothing to judge against is not coverage
        (AGENTS.md 10.4), so it says which declaration was missing.
        """
        result = check_total_duration(
            [{"timeline_start": 0, "timeline_end": 516}], {}, {})
        assert result["checked"] is False
        assert result["passed"] is True
        assert "target_duration_seconds" in result["reason"]
        assert result["actual_duration_seconds"] == 516.0


class TestRunMechanicalChecksWithDuration:
    """run_mechanical_checks integrates total_duration check."""

    def test_duration_overshoot_rejects(self):
        data = {
            "a_roll_assignments": [
                {"timeline_start": 0, "timeline_end": 516,
                 "spine_block_position": 1, "block_type": "speech",
                 "video_segments": [{"video_in": 0, "video_out": 516}]}
            ],
            "b_roll_assignments": [],
            "audio_spine": {},
            "project_folder": ".",
            **DECLARES_60,
        }
        result = run_mechanical_checks(data)
        assert result["passed"] is False
        assert result["mechanical_checks"]["total_duration"]["passed"] is False
        assert any("Total duration" in r for r in result["rejection_reasons"])


# --- step_5_03 duration warning tests ---

def _spine(seconds):
    """The timeline, which is what the duration gate measures.

    These fixtures used to express the length as `body_sequence[-1]
    ["end_time"]` - a SOURCE timestamp, where the last passage ends
    inside its own clip - because that is what the gate read. It made the
    gate warn about a length the video never had: 001's 54.77s timeline
    was reported as 40.1s and "below the minimum target zone".
    """
    return {"structure": [
        {"block_type": "speech", "position": 0,
         "timeline_start": 0.0, "timeline_end": float(seconds)},
    ]}


class TestCohesionDurationWarning:
    """creative_cohesion warns (does not fail) when outside target zone."""

    def test_no_project_config_states_that_nothing_was_checked(self):
        """It used to fall back to a 54-66s zone nobody declared."""
        inputs = {
            "creative_direction": {"target_energy": "moderate"},
            "transition_spec": [],
            "sfx_spec": [],
            "speech_sequence": {
                "body_sequence": [{"start_time": 0, "end_time": 120}]
            },
            "audio_spine": _spine(120),
            "color_grade_spec": {},
        }
        review = review_creative_cohesion(inputs)
        assert any(w.startswith("Duration not checked:")
                   for w in review["warnings"])
        assert not any("Duration warning" in w for w in review["warnings"])

        # And with the project's own declaration it fires as before.
        review = review_creative_cohesion({**inputs, **DECLARES_60})
        assert any("Duration warning" in w for w in review["warnings"])
