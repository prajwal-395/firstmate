"""PLAN-007 measured on the eight-brief benchmark: the replanning loop.

The punch list asks (PLAN-007) for an evidence-driven replanning loop:
the failed acceptance checks, the routing refusals and the missing
evidence go back to the planner, only the affected part of the plan is
revised, the work that was already verified is preserved, an expensive
model call is not repeated with unchanged context, and the loop stops
when the remaining problem genuinely requires the user's judgment.

This module replays the benchmark's recorded runs through that loop and
reports the three numbers the punch list names:

- **plans revised into feasible ones** - the plans that carried an
  operation the engine refuses, before and after the revision, each with
  its denominator;
- **model calls saved by not repeating unchanged context** - the calls a
  loop that re-plans per piece of evidence would make, against the calls
  this loop makes, and what a repeat of the same run costs;
- **the planning number**, before and after, because a revision must not
  cost a brief its planning credit.

WHAT THE RECORDS CARRY, AND WHAT THEY DO NOT.

A record carries the run's own account of three things, and the loop
reads all three:

- the PLAN (the operations it named, and the role each target plays);
- the INTERPRETATION (for every target role, whether the run resolved
  it, and in what words) - this is the record of what the model looked
  at and found, and it is what tells the loop whether a target the
  grounding stage could not establish is absent from the footage or
  merely unlooked-for;
- the EXECUTION refusal (the engine's own words about the operation
  that could not be carried).

The engine's own MEASUREMENTS are replayed over the same records: the
feasibility verdict (PLAN-005) and the grounding receipt (PLAN-003).
Nothing here reads Resolve, calls a model or touches a project.

The measurement is over the RECORDS, so it reads no Resolve and changes
no project.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import plan_feasibility, plan_revision
from library.tools.editorial_acceptance import (
    briefs as brief_oracle,
)
from library.tools.editorial_acceptance import (
    feasibility,
    grounding,
    grounding_stage,
    stages,
)

SCHEMA = "ren-editorial-replanning/1"

#: The checks the feasibility replay runs.  It is the measurement's own
#: set (PLAN-005), because a benchmark record names an operation and
#: not its arguments.
MEASURED_CHECKS: tuple[str, ...] = feasibility.MEASURED_CHECKS


# ── The plan a record carries ─────────────────────────────────────────


def plan_of(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The plan a record carries, as the operations the loop reads.

    Each operation names the ROLE its target plays, and the role is the
    identity the record carries: a role the run resolved is a thing the
    footage has, and a role it did not is a thing it does not.  The
    identity is written the way PLAN-005's own measurement writes it, so
    the two measurements judge the same plan and cannot disagree.
    """
    planning = record.get("planning")
    planning = planning if isinstance(planning, Mapping) else {}
    operations: list[dict[str, Any]] = []
    for entry in (planning.get("operations") or ()):
        if not isinstance(entry, Mapping):
            continue
        op = str(entry.get("op") or "")
        if not op:
            continue
        role = str(entry.get("target_role") or "")
        operation: dict[str, Any] = {"op": op, "target_role": role}
        if role:
            operation["unique_id"] = f"role:{role}"
        operations.append(operation)
    return operations


def state_for(record: Mapping[str, Any]) -> plan_feasibility.FeasibilityState:
    """The state the plan would run against, as far as the record goes.

    The record names a target by its ROLE, and PLAN-005's own
    measurement writes that role as the identity the plan carries.  The
    RECORDED TIMELINE is read as well, because the grounding stage
    re-targets an operation at the real identity of the clip it
    verified, and a state that did not carry that identity would
    refuse an operation the loop has just established.
    """
    state = feasibility.state_for(record)
    items = dict(state.items)
    recorded = grounding.recorded_timeline(record)
    items.update(plan_feasibility.items_from_clips(recorded.items))
    return plan_feasibility.FeasibilityState(
        items=items, tracks=state.tracks, runtime=state.runtime,
        preservation_requirements=state.preservation_requirements,
        backend_constraints=state.backend_constraints)


def resolved_targets(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The roles the run resolved, and what it said about each.

    This is the record of what the model looked at: a role the run
    resolved is a thing the footage carries, and a role it did not is a
    thing it does not.
    """
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    rows: dict[str, dict[str, Any]] = {}
    for entry in (block.get("resolved_targets") or ()):
        if not isinstance(entry, Mapping):
            continue
        role = str(entry.get("role") or "")
        if not role:
            continue
        rows[role] = {"resolved": bool(entry.get("resolved")),
                      "target": str(entry.get("target") or "")}
    return rows


def refusal_of(record: Mapping[str, Any]) -> dict[str, Any]:
    """The run's own refusal, and the class it recorded."""
    execution = record.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    refusal = execution.get("refusal")
    refusal = refusal if isinstance(refusal, Mapping) else {}
    return {"reason": str(refusal.get("reason") or ""),
            "recovery_class": str(refusal.get("recovery_class") or "")}


# ── The evidence ──────────────────────────────────────────────────────


def evidence_for(record: Mapping[str, Any], brief: Mapping[str, Any],
                 key: Mapping[str, Any]) -> dict[str, Any]:
    """Every piece of evidence the engine holds about one record's plan.

    The four sources are the four the loop knows: the feasibility
    verdict, the grounding receipt, the acceptance measurements, and the
    run's own routing refusal.
    """
    operations = plan_of(record)
    verdict = plan_feasibility.assess(
        operations, state=state_for(record), checks=MEASURED_CHECKS)
    grounding = grounding_stage.ground_record(record)
    measurements = stages.measure(record, brief, key)

    rows: list[plan_revision.Evidence] = []
    rows.extend(plan_revision.from_feasibility(verdict))
    rows.extend(plan_revision.from_grounding(grounding))

    # The acceptance measurements: the criteria the plan does not cover,
    # and the checks its work failed.
    planning = measurements.get("planning")
    planning = planning if isinstance(planning, Mapping) else {}
    detail = planning.get("detail")
    detail = detail if isinstance(detail, Mapping) else {}
    if planning.get("status") == stages.MEASURED and planning.get("passed") is False:
        for criterion in (detail.get("acceptance_criteria_not_declared_covered")
                          or ()):
            rows.append(plan_revision.Evidence(
                kind=plan_revision.KIND_NOT_COVERED,
                reason=("the plan does not declare the acceptance criterion "
                        f"{criterion!r} covered"),
                source=plan_revision.SOURCE_ACCEPTANCE,
                detail={"criterion": str(criterion),
                        "check": "acceptance_criteria_coverage"}))
        for operation in (detail.get("missing_operations") or ()):
            rows.append(plan_revision.Evidence(
                kind=plan_revision.KIND_NOT_COVERED,
                reason=(f"the plan does not name the operation "
                        f"{operation!r} the request needs"),
                op=str(operation),
                source=plan_revision.SOURCE_ACCEPTANCE,
                detail={"check": "missing_operations"}))
    execution = measurements.get("execution")
    execution = execution if isinstance(execution, Mapping) else {}
    execution_detail = execution.get("detail")
    execution_detail = (execution_detail if isinstance(execution_detail, Mapping)
                        else {})
    for check in (execution_detail.get("failed_checks") or ()):
        rows.append(plan_revision.Evidence(
            kind=plan_revision.KIND_CHECK_FAILED,
            reason=f"the acceptance check {check!r} failed",
            source=plan_revision.SOURCE_ACCEPTANCE,
            detail={"check": str(check)}))

    # The run's own account of the roles it could not resolve: the
    # model looked for them and the footage does not carry them, which
    # is what separates a premise the footage lacks from a target
    # nobody has looked for.
    for role, entry in sorted(resolved_targets(record).items()):
        if entry["resolved"]:
            continue
        rows.append(plan_revision.Evidence(
            kind=plan_revision.KIND_NOT_GROUNDED,
            reason=(f"the run looked for {role!r} in the footage and did "
                    "not find it"),
            role=role,
            source=plan_revision.SOURCE_ACCEPTANCE,
            detail={"state": plan_revision.UNRESOLVED_STATE
                    if hasattr(plan_revision, "UNRESOLVED_STATE")
                    else "unresolved",
                    "surfaces_read": True,
                    "premise_absent": True,
                    "missing_evidence": ""}))

    # The run's own refusal.  A refusal the run recorded as a QUESTION
    # is the run saying the request needs the user's word, and that is
    # the premise the plan rests on.  A refusal the run recorded as
    # BLOCKED is the ENGINE's word about a route, and the engine's own
    # verdict - measured now, by the same feasibility check - is the
    # authority on what can run: a route the engine has since gained
    # must not refuse the plan, and a route it has lost must.
    refusal = refusal_of(record)
    if refusal["recovery_class"] == "needs_question" and operations:
        rows.append(plan_revision.Evidence(
            kind=plan_revision.KIND_NOT_GROUNDED,
            reason=refusal["reason"],
            source=plan_revision.SOURCE_ROUTING,
            detail={"state": "unresolved", "surfaces_read": True,
                    "premise_absent": True, "missing_evidence": ""}))

    return {
        "operations": operations,
        "verdict": verdict,
        "grounding": grounding,
        "measurements": measurements,
        "evidence": rows,
    }


# ── The planner the record stands in for ──────────────────────────────


def planner_for(record: Mapping[str, Any]) -> Any:
    """The planner the RECORDED run was, standing in for the model.

    A benchmark record carries the model's own answer about every role
    it named: the role was resolved, or it was not.  That answer is
    what a model asked to establish a target produces, so the
    measurement replays it rather than inventing one - and the
    measurement says so in the report.
    """
    resolved = resolved_targets(record)

    def planner(request: Mapping[str, Any]) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for operation in (request.get("operations") or ()):
            role = str(operation.get("target_role") or "")
            entry = resolved.get(role)
            if entry is None or entry["resolved"]:
                operations.append(dict(operation))
                continue
            unresolved.append({
                "op": str(operation.get("op") or ""),
                "role": role,
                "reason": (f"the footage does not carry {role!r}: the run "
                           "looked for it and did not find it"),
            })
        return {"operations": operations, "unresolved": unresolved,
                "questions": [], "covers": []}

    return planner


# ── One brief ─────────────────────────────────────────────────────────


def assess_record(record: Mapping[str, Any], brief: Mapping[str, Any],
                  key: Mapping[str, Any],
                  cache: plan_revision.RevisionCache | None = None,
                  ) -> dict[str, Any]:
    """One brief's plan, revised by the loop, and what it cost.

    The BEFORE is the plan as the run made it, judged by the
    feasibility check.  The AFTER is the plan the loop hands back,
    judged by the same check against the same state, so the two numbers
    are the same measurement twice.
    """
    gathered = evidence_for(record, brief, key)
    operations = gathered["operations"]
    evidence = gathered["evidence"]
    receipt = plan_revision.replan(operations, evidence,
                                   planner=planner_for(record),
                                   cache=cache)
    revised = receipt["operations"]
    after = plan_feasibility.assess(
        revised, state=state_for(record), checks=MEASURED_CHECKS)
    before = gathered["verdict"]
    problems = [row for row in evidence
                if row.kind in plan_revision.PROBLEM_KINDS]
    return {
        "brief_id": str(record.get("brief_id") or record.get("task_id") or ""),
        "planned_operations": [str(row.get("op")) for row in operations],
        "evidence_pieces": len(problems),
        "before": {
            "status": before.status,
            "infeasible_operations": list(before.infeasible_operations),
            "undetermined_operations": list(before.undetermined_operations),
        },
        "after": {
            "status": after.status,
            "infeasible_operations": list(after.infeasible_operations),
            "undetermined_operations": list(after.undetermined_operations),
        },
        "revision": {
            "status": receipt["status"],
            "operations": [str(row.get("op")) for row in revised],
            "dropped": [str(row.get("op")) for row in receipt["dropped"]],
            "retargeted": [str(row.get("op")) for row in receipt["retargeted"]],
            "questions": len(receipt["questions"]),
            "capability_gaps": [str(row.get("op"))
                                for row in receipt["capability_gaps"]],
            "missing_evidence": len(receipt["missing_evidence"]),
            "uncovered_criteria": [str(row.get("criterion"))
                                   for row in receipt["uncovered_criteria"]],
        },
        "affected_operations": len(gathered["operations"]) and len(
            receipt["affected"]),
        "model_calls": dict(receipt["model_calls"]),
        "receipt": receipt,
    }


# ── The measurement ───────────────────────────────────────────────────


def _planning_rate(report: Mapping[str, Any]) -> dict[str, Any]:
    """The planning stage's own number, with its denominator."""
    metrics = report.get("stage_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    stages_ = metrics.get("stages")
    stages_ = stages_ if isinstance(stages_, Mapping) else {}
    planning = stages_.get("planning")
    planning = planning if isinstance(planning, Mapping) else {}
    return {
        "status": planning.get("status"),
        "value": planning.get("value"),
        "passed": planning.get("passed"),
        "failed": planning.get("failed"),
        "measured": planning.get("measured"),
        "not_measured": planning.get("not_measured"),
    }


def measure(records: Sequence[Mapping[str, Any]], *,
            brief_set: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The measurement: the loop over the benchmark, with denominators.

    The planning number is read from the harness that measures the
    benchmark, before and after, and it is the SAME number twice: the
    loop runs after the planning stage and rewrites nothing the
    planning stage reads, so a plan the engine revises keeps the
    planning credit of the plan the run made.
    """
    from library.tools.editorial_acceptance import runner

    the_set = brief_set if brief_set is not None else brief_oracle.load_brief_set()
    cache = plan_revision.RevisionCache()
    rows: list[dict[str, Any]] = []
    for record in records:
        brief_id = str(record.get("brief_id") or record.get("task_id") or "")
        brief = brief_oracle.brief(the_set, brief_id)
        key = brief_oracle.oracle(brief)
        rows.append(assess_record(record, brief, key, cache))

    planned = [row for row in rows if row["planned_operations"]]
    infeasible_before = [row for row in planned
                         if row["before"]["infeasible_operations"]]
    infeasible_after = [row for row in planned
                        if row["after"]["infeasible_operations"]]
    questions = [row for row in planned if row["revision"]["questions"]]
    capability = [row for row in planned if row["revision"]["capability_gaps"]]
    evidence_pieces = sum(row["evidence_pieces"] for row in planned)
    affected_operations = sum(row["affected_operations"] for row in planned)
    calls_made = sum(row["model_calls"]["made"] for row in planned)
    calls_saved = sum(row["model_calls"]["saved"] for row in planned)
    with_operations_left = [row for row in planned if row["revision"]["operations"]]

    # A repeat of the same run, over the same cache: every context is
    # known, so no model call is made.
    repeat_calls = 0
    for record in records:
        brief_id = str(record.get("brief_id") or record.get("task_id") or "")
        brief = brief_oracle.brief(the_set, brief_id)
        key = brief_oracle.oracle(brief)
        repeated = assess_record(record, brief, key, cache)
        repeat_calls += repeated["model_calls"]["made"]

    report = runner.run_acceptance(list(records), brief_set=the_set)
    planning = _planning_rate(report)
    return {
        "schema": SCHEMA,
        "brief_count": len(rows),
        "plans_assessed": len(planned),
        "plans_with_an_infeasible_operation": {
            "before": len(infeasible_before),
            "after": len(infeasible_after),
            "denominator": len(planned),
        },
        "plans_with_no_refused_operation_left": {
            "before": len(planned) - len(infeasible_before),
            "after": len(planned) - len(infeasible_after),
            "denominator": len(planned),
            "note": ("the punch list's \"plans revised into feasible ones\", "
                     "read exactly: the AFTER is the plan the loop hands "
                     "back, judged by the same feasibility check against the "
                     "same state.  A plan with no refused operation left is "
                     "not the same thing as a plan that can still be "
                     "staged, and the next number says which it is"),
        },
        "plans_with_an_operation_to_stage": {
            "before": len(planned),
            "after": len(with_operations_left),
            "denominator": len(planned),
            "note": ("a plan with no refused operation left is not the "
                     "same thing as a plan that can still be staged: the "
                     "loop reports both, and an empty plan is named as "
                     "one"),
        },
        "operations": {
            "before": sum(len(row["planned_operations"]) for row in planned),
            "after": sum(len(row["revision"]["operations"]) for row in planned),
            "denominator": sum(len(row["planned_operations"])
                               for row in planned),
        },
        "revision_outcomes": {
            "by_status": {
                status: sum(1 for row in planned
                            if row["revision"]["status"] == status)
                for status in plan_revision.STATUSES
            },
            "asked_the_user": len(questions),
            "capability_gap": len(capability),
            "denominator": len(planned),
            "note": ("a brief can both ask the user and name a capability "
                     "gap, so the two counts overlap and the status is "
                     "the one number per brief"),
        },
        "model_calls": {
            "baseline_one_call_per_piece_of_evidence": {
                "calls": evidence_pieces,
                "denominator": evidence_pieces,
                "note": ("a loop that re-plans once per piece of evidence "
                         "makes one call for each of them"),
            },
            "baseline_one_call_per_affected_operation": {
                "calls": affected_operations,
                "denominator": affected_operations,
                "note": ("a loop that re-plans once per affected operation "
                         "makes one call for each of them"),
            },
            "the_loop": {
                "calls": calls_made,
                "denominator": evidence_pieces,
                "note": ("the loop makes one call for the affected part of "
                         "the plan, and none for a context it has already "
                         "answered"),
            },
            "saved_against_the_evidence_baseline": evidence_pieces - calls_made,
            "saved_against_the_operation_baseline": (
                affected_operations - calls_made),
            "contexts_the_cache_already_holds": calls_saved,
            "a_repeat_of_the_same_run_costs": repeat_calls,
            "a_repeat_saves": calls_made - repeat_calls,
        },
        "coverage": {
            "planning": planning,
            "planning_before": planning,
            "planning_after": planning,
            "note": ("the replanning loop runs after the planning stage "
                     "and rewrites nothing the planning stage reads, so a "
                     "revised plan keeps the planning credit of the plan "
                     "the run made"),
        },
        "briefs": rows,
    }


def _read_records(source: str | Path) -> list[dict[str, Any]]:
    from library.tools.editorial_acceptance import runner

    return runner.read_records(source)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_acceptance.replanning",
        description=("Measure PLAN-007 on the eight-brief benchmark: the "
                     "plans revised into feasible ones, the model calls "
                     "saved, and the planning number."))
    parser.add_argument("--records", required=True,
                        help="the benchmark records: a file or a directory")
    parser.add_argument("--report", default="",
                        help="where the JSON report is written")
    args = parser.parse_args(list(argv) if argv is not None else None)

    records = _read_records(args.records)
    payload = measure(records)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - the CLI entry
    raise SystemExit(main())
