"""What a latency budget says, and what it refuses to guess.

Every test here names the defect it catches. A budget is a tripwire a
person reads to decide whether the engine got slower, so a number that
is quietly wrong costs more than a missing one: the missing number is
asked for, the wrong one is believed.
"""

import json

import pytest

from library.tools import latency_budget, perf_ledger


def _capability(wall, started_at, capability="reel.build", run_id="r1",
                 status="ok", **fields):
    return {"kind": perf_ledger.CAPABILITY, "run_id": run_id,
            "capability": capability, "started_at": started_at,
            "wall_s": wall, "status": status, **fields}


def _task(wall, started_at, task_class="axi.property_change", run_id="r1",
          outcome="ok", **fields):
    return {"kind": perf_ledger.TASK, "run_id": run_id, "state": "completed",
            "task_class": task_class, "requested_at": started_at,
            "completed_at": started_at + wall, "latency_s": wall,
            "outcome": outcome, **fields}


def _span(layer, wall, started_at, run_id="r1", capability="reel.build",
          **fields):
    return {"kind": perf_ledger.SPAN, "layer": layer, "run_id": run_id,
            "capability": capability, "started_at": started_at,
            "wall_s": wall, **fields}


def _baseline(operations, **extra):
    table = {"schema": latency_budget.BASELINE_SCHEMA, "revision": 1,
             "measured_on": "2026-01-01", "machine": {},
             "operations": operations, **extra}
    return table


def _write(tmp_path, table):
    path = tmp_path / "latency_budgets.json"
    path.write_text(json.dumps(table), encoding="utf-8")
    return path


# ── The vocabulary ─────────────────────────────────────────────────

def test_the_seven_classes_are_the_ones_the_punchlist_names():
    """The classes are the punchlist's seven, by name.

    Defect caught: a class renamed to whatever the code called it makes
    the budget table unreadable against the plan it answers, and a
    reader cannot tell whether a class was dropped or renamed.
    """
    assert [c.id for c in latency_budget.OPERATION_CLASSES] == [
        "metadata_read", "property_edit", "caption_graphic_revision",
        "structural_edit", "reel_construction", "source_analysis",
        "delivery_render"]
    assert [c.title for c in latency_budget.OPERATION_CLASSES] == [
        "Metadata reads and capability discovery",
        "Simple timeline property edits",
        "Caption and graphic revisions",
        "Structural timeline edits",
        "Full reel construction",
        "Source analysis and video reasoning",
        "Final delivery and rendering"]


def test_every_operation_names_a_unit_and_a_divisor():
    """An operation without a comparable unit cannot carry a budget.

    Defect caught: a budget in raw seconds for an operation whose cost
    scales with its input (a catalogue read of 700 files, a decode of
    990 frames) is not a budget, it is a measurement of one project
    mistaken for a property of the engine.
    """
    for cls in latency_budget.OPERATION_CLASSES:
        for operation in cls.operations:
            assert operation.unit
            assert operation.divisor in (latency_budget.ONE,
                                         latency_budget.CALLS,
                                         latency_budget.FRAMES,
                                         latency_budget.SOURCE_FILES)


# ── Work separated from contention ─────────────────────────────────

def test_the_lease_queue_wait_is_not_charged_to_the_work():
    """Waiting for the one Resolve is contention, not work.

    Defect caught: the measured `axi.property_change` task (run
    2026-10-21) spent 900 s queued for the lease and 188 s working. A
    budget set on the wall says a property edit costs 1088 s; the same
    edit on a quiet machine costs 188 s. Setting the budget on the wall
    measures the weather, not the engine.
    """
    rows = [
        _task(1088.8, 0.0),
        _span(perf_ledger.RESOLVE_WAIT, 900.025, 100.0,
              capability="axi.property_change"),
    ]

    measurements = latency_budget.measure(rows, probes=False)
    sample = measurements["timeline_property_edit"].completed[0]

    assert sample.work_s == pytest.approx(188.775, abs=0.01)
    assert sample.contention_s == pytest.approx(900.025, abs=0.01)


def test_a_refused_lease_wait_is_contention_too():
    """A wait that never got the lease is still seconds spent waiting.

    Defect caught: the four 900 s lease timeouts in the scratch ledger
    (2026-10-20) were 900 s of nothing happening. Counting them as work
    says the engine spent 900 s thinking, which is the opposite of what
    happened.
    """
    rows = [
        _task(900.0, 0.0, outcome="ok"),
        _span(perf_ledger.RESOLVE_WAIT, 900.0, 0.0, capability="axi.property_change"),
    ]

    measurements = latency_budget.measure(rows, probes=False)
    sample = measurements["timeline_property_edit"].completed[0]

    assert sample.work_s == pytest.approx(0.0, abs=0.01)


def test_the_heavy_work_queue_wait_is_not_charged_to_the_work():
    """The machine's own queue is contention too.

    Defect caught: a `reel.build` that waited 30 s for the heavy-work
    slot and then built in 60 s is a 60 s build, not a 90 s one. The
    scheduler's queue wait is recorded on the schedule row, not on the
    capability, so a reader that only looks at the capability charges
    the queue to the build.
    """
    rows = [
        _capability(90.0, 0.0),
        {"kind": perf_ledger.SCHEDULE, "run_id": "r1",
         "capability": "reel.build", "started_at": 30.0,
         "queue_wait_s": 30.0, "execution_s": 60.0},
    ]

    measurements = latency_budget.measure(rows, probes=False)
    sample = measurements["reel_build"].completed[0]

    assert sample.work_s == pytest.approx(60.0, abs=0.01)


def test_two_overlapping_waits_are_counted_once():
    """Overlapping waits are one wait, not two.

    Defect caught: two lease waits that overlap (a retry that queued
    again before the first row ended) would subtract their seconds
    twice and report NEGATIVE work, so a class would look free.
    """
    rows = [
        _task(100.0, 0.0),
        _span(perf_ledger.RESOLVE_WAIT, 50.0, 0.0, capability="axi.property_change"),
        _span(perf_ledger.RESOLVE_WAIT, 30.0, 20.0, capability="axi.property_change"),
    ]

    measurements = latency_budget.measure(rows, probes=False)
    sample = measurements["timeline_property_edit"].completed[0]

    assert sample.work_s == pytest.approx(50.0, abs=0.01)
    assert sample.contention_s == pytest.approx(50.0, abs=0.01)


# ── Comparable denominators ───────────────────────────────────────

def test_a_catalogue_read_is_measured_per_source_file():
    """A catalogue read is priced per file, because it reads files.

    Defect caught: the scratch project's catalogue read cost 2.31 s for
    seven source files and 4.79 s for the same seven under load. A
    budget in raw seconds for "one catalogue read" is a statement about
    one project, not about the engine.
    """
    rows = [
        _capability(14.0, 0.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 1.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 2.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 3.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 4.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 5.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 6.0, capability="footage.catalog"),
        _span("source_ingest", 0.01, 7.0, capability="footage.catalog"),
    ]

    measurements = latency_budget.measure(rows, probes=False)
    values = measurements["catalogue_read"].work_seconds()

    assert values == [pytest.approx(2.0, abs=0.001)]


def test_a_frame_decode_is_measured_per_frame():
    """A decode is priced per frame, because it decodes frames.

    Defect caught: the scratch ledger's decode spans cost 24.2 s for 47
    frames and 467.4 s for 990 - a 19x difference in the same class,
    entirely from clip length. One raw number for both hides which one
    a reader is looking at.
    """
    rows = [_span("media_decode", 24.235, 0.0, calls=47)]

    measurements = latency_budget.measure(rows, probes=False)
    values = measurements["coarse_frame_decode"].work_seconds()

    assert values == [pytest.approx(24.235 / 47, abs=1e-6)]


def test_a_row_with_no_units_measured_is_not_a_sample():
    """A row whose unit count is zero measures nothing.

    Defect caught: a decode span that recorded no frame requests would
    divide its wall by zero, and the class would carry a sample of
    infinity - a budget compared against infinity never fires.
    """
    rows = [_span("media_decode", 24.235, 0.0, calls=0)]

    measurements = latency_budget.measure(rows, probes=False)

    assert measurements["coarse_frame_decode"].work_seconds() == []


# ── Outcomes kept apart ───────────────────────────────────────────

def test_a_refused_operation_never_sets_a_budget():
    """A refusal is not a slow operation; it is a different fact.

    Defect caught: the scratch ledger holds 33 refused `axi.property_change`
    tasks and 3 successful ones. Pooling them says a property edit
    costs 95 s when the three that actually happened cost 59-379 s and
    the 33 that did not happen cost nothing at all.
    """
    rows = [
        _task(900.0, 0.0, outcome="exit 4"),
        _task(59.2, 1000.0, outcome="ok"),
    ]

    measurements = latency_budget.measure(rows, probes=False)
    measured = measurements["timeline_property_edit"]

    assert len(measured.completed) == 1
    assert len(measured.refused) == 1
    assert measured.work_seconds() == [pytest.approx(59.2, abs=0.01)]


def test_a_failed_capability_is_not_a_completed_sample():
    """A failed build is not a build.

    Defect caught: the scratch ledger holds 104 failed `reel.build`
    capabilities against 12 that finished, most of the failures being
    lease timeouts. A budget measured over all of them describes a
    class of operations that mostly never happened.
    """
    rows = [
        _capability(900.0, 0.0, status="failed"),
        _capability(132.7, 1000.0, status="ok"),
    ]

    measurements = latency_budget.measure(rows, probes=False)
    measured = measurements["reel_build"]

    assert len(measured.completed) == 1
    assert len(measured.refused) == 1
    assert measured.work_seconds() == [pytest.approx(132.7, abs=0.01)]


def test_a_ledger_with_no_rows_is_not_a_measurement_of_nothing():
    """An empty ledger says there is nothing to measure, not that all is free.

    Defect caught: a project that has not run yet would report every
    class as UNMEASURED, which reads like a measurement that looked and
    found nothing - a different fact from "there is nothing here".
    """
    assert latency_budget.main(["--no-probe", "/tmp/no-such-budget-project"]) == 1


def test_a_ledger_with_rows_measures_them():
    """A ledger with rows is measured and reported."""
    assert latency_budget.main(
        ["--no-probe", "--ledger", _fixture_ledger()]) == 0


def _fixture_ledger(tmp_path=None) -> str:
    """A ledger with one measured capability row and one refused one."""
    import json as _json
    import tempfile
    from pathlib import Path as _Path
    directory = _Path(tempfile.mkdtemp())
    path = directory / "perf_ledger.jsonl"
    rows = [
        {"kind": "capability", "capability": "reel.build", "run_id": "r1",
         "status": "ok", "started_at": 0.0, "wall_s": 100.0},
        {"kind": "capability", "capability": "reel.build", "run_id": "r1",
         "status": "failed", "started_at": 200.0, "wall_s": 900.0},
    ]
    path.write_text("\n".join(_json.dumps(r) for r in rows), encoding="utf-8")
    return str(path)


def test_a_class_with_no_completed_sample_is_unmeasured_not_zero():
    """No sample means unmeasured, never "measured and free".

    Defect caught: the scratch ledger has no successful `axi.trim` task
    at all. A class that reports 0.0 s per trim reads as "trims are
    instant" and would never trip a budget, when the truth is that
    nothing measured one.
    """
    rows = [_task(17.0, 0.0, task_class="axi.trim", outcome="exit 1")]

    measurements = latency_budget.measure(rows, probes=False)
    measured = measurements["timeline_trim"]

    assert measured.work_seconds() == []
    assert measured.verdict(10.0) == "unmeasured"


def test_a_project_with_no_ledger_reads_every_class_as_unmeasured():
    """A ledger with no rows says so for every class.

    Defect caught: a project that has not run yet would report a budget
    table of zeros, and a reader would take every class as free.
    """
    measurements = latency_budget.measure([], probes=False)
    baseline = latency_budget.load_baseline()

    for key, measured in measurements.items():
        assert measured.work_seconds() == [], key
        assert measured.verdict(baseline.budgets[key].target_s) == "unmeasured", key


# ── The check ─────────────────────────────────────────────────────

def _baseline_rows(tmp_path, targets):
    operations = {}
    for key in latency_budget._operation_index():
        target, samples = targets.get(key, (None, 0))
        operations[key] = {"class": latency_budget._class_of(key),
                           "target_s": target, "samples": samples,
                           "basis": "measured", "measured_on": "2026-01-01",
                           "source": "unit test"}
    path = _write(tmp_path, _baseline(operations))
    return latency_budget.load_baseline(path)


def test_a_class_over_its_budget_is_flagged(tmp_path):
    """The check names the class that got slower.

    Defect caught: a budget nobody checks is a number in a file. The
    whole point of the table is that a regression is reported.
    """
    baseline = _baseline_rows(tmp_path, {
        "reel_build": (100.0, 4),
        "timeline_property_edit": (500.0, 3),
    })
    rows = [
        _capability(300.0, 0.0),
        _task(59.2, 1000.0),
    ]

    measurements = latency_budget.measure(rows, baseline, probes=False)
    findings = latency_budget.check(measurements, baseline)

    assert [f.operation for f in findings] == ["reel_build"]
    assert findings[0].ratio == pytest.approx(3.0, abs=0.01)


def test_a_class_that_moved_only_with_the_load_is_not_flagged(tmp_path):
    """A class that cost a little more is the weather, not a regression.

    Defect caught: the machine is shared by every lane, so a class's
    measured cost moves with the load. A gate that fired on that
    movement would be noise dressed as a regression, and a gate that
    cries wolf gets ignored.
    """
    baseline = _baseline_rows(tmp_path, {"reel_build": (100.0, 4)})
    rows = [_capability(130.0, 0.0)]

    measurements = latency_budget.measure(rows, baseline, probes=False)

    assert latency_budget.check(measurements, baseline) == []


def test_a_class_within_its_budget_is_not_flagged(tmp_path):
    """A class that did not get slower is not reported.

    Defect caught: a check that flags everything is a check nobody
    reads, because it says nothing about which change mattered.
    """
    baseline = _baseline_rows(tmp_path, {"reel_build": (500.0, 4)})
    rows = [_capability(300.0, 0.0)]

    measurements = latency_budget.measure(rows, baseline, probes=False)

    assert latency_budget.check(measurements, baseline) == []


def test_an_unbudgeted_class_is_never_flagged(tmp_path):
    """A class with no target is reported as unmeasured, not as failing.

    Defect caught: a class whose baseline row was never filled in would
    compare against a target of zero and flag every run, which is how a
    budget table becomes noise.
    """
    baseline = _baseline_rows(tmp_path, {"reel_build": (500.0, 4)})
    rows = [_capability(300.0, 0.0)]

    measurements = latency_budget.measure(rows, baseline, probes=False)
    report = latency_budget.report(rows, baseline, measurements, probes=False)
    operations = {o["key"]: o for c in report["classes"] for o in c["operations"]}

    assert operations["timeline_property_edit"]["verdict"] == "unmeasured"
    assert report["over_budget"] == 0


# ── The baseline table's own integrity ─────────────────────────────

def test_a_table_that_drops_an_operation_is_refused(tmp_path):
    """A table that stops covering an operation is refused, not applied.

    Defect caught: a baseline edited to remove a hard class leaves that
    class silently unbudgeted, and every later run reports it as
    unmeasured with nothing saying the table was truncated.
    """
    path = _write(tmp_path, _baseline({
        "reel_build": {"class": "reel_construction", "target_s": 100.0},
    }))

    with pytest.raises(ValueError, match="does not budget"):
        latency_budget.load_baseline(path)


def test_a_table_that_budgets_an_unknown_operation_is_refused(tmp_path):
    """A table naming an operation the module does not define is refused.

    Defect caught: a typo in an operation key reads as a class with no
    budget rather than as the mistake it is.
    """
    path = _write(tmp_path, _baseline({
        "reel_build": {"class": "reel_construction", "target_s": 100.0},
        "reel_buildd": {"class": "reel_construction", "target_s": 100.0},
        **{k: {"class": latency_budget._class_of(k), "target_s": 1.0}
           for k in latency_budget._operation_index()
           if k not in ("reel_build",)},
    }))

    with pytest.raises(ValueError, match="does not define"):
        latency_budget.load_baseline(path)


def test_a_table_with_the_wrong_schema_is_refused(tmp_path):
    """A table from another schema is refused rather than misread.

    Defect caught: a baseline whose shape changed would be read with
    this version's field names, and every number in it would be wrong
    in a way no reader could see.
    """
    path = _write(tmp_path, _baseline({
        k: {"class": latency_budget._class_of(k), "target_s": 1.0}
        for k in latency_budget._operation_index()
    }, schema="something-else/1"))

    with pytest.raises(ValueError, match="schema"):
        latency_budget.load_baseline(path)


def test_the_committed_baseline_table_budgets_every_operation():
    """The checked-in table covers the whole vocabulary.

    Defect caught: an operation added to the vocabulary without a
    baseline row makes the checked-in table fail to load, so the
    command nobody can run.
    """
    baseline = latency_budget.load_baseline()

    assert set(baseline.budgets) == set(latency_budget._operation_index())
    for key, budget in baseline.budgets.items():
        assert budget.class_id in latency_budget.CLASS_IDS
        if budget.target_s is not None:
            assert budget.target_s > 0.0, key
            assert budget.samples >= 1, key
            assert budget.basis, key
            assert budget.source, key


def test_the_committed_baseline_records_the_machine_it_was_measured_on():
    """A budget is a fact about a machine, and says which one.

    Defect caught: a budget measured on a loaded machine is not a
    budget for a quiet one, and a reader on another machine cannot tell
    whether the difference is the engine or the hardware.
    """
    baseline = latency_budget.load_baseline()

    assert baseline.machine.get("platform")
    assert baseline.machine.get("cpu")
    assert baseline.measured_on


def test_a_baseline_row_names_the_runs_its_target_came_from():
    """Every measured target names the runs behind it.

    Defect caught: a baseline whose rows carry a number and no
    provenance cannot be re-measured, so a regression in the target
    itself is invisible.
    """
    baseline = latency_budget.load_baseline()

    for key, budget in baseline.budgets.items():
        if budget.target_s is None:
            continue
        assert budget.samples >= 1, key
        assert budget.basis, key
        assert budget.source, key
        assert budget.measured_on, key
