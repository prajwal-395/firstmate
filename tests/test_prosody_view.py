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


def test_the_absence_is_reported_rather_than_hidden():
    """A model told plainly that nothing was measured knows not to reason
    about it. Silence would read as "no prosody worth mentioning"."""
    view = build_view("prosody", {"prosody_analysis": {"profiles": HOLLOW}})
    assert "no prosody measurement" in view["prosody"]["not_measured"]


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


def test_the_view_and_the_step_agree_on_what_a_measurement_is():
    """One predicate, so the write-time and read-time answers cannot differ."""
    from library.steps.step_1_05_prosody_analysis.step import (
        profile_defect as step_side,
    )
    assert step_side is profile_defect
    assert profile_defect(REAL["clip_011"]) == ""
    assert profile_defect(HOLLOW["clip_001"]) == "parselmouth not installed"


# ── The step still runs ───────────────────────────────────────────────

def test_creative_direction_declares_the_view_and_its_source():
    """A view is not routing. The step still has to be sent the input."""
    m = manifest()
    assert "view:prosody" in m["context_fields"]
    assert not any(f.startswith("prosody_analysis.")
                   for f in m["context_fields"]), (
        "the raw paths are what carried the seventeen error records")
    names = [i["name"] for i in m["interface"]["inputs"]]
    assert "prosody_analysis" in names


def test_projecting_creative_direction_carries_no_error_records():
    inputs = {
        "prosody_analysis": {"profiles": HOLLOW, "total_clips": 17},
        "clip_catalog": [{"filename": "IMG_1816.MOV", "duration_seconds": 70.1}],
    }
    context = json_to_toon(project_fields(inputs, manifest()["context_fields"]))
    assert context.count("parselmouth not installed") == 1
    assert "IMG_1816.MOV" in context, "the rest of the projection is unchanged"


def test_a_step_with_no_prosody_routed_gets_nothing_rather_than_an_error():
    assert build_view("prosody", {"clip_catalog": []}) == {}
    assert build_view("prosody", {"prosody_analysis": {}}) == {}


@pytest.mark.parametrize("name", sorted(CONTEXT_VIEWS))
def test_every_view_survives_a_second_projection(name):
    """`llm_only` steps are projected twice on every run."""
    fields = manifest()["context_fields"]
    once = project_fields(
        {"prosody_analysis": {"profiles": {**HOLLOW, **REAL}}}, fields)
    twice = project_fields(once, fields)
    assert twice.get(name) == once.get(name)
