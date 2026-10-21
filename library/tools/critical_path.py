"""The COMPLETE critical path of a task, read off the performance ledger.

`perf_report.stage_breakdown` answers "where did the run's wall go, by
stage".  That is a SHARE, and a share cannot say whether two stages ran
one after the other (a serial dependency) or at the same time (an
overlap), so it cannot say which second of the wall is the one that
cannot be removed.  PERF-012 asks for that: the critical path, the
bottleneck resource behind each stage, and whether the task is limited
by INFERENCE, TRANSACTION OVERHEAD or QUEUE CONTENTION - because the
next optimization depends on which of the three it is, and "rewrite it in
Rust" or "parallelise it" is the wrong answer to two of them.

This module reads what the engine already measures and adds the three
readings the ledger alone does not give:

    serial vs overlapped   For every stage: the seconds it ran ALONE
                           (exclusive) and the seconds it shared with
                           another stage (shared), and which stage it
                           shared with.  The exclusive seconds are the
                           ones no amount of parallelism removes; the
                           shared seconds are the ones parallelism has
                           ALREADY taken.

    the bottleneck         For every stage: which RESOURCE its seconds
                           went to - the local GPU's model, a model
                           call, the disk, Resolve itself, the graphics
                           engine, the broker's queue, or Ren's own CPU -
                           with the seconds that earned it.

    the limit              For every task: whether it is limited by
                           inference, by transaction overhead (the cost
                           of talking to Resolve and keeping the books
                           around it), or by queue contention (the wait
                           for the one Resolve), named with the seconds
                           and the denominator they are a share of.

Every second is counted ONCE.  The ledger's rows nest - a container
span holds the spans it contains - so a naive sum charges the same
second to the container and to the child, and the totals then exceed
the wall they describe.  Here a sweep over the measured intervals
gives every second to the MOST SPECIFIC span that covers it, so the
stage shares, the resource shares and the family shares all add up to
the same wall.

Every number here is READ, never invented.  A stage no row measured is
`unmeasured`, not zero; a resource nothing was attributed to is named as
unattributed rather than folded into the biggest bucket.

    python3 -m library.tools.critical_path <project> [--run RUN]
    python3 -m library.tools.critical_path <project> --task-class axi.trim

Nothing here changes what any capability produces, and nothing here
runs a task: this is a READER over `pipeline_output/logs/
perf_ledger.jsonl`, the same file `ren profile` reads.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from library.tools import perf_ledger, perf_report

# ── The resources a stage's seconds can be spent on ─────────────────
#
# The punch list asks for GPU, CPU, disk, model and Resolve to be told
# apart, so they are separate buckets and nothing is folded into a
# bigger one.

GPU = "gpu"
MODEL = "model"
DISK = "disk"
RESOLVE = "resolve"
GRAPHICS = "graphics"
QUEUE = "queue"
OWN_CODE = "own_code"

BUCKETS = (GPU, MODEL, DISK, RESOLVE, GRAPHICS, QUEUE, OWN_CODE)

#: The GPU bucket is the LOCAL vision model's work - the weights are on
#: this machine's GPU, so its seconds are GPU seconds and no other
#: machine's.  The model bucket is every OTHER model call: the host
#: model (a remote service), transcription and forced alignment (CPU
#: inference, but a MODEL's work, not the disk's).  The disk bucket is
#: media being read or written.  The queue bucket is time spent WAITING
#: for the one Resolve - contention, not work, and the two are never
#: added together.
#:
#: A layer not named here is Ren's own code (`own_code`), which is the
#: CPU bucket.  The list is EXPLICIT so a new layer cannot silently
#: change a bottleneck; a layer added to the ledger without a bucket
#: here shows up as `own_code` and its name is printed.
BUCKET_BY_LAYER: dict[str, str] = {
    # The local VLM, on this machine's GPU.
    "gemma_inference": GPU,
    "semantic_inference_attempt": GPU,
    "model_load": GPU,
    "model_cache": GPU,
    # Every other model call.
    "host_model": MODEL,
    "mfa_align": MODEL,
    "transcribe": MODEL,
    "language_id": MODEL,
    "computer_use_decide": MODEL,
    # Media in and out of the disk.
    "media_decode": DISK,
    "source_probe": DISK,
    "source_ingest": DISK,
    "demux": DISK,
    "ffmpeg_encode": DISK,
    # The placement's own offline compile and the file it writes: Ren's
    # own CPU and the disk, not Resolve.
    "otio_placement.offline_compile_s": OWN_CODE,
    "otio_placement.otio_file_write_s": DISK,
    # The wait for the one Resolve: contention, not work.  It is its
    # own RESOURCE because a second spent WAITING is not a second spent
    # doing anything, and a bottleneck that says "own code" for a wait
    # names the wrong thing to optimise.
    "resolve_wait": QUEUE,
    # Resolve's own engine: its API, the lease frame it worked in, its
    # renders.  The seconds here are INSIDE Resolve.
    "resolve_hold": RESOLVE,
    "resolve_render": RESOLVE,
    "resolve_api_call": RESOLVE,
    "resolve_snapshot_read": RESOLVE,
    "resolve_mutation_batch": RESOLVE,
    "resolve_connection_setup": RESOLVE,
    "resolve_switch_settle_wait": RESOLVE,
    # The broker's own round trip and the Resolve API calls it carries.
    "broker_request": RESOLVE,
    "qualification_broker_job": RESOLVE,
    # The EDIT CORE: the Rust (or Python) implementation that computes
    # the edit and hands it to the broker.  Its seconds are Ren's own
    # code, not Resolve's.
    "edit_core_rust": OWN_CODE,
    "project_switch": RESOLVE,
    "project_switch_load": RESOLVE,
    "project_switch_settle": RESOLVE,
    "project_switch_readback": RESOLVE,
    "project_folder_capture": RESOLVE,
    "project_save_before_borrow": RESOLVE,
    "project_save_before_restore": RESOLVE,
    "project_folder_restore": RESOLVE,
    "project_timeline_restore": RESOLVE,
    "project_restore": RESOLVE,
    # The touch path's phases, and the whole-reel bookkeeping around a
    # promotion: these happen inside the lease.
    "touchup_duplicate": RESOLVE,
    "touchup_conform_compose_verify": RESOLVE,
    "touchup_promote": RESOLVE,
    "touchup_direct": RESOLVE,
    "version_store": RESOLVE,
    "archive_export": RESOLVE,
    # The OTIO placement pass.  Its phases end in a Resolve write - an
    # item's transform, a timeline's grade, a Fusion comp - so the
    # seconds are INSIDE Resolve, not Ren's own CPU.  The two that are
    # not (the offline compile and the file write) are named apart.
    "otio_placement.deferred_transform_s": RESOLVE,
    "otio_placement.unattributed_build_work_s": RESOLVE,
    "otio_placement.punch_in_s": RESOLVE,
    "otio_placement.transform_overrides_and_replay_s": RESOLVE,
    "otio_placement.overlay_sweep_readback_s": RESOLVE,
    "otio_placement.linking_s": RESOLVE,
    "otio_placement.grade_s": RESOLVE,
    "otio_placement.fusion_comps_s": RESOLVE,
    "otio_placement.ledger_retime_s": RESOLVE,
    "otio_placement.row_occupancy_cleanup_s": RESOLVE,
    "otio_placement.motion_coverage_s": RESOLVE,
    "otio_placement.import_setup_s": RESOLVE,
    "otio_placement.timeline_import_s": RESOLVE,
    "otio_placement.pool_organization_s": RESOLVE,
    "otio_placement.placement_restore_readback_s": RESOLVE,
    "otio_placement.source_markers_restore_s": RESOLVE,
    "otio_placement.exclusive_context_wall_s": RESOLVE,
    "otio_placement.mismatch_default_switch_s": RESOLVE,
    "otio_placement.mismatch_default_restore_s": RESOLVE,
    "otio_placement.mismatch_cursor_park_s": RESOLVE,
    "otio_placement.mismatch_cursor_return_s": RESOLVE,
    "otio_placement.mismatch_custom_size_s": RESOLVE,
    "otio_placement.channel_checks_s": RESOLVE,
    "otio_placement.imported_resolution_verify_s": RESOLVE,
    "otio_placement.linkage_repair_s": RESOLVE,
    "otio_placement.timeline_cursor_check_s": RESOLVE,
    # A graphics engine drawing pixels.
    "remotion_render": GRAPHICS,
    "hyperframes_render": GRAPHICS,
    "fframes_render": GRAPHICS,
    "graphics_render": GRAPHICS,
    "hyperframes-caption": GRAPHICS,
    "ffframes-caption": GRAPHICS,
}

# ── The families the punch list names ───────────────────────────────
#
# A family says WHAT KIND of cost a second was.  The resource says
# WHERE it went; the two axes are orthogonal and a second has one of
# each.

INFERENCE = "inference"
TRANSACTION_OVERHEAD = "transaction_overhead"
QUEUE_CONTENTION = "queue_contention"
CONTENT_WORK = "content_work"

FAMILIES = (INFERENCE, TRANSACTION_OVERHEAD, QUEUE_CONTENTION, CONTENT_WORK)

#: The layers whose seconds are a MODEL's work.
INFERENCE_LAYERS = frozenset({
    "gemma_inference", "semantic_inference_attempt", "model_load",
    "model_cache", "host_model", "mfa_align", "transcribe",
    "language_id", "computer_use_decide",
})

#: The layers whose seconds are the WAIT for the one Resolve.
CONTENTION_LAYERS = frozenset({"resolve_wait"})

#: The layers whose seconds are the TRANSACTION machinery: talking to
#: Resolve one call at a time, and keeping the books (the project
#: inventory, the marker fleet, the archive) honest around the change.
#:
#: This is the family PERF-012 names "transaction overhead", and it is
#: where a rewrite in Rust does NOT help: the seconds are not in Ren's
#: code, they are in round trips to another program and in reads nobody
#: asked for.
TRANSACTION_LAYERS = frozenset({
    # The round trips to the broker and to Resolve.
    "broker_request",
    "resolve_api_call",
    "resolve_snapshot_read",
    "resolve_mutation_batch",
    "resolve_connection_setup",
    "resolve_switch_settle_wait",
    "qualification_broker_job",
    "project_switch",
    "project_switch_load",
    "project_switch_settle",
    "project_switch_readback",
    "project_folder_capture",
    "project_save_before_borrow",
    "project_save_before_restore",
    "project_folder_restore",
    "project_timeline_restore",
    "project_restore",
    # The bookkeeping around a change: the staging copy, the promotion's
    # whole-project reads, the version store, the archive.
    "touchup_duplicate",
    "touchup_promote",
    "version_store",
    "archive_export",
    # The placement's per-item writes: each item's transform, grade and
    # Fusion comp is one API call at a time.
    "otio_placement.deferred_transform_s",
    "otio_placement.unattributed_build_work_s",
    "otio_placement.punch_in_s",
    "otio_placement.transform_overrides_and_replay_s",
    "otio_placement.overlay_sweep_readback_s",
    "otio_placement.linking_s",
    "otio_placement.grade_s",
    "otio_placement.fusion_comps_s",
    "otio_placement.ledger_retime_s",
    "otio_placement.row_occupancy_cleanup_s",
    "otio_placement.motion_coverage_s",
    "otio_placement.import_setup_s",
    "otio_placement.pool_organization_s",
    "otio_placement.placement_restore_readback_s",
    "otio_placement.source_markers_restore_s",
    "otio_placement.exclusive_context_wall_s",
    "otio_placement.mismatch_default_switch_s",
    "otio_placement.mismatch_default_restore_s",
    "otio_placement.mismatch_cursor_park_s",
    "otio_placement.mismatch_cursor_return_s",
    "otio_placement.mismatch_custom_size_s",
    "otio_placement.channel_checks_s",
    "otio_placement.imported_resolution_verify_s",
    "otio_placement.linkage_repair_s",
    "otio_placement.timeline_cursor_check_s",
})

#: The layers whose seconds are the EDIT ITSELF: the content work the
#: person asked for.  A layer not named in any family above is Ren's
#: own code preparing that work, and is content work by default.
CONTENT_LAYERS = frozenset({
    "reel_build_survey",
    "reel_build_plan",
    "reel_build_prepare",
    "reel_build_place",
    "reel_build_promote",
    "touchup_conform_compose_verify",
    "touchup_direct",
    "ffmpeg_encode",
    "fframes_render",
    "remotion_render",
    "hyperframes_render",
    "hyperframes-caption",
    "ffframes-caption",
    "graphics_render",
    "media_decode",
    "source_probe",
    "source_ingest",
    "demux",
    "diarize_embed",
    "reel_lifecycle",
    # The placement's own offline compile and the file it writes: Ren's
    # own work, not a round trip to Resolve.
    "otio_placement.offline_compile_s",
    "otio_placement.otio_file_write_s",
})

#: The lease frame is not a cost of its own: it is the window the work
#: happened in.  Its seconds are the work, so they are content work.
LEASE_FRAME_LAYERS = frozenset({"resolve_hold", "resolve_render"})


def bucket_of(layer: str) -> str:
    """The resource a layer's seconds belong to."""
    return BUCKET_BY_LAYER.get(str(layer), OWN_CODE)


#: The reel lifecycle's own phases, and the stage each one belongs to.
#: The `reel_lifecycle` span is a THIN wrapper that names its phase on
#: the row, so its stage is read from the row and not from the layer
#: name: one layer name, ten phases.
LIFECYCLE_PHASE_STAGE = {
    "planning": "planning",
    "model_access": "planning",
    "cache": "planning",
    "staging": "rendering",
    "resolve_ops": "axi",
    "verification": "verification",
    "promotion": "axi",
    "version_commits": "axi",
    "cleanup": "axi",
    "archive_export": "axi",
}


def stage_of_span(span: dict[str, Any]) -> str | None:
    """The stage a span's seconds belong to, or None when none claims it.

    A layer no stage claims is UNATTRIBUTED time, never folded into a
    stage that did not earn it.
    """
    layer = str(span.get("layer"))
    if layer == "reel_lifecycle":
        phase = str(span.get("phase") or "")
        return LIFECYCLE_PHASE_STAGE.get(phase)
    return perf_ledger.stage_of(layer)


def family_of(layer: str) -> str:
    """The kind of cost a layer's seconds are."""
    name = str(layer)
    if name in INFERENCE_LAYERS:
        return INFERENCE
    if name in CONTENTION_LAYERS:
        return QUEUE_CONTENTION
    if name in TRANSACTION_LAYERS:
        return TRANSACTION_OVERHEAD
    if name in CONTENT_LAYERS or name in LEASE_FRAME_LAYERS:
        return CONTENT_WORK
    return CONTENT_WORK


#: Below this share of the task's instrumented wall, no single limit
#: owns the task and the answer is `mixed`.  A quarter is chosen so a
#: task that is 30% inference and 30% transaction overhead reads as
#: what it is - both - instead of as whichever was measured first.
LIMIT_SHARE = 0.25

# ── The next optimization each measured component implies ───────────
#
# The punch list asks for the single next optimization each stage
# implies, with seconds - and for it to be SELECTED BY THE MEASUREMENT
# rather than assumed ("Rust or parallelism will automatically help" is
# the answer the punch list refuses).
#
# Each entry names the measured component, the action the measurement
# points at, and the lever it is.  The seconds are the MEASURED seconds
# that component held - an upper bound on what the action could remove,
# never a promise that it will.
#
# A component with no entry here is reported as the stage's cost with
# NO optimization claimed: the engine does not invent a next step for a
# measurement it has not explained.
NEXT_OPTIMIZATION: dict[str, dict[str, str]] = {
    "touchup_promote": {
        "action": "stop reading the whole project twice to promote one "
                  "clip: the promotion's inventory, row snapshots and "
                  "marker fleet are re-read for a one-property change",
        "lever": "the promotion's whole-project bookkeeping",
    },
    "touchup_conform_compose_verify": {
        "action": "the staging copy pays a full conform, composition and "
                  "verification re-read for a change the direct route "
                  "proves reversible",
        "lever": "the staged conform/compose/verify pass",
    },
    "touchup_duplicate": {
        "action": "duplicate only the tracks the edit touches, not the "
                  "whole reel",
        "lever": "the staging copy of the whole reel",
    },
    "touchup_direct": {
        "action": "the direct route's own cost: the router's qualification "
                  "and the write it proves",
        "lever": "the direct route's plan and write",
    },
    "resolve_hold": {
        "action": "the seconds inside the lease that no phase named: the "
                  "touch path's phases (the duplicate, the conform, the "
                  "promotion) are what fill this frame, so name them "
                  "before optimising anything inside it",
        "lever": "the lease frame's unnamed work",
    },
    "resolve_snapshot_read": {
        "action": "read the timeline once and reuse the reading: the "
                  "snapshot reads behind a write re-read what the write "
                  "already knows",
        "lever": "the snapshot read before the write",
    },
    "resolve_api_call": {
        "action": "batch the Resolve API calls: each call is a round trip "
                  "to the one Resolve",
        "lever": "the Resolve API round trips",
    },
    "resolve_mutation_batch": {
        "action": "the mutation batch: one commit for the whole edit",
        "lever": "the mutation batch",
    },
    "otio_placement.deferred_transform_s": {
        "action": "the placement writes each item's transform one API call "
                  "at a time: one call for the whole reel's transforms",
        "lever": "the placement's per-item transform replay",
    },
    "otio_placement.unattributed_build_work_s": {
        "action": "the placement's own unmeasured work: name the phases "
                  "inside it before optimising any of them",
        "lever": "the placement's unmeasured build work",
    },
    "otio_placement.fusion_comps_s": {
        "action": "the per-item Fusion comp pass inside the placement",
        "lever": "the placement's Fusion comp pass",
    },
    "otio_placement.linking_s": {
        "action": "the placement's per-item media linking",
        "lever": "the placement's linking pass",
    },
    "otio_placement.punch_in_s": {
        "action": "the placement's per-item punch-in write",
        "lever": "the placement's punch-in pass",
    },
    "otio_placement.grade_s": {
        "action": "the placement's per-item grade write",
        "lever": "the placement's grade pass",
    },
    "reel_build_place": {
        "action": "the placement pass that puts the reel on the timeline",
        "lever": "the placement pass",
    },
    "reel_build_plan": {
        "action": "the planning pass that compiles the placement plan",
        "lever": "the placement plan compilation",
    },
    "reel_build_survey": {
        "action": "the survey that reads the project before the build",
        "lever": "the build's project survey",
    },
    "reel_build_prepare": {
        "action": "the preparation pass that renders the reel's own assets",
        "lever": "the build's asset preparation",
    },
    "ffmpeg_encode": {
        "action": "the encode of the reel's rendered assets",
        "lever": "the asset encode",
    },
    "fframes_render": {
        "action": "the graphics render of the reel's cards",
        "lever": "the graphics render",
    },
    "gemma_inference": {
        "action": "the local model's own inference: fewer frames, or a "
                  "smaller prompt, buys the same seconds back",
        "lever": "the local VLM's inference",
    },
    "host_model": {
        "action": "the host model's own answer: a smaller exchange buys "
                  "the same seconds back",
        "lever": "the host model's answer",
    },
    "media_decode": {
        "action": "decode each source once: a second decode of the same "
                  "source is the same picture again",
        "lever": "the media decode",
    },
    "resolve_wait": {
        "action": "the wait for the one Resolve: the queue is the machine's, "
                  "so the work must not ask for the lease more than once",
        "lever": "the wait for the one Resolve",
    },
    "source_probe": {
        "action": "the probe of every source file",
        "lever": "the source probe",
    },
}

#: The optimization a stage falls back on when its dominant measured
#: component has no named entry: the resource itself, named honestly.
FALLBACK_OPTIMIZATION = {
    "action": "the measured component holds this stage's seconds and no "
              "lever is named for it: name the phases inside it before "
              "optimising any of them",
    "lever": "the stage's dominant measured component",
}


def _union(intervals: list[tuple]) -> list[tuple]:
    """The intervals merged, so overlapping time is counted once."""
    merged: list[tuple] = []
    for begin, finish in sorted(intervals):
        if merged and begin <= merged[-1][1]:
            if finish > merged[-1][1]:
                merged[-1] = (merged[-1][0], finish)
            continue
        merged.append((begin, finish))
    return merged


def _measure(intervals: list[tuple]) -> float:
    """Seconds covered by an interval set."""
    return perf_ledger.union_s(intervals)


def _overlap_seconds(left: list[tuple], right: list[tuple]) -> float:
    """Seconds covered by BOTH interval sets."""
    total = 0.0
    for begin, finish in left:
        for other_begin, other_end in right:
            start = max(begin, other_begin)
            end = min(finish, other_end)
            if end > start:
                total += end - start
    return total


def _coverage(row: dict[str, Any]) -> list[tuple]:
    """The seconds a span covers: its own gaps when it measured them.

    A `span()` context manager records `self_intervals` - the gaps
    between the spans it contained - so its coverage is already its own
    seconds and the children are excluded.  A span recorded as a leaf
    covers its whole wall.
    """
    return perf_ledger.span_intervals(row)


def _attribute(spans: list[dict[str, Any]]) -> dict[int, float]:
    """Give every measured second to exactly ONE span: the narrowest.

    The ledger's rows nest: a container span holds the spans it
    contains, so a naive sum charges the same second twice and the
    totals exceed the wall they describe.  A sweep over the measured
    intervals settles it: at every instant the covering spans are
    ranked by how NARROWLY they cover that instant - the span whose own
    interval around it is the shortest is the most specific
    measurement of that second, and it owns the second.

    Two spans covering exactly the same interval are siblings, not
    parent and child; the later-recorded one wins, so the second is
    still counted once.
    """
    coverage = [_coverage(span) for span in spans]
    boundaries = sorted({point for intervals in coverage for pair in intervals
                         for point in pair})
    owned: dict[int, float] = {}
    for index in range(len(boundaries) - 1):
        begin, finish = boundaries[index], boundaries[index + 1]
        middle = (begin + finish) / 2
        candidates: list[tuple[float, int]] = []
        for position, intervals in enumerate(coverage):
            for start, end in intervals:
                if start <= middle < end:
                    candidates.append((end - start, position))
                    break
        if not candidates:
            continue
        winner = min(candidates, key=lambda item: (item[0], -item[1]))[1]
        owned[winner] = owned.get(winner, 0.0) + (finish - begin)
    return owned


def _task_row(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """The task row that closed this run, if the run opened one."""
    for row in rows:
        if (row.get("kind") == perf_ledger.TASK
                and row.get("state") == "completed"
                and row.get("run_id") == run_id):
            return dict(row)
    return {}


def _run_window(rows: list[dict[str, Any]], run_id: str
                ) -> tuple[float, float] | None:
    """The run's own wall window: when it started and when it ended."""
    stamps = []
    for row in rows:
        if row.get("run_id") != run_id:
            continue
        begin = row.get("started_at")
        wall = row.get("wall_s")
        if begin is None or wall is None:
            continue
        try:
            stamps.append((float(begin), float(begin) + float(wall)))
        except (TypeError, ValueError):
            continue
    if not stamps:
        return None
    return (min(begin for begin, _end in stamps),
            max(end for _begin, end in stamps))


def queue_holders(since: float, until: float) -> list[dict[str, Any]]:
    """What held the one Resolve while this run waited for it.

    The broker's own receipts say which job held the instance for each
    waiting second (`library/tools/resolved/kpi.py`, PR 3087's wait
    attribution).  This reads them READ-ONLY - the job table is opened
    `mode=ro` and never through `JobStore`, whose constructor closes a
    live broker's open jobs as failed.

    A machine with no broker receipts returns an empty list, and the
    report says the attribution is unmeasured rather than inventing a
    holder.
    """
    try:
        from library.tools.resolved.kpi import receipts_since, wait_causes
        from library.tools.resolved.server import db_path
        receipts = receipts_since(db_path(), since, until)
    except Exception:  # noqa: BLE001 - attribution never fails a report
        return []
    if not receipts:
        return []
    return wait_causes(receipts, until)


def analyse_run(rows: list[dict[str, Any]], run_id: str) -> dict[str, Any]:
    """One run's critical path: the stages, their resources, the limit.

    `rows` is every ledger row; `run_id` names the run.  The answer
    carries the DENOMINATOR of every share: the task's instrumented
    wall, and the person-visible latency beside it, so a reader can see
    how much of the wait the instrumented work even covers.
    """
    spans = [r for r in rows
             if r.get("run_id") == run_id and r.get("kind") == perf_ledger.SPAN
             and str(r.get("layer")) not in perf_ledger.SUMMARY_LAYERS]
    caps = [r for r in rows
            if r.get("run_id") == run_id and r.get("kind") == perf_ledger.CAPABILITY]
    task = _task_row(rows, run_id)

    owned = _attribute(spans)
    stage_intervals: dict[str, list[tuple]] = {}
    stage_layers: dict[str, set[str]] = {}
    layer_seconds: dict[str, float] = {}
    layer_stage: dict[str, str] = {}
    bucket_seconds: dict[str, float] = {}
    family_seconds: dict[str, float] = {}
    bucket_cpu_s: dict[str, float] = {}
    stage_bucket_seconds: dict[str, dict[str, float]] = {}

    for index, span in enumerate(spans):
        seconds = owned.get(index, 0.0)
        if seconds <= 0:
            continue
        layer = str(span.get("layer"))
        stage = stage_of_span(span) or "unattributed"
        bucket = bucket_of(layer)
        family = family_of(layer)
        layer_seconds[layer] = layer_seconds.get(layer, 0.0) + seconds
        layer_stage.setdefault(layer, stage)
        stage_layers.setdefault(stage, set()).add(layer)
        stage_intervals.setdefault(stage, []).extend(_coverage(span))
        bucket_seconds[bucket] = bucket_seconds.get(bucket, 0.0) + seconds
        family_seconds[family] = family_seconds.get(family, 0.0) + seconds
        stage_bucket_seconds.setdefault(stage, {})
        stage_bucket_seconds[stage][bucket] = (
            stage_bucket_seconds[stage].get(bucket, 0.0) + seconds)
        bucket_cpu_s[bucket] = bucket_cpu_s.get(bucket, 0.0) + float(
            span.get("cpu_s") or 0.0)

    merged_stage = {stage: _union(intervals)
                    for stage, intervals in stage_intervals.items()}
    wall_s = sum(layer_seconds.values())
    capability_wall_s = sum(float(c.get("wall_s") or 0.0) for c in caps)

    stages = []
    for stage in sorted(merged_stage):
        own = merged_stage[stage]
        own_s = _measure(own)
        shared_with: dict[str, float] = {}
        shared_total = 0.0
        for other, other_intervals in merged_stage.items():
            if other == stage:
                continue
            seconds = _overlap_seconds(own, other_intervals)
            if seconds > 0.005:
                shared_with[other] = round(seconds, 3)
                shared_total += seconds
        shared_total = min(shared_total, own_s)
        exclusive_s = max(0.0, own_s - shared_total)
        stage_seconds = sum(
            seconds for layer, seconds in layer_seconds.items()
            if layer_stage.get(layer) == stage)
        bucket_rows = [
            {"resource": bucket,
             "seconds": round(seconds, 3),
             "share_pct": (round(100.0 * seconds / stage_seconds, 1)
                           if stage_seconds else None),
             "cpu_s": round(bucket_cpu_s.get(bucket, 0.0), 3)}
            for bucket, seconds in stage_bucket_seconds.get(stage, {}).items()]
        bucket_rows.sort(key=lambda row: -row["seconds"])
        bottleneck = bucket_rows[0] if bucket_rows else None
        layer_rows = sorted(
            ({"layer": layer, "seconds": round(seconds, 3),
              "resource": bucket_of(layer), "family": family_of(layer)}
             for layer, seconds in layer_seconds.items()
             if layer_stage.get(layer) == stage),
            key=lambda row: -row["seconds"])
        dominant = layer_rows[0] if layer_rows else None
        optimization = None
        if dominant:
            named = NEXT_OPTIMIZATION.get(str(dominant["layer"]))
            optimization = {
                "layer": dominant["layer"],
                "seconds": dominant["seconds"],
                "share_pct": (round(100.0 * dominant["seconds"] / stage_seconds, 1)
                              if stage_seconds else None),
                "action": (named or FALLBACK_OPTIMIZATION)["action"],
                "lever": (named or FALLBACK_OPTIMIZATION)["lever"],
                "named_lever": named is not None,
            }
        role = ("overlapped" if shared_total > 0.005
                else ("serial" if own_s > 0 else "unmeasured"))
        stages.append({
            "stage": stage,
            "role": role,
            "wall_s": round(own_s, 3),
            "measured_s": round(stage_seconds, 3),
            "share_pct": (round(100.0 * stage_seconds / wall_s, 1) if wall_s else None),
            "exclusive_s": round(exclusive_s, 3),
            "shared_s": round(shared_total, 3),
            "shared_with": shared_with,
            "bottleneck": bottleneck,
            "next_optimization": optimization,
            "resources": bucket_rows,
            "layers": layer_rows,
        })

    # The serial spine: the stages that could not start before the one
    # before them finished.  Two stages are serial with respect to each
    # other when they never overlap in time and one ends first.
    edges = []
    names = sorted(merged_stage)
    for index, before in enumerate(names):
        for after in names[index + 1:]:
            left = merged_stage[before]
            right = merged_stage[after]
            if _overlap_seconds(left, right) > 0.005:
                continue
            left_end = max(end for _begin, end in left)
            right_start = min(begin for begin, _end in right)
            if left_end <= right_start:
                edges.append({"before": before, "after": after,
                              "gap_s": round(right_start - left_end, 3)})
            else:
                edges.append({"before": after, "after": before,
                              "gap_s": round(
                                  min(begin for begin, _end in left)
                                  - max(end for _begin, end in right), 3)})

    # The limit: which of the three named families owns the task.
    #
    # PERF-012 names three: inference, transaction overhead and queue
    # contention.  When none of them owns a quarter of the wall, the
    # task is limited by its OWN work - and the report says that
    # rather than picking the nearest of the three.
    candidates = {family: family_seconds.get(family, 0.0)
                  for family in FAMILIES}
    named = {name: candidates[name] for name in
             (INFERENCE, TRANSACTION_OVERHEAD, QUEUE_CONTENTION)}
    limit = "own_work"
    limit_seconds = candidates.get(CONTENT_WORK, 0.0)
    if wall_s > 0:
        ranked = sorted(named.items(), key=lambda item: -item[1])
        top_name, top_seconds = ranked[0]
        if top_seconds > 0 and top_seconds / wall_s >= LIMIT_SHARE:
            limit = top_name
            limit_seconds = top_seconds
        elif candidates.get(CONTENT_WORK, 0.0) < LIMIT_SHARE * wall_s:
            limit = "mixed"
    elif not spans:
        limit = "unmeasured"

    waits = perf_report.waits(rows, run_id)
    window = _run_window(rows, run_id)
    holders: list[dict[str, Any]] = []
    if window and candidates.get(QUEUE_CONTENTION, 0.0) > 0:
        holders = queue_holders(window[0], window[1])
    latency = task.get("latency_s")
    return {
        "run_id": run_id,
        "task_class": task.get("task_class"),
        "outcome": task.get("outcome"),
        "latency_s": float(latency) if latency is not None else None,
        "instrumented_wall_s": round(wall_s, 3),
        "capability_wall_s": round(capability_wall_s, 3),
        "uninstrumented_s": round(
            max(0.0, (float(latency) if latency is not None
                      else capability_wall_s) - wall_s), 3),
        "stages": stages,
        "serial_edges": edges,
        "resources": [
            {"resource": bucket,
             "seconds": round(seconds, 3),
             "share_pct": (round(100.0 * seconds / wall_s, 1) if wall_s else None)}
            for bucket, seconds in sorted(bucket_seconds.items(),
                                          key=lambda item: -item[1])],
        "families": [
            {"family": family,
             "seconds": round(seconds, 3),
             "share_pct": (round(100.0 * seconds / wall_s, 1) if wall_s else None)}
            for family, seconds in sorted(family_seconds.items(),
                                          key=lambda item: -item[1])],
        "limit": {
            "name": limit,
            "seconds": round(limit_seconds, 3),
            "share_pct": (round(100.0 * limit_seconds / wall_s, 1)
                          if wall_s else None),
            "candidates": {name: round(seconds, 3)
                           for name, seconds in candidates.items()},
        },
        "waits": waits,
        "queue_holders": holders,
        "unattributed_layers": sorted(
            layer for layer in layer_seconds
            if stage_of_span({"layer": layer}) is None),
    }


def runs_of_class(rows: list[dict[str, Any]], task_class: str
                  ) -> list[str]:
    """The run ids of every COMPLETED task of this class, oldest first."""
    found = []
    for row in rows:
        if (row.get("kind") == perf_ledger.TASK
                and row.get("state") == "completed"
                and str(row.get("task_class")) == str(task_class)):
            run_id = str(row.get("run_id") or "")
            if run_id and run_id not in found:
                found.append(run_id)
    return found


def summarise_class(rows: list[dict[str, Any]], task_class: str) -> dict[str, Any]:
    """Every run of one task class, read as a critical path.

    The DENOMINATOR is here: how many runs of this class the ledger
    holds, how many of them produced an instrumented critical path, and
    how many finished.  A class whose runs were all refused is reported
    as refused - never as a class with no cost.
    """
    run_ids = runs_of_class(rows, task_class)
    per_run = [analyse_run(rows, run_id) for run_id in run_ids]
    finished = [r for r in per_run if str(r.get("outcome") or "") == "ok"]
    instrumented = [r for r in per_run
                    if float(r.get("instrumented_wall_s") or 0.0) > 0]
    limits: dict[str, int] = {}
    for report in per_run:
        name = str((report.get("limit") or {}).get("name") or "unmeasured")
        limits[name] = limits.get(name, 0) + 1
    latencies = sorted(float(r["latency_s"]) for r in per_run
                       if r.get("latency_s") is not None)
    walls = sorted(float(r["instrumented_wall_s"]) for r in per_run
                   if float(r.get("instrumented_wall_s") or 0.0) > 0)
    return {
        "task_class": task_class,
        "runs": len(run_ids),
        "finished": len(finished),
        "instrumented": len(instrumented),
        "limits": limits,
        "latency_s": {
            "p50": perf_ledger.quantile(latencies, 0.5),
            "p95": perf_ledger.quantile(latencies, 0.95),
            "max": round(latencies[-1], 3) if latencies else None,
        },
        "instrumented_wall_s": {
            "p50": perf_ledger.quantile(walls, 0.5),
            "p95": perf_ledger.quantile(walls, 0.95),
            "max": round(walls[-1], 3) if walls else None,
        },
        "run_ids": run_ids,
        "per_run": per_run,
    }


def render(report: dict[str, Any]) -> str:
    """The report as text, for a person reading a terminal."""
    out: list[str] = []
    if "task_class" in report and "per_run" in report:
        out.append(f"Task class {report['task_class']}: "
                   f"{report['runs']} run(s), {report['finished']} finished, "
                   f"{report['instrumented']} instrumented")
        limits = ", ".join(f"{name} x{count}"
                           for name, count in sorted(report["limits"].items()))
        out.append(f"  limits seen: {limits or 'none'}")
        out.append(f"  latency p50 {report['latency_s']['p50']}s, "
                   f"p95 {report['latency_s']['p95']}s, "
                   f"max {report['latency_s']['max']}s")
        out.append(f"  instrumented wall p50 "
                   f"{report['instrumented_wall_s']['p50']}s, "
                   f"p95 {report['instrumented_wall_s']['p95']}s")
        out.append("")
        for run in report["per_run"]:
            out.extend(render_run(run))
        return "\n".join(out)
    out.extend(render_run(report))
    return "\n".join(out)


def render_run(report: dict[str, Any]) -> list[str]:
    """One run's critical path, as text."""
    out = [(f"Run {report.get('run_id')} "
            f"({report.get('task_class') or 'no task row'}, "
            f"outcome {report.get('outcome') or 'unrecorded'})")]
    latency = report.get("latency_s")
    out.append(f"  person waited "
               f"{(f'{latency:.1f}s') if latency is not None else 'unmeasured'}"
               f"; instrumented wall "
               f"{report.get('instrumented_wall_s', 0):.1f}s"
               f"; uninstrumented {report.get('uninstrumented_s', 0):.1f}s")
    out.append(f"  limit: {report['limit']['name']} "
               f"{report['limit']['seconds']:.1f}s "
               f"({report['limit']['share_pct']}%)")
    for family in report.get("families") or []:
        out.append(f"    family {family['family']:<22} "
                   f"{family['seconds']:>8.1f}s {family['share_pct']}%")
    for stage in report["stages"]:
        bottleneck = stage.get("bottleneck") or {}
        out.append(
            f"    {stage['stage']:<14} {stage['wall_s']:>8.1f}s "
            f"{stage['role']:<10} exclusive {stage['exclusive_s']:>7.1f}s "
            f"shared {stage['shared_s']:>7.1f}s  "
            f"bottleneck {bottleneck.get('resource', 'unmeasured')} "
            f"{bottleneck.get('seconds', 0):.1f}s")
        for partner, seconds in (stage.get("shared_with") or {}).items():
            out.append(f"        shares time with {partner}: {seconds:.1f}s")
        optimization = stage.get("next_optimization")
        if optimization:
            out.append(f"        next optimization ({optimization['lever']}, "
                       f"{optimization['seconds']:.1f}s): "
                       f"{optimization['action']}")
    for holder in (report.get("queue_holders") or [])[:5]:
        out.append(f"    the queue was held by {holder['kind']} "
                   f"({holder['description']}): {holder['blocked_s']:.1f}s")
    if report.get("unattributed_layers"):
        out.append("    layers no stage claims: "
                   + ", ".join(report["unattributed_layers"]))
    return out


def _resolve_project(ref: str) -> str:
    """A project slug or path, resolved the way `ren profile` resolves one."""
    from library.tools.project_registry import get_project, resolve_project_path

    found = resolve_project_path(ref)
    if found is not None:
        return str(found.parent)
    config = get_project(ref)
    return str(config._project_root)


def main(argv: list[str] | None = None) -> int:
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren profile --critical-path",
        description="The critical path of a run: which stages are serial, "
                    "which resource each stage is limited by, and whether "
                    "the task is limited by inference, transaction overhead "
                    "or queue contention. Reads the performance ledger; "
                    "changes nothing.")
    parser.add_argument("project", nargs="?", help="a project slug or path")
    parser.add_argument("--run", help="a run id (default: the latest)")
    parser.add_argument("--ledger", help="read a standalone perf ledger JSONL path")
    parser.add_argument("--task-class",
                        help="every run of this task class, read the same way")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not args.project and not args.ledger:
        parser.error("provide a project or --ledger")
    project = (_resolve_project(args.project) if args.ledger is None
               else args.project or "")
    rows = perf_ledger.read_rows(project, args.ledger)
    if not rows:
        print("No performance rows to read.", file=sys.stderr)
        return 1
    if args.task_class:
        report = summarise_class(rows, args.task_class)
    else:
        ids = perf_ledger.run_ids(rows)
        if not ids:
            print("No run ids in this ledger.", file=sys.stderr)
            return 1
        run_id = args.run or ids[-1]
        if run_id not in ids:
            print(f"No run {run_id!r} in this ledger; `--list` shows what is there.",
                  file=sys.stderr)
            return 1
        report = analyse_run(rows, run_id)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
