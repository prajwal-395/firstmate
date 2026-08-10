"""Tests for duration enforcement in review_rough_cut and creative_cohesion."""
import json
import pytest

from library.steps.step_3_03_review_rough_cut.step import (
    check_total_duration,
    run_mechanical_checks,
)
from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion


# --- step_3_03 total duration tests ---

class TestCheckTotalDuration:
    """check_total_duration hard-fails when duration exceeds target by >50%."""

    def test_within_target_passes(self):
        """60s actual vs 60s target = passes."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 60}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is True
        assert result["actual_duration_seconds"] == 60.0

    def test_at_threshold_passes(self):
        """90s actual vs 60s target = exactly 150%, should pass."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 90}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is True

    def test_over_threshold_fails(self):
        """91s actual vs 60s target = >150%, should fail."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 91}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is False
        assert "overshoot_ratio" in result
        assert result["overshoot_ratio"] > 1.5

    def test_audio_spine_takes_precedence(self):
        """When audio_spine has structure, it is used instead of a_rolls."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 30}]
        audio_spine = {
            "structure": [
                {"timeline_start": 0, "timeline_end": 50},
                {"timeline_start": 50, "timeline_end": 100},
            ]
        }
        result = check_total_duration(a_rolls, audio_spine, {})
        assert result["actual_duration_seconds"] == 100.0
        # 100 > 60*1.5=90 -> fail
        assert result["passed"] is False

    def test_project_config_target(self):
        """project_config.target_duration_seconds sets the target."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 200}]
        data = {
            "project_config": {
                "target_duration_seconds": 300
            }
        }
        # 200 <= 300*1.5=450, passes
        result = check_total_duration(a_rolls, {}, data)
        assert result["passed"] is True
        assert result["target_duration_seconds"] == 300.0

    def test_massive_overshoot_fails(self):
        """8.6 min (516s) vs 60s target - the original bug scenario."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 516}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is False
        assert result["actual_duration_seconds"] == 516.0
        assert result["overshoot_ratio"] == 8.6

    def test_empty_inputs(self):
        """Empty inputs should pass (0s duration)."""
        result = check_total_duration([], {}, {})
        assert result["passed"] is True
        assert result["actual_duration_seconds"] == 0.0


class TestRunMechanicalChecksWithDuration:
    """run_mechanical_checks integrates total_duration check."""

    def test_duration_check_included(self):
        data = {
            "a_roll_assignments": [{"timeline_start": 0, "timeline_end": 50}],
            "b_roll_assignments": [],
            "audio_spine": {},
            "project_folder": ".",
        }
        result = run_mechanical_checks(data)
        assert "total_duration" in result["mechanical_checks"]
        assert result["mechanical_checks"]["total_duration"]["passed"] is True

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
        }
        result = run_mechanical_checks(data)
        assert result["passed"] is False
        assert result["mechanical_checks"]["total_duration"]["passed"] is False
        assert any("Total duration" in r for r in result["rejection_reasons"])


# --- step_5_03 duration warning tests ---

class TestCohesionDurationWarning:
    """creative_cohesion warns (does not fail) when outside project target."""

    def test_duration_over_target_warns(self):
        inputs = {
            "creative_direction": {"target_energy": "moderate"},
            "transition_spec": {"transitions": []},
            "sfx_spec": [],
            "speech_sequence": {
                "body_sequence": [{"start_time": 0, "end_time": 120}]
            },
            "color_grade_spec": {},
            "project_config": {
                "target_duration_seconds": 60
            },
        }
        review = review_creative_cohesion(inputs)
        assert any("exceeds target" in w for w in review["warnings"])

    def test_duration_under_target_warns(self):
        inputs = {
            "creative_direction": {"target_energy": "moderate"},
            "transition_spec": {"transitions": []},
            "sfx_spec": [],
            "speech_sequence": {
                "body_sequence": [{"start_time": 0, "end_time": 20}]
            },
            "color_grade_spec": {},
            "project_config": {
                "target_duration_seconds": 60
            },
        }
        review = review_creative_cohesion(inputs)
        assert any("less than half" in w for w in review["warnings"])

    def test_duration_within_range_no_warning(self):
        inputs = {
            "creative_direction": {"target_energy": "moderate"},
            "transition_spec": {"transitions": []},
            "sfx_spec": [],
            "speech_sequence": {
                "body_sequence": [{"start_time": 0, "end_time": 45}]
            },
            "color_grade_spec": {},
            "project_config": {
                "target_duration_seconds": 60
            },
        }
        review = review_creative_cohesion(inputs)
        assert not any("Duration warning" in w for w in review["warnings"])

    def test_no_project_config_no_warning(self):
        """Without project_config, no duration warning is produced."""
        inputs = {
            "creative_direction": {"target_energy": "moderate"},
            "transition_spec": {"transitions": []},
            "sfx_spec": [],
            "speech_sequence": {
                "body_sequence": [{"start_time": 0, "end_time": 120}]
            },
            "color_grade_spec": {},
        }
        review = review_creative_cohesion(inputs)
        assert not any("Duration warning" in w for w in review["warnings"])
