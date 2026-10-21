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
            {"id": "select_reels", "step_ref": "steps/select_reels"},
            {"id": "judge_reels", "step_ref": "steps/judge_reels"},
        ],
        "edges": [
            {"from": "analysis", "to": "left"},
            {"from": "analysis", "to": "right"},
            {"from": "left", "to": "compile_manifest"},
            {"from": "right", "to": "compile_manifest"},
            {"from": "select_reels", "to": "judge_reels"},
        ],
    }


def test_planning_nodes_are_the_edit_ancestors_of_manifest():
    nodes = planning_reuse.planning_nodes(
        _dag(),
        {"analysis": "preflight", "left": "edit", "right": "edit",
         "compile_manifest": "edit", "select_reels": "edit",
         "judge_reels": "edit"},
    )

    assert nodes == {"left", "right", "compile_manifest",
                     "select_reels", "judge_reels"}


def test_planning_nodes_can_still_select_one_destination():
    nodes = planning_reuse.planning_nodes(
        _dag(),
        {"analysis": "preflight", "left": "edit", "right": "edit",
         "compile_manifest": "edit", "select_reels": "edit",
         "judge_reels": "edit"},
        final_node="judge_reels",
    )

    assert nodes == {"select_reels", "judge_reels"}


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


def test_backfilled_transcript_evidence_envelope_does_not_break_reuse(
        tmp_path):
    project = _project(tmp_path)
    step_dir = tmp_path / "step"
    step_dir.mkdir()
    (step_dir / "step.py").write_text("print('step')\n")
    dag = _dag()
    base_inputs = {
        "timeline_transcript": {
            "segments": [{"text": "same words"}],
        },
    }
    args = (
        "left", dag, base_inputs,
        {"full_auto": "agent", "auto_mode": False}, step_dir,
        str(project), Path(__file__).resolve().parents[3],
    )

    before = planning_reuse.cache_identity(*args)
    with_envelope = {
        "timeline_transcript": {
            **base_inputs["timeline_transcript"],
            "evidence_envelope": {"coverage_status": "sampled"},
        },
    }
    after = planning_reuse.cache_identity(
        "left", dag, with_envelope, args[3], step_dir, str(project), args[6])
    changed_content = planning_reuse.cache_identity(
        "left", dag,
        {"timeline_transcript": {"segments": [{"text": "changed words"}]}},
        args[3], step_dir, str(project), args[6])

    assert after["key"] == before["key"]
    assert changed_content["key"] != before["key"]


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


# ── The code witness: what may change a planning key ────────────────
#
# The defect the scout measured (scout `scout-opt-planning-cost`, C4):
# the key hashed 599 files for `creative_direction`, 572 of them shared
# library modules, and ONE COMMENT LINE in
# `library/tools/analysis/object_segmentation.py` - a module with nothing
# to do with creative direction - changed it.  Every unrelated engine
# merge therefore re-paid the whole planning band: 231,346 tokens and
# about twenty-two minutes of bridge time per re-plan.


def _fake_repo(tmp_path):
    """A repository with the declared context modules and one step."""
    root = tmp_path / "repo"
    for relative in planning_reuse._CONTEXT_CODE:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# a module\n", encoding="utf-8")
    step = root / "library" / "steps" / "step_x"
    step.mkdir(parents=True)
    (step / "manifest.json").write_text(json.dumps({"skills": []}),
                                        encoding="utf-8")
    (step / "handoff.md").write_text("Choose a direction.\n",
                                     encoding="utf-8")
    return root, step


def test_an_unrelated_module_does_not_change_the_planning_code_digest(
        tmp_path):
    """The defect, pinned: an unrelated change must not re-pay the band."""
    root, step = _fake_repo(tmp_path)
    before = planning_reuse._code_digest(step, root)

    unrelated = root / "library" / "tools" / "unrelated.py"
    unrelated.write_text("# a change nobody reads\n", encoding="utf-8")

    assert planning_reuse._code_digest(step, root) == before


def test_a_declared_context_module_changes_the_planning_code_digest(
        tmp_path):
    """The other half: a change that shapes the question MUST move it."""
    root, step = _fake_repo(tmp_path)
    before = planning_reuse._code_digest(step, root)

    (root / "library" / "tools" / "context_views.py").write_text(
        "# a change that shapes the question\n", encoding="utf-8")

    assert planning_reuse._code_digest(step, root) != before


def test_a_module_reached_through_a_lazy_import_changes_the_digest(
        tmp_path):
    """The question-forming code calls into these at run time.

    A change in a module a context module imports INSIDE a function
    changes what the step asks, so it has to move the key.
    """
    root, step = _fake_repo(tmp_path)
    (root / "library" / "tools" / "context_views.py").write_text(
        "def build():\n    from library.tools import helper\n"
        "    return helper\n", encoding="utf-8")
    helper = root / "library" / "tools" / "helper.py"
    helper.write_text("# the helper\n", encoding="utf-8")
    before = planning_reuse._code_digest(step, root)

    helper.write_text("# the helper, changed\n", encoding="utf-8")

    assert planning_reuse._code_digest(step, root) != before


def test_the_code_witness_does_not_reach_the_whole_engine():
    """The witness is the modules that shape the QUESTION, not the engine.

    `object_segmentation` is the module the scout proved was in the key
    of a step that has nothing to do with segmentation.
    """
    repo_root = Path(__file__).resolve().parents[3]
    creative = repo_root / "library" / "steps" / "step_2_01_creative_direction"
    found = planning_reuse._code_files(creative, repo_root)

    segmentation = (repo_root / "library" / "tools" / "analysis"
                    / "object_segmentation.py").resolve()
    assert segmentation not in found, (
        "a module with nothing to do with creative direction is in its "
        "planning key: every unrelated merge re-pays the band")
    assert len(found) < 300, (
        f"the code witness is {len(found)} files - it is the whole engine "
        "again")
