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
from library.tools.resolved.store import receipt

RENDER_PRIORITIES = ("qa_render", "export")


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
    shadow_reads = shadow_reads or {}
    shadow_hits = shadow_reads.get("shadow_hits")
    live_refreshes = shadow_reads.get("live_refreshes")
    misses = shadow_reads.get("misses")
    request_count = (None if shadow_hits is None else
                     shadow_hits + live_refreshes + misses)
    return {
        "since": round(since, 3), "until": round(until, 3),
        "jobs": len(jobs), "states": states,
        "utilization": round(busy / window, 4) if window else None,
        "hold_s": round(sum(j["hold_seconds"] for j in held), 3),
        "exclusive_hold_s": round(sum(j["hold_seconds"] for j in held
                                      if j["mode"] == "exclusive"), 3),
        "wait_p50_s": quantile(waits, 0.5),
        "wait_p95_s": quantile(waits, 0.95),
        "hold_p50_s": quantile(hold_values, 0.5),
        "hold_p95_s": quantile(hold_values, 0.95),
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
        f"{s(report['wait_p95_s'])}",
        f"Resolve hold p50 {s(report['hold_p50_s'])} p95 "
        f"{s(report['hold_p95_s'])}",
        f"{report['coalesced']} coalesced; {report['operations_batched']} "
        f"operation(s) in {report['patches']} patch commit(s); "
        f"{report['cursor_changes']} cursor change(s)",
        "live reads avoided by the shadow store: "
        + ("-" if report["live_reads_avoided"] is None
           else str(report["live_reads_avoided"])),
        shadow_rate,
    ]
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
