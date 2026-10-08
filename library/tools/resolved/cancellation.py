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
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / "ren-resolved-cancel" / job_id


def begin(job_id: str) -> None:
    """Record wall and monotonic clocks at broker-job admission."""
    with _clock_lock:
        _job_clocks[job_id] = (_agent_clock(), _suspend_clock())


def end(job_id: str) -> None:
    with _clock_lock:
        _job_clocks.pop(job_id, None)


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
    gap = _sleep_gap(job_id)
    if gap >= SUSPEND_GAP_SECONDS:
        raise CancelRequested(
            f"machine sleep or a wall-clock jump interrupted job {job_id}; "
            f"stopped after a {gap:.1f}s clock gap at a safe checkpoint",
            result=result)
