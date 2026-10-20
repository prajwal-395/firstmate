"""The evaluation taxonomy, now owned by the engine.

The definition lives in `library/tools/evaluation_levels.py` because the
acceptance runner that reads it ships in the engine, and a packaged
engine carries `library/` but not `tests/`. This module keeps the old
import path working, so the live harness and its contract tests are
unaffected.
"""

from library.tools.evaluation_levels import LEVELS, catalog

__all__ = ["LEVELS", "catalog"]
