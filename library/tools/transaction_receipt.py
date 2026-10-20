"""Shared lifecycle vocabulary and receipt normalization for edit transactions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

STAGES = ("PLANNED", "VALIDATED", "EXECUTING", "APPLIED", "VERIFIED",
          "COMMITTED", "DELIVERED")
RECOVERY_ACTIONS = {
    "target_not_found": "replan_target",
    "ambiguous_target": "replan_target",
    "unsupported": "refuse",
    "api_uncertain": "inspect_observed_state_before_retry",
    "unexpected_insert": "remove_verified_unintended_item",
    "clamped_property": "replan_from_readback",
    "stale_generation": "rebase_if_provably_safe_else_replan",
    "collateral_change": "apply_and_verify_inverse",
    "verification_failed": "preserve_readback_and_replan",
    "recovery_failed": "preserve_forensics_and_intervene",
}


def recovery(kind: str, *, reason: str = "") -> dict[str, str]:
    if kind not in RECOVERY_ACTIONS:
        raise ValueError(f"unknown transaction recovery type: {kind}")
    return {"type": kind, "action": RECOVERY_ACTIONS[kind],
            "reason": reason}


def lifecycle(receipt: dict[str, Any], *, stage: str,
              expected_state: Any = None, actual_state: Any = None,
              backend: str | None = None, idempotency_key: str | None = None,
              affected_changes: Any = None,
              unrelated_changes: Any = None,
              verification: Any = None, persistence: Any = None,
              promotion: Any = None, delivery: Any = None) -> dict[str, Any]:
    """Return the standard transaction record at its last completed stage."""
    if stage not in STAGES:
        raise ValueError(f"unknown transaction stage: {stage}")
    values = {
        "stage": stage,
        "ids": {"transaction_id": receipt.get("patch_id"),
                "idempotency_key": idempotency_key or receipt.get("idempotency_key") or receipt.get("patch_id"),
                "project": receipt.get("project"),
                "timeline": receipt.get("timeline")},
        "expected_state": expected_state,
        "actual_state": actual_state,
        "affected_changes": affected_changes or [],
        "unrelated_changes": unrelated_changes or [],
        "backend": backend,
        "route_selection": receipt.get("route_selection"),
        "verification": verification,
        "recovery": receipt.get("recovery"),
        "recovery_result": receipt.get("recovery_result"),
        "persistence": persistence,
        "promotion": promotion,
        "delivery": delivery,
    }
    existing = list(receipt.get("lifecycle", {}).get("history", ()))
    values["history"] = existing or list(STAGES[:STAGES.index(stage) + 1])
    if values["history"][-1] != stage:
        values["history"].append(stage)
    return values


def standardize(receipt: dict[str, Any]) -> dict[str, Any]:
    """Add ordered lifecycle stages to a legacy patch receipt.

    A failed operation or acceptance contract cannot be described as
    verified, committed, or delivered. A shadow generation may still be
    persisted as forensic read-back after a failed verification.
    """
    out = dict(receipt)
    status = out.get("status")
    operations = out.get("operations") or []
    applied_count = sum(1 for row in operations
                        if row.get("failure") is None)
    requested_count = int(out.get("requested_operation_count", len(operations)))
    contracts = out.get("acceptance_contracts") or []
    contracts_ok = bool(contracts) and all(
        row.get("passed") is True for row in contracts)
    verified = out.get("resolve_write_status", status) == "committed" and contracts_ok
    persisted = out.get("generation") is not None
    model_commit = out.get("project_model_commit", {"status": "not_required"})
    committed = persisted and verified and model_commit.get("status") != "failed"
    if "recovery" not in out:
        if status == "verification_failed" or not contracts_ok:
            stopped = out.get("stopped")
            out["recovery"] = recovery(
                "api_uncertain" if stopped else "verification_failed",
                reason=(stopped.get("raised", "") if stopped else
                        "read-back or operation acceptance contract failed"))
        else:
            out["recovery"] = None
    out.setdefault("recovery_result", {
        "status": "not_attempted" if verified else "recovery_required",
        "reason": "verified result" if verified else
                  "read-back and transaction history retained"})
    out.update({
        "planned": {"status": "planned", "patch_id": out.get("patch_id")},
        "validated": {"status": "validated" if out.get("base_generation") is not None else "not_validated",
                      "base_generation": out.get("base_generation")},
        "executing": {"status": "completed" if requested_count == len(operations) else
                      ("partial" if operations else
                       "failed" if out.get("stopped") else "not_started"),
                      "stopped": out.get("stopped")},
        "applied": {"status": "applied" if applied_count == requested_count else
                    ("partial" if applied_count else "not_applied"),
                    "operations": applied_count, "requested": requested_count,
                    "complete": applied_count == requested_count},
        "verified": {"status": "verified" if verified else
                     ("failed" if status == "verification_failed" or not contracts_ok else "not_verified"),
                     "acceptance_contracts_passed": contracts_ok},
        "committed": {"status": "committed" if committed else
                      ("project_model_commit_failed" if persisted and verified else
                       "readback_persisted_unverified" if persisted else "not_committed"),
                      "generation": out.get("generation"),
                      "project_model": model_commit},
        "delivered": {"status": out.get("delivered", {}).get("status", "not_requested")},
    })
    out["lifecycle"] = lifecycle(
        out, stage=("COMMITTED" if committed else
                    "VERIFIED" if persisted and verified else
                    "APPLIED" if applied_count else "PLANNED"),
        expected_state=out.get("expected_state"),
        actual_state=out.get("readback"), backend=out.get("backend"),
        affected_changes=out.get("operations"),
        unrelated_changes=out.get("unrelated_changes"),
        verification={"outcome": out.get("verified"),
                      "acceptance_contracts": contracts},
        persistence={**out.get("committed", {}),
                     "project_model": model_commit},
        promotion=out.get("promotion"), delivery=out.get("delivered"))
    return out


def interrupted(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find transactions whose durable event stream has no terminal commit."""
    transactions: dict[str, dict[str, Any]] = {}
    for event in events:
        fields = event.get("fields", {})
        transaction_id = fields.get("transaction_id")
        if not transaction_id:
            continue
        row = transactions.setdefault(transaction_id, {
            "transaction_id": transaction_id, "last_stage": None,
            "last_event": None, "operations": {}})
        row["last_stage"] = fields.get("stage")
        row["last_event"] = event
        if fields.get("stage") in {"operation_applying", "operation_applied"}:
            operation = fields.get("operation", fields.get("op"))
            operation_id = (operation.get("op")
                            if isinstance(operation, Mapping) else operation)
            operation_row = {"index": fields.get("index"),
                             "op": operation_id}
            if fields.get("stage") == "operation_applying":
                operation_row["mutation_state"] = "uncertain"
            row["operations"][fields.get("index")] = operation_row
    return [{**row, "operations": list(row["operations"].values()),
             "status": "recovery_required"}
            for row in transactions.values()
            if row["last_stage"] not in {"committed", "delivered"}]
