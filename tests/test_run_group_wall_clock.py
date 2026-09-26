"""Run-group wall clock on the run's own record.

Filed 2026-09-26: per-step `elapsed_s` exists and per-invocation
start/finish exists, but one plan run is several CLI invocations
(review gates, `--resume`, single steps) and each one started a fresh
account - so nothing summed a full plan run end to end. `run_group` is
that sum: a `--resume` carries the previous run's group id forward and
each invocation stamps its own wall plus the group's running total.

Fixture-only: nothing here runs a step, renders, transcribes or opens
a real project.
"""

from types import SimpleNamespace

from library.tools import run_control


def _begin(project, steps, carry=False):
    run_control.begin_run_status(
        str(project), "full run", steps, argv=[],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={}, carry_run_group=carry)


def _record(project):
    return run_control.read_run_status(str(project))


def _finish(project, started_at, finished_at):
    run_control.write_run_status(
        str(project), status="success", current_step=None,
        started_at=started_at, finished_at=finished_at)


def test_a_fresh_run_mints_a_group_with_no_prior_wall(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"])
    record = _record(project)
    assert record["run_group"]["id"].startswith("rg-")
    assert record["run_group"]["prior_wall_s"] == 0.0
    assert "run_group_wall_s" not in record


def test_a_resume_carries_the_group_while_a_rerun_mints_a_new_one(tmp_path):
    """A `--resume` that minted a fresh group would split one plan run's
    wall across two groups, and the run-total would be unreadable -
    carrying is the whole point of the id."""
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"])
    _finish(project, "2026-09-26T10:00:00", "2026-09-26T10:01:40")
    run_control.record_run_total(str(project))
    first = _record(project)
    assert first["invocation_wall_s"] == 100.0
    assert first["run_group_wall_s"] == 100.0

    _begin(project, ["catalog"], carry=True)
    resumed = _record(project)
    assert resumed["run_group"]["id"] == first["run_group"]["id"]
    assert resumed["run_group"]["prior_wall_s"] == 100.0

    _finish(project, "2026-09-26T11:00:00", "2026-09-26T11:00:30")
    run_control.record_run_total(str(project))
    second = _record(project)
    assert second["invocation_wall_s"] == 30.0
    assert second["run_group_wall_s"] == 130.0

    _begin(project, ["scan"])
    fresh = _record(project)
    assert fresh["run_group"]["id"] != first["run_group"]["id"]
    assert fresh["run_group"]["prior_wall_s"] == 0.0


def test_a_resume_with_no_previous_group_starts_one_rather_than_inheriting(
        tmp_path):
    """A group id that names no first invocation cannot be summed, so a
    resume after a deleted status file mints instead of carrying."""
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"], carry=True)
    record = _record(project)
    assert record["run_group"]["id"].startswith("rg-")
    assert record["run_group"]["prior_wall_s"] == 0.0


def test_an_uncomputable_wall_is_left_unwritten_never_estimated(tmp_path):
    """A wall with no honest endpoints is absent, not zero: a zero would
    read as "this invocation was free" and silently shrink every group
    total it joins."""
    project = tmp_path / "proj"
    project.mkdir()
    _begin(project, ["scan"])
    assert run_control.record_run_total(str(project)) == _record(project)
    assert "invocation_wall_s" not in _record(project)
    assert "run_group_wall_s" not in _record(project)
