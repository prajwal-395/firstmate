"""One reading of `target_energy`, and "building" is not "high".

"building" names a TRAJECTORY, not a level: it reads as "moderate", and
it no longer makes 5.03 shorten transitions (its energy thresholds were
creative values and are gone). Incident on project 001 and the
reconciliation: `docs/evidence/energy_reading.md`.
"""
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)
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
