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
    """check_total_duration hard-fails when duration is outside target zone."""

    def test_within_target_passes(self):
        """60s actual vs 60s target = passes."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 60}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is True
        assert result["actual_duration_seconds"] == 60.0

    def test_at_threshold_passes(self):
        """66s actual vs 60s target (max is 66), should pass."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 66}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is True

    def test_over_threshold_fails(self):
        """67s actual vs 60s target (max is 66), should fail."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 67}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is False

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
        # 100 > 66 -> fail
        assert result["passed"] is False

    def test_project_config_target(self):
        """project_config.target_duration_seconds sets the target."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 315}]
        data = {
            "project_config": {
                "target_duration_seconds": 300
            }
        }
        # zone is [270, 330], 315 passes
        result = check_total_duration(a_rolls, {}, data)
        assert result["passed"] is True
        assert result["target_duration_seconds"] == 300.0

    def test_massive_overshoot_fails(self):
        """8.6 min (516s) vs 60s target - the original bug scenario."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 516}]
        result = check_total_duration(a_rolls, {}, {})
        assert result["passed"] is False
        assert result["actual_duration_seconds"] == 516.0

    def test_empty_inputs(self):
        """Empty inputs means 0s duration, which is below min 54s, so fails."""
        result = check_total_duration([], {}, {})
        assert result["passed"] is False
        assert result["actual_duration_seconds"] == 0.0


class TestRunMechanicalChecksWithDuration:
    """run_mechanical_checks integrates total_duration check."""

    def test_duration_check_included(self):
        data = {
            "a_roll_assignments": [{"timeline_start": 0, "timeline_end": 60}],
            "b_roll_assignments": [],
            "audio_spine": {},
            "project_folder": ".",
        }
        result = run_mechanical_checks(data)
        assert "total_duration" in result["mechanical_checks"]
        assert result["mechanical_checks"]["total_duration"]["passed"] is True

    def test_b_roll_checks_run_on_real_assignments(self):
        """The B-roll checks need B-roll to check."""
        data = {
            "a_roll_assignments": [
                {"timeline_start": 0, "timeline_end": 60,
                 "spine_block_position": 1, "block_type": "speech",
                 "source_file": __file__,
                 "video_segments": [{"video_in": 1.204, "video_out": 61.204}]}
            ],
            "b_roll_assignments": [
                {"spine_block_position": 1, "clip_id": "clip_2",
                 "source_file": __file__,
                 "video_in": 1.204, "video_out": 3.086,
                 "duration_seconds": 1.882,
                 "timeline_start": 0.0, "timeline_end": 1.882},
            ],
            "b_roll_interjections": [],
            "audio_spine": {},
            "project_folder": ".",
        }
        result = run_mechanical_checks(data)
        checks = result["mechanical_checks"]
        assert checks["b_roll_duration_invariant"]["passed"] is True
        assert checks["source_files_exist"]["passed"] is True

    def test_b_roll_duration_mismatch_is_rejected(self):
        """A B-roll clip whose source range does not fill its slot."""
        data = {
            "a_roll_assignments": [
                {"timeline_start": 0, "timeline_end": 60,
                 "spine_block_position": 1, "block_type": "speech",
                 "source_file": __file__,
                 "video_segments": [{"video_in": 0.0, "video_out": 60.0}]}
            ],
            "b_roll_assignments": [
                {"spine_block_position": 1, "clip_id": "clip_2",
                 "source_file": __file__,
                 "video_in": 0.0, "video_out": 0.0,
                 "duration_seconds": 0.0,
                 "timeline_start": 0.0, "timeline_end": 1.882},
            ],
            "b_roll_interjections": [],
            "audio_spine": {},
            "project_folder": ".",
        }
        result = run_mechanical_checks(data)
        assert result["mechanical_checks"]["b_roll_duration_invariant"]["passed"] is False

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
    """creative_cohesion warns (does not fail) when outside target zone."""

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
        assert any("exceeds the maximum" in w for w in review["warnings"])

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
        assert any("is below the minimum" in w for w in review["warnings"])

    def test_duration_within_range_no_warning(self):
        inputs = {
            "creative_direction": {"target_energy": "moderate"},
            "transition_spec": {"transitions": []},
            "sfx_spec": [],
            "speech_sequence": {
                "body_sequence": [{"start_time": 0, "end_time": 60}]
            },
            "color_grade_spec": {},
            "project_config": {
                "target_duration_seconds": 60
            },
        }
        review = review_creative_cohesion(inputs)
        assert not any("Duration warning" in w for w in review["warnings"])

    def test_no_project_config_warns_with_default(self):
        """Without project_config, it falls back to 54-66s default and warns if out of bounds."""
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
        assert any("Duration warning" in w for w in review["warnings"])
