# `library.tools.reel_phase_log` - why the instrument exists

Moved from the module docstring of `tests/test_reel_phase_log.py` (2026-10-02).

2026-09-18, batch 5: reel M05 waited 85 minutes between its plan answers
arriving (10:06:58) and its build landing (11:32:08), with a 64-minute window
with zero writes from any lane. The cause could not be recovered.
`library/tools/reel_phase_log.py` is the instrument: per reel, when the plan
was asked for, when its answers arrived, when the build started and finished,
when verification ran, when consolidation ran, plus a one-line wait reason by
the waiter - each with its OWN timestamp taken at the moment, never inferred
from file times (the `llm_requests` mtime trap).
