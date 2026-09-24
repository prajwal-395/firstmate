"""Seventeen copies of an error message are not seventeen measurements.

Project 001's `prosody_analysis.profiles` held seventeen records that each
said only `{"method": null, "error": "parselmouth not installed"}`, and all
seventeen were serialised into the creative-direction prompt as if they were
data.  One arm of the A/B on that context said, unprompted, "There is no
actual prosody data to evaluate... I had to completely ignore this section."

A path allow-list cannot fix that: `context_fields` selects by NAME and
these records have the right names.  `view:prosody` selects by
`profile_defect` - the same predicate step 1.05 refuses to write a hollow
profile with - and reports the absence in one line instead of hiding it.

Step 1.05 is UNWIRED as of #F5 and nothing declares `prosody_analysis` any
more (docs/PROSODY_MEASURED.md).  The view and its predicate are untouched,
so what is tested here is the view's own contract rather than one step's
manifest - and `test_no_step_declares_prosody_any_more` holds the other end.
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.context_projector import project_fields
from library.tools.context_views import CONTEXT_VIEWS, build_view
from library.tools.prosody_profile import profile_defect
from library.tools.toon_serializer import json_to_toon

STEP = REPO / "library" / "steps" / "step_2_01_creative_direction"

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


def manifest():
    return json.loads((STEP / "manifest.json").read_text(encoding="utf-8"))


# ── The absence is stated once, not seventeen times ───────────────────

def test_seventeen_error_records_become_one_line():
    view = build_view("prosody", {"prosody_analysis": {"profiles": HOLLOW}})
    context = json_to_toon(view)

    assert context.count("parselmouth not installed") == 1, context
    assert "17 of 17" in context
    assert "measured" not in view["prosody"]


def test_a_real_measurement_still_reaches_the_prompt():
    """A saving bought by blinding the step is not a saving."""
    view = build_view(
        "prosody", {"prosody_analysis": {"profiles": {**HOLLOW, **REAL}}})
    assert view["prosody"]["clips_measured"] == 1
    assert view["prosody"]["measured"]["clip_011"]["prosody"][
        "pitch_stats"]["mean_f0_hz"] == 118.4
    assert "118.4" in json_to_toon(view)
    # ...and the sixteen that measured nothing are still declared.
    assert "16 of 17" in view["prosody"]["not_measured"]


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


# ── Nothing consumes it, and the view is intact for whatever does ────
#
# Step 1.05 is UNWIRED since #F5 and 2.01 declares neither the input nor
# the view (docs/PROSODY_MEASURED.md).  The view itself is untouched, so
# what these assert is the view's own contract - the shape any future
# consumer would declare - rather than one step's manifest.

CONSUMER_FIELDS = [
    "view:prosody",
    "clip_catalog.*.filename",
    "clip_catalog.*.duration_seconds",
]


def test_prosody_is_declared_by_its_wired_consumers():
    """Prosody was re-wired on 2026-09-01: its deterministic measurements
    are unbiased signal the model lacks. Steps 2.01 and 2.02 declare it
    as an optional input and project it via view:prosody; rung 5b routes
    it to every anchor consumer (4.02, 4.03, 4.04), projected as
    view:emphasis."""
    expected_consumers = {
        "step_2_01_creative_direction",
        "step_2_02_speech_sequence",
        "step_4_02_plan_transitions",
        "step_4_03_plan_vfx",
        "step_4_04_plan_sfx",
    }
    actual = set()
    for path in sorted((REPO / "library" / "steps").glob("*/manifest.json")):
        if path.parent.name == "step_1_05_prosody_analysis":
            continue
        m = json.loads(path.read_text())
        names = [i.get("name") for i in
                 (m.get("interface") or {}).get("inputs") or []]
        if "prosody_analysis" in names:
            actual.add(path.parent.name)
    assert actual == expected_consumers, (
        f"Expected {expected_consumers}, got {actual}")


def test_a_step_with_no_prosody_routed_gets_nothing_rather_than_an_error():
    assert build_view("prosody", {"clip_catalog": []}) == {}
    assert build_view("prosody", {"prosody_analysis": {}}) == {}


