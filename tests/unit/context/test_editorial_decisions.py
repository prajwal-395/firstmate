"""The editorial decision graph and its compiler, proved against named defects.

The gap: a decision becomes a cut somewhere between a model's reasoning
and a clip's in/out points, and nothing records that passage - which
candidate was accepted, what evidence it rested on, what it gave up,
what effect it intended and what would show the effect landed.

Each test names ONE defect the decision layer closes:

    a candidate that compiles                  EDIT-009
    a decision with no evidence                EDIT-010
    a relation with nothing on either side     EDIT-011
    a dependency cycle                         EDIT-012
    a tradeoff nobody resolved                 EDIT-013
    a change with no effect and no criteria    EDIT-014
    an effect the execution layer wrote        EDIT-015

The last one is the load-bearing test of this lane: the compiler must
REFUSE where the request builder would have filled a gap in.
"""
from __future__ import annotations

import pytest

from library.tools import edit_request
from library.tools import editorial_decisions as ed
from library.tools import editorial_intent as ei


def _intent(**overrides):
    kwargs = {
        "intent_id": "intent:1",
        "editorial_spec": {
            "requirements": [], "preferences": [],
            "narrative_goals": ["the viewer learns the price"],
            "desired_content": [], "prohibited_content": [],
            "references": [], "unresolved_decisions": [],
            "target_duration": None, "platform": None, "audience": None,
            "pacing": [], "subjects": {}},
        "requests": [{"id": "request:1",
                      "statement": "remove the repetition in the answer",
                      "evidence_lanes": ["m1"]}],
    }
    kwargs.update(overrides)
    return ei.build_intent(**kwargs)


def _decision(**overrides):
    fields = {
        "decision_id": "decision:1",
        "intent_id": "intent:1",
        "request_id": "request:1",
        "statement": "cut the repeated answer",
        "state": ed.APPROVED,
        "stated_by": "model",
        "evidence_ids": ("words:the price is forty",),
        "intended_effect": "the viewer hears the answer once",
        "evaluation_criteria": ({"criterion": "the repeat is gone",
                                  "measure": "the rendered transcript"},),
        "operations": (ed.DecisionOperation(
            op="clip.trim",
            params={"unique_id": "clip_001", "in_frame": 10, "out_frame": 40},
            effect="the repeat is cut"),),
        "approval": {"by": "captain", "note": "yes", "at": "2026-10-20"},
    }
    fields.update(overrides)
    return ed.EditorialDecision(**fields)


def _graph(*decisions):
    return ed.DecisionGraph(intent_id="intent:1",
                             decisions=list(decisions) or [_decision()])


# ── A well formed decision ─────────────────────────────────────────────

def test_a_valid_decision_has_no_problems():
    assert ed.validate_decision(_decision()) == []


def test_a_valid_graph_has_no_problems():
    assert ed.validate_graph(_graph()) == []


def test_compile_normalizes_string_base_generation_from_edit_spec():
    """EDIT-016: the edit_spec wire format stores generations as strings."""
    request = ed.compile_to_edit_request(
        _graph(), project="Ren Scratch", timeline="reel",
        intent=_intent(), expected_base_generation="4")

    assert request.expected_base_generation == 4


# ── The five layers stay apart (EDIT-009) ──────────────────────────────

def test_a_candidate_decision_does_not_compile():
    """EDIT-009: proposed and accepted are different states."""
    graph = _graph(_decision(state=ed.CANDIDATE, approval=None))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "is a candidate" in str(raised.value)


def test_a_rejected_decision_does_not_compile():
    graph = _graph(_decision(state=ed.REJECTED))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "was rejected" in str(raised.value)


def test_a_decision_serving_no_intent_is_refused():
    """EDIT-009: a decision that serves no intent is not an editorial
    decision."""
    problems = ed.validate_decision(_decision(intent_id=""))
    assert any("no intent_id" in problem for problem in problems), problems


def test_an_approved_decision_with_no_approval_record_is_refused():
    """EDIT-009: nothing says who accepted it."""
    problems = ed.validate_decision(_decision(approval=None))
    assert any("no approval record" in problem for problem in problems), problems


# ── Evidence (EDIT-010) ────────────────────────────────────────────────

def test_a_decision_with_no_evidence_does_not_compile():
    """EDIT-010: a proposed edit nobody can point at is not grounded."""
    graph = _graph(_decision(evidence_ids=()))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "rests on no evidence" in str(raised.value)


def test_approval_cannot_be_what_hides_the_gap():
    """EDIT-010: approving a decision with no evidence is refused, so
    approval cannot stand in for the evidence."""
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.approve([_decision(evidence_ids=(), state=ed.CANDIDATE,
                              approval=None)])
    assert "rests on no evidence" in str(raised.value)


# ── The relationships a decision spans (EDIT-011) ──────────────────────

def test_a_relation_must_name_a_kind_and_something_to_point_at():
    """EDIT-011: a relationship with nothing on either side is not one."""
    problems = ed.validate_decision(_decision(
        relations=({"kind": "temporal"}, {"kind": "nope", "id": "r-1"})))
    assert any("names no id" in problem for problem in problems), problems
    assert any("nope" in problem for problem in problems), problems


def test_the_relationships_a_decision_spans_are_recorded():
    """EDIT-011: shots, dialogue, events and audio each have a kind."""
    decision = _decision(relations=(
        {"kind": "temporal", "id": "rel-1"},
        {"kind": "narrative", "id": "rel-2"},
        {"kind": "dialogue", "id": "passage-3"},
        {"kind": "audio", "id": "sound-4"},
    ))
    assert ed.validate_decision(decision) == []
    assert [row["kind"] for row in decision.relations] == [
        "temporal", "narrative", "dialogue", "audio"]


# ── Dependencies between decisions (EDIT-012) ──────────────────────────

def test_a_decision_depending_on_a_decision_that_is_not_there_is_refused():
    graph = _graph(_decision(depends_on=("decision:absent",)))
    problems = ed.validate_graph(graph)
    assert any("not in the graph" in problem for problem in problems), problems


def test_a_dependency_cycle_is_refused():
    graph = _graph(
        _decision(decision_id="decision:1", depends_on=("decision:2",)),
        _decision(decision_id="decision:2", depends_on=("decision:1",)),
    )
    problems = ed.validate_graph(graph)
    assert any("cycle" in problem for problem in problems), problems


def test_a_decision_that_waits_for_another_waits_for_its_operations():
    """EDIT-012: the compiled request orders the two decisions, and the
    one that waits comes after the one it waits for."""
    graph = _graph(
        _decision(decision_id="decision:1", depends_on=("decision:2",)),
        _decision(decision_id="decision:2"),
    )
    request = ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                         intent=_intent())
    first, second = request.operations
    assert first.depends_on == ()
    assert second.depends_on == (0,)


def test_the_compiler_orders_the_decisions_by_their_dependencies():
    graph = _graph(
        _decision(decision_id="decision:2", depends_on=("decision:1",)),
        _decision(decision_id="decision:1"),
    )
    request = ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                         intent=_intent())
    assert request.operations[0].depends_on == ()
    assert request.operations[1].depends_on == (0,)


# ── Tradeoffs (EDIT-013) ───────────────────────────────────────────────

def test_a_tradeoff_must_name_two_axes_and_a_resolution():
    """EDIT-013: a tradeoff nobody resolved is a worry, not a decision."""
    problems = ed.validate_decision(_decision(tradeoffs=(
        {"axis": "pace", "against": "clarity"},
        {"axis": "pace", "against": "pace", "resolution": "kept pace"},
        {"axis": "pace", "against": "mood", "resolution": "kept pace"},
    )))
    assert any("records no resolution" in problem
               for problem in problems), problems
    assert any("against itself" in problem for problem in problems), problems
    assert any("mood" in problem for problem in problems), problems


def test_a_tradeoff_is_recorded_with_what_was_given_up():
    """EDIT-013: pace against clarity, and how it was settled."""
    decision = _decision(tradeoffs=(
        {"axis": "pace", "against": "completeness",
         "resolution": "kept the pace, cut the second example",
         "reason": "the first example carries the point"},
    ))
    assert ed.validate_decision(decision) == []
    assert decision.tradeoffs[0]["axis"] == "pace"


# ── The effect and its criteria (EDIT-014) ─────────────────────────────

def test_a_change_with_no_intended_effect_does_not_compile():
    """EDIT-014: a change nobody said what it should do has no effect
    to look for."""
    graph = _graph(_decision(intended_effect="",
                              operations=(ed.DecisionOperation(
                                  op="clip.trim",
                                  params={"unique_id": "clip_001",
                                          "in_frame": 10, "out_frame": 40},
                                  effect=""),)))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "no intended effect" in str(raised.value)


def test_a_change_with_no_evaluation_criteria_does_not_compile():
    """EDIT-014: nothing would show whether the effect landed."""
    graph = _graph(_decision(evaluation_criteria=()))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "no evaluation criteria" in str(raised.value)


def test_a_criterion_nobody_can_look_for_is_refused():
    problems = ed.validate_decision(_decision(
        evaluation_criteria=({"criterion": "it feels better"},)))
    assert any("names no measure" in problem for problem in problems), problems


# ── The compiler never invents the intent (EDIT-015) ───────────────────

def test_the_execution_layer_never_writes_the_effect():
    """EDIT-015: the request builder fills an absent effect with a
    generated sentence.  The decision compiler refuses instead."""
    program = {"kind": "edit_program", "id": "prog-1", "ops": [
        {"op": "clip.trim", "params": {"unique_id": "clip_001",
                                       "in_frame": 10, "out_frame": 40}}]}
    generated = edit_request.from_edit_program(program, project="p",
                                               timeline="reel")
    assert generated.operations[0].expected_effect.startswith("apply clip.trim")

    decision = _decision(
        intended_effect="",
        operations=(ed.DecisionOperation(
            op="clip.trim",
            params={"unique_id": "clip_001", "in_frame": 10, "out_frame": 40},
            effect=""),))
    graph = _graph(decision)
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "states no intended effect" in str(raised.value)
    assert "would have to invent one" in str(raised.value)


def test_an_operation_with_no_effect_inherits_the_decisions_own():
    """EDIT-015: the effect the request carries is the one somebody
    wrote down."""
    graph = _graph(_decision(operations=(ed.DecisionOperation(
        op="clip.trim",
        params={"unique_id": "clip_001", "in_frame": 10, "out_frame": 40},
        effect=""),)))
    request = ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                         intent=_intent())
    assert request.operations[0].expected_effect == \
        "the viewer hears the answer once"


def test_the_compiled_request_carries_the_decisions_own_effect():
    """EDIT-015: the effect the request carries is the one the decision
    stated."""
    graph = _graph(_decision())
    request = ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                         intent=_intent())
    assert request.operations[0].expected_effect == "the repeat is cut"


def test_the_compiled_request_names_the_decisions_it_came_from():
    graph = _graph(_decision())
    request = ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                         intent=_intent())
    assert "decision:1" in request.originator["detail"]


def test_a_request_whose_evidence_bar_is_unmet_does_not_compile():
    """EDIT-006 and EDIT-008: the bar is executable, not a note."""
    intent = _intent(requests=[{"id": "request:1",
                                 "statement": "remove the repetition"}])
    graph = _graph(_decision())
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=intent)
    assert "evidence bar is unmet" in str(raised.value)


def test_a_request_that_needs_clarification_does_not_compile():
    intent = _intent(requests=[{"id": "request:1",
                                 "statement": "remove the repetition",
                                 "evidence_lanes": ["m1"],
                                 "autonomy": ei.NEEDS_CLARIFICATION,
                                 "question": "which answer is the repeat?"}])
    graph = _graph(_decision())
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=intent)
    assert "needs clarification" in str(raised.value)


def test_a_request_needing_approval_compiles_only_with_the_approval():
    intent = _intent(requests=[{"id": "request:1",
                                 "statement": "remove the repetition",
                                 "evidence_lanes": ["m1"],
                                 "autonomy": ei.NEEDS_APPROVAL}])
    graph = _graph(_decision(approval=None))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=intent)
    assert "needs approval" in str(raised.value)


def test_a_decision_serving_a_request_the_intent_does_not_carry_is_refused():
    graph = _graph(_decision(request_id="request:absent"))
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.compile_to_edit_request(graph, project="p", timeline="reel",
                                   intent=_intent())
    assert "which the intent does not carry" in str(raised.value)


# ── The decision layer is a reader of the program, not a second path ──

def test_the_decision_compiler_builds_the_same_request_the_program_reader_builds():
    """EDIT-015: the decision layer reads the program the paths that
    already translate a request produce, and returns the same request -
    so it is a second READER, not a second execution path."""
    program = {"kind": "edit_program", "id": "prog-1",
               "idempotency_key": "prog-1", "base_generation": 12,
               "ops": [
                   {"op": "clip.trim", "text": "cut the repeated answer",
                    "params": {"unique_id": "clip_001", "in_frame": 10,
                               "out_frame": 40}},
                   {"op": "clip.set_enabled", "text": "hide the dead section",
                    "params": {"unique_id": "clip_002", "enabled": False}},
               ]}
    theirs = edit_request.from_edit_program(program, project="p",
                                             timeline="reel")
    mine = ed.request_from_program(
        program, project="p", timeline="reel", intent_id="intent:1",
        evidence_ids=["words:the price is forty"],
        criteria=[{"criterion": "the repeat is gone",
                   "measure": "the rendered transcript"}])
    assert [op.to_dict() for op in mine.operations] == [
        op.to_dict() for op in theirs.operations]
    assert mine.id == theirs.id
    assert mine.idempotency_key == theirs.idempotency_key
    assert mine.expected_base_generation == theirs.expected_base_generation


def test_a_program_with_no_evidence_cannot_become_a_request():
    """EDIT-010: the program names no source for the change."""
    program = {"kind": "edit_program", "ops": [
        {"op": "clip.trim", "text": "cut the repeated answer",
         "params": {"unique_id": "clip_001", "in_frame": 10, "out_frame": 40}}]}
    with pytest.raises(ed.EditorialDecisionError) as raised:
        ed.request_from_program(program, project="p", timeline="reel")
    assert "rests on no evidence" in str(raised.value)


# ── The graph's own questions ──────────────────────────────────────────

def test_the_graph_answers_what_depends_on_a_decision():
    graph = _graph(
        _decision(decision_id="decision:1"),
        _decision(decision_id="decision:2", depends_on=("decision:1",)),
        _decision(decision_id="decision:3", depends_on=("decision:2",)),
    )
    assert graph.dependents_of("decision:1") == ["decision:2", "decision:3"]
    assert graph.dependents_of("decision:3") == []


def test_the_decision_document_round_trips():
    graph = _graph(_decision())
    assert ed.DecisionGraph.from_dict(graph.to_dict()).to_dict() == \
        graph.to_dict()


def test_the_compiled_document_carries_the_decisions_beside_the_request():
    graph = _graph(_decision())
    document = ed.compile_document(graph, project="p", timeline="reel",
                                    intent=_intent())
    assert document["request"]["operations"][0]["expected_effect"] == \
        "the repeat is cut"
    assert document["decisions"][0]["decision_id"] == "decision:1"
