import sys
import os
from unittest.mock import MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# DaVinciResolveScript only exists where DaVinci Resolve is installed, and
# several modules import it at module level. Until this lived here, the
# only stub was installed by tests/test_resolve_build_timeline.py as an
# import side effect - so whether a test could import those modules
# depended on pytest's alphabetical collection order, and a new test file
# sorting before "test_resolve_..." failed on CI while passing locally.
#
# Only stubbed when the real module is absent, so a machine with Resolve
# still exercises the real bindings.
if "DaVinciResolveScript" not in sys.modules:
    try:
        import DaVinciResolveScript  # noqa: F401
    except ImportError:
        sys.modules["DaVinciResolveScript"] = MagicMock()
