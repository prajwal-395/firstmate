"""Per-step wall clock on the run's own record.

Filed 2026-09-17: the captain asked how long a rebuild took versus the
original build, and no run had ever written down what it cost - the
answer had to be assembled from file counts and mtimes. `step_timings`
in `pipeline_run.json` is that record: one honest duration for each
step that RAN, and a `reused` mark for each step that was skipped
because its recorded output was still good.

Fixture-only: nothing here runs a step, renders, transcribes or opens
a real project.
"""

from types import SimpleNamespace

from library.tools import run_control


def _begin(project, steps):
    run_control.begin_run_status(
        str(project), "full run", steps, argv=[],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})


def _record(project):
    return run_control.read_run_status(str(project))


def test_completed_run_carries_a_duration_for_each_step_that_ran(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan", "catalog", "temporal_index"])
    run_control.record_step_timing(str(project), "scan", duration_s=12.34)
    run_control.record_step_timing(str(project), "catalog", duration_s=0.24)
    run_control.record_step_timing(
        str(project), "temporal_index", reused=True)
    record = _record(project)
    assert record["step_timings"]["scan"] == {
        "duration_s": 12.3, "reused": False}
    assert record["step_timings"]["catalog"] == {
        "duration_s": 0.2, "reused": False}
    # The reused step says so, and carries no invented duration.
    assert record["step_timings"]["temporal_index"] == {"reused": True}

    # A rerun overwrites the step's previous entry.
    run_control.record_step_timing(str(project), "temporal_index",
                                   duration_s=5.0)
    assert _record(project)["step_timings"]["temporal_index"] == {
        "duration_s": 5.0, "reused": False}


def test_new_run_starts_fresh_rather_than_carrying_costs_forward(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"])
    run_control.record_step_timing(str(project), "scan", duration_s=60.0)
    _begin(project, ["scan"])
    assert _record(project)["step_timings"] == {}
