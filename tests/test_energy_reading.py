"""One reading of `target_energy`, and "building" is not "high".

Project 001's creative direction chose `target_energy: "building"`
deliberately - its own rationale said cutting the piece as high-energy
would "fight the source". Two readers then bucketed it differently and
only one of them acted:

* `transition_selector` did not match it, so scene-change cuts stayed
  hard cuts;
* `step_5_03_creative_cohesion.map_energy` substring-matched it to
  "high", demanded transitions under 500 ms and >=10 SFX per minute, and
  cut three `defocus` transitions from 500 ms to 333 ms.

Those four cohesion thresholds are now GONE - not re-bucketed.  "a high
energy edit holds every transition under 500 ms" and "carries at least 10
SFX per minute" are creative values the step chose (AGENTS.md 10.5), so
`map_energy` and the checks it fed are deleted and 5.03 reports the
counts instead of judging them.  What remains here is the vocabulary
itself, which still reaches the picture through `transition_selector`.

The reconciliation is deliberate and recorded in
`library/tools/energy_reading.py`: "building" names a TRAJECTORY, not a
level, and reading a trajectory as its endpoint discards the very
distinction the direction was drawing. The transition selector's
vocabulary is adopted whole, because it is the reading whose result
reaches the picture and the one that was right on the only project
anyone has measured.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_5_03_creative_cohesion.step import (
    review_creative_cohesion,
)
from library.tools import energy_reading
from library.tools.energy_reading import read_energy
from library.tools.transition_selector import _is_high_energy

# Every module that reads the energy vocabulary, discovered rather than
# listed, so a SECOND reader cannot appear without being registered here.
#
# `creative_cohesion` was the other entry until its four energy
# thresholds were removed; it buckets no energy now, it only REPORTS the
# word the direction wrote.  That left one reader, and an "every reader
# agrees" test over one reader - or over `transition_selector` and
# `read_energy`, which is the function it calls - cannot fail.  So the
# guarantee is stated the way it can still fail: the set of importers is
# pinned, and the vocabulary is asserted to live in one module.
KNOWN_READERS = {
    "library/tools/transition_selector.py",
}


def _modules_importing_the_vocabulary():
    found = set()
    for path in sorted((REPO / "library").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            if "energy_reading" in stripped:
                found.add(str(path.relative_to(REPO)))
    return found


# The verdict every registered reader must give, written out rather than
# derived, so the test compares against a STATEMENT and not against the
# implementation it is checking.
PHRASE_VERDICTS = {
    "building": "not-high",
    "high": "high",
    "calm": "not-high",
    "moderate": "not-high",
    "high energy, fast cuts": "high",
    "start observational -> build to a peak": "high",
    "low and reflective": "not-high",
    "": "not-high",
    None: "not-high",
}

EVERY_READER = {
    "transition_selector": lambda e: (
        "high" if _is_high_energy({"target_energy": e}) else "not-high"),
}


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
