"""T7: `auto_recoverable` is real for the safe cases.

A missing-prerequisite refusal whose every state-side entry names a
producer step carries recovery class `auto_recoverable` with the
producer list. Under `--full-auto agent` the runner adds those
producers to the run (the same selection as `--only <producer>`) and
says so in one line; everywhere else it refuses with the runnable
`ren edit <project> --only ...` command.

`ren new` creates a missing projects root outright (`ren init` is not
required first), and the deliver worker's refusal names `ren setup`
(which repairs) instead of `ren doctor` (which does not).
"""
from __future__ import annotations

import json
from pathlib import Path

from library.processes.edit_video import run_pipeline as runner


def _synthetic_dag():
    return {
        "id": "edit_video",
        "nodes": [
            {"id": "scan", "name": "scan",
             "step_ref": "steps/step_1_01_scan_project"},
            {"id": "catalog", "name": "catalog",
             "step_ref": "steps/step_1_02_catalog_footage"},
        ],
        # No data edge: the consumer's need arrives as a predicate
        # requirement (a code route, not a DAG edge), so run_scope does
        # not pull the producer in and the runner must refuse - the
        # shape T7 recovers from.
        "edges": [],
    }


def _synthetic_manifests():
    return {
        node_id: {
            "id": f"step_{node_id}",
            "interface": {
                "inputs": [],
                "outputs": [{"name": "result", "type": "string"}],
            },
            "classification": {"stage": "edit"},
        }
        for node_id in ("scan", "catalog")
    }


def _synthetic_requirement():
    """One predicate the consumer needs and only the producer makes."""
    from library.tools import requirements as R

    def check(ctx: R.Context) -> R.Satisfaction:
        if ctx.value_for("scan", "made_thing") is None:
            return R.UNSATISFIED(
                "producer has made no thing, so catalog cannot run",
                missing="made_thing", produced_by=("scan",))
        return R.SATISFIED(R.IN_STATE)

    return R.Requirement(
        name="t7.consumer_needs_thing", kind=R.KIND_PREDICATE,
        describe="catalog runs on what producer makes",
        produced_by=("scan",), consumers=("catalog",),
        produced_keys=(("scan", "made_thing"),),
        check=check,
        refuting_context=lambda: R.Context(),
        satisfying_context=lambda: R.Context(state={
            "capability_outputs": {
                "scan": {"output": {"made_thing": {"_witness": True}}}}}),
    )


def _fresh_project(tmp_path: Path) -> Path:
    project = tmp_path / "t7-project"
    project.mkdir()
    (project / "project.yaml").write_text(
        "name: T7 Project\nslug: t7-project\n", encoding="utf-8")
    (project / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(project),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")
    return project


def _drive(monkeypatch, project: Path, **kwargs):
    """Run the pipeline on the synthetic DAG with deterministic steps."""
    from library.tools import requirements as R

    dag = _synthetic_dag()
    manifests = _synthetic_manifests()
    executed: list = []

    def implementation(step_dir):
        node_id = ("scan" if Path(step_dir).name == "step_1_01_scan_project"
                   else "catalog")
        return {
            "type": "deterministic",
            "entry": str(Path(step_dir) / "step.py"),
            "step_dir": Path(step_dir),
            "manifest": manifests[node_id],
        }

    def run_step(entry, _inputs):
        node_id = ("scan" if Path(entry).parent.name
                   == "step_1_01_scan_project" else "catalog")
        executed.append(node_id)
        return {"result": node_id}

    monkeypatch.setattr(runner, "load_dag", lambda: dag)
    monkeypatch.setattr(runner, "get_step_implementation", implementation)
    monkeypatch.setattr(runner, "run_deterministic_step", run_step)
    monkeypatch.setattr(runner, "apply_source_identity", lambda *_a: None)
    monkeypatch.setattr(
        runner, "parallel_workers_enabled", lambda *_a, **_k: False)
    # This is a runner recovery test with synthetic nodes. The shadow
    # capability compiler describes the real registry and is outside that
    # fixture; compiling it against the synthetic requirement list couples
    # this test to unrelated real capability declarations.
    from library.tools import capabilities
    monkeypatch.setattr(capabilities, "all", lambda: ())
    monkeypatch.setattr(
        runner.briefing_chat, "conduct_if_needed",
        lambda _project, state, *_a, **_k: state)
    monkeypatch.setattr(
        R, "all_requirements", lambda dag=None, manifests=None:
        [_synthetic_requirement()])
    return executed


def test_missing_producer_is_auto_recoverable():
    from library.tools import requirements as R

    req = _synthetic_requirement()
    unmet = R.check(["catalog"], R.Context(), [req])
    assert len(unmet) == 1
    assert R.is_auto_recoverable(unmet)
    assert R.missing_producers(unmet) == ["scan"]
    refusal = R.MissingPrerequisiteRefused(unmet, "t7-project")
    assert refusal.recovery_class == "auto_recoverable"
    assert refusal.fix == "ren edit t7-project --only scan"
    assert refusal.producers == ("scan",)


def test_agent_backend_adds_the_missing_producer(tmp_path, monkeypatch,
                                                capsys):
    from library.tools import run_control

    project = _fresh_project(tmp_path)
    executed = _drive(monkeypatch, project)

    summary = runner.run_pipeline(
        str(project), only=["catalog"], full_auto="agent")

    assert summary["status"] != "REFUSED", summary.get("reason", "")
    assert executed == ["scan", "catalog"]
    assert summary.get("auto_recovered_producers") == ["scan"]
    record = run_control.read_run_status(str(project))
    assert record.get("auto_recovered_producers") == ["scan"]
    assert "scan" in (record.get("steps_to_run") or [])
    out, err = capsys.readouterr()
    assert "auto-recovered" in err
    # `emit_run_summary` prints the summary JSON, then the artifact
    # line, so decode the leading object rather than the whole stream.
    printed, _ = json.JSONDecoder().raw_decode(out)
    assert printed["status"] == summary["status"]


def test_default_backend_refuses_with_a_runnable_command(tmp_path,
                                                         monkeypatch):
    project = _fresh_project(tmp_path)
    _drive(monkeypatch, project)

    summary = runner.run_pipeline(str(project), only=["catalog"])

    assert summary["status"] == "REFUSED"
    assert summary.get("recovery_class") == "auto_recoverable"
    fix = summary.get("fix", "")
    # The fix is a runnable command, not step-id prose: exactly the
    # command, with the producers as --only selections.
    assert fix == "ren edit t7-project --only scan"
    assert "\n" not in fix
    assert summary.get("producers") == ["scan"]
    assert fix in summary.get("reason", "")


def test_ren_new_creates_a_missing_projects_root(tmp_path):
    from library.tools import project_registry

    root = tmp_path / "no-such-root"
    assert not root.exists()
    config = project_registry.create_project(
        "demo", name="Demo", root=root)
    assert root.is_dir()
    assert (root / "demo" / "project.yaml").is_file()
    assert config.slug == "demo"


def test_deliver_refusal_names_setup_not_doctor(tmp_path, monkeypatch):
    from library.tools import reel_deliver

    # Bound into reel_deliver's own namespace at import (from-import),
    # so the seam is patched where the call reads it.
    monkeypatch.setattr(
        reel_deliver, "check_render",
        lambda: ["need gpu: not met on this worker"])
    monkeypatch.setattr(reel_deliver, "is_refusal", lambda entry: True)

    try:
        reel_deliver.deliver_reel(str(tmp_path), 1)
    except reel_deliver.DeliverRefused as exc:
        assert "ren setup" in exc.fix
        assert "ren doctor" not in exc.fix
    else:  # pragma: no cover - the worker gate must refuse here
        raise AssertionError("deliver_reel did not refuse")
