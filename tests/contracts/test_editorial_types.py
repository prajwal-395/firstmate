"""The fact/judgment/request contract, proved against named defects.

The review's rule: a judgment must never silently become a fact or a
request, and a fact is never written by a planner.  Each test names one
defect that rule prevents.  None of them asserts that a registry contains
a particular entry or a particular count - the shipped registry is
checked against the contract, and the contract is checked to FAIL on a
broken copy, which is what makes these gates able to fail.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.tools import editorial_types as et
from library.tools.context_views import CONTEXT_VIEWS, VIEW_TYPES, build_view

STEPS = REPO / "library" / "steps"


def _step_output_keys() -> set:
    """Every state key the steps declare as an output."""
    keys = set()
    for manifest in sorted(STEPS.glob("*/manifest.json")):
        m = json.loads(manifest.read_text(encoding="utf-8"))
        for out in (m.get("interface") or {}).get("outputs") or []:
            keys.add(out["name"])
    return keys


def _broken(**overrides) -> et.EditorialTypes:
    """A copy of the shipped registry with one field replaced."""
    base = {
        "key_types": dict(et.KEY_TYPES),
        "key_producers": dict(et.KEY_PRODUCERS),
        "judgment_provenance": dict(et.JUDGMENT_PROVENANCE),
        "planner_steps": frozenset(et.PLANNER_STEPS),
    }
    base.update(overrides)
    return et.EditorialTypes(**base)


# ── The registry is complete and clean ────────────────────────────────

def test_every_step_output_key_is_typed():
    """A step output key nothing typed is a gap the contract refuses.

    The done definition is "every editorial state key is typed".  This
    walks the manifests - the authoritative list of what the steps write -
    and refuses a key absent from the registry.
    """
    untyped = _step_output_keys() - set(et.KEY_TYPES)
    assert not untyped, f"step output keys with no editorial type: {sorted(untyped)}"


def test_the_shipped_registry_satisfies_the_contract():
    """The shipped registry writes no fact from a planner and leaves no
    judgment uncited - the contract holds, not just the mutations."""
    assert et.registry_problems() == []


# ── A fact key written by a planner ───────────────────────────────────

def test_a_fact_key_produced_by_a_planner_is_refused():
    """A planner stating a judgment as a fact is the defect.

    `clip_catalog` is a measurement step_1_02 writes.  Reassign its
    producer to a planning step and the contract must name the key.
    """
    broken = _broken(key_producers={**et.KEY_PRODUCERS,
                                    "clip_catalog": "step_2_01_creative_direction"})
    problems = et.check_contract(broken)
    assert any("clip_catalog" in p and "planner" in p for p in problems), problems


def test_a_planner_produced_key_typed_fact_is_refused():
    """The same defect from the type side: a planner's own output typed
    as a measurement.  `creative_direction` is step_2_01's judgment;
    typing it fact must be refused."""
    broken = _broken(key_types={**et.KEY_TYPES,
                                "creative_direction": et.FACT})
    problems = et.check_contract(broken)
    assert any("creative_direction" in p and "planner" in p for p in problems), problems


# ── A judgment that cites nothing ─────────────────────────────────────

def test_a_judgment_with_no_provenance_is_refused():
    """A judgment resting on nothing is a judgment presented as a fact."""
    broken = _broken(judgment_provenance={**et.JUDGMENT_PROVENANCE,
                                          "creative_direction": ()})
    problems = et.check_contract(broken)
    assert any("creative_direction" in p for p in problems), problems


def test_a_judgment_citing_only_other_judgments_is_refused():
    """Citations that name no fact or request key ground nothing: the
    judgment is a decision resting on decisions, which is how a planner's
    taste comes to read as a measurement."""
    broken = _broken(judgment_provenance={**et.JUDGMENT_PROVENANCE,
                                          "speech_sequence": ("creative_direction",)})
    problems = et.check_contract(broken)
    assert any("speech_sequence" in p for p in problems), problems


# ── A judgment in a state with nothing under it ───────────────────────

def test_a_judgment_with_no_fact_in_the_state_is_refused():
    """`creative_direction` cites the catalog, the vision documents, the
    prosody and the brief.  A state holding the direction and none of
    them is a direction that rests on nothing."""
    problems = et.validate_state({"creative_direction": {"narrative_theme": "x"}})
    assert any("creative_direction" in p for p in problems), problems


def test_a_judgment_grounded_in_a_present_fact_passes():
    """The same judgment beside a fact it cites is grounded."""
    state = {
        "clip_catalog": [{"clip_id": "clip_001"}],
        "creative_direction": {"narrative_theme": "x"},
    }
    assert et.validate_state(state) == []


def test_a_judgment_grounded_in_a_present_request_passes():
    """A judgment may rest on the editor's request, not only on a
    measurement - `color_grade_spec` honors the declared brand template."""
    state = {
        "brand_template": {"style": {"series_look": "x"}},
        "color_grade_spec": {"corrections": []},
    }
    assert et.validate_state(state) == []


def test_a_judgment_grounded_only_in_an_absent_fact_is_refused():
    """A citation to a fact the state does not hold is a dangling
    provenance link, not a grounding.  `color_grade_spec` cites the brand
    template; a state holding the spec and no template leaves it
    ungrounded."""
    state = {"color_grade_spec": {"corrections": []}}
    problems = et.validate_state(state)
    assert any("color_grade_spec" in p for p in problems), problems


def test_non_editorial_keys_are_not_judgments():
    """Run metadata is not editorial state; the validator must not read
    it as an untyped judgment."""
    assert et.validate_state({"pipeline": {"run": "x"},
                              "capability_outputs": {}}) == []


# ── A view built from a request ───────────────────────────────────────

def test_a_measurement_view_built_from_a_request_is_refused():
    """A view typed fact reading the editor's brief would present the
    editor's taste as a measurement - the defect at the prompt layer."""
    with pytest.raises(et.TypeContractError):
        et.check_view_type(et.FACT, ("creative_brief",))


def test_a_judgment_view_built_from_a_request_is_refused():
    """The same defect for a judgment reading: a view typed judgment
    reading the brief would pass Ren's reasoning off as the editor's
    instruction."""
    with pytest.raises(et.TypeContractError):
        et.check_view_type(et.JUDGMENT, ("taste_profile",))


def test_a_request_view_built_from_a_measurement_is_refused():
    """A view typed request reading a catalog would present a measurement
    as the editor's instruction - a request with no editor behind it."""
    with pytest.raises(et.TypeContractError):
        et.check_view_type(et.REQUEST, ("clip_catalog",))


def test_a_view_built_from_a_measurement_passes():
    """A fact view reading a fact is the honest case."""
    et.check_view_type(et.FACT, ("clip_catalog", "temporal_event_indices"))


def test_build_view_refuses_a_view_whose_type_contradicts_its_source():
    """`build_view` enforces the view type on every build.  Retype the
    transcript view as a request - it reads `temporal_index`, a fact -
    and the build must refuse."""
    data = {"temporal_index": [{"clip_id": "c1",
                                "speech_regions": [{"text": "hi",
                                                    "start": 0.0,
                                                    "end": 1.0}]}]}
    with pytest.raises(et.TypeContractError), _view_types(
            {"transcript": (et.REQUEST, ("temporal_event_indices",))}):
        build_view("transcript", data)


def test_build_view_succeeds_when_the_type_matches_the_source():
    """The honest case through the real registry: a fact view reading a
    fact builds."""
    data = {"temporal_index": [{"clip_id": "c1",
                                "speech_regions": [{"text": "hi",
                                                    "start": 0.0,
                                                    "end": 1.0}]}]}
    built = build_view("transcript", data)
    assert built["transcript"][0]["text"] == "hi"


def test_every_view_carries_a_type():
    """A view with no type declaration is a reading that reaches a prompt
    saying whether it is a measurement or a decision."""
    untyped = set(CONTEXT_VIEWS) - set(VIEW_TYPES)
    assert not untyped, f"views carrying no editorial type: {sorted(untyped)}"


class _view_types:
    """Replace VIEW_TYPES for the duration of a `with` block."""

    def __init__(self, replacement: dict):
        self._replacement = replacement

    def __enter__(self):
        self._original = dict(VIEW_TYPES)
        VIEW_TYPES.clear()
        VIEW_TYPES.update(self._replacement)
        return VIEW_TYPES

    def __exit__(self, *exc):
        VIEW_TYPES.clear()
        VIEW_TYPES.update(self._original)
        return False
