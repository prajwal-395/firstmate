"""What the editor decided about a candidate treatment, and why.

The gap this closes
-------------------
Ren proposed a treatment; the editor answered.  Nothing recorded WHAT
the answer was.  "Accepted", "rejected" and "modified" are three
different facts, and the difference decides what Ren does next: an
accepted treatment is settled, a rejected one is a correction to learn
from, and a modified one is a rejection AND a new plan.  The editor's
reason for the correction was the one piece of information nobody
recorded, so the next run made the same mistake in a different costume.

Two more facts travel with the ruling, and both are about scope:

* a preference the editor means for THIS project is not a preference
  they hold everywhere;
* a preference they DO hold everywhere must not travel on its own,
  because generalising a subjective choice across unrelated projects is
  how one reel's taste becomes the next client's taste.

The rules
---------
* **The outcome is one of three.**  `accepted`, `rejected`, `modified`.
  "Modified" is not a rejection with extra words: it means the plan as
  proposed did not stand, and it says so.
* **A correction carries a reason.**  A rejection or a modification with
  no reason teaches nothing, and a learning nobody explained reads as
  noise.
* **The editor's own words go in first.**  The reason is quoted, not
  paraphrased; the quote is what makes the ruling readable later.
* **A project-scoped ruling stays in its project.**  It is recorded as a
  project learning, and a project learning cannot reach another project.
* **An editor-wide ruling travels only through a slot that exists.**  A
  ruling scoped `editor` names the preference slot it corresponds to.
  When it names none, the ruling is recorded with `generalizable: false`
  and the reason it does not travel: Ren does not invent a slot for a
  subjective choice, and a preference nobody can address is a
  preference nobody else can be held to.

This module imports nothing from steps or processes: it reads the
preference vocabulary through `library/tools/decided_value.py`, records
the project learning through `library/tools/learned_context.py` and the
decision through `library/tools/creative_revision.py`.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

FORMAT = "treatment_rulings/1"

# ── The outcomes ─────────────────────────────────────────────────────

ACCEPTED = "accepted"
REJECTED = "rejected"
MODIFIED = "modified"

OUTCOMES = (ACCEPTED, REJECTED, MODIFIED)

CORRECTIONS = (REJECTED, MODIFIED)
"""The outcomes that correct a plan rather than settle it."""

# ── The scopes ───────────────────────────────────────────────────────

SCOPE_PROJECT = "project"
SCOPE_EDITOR = "editor"

SCOPES = (SCOPE_PROJECT, SCOPE_EDITOR)

#: The decision a ruling leaves on the record.
DECISION_APPROVED = "approved"
DECISION_REJECTED = "rejected"


class RulingError(ValueError):
    """A ruling that cannot be acted on, refused by name."""


def _nonempty(value, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise RulingError(f"{field} must not be empty")
    return text


def ruling(outcome: str, reason: str = "", *, scope: str = SCOPE_PROJECT,
           editor_words: str = "", preference_slot: str = "",
           stated_by: str = "") -> dict[str, Any]:
    """One ruling on a proposed treatment.

    `outcome` is what the editor decided, `reason` is why they decided
    it, `editor_words` is their own sentence, quoted, and `scope` says
    whether the preference is this project's or theirs in general.
    """
    if outcome not in OUTCOMES:
        raise RulingError(
            f"unknown ruling outcome {outcome!r}; known outcomes: "
            f"{list(OUTCOMES)}")
    if scope not in SCOPES:
        raise RulingError(
            f"unknown ruling scope {scope!r}; known scopes: {list(SCOPES)}")
    why = str(reason or "").strip()
    if outcome in CORRECTIONS and not why:
        raise RulingError(
            f"a {outcome} ruling needs a reason: a correction nobody "
            "explained teaches nothing")
    slot_name = str(preference_slot or "").strip()
    if slot_name:
        from library.tools import decided_value

        try:
            decided_value.slot(slot_name)
        except Exception as exc:
            raise RulingError(
                f"unknown preference slot {slot_name!r}: {exc}") from exc
    if scope == SCOPE_EDITOR and not slot_name:
        generalizable = False
        why_not = ("it names no registered preference slot, so it stays "
                   "with this project: Ren does not invent a slot for a "
                   "subjective choice")
    else:
        generalizable = scope == SCOPE_EDITOR
        why_not = ""
    return {
        "format": FORMAT,
        "outcome": outcome,
        "reason": why,
        "scope": scope,
        "editor_words": str(editor_words or "").strip(),
        "preference_slot": slot_name,
        "stated_by": str(stated_by or "").strip(),
        "generalizable": generalizable,
        "generalizable_reason": why_not,
    }


def describe(row: Mapping[str, Any]) -> str:
    """One line saying what the editor decided and why."""
    outcome = str(row.get("outcome") or "")
    reason = str(row.get("reason") or "")
    if not reason:
        return f"{outcome}"
    return f"{outcome}: {reason}"


def _approval(row: Mapping[str, Any]) -> dict[str, Any]:
    """The ruling in the shape a decision record's approval carries."""
    return {
        "decision": (DECISION_APPROVED
                     if row.get("outcome") == ACCEPTED
                     else DECISION_REJECTED),
        "outcome": str(row.get("outcome") or ""),
        "why": str(row.get("reason") or ""),
        "by": str(row.get("stated_by") or "the editor"),
        "scope": str(row.get("scope") or SCOPE_PROJECT),
    }


def record(project: os.PathLike | str, revision_id: str,
           row: Mapping[str, Any], *, author: str = "ren") -> dict[str, Any]:
    """Keep the ruling on the decision it belongs to, and what it teaches.

    The ruling lands on the candidate decision record, so the record
    says what the editor decided about the plan it proposed.  A
    correction also becomes a project learning, carrying the editor's
    words, so the next run in THIS project knows better; a project
    learning cannot reach another project, which is what keeps a
    project-scoped preference where it was stated.
    """
    from library.tools import creative_revision

    if not isinstance(row, Mapping) or not row:
        raise RulingError("a recorded ruling needs a ruling")
    if str(row.get("outcome") or "") not in OUTCOMES:
        raise RulingError(
            f"unknown ruling outcome {row.get('outcome')!r}")
    revision = creative_revision.create(
        project, revision_id=str(revision_id),
        author=str(author or "ren"),
        approval=_approval(row),
        refs={"ruling": dict(row)},
        status=(DECISION_APPROVED if row.get("outcome") == ACCEPTED
                else DECISION_REJECTED))
    learning = None
    if row.get("outcome") in CORRECTIONS:
        learning = _learn(project, row)
    return {"revision": revision, "learning": learning}


def _learn(project: os.PathLike | str, row: Mapping[str, Any]) -> dict | None:
    """Record the correction as a project learning, with the reason.

    The learning is a CORRECTION - the editor's own verdict, in their
    own words - and it is read by every planning step of THIS project.
    It is not written anywhere another project can see.
    """
    from library.tools import learned_context

    reason = str(row.get("reason") or "").strip()
    words = str(row.get("editor_words") or "").strip()
    statement = (f"Editorial correction: {reason}"
                 + (f" The editor said: {words}" if words else ""))
    source = {"outcome": str(row.get("outcome") or ""),
              "scope": str(row.get("scope") or SCOPE_PROJECT),
              "decision_revision": str(row.get("decision_revision") or "")}
    try:
        return learned_context.record(
            str(project), learned_context.CORRECTION, statement,
            read_by=[learned_context.GLOBAL], source=source, detail=reason)
    except Exception:  # noqa: BLE001 - a learning must not lose the ruling
        return None


def read_rulings(project: os.PathLike | str) -> list[dict[str, Any]]:
    """The rulings this project's decision records carry.

    The reader for what `record` wrote: a ruling nobody can read back is
    a ruling nobody can act on.
    """
    from library.tools import creative_revision

    rows = []
    for revision in creative_revision.list_revisions(project):
        refs = revision.get("refs") or {}
        row = refs.get("ruling")
        if not isinstance(row, Mapping):
            continue
        rows.append({
            "revision_id": revision.get("revision_id"),
            "outcome": row.get("outcome"),
            "reason": row.get("reason"),
            "scope": row.get("scope"),
            "editor_words": row.get("editor_words"),
            "preference_slot": row.get("preference_slot"),
            "generalizable": row.get("generalizable"),
            "status": revision.get("status"),
        })
    return rows


def editor_wide_preferences() -> list[dict[str, Any]]:
    """The registered preference slots an editor-wide ruling may name.

    An editor-wide ruling reaches another project only through one of
    these, and only when the editor names it.  The list is the slots the
    engine knows how to read back, so a ruling that names none stays
    where it was stated.
    """
    from library.tools import decided_value

    return [{"key": key,
             "question": slot.question,
             "answer_units": slot.answer_units,
             "scopes": list(slot.scope_names())}
            for key, slot in sorted(decided_value.SLOTS.items())]
