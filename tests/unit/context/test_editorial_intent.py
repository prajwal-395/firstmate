"""The canonical EditorialIntent, proved against named defects.

The gap: the commission reaches Ren as prose, so every answer to "what
did the user require, what did they prefer, what did Ren infer, and
what evidence is still missing" is re-derived per step and per run - and
a preference can drift into a requirement without anything noticing.

Each test names ONE defect the canonical record closes:

    a requirement nobody can check          EDIT-004
    an inference wearing the user's words   EDIT-003
    a request whose evidence bar is unmet   EDIT-006, EDIT-008
    a request whose kind is ambiguous        EDIT-005
    a request that needs a question          EDIT-007
    a project-model row nobody wrote         EDIT-001
    an edit pointing at no intent            EDIT-009, EDIT-010

None of them asserts that the module holds a particular entry or count:
the record is built and mutated, so a test passes only when the record
actually refuses the defect.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from library.tools import editorial_intent as ei


def _spec(**overrides):
    spec = {
        "requirements": [{"id": "req-1", "statement": "the reel is 45-60 seconds",
                          "source": "the brief says 45-60 seconds"}],
        "preferences": [{"id": "pref-1", "statement": "punchy and to the point",
                         "source": "the brief says punchy"}],
        "target_duration": {"minimum_seconds": 45, "target_seconds": 55,
                            "maximum_seconds": 60},
        "platform": "vertical",
        "audience": None,
        "pacing": ["quick cuts in the opening"],
        "subjects": {"include": [], "exclude": []},
        "narrative_goals": ["the viewer learns the price"],
        "desired_content": ["the price"],
        "prohibited_content": [],
        "references": [],
        "unresolved_decisions": [],
        "revision": {"revision_of": None, "preserved": [], "changed": [],
                     "removed": []},
    }
    spec.update(overrides)
    return spec


def _intent(**overrides):
    kwargs = {
        "intent_id": "intent:1",
        "editorial_spec": _spec(),
        "brief_ref": "/tmp/brief.md",
        "requests": [{"id": "request:1",
                      "statement": "remove the repetition in the answer",
                      "evidence_lanes": ["m1"]}],
        "inferred": [{"id": "inf:1", "statement": "quick openings hold attention"}],
    }
    kwargs.update(overrides)
    return ei.build_intent(**kwargs)


# ── The record is well formed ──────────────────────────────────────────

def test_a_valid_intent_has_no_problems():
    assert ei.validate_intent(_intent()) == []


def test_the_intent_records_the_outcome_the_commission_asked_for():
    """EDIT-002: the desired outcome is a field, not a sentence somebody
    re-reads per step."""
    intent = _intent()
    assert intent.desired_outcome == "the viewer learns the price"
    assert intent.brief_ref == "/tmp/brief.md"


def test_the_requirements_and_the_preferences_stay_apart():
    """EDIT-002: a requirement is mandatory, a preference may yield."""
    intent = _intent()
    assert [row.statement for row in intent.requirements] == [
        "the reel is 45-60 seconds"]
    assert [row.statement for row in intent.preferences] == [
        "punchy and to the point"]


def test_an_inferred_preference_is_never_the_requesters_own_words():
    """EDIT-003: what Ren read out of the footage is evidence, and the
    record keeps it in its own list."""
    intent = _intent()
    assert [row.statement for row in intent.inferred_preferences] == [
        "quick openings hold attention"]
    assert all(not row.explicit for row in intent.inferred_preferences)
    assert all(row.explicit for row in intent.requirements)
    assert all(row.explicit for row in intent.preferences)


def test_the_scoped_intent_views_inferences_become_inferred_preferences():
    """EDIT-003: the machinery that already separates the sources is the
    one this reads, so the two cannot disagree."""
    view = {"declarations": [
        {"key": "tempo", "value": "quick", "source": "inference",
         "provenance": "intent_model.tempo", "confidence": 0.6},
        {"key": "caption_size", "value": 48, "source": "creator",
         "provenance": "taste_profile:caption_size", "confidence": 1.0},
    ]}
    rows = ei.inferred_from_view(view)
    assert [row["id"] for row in rows] == ["tempo"]


# ── A requirement nobody can check ─────────────────────────────────────

def test_a_requirement_nobody_can_check_is_refused():
    """EDIT-004: a mandatory statement with nothing to measure it against
    is a goal wearing a requirement's name."""
    intent = _intent()
    broken = copy.deepcopy(intent)
    object.__setattr__(
        broken, "requirements",
        (ei.Declaration(declaration_id="req-1",
                        statement="the reel is 45-60 seconds",
                        kind=ei.REQUIREMENT, explicit=True, objective=False,
                        source="the brief"),))
    problems = ei.validate_intent(broken)
    assert any("not objective" in problem for problem in problems), problems


def test_a_preference_may_declare_itself_objective():
    """EDIT-004: the classification is per entry, not per list."""
    intent = _intent()
    measurable = ei.Declaration(declaration_id="pref-2",
                                statement="the frame is vertical",
                                kind=ei.PREFERENCE, explicit=True,
                                objective=True, source="the brief")
    assert ei.validate_intent(
        ei.EditorialIntent(**{**intent.__dict__,
                              "preferences": intent.preferences + (measurable,)}
                           )) == []


# ── An inference wearing the requesters words ─────────────────────────

def test_an_inferred_preference_marked_explicit_is_refused():
    """EDIT-003: an inference is evidence, never an instruction."""
    intent = _intent()
    broken = copy.deepcopy(intent)
    object.__setattr__(
        broken, "inferred_preferences",
        (ei.Declaration(declaration_id="inf:1",
                        statement="quick openings hold attention",
                        kind=ei.INFERRED_PREFERENCE, explicit=True,
                        objective=False, source="inference"),))
    problems = ei.validate_intent(broken)
    assert any("marked explicit" in problem for problem in problems), problems


def test_an_explicit_preference_marked_inferred_is_refused():
    """EDIT-003: the other direction - the requester's own words cannot
    be downgraded into an inference."""
    intent = _intent()
    broken = copy.deepcopy(intent)
    object.__setattr__(
        broken, "preferences",
        (ei.Declaration(declaration_id="pref-1",
                        statement="punchy and to the point",
                        kind=ei.PREFERENCE, explicit=False, objective=False,
                        source="the brief"),))
    problems = ei.validate_intent(broken)
    assert any("marked inferred" in problem for problem in problems), problems


# ── The request kinds (EDIT-005) ───────────────────────────────────────

def test_the_named_editorial_requests_are_one_enumeration():
    assert set(ei.REQUEST_KINDS) == {
        "improve_pacing", "strengthen_opening", "remove_repetition",
        "improve_continuity", "choose_better_angle", "tighten_dialogue",
        "replace_broll", "correct_captions", "revise_graphics", "mixed_edit"}


@pytest.mark.parametrize(
    ("statement", "expected", "evidence_bar"),
    [
        ("Clean up dialogue retakes", "tighten_dialogue", ("m1",)),
        ("Replace the B-roll shot", "replace_broll", ("m3", "m4")),
        ("Correct captions", "correct_captions", ("m1", "ocr_extraction")),
        ("Revise the logo", "revise_graphics", ("m3",)),
        ("Make a mixed edit", "mixed_edit",
         ("m1", "m3", "ocr_extraction", "m5", "m4")),
    ],
)
def test_e2e_workflow_briefs_have_a_named_intent_and_evidence_bar(
    statement, expected, evidence_bar
):
    intent = ei.build_intent(
        intent_id="intent:e2e",
        requests=[{"id": "request:e2e", "statement": statement}],
    )

    assert intent.requests[0].kind == expected
    assert intent.requests[0].autonomy == ei.REQUEST_PROFILES[expected].autonomy
    assert intent.requests[0].evidence_bar == evidence_bar


def test_a_statement_naming_two_request_kinds_is_refused():
    """EDIT-005: the choice between two kinds is the commissioner's, and
    the two carry different evidence bars."""
    with pytest.raises(ei.IntentError) as raised:
        ei.request_kind("tighten the dialogue and remove the repetition")
    assert "more than one request kind" in str(raised.value)


def test_a_statement_naming_no_request_kind_is_refused():
    with pytest.raises(ei.IntentError) as raised:
        ei.request_kind("make it sing")
    assert "none of the request kinds" in str(raised.value)


def test_a_request_names_the_kind_the_commission_used():
    """EDIT-005: the kind is recorded, not re-derived per step."""
    intent = _intent(requests=[{"id": "request:1",
                                 "statement": "the opening is weak, strengthen it",
                                 "evidence_lanes": ["m1", "m3"]}])
    assert intent.requests[0].kind == "strengthen_opening"


def test_a_request_naming_an_unknown_kind_is_refused():
    with pytest.raises(ei.IntentError) as raised:
        ei.build_intent(intent_id="intent:1", editorial_spec=_spec(),
                        requests=[{"id": "request:1",
                                   "statement": "do the thing",
                                   "kind": "make_it_great"}])
    assert "make_it_great" in str(raised.value)


# ── The evidence bar (EDIT-006, EDIT-008) ──────────────────────────────

def test_a_request_whose_evidence_bar_is_unmet_records_what_is_missing():
    """EDIT-008: the gap is a field, not silence."""
    intent = _intent(requests=[{"id": "request:1",
                                 "statement": "remove the repetition"}])
    request = intent.requests[0]
    assert ei.missing_lanes(request) == ("m1",)
    assert [row["lane"] for row in request.missing_evidence] == ["m1"]
    assert row_status(request) == ei.NOT_YET_ANALYSED


def row_status(request):
    return request.missing_evidence[0].get("status")


def test_a_request_that_hides_its_missing_evidence_is_refused():
    """EDIT-008: silence is not evidence."""
    intent = _intent()
    hidden = ei.IntentRequest(
        request_id="request:1", kind="remove_repetition",
        statement="remove the repetition", explicit=True, objective=True,
        autonomy="autonomous", evidence_bar=("m1",), evidence_lanes=(),
        missing_evidence=())
    broken = copy.deepcopy(intent)
    object.__setattr__(broken, "requests", (hidden,))
    problems = ei.validate_intent(broken)
    assert any("declares nothing about it" in problem
               for problem in problems), problems


def test_a_request_may_narrow_its_bar_but_not_widen_it():
    """EDIT-006: the bar is a floor Ren may raise, never lower."""
    narrowed = ei.build_intent(
        intent_id="intent:1", editorial_spec=_spec(),
        requests=[{"id": "request:1",
                   "statement": "remove the repetition",
                   "evidence_bar": ["m1"], "evidence_lanes": ["m1"]}])
    assert narrowed.requests[0].evidence_bar == ("m1",)
    widened = ei.build_intent(
        intent_id="intent:1", editorial_spec=_spec(),
        requests=[{"id": "request:1",
                   "statement": "remove the repetition",
                   "evidence_bar": ["m1", "m3"], "evidence_lanes": ["m1"]}])
    problems = ei.validate_intent(widened)
    assert any("may be narrowed" in problem for problem in problems), problems


def test_an_evidence_bar_naming_an_unknown_lane_is_refused():
    intent = ei.build_intent(
        intent_id="intent:1", editorial_spec=_spec(),
        requests=[{"id": "request:1",
                   "statement": "remove the repetition",
                   "evidence_bar": ["m99"], "evidence_lanes": []}])
    problems = ei.validate_intent(intent)
    assert any("m99" in problem for problem in problems), problems


# ── Autonomy (EDIT-007) ────────────────────────────────────────────────

def test_a_request_that_needs_clarification_must_ask_something():
    """EDIT-007: a request Ren may not act on alone says which question
    it is waiting for."""
    intent = _intent(requests=[{"id": "request:1",
                                 "statement": "remove the repetition",
                                 "evidence_lanes": ["m1"],
                                 "autonomy": ei.NEEDS_CLARIFICATION}])
    problems = ei.validate_intent(intent)
    assert any("needs clarification and asks nothing" in problem
               for problem in problems), problems
    asked = ei.build_intent(
        intent_id="intent:1", editorial_spec=_spec(),
        requests=[{"id": "request:1",
                   "statement": "remove the repetition",
                   "evidence_lanes": ["m1"],
                   "autonomy": ei.NEEDS_CLARIFICATION,
                   "question": "which of the two answers is the repeat?"}])
    assert ei.validate_intent(asked) == []


def test_a_request_records_the_basis_that_decided_its_autonomy():
    """EDIT-007: the record says which, and what decided it."""
    intent = _intent()
    request = intent.requests[0]
    assert request.autonomy == ei.AUTONOMOUS
    assert "remove_repetition" in request.autonomy_basis


# ── The Project Model projection (EDIT-001) ────────────────────────────

def test_the_intent_round_trips_through_the_project_model_row():
    """EDIT-001: the durable record and the canonical record are one
    record read two ways."""
    intent = _intent()
    row = ei.to_project_model_row(intent)
    assert row["id"] == "intent:1"
    assert row["brief_ref"] == "/tmp/brief.md"
    assert ei.from_project_model_row(row).to_dict() == intent.to_dict()


def test_a_row_somebody_else_wrote_is_not_read_as_an_intent():
    """EDIT-001: the slot is shared with other uses of `constraints`; a
    row that is not this record is refused, not read as empty."""
    with pytest.raises(ei.IntentError) as raised:
        ei.from_project_model_row({
            "id": "intent-1", "brief_ref": "brief.md",
            "constraints": [{"kind": "trim", "frames_removed": 12}],
            "evidence_links": ["obs-1"]})
    assert "carries no canonical entries" in str(raised.value)


def test_a_row_mixing_canonical_and_other_entries_is_refused():
    with pytest.raises(ei.IntentError) as raised:
        ei.from_project_model_row({
            "id": "intent-1", "brief_ref": None,
            "constraints": [{"entry": "requirement", "statement": "x"},
                            {"kind": "trim", "frames_removed": 12}],
            "evidence_links": []})
    assert "mixes canonical entries" in str(raised.value)


# ── The intent read out of a run's state ───────────────────────────────

def test_the_intent_is_read_out_of_the_commission_the_run_carries():
    state = {"intent_model": {"editorial_spec": _spec()},
             "creative_brief": "/tmp/brief.md"}
    intent = ei.from_state(state)
    assert intent.desired_outcome == "the viewer learns the price"
    assert intent.brief_ref == "/tmp/brief.md"
    assert ei.validate_intent(intent) == []


def test_a_run_with_no_commission_yields_an_intent_with_nothing_in_it():
    """An absent commission is the commission's own statement, not a gap
    this module fills."""
    intent = ei.from_state({})
    assert intent.requirements == ()
    assert intent.requests == ()
    assert any("no desired_outcome" in problem
               for problem in ei.validate_intent(intent))


# ── The intent of a ONE-OFF request ───────────────────────────────────

def test_a_one_off_request_becomes_a_canonical_intent():
    """The defect: a request that arrives as a sentence had no path to
    the canonical record, so the harness could only read a prose
    restatement the run wrote about itself."""
    intent = ei.intent_from_request(
        "The lower third fights the captions - move it down.",
        intent_id="intent:request-1")
    assert intent.desired_outcome == (
        "The lower third fights the captions - move it down.")
    assert [request.kind for request in intent.requests] == [
        ei.REVISE_GRAPHICS]
    assert intent.requests[0].evidence_bar == ei.REQUEST_PROFILES[
        ei.REVISE_GRAPHICS].evidence_bar
    assert ei.validate_intent(intent) == []


def test_a_one_off_request_invents_no_requirement():
    """The defect: a request says "move it down", and the intent builder
    promotes it to a mandatory statement nobody asked for - an
    invention, wearing a requirement's name."""
    intent = ei.intent_from_request("Move it down.", intent_id="intent:1")
    assert intent.requirements == ()
    assert intent.preferences == ()
    assert intent.inferred_preferences == ()


def test_a_one_off_request_naming_no_kind_stays_in_the_record():
    """The defect: a request in the requester's own words that fits none
    of the declared kinds is DROPPED from the canonical record, and the
    commissioner's sentence survives only as a summary line - the words
    are in the record and the request is not, so nobody can act on it or
    ask about it.

    The request now stays, with its kind undetermined, its candidates
    empty, and the question it has to be asked.
    """
    intent = ei.intent_from_request(
        "Put the shot of the bench over the tooling bit.",
        intent_id="intent:1")
    assert len(intent.requests) == 1
    request = intent.requests[0]
    assert request.kind == ei.UNDETERMINED_KIND
    assert request.statement == "Put the shot of the bench over the tooling bit."
    assert request.autonomy == ei.NEEDS_CLARIFICATION
    assert request.question
    assert intent.desired_outcome
    assert ei.validate_intent(intent) == []


def test_a_one_off_request_that_states_nothing_is_refused():
    """The defect: an empty request becomes an intent with no outcome,
    and the validator has to catch it after the fact."""
    with pytest.raises(ei.IntentError):
        ei.intent_from_request("   ", intent_id="intent:1")


# ── The intent reaches the Project Model ───────────────────────────────

def test_the_intent_is_written_into_the_project_model(tmp_path):
    """EDIT-001: the `intents` collection had no producer; this is it."""
    from library.tools import project_model_store

    fixture = json.loads(
        (Path(__file__).resolve().parents[3] / "native" / "ren-protocol" /
         "fixtures" / "project-model.json").read_text(encoding="utf-8"))
    project_model_store.commit_project_model(tmp_path, fixture,
                                             expected_revision=0)
    intent = _intent()
    ei.commit_intent(str(tmp_path), intent)
    _revision, model = project_model_store.load_project_model(tmp_path)
    rows = {row["id"]: row for row in model["intents"]}
    assert "intent:1" in rows
    assert ei.from_project_model_row(rows["intent:1"]).to_dict() == \
        intent.to_dict()


def test_an_intent_that_is_not_valid_is_not_written(tmp_path):
    """A record with a hole in it does not reach the durable model."""
    from library.tools import project_model_store

    fixture = json.loads(
        (Path(__file__).resolve().parents[3] / "native" / "ren-protocol" /
         "fixtures" / "project-model.json").read_text(encoding="utf-8"))
    project_model_store.commit_project_model(tmp_path, fixture,
                                             expected_revision=0)
    broken = copy.deepcopy(_intent())
    object.__setattr__(broken, "desired_outcome", "")
    with pytest.raises(ei.IntentError) as raised:
        ei.commit_intent(str(tmp_path), broken)
    assert "is not valid and is not written" in str(raised.value)
    _revision, model = project_model_store.load_project_model(tmp_path)
    assert "intent:1" not in {row["id"] for row in model["intents"]}
