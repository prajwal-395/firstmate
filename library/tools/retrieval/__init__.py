"""Retrieval that says WHAT it was asked for, and WHAT it could not say.

The footage search answers one question with one fused score
(`library/tools/analysis/footage_query.py`).  That is the right shape for
a search box, and the wrong shape for an editor asking a question with
several parts.

"the moment he explains the pricing rule, while the pricing slide is up"
is not one requirement.  It is a claim about SPEECH (the words), a claim
about PICTURE (the slide), a claim about ON-SCREEN TEXT (what the slide
says), and a claim about WHEN (the pricing beat, not the small talk).
Fusing those four into one number means a hit cannot say which part it
satisfied, and an empty answer cannot say which part was never searched.

The five modalities are the ones the punch list names, and each answers
a different question, so each is searched and judged on its own:

| modality | the question it answers |
|---|---|
| `textual` | what is SAID, or written on screen |
| `visual`  | what is SEEN - objects, people, actions, scenes, composition |
| `acoustic`| what is HEARD that is not speech |
| `temporal`| WHEN - order, duration, repetition |
| `relational` | ACROSS SHOTS - the same subject, the other angle, continuity |

The modules here are one subsystem, not five:

* `requirements` - a query becomes a typed `RetrievalPlan` (VI-001).
* `coverage` - which of those requirements the footage could answer, and
  whether an empty answer is "not found" or "confirmed absent" (VI-005).
* `dedup` - overlapping and redundant candidate windows are one window
  (VI-007).
* `support` - every result carries its interval, its frames and its
  source references (VI-006).
* `search` - run the lanes the plan names, and answer the plan
  (`search_footage`).
* `calibration` - a score means a probability, or it says it does not
  (VI-004).
* `verify` - the top candidates are re-read at higher fidelity before
  anyone cuts on them (VI-008).
* `incumbent` - a change is measured against what it replaces (VI-009).
* `consequence` - a false positive is priced in the edit it would
  cause (VI-010).

Everything here is deterministic and reads only what the project already
measured.  Nothing in this package decides WHAT to keep in the cut; it
decides what the search was asked, what it could answer, and how sure
it is.
"""

from library.tools.retrieval.requirements import (
    MODALITIES,
    RetrievalPlan,
    RetrievalRequirement,
    decompose,
    requirement_kinds,
)
from library.tools.retrieval.coverage import (
    absence_verdict,
    coverage_sufficiency,
    requirement_coverage_flags,
)
from library.tools.retrieval.dedup import dedupe_windows
from library.tools.retrieval.search import search_footage
from library.tools.retrieval.support import supporting_evidence

__all__ = [
    "MODALITIES",
    "RetrievalPlan",
    "RetrievalRequirement",
    "absence_verdict",
    "coverage_sufficiency",
    "dedupe_windows",
    "decompose",
    "requirement_coverage_flags",
    "requirement_kinds",
    "search_footage",
    "supporting_evidence",
]
