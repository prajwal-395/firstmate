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
import os
import shutil
import subprocess
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


def _git(repo: str, *argv: str, timeout: int = 300) -> str:
    result = subprocess.run(
        ["git", "-C", repo, *argv], capture_output=True,
        encoding="utf-8", check=False, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: "
                           f"{result.stderr.strip()}")
    return result.stdout.strip()


def ensure_mirror(origin: str, state: str = "") -> str:
    """The daemon's origin mirror: one clone, worktrees per run.

    Created once (git clone), refreshed by fetch after. A mirror plus
    worktrees is what makes per-run checkouts seconds-cheap while still
    disposable: the worktree is deleted, the mirror stays.
    """
    root = state or config.state_dir()
    mirror = os.path.join(root, "mirror")
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
    _git(mirror, "fetch", "origin", f"pull/{number}/head:refs/ren-ci/pr-{number}",
         timeout=300)
    return _git(mirror, "rev-parse", f"refs/ren-ci/pr-{number}")


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
    path = os.path.join(root, f"run-{os.getpid()}-{int(time.time())}")
    _git(mirror, "worktree", "add", "--detach", path, rev)
    try:
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
        import threading
        self._dir = os.path.join(state or config.state_dir(), "slots")
        os.makedirs(self._dir, exist_ok=True)
        self._jobs = max(1, jobs)
        self._guard = threading.Lock()

    @contextlib.contextmanager
    def claim(self):
        """One slot for the enclosed block, or None when all are busy.

        A context manager (not acquire/release pairs) because Tier-1
        workers share one Slots across threads: a bare release would
        drop a sibling thread's slot. Yields the slot index, or None -
        the caller schedules, never waits blindly.
        """
        handle = None
        index = None
        with self._guard:
            for candidate in range(self._jobs):
                attempt = open(
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
            yield index
        finally:
            if handle is not None:
                with self._guard:
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
    except Exception:
        return False
