"""PERF-011: a refusal the engine already decided is not decided again.

The runner paid for the same refusal twice: a step refused, the run
ended, the next run gathered the same inputs, ran the same code and
paid for the same decision again. `library/tools/retry_guard.py` is the
memory that stops the second payment, and this file drives the real
runner to prove it.

The defects these tests name:

  * a refused step runs again on the next run, on the same inputs, and
    pays for the same decision twice;
  * the repeat says nothing about WHY it refused, so the person reading
    it cannot act;
  * a step that asked the MODEL is remembered as if the model had been
    consulted already - the model is resampled, so that refusal is a
    sample and not a decision;
  * `--rerun <step>` does not get past the memo, so a lane cannot
    force the step it needs.
"""
from __future__ import annotations

import json
from pathlib import Path

from library.processes.edit_video import run_pipeline as runner
from library.tools.ren_refusal import RenRefusal


def _synthetic_dag():
    return {
        "id": "edit_video",
        "nodes": [
            {"id": "scan", "name": "scan",
             "step_ref": "steps/step_1_01_scan_project"},
        ],
        "edges": [],
    }


def _synthetic_manifests():
    return {
        "scan": {
            "id": "step_scan",
            "interface": {
                "inputs": [],
                "outputs": [{"name": "result", "type": "string"}],
            },
            "classification": {"stage": "edit"},
        },
    }


def _fresh_project(tmp_path: Path) -> Path:
    project = tmp_path / "perf011-project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: PERF-011\nslug: perf011-project\n", encoding="utf-8")
    (project / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(project),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")
    return project


def _drive(monkeypatch, project: Path, *, refuse: bool = True,
           asked_the_model: bool = False, identity: dict | None = None,
           executed: list | None = None):
    """Run the pipeline on the synthetic DAG with a step that refuses.

    `identity` names the step's identity KEY. Left alone, the runner
    computes the real one - the planner cache's identity over the
    gathered inputs, the files they point at, the step's code and the
    run mode - which is what production matches on. It is not cheap
    (the code closure is hashed), so a test that does not care about
    WHICH identity it gets names one.

    `executed` is the list of the steps that ran, so a test can watch
    one list across two drives.
    """
    dag = _synthetic_dag()
    manifests = _synthetic_manifests()
    if executed is None:
        executed = []

    def implementation(step_dir):
        return {
            "type": "deterministic",
            "entry": str(Path(step_dir) / "step.py"),
            "step_dir": Path(step_dir),
            "manifest": manifests["scan"],
        }

    def run_step(entry, _inputs):
        executed.append("scan")
        if asked_the_model:
            # A refusal raised AFTER the model was asked is a sample of
            # the model, not a decision about the state.
            runner._note_model_asked()
        if refuse:
            raise RenRefusal(
                what="the plan names an overlay that is not on disk",
                why="the overlay segment was planned and never rendered",
                fix="render the overlay, then re-run the step",
                code="REN_TEST_OVERLAY_MISSING", blocker="missing_media")
        return {"result": "scan"}

    monkeypatch.setattr(runner, "load_dag", lambda: dag)
    monkeypatch.setattr(runner, "get_step_implementation", implementation)
    monkeypatch.setattr(runner, "run_deterministic_step", run_step)
    monkeypatch.setattr(runner, "apply_source_identity", lambda *_a: None)
    monkeypatch.setattr(
        runner, "parallel_workers_enabled", lambda *_a, **_k: False)
    from library.tools import capabilities
    monkeypatch.setattr(capabilities, "all", lambda: ())
    monkeypatch.setattr(
        runner.briefing_chat, "conduct_if_needed",
        lambda _project, state, *_a, **_k: state)
    from library.tools import requirements as R
    monkeypatch.setattr(R, "all_requirements", lambda dag=None, manifests=None: [])
    if identity is not None:
        monkeypatch.setattr(
            runner, "_step_identity_key",
            lambda *a, **k: identity["key"])
    return executed


def _state(project: Path) -> dict:
    return json.loads((project / "pipeline_data.json").read_text(
        encoding="utf-8"))


def test_a_refused_step_is_not_paid_for_twice(tmp_path, monkeypatch):
    project = _fresh_project(tmp_path)
    executed = _drive(monkeypatch, project)

    first = runner.run_pipeline(str(project), only=["scan"])

    assert first["status"] == "FAILED", first
    assert executed == ["scan"]
    memo = _state(project)["retry_guard"]
    assert "scan" in memo
    assert memo["scan"]["code"] == "REN_TEST_OVERLAY_MISSING"

    second = runner.run_pipeline(str(project), only=["scan"])

    # The step did not run again: the refusal was already decided on
    # these inputs, and running it would decide the same thing again.
    assert executed == ["scan"]
    assert second["status"] == "FAILED", second
    # The repeat says WHY, so the person reading it can act.
    error = _state(project)["step_errors"]["scan"]
    assert "overlay" in error
    assert "--rerun scan" in error


def test_a_rerun_gets_past_the_memo(tmp_path, monkeypatch):
    project = _fresh_project(tmp_path)
    executed = _drive(monkeypatch, project, identity={"key": "key-1"})

    runner.run_pipeline(str(project), only=["scan"])
    runner.run_pipeline(str(project), only=["scan"])
    assert executed == ["scan"]

    runner.run_pipeline(str(project), only=["scan"], rerun=["scan"])

    # The operator's word beats the memo.
    assert executed == ["scan", "scan"]


def test_a_refusal_the_model_decided_is_not_remembered(tmp_path,
                                                        monkeypatch):
    project = _fresh_project(tmp_path)
    executed = _drive(monkeypatch, project, asked_the_model=True,
                      identity={"key": "key-1"})

    runner.run_pipeline(str(project), only=["scan"])

    # The model is resampled, so the same inputs can produce a
    # different answer and a different outcome. Remembering this
    # refusal would refuse a step that would now answer differently.
    assert "retry_guard" not in _state(project)

    runner.run_pipeline(str(project), only=["scan"])

    assert executed == ["scan", "scan"]


def test_a_refusal_on_a_changed_state_does_not_refuse_and_is_forgotten(
        tmp_path, monkeypatch):
    project = _fresh_project(tmp_path)
    identity = {"key": "key-1"}
    executed = _drive(monkeypatch, project, identity=identity)

    runner.run_pipeline(str(project), only=["scan"])
    assert "scan" in _state(project)["retry_guard"]

    # The state the refusal was decided on has changed, so the step
    # runs - and it works, so the memo goes with it.
    identity["key"] = "key-2"
    _drive(monkeypatch, project, refuse=False, identity=identity,
           executed=executed)
    summary = runner.run_pipeline(str(project), only=["scan"])

    assert summary["status"] != "FAILED", summary
    assert executed == ["scan", "scan"]
    # The memo does not outlive its cause.
    assert "retry_guard" not in _state(project)
