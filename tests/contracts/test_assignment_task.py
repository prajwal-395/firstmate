"""The task object: one read of a request, from the request to the delivery.

Each test here names a defect it would catch. The eight E2E items this
file establishes are, in order of appearance:

* E2E-001 one task object linking request, project state, evidence, plan,
  execution, verification and delivery;
* E2E-002 an explicit acceptance contract on every task;
* E2E-003 source evidence and editorial decisions carried into execution
  without losing provenance;
* E2E-004 dependencies and grouped operations within a task;
* E2E-005 pause, review, resume, replan, cancel and safe recovery;
* E2E-006 a refusal for an unsupported execution told apart from a
  refusal for missing editorial information;
* E2E-007 the smallest actionable blocker, not the whole workflow;
* E2E-008 a task never reported complete because a plan compiled or a
  timeline mutation was submitted.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import (
    assignment_actions,
    assignment_contract,
    assignment_interaction,
    assignment_plan,
    assignment_refusal,
    assignment_spine,
    assignment_task,
    operations,
)
from library.tools import assignment_lifecycle as lifecycle
from library.tools import assignment_provenance as provenance


def _task(root, aid="task-1", request="make two reels about pricing",
          kind="reels"):
    lifecycle.create(str(root), aid, brief=request, request=request, kind=kind)
    return aid


def _deliver(root, aid, *, status="complete", missing=(), failed=()):
    """Publish a delivery receipt for the task, the way delivery does.

    The RECEIPT is the truth about the files, so the gate reads the
    receipts themselves rather than a copy somebody kept.
    """
    from library.tools import assignment_artifacts

    files = [{"name": "reel-01.mp4", "path": f"/tmp/{aid}/reel-01.mp4",
              "platform": None, "spec": None, "exists": True,
              "status": "delivered", "qc": {"passed": status == "complete"}}]
    for name in missing:
        files.append({"name": name, "path": f"/tmp/{aid}/{name}",
                      "platform": None, "spec": None, "exists": False,
                      "status": "missing", "qc": None})
    for name in failed:
        files.append({"name": name, "path": f"/tmp/{aid}/{name}",
                      "platform": None, "spec": None, "exists": True,
                      "status": "failed", "qc": {"passed": False}})
    _path, receipt = assignment_artifacts.publish_delivery_receipt(
        str(root), assignment_id=aid,
        approved_version={"status": "approved", "assignment_id": aid,
                          "state": "delivered", "reels": [1]},
        files=files, qc={"per_file": {}},
        resume_point={"action": "none", "command": None, "why": ""})
    return receipt


# ── E2E-001: one task object ─────────────────────────────────────────


def test_the_task_object_links_the_request_to_its_delivery(tmp_path):
    """A task is ONE object: request, state, evidence, plan, execution,
    verification and delivery are all in the same read."""
    root = str(tmp_path)
    aid = _task(root)
    provenance.record_evidence(
        root, aid, evidence_id="ev-1", kind="footage", source="footage_query",
        locator="clip:abc@12.0-18.0", sha256="deadbeef")
    provenance.record_decision(
        root, aid, decision_id="dec-1", question="which clip carries the price?",
        answer="the second take", evidence_ids=["ev-1"])
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.catalog",
                        evidence_ids=["ev-1"], decision_ids=["dec-1"])
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])
    assignment_contract.record_evidence(
        root, aid, "c1", status="passed", observation="both reels are on disk")
    for state in ("draft", "review", "approved", "delivered"):
        lifecycle.transition(root, aid, state, event=f"to-{state}")
    _deliver(root, aid)

    # The RECORD is the task object: it links the request, the project
    # state, the evidence, the decisions, the plan, the execution, the
    # acceptance contract and the delivery, in one read.
    record = lifecycle.load(root, aid)

    assert record["assignment_id"] == aid
    assert record["request"] == "make two reels about pricing"
    assert record["state"] == "delivered"
    assert record["project_state"] is None
    assert [item["id"] for item in record["evidence"]] == ["ev-1"]
    assert [item["id"] for item in record["decisions"]] == ["dec-1"]
    assert list(record["plan"]["operations"]) == ["n1"]
    assert record["delivery"]["status"] == "complete"
    assert record["history"]

    # And the READ answers what the record says, in one projection.
    from library.tools import assignment_view

    state = assignment_view.task_state(root, aid)
    assert state["assignment_id"] == aid
    assert state["completed"]["complete"] is True
    assert state["blocking"]["kind"] == "none"


def test_a_record_written_before_the_task_sections_reads_with_all_of_them(
        tmp_path):
    """A record from before the task sections existed is still a task:
    the sections it lacks are EMPTY, never invented, and the reader sees
    the same keys either way."""
    root = str(tmp_path)
    aid = _task(root)
    path = lifecycle._path(root, aid)
    stored = json.loads(path.read_text(encoding="utf-8"))
    for key in lifecycle.TASK_SECTIONS:
        stored.pop(key, None)
    path.write_text(json.dumps(stored), encoding="utf-8")

    record = lifecycle.load(root, aid)

    for key, default in lifecycle.TASK_SECTIONS.items():
        assert key in record
        if default is not None:
            assert record[key] == default


def test_the_task_carries_the_project_state_it_acts_on(tmp_path):
    """The link is not decoration: the state the task acts on is
    recorded, and a piece that cannot be measured reads as not
    measured."""
    root = str(tmp_path)
    aid = _task(root)
    (tmp_path / "pipeline_data.json").write_text(
        json.dumps({"total_clips": 7}), encoding="utf-8")

    captured = assignment_task.capture_project_state(root, aid)

    assert captured["clips"] == 7
    assert captured["project"] == str(Path(root).resolve())
    assert lifecycle.load(root, aid)["project_state"]["clips"] == 7


def test_a_capture_that_fails_is_recorded_and_never_fails_the_act(
        tmp_path, monkeypatch):
    """A reading that cannot be taken must not fail the act that was
    reading; the failure belongs on the task, where a reader sees it."""
    root = str(tmp_path)
    aid = _task(root)

    def refuse(*_args, **_kwargs):
        raise OSError("the project state cannot be read")

    monkeypatch.setattr(assignment_task, "capture_project_state", refuse)
    assignment_spine._capture_state(root, lifecycle.load(root, aid))

    history = lifecycle.load(root, aid)["history"]
    assert [entry["event"] for entry in history] == [
        "project_state_capture_failed"]


# ── E2E-002: the acceptance contract ─────────────────────────────────


def test_a_criterion_cannot_be_rewritten_after_the_work_started(tmp_path):
    """The contract is a promise. A criterion that changed its meaning
    after the work began is not a contract any more."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])

    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])

    with pytest.raises(ValueError, match="already declared"):
        assignment_contract.declare(
            root, aid, [{"id": "c1", "statement": "three reels exist",
                         "kind": "technical"}])


def test_a_criterion_with_no_evidence_is_missing_and_never_a_pass(tmp_path):
    """Absent evidence is missing, not a pass: the same rule a single
    operation's acceptance contract follows."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])

    evaluated = assignment_contract.evaluate(root, aid)

    assert evaluated["status"] == "incomplete"
    assert "c1" in evaluated["missing"]
    assert "c1" not in evaluated["passed"]
    assert evaluated["criteria"][0]["status"] == "missing"


def test_a_criterion_needs_an_observation_and_not_merely_a_verdict(tmp_path):
    """A criterion with a verdict and no observation is a verdict nobody
    can check."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])

    with pytest.raises(ValueError, match="what was observed"):
        assignment_contract.record_evidence(root, aid, "c1", status="passed",
                                            observation="  ")


# ── E2E-003: provenance ───────────────────────────────────────────────


def test_evidence_without_a_locator_is_refused(tmp_path):
    """Evidence is a CITED thing. A claim with no locator is not
    evidence, and the record says so rather than storing it."""
    root = str(tmp_path)
    aid = _task(root)

    with pytest.raises(ValueError, match="locator"):
        provenance.record_evidence(
            root, aid, evidence_id="ev-1", kind="footage",
            source="footage_query", locator="")


def test_a_decision_resting_on_evidence_nobody_recorded_is_refused(tmp_path):
    """A broken link is worse than no link."""
    root = str(tmp_path)
    aid = _task(root)

    with pytest.raises(ValueError, match="never recorded"):
        provenance.record_decision(
            root, aid, decision_id="dec-1", question="which clip?",
            answer="the second take", evidence_ids=["ev-1"])


def test_the_provenance_reaches_the_operations_checkpoint(tmp_path,
                                                          monkeypatch):
    """The evidence and the decision ride into execution, so the chain
    from source to choice to edit survives the run that made it."""
    root = str(tmp_path)
    aid = _task(root)
    provenance.record_evidence(
        root, aid, evidence_id="ev-1", kind="footage", source="footage_query",
        locator="clip:abc@12.0-18.0", sha256="deadbeef")
    provenance.record_decision(
        root, aid, decision_id="dec-1", question="which clip?",
        answer="the second take", evidence_ids=["ev-1"])
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.scan",
                        evidence_ids=["ev-1"], decision_ids=["dec-1"])
    monkeypatch.setattr(operations, "get", _fake_operation)

    envelope = assignment_interaction.structured_transport({
        "assignment_id": aid, "operation_id": "footage.scan",
        "project": root, "inputs": {}, "node_id": "n1"})

    assert envelope["status"] == "completed"
    assert envelope["provenance"]["evidence"][0]["id"] == "ev-1"
    assert envelope["provenance"]["evidence"][0]["locator"] == "clip:abc@12.0-18.0"
    assert envelope["provenance"]["decisions"][0]["id"] == "dec-1"
    checkpoint = lifecycle.load(root, aid)["operations"]
    stored = next(iter(checkpoint.values()))
    assert stored["request"]["provenance"]["evidence"][0]["id"] == "ev-1"


# ── E2E-004: dependencies and groups ─────────────────────────────────


def test_a_node_waiting_on_a_dependency_is_not_ready(tmp_path):
    """Dependencies order the work: a node whose dependency has not
    completed is not ready, and `ready` says which one is."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.catalog")
    assignment_plan.add(root, aid, node_id="n2", operation_id="footage.scan",
                        depends_on=["n1"])

    assert [node["node_id"] for node in assignment_plan.ready(root, aid)] == ["n1"]
    assert assignment_plan.blocked_by(root, aid) == {"n2": ["n1"]}

    assignment_plan.mark(root, aid, "n1", "completed")

    assert [node["node_id"] for node in assignment_plan.ready(root, aid)] == ["n2"]
    assert assignment_plan.blocked_by(root, aid) == {}


def test_a_dependency_cycle_is_refused(tmp_path):
    """A cycle makes "ready" meaningless, so it never gets to exist."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.catalog",
                        depends_on=["n2"])

    with pytest.raises(ValueError, match="cycle"):
        assignment_plan.add(root, aid, node_id="n2",
                            operation_id="footage.scan", depends_on=["n1"])


def test_a_group_is_derived_from_the_nodes_that_name_it(tmp_path):
    """A node's group and the group it appears in cannot disagree: the
    group is read off the nodes."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.catalog",
                        group="opening")
    assignment_plan.add(root, aid, node_id="n2", operation_id="footage.scan",
                        group="opening")
    assignment_plan.add(root, aid, node_id="n3", operation_id="temporal.index")

    grouped = assignment_plan.groups(root, aid)

    assert set(grouped) == {"opening"}
    assert grouped["opening"]["node_ids"] == ["n1", "n2"]
    assert grouped["opening"]["status"] == "planned"
    assignment_plan.mark(root, aid, "n1", "completed")
    assignment_plan.mark(root, aid, "n2", "completed")
    assert assignment_plan.groups(root, aid)["opening"]["status"] == "completed"


# ── E2E-005: pause, replan, cancel ───────────────────────────────────


def test_a_paused_task_accepts_no_act(tmp_path):
    """The task's own handbrake: the work stays where it is, and the
    refusal says how to release it."""
    root = str(tmp_path)
    aid = _task(root, kind="edit")
    assignment_actions.pause(root, aid, reason="the captain is thinking")

    with pytest.raises(Exception) as held:
        assignment_spine.begin_edit(root)

    refusal = held.value
    assert getattr(refusal, "code", "") == "REN_ASSIGNMENT_PAUSED"
    assert "--resume" in getattr(refusal, "fix", "")
    assert lifecycle.load(root, aid)["state"] == "brief"

    assignment_actions.resume(root, aid)
    assert assignment_spine.begin_edit(root) is not None


def test_a_replan_keeps_the_work_that_happened_and_drops_the_rest(tmp_path):
    """A node that completed is KEPT - its work happened. The nodes that
    have not run are replaced, and the reason stays on the record."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.catalog")
    assignment_plan.add(root, aid, node_id="n2", operation_id="footage.scan")
    assignment_plan.mark(root, aid, "n1", "completed")

    assignment_actions.replan(root, aid, reason="the brief changed",
                              nodes=[{"node_id": "n3",
                                      "operation_id": "temporal.index"}])

    planned = assignment_plan.plan(root, aid)["operations"]
    assert set(planned) == {"n1", "n3"}
    assert planned["n1"]["status"] == "completed"
    assert planned["n3"]["status"] == "planned"
    history = lifecycle.load(root, aid)["history"]
    replanned = [entry for entry in history if entry["event"] == "replan"]
    assert len(replanned) == 1
    assert replanned[0]["reason"] == "the brief changed"
    assert replanned[0]["dropped"] == ["n2"]


def test_cancelling_ends_the_task_and_reopening_starts_it_again(tmp_path):
    """Cancel does not undo what was delivered; it ends the task and
    keeps everything that happened."""
    root = str(tmp_path)
    aid = _task(root)
    lifecycle.transition(root, aid, "draft")

    assignment_actions.cancel(root, aid, reason="the captain changed their mind")

    record = lifecycle.load(root, aid)
    assert record["state"] == "cancelled"
    assert [entry["event"] for entry in record["history"]][-1] == "cancel"
    assert assignment_spine.current(root) is None

    assignment_actions.reopen(root, aid)
    assert lifecycle.load(root, aid)["state"] == "brief"


# ── E2E-006: which kind of refusal this is ──────────────────────────


def test_a_missing_judgement_is_not_the_same_refusal_as_a_missing_capability():
    """A host that cannot tell them apart either gives up on a question
    it could have asked, or keeps asking about a capability that does
    not exist. Both are read from the STRUCTURE, never from the prose."""
    from library.tools import transaction_orchestration

    missing_judgement = assignment_refusal.classify(
        SimpleNamespace(refusal_kind="missing_editorial_information",
                        refusal_reason="the model owes this step an answer",
                        error="", fix="ask the model", unsatisfied=()))
    unsupported = assignment_refusal.classify(
        transaction_orchestration.ExecutionRefusal(
            "unsupported", "no eligible production route"))

    assert missing_judgement["kind"] == "missing_editorial_information"
    assert missing_judgement["recovery_class"] == "needs_question"
    assert unsupported["kind"] == "unsupported_execution"
    assert unsupported["recovery_class"] == "blocked"
    assert missing_judgement["kind"] != unsupported["kind"]


def test_a_refused_operation_carries_its_kind_on_the_envelope(tmp_path,
                                                              monkeypatch):
    """The refusal the host reads says WHICH kind it is, so the host
    knows whether to ask or to stop."""
    root = str(tmp_path)
    aid = _task(root)
    monkeypatch.setattr(operations, "get", _fake_operation)

    envelope = assignment_interaction.structured_transport({
        "assignment_id": aid, "operation_id": "footage.scan",
        "project": root, "inputs": {"refuse": True}})

    assert envelope["status"] == "refused"
    assert envelope["refusal"]["kind"] == "missing_editorial_information"
    assert lifecycle.load(root, aid)["last_refusal"]["kind"] == \
        "missing_editorial_information"


def test_an_unsupported_execution_is_not_a_missing_answer():
    """A refusal that says the operation has no eligible route is a
    capability gap; a refusal that says a judgement is missing is a
    question. The host must not keep asking about a capability that
    does not exist."""
    from library.tools import transaction_orchestration

    unsupported = transaction_orchestration.ExecutionRefusal(
        "unsupported", "no eligible production route for clip.ripple")

    classified = assignment_refusal.classify(unsupported)

    assert classified["kind"] == "unsupported_execution"
    assert classified["recovery_class"] == "blocked"


def test_a_refusal_that_names_a_conflict_class_keeps_that_class():
    """`edit_conflict` owns the other axis - the four ways an edit fails
    to land - and a refusal that carries a class has already been
    classified on it. The kind says the same thing in the task's
    vocabulary, and the class travels on for the operator."""
    from library.tools import edit_conflict

    refusal = {"code": "REN_STALE_PATCH", "conflict_class": "stale_plan",
               "what": "the plan was made against an older revision",
               "fix": "rebase or replan"}

    classified = assignment_refusal.classify(refusal)

    assert classified["kind"] == "stale_state"
    assert classified["conflict_class"] == "stale_plan"
    assert classified["recovery_class"] == "auto_recoverable"
    assert "stale_plan" in edit_conflict.CONFLICT_CLASSES


# ── E2E-007: the smallest actionable blocker ─────────────────────────


def test_the_blocker_names_one_dependency_and_not_the_whole_plan(tmp_path):
    """The blocker is the smallest thing in the way."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_plan.add(root, aid, node_id="n1", operation_id="footage.catalog")
    assignment_plan.add(root, aid, node_id="n2", operation_id="footage.scan",
                        depends_on=["n1"])
    assignment_plan.add(root, aid, node_id="n3", operation_id="temporal.index",
                        depends_on=["n2"])

    blocker = assignment_task.blocker(root, aid)

    assert blocker["kind"] == "dependency"
    assert blocker["needs"] == ["n1 (operation footage.catalog)"]
    assert blocker["node_id"] == "n2"


def test_the_blocker_names_the_criterion_that_lacks_evidence(tmp_path):
    """A task waiting on evidence says WHICH criterion, so the host can
    go and measure that one thing."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])

    blocker = assignment_task.blocker(root, aid)

    assert blocker["kind"] == "acceptance"
    assert blocker["needs"] == ["c1"]


# ── E2E-008: the completion gate ─────────────────────────────────────


def test_a_compiled_plan_is_not_a_complete_task(tmp_path):
    """A plan that compiled is a stage on the way, not the end."""
    root = str(tmp_path)
    aid = _task(root)
    lifecycle.transition(root, aid, "draft")

    completion = assignment_contract.completion(root, aid)

    assert completion["complete"] is False
    assert completion["stage"] == "PLANNED"
    assert any("not delivered" in blocker for blocker in completion["blockers"])


def test_a_written_timeline_is_not_a_complete_task(tmp_path):
    """A timeline that was written is a stage on the way, not the end."""
    root = str(tmp_path)
    aid = _task(root)
    for state in ("draft", "review"):
        lifecycle.transition(root, aid, state)

    completion = assignment_contract.completion(root, aid)

    assert completion["complete"] is False
    assert completion["stage"] == "EXECUTED"


def test_a_delivered_task_with_no_evidence_is_not_complete(tmp_path):
    """Delivered is not the same as done: the acceptance contract still
    has to be evidenced."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])
    for state in ("draft", "review", "approved", "delivered"):
        lifecycle.transition(root, aid, state)
    _deliver(root, aid, status="complete")

    completion = assignment_contract.completion(root, aid)

    assert completion["complete"] is False
    assert completion["stage"] == "DELIVERED"
    assert any("no evidence" in blocker for blocker in completion["blockers"])


def test_a_task_with_no_acceptance_contract_is_not_complete(tmp_path):
    """The task never said what done means, so nothing can say it is
    done: the contract is what makes the claim checkable."""
    root = str(tmp_path)
    aid = _task(root)
    for state in ("draft", "review", "approved", "delivered"):
        lifecycle.transition(root, aid, state)
    _deliver(root, aid, status="complete")

    completion = assignment_contract.completion(root, aid)

    assert completion["complete"] is False
    assert any("no acceptance contract" in blocker
               for blocker in completion["blockers"])
    assert assignment_task.blocker(root, aid)["action"] == "declare_contract"


def test_a_delivered_task_with_evidence_and_a_clean_receipt_is_complete(
        tmp_path):
    """The one claim of done-ness: delivered, every criterion evidenced,
    and a receipt with nothing missing and nothing failed."""
    root = str(tmp_path)
    aid = _task(root)
    assignment_contract.declare(
        root, aid, [{"id": "c1", "statement": "two reels exist",
                     "kind": "technical"}])
    assignment_contract.record_evidence(
        root, aid, "c1", status="passed", observation="both reels are on disk")
    for state in ("draft", "review", "approved", "delivered"):
        lifecycle.transition(root, aid, state)
    _deliver(root, aid, status="complete")

    completion = assignment_contract.completion(root, aid)

    assert completion["complete"] is True
    assert completion["stage"] == "DELIVERED"
    assert completion["blockers"] == []
    assert assignment_task.blocker(root, aid)["kind"] == "none"


def test_a_delivery_with_a_missing_file_is_not_a_complete_task(tmp_path):
    """The receipt's own failures are the task's failures."""
    root = str(tmp_path)
    aid = _task(root)
    for state in ("draft", "review", "approved", "delivered"):
        lifecycle.transition(root, aid, state)
    _deliver(root, aid, status="incomplete", missing=["reel-02.mp4"])

    completion = assignment_contract.completion(root, aid)

    assert completion["complete"] is False
    assert any("delivery receipt" in blocker
               for blocker in completion["blockers"])


# ── The fake operation the interaction tests run ─────────────────────


def _fake_operation(name):
    """An operation that completes, or refuses when the caller says so."""

    class Operation:
        def __init__(self, name):
            self.name = name

        def execute(self, project, **inputs):
            if inputs.get("refuse"):
                refusal_reason = lambda: "footage.catalog refused: fixture"
                return SimpleNamespace(
                    refused=True, operation=self.name,
                    error="fixture refusal", refusal_reason=refusal_reason,
                    unsatisfied=(), payload=None, fix="answer the question",
                    refusal_kind="missing_editorial_information")
            return SimpleNamespace(
                refused=False, operation=self.name, error="",
                unsatisfied=(), refusal_kind="",
                payload={"clips": 1})

    return Operation(name)
