"""An empty answer is TWO answers, and only one of them is a conclusion.

`ren search` on a project with no dogs returns nothing.  So does
`ren search` on a project whose footage was never analysed for dogs, and
so does `ren search` on a project whose index was built before the
frames existed.  All three are an empty list.  Only the first is a
statement about the DOG.

The difference is COVERAGE: did the footage get measured for this kind
of thing, over the whole scope the question was about?  "I looked and
there is no dog" needs every source measured for dogs.  "I found no dog"
needs nothing more than the index.

This module decides that, from two facts the project already holds:

* the plan's requirements (`library/tools/retrieval/requirements.py`) -
  what was asked, and therefore which measurements would answer it;
* the per-clip coverage report
  (`library/tools/analysis/footage_segments.coverage_report`) - which
  measurements exist, per source.

**The answer vocabulary is not this module's to invent.**  It uses
`evidence_query.classify_absence`, the same four states and the same
DATA-036 vocabulary the typed Project Model query already uses, so a
caller that already knows how to read one knows how to read the other.

**What is NOT attestable stays unattested.**  A requirement about a
RELATION between two shots needs cross-shot identity resolution, and the
per-clip coverage report does not attest that; a requirement about an
object's STATE needs a state measurement, and nothing measures one.  Both
are marked unattested, and an unattested requirement BLOCKS a confirmed
absence.  That is the fail-closed direction: the cost of saying "not
there" when it might be there is a lost moment; the cost of saying
"could not tell" is one extra question to the editor.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from library.tools.evidence_query import (
    CONFIDENTLY_ABSENT,
    INCONCLUSIVE,
    NOT_YET_ANALYSED,
    PRESENT,
    classify_absence,
)
from library.tools.retrieval.requirements import (
    RetrievalPlan,
    RetrievalRequirement,
)

REQUIREMENT_COVERAGE_FLAGS: dict[str, tuple[str, ...] | None] = {
    "textual:speech_content": ("speech",),
    "textual:on_screen_text": ("ocr",),
    "visual:object": ("object",),
    "visual:person": ("picture", "expressions"),
    "visual:action": ("action",),
    "visual:state": None,
    "visual:scene": ("scene",),
    "visual:composition": ("picture",),
    "acoustic:sound_event": ("sound",),
    "acoustic:voice_character": ("sound", "expressions"),
    "temporal:ordering": (),
    "temporal:duration": (),
    "temporal:repetition": (),
    "temporal:interval": (),
    "relational:same_subject": None,
    "relational:shot_relation": None,
    "relational:cross_clip": None,
}
"""Which per-clip coverage flags attest that a requirement was searched.

`None` means the per-clip coverage report CANNOT attest this
requirement at all, and an unattested requirement blocks a confirmed
absence:

* `visual:state` - no measurement records an object's state.
* `relational:*` - a relation between two shots needs cross-shot
  identity resolution, which the per-clip report does not record.

An EMPTY tuple means the requirement needs no measurement of its own: the
timeline always exists, so a temporal constraint is always satisfiable.
"""

#: Why a requirement cannot be attested, in prose a caller can show.
UNATTESTED_REASON = {
    "visual:state": (
        "no measurement records an object's state; the coverage report "
        "attests that an object was seen, not what state it was in"),
    "relational:same_subject": (
        "a relation across shots needs cross-shot identity resolution, "
        "which the per-clip coverage report does not attest"),
    "relational:shot_relation": (
        "a relation across shots needs cross-shot identity resolution, "
        "which the per-clip coverage report does not attest"),
    "relational:cross_clip": (
        "a relation across clips needs cross-shot identity resolution, "
        "which the per-clip coverage report does not attest"),
}


def requirement_coverage_flags(requirement: RetrievalRequirement
                               ) -> tuple[str, ...] | None:
    """The coverage flags that attest one requirement, or None."""
    return REQUIREMENT_COVERAGE_FLAGS.get(requirement.key)


def _clip_flags(coverage: Mapping) -> Mapping[str, Mapping]:
    per_clip = coverage.get("per_clip") if isinstance(coverage, Mapping) else None
    return per_clip if isinstance(per_clip, Mapping) else {}


def requirement_sufficiency(requirement: RetrievalRequirement,
                            coverage: Mapping, *,
                            lane_ran: bool) -> dict:
    """Whether ONE requirement was searched over the whole scope.

    `lane_ran` is whether the lane that answers this requirement
    executed.  A requirement whose lane never ran is uncovered even when
    the measurements exist on disk, because nobody asked them.
    """
    flags = requirement_coverage_flags(requirement)
    if flags is None:
        return {
            "requirement": requirement.key,
            "covered": False,
            "attestable": False,
            "reason": UNATTESTED_REASON.get(
                requirement.key,
                "the coverage report does not attest this requirement"),
            "clips": [],
        }
    if not lane_ran:
        return {
            "requirement": requirement.key,
            "covered": False,
            "attestable": True,
            "reason": f"the {requirement.lane} lane did not run",
            "clips": [],
        }
    if not flags:
        return {
            "requirement": requirement.key,
            "covered": True,
            "attestable": True,
            "reason": "",
            "clips": [],
        }
    per_clip = _clip_flags(coverage)
    missing = [clip_id for clip_id in sorted(per_clip)
               if not any(per_clip[clip_id].get(flag) for flag in flags)]
    if missing:
        return {
            "requirement": requirement.key,
            "covered": False,
            "attestable": True,
            "reason": "some sources carry no measurement of this kind",
            "clips": missing,
        }
    return {
        "requirement": requirement.key,
        "covered": True,
        "attestable": True,
        "reason": "",
        "clips": [],
    }


def coverage_sufficiency(plan: RetrievalPlan, coverage: Mapping,
                         *, lanes_ran: Iterable[str] = ()) -> dict:
    """Whether the footage could have answered this plan at all.

    A requirement is COVERED when every source in the catalog carries at
    least one of its coverage flags.  A requirement is UNCOVERED when
    some source does not - the search could have found the thing in the
    sources that have it and missed it in the sources that do not, so
    the empty answer is about the index, not about the footage.
    """
    ran = set(lanes_ran or ())
    per_clip = _clip_flags(coverage)
    catalog_clips = sorted(per_clip)
    declared = coverage.get("clips_in_catalog") if isinstance(coverage, Mapping) else None
    clips_in_catalog = int(declared) if isinstance(declared, int) else len(catalog_clips)

    covered: list[str] = []
    uncovered: list[dict] = []
    unattested: list[dict] = []
    uncovered_clips: dict[str, list[str]] = {}

    for requirement in plan.requirements:
        if not requirement.mandatory:
            continue
        verdict = requirement_sufficiency(
            requirement, coverage, lane_ran=requirement.lane in ran)
        if verdict["covered"]:
            covered.append(verdict["requirement"])
            continue
        if not verdict["attestable"]:
            unattested.append({"requirement": verdict["requirement"],
                               "reason": verdict["reason"]})
            continue
        uncovered.append({"requirement": verdict["requirement"],
                          "reason": verdict["reason"],
                          "clips": verdict["clips"]})
        if verdict["clips"]:
            uncovered_clips[verdict["requirement"]] = verdict["clips"]

    reasons = [entry["reason"] for entry in uncovered]
    reasons += [entry["reason"] for entry in unattested]
    if not catalog_clips:
        reasons.append("the catalog lists no source to search")

    return {
        "complete": not uncovered and not unattested and bool(catalog_clips),
        "covered": covered,
        "uncovered": uncovered,
        "unattested": unattested,
        "uncovered_clips": uncovered_clips,
        "clips_in_catalog": clips_in_catalog,
        "clips_searched": len(catalog_clips),
        "lanes_ran": sorted(ran),
        "reasons": reasons,
    }


def classify_retrieval(plan: RetrievalPlan, *, found: bool,
                       sufficiency: Mapping,
                       has_explicit_negative: bool = False) -> dict:
    """The four-state answer, and the DATA-036 reading of it.

    `found` is whether the search returned anything.  `sufficiency` is
    `coverage_sufficiency`'s report.  `has_explicit_negative` is whether
    the corpus carries an explicit assertion that the thing is NOT there
    - an absence label, not merely a completed search.  Without it, a
    completed search is "not observed", which is the honest reading: we
    looked, we did not see it, we are not claiming it is not there.
    """
    if found:
        status = PRESENT
    elif sufficiency.get("unattested"):
        status = INCONCLUSIVE
    elif sufficiency.get("uncovered"):
        # A requirement whose measurement never ran anywhere is a lane
        # that never produced evidence; a requirement uncovered on SOME
        # sources is partial coverage.
        never_ran = [entry for entry in sufficiency["uncovered"]
                     if not entry.get("clips")]
        status = NOT_YET_ANALYSED if never_ran else INCONCLUSIVE
    elif not sufficiency.get("complete"):
        status = INCONCLUSIVE
    else:
        status = CONFIDENTLY_ABSENT

    coverage_complete = bool(sufficiency.get("complete"))
    reading = classify_absence(
        status,
        has_explicit_negative=bool(has_explicit_negative),
        coverage_complete=coverage_complete)
    return {
        "status": status,
        "state": reading["state"],
        "basis": reading["basis"],
        "coverage": sufficiency,
        "requirements": [r.to_dict() for r in plan.requirements],
    }


def absence_verdict(plan: RetrievalPlan, *, found: bool, coverage: Mapping,
                    lanes_ran: Sequence[str] = (),
                    has_explicit_negative: bool = False) -> dict:
    """`coverage_sufficiency` and `classify_retrieval`, in one call."""
    sufficiency = coverage_sufficiency(plan, coverage, lanes_ran=lanes_ran)
    return classify_retrieval(plan, found=found, sufficiency=sufficiency,
                              has_explicit_negative=has_explicit_negative)
