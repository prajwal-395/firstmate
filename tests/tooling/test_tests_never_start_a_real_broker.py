"""No test ever starts - or connects to - a real broker on the default socket.

Incident 2026-10-05, about 40 minutes after the broker became a
standalone service any client starts on demand: 49 orphaned `python -m
library.tools.resolved serve` processes were found, each holding the
production default socket (`$TMPDIR/ren-resolved.sock`). Each new one
had re-bound the socket path, so real clients talked to whichever
started last. They were started by test runs, not by Ren's real use -
and they ran under the caller's homebrew Python rather than Ren's venv
interpreter.

Three lines of defence, all pinned here:

1. `tests/conftest.py` points `PIPELINE_RESOLVE_LOCK_DIR` at a
   per-session temp dir (set with `os.environ`, never `monkeypatch`,
   so no `monkeypatch.undo()` inside a test can remove it), and stops
   every broker a test starts at teardown - by exact pid, including
   ones started by hand with `subprocess.Popen`, which bypass
   `client._start_detached` - and fails the whole run at session end
   where any broker the session started is still alive.
2. `library.tools.resolved.client.ensure` refuses the default
   production socket while running under pytest - judged by the
   TARGET, so an explicit default path or an unisolated child refuses
   as well - refuses to SPAWN on any other socket unless the test
   explicitly opts in (`REN_TESTS_ALLOW_BROKER_SPAWN=1`, which a
   lock-dir fixture sets for the suite that IS the spawn path), and
   `client._start_detached` launches the Python fallback with Ren's venv
   interpreter rather than whatever Python ran the caller (the default
   Rust path launches the built ren-resolved binary instead), with the
   shadow watcher forced off.
3. A test's subprocess children inherit the isolation: the update
   install test carries `PYTEST_CURRENT_TEST`,
   `PIPELINE_RESOLVE_LOCK_DIR` and `REN_SHADOW_WATCHER=0` into the
   `ren update` child (whose own doctor probe takes the lease), so the
   chain runs brokerless instead of spawning a detached broker the
   parent test could never record.

The routine unit tests below pin the guard and the interpreter. The
nested `test_nested_broker_isolation.py` replay covers both the broker-
exercising suites and the hand-started Python broker the teardown used
to miss (2026-10-07: an orphaned `python -m library.tools.resolved
serve`, parent pid 1, survived because the Popen recorder only matched
the Rust argv). It proves the child runs leave no broker behind and
never touch the default socket.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.resolved import client
from library.tools.resolved.server import SOCKET_NAME

TESTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_DIR.parent


def _project_conftest(pytestconfig):
    """Return pytest's loaded conftest, independent of the ``tests`` package.

    ``tests`` is an implicit namespace package, so a site-installed regular
    package with the same name can win import resolution in an xdist worker.
    Resolve the hook by its file to keep this test bound to pytest's session
    guard instead of an unrelated ``tests.conftest`` module.
    """
    conftest_path = TESTS_DIR / "conftest.py"
    for module in pytestconfig.pluginmanager.get_plugins():
        module_path = getattr(module, "__file__", None)
        if module_path and Path(module_path).resolve() == conftest_path:
            return module
    raise AssertionError(f"pytest did not register {conftest_path} as a plugin")


def _production_socket() -> Path:
    """The default socket, whatever this process isolated."""
    return Path(tempfile.gettempdir()) / SOCKET_NAME


# ── 1. The guard, in process ──────────────────────────────────────────

def test_ensure_refuses_to_spawn_without_an_explicit_opt_in(
        tmp_path, monkeypatch):
    """A stray spawn inside the suite fails its test instead of leaking.

    Incident 2026-10-06: 144 orphaned test brokers, each holding the
    live Resolve scripting port, respawned by a later `ensure`. Spawning
    a detached broker is opt-in (`REN_TESTS_ALLOW_BROKER_SPAWN=1`,
    which a lock-dir fixture sets for the suite that IS the spawn
    path); without it `ensure` raises where nothing is serving. Using
    a broker that is already serving still works - the guard only
    closes the spawn.
    """
    from library.tools.ren_refusal import RenRefusal

    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    monkeypatch.delenv(client.TESTS_ALLOW_SPAWN_ENV, raising=False)
    started = []
    monkeypatch.setattr(
        client, "_start_detached",
        lambda path: started.append(Path(path)))
    monkeypatch.setattr(client, "ping", lambda path=None: None)
    with pytest.raises(RenRefusal):
        client.ensure(path=directory / SOCKET_NAME, timeout=2)
    assert started == []


def test_ensure_uses_a_broker_already_serving_without_opt_in(
        tmp_path, monkeypatch):
    """The opt-in closes the spawn, not the use of a serving broker."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    monkeypatch.delenv(client.TESTS_ALLOW_SPAWN_ENV, raising=False)
    serving = {"ok": True, "pid": 4242}
    wanted = str(directory / SOCKET_NAME)

    def fake_ping(path=None):
        # Only this test's socket answers: the session teardown pings
        # its own socket while this mock is still up, and a mock that
        # answers everywhere makes the teardown "stop" a broker that
        # was never started.
        try:
            target = str(client._path(path))
        except (OSError, ValueError):
            return None
        return serving if target == wanted else None

    monkeypatch.setattr(client, "ping", fake_ping)
    started = []
    monkeypatch.setattr(
        client, "_start_detached",
        lambda path: started.append(Path(path)))
    assert client.ensure(path=directory / SOCKET_NAME, timeout=2) is serving
    assert started == []


def test_ensure_refuses_an_explicit_default_socket_while_isolated(
        tmp_path, monkeypatch):
    """The bypass the first guard missed: isolated, but aimed at default.

    The original refusal only looked at whether `PIPELINE_RESOLVE_LOCK_DIR`
    was set. With it set and the default path passed explicitly, `ensure`
    went on to ping - and where nothing answered, to spawn on - the
    production socket. Judged by target, it returns None before either.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    started = []
    pings = []

    monkeypatch.setattr(
        client, "_start_detached",
        lambda path: started.append(Path(path)))
    monkeypatch.setattr(
        client, "ping",
        lambda path=None: pings.append(path) or None)
    assert client.ensure(path=_production_socket(), timeout=2) is None
    assert started == []
    assert pings == []


def test_ensure_refuses_the_default_socket_from_a_test_child_process(
        tmp_path, monkeypatch):
    """A test's non-pytest child refuses the default socket too.

    The child never imports pytest, but it inherits `PYTEST_CURRENT_TEST`
    from the test that spawned it - which is what makes it the suite's
    doing rather than Ren's real use.
    """
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    # The child never imports pytest; it inherits PYTEST_CURRENT_TEST
    # from the test that spawns it (set by pytest itself during the
    # call phase, inherited through the environment below) - which is
    # what makes it the suite's doing rather than Ren's real use.
    monkeypatch.setenv("PYTEST_CURRENT_TEST",
                       "test_tests_never_start_a_real_broker.py::x (call)")
    probe = (
        "import os, sys, tempfile; sys.path.insert(0, {repo!r}); "
        "from library.tools.resolved import client; "
        "client._start_detached = lambda path: print('SPAWNED ' + str(path)); "
        "client.ping = lambda path=None: None; "
        "print('ensure ->', client.ensure(path=tempfile.gettempdir() "
        "+ '/" + SOCKET_NAME + "', timeout=2))").format(repo=str(REPO_ROOT))
    env = dict(os.environ)
    env[resolve_lock.LOCK_DIR_ENV] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(REPO_ROOT), env=env,
        capture_output=True, encoding="utf-8", timeout=120, check=False)
    assert "SPAWNED" not in result.stdout, (
        "a test child spawned a broker on the default socket:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    assert "ensure -> None" in result.stdout, (
        f"a test child did not refuse the default socket:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")


def test_the_broker_spawns_with_the_ren_venv_interpreter(tmp_path, monkeypatch):
    """The Python fallback starts with Ren's interpreter, not the caller's.

    The orphans ran under homebrew Python 3.14 - `sys.executable` of
    whichever test worker started them. The fallback spawn path resolves the
    ladder in `shared_environment` instead.

    The Rust broker is the default - the spawn path there is the built
    binary, pinned by `test_the_rust_broker_spawns_with_the_built_binary`
    below - so this guard pins the fallback explicitly via
    `REN_RESOLVED_DAEMON=python`.
    """
    from library.tools import shared_environment

    expected, why_not = shared_environment.python_interpreter(str(REPO_ROOT))
    if not expected:
        pytest.skip(f"no Ren venv on this machine: {why_not}")
    monkeypatch.setenv("REN_RESOLVED_DAEMON", "python")
    seen = []

    class _Child:
        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        seen.append(argv)
        return _Child()

    monkeypatch.setattr(client.subprocess, "Popen", fake_popen)
    sock_dir = tmp_path / "locks"
    sock_dir.mkdir()
    client._start_detached(sock_dir / SOCKET_NAME)
    assert seen and seen[0][0] == expected, (
        f"the broker would spawn with {seen[0][0] if seen else 'nothing'} "
        f"instead of Ren's interpreter {expected}")
    assert seen[0][1:4] == ["-m", "library.tools.resolved", "serve"]


def test_the_rust_broker_spawns_with_the_built_binary(tmp_path, monkeypatch):
    """The default (Rust) spawn path is the built binary, not an interpreter.

    With Rust the default, `ensure` starts the ren-resolved binary - the
    same protocol, a different process. The freshness check against
    `native_refresh` is mocked to fresh here so the guard pins the argv
    shape without requiring a real build; a stale or missing binary must
    still refuse in `_start_detached` itself. Both the unset default and
    the explicit `REN_RESOLVED_DAEMON=rust` take this path.
    """
    from library.tools import native_refresh

    fake_binary = tmp_path / "ren-resolved"
    monkeypatch.setattr(client, "_rust_daemon", lambda: fake_binary)
    monkeypatch.setattr(
        native_refresh, "status", lambda root, name: ("fresh", "test"))
    seen = []

    class _Child:
        def poll(self):
            return None

    def fake_popen(argv, **kwargs):
        seen.append(argv)
        return _Child()

    monkeypatch.setattr(client.subprocess, "Popen", fake_popen)
    sock_dir = tmp_path / "locks"
    sock_dir.mkdir()
    for daemon_env in (None, "rust"):
        if daemon_env is None:
            monkeypatch.delenv("REN_RESOLVED_DAEMON", raising=False)
        else:
            monkeypatch.setenv("REN_RESOLVED_DAEMON", daemon_env)
        seen.clear()
        client._start_detached(sock_dir / SOCKET_NAME)
        label = "default" if daemon_env is None else "REN_RESOLVED_DAEMON=rust"
        assert seen and seen[0][0] == str(fake_binary), (
            f"the Rust broker ({label}) would spawn with "
            f"{seen[0][0] if seen else 'nothing'} instead of {fake_binary}")
        # The socket is NAMED, not derived: the daemon and the client
        # must never disagree about where the broker serves.
        assert seen[0][1:] == ["serve", str(sock_dir / SOCKET_NAME)]


# ── 2. The incident's shape, replayed in a subprocess ────────────────

def _broker_pids() -> set:
    """Pids of every `resolved serve` process on the machine."""
    try:
        listing = subprocess.run(
            ["ps", "-eo", "pid=,command="], capture_output=True,
            encoding="utf-8", check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("ps unavailable")
    found = set()
    for line in listing.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid, command = parts
        if (("library.tools.resolved" in command or "ren-resolved" in command)
                and " serve" in command
                and "test_tests_never_start_a_real_broker" not in command):
            found.add(int(pid))
    return found


# ── 3. The session-end leak guard ──────────────────────────────────

def test_the_session_guard_fails_where_a_tracked_broker_is_alive(pytestconfig):
    """The session-end guard names a tracked broker that is still alive.

    A live pid (this process) stands in for a leaked broker; a dead
    entry and an empty session change nothing. Only tracked entries
    are read - the production broker is never touched.
    """
    conftest = _project_conftest(pytestconfig)

    kept = list(conftest._SESSION_BROKERS)
    try:
        conftest._SESSION_BROKERS.append(
            {"pid": os.getpid(), "socket": None, "node": "guard-probe"})

        class _Session:
            exitstatus = 0

        session = _Session()
        conftest.pytest_sessionfinish(session, 0)
        assert session.exitstatus != 0, (
            "the session guard passed with a tracked broker still alive")
    finally:
        conftest._SESSION_BROKERS[:] = kept


def test_the_session_guard_passes_where_nothing_tracked_is_alive(pytestconfig):
    """Dead tracked entries and an empty session are not failures."""
    conftest = _project_conftest(pytestconfig)

    kept = list(conftest._SESSION_BROKERS)
    try:
        conftest._SESSION_BROKERS.append(
            {"pid": 2 ** 30, "socket": None, "node": "guard-probe-dead"})

        class _Session:
            exitstatus = 0

        session = _Session()
        conftest.pytest_sessionfinish(session, 0)
        assert session.exitstatus == 0, (
            "the session guard failed with nothing alive")
    finally:
        conftest._SESSION_BROKERS[:] = kept
