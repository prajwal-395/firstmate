"""`REN_RESULT` on the leftover sites (LE-03 follow-up).

LE-03 wired the front door, `manage_project.py`'s guard, `run_reels`'
`RenRefusal` paths and `footage_query`. This file pins what was left:
the other `python -m` verb modules, the inner exit-4 catch sites, the
`result.refused` string paths (which carry no `RenRefusal` object), and
codex's reliance on the runtime default.

Sizing: one behavioral test per refusal SHAPE (guard, internal catch,
string path, summary path, exit-2 variant), unit tests for the two
converter objects (`OperationResult.as_refusal`,
`run_pipeline._summary_refusal`), and one wiring-inventory test that
parametrizes every newly covered module - thirty-three subprocess
runs would fry the CPU for what the inventory pins statically.
"""
import contextlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools import operations  # noqa: E402
from library.tools.ren_refusal import (  # noqa: E402
    REFUSAL_EXIT_CODE,
    RenRefusal,
    refusal_result_dict,
)

ROOT = Path(__file__).resolve().parents[3]


def _result_lines(err: str) -> list:
    return [line for line in err.splitlines()
            if line.startswith("REN_RESULT ")]


# ── OperationResult.as_refusal ───────────────────────────────


def _op_scope():
    return operations.scope_mod.project()


def test_as_refusal_carries_teach_fix_and_code():
    """The string path converts without inventing: `what` is the
    structured reason, `why` the teacher's prose, `fix` the teach
    site's own way out."""
    result = operations.OperationResult(
        operation="reel.ask", legacy_node="select_reels",
        scope=_op_scope(), status=operations.REFUSED,
        error="an operation runs ALONE, with no model to ask",
        fix="run select_reels so the runner asks the model for it")
    refused = result.as_refusal()
    assert isinstance(refused, RenRefusal)
    payload = refusal_result_dict(refused)
    assert payload == {"ok": False, "exit": 4, **refused.to_dict()}
    assert payload["code"] == "REN_OPERATION_REFUSED"
    assert "reel.ask refused" in payload["what"]
    assert payload["why"] == "an operation runs ALONE, with no model to ask"
    assert payload["fix"] == \
        "run select_reels so the runner asks the model for it"


def test_as_refusal_falls_back_to_prose_without_a_fix():
    """Results built before the `fix` field existed still convert:
    the prose always names the way out, so it doubles as the fix
    rather than failing the conversion."""
    result = operations.OperationResult(
        operation="o", legacy_node="n", scope=_op_scope(),
        status=operations.REFUSED, error="run the DAG first")
    refused = result.as_refusal()
    assert refused.fix == "run the DAG first"


def test_as_refusal_refuses_completions():
    scope = _op_scope()
    done = operations.OperationResult(
        operation="o", legacy_node="n", scope=scope,
        status=operations.COMPLETED, payload={"x": 1})
    with pytest.raises(operations.OperationError):
        done.as_refusal()


def test_teach_missing_returns_message_and_fix():
    """`_teach`'s fix names the producers the prose names - derived
    from the same requirement data, never a second source."""
    from library.tools import requirements
    entries = [
        types.SimpleNamespace(
            requirement=types.SimpleNamespace(
                name="audio_spine", produced_by=("mesh_spine",),
                kind=requirements.KIND_STATE_KEY),
            satisfaction=types.SimpleNamespace(
                reason="no spine on file",
                produced_by=("mesh_spine",))),
        types.SimpleNamespace(
            requirement=types.SimpleNamespace(
                name="orphan_key", produced_by=(),
                kind=requirements.KIND_STATE_KEY),
            satisfaction=types.SimpleNamespace(
                reason="nothing makes it", produced_by=())),
    ]
    op = operations.get("reel.ask")
    message, fix = op._teach(entries)
    assert "no spine on file" in message and "nothing makes it" in message
    assert "audio_spine" in fix and "mesh_spine" in fix
    assert "orphan_key" in fix
    assert "no step declares a producer" in fix


def test_teach_prompt_and_unbound_fixes_mirror_their_prose():
    op = operations.get("reel.ask")
    message, fix = op._teach_prompt()
    assert op.legacy_node in message and op.legacy_node in fix
    message, fix = op._teach_unbound(("stored_plan",), None)
    assert "--set stored_plan=@stored_plan.json" in message
    assert "--set stored_plan=@stored_plan.json" in fix


# ── run_pipeline._summary_refusal ────────────────────────────


def test_summary_refusal_reconstructs_a_caught_refusal():
    """A summary carrying the caught refusal's `to_dict` rebuilds it
    exactly - code, fix and recovery class included."""
    from library.processes.edit_video import run_pipeline as runner
    original = RenRefusal("the profile names no step", "it names nothing",
                          "point it at a declared profile",
                          code="REN_PROFILE_REFUSED",
                          recovery_class="needs_question", blocker="insufficient_evidence")
    summary = {"status": "REFUSED", "reason": "the profile names no step",
               "refusal": original.to_dict()}
    rebuilt = runner._summary_refusal(summary)
    assert rebuilt.to_dict() == original.to_dict()


def test_summary_refusal_with_fix_only_names_first_line():
    from library.processes.edit_video import run_pipeline as runner
    summary = {"status": "REFUSED",
               "reason": "first line\nsecond line",
               "fix": "do the thing"}
    rebuilt = runner._summary_refusal(summary)
    assert rebuilt.what == "first line"
    assert rebuilt.fix == "do the thing"
    assert rebuilt.code == "REN_RUN_REFUSED"


# ── -m verb modules: behavioral representatives ─────────────


def test_reel_state_unknown_reel_emits_one_line(tmp_path, monkeypatch,
                                                capsys):
    """An internal (non-guard) catch site: `ren reels --reel` names
    nothing recorded."""
    from library.tools import reel_state
    monkeypatch.setenv("REN_OUTPUT", "json")
    monkeypatch.setattr(reel_state, "reels_state",
                        lambda project, only_reel=None: {"reels": []})
    rc = reel_state.main([str(tmp_path), "--reel", "99"])
    assert rc == REFUSAL_EXIT_CODE == 4
    err = capsys.readouterr().err
    assert "ren: refused - " in err
    lines = _result_lines(err)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["ok"] is False and payload["exit"] == 4
    assert "99" in payload["what"]
    assert payload["fix"]


def test_take_pick_missing_proposal_emits_exit_2_line(tmp_path, monkeypatch,
                                                      capsys):
    """The exit-2 variant: the payload's exit matches the process's."""
    from library.tools import take_pick_preview
    monkeypatch.setenv("REN_OUTPUT", "json")
    rc = take_pick_preview.main([str(tmp_path), "--reel", "21"])
    assert rc == 2
    lines = _result_lines(capsys.readouterr().err)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["ok"] is False and payload["exit"] == 2
    assert "ren propose" in payload["fix"]


def test_run_wait_unknown_project_emits_one_line(monkeypatch, capsys):
    from library.tools import run_wait
    monkeypatch.setenv("REN_OUTPUT", "json")
    rc = run_wait.main(["no-such-project-xyz"])
    assert rc == REFUSAL_EXIT_CODE == 4
    lines = _result_lines(capsys.readouterr().err)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["exit"] == 4 and payload["fix"]


def test_operations_cli_unknown_operation_emits_one_line(tmp_path,
                                                          monkeypatch,
                                                          capsys):
    monkeypatch.setenv("REN_OUTPUT", "json")
    rc = operations.main(["no.such.op", "--project", str(tmp_path)])
    assert rc == REFUSAL_EXIT_CODE == 4
    lines = _result_lines(capsys.readouterr().err)
    assert len(lines) == 1
    assert json.loads(lines[0][len("REN_RESULT "):])["exit"] == 4


def test_operations_cli_refused_result_emits_one_line(tmp_path, monkeypatch,
                                                       capsys):
    """The `operations` CLI string path: `result.refused` with no
    `RenRefusal` object still ends with the data line."""
    from library.tools import provenance
    refused = operations.OperationResult(
        operation="reel.ask", legacy_node="select_reels",
        scope=_op_scope(), status=operations.REFUSED,
        error="an operation runs ALONE",
        fix="run select_reels so the runner asks")
    fake_op = types.SimpleNamespace(
        name="reel.ask", check_scope=lambda where: None,
        execute=lambda *args, **kwargs: refused)
    monkeypatch.setenv("REN_OUTPUT", "json")
    monkeypatch.setattr(operations, "get", lambda name: fake_op)
    monkeypatch.setattr(provenance, "observing_operation",
                        lambda *args, **kwargs: contextlib.nullcontext())
    rc = operations.main(["reel.ask", "--project", str(tmp_path)])
    assert rc == REFUSAL_EXIT_CODE == 4
    err = capsys.readouterr().err
    assert "REFUSED: reel.ask" in err
    lines = _result_lines(err)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["code"] == "REN_OPERATION_REFUSED"
    assert payload["fix"] == "run select_reels so the runner asks"


def test_run_reels_refused_result_emits_one_line(tmp_path, monkeypatch,
                                                  capsys):
    """The brief's named site: the reels runner's `result.refused`
    branch emits through `as_refusal`."""
    from library.processes.reels import run_reels
    from library.tools import (assignment_spine, capabilities,
                               project_format, provenance, resolve_lock)
    refused = operations.OperationResult(
        operation="reel.ask", legacy_node="select_reels",
        scope=_op_scope(), status=operations.REFUSED,
        error="an operation runs ALONE",
        fix="run select_reels so the runner asks")
    fake_op = types.SimpleNamespace(
        name="reel.ask", caller_supplied=False,
        execute=lambda *args, **kwargs: refused)
    monkeypatch.setenv("REN_OUTPUT", "json")
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda folder: None)
    monkeypatch.setattr(assignment_spine, "begin_build",
                        lambda folder, reels: object())
    monkeypatch.setattr(assignment_spine, "end_act",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *args, **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(capabilities, "run_order",
                        lambda process: [types.SimpleNamespace(id="x")])
    monkeypatch.setattr(operations, "get", lambda spec_id: fake_op)
    monkeypatch.setattr(provenance, "observing_operation",
                        lambda *args, **kwargs: contextlib.nullcontext())
    monkeypatch.setattr(run_reels, "record_project_output",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(run_reels, "commit_run_tail",
                        lambda folder: None)
    monkeypatch.setattr(run_reels, "print_digest",
                        lambda *args, **kwargs: None)
    args = types.SimpleNamespace(
        skip_captions=False, only_reel=[], name_suffix="",
        allow_drop=[], accept_editor_changes=[], supersede=[],
        retain=[], rebuild_all=False, placement_mode="auto", json=False)
    rc = run_reels.run(str(tmp_path), args)
    assert rc == REFUSAL_EXIT_CODE == 4
    err = capsys.readouterr().err
    assert "REFUSED: reel.ask" in err
    lines = _result_lines(err)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["code"] == "REN_OPERATION_REFUSED"
    assert payload["fix"] == "run select_reels so the runner asks"


def test_run_pipeline_refused_summary_emits_one_line(tmp_path, monkeypatch,
                                                      capsys):
    """The edit runner's REFUSED-summary branch emits from the
    summary's own `fix` - never invented in `main`."""
    from library.processes.edit_video import run_pipeline as runner
    from library.tools import native_refresh, project_format
    monkeypatch.setenv("REN_OUTPUT", "json")
    monkeypatch.setattr(
        sys, "argv", ["run_pipeline.py", "--project", str(tmp_path)])
    monkeypatch.setattr(project_format, "ensure_project_format",
                        lambda folder: None)
    monkeypatch.setattr(runner, "run_pipeline",
                        lambda *args, **kwargs: {"status": "REFUSED",
                                                 "reason": "first line\nrest",
                                                 "fix": "do the thing"})
    monkeypatch.setattr(runner, "notify_run_end",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(native_refresh, "ensure_current",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_announce_native_refresh_wait",
                        lambda folder: None)
    monkeypatch.setattr(runner, "_clear_native_refresh_wait",
                        lambda folder: None)
    with pytest.raises(SystemExit) as done:
        runner.main()
    assert done.value.code == REFUSAL_EXIT_CODE == 4
    lines = _result_lines(capsys.readouterr().err)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["code"] == "REN_RUN_REFUSED"
    assert payload["what"] == "first line"
    assert payload["fix"] == "do the thing"


def test_run_pipeline_caught_refusal_emits_one_line(tmp_path, monkeypatch,
                                                     capsys):
    """The edit runner's `RenRefusal` catch (e.g. the format gate)
    emits like every other catch site."""
    from library.processes.edit_video import run_pipeline as runner
    from library.tools import project_format
    monkeypatch.setenv("REN_OUTPUT", "json")
    monkeypatch.setattr(
        sys, "argv", ["run_pipeline.py", "--project", str(tmp_path)])
    monkeypatch.setattr(runner, "notify_run_end",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_announce_native_refresh_wait",
                        lambda folder: None)
    monkeypatch.setattr(runner, "_clear_native_refresh_wait",
                        lambda folder: None)

    def _refuse(folder):
        raise RenRefusal("this project is too old", "it predates formats",
                         "migrate it first: ren organize <project>", blocker="unsupported_operation")
    monkeypatch.setattr(project_format, "ensure_project_format", _refuse)
    with pytest.raises(SystemExit) as done:
        runner.main()
    assert done.value.code == REFUSAL_EXIT_CODE == 4
    lines = _result_lines(capsys.readouterr().err)
    assert len(lines) == 1
    assert json.loads(lines[0][len("REN_RESULT "):])["exit"] == 4


def test_override_error_is_a_refusal_with_a_code():
    """`--override` naming nothing closed is a refusal now, still a
    `ValueError` for every existing catcher, and the old message
    words survive."""
    from library.tools import requirements
    pool = requirements.all_requirements()
    closed = next(r.name for r in pool if not r.overridable)
    with pytest.raises(requirements.OverrideError, match="not overridable"):
        requirements.assert_overrides_are_real([closed], pool)
    with pytest.raises(requirements.OverrideError,
                       match="names no requirement"):
        requirements.assert_overrides_are_real(["no-such-requirement"],
                                               pool)
    try:
        requirements.assert_overrides_are_real([closed], pool)
    except requirements.OverrideError as refused:
        assert isinstance(refused, ValueError)
        assert refused.code == "REN_OVERRIDE_REFUSED"
        assert refused.fix
    else:  # pragma: no cover - the raise above already proved it raises
        raise AssertionError("unreachable")


# ── manage_project inner sites: one behavioral, rest inventoried ─


def test_notes_add_refusal_emits_one_line(tmp_path):
    """An inner exit-4 site (past the main guard's reach): `ren notes
    add` with nothing delivered refuses with prose AND the line."""
    env = dict(os.environ, REN_OUTPUT="json")
    done = subprocess.run(
        [sys.executable, "manage_project.py", "notes", "add",
         str(tmp_path), "--at", "0:12", "a note"],
        capture_output=True, encoding="utf-8", cwd=str(ROOT),
        env=env, check=False)
    assert done.returncode == 4
    assert "ren: refused - " in done.stderr
    lines = _result_lines(done.stderr)
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["ok"] is False and payload["exit"] == 4
    assert payload["fix"]


# ── wiring inventory: every newly covered site stays covered ──

VERB_MODULES = [
    "library/tools/taste_profile.py",
    "library/tools/local_ci/__main__.py",
    "library/tools/series_registry.py",
    "library/tools/project_work_state.py",
    "library/tools/pending_asks.py",
    "library/tools/assignment_interaction.py",
    "library/tools/assignment_view.py",
    "library/tools/run_wait.py",
    "library/tools/operations.py",
    "library/tools/perf_ledger.py",
    "library/tools/progress.py",
    "library/tools/edit_spec.py",
    "library/tools/revise.py",
    "library/tools/resolve_axi.py",
    "library/tools/reel_preview.py",
    "library/tools/round_page.py",
    "library/tools/creative_history.py",
    "library/tools/feedback_outcomes.py",
    "library/tools/reel_state.py",
    "library/tools/timeline_shadow.py",
    "library/tools/resolved/__main__.py",
    "library/tools/shadow_watcher.py",
    "library/tools/qualification_project.py",
    "library/tools/project_model_cli.py",
    "library/tools/computer_use/__main__.py",
    "library/tools/resolvefx_effects.py",
    "library/tools/footage_analysis.py",
    "library/tools/memory_export.py",
    "library/tools/retrieval_eval.py",
    "library/tools/eval_harness.py",
    "library/tools/worker_manifest.py",
    "library/tools/fabric_scheduler.py",
    "library/tools/event_log.py",
    "library/tools/take_pick_preview.py",
]


@pytest.mark.parametrize("relative", VERB_MODULES)
def test_verb_module_wires_ren_result(relative):
    """The LE-03 contract per verb module: `main` defaults the flag
    for direct `-m` use, and the `__main__` guard ends a refusal and
    an uncaught error with the data line. Reads source so all
    thirty-three stay covered without thirty-three subprocesses."""
    source = (ROOT / relative).read_text(encoding="utf-8")
    assert "ensure_harness_output_env" in source, relative
    guard = source.split('if __name__ == "__main__":', 1)[1]
    assert "emit_refusal_result" in guard, relative
    assert "report_unexpected" in guard, relative


def test_manage_project_inner_sites_all_emit():
    """Every `render()` + exit-4 site inside `manage_project.py`
    (which the main guard never sees) ends with the data line."""
    lines = (ROOT / "manage_project.py").read_text(
        encoding="utf-8").splitlines()
    missing = []
    for index, line in enumerate(lines):
        if ".render(), file" not in line:
            continue
        window = "\n".join(lines[index:index + 5])
        if ("sys.exit(REFUSAL" in window
                and "emit_refusal_result" not in window):
            missing.append(index + 1)
    assert missing == []


def test_run_reels_refused_branches_emit():
    """Both `run_reels` refusal branches (object and string path)
    end with the data line."""
    source = (ROOT / "library/processes/reels/run_reels.py").read_text(
        encoding="utf-8")
    assert source.count("emit_refusal_result") >= 3


def test_run_pipeline_refused_paths_emit():
    """The edit runner's catch, its REFUSED-summary branch and its
    `__main__` guard all end with the data line."""
    source = (ROOT / "library/processes/edit_video/run_pipeline.py").read_text(
        encoding="utf-8")
    assert "ensure_harness_output_env" in source
    assert "emit_refusal_result(_summary_refusal(" in source
    assert '"refusal": exc.to_dict()' in source
