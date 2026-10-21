"""`ren accept`: score a run of unfamiliar briefs against their expectations.

The acceptance run is two halves, and they happen in different places.
The WORK is done by whoever takes the brief - the live lane's driver, or
an agent working the `ren-co-editor` loop - and it leaves a task record
per brief. The MEASUREMENT is done here, on the records, with no
Resolve, no model and no network.

    ren accept --list
        the briefs in the set, their edge cases and their coverage

    ren accept --records <path> [--report <path>] [--judge <module:attr>]
        measure every record against its brief and write the report

    ren accept --select
        the substrate each brief would run against, and why

`--records` takes one JSON file holding a list of records, or a
directory of one-record files. `--judge` names a factory that returns
the independent judge; without one, the editorial half of a task is
`not_measured` and the report says so rather than scoring it.
`--substrates` names the substrate catalogue to measure against.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from typing import Any

from library.tools.editorial_acceptance import briefs, footage, runner


def _load_factory(spec: str):
    """`module:attribute` - the attribute is a callable that builds the
    judge."""
    if ":" not in spec:
        raise SystemExit(
            f"--judge wants module:attribute, not {spec!r}")
    module_name, _, attribute = spec.partition(":")
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise SystemExit(
            f"--judge {spec!r} names something that is not callable")
    return factory


def _list_briefs(brief_set: dict[str, Any],
                 catalogue: dict[str, Any]) -> int:
    coverage = briefs.coverage(brief_set)
    selection = footage.select_for_set(brief_set, catalogue)
    print(f"brief set: {brief_set.get('path')}")
    print(f"sha256:    {brief_set.get('sha256')}")
    print(f"briefs:    {coverage['brief_count']} "
          f"({coverage['acceptance_criterion_count']} criteria)")
    for brief in brief_set["briefs"]:
        key = briefs.oracle(brief)
        print(f"\n  {brief['id']}  [{key['edge_case'] or 'no edge case'}]")
        print(f"    {brief['instruction']}")
        for criterion in brief["acceptance_criteria"]:
            print(f"      - {criterion['id']}: {criterion['statement']}")
        for requirement in key.get("footage_requirements") or []:
            feature = requirement.get("feature")
            print(f"      premise: {feature} must be "
                  f"{requirement.get('presence')}")
        chosen = selection["briefs"].get(str(brief["id"]), {})
        print(f"      substrate: {chosen.get('selected')} "
              f"({chosen.get('reason')})")
    print("\nedge cases covered: "
          + ", ".join(f"{case} ({count})" for case, count
                      in sorted(coverage["edge_case_counts"].items())))
    if coverage["edge_cases_missing"]:
        print("edge cases MISSING: "
              + ", ".join(coverage["edge_cases_missing"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren accept",
        description="Score a run of unfamiliar editorial briefs.")
    parser.add_argument("--briefs", default=None,
                        help="the brief set to measure against")
    parser.add_argument("--records", default=None,
                        help="a record file, or a directory of them")
    parser.add_argument("--judge", default=None,
                        help="module:attribute naming a factory that "
                             "returns the independent judge")
    parser.add_argument("--report", default=None,
                        help="where to write the report")
    parser.add_argument("--substrates", default=None,
                        help="the substrate catalogue to measure against")
    parser.add_argument("--select", action="store_true",
                        help="print the substrate each brief would run "
                             "against, and why")
    parser.add_argument("--list", action="store_true",
                        help="print the briefs and their coverage, and stop")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    brief_set = briefs.load_brief_set(args.briefs)
    catalogue = footage.load_catalogue(args.substrates)
    if args.select:
        selection = footage.select_for_set(brief_set, catalogue)
        print(json.dumps(selection, indent=2, sort_keys=True))
        return 0
    if args.list or not args.records:
        return _list_briefs(brief_set, catalogue)
    judge = _load_factory(args.judge)() if args.judge else None
    records = runner.read_records(args.records)
    report = runner.run_acceptance(records, brief_set=brief_set, judge=judge,
                                   catalogue=catalogue)
    out = runner.write_report(report, args.report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps({
            "task_count": report["task_count"],
            "scored": report["scored"],
            "not_run": report["not_run"],
            "passed": report["passed"],
            "failed": report["failed"],
            "stage_metrics": {
                stage: {"value": row["value"], "measured": row["measured"],
                        "not_measured": row["not_measured"]}
                for stage, row in report["stage_metrics"]["stages"].items()},
            "premise": {
                "not_carried": report["premise"]["premise_not_carried"],
                "unchecked": report["premise"]["premise_unchecked"],
                "carried": report["premise"]["premise_carried_count"],
            },
            "failure_causes": {
                "bottleneck": report["failure_causes"]["bottleneck"],
                "dominant_cause": report["failure_causes"]["dominant_cause"],
                "by_stage": report["failure_causes"]["by_stage"],
            },
        }, indent=2))
    print(f"wrote {out}")
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
