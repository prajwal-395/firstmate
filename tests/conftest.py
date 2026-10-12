import atexit
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── ONNX Runtime telemetry: never loaded, so its teardown never races ──
#
# onnxruntime ships a Microsoft Applications Events telemetry subsystem
# that spawns native background threads (WorkerThread::threadFunc) on
# first import.  Those threads race interpreter shutdown, intermittently
# aborting a process whose tests all passed:
#
#   2026-09-07: WorkerThread -> uploadAsync -> handleRetrieveEvents ->
#     ILogConfiguration::operator[] -> SEGV (KERN_INVALID_ADDRESS).
#     Evidence: ~/Library/Logs/DiagnosticReports/python3.12-2026-09-07-*.ips
#   2026-09-23: the same teardown, a different losing thread -
#     WorkerThread -> onHttpResponse -> handleDecode ->
#     HttpResponseDecoder::DispatchEvent -> LogManagerImpl::DispatchEvent
#     -> DebugEventSource::DispatchEvent -> recursive_mutex::lock() throws
#     system_error -> terminate -> abort (SIGABRT), while the main thread
#     sits in PosixTelemetry::Shutdown -> FlushAndTeardown ->
#     cancelAllRequests.  The tier's one test had passed.
#     Evidence: python3.12-2026-09-23-215150.ips and -221358.ips,
#     identical stacks.
#
# The 2026-09-07 fix (import onnxruntime in pytest_configure, then call
# disable_telemetry_events()) did not hold, and its shape is why:
# disable_telemetry_events() stops ORT telemetry EVENT collection, not
# the 1DS SDK's own upload/debug-event path - and the eager import was
# itself the ONLY thing loading onnxruntime_pybind11_state.so into the
# test process (no test imports it; the real_model test measures with
# parselmouth only), queueing the session-start upload whose response
# handler crashes at exit.
#
# So the fix is structural: pytest_configure never imports onnxruntime.
# It disables telemetry only when onnxruntime is ALREADY loaded (a test
# that genuinely uses it), and imports nothing itself.  A process that
# never loads onnxruntime_pybind11_state.so has no WorkerThread to race
# shutdown with.


def pytest_configure(config):
    """Disable ONNX Runtime telemetry only if it is already loaded.

    Never imports onnxruntime here: importing it to "disable" telemetry
    loads the native library into every test process and arms the
    shutdown race this block exists to prevent (2026-09-23 SIGABRT).
    """
    # Tests read refusal prose unless they opt into JSON: without this
    # a machine with a chat harness installed (which the `ren` entries
    # detect) would emit `REN_RESULT` lines inside tests that call them,
    # and one test's entry call would leak `REN_OUTPUT=json` into every
    # later test in the worker. Present-but-empty counts as an explicit
    # prose choice, so the entries leave it alone; a test that wants the
    # JSON line sets it itself (LE-03).
    os.environ.setdefault("REN_OUTPUT", "")
    marker_names = {
        marker.split(":", maxsplit=1)[0]
        for marker in config.getini("markers")
    }
    if "unit" not in marker_names:
        config.addinivalue_line(
            "markers",
            "unit: the default unit-test category for standalone test sessions",
        )

    ort = sys.modules.get("onnxruntime")
    if ort is not None:
        disable = getattr(ort, "disable_telemetry_events", None)
        if disable is not None:
            disable()


def pytest_collection_modifyitems(items):
    """Give otherwise-unmarked tests their semantic default category.

    A test under `tests/scenarios/` is a `scenario` by where it sits
    (`tests/layers.py`); every other unmarked test is `unit`.
    """
    from tests import layers

    categories = ("unit", "scenario", "resolve_live", "real_model")
    for item in items:
        if any(item.get_closest_marker(name) for name in categories):
            continue
        if layers.layer_of(item.path) == "scenarios":
            item.add_marker(pytest.mark.scenario)
        else:
            item.add_marker(pytest.mark.unit)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── Non-root directories tests may import from: owned HERE, nowhere else ─
#
# Several step bodies import a sibling by bare name, so the step's own
# directory has to be on sys.path before the step module can be
# imported - by absolute package path or otherwise. When each test
# module did that insert itself, at import time, collection order
# decided which directory a bare name bound to (PR 624: a bare
# `import step` bound step_5_04's step.py instead of step_6_01's).
# library/tools/static_check.py forbids test modules from adding
# anything but the repo root, so the entries that production code
# genuinely requires live here instead: this file is imported before
# every test module, deterministically, so what it establishes cannot
# depend on collection order. Each entry names the bare sibling import
# that requires it; remove the entry when that import goes absolute.
_NON_ROOT_ENTRIES = (
    # step_6_01_render/step.py:21 `from resolve_build_timeline import ...`
    # and resolve_build_timeline.py:51 `from build_verification import ...`.
    os.path.join(PROJECT_ROOT, "library", "steps", "step_6_01_render"),
    # step_4_05_render_subtitles/step.py:60
    # `from generate_remotion_props import ...`.
    os.path.join(PROJECT_ROOT, "library", "steps",
                 "step_4_05_render_subtitles"),
    # step_4_06_render_motion_graphics/post_bridge.py:40
    # `from generate_motion_props import ...`.
    os.path.join(PROJECT_ROOT, "library", "steps",
                 "step_4_06_render_motion_graphics"),
    # execution/apply_fusion_comps.py:60 `from transition_vocabulary
    # import ...` and :62 `from fusion.comp_builder import ...`.
    os.path.join(PROJECT_ROOT, "library", "tools", "execution"),
    # tools/video_segment_analyzer.py:8 `from vision_model import ...`
    # and :23 `import render_qa`.
    os.path.join(PROJECT_ROOT, "library", "tools"),
)
for _entry in _NON_ROOT_ENTRIES:
    if _entry not in sys.path:
        sys.path.append(_entry)

# ── The captain's projects root is not reachable from a test ────────
#
# `library.tools.paths.PROJECTS_ROOT` is the ONE constant that names
# where the captain's real projects live, and it is fixed at import.
# Any test that reads it - directly, or through a helper that walks it
# looking for a project.yaml - binds itself to real footage and real
# renders that cannot be re-shot.  tests/test_pipeline.py did exactly
# that at IMPORT time, so every pytest COLLECTION resolved a real
# project directory and the test then wrote `_v2` artifacts into it and
# shutil.move'd a backup over an original.
#
# The route is closed at its source: before anything under `library/`
# is imported, PIPELINE_PROJECTS_ROOT is pointed at an empty temporary
# directory for the whole session.  `paths.load_configuration` does not
# override a variable that is already set, so this beats the user config
# and .env too.
# Nothing else needs to opt in, and a route into the real root that
# nobody has thought of yet is closed as well.
#
# This is a sandbox, not a fixture: it stays EMPTY.  A test that needs
# a project builds one under tmp_path.
#
# tests/tooling/test_tests_never_reach_real_projects.py asserts the guarantee,
# and is the only thing that sets the escape hatch below - which it
# points at a decoy, never at the real root.
REAL_PROJECTS_ROOT_ENV = "PIPELINE_PROJECTS_ROOT"
CONFIGURED_PROJECTS_ROOT_ENV = "PIPELINE_TESTS_CONFIGURED_PROJECTS_ROOT"
SANDBOX_ESCAPE_HATCH_ENV = "PIPELINE_TESTS_SKIP_PROJECTS_ROOT_SANDBOX"

if not os.environ.get(SANDBOX_ESCAPE_HATCH_ENV):
    # Import paths FIRST, before anything else under library/ can, so
    # that: (a) its .env loader has run and PIPELINE_PROJECTS_ROOT holds
    # the value this machine is really configured with, which the
    # guarantee test asserts tests are not seeing; and (b) the constant
    # is rebound here, before project_registry.py - which does
    # `from library.tools.paths import PROJECTS_ROOT`, a VALUE import
    # that would otherwise keep the real root - is imported at all.
    import library.tools.paths as _pipeline_paths

    os.environ.setdefault(
        CONFIGURED_PROJECTS_ROOT_ENV, str(_pipeline_paths.PROJECTS_ROOT))
    _sandbox = tempfile.mkdtemp(prefix="pipeline-projects-sandbox-")
    # The env var covers subprocesses and any later fresh import; the
    # rebind covers this process, where paths.py has already run.
    os.environ[REAL_PROJECTS_ROOT_ENV] = _sandbox
    _pipeline_paths.PROJECTS_ROOT = Path(_sandbox)
    atexit.register(shutil.rmtree, _sandbox, True)

# ── The machine's heavy-work lock is not reachable from a test ──────
#
# `library/tools/heavy_work_lock.py` serialises heavy work across every
# lane on the machine through one directory under ~/.local/share/vep.
# The resolve and reel-build paths take it, so a test that drives one of
# them through mocks still queued behind whichever lane held the real
# lock: on 2026-10-01 `test_timeline_sop_conformance`, `test_proof_scope`
# and the `test_reel_build_gate*` files sat at 0% CPU, polling it, in any
# run outside `scripts/full_suite_gate.sh` (whose inherited owner token
# is the only thing that let them re-enter). A test's heavy work is
# mocked, so it contends for nothing: each test session takes a private
# lock instead. Tests OF the lock point it at their own directory.
# The env var covers the subprocesses a test spawns (the gate-shim tests
# run `scripts/full_suite_gate.sh`, which takes the lock); the rebind
# covers this process, where the module may already have been imported.
import library.tools.heavy_work_lock as _heavy_work_lock

_heavy_sandbox = tempfile.mkdtemp(prefix="pipeline-heavy-work-sandbox-")
os.environ[_heavy_work_lock.LOCK_DIR_ENV] = os.path.join(
    _heavy_sandbox, "heavy-work.lock")
_heavy_work_lock.HEAVY_LOCK_DIR = Path(
    os.environ[_heavy_work_lock.LOCK_DIR_ENV])
atexit.register(shutil.rmtree, _heavy_sandbox, True)

# ── The Resolve broker is never a test's ────────────────────────────
#
# `library.tools.resolved.client.ensure` starts a DETACHED broker on
# demand - its own session, outliving the worker that started it - so a
# broker a test starts and never stops keeps serving after the test is
# gone. Measured 2026-10-05: 49 orphaned `python -m
# library.tools.resolved serve` processes, each holding the production
# default socket (`$TMPDIR/ren-resolved.sock`), each re-binding it so
# real clients talked to whichever started last. They were started by
# test runs, not by Ren's real use.
#
# So the whole session takes a private lock directory for the broker,
# the way it already takes a private projects root and a private
# heavy-work lock above. `resolve_lock.lock_dir` (and through it the
# broker's socket, starter lock and log) reads `PIPELINE_RESOLVE_LOCK_DIR`
# on every call, so pointing the variable here isolates every test
# process - including its non-pytest subprocess children, which inherit
# the environment - without any test opting in.
#
# This is NOT done through `monkeypatch`: a per-test `monkeypatch.undo()`
# undoes the fixture that set the variable too, and the broker then
# binds the default socket (measured in `test_ensure.py`). Set with
# `os.environ` at import, re-asserted per test by the fixture below
# (also without `monkeypatch`), an undo restores the session value
# rather than removing it. Where the variable is missing anyway,
# `client.ensure` refuses the default socket outright.
#
# A test that needs its own lock dir (a short one under /tmp - a Unix
# socket path is bounded) still sets the variable itself; the fixture
# puts the session value back afterwards.
import library.tools.resolve_lock as _resolve_lock

_CONFIGURED_RESOLVE_LOCK_DIR_ENV = "PIPELINE_TESTS_CONFIGURED_RESOLVE_LOCK_DIR"
os.environ.setdefault(
    _CONFIGURED_RESOLVE_LOCK_DIR_ENV,
    os.environ.get(_resolve_lock.LOCK_DIR_ENV, ""))
_SESSION_RESOLVE_DIR = tempfile.mkdtemp(prefix="ren-test-resolve-", dir="/tmp")
os.environ[_resolve_lock.LOCK_DIR_ENV] = _SESSION_RESOLVE_DIR
# A broker a test starts is a detached process this file's guards do not
# reach; hosting the shadow watcher, it read the LIVE Resolve project
# against the machine's shadow store. No test broker hosts it; the one
# test of the hosting turns it back on in-process.
import library.tools.shadow_watcher as _shadow_watcher

os.environ[_shadow_watcher.HOSTED_ENV] = "0"
# A run that ends posts a macOS notification (`completion_notice.py`); on
# the captain's Mac every scenario that ends a run would pop one. Off for
# the session; the notice's own test stubs `osascript` and unsets this.
os.environ["REN_NOTIFY"] = "0"


def _restore_session_resolve_dir():
    os.environ[_resolve_lock.LOCK_DIR_ENV] = _SESSION_RESOLVE_DIR


def _remove_session_resolve_dir():
    original = os.environ.get(_CONFIGURED_RESOLVE_LOCK_DIR_ENV)
    if original:
        os.environ[_resolve_lock.LOCK_DIR_ENV] = original
    else:
        os.environ.pop(_resolve_lock.LOCK_DIR_ENV, None)
    shutil.rmtree(_SESSION_RESOLVE_DIR, True)


atexit.register(_remove_session_resolve_dir)


#: Every broker process this session started, in-process starts only.
#: A detached broker outlives the test that started it; the per-test
#: teardown below stops what each test started (by exact pid), and the
#: session-end guard fails the run if any tracked broker is still alive.
#: Entries are `{"pid": int, "socket": str | None, "node": str}`.
#: Only tracked pids are ever signalled - never a pattern kill, never
#: the production broker, which this session did not start.
_SESSION_BROKERS = []


def _pid_alive(pid) -> bool:
    """Whether an exact pid is still running (no signal sent).

    A zombie still answers `kill(pid, 0)` but holds nothing: it has
    already exited and awaits its parent's `waitpid` (a test that
    starts a broker by hand and never waits on the handle leaves one
    behind). A zombie is dead, so it reads as not alive - checked
    through `ps`, which the teardown's `Popen` recording never
    patches (`subprocess.run`, not `Popen`).
    """
    try:
        pid = int(pid)
    except (ValueError, TypeError, OverflowError):
        return False
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
    except (OSError, ValueError, OverflowError):
        return False
    return True


def _terminate_exact_pid(pid, timeout=5.0) -> bool:
    """Stop one exact pid: SIGTERM, then SIGKILL past the deadline.

    Returns whether the pid is gone. Only ever the pid recorded when
    this session started the process - never a pattern, never a lookup.
    """
    import time as _time

    try:
        pid = int(pid)
    except (ValueError, TypeError, OverflowError):
        return True
    if not _pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _pid_alive(pid)
    deadline = _time.time() + max(0.0, timeout)
    while _pid_alive(pid) and _time.time() < deadline:
        _time.sleep(0.05)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        deadline = _time.time() + 5.0
        while _pid_alive(pid) and _time.time() < deadline:
            _time.sleep(0.05)
    return not _pid_alive(pid)


def _popen_spawns_broker(parts) -> bool:
    """Whether an argv starts a broker process.

    Both daemons: the Rust broker (`ren-resolved ... serve`, as the
    Rust-daemon tests start by hand) and the Python broker (`python -m
    library.tools.resolved serve`, as `qualification_project._broker`
    does) - the second pattern is what the first leak's teardown
    missed, so a hand-started Python broker survived the test.
    """
    if "serve" not in parts:
        return False
    return (any("ren-resolved" in part for part in parts)
            or any("library.tools.resolved" in part for part in parts))


def _popen_broker_socket(parts, env) -> Path | None:
    """The socket a hand-started broker serves, or None where unknowable.

    From the argv's `.sock` part first; the Python broker takes no
    socket argument (it serves `PIPELINE_RESOLVE_LOCK_DIR` from its
    environment), so fall back to that directory and the session socket
    name there.
    """
    for part in parts:
        if part.endswith(".sock"):
            return Path(part)
    lock_dir = (env or {}).get(_resolve_lock.LOCK_DIR_ENV) or os.environ.get(
        _resolve_lock.LOCK_DIR_ENV)
    if lock_dir:
        from library.tools.resolved.server import SOCKET_NAME as _name
        return Path(lock_dir) / _name
    return None


def pytest_sessionfinish(session, exitstatus):
    """Fail the run where a broker this session started is still alive.

    The backstop behind the per-test teardown: a detached broker
    outlives the test that started it, and one the teardown never
    recorded (or could not stop) would otherwise hold its socket
    against the next run - measured 2026-10-07 as orphaned
    `python -m library.tools.resolved serve` processes (parent pid 1)
    after test runs. Only tracked pids and sockets are read; the
    production broker is never touched.
    """
    from library.tools.resolved import client as _client

    left = []
    for entry in _SESSION_BROKERS:
        pid, sock = entry.get("pid"), entry.get("socket")
        alive = _pid_alive(pid) if pid is not None else False
        serving = False
        if sock:
            try:
                serving = (not _client._is_default_socket(Path(sock))
                           and _client.ping(Path(sock)) is not None)
            except (OSError, ValueError):
                serving = False
        if alive or serving:
            left.append(f"pid {pid} on {sock} (from {entry.get('node')})")
    if left:
        session.exitstatus = 1
        print("\nFAILED: brokers this session started are still alive:\n  "
              + "\n  ".join(sorted(set(left)))
              + "\nStop what is started: a detached broker outlives its "
              "test (tests/conftest.py).")


@pytest.fixture(autouse=True)
def _resolve_broker_is_the_tests_own(request):
    """The broker a test starts is stopped when the test ends.

    Re-asserts the session lock dir without `monkeypatch` (an undo
    cannot remove what `monkeypatch` never set), records every broker
    this test spawns - by exact pid - and stops each one at teardown:
    graceful shutdown first, then the exact pid where the process
    lingers. A detached broker outlives the test that started it, and
    a leaked one holds its socket against the next run. The production
    socket is never sent anything - a recorded path resolving to it is
    skipped for shutdown, though an exact tracked pid there is still
    terminated - and `client.ensure` refuses to start one there in the
    first place.
    """
    import time as _time

    from library.tools.resolved import client as _client
    from library.tools.resolved.server import SOCKET_NAME as _SOCKET_NAME

    _restore_session_resolve_dir()
    setup_dir = _SESSION_RESOLVE_DIR
    node = getattr(request.node, "nodeid", "<unknown>")
    real_start = _client._start_detached
    started = []
    test_brokers = []

    def _recording_start(path):
        process = real_start(path)
        try:
            pid = getattr(process, "pid", None)
        except Exception:  # noqa: BLE001 - recording never breaks startup
            pid = None
        entry = {"pid": pid, "socket": str(Path(path)), "node": node}
        started.append(Path(path))
        if pid is not None:
            test_brokers.append(entry)
            _SESSION_BROKERS.append(entry)
        return process

    _client._start_detached = _recording_start
    # A broker started by hand bypasses `_start_detached` above, so the
    # teardown below would never stop it. Both daemons are recorded
    # here, from the argv: the Rust broker (`ren-resolved ... serve
    # <sock>`, as the Rust-daemon tests do) and the Python broker
    # (`python -m library.tools.resolved serve`, as
    # `qualification_project._broker` does, with the socket only in
    # its environment) - the second pattern is the one the teardown
    # missed, measured 2026-10-07 as an orphaned serve process after
    # the test run.
    real_popen = subprocess.Popen
    popen_started = []

    def _recording_popen(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        try:
            parts = [str(a) for a in (argv or [])]
        except TypeError:
            parts = []
        watched = _popen_spawns_broker(parts)
        sock = None
        if watched:
            try:
                sock = _popen_broker_socket(
                    parts, kwargs.get("env", None))
            except Exception:  # noqa: BLE001 - recording never breaks spawn
                sock = None
        process = real_popen(*args, **kwargs)
        if watched:
            try:
                pid = getattr(process, "pid", None)
            except Exception:  # noqa: BLE001 - recording never breaks spawn
                pid = None
            if sock is not None:
                popen_started.append(sock)
            if pid is not None:
                entry = {"pid": pid,
                         "socket": str(sock) if sock is not None else None,
                         "node": node}
                test_brokers.append(entry)
                _SESSION_BROKERS.append(entry)
        return process

    subprocess.Popen = _recording_popen
    try:
        yield
    finally:
        _client._start_detached = real_start
        subprocess.Popen = real_popen
        _restore_session_resolve_dir()
        seen = []
        for sock in started + popen_started + [Path(setup_dir) / _SOCKET_NAME]:
            if sock not in seen:
                seen.append(sock)
        for sock in seen:
            if _client._is_default_socket(sock):
                continue
            try:
                if _client.ping(sock) is None:
                    continue
            except (OSError, ValueError):
                continue
            try:
                _client.call({"op": "shutdown"}, sock)
            except (ConnectionError, _client.BrokerError, OSError):
                pass
            deadline = _time.time() + 10.0
            while (_client.ping(sock) is not None
                    and _time.time() < deadline):
                _time.sleep(0.05)
            if _client.ping(sock) is not None:
                pytest.fail(
                    f"a broker this test started is still serving on "
                    f"{sock}: a detached broker outlives its test, so "
                    f"stop what is started (tests/conftest.py)",
                    pytrace=False)
        for entry in test_brokers:
            if _pid_alive(entry["pid"]):
                _terminate_exact_pid(entry["pid"])
            if _pid_alive(entry["pid"]):
                pytest.fail(
                    f"a broker this test started is still alive as pid "
                    f"{entry['pid']} (socket {entry['socket']}): a "
                    f"detached broker outlives its test, so stop what "
                    f"is started (tests/conftest.py)",
                    pytrace=False)

# ── No default test reaches the live Resolve ────────────────────────
#
# Importing DaVinciResolveScript loads Blackmagic's fusionscript.so, and
# `scriptapp("Resolve")` then connects to whatever Resolve is running.
# On 2026-10-01 a full-suite gate hung 17 minutes: Resolve's scripting
# connection was wedged, and an ordinary test worker blocked inside it.
# Two routes led there. This file imported the real module into EVERY
# worker at collection, and eight reel-build tests patched
# `resolve_project_exactly` but not `reel_build._connect_resolve_project`,
# so each opened a real `scriptapp("Resolve")` on a machine with Resolve
# and passed only because CI has none.
#
# So the module is UNIMPORTABLE in a test process, exactly as on CI, and
# the attempt is the defect: `_no_live_resolve` fails any test that
# tries, naming the test. The one way in is the `resolve_session`
# fixture below, which leases the instance and puts Resolve's Modules
# directory on the path for the tests that take it - the same tests
# `library/tools/lane_routing.py` routes to the serial lane.
# A test that wants the bindings without Resolve installs its own
# stand-in with `patch.dict("sys.modules", ...)`; a module already in
# `sys.modules` never reaches this finder.
RESOLVE_MODULE = "DaVinciResolveScript"
_RESOLVE_MODULES_DIR = os.path.join(
    os.environ.get(
        "RESOLVE_SCRIPT_API",
        "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
        "Developer/Scripting",
    ),
    "Modules",
)


class LiveResolveRefused(ImportError):
    """A test outside `resolve_session` tried to load the Resolve bindings."""


def _is_real_resolve_origin(origin) -> bool:
    """Blackmagic's own bindings, wherever the caller looked for them."""
    origin = str(origin or "")
    return (origin.startswith(_RESOLVE_MODULES_DIR)
            or "Blackmagic Design" in origin)


def _is_presence_probe() -> bool:
    """Called by `importlib.util.find_spec`: asking, not loading."""
    import importlib.util
    frame = sys._getframe(2)
    for _ in range(4):
        if frame is None:
            return False
        if frame.f_code is importlib.util.find_spec.__code__:
            return True
        frame = frame.f_back
    return False


class _LiveResolveGuard:
    """A meta-path finder that refuses DaVinciResolveScript while armed.

    Refused: the real bindings, and a bare import that finds none - on
    a machine with Resolve the caller's own fallback would go on to find
    them, so the attempt is the defect whatever this machine has. A
    stand-in a test wrote to its own directory imports as usual, and a
    presence probe (`importlib.util.find_spec`, which loads nothing) is
    told the module is absent, exactly as on CI, without counting.
    """

    def __init__(self):
        self.armed = True
        self.attempts = []

    def find_spec(self, name, path=None, target=None):
        if name != RESOLVE_MODULE:
            return None
        if not self.armed:
            if (os.path.isdir(_RESOLVE_MODULES_DIR)
                    and _RESOLVE_MODULES_DIR not in sys.path):
                sys.path.append(_RESOLVE_MODULES_DIR)
            return None
        import importlib.machinery
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and not _is_real_resolve_origin(spec.origin):
            return spec
        if _is_presence_probe():
            if spec is None:
                return None
            raise ModuleNotFoundError(name=name)
        import traceback
        self.attempts.append("".join(traceback.format_stack(limit=12)[:-1]))
        raise LiveResolveRefused(
            f"{RESOLVE_MODULE} is unimportable in the test suite: only a "
            "test taking the `resolve_session` fixture may reach the live "
            "Resolve (tests/conftest.py)")


LIVE_RESOLVE_GUARD = _LiveResolveGuard()
sys.meta_path.insert(0, LIVE_RESOLVE_GUARD)


def _is_real_resolve_module(module) -> bool:
    return _is_real_resolve_origin(getattr(module, "__file__", None))


@pytest.fixture
def resolve_bindings_stand_in(monkeypatch):
    """A DaVinciResolveScript that imports cleanly and connects to nothing.

    For a test that imports or reloads a module binding the Resolve
    module at import time and then replaces what it bound: the import
    finds this, never the real bindings. Its `scriptapp` raises, as the
    missing-bindings sentinel in `apply_fusion_comps` does.
    """
    import types

    def scriptapp(name):
        raise RuntimeError(
            f"resolve_bindings_stand_in: scriptapp({name!r}) is not "
            "connected; patch what the test needs")

    module = types.ModuleType(RESOLVE_MODULE)
    module.scriptapp = scriptapp
    monkeypatch.setitem(sys.modules, RESOLVE_MODULE, module)
    return module


@pytest.fixture(autouse=True)
def _no_live_resolve(request, monkeypatch):
    """Fail a test that tries to load the Resolve bindings without leasing."""
    if "resolve_session" in request.fixturenames:
        yield
        return
    # A live-Resolve module earlier in this worker may have left the
    # real bindings cached; a cached module never asks the finder.
    cached = sys.modules.get(RESOLVE_MODULE)
    if cached is not None and _is_real_resolve_module(cached):
        monkeypatch.delitem(sys.modules, RESOLVE_MODULE)
    previous = LIVE_RESOLVE_GUARD.armed
    LIVE_RESOLVE_GUARD.armed = True
    LIVE_RESOLVE_GUARD.attempts = []
    try:
        yield
    finally:
        LIVE_RESOLVE_GUARD.armed = previous
    attempts, LIVE_RESOLVE_GUARD.attempts = LIVE_RESOLVE_GUARD.attempts, []
    if attempts:
        pytest.fail(
            f"{request.node.nodeid} tried to import {RESOLVE_MODULE}, which "
            "on a machine with Resolve opens the live instance. Patch the "
            "connection point (e.g. `reel_build._connect_resolve_project`) "
            "or take the `resolve_session` fixture. First attempt:\n"
            + attempts[0], pytrace=False)


# ── One Resolve, many writers: what a test is, in that scheme ───────
#
# `library/tools/resolve_lock.py` refuses a write into Resolve outside
# an instance lease.  A test whose Resolve is a MagicMock has no
# instance to contend for, and taking a real machine-wide lease there
# would make the suite queue behind - or ahead of - the captain's live
# session for nothing.  So the whole session declares itself the sole
# writer, which is TRUE for a mocked Resolve and is recorded as a
# reason rather than assumed.  `resolve_lease` honours the declaration
# by yielding without touching the real instance (no captain wait, no
# flock, no lease file); `assert_current_timeline` honours it through
# `held()`.
#
# The tests that reach a REAL Resolve are the other case, and they are
# a writer nobody counted: four full-suite runs at once wedged at 0%
# CPU holding a Resolve handle on 2026-09-11, with no timeout and no
# diagnostic.  `resolve_session` below is the fixture those tests take:
# it asks for the real lease with a short timeout and SKIPS naming the
# holder rather than waiting.  A suite that cannot hang is worth more
# than a suite that eventually wins the race.


@pytest.fixture(scope="session", autouse=True)
def _resolve_guard_sole_writer():
    from library.tools import resolve_lock
    with resolve_lock.assume_sole_writer(
            "pytest: this suite's Resolve is a mock, so there is no "
            "instance to contend for"):
        yield


#: The ONE opt-in for driving the live Resolve. Without it every test
#: taking `resolve_session` skips before any Resolve call, so no default
#: pytest run, dependent selection or gate can reach the captain's
#: instance (2026-10-02: a dependent-selection run saved and switched the
#: open project because Resolve merely happened to be running).
LIVE_RESOLVE_ENV = "REN_LIVE_RESOLVE"


@pytest.fixture(scope="module")
def resolve_session():
    """The real Resolve instance, leased - or the module SKIPS.

    Skips unless `REN_LIVE_RESOLVE=1`: set it only inside a granted
    borrow-resolve window.

    Module scope, so one live-Resolve test file holds the instance for
    its own duration rather than dropping it between tests and letting
    a build in halfway through a fixture's teardown.  Any test that
    connects to a live Resolve takes this.

    A test ABOUT the guard has to stand outside the session's
    sole-writer declaration (`unguarded` in test_resolve_lock.py does
    the same): this fixture drives the LIVE instance, so the lease it
    takes must be real - contention, captain wait and all - and it
    SKIPS naming the holder rather than waiting.
    """
    if os.environ.get(LIVE_RESOLVE_ENV) != "1":
        pytest.skip(f"drives the live Resolve; runs only with "
                    f"{LIVE_RESOLVE_ENV}=1 inside a granted borrow-resolve "
                    f"window")
    from library.tools import resolve_lock
    previous = resolve_lock._sole_writer_reason
    resolve_lock._sole_writer_reason = None
    LIVE_RESOLVE_GUARD.armed = False
    try:
        try:
            from library.tools.resolved.jobs import QUALIFICATION_PROJECT
            with resolve_lock.resolve_lease(
                    "pytest: a test that drives the live Resolve",
                    exclusive=True, timeout=5.0,
                    qualification_project=QUALIFICATION_PROJECT) as lease:
                # A live test touches the qualification project and
                # nothing else, broker or no broker
                # (`ren qualification reset` builds it).
                from library.tools.marker_feedback import connect_resolve
                from library.tools.resolved.jobs import open_project_name
                try:
                    found = open_project_name(connect_resolve())
                except Exception as exc:                  # noqa: BLE001
                    pytest.skip(f"no live Resolve: {exc}")
                if found != QUALIFICATION_PROJECT:
                    pytest.skip(
                        f"Resolve has {found or 'no project'!r} open, not "
                        f"{QUALIFICATION_PROJECT!r}: a live test never runs "
                        f"in a user project")
                yield lease
        except resolve_lock.ResolveBusy as busy:
            pytest.skip(str(busy))
    finally:
        resolve_lock._sole_writer_reason = previous
        LIVE_RESOLVE_GUARD.armed = True


# ── Stubbing DaVinciResolveScript without evicting the import graph ──
#
# Nine test files stub the Resolve bindings so a module that imports
# them at module level can be imported at all.  Every one of them did
# it with `patch.dict("sys.modules", {...})`, and `patch.dict` RESTORES
# THE WHOLE DICT on exit - which DELETES every module first imported
# inside the patch.  So a reel-build test that imports
# `library.tools.reel_proposal` on its way through leaves that module
# evicted; a later test monkeypatching `library.tools.reel_proposal.
# read_proposal` then patches a FRESH module object while a module
# imported earlier still holds the old function, and the patch silently
# misses.
#
# Measured 2026-09-12: `tests/test_variants_are_routine.py::
# test_declared_variants_resolve_through_the_plans_own_reel_name`
# passes alone and fails after any of `test_reel_build_gate.py`,
# `test_reel_build_gate_keeps_good_reel.py` or
# `test_reel_build_touches_only_its_own_timelines.py` - order-dependent,
# and therefore invisible until a selection happens to order it that
# way.  Nothing about the test under it is wrong.
#
# This fixture swaps ONE key and restores ONE key, so nothing else in
# `sys.modules` is disturbed.  A file that needs the stub asks for it
# by name (`def mock_dvr(stub_resolve_script)`), which is also how the
# next such file gets it right by default.
@pytest.fixture
def stub_resolve_script():
    """Replace only `sys.modules["DaVinciResolveScript"]`, and put back
    only that."""
    absent = object()
    previous = sys.modules.get("DaVinciResolveScript", absent)
    sys.modules["DaVinciResolveScript"] = MagicMock()
    try:
        yield sys.modules["DaVinciResolveScript"]
    finally:
        if previous is absent:
            sys.modules.pop("DaVinciResolveScript", None)
        else:
            sys.modules["DaVinciResolveScript"] = previous


# ── The per-machine source memory is never a test's ────────────────
#
# `library/tools/transcript_measurement.py` stores every hearing under
# the source-memory root, and step 1.04's transcription path reaches it
# with no root argument - so a test that stubs the seam would otherwise
# write its stub's words into the real `~/.local/share/vep/source_memory`
# and serve them to the next real run of byte-identical audio. Per test,
# not per session: two tests that hear the same synthetic silence must
# not answer each other. The directory is never created here; only a
# store makes it.


@pytest.fixture(autouse=True)
def _source_memory_is_the_tests_own(request, monkeypatch, tmp_path_factory):
    import hashlib

    from library.tools import source_memory
    name = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()
    monkeypatch.setenv(
        source_memory.MEMORY_ROOT_ENV,
        str(tmp_path_factory.getbasetemp() / "source_memory" / name[:16]))
    yield
