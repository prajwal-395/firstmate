"""PLAN-005 measured on the eight-brief benchmark: plans refused before staging.

The punch list asks for pre-execution feasibility checks, and names the
measurement: on the eight-brief benchmark, plans rejected or revised
BEFORE staging, with no drop in the planning number.

So this module replays the recorded plans of the benchmark through the
engine's own feasibility check and reports:

- how many briefs planned an operation at all;
- how many of those plans the engine REFUSES before a route is chosen,
  and how many it would REVISE instead;
- how many plans reached the engine's route and execution path BEFORE
  the check existed - the cost the check removes;
- the planning number, before and after, because a feasibility refusal
  must not cost a brief its planning credit.

WHAT THE RECORDS CARRY, AND WHAT THEY DO NOT.

A record names the plan's operations and, for each, the ROLE its target
plays; the interpretation block says, per role, whether the run
RESOLVED that role in the footage.  The role is therefore the identity
the record carries: a role the run resolved is an item the timeline
has, and a role it did not is an item the timeline does not.  The
record carries no operation PARAMETERS, so this measurement does not
run the parameter check and says so in its report - a plan that names
an operation without spelling out its arguments is not a plan whose
arguments are missing.

The measurement is over the RECORDS, so it reads no Resolve and
changes no project.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import plan_feasibility

#: The checks this measurement runs.  The parameter check is NOT one of
#: them: a benchmark record names the operation, not its arguments, and
#: a check the record cannot answer would refuse every plan for a field
#: the record never had.
MEASURED_CHECKS: tuple[str, ...] = tuple(
    check for check in plan_feasibility.CHECKS
    if check != plan_feasibility.CHECK_REQUIRED_PARAMETERS)

SCHEMA = "ren-editorial-plan-feasibility/1"


def _role_identity(role: str) -> str:
    """The identity a role has in a record."""
    return f"role:{role}" if role else ""


def plan_of(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The plan a record carries, as the operations the check reads."""
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
        operation: dict[str, Any] = {"op": op}
        identity = _role_identity(role)
        if identity:
            operation["unique_id"] = identity
        operations.append(operation)
    return operations


def resolved_roles(record: Mapping[str, Any]) -> dict[str, bool]:
    """The roles the run resolved in the footage, by name."""
    interpretation = record.get("interpretation")
    interpretation = interpretation if isinstance(interpretation, Mapping) else {}
    roles: dict[str, bool] = {}
    for entry in (interpretation.get("resolved_targets") or ()):
        if not isinstance(entry, Mapping):
            continue
        role = str(entry.get("role") or "")
        if not role:
            continue
        roles[role] = bool(entry.get("resolved"))
    return roles


def state_for(record: Mapping[str, Any]) -> plan_feasibility.FeasibilityState:
    """The state a record's plan would run against, as far as it goes.

    The record carries no rows, no media kinds and no effect state, so
    the checks that need those report UNDETERMINED: the measurement
    never reads an absent field as an absent fact.
    """
    items: dict[str, dict[str, Any]] = {}
    for role, resolved in resolved_roles(record).items():
        if not resolved:
            continue
        items[_role_identity(role)] = {"track": "", "source_in": None,
                                       "source_out": None}
    return plan_feasibility.FeasibilityState(items=items)


def unresolved_roles(record: Mapping[str, Any]) -> list[str]:
    """The roles the run did NOT resolve in the footage.

    A plan that names a role the footage does not carry asks for
    something that is not there, and that is a refusal of the PLAN, not
    of the footage: the run's own resolution is the evidence.
    """
    return sorted(role for role, resolved in resolved_roles(record).items()
                  if not resolved)


def _execution_of(record: Mapping[str, Any]) -> dict[str, Any]:
    execution = record.get("execution")
    return dict(execution) if isinstance(execution, Mapping) else {}


def reached_the_execution_path(record: Mapping[str, Any]) -> bool:
    """Whether the plan reached the engine's route and execution path.

    The receipt names the backend that ran.  A receipt with no backend
    names a plan that was refused before a route was chosen - by the
    run itself, or (after this change) by the feasibility check.
    """
    receipt = _execution_of(record).get("receipt")
    receipt = receipt if isinstance(receipt, Mapping) else {}
    backend = str(receipt.get("backend") or "none")
    return backend not in {"", "none"}


def assess_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """One brief's plan, judged by the feasibility check.

    Two refusals are possible and both are named: the ENGINE's (the
    operation has no eligible route, or its target is not on the
    timeline) and the RECORD's (the plan names a role the run did not
    resolve in the footage).  A plan refused by either is refused
    before staging.
    """
    operations = plan_of(record)
    brief_id = str(record.get("brief_id") or record.get("task_id") or "")
    if not operations:
        return {
            "brief_id": brief_id,
            "planned_operations": [],
            "verdict": "nothing_planned",
            "outcome": "nothing_planned",
            "reasons": [],
            "revision": None,
            "reached_the_execution_path": reached_the_execution_path(record),
            "attempts": _execution_of(record).get("attempts"),
        }
    verdict = plan_feasibility.assess(
        operations, state=state_for(record), checks=MEASURED_CHECKS)
    missing_roles = unresolved_roles(record)
    reasons = list(verdict.reasons())
    for role in missing_roles:
        reasons.append(
            f"the plan names the target role {role!r}, which the run did "
            "not resolve in the footage")
    refused = verdict.infeasible or bool(missing_roles)
    # The revision a plan is left with is the plan that survives BOTH
    # refusals: an operation the engine refused, and an operation whose
    # target the footage does not carry, are both gone.
    roles_by_op = {}
    for entry in ((record.get("planning") or {}).get("operations") or ()):
        if isinstance(entry, Mapping):
            roles_by_op[str(entry.get("op") or "")] = str(
                entry.get("target_role") or "")
    dropped = set(verdict.infeasible_operations)
    dropped.update(op for op, role in roles_by_op.items()
                   if role in missing_roles)
    surviving = [str(op.get("op")) for op in operations
                 if str(op.get("op")) not in dropped]
    revision = {
        "status": ("proposed" if surviving else "rejected"),
        "operations": surviving,
        "dropped": sorted(dropped),
        "reason": ("the refused operations are dropped; what they covered "
                   "is no longer covered by the plan") if surviving else (
            "every operation in the plan was refused, so there is no plan "
            "left to revise"),
    }
    if refused and not surviving:
        outcome = "rejected_before_staging"
    elif refused:
        outcome = "revised_before_staging"
    else:
        outcome = "would_reach_staging"
    return {
        "brief_id": brief_id,
        "planned_operations": [str(op.get("op")) for op in operations],
        "verdict": verdict.status,
        "outcome": outcome,
        "reasons": reasons,
        "unresolved_roles": missing_roles,
        "undetermined_operations": list(verdict.undetermined_operations),
        "revision": (dict(revision) if revision else None),
        "findings": [row.as_dict() for row in verdict.findings],
        "reached_the_execution_path": reached_the_execution_path(record),
        "attempts": _execution_of(record).get("attempts"),
    }


def _planning_rate(report: Mapping[str, Any]) -> dict[str, Any]:
    """The planning stage's own number, with its denominator."""
    metrics = report.get("stage_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    stages = metrics.get("stages")
    stages = stages if isinstance(stages, Mapping) else {}
    planning = stages.get("planning")
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
    """The measurement: plans refused before staging, and the coverage gate.

    The planning number is read from the harness that measures the
    benchmark, before and after, and it is the SAME number twice: the
    feasibility check runs after the planning stage and rewrites nothing
    the planning stage reads, so a plan the engine refuses keeps the
    planning credit of the plan the run made.
    """
    from library.tools.editorial_acceptance import runner

    rows = [assess_record(record) for record in records]
    planned = [row for row in rows if row["planned_operations"]]
    rejected = [row for row in planned
                if row.get("outcome") == "rejected_before_staging"]
    revised = [row for row in planned
               if row.get("outcome") == "revised_before_staging"]
    would_stage = [row for row in planned
                   if row.get("outcome") == "would_reach_staging"]
    reached = [row for row in planned if row["reached_the_execution_path"]]
    attempts = sum(int(row.get("attempts") or 0) for row in planned)

    report = runner.run_acceptance(list(records), brief_set=brief_set)
    planning = _planning_rate(report)
    return {
        "schema": SCHEMA,
        "brief_count": len(rows),
        "plans_assessed": len(planned),
        "plans_rejected_before_staging": len(rejected),
        "plans_revised_before_staging": len(revised),
        "plans_that_would_reach_staging": len(would_stage),
        "before": {
            "plans_that_reached_the_execution_path": len(reached),
            "attempts_spent_on_those_plans": attempts,
            "plans_refused_by_the_engine_before_staging": 0,
        },
        "after": {
            "plans_refused_by_the_engine_before_staging": len(rejected),
            "plans_revised_by_the_engine_before_staging": len(revised),
            "plans_that_reach_the_execution_path": len(would_stage),
        },
        "coverage": {
            "planning": planning,
            "planning_before": planning,
            "planning_after": planning,
            "note": ("the feasibility check runs after the planning stage "
                     "and rewrites nothing the planning stage reads, so a "
                     "plan the engine refuses keeps its planning credit"),
        },
        "checks": list(MEASURED_CHECKS),
        "briefs": rows,
    }


def _read_records(source: str | Path) -> list[dict[str, Any]]:
    from library.tools.editorial_acceptance import runner

    return runner.read_records(source)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_acceptance.feasibility",
        description=("Measure PLAN-005 on the eight-brief benchmark: the "
                     "plans refused before staging, and the planning "
                     "coverage gate."))
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
