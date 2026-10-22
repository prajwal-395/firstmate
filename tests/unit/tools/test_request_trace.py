"""One trace per request, with nothing silently lost.

Every test here names the defect it catches. A trace is read to answer
"where did this request's time go" - a second that vanishes, or that
lands in a stage that did not earn it, is believed rather than asked
for.
"""

from library.tools import perf_ledger, request_trace


def _capability(capability, wall, started_at, run_id="r1"):
    return {"kind": perf_ledger.CAPABILITY, "run_id": run_id,
            "capability": capability, "started_at": started_at,
            "wall_s": wall, "cpu_s": wall / 2}


def _span(layer, wall, started_at, capability="edit", run_id="r1",
          **fields):
    return {"kind": perf_ledger.SPAN, "layer": layer, "run_id": run_id,
            "capability": capability, "started_at": started_at,
            "wall_s": wall, "cpu_s": wall / 4, **fields}


def _full_request():
    """One request touching every M01 stage that has a ledger layer."""
    return [
        _capability("edit", 100.0, 0.0),
        _span("source_probe", 4.0, 1.0),
        _span("media_decode", 6.0, 5.0,
              media_kind="video", decode_purpose="frame_exact.dense_sample"),
        _span("transcribe", 5.0, 11.0),
        _span("gemma_inference", 10.0, 16.0),
        _span("footage_index_build", 4.0, 26.0),
        _span("footage_search", 8.0, 30.0),
        _span("host_model", 6.0, 38.0),
        _span("resolve_wait", 5.0, 44.0),
        _span("resolve_hold", 12.0, 49.0, held_s=12.0),
        _span("render_qa", 6.0, 61.0),
        _span("model_load", 4.0, 67.0, model="gemma-4"),
    ]


# ── M01: every second is named or reported ──────────────────────────

def test_named_stages_unclassified_and_unattributed_add_up_to_the_wall():
    """A lost second is a second nobody can account for.

    Defect caught: stage shares that add to less than the run's wall
    with no remainder reported, so the trace reads complete while
    seconds are missing.
    """
    rows = _full_request()

    trace = request_trace.trace_for_run(rows, "r1")

    named = sum(s["wall_s"] for s in trace["stages"])
    accounted = named + trace["unclassified_s"] + trace["unattributed_s"]
    assert accounted == trace["request"]["total_wall_s"] == 100.0
    assert trace["unclassified_s"] == 0.0


def test_a_layer_no_request_stage_claims_is_reported_not_folded():
    """Unclassified time is named, not charged to a stage that did not
    earn it.

    Defect caught: a newly added span layer bucketed into the nearest
    M01 stage, so the trace reports planning time nobody spent.
    """
    rows = [
        _capability("edit", 10.0, 0.0),
        _span("a_layer_nobody_placed", 4.0, 1.0),
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    assert trace["unclassified_s"] == 4.0
    assert trace["unclassified_layers"] == ["a_layer_nobody_placed"]
    assert all(s["wall_s"] == 0.0 for s in trace["stages"])
    assert trace["unattributed_s"] == 6.0


def test_capability_wall_outside_every_span_is_not_invented_into_a_stage():
    """The remainder names its capability instead of guessing a stage.

    Defect caught: span-less front-door wall (a catalogue read, a
    refused lease) attributed to the nearest stage, so a reader
    optimises a stage that never ran.
    """
    rows = [
        _capability("footage.scan", 5.0, 0.0),
        _capability("edit", 10.0, 5.0),
        _span("footage_search", 4.0, 6.0, capability="edit"),
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    by_cap = {r["capability"]: r
              for r in trace["unattributed_by_capability"]}
    assert by_cap["footage.scan"]["remainder_s"] == 5.0
    assert by_cap["edit"]["remainder_s"] == 6.0
    assert trace["unattributed_s"] == 11.0


def test_audio_decode_is_not_charged_to_frame_extraction():
    """The same decode layer serves two stages; the row says which.

    Defect caught: every `media_decode` span charged to frame
    extraction, so an audio-heavy request reads as a video-decode
    bottleneck - the generic attribution M01 exists to end.
    """
    rows = [
        _capability("edit", 20.0, 0.0),
        _span("media_decode", 6.0, 1.0, media_kind="video",
              decode_purpose="frame_exact.dense_sample"),
        _span("media_decode", 5.0, 7.0, media_kind="audio",
              decode_purpose="temporal.audio_16k_mono"),
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    by_stage = {s["stage"]: s for s in trace["stages"]}
    assert by_stage["source_decode"]["wall_s"] == 6.0
    assert by_stage["audio_speech"]["wall_s"] == 5.0


def test_an_audio_purpose_without_a_media_kind_still_reads_as_audio():
    """Older decode rows carry the purpose but no media kind.

    Defect caught: purpose-only rows falling back to frame extraction,
    mischarging the speech analysis of every run recorded before the
    kind was added.
    """
    rows = [
        _capability("edit", 10.0, 0.0),
        _span("media_decode", 5.0, 1.0,
              decode_purpose="temporal.audio_16k_mono"),
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    by_stage = {s["stage"]: s for s in trace["stages"]}
    assert by_stage["audio_speech"]["wall_s"] == 5.0
    assert by_stage["source_decode"]["wall_s"] == 0.0


def test_summary_rows_are_not_charged_twice():
    """A summary of other rows is not a stage of its own.

    Defect caught: `reel_edit` (the reel's own accounting of its cost)
    charged as execution time on top of the phases it summarises, so
    the trace exceeds the wall it describes.
    """
    rows = _full_request() + [
        {"kind": perf_ledger.SPAN, "layer": "reel_edit", "run_id": "r1",
         "capability": "edit", "started_at": 0.0, "wall_s": 100.0,
         "reel": "reel-01", "measurements": {"total_s": 100.0}},
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    named = sum(s["wall_s"] for s in trace["stages"])
    assert named + trace["unattributed_s"] == 100.0


# ── M01: the cost vector, separately ────────────────────────────────

def test_ingestion_and_per_request_are_disjoint_and_cover_the_stages():
    """The two halves share no stage and lose no stage.

    Defect caught: a stage counted on both sides of the
    ingestion/per-request split, so the split adds past the wall it
    divides.
    """
    trace = request_trace.trace_for_run(_full_request(), "r1")

    ingestion = trace["ingestion"]
    per_request = trace["per_request"]
    assert set(ingestion["stages"]) | set(per_request["stages"]) == set(
        request_trace.REQUEST_STAGES)
    assert not (set(ingestion["stages"]) & set(per_request["stages"]))
    assert round(
        ingestion["wall_s"] + per_request["wall_s"], 3
    ) == trace["attributed_s"]


def test_gpu_without_a_counter_is_unmeasured_not_zero():
    """Nobody measured the GPU, so the trace must not price it at zero.

    Defect caught: `gpu_s: 0.0` reading as "the model cost no GPU",
    which a later optimisation would cite as measured.
    """
    trace = request_trace.trace_for_run(_full_request(), "r1")

    assert trace["compute"]["gpu_s"] is None
    assert trace["compute"]["gpu_note"]


def test_cache_without_a_count_is_unmeasured_not_zero():
    """A request whose spans carried no cache counts has no cache
    measurement.

    Defect caught: `hits: 0` reading as "nothing was served from the
    cache", when no span said either way.
    """
    trace = request_trace.trace_for_run(_full_request(), "r1")

    assert trace["cache"]["measured"] is False
    assert trace["cache"]["hits"] is None


def test_cache_counts_are_kept_when_spans_carry_them():
    """Spans that measured the cache land in the trace.

    Defect caught: the trace dropping the hit/miss the decode rows
    already record, so a cached request reads the same as a cold one.
    """
    rows = [
        _capability("edit", 10.0, 0.0),
        _span("media_decode", 5.0, 1.0, media_kind="video",
              decode_purpose="shared_decode.sampled_frames",
              cache_hits=3, cache_misses=1),
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    assert trace["cache"]["measured"] is True
    assert trace["cache"]["hits"] == 3
    assert trace["cache"]["misses"] == 1


def test_cold_and_warm_inference_stay_separate():
    """The load, the first inference and the later ones are three
    numbers.

    Defect caught: the model's warm-up folded into one "model time",
    hiding whether the request paid for the weights or for answers.
    """
    rows = [
        _capability("edit", 30.0, 0.0),
        _span("model_load", 4.0, 1.0, model="gemma-4"),
        _span("gemma_inference", 6.0, 5.0, cold_start=True),
        _span("gemma_inference", 8.0, 11.0, cold_start=False),
    ]

    trace = request_trace.trace_for_run(rows, "r1")

    model = trace["model"]
    assert model["model_load_s"] == 4.0
    assert model["cold_inference_s"] == 6.0
    assert model["cold_inferences"] == 1
    assert model["warm_inference_s"] == 8.0
    assert model["warm_inferences"] == 1


def test_ranking_verification_with_no_layer_is_unmeasured_not_free():
    """No ledger layer separates candidate ranking yet; the trace says
    so.

    Defect caught: the stage reading `wall_s: 0.0` with spans and
    earning a "ranking is free" citation. The zero has no spans behind
    it, which is what makes it unmeasured rather than free.
    """
    trace = request_trace.trace_for_run(_full_request(), "r1")

    stage = next(s for s in trace["stages"]
                 if s["stage"] == "ranking_verification")
    assert stage["spans"] == 0


# ── M01: one task inside the run ────────────────────────────────────

def _task(task_id, requested_at, completed_at, run_id="r1",
          task_class="axi.trim", outcome="ok"):
    return {"kind": perf_ledger.TASK, "run_id": run_id,
            "task_id": task_id, "task_class": task_class,
            "state": "completed", "outcome": outcome,
            "requested_at": requested_at, "completed_at": completed_at,
            "latency_s": round(completed_at - requested_at, 3)}


def test_a_task_window_narrows_the_trace_to_what_it_waited_through():
    """A task-scoped trace counts the task's window, not the run's.

    Defect caught: the task id accepted but ignored, so a "per-request"
    trace prices the whole run - every concurrent lane with it.
    """
    rows = [
        _capability("trim", 10.0, 0.0),
        _span("resolve_wait", 8.0, 1.0, capability="trim"),
        _capability("other", 50.0, 100.0),
        _span("gemma_inference", 40.0, 101.0, capability="other"),
        _task("t1", 0.0, 10.0),
    ]

    trace = request_trace.trace_for_run(rows, "r1", task_id="t1")

    assert trace["request"]["task_scoped"] is True
    assert trace["request"]["total_wall_s"] == 10.0
    by_stage = {s["stage"]: s for s in trace["stages"]}
    assert by_stage["resolve_queue"]["wall_s"] == 8.0
    assert by_stage["semantic_events"]["wall_s"] == 0.0


def test_a_task_without_a_recorded_window_stays_run_wide_and_says_so():
    """Narrowing to half a window would lose seconds.

    Defect caught: a windowless task silently producing a run-wide
    trace that reads as task-scoped.
    """
    rows = [
        _capability("trim", 10.0, 0.0),
        {"kind": perf_ledger.TASK, "run_id": "r1", "task_id": "t9",
         "task_class": "axi.trim", "state": "requested",
         "requested_at": 0.0},
    ]

    trace = request_trace.trace_for_run(rows, "r1", task_id="t9")

    assert trace["request"]["task_scoped"] is False
    assert trace["request"]["total_wall_s"] == 10.0
