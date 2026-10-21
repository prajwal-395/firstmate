"""PLAN-011: the instruction loop, and the account of every stated instruction.

WHAT WAS WRONG
--------------
A captain's instruction reaches Ren as a sentence.  The engine then keeps
FIVE records of what happened: the canonical intent of what was asked
(`editorial_intent`, PLAN-001), the plan that will serve it
(`assignment_plan`), the operations that ran and their receipts, the
acceptance contract and the evidence against it (`assignment_contract`),
and the acceptance state the gate reached (`acceptance_state`, QA-008).

Every one of those records was sound on its own, and NONE of them read
the others.  So a task could:

* serve ONE of the three requests its intent carried, and finish with
  the other two never planned, never executed and never mentioned;
* declare an acceptance contract of its own, and finish with the
  instruction the person actually stated carrying no criterion at all;
* and report itself DONE, because "done" was read from the contract and
  the delivery receipt, and neither of them knows what the person asked.

The punch list asks (PLAN-011) for the opposite: "from a natural-language
request, prove that the agent inspects work state, gathers evidence,
compiles intent, validates a plan, executes, verifies, and reports", and
"ensure the final reply states the actual result rather than repeating
the original intention".

WHAT THIS IS
------------
One account, over the records the engine ALREADY keeps, of every stated
instruction the task carried and where each one ended up.  It is a
READER, in the shape of `assignment_view`: it decides nothing, invents
nothing, and a record that lacks a section yields "not reached" rather
than a value this module made up.

    trace(project, assignment_id)   the whole loop, and its verdict
    account_assignment(...)         the account for the PRODUCT's task
    account_record(record, brief)   the account for a CORPUS task record
    render(trace)                   the final report a host may relay
    states_actual_result(...)       does a reply state the RESULT

THE STATED INSTRUCTIONS
-----------------------
Three sources, and each is a record the engine already keeps:

    requirement   an explicit requirement of the canonical intent - the
                  commission's own words, mandatory and objective.
    request       an explicit request of the canonical intent - the
                  editorial work the person asked for.
    criterion     an acceptance criterion of the task's contract - the
                  promise the task makes.

An instruction is ACCOUNTED when it reached a VERDICT: it was met, or it
was refused with a reason, or it failed a check.  An instruction with no
verdict is UNACCOUNTED, and the task is not finished while one is.  A
refusal IS a verdict: "this could not be done, and here is why" is an
answer, and an answer that says nothing is not one.

THE SEVEN STAGES
----------------
    inspect_state     the task read the project it acts on
    gather_evidence   the task cited what it rests on
    compile_intent    the request became a canonical intent
    validate_plan     a plan was compiled against that intent
    execute           the plan's operations ran
    verify            the run was read back and checked
    report            the result was stated, in the result's words

A stage with no record behind it is `not_reached`, with the reason.  It
is never a pass, and never a zero: the same rule `editorial_acceptance`
follows for its five stages.

THE FINAL REPLY
---------------
`states_actual_result` asks one question of a reply: does it state what
HAPPENED, or does it restate what was ASKED?  A reply that repeats the
request is not a result, however well it is written - and a reply that
names no outcome is not a result either.  The check is deterministic:
it compares the reply with the request it answers and with the outcome
the record holds.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

STAGES: tuple[str, ...] = (
    "inspect_state",
    "gather_evidence",
    "compile_intent",
    "validate_plan",
    "execute",
    "verify",
    "report",
)
"""The loop, in the order a task passes through it."""

STAGE_DEFINITIONS: dict[str, str] = {
    "inspect_state": "the task read the project it acts on",
    "gather_evidence": "the task cited what it rests on",
    "compile_intent": "the request became a canonical intent",
    "validate_plan": "a plan was compiled against that intent",
    "execute": "the plan's operations ran",
    "verify": "the run was read back and checked",
    "report": "the result was stated, in the result's words",
}

#: The kinds of stated instruction, and where each one is read from.
REQUIREMENT = "requirement"
REQUEST = "request"
CRITERION = "criterion"

INSTRUCTION_KINDS: tuple[str, ...] = (REQUIREMENT, REQUEST, CRITERION)

#: The verdicts an instruction can reach.
MET = "met"
REFUSED = "refused"
FAILED = "failed"
UNACCOUNTED = "unaccounted"

VERDICTS: tuple[str, ...] = (MET, REFUSED, FAILED, UNACCOUNTED)

#: The kind of a STATED instruction, when a reader has to tell a
#: preservation instruction from a change one.  The engine already
#: classifies a request's KIND by its words (`editorial_intent`), and this
#: is the same shape of rule: a statement that says something must
#: SURVIVE is a preservation, a statement that says the run must SAY
#: something is a report, and anything else is a change.
PRESERVATION = "preservation"
REPORT = "report"
CHANGE = "change"

CRITERION_KEYWORDS: dict[str, tuple[str, ...]] = {
    PRESERVATION: (
        "survive", "survives", "untouched", "unchanged", "intact",
        "not moved", "did not move", "nothing else", "no other", "every other",
        "still", "kept", "keep", "no clip", "nothing on the timeline",
        "nothing changed", "no caption card", "continuous",
        "where it was", "where they were", "where the hand put it",
    ),
    REPORT: (
        "the run says", "the run reports", "the run asks",
        "reports that", "asks what", "instead of choosing",
    ),
}
"""The words that carry each kind.  A statement that matches none of them
is a CHANGE, which is the residue - and the residue is the majority.

The rule is the engine's own, in the shape `editorial_intent` uses for a
request's kind: a statement that says something must SURVIVE is a
preservation, a statement that says the run must SAY something is a
report, and everything else asks for a change.  It is a reading of the
words, so it is stated plainly here rather than hidden: a statement the
engine cannot place is a CHANGE, and the account says so.
"""

#: The words that carry each OUTCOME, for the reply check.  The caller
#: may pass its own; these are the loop's own.
OUTCOME_WORDS: dict[str, tuple[str, ...]] = {
    "accepted": ("accepted", "complete", "delivered", "done"),
    "editorially_rejected": ("rejected", "refused"),
    "failed": ("failed", "failure"),
    "refused": ("refused", "not done", "nothing changed"),
    "no_change": ("nothing", "no change", "left it alone"),
    "incomplete": ("incomplete", "unverified", "not verified"),
    "in_flight": ("planned", "running", "in flight"),
    # The revision front door's own outcomes, so a reply that says what
    # happened passes the check without the caller naming a word.
    "awaiting_translation": ("translating",),
    "question": ("question", "ask"),
    "proposed": ("would change", "nothing is changed"),
    "patched": ("changed", "set", "moved", "switched", "applied"),
    "touched": ("changed", "set", "moved", "switched", "applied"),
    "rebuilt": ("rebuilt", "rebuild"),
    "unverified": ("no request-specific verification", "unverified"),
}


def _norm(text: str) -> str:
    """A comparable form of a sentence: case, punctuation and space out."""
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def criterion_kind(statement: str) -> str:
    """What KIND of stated instruction a statement is.

    The engine's own rule, in the shape `editorial_intent` uses for a
    request's kind: a statement that says something must SURVIVE is a
    preservation, a statement that says the run must SAY something is a
    report, and everything else is a change.
    """
    text = f" {_norm(statement)} "
    for kind in (PRESERVATION, REPORT):
        if any(f" {word} " in text for word in CRITERION_KEYWORDS[kind]):
            return kind
    return CHANGE


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


# ── the account ──────────────────────────────────────────────────────


def _instruction(instruction_id: str, kind: str, statement: str, *,
                 verdict: str, where: str, reason: str = "") -> dict:
    return {
        "id": instruction_id,
        "kind": kind,
        "statement": statement,
        "verdict": verdict,
        "accounted": verdict != UNACCOUNTED,
        "where": where,
        "reason": reason,
    }


def _counts(instructions: Sequence[Mapping]) -> dict:
    verdicts = {verdict: 0 for verdict in VERDICTS}
    for row in instructions:
        verdicts[str(row.get("verdict") or UNACCOUNTED)] += 1
    return {
        "total": len(instructions),
        "accounted": verdicts[MET] + verdicts[REFUSED] + verdicts[FAILED],
        "met": verdicts[MET],
        "refused": verdicts[REFUSED],
        "failed": verdicts[FAILED],
        "unaccounted": verdicts[UNACCOUNTED],
    }


def _account(instructions: Sequence[Mapping]) -> dict:
    return {
        "instructions": [dict(row) for row in instructions],
        "unaccounted": [str(row.get("id")) for row in instructions
                        if not row.get("accounted")],
        "counts": _counts(instructions),
    }


# ── the product's task ───────────────────────────────────────────────


def _intent_on_record(project: str, record: Mapping) -> dict | None:
    """The canonical intent this task serves, or None.

    Read the way `assignment_view` reads it: the assignment names the
    intent it serves, and a project with exactly one intent on record is
    unambiguous.  A project with several and no name is NOT guessed.
    """
    from library.tools import editorial_intent, project_model_store

    store = Path(project) / project_model_store.STORE_DIR
    if not store.is_dir():
        return None
    try:
        _revision, model = project_model_store.load_project_model(Path(project))
    except (OSError, ValueError):
        return None
    rows = [row for row in (model.get("intents") or [])
            if isinstance(row, dict) and row.get("id")]
    named = str(record.get("intent_id") or "")
    if named:
        chosen = next((row for row in rows if row["id"] == named), None)
    elif len(rows) == 1:
        chosen = rows[0]
    else:
        return None
    if chosen is None:
        return None
    try:
        intent = editorial_intent.from_project_model_row(chosen)
    except (editorial_intent.IntentError, KeyError, TypeError, ValueError):
        return None
    return intent.to_dict()


def _workflow_verdict(workflows: Sequence[Mapping], request_id: str
                       ) -> tuple[str, str, str]:
    """The verdict a plan's workflow reached for one intent request.

    `verified` is the plan's own word for "the edit landed and was read
    back"; a refused or failed workflow carries its reason.  A workflow
    that is only planned has not reached a verdict yet, and a request no
    workflow serves has not either.
    """
    served = [row for row in workflows
              if str(row.get("intent_request_id") or "") == request_id]
    if not served:
        return UNACCOUNTED, "no plan", "no planned work serves this request"
    row = served[-1]
    status = str(row.get("status") or "")
    if status == "verified":
        return MET, f"the plan's workflow {row.get('request_id')}", ""
    if status in ("refused", "failed"):
        receipt = row.get("receipt") if isinstance(row.get("receipt"), Mapping) else {}
        reason = str(receipt.get("reason") or receipt.get("refusal") or "")
        return REFUSED, f"the plan's workflow {row.get('request_id')}", reason
    return UNACCOUNTED, f"the plan's workflow {row.get('request_id')}", (
        f"the workflow is {status or 'planned'}: it has reached no verdict")


def account_assignment(project: str, assignment_id: str) -> dict:
    """The account for the task the engine keeps on a project.

    Every stated instruction the task carried, and where it ended up.
    A task with no intent on record carries no stated requirement and no
    stated request, and its account is its acceptance criteria alone.
    """
    from library.tools import assignment_lifecycle as lifecycle

    root = str(Path(project).expanduser().resolve())
    record = lifecycle.load(root, assignment_id)
    if record is None:
        raise FileNotFoundError(
            f"assignment {assignment_id!r} has no checkpoint")
    intent = _intent_on_record(root, record)
    acceptance = record.get("acceptance") if isinstance(
        record.get("acceptance"), Mapping) else {}
    criteria = [row for row in (acceptance.get("criteria") or ())
                if isinstance(row, Mapping)]
    evidence = acceptance.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    workflows = [row for row in (record.get("edit_workflows") or {}).values()
                 if isinstance(row, Mapping)]
    declined: dict[str, str] = {}
    for row in workflows:
        for name, reason in (row.get("declined_instructions") or {}).items():
            declined[str(name)] = str(reason)

    instructions: list[dict] = []
    for criterion in criteria:
        criterion_id = str(criterion.get("id") or "")
        entry = evidence.get(criterion_id)
        if isinstance(entry, Mapping):
            status = str(entry.get("status") or "")
            verdict = FAILED if status == "failed" else MET
            instructions.append(_instruction(
                criterion_id, CRITERION, str(criterion.get("statement") or ""),
                verdict=verdict, where="the acceptance contract",
                reason=str(entry.get("observation") or "")))
        else:
            instructions.append(_instruction(
                criterion_id, CRITERION, str(criterion.get("statement") or ""),
                verdict=UNACCOUNTED, where="the acceptance contract",
                reason="no evidence has been recorded for this criterion"))
    requests = [row for row in ((intent or {}).get("requests") or ())
                if isinstance(row, Mapping) and row.get("explicit", True)]
    for request in requests:
        request_id = str(request.get("request_id") or "")
        verdict, where, reason = _workflow_verdict(workflows, request_id)
        instructions.append(_instruction(
            request_id, REQUEST, str(request.get("statement") or ""),
            verdict=verdict, where=where, reason=reason))
    requirements = [row for row in ((intent or {}).get("requirements") or ())
                    if isinstance(row, Mapping) and row.get("explicit", True)]
    carried = {str(row.get("instruction_id") or "")
               for row in criteria if row.get("instruction_id")}
    for requirement in requirements:
        requirement_id = str(requirement.get("declaration_id") or "")
        if requirement_id in carried:
            verdict, where, reason = MET, "the acceptance contract", ""
            for criterion in criteria:
                if str(criterion.get("instruction_id") or "") == requirement_id:
                    entry = evidence.get(str(criterion.get("id") or ""))
                    if isinstance(entry, Mapping):
                        status = str(entry.get("status") or "")
                        verdict = FAILED if status == "failed" else MET
                        reason = str(entry.get("observation") or "")
                    else:
                        verdict = UNACCOUNTED
                        reason = ("the criterion that carries it has no "
                                  "evidence yet")
                    break
        elif requirement_id in declined:
            verdict, where = REFUSED, "the plan"
            reason = declined[requirement_id]
        else:
            verdict, where = UNACCOUNTED, "nothing"
            reason = ("no plan carries this requirement and no plan "
                      "declined it with a reason")
        instructions.append(_instruction(
            requirement_id, REQUIREMENT, str(requirement.get("statement") or ""),
            verdict=verdict, where=where, reason=reason))
    return _account(instructions)


# ── a corpus task record ─────────────────────────────────────────────


def account_record(record: Mapping, brief: Mapping) -> dict:
    """The account for a task record in the acceptance harness's shape.

    The harness's records are the other place the engine keeps what a
    run did with a brief.  The stated instructions are the BRIEF's own
    acceptance criteria, and the verdicts are read from the record: a
    criterion the plan declared, a criterion the execution proved, and a
    criterion a refusal settled by saying what could not be done.
    """
    criteria = [row for row in (brief.get("acceptance_criteria") or ())
                if isinstance(row, Mapping)]
    planning = record.get("planning") if isinstance(
        record.get("planning"), Mapping) else {}
    declared = {str(name) for name in (planning.get("requirements_covered") or ())}
    execution = record.get("execution") if isinstance(
        record.get("execution"), Mapping) else {}
    receipt = execution.get("receipt") if isinstance(
        execution.get("receipt"), Mapping) else {}
    status = str(receipt.get("status") or "")
    checks = execution.get("checks") if isinstance(
        execution.get("checks"), Mapping) else {}
    collateral = list(execution.get("collateral_changes") or ())
    refusal = execution.get("refusal") if isinstance(
        execution.get("refusal"), Mapping) else {}
    reason = str(refusal.get("reason") or "")
    ran = status in ("verified", "committed", "delivered")
    refused = status in ("refused", "rejected", "needs_input", "blocked")
    instructions: list[dict] = []
    for criterion in criteria:
        criterion_id = str(criterion.get("id") or "")
        statement = str(criterion.get("statement") or "")
        kind = criterion_kind(statement)
        if criterion_id in declared:
            if ran and all(value is True for value in checks.values()) \
                    and not collateral:
                verdict, where = MET, "the plan and the execution"
            elif refused and reason:
                verdict, where = REFUSED, "the run's refusal"
            elif ran:
                verdict, where = FAILED, "the execution"
            else:
                verdict, where = UNACCOUNTED, "the plan"
            instructions.append(_instruction(
                criterion_id, CRITERION, statement, verdict=verdict,
                where=where, reason=reason if refused else ""))
            continue
        # The plan said nothing about this criterion.  It is still
        # accounted when the run's own evidence settles it: a
        # preservation that the execution shows nothing else moved, or a
        # report the refusal made.
        if refused and reason:
            verdict = REFUSED
            where = "the run's refusal"
        elif kind == PRESERVATION and ran and not collateral \
                and all(value is True for value in checks.values()):
            verdict, where = MET, "the execution's collateral check"
        else:
            verdict, where = UNACCOUNTED, "nothing"
        instructions.append(_instruction(
            criterion_id, CRITERION, statement, verdict=verdict,
            where=where, reason=reason if refused else (
                "" if verdict == MET else
                "the plan declared no coverage for it and the run recorded "
                "no evidence that settles it")))
    return _account(instructions)


# ── the trace ────────────────────────────────────────────────────────


def _stage(name: str, reached: bool, evidence: str = "",
           reason: str = "") -> dict:
    return {"stage": name, "reached": bool(reached), "evidence": evidence,
            "reason": reason, "definition": STAGE_DEFINITIONS[name]}


def _stages_assignment(project: str, record: Mapping,
                        intent: Mapping | None) -> list[dict]:
    acceptance = record.get("acceptance") if isinstance(
        record.get("acceptance"), Mapping) else {}
    evidence = acceptance.get("evidence")
    evidence = evidence if isinstance(evidence, Mapping) else {}
    plan = record.get("plan") if isinstance(record.get("plan"), Mapping) else {}
    operations = plan.get("operations") if isinstance(
        plan.get("operations"), Mapping) else {}
    workflows = [row for row in (record.get("edit_workflows") or {}).values()
                 if isinstance(row, Mapping)]
    delivered = record.get("delivery")
    return [
        _stage("inspect_state", bool(record.get("project_state")),
               "the task captured the project it acts on",
               "the task captured no project state"),
        _stage("gather_evidence", bool(evidence) or bool(
            record.get("evidence")),
            "the task cited what it rests on",
            "the task cited nothing"),
        _stage("compile_intent", intent is not None,
               "a canonical intent is on record for this task",
               "no canonical intent is on record for this task"),
        _stage("validate_plan", bool(operations) or bool(workflows),
               "a plan was compiled against the intent",
               "no plan was compiled"),
        _stage("execute", any(str(row.get("status") or "") not in
                              ("", "planned") for row in workflows),
               "the plan's workflows ran", "nothing ran"),
        _stage("verify", bool(evidence),
               "the task recorded what it checked",
               "nothing was checked"),
        _stage("report", delivered is not None or bool(workflows),
               "the task's result is on record",
               "no result is on record"),
    ]


def _stages_record(record: Mapping) -> list[dict]:
    planning = record.get("planning") if isinstance(
        record.get("planning"), Mapping) else {}
    execution = record.get("execution") if isinstance(
        record.get("execution"), Mapping) else {}
    receipt = execution.get("receipt") if isinstance(
        execution.get("receipt"), Mapping) else {}
    status = str(receipt.get("status") or "")
    delivery = record.get("delivery") if isinstance(
        record.get("delivery"), Mapping) else {}
    interpretation = record.get("interpretation") if isinstance(
        record.get("interpretation"), Mapping) else {}
    retrieval = record.get("retrieval") if isinstance(
        record.get("retrieval"), Mapping) else {}
    return [
        _stage("inspect_state", bool(record.get("project")),
               "the run recorded the project it worked in",
               "the run recorded no project"),
        _stage("gather_evidence", bool(retrieval),
               "the run recorded what it found", "the run found nothing"),
        _stage("compile_intent", bool(interpretation.get("restated_outcome")),
               "the run stated what it read the request to mean",
               "the run recorded no reading"),
        _stage("validate_plan", bool(planning.get("operations")),
               "the run named the work it would do", "no plan was recorded"),
        _stage("execute", bool(status), "the plan ran",
               "nothing ran"),
        _stage("verify", status in ("verified", "committed", "delivered"),
               "the run was read back", "the run was not read back"),
        _stage("report", bool(delivery) or bool(status),
               "the run's outcome is on record", "no outcome is on record"),
    ]


def trace(project: str, assignment_id: str | None = None) -> dict:
    """The whole loop of one task, and its verdict.

    The trace is a READ: it changes nothing, and every answer is read
    from a record the task already owns.
    """
    from library.tools import assignment_contract
    from library.tools import assignment_lifecycle as lifecycle

    root = str(Path(project).expanduser().resolve())
    record = lifecycle.resolve(root, assignment_id)
    if record is None:
        return {
            "assignment_id": assignment_id or "",
            "request": "",
            "stages": [_stage(name, False, reason="no task is on record")
                       for name in STAGES],
            "instructions": [],
            "unaccounted": [],
            "counts": _counts([]),
            "outcome": {"state": "no_task", "complete": False,
                        "summary": "no task is on record for this project"},
            "verdict": "no_task",
            "report": "No task is on record for this project.",
        }
    intent = _intent_on_record(root, record)
    account = account_assignment(root, str(record["assignment_id"]))
    completion = assignment_contract.completion(
        root, str(record["assignment_id"]))
    stages = _stages_assignment(root, record, intent)
    outcome = {
        "state": str(record.get("state") or "brief"),
        "complete": bool(completion.get("complete")),
        "summary": str(completion.get("reason") or ""),
        "blockers": list(completion.get("blockers") or ()),
    }
    verdict = _verdict(stages, account, outcome)
    trace_payload = {
        "assignment_id": str(record.get("assignment_id") or ""),
        "request": str(record.get("request") or record.get("brief") or ""),
        "intent_id": str(record.get("intent_id") or ""),
        "stages": stages,
        "instructions": account["instructions"],
        "unaccounted": account["unaccounted"],
        "counts": account["counts"],
        "outcome": outcome,
        "verdict": verdict,
    }
    trace_payload["report"] = render(trace_payload)
    return trace_payload


def _verdict(stages: Sequence[Mapping], account: Mapping,
             outcome: Mapping) -> str:
    """The one word the trace ends with.

    `complete` is the only word that means the task is finished, and it
    is the engine's own completion verdict - the loop does not invent a
    second one.  `unaccounted` says a stated instruction reached no
    verdict, which is the defect this module exists to name.
    """
    if account["counts"]["unaccounted"]:
        return "unaccounted"
    if outcome.get("complete"):
        return "complete"
    if not any(row.get("reached") for row in stages):
        return "not_started"
    return "incomplete"


def render(trace_payload: Mapping) -> str:
    """The final report: what happened, in the result's words.

    It states the ACTUAL result - the outcome the record holds, and
    where every stated instruction ended up.  It never restates the
    request, and a task with an unaccounted instruction says so in the
    same breath as the outcome.
    """
    outcome = trace_payload.get("outcome") if isinstance(
        trace_payload.get("outcome"), Mapping) else {}
    counts = trace_payload.get("counts") if isinstance(
        trace_payload.get("counts"), Mapping) else {}
    lines = [(f"Task {trace_payload.get('assignment_id') or ''}: "
              f"{outcome.get('state') or 'unknown'}.")]
    if outcome.get("summary"):
        lines.append(str(outcome["summary"]))
    if counts.get("total"):
        lines.append(
            f"Stated instructions: {counts.get('accounted')} of "
            f"{counts.get('total')} reached a verdict "
            f"({counts.get('unaccounted')} unaccounted).")
        for row in trace_payload.get("instructions") or ():
            if row.get("accounted"):
                continue
            lines.append(
                f"  {row.get('id')} ({row.get('kind')}) is unaccounted: "
                f"{row.get('reason') or row.get('where')}")
    return "\n".join(lines)


# ── the reply check ──────────────────────────────────────────────────


def states_actual_result(reply: str, *, request: str = "",
                          outcome: Mapping | None = None,
                          words: Sequence[str] = ()) -> tuple[bool, str]:
    """Does this reply state what HAPPENED, or restate what was ASKED?

    Three questions, and the first failure is the answer:

    1. does the reply say anything at all;
    2. does it repeat the request instead of the result;
    3. does it name the outcome the record holds.
    """
    text = str(reply or "").strip()
    if not text:
        return False, "the reply says nothing"
    asked = _norm(request)
    said = _norm(text)
    if asked and (said == asked
                  or (len(asked) >= 20 and asked in said)
                  or (len(said) >= 20 and said in asked)):
        return False, ("the reply repeats the request instead of the "
                       "result it is answering")
    outcome = outcome or {}
    summary = _norm(str(outcome.get("summary") or ""))
    if summary and summary in said:
        return True, ""
    candidates = [str(word).lower() for word in words] + [
        str(word).lower()
        for word in OUTCOME_WORDS.get(str(outcome.get("state") or ""), ())]
    if any(word and word in said for word in candidates):
        return True, ""
    return False, "the reply does not name what happened"


# ── the command line ─────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    """`python3 -m library.tools.instruction_loop <project>`: the trace."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="library.tools.instruction_loop",
        description="Trace one task's instruction loop, and say whether "
                    "every stated instruction reached a verdict.")
    parser.add_argument("project", help="project path")
    parser.add_argument("--id", dest="assignment_id", default=None,
                        help="the assignment to trace (default: the open one)")
    parser.add_argument("--json", action="store_true",
                        help="print the whole trace")
    args = parser.parse_args(argv)
    payload = trace(args.project, args.assignment_id)
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(payload["report"])
        for stage in payload["stages"]:
            mark = "reached" if stage["reached"] else "NOT reached"
            print(f"  {stage['stage']}: {mark}"
                  + (f" - {stage['reason']}" if not stage["reached"] else ""))
    return 0 if payload["verdict"] in ("complete", "not_started") else 1


if __name__ == "__main__":
    raise SystemExit(main())
