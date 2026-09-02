"""run_restart.py - a run that was restarted, recorded where the outputs are.

On 29 Aug 2026 project 001 halted at 12:32:35 on the spine contract and
resumed at 12:33:24 - a 49-second gap in which a human or an agent re-ran
it.  Nothing in `pipeline_data.json`, the assembly manifest or
`pipeline_output/reasoning/` recorded that the run had been interrupted
and restarted.  The rejected 52.1s spine and its rejection existed only
in `logs/pipeline_log.jsonl`.  Anyone reading the outputs afterwards -
including an audit of this pipeline - saw a clean single pass.

The reason the evidence went missing is one line of behaviour:
`run_control.begin_run_status` REPLACES `pipeline_run.json` wholesale, so
the account of how the previous run ended is overwritten by the account
of the run that followed it.

This module reads the outgoing account BEFORE it is replaced and turns it
into a RESTART record: what the previous run was, how it ended, and the
cause where the cause is knowable.  The record goes three places - the
run status file, the provenance ledger's run record, and the pipeline
state - because the complaint was specifically that the outputs did not
carry it.

**Nothing here is inferred beyond what a file says.**  A previous run
whose status file says `running` did not report an ending at all: that is
`interrupted`, and it is a different claim from `after_failure`.  A
previous run this cannot see is `unknown`, never `clean`.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# How the previous run ended, as read off its own account of itself.
CLEAN = "clean"                  # it finished and reported success
AFTER_FAILURE = "after_failure"  # it finished and reported a failure
INTERRUPTED = "interrupted"      # it never reported an ending at all
AFTER_HOLD = "after_hold"        # the captain pulled the handbrake
AFTER_GATE = "after_gate"        # it stopped at a review gate
UNKNOWN = "unknown"              # there is no previous account to read

BASES = (CLEAN, AFTER_FAILURE, INTERRUPTED, AFTER_HOLD, AFTER_GATE, UNKNOWN)

# A restart is only worth recording when the previous run did not end
# cleanly.  A second run after a clean one is a new run, not a restart.
RESTART_BASES = frozenset({AFTER_FAILURE, INTERRUPTED, AFTER_HOLD,
                           AFTER_GATE})

# How many predecessors the status file keeps.  Bounded so a project that
# is re-run fifty times does not grow an unbounded record in a file the
# dashboard reads on every poll; the provenance ledger is append-only and
# keeps the lot.
MAX_HISTORY = 20

# The state key.  One spelling.
STATE_KEY = "run_restarts"


@dataclass
class Restart:
    """One run, and what the run before it left behind."""

    basis: str
    previous_mode: str = ""
    previous_started_at: str = ""
    previous_finished_at: str = ""
    previous_status: str = ""
    previous_last_completed_step: str = ""
    stopped_at_step: str = ""
    """The step the previous run was in, or stopped before.

    For an INTERRUPTED run this is `current_step` - what was in flight
    when the account stopped being updated.  For a hold it is the step it
    held before.
    """
    cause: str = ""
    """Why the previous run ended, in its own words where it has any."""
    recorded_at: str = ""

    @property
    def is_restart(self) -> bool:
        return self.basis in RESTART_BASES


def _first_str(record: dict, *keys: str) -> str:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def classify(previous: Optional[dict], state: Optional[dict] = None) -> Restart:
    """Read the outgoing run status into a restart record.

    `state` is the pipeline state, which is where a failure's own words
    live (`step_errors`).  The status file records THAT a run failed; the
    state records WHAT it failed on.  Neither is guessed from the other.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if not previous:
        return Restart(basis=UNKNOWN, recorded_at=now)

    status = (previous.get("status") or "").lower()
    summary_status = (previous.get("summary_status") or "").lower()
    held_before = _first_str(previous, "held_before_step")
    paused_at = _first_str(previous, "paused_at_gate")
    current = _first_str(previous, "current_step")
    finished = _first_str(previous, "finished_at")

    common = dict(
        previous_mode=previous.get("mode") or "",
        previous_started_at=previous.get("started_at") or "",
        previous_finished_at=finished,
        previous_status=status or summary_status,
        previous_last_completed_step=_first_str(
            previous, "last_completed_step"),
        recorded_at=now,
    )

    if status == "held" or held_before:
        return Restart(basis=AFTER_HOLD, stopped_at_step=held_before,
                       cause=(previous.get("hold") or {}).get("reason", "")
                             or "the handbrake was engaged",
                       **common)

    if status == "gate_pending" or paused_at:
        return Restart(basis=AFTER_GATE, stopped_at_step=paused_at,
                       cause="stopped at a review gate", **common)

    # No ending was ever written.  The process died, was killed, or the
    # machine went away; the account stops mid-run and says so by saying
    # nothing.  This is NOT read as a failure, because the run never
    # reported one.
    if status == "running" or not finished:
        step = current or _first_str(previous, "last_completed_step")
        return Restart(
            basis=INTERRUPTED, stopped_at_step=current,
            cause=("the previous run wrote no ending - it was interrupted"
                   + (f" during {step}" if step else "")),
            **common)

    if status in ("failed", "error") or summary_status == "failed":
        step, message = _failure_cause(state)
        return Restart(basis=AFTER_FAILURE, stopped_at_step=step,
                       cause=message or "the previous run reported FAILED",
                       **common)

    return Restart(basis=CLEAN, **common)


def _failure_cause(state: Optional[dict]) -> tuple:
    """The step and the words, out of the state file's own record.

    `failed_steps` is current state and `step_errors` carries the
    message; both are what `_record_step_failure` wrote.  A state with no
    recorded failure yields no cause rather than an invented one.
    """
    if not isinstance(state, dict):
        return "", ""
    failed = state.get("failed_steps") or []
    errors = state.get("step_errors") or {}
    for step in failed:
        message = errors.get(step)
        if message:
            return step, str(message).strip().splitlines()[0][:500]
    if failed:
        return str(failed[0]), ""
    for step, message in errors.items():
        return str(step), str(message).strip().splitlines()[0][:500]
    return "", ""


def as_record(restart: Restart) -> dict:
    return asdict(restart)


def history_entry(previous: dict, restart: Restart) -> dict:
    """The one-line account of the previous run kept in the status file."""
    return {
        "mode": restart.previous_mode,
        "started_at": restart.previous_started_at,
        "finished_at": restart.previous_finished_at,
        "status": restart.previous_status,
        "basis": restart.basis,
        "stopped_at_step": restart.stopped_at_step,
        "cause": restart.cause,
        "steps_to_run": len(previous.get("steps_to_run") or []),
    }


def append_to_state(state: dict, restart: Restart) -> dict:
    """Put the record where an audit of the OUTPUTS will find it.

    The complaint that produced this module was not that no file recorded
    the restart - `logs/pipeline_log.jsonl` half did - but that nothing a
    reader of the outputs opens recorded it.  `pipeline_data.json` is
    that file.
    """
    if not restart.is_restart:
        return state
    rows = state.setdefault(STATE_KEY, [])
    rows.append(as_record(restart))
    del rows[:-MAX_HISTORY]
    return state


def summary_lines(restart: Optional[Restart]) -> List[str]:
    """What the run header prints.  It reports; it decides nothing."""
    if restart is None or not restart.is_restart:
        return []
    where = f" at {restart.stopped_at_step}" if restart.stopped_at_step else ""
    return [
        f"  Restart: this run follows one that ended "
        f"{restart.basis.replace('_', ' ')}{where}",
        f"           cause: {restart.cause or 'not recorded'}",
    ]


# ── Reading it back for runs that were never recorded ────────────────

def reconstruct_from_ledger(runs: List[Any]) -> List[dict]:
    """Restarts inferable from the provenance run ledger, after the fact.

    The ledger is append-only and predates this module, so runs recorded
    before it can be partly recovered: consecutive run records where the
    earlier one ended in anything but `success` are restarts.

    **What cannot be recovered is the CAUSE.**  The ledger records that a
    run ended and its status; the step and the words lived in the state
    file, and the run that followed overwrote them.  Every row here
    therefore carries `cause: ""` and a basis of `after_failure` or
    `interrupted` and says nothing more - a reconstruction that guessed
    the cause would be exactly the invented attribution this repository's
    provenance module exists not to make.
    """
    rows = []
    ordered = sorted(runs, key=lambda r: getattr(r, "run_id", ""))
    for previous, following in zip(ordered, ordered[1:]):
        status = (getattr(previous, "status", "") or "").lower()
        if status in ("success", ""):
            if status == "" and not getattr(previous, "ended_at", ""):
                rows.append({
                    "run_id": getattr(following, "run_id", ""),
                    "follows_run_id": getattr(previous, "run_id", ""),
                    "basis": INTERRUPTED,
                    "previous_status": "",
                    "cause": "",
                    "reconstructed": True,
                })
            continue
        rows.append({
            "run_id": getattr(following, "run_id", ""),
            "follows_run_id": getattr(previous, "run_id", ""),
            "basis": AFTER_FAILURE,
            "previous_status": status,
            "cause": "",
            "reconstructed": True,
        })
    return rows
