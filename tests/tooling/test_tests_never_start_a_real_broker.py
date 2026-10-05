"""No test ever starts - or connects to - a real broker on the default socket.

Incident 2026-10-05, about 40 minutes after the broker became a
standalone service any client starts on demand: 49 orphaned `python -m
library.tools.resolved serve` processes were found, each holding the
production default socket (`$TMPDIR/ren-resolved.sock`). Each new one
had re-bound the socket path, so real clients talked to whichever
started last. They were started by test runs, not by Ren's real use -
and they ran under the caller's homebrew Python rather than Ren's venv
interpreter.

Two lines of defence, both pinned here:

1. `tests/conftest.py` points `PIPELINE_RESOLVE_LOCK_DIR` at a
   per-session temp dir (set with `os.environ`, never `monkeypatch`,
   so no `monkeypatch.undo()` inside a test can remove it), and stops
   every broker a test starts at teardown.
2. `library.tools.resolved.client.ensure` refuses the default
   production socket while running under pytest - judged by the
   TARGET, so an explicit default path or an unisolated child refuses
   as well - and `client._start_detached` launches the broker with
   Ren's venv interpreter rather than whatever Python ran the caller.

The unit tests below pin the guard and the interpreter; the
subprocess test replays the incident's shape - the broker-exercising
suites, run as a worker would run them - and proves it leaves no
broker process behind and never touches the default socket.
"""

import json
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


def _production_socket() -> Path:
    """The default socket, whatever this process isolated."""
    return Path(tempfile.gettempdir()) / SOCKET_NAME


# ── 1. The guard, in process ──────────────────────────────────────────

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
    """`ensure` starts the broker with Ren's interpreter, not the caller's.

    The orphans ran under homebrew Python 3.14 - `sys.executable` of
    whichever test worker started them. The spawn path resolves the
    ladder in `shared_environment` instead.
    """
    from library.tools import shared_environment

    expected, why_not = shared_environment.python_interpreter(str(REPO_ROOT))
    if not expected:
        pytest.skip(f"no Ren venv on this machine: {why_not}")
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


# ── 2. The incident's shape, replayed in a subprocess ────────────────

_BROKER_WATCH_PLUGIN = '''
"""Fails the report (not the run) where a test touches the default socket."""
import json
import os
import sys
import tempfile
from pathlib import Path

REPORT = Path(os.environ["PIPELINE_BROKER_WATCH_REPORT"])
DEFAULT = Path(tempfile.gettempdir()) / "ren-resolved.sock"

events = {"spawns": [], "default_pings": [], "default_calls": [],
          "default_ensures": []}


def _is_default(target):
    try:
        return (os.path.realpath(target) == os.path.realpath(DEFAULT))
    except OSError:
        return False


def pytest_configure(config):
    from library.tools.resolved import client

    orig_ensure = client.ensure
    orig_start = client._start_detached
    orig_ping = client.ping
    orig_call = client.call
    orig_python = client._broker_python

    def ensure(path=None, timeout=15.0):
        try:
            target = str(client._path(path))
        except Exception:
            target = "<unresolvable>"
        if _is_default(target):
            events["default_ensures"].append(target)
        return orig_ensure(path=path, timeout=timeout)

    def start(path):
        events["spawns"].append(
            {"path": str(path), "default": _is_default(Path(path)),
             "python": orig_python()})
        return orig_start(path)

    def ping(path=None):
        try:
            target = str(client._path(path))
        except Exception:
            target = "<unresolvable>"
        if _is_default(target):
            events["default_pings"].append(target)
        return orig_ping(path)

    def call(request, path=None, timeout=30.0):
        try:
            target = str(client._path(path))
        except Exception:
            target = "<unresolvable>"
        if _is_default(target):
            events["default_calls"].append(
                {"target": target, "op": request.get("op")})
        return orig_call(request, path, timeout)

    client.ensure = ensure
    client._start_detached = start
    client.ping = ping
    client.call = call


def pytest_sessionfinish(session, exitstatus):
    REPORT.write_text(json.dumps(events, indent=2), encoding="utf-8")
'''


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
        if ("library.tools.resolved" in command and " serve" in command
                and "test_tests_never_start_a_real_broker" not in command):
            found.add(int(pid))
    return found


def _socket_stat(path: Path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_ino)


def test_broker_exercising_suites_leave_no_broker_and_never_touch_default(
        tmp_path):
    """The reproduction, run the way a worker runs it, in a subprocess.

    Runs the suites that exercise the broker client, then proves the
    run left no broker process behind and never touched the default
    socket. Never stops the production broker: it only ever reads it.
    """
    default = _production_socket()
    before_pids = _broker_pids()
    before_serving = client.ping(default)
    before_stat = _socket_stat(default)

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "broker_watch.py").write_text(
        _BROKER_WATCH_PLUGIN, encoding="utf-8")
    report = tmp_path / "broker-events.json"

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(plugin_dir), str(REPO_ROOT), env.get("PYTHONPATH", "")])
    env["PIPELINE_BROKER_WATCH_REPORT"] = str(report)

    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/unit/resolve/test_ensure.py",
         "tests/unit/resolve/test_resolve_lock.py",
         "-q", "-p", "no:cacheprovider", "-o", "addopts=",
         "-p", "broker_watch"],
        cwd=str(REPO_ROOT), env=env, capture_output=True,
        encoding="utf-8", timeout=900, check=False)
    assert result.returncode == 0, (
        "the broker-exercising suites failed in the subprocess:\n"
        f"{result.stdout[-4000:]}\n{result.stderr[-4000:]}")
    assert report.exists(), (
        "the broker_watch plugin never ran, so this check proved "
        f"nothing:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    events = json.loads(report.read_text(encoding="utf-8"))

    default_spawns = [s for s in events["spawns"] if s["default"]]
    assert not default_spawns, (
        "a test started a broker on the production default socket:\n  "
        + "\n  ".join(json.dumps(s) for s in default_spawns))
    assert not events["default_pings"], (
        "a test pinged the production default socket "
        f"(would answer where a broker serves): {events['default_pings']}")
    assert not events["default_calls"], (
        "a test called the production default socket: "
        f"{events['default_calls']}")

    from library.tools import shared_environment
    expected, _ = shared_environment.python_interpreter(str(REPO_ROOT))
    if expected:
        for spawn in events["spawns"]:
            assert spawn["python"] == expected, (
                "a test started a broker with the caller's interpreter "
                f"{spawn['python']} instead of Ren's {expected}: {spawn}")
    assert events["spawns"], (
        "the subprocess run started no broker at all - it did not "
        "exercise the spawn path, so this check proved nothing.")

    after_pids = _broker_pids()
    assert after_pids == before_pids, (
        "the subprocess run left broker processes behind: "
        f"before={sorted(before_pids)} after={sorted(after_pids)} "
        f"(new={sorted(after_pids - before_pids)}). "
        "A detached broker outlives its test; stop what is started.")

    if before_serving is not None:
        after_serving = client.ping(default)
        assert (after_serving is not None
                and after_serving.get("pid") == before_serving.get("pid")), (
            f"the production broker changed under the run: before "
            f"{before_serving} after {after_serving} - something "
            f"re-bound the default socket.")
        assert _socket_stat(default) == before_stat, (
            "the default socket file changed under the run - something "
            "re-bound it.")
