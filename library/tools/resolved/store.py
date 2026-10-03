"""The broker's job table and receipts: SQLite in WAL mode.

Runtime state, not project history: queue position, who is holding
Resolve and for how long. It lives next to the lease
(`resolve_lock.lock_dir()`), never in a project or in git.

A job left `queued` or `running` by a broker that died is closed as
`failed` when the next broker opens the table - its client lost its
connection with that broker, so nobody is waiting on it any more.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

TERMINAL = ("done", "failed", "rejected", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    priority TEXT NOT NULL,
    mode TEXT NOT NULL,
    executed INTEGER NOT NULL,
    params TEXT NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    timeline TEXT NOT NULL DEFAULT '',
    qualification INTEGER NOT NULL DEFAULT 0,
    coalesce_key TEXT,
    owner TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    subscribers INTEGER NOT NULL DEFAULT 1,
    submitted REAL NOT NULL,
    started REAL,
    finished REAL,
    result TEXT,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_state ON jobs(state);
CREATE TABLE IF NOT EXISTS idempotency_records (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    digest TEXT NOT NULL,
    job_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY (kind, key)
);
"""


class IdempotencyConflict(ValueError):
    """An idempotency key was reused for a different request."""


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False,
                                   isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.execute(
            "UPDATE jobs SET state='failed', finished=?, "
            "error='the broker that held this job exited' "
            "WHERE state IN ('queued', 'running')", (time.time(),))

    def insert(self, job: dict) -> None:
        row = dict(job)
        row["params"] = json.dumps(row["params"], sort_keys=True)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock:
            self._db.execute(f"INSERT INTO jobs ({columns}) VALUES ({marks})",
                             tuple(row.values()))

    def insert_idempotent(self, job: dict, *, kind: str, key: str,
                          digest: str) -> dict | None:
        """Insert a job and its durable key atomically.

        Return the previous job receipt for an identical retry, or None
        after inserting a new job. A key is scoped by job kind so patch ids
        and caller keys on unrelated operations cannot collide.
        """
        row = dict(job)
        row["params"] = json.dumps(row["params"], sort_keys=True)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                previous = self._db.execute(
                    "SELECT digest, job_id FROM idempotency_records"
                    " WHERE kind=? AND key=?", (kind, key)).fetchone()
                if previous is not None:
                    if previous["digest"] != digest:
                        raise IdempotencyConflict(
                            f"idempotency key {key!r} for {kind} was already "
                            "used with different contents")
                    found = self._db.execute(
                        "SELECT * FROM jobs WHERE id=?",
                        (previous["job_id"],)).fetchone()
                    if found is None:
                        raise RuntimeError(
                            f"idempotency record for {kind} {key!r} points "
                            "to a missing job")
                    self._db.execute("COMMIT")
                    return receipt(found)

                self._db.execute(
                    f"INSERT INTO jobs ({columns}) VALUES ({marks})",
                    tuple(row.values()))
                self._db.execute(
                    "INSERT INTO idempotency_records"
                    " (kind, key, digest, job_id) VALUES (?, ?, ?, ?)",
                    (kind, key, digest, row["id"]))
                self._db.execute("COMMIT")
                return None
            except BaseException:
                if self._db.in_transaction:
                    self._db.execute("ROLLBACK")
                raise

    def update(self, job_id: str, **fields) -> None:
        if "result" in fields and fields["result"] is not None:
            fields["result"] = json.dumps(fields["result"], sort_keys=True)
        assignments = ", ".join(f"{name}=?" for name in fields)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {assignments} WHERE id=?",
                             (*fields.values(), job_id))

    def add_subscriber(self, job_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE jobs SET subscribers = subscribers + 1 WHERE id=?",
                (job_id,))

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            row = self._db.execute("SELECT * FROM jobs WHERE id=?",
                                   (job_id,)).fetchone()
        return receipt(row) if row is not None else None

    def recent(self, limit: int = 20) -> list:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM jobs ORDER BY submitted DESC LIMIT ?",
                (limit,)).fetchall()
        return [receipt(row) for row in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()


def receipt(row) -> dict:
    """One job as its receipt: what was asked, what Resolve cost, outcome.

    `wait_seconds` is queue time and `hold_seconds` is the time the job
    held Resolve; either is None until it has happened - an absent
    measurement is never zero.
    """
    job = dict(row)
    job["params"] = json.loads(job["params"])
    job["result"] = json.loads(job["result"]) if job["result"] else None
    job["executed"] = bool(job["executed"])
    job["qualification"] = bool(job["qualification"])
    started, finished = job["started"], job["finished"]
    job["wait_seconds"] = (round(started - job["submitted"], 3)
                           if started is not None else None)
    job["hold_seconds"] = (round(finished - started, 3)
                           if started is not None and finished is not None
                           else None)
    return job
