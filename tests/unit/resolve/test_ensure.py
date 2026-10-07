"""The broker as a standalone service: `client.ensure` starts it on demand.

Every test here runs the broker on its OWN socket (`PIPELINE_RESOLVE_LOCK_DIR`
pointed at a tmp dir), so nothing contends with a live broker elsewhere on
the machine, and every broker a test starts is stopped at teardown - a
detached process outlives the test that started it, and a leaked one would
hold the socket of a later run.

The broker connects to Resolve LAZILY (only when a job runs), so these
tests never touch Resolve: they start the broker, ping it, and stop it.
"""

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.ren_refusal import RenRefusal
from library.tools.resolved import __main__ as resolved_cli
from library.tools.resolved import client
from library.tools.resolved.server import SOCKET_NAME, serve


@pytest.fixture
def lock_dir(monkeypatch):
    """A lock dir of our own - the socket, the starter lock and the log.

    Under /tmp and SHORT: a Unix socket path is bounded (104 bytes on
    macOS), and a pytest tmp_path is deep enough to overflow it - the
    broker would die on `AF_UNIX path long` before binding.

    Opts into detached brokers (`REN_TESTS_ALLOW_BROKER_SPAWN`): this
    suite IS the spawn path, so every test here means to spawn, and
    the `started` fixture plus the session teardown stop what is
    started. Any other suite spawning without opting in fails loudly
    in `client.ensure` instead of leaking.
    """
    directory = Path(tempfile.mkdtemp(prefix="ren-ensure-", dir="/tmp"))
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(client.TESTS_ALLOW_SPAWN_ENV, "1")
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def unguarded(monkeypatch):
    """Stand outside the suite's session-wide sole-writer declaration."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


@pytest.fixture
def started(lock_dir):
    """Ensure the broker, and stop at teardown whatever is serving.

    The teardown stops whatever broker answers on the test's socket -
    whether the test started it via `start` or via `client.call` (which
    ensures first) - and waits for it to actually exit: shutdown is
    answered at once but the broker exits on its next serve tick, and a
    broker still shutting down when the directory is removed is left
    holding a socket file that no longer exists.
    """
    def start(**kwargs):
        return client.ensure(path=lock_dir / SOCKET_NAME, **kwargs)

    yield start
    path = lock_dir / SOCKET_NAME
    if client.ping(path) is None:
        return
    try:
        client.call({"op": "shutdown"}, path)
    except (ConnectionError, client.BrokerError, OSError):
        pass
    deadline = time.time() + 10.0
    while client.ping(path) is not None and time.time() < deadline:
        time.sleep(0.05)


def _broker_pids() -> list:
    """Pids of brokers THIS process started (its detached children).

    Matches both daemons: the Python broker (`library.tools.resolved
    serve`) and the Rust broker (`ren-resolved serve`) - the strangler
    switch starts one or the other, and this test counts whichever is
    serving.
    """
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,command="], capture_output=True,
            encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("ps unavailable")
    mine = []
    for line in listing.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid, ppid, command = parts
        if (ppid == str(os.getpid())
                and ("library.tools.resolved" in command
                     or "ren-resolved" in command)
                and " serve" in command):
            mine.append(int(pid))
    return mine


def test_ensure_starts_a_broker_when_none_serves(started, lock_dir):
    assert client.ping(lock_dir / SOCKET_NAME) is None
    answer = started()
    assert answer is not None and answer["pid"] > 0
    assert client.ping(lock_dir / SOCKET_NAME)["pid"] == answer["pid"]
    assert (lock_dir / SOCKET_NAME).exists()


def test_ensure_is_idempotent(started):
    first = started()
    second = started()
    assert first["pid"] == second["pid"]


def test_ensure_starts_the_broker_detached_from_the_starter(started):
    """Its own session, not the starter's process group.

    How agent sessions end is a kill of the whole process group; a broker
    in the starter's group dies with it. `start_new_session` puts the
    broker in a group of its own, so the group's death spares it.
    """
    answer = started()
    pid = answer["pid"]
    pgid = os.getpgid(pid)
    assert pgid != os.getpgid(0), (
        f"broker pid {pid} is in the starter's process group {pgid} - "
        f"it would die with the worker that started it")


def test_concurrent_ensures_start_exactly_one_broker(started):
    """The starter lock serializes racing clients to one broker."""
    answers = []
    barrier = threading.Barrier(8)

    def race():
        barrier.wait()
        answers.append(started())

    threads = [threading.Thread(target=race) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(answers) == 8
    pids = {answer["pid"] for answer in answers}
    assert len(pids) == 1, f"concurrent ensures started brokers {pids}"
    assert len(_broker_pids()) == 1


def test_ensure_after_stop_starts_a_fresh_broker(started, lock_dir):
    first = started()
    client.call({"op": "shutdown"}, lock_dir / SOCKET_NAME)
    deadline = time.time() + 10.0
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.time() < deadline:
        time.sleep(0.05)
    assert client.ping(lock_dir / SOCKET_NAME) is None
    second = started()
    assert second["pid"] != first["pid"]


def test_a_broker_that_dies_on_startup_is_reported_and_retried(lock_dir):
    """Fail fast on a dead starter, and the next client starts one.

    The lock dir stays isolated for the WHOLE test: `_start_detached` is
    restored by hand, never by `monkeypatch.undo()` - an undo also
    undid the fixture's PIPELINE_RESOLVE_LOCK_DIR, and the broker then
    bound the DEFAULT socket (measured: it served production Resolve
    calls on unmerged code).
    """
    class _Dead:
        def poll(self):
            return 1            # exited non-zero

    real = client._start_detached
    client._start_detached = lambda path: _Dead()
    try:
        assert client.ensure(path=lock_dir / SOCKET_NAME, timeout=5) is None
    finally:
        client._start_detached = real
    answer = client.ensure(path=lock_dir / SOCKET_NAME)
    assert answer is not None and answer["pid"] > 0
    client.call({"op": "shutdown"}, lock_dir / SOCKET_NAME)
    deadline = time.time() + 10.0
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.time() < deadline:
        time.sleep(0.05)


def test_ensure_inside_the_broker_is_a_noop(lock_dir):
    """The broker's executor must not try to start itself."""
    client.mark_executor()
    try:
        assert client.ensure(path=lock_dir / SOCKET_NAME) is None
    finally:
        client._executor.active = False
    assert client.ping(lock_dir / SOCKET_NAME) is None


def test_ensure_refuses_the_default_socket_inside_pytest(lock_dir, monkeypatch):
    """Never auto-start a broker on the production socket from a test.

    `_start_detached` is mocked so the guard is verified without a
    real broker: with the lock dir unisolated, `ensure` returns None
    WITHOUT starting anything. (The defect this guards: a test's
    monkeypatch.undo() unset PIPELINE_RESOLVE_LOCK_DIR, and the broker
    bound the default socket - serving production Resolve calls on
    unmerged code.)
    """
    class _Dead:
        def poll(self):
            return 1

    started = []

    def fake_start(path):
        started.append(path)
        return _Dead()

    monkeypatch.delenv(resolve_lock.LOCK_DIR_ENV, raising=False)
    monkeypatch.setattr(client, "_start_detached", fake_start)
    assert client.ensure() is None
    assert started == []


def test_call_starts_the_broker_when_none_serves(started):
    """`call` ensures first, so a client needs no broker of its own."""
    reply = client.call({"op": "ping"})
    assert reply["ok"] is True and reply["pid"] > 0


def test_shutdown_does_not_start_a_broker(lock_dir):
    """The stop command must be able to say 'nothing is serving'."""
    with pytest.raises(ConnectionError):
        client.call({"op": "shutdown"}, lock_dir / SOCKET_NAME)


def test_the_cli_ensure_verb_starts_and_reports(started):
    assert resolved_cli.main(["ensure"]) == 0
    assert resolved_cli.main(["status"]) == 0
    assert resolved_cli.main(["stop"]) == 0
    assert resolved_cli.main(["status"]) == 1


def test_the_cli_stop_reports_when_nothing_serves(lock_dir, capsys):
    assert resolved_cli.main(["stop"]) == 1
    assert "nothing to stop" in capsys.readouterr().out


def test_a_lease_acquisition_starts_the_broker_on_demand(lock_dir, unguarded):
    """The lease path - every Resolve operation - ensures the broker."""
    assert client.ping(lock_dir / SOCKET_NAME) is None
    with resolve_lock.resolve_lease("test: ensure on acquisition",
                                   timeout=10):
        answer = client.ping(lock_dir / SOCKET_NAME)
        assert answer is not None and answer["pid"] > 0
    client.call({"op": "shutdown"}, lock_dir / SOCKET_NAME)
    deadline = time.time() + 10.0
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.time() < deadline:
        time.sleep(0.05)


# ── The worktree guard ────────────────────────────────────────────────

def _production_socket() -> Path:
    """The default socket, whatever this process isolated."""
    return Path(tempfile.gettempdir()) / SOCKET_NAME


def test_ensure_refuses_the_default_socket_from_a_linked_worktree(
        tmp_path, monkeypatch):
    """A client from a linked worktree must not auto-start the production broker.

    The defect (2026-10-05): a worker's tests, a standalone shadow watcher
    run from a worktree, and a live smoke each started a production broker
    from their own branch checkout - one branch then refused every reel
    build. The guard refuses, naming the reason; a broker already serving
    is still used (the ping before the guard).
    """
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    started = []
    pings = []
    monkeypatch.setattr(client, "_start_detached",
                        lambda path: started.append(Path(path)))
    monkeypatch.setattr(client, "ping",
                        lambda path=None: pings.append(path) or None)
    with pytest.raises(RenRefusal) as exc_info:
        client.ensure(path=_production_socket(), timeout=2)
    assert started == []
    assert len(pings) == 1, "the guard pinged once, then refused"
    assert "linked worktree" in exc_info.value.why
    assert exc_info.value.fix, "the refusal names its fix"


def test_ensure_uses_a_broker_already_serving_from_a_linked_worktree(
        tmp_path, monkeypatch):
    """The guard refuses to START a broker, not to USE one serving."""
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    serving = {"ok": True, "pid": 4242}
    real_ping = client.ping
    client.ping = lambda path=None: serving
    started = []
    monkeypatch.setattr(client, "_start_detached",
                        lambda path: started.append(Path(path)))
    try:
        assert client.ensure(path=_production_socket(), timeout=2) is serving
        assert started == []
    finally:
        client.ping = real_ping


class _DeadProcess:
    """A broker that died on startup: `poll` answers at once."""

    def poll(self):
        return 1


def test_ensure_starts_the_default_socket_outside_a_worktree(
        tmp_path, monkeypatch):
    """A client from the installed engine (not a worktree) starts the broker."""
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: False)
    monkeypatch.delenv("REN_ENGINE_ROOT", raising=False)
    monkeypatch.setattr(client, "ping", lambda path=None: None)
    started = []

    def fake_start(path):
        started.append(Path(path))
        return _DeadProcess()

    monkeypatch.setattr(client, "_start_detached", fake_start)
    assert client.ensure(path=_production_socket(), timeout=2) is None
    assert len(started) == 1


def test_ensure_allows_a_worktree_when_ren_engine_root_is_set(
        tmp_path, monkeypatch):
    """`$REN_ENGINE_ROOT` is an explicit engine choice and overrides the guard."""
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    monkeypatch.setenv("REN_ENGINE_ROOT", str(tmp_path))
    monkeypatch.setattr(client, "ping", lambda path=None: None)
    started = []

    def fake_start(path):
        started.append(Path(path))
        return _DeadProcess()

    monkeypatch.setattr(client, "_start_detached", fake_start)
    assert client.ensure(path=_production_socket(), timeout=2) is None
    assert len(started) == 1


def test_is_linked_worktree_detects_a_real_worktree(tmp_path):
    """The signal is git's own: git-dir differing from git-common-dir."""
    git_env = {**os.environ,
               "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    primary = tmp_path / "primary"
    primary.mkdir()
    subprocess.run(["git", "init"], cwd=primary, capture_output=True,
                   check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                   cwd=primary, capture_output=True, check=True, env=git_env)
    assert not client._is_linked_worktree(primary)
    linked = tmp_path / "linked"
    subprocess.run(["git", "worktree", "add", str(linked), "--detach"],
                   cwd=primary, capture_output=True, check=True)
    assert client._is_linked_worktree(linked)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not client._is_linked_worktree(plain)
    assert not client._is_linked_worktree(None)


# ── The bind race ─────────────────────────────────────────────────────

def test_a_second_broker_refuses_to_bind_a_serving_socket(started, lock_dir):
    """A second broker refuses to bind a path another live broker serves.

    The race the bind lock closes: two brokers starting within a second
    of each other (a watcher respawning after a deliberate restart) both
    passed the ping - neither had bound yet - and the second's bind
    orphaned the first, leaving two processes on one socket path.
    """
    socket_path = lock_dir / SOCKET_NAME
    first = started()
    assert first is not None and first["pid"] > 0
    with pytest.raises(SystemExit):
        serve(path=socket_path)
    assert client.ping(socket_path)["pid"] == first["pid"]
