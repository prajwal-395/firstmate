"""QA-008: final acceptance is an explicit state, and it is not a stage.

Each test here names a defect it would catch.  The defects, in order of
appearance:

* a task that executed cleanly read as a task that was EDITORIALLY
  finished, because nothing joined the legs into a state;
* a work that was technically valid and editorially refused read the
  same as a work that was broken;
* a check that could not run read as a check that passed;
* a task whose acceptance was refused still read as complete;
* the state a task ends in was not the state a reader of the record
  saw;
* a task in flight could be handed a verdict, which is a guess.
"""

from __future__ import annotations

import json

import pytest

from library.tools import (
    acceptance_state,
    assignment_contract,
    assignment_view,
    project_work_state,
)
from library.tools import (
    assignment_lifecycle as lifecycle,
)


def _task(root, aid="task-1", request="make two reels about pricing"):
    lifecycle.create(str(root), aid, brief=request, request=request,
                     kind="reels")
    return aid


def _deliver(root, aid):
    """Publish a clean delivery receipt, the way delivery does."""
    from library.tools import assignment_artifacts

    files = [{"name": "reel-01.mp4", "path": f"/tmp/{aid}/reel-01.mp4",
              "platform": None, "spec": None, "exists": True,
              "status": "delivered", "qc": {"passed": True}}]
    _path, receipt = assignment_artifacts.publish_delivery_receipt(
        str(root), assignment_id=aid,
        approved_version={"status": "approved", "assignment_id": aid,
                          "state": "delivered", "reels": [1]},
        files=files, qc={"per_file": {}},
        resume_point={"action": "none", "command": None, "why": ""})
    return receipt


def _delivered(root, aid):
    """Walk a task to the DELIVERED state, the way the lifecycle does."""
    for state in ("draft", "review", "approved", "delivered"):
        lifecycle.transition(str(root), aid, state)
    return aid


def _facts(**overrides):
    """Every fact established, so the legs that apply are the ones asked for."""
    facts = {
        "applied_mutations": True,
        "has_timeline": True,
        "has_approved_look": True,
        "has_delivered_file": True,
        "has_captions": True,
        "requires_approval": True,
    }
    facts.update(overrides)
    return facts


def _passing(legs):
    return {leg: {"status": "passed", "observation": "it passed"}
            for leg in legs}


ALL_LEGS = list(acceptance_state.LEG_IDS)


def test_every_applicable_leg_must_pass_before_the_work_is_accepted():
    """One leg short of a pass is not an acceptance."""
    results = _passing(ALL_LEGS)
    results["delivered_audio"] = {"status": "failed",
                                  "observation": "the file is 6 dB quiet"}
    verdict = acceptance_state.evaluate(_facts(), results)
    assert verdict["outcome"] == acceptance_state.TECHNICALLY_FAILED
    assert verdict["state"] == acceptance_state.STATE_REJECTED
    assert verdict["counts"]["passed"] == len(ALL_LEGS) - 1
    assert verdict["counts"]["failed"] == 1
    assert verdict["counts"]["total"] == len(ALL_LEGS)


def test_technically_valid_but_editorially_rejected_is_not_a_failure():
    """The distinction the punch list asks for, kept apart.

    Every technical leg passed and the editorial review refused the
    work.  That is a REJECTION, not a defect: the two have different
    fixes, and folding them together loses the difference.
    """
    results = _passing(ALL_LEGS)
    results["editorial_review"] = {
        "status": "failed",
        "observation": "the review read the file and refused the pacing"}
    verdict = acceptance_state.evaluate(_facts(), results)
    assert verdict["outcome"] == acceptance_state.EDITORIALLY_REJECTED
    assert verdict["outcome"] != acceptance_state.TECHNICALLY_FAILED
    assert verdict["counts"]["failed"] == 1
    assert "technically" not in verdict["reason"].split("(")[0]


def test_a_check_that_could_not_run_is_unmeasured_and_never_a_pass():
    """An incomplete check is recorded as unmeasured."""
    results = _passing(ALL_LEGS)
    results["delivered_captions"] = {
        "status": "unmeasured",
        "observation": "no caption intent was supplied, so the captions "
                       "had nothing to be compared against"}
    verdict = acceptance_state.evaluate(_facts(), results)
    assert verdict["outcome"] == acceptance_state.OUTCOME_UNMEASURED
    assert verdict["counts"]["unmeasured"] == 1
    assert verdict["counts"]["passed"] == len(ALL_LEGS) - 1
    assert verdict["state"] == acceptance_state.STATE_REJECTED


def test_a_leg_whose_applicability_nobody_established_is_unmeasured():
    """An unknown fact is not an inapplicable leg.

    A leg nobody could say whether it applied to this task is a leg
    nobody checked, and reading it as inapplicable would let a task be
    accepted on the strength of a check that never ran.
    """
    facts = _facts()
    facts["has_delivered_file"] = None
    results = _passing(ALL_LEGS)
    verdict = acceptance_state.evaluate(facts, results)
    assert verdict["outcome"] == acceptance_state.OUTCOME_UNMEASURED
    unmeasured = [row for row in verdict["legs"]
                  if row["status"] == acceptance_state.UNMEASURED]
    assert {row["id"] for row in unmeasured} == {
        "delivered_audio", "delivered_picture", "editorial_review"}
    assert {row["id"] for row in verdict["legs"]
            if row["status"] == acceptance_state.NOT_APPLICABLE} == set()


def test_a_leg_that_does_not_apply_is_recorded_with_the_fact_that_said_so():
    """Inapplicability is a reading, not a deletion."""
    facts = _facts(has_captions=False, requires_approval=False)
    results = _passing(["operation_pre_post", "timeline_structure",
                        "look_preservation", "delivered_audio",
                        "delivered_picture", "editorial_review"])
    verdict = acceptance_state.evaluate(facts, results)
    assert verdict["outcome"] == acceptance_state.ACCEPTED
    assert verdict["counts"]["not_applicable"] == 2
    assert verdict["counts"]["applicable"] == len(ALL_LEGS) - 2
    for row in verdict["legs"]:
        if row["status"] == acceptance_state.NOT_APPLICABLE:
            assert row["observation"]


def test_a_verdict_with_no_reason_is_refused():
    """A result with no observation is a verdict nobody can check."""
    results = _passing(ALL_LEGS)
    results["delivered_picture"] = {"status": "passed", "observation": ""}
    with pytest.raises(acceptance_state.AcceptanceError):
        acceptance_state.evaluate(_facts(), results)


def test_the_task_state_says_accepted_and_the_record_carries_the_legs(tmp_path):
    """The state a reader sees and the record a reader reads agree."""
    aid = _task(tmp_path)
    _delivered(tmp_path, aid)
    verdict = acceptance_state.record(
        str(tmp_path), aid, facts=_facts(),
        results=_passing(ALL_LEGS))
    assert verdict["outcome"] == acceptance_state.ACCEPTED

    stored = lifecycle.load(str(tmp_path), aid)
    assert stored["state"] == "accepted"
    final = stored["acceptance"]["final"]
    assert final["outcome"] == acceptance_state.ACCEPTED
    assert {row["id"] for row in final["legs"]} == set(ALL_LEGS)
    assert final["counts"]["applicable"] == len(ALL_LEGS)
    assert acceptance_state.read(str(tmp_path), aid)["outcome"] == \
        acceptance_state.ACCEPTED


def test_a_refused_acceptance_is_its_own_state_and_not_a_completion(tmp_path):
    """Technically valid, editorially refused: rejected, and not complete."""
    aid = _task(tmp_path)
    _delivered(tmp_path, aid)
    _deliver(tmp_path, aid)
    assignment_contract.declare(str(tmp_path), aid, [
        {"id": "reel-01-exists", "statement": "the reel exists",
         "kind": "technical"}])
    assignment_contract.record_evidence(
        str(tmp_path), aid, "reel-01-exists", status="passed",
        observation="the file is on disk")
    results = _passing(ALL_LEGS)
    results["editorial_review"] = {
        "status": "failed",
        "observation": "the review read the file and refused the pacing"}
    acceptance_state.record(str(tmp_path), aid, facts=_facts(),
                            results=results)

    stored = lifecycle.load(str(tmp_path), aid)
    assert stored["state"] == "rejected"
    final = stored["acceptance"]["final"]
    assert final["outcome"] == acceptance_state.EDITORIALLY_REJECTED
    # Every technical leg passed: the work was not broken, it was refused.
    technical = [row for row in final["legs"]
                 if row["kind"] == acceptance_state.TECHNICAL]
    assert all(row["status"] == acceptance_state.PASSED
               for row in technical)

    completion = assignment_contract.completion(str(tmp_path), aid)
    assert completion["complete"] is False
    assert any("final acceptance" in blocker
               for blocker in completion["blockers"])


def test_a_task_whose_acceptance_was_not_evaluated_is_not_a_pass(tmp_path):
    """No verdict is not a verdict, and the reader is told so."""
    aid = _task(tmp_path)
    _delivered(tmp_path, aid)
    assert acceptance_state.read(str(tmp_path), aid) is None
    completion = assignment_contract.completion(str(tmp_path), aid)
    assert completion["acceptance"] is None


def test_a_task_in_flight_cannot_be_handed_a_verdict(tmp_path):
    """A verdict on work in flight is a guess."""
    aid = _task(tmp_path)
    with pytest.raises(acceptance_state.AcceptanceError):
        acceptance_state.record(str(tmp_path), aid, facts=_facts(),
                                results=_passing(ALL_LEGS))


def test_the_work_state_carries_the_acceptance_and_keeps_a_refusal_open(
        tmp_path):
    """`ren work-state` shows the acceptance, and a refusal is open work."""
    project = tmp_path / "show"
    project.mkdir()
    (project / "project.yaml").write_text(
        'name: "Show"\nslug: show\n', encoding="utf-8")
    aid = _task(project)
    _delivered(project, aid)
    results = _passing(ALL_LEGS)
    results["editorial_review"] = {
        "status": "failed",
        "observation": "the review read the file and refused the pacing"}
    acceptance_state.record(str(project), aid, facts=_facts(),
                            results=results)
    state = project_work_state.project_work_state(str(project))
    assignment = next(row for row in state["assignments"]
                      if row["assignment_id"] == aid)
    assert assignment["acceptance"]["outcome"] == \
        acceptance_state.EDITORIALLY_REJECTED
    kinds = {row["kind"] for row in state["open_work"]}
    assert "acceptance" in kinds


def test_the_acceptance_state_is_read_by_the_task_view(tmp_path):
    """The view a host reads carries the acceptance verdict."""
    aid = _task(tmp_path)
    _delivered(tmp_path, aid)
    acceptance_state.record(str(tmp_path), aid, facts=_facts(),
                            results=_passing(ALL_LEGS))
    view = assignment_view.task_state(str(tmp_path), aid)
    assert view["completed"]["acceptance"]["outcome"] == \
        acceptance_state.ACCEPTED
    assert view["state"] == "accepted"


def test_the_acceptance_document_is_what_the_verb_reads(tmp_path):
    """`ren assignment --accept` records what the document says."""
    from library.tools import assignment_interaction

    aid = _task(tmp_path)
    _delivered(tmp_path, aid)
    document = tmp_path / "acceptance.json"
    document.write_text(json.dumps({
        "facts": _facts(),
        "results": _passing(ALL_LEGS),
    }), encoding="utf-8")
    envelope = assignment_interaction.main(
        [str(tmp_path), "--id", aid, "--accept", str(document)])
    assert envelope == 0
    stored = lifecycle.load(str(tmp_path), aid)
    assert stored["state"] == "accepted"
    assert stored["acceptance"]["final"]["outcome"] == \
        acceptance_state.ACCEPTED


def test_the_engine_own_measurements_are_read_as_leg_results():
    """The legs are the engine's own measurements, not a second opinion."""
    from library.tools import operation_acceptance

    contract = operation_acceptance.evaluate(
        {"op": "clip.trim", "acceptance_op": "clip.trim"},
        readback_passed=True, evidence={})
    # A trim with no evidence for its own contract is missing evidence,
    # so the leg is unmeasured - the same rule AXI-013 follows.
    result = acceptance_state.operation_result([
        {"op": "clip.trim", "readback_passed": True}])
    assert result["status"] == acceptance_state.UNMEASURED
    assert contract["missing"]
