"""What a task rests on, and what it decided: the evidence and the choices.

A task that changes a video stands on two things: the source it read
(evidence) and the judgements it made about that source (decisions).
Both are RECORDS on the task, and both keep their provenance - what was
observed, where it came from, and which choice it supports.

EVIDENCE is a cited thing with a LOCATOR: a clip and its span, a
transcript passage, a measurement, a rendered file, a timeline. Evidence
without a locator is a claim, and a claim is not evidence, so the
locator is required and a record without one is refused.

A DECISION is a question, an answer, and the evidence it rests on. A
decision that rests on evidence names it; a decision that rests on a
judgement says so with `basis="judgement"`, and is never presented as
though it were derived. A decision naming evidence that was never
recorded is refused: a broken link is worse than no link.

THE LINK is the point. A plan node declares the evidence and the
decisions it consumes, and `stamp` assembles them into the provenance an
operation carries INTO execution. Once the stamp is in the operation's
checkpoint, the chain from source to choice to edit survives the run
that made it - which is the only way a later reader can tell an
evidence-backed decision from an assertion.

WHERE THE OTHER ANSWERS LIVE
----------------------------
`library/tools/editorial_intent.py` is the canonical record of what the
user ASKED for, and `editorial_decisions.py` is the graph of the choices
that answer it - both at the editor's level, where a brief becomes a
cut. This module is the TASK's own ledger: what this task read, what it
decided, and what it carried into the operation it ran. The two levels
meet at the plan node, which names the evidence and the decisions a
single operation rests on.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence

from library.tools import assignment_lifecycle as lifecycle

EVIDENCE_KINDS = (
    "footage",
    "transcript",
    "analysis",
    "measurement",
    "render",
    "timeline",
    "document",
)

DECISION_BASES = ("evidence", "judgement")

DECIDED_BY = ("model", "creator", "pipeline")


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _plain(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    if value.strip() != value or any(char in value for char in "/\\\0"):
        raise ValueError(f"{label} must be a plain identifier")
    return value


def _section(record: Mapping, name: str) -> list:
    value = record.get(name)
    return list(value) if isinstance(value, list) else []


def _find(items: Iterable[Mapping], item_id: str) -> dict | None:
    for item in items:
        if item.get("id") == item_id:
            return dict(item)
    return None


def record_evidence(project: str, assignment_id: str, *, evidence_id: str,
                     kind: str, source: str, locator: str,
                     sha256: str = "", detail: Mapping | None = None) -> dict:
    """Record one cited thing the task rests on.

    The same evidence id recorded twice with the same content is the same
    evidence; recorded twice with different content it is a REFUSAL, not
    a second version - a task that needs two versions of one source says
    so with two ids.
    """
    evidence_id = _plain(evidence_id, "evidence_id")
    if kind not in EVIDENCE_KINDS:
        raise ValueError(f"unknown evidence kind {kind!r}; expected one of "
                         f"{', '.join(EVIDENCE_KINDS)}")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("evidence needs a source: what produced it")
    if not isinstance(locator, str) or not locator.strip():
        raise ValueError("evidence needs a locator: a claim without one is "
                         "not evidence")
    record = {
        "id": evidence_id,
        "kind": kind,
        "source": source.strip(),
        "locator": locator.strip(),
        "sha256": (sha256 or "").strip(),
        "detail": copy.deepcopy(dict(detail)) if detail else {},
    }
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        items = _section(stored, "evidence")
        prior = _find(items, evidence_id)
        if prior is not None:
            comparable = {key: prior.get(key) for key in record}
            if comparable != record:
                raise ValueError(
                    f"evidence {evidence_id!r} is already recorded with "
                    f"different content; use a second id for a second "
                    f"version of the same source")
            return prior
        entry = {**record, "recorded_at": _now()}
        items.append(entry)
        stored["evidence"] = items
        lifecycle.save(project, stored)
        return entry


def record_decision(project: str, assignment_id: str, *, decision_id: str,
                    question: str, answer: str, basis: str = "evidence",
                    evidence_ids: Sequence[str] = (),
                    decided_by: str = "model") -> dict:
    """Record one editorial choice, and the evidence it rests on.

    `basis="evidence"` names the evidence ids, and each one must exist:
    a decision resting on evidence nobody recorded is a broken link. A
    choice made on judgement says so, and carries no evidence it does
    not have.
    """
    decision_id = _plain(decision_id, "decision_id")
    if basis not in DECISION_BASES:
        raise ValueError(f"unknown decision basis {basis!r}; expected one of "
                         f"{', '.join(DECISION_BASES)}")
    if decided_by not in DECIDED_BY:
        raise ValueError(f"unknown decider {decided_by!r}; expected one of "
                         f"{', '.join(DECIDED_BY)}")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("a decision names the question it answers")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("a decision names its answer")
    named = [str(item) for item in evidence_ids]
    if basis == "evidence" and not named:
        raise ValueError(
            "a decision on evidence names the evidence it rests on; use "
            "basis='judgement' when the choice was a judgement")
    with lifecycle.assignment_lock(project, assignment_id):
        stored = lifecycle.load(project, assignment_id)
        if stored is None:
            raise FileNotFoundError(
                f"assignment {assignment_id!r} has no checkpoint")
        items = _section(stored, "decisions")
        prior = _find(items, decision_id)
        record = {
            "id": decision_id,
            "question": question.strip(),
            "answer": answer.strip(),
            "basis": basis,
            "evidence_ids": named,
            "decided_by": decided_by,
        }
        if prior is not None:
            if {key: prior.get(key) for key in record} != record:
                raise ValueError(
                    f"decision {decision_id!r} is already recorded with "
                    f"different content; use a second id for a second "
                    f"choice")
            return prior
        known = {item["id"] for item in _section(stored, "evidence")}
        missing = [item for item in named if item not in known]
        if missing:
            raise ValueError(
                "decision rests on evidence that was never recorded: "
                + ", ".join(missing))
        entry = {**record, "recorded_at": _now()}
        items.append(entry)
        stored["decisions"] = items
        lifecycle.save(project, stored)
        return entry


def evidence(project: str, assignment_id: str) -> list[dict]:
    """Every evidence item the task recorded, in the order recorded."""
    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return []
    return _section(stored, "evidence")


def decisions(project: str, assignment_id: str) -> list[dict]:
    """Every decision the task recorded, in the order recorded."""
    stored = lifecycle.load(project, assignment_id)
    if stored is None:
        return []
    return _section(stored, "decisions")


def chain(project: str, assignment_id: str) -> dict:
    """The task's evidence, its decisions, and the links between them.

    `unlinked` names the decisions that rest on no evidence - a judgement,
    which is allowed and must not be read as derived - and the evidence no
    decision used, which is material the task looked at and did not act
    on.
    """
    items = evidence(project, assignment_id)
    choices = decisions(project, assignment_id)
    used = {name for choice in choices for name in choice.get("evidence_ids", ())}
    return {
        "assignment_id": assignment_id,
        "evidence": items,
        "decisions": choices,
        "unlinked_decisions": [choice["id"] for choice in choices
                               if not choice.get("evidence_ids")],
        "unused_evidence": [item["id"] for item in items
                            if item["id"] not in used],
    }


def stamp(project: str, assignment_id: str, *, node_id: str = "",
          operation_id: str = "") -> dict:
    """The provenance an operation carries INTO execution.

    The stamp is the chain, compact enough to live in a checkpoint: the
    evidence with its locators and digests, and the decisions with the
    evidence ids they rest on. A task with no plan and no ledger stamps
    an EMPTY provenance - never a placeholder that reads as provenance.
    """
    from library.tools import assignment_plan

    node = assignment_plan.find_node(project, assignment_id,
                                     node_id=node_id,
                                     operation_id=operation_id)
    items = {item["id"]: item for item in evidence(project, assignment_id)}
    choices = {choice["id"]: choice
               for choice in decisions(project, assignment_id)}
    if node is None:
        return {"node_id": "", "operation_id": operation_id,
                "evidence": [], "decisions": []}
    picked_evidence = [items[name] for name in node.get("evidence_ids", ())
                       if name in items]
    picked_decisions = [choices[name] for name in node.get("decision_ids", ())
                        if name in choices]
    return {
        "node_id": node.get("node_id", ""),
        "operation_id": node.get("operation_id", operation_id),
        "evidence": [
            {"id": item["id"], "kind": item["kind"], "source": item["source"],
             "locator": item["locator"], "sha256": item.get("sha256", "")}
            for item in picked_evidence],
        "decisions": [
            {"id": choice["id"], "question": choice["question"],
             "answer": choice["answer"], "basis": choice["basis"],
             "evidence_ids": list(choice.get("evidence_ids", ()))}
            for choice in picked_decisions],
    }
