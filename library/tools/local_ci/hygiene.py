"""Resource hygiene: the four failures that retired the scratch scripts.

1. Disk floor: refuse a run below DISK_FLOOR_BYTES and SAY so, instead
   of filling the disk to 100% with checkouts nobody deletes.
2. Disposable checkouts: a worktree per run, deleted in a finally -
   including when the tests fail, time out, or the runner is killed.
3. Slot semaphore: at most MAX_SLOTS concurrent runs, as file locks so
   two daemon processes (or a daemon and a manual `ren ci check`) see
   the same two slots.
4. Broker leaks: no Resolve broker the run started may outlive it. A
   broker that was ALREADY serving (the captain's standalone service)
   is left alone; a NEW one the tests spawned is the leak.

Heavy-work deference: a run starts only when no full-suite gate holds
the machine grant (`heavy_work_lock jobs`), because two gates never run
at once and a PR check that fights the batch gate slows both.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time

from library.tools.local_ci import config


class HygieneRefused(RuntimeError):
    """A run that declined BEFORE spending anything: disk, slots, gate."""


def disk_ok(path: str, floor: int = config.DISK_FLOOR_BYTES) -> bool:
    """Whether the volume holding path has floor bytes free."""
    return shutil.disk_usage(path).free >= floor


def disk_free(path: str) -> int:
    """Free bytes on the volume holding path (for the refusal message)."""
    return shutil.disk_usage(path).free


def checkouts_root(state: str = "") -> str:
    """Where disposable worktrees live, beside the ledger, never in a repo."""
    return os.path.join(state or config.state_dir(), "checkouts")


def _slot_lock_is_held(slots_dir: str, slot: int) -> bool:
    """Whether a worker currently owns this slot's cross-process lock."""
    path = os.path.join(slots_dir, f"slot-{slot}.lock")
    try:
        with open(path, encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
    except FileNotFoundError:
        return False


def running_jobs(state: str = "") -> list[dict]:
    """Return live slot owners with their PR metadata and elapsed time.

    Slot metadata is written atomically while its file lock is held.
    A dead daemon can leave that metadata behind, and a PID can be
    reused. Require both a live PID and a held slot lock before
    presenting the record as an active job.
    """
    slots_dir = os.path.join(state or config.state_dir(), "slots")
    try:
        names = os.listdir(slots_dir)
    except FileNotFoundError:
        return []
    jobs = []
    for name in names:
        if not name.startswith("slot-") or not name.endswith(".json"):
            continue
        path = os.path.join(slots_dir, name)
        try:
            with open(path, encoding="utf-8") as handle:
                job = json.load(handle)
            if not isinstance(job, dict):
                continue
            pid = int(job["pid"])
            slot = int(job["slot"])
            started_at = float(job["started_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if pid <= 0 or slot < 0 or name != f"slot-{slot}.json":
            continue
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError:
            pass
        if not _slot_lock_is_held(slots_dir, slot):
            continue
        job["elapsed_seconds"] = max(0.0, time.time() - started_at)
        jobs.append(job)
    return sorted(jobs, key=lambda job: (job["started_at"], job["slot"]))


def running_gate_jobs() -> list[dict]:
    """Return live full-suite grants, including Tier-2 queue evaluations.

    Tier-2 runs outside the Tier-1 slot files, under the machine resource
    scheduler. The status command reads its live rows so an active stack
    is not reported as "running jobs: none".
    """
    from library.tools import heavy_work_lock, resource_scheduler

    try:
        rows = resource_scheduler.Scheduler(
            heavy_work_lock.HEAVY_LOCK_DIR).jobs()
    except (OSError, RuntimeError, sqlite3.Error):
        return []
    now = time.time()
    jobs = []
    for row in rows:
        if (row["state"] != "running"
                or "full_suite_gate" not in row["owner"]):
            continue
        job = dict(row)
        job["elapsed_seconds"] = max(0.0, now - job["started_at"])
        jobs.append(job)
    return sorted(jobs, key=lambda job: (job["started_at"], job["pid"]))


def _git(repo: str, *argv: str, timeout: int = 300) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *argv], capture_output=True,
        encoding="utf-8", check=False, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: "
                           f"{result.stderr.strip()}")
    return result.stdout.strip()


@contextlib.contextmanager
def _mirror_lock(mirror: str):
    """Serialize clone/fetch mutations to the shared local mirror.

    Tier-1 workers deliberately run in parallel, but Git's clone and
    fetch state belongs to the mirror as a whole. A sibling lock file
    also exists before the mirror has been cloned, so first-use races
    are covered across threads and daemon processes.
    """
    os.makedirs(os.path.dirname(os.path.abspath(mirror)), exist_ok=True)
    with open(mirror + ".lock", "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def ensure_mirror(origin: str, state: str = "") -> str:
    """The daemon's origin mirror: one clone, worktrees per run.

    Created once (git clone), refreshed by fetch after. A mirror plus
    worktrees is what makes per-run checkouts seconds-cheap while still
    disposable: the worktree is deleted, the mirror stays.
    """
    root = state or config.state_dir()
    mirror = os.path.join(root, "mirror")
    with _mirror_lock(mirror):
        if not os.path.isdir(os.path.join(mirror, ".git")):
            if os.path.exists(mirror):
                raise HygieneRefused(f"{mirror} exists but is not a git repo")
            os.makedirs(root, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", origin, mirror], capture_output=True,
                encoding="utf-8", check=False, timeout=600)
            if result.returncode != 0:
                raise HygieneRefused(f"could not clone {origin}: "
                                     f"{result.stderr.strip()}")
        _git(mirror, "fetch", "origin", "--prune")
    return mirror


def fetch_pr(mirror: str, number: int) -> str:
    """Fetch a PR head into the mirror. Returns the head SHA."""
    with _mirror_lock(mirror):
        _git(mirror, "fetch", "origin",
             f"+pull/{number}/head:refs/ren-ci/pr-{number}", timeout=300)
        return _git(mirror, "rev-parse", f"refs/ren-ci/pr-{number}")


def fetch_base(mirror: str, base: str) -> None:
    """Fetch the base SHA the batch loop may have just landed.

    main moves UNDER the daemon (the mirror fetch and the main_sha
    read are not atomic): a base the mirror lacks makes every later
    diff fail with "invalid revision range". Fetch it by SHA - GitHub
    serves reachable SHAs - and DEFER (HygieneRefused, retried next
    pass) when it is not fetchable yet, instead of crashing the check
    onto the PR as an error.
    """
    try:
        with _mirror_lock(mirror):
            _git(mirror, "fetch", "origin", base, timeout=300)
    except RuntimeError as exc:
        raise HygieneRefused(
            f"base {base[:12]} not fetchable yet - main moved under "
            f"the fetch, retrying next pass ({exc})") from exc


@contextlib.contextmanager
def disposable_checkout(mirror: str, rev: str, state: str = ""):
    """A detached worktree at rev, deleted on exit no matter what.

    Yields the worktree path. Deletion runs in a finally AND tolerates
    a worktree git already forgot (a killed run's debris): `worktree
    remove --force`, falling back to shutil.rmtree, so debris never
    accumulates into the next disk-full incident.
    """
    root = checkouts_root(state)
    os.makedirs(root, exist_ok=True)
    # A daemon's Tier-1 slots can start in the same process and second.
    # Reserve a unique directory atomically instead of deriving the name
    # from the PID and wall clock.
    path = tempfile.mkdtemp(prefix=f"run-{os.getpid()}-", dir=root)
    try:
        _git(mirror, "worktree", "add", "--detach", path, rev)
        yield path
    finally:
        try:
            _git(mirror, "worktree", "remove", "--force", path)
        except RuntimeError:
            shutil.rmtree(path, ignore_errors=True)
        try:
            _git(mirror, "worktree", "prune")
        except RuntimeError:
            pass


class Slots:
    """Test slots as file locks: cross-process, non-blocking.

    Sized per dispatch (`load.targets()`): a quiet machine holds more,
    a loaded one fewer. `acquire` returns the slot index or None when
    all are busy - the caller schedules, never waits blindly. Released
    by context exit, and by process death (fcntl locks die with the
    holder, so a killed run cannot wedge the queue). Thread-safe: the
    daemon's Tier-1 workers share one Slots across threads.
    """

    def __init__(self, state: str = "", jobs: int = 2) -> None:
        self._dir = os.path.join(state or config.state_dir(), "slots")
        os.makedirs(self._dir, exist_ok=True)
        self._jobs = max(1, jobs)
        self._guard = threading.Lock()

    @contextlib.contextmanager
    def claim(self, job: dict | None = None):
        """One slot for the enclosed block, or None when all are busy.

        A context manager (not acquire/release pairs) because Tier-1
        workers share one Slots across threads: a bare release would
        drop a sibling thread's slot. Yields the slot index, or None -
        the caller schedules, never waits blindly.
        """
        handle = None
        index = None
        metadata_path = ""
        with self._guard:
            for candidate in range(self._jobs):
                attempt = open(  # noqa: SIM115 - keep the lock fd open to release
                    os.path.join(self._dir, f"slot-{candidate}.lock"),
                    "w", encoding="utf-8")
                try:
                    fcntl.flock(attempt.fileno(),
                                fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    attempt.close()
                    continue
                handle, index = attempt, candidate
                break
        try:
            if handle is not None:
                metadata_path = os.path.join(
                    self._dir, f"slot-{index}.json")
                metadata_tmp = (
                    f"{metadata_path}.tmp-{os.getpid()}-"
                    f"{threading.get_ident()}")
                record = dict(job or {})
                record.update({"pid": os.getpid(), "slot": index,
                               "started_at": time.time()})
                with open(metadata_tmp, "w", encoding="utf-8") as output:
                    json.dump(record, output, sort_keys=True)
                    output.write("\n")
                os.replace(metadata_tmp, metadata_path)
            yield index
        finally:
            if handle is not None:
                with self._guard:
                    try:
                        os.unlink(metadata_path)
                    except OSError:
                        pass
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                    handle.close()


def gate_running() -> bool:
    """Whether a full-suite gate holds the machine: defer to it.

    Reads the scheduler's live rows through the lock's own `jobs`
    verb (no privates): a `running` row owned by the gate script is a
    gate in flight. Anything else (a stale queued row, an unrelated
    owner) is not our business.
    """
    result = subprocess.run(
        ["python3", "-m", "library.tools.heavy_work_lock", "jobs"],
        capture_output=True, encoding="utf-8", check=False, timeout=30)
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines():
        state, _, owner = line.partition("pid")
        if state.strip() == "running" and "full_suite_gate" in owner:
            return True
    return False


def broker_serving() -> bool:
    """Whether a Resolve broker answers right now.

    Imported lazily: the resolved package is Resolve-side machinery and
    a CI module must not need it at import time.
    """
    from library.tools.resolved import client
    try:
        return client.ping() is not None
    except Exception:  # noqa: BLE001 - any probe failure means not serving
        return False
