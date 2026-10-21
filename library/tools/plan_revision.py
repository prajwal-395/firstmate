"""PLAN-007: the evidence-driven replanning loop.

A plan fails in four ways, and the engine has four places that already
know about them:

- the FEASIBILITY gate (PLAN-005, :mod:`library.tools.plan_feasibility`)
  refuses a plan that cannot run on the timeline it names;
- the GROUNDING stage (PLAN-003, :mod:`library.tools.target_grounding`)
  answers for every target role a plan names, and refuses a destructive
  change whose target was never established;
- the ACCEPTANCE harness (:mod:`library.tools.editorial_acceptance`) says
  which acceptance check a plan failed;
- the ROUTER (:mod:`library.tools.execution_router`) refuses a request
  that has no eligible route, in a closed category vocabulary.

Until now each of those REFUSED and the refusal stopped there.  The plan
that was refused was not carried back to the planner, so the planner
re-planned the whole plan - or did not re-plan at all - and the work
that had already been verified was paid for again.

This module is the LOOP that carries the evidence back:

    evidence  ->  a NARROW revision  ->  the planner (once)  ->  a plan

THE FOUR RULES THE LOOP KEEPS.

**Revise only the affected part.**  An operation that no piece of
evidence names is PRESERVED, byte for byte, and the planner is shown
only the affected operations.  A planner that answers with operations
outside the affected set is refused: that is a new plan, not a
revision.

**Preserve work that has already been verified.**  An operation the
evidence says was already carried (``KIND_PRESERVED``) survives every
revision, and a refusal that names it does not remove it.

**Do not repeat an expensive model call with unchanged context.**  The
context of a revision is the affected operations plus the evidence that
bears on them, and it is FINGERPRINTED.  A fingerprint the loop has
already answered is answered from the cache: no model call is made, and
the receipt says how many calls that saved.  A planner that answers
without changing anything is the same thing - the next turn has the
same fingerprint, so the next turn is free.

**Stop when the remaining problem genuinely requires user judgment.**
  The loop stops with a QUESTION when the evidence shows the thing the
  request names is not in the footage and no re-plan can establish it,
  and it stops with a named CAPABILITY GAP when the engine has no route
  for the operation.  Neither is a silent drop: the receipt names what
  is missing and who has to supply it.  A refusal to guess is the
  correct outcome (AGENTS.md 10.5).

WHAT THE LOOP DOES NOT DO.

It does not choose a target, a route or a plan.  Every decision it
makes is either MECHANICAL (an operation the evidence proves cannot run
is removed; an operation whose target the evidence names exactly is
re-targeted at that identity) or it is DELEGATED to the planner the
caller supplies.  With no planner supplied the loop does the mechanical
part and reports the rest as missing evidence; it never invents the
plan (AGENTS.md 10.5).

A measurement nobody took is not evidence: an UNDETERMINED check is
reported as missing evidence and never removes an operation
(AGENTS.md 10.3, 10.4).

    python3 -m library.tools.plan_revision --help
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

FORMAT = "ren-plan-revision/1"
"""The format of a revision receipt."""

# ── The evidence ──────────────────────────────────────────────────────

#: Where a piece of evidence came from.  The four sources are the four
#: places the engine already refuses, and nothing else is evidence.
SOURCE_FEASIBILITY = "feasibility"
SOURCE_GROUNDING = "grounding"
SOURCE_ACCEPTANCE = "acceptance"
SOURCE_ROUTING = "routing_refusal"
SOURCES: tuple[str, ...] = (
    SOURCE_FEASIBILITY,
    SOURCE_GROUNDING,
    SOURCE_ACCEPTANCE,
    SOURCE_ROUTING,
)

#: What a piece of evidence PROVES.  The kinds are the loop's whole
#: vocabulary; a piece of evidence whose kind is not here is context
#: and changes nothing.
KIND_IMPOSSIBLE = "impossible"
"""The operation cannot run here.  The evidence names a reason."""

KIND_NOT_GROUNDED = "not_grounded"
"""The target of the operation was not established."""

KIND_NOT_COVERED = "not_covered"
"""An acceptance criterion the plan does not cover failed."""

KIND_CHECK_FAILED = "check_failed"
"""An acceptance check the plan's work failed."""

KIND_MISSING = "missing"
"""The evidence that would settle the question was not gathered."""

KIND_RESOLVED = "resolved"
"""A verified identity for a target role.  Context, not a problem."""

KIND_PRESERVED = "preserved"
"""The operation was already carried and verified.  Context, not a
problem, and it survives every revision."""

PROBLEM_KINDS: tuple[str, ...] = (
    KIND_IMPOSSIBLE,
    KIND_NOT_GROUNDED,
    KIND_NOT_COVERED,
    KIND_CHECK_FAILED,
)

#: The feasibility checks that mean the ENGINE has no route, as opposed
#: to a state that does not satisfy a recorded predicate.  The first
#: group is a capability gap; the second is a condition that may hold
#: elsewhere.
CAPABILITY_CHECKS: frozenset[str] = frozenset({
    "operation_known",
    "executor_exists",
    "route_eligible",
    "capability_query",
})

# ── The states a revision can end in ──────────────────────────────────

STATUS_REVISED = "revised"
"""A narrow revision exists: the plan changed and what changed is
named."""

STATUS_NO_CHANGE = "no_change"
"""No evidence bore on the plan, so the plan stands."""

STATUS_NEEDS_JUDGEMENT = "needs_judgement"
"""The remaining problem is the user's call, and the receipt asks."""

STATUS_NEEDS_CAPABILITY = "needs_capability"
"""The engine has no route for an operation the plan names."""

STATUS_NEEDS_EVIDENCE = "needs_evidence"
"""The evidence that would settle the question was not gathered, and
the receipt names it."""

STATUSES: tuple[str, ...] = (
    STATUS_REVISED,
    STATUS_NO_CHANGE,
    STATUS_NEEDS_JUDGEMENT,
    STATUS_NEEDS_CAPABILITY,
    STATUS_NEEDS_EVIDENCE,
)

#: The number of planner calls one revision may make.  The first one
#: included, and the bound is the same shape the post-bridge retry
#: uses: at the bound the loop stops and says so.
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Evidence:
    """One piece of evidence, and what it bears on.

    ``op`` and ``role`` say WHAT the evidence is about, and either may
    be empty: an evidence with neither bears on the whole plan.
    ``kind`` says what it proves, ``reason`` says it in words, and
    ``detail`` carries whatever a reader needs to check the claim.
    """

    kind: str
    reason: str = ""
    op: str = ""
    role: str = ""
    source: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "op": self.op,
            "role": self.role,
            "source": self.source,
            "detail": dict(self.detail),
        }

    def bears_on(self, op: str, role: str) -> bool:
        """Whether this evidence bears on one operation.

        An evidence that names NEITHER an operation nor a role bears on
        every operation: that is what a whole-plan failure is.
        """
        if not self.op and not self.role:
            return True
        if self.op and self.op == op:
            return True
        return bool(self.role and role and self.role == role)


# ── Collecting the evidence ───────────────────────────────────────────


def _get(obj: Any, key: str) -> Any:
    """Read a key from a mapping or an object, without assuming either."""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def from_feasibility(verdict: Any) -> list[Evidence]:
    """The evidence a feasibility verdict carries.

    A refusal is evidence that the operation cannot run here.  An
    UNDETERMINED check is MISSING evidence: the state was not read, so
    the operation is not known to be impossible and it is not removed.
    """
    findings = _get(verdict, "findings") or ()
    rows: list[Evidence] = []
    for finding in findings:
        status = str(_get(finding, "status") or "")
        op = str(_get(finding, "op") or "")
        check = str(_get(finding, "check") or "")
        reason = str(_get(finding, "reason") or "")
        if status == "infeasible":
            rows.append(Evidence(
                kind=KIND_IMPOSSIBLE, reason=reason, op=op,
                source=SOURCE_FEASIBILITY,
                detail={"check": check,
                        "capability_gap": check in CAPABILITY_CHECKS}))
        elif status == "undetermined":
            rows.append(Evidence(
                kind=KIND_MISSING, reason=reason, op=op,
                source=SOURCE_FEASIBILITY,
                detail={"check": check}))
    return rows


def from_grounding(receipt: Mapping[str, Any]) -> list[Evidence]:
    """The evidence a grounding receipt carries.

    A VERIFIED role is context: it names the identity a plan may be
    re-targeted at.  A role that is not verified is evidence that the
    target was not established, and the receipt says whether the
    surfaces were READ: a role the timeline does not carry and the
    evidence surface was never asked is missing evidence, while a role
    both surfaces do not carry is a premise the footage does not have.
    """
    rows: list[Evidence] = []
    targets = _get(receipt, "targets") or ()
    for target in targets:
        role = str(_get(target, "role") or "")
        state = str(_get(target, "state") or "")
        reason = str(_get(target, "reason") or "")
        identity = _get(target, "identity")
        if state == "verified":
            rows.append(Evidence(
                kind=KIND_RESOLVED, reason=reason, role=role,
                source=SOURCE_GROUNDING,
                detail={"identity": dict(identity) if isinstance(
                    identity, Mapping) else None,
                    "state": state}))
            continue
        surfaces_read = _surfaces_read(target)
        rows.append(Evidence(
            kind=KIND_NOT_GROUNDED, reason=reason, role=role,
            source=SOURCE_GROUNDING,
            detail={"state": state,
                    "surfaces_read": surfaces_read,
                    "premise_absent": bool(surfaces_read),
                    "missing_evidence": (
                        "" if surfaces_read else
                        "the evidence surface was not read, so the target "
                        "may exist and nothing has looked for it")}))
    return rows


def _surfaces_read(target: Any) -> bool:
    """Whether the EVIDENCE surface was READ about this target.

    The grounding receipt records every attempt; an attempt against the
    evidence surface that carried a model is what distinguishes "the
    surfaces do not carry it" from "nobody looked".  A surface that
    was asked with no model to answer it carries nothing.
    """
    attempts = _get(target, "attempts") or ()
    for attempt in attempts:
        if str(_get(attempt, "surface") or "") != "evidence":
            continue
        if bool(_get(attempt, "model_read")):
            return True
    return False


def from_acceptance(failures: Sequence[Mapping[str, Any]]) -> list[Evidence]:
    """The evidence a set of acceptance failures carries.

    A failure names the operation it is about when it can (``op``), and
    the criterion it failed either way.
    """
    rows: list[Evidence] = []
    for failure in failures or ():
        if not isinstance(failure, Mapping):
            continue
        rows.append(Evidence(
            kind=KIND_NOT_COVERED,
            reason=str(failure.get("reason") or ""),
            op=str(failure.get("op") or ""),
            role=str(failure.get("role") or ""),
            source=SOURCE_ACCEPTANCE,
            detail={"criterion": str(failure.get("criterion") or ""),
                    "check": str(failure.get("check") or "")}))
    return rows


def from_refusals(refusals: Sequence[Mapping[str, Any]]) -> list[Evidence]:
    """The evidence a set of routing refusals carries.

    A refusal in the ``unsupported`` category is evidence that the
    operation cannot run here.  The other categories - ``stale``,
    ``conflict``, ``backend_failure``, ``broken`` - are not: a stale
    base or a conflict is a condition that may clear, so the refusal is
    reported as missing evidence and the operation is not removed.
    """
    rows: list[Evidence] = []
    for refusal in refusals or ():
        if not isinstance(refusal, Mapping):
            continue
        category = str(refusal.get("category") or "")
        reason = str(refusal.get("reason") or "")
        op = str(refusal.get("op") or "")
        if category == "unsupported":
            rows.append(Evidence(
                kind=KIND_IMPOSSIBLE, reason=reason, op=op,
                source=SOURCE_ROUTING,
                detail={"category": category, "capability_gap": True}))
        else:
            rows.append(Evidence(
                kind=KIND_MISSING, reason=reason, op=op,
                source=SOURCE_ROUTING, detail={"category": category}))
    return rows


def collect(*, feasibility: Any = None,
            grounding: Mapping[str, Any] | None = None,
            acceptance: Sequence[Mapping[str, Any]] = (),
            refusals: Sequence[Mapping[str, Any]] = (),
            ) -> list[Evidence]:
    """Every piece of evidence the engine holds about one plan."""
    rows: list[Evidence] = []
    if feasibility is not None:
        rows.extend(from_feasibility(feasibility))
    if grounding is not None:
        rows.extend(from_grounding(grounding))
    rows.extend(from_acceptance(acceptance))
    rows.extend(from_refusals(refusals))
    return rows


# ── The cache ─────────────────────────────────────────────────────────


class RevisionCache:
    """The revisions this machine has already paid for.

    The key is the fingerprint of the revision's CONTEXT, and a hit is
    a model call that does not have to be made again.  The cache holds
    answers, not plans: it is the loop's memory of what the planner
    said about a context it has already seen.
    """

    def __init__(self, entries: Mapping[str, Any] | None = None) -> None:
        self._entries: dict[str, Any] = dict(entries or {})

    def get(self, key: str) -> Any:
        return self._entries.get(key)

    def put(self, key: str, answer: Any) -> None:
        self._entries[key] = answer

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._entries)


def fingerprint(operations: Sequence[Mapping[str, Any]],
                evidence: Sequence[Evidence]) -> str:
    """The identity of a revision's CONTEXT.

    The context is the affected operations and the evidence that bears
    on them.  Two turns that carry the same context are the same
    question, and the second one is free.
    """
    payload = {
        "operations": [dict(operation) for operation in operations or ()],
        "evidence": sorted(
            (dict(item.as_dict()) for item in evidence or ()),
            key=lambda row: (row.get("kind") or "", row.get("op") or "",
                             row.get("role") or "", row.get("reason") or "")),
    }
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── The revision ──────────────────────────────────────────────────────


def _slot(operation: Mapping[str, Any]) -> str:
    """The name of one operation's slot in the plan."""
    return (f"{operation.get('op') or ''!s}:"
            f"{operation.get('target_role') or ''!s}")


def _identity_of(detail: Mapping[str, Any]) -> Mapping[str, Any]:
    """The verified identity a piece of evidence carries, if it carries one."""
    identity = detail.get("identity")
    return identity if isinstance(identity, Mapping) else {}


def _planner_answer(answer: Any, affected: Sequence[Mapping[str, Any]],
                    ) -> dict[str, Any]:
    """The planner's answer, checked against the affected set.

    A planner may return FEWER operations than it was shown - dropping
    one is a revision - and it may replace one with another operation.
    It may not answer with an operation that was not affected: that is
    a new plan, not a revision of this one, and the loop refuses it.
    """
    if not isinstance(answer, Mapping):
        raise ValueError(
            "the planner's answer is not a mapping, so it names no "
            "revision")
    operations = answer.get("operations")
    if not isinstance(operations, list):
        raise ValueError(
            "the planner's answer carries no 'operations' list, so it "
            "names no revision")
    allowed = {_slot(operation) for operation in affected}
    # A role that ONE affected operation carries may be answered with a
    # DIFFERENT operation: that is a revision of the affected part, and
    # the plan may reach the same goal by another way.  A role that
    # several affected operations carry cannot, because the answer would
    # not say which of them it replaces.
    by_role: dict[str, list[str]] = {}
    for operation in affected:
        role = str(operation.get("target_role") or "")
        if role:
            by_role.setdefault(role, []).append(_slot(operation))
    revised: list[dict[str, Any]] = []
    replacements: dict[str, str] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            raise ValueError(
                "the planner's answer carries an operation that is not a "
                "mapping")
        op = str(operation.get("op") or "")
        role = str(operation.get("target_role") or "")
        if not op:
            raise ValueError(
                "the planner's answer carries an operation with no 'op'")
        slot = _slot({"op": op, "target_role": role})
        if slot in allowed:
            revised.append(dict(operation))
            continue
        candidates = by_role.get(role) or []
        if role and len(candidates) == 1:
            replacements[candidates[0]] = slot
            revised.append(dict(operation))
            continue
        raise ValueError(
            f"the planner's answer names the operation {op!r} on the "
            f"role {role!r}, which no evidence bore on: a revision may "
            "only change the affected part of the plan")
    questions = [dict(question) for question in (answer.get("questions") or ())
                 if isinstance(question, Mapping)]
    unresolved = [dict(row) for row in (answer.get("unresolved") or ())
                  if isinstance(row, Mapping)]
    covers = [str(criterion) for criterion in (answer.get("covers") or ())]
    return {"operations": revised, "questions": questions,
            "unresolved": unresolved, "covers": covers,
            "replacements": replacements}


def replan(operations: Sequence[Mapping[str, Any]],
           evidence: Sequence[Evidence],
           *, planner: Any = None,
           cache: RevisionCache | None = None,
           max_attempts: int = MAX_ATTEMPTS,
           ) -> dict[str, Any]:
    """Revise a plan, narrowly, from the evidence about it.

    `planner` is the caller's model, called ONCE for the affected part
    of the plan.  It is handed the affected operations and the evidence
    that bears on them, and it answers with the operations that survive
    (fewer is a revision), the acceptance criteria the revised plan now
    covers, and the questions it could not answer.  With no planner the
    loop does the mechanical part only.

    `cache` is the machine's memory of the contexts it has already
    answered: a context the cache holds is answered from it, and no
    model call is made.
    """
    plan = [dict(operation) for operation in operations or ()]
    rows = list(evidence or ())
    problems = [row for row in rows if row.kind in PROBLEM_KINDS]
    missing = [row for row in rows if row.kind == KIND_MISSING]
    resolved = [row for row in rows if row.kind == KIND_RESOLVED]
    done = [row for row in rows if row.kind == KIND_PRESERVED]

    # The preserved operations: nothing PROVES them wrong, and the work
    # that was already verified survives whatever else happens.  An
    # operation that bears only on missing evidence is preserved too -
    # a measurement nobody took never removes anything.
    affected: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    retargets: list[dict[str, Any]] = []
    for operation in plan:
        op = str(operation.get("op") or "")
        role = str(operation.get("target_role") or "")
        bears = [row for row in problems if row.bears_on(op, role)]
        already = any(row.op == op for row in done)
        if bears and not already:
            affected.append(operation)
        else:
            # A preserved operation may still be RE-TARGETED: the
            # grounding stage answers for every target role a plan
            # names, and a VERIFIED role names the identity the
            # operation acts on.  Work that was already carried is
            # never re-targeted.
            identity = ({} if already
                        else _verified_identity(role, resolved))
            if identity:
                bound = dict(operation)
                bound["unique_id"] = identity.get("unique_id", "")
                bound["name"] = identity.get("name", "")
                bound["row"] = identity.get("row", "")
                retargets.append({
                    "op": op, "role": role, "identity": dict(identity)})
                preserved.append(bound)
            else:
                preserved.append(operation)

    receipt: dict[str, Any] = {
        "format": FORMAT,
        "status": STATUS_NO_CHANGE,
        "operations": list(preserved),
        "preserved": [dict(operation) for operation in preserved],
        "affected": [dict(operation) for operation in affected],
        "dropped": [],
        "held": [],
        "replaced": [],
        "retargeted": retargets,
        "established": [],
        "questions": [],
        "missing_evidence": [dict(row.as_dict()) for row in missing],
        "uncovered_criteria": [],
        "covered_criteria": [],
        "capability_gaps": [],
        "model_calls": {"made": 0, "saved": 0, "contexts": []},
        "attempts": 0,
    }
    if not affected:
        receipt["capability_gaps"] = _capability_gap(problems)
        receipt["status"] = (STATUS_NEEDS_CAPABILITY
                             if receipt["capability_gaps"]
                             else STATUS_NO_CHANGE)
        return receipt

    # The mechanical part: an operation the evidence proves cannot run
    # is removed, and an operation whose target the evidence names
    # exactly is re-targeted at that identity.  Neither is a creative
    # decision, so neither costs a model call.
    # The survivors keep the SLOT they came from, so a replacement
    # operation stays where the operation it replaced stood.
    survivors: list[tuple[str, dict[str, Any]]] = []
    judged: list[dict[str, Any]] = []
    for operation in affected:
        op = str(operation.get("op") or "")
        role = str(operation.get("target_role") or "")
        bearing = [row for row in problems if row.bears_on(op, role)]
        impossible = [row for row in bearing if row.kind == KIND_IMPOSSIBLE]
        ungrounded = [row for row in bearing if row.kind == KIND_NOT_GROUNDED]
        uncovered = [row for row in bearing
                     if row.kind in (KIND_NOT_COVERED, KIND_CHECK_FAILED)]
        dropped = bool(impossible)
        if dropped:
            receipt["dropped"].append({
                "op": op, "role": role, "kind": KIND_IMPOSSIBLE,
                "reason": impossible[0].reason,
                "source": impossible[0].source,
                "covers": ("what this operation covered is no longer "
                           "covered by the plan"),
            })
        # The current version of the operation: the evidence may name
        # the identity it acts on, and an operation that acts on a
        # verified identity is the one the plan should carry.
        current = dict(operation)
        identity = _verified_identity(role, resolved)
        if identity and not dropped:
            current["unique_id"] = identity.get("unique_id", "")
            current["name"] = identity.get("name", "")
            current["row"] = identity.get("row", "")
            receipt["retargeted"].append({
                "op": op, "role": role, "identity": dict(identity)})
        needs: list[str] = []
        if ungrounded:
            premise_absent = any(bool(row.detail.get("premise_absent"))
                                  for row in ungrounded)
            receipt["held"].append({
                "op": op, "role": role,
                "reason": ungrounded[0].reason,
                "surfaces_read": any(bool(row.detail.get("surfaces_read"))
                                     for row in ungrounded),
                "premise_absent": premise_absent,
                "missing_evidence": next(
                    (str(row.detail.get("missing_evidence") or "")
                     for row in ungrounded), ""),
            })
            if premise_absent:
                # Both surfaces were asked and neither carries the
                # thing: the request's own referent is not in the
                # footage, and only the user can say what to do.
                _ask(receipt, {
                    "question": (f"the request names {role!r} and the "
                                 "footage does not carry it: what should "
                                 "the plan do instead?"),
                    "op": op, "role": role,
                    "reason": ungrounded[0].reason,
                })
            else:
                needs.append("target")
        if uncovered:
            needs.append("coverage")
        if needs:
            judged.append({"operation": current, "dropped": dropped,
                           "needs": needs})
        elif not dropped:
            survivors.append((_slot(operation), current))
        for row in uncovered:
            criterion = str(row.detail.get("criterion") or row.check)
            if criterion:
                receipt["uncovered_criteria"].append({
                    "criterion": criterion,
                    "op": row.op, "role": row.role,
                    "reason": row.reason,
                })

    # The part that is a JUDGEMENT goes to the planner, once, with the
    # narrow context.  A context the cache already holds is free.
    if judged:
        judged_operations = [dict(entry["operation"]) for entry in judged]
        context_evidence = [
            row for row in problems
            if any(row.bears_on(str(operation.get("op") or ""),
                                str(operation.get("target_role") or ""))
                   for operation in judged_operations)]
        key = fingerprint(judged_operations, context_evidence)
        receipt["model_calls"]["contexts"].append(key)
        cached = cache.get(key) if cache is not None else None
        answer: Any = None
        if cached is not None:
            receipt["model_calls"]["saved"] += 1
            answer = cached
        elif planner is None:
            answer = None
        elif receipt["attempts"] >= max_attempts:
            receipt["attempts"] += 1
        else:
            receipt["attempts"] += 1
            receipt["model_calls"]["made"] += 1
            answer = planner({
                "operations": judged_operations,
                "evidence": [row.as_dict() for row in context_evidence],
                "resolved": [dict(row.detail) for row in resolved],
            })
            if cache is not None:
                cache.put(key, answer)
        if isinstance(answer, Mapping):
            checked = _planner_answer(answer, judged_operations)
            # A replaced operation answers the slot it replaces, so the
            # slot - not the operation's own name - is what the loop
            # matches on.
            replacements = checked.get("replacements") or {}
            answered: dict[str, str] = {}
            for operation in checked["operations"]:
                slot = _slot(operation)
                answered[slot] = slot
            for original, returned in replacements.items():
                answered[original] = returned
            unresolved_slots = {
                _slot({"op": str(row.get("op") or ""),
                       "target_role": str(row.get("role") or "")})
                for row in checked["unresolved"]}
            for entry in judged:
                operation = entry["operation"]
                slot = _slot(operation)
                if slot in answered:
                    returned = answered[slot]
                    revised = next(item for item in checked["operations"]
                                   if _slot(item) == returned)
                    receipt["established"].append({
                        "op": str(operation.get("op") or ""),
                        "role": str(operation.get("target_role") or ""),
                        "note": ("the target was established"
                                 + (", and the operation still has no route"
                                    if entry["dropped"] else "")),
                    })
                    # The target this slot was held for is established, so
                    # the slot is no longer waiting for evidence.
                    receipt["held"] = [row for row in receipt["held"]
                                        if _slot({"op": str(row.get("op")
                                                            or ""),
                                                  "target_role": str(
                                                      row.get("role") or "")})
                                        != slot]
                    if not entry["dropped"]:
                        survivors.append((slot, revised))
                    if returned != slot:
                        receipt["replaced"].append({
                            "slot": slot, "operation": returned,
                            "note": ("the affected operation was replaced "
                                     "by another way of reaching the same "
                                     "role")})
                    continue
                reason = next((str(row.get("reason") or "")
                               for row in checked["unresolved"]
                               if _slot({"op": str(row.get("op") or ""),
                                         "target_role": str(row.get("role")
                                                           or "")})
                               == slot), "")
                if slot in unresolved_slots and not reason:
                    reason = ("the target this operation names is not in "
                              "the footage")
                if not reason:
                    reason = "the planner did not answer for this operation"
                if not entry["dropped"]:
                    receipt["dropped"].append({
                        "op": str(operation.get("op") or ""),
                        "role": str(operation.get("target_role") or ""),
                        "kind": "planner",
                        "reason": reason,
                        "source": SOURCE_GROUNDING,
                        "covers": ("what this operation covered is no "
                                   "longer covered by the plan"),
                    })
                _ask(receipt, {
                    "question": (f"the plan's "
                                 f"{str(operation.get('op') or '')!r} on "
                                 f"{str(operation.get('target_role') or '')!r} "
                                 "could not be settled: what should it do?"),
                    "op": str(operation.get("op") or ""),
                    "role": str(operation.get("target_role") or ""),
                    "reason": reason,
                })
            for question in checked["questions"]:
                _ask(receipt, question)
            covered = set(checked["covers"])
            receipt["covered_criteria"] = sorted(covered)
            receipt["uncovered_criteria"] = [
                row for row in receipt["uncovered_criteria"]
                if row["criterion"] not in covered]

    # The revised plan keeps the ORDER of the plan it came from: a
    # revision changes what the plan does, not the sequence it does it
    # in.
    final_by_slot: dict[str, dict[str, Any]] = {}
    for operation in preserved:
        final_by_slot[_slot(operation)] = operation
    for slot, operation in survivors:
        final_by_slot[slot] = operation
    ordered: list[dict[str, Any]] = []
    for operation in plan:
        slot = _slot(operation)
        if slot in final_by_slot:
            ordered.append(final_by_slot.pop(slot))
    ordered.extend(final_by_slot.values())
    receipt["operations"] = ordered
    receipt["capability_gaps"] = _capability_gap(problems)
    receipt["status"] = _status(receipt)
    return receipt


def _verified_identity(role: str, resolved: Sequence[Evidence]
                       ) -> Mapping[str, Any]:
    """The identity a VERIFIED role names, when there is exactly one.

    Two receipts that name DIFFERENT identities for one role are two
    candidates, and a role with two candidates has no unique target.
    """
    if not role:
        return {}
    identities = [dict(_identity_of(row.detail)) for row in resolved
                  if row.role == role and _identity_of(row.detail)]
    unique = {json.dumps(item, sort_keys=True, default=str)
              for item in identities}
    if len(unique) != 1:
        return {}
    return identities[0]


def _ask(receipt: dict[str, Any], question: Mapping[str, Any]) -> None:
    """Record ONE question per slot.

    The same slot can be unsettled for two reasons - the footage does
    not carry the thing, and the planner could not settle it - and a
    user asked the same question twice has not been asked one question.
    The FIRST question for a slot is the one that stands.
    """
    slot = _slot({"op": str(question.get("op") or ""),
                  "target_role": str(question.get("role") or "")})
    for row in receipt["questions"]:
        if _slot({"op": str(row.get("op") or ""),
                  "target_role": str(row.get("role") or "")}) == slot:
            return
    receipt["questions"].append(dict(question))


def _capability_gap(problems: Sequence[Evidence]) -> list[dict[str, Any]]:
    """The operations the engine has no route for.

    A refusal that says the operation has no eligible route is a
    capability gap: no re-plan of this plan can produce it, and asking
    the user cannot either.  A refusal about the STATE - a predicate
    the runtime does not satisfy, a target that is not on the timeline -
    is not a capability gap: it names a condition, not a missing route.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in problems:
        if row.kind != KIND_IMPOSSIBLE or not row.op:
            continue
        if not bool(row.detail.get("capability_gap")):
            continue
        if row.op in seen:
            continue
        seen.add(row.op)
        rows.append({"op": row.op, "reason": row.reason,
                     "source": row.source})
    return rows


def _status(receipt: Mapping[str, Any]) -> str:
    """The state a revision ends in.

    The order matters: a question outranks a capability gap, because a
    user can answer a question and cannot answer a missing route; and
    both outrank a revision, because a plan that is still missing its
    target has not been revised into a plan that can run.
    """
    if receipt["questions"]:
        return STATUS_NEEDS_JUDGEMENT
    if receipt["held"]:
        return STATUS_NEEDS_EVIDENCE
    if receipt["capability_gaps"]:
        return STATUS_NEEDS_CAPABILITY
    if receipt["dropped"] or receipt["retargeted"]:
        return STATUS_REVISED
    return STATUS_NO_CHANGE


def loop(operations: Sequence[Mapping[str, Any]],
         gather: Any, *, planner: Any = None,
         cache: RevisionCache | None = None,
         max_attempts: int = MAX_ATTEMPTS) -> dict[str, Any]:
    """The loop: revise, gather again, revise again, until it settles.

    `gather` is the caller's evidence reader: it is handed the current
    plan and returns the evidence about it.  The loop stops when the
    evidence stops changing, when the revision settles, or at the
    bound - and a turn whose CONTEXT the cache already holds costs no
    model call.
    """
    plan = [dict(operation) for operation in operations or ()]
    receipt: dict[str, Any] = {}
    for turn in range(max_attempts):
        evidence = list(gather(plan) or ())
        receipt = replan(plan, evidence, planner=planner, cache=cache,
                         max_attempts=max_attempts)
        receipt["turn"] = turn + 1
        if receipt["status"] != STATUS_REVISED:
            break
        revised = [dict(operation) for operation in receipt["operations"]]
        if revised == plan:
            break
        plan = revised
    receipt.setdefault("turn", 1)
    return receipt


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.plan_revision",
        description=("Revise a plan narrowly, from the evidence about it."))
    parser.add_argument("--plan", required=True,
                        help="a JSON file holding the plan's operations")
    parser.add_argument("--evidence", default="",
                        help="a JSON file holding the evidence")
    parser.add_argument("--report", default="",
                        help="where the JSON receipt is written")
    args = parser.parse_args(list(argv) if argv is not None else None)

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    raw = (json.loads(Path(args.evidence).read_text(encoding="utf-8"))
           if args.evidence else [])
    evidence = [Evidence(kind=str(row.get("kind") or ""),
                         reason=str(row.get("reason") or ""),
                         op=str(row.get("op") or ""),
                         role=str(row.get("role") or ""),
                         source=str(row.get("source") or ""),
                         detail=dict(row.get("detail") or {}))
                for row in raw]
    receipt = replan(plan, evidence)
    text = json.dumps(receipt, indent=2, sort_keys=True, default=str)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI entry
    raise SystemExit(main())
