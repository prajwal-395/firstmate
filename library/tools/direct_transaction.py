"""Fail-closed executor for separately qualified in-place transactions.

This path deliberately has its own recovery qualification. Existing EditPatch
qualification proves that a write and its read-back work; it does not prove
that a compensating write is safe after a partial failure.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class DirectTransactionRefused(RuntimeError):
    """The transaction lacks the evidence or state needed for a safe write."""


class DirectTransactionFailed(RuntimeError):
    """A direct write failed; recovery evidence remains in the journal."""


@dataclass(frozen=True)
class RecoveryQualification:
    """Evidence specific to direct application and compensation."""

    operation: str
    inverse: str
    evidence: tuple[str, ...]
    compensator: Callable[[Mapping[str, Any], Any], Any]


# Empty by design. A normal native inverse declaration or EditPatch
# qualification is not evidence that direct compensation is safe.
RECOVERY_QUALIFICATIONS: dict[str, RecoveryQualification] = {}


def transaction_history(project_folder: str, transaction_id: str) -> list[dict]:
    """Return the durable events for one direct transaction, oldest first."""
    from library.tools import event_log

    return [{**event.get("fields", {}), "_at": event.get("at")}
            for event in event_log.read_events(project_folder)
            if event.get("fields", {}).get("direct_transaction")
            and str(event.get("fields", {}).get("transaction_id"))
            == str(transaction_id)]


def _transaction_head(project_folder: str, transaction_id: str) -> dict:
    history = transaction_history(project_folder, transaction_id)
    if not history:
        raise DirectTransactionRefused(
            f"direct transaction {transaction_id!r} is not in the recovery journal")
    head = history[-1]
    if head.get("stage") != "committed":
        raise DirectTransactionRefused(
            f"direct transaction {transaction_id!r} is {head.get('stage')!r}; "
            "unfinished recovery blocks undo and redo")
    if head.get("recovered_state") is not None and not head.get("action"):
        raise DirectTransactionRefused(
            f"direct transaction {transaction_id!r} was compensated after "
            "a failed apply and is not a committed creative edit")
    return head


def read_transaction(project_folder: str, transaction_id: str) -> dict:
    """Read one durable transaction's latest stage and intent."""
    return dict(_transaction_head(project_folder, transaction_id))


def explain(project_folder: str, transaction_id: str, *, action: str) -> dict:
    """Describe a direct transaction undo or redo without touching Resolve."""
    if action not in {"undo", "redo"}:
        raise ValueError("action must be 'undo' or 'redo'")
    head = _transaction_head(project_folder, transaction_id)
    operations = head.get("operations") or []
    qualifications = []
    blockers = []
    for operation in operations:
        try:
            qualifications.append(_qualification_for(operation))
        except DirectTransactionRefused as refused:
            blockers.append(str(refused))
    if action == "undo" and head.get("action") == "undo":
        blockers.append("the transaction is already undone")
    if action == "redo" and head.get("action") != "undo":
        blockers.append("the transaction has no current undo to redo")
    return {"transaction_id": transaction_id, "action": action,
            "mechanism": ("verified native compensation" if action == "undo"
                          else "re-apply the recorded native operations"),
            "operations": [operation.get("op") for operation in operations],
            "recovery_evidence": [list(item.evidence)
                                  for item in qualifications],
            "intervening": "incompatible live state refuses",
            "blockers": blockers,
            "verified": not blockers}


def undo_committed(*, project_folder: str, transaction_id: str,
                   observe: Callable[[], Any],
                   apply: Callable[[Mapping[str, Any]], Any],
                   same_state: Callable[[Any, Any], bool]) -> dict:
    """Compensate one committed direct transaction with verified read-back."""
    from library.tools import event_log, resolve_lock

    if not resolve_lock.exclusive_held():
        raise DirectTransactionRefused(
            "direct transaction undo requires the exclusive Resolve lease")
    head = _transaction_head(project_folder, transaction_id)
    if head.get("action") == "undo":
        raise DirectTransactionRefused(
            f"direct transaction {transaction_id!r} is already undone")
    operations = [dict(operation) for operation in head.get("operations") or []]
    qualifications = [_qualification_for(operation) for operation in operations]
    current = copy.deepcopy(observe())
    expected = head.get("observed_state")
    if expected is None or not same_state(expected, current):
        raise DirectTransactionRefused(
            "the live timeline no longer reads as the direct transaction left it")

    def journal(stage: str, *, event_action: str = "undo", **fields) -> None:
        event_log.emit(project_folder, event_log.EDIT_TRANSACTION_STAGE,
                       direct_transaction=True,
                       transaction_id=transaction_id, stage=stage,
                       action=event_action,
                       target_transaction_id=transaction_id,
                       operations=operations,
                       project=head.get("project", ""),
                       timeline=head.get("timeline", ""),
                       base_generation=head.get("base_generation"), **fields)

    journal("undo_prepared", original_state=head.get("original_state"),
            observed_state=current,
            recovery_evidence=head.get("recovery_evidence"))
    try:
        for operation, qualification in zip(reversed(operations),
                                            reversed(qualifications)):
            qualification.compensator(operation, head.get("original_state"))
        restored = copy.deepcopy(observe())
        if not same_state(head.get("original_state"), restored):
            raise RuntimeError("compensation read-back differs from original state")
    except Exception as failure:
        try:
            for operation in operations:
                apply(operation)
            reapplied = copy.deepcopy(observe())
            if not same_state(expected, reapplied):
                raise RuntimeError(
                    "re-applying the transaction did not restore its committed state")
        except Exception as recovery_failure:
            journal("undo_recovery_required", failure=repr(failure),
                    recovery_failure=repr(recovery_failure),
                    final_state=copy.deepcopy(observe()))
            raise DirectTransactionFailed(
                "direct transaction undo and recovery failed; evidence remains "
                "in the journal") from recovery_failure
        journal("committed", event_action="redo",
                result_state=head.get("observed_state"),
                original_state=head.get("original_state"),
                recovery_evidence=head.get("recovery_evidence"),
                observed_state=reapplied, verified=False,
                undo_failure=repr(failure))
        raise DirectTransactionFailed(
            f"direct transaction undo failed and was restored: {failure}") \
            from failure
    journal("committed", result_state=restored,
            original_state=head.get("original_state"),
            recovery_evidence=head.get("recovery_evidence"),
            observed_state=current, verified=True)
    return {"transaction_id": transaction_id, "action": "undo",
            "status": "verified", "before": current, "after": restored}


def redo_committed(*, project_folder: str, transaction_id: str,
                   observe: Callable[[], Any],
                   apply: Callable[[Mapping[str, Any]], Any],
                   verify: Callable[[Sequence[Mapping[str, Any]], Any, Any], Any],
                   same_state: Callable[[Any, Any], bool]) -> dict:
    """Re-apply a compensated direct transaction and verify its read-back."""
    from library.tools import event_log, resolve_lock

    if not resolve_lock.exclusive_held():
        raise DirectTransactionRefused(
            "direct transaction redo requires the exclusive Resolve lease")
    head = _transaction_head(project_folder, transaction_id)
    if head.get("action") != "undo":
        raise DirectTransactionRefused(
            f"direct transaction {transaction_id!r} has no current undo to redo")
    operations = [dict(operation) for operation in head.get("operations") or []]
    qualifications = [_qualification_for(operation) for operation in operations]
    before = copy.deepcopy(observe())
    expected = head.get("result_state")
    if expected is None or not same_state(expected, before):
        raise DirectTransactionRefused(
            "the live timeline changed after the direct transaction undo")

    def journal(stage: str, *, event_action: str = "redo", **fields) -> None:
        event_log.emit(project_folder, event_log.EDIT_TRANSACTION_STAGE,
                       direct_transaction=True,
                       transaction_id=transaction_id, stage=stage,
                       action=event_action,
                       target_transaction_id=transaction_id,
                       operations=operations,
                       project=head.get("project", ""),
                       timeline=head.get("timeline", ""),
                       base_generation=head.get("base_generation"), **fields)

    journal("redo_prepared", original_state=before,
            recovery_evidence=head.get("recovery_evidence"))
    try:
        for operation in operations:
            apply(operation)
        after = copy.deepcopy(observe())
        verdict = verify(operations, before, after)
        if verdict:
            raise RuntimeError(str(verdict))
    except Exception as failure:
        try:
            for operation, qualification in zip(reversed(operations),
                                                reversed(qualifications)):
                qualification.compensator(operation, before)
            restored = copy.deepcopy(observe())
            if not same_state(before, restored):
                raise RuntimeError("redo compensation did not restore its base")
        except Exception as recovery_failure:
            journal("redo_recovery_required", failure=repr(failure),
                    recovery_failure=repr(recovery_failure),
                    final_state=copy.deepcopy(observe()))
            raise DirectTransactionFailed(
                "direct transaction redo and recovery failed; evidence remains") \
                from recovery_failure
        journal("committed", event_action="undo", result_state=restored,
                original_state=before,
                recovery_evidence=head.get("recovery_evidence"),
                observed_state=restored, verified=False,
                failure=repr(failure))
        raise DirectTransactionFailed(
            f"direct transaction redo failed and was compensated: {failure}") \
            from failure
    journal("committed", original_state=before,
            recovery_evidence=head.get("recovery_evidence"),
            result_state=before, observed_state=after,
            verified=True)
    return {"transaction_id": transaction_id, "action": "redo",
            "status": "verified", "before": before, "after": after}


def _qualification_for(operation: Mapping[str, Any]) -> RecoveryQualification:
    from library.tools import edit_algebra

    op_id = str(operation.get("op", ""))
    evidence = RECOVERY_QUALIFICATIONS.get(op_id)
    if (evidence is None or evidence.operation != op_id
            or not evidence.evidence):
        raise DirectTransactionRefused(
            f"{op_id or 'operation'} has no direct recovery qualification")
    blockers = edit_algebra.direct_blockers((op_id,), "edit_patch")
    if blockers:
        raise DirectTransactionRefused("; ".join(blockers))
    declared = edit_algebra.get(op_id)
    if declared is None:
        raise DirectTransactionRefused(f"unknown operation {op_id!r}")
    inverse = op_id if declared.inverse.self_inverse else declared.inverse.op
    if inverse != evidence.inverse:
        raise DirectTransactionRefused(
            f"{op_id} recovery qualification does not match its declared inverse")
    if not edit_algebra.proven_inverse(op_id, "edit_patch"):
        raise DirectTransactionRefused(
            f"{op_id} inverse is not proven on edit_patch")
    return evidence


def execute(*, decision, operations: Sequence[Mapping[str, Any]],
            project_folder: str, transaction_id: str | None,
            observe: Callable[[], Any], apply: Callable[[Mapping[str, Any]], Any],
            verify: Callable[[Sequence[Mapping[str, Any]], Any, Any], Any],
            same_state: Callable[[Any, Any], bool],
            project_name: str = "", timeline_name: str = "",
            base_generation: int | None = None) -> dict[str, Any]:
    """Execute a qualified direct edit and compensate only with proven evidence.

    Callbacks are adapter-owned native operations. ``observe`` and ``apply``
    must run while the caller holds Resolve's exclusive lease. The journal
    callback must durably append to the existing project event log.
    """
    from library.tools import event_log, resolve_lock

    if getattr(decision, "mode", None) != "direct":
        raise DirectTransactionRefused("router did not select direct mode")
    if not resolve_lock.exclusive_held():
        raise DirectTransactionRefused(
            "direct transaction requires the broker's exclusive Resolve lease")
    if not project_folder:
        raise DirectTransactionRefused(
            "direct transaction requires a durable project recovery journal")
    txid = transaction_id or str(uuid.uuid4())
    prior_states: dict[str, str] = {}
    prior_events: dict[str, Mapping[str, Any]] = {}
    for event in event_log.read_events(project_folder):
        fields = event.get("fields", {})
        if fields.get("direct_transaction"):
            prior_id = str(fields["transaction_id"])
            prior_states[prior_id] = str(fields["stage"])
            prior_events[prior_id] = fields
    if prior_states.get(txid) == "committed":
        prior = prior_events[txid]
        requested_intent = tuple(dict(op) for op in operations)
        if list(requested_intent) != prior.get("operations"):
            raise DirectTransactionRefused(
                "transaction id was already used for different operations")
        observed = prior.get("observed_state", prior.get("recovered_state"))
        status = "compensated" if "recovered_state" in prior else "verified"
        return {"transaction_id": txid, "status": status,
                "before": prior["original_state"],
                "after": observed, "idempotent": True,
                "recovery_evidence": prior["recovery_evidence"]}
    unresolved = [txid for txid, stage in prior_states.items()
                  if stage != "committed"]
    if unresolved:
        raise DirectTransactionRefused(
            "unfinished direct transaction recovery blocks writes: "
            + ", ".join(unresolved))
    requested = tuple(copy.deepcopy(dict(op)) for op in operations)
    if not requested:
        raise DirectTransactionRefused("direct transaction has no operations")
    qualifications = tuple(_qualification_for(op) for op in requested)
    if len({q.operation for q in qualifications}) != 1:
        raise DirectTransactionRefused(
            "direct transaction operations must share one qualified inverse")

    before = copy.deepcopy(observe())
    record: dict[str, Any] = {
        "direct_transaction": True,
        "transaction_id": txid, "stage": "prepared",
        "operations": requested, "original_state": before,
        "project": project_name, "timeline": timeline_name,
        "base_generation": base_generation,
        "recovery_evidence": [list(q.evidence) for q in qualifications],
    }
    # A journal failure must happen before the first native mutation.
    def journal(stage_record: Mapping[str, Any]) -> None:
        event_log.emit(project_folder, event_log.EDIT_TRANSACTION_STAGE,
                       task="direct-transaction", **dict(stage_record))

    journal(record)
    applied: list[Mapping[str, Any]] = []
    try:
        for index, operation in enumerate(requested):
            # An API may mutate and then raise. Record the attempted op
            # before calling it so recovery treats that write as uncertain.
            record.update(stage="operation_applying", index=index,
                          operation=operation)
            journal(record)
            applied.append(operation)
            apply(operation)
            record.update(stage="operation_applied", index=index,
                          operation=operation)
            journal(record)
        after = observe()
        verdict = verify(requested, before, after)
        if verdict:
            raise DirectTransactionFailed(str(verdict))
        record.update(stage="verified", observed_state=copy.deepcopy(after))
        journal(record)
        record["stage"] = "committed"
        journal(record)
        return {"transaction_id": txid, "status": "verified",
                "before": before, "after": after,
                "recovery_evidence": record["recovery_evidence"]}
    except Exception as failure:  # preserve the failed state and compensation outcome
        actual = copy.deepcopy(observe())
        record.update(stage="compensating", observed_state=actual,
                      failure=f"{type(failure).__name__}: {failure}")
        try:
            journal(record)
        except OSError as journal_error:
            # The prepared and per-operation intent events remain durable;
            # still attempt the proven inverse after a later journal outage.
            record["journal_error"] = str(journal_error)
        try:
            for operation, qualification in zip(reversed(applied),
                                                reversed(qualifications)):
                qualification.compensator(operation, before)
            recovered = observe()
            if not same_state(before, recovered):
                raise RuntimeError(
                    "compensation read-back differs from the captured original")
        except Exception as compensation_failure:
            record.update(stage="recovery_required",
                          compensation_failure=(
                              f"{type(compensation_failure).__name__}: "
                              f"{compensation_failure}"),
                          final_state=copy.deepcopy(observe()))
            try:
                journal(record)
            except OSError as journal_error:
                record["journal_error"] = str(journal_error)
            raise DirectTransactionFailed(
                "compensation failed; recovery evidence was retained and "
                "further unsafe writes must be refused") from compensation_failure
        record.update(stage="compensated", recovered_state=copy.deepcopy(recovered))
        try:
            journal(record)
        except OSError as journal_error:
            record["journal_error"] = str(journal_error)
        record["stage"] = "committed"
        try:
            journal(record)
        except OSError as journal_error:
            record["journal_error"] = str(journal_error)
        raise DirectTransactionFailed(
            f"transaction failed and was compensated: {failure}") from failure
