#!/usr/bin/env python3
"""Read a performance ledger back as per-task user-visible latency.

The engine's own reader (`library/tools/perf_report.py`) answers the
profile's questions for one RUN: the stages of an edit, the latency the
person waited for, the waits, the machine's state. This script applies
that reader to a ledger that holds SEVERAL tasks and prints one row per
task, so a benchmark of four tasks reads as four comparable rows with
their raw numbers - not as four separate profile dumps.

Nothing here measures anything: every number is read from the ledger, and
a number no row measured stays absent rather than becoming a zero.

    scripts/bench_read_ledger.py <ledger.jsonl> [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools import perf_ledger, perf_report  # noqa: E402


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


def _stage_wall(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-stage seconds, charged the way the profile charges a layer.

    A stage is charged the time its spans COVER; overlapping spans inside
    one stage count once. A span whose layer no stage claims is
    `unclassified` time and is never folded into a stage that did not
    earn it.
    """
    buckets: dict[str, list[tuple[float, float]]] = {
        stage: [] for stage in perf_ledger.STAGES}
    unclassified: list[tuple[float, float]] = []
    layers: dict[str, set[str]] = {stage: set() for stage in perf_ledger.STAGES}
    for row in rows:
        if row.get("kind") != perf_ledger.SPAN:
            continue
        layer = str(row.get("layer"))
        if layer in perf_ledger.SUMMARY_LAYERS:
            continue
        stage = perf_ledger.stage_of(layer)
        intervals = perf_ledger.span_intervals(row)
        if stage:
            buckets[stage].extend(intervals)
            layers[stage].add(layer)
        else:
            unclassified.extend(intervals)
    total = perf_ledger.union_s([interval for group in buckets.values()
                                 for interval in group] + unclassified)
    stages = []
    for stage, intervals in buckets.items():
        wall = perf_ledger.union_s(intervals)
        stages.append({
            "stage": stage,
            "wall_s": round(wall, 3),
            "pct": (round(100.0 * wall / total, 1) if total else None),
            "layers": sorted(layers[stage]),
        })
    return {
        "total_span_wall_s": round(total, 3),
        "stages": stages,
        "unclassified_s": round(perf_ledger.union_s(unclassified), 3),
    }


def _layer_wall(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wall: dict[str, float] = {}
    for row in rows:
        if row.get("kind") != perf_ledger.SPAN:
            continue
        layer = str(row.get("layer"))
        wall[layer] = wall.get(layer, 0.0) + float(row.get("wall_s") or 0.0)
    return [{"layer": layer, "wall_s": round(seconds, 3)}
            for layer, seconds in sorted(wall.items(),
                                         key=lambda item: -item[1])]


def _counters(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("kind") != perf_ledger.COUNT:
            continue
        family = str(row.get("family"))
        counts[family] = counts.get(family, 0) + int(row.get("count") or 0)
    return counts


def _task_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The task rows, each with the completion that closed it."""
    requested: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != perf_ledger.TASK:
            continue
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        if row.get("state") == "requested":
            requested.setdefault(task_id, row)
        elif row.get("state") == "completed":
            completed[task_id] = row
    reported = []
    for task_id, row in completed.items():
        start = requested.get(task_id, {}).get("requested_at")
        end = row.get("completed_at")
        latency = row.get("latency_s")
        if latency is None and start is not None and end is not None:
            latency = round(float(end) - float(start), 3)
        reported.append({
            "task_id": task_id,
            "task_class": row.get("task_class"),
            "run_id": row.get("run_id"),
            "outcome": row.get("outcome"),
            "latency_s": round(float(latency or 0.0), 3),
            "requested_at": start,
            "completed_at": end,
            "peak_child_rss_mb": row.get("peak_child_rss_mb"),
        })
    reported.sort(key=lambda row: -(row["latency_s"] or 0.0))
    return reported


def report_for(ledger: Path) -> dict[str, Any]:
    rows = _read_rows(ledger)
    tasks = _task_rows(rows)
    reported = []
    for task in tasks:
        run_id = task.get("run_id")
        scoped = [row for row in rows
                  if not run_id or row.get("run_id") == run_id]
        capabilities = [row for row in scoped
                        if row.get("kind") == perf_ledger.CAPABILITY]
        capability_wall = sum(float(row.get("wall_s") or 0.0)
                              for row in capabilities)
        reported.append({
            **task,
            "capability_rows": len(capabilities),
            "capability_wall_s": round(capability_wall, 3),
            "stages": _stage_wall(scoped),
            "layers": _layer_wall(scoped),
            "waits": perf_report.waits(scoped, run_id) if run_id else {},
            "model_access": perf_report.model_access(scoped, run_id) if run_id else {},
            "project_open": perf_report.project_open(scoped, run_id) if run_id else {},
            "resources": perf_report.resource_summary(scoped, run_id) if run_id else {},
            "counts": _counters(scoped),
            "verified_decisions": perf_report.cost_per_verified_decision(
                scoped, run_id).get("verified_decisions"),
        })
    return {"ledger": str(ledger), "rows": len(rows), "tasks": reported}


def render(report: dict[str, Any]) -> str:
    lines = [f"Ledger: {report['ledger']}", ""]
    for task in report["tasks"]:
        lines.append(
            f"{task['task_class']}  run={task['run_id']}  "
            f"waited {task['latency_s']:.1f}s  outcome={task['outcome']}  "
            f"peak child RSS {task.get('peak_child_rss_mb')}MB")
        stages = (task.get("stages") or {}).get("stages") or []
        for stage in stages:
            if not stage.get("wall_s"):
                continue
            lines.append(f"    {stage['stage']:<12} {stage['wall_s']:>8.1f}s"
                         f"  {stage.get('pct')}%")
        unclassified = (task.get("stages") or {}).get("unclassified_s")
        if unclassified:
            lines.append(f"    {'(no stage)':<12} {unclassified:>8.1f}s")
        waits = task.get("waits") or {}
        if waits:
            lines.append(
                f"    waits: scheduler {waits.get('scheduler_queue_s')}s, "
                f"lease {waits.get('resolve_lease_wait_s')}s, "
                f"project open {waits.get('project_open_s')}s")
        access = task.get("model_access") or {}
        if access:
            lines.append(
                f"    model: load {access.get('model_load_s')}s, "
                f"cold {access.get('cold_inference_s')}s, "
                f"warm {access.get('warm_inference_s')}s")
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
