"""Every transition type advertised anywhere must be one the renderer draws.

This is the test the audit asked for: a vocabulary was advertised at one
end and implemented at the other, with nothing in between comparing them.
The handoff taught nine types, the brand templates allowed three, the
selector honoured two and the renderer could draw four - and no two of
those sets shared a token, so not one transition of any kind reached the
finished video.
"""
import pathlib

import pytest

from library.tools.fusion.effects import DRAWABLE_TRANSITIONS, fx
from library.tools.transition_vocabulary import (
    CUT_TYPES,
    FUSION_TYPES,
    PLANNABLE_TYPES,
    WITHDRAWN,
    canonical_type,
    is_cut,
    is_drawn,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
HANDOFF = REPO / "library/steps/step_4_02_plan_transitions/handoff.md"
CLIP_DUR = 90
#: The source frame every builder below sizes its canvas at. The
#: builders take no default frame, so each call states it - the same
#: numbers the removed defaults carried.
RES = (1080, 1920)


# ── The vocabulary agrees with the renderer ──

def test_fusion_types_are_exactly_what_the_engine_draws():
    assert set(FUSION_TYPES) == set(DRAWABLE_TRANSITIONS)


def test_an_undrawable_type_raises_instead_of_drawing_nothing():
    """The silent empty block is the whole defect: `dissolve` reached the
    renderer, matched no branch, and produced a comp with nothing in it."""
    for ttype in sorted(WITHDRAWN) + ["utter_nonsense"]:
        for build in (fx.transition_tail, fx.transition_head):
            with pytest.raises(ValueError,
                               match="No Fusion transition builder"):
                build(CLIP_DUR, ttype, 10, res=RES)


def test_cut_types_are_not_drawn():
    for ttype in CUT_TYPES:
        assert is_cut(ttype)
        assert not is_drawn(ttype)
        with pytest.raises(ValueError):
            fx.transition_tail(CLIP_DUR, ttype, 10, res=RES)


# ── Nothing advertises what the renderer cannot honour ──

def test_no_template_permits_the_full_vocabulary_without_declaring_it():
    """A project that names no brand template declares no allow-list.

    The list is EMPTY rather than the seven types written out, because an
    allow-list is a permission and an absent permission must not be
    indistinguishable from a template that really listed everything. What
    matters is what reaches the picture, so this asserts the SELECTOR's
    reading of that emptiness, not the stored value.
    """
    from library.tools.brand_registry import (
        no_brand_template, resolve_project_template)
    from library.tools.transition_selector import select_transition

    assert resolve_project_template("").effect.transition_types == []
    assert no_brand_template().effect.transition_types == []

    brand_effect = {"transition_types": []}
    for ttype in PLANNABLE_TYPES:
        res = select_transition({"clip_id": "a"}, {"clip_id": "b"},
                                brand_effect, {}, requested_type=ttype)
        assert res["type"] == ttype, (
            f"{ttype} is drawable and no brand forbade it, but an absent "
            f"allow-list refused it")


# ── The vocabulary itself is coherent ──


def test_canonicalisation_is_case_and_space_insensitive():
    assert canonical_type("  Fade_To_Black ") == "fade_to_black"
    assert canonical_type("CUT") == "hard_cut"
    # An absent request is not a request.
    assert canonical_type("") is None
    assert canonical_type(None) is None
