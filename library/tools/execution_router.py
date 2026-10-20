"""Authoritative execution routing for compiled timeline edits.

VC-03 / VC-04 / VC-06 / VC-09: exactly one strategy is assigned before any
Resolve write - no-op | direct EditPatch | staged incremental | staged full
rebuild - and the decision names its evidence. Qualification comes from
``edit_algebra.OP_STATUS`` (defined / compilable / qualified /
production_enabled per operation and backend); a native ``apply`` existing
never qualifies a backend, it only states what an executor lists as
compilable. A refusal that carries an incremental category (unsupported /
stale / conflict / backend failure) is never collapsed into a rebuild here:
only an explicit caller policy (``allow_full_rebuild``) or an explicit
``requested_backend == "full_rebuild"`` selects the full rebuild.

VC-06: staging stays the production default (``STAGING_IS_PRODUCTION_DEFAULT``).
New routing behaviour runs observation-only through ``observe_with_gates``:
the production decision stays authoritative, the candidate decision is
compared and reported, per-op enablement lives on the gates object, dropping
the gates rolls back instantly, and observations are process-local (stderr
plus an in-memory ledger) - no persisted format changes.

VC-11: execution safety classes (``edit_algebra.OP_SAFETY_CLASS``) decide
direct vs staged vs refused. Direct is proven per op - reversible-mutation
class, independent read-back, a production-enabled executor and a proven
executable inverse on the chosen backend - never requested: any caller flag
asking for direct (``DIRECT_BYPASS_FLAGS``) is refused outright, and the
``staged`` flag can only escalate to staged, never widen to direct.
Nothing goes direct merely because it is small or an inverse name exists.
"""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field

from library.tools.ren_refusal import RenRefusal

#: VC-06: staging stays the production default. No router path promotes a
#: direct-write default; gates may only observe candidates beside it.
STAGING_IS_PRODUCTION_DEFAULT = True
QUALIFICATION_POLICIES = frozenset({"production", "protected_experimental"})

#: VC-11: no caller flag may select a direct write. A bypass flag that is
#: set is refused outright - direct is proven per op, never requested.
DIRECT_BYPASS_FLAGS: tuple[str, ...] = (
    "direct",
    "allow_direct",
    "force_direct",
    "allow_direct_edit",
    "force_direct_edit",
    "direct_edit",
)

#: VC-03: refusal categories that must stay distinct end to end. Stale bases,
#: conflicting live edits and broken reads are not backend-selection hints;
#: only ``unsupported`` may fall back to a rebuild, and only under an
#: explicit caller policy.
#:
#: AXI-40: the set is CLOSED. A category outside it is never coerced into
#: ``unsupported`` - that is the one category a rebuild may follow, so an
#: unrecognised value coerced into it turns a broken read into a silent
#: rebuild. ``broken`` (live state could not be read) is a category of its
#: own: it is neither a stale base nor a conflicting edit, and it must not
#: be rebuilt over either.
ROUTING_REFUSAL_CATEGORIES = (
    "unsupported",
    "stale",
    "conflict",
    "backend_failure",
    "broken",
)

#: AXI-42: what the caller does next, per category. The category says what
#: kind of refusal this is; the fix says the exact next step. Both travel
#: with the refusal, so a host never has to infer the action from prose.
ROUTING_REFUSAL_FIXES: Mapping[str, str] = {
    "unsupported": (
        "Report the refusal and the qualification gap: a backend must be "
        "qualified before this edit can run. Do not substitute another "
        "backend or a full rebuild without an explicit authorization."),
    "stale": (
        "Re-read the timeline, resolve the target again and prepare a "
        "fresh request against the current generation."),
    "conflict": (
        "Preserve the live state and ask for direction; the intended "
        "result is no longer clear."),
    "backend_failure": (
        "Read the transaction receipt and its recovery result before "
        "deciding whether another attempt is safe."),
    "broken": (
        "Re-read the live timeline state; do not rebuild over a timeline "
        "nobody could read."),
}

#: AXI-42: how the host may proceed, per category. ``auto_recoverable``
#: permits only the named safe recovery, ``needs_question`` asks the
#: smallest blocking question, ``blocked`` states what must be resolved
#: first.
ROUTING_REFUSAL_RECOVERY_CLASSES: Mapping[str, str] = {
    "unsupported": "blocked",
    "stale": "auto_recoverable",
    "conflict": "needs_question",
    "backend_failure": "blocked",
    "broken": "blocked",
}

#: Strategy-only request ids kept for callers that select a physical plan
#: rather than algebra operations. They are not algebra ops and are never
#: reported as qualified; they delegate to the strategy entry points below.
STRATEGY_FULL_BUILD_ID = "reel.full_build"
STRATEGY_INCREMENTAL_ID = "incremental.overlay_diff"

#: AXI-39: the mutation scope of every route the router can select.
#: `targeted` touches only what changed; `full_rebuild` reconstructs the
#: timeline and therefore disturbs everything it does not carry over;
#: `none` writes nothing. The decision records which one it chose, so a
#: receipt can answer "was the rest of the reel preserved?" without
#: reading the code that ran.
ROUTE_MUTATION_SCOPE: Mapping[str, str] = {
    "no_op": "none",
    "incremental": "targeted",
    "edit_patch": "targeted",
    "edit_patch.property": "targeted",
    # Semantic AXI adapters perform a narrowly authorized request and own
    # their live read-back.  They are a targeted route, never a rebuild.
    "resolve_axi": "targeted",
    "reel_touchup": "targeted",
    "composed_edit.structural": "targeted",
    "full_rebuild": "full_rebuild",
}

#: AXI-41: the one guard every route's ownership is enforced by.
TRANSACTION_OWNERSHIP_ENFORCED_BY = "library/tools/resolve_lock.py"

#: The constraints a plan may put on the ROUTE, and the routes each one
#: admits. A constraint may only NARROW the choice: it names routes the
#: router may pick, and it can never make a route eligible that the
#: evidence would not have chosen. The model does not pick a backend; it
#: states a requirement the router satisfies or refuses by name.
#:
#: `no_full_rebuild` is stated as a mutation scope rather than a route
#: name, because a full rebuild is the one route that disturbs everything
#: it does not carry over - and a plan that must not disturb the rest of
#: the reel says so in those terms.
BACKEND_CONSTRAINT_ROUTES: Mapping[str, tuple[str, ...]] = {
    "direct_patch_only": ("edit_patch", "edit_patch.property"),
    "staged_only": ("reel_touchup", "composed_edit.structural",
                    "full_rebuild"),
}

#: The constraints that are about a route's MUTATION SCOPE rather than
#: its name.
BACKEND_CONSTRAINT_SCOPES: Mapping[str, tuple[str, ...]] = {
    "no_full_rebuild": ("targeted", "none"),
}


def _ownership(broker_kind: str, *, lease: str, writes: bool) -> dict:
    """One route's transaction ownership, in the common shape."""
    return {"broker_kind": broker_kind,
            "lease": lease,
            "writes": writes,
            "single_instance": True,
            "enforced_by": TRANSACTION_OWNERSHIP_ENFORCED_BY}


#: AXI-41: who owns the mutation for each route. Every Resolve write runs
#: under the single-instance broker and one transaction owner; a decision
#: that does not say so has not said enough about the write it chose.
#: `broker_kind` is the typed commit adapter that owns the route when AXI
#: dispatches it, and is empty for the routes the reel build runs itself
#: under its own exclusive lease.
ROUTE_TRANSACTION_OWNERSHIP: Mapping[str, Mapping[str, object]] = {
    "no_op": _ownership("", lease="none", writes=False),
    "edit_patch": _ownership("timeline.apply_patch", lease="exclusive",
                             writes=True),
    "edit_patch.property": _ownership("timeline.apply_patch",
                                      lease="exclusive", writes=True),
    "resolve_axi": _ownership("timeline.semantic_edit", lease="exclusive",
                               writes=True),
    "reel_touchup": _ownership("timeline.semantic_edit", lease="exclusive",
                               writes=True),
    "composed_edit.structural": _ownership("timeline.semantic_edit",
                                            lease="exclusive", writes=True),
    "incremental": _ownership("", lease="exclusive", writes=True),
    "full_rebuild": _ownership("", lease="exclusive", writes=True),
}

_PROPERTY_OPS = frozenset({"clip.set_property", "clip.set_enabled"})


class RoutingRefused(RenRefusal):
    """No executor can apply the requested semantics safely.

    AXI-42: this is the engine-wide refusal shape, not a bare exception.
    It carries what happened, why, the next action, the recovery class
    and the backend limitations that blocked the route, so it reaches the
    `ren` / AXI boundary as a REFUSAL (exit 4, with a fix) instead of an
    unexpected error (exit 1, with a traceback).

    The category is a closed vocabulary member (``ROUTING_REFUSAL_CATEGORIES``)
    and it is what decides whether a rebuild may follow: only
    ``unsupported`` may.
    """

    CODE = "REN_ROUTING_REFUSED"
    USER_MESSAGE = ("I couldn't find a safe execution route for this edit, "
                    "so nothing on the timeline changed. The blocking "
                    "limitation is named below.")

    def __init__(self, why: str, *, what: str = "", fix: str = "",
                 category: str = "unsupported",
                 limitations: Sequence[str] = (),
                 recovery_class: str = "") -> None:
        if category not in ROUTING_REFUSAL_CATEGORIES:
            raise ValueError(
                f"unknown routing refusal category: {category!r}; one of "
                f"{list(ROUTING_REFUSAL_CATEGORIES)}")
        self.category = category
        self.limitations = tuple(str(item) for item in limitations)
        super().__init__(
            what or "no eligible execution route for the requested edit",
            why,
            fix or ROUTING_REFUSAL_FIXES[category],
            recovery_class=(recovery_class
                            or ROUTING_REFUSAL_RECOVERY_CLASSES[category]),
        )

    def as_dict(self) -> dict:
        """The normalized refusal value every backend reports."""
        from library.tools.transaction_orchestration import ExecutionRefusal

        return ExecutionRefusal.from_exception(self).as_dict()


class IncrementalRoutingRefused(RoutingRefused):
    """Incremental refusal that must not be collapsed into a rebuild."""

    def __init__(self, category: str, reason: str):
        self.reason = reason
        super().__init__(
            f"incremental {category}: {reason}",
            what=("the incremental route refused this plan, and it may not "
                  "be collapsed into a rebuild"),
            category=category,
        )


@dataclass(frozen=True)
class RejectedBackend:
    backend: str
    reason: str


@dataclass(frozen=True)
class ExecutionDecision:
    backend: str
    reasons: tuple[str, ...]
    rejected_alternatives: tuple[RejectedBackend, ...]
    verification: tuple[str, ...]
    algebra_status: tuple[tuple[str, str], ...]
    mode: str = "direct"
    qualification_gaps: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = ()
    selection: Mapping = field(default_factory=dict)

    def as_dict(self) -> dict:
        value = asdict(self)
        # Preserve the legacy decision fields while making the dimensions
        # behind the choice explicit and machine-readable. Missing evidence
        # stays "unknown"; a router must not infer live state from silence.
        value["selection"] = {
            "semantics": list(self.selection.get("semantics", ())),
            "qualification": self.selection.get("qualification", "unknown"),
            "qualification_policy": self.selection.get(
                "qualification_policy", "production"),
            "qualification_state_inspected": self.selection.get(
                "qualification_state_inspected", False),
            "media_type": self.selection.get("media_type", "unknown"),
            "timeline_state": self.selection.get("timeline_state", "unknown"),
            "preservation_constraints": list(
                self.selection.get("preservation_constraints", ())),
            "safety_class": self.selection.get("safety_class", "unknown"),
            "cost": self.selection.get("cost", "unmeasured"),
            "minimum_mutation": self.selection.get("minimum_mutation", self.backend),
            "limitations": list(self.selection.get("limitations", ())),
            # AXI-39: whether this route reconstructs the timeline or
            # touches only what changed. A receipt that cannot say so
            # cannot answer "was the rest of the reel preserved?".
            "mutation_scope": self.selection.get("mutation_scope", "unknown"),
            # AXI-41: who owns the write, and under which lease. The
            # decision says so, not only the code path that runs it.
            "transaction_ownership": dict(
                self.selection.get("transaction_ownership", {})),
        }
        return value


def _ownership_for(backend: str) -> dict:
    """Who owns the write this route performs.

    AXI-41: every Resolve mutation runs under the single-instance broker
    and one transaction owner. The decision states which, so the receipt
    carries the ownership instead of leaving it to be inferred from the
    code path that runs it. A route the router does not know about
    declares no ownership rather than borrowing another route's.
    """
    entry = ROUTE_TRANSACTION_OWNERSHIP.get(backend)
    if entry is None:
        return _ownership("", lease="unknown", writes=False) | {
            "writes": None}
    return dict(entry)
def _preservation_constraints(operations: Sequence[Mapping],
                              context: Mapping) -> tuple:
    """What this edit writes, and therefore what must survive it.

    A backend choice is a preservation promise: the chosen backend
    promises that everything OUTSIDE the domains the operations write
    stays where it is. The record used to say `()` - no constraint at
    all - however many domains the edit touched. These are the
    operations' own declared domains, read off the algebra, not a
    summary written beside the decision.
    """
    declared = context.get("preservation_constraints")
    if declared:
        return tuple(str(value) for value in declared)
    from library.tools import edit_algebra

    domains = set()
    for op in operations:
        spec = edit_algebra.get(str(op.get("op", "")))
        if spec is not None and spec.domain:
            domains.add(str(spec.domain))
    return tuple(sorted(domains))


def _cost_basis(context: Mapping):
    """The measured cost of this choice, or "unmeasured".

    PERF-017: the choice between a targeted mutation and a bulk
    rebuild is made from MEASURED cost and preservation guarantees.
    The preservation half is the domains above; the cost half is what
    this project has measured for the strategies it has run - read
    from the recorded ledger, never from a table of somebody else's
    numbers. Where nothing was measured, the record says so.
    """
    measured = context.get("measured_costs")
    if isinstance(measured, Mapping) and measured:
        return dict(measured)
    estimated = context.get("estimated_cost")
    return estimated if estimated else "unmeasured"


def _selection_basis(operations: Sequence[Mapping], context: Mapping,
                     backend: str, *, limitations: Sequence[str] = ()) -> dict:
    """Capture the evidence dimensions used by a backend choice.

    Context fields are observations supplied by preparation/callers. The
    router records absence as unknown instead of manufacturing certainty.
    """
    from library.tools import edit_algebra

    ids = tuple(str(op.get("op", "")) for op in operations)
    safety = tuple(dict.fromkeys(edit_algebra.safety_class(op_id) for op_id in ids))
    return {
        "semantics": ids,
        "qualification": tuple(_statuses(edit_algebra, ids)),
        "qualification_policy": context.get(
            "qualification_policy", "production"),
        "qualification_state_inspected": bool(
            isinstance(context.get("qualification_state"), Mapping)
            and context["qualification_state"].get("inspected") is True
            and isinstance(context["qualification_state"].get("state"), Mapping)),
        "media_type": context.get("media_type", "unknown"),
        "timeline_state": context.get("timeline_state", "unknown"),
        "preservation_constraints": _preservation_constraints(
            operations, context),
        "backend_constraints": list(context.get("backend_constraints", ())),
        "safety_class": safety[0] if len(safety) == 1 else safety or "unknown",
        "cost": _cost_basis(context),
        "minimum_mutation": context.get("minimum_mutation", backend),
        "limitations": tuple(limitations) + tuple(context.get("limitations", ())),
        # AXI-39: what this route does to the timeline - touches only
        # what changed, or reconstructs it.
        "mutation_scope": ROUTE_MUTATION_SCOPE.get(backend, "unknown"),
        # AXI-41: who owns the write it performs.
        "transaction_ownership": _ownership_for(backend),
    }


@dataclass(frozen=True)
class RoutingGates:
    """Observation-only enablement for candidate routing (VC-06).

    ``incremental_candidate_ops`` names algebra op ids the candidate route
    may try incrementally beside the production decision. The candidate is
    never executed: dropping this object rolls back instantly.
    """

    name: str
    incremental_candidate_ops: tuple[str, ...] = ()
    structural_candidate_ops: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


#: Process-local observation ledger only. Never persisted: VC-06 forbids
#: persisted-format changes until readers and migration tests pass.
GATE_OBSERVATIONS: list = []


def _ops(compiled_edit) -> list[dict]:
    if isinstance(compiled_edit, Mapping):
        operations = compiled_edit.get("operations")
    else:
        operations = compiled_edit
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise RoutingRefused(
            "compiled edit must contain an operations sequence",
            what="the compiled edit carries no operations to route",
            fix="Pass the compiled algebra operations as a list; a request "
                "with no operation list has nothing to route.")
    result = [dict(op) for op in operations]
    if not result:
        raise RoutingRefused(
            "compiled edit contains no operations",
            what="the compiled edit carries no operations to route",
            fix="Compile the request first; an empty operation list has "
                "nothing to route.")
    return result


def _qualification_level(entry) -> str:
    """Canonical qualification state for one OP_STATUS entry."""
    if entry is None:
        return "unknown"
    return entry.qualification_status


def _statuses(edit_algebra, ids: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Per-op qualification levels, read off the matrix, never hasattr."""
    return tuple((op_id, _qualification_level(edit_algebra.status(op_id))) for op_id in ids)


def _limitations_for(edit_algebra, op_ids: Sequence[str]) -> tuple[str, ...]:
    """The recorded limitations of the named operations, by name.

    AXI-42: a refusal that says only "no backend is proven" leaves the
    caller to guess what would have to change. The matrix already records
    why each backend cannot take an operation, so the refusal carries
    those lines verbatim.
    """
    lines: list[str] = []
    for op_id in op_ids:
        entry = edit_algebra.status(op_id)
        if entry is None:
            lines.append(f"{op_id}: no algebra operation is declared for it")
            continue
        for backend in entry.backends:
            for limitation in backend.limitations:
                lines.append(f"{op_id} on {backend.backend}: {limitation}")
    return tuple(dict.fromkeys(lines))


def _refuse_unproven(edit_algebra, operations: Sequence[dict]) -> None:
    """Refuse unknown ids and ids no backend lists as compilable."""
    unknown = [str(op.get("op", "")) for op in operations
               if edit_algebra.status(str(op.get("op", ""))) is None]
    if unknown:
        raise RoutingRefused(
            "no backend is proven for operations: " + ", ".join(unknown),
            what=("these operations are not in the algebra vocabulary: "
                  + ", ".join(unknown)),
            fix=("Use an operation id the registry knows: "
                 "`resolve-axi capabilities --search \"<effect>\"` lists the "
                 "declared vocabulary and its evidence."),
            limitations=_limitations_for(edit_algebra, unknown))
    uncompilable = [str(op.get("op", "")) for op in operations
                    if not edit_algebra.status(str(op.get("op", ""))).compilable]
    if uncompilable:
        raise RoutingRefused(
            "no backend lists these operations as compilable: " + ", ".join(uncompilable),
            what=("no executor lists these operations as compilable: "
                  + ", ".join(uncompilable)),
            fix=("Qualify an executor for these operations before routing "
                 "them; `resolve-axi capabilities <operation> --json` shows "
                 "what each backend lists."),
            limitations=_limitations_for(edit_algebra, uncompilable))


def _check_op_conditions(edit_algebra, operations: Sequence[dict],
                         context: Mapping) -> tuple[str, ...]:
    """Op-specific preconditions checkable before any write (VC-04).

    Only what the operation declares and the caller provides is checked:
    required params, unique item identity, and media-type fit. A required
    key is missing when absent or None; an empty value stays the
    executor's to judge against live state. A check that needs live
    Resolve state belongs to the executor's guards, not here.
    """
    checked = []
    media_type = context.get("media_type")
    for op in operations:
        op_id = str(op.get("op", ""))
        spec = edit_algebra.get(op_id)
        if spec is None:
            continue
        missing = [key for key in spec.required
                   if key not in op or op.get(key) is None]
        if missing:
            raise RoutingRefused(
                f"{op_id} is missing required parameters: {', '.join(missing)}",
                what=f"{op_id} is missing required parameters: "
                     + ", ".join(missing),
                fix=(f"Supply {', '.join(missing)} on the {op_id} operation; "
                     "the operation cannot be planned without them."))
        if spec.item and not op.get("unique_id"):
            raise RoutingRefused(
                f"{op_id} targets an existing timeline item but names no unique_id",
                what=f"{op_id} names no target item",
                fix=("Name the target item's unique_id (or resolve it in "
                     "preparation) so the edit cannot land on the wrong "
                     "clip."),
                limitations=_limitations_for(edit_algebra, [op_id]))
        if media_type is not None:
            entry = edit_algebra.status(op_id)
            if entry is not None and entry.media_types and str(media_type) not in entry.media_types:
                raise RoutingRefused(
                    f"{op_id} does not support media type {media_type!r} "
                    f"(supports: {', '.join(entry.media_types)})",
                    what=f"{op_id} does not support media type {media_type!r}",
                    fix=(f"Run the edit on a "
                         f"{', '.join(entry.media_types)} item, or use an "
                         "operation that supports this media type."),
                    limitations=_limitations_for(edit_algebra, [op_id]))
        checked.append(f"{op_id}: required parameters present")
    return tuple(checked)


def _qualification_gaps(edit_algebra, operations: Sequence[dict],
                        context: Mapping | None = None
                        ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Per op, which backends are qualified / production-enabled (VC-04).

    A gap never silently passes. Ordinary production routing refuses it;
    protected experimental routing must separately provide all required
    staging, preservation and verification guarantees.
    """
    # The dict it was handed, not a fresh one: the concrete predicate
    # mismatch is recorded INTO it below, and `context or {}` would hand
    # a new dict to an EMPTY context and drop the record with it.
    if context is None:
        context = {}
    inspected = context.get("qualification_state")
    gaps = []
    for op in operations:
        op_id = str(op.get("op", ""))
        entry = edit_algebra.status(op_id)
        if entry is None:
            continue
        qualified = tuple(backend.backend for backend in entry.backends if backend.qualified)
        production_rows = tuple(backend for backend in entry.backends
                                if backend.production_enabled)
        production = tuple(backend.backend for backend in production_rows
                           if _qualification_matches(backend, inspected))
        if production_rows and not production:
            requirements = tuple(dict.fromkeys(
                str(predicate.get("field", ""))
                for backend in production_rows
                for predicate in backend.qualification_predicates))
            mismatches = _predicate_mismatches(production_rows, inspected)
            reason = _refusal_reason(inspected, mismatches)
            gaps.append((op_id, qualified, ()))
            # Keep the concrete predicate mismatch available to the caller's
            # refusal and decision receipt without broadening support.
            if isinstance(context, dict):
                context.setdefault("qualification_refusals", {})[op_id] = {
                    "reason": reason, "required_fields": requirements,
                    "mismatches": list(mismatches)}
            continue
        if not production:
            gaps.append((op_id, qualified, production))
    return tuple(gaps)


def _refusal_reason(inspected, mismatches: Sequence[str]) -> str:
    """Why a promoted operation is not production-routable, by name.

    A state nobody read and a state that was read and does not match are
    DIFFERENT facts, and the refusal says which: the first names the
    missing inspection, the second names the predicates the runtime
    failed.
    """
    if not isinstance(inspected, Mapping) or inspected.get("inspected") is not True:
        detail = (str(inspected.get("reason") or "")
                  if isinstance(inspected, Mapping) else "")
        reason = "runtime qualification state was not inspected"
        return f"{reason} ({detail})" if detail else reason
    reason = "runtime state does not satisfy recorded qualification predicates"
    return f"{reason}: {'; '.join(mismatches)}" if mismatches else reason


def _predicate_mismatches(backends: Sequence, inspected) -> tuple[str, ...]:
    """Which recorded predicates the inspected state fails, by name.

    The predicate check itself is unchanged - this only NAMES the fields
    that failed, so the refusal says which condition the runtime is not
    in rather than that the conditions are unmet.
    """
    if not isinstance(inspected, Mapping) or inspected.get("inspected") is not True:
        return ()
    state = inspected.get("state")
    if not isinstance(state, Mapping):
        return ()
    mismatches: list[str] = []
    for backend in backends:
        for predicate in backend.qualification_predicates:
            field = str(predicate.get("field", ""))
            if field not in state:
                mismatches.append(f"{field} was not measured")
                continue
            actual, expected = state[field], predicate.get("value")
            operator = predicate.get("operator")
            if operator == "equals" and actual != expected:
                mismatches.append(f"{field} is {actual!r}, not {expected!r}")
            elif operator == "one_of" and actual not in expected:
                mismatches.append(
                    f"{field} is {actual!r}, not one of {expected!r}")
    return tuple(dict.fromkeys(mismatches))


def _qualification_matches(backend, inspected) -> bool:
    """Match a live inspected state against the evidence's closed predicates."""
    if not backend.qualification_predicates:
        return False
    if not isinstance(inspected, Mapping) or inspected.get("inspected") is not True:
        return False
    state = inspected.get("state")
    if not isinstance(state, Mapping):
        return False
    for predicate in backend.qualification_predicates:
        field = predicate["field"]
        if field not in state:
            return False
        actual, expected = state[field], predicate["value"]
        if predicate["operator"] == "equals" and actual != expected:
            return False
        if predicate["operator"] == "one_of" and actual not in expected:
            return False
    return True


def _axi_implemented_rows(edit_algebra, ids: Sequence[str]):
    """The AXI adapter row for each operation, when the CODE implements it.

    A row that lists implementation evidence names the adapter that runs
    the operation on Resolve; a row without it names a vocabulary, not a
    path.  The protected route may only name a path that exists, so a
    row without evidence keeps the route off the AXI backend.
    """
    rows = []
    for op_id in ids:
        entry = edit_algebra.status(op_id)
        row = entry.for_backend("resolve_axi") if entry is not None else None
        if (row is None or not row.compilable
                or not row.implementation_evidence):
            return None
        rows.append(row)
    return tuple(rows)


def _production_writers_elsewhere(edit_algebra,
                                   ids: Sequence[str]) -> tuple[str, ...]:
    """Production-enabled writers for these ops that are NOT the AXI path.

    A production-qualified writer on another backend keeps the route: the
    AXI adapter is the implemented path, not a better one.
    """
    found: list[str] = []
    for op_id in ids:
        entry = edit_algebra.status(op_id)
        if entry is None:
            continue
        for backend in entry.backends:
            if (backend.backend != "resolve_axi"
                    and backend.production_enabled):
                found.append(f"{op_id} on {backend.backend}")
    return tuple(found)


def _gap_reasons(gaps) -> tuple[str, ...]:
    reasons = []
    for op_id, qualified, _production in gaps:
        if qualified:
            reasons.append(
                f"{op_id} has no production-enabled executor in this path; "
                f"live-qualified: {', '.join(qualified)} - allowed only by "
                "protected experimental policy")
        else:
            reasons.append(
                f"{op_id} has no live-qualified executor on any backend - "
                "allowed only by protected experimental policy")
    return tuple(reasons)


def _enforce_qualification_policy(gaps, context: Mapping) -> None:
    """Keep unqualified implementations out of ordinary production routes."""
    if not gaps:
        return
    policy = str(context.get("qualification_policy", "production"))
    if policy not in QUALIFICATION_POLICIES:
        raise RoutingRefused(
            f"unknown qualification policy {policy!r}",
            what=f"the qualification policy {policy!r} is not one Ren speaks",
            fix=(f"Use one of {sorted(QUALIFICATION_POLICIES)}; an unknown "
                 "policy cannot widen support."))
    if policy == "production" or context.get("require_qualified"):
        names = ", ".join(op_id for op_id, _qualified, _production in gaps)
        refusals = context.get("qualification_refusals", {})
        details = [f"{op_id}: {refusals[op_id]['reason']}"
                   for op_id, _qualified, _production in gaps
                   if op_id in refusals]
        from library.tools import edit_algebra as _algebra

        raise RoutingRefused(
            f"no production-qualified executor is available for: {names}; "
            "production routing fails closed"
            + (" (" + "; ".join(details) + ")" if details else ""),
            what=("no production-qualified executor is available for: "
                  + names),
            fix=("Qualify the operation on a live Resolve timeline and "
                 "import the evidence, or route it under the protected "
                 "experimental policy with explicit staging, preservation "
                 "and verification guarantees."),
            limitations=_limitations_for(
                _algebra, [op_id for op_id, _q, _p in gaps]))
    required_guards = (
        "protected_staging", "preserve_approved_state", "verify_after_write",
    )
    missing = [name for name in required_guards if context.get(name) is not True]
    if missing:
        raise RoutingRefused(
            "protected experimental routing requires explicit true guarantees: "
            + ", ".join(missing),
            what=("the protected experimental route is missing its "
                  "guarantees: " + ", ".join(missing)),
            fix=("State each guarantee explicitly - protected_staging, "
                 "preserve_approved_state and verify_after_write - or route "
                 "under the production policy."))


def _preflight_full_rebuild_operations(edit_algebra, operations, context) -> None:
    """A rebuild cannot turn an unknown or unqualified semantic op into support."""
    _refuse_unproven(edit_algebra, operations)
    _check_op_conditions(edit_algebra, operations, context)
    _enforce_qualification_policy(
        _qualification_gaps(edit_algebra, operations, context), context)


def _mode_for(backend: str, *, blockers: Sequence[str],
                context: Mapping) -> str:
    """The write mode the safety proof allows - never what a flag asks for."""
    if backend == "no_op":
        return "no_op"
    if backend == "incremental":
        return "staged"
    if backend == "full_rebuild":
        return "staged_full_rebuild"
    # VC-11: an EditPatch arm goes direct only when every op carries the
    # full proof (reversible class, read-back, qualified executor, proven
    # inverse). Anything else is staged; the caller's ``staged`` flag can
    # escalate to staged but never widens to direct.
    if blockers:
        return "staged"
    if context.get("staged"):
        return "staged"
    return "direct"


def _proof_reasons(request_class: str | None,
                   blockers: Sequence[str]) -> tuple[str, ...]:
    """Name the VC-11 proof outcome beside the backend selection."""
    if blockers:
        return (
            "direct withheld under VC-11 "
            f"({request_class or 'unclassified'}): " + "; ".join(blockers),
        )
    return (
        "direct proven under VC-11: reversible class, independent "
        + "read-back, qualified executor and proven inverse",
    )


def _proof_verification(blockers: Sequence[str]) -> tuple[str, ...]:
    """The recovery evidence the chosen mode still owes."""
    if blockers:
        return ("staged transaction; approved timeline untouched until promotion",
                "verify unchanged rows and items")
    return ("prior values captured before mutation for inverse recovery",)


def _normalize_category(context: Mapping) -> str:
    """The refusal category this context names, or a refusal about it.

    AXI-40: an UNKNOWN category is never coerced into ``unsupported``.
    ``unsupported`` is the one category a rebuild may follow, so an
    unrecognised value coerced into it turns a broken read or a typo into
    a silent rebuild. The category set is closed; anything outside it
    refuses the route instead of choosing one.
    """
    category = str(context.get("ineligibility_category") or "unsupported")
    if category not in ROUTING_REFUSAL_CATEGORIES:
        raise RoutingRefused(
            f"the ineligibility category {category!r} is not one of "
            f"{list(ROUTING_REFUSAL_CATEGORIES)}",
            what=("the ineligibility category is unknown, so no route may "
                  f"be chosen from it: {category!r}"),
            fix=("Name the category from the closed vocabulary "
                 f"{list(ROUTING_REFUSAL_CATEGORIES)}; an unknown category "
                 "must not be treated as a rebuildable one."),
            category="unsupported")
    return category


def route_full_rebuild(*, reason: str, ineligibility: str | None = None,
                       operations: Sequence[dict] | None = None,
                       context: Mapping | None = None) -> ExecutionDecision:
    """Explicit full-rebuild strategy, selected before any Resolve write."""
    from library.tools import edit_algebra

    context = dict(context or {})
    ops = [dict(op) for op in (operations or [])]
    ids = [str(op.get("op", "")) for op in ops]
    statuses = _statuses(edit_algebra, ids) if ids else ()
    if ineligibility:
        rejected_reason = ineligibility
    elif context.get("incremental_eligibility") is True:
        # AXI-39: the targeted route WAS eligible. Recording it as "not
        # requested or not eligible" would be a lie in the receipt, and
        # choosing a full reconstruction over an eligible targeted edit is
        # exactly the choice a reader needs to see - a rebuild disturbs
        # everything it does not carry over.
        rejected_reason = (
            "incremental was eligible; the rebuild was chosen for: "
            + str(reason))
    else:
        rejected_reason = str(context.get("ineligibility_reason")
                              or "not requested or not eligible")
    return ExecutionDecision(
        backend="full_rebuild",
        reasons=(reason,),
        rejected_alternatives=(RejectedBackend("incremental", rejected_reason),),
        verification=("verify complete compiled timeline inventory",
                      "verify no collateral changes"),
        algebra_status=statuses,
        mode="staged_full_rebuild",
        selection=_selection_basis(ops, context, "full_rebuild",
                                   limitations=(rejected_reason,)),
    )


def route_incremental(*, reason: str = "incremental eligibility checks passed",
                      operations: Sequence[dict] | None = None,
                      context: Mapping | None = None) -> ExecutionDecision:
    """Staged incremental strategy for an eligibility-proven plan."""
    from library.tools import edit_algebra

    context = dict(context or {})
    ops = [dict(op) for op in (operations or [])]
    ids = [str(op.get("op", "")) for op in ops]
    return ExecutionDecision(
        backend="incremental",
        reasons=(reason,),
        rejected_alternatives=(RejectedBackend(
            "full_rebuild", "incremental editor can preserve unchanged timeline items"),),
        verification=("verify patched inventory against the compiled plan",
                      "verify unchanged rows and items"),
        algebra_status=_statuses(edit_algebra, ids) if ids else (),
        mode="staged",
        selection=_selection_basis(ops, context, "incremental"),
    )


def route_no_op(*, reason: str, context: Mapping | None = None) -> ExecutionDecision:
    """No-op strategy: the request is already satisfied, nothing is written."""
    context = dict(context or {})
    return ExecutionDecision(
        backend="no_op",
        reasons=(reason,),
        rejected_alternatives=(RejectedBackend("edit_patch", "no write is needed"),
                               RejectedBackend("full_rebuild", "no write is needed")),
        verification=("no Resolve write issued", "recorded state already satisfies the request"),
        algebra_status=(),
        mode="no_op",
        selection=_selection_basis((), context, "no_op"),
    )


#: The report words for a request the recorded state already satisfies.
#: Callers print this verbatim so a no-op reads as satisfied, never as
#: a skipped or failed edit.
ALREADY_SATISFIED = "already satisfied"


def decision_for_prepared_request(prepared, *, operations: Sequence[dict] | None = None,
                                  context: Mapping | None = None) -> ExecutionDecision | None:
    """The no-op decision for a prepared request, or None when work remains.

    VC-16: the preparation stage detects identical values before any
    asset is generated (`edit_preparation._check_no_write`), and the
    router turns that finding into the cheapest successful edit here:
    no staging timeline, no Resolve write, no render. The decision is
    taken ONLY on verified recorded state - `ready` with no refusals
    and `no_write_needed`. Anything else (no base snapshot, refused
    identities, changes outstanding, an unreadable finding) returns
    None so the caller proceeds down its normal path: an UNVERIFIED
    live state is never reported as an unchanged recorded one.
    """
    ready = getattr(prepared, "ready", None)
    no_write_needed = getattr(prepared, "no_write_needed", None)
    finding_reason = ""
    if ready is None or no_write_needed is None:
        if isinstance(prepared, Mapping):
            ready = prepared.get("ready", ready)
            no_write_needed = prepared.get("no_write_needed", no_write_needed)
            finding = prepared.get("no_write") or {}
            finding_reason = str(finding.get("reason") or "") if isinstance(
                finding, Mapping) else ""
        else:
            return None
    else:
        finding = getattr(prepared, "no_write", None)
        finding_reason = str(getattr(finding, "reason", "") or "")
    if ready is True and no_write_needed is True:
        detail = f" - {finding_reason}" if finding_reason else ""
        return route_no_op(
            reason=f"{ALREADY_SATISFIED}: recorded state already holds "
                   f"every requested value{detail}",
            context=context,
        )
    return None


def route_compiled_edit(compiled_edit, *, context: Mapping | None = None) -> ExecutionDecision:
    """Choose a backend for an already compiled edit and explain every choice.

    Strategy selection lands before any Resolve write. Existing-item
    property setters use EditPatch's targeted native property executor and
    must not reconstruct the timeline. Incremental is selected only on an
    affirmative eligibility result - eligibility evidence is that backend's
    own qualification basis, so it precedes the algebra proof checks; an
    ineligible result is surfaced as a named refusal and never converted
    into a rebuild here. An explicit full rebuild never tries incremental,
    even beside affirmative eligibility. Unknown or uncompilable operations
    are refused, and op-specific conditions (required params, item
    identity, media type) are checked before a targeted backend is named.
    VC-11 safety classes decide direct vs staged vs refused after the
    strategy arms: no caller flag can widen a class to direct, and nothing
    goes direct without the per-op proof.
    """
    context = dict(context or {})
    constraints = tuple(str(c) for c in
                        (context.get("backend_constraints") or ()))
    decision = _route_compiled_edit(compiled_edit, context=context)
    if not constraints:
        return decision
    violation = _constraint_violation(decision.backend, constraints)
    if not violation:
        return decision
    admitted = sorted({route for constraint in constraints
                       for route in _routes_admitted_by(constraint)})
    raise RoutingRefused(
        f"the chosen route {decision.backend!r} breaks the plan's "
        f"constraint {violation!r}",
        what=(f"the plan requires {violation!r}, and the route the "
              f"evidence chose ({decision.backend!r}) does not satisfy it"),
        fix=(f"the constraint admits {admitted or 'no route'}; re-plan the "
             "edit so the eligible route satisfies it, or drop the "
             "constraint"),
        category="unsupported")


def _routes_admitted_by(constraint: str) -> tuple[str, ...]:
    """The routes one constraint admits."""
    routes = BACKEND_CONSTRAINT_ROUTES.get(constraint)
    if routes is not None:
        return routes
    scopes = BACKEND_CONSTRAINT_SCOPES.get(constraint)
    if scopes is None:
        return ()
    return tuple(route for route, scope in ROUTE_MUTATION_SCOPE.items()
                 if scope in scopes)


def _constraint_violation(backend: str, constraints: Sequence[str]) -> str:
    """The constraint this route breaks, or "" when it breaks none.

    A constraint the router does not know is a refusal, not a pass: a
    constraint nobody reads is a declaration nothing honours.
    """
    for constraint in constraints:
        routes = BACKEND_CONSTRAINT_ROUTES.get(constraint)
        if routes is not None:
            if backend not in routes:
                return constraint
            continue
        scopes = BACKEND_CONSTRAINT_SCOPES.get(constraint)
        if scopes is None:
            return constraint
        scope = ROUTE_MUTATION_SCOPE.get(backend, "")
        if scope not in scopes:
            return constraint
    return ""


def _route_compiled_edit(compiled_edit, *, context: Mapping | None = None) -> ExecutionDecision:
    """Choose a backend for an already compiled edit and explain every choice.

    Strategy selection lands before any Resolve write. Existing-item
    property setters use EditPatch's targeted native property executor and
    must not reconstruct the timeline. Incremental is selected only on an
    affirmative eligibility result - eligibility evidence is that backend's
    own qualification basis, so it precedes the algebra proof checks; an
    ineligible result is surfaced as a named refusal and never converted
    into a rebuild here. An explicit full rebuild never tries incremental,
    even beside affirmative eligibility. Unknown or uncompilable operations
    are refused, and op-specific conditions (required params, item
    identity, media type) are checked before a targeted backend is named.
    VC-11 safety classes decide direct vs staged vs refused after the
    strategy arms: no caller flag can widen a class to direct, and nothing
    goes direct without the per-op proof.
    """
    from library.tools import edit_algebra

    context = dict(context or {})
    for flag in DIRECT_BYPASS_FLAGS:
        if context.get(flag):
            raise RoutingRefused(
                f"direct execution is never selected by flag ({flag}): direct "
                "needs the VC-11 per-op proof - reversible class, independent "
                "read-back, qualified executor and proven inverse",
                what=(f"the caller asked for a direct write with {flag!r}, "
                      "which is never a selection"),
                fix=("Remove the flag: direct execution is proven per "
                     "operation, never requested. The router picks the "
                     "mode the safety proof allows."))
    operations = _ops(compiled_edit)
    ids = tuple(str(op.get("op", "")) for op in operations)

    # Strategy-only requests name a physical plan, not algebra operations.
    # They delegate to the strategy entry points and never read as qualified.
    if set(ids) == {STRATEGY_FULL_BUILD_ID}:
        if context.get("request_incremental"):
            reason = str(context.get("ineligibility_reason") or "eligibility was not established")
            category = _normalize_category(context)
            if category == "unsupported" and context.get("allow_full_rebuild"):
                return route_full_rebuild(
                    reason="incremental eligibility rejected the plan",
                    ineligibility=f"unsupported: {reason}",
                    operations=operations, context=context)
            raise IncrementalRoutingRefused(category, reason)
        if context.get("requested_backend") == "full_rebuild":
            return route_full_rebuild(
                reason=str(context.get("reason") or "full build explicitly requested"),
                operations=operations, context=context)
        raise RoutingRefused(
            "no backend is proven for operations: " + ", ".join(ids),
            what=("the strategy request names no route: neither an "
                  "incremental eligibility result nor an explicit rebuild "
                  "was given"),
            fix=("Establish incremental eligibility and pass it, or ask "
                 "for the rebuild route explicitly with "
                 "requested_backend=full_rebuild."))
    if set(ids) == {STRATEGY_INCREMENTAL_ID}:
        if context.get("incremental_eligibility") is True:
            return route_incremental(
                reason=str(context.get("reason")
                           or "incremental eligibility checks passed"),
                operations=operations, context=context)
        raise RoutingRefused(
            "no backend is proven for operations: " + ", ".join(ids),
            what=("the incremental route was asked for without an "
                  "affirmative eligibility result"),
            fix=("Prove incremental eligibility before routing it; the "
                 "route is selected on evidence, not on a request."))

    # VC-03: an explicit full rebuild never tries incremental, so it is
    # decided before eligibility or qualification can select another path.
    if context.get("requested_backend") == "full_rebuild":
        _preflight_full_rebuild_operations(edit_algebra, operations, context)
        return route_full_rebuild(
            reason=str(context.get("reason") or "full build explicitly requested"),
            operations=operations, context=context)

    # VC-09: the no-op outcome is distinguished, never silent.
    if context.get("requested_backend") == "no_op" or context.get("already_satisfied"):
        return route_no_op(
            reason=str(context.get("reason") or "recorded state already satisfies the request"),
            context=context)

    # Eligibility is the incremental backend's own qualification basis (it
    # applies planned overlay diffs, not algebra operations), so an
    # affirmative result selects it before the algebra proof checks below.
    # Qualification levels are still recorded truthfully beside the choice.
    if context.get("incremental_eligibility") is True:
        decision = route_incremental(
            reason=str(context.get("reason")
                       or "incremental eligibility checks passed"),
            operations=operations, context=context)
        gaps = _qualification_gaps(edit_algebra, operations, context)
        _enforce_qualification_policy(gaps, context)
        return ExecutionDecision(
            backend=decision.backend, reasons=decision.reasons,
            rejected_alternatives=decision.rejected_alternatives,
            verification=decision.verification,
            algebra_status=_statuses(edit_algebra, ids), mode=decision.mode,
            qualification_gaps=gaps,
            selection=_selection_basis(operations, context, decision.backend),
        )

    # An ineligible incremental result is a named refusal, never a
    # rebuild: only the unsupported category under an explicit caller
    # policy falls back. This precedes the proof checks so a stale,
    # conflicting or broken verdict keeps its category for unknown ids too.
    if context.get("request_incremental"):
        reason = str(context.get("ineligibility_reason") or "eligibility was not established")
        category = _normalize_category(context)
        if category == "unsupported" and context.get("allow_full_rebuild"):
            _preflight_full_rebuild_operations(edit_algebra, operations, context)
            return route_full_rebuild(
                reason="incremental eligibility rejected the plan",
                ineligibility=f"unsupported: {reason}",
                operations=operations, context=context)
        raise IncrementalRoutingRefused(category, reason)

    # VC-11: plan deliveries and unproven ops never reach a write backend.
    # This precedes the proof checks so the refusal names the safety class;
    # unknown ids keep the existing unproven refusal below.
    read_only = [op_id for op_id in ids
                 if edit_algebra.safety_class(op_id) == edit_algebra.SAFETY_READ_ONLY]
    if read_only:
        raise RoutingRefused(
            "plan deliveries, not timeline writes: " + ", ".join(read_only) +
            " - deliver them to their owning planners; the write router "
            "takes no plan op",
            what=("these operations deliver a plan, they do not write: "
                  + ", ".join(read_only)),
            fix=("Send the plan operation to the planner that owns it; the "
                 "write router does not take a plan op."))
    unproven = [op_id for op_id in ids
                if edit_algebra.safety_class(op_id) == edit_algebra.SAFETY_UNREPRESENTABLE]
    if unproven:
        raise RoutingRefused(
            "no qualified executor proves: " + ", ".join(unproven) +
            " - refused under VC-11 unrepresentable_or_unqualified; stage "
            "nothing until new qualification evidence lands",
            what=("no qualified executor proves these operations: "
                  + ", ".join(unproven)),
            fix=("Qualify an executor for them on a live Resolve timeline "
                 "and import the evidence; until then they are refused, "
                 "not approximated."),
            limitations=_limitations_for(edit_algebra, unproven))
    request_class = edit_algebra.request_safety_class(ids)

    # VC-04: refuse what no backend proves, and check op-specific
    # conditions, before any targeted backend is named.
    _refuse_unproven(edit_algebra, operations)
    checked = _check_op_conditions(edit_algebra, operations, context)
    gaps = _qualification_gaps(edit_algebra, operations, context)
    _enforce_qualification_policy(gaps, context)
    statuses = _statuses(edit_algebra, ids)
    gap_reasons = _gap_reasons(gaps)

    # Registered Resolve AXI adapters are a real semantic backend. Keep the
    # adapter choice inside the router, and make it eligible only when every
    # operation has a production-enabled AXI row whose recorded predicates
    # match the inspected live state. The adapter owns the command, lease,
    # transaction receipt and operation-specific read-back.
    axi_rows = [edit_algebra.status(op_id).for_backend("resolve_axi")
                for op_id in ids]
    if (axi_rows and all(row is not None and row.compilable
                         and row.production_enabled
                         for row in axi_rows)
            and all(_qualification_matches(row, context.get(
                "qualification_state")) for row in axi_rows)):
        blockers = edit_algebra.direct_blockers(ids, "resolve_axi")
        return ExecutionDecision(
            backend="resolve_axi",
            reasons=("every operation has a production-enabled Resolve AXI adapter",
                     "live qualification predicates match the inspected project state",
                     *checked, *gap_reasons,
                     *_proof_reasons(request_class, blockers)),
            rejected_alternatives=(
                RejectedBackend("edit_patch", "the registered AXI adapter owns this semantic operation"),
                RejectedBackend("full_rebuild", "a qualified targeted adapter is available"),
            ),
            verification=("verify the adapter receipt and fresh operation-specific read-back",
                          "verify timeline state outside the declared targets is unchanged",
                          *_proof_verification(blockers)),
            algebra_status=statuses,
            mode=_mode_for("resolve_axi", blockers=blockers, context=context),
            qualification_gaps=gaps,
            selection=_selection_basis(operations, context, "resolve_axi"),
        )

    protected_compound = (
        len(ids) == 2
        and set(ids) in ({"clip.trim", "clip.insert"},
                         {"clip.replace", "caption.style"}))
    if (protected_compound
            and context.get("qualification_policy")
            == "protected_experimental"):
        return ExecutionDecision(
            backend="reel_touchup",
            reasons=("the exact compound has a shared staged transaction adapter",
                     "one candidate is verified against the complete request",
                     "production routing remains closed pending live evidence",
                     *checked, *gap_reasons),
            rejected_alternatives=(
                RejectedBackend("edit_patch", "the approved timeline is not the protected staging target"),
                RejectedBackend("full_rebuild", "a targeted staged transaction is available"),
            ),
            verification=("verify every requested operation on one staged read-back",
                          "verify unrelated timeline state remains unchanged",
                          "retain the source timeline until guarded promotion"),
            algebra_status=statuses, mode="staged",
            qualification_gaps=gaps,
            selection=_selection_basis(
                operations, context, "reel_touchup",
                limitations=("protected experimental compound; live qualification deferred",)),
        )

    # These audio adapters already execute on protected staged candidates and
    # independently verify their requested native/OTIO state. Keep them
    # available to semantic requests through that route while their live
    # qualification remains incomplete. Ordinary router calls still refuse
    # them under the default production policy above.
    if (len(ids) == 1
            and ids[0] in {"audio.gain", "audio.fade", "transition",
                           "overlay.resize", "overlay.position",
                           "clip.split", "clip.slide",
                           "clip.roll"}
            and context.get("qualification_policy") == "protected_experimental"):
        status = edit_algebra.status(ids[0])
        row = status.for_backend("reel_touchup") if status else None
        if (row is not None and row.compilable
                and not row.production_enabled):
            backend_gaps = ((ids[0],
                             ("reel_touchup",) if row.qualified else (),
                             ()),)
            return ExecutionDecision(
                backend="reel_touchup",
                reasons=("the operation has a registered staged touch-up adapter",
                         "protected experimental execution keeps production qualification closed",
                         *checked, *gap_reasons),
                rejected_alternatives=(
                    RejectedBackend("edit_patch", "no qualified EditPatch writer is available for this operation"),
                    RejectedBackend("full_rebuild", "full reconstruction is not the minimum safe mutation"),
                ),
                verification=("verify the adapter receipt and operation-specific fresh state read-back",
                              "verify unrelated timeline state remains unchanged",
                              "retain the source timeline until candidate promotion verifies"),
                algebra_status=statuses,
                mode="staged",
                qualification_gaps=backend_gaps,
                selection=_selection_basis(
                    operations, context, "reel_touchup",
                    limitations=("protected experimental route; live operation qualification deferred",)),
            )

    # AXI-11: the route chosen for an executed edit must be the path
    # that actually ran.  Under the protected experimental policy an
    # operation whose AXI adapter is IMPLEMENTED is routed to that
    # adapter - the journaled AXI verb that performs it - and never to a
    # lower-level writer whose matrix row merely says "compilable": a
    # route that names a path nobody executed records a decision that
    # did not happen.  The qualification gap stays on the record, and
    # promoting this route is the live qualification lane's to do -
    # nothing here grants it.
    if context.get("qualification_policy") == "protected_experimental":
        axi_rows = _axi_implemented_rows(edit_algebra, ids)
        elsewhere = _production_writers_elsewhere(edit_algebra, ids)
        if axi_rows and not elsewhere:
            return ExecutionDecision(
                backend="resolve_axi",
                reasons=("every operation has an implemented Resolve AXI adapter",
                         "protected experimental execution keeps production qualification closed",
                         *checked, *gap_reasons),
                rejected_alternatives=(
                    RejectedBackend("edit_patch", "the AXI adapter is the implemented path for this operation"),
                    RejectedBackend("full_rebuild", "a targeted semantic AXI transaction is available"),
                ),
                verification=("verify the adapter receipt and operation-specific fresh state read-back",
                              "verify unrelated timeline state remains unchanged"),
                algebra_status=statuses,
                mode=_mode_for("resolve_axi",
                               blockers=edit_algebra.direct_blockers(
                                   ids, "resolve_axi"),
                               context=context),
                qualification_gaps=gaps,
                selection=_selection_basis(
                    operations, context, "resolve_axi",
                    limitations=("protected experimental route; live operation qualification deferred",)),
            )

    if all(op_id in _PROPERTY_OPS for op_id in ids):
        if any(not op.get("unique_id") for op in operations):
            raise RoutingRefused(
                "property patch requires an existing-item unique_id",
                what="a property change names no target item",
                fix=("Name the target item's unique_id so the property "
                     "change cannot land on the wrong clip."))
        blockers = edit_algebra.direct_blockers(ids, "edit_patch.property")
        return ExecutionDecision(
            backend="edit_patch.property",
            reasons=("all operations set properties on identified existing items",
                      "property patch preserves unrelated timeline items",
                      *checked, *gap_reasons,
                      *_proof_reasons(request_class, blockers)),
            rejected_alternatives=(
                RejectedBackend("incremental", "incremental editor applies planned overlay diffs, not item property setters"),
                RejectedBackend("full_rebuild", "existing-item property changes must not reconstruct the timeline"),
                RejectedBackend("composed_edit", "re-placement is unnecessary for native property setters"),
            ),
            verification=("read back each requested property", "compare timeline state outside declared targets",
                          *_proof_verification(blockers)),
            algebra_status=statuses,
            mode=_mode_for("edit_patch.property", blockers=blockers, context=context),
            qualification_gaps=gaps,
            selection=_selection_basis(
                operations, context, "edit_patch.property",
                limitations=tuple(reason for op_id, _qualified, _production in gaps
                                  for reason in (f"{op_id} is not production-qualified",))),
        )

    # A compilable EditPatch row is not an authorization to use that
    # writer. Prefer the operation's production-enabled semantic AXI
    # adapter when its recorded runtime predicates match. Otherwise an
    # operation qualified only on Resolve AXI could be misrouted to the
    # lower-level EditPatch implementation simply because it is present.
    resolve_axi_rows = []
    for op_id in ids:
        entry = edit_algebra.status(op_id)
        row = entry.for_backend("resolve_axi") if entry is not None else None
        if row is None or not row.production_enabled:
            resolve_axi_rows = []
            break
        resolve_axi_rows.append(row)
    from library.tools import axi_transaction
    axi_batch_supported = (len(operations) == 1
                           or axi_transaction.batch_supported(
                               "resolve_axi", operations))
    if (resolve_axi_rows and axi_batch_supported
            and all(_qualification_matches(row, context.get(
                "qualification_state")) for row in resolve_axi_rows)):
        blockers = edit_algebra.direct_blockers(ids, "resolve_axi")
        return ExecutionDecision(
            backend="resolve_axi",
            reasons=("every operation has a production-enabled Resolve AXI adapter",
                     "the inspected runtime satisfies each adapter's recorded qualification predicates",
                     *checked, *gap_reasons,
                     *_proof_reasons(request_class, blockers)),
            rejected_alternatives=(
                RejectedBackend("edit_patch", "the semantic AXI adapter is the qualified production writer"),
                RejectedBackend("full_rebuild", "a targeted semantic AXI transaction is available"),
            ),
            verification=("verify every requested operation on fresh live read-back",
                          "verify unrelated timeline state remains unchanged",
                          *_proof_verification(blockers)),
            algebra_status=statuses,
            mode=_mode_for("resolve_axi", blockers=blockers,
                           context=context),
            qualification_gaps=gaps,
            selection=_selection_basis(operations, context, "resolve_axi"),
        )

    # EditPatch schedules exactly the ops its matrix row lists as
    # compilable - read off OP_STATUS, never off a function existing.
    if all((entry := edit_algebra.status(op_id)) is not None
           and (row := entry.for_backend("edit_patch")) is not None
           and row.compilable for op_id in ids):
        blockers = edit_algebra.direct_blockers(ids, "edit_patch")
        return ExecutionDecision(
            backend="edit_patch",
            reasons=("all operations are listed compilable on the EditPatch matrix row",
                     "EditPatch provides the scoped transaction and read-back",
                     *checked, *gap_reasons,
                     *_proof_reasons(request_class, blockers)),
            rejected_alternatives=(
                RejectedBackend("full_rebuild", "a targeted transaction can apply these operations"),
                RejectedBackend("incremental", "incremental editor is reserved for eligible compiled build diffs"),
            ),
            verification=("run algebra read-back verification", "check changes outside declared scope",
                          *_proof_verification(blockers)),
            algebra_status=statuses,
            mode=_mode_for("edit_patch", blockers=blockers, context=context),
            qualification_gaps=gaps,
            selection=_selection_basis(
                operations, context, "edit_patch",
                limitations=tuple(f"{op_id} is not production-qualified"
                                  for op_id, _qualified, _production in gaps)),
        )
    raise RoutingRefused(
        "no backend is proven for operations: " + ", ".join(ids),
        what=("no backend is proven for these operations: "
              + ", ".join(ids)),
        fix=("Look the operations up in the registry - "
             "`resolve-axi capabilities <operation> --json` - and route "
             "one that is qualified, or have the qualification evidence "
             "produced first."),
        limitations=_limitations_for(edit_algebra, ids))


def route_axi_dispatch(operations: Sequence[dict], *,
                       context: Mapping | None = None) -> dict:
    """One routing contract for AXI's direct dispatches (VC-09).

    AXI command handlers route their EditPatch operations through the same
    boundary as build, incremental, touch-up and feedback paths, then
    attach the returned dict beside the patch receipt. A refusal
    propagates: the dispatch must not write what routing would not select.
    VC-11 safety enforcement applies here unchanged: a direct bypass flag
    is refused and an unproven op never authorizes a direct mode.
    """
    decision = route_compiled_edit({"operations": [dict(op) for op in operations]},
                                   context=dict(context or {}))
    return decision.as_dict()


def observe_with_gates(compiled_edit, *, context: Mapping | None = None,
                       gates: RoutingGates) -> dict:
    """Old-vs-new route comparison with the production route authoritative.

    The production decision is what the caller executes. The candidate
    applies the gates' per-op incremental enablement beside it and is only
    reported. Observations stay process-local (returned, appended to
    ``GATE_OBSERVATIONS``, noted on stderr); rollback is dropping ``gates``.
    """
    context = dict(context or {})
    production = route_compiled_edit(compiled_edit, context=context)
    operations = _ops(compiled_edit)
    ids = [str(op.get("op", "")) for op in operations]
    candidate: ExecutionDecision | dict
    candidate_note = "no gate override applies to these operations"
    if (gates.structural_candidate_ops
            and all(op_id in gates.structural_candidate_ops for op_id in ids)):
        try:
            candidate = route_structural_candidate(
                operations=operations, context=context)
            candidate_note = (
                f"candidate evaluates dependency-aware structural execution "
                f"under gates {gates.name!r}; it is observation-only")
        except RoutingRefused as exc:
            candidate = {"refused": str(exc)}
            candidate_note = (
                f"structural candidate refused under gates {gates.name!r}; "
                "production route remains authoritative")
    elif (production.backend != "incremental" and gates.incremental_candidate_ops
            and all(op_id in gates.incremental_candidate_ops for op_id in ids)):
        try:
            candidate = route_incremental(
                reason=f"observation candidate under gates {gates.name!r}",
                operations=operations, context=context)
            candidate_note = f"candidate tries incremental under gates {gates.name!r}"
        except RoutingRefused as exc:
            candidate = {"refused": str(exc)}
            candidate_note = f"candidate refused under gates {gates.name!r}"
    else:
        candidate = production
    record = {
        "gates": gates.name,
        "production_backend": production.backend,
        "candidate_backend": (candidate.backend if isinstance(candidate, ExecutionDecision)
                              else candidate.get("refused", "refused")),
        "agree": ((candidate.backend if isinstance(candidate, ExecutionDecision)
                   else None) == production.backend),
        "note": candidate_note,
    }
    GATE_OBSERVATIONS.append(record)
    print(f"[routing-gates:{gates.name}] production={record['production_backend']} "
          f"candidate={record['candidate_backend']} agree={record['agree']}",
          file=sys.stderr)
    return {"production": production, "candidate": candidate,
            "agree": record["agree"], "record": dict(record)}


def route_structural_candidate(*, operations: Sequence[dict],
                               context: Mapping | None = None
                               ) -> ExecutionDecision:
    """Observation-only structural route, gated by proof and VC-11.

    This decision is never an execution authorization.  ``observe_with_gates``
    keeps the production decision authoritative until exact operation and
    native-state qualification enables a later rollout.
    """
    from library.tools import edit_algebra

    context = dict(context or {})
    ops = [dict(op) for op in operations]
    ids = tuple(str(op.get("op", "")) for op in ops)
    if not ids or any(edit_algebra.safety_class(op_id)
                      != edit_algebra.SAFETY_STRUCTURAL for op_id in ids):
        raise RoutingRefused(
            "structural candidate requires only VC-11 structural operations",
            what="the structural candidate carries a non-structural operation",
            fix=("Route each operation by its own safety class; the "
                 "structural candidate takes structural operations only."))
    safety = edit_algebra.SAFETY_CLASS_POLICY[edit_algebra.SAFETY_STRUCTURAL]
    if (safety.direct_allowed or not safety.staged_allowed
            or not safety.requires_qualified_executor
            or not safety.requires_readback):
        raise RoutingRefused(
            "VC-11 structural policy does not permit a staged, qualified, "
            "read-back verified executor",
            what="the VC-11 structural policy forbids this route",
            fix=("Structural writes need protected staging, a qualified "
                 "executor and independent read-back; the policy does not "
                 "permit it here."))
    plan = context.get("structural_patch")
    if not isinstance(plan, Mapping):
        raise RoutingRefused(
            "structural candidate has no dependency plan; full reconstruction "
            "is required",
            what="the structural candidate has no dependency plan",
            fix=("Prove which affected range can be reused before asking "
                 "for a localized structural edit; without the plan the "
                 "route is a full reconstruction."))
    if plan.get("full_reconstruction_required"):
        reason = str(plan.get("reason") or
                     "dependency graph cannot prove localized reuse")
        return route_full_rebuild(
            reason="structural dependencies are unproven; escalate to full reconstruction",
            ineligibility=reason, operations=ops, context=context)
    if context.get("native_effects_proven") is not True:
        return route_full_rebuild(
            reason="native effects are not proven for the affected structural range",
            ineligibility="native effect preservation/rederivation is unproven",
            operations=ops, context=context)

    backend = str(context.get("structural_executor_backend") or "composed_edit")
    gaps = []
    for op_id in ids:
        status = edit_algebra.status(op_id)
        row = status.for_backend(backend) if status is not None else None
        if row is None or not row.compilable or not row.production_enabled:
            gaps.append(op_id)
    if gaps:
        reason = ("no production-enabled structural executor is qualified for: "
                  + ", ".join(gaps))
        return route_full_rebuild(
            reason="structural executor is not qualified; escalate to full reconstruction",
            ineligibility=reason, operations=ops, context=context)

    return ExecutionDecision(
        backend="composed_edit.structural",
        reasons=("VC-11 structural class requires protected staging",
                 "dependency graph proves the affected range and reused components",
                 "candidate is observation-only under VC-06"),
        rejected_alternatives=(RejectedBackend(
            "direct", "structural operations never execute directly"),),
        verification=("read back every changed placement",
                      "verify continuity, audio synchronization, and supported effects",
                      "preserve proven unaffected rendered assets"),
        algebra_status=_statuses(edit_algebra, ids),
        mode="staged",
        qualification_gaps=(),
        selection=_selection_basis(
            ops, context, "composed_edit.structural",
            limitations=("observation-only candidate",)),
    )


def observe_structural_candidate(*, operations: Sequence[dict],
                                 structural_plan: Mapping,
                                 production_backend: str = "composed_edit",
                                 context: Mapping | None = None) -> dict:
    """Record a structural route comparison without changing the writer.

    The caller has already selected its established staged executor. This
    function evaluates the VC-24 candidate beside it and never returns an
    authorization that can be mistaken for the production decision.
    """
    context = dict(context or {})
    context["structural_patch"] = dict(structural_plan)
    ids = [str(op["op"]) for op in operations]
    gates = RoutingGates(
        name="vc24-structural-observation",
        structural_candidate_ops=tuple(ids))
    try:
        candidate = route_structural_candidate(
            operations=operations, context=context)
        candidate_value = candidate.as_dict()
    except RoutingRefused as exc:
        candidate_value = {"refused": str(exc)}
    record = {
        "gates": gates.name,
        "production_backend": str(production_backend),
        "candidate_backend": candidate_value.get("backend", "refused"),
        "agree": candidate_value.get("backend") == production_backend,
        "note": "observation-only; established staged executor remains authoritative",
        "structural_plan": dict(structural_plan),
    }
    GATE_OBSERVATIONS.append(record)
    print(f"[routing-gates:{gates.name}] production={record['production_backend']} "
          f"candidate={record['candidate_backend']} agree={record['agree']}",
          file=sys.stderr)
    return {"production": production_backend,
            "candidate": candidate_value,
            "agree": record["agree"], "record": dict(record)}


def reset_gate_observations() -> int:
    """Drop all process-local gate observations; returns how many were held."""
    held = len(GATE_OBSERVATIONS)
    del GATE_OBSERVATIONS[:]
    return held
