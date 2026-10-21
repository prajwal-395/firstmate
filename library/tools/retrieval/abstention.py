"""What an EMPTY search answer means, and the three answers it is not.

`ren search` on a project with no dogs returns nothing.  So does
`ren search` on a project whose footage was never analysed for dogs,
and so does `ren search` on a project whose index was built before the
frames existed.  All three are an empty list.  Only the first is a
statement about the DOG.

**This module is the one place that decides which of them a lane's
answer is.**  It exists because the lanes used to decide it
themselves, and they decided it differently: the text lane said
`not_found`, the frame lane said `abstained`, and - the defect this
module was written for - the frame lane said
`insufficient_evidence` on a search that returned five windows, which
claims the evidence is missing when the lane had five.

**The vocabulary is the one the evidence stack already uses.**  The
five-state answer (`present`, `absent`, `not_observed`, `not_analyzed`,
`inconclusive`) is computed by `library.tools.evidence_query`, the same
table the typed Project Model query uses and the same one the plan
layer (`library.tools.retrieval.coverage`) already reads.  A lane that
answered in its own words would be a second vocabulary to keep in
step; this one asks the table.

**The three answers the punch list names, and what each one claims:**

| outcome | claim |
|---|---|
| `not_observed` | the lane searched its WHOLE measured scope and nothing cleared the floor |
| `insufficient_evidence` | the lane could NOT search its whole measured scope, so the empty answer is about the index, not about the footage |
| `confirmed_absent` | the measured scope carries an EXPLICIT negative assertion over the queried kind |

`confirmed_absent` is the strongest of the three and the hardest to
earn.  A ranking is not an assertion: CLIP ranking a studio frame for
"a plate of food" says the studio is the closest thing to a plate of
food, which is not the same claim as "there is no plate of food".  So
the ranking lanes can only ever reach `not_observed`, and a lane that
WANTS to claim a confirmed absence has to point at an explicit
negative in the measured scope - a measurement that ran and recorded
the absence of the thing, rather than a measurement that ran and
recorded something else.

**A refusal is not an observation.**  A lane that declined the
question has observed nothing at all, and saying `not_observed` for it
would claim a look that never happened.  It is `refused`, and the
answer state is `not_analyzed`.

**The scope is part of the answer.**  "Not observed" is only a claim
about the footage the lane actually measured, so the scope travels with
the outcome: how many sources, how many rows, and whether the lane
knows it missed any.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from library.tools.evidence_query import (
    CONFIDENTLY_ABSENT,
    INCONCLUSIVE,
    NOT_YET_ANALYSED,
    PRESENT,
    classify_absence,
)

OUTCOME_FOUND = "found"
"""The lane returned evidence."""

OUTCOME_NOT_OBSERVED = "not_observed"
"""The whole measured scope was searched and nothing cleared the floor."""

OUTCOME_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
"""The lane could not search its whole measured scope."""

OUTCOME_CONFIRMED_ABSENT = "confirmed_absent"
"""The measured scope carries an explicit negative over the queried kind."""

OUTCOME_REFUSED = "refused"
"""The lane declined the question and observed nothing."""

OUTCOMES = (OUTCOME_FOUND, OUTCOME_NOT_OBSERVED,
            OUTCOME_INSUFFICIENT_EVIDENCE, OUTCOME_CONFIRMED_ABSENT,
            OUTCOME_REFUSED)
"""Every outcome a search lane can report, in the order a reader meets
them: the evidence, then the three ways of having none, then the
refusal."""

NOT_OBSERVED_BASIS = ("the lane searched its whole measured scope and "
                       "nothing in it cleared the floor")
INSUFFICIENT_BASIS = ("the lane could not search its whole measured "
                      "scope, so the empty answer is about the index, "
                      "not about the footage")
CONFIRMED_ABSENT_BASIS = ("the measured scope carries an explicit "
                          "negative assertion over the queried kind")
REFUSED_BASIS = ("the lane declined the question, so it observed "
                 "nothing and says so")


def lane_outcome(*, lane: str, ran: bool, found: bool,
                 scope_complete: bool, scope_note: str = "",
                 refused: bool = False,
                 has_explicit_negative: bool = False,
                 near_miss: Mapping | None = None) -> dict:
    """What one lane's answer means, and the state it maps to.

    `ran` is whether the lane's measurement executed.  `found` is
    whether it returned evidence.  `scope_complete` is whether the
    scope it measured is the whole scope the question was about - the
    lane knows this when it skipped a source, or when the index holds
    fewer sources than the catalog names.  `has_explicit_negative` is
    whether the scope carries an explicit assertion that the thing is
    NOT there; a ranking never carries one, so the ranking lanes reach
    `not_observed` and never `confirmed_absent`.

    `near_miss` is what came closest.  It is carried beside the
    outcome and never inside the results: a near miss is context for
    an abstain, not a find.
    """
    scope = {"complete": bool(scope_complete), "note": scope_note}
    if refused:
        outcome = OUTCOME_REFUSED
        status = NOT_YET_ANALYSED
        basis = REFUSED_BASIS
    elif not ran:
        outcome = OUTCOME_INSUFFICIENT_EVIDENCE
        status = NOT_YET_ANALYSED
        basis = INSUFFICIENT_BASIS
    elif found:
        outcome = OUTCOME_FOUND
        status = PRESENT
        basis = "the lane returned evidence"
    elif not scope_complete:
        outcome = OUTCOME_INSUFFICIENT_EVIDENCE
        status = INCONCLUSIVE
        basis = INSUFFICIENT_BASIS
    elif has_explicit_negative:
        outcome = OUTCOME_CONFIRMED_ABSENT
        status = CONFIDENTLY_ABSENT
        basis = CONFIRMED_ABSENT_BASIS
    else:
        outcome = OUTCOME_NOT_OBSERVED
        status = CONFIDENTLY_ABSENT
        basis = NOT_OBSERVED_BASIS
    reading = classify_absence(status,
                                has_explicit_negative=has_explicit_negative,
                                coverage_complete=bool(scope_complete))
    return {
        "lane": lane,
        "outcome": outcome,
        "answer_state": reading["state"],
        "basis": basis,
        "scope": scope,
        "near_miss": dict(near_miss) if near_miss else None,
    }


def unverified_negatives(corpus: Mapping, spoken_words: Iterable[str]
                          ) -> list[dict]:
    """The corpus entries whose subject words occur in the measured scope.

    A negative is only a negative if the thing is really not there.  The
    corpus says it is not there, and this checks that claim against the
    words the measured scope actually holds: an entry whose subject
    word occurs is REFUSED, because a query about something that IS in
    the footage is not a false positive when the lane finds it - it is
    a mislabelled corpus entry, and scoring it as a negative would
    punish the lane for being right.

    **Only an entry whose subject is a SPOKEN event is checked this
    way.**  A picture's negative is established by looking at the
    frames, and the words the transcript happens to use are not a
    measurement of the picture: a corpus entry about a brick building
    is still a negative when the transcript says the word "building".
    The corpus says which guard each entry carries.

    The words are the SCOPE's, not the lane's: the lane is what is
    being measured, so it cannot also be what says what is not there.
    """
    known = {str(word).lower() for word in spoken_words or ()}
    refused = []
    for entry in (corpus.get("queries") if isinstance(corpus, Mapping) else None) or ():
        if entry.get("guard", "words") != "words":
            continue
        subject = [str(word).lower()
                   for word in entry.get("subject_words") or ()]
        present = sorted({word for word in subject if word in known})
        if present:
            refused.append({"id": entry.get("id"),
                            "subject_words_present": present,
                            "reason": ("the subject words occur in the "
                                       "measured scope, so this entry is not "
                                       "a negative")})
    return refused
