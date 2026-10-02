"""Optional edges: the requirement layer can say what may or may not travel.

Optionality is its own requirement kind (captain's ruling 2026-09-23,
"add-optional"), never a loosened requirement. Both mechanisms are pinned
by name: A, the edge is marked NOT REQUIRED (`creative_cohesion`,
`prosody_analysis`, `color_grade`, `ocr_extraction`); B, the producer
declares the key for a DIFFERENT consumer (`plan_transitions`, `plan_sfx`,
`plan_vfx`). A repair covering only A leaves three nodes broken.
"""

import os
import sys


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from library.tools import composer as C
from library.tools import operations as O

def test_every_optional_edge_composes_to_its_producer():
    """Each optional goal plans the capability that produces it - which
    also means none of the producers derives an empty effect (the
    composer works backwards through `effect`). Mechanism A, mechanism B,
    and the seventh node, `cohesion.review`, whose goal closes once the
    capability is registered."""
    rows = {
        "optional.creative_direction.prosody_analysis": "prosody.analyse",
        "optional.speech_sequence.prosody_analysis": "prosody.analyse",
        "optional.compile_manifest.color_grade_spec": "color_grade.resolve",
        "optional.ocr_extraction.ocr_extraction": "ocr.extract",
        "optional.compile_manifest.transition_spec": "transitions.resolve",
        "optional.compile_manifest.sfx_spec": "sfx.resolve",
        "optional.compile_manifest.enhancement_spec": "vfx.resolve",
    }
    for goal, producer in rows.items():
        comp = C.compose(goal)
        assert producer in comp.operations, (goal, comp.operations)
        assert O.get(producer).effect, producer
    assert not set(rows.values()) & set(O.EMPTY_EFFECT_REASONS)

    comp = C.compose("optional.compile_manifest.cohesion_review")
    assert comp.completed
    assert comp.operations[-1] == "cohesion.review"
