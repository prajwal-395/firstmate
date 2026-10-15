"""LE-10: `ren edit --detach` and `ren wait <p>` - events instead of sleep loops.

Hermetic: every project is built under `tmp_path`; no pipeline run,
no Resolve, no broker, no network. Host/agent environment markers the
code depends on are cleared by the autouse fixture, since firstmate's
full-suite run executes inside a Claude Code session.
"""

import json
import os
import threading
import time
from pathlib import Path

import pytest

from library.tools import run_control, run_wait
from library.tools import run_id as run_identity


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for key in (
        "REN_RUN_ID",
        run_control.DETACHED_LOG_ENV,
        "REN_OUTPUT",
        "REN_NOTIFY",
        "PIPELINE_PROJECTS_ROOT",
        "PIPELINE_SFX_LIBRARY",
        "PIPELINE_MUSIC_LIBRARY",
        "PIPELINE_HOST_HARNESS",
    ):
        monkeypatch.delenv(key, raising=False)


def _project(tmp_path, name="show"):
    root = tmp_path / name
    root.mkdir()
    (root / "project.yaml").write_text(
        f'name: "Test Show"\nslug: {name}\n', encoding="utf-8"
    )
    return root


def _write_running_status(root, **extra):
    status = {
        "status": "running",
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "run_id": run_identity.mint_run_id(),
        "steps_to_run": ["temporal_index"],
        "current_step": "temporal_index",
    }
    status.update(extra)
    (root / run_control.RUN_STATUS_FILE).write_text(
        json.dumps(status), encoding="utf-8"
    )
    return status


def _write_request(root, step="plan_vfx", kind="llm_step"):
    requests = root / "pipeline_output" / "llm_requests"
    requests.mkdir(parents=True, exist_ok=True)
    payload = {
        "step_id": step,
        "prompt": "p",
        "constraints": "c",
        "context": "x",
        "expected_schema": "{}",
        "project_folder": str(root),
        "timestamp": "2026-10-12T00:00:00",
        "kind": kind,
    }
    path = requests / f"{step}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ── Acceptance: a fake runner writes a request after 2 s ──────────────
# `ren wait` returns within 3 s with `event: ask` and the request path.


def test_wait_returns_ask_within_3s(tmp_path):
    root = _project(tmp_path)

    def _fake_runner():
        time.sleep(1.0)
        _write_request(root)

    thread = threading.Thread(target=_fake_runner, daemon=True)
    started = time.monotonic()
    thread.start()
    try:
        event = run_wait.wait_for_event(str(root), str(root), timeout=10.0)
    finally:
        thread.join(timeout=5.0)
    wall = time.monotonic() - started

    assert event["event"] == "ask"
    assert event["request"].endswith("plan_vfx.json")
    assert event["step"] == "plan_vfx"
    assert event["kind"] == "llm_step"
    assert event["next_command"] == f"ren wait {root}"
    assert wall < 3.0


def test_answered_request_is_not_an_ask(tmp_path):
    root = _project(tmp_path)
    request = _write_request(root)
    responses = root / "pipeline_output" / "llm_responses"
    responses.mkdir(parents=True, exist_ok=True)
    (responses / request.name).write_text("{}", encoding="utf-8")
    _write_running_status(
        root,
        status="success",
        summary_status="SUCCESS",
        current_step=None,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    event = run_wait.wait_for_event(str(root), str(root), timeout=1.0)

    assert event["event"] == "ended"
    assert event["status"] == "SUCCESS"


# ── Acceptance: an idle live run returns at the timeout ───────────────


def test_wait_still_running_on_live_idle_run(tmp_path):
    root = _project(tmp_path)
    (root / run_control.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    _write_running_status(root)

    started = time.monotonic()
    event = run_wait.wait_for_event(str(root), str(root), timeout=2.0)
    wall = time.monotonic() - started

    assert event["event"] == "still_running"
    assert event["current_step"] == "temporal_index"
    assert event["elapsed_s"] is not None and event["elapsed_s"] >= 1.5
    assert event["next_command"] == f"ren wait {root}"
    assert 1.5 < wall < 5.0


def test_wait_main_prints_one_json_line_and_exits_0(tmp_path, capsys):
    root = _project(tmp_path)
    (root / run_control.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    _write_running_status(root)

    assert run_wait.main([str(root), "--timeout", "1"]) == 0
    line = capsys.readouterr().out.strip()
    assert json.loads(line)["event"] == "still_running"


# ── Acceptance: a finished run returns at once ────────────────────────


def test_wait_ended_on_finished_run(tmp_path):
    root = _project(tmp_path)
    _write_running_status(
        root,
        status="success",
        summary_status="SUCCESS",
        current_step=None,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    started = time.monotonic()
    event = run_wait.wait_for_event(str(root), str(root), timeout=10.0)
    wall = time.monotonic() - started

    assert event["event"] == "ended"
    assert event["status"] == "SUCCESS"
    assert event["next_command"] == f"ren status {root}"
    assert wall < 2.0


def test_wait_explains_interruption_and_preserves_last_progress_and_retry_state(tmp_path):
    root = _project(tmp_path)
    last = {"step": "semantic_analysis", "stage": "analysis",
            "message": "Ren is measuring footage.", "next_action": "Inspect the run log."}
    _write_running_status(root, progress_events=[last], last_completed_step="catalog")

    event = run_wait.poll_once(str(root), str(root))

    assert event["event"] == "ended"
    assert event["status"] == "INTERRUPTED"
    assert event["progress_event"] == last
    assert event["interruption"]["detected"] is True
    assert event["retry"]["state"] == "not_started"
    assert "cannot tell whether" in event["retry"]["guidance"]
    assert event["next_command"] == f"ren status {root}"


def test_wait_ended_carries_refused(tmp_path):
    root = _project(tmp_path)
    _write_running_status(
        root,
        status="refused",
        summary_status="REFUSED",
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )

    event = run_wait.wait_for_event(str(root), str(root), timeout=5.0)

    assert event["event"] == "ended"
    assert event["status"] == "REFUSED"


def test_wait_names_interrupted_run(tmp_path):
    root = _project(tmp_path)
    _write_running_status(root)

    event = run_wait.wait_for_event(str(root), str(root), timeout=5.0)

    assert event["event"] == "ended"
    assert event["status"] == "INTERRUPTED"


# ── The other events, in LE-10 order ──────────────────────────────────


def test_wait_reports_pending_review_gate(tmp_path):
    from library.tools import review_gate

    root = _project(tmp_path)
    review_gate.save_gate_snapshot(
        str(root), "creative_direction", "Creative direction", {"choice": "a"}
    )

    event = run_wait.wait_for_event(str(root), str(root), timeout=5.0)

    assert event["event"] == "review_gate"
    assert event["gates"] == ["creative_direction"]
    assert "creative_direction" in event["next_command"]


def test_wait_reports_hold(tmp_path):
    root = _project(tmp_path)
    run_control.request_hold(str(root), requested_by="captain", reason="lunch")

    event = run_wait.wait_for_event(str(root), str(root), timeout=5.0)

    assert event["event"] == "hold"
    assert event["hold"]["requested_by"] == "captain"
    assert event["next_command"] == f"ren resume {root}"


def test_wait_reports_lock_wait(tmp_path):
    root = _project(tmp_path)
    (root / run_control.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    _write_running_status(
        root,
        heavy_work_wait={
            "message": "heavy-work: waiting for running vep-x (pid 1)",
            "holders": [{"owner": "vep-x", "pid": 1}],
        },
    )

    event = run_wait.wait_for_event(str(root), str(root), timeout=5.0)

    assert event["event"] == "lock_wait"
    assert event["holders"] == [{"owner": "vep-x", "pid": 1}]
    assert event["next_command"] == f"ren wait {root}"


def test_still_running_carries_t5_progress_events(tmp_path):
    root = _project(tmp_path)
    (root / run_control.PID_FILE).write_text(str(os.getpid()), encoding="utf-8")
    _write_running_status(
        root,
        progress_events=[
            {
                "at": "2026-10-12T00:00:00",
                "step": "catalog",
                "outcome": "completed",
                "message": "Catalog done.",
                "steps_done": 1,
                "steps_remaining": 2,
            }
        ],
    )

    event = run_wait.wait_for_event(str(root), str(root), timeout=1.0)

    assert event["event"] == "still_running"
    assert event["progress"] == "Catalog done."


def test_wait_refuses_an_unknown_project(capsys):
    from library.tools.ren_refusal import REFUSAL_EXIT_CODE

    assert run_wait.main(["no-such-project-xyz", "--timeout", "1"]) == REFUSAL_EXIT_CODE
    assert "no project" in capsys.readouterr().err.lower()


# ── Registration: both front doors ────────────────────────────────────


def test_wait_verb_is_registered_in_both_front_doors():
    from ren.commands import VERBS

    repo = Path(__file__).resolve().parents[3]
    assert any(
        verb.name == "wait" and verb.module_argv == ("library.tools.run_wait",)
        for verb in VERBS
    )
    native = (repo / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8"
    )
    assert 'name: "wait"' in native


# ── begin_run_status carries the detached handle ──────────────────────


def test_begin_run_status_records_detached(tmp_path):
    root = _project(tmp_path)
    record = run_control.begin_run_status(
        str(root),
        "edit",
        ["temporal_index"],
        run_id="run-20261012T000000Z-abcdef",
        detached={
            "run_id": "run-20261012T000000Z-abcdef",
            "pid": 4242,
            "log": str(
                root / "pipeline_output" / "logs" / "run-20261012T000000Z-abcdef.log"
            ),
        },
    )

    assert record["detached"]["pid"] == 4242
    assert (
        run_control.read_run_status(str(root))["detached"]["run_id"]
        == "run-20261012T000000Z-abcdef"
    )


def test_begin_run_status_without_detached_writes_no_key(tmp_path):
    root = _project(tmp_path)
    record = run_control.begin_run_status(str(root), "edit", [])

    assert "detached" not in record


# ── --detach: own session, log file, handle ───────────────────────────


def test_detach_spawns_own_session_and_answers_at_once(tmp_path, capsys, monkeypatch):
    import subprocess
    import sys

    import manage_project

    root = _project(tmp_path)
    seen = {}

    class _FakeProc:
        pid = 4242

    def _fake_popen(cmd, **kwargs):
        seen["cmd"] = cmd
        seen.update(kwargs)
        log = kwargs.get("stdout")
        assert log is not None
        log.write("runner says hi\n")
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        "library.tools.assignment_spine.begin_edit",
        lambda project, request=None: object(),
    )
    monkeypatch.setattr(
        "library.tools.assignment_spine.end_edit",
        lambda act, **kwargs: seen.update(end=kwargs),
    )

    config = type("Config", (), {"project_root": root, "name": "Test Show"})()
    args = type("Args", (), {"slug": str(root), "dry_run": False})()
    manage_project._cmd_run_detached(config, args, [sys.executable, "run_pipeline.py"])

    assert seen["start_new_session"] is True
    assert seen["stderr"] == subprocess.STDOUT
    assert run_identity.ENV_VAR in seen["env"]
    assert run_control.DETACHED_LOG_ENV in seen["env"]
    log_path = Path(seen["env"][run_control.DETACHED_LOG_ENV])
    assert log_path.parent == root / "pipeline_output" / "logs"
    assert log_path.read_text(encoding="utf-8") == "runner says hi\n"
    assert (root / run_control.PID_FILE).read_text(encoding="utf-8").strip() == "4242"
    # The parent writes the pid file only: the pid/log/run id land on
    # pipeline_run.json through begin_run_status when the runner
    # starts, so a provisional record here can never read as an
    # INTERRUPTED predecessor.
    assert run_control.read_run_status(str(root)) == {}
    assert seen["end"]["summary_status"] == "detached"

    handle = json.loads(capsys.readouterr().out.strip().splitlines()[0])
    assert handle["pid"] == 4242
    assert handle["log"] == str(log_path)
    assert handle["wait"] == f"ren wait {root}"
    assert handle["run_id"] == seen["env"][run_identity.ENV_VAR]


def test_detach_with_dry_run_refuses(tmp_path, monkeypatch):
    import manage_project
    from library.tools.ren_refusal import RenRefusal

    root = _project(tmp_path)
    config = type("Config", (), {"project_root": root, "name": "Test Show"})()
    monkeypatch.setattr(manage_project, "get_project", lambda slug, **kwargs: config)
    args = type(
        "Args",
        (),
        {
            "slug": str(root),
            "from_step": None,
            "step": None,
            "dry_run": True,
            "json": False,
            "auto": False,
            "review": False,
            "resume": False,
            "rerun": [],
            "target": None,
            "only": [],
            "skip": [],
            "with_steps": [],
            "overrides": [],
            "profile": None,
            "break_at": [],
            "no_break_at": [],
            "full_auto": None,
            "llm_timeout": 300,
            "detach": True,
        },
    )()
    with pytest.raises(RenRefusal, match="detach"):
        manage_project.cmd_run(args)


def test_run_parser_offers_detach():
    import subprocess
    import sys

    repo = Path(__file__).resolve().parents[3]
    proc = subprocess.run(
        [sys.executable, str(repo / "manage_project.py"), "run", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0
    assert "--detach" in proc.stdout
