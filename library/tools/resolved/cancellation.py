"""Cooperative cancellation markers for jobs that declare a safe stop path.

The broker owns admission and queueing. A running job can only be stopped
when its runner checks this marker and has a cleanup path; this module
provides the worker-side signal without ever terminating a worker process.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

SUSPEND_GAP_SECONDS = 5.0
_clock_lock = threading.Lock()
_job_clocks: dict[str, tuple[float, float]] = {}
_job_budgets: dict[str, tuple[float, float]] = {}
"""Each job's budget and the moment it started: `(seconds, started)`."""


def _suspend_clock() -> float:
    """A monotonic clock that advances while the host is asleep."""
    if sys.platform == "darwin" and hasattr(time, "CLOCK_MONOTONIC_RAW"):
        # Apple maps this to mach_continuous_time, unlike CLOCK_UPTIME_RAW,
        # which stops while the machine sleeps.
        return time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


def _agent_clock() -> float:
    """Wall clock seam for cancellation's sleep-gap measurement."""
    return time.time()


class CancelRequested(Exception):
    """The broker requested a safe stop at a runner checkpoint."""

    def __init__(self, message: str, result=None):
        super().__init__(message)
        self.result = result


def marker_path(job_id: str) -> Path:
    """Where the marker for this job lives.

    Beside the broker that queued the job: the broker's directory is
    the directory its socket lives in, so a worker the broker started
    writes and reads the same markers its broker does.
    """
    from library.tools.resolved import broker_paths
    return broker_paths.cancel_dir() / job_id


def begin(job_id: str) -> None:
    """Record wall and monotonic clocks at broker-job admission."""
    with _clock_lock:
        _job_clocks[job_id] = (_agent_clock(), _suspend_clock())


def declare_budget(job_id: str, budget_seconds: float) -> None:
    """Record the job's DECLARED budget: the seconds it may hold Resolve.

    `library/tools/resolved/job_budget.py` owns the budget itself; this
    is where the running job's copy of it is kept, so the checkpoints
    the job passes can stop it. Zero means the job declares none - a
    GRANT, whose turn is the caller's own.
    """
    with _clock_lock:
        if budget_seconds and budget_seconds > 0:
            _job_budgets[job_id] = (float(budget_seconds), _suspend_clock())
        else:
            _job_budgets.pop(job_id, None)


def end(job_id: str) -> None:
    with _clock_lock:
        _job_clocks.pop(job_id, None)
        _job_budgets.pop(job_id, None)


def _sleep_gap(job_id: str) -> float:
    with _clock_lock:
        started = _job_clocks.get(job_id)
    if started is None:
        return 0.0
    wall, monotonic = started
    return max(0.0, (_agent_clock() - wall) -
               (_suspend_clock() - monotonic))


def requested(job_id: str) -> bool:
    return marker_path(job_id).exists()


def request(job_id: str) -> None:
    marker = marker_path(job_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("requested\n", encoding="utf-8")


def clear(job_id: str) -> None:
    try:
        marker_path(job_id).unlink()
    except FileNotFoundError:
        pass


def checkpoint(job_id: str, *, result=None) -> None:
    if requested(job_id):
        raise CancelRequested(
            f"job {job_id} stopped at a safe checkpoint", result=result)
    _check_the_budget(job_id, result=result)
    gap = _sleep_gap(job_id)
    if gap >= SUSPEND_GAP_SECONDS:
        raise CancelRequested(
            f"machine sleep or a wall-clock jump interrupted job {job_id}; "
            f"stopped after a {gap:.1f}s clock gap at a safe checkpoint",
            result=result)


def _check_the_budget(job_id: str, *, result=None) -> None:
    """Stop the job at a safe checkpoint once its budget is gone.

    A job that has held the one Resolve for longer than it declared
    stops HERE, at the next place it knows is safe - not at the end of
    whatever it was doing, and not for ever. The refusal is typed
    (`job_budget.JobBudgetExceeded`), so the receipt says what the
    budget was and how long the job ran.
    """
    with _clock_lock:
        declared = _job_budgets.get(job_id)
    if declared is None:
        return
    budget, started = declared
    elapsed = _suspend_clock() - started
    if elapsed <= budget:
        return
    from library.tools.resolved import job_budget
    raise job_budget.JobBudgetExceeded(
        job_budget.refusal_message(job_id, "", budget, elapsed),
        result=result)
