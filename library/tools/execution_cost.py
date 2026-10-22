"""Cost-aware execution selection: which way an edit runs, and what it costs.

AXI-016.  Three things reach a timeline and they do not cost the same:

* a **direct patch** writes one property on one item and reads it back;
* a **staged incremental** duplicates the reel, conforms it, composes it,
  verifies it and promotes it over the approved one;
* a **full rebuild** constructs the reel again from the plan.

The engine already knows how to run all three.  What it did NOT know was
what each one COSTS, so the choice between them was made on what was
provable and never on what was cheap - and the record of the choice
carried a cost slot that said ``unmeasured`` and nothing else.

This module is the ONE place that answers the cost question, and it
answers it three ways:

1. :func:`record_request` writes ONE row per execution request - the
   strategy that ran, the wall clock it took and the verification
   outcome it produced - so the three strategies are measured in the
   same unit, on the same project's own ledger.  A request whose cost
   was never measured is absent from the measurement, never a zero.
2. :func:`measured_costs` reads those rows back and says what each
   strategy has MEASURED to cost here, with the denominator (how many
   requests) beside the number.  Unmeasured is reported as unmeasured.
3. :func:`select` chooses.  It never chooses a strategy that is not
   proven safe, and it never chooses one that cannot verify what the
   request needs; among the ones that can, it takes the cheapest MEASURED
   one, and when the cost has not been measured it says so and falls back
   to the strategy that disturbs the least.

The rules, stated so a reader can obey them without this file
--------------------------------------------------------------------

**A smaller mutation is preferred only where it is verified and safe.**
The candidates handed to :func:`select` are strategies some other piece
of evidence has already proven safe (the router's proof for a direct
write, the staging copy's protection for a staged one).  A candidate
that is not safe is excluded, with its reason, and the exclusion is
recorded rather than dropped.

**The cost never buys a verification the request needs.**  A candidate
whose verification does not cover the request's needs is excluded, and
so is one whose cost has not been measured when a measured candidate
exists - an unmeasured strategy is not a cheap strategy.

**A rebuild is not an escape from a qualification refusal.**  A refusal
carries a category, and only ``unsupported`` may be followed by a
rebuild, and only when the caller authorized it.  :func:`assert_documented_escalation`
refuses a decision that took the rebuild without that authorization and
without the request being a construction of the whole reel.  The bulk
build stays: a request that CONSTRUCTS a reel is not an escape from
anything, and it is the right route for one.

**Nothing here decides whether an edit is good.**  This module chooses
how an edit is carried out, from cost and preservation, and records the
reason.  What the edit should BE is somebody else's decision, made
elsewhere.

Measured: `docs/EXECUTION_COST_SELECTION.md`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from library.tools.ren_refusal import RenRefusal

#: The ways an edit reaches a timeline.  Closed: a strategy nobody
#: declared is a defect, not a fourth option.
STRATEGIES = ("direct_patch", "staged_incremental", "full_rebuild")

#: The ledger layer ONE execution request's row is recorded under.  It
#: is a SUMMARY row (like `reel_edit`): it accounts for a request whose
#: seconds are already carried by the phase rows, so counting it as a
#: stage would charge the same seconds twice.
STRATEGY_LAYER = "execution_strategy"

#: The outcomes a request's verification may have.  Closed, because an
#: outcome nobody recognises reads as a success nobody checked.
VERIFICATION_OUTCOMES = ("verified", "refused", "failed")

#: The three phases of the STAGED route, and the phase the DIRECT route
#: opens instead.  A touch that ran before this module existed wrote
#: these rows and no request row, so the measurement reads them too -
#: a project's recorded touches are its measured costs, whether they
#: were written by the old code or the new one.
STAGED_PHASE_LAYERS = ("touchup_duplicate", "touchup_conform_compose_verify",
                        "touchup_promote")
DIRECT_PHASE_LAYER = "touchup_direct"

#: The preservation order, least disturbance first.  It is the order the
#: selection falls back to when the cost has NOT been measured: the
#: smaller mutation is preferred, and the record says the cost was
#: unmeasured rather than pretending it was compared.
PRESERVATION_ORDER = STRATEGIES


@dataclass(frozen=True)
class StrategyProfile:
    """What one strategy is, what it preserves and what it verifies."""

    name: str
    #: What the strategy does to the timeline: `targeted` writes only
    #: what changed, `full_rebuild` reconstructs it.
    mutation_scope: str
    #: What survives the strategy untouched.
    preserves: tuple[str, ...]
    #: What the strategy disturbs.
    disturbs: tuple[str, ...]
    #: The read-back the strategy performs.  A request's needs are
    #: stated in these words.
    verification: tuple[str, ...]
    #: The way back, if the strategy lands wrong.
    recovery: str
    #: Whether this strategy is the one that CONSTRUCTS a whole reel.
    constructs_whole_reel: bool


PROFILES: Mapping[str, StrategyProfile] = {
    "direct_patch": StrategyProfile(
        name="direct_patch",
        mutation_scope="targeted",
        preserves=("every item outside the declared targets",
                   "the approved timeline's own name",
                   "the rows the change did not name"),
        disturbs=("the declared targets only",),
        verification=("read back each written property",
                     "compare timeline state outside the declared targets"),
        recovery=("the EditPatch's recorded inverse restores the accepted "
                  "generation"),
        constructs_whole_reel=False),
    "staged_incremental": StrategyProfile(
        name="staged_incremental",
        mutation_scope="targeted",
        preserves=("the approved timeline until the promotion verifies",
                   ("the editor's unattributed hand edits, which ride the "
                    "staging copy"),
                   "every item the plan did not change"),
        disturbs=("a duplicate of the reel, while the work runs",),
        verification=(("re-read the whole staging and compare it with the "
                       "plan"),
                     ("verify the promotion before the approved timeline is "
                      "replaced")),
        recovery=("the undo journal's inverse, and the replaced "
                  "generation's export"),
        constructs_whole_reel=False),
    "full_rebuild": StrategyProfile(
        name="full_rebuild",
        mutation_scope="full_rebuild",
        preserves=("only what the plan carries over",),
        disturbs=("every timeline the build reconstructs",
                  "everything it does not carry over"),
        verification=("verify complete compiled timeline inventory",
                      "verify no collateral changes"),
        recovery=("the build's own placed generation, which the plan "
                  "reconstructs"),
        constructs_whole_reel=True),
}


class NoStrategyAvailable(RenRefusal):
    """No strategy is both safe and able to verify what the request needs.

    The refusal names every candidate and why each was excluded, so the
    next step is a decision somebody can make rather than a guess.
    """

    CODE = "REN_NO_STRATEGY_AVAILABLE"
    BLOCKER = "unsupported_operation"
    #: The person is asked because the RECORD does not settle the route:
    #: every candidate is named with the reason it was excluded, and
    #: which route to take is a judgement the engine does not make.
    ASK_REASON = "insufficient_evidence"
    USER_MESSAGE = ("I couldn't find an execution strategy that is both "
                    "safe and able to verify this request, so nothing on "
                    "the timeline changed.")

    def __init__(self, why: str, *, comparison: Sequence[Mapping] = ()):
        self.comparison = tuple(dict(entry) for entry in comparison)
        super().__init__(
            "no safe strategy meets the request's verification needs",
            why or "no candidate was offered for this request",
            ("Report the comparison: every candidate is named with the "
             "reason it was excluded."),
            recovery_class="needs_question")


def profile(strategy: str) -> StrategyProfile:
    """The profile of one strategy, by name."""
    name = str(strategy or "")
    found = PROFILES.get(name)
    if found is None:
        raise ValueError(
            f"unknown execution strategy: {name!r}; one of "
            f"{list(STRATEGIES)}")
    return found


@dataclass(frozen=True)
class Candidate:
    """One strategy the caller has already proven safe for this request.

    `safe` is the CALLER's evidence, not this module's: the router's
    proof for a direct write, the staging copy's protection for a
    staged one.  `verifies` is what the strategy reads back, in the
    words `PROFILES` uses.
    """

    strategy: str
    safe: bool
    reason: str
    verifies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    """The chosen strategy, and everything the choice was made from."""

    strategy: str
    reason: str
    #: One entry per candidate, in the order they were offered.
    comparison: tuple[dict[str, Any], ...] = ()
    #: `measured` when the choice was made on a measured cost, and
    #: `unmeasured` when it fell back to the preservation order.
    cost_basis: str = "unmeasured"

    def as_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy,
                "reason": self.reason,
                "cost_basis": self.cost_basis,
                "comparison": [dict(entry) for entry in self.comparison]}


def _cost_entry(costs: Mapping[str, Any] | None, strategy: str) -> dict:
    """One strategy's measured cost, or the fact that it has none."""
    measured = (costs or {}).get(strategy)
    if not isinstance(measured, Mapping):
        return {"measured": False, "requests": 0, "wall_s_p50": None,
                "wall_s_p95": None}
    return {"measured": True,
            "requests": int(measured.get("requests") or 0),
            "wall_s_p50": measured.get("wall_s_p50"),
            "wall_s_p95": measured.get("wall_s_p95"),
            "verified": int(measured.get("verified") or 0),
            "refused": int(measured.get("refused") or 0),
            "failed": int(measured.get("failed") or 0)}


def select(candidates: Sequence[Candidate], *,
           costs: Mapping[str, Any] | None = None,
           required_verification: Sequence[str] = ()) -> Selection:
    """The cheapest strategy that is safe and verifies what is needed.

    The candidates are already proven safe by their caller; this
    function filters them by what they VERIFY, compares the ones that
    remain on their MEASURED cost, and records the comparison.

    * a candidate that is not safe is excluded, with its reason;
    * a candidate that does not verify every required item is excluded,
      with the item it misses;
    * when two or more candidates remain and their costs have been
      measured, the cheapest measured one is chosen;
    * otherwise the candidate that disturbs the least is chosen, and the
      record says the cost was unmeasured - an unmeasured strategy is
      never reported as a cheap one.
    """
    required = tuple(str(item) for item in (required_verification or ()))
    comparison: list[dict[str, Any]] = []
    eligible: list[Candidate] = []
    for candidate in candidates:
        entry: dict[str, Any] = {
            "strategy": candidate.strategy,
            "safe": bool(candidate.safe),
            "reason": candidate.reason,
            "mutation_scope": profile(candidate.strategy).mutation_scope,
            "verifies": list(candidate.verifies),
        }
        entry.update(_cost_entry(costs, candidate.strategy))
        if not candidate.safe:
            entry["excluded"] = "not proven safe: " + candidate.reason
            comparison.append(entry)
            continue
        missing = [item for item in required if item not in candidate.verifies]
        if missing:
            entry["excluded"] = ("does not verify: " + ", ".join(missing))
            comparison.append(entry)
            continue
        eligible.append(candidate)
        comparison.append(entry)

    if not eligible:
        raise NoStrategyAvailable(
            "every candidate was excluded: " + "; ".join(
                str(entry.get("excluded") or "unknown")
                for entry in comparison),
            comparison=comparison)

    measured = [candidate for candidate in eligible
                if _cost_entry(costs, candidate.strategy)["measured"]]
    if len(measured) >= 2:
        chosen = min(measured, key=lambda candidate: (
            float(_cost_entry(costs, candidate.strategy)["wall_s_p50"] or 0.0),
            PRESERVATION_ORDER.index(candidate.strategy)))
        reason = ("the cheapest measured strategy that is safe and "
                  "verifies what this request needs")
        basis = "measured"
    else:
        chosen = min(eligible, key=lambda candidate: (
            PRESERVATION_ORDER.index(candidate.strategy)))
        reason = ("the strategy that disturbs the least; the cost of "
                  "these strategies has not been measured here, so the "
                  "preservation order decided")
        basis = "unmeasured"
    for entry in comparison:
        entry["chosen"] = entry["strategy"] == chosen.strategy
    return Selection(strategy=chosen.strategy, reason=reason,
                     comparison=tuple(comparison), cost_basis=basis)


def record_request(*, strategy: str, wall_s: float,
                   verification: str, request_id: str = "",
                   target: str = "", **fields: Any) -> dict[str, Any]:
    """Record ONE execution request's strategy, wall clock and outcome.

    The row is what makes the three strategies comparable: they are
    measured in the same unit, on the same ledger, with the
    verification outcome beside the seconds.  A request whose cost was
    never measured leaves no row, and the measurement says it was never
    measured - it is never a zero.
    """
    name = str(strategy or "")
    if name not in PROFILES:
        raise ValueError(
            f"unknown execution strategy: {name!r}; one of "
            f"{list(STRATEGIES)}")
    outcome = str(verification or "")
    if outcome not in VERIFICATION_OUTCOMES:
        raise ValueError(
            f"unknown verification outcome: {outcome!r}; one of "
            f"{list(VERIFICATION_OUTCOMES)}")
    from library.tools import perf_ledger

    row = {"strategy": name,
           "verification": outcome,
           "request_id": str(request_id or ""),
           "target": str(target or ""),
           "mutation_scope": PROFILES[name].mutation_scope,
           **fields}
    perf_ledger.record(STRATEGY_LAYER, max(0.0, float(wall_s)),
                       **row)
    return row


def measured_costs(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict]:
    """What each strategy has MEASURED to cost, with its denominator.

    Read from ONE project's ledger, from two kinds of row:

    * the request's own row (`execution_strategy`), which carries the
      strategy, the wall clock and the verification outcome; and
    * the touch path's PHASE rows, which every touch wrote before this
      module existed - the staged route's three phases and the direct
      route's one.

    A staged touch's cost is the sum of its three phases, and a touch
    that never reached its promotion is INTERRUPTED: its seconds are a
    lower bound, not a request's cost, so it is counted and reported
    apart from the statistics.  A staged touch that ran all three
    phases is COMPLETED - the phase rows do not carry the promotion's
    verdict, which is on the receipt and in the undo journal, so
    "completed" is what the ledger can honestly say.  A direct phase
    that declined is a DECLINED attempt: the route was proved and
    refused, which is a cost of the proof and not a direct write.

    A strategy with no row is absent from the answer: it has not been
    measured here, which is a different fact from costing nothing.
    """
    samples: dict[str, list[float]] = {}
    outcomes: dict[str, dict[str, int]] = {}
    staged_groups: dict[tuple, dict] = {}
    direct_groups: dict[tuple, dict] = {}

    def _count(strategy: str, outcome: str) -> None:
        counts = outcomes.setdefault(strategy, {"verified": 0, "refused": 0,
                                                "failed": 0, "declined": 0,
                                                "interrupted": 0,
                                                "completed": 0})
        if outcome in counts:
            counts[outcome] += 1

    for row in rows or ():
        layer = str(row.get("layer") or "")
        if layer == STRATEGY_LAYER:
            strategy = str(row.get("strategy") or "")
            if strategy not in PROFILES:
                continue
            wall = row.get("wall_s")
            if not isinstance(wall, (int, float)):
                continue
            samples.setdefault(strategy, []).append(float(wall))
            _count(strategy, str(row.get("verification") or ""))
            continue
        if layer == DIRECT_PHASE_LAYER:
            wall = row.get("wall_s")
            if not isinstance(wall, (int, float)):
                continue
            key = (str(row.get("run_id") or ""),
                   str(row.get("touchup_target") or ""))
            group = direct_groups.setdefault(key, {"wall_s": 0.0,
                                                   "notes": []})
            group["wall_s"] += float(wall)
            note = str(row.get("touchup_note") or "")
            if note:
                group["notes"].append(note)
            continue
        if layer in STAGED_PHASE_LAYERS:
            wall = row.get("wall_s")
            if not isinstance(wall, (int, float)):
                continue
            key = (str(row.get("run_id") or ""),
                   str(row.get("touchup_target") or ""))
            group = staged_groups.setdefault(key, {"wall_s": 0.0,
                                                   "phases": set()})
            group["wall_s"] += float(wall)
            group["phases"].add(layer)

    for group in staged_groups.values():
        complete = set(STAGED_PHASE_LAYERS) <= group["phases"]
        if not complete:
            _count("staged_incremental", "interrupted")
            continue
        samples.setdefault("staged_incremental", []).append(group["wall_s"])
        _count("staged_incremental", "completed")

    for group in direct_groups.values():
        notes = group["notes"]
        if any(note.startswith("applied") for note in notes):
            outcome = "verified"
        elif any(note.startswith("declined") for note in notes):
            outcome = "declined"
        else:
            outcome = "unknown"
        samples.setdefault("direct_patch", []).append(group["wall_s"])
        _count("direct_patch", outcome)

    measured: dict[str, dict] = {}
    for strategy, values in samples.items():
        measured[strategy] = {
            "requests": len(values),
            "wall_s_p50": _quantile(values, 0.5),
            "wall_s_p95": _quantile(values, 0.95),
            "wall_s_min": round(min(values), 3),
            "wall_s_max": round(max(values), 3),
            **outcomes.get(strategy, {}),
        }
    return measured


def _quantile(values: Sequence[float], fraction: float) -> float:
    """The `fraction` quantile of measured values, nearest-rank."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       round(fraction * (len(ordered) - 1))))
    return round(float(ordered[index]), 3)


def comparison(costs: Mapping[str, Any] | None = None) -> list[dict]:
    """The three strategies side by side: cost, preservation, verification.

    This is the table AXI-016 asks for - what each strategy costs HERE,
    what it preserves and what it verifies - and it is the same data the
    selection reads, so the record and the decision cannot disagree.
    """
    rows: list[dict] = []
    for name in STRATEGIES:
        details = profile(name)
        entry = {"strategy": name,
                 "mutation_scope": details.mutation_scope,
                 "preserves": list(details.preserves),
                 "disturbs": list(details.disturbs),
                 "verification": list(details.verification),
                 "recovery": details.recovery,
                 "constructs_whole_reel": details.constructs_whole_reel}
        entry.update(_cost_entry(costs, name))
        rows.append(entry)
    return rows


class RebuildEscapeRefused(RenRefusal):
    """A rebuild was chosen to escape a qualification refusal.

    AXI-016: the bulk build stays the right route for CONSTRUCTING a
    reel.  It is not a way around a refusal: only an `unsupported`
    refusal may be followed by a rebuild, and only when the caller
    authorized it.  A stale base, a conflicting live edit and an
    unreadable timeline are three different facts, and none of them is
    a reason to reconstruct the reel over the top of it.
    """

    CODE = "REN_REBUILD_ESCAPE_REFUSED"
    BLOCKER = "unsafe_state"
    USER_MESSAGE = ("I refused to rebuild the reel over a refusal it does "
                    "not answer, so nothing on the timeline changed.")

    def __init__(self, why: str, *, category: str = "",
                 authorization: str = ""):
        self.category = str(category or "")
        self.authorization = str(authorization or "")
        super().__init__(
            "a rebuild may not follow this refusal",
            why,
            ("Resolve the refusal itself: re-read the live state for a "
             "stale base, ask for direction on a conflicting edit, or "
             "qualify the operation. Ask for the rebuild explicitly if "
             "the whole reel must be reconstructed."))


def assert_documented_escalation(decision: Mapping[str, Any], *,
                                 refusal_category: str = "",
                                 authorized: bool = False,
                                 construction: bool = False) -> dict:
    """Whether the rebuild this decision chose is a DOCUMENTED escalation.

    A rebuild is allowed when the request CONSTRUCTS the reel - that is
    the bulk build, and it is not an escape from anything - or when the
    refusal it followed was `unsupported` and the caller authorized it.
    Anything else is refused here, by name, so a decision that reached
    the rebuild by a route nobody authorized cannot land.
    """
    backend = str((decision or {}).get("backend") or "")
    if backend != "full_rebuild":
        return {"escalation": False, "backend": backend}
    if construction:
        return {"escalation": False, "backend": backend,
                "reason": "the request constructs the reel - the bulk "
                          "build is the route, not an escape"}
    category = str(refusal_category or "")
    if category == "unsupported" and authorized:
        return {"escalation": True, "backend": backend,
                "category": category,
                "reason": "the caller authorized the rebuild after an "
                          "unsupported refusal"}
    raise RebuildEscapeRefused(
        "the rebuild follows a "
        f"{category or 'unclassified'} refusal"
        + ("" if authorized else " that nobody authorized"),
        category=category,
        authorization="authorized" if authorized else "unauthorized")


__all__ = [
    "PRESERVATION_ORDER",
    "PROFILES",
    "STRATEGIES",
    "STRATEGY_LAYER",
    "VERIFICATION_OUTCOMES",
    "Candidate",
    "NoStrategyAvailable",
    "RebuildEscapeRefused",
    "Selection",
    "StrategyProfile",
    "assert_documented_escalation",
    "comparison",
    "measured_costs",
    "profile",
    "record_request",
    "select",
]
