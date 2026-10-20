"""The task's acceptance contract, and the gate that decides whether it is done.

A contract is DECLARED before the work runs: a list of criteria, each with
an id, a statement, and the kind of judgement it is - `technical` for a
check a machine makes, `editorial` for a judgement a person or a model
makes. It is not a list the engine fills in afterwards; it is the promise
the task makes, and the gate reads it.

An acceptance criterion is satisfied by EVIDENCE. Absent evidence is
`missing`, never a pass - the rule `operation_acceptance` already follows
for a single operation, and the reason a task cannot declare itself done
merely by having run.

THE GATE is what the contract is for. A task is COMPLETE only when every
criterion is passed AND the delivery receipt exists AND it reports no
missing and no failed output. A compiled plan, a submitted mutation, an
applied timeline write and a rendered file are stages on the way to
completion, and none of them is completion. A criterion can be added to a
declared contract but never removed: the contract is a promise, and a
promise is not withdrawn by the party that made it.

WHERE THE OTHER ANSWERS LIVE
----------------------------
`library/tools/assignment_view.py` answers what Ren understood,
proposed, applied and verified, and it carries this gate as its
`completed` answer. `library/tools/editorial_acceptance/` grades a
DELIVERED FILE against a brief - the evaluation harness, not a task's
own completion - and `editorial_decisions` carries the criteria a
decision proposes. This module owns one thing: the contract a TASK
makes, and the verdict that reads it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from library.tools import assignment_lifecycle as lifecycle

CRITERION_KINDS = ("technical", "editorial")

EVIDENCE_STATUSES = ("passed", "failed")

EVALUATION_STATUSES = ("undeclared", "passed", "failed", "incomplete")

STAGES = ("BRIEF", "PLANNED", "EXECUTED", "APPROVED", "DELIVERED", "CANCELLED")

STATE_STAGES = {
    "brief": "BRIEF",
    "draft": "PLANNED",
    "review": "EXECUTED",
    "approved": "APPROVED",
    "delivered": "DELIVERED",
    "cancelled": "CANCELLED",
}

DERIVED_CRITERION_ID = "delivery-files"

DERIVED_CRITERION = {
    "id": DERIVED_CRITERION_ID,
    "statement": "every delivered file exists and passes its own checks",
    "kind": "technical",
    "derived": True,
}
"""The one criterion this module derives rather than reads.

It says nothing the delivery receipt does not already say: the receipt
lists the files, whether each exists, and whether each passed its own
checks. A task with no declared contract still has this floor, so the
gate is never vacuous - and a task that declares its own contract keeps
this one as well.
"""


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _contract(record: Mapping) -> dict:
    section = record.get("acceptance")
    if not isinstance(section, Mapping):
        return {"criteria": [], "evidence": {}}
    criteria = section.get("criteria")
    evidence = section.get("evidence")
    return {
        "criteria": list(criteria) if isinstance(criteria, list) else [],
        "evidence": dict(evidence) if isinstance(evidence, Mapping) else {},
    }


def declare(project: str, assignment_id: str,
            criteria: Sequence[Mapping]) -> dict:
    """Declare the task's acceptance contract.

    Every criterion needs an id, a statement and a kind. Declaring the
    same criterion twice with the same content is the same criterion;
    declaring it with a different statement is a REFUSAL, because a
    criterion that changes its meaning after the work started is not a
    contract any more. A declared criterion is never removed - only
    added to.
    """
    declared = []
    for criterion in criteria:
        if not isinstance(criterion, Mapping):
            raise TypeError("each acceptance criterion is an object")
        for key in ("id", "statement", "kind"):
            if key not in criterion:
                raise ValueError(
                    f"an acceptance criterion needs a {key!r}: "
                    f"{dict(criterion)!r}")
        if criterion["kind"] not in CRITERION_KINDS:
            raise ValueError(
                f"unknown criterion kind {criterion['kind']!r}; expected one "
                f"of {', '.join(CRITERION_KINDS)}")
        if not isinstance(criterion["statement"], str) \
                or not criterion["statement"].strip():
            raise ValueError("an acceptance criterion states what it promises")
        declared.append({
            "id": str(criterion["id"]),
            "statement": criterion["statement"].strip(),
            "kind": criterion["kind"],
            "declared_at": str(criterion.get("declared_at") or _now()),
            "declared_by": str(criterion.get("declared_by") or "creator"),
        })
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        section = _contract(stored)
        known = {criterion["id"]: criterion for criterion in section["criteria"]}
        for criterion in declared:
            prior = known.get(criterion["id"])
            if prior is None:
                section["criteria"].append(criterion)
                known[criterion["id"]] = criterion
                continue
            if prior["statement"] != criterion["statement"] \
                    or prior["kind"] != criterion["kind"]:
                raise ValueError(
                    f"acceptance criterion {criterion['id']!r} is already "
                    f"declared with a different meaning; a contract is a "
                    f"promise and is not rewritten after the fact")
        stored["acceptance"] = section
        lifecycle.save(project, stored)
        return section


def record_evidence(project: str, assignment_id: str, criterion_id: str, *,
                    status: str, observation: str,
                    artifact: str = "") -> dict:
    """Record the observation that settles one acceptance criterion.

    The observation is required: a criterion with a verdict and no
    observation is a verdict nobody can check.
    """
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"unknown acceptance evidence status {status!r}; "
                         f"expected one of {', '.join(EVIDENCE_STATUSES)}")
    if not isinstance(observation, str) or not observation.strip():
        raise ValueError("acceptance evidence carries what was observed")
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        section = _contract(stored)
        known = {criterion["id"] for criterion in section["criteria"]}
        if criterion_id not in known:
            raise ValueError(
                f"acceptance criterion {criterion_id!r} is not declared; "
                f"declare it before recording evidence for it")
        entry = {
            "status": status,
            "observation": observation.strip(),
            "artifact": (artifact or "").strip(),
            "recorded_at": _now(),
        }
        section["evidence"][criterion_id] = entry
        stored["acceptance"] = section
        lifecycle.save(project, stored)
        return entry


def _delivery(project: str, record: Mapping) -> dict | None:
    """What the delivery receipts say about this task.

    The RECEIPT is the truth about the files, so the gate reads the
    receipts themselves - the same ones `assignment_view` reads - and
    not a copy somebody kept. A task with no receipt has no delivery,
    and that is what stops it being complete.
    """
    from library.tools import assignment_view

    for receipt in assignment_view.delivery_receipts(project, dict(record)):
        if receipt.get("status"):
            return {
                "receipt_path": receipt.get("receipt_path", ""),
                "status": receipt.get("status", ""),
                "files": [row.get("name") for row in receipt.get("files", ())],
                "missing_outputs": list(receipt.get("missing_outputs", ())),
                "failed_outputs": list(receipt.get("failed_outputs", ())),
                "qc": dict(receipt.get("qc") or {}),
                "at": receipt.get("at", ""),
            }
    return None


def _derived_evidence(delivery: Mapping | None) -> dict:
    """What the delivery receipt itself says about the derived criterion."""
    if not delivery:
        return {"status": "missing",
                "observation": "no delivery receipt exists yet",
                "artifact": "", "recorded_at": ""}
    status = str(delivery.get("status") or "")
    if status == "complete":
        return {"status": "passed",
                "observation": "the delivery receipt reports every file "
                               "present and passing its own checks",
                "artifact": str(delivery.get("receipt_path") or ""),
                "recorded_at": str(delivery.get("at") or "")}
    missing = list(delivery.get("missing_outputs") or [])
    failed = list(delivery.get("failed_outputs") or [])
    parts = []
    if missing:
        parts.append("missing: " + ", ".join(str(name) for name in missing))
    if failed:
        parts.append("failed: " + ", ".join(str(name) for name in failed))
    if not parts:
        parts.append(f"the delivery receipt reports {status or 'no status'}")
    return {"status": "failed", "observation": "; ".join(parts),
            "artifact": str(delivery.get("receipt_path") or ""),
            "recorded_at": str(delivery.get("at") or "")}


def evaluate(project: str, assignment_id: str) -> dict:
    """The contract and where every criterion stands.

    `undeclared` means the task promised nothing of its own; the derived
    criterion is still evaluated, so the gate is never vacuous.
    """
    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return {"status": "undeclared", "criteria": [], "passed": [],
                "failed": [], "missing": []}
    section = _contract(stored)
    delivery = _delivery(project, stored)
    rows = []
    declared_ids = {criterion["id"] for criterion in section["criteria"]}
    for criterion in section["criteria"]:
        evidence = section["evidence"].get(criterion["id"])
        rows.append({
            "id": criterion["id"],
            "statement": criterion["statement"],
            "kind": criterion["kind"],
            "derived": False,
            "status": evidence["status"] if evidence else "missing",
            "observation": evidence["observation"] if evidence else "",
            "artifact": evidence.get("artifact", "") if evidence else "",
        })
    if DERIVED_CRITERION_ID not in declared_ids:
        derived_evidence = _derived_evidence(delivery)
        rows.append({
            **DERIVED_CRITERION,
            "status": derived_evidence["status"],
            "observation": derived_evidence["observation"],
            "artifact": derived_evidence["artifact"],
        })
    passed = [row["id"] for row in rows if row["status"] == "passed"]
    failed = [row["id"] for row in rows if row["status"] == "failed"]
    missing = [row["id"] for row in rows if row["status"] == "missing"]
    if not section["criteria"]:
        # The task promised nothing of its own. The derived criterion is
        # a floor, not a contract, and a task that never said what "done"
        # means has not said it is done.
        status = "undeclared"
    elif failed:
        status = "failed"
    elif missing:
        status = "incomplete"
    else:
        status = "passed"
    return {"status": status, "criteria": rows, "passed": passed,
            "failed": failed, "missing": missing}


def completion(project: str, assignment_id: str) -> dict:
    """The gate: is this task done, and if not, what says it is not.

    `complete` is the only claim of done-ness, and it is true only when
    the task was DELIVERED, every acceptance criterion is passed, and the
    delivery receipt itself reports no missing and no failed output. The
    stage names how far the task got, so a reader can tell a task that
    has not started from one whose plan compiled, and one whose timeline
    was written from one that was delivered.
    """
    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return {"complete": False, "stage": "BRIEF", "assignment_state": None,
                "reason": "the task has no record", "criteria": {},
                "delivery": None, "blockers": ["the task has no record"]}
    state = str(stored.get("state") or "brief")
    stage = STATE_STAGES.get(state, "BRIEF")
    evaluated = evaluate(project, assignment_id)
    delivery = _delivery(project, stored)
    blockers = []
    if stage != "DELIVERED":
        blockers.append(
            f"the task is {state}, not delivered: a compiled plan, a "
            f"submitted mutation and a written timeline are stages on the "
            f"way, and none of them is completion")
    for row in evaluated["criteria"]:
        if row["status"] == "failed":
            blockers.append(
                f"acceptance criterion {row['id']!r} failed: "
                f"{row['observation'] or 'no observation recorded'}")
        elif row["status"] == "missing":
            blockers.append(
                f"acceptance criterion {row['id']!r} has no evidence yet")
    if evaluated["status"] == "undeclared":
        blockers.append(
            "the task has no acceptance contract: it never said what done "
            "means, so nothing can say it is done")
    if delivery is None:
        blockers.append("no delivery receipt exists for this task")
    elif str(delivery.get("status")) != "complete":
        blockers.append(
            f"the delivery receipt reports {delivery.get('status')!r}, not "
            f"a complete delivery")
    complete = (stage == "DELIVERED" and not blockers)
    return {
        "complete": complete,
        "stage": stage,
        "assignment_state": state,
        "reason": ("the task is complete: it was delivered and every "
                   "acceptance criterion is evidenced"
                   if complete else
                   "; ".join(blockers) or "the task is not complete"),
        "criteria": evaluated,
        "delivery": delivery,
        "blockers": blockers,
    }
