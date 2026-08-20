"""
run_control.py - the file protocol behind the dashboard's run controls.

The dashboard and the pipeline runner are separate processes, so every
control the captain presses has to survive a process boundary.  This
module owns the whole vocabulary so the two halves cannot disagree about
a file name or a key name - the failure mode this repo has paid for more
than once (see CLAUDE.md, "Key-name mismatches are the dominant bug
class").

Three files live at the root of a project directory:

    pipeline.pid        written by run_pipeline.py's __main__ guard.
                        Its existence plus a live pid means a run is up.
    pipeline.hold       the handbrake.  Written by the dashboard, read by
                        the runner at the top of every step.  The runner
                        finishes the step it is in, then stops.  It never
                        interrupts a step mid-flight.
    pipeline_run.json   the runner's own account of itself: mode, current
                        step, last completed step, and how it ended.
                        Nothing else may write it.

The handbrake is deliberately advisory.  A control that killed the
process mid-step would leave `pipeline_data.json` describing a step that
half happened, and the next run would have no way to tell.  Holding
between steps means the state on disk is always a real boundary.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PID_FILE = "pipeline.pid"
HOLD_FILE = "pipeline.hold"
RUN_STATUS_FILE = "pipeline_run.json"


# ── Paths ───────────────────────────────────────────────────────────

def pid_path(project_dir: str) -> Path:
    return Path(project_dir) / PID_FILE


def hold_path(project_dir: str) -> Path:
    return Path(project_dir) / HOLD_FILE


def run_status_path(project_dir: str) -> Path:
    return Path(project_dir) / RUN_STATUS_FILE


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


# ── Is a run up? ────────────────────────────────────────────────────

def running_pid(project_dir: str) -> Optional[int]:
    """Return the pid of a live run, or None.

    A pid file whose process is gone is removed, so a crashed run does
    not wedge the dashboard into believing the pipeline is busy forever.
    """
    path = pid_path(project_dir)
    try:
        pid_str = path.read_text().strip()
    except OSError:
        return None
    if not pid_str.isdigit():
        return None
    pid = int(pid_str)
    try:
        os.kill(pid, 0)
    except OSError:
        try:
            path.unlink()
        except OSError:
            pass
        return None
    return pid


def is_running(project_dir: str) -> bool:
    return running_pid(project_dir) is not None


# ── The handbrake ───────────────────────────────────────────────────

def request_hold(project_dir: str, requested_by: str = "dashboard",
                 reason: str = "") -> Dict[str, Any]:
    """Engage the handbrake.  The current step still finishes."""
    record = {
        "requested_at": _now(),
        "requested_by": requested_by,
        "reason": reason,
    }
    _write_json(hold_path(project_dir), record)
    return record


def hold_requested(project_dir: str) -> Optional[Dict[str, Any]]:
    """The hold record if the handbrake is engaged, else None.

    A hold file that is present but unreadable still counts as engaged.
    Failing open here would run the pipeline the captain asked to stop.
    """
    path = hold_path(project_dir)
    if not path.exists():
        return None
    return _read_json(path) or {"requested_at": "", "requested_by": "unknown",
                                "reason": "unparseable hold file"}


def release_hold(project_dir: str) -> bool:
    """Disengage the handbrake.  True if one was engaged."""
    path = hold_path(project_dir)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


# ── The runner's account of itself ──────────────────────────────────

def read_run_status(project_dir: str) -> Dict[str, Any]:
    return _read_json(run_status_path(project_dir)) or {}


def write_run_status(project_dir: str, **fields: Any) -> Dict[str, Any]:
    """Merge `fields` into the run status file.

    Best-effort by design: the runner calls this between steps and a
    failure to write a status file must never take down a real run.
    """
    try:
        current = read_run_status(project_dir)
        current.update(fields)
        current["updated_at"] = _now()
        _write_json(run_status_path(project_dir), current)
        return current
    except OSError:
        return {}


def begin_run_status(project_dir: str, mode: str, steps_to_run: List[str],
                     argv: Optional[List[str]] = None) -> Dict[str, Any]:
    """Replace the run status wholesale at the start of a run."""
    record = {
        "pid": os.getpid(),
        "mode": mode,
        "argv": list(argv or []),
        "started_at": _now(),
        "updated_at": _now(),
        "status": "running",
        "steps_to_run": list(steps_to_run),
        "current_step": None,
        "current_step_started_at": None,
        "last_completed_step": None,
        "held_before_step": None,
        "finished_at": None,
    }
    try:
        _write_json(run_status_path(project_dir), record)
    except OSError:
        return {}
    return record


def describe_mode(*, full_auto: Optional[str] = None, auto_mode: bool = False,
                  review_mode: bool = False, resume_mode: bool = False,
                  single_step: Optional[str] = None,
                  from_step: Optional[str] = None) -> str:
    """A one-line human description of how this run was launched.

    The dashboard prints this back so the captain can see that Start
    really did launch `--full-auto agy` and really did not force review
    gates on all 26 steps.
    """
    parts = []
    if single_step:
        parts.append(f"single-step {single_step}")
    elif resume_mode:
        parts.append("resume")
    elif from_step:
        parts.append(f"from {from_step}")
    else:
        parts.append("full run")
    parts.append(f"full-auto {full_auto}" if full_auto else "manual LLM")
    if auto_mode:
        parts.append("bridge-auto")
    parts.append("review gates on" if review_mode else "review gates off")
    return ", ".join(parts)


# ── Single-step resolution ──────────────────────────────────────────

def next_runnable_step(order: List[str], completed: Any) -> Optional[str]:
    """The one step a `Step` press should advance.

    `order` is the topologically sorted DAG.  Anything already recorded
    complete is skipped, so the returned step's upstreams are satisfied
    and `run_pipeline.py --step <id>` will run exactly it.
    """
    done = set(completed or ())
    for step_id in order:
        if step_id not in done:
            return step_id
    return None
