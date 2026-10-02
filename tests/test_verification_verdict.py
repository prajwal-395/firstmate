"""The build's verification verdict is DERIVED from its QA stations, and a
planned Fusion effect whose label was never placed is detected, not
silently dropped. History of both defects: docs/evidence/build_verification.md.
"""
from types import SimpleNamespace

# The pure helpers live in library/steps/step_6_01_render/, whose
# directory tests/conftest.py owns on sys.path (it is needed for the
# sibling import inside resolve_build_timeline).
from library.steps.step_6_01_render.build_verification import (  # noqa: E402
    derive_verification_verdict,
    detect_unreachable_fusion_effects,
    format_fusion_drop_error,
)


def _report(station: str, passed: bool):
    """A minimal QA report stub with the .passed attribute the function reads."""
    return SimpleNamespace(station=station, passed=passed)


def test_the_verdict_is_derived_from_the_stations():
    """One failing station fails the build (the old hardcoded True passed
    it regardless); no evidence of failure is not a failure."""
    reports = [
        _report("clip_placement", True),
        _report("fusion_comps", False),
    ]
    assert derive_verification_verdict(reports) is False
    assert derive_verification_verdict([]) is True


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


def test_only_an_unplaced_label_is_a_dropped_effect():
    """The pass walks V1 and V2 (`library/tools/execution/fusion_tracks.py`),
    so placed B-roll is a normal hit; what is caught is a label that was
    never placed - and a build that placed nothing drops everything."""
    per_clip = {label: _PMK_LOOK for label in (
        "a_roll_0", "a_roll_1", "broll_1", "broll_4", "broll_8")}
    placed = {
        1: {"a_roll_0", "a_roll_1"},
        2: {"broll_1", "broll_4", "broll_8"},
    }
    assert detect_unreachable_fusion_effects(per_clip, placed) == []

    dropped = detect_unreachable_fusion_effects(
        {"a_roll_0": _PMK_LOOK, "broll_9": _PMK_LOOK},
        {1: {"a_roll_0"}, 2: {"broll_1"}})
    assert len(dropped) == 1
    assert dropped[0]["label"] == "broll_9"
    assert dropped[0]["track"] == "unplaced"
    assert dropped[0]["params"] == _PMK_LOOK
    assert dropped[0]["label"] in dropped[0]["detail"]

    dropped = detect_unreachable_fusion_effects({"a_roll_0": _PMK_LOOK}, {})
    assert [d["label"] for d in dropped] == ["a_roll_0"]


def test_format_error_message_names_clips_and_params():
    """The error message must be actionable: clip labels and parameters."""
    per_clip = {"phantom": {"glow_gain": 0.12, "film_grain": True}}
    dropped = detect_unreachable_fusion_effects(
        per_clip, {1: set(), 2: set()})

    msg = format_fusion_drop_error(dropped)
    assert "phantom" in msg
    assert "glow_gain" in msg
    assert "cannot reach" in msg
