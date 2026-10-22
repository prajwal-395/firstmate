#!/usr/bin/env python3
"""What the execution strategies cost in ONE project, with denominators.

AXI-016.  The engine can reach a timeline three ways - a direct patch,
a staged incremental and a full rebuild - and they do not cost the
same.  This script reads ONE project's performance ledger and says what
each of them has MEASURED to cost there, how many requests that was
measured over, and what each of them preserves and verifies.

The rules it keeps:

* a strategy with no measured row is reported as UNMEASURED, never as a
  zero - "nothing was measured" and "it was free" are different facts;
* the denominator is always beside the number - a p50 over one request
  says so;
* the verification outcome of every request is counted, because a
  cheaper route that verifies less is not cheaper;
* the preservation guarantees come from the same module the selection
  reads (`library/tools/execution_cost.py`), so this report and the
  decision cannot disagree.

    scripts/measure_execution_cost.py <project folder> [--json]

The project's ledger is the engine's own
(`<project>/pipeline_output/logs/perf_ledger.jsonl`); `--ledger` names
another file.  A project with no ledger reports that nothing was
measured, which is the honest answer and not a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _rows(project_folder: str, ledger: str = "") -> list[dict]:
    from library.tools import perf_ledger

    return perf_ledger.read_rows(
        project_folder,
        ledger_override=ledger or None)


def _report(rows: list[dict]) -> dict:
    from library.tools import execution_cost

    costs = execution_cost.measured_costs(rows)
    strategies = []
    for entry in execution_cost.comparison(costs):
        name = entry["strategy"]
        measured = costs.get(name)
        strategies.append({
            "strategy": name,
            "mutation_scope": entry["mutation_scope"],
            "constructs_whole_reel": entry["constructs_whole_reel"],
            "requests": int(measured.get("requests") or 0) if measured else 0,
            "verified": int(measured.get("verified") or 0) if measured else 0,
            "completed": int(measured.get("completed") or 0) if measured else 0,
            "declined": int(measured.get("declined") or 0) if measured else 0,
            "refused": int(measured.get("refused") or 0) if measured else 0,
            "failed": int(measured.get("failed") or 0) if measured else 0,
            "interrupted": int(measured.get("interrupted") or 0) if measured else 0,
            "wall_s_p50": measured.get("wall_s_p50") if measured else None,
            "wall_s_p95": measured.get("wall_s_p95") if measured else None,
            "wall_s_min": measured.get("wall_s_min") if measured else None,
            "wall_s_max": measured.get("wall_s_max") if measured else None,
            "measured": bool(measured),
            "preserves": entry["preserves"],
            "disturbs": entry["disturbs"],
            "verification": entry["verification"],
            "recovery": entry["recovery"],
        })
    return {
        "strategies": strategies,
        "measured_strategies": sorted(costs),
        "requests_measured": sum(entry["requests"] for entry in strategies),
    }


def _print(report: dict) -> None:
    print("execution strategies, measured in this project")
    print()
    header = (f"{'strategy':<20} {'requests':>8} {'verified':>8} "
              f"{'completed':>9} {'declined':>8} {'p50 s':>9} {'p95 s':>9}"
              f"  scope")
    print(header)
    print("-" * len(header))
    for entry in report["strategies"]:
        if not entry["measured"]:
            print(f"{entry['strategy']:<20} {'-':>8} {'-':>8} {'-':>9} "
                  f"{'-':>8} {'unmeasured':>9} {'unmeasured':>9}  "
                  f"{entry['mutation_scope']}")
            continue
        print(f"{entry['strategy']:<20} {entry['requests']:>8} "
              f"{entry['verified']:>8} {entry['completed']:>9} "
              f"{entry['declined']:>8} "
              f"{entry['wall_s_p50']:>9} {entry['wall_s_p95']:>9}  "
              f"{entry['mutation_scope']}")
    print()
    for entry in report["strategies"]:
        if entry.get("interrupted"):
            print(f"({entry['strategy']}: {entry['interrupted']} request(s) "
                  f"were INTERRUPTED and are counted apart - their seconds "
                  f"are a lower bound, not a request's cost)")
    print()
    for entry in report["strategies"]:
        print(f"{entry['strategy']}:")
        print(f"  preserves: {'; '.join(entry['preserves'])}")
        print(f"  disturbs:  {'; '.join(entry['disturbs'])}")
        print(f"  verifies:  {'; '.join(entry['verification'])}")
        print(f"  recovery:  {entry['recovery']}")
        print()
    print(f"requests measured: {report['requests_measured']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("project", help="the project folder to measure")
    parser.add_argument("--ledger", default="",
                        help="the ledger to read instead of the project's own")
    parser.add_argument("--json", action="store_true",
                        help="print the report as JSON")
    args = parser.parse_args(argv)

    rows = _rows(args.project, args.ledger)
    report = _report(rows)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    _print(report)
    if not report["requests_measured"]:
        print("nothing was measured for this project yet - the numbers "
              "above are UNMEASURED, not zero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
