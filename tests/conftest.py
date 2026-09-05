import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

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
