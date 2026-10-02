"""Tests for duration enforcement in review_rough_cut and creative_cohesion.

The duration zone is the PROJECT's declaration; with nothing declared a gate
reports that it did not check rather than judging an invented length.
History: `docs/evidence/duration_enforcement.md`.
"""
from library.steps.step_3_03_review_rough_cut.step import (
    check_total_duration,
    run_mechanical_checks,
)
from library.steps.step_5_03_creative_cohesion.step import review_creative_cohesion
import sys
from pathlib import Path


# 001's own project.yaml declaration: 60 seconds, so the zone is 54-66.
DECLARES_60 = {"project_config": {"target_duration_seconds": 60}}


# --- step_3_03 total duration tests ---

class TestCheckTotalDuration:
    """check_total_duration hard-fails when duration is outside target zone."""

    def test_the_declared_zone_passes_60_and_fails_67(self):
        """60s target: the zone is 54-66s."""
        a_rolls = [{"timeline_start": 0, "timeline_end": 60}]
        result = check_total_duration(a_rolls, {}, DECLARES_60)
        assert result["passed"] is True
        assert result["actual_duration_seconds"] == 60.0
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


# --------------------------------------------------------------------------
# From test_pacing_report.py
#
# Rung 7 (PA3.2/PA2.2): the plan's ASL windows beside delivered ASL.
#
# PA3.2 - "average shot length around 2.5s in the opening 20s, then let
# it relax to ~5s" - had no plan spelling and no measurement. Step 2.05
# validates the windows; `library/tools/pacing.py` measures the built
# picture against them into `compile_manifest`'s `pacing_report`,
# report-only. Abutting clips share their edge frame, so one straight
# cut is one cut, not two. A window naming a block the built spine does
# not carry reports the miss instead of refusing - the picture is built
# and the report is what says the target missed its address.

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.pacing import measure_pacing

FPS = 30.0


def _block(position, start_f, end_f):
    return {"position": position, "block_type": "speech",
            "timeline_start_frame": start_f,
            "timeline_end_frame": end_f,
            "timeline_start": start_f / FPS,
            "timeline_end": end_f / FPS}


def _clip(label, start_f, end_f):
    return {"label": label, "timeline_in_frame": start_f,
            "timeline_out_frame": end_f,
            "timeline_in": start_f / FPS, "timeline_out": end_f / FPS}


def test_delivered_asl_counts_each_cut_once_beside_either_target():
    """20 s, one mid cut shared by two abutting clips: 2 shots, ASL 10."""
    report = measure_pacing(
        [_block(1, 0, 300), _block(2, 300, 600)],
        [_clip("a", 0, 300), _clip("b", 300, 600)], [],
        [{"start_block": 1, "end_block": 2, "asl_seconds": 2.5}],
        FPS)
    (row,) = report
    assert row["cuts"] == 1
    assert row["window_seconds"] == 20.0
    assert row["delivered_asl_seconds"] == 10.0
    assert row["target_asl_seconds"] == 2.5

    # PA2.2's feel survives compile without being made numeric.
    report = measure_pacing(
        [_block(1, 0, 600)], [_clip("a", 0, 300), _clip("b", 300, 600)],
        [], [{"start_block": 1, "end_block": 1,
              "feel": "faster cuts as it builds"}], FPS)
    (row,) = report
    assert row["target_asl_seconds"] is None
    assert row["target_feel"] == "faster cuts as it builds"
    assert row["delivered_asl_seconds"] == 10.0


def test_b_roll_edges_are_shots_too():
    """A V2 cutaway over speech changes the visible shot: it cuts."""
    report = measure_pacing(
        [_block(1, 0, 600)],
        [_clip("a", 0, 600)],
        [_clip("v", 150, 450)],
        [{"start_block": 1, "end_block": 1, "asl_seconds": 5.0}],
        FPS)
    (row,) = report
    # Edges at 150 and 450: three shots over 20 s.
    assert row["cuts"] == 2
    assert row["delivered_asl_seconds"] == round(20.0 / 3, 3)


# --------------------------------------------------------------------------
# From test_energy_reading.py
#
# One reading of `target_energy`, and "building" is not "high".
#
# "building" names a TRAJECTORY, not a level: it reads as "moderate", and
# it no longer makes 5.03 shorten transitions (its energy thresholds were
# creative values and are gone). Incident on project 001 and the
# reconciliation: `docs/evidence/energy_reading.md`.

if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.energy_reading import read_energy
from library.tools.transition_selector import _is_high_energy

def test_building_is_not_high():
    """The word the 001 direction chose OVER "high"."""
    assert read_energy("building") == "moderate"
    assert not _is_high_energy({"target_energy": "building"})


# ── The concrete regression: what 001's "building" made 5.03 do ───────

def _building_inputs():
    return {
        "creative_direction": {"target_energy": "building"},
        "transition_spec": [
            {"transition_type": "defocus", "duration_frames": 15},
            {"transition_type": "defocus", "duration_frames": 15},
            {"transition_type": "defocus", "duration_frames": 15},
        ],
        "sfx_spec": [],
        "color_grade_spec": {},
        "audio_spine": {"structure": [
            {"block_type": "speech", "position": 0,
             "timeline_start": 0.0, "timeline_end": 54.77},
        ]},
    }


def test_building_no_longer_shortens_the_defocus_transitions():
    review = review_creative_cohesion(_building_inputs())
    shortened = [a for a in review["adjustments"]
                 if a.get("field") == "duration_frames"]
    assert shortened == [], (
        "a deliberate 'building' still cuts 500ms defocus transitions to "
        "333ms")
    assert not any("High energy" in w for w in review["warnings"])
