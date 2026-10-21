"""PLAN-005: can this plan run on THIS timeline, before anything is staged.

A plan names operations and their targets.  Whether those operations can
execute on the actual timeline is a question about the live state, and
the engine used to answer it at the far end of the write path: the plan
was routed, a candidate was staged, an executor was reached, and the
refusal came back from there.  The live unfamiliar-brief benchmark
(PR 2989, ``docs/benchmarks/2026-10-20-unfamiliar-brief-acceptance.md``)
paid for that: five of the eight briefs spent their attempts INSIDE the
staging path to learn that no route existed.

So the question is asked earlier, from the same registries the router
routes from, and the answer is a plan: reject the plan, or revise it,
before the expensive staging starts.

The checks, one per question the punch list asks:

``route_eligible``
    Does an eligible executor exist for this operation at all?  The
    router's own eligibility, read without routing.
``qualification_predicates``
    Does the measured runtime satisfy the predicates a production route
    was qualified against?
``target_identity`` / ``track_presence`` / ``source_handle``
    Is the thing the operation names actually there?
``media_prerequisite`` / ``effect_state``
    Is the media and the effect state of that thing something the
    operation can act on?
``reconstruction_strategy``
    Does a structural change have a supported way of being reconstructed?
``preservation_compatibility``
    Do the preservation constraints and the only available backend agree?

TWO THINGS THIS MODULE DOES NOT DO.

It does not invent a reading of a state it was not given.  A check whose
measurement is missing is UNDETERMINED: undetermined is reported, and it
never reads as a pass and never reads as a refusal (AGENTS.md 10.3, 10.4).
A gate that cannot see cannot forbid.

It does not choose a backend.  The route stays the router's; this module
only says whether the plan can reach one (AGENTS.md 10.5).

THE SEAM.  ``capability_facts`` is the one place this module reads the
engine's knowledge of an operation, so a capability report (BASE-002)
replaces that function and nothing else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The three answers a check may give.
FEASIBLE = "feasible"
INFEASIBLE = "infeasible"
UNDETERMINED = "undetermined"

#: The check ids.  They are STABLE: a receipt, a test and a reader name
#: the same question by the same word.
CHECK_REQUIRED_PARAMETERS = "required_parameters"
CHECK_OPERATION_KNOWN = "operation_known"
CHECK_EXECUTOR_EXISTS = "executor_exists"
CHECK_ROUTE_ELIGIBLE = "route_eligible"
CHECK_CAPABILITY_QUERY = "capability_query"
CHECK_QUALIFICATION_PREDICATES = "qualification_predicates"
CHECK_TARGET_IDENTITY = "target_identity"
CHECK_TRACK_PRESENCE = "track_presence"
CHECK_SOURCE_HANDLE = "source_handle"
CHECK_MEDIA_PREREQUISITE = "media_prerequisite"
CHECK_EFFECT_STATE = "effect_state"
CHECK_RECONSTRUCTION_STRATEGY = "reconstruction_strategy"
CHECK_PRESERVATION_COMPATIBILITY = "preservation_compatibility"

CHECKS: tuple[str, ...] = (
    CHECK_REQUIRED_PARAMETERS,
    CHECK_OPERATION_KNOWN,
    CHECK_EXECUTOR_EXISTS,
    CHECK_ROUTE_ELIGIBLE,
    CHECK_CAPABILITY_QUERY,
    CHECK_QUALIFICATION_PREDICATES,
    CHECK_TARGET_IDENTITY,
    CHECK_TRACK_PRESENCE,
    CHECK_SOURCE_HANDLE,
    CHECK_MEDIA_PREREQUISITE,
    CHECK_EFFECT_STATE,
    CHECK_RECONSTRUCTION_STRATEGY,
    CHECK_PRESERVATION_COMPATIBILITY,
)

#: The check each implementation answers, by the function's own name.
#: The mapping is what lets a caller narrow the set that runs without
#: the engine growing a flag per check.
_CHECK_BY_NAME: Mapping[str, str] = {
    "_check_qualification": CHECK_QUALIFICATION_PREDICATES,
    "_check_capability_query": CHECK_CAPABILITY_QUERY,
    "_check_target_identity": CHECK_TARGET_IDENTITY,
    "_check_track_presence": CHECK_TRACK_PRESENCE,
    "_check_source_handle": CHECK_SOURCE_HANDLE,
    "_check_media_prerequisite": CHECK_MEDIA_PREREQUISITE,
    "_check_effect_state": CHECK_EFFECT_STATE,
    "_check_reconstruction": CHECK_RECONSTRUCTION_STRATEGY,
    "_check_preservation": CHECK_PRESERVATION_COMPATIBILITY,
}

#: The policy the plan is routed under.  A plan that names only
#: experimental operations is not refused for that alone when the
#: protected experimental policy is what the request carries.
PRODUCTION_POLICY = "production"
PROTECTED_EXPERIMENTAL_POLICY = "protected_experimental"


# ── The state the checks read ─────────────────────────────────────────


@dataclass(frozen=True)
class FeasibilityState:
    """What is known about the timeline a plan would run on.

    ``items``
        The measured items, by stable identity.  Each carries the
        fields the checks read: ``track`` (the row it lives on),
        ``source_in`` / ``source_out`` (its source handle),
        ``media_type`` (Resolve's own kind: ``Video``, ``Still``,
        ``Audio``), ``effects`` (the kinds it already carries) and
        ``source_file``.  An EMPTY mapping means the state was not
        read, and every item check is then UNDETERMINED.
    ``tracks``
        The rows the timeline has, by name.  Empty means not read.
    ``runtime``
        The measured runtime state, in the router's vocabulary
        (``{"inspected": True, "state": {...}}`` or
        ``{"inspected": False, "reason": ...}``).
    ``preservation_requirements``
        What the plan promises to leave alone, by name (for example
        ``others-untouched``).
    ``backend_constraints``
        The request's own constraints on the ROUTE, in the router's
        vocabulary (``no_full_rebuild`` and friends).
    ``policy`` / ``originator``
        The routing policy the plan is judged under, and the front door
        that asked for it.
    """

    items: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    tracks: tuple[str, ...] = ()
    runtime: Mapping[str, Any] | None = None
    preservation_requirements: tuple[str, ...] = ()
    backend_constraints: tuple[str, ...] = ()
    policy: str = PRODUCTION_POLICY
    originator: str = ""


# ── What a check found ────────────────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One check's answer, with the evidence it stands on."""

    op: str
    check: str
    status: str
    reason: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "check": self.check,
            "status": self.status,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class FeasibilityVerdict:
    """Whether a plan can run, and what to do about it if it cannot.

    ``status``
        ``infeasible`` when a check refused, ``undetermined`` when a
        check could not determine its answer and none refused, and
        ``feasible`` when every check determined and passed.
    ``revision``
        The revised plan, when one is available: the operations that
        survive, the ones dropped, and what the drop no longer covers.
        A revision is a PROPOSAL: it names what changed, and the caller
        decides whether the plan still answers the request.
    """

    status: str
    findings: tuple[Finding, ...] = ()
    revision: Mapping[str, Any] | None = None
    operations: tuple[str, ...] = ()

    @property
    def infeasible(self) -> bool:
        return self.status == INFEASIBLE

    @property
    def infeasible_operations(self) -> tuple[str, ...]:
        """The operations a check refused, in plan order."""
        refused = {row.op for row in self.findings
                   if row.status == INFEASIBLE}
        return tuple(op for op in self.operations if op in refused)

    @property
    def undetermined_operations(self) -> tuple[str, ...]:
        """The operations a check could not determine, in plan order."""
        unknown = {row.op for row in self.findings
                   if row.status == UNDETERMINED}
        return tuple(op for op in self.operations if op in unknown)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "operations": list(self.operations),
            "findings": [row.as_dict() for row in self.findings],
            "infeasible_operations": list(self.infeasible_operations),
            "undetermined_operations": list(self.undetermined_operations),
            "revision": (dict(self.revision)
                         if self.revision is not None else None),
        }

    def reasons(self) -> tuple[str, ...]:
        """The refusals, by name, one line each."""
        return tuple(f"{row.op}: {row.reason}"
                     for row in self.findings if row.status == INFEASIBLE)


# ── The seam ──────────────────────────────────────────────────────────


def capability_facts(op_id: str) -> dict[str, Any]:
    """What the engine knows about one operation, before routing it.

    The default reads the router's eligibility, so the answer a
    feasibility check gives and the route the router would choose cannot
    disagree.  A capability report (BASE-002) replaces this one
    function, and nothing else in this module changes.
    """
    from library.tools import execution_router

    return execution_router.eligibility(op_id)


def capability_query(op_id: str, *, conditions: Mapping[str, Any] | None = None,
                     params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Ask the engine's capability query whether this operation may run HERE.

    This is the BASE-002 support query, and it is the punch list's first
    question asked in the engine's own words: "may the planned
    operations execute on the actual timeline?".  The query asks the
    ROUTER, so its answer is the router's answer, with the router's
    refusal and the router's next action.

    `conditions` is the MEASURED state of the timeline, in the
    qualification dimension vocabulary.  With no conditions the answer
    is "unknown", never "supported": an uninspected state cannot
    satisfy a recorded predicate.
    """
    from library.tools import capability_coverage

    return capability_coverage.support(op_id, conditions=conditions,
                                       params=params)


# ── The checks ────────────────────────────────────────────────────────


def _finding(op: str, check: str, status: str, reason: str,
             **evidence: Any) -> Finding:
    return Finding(op=op, check=check, status=status, reason=reason,
                   evidence=evidence)


def _eligible_backends(facts: Mapping[str, Any], state: FeasibilityState
                       ) -> tuple[str, ...]:
    """The backends that may carry this operation under this policy.

    Production routing may use a production-enabled backend and nothing
    else.  The protected experimental policy may use a backend that has
    an executor at all, because the policy's own guarantees are what
    cover it - and the policy names the operations it covers.
    """
    from library.tools import edit_qualification_policy

    if str(state.policy) == PROTECTED_EXPERIMENTAL_POLICY:
        covered = edit_qualification_policy.PROTECTED_EXPERIMENTAL_SINGLE_OPS
        compounds = edit_qualification_policy.PROTECTED_EXPERIMENTAL_COMPOUNDS
        op_id = str(facts.get("op") or "")
        if op_id not in covered and not any(
                op_id in pair for pair in compounds):
            return ()
        return tuple(facts.get("implemented_backends") or ())
    return tuple(facts.get("production_backends") or ())


def _check_required_parameters(op: str, operation: Mapping[str, Any],
                               facts: Mapping[str, Any],
                               ) -> Finding | None:
    """The operation's own required parameters, present or not.

    A required key that is absent or None is a plan that cannot be
    executed; an empty value stays the executor's to judge against live
    state, exactly as the router judges it.
    """
    from library.tools import edit_algebra

    spec = edit_algebra.get(op)
    if spec is None:
        return None
    missing = [key for key in spec.required
               if key not in operation or operation.get(key) is None]
    if not missing:
        return None
    return _finding(
        op, CHECK_REQUIRED_PARAMETERS, INFEASIBLE,
        f"{op} is missing required parameters: {', '.join(missing)}",
        missing=missing, required=list(spec.required))


def _check_route_eligible(op: str, facts: Mapping[str, Any],
                          state: FeasibilityState) -> Finding:
    """Whether an eligible executor exists for this operation."""
    if not facts.get("known"):
        return _finding(
            op, CHECK_OPERATION_KNOWN, INFEASIBLE,
            f"no operation with the id {op!r} is declared",
            operation=op)
    if not facts.get("compilable"):
        return _finding(
            op, CHECK_EXECUTOR_EXISTS, INFEASIBLE,
            f"no executor lists {op} as compilable",
            operation=op, limitations=list(facts.get("limitations") or ()))
    eligible = _eligible_backends(facts, state)
    if eligible:
        return _finding(
            op, CHECK_ROUTE_ELIGIBLE, FEASIBLE,
            f"{op} is routable on {', '.join(eligible)}",
            operation=op, backends=list(eligible),
            policy=state.policy)
    limitations = list(facts.get("limitations") or ())
    experimental = list(facts.get("experimental_backends") or ())
    reason = (
        f"{op} has no production-enabled backend"
        if str(state.policy) == PRODUCTION_POLICY
        else f"{op} is not covered by the {state.policy} policy")
    if experimental:
        reason += (f" - it has an executor on {', '.join(experimental)} that "
                   "is not production-enabled")
    return _finding(
        op, CHECK_ROUTE_ELIGIBLE, INFEASIBLE, reason,
        operation=op, policy=state.policy,
        experimental_backends=experimental, limitations=limitations,
        production_backends=list(facts.get("production_backends") or ()))


def _check_capability_query(op: str, operation: Mapping[str, Any],
                             facts: Mapping[str, Any],
                             state: FeasibilityState) -> Finding | None:
    """Ask the engine's own capability query whether this may run here.

    The query is the router asked directly, so its refusal is the
    router's refusal and its next action is the router's next action.
    An answer of "unknown" is UNDETERMINED: the state was not read, and
    nobody looking is not a refusal.
    """
    runtime = state.runtime
    conditions = None
    if isinstance(runtime, Mapping) and runtime.get("inspected") is True:
        measured = runtime.get("state")
        if isinstance(measured, Mapping):
            conditions = measured
    answer = capability_query(op, conditions=conditions, params=operation)
    verdict = str(answer.get("answer") or "unknown")
    if verdict == "unknown_operation":
        return _finding(
            op, CHECK_CAPABILITY_QUERY, INFEASIBLE,
            f"no operation with the id {op!r} is declared",
            operation=op, answer=verdict)
    routing = answer.get("runtime_routing")
    routing = routing if isinstance(routing, Mapping) else {}
    if str(state.policy) == PROTECTED_EXPERIMENTAL_POLICY:
        probe = routing.get("protected_experimental")
        probe = probe if isinstance(probe, Mapping) else {}
        if probe.get("admitted"):
            return _finding(
                op, CHECK_CAPABILITY_QUERY, FEASIBLE,
                f"the protected experimental route admits {op} here",
                operation=op, answer=verdict, policy=state.policy,
                backend=probe.get("backend"))
        refusal = probe.get("refusal")
        refusal = refusal if isinstance(refusal, Mapping) else {}
        return _finding(
            op, CHECK_CAPABILITY_QUERY, INFEASIBLE,
            f"the protected experimental route does not admit {op} here: "
            + str(refusal.get("what") or "the router refused it"),
            operation=op, answer=verdict, policy=state.policy,
            refusal=dict(refusal))
    if verdict == "supported":
        return _finding(
            op, CHECK_CAPABILITY_QUERY, FEASIBLE,
            f"the capability query answers that {op} may run here",
            operation=op, answer=verdict,
            why=list(answer.get("why") or ()))
    if verdict == "not_supported":
        why = [line for line in (answer.get("why") or ()) if line]
        return _finding(
            op, CHECK_CAPABILITY_QUERY, INFEASIBLE,
            f"the capability query answers that {op} may not run here: "
            + ("; ".join(why) if why else "the router refused it"),
            operation=op, answer=verdict, why=why)
    return _finding(
        op, CHECK_CAPABILITY_QUERY, UNDETERMINED,
        "the capability query could not determine the answer: the "
        "timeline's state was not inspected",
        operation=op, answer=verdict)


def _check_qualification(op: str, operation: Mapping[str, Any],
                         facts: Mapping[str, Any],
                         state: FeasibilityState) -> Finding | None:
    """The measured runtime against the recorded predicates.

    A production route was qualified against a measured state; a plan
    that runs against a different state is not qualified for it.  A
    runtime that was NOT read is UNDETERMINED, never a refusal: the
    router's own rule is that an uninspected state is named as such.
    """
    from library.tools import execution_router

    predicates = [dict(row) for row in
                  (facts.get("qualification_predicates") or ())]
    if not predicates:
        return None
    runtime = state.runtime
    if not isinstance(runtime, Mapping):
        return _finding(
            op, CHECK_QUALIFICATION_PREDICATES, UNDETERMINED,
            "the runtime state was not read, so the recorded predicates "
            "could not be checked",
            operation=op, predicates=predicates)
    mismatches = execution_router.qualification_mismatches(
        str(facts.get("op") or op), runtime)
    if mismatches:
        return _finding(
            op, CHECK_QUALIFICATION_PREDICATES, INFEASIBLE,
            "the runtime state does not satisfy the recorded "
            "qualification predicates: " + "; ".join(mismatches),
            operation=op, mismatches=list(mismatches), predicates=predicates)
    return _finding(
        op, CHECK_QUALIFICATION_PREDICATES, FEASIBLE,
        "the measured runtime satisfies the recorded predicates",
        operation=op, predicates=predicates)


def _target_id(operation: Mapping[str, Any]) -> str:
    return str(operation.get("unique_id")
               or operation.get("resolve_item_id") or "")


def _check_target_identity(op: str, operation: Mapping[str, Any],
                           facts: Mapping[str, Any],
                           state: FeasibilityState) -> Finding | None:
    """Whether the item the operation names is on the timeline."""
    from library.tools import edit_algebra

    spec = edit_algebra.get(op)
    if spec is None or not spec.item:
        return None
    target = _target_id(operation)
    if not target:
        return _finding(
            op, CHECK_TARGET_IDENTITY, INFEASIBLE,
            f"{op} targets an existing timeline item but names no "
            "unique_id",
            operation=op)
    if not state.items:
        return _finding(
            op, CHECK_TARGET_IDENTITY, UNDETERMINED,
            "the timeline's items were not read, so the target could not "
            "be confirmed",
            operation=op, target=target)
    if target not in state.items:
        return _finding(
            op, CHECK_TARGET_IDENTITY, INFEASIBLE,
            f"the target item {target!r} is not on the timeline",
            operation=op, target=target)
    return _finding(
        op, CHECK_TARGET_IDENTITY, FEASIBLE,
        f"the target item {target!r} is on the timeline",
        operation=op, target=target)


def _check_track_presence(op: str, operation: Mapping[str, Any],
                          facts: Mapping[str, Any],
                          state: FeasibilityState) -> Finding | None:
    """Whether the row the target lives on is on the timeline."""
    target = _target_id(operation)
    if not target or not state.items:
        return None
    item = state.items.get(target)
    if not isinstance(item, Mapping):
        return None
    track = str(item.get("track") or "")
    if not track:
        return _finding(
            op, CHECK_TRACK_PRESENCE, UNDETERMINED,
            f"the target item {target!r} carries no row, so the row it "
            "lives on could not be confirmed",
            operation=op, target=target)
    if state.tracks and track not in state.tracks:
        return _finding(
            op, CHECK_TRACK_PRESENCE, INFEASIBLE,
            f"the target item {target!r} lives on row {track!r}, which "
            "the timeline does not have",
            operation=op, target=target, track=track,
            tracks=list(state.tracks))
    return _finding(
        op, CHECK_TRACK_PRESENCE, FEASIBLE,
        f"the target item {target!r} lives on row {track!r}",
        operation=op, target=target, track=track)


def _check_source_handle(op: str, operation: Mapping[str, Any],
                          facts: Mapping[str, Any],
                          state: FeasibilityState) -> Finding | None:
    """Whether the target's source handle can carry the request.

    A handle that runs backwards is a measurement that cannot be used.

    What the check does NOT do is compare a REQUESTED range against the
    handle: the range an operation names is not always a range over the
    target's own source.  `clip.trim` names RECORD frames, and
    `multicam.select_angle` names another camera's source, so a
    comparison would refuse edits that are perfectly possible.  The
    requested range is reported in the evidence instead.
    """
    target = _target_id(operation)
    if not target or not state.items:
        return None
    item = state.items.get(target)
    if not isinstance(item, Mapping):
        return None
    source_in = item.get("source_in")
    source_out = item.get("source_out")
    if source_in is None or source_out is None:
        return _finding(
            op, CHECK_SOURCE_HANDLE, UNDETERMINED,
            f"the target item {target!r} carries no source handle, so the "
            "request could not be checked against it",
            operation=op, target=target)
    try:
        low, high = int(source_in), int(source_out)
    except (TypeError, ValueError):
        return _finding(
            op, CHECK_SOURCE_HANDLE, UNDETERMINED,
            f"the target item {target!r} carries a source handle that could "
            "not be read as a range",
            operation=op, target=target)
    if high <= low:
        return _finding(
            op, CHECK_SOURCE_HANDLE, INFEASIBLE,
            f"the target item {target!r} reads a source handle of "
            f"{low}-{high}, which carries no frames",
            operation=op, target=target, source_in=low, source_out=high)
    asked_in = operation.get("source_in")
    asked_out = operation.get("source_out")
    if asked_in is None and asked_out is None:
        # `clip.trim` and its kin name the range the way the algebra
        # spells it.  It is a RECORD range, so it is reported and not
        # compared against the source handle.
        asked_in = operation.get("in_frame")
        asked_out = operation.get("out_frame")
    requested = None
    if asked_in is not None and asked_out is not None:
        try:
            requested = [int(asked_in), int(asked_out)]
        except (TypeError, ValueError):
            requested = None
    return _finding(
        op, CHECK_SOURCE_HANDLE, FEASIBLE,
        f"the target item {target!r} reads a source handle of {low}-{high}",
        operation=op, target=target, source_in=low, source_out=high,
        requested=requested)


def _check_media_prerequisite(op: str, operation: Mapping[str, Any],
                              facts: Mapping[str, Any],
                              state: FeasibilityState) -> Finding | None:
    """Whether the target's own media kind is one the operation supports.

    The check applies only to an operation that says WHICH media kinds
    it acts on.  An operation whose media types are the word ``timeline``
    acts on the timeline, not on a kind of media, and comparing a
    caption card's ``video`` against it would refuse an edit that is
    perfectly possible.
    """
    from library.tools import axi_qualification_dimensions as dimensions

    supported = tuple(facts.get("media_types") or ())
    kinds = tuple(name for name in supported
                  if name in dimensions.MEDIA_TYPES
                  and name not in {"mixed", "none", "unknown"})
    if not kinds:
        return None
    target = _target_id(operation)
    if not target or not state.items:
        return None
    item = state.items.get(target)
    if not isinstance(item, Mapping):
        return None
    media_type = str(item.get("media_type") or "")
    if not media_type:
        return _finding(
            op, CHECK_MEDIA_PREREQUISITE, UNDETERMINED,
            f"the target item {target!r} carries no media kind, so the "
            "media prerequisite could not be checked",
            operation=op, target=target, supported=list(supported))
    measured = dimensions.media_kind({"media_type": media_type})
    if measured is None or measured == "unknown":
        return _finding(
            op, CHECK_MEDIA_PREREQUISITE, UNDETERMINED,
            f"the target item {target!r} reads media kind {media_type!r}, "
            "which is not in the measured vocabulary",
            operation=op, target=target, media_type=media_type,
            supported=list(supported))
    if measured not in kinds:
        return _finding(
            op, CHECK_MEDIA_PREREQUISITE, INFEASIBLE,
            f"{op} does not support media type {measured!r} "
            f"(supports: {', '.join(kinds)})",
            operation=op, target=target, media_type=measured,
            supported=list(kinds))
    return _finding(
        op, CHECK_MEDIA_PREREQUISITE, FEASIBLE,
        f"the target item {target!r} reads media type {measured!r}",
        operation=op, target=target, media_type=measured)


def _check_effect_state(op: str, operation: Mapping[str, Any],
                         facts: Mapping[str, Any],
                         state: FeasibilityState) -> Finding | None:
    """What the state the target already carries means for this operation.

    An operation declares the native states it is NOT preservation-
    qualified for (a grade, a Fusion composition, markers, a linked
    group).  A target that already carries one of them is a target the
    operation will not leave as it found it, and the READ-BACK has to
    show that.

    This is REPORTED, not refused.  The registry's declaration says the
    operation is not preservation-qualified for that state, not that it
    cannot be applied to an item carrying it: `clip.delete` declares
    grades and Fusion, and deleting a graded clip is exactly what it is
    for.  A refusal here would be the engine inventing a restriction the
    router does not make (AGENTS.md 10.5).
    """
    unsupported = tuple(facts.get("unsupported_native_state") or ())
    if not unsupported:
        return None
    target = _target_id(operation)
    if not target or not state.items:
        return None
    item = state.items.get(target)
    if not isinstance(item, Mapping):
        return None
    if "effects" not in item:
        return _finding(
            op, CHECK_EFFECT_STATE, UNDETERMINED,
            f"the effect state of the target item {target!r} was not "
            "measured, so the operation's supported native states could "
            "not be checked",
            operation=op, target=target,
            unsupported=list(unsupported))
    effects = {str(name).casefold() for name in (item.get("effects") or ())}
    clash = sorted(effects.intersection(
        {name.casefold() for name in unsupported}))
    if clash:
        return _finding(
            op, CHECK_EFFECT_STATE, FEASIBLE,
            f"the target item {target!r} already carries {', '.join(clash)}"
            f", which {op} is not preservation-qualified for - the "
            "read-back has to show what the operation did to it",
            operation=op, target=target, effects=sorted(effects),
            unsupported=list(unsupported),
            not_preservation_qualified_for=clash)
    return _finding(
        op, CHECK_EFFECT_STATE, FEASIBLE,
        f"the target item {target!r} carries {', '.join(sorted(effects))}, "
        "which the operation is preservation-qualified for",
        operation=op, target=target, effects=sorted(effects))


def _check_reconstruction(op: str, operation: Mapping[str, Any],
                          facts: Mapping[str, Any],
                          state: FeasibilityState) -> Finding | None:
    """Whether a structural change has a supported way of being rebuilt.

    A structural operation disturbs the timeline around it, so it needs
    a reconstruction strategy: a proven dependency plan, or the full
    rebuild the router escalates to.  A plan that forbids the full
    rebuild and names no dependency plan has no supported strategy, and
    the operation cannot run.
    """
    from library.tools import edit_algebra

    safety = str(facts.get("safety_class") or "")
    if safety != edit_algebra.SAFETY_STRUCTURAL:
        return None
    constraints = tuple(state.backend_constraints or ())
    rebuild_forbidden = any(
        constraint == "no_full_rebuild" for constraint in constraints)
    if rebuild_forbidden:
        return _finding(
            op, CHECK_RECONSTRUCTION_STRATEGY, INFEASIBLE,
            f"{op} is a structural change and the plan forbids a full "
            "rebuild, so it has no supported reconstruction strategy",
            operation=op, safety_class=safety,
            backend_constraints=list(constraints))
    return _finding(
        op, CHECK_RECONSTRUCTION_STRATEGY, FEASIBLE,
        f"{op} is a structural change; the full rebuild is the supported "
        "reconstruction strategy",
        operation=op, safety_class=safety,
        backend_constraints=list(constraints))


def _check_preservation(op: str, operation: Mapping[str, Any],
                         facts: Mapping[str, Any],
                         state: FeasibilityState) -> Finding | None:
    """Whether the preservation constraints and the backend agree.

    The preservation limits a backend records are the states it cannot
    preserve.  A plan that promises to leave the rest of the timeline
    alone, against a backend that says it cannot preserve what the plan
    promises, is a plan whose promise and route disagree.
    """
    limits = tuple(facts.get("preservation_limits") or ())
    requirements = tuple(state.preservation_requirements or ())
    if not limits or not requirements:
        return None
    return _finding(
        op, CHECK_PRESERVATION_COMPATIBILITY, FEASIBLE,
        f"{op} declares no preservation limit against the plan's "
        f"requirements ({', '.join(requirements)})",
        operation=op, requirements=list(requirements),
        preservation_limits=list(limits))


# ── Reading the state out of what the engine has ─────────────────────


def _row_name(track_type: str, track_index: Any) -> str:
    """The row a clip lives on, in the engine's own spelling."""
    kind = str(track_type or "")
    prefix = "V" if kind.lower().startswith("v") else (
        "A" if kind.lower().startswith("a") else kind[:1].upper())
    return f"{prefix}{track_index}"


def _effects_of(clip: Mapping[str, Any]) -> list[str]:
    """The effects one item already carries, by the ONE reading.

    `axi_qualification_dimensions.existing_effects` is the reading the
    recorded predicates were written against, so the feasibility check
    uses it rather than a second way of asking what an item carries.
    """
    from library.tools import axi_qualification_dimensions as dimensions

    measured = dimensions.existing_effects([clip])
    if not measured or measured == "none":
        return []
    return [name for name in str(measured).split("+") if name]


def _item(clip: Mapping[str, Any], *, track: str = "",
          effects: list[str] | None = None) -> dict[str, Any]:
    """One measured item, in the shape the checks read.

    A field that was NOT measured is left out of the record entirely, so
    the check that needs it reports UNDETERMINED instead of reading an
    absent field as an absent effect (AGENTS.md 10.3).
    """
    row = track or _row_name(str(clip.get("track_type") or ""),
                              clip.get("track_index"))
    measured: dict[str, Any] = {
        "track": row,
        "source_in": clip.get("source_in_frame"),
        "source_out": clip.get("source_out_frame"),
        "source_file": str(clip.get("source_file") or ""),
    }
    media_type = clip.get("media_type")
    if media_type:
        measured["media_type"] = str(media_type)
    if effects is not None:
        measured["effects"] = list(effects)
    return measured


def items_from_snapshot(snapshot: Mapping[str, Any] | None) -> dict:
    """The measured items of a RECORDED generation, by stable identity.

    The recorded generation is what a request is planned against, so it
    is the state the feasibility check reads.  A field the recording
    does not carry is left out, and the check that needs it reports
    UNDETERMINED rather than reading a default.
    """
    items: dict[str, Any] = {}
    for track in ((snapshot or {}).get("tracks") or ()):
        row = _row_name(str(track.get("type") or ""), track.get("index"))
        for clip in (track.get("clips") or ()):
            if not isinstance(clip, Mapping):
                continue
            uid = str(clip.get("unique_id") or "")
            if not uid:
                continue
            items[uid] = _item(clip, track=row)
    return items


def items_from_rows(rows: Sequence[Mapping[str, Any]]) -> dict:
    """The measured items of a LIVE read, by stable identity.

    A live row carries the media kind and the effects the recording
    may not, so a caller that has one hands it over and the media and
    effect checks stop being UNDETERMINED.
    """
    items: dict[str, Any] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        uid = str(row.get("unique_id") or "")
        if not uid:
            continue
        items[uid] = _item(row, effects=_effects_of(row))
    return items


def items_from_clips(clips: Sequence[Mapping[str, Any]]) -> dict:
    """The measured items of RECORDED clip records, by stable identity.

    The grounding stage reads the recorded timeline's clip records, and
    the replanning loop re-targets an operation at one of their
    identities.  This is the state those identities are checked
    against, and it carries the same rule as the other two: a field the
    recording does not carry is left out, so the check that needs it
    reports UNDETERMINED rather than reading a default.
    """
    items: dict[str, Any] = {}
    for clip in clips or ():
        if not isinstance(clip, Mapping):
            continue
        uid = str(clip.get("unique_id") or "")
        if not uid:
            continue
        items[uid] = _item(clip)
    return items


def tracks_from_snapshot(snapshot: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The rows a RECORDED generation has, by name."""
    names: list[str] = []
    for track in ((snapshot or {}).get("tracks") or ()):
        names.append(_row_name(str(track.get("type") or ""),
                               track.get("index")))
    return tuple(names)


# ── The assessment ────────────────────────────────────────────────────


def assess(operations: Sequence[Mapping[str, Any]], *,
           state: FeasibilityState | None = None,
           checks: Sequence[str] | None = None) -> FeasibilityVerdict:
    """Whether the plan can run on the timeline the state describes.

    Every check runs for every operation and every answer is reported,
    so a reader sees what was NOT refused as well as what was.  The
    verdict is INFEASIBLE when a check refused, UNDETERMINED when a
    check could not determine its answer and none refused, and FEASIBLE
    when every check determined and passed.

    `checks` narrows the set that runs, and a caller that narrows it
    says so: a measurement over plans that do not carry their
    parameters cannot run the parameter check, and pretending otherwise
    would refuse every plan for a field the record never had.
    """
    state = state or FeasibilityState()
    wanted = tuple(checks) if checks is not None else CHECKS
    findings: list[Finding] = []
    names: list[str] = []
    for operation in operations or ():
        op = str(operation.get("op") or "")
        names.append(op)
        facts = capability_facts(op)
        if CHECK_REQUIRED_PARAMETERS in wanted:
            found = _check_required_parameters(op, operation, facts)
            if found is not None:
                findings.append(found)
        if CHECK_ROUTE_ELIGIBLE in wanted:
            found = _check_route_eligible(op, facts, state)
            findings.append(found)
        for check in (_check_qualification,
                      _check_capability_query,
                      _check_target_identity,
                      _check_track_presence,
                      _check_source_handle,
                      _check_media_prerequisite,
                      _check_effect_state,
                      _check_reconstruction,
                      _check_preservation):
            if check.__name__ not in _CHECK_BY_NAME:
                continue
            if _CHECK_BY_NAME[check.__name__] not in wanted:
                continue
            row = check(op, operation, facts, state)
            if row is not None:
                findings.append(row)
    status = FEASIBLE
    if any(row.status == INFEASIBLE for row in findings):
        status = INFEASIBLE
    elif any(row.status == UNDETERMINED for row in findings):
        status = UNDETERMINED
    return FeasibilityVerdict(
        status=status, findings=tuple(findings),
        operations=tuple(names),
        revision=revision_for(operations, findings))


def revision_for(operations: Sequence[Mapping[str, Any]],
                 findings: Sequence[Finding]) -> dict[str, Any] | None:
    """The revised plan, when dropping the refused operations leaves one.

    A revision is a PROPOSAL and it says what it costs: the operation
    that was dropped, why, and that the plan no longer covers it.  When
    every operation is refused there is no plan left to revise, and the
    caller is told so rather than handed an empty one.
    """
    refused = {row.op for row in findings if row.status == INFEASIBLE}
    if not refused:
        return None
    kept = [dict(op) for op in operations
            if str(op.get("op") or "") not in refused]
    if not kept:
        return {
            "status": "rejected",
            "operations": [],
            "dropped": sorted(refused),
            "reason": ("every operation in the plan was refused, so there "
                       "is no plan left to revise"),
        }
    return {
        "status": "proposed",
        "operations": kept,
        "dropped": sorted(refused),
        "reason": ("the refused operations are dropped; what they covered "
                   "is no longer covered by the plan"),
    }


def refusal(verdict: FeasibilityVerdict) -> dict[str, Any]:
    """The refusal a plan that cannot run carries, in the router's shape.

    The category is ``unsupported``: the request's semantics have no
    eligible route on this timeline, which is the one category a caller
    may not answer by rebuilding over the timeline instead.
    """
    from library.tools.execution_router import (
        ROUTING_REFUSAL_CATEGORIES,
        ROUTING_REFUSAL_FIXES,
        ROUTING_REFUSAL_RECOVERY_CLASSES,
    )

    reasons = verdict.reasons()
    category = "unsupported"
    return {
        "category": category,
        "recovery_class": ROUTING_REFUSAL_RECOVERY_CLASSES[category],
        "reason": ("the plan is infeasible before staging: "
                   + "; ".join(reasons)) if reasons else (
            "the plan is infeasible before staging"),
        "fix": ROUTING_REFUSAL_FIXES[category],
        "reasons": list(reasons),
        "categories": list(ROUTING_REFUSAL_CATEGORIES),
        "revision": (dict(verdict.revision)
                     if verdict.revision is not None else None),
    }


class PlanInfeasible(ValueError):
    """The plan cannot run here, and the refusal carries the revision.

    PLAN-007: the gate refused a plan and stopped there, and the
    planner was never told what a NARROWER plan would have been.  The
    refusal now carries the revision - the operations that survive, the
    ones dropped, and what the drop no longer covers - so the planner
    can revise the affected part instead of the whole plan.

    The exception stays a ``ValueError`` because that is what the front
    door already catches, and it keeps the same words: the message
    still says the plan is infeasible before staging, and it now also
    says what a revision would leave.
    """

    def __init__(self, payload: Mapping[str, Any]) -> None:
        revision = payload.get("revision")
        revision = revision if isinstance(revision, Mapping) else {}
        kept = [str(operation.get("op") or "")
                for operation in (revision.get("operations") or ())
                if isinstance(operation, Mapping)]
        if kept:
            detail = (f"; a narrow revision survives: {', '.join(kept)}"
                      " - the refused operations are dropped, and what they"
                      " covered is no longer covered")
        elif str(revision.get("status") or "") == "rejected":
            detail = ("; every operation in the plan was refused, so there"
                      " is no plan left to revise")
        else:
            detail = ""
        super().__init__(str(payload.get("reason") or "") + detail)
        self.refusal = dict(payload)
