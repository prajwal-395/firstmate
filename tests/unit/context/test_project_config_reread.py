"""Finding 29: a project declaration changed after the first run never
reaches a step again.

`run_pipeline.load_pipeline_state` loads project.yaml into
`state["project_config"]` only `if "project_config" not in state`, and
state persists in pipeline_data.json. On the scout's B6 run
project.yaml went `target_duration_seconds: 60 -> 30` and the rerun's
2.02 gate still said "declared target zone (54.0-66.0s, target 60.0s)".
Silent - nothing said the declaration on disk differs from the one in
force.

The fix: re-read the declaration every run, and name the difference on
stderr when what is on disk is not what the last run used.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _project_with(tmp_path, target_seconds):
    from library.tools.project_layout import ProjectLayout

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "project.yaml").write_text(
        f"target_duration_seconds: {target_seconds}\n", encoding="utf-8")
    layout = ProjectLayout(str(project_dir))
    layout.ensure()
    return project_dir, layout


def test_a_changed_declaration_reaches_the_next_run(tmp_path, capsys):
    """The B6 shape: the first run persisted 60, the declaration now
    says 30."""
    from library.processes.edit_video import run_pipeline

    project_dir, layout = _project_with(tmp_path, 60)
    state = run_pipeline.load_pipeline_state(str(project_dir))
    assert state["project_config"]["target_duration_seconds"] == 60
    # The first run persists its state; the rerun loads it back.
    layout.pipeline_data_path.write_text(json.dumps(state), encoding="utf-8")

    (project_dir / "project.yaml").write_text(
        "target_duration_seconds: 30\n", encoding="utf-8")
    reread = run_pipeline.load_pipeline_state(str(project_dir))
    assert reread["project_config"]["target_duration_seconds"] == 30, (
        "the changed declaration never reached state - finding 29")
    err = capsys.readouterr().err
    assert "30" in err and "60" in err, (
        f"the difference was not named: {err!r}")


def test_a_removed_declaration_leaves_state_too(tmp_path):
    """A declaration deleted from project.yaml must not haunt state."""
    from library.processes.edit_video import run_pipeline

    project_dir, layout = _project_with(tmp_path, 60)
    state = run_pipeline.load_pipeline_state(str(project_dir))
    assert "project_config" in state
    layout.pipeline_data_path.write_text(json.dumps(state), encoding="utf-8")

    (project_dir / "project.yaml").write_text("{}\n", encoding="utf-8")
    reread = run_pipeline.load_pipeline_state(str(project_dir))
    assert "project_config" not in reread
