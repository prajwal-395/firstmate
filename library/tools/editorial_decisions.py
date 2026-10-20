"""The editorial decision graph - what was decided, on what evidence, and why.

The gap this closes
-------------------
An intent says what the viewer should get.  Something has to turn that
into a cut.  Today the turn happens in three places that cannot see
each other: a planner's own output, the edit ledger's rows, and the
edit request's operations.  Between them there is no record that says

    - this is a CANDIDATE, this is APPROVED, and this is the PHYSICAL
      OPERATION that carries it into Resolve
    - the evidence the decision rests on
    - the temporal and narrative relationships it spans
    - the other decisions it depends on
    - the tradeoff it accepted (pace against clarity, brevity against
      completeness)
    - the effect it intends and the criteria that would show it landed

Without that record a decision cannot be reviewed before it executes,
cannot be traced back to the evidence that justified it, and cannot be
explained afterwards except by reading the diff.

The model
---------
Five layers, and the separation is the point (EDIT-009):

    OBSERVATION          a measurement in the Project Model
    INTERPRETATION       what someone read out of the observations
    CANDIDATE DECISION   what someone proposes, with its evidence
    APPROVED DECISION    the candidate somebody accepted
    PHYSICAL OPERATION   the algebra op that carries it into Resolve

`EditorialDecision` holds layers three and four together with the
operations of layer five, and never holds an observation or an
interpretation: it REFERENCES them, so a decision can never be read as
a measurement.

    evidence             ids of the observations the decision rests on
    relations            temporal, physical, narrative, dialogue, audio
    depends_on           the decisions this one waits for
    tradeoffs            what was given up, and why
    intended_effect      what the viewer should notice
    evaluation_criteria  what would show the effect landed
    operations           the physical ops, fully specified
    approval             who accepted it, and when

`compile_to_edit_request` is the one way a decision becomes an
`edit_request.EditRequest` - the semantic interface AXI executes.  It
refuses rather than completing anything:

    - a decision that was never approved does not compile
    - a decision whose intent's request needs approval and carries no
      approval does not compile
    - a decision whose request's evidence bar is unmet does not compile
    - a decision with no evidence does not compile
    - an operation with no declared effect does not compile - the
      execution layer never writes the intent for the editor
    - a decision with no operation does not compile

The refusal names the decision, so the answer is "which one", not "it
failed".

Why a separate module and not a field on the edit request: the request
is what the BACKEND evaluates, and a backend must never carry the
reasoning that produced it.  Keeping the decision beside the request is
what lets the request stay a request.

This module imports nothing from steps or processes: it reads the
records a run carries and the algebra it compiles into.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from library.tools import edit_request

#: Contract version. Bumped only with a migration that reads older ones.
DECISION_VERSION = 1


# ── The five layers, and the states a decision moves through ──────────
#
# The states ARE the separation between a proposed change and an
# executed one.  A decision is compiled only from `approved`; `compiled`
# is set by the compiler, so a request can always be traced back to the
# decision that produced it.

CANDIDATE = "candidate"
"""Proposed, with its evidence, waiting for somebody to accept it."""

APPROVED = "approved"
"""Accepted.  Only an approved decision may compile."""

COMPILED = "compiled"
"""Turned into an edit request.  Set by the compiler, never by hand."""

REJECTED = "rejected"
"""Declined, with the reason recorded."""

DECISION_STATES = (CANDIDATE, APPROVED, COMPILED, REJECTED)


# ── The relationships a decision spans (EDIT-011) ──────────────────────
#
# Shots, dialogue, events and audio.  `physical` covers the tracked
# object and person relations the continuity check reads; `narrative`
# covers the semantic relations (claim, topic, explanation,
# demonstration, context dependency) that stay candidates.

TEMPORAL = "temporal"
PHYSICAL = "physical"
NARRATIVE = "narrative"
DIALOGUE = "dialogue"
AUDIO = "audio"

RELATION_KINDS = (TEMPORAL, PHYSICAL, NARRATIVE, DIALOGUE, AUDIO)


# ── The tradeoff axes (EDIT-013) ──────────────────────────────────────
#
# The pairs the punch list names, and the axes a resolution may be
# recorded against.  A tradeoff names the axis it took and the axis it
# gave up: "pace" against "clarity" is a decision, not a mood.

PACE = "pace"
CLARITY = "clarity"
VISUAL_INTEREST = "visual_interest"
CONTINUITY = "continuity"
BREVITY = "brevity"
COMPLETENESS = "completeness"

TRADEOFF_AXES = (PACE, CLARITY, VISUAL_INTEREST, CONTINUITY, BREVITY,
                 COMPLETENESS)


# ── Who stated it ─────────────────────────────────────────────────────
#
# The same three the edit ledger already uses, so a decision and the
# ledger row that carries it agree about who spoke.

STATED_BY = ("captain", "requester", "model")


class EditorialDecisionError(ValueError):
    """A decision that cannot be what it claims to be, refused by name."""


# ── The records ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionOperation:
    """One physical operation a decision authorises.

    The operation is FULLY SPECIFIED: `op` names an algebra op, `params`
    carries every parameter it requires, and `effect` says what the
    viewer should notice.  An operation with no effect is refused by the
    compiler rather than given one - the execution layer never writes
    the editor's intent.
    """

    op: str
    params: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)
    kind: str = edit_request.DIRECT
    effect: str = ""
    depends_on: tuple[int, ...] = ()
    preconditions: tuple = ()
    postconditions: tuple = ()

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "params": dict(self.params),
            "target": dict(self.target),
            "kind": self.kind,
            "effect": self.effect,
            "depends_on": list(self.depends_on),
            "preconditions": [dict(row) for row in self.preconditions],
            "postconditions": [dict(row) for row in self.postconditions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionOperation:
        if not isinstance(data, Mapping):
            raise EditorialDecisionError("an operation must be a mapping")
        return cls(
            op=str(data.get("op") or ""),
            params=dict(data.get("params") or {}),
            target=dict(data.get("target") or {}),
            kind=str(data.get("kind") or edit_request.DIRECT),
            effect=str(data.get("effect") or ""),
            depends_on=tuple(int(index) for index in
                             (data.get("depends_on") or ())),
            preconditions=tuple(dict(row) for row in
                               (data.get("preconditions") or ())),
            postconditions=tuple(dict(row) for row in
                                (data.get("postconditions") or ())),
        )


@dataclass(frozen=True)
class EditorialDecision:
    """One editorial decision, from candidate to compiled."""

    decision_id: str
    intent_id: str
    statement: str
    state: str = CANDIDATE
    request_id: str = ""
    stated_by: str = "model"
    evidence_ids: tuple[str, ...] = ()
    relations: tuple[dict, ...] = ()
    depends_on: tuple[str, ...] = ()
    tradeoffs: tuple[dict, ...] = ()
    intended_effect: str = ""
    evaluation_criteria: tuple[dict, ...] = ()
    operations: tuple[DecisionOperation, ...] = ()
    approval: dict | None = None
    uncertainty: float | None = None
    missing_evidence: tuple[dict, ...] = ()
    version: int = DECISION_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "decision_id": self.decision_id,
            "intent_id": self.intent_id,
            "request_id": self.request_id,
            "statement": self.statement,
            "state": self.state,
            "stated_by": self.stated_by,
            "evidence_ids": list(self.evidence_ids),
            "relations": [dict(row) for row in self.relations],
            "depends_on": list(self.depends_on),
            "tradeoffs": [dict(row) for row in self.tradeoffs],
            "intended_effect": self.intended_effect,
            "evaluation_criteria": [dict(row)
                                    for row in self.evaluation_criteria],
            "operations": [op.to_dict() for op in self.operations],
            "approval": (None if self.approval is None
                         else dict(self.approval)),
            "uncertainty": self.uncertainty,
            "missing_evidence": [dict(row) for row in self.missing_evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditorialDecision:
        if not isinstance(data, Mapping):
            raise EditorialDecisionError("a decision must be a mapping")
        uncertainty = data.get("uncertainty")
        approval = data.get("approval")
        return cls(
            decision_id=str(data.get("decision_id") or ""),
            intent_id=str(data.get("intent_id") or ""),
            request_id=str(data.get("request_id") or ""),
            statement=str(data.get("statement") or ""),
            state=str(data.get("state") or CANDIDATE),
            stated_by=str(data.get("stated_by") or "model"),
            evidence_ids=tuple(str(link) for link in
                               (data.get("evidence_ids") or ())),
            relations=tuple(dict(row) for row in (data.get("relations") or ())),
            depends_on=tuple(str(link) for link in
                             (data.get("depends_on") or ())),
            tradeoffs=tuple(dict(row) for row in (data.get("tradeoffs") or ())),
            intended_effect=str(data.get("intended_effect") or ""),
            evaluation_criteria=tuple(dict(row) for row in
                                      (data.get("evaluation_criteria") or ())),
            operations=tuple(DecisionOperation.from_dict(op)
                             for op in (data.get("operations") or ())),
            approval=(None if approval is None else dict(approval)),
            uncertainty=(None if uncertainty is None else float(uncertainty)),
            missing_evidence=tuple(dict(row) for row in
                                   (data.get("missing_evidence") or ())),
            version=int(data.get("version") or DECISION_VERSION),
        )


@dataclass
class DecisionGraph:
    """The decisions of one intent, and the dependencies between them."""

    intent_id: str = ""
    decisions: list[EditorialDecision] = field(default_factory=list)

    def add(self, decision: EditorialDecision) -> None:
        self.decisions.append(decision)

    def decision(self, decision_id: str) -> EditorialDecision | None:
        """The decision with this id, or None."""
        for entry in self.decisions:
            if entry.decision_id == decision_id:
                return entry
        return None

    def for_intent(self, intent_id: str) -> list[EditorialDecision]:
        """Every decision serving this intent."""
        return [entry for entry in self.decisions
                if entry.intent_id == intent_id]

    def dependents_of(self, decision_id: str) -> list[str]:
        """The decisions that transitively depend on this one."""
        reverse: dict[str, list[str]] = {}
        for entry in self.decisions:
            for dependency in entry.depends_on:
                reverse.setdefault(dependency, []).append(entry.decision_id)
        seen: set[str] = set()
        stack = [decision_id]
        while stack:
            current = stack.pop()
            for dependent in reverse.get(current, ()):
                if dependent not in seen:
                    seen.add(dependent)
                    stack.append(dependent)
        return sorted(seen)

    def to_dict(self) -> dict:
        return {
            "version": DECISION_VERSION,
            "intent_id": self.intent_id,
            "decisions": [entry.to_dict() for entry in self.decisions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DecisionGraph:
        if not isinstance(data, Mapping):
            raise EditorialDecisionError("a decision graph must be a mapping")
        return cls(
            intent_id=str(data.get("intent_id") or ""),
            decisions=[EditorialDecision.from_dict(entry)
                       for entry in (data.get("decisions") or ())],
        )


# ── The contract over a decision ──────────────────────────────────────


def validate_decision(decision: EditorialDecision) -> list[str]:
    """Every defect in a decision, as a list of problems. Empty is valid."""
    if not isinstance(decision, EditorialDecision):
        raise EditorialDecisionError("validate_decision needs a decision")
    problems: list[str] = []
    label = decision.decision_id or "<no decision_id>"

    if not decision.decision_id:
        problems.append(f"decision {label}: no decision_id")
    if not decision.intent_id:
        problems.append(f"decision {label}: no intent_id - a decision that "
                        "serves no intent is not an editorial decision")
    if not decision.statement:
        problems.append(f"decision {label}: no statement")
    if decision.state not in DECISION_STATES:
        problems.append(f"decision {label}: state {decision.state!r}; one of "
                        f"{list(DECISION_STATES)}")
    if decision.stated_by not in STATED_BY:
        problems.append(f"decision {label}: stated_by {decision.stated_by!r}; "
                        f"one of {list(STATED_BY)}")
    if decision.uncertainty is not None and not (
            0.0 <= decision.uncertainty <= 1.0):
        problems.append(f"decision {label}: uncertainty "
                        f"{decision.uncertainty} outside 0..1")
    if decision.decision_id in decision.depends_on:
        problems.append(f"decision {label}: depends on itself")

    for index, relation in enumerate(decision.relations):
        kind = relation.get("kind")
        if kind not in RELATION_KINDS:
            problems.append(
                f"decision {label}: relation[{index}] names kind {kind!r}; "
                f"one of {list(RELATION_KINDS)}")
        if not relation.get("id"):
            problems.append(
                f"decision {label}: relation[{index}] names no id - a "
                "relationship with nothing on either side is not one")

    for index, tradeoff in enumerate(decision.tradeoffs):
        axis = tradeoff.get("axis")
        against = tradeoff.get("against")
        if axis not in TRADEOFF_AXES or against not in TRADEOFF_AXES:
            problems.append(
                f"decision {label}: tradeoff[{index}] names {axis!r} against "
                f"{against!r}; both are axes of {list(TRADEOFF_AXES)}")
        elif axis == against:
            problems.append(
                f"decision {label}: tradeoff[{index}] weighs {axis} against "
                "itself")
        if not str(tradeoff.get("resolution") or "").strip():
            problems.append(
                f"decision {label}: tradeoff[{index}] records no resolution - "
                "a tradeoff nobody resolved is a worry, not a decision")

    for index, criterion in enumerate(decision.evaluation_criteria):
        if not str(criterion.get("criterion") or "").strip():
            problems.append(
                f"decision {label}: evaluation_criteria[{index}] names no "
                "criterion")
        if not str(criterion.get("measure") or "").strip():
            problems.append(
                f"decision {label}: evaluation_criteria[{index}] names no "
                "measure - a criterion nobody can look for is not one")

    for index, operation in enumerate(decision.operations):
        if not operation.op:
            problems.append(f"decision {label}: operation[{index}] names no op")
        if operation.kind not in edit_request.KINDS:
            problems.append(
                f"decision {label}: operation[{index}] names kind "
                f"{operation.kind!r}; one of {list(edit_request.KINDS)}")
        for dependency in operation.depends_on:
            if not isinstance(dependency, int) or dependency < 0 \
                    or dependency >= len(decision.operations):
                problems.append(
                    f"decision {label}: operation[{index}] depends on "
                    f"{dependency!r}, outside 0..{len(decision.operations) - 1}")
            elif dependency == index:
                problems.append(
                    f"decision {label}: operation[{index}] depends on itself")

    # The approval-gated rules: a decision that is not merely a
    # candidate must carry what makes it executable.
    if decision.state in (APPROVED, COMPILED):
        if not decision.evidence_ids:
            problems.append(
                f"decision {label}: approved with no evidence - a proposed "
                "edit nobody can point at is not grounded (EDIT-010)")
        if not decision.intended_effect:
            problems.append(
                f"decision {label}: approved with no intended effect "
                "(EDIT-014)")
        if not decision.evaluation_criteria:
            problems.append(
                f"decision {label}: approved with no evaluation criteria "
                "(EDIT-014)")
        if not decision.operations:
            problems.append(
                f"decision {label}: approved with no operation - an approved "
                "decision that changes nothing is not a decision")
        if not decision.approval:
            problems.append(
                f"decision {label}: approved with no approval record - "
                "nothing says who accepted it")
    if (decision.approval is not None
            and not decision.approval.get("by")):
        problems.append(
            f"decision {label}: the approval names nobody")
    return problems


def validate_graph(graph: DecisionGraph) -> list[str]:
    """Every defect in the graph, including the ones no single decision shows."""
    problems: list[str] = []
    if not isinstance(graph, DecisionGraph):
        raise EditorialDecisionError("validate_graph needs a DecisionGraph")
    seen: set[str] = set()
    for decision in graph.decisions:
        problems.extend(validate_decision(decision))
        if not decision.decision_id:
            continue
        if decision.decision_id in seen:
            problems.append(
                f"decision {decision.decision_id}: duplicate decision_id")
        seen.add(decision.decision_id)
    for decision in graph.decisions:
        for dependency in decision.depends_on:
            if dependency not in seen:
                problems.append(
                    f"decision {decision.decision_id}: depends on "
                    f"{dependency!r}, which is not in the graph")
    problems.extend(_dependency_cycles(graph))
    return problems


def _dependency_cycles(graph: DecisionGraph) -> list[str]:
    """Every dependency cycle in the graph, named as a trail."""
    problems: list[str] = []
    edges = {entry.decision_id: list(entry.depends_on)
             for entry in graph.decisions if entry.decision_id}
    for start in edges:
        stack = [(start, (start,))]
        visiting: set[str] = set()
        while stack:
            current, trail = stack.pop()
            if current in visiting:
                problems.append(
                    "decisions depend in a cycle: "
                    + " -> ".join(str(node) for node in trail))
                continue
            visiting.add(current)
            for dependency in edges.get(current, ()):
                if dependency in trail:
                    problems.append(
                        "decisions depend in a cycle: "
                        + " -> ".join(str(node) for node in (*trail, dependency)))
                    continue
                stack.append((dependency, (*trail, dependency)))
            visiting.discard(current)
    return problems


# ── Compiling into an edit request (EDIT-015) ────────────────────────


def _order(graph: DecisionGraph, decision_ids: Sequence[str]
           ) -> list[EditorialDecision]:
    """The named decisions in dependency order, stable on author order."""
    by_id = {entry.decision_id: entry for entry in graph.decisions}
    chosen = [by_id[decision_id] for decision_id in decision_ids
              if decision_id in by_id]
    missing = [decision_id for decision_id in decision_ids
               if decision_id not in by_id]
    if missing:
        raise EditorialDecisionError(
            f"the graph holds no decision {missing}")
    chosen_ids = {entry.decision_id for entry in chosen}
    ordered: list[EditorialDecision] = []
    done: set[str] = set()
    remaining = list(chosen)
    while remaining:
        progressed = False
        for entry in list(remaining):
            dependencies = [dep for dep in entry.depends_on
                            if dep in chosen_ids]
            if all(dep in done for dep in dependencies):
                ordered.append(entry)
                done.add(entry.decision_id)
                remaining.remove(entry)
                progressed = True
        if not progressed:
            raise EditorialDecisionError(
                "the decisions depend in a cycle and cannot be ordered: "
                + ", ".join(entry.decision_id for entry in remaining))
    return ordered


def _compile_operation(decision: EditorialDecision,
                       operation: DecisionOperation,
                       index: int,
                       depends_on: Sequence[int],
                       ) -> edit_request.EditOperation:
    """One decision operation as the request's operation.

    The effect is the OPERATION's own when it states one, and the
    decision's otherwise.  Neither may be absent: the request's own
    contract refuses an operation with no expected effect, and this
    module refuses it first, by name, so the failure says which
    decision and which operation.
    """
    effect = operation.effect or decision.intended_effect
    if not effect.strip():
        raise EditorialDecisionError(
            f"decision {decision.decision_id!r} operation[{index}] "
            f"({operation.op!r}) states no effect and its decision states "
            "none either - the execution layer never writes the editor's "
            "intent")
    # The flat op is the ONE thing the backends evaluate, so the
    # decision's target and params are joined into it through the
    # request's own split rule - the same rule `from_edit_program` uses.
    flat = dict(operation.params)
    flat.update(operation.target)
    target, params = edit_request.split_target(flat)
    return edit_request.EditOperation(
        op=operation.op,
        target=target,
        params=params,
        kind=operation.kind,
        expected_effect=effect,
        depends_on=tuple(sorted(depends_on)),
        preconditions=tuple(dict(row) for row in operation.preconditions),
        postconditions=tuple(dict(row) for row in operation.postconditions),
    )


def compile_to_edit_request(graph: DecisionGraph, *,
                             project: str,
                             timeline: str,
                             decision_ids: Sequence[str] | None = None,
                             intent: Any = None,
                             request_id: str = "",
                             idempotency_key: str = "",
                             expected_base_generation: int | None = None,
                             expected_model_revision: int | None = None,
                             originator: Mapping[str, Any] | None = None,
                             ) -> edit_request.EditRequest:
    """Compile approved decisions into the request AXI executes.

    `intent` is the canonical `editorial_intent.EditorialIntent` the
    decisions serve.  It is consulted for two things only: that the
    request the decision serves exists, and that the request's autonomy
    and evidence bar are satisfied.  The compiler reads it; it never
    writes to it and never fills a gap in it.
    """
    if not isinstance(graph, DecisionGraph):
        raise EditorialDecisionError(
            "compile_to_edit_request needs a DecisionGraph")
    chosen = [entry for entry in graph.decisions
              if decision_ids is None or entry.decision_id in decision_ids]
    if not chosen:
        raise EditorialDecisionError(
            "no decisions to compile - an empty request is not a request")

    for decision in chosen:
        _check_compilable(decision, intent)

    ordered = _order(graph, [entry.decision_id for entry in chosen])
    counts = {entry.decision_id: len(entry.operations) for entry in ordered}
    offsets: dict[str, int] = {}
    running = 0
    for entry in ordered:
        offsets[entry.decision_id] = running
        running += counts[entry.decision_id]

    operations: list[edit_request.EditOperation] = []
    for decision in ordered:
        offset = offsets[decision.decision_id]
        for index, operation in enumerate(decision.operations):
            depends = {offset + dependency
                       for dependency in operation.depends_on}
            # A decision that waits for another waits for every
            # operation of that one, in order.
            for dependency in decision.depends_on:
                if dependency in offsets:
                    depends.update(offsets[dependency] + step
                                   for step in range(counts[dependency]))
            operations.append(
                _compile_operation(decision, operation, index, depends))

    names = ", ".join(entry.decision_id for entry in ordered)
    request_originator = dict(originator or {})
    request_originator.setdefault("kind", "instruction")
    request_originator.setdefault("name", "editorial decision")
    request_originator.setdefault("detail", names)
    resolved_id = request_id or (
        f"editorial:{graph.intent_id or 'intent'}:{_digest(names)}")
    resolved_key = idempotency_key or resolved_id
    return edit_request.EditRequest(
        id=resolved_id,
        project=project,
        timeline=timeline,
        operations=tuple(operations),
        expected_base_generation=expected_base_generation,
        expected_model_revision=expected_model_revision,
        originator=request_originator,
        idempotency_key=resolved_key,
    )


def _digest(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _check_compilable(decision: EditorialDecision, intent: Any) -> None:
    """Refuse a decision that may not become a physical operation."""
    label = decision.decision_id or "<no decision_id>"
    if decision.state == CANDIDATE:
        raise EditorialDecisionError(
            f"decision {label!r} is a candidate - only an approved "
            "decision becomes a request")
    if decision.state == REJECTED:
        raise EditorialDecisionError(
            f"decision {label!r} was rejected - it does not become a request")
    if decision.state not in (APPROVED, COMPILED):
        raise EditorialDecisionError(
            f"decision {label!r} is in state {decision.state!r}")
    if not decision.evidence_ids:
        raise EditorialDecisionError(
            f"decision {label!r} rests on no evidence - a proposed edit "
            "nobody can point at is not grounded")
    if not decision.intended_effect:
        raise EditorialDecisionError(
            f"decision {label!r} states no intended effect - the request "
            "would have to invent one")
    if not decision.evaluation_criteria:
        raise EditorialDecisionError(
            f"decision {label!r} states no evaluation criteria - nothing "
            "would show whether the effect landed")
    if not decision.operations:
        raise EditorialDecisionError(
            f"decision {label!r} authorises no operation")
    if intent is not None:
        request = intent.request(decision.request_id) if decision.request_id \
            else None
        if decision.request_id and request is None:
            raise EditorialDecisionError(
                f"decision {label!r} serves request "
                f"{decision.request_id!r}, which the intent does not carry")
        if request is not None:
            from library.tools import editorial_intent

            absent = editorial_intent.missing_lanes(request)
            if absent:
                raise EditorialDecisionError(
                    f"decision {label!r} serves request "
                    f"{request.request_id!r}, whose evidence bar is unmet "
                    f"({list(absent)}) - act on it once the evidence exists")
            if request.autonomy == editorial_intent.NEEDS_CLARIFICATION:
                raise EditorialDecisionError(
                    f"decision {label!r} serves request "
                    f"{request.request_id!r}, which needs clarification: "
                    f"{request.question or 'the request asks nothing'}")
            if (request.autonomy == editorial_intent.NEEDS_APPROVAL
                    and not decision.approval):
                raise EditorialDecisionError(
                    f"decision {label!r} serves request "
                    f"{request.request_id!r}, which needs approval, and "
                    "carries no approval")


# ── Deriving decisions from an edit program ───────────────────────────
#
# The revise path already translates a captain's note into a program of
# algebra ops, each with the effect its author stated.  That program is
# a set of CANDIDATE decisions: the compiler above turns the approved
# ones into the request.  This is the connection - the program is not a
# second representation, it is where the candidates come from.


def _flat_op(raw: Mapping[str, Any], index: int) -> dict:
    """One program op as the flat algebra op the backends evaluate.

    The program's own `params` are the op's parameters; the keys beside
    them are the same op's parameters too.  The keys that are NOT op
    parameters - the op's name, its stated effect, its kind and its
    dependencies - are taken out here, exactly as the request's own
    program reader takes them out, so the two cannot disagree about
    what an op carries.
    """
    if not isinstance(raw, Mapping):
        raise EditorialDecisionError(
            f"the program's op[{index}] must be a mapping")
    flat = dict(raw)
    op_id = str(flat.pop("op", "") or "")
    if not op_id:
        raise EditorialDecisionError(
            f"the program's op[{index}] names no op")
    nested = flat.pop("params", None)
    if isinstance(nested, Mapping):
        merged = dict(nested)
        for key, value in flat.items():
            merged.setdefault(key, value)
        flat = merged
    kind = str(flat.pop("kind", "") or edit_request.DIRECT)
    effect = str(flat.pop("expected_effect", "") or flat.pop("text", "") or "")
    # Evidence and criteria annotate the approved decision, not the
    # physical algebra operation the backend receives.
    flat.pop("evidence_ids", None)
    flat.pop("evaluation_criteria", None)
    depends = tuple(int(value) for value in (flat.pop("depends_on", None) or ()))
    pre = tuple(dict(row) for row in (flat.pop("preconditions", None) or ()))
    post = tuple(dict(row) for row in (flat.pop("postconditions", None) or ()))
    return {"op": op_id, "params": flat, "kind": kind, "effect": effect,
            "depends_on": depends, "preconditions": pre,
            "postconditions": post}


def decisions_from_program(program: Mapping[str, Any], *,
                            intent_id: str,
                            request_id: str = "",
                            approval: Mapping[str, Any] | None = None,
                            stated_by: str = "model",
                            evidence_ids: Sequence[str] = (),
                            ) -> list[EditorialDecision]:
    """The candidate decisions an edit program carries, one per operation.

    Each op states its own effect (`text` in the program's own
    vocabulary), so the decision inherits a declared effect rather than
    one written here.  The decisions are CANDIDATE: the caller approves
    them, and only then do they compile.
    """
    if not isinstance(program, Mapping):
        raise EditorialDecisionError("an edit program must be a mapping")
    ops = program.get("ops") or []
    decisions = []
    for index, raw in enumerate(ops):
        flat = _flat_op(raw, index)
        operation_evidence = raw.get("evidence_ids")
        operation_criteria = raw.get("evaluation_criteria")
        decisions.append(EditorialDecision(
            decision_id=f"{intent_id}:decision:{index}",
            intent_id=intent_id,
            request_id=request_id,
            statement=flat["effect"] or flat["op"],
            state=CANDIDATE,
            stated_by=stated_by,
            evidence_ids=tuple(str(link) for link in
                               (operation_evidence if operation_evidence is not None
                                else evidence_ids or ())),
            evaluation_criteria=tuple(dict(row) for row in
                                      (operation_criteria or ())),
            intended_effect=flat["effect"],
            operations=(DecisionOperation(
                op=flat["op"],
                params=flat["params"],
                kind=flat["kind"],
                effect=flat["effect"],
                depends_on=flat["depends_on"],
                preconditions=flat["preconditions"],
                postconditions=flat["postconditions"]),),
            approval=(None if approval is None else dict(approval)),
        ))
    return decisions


def request_from_program(program: Mapping[str, Any], *,
                         project: str,
                         timeline: str,
                         intent_id: str = "",
                         request_id: str = "",
                         idempotency_key: str = "",
                         originator: Mapping[str, Any] | None = None,
                         evidence_ids: Sequence[str] = (),
                         criteria: Sequence[Mapping[str, Any]] = (),
                         approval: Mapping[str, Any] | None = None,
                         intent: Any = None) -> edit_request.EditRequest:
    """The request an edit program means, read through the decision layer.

    This is the connection to the paths that already translate a request
    into a program: the program's ops ARE decisions, each stating its
    own effect, and the request this returns carries the SAME
    operations, in the SAME order, with the SAME effects as
    `edit_request.from_edit_program` builds from the same program - the
    decision layer is a second READER of one program, not a second
    execution path.  What it adds is the record beside the request:
    which decisions were approved, on what evidence, against what
    criteria.
    """
    if not isinstance(program, Mapping):
        raise EditorialDecisionError("an edit program must be a mapping")
    resolved_intent = str(intent_id or program.get("intent_id") or "")
    candidates = decisions_from_program(
        program, intent_id=resolved_intent, evidence_ids=evidence_ids)
    if criteria:
        candidates = [replace(decision,
                              evaluation_criteria=tuple(dict(row)
                                                       for row in criteria))
                      for decision in candidates]
    approved = approve(candidates, **(dict(approval or {"by": "captain"})))
    graph = DecisionGraph(intent_id=resolved_intent, decisions=approved)
    return compile_to_edit_request(
        graph, project=project, timeline=timeline, intent=intent,
        request_id=request_id or str(program.get("id") or ""),
        idempotency_key=idempotency_key
        or str(program.get("idempotency_key") or program.get("id") or ""),
        expected_base_generation=program.get("base_generation"),
        originator=originator)


def approve(decisions: Sequence[EditorialDecision], *,
            by: str = "captain",
            note: str = "",
            at: str = "") -> list[EditorialDecision]:
    """The same decisions, approved, with the approval recorded.

    Approval is a RECORD, not a flag: it names who accepted it, when,
    and in whose words.  A decision whose evidence bar is unmet, whose
    effect is unstated or whose criteria are absent is NOT approved by
    this call - it is refused, so approval cannot be the thing that
    hides a gap.
    """
    approved = []
    for decision in decisions:
        gaps = []
        if not decision.evidence_ids:
            gaps.append("it rests on no evidence")
        if not decision.intended_effect:
            gaps.append("it states no intended effect")
        if not decision.evaluation_criteria:
            gaps.append("it states no evaluation criteria")
        if not decision.operations:
            gaps.append("it authorises no operation")
        if gaps:
            raise EditorialDecisionError(
                f"decision {decision.decision_id!r} cannot be approved: "
                + "; ".join(gaps))
        approved.append(replace(
            decision,
            state=APPROVED,
            approval={"by": by, "note": note, "at": at},
        ))
    return approved


def compile_document(graph: DecisionGraph, *, project: str, timeline: str,
                     decision_ids: Sequence[str] | None = None,
                     intent: Any = None) -> dict:
    """The compiled request beside the decisions that produced it.

    The two travel together on purpose: a request with no decision
    behind it is a list of operations, and the decisions are what make
    them reviewable.
    """
    request = compile_to_edit_request(
        graph, project=project, timeline=timeline,
        decision_ids=decision_ids, intent=intent)
    return {
        "request": request.to_dict(),
        "decisions": [entry.to_dict() for entry in graph.decisions],
    }


# ── The CLI ───────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_decisions",
        description="read an editorial decision document, check it, and "
                    "compile the approved decisions into an edit request")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate a decision document")
    check.add_argument("path", help="the decision document (JSON)")
    compile_cmd = sub.add_parser(
        "compile", help="compile the approved decisions into an edit request")
    compile_cmd.add_argument("path", help="the decision document (JSON)")
    compile_cmd.add_argument("--project", required=True)
    compile_cmd.add_argument("--timeline", required=True)
    compile_cmd.add_argument("--intent", default="",
                              help="the canonical intent (JSON) the "
                                   "decisions serve")
    args = parser.parse_args(argv)

    document = json.loads(Path(args.path).read_text(encoding="utf-8"))
    graph = DecisionGraph.from_dict(document)
    problems = validate_graph(graph)
    if args.command == "check":
        print(json.dumps({"problems": problems,
                          "decisions": [entry.to_dict()
                                        for entry in graph.decisions]},
                         indent=2, ensure_ascii=False))
        return 1 if problems else 0

    intent = None
    if args.intent:
        from library.tools import editorial_intent

        intent = editorial_intent.EditorialIntent.from_dict(
            json.loads(Path(args.intent).read_text(encoding="utf-8")))
    request = compile_to_edit_request(graph, project=args.project,
                                      timeline=args.timeline, intent=intent)
    print(json.dumps(request.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
