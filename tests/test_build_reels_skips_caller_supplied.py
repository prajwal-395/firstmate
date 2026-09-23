"""`build-reels` completes on a ready project instead of refusing.

The defect (PR #1346): as coded, `cmd_build_reels` walked every
operation each reels node owns, so on a ready project `reel.build`
completed and the loop then REFUSED at `reel.touchup`
(caller-supplied `spec` unbound) before `reel.ask`/`verify_reels`
ever ran. The loop is not the caller that supplies those arguments,
so it leaves caller-supplied operations out and runs the rest.

The step bodies that reach Resolve are stubbed at the module the
registry resolves - the loop, the requirement checks, the input
gathering, the refusal and the state recording all run for real, and
no Resolve, render or real reel build happens here.
"""
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import manage_project  # noqa: E402
from library.tools import operations  # noqa: E402
from library.tools import processes as processes_mod  # noqa: E402
from library.tools import requirements as _R  # noqa: E402
from library.tools.reel_proposal import (  # noqa: E402
    Approval, ReelMoment, proposal_path, write_proposal)
from library.tools.timeline_transcript import transcript_path  # noqa: E402


def _ready_project(root: Path) -> str:
    """A project with everything `reel.build` asks for, under tmp only."""
    (root / "pipeline_output").mkdir(parents=True, exist_ok=True)
    state = {"project_folder": str(root), "step_outputs": {}}
    state[_R._FORCE] = {
        "resolve_scripting": True,
        "face_detector": True,
        "reel_build_libraries": True,
    }
    (root / "pipeline_data.json").write_text(json.dumps(state), encoding="utf-8")
    path = transcript_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "segments": [], "segment_count": 0,
        "derived_from": {"duration_seconds": 0.0}}), encoding="utf-8")
    moment = ReelMoment(
        number=1, slug="a-witness", reason="a moment to build",
        timeline_start=10.0, timeline_end=40.0, approval=Approval.APPROVED)
    write_proposal(proposal_path(root), [moment],
                   {"derived_from": {"duration_seconds": 60.0}})
    (root / "project.yaml").write_text(
        "name: fixture\nresolve:\n"
        "  project_name: Fixture Project\n"
        "  timeline_name: Fixture Timeline\n", encoding="utf-8")
    return str(root)


def _args(project: str):
    return types.SimpleNamespace(
        project=project, skip_captions=False, only_reel=[],
        name_suffix="", allow_drop=[], supersede=[], retain=[],
        rebuild_all=False)


def test_the_loop_passes_a_ready_project_and_runs_what_follows_touchup(
        tmp_path, monkeypatch, capsys):
    """The regression: no refusal at the first caller-supplied op, and
    `reel.ask` plus `reel.verify` still run after it."""
    folder = _ready_project(tmp_path)
    ran = []

    build_mod = operations.load_step_module("step_7_01_build_reels")
    verify_mod = operations.load_step_module("step_7_02_verify_reels")
    monkeypatch.setattr(
        build_mod, "build_reels",
        lambda data: (ran.append("reel.build"), {
            "reel_build": {
                "timelines_built": ["Reel 01 - a-witness"],
                "plan_path": str(proposal_path(tmp_path))}})[1])
    monkeypatch.setattr(
        build_mod, "ask_reels",
        lambda data: (ran.append("reel.ask"), {"reel_asks": {}})[1])
    monkeypatch.setattr(
        verify_mod, "verify_reels",
        lambda data: (ran.append("reel.verify"),
                      {"reel_verification": {}})[1])

    manage_project.cmd_build_reels(_args(folder))  # exits on refusal

    # What follows the skipped ops still ran - named, not counted.
    assert "reel.ask" in ran
    assert "reel.verify" in ran
    # Nothing caller-supplied was driven: the loop is not their caller.
    reels_nodes = set(processes_mod.execution_order(processes_mod.REELS))
    caller_supplied = {op.name for op in operations.all()
                       if op.caller_supplied and op.owning_node in reels_nodes}
    assert caller_supplied, "the registry names no caller-supplied reel op"
    assert not (set(ran) & caller_supplied)
    # The run recorded the runner-driven outputs node by node.
    state = json.loads(
        (tmp_path / "pipeline_data.json").read_text(encoding="utf-8"))
    assert "reel_build" in state["step_outputs"]["build_reels"]
    assert "reel_verification" in state["step_outputs"]["verify_reels"]
    out = capsys.readouterr().err
    assert "reel.touchup: skipped (caller-supplied)" in out


def test_the_skipped_op_would_still_refuse_without_a_caller(tmp_path):
    """The skip is load-bearing: driven as the old loop drove it -
    with only the loop's own arguments - `reel.touchup` refuses with
    `spec` unbound while its requirements are satisfied."""
    folder = _ready_project(tmp_path)
    op = operations.get("reel.touchup")
    assert op.unmet(folder) == []
    result = op.execute(
        folder, skip_captions=False, only_reels=None,
        timeline_name_suffix="", allow_drops=None, supersede=None,
        retain=None, rebuild_all=False)
    assert result.refused
    assert result.unsatisfied == ()
    assert op.unbound_parameters(
        op._arguments(op.gather(folder), None)) == ("spec",)
