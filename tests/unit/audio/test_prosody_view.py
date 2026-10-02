"""Seventeen copies of an error message are not seventeen measurements.

`view:prosody` selects by `profile_defect` (the predicate step 1.05
refuses a hollow profile with), states the absence once, and still
carries every real measurement. History: docs/evidence/prosody.md.
"""
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon


HOLLOW = {
    f"clip_{i:03d}": {
        "clip_id": f"clip_{i:03d}",
        "audio_file": f"/nowhere/IMG_18{i:02d}.MOV",
        "prosody": {"method": None, "error": "parselmouth not installed"},
        "analysis_time_s": 0.0,
    }
    for i in range(1, 18)
}
REAL = {
    "clip_011": {
        "clip_id": "clip_011",
        "prosody": {"method": "praat",
                    "pitch_stats": {"mean_f0_hz": 118.4, "range_hz": 96.2}},
    }
}


# ── The absence is stated once, not seventeen times ───────────────────

def test_the_absence_is_stated_once_and_real_measurements_still_reach():
    view = build_view("prosody", {"prosody_analysis": {"profiles": HOLLOW}})
    context = json_to_toon(view)
    assert context.count("parselmouth not installed") == 1, context
    assert "17 of 17" in context
    assert "measured" not in view["prosody"]

    # A saving bought by blinding the step is not a saving.
    view = build_view(
        "prosody", {"prosody_analysis": {"profiles": {**HOLLOW, **REAL}}})
    assert view["prosody"]["clips_measured"] == 1
    assert view["prosody"]["measured"]["clip_011"]["prosody"][
        "pitch_stats"]["mean_f0_hz"] == 118.4
    assert "118.4" in json_to_toon(view)
    assert "16 of 17" in view["prosody"]["not_measured"]

    # A step with no prosody routed gets nothing rather than an error.
    assert build_view("prosody", {"clip_catalog": []}) == {}
    assert build_view("prosody", {"prosody_analysis": {}}) == {}


def test_contours_do_not_reach_the_prompt():
    """AGENTS.md 10.1: No raw value list reaches a prompt."""
    data = {
        "clip_001": {
            "clip_id": "clip_001",
            "prosody": {
                "method": "praat",
                "pitch_stats": {"mean_f0_hz": 118.4},
                "pitch_contour_10ms": [{"time": 0.01, "pitch": 100}],
                "intensity_contour_50ms": [{"time": 0.05, "intensity": 60}],
            }
        }
    }
    view = build_view("prosody", {"prosody_analysis": {"profiles": data}})
    assert view["prosody"]["clips_measured"] == 1
    assert "pitch_stats" in view["prosody"]["measured"]["clip_001"]["prosody"]
    assert "pitch_contour_10ms" not in view["prosody"]["measured"]["clip_001"]["prosody"]
    assert "intensity_contour_50ms" not in view["prosody"]["measured"]["clip_001"]["prosody"]
