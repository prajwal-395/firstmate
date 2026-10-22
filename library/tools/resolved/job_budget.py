"""How long ONE broker job may hold the one Resolve.

THE DEFECT (measured 2026-10-21, the machine's own broker, 24 h, 4,449
jobs): a broker job had NO upper bound on its run.  The instance
lease's own timeout bounds the WAIT for the machine, never the HOLD of
it, so a Resolve call that blocks - 36 minutes inside
`Fusion::RemoteApp::WaitPkt` in one measurement, a `project scratch
open` that held the instance lease for fifteen minutes in another - held
the one Resolve and the whole queue for as long as it blocked.  The
machine's own KPI read `resolve hold p99 900.58s, max 4297.34s`, and
the only way out was for a lane to kill the worker's process by hand
(measured the same day: pid 33974).

So a job now carries a DECLARED BUDGET: the seconds it may hold the
machine.  The budget is declared in ONE place (this module), it is
visible in the job's shape before the job is queued, and a job that
overruns it is REFUSED with a typed receipt rather than left to hold
the machine for ever.

The budgets are the floor of what the work legitimately costs, not a
target: a budget that fires on a job that was going to finish refuses
work that would have landed, so each one is generous, and the two
kinds that genuinely take minutes (a project open, a qualification
session) carry minutes.  A caller that knows its job is longer says so
(`params["budget_seconds"]`), up to `MAX_BUDGET_SECONDS` - a declared
budget is a DECLARATION, and a declaration above the ceiling is
refused rather than honoured.

A GRANT (`lease`) carries no budget: the client holds the machine's
turn for its own critical section, and the broker's job in that is to
schedule it, not to end it.  A grant whose owner dies is reaped
(`library/tools/resolved/job_owner.py`), which is the bound that
belongs to a turn.

`library/tools/resolved/jobs.py` puts the budget in the shape;
`library/tools/resolved/worker.py` and the daemons enforce it.
`tests/unit/resolve/test_job_budget.py`.
"""

from __future__ import annotations

from library.tools.ren_refusal import RenRefusal

#: The budget a job carries when its kind names none: five minutes.
#: A read, a write and an AXI command all finish inside it; a project
#: open and a qualification session name their own.
DEFAULT_BUDGET_SECONDS = 300.0

#: The ceiling on a DECLARED budget.  A caller may say its job is
#: longer than the kind's budget; it may not say it is unbounded.
MAX_BUDGET_SECONDS = 7200.0

#: The budget per job kind, in seconds.  One enumeration: a kind with
#: no entry here carries `DEFAULT_BUDGET_SECONDS`.
#:
#: - a READ is a snapshot, a fingerprint or a registry summary: seconds.
#: - a WRITE is a patch, a semantic edit or a UI transaction: the write
#:   plus its verification, a minute or two.
#: - `resolve_axi` is the AXI command line, and one of them opens a
#:   project (measured 2026-10-21: 8.77 s at the median, 34.65 s for a
#:   park in the captain's project) while another renders a timeline
#:   for inspection - so it carries minutes.
#: - the QUALIFICATION kinds run a whole session over the live Resolve
#:   and are measured in tens of minutes.
BUDGETS: dict[str, float] = {
    "timeline.snapshot": 120.0,
    "timeline.observe_current": 120.0,
    "fusion.registry_summary": 120.0,
    "resolve.activity_probe": 120.0,
    "timeline.apply_patch": 300.0,
    "timeline.semantic_edit": 300.0,
    "resolve.ui_transaction": 300.0,
    "resolve_axi": 600.0,
    "reel.promote_staged": 1800.0,
    "reel.prepare_qualification_fixture": 1800.0,
    "reel.probe_qualification_marker_loss": 1800.0,
    "reel.copy_tv_ending": 1800.0,
    "timeline.drt_export_probe": 1800.0,
    "timeline.drt_recovery_probe": 1800.0,
    "resolve.qualification_session": 3600.0,
    "qualification.workflow": 3600.0,
}

#: The key a caller declares its budget under, in the job's params.
DECLARED_KEY = "budget_seconds"


class BudgetRefused(ValueError):
    """A declared budget is not a budget. The submission is refused."""


class JobBudgetExceeded(RenRefusal):
    """A job held the machine for longer than its declared budget.

    A REFUSAL, not a crash: the job is over, the machine is free, and
    the receipt says what the budget was and how long the job ran.  The
    block is `resource_contention` - the machine was held past the time
    the caller was promised - and the next action is to wait for the
    resource and try again, or to declare a longer budget.

    The job may have DONE something before the budget ran out: a job
    that writes and then overruns is not a job that wrote nothing.  The
    receipt says which it was, and a write is verified by its own
    read-back, not by the budget.
    """

    CODE = "REN_JOB_BUDGET_EXCEEDED"
    RECOVERY_CLASS = "auto_recoverable"
    BLOCKER = "resource_contention"
    NEXT_ACTION = "wait_for_the_resource"
    USER_MESSAGE = ("This step took longer than the time it was allowed "
                    "on Resolve, so I stopped it and gave the machine "
                    "back. Nothing else can run until it does.")

    def __init__(self, message: str, *, what: str = "", why: str = "",
                 fix: str = "", result=None):
        super().__init__(
            what or message,
            why or ("the job held the one Resolve for longer than its "
                    "declared budget, so the machine is freed rather than "
                    "held"),
            fix or ("raise the job's declared budget (budget_seconds) if "
                    "the work legitimately takes longer, or split the "
                    "work into smaller jobs"),
        )
        self.message = message
        self.result = result
        """What the job had done when the budget ran out.

        A job that stops at a committed checkpoint carries its committed
        receipt here: the work may have LANDED, and a refusal that
        dropped it would lose the only record of what happened.
        """

    def __str__(self) -> str:
        return self.message


def budget_for(kind: str, params: dict | None = None) -> float:
    """The budget one job carries, in seconds.

    The kind's declared budget, unless the CALLER declared one in the
    job's params (`budget_seconds`).  A declared budget must be a
    positive number of seconds and may not exceed
    `MAX_BUDGET_SECONDS`; anything else raises `BudgetRefused`, which
    the submission gate turns into a refusal.
    """
    declared = (params or {}).get(DECLARED_KEY)
    if declared is not None:
        return declared_budget(declared)
    return float(BUDGETS.get(kind, DEFAULT_BUDGET_SECONDS))


def declared_budget(declared) -> float:
    """The budget a caller declared, checked.

    A declared budget is a number of seconds: positive, finite and
    within the ceiling.  A budget of zero, a negative budget, a
    boolean, a string and a budget above the ceiling are all refused -
    each of them would either end the job at once or hold the machine
    for ever, which is the defect this module exists to close.
    """
    if isinstance(declared, bool) or not isinstance(declared, (int, float)):
        raise BudgetRefused(
            f"a declared budget must be a number of seconds, not "
            f"{declared!r}")
    value = float(declared)
    if value != value or value in (float("inf"), float("-inf")):
        raise BudgetRefused("a declared budget must be a finite number "
                            f"of seconds, not {declared!r}")
    if value <= 0:
        raise BudgetRefused(
            f"a declared budget must be positive, not {value:g}s")
    if value > MAX_BUDGET_SECONDS:
        raise BudgetRefused(
            f"a declared budget of {value:g}s is above the ceiling of "
            f"{MAX_BUDGET_SECONDS:g}s; split the work into smaller jobs "
            "instead")
    return value


def refusal_receipt(job_id: str, kind: str, budget: float,
                     elapsed: float, *, state: str = "failed") -> dict:
    """The typed receipt a job that overran its budget leaves behind.

    The same body in every daemon and in the worker, so a reader does
    not have to know which one served the job: the refusal's name, the
    kind of block, the next action, the budget and how long the job
    actually ran.
    """
    return {
        "refusal": "JobBudgetExceeded",
        "blocker": JobBudgetExceeded.BLOCKER,
        "next_action": JobBudgetExceeded.NEXT_ACTION,
        "blocking_reason": ("something else is holding the resource"),
        "job_id": job_id,
        "kind": kind,
        "budget_seconds": float(budget),
        "elapsed_seconds": float(elapsed),
        "state": state,
    }


def refusal_message(job_id: str, kind: str, budget: float,
                    elapsed: float) -> str:
    """The one sentence a receipt and an operator both read."""
    subject = (f"the {kind} job {job_id}" if kind
               else f"the broker job {job_id}")
    return (f"{subject} held Resolve for {elapsed:.1f}s, "
            f"past its declared budget of {budget:.1f}s - the machine is "
            f"freed and the job is refused rather than left holding it")
