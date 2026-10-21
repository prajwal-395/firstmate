"""PLAN-003 measured on the eight-brief benchmark: grounding as a required stage.

The punch list asks (PLAN-003) that grounding become a required planning
stage: every named target role is resolved to an actual source media item
or a timeline item, the resolution stores the verified identity and the
source interval, the four answers - claimed, observed, verified and
unresolved - are kept apart, an ambiguous target is RE-QUERIED rather
than guessed, and a destructive change is refused when a unique target
cannot be established.

This module replays the benchmark's recorded plans through that stage
and reports:

- the target roles each brief names, and how many of them are GROUNDED
  (verified) against the recorded timeline, how many are OBSERVED
  (the surfaces saw several candidates), how many are CLAIMED (the run
  asserted one and no surface carries it) and how many are UNRESOLVED;
- the destructive operations the stage REFUSES, because the role they
  name is not verified;
- the planning number, before and after, because a grounding refusal
  must not cost a brief its planning credit.

WHAT THE RECORDS CARRY, AND WHAT THEY DO NOT.

A record names the plan's operations and, for each, the ROLE its target
plays; the interpretation block says, per role, whether the run RESOLVED
that role, and in what words.  The stage grounds those words against the
RECORDED timeline - the same store the verification path reads - so a
claim is checked against the timeline itself and not against the run's
own account of it.  The records carry no Project Model, so the EVIDENCE
surface is not read here: the stage asks it, the answer is "the model was
not read", and the receipt says so.

The measurement is over the RECORDS, so it reads no Resolve and changes
no project.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from library.tools import target_grounding
from library.tools.editorial_acceptance import grounding

SCHEMA = "ren-editorial-grounding-stage/1"

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_QUOTED = re.compile(r"'([^']+)'|\"([^\"]+)\"")
_SPAN = re.compile(r"frames?\s+(\d+)\s*-\s*(\d+)")
_ROW = re.compile(r"\b([VA]\d+)\b")

#: The media extensions a file name in prose may carry.  A token with
#: one of these is a clip's name; anything else is ordinary English.
_MEDIA_NAME = re.compile(
    r"\b[\w][\w\-.]*\.(?:mov|mxf|mp4|avi|mkv|wav|mp3|m4a|aif|aiff|png"
    r"|jpg|jpeg|webp|gif|tif|tiff|exr|braw|r3d|drp)\b", re.IGNORECASE)


def selectors_of(text: str) -> dict[str, Any]:
    """What a run's own words about a target actually name.

    A record's target is prose, and the prose is read for the four
    things it can name: an item's own id, the clip's file name, the
    record span it occupies, and the row it lives on.  A name is only a
    name when it LOOKS like one - a media file name, with no spaces -
    because an apostrophe in ordinary English is not a quote.
    """
    claim: dict[str, Any] = {}
    text = str(text or "")
    found = _UUID.search(text)
    if found:
        claim["unique_id"] = found.group(0)
    names: list[str] = []
    for match in _QUOTED.finditer(text):
        value = match.group(1) or match.group(2) or ""
        if "." in value and not any(character.isspace()
                                    for character in value):
            names.append(value)
    for match in _MEDIA_NAME.finditer(text):
        value = match.group(0)
        if value not in names:
            names.append(value)
    if names:
        claim["names"] = names
        claim["name"] = names[0]
    spans = [(int(low), int(high))
             for low, high in _SPAN.findall(text)]
    if spans:
        claim["record_spans"] = spans
    row = _ROW.search(text)
    if row:
        claim["row"] = row.group(1)
    return claim


def claims_of(record: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """What the record asserts about each target role.

    A role the run did NOT resolve is not a claim at all: the run said
    the thing was not there, and a role nobody asserted is UNRESOLVED
    rather than CLAIMED.
    """
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    claims: dict[str, dict[str, Any]] = {}
    for entry in (block.get("resolved_targets") or ()):
        if not isinstance(entry, Mapping):
            continue
        role = str(entry.get("role") or "")
        if not role or not entry.get("resolved"):
            continue
        claims[role] = selectors_of(str(entry.get("target") or ""))
    return claims


def operations_of(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The plan's operations, each with the role its target plays."""
    block = record.get("planning")
    block = block if isinstance(block, Mapping) else {}
    operations: list[dict[str, Any]] = []
    for entry in (block.get("operations") or ()):
        if not isinstance(entry, Mapping):
            continue
        op = str(entry.get("op") or "")
        if not op:
            continue
        operations.append({"op": op,
                           "target_role": str(entry.get("target_role") or "")})
    return operations


def roles_of(record: Mapping[str, Any]) -> list[str]:
    """Every target role the record names, in the order it names them.

    The interpretation's roles come first, because that is where the
    run says what it did with them; a role the PLAN names and the
    interpretation does not is still a role, and it is grounded too.
    """
    roles: list[str] = []
    block = record.get("interpretation")
    block = block if isinstance(block, Mapping) else {}
    for entry in (block.get("resolved_targets") or ()):
        if isinstance(entry, Mapping):
            role = str(entry.get("role") or "")
            if role and role not in roles:
                roles.append(role)
    for operation in operations_of(record):
        role = str(operation.get("target_role") or "")
        if role and role not in roles:
            roles.append(role)
    return roles


def ground_record(record: Mapping[str, Any],
                  timeline: grounding.RecordedTimeline | None = None,
                  ) -> dict[str, Any]:
    """One brief's grounding, per target role.

    The recorded timeline is the surface the claims are checked against.
    A machine with no recorded timeline answers nothing, and every role
    is then CLAIMED or UNRESOLVED - never verified by a timeline nobody
    read.
    """
    recorded = timeline if timeline is not None else grounding.recorded_timeline(
        record)
    items = target_grounding.items_of(recorded.items)
    claims = claims_of(record)
    rows = []
    for role in roles_of(record):
        claim = claims.get(role)
        row = target_grounding.ground_role(role, claim, items=items)
        rows.append(row)
    counts = {state: 0 for state in target_grounding.STATES}
    for row in rows:
        counts[str(row["state"])] = counts.get(str(row["state"]), 0) + 1
    operations = operations_of(record)
    refusals = target_grounding.refuse_ungrounded(operations, {
        "targets": rows})
    destructive = [operation for operation in operations
                   if target_grounding.is_destructive(operation["op"])]
    return {
        "brief_id": str(record.get("brief_id") or record.get("task_id") or ""),
        "role_instances": len(rows),
        "grounded_role_instances": counts[target_grounding.VERIFIED],
        "observed_role_instances": counts[target_grounding.OBSERVED],
        "claimed_role_instances": counts[target_grounding.CLAIMED],
        "unresolved_role_instances": counts[target_grounding.UNRESOLVED],
        "planned_operations": len(operations),
        "destructive_operations": len(destructive),
        "destructive_operations_refused": len(refusals),
        "refusals": refusals,
        "recorded_timeline": {"available": recorded.available,
                              "note": recorded.note},
        "targets": rows,
    }


def gate(record: Mapping[str, Any],
         timeline: grounding.RecordedTimeline | None = None) -> dict[str, Any]:
    """The REQUIRED planning gate: what may not be staged.

    A plan whose destructive operations name a target the footage does
    not carry may NOT be staged as it stands.  The gate says which
    operations are refused and why, and it says whether the plan may
    stage - it does not rewrite the plan, and it does not touch the
    planning stage's own number (AGENTS.md 10.4: a gate that cannot see
    cannot forbid; a refusal that is not the gate's business is not the
    gate's verdict).
    """
    row = ground_record(record, timeline)
    return {
        "brief_id": row["brief_id"],
        "may_stage": not row["destructive_operations_refused"],
        "destructive_operations": row["destructive_operations"],
        "destructive_operations_refused": row["destructive_operations_refused"],
        "refusals": row["refusals"],
        "targets": row["targets"],
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
    """The measurement: targets grounded, and destructive plans refused.

    The planning number is read from the harness that measures the
    benchmark, before and after, and it is the SAME number twice: the
    grounding stage runs after the planning stage and rewrites nothing
    the planning stage reads, so a plan whose targets are refused keeps
    the planning credit of the plan the run made.
    """
    from library.tools.editorial_acceptance import runner

    rows = [ground_record(record) for record in records]
    roles = sum(row["role_instances"] for row in rows)
    grounded = sum(row["grounded_role_instances"] for row in rows)
    observed = sum(row["observed_role_instances"] for row in rows)
    claimed = sum(row["claimed_role_instances"] for row in rows)
    unresolved = sum(row["unresolved_role_instances"] for row in rows)
    destructive = sum(row["destructive_operations"] for row in rows)
    refused = sum(row["destructive_operations_refused"] for row in rows)
    may_stage = sum(1 for row in rows
                     if not row["destructive_operations_refused"])
    report = runner.run_acceptance(list(records), brief_set=brief_set)
    planning = _planning_rate(report)
    return {
        "schema": SCHEMA,
        "brief_count": len(rows),
        "role_instances": roles,
        "grounded_role_instances": grounded,
        "observed_role_instances": observed,
        "claimed_role_instances": claimed,
        "unresolved_role_instances": unresolved,
        "destructive_operations": destructive,
        "destructive_operations_refused": refused,
        "plans_that_may_stage": may_stage,
        "plans_refused_before_staging": len(rows) - may_stage,
        "value": ((grounded / roles) if roles else None),
        "coverage": {
            "planning": planning,
            "planning_before": planning,
            "planning_after": planning,
            "note": ("the grounding stage runs after the planning stage "
                     "and rewrites nothing the planning stage reads, so a "
                     "plan whose targets are refused keeps its planning "
                     "credit"),
        },
        "briefs": rows,
    }


def _read_records(source: str | Path) -> list[dict[str, Any]]:
    from library.tools.editorial_acceptance import runner

    return runner.read_records(source)


def main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_acceptance.grounding_stage",
        description=("Measure PLAN-003 on the eight-brief benchmark: the "
                     "target roles grounded, and the destructive plans "
                     "refused for a target that could not be established."))
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
