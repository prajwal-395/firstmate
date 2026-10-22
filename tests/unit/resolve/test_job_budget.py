"""A broker job carries a DECLARED BUDGET, and a job that overruns it
is refused with a typed receipt.

THE DEFECT (measured 2026-10-21, the machine's own broker, 24 h,
4,449 jobs): a broker job had NO upper bound on its run. The instance
lease's own timeout bounds the WAIT for the machine, never the HOLD of
it, so a Resolve call that blocks held the one Resolve and the whole
queue for as long as it blocked - 15 minutes for one `project scratch
open`, and a wedged call that only a lane could end by killing the
worker's process by hand (pid 33974).

These are the defects this could reintroduce, each named:

1. a job kind with NO budget of its own would hold the machine for
   ever - every kind declares one, or the default does;
2. a CALLER's declared budget would be ignored (the kind's budget would
   be used instead);
3. a declared budget that is not a budget (zero, negative, a
   boolean, a string, above the ceiling) would be half-honoured
   instead of refused at the submission gate;
4. a job that overran its budget would be reported as a RESULT - the
   caller would read a machine that was held too long as work that
   landed;
5. the refusal would not be TYPED - no kind of block, no next action,
   nothing for a host to act on;
6. a job that stopped at a committed checkpoint would lose its own
   result - the write may have landed, and a refusal that dropped the
   receipt would lose the only record of it;
7. a job would keep running past its budget at the checkpoints it
   passes, so the machine would be held for the whole of a long job
   rather than for its budget.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.resolved import cancellation, job_budget, jobs


# ── the declaration ──────────────────────────────────────────────────


def test_every_job_kind_declares_a_budget():
    """A kind with no budget would hold the machine for ever.

    The GRANT (`lease`) is the one kind that carries none: the client
    holds the machine's turn for its own critical section, and the
    broker's job in that is to schedule it, not to end it. Its bound is
    the dead-owner reaping, which belongs to a turn.
    """
    from library.tools.resolved import jobs as job_kinds

    for kind in job_kinds.KINDS:
        if kind == "lease":
            continue
        assert kind in job_budget.BUDGETS, (
            f"{kind} declares no budget of its own")
        assert job_budget.BUDGETS[kind] > 0


def test_a_kind_with_no_budget_of_its_own_carries_the_default():
    assert job_budget.budget_for("a.kind.nobody.declared") == (
        job_budget.DEFAULT_BUDGET_SECONDS)


def test_a_caller_may_declare_its_own_budget():
    """The declaration is the caller's, and it is the one that is used."""
    assert job_budget.budget_for("resolve_axi", {}) == (
        job_budget.BUDGETS["resolve_axi"])
    assert job_budget.budget_for(
        "resolve_axi", {"budget_seconds": 42.0}) == 42.0


@pytest.mark.parametrize("declared", [
    0, -1, -0.5, True, False, "300", None, float("inf"),
    job_budget.MAX_BUDGET_SECONDS + 1,
])
def test_a_declared_budget_that_is_not_a_budget_is_refused(declared):
    """Half-honoured is worse than refused.

    A budget of zero would end the job at once; a negative one would
    end it before it began; a string is not a number of seconds; and a
    budget above the ceiling is a job that may hold the machine for
    ever, which is the defect this closes.
    """
    with pytest.raises(job_budget.BudgetRefused):
        job_budget.declared_budget(declared)


def test_a_malformed_declared_budget_is_refused_at_the_submission_gate():
    """The refusal happens where the submission is decided.

    A budget that is not a budget is refused before the job is queued,
    not half-honoured at execution - and the refusal says what the
    caller can do about it.
    """
    with pytest.raises(jobs.JobRefused) as caught:
        jobs.prepare("fusion.registry_summary",
                     {"budget_seconds": "ten minutes"})
    refusal = caught.value
    assert refusal.next_action == "supply_the_missing_parameter"
    assert "budget" in str(refusal)


def test_the_shape_carries_the_budget_before_the_job_is_queued():
    """The budget is visible in the shape, not decided at execution."""
    shape = jobs.prepare("fusion.registry_summary", {})
    assert shape["budget_seconds"] == job_budget.BUDGETS[
        "fusion.registry_summary"]
    shape = jobs.prepare("fusion.registry_summary",
                         {"budget_seconds": 12.0})
    assert shape["budget_seconds"] == 12.0


# ── the refusal ──────────────────────────────────────────────────────


def test_the_refusal_is_typed():
    """A host acts on the kind of block and the next action.

    A refusal that says only that the job was too slow gives the host
    nothing to act on; the two fields are what it acts on.
    """
    refusal = job_budget.JobBudgetExceeded("too slow")
    assert refusal.blocker == "resource_contention"
    assert refusal.next_action == "wait_for_the_resource"
    assert refusal.code == "REN_JOB_BUDGET_EXCEEDED"
    assert refusal.what and refusal.why and refusal.fix


def test_the_receipt_carries_the_budget_and_the_elapsed_time():
    """The receipt says what the budget was and how long the job ran."""
    receipt = job_budget.refusal_receipt("job-1", "resolve_axi", 300.0, 412.5)
    assert receipt["refusal"] == "JobBudgetExceeded"
    assert receipt["blocker"] == "resource_contention"
    assert receipt["next_action"] == "wait_for_the_resource"
    assert receipt["job_id"] == "job-1"
    assert receipt["kind"] == "resolve_axi"
    assert receipt["budget_seconds"] == 300.0
    assert receipt["elapsed_seconds"] == 412.5


def test_the_message_names_the_budget_and_the_overrun():
    message = job_budget.refusal_message("job-1", "resolve_axi", 300.0, 412.5)
    assert "300.0s" in message
    assert "412.5s" in message
    assert "job-1" in message


# ── the enforcement ──────────────────────────────────────────────────


def test_a_job_that_overran_its_budget_stops_at_its_next_checkpoint():
    """The machine is given back at a safe place, not at the end.

    THE DEFECT: a job that held the machine past its budget kept
    holding it for the whole of the rest of its work - the queue behind
    it waited for a job that had already broken its promise.
    """
    cancellation.begin("job-budget")
    cancellation.declare_budget("job-budget", 0.01)
    try:
        time.sleep(0.05)
        with pytest.raises(job_budget.JobBudgetExceeded) as caught:
            cancellation.checkpoint("job-budget")
    finally:
        cancellation.end("job-budget")
    assert "budget" in str(caught.value)


def test_a_job_within_its_budget_is_not_stopped():
    cancellation.begin("job-budget-ok")
    cancellation.declare_budget("job-budget-ok", 60.0)
    try:
        cancellation.checkpoint("job-budget-ok")
    finally:
        cancellation.end("job-budget-ok")


def test_a_job_that_declares_no_budget_is_never_stopped_by_one():
    """A GRANT carries no budget: the caller's turn is the caller's."""
    cancellation.begin("job-no-budget")
    cancellation.declare_budget("job-no-budget", 0.0)
    try:
        time.sleep(0.02)
        cancellation.checkpoint("job-no-budget")
    finally:
        cancellation.end("job-no-budget")


def test_a_stopped_job_keeps_the_result_it_had():
    """A write that landed before the budget ran out is still a write.

    The job stops at a checkpoint, and the checkpoint that stops it may
    be the one that carries the committed receipt. Dropping it would
    lose the only record that the work landed.
    """
    cancellation.begin("job-budget-result")
    cancellation.declare_budget("job-budget-result", 0.01)
    try:
        time.sleep(0.05)
        with pytest.raises(job_budget.JobBudgetExceeded) as caught:
            cancellation.checkpoint("job-budget-result",
                                    result={"status": "committed"})
    finally:
        cancellation.end("job-budget-result")
    assert caught.value.result == {"status": "committed"}
