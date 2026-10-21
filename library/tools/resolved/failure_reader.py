"""What the broker said last, read from the files it leaves behind.

A broker that dies mid-request leaves the client with one line -
"ren-resolved closed the connection" - and nothing else.  That line
cannot be told apart from a transient refusal, and a lane cannot say
which job died or what it died of.

The broker already leaves two records of its own life beside its
socket: the JOB TABLE (every job it accepted, with its state and its
error) and its LOG (its stdout and stderr).  This module reads both
READ-ONLY, so a client can name the job and the error without opening
the broker's database for writing and without starting a broker.

Nothing here opens the job table for writing: `JobStore` closes every
job a dead broker left queued, so a client that constructed one would
close other lanes' work.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from library.tools.resolved import broker_paths

#: How many log lines the last-error answer carries.  Enough to name
#: the failure, few enough that a refusal stays one line.
LOG_TAIL_LINES = 8


def _socket_dir(socket=None) -> Path:
    """The directory the broker serving on `socket` keeps its files in."""
    return broker_paths.dir_for(socket or broker_paths.socket_path())


def read_job(job_id: str, socket=None) -> dict | None:
    """One job's receipt, read from the broker's table without writing.

    None where the table cannot be read or holds no such job: an
    unreadable table is UNKNOWN, never "the job succeeded".
    """
    if not job_id:
        return None
    path = broker_paths.db_path(socket)
    if not Path(path).exists():
        return None
    uri = f"file:{Path(path).as_posix()}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error:
        return None
    try:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT id, kind, state, error, owner, submitted, started, "
            "finished, result FROM jobs WHERE id=?",
            (str(job_id),)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    return dict(row)


def read_log_tail(socket=None, lines: int = LOG_TAIL_LINES) -> list:
    """The broker's last log lines, oldest first."""
    path = broker_paths.dir_for(socket or broker_paths.socket_path()) / (
        broker_paths.LOG_NAME)
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows = [line for line in text.splitlines() if line.strip()]
    return rows[-int(lines):]


def _job_line(job: dict) -> str:
    """One line about the job the broker was holding."""
    state = job.get("state") or "unknown"
    error = str(job.get("error") or "").strip()
    line = f"job {job.get('id')} ({job.get('kind') or 'unknown'}) is {state}"
    if error:
        line += f": {error}"
    return line


def describe(socket=None, job_id: str = "") -> str:
    """The broker's last error and the job it happened on, as one line.

    Empty where the broker left nothing to read: the caller then says
    only what it knows, rather than inventing a cause.
    """
    parts = []
    job = read_job(job_id, socket) if job_id else None
    if job is not None:
        parts.append(_job_line(job))
    tail = read_log_tail(socket)
    if tail:
        parts.append("broker log: " + " | ".join(tail[-3:]))
    return "; ".join(parts)
