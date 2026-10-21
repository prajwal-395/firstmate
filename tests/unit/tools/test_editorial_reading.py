"""The reading half, measured on its own.

The defect this file exists for is a report that says "interpretation
5/8" and leaves the reader guessing which half of it was earned. The
stage folded reading and grounding together: eight of eight briefs were
read correctly while four of eleven target roles could be pointed at,
and the published number could not say so.

Two defects in particular are named here:

- a request that says "move it down" and a reading that says "move it so
  it stops colliding" are DIFFERENT readings, and before this the
  difference was invisible: a prose restatement has nowhere to put a
  direction, so the constraint the request stated was never measured.
- a run that committed the canonical intent could say anything about
  it, and the harness believed it. The intent's own contract is checked
  here instead.
"""

from __future__ import annotations

from library.tools.editorial_acceptance import reading


def _key(**overrides):
    key = {
        "edge_case": None,
        "expected_refusal": False,
        "expected_operations": ["overlay.position"],
        "expected_target_roles": ["name_lower_third"],
        "expected_moment_ids": [],
        "allowed_operations": ["overlay.position"],
        "footage_requirements": [],
        "stated_constraints": [],
        "role_premises": {},
        "notes": "",
    }
    key.update(overrides)
    return key


def _interpretation(**detail):
    base = {
        "status": "measured",
        "passed": True,
        "reason": None,
        "detail": {
            "restated_outcome": "Move the name bar so it stops colliding "
                                "with the captions.",
            "selected_operations": ["overlay.position"],
            "expected_operations": ["overlay.position"],
            "missing_operations": [],
            "unexpected_operations": [],
            "expected_target_roles": ["name_lower_third"],
            "unresolved_roles": [],
        },
    }
    base["detail"].update(detail)
    return base


def _record(**overrides):
    record = {
        "task_id": "b",
        "interpretation": {
            "restated_outcome": "Move the name bar so it stops colliding "
                                "with the captions.",
            "selected_operations": ["overlay.position"],
            "resolved_targets": [{"role": "name_lower_third",
                                  "resolved": True}],
        },
    }
    record.update(overrides)
    return record


def _direction_key():
    return _key(stated_constraints=[{
        "id": "direction-down",
        "terms": ["down"],
        "statement": "the name bar moves DOWN",
    }])


def test_a_reading_that_dropped_a_stated_constraint_is_named():
    """The defect: the request said "move it down" and the reading said
    "move it so it stops colliding", and the report called the reading
    perfect - because nothing could express a direction."""
    measured = reading.measure(_record(), _direction_key(),
                                _interpretation())
    assert measured["passed"] is True
    assert measured["constraints_passed"] is False
    assert measured["detail"]["constraints_dropped"] == ["direction-down"]
    assert "direction-down" in measured["constraint_reasons"][0]


def test_a_reading_that_carried_the_constraint_passes_it():
    """The connection: the constraint is the REQUESTER's own word, so a
    reading that kept it is measured as keeping it."""
    record = _record()
    record["interpretation"]["restated_outcome"] = (
        "Move the name bar down so it stops colliding with the captions.")
    measured = reading.measure(record, _direction_key(), _interpretation())
    assert measured["constraints_passed"] is True
    assert measured["detail"]["constraints_dropped"] == []


def test_a_declared_constraint_counts_even_when_the_restatement_is_thin():
    """The defect: the run restated the request in the engine's own
    vocabulary rather than in prose, and the constraint it carried was
    invisible because only the prose was read."""
    record = _record()
    record["interpretation"]["constraints_covered"] = ["direction-down"]
    measured = reading.measure(record, _direction_key(), _interpretation())
    assert measured["constraints_passed"] is True


def test_a_reading_that_misread_the_request_fails():
    """The defect: the run named an operation the request does not imply,
    and the reading was reported as sound."""
    measured = reading.measure(_record(), _key(),
                                _interpretation(unexpected_operations=[
                                    "clip.trim"]))
    assert measured["passed"] is False
    assert any("does not imply" in reason for reason in measured["reasons"])


def test_a_refusal_brief_that_planned_work_fails_the_reading():
    """The defect: the brief could not be obeyed, and the run planned an
    edit anyway - which is a reading failure, not only a planning one."""
    key = _key(expected_refusal=True, expected_operations=[],
               expected_target_roles=[], allowed_operations=[])
    measured = reading.measure(_record(), key, _interpretation())
    assert measured["passed"] is False
    assert any("report back" in reason for reason in measured["reasons"])


def test_a_committed_canonical_intent_is_measured_against_its_own_contract():
    """The defect: the run committed an intent that breaks the intent
    contract - a requirement nobody can check - and the harness believed
    the run's word that it had read the request."""
    record = _record()
    record["interpretation"]["intent"] = {
        "intent_id": "intent:1",
        "desired_outcome": "move the name bar down",
        "requirements": [{
            "declaration_id": "requirement:0",
            "statement": "it looks right",
            "kind": "requirement",
            "explicit": True,
            "objective": False,
        }],
        "requests": [],
    }
    measured = reading.measure(record, _key(), _interpretation())
    assert measured["passed"] is False
    assert measured["detail"]["intent_problems"]
    assert any("not objective" in problem
               for problem in measured["detail"]["intent_problems"])


def test_a_committed_canonical_intent_that_holds_is_reported_as_the_surface():
    """The connection: the run committed the canonical intent, so that
    is what the reading is read from, and the harness says so."""
    record = _record()
    record["interpretation"]["intent"] = {
        "intent_id": "intent:1",
        "desired_outcome": "move the name bar down",
        "requirements": [],
        "preferences": [],
        "inferred_preferences": [],
        "requests": [{"request_id": "request:0",
                      "kind": "revise_graphics",
                      "statement": "move the name bar down"}],
    }
    measured = reading.measure(record, _direction_key(), _interpretation())
    assert measured["passed"] is True
    assert measured["detail"]["intent_problems"] == []
    assert measured["detail"]["intent_request_kinds"] == ["revise_graphics"]
    assert "canonical intent" in measured["detail"]["reading_surface"]


def test_the_reading_view_counts_the_constraints_beside_the_reading():
    """The defect: one number over two measurements - whether the request
    was read and whether the reading carried what the request said -
    and the reader cannot tell which one moved."""
    rows = [
        {"brief_id": "a", **reading.measure(_record(), _key(),
                                            _interpretation())},
        {"brief_id": "b", **reading.measure(_record(), _direction_key(),
                                            _interpretation())},
    ]
    summary = reading.summarize(rows)
    assert summary["measured"] == 2
    assert summary["passed"] == 2
    assert summary["value"] == 1.0
    assert summary["constraints"]["carried"] == 1
    assert summary["constraints"]["dropped"] == 1
    assert summary["value_including_constraints"] == 0.5
    assert summary["constraints"]["dropped_detail"] == [
        {"brief_id": "b", "constraint_id": "direction-down"}]
