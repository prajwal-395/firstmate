"""resolve.qualification_session: one borrowed job for every lane.

The defect this pins: every lane opened its own scripting connection
for borrowed Resolve work, and on 2026-10-06 three lanes' handshakes
beside the broker's crashed Resolve in scripting-thread shutdown
(EXC_GUARD, fusionscript WaitAllThreads). Now one broker job kind runs
a submitted step list inside borrowed() on the broker's own persistent
connection - no step shakes a new hand - with the switch settle, the
session marker the watcher stands down on, and restore with read-back.
"""

import json

import pytest

from library.tools.resolved import jobs
from library.tools.resolved.jobs import JobRefused


class _Timeline:
    def __init__(self, name, uid):
        self.name, self.uid = name, uid

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid


class _Project:
    def __init__(self, name, timelines):
        self.name, self.timelines = name, timelines
        self.current = timelines[0] if timelines else None

    def GetName(self):
        return self.name

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True


class _Manager:
    def __init__(self, *projects):
        self.projects = {p.name: p for p in projects}
        self.open = projects[0]
        self.saved = []
        self.pages = ["edit"]

    def GetProjectManager(self):
        return self

    def GetCurrentPage(self):
        return self.pages[-1]

    def OpenPage(self, page):
        self.pages.append(page)
        return True

    def GetCurrentProject(self):
        return self.open

    def SaveProject(self):
        self.saved.append(self.open.name)
        return True

    def LoadProject(self, name):
        self.open = self.projects.get(name)
        return self.open


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """The session marker lives beside the lock: isolate both."""
    from library.tools import resolve_lock

    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    return directory


@pytest.fixture
def resolve():
    captain = _Project("Podcast (field test)",
                       [_Timeline("Reel 11", "uid-reel-11")])
    qualification = _Project("Ren Qualification",
                             [_Timeline("Q Spine", "uid-q")])
    return _Manager(captain, qualification)


def _params(**over):
    params = {"project": "Podcast (field test)",
              "steps": [{"resolve_axi": ["api", "search", "marker"]}],
              **over}
    return params


# ─── prepare: the fail-closed boundary ──────────────────────────


def test_prepare_refuses_what_cannot_run():
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session", {"project": "X"})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": []})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": ["run"]})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": [
                         {"resolve_axi": ["frobnicate"]}]})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": [
                         {"resolve_axi": ["api", "search"]}]})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": [
                         {"ui_transaction": {"actions": []}}]})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": [
                         {"resolve_axi": ["pool"],
                          "ui_transaction": {"actions": []}}]})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"project": "X", "steps": [{"mystery": {}}]})
    with pytest.raises(JobRefused):
        jobs.prepare("resolve.qualification_session",
                     {"steps": [{"resolve_axi": ["pool"]}]})


def test_prepare_holds_the_whole_take():
    from library.tools.resolved import scheduler

    shape = jobs.prepare("resolve.qualification_session", _params())
    assert shape["mode"] == scheduler.EXCLUSIVE
    assert shape["priority"] == "mutation"
    assert shape["executed"] is True
    assert shape["project"] == "Podcast (field test)"
    assert shape["coalesce_key"] is None


# ─── run: borrowed, stepped, restored ───────────────────────────


def _stub_runners(monkeypatch, calls, failing_at=None):
    def fake_axi(params):
        calls.append(("resolve_axi", params["argv"]))
        if failing_at == len(calls) - 1:
            return {"exit_code": 1, "stdout": "", "stderr": "boom"}
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    def fake_uitxn(job, resolve):
        from library.tools import qualification_project as qp
        from library.tools import shadow_watcher

        calls.append(("ui_transaction", job["id"]))
        assert qp.qualification_session_active() is not None, (
            "the watcher stands down off the marker - a step running "
            "unmarked reopens the collision")
        assert shadow_watcher.should_stand_down() is not None
        assert (resolve.GetProjectManager().GetCurrentProject()
                .GetName() == "Ren Qualification")
        return {"status": "committed", "result": {"committed": True}}

    monkeypatch.setattr(jobs, "_run_resolve_axi", fake_axi)
    from library.tools.resolved import ui_transaction
    monkeypatch.setattr(ui_transaction, "run", fake_uitxn)


def test_run_borrows_steps_and_restores(resolve, lock_dir, monkeypatch):
    """The session the watcher relies on: marked while steps run (so it
    stands down), unmarked and restored after. On origin/main the kind
    is unknown, so every assertion here fails there."""
    calls = []
    _stub_runners(monkeypatch, calls)
    from library.tools import qualification_project as qp

    job = {"id": "session-1", "kind": "resolve.qualification_session", "params": _params(steps=[
        {"resolve_axi": ["pool"]},
        {"ui_transaction": {"project": "Ren Qualification",
                            "actions": [{"action": "wait",
                                         "for": {"seconds": 0.01}}]}},
    ])}
    receipt = jobs.run(job, resolve)
    assert [kind for kind, _ in calls] == ["resolve_axi",
                                           "ui_transaction"]
    assert receipt["steps"][0]["exit_code"] == 0
    assert receipt["borrowed_from"]["project"] == "Podcast (field test)"
    assert receipt["restored"]["timeline"] == "Reel 11"
    assert resolve.GetCurrentProject().GetName() == "Podcast (field test)"
    podcast = resolve.projects["Podcast (field test)"]
    assert podcast.GetCurrentTimeline().GetName() == "Reel 11"
    assert resolve.GetCurrentPage() == "edit"
    assert qp.qualification_session_active() is None


def test_failing_step_stops_the_run_and_still_restores(
        resolve, lock_dir, monkeypatch):
    calls = []
    _stub_runners(monkeypatch, calls, failing_at=1)
    from library.tools import qualification_project as qp

    job = {"id": "session-2", "kind": "resolve.qualification_session", "params": _params(steps=[
        {"resolve_axi": ["pool"]},
        {"resolve_axi": ["pool"]},
        {"resolve_axi": ["pool"]},
    ])}
    with pytest.raises(JobRefused) as excinfo:
        jobs.run(job, resolve)
    assert "step 1" in str(excinfo.value)
    assert len(calls) == 2
    assert resolve.GetCurrentProject().GetName() == "Podcast (field test)"
    assert qp.qualification_session_active() is None


def test_failed_transaction_step_refuses_by_status(
        resolve, lock_dir, monkeypatch):
    from library.tools.resolved import ui_transaction

    monkeypatch.setattr(
        ui_transaction, "run",
        lambda job, resolve: {"status": "failed",
                              "result": {"error": "timed out"}})
    job = {"id": "session-3", "kind": "resolve.qualification_session", "params": _params(steps=[
        {"ui_transaction": {"project": "Ren Qualification",
                            "actions": [{"action": "wait",
                                         "for": {"seconds": 0.01}}]}},
    ])}
    with pytest.raises(JobRefused) as excinfo:
        jobs.run(job, resolve)
    assert "failed" in str(excinfo.value)
    assert resolve.GetCurrentProject().GetName() == "Podcast (field test)"
