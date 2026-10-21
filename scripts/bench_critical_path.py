#!/usr/bin/env python3
"""Read a performance ledger back as a CRITICAL PATH, for the report.

The engine's own reader (`library/tools/critical_path.py`) computes one
run's critical path: the stages that are serial, the resource each stage
is limited by, the limit the task is under, and the next optimization
the measurement implies.  This script applies that reader to a ledger
that holds SEVERAL runs and prints one row per task class, so a
critical-path report over three real task classes reads as three
comparable rows with their raw numbers - not as three separate profile
dumps.

Nothing here measures anything: every number is read from the ledger,
and a number no row measured stays absent rather than becoming a zero.

    scripts/bench_critical_path.py <ledger.jsonl> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools import critical_path, perf_ledger


def _read_rows(ledger: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with ledger.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def _classes(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Every task class in the ledger, with how many COMPLETED runs it has."""
    counts: dict[str, int] = {}
    for row in rows:
        if (row.get("kind") == perf_ledger.TASK
                and row.get("state") == "completed"):
            name = str(row.get("task_class") or "unclassified")
            counts[name] = counts.get(name, 0) + 1
    return counts


def _representative(rows: list[dict[str, Any]], task_class: str
                    ) -> dict[str, Any] | None:
    """The run of this class a reader should look at.

    The run with the most INSTRUMENTED work: a run that measured
    nothing says nothing about where the work went, however well it
    finished.
    """
    best = None
    for run_id in critical_path.runs_of_class(rows, task_class):
        report = critical_path.analyse_run(rows, run_id)
        seconds = float(report.get("instrumented_wall_s") or 0.0)
        if seconds <= 0:
            continue
        if best is None or seconds > float(
                best.get("instrumented_wall_s") or 0.0):
            best = report
    return best


def report_for(ledger: Path) -> dict[str, Any]:
    rows = _read_rows(ledger)
    classes = _classes(rows)
    per_class = {}
    for name in sorted(classes):
        summary = critical_path.summarise_class(rows, name)
        representative = _representative(rows, name)
        per_class[name] = {
            "runs": summary["runs"],
            "finished": summary["finished"],
            "instrumented": summary["instrumented"],
            "limits": summary["limits"],
            "latency_s": summary["latency_s"],
            "instrumented_wall_s": summary["instrumented_wall_s"],
            "representative": representative,
            "every_run": [
                {
                    "run_id": run["run_id"],
                    "outcome": run["outcome"],
                    "latency_s": run["latency_s"],
                    "instrumented_wall_s": run["instrumented_wall_s"],
                    "limit": run["limit"]["name"],
                    "limit_seconds": run["limit"]["seconds"],
                    "families": run["families"],
                    "stages": [
                        {"stage": stage["stage"],
                         "wall_s": stage["wall_s"],
                         "role": stage["role"],
                         "exclusive_s": stage["exclusive_s"],
                         "shared_s": stage["shared_s"],
                         "shared_with": stage["shared_with"],
                         "bottleneck": stage["bottleneck"],
                         "next_optimization": stage["next_optimization"]}
                        for stage in run["stages"]
                    ],
                }
                for run in summary["per_run"]
            ],
        }
    return {"ledger": str(ledger), "rows": len(rows), "classes": per_class}


def _seconds(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f}"


def render(report: dict[str, Any]) -> str:
    lines = [f"Ledger: {report['ledger']}", ""]
    for name, entry in report["classes"].items():
        lines.append(
            f"{name}: {entry['runs']} run(s), {entry['finished']} finished, "
            f"{entry['instrumented']} instrumented")
        limits = ", ".join(f"{limit} x{count}" for limit, count
                           in sorted(entry["limits"].items()))
        lines.append(f"    limits seen: {limits or 'none'}")
        lines.append(f"    latency p50 {_seconds(entry['latency_s']['p50'])}s, "
                     f"p95 {_seconds(entry['latency_s']['p95'])}s, "
                     f"max {_seconds(entry['latency_s']['max'])}s")
        lines.append(f"    instrumented wall p50 "
                     f"{_seconds(entry['instrumented_wall_s']['p50'])}s, "
                     f"p95 {_seconds(entry['instrumented_wall_s']['p95'])}s")
        representative = entry.get("representative")
        if representative:
            lines.append(f"    most-instrumented run "
                         f"{representative['run_id']}:")
            lines.extend("      " + line
                         for line in critical_path.render_run(representative))
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ledger", help="the performance ledger to read")
    parser.add_argument("--json", dest="json_out", default="",
                        help="write the whole report as JSON to this path")
    args = parser.parse_args(argv)
    report = report_for(Path(args.ledger).expanduser().resolve())
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
