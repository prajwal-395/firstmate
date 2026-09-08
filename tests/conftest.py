import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

# ── ONNX Runtime telemetry: disabled to prevent SIGSEGV on teardown ─────
#
# onnxruntime ships a Microsoft Applications Events telemetry subsystem
# that spawns a native background thread (WorkerThread::threadFunc) on
# first import.  That thread accesses std::map nodes owned by
# ILogConfiguration after Python has torn them down, producing a
# KERN_INVALID_ADDRESS / SIGSEGV on thread 3.  The crash is intermittent
# because it races the interpreter's atexit / module-cleanup sequence:
# if the thread happens to be idle when the process exits, no fault.
#
# Two mechanisms, belt-and-suspenders:
#   1. The pytest_configure hook calls ort.disable_telemetry_events()
#      the moment onnxruntime becomes importable.  This is the reliable
#      path - it is the official Python API and it stops event collection,
#      which starves the background thread of work to do during teardown.
#   2. As a fallback, the hook also sets the session env so any child
#      process that imports onnxruntime inherits the setting.
#
# Evidence: ~/Library/Logs/DiagnosticReports/python3.12-2026-09-07-*.ips
# both show the identical stack:
#   onnxruntime_pybind11_state.so  WorkerThread::threadFunc
#     -> uploadAsync -> handleRetrieveEvents -> GetMaximumUploadSizeBytes
#     -> ILogConfiguration::operator[] -> __emplace_unique_key_args (SEGV)


def pytest_configure(config):
    """Disable ONNX Runtime telemetry before any test imports it."""
    try:
        import onnxruntime
        onnxruntime.disable_telemetry_events()
    except ImportError:
        pass  # onnxruntime not installed - nothing to disable

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
# directory for the whole session.  `paths._load_dotenv` does not
# override a variable that is already set, so this beats .env too.
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

# DaVinciResolveScript only exists where DaVinci Resolve is installed, and
# several modules import it at module level. Until this lived here, the
# only stub was installed by tests/test_resolve_build_timeline.py as an
# import side effect - so whether a test could import those modules
# depended on pytest's alphabetical collection order, and a new test file
# sorting before "test_resolve_..." failed on CI while passing locally.
#
# Only stubbed when the real module is absent, so a machine with Resolve
# still exercises the real bindings. That promise needs the module's own
# directory on the path first: Resolve ships DaVinciResolveScript.py under
# Application Support and nothing else puts it on sys.path, so the bare
# import failed on a machine that HAS Resolve and the stub went in anyway.
# `tests/test_marker_feedback_against_resolve.py` is the suite that needs
# the real bindings; without this it skipped everywhere.
if "DaVinciResolveScript" not in sys.modules:
    _resolve_modules = os.path.join(
        os.environ.get(
            "RESOLVE_SCRIPT_API",
            "/Library/Application Support/Blackmagic Design/DaVinci Resolve/"
            "Developer/Scripting",
        ),
        "Modules",
    )
    if os.path.isdir(_resolve_modules) and _resolve_modules not in sys.path:
        sys.path.append(_resolve_modules)
    try:
        import DaVinciResolveScript  # noqa: F401
    except ImportError:
        pass
