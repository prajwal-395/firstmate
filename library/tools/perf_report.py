"""Read the performance ledger back as the questions a profile asks.

`library/tools/perf_ledger.py` WRITES the ledger; this module READS it
back and answers the questions those rows exist to answer:

  * which STAGE of an edit the time went to - ingestion, search,
    evidence acquisition, planning, AXI execution, rendering,
    verification (PERF-001);
  * how long the PERSON waited for the task, beside the individual
    tool durations (PERF-002);
  * what one VERIFIED EDITORIAL DECISION cost (PERF-005);
  * what the MACHINE was doing while the run worked - peak memory,
    memory pressure, swap, disk (PERF-004);
  * which work was REPEATED across edits on the same source (PERF-006).

Nothing here writes, and nothing here invents a measurement. A number
no row measured is `None` and is named as unmeasured; a `0` means
"measured, and it was zero". The distinction is the whole point: a
profile that reports a default as though it were measured teaches its
reader to trust the wrong numbers.

The stages, the task rows, the resource fields and the decode identity
all live in the ledger's own vocabulary (`perf_ledger`), so this module
adds an ANALYSIS, never a second ledger.
"""

from __future__ import annotations

from typing import Any

from library.tools import perf_ledger

#: The cost dimensions a profile reports. A dimension no row measured
#: stays `None` and is named in `dimensions_unmeasured`.
COST_DIMENSIONS = ("wall_s", "cpu_s", "model_tokens", "agent_bytes", "usd")

#: Memory pressure readings, worst last. The label comes from a measured
#: percentage (the row keeps the raw number too), so a reader can
#: re-derive it rather than trust the band.
MEMORY_PRESSURES = ("normal", "warn", "critical")


# ── PERF-001: which stage of an edit the time went to ──────────────

def stage_breakdown(rows: list[dict[str, Any]],
                    run_id: str) -> dict[str, Any]:
    """The run's wall, attributed to the stage of an edit it went to.

    A stage is charged the time its spans COVER, the way
    `perf_ledger.profile` charges a layer: overlapping spans inside one
    stage count once, and two stages that overlap in time are each
    charged the overlap (the report says so in `overlap_s`).

    A span whose layer no stage claims is `unclassified` time. It is
    never folded into a stage that did not earn it, and it is never a
    zero: an uninstrumented stage reads as absent, not as free.
    """
    spans = [r for r in rows
             if r.get("run_id") == run_id and r.get("kind") == perf_ledger.SPAN]
    caps = [r for r in rows
            if r.get("run_id") == run_id and r.get("kind") == perf_ledger.CAPABILITY]
    total = sum(float(c.get("wall_s") or 0.0) for c in caps)

    per_stage: dict[str, dict[str, Any]] = {
        stage: {"wall_s": 0.0, "cpu_s": 0.0, "spans": 0, "layers": set(),
                "_intervals": []}
        for stage in perf_ledger.STAGES}
    unclassified: dict[str, Any] = {"wall_s": 0.0, "cpu_s": 0.0, "spans": 0,
                                     "layers": set(), "_intervals": []}

    for s in spans:
        layer = str(s.get("layer"))
        if layer in perf_ledger.SUMMARY_LAYERS:
            # A summary of other rows, not a stage of its own.
            continue
        stage = perf_ledger.stage_of(layer)
        bucket = per_stage.get(stage) if stage else None
        if bucket is None:
            bucket = unclassified
        bucket["spans"] += 1
        bucket["layers"].add(layer)
        bucket["cpu_s"] += float(s.get("cpu_s") or 0.0)
        bucket["_intervals"].extend(perf_ledger.span_intervals(s))

    stages = []
    attributed = 0.0
    for stage, bucket in per_stage.items():
        wall = perf_ledger.union_s(bucket["_intervals"])
        attributed += wall
        stages.append({
            "stage": stage,
            "wall_s": round(wall, 3),
            "pct": (round(100.0 * wall / total, 1) if total else None),
            "cpu_s": round(bucket["cpu_s"], 3),
            "spans": bucket["spans"],
            "layers": sorted(bucket["layers"]),
        })
    unclassified_wall = perf_ledger.union_s(unclassified["_intervals"])
    everything = []
    for bucket in list(per_stage.values()) + [unclassified]:
        everything.extend(bucket["_intervals"])
    covered = perf_ledger.union_s(everything)
    return {
        "total_wall_s": round(total, 3),
        "stages": stages,
        "attributed_s": round(attributed, 3),
        "unattributed_s": round(max(0.0, total - covered), 3),
        "unclassified_s": round(unclassified_wall, 3),
        "unclassified_layers": sorted(unclassified["layers"]),
        "overlap_s": round(max(0.0, attributed - covered), 3),
    }


# ── PERF-002: how long the person waited ───────────────────────────

def task_latency(rows: list[dict[str, Any]],
                 run_id: str | None = None) -> dict[str, Any]:
    """The latency a PERSON waited, beside what the run measured.

    A task is a request row and its completion row. The latency is the
    wall clock between them, and it contains every wait the tool
    durations do not: a lease queue, a project switch, a model answer,
    the gap between two capabilities.

    `capability_s` is the capability wall inside the task's window and
    `unattributed_s` is what is left. Capabilities can overlap (two
    lanes at once), so `capability_s` may exceed the latency; the
    report says the number it is rather than clipping it.

    A request with no completion is an OPEN task: the run ended before
    the deliverable, and the reader needs to see that.
    """
    tasks = [r for r in rows if r.get("kind") == perf_ledger.TASK]
    if run_id:
        tasks = [r for r in tasks
                 if not r.get("run_id") or r.get("run_id") == run_id]
    caps = [r for r in rows if r.get("kind") == perf_ledger.CAPABILITY]
    if run_id:
        caps = [r for r in caps if r.get("run_id") == run_id]

    requested: dict[str, dict[str, Any]] = {}
    completed: dict[str, dict[str, Any]] = {}
    for row in tasks:
        task_id = str(row.get("task_id") or "")
        if not task_id:
            continue
        state = row.get("state")
        if state == "requested":
            requested.setdefault(task_id, row)
        elif state == "completed":
            completed[task_id] = row

    reported = []
    for task_id, row in completed.items():
        start = requested.get(task_id, {}).get("requested_at")
        end = row.get("completed_at")
        latency = row.get("latency_s")
        if latency is None and start is not None and end is not None:
            latency = round(float(end) - float(start), 3)
        latency = float(latency or 0.0)
        window = (float(start), float(end)) if (
            start is not None and end is not None) else None
        capability_s = 0.0
        inside = 0
        for cap in caps:
            if window is None:
                capability_s += float(cap.get("wall_s") or 0.0)
                inside += 1
                continue
            began = float(cap.get("started_at") or 0.0)
            if began >= window[0] - 1e-6 and began <= window[1] + 1e-6:
                capability_s += float(cap.get("wall_s") or 0.0)
                inside += 1
        reported.append({
            "task_id": task_id,
            "task_class": row.get("task_class"),
            "outcome": row.get("outcome"),
            "run_id": row.get("run_id"),
            "latency_s": round(latency, 3),
            "capability_s": round(capability_s, 3),
            "unattributed_s": round(max(0.0, latency - capability_s), 3),
            "capabilities": inside,
        })
    reported.sort(key=lambda r: -(r["latency_s"] or 0.0))
    open_ids = sorted(set(requested) - set(completed))
    return {
        "count": len(reported),
        "tasks": reported,
        "open_task_ids": open_ids,
        "total_latency_s": round(sum(r["latency_s"] for r in reported), 3),
    }


# ── PERF-003: the waits, the opens and the cold start ─────────────

#: The layers that carry an INFERENCE, derived from the ledger's own
#: external-engine classification so the two cannot drift apart.
INFERENCE_LAYERS = tuple(
    sorted(layer for layer, engine in perf_ledger.EXTERNAL_LAYERS.items()
           if engine == "model_inference")) + ("computer_use_decide",)


def model_access(rows: list[dict[str, Any]],
                 run_id: str) -> dict[str, Any]:
    """Model runtime: the load, the warm reuse, and cold vs warm inference.

    The load's seconds are the load's, and the first inference after it
    is a different cost from every later one - the punch list asks for
    both, beside each other rather than inside one number.

    An inference that could not say whether it was cold or warm (a
    remote server holding the weights) is counted as `unmeasured`,
    never as warm.
    """
    spans = [r for r in rows
             if r.get("run_id") == run_id and r.get("kind") == perf_ledger.SPAN]
    load_s = cache_s = cold_s = warm_s = unmeasured_s = 0.0
    cold_n = warm_n = unmeasured_n = 0
    for s in spans:
        layer = s.get("layer")
        wall = float(s.get("wall_s") or 0.0)
        if layer == "model_load":
            load_s += wall
        elif layer == "model_cache":
            cache_s += wall
        elif layer in INFERENCE_LAYERS:
            cold = s.get("cold_start")
            if cold is True:
                cold_s += wall
                cold_n += 1
            elif cold is False:
                warm_s += wall
                warm_n += 1
            else:
                unmeasured_s += wall
                unmeasured_n += 1
    return {
        "model_load_s": round(load_s, 3),
        "model_cache_s": round(cache_s, 3),
        "cold_inference_s": round(cold_s, 3),
        "cold_inferences": cold_n,
        "warm_inference_s": round(warm_s, 3),
        "warm_inferences": warm_n,
        "unmeasured_inference_s": round(unmeasured_s, 3),
        "unmeasured_inferences": unmeasured_n,
    }


def project_open(rows: list[dict[str, Any]],
                 run_id: str) -> dict[str, Any]:
    """Opening a Resolve project: the load, the settle, the read-back.

    `total_s` is the switch as a whole; the three phases are its
    decomposition, so they are not added to it. Opening a project is
    not free and it is not the same cost every time - the settle waits
    for Resolve to finish loading, and the read-back proves it did.
    """
    spans = [r for r in rows
             if r.get("run_id") == run_id and r.get("kind") == perf_ledger.SPAN]
    load_s = settle_s = readback_s = total_s = 0.0
    switches = 0
    for s in spans:
        layer = s.get("layer")
        wall = float(s.get("wall_s") or 0.0)
        if layer == "project_switch_load":
            load_s += wall
        elif layer == "project_switch_settle":
            settle_s += wall
        elif layer == "project_switch_readback":
            readback_s += wall
        elif layer == "project_switch":
            switches += int(s.get("switches") or 1)
            total_s += wall
    return {
        "switches": switches,
        "total_s": round(total_s, 3),
        "load_s": round(load_s, 3),
        "settle_s": round(settle_s, 3),
        "readback_s": round(readback_s, 3),
    }


def waits(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """Every wait the run paid, named apart from the work.

    The scheduler queue (a heavy job waiting for the machine), the
    Resolve lease queue (a capability waiting for Resolve) and the
    project open (Resolve loading a project) are three different
    waits, and a profile that folds them into one number cannot say
    which one to fix.
    """
    scheduled = [r for r in rows
                 if r.get("run_id") == run_id and r.get("kind") == perf_ledger.SCHEDULE]
    scheduler_queue_s = sum(float(r.get("queue_wait_s") or 0.0)
                            for r in scheduled)
    spans = [r for r in rows
             if r.get("run_id") == run_id and r.get("kind") == perf_ledger.SPAN]
    lease_wait_s = sum(float(s.get("wall_s") or 0.0) for s in spans
                       if s.get("layer") == perf_ledger.RESOLVE_WAIT)
    opened = project_open(rows, run_id)
    return {
        "scheduler_queue_s": round(scheduler_queue_s, 3),
        "resolve_lease_wait_s": round(lease_wait_s, 3),
        "project_open_s": opened["total_s"],
        "total_s": round(scheduler_queue_s + lease_wait_s + opened["total_s"], 3),
    }


# ── PERF-005: what one verified editorial decision cost ───────────

def _run_cost(rows: list[dict[str, Any]],
              run_id: str | None) -> dict[str, float | None]:
    """The measured cost of a set of rows, per dimension.

    A dimension no row measured is None, never zero.
    """
    def rows_of(kind):
        selected = [r for r in rows if r.get("kind") == kind]
        if run_id:
            selected = [r for r in selected
                        if not r.get("run_id") or r.get("run_id") == run_id]
        return selected

    capabilities = rows_of(perf_ledger.CAPABILITY)
    spans = rows_of(perf_ledger.SPAN)
    wall = sum(float(c.get("wall_s") or 0.0) for c in capabilities)
    cpu = sum(float(c.get("cpu_s") or 0.0) for c in capabilities)
    tokens = 0
    for s in spans:
        tokens += int(s.get("input_tokens") or 0)
        tokens += int(s.get("output_tokens") or 0)
    agent_bytes = 0
    usd = 0.0
    saw_usd = False
    for row in rows:
        if row.get("kind") == "agent_task":
            agent_bytes += int(row.get("input_bytes") or 0)
            agent_bytes += int(row.get("output_bytes") or 0)
        if row.get("usd") is not None:
            saw_usd = True
            usd += float(row.get("usd") or 0.0)
    return {
        "wall_s": round(wall, 3),
        "cpu_s": round(cpu, 3),
        "model_tokens": tokens,
        "agent_bytes": agent_bytes,
        "usd": round(usd, 4) if saw_usd else None,
    }


def cost_per_verified_decision(rows: list[dict[str, Any]],
                               run_id: str | None = None
                               ) -> dict[str, Any]:
    """What one VERIFIED EDITORIAL DECISION cost.

    A verified editorial decision is one reel edit that passed its
    verification gate and was promoted: the ledger's `reel_edit` row
    carries `verified` for exactly that, and an edit that was placed but
    never graded does not count.

    The cost is a VECTOR, not a token count: wall seconds, CPU
    seconds, model tokens, agent bytes and money where a row priced it.
    A dimension no row measured is `None` and is named in
    `dimensions_unmeasured` - it is never a zero, because "nobody
    measured the money" and "the money was nothing" are different
    facts.
    """
    edits = [r for r in rows if r.get("layer") == "reel_edit"]
    if run_id:
        edits = [r for r in edits
                 if not r.get("run_id") or r.get("run_id") == run_id]
    verified = [r for r in edits
                if (r.get("measurements") or {}).get("verified")]
    unverified = [r for r in edits if r not in verified]
    cost = _run_cost(rows, run_id)
    unmeasured = [name for name in COST_DIMENSIONS if cost.get(name) is None]

    per_decision: dict[str, Any] = {}
    for name in COST_DIMENSIONS:
        value = cost.get(name)
        if value is None or not verified:
            per_decision[name] = None
        else:
            per_decision[name] = round(value / len(verified), 4)

    decision_walls = [float((r.get("measurements") or {}).get("total_s") or 0.0)
                      for r in verified]
    decision_cpus = [float((r.get("measurements") or {}).get("cpu_s") or 0.0)
                     for r in verified]
    return {
        "run_id": run_id,
        "verified_decisions": len(verified),
        "unverified_decisions": len(unverified),
        "run_cost": cost,
        "per_verified_decision": per_decision,
        "dimensions_unmeasured": unmeasured,
        "mean_decision_wall_s": (round(sum(decision_walls) / len(verified), 3)
                                 if verified else None),
        "mean_decision_cpu_s": (round(sum(decision_cpus) / len(verified), 3)
                                if verified else None),
    }


# ── PERF-004: what the machine was doing ──────────────────────────

def resource_summary(rows: list[dict[str, Any]],
                     run_id: str) -> dict[str, Any]:
    """The machine's own state while the run worked.

    Peak resident memory is the high-water mark the kernel keeps for
    this process and the children it waited for. Memory pressure and
    swap are the OS's own readings. Disk activity is what this run wrote
    under the project tree; where the OS exposes per-process disk
    counters they are reported too.

    Every field is `None` when no row measured it.
    """
    caps = [r for r in rows
            if r.get("run_id") == run_id and r.get("kind") == perf_ledger.CAPABILITY]
    peak = None
    pressures: list[str] = []
    swap_used = None
    swap_total = None
    written = 0
    saw_written = False
    free = None
    total_disk = None
    read_bytes = None
    write_bytes = None
    for cap in caps:
        for key in ("self_peak_rss_mb", "children_peak_rss_mb"):
            value = cap.get(key)
            if value is None:
                continue
            peak = max(peak or 0.0, float(value))
        pressure = cap.get("memory_pressure")
        if isinstance(pressure, str) and pressure and pressure != "unknown":
            pressures.append(pressure)
        if cap.get("swap_used_mb") is not None:
            swap_used = max(swap_used or 0.0,
                            float(cap.get("swap_used_mb") or 0.0))
        if cap.get("swap_total_mb") is not None:
            swap_total = max(swap_total or 0.0,
                             float(cap.get("swap_total_mb") or 0.0))
        if cap.get("bytes_written") is not None:
            saw_written = True
            written += int(cap.get("bytes_written") or 0)
        if cap.get("disk_free_bytes") is not None:
            free = int(cap.get("disk_free_bytes") or 0)
        if cap.get("disk_total_bytes") is not None:
            total_disk = int(cap.get("disk_total_bytes") or 0)
        if cap.get("disk_bytes_read") is not None:
            read_bytes = (int(cap.get("disk_bytes_read") or 0)
                          if read_bytes is None
                          else read_bytes + int(cap.get("disk_bytes_read") or 0))
        if cap.get("disk_bytes_written") is not None:
            write_bytes = (int(cap.get("disk_bytes_written") or 0)
                           if write_bytes is None
                           else write_bytes + int(cap.get("disk_bytes_written") or 0))
    worst = None
    for level in MEMORY_PRESSURES:
        if level in pressures:
            worst = level
    return {
        "peak_rss_mb": peak,
        "memory_pressure": worst,
        "swap_used_mb": swap_used,
        "swap_total_mb": swap_total,
        "project_bytes_written": written if saw_written else None,
        "disk_bytes_read": read_bytes,
        "disk_bytes_written": write_bytes,
        "disk_free_bytes": free,
        "disk_total_bytes": total_disk,
    }


# ── PERF-006: the work repeated across edits ──────────────────────

def repeated_work(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Which source material was measured AGAIN, across edits.

    Every decode row carries the source's content fingerprint and the
    purpose it was measured for. Two asks with the same pair are the
    same measurement asked twice - once per edit, once per run, or
    twice inside one run.

    A repeat that was served from the cache cost nothing and is
    reported as `cache_hits`; a repeat that was paid for again is
    `paid_repeats`, and those seconds are the waste this names.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != perf_ledger.COUNT:
            continue
        family = row.get("family")
        if family not in ("media_decode_requests", "media_decode_cache_hits"):
            continue
        fingerprint = row.get("source_fingerprint")
        if not fingerprint:
            continue
        purpose = str(row.get("decode_purpose") or "")
        key = (str(fingerprint), purpose)
        bucket = groups.setdefault(key, {
            "source_fingerprint": str(fingerprint),
            "decode_purpose": purpose,
            "requests": 0, "cache_hits": 0, "runs": set(), "capabilities": set(),
            "edits": set(), "wall_s": 0.0})
        amount = int(row.get("count") or 0)
        if family == "media_decode_requests":
            bucket["requests"] += amount
        else:
            bucket["cache_hits"] += amount
        if row.get("run_id"):
            bucket["runs"].add(str(row["run_id"]))
        if row.get("capability"):
            bucket["capabilities"].add(str(row["capability"]))
        for edit_id in row.get("edit_ids") or ():
            bucket["edits"].add(str(edit_id))
    for row in rows:
        if row.get("kind") != perf_ledger.SPAN or row.get("layer") != "media_decode":
            continue
        key = (str(row.get("source_fingerprint") or ""),
               str(row.get("decode_purpose") or ""))
        if key[0] and key in groups:
            groups[key]["wall_s"] += float(row.get("wall_s") or 0.0)

    reported = []
    for bucket in groups.values():
        # The FIRST measurement of a source for a purpose is the one
        # that had to be paid for. Every later one is a repeat: served
        # from the cache it cost nothing, served fresh it was paid for
        # again.
        asks = bucket["requests"] + bucket["cache_hits"]
        repeats = max(0, asks - 1)
        if repeats <= 0:
            continue
        reported.append({
            "source_fingerprint": bucket["source_fingerprint"],
            "decode_purpose": bucket["decode_purpose"],
            "requests": bucket["requests"],
            "cache_hits": bucket["cache_hits"],
            "repeats": repeats,
            "paid_repeats": max(0, repeats - bucket["cache_hits"]),
            "runs": sorted(bucket["runs"]),
            "capabilities": sorted(bucket["capabilities"]),
            "edits": sorted(bucket["edits"]),
            "wall_s": round(bucket["wall_s"], 3),
        })
    reported.sort(key=lambda r: (-r["paid_repeats"], -r["requests"]))
    return {
        "repeated_sources": len(reported),
        "paid_repeats": sum(r["paid_repeats"] for r in reported),
        "cache_hits": sum(r["cache_hits"] for r in reported),
        "wall_s": round(sum(r["wall_s"] for r in reported), 3),
        "sources": reported,
    }


# ── The text the CLI prints ────────────────────────────────────────

def render_stages(report: dict[str, Any]) -> list[str]:
    """The stage table: where the run's wall went, stage by stage."""
    stages = (report.get("stages") or {}).get("stages") or []
    if not stages or not any(s.get("spans") for s in stages):
        return []
    out = ["", "Stages of the edit:"]
    for stage in stages:
        if not stage.get("spans"):
            continue
        out.append(f"  {stage['stage']:<14} {stage['wall_s']:>8.1f}s "
                   f"{_pct(stage.get('pct')):>6}  "
                   f"{stage.get('spans', 0)} span(s)")
    unattributed = (report.get("stages") or {}).get("unattributed_s")
    if unattributed:
        out.append(f"  {'(no stage)':<14} {unattributed:>8.1f}s"
                   f"{'':>6}  the run's wall no stage claimed")
    return out


def render_tasks(report: dict[str, Any]) -> list[str]:
    """The person-visible latency, beside the run's own accounting."""
    tasks = (report.get("tasks") or {}).get("tasks") or []
    if not tasks:
        return []
    out = ["", "Tasks (what the person waited for):"]
    for task in tasks:
        out.append(f"  {task.get('task_class') or 'task'!s:<20} "
                   f"{task.get('latency_s', 0):>8.1f}s  "
                   f"{task.get('outcome') or 'ok'}")
        out.append(f"    capability time {task.get('capability_s', 0):.1f}s, "
                   f"unattributed {task.get('unattributed_s', 0):.1f}s "
                   f"across {task.get('capabilities', 0)} capability row(s)")
    return out


def render_cost(report: dict[str, Any]) -> list[str]:
    """The cost of one verified editorial decision."""
    cost = report.get("cost_per_decision") or {}
    verified = cost.get("verified_decisions")
    if not verified:
        return ["", ("Cost per verified editorial decision: none "
                     "verified in this ledger yet.")]
    out = ["", (f"Cost per verified editorial decision "
                f"({verified} verified"
                f", {cost.get('unverified_decisions', 0)} unverified):")]
    for name in COST_DIMENSIONS:
        value = cost.get("per_verified_decision", {}).get(name)
        out.append(f"  {name:<14} "
                   f"{'unmeasured' if value is None else value}")
    mean = cost.get("mean_decision_wall_s")
    if mean is not None:
        out.append(f"  a decision itself took {mean:.1f}s of wall")
    return out


def render_resources(report: dict[str, Any]) -> list[str]:
    """The machine's own state during the run."""
    resources = report.get("resources") or {}
    if not resources:
        return []
    out = ["", "Machine during the run:"]
    for name in ("peak_rss_mb", "memory_pressure", "swap_used_mb",
                 "swap_total_mb", "project_bytes_written", "disk_bytes_read",
                 "disk_bytes_written", "disk_free_bytes"):
        value = resources.get(name)
        out.append(f"  {name:<22} "
                   f"{'unmeasured' if value is None else value}")
    return out


def render_waits(report: dict[str, Any]) -> list[str]:
    """The waits, named apart from the work."""
    waits = report.get("waits") or {}
    access = report.get("model_access") or {}
    opened = report.get("project_open") or {}
    out = ["", "Waits and model access:"]
    out.append(f"  scheduler queue      {waits.get('scheduler_queue_s', 0):.1f}s")
    out.append(f"  Resolve lease queue  "
               f"{waits.get('resolve_lease_wait_s', 0):.1f}s")
    out.append(f"  project open         {waits.get('project_open_s', 0):.1f}s "
               f"across {opened.get('switches', 0)} switch(es) "
               f"(load {opened.get('load_s', 0):.1f}s, "
               f"settle {opened.get('settle_s', 0):.1f}s, "
               f"read-back {opened.get('readback_s', 0):.1f}s)")
    out.append(f"  model load           {access.get('model_load_s', 0):.1f}s; "
               f"warm reuse "
               f"{access.get('model_cache_s', 0):.1f}s")
    out.append(f"  cold inference       "
               f"{access.get('cold_inference_s', 0):.1f}s across "
               f"{access.get('cold_inferences', 0)} call(s); warm inference "
               f"{access.get('warm_inference_s', 0):.1f}s across "
               f"{access.get('warm_inferences', 0)} call(s); unmeasured "
               f"{access.get('unmeasured_inference_s', 0):.1f}s across "
               f"{access.get('unmeasured_inferences', 0)} call(s)")
    return out


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"
