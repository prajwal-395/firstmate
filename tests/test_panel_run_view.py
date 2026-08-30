"""The panel drives a configured run, and shows the refusal FIRST.

The panel adds one thing to `run_control`'s file protocol: the captain
SEES the selection before it runs - which steps this profile will and
will not fire, why each excluded one is excluded, and, when the selection
cannot be met, the refusal, in the panel, in the second before anything
is deleted or written.

So the properties worth holding are that the preview is the SAME answer
the runner would give (it is `run_scope`'s, not a second opinion), that
it runs NOTHING, and that the handbrake stays advisory.
"""

from __future__ import annotations

import json

import pytest
import yaml

from library.tools import run_control, run_scope
from library.tools.panel import run_view
from library.tools.project_layout import Area, ProjectLayout


@pytest.fixture
def project(tmp_path):
    (tmp_path / "project.yaml").write_text("name: t\n", encoding="utf-8")
    ProjectLayout(tmp_path).pipeline_data_path.write_text(json.dumps({
        "project_folder": str(tmp_path),
        "preflight_completed": {}, "edit_completed": {},
        "failed_steps": [], "step_outputs": {}}), encoding="utf-8")
    return tmp_path


def _write_profile(project, name, body):
    directory = ProjectLayout(project).read_dir(Area.RUN_PROFILES)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ("%s.yaml" % name)).write_text(
        yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


# ── The preview is the runner's own answer ───────────────────────────

def test_a_plain_preview_runs_the_whole_pipeline(project):
    result = run_view.preview(str(project))
    assert result.can_run
    assert len(result.steps_to_run) == len(
        run_scope.resolve(run_scope.Selection(), state=None,
                          external={}).steps_to_run)


def test_the_preview_shows_every_excluded_step_with_its_reason(project):
    result = run_view.preview(str(project), "podcast")
    assert result.can_run
    assert set(result.skipped) >= {"plan_vfx", "plan_sfx", "validate"}
    for step in result.skipped:
        assert result.reasons[step], "%s is excluded with no reason" % step


def test_the_preview_is_the_same_refusal_the_runner_would_make(project):
    """Not a second opinion: `run_scope` raises it and the panel prints
    it. A preview with its own idea of what can run would be worse than
    no preview."""
    _write_profile(project, "impossible", {
        "description": "Wants the render without the creative direction.",
        "goals": ["render"], "skip": ["creative_direction"]})
    result = run_view.preview(str(project), "impossible")

    with pytest.raises(run_scope.ScopeError) as raised:
        run_scope.resolve(run_scope.Selection(only=("render",),
                                              skip=("creative_direction",)),
                          state=None, external={})
    assert result.refusal == str(raised.value)
    assert not result.can_run


def test_a_profile_that_cannot_be_read_is_reported_not_raised(project):
    _write_profile(project, "typo", {"description": "d",
                                     "goals": ["not_a_step"]})
    result = run_view.preview(str(project), "typo")
    assert not result.can_run
    assert "not_a_step" in result.profile_error
    assert result.refusal == ""


def test_an_unknown_breakpoint_is_reported_not_raised(project):
    result = run_view.preview(str(project), break_at=["nope"])
    assert not result.can_run
    assert "nope" in result.breakpoint_error


def test_a_breakpoint_the_run_will_not_reach_is_surfaced(project):
    """Armed and unreachable is not a refusal - it strands nothing - but
    a pause that silently never happens is the trap section 3 exists to
    stop, so the preview names it."""
    result = run_view.preview(str(project), "podcast", skip=[],
                              break_at=["render"])
    plain = run_view.preview(str(project), break_at=["render"])
    assert plain.unreachable_breakpoints == ()
    assert "render" in result.steps_to_run or \
        "render" in result.unreachable_breakpoints


def test_the_preview_runs_nothing(project):
    before = ProjectLayout(project).pipeline_data_path.read_bytes()
    run_view.preview(str(project), "podcast")
    assert ProjectLayout(project).pipeline_data_path.read_bytes() == before
    assert not run_control.is_running(str(project))


def test_the_profiles_a_project_could_name_are_listed(project):
    _write_profile(project, "house", {"description": "d",
                                      "goals": ["catalog"]})
    names = {entry.name: entry.source
             for entry in run_view.available_profiles(str(project))}
    assert names["house"] == "project"
    assert names["podcast"] == "engine"


def test_an_adopted_profile_is_reported(project):
    (project / "project.yaml").write_text(
        "name: t\npipeline:\n  run_profile: podcast\n", encoding="utf-8")
    assert run_view.adopted_profile(str(project)) == "podcast"
    result = run_view.preview(str(project))
    assert result.profile.name == "podcast"
    assert result.profile.adopted is True


# ── Which interpreter runs the pipeline ──────────────────────────────

def test_a_checkout_with_no_venv_refuses_by_name(tmp_path):
    """Never `sys.executable`: under Resolve that is a stock interpreter
    with none of the ML stack, and a run launched with it dies inside a
    step's import."""
    path, why_not = run_view.pipeline_interpreter(str(tmp_path))
    assert path == ""
    assert ".venv" in why_not
    assert "whisperx" in why_not


def test_a_checkout_with_a_venv_uses_it(tmp_path):
    import os
    import stat

    venv = tmp_path / ".venv" / "bin"
    venv.mkdir(parents=True)
    interpreter = venv / "python3"
    interpreter.write_text("#!/bin/sh\n")
    interpreter.chmod(interpreter.stat().st_mode | stat.S_IXUSR)
    path, why_not = run_view.pipeline_interpreter(str(tmp_path))
    assert path == str(interpreter)
    assert why_not == ""
    assert os.path.isfile(path)


def test_the_argv_carries_what_the_preview_resolved(project, tmp_path):
    import stat

    venv = tmp_path / "repo" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python3").write_text("#!/bin/sh\n")
    (venv / "python3").chmod((venv / "python3").stat().st_mode | stat.S_IXUSR)

    result = run_view.preview(str(project), "podcast")
    argv, why_not = run_view.run_argv(str(tmp_path / "repo"), str(project),
                                      result, break_at=["review_rough_cut"])
    assert why_not == ""
    assert "--profile" in argv and "podcast" in argv
    assert "--break" in argv and "review_rough_cut" in argv
    assert "run_pipeline.py" in " ".join(argv)


def test_an_adopted_profile_is_not_repeated_on_the_command_line(project,
                                                                tmp_path):
    """It is already the project's own declaration; passing it again
    would make a run that reads differently from the one the captain
    configured."""
    import stat

    venv = tmp_path / "repo" / ".venv" / "bin"
    venv.mkdir(parents=True)
    (venv / "python3").write_text("#!/bin/sh\n")
    (venv / "python3").chmod((venv / "python3").stat().st_mode | stat.S_IXUSR)
    (project / "project.yaml").write_text(
        "name: t\npipeline:\n  run_profile: podcast\n", encoding="utf-8")

    result = run_view.preview(str(project))
    argv, _ = run_view.run_argv(str(tmp_path / "repo"), str(project), result)
    assert "--profile" not in argv


# ── The handbrake stays advisory ─────────────────────────────────────

def test_the_handbrake_writes_a_file_and_kills_nothing(project):
    """`run_control.py`'s docstring explains why it is advisory and that
    reasoning binds the panel: a half-finished step leaves state nothing
    downstream can trust."""
    import os

    run_control.pid_path(str(project)).write_text(str(os.getpid()))
    message = run_view.handbrake(str(project))
    assert "finish" in message
    assert run_control.hold_requested(str(project)) is not None
    assert run_control.hold_requested(str(project))["requested_by"] == \
        "resolve panel"
    assert "released" in run_view.release(str(project))
    assert run_control.hold_requested(str(project)) is None


def test_the_handbrake_on_a_project_with_no_run_says_so(project):
    assert "No run is up" in run_view.handbrake(str(project))


def test_a_second_run_is_refused_while_one_is_up(project):
    import os

    run_control.pid_path(str(project)).write_text(str(os.getpid()))
    launch = run_view.start_run(".", str(project), ["/bin/true"])
    assert not launch.ok
    assert "already up" in launch.message


# ── The gates ────────────────────────────────────────────────────────

def test_a_project_with_no_gates_lists_none(project):
    assert run_view.gates(str(project)) == []


def test_a_pending_gate_is_listed_first_and_answerable(project):
    from library.tools import review_gate

    review_gate.save_gate_snapshot(str(project), "creative_direction",
                                   "Creative Direction",
                                   {"target_mood": "wry"})
    review_gate.save_gate_snapshot(str(project), "scan", "Scan", {"a": 1})
    review_gate.save_gate_feedback(str(project), "scan", "approved")

    listed = run_view.gates(str(project))
    assert listed[0].step_id == "creative_direction"
    assert listed[0].status == "pending"
    assert listed[0].output_keys == ["target_mood"]
    assert listed[1].feedback_action == "approved"

    message = run_view.answer_gate(str(project), "creative_direction",
                                   "approved", note="fine")
    assert "approved" in message
    assert review_gate.get_gate_status(str(project),
                                      "creative_direction") == "approved"


def test_a_revision_must_be_a_json_object(project):
    from library.tools import review_gate

    review_gate.save_gate_snapshot(str(project), "scan", "Scan", {"a": 1})
    assert "not JSON" in run_view.answer_gate(str(project), "scan", "revised",
                                              revision_json="{oops")
    assert "must be a JSON object" in run_view.answer_gate(
        str(project), "scan", "revised", revision_json="[1,2]")
    assert "needs a JSON object" in run_view.answer_gate(
        str(project), "scan", "revised", revision_json="")
    assert review_gate.get_gate_status(str(project), "scan") == "pending", (
        "a refused revision must not have been written")


def test_an_unknown_action_is_refused_by_name(project):
    assert "Unknown action" in run_view.answer_gate(str(project), "scan",
                                                    "maybe")


def test_the_resume_hint_is_the_runs_own_argv(project):
    run_control.begin_run_status(
        str(project), "mode", ["scan"],
        argv=["--project", str(project), "--profile", "podcast",
              "--rerun", "scan"])
    hint = run_view.resume_hint(str(project))
    assert "--profile podcast" in hint
    assert "--resume" in hint
    assert "--rerun" not in hint, "a re-run that already happened is dropped"


def test_no_recorded_run_says_so(project):
    assert "no recorded run" in run_view.resume_hint(str(project))
