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
                        Per-step wall clock lives here too, under
                        `step_timings` (see `record_step_timing`).

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
    # Canonical spelling (library/tools/stable_json.py): pipeline_run.json
    # is run state, rebuilt rather than merged, but sorted keys still
    # keep no-op rewrites byte-identical so the tree stays clean.
    from library.tools.stable_json import dump_stable
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        dump_stable(data, f)
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
                     argv: Optional[List[str]] = None,
                     profile: Any = None,
                     breakpoints: Optional[Dict[str, Any]] = None,
                     state: Optional[Dict[str, Any]] = None,
                     carry_run_group: bool = False,
                     ) -> Dict[str, Any]:
    """Replace the run status wholesale at the start of a run.

    `profile` and `breakpoints` are this run's CONFIGURATION - which
    steps it fires and where it stops.  They go on the record because a
    reader that never saw the command line (the Resolve panel, the
    dashboard, the captain tomorrow) otherwise has no way to tell a run
    that stopped at a breakpoint from one that stopped for any other
    reason.

    "Wholesale" used to mean the outgoing account was simply lost, and
    that is how a restart became invisible: a run that halted on a
    contract violation and was re-run 49 seconds later left a status file
    describing one clean pass.  The previous account is now READ before
    it is replaced and carried forward as `restart` plus a bounded
    `run_history`.  See library/tools/run_restart.py.
    """
    from library.tools import run_restart

    previous = read_run_status(project_dir)
    restart = run_restart.classify(previous, state)
    history = list(previous.get("run_history") or [])
    if previous:
        history.append(run_restart.history_entry(previous, restart))
        del history[:-run_restart.MAX_HISTORY]

    # The run GROUP this invocation belongs to. One plan run is several
    # CLI invocations (review gates, `--resume`, single steps), and each
    # one used to start a fresh account - so nothing summed a full plan
    # run end to end. A `--resume` carries the previous run's group id
    # forward; anything else mints a new one. The group's wall is the
    # sum of its invocations' walls, accumulated in `record_run_total`
    # as each invocation ends. An invocation the previous run never
    # ended (interrupted, no `finished_at`) contributes nothing it did
    # not record - `prior_wall_s` is a lower bound, said as one.
    run_group = _carry_or_mint_run_group(previous, carry_run_group)

    # Edit-step input digests (library/tools/edit_input_digest.py): one
    # stamp per step from the run that just ended becomes the baseline
    # this run compares against. Carried forward the way run_history is -
    # the file is replaced wholesale, so anything not carried is lost -
    # and bounded by the step count rather than by history length: it is
    # one small entry per step, not one per run.
    previous_digests = dict(previous.get("step_input_digests") or {})

    record = {
        "pid": os.getpid(),
        "mode": mode,
        "argv": list(argv or []),
        "profile": {
            "name": getattr(profile, "name", "") or "",
            "path": getattr(profile, "path", "") or "",
            "source": getattr(profile, "source", "") or "",
            "adopted": bool(getattr(profile, "adopted", False)),
            "description": getattr(profile, "description", "") or "",
        },
        "breakpoints": dict(breakpoints or {}),
        "started_at": _now(),
        "updated_at": _now(),
        "status": "running",
        "steps_to_run": list(steps_to_run),
        "current_step": None,
        "current_step_started_at": None,
        "last_completed_step": None,
        "held_before_step": None,
        "finished_at": None,
        # How the run BEFORE this one ended, and what this run is
        # therefore a restart of.  `None` when it followed a clean run
        # or when there was no previous run to read.
        "restart": (run_restart.as_record(restart)
                    if restart.is_restart else None),
        "run_history": history,
        # This run's own stamps, filled in as steps complete (see
        # `record_step_input_digest`), and the previous run's, carried
        # above, which is what each stamp is compared against.
        "step_input_digests": {},
        "previous_step_input_digests": previous_digests,
        # What this run COST, filled in as steps run or are reused (see
        # `record_step_timing`). Fresh every run: the previous run's
        # costs lived in the record this call just replaced, and carrying
        # them forward would read as this run's.
        "step_timings": {},
        # Which run GROUP this invocation belongs to (see above), and
        # the wall the group had banked before this invocation started.
        # This invocation's own wall lands in `record_run_total`.
        "run_group": run_group,
    }
    try:
        _write_json(run_status_path(project_dir), record)
    except OSError:
        return {}
    return record


def describe_mode(*, full_auto: Optional[str] = None, auto_mode: bool = False,
                  review_mode: bool = False, resume_mode: bool = False,
                  single_step: Optional[str] = None,
                  from_step: Optional[str] = None,
                  rerun: Optional[List[str]] = None,
                  scope: Any = None,
                  profile: Any = None,
                  breakpoints: Any = None) -> str:
    """A one-line human description of how this run was launched.

    The dashboard prints this back so the captain can see that Start
    really did launch `--full-auto agent` and really did not force review
    gates on all 26 steps - and, since #250, exactly how much of the DAG
    a scoped run left out.  Since the run profile it also says which
    declared configuration this run is under and where it means to stop,
    because both can now come from a file rather than from the words
    somebody typed.
    """
    parts = []
    profile_name = getattr(profile, "name", "") or ""
    if profile_name:
        how = "adopted" if getattr(profile, "adopted", False) else "named"
        parts.append(f"profile {profile_name} ({how})")
    if single_step:
        parts.append(f"single-step {single_step}")
    elif resume_mode:
        parts.append("resume")
    elif from_step:
        parts.append(f"from {from_step}")
    elif scope is not None and getattr(scope, "is_scoped", False):
        selection = getattr(scope, "selection", None)
        target = getattr(selection, "target", None)
        parts.append(f"target {target}" if target else "scoped run")
        parts.append(f"{len(scope.steps_to_run)} of "
                     f"{len(scope.universe)} steps")
    else:
        parts.append("full run")
    parts.append(f"full-auto {full_auto}" if full_auto else "manual LLM")
    if auto_mode:
        parts.append("bridge-auto")
    # Where the run stops. `--review` is the every-step case of the same
    # thing, so it is reported through the breakpoints when they are
    # given, and on its own when they are not.
    if breakpoints is not None:
        if getattr(breakpoints, "every_step", False):
            parts.append("breakpoints at every step")
        elif getattr(breakpoints, "steps", ()):
            parts.append("breakpoints at "
                         + ", ".join(breakpoints.steps))
        else:
            parts.append("no breakpoints")
    else:
        parts.append("review gates on" if review_mode else "review gates off")
    if rerun:
        parts.append("re-running " + " + ".join(rerun))
    supplied = sorted(getattr(scope, "from_external", None) or ())
    if supplied:
        # A run that skipped work because the captain supplied the state
        # has to say so on its own record. Reading `pipeline_run.json`
        # and seeing "1 of 26 steps" with no explanation is the report
        # this line prevents (#260).
        parts.append("state supplied from outside: " + ", ".join(supplied))
    return ", ".join(parts)


# ── Edit-step input digests ─────────────────────────────────────────

def record_step_input_digest(project_dir: str, node_id: str,
                             stamp: Dict[str, Any]) -> Dict[str, Any]:
    """Merge one step's input-digest stamp into this run's record.

    Best-effort like `write_run_status`: a stamp that fails to persist
    must never take down a real run - the comparison degrades to
    "unknown", which the comparison lines say aloud.
    """
    try:
        current = read_run_status(project_dir)
        digests = dict(current.get("step_input_digests") or {})
        digests[node_id] = dict(stamp)
        current["step_input_digests"] = digests
        current["updated_at"] = _now()
        _write_json(run_status_path(project_dir), current)
        return current
    except OSError:
        return {}


# ── Per-step wall clock ─────────────────────────────────────────────

def record_step_timing(project_dir: str, node_id: str,
                       duration_s: Optional[float] = None,
                       reused: bool = False) -> Dict[str, Any]:
    """Merge one step's wall clock into this run's record.

    A step that RAN passes its measured seconds (`duration_s`, a plain
    number - this is a measurement record, not a creative value, so it
    does not go through the decided-value path).  A step that was
    SKIPPED because its recorded output was still good passes
    `reused=True` and no duration: there is nothing honest to time on
    a step that did not run, and the mark is what says the run reused
    rather than rebuilt it.

    Best-effort like `write_run_status`: a timing that fails to persist
    must never take down a real run - the record is then missing a row,
    which a reader sees as an absent step, not as a wrong number.
    """
    try:
        current = read_run_status(project_dir)
        timings = dict(current.get("step_timings") or {})
        if reused:
            timings[node_id] = {"reused": True}
        else:
            timings[node_id] = {"duration_s": round(float(duration_s or 0.0), 1),
                                "reused": False}
        current["step_timings"] = timings
        current["updated_at"] = _now()
        _write_json(run_status_path(project_dir), current)
        return current
    except (OSError, ValueError, TypeError):
        return {}


# ── Run-group wall clock ──────────────────────────────────────────

def new_run_group() -> str:
    """Mint a run-group id: the thing several invocations share.

    Random, not time-ordered: `started_at` already orders invocations,
    and two invocations in one second on one pid must still differ.
    """
    import uuid

    return f"rg-{uuid.uuid4().hex[:12]}"


def _group_wall_of(record: Dict[str, Any]) -> float:
    """The wall a previous run account banked, best-effort 0.0."""
    group = record.get("run_group") or {}
    try:
        prior = float(group.get("prior_wall_s") or 0.0)
    except (TypeError, ValueError):
        prior = 0.0
    try:
        own = float(record.get("invocation_wall_s") or 0.0)
    except (TypeError, ValueError):
        own = 0.0
    return round(max(0.0, prior) + max(0.0, own), 3)


def _carry_or_mint_run_group(previous: Dict[str, Any],
                             carry: bool) -> Dict[str, Any]:
    """This invocation's `run_group` record: carried or fresh.

    Carried only when asked AND the previous account names a group: a
    `--resume` after a deleted status file starts a new group rather
    than inheriting nothing, because a group id that names no first
    invocation cannot be summed.
    """
    group = (previous.get("run_group") or {}) if previous else {}
    group_id = group.get("id") if isinstance(group, dict) else None
    if carry and isinstance(group_id, str) and group_id:
        return {"id": group_id,
                "prior_wall_s": _group_wall_of(previous)}
    return {"id": new_run_group(), "prior_wall_s": 0.0}


def _invocation_wall_s(record: Dict[str, Any]) -> Optional[float]:
    """This invocation's own wall, from its recorded start to its end.

    Both endpoints are recorded, never estimated: None where either is
    missing or unparseable, and the caller then writes nothing rather
    than a guess.
    """
    import datetime

    try:
        start = datetime.datetime.strptime(
            str(record.get("started_at")), "%Y-%m-%dT%H:%M:%S")
        end = datetime.datetime.strptime(
            str(record.get("finished_at")), "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None
    wall = (end - start).total_seconds()
    return round(wall, 1) if wall >= 0 else None


def record_run_total(project_dir: str) -> Dict[str, Any]:
    """Stamp this invocation's wall and its group's running total.

    Reads `started_at`/`finished_at` off the run's own account and
    writes `invocation_wall_s` plus `run_group_wall_s` (the group's
    banked prior wall plus this invocation). Best-effort like every
    other status write: a wall that cannot be computed honestly is left
    unwritten, never estimated, and a write failure never takes down a
    real run.
    """
    try:
        current = read_run_status(project_dir)
        if not current:
            return {}
        wall = _invocation_wall_s(current)
        if wall is None:
            return current
        group = dict(current.get("run_group") or {})
        prior = group.get("prior_wall_s") or 0.0
        try:
            prior = max(0.0, float(prior))
        except (TypeError, ValueError):
            prior = 0.0
        current["invocation_wall_s"] = wall
        current["run_group_wall_s"] = round(prior + wall, 1)
        current["updated_at"] = _now()
        _write_json(run_status_path(project_dir), current)
        return current
    except OSError:
        return {}


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
