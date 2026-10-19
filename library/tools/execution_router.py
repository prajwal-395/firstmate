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
ROUTING_REFUSAL_CATEGORIES = (
    "unsupported",
    "stale",
    "conflict",
    "backend_failure",
)

#: Strategy-only request ids kept for callers that select a physical plan
#: rather than algebra operations. They are not algebra ops and are never
#: reported as qualified; they delegate to the strategy entry points below.
STRATEGY_FULL_BUILD_ID = "reel.full_build"
STRATEGY_INCREMENTAL_ID = "incremental.overlay_diff"

_PROPERTY_OPS = frozenset({"clip.set_property", "clip.set_enabled"})


class RoutingRefused(RuntimeError):
    """No executor can apply the requested semantics safely."""

    category = "unsupported"

    def as_dict(self) -> dict:
        from library.tools.transaction_orchestration import ExecutionRefusal

        return ExecutionRefusal(self.category, str(self)).as_dict()


class IncrementalRoutingRefused(RoutingRefused):
    """Incremental refusal that must not be collapsed into a rebuild."""

    def __init__(self, category: str, reason: str):
        self.category = category
        self.reason = reason
        super().__init__(f"incremental {category}: {reason}")


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
            "media_type": self.selection.get("media_type", "unknown"),
            "timeline_state": self.selection.get("timeline_state", "unknown"),
            "preservation_constraints": list(
                self.selection.get("preservation_constraints", ())),
            "safety_class": self.selection.get("safety_class", "unknown"),
            "cost": self.selection.get("cost", "unmeasured"),
            "minimum_mutation": self.selection.get("minimum_mutation", self.backend),
            "limitations": list(self.selection.get("limitations", ())),
        }
        return value


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
        "media_type": context.get("media_type", "unknown"),
        "timeline_state": context.get("timeline_state", "unknown"),
        "preservation_constraints": context.get("preservation_constraints", ()),
        "safety_class": safety[0] if len(safety) == 1 else safety or "unknown",
        "cost": context.get("estimated_cost", "unmeasured"),
        "minimum_mutation": context.get("minimum_mutation", backend),
        "limitations": tuple(limitations) + tuple(context.get("limitations", ())),
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
        raise RoutingRefused("compiled edit must contain an operations sequence")
    result = [dict(op) for op in operations]
    if not result:
        raise RoutingRefused("compiled edit contains no operations")
    return result


def _qualification_level(entry) -> str:
    """Canonical qualification state for one OP_STATUS entry."""
    if entry is None:
        return "unknown"
    return entry.qualification_status


def _statuses(edit_algebra, ids: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Per-op qualification levels, read off the matrix, never hasattr."""
    return tuple((op_id, _qualification_level(edit_algebra.status(op_id))) for op_id in ids)


def _refuse_unproven(edit_algebra, operations: Sequence[dict]) -> None:
    """Refuse unknown ids and ids no backend lists as compilable."""
    unknown = [str(op.get("op", "")) for op in operations
               if edit_algebra.status(str(op.get("op", ""))) is None]
    if unknown:
        raise RoutingRefused("no backend is proven for operations: " + ", ".join(unknown))
    uncompilable = [str(op.get("op", "")) for op in operations
                    if not edit_algebra.status(str(op.get("op", ""))).compilable]
    if uncompilable:
        raise RoutingRefused(
            "no backend lists these operations as compilable: " + ", ".join(uncompilable))


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
                f"{op_id} is missing required parameters: {', '.join(missing)}")
        if spec.item and not op.get("unique_id"):
            raise RoutingRefused(
                f"{op_id} targets an existing timeline item but names no unique_id")
        if media_type is not None:
            entry = edit_algebra.status(op_id)
            if entry is not None and entry.media_types and str(media_type) not in entry.media_types:
                raise RoutingRefused(
                    f"{op_id} does not support media type {media_type!r} "
                    f"(supports: {', '.join(entry.media_types)})")
        checked.append(f"{op_id}: required parameters present")
    return tuple(checked)


def _qualification_gaps(edit_algebra, operations: Sequence[dict]
                        ) -> tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...]:
    """Per op, which backends are qualified / production-enabled (VC-04).

    A gap never silently passes. Ordinary production routing refuses it;
    protected experimental routing must separately provide all required
    staging, preservation and verification guarantees.
    """
    gaps = []
    for op in operations:
        op_id = str(op.get("op", ""))
        entry = edit_algebra.status(op_id)
        if entry is None:
            continue
        qualified = tuple(backend.backend for backend in entry.backends if backend.qualified)
        production = tuple(backend.backend for backend in entry.backends
                           if backend.production_enabled)
        if not production:
            gaps.append((op_id, qualified, production))
    return tuple(gaps)


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
        raise RoutingRefused(f"unknown qualification policy {policy!r}")
    if policy == "production" or context.get("require_qualified"):
        names = ", ".join(op_id for op_id, _qualified, _production in gaps)
        raise RoutingRefused(
            f"no production-qualified executor is available for: {names}; "
            "production routing fails closed")
    required_guards = (
        "protected_staging", "preserve_approved_state", "verify_after_write",
    )
    missing = [name for name in required_guards if context.get(name) is not True]
    if missing:
        raise RoutingRefused(
            "protected experimental routing requires explicit true guarantees: "
            + ", ".join(missing))


def _preflight_full_rebuild_operations(edit_algebra, operations, context) -> None:
    """A rebuild cannot turn an unknown or unqualified semantic op into support."""
    _refuse_unproven(edit_algebra, operations)
    _check_op_conditions(edit_algebra, operations, context)
    _enforce_qualification_policy(
        _qualification_gaps(edit_algebra, operations), context)


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
    category = str(context.get("ineligibility_category") or "unsupported")
    if category not in ROUTING_REFUSAL_CATEGORIES:
        return "unsupported"
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
    rejected_reason = ineligibility or str(context.get("ineligibility_reason")
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
    from library.tools import edit_algebra

    context = dict(context or {})
    for flag in DIRECT_BYPASS_FLAGS:
        if context.get(flag):
            raise RoutingRefused(
                f"direct execution is never selected by flag ({flag}): direct "
                "needs the VC-11 per-op proof - reversible class, independent "
                "read-back, qualified executor and proven inverse")
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
        raise RoutingRefused("no backend is proven for operations: " + ", ".join(ids))
    if set(ids) == {STRATEGY_INCREMENTAL_ID}:
        if context.get("incremental_eligibility") is True:
            return route_incremental(operations=operations, context=context)
        raise RoutingRefused("no backend is proven for operations: " + ", ".join(ids))

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
        decision = route_incremental(operations=operations, context=context)
        gaps = _qualification_gaps(edit_algebra, operations)
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
            "takes no plan op")
    unproven = [op_id for op_id in ids
                if edit_algebra.safety_class(op_id) == edit_algebra.SAFETY_UNREPRESENTABLE]
    if unproven:
        raise RoutingRefused(
            "no qualified executor proves: " + ", ".join(unproven) +
            " - refused under VC-11 unrepresentable_or_unqualified; stage "
            "nothing until new qualification evidence lands")
    request_class = edit_algebra.request_safety_class(ids)

    # VC-04: refuse what no backend proves, and check op-specific
    # conditions, before any targeted backend is named.
    _refuse_unproven(edit_algebra, operations)
    checked = _check_op_conditions(edit_algebra, operations, context)
    gaps = _qualification_gaps(edit_algebra, operations)
    _enforce_qualification_policy(gaps, context)
    statuses = _statuses(edit_algebra, ids)
    gap_reasons = _gap_reasons(gaps)

    if all(op_id in _PROPERTY_OPS for op_id in ids):
        if any(not op.get("unique_id") for op in operations):
            raise RoutingRefused("property patch requires an existing-item unique_id")
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
    raise RoutingRefused("no backend is proven for operations: " + ", ".join(ids))


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
            "structural candidate requires only VC-11 structural operations")
    safety = edit_algebra.SAFETY_CLASS_POLICY[edit_algebra.SAFETY_STRUCTURAL]
    if (safety.direct_allowed or not safety.staged_allowed
            or not safety.requires_qualified_executor
            or not safety.requires_readback):
        raise RoutingRefused(
            "VC-11 structural policy does not permit a staged, qualified, "
            "read-back verified executor")
    plan = context.get("structural_patch")
    if not isinstance(plan, Mapping):
        raise RoutingRefused(
            "structural candidate has no dependency plan; full reconstruction "
            "is required")
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
