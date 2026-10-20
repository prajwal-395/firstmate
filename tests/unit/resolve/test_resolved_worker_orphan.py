"""A worker must not outlive its broker holding the one Resolve.

The break test (2026-10-20) was blocked for hours: every writer timed
out with

    ren-resolved did not grant 'build reels connect': timed out waiting for Resolve

because `ren resolved stop` stopped the broker and left its
`library.tools.resolved.worker` running - alive 3h10m, parent pid 1 -
holding the instance flock. Restarting the broker started a second
worker beside it, and the second could not have the instance either.
Killing that exact orphan freed the flock.

The status called the free instance busy at the same time:
`ren resolved status` reported `busy: true` with the dead holder's pid
(72326), because it read the lease RECORD - a diagnostic that a killed
holder leaves behind - instead of probing the FLOCK, which is the truth.

Each test here names its defect, and none of them touches a real
Resolve: the worker is driven over its own stdio protocol, the instance
lock is a file in a temporary directory, and every flock reading is
taken by a process that does not hold the lock (macOS's flock is
per-process, so a holder can lock the file again and would answer
"free").
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

from library.tools import resolve_lock  # noqa: E402
from library.tools.resolved import __main__ as resolved_cli  # noqa: E402
from library.tools.resolved import worker  # noqa: E402


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """A lease of our own, never the machine's.

    The same isolation `test_resolve_lock.py` uses: a test that
    competed for the real lease would block on - or block - the
    captain's live session.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    return directory


@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration.

    The suite declares itself the sole writer because its Resolve is a
    mock, so a lease taken here would be no lease at all. These tests
    are ABOUT the lock, so they need the real one.
    """
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


def _pid_alive(pid: int) -> bool:
    """Whether an exact pid is still running (a zombie reads as dead)."""
    try:
        out = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True, encoding="utf-8",
            check=False).stdout.strip()
        if out:
            return not out.startswith("Z")
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_gone(pid: int, timeout: float = 10.0) -> bool:
    """Whether an exact pid is gone within the timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.05)
    return not _pid_alive(pid)


_EXCLUSIVE_FLOCK_PROBE = textwrap.dedent("""
    import fcntl
    with open({path!r}, "a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("BUSY")
        else:
            print("FREE")
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
""")


def _flock_busy(lock_dir: Path) -> bool:
    """Whether a second process cannot take the exclusive flock.

    The flock is the fence; this is the independent reading of it, from
    a process that does not hold it.
    """
    probe = subprocess.run(
        [sys.executable, "-c", _EXCLUSIVE_FLOCK_PROBE.format(
            path=str(lock_dir / resolve_lock.LOCK_FILENAME))],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    return probe.stdout.strip() == "BUSY"


#: A holder in its own process: the flock and the record it publishes.
#: This is what a live writer looks like from outside - and what a
#: killed writer leaves behind (the record, without the flock).
_HOLDER = textwrap.dedent("""
    import fcntl, json, os, socket, sys
    from pathlib import Path
    root = Path(sys.argv[1])
    lock = (root / 'resolve_instance.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX)
    (root / 'resolve_instance.lease.json').write_text(json.dumps({
        'owner': sys.argv[2], 'purpose': sys.argv[3], 'pid': os.getpid(),
        'host': socket.gethostname(), 'since': 1.0}), encoding='utf-8')
    print('held', flush=True)
    sys.stdin.read()
""")


def _start_holder(lock_dir: Path, owner: str, purpose: str) -> subprocess.Popen:
    """A process that holds the instance flock and publishes the record."""
    return subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(lock_dir), owner, purpose],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        encoding="utf-8")


def _fake_broker() -> subprocess.Popen:
    """A process that answers nothing and holds nothing: the broker's pid.

    The worker only ever asks "is that pid still there", so a sleeping
    process is the whole of a broker from the worker's side.
    """
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(300)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def _start_worker(broker_pid: int, lock_dir: Path) -> subprocess.Popen:
    """Start the Resolve worker, told which broker it belongs to."""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else
        [str(REPO)])
    env[worker.BROKER_PID_ENV] = str(broker_pid)
    # A worker that cannot take the instance must say so rather than
    # wait out the production timeout: this test is about the worker
    # ENDING, not about how long it waits.
    env[resolve_lock.TIMEOUT_ENV] = "20"
    env["REN_SHADOW_WATCHER"] = "0"
    return subprocess.Popen(
        [sys.executable, "-m", "library.tools.resolved.worker"],
        env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, cwd=str(REPO), text=True)


def _grant_job(job_id: str = "j1") -> dict:
    """A lease request: the worker takes the instance and gives it back."""
    return {"id": job_id, "kind": "job",
            "job": {"id": job_id, "kind": "lease", "params": {},
                    "project": "", "timeline": "", "qualification": False,
                    "mode": "exclusive", "executed": False}}


# ── the orphan worker ─────────────────────────────────────────────────

def test_a_worker_whose_broker_died_ends_instead_of_holding_the_instance(
        lock_dir, unguarded):
    """The worker ends when its broker is gone.

    The break test's orphan: `ren resolved stop` stopped the broker and
    the worker survived it (parent pid 1, alive 3h10m), holding the
    instance flock - so every later writer timed out with "ren-resolved
    did not grant 'build reels connect': timed out waiting for
    Resolve", and a restarted broker started a second worker beside it.

    The broker says which pid it is; the worker ends when that pid is
    gone. Here the worker is in the middle of TAKING the instance (this
    test holds the lock), which is where the orphan was.
    """
    broker = _fake_broker()
    worker_process = _start_worker(broker.pid, lock_dir)
    try:
        # This process holds the instance, so the worker's lease waits -
        # the worker is inside the lease, holding nothing yet.
        with resolve_lock.resolve_lease("the test holds the instance",
                                        timeout=5):
            assert _flock_busy(lock_dir) is True
            worker_process.stdin.write(json.dumps(_grant_job()) + "\n")
            worker_process.stdin.flush()

            # The broker dies. The worker must not outlive it.
            broker.kill()
            broker.wait(timeout=5)
            assert _wait_gone(worker_process.pid, timeout=15), (
                "the worker outlived its broker - it would keep holding "
                "the one Resolve against every later writer")

            # Nothing of the worker's survives: the instance is still
            # this test's, and no worker holds it.
            assert _flock_busy(lock_dir) is True
        assert _flock_busy(lock_dir) is False
    finally:
        for process in (worker_process, broker):
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def test_the_worker_gives_the_instance_back_when_its_broker_is_gone(
        lock_dir, unguarded):
    """The worker's exit releases the instance lock it holds.

    A worker holds the instance lease while it works. When the broker
    dies, the worker must give the instance back rather than keep it:
    the lock is the one Resolve, and a holder that is gone is a holder
    nobody can wait for.
    """
    with resolve_lock.resolve_lease("the worker holds the instance",
                                    timeout=5):
        assert _flock_busy(lock_dir) is True, (
            "the instance was not held, so nothing was released")
        worker._release_the_instance()
        assert _flock_busy(lock_dir) is False, (
            "the instance is still held after the worker's exit")


# ── the status that called a free instance busy ───────────────────────

def test_the_status_calls_a_free_instance_free_and_the_record_stale(
        lock_dir, capsys):
    """A lease record with no lock behind it is STALE, not busy.

    The break test's second defect: `ren resolved status` reported
    `busy: true` with the dead holder's pid (72326) for hours after the
    flock was free, because it read the diagnostic record instead of
    probing the flock. The record is left on disk - another writer may
    already be publishing its own - so it is marked, not deleted.
    """
    record = {"owner": "pid-72326", "purpose": "build reels connect",
              "pid": 72326, "host": "localhost", "since": time.time()}
    lease_file = lock_dir / resolve_lock.LEASE_FILENAME
    lease_file.write_text(json.dumps(record), encoding="utf-8")

    # No broker serves: the status must still answer about the instance.
    assert resolved_cli.main(["status"]) == 1
    status = json.loads(capsys.readouterr().out)

    assert status["busy"] is False, (
        "a free instance was called busy because a dead holder's record "
        "was read as a lease")
    assert status["lease"] is None
    assert status["stale_lease"]["pid"] == 72326, (
        "the stale record was not reported: the status must say whose "
        "record it is")
    assert lease_file.exists(), (
        "the status unlinked the lease record - another writer may "
        "already be publishing its own")


def test_the_status_reports_a_held_instance_as_busy(lock_dir, capsys):
    """The other half: a held instance is busy, and names its holder.

    The flock probe answers "is it held", and the record beside it
    answers "by whom" - so the stale marking cannot swallow a real
    holder.
    """
    holder = _start_holder(lock_dir, "a real holder", "a real holder")
    try:
        assert holder.stdout.readline().strip() == "held", (
            "the holder never took the instance")
        assert resolved_cli.main(["status"]) == 1
        status = json.loads(capsys.readouterr().out)
        assert status["busy"] is True
        assert status["lease"]["purpose"] == "a real holder"
        assert status["stale_lease"] is None
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)


# ── the daemon's own half: the broker's stop ends its worker ─────────

def _serving_binary() -> Path | None:
    """The binary the broker starter would serve: the one this tree built.

    The defect was in the SERVING path. The starter picked its binary
    from a list of its own, in which the shipped prebuilt came before
    the checkout's build, so the broker served code the tree had
    already replaced - the prebuilt carried no broker pid for its
    worker to watch, and no stop of its own. A test that starts the
    build output instead of the SERVING binary never sees that.
    """
    from library.tools import native_refresh

    return native_refresh.serving_binary(REPO, "ren-resolved")


def _worker_environment(pid: int) -> str:
    """The worker's environment, as the machine reports it."""
    try:
        return subprocess.run(
            ["ps", "eww", "-p", str(pid)],
            capture_output=True, encoding="utf-8", check=False).stdout
    except OSError:
        return ""


@pytest.mark.resolve_live
def test_the_serving_broker_names_itself_to_its_worker(tmp_path):
    """The worker must know which broker it belongs to.

    The break test's orphan: `ren resolved stop` stopped the broker and
    the worker survived it (parent pid 1, alive 3h10m), holding the
    instance flock - so every later writer timed out with "ren-resolved
    did not grant 'build reels connect': timed out waiting for
    Resolve". The worker ends when its broker is gone, and it can only
    know that if the broker named itself in the worker's environment.

    The broker that serves is the one this tree built. The prebuilt
    that served on 2026-10-20 named nothing, so the watch could never
    fire - measured on the machine: the worker it started carried no
    `REN_RESOLVED_BROKER_PID` at all.
    """
    binary = _serving_binary()
    if binary is None:
        pytest.skip("no ren-resolved binary to serve")

    from library.tools.resolved.server import SOCKET_NAME

    lock_dir = Path(tempfile.mkdtemp(prefix="ren-orphan-", dir="/tmp"))
    socket_path = lock_dir / SOCKET_NAME
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else
        [str(REPO)])
    env["REN_SHADOW_WATCHER"] = "0"
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    log = open(lock_dir / "ren-resolved.log", "ab", buffering=0)
    daemon = subprocess.Popen(
        [str(binary), "serve", str(socket_path)],
        env=env, stdin=subprocess.DEVNULL, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.time() + 30
        worker_pids: list[int] = []
        while time.time() < deadline and not worker_pids:
            worker_pids = _worker_pids(daemon.pid)
            time.sleep(0.1)
        assert worker_pids, "the daemon started no worker"

        environment = _worker_environment(worker_pids[0])
        assert f"{worker.BROKER_PID_ENV}={daemon.pid}" in environment, (
            f"the worker does not know its broker: it carries no "
            f"{worker.BROKER_PID_ENV}, so it can never learn the broker "
            f"is gone - and it outlives it, holding the one Resolve")
    finally:
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(timeout=5)
        log.close()
        shutil.rmtree(lock_dir, ignore_errors=True)


@pytest.mark.resolve_live
def test_the_daemon_stops_its_worker_when_the_broker_stops(tmp_path):
    """`ren resolved stop` must not leave the worker behind.

    The daemon's shutdown races its own detached threads, so the
    worker's process could outlive the broker that started it - and a
    worker that outlives its broker holds the one Resolve against every
    later writer.

    It runs against the binary that SERVES - the one the starter picks
    - because that is where the defect was: the shipped prebuilt served
    while the tree's own build sat beside it, and the prebuilt left its
    worker running (measured 2026-10-20).
    """
    binary = _serving_binary()
    if binary is None:
        pytest.skip("no ren-resolved binary to serve")

    from library.tools.resolved import client
    from library.tools.resolved.server import SOCKET_NAME

    lock_dir = Path(tempfile.mkdtemp(prefix="ren-orphan-", dir="/tmp"))
    socket_path = lock_dir / SOCKET_NAME
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO), env.get("PYTHONPATH", "")] if env.get("PYTHONPATH") else
        [str(REPO)])
    env["REN_SHADOW_WATCHER"] = "0"
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    log = open(lock_dir / "ren-resolved.log", "ab", buffering=0)
    daemon = subprocess.Popen(
        [str(binary), "serve", str(socket_path)],
        env=env, stdin=subprocess.DEVNULL, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True)
    worker_pids: list[int] = []
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            worker_pids = _worker_pids(daemon.pid)
            if worker_pids:
                break
            time.sleep(0.1)
        assert worker_pids, "the daemon started no worker to stop"

        client.call({"op": "shutdown"}, socket_path)
        deadline = time.time() + 15
        while time.time() < deadline and any(
                _pid_alive(pid) for pid in worker_pids):
            time.sleep(0.05)
        assert not any(_pid_alive(pid) for pid in worker_pids), (
            f"the broker stopped and left its worker running: {worker_pids}")
    finally:
        with contextlib.suppress(Exception):
            # The daemon may already be gone; the shutdown is best effort.
            client.call({"op": "shutdown"}, socket_path)
        if daemon.poll() is None:
            daemon.kill()
            daemon.wait(timeout=5)
        log.close()
        shutil.rmtree(lock_dir, ignore_errors=True)


def _worker_pids(broker_pid: int) -> list[int]:
    """The worker processes the broker started, by parent pid."""
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,command="],
            capture_output=True, encoding="utf-8", check=False).stdout
    except OSError:
        return []
    found = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, ppid, command = parts
        if ppid.strip() == str(broker_pid) and "resolved.worker" in command:
            found.append(int(pid))
    return found
