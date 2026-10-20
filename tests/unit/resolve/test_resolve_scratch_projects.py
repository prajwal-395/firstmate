from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import resolve_axi
from library.tools import resolve_scratch_projects as scratch


@pytest.fixture(autouse=True)
def local_lifecycle_log(monkeypatch, tmp_path):
    monkeypatch.setattr(scratch, "_lifecycle_log_path",
                        lambda: tmp_path / "event_log.jsonl")
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held",
                        lambda: True)
    monkeypatch.setattr("library.tools.shared_environment.vep_home",
                        lambda: tmp_path)


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
        self.settings = {}
        self.uid = f"project-{name}"

    def GetUniqueId(self):
        return self.uid

    def GetSetting(self, key):
        return self.settings.get(key, "")

    def SetSetting(self, key, value):
        self.settings[key] = str(value)
        return True

    def SetSettings(self, values):
        for key, value in values.items():
            self.settings[key] = str(value)
        return True

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

    def ExportProject(self, name, path):
        Path(path).write_bytes(f"export:{name}".encode())
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
    scratch.create(resolve, manager, name)
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


def test_create_with_fps_sets_before_first_timeline_and_reads_back(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    result = scratch.create(resolve, manager, "Ren Scratch - fps", fps="23.976")
    created = manager.projects[result["project"]]
    assert created.settings["timelineFrameRate"] == "23.976"
    assert result["requested"] == {"timelineFrameRate": "23.976"}
    assert result["fps"] == pytest.approx(23.976)
    assert created.GetTimelineCount() == 1


def test_create_with_resolution_sets_width_and_height(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    result = scratch.create(
        resolve, manager, "Ren Scratch - shape", fps="29.97",
        width="1920", height="1080")
    created = manager.projects[result["project"]]
    assert created.settings["timelineResolutionWidth"] == "1920"
    assert created.settings["timelineResolutionHeight"] == "1080"
    assert result["resolution"] == "1920x1080"
    assert result["fps"] == pytest.approx(29.97)


def test_create_without_shape_leaves_resolve_default(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    result = scratch.create(resolve, manager, "Ren Scratch - default")
    created = manager.projects[result["project"]]
    assert created.settings == {}
    assert "requested" not in result
    assert result["fps"] is None
    assert result["resolution"] is None


def test_create_refuses_bad_shape_before_touching_resolve(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    for kwargs in ({"fps": "not-a-rate"}, {"fps": "0"}, {"fps": "-29.97"},
                   {"width": "1920"}, {"height": "1080"},
                   {"width": "0", "height": "1080"}):
        with pytest.raises(scratch.ScratchProjectError):
            scratch.create(resolve, manager, "Ren Scratch - bad-shape", **kwargs)
    assert "Ren Scratch - bad-shape" not in manager.projects


def test_create_refuses_when_rate_does_not_read_back(monkeypatch):
    resolve, manager = prepare(monkeypatch, captain())
    original = Manager.CreateProject

    def lying_create(self, name):
        project = original(self, name)
        project.SetSetting = lambda key, value: True  # claims the write, stores nothing
        project.SetSettings = lambda values: True
        return project

    monkeypatch.setattr(Manager, "CreateProject", lying_create)
    with pytest.raises(scratch.ScratchProjectError, match="refused"):
        scratch.create(resolve, manager, "Ren Scratch - lying", fps="29.97")


def test_apply_shape_refuses_a_project_that_already_has_timelines():
    project = Project("Ren Scratch - lived-in", [Timeline("T", "t")])
    with pytest.raises(scratch.ScratchProjectError, match="deadlocks"):
        scratch._apply_project_shape(project, project.name, 29.97, None)


def test_cli_forwards_fps_and_resolution_to_create(monkeypatch, capsys):
    seen = {}

    def fake_create(resolve, manager, name, fps=None, width=None, height=None):
        seen.update(name=name, fps=fps, width=width, height=height)
        return {"project": name, "verified": True}

    monkeypatch.setattr(scratch, "create", fake_create)
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: nullcontext())
    monkeypatch.setattr(resolve_axi, "_connect", lambda: captain())
    args = SimpleNamespace(scratch_command="create", apply=True,
                           name="Ren Scratch - cli-shape",
                           fps="29.97", width="1920", height="1080")
    assert resolve_axi.cmd_project_scratch(args) == 0
    assert seen == {"name": "Ren Scratch - cli-shape", "fps": "29.97",
                    "width": "1920", "height": "1080"}


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
        lambda resolve, manager, name, fps=None, width=None, height=None: {
            "project": name, "verified": True})
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


def test_a_recorded_origin_can_be_recovered_when_resolve_reopened_the_captains_project(
        monkeypatch, tmp_path):
    """Catches: the scratch origin deadlocking.

    `open_project` refuses while an origin is recorded, and
    `restore_recorded` refuses unless the SCRATCH project is open. After
    Resolve reopened the captain's project - a broker restart does
    exactly that - the pair could not move: the machine was already
    where the record wanted it, and neither command would say so.

    Measured 2026-10-20 on the Ren Scratch - breaktest project: the
    origin record had to be removed by hand before the scratch project
    could be opened again.
    """
    resolve, manager = prepare(monkeypatch, captain())
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    scratch_project = Project("Ren Scratch - recover-test", [Timeline("T", "t")])
    manager.projects[scratch_project.name] = scratch_project

    scratch.open_project(resolve, manager, scratch_project.name)
    assert origin.exists()

    # Resolve reopened the captain's project: the scratch project is no
    # longer open, and the machine is already where the record wanted it.
    manager.current = manager.projects["Podcast (field test)"]

    restored = scratch.restore_recorded(resolve, manager)
    assert restored["verified"] is True
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert not origin.exists()


def test_a_reimported_timeline_recovers_by_unique_name_only_in_ren_projects(
        monkeypatch):
    """Catches: a restored Ren DRT gets a new UID and strands its receipt."""
    resolve, manager = prepare(
        monkeypatch, Resolve(Project("Ren Qualification", [
            Timeline("fm-scratch qualify", "new-drt-uid")]))
    )
    state = {"project": "Ren Qualification", "timeline": "fm-scratch qualify",
             "timeline_id": "deleted-old-uid", "page": "edit"}

    restored = scratch.restore(resolve, manager, state)

    assert restored["verified"] is True
    assert restored["timeline_identity_recovered"] is True
    assert restored["recorded_timeline_id"] == "deleted-old-uid"
    assert restored["timeline_id"] == "new-drt-uid"
    current = manager.GetCurrentProject().GetCurrentTimeline()
    assert current.GetUniqueId() == "new-drt-uid"


def test_a_missing_captain_timeline_id_never_recovers_by_name(monkeypatch):
    """Catches: same-named captain timelines must retain exact-UID protection."""
    resolve, manager = prepare(monkeypatch, captain())
    state = {"project": "Podcast (field test)", "timeline": "Main",
             "timeline_id": "deleted-captain-uid", "page": "edit"}

    with pytest.raises(scratch.ScratchProjectError,
                       match="captain projects require exact identity"):
        scratch.restore(resolve, manager, state)


def test_drt_restore_reopens_the_recorded_ren_origin_before_import(
        monkeypatch, tmp_path):
    """Catches: imports were refused while the recorded scratch was open."""
    qualification = Project("Ren Qualification", [])
    resolve = Resolve(qualification)
    manager = resolve.manager
    scratch_project = Project("Ren Scratch - breaktest", [
        Timeline("Staging", "scratch-staging")])
    manager.projects[scratch_project.name] = scratch_project
    manager.current = scratch_project
    prepare(monkeypatch, resolve)
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    state = {
        "project": "Ren Qualification",
        "scratch_project": scratch_project.name,
        "timeline": None,
        "timeline_id": None,
        "page": "deliver",
    }
    origin.write_text(json.dumps(state), encoding="utf-8")
    imported = [{"name": "fm-scratch qualify", "timeline_id": "new-id"}]
    calls = []

    def import_drts(project, archives):
        calls.append((project.GetName(), tuple(archives)))
        return imported

    monkeypatch.setattr(scratch, "_import_qualification_drts", import_drts)

    result = scratch.restore_recorded(resolve, manager,
                                      timeline_drts=("fixture.drt",))

    assert calls == [("Ren Qualification", ("fixture.drt",))]
    assert manager.GetCurrentProject().GetName() == "Ren Qualification"
    assert resolve.GetCurrentPage() == "deliver"
    assert result["imported_timeline_drts"] == imported
    assert result["verified"] is True
    assert not origin.exists()


def test_an_empty_untitled_project_does_not_block_the_scratch_lifecycle(
        monkeypatch, tmp_path):
    """Catches: the scratch lifecycle refusing to leave the untitled
    project a relaunch of Resolve leaves behind.

    Measured 2026-10-20 on the Ren Scratch - breaktest project, after
    Resolve had crashed and come back on an empty untitled project:
    `project scratch open` refused with "cannot switch from 'Untitled
    Project': Resolve would not save it", and the AXI CLI refused the
    way out with "Resolve project 'Untitled Project' has no timeline
    open". Nothing but a person in Resolve's UI could move.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    untitled = Project("Untitled Project", [])
    manager.projects["Untitled Project"] = untitled
    manager.current = untitled
    monkeypatch.setattr(manager, "SaveProject", lambda: False)

    scratch_project = Project("Ren Scratch - untitled-test", [Timeline("T", "t")])
    manager.projects[scratch_project.name] = scratch_project

    result = scratch.open_project(resolve, manager, scratch_project.name)
    assert result["opened"] is True
    assert manager.GetCurrentProject().GetName() == scratch_project.name


def test_a_recorded_origin_completes_when_its_timeline_is_gone(
        monkeypatch, tmp_path):
    """Catches: the restore refusing because the timeline it recorded is
    gone - the reel was rebuilt since - when the machine is already back
    on the origin's project.

    Measured 2026-10-20 on the Ren Scratch - breaktest project:
    "restore cannot find timeline 'fm-scratch qualify' by its recorded
    id" refused the whole restore, so the scratch project could not be
    opened again.
    """
    resolve, manager = prepare(monkeypatch, captain())
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    scratch_project = Project("Ren Scratch - gone-timeline", [Timeline("T", "t")])
    manager.projects[scratch_project.name] = scratch_project

    scratch.open_project(resolve, manager, scratch_project.name)
    assert origin.exists()

    # The origin's project is open again, but its recorded timeline is
    # not there any more.
    manager.current = manager.projects["Podcast (field test)"]
    manager.projects["Podcast (field test)"].timelines = [
        Timeline("Other", "other-uid")]

    restored = scratch.restore_recorded(resolve, manager)
    assert restored["timeline_restored"] is False
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert not origin.exists()


def test_missing_ren_owned_origin_timeline_reopens_recorded_scratch_project(
        monkeypatch, tmp_path):
    """Catches restore depending on the Ren Qualification fixture timeline.

    After a broker restart the origin project may be open, but qualification
    refreshes can replace its timeline ids. Recovery must return to the
    recorded scratch project without requiring that fixture timeline.
    """
    resolve, manager = prepare(monkeypatch, captain())
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    qualification = Project(
        "Ren Qualification", [Timeline("fm-scratch qualify", "old-fixture")])
    scratch_project = Project(
        "Ren Scratch - breaktest", [Timeline("Reel 13", "copied-reel")])
    manager.projects[qualification.name] = qualification
    manager.projects[scratch_project.name] = scratch_project
    manager.current = qualification
    monkeypatch.setattr(
        scratch, "switch_project",
        lambda current_manager, name: current_manager.LoadProject(name))

    scratch.open_project(resolve, manager, scratch_project.name)
    assert manager.GetCurrentProject().GetName() == scratch_project.name

    # A broker restart put Resolve back on the origin project, whose fixture
    # timeline has since been replaced.
    qualification.timelines = []
    qualification.current = None
    manager.current = qualification

    restored = scratch.restore_recorded(resolve, manager)

    assert restored["project"] == scratch_project.name
    assert restored["timeline"] == "Reel 13"
    assert restored["verified"] is True
    assert restored["timeline_restored"] is False
    assert manager.GetCurrentProject().GetName() == scratch_project.name
    assert not origin.exists()
