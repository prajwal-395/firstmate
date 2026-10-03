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

Capability phases declare their resource demand on
`operations.Operation.execution`; this module derives the profiles it
admits. An exclusive Resolve phase also reserves `resolve_cursor`, so
the resource scheduler and Resolve router cannot disagree. `machine` is
the default and the old mutex exactly. `full_suite_gate` is the one
non-capability workload profile: it is a test runner, not a pipeline
capability. Its 6 GB RAM ceiling was measured at 5.30 GB resident
across 243 samples. Its CPU demand stays declared: a noisy shared-machine
run showed what the gate obtained, not what it asked for.

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

What a grant really used
------------------------
Every grant is sampled while it runs (`TreeSampler`) and kept in the
`grants` table when it is released;
`python3 -m library.tools.heavy_work_lock history` sets the measured peak
cores and resident memory beside each profile's declaration, which is
how a declaration above is checked.

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

`tests/unit/context/test_concurrency.py`.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, Dict, Optional

RESOURCES = ("resolve_cursor", "resolve_render", "cpu", "gpu", "ram_gb",
             "disk")
RAM_RESERVE_GB = 8
DISK_CAPACITY = 4
# Resolve's own process, by executable name (`TreeSampler`).
RESOLVE_PROCESS = "Resolve"
DB_FILENAME = "resource-scheduler.sqlite3"
OWNER_FILE = "owner"
POLL_SECONDS = 0.5
BUSY_TIMEOUT_MS = 30_000


class Cancelled(Exception):
    """A waiter's `cancelled` answered True before it was admitted."""


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


def _capability_demand(phase, cap: Dict[str, int]) -> Dict[str, int]:
    """Materialize one capability phase's machine-clamped demand."""
    demand = {resource: min(amount, cap[resource])
              for resource, amount in phase.resources}
    if phase.resolve_mode == "exclusive":
        demand["resolve_cursor"] = 1
    return {resource: amount for resource, amount in demand.items() if amount}


def profiles() -> Dict[str, Dict[str, int]]:
    """Derived scheduler profiles, keyed by capability id and phase."""
    from library.tools import capabilities

    cap = capacity()
    declared = {
        "machine": dict(cap),
        # The full-suite runner is infrastructure, not a pipeline capability.
        "full_suite_gate": {
            "cpu": max(cap["cpu"] - 2, cap["cpu"] // 2 + 1),
            "ram_gb": min(6, cap["ram_gb"]),
            "disk": 2,
        },
    }
    for capability in capabilities.all():
        for phase in capability.execution.phases:
            demand = _capability_demand(phase, cap)
            if demand:
                declared[f"{capability.id}:{phase.name}"] = demand
    return declared


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
        # What each finished grant really used, for checking `profiles()`.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS grants ("
            " owner TEXT NOT NULL,"
            " profile TEXT NOT NULL,"
            " demand TEXT NOT NULL,"
            " enqueued_at REAL NOT NULL,"
            " started_at REAL NOT NULL,"
            " finished_at REAL NOT NULL,"
            " cpu_s REAL,"
            " peak_cores REAL,"
            " peak_rss_gb REAL,"
            " samples INTEGER,"
            " resolve_peak_cores REAL,"
            " resolve_peak_rss_gb REAL)")
        have = {row[1] for row in conn.execute("PRAGMA table_info(grants)")}
        for column in ("resolve_peak_cores", "resolve_peak_rss_gb"):
            if column not in have:     # a table from before the column
                conn.execute(f"ALTER TABLE grants ADD COLUMN {column} REAL")
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
                announce: Callable[[str], None] = print,
                cancelled: Optional[Callable[[], bool]] = None) -> str:
        """Block until `demand` is admitted; return the job's token.

        `cancelled` is polled between attempts; once it answers True the
        wait ends with `Cancelled`, its row removed. It is the way to
        stop a waiter from a signal handler: a handler that RAISES can
        land inside the admitting transaction, after the grant is taken
        and before anyone holds its token to release it.
        """
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
                if cancelled is not None and cancelled():
                    raise Cancelled(f"resource scheduler: {owner!r} stopped "
                                    "waiting")
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

    def release(self, token: str, profile: str = "",
                measured: Optional[Dict[str, float]] = None) -> None:
        """End a running job; keep what it used in `grants`."""
        def body(conn):
            row = conn.execute(
                "SELECT owner, demand, enqueued_at, started_at FROM jobs"
                " WHERE token = ? AND state = 'running'", (token,)).fetchone()
            if row is None:
                raise RuntimeError("resource scheduler: release of a job "
                                   "that is not running; refusing")
            conn.execute("DELETE FROM jobs WHERE token = ?", (token,))
            used = measured or {}
            try:
                _record_grant(conn, row, profile, used)
            except sqlite3.Error:
                # A measurement that fails to persist never fails the
                # release (seen 2026-10-02: a waiter on older code met a
                # newer table and the gate's grant raised on release).
                pass
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

    def history(self, since: float = 0.0) -> list:
        """Every finished grant since `since`, oldest first, as dicts."""
        def body(conn):
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(
                "SELECT * FROM grants WHERE finished_at >= ?"
                " ORDER BY finished_at", (since,)).fetchall()]
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


class TreeSampler:
    """What one grant's process tree really uses, sampled while it runs.

    Every `SAMPLE_SECONDS` one `ps` over the machine: the tree is this
    process and every descendant. Resident memory is SUMMED across the
    tree - a gate's pytest workers each hold their own - and cores are
    the tree's CPU-time growth over the interval, so a burst between
    samples is averaged into its interval, never missed. A process that
    is not a descendant (a model server the work talks to over a socket)
    is not in the tree, and its use is not measured here. Resolve is the
    exception that matters: with `watch_resolve` (a grant demanding the
    cursor) its own process is sampled the same way into
    `resolve_peak_cores` / `resolve_peak_rss_gb`, kept apart because the
    captain's live use of Resolve lands there too. Its cores are sound;
    its RSS is NOT its memory - macOS keeps most of Resolve's in GPU and
    compressed pages `ps` does not count (0.06 GB RSS with a project
    open), so a ram declaration for Resolve needs a footprint reading.
    `cpu_s` is the grant's whole CPU time from `getrusage`.
    """

    SAMPLE_SECONDS = 2.0

    def __init__(self, watch_resolve: bool = False) -> None:
        import resource
        self.watch_resolve = watch_resolve
        self._resolve_last: Dict[int, float] = {}
        self.resolve_peak_cores = 0.0
        self.resolve_peak_rss_gb = 0.0
        self._resource = resource
        self._cpu0 = self._cpu()
        self._stop = threading.Event()
        self._last: Dict[int, float] = {}
        self._last_at = 0.0
        self.peak_cores = 0.0
        self.peak_rss_gb = 0.0
        self.samples = 0
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="heavy-work-sampler")
        self._thread.start()

    def _cpu(self) -> float:
        total = 0.0
        for who in (self._resource.RUSAGE_SELF,
                    self._resource.RUSAGE_CHILDREN):
            usage = self._resource.getrusage(who)
            total += usage.ru_utime + usage.ru_stime
        return total

    def _loop(self) -> None:
        while True:
            self._sample()
            if self._stop.wait(self.SAMPLE_SECONDS):
                return

    def _sample(self) -> None:
        try:
            out = subprocess.run(
                ["ps", "-A", "-o", "pid=,ppid=,rss=,time=,comm="],
                capture_output=True, encoding="utf-8", timeout=10,
                check=False).stdout
        except (OSError, subprocess.SubprocessError):
            return
        children: Dict[int, list] = {}
        stats: Dict[int, tuple] = {}
        resolve: set = set()
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) != 5:
                continue
            if os.path.basename(parts[4]) == RESOLVE_PROCESS:
                resolve.add(int(parts[0]))
            try:
                pid, ppid, rss_kb = int(parts[0]), int(parts[1]), int(parts[2])
                cpu = _cpu_seconds(parts[3])
            except ValueError:
                continue
            children.setdefault(ppid, []).append(pid)
            stats[pid] = (rss_kb, cpu)
        tree, frontier = set(), [os.getpid()]
        while frontier:
            pid = frontier.pop()
            if pid in tree:
                continue
            tree.add(pid)
            frontier.extend(children.get(pid, []))
        now = time.monotonic()
        rss = sum(stats[pid][0] for pid in tree if pid in stats)
        cpu = {pid: stats[pid][1] for pid in tree if pid in stats}
        theirs = {pid: stats[pid][1] for pid in resolve if pid in stats}
        if self._last_at:
            elapsed = max(1e-6, now - self._last_at)
            self.peak_cores = max(self.peak_cores,
                                  _grown(self._last, cpu) / elapsed)
            if self.watch_resolve:
                self.resolve_peak_cores = max(
                    self.resolve_peak_cores,
                    _grown(self._resolve_last, theirs) / elapsed)
        self._last, self._resolve_last, self._last_at = cpu, theirs, now
        self.peak_rss_gb = max(self.peak_rss_gb, rss / 2 ** 20)
        if self.watch_resolve:
            self.resolve_peak_rss_gb = max(
                self.resolve_peak_rss_gb,
                sum(stats[pid][0] for pid in theirs) / 2 ** 20)
        self.samples += 1

    def stop(self) -> Dict[str, float]:
        """The peaks so far. A release never waits on a measurement: a
        sample still in flight on a starved machine is left behind."""
        self._stop.set()
        self._thread.join(timeout=0.5)
        return {"cpu_s": round(self._cpu() - self._cpu0, 1),
                "peak_cores": round(self.peak_cores, 2),
                "peak_rss_gb": round(self.peak_rss_gb, 2),
                "samples": self.samples,
                **({"resolve_peak_cores": round(self.resolve_peak_cores, 2),
                    "resolve_peak_rss_gb": round(self.resolve_peak_rss_gb,
                                                 2)}
                   if self.watch_resolve else {})}


def _grown(before: Dict[int, float], after: Dict[int, float]) -> float:
    """CPU seconds a set of processes gained between two samples."""
    return sum(max(0.0, value - before.get(pid, 0.0))
               for pid, value in after.items())


def _cpu_seconds(text: str) -> float:
    """`ps` TIME, `[[DD-]HH:]MM:SS.ss`, as seconds."""
    days, _, clock = text.rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86400 if days else 0)


def _record_grant(conn, row, profile: str, used: Dict[str, float]) -> None:
    """Keep one finished grant's measured use in `grants`."""
    conn.execute(
        "INSERT INTO grants (owner, profile, demand, enqueued_at,"
        " started_at, finished_at, cpu_s, peak_cores, peak_rss_gb,"
        " samples, resolve_peak_cores, resolve_peak_rss_gb)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row[0], profile, row[1], row[2], row[3], time.time(),
         used.get("cpu_s"), used.get("peak_cores"), used.get("peak_rss_gb"),
         used.get("samples"), used.get("resolve_peak_cores"),
         used.get("resolve_peak_rss_gb")))


def _label(owner: dict) -> str:
    label = str(owner.get("owner") or "unknown owner")
    if owner.get("pid") is not None:
        label += f" (pid {owner['pid']}"
        if owner.get("host"):
            label += f"@{owner['host']}"
        label += ")"
    return label
