"""What KIND of refusal this is, so the host knows whether to ask or to stop.

A refusal is not one thing. Three of them look alike from the outside
and mean opposite things to the host:

* `missing_editorial_information` - the work needs a judgement nobody has
  made yet. The host can ASK, and the answer unblocks it.
* `unsupported_execution` - the work is understood and there is no
  eligible way to do it. More information will not help; the capability
  is missing, and pretending otherwise is a lie the host would repeat.
* `needs_decision` - the work is fine and it would change work the
  creator already approved. The host must ask the CREATOR, not the model.

Collapsing these into one "refused" is what makes a host either give up
on a question it could have asked, or keep asking about a capability that
does not exist. The kinds are the vocabulary; `classify` reads a refusal
and says which one it is, from STRUCTURE - the kind the refusing site
declared, the requirement kind that was unmet, the transaction's own
recovery type, the execution refusal's category - and never from the
prose, which is written for a person.

THE OTHER AXIS
--------------
`library/tools/edit_conflict.py` owns the four ways an EDIT fails to
LAND: a stale plan, a concurrent modification, a failed write, an
uncertain one. That is a different question from the one here - a
conflict says the world moved, a kind here says why the work did not
happen - and a refusal that carries a `conflict_class` has already been
classified on that axis. `CONFLICT_CLASS_KINDS` maps that class onto the
kind a task refuses with, and the class travels on in the result, so
one answer reaches the host without either vocabulary being restated.
"""

from __future__ import annotations

from collections.abc import Mapping

KINDS = (
    "missing_editorial_information",
    "unsupported_execution",
    "missing_prerequisite",
    "needs_decision",
    "stale_state",
    "conflict",
    "execution_failed",
    "environment",
    "unknown",
)

RECOVERY_CLASSES = {
    "missing_editorial_information": "needs_question",
    "unsupported_execution": "blocked",
    "missing_prerequisite": "needs_question",
    "needs_decision": "needs_question",
    "stale_state": "auto_recoverable",
    "conflict": "needs_question",
    "execution_failed": "needs_question",
    "environment": "blocked",
    "unknown": "blocked",
}

CODE_KINDS = {
    "REN_ASSIGNMENT_DECISION_REQUIRED": "needs_decision",
    "REN_STALE_PATCH": "stale_state",
}
"""A refusal family that knows its own kind names it in its code.

A code is a machine-readable string a family sets on purpose, so this
table reads a DECLARATION, not a guess about the message. A family whose
kind is not here is classified by the structures below.
"""

CATEGORY_KINDS = {
    "unsupported": "unsupported_execution",
    "stale": "stale_state",
    "conflict": "conflict",
    "backend_failure": "execution_failed",
    "verification_failed": "conflict",
    "recovery_required": "conflict",
}
"""`transaction_orchestration.ExecutionRefusal`'s categories, read as kinds.

The Resolve path refuses with a CATEGORY - the shared vocabulary for
every backend - and an unsupported operation says so there. This table
reads that category instead of re-deriving it from the message.
"""

RECOVERY_TYPE_KINDS = {
    "unsupported": "unsupported_execution",
    "stale_generation": "stale_state",
    "collateral_change": "conflict",
    "api_uncertain": "conflict",
    "verification_failed": "conflict",
    "target_not_found": "missing_prerequisite",
    "ambiguous_target": "needs_decision",
    "replan_target": "needs_decision",
}
"""A transaction receipt's own recovery type, read as a kind.

`transaction_receipt.RECOVERY_ACTIONS` is the vocabulary the Resolve
path already writes; an unsupported operation says so there, and this
table reads it rather than re-deriving it from prose.
"""

CONFLICT_CLASS_KINDS = {
    "stale_plan": "stale_state",
    "concurrent_modification": "conflict",
    "uncertain_mutation": "conflict",
    "execution_failure": "execution_failed",
}
"""`edit_conflict`'s classes, read as kinds.

`library/tools/edit_conflict.py` owns the OTHER axis - the four ways an
edit fails to LAND - and a refusal that carries a `conflict_class` has
already been classified on that axis. This table maps that class onto
the kind a TASK refuses with, so a host reads one answer instead of two
vocabularies it has to reconcile. The class itself travels on in the
result, because the axis it names is still the operator's to read.
"""


def _kind(kind: str, reason: str, next_step: str,
          conflict_class: str = "") -> dict:
    return {"kind": kind, "reason": reason, "next": next_step,
            "conflict_class": conflict_class,
            "recovery_class": RECOVERY_CLASSES.get(kind, "blocked")}


def classify(refusal: object, *, operation_id: str = "") -> dict:
    """Say which kind of refusal this is.

    `refusal` may be a `RenRefusal`, a `RenRefusal.to_dict()`, an
    `OperationResult`, an `ExecutionRefusal` or a transaction receipt -
    whichever the caller holds. The kinds are read from the structure
    each of those carries, never from the prose.
    """
    if refusal is None:
        return _kind("unknown", "nothing was refused", "")
    if hasattr(refusal, "refusal_kind"):
        return _from_operation_result(refusal)
    if isinstance(refusal, Mapping):
        return _from_mapping(refusal, operation_id=operation_id)
    category = str(getattr(refusal, "category", "") or "")
    if category in CATEGORY_KINDS:
        return _kind(CATEGORY_KINDS[category], str(getattr(refusal, "reason", "")
                                                   or getattr(refusal, "what", "")),
                     str(getattr(refusal, "fix", "") or ""))
    to_dict = getattr(refusal, "to_dict", None)
    if callable(to_dict):
        return _from_mapping(to_dict(), operation_id=operation_id)
    code = str(getattr(refusal, "code", "") or "")
    if code:
        return _from_code(code, str(getattr(refusal, "what", "")
                                     or getattr(refusal, "message", "")))
    recovery_class = str(getattr(refusal, "recovery_class", "") or "")
    if recovery_class == "needs_question":
        return _kind("missing_editorial_information",
                     str(getattr(refusal, "what", "") or ""),
                     str(getattr(refusal, "fix", "") or ""))
    return _kind("unknown", str(getattr(refusal, "what", "") or ""),
                 str(getattr(refusal, "fix", "") or ""))


def _from_code(code: str, reason: str) -> dict:
    kind = CODE_KINDS.get(code)
    if kind is not None:
        return _kind(kind, reason, "")
    return _kind("unknown", reason, "")


def _from_operation_result(result) -> dict:
    kind = str(getattr(result, "refusal_kind", "") or "")
    reason = str(getattr(result, "refusal_reason", "") or
                 getattr(result, "error", "") or "")
    if kind in KINDS:
        return _kind(kind, reason, str(getattr(result, "fix", "") or ""))
    unsatisfied = list(getattr(result, "unsatisfied", ()) or ())
    if unsatisfied:
        kinds = {str(getattr(item, "kind", "")) for item in unsatisfied}
        kind = ("environment" if "environment" in kinds
                else "missing_prerequisite")
        return _kind(kind, reason, str(getattr(result, "fix", "") or ""))
    return _kind("unknown", reason, str(getattr(result, "fix", "") or ""))


def _from_mapping(refusal: Mapping, *, operation_id: str = "") -> dict:
    conflict_class = str(refusal.get("conflict_class") or "")
    if conflict_class in CONFLICT_CLASS_KINDS:
        return _kind(CONFLICT_CLASS_KINDS[conflict_class],
                     str(refusal.get("what") or ""),
                     str(refusal.get("fix") or ""), conflict_class)
    code = str(refusal.get("code") or "")
    if code:
        mapped = CODE_KINDS.get(code)
        if mapped is not None:
            return _kind(mapped, str(refusal.get("what") or ""),
                         str(refusal.get("fix") or ""), conflict_class)
    recovery = refusal.get("recovery")
    if isinstance(recovery, Mapping):
        kind = RECOVERY_TYPE_KINDS.get(str(recovery.get("type") or ""))
        if kind is not None:
            return _kind(kind, str(recovery.get("reason") or
                                   refusal.get("what") or ""),
                         str(recovery.get("action") or ""))
    if refusal.get("refusal_kind") in KINDS:
        return _kind(str(refusal["refusal_kind"]),
                     str(refusal.get("what") or ""),
                     str(refusal.get("fix") or ""))
    unsatisfied = refusal.get("unsatisfied")
    if isinstance(unsatisfied, list) and unsatisfied:
        return _kind("missing_prerequisite",
                     str(refusal.get("what") or ""),
                     str(refusal.get("fix") or ""))
    if str(refusal.get("recovery_class") or "") == "needs_question":
        return _kind("missing_editorial_information",
                     str(refusal.get("what") or ""),
                     str(refusal.get("fix") or ""))
    return _kind("unknown", str(refusal.get("what") or ""),
                 str(refusal.get("fix") or ""))
