"""Automatic Ren task lifecycle over isolated semantic worktrees."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from library.tools.versions import store, worktrees


def _write_json(root: Path, relative: str, value: dict) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv(worktrees.ROOT_ENV, str(tmp_path / "worktrees"))
    root = tmp_path / "project"
    root.mkdir()
    assert store.init_project_repo(str(root))["initialised"]
    (root / "project.yaml").write_text("name: p\nslug: p\n", encoding="utf-8")
    _write_json(root, "external/declarations/captain_edits.json", {"edits": []})
    _write_json(root, "external/declarations/reel_ending.json", {"ending": "cut"})
    _write_json(root, "pipeline_data.json", {"generated": "target"})
    assert store.commit_build(str(root), "base")["committed"]
    return root


def test_task_start_finish_merges_semantics_and_discards_generated_state(
        tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    original_branch = store.git(str(project), "symbolic-ref", "--short",
                                "HEAD").stdout.strip()

    started = worktrees.task_start(
        str(project), "captions", ["external/declarations/captain_edits.json"])
    assert started["started"], started
    workspace = Path(started["path"])
    assert started["branch"] == "ren/captions"
    assert workspace != project
    assert store.git(str(project), "symbolic-ref", "--short",
                     "HEAD").stdout.strip() == original_branch

    _write_json(workspace, "external/declarations/captain_edits.json",
                {"edits": ["lower caption 3"]})
    _write_json(workspace, "pipeline_data.json",
                {"generated": "from task"})
    _write_json(workspace,
                "pipeline_output/steps/1_03_semantic_analysis/output.json",
                {"generated": "new path"})

    finished = worktrees.task_finish(str(project), "captions")
    assert finished["finished"], finished
    assert finished["resolve_rebuild_required"]
    assert finished["merged"]["semantic_changes"] == [
        "external/declarations/captain_edits.json"]
    assert finished["merged"]["generated_discarded"] == [
        "pipeline_data.json",
        "pipeline_output/steps/1_03_semantic_analysis/output.json"]
    assert json.loads((project / "external/declarations/captain_edits.json").read_text(
        encoding="utf-8")) == {"edits": ["lower caption 3"]}
    assert json.loads((project / "pipeline_data.json").read_text(
        encoding="utf-8")) == {"generated": "target"}
    assert not (project / "pipeline_output/steps/1_03_semantic_analysis"
                / "output.json").exists()
    assert store.git(str(project), "symbolic-ref", "--short",
                     "HEAD").stdout.strip() == original_branch
    assert not workspace.exists()
    assert worktrees.list_tasks(str(project)) == []


def test_task_start_refuses_overlapping_write_claims_and_allows_disjoint(
        tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    first = worktrees.task_start(
        str(project), "captions", ["external/declarations/captain_edits.json"])
    assert first["started"], first

    overlap = worktrees.task_start(
        str(project), "caption-followup", ["external/declarations/captain_edits.json"])
    assert not overlap["started"]
    assert "overlapping write claims" in overlap["reason"]

    default_claim = worktrees.task_start(str(project), "unscoped")
    assert not default_claim["started"]
    assert "overlapping write claims" in default_claim["reason"]

    second = worktrees.task_start(
        str(project), "ending", ["external/declarations/reel_ending.json"])
    assert second["started"], second

    assert worktrees.task_finish(str(project), "captions")["finished"]
    assert worktrees.task_finish(str(project), "ending")["finished"]


def test_task_finish_aborts_semantic_conflict_and_keeps_task_workspace(
        tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    started = worktrees.task_start(
        str(project), "ending", ["external/declarations/reel_ending.json"])
    assert started["started"], started
    workspace = Path(started["path"])
    _write_json(workspace, "external/declarations/reel_ending.json",
                {"ending": "task version"})

    _write_json(project, "external/declarations/reel_ending.json",
                {"ending": "project version"})
    assert store.commit_build(str(project), "project edit")["committed"]

    finished = worktrees.task_finish(str(project), "ending")
    assert not finished["finished"]
    assert finished["conflicts"] == ["external/declarations/reel_ending.json"]
    assert "merge was aborted" in finished["reason"]
    assert workspace.is_dir()
    assert store.git(str(project), "status", "--porcelain").stdout == ""
    assert json.loads((project / "external/declarations/reel_ending.json").read_text(
        encoding="utf-8")) == {"ending": "project version"}


def test_task_start_refuses_dirty_project_store_and_unclaimed_semantic_edits(
        tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    (project / "external/declarations/reel_ending.json").write_text(
        '{"ending": "uncommitted"}\n', encoding="utf-8")
    dirty = worktrees.task_start(str(project), "ending")
    assert not dirty["started"]
    assert "tracked changes" in dirty["reason"]

    assert store.commit_build(str(project), "clean target")["committed"]
    started = worktrees.task_start(
        str(project), "ending", ["external/declarations/captain_edits.json"])
    assert started["started"], started
    workspace = Path(started["path"])
    _write_json(workspace, "external/declarations/reel_ending.json",
                {"ending": "outside claim"})

    finished = worktrees.task_finish(str(project), "ending")
    assert not finished["finished"]
    assert "outside its write claims" in finished["reason"]
    assert workspace.is_dir()


def test_generated_only_task_finishes_without_resolve_rebuild(
        tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    started = worktrees.task_start(
        str(project), "analysis", ["external/declarations/captain_edits.json"])
    assert started["started"], started
    workspace = Path(started["path"])
    _write_json(workspace, "pipeline_data.json", {"generated": "task"})

    finished = worktrees.task_finish(str(project), "analysis")
    assert finished["finished"], finished
    assert not finished["resolve_rebuild_required"]
    assert finished["merged"]["semantic_changes"] == []
    assert finished["merged"]["generated_discarded"] == ["pipeline_data.json"]
    assert json.loads((project / "pipeline_data.json").read_text(
        encoding="utf-8")) == {"generated": "target"}
    assert worktrees.list_tasks(str(project)) == []


def test_ren_task_start_and_finish_commands_drive_the_lifecycle(
        tmp_path, monkeypatch):
    project = _project(tmp_path, monkeypatch)
    repo = Path(__file__).resolve().parents[3]
    vep = repo / "bin" / "vep"
    start = subprocess.run(
        [str(vep), "-m", "ren.cli", "task", str(project), "start",
         "cli-task", "--claim", "external/declarations/captain_edits.json"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert start.returncode == 0, start.stderr
    started = json.loads(start.stdout)
    assert started["started"]
    _write_json(Path(started["path"]), "external/declarations/captain_edits.json",
                {"edits": ["from cli"]})

    finish = subprocess.run(
        [str(vep), "-m", "ren.cli", "task", str(project), "finish",
         "cli-task"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert finish.returncode == 0, finish.stderr
    finished = json.loads(finish.stdout)
    assert finished["finished"]
    assert finished["resolve_rebuild_required"]
