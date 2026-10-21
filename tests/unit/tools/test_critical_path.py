"""The critical path: what each stage is limited by, and by how much.

Every test here names the DEFECT it would catch.  A test that asserts a
count or a shape fires on every legitimate change and teaches nobody
anything; these assert the BEHAVIOUR that a wrong critical path would
break.

    - a second is counted ONCE, so the shares add to the wall they
      describe (a double count is what made the reel build's biggest
      cost invisible);
    - a stage that ran ALONE is serial and one that shared time with
      another stage is overlapped;
    - the limit is named from the MEASUREMENT, and the queue's wait is
      contention rather than work;
    - the next optimization is the one the measurement implies, and an
      unexplained component gets no invented action;
    - nothing is measured means unmeasured, never zero.
"""

import pytest

from library.tools import critical_path, perf_ledger


def _span(layer, started, wall, **fields):
    return {"kind": "span", "run_id": "r1", "layer": layer,
            "started_at": started, "wall_s": wall, **fields}


def test_a_contained_second_is_counted_once_not_twice():
    """Defect: the placement's own phases live INSIDE the placement's
    span, so a naive sum charged the build's biggest cost twice and the
    stage's seconds exceeded the wall they described."""
    rows = [
        _span("reel_build_place", 0.0, 100.0),
        _span("otio_placement.deferred_transform_s", 10.0, 80.0),
    ]

    report = critical_path.analyse_run(rows, "r1")

    assert report["instrumented_wall_s"] == 100.0
    layers = {row["layer"]: row["seconds"]
              for stage in report["stages"] for row in stage["layers"]}
    assert layers["otio_placement.deferred_transform_s"] == 80.0
    assert layers["reel_build_place"] == 20.0
    assert sum(layers.values()) == pytest.approx(
        report["instrumented_wall_s"], abs=0.01)


def test_the_stage_shares_add_up_to_the_wall_they_describe():
    """Defect: a profile whose stage shares add to more than the wall
    reads as "the stages overlap", which is true of the LEDGER's rows
    and false of the work."""
    rows = [
        _span("reel_build_survey", 0.0, 10.0),
        _span("reel_build_plan", 10.0, 20.0),
        _span("otio_placement.deferred_transform_s", 30.0, 30.0),
        _span("ffmpeg_encode", 60.0, 10.0),
    ]

    report = critical_path.analyse_run(rows, "r1")

    shares = sum(stage["share_pct"] for stage in report["stages"])
    assert shares == pytest.approx(100.0, abs=0.2)


def test_a_stage_that_ran_alone_is_serial_and_one_that_shared_is_overlapped():
    """Defect: the report called every stage "serial", so a reader could
    not tell a stage that could be overlapped from one that could not."""
    rows = [
        _span("source_probe", 0.0, 10.0),
        _span("reel_build_plan", 5.0, 10.0),
        _span("ffmpeg_encode", 30.0, 10.0),
    ]

    report = critical_path.analyse_run(rows, "r1")
    stages = {stage["stage"]: stage for stage in report["stages"]}

    assert stages["planning"]["shared_s"] == 5.0
    assert stages["planning"]["exclusive_s"] == 5.0
    assert stages["planning"]["shared_with"] == {"ingestion": 5.0}
    assert stages["rendering"]["role"] == "serial"
    assert stages["rendering"]["shared_s"] == 0.0


def test_the_wait_for_the_one_resolve_is_contention_and_not_work():
    """Defect: the queue's seconds were folded into the work, so a task
    that waited ten minutes read as a task that worked for ten
    minutes."""
    rows = [
        _span("resolve_wait", 0.0, 600.0, outcome="granted"),
        _span("reel_build_place", 600.0, 40.0),
    ]

    report = critical_path.analyse_run(rows, "r1")

    assert report["limit"]["name"] == critical_path.QUEUE_CONTENTION
    families = {row["family"]: row["seconds"]
                for row in report["families"]}
    assert families[critical_path.QUEUE_CONTENTION] == 600.0
    assert families[critical_path.CONTENT_WORK] == 40.0


def test_the_limit_is_named_from_the_measurement_not_assumed():
    """Defect: the report asserted a limit the rows did not support."""
    rows = [
        _span("host_model", 0.0, 50.0, calls=1),
        _span("gemma_inference", 50.0, 30.0),
    ]

    report = critical_path.analyse_run(rows, "r1")

    assert report["limit"]["name"] == critical_path.INFERENCE
    assert report["limit"]["seconds"] == 80.0


def test_a_task_with_no_measurement_reports_unmeasured_not_zero():
    """Defect: a run nothing measured read as a run that cost nothing."""
    report = critical_path.analyse_run([], "r1")

    assert report["limit"]["name"] == "unmeasured"
    assert report["instrumented_wall_s"] == 0.0
    assert report["stages"] == []


def test_the_next_optimization_is_the_measured_component_and_its_seconds():
    """Defect: the report named an optimization the measurement did not
    point at, or left the seconds it addressed unnamed."""
    rows = [_span("touchup_promote", 0.0, 40.0)]

    report = critical_path.analyse_run(rows, "r1")
    optimization = report["stages"][0]["next_optimization"]

    assert optimization["layer"] == "touchup_promote"
    assert optimization["seconds"] == 40.0
    assert optimization["named_lever"] is True
    assert optimization["lever"] == "the promotion's whole-project bookkeeping"


def test_an_unexplained_component_gets_no_invented_action():
    """Defect: the engine invented a next step for a measurement it had
    not explained."""
    rows = [_span("a_layer_nobody_placed", 0.0, 12.0)]

    report = critical_path.analyse_run(rows, "r1")
    optimization = report["stages"][0]["next_optimization"]

    assert optimization["named_lever"] is False
    assert "no lever is named for it" in optimization["action"]
    assert optimization["seconds"] == 12.0


def test_the_person_visible_wait_and_the_instrumented_work_are_both_reported():
    """Defect: a profile that showed only the work hid the wait."""
    rows = [
        {"kind": "task", "state": "requested", "task_id": "t1",
         "task_class": "axi.property_change", "run_id": "r1",
         "requested_at": 0.0},
        {"kind": "task", "state": "completed", "task_id": "t1",
         "task_class": "axi.property_change", "run_id": "r1",
         "requested_at": 0.0, "completed_at": 100.0, "latency_s": 100.0,
         "outcome": "ok"},
        _span("resolve_wait", 0.0, 90.0, outcome="granted"),
        _span("reel_build_place", 90.0, 10.0),
    ]

    report = critical_path.analyse_run(rows, "r1")

    assert report["latency_s"] == 100.0
    assert report["instrumented_wall_s"] == 100.0
    assert report["uninstrumented_s"] == 0.0
    assert report["task_class"] == "axi.property_change"
    assert report["outcome"] == "ok"


def test_the_direct_routes_phase_claims_the_axistage():
    """Defect: the DIRECT route's phase claimed no stage, so the
    whole-reel bookkeeping the direct route AVOIDS was the one number
    the profile could not show - its seconds read as unattributed."""
    assert perf_ledger.stage_of("touchup_direct") == "axi"
    assert perf_ledger.stage_of("touchup_duplicate") == "axi"
    assert perf_ledger.stage_of("touchup_conform_compose_verify") == "axi"
    assert perf_ledger.stage_of("touchup_promote") == "axi"


def test_a_class_summary_carries_the_denominator_of_its_runs():
    """Defect: a report that counted only the runs that worked read as
    the cost of the class."""
    rows = [
        {"kind": "task", "state": "completed", "task_id": "t1",
         "task_class": "axi.trim", "run_id": "r1", "latency_s": 4.0,
         "outcome": "ok"},
        {"kind": "task", "state": "completed", "task_id": "t2",
         "task_class": "axi.trim", "run_id": "r2", "latency_s": 10.0,
         "outcome": "exit 4"},
        {"kind": "task", "state": "completed", "task_id": "t3",
         "task_class": "axi.trim", "run_id": "r3", "latency_s": 30.0,
         "outcome": "exit 4"},
        _span("reel_build_place", 0.0, 10.0),
    ]

    summary = critical_path.summarise_class(rows, "axi.trim")

    assert summary["runs"] == 3
    assert summary["finished"] == 1
    assert summary["instrumented"] == 1
    assert summary["latency_s"]["p50"] == 10.0
    assert summary["latency_s"]["max"] == 30.0
