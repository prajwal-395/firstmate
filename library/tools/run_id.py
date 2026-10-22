"""One run, one id (Lane A1, closes R1).

Three stores each named the run its own way - `pipeline_run.json` kept a
`run_group` but no run id at all, `provenance/runs.jsonl` minted
`YYYYMMDDTHHMMSS-pid`, and perf rows carried whatever the front door
passed - so nothing could join "what did this run cost" to "what did it
change". The runner now mints ONE id at start (`mint_run_id`), exports it
as `REN_RUN_ID`, and all three stores record that same value.

Format is `run-<UTC>-<rand6>`, e.g. `run-20261010T143022Z-a1b2c3`: UTC,
not local time, so two machines never disagree about when a run was, and
a random suffix, not a pid, so two invocations in one second still
differ. The timestamp prefix keeps ids sorting chronologically, which is
what readers of several runs touching one file want.

Readers stay lenient: old provenance rows (`YYYYMMDDTHHMMSS-pid`) and old
status files (no `run_id` at all) still read. Nothing here refuses a
legacy value - `is_run_id` only names which scheme a value came from.

Rules relocated from AGENTS.md 3
--------------------------------
One enumeration, `library/tools/run_id.py`.
"""

from __future__ import annotations

import calendar
import os
import re
import secrets
import time

# The environment name the runner exports. Children (step subprocesses,
# the perf ledger) read the run they belong to off this.
ENV_VAR = "REN_RUN_ID"

# `run-20261010T143022Z-a1b2c3`
_UNIFIED_RE = re.compile(
    r"^run-(?P<date>\d{8})T(?P<time>\d{6})Z-[0-9a-f]{6}$")

# The provenance scheme this replaces: `20260829T123000-12345`
# (local time plus pid). Rows in this shape still read everywhere.
_LEGACY_RE = re.compile(r"^\d{8}T\d{6}-\d+$")


def mint_run_id(now: float | None = None) -> str:
    """Mint the one id for a run about to start.

    `now` is seconds since the epoch, UTC; None means this instant.
    Kept as a parameter so tests can pin the timestamp half without
    waiting on the clock. Uniqueness across same-second invocations
    comes from the random suffix, never from the caller.
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
    return f"run-{stamp}-{secrets.token_hex(3)}"


def started_at(run_id: str | None) -> float | None:
    """When the run that owns this id started, in epoch seconds (UTC).

    The id carries the run's start in its own name, so every process
    that knows the id knows the run's age without reading a file.  A
    legacy id carries no timestamp and reads as `None` - "unknown",
    never a guess.

    The stamp is truncated to the second, so the answer is never LATER
    than the true start: a bound derived from it is never tighter than
    the truth.
    """
    match = _UNIFIED_RE.match(str(run_id or ""))
    if not match:
        return None
    stamp = f"{match.group('date')}{match.group('time')}"
    try:
        return calendar.timegm(time.strptime(stamp, "%Y%m%d%H%M%S"))
    except ValueError:
        return None


def current() -> str | None:
    """The run id this process was started under, if one was exported."""
    value = os.environ.get(ENV_VAR)
    return value if value else None


def is_unified_run_id(value: object) -> bool:
    """True when `value` is an id this module minted."""
    return isinstance(value, str) and _UNIFIED_RE.match(value) is not None


def is_legacy_run_id(value: object) -> bool:
    """True when `value` is a pre-unification provenance run id."""
    return isinstance(value, str) and _LEGACY_RE.match(value) is not None


def is_run_id(value: object) -> bool:
    """True for any run id either scheme produced.

    Readers do not gate on this - an unknown shape still reads as the
    string it is - but tests and audits use it to say which scheme a
    stored value came from.
    """
    return is_unified_run_id(value) or is_legacy_run_id(value)
