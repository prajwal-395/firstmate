"""A step's stdout is its result, and a dependency must not be able to
write to it: the step points process-wide stdout at stderr and keeps the
real handle private for the result. Run in a SUBPROCESS, because the
guarantee is about a real process's file descriptors.
History (whisperx on 001's temporal_index): docs/evidence/step_stdout.md.
"""
import json
import subprocess
import sys
import textwrap
from pathlib import Path
import pytest
import os
from library.processes.edit_video.run_pipeline import generate_output_schema_text, validate_step_output
from library.tools.empty_table_guard import find_empty_tables
from library.tools.toon_serializer import json_to_toon


REPO_ROOT = Path(__file__).resolve().parents[3]
STEP = REPO_ROOT / "library" / "steps" / "step_1_04_temporal_index" / "step.py"


def _run(snippet: str) -> subprocess.CompletedProcess:
    """Import the step module and run `snippet` against its stdout guard."""
    program = textwrap.dedent(f"""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("step_1_04", r"{STEP}")
        step = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(step)
    """) + textwrap.dedent(snippet)
    return subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def test_no_dependency_write_to_stdout_corrupts_the_result():
    """The exact whisperx shape (a StreamHandler on sys.stdout, bound
    lazily AFTER the claim, which is why order must not matter), plain
    prints and raw writes (tqdm and friends), and a trailing print after
    the result: stdout carries exactly one JSON document, and everything
    else lands on stderr."""
    proc = _run("""
        step._claim_stdout()

        # Precisely what whisperx/log_utils.py:32 does, after the claim.
        import logging
        logger = logging.getLogger("pretend_whisperx")
        handler = logging.StreamHandler(sys.stdout)
        assert handler.stream is not sys.__stdout__, "handler holds real stdout"
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.info("No language specified, language will be detected")
        logger.warning("No active speech found in audio")
        print("Fetching 9 files: 100%")
        sys.stdout.write("raw write\\n")

        step._emit({"temporal_event_indices": [1, 2, 3], "total_failed": 0})
        print("goodbye")
    """)

    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "temporal_event_indices": [1, 2, 3], "total_failed": 0}
    for line in ("No active speech found", "Fetching 9 files", "raw write",
                 "goodbye"):
        assert line in proc.stderr, line


def test_without_the_claim_the_result_would_be_corrupted():
    """The control. Shows these tests can fail, and how the run failed.

    This is the pre-fix behaviour, asserted deliberately: it is the thing
    the guard prevents, and without it the tests above could pass for the
    wrong reason.
    """
    proc = _run("""
        import logging
        logger = logging.getLogger("pretend_whisperx")
        logger.addHandler(logging.StreamHandler(sys.stdout))
        logger.setLevel(logging.INFO)
        logger.info("INFO - No language specified")
        import json as _json
        _json.dump({"ok": True}, sys.stdout)
    """)

    assert proc.returncode == 0, proc.stderr
    with pytest.raises(json.JSONDecodeError):
        json.loads(proc.stdout)


# --------------------------------------------------------------------------
# From test_step_subprocess_large_output.py
#
# A step's result must survive being bigger than a pipe buffer, with
# traffic on BOTH pipes at once - the shape that deadlocked the runner on
# project 001's semantic_analysis. History: docs/evidence/step_subprocess.md.

sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    _run_step_subprocess,
)

def _write_step(tmp_path: Path, body: str) -> Path:
    step = tmp_path / "step.py"
    step.write_text(textwrap.dedent(body), encoding="utf-8")
    return step


BIG_BOTH_STEP = """
    import json, sys
    data = json.loads(sys.stdin.read())
    for i in range(data["rows"]):
        print(f"progress line {i} " + "y" * 40, file=sys.stderr)
    json.dump({"rows": list(range(data["rows"]))}, sys.stdout)
"""


def test_large_stderr_and_large_stdout_together(tmp_path):
    """Both pipes over the buffer at once - the real analysis-phase shape.

    A long vision pass logs steadily while building a large result. If
    either pipe is left unserviced while the other is read, this hangs.
    """
    step = _write_step(tmp_path, BIG_BOTH_STEP)

    code, out, err = _run_step_subprocess(
        [sys.executable, str(step)], {"rows": 3000}, "both")

    assert code == 0
    assert json.loads(out)["rows"][-1] == 2999
    assert err.count("progress line") == 3000


# --------------------------------------------------------------------------
# From test_step_timeout.py
#
# A step subprocess must not be killed at somebody's estimate.
#
# Every step ran under a hardcoded `timeout=600`. That is the same number
# the DAG carries as `semantic_analysis`'s `estimated_duration_seconds` - an
# estimate used as a deadline. On project 001 (17 clips, 13.5 minutes of
# footage) the vision pass needs 45 to 90 minutes; it was killed four clips
# in and then RETRIED, because `_is_transient` matches "timed out". The
# same ceiling sits under `temporal_index`, `render_subtitles` and `render`,
# all of which exceed ten minutes on real footage. That is why no project on
# disk has ever had a completed run.
#
# The remaining timeout exists to break a wedge, not to enforce an estimate.

sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video import run_pipeline  # noqa: E402


def test_the_default_ceiling_is_hours_not_minutes():
    assert run_pipeline.step_timeout_seconds() >= 3600, (
        "a step ceiling under an hour kills the vision pass on any real "
        "project's footage"
    )


def test_stderr_is_streamed_and_still_reaches_the_error(tmp_path, capsys):
    """A 90-minute step held behind capture_output looks like a wedge."""
    script = tmp_path / "loud.py"
    script.write_text(
        "import sys\n"
        "print('working on it', file=sys.stderr, flush=True)\n"
        "sys.exit(3)\n"
    )
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline.run_deterministic_step(str(script), {})
    assert "working on it" in str(excinfo.value)
    assert "working on it" in capsys.readouterr().err


# --------------------------------------------------------------------------
# From test_runner_library_paths.py
#
# The runner's own imports must resolve, and the asset libraries must land.
#
# `run_pipeline.py` put ONE entry on sys.path - `library/tools` - and then
# imported two different ways off it:
#
#     from model_lifecycle import unload_all     # works: library/tools is on the path
#     from tools.paths import sfx_library_path   # ImportError: library is NOT
#
# All three `from tools.paths import ...` sites sat inside `except
# ImportError` handlers, two of which were `pass`. So PIPELINE_SFX_LIBRARY
# and PIPELINE_MUSIC_LIBRARY never reached a run. Projects whose
# `pipeline_data.json` already carried an `sfx_library` from an earlier era
# kept working, which is why it survived: a genuinely fresh project failed
# at step 0.01 with "No sfx_library path provided" while the environment had
# a valid library the whole time.

sys.path.insert(0, str(REPO_ROOT))


def test_both_import_styles_the_runner_uses_resolve():
    """Importing the module must make both of its import styles work."""
    import importlib

    # The bare-module style, off library/tools.
    importlib.import_module("model_lifecycle")
    # The package style, off library. This one was broken.
    paths = importlib.import_module("tools.paths")
    assert hasattr(paths, "sfx_library_path")
    assert hasattr(paths, "music_library_path")


def test_fresh_state_picks_up_the_env_asset_libraries(tmp_path, monkeypatch):
    """A project with no pipeline_data.json still gets the shared libraries."""
    sfx = tmp_path / "sfx library"
    music = tmp_path / "music"
    sfx.mkdir()
    music.mkdir()

    monkeypatch.setitem(os.environ, "PIPELINE_SFX_LIBRARY", str(sfx))
    monkeypatch.setitem(os.environ, "PIPELINE_MUSIC_LIBRARY", str(music))

    # paths.py resolves the env vars at import time, so it has to be reloaded
    # for the monkeypatched values to take effect.
    import importlib

    import tools.paths as paths_mod
    importlib.reload(paths_mod)

    project = tmp_path / "project"
    project.mkdir()
    state = run_pipeline.load_pipeline_state(str(project))

    assert state["sfx_library"] == str(sfx), (
        "load_pipeline_state did not inject PIPELINE_SFX_LIBRARY; step 0.01 will fail "
        "any project whose state does not already carry a path."
    )
    assert state["music_library"] == str(music)

    importlib.reload(paths_mod)


def test_missing_paths_module_is_not_swallowed(monkeypatch, tmp_path):
    """If tools.paths ever stops resolving, the run must fail, not continue.

    The old code caught ImportError and carried on with no asset libraries.
    That is what turned a broken sys.path into a step-0.01 mystery.
    """
    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def blocked(name, *args, **kwargs):
        if name == "tools.paths":
            raise ImportError("blocked for the test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", blocked)
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(ImportError):
        run_pipeline.load_pipeline_state(str(project))


# --------------------------------------------------------------------------
# From test_project_config_reread.py
#
# Finding 29: a project declaration changed after the first run never
# reaches a step again.
#
# `run_pipeline.load_pipeline_state` loads project.yaml into
# `state["project_config"]` only `if "project_config" not in state`, and
# state persists in pipeline_data.json. On the scout's B6 run
# project.yaml went `target_duration_seconds: 60 -> 30` and the rerun's
# 2.02 gate still said "declared target zone (54.0-66.0s, target 60.0s)".
# Silent - nothing said the declaration on disk differs from the one in
# force.
#
# The fix: re-read the declaration every run, and name the difference on
# stderr when what is on disk is not what the last run used.

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


# --------------------------------------------------------------------------
# From test_schema_injection.py

def test_validate_step_output_warnings():
    """Verify validate_step_output produces warnings without raising errors."""
    manifest = {
        "interface": {
            "outputs": [
                {"name": "score", "type": "int", "required": True},
                {"name": "notes", "type": "str", "required": False}
            ]
        }
    }
    
    # 1. Valid output
    valid_output = {"score": 8, "notes": "good"}
    issues = validate_step_output("test_node", valid_output, manifest)
    assert not issues, "Valid output should have no issues"
    
    # 2. Missing required
    missing_req = {"notes": "good"}
    issues = validate_step_output("test_node", missing_req, manifest)
    assert any("missing required key: 'score'" in i for i in issues), issues
    
    # 3. Missing optional
    missing_opt = {"score": 8}
    issues = validate_step_output("test_node", missing_opt, manifest)
    assert not issues, "Missing optional should not produce an issue"
    
    # 4. Wrong type
    wrong_type = {"score": "eight", "notes": "good"}
    issues = validate_step_output("test_node", wrong_type, manifest)
    assert any("expected type int, got str" in i for i in issues), issues
    
    # 5. Extra fields
    extra = {"score": 8, "extra_field": True}
    issues = validate_step_output("test_node", extra, manifest)
    assert any("unexpected extra fields: extra_field" in i for i in issues), issues


# --------------------------------------------------------------------------
# From test_pipeline_validation.py

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

def test_bridge_empty_inputs():
    """Test that feeding empty {} as inputs to each bridge raises ValueError."""
    # List of bridge/post_bridge scripts that were modified
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'library', 'steps'))
    scripts = [
        "step_2_02_speech_sequence/bridge.py",
        "step_2_05_mesh_spine/post_bridge.py",
        "step_3_02_select_broll/bridge.py",
        "step_4_02_plan_transitions/post_bridge.py",
        "step_4_03_plan_vfx/post_bridge.py",
        "step_4_04_plan_sfx/post_bridge.py",
    ]
    
    for script_rel in scripts:
        script_path = os.path.join(base_dir, script_rel)
        assert os.path.exists(script_path), f"Script not found: {script_path}"
        
        env = os.environ.copy()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        env["PYTHONPATH"] = project_root
        
        result = subprocess.run(
            ["python3", script_path],
            input="{}",
            capture_output=True,
            text=True,
            env=env
        )
        
        # Depending on how the exception is handled:
        # Some scripts have a try-except that prints json with "error", others just crash.
        # Check either a non-zero exit code or "error" in stdout.
        if result.returncode == 0:
            try:
                out = json.loads(result.stdout)
                assert "error" in out
                assert "missing required keys" in out["error"] or "missing required input keys" in out["error"]
            except json.JSONDecodeError:
                assert False, f"Script {script_path} exited with 0 but invalid JSON: {result.stdout}"
        else:
            assert "ValueError" in result.stderr
            assert "missing required" in result.stderr


def test_validate_step_output():
    manifest = {
        "interface": {
            "outputs": [
                {"name": "req_key", "required": True, "type": "dict"},
                {"name": "opt_key", "required": False, "type": "list"}
            ]
        }
    }
    
    # Valid output (non-empty required values)
    issues = validate_step_output("test_node", {"req_key": {"data": 1}, "opt_key": []}, manifest)
    assert not issues
    
    # Missing optional key is OK
    issues = validate_step_output("test_node", {"req_key": {"data": 1}}, manifest)
    assert not issues

    # Semantically empty required output
    issues = validate_step_output("test_node", {"req_key": {}, "opt_key": []}, manifest)
    assert any("semantically empty" in issue for issue in issues)
    
    # Missing required key
    issues = validate_step_output("test_node", {"opt_key": []}, manifest)
    assert any("missing required key: 'req_key'" in issue for issue in issues)
        
    # Wrong type
    issues = validate_step_output("test_node", {"req_key": []}, manifest)
    assert any("expected type dict, got list" in issue for issue in issues)


# --------------------------------------------------------------------------
# From test_validate_verdict_is_not_hollow.py
#
# D10 regression: a considered `fail` verdict is not hollow output.
#
# `validate` fails the run under its own classification
# (`ValidationFailed`), never borrowing `HollowOutput`'s "produced no usable
# output" wording. History: docs/evidence/validate_verdict.md.

sys.path.insert(0, str(REPO_ROOT))

from library.processes.edit_video.run_pipeline import (
    check_output_is_real,
    check_validation_verdict,
)


def _validate_output(status, n_issues):
    """A `validate` step output shaped like the bridge's real one."""
    issues = [f"issue_{i}" for i in range(n_issues)]
    # The same expression `bridge.py` builds the summary from, not the
    # report's literal "2 issue(s) found".
    summary = f"{len(issues)} issue(s) found"
    return {"validation_result": {
        "status": status,
        "summary": summary,
        "distribution_ready": status == "pass",
        "all_issues": issues,
    }}


def _expected_detail(output):
    """The detail line, built from the verdict's own fields."""
    v = output["validation_result"]
    return f"Validation outcome was not successful: {v['status']} - {v['summary']}"


def test_a_considered_verdict_is_not_hollow_but_still_fails():
    """The hollow-output gate stays silent on a complete verdict, while
    the verdict gate still fails the run, under its own name."""
    for status, n_issues in (("fail", 2), ("undetermined", 1)):
        output = _validate_output(status, n_issues)
        assert check_output_is_real("validate", output) == []
        assert check_validation_verdict("validate", output) == [
            _expected_detail(output)]


def test_verdict_gate_only_reads_validate():
    """A fail-shaped payload on any other node is not a verdict."""
    assert check_validation_verdict("render", _validate_output("fail", 2)) == []


# --------------------------------------------------------------------------
# From test_empty_table_guard.py
#
# A table arriving with zero rows is reported on the run that sent it.
#
# Two occurrences of the same defect, both found by an audit weeks later:
# `cuts_toon` (#218) and `sfx_candidates_toon` (#223). The guard exists so
# the third one surfaces at the moment it happens.
#
# These tests pin what it catches, what it deliberately does not catch,
# and that it never fails a run: an empty table can be the honest answer,
# and a gate that fires on correct output is not coverage (AGENTS.md
# 10.4).

# ── What it catches ───────────────────────────────────────────────────


def test_it_catches_the_defect_that_shipped_twice():
    """The literal bytes out of project 001's archived plan_sfx request."""
    context = (
        "available_sfx_types:\n"
        "  [0] whoosh\n"
        "  [1] bass_impact\n"
        "sfx_candidates_toon: |\n"
        "  [0]{segment_id,text,action_sfx_suggested}\n"
        "\n"
        "project_folder: /tmp/001\n"
    )
    prompt = "The `sfx_candidates_toon` table provides a summarized list."

    found = find_empty_tables(context, prompt)
    assert [t.key for t in found] == ["sfx_candidates_toon"]
    assert found[0].columns == ("segment_id", "text", "action_sfx_suggested")
    assert found[0].named_in_prompt is True


def test_an_empty_list_input_is_caught_too():
    """`json_to_toon` writes `[]`, not a header, for an empty list.

    It is the same silence in a different spelling: a step told to read
    `b_roll_assignments` is given nothing and no note saying so.
    """
    context = json_to_toon({"b_roll_assignments": [], "project_fps": 30.0})
    found = find_empty_tables(context, "consider each b_roll_assignments entry")
    assert [(t.key, t.form) for t in found] == [("b_roll_assignments", "[]")]
    assert found[0].columns == ()
    assert found[0].named_in_prompt is True


# ── The runner really calls it ────────────────────────────────────────


def test_the_runner_reports_the_empty_table_before_it_asks(tmp_path, capsys):
    """Driven through `present_llm_step`, not asserted off the source.

    That function is where the context and the prompt are both in hand,
    and it is the one place every LLM step passes through. Mock mode
    reads a canned answer off disk, so this exercises the real call
    without an LLM.
    """
    from library.processes.edit_video.run_pipeline import present_llm_step

    project = tmp_path / "project"
    bak = project / "pipeline_output" / "llm_responses_bak"
    bak.mkdir(parents=True)
    (bak / "plan_sfx.json").write_text(
        json.dumps({"sfx_creative": [{"spine_block_position": 1,
                                      "sfx_type": "whoosh",
                                      "volume_db": -18,
                                      "rationale": "marks the cut"}]}),
        encoding="utf-8",
    )
    prompt = tmp_path / "handoff.md"
    prompt.write_text("Read the `sfx_candidates_toon` table.\n",
                      encoding="utf-8")

    present_llm_step(
        str(prompt),
        {"project_folder": str(project),
         "sfx_candidates_toon": "[0]{segment_id,text,action_sfx_suggested}\n"},
        "plan_sfx",
        {"interface": {"llm_outputs": [{"name": "sfx_creative",
                                        "type": "list"}]},
         "context_fields": ["sfx_candidates_toon"]},
        full_auto="mock",
        bridge_supplied={"sfx_candidates_toon"},
    )

    err = capsys.readouterr().err
    assert "[empty-table] plan_sfx" in err, (
        f"the runner sent an empty named table without saying so:\n{err}"
    )
    assert "sfx_candidates_toon" in err
    assert err.count("[empty-table]") == 1, (
        "the report fired more than once for one step"
    )
