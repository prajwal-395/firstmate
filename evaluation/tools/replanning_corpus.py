"""PLAN-007 measured on the representative corpus (BASE-003).

This tool lives OUTSIDE the engine on purpose: the corpus's answer key
is sealed, and the engine must not be able to read it.  It measures
the replanning loop over the thirteen briefs of the representative
corpus:

- the plan each brief implies, and what the loop does with the evidence
  about it: a NARROW revision, a QUESTION for the user, or a named
  capability gap;
- how many plans carry no refused operation after the revision, and how
  many still carry an operation that can be staged;
- the model calls the loop makes against the calls a loop that re-plans
  per piece of evidence would make, and what a repeat of the same run
  costs.

The evidence is the corpus's own: which premises the substrate carries
(the premise audit), and which target roles ground to a single measured
item (the grounding measurement, :mod:`grounding_corpus`).

A brief whose premise the substrate does NOT carry is the case the loop
exists for: the request names something the footage does not have, and
the right outcome is a QUESTION, not a plan.

    python3 evaluation/tools/replanning_corpus.py [--report <path>]

The measurement reads the corpus and the machine's recorded timelines.
It contacts no Resolve, calls no model and changes no project.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus import (
    load_sealed_briefs,
    load_sealed_substrates,
    premise_audit,
)
from grounding_corpus import ground_brief

from library.tools import plan_revision
from library.tools.editorial_acceptance import briefs as brief_oracle

SCHEMA = "ren-editorial-corpus-replanning/1"


def plan_of(key: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The plan a corpus brief implies, with the role each target plays.

    The corpus's brief carries the operations the request needs and the
    roles their targets play, and the two are paired in the oracle.
    """
    operations = [str(op) for op in (key.get("expected_operations") or ())]
    roles = [str(role) for role in (key.get("expected_target_roles") or ())]
    plan: list[dict[str, Any]] = []
    for index, op in enumerate(operations):
        role = roles[index] if index < len(roles) else (
            roles[0] if roles else "")
        plan.append({"op": op, "target_role": role})
    return plan


def evidence_for(brief: Mapping[str, Any], key: Mapping[str, Any],
                 audit: Mapping[str, Any], grounding: Mapping[str, Any],
                 ) -> list[plan_revision.Evidence]:
    """The evidence the corpus holds about one brief's plan.

    A premise the substrate does not carry is a premise the footage
    lacks, and the request's own referent is not in it: that is the
    question.  A role the grounding stage verified names the identity
    the plan may act on.
    """
    rows: list[plan_revision.Evidence] = []
    requirements = audit.get("requirements")
    requirements = requirements if isinstance(requirements, list) else []
    carried = all(str(row.get("state")) == "satisfied" for row in requirements)
    roles = {str(row.get("role")): row
             for row in (grounding.get("targets") or ())
             if isinstance(row, Mapping)}
    for role in (key.get("expected_target_roles") or ()):
        row = roles.get(str(role)) or {}
        state = str(row.get("state") or plan_revision.KIND_NOT_GROUNDED)
        if state == "verified":
            rows.append(plan_revision.Evidence(
                kind=plan_revision.KIND_RESOLVED,
                role=str(role),
                source=plan_revision.SOURCE_GROUNDING,
                detail={"identity": dict(row.get("identity") or {}),
                        "state": state}))
            continue
        rows.append(plan_revision.Evidence(
            kind=plan_revision.KIND_NOT_GROUNDED,
            reason=str(row.get("reason") or ""),
            role=str(role),
            source=plan_revision.SOURCE_GROUNDING,
            detail={"state": state,
                    "surfaces_read": not carried,
                    "premise_absent": not carried,
                    "missing_evidence": (
                        "" if carried else
                        "the substrate does not carry what the request "
                        "presupposes: there is nothing to point at")}))
    return rows


def planner_for(grounding: Mapping[str, Any]) -> Any:
    """The planner the corpus measurement stands in for.

    The corpus says which roles ground to a single measured item, so
    the answer for a role is measured rather than invented: a grounded
    role survives, and a role that grounds to nothing does not.
    """
    roles = {str(row.get("role")): row
             for row in (grounding.get("targets") or ())
             if isinstance(row, Mapping)}

    def planner(request: Mapping[str, Any]) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for operation in (request.get("operations") or ()):
            role = str(operation.get("target_role") or "")
            row = roles.get(role) or {}
            if str(row.get("state") or "") == "verified":
                operations.append(dict(operation))
                continue
            unresolved.append({
                "op": str(operation.get("op") or ""),
                "role": role,
                "reason": str(row.get("reason")
                              or "no surface carries this target"),
            })
        return {"operations": operations, "unresolved": unresolved,
                "questions": [], "covers": []}

    return planner


def measure() -> dict[str, Any]:
    """The measurement over the corpus."""
    from corpus import SEALED_EVIDENCE

    brief_set = load_sealed_briefs()
    catalogue = load_sealed_substrates()
    evidence = json.loads(SEALED_EVIDENCE.read_text(encoding="utf-8"))
    audit = premise_audit(brief_set, catalogue)

    cache = plan_revision.RevisionCache()
    rows: list[dict[str, Any]] = []
    for brief, audit_row in zip(brief_set["briefs"], audit["rows"]):
        key = brief_oracle.oracle(brief)
        grounding = ground_brief(brief, catalogue, audit_row, evidence, {})
        plan = plan_of(key)
        collected = evidence_for(brief, key, audit_row, grounding)
        receipt = plan_revision.replan(plan, collected,
                                       planner=planner_for(grounding),
                                       cache=cache)
        roles = {str(row.get("role")): row
                 for row in (grounding.get("targets") or ())
                 if isinstance(row, Mapping)}
        grounded_roles = [str(role)
                          for role in (key.get("expected_target_roles") or ())
                          if str((roles.get(str(role)) or {}).get("state")
                                 or "") == "verified"]
        rows.append({
            "brief_id": str(brief.get("id") or ""),
            "outcome": str(brief.get("outcome") or ""),
            "premise_carried": all(
                str(row.get("state")) == "satisfied"
                for row in (audit_row.get("requirements") or [])),
            "planned_operations": [str(row.get("op")) for row in plan],
            "evidence_pieces": len([row for row in collected
                                    if row.kind in plan_revision.PROBLEM_KINDS]),
            "status": receipt["status"],
            "operations": [str(row.get("op")) for row in receipt["operations"]],
            "dropped": [str(row.get("op")) for row in receipt["dropped"]],
            "questions": len(receipt["questions"]),
            "capability_gaps": [str(row.get("op"))
                                for row in receipt["capability_gaps"]],
            "roles": [str(role)
                      for role in (key.get("expected_target_roles") or ())],
            "grounded_roles": grounded_roles,
            "model_calls": dict(receipt["model_calls"]),
        })

    planned = [row for row in rows if row["planned_operations"]]
    asked = [row for row in planned if row["questions"]]
    # A plan is feasible BEFORE the loop when every role it names
    # grounds to a single measured item; it is feasible AFTER when the
    # loop hands it back with no refused operation in it.
    feasible_before = [row for row in planned
                       if row["roles"]
                       and len(row["grounded_roles"]) == len(row["roles"])]
    feasible_after = [row for row in planned if not row["dropped"]]
    evidence_pieces = sum(row["evidence_pieces"] for row in planned)
    affected = sum(len(row["planned_operations"]) for row in planned)
    calls_made = sum(row["model_calls"]["made"] for row in planned)
    repeat_calls = 0
    for brief, audit_row in zip(brief_set["briefs"], audit["rows"]):
        key = brief_oracle.oracle(brief)
        grounding = ground_brief(brief, catalogue, audit_row, evidence, {})
        collected = evidence_for(brief, key, audit_row, grounding)
        repeated = plan_revision.replan(plan_of(key), collected,
                                        planner=planner_for(grounding),
                                        cache=cache)
        repeat_calls += repeated["model_calls"]["made"]
    return {
        "schema": SCHEMA,
        "brief_count": len(rows),
        "plans_assessed": len(planned),
        "plans_revised_into_feasible_ones": {
            "before": len(feasible_before),
            "after": len(feasible_after),
            "denominator": len(planned),
            "note": ("BEFORE is the plan whose every role grounds to a "
                     "single measured item; AFTER is the plan the loop "
                     "hands back with no refused operation in it.  The "
                     "loop does not make a plan that names a target the "
                     "footage lacks executable: it stops that plan with a "
                     "question, which the next number reports"),
        },
        "plans_refused_before_staging": {
            "before": 0,
            "after": len([row for row in planned if row["questions"]]),
            "denominator": len(planned),
            "note": ("a plan that names a target the substrate does not "
                     "carry is stopped before it is staged, and the user "
                     "is asked what to do instead"),
        },
        "plans_with_an_operation_to_stage": {
            "before": len(planned),
            "after": len([row for row in planned if row["operations"]]),
            "denominator": len(planned),
        },
        "revision_outcomes": {
            "by_status": {
                status: sum(1 for row in planned if row["status"] == status)
                for status in plan_revision.STATUSES
            },
            "asked_the_user": len(asked),
            "denominator": len(planned),
        },
        "model_calls": {
            "baseline_one_call_per_piece_of_evidence": {
                "calls": evidence_pieces, "denominator": evidence_pieces},
            "baseline_one_call_per_planned_operation": {
                "calls": affected, "denominator": affected},
            "the_loop": {"calls": calls_made, "denominator": evidence_pieces},
            "saved_against_the_evidence_baseline": evidence_pieces - calls_made,
            "saved_against_the_operation_baseline": affected - calls_made,
            "a_repeat_of_the_same_run_costs": repeat_calls,
            "a_repeat_saves": calls_made - repeat_calls,
        },
        "briefs": rows,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="replanning_corpus.py",
        description=("Measure PLAN-007 on the representative corpus: the "
                     "plans revised into feasible ones, the questions "
                     "asked, and the model calls saved."))
    parser.add_argument("--report", default=None,
                        help="where to write the report")
    args = parser.parse_args(list(argv) if argv is not None else None)

    payload = measure()
    text = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if args.report:
        Path(args.report).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
