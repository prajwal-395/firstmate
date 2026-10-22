"""One machine-readable trace for one editorial request.

`ren profile` says where a run's wall went, layer by layer.
`ren profile --critical-path` says which stages ran alone and which
overlapped, and what each stage was limited by. Neither answers the
punch list's M01 question directly: for ONE request - one search, one
edit, one recorded run - where did every second go, in the vocabulary
of an editorial request, with the seconds no stage earned reported
rather than hidden.

This module is that reader. It reads the same ledger
(`pipeline_output/logs/perf_ledger.jsonl`) the profile reads - it adds
no store, no tracer, no background writer - and assembles one trace
per request:

    request   which run (and which task inside it) this trace accounts
              for, and the window it occupied.
    stages    the run's wall, attributed to the eleven M01 stages:
              source discovery and metadata; source decoding and frame
              extraction; audio decoding and speech analysis;
              semantic/event detection; embedding/index construction;
              candidate generation; ranking and verification; planning;
              Resolve queue wait; Resolve execution; validation.
    totals    wall clock, active compute (CPU), queue wait, cache hits
              and misses, CPU/GPU usage and peak memory - each
              separately, each unmeasured rather than zero where no
              row measured it.
    model     warm-up and cold start beside each other: the load's
              seconds, the warm reuse, and the cold vs warm vs
              unsaid inference split (`perf_report.model_access`).
    ingestion the source-ingestion work beside the per-request work:
              building a store once per corpus (discovery/catalogue,
              index construction) is ingestion; every decode,
              inference and execution serves a request, even when the
              cache serves it.
    remainder the wall no stage claimed, reported per capability -
              never folded into a stage that did not earn it.

A stage with no spans in this request is UNMEASURED for it, never a
zero: `spans` carries the count, so a reader can tell "this request
did no ranking" from "ranking cost nothing". A layer no request stage
claims is `unclassified` time. The capability wall outside every span
is `unattributed` time with the owning capability named. The three
numbers - stages, unclassified, unattributed - add up to the run's
capability wall; a trace that lost a second would be a second nobody
can account for.

Nothing here runs a task, loads a model or touches Resolve: this is a
READER over rows a run already recorded, the way `critical_path` is.

    python3 -m library.tools.request_trace <project> [--run RUN]
        [--task TASK-ID] [--json]
    ren profile --trace [--run RUN] [--task TASK-ID]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from library.tools import perf_ledger, perf_report

#: The schema a request trace is written in. It is named here because
#: the trace is a report like any other, and a reader has to know
#: which numbers this shape carries.
TRACE_SCHEMA = "ren-request-trace/1"

# ── The eleven M01 stages ──────────────────────────────────────────
#
# The punch list names them as the stages of a real editorial request.
# Each is a THING THE REQUEST PAID FOR, not a pipeline phase: the
# same stage is measured the same way whatever capability ran it.

SOURCE_DISCOVERY = "source_discovery"
SOURCE_DECODE = "source_decode"
AUDIO_SPEECH = "audio_speech"
SEMANTIC_EVENTS = "semantic_events"
INDEX_BUILD = "index_build"
CANDIDATE_GENERATION = "candidate_generation"
RANKING_VERIFICATION = "ranking_verification"
PLANNING = "planning"
RESOLVE_QUEUE = "resolve_queue"
RESOLVE_EXECUTION = "resolve_execution"
VALIDATION = "validation"

REQUEST_STAGES: tuple[str, ...] = (
    SOURCE_DISCOVERY,
    SOURCE_DECODE,
    AUDIO_SPEECH,
    SEMANTIC_EVENTS,
    INDEX_BUILD,
    CANDIDATE_GENERATION,
    RANKING_VERIFICATION,
    PLANNING,
    RESOLVE_QUEUE,
    RESOLVE_EXECUTION,
    VALIDATION,
)

#: What each stage is, in one line, for the human rendering.
STAGE_TITLES = {
    SOURCE_DISCOVERY: "source discovery and metadata",
    SOURCE_DECODE: "source decoding and frame extraction",
    AUDIO_SPEECH: "audio decoding and speech analysis",
    SEMANTIC_EVENTS: "semantic/event detection",
    INDEX_BUILD: "embedding/index construction",
    CANDIDATE_GENERATION: "candidate generation",
    RANKING_VERIFICATION: "ranking and verification",
    PLANNING: "planning",
    RESOLVE_QUEUE: "Resolve queue wait",
    RESOLVE_EXECUTION: "Resolve execution",
    VALIDATION: "validation",
}

#: The layers whose seconds build a store once per corpus. Everything
#: else a trace measures serves the request in front of it - a decode
#: the cache served still served THIS request, which is why the cache
#: hits are reported beside it rather than subtracted from it.
INGESTION_STAGES = (SOURCE_DISCOVERY, INDEX_BUILD)

# ── Which layer belongs to which request stage ─────────────────────
#
# EXPLICIT on purpose: a layer added to the ledger without a row here
# reads as `unclassified` time, never as a guess. The map covers every
# layer `perf_ledger.STAGE_LAYERS` and `critical_path.BUCKET_BY_LAYER`
# name, plus the audio-analysis layers neither classifies yet
# (`diarize_embed`, `filler_detect`, `ren_analysis_cli`, `demux`), so
# the trace leaves no known layer silent.
#
# Two layers need the ROW, not just the name. `media_decode` decodes
# video for frame extraction and audio for speech analysis; the row's
# own `media_kind` (and, where that is absent, its `decode_purpose`)
# says which. `reel_lifecycle` is a thin phase wrapper, so its stage
# is read from the row's phase the way `critical_path` reads it.

_LAYER_STAGE: dict[str, str] = {
    # Finding the material and writing down what it is.
    "source_ingest": SOURCE_DISCOVERY,
    "source_probe": SOURCE_DISCOVERY,
    "ren_analysis_cli": SOURCE_DISCOVERY,
    # Getting pixels and samples out of the files.
    "media_decode": SOURCE_DECODE,
    "demux": SOURCE_DECODE,
    "evidence_acquisition": SOURCE_DECODE,
    # Hearing the words: transcription, alignment, language, fillers,
    # speaker embeddings.
    "transcribe": AUDIO_SPEECH,
    "mfa_align": AUDIO_SPEECH,
    "language_id": AUDIO_SPEECH,
    "filler_detect": AUDIO_SPEECH,
    "diarize_embed": AUDIO_SPEECH,
    # Seeing what happens: the local VLM and the inference attempts.
    "gemma_inference": SEMANTIC_EVENTS,
    "semantic_inference_attempt": SEMANTIC_EVENTS,
    # Building the store the searches query.
    "footage_index_build": INDEX_BUILD,
    # Asking the store what matches.
    "footage_search": CANDIDATE_GENERATION,
    # Deciding the edit: the host model's answers, the local model's
    # runtime, and the reel build's own survey and plan.
    "host_model": PLANNING,
    "model_load": PLANNING,
    "model_cache": PLANNING,
    "model_task": PLANNING,
    "computer_use_decide": PLANNING,
    "reel_build_survey": PLANNING,
    "reel_build_plan": PLANNING,
    # Waiting for the one Resolve.
    "resolve_wait": RESOLVE_QUEUE,
    # Everything that executes inside the lease - the API calls, the
    # project opens, the placement pass, the touch-up phases, the
    # renders that produce pixels - plus the offline compile and file
    # write the placement does on Ren's own CPU, which are still the
    # execution of this request.
    "broker_request": RESOLVE_EXECUTION,
    "resolve_hold": RESOLVE_EXECUTION,
    "resolve_api_call": RESOLVE_EXECUTION,
    "resolve_snapshot_read": RESOLVE_EXECUTION,
    "resolve_mutation_batch": RESOLVE_EXECUTION,
    "resolve_connection_setup": RESOLVE_EXECUTION,
    "resolve_switch_settle_wait": RESOLVE_EXECUTION,
    "project_switch": RESOLVE_EXECUTION,
    "project_switch_load": RESOLVE_EXECUTION,
    "project_switch_settle": RESOLVE_EXECUTION,
    "project_switch_readback": RESOLVE_EXECUTION,
    "project_folder_capture": RESOLVE_EXECUTION,
    "project_save_before_borrow": RESOLVE_EXECUTION,
    "project_save_before_restore": RESOLVE_EXECUTION,
    "project_folder_restore": RESOLVE_EXECUTION,
    "project_timeline_restore": RESOLVE_EXECUTION,
    "project_restore": RESOLVE_EXECUTION,
    "qualification_broker_job": RESOLVE_EXECUTION,
    "edit_core_rust": RESOLVE_EXECUTION,
    "resolve_render": RESOLVE_EXECUTION,
    "remotion_render": RESOLVE_EXECUTION,
    "hyperframes_render": RESOLVE_EXECUTION,
    "fframes_render": RESOLVE_EXECUTION,
    "graphics_render": RESOLVE_EXECUTION,
    "hyperframes-caption": RESOLVE_EXECUTION,
    "ffframes-caption": RESOLVE_EXECUTION,
    "ffmpeg_encode": RESOLVE_EXECUTION,
    "reel_build_prepare": RESOLVE_EXECUTION,
    "reel_build_place": RESOLVE_EXECUTION,
    "reel_build_promote": RESOLVE_EXECUTION,
    "touchup_duplicate": RESOLVE_EXECUTION,
    "touchup_conform_compose_verify": RESOLVE_EXECUTION,
    "touchup_promote": RESOLVE_EXECUTION,
    "touchup_direct": RESOLVE_EXECUTION,
    "version_store": RESOLVE_EXECUTION,
    "archive_export": RESOLVE_EXECUTION,
    # Checking the built output is what was planned.
    "verification": VALIDATION,
    "reel_build_verify": VALIDATION,
    "render_qa": VALIDATION,
}

#: `reel_lifecycle` phases read as request stages, the way
#: `critical_path.LIFECYCLE_PHASE_STAGE` reads them as pipeline
#: stages. A phase absent here leaves the row unclassified rather
#: than guessed.
_LIFECYCLE_STAGE = {
    "planning": PLANNING,
    "model_access": PLANNING,
    "cache": PLANNING,
    "staging": RESOLVE_EXECUTION,
    "resolve_ops": RESOLVE_EXECUTION,
    "verification": VALIDATION,
    "promotion": RESOLVE_EXECUTION,
    "version_commits": RESOLVE_EXECUTION,
    "cleanup": RESOLVE_EXECUTION,
    "archive_export": RESOLVE_EXECUTION,
}


def request_stage_of(row: dict[str, Any]) -> str | None:
    """The M01 request stage a span's seconds belong to.

    None means "no request stage claims this layer", which the trace
    reports as `unclassified` time. It never means zero, and it never
    folds the seconds into a stage that did not earn them.
    """
    layer = str(row.get("layer") or "")
    if not layer or layer in perf_ledger.SUMMARY_LAYERS:
        # A summary of other rows, not a stage of its own.
        return None
    if layer == "reel_lifecycle":
        return _LIFECYCLE_STAGE.get(str(row.get("phase") or ""))
    if layer == "media_decode":
        # The same layer decodes picture and sound: the row says which.
        if str(row.get("media_kind") or "") == "audio":
            return AUDIO_SPEECH
        purpose = str(row.get("decode_purpose") or "")
        if "audio" in purpose:
            return AUDIO_SPEECH
        return SOURCE_DECODE
    return _LAYER_STAGE.get(layer)


def _task_window(rows: list[dict[str, Any]],
                 run_id: str, task_id: str) -> tuple[float, float] | None:
    """The wall a task occupied, or None where it cannot be read.

    A task without a recorded window cannot narrow a trace: narrowing
    to half a window would lose seconds, so the trace stays run-wide
    and says so.
    """
    for row in rows:
        if row.get("kind") != perf_ledger.TASK:
            continue
        if str(row.get("task_id") or "") != task_id:
            continue
        if row.get("run_id") not in (None, run_id):
            continue
        if row.get("state") != "completed":
            continue
        start = row.get("requested_at")
        end = row.get("completed_at")
        if start is None or end is None:
            return None
        return (float(start), float(end))
    return None


def trace_for_run(rows: list[dict[str, Any]], run_id: str,
                  task_id: str | None = None) -> dict[str, Any]:
    """One machine-readable trace for one request.

    The request is the run, narrowed to one task's window when
    `task_id` names a completed task with a recorded window. Every
    second of the run's capability wall lands in exactly one of three
    places - a named M01 stage, `unclassified` (a layer no stage
    claims), or `unattributed` (capability wall outside every span) -
    and the trace reports all three, so nothing is silently lost.
    """
    from library.tools import ledger_anomaly
    rows = ledger_anomaly.clamp_rows(rows)
    caps = [r for r in rows if r.get("run_id") == run_id
            and r.get("kind") == perf_ledger.CAPABILITY]
    spans = [r for r in rows if r.get("run_id") == run_id
             and r.get("kind") == perf_ledger.SPAN
             and str(r.get("layer") or "")
             not in perf_ledger.SUMMARY_LAYERS]

    window: tuple[float, float] | None = None
    task_scoped = False
    if task_id is not None:
        window = _task_window(rows, run_id, task_id)
        if window is not None:
            task_scoped = True
            caps = [c for c in caps
                    if window[0] - 1e-6 <= float(c.get("started_at") or 0.0)
                    <= window[1] + 1e-6]
            spans = [s for s in spans
                     if window[0] - 1e-6 <= float(s.get("started_at") or 0.0)
                     <= window[1] + 1e-6]

    total_wall = sum(float(c.get("wall_s") or 0.0) for c in caps)
    total_cpu = sum(float(c.get("cpu_s") or 0.0) for c in caps)

    per_stage: dict[str, dict[str, Any]] = {
        stage: {"wall_s": 0.0, "cpu_s": 0.0, "spans": 0,
                "layers": set(), "_intervals": []}
        for stage in REQUEST_STAGES}
    unclassified = {"wall_s": 0.0, "cpu_s": 0.0, "spans": 0,
                    "layers": set(), "_intervals": []}
    cache_hits = cache_misses = 0
    cache_measured = False
    for s in spans:
        stage = request_stage_of(s)
        bucket = per_stage.get(stage) if stage else None
        if bucket is None:
            bucket = unclassified
        bucket["spans"] += 1
        bucket["layers"].add(str(s.get("layer")))
        bucket["cpu_s"] += float(s.get("cpu_s") or 0.0)
        bucket["_intervals"].extend(perf_ledger.span_intervals(s))
        if s.get("cache_hits") is not None:
            cache_measured = True
            cache_hits += int(s.get("cache_hits") or 0)
        if s.get("cache_misses") is not None:
            cache_measured = True
            cache_misses += int(s.get("cache_misses") or 0)

    stages = []
    attributed = 0.0
    for stage in REQUEST_STAGES:
        bucket = per_stage[stage]
        wall = perf_ledger.union_s(bucket["_intervals"])
        attributed += wall
        stages.append({
            "stage": stage,
            "title": STAGE_TITLES[stage],
            "wall_s": round(wall, 3),
            "pct": (round(100.0 * wall / total_wall, 1)
                    if total_wall else None),
            "cpu_s": round(bucket["cpu_s"], 3),
            "spans": bucket["spans"],
            "layers": sorted(bucket["layers"]),
        })
    unclassified_wall = perf_ledger.union_s(unclassified["_intervals"])
    everything = []
    for bucket in list(per_stage.values()) + [unclassified]:
        everything.extend(bucket["_intervals"])
    covered = perf_ledger.union_s(everything)

    remainder_rows = []
    for c in caps:
        wall = float(c.get("wall_s") or 0.0)
        own = [i for s in spans
               if str(s.get("capability")) == str(c.get("capability"))
               for i in perf_ledger.span_intervals(s)]
        inside = min(wall, perf_ledger.union_s(own))
        remainder_rows.append({
            "capability": str(c.get("capability")),
            "wall_s": round(wall, 3),
            "covered_s": round(inside, 3),
            "remainder_s": round(max(0.0, wall - inside), 3),
        })
    remainder_rows.sort(key=lambda r: -r["remainder_s"])

    ingestion_wall = sum(s["wall_s"] for s in stages
                         if s["stage"] in INGESTION_STAGES)
    ingestion_cpu = sum(s["cpu_s"] for s in stages
                        if s["stage"] in INGESTION_STAGES)

    task_rows = [r for r in rows if r.get("kind") == perf_ledger.TASK
                 and (not r.get("run_id") or r.get("run_id") == run_id)]
    task_entry: dict[str, Any] | None = None
    if task_id is not None:
        task_entry = next(
            ({"task_id": str(r.get("task_id") or ""),
              "task_class": r.get("task_class"),
              "outcome": r.get("outcome"),
              "latency_s": (round(float(r["latency_s"]), 3)
                            if r.get("latency_s") is not None else None)}
             for r in task_rows
             if str(r.get("task_id") or "") == task_id
             and r.get("state") == "completed"),
            None)

    counts: dict[str, int | None] = {}
    for r in rows:
        if r.get("run_id") != run_id or r.get("kind") != perf_ledger.COUNT:
            continue
        family = r.get("family")
        if isinstance(family, str):
            counts[family] = (counts.get(family) or 0) + int(
                r.get("count") or 0)

    from library.tools import report_provenance
    return {
        "schema": TRACE_SCHEMA,
        "request": {
            "run_id": run_id,
            "task_id": task_id,
            "task_scoped": task_scoped,
            "task": task_entry,
            "capabilities": len(caps),
            "total_wall_s": round(total_wall, 3),
            "total_cpu_s": round(total_cpu, 3),
        },
        "stages": stages,
        "attributed_s": round(attributed, 3),
        "unclassified_s": round(unclassified_wall, 3),
        "unclassified_layers": sorted(unclassified["layers"]),
        "unattributed_s": round(max(0.0, total_wall - covered), 3),
        "unattributed_by_capability": remainder_rows,
        "overlap_s": round(max(0.0, attributed - covered), 3),
        "ingestion": {
            "rule": ("source discovery/catalogue and index construction "
                     "build a store once per corpus; every decode, "
                     "inference and execution serves a request"),
            "stages": sorted(INGESTION_STAGES),
            "wall_s": round(ingestion_wall, 3),
            "cpu_s": round(ingestion_cpu, 3),
        },
        "per_request": {
            "stages": [s for s in REQUEST_STAGES
                       if s not in INGESTION_STAGES],
            "wall_s": round(attributed - ingestion_wall, 3),
        },
        "queue": {
            "resolve_lease_wait_s": next(
                s["wall_s"] for s in stages if s["stage"] == RESOLVE_QUEUE),
            "scheduler_queue_s": perf_report.waits(
                rows, run_id).get("scheduler_queue_s"),
        },
        "cache": {
            "hits": cache_hits if cache_measured else None,
            "misses": cache_misses if cache_measured else None,
            "measured": cache_measured,
            "media_decode_requests": counts.get("media_decode_requests"),
            "media_decode_cache_hits": counts.get(
                "media_decode_cache_hits"),
        },
        "compute": {
            # No ledger row carries a GPU counter: the local model's
            # GPU seconds hide inside the semantic/model stage walls.
            # Reporting them as unmeasured keeps a reader from
            # mistaking "nobody measured the GPU" for "the GPU cost
            # nothing".
            "cpu_s": round(total_cpu, 3),
            "gpu_s": None,
            "gpu_note": ("no GPU counter is instrumented on any ledger "
                         "row; model GPU seconds are inside the "
                         "semantic/model stage wall time"),
        },
        "resources": perf_report.resource_summary(rows, run_id),
        "model": perf_report.model_access(rows, run_id),
        "provenance": report_provenance.header(
            evidence_kind="live-qualification",
            evaluation_schema=TRACE_SCHEMA,
            fixture=f"the performance ledger's rows for run {run_id}",
            producer=("library/tools/request_trace.py:trace_for_run "
                      "(ren profile --trace)"),
            scope={
                "measurement": "request_trace",
                "evaluation_level": "not_applicable",
                "task_set": (f"run {run_id} task {task_id}"
                             if task_id else f"run {run_id}"),
                "fixture": (f"the performance ledger's rows for run "
                            f"{run_id}"),
            },
        ),
    }


def render(report: dict[str, Any]) -> str:
    """The human-readable half of `ren profile --trace`."""
    request = report.get("request") or {}
    total = float(request.get("total_wall_s") or 0.0)
    scope = f"run {request.get('run_id')}"
    if request.get("task_id"):
        scope += (f" task {request['task_id']} (windowed)"
                  if request.get("task_scoped")
                  else f" task {request['task_id']} (no recorded window: "
                       "run-wide)")
    out = [f"Request trace for {scope}: {total:.1f}s across "
           f"{request.get('capabilities', 0)} capability row(s)"]
    for stage in report.get("stages") or []:
        if not stage.get("spans"):
            continue
        pct = stage.get("pct")
        out.append(f"  {stage['stage']:<22} {stage['wall_s']:>9.1f}s "
                   f"{('' if pct is None else f'{pct:>5.1f}%'):>6}  "
                   f"{stage.get('spans', 0)} span(s) "
                   f"[{STAGE_TITLES.get(stage['stage'], '')}]")
    missing = [s["stage"] for s in report.get("stages") or []
               if not s.get("spans")]
    if missing:
        out.append("  unmeasured here (no spans, never zero): "
                   + ", ".join(missing))
    if report.get("unclassified_s"):
        out.append(f"  {'(unclassified)':<22} "
                   f"{report['unclassified_s']:>9.1f}s        "
                   f"layers no stage claims: "
                   + ", ".join(report.get("unclassified_layers") or []))
    if report.get("unattributed_s"):
        out.append(f"  {'(unattributed)':<22} "
                   f"{report['unattributed_s']:>9.1f}s        "
                   "capability wall outside every span:")
        for row in report.get("unattributed_by_capability") or []:
            if float(row.get("remainder_s") or 0.0) <= 0:
                continue
            out.append(f"    {row.get('capability')}: "
                       f"{row['remainder_s']:.1f}s of {row['wall_s']:.1f}s "
                       "outside every span")
    if report.get("overlap_s", 0) >= 0.05:
        out.append(f"{report['overlap_s']:.1f}s ran in two stages at once "
                   "and is counted in both.")
    ingestion = report.get("ingestion") or {}
    per_request = report.get("per_request") or {}
    out.append(f"Ingestion {ingestion.get('wall_s', 0):.1f}s "
               f"({', '.join(ingestion.get('stages') or [])}); "
               f"per-request {per_request.get('wall_s', 0):.1f}s.")
    queue = report.get("queue") or {}
    out.append(f"Queue: Resolve lease {queue.get('resolve_lease_wait_s', 0):.1f}s, "
               f"scheduler {queue.get('scheduler_queue_s', 0):.1f}s.")
    cache = report.get("cache") or {}
    if cache.get("measured"):
        out.append(f"Cache: {cache.get('hits', 0)} hit(s), "
                   f"{cache.get('misses', 0)} miss(es) on spans; "
                   f"media decodes {cache.get('media_decode_requests')} "
                   f"requested, {cache.get('media_decode_cache_hits')} "
                   "served from cache.")
    else:
        out.append("Cache: unmeasured - no span carried a cache count.")
    compute = report.get("compute") or {}
    out.append(f"Compute: CPU {compute.get('cpu_s', 0):.1f}s; GPU "
               "unmeasured (no GPU counter instrumented).")
    resources = report.get("resources") or {}
    peak = resources.get("peak_rss_mb")
    out.append("Peak RSS: "
               + ("unmeasured" if peak is None else f"{peak:.1f} MB") + ".")
    model = report.get("model") or {}
    out.append(f"Model: load {model.get('model_load_s', 0):.1f}s, "
               f"cold {model.get('cold_inference_s', 0):.1f}s "
               f"({model.get('cold_inferences', 0)}), warm "
               f"{model.get('warm_inference_s', 0):.1f}s "
               f"({model.get('warm_inferences', 0)}).")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    """`python3 -m library.tools.request_trace <project> [--run RUN] ...`."""
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren profile --trace",
        description="One machine-readable trace for one request: every "
                    "second of its wall attributed to a named M01 stage, "
                    "with the unattributed remainder reported. Reads "
                    f"pipeline_output/logs/{perf_ledger.LEDGER_FILE}; "
                    "changes nothing.")
    parser.add_argument("project", nargs="?",
                        help="a project slug or path")
    parser.add_argument("--run", help="a run id (default: the latest)")
    parser.add_argument("--task", help="a task id to narrow the trace to")
    parser.add_argument("--ledger",
                        help="read a standalone perf ledger JSONL path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.project and not args.ledger:
        parser.error("provide a project or --ledger")
    project = (perf_ledger._resolve_project(args.project)
               if not args.ledger else args.project or "")
    rows = perf_ledger.read_rows(project, args.ledger)
    ids = perf_ledger.run_ids(rows)
    if args.task and not args.run:
        runs = sorted({r.get("run_id") for r in rows
                       if str(r.get("task_id") or "") == args.task
                       and r.get("run_id")}) if rows else set()
        run_id = sorted(runs)[-1] if runs else None
    else:
        run_id = args.run or (ids[-1] if ids else None)
    if not run_id or run_id not in ids:
        print(f"No run {run_id!r} in this ledger; `--list` shows what is "
              "there." if run_id else "No run ids in this ledger.",
              file=sys.stderr)
        return 1
    report = trace_for_run(rows, run_id, task_id=args.task)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )
    try:
        sys.exit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
