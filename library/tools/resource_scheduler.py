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
the default and the old mutex exactly. Two workload profiles are not
capabilities. `full_suite_gate` is a test runner. Its 8 GB RAM ceiling
was measured at 6.69 GiB process-tree RSS peak at -n 6 across the
2026-10-13 worker-count benchmark (the old 6 GB reservation predates
that measurement). Its CPU demand
stays declared: a noisy shared-machine run showed what the gate
obtained, not what it asked for. `temporal.index:image_defects` caps VI-03's
streaming source sampler at 2 GB. `reel_preview` is `ren preview`'s
headless engine render and proxy encode (`library/tools/reel_preview.py`),
an explicit verb rather than a capability like `ren deliver`. It takes
the whole GPU, so a preview never runs beside a Resolve render or a
model pass: one heavy media job at a time on the captain's Mac.

The store
---------
SQLite in WAL mode beside the old lock directory. Every state change is
one `BEGIN IMMEDIATE` transaction, so admission is atomic across
processes. A job is a row: `waiting` until admitted, then `running`.
Admission is FIFO with backfill: a job runs when it fits beside every
running job AND every job that queued before it, so a stream of small
jobs cannot starve a large one. A dead holder is reaped by the next
transaction, on every acquire and on every jobs listing: pid liveness
(zombies read as dead) plus the pid's wall-clock start, so a recycled
number never keeps a dead grant alive; and a heartbeat lease
(`LEASE_SECONDS`, refreshed every `HEARTBEAT_SECONDS` and on every
wait poll), so a holder whose process is gone frees its grant even when
its number was recycled. An abnormal exit releases its grants on a
clean interpreter exit and lets the lease expire past `kill -9`. New
rows carry a stable machine identity, so a network hostname change
cannot strand a dead grant; legacy rows with an old hostname are also
reaped when their pid is gone.

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
             "disk", *("native_" + name.replace("-", "_") for name in (
                 "ren-graphics-fframes", "resolve-control", "ren-resolved",
                 "resolve-activity-overlay", "ren_native")))
NATIVE_PARTS = tuple(resource.removeprefix("native_")
                     for resource in RESOURCES if resource.startswith("native_"))
RAM_RESERVE_GB = 8
DISK_CAPACITY = 4
# Resolve's own process, by executable name (`TreeSampler`).
RESOLVE_PROCESS = "Resolve"
DB_FILENAME = "resource-scheduler.sqlite3"
OWNER_FILE = "owner"
POLL_SECONDS = 0.5
BUSY_TIMEOUT_MS = 30_000
STALL_SECONDS = 600
LEASE_SECONDS = 60.0
HEARTBEAT_SECONDS = 15.0


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
        **{"native_" + part: 1 for part in NATIVE_PARTS},
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
            "ram_gb": min(8, cap["ram_gb"]),
            "disk": 2,
        },
        # `ren preview` (module docstring): a bounded engine render plus
        # one proxy encode, serialised against every other GPU tenant.
        "reel_preview": {
            "cpu": min(4, cap["cpu"]),
            "ram_gb": min(4, cap["ram_gb"]),
            "disk": 2,
            "gpu": 1,
        },
    }
    for part in NATIVE_PARTS:
        declared[f"native_refresh:{part.replace('_', '-')}" ] = {
            "cpu": min(2, cap["cpu"]),
            "ram_gb": min(4, cap["ram_gb"]),
            "disk": 1,
            "native_" + part: 1,
        }
    for capability_id, execution in capabilities.executions():
        for phase in execution.phases:
            demand = _capability_demand(phase, cap)
            if demand:
                declared[f"{capability_id}:{phase.name}"] = demand
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


def _pid_stat(pid: int) -> str:
    """One `ps` STAT for `pid`, empty when the pid is gone."""
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, encoding="utf-8", timeout=3,
            check=False).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return out


def _is_zombie(pid: int) -> bool:
    """Whether `pid` is a zombie awaiting its parent's waitpid.

    A zombie still answers `kill(pid, 0)` but holds nothing: the process
    has exited and only its table slot remains. It reads as dead, the
    way `tests/conftest.py` already treats broker pids.
    """
    return _pid_stat(pid).startswith("Z")


def _alive(pid: int) -> bool:
    if _is_zombie(pid):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, OverflowError):
        return False
    return True


def _pid_start(pid: int) -> str:
    """The wall-clock start of `pid`, empty when the pid is gone.

    `ps -o lstart=` is stable across a boot and distinct across pid
    reuse: a row whose recorded start differs from the live pid's start
    is a different process wearing a recycled number. Compared stripped,
    because `ps` pads the field. Empty on any failure, so a missing
    reading never blocks reaping by liveness or lease.
    """
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True, encoding="utf-8", timeout=3,
            check=False).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return ""
    return " ".join(out.split())


def _holder_dead(pid: int, recorded_start: str | None) -> bool:
    """Whether the holder recorded as `pid`/`recorded_start` is gone.

    Dead when the pid is gone (including zombies), or when the live
    pid's start differs from the recorded one (the number was recycled
    for a new process). A row with no recorded start predates the
    identity column and falls back to liveness alone.
    """
    if not _alive(pid):
        return True
    if recorded_start:
        live_start = _pid_start(pid)
        if live_start and live_start != recorded_start:
            return True
    return False


_MACHINE_ID_CACHE: str | None = None


def _machine_id() -> str:
    """A stable local identity that does not change with network naming.

    `uuid.getnode()` is NOT stable here: measured 2026-10-08 returning a
    different random value per process (no usable MAC), so two processes
    disagreed about who owned a row and a dead grant survived every reap
    (job 1845). This prefers the stable hardware UUID (`IOPlatformUUID`
    on macOS, `/etc/machine-id` on Linux) and falls back to a persisted
    file, so every process on the machine agrees. Rows written by the old
    `mac-<getnode>` scheme read as legacy and are reaped by pid death
    alone.
    """
    global _MACHINE_ID_CACHE
    if _MACHINE_ID_CACHE:
        return _MACHINE_ID_CACHE
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, encoding="utf-8", timeout=5,
            check=False).stdout
        import re

        match = re.search(
            r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out or "")
        if match:
            _MACHINE_ID_CACHE = f"hw-{match.group(1)}"
            return _MACHINE_ID_CACHE
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            text = Path(candidate).read_text(encoding="utf-8").strip()
            if text:
                _MACHINE_ID_CACHE = f"machine-{text.split()[0]}"
                return _MACHINE_ID_CACHE
        except OSError:
            continue
    try:
        store = (Path(os.environ.get("VEP_MACHINE_ID_FILE", ""))
                 if os.environ.get("VEP_MACHINE_ID_FILE") else
                 Path.home() / ".local" / "share" / "vep" / "machine-id")
        try:
            text = store.read_text(encoding="utf-8").strip()
            if text:
                _MACHINE_ID_CACHE = text.split()[0]
                return _MACHINE_ID_CACHE
        except OSError:
            pass
        fresh = f"local-{uuid.uuid4().hex}"
        try:
            store.parent.mkdir(parents=True, exist_ok=True)
            store.write_text(fresh + "\n", encoding="utf-8")
        except OSError:
            pass
        _MACHINE_ID_CACHE = fresh
        return fresh
    except (OSError, ValueError):
        _MACHINE_ID_CACHE = "local"
        return "local"


_heartbeats_lock = threading.Lock()
_heartbeats: dict[str, tuple[str, threading.Event, threading.Thread]] = {}
_exit_hook_installed = False


def _heartbeat_loop(db_path: str, token: str, stop: threading.Event) -> None:
    """Refresh one running job's lease until it is released.

    Daemon thread, so a `kill -9` ends the heartbeat with the process
    and the lease expires: the reaper then frees the grant without ever
    needing the dead holder's cooperation. Interval reads the module
    constants at each loop so tests can shorten the lease.
    """
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            with closing(sqlite3.connect(
                    db_path, timeout=BUSY_TIMEOUT_MS / 1000)) as conn:
                conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                with conn:
                    conn.execute(
                        "UPDATE jobs SET heartbeat_at = ? WHERE token = ?",
                        (time.time(), token))
        except sqlite3.Error:
            continue
        except OSError:
            return


def _start_heartbeat(db_path: Path, token: str) -> None:
    """Begin leasing `token`; the reaper owns it past this process."""
    global _exit_hook_installed
    stop = threading.Event()
    thread = threading.Thread(
        target=_heartbeat_loop, args=(str(db_path), token, stop),
        daemon=True, name=f"heavy-work-heartbeat-{token[:8]}")
    with _heartbeats_lock:
        _heartbeats[token] = (str(db_path), stop, thread)
        if not _exit_hook_installed:
            import atexit

            atexit.register(_release_at_exit)
            _exit_hook_installed = True
    thread.start()


def _stop_heartbeat(token: str) -> None:
    """End the lease thread for `token`; the row's own release follows."""
    with _heartbeats_lock:
        entry = _heartbeats.pop(token, None)
    if entry is not None:
        _, stop, _ = entry
        stop.set()


def _release_at_exit() -> None:
    """Best-effort release of grants this process still holds at exit.

    An exception path that forgets `release()` still frees the machine
    on a clean interpreter exit (raising inside the grant, `sys.exit`
    under it). A `kill -9` never reaches here: the heartbeat stops with
    the process and the lease expires instead. Never raises.
    """
    with _heartbeats_lock:
        pending = list(_heartbeats.items())
    for token, (db_path, stop, _) in pending:
        stop.set()
        try:
            with closing(sqlite3.connect(
                    db_path, timeout=2)) as conn:
                with conn:
                    row = conn.execute(
                        "SELECT owner, demand, enqueued_at, started_at FROM"
                        " jobs WHERE token = ? AND state = 'running'",
                        (token,)).fetchone()
                    if row is None:
                        continue
                    conn.execute("DELETE FROM jobs WHERE token = ?",
                                 (token,))
                    try:
                        _record_grant(conn, row, "", {})
                    except sqlite3.Error:
                        pass
        except (sqlite3.Error, OSError):
            continue
    with _heartbeats_lock:
        _heartbeats.clear()


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
            " machine_id TEXT NOT NULL DEFAULT '',"
            " state TEXT NOT NULL CHECK (state IN ('waiting', 'running')),"
            " demand TEXT NOT NULL,"
            " enqueued_at REAL NOT NULL,"
            " started_at REAL, last_cpu REAL, last_cpu_at REAL,"
            " stalled INTEGER NOT NULL DEFAULT 0)")
        have_jobs = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "machine_id" not in have_jobs:
            conn.execute("ALTER TABLE jobs ADD COLUMN machine_id TEXT NOT NULL"
                         " DEFAULT ''")
        for column, kind in (("last_cpu", "REAL"), ("last_cpu_at", "REAL"),
                             ("stalled", "INTEGER NOT NULL DEFAULT 0"),
                             ("pid_start", "TEXT NOT NULL DEFAULT ''"),
                             ("heartbeat_at", "REAL")):
            if column not in have_jobs:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {column} {kind}")
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
            " resolve_peak_rss_gb REAL,"
            " queue_wait_s REAL, execution_s REAL)")
        have = {row[1] for row in conn.execute("PRAGMA table_info(grants)")}
        for column in ("resolve_peak_cores", "resolve_peak_rss_gb",
                       "queue_wait_s", "execution_s"):
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
        machine_id = _machine_id()
        now = time.time()
        for row_id, pid, row_host, row_machine, row_start, row_beat, \
                row_started, row_enqueued, state in conn.execute(
                "SELECT id, pid, host, machine_id, pid_start, heartbeat_at,"
                " started_at, enqueued_at, state FROM jobs").fetchall():
            # The store is machine-local, so a dead holder is always ours
            # to reclaim: by stable identity, by hostname when the identity
            # predates this scheme (`mac-<getnode>`, which was random per
            # process, measured 2026-10-08), or for rows predating any
            # identity when their pid is gone.
            same_machine = row_machine == machine_id
            same_host = row_host == host
            legacy_style = not row_machine or row_machine.startswith("mac-")
            if not (same_machine or same_host or legacy_style):
                continue
            last_beat = row_beat if row_beat else (row_started or row_enqueued or 0)
            if (state == "waiting" and row_beat
                    and now - row_beat <= LEASE_SECONDS):
                # A fresh heartbeat proves that a live waiter is refreshing
                # its row, even if a wrapper's recorded pid/start differs.
                continue
            if _holder_dead(pid, row_start):
                conn.execute("DELETE FROM jobs WHERE id = ?", (row_id,))
                continue
            if state == "waiting":
                # Lease expiry reclaims abandoned grants. A live waiter
                # holds no grant, and its queue row must survive delays in
                # heartbeat polling or sqlite lock contention.
                continue
            # The lease is the fallback past pid identity: a holder whose
            # heartbeat stopped (kill -9 ends the thread with the process)
            # frees the grant even when its number was recycled for a new
            # process. Live holders heartbeat, so a stale lease means the
            # holder is gone. Rows predating the heartbeat column fall back
            # to their start times rather than reading as forever fresh.
            if last_beat and now - last_beat > LEASE_SECONDS:
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
        record_machine = record.get("machine_id") if isinstance(
            record, dict) else None
        if (isinstance(record, dict)
                and (record_machine == _machine_id()
                     or not record_machine
                     or (isinstance(record_machine, str)
                         and record_machine.startswith("mac-")))
                and isinstance(record.get("pid"), int)
                and not _alive(record["pid"])):
            # The pre-scheduler mkdir lock has no lease to expire. Reclaim
            # it only when its complete contents are the owner record, so
            # a dead owner can never make us erase unrelated files.
            try:
                entries = list(self.legacy_dir.iterdir())
                if len(entries) == 1 and entries[0].name == OWNER_FILE:
                    entries[0].unlink()
                    self.legacy_dir.rmdir()
                    return None
            except OSError:
                pass
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
            "machine_id": _machine_id(),
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
                cancelled: Optional[Callable[[], bool]] = None,
                timeout: Optional[float] = None) -> str:
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
        me_start = _pid_start(os.getpid())
        now = time.time()

        def insert(conn):
            self._reap(conn)
            conn.execute(
                "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
                " demand, enqueued_at, pid_start, heartbeat_at)"
                " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?)",
                (token, owner, os.getpid(), socket.gethostname(),
                 _machine_id(), json.dumps(demand, sort_keys=True), now,
                 me_start, now))
        self._transaction(insert)
        announced = False
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        admitted = False
        try:
            while True:
                blockers = self._transaction(
                    lambda conn: self._try_admit(conn, token, demand, cap))
                if not blockers:
                    admitted = True
                    _start_heartbeat(self.db_path, token)
                    return token
                # A waiter refreshes its own lease while it queues, so a
                # live waiter never reads as stale and a dead waiter's row
                # expires promptly past its last beat.
                self._transaction(lambda conn: conn.execute(
                    "UPDATE jobs SET heartbeat_at = ? WHERE token = ?",
                    (time.time(), token)))
                if cancelled is not None and cancelled():
                    raise Cancelled(f"resource scheduler: {owner!r} stopped "
                                    "waiting")
                if deadline is not None and time.monotonic() >= deadline:
                    raise Cancelled(f"resource scheduler: {owner!r} timed out waiting")
                if not announced:
                    announce("heavy-work: waiting for " + "; ".join(blockers))
                    announced = True
                time.sleep(POLL_SECONDS)
        except BaseException:
            if admitted:
                _stop_heartbeat(token)
            self._transaction(lambda conn: (
                conn.execute("DELETE FROM jobs WHERE token = ?", (token,)),
                self._drop_legacy_dir_if_idle(conn)))
            raise

    def _try_admit(self, conn, token, demand, cap) -> list:
        """Admit the job in this transaction, or name what it waits for."""
        self._reap(conn)
        mine = conn.execute("SELECT id, pid FROM jobs WHERE token = ?",
                            (token,)).fetchone()
        if mine is None:
            raise RuntimeError("resource scheduler: queued job row vanished")
        me_start = _pid_start(mine[1])
        nested_holder = False
        for (holder_start,) in conn.execute(
                "SELECT pid_start FROM jobs WHERE state = 'running'"
                " AND pid = ?", (mine[1],)).fetchall():
            # A recycled pid wearing an old row's number is not us: the
            # reaper removes it, and it never grants a nested backfill.
            if not holder_start or not me_start or holder_start == me_start:
                nested_holder = True
                break
        ahead = conn.execute(
            "SELECT owner, pid, state, demand FROM jobs WHERE id != ? AND"
            " (state = 'running' OR id < ?) ORDER BY id",
            (mine[0], mine[0])).fetchall()
        # A nested acquisition by a process already holding resources must
        # not sit behind a waiter that needs those resources released first.
        # Keep every running grant as a constraint, but let the holder's
        # request backfill past queued jobs. Once its grants are released,
        # ordinary FIFO admission resumes, so large jobs still make progress.
        if nested_holder:
            ahead = [row for row in ahead if row[2] == "running"]
        legacy = self._legacy_owner()
        if legacy is not None:
            return [f"lock held by {_label(legacy)}"]
        if not _fits([demand] + [json.loads(d) for *_, d in ahead], cap):
            return [f"{state} {owner} (pid {pid})"
                    for owner, pid, state, _ in ahead]
        if not self._hold_legacy_dir():
            return ["lock directory claimed by an old-code lane"]
        now = time.time()
        conn.execute("UPDATE jobs SET state = 'running', started_at = ?,"
                     " heartbeat_at = ? WHERE token = ?", (now, now, token))
        return []

    def release(self, token: str, profile: str = "",
                measured: Optional[Dict[str, float]] = None) -> None:
        """End a running job; keep what it used in `grants`."""
        _stop_heartbeat(token)

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

    def timing(self, token: str) -> Optional[tuple[float, float]]:
        """Return enqueue and admission wall times for a live grant."""
        def body(conn):
            row = conn.execute(
                "SELECT enqueued_at, started_at FROM jobs WHERE token = ?"
                " AND state = 'running'", (token,)).fetchone()
            return (float(row[0]), float(row[1])) if row else None
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
            self._observe_cpu_progress(conn)
            return conn.execute(
                "SELECT owner, pid, state, demand, enqueued_at, started_at"
                ", stalled FROM jobs ORDER BY id").fetchall()
        return [{"owner": o, "pid": p, "state": s, "demand": json.loads(d),
                 "enqueued_at": e, "started_at": st, "stalled": bool(x)}
                for o, p, s, d, e, st, x in self._transaction(body)]

    @staticmethod
    def _observe_cpu_progress(conn: sqlite3.Connection) -> None:
        """Flag a live holder without CPU growth for a long bound.

        Diagnostic only: the scheduler reports stalled holders but never
        kills or signals them.
        """
        now = time.time()
        me = _machine_id()
        host = socket.gethostname()
        for row_id, pid, row_host, row_machine, previous, checked in conn.execute(
                "SELECT id, pid, host, machine_id, last_cpu, last_cpu_at"
                " FROM jobs WHERE state = 'running'").fetchall():
            same = row_machine == me or (not row_machine and row_host == host)
            if not same:
                continue
            try:
                raw = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "time="],
                    capture_output=True, encoding="utf-8", timeout=3,
                    check=False).stdout.strip()
                cpu = _cpu_seconds(raw)
            except (OSError, ValueError, subprocess.SubprocessError):
                continue
            if previous is None or cpu > previous:
                conn.execute("UPDATE jobs SET last_cpu = ?, last_cpu_at = ?,"
                             " stalled = 0 WHERE id = ?",
                             (cpu, now, row_id))
            elif checked is not None and now - checked >= STALL_SECONDS:
                conn.execute("UPDATE jobs SET stalled = 1 WHERE id = ?",
                             (row_id,))


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
    finished = time.time()
    conn.execute(
        "INSERT INTO grants (owner, profile, demand, enqueued_at,"
        " started_at, finished_at, cpu_s, peak_cores, peak_rss_gb,"
        " samples, resolve_peak_cores, resolve_peak_rss_gb,"
        " queue_wait_s, execution_s)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row[0], profile, row[1], row[2], row[3], finished,
         used.get("cpu_s"), used.get("peak_cores"), used.get("peak_rss_gb"),
         used.get("samples"), used.get("resolve_peak_cores"),
         used.get("resolve_peak_rss_gb"), max(0.0, row[3] - row[2]),
         max(0.0, finished - row[3])))


def _label(owner: dict) -> str:
    label = str(owner.get("owner") or "unknown owner")
    if owner.get("pid") is not None:
        label += f" (pid {owner['pid']}"
        if owner.get("host"):
            label += f"@{owner['host']}"
        label += ")"
    return label
