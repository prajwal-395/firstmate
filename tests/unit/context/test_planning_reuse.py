import json
from pathlib import Path

from library.tools import planning_reuse


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "raw").mkdir(parents=True)
    (project / "project.yaml").write_text("target_duration_seconds: 30\n")
    return project


def _dag():
    return {
        "nodes": [
            {"id": "analysis", "step_ref": "steps/analysis"},
            {"id": "left", "step_ref": "steps/left"},
            {"id": "right", "step_ref": "steps/right"},
            {"id": "compile_manifest", "step_ref": "steps/manifest"},
        ],
        "edges": [
            {"from": "analysis", "to": "left"},
            {"from": "analysis", "to": "right"},
            {"from": "left", "to": "compile_manifest"},
            {"from": "right", "to": "compile_manifest"},
        ],
    }


def test_planning_nodes_are_the_edit_ancestors_of_manifest():
    nodes = planning_reuse.planning_nodes(
        _dag(),
        {"analysis": "preflight", "left": "edit", "right": "edit",
         "compile_manifest": "edit"},
    )

    assert nodes == {"left", "right", "compile_manifest"}


def test_planner_code_identity_includes_declared_skill_instructions_and_code(
        tmp_path):
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    (step_dir / "manifest.json").write_text(
        json.dumps({"skills": ["ask_the_footage"]}))
    repo_root = Path(__file__).resolve().parents[3]

    found = planning_reuse._declared_skill_files(step_dir, repo_root)

    assert (repo_root / ".agents/skills/ask_the_footage/SKILL.md").resolve() \
        in found
    assert any(path.name == "skill.py" and "ask_the_footage" in path.parts
               for path in found)


def test_cache_identity_covers_gathered_inputs_references_code_and_routing(
        tmp_path):
    project = _project(tmp_path)
    brief = project / "brief.md"
    brief.write_text("Original brief content\n")
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    (step_dir / "handoff.md").write_text("Choose a visual direction.\n")
    dag = _dag()
    inputs = {
        "creative_brief": f"FILE: {brief}",
        "project_config": {"target_duration_seconds": 30},
        "timeline_notes": {"notes": [{"text": "keep the opening"}]},
    }
    args = (
        "left", dag, inputs, {"full_auto": "agent", "auto_mode": False},
        step_dir, str(project), Path(__file__).resolve().parents[3],
    )

    before = planning_reuse.cache_identity(*args)

    brief.write_text("Changed brief content\n")
    after_brief = planning_reuse.cache_identity(*args)
    assert after_brief["key"] != before["key"]
    assert after_brief["components"]["external_files"] != \
        before["components"]["external_files"]

    (step_dir / "handoff.md").write_text("Choose a new visual direction.\n")
    after_prompt = planning_reuse.cache_identity(*args)
    assert after_prompt["components"]["planner_code"] != \
        after_brief["components"]["planner_code"]

    changed_dag = _dag()
    changed_dag["edges"][0]["data_mappings"] = {"changed": "input"}
    after_routing = planning_reuse.cache_identity(
        "left", changed_dag, inputs, args[3], step_dir, str(project), args[6])
    assert after_routing["components"]["dag_inputs"] != \
        after_prompt["components"]["dag_inputs"]


def test_source_identity_is_part_of_each_planning_key(tmp_path):
    project = _project(tmp_path)
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('step')\n")
    dag = _dag()
    args = (
        "left", dag, {"project_config": {"target_duration_seconds": 30}},
        {"full_auto": "agent", "auto_mode": False}, step_dir,
        str(project), Path(__file__).resolve().parents[3],
    )

    first = planning_reuse.cache_identity(
        *args, source_fingerprints={"clip_001": {"sha256": "old"}})
    second = planning_reuse.cache_identity(
        *args, source_fingerprints={"clip_001": {"sha256": "new"}})

    assert second["components"]["source_identity"] != \
        first["components"]["source_identity"]
    assert second["key"] != first["key"]


def test_hidden_context_files_are_part_of_external_input_identity(tmp_path):
    project = _project(tmp_path)
    dag = _dag()
    inputs = {"project_context": {"summary": "Context is available."}}
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    args = (
        "left", dag, inputs, {"full_auto": "agent", "auto_mode": False},
        step_dir, str(project), Path(__file__).resolve().parents[3],
    )

    before = planning_reuse.cache_identity(*args)
    hidden_context = project / "context" / ".captain" / "editing-notes.md"
    hidden_context.parent.mkdir(parents=True)
    hidden_context.write_text("Keep the first scene quiet.\n")
    after = planning_reuse.cache_identity(*args)

    assert after["components"]["external_files"] != \
        before["components"]["external_files"]
    assert after["key"] != before["key"]


def test_context_symlink_directories_are_hashed(tmp_path):
    project = _project(tmp_path)
    context = project / "context"
    context.mkdir()
    target = project / "linked-context"
    target.mkdir()
    note = target / "editing-notes.md"
    note.write_text("Hold the quiet opening.\n")
    link = context / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        import pytest
        pytest.skip(f"directory symlinks unavailable: {exc}")

    found = set()
    planning_reuse._add_tree_files(context, found)

    assert note.resolve() in found


def test_reuse_requires_unchanged_recorded_output_and_all_step_artifacts(
        tmp_path):
    project = _project(tmp_path)
    step_output = project / "pipeline_output" / "steps" / "5_04_compile_manifest"
    step_output.mkdir(parents=True)
    (step_output / "output.json").write_text(json.dumps({"manifest": []}))
    (step_output / "assembly_manifest.json").write_text("manifest bytes")
    state = {}
    output = {"assembly_manifest": []}
    identity = {"format": planning_reuse.FORMAT, "key": "same",
                "components": {"inputs": "same"}}

    assert planning_reuse.record_success(
        state, "compile_manifest", identity, output, str(project)) is None
    assert planning_reuse.reusable(
        state, "compile_manifest", identity, output, str(project)) == (
            True, "identical inputs, code and outputs")

    (step_output / "assembly_manifest.json").write_text("changed bytes")
    assert planning_reuse.reusable(
        state, "compile_manifest", identity, output, str(project))[0] is False

    (step_output / "assembly_manifest.json").write_text("manifest bytes")
    assert planning_reuse.reusable(
        state, "compile_manifest", identity,
        {"assembly_manifest": ["changed"]}, str(project))[0] is False


def test_cleared_capability_output_can_be_restored_from_verified_export(
        tmp_path):
    project = _project(tmp_path)
    step_output = project / "pipeline_output" / "steps" / "5_04_compile_manifest"
    step_output.mkdir(parents=True)
    output = {"assembly_manifest": [{"clip_id": "clip_001"}]}
    (step_output / "output.json").write_text(
        json.dumps(output), encoding="utf-8")
    state = {}
    identity = {"format": planning_reuse.FORMAT, "key": "same",
                "components": {"inputs": "same"}}

    assert planning_reuse.record_success(
        state, "compile_manifest", identity, output, str(project)) is None

    restored = planning_reuse.recorded_output(str(project), "compile_manifest")

    assert restored == output
    assert planning_reuse.reusable(
        state, "compile_manifest", identity, restored, str(project)) == (
            True, "identical inputs, code and outputs")
