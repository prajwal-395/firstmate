import atexit
import os
import shutil
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
    """Give otherwise-unmarked tests their semantic default category."""
    categories = ("unit", "scenario", "resolve_live", "real_model")
    for item in items:
        if not any(item.get_closest_marker(name) for name in categories):
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
# tests/test_no_syspath_shadowing.py forbids test modules from adding
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
# tests/test_tests_never_reach_real_projects.py asserts the guarantee,
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


@pytest.fixture(scope="module")
def resolve_session():
    """The real Resolve instance, leased - or the module SKIPS.

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
    from library.tools import resolve_lock
    previous = resolve_lock._sole_writer_reason
    resolve_lock._sole_writer_reason = None
    LIVE_RESOLVE_GUARD.armed = False
    try:
        try:
            with resolve_lock.resolve_lease(
                    "pytest: a test that drives the live Resolve",
                    exclusive=True, timeout=5.0) as lease:
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
