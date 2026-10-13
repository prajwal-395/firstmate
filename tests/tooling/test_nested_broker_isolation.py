"""Nested subprocess proof for the broker isolation contract.

Kept separate from the routine broker guard assertions so the expensive
replay is selected on isolation changes and during the full suite.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = TESTS_DIR.parent

_DIRECT_POPEN_REPRO = '''
"""A hand-started Python broker the teardown used to miss, then stops it."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from library.tools.resolved import client
from library.tools.resolved.server import SOCKET_NAME


def test_hand_started_python_broker_is_stopped_at_teardown():
    lock_dir = Path(tempfile.mkdtemp(prefix="ren-leak-repro-", dir="/tmp"))
    env = dict(os.environ, PIPELINE_RESOLVE_LOCK_DIR=str(lock_dir),
               REN_SHADOW_WATCHER="0")
    process = subprocess.Popen(
        [sys.executable, "-m", "library.tools.resolved", "serve"],
        env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        start_new_session=True)
    sock = lock_dir / SOCKET_NAME
    deadline = time.time() + 15
    while client.ping(sock) is None and time.time() < deadline:
        time.sleep(0.1)
    assert client.ping(sock) is not None, "the repro broker never started"
    print(f"REPRO_BROKER_PID={process.pid} SOCK={sock}")
    # Deliberately no shutdown: the per-test teardown must stop this pid.
'''

_BROKER_WATCH_PLUGIN = '''
"""Fails the report (not the run) where a test touches the default socket."""
import json
import os
import subprocess
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
    orig_popen = subprocess.Popen

    def ensure(path=None, timeout=15.0):
        try:
            target = str(client._path(path))
        except Exception:
            target = "<unresolvable>"
        if _is_default(target):
            events["default_ensures"].append(target)
        return orig_ensure(path=path, timeout=timeout)

    def start(path):
        # The Rust daemon is a binary, not a Python interpreter: record it
        # as a rust spawn so the interpreter assertion below skips it.
        is_rust = (os.environ.get("REN_RESOLVED_DAEMON", "").strip().lower()
                   == "rust")
        spawn = {"path": str(path), "default": _is_default(Path(path)),
                 "python": None if is_rust else orig_python(),
                 "rust": is_rust, "pid": None, "via": "_start_detached"}
        events["spawns"].append(spawn)
        process = orig_start(path)
        spawn["pid"] = getattr(process, "pid", None)
        return process

    def popen(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        try:
            parts = [str(a) for a in (argv or [])]
        except TypeError:
            parts = []
        watched = (any("ren-resolved" in part for part in parts)
                   and "serve" in parts)
        spawn = None
        if watched:
            spawn = {"path": next((p for p in parts if p.endswith(".sock")),
                                   "<no-sock-arg>"),
                     "default": False, "python": None, "rust": True,
                     "pid": None, "via": "Popen"}
            events["spawns"].append(spawn)
        process = orig_popen(*args, **kwargs)
        if spawn is not None:
            try:
                spawn["path"] = str(spawn["path"])
                spawn["default"] = _is_default(Path(spawn["path"]))
            except (OSError, TypeError):
                pass
            spawn["pid"] = getattr(process, "pid", None)
        return process

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
    subprocess.Popen = popen
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
        if (("library.tools.resolved" in command or "ren-resolved" in command)
                and " serve" in command
                and "test_tests_never_start_a_real_broker" not in command):
            found.add(int(pid))
    return found


@pytest.mark.nested
@pytest.mark.serial(
    reason="Runs nested broker and Resolve-lock suites; keep child startup "
           "out of xdist saturation.")
def test_broker_exercising_suites_leave_no_broker_and_never_touch_default(
        tmp_path):
    """The reproduction, run the way a worker runs it, in a subprocess.

    Runs the suites that exercise the broker client - the spawn path
    itself (`test_ensure`), the in-process broker suites that must
    never spawn (`test_ui_transaction`, `test_shadow_watcher`,
    `test_resolve_lock`) - then proves the run left no broker process
    behind and never touched the default socket. The same child run also
    replays the direct-Popen teardown case. It never connects to or stops
    the production broker.

    A stray `ensure` without the spawn opt-in raises inside the inner
    run (failing it, which fails this test with the inner output), so
    a suite that spawns where its fixture did not opt in is caught
    here rather than leaking.
    """
    # The child plugin watches every broker-client ensure, ping and call,
    # plus broker subprocess starts. Do not ping the shared default before
    # and after this long run: Firstmate owns its lifecycle, and a normal
    # supervisor restart must not fail an otherwise isolated test.
    before_pids = _broker_pids()

    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    (plugin_dir / "broker_watch.py").write_text(
        _BROKER_WATCH_PLUGIN, encoding="utf-8")
    report = tmp_path / "broker-events.json"
    repro = TESTS_DIR / "test_direct_popen_repro_tmp.py"
    repro.write_text(_DIRECT_POPEN_REPRO, encoding="utf-8")

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(plugin_dir), str(REPO_ROOT), env.get("PYTHONPATH", "")])
    env["PIPELINE_BROKER_WATCH_REPORT"] = str(report)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/unit/resolve/test_ensure.py",
             "tests/unit/resolve/test_ui_transaction.py",
             "tests/unit/resolve/test_shadow_watcher.py",
             "tests/unit/resolve/test_resolve_lock.py", str(repro),
             "-q", "-s", "-p", "no:cacheprovider", "-o", "addopts=",
             "-p", "broker_watch"],
            cwd=str(REPO_ROOT), env=env, capture_output=True,
            encoding="utf-8", timeout=1200, check=False)
    finally:
        repro.unlink(missing_ok=True)
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
            if spawn.get("rust"):
                continue
            assert spawn["python"] == expected, (
                "a test started a broker with the caller's interpreter "
                f"{spawn['python']} instead of Ren's {expected}: {spawn}")
    assert events["spawns"], (
        "the subprocess run started no broker at all - it did not "
        "exercise the spawn path, so this check proved nothing.")

    marker = next((line for line in result.stdout.splitlines()
                   if "REPRO_BROKER_PID=" in line), None)
    assert marker, (
        "the combined child run did not replay the direct-Popen teardown:\n"
        f"{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
    repro_pid = int(marker.split("REPRO_BROKER_PID=")[1].split()[0])
    assert repro_pid not in before_pids, (
        f"the repro broker pid {repro_pid} predates the run: {sorted(before_pids)}")
    try:
        os.kill(repro_pid, 0)
    except OSError:
        pass
    else:
        pytest.fail(
            f"the hand-started broker survived its test as pid {repro_pid}; "
            "the teardown missed the Python Popen shape.")

    # Only the brokers this run spawned: a machine-wide before/after diff
    # also counts one a sibling xdist worker or another checkout starts
    # meanwhile, and failed for a broker this run never touched.
    spawned = {spawn["pid"] for spawn in events["spawns"] if spawn["pid"]}
    left = sorted(spawned & _broker_pids())
    assert not left, (
        f"the subprocess run left broker processes behind: {left} "
        f"(spawned {sorted(spawned)}, running before {sorted(before_pids)}). "
        "A detached broker outlives its test; stop what is started.")
