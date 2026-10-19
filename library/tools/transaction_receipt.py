"""Standard outcome fields for durable editing transactions.

Each stage is reported independently. In particular, a verified edit is not
implicitly described as delivered: delivery belongs to a later product step.
"""

from __future__ import annotations

from typing import Any


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


def standardize(receipt: dict[str, Any]) -> dict[str, Any]:
    """Add the five distinct stage outcomes to a legacy patch receipt."""
    out = dict(receipt)
    status = out.get("status")
    operations = out.get("operations") or []
    applied_count = len(operations)
    verified = status == "committed"
    persisted = out.get("generation") is not None
    out.update({
        "planned": {"status": "planned", "patch_id": out.get("patch_id")},
        "applied": {"status": "applied" if applied_count else
                    ("partial" if operations else "not_applied"),
                    "operations": applied_count},
        "verified": {"status": "verified" if verified else
                     ("failed" if status == "verification_failed" else
                      "not_verified")},
        "committed": {"status": "committed" if persisted else
                      "not_committed", "generation": out.get("generation")},
        "delivered": {"status": "not_requested"},
    })
    if "recovery" not in out:
        if status == "verification_failed":
            stopped = out.get("stopped")
            kind = "api_uncertain" if stopped else "verification_failed"
            reason = (stopped.get("raised", "") if stopped else
                      "read-back did not match plan")
            out["recovery"] = recovery(kind, reason=reason)
        else:
            out["recovery"] = None
    return out
