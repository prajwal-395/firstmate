"""Which queued job Resolve runs next. Pure: no socket, no clock of its own.

Order is (priority band after aging, locality, arrival):

* PRIORITY is a class, most urgent first (`PRIORITIES`). A person
  waiting outranks a read, a read outranks a write, a write outranks a
  render.
* AGING lifts a waiting job one class per `AGING_SECONDS`, so a batch
  job cannot starve behind a steady stream of urgent ones.
* LOCALITY breaks ties inside a band: a job naming the project and
  timeline the cursor already sits on goes first, so five requests on
  Reel03 do not bounce the cursor through Reel09 between them.

Strict head-of-line: only the top job may start. If it cannot start yet
(a writer behind running readers), nothing behind it starts either -
that is what keeps readers from starving a writer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional, Tuple

#: Most urgent first. The plan's ladder: a human, a short read, a short
#: mutation, a build commit, a QA render, a qualification test, an export.
PRIORITIES = ("interactive", "read", "mutation", "build", "qa_render",
              "qualification", "export")
RANK = {name: index for index, name in enumerate(PRIORITIES)}

#: Seconds of waiting that lift a job one priority class.
AGING_SECONDS = 30.0

EXCLUSIVE = "exclusive"
SHARED = "shared"


@dataclass
class Pending:
    """What the scheduler needs to know about one job."""

    id: str
    priority: str
    mode: str
    submitted: float
    project: str = ""
    timeline: str = ""
    executed: bool = False
    qualification: bool = False
    locality: str = ""


def effective_rank(job: Pending, now: float) -> float:
    return RANK[job.priority] - max(0.0, now - job.submitted) / AGING_SECONDS


def order_key(job: Pending, now: float,
              cursor: Tuple[str, str]) -> tuple:
    locality = job.locality or ("timeline" if job.timeline else
                                "project" if job.project else "none")
    local = ((locality == "timeline" and bool(job.project)
              and (job.project, job.timeline) == cursor)
             or (locality == "project" and bool(job.project)
                 and job.project == cursor[0]))
    return (math.floor(effective_rank(job, now)), 0 if local else 1,
            job.submitted, job.id)


def can_start(job: Pending, running: Iterable[Pending]) -> bool:
    """Exclusive needs Resolve to itself; shared runs beside shared.

    The broker runs EXECUTED jobs one at a time on its own connection,
    so a second executed job waits for the first whatever its mode.
    """
    running = list(running)
    if job.executed and any(other.executed for other in running):
        return False
    if job.mode == EXCLUSIVE:
        return not running
    return all(other.mode == SHARED for other in running)


def next_job(queued: Iterable[Pending], running: Iterable[Pending],
             cursor: Tuple[str, str], now: float) -> Optional[Pending]:
    """The job to start now, or None - strict head-of-line."""
    queued = list(queued)
    if not queued:
        return None
    head = min(queued, key=lambda job: order_key(job, now, cursor))
    return head if can_start(head, running) else None
