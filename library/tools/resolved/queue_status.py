"""What the broker's queue is doing, and why a job is still waiting.

The broker's receipts say a job was submitted, when it started and how
long it held Resolve. They do not say where it sits in the queue, what
it is waiting for, or which resource blocks it - so a lane that waits
fifteen minutes cannot tell whether it is behind a long exclusive
session, behind a dead lane's abandoned job, or behind nothing at all.

This module answers those three questions from what the broker already
publishes: the `list` reply (the queued jobs and the running ones) and
the instance lease's own record. It is read-only, and it invents
nothing: the ORDER it reports is the scheduler's own order
(`library.tools.resolved.scheduler`), so the position it names is the
position the broker will use, and the blocker it names is a job the
broker really has running.

Two things it adds that the broker does not publish at all:

* the OWNER of a queued job, and whether that owner's process is still
  alive. A grant whose owner is gone is a job nobody is waiting for and
  nobody will release - it sits in the queue holding an exclusive slot
  until the broker reaps it.
* the instance lease's holder, when it is NOT one of the broker's
  running jobs. That is the case where the queue waits on something the
  broker does not know about, and it is invisible from the job table
  alone.

`library/tools/resolved/kpi.py` measures the same queue over a window;
this module describes it right now.
"""

from __future__ import annotations

import time
from collections.abc import Iterable

from library.tools.resolved import scheduler
from library.tools.resolved.job_owner import (
    owner_alive,
    owner_pid,
    process_alive,
)

__all__ = [
    "owner_alive",
    "owner_pid",
    "process_alive",
    "queue_report",
    "render",
]


def _locality(job: dict) -> str:
    """The locality the scheduler would read for this job.

    The receipt does not carry the broker's own `locality` field (the
    job table has no column for it), so this is the shape `jobs.prepare`
    derives for a job that names no other: the timeline it names, else
    the project it names, else neither.
    """
    if job.get("timeline"):
        return "timeline"
    if job.get("project"):
        return "project"
    return "none"


def _pending(job: dict) -> scheduler.Pending:
    return scheduler.Pending(
        id=job["id"],
        priority=job.get("priority") or "read",
        mode=job.get("mode") or scheduler.SHARED,
        submitted=float(job.get("submitted") or 0.0),
        project=job.get("project") or "",
        timeline=job.get("timeline") or "",
        executed=bool(job.get("executed")),
        qualification=bool(job.get("qualification")),
        locality=_locality(job),
    )


def _cursor(jobs: Iterable[dict]) -> tuple:
    """Where the cursor sits: the last started job that named a project."""
    cursor = ("", "")
    for job in sorted(jobs, key=lambda j: j.get("started") or 0.0):
        if job.get("project"):
            cursor = (job["project"], job.get("timeline") or "")
    return cursor


def queue_report(reply: dict, *, lease: dict | None = None,
                 now: float | None = None) -> dict:
    """The queue's shape right now: positions, wait causes, blockers.

    `reply` is the broker's `list` reply (`{"jobs": [...], "running":
    [...]}`); `lease` is the instance lease's active holder, when one
    is known. Everything reported here is derived from those two.
    """
    now = time.time() if now is None else float(now)
    jobs = [job for job in (reply.get("jobs") or []) if isinstance(job, dict)]
    running = [job for job in (reply.get("running") or [])
               if isinstance(job, dict)]
    receipts = {job["id"]: job for job in jobs if job.get("id")}
    queued = [job for job in jobs if job.get("state") == "queued"]
    cursor = _cursor(jobs)

    blockers = []
    for entry in running:
        receipt = receipts.get(entry.get("id"), {})
        started = receipt.get("started") or entry.get("submitted") or now
        blockers.append({
            "id": entry.get("id", ""),
            "kind": receipt.get("kind", ""),
            "mode": entry.get("mode", ""),
            "executed": bool(entry.get("executed")),
            "description": receipt.get("description", ""),
            "owner": receipt.get("owner", ""),
            "project": entry.get("project", ""),
            "timeline": entry.get("timeline", ""),
            "held_seconds": round(max(0.0, now - float(started)), 3),
        })

    ordered = sorted((_pending(job) for job in queued),
                     key=lambda job: scheduler.order_key(job, now, cursor))
    position_of = {job.id: index for index, job in enumerate(ordered, start=1)}

    # The instance lease's holder, when it is NOT one of the broker's
    # running jobs: the queue is then waiting on something the broker
    # does not know about, which the job table alone cannot show.
    outside = None
    if lease:
        holder = lease.get("owner") or ""
        running_owners = {blocker["owner"] for blocker in blockers}
        if holder and holder not in running_owners:
            outside = {
                "owner": holder,
                "purpose": lease.get("purpose", ""),
                "pid": lease.get("pid"),
                "since": lease.get("since"),
            }

    rows = []
    for job in queued:
        receipt = receipts.get(job["id"], {})
        pending = _pending(job)
        waited = round(max(0.0, now - pending.submitted), 3)
        alive = owner_alive(job.get("owner", ""))
        cause, blocked_by = _wait_cause(pending, blockers, outside)
        if alive is False:
            cause = (f"the owner ({job.get('owner')}) is gone, so this job "
                     f"holds its place in the queue until the broker reaps it")
        rows.append({
            "id": job["id"],
            "kind": job.get("kind", ""),
            "description": receipt.get("description", ""),
            "owner": job.get("owner", ""),
            "owner_alive": alive,
            "priority": pending.priority,
            "mode": pending.mode,
            "project": pending.project,
            "timeline": pending.timeline,
            "position": position_of.get(job["id"]),
            "waited_seconds": waited,
            "wait_cause": cause,
            "blocked_by": blocked_by,
        })

    rows.sort(key=lambda row: row["position"] or 0)
    dead = [row for row in rows if row["owner_alive"] is False]
    return {
        "depth": len(rows),
        "running": blockers,
        "queued": rows,
        "oldest_wait_seconds": (rows[0]["waited_seconds"] if rows else None),
        "dead_owner_jobs": len(dead),
        "blocked_resources": ([
            f"the instance lease: {outside['owner']} - {outside['purpose']}"
        ] if outside else []),
    }


def _wait_cause(pending: scheduler.Pending, blockers: list,
                outside: dict | None) -> tuple:
    """Why this job has not started, and what blocks it.

    The cause names the job that holds Resolve, or the fact that
    NOTHING holds it - which is the case a queue report must not
    paper over: a job waiting with nothing running is waiting on the
    broker, not on the machine.
    """
    if not blockers:
        if outside is not None:
            cause = (
                f"the instance lease is held by {outside['owner']} "
                f"({outside['purpose']}), which is not one of the "
                f"broker's running jobs")
            resource = {
                "id": "", "kind": "instance_lease", "mode": "exclusive",
                "executed": False, "description": outside["purpose"],
                "owner": outside["owner"],
                "held_seconds": round(
                    max(0.0, time.time() - float(outside.get("since")
                                                 or time.time())), 3)}
            return cause, [resource]
        idle = ("nothing is running and this job has not started: the "
                "broker is not scheduling it")
        return idle, []
    exclusive = [b for b in blockers if b["mode"] == scheduler.EXCLUSIVE]
    executed = [b for b in blockers if b["executed"]]
    shared = [b for b in blockers if b not in exclusive]
    if pending.mode == scheduler.EXCLUSIVE and exclusive:
        holder = exclusive[0]
        cause = (f"an exclusive job holds Resolve: {holder['description']}"
                 f" ({holder['owner'] or 'unknown owner'}, held "
                 f"{holder['held_seconds']:.0f}s)")
        blocked_by = exclusive
    elif pending.mode == scheduler.EXCLUSIVE and shared:
        holder = shared[0]
        cause = (f"a shared job holds Resolve: {holder['description']}"
                 f" ({holder['owner'] or 'unknown owner'}, held "
                 f"{holder['held_seconds']:.0f}s)")
        blocked_by = shared
    elif executed:
        holder = executed[0]
        cause = (f"the execution worker is running {holder['description']}"
                 f" ({holder['owner'] or 'unknown owner'}, held "
                 f"{holder['held_seconds']:.0f}s)")
        blocked_by = executed
    else:
        holder = blockers[0]
        cause = (f"a shared job holds Resolve: {holder['description']}"
                 f" ({holder['owner'] or 'unknown owner'}, held "
                 f"{holder['held_seconds']:.0f}s)")
        blocked_by = blockers
    if outside is not None:
        cause += (f"; the instance lease is also held by {outside['owner']}"
                  f" ({outside['purpose']}), which is not one of the "
                  f"broker's running jobs")
    return cause, blocked_by


def render(report: dict) -> str:
    """The queue as text, for a terminal."""
    lines = []
    if report["depth"] == 0:
        lines.append("queue: empty")
    else:
        lines.append(f"queue: {report['depth']} waiting, oldest "
                     f"{report['oldest_wait_seconds']:.0f}s")
    for row in report["queued"]:
        owner = row["owner"] or "no owner"
        if row["owner_alive"] is False:
            owner += " (GONE)"
        lines.append(
            f"  #{row['position']} {row['kind']} [{row['mode']}] "
            f"waited {row['waited_seconds']:.0f}s - {row['description']}"
            f" [{owner}]")
        lines.append(f"      because: {row['wait_cause']}")
    for blocker in report["running"]:
        lines.append(
            f"  holding: {blocker['kind']} [{blocker['mode']}] "
            f"{blocker['held_seconds']:.0f}s - {blocker['description']}"
            f" [{blocker['owner'] or 'unknown owner'}]")
    for resource in report["blocked_resources"]:
        lines.append(f"  blocked resource: {resource}")
    if report["dead_owner_jobs"]:
        lines.append(f"  {report['dead_owner_jobs']} job(s) have a dead "
                     f"owner")
    return "\n".join(lines)
