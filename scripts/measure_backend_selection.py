#!/usr/bin/env python3
"""Measure how many places choose an execution backend, and how often two
of them would have chosen differently.

AXI-002: "One semantic request has one auditable routing decision."  The
measurement is the two numbers the punchlist names:

1. the NUMBER OF SELECTION SITES - the places in the engine that DECIDE
   which execution backend runs a request;
2. the COUNT OF RECORDED REQUESTS where two sites would have chosen
   differently, BEFORE this lane and AFTER it, over the receipts
   recorded today.

Both are computed over the SAME corpus, so the numbers are comparable.
The corpus is:

* the machine broker's own job records (`timeline.semantic_edit` jobs),
  read READ-ONLY from the broker database; and
* the recorded `ren revise` receipts in a projects root, when the caller
  names that root.

Every measurement carries its OWN denominator, and a measurement nobody
could take is reported as unmeasured - never as zero.

The BEFORE sites are modelled here explicitly: they are the sets this
lane deleted, with the file and the line they were on, so the
before/after comparison is computed rather than asserted.

Usage:
    python3 scripts/measure_backend_selection.py [--projects-root DIR]

Nothing here contacts Resolve, writes to a project, or starts a broker.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from library.tools import (
    backend_selection,
    edit_algebra,
    execution_router,
)

# ── the BEFORE sites ────────────────────────────────────────────────────
#
# AXI-002 deleted these.  Each is a set of route names that answered a
# question the router had already answered.

#: `resolve_axi_core._execute_registered_backend`'s own two sets.
BEFORE_DISPATCHER_QUALIFIED = frozenset({
    "incremental", "resolve_native_invocation"})
BEFORE_DISPATCHER_PROTECTED = frozenset({"reel_touchup", "resolve_axi"})

#: `resolve_axi_core.submit_edit_request_async`'s set.
BEFORE_SUBMIT_BACKENDS = frozenset({"edit_patch", "edit_patch.property"})

#: `resolved/jobs.prepare`'s set.
BEFORE_BROKER_GATE_BACKENDS = frozenset({
    "edit_patch", "edit_patch.property", "no_op"})

#: `feedback_patch_apply.apply`'s set.
BEFORE_FRONT_DOOR_BACKENDS = frozenset({"edit_patch", "edit_patch.property"})

#: `resolve_axi_core.register_edit_executor`'s set.
BEFORE_REGISTRATION_BACKENDS = frozenset({
    "incremental", "resolve_native_invocation"})

#: The BEFORE deciders, as the measurement counts them.
BEFORE_SITES = (
    "execution_router.route_compiled_edit (the selector)",
    ("execution_router._route_compiled_edit - a SECOND Resolve AXI arm, "
     "repeating the same eligibility judgment"),
    ("resolve_axi_core._execute_registered_backend - a second eligibility "
     "judgment and a second physical-path choice"),
    ("resolve_axi_core.submit_edit_request_async - which broker job carries "
     "the route"),
    ("resolve_axi_core.register_edit_executor - which backend an executor "
     "may be registered on"),
    ("resolve_axi_core._plan_transaction_patch - the legacy typed path's "
     "route, declared outside the router"),
    "resolved.jobs.prepare - which routes a semantic job may carry",
    "feedback_patch_apply.apply - which routes the guarded path accepts",
)


def before_guard_reasons(route: str, operation_ids, decision) -> list[str]:
    """The BEFORE dispatcher's own eligibility judgment."""
    route = str(route or "").removesuffix(".structural")
    reasons: list[str] = []
    selection = (decision or {}).get("selection") or {}
    policy = str(selection.get("qualification_policy") or "production")
    gaps = {str(gap[0]) for gap in
            ((decision or {}).get("qualification_gaps") or ()) if gap}
    for op_id in operation_ids:
        entry = edit_algebra.status(str(op_id))
        row = entry.for_backend(route) if entry is not None else None
        production = row is not None and row.production_enabled
        if ((row is None or not production)
                and route not in BEFORE_DISPATCHER_QUALIFIED
                and route not in BEFORE_DISPATCHER_PROTECTED):
            reasons.append(
                f"dispatcher(before): {op_id} is not production enabled on "
                f"{route}")
            continue
        if (route in BEFORE_DISPATCHER_PROTECTED
                and (row is None or not production)):
            if policy != "protected_experimental":
                reasons.append(
                    f"dispatcher(before): unqualified {op_id} on {route} "
                    "without protected experimental policy")
            elif op_id not in gaps:
                reasons.append(
                    f"dispatcher(before): the router omitted the "
                    f"qualification gap for {op_id} on {route}")
    return reasons


def after_guard_reasons(route: str, operation_ids, decision) -> list[str]:
    """The AFTER dispatcher's judgment: the router's own."""
    return list(execution_router.route_admission_reasons(
        str(route or "").removesuffix(".structural"), operation_ids,
        decision))


def before_submit_job(route: str) -> str:
    """The BEFORE broker job the submit path chose for this route."""
    return ("timeline.apply_patch"
            if str(route) in BEFORE_SUBMIT_BACKENDS
            else "timeline.semantic_edit")


def after_submit_job(route: str) -> str:
    """The AFTER broker job: the router's transport table."""
    try:
        return execution_router.transport_for(route)
    except Exception:  # noqa: BLE001 - an unknown route owns no write
        return "none"


def before_broker_gate(route: str) -> str:
    """What the BEFORE broker's submission gate did with the route."""
    return ("refused" if str(route) in BEFORE_BROKER_GATE_BACKENDS
            else "accepted")


def after_broker_gate(route: str) -> str:
    """What the AFTER broker's submission gate does: the router's table."""
    try:
        transport = execution_router.transport_for(route)
    except Exception:  # noqa: BLE001
        return "refused"
    return ("refused" if transport != "timeline.semantic_edit" else "accepted")


def before_front_door(route: str) -> bool:
    return str(route) in BEFORE_FRONT_DOOR_BACKENDS


def after_front_door(route: str) -> bool:
    try:
        return execution_router.is_edit_patch_route(route)
    except Exception:  # noqa: BLE001
        return False


# ── the corpus ──────────────────────────────────────────────────────────


def _recorded_route_change(error: str) -> tuple[str, str]:
    """The route change a recorded refusal names, if it names one.

    The broker's own refusal for a request whose route changed between
    the plan and the execution reads "semantic edit route changed while
    queued: <plan> to <execution>".  It is the ONLY record of that
    second choice: the request died before it published a receipt, so the
    receipt trail shows nothing at all.
    """
    text = str(error or "")
    marker = "route changed while queued:"
    if marker not in text:
        return ("", "")
    tail = text.split(marker, 1)[1].strip()
    if " to " not in tail:
        return ("", "")
    before, after = tail.split(" to ", 1)
    return (before.strip(), after.strip().rstrip("."))


def broker_receipts() -> list[dict]:
    """The machine broker's recorded semantic-edit jobs, read-only."""
    from library.tools.resolved import broker_paths

    path = broker_paths.production_dir() / broker_paths.DB_NAME
    if not path.exists():
        return []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "select id, kind, state, params, result, error, submitted "
            "from jobs where kind = 'timeline.semantic_edit'").fetchall()
    finally:
        connection.close()
    records = []
    for job_id, kind, state, params, result, error, submitted in rows:
        try:
            params = json.loads(params) if params else {}
            result = json.loads(result) if result else {}
        except (TypeError, ValueError):
            continue
        request = params.get("request") or {}
        error = str(error or "")
        _error_before, error_after = _recorded_route_change(error)
        records.append({
            "source": "broker",
            "id": job_id,
            "state": state,
            "submitted": submitted,
            "operations": [str(op.get("op") or "")
                           for op in (request.get("operations") or [])],
            "plan_route": params.get("backend"),
            "execute_route": (result.get("execution_routing") or {}).get(
                "backend") or error_after,
            "decision": result.get("execution_routing") or {},
            "error": error,
            "result_recorded": bool(result),
        })
    return records


def project_receipts(root: Path) -> list[dict]:
    """The recorded `ren revise` receipts under a projects root."""
    records = []
    if not root.is_dir():
        return records
    for path in sorted(root.glob(
            "*/pipeline_output/llm_requests/revise/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        preparation = data.get("preparation") or {}
        routing = preparation.get("routing") or {}
        receipt = data.get("execution_receipt") or {}
        request = data.get("edit_request") or {}
        if not routing:
            continue
        records.append({
            "source": "project",
            "id": str(path),
            "state": receipt.get("status"),
            "operations": [str(op.get("op") or "")
                           for op in (request.get("operations") or [])],
            "plan_route": routing.get("backend"),
            "execute_route": (receipt.get("execution_routing") or {}).get(
                "backend"),
            "decision": routing,
            "error": str((data.get("refusal_detail") or {}).get("what") or ""),
            "result_recorded": bool(receipt),
        })
    return records


# ── the measurement ─────────────────────────────────────────────────────


def measure(records: list[dict]) -> dict:
    """Before and after, over the same records, each with its denominator."""
    guard_evaluable = 0
    route_change_observable = 0
    route_changed = 0
    route_changed_without_a_recorded_decision = 0
    before_divergent = 0
    after_divergent = 0
    details = []
    for record in records:
        route = str(record.get("plan_route") or "")
        execute_route = str(record.get("execute_route") or "")
        operations = record.get("operations") or []
        decision = record.get("decision") or {}
        reasons_before = []
        reasons_after = []
        if route and decision:
            guard_evaluable += 1
            reasons_before = before_guard_reasons(route, operations, decision)
            reasons_after = after_guard_reasons(route, operations, decision)
        job_before = before_submit_job(route) if route else ""
        job_after = after_submit_job(route) if route else ""
        gate_before = before_broker_gate(route) if route else ""
        gate_after = after_broker_gate(route) if route else ""
        # Two sites disagree when one of them would NOT run the route the
        # router chose, when they disagree about the job that carries it,
        # or when the route the request was planned against is not the
        # route the execution chose - one request, two decisions.
        route_changed_here = bool(route and execute_route
                                  and route != execute_route)
        divergent_before = bool(reasons_before) or (
            gate_before == "accepted" and job_before == "timeline.semantic_edit"
            and route in BEFORE_BROKER_GATE_BACKENDS) or route_changed_here
        # AFTER: the executor CONFIRMS the route the request carries
        # instead of choosing a second one, so a disagreement is a
        # refusal, not a second choice.
        divergent_after = bool(reasons_after) or (
            gate_after == "accepted" and job_after == "timeline.semantic_edit"
            and route in BEFORE_BROKER_GATE_BACKENDS)
        if route and (execute_route or record.get("result_recorded")):
            route_change_observable += 1
            if route_changed_here:
                route_changed += 1
                if not record.get("result_recorded"):
                    route_changed_without_a_recorded_decision += 1
        if divergent_before:
            before_divergent += 1
        if divergent_after:
            after_divergent += 1
        if divergent_before or divergent_after or (
                route and execute_route and route != execute_route):
            details.append({
                "id": record.get("id"),
                "source": record.get("source"),
                "operations": operations,
                "plan_route": route,
                "execute_route": execute_route,
                "dispatcher_before": reasons_before,
                "dispatcher_after": reasons_after,
                "job_before": job_before,
                "job_after": job_after,
                "gate_before": gate_before,
                "gate_after": gate_after,
                "route_changed_while_queued": bool(
                    route and execute_route and route != execute_route),
                "decision_recorded": bool(record.get("result_recorded")),
            })
    return {
        "requests": len(records),
        "guard_evaluable": guard_evaluable,
        "route_change_observable": route_change_observable,
        "route_changed_while_queued": route_changed,
        "route_changed_without_a_recorded_decision":
            route_changed_without_a_recorded_decision,
        "divergent_before": before_divergent,
        "divergent_after": after_divergent,
        "details": details,
    }


def duplicate_judgments() -> dict:
    """The second Resolve AXI arm inside the selector, measured.

    The arm was unreachable, but it answered the same question with a
    different rule (it added the compound-shape check), so it would have
    chosen differently for every multi-operation request whose shape has
    no declared compound scenario.  The count is the number of such
    shapes among the operations that are production-enabled on the AXI
    adapter.
    """
    from library.tools import axi_transaction

    production = sorted(
        op_id for op_id, entry in edit_algebra.OP_STATUS.items()
        if entry.for_backend("resolve_axi") is not None
        and entry.for_backend("resolve_axi").production_enabled)
    diverging = []
    for count in (2, 3):
        from itertools import combinations

        for combo in combinations(production, count):
            operations = [{"op": op_id} for op_id in combo]
            if not axi_transaction.batch_supported("resolve_axi", operations):
                diverging.append(list(combo))
    return {"production_ops_on_resolve_axi": production,
            "shapes_the_two_arms_disagreed_on": diverging,
            "count": len(diverging)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects-root", default="",
                        help="a projects root whose recorded revise receipts "
                             "join the corpus")
    parser.add_argument("--json", action="store_true",
                        help="print the measurement as JSON")
    args = parser.parse_args()

    records = broker_receipts()
    broker_count = len(records)
    if args.projects_root:
        records += project_receipts(Path(args.projects_root).expanduser())
    result = measure(records)
    result["corpus"] = {
        "broker_jobs": broker_count,
        "project_receipts": len(records) - broker_count,
    }
    result["sites"] = {
        "before": len(BEFORE_SITES),
        "before_list": list(BEFORE_SITES),
        "after_deciders": len(backend_selection.selectors()),
        "after_sites": len(backend_selection.selection_sites()),
        "after_list": [site.as_dict()
                       for site in backend_selection.selection_sites()],
    }
    result["duplicate_judgments"] = duplicate_judgments()
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    print(f"requests measured: {result['requests']} "
          f"(broker {result['corpus']['broker_jobs']}, "
          f"project {result['corpus']['project_receipts']})")
    print(f"selection sites that DECIDE: before {result['sites']['before']}, "
          f"after {result['sites']['after_deciders']} "
          f"(all sites after: {result['sites']['after_sites']})")
    print(f"requests where two sites would have chosen differently: "
          f"before {result['divergent_before']}/{result['requests']}, "
          f"after {result['divergent_after']}/{result['requests']}")
    print(f"  (the dispatcher's guard was evaluable for "
          f"{result['guard_evaluable']}/{result['requests']} requests; the "
          f"route change was observable for "
          f"{result['route_change_observable']}/{result['requests']})")
    print(f"requests whose route changed between the plan and the "
          f"execution: {result['route_changed_while_queued']}"
          f"/{result['route_change_observable']} observable "
          f"(of which {result['route_changed_without_a_recorded_decision']} "
          f"recorded no decision at all)")
    print(f"shapes on which the selector's two Resolve AXI arms disagreed: "
          f"{result['duplicate_judgments']['count']}")
    for row in result["details"]:
        print("  -", row["source"], row["id"], row["operations"],
              row["plan_route"], "->", row["execute_route"],
              "| before:", row["dispatcher_before"], row["job_before"],
              row["gate_before"],
              "| after:", row["dispatcher_after"], row["job_after"],
              row["gate_after"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
