"""The reading half of an interpretation, measured on its own.

The interpretation stage measures two different things at once: whether
the run READ the request, and whether it could GROUND the work in
something the footage carries. They are not the same measurement, and
folding them together is what made the published number unreadable -
eight of eight briefs were read correctly while four of eleven target
roles could be pointed at.

This module measures the READING half. It is published ALONGSIDE the
headline, never instead of it: the headline stays the engine's headline,
and this is the view that says which half of it was earned.

What the reading half asks of one brief:

- did the run restate the request? An empty restatement is no reading.
- did it name exactly the operations the request implies? Nothing
  missing, nothing extra.
- for a brief whose correct outcome is to report back, did it plan
  nothing?
- did the reading CARRY every constraint the request states? A request
  that says "move it down" and a reading that says "move it so it stops
  colliding" has dropped the direction, and a prose restatement has
  nowhere to put a direction - so the brief's answer key DECLARES the
  constraint, with the words that carry it, and the reading is measured
  against that. The constraint is counted BESIDE the reading and
  published as its own number: the reading is one measurement, and
  whether it carried the request's constraints is another.

The constraint's words are the REQUESTER's own words: they are already
in the instruction the run read, so declaring them here gives the run
nothing it did not have. What it gives the harness is a place to say
"the reading dropped this".
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

#: The states a reading can be in.
MEASURED = "measured"

#: Why a constraint counts as dropped.
DROPPED = "dropped"
CARRIED = "carried"


def stated_constraints(key: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The constraints the request states, as the answer key declares them.

    They live in the key, not in the public view: the run reads the
    request, and the harness reads the key. Nothing here is secret from
    the run - these are the requester's own words - but they are not
    handed to it either, because the harness is what measures them.
    """
    rows = []
    for entry in (key.get("stated_constraints") or ()):
        if not isinstance(entry, Mapping):
            continue
        rows.append({
            "id": str(entry.get("id") or ""),
            "statement": str(entry.get("statement") or ""),
            "terms": [str(term) for term in (entry.get("terms") or ())
                      if str(term).strip()],
        })
    return rows


def _mentions(text: str, terms: Sequence[str]) -> bool:
    """Whether the text carries any of the terms, as WHOLE words.

    Whole words, so "down" is not found inside "download" and "the" is
    not found inside "there".
    """
    for term in terms:
        pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _intent_statements(record: Mapping[str, Any]) -> list[str]:
    """The statements of a committed canonical intent, if the record has one.

    A run that committed the canonical intent (`interpretation.intent`)
    has said what it read in the engine's own vocabulary, and that is
    part of the reading - it is measured here beside the prose
    restatement, never instead of it.
    """
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    intent = block.get("intent")
    if not isinstance(intent, Mapping):
        return []
    statements = []
    for field in ("requirements", "preferences", "inferred_preferences"):
        for entry in (intent.get(field) or ()):
            if isinstance(entry, Mapping) and str(entry.get("statement")
                                                 or "").strip():
                statements.append(str(entry["statement"]))
    for entry in (intent.get("requests") or ()):
        if isinstance(entry, Mapping) and str(entry.get("statement")
                                             or "").strip():
            statements.append(str(entry["statement"]))
    return statements


def _intent_problems(record: Mapping[str, Any]) -> list[str]:
    """Every defect in the canonical intent the run committed.

    A record may carry the canonical intent it read the request into
    (`interpretation.intent`). When it does, the harness MEASURES it -
    the intent's own contract is checked here, rather than the run's
    word that the intent is fine.
    """
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    intent = block.get("intent")
    if not isinstance(intent, Mapping):
        return []
    try:
        from library.tools import editorial_intent
        parsed = editorial_intent.EditorialIntent.from_dict(intent)
        return editorial_intent.validate_intent(parsed)
    except Exception as exc:  # noqa: BLE001 - a broken intent is data
        return [f"the committed canonical intent does not read: {exc}"]


def _intent_kinds(record: Mapping[str, Any]) -> list[str]:
    """The request kinds the committed intent declares."""
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    intent = block.get("intent")
    if not isinstance(intent, Mapping):
        return []
    return [str(entry.get("kind") or "")
            for entry in (intent.get("requests") or ())
            if isinstance(entry, Mapping)]


def reading_text(record: Mapping[str, Any],
                 restated: str) -> str:
    """Everything the run said about the request, as one text.

    The prose restatement, the constraints the run declared it carried,
    and the statements of a committed canonical intent. A constraint
    counts as carried when ANY of them carries it.
    """
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    parts = [str(restated or "")]
    for item in (block.get("constraints_covered") or ()):
        parts.append(str(item))
    parts.extend(_intent_statements(record))
    return " ".join(part for part in parts if part)


def measure(record: Mapping[str, Any], key: Mapping[str, Any],
            interpretation: Mapping[str, Any]) -> dict[str, Any]:
    """One brief's reading, measured against what the request asked.

    `interpretation` is the stage's own measurement of the same brief -
    this reads its detail rather than re-deriving it, so the reading
    view and the headline can never disagree about what the run said.
    """
    detail = interpretation.get("detail")
    detail = detail if isinstance(detail, Mapping) else {}
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    # The restatement is the RUN's own words, read from the record. The
    # stage's detail carries the same text - it is derived from the same
    # record - and the operations come from there, so the reading view
    # and the headline cannot disagree about what the run named.
    restated = str(block.get("restated_outcome")
                   or detail.get("restated_outcome") or "").strip()
    selected = [str(op) for op in (detail.get("selected_operations") or [])]
    missing = [str(op) for op in (detail.get("missing_operations") or [])]
    unexpected = [str(op) for op in (detail.get("unexpected_operations") or [])]
    expected_refusal = bool(key.get("expected_refusal"))
    text = reading_text(record, restated)

    constraints = []
    dropped = []
    for constraint in stated_constraints(key):
        terms = constraint["terms"]
        carried = bool(terms) and _mentions(text, terms)
        if not carried:
            dropped.append(constraint["id"])
        constraints.append({
            "id": constraint["id"],
            "statement": constraint["statement"],
            "status": CARRIED if carried else DROPPED,
        })

    reasons = []
    if not restated:
        reasons.append("the run restated nothing")
    if missing:
        reasons.append("the run named none of the operations the request "
                       "implies")
    if unexpected:
        reasons.append("the run named operations the request does not "
                       "imply")
    if expected_refusal and selected:
        reasons.append("the run planned work for a brief whose correct "
                       "outcome is to report back")
    intent_problems = _intent_problems(record)
    if intent_problems:
        reasons.append("the committed canonical intent breaks its own "
                       "contract: " + "; ".join(intent_problems))
    return {
        "status": MEASURED,
        "passed": not reasons,
        "reasons": reasons,
        "constraints_passed": not dropped,
        "constraint_reasons": (
            ["the reading dropped a constraint the request stated: "
             + ", ".join(dropped)] if dropped else []),
        "detail": {
            "restated_outcome": restated,
            "selected_operations": selected,
            "missing_operations": missing,
            "unexpected_operations": unexpected,
            "stated_constraints": constraints,
            "constraints_dropped": dropped,
            "intent_problems": intent_problems,
            "intent_request_kinds": _intent_kinds(record),
            "reading_surface": ("the committed canonical intent and the "
                                 "declared constraints"
                                 if _intent_statements(record) or
                                 (record.get("interpretation") or {}).get(
                                     "constraints_covered")
                                 else "the prose restatement"),
        },
    }


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Every brief's reading, on one denominator.

    A supplementary view, published ALONGSIDE the headline. It never
    replaces it: the headline is the engine's headline, and this says
    how much of it the reading earned.

    The constraints the request states are counted SEPARATELY, and both
    numbers are published: the reading itself (the restatement and the
    operations it named), and the reading together with the constraints
    it carried. A request that says "move it down" and a reading that
    says "move it so it stops colliding" are the same reading and a
    different one, and the report says which it is counting.
    """
    measured = [row for row in rows if row.get("status") == MEASURED]
    passed = sum(row.get("passed") is True for row in measured)
    constraints_carried = sum(row.get("constraints_passed") is True
                              for row in measured)
    dropped = []
    for row in measured:
        detail = row.get("detail")
        detail = detail if isinstance(detail, Mapping) else {}
        for constraint_id in (detail.get("constraints_dropped") or ()):
            dropped.append({"brief_id": str(row.get("brief_id")),
                            "constraint_id": str(constraint_id)})
    return {
        "note": (
            "A supplementary view, published ALONGSIDE the headline and "
            "never instead of it: whether the run READ the request - it "
            "restated it, named exactly the operations the request "
            "implies, and planned nothing for a brief whose correct "
            "outcome is to report back. The constraints the request "
            "states are counted beside it, because a restatement has "
            "nowhere to put a direction the request gave it. The "
            "headline's interpretation stage measures the reading AND "
            "the grounding together."),
        "measured": len(measured),
        "passed": passed,
        "failed": len(measured) - passed,
        "value": (passed / len(measured)) if measured else None,
        "constraints": {
            "measured": len(measured),
            "carried": constraints_carried,
            "dropped": len(measured) - constraints_carried,
            "value": ((constraints_carried / len(measured))
                      if measured else None),
            "dropped_detail": dropped,
        },
        "value_including_constraints": (
            (constraints_carried / len(measured)) if measured else None),
        "briefs": {str(row.get("brief_id")): row for row in rows},
    }
