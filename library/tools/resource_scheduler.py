"""The machine's resource scheduler: jobs that can coexist do.

One machine, one Resolve. Work that competes for it declares what it
needs as a DEMAND over named resources, and the scheduler admits a job
only while every resource stays within its capacity. Two jobs whose
demands fit together run together; two that do not, serialise. It
replaces the single machine-wide heavy-work mutex, which serialised a
seconds-long Resolve placement behind a minutes-long test gate although
they compete for nothing.

The resources
-------------
`resolve_cursor`  the Resolve instance cursor (capacity 1). The Resolve
                  lease (`library/tools/resolve_lock.py`) stays the
                  authority over the cursor and is always taken FIRST;
                  this row records who holds Resolve alongside the
                  machine's other resources.
`resolve_render`  Resolve's render engine (capacity 1).
`cpu`             cores (`os.cpu_count()`).
`gpu`             the GPU as one tenant: local model inference and a
                  Resolve render each take all of it (capacity 1).
`ram_gb`          physical memory less `RAM_RESERVE_GB` for the OS and
                  the captain's live Resolve.
`disk`            I/O weight (capacity `DISK_CAPACITY`).

`PROFILES` is the declared demand of each kind of heavy work. Its numbers
are DECLARED, not measured: they encode which pairs may coexist (written
beside the table), and they are the table to tune once the KPI
instrumentation measures real contention. `machine` - every resource at
capacity - is the default and the old mutex exactly: a caller that
declares nothing excludes everything.

The store
---------
SQLite in WAL mode beside the old lock directory. Every state change is
one `BEGIN IMMEDIATE` transaction, so admission is atomic across
processes. A job is a row: `waiting` until admitted, then `running`.
Admission is FIFO with backfill: a job runs when it fits beside every
running job AND every job that queued before it, so a stream of small
jobs cannot starve a large one. A row whose process is gone is reaped by
the next transaction; a reused pid only keeps a row alive longer, which
errs toward exclusion, never toward double admission.

The bridge to the old lock
--------------------------
Lanes still on code from before this module take the old mkdir lock at
`HEAVY_LOCK_DIR` and know nothing of this store. So while ANY scheduler
job runs, the scheduler holds that directory (its owner record carries
`"scheduler": true`), and an old-code holder of it counts as a job
demanding the whole machine. Old and new code therefore exclude each
other exactly as two old lanes did, and no two full-suite gates can run
at once across the switch. The bridge goes once no lane runs pre-
scheduler code.

`tests/test_heavy_work_lock.py`.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, Dict, Optional

RESOURCES = ("resolve_cursor", "resolve_render", "cpu", "gpu", "ram_gb",
             "disk")
RAM_RESERVE_GB = 8
DISK_CAPACITY = 4
DB_FILENAME = "resource-scheduler.sqlite3"
OWNER_FILE = "owner"
POLL_SECONDS = 0.5
BUSY_TIMEOUT_MS = 30_000


def _physical_ram_gb() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                   // 2 ** 30)
    except (ValueError, OSError, AttributeError):
        return 16


def capacity() -> Dict[str, int]:
    """What this machine has of each resource."""
    return {
        "resolve_cursor": 1,
        "resolve_render": 1,
        "cpu": os.cpu_count() or 1,
        "gpu": 1,
        "ram_gb": max(1, _physical_ram_gb() - RAM_RESERVE_GB),
        "disk": DISK_CAPACITY,
    }


def profiles() -> Dict[str, Dict[str, int]]:
    """Declared demand per kind of heavy work, on this machine.

    Which pairs coexist (on the 10-core, 24 GB machine: cpu 10, ram 16):
      * gate + gate              no  - each takes more than half the cpu
      * gate + local_vlm         no  - ram 10 + 10 > 16
      * gate + resolve_placement yes - a placement is Resolve seconds
      * gate + resolve_render    no  - cpu 8 + 4 > 10
      * local_vlm + placement    yes
      * local_vlm + render       no  - both take the gpu
      * placement + anything holding the cursor: no (and the Resolve
        lease already serialises them first)
    """
    cap = capacity()
    return {
        "machine": dict(cap),
        "full_suite_gate": {
            "cpu": max(cap["cpu"] - 2, cap["cpu"] // 2 + 1),
            "ram_gb": min(10, cap["ram_gb"]),
            "disk": 2,
        },
        "local_vlm": {
            "gpu": 1,
            "ram_gb": min(10, cap["ram_gb"]),
            "cpu": min(2, cap["cpu"]),
        },
        "resolve_placement": {
            "resolve_cursor": 1,
            "cpu": min(2, cap["cpu"]),
            "ram_gb": min(2, cap["ram_gb"]),
        },
        "resolve_render": {
            "resolve_cursor": 1,
            "resolve_render": 1,
            "gpu": 1,
            "cpu": min(4, cap["cpu"]),
            "ram_gb": min(4, cap["ram_gb"]),
            "disk": 2,
        },
    }


def demand_for(profile: str) -> Dict[str, int]:
    """The demand a named profile declares; an unknown name raises."""
    known = profiles()
    if profile not in known:
        raise ValueError(f"resource scheduler: unknown profile {profile!r}; "
                         f"known: {', '.join(sorted(known))}")
    return {k: v for k, v in known[profile].items() if v}


def covers(held: Dict[str, int], wanted: Dict[str, int]) -> bool:
    """Whether a held grant already includes everything `wanted` needs."""
    return all(held.get(k, 0) >= v for k, v in wanted.items())


def _fits(demands, cap: Dict[str, int]) -> bool:
    total: Dict[str, int] = {}
    for demand in demands:
        for k, v in demand.items():
            total[k] = total.get(k, 0) + v
    return all(total[k] <= cap.get(k, 0) for k in total)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Scheduler:
    """One machine's job table, plus the bridge to the old lock dir."""

    def __init__(self, legacy_dir: Path):
        self.legacy_dir = Path(legacy_dir)
        self.db_path = self.legacy_dir.parent / DB_FILENAME

    # ── store ──
    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=BUSY_TIMEOUT_MS / 1000,
                               isolation_level=None)
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " token TEXT NOT NULL UNIQUE,"
            " owner TEXT NOT NULL,"
            " pid INTEGER NOT NULL,"
            " host TEXT NOT NULL,"
            " state TEXT NOT NULL CHECK (state IN ('waiting', 'running')),"
            " demand TEXT NOT NULL,"
            " enqueued_at REAL NOT NULL,"
            " started_at REAL)")
        return conn

    def _transaction(self, body: Callable[[sqlite3.Connection], object]):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                result = body(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            return result

    @staticmethod
    def _reap(conn: sqlite3.Connection) -> None:
        host = socket.gethostname()
        for row_id, pid, row_host in conn.execute(
                "SELECT id, pid, host FROM jobs").fetchall():
            if row_host == host and not _alive(pid):
                conn.execute("DELETE FROM jobs WHERE id = ?", (row_id,))

    # ── the old lock directory ──
    def _legacy_owner(self) -> Optional[dict]:
        """The old-code holder of the lock dir, None when free or ours."""
        if not self.legacy_dir.exists():
            return None
        try:
            raw = (self.legacy_dir / OWNER_FILE).read_text(encoding="utf-8")
            record = json.loads(raw)
        except (OSError, ValueError):
            # Ownerless or unreadable: an old-code owner mid-publish.
            return {"owner": "unknown owner (owner file missing)"}
        if isinstance(record, dict) and record.get("scheduler"):
            return None
        return record if isinstance(record, dict) else {"owner": str(record)}

    def _hold_legacy_dir(self) -> bool:
        """Make sure the scheduler holds the old lock dir; False if raced."""
        if self.legacy_dir.exists():
            return True
        try:
            self.legacy_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            return False
        tag = uuid.uuid4().hex
        temp = self.legacy_dir / f".{OWNER_FILE}.{tag}.tmp"
        temp.write_text(json.dumps({
            "owner": "resource scheduler (see "
                     "library/tools/resource_scheduler.py)",
            "scheduler": True,
            "token": tag,
            "pid": os.getpid(),
            "host": socket.gethostname(),
        }) + "\n", encoding="utf-8")
        os.replace(temp, self.legacy_dir / OWNER_FILE)
        return True

    def _drop_legacy_dir_if_idle(self, conn: sqlite3.Connection) -> None:
        running = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state = 'running'").fetchone()[0]
        if running or not self.legacy_dir.exists():
            return
        if self._legacy_owner() is not None:
            return  # an old-code owner's directory: never ours to remove
        for path in self.legacy_dir.iterdir():
            path.unlink()
        self.legacy_dir.rmdir()

    # ── jobs ──
    def acquire(self, owner: str, demand: Dict[str, int],
                announce: Callable[[str], None] = print) -> str:
        """Block until `demand` is admitted; return the job's token."""
        cap = capacity()
        if not _fits([demand], cap):
            raise ValueError(f"resource scheduler: {owner!r} demands "
                             f"{demand}, beyond this machine's {cap}")
        token = uuid.uuid4().hex
        self._transaction(lambda conn: conn.execute(
            "INSERT INTO jobs (token, owner, pid, host, state, demand,"
            " enqueued_at) VALUES (?, ?, ?, ?, 'waiting', ?, ?)",
            (token, owner, os.getpid(), socket.gethostname(),
             json.dumps(demand, sort_keys=True), time.time())))
        announced = False
        try:
            while True:
                blockers = self._transaction(
                    lambda conn: self._try_admit(conn, token, demand, cap))
                if not blockers:
                    return token
                if not announced:
                    announce("heavy-work: waiting for " + "; ".join(blockers))
                    announced = True
                time.sleep(POLL_SECONDS)
        except BaseException:
            self._transaction(lambda conn: (
                conn.execute("DELETE FROM jobs WHERE token = ?", (token,)),
                self._drop_legacy_dir_if_idle(conn)))
            raise

    def _try_admit(self, conn, token, demand, cap) -> list:
        """Admit the job in this transaction, or name what it waits for."""
        self._reap(conn)
        mine = conn.execute("SELECT id FROM jobs WHERE token = ?",
                            (token,)).fetchone()
        if mine is None:
            raise RuntimeError("resource scheduler: queued job row vanished")
        ahead = conn.execute(
            "SELECT owner, pid, state, demand FROM jobs WHERE id != ? AND"
            " (state = 'running' OR id < ?) ORDER BY id",
            (mine[0], mine[0])).fetchall()
        legacy = self._legacy_owner()
        if legacy is not None:
            return [f"lock held by {_label(legacy)}"]
        if not _fits([demand] + [json.loads(d) for *_, d in ahead], cap):
            return [f"{state} {owner} (pid {pid})"
                    for owner, pid, state, _ in ahead]
        if not self._hold_legacy_dir():
            return ["lock directory claimed by an old-code lane"]
        conn.execute("UPDATE jobs SET state = 'running', started_at = ?"
                     " WHERE token = ?", (time.time(), token))
        return []

    def release(self, token: str) -> None:
        def body(conn):
            gone = conn.execute("DELETE FROM jobs WHERE token = ?"
                                " AND state = 'running'", (token,)).rowcount
            if not gone:
                raise RuntimeError("resource scheduler: release of a job "
                                   "that is not running; refusing")
            self._reap(conn)
            self._drop_legacy_dir_if_idle(conn)
        self._transaction(body)

    def running_demand(self, token: str) -> Optional[Dict[str, int]]:
        """The demand of a running job with this token, else None."""
        def body(conn):
            self._reap(conn)
            row = conn.execute("SELECT demand FROM jobs WHERE token = ? AND"
                               " state = 'running'", (token,)).fetchone()
            return json.loads(row[0]) if row else None
        return self._transaction(body)

    def jobs(self) -> list:
        """Every live row, oldest first, as dicts."""
        def body(conn):
            self._reap(conn)
            return conn.execute(
                "SELECT owner, pid, state, demand, enqueued_at, started_at"
                " FROM jobs ORDER BY id").fetchall()
        return [{"owner": o, "pid": p, "state": s, "demand": json.loads(d),
                 "enqueued_at": e, "started_at": st}
                for o, p, s, d, e, st in self._transaction(body)]


def _label(owner: dict) -> str:
    label = str(owner.get("owner") or "unknown owner")
    if owner.get("pid") is not None:
        label += f" (pid {owner['pid']}"
        if owner.get("host"):
            label += f"@{owner['host']}"
        label += ")"
    return label
