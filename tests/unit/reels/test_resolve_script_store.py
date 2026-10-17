from __future__ import annotations

import json
import subprocess

import pytest

from library.tools import resolve_script_store, shared_environment


def _run_record(run_id, script, purpose="inspect the ending"):
    return {
        "run_id": run_id,
        "at": f"2026-10-07T12:00:0{run_id[-1]}Z",
        "scope": "user",
        "purpose": purpose,
        "purpose_hash": resolve_script_store.purpose_hash(purpose),
        "script_hash": resolve_script_store.normalized_script_hash(script),
        "script": script,
        "project": "Podcast",
        "timeline": "Reel 22",
        "caller": "test-agent",
        "writing": False,
        "result": "ok",
        "error": None,
    }


def _git(project, *args):
    return subprocess.run(
        ["git", *args], cwd=project, check=True, capture_output=True,
        text=True, encoding="utf-8")


def test_project_run_commit_names_only_its_record_and_drt(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "project.yaml").write_text("name: Podcast\n", encoding="utf-8")
    footage = project / "raw" / "footage.mov"
    footage.parent.mkdir()
    footage.write_bytes(b"video")
    _git(project, "init", "-q")
    _git(project, "config", "user.name", "Test")
    _git(project, "config", "user.email", "test@example.invalid")
    _git(project, "add", "project.yaml")
    _git(project, "commit", "-m", "project declaration")

    archive = (project / "pipeline_output" / "review" / "recovery_archives"
               / "Reel--custom.drt")
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"recovery")
    record = _run_record("run-1", "result = 1")
    record["scope"] = "project"
    record_path, commit = resolve_script_store.save_run(
        "project", record, project, (archive,))

    assert record_path == (project / "pipeline_output" / "review"
                           / "resolve_scripts" / "runs" / "run-1.json")
    assert commit["committed"] is True
    committed = set(_git(project, "show", "--pretty=format:",
                         "--name-only", "HEAD").stdout.splitlines())
    assert committed == {
        str(record_path.relative_to(project)),
        str(archive.relative_to(project)),
    }
    assert str(footage.relative_to(project)) not in committed


def test_recurring_user_run_becomes_candidate_only_after_two_runs(
        tmp_path, monkeypatch):
    monkeypatch.setattr(shared_environment, "vep_home", lambda: tmp_path)
    store = resolve_script_store.user_store_dir()
    first = _run_record("run-1", "result = 1")
    second = _run_record("run-2", "result = 2")

    resolve_script_store.save_run("user", first)
    assert resolve_script_store.candidates("user") == []
    resolve_script_store.save_run("user", second)

    rows = resolve_script_store.candidates("user")
    candidate = next(row for row in rows
                     if row["matched_by"] == "purpose")
    assert candidate["occurrences"] == 2
    assert candidate["script_variants"] == 2
    assert resolve_script_store.workflows("user") == []

    path, commit = resolve_script_store.promote(
        "user", None, candidate["candidate_id"], "run-2", "inspect-ending",
        "test-agent")
    workflow = resolve_script_store.load_workflow(
        "user", None, "inspect-ending")
    assert path == store / "workflows" / "inspect-ending.json"
    assert workflow["script"] == "result = 2"
    assert workflow["promoted_from"]["run_id"] == "run-2"
    assert commit["committed"] is False


def test_project_inventory_diff_names_foreign_timeline_changes():
    before = [
        {"name": "Reel 22", "unique_id": "target", "settings": {}},
        {"name": "Reel 27", "unique_id": "other", "settings": {}},
    ]
    after = [
        {"name": "Reel 22", "unique_id": "target", "settings": {}},
        {"name": "Reel 28", "unique_id": "other", "settings": {}},
    ]

    diff = resolve_script_store.project_inventory_diff(
        before, after, "Reel 22", "target")

    assert diff["target_present"] is True
    assert diff["guard_passed"] is False
    assert diff["changed"] == [{"before": before[1], "after": after[1]}]


class _Timeline:
    def GetName(self):
        return "Reel 22"

    def GetUniqueId(self):
        return "target"


class _Project:
    def __init__(self):
        self.timeline = _Timeline()

    def GetName(self):
        return "Podcast"

    def GetUniqueId(self):
        return "project-id"

    def GetCurrentTimeline(self):
        return self.timeline

    def GetTimelineCount(self):
        return 1

    def GetTimelineByIndex(self, index):
        assert index == 1
        return self.timeline


class _Manager:
    def __init__(self, project):
        self.project = project
        self.folder_path = ()

    def GetCurrentProject(self):
        return self.project

    def GetCurrentFolder(self):
        return self.folder_path[-1] if self.folder_path else ""

    def GotoParentFolder(self):
        if not self.folder_path:
            return False
        self.folder_path = self.folder_path[:-1]
        return True

    def GotoRootFolder(self):
        self.folder_path = ()
        return True

    def GetFoldersInCurrentFolder(self):
        return {}

    def OpenFolder(self, _name):
        return False


class _Resolve:
    def __init__(self, project):
        self.manager = _Manager(project)

    def GetProjectManager(self):
        return self.manager

    def GetCurrentPage(self):
        return "edit"


@pytest.mark.parametrize("runner_exit", [0, 7])
def test_recorded_writer_keeps_backup_and_inventory_in_run_record(
        tmp_path, monkeypatch, capsys, runner_exit):
    from contextlib import nullcontext

    from library.tools import feedback_patch_apply, marker_feedback
    from library.tools import reel_replace_guard, resolve_axi, timeline_shadow
    from library.tools import resolve_lock
    from library.tools.versions import store as version_store

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "project.yaml").write_text(
        "name: Podcast\n", encoding="utf-8")
    monkeypatch.chdir(project_root)
    project = _Project()
    resolve = _Resolve(project)
    monkeypatch.setattr(resolve_axi, "_connect", lambda: resolve)
    monkeypatch.setattr(resolve_axi, "_lease", lambda exclusive: nullcontext())
    monkeypatch.setattr(marker_feedback, "current_timeline",
                        lambda live: (project.timeline, project))
    inventory = [{"name": "Reel 22", "unique_id": "target",
                  "settings": {}}]
    monkeypatch.setattr(reel_replace_guard, "timeline_inventory",
                        lambda live_project: inventory)
    snapshots = iter(({"clips": [{"unique_id": "clip"}]},
                      {"clips": [{"unique_id": "clip"},
                                 {"unique_id": "new"}]}))
    monkeypatch.setattr(timeline_shadow, "read_live",
                        lambda *args: next(snapshots))
    monkeypatch.setattr(feedback_patch_apply, "inventory_changes",
                        lambda before, after: [{"change": "added"}])
    monkeypatch.setattr(resolve_lock, "cursor_excursion",
                        lambda *args, **kwargs: nullcontext())
    archive = (project_root / "pipeline_output" / "review" / "recovery_archives"
               / "custom.drt")
    monkeypatch.setattr(version_store, "export_timeline_recovery_archive",
                        lambda *args, **kwargs: {
                            "archived": True, "path": str(archive)})

    run_recorded = resolve_axi.cmd_run

    def run_inner(args):
        assert resolve_axi._RECORDED_RUN_CONTEXT.get() is True
        if runner_exit:
            raise SystemExit(runner_exit)
        print("script completed")
        return 0

    monkeypatch.setenv("REN_RESOLVED_CALLER", "agent-42")
    args = resolve_axi.build_parser().parse_args([
        "run", "--purpose", "Add ending marker", "--unsafe",
        "--script", "timeline.AddMarker(1, {})",
    ])
    monkeypatch.setattr(resolve_axi, "cmd_run", run_inner)

    assert run_recorded(args) == (1 if runner_exit else 0)
    capsys.readouterr()
    record_path = next((project_root / "pipeline_output" / "review"
                        / "resolve_scripts" / "runs").glob("*.json"))
    record = json.loads(record_path.read_text(encoding="utf-8"))
    assert record["caller"] == "agent-42"
    assert record["writing"] is True
    assert record["drt_backup"] == str(archive)
    assert record["inventory"]["timeline"]["changes"] == [
        {"change": "added"}]
    assert record["inventory"]["project"]["guard_passed"] is True
    if runner_exit:
        assert record["result"] is None
        assert "SystemExit(7)" in record["error"]
    else:
        assert record["result"] == "script completed\n"
