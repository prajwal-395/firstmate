"""Concurrent tasks on one project each hold their own semantic state.

The defect: the project store has ONE checkout and a branch is a property
of it, so a task that branched (`variants.create_variation`) moved every
other task's writes onto its branch - the captain's geo-podcast checkout
sat on a variant branch for three days with 127 tracked edits on it.
`library/tools/versions/worktrees.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.versions import store, worktrees


def _branch(folder) -> str:
    return store.git(folder, "rev-parse", "--abbrev-ref",
                     "HEAD").stdout.strip()


def _write(folder, rel, document) -> None:
    path = folder / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv(worktrees.ROOT_ENV, str(tmp_path / "worktrees"))
    folder = tmp_path / "project"
    folder.mkdir()
    assert store.init_project_repo(str(folder))["initialised"]
    (folder / "project.yaml").write_text("name: p\n", encoding="utf-8")
    _write(folder, "external/captain_edits.json", {"edits": []})
    _write(folder, "external/reel_ending.json", {"ending": "cut"})
    assert store.commit_build(str(folder), "base")["committed"]
    return folder


def test_two_tasks_hold_separate_states_and_both_merge(project):
    home = _branch(project)
    a = worktrees.add(str(project), "captions")
    b = worktrees.add(str(project), "Reel 07 ending")
    assert a["added"] and b["added"], (a, b)
    assert b["branch"] == "ren/reel-07-ending"

    # Each task edits a different declaration in its own checkout.
    _write(Path(a["path"]), "external/captain_edits.json",
           {"edits": ["caption 3 lower"]})
    _write(Path(b["path"]), "external/reel_ending.json",
           {"ending": "hold"})
    assert worktrees.commit(str(project), "captions", "a")["committed"]
    assert worktrees.commit(str(project), "reel 07 ending", "b")["committed"]

    # Neither task moved the project checkout or wrote into it.
    assert _branch(project) == home
    assert json.loads((project / "external/reel_ending.json").read_text())[
        "ending"] == "cut"
    assert {t["task"] for t in worktrees.list_tasks(str(project))} == {
        "captions", "reel-07-ending"}

    for task in ("captions", "reel 07 ending"):
        merged = worktrees.merge(str(project), task)
        assert merged["merged"], merged
        assert worktrees.remove(str(project), task)["branch_deleted"]
    assert _branch(project) == home
    assert json.loads((project / "external/captain_edits.json").read_text())[
        "edits"] == ["caption 3 lower"]
    assert json.loads((project / "external/reel_ending.json").read_text())[
        "ending"] == "hold"
    assert worktrees.list_tasks(str(project)) == []


def test_conflicting_declarations_reach_a_human_and_uncommitted_work_stays(
        project):
    for task, ending in (("one", "hold"), ("two", "fade")):
        opened = worktrees.add(str(project), task)
        _write(Path(opened["path"]), "external/reel_ending.json",
               {"ending": ending})
        if task == "two":
            # Uncommitted work is never merged and never removed.
            assert not worktrees.merge(str(project), task)["merged"]
            assert not worktrees.remove(str(project), task)["removed"]
        assert worktrees.commit(str(project), task, task)["committed"]

    assert worktrees.merge(str(project), "one")["merged"]
    second = worktrees.merge(str(project), "two")
    assert not second["merged"]
    assert second["conflicts"] == ["external/reel_ending.json"]
