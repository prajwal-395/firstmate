"""Branch lifecycle events: a task branch opening and committing.

The spec's "everything should be event-sourced": a worker's branch
(`ren/<task>`) is where its work happens, and the commit is the point
it becomes durable. The worktree listing says WHAT branches exist; the
event says WHEN each opened, from WHICH base, and when it committed -
the operation history a scheduler reads to reassign a dead worker's
task. Every test names a defect it catches: on origin/main opening or
committing a branch writes no event, so a task's whole branch
lifecycle is missing from the operation history.
"""
import json

import pytest

from library.tools import event_log
from library.tools.event_log import read_events
from library.tools.versions import store, worktrees


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A git project with a LOGS area, never a real one."""
    monkeypatch.setenv(worktrees.ROOT_ENV, str(tmp_path / "worktrees"))
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "pipeline_output" / "logs").mkdir(parents=True)
    assert store.init_project_repo(str(folder))["initialised"]
    (folder / "project.yaml").write_text("name: p\n", encoding="utf-8")
    assert store.commit_build(str(folder), "base")["committed"]
    return str(folder)


def test_add_leaves_a_branch_created_event(project):
    """Opening a branch is an operation: the log names the branch and base.

    Defect caught: the branch exists in git but the event log - the
    history a resumed worker filters by task - never mentions it, so
    the task's work starts mid-air.
    """
    result = worktrees.add(project, "captions")
    assert result["added"], result
    created = [e for e in read_events(project)
               if e["type"] == event_log.BRANCH_CREATED]
    assert len(created) == 1
    assert created[0]["task"] == "captions"
    assert created[0]["fields"] == {
        "branch": "ren/captions",
        "base": result["base"],
    }


def test_commit_leaves_a_branch_committed_event(project):
    """Committing is an operation: the log says whose work went durable.

    Defect caught: the commit is in git but no event marks it, so a
    scheduler reassigning the task cannot tell from the operation
    history whether the dead worker's work was committed.
    """
    assert worktrees.add(project, "captions")["added"]
    path = worktrees.task_path(project, "captions")
    declarations = path / "external" / "declarations"
    declarations.mkdir(parents=True, exist_ok=True)
    (declarations / "captain_edits.json").write_text(
        json.dumps({"edits": ["caption 3 lower"]}), encoding="utf-8")
    assert worktrees.commit(project, "captions", "a")["committed"]
    committed = [e for e in read_events(project)
                 if e["type"] == event_log.BRANCH_COMMITTED]
    assert len(committed) == 1
    assert committed[0]["task"] == "captions"
    assert committed[0]["fields"] == {"branch": "ren/captions"}


def test_a_refused_add_leaves_no_event(project):
    """No invented operations: a branch that never opened emits nothing.

    Defect caught: an emit before the failure checks would record a
    branch creation for a task whose worktree never existed, and a
    resumed worker would read a history of things that never happened.
    """
    assert worktrees.add(project, "captions")["added"]
    second = worktrees.add(project, "captions")
    assert second["added"] is False
    assert len([e for e in read_events(project)
                if e["type"] == event_log.BRANCH_CREATED]) == 1


def test_a_log_failure_never_takes_down_task_work(project, monkeypatch):
    """The branch is the act, the event is its record.

    Defect caught: an emit that raises through `add` turns a logging
    failure into a refused branch - the same refusal the lease layer
    already refuses to make (`resource_leases._emit`).
    """
    def _boom(*args, **kwargs):
        raise OSError("disk gone")
    monkeypatch.setattr(event_log, "emit", _boom)
    result = worktrees.add(project, "captions")
    assert result["added"], result
