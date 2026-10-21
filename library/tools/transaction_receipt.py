"""Shared lifecycle vocabulary and receipt normalization for edit transactions.

AXI-003: the SIX lifecycle states answer SIX questions, and each one
answers only its own.

    planned     the request was planned (and named)
    applied     how many operations the write actually applied
    verified    whether the acceptance was MEASURED and passed
    committed   whether the write is durably committed
    promoted    whether a staged change reached the live timeline
    delivered   whether the file was delivered

`verified` used to answer two questions at once, and it answered the
second one wrongly: a receipt with NO acceptance contract, and a receipt
whose contract FAILED, both read `failed`.  A build that worked and was
never measured therefore read as a failure, and carried a recovery that
nothing had to recover.  The contract's own outcome
(`operation_acceptance.outcome_of`) now decides, and the three readings
stay apart:

    verified     the acceptance was measured and passed
    failed       the acceptance was measured and did not pass
    unmeasured   the acceptance was not measured

`acceptance_outcome` is the ONE reader of that: every path asks it the
same question and gets the same answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

STAGES = ("PLANNED", "VALIDATED", "PREPARED", "EXECUTING", "APPLIED",
          "VERIFIED", "COMMITTED", "PROMOTED", "DELIVERED")

#: The outcome of a receipt's acceptance, in the three readings a
#: reader can act on.  `accepted` means the work was measured and it
#: passed; `failed` means it was measured and it did not; `unmeasured`
#: means nobody established it, which is neither of the other two.
ACCEPTED = "accepted"
FAILED_OUTCOME = "failed"
UNMEASURED_OUTCOME = "unmeasured"
REFUSED_OUTCOME = "refused"

ACCEPTANCE_OUTCOMES: tuple[str, ...] = (ACCEPTED, FAILED_OUTCOME,
                                         UNMEASURED_OUTCOME, REFUSED_OUTCOME)

#: The receipt-level verification statuses.  `unverified` is the third
#: one: the write landed and the acceptance was not measured.  A reader
#: that asks "is this committed?" still says no (the work is not
#: accepted), and a reader that asks "did this fail?" no longer hears
#: yes.
VERIFIED = "verified"
VERIFICATION_FAILED = "failed"
UNVERIFIED = "unverified"
RECOVERY_ACTIONS = {
    "target_not_found": "replan_target",
    "ambiguous_target": "replan_target",
    "unsupported": "refuse",
    "api_uncertain": "inspect_observed_state_before_retry",
    "unexpected_insert": "remove_verified_unintended_item",
    "clamped_property": "replan_from_readback",
    "stale_generation": "rebase_if_provably_safe_else_replan",
    "execution_failed": "apply_and_verify_inverse",
    "collateral_change": "apply_and_verify_inverse",
    "conflicting_state": "preserve_state_and_clarify",
    "verification_failed": "preserve_readback_and_replan",
    "recovery_failed": "preserve_forensics_and_intervene",
}

# A transaction whose last durable stage is one of these owes nothing.
# ``recorded`` is the edit_patch stage for a write whose Project Model
# commit failed; it is resolved when the write itself was verified, and
# UNRESOLVED when the write was not - that is the recovery-required
# state, and it must keep blocking further writes. ``recovered`` and
# ``abandoned`` are the two ways a captain closes an outstanding
# recovery, and they are journalled so the decision is not asked twice.
RESOLVED_STAGES = frozenset({"committed", "delivered", "promoted",
                              "recovered", "abandoned"})

#: The durable stages that mean a Resolve write was ATTEMPTED for the
#: operation.  Every writer journals the attempt BEFORE it calls
#: Resolve, so a transaction whose record never reached one of these
#: never touched the timeline: it owes no recovery, and the only thing
#: it can be is the debris of a job that died before its first write.
WRITE_ATTEMPT_STAGES = frozenset({"applying", "operation_applying",
                                  "applied", "operation_applied"})


def _is_resolved(fields: Mapping[str, Any]) -> bool:
    """Whether a durable stage leaves the transaction settled."""
    stage = str(fields.get("stage") or "")
    if stage in RESOLVED_STAGES:
        return True
    if stage == "recorded":
        return str(fields.get("status") or "") not in {
            "", "verification_failed"}
    return False



def recovery(kind: str, *, reason: str = "",
             conflict_class: str = "") -> dict[str, str]:
    if kind not in RECOVERY_ACTIONS:
        raise ValueError(f"unknown transaction recovery type: {kind}")
    out = {"type": kind, "action": RECOVERY_ACTIONS[kind], "reason": reason}
    if conflict_class:
        out["conflict_class"] = conflict_class
    return out


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


def _prepared_status(receipt: Mapping[str, Any]) -> str:
    """Whether the durable intent reached the journal before the write.

    PREPARED is a claim about the journal, not about the write: it says
    the transaction could be recovered from the record even if the
    process died mid-write.
    """
    journal = receipt.get("journal")
    if not isinstance(journal, Mapping):
        return "unknown"
    if journal.get("intent_persisted") is True:
        return "prepared"
    if journal.get("intent_persisted") is False:
        return "not_prepared"
    return "unknown"


def _promoted_status(receipt: Mapping[str, Any]) -> str:
    """Whether a staged change reached the live timeline."""
    promotion = receipt.get("promotion")
    if not isinstance(promotion, Mapping) or not promotion:
        return "not_requested"
    status = str(promotion.get("status") or "")
    if status in {"promoted", "applied", "promotion_succeeded"}:
        return "promoted"
    if status in {"not_required", "not_applicable"}:
        return "not_required"
    if status:
        return status
    if promotion.get("final") and promotion.get("staging"):
        return "promoted"
    return "not_requested"


def _acceptance_reading(contracts: Any) -> tuple[str, list[str], list[str]]:
    """One reading of a receipt's acceptance contracts.

    Returns ``(outcome, failed_checks, missing_checks)`` where the
    outcome is one of `operation_acceptance`'s three.  A receipt with NO
    contract is `unmeasured`: nothing was established, which is not the
    same as a contract that failed.  A contract that failed outranks a
    contract that was never finished, because a measured failure is a
    fact about the work and an unmeasured one is only a gap.
    """
    from library.tools import operation_acceptance

    failed: list[str] = []
    missing: list[str] = []
    if not contracts:
        return operation_acceptance.UNMEASURED, failed, missing
    outcomes = []
    for contract in contracts:
        outcome = operation_acceptance.outcome_of(contract)
        outcomes.append(outcome)
        if outcome == operation_acceptance.FAILED:
            names = [str(name) for name in (contract.get("failed") or ())]
            failed.extend(names or ["(unnamed check)"])
        elif outcome == operation_acceptance.UNMEASURED:
            missing.extend(str(name) for name in (contract.get("missing") or ()))
    if operation_acceptance.FAILED in outcomes:
        return operation_acceptance.FAILED, failed, missing
    if operation_acceptance.UNMEASURED in outcomes:
        return operation_acceptance.UNMEASURED, failed, missing
    return operation_acceptance.PASSED, failed, missing


def _write_state(receipt: Mapping[str, Any]) -> str:
    """What happened to the WRITE, apart from its acceptance.

    `committed` - the write landed.  `failed` - the write did not
    happen, or the read-back rejected it.  `unverified` - the write
    landed and the acceptance was not measured.  `unknown` - the
    receipt says nothing about it, so nothing is established.
    """
    status = str(receipt.get("status") or "")
    write_status = str(receipt.get("resolve_write_status") or "")
    if status in {"failed", "partial_failure", "verification_failed",
                  "rejected"} or write_status in {
                      "failed", "verification_failed", "rejected"}:
        return "failed"
    if status == UNVERIFIED or write_status == UNVERIFIED:
        return UNVERIFIED
    if status == "committed" or write_status == "committed":
        return "committed"
    return "unknown"


def _is_refusal(receipt: Mapping[str, Any]) -> bool:
    """Whether the receipt records a REFUSAL rather than a failure.

    A refusal is a decision Ren made: nothing ran, and the next step is
    known.  It is not a failure and not an exception, and a receipt that
    carries one must not read as either.  The receipt qualifies when it
    says it was refused - the status says so, or the refusal carries
    the engine-wide shape - and NOTHING was applied.  A receipt that
    applied part of the work and then refused is a partial failure,
    which is a different thing.
    """
    refusal = receipt.get("refusal")
    engine = refusal.get("engine_refusal") if isinstance(refusal, Mapping) else None
    if not isinstance(engine, Mapping):
        engine = receipt.get("engine_refusal")
    status = str(receipt.get("status") or "")
    if status not in {"rejected", "refused"} and not isinstance(engine, Mapping):
        return False
    applied = receipt.get("applied")
    if isinstance(applied, Mapping):
        return int(applied.get("operations") or 0) == 0
    operations = receipt.get("operations") or []
    return not operations and not receipt.get("mutated")


def acceptance_outcome(receipt: Mapping[str, Any] | None) -> str:
    """The ONE reading of whether the work was accepted.

    Every production path answers this question the same way, so two
    readers of the same receipt cannot disagree about whether the
    operation was accepted.  The four answers:

    `accepted`    the acceptance was measured and it passed
    `failed`      the acceptance was measured and it did not pass, or
                  the write itself failed
    `unmeasured`  nothing established it - no contract, or a contract
                  nobody finished
    `refused`     Ren decided not to act; nothing ran

    A receipt that carries no acceptance block at all is read from its
    own contracts and its own status, so a receipt written before the
    block existed is read, not guessed at.
    """
    from library.tools import operation_acceptance

    if not isinstance(receipt, Mapping):
        return UNMEASURED_OUTCOME
    if _is_refusal(receipt):
        return REFUSED_OUTCOME
    if _write_state(receipt) == "failed":
        # The write did not land, so the operation was not accepted -
        # whatever its acceptance evidence says.
        return FAILED_OUTCOME
    verified = receipt.get("verified")
    if isinstance(verified, Mapping):
        status = str(verified.get("status") or "")
        if status == VERIFIED:
            return ACCEPTED
        if status == VERIFICATION_FAILED:
            return FAILED_OUTCOME
        if status == UNMEASURED_OUTCOME:
            return UNMEASURED_OUTCOME
    contracts = receipt.get("acceptance_contracts") or []
    outcome, failed, missing = _acceptance_reading(contracts)
    if outcome == operation_acceptance.FAILED:
        return FAILED_OUTCOME
    if outcome == operation_acceptance.UNMEASURED:
        return UNMEASURED_OUTCOME
    if _write_state(receipt) != "committed":
        return UNMEASURED_OUTCOME
    return ACCEPTED


def lifecycle_blocks(*, applied: int, requested: int, acceptance: str,
                     failed_checks: Sequence[str] = (),
                     missing_checks: Sequence[str] = (),
                     committed: bool = False, persisted: bool = False,
                     write_state: str = "unknown", refused: bool = False,
                     generation: Any = None, project_model: Any = None,
                     promotion: Any = None, delivery: Any = None,
                     patch_id: Any = None, reason: str = "") -> dict[str, Any]:
    """The SIX lifecycle blocks, in the one vocabulary every path uses.

    AXI-003: `planned`, `applied`, `verified`, `committed`, `promoted`
    and `delivered` each answer ONE question, and the six are defined
    here, once, so a semantic patch and a staged reel build answer them
    the same way.

    `acceptance` is the contract's outcome (`operation_acceptance`'s
    three).  It decides the `verified` block alone: a contract nobody
    measured makes the verification `unmeasured`, and `unmeasured` is
    not `failed`.
    """
    from library.tools import operation_acceptance

    failed_checks = [str(name) for name in failed_checks]
    missing_checks = [str(name) for name in missing_checks]
    if refused:
        # Nothing ran, so nothing was measured and nothing failed.
        verified_status = UNMEASURED_OUTCOME
    elif write_state == "failed" or acceptance == operation_acceptance.FAILED:
        verified_status = VERIFICATION_FAILED
    elif acceptance == operation_acceptance.UNMEASURED:
        verified_status = UNMEASURED_OUTCOME
    elif write_state == "committed":
        verified_status = VERIFIED
    else:
        verified_status = UNMEASURED_OUTCOME
    if committed:
        committed_status = "committed"
    elif (persisted and acceptance == operation_acceptance.PASSED
            and isinstance(project_model, Mapping)
            and project_model.get("status") == "failed"):
        committed_status = "project_model_commit_failed"
    elif persisted and acceptance == operation_acceptance.FAILED:
        committed_status = "readback_persisted_unverified"
    elif persisted:
        committed_status = "readback_persisted_unmeasured"
    else:
        committed_status = "not_committed"
    return {
        "planned": {"status": "planned", "patch_id": patch_id},
        "applied": {"status": ("applied" if applied == requested and applied
                               else "partial" if applied else "not_applied"),
                    "operations": applied, "requested": requested,
                    "complete": applied == requested},
        "verified": {"status": verified_status,
                     "acceptance_contracts_passed":
                         acceptance == operation_acceptance.PASSED,
                     "acceptance_status": acceptance,
                     "missing": missing_checks,
                     "failed": failed_checks,
                     "reason": reason},
        "committed": {"status": committed_status,
                      "generation": generation,
                      "project_model": project_model},
        "promoted": {"status": _promoted_status({"promotion": promotion})},
        "delivered": {"status": (delivery if isinstance(delivery, str)
                                 else str((delivery or {}).get("status")
                                          or "not_requested")
                                 if isinstance(delivery, Mapping)
                                 else "not_requested")},
    }


def standardize(receipt: dict[str, Any]) -> dict[str, Any]:
    """Add ordered lifecycle stages to a legacy patch receipt.

    A failed operation or acceptance contract cannot be described as
    verified, committed, or delivered. A shadow generation may still be
    persisted as forensic read-back after a failed verification.

    AXI-003: a contract nobody measured is not a contract that failed.
    The `verified` block now says which of the two happened, and a
    receipt that is merely unmeasured carries no recovery: there is
    nothing to compensate, only evidence to collect.
    """
    from library.tools import operation_acceptance

    out = dict(receipt)
    status = out.get("status")
    operations = out.get("operations") or []
    applied_count = sum(1 for row in operations
                        if row.get("failure") is None)
    requested_count = int(out.get("requested_operation_count", len(operations)))
    contracts = out.get("acceptance_contracts") or []
    acceptance, failed_checks, missing_checks = _acceptance_reading(contracts)
    contracts_ok = acceptance == operation_acceptance.PASSED
    write_state = _write_state(out)
    refused = _is_refusal(out)
    if refused:
        # Nothing ran, so nothing was measured and nothing failed.
        verified = False
        verified_status = UNMEASURED_OUTCOME
    elif acceptance == operation_acceptance.FAILED:
        verified = False
        verified_status = VERIFICATION_FAILED
    elif acceptance == operation_acceptance.UNMEASURED:
        # The acceptance was never measured.  A write that failed
        # carries no acceptance either - nothing was read back - and
        # the FAILED write is reported by the receipt's own status and
        # by `applied`, `committed` and `recovery`.  The six blocks
        # answer six questions, and this one answers only its own.
        verified = False
        verified_status = UNMEASURED_OUTCOME
    elif write_state == "committed":
        verified = True
        verified_status = VERIFIED
    else:
        # The receipt says nothing about the write's fate, so nothing
        # is established about it.
        verified = False
        verified_status = UNMEASURED_OUTCOME
    persisted = out.get("generation") is not None
    model_commit = out.get("project_model_commit", {"status": "not_required"})
    committed = persisted and verified and model_commit.get("status") != "failed"
    if "recovery" not in out:
        if write_state == "failed" or acceptance == operation_acceptance.FAILED:
            stopped = out.get("stopped")
            out["recovery"] = recovery(
                "api_uncertain" if stopped else "verification_failed",
                reason=(stopped.get("raised", "") if stopped else
                        "read-back or operation acceptance contract failed"))
        else:
            # An UNMEASURED acceptance owes no compensation: the write
            # is not known to be wrong, and a recovery on a working
            # build is the misclassification AXI-003 names.
            out["recovery"] = None
    out.setdefault("recovery_result", {
        "status": ("not_attempted" if verified else
                   "not_required" if verified_status == UNMEASURED_OUTCOME
                   else "recovery_required"),
        "reason": ("verified result" if verified else
                   "the acceptance evidence was not measured: nothing "
                   "established whether the write did what was asked"
                   if verified_status == UNMEASURED_OUTCOME else
                   "read-back and transaction history retained")})
    if verified_status == UNMEASURED_OUTCOME:
        verification_reason = (
            "the request was refused, so nothing ran and nothing was "
            "measured" if refused else
            "the acceptance was not measured"
            + (f": {len(missing_checks)} check(s) nobody measured"
               if missing_checks else ": the receipt carries no "
               "acceptance contract"))
    elif verified_status == VERIFICATION_FAILED:
        verification_reason = (
            "the acceptance failed"
            + (f": {len(failed_checks)} check(s) did not pass"
               if failed_checks else ""))
    else:
        verification_reason = "the acceptance was measured and passed"
    out.update({
        "validated": {"status": "validated" if out.get("base_generation") is not None else "not_validated",
                      "base_generation": out.get("base_generation")},
        "prepared": {"status": _prepared_status(out),
                     "journal": out.get("journal")},
        "executing": {"status": "completed" if requested_count == len(operations) else
                      ("partial" if operations else
                       "failed" if out.get("stopped") else "not_started"),
                      "stopped": out.get("stopped")},
    })
    out.update(lifecycle_blocks(
        applied=applied_count, requested=requested_count,
        acceptance=acceptance, failed_checks=failed_checks,
        missing_checks=missing_checks, committed=committed,
        persisted=persisted, write_state=write_state, refused=refused,
        generation=out.get("generation"),
        project_model=model_commit, promotion=out.get("promotion"),
        delivery=out.get("delivered"), patch_id=out.get("patch_id"),
        reason=verification_reason))
    promoted = _promoted_status(out)
    out["lifecycle"] = lifecycle(
        out, stage=("PROMOTED" if promoted == "promoted" else
                    "COMMITTED" if committed else
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
            "last_event": None, "project": fields.get("project", ""),
            "timeline": fields.get("timeline", ""),
            "write_attempted": False, "operations": {}})
        row["last_stage"] = fields.get("stage")
        if fields.get("stage") in WRITE_ATTEMPT_STAGES:
            row["write_attempted"] = True
        row["last_event"] = event
        if fields.get("project"):
            row["project"] = fields["project"]
        if fields.get("timeline"):
            row["timeline"] = fields["timeline"]
        if fields.get("stage") in {"operation_applying", "operation_applied"}:
            operation = fields.get("operation", fields.get("op"))
            if operation is None:
                operations = fields.get("operations") or ()
                index = fields.get("index")
                if isinstance(index, int) and 0 <= index < len(operations):
                    operation = operations[index]
            operation_id = (operation.get("op")
                            if isinstance(operation, Mapping) else operation)
            operation_row = {"index": fields.get("index"),
                             "op": operation_id}
            if fields.get("stage") == "operation_applying":
                operation_row["mutation_state"] = "uncertain"
            row["operations"][fields.get("index")] = operation_row
    return [{**row, "operations": list(row["operations"].values()),
             "status": "recovery_required",
             # A transaction with no terminal commit may have written:
             # its outcome is not observable, so it is an uncertain
             # mutation until the observed state is read.
             "conflict_class": "uncertain_mutation"}
            for row in transactions.values()
            if not _is_resolved({"stage": row["last_stage"],
                                 "status": (row["last_event"] or {})
                                 .get("fields", {}).get("status", "")})]
