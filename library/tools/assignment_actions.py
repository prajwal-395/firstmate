"""Pause, resume, replan and cancel: the acts that change a task's course
without changing its work.

PAUSE stops the task accepting new acts. The work stays exactly where it
is, the next act is refused until it is RESUMED, and nothing is lost. It
is the TASK's handbrake, and it is not the RUN's: `ren pause` holds a
pipeline run at a step boundary, and a task may be paused while no run is
going at all.

REPLAN replaces the part of the plan that has not run. A node that has
completed is KEPT - its work happened, and the world moved on - and the
nodes that have not are replaced by what the replan declares. A replan
never rewrites history: the reason for changing the plan and the nodes
it replaced stay on the record, so a later reader can see that the plan
moved and why.

CANCEL ends the task. It does NOT undo work that was delivered or
approved - the files exist and the creator saw them - it says the task is
over and keeps everything that happened. A cancelled task can be
reopened, and reopening is a NEW request rather than a resumption of this
one: the old record stays cancelled.
"""

from __future__ import annotations

import copy
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path

from library.tools import assignment_lifecycle as lifecycle
from library.tools.ren_refusal import RenRefusal


def _root(project) -> str:
    return str(Path(project).expanduser().resolve())


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _reason(reason: str) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a reason states why the task is changing course")
    return reason.strip()


def _command(project, assignment_id: str, flag: str) -> str:
    return (f"ren assignment {shlex.quote(_root(project))} "
            f"--id {shlex.quote(assignment_id)} {flag}")


def resume_command(project, assignment_id: str) -> str:
    """The runnable way to release a paused task."""
    return _command(project, assignment_id, "--resume")


def pause_command(project, assignment_id: str) -> str:
    """The runnable way to pause a task."""
    return _command(project, assignment_id, "--pause \"<reason>\"")


def cancel_command(project, assignment_id: str) -> str:
    """The runnable way to cancel a task."""
    return _command(project, assignment_id, "--cancel \"<reason>\"")


def is_paused(record: Mapping) -> bool:
    """Whether this task is holding its acts."""
    return bool(record.get("pause"))


def pause(project: str, assignment_id: str, *, reason: str) -> dict:
    """Hold the task's acts. Idempotent: a second pause keeps the first."""
    reason = _reason(reason)
    with lifecycle.assignment_lock(project, assignment_id):
        record = lifecycle.load(project, assignment_id)
        if record is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        if record["state"] == "cancelled":
            raise RenRefusal(
                f"assignment {assignment_id} is cancelled",
                "a cancelled task is over and holds nothing",
                f"reopen it first: `ren assignment "
                f"{shlex.quote(_root(project))} --id "
                f"{shlex.quote(assignment_id)} --reopen`", blocker="unsafe_state")
        if record.get("pause"):
            return record
        record["pause"] = {"at": _now(), "reason": reason}
        record.setdefault("history", []).append(
            {"event": "pause", "from": record["state"], "to": record["state"],
             "at": _now(), "reason": reason})
        return lifecycle.save(project, record)


def resume(project: str, assignment_id: str) -> dict:
    """Release the task's acts. Idempotent: resuming a running task is a no-op."""
    with lifecycle.assignment_lock(project, assignment_id):
        record = lifecycle.load(project, assignment_id)
        if record is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        if not record.get("pause"):
            return record
        previous = record["pause"]
        record["pause"] = None
        record.setdefault("history", []).append(
            {"event": "resume", "from": record["state"], "to": record["state"],
             "at": _now(), "reason": previous.get("reason", "")})
        return lifecycle.save(project, record)


def replan(project: str, assignment_id: str, *, reason: str,
           nodes: Sequence[Mapping] = ()) -> dict:
    """Replace the part of the plan that has not run.

    A node that has completed stays: its work happened. The nodes that
    have not are dropped, and the ones the caller declares are put in
    their place. The old plan and the reason stay on the record.
    """
    from library.tools import assignment_plan

    reason = _reason(reason)
    with lifecycle.assignment_lock(project, assignment_id):
        record = lifecycle.load(project, assignment_id)
        if record is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        if record["state"] == "cancelled":
            raise RenRefusal(
                f"assignment {assignment_id} is cancelled",
                "a cancelled task is over and holds no plan",
                f"reopen it first: `ren assignment "
                f"{shlex.quote(_root(project))} --id "
                f"{shlex.quote(assignment_id)} --reopen`", blocker="unsafe_state")
        planned = assignment_plan.plan(project, assignment_id)["operations"]
        kept = {name: node for name, node in planned.items()
                if node.get("status") in assignment_plan.TERMINAL_STATUSES}
        dropped = sorted(name for name in planned if name not in kept)
        record["plan"] = {"operations": copy.deepcopy(kept)}
        record.setdefault("history", []).append(
            {"event": "replan", "from": record["state"], "to": record["state"],
             "at": _now(), "reason": reason, "dropped": dropped,
             "kept": sorted(kept)})
        lifecycle.save(project, record)
    for node in nodes:
        assignment_plan.add(
            project, assignment_id,
            node_id=str(node["node_id"]),
            operation_id=str(node["operation_id"]),
            group=str(node.get("group") or ""),
            depends_on=[str(name) for name in node.get("depends_on", ())],
            label=str(node.get("label") or ""),
            evidence_ids=[str(name) for name in node.get("evidence_ids", ())],
            decision_ids=[str(name) for name in node.get("decision_ids", ())])
    return lifecycle.load(project, assignment_id)


def cancel(project: str, assignment_id: str, *, reason: str) -> dict:
    """End the task. Nothing that was delivered is taken back."""
    reason = _reason(reason)
    with lifecycle.assignment_lock(project, assignment_id):
        record = lifecycle.load(project, assignment_id)
        if record is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        if record["state"] == "cancelled":
            return record
        record["pause"] = None
        lifecycle.save(project, record)
        return lifecycle.transition(project, assignment_id, "cancelled",
                                    event="cancel", detail={"reason": reason})


def reopen(project: str, assignment_id: str) -> dict:
    """Bring a cancelled task back as a request of its own.

    Reopening does not resume the old work: the cancelled record keeps
    its history, and the task starts again from `brief` with nothing
    assumed about what the cancelled attempt proved.
    """
    with lifecycle.assignment_lock(project, assignment_id):
        record = lifecycle.load(project, assignment_id)
        if record is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        if record["state"] != "cancelled":
            return record
        return lifecycle.transition(project, assignment_id, "brief",
                                    event="reopen")
