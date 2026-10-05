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

Head-of-line within a band: the top job in a band starts if it can. A
blocked head (a writer behind running readers) is skipped only by jobs
in the SAME band that can start, so an unrelated read is not stuck
behind a writer that cannot run. The skip is bounded by aging: the
blocked head is lifted one band per `AGING_SECONDS`, and once it is in
a more urgent band than the jobs that would skip it, strict head-of-line
protects it - readers cannot starve a writer it outranks.
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
    """The job to start now, or None.

    The head of the queue starts if it can. If it cannot (a writer
    behind running readers), the next job in the SAME priority band that
    can start is skipped to, so a blocked head does not stall unrelated
    work. The skip is bounded: aging lifts the blocked head out of the
    skippers' band within `AGING_SECONDS`, after which strict head-of-line
    applies and the head cannot be overtaken by jobs it outranks.
    """
    queued = list(queued)
    if not queued:
        return None
    ordered = sorted(queued, key=lambda job: order_key(job, now, cursor))
    head = ordered[0]
    if can_start(head, running):
        return head
    head_band = math.floor(effective_rank(head, now))
    for job in ordered[1:]:
        if math.floor(effective_rank(job, now)) != head_band:
            break
        if can_start(job, running):
            return job
    return None
