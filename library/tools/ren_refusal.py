"""One refusal shape for every `ren` verb.

A REFUSAL is not a failure: nothing ran, nothing is half-written, and
the next step is known. Every refusal a user or host LLM meets on a
`ren` verb's path carries the same shape - a first line stating the
consequence in plain creative terms, then the technical detail
(what happened, why, and the fix: the exact next command or edit):

    ren: refused - <consequence and next step, in plain language>
      what: <what happened>
      why: <why it refused>
      fix: <the exact next command or edit>

The first line is what a host relays to the editor; `what`/`why`/`fix`
stay in the prose for the technical explanation on request. Every
refusal also carries a recovery class - `auto_recoverable` (the host
may perform only the named safe recovery), `needs_question` (ask the
smallest question that unblocks) or `blocked` (state what must be
resolved first) - so the host knows whether to act, ask or wait.

`RenRefusal` is the type; the `ren` CLI boundary (each process a verb
can land in: `ren` itself, `manage_project.py`, `run_pipeline.py`,
`footage_query`) catches it in ONE place per process, prints
`render()`, and exits `REFUSAL_EXIT_CODE`. An unexpected error - a bug,
a missing file the code did not anticipate - is NOT a refusal and
keeps its traceback and exit 1, because a traceback with a fix
invented for it would be a lie.

A refusal class for one area (touchups, undos, hooks, ...) subclasses
`RenRefusal` alongside its historical base (`class TouchupRefused
(RenRefusal, RuntimeError)`) so every existing `except` still catches
it, and every raise site passes all three fields - the constructor
refuses an empty one, so a refusal without a fix fails loudly at the
raise site rather than reaching a user.

Every refusal also carries a stable machine-readable CODE
(`RenRefusal.code`): the support bundle, the structured log and any
machine reader key off it instead of parsing the prose. The default is
derived from the class name (`SpliceRefused` -> `REN_SPLICE_REFUSED`)
so every existing subclass is coded without touching its file; a
subclass that needs a code that survives a rename sets `CODE`
explicitly, and a raise site that needs one for this instance passes
`code=`. The human shape `render()` never carries the code - it stays
out of prose a machine would parse - and `to_dict()` carries all eight
fields (`code`, `what`, `why`, `fix`, `user_message`, `recovery_class`,
`conflict_class`, `ask_reason`).

A refusal may also name the CONFLICT CLASS it came from - the kind of
badness the refusal is about - so a host can act on the class without
parsing the prose. The classes are enumerated by
`library/tools/edit_conflict.py`; a refusal that is not about a
conflict carries an empty string.

A refusal that asks the PERSON carries an ASK REASON: which of the
three things genuinely demands them - the intent is not determinate, the
change is not authorized, or the available evidence does not settle it.
`needs_question` says THAT a question is needed; the ask reason says
WHY, and it is the difference between asking the person and the host
improvising a question for them. A refusal that does not ask carries
an empty reason.

PROD-003: a refusal also carries a BLOCKER - what KIND of thing blocked
it, in the six kinds `library/tools/refusal_taxonomy.py` enumerates -
and a NEXT ACTION, the move the caller can actually take. The two are
required: a refusal without a blocker says what happened but not what
kind of block it was, and a refusal without a next action is a dead
end. A family declares its blocker once (`BLOCKER`) or is classified in
the taxonomy's inventory; a raise site may name its own. A refusal whose
fix recommends an argument the caller cannot supply is refused at the
raise site (`SUPPLIES` names the surface whose vocabulary the fix may
use), because advice the caller cannot act on is worse than no advice.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback

from library.tools import refusal_taxonomy


REFUSAL_EXIT_CODE = 4
"""Exit code for a refusal with a fix. Part of the contract below."""


ASK_REASONS = ("ambiguous_intent", "missing_authorization",
               "insufficient_evidence")
"""The three things that genuinely demand a person, and the reasons a
refusal names when it asks one.

`ambiguous_intent` - the request means more than one thing, and the
choice changes the result.
`missing_authorization` - the change is the person's to approve, and
nobody has said yes.
`insufficient_evidence` - what is on the record does not settle it, so
a judgement would be a guess.

A refusal that does not ask the person carries no reason. This is the
ONE vocabulary of them: `library/tools/nothing_to_decide.py` is the
sibling for the MODEL, and it is deliberately a different module,
because "the model had nothing to decide" and "the person had nothing
to decide" are different sentences.
"""


def ask_reason(*, intent_unambiguous: bool, authorized: bool,
               evidence_sufficient: bool) -> str:
    """Whether a person is needed, and why. Empty means proceed.

    The one place that answers "does this genuinely demand a person",
    so "ask only when it must" has one spelling rather than a habit.
    The three questions are the three reasons, and the order is the
    order they bite: an ambiguous intent cannot be answered by more
    evidence, and an unauthorized change cannot be answered by a
    clearer intent.
    """
    if not intent_unambiguous:
        return "ambiguous_intent"
    if not authorized:
        return "missing_authorization"
    if not evidence_sufficient:
        return "insufficient_evidence"
    return ""


EXIT_CONTRACT = """\
ren exit codes:
  0  success (a verb that only reports still exits 0 once it printed).
  1  failed - an unexpected error. Traceback on stderr, no fix recorded;
     this is a bug or an environment the code did not anticipate, not a refusal.
  2  usage - an unknown verb, bad arguments, or a run selection that never
     started (a REFUSED run summary keeps this code; its reason prints in
     the refusal shape).
  3  no engine checkout (ren only: no manage_project.py beside the install).
  4  refused - nothing ran. The fix printed above is the next step; run it
     and retry the verb."""
"""The exit code contract, stated once. `ren --help` does not print it;
`ren doctor` points here when a verb exits 4."""


class RenRefusal(RuntimeError, ValueError):
    """What happened, why, and the fix. All three are required.

    Subclasses both `RuntimeError` and `ValueError` so every existing
    `except` clause keeps catching an adopted refusal - adopting a
    class changes what its message carries, never who catches it."""

    CODE = ""
    """Explicit machine-readable code for this refusal family. When
    empty the instance falls back to the class-name derivation
    (`SpliceRefused` -> `REN_SPLICE_REFUSED`); set this when the code
    must survive a class rename."""

    USER_MESSAGE = ("I couldn't safely finish this step. The requested "
                    "change has not been confirmed; resolve the issue "
                    "below before continuing.")
    """Plain-language consequence and next step shown to the editor.
    A refusal family may override this; the detailed `what`, `why` and
    `fix` remain available for a technical explanation."""

    RECOVERY_CLASS = "blocked"
    """One of auto_recoverable, needs_question or blocked. Families
    override this only when their safe recovery is known."""

    ASK_REASON = ""
    """Why a person is needed, when one is. One of `ASK_REASONS`, or
    empty for a refusal that does not ask. A family that asks sets this
    (or passes `ask_reason=` at the raise site), so a host can tell an
    ambiguous request from an unauthorized one from an unproven one
    without reading the prose."""

    BLOCKER = ""
    """What KIND of thing blocks this family, in the six kinds
    `refusal_taxonomy.BLOCKERS` names. A family that leaves it empty is
    classified in the taxonomy's inventory; a family that sets it wins
    over the inventory, and a raise site may name its own."""

    NEXT_ACTION = ""
    """The move the caller can take, in the vocabulary
    `refusal_taxonomy.NEXT_ACTIONS` names. Empty means the blocker's
    own next action, which is the right one unless this family knows
    better."""

    SUPPLIES = ""
    """The surface whose vocabulary this family's fix may name.

    Empty means the fix is not checked. A family that names a surface
    has its fix checked against what the caller can supply there, and a
    fix naming anything else is refused at the raise site."""

    def __init__(self, what: str, why: str, fix: str, code: str = "",
                 user_message: str = "", recovery_class: str = "",
                 conflict_class: str = "", ask_reason: str = "",
                 blocker: str = "", next_action: str = "") -> None:
        missing = [name for name, value in
                   (("what", what), ("why", why), ("fix", fix))
                   if not (isinstance(value, str) and value.strip())]
        if missing:
            raise ValueError(
                "a refusal must carry what, why and fix; missing: "
                + ", ".join(missing) + ". A refusal without a fix is "
                "the defect this module exists to remove - state the "
                "exact next command or edit.")
        self.what = what.strip()
        self.why = why.strip()
        self.fix = fix.strip()
        self.code = (code.strip() or type(self).CODE.strip()
                     or _default_code(type(self).__name__))
        self.user_message = (user_message.strip()
                             or type(self).USER_MESSAGE.strip()
                             or f"I couldn't safely finish this step. {self.fix}")
        self.recovery_class = (recovery_class.strip()
                               or type(self).RECOVERY_CLASS.strip())
        if self.recovery_class not in {
                "auto_recoverable", "needs_question", "blocked"}:
            raise ValueError("recovery_class must be auto_recoverable, "
                             "needs_question or blocked")
        self.conflict_class = conflict_class.strip()
        self.ask_reason = (ask_reason.strip()
                           or type(self).ASK_REASON.strip())
        if self.ask_reason and self.ask_reason not in ASK_REASONS:
            raise ValueError(
                f"ask_reason must be one of {list(ASK_REASONS)}, or empty; "
                f"got {self.ask_reason!r}")
        self.blocker = (blocker.strip() or type(self).BLOCKER.strip()
                        or refusal_taxonomy.blocker_for(type(self)))
        if self.blocker not in refusal_taxonomy.BLOCKERS:
            raise ValueError(
                f"blocker must be one of {list(refusal_taxonomy.BLOCKERS)}; "
                f"got {self.blocker!r}")
        self.next_action = (next_action.strip()
                            or type(self).NEXT_ACTION.strip()
                            or refusal_taxonomy.next_action_for(self.blocker))
        if self.next_action not in refusal_taxonomy.NEXT_ACTIONS:
            raise ValueError(
                f"next_action must be one of "
                f"{list(refusal_taxonomy.NEXT_ACTIONS)}; got "
                f"{self.next_action!r}")
        surface = type(self).SUPPLIES.strip()
        if surface:
            refusal_taxonomy.assert_supplyable(
                self.fix, refusal_taxonomy.supplyable(surface),
                where=f"{type(self).__module__}.{type(self).__name__}")
        super().__init__(self.render())

    def render(self) -> str:
        """The host-facing consequence, with technical detail retained."""
        return (f"ren: refused - {self.user_message}\n"
                f"  blocker: {self.blocker} - "
                f"{refusal_taxonomy.plain_reason(self.blocker)}\n"
                f"  what: {self.what}\n"
                f"  why: {self.why}\n"
                f"  fix: {self.fix}\n"
                f"  next action: {self.next_action}")

    def to_dict(self) -> dict:
        """Structured refusal, including editor guidance and recovery."""
        return {"code": self.code, "what": self.what,
                "why": self.why, "fix": self.fix,
                "user_message": self.user_message,
                "recovery_class": self.recovery_class,
                "conflict_class": self.conflict_class,
                "ask_reason": self.ask_reason,
                "blocker": self.blocker,
                "next_action": self.next_action,
                "blocking_reason": refusal_taxonomy.plain_reason(
                    self.blocker)}


def _default_code(class_name: str) -> str:
    """`SpliceRefused` -> `REN_SPLICE_REFUSED`. Derived, so every
    existing subclass is coded without touching its file; a rename
    changes the code, which is why a family that must not move sets
    `CODE` explicitly."""
    if class_name == "RenRefusal":
        return "REN_REFUSAL"
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).upper()
    return snake if snake.startswith("REN_") else f"REN_{snake}"


REN_RESULT_PREFIX = "REN_RESULT"
"""The final stderr line's prefix when `REN_OUTPUT=json` (LE-03)."""

UNEXPECTED_CODE = "REN_UNEXPECTED"
"""The `code` an uncaught error carries in its `REN_RESULT` line."""


def json_output_enabled() -> bool:
    """Whether the caller wants the machine-readable result line.

    The host adapters set `REN_OUTPUT=json` for detected harnesses;
    humans never set it and keep the prose only."""
    return os.environ.get("REN_OUTPUT") == "json"


def refusal_result_dict(refused: RenRefusal,
                        exit_code: int = REFUSAL_EXIT_CODE) -> dict:
    """The `REN_RESULT` payload for a refusal: the exit plus `to_dict`."""
    return {"ok": False, "exit": exit_code, **refused.to_dict()}


def emit_ren_result(payload: dict) -> None:
    """Print one final `REN_RESULT <json>` line on stderr."""
    print(f"{REN_RESULT_PREFIX} {json.dumps(payload, sort_keys=True)}",
          file=sys.stderr)


def emit_refusal_result(refused: RenRefusal,
                        exit_code: int = REFUSAL_EXIT_CODE) -> None:
    """Print the refusal's `REN_RESULT` line when JSON output is on.

    Every refusal catch site calls this after `render()`: the prose
    stays as is for humans, and the line carries the code, the
    recovery class and the fix for the host."""
    if json_output_enabled():
        emit_ren_result(refusal_result_dict(refused, exit_code))


def unexpected_result_dict(exc: BaseException) -> dict:
    """The `REN_RESULT` payload for an uncaught error.

    `where` names the frame that raised, so the host can say which
    file to read; the traceback itself is still printed first."""
    where = "<unknown>"
    try:
        frames = traceback.extract_tb(exc.__traceback__)
        if frames:
            last = frames[-1]
            where = f"{last.filename}:{last.lineno}"
    except Exception:  # noqa: BLE001 - the location is best-effort
        pass
    return {"ok": False, "exit": 1, "code": UNEXPECTED_CODE,
            "exception": f"{type(exc).__name__}: {exc}", "where": where}


def report_unexpected(exc: BaseException) -> int:
    """Print the traceback, then the `REN_RESULT` line when JSON is on.

    Returns 1, so an `__main__` guard ends with
    `raise SystemExit(report_unexpected(exc))`."""
    traceback.print_exc()
    if json_output_enabled():
        emit_ren_result(unexpected_result_dict(exc))
    return 1
