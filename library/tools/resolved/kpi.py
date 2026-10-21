"""What the one Resolve cost the machine, read off the broker's receipts.

The single-Resolve plan's KPIs (captain, 2026-10-02, item 14), for every
job the broker scheduled in a window - grants and executed jobs alike:

    utilization          seconds some job held Resolve, over the window
    hold / exclusive     seconds held, and the part no other job shared
    wait p50 / p95       queue time, submit to start
    coalesced            submissions that joined a running or queued
                         identical read instead of repeating it
    batched              EditPatch operations committed per Resolve job
    cursor changes       jobs that moved the broker's project/timeline
                         from the last one a job named (a grant names
                         none, so its own moves are in its run's ledger:
                         `perf_ledger` `timeline_switches`)
    render               seconds held by render-priority jobs
    live reads avoided   timeline questions the shadow store answered
                         (`timeline_shadow` `answers`)
    shadow-hit rate      facade and `timeline_shadow` CLI requests served
                         by the shadow, divided by those hits, live
                         refreshes and misses in `timeline_shadow`

A run's own share - its agents' time lost waiting for Resolve over their
wall clock - is `ren profile` (`perf_ledger.resolve_kpis`).

Read-only: the job table is opened `mode=ro`, never through `JobStore`,
whose constructor closes a live broker's open jobs as failed.

    ren resolved kpi [--hours N | --since EPOCH] [--until EPOCH] [--json]
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional

from library.tools.perf_ledger import quantile, union_s
from library.tools.resolved.job_owner import owner_alive
from library.tools.resolved.store import receipt

RENDER_PRIORITIES = ("qa_render", "export")


def _queue_intervals(jobs: List[dict], until: float) -> List[tuple]:
    """Each job's time in the queue: submitted to started (or to the end).

    A job that is still queued at the end of the window is queued until
    the window ends - an absent measurement is never zero.
    """
    spans = []
    for job in jobs:
        start = float(job.get("submitted") or 0.0)
        if job.get("started") is not None:
            end = float(job["started"])
        elif job.get("finished") is not None:
            end = float(job["finished"])
        else:
            end = until
        if end > start:
            spans.append((start, end, job))
    return spans


def _running_intervals(jobs: List[dict], until: float) -> List[tuple]:
    """Each job's time holding Resolve: started to finished."""
    spans = []
    for job in jobs:
        if job.get("started") is None:
            continue
        start = float(job["started"])
        end = float(job["finished"]) if job.get("finished") is not None else until
        if end > start:
            spans.append((start, end, job))
    return spans


def occupancy(jobs: List[dict], until: float) -> dict:
    """How the broker's time was spent: busy, idle with a queue, idle without.

    `idle_with_queue_s` is the measure the queue report cannot give: the
    seconds the broker held nothing while work was waiting for it. It is
    the number that says whether a long wait was the MACHINE's or the
    BROKER's.
    """
    queue = _queue_intervals(jobs, until)
    running = _running_intervals(jobs, until)
    edges = {float(job.get("submitted") or 0.0) for job in jobs}
    edges.update({until})
    for start, end, _ in queue:
        edges.add(start)
        edges.add(end)
    for start, end, _ in running:
        edges.add(start)
        edges.add(end)
    points = sorted(edge for edge in edges if edge <= until)
    idle_with_queue = 0.0
    idle_without_queue = 0.0
    busy_with_queue = 0.0
    previous = points[0] if points else until
    for point in points:
        span = point - previous
        if span > 0:
            waiting = any(start <= previous < end for start, end, _ in queue)
            holding = any(start <= previous < end for start, end, _ in running)
            if waiting and holding:
                busy_with_queue += span
            elif waiting:
                idle_with_queue += span
            else:
                idle_without_queue += span
        previous = point
    return {
        "idle_with_queue_s": round(idle_with_queue, 3),
        "idle_without_queue_s": round(idle_without_queue, 3),
        "busy_with_queue_s": round(busy_with_queue, 3),
    }


def wait_causes(jobs: List[dict], until: float) -> List[dict]:
    """Which job held each waiting second.

    Every second a job spent in the queue is attributed to the job that
    was holding Resolve at that moment, so the wait is explained rather
    than only counted. A second is counted once: where two jobs overlap,
    the one that held it longest takes it.
    """
    queue = _queue_intervals(jobs, until)
    running = _running_intervals(jobs, until)
    shares: dict = {}
    for start, end, waiting in queue:
        edges = {start, end}
        for run_start, run_end, _ in running:
            for point in (run_start, run_end):
                if start < point < end:
                    edges.add(point)
        points = sorted(edges)
        for left, right in zip(points, points[1:]):
            middle = (left + right) / 2
            holders = [job for run_start, run_end, job in running
                       if run_start <= middle < run_end]
            if not holders:
                key = ("NOTHING RUNNING", "the broker was not scheduling")
            else:
                holder = max(holders, key=lambda job: min(
                    float(job.get("finished") or until), end) - max(
                        float(job.get("started") or 0.0), start))
                key = (holder.get("kind", ""),
                       holder.get("description") or holder.get("kind", ""))
            bucket = shares.setdefault(
                key, {"blocked_s": 0.0, "waiters": set()})
            bucket["blocked_s"] += right - left
            bucket["waiters"].add(waiting.get("id", ""))
    total = sum(bucket["blocked_s"] for bucket in shares.values()) or 1.0
    return sorted(
        [{"kind": kind, "description": description,
          "blocked_s": round(bucket["blocked_s"], 3),
          "share": round(bucket["blocked_s"] / total, 4),
          "waiters": len(bucket["waiters"])}
         for (kind, description), bucket in shares.items()],
        key=lambda row: -row["blocked_s"])


def dead_owner_jobs(jobs: List[dict]) -> List[dict]:
    """The jobs still in the queue whose owner is gone.

    A grant whose owner is gone is a job nobody is waiting for and
    nobody will release.
    """
    rows = []
    for job in jobs:
        if job.get("state") not in ("queued", "running"):
            continue
        if owner_alive(job.get("owner", "")) is False:
            rows.append({"id": job["id"], "kind": job["kind"],
                         "owner": job["owner"], "state": job["state"],
                         "waited_s": round(
                             max(0.0, (job.get("started") or until_now())
                                 - float(job.get("submitted") or 0.0)), 3)})
    return rows


def until_now() -> float:
    return time.time()


def receipts_since(db: Path, since: float,
                   until: Optional[float] = None) -> List[dict]:
    """Every receipt submitted in the inclusive window, oldest first."""
    if not Path(db).exists():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if until is None:
            rows = conn.execute("SELECT * FROM jobs WHERE submitted >= ?"
                                " ORDER BY submitted", (since,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM jobs WHERE submitted >= ?"
                                " AND submitted <= ? ORDER BY submitted",
                                (since, until)).fetchall()
    finally:
        conn.close()
    return [receipt(row) for row in rows]


def kpis(jobs: Iterable[dict], since: float, until: float,
         shadow_answers: Optional[int] = None,
         shadow_reads: Optional[dict] = None) -> dict:
    jobs = list(jobs)
    started = [j for j in jobs if j["started"] is not None]
    held = [j for j in started if j["hold_seconds"] is not None]
    window = max(0.0, until - since)
    busy = union_s([(j["started"], j["finished"]) for j in held])
    patches = [j for j in started if j["kind"] == "timeline.apply_patch"]
    operations = sum(len((j["params"].get("patch") or {}).get(
        "operations") or []) for j in patches)
    cursor, changes = None, 0
    for job in sorted(started, key=lambda j: j["started"]):
        if job["project"]:
            here = (job["project"], job["timeline"])
            if cursor is not None and here != cursor:
                changes += 1
            cursor = here
    states: dict = {}
    for job in jobs:
        states[job["state"]] = states.get(job["state"], 0) + 1
    waits = [j["wait_seconds"] for j in started]
    hold_values = [j["hold_seconds"] for j in held]
    by_kind_rows: dict = {}
    for job in jobs:
        operation_kind = (job["params"].get("purpose") or job["kind"])
        bucket = by_kind_rows.setdefault(operation_kind, {
            "jobs": 0, "started": 0, "waits": [], "holds": []})
        bucket["jobs"] += 1
        if job["started"] is not None:
            bucket["started"] += 1
            bucket["waits"].append(job["wait_seconds"])
        if job["hold_seconds"] is not None:
            bucket["holds"].append(job["hold_seconds"])
    by_kind = {
        kind: {
            "jobs": bucket["jobs"], "started": bucket["started"],
            "wait_p50_s": quantile(bucket["waits"], 0.5),
            "wait_p95_s": quantile(bucket["waits"], 0.95),
            "hold_p50_s": quantile(bucket["holds"], 0.5),
            "hold_p95_s": quantile(bucket["holds"], 0.95),
        }
        for kind, bucket in by_kind_rows.items()
    }
    shadow_reads = shadow_reads or {}
    shadow_hits = shadow_reads.get("shadow_hits")
    live_refreshes = shadow_reads.get("live_refreshes")
    misses = shadow_reads.get("misses")
    request_count = (None if shadow_hits is None else
                     shadow_hits + live_refreshes + misses)
    occupancy_report = occupancy(jobs, until)
    window = max(0.0, until - since)
    return {
        "since": round(since, 3), "until": round(until, 3),
        "jobs": len(jobs), "states": states, "by_kind": by_kind,
        "utilization": round(busy / window, 4) if window else None,
        "hold_s": round(sum(j["hold_seconds"] for j in held), 3),
        "exclusive_hold_s": round(sum(j["hold_seconds"] for j in held
                                      if j["mode"] == "exclusive"), 3),
        "wait_p50_s": quantile(waits, 0.5),
        "wait_p95_s": quantile(waits, 0.95),
        "wait_p99_s": quantile(waits, 0.99),
        "wait_max_s": max(waits) if waits else None,
        "hold_p50_s": quantile(hold_values, 0.5),
        "hold_p95_s": quantile(hold_values, 0.95),
        "hold_p99_s": quantile(hold_values, 0.99),
        "hold_max_s": max(hold_values) if hold_values else None,
        "idle_with_queue_s": occupancy_report["idle_with_queue_s"],
        "idle_without_queue_s": occupancy_report["idle_without_queue_s"],
        "busy_with_queue_s": occupancy_report["busy_with_queue_s"],
        "idle_with_queue_share": (round(
            occupancy_report["idle_with_queue_s"] / window, 4)
            if window else None),
        "wait_causes": wait_causes(jobs, until),
        "dead_owner_jobs": dead_owner_jobs(jobs),
        "coalesced": sum(max(0, int(j["subscribers"]) - 1) for j in jobs),
        "patches": len(patches),
        "operations_batched": operations,
        "cursor_changes": changes,
        "render_s": round(sum(j["hold_seconds"] for j in held
                              if j["priority"] in RENDER_PRIORITIES), 3),
        "live_reads_avoided": shadow_answers,
        "shadow_read_requests": request_count,
        "shadow_read_hits": shadow_hits,
        "live_refreshes": live_refreshes,
        "shadow_read_misses": misses,
        "shadow_hit_rate": (round(shadow_hits / request_count, 4)
                            if request_count else None),
    }


def render(report: dict) -> str:
    def s(value):
        return "-" if value is None else f"{value:.2f}s"
    util = report["utilization"]
    if report["shadow_read_requests"] is None:
        shadow_rate = "shadow-hit rate: - (no recorded read history)"
    else:
        shadow_rate = (
            "shadow-hit rate: "
            + ("-" if report["shadow_hit_rate"] is None else
               f"{100 * report['shadow_hit_rate']:.1f}%")
            + f" ({report['shadow_read_hits']}/"
            + f"{report['shadow_read_requests']}; "
            + f"{report['live_refreshes']} live refreshes, "
            + f"{report['shadow_read_misses']} misses)")
    lines = [
        f"{report['jobs']} job(s) since "
        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(report['since']))}"
        f" {json.dumps(report['states'], sort_keys=True)}",
        f"Resolve utilization {'-' if util is None else f'{100 * util:.1f}%'}"
        f"; held {report['hold_s']:.1f}s, "
        f"{report['exclusive_hold_s']:.1f}s exclusive; render "
        f"{report['render_s']:.1f}s",
        f"queue wait p50 {s(report['wait_p50_s'])} p95 "
        f"{s(report['wait_p95_s'])} p99 {s(report['wait_p99_s'])} "
        f"max {s(report['wait_max_s'])}",
        f"Resolve hold p50 {s(report['hold_p50_s'])} p95 "
        f"{s(report['hold_p95_s'])} p99 {s(report['hold_p99_s'])} "
        f"max {s(report['hold_max_s'])}",
        f"idle with a queue {report['idle_with_queue_s']:.1f}s "
        f"({'idle without one ' + format(report['idle_without_queue_s'], '.1f') + 's'})",
    ]
    if report["wait_causes"]:
        lines.append("what held the queue (each waiting second counted once)")
        for row in report["wait_causes"][:10]:
            lines.append(
                f"  {row['blocked_s']:9.1f}s {100 * row['share']:5.1f}%  "
                f"{row['kind']} - {row['description']}")
    if report["dead_owner_jobs"]:
        lines.append(f"{len(report['dead_owner_jobs'])} job(s) still waiting "
                     f"whose owner is gone")
        for row in report["dead_owner_jobs"][:10]:
            lines.append(f"  {row['kind']} [{row['owner']}] "
                         f"waiting {row['waited_s']:.0f}s")
    lines += [
        f"{report['coalesced']} coalesced; {report['operations_batched']} "
        f"operation(s) in {report['patches']} patch commit(s); "
        f"{report['cursor_changes']} cursor change(s)",
        "live reads avoided by the shadow store: "
        + ("-" if report["live_reads_avoided"] is None
           else str(report["live_reads_avoided"])),
        shadow_rate,
    ]
    lines.append("Operation kind | jobs | wait p50/p95 | hold p50/p95")
    for kind, bucket in sorted(report["by_kind"].items()):
        lines.append(
            f"{kind} | {bucket['jobs']} | "
            f"{s(bucket['wait_p50_s'])} / {s(bucket['wait_p95_s'])} | "
            f"{s(bucket['hold_p50_s'])} / {s(bucket['hold_p95_s'])}")
    return "\n".join(lines)


def main(hours: Optional[float], as_json: bool,
         since: Optional[float] = None,
         until: Optional[float] = None) -> int:
    from library.tools import timeline_shadow
    from library.tools.resolved.server import db_path
    until = time.time() if until is None else until
    since = (until - (24.0 if hours is None else hours) * 3600
             if since is None else since)
    if since > until:
        print("ren resolved kpi: --since must be at or before --until",
              file=sys.stderr)
        return 2
    report = kpis(receipts_since(db_path(), since, until), since, until,
                  timeline_shadow.answers_since(since, until=until),
                  timeline_shadow.read_requests_since(since, until=until))
    print(json.dumps(report, indent=2) if as_json else render(report))
    return 0
