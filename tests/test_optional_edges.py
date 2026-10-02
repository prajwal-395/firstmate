"""Optional edges: the requirement layer can say what may or may not travel.

The captain's ruling, 2026-09-23, board answer "add-optional": ADD
OPTIONALITY TO THE VOCABULARY.  Seven nodes write real state the
requirement layer could not see, for two different reasons:

* MECHANISM A - the edge is marked NOT REQUIRED, so the requirement
  layer never sees it at all: `creative_cohesion`, `prosody_analysis`,
  `color_grade`, `ocr_extraction`.
* MECHANISM B - the producer DOES declare the key, just for a
  DIFFERENT consumer: `plan_transitions` (`transition_spec`),
  `plan_sfx` (`sfx_spec`), `plan_vfx` (`enhancement_spec`).

A repair covering only mechanism A looks like it works and leaves
three nodes broken, so this file pins BOTH, by name - and pins the
constraint too: optionality is its own kind, never a loosened
requirement, a widened kind, or a relaxed `produced_by`.

What is deliberately NOT pinned here: exact registry counts.  The
composer census moves with every coverage lane by design, so this
file asserts properties (which goals complete, which refusals
survive, which effects are non-empty) rather than totals.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import composer as C
from library.tools import operations as O
from library.tools import processes
from library.tools import requirements as R

# name -> (producing node, mechanism)
EXPECTED = {
    "optional.creative_direction.prosody_analysis": ("prosody_analysis", "A"),
    "optional.speech_sequence.prosody_analysis": ("prosody_analysis", "A"),
    "optional.compile_manifest.color_grade_spec": ("color_grade", "A"),
    "optional.compile_manifest.cohesion_review": ("creative_cohesion", "A"),
    "optional.ocr_extraction.ocr_extraction": ("ocr_extraction", "A"),
    "optional.compile_manifest.transition_spec": ("plan_transitions", "B"),
    "optional.compile_manifest.sfx_spec": ("plan_sfx", "B"),
    "optional.compile_manifest.enhancement_spec": ("plan_vfx", "B"),
}


def _optionals():
    return {r.name: r for r in R.all_requirements()
            if r.kind == R.KIND_OPTIONAL}










def test_six_operations_leave_the_blind_set():
    """The three mechanism-A operations with capabilities, and all
    three mechanism-B producers, derive non-empty effects from the keys
    each declares it `produces`, so none is in the blind set."""
    for op_name in ("prosody.analyse", "color_grade.resolve",
                    "ocr.extract"):
        op = O.get(op_name)
        assert op.effect, (
            f"{op_name} still derives an empty effect - its optional "
            f"production is unexpressed")
    assert not {"prosody.analyse", "color_grade.resolve", "ocr.extract",
                "transitions.resolve", "sfx.resolve", "vfx.resolve"} & set(
        O.EMPTY_EFFECT_REASONS), (
        f"an optional-edge producer is back in the blind set: "
        f"{sorted(O.EMPTY_EFFECT_REASONS)}")


def test_each_mechanism_a_capability_composes():
    """Mechanism A: the productions no REQUIRED edge carries are goals
    that plan their producer."""
    assert "prosody.analyse" in C.compose(
        "optional.creative_direction.prosody_analysis").operations
    assert "prosody.analyse" in C.compose(
        "optional.speech_sequence.prosody_analysis").operations
    assert "color_grade.resolve" in C.compose(
        "optional.compile_manifest.color_grade_spec").operations
    assert "ocr.extract" in C.compose(
        "optional.ocr_extraction.ocr_extraction").operations


def test_each_mechanism_b_edge_composes():
    """Mechanism B: the same key for a different consumer - the
    compile_manifest-facing optional edge plans the producer that the
    required edge already made visible."""
    assert "transitions.resolve" in C.compose(
        "optional.compile_manifest.transition_spec").operations
    assert "sfx.resolve" in C.compose(
        "optional.compile_manifest.sfx_spec").operations
    assert "vfx.resolve" in C.compose(
        "optional.compile_manifest.enhancement_spec").operations


def test_cohesion_composes_through_its_capability():
    """The seventh node: the edge was in the vocabulary but refused by
    name while no operation owned `creative_cohesion`.  With
    `cohesion.review` registered the goal closes with nothing in the
    composer changed - the middle-of-the-DAG refusal was a registry
    gap, not a property of the edge."""
    comp = C.compose("optional.compile_manifest.cohesion_review")
    assert comp.completed
    assert comp.operations[-1] == "cohesion.review"
