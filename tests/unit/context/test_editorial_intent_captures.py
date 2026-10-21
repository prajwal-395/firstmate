"""PLAN-001: the canonical editorial intent contract, proved against named defects.

The commission reaches Ren as prose, and every answer to "what did they
ask for, what must not change, what may Ren decide alone" used to be
re-derived per step, per prompt and per run.  The canonical record
(`library/tools/editorial_intent.py`) is where those answers live; this
file names the defects the contract closes:

    a stated instruction with nowhere to live      PLAN-001
    a goal the requester stated, flattened          PLAN-001
    a goal Ren inferred, wearing the words          PLAN-001
    an unsupported request, forced into a kind      PLAN-001
    an ambiguous request, resolved by keyword order PLAN-001
    a request that acts alone with no kind          PLAN-001
    a preservation requirement, buried in prose     PLAN-001
    a handoff whose request kinds drift             PLAN-001
    a spec that may leave the requested changes out PLAN-001

None of them asserts a count or a shape for its own sake: each one
builds a record and reads it back, so a test passes only when the
record actually carries - or actually refuses - the defect.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from library.tools import editorial_intent as ei
from library.tools import intent_model

REPO = Path(__file__).resolve().parents[3]
CORPUS = REPO / "tests/fixtures/intent_contract/captain_instructions.json"
HANDOFF = REPO / "library/steps/step_2_01_creative_direction/handoff.md"
MEASUREMENT = REPO / "scripts/measure_intent_recoverability.py"


def _measurement():
    """The measurement, loaded from the script that owns it."""
    spec = importlib.util.spec_from_file_location("intent_measurement",
                                                  MEASUREMENT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.measure(module.load_module(None))


def _corpus() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _spec(**overrides) -> dict:
    spec = {
        "requirements": [],
        "preferences": [],
        "narrative_goals": [],
        "requests": [],
    }
    spec.update(overrides)
    return spec


# ── Every stated instruction has a place ───────────────────────────────

def test_every_stated_instruction_of_a_real_commission_is_recoverable():
    """The defect: a captain's instruction reaches the record, and the
    pieces of it that have no slot - the target it acts on, the bound
    the requester put on it, what must not change, what it must
    produce, the latitude it was given - are dropped on the way.

    The corpus is nine captain instructions, quoted verbatim, and every
    stated instruction in them is counted against the record.
    """
    totals = _measurement()["totals"]
    assert totals["stated"] > 0
    assert totals["verbatim"] == totals["stated"], (
        "the corpus must be the captain's own words")
    assert totals["slot"] == totals["stated"], (
        "a stated instruction has nowhere to live in the record")
    assert totals["text"] == totals["stated"]
    assert totals["unbuildable"] == 0
    assert totals["invalid"] == 0


def test_the_goals_the_requester_stated_are_goals_and_not_requirements():
    """The defect: what the commission wants to be true is flattened
    into one summary line, so a reader cannot find the goals, cannot
    tell them from the mandates, and loses the source wording."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(narrative_goals=[
            {"id": "goal:1", "statement": "get reel 22 to match the rest",
             "source": "the captain, 2026-10-01"}]))
    assert [goal.statement for goal in intent.goals] == [
        "get reel 22 to match the rest"]
    assert intent.goals[0].source == "the captain, 2026-10-01"
    assert intent.goals[0].kind == ei.GOAL
    assert intent.requirements == ()


def test_a_goal_ren_inferred_is_never_the_requesters_own_words():
    """The defect: a goal Ren read out of the footage is listed beside
    the ones the requester stated, and a reader cannot tell which is
    which."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(
            narrative_goals=[
                {"id": "goal:1", "statement": "the overlays are stills",
                 "explicit": False,
                 "provenance": "the other reels use stills"}],
            requests=[{"id": "request:1",
                       "statement": "overlays made as fixed-length videos "
                                    "instead of stills"}]))
    assert intent.goals == ()
    assert [goal.statement for goal in intent.inferred_goals] == [
        "the overlays are stills"]
    assert intent.inferred_goals[0].explicit is False
    assert intent.inferred_goals[0].kind == ei.INFERRED_GOAL
    assert ei.validate_intent(intent) == []


# ── A request that names no kind stays in the record ───────────────────

def test_a_request_that_names_no_kind_stays_in_the_record():
    """The defect: a request the commission words in a way that fits no
    declared kind is dropped, so the words are in the record and the
    request is not."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(requests=[
            {"id": "request:1",
             "statement": "change the light pink from Akshita to one of "
                          "the other Lucie brand colors"}]))
    request = intent.requests[0]
    assert request.kind == ei.UNDETERMINED_KIND
    assert request.statement.startswith("change the light pink")
    assert request.question
    assert ei.validate_intent(intent) == []


def test_a_request_that_fits_two_kinds_keeps_both_as_candidates():
    """The defect: a request whose words fit two kinds is resolved by
    keyword order, and the engine picks one the commission never
    chose - the two kinds carry different evidence bars."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(requests=[
            {"id": "request:1",
             "statement": "tighten the dialogue and remove the repetition"}]))
    request = intent.requests[0]
    assert request.kind == ei.UNDETERMINED_KIND
    assert set(request.candidates) == {ei.TIGHTEN_DIALOGUE,
                                       ei.REMOVE_REPETITION}
    assert ei.validate_intent(intent) == []


def test_a_request_that_names_no_kind_cannot_act_alone():
    """The defect: a change nobody has identified is autonomous, and
    Ren acts on it without asking."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(requests=[
            {"id": "request:1", "statement": "make it sing"}]))
    assert intent.requests[0].autonomy != ei.AUTONOMOUS

    forced = ei.EditorialIntent(
        intent_id="intent:1",
        desired_outcome="make it sing",
        requests=(ei.IntentRequest(
            request_id="request:1", kind=ei.UNDETERMINED_KIND,
            statement="make it sing", autonomy=ei.AUTONOMOUS),))
    problems = ei.validate_intent(forced)
    assert any("cannot act alone" in problem for problem in problems), problems


def test_a_request_that_asks_nothing_is_refused():
    """The defect: a request Ren may not act on alone says which question
    it is waiting for, and the record accepts one that waits for
    nothing."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(requests=[
            {"id": "request:1", "statement": "remove the repetition",
             "autonomy": ei.NEEDS_CLARIFICATION}]))
    problems = ei.validate_intent(intent)
    assert any("asks nothing" in problem for problem in problems), problems


def test_a_request_that_names_an_unknown_candidate_kind_is_refused():
    """The defect: the request kinds have two spellings - one in the
    enumeration and one in the record - and a candidate nobody declares
    is accepted."""
    with pytest.raises(ei.IntentError) as raised:
        ei.build_intent(
            intent_id="intent:1",
            editorial_spec=_spec(requests=[
                {"id": "request:1", "statement": "make it sing",
                 "candidates": ["make_it_great"]}]))
    assert "make_it_great" in str(raised.value)


# ── What the change acts on, and what must not change ──────────────────

def test_the_request_carries_what_it_acts_on_and_what_must_not_change():
    """The defect: a requested change is a sentence, so what it acts
    on, what the requester bound it by, what must not change and what
    it must produce are prose nobody can act on."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(requests=[
            {"id": "request:1",
             "statement": "make sure you get reel 22 to match the rest",
             "target_roles": ["the video positioning", "the twitter text"],
             "explicit_constraints": [
                 "there is no twitter text like there are with the "
                 "other reels"],
             "preservation": ["the word timings are fine"],
             "outputs": ["reel 22 to match the rest"],
             "accepted_autonomy": "lets go with the second option"}]))
    request = intent.requests[0]
    assert request.target_roles == ("the video positioning",
                                    "the twitter text")
    assert request.explicit_constraints == (
        "there is no twitter text like there are with the other reels",)
    assert request.preservation == ("the word timings are fine",)
    assert request.outputs == ("reel 22 to match the rest",)
    assert request.accepted_autonomy == "lets go with the second option"


def test_what_the_request_carries_survives_the_project_model_row():
    """The defect: the durable record is a projection of the canonical
    one, and the projection drops what the request carries."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(
            narrative_goals=[{"id": "goal:1", "statement": "match the rest"}],
            requests=[
                {"id": "request:1", "statement": "match the rest",
                 "target_roles": ["the twitter text"],
                 "explicit_constraints": ["like the other reels"],
                 "preservation": ["the word timings are fine"],
                 "outputs": ["a reel 22"],
                 "accepted_autonomy": "adopt that approach"}]))
    row = ei.to_project_model_row(intent)
    back = ei.from_project_model_row(row)
    assert back.to_dict() == intent.to_dict()
    request = back.requests[0]
    assert request.target_roles == ("the twitter text",)
    assert request.preservation == ("the word timings are fine",)
    assert request.outputs == ("a reel 22",)
    assert request.accepted_autonomy == "adopt that approach"
    assert [goal.statement for goal in back.goals] == ["match the rest"]


def test_a_commission_that_names_no_goal_is_still_a_commission():
    """The defect: the record refuses a commission that names the
    changes it wants but no goal, so the requester's instruction is
    rejected for a summary line nobody asked for."""
    intent = ei.build_intent(
        intent_id="intent:1",
        editorial_spec=_spec(requests=[
            {"id": "request:1", "statement": "fix the other stuff we need to"}]))
    assert intent.desired_outcome == "fix the other stuff we need to"
    assert ei.validate_intent(intent) == []


# ── The producer: the spec the run carries ─────────────────────────────

def test_the_spec_must_carry_the_requested_changes():
    """The defect: the commission's requested changes are optional in
    the spec, so an omitted decision is indistinguishable from an
    unasked one."""
    spec = {"requirements": [], "preferences": [], "target_duration": None,
            "platform": None, "audience": None, "pacing": [],
            "subjects": {"include": [], "exclude": []},
            "narrative_goals": [], "desired_content": [],
            "prohibited_content": [], "references": [],
            "unresolved_decisions": [],
            "revision": {"revision_of": None, "preserved": [],
                         "changed": [], "removed": []}}
    problems = intent_model.validate_editorial_spec(spec)
    assert any("missing requests" in problem for problem in problems), problems


def test_the_spec_refuses_a_request_kind_nobody_declares():
    """The defect: the spec names a request kind the engine does not
    declare, and the vocabulary has two spellings."""
    spec = {"requirements": [], "preferences": [], "target_duration": None,
            "platform": None, "audience": None, "pacing": [],
            "subjects": {"include": [], "exclude": []},
            "narrative_goals": [], "desired_content": [],
            "prohibited_content": [], "references": [],
            "unresolved_decisions": [],
            "revision": {"revision_of": None, "preserved": [],
                         "changed": [], "removed": []},
            "requests": [{"id": "request:1", "statement": "do the thing",
                          "kind": "make_it_great"}]}
    problems = intent_model.validate_editorial_spec(spec)
    assert any("make_it_great" in problem for problem in problems), problems


def test_the_spec_accepts_a_request_that_names_no_kind():
    spec = {"requirements": [], "preferences": [], "target_duration": None,
            "platform": None, "audience": None, "pacing": [],
            "subjects": {"include": [], "exclude": []},
            "narrative_goals": [], "desired_content": [],
            "prohibited_content": [], "references": [],
            "unresolved_decisions": [],
            "revision": {"revision_of": None, "preserved": [],
                         "changed": [], "removed": []},
            "requests": [{"id": "request:1",
                          "statement": "change the light pink from Akshita"}]}
    assert intent_model.validate_editorial_spec(spec) == []


# ── The handoff names the kinds the engine declares ────────────────────

def test_the_handoff_names_exactly_the_declared_request_kinds():
    """The defect: the handoff lists the request kinds in prose beside
    the enumeration in code, and the two drift - so the model is asked
    for a kind the engine refuses."""
    text = HANDOFF.read_text(encoding="utf-8")
    sentences = re.split(r"(?<=\.)\s", text)
    naming = [sentence for sentence in sentences if "improve_pacing" in sentence]
    assert naming, "the handoff no longer names the request kinds"
    named = {name for name in re.findall(r"`([a-z_]+)`", naming[0])
             if name != "kind"}
    assert named == set(ei.REQUEST_KINDS), (
        "the handoff and the enumeration disagree about the request kinds")


@pytest.mark.parametrize("facet", ["goals", "inferred_goals", "requests",
                                   "target_roles", "explicit_constraints",
                                   "preservation", "outputs",
                                   "accepted_autonomy"])
def test_the_record_carries_every_facet_it_names(facet):
    """The defect: a field the record names is a field a reader looks
    for, and an empty one is not a field."""
    assert hasattr(ei.IntentRequest, facet) or hasattr(ei.EditorialIntent,
                                                       facet)
