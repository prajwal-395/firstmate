"""What the performance profile says, and what it refuses to guess.

Every test here names the defect it catches. A profile is read by a
person deciding where to spend the next hour, so a number that is
quietly wrong costs more than a missing one: the missing number is
asked for, the wrong one is believed.
"""

from library.tools import perf_ledger, perf_report


def _span(layer, wall, started_at, **fields):
    return {"kind": perf_ledger.SPAN, "layer": layer, "run_id": "r1",
            "capability": "reel.build", "started_at": started_at,
            "wall_s": wall, **fields}


def _capability(wall, started_at, capability="reel.build"):
    return {"kind": perf_ledger.CAPABILITY, "run_id": "r1",
            "capability": capability, "started_at": started_at,
            "wall_s": wall, "cpu_s": wall / 2}


def _reel_edit(reel, verified, total_s=10.0):
    return {"kind": perf_ledger.SPAN, "layer": "reel_edit", "run_id": "r1",
            "capability": "reel.build", "started_at": 100.0,
            "wall_s": total_s, "reel": reel,
            "measurements": {"total_s": total_s, "cpu_s": 1.0,
                             "verified": verified}}


# ── PERF-001: the stages of an edit ────────────────────────────────

def test_a_layer_no_stage_claims_is_not_charged_to_a_stage():
    """Unclassified time is named, not folded into a stage that did not earn it.

    Defect caught: a newly added span layer bucketed into the nearest
    stage makes the stage table report a stage that never spent that
    time - and the reader trusts it.
    """
    rows = [
        _capability(10.0, 0.0),
        _span("a_layer_nobody_placed", 4.0, 1.0),
    ]

    report = perf_report.stage_breakdown(rows, "r1")

    assert report["unclassified_s"] == 4.0
    assert all(stage["wall_s"] == 0.0 for stage in report["stages"])
    assert "a_layer_nobody_placed" in report["unclassified_layers"]


def test_the_placement_pass_is_charged_to_the_stage_it_is():
    """The OTIO placement pass is the AXI stage, and it is NOT silent.

    Defect caught: the placement pass wrote eleven span layers that no
    stage claimed, so a live reel build (measured 2026-10-20) reported
    40-46% of its wall as unattributed time - the exact silence the
    stage vocabulary exists to end. A reader deciding where to spend
    the next hour saw a build with no visible biggest cost.
    """
    rows = [
        _capability(100.0, 0.0),
        _span("otio_placement.deferred_transform_s", 40.0, 1.0),
        _span("otio_placement.unattributed_build_work_s", 5.0, 41.0),
    ]

    report = perf_report.stage_breakdown(rows, "r1")

    axi = next(stage for stage in report["stages"]
               if stage["stage"] == "axi")
    assert axi["wall_s"] == 45.0
    assert report["unclassified_s"] == 0.0
    assert report["unattributed_s"] == 55.0


def test_two_spans_of_one_stage_that_overlap_count_once():
    """A stage is charged the time it COVERED, not the sum of its spans.

    Defect caught: two spans of one stage overlapping in time counted
    twice, so the stage shares add past 100% and no longer answer
    "where did the time go".
    """
    rows = [
        _capability(10.0, 0.0),
        _span("gemma_inference", 4.0, 1.0),
        _span("gemma_inference", 4.0, 3.0),
    ]

    report = perf_report.stage_breakdown(rows, "r1")

    planning = next(s for s in report["stages"] if s["stage"] == "planning")
    assert planning["wall_s"] == 6.0


def test_the_run_wall_no_stage_claimed_is_reported():
    """The wall between the capabilities is named, not hidden in a stage.

    Defect caught: the time a run spent outside every measured layer
    reading as zero, so a profile looks complete while most of the run
    is unattributed.
    """
    rows = [
        _capability(10.0, 0.0),
        _span("gemma_inference", 2.0, 1.0),
    ]

    report = perf_report.stage_breakdown(rows, "r1")

    assert report["unattributed_s"] == 8.0


# ── PERF-002: the latency the person waited ────────────────────────

def test_a_task_reports_the_wait_no_capability_row_explains():
    """The task's latency is the wall clock; the capabilities are a part of it.

    Defect caught: reporting the task latency as the sum of the
    capability walls, which hides the lease queue, the project switch
    and the gaps between capabilities - exactly the wait the person
    complained about.
    """
    rows = [
        {"kind": perf_ledger.TASK, "state": "requested", "task_id": "t1",
         "task_class": "reels.build", "run_id": "r1", "requested_at": 0.0},
        {"kind": perf_ledger.TASK, "state": "completed", "task_id": "t1",
         "task_class": "reels.build", "run_id": "r1", "requested_at": 0.0,
         "completed_at": 30.0, "latency_s": 30.0, "outcome": "ok"},
        _capability(10.0, 1.0),
    ]

    report = perf_report.task_latency(rows, "r1")

    assert report["count"] == 1
    task = report["tasks"][0]
    assert task["latency_s"] == 30.0
    assert task["capability_s"] == 10.0
    assert task["unattributed_s"] == 20.0


def test_a_task_that_never_completed_is_reported_as_open():
    """A task with no completion is OPEN, never a zero-latency task.

    Defect caught: a build that crashed after the request reading as
    "the task took no time", which hides the failure behind a fast
    number.
    """
    rows = [
        {"kind": perf_ledger.TASK, "state": "requested", "task_id": "t1",
         "task_class": "reels.build", "run_id": "r1", "requested_at": 0.0},
    ]

    report = perf_report.task_latency(rows, "r1")

    assert report["tasks"] == []
    assert report["open_task_ids"] == ["t1"]


# ── PERF-001: the split an operation's wall is made of ────────────

def _task_rows(task_id, task_class, start, end, outcome="ok"):
    return [
        {"kind": perf_ledger.TASK, "state": "requested", "task_id": task_id,
         "task_class": task_class, "run_id": "r1", "requested_at": start},
        {"kind": perf_ledger.TASK, "state": "completed", "task_id": task_id,
         "task_class": task_class, "run_id": "r1", "requested_at": start,
         "completed_at": end, "latency_s": end - start, "outcome": outcome},
    ]


def _lease(layer, started_at, seconds, **fields):
    row = {"kind": perf_ledger.SPAN, "layer": layer, "run_id": "r1",
           "capability": "axi.property_change", "started_at": started_at,
           "wall_s": seconds, **fields}
    if layer == perf_ledger.RESOLVE_HOLD:
        row["held_s"] = seconds
    return row


def test_an_operation_is_split_into_queue_wait_resolve_time_and_ren_time():
    """The three numbers an operation's wall is made of add up to its wall.

    Defect caught: a profile that reported the lease queue and the
    Resolve hold as separate numbers WITHOUT saying what was left over
    - so a reader could not tell a fast operation from a slow one that
    was mostly Ren's own work. Every second of the wall is named.
    """
    rows = _task_rows("t1", "axi.property_change", 0.0, 100.0) + [
        _lease(perf_ledger.RESOLVE_WAIT, 1.0, 60.0, outcome="granted"),
        _lease(perf_ledger.RESOLVE_HOLD, 61.0, 25.0, exclusive=True),
    ]

    split = perf_report.operation_split(rows, "r1")

    task = split["tasks"][0]
    assert task["queue_wait_s"] == 60.0
    assert task["resolve_s"] == 25.0
    assert task["ren_s"] == 15.0
    assert (task["queue_wait_s"] + task["resolve_s"] + task["ren_s"]
            == task["latency_s"])


def test_an_operation_that_never_asked_for_a_lease_is_unmeasured_not_zero():
    """No lease means no measured queue wait - never a zero queue wait.

    Defect caught: a task that refused before it ever asked for Resolve
    reporting a queue wait of 0.0s, which reads as "the queue was
    free" - a fact nobody measured - and hides the refusal behind a
    fast number.
    """
    rows = _task_rows("t1", "axi.property_change", 0.0, 4.0,
                      outcome="exit 4")

    split = perf_report.operation_split(rows, "r1")

    task = split["tasks"][0]
    assert task["queue_wait_s"] is None
    assert task["resolve_s"] is None
    assert task["measured"] is False
    assert task["ren_s"] == 4.0
    assert split["classes"][0]["unmeasured"] == 1


def test_the_split_reports_the_denominator_it_is_a_mean_of():
    """Each class says how many operations it is a mean of.

    Defect caught: a class total read as "the cost of one operation" -
    a property change that took 900s in the queue and one that took 3s
    averaged into one number that describes neither.
    """
    rows = []
    for index, (wait, hold) in enumerate(((10.0, 5.0), (30.0, 5.0))):
        rows += _task_rows(f"t{index}", "axi.property_change",
                           index * 100.0, index * 100.0 + 50.0)
        rows.append(_lease(perf_ledger.RESOLVE_WAIT,
                           index * 100.0 + 1.0, wait, outcome="granted"))
        rows.append(_lease(perf_ledger.RESOLVE_HOLD,
                           index * 100.0 + wait + 1.0, hold, exclusive=True))

    split = perf_report.operation_split(rows, "r1")

    row = split["classes"][0]
    assert row["operations"] == 2
    assert row["measured"] == 2
    assert row["latency_s"] == 100.0
    assert row["queue_wait_s"] == 40.0
    assert row["resolve_s"] == 10.0
    assert row["ren_s"] == 50.0
    assert row["latency_p50_s"] == 50.0


def test_a_lease_that_belongs_to_no_operation_is_not_charged_to_one():
    """A lease span outside every operation's window is left alone.

    Defect caught: every lease span in the ledger charged to the
    operation nearest it in time - so a lane's own lease inflated
    another lane's measured queue wait, and the comparison between two
    runs became a comparison of who was on the machine.
    """
    rows = _task_rows("t1", "axi.property_change", 100.0, 120.0) + [
        _lease(perf_ledger.RESOLVE_WAIT, 1.0, 60.0, outcome="granted"),
    ]

    split = perf_report.operation_split(rows, "r1")

    assert split["tasks"][0]["queue_wait_s"] is None
    assert split["unattributed_spans"] == 1


def test_the_split_is_rendered_so_a_reader_sees_the_three_numbers():
    """`ren profile` shows the split, so the measurement has a reader.

    Defect caught: a split computed and never rendered - the punchlist
    asks for the wall time of an operation split into queue wait,
    Resolve time and Ren time, and a number nothing prints is a number
    nobody reads.
    """
    rows = _task_rows("t1", "axi.property_change", 0.0, 100.0) + [
        _lease(perf_ledger.RESOLVE_WAIT, 1.0, 60.0, outcome="granted"),
        _lease(perf_ledger.RESOLVE_HOLD, 61.0, 25.0, exclusive=True),
    ]

    report = {"operations": perf_report.operation_split(rows, "r1")}
    text = "\n".join(perf_report.render_operations(report))

    assert "axi.property_change" in text
    assert "60.0" in text and "25.0" in text and "15.0" in text


# ── PERF-003: the waits, the opens and the cold start ──────────────

def test_cold_and_warm_inference_are_separated_and_an_unsaid_one_is_unmeasured():
    """An inference that cannot say whether it was cold is UNMEASURED.

    Defect caught: every inference charged as warm, so the cold-start
    cost of a session disappears and a model that is paid to load
    every run reads as free.
    """
    rows = [
        _capability(10.0, 0.0),
        _span("gemma_inference", 5.0, 1.0, cold_start=True),
        _span("gemma_inference", 2.0, 2.0, cold_start=False),
        _span("gemma_inference", 1.0, 3.0),
    ]

    access = perf_report.model_access(rows, "r1")

    assert access["cold_inference_s"] == 5.0
    assert access["warm_inference_s"] == 2.0
    assert access["unmeasured_inference_s"] == 1.0
    assert access["unmeasured_inferences"] == 1


def test_a_project_opens_phases_are_a_decomposition_not_an_addition():
    """The switch's total is the switch; its phases explain it.

    Defect caught: the total being the sum of the phases, so the
    profile charges the project open twice - once whole and once in
    pieces.
    """
    rows = [
        _capability(10.0, 0.0),
        _span("project_switch", 5.0, 1.0, switches=1),
        _span("project_switch_load", 2.0, 1.0),
        _span("project_switch_settle", 2.0, 1.1),
        _span("project_switch_readback", 1.0, 1.2),
    ]

    opened = perf_report.project_open(rows, "r1")

    assert opened["total_s"] == 5.0
    assert opened["load_s"] == 2.0
    assert opened["settle_s"] == 2.0
    assert opened["readback_s"] == 1.0
    assert opened["switches"] == 1


def test_the_waits_are_named_apart_from_each_other():
    """The scheduler queue, the Resolve queue and the project open are three.

    Defect caught: one "wait" number that cannot say which queue the
    run stood in, so the reader optimizes the wrong one.
    """
    rows = [
        _capability(10.0, 0.0),
        {"kind": perf_ledger.SCHEDULE, "run_id": "r1", "owner": "semantics",
         "profile": "machine", "queue_wait_s": 3.0, "execution_s": 1.0,
         "started_at": 0.0},
        _span(perf_ledger.RESOLVE_WAIT, 4.0, 1.0, outcome="granted"),
        _span("project_switch", 5.0, 2.0, switches=1),
    ]

    waits = perf_report.waits(rows, "r1")

    assert waits["scheduler_queue_s"] == 3.0
    assert waits["resolve_lease_wait_s"] == 4.0
    assert waits["project_open_s"] == 5.0
    assert waits["total_s"] == 12.0


# ── PERF-005: the cost of one verified decision ────────────────────

def test_an_unverified_edit_does_not_count_as_a_verified_decision():
    """Only an edit that passed its gate is a verified decision.

    Defect caught: the cost per decision counting an edit that was
    placed and never graded, so the denominator says the engine is
    cheaper per decision than it is.
    """
    rows = [
        _capability(20.0, 0.0),
        _reel_edit("Reel 07", True),
        _reel_edit("Reel 08", False),
    ]

    cost = perf_report.cost_per_verified_decision(rows, "r1")

    assert cost["verified_decisions"] == 1
    assert cost["unverified_decisions"] == 1
    assert cost["per_verified_decision"]["wall_s"] == 20.0


def test_a_cost_dimension_nobody_measured_is_unmeasured_not_zero():
    """Nobody priced the run, so the price is unknown - not free.

    Defect caught: an unmeasured dimension reported as zero, so a
    profile claims the money was nothing when nobody ever looked.
    """
    rows = [
        _capability(20.0, 0.0),
        _reel_edit("Reel 07", True),
    ]

    cost = perf_report.cost_per_verified_decision(rows, "r1")

    assert cost["per_verified_decision"]["usd"] is None
    assert "usd" in cost["dimensions_unmeasured"]


def test_the_mean_decision_wall_is_the_decision_not_the_run():
    """A decision's own cost is reported beside the run's cost.

    Defect caught: the only number for a decision being the whole
    run's cost divided by the decisions in it, which grows with every
    unrelated capability the run happened to do.
    """
    rows = [
        _capability(100.0, 0.0),
        _reel_edit("Reel 07", True, total_s=12.0),
    ]

    cost = perf_report.cost_per_verified_decision(rows, "r1")

    assert cost["mean_decision_wall_s"] == 12.0
    assert cost["per_verified_decision"]["wall_s"] == 100.0


# ── PERF-006: the work repeated across edits ──────────────────────

def _decode(fingerprint, purpose, count, run_id="r1", capability="reel.build"):
    return {"kind": perf_ledger.COUNT, "family": "media_decode_requests",
            "count": count, "run_id": run_id, "capability": capability,
            "source_fingerprint": fingerprint, "decode_purpose": purpose}


def test_the_same_source_measured_again_across_edits_is_reported():
    """The same source, measured for the same purpose, twice.

    Defect caught: the repeated work never being reported, so the
    engine re-measures the same footage for every edit and nobody
    knows what it pays for it.
    """
    rows = [
        _decode("abc", "semantic.coarse_frames", 1, run_id="r1"),
        _decode("abc", "semantic.coarse_frames", 1, run_id="r2"),
    ]

    report = perf_report.repeated_work(rows)

    assert report["repeated_sources"] == 1
    assert report["paid_repeats"] == 1
    assert report["sources"][0]["runs"] == ["r1", "r2"]


def test_a_repeat_served_from_the_cache_is_not_paid_waste():
    """A repeat the cache answered cost nothing, and says so.

    Defect caught: the repeated-work report calling cached reuse
    waste, so the reader goes and removes a cache that was already
    doing its job.
    """
    rows = [
        _decode("abc", "semantic.coarse_frames", 1),
        {"kind": perf_ledger.COUNT, "family": "media_decode_cache_hits",
         "count": 1, "run_id": "r2", "capability": "reel.build",
         "source_fingerprint": "abc",
         "decode_purpose": "semantic.coarse_frames"},
    ]

    report = perf_report.repeated_work(rows)

    assert report["repeated_sources"] == 1
    assert report["paid_repeats"] == 0
    assert report["cache_hits"] == 1


def test_one_source_measured_for_two_purposes_is_not_repeated_work():
    """Two different measurements of one source are not a repeat.

    Defect caught: two legitimately different purposes flagged as
    wasted re-measurement, so the report cries wolf on every run that
    measured anything twice.
    """
    rows = [
        _decode("abc", "semantic.coarse_frames", 1),
        _decode("abc", "semantic.action_windows", 1),
    ]

    report = perf_report.repeated_work(rows)

    assert report["repeated_sources"] == 0
