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
    map_energy,
    review_creative_cohesion,
)
from library.tools import energy_reading
from library.tools.energy_reading import read_energy
from library.tools.transition_selector import _is_high_energy

EVERY_READER = {
    "transition_selector": lambda e: (
        "high" if _is_high_energy({"target_energy": e}) else "not-high"),
    "creative_cohesion": lambda e: (
        "high" if map_energy(e) == "high" else "not-high"),
}

PHRASES = [
    "building",
    "high",
    "calm",
    "moderate",
    "high energy, fast cuts",
    "start observational -> build to a peak",
    "low and reflective",
    "",
    None,
]


@pytest.mark.parametrize("phrase", PHRASES)
def test_every_reader_agrees(phrase):
    verdicts = {name: read(phrase) for name, read in EVERY_READER.items()}
    assert len(set(verdicts.values())) == 1, (
        f"the readers of target_energy disagree on {phrase!r}: {verdicts}")


def test_building_is_not_high():
    """The word the 001 direction chose OVER "high"."""
    assert read_energy("building") == "moderate"
    assert not _is_high_energy({"target_energy": "building"})
    assert map_energy("building") == "moderate"


def test_high_is_still_high():
    assert read_energy("high") == "high"
    assert _is_high_energy({"target_energy": "high"})


def test_calm_is_still_calm():
    assert read_energy("low and reflective") == "calm"


def test_high_wins_a_mixed_phrase():
    assert read_energy("calm but building to something intense") == "high"


def test_the_withdrawn_words_are_recorded_with_reasons():
    """Widening the high bucket decides which transitions get drawn on
    every project using the word, so it must be a decision, not drift."""
    assert "building" in energy_reading.WITHDRAWN_HIGH_WORDS
    for word, reason in energy_reading.WITHDRAWN_HIGH_WORDS.items():
        assert word not in energy_reading.HIGH_ENERGY_WORDS
        assert len(reason) > 20, f"{word} is withdrawn with no reason"


def test_neither_reader_carries_its_own_word_list():
    """The defect was two lists, not two opinions.

    Read with the AST rather than by grepping, so the modules can keep
    explaining themselves in prose while still owning no vocabulary.
    """
    import ast

    for module_path in (
        REPO / "library" / "tools" / "transition_selector.py",
        REPO / "library" / "steps" / "step_5_03_creative_cohesion" / "step.py",
    ):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple, ast.Set)):
                continue
            words = {e.value.lower() for e in node.elts
                     if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            # Only the withdrawn words. The colour-mood check next door
            # legitimately owns a list containing "calm", and that is a
            # different vocabulary about a different thing.
            clash = words & set(energy_reading.WITHDRAWN_HIGH_WORDS)
            assert not clash, (
                f"{module_path.name} carries its own energy word list "
                f"({sorted(clash)}) - the vocabulary belongs in "
                f"library/tools/energy_reading.py")


# ── The concrete regression: what 001's "building" made 5.03 do ───────

def _building_inputs():
    return {
        "creative_direction": {"target_energy": "building"},
        "transition_spec": {"transitions": [
            {"transition_type": "defocus", "duration_frames": 15},
            {"transition_type": "defocus", "duration_frames": 15},
            {"transition_type": "defocus", "duration_frames": 15},
        ]},
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


def test_an_explicit_high_still_shortens_them():
    """The capability is untouched - only the reading of 'building' is."""
    inputs = _building_inputs()
    inputs["creative_direction"]["target_energy"] = "high"
    review = review_creative_cohesion(inputs)
    shortened = [a for a in review["adjustments"]
                 if a.get("field") == "duration_frames"]
    assert len(shortened) == 3
    assert all(a["suggested_value"] == 10 for a in shortened)
