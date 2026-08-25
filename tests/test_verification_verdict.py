"""Tests for the two verification defects fixed in this PR.

Defect 1: verification_passed was hardcoded True and never derived from
QA station outcomes. A failing station printed "Station fusion_comps:
Failed" but the build returned verification_passed = True.

Defect 2: The Fusion subprocess only processes V1 clips. Per-clip effects
planned for B-roll on V2 (e.g. broll_1, broll_4, broll_8) were silently
dropped with no error, warning, or explanation.

Two layers of testing:
  1. BEHAVIORAL tests that call the extracted pure functions with real
     data objects and assert the returned values. These prove the logic
     is correct.
  2. SOURCE-GREP tests (second line) that verify the renderer calls
     those functions and has not regressed to a hardcoded True.
"""
import os
import sys
from types import SimpleNamespace

import pytest

# The pure helpers live in library/steps/step_6_01_render/ which is not
# on sys.path in a normal test run. Add it.
_STEP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "steps", "step_6_01_render",
)
if _STEP_DIR not in sys.path:
    sys.path.insert(0, _STEP_DIR)

from build_verification import (
    derive_verification_verdict,
    detect_unreachable_fusion_effects,
    format_fusion_drop_error,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RENDERER = os.path.join(PROJECT_ROOT, "library", "steps", "step_6_01_render",
                        "resolve_build_timeline.py")


def _source():
    with open(RENDERER, encoding="utf-8") as f:
        return f.read()


def _report(station: str, passed: bool):
    """A minimal QA report stub with the .passed attribute the function reads."""
    return SimpleNamespace(station=station, passed=passed)


# ═══════════════════════════════════════════════════════════════════
# DEFECT 1 - BEHAVIORAL TESTS: derive_verification_verdict
# ═══════════════════════════════════════════════════════════════════

class TestDeriveVerificationVerdict:
    """The function that replaces the hardcoded all_passed = True."""

    def test_two_passing_stations_returns_true(self):
        reports = [_report("clip_placement", True), _report("audio", True)]
        assert derive_verification_verdict(reports) is True

    def test_one_of_two_failing_returns_false(self):
        """This is the core test. On the old hardcoded True, this would
        pass regardless of what the stations said."""
        reports = [
            _report("clip_placement", True),
            _report("fusion_comps", False),
        ]
        assert derive_verification_verdict(reports) is False

    def test_all_stations_failing_returns_false(self):
        reports = [
            _report("clip_placement", False),
            _report("audio", False),
            _report("fusion_comps", False),
        ]
        assert derive_verification_verdict(reports) is False

    def test_no_stations_returns_true(self):
        """No evidence of failure is not a failure."""
        assert derive_verification_verdict([]) is True

    def test_single_passing_station_returns_true(self):
        assert derive_verification_verdict([_report("full_sweep", True)]) is True

    def test_single_failing_station_returns_false(self):
        assert derive_verification_verdict([_report("full_sweep", False)]) is False


# ═══════════════════════════════════════════════════════════════════
# DEFECT 2 - BEHAVIORAL TESTS: detect_unreachable_fusion_effects
# ═══════════════════════════════════════════════════════════════════

# Representative pmk_default Fusion look parameters
_PMK_LOOK = {
    "grade_contrast": 0.10,
    "glow_gain": 0.12,
    "glow_threshold": 0.78,
    "glow_size": 3.5,
    "film_grain": True,
    "film_grain_power": 0.18,
    "film_grain_size": 1.5,
    "vignette": True,
    "vignette_blend": 0.16,
    "vignette_soft": 0.35,
    "vignette_color": [0.0, 0.0, 0.0],
}


class TestDetectUnreachableFusionEffects:
    """The function that makes a dropped Fusion effect impossible to miss.

    V2 used to be unreachable by construction, because the Fusion pass
    read `tracks['V1']` alone, and this class asserted that every B-roll
    label was a permanent drop. That gap is closed - the pass walks both
    tracks now (`library/tools/execution/fusion_tracks.py`) - so a placed
    B-roll label is a normal hit, and what this function still catches is
    the real question: was the label placed at all?
    """

    def test_placed_broll_on_v2_is_not_dropped(self):
        """broll_1, broll_4 and broll_8 carry the merged look and are on
        V2, which the Fusion pass now reaches."""
        per_clip = {
            "a_roll_0": _PMK_LOOK,
            "a_roll_1": _PMK_LOOK,
            "broll_1": _PMK_LOOK,
            "broll_4": _PMK_LOOK,
            "broll_8": _PMK_LOOK,
        }
        placed = {
            1: {"a_roll_0", "a_roll_1"},
            2: {"broll_1", "broll_4", "broll_8"},
        }

        assert detect_unreachable_fusion_effects(per_clip, placed) == []

    def test_a_broll_label_that_was_never_placed_is_detected(self):
        """The gate must still be able to fire."""
        per_clip = {"a_roll_0": _PMK_LOOK, "broll_9": _PMK_LOOK}
        placed = {1: {"a_roll_0"}, 2: {"broll_1"}}

        dropped = detect_unreachable_fusion_effects(per_clip, placed)
        assert len(dropped) == 1
        assert dropped[0]["label"] == "broll_9"
        assert dropped[0]["track"] == "unplaced"
        assert dropped[0]["params"] == _PMK_LOOK
        assert dropped[0]["label"] in dropped[0]["detail"]

    def test_all_placed_returns_empty(self):
        per_clip = {"a_roll_0": _PMK_LOOK, "a_roll_1": _PMK_LOOK}
        placed = {1: {"a_roll_0", "a_roll_1"}, 2: set()}

        assert detect_unreachable_fusion_effects(per_clip, placed) == []

    def test_empty_per_clip_returns_empty(self):
        assert detect_unreachable_fusion_effects({}, {1: {"a_roll_0"}}) == []

    def test_no_tracks_at_all_drops_everything(self):
        """A build that placed nothing must not read as a clean run."""
        dropped = detect_unreachable_fusion_effects(
            {"a_roll_0": _PMK_LOOK}, {})
        assert [d["label"] for d in dropped] == ["a_roll_0"]

    def test_format_error_message_names_clips_and_params(self):
        """The error message must be actionable: clip labels and parameters."""
        per_clip = {"phantom": {"glow_gain": 0.12, "film_grain": True}}
        dropped = detect_unreachable_fusion_effects(
            per_clip, {1: set(), 2: set()})

        msg = format_fusion_drop_error(dropped)
        assert "phantom" in msg
        assert "glow_gain" in msg
        assert "cannot reach" in msg


# ═══════════════════════════════════════════════════════════════════
# SOURCE-GREP TESTS (second line of defense)
# ═══════════════════════════════════════════════════════════════════

class TestRendererCallsExtractedFunctions:
    """The renderer must call the pure helpers, not inline the logic."""

    def test_no_hardcoded_all_passed_true(self):
        src = _source()
        lines = src.splitlines()
        hardcoded = [
            (i + 1, l) for i, l in enumerate(lines)
            if l.strip() == "all_passed = True"
        ]
        assert not hardcoded, (
            f"all_passed = True is still hardcoded at line(s) "
            f"{[n for n, _ in hardcoded]}"
        )

    def test_renderer_calls_derive_verification_verdict(self):
        src = _source()
        assert "derive_verification_verdict(qa_reports)" in src

    def test_renderer_calls_detect_unreachable_fusion_effects(self):
        src = _source()
        assert "detect_unreachable_fusion_effects(" in src

    def test_renderer_calls_format_fusion_drop_error(self):
        src = _source()
        assert "format_fusion_drop_error(dropped)" in src

    def test_success_is_independent_of_verification(self):
        src = _source()
        line = next(l for l in src.splitlines() if 'results["success"] =' in l)
        assert "verification_passed" not in line
        assert "qa_reports" not in line
