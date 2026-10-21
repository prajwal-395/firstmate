"""The ONE capability coverage report for the edit vocabulary, and the
read-only query that answers "can this edit run on this timeline?".

BASE-002 asked for a single report over the eighty-four algebra
operations, and for a question Ren can answer BEFORE it attempts an
edit.  Both are here, and both are GENERATED:

* the operation rows come from `edit_algebra.OP_STATUS` - the registry
  that already owns "what exists, which backend declares it, and what
  evidence qualifies it";
* the dimension coverage comes from the qualification registry
  (`library/tools/axi_qualification_records.json`), read through
  `axi_qualification.load_qualification_records`, so "which states were
  measured" is the record's own coverage and never a list written here;
* the ROUTING answers come from `execution_router` itself - the report
  asks the router what it would do and reports what it said, so the
  report cannot disagree with the router that refuses the write.

Nothing in this module declares an operation, a backend, a
qualification or a route.  A second roster is exactly the defect
`library/tools/operation_map.py` exists to prevent, and this file sits
BESIDE that map rather than beside it in spirit: the map answers "what
does this operation MEAN and how is it reached", and this one answers
"what is covered, what is missing, and may this run here".

TWO QUALIFICATIONS, KEPT APART
------------------------------
An operation can be QUALIFIED (live evidence exists, under recorded
predicates) and still not be ROUTABLE on the timeline in front of you
(the inspected state does not satisfy those predicates).  Those are two
different facts, and the report keeps them apart:

* `operation_qualification` is the static fact, read off OP_STATUS;
* `runtime_routing` is what the router would do with the state in
  front of it, and it says "unknown" - never "supported" - when no
  state was inspected.

An UNKNOWN state is not a pass.  A state nobody read cannot satisfy a
predicate, and the router refuses it; the report says so.

ALTERNATIVES, INCLUDING THE REBUILD
-----------------------------------
An operation the router refuses may still have another way to happen.
The report asks the router about each way it knows - the production
route, the protected experimental route, and the full rebuild - and
records the answer with the router's own words.  A rebuild is NOT a
way around a qualification gap: `execution_router` preflights the
rebuild route with the same qualification policy, so an unqualified
operation is refused by the rebuild too, and the report says that
rather than implying a fallback exists.

FREQUENCY, AND WHAT IT IS NOT
-----------------------------
The punchlist asked for prioritization by "real editorial frequency and
impact rather than operation count".  The engine keeps no telemetry of
completed edits, so the honest measurement of editorial demand is the
SHIPPED PIPELINE'S OWN REQUEST SITES: the production call sites that
construct the operation.  That is a measurement of how often the
editorial loop asks for an operation, not a count of edits that
happened, and the report says which one it is.  The vocabulary
declaration (`edit_algebra.py`) and this report are excluded, so
declaring an operation never counts as asking for one.

`tests/unit/resolve/test_capability_coverage.py`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import cache
from pathlib import Path

from library.tools import (
    axi_qualification,
    edit_algebra,
    execution_router,
    operation_map,
)
from library.tools import axi_qualification_dimensions as dimensions

#: The schema the coverage report is published under.
SCHEMA = "ren-capability-coverage/v1"

#: The schema the support answer is published under.
SUPPORT_SCHEMA = "ren-capability-support/v1"

#: The repository root, for the frequency scan.
ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "library"

#: Files whose operation literals are the VOCABULARY or a qualification
#: harness - not the editorial loop asking for an operation.
_FREQUENCY_EXCLUDED = frozenset({
    # the vocabulary declaration: every operation is named here once
    "library/tools/edit_algebra.py",
    # the map over the vocabulary
    "library/tools/operation_map.py",
    # this report
    "library/tools/capability_coverage.py",
    # a live qualification harness, not an editorial request
    "library/tools/qualification_workflow.py",
})

#: The two ways production code constructs an operation.
_OP_LITERAL_PATTERNS = (
    re.compile(r"""["']op["']\s*:\s*["']([A-Za-z_][\w.]*)["']"""),
    re.compile(r"""\bop\s*=\s*["']([A-Za-z_][\w.]*)["']"""),
)

#: The impact tiers, and what each one is worth in the priority score.
#:
#: The tier is DERIVED from the algebra's own safety class and state
#: domain - it is not an opinion written here.  A structural operation
#: changes the timeline's structure, so an unqualified one is a hole in
#: the reel itself; a property mutation is one value on one clip.
IMPACT_WEIGHTS: dict[str, int] = {
    "high": 3,
    "medium": 2,
    "low": 1,
    "none": 0,
}


# ── The measured request sites ─────────────────────────────────────────


@cache
def request_sites() -> dict[str, tuple[str, ...]]:
    """Every production call site that constructs each operation.

    Read from the shipped pipeline's own source, so the demand an
    operation carries is the demand the CODE expresses.  The vocabulary
    declaration, the operation map, this report and the qualification
    harness are excluded: a file that NAMES an operation does not ask
    for it.

    Returned as `op id -> ("path:line", ...)`, one entry per site.
    """
    sites: dict[str, list[str]] = {op_id: [] for op_id in edit_algebra.OP_STATUS}
    if not LIBRARY.is_dir():
        return {op_id: () for op_id in sites}
    for path in sorted(LIBRARY.rglob("*.py")):
        try:
            relative = path.relative_to(ROOT).as_posix()
        except ValueError:  # pragma: no cover - a file outside the root
            continue
        if relative in _FREQUENCY_EXCLUDED:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable
            continue
        for pattern in _OP_LITERAL_PATTERNS:
            for match in pattern.finditer(text):
                op_id = match.group(1)
                if op_id not in sites:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                sites[op_id].append(f"{relative}:{line}")
    return {op_id: tuple(sorted(set(entries)))
            for op_id, entries in sites.items()}


# ── The qualification registry, read for coverage ─────────────────────


@cache
def qualification_rows() -> dict[tuple[str, str], dict]:
    """The evidence-complete qualification rows, by operation and backend."""
    return axi_qualification.load_qualification_records()


@cache
def _incomplete_rows() -> dict[str, tuple[str, ...]]:
    """The rows that are present but NOT evidence-complete, by key.

    A row the registry holds but the contract cannot keep is still a
    fact about the operation, and hiding it would make an operation look
    unqualified for no stated reason.
    """
    audit = axi_qualification.audit()
    return {row["key"]: tuple(row.get("problems") or ())
            for row in audit.get("rows") or ()
            if not row.get("production_eligible")}


def dimension_coverage(record: Mapping | None) -> dict:
    """What the evidence measured, per qualification dimension.

    A dimension is COVERED when the evidence exercised every value its
    vocabulary can take; it is PARTIAL when it exercised some; it is
    MISSING when it exercised none.  The vocabulary is the dimension
    enumeration's own, so "untested" is computed against what the
    dimension can measure rather than against the record's own list.
    """
    covered: list[str] = []
    partial: dict[str, dict] = {}
    missing: list[str] = []
    coverage = dict((record or {}).get("coverage") or {})
    measured = dict(coverage.get("dimensions") or {})
    for dimension in dimensions.DIMENSIONS:
        tested = [str(value) for value in (measured.get(dimension) or ())]
        vocabulary = dimensions.vocabulary(dimension)
        if not tested:
            missing.append(dimension)
            continue
        if vocabulary is None:
            # A dimension with an open vocabulary (the Resolve version)
            # is covered by the version the evidence names.
            covered.append(dimension)
            continue
        untested = [value for value in vocabulary if value not in tested]
        if untested:
            partial[dimension] = {"tested": tested, "untested": untested}
        else:
            covered.append(dimension)
    return {
        "covered": covered,
        "partial": partial,
        "missing": missing,
        "source": coverage.get("source"),
        "configurations": list(coverage.get("configurations") or ()),
    }


# ── The impact an operation carries ───────────────────────────────────


def impact_tier(status) -> tuple[str, tuple[str, ...]]:
    """The impact tier of one operation, and why.

    Derived from the algebra's safety class and state domain: a
    structural operation rewrites the reel's structure, a localized one
    changes a clip, a reversible mutation changes a value, and a plan
    operation writes nothing to the timeline at all.
    """
    safety = status.safety_class
    domain = status.state_domains[0] if status.state_domains else ""
    if safety == edit_algebra.SAFETY_STRUCTURAL:
        return "high", ("structural class: the operation rewrites the "
                        "timeline's structure")
    if safety == edit_algebra.SAFETY_COMPLEX_LOCALIZED:
        return "medium", ("localized class: the operation changes one "
                          "clip's state")
    if safety == edit_algebra.SAFETY_REVERSIBLE_MUTATION:
        return "low", ("reversible mutation: the operation changes one "
                       "value on one item")
    if safety in (edit_algebra.SAFETY_READ_ONLY, edit_algebra.SAFETY_NO_OP):
        return "none", ("the operation delivers a plan and writes nothing "
                        "to the timeline")
    if domain == "timeline_structure":
        return "high", ("no executor proves this structural operation, so "
                        "the edit it names cannot be made at all")
    return "medium", ("no executor proves this operation, so the edit it "
                      "names cannot be made at all")


# ── The router, asked ─────────────────────────────────────────────────


def _probe_operation(op_id: str,
                     params: Mapping | None = None) -> dict:
    """A probe request for one operation.

    The required parameters are filled with placeholders: the report
    answers whether the OPERATION is supported here, and a request that
    has not spelled out its parameters is not a statement about the
    operation.  A caller that has real parameters passes them in.
    """
    spec = edit_algebra.get(op_id)
    operation: dict = {"op": op_id}
    if spec is not None:
        for key in spec.required:
            operation[key] = "probe"
        if spec.item:
            operation.setdefault("unique_id", "probe-item")
    if params:
        operation.update({str(key): value for key, value in params.items()})
    return operation


def _route_probe(op_id: str, context: Mapping,
                 params: Mapping | None = None) -> dict:
    """What the ROUTER would do with this operation, in this context.

    The router is the authority, so it is asked rather than
    reimplemented: a refusal here is the router's own refusal, with
    its what/why/fix.
    """
    operation = _probe_operation(op_id, params)
    try:
        decision = execution_router.route_compiled_edit([operation],
                                                         context=dict(context))
    except execution_router.RoutingRefused as refused:
        return {"admitted": False,
                "refusal": {
                    "what": refused.what,
                    "why": refused.why if hasattr(refused, "why") else str(refused),
                    "fix": refused.fix,
                    "limitations": list(refused.limitations or ()),
                    "category": getattr(refused, "category", None),
                }}
    except Exception as exc:  # noqa: BLE001 - a probe must not crash a report
        return {"admitted": False,
                "refusal": {"what": f"the router could not answer: "
                                    f"{type(exc).__name__}: {exc}",
                            "why": "", "fix": "", "limitations": [],
                            "category": None}}
    return {
        "admitted": True,
        "backend": decision.backend,
        "mode": decision.mode,
        "reasons": list(decision.reasons or ()),
        "rejected_alternatives": [
            {"backend": item.backend, "reason": item.reason}
            for item in (decision.rejected_alternatives or ())],
        "verification": list(decision.verification or ()),
    }


def _routing_contexts(conditions: Mapping | None) -> dict[str, dict]:
    """The contexts the report asks the router about."""
    production: dict = {"qualification_policy": "production"}
    protected: dict = {
        "qualification_policy": "protected_experimental",
        "protected_staging": True,
        "preserve_approved_state": True,
        "verify_after_write": True,
    }
    rebuild: dict = {"qualification_policy": "production",
                     "requested_backend": "full_rebuild"}
    if conditions:
        # The qualification dimensions are the STATE's vocabulary; the
        # router's own request-level `media_type` is the REQUEST's
        # vocabulary (what kind of item the caller is editing).  They
        # share a name and mean different things, so the state's
        # `media_type` is NOT handed to the router as the request's.
        # The state's media type is checked where it belongs - against
        # the recorded qualification predicates.
        state = {"inspected": True, "state": dict(conditions)}
        production["qualification_state"] = state
        protected["qualification_state"] = state
        rebuild["qualification_state"] = state
    return {"production": production,
            "protected_experimental": protected,
            "full_rebuild": rebuild}


# ── The report ────────────────────────────────────────────────────────


def _backend_row(op_id: str, backend) -> dict:
    """One backend's coverage for one operation."""
    record = qualification_rows().get((op_id, backend.backend))
    coverage = dimension_coverage(record)
    incomplete = _incomplete_rows().get(f"{op_id}::{backend.backend}")
    implemented = operation_map.implemented_coverage().get(op_id, {})
    return {
        "backend": backend.backend,
        "declared_by_registry": True,
        "compilable": backend.compilable,
        "implementation_status": backend.implementation_status,
        "implemented": backend.backend in implemented,
        "implementation_mechanism": implemented.get(backend.backend),
        "implementation_evidence": list(backend.implementation_evidence),
        "qualification_status": backend.qualification_status,
        "qualified": backend.qualified,
        "production_enabled": backend.production_enabled,
        "qualification_evidence": list(backend.qualification_evidence),
        "resolve_version": backend.resolve_version,
        "tested_media_types": list(backend.qualification_media_types),
        "timeline_conditions": list(
            backend.qualification_timeline_conditions),
        "qualification_predicates": [dict(predicate) for predicate
                                     in backend.qualification_predicates],
        "preservation_limits": list(backend.preservation_limits),
        "preservation_checks": list((record or {}).get("preservation_checks")
                                    or ()),
        "visual_verification": list(backend.visual_verification),
        "audio_verification": list(backend.audio_verification),
        "recovery": backend.recovery_qualification,
        "semantic_boundary": dict((record or {}).get("semantic_boundary")
                                   or {}),
        "environment": dict((record or {}).get("environment") or {}),
        "limitations": list(backend.limitations),
        "qualification_dimensions": coverage,
        "record_problems": list(incomplete or ()),
    }


def _undeclared_backend_row(op_id: str, backend: str) -> dict:
    """A backend that dispatches the operation but is not in the registry.

    The dispatch tables and the registry are different things, and a
    backend that can run an operation the registry does not list is a
    FINDING: it is implemented, and it carries no qualification, and
    nothing in the registry speaks for it.
    """
    mechanism = operation_map.implemented_coverage().get(op_id, {}).get(backend)
    return {
        "backend": backend,
        "declared_by_registry": False,
        "compilable": False,
        "implementation_status": "implemented_without_registry_row",
        "implemented": True,
        "implementation_mechanism": mechanism,
        "implementation_evidence": [],
        "qualification_status": "not_declared_by_registry",
        "qualified": False,
        "production_enabled": False,
        "qualification_evidence": [],
        "resolve_version": None,
        "tested_media_types": [],
        "timeline_conditions": [],
        "qualification_predicates": [],
        "preservation_limits": [],
        "preservation_checks": [],
        "visual_verification": [],
        "audio_verification": [],
        "recovery": "not live-qualified",
        "semantic_boundary": {},
        "environment": {},
        "limitations": [
            ("the registry declares no qualification row for this backend "
             "on this operation")],
        "qualification_dimensions": dimension_coverage(None),
        "record_problems": [],
    }


def operation_row(op_id: str, *,
                  conditions: Mapping | None = None) -> dict:
    """The coverage row of one operation."""
    status = edit_algebra.status(op_id)
    if status is None:
        raise KeyError(f"unknown operation {op_id!r}")
    spec = edit_algebra.ALGEBRA[op_id]
    contexts = _routing_contexts(conditions)
    routes = {name: _route_probe(op_id, context)
              for name, context in contexts.items()}
    backends = [_backend_row(op_id, backend) for backend in status.backends]
    declared = {backend["backend"] for backend in backends}
    for backend in sorted(operation_map.implemented_coverage().get(op_id, {})):
        if backend not in declared:
            backends.append(_undeclared_backend_row(op_id, backend))
    tier, reasons = impact_tier(status)
    sites = request_sites().get(op_id, ())
    missing_dimensions: list[str] = []
    partial_dimensions: list[str] = []
    covered_dimensions: list[str] = []
    for dimension in dimensions.DIMENSIONS:
        states = [backend["qualification_dimensions"] for backend in backends]
        if any(dimension in state["covered"] for state in states):
            covered_dimensions.append(dimension)
        elif any(dimension in state["partial"] for state in states):
            partial_dimensions.append(dimension)
        else:
            missing_dimensions.append(dimension)
    return {
        "id": op_id,
        "domain": status.state_domains[0] if status.state_domains else "",
        "purpose": spec.note or f"{op_id.replace('.', ' ')} operation.",
        "implementation_status": status.implementation_status,
        "operation_qualification": {
            "status": status.qualification_status,
            "qualified": status.qualified,
            "production_enabled": status.production_enabled,
            "compilable": status.compilable,
            "backends": backends,
        },
        "supported_states": {
            "media_types": list(status.media_types),
            "timeline_conditions": list(status.timeline_conditions),
            "required_resolve_capabilities": list(
                status.required_resolve_capabilities),
            "verification_contract": status.verification_contract,
            "inverse_recovery": dict(status.inverse_recovery),
        },
        "qualification_dimensions": {
            "covered": covered_dimensions,
            "partially_covered": partial_dimensions,
            "missing": missing_dimensions,
        },
        "runtime_routing": routes,
        "alternatives": {
            "declared_backends": [backend["backend"] for backend in backends
                                  if backend["declared_by_registry"]],
            "implemented_backends": [backend["backend"] for backend in backends
                                     if backend["implemented"]],
            "unqualified_backends": [backend["backend"] for backend in backends
                                     if not backend["production_enabled"]],
            "production_route": routes["production"],
            "protected_route": routes["protected_experimental"],
            "rebuild_route": routes["full_rebuild"],
            "same_domain_operations": sorted(
                other for other, other_status in edit_algebra.OP_STATUS.items()
                if other != op_id
                and other_status.state_domains
                and other_status.state_domains[0] == status.state_domains[0]),
        },
        "priority": {
            "editorial_request_sites": list(sites),
            "editorial_request_count": len(sites),
            "impact_tier": tier,
            "impact_reasons": list(reasons),
            "impact_weight": IMPACT_WEIGHTS[tier],
            "priority_score": len(sites) * IMPACT_WEIGHTS[tier],
        },
    }


@cache
def coverage_report() -> dict:
    """The whole coverage report, generated from the code as it stands.

    Returned as plain data: a consumer reads it, never calls into it.
    """
    rows = {op_id: operation_row(op_id) for op_id in sorted(edit_algebra.OP_STATUS)}
    priority = sorted(
        rows.values(),
        key=lambda row: (-row["priority"]["priority_score"],
                         -row["priority"]["impact_weight"],
                         -row["priority"]["editorial_request_count"],
                         row["id"]))
    for rank, row in enumerate(priority, start=1):
        row["priority"]["rank"] = rank
    levels: dict[str, list[str]] = {}
    for row in rows.values():
        levels.setdefault(row["operation_qualification"]["status"],
                          []).append(row["id"])
    gaps = [row["id"] for row in priority
            if not row["operation_qualification"]["production_enabled"]]
    return {
        "schema": SCHEMA,
        "generated_by": "library/tools/capability_coverage.py",
        "sources": [
            "library/tools/edit_algebra.py (OP_STATUS)",
            "library/tools/execution_router.py (routing answers)",
            "library/tools/axi_qualification_records.json (evidence coverage)",
        ],
        "operation_count": len(rows),
        "qualification_status_counts": {
            status: len(ids) for status, ids in sorted(levels.items())},
        "production_enabled_operations": sorted(
            row["id"] for row in rows.values()
            if row["operation_qualification"]["production_enabled"]),
        "operations_refused_by_the_production_route": sorted(
            row["id"] for row in rows.values()
            if not row["runtime_routing"]["production"]["admitted"]),
        "production_route_note": (
            "the production route is asked with NO inspected state, so a "
            "promoted operation is refused for want of an inspection and "
            "an unqualified one for want of evidence; `support` answers "
            "the same question against the state in front of you"),
        "qualification_gap_priority": gaps,
        "priority_note": (
            "the priority order is the measured editorial demand (the "
            "shipped pipeline's own request sites) times the impact tier "
            "the algebra's safety class gives the operation; the gap "
            "priority is the same order over the operations that are NOT "
            "production-enabled"),
        "priority": [row["id"] for row in priority],
        "operations": rows,
    }


# ── The read-only query ───────────────────────────────────────────────


def support(op_id: str, *, conditions: Mapping | None = None,
            params: Mapping | None = None) -> dict:
    """Answer whether one operation may run on a timeline in these
    conditions, and say what prevents it when it may not.

    `conditions` is the measured state of the timeline, in the
    qualification dimension vocabulary
    (`library/tools/axi_qualification_dimensions.py`).  With no
    conditions the answer is "unknown", never "supported": an
    uninspected state cannot satisfy a recorded predicate, and the
    router refuses it.
    """
    status = edit_algebra.status(op_id)
    if status is None:
        return {
            "schema": SUPPORT_SCHEMA,
            "operation": op_id,
            "known": False,
            "answer": "unknown_operation",
            "why": [f"no algebra operation is declared for {op_id!r}"],
        }
    contexts = _routing_contexts(conditions)
    production = _route_probe(op_id, contexts["production"], params)
    protected = _route_probe(op_id, contexts["protected_experimental"], params)
    rebuild = _route_probe(op_id, contexts["full_rebuild"], params)
    if conditions is None:
        eligibility = "unknown"
        answer = "unknown"
        why = [("the timeline's state was not inspected, so the recorded "
                "qualification predicates cannot be satisfied or refused")]
    elif production["admitted"]:
        eligibility = "eligible"
        answer = "supported"
        why = list(production.get("reasons") or ())
    else:
        eligibility = "not_eligible"
        answer = "not_supported"
        refusal = production["refusal"]
        why = [refusal.get("what", ""), refusal.get("why", ""),
               refusal.get("fix", "")]
    return {
        "schema": SUPPORT_SCHEMA,
        "operation": op_id,
        "known": True,
        "answer": answer,
        "runtime_eligibility": eligibility,
        "why": [line for line in why if line],
        "conditions": dict(conditions) if conditions else None,
        "operation_qualification": {
            "status": status.qualification_status,
            "qualified": status.qualified,
            "production_enabled": status.production_enabled,
            "note": ("the operation is qualified SOMEWHERE; whether it may "
                     "run HERE is the runtime answer above"),
        },
        "runtime_routing": {
            "production": production,
            "protected_experimental": protected,
            "full_rebuild": rebuild,
        },
        "alternatives": {
            "production_route_admitted": production["admitted"],
            "protected_route_admitted": protected["admitted"],
            "rebuild_route_admitted": rebuild["admitted"],
            "rebuild_route_refusal": (
                rebuild.get("refusal", {}).get("what")
                if not rebuild["admitted"] else None),
            "unqualified_backends": [backend.backend for backend in
                                     status.backends
                                     if not backend.production_enabled],
        },
    }
