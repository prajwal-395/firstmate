"""
Run control: the four things the captain presses.

Decision B, ratified 2026-08-17: runs are driven from the dashboard, and
the reason given was to *step, drive and steer* a live pipeline - not
merely to start and stop it.  These tests hold the four controls to that.

What is asserted here is the COMMAND LINE each control builds and the
FILES it writes, because those are the whole contract with the runner in
the other process.  A test that only checked for HTTP 200 would pass over
a Start button that forced `--review` on all 26 steps, which is exactly
the state this work was commissioned to repair.

The handbrake's own half of the contract - that `run_pipeline.py` reads
the hold file and stops at a step boundary - is asserted in
``test_handbrake_stops_the_runner`` against the real runner loop.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from library.dashboard.server import app
from library.tools import run_control

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "e2e_pipeline_data.json"


@pytest.fixture
def temp_project(tmp_path):
    project_dir = tmp_path / "run_control_project"
    project_dir.mkdir()
    shutil.copy(FIXTURE_PATH, project_dir / "pipeline_data.json")
    (project_dir / "project.yaml").write_text(
        "name: Run Control Project\nslug: run-control\n")
    return str(project_dir)


@pytest.fixture
def client(temp_project):
    with patch("library.dashboard.server._get_project_dir",
               return_value=temp_project):
        yield TestClient(app)


@pytest.fixture
def spawned():
    """Capture the command each control would spawn, without spawning it.

    ``poll()`` returns None so the server believes the child is alive,
    which is what a healthy launch looks like.
    """
    proc = MagicMock()
    proc.pid = 424242
    proc.poll.return_value = None
    with patch("library.dashboard.server.subprocess.Popen",
               return_value=proc) as popen:
        yield popen


def _cmd(popen) -> list:
    assert popen.call_count == 1, "expected exactly one spawn"
    return list(popen.call_args[0][0])


# ── 1. Start ────────────────────────────────────────────────────────

def test_start_launches_full_auto_agent_and_does_not_force_review(client, spawned):
    """The captain's actual mode, and no review gate they did not ask for.

    Start used to send ``review_mode=True`` by default, turning one press
    into 26 stops, and never passed ``--full-auto`` at all, so hybrid and
    LLM-only steps stalled waiting for a human who was looking at a web
    page.
    """
    response = client.post("/api/pipeline/run", json={})
    assert response.status_code == 200, response.text

    cmd = _cmd(spawned)
    assert "--full-auto" in cmd
    assert cmd[cmd.index("--full-auto") + 1] == "agent"
    assert "--review" not in cmd
    assert "--step" not in cmd

    body = response.json()
    assert body["status"] == "started"
    assert "full-auto agent" in body["mode"]
    assert "review gates off" in body["mode"]


def test_start_passes_deprecated_agy_alias_through(client, spawned):
    """Saved commands and muscle memory still pass `agy`.

    The dashboard forwards it untouched; the runner maps it to `agent`
    and prints the deprecation (asserted in
    ``test_full_auto_agent_alias.py``), so the alias keeps working
    end to end without the dashboard owning the vocabulary.
    """
    response = client.post("/api/pipeline/run", json={"full_auto": "agy"})
    assert response.status_code == 200, response.text

    cmd = _cmd(spawned)
    assert cmd[cmd.index("--full-auto") + 1] == "agy"


def test_start_uses_the_dashboards_own_interpreter(client, spawned):
    """Not a bare `python3`.

    The dashboard is started from the pipeline's .venv, which is where
    every ML dependency lives.  Spawning `python3` off PATH picked a
    different interpreter, and the run died on an import before it
    reached a single step.
    """
    client.post("/api/pipeline/run", json={})
    assert _cmd(spawned)[0] == sys.executable






def test_start_reports_a_runner_that_died_immediately(client, temp_project):
    """A launch that fell over is not a launch that started.

    Discarding the child's output to DEVNULL and returning 200 is how a
    control reports success over nothing at all.
    """
    proc = MagicMock()
    proc.pid = 5150
    proc.poll.return_value = 1          # already dead
    with patch("library.dashboard.server.subprocess.Popen", return_value=proc):
        response = client.post("/api/pipeline/run", json={})
    assert response.status_code == 500
    assert "exited immediately" in response.json()["detail"]


def test_start_refuses_while_a_run_is_up(client, temp_project, spawned):
    run_control.pid_path(temp_project).write_text(str(os.getpid()))
    try:
        response = client.post("/api/pipeline/run", json={})
        assert response.status_code == 400
        assert spawned.call_count == 0
    finally:
        run_control.pid_path(temp_project).unlink()


# ── 2. The handbrake ────────────────────────────────────────────────

def test_pause_writes_the_hold_file(client, temp_project):
    response = client.post("/api/pipeline/pause",
                           json={"reason": "captain wants a look"})
    assert response.status_code == 200

    record = run_control.hold_requested(temp_project)
    assert record is not None
    assert record["requested_by"] == "dashboard"
    assert record["reason"] == "captain wants a look"








def test_handbrake_stops_the_runner(temp_project, monkeypatch):
    """The runner's half: engage the hold, and no step runs.

    This drives the real ``run_pipeline`` loop rather than a stand-in, so
    a refactor that drops the check between steps fails here.  The
    dashboard's live drive covers the other half - a hold engaged while a
    step is in flight, which finishes that step first.
    """
    from library.processes.edit_video import run_pipeline as rp

    # The shared fixture carries an outstanding failure, and an
    # outstanding failure outranks everything in the status rule.  Clear
    # it so PARTIAL is actually reachable and the assertion below means
    # what it says.
    state_path = Path(temp_project, "pipeline_data.json")
    state = json.loads(state_path.read_text())
    state["failed_steps"] = []
    state_path.write_text(json.dumps(state))

    run_control.request_hold(temp_project, requested_by="test")

    ran = []
    monkeypatch.setattr(
        rp, "run_deterministic_step",
        lambda *a, **k: ran.append(a) or {})

    summary = rp.run_pipeline(project_dir=temp_project, full_auto="mock")

    assert ran == [], "a step ran with the handbrake engaged"
    assert summary["held_before_step"], "the runner did not report the hold"
    assert summary["status"] == "PARTIAL", (
        "a deliberate hold is an incomplete run, not a broken one")

    status = run_control.read_run_status(temp_project)
    assert status["status"] == "held"
    assert status["held_before_step"] == summary["held_before_step"]


# ── 3. Resume ───────────────────────────────────────────────────────

def test_resume_releases_the_hold_and_passes_resume(client, temp_project, spawned):
    run_control.request_hold(temp_project)

    response = client.post("/api/pipeline/resume", json={})
    assert response.status_code == 200

    cmd = _cmd(spawned)
    assert "--resume" in cmd
    assert cmd[cmd.index("--full-auto") + 1] == "agent"
    assert "--review" not in cmd

    body = response.json()
    assert body["hold_released"] is True
    assert run_control.hold_requested(temp_project) is None, (
        "resuming with the handbrake still on would stop at the next step")




# ── 4. Step ─────────────────────────────────────────────────────────

def test_step_advances_exactly_one_step(client, temp_project, spawned):
    """`--step` already runs precisely one node.  All this adds is the id.

    Reuse is the point: the runner needed no new single-step machinery,
    only the resolution of *which* step - the first in topological order
    the project has not completed.
    """
    response = client.post("/api/pipeline/step", json={})
    assert response.status_code == 200

    cmd = _cmd(spawned)
    assert cmd.count("--step") == 1
    step_id = cmd[cmd.index("--step") + 1]
    assert response.json()["step_id"] == step_id

    state = json.loads(Path(temp_project, "pipeline_data.json").read_text())
    completed = state.get("steps_completed", {})
    assert step_id not in completed, "stepped onto a step already complete"

    order = _topological_order()
    for earlier in order[:order.index(step_id)]:
        assert earlier in completed, (
            f"{step_id} was chosen while {earlier} upstream of it is unrun")




def test_step_rejects_an_unknown_step(client, spawned):
    response = client.post("/api/pipeline/step",
                           json={"step_id": "not_a_real_step"})
    assert response.status_code == 404
    assert spawned.call_count == 0




def test_step_refuses_while_a_run_is_up(client, temp_project, spawned):
    run_control.pid_path(temp_project).write_text(str(os.getpid()))
    try:
        assert client.post("/api/pipeline/step", json={}).status_code == 400
        assert spawned.call_count == 0
    finally:
        run_control.pid_path(temp_project).unlink()


# ── Status honesty ──────────────────────────────────────────────────

def test_status_does_not_report_a_current_step_for_a_dead_run(
        client, temp_project):
    """A stale field from a killed run must not read as work in progress.

    "A reader that reports success is not proof either" - CLAUDE.md.  The
    same applies to a reader that reports motion.
    """
    run_control.write_run_status(temp_project, status="running",
                                 current_step="semantic_analysis")
    status = client.get("/api/pipeline/status").json()
    assert status["is_running"] is False
    assert status["current_step"] is None




def _topological_order() -> list:
    from library.processes.edit_video.run_pipeline import topological_sort
    dag_path = REPO_ROOT / "library" / "processes" / "edit_video" / "dag.json"
    return topological_sort(json.loads(dag_path.read_text()))
