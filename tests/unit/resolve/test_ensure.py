"""The broker as a standalone service: `client.ensure` starts it on demand.

Every test here runs the broker on its OWN socket (`PIPELINE_RESOLVE_LOCK_DIR`
pointed at a tmp dir), so nothing contends with a live broker elsewhere on
the machine, and every broker a test starts is stopped at teardown - a
detached process outlives the test that started it, and a leaked one would
hold the socket of a later run.

The broker connects to Resolve LAZILY (only when a job runs), so these
tests never touch Resolve: they start the broker, ping it, and stop it.
"""

import json
import os
import shutil
import socket
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

    Seeds this checkout's ren_native cache entry (an unloaded
    stand-in, never built): broker startup runs the real refresh, and
    without an entry it would compile the extension for real - minutes
    on a cold target - into the machine's cache. The seed reads as
    fresh so startup never builds; nothing here loads it.
    """
    directory = Path(tempfile.mkdtemp(prefix="ren-ensure-", dir="/tmp"))
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    monkeypatch.setenv(client.TESTS_ALLOW_SPAWN_ENV, "1")
    from library.tools import ren_native_cache, ren_native_source

    cache = directory / "native-cache"
    monkeypatch.setenv(ren_native_cache.CACHE_ENV, str(cache))
    root = client._engine_root()
    fingerprint = (ren_native_source.source_fingerprint(root)
                   if root is not None else None)
    if fingerprint is not None:
        entry = cache / fingerprint
        entry.mkdir(parents=True, exist_ok=True)
        # Named for the RUNNING interpreter (`ren_native_cache`
        # only counts a build the running interpreter would itself
        # import): any other name reads as missing and startup would
        # compile the extension for real.
        from importlib.machinery import EXTENSION_SUFFIXES
        (entry / ("ren_native" + EXTENSION_SUFFIXES[0])).write_bytes(b"test-seed")
        (entry / ren_native_cache.FINGERPRINT_FILENAME).write_text(
            fingerprint + "\n", encoding="utf-8")
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
    deadline = time.monotonic() + 10.0
    while client.ping(path) is not None and time.monotonic() < deadline:
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
    deadline = time.monotonic() + 10.0
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.monotonic() < deadline:
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
    deadline = time.monotonic() + 10.0
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.monotonic() < deadline:
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


def test_ping_and_status_report_the_exclusive_lease_as_busy(
        started, lock_dir, capsys, monkeypatch, unguarded):
    """A live broker reports its lease without routing status through Resolve."""
    original = started()
    broker_serving = client.serving
    # Model a cooperating writer outside this broker: it takes the same
    # machine lease directly instead of asking the broker for a turn.
    monkeypatch.setattr(client, "serving", lambda path=None: False)
    with resolve_lock.resolve_lease("fake exclusive broker job", timeout=1):
        monkeypatch.setattr(client, "serving", broker_serving)
        answer = client.ping(lock_dir / SOCKET_NAME)
        assert answer is not None
        assert answer["serving"] is True
        assert answer["responsive"] is True
        assert answer["busy"] is True
        assert answer["lease"]["purpose"] == "fake exclusive broker job"
        assert answer["lease"]["since"] > 0
        assert client.serving(lock_dir / SOCKET_NAME) is True
        assert started()["pid"] == original["pid"]

        assert resolved_cli.main(["status"]) == 0
        status = json.loads(capsys.readouterr().out)
        assert status["serving"] is True
        assert status["busy"] is True
        assert status["lease"]["purpose"] == "fake exclusive broker job"


def test_a_slow_ping_on_a_connected_busy_broker_still_counts_as_serving(
        lock_dir, monkeypatch, capsys, unguarded):
    """A connected socket stays serving when its delayed ping misses timeout."""
    path = lock_dir / SOCKET_NAME
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    accepted = threading.Event()
    release = threading.Event()

    def hold_ping_reply():
        connection, _ = listener.accept()
        with connection:
            accepted.set()
            release.wait(timeout=5)

    server = threading.Thread(target=hold_ping_reply, daemon=True)
    server.start()
    monkeypatch.setattr(client, "CONNECT_TIMEOUT_SECONDS", 0.02)
    broker_serving = client.serving
    monkeypatch.setattr(client, "serving", lambda path=None: False)
    try:
        with resolve_lock.resolve_lease("fake slow exclusive job", timeout=1):
            monkeypatch.setattr(client, "serving", broker_serving)
            answer = client.ping(path)
            assert accepted.wait(timeout=1)
            assert answer is not None
            assert answer["ok"] is False
            assert answer["pid"] is None
            assert answer["serving"] is True
            assert answer["responsive"] is False
            assert answer["busy"] is True
            assert answer["lease"]["purpose"] == "fake slow exclusive job"
            assert answer["lease"]["since"] > 0
            assert client.serving(path) is True

            assert resolved_cli.main(["status"]) == 0
            status = json.loads(capsys.readouterr().out)
            assert status["serving"] is True
            assert status["responsive"] is False
            assert status["busy"] is True
            assert status["lease"]["purpose"] == "fake slow exclusive job"
    finally:
        release.set()
        server.join(timeout=1)
        listener.close()


def test_the_cli_status_reports_an_absent_socket_as_not_serving(
        lock_dir, capsys):
    assert resolved_cli.main(["status"]) == 1
    assert json.loads(capsys.readouterr().out) == {
        "serving": False,
        "pid": None,
        "responsive": False,
        "busy": False,
        "lease": None,
        "binary": "none",
        "rebuild": "not applicable",
    }


def test_the_cli_stop_reports_when_nothing_serves(lock_dir, capsys):
    assert resolved_cli.main(["stop"]) == 1
    assert "nothing to stop" in capsys.readouterr().out


def test_the_cli_stop_refuses_the_production_socket_in_pytest(
        monkeypatch, capsys):
    """`ren resolved stop` inside the suite must never reach the shared
    broker - whatever answers on the default socket is production, and
    stopping it takes every lane's Resolve with it. The refusal fires
    before any connection is opened, so this test is safe to run while
    the shared broker serves."""
    monkeypatch.delenv(resolve_lock.LOCK_DIR_ENV, raising=False)
    calls = []
    monkeypatch.setattr(
        client, "call",
        lambda *args, **kwargs: calls.append((args, kwargs)))
    assert resolved_cli.main(["stop"]) == 1
    assert calls == []
    assert "refusing to stop the production broker" in capsys.readouterr().err


def test_the_cli_stop_refuses_the_production_socket_from_a_linked_worktree(
        monkeypatch, capsys):
    """A lane worktree stops only the broker on its own socket: the
    shared one outlives every lane."""
    monkeypatch.delenv(resolve_lock.LOCK_DIR_ENV, raising=False)
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    calls = []
    monkeypatch.setattr(
        client, "call",
        lambda *args, **kwargs: calls.append((args, kwargs)))
    assert resolved_cli.main(["stop"]) == 1
    assert calls == []
    assert "refusing to stop the production broker" in capsys.readouterr().err


def test_the_broker_logs_its_lifecycle_to_its_log(lock_dir, monkeypatch):
    """Every start and every stop lands timestamped in the broker log
    with which tree serves: the 2026-10-08 stop was found with no
    known cause because neither was logged. (The Python daemon here;
    the Rust daemon logs the same three lines.)"""
    from library.tools import native_refresh
    from library.tools.resolved import client as broker_client

    monkeypatch.setenv("REN_RESOLVED_DAEMON", "python")
    monkeypatch.setattr(
        native_refresh, "ensure_current", lambda *args, **kwargs: {})
    target = lock_dir / SOCKET_NAME
    try:
        answer = broker_client.ensure(path=target)
        assert answer is not None
        log = lock_dir / "ren-resolved.log"
        assert "serving on" in log.read_text(encoding="utf-8")
        broker_client.call({"op": "shutdown"}, target)
        deadline = time.monotonic() + 10.0
        while broker_client.ping(target) is not None \
                and time.monotonic() < deadline:
            time.sleep(0.05)
        assert broker_client.ping(target) is None
        text = log.read_text(encoding="utf-8")
        assert "shutdown requested" in text
        assert "stopping" in text
    finally:
        try:
            broker_client.call({"op": "shutdown"}, target)
        except (ConnectionError, broker_client.BrokerError, OSError):
            pass
        deadline = time.monotonic() + 10.0
        while broker_client.ping(target) is not None \
                and time.monotonic() < deadline:
            time.sleep(0.05)


def test_a_lease_acquisition_starts_the_broker_on_demand(lock_dir, unguarded):
    """The lease path - every Resolve operation - ensures the broker."""
    assert client.ping(lock_dir / SOCKET_NAME) is None
    with resolve_lock.resolve_lease("test: ensure on acquisition",
                                   timeout=10):
        answer = client.ping(lock_dir / SOCKET_NAME)
        assert answer is not None and answer["pid"] > 0
    client.call({"op": "shutdown"}, lock_dir / SOCKET_NAME)
    deadline = time.monotonic() + 10.0
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.monotonic() < deadline:
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


def test_ensure_refuses_worktree_when_bin_ren_sets_engine_root(
        monkeypatch):
    """The `bin/ren` wrapper sets REN_ENGINE_ROOT to this worktree.

    That value names the code currently executing, not an installed
    engine. It must not bypass the guard and start a detached broker
    whose lifetime outlasts this disposable checkout.
    """
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    monkeypatch.setenv("REN_ENGINE_ROOT", str(client._engine_root()))
    monkeypatch.setattr(client, "ping", lambda path=None: None)
    started = []

    def fake_start(path):
        started.append(Path(path))
        return _DeadProcess()

    monkeypatch.setattr(client, "_start_detached", fake_start)
    with pytest.raises(RenRefusal) as exc_info:
        client.ensure(path=_production_socket(), timeout=2)
    assert started == []
    assert "linked worktree" in exc_info.value.why


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


# ── Idle rebuild-and-swap ────────────────────────────────────────────
# A serving broker otherwise serves its stale build until someone
# restarts it by hand (measured 2026-10-08: an hour stale, heavy-work
# queue empty, broker idle). ensure refreshes opportunistically around
# an idle production broker and swaps onto a just-rebuilt binary.


def _fake_target_ping(monkeypatch, answers, test_target):
    """Fake ping only for the test target; the session teardown guard
    pings its own socket with the real client, so a global fake would
    read as a leaked broker (measured 2026-10-08). Anything else
    delegates to the real ping.
    """
    real_ping = client.ping
    script = list(answers)

    def fake(target):
        if str(target) != str(test_target):
            return real_ping(target)
        if script:
            return script.pop(0)
        return None

    monkeypatch.setattr(client, "ping", fake)


def _fake_target_call(monkeypatch, calls, test_target):
    """Record requests to the test target; delegate everything else."""
    real_call = client.call

    def fake(request, target=None, **kwargs):
        if str(target) != str(test_target):
            return real_call(request, target, **kwargs)
        calls.append(request)
        return {"ok": True}

    monkeypatch.setattr(client, "call", fake)


IDLE_ANSWER = {"pid": 4242, "serving": True, "responsive": True,
               "busy": False, "lease": None}


def _as_production(monkeypatch):
    """Pretend this starter may run the production broker.

    The pre-ping guard refuses the default socket inside pytest, so the
    first `_is_default_socket` probe answers False (reach the serving
    path); every later probe answers True (take the production path).
    """
    probes = []

    def fake_default(target):
        probes.append(1)
        return len(probes) > 1

    monkeypatch.setattr(client, "_is_default_socket", fake_default)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: False)
    monkeypatch.delenv("REN_RESOLVED_DAEMON", raising=False)


def test_idle_production_broker_swaps_onto_a_rebuild(
        lock_dir, monkeypatch):
    """Rebuilt binary + idle broker -> shutdown, fresh broker, same path."""
    from library.tools import native_refresh

    target = lock_dir / SOCKET_NAME
    _as_production(monkeypatch)
    new_answer = dict(IDLE_ANSWER, pid=4243)
    _fake_target_ping(
        monkeypatch, [dict(IDLE_ANSWER), dict(IDLE_ANSWER), None, None,
                      dict(new_answer)], target)
    calls = []
    _fake_target_call(monkeypatch, calls, target)
    monkeypatch.setattr(
        native_refresh, "ensure_current",
        lambda root, **kwargs: {"ren-resolved": "rebuilt"})
    started = []

    class _Process:
        def poll(self):
            return None

    monkeypatch.setattr(
        client, "_start_detached",
        lambda path: started.append(path) or _Process())
    assert client.ensure(path=target, timeout=30) == new_answer
    assert calls == [{"op": "shutdown"}]
    assert started == [target]


def test_idle_production_broker_keeps_serving_when_busy_or_fresh(
        lock_dir, monkeypatch):
    """A busy broker is never restarted, and a fresh tree never rebuilds
    into a restart: no shutdown, no start."""
    from library.tools import native_refresh

    target = lock_dir / SOCKET_NAME
    _as_production(monkeypatch)
    busy = dict(IDLE_ANSWER, busy=True, lease={"holder": "someone"})
    _fake_target_ping(monkeypatch, [dict(busy)], target)
    calls = []
    _fake_target_call(monkeypatch, calls, target)
    monkeypatch.setattr(
        native_refresh, "ensure_current",
        lambda root, **kwargs: {"ren-resolved": "rebuilt"})
    started = []
    monkeypatch.setattr(
        client, "_start_detached",
        lambda path: started.append(path))
    assert client.ensure(path=target, timeout=30) == busy
    assert calls == []
    assert started == []

    _as_production(monkeypatch)  # fresh probe count for the second ensure
    _fake_target_ping(
        monkeypatch, [dict(IDLE_ANSWER), dict(IDLE_ANSWER)], target)
    monkeypatch.setattr(
        native_refresh, "ensure_current",
        lambda root, **kwargs: {"ren-resolved": "fresh"})
    assert client.ensure(path=target, timeout=30) == IDLE_ANSWER
    assert calls == []
    assert started == []


def test_idle_production_broker_keeps_serving_where_it_will_not_stop(
        lock_dir, monkeypatch):
    """A shutdown the broker ignores is not followed by a second broker."""
    from library.tools import native_refresh

    target = lock_dir / SOCKET_NAME
    _as_production(monkeypatch)
    _fake_target_ping(monkeypatch, [dict(IDLE_ANSWER)] * 30, target)
    calls = []
    _fake_target_call(monkeypatch, calls, target)
    monkeypatch.setattr(
        native_refresh, "ensure_current",
        lambda root, **kwargs: {"ren-resolved": "rebuilt"})
    started = []
    monkeypatch.setattr(
        client, "_start_detached",
        lambda path: started.append(path))
    assert client.ensure(path=target, timeout=1) == IDLE_ANSWER
    assert calls == [{"op": "shutdown"}]
    assert started == []
