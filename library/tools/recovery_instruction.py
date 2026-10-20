"""AGENT-015: one typed recovery instruction, so no retry is improvised.

Three vocabularies already answered three different questions about a
failure, and nothing joined them:

- `ren_refusal.RECOVERY_CLASS` - should the host ACT, ASK or WAIT?
- `transaction_orchestration.ExecutionRefusal.CATEGORIES` - what KIND of
  failure was this?
- `transaction_receipt.RECOVERY_ACTIONS` - what is the next concrete ACTION?

The host had to hold all three in its head and improvise the join. That
is how a stale base became "try it again" and a failed read-back became
"retry the same way" - the two things the resolve-axi skill spends two
paragraphs warning against. The join is the missing piece, so this module
owns it: the one table that turns a failure CATEGORY into a recovery
TYPE, and a recovery TYPE into an action and a recovery class.

One instruction shape comes out of it:

    {"category": ..., "recovery_class": ..., "action": ..., "reason": ...}

`transaction_receipt.RECOVERY_ACTIONS` stays the ONE action vocabulary -
this module reads it and never lists an action of its own. The mapping
here is the one the resolve-axi skill already states in prose, so the
skill and the code cannot disagree about what a `stale` means.

The categories are the execution refusal vocabulary. `broken` is carried
here because a live state nobody could read is neither a stale base nor
a conflicting edit; it is refused as its own kind rather than coerced
into `unsupported`, which is the one category a rebuild may follow.

`instruction()` REFUSES a category it does not know. A failure kind with
no named recovery is not something the host should improvise around, and
silently falling back to a generic action would put back exactly the
guess this module exists to remove.
"""
from __future__ import annotations

from library.tools import transaction_receipt
from library.tools.ren_refusal import RenRefusal

RECOVERY_CLASSES = ("auto_recoverable", "needs_question", "blocked")
"""The host's three moves, re-exported from `ren_refusal` so a caller
that reaches for a recovery instruction does not have to know which
module owns the class vocabulary."""


CATEGORY_RECOVERY_TYPE = {
    "unsupported": "unsupported",
    "stale": "stale_generation",
    "conflict": "conflicting_state",
    "backend_failure": "api_uncertain",
    "verification_failed": "verification_failed",
    "recovery_required": "recovery_failed",
    "broken": "api_uncertain",
}
"""The join: a failure category names the recovery TYPE that answers it.

`unsupported` and `api_uncertain` are already recovery types. The rest
are failures whose recovery is one of the existing types under another
name - a stale base is a `stale_generation`, a failed read-back is a
`verification_failed`, a compensation that did not land is a
`recovery_failed`.
"""


CATEGORY_RECOVERY_CLASS = {
    "unsupported": "blocked",
    "stale": "auto_recoverable",
    "conflict": "needs_question",
    "backend_failure": "needs_question",
    "verification_failed": "needs_question",
    "recovery_required": "blocked",
    "broken": "needs_question",
}
"""The host's move for each failure category.

`unsupported` is BLOCKED: there is no route, so there is nothing to
recover to. `stale` is AUTO_RECOVERABLE: re-read the state and prepare a
fresh request is the named safe recovery. `conflict` and `verification
failed` NEED A QUESTION: the current state is preserved and the intent
has to be settled before anything else moves. `recovery_required` is
BLOCKED: editing stops until the state is proven restored.
"""


class UnknownRecoveryCategory(ValueError):
    """A failure category with no named recovery instruction."""


CATEGORY_RETRYABLE = {
    "unsupported": False,
    "stale": True,
    "conflict": False,
    "backend_failure": False,
    "verification_failed": False,
    "recovery_required": False,
    "broken": False,
}
"""Whether the SAME call may be repeated for this failure category.

Only a stale base qualifies: the intent did not change, the state the
request was written against did, so preparing it again against the new
base is the same call. Everything else needs the named recovery first -
a conflict needs the intent settled, a backend failure needs the
observed state inspected, a failed read-back needs a replan. A refusal
that reports `retryable: false` because nothing decided is worse than
one that says so, so this table decides it and every other category is
`False` on purpose.
"""


def recovery_class_for(category: str) -> str:
    """The host's move for a failure category."""
    if category not in CATEGORY_RECOVERY_CLASS:
        raise UnknownRecoveryCategory(
            f"unknown failure category {category!r}; one of "
            f"{sorted(CATEGORY_RECOVERY_CLASS)}")
    return CATEGORY_RECOVERY_CLASS[category]


def instruction(category: str, *, reason: str = "") -> dict[str, str]:
    """The typed next step for a failure category.

    The action comes from `transaction_receipt.RECOVERY_ACTIONS`, so the
    action vocabulary has one owner, and the recovery class comes from
    the table above, so "what do I do now" has one answer.
    """
    if category not in CATEGORY_RECOVERY_TYPE:
        raise UnknownRecoveryCategory(
            f"unknown failure category {category!r}; one of "
            f"{sorted(CATEGORY_RECOVERY_TYPE)}")
    recovery_type = CATEGORY_RECOVERY_TYPE[category]
    recovery = transaction_receipt.recovery(recovery_type, reason=reason)
    return {
        "category": category,
        "recovery_type": recovery_type,
        "recovery_class": recovery_class_for(category),
        "action": recovery["action"],
        "reason": recovery["reason"],
    }


def refusal_instruction(refused: RenRefusal, *, category: str = "",
                        reason: str = "") -> dict[str, str]:
    """The typed next step for a `RenRefusal` that names a category.

    A refusal that already carries a recovery class keeps it - the class
    was decided where the refusal was raised, and re-deciding it here
    would be a second opinion. A refusal with no class takes the one its
    category names.
    """
    if not category:
        return {
            "category": "",
            "recovery_type": "",
            "recovery_class": refused.recovery_class,
            "action": "",
            "reason": reason or refused.why,
        }
    typed = instruction(category, reason=reason or refused.why)
    typed["recovery_class"] = refused.recovery_class
    return typed


def retryable_for(category: str) -> bool:
    """Whether the same call may be repeated for this failure category."""
    if category not in CATEGORY_RETRYABLE:
        raise UnknownRecoveryCategory(
            f"unknown failure category {category!r}; one of "
            f"{sorted(CATEGORY_RETRYABLE)}")
    return CATEGORY_RETRYABLE[category]
