"""Reversal semantics for a partially completed compound edit, and the
gate that says whether work may resume.

PRES-021 asked for reliable reversal of partially completed compound
edits. A compound edit applies several operations; when it fails
halfway, the operations that landed must be undoable, and the undo must
be RELIABLE: the operations come back in REVERSE order, each one is
judged by a fresh read rather than by a return value, and a failure to
reverse one operation never silently drops the rest - the remaining
operations are named, so the next actor knows what is still on the
timeline.

Three paths reverse today - the AXI compound compensation
(`resolve_axi_core`), the creative undo journal and the direct
transaction - and each grew its own statement of those rules. This
module owns the STATEMENT, so the rules are said once:

- `reversal_plan` reads the inverse of every operation that landed off
  the snapshot taken BEFORE the write, and refuses an operation whose
  inverse cannot be stated rather than guessing one;
- `reverse_each` is the driver: reverse order, one verified step at a
  time, and the remaining operations named when a step fails;
- `reversal_required` and `reversal_verified` are the two outcomes, in
  the shape the receipts already use.

PRES-022 asked that the restored state be verified INDEPENDENTLY
before resuming - verified by a fresh read compared against the state
before the edit, not by the return value of the code that performed the
restore. `verify_restored` is that comparison and `resume_gate` is the
gate that refuses to continue while a recovery is unresolved or the
restored state is unverified.

The module is offline and side-effect free: it plans, compares and
decides. The physical writes stay with the path that owns them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

#: A compound reversal undoes the operations in the opposite order to
#: the one they were applied in. The last thing applied is the first
#: thing undone, because each operation may depend on the one before it.
REVERSAL_ORDER = "reverse"

#: The two outcomes a reversal has.
RESTORED = "restored_original_verified"
RECOVERY_REQUIRED = "recovery_required"


# ── PRES-021: the reversal ────────────────────────────────────────────

def reversal_plan(*, operations: Sequence[Mapping],
                  before: Mapping) -> dict:
    """The inverse of the operations that landed, in reverse order.

    `operations` are the operations that RAN, in the order they ran.
    `before` is the snapshot taken before the write - the only place a
    previous value can be read from.

    Returns
    -------
    dict
        ``{"status", "steps", "refused", "reason"}`` where ``status`` is
        ``"reversible"`` when every operation has a stated inverse and
        ``"not_reversible"`` otherwise, ``steps`` are the inverses in
        the order they must be applied, and ``refused`` names every
        operation whose inverse could not be stated.

    An operation with no measured inverse is REFUSED, never guessed: a
    reversal that restores the wrong thing is worse than a reversal
    that says it cannot be done.
    """
    from library.tools import feedback_patch_apply

    steps: list = []
    refused: list = []
    for index, operation in enumerate(operations or ()):
        operation = dict(operation)
        try:
            inverse = feedback_patch_apply.inverse_operations([operation],
                                                               before)
        except feedback_patch_apply.UnrestorableOp as unrestorable:
            refused.append({
                "index": index,
                "operation": operation,
                "reason": str(unrestorable),
            })
            continue
        except Exception as exc:  # noqa: BLE001 - an unstatable inverse is refused
            refused.append({
                "index": index,
                "operation": operation,
                "reason": (f"the inverse of this operation could not be "
                           f"stated: {type(exc).__name__}: {exc}"),
            })
            continue
        steps.append({
            "index": index,
            "operation": operation,
            "inverse": [dict(op) for op in inverse],
        })
    steps.reverse()
    if refused:
        return {
            "status": "not_reversible",
            "steps": steps,
            "refused": refused,
            "reason": (f"{len(refused)} of {len(operations or ())} operations "
                       "have no stated inverse"),
        }
    return {
        "status": "reversible",
        "steps": steps,
        "refused": [],
        "reason": "",
    }


def reversal_required(*, reason: str, actions: Sequence[Mapping],
                      remaining: Sequence[Any]) -> dict:
    """The outcome when a reversal could not finish.

    The operations that were NOT reversed are named. They are still on
    the timeline, and a reversal that dropped them would leave the
    editor believing the timeline was whole.
    """
    remaining = list(remaining or ())
    return {
        "status": RECOVERY_REQUIRED,
        "reason": reason,
        "actions": [dict(action) for action in (actions or ())],
        "remaining_operations": remaining,
        "failed_operation_index": remaining[0] if remaining else None,
    }


def reversal_verified(*, actions: Sequence[Mapping],
                      independent_readback: str = "") -> dict:
    """The outcome when every step was reversed and verified."""
    return {
        "status": RESTORED,
        "actions": [dict(action) for action in (actions or ())],
        "remaining_operations": [],
        "independent_readback": independent_readback,
    }


def reverse_each(*, rows: Sequence[Mapping],
                 undo_one: Callable[[Mapping], Mapping]) -> dict:
    """Reverse `rows` in order, verifying each step, never dropping one.

    `rows` are the items to undo, ALREADY in the order they must be
    undone (the caller reverses them; this module states the rule, not
    the order of the caller's list). `undo_one(row)` performs one
    reversal and returns the record of what it did; it raises when the
    reversal did not verify.

    On the first failure the reversal stops - a later reversal may
    depend on an earlier one - and the outcome names every operation
    that is still on the timeline, the failed one included.
    """
    rows = list(rows or ())
    actions: list = []
    for position, row in enumerate(rows):
        try:
            action = undo_one(row)
        except Exception as exc:  # noqa: BLE001 - the failure is the report
            remaining = [row.get("operation_index") for row in rows[position:]]
            return reversal_required(
                reason=str(exc) or type(exc).__name__,
                actions=actions,
                remaining=remaining)
        actions.append(dict(action or {}))
    return reversal_verified(
        actions=actions,
        independent_readback=("each reversed operation verified its own "
                              "read-back"))


# ── PRES-022: independent verification, and the resume gate ───────────

def _tracks(state: Any) -> list:
    """The tracks of a snapshot, or the tracks themselves."""
    if isinstance(state, Mapping):
        return list(state.get("tracks") or ())
    return list(state or ())


def verify_restored(*, expected: Any, observed: Any,
                    include_markers: bool = False) -> dict:
    """Whether the restored state reads as the state before the edit.

    INDEPENDENT: the caller re-reads the timeline and this compares the
    two reads. Nothing here trusts the return value of the code that
    performed the restore, because a restore that cannot be proved is
    not a restore.

    `expected` is the state before the edit (a snapshot or its tracks);
    `observed` is the fresh read of the restored timeline. Returns
    ``{"verified", "reason", "diff"}``.
    """
    from library.tools import undo_journal

    before_rows = undo_journal.projection(_tracks(expected))
    after_rows = undo_journal.projection(_tracks(observed))
    wrong = undo_journal.projection_diff(before_rows, after_rows)
    if wrong:
        return {
            "verified": False,
            "reason": (f"the restored timeline does not read as the state "
                       f"before the edit: {wrong}"),
            "diff": wrong,
        }
    if include_markers:
        marker_diff = _marker_diff(expected, observed)
        if marker_diff:
            return {
                "verified": False,
                "reason": ("the restored timeline does not carry the same "
                           f"markers: {marker_diff}"),
                "diff": marker_diff,
            }
    return {"verified": True, "reason": "", "diff": []}


def _marker_diff(expected: Any, observed: Any) -> list:
    """Every marker present in one state and not the other."""
    def markers(state: Any) -> dict:
        if isinstance(state, Mapping):
            block = state.get("markers") or {}
            rows = list(block.get("timeline") or ()) + \
                list(block.get("notes") or ())
        else:
            rows = []
        out = {}
        for row in rows:
            key = (int(row.get("frame") or 0), str(row.get("name") or ""),
                   str(row.get("level") or "timeline"))
            out[key] = row
        return out

    before, after = markers(expected), markers(observed)
    diff = []
    for key in sorted(before.keys() | after.keys()):
        if before.get(key) != after.get(key):
            diff.append({"key": list(key),
                         "expected": before.get(key),
                         "observed": after.get(key)})
    return diff


def resume_gate(*, unresolved: Sequence[Mapping] = (),
                restored: Mapping | None = None) -> dict:
    """Whether work may resume after a recovery.

    Two things block a resume: a transaction that is still unresolved
    (its outcome is not observable, so nobody knows what is on the
    timeline), and a restored state that was not independently verified
    (the restore may have restored the wrong thing).

    Returns ``{"resumable", "reason", "blockers"}``.
    """
    blockers: list = []
    for row in (unresolved or ()):
        transaction_id = str(row.get("transaction_id") or "?")
        blockers.append(
            f"transaction {transaction_id} is unresolved "
            f"({row.get('status') or 'recovery_required'})")
    if restored is not None and not restored.get("verified"):
        blockers.append("the restored state was not verified: "
                        + str(restored.get("reason") or "no reason given"))
    return {
        "resumable": not blockers,
        "reason": "; ".join(blockers),
        "blockers": blockers,
    }


# ── PRES-023: the recovery artifact, kept until the edit is settled ───

def hold_recovery_archive(project_folder: str, path: str, *,
                          transaction_id: str,
                          project_id: str = "",
                          run_id: str = "",
                          job_id: str = "",
                          reason: str = "") -> dict:
    """Register a recovery archive and HOLD it against its transaction.

    The archive is the only copy of an edit that did not finish, so it
    must survive the cleanup until the affected edit is safely committed
    or the recovery is explicitly abandoned (PRES-023). Holding it does
    two things: the artifact lifecycle records WHICH transaction still
    needs it, and the cleanup refuses to collect it.

    Best effort by construction, like the export that produced it: a
    failed hold is REPORTED, never raised, because a cleanup that failed
    must not mask the refusal it is cleaning up.

    Returns ``{"held", "artifact_id", "transaction_id", "reason"}``.
    """
    from datetime import UTC, datetime

    from library.tools import artifact_lifecycle

    if not transaction_id:
        return {"held": False, "artifact_id": "", "transaction_id": "",
                "reason": "a recovery archive must be held for a named "
                          "transaction"}
    try:
        identity = artifact_lifecycle.FileIdentity.capture(path)
    except (OSError, artifact_lifecycle.LifecycleRefused) as exc:
        return {"held": False, "artifact_id": "", "transaction_id": "",
                "reason": f"the recovery archive is not readable: {exc}"}
    owner = project_id or "project"
    holder = run_id or transaction_id
    artifact_id = artifact_lifecycle.stable_artifact_id(
        owner, holder, job_id or holder, "recovery_archive",
        f"{path}::{transaction_id}")
    record = artifact_lifecycle.ArtifactRecord(
        artifact_id=artifact_id, kind="recovery_archive", project_id=owner,
        run_id=holder, job_id=job_id or holder, path=str(path),
        created_at=datetime.now(UTC).isoformat(), identity=identity,
        retention_class="recovery", dependencies=(str(transaction_id),),
        pinned=True, regenerable=False, regeneration_method="")
    try:
        artifact_lifecycle.register_artifact(project_folder, record)
        held = artifact_lifecycle.hold_for_transaction(
            project_folder, artifact_id, transaction_id=str(transaction_id),
            reason=reason or "recovery evidence for an edit that has not "
                             "been settled")
    except artifact_lifecycle.LifecycleRefused as refused:
        return {"held": False, "artifact_id": artifact_id,
                "transaction_id": str(transaction_id),
                "reason": str(refused)}
    except Exception as exc:  # noqa: BLE001 - a failed hold is reported
        return {"held": False, "artifact_id": artifact_id,
                "transaction_id": str(transaction_id),
                "reason": f"the recovery archive could not be held: {exc}"}
    return {"held": True, "artifact_id": held["artifact_id"],
            "state": held["state"],
            "transaction_id": held["transaction_id"], "path": str(path),
            "reason": ""}


def release_recovery_archive(project_folder: str, artifact_id: str, *,
                             transaction_id: str, outcome: str) -> dict:
    """Release a held recovery archive.

    `committed` keeps it for good (`retained`); `abandoned` makes it
    disposable again, and it is the ONLY way a recovery artifact becomes
    collectable. The hold is released only by the transaction it names,
    so one edit cannot release another's evidence.
    """
    from library.tools import artifact_lifecycle

    try:
        return artifact_lifecycle.release_hold(
            project_folder, artifact_id, transaction_id=str(transaction_id),
            outcome=outcome)
    except artifact_lifecycle.LifecycleRefused as refused:
        return {"artifact_id": artifact_id, "state": "",
                "transaction_id": str(transaction_id), "outcome": outcome,
                "released": False, "reason": str(refused)}


def held_recovery_archives(project_folder: str, *,
                           transaction_id: str = "") -> list[dict]:
    """The recovery artifacts still held, optionally for one transaction."""
    from library.tools import artifact_lifecycle

    return [row for row in artifact_lifecycle.held_artifacts(
        project_folder, transaction_id=transaction_id)
        if row.get("kind") == "recovery_archive"]
