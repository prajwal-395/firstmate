"""One reels request, verb by verb, leaves one assignment that says what happened.

The verbs a user runs (`ren propose`, `ren build`, `ren sign-off`,
`ren deliver`, `ren edit`) write the assignment record
(`library/tools/assignment_spine.py`); `ren work-state` and
`ren assignment` read it back. Resolve, the capability loop and the render
are test doubles; the project lives under `tmp_path`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
REEL = "Reel 01 - pricing"


def _records(project: Path) -> list[dict]:
    directory = project / "pipeline_output" / "llm_requests" / "assignments"
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))] \
        if directory.is_dir() else []


def _proposal_document(approval: str) -> dict:
    from library.tools.reel_proposal import PROPOSAL_FORMAT, ReelMoment

    moment = ReelMoment(number=1, slug="pricing", reason="the price lands",
                        timeline_start=10.0, timeline_end=40.0)
    data = moment.as_dict()
    data["approval"] = approval
    return {"format": PROPOSAL_FORMAT, "moments": [data]}


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "pricing-show"
    root.mkdir()
    (root / "project.yaml").write_text(
        'name: "Pricing Show"\nslug: pricing-show\nproject_format_version: 1\n',
        encoding="utf-8")
    monkeypatch.setenv("PIPELINE_PROJECTS_ROOT", str(tmp_path))
    return root


def _approve_in_proposal(project: Path) -> None:
    """Approve the reel the way a ruling lands: in the proposal file."""
    from library.tools import reel_proposal

    path = reel_proposal.proposal_path(str(project))
    path.write_text(json.dumps(_proposal_document("approved")), encoding="utf-8")


def _propose(project: Path, monkeypatch) -> None:
    import manage_project
    from library.tools import reel_proposal

    def publish(folder, force=False):
        path = reel_proposal.proposal_path(folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_proposal_document("proposed")),
                        encoding="utf-8")
        return path

    monkeypatch.setattr(reel_proposal, "write_from_step_output", publish)
    manage_project.cmd_propose_reels(
        argparse.Namespace(project=str(project), force=False))


def _build_args(**overrides):
    return argparse.Namespace(only_reel=overrides.get("only_reel", []))


def _build(project: Path, monkeypatch, *, loop=lambda folder, args: 0) -> int:
    from library.processes.reels import run_reels
    from library.tools import project_format

    monkeypatch.setattr(project_format, "ensure_project_format", lambda folder: None)
    monkeypatch.setattr(run_reels, "_run_capabilities", loop)
    return run_reels.run(str(project), _build_args())


def _sign_off(project: Path) -> None:
    import manage_project

    manage_project.cmd_sign_off(argparse.Namespace(
        slug=str(project), reel=REEL, note="ship it", by="captain",
        withdraw=False))


def _deliver(project: Path, monkeypatch) -> None:
    import manage_project
    from library.tools import reel_deliver

    output = project / "exports" / f"{REEL}.mp4"

    def render(folder, reel, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"fixture")
        check = {"passed": True, "detail": "fixture"}
        return {"timeline_name": REEL, "reel": reel,
                "settings": {"needs_captain_word": []},
                "render": {"output_path": str(output)},
                "verification": {"output_path": str(output), "duration": check,
                                 "resolution": check, "audio": check,
                                 "content_present": True,
                                 "opens_on_black": False, "passed": True},
                "delivered": True, "report_path": str(output) + ".deliver.json"}

    monkeypatch.setattr(reel_deliver, "deliver_reel", render)
    manage_project.cmd_deliver_reel(argparse.Namespace(
        project=str(project), reel=1, output_dir="", name="", timeout=1))


def test_one_request_leaves_one_assignment_whose_history_is_the_verbs(
        project, monkeypatch, capsys):
    from library.tools.project_work_state import project_work_state

    _propose(project, monkeypatch)
    _approve_in_proposal(project)
    assert _build(project, monkeypatch) == 0

    state = project_work_state(str(project))
    [assignment] = state["assignments"]
    assert assignment["state"] == "review"
    assert assignment["resume_point"]["action"] == "sign_off"
    assert REEL in assignment["resume_point"]["command"]
    assert assignment["assignment_id"] in state["summary"]
    assert "ren sign-off" in state["summary"]

    _sign_off(project)
    _deliver(project, monkeypatch)

    [record] = _records(project)
    assert [(h["event"], h["from"], h["to"]) for h in record["history"]] == [
        ("propose", "brief", "draft"),
        ("approve", "draft", "draft"),
        ("build", "draft", "review"),
        ("sign_off", "review", "approved"),
        ("deliver", "approved", "delivered"),
    ]
    [assignment] = project_work_state(str(project))["assignments"]
    assert assignment["assignment_id"] == record["assignment_id"]
    assert assignment["state"] == "delivered"
    assert assignment["resume_point"]["action"] == "none"


def test_rebuilding_an_approved_reel_asks_until_the_assignment_authorizes(
        project, monkeypatch, capsys):
    from library.tools import assignment_interaction, assignment_spine

    _propose(project, monkeypatch)
    _approve_in_proposal(project)
    assert _build(project, monkeypatch) == 0
    _sign_off(project)
    capsys.readouterr()

    ran = []
    assert _build(project, monkeypatch,
                  loop=lambda folder, args: ran.append(folder) or 0) == 4
    assert ran == []
    assert "already approved" in capsys.readouterr().err
    with pytest.raises(assignment_spine.AssignmentDecisionRequired) as asked:
        assignment_spine.begin_build(str(project))
    refusal = asked.value.to_dict()
    assert refusal["recovery_class"] == "needs_question"
    assert refusal["code"] == "REN_ASSIGNMENT_DECISION_REQUIRED"
    [record] = _records(project)
    assert record["state"] == "approved"

    envelope = assignment_interaction.authorize(
        str(project), record["assignment_id"])
    assert envelope["result"]["replacement_approved"] is True
    assert _build(project, monkeypatch) == 0
    [record] = _records(project)
    assert record["state"] == "review"
    assert record["history"][-1]["event"] == "build"

    # Approving the reworked reel closes the authorization: the next
    # replacement asks again.
    _sign_off(project)
    [record] = _records(project)
    assert record["state"] == "approved"
    assert record["replacement_approved"] is False
    assert _build(project, monkeypatch) == 4


def test_a_build_killed_mid_run_recovers_to_its_pre_build_state(
        project, monkeypatch):
    from library.tools import assignment_interaction
    from library.tools.project_work_state import project_work_state

    _propose(project, monkeypatch)
    _approve_in_proposal(project)
    killer = (
        "import os, signal, sys, argparse\n"
        "from library.processes.reels import run_reels\n"
        "from library.tools import project_format\n"
        "project_format.ensure_project_format = lambda folder: None\n"
        "def loop(folder, args):\n"
        "    os.kill(os.getpid(), signal.SIGKILL)\n"
        "run_reels._run_capabilities = loop\n"
        "run_reels.run(sys.argv[1], argparse.Namespace(only_reel=[]))\n")
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    killed = subprocess.run([sys.executable, "-c", killer, str(project)],
                            cwd=REPO, env=env, check=False,
                            capture_output=True, encoding="utf-8")
    assert killed.returncode == -9, killed.stderr

    [record] = _records(project)
    checkpoint = record["operations"][record["checkpoint"]]
    assert checkpoint["status"] == "running"
    assert record["state"] == "draft"
    [assignment] = project_work_state(str(project))["assignments"]
    assert assignment["recovery"] == "resume"
    assert assignment["resume_point"]["action"] == "rerun_build"
    envelope = assignment_interaction.state(str(project))
    assert envelope["recovery"]["resume_with"]["command"].startswith("ren build")

    assert _build(project, monkeypatch) == 0
    [record] = _records(project)
    interrupted = [op for op in record["operations"].values()
                   if op["status"] == "interrupted"]
    assert len(interrupted) == 1
    assert interrupted[0]["recovered_to"] == "draft"
    assert interrupted[0]["request"]["pre_state"] == "draft"
    assert record["state"] == "review"
    assert [h["event"] for h in record["history"]] == [
        "propose", "approve", "build"]


def test_a_refused_build_leaves_the_state_where_it_was(project, monkeypatch, capsys):
    _propose(project, monkeypatch)
    _approve_in_proposal(project)
    assert _build(project, monkeypatch, loop=lambda folder, args: 4) == 4
    [record] = _records(project)
    assert record["state"] == "draft"
    assert record["operations"][record["checkpoint"]]["status"] == "refused"
    assert [h["event"] for h in record["history"]] == ["propose", "approve"]


def test_ren_edit_opens_an_assignment_and_asks_before_replacing_an_approved_edit(
        project, monkeypatch, capsys):
    import manage_project
    from library.tools import assignment_spine, run_control

    def pipeline(cmd, env=None, check=False):
        run_control.write_run_status(str(project), summary_status="SUCCESS")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", pipeline)
    args = argparse.Namespace(
        slug=str(project), from_step=None, step=None, dry_run=False,
        auto=False, review=False, resume=False)
    with pytest.raises(SystemExit) as exited:
        manage_project.cmd_run(args)
    assert exited.value.code == 0
    [record] = _records(project)
    assert record["kind"] == "edit"
    assert [(h["event"], h["to"]) for h in record["history"]] == [
        ("edit", "draft"), ("build", "review")]

    assignment_spine.on_sign_off(str(project), "Pricing Show v1")
    [record] = _records(project)
    assert record["state"] == "approved"
    with pytest.raises(assignment_spine.AssignmentDecisionRequired):
        manage_project.cmd_run(args)
    args.dry_run = True
    with pytest.raises(SystemExit):
        manage_project.cmd_run(args)
    assert len(_records(project)) == 1


def test_assignment_verb_is_registered_in_both_front_doors():
    from ren.commands import VERBS

    assert any(verb.name == "assignment" and verb.module_argv ==
               ("library.tools.assignment_interaction",) for verb in VERBS)
    rust = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "assignment"' in rust
