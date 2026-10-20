"""The reel build's receipt in the common execution-contract shape.

AXI-36 / AXI-37: every backend publishes a receipt that answers the same
questions - what was requested, what was applied, what was VERIFIED, what
was NOT verified, what recovery state exists and which route ran. The
types are ``transaction_orchestration``'s (``ExecutionResult``,
``VerificationResult``, ``RecoveryResult``) and the lifecycle is
``transaction_receipt``'s, so a reader asks the same question of a reel
build and of a semantic patch and gets the same shape of answer.

The build's own measurements are the evidence. What the build did NOT
measure is named as missing, never passed: a reel build does not render,
so the rendered-pixel and rendered-audio layers read ``not_measured``, and
an overlay sweep that was deferred to its own judge is named as pending
rather than counted as a check that passed.

This module publishes a receipt; it never writes to Resolve and never
judges anything the build did not measure.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: The acceptance contracts a reel build can settle on its own. Each one
#: names a measurement the build took and reads back.
BUILD_CONTRACTS = ("reel.placement", "reel.streams")

#: What a reel build cannot measure by itself. A build places and reads
#: back a timeline; it does not render, so the rendered layers stay
#: unmeasured and are never reported as verified.
BUILD_UNMEASURED = ("rendered_pixels", "rendered_audio")

#: The layers of a verification result, in the common vocabulary.
_NOT_MEASURED = {"status": "not_measured", "evidence": {}}


def _contract(operation: str, checks: Sequence[str], passed: bool,
              evidence: Mapping | None = None) -> dict:
    """One acceptance contract in the common shape."""
    rows = [{"check": check, "passed": bool(passed), "status":
             "passed" if passed else "failed", "evidence": None}
            for check in checks]
    return {
        "id": f"acceptance:{operation}",
        "operation": operation,
        "checks": list(checks),
        "structural_source": "timeline_readback",
        "passed": bool(passed),
        "results": rows,
        "missing": [],
        "failed": [check for check in checks if not passed],
        "evidence": dict(evidence or {}),
    }


def build_contracts(record: Mapping) -> list[dict]:
    """The build's own measurements, as acceptance contracts.

    Only measurements the build took and read back become contracts. A
    check the build never took is named in the receipt's ``missing`` list
    instead of being counted as a contract that passed.
    """
    contracts: list[dict] = []
    placement = record.get("placement_check")
    if isinstance(placement, Mapping):
        contracts.append(_contract(
            "reel.placement",
            ("requested_timeline_span", "unrelated_items_unchanged"),
            bool(placement.get("passed")),
            {"repair_attempted": bool(placement.get("repair_attempted"))}))
    streams = record.get("stream_enforcement")
    if isinstance(streams, Mapping):
        unverified = list(streams.get("unverified") or ())
        contracts.append(_contract(
            "reel.streams",
            ("requested_audio_streams_present",),
            not unverified,
            {"checked": int(streams.get("checked") or 0),
             "unverified": unverified}))
    return contracts


def unmeasured(record: Mapping) -> tuple[str, ...]:
    """What this build did not measure, by name."""
    missing: list[str] = list(BUILD_UNMEASURED)
    if record.get("overlay_sweep") is None:
        missing.append("overlay_sweep_verdict")
    return tuple(missing)


def execution_contract(record: Mapping, *, backend: str,
                       routing: Mapping | None = None,
                       recovery: Mapping | None = None) -> dict:
    """The common execution contract for one reel build."""
    from library.tools.transaction_orchestration import (
        ExecutionResult,
        RecoveryResult,
        VerificationResult,
    )

    contracts = build_contracts(record)
    checks = tuple({"passed": row["passed"], "operation": row["operation"],
                    "failures": list(row["failed"])} for row in contracts)
    missing = tuple(unmeasured(record))
    verified = bool(checks) and all(row["passed"] for row in checks)
    recovery_state = dict(recovery or {})
    status = "verified" if verified else ("failed" if checks else "not_measured")
    return ExecutionResult(
        status=status,
        backend=backend,
        verification=VerificationResult(
            status=status,
            checks=checks,
            missing=missing,
            evidence={
                "acceptance_check_count": len(checks),
                "acceptance_contracts": contracts,
                "route_selection": dict(routing or {}),
            },
            layers={
                "stored_state": {"status": "verified" if verified else "failed",
                                 "checks": len(checks)},
                "rendered_pixels": dict(_NOT_MEASURED),
                "rendered_audio": dict(_NOT_MEASURED),
            }),
        recovery=RecoveryResult(
            status=str(recovery_state.get("status") or "not_required"),
            action=str(recovery_state.get("action") or "none"),
            reason=str(recovery_state.get("reason") or ""),
            evidence={"recovery_record_present": bool(recovery_state)}),
        limitations=tuple(str(value) for value in record.get("limitations", ())),
        receipt={
            "transaction_id": record.get("patch_id"),
            "idempotency_key": record.get("idempotency_key"),
            "timeline": record.get("timeline_name"),
        },
    ).as_dict()


def publish(record: Mapping, *, backend: str,
            routing: Mapping | None = None,
            recovery: Mapping | None = None) -> dict:
    """Publish one build record through the common receipt contract.

    Additive: the record keeps every key it had, and gains the common
    ``execution_contract`` and ``lifecycle`` beside them, plus the
    mutation scope and the transaction ownership the route declares
    (AXI-39 / AXI-41) - so a reader of a build record can tell what the
    build disturbed and who owned the write.
    """
    from library.tools import transaction_receipt
    from library.tools.execution_router import (
        ROUTE_MUTATION_SCOPE,
        _ownership_for,
    )

    out = dict(record)
    route = dict(routing or out.get("execution_routing") or {})
    selection = dict(route.get("selection") or {})
    out["mutation_scope"] = (selection.get("mutation_scope")
                              or ROUTE_MUTATION_SCOPE.get(backend, "unknown"))
    out["transaction_ownership"] = dict(
        selection.get("transaction_ownership") or _ownership_for(backend))
    out["execution_contract"] = execution_contract(
        out, backend=backend, routing=route, recovery=recovery)
    contracts = build_contracts(out)
    verified = bool(contracts) and all(row["passed"] for row in contracts)
    out["lifecycle"] = transaction_receipt.lifecycle(
        out, stage=("VERIFIED" if verified else "APPLIED"),
        expected_state={"planned_reel": out.get("timeline_name")},
        actual_state={"contracts": contracts},
        backend=backend,
        affected_changes=out.get("placements"),
        unrelated_changes=[],
        verification={"outcome": out["execution_contract"]["verification"]},
        promotion=out.get("promotion"),
        delivery=out.get("delivered"))
    return out
