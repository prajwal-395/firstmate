"""Progressive fidelity: one source's evidence at several resolutions.

The problem this removes
------------------------
A question about footage is answered at ONE resolution, and the
resolution is the caller's accident rather than the caller's choice.
The frame lane is the clearest case: it ranks the sampled frames of a
recording against a query (cheap, the index already exists) and then
puts the candidate shots to the local vision model to find out whether
the shot ESTABLISHES the query (expensive, one model call per
candidate).  The second half is not a refinement of the first - it is
a different kind of claim - and it was not optional: the lane ran it
before it said anything, so a caller who wanted to know where to look
paid for a verdict it never read.

Three things were missing, and this module owns them.

**The tiers are named.**  ``coarse`` is a ranking over a sampled
representation: it says WHERE TO LOOK.  ``detailed`` is a measurement
of the candidate itself: it says whether the candidate ESTABLISHES the
claim.  ``original`` is the source media at the frame or span the
finding names: it is the thing a reader can go and look at.  The tiers
are ordered, and an answer says which one it was reached at.

**Provenance travels with the answer.**  A detailed answer derived from
a coarse one says so, and carries the coarse answer's identity, so a
reader can walk back from a verdict to the ranking that proposed the
candidate and on to the index that ranked it.  An answer that does not
say what it was measured from cannot be checked.

**The escalation is part of the answer.**  An answer that stops at the
coarse tier carries the request that raises it to the next tier, what
that request would establish, and what it is measured to cost.  A
caller that does not need the higher fidelity pays nothing for it; a
caller that does need it knows what it is buying before it buys it.

One ladder, one place.  Every application that asks a question about
footage reads its tiers from here, so no application rebuilds the
vocabulary - and a second ladder would be a second set of answers to
the same question.

What this module does NOT do
----------------------------
It does not measure anything, decode anything, or hold an index.  The
lanes that measure keep their own code; this module only says which
resolution an answer was reached at, what it was measured from, and
what the next resolution would cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TIER_COARSE = "coarse"
TIER_DETAILED = "detailed"
TIER_ORIGINAL = "original"


@dataclass(frozen=True)
class Tier:
    """One resolution of the same source evidence.

    ``resolution`` says what a reader GETS at this tier, and
    ``establishes``/``does_not_establish`` say what the tier can and
    cannot claim.  The last two are not decoration: a ranking that
    cannot establish a claim must not be presented as though it had.
    """

    name: str
    order: int
    resolution: str
    establishes: str
    does_not_establish: str


TIERS: tuple[Tier, ...] = (
    Tier(
        name=TIER_COARSE,
        order=0,
        resolution=("a ranking over a sampled representation of the "
                    "source"),
        establishes="where to look",
        does_not_establish=("that the candidate establishes the claim - a "
                            "ranking says what resembles the query, not "
                            "what shows it"),
    ),
    Tier(
        name=TIER_DETAILED,
        order=1,
        resolution=("a measurement of the candidate itself, taken at the "
                    "source's own frame cadence"),
        establishes="that the candidate establishes the claim",
        does_not_establish=("anything the measurement did not inspect - "
                            "the coverage map says what was never looked "
                            "at"),
    ),
    Tier(
        name=TIER_ORIGINAL,
        order=2,
        resolution=("the source media itself, at the exact frame or span "
                    "the finding names"),
        establishes="the pixels behind the finding",
        does_not_establish="an edit decision - that is the captain's",
    ),
)

_BY_NAME = {tier.name: tier for tier in TIERS}


def _resolve(name: str) -> Tier:
    """The named tier, or a refusal.

    An unknown tier name raises rather than matching nothing: a ladder
    with a hole in it is a ladder that answers a question nobody asked.
    """
    found = _BY_NAME.get(str(name))
    if found is None:
        raise ValueError(
            f"unknown fidelity tier {name!r}; one of "
            f"{', '.join(sorted(_BY_NAME))}")
    return found


def tier(name: str) -> Tier:
    """The named tier, or a refusal."""
    return _resolve(name)


def tier_order(name: str) -> int:
    """Where the tier sits on the ladder (0 is the coarsest)."""
    return tier(name).order


def next_tier(name: str) -> Tier | None:
    """The tier above the named one, or None at the top of the ladder."""
    order = tier_order(name)
    for candidate in TIERS:
        if candidate.order == order + 1:
            return candidate
    return None


def provenance(*, tier: str,
               lane: str | None = None,
               representation: dict | None = None,
               source_digest: str | None = None,
               producer: str | None = None,
               producer_version: str | None = None,
               derived_from: dict | None = None,
               coverage: dict | None = None,
               cost: dict | None = None) -> dict[str, Any]:
    """The block an answer carries: which resolution, and from what.

    Every key is optional except the tier, because a lane that cannot
    name its own representation must still be able to say at which
    resolution it answered - and an absent key is better than a
    fabricated one.
    """
    resolved = _resolve(tier)
    block: dict[str, Any] = {
        "tier": resolved.name,
        "resolution": resolved.resolution,
        "establishes": resolved.establishes,
        "does_not_establish": resolved.does_not_establish,
    }
    if lane:
        block["lane"] = lane
    if representation:
        block["representation"] = representation
    if source_digest:
        block["source_digest"] = source_digest
    if producer:
        block["producer"] = producer
    if producer_version:
        block["producer_version"] = producer_version
    if derived_from:
        block["derived_from"] = derived_from
    if coverage:
        block["coverage"] = coverage
    if cost:
        block["cost"] = cost
    return block


def escalation(*, from_tier: str, to_tier: str | None = None,
               request: dict | None = None,
               establishes: str | None = None,
               cost_seconds: float | None = None,
               cost_basis: str | None = None) -> dict[str, Any]:
    """The request that raises an answer to the next tier.

    ``to_tier`` defaults to the tier above ``from_tier``.  The request
    is a DESCRIPTION of the work, not a command: the caller knows how
    to ask this lane for that tier, and the answer says what it wants.
    """
    source = _resolve(from_tier)
    target = _resolve(to_tier) if to_tier else next_tier(source.name)
    if target is None:
        return {}
    block: dict[str, Any] = {
        "from": source.name,
        "to": target.name,
        "establishes": establishes or target.establishes,
    }
    if request:
        block["request"] = request
    if cost_seconds is not None:
        block["cost_seconds_estimate"] = round(float(cost_seconds), 2)
    if cost_basis:
        block["cost_basis"] = cost_basis
    return block


def annotate(answer: dict[str, Any], *,
             fidelity: dict[str, Any] | None = None,
             escalation: dict[str, Any] | None = None) -> dict[str, Any]:
    """The answer, carrying its resolution and its way to the next one.

    The answer is copied, never mutated in place: the caller may hold
    the report the annotation was made from.
    """
    out = dict(answer)
    if fidelity:
        out["fidelity"] = fidelity
    if escalation:
        out["escalation"] = escalation
    return out
