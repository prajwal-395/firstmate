"""The brief scorer, now owned by the engine.

The definition lives in `library/tools/editorial_brief_scorer.py`
because the acceptance runner that uses it ships in the engine, and a
packaged engine carries `library/` but not `tests/`. This module keeps
the old import path working, so the live harness and its contract tests
are unaffected.
"""

from library.tools.editorial_brief_scorer import (
    SCHEMA,
    VALID_STATUSES,
    score_batch,
    score_brief,
)

__all__ = ["SCHEMA", "VALID_STATUSES", "score_batch", "score_brief"]
