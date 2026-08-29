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


def test_the_readers_of_the_vocabulary_are_the_registered_ones():
    """A new bucketer has to be registered, and then agree.

    The point of this module is that two readers of `target_energy` once
    disagreed about "building" and only one of them acted.  With one
    reader left, "they agree" is not a statement that can fail - what can
    is "these are the readers", so a second one arriving fails here and
    has to be added to PHRASE_VERDICTS below.
    """
    assert _modules_importing_the_vocabulary() == KNOWN_READERS, (
        "a module reads library/tools/energy_reading.py and is not "
        "registered in KNOWN_READERS. Add it, and add it to "
        "PHRASE_VERDICTS so its bucketing is compared against the others.")


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


@pytest.mark.parametrize("phrase", list(PHRASE_VERDICTS))
def test_every_reader_gives_the_stated_verdict(phrase):
    expected = PHRASE_VERDICTS[phrase]
    for name, read in EVERY_READER.items():
        assert read(phrase) == expected, (
            f"{name} buckets {phrase!r} as {read(phrase)!r}, and this "
            f"module states {expected!r}")


def test_building_is_not_high():
    """The word the 001 direction chose OVER "high"."""
    assert read_energy("building") == "moderate"
    assert not _is_high_energy({"target_energy": "building"})


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


def test_no_module_carries_its_own_word_list():
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


def test_an_explicit_high_does_not_shorten_them_either():
    """The threshold itself is gone, not just the reading of "building".

    "high energy means every transition under 500 ms, so make it 10
    frames" is a creative value step 5.03 chose, and `duration_frames` is
    the ONE field `compile_manifest` rewrites - so the invented number
    reached the picture.  A pace check here needs a pace the creative
    direction DECLARED, which no step emits (AGENTS.md 10.5).
    """
    inputs = _building_inputs()
    inputs["creative_direction"]["target_energy"] = "high"
    review = review_creative_cohesion(inputs)
    assert review["adjustments"] == []
    assert not any("High energy" in w for w in review["warnings"])


def test_the_transitions_are_counted_and_not_judged():
    """What replaced the four thresholds: a number, and no verdict."""
    inputs = _building_inputs()
    inputs["creative_direction"]["target_energy"] = "high"
    review = review_creative_cohesion(inputs)
    m = review["measurements"]
    assert m["transitions_planned"] == 3
    assert m["transitions_drawn"] == 3
    assert m["sfx_events"] == 0
    assert m["declared_target_energy"] == "high"
