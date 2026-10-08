"""Cooperative cancellation markers for jobs that declare a safe stop path.

The broker owns admission and queueing. A running job can only be stopped
when its runner checks this marker and has a cleanup path; this module
provides the worker-side signal without ever terminating a worker process.
"""

from __future__ import annotations

from pathlib import Path


class CancelRequested(Exception):
    """The broker requested a safe stop at a runner checkpoint."""


def marker_path(job_id: str) -> Path:
    from library.tools.resolve_lock import lock_dir
    return lock_dir() / "ren-resolved-cancel" / job_id


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


def checkpoint(job_id: str) -> None:
    if requested(job_id):
        raise CancelRequested(f"job {job_id} stopped at a safe checkpoint")
