"""PLAN-011: the instruction loop accounts for every stated instruction.

Each test here names a DEFECT it would fail on.  A test that cannot
fail is worse than no test, because it reads as coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import (
    assignment_contract,
    assignment_lifecycle,
    editorial_intent,
    instruction_loop,
)

MODEL_FIXTURE = (Path(__file__).resolve().parents[3] / "native" / "ren-protocol"
                  / "fixtures" / "project-model.json")


def _project(tmp_path, requirements: list[dict] | None = None) -> str:
    """A scratch project with a task and a canonical intent on record."""
    from library.tools import project_model_store

    root = str(tmp_path)
    model = json.loads(MODEL_FIXTURE.read_text(encoding="utf-8"))
    project_model_store.commit_project_model(
        Path(root), model, expected_revision=0, reason="test")
    intent = editorial_intent.build_intent(
        intent_id="intent-test",
        desired_outcome="the thing that was asked for",
        editorial_spec={
            "requirements": requirements or [],
            "preferences": [], "platform": "", "audience": "",
            "target_duration": {}, "pacing": [], "subjects": {},
            "prohibited_content": [], "references": [],
            "unresolved_decisions": []},
        requests=[])
    editorial_intent.commit_intent(root, intent)
    record = assignment_lifecycle.create(root, "task-1")
    record["intent_id"] = "intent-test"
    record["request"] = "the thing that was asked for"
    assignment_lifecycle.save(root, record)
    return root


def _criterion(root: str, criterion_id: str, statement: str,
               instruction_id: str = "") -> None:
    assignment_contract.declare(root, "task-1", [
        {"id": criterion_id, "statement": statement, "kind": "editorial",
         "instruction_id": instruction_id}])


def test_a_stated_requirement_the_contract_never_carried_is_unaccounted(tmp_path):
    """The defect: the person asked for two things and one was dropped.

    Before PLAN-011 the task's contract said nothing about the second
    requirement, so the task read as finished with half the request
    unmentioned.  The account must name it.
    """
    root = _project(tmp_path,
                    requirements=[
                        {"id": "req:one", "statement": "the first thing"},
                        {"id": "req:two", "statement": "the second thing"}])
    _criterion(root, "one", "the first thing", "req:one")
    assignment_contract.record_evidence(
        root, "task-1", "one", status="passed", observation="it was checked")
    account = instruction_loop.account_assignment(root, "task-1")
    verdicts = {row["id"]: row["verdict"] for row in account["instructions"]}
    assert verdicts["req:one"] == "met", (
        "the requirement the criterion carries did not reach a verdict")
    assert verdicts["req:two"] == "unaccounted", (
        "the requirement the contract never carried read as accounted for; "
        "the dropped half of a request is exactly what this lane names")
    assert "req:two" in account["unaccounted"]
    assert account["counts"]["unaccounted"] == 1


def test_a_refusal_is_a_verdict_and_an_account(tmp_path):
    """The defect: a refusal read as a gap, so a task could never finish.

    "This could not be done, and here is why" is an answer.  An
    instruction that reached it is accounted for, and the account says
    the verdict was a refusal rather than a success.
    """
    root = _project(tmp_path,
                    requirements=[{"id": "req:one",
                                   "statement": "the thing asked for"}])
    _criterion(root, "one", "the thing asked for", "req:one")
    assignment_contract.record_evidence(
        root, "task-1", "one", status="failed",
        observation="the operation refused: the media is not a still")
    account = instruction_loop.account_assignment(root, "task-1")
    verdicts = {row["id"]: row["verdict"] for row in account["instructions"]}
    assert verdicts["req:one"] == "failed"
    assert account["counts"]["unaccounted"] == 0


def test_a_criterion_with_no_evidence_is_not_accounted(tmp_path):
    """The defect: a declared criterion read as satisfied by being declared.

    A criterion is satisfied by EVIDENCE.  Absent evidence is missing,
    never a pass - and a missing criterion is an instruction that reached
    no verdict.
    """
    root = _project(tmp_path)
    _criterion(root, "one", "the reel opens on the cost line")
    account = instruction_loop.account_assignment(root, "task-1")
    assert account["counts"]["unaccounted"] == 1
    assert account["instructions"][0]["reason"], (
        "an unaccounted instruction must say why it is unaccounted")


def test_a_reply_that_repeats_the_request_is_not_a_result():
    """The defect: the final reply restates the intention.

    A reply that repeats what was asked says nothing about what
    happened, however well written it is.
    """
    request = "Move the name bar down so it stops fighting the captions"
    result = "Set position on V6 name bar from 0.02 to 0.14 of the frame height."
    ok, reason = instruction_loop.states_actual_result(
        result, request=request,
        outcome={"state": "accepted", "summary": result})
    assert ok, reason
    bad, why = instruction_loop.states_actual_result(
        request, request=request, outcome={"state": "accepted"})
    assert not bad, "a reply that repeats the request read as a result"
    assert "repeats the request" in why


def test_a_reply_that_names_no_outcome_is_not_a_result():
    """The defect: a cheerful reply that says nothing about the outcome."""
    ok, reason = instruction_loop.states_actual_result(
        "All done, thanks for waiting.", request="tighten the opening",
        outcome={"state": "refused"})
    assert not ok, "a reply that names no outcome read as a result"
    assert "does not name" in reason


def test_the_report_states_the_result_and_names_what_is_unaccounted(tmp_path):
    """The defect: a report that reads as done while an instruction is open."""
    root = _project(tmp_path,
                    requirements=[
                        {"id": "req:one", "statement": "the first thing"},
                        {"id": "req:two", "statement": "the second thing"}])
    _criterion(root, "one", "the first thing", "req:one")
    assignment_contract.record_evidence(
        root, "task-1", "one", status="passed", observation="it was checked")
    report = instruction_loop.trace(root)["report"]
    assert "req:two" in report, (
        "the report did not name the instruction that reached no verdict")
    assert "unaccounted" in report


def test_a_preservation_instruction_is_told_from_a_change_one():
    """The defect: the account reads a preservation as a change.

    "The rest is untouched" and "the reel opens on the cost line" ask
    for different things, and the account's evidence for them is
    different.  The engine classifies them the way it classifies a
    request's kind: by the words.
    """
    assert instruction_loop.criterion_kind(
        "The grade and both cameras' framing survive the change."
    ) == instruction_loop.PRESERVATION
    assert instruction_loop.criterion_kind(
        "Every other clip on the reel is where it was."
    ) == instruction_loop.PRESERVATION
    assert instruction_loop.criterion_kind(
        "No clip on the timeline changed."
    ) == instruction_loop.PRESERVATION
    assert instruction_loop.criterion_kind(
        "The run reports that the project holds no screen recording to cut in."
    ) == instruction_loop.REPORT
    assert instruction_loop.criterion_kind(
        "The first line of the cut is Craig stating what it costs."
    ) == instruction_loop.CHANGE


def test_a_refused_record_still_reaches_a_verdict_for_every_instruction():
    """The defect: a run that refused is judged as one that said nothing.

    The corpus records of 2026-10-20 refused most briefs.  A refusal
    settles every stated instruction it covers, so none of them is left
    without a verdict.
    """
    record = {
        "brief_id": "name-bar-fights-the-captions",
        "planning": {"operations": [{"op": "overlay.position"}],
                     "requirements_covered": ["collision-gone"]},
        "execution": {"receipt": {"status": "refused"},
                      "checks": {}, "collateral_changes": [],
                      "refusal": {"reason": "the engine has no route to "
                                            "move this name bar"}},
    }
    brief = {"acceptance_criteria": [
        {"id": "collision-gone", "statement": "the name bar stops colliding"},
        {"id": "others-untouched",
         "statement": "Every other clip on the reel is where it was."}]}
    account = instruction_loop.account_record(record, brief)
    verdicts = {row["id"]: row["verdict"] for row in account["instructions"]}
    assert verdicts["collision-gone"] == "refused"
    assert verdicts["others-untouched"] == "refused", (
        "the instruction the plan never declared read as unaccounted; the "
        "refusal settled it")
    assert account["counts"]["unaccounted"] == 0


@pytest.mark.parametrize("stage", instruction_loop.STAGES)
def test_every_stage_of_the_loop_is_named_and_defined(stage):
    """The defect: a stage of the loop that nobody can state.

    The seven stages are the loop PLAN-011 names.  A stage with no
    definition cannot be read, and a stage the module does not know is a
    stage the check cannot report on.
    """
    assert stage in instruction_loop.STAGE_DEFINITIONS
    assert instruction_loop.STAGE_DEFINITIONS[stage].strip()
