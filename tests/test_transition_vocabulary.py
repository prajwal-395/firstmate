"""Every transition type advertised anywhere must be one the renderer draws.

This is the test the audit asked for: a vocabulary was advertised at one
end and implemented at the other, with nothing in between comparing them.
The handoff taught nine types, the brand templates allowed three, the
selector honoured two and the renderer could draw four - and no two of
those sets shared a token, so not one transition of any kind reached the
finished video.
"""
import pathlib
import re

import pytest
import yaml

from library.tools.fusion.effects import DRAWABLE_TRANSITIONS, EffectBlock, fx
from library.tools.transition_vocabulary import (
    ALIASES,
    CUT_TYPES,
    FUSION_TYPES,
    PLANNABLE_TYPES,
    WITHDRAWN,
    canonical_type,
    filter_allowed,
    is_cut,
    is_drawn,
    withdrawal_reason,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
HANDOFF = REPO / "library/steps/step_4_02_plan_transitions/handoff.md"
TEMPLATES = sorted((REPO / "library/templates").glob("*.yaml"))
CLIP_DUR = 90
#: The source frame every builder below sizes its canvas at. The
#: builders take no default frame, so each call states it - the same
#: numbers the removed defaults carried.
RES = (1080, 1920)


# ── The vocabulary agrees with the renderer ──

def test_fusion_types_are_exactly_what_the_engine_draws():
    assert set(FUSION_TYPES) == set(DRAWABLE_TRANSITIONS)


@pytest.mark.parametrize("ttype", FUSION_TYPES)
def test_every_drawable_type_produces_real_nodes(ttype):
    for build in (fx.transition_tail, fx.transition_head):
        block = build(CLIP_DUR, ttype, 10, res=RES)
        assert isinstance(block, EffectBlock)
        assert block.nodes, f"{build.__name__}({ttype}) drew nothing"
        assert block.input_name


@pytest.mark.parametrize("ttype", sorted(WITHDRAWN) + ["utter_nonsense"])
def test_an_undrawable_type_raises_instead_of_drawing_nothing(ttype):
    """The silent empty block is the whole defect: `dissolve` reached the
    renderer, matched no branch, and produced a comp with nothing in it."""
    for build in (fx.transition_tail, fx.transition_head):
        with pytest.raises(ValueError, match="No Fusion transition builder"):
            build(CLIP_DUR, ttype, 10, res=RES)


def test_cut_types_are_not_drawn():
    for ttype in CUT_TYPES:
        assert is_cut(ttype)
        assert not is_drawn(ttype)
        with pytest.raises(ValueError):
            fx.transition_tail(CLIP_DUR, ttype, 10, res=RES)


# ── Nothing advertises what the renderer cannot honour ──

def _handoff_toolkit_types() -> set:
    """The type names in the handoff's toolkit table, as backticked cells."""
    text = HANDOFF.read_text()
    table = text.split("### Transition toolkit:", 1)[1].split("###", 1)[0]
    return {
        m.group(1)
        for line in table.splitlines() if line.startswith("| `")
        for m in [re.match(r"\|\s*`([^`]+)`", line)]
        if m
    }


def test_the_handoff_offers_only_plannable_types():
    offered = _handoff_toolkit_types()
    assert offered, "could not parse the toolkit table"
    assert offered <= set(PLANNABLE_TYPES), sorted(offered - set(PLANNABLE_TYPES))


def test_the_handoff_offers_every_plannable_type():
    """A drawable transition nobody is told about is dead capability."""
    assert _handoff_toolkit_types() == set(PLANNABLE_TYPES)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_brand_templates_allow_only_plannable_types(template):
    data = yaml.safe_load(template.read_text()) or {}
    types = (data.get("effect") or {}).get("transition_types") or []
    allowed, rejected = filter_allowed(types)
    assert not rejected, f"{template.name} lists {rejected}"
    assert allowed, f"{template.name} would permit no transition at all"


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

def test_withdrawn_and_plannable_do_not_overlap():
    assert not set(WITHDRAWN) & set(PLANNABLE_TYPES)


def test_aliases_resolve_to_plannable_types():
    for alias, target in ALIASES.items():
        assert target in PLANNABLE_TYPES, alias
        assert canonical_type(alias) == target


def test_withdrawn_types_come_back_with_a_reason():
    for ttype in WITHDRAWN:
        assert canonical_type(ttype) is None
        assert len(withdrawal_reason(ttype)) > 40, ttype


def test_canonicalisation_is_case_and_space_insensitive():
    assert canonical_type("  Fade_To_Black ") == "fade_to_black"
    assert canonical_type("CUT") == "hard_cut"


def test_an_absent_request_is_not_a_request():
    assert canonical_type("") is None
    assert canonical_type(None) is None
