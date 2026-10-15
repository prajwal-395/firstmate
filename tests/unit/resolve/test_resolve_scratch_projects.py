from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from library.tools import resolve_scratch_projects as scratch
from library.tools import resolve_axi


class Timeline:
    def __init__(self, name, uid):
        self.name, self.uid = name, uid

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid


class MediaPool:
    def __init__(self, project):
        self.project = project

    def CreateEmptyTimeline(self, name):
        timeline = Timeline(name, f"uid-{len(self.project.timelines) + 1}")
        self.project.timelines.append(timeline)
        self.project.current = timeline
        return timeline


class Project:
    def __init__(self, name, timelines=()):
        self.name = name
        self.timelines = list(timelines)
        self.current = self.timelines[0] if self.timelines else None

    def GetName(self):
        return self.name

    def GetIsProjectDirty(self):
        return False

    def GetCurrentTimeline(self):
        return self.current

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True

    def GetMediaPool(self):
        return MediaPool(self)


class Manager:
    def __init__(self, *projects):
        self.projects = {p.name: p for p in projects}
        self.current = projects[0]
        self.saved = []
        self.save_ok = True

    def GetCurrentProject(self):
        return self.current

    def GetProjectListInCurrentFolder(self):
        return list(self.projects)

    def CreateProject(self, name):
        self.current = Project(name)
        self.projects[name] = self.current
        return self.current

    def SaveProject(self):
        self.saved.append(
            self.current.GetName() if self.current else None)
        return self.save_ok

    def LoadProject(self, name):
        self.current = self.projects[name]
        if self.current.timelines:
            self.current.current = self.current.timelines[0]
        return self.current

    def DeleteProject(self, name):
        del self.projects[name]
        return True

    def ImportProject(self, path, name):
        self.projects[name] = Project(name, [Timeline("Imported", "imported-1")])
        return True


class Resolve:
    def __init__(self, project):
        self.manager = Manager(project)
        self.page = "edit"

    def GetCurrentPage(self):
        return self.page

    def GetProjectManager(self):
        return self.manager

    def OpenPage(self, page):
        self.page = page
        return True


def prepare(monkeypatch, resolve):
    monkeypatch.setattr(scratch, "switch_project",
                        lambda manager, name: manager.LoadProject(name))
    monkeypatch.setattr(scratch, "assert_current_timeline",
                        lambda project, timeline: project.SetCurrentTimeline(timeline))
    monkeypatch.setattr(scratch.resolve_switch_guard, "note_project_switch", lambda: None)
    monkeypatch.setattr(scratch.resolve_switch_guard, "wait_for_settle", lambda: None)
    return resolve, resolve.manager


def captain():
    first = Timeline("Main", "captain-main")
    alternate = Timeline("Alt", "captain-alt")
    project = Project("Podcast (field test)", [first, alternate])
    project.current = alternate
    return Resolve(project)


def test_create_builds_one_empty_current_timeline_and_restores_origin(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    result = scratch.create(resolve, manager, "Ren Scratch - create-test")
    created = manager.projects[result["project"]]
    assert created.GetTimelineCount() == 1
    assert created.GetCurrentTimeline().GetName() == result["timeline"]
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert manager.GetCurrentProject().GetCurrentTimeline().GetUniqueId() == "captain-alt"
    assert result["restored"] == {
        "project": "Podcast (field test)", "timeline": "Alt",
        "page": "edit", "verified": True}


def test_open_uses_switch_and_reads_back(monkeypatch, tmp_path):
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    scratch_project = Project("Ren Scratch - open-test", [Timeline("T", "t")])
    manager.projects[scratch_project.name] = scratch_project
    result = scratch.open_project(resolve, manager, scratch_project.name)
    assert result["opened"] is True
    assert manager.GetCurrentProject().GetName() == scratch_project.name
    assert result["origin"]["timeline"] == "Alt"
    restored = scratch.restore_recorded(resolve, manager)
    assert restored["verified"] is True
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert manager.GetCurrentProject().GetCurrentTimeline().GetUniqueId() == "captain-alt"
    assert not (tmp_path / "scratch-origin.json").exists()


def test_import_requires_drp_and_restores_origin(monkeypatch, tmp_path):
    resolve, manager = prepare(monkeypatch, captain())
    drp = tmp_path / "source.drp"
    drp.write_bytes(b"fixture")
    result = scratch.import_project(
        resolve, manager, drp, "Ren Scratch - import-test")
    assert result["imported"] is True
    assert "Ren Scratch - import-test" in manager.projects
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert manager.GetCurrentProject().GetCurrentTimeline().GetUniqueId() == "captain-alt"


def test_delete_refuses_open_and_non_scratch_projects(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    with pytest.raises(scratch.ScratchProjectError, match="non-scratch"):
        scratch.delete(resolve, manager, "Podcast (field test)")
    current = Project("Ren Scratch - current", [Timeline("T", "current")])
    manager.projects[current.name] = current
    manager.current = current
    with pytest.raises(scratch.ScratchProjectError, match="open project"):
        scratch.delete(resolve, manager, current.name)


def test_delete_removes_only_closed_scratch_and_restores_origin(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    name = "Ren Scratch - delete-test"
    manager.projects[name] = Project(name)
    result = scratch.delete(resolve, manager, name)
    assert name not in manager.projects
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert result["restored"]["timeline"] == "Alt"


def test_name_and_unsavable_state_fail_closed(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    with pytest.raises(scratch.ScratchProjectError, match="must start"):
        scratch.create(resolve, manager, "Podcast (field test)")
    manager.save_ok = False
    with pytest.raises(scratch.ScratchProjectError, match="would not save"):
        scratch.create(resolve, manager, "Ren Scratch - unsavable")
    assert "Ren Scratch - unsavable" not in manager.projects


def test_create_saves_the_origin_before_switching(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    scratch.create(resolve, manager, "Ren Scratch - save-test")
    assert manager.saved[0] == "Podcast (field test)"


def test_restore_saves_the_scratch_before_restoring(monkeypatch, tmp_path):
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    scratch_project = Project("Ren Scratch - save-restore", [Timeline("T", "t")])
    manager.projects[scratch_project.name] = scratch_project
    scratch.open_project(resolve, manager, scratch_project.name)
    manager.saved.clear()
    scratch.restore_recorded(resolve, manager)
    assert manager.saved == [scratch_project.name]


def test_cli_takes_exclusive_lease_before_connecting(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: (events.append(("lease", exclusive))
                                           or nullcontext()))
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: (events.append(("connect",)) or captain()))
    monkeypatch.setattr(
        scratch, "create",
        lambda resolve, manager, name: {"project": name, "verified": True})
    args = SimpleNamespace(scratch_command="create", apply=True,
                           name="Ren Scratch - cli-test")
    assert resolve_axi.cmd_project_scratch(args) == 0
    assert events == [("lease", True), ("connect",)]
    assert "Ren Scratch - cli-test" in capsys.readouterr().out


def test_recorded_run_script_cannot_import_a_project(monkeypatch, tmp_path,
                                                     capsys):
    """Raw ProjectManager lifecycle stays blocked in recorded scripts.

    The sanctioned import path is `project scratch import`; a recorded
    `run` script naming `manager.ImportProject` is refused as a project
    switch before any Resolve connection is opened."""
    monkeypatch.chdir(tmp_path)
    args = resolve_axi.build_parser().parse_args([
        "run", "--purpose", "Import scratch fixture", "--unsafe",
        "--script",
        "manager.ImportProject('/tmp/source.drp', 'Ren Scratch - x')\n"
        "result = {'imported': True}",
    ])
    assert resolve_axi.cmd_run(args) == 1
    out = capsys.readouterr().out
    assert "may not switch" in out
    assert "ImportProject" in out


def test_no_switch_manager_refuses_import_at_runtime():
    """The runtime guard matches the static refusal above."""
    from library.tools.resolve_axi import AxiError
    manager = Manager(Project("Podcast (field test)"))
    view = resolve_axi._NoProjectSwitchManager(manager)
    with pytest.raises(AxiError, match="cannot call ProjectManager.ImportProject"):
        view.ImportProject("/tmp/source.drp", "Ren Scratch - x")
