"""One acceptance harness for unfamiliar editorial briefs.

The engine is measured against requests it was not tuned on: a brief is
a user's own words, the agent is handed nothing else, and the result is
judged on the delivered file rather than on the timeline it passed
through.

The pieces, each in one module:

- ``briefs``   the acceptance set, and the split between what the agent
  reads and the answer key the evaluator holds.
- ``stages``   the five stage measures - interpretation, retrieval,
  planning, execution, final quality - kept apart on purpose.
- ``failures`` the failure-cause taxonomy, so the next engineering
  change targets the bottleneck that actually broke.
- ``delivery`` the evidence taken off the EXPORTED file.
- ``judgement`` the independent editorial assessment, and what a judge is
  allowed to see.
- ``record``   the task record a run leaves behind.
- ``footage``  the substrate a brief runs against, and whether the
  brief's premise is in it.
- ``runner``   the report these make together.

Import the submodules directly; this package declares no eager imports so
that reading the brief set costs nothing on a machine with no Resolve and
no ML environment.
"""

from __future__ import annotations

__all__ = [
    "briefs",
    "delivery",
    "failures",
    "footage",
    "judgement",
    "record",
    "runner",
    "stages",
]
