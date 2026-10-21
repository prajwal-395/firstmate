"""Admit concurrent heavy work, guarding only true exclusives and memory.

The resources
-------------
Only `native_*` resources are scheduler exclusives: a native build
directory has one writer. Resolve's lease and Resolve itself arbitrate
Resolve work. CPU, GPU, and disk are not statically budgeted here;
only the measured edge-pushing kinds (`MEASURED_MEMORY_RESERVATIONS_GB`)
carry a RAM reservation, and the scheduler checks live OS memory only
for jobs declaring one.

Profiles are retained for call-site identity and reporting, but their
CPU, GPU, and disk declarations do not throttle work. `machine` is
an empty profile. Capability phases contribute only native build-directory
exclusives; the measured RAM reservations above are the only memory
demands any profile carries. RAM-reserving jobs use live available memory from
`/proc/meminfo` on Linux or the kernel's `kern.memorystatus_level` estimate
on macOS; failure to measure refuses their admission.
Jobs without a RAM reservation do not contend for memory and are not held
by its guard.

SWAP is a second reading of the same constraint, and it is the one that
says the machine is ALREADY paging: `perf_resources` reads it (macOS
`vm.swapusage`, Linux `/proc/meminfo`). A RAM-reserving job is REFUSED -
not waited for - when the swap device is at or above
`SWAP_REFUSAL_FRACTION` of its size AND the demand does not fit the
free memory. A wait for memory that only paging can deliver is a swap
death with extra steps; a demand that DOES fit is still admitted,
because refusing work that would have run is its own regression.

The store
---------
SQLite in WAL mode beside the old lock directory. Every state change is
one `BEGIN IMMEDIATE` transaction, so admission is atomic across
processes. A job is a row: `waiting` until admitted, then `running`.
Admission backfills around waiters that cannot run now. Among eligible
waiters, older jobs have priority only when they need the same resource;
an old waiter never stops unrelated work. A dead holder is reaped by the next
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
cores, resident memory and the machine's peak swap and memory pressure
beside each profile, for operational evidence.

The bridge to the old lock
--------------------------
Lanes still on code from before this module take the old mkdir lock at
`HEAVY_LOCK_DIR` and know nothing of this store. So while ANY scheduler
job runs, the scheduler holds that directory (its owner record carries
`"scheduler": true`), so old-code holders and new-code jobs continue to
exclude each other during rollout. The bridge goes once no lane runs
pre-scheduler code.

`tests/unit/context/test_concurrency.py`.
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Callable, Dict, Optional

from library.tools import perf_resources

RESOURCES = ("ram_gb",) + tuple(
    "native_" + name.replace("-", "_") for name in (
    "ren-graphics-fframes", "resolve-control", "ren-resolved",
    "resolve-activity-overlay", "ren_native"))
# `ram_gb` is a declared demand enforced against live OS memory at
# admission, not a static capacity: `NATIVE_PARTS` (and `capacity()`)
# derive from the `native_*` members only.
NATIVE_PARTS = tuple(resource.removeprefix("native_")
                     for resource in RESOURCES if resource.startswith("native_"))
MEMORY_HEADROOM_FRACTION = 0.10
MINIMUM_MEMORY_FLOOR_GB = 2.0
#: RAM reservations (GB) held only by the job kinds measured pushing the
#: machine to the edge beside Resolve. Every other profile admits
#: regardless of free memory. Basis is peak phys_footprint, not RSS:
#: docs/benchmarks/2026-10-15-memory-admission.md, superseded for SAM 2
#: by docs/benchmarks/2026-10-22-prod006-resource-constrained.md. A
#: reservation sizes the live-headroom check; nothing here is a static
#: budget that serializes by accounting. A reservation is the MEASURED
#: PEAK of the kind, not its average: the average leaves the tail
#: unpaged for, and the tail is what swaps the machine to death.
MEASURED_MEMORY_RESERVATIONS_GB: Dict[str, int] = {
    # One Gemma 4 12B load plus a single inference: 14.43 GB peak
    # footprint against 1.69 GB peak RSS (2026-10-15).
    "semantics.analyse:model_memory": 15,
    # SAM 2 over a 3 s clip: 7.41 GB peak footprint (2026-10-15) - but
    # the 2026-10-22 measurement on THIS machine reached 10.15 GB peak
    # footprint over 8 sampled grants (mean 6.55 GB), because the peak
    # tracks the clip's frame count. Reserving the measured 7.41 GB
    # admitted a session that then overran its reservation by 2.74 GB
    # and pushed the machine into swap; the reservation is now the
    # measured peak plus a margin.
    "objects.segment:model_memory": 11,
}

#: The share of the swap device in use at which a RAM-reserving job is
#: REFUSED instead of admitted. Measured 2026-10-22 on this 24 GiB
#: machine: 14.0 of 15.4 GB (91%) of swap in use with 6.96 GB available,
#: while both resident-model stages waited on memory they could not
#: have. A machine paging at this depth cannot page a resident model's
#: weights IN: the wait is a swap death with extra steps, so the job is
#: refused with the reading instead. Below the fraction the swap
#: device is not the binding constraint and the headroom check decides.
SWAP_REFUSAL_FRACTION = 0.75

# Resolve's own process, by executable name (`TreeSampler`).
RESOLVE_PROCESS = "Resolve"
DB_FILENAME = "resource-scheduler.sqlite3"
OWNER_FILE = "owner"
POLL_SECONDS = 0.5
BUSY_TIMEOUT_MS = 30_000
STALL_SECONDS = 600
LEASE_SECONDS = 60.0
HEARTBEAT_SECONDS = 15.0
BACKFILL_AGE_SECONDS = 300.0
SCHEDULER_POLICY_VERSION = 1


class Cancelled(Exception):
    """A waiter's `cancelled` answered True before it was admitted."""


class MemoryUnavailable(ValueError):
    """This machine cannot host the memory the job asked to reserve.

    A refusal with the reading beside it, never a wait: the caller
    degrades the capability it wanted (a model that cannot be
    resident says so) instead of sitting on a grant the machine will
    never grant. It is a `ValueError` because it refuses a DEMAND the
    machine cannot satisfy, which is what a `ValueError` has always
    meant here.
    """


def _swap_state_gb() -> tuple[float | None, float | None]:
    """Swap in use and swap total, in GB; None where the OS says nothing.

    The reading is the machine's, not this process's: swap is where the
    WHOLE machine's cold pages live, so a stage's share of it is not
    separable here. `perf_resources` owns the reading and states that a
    counter the OS does not expose is absent, never zero.
    """
    reading = perf_resources.swap_usage_mb()
    used_mb, total_mb = reading.get("used_mb"), reading.get("total_mb")
    scale = 1024.0
    used = None if used_mb is None else used_mb / scale
    total = None if total_mb is None else total_mb / scale
    return used, total


def _swap_refusal_reason(demand_gb: float,
                         already_reserved_gb: float = 0.0) -> str | None:
    """Why this machine refuses a RAM-reserving job now, or None.

    Two readings have to agree before a job is refused:

    * the SWAP DEVICE is at or above `SWAP_REFUSAL_FRACTION` of its
      size - the machine is already paging, not briefly busy; and
    * the demand does not fit the memory the machine has free - so
      admitting it would page the model's weights in, or eat the
      headroom the floor keeps.

    A demand that DOES fit is admitted on a paging machine: the swap
    says the machine is under pressure, not that this job is the wrong
    size, and refusing work that would have run is its own regression.
    The message carries the readings, because a refusal a reader
    cannot check is a refusal nobody trusts.
    """
    used, total = _swap_state_gb()
    if used is None or not total:
        return None
    if used / total < SWAP_REFUSAL_FRACTION:
        return None
    reading = _memory_available_gb()
    if reading is None or reading[1] <= 0:
        # Without a measurement the fit cannot be proven, so the job is
        # not refused on a reading nobody can check.
        return None
    available, physical = reading
    if available - already_reserved_gb - demand_gb >= _memory_floor_gb(
            physical):
        return None
    return (
        f"this machine is already paging: {used:.1f} GB of {total:.1f} GB "
        f"of swap in use ({used / total:.0%}), and "
        f"{available:.1f} GB of {physical:.0f} GB of memory available. "
        f"a {demand_gb:g} GB resident model cannot be paged in on top "
        "of that, so it is refused rather than waited for")


def _physical_ram_gb() -> int:
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
                   // 2 ** 30)
    except (ValueError, OSError, AttributeError):
        return 16


def capacity() -> Dict[str, int]:
    """Capacities for genuinely exclusive scheduler resources."""
    return {"native_" + part: 1 for part in NATIVE_PARTS}


MEMORY_OVERRIDE_ENV = "VEP_SCHEDULER_MEMORY_GB"
"""Test seam for the memory probe: when set to "<available>,<total>"
gigabytes, `_memory_available_gb` returns it instead of reading the OS.

Scheduler tests (including the subprocesses they spawn, which inherit
the environment) admit against fixed memory rather than the host's
transient pressure. Unset - or unparseable, which falls through to the
live reading - in production, where admission always reads the running
OS. This changes no admission decision, only where the probe reads."""


def _memory_available_gb() -> tuple[float, float] | None:
    """Return OS measured available and total memory, or fail closed."""
    override = os.environ.get(MEMORY_OVERRIDE_ENV)
    if override:
        try:
            available_s, _, total_s = override.partition(",")
            available, total = float(available_s), float(total_s)
            if total > 0 and available >= 0:
                return available, total
        except ValueError:
            pass
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "kern.memorystatus_level"],
                                 capture_output=True, encoding="utf-8",
                                 timeout=3, check=True).stdout
            available_percent = int(out.strip())
            if not 0 <= available_percent <= 100:
                return None
            total = float(_physical_ram_gb())
            return total * available_percent / 100, total
        meminfo = Path("/proc/meminfo")
        if meminfo.is_file():
            values = {}
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                key, _, value = line.partition(":")
                if key in {"MemAvailable", "MemTotal"}:
                    values[key] = int(value.strip().split()[0]) / (1024 * 1024)
            return values["MemAvailable"], values["MemTotal"]
        out = subprocess.run(["vm_stat"], capture_output=True,
                             encoding="utf-8", timeout=3,
                             check=False).stdout
        page_size = int(out.split("page size of ", 1)[1].split()[0])
        pages = {}
        for line in out.splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() in {"Pages free", "Pages inactive",
                                       "Pages speculative", "Pages purgeable"}:
                pages[key.strip()] = int(value.strip().rstrip("."))
        available = sum(pages.values()) * page_size / 2**30
        return available, float(_physical_ram_gb())
    except (OSError, ValueError, KeyError, IndexError,
            subprocess.SubprocessError):
        return None


def _memory_allows_admission() -> bool:
    """Retain proportional OS headroom using a live memory measurement."""
    reading = _memory_available_gb()
    if reading is None:
        return False
    available, total = reading
    return total > 0 and available / total >= MEMORY_HEADROOM_FRACTION


def _memory_floor_gb(total: float) -> float:
    """Keep both proportional and minimum OS headroom available."""
    return max(MINIMUM_MEMORY_FLOOR_GB, total * MEMORY_HEADROOM_FRACTION)


def _older_waiter_can_run(older_demand: Dict[str, int],
                          running_demands) -> bool:
    """Whether an older waiter could be admitted right now.

    An older waiter that cannot fit the LIVE headroom is not eligible:
    it is waiting for memory the machine does not have, and it must not
    serialize work that does fit behind it. A demand with no RAM part
    is always eligible - it never waits on memory.
    """
    older_memory = float(older_demand.get("ram_gb", 0))
    if not older_memory:
        return True
    reading = _memory_available_gb()
    if reading is None or reading[1] <= 0:
        # Without a measurement nothing can be proven satisfiable, so
        # the waiter is not eligible to hold the queue.
        return False
    available, total = reading
    reserved = sum(float(demand.get("ram_gb", 0))
                   for demand in running_demands)
    return available - reserved - older_memory >= _memory_floor_gb(total)


def _maximum_memory_demand_gb() -> float:
    """Largest reservation that can fit while preserving the memory floor."""
    reading = _memory_available_gb()
    if reading is None or reading[1] <= 0:
        # Without a machine measurement, do not queue a request that cannot
        # be proven satisfiable. Admission itself will still fail closed.
        return 0.0
    return max(0.0, reading[1] - _memory_floor_gb(reading[1]))


def _capability_demand(phase, cap: Dict[str, int]) -> Dict[str, int]:
    """Materialize build-directory exclusives plus RAM reservations.

    `ram_gb` passes through unclamped: it is enforced against live OS
    memory at admission (`_try_admit`), not against a static capacity,
    so a measured reservation serializes model sessions without a
    machine-wide gigabyte budget.
    """
    result = {resource: min(amount, cap[resource])
              for resource, amount in phase.resources
              if resource.startswith("native_") and amount}
    for resource, amount in phase.resources:
        if resource == "ram_gb" and amount:
            result[resource] = amount
    return result


_RESOLVE_EXCLUSIVE_PROFILES: set | None = None


def resolve_exclusive_profiles() -> set:
    """The profiles whose work holds Resolve's cursor exclusively.

    Derived from the capability execution policies, the same source the
    scheduler's other profiles come from: a phase declared
    `resolve_mode=exclusive` runs beside the captain's own Resolve use,
    so its grant must record what Resolve was doing at the time. The
    answer is a SET of profile names, not a demand: Resolve's own lease
    arbitrates Resolve work, and adding a key to the demand would make
    the scheduler arbitrate it as well.

    Cached: the registry is fixed for the process, and every admission
    asks.
    """
    global _RESOLVE_EXCLUSIVE_PROFILES
    if _RESOLVE_EXCLUSIVE_PROFILES is not None:
        return _RESOLVE_EXCLUSIVE_PROFILES
    from library.tools import capabilities

    exclusive = set()
    for capability_id, execution in capabilities.executions():
        for phase in execution.phases:
            if phase.resolve_mode == "exclusive":
                exclusive.add(f"{capability_id}:{phase.name}")
    _RESOLVE_EXCLUSIVE_PROFILES = exclusive
    return exclusive


def watches_resolve(profile: str) -> bool:
    """Whether a grant under `profile` samples Resolve beside itself."""
    if profile in resolve_exclusive_profiles():
        return True
    return False


def profiles() -> Dict[str, Dict[str, int]]:
    """Derived scheduler profiles, keyed by capability id and phase.

    Capability phases contribute only native build-directory exclusives;
    the measured RAM reservations (`MEASURED_MEMORY_RESERVATIONS_GB`)
    are overlaid afterwards, so only the edge-pushing kinds wait on
    memory. A reservation naming a profile no capability declares
    raises here rather than silently covering nothing.
    """
    from library.tools import capabilities

    cap = capacity()
    declared = {"machine": {}, "full_suite_gate": {},
                "query_refinement": {}, "reel_preview": {}}
    for part in NATIVE_PARTS:
        declared[f"native_refresh:{part.replace('_', '-')}"] = {
            "native_" + part: 1}
    for capability_id, execution in capabilities.executions():
        for phase in execution.phases:
            demand = _capability_demand(phase, cap)
            declared[f"{capability_id}:{phase.name}"] = demand
    for profile, ram_gb in MEASURED_MEMORY_RESERVATIONS_GB.items():
        if profile not in declared:
            raise ValueError(
                f"resource scheduler: measured memory reservation for "
                f"undeclared profile {profile!r}")
        declared[profile] = {**declared[profile], "ram_gb": ram_gb}
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
            if not k.startswith("native_"):
                continue
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
            " stalled INTEGER NOT NULL DEFAULT 0,"
            " policy_version INTEGER NOT NULL DEFAULT 0)")
        have_jobs = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
        if "machine_id" not in have_jobs:
            conn.execute("ALTER TABLE jobs ADD COLUMN machine_id TEXT NOT NULL"
                         " DEFAULT ''")
        for column, kind in (("last_cpu", "REAL"), ("last_cpu_at", "REAL"),
                             ("stalled", "INTEGER NOT NULL DEFAULT 0"),
                             ("policy_version", "INTEGER NOT NULL DEFAULT 0"),
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
            " peak_footprint_gb REAL,"
            " resolve_peak_footprint_gb REAL,"
            " queue_wait_s REAL, execution_s REAL,"
            " peak_swap_gb REAL, peak_memory_pressure_pct REAL)")
        have = {row[1] for row in conn.execute("PRAGMA table_info(grants)")}
        for column in ("resolve_peak_cores", "resolve_peak_rss_gb",
                       "queue_wait_s", "execution_s",
                       "peak_footprint_gb", "resolve_peak_footprint_gb",
                       "peak_swap_gb", "peak_memory_pressure_pct"):
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
        try:
            for path in self.legacy_dir.iterdir():
                path.unlink()
            self.legacy_dir.rmdir()
        except FileNotFoundError:
            # An older lane or an external cleanup may remove the bridge
            # directory after the existence check. The scheduler row has
            # already been released, so a missing legacy directory is idle.
            pass

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
        requested_memory = float(demand.get("ram_gb", 0))
        if requested_memory:
            maximum_memory = _maximum_memory_demand_gb()
            if requested_memory > maximum_memory:
                raise MemoryUnavailable(
                    f"resource scheduler: {owner!r} requests "
                    f"{requested_memory:g} GB RAM, but this machine can "
                    f"provide at most {maximum_memory:g} GB while "
                    "preserving the measured memory floor")
            # A machine already paging cannot host a resident model, and
            # a wait for it is a swap death with extra steps: refuse now,
            # with the reading, so the caller degrades the capability
            # instead of sitting on a grant that will never arrive.
            already_reserved = sum(
                float(item.get("ram_gb", 0))
                for item in self._running_demands())
            refusal = _swap_refusal_reason(requested_memory,
                                           already_reserved)
            if refusal is not None:
                raise MemoryUnavailable(
                    f"resource scheduler: {owner!r} requests "
                    f"{requested_memory:g} GB RAM, but {refusal}")
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
                " demand, enqueued_at, pid_start, heartbeat_at, policy_version)"
                " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?)",
                (token, owner, os.getpid(), socket.gethostname(),
                 _machine_id(), json.dumps(demand, sort_keys=True), now,
                 me_start, now, SCHEDULER_POLICY_VERSION))
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
        running = conn.execute(
            "SELECT owner, pid, demand, policy_version FROM jobs"
            " WHERE state = 'running'"
            " AND id != ? ORDER BY id", (mine[0],)).fetchall()
        legacy = self._legacy_owner()
        if legacy is not None:
            return [f"lock held by {_label(legacy)}"]
        if any(row[3] < SCHEDULER_POLICY_VERSION for row in running):
            return ["running job uses an older resource policy"]
        running_demands = [json.loads(row[2]) for row in running]
        if not _fits([demand, *running_demands], cap):
            return [f"running {row[0]} (pid {row[1]})" for row in running]
        requested_memory = float(demand.get("ram_gb", 0))
        if requested_memory:
            reading = _memory_available_gb()
            already_reserved = sum(float(item.get("ram_gb", 0))
                                   for item in running_demands)
            if (reading is None or reading[1] <= 0
                    or reading[0] - already_reserved - requested_memory
                    < _memory_floor_gb(reading[1])):
                return ["live OS memory pressure"]
        # Backfill around an older waiter only when it cannot run beside
        # current exclusive holders. Enqueue age gives older eligible jobs
        # priority on a contended key, and bounds all backfill: once an
        # older waiters only take priority from jobs that demand one of the
        # same resources. An impossible or aged memory waiter therefore
        # cannot stop work that does not need memory.
        if not nested_holder:
            older = conn.execute(
                "SELECT owner, pid, demand FROM jobs"
                " WHERE state = 'waiting' AND id < ? ORDER BY id",
                (mine[0],)).fetchall()
            for owner, pid, raw_demand in older:
                older_demand = json.loads(raw_demand)
                if not _fits([older_demand, *running_demands], cap):
                    continue
                if not _older_waiter_can_run(older_demand, running_demands):
                    # The older waiter cannot be admitted NOW: its own
                    # RAM demand does not fit the live headroom, so it is
                    # not an eligible waiter and must not hold the
                    # queue. A smaller job that DOES fit is admitted
                    # rather than waiting behind a job the machine
                    # cannot run - that wait is how one model stage
                    # starves another for the whole timeout.
                    continue
                contention = set(demand).intersection(older_demand)
                if contention:
                    return [f"older eligible waiter {owner} (pid {pid})"]
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

    def _running_demands(self) -> list:
        """The demands of every job that is running right now.

        The RAM already reserved on this machine, so a new reservation
        is judged against what the running ones hold.
        """
        def body(conn):
            self._reap(conn)
            return [json.loads(row[0]) for row in conn.execute(
                "SELECT demand FROM jobs WHERE state = 'running'")]
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
    process and every descendant. Resident RSS is summed across the
    tree - a gate's pytest workers each hold their own - while macOS
    physical footprint is sampled for every tree PID, including GPU and
    compressed-memory charges. Declared RAM reservations are enforced
    against that physical peak. Cores are
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
    #: The machine's peak swap and memory pressure while this grant ran.
    #: Class defaults, so a sampler built without `__init__` still reads
    #: them: the reading is evidence, never a reason to fail a sample.
    peak_swap_gb = None
    peak_memory_pressure_pct = None

    def __init__(self, watch_resolve: bool = False,
                 reservation_gb: float | None = None,
                 over_reservation: Callable[[float, float], None]
                 | None = None) -> None:
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
        self.peak_footprint_gb: float | None = None
        self.resolve_peak_footprint_gb: float | None = None
        self.reservation_gb = reservation_gb
        self.over_reservation = over_reservation
        self._over_reservation_reported = False
        self._enforcement_enabled = threading.Event()
        self.samples = 0
        self.peak_swap_gb = None
        self.peak_memory_pressure_pct = None
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

    def enforce_reservation(self) -> None:
        """Start applying the ceiling after admission has completed."""
        self._enforcement_enabled.set()

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
        # The machine's swap and pressure, sampled on the same beat as
        # the tree: a stage's own share of the swap is not separable
        # from the machine's, but the PEAK the machine reached while
        # this stage ran is the number the operator needs, and it was
        # unmeasured before.
        self._sample_machine_state()
        if self.watch_resolve:
            self.resolve_peak_rss_gb = max(
                self.resolve_peak_rss_gb,
                sum(stats[pid][0] for pid in theirs) / 2 ** 20)
        self.samples += 1
        # `footprint -p` accepts several PIDs and reports their combined
        # phys_footprint. Pass every descendant found by this same process
        # tree walk; the scheduler wrapper's PID alone excludes its worker.
        footprint = _tree_footprint_gb(*sorted(tree))
        if footprint is not None:
            self.peak_footprint_gb = max(
                self.peak_footprint_gb or 0.0, footprint)
            if (self.reservation_gb is not None
                    and self._enforcement_enabled.is_set()
                    and footprint > self.reservation_gb
                    and not self._over_reservation_reported):
                self._over_reservation_reported = True
                if self.over_reservation is not None:
                    self.over_reservation(footprint, self.reservation_gb)
        if self.watch_resolve and resolve:
            resolve_footprint = _tree_footprint_gb(*sorted(resolve))
            if resolve_footprint is not None:
                self.resolve_peak_footprint_gb = max(
                    self.resolve_peak_footprint_gb or 0.0,
                    resolve_footprint)

    def _sample_machine_state(self) -> None:
        """Record the machine's peak swap and memory pressure so far."""
        used, _total = _swap_state_gb()
        if used is not None:
            self.peak_swap_gb = max(self.peak_swap_gb or 0.0, used)
        pressure = perf_resources.memory_pressure()
        pct = pressure.get("pct")
        if pct is not None:
            self.peak_memory_pressure_pct = max(
                self.peak_memory_pressure_pct or 0.0, float(pct))

    def stop(self) -> Dict[str, float]:
        """The peaks so far. A release never waits on a measurement: a
        sample still in flight on a starved machine is left behind."""
        self._stop.set()
        self._thread.join(timeout=0.5)
        return {"cpu_s": round(self._cpu() - self._cpu0, 1),
                "peak_cores": round(self.peak_cores, 2),
                "peak_rss_gb": round(self.peak_rss_gb, 2),
                "peak_footprint_gb": (
                    round(self.peak_footprint_gb, 3)
                    if self.peak_footprint_gb is not None else None),
                "peak_swap_gb": (
                    round(self.peak_swap_gb, 3)
                    if self.peak_swap_gb is not None else None),
                "peak_memory_pressure_pct": (
                    round(self.peak_memory_pressure_pct, 1)
                    if self.peak_memory_pressure_pct is not None else None),
                "samples": self.samples,
                **({"resolve_peak_cores": round(self.resolve_peak_cores, 2),
                    "resolve_peak_rss_gb": round(self.resolve_peak_rss_gb,
                                                 2),
                    "resolve_peak_footprint_gb": (
                        round(self.resolve_peak_footprint_gb, 3)
                        if self.resolve_peak_footprint_gb is not None
                        else None)}
                   if self.watch_resolve else {})}


def _grown(before: Dict[int, float], after: Dict[int, float]) -> float:
    """CPU seconds a set of processes gained between two samples."""
    return sum(max(0.0, value - before.get(pid, 0.0))
               for pid, value in after.items())


def _tree_footprint_gb(*pids: int) -> float | None:
    """Read combined physical footprint for the supplied process IDs.

    macOS `footprint` exposes phys_footprint, which RSS misses for pages
    charged to compressed memory and other VM categories. Other platforms
    retain the existing RSS measurement and store no footprint value.
    Callers supply the complete process tree when measuring a job.
    """
    if sys.platform != "darwin" or not pids:
        return None
    command = ["footprint", "-t", "-f", "bytes", "--noCategories"]
    for pid in pids:
        command.extend(("-p", str(pid)))
    try:
        out = subprocess.run(command, capture_output=True, encoding="utf-8",
                             timeout=5, check=False).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith("Summary Footprint:"):
            try:
                return int(line.split(":", 1)[1].split()[0]) / 2**30
            except (ValueError, IndexError):
                return None
    return None


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
        " peak_footprint_gb, resolve_peak_footprint_gb,"
        " queue_wait_s, execution_s, peak_swap_gb,"
        " peak_memory_pressure_pct)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (row[0], profile, row[1], row[2], row[3], finished,
         used.get("cpu_s"), used.get("peak_cores"), used.get("peak_rss_gb"),
         used.get("samples"), used.get("resolve_peak_cores"),
         used.get("resolve_peak_rss_gb"),
         used.get("peak_footprint_gb"),
         used.get("resolve_peak_footprint_gb"),
         max(0.0, row[3] - row[2]),
         max(0.0, finished - row[3]),
         used.get("peak_swap_gb"),
         used.get("peak_memory_pressure_pct")))


def _label(owner: dict) -> str:
    label = str(owner.get("owner") or "unknown owner")
    if owner.get("pid") is not None:
        label += f" (pid {owner['pid']}"
        if owner.get("host"):
            label += f"@{owner['host']}"
        label += ")"
    return label
