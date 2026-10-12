"""T5: push progress and a completion notice that names the file.

The acceptance, verbatim from the plan item:

- a mock run on a tmp project records at least one progress event per
  completed step;
- with a stubbed `osascript` on PATH, exactly one notification is invoked
  with the status and project name;
- the final stdout line contains an existing path;
- a project `hooks.json` declaring `operation_completed` fires once per
  operation.

Plus the `ren status` half of (a): the live run shows while
`pipeline_run.json` says running.

The DAG is mocked down to three deterministic steps for speed (the shape
`tests/scenarios/test_run_configuration_end_to_end.py` establishes); the
code under test is the runner's own, unpatched.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from library.processes.edit_video.run_pipeline import run_pipeline
from library.tools import hooks, run_control


# a -> b -> c, all deterministic, all cheap.
THREE_STEPS = {
    "id": "edit_video",
    "nodes": [
        {"id": "scan", "name": "Scan Project Folder",
         "step_ref": "steps/step_1_01_scan_project"},
        {"id": "catalog", "name": "Catalog Footage",
         "step_ref": "steps/step_1_02_catalog_footage"},
        {"id": "temporal_index", "name": "Temporal Index",
         "step_ref": "steps/step_1_04_temporal_index"},
    ],
    "edges": [
        {"from": "scan", "to": "catalog",
         "data_mapping": {"raw_footage_files": "raw_footage_files"}},
        {"from": "catalog", "to": "temporal_index",
         "data_mapping": {"catalog": "catalog"}},
    ],
}

_OUTPUT = {
    "scan": {"raw_footage_files": ["one.mov", "two.mov", "three.mov"]},
    "catalog": {"catalog": [{"clip_id": "clip_001"}]},
    "temporal_index": {"temporal_event_indices": []},
}


@pytest.fixture(autouse=True)
def _fresh_hooks():
    hooks.reset()
    had = os.environ.pop(hooks.ENV_DEPTH, None)
    yield
    hooks.reset()
    os.environ.pop(hooks.ENV_DEPTH, None)
    if had is not None:
        os.environ[hooks.ENV_DEPTH] = had


@pytest.fixture
def project(tmp_path):
    """A project with nothing done yet, so every step really runs."""
    folder = tmp_path / "mock_project"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(folder),
        "preflight_completed": {},
        "edit_completed": {},
        "failed_steps": [],
        "capability_outputs": {},
    }), encoding="utf-8")
    (folder / "project.yaml").write_text(
        "name: Mock Project\nslug: mock-project\n", encoding="utf-8")
    return folder


class _Runner:
    """Drives the runner over `THREE_STEPS`."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.seen = []

    def _implementation(self, step_dir):
        return {"type": "deterministic",
                "entry": str(Path(step_dir) / "step.py"), "manifest": {}}

    def _node_of(self, entry) -> str:
        directory = Path(entry).parent.name
        for node in THREE_STEPS["nodes"]:
            if node["step_ref"].endswith(directory):
                return node["id"]
        raise AssertionError(f"unmapped step directory {directory}")

    def _deterministic(self, entry, inputs):
        node_id = self._node_of(entry)
        self.seen.append((node_id, dict(inputs)))
        return dict(_OUTPUT[node_id])

    def run(self, **kw):
        with patch("library.processes.edit_video.run_pipeline.load_dag",
                   return_value=THREE_STEPS), \
             patch("library.processes.edit_video.run_pipeline"
                   ".get_step_implementation",
                   side_effect=self._implementation), \
             patch("library.processes.edit_video.run_pipeline"
                   ".run_deterministic_step", side_effect=self._deterministic), \
             patch("library.processes.edit_video.run_pipeline"
                   ".apply_source_identity", return_value=None):
            return run_pipeline(str(self.folder), **kw)


@pytest.fixture
def runner(project):
    return _Runner(project)


@pytest.fixture
def stubbed_osascript(tmp_path, monkeypatch):
    """An `osascript` that records its invocations instead of notifying."""
    bindir = tmp_path / "stubbin"
    bindir.mkdir()
    log = tmp_path / "osascript_calls.jsonl"
    script = bindir / "osascript"
    script.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{log}"\n',
        encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("REN_NOTIFY", raising=False)
    # The notification is a macOS surface; pin the platform so the test
    # says what it means on any machine it runs on.
    monkeypatch.setattr(sys, "platform", "darwin")
    return log


def _calls(log: Path) -> list[str]:
    if not log.is_file():
        return []
    return [line for line in
            log.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── (a) progress events on the run record ─────────────────────────────

def test_a_mock_run_records_a_progress_event_per_completed_step(
        runner, project):
    summary = runner.run()

    assert summary["status"] == "SUCCESS"
    assert [node for node, _ in runner.seen] == [
        "scan", "catalog", "temporal_index"]

    record = run_control.read_run_status(str(project))
    events = record.get("progress_events") or []
    by_step = {event.get("step"): event for event in events}
    for node_id in ("scan", "catalog", "temporal_index"):
        assert node_id in by_step, (
            f"no progress event for completed step {node_id}: {events!r}")
        assert by_step[node_id].get("message"), (
            f"progress event for {node_id} carries no plain-language "
            f"message: {by_step[node_id]!r}")


# ── (c) exactly one completion notification ───────────────────────────

def test_success_posts_exactly_one_notification_naming_status_and_project(
        runner, project, stubbed_osascript, capsys):
    summary = runner.run()

    assert summary["status"] == "SUCCESS"
    calls = _calls(stubbed_osascript)
    assert len(calls) == 1, (
        f"expected exactly one notification, got {len(calls)}: {calls!r}")
    assert "SUCCESS" in calls[0], calls[0]
    assert project.name in calls[0], calls[0]


def test_ren_notify_zero_opts_out_of_the_notification(
        runner, project, stubbed_osascript, monkeypatch, capsys):
    monkeypatch.setenv("REN_NOTIFY", "0")
    summary = runner.run()

    assert summary["status"] == "SUCCESS"
    assert _calls(stubbed_osascript) == [], "REN_NOTIFY=0 must stay silent"


def test_a_refused_run_notifies_once_and_still_names_a_file(
        runner, project, stubbed_osascript, capsys):
    summary = runner.run(break_at=["not_a_step"])

    assert summary["status"] == "REFUSED"
    calls = _calls(stubbed_osascript)
    assert len(calls) == 1, (
        f"a refused run notifies exactly once, got {len(calls)}: {calls!r}")
    assert "REFUSED" in calls[0], calls[0]
    assert project.name in calls[0], calls[0]
    out = capsys.readouterr().out
    last = [line for line in out.splitlines() if line.strip()][-1]
    assert any(Path(token.rstrip(".,;")).exists()
               for token in last.split() if "/" in token), (
        f"even a refused run's last stdout line names a real path: {last!r}")


# ── (b) the last stdout line names a file ─────────────────────────────

def test_the_final_stdout_line_contains_an_existing_path(
        runner, project, stubbed_osascript, capsys):
    summary = runner.run()

    assert summary["status"] == "SUCCESS"
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert lines, "the run printed nothing on stdout"
    last = lines[-1]
    candidates = [token for token in last.split() if "/" in token]
    assert candidates, f"the final stdout line names no path: {last!r}"
    assert any(Path(token.rstrip(".,;")).exists() for token in candidates), (
        f"the final stdout line names no EXISTING path: {last!r}")


# ── (d) operation_completed fires once per operation ──────────────────

def _declare_operation_hook(folder: Path, when: str):
    (folder / hooks.DECLARATION_FILENAME).write_text(json.dumps({"hooks": [{
        "name": f"on_{when}",
        "describe": "T5 acceptance: the operation announces itself.",
        "when": when,
        "action": {"kind": "run", "script": "record_payload.py"},
    }]}), encoding="utf-8")


def _hook_log_lines(folder: Path) -> list[dict]:
    log = folder / "pipeline_output" / "logs" / "hooks.log"
    if not log.is_file():
        return []
    return [json.loads(line) for line in
            log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _tiny_footage_project(tmp_path) -> str:
    """A scratch project `footage.scan` genuinely completes on."""
    folder = tmp_path / "scan_project"
    raw = folder / "raw"
    raw.mkdir(parents=True)
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=128x128:rate=10",
         "-pix_fmt", "yuv420p", str(raw / "tiny.mp4"), "-y"],
        capture_output=True, check=False)
    assert done.returncode == 0, done.stderr.decode()[:500]
    (folder / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(folder)}), encoding="utf-8")
    return str(folder)


def test_operation_completed_fires_once_per_operation(tmp_path):
    from library.tools import operations

    folder = Path(_tiny_footage_project(tmp_path))
    _declare_operation_hook(folder, "operation_completed")

    result = operations.get("footage.scan").execute(str(folder))

    assert result.completed, result.error
    lines = _hook_log_lines(folder)
    assert len(lines) == 1, (
        f"operation_completed must fire once per operation, fired "
        f"{len(lines)} times: {lines!r}")
    assert lines[0]["condition"] == "operation_completed"
    assert lines[0]["payload"]["operation"] == "footage.scan"


def test_a_refused_operation_fires_operation_refused(tmp_path):
    from library.tools import operations

    folder = tmp_path / "refused_project"
    folder.mkdir()
    (folder / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(folder)}), encoding="utf-8")
    _declare_operation_hook(folder, "operation_refused")

    op = next(op for op in operations.all()
              if op.name == "reel.select")
    result = op.execute(str(folder))

    assert result.refused
    lines = _hook_log_lines(folder)
    assert len(lines) == 1, (
        f"operation_refused must fire once per refusal, fired "
        f"{len(lines)} times: {lines!r}")
    assert lines[0]["condition"] == "operation_refused"
    assert lines[0]["payload"]["operation"] == "reel.select"


# ── (a) `ren status` shows the live run ───────────────────────────────

def test_status_shows_the_live_run_while_it_says_running(
        tmp_path, capsys):
    from library.tools.project_registry import create_project, project_status
    import manage_project

    config = create_project("live-run-probe", name="Live Run Probe",
                            root=tmp_path)
    run_control.write_run_status(
        str(config.project_root), status="running", mode="full run",
        steps_to_run=["scan", "catalog"],
        current_step="catalog", current_step_name="Catalog Footage",
        current_step_started_at="2026-10-11T10:00:00",
        last_completed_step="scan")

    info = project_status(str(config.project_root))
    live = info.get("live_run")
    assert live is not None, (
        "project_status must surface the live run while pipeline_run.json "
        "says running")
    assert live.get("current_step") == "catalog"

    manage_project.cmd_status(
        SimpleNamespace(slug=str(config.project_root)))
    out = capsys.readouterr().out
    assert "catalog" in out.lower(), (
        f"ren status must show the live step while running, got:\n{out}")


def test_status_shows_no_live_run_once_finished(tmp_path):
    from library.tools.project_registry import create_project, project_status

    config = create_project("settled-probe", name="Settled Probe",
                            root=tmp_path)
    run_control.write_run_status(
        str(config.project_root), status="success", current_step=None)

    info = project_status(str(config.project_root))
    assert info.get("live_run") is None, (
        "a finished run is not a live one")
