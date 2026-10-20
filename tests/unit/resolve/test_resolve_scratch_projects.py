from contextlib import nullcontext
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from library.tools import resolve_axi
from library.tools import resolve_scratch_projects as scratch
from library.tools.qualification_project import QualificationError


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

    def SetName(self, name):
        self.name = name
        return True

    def GetTrackCount(self, media_type):
        return 1 if media_type in {"video", "audio"} else 0

    def GetItemListInTrack(self, media_type, index):
        return []


class MediaPool:
    def __init__(self, project):
        self.project = project

    def CreateEmptyTimeline(self, name):
        timeline = Timeline(name, f"uid-{len(self.project.timelines) + 1}")
        self.project.timelines.append(timeline)
        self.project.current = timeline
        return timeline

    def ImportTimelineFromFile(self, path, options=None):
        name = (options or {}).get("timelineName", Path(path).stem)
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


def _ledger(tmp_path):
    """The recorded origins, oldest first."""
    path = tmp_path / "scratch-origin.json"
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    records = document.get("records")
    if records is None:
        return [document]
    return records


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
    assert _ledger(tmp_path) == []


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


def test_import_drt_creates_owned_scratch_timeline_and_restores_origin(monkeypatch, tmp_path):
    resolve, manager = prepare(monkeypatch, captain())
    drt = tmp_path / "reel.drt"
    drt.write_bytes(b"timeline fixture")
    result = scratch.import_project(
        resolve, manager, drt, "Ren Scratch - timeline-import",
        fps="23.976", width=1920, height=1080)
    created = manager.projects["Ren Scratch - timeline-import"]
    assert result["timeline"] == "reel"
    assert result["timeline_count"] == 1
    assert result["fps"] == pytest.approx(23.976)
    assert result["resolution"] == "1920x1080"
    assert created.settings["timelineFrameRate"] == "23.976"
    assert created.settings["timelineResolutionWidth"] == "1920"
    assert created.settings["timelineResolutionHeight"] == "1080"
    assert len(result["owned_artifacts"]) == 2
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert manager.GetCurrentProject().GetCurrentTimeline().GetUniqueId() == "captain-alt"


def test_import_drt_does_not_require_project_list_to_refresh_immediately(monkeypatch, tmp_path):
    resolve, manager = prepare(monkeypatch, captain())
    drt = tmp_path / "reel.drt"
    drt.write_bytes(b"timeline fixture")
    listed = scratch._listed
    calls = 0

    def delayed_project_list(manager, name):
        nonlocal calls
        calls += 1
        return listed(manager, name) if calls == 1 else False

    monkeypatch.setattr(scratch, "_listed", delayed_project_list)
    result = scratch.import_project(
        resolve, manager, drt, "Ren Scratch - delayed-list",
        fps="23.976", width=3840, height=2160)
    assert result["timeline_count"] == 1
    assert result["fps"] == pytest.approx(23.976)
    assert result["resolution"] == "3840x2160"
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"


def test_import_drt_requires_shape_before_creating_project(monkeypatch, tmp_path):
    resolve, manager = prepare(monkeypatch, captain())
    drt = tmp_path / "reel.drt"
    drt.write_bytes(b"timeline fixture")
    with pytest.raises(scratch.ScratchProjectError, match="needs --fps, --width and --height"):
        scratch.import_project(resolve, manager, drt, "Ren Scratch - missing-shape")
    assert "Ren Scratch - missing-shape" not in manager.projects
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"


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


def test_drt_restore_recovers_a_misnamed_import_by_exact_archive_stem(
        monkeypatch, tmp_path):
    """Catches: Resolve named a DRT import after its filename, not its DRT."""
    from library.tools import drt_reader, resolve_archive

    path = tmp_path / "fm-scratch-qualify--fixture-id.drt"
    path.write_bytes(b"fixture")
    monkeypatch.setattr(resolve_archive, "read_archive", lambda _path: {
        "format": "drt", "project": "fm-scratch qualify",
        "timelines": [{}],
    })
    monkeypatch.setattr(drt_reader, "read_drt_archive", lambda _path: {
        "timelines": [{"tracks": [
            {"kind": "video", "items": []},
            {"kind": "audio", "items": []},
        ]}],
        "item_count": 0,
    })
    timeline = Timeline(path.stem, "imported-with-filename-name")
    project = Project("Ren Qualification", [timeline])

    result = scratch._import_qualification_drts(project, [path])

    assert timeline.GetName() == "fm-scratch qualify"
    assert result == [{
        "name": "fm-scratch qualify",
        "timeline_id": "imported-with-filename-name",
        "source": "recovered-renamed",
        "items": 0,
        "tracks": [["audio", 0], ["video", 0]],
        "archive": str(path.resolve()),
    }]


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


def test_ren_owned_origin_without_current_timeline_returns_to_scratch(
        monkeypatch, tmp_path):
    """Catches scratch restore switching to a Ren fixture with no timeline."""
    resolve, manager = prepare(monkeypatch, captain())
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    qualification = Project("Ren Qualification", [])
    scratch_project = Project(
        "Ren Scratch - breaktest", [Timeline("Reel 13", "copied-reel")])
    manager.projects[qualification.name] = qualification
    manager.projects[scratch_project.name] = scratch_project
    manager.current = qualification

    scratch.open_project(resolve, manager, scratch_project.name)
    recorded = _ledger(tmp_path)[-1]
    assert recorded["timeline_id"] is None

    # Broker restart returns to the Ren-owned origin, which has no timeline.
    manager.current = qualification
    restored = scratch.restore_recorded(resolve, manager)

    assert restored["project"] == scratch_project.name
    assert restored["timeline"] == "Reel 13"
    assert restored["verified"] is True
    assert restored["timeline_restored"] is False
    assert manager.GetCurrentProject().GetName() == scratch_project.name
    assert not origin.exists()




# ── One scratch origin, several writers ───────────────────────────────
#
# The scratch origin is ONE machine resource: the machine has one
# current project. Five lanes each need their own Ren-owned scratch
# project, and one lane's origin record must never decide another
# lane's open.
#
# Measured 2026-10-20: the live lane's `resolve-axi project scratch
# open` refused with "a scratch-project origin is already recorded;
# restore it before opening another scratch project", because the
# record on disk belonged to another lane - and, when that lane's
# writer had exited, with "the recorded scratch-project origin
# 'Podcast (field test)' is not Ren-owned; restore it by hand before
# opening another scratch project". Both refusals starved every other
# writer until a person removed the file by hand.


def _exited_pid() -> int:
    """A pid that is provably not running: a child that has been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def _write_ledger(path, records):
    path.write_text(json.dumps(
        {"format": scratch.ORIGIN_LEDGER_FORMAT, "records": records},
        sort_keys=True), encoding="utf-8")


def _record(scratch_project, origin_project, *, owner="lane-a",
            pid=None, owner_start=None):
    """One recorded origin, as the ledger holds it."""
    return {"project": origin_project, "timeline": "T", "timeline_id": "t",
            "page": "edit", "scratch_project": scratch_project,
            "owner": owner, "owner_pid": os.getpid() if pid is None else pid,
            "owner_host": socket.gethostname(),
            "owner_start": scratch._process_start(os.getpid())
            if owner_start is None else owner_start,
            "recorded_at": 1.0}


def test_the_origin_record_names_the_process_that_wrote_it(monkeypatch, tmp_path):
    """Catches: a record that names no owner.

    Without an owner on the record, a dead writer's record and a live
    writer's record are the same file, and only one of them may be
    cleared by the next writer.
    """
    resolve, manager = prepare(monkeypatch, captain())
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    scratch_project = Project("Ren Scratch - owner-test", [Timeline("T", "t")])
    manager.projects[scratch_project.name] = scratch_project

    scratch.open_project(resolve, manager, scratch_project.name)

    recorded = _ledger(tmp_path)[-1]
    assert recorded["owner_pid"] == os.getpid()
    assert recorded["owner_host"] == socket.gethostname()
    assert recorded["owner_start"] == scratch._process_start(os.getpid())


def test_two_lanes_open_two_scratch_projects_in_turn(monkeypatch, tmp_path):
    """Catches: one lane's scratch origin starving the next lane.

    Two lanes each need their own Ren-owned scratch project, and each
    restores its own before the next one opens. The record of the first
    lane must not decide the second lane's open, and the second lane's
    restore must not clear the first lane's route back.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    holder = {"name": "lane-a"}
    monkeypatch.setattr(scratch, "_owner_name", lambda: holder["name"])
    first = Project("Ren Scratch - lane-a", [Timeline("T", "t1")])
    second = Project("Ren Scratch - lane-b", [Timeline("T", "t2")])
    manager.projects[first.name] = first
    manager.projects[second.name] = second

    scratch.open_project(resolve, manager, first.name)
    assert manager.GetCurrentProject().GetName() == first.name

    scratch.restore_recorded(resolve, manager)
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"

    holder["name"] = "lane-b"
    result = scratch.open_project(resolve, manager, second.name)

    assert result["opened"] is True
    assert manager.GetCurrentProject().GetName() == second.name
    records = _ledger(tmp_path)
    assert [row["scratch_project"] for row in records] == [second.name]
    assert records[-1]["owner"] == "lane-b"
    assert records[-1]["project"] == "Podcast (field test)"

    scratch.restore_recorded(resolve, manager)
    assert manager.GetCurrentProject().GetName() == "Podcast (field test)"
    assert _ledger(tmp_path) == []


def test_a_second_lane_waits_for_a_live_holder_instead_of_being_refused(
        monkeypatch, tmp_path):
    """Catches: a live holder's session refused on the first sight.

    The scratch origin is one machine resource, so an open that finds it
    held by a LIVE writer waits for that writer to release it - the way
    the instance lease queues - rather than refusing. The wait ends
    when the holder restores its origin and clears its record.
    """
    import threading
    import time as clock

    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    holder = {"name": "lane-a"}
    monkeypatch.setattr(scratch, "_owner_name", lambda: holder["name"])
    first = Project("Ren Scratch - lane-a", [Timeline("T", "t1")])
    second = Project("Ren Scratch - lane-b", [Timeline("T", "t2")])
    manager.projects[first.name] = first
    manager.projects[second.name] = second

    scratch.open_project(resolve, manager, first.name)

    holder["name"] = "lane-b"
    outcome = {}

    def waiter():
        outcome["status"] = scratch.wait_for_origin_slot(timeout=10.0, poll=0.01)

    thread = threading.Thread(target=waiter)
    thread.start()
    clock.sleep(0.05)
    # It is WAITING, not refusing: the holder is still in its session.
    assert thread.is_alive()
    with pytest.raises(scratch.ScratchProjectError, match="held by"):
        scratch.open_project(resolve, manager, second.name)

    # The holder releases: the wait ends and the second lane opens.
    holder["name"] = "lane-a"
    scratch.restore_recorded(resolve, manager)
    thread.join(10)
    assert not thread.is_alive()
    assert outcome["status"]["released"] is True

    holder["name"] = "lane-b"
    result = scratch.open_project(resolve, manager, second.name)
    assert result["opened"] is True
    assert manager.GetCurrentProject().GetName() == second.name


def test_the_wait_names_the_holder_it_is_waiting_for(monkeypatch, tmp_path):
    """Catches: a bounded wait that says nothing about who holds the slot."""
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    holder = {"name": "lane-a"}
    monkeypatch.setattr(scratch, "_owner_name", lambda: holder["name"])
    scratch_project = Project("Ren Scratch - lane-a", [Timeline("T", "t1")])
    manager.projects[scratch_project.name] = scratch_project
    scratch.open_project(resolve, manager, scratch_project.name)

    holder["name"] = "lane-b"
    status = scratch.wait_for_origin_slot(timeout=0.05, poll=0.01)

    assert status["timed_out"] is True
    assert status["holder"] == "lane-a"
    assert status["released"] is False


def test_an_exited_holder_is_taken_back_instead_of_blocking(monkeypatch, tmp_path):
    """Catches: a dead writer's session blocking every other writer.

    Measured 2026-10-20: the queued AXI scratch open for "Ren Scratch -
    workflows-e2e" exited 1 with "a scratch-project origin is already
    recorded; restore it before opening another scratch project",
    because the receipt of an exited writer (pid 45907) was still on
    disk. The next open now puts the machine back on the recorded
    Ren-owned origin and clears the record the only safe way - by
    restoring it, never by deleting it.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    breaktest = Project("Ren Scratch - breaktest", [Timeline("Reel 13", "t13")])
    dead_scratch = Project("Ren Scratch - dead-owner", [Timeline("T", "t2")])
    next_scratch = Project("Ren Scratch - next", [Timeline("T", "t3")])
    for project in (breaktest, dead_scratch, next_scratch):
        manager.projects[project.name] = project
    _write_ledger(tmp_path / "scratch-origin.json", [
        _record(dead_scratch.name, breaktest.name, pid=_exited_pid())])
    # The dead writer left its scratch project open in Resolve.
    manager.current = dead_scratch

    result = scratch.open_project(resolve, manager, next_scratch.name)

    assert result["opened"] is True
    assert result["recovered_origin"]["project"] == breaktest.name
    assert manager.GetCurrentProject().GetName() == next_scratch.name
    records = _ledger(tmp_path)
    assert [row["scratch_project"] for row in records] == [next_scratch.name]
    assert records[-1]["project"] == breaktest.name


def test_a_recycled_pid_does_not_read_as_a_live_holder(monkeypatch, tmp_path):
    """Catches: a pid the machine reused reading as a live writer.

    Measured 2026-10-20: the live lane's open was refused with "a
    scratch-project origin is already recorded" while the recorded
    owner pid was alive - a number the machine had since given to
    another process. A pid alone cannot answer "is this the same
    writer", so the process's start is recorded with it.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    breaktest = Project("Ren Scratch - breaktest", [Timeline("Reel 13", "t13")])
    dead_scratch = Project("Ren Scratch - dead-owner", [Timeline("T", "t2")])
    next_scratch = Project("Ren Scratch - next", [Timeline("T", "t3")])
    for project in (breaktest, dead_scratch, next_scratch):
        manager.projects[project.name] = project
    _write_ledger(tmp_path / "scratch-origin.json", [
        _record(dead_scratch.name, breaktest.name,
                owner_start="Mon Jan  1 00:00:00 2024")])
    manager.current = dead_scratch

    result = scratch.open_project(resolve, manager, next_scratch.name)

    assert result["opened"] is True
    assert result["recovered_origin"]["project"] == breaktest.name


def test_a_captain_origin_is_named_and_never_restored_by_judgement(
        monkeypatch, tmp_path):
    """Catches: another writer moving the captain's project on its own.

    The recorded origin of an abandoned session is the captain's
    project. Restoring it is the HOLDER's act, not another writer's
    judgement, so the origin is NAMED and left where it is - and the
    open proceeds instead of refusing, because a refusal is what
    starved every other writer.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    dead_scratch = Project("Ren Scratch - dead-owner", [Timeline("T", "t2")])
    next_scratch = Project("Ren Scratch - next", [Timeline("T", "t3")])
    for project in (dead_scratch, next_scratch):
        manager.projects[project.name] = project
    _write_ledger(tmp_path / "scratch-origin.json", [
        _record(dead_scratch.name, "Podcast (field test)", pid=_exited_pid())])
    manager.current = dead_scratch

    result = scratch.open_project(resolve, manager, next_scratch.name)

    assert result["opened"] is True
    assert result["held_origin"]["origin"] == "Podcast (field test)"
    assert manager.GetCurrentProject().GetName() == next_scratch.name
    # The captain's project was never switched onto, and the abandoned
    # session's route back is still recorded.
    records = _ledger(tmp_path)
    assert [row["scratch_project"] for row in records] == [
        dead_scratch.name, next_scratch.name]
    assert records[-1]["project"] == dead_scratch.name


def test_an_abandoned_record_that_cannot_be_reopened_is_kept(
        monkeypatch, tmp_path):
    """Catches: the recovery clearing a record it could not restore.

    A record that cannot be restored is still the only route back, so
    it stays on disk and the refusal names the origin.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    gone = Project("Ren Scratch - gone", [Timeline("T", "t")])
    dead_scratch = Project("Ren Scratch - dead-owner", [Timeline("T", "t2")])
    next_scratch = Project("Ren Scratch - next", [Timeline("T", "t3")])
    for project in (gone, dead_scratch, next_scratch):
        manager.projects[project.name] = project
    _write_ledger(tmp_path / "scratch-origin.json", [
        _record(dead_scratch.name, gone.name, pid=_exited_pid())])
    manager.current = dead_scratch

    def refuse(current_manager, name):
        raise QualificationError(f"{name!r} did not read back after the switch")

    monkeypatch.setattr(scratch, "switch_project", refuse)

    with pytest.raises(scratch.ScratchProjectError, match="Ren Scratch - gone"):
        scratch.open_project(resolve, manager, next_scratch.name)
    assert _ledger(tmp_path)


def test_an_abandoned_record_waits_for_another_writer(monkeypatch, tmp_path):
    """Catches: the recovery moving Resolve while another writer holds it.

    The instance lease is the fence between writers, so an abandoned
    session is only taken back when nobody else is inside Resolve.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    breaktest = Project("Ren Scratch - breaktest", [Timeline("Reel 13", "t13")])
    dead_scratch = Project("Ren Scratch - dead-owner", [Timeline("T", "t2")])
    next_scratch = Project("Ren Scratch - next", [Timeline("T", "t3")])
    for project in (breaktest, dead_scratch, next_scratch):
        manager.projects[project.name] = project
    _write_ledger(tmp_path / "scratch-origin.json", [
        _record(dead_scratch.name, breaktest.name, pid=_exited_pid())])
    manager.current = dead_scratch
    monkeypatch.setattr(scratch, "_instance_lease_is_ours_or_free",
                        lambda: False)

    with pytest.raises(scratch.ScratchProjectError, match="held by another writer"):
        scratch.open_project(resolve, manager, next_scratch.name)
    assert _ledger(tmp_path)


def test_the_origin_ledger_lives_beside_the_instance_lease(
        monkeypatch, tmp_path):
    """Catches: a per-broker ledger hiding one broker's session.

    The ledger protects the one Resolve's current project, so every
    broker driving that Resolve has to see the same ledger.
    """
    monkeypatch.setenv("PIPELINE_RESOLVE_INSTANCE_DIR", str(tmp_path / "instance"))
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(tmp_path / "broker"))

    assert scratch._origin_path().parent == tmp_path / "instance"


def test_the_cli_queues_for_the_scratch_origin_between_attempts(
        monkeypatch, capsys):
    """Catches: the CLI refusing a queued open instead of waiting for it.

    The queue runs BETWEEN attempts and OUTSIDE the Resolve lease: the
    holder of the scratch origin has to be able to take that lease to
    restore and release it. A wait that ran under the lease would hold
    the very lock the holder needs.
    """
    events = []
    monkeypatch.setattr(scratch, "wait_for_origin_slot",
                        lambda timeout=None, poll=1.0: events.append(("wait",))
                        or {"released": True})
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: (events.append(("lease", exclusive))
                                           or nullcontext()))
    monkeypatch.setattr(resolve_axi, "_connect",
                        lambda: (events.append(("connect",)) or captain()))
    monkeypatch.setattr(
        scratch, "open_project",
        lambda resolve, manager, name: events.append(("open",)) or {"opened": True})
    args = SimpleNamespace(scratch_command="open", apply=True,
                           name="Ren Scratch - cli-queue")
    assert resolve_axi.cmd_project_scratch(args) == 0
    assert events == [("lease", True), ("connect",), ("open",)]


def test_the_cli_waits_and_retries_until_the_holder_releases(
        monkeypatch, capsys):
    """Catches: one refusal ending a queue that had not finished."""
    attempts = []

    def refuse_then_open(resolve, manager, name):
        attempts.append(name)
        if len(attempts) == 1:
            raise scratch.ScratchProjectError(
                "the scratch origin is held by 'lane-a' (pid 1); wait for it "
                "to be released before opening another scratch project")
        return {"opened": True}

    waits = []
    monkeypatch.setattr(scratch, "wait_for_origin_slot",
                        lambda timeout=None, poll=1.0: waits.append(timeout)
                        or {"released": True})
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: nullcontext())
    monkeypatch.setattr(resolve_axi, "_connect", lambda: captain())
    monkeypatch.setattr(scratch, "open_project", refuse_then_open)
    monkeypatch.setattr(scratch, "slot_timeout", lambda: 30.0)
    args = SimpleNamespace(scratch_command="open", apply=True,
                           name="Ren Scratch - cli-retry")
    assert resolve_axi.cmd_project_scratch(args) == 0
    assert attempts == ["Ren Scratch - cli-retry"] * 2
    assert len(waits) == 1


def test_the_cli_refuses_when_the_holder_never_releases(monkeypatch, capsys):
    """Catches: a queue with no end."""
    monkeypatch.setattr(scratch, "wait_for_origin_slot",
                        lambda timeout=None, poll=1.0: {"released": False,
                                                         "timed_out": True})
    monkeypatch.setattr(resolve_axi, "_lease",
                        lambda exclusive: nullcontext())
    monkeypatch.setattr(resolve_axi, "_connect", lambda: captain())
    monkeypatch.setattr(
        scratch, "open_project",
        lambda resolve, manager, name: (_ for _ in ()).throw(
            scratch.ScratchProjectError(
                "the scratch origin is held by 'lane-a' (pid 1); wait for it "
                "to be released before opening another scratch project")))
    monkeypatch.setattr(scratch, "slot_timeout", lambda: 0.0)
    args = SimpleNamespace(scratch_command="open", apply=True,
                           name="Ren Scratch - cli-refuse")
    assert resolve_axi.cmd_project_scratch(args) == 1
    assert "held by" in capsys.readouterr().out


def test_a_verb_that_moves_the_machine_refuses_while_another_writer_holds(
        monkeypatch, tmp_path):
    """Catches: create yanking the machine out of a live holder's session.

    The holder's next command runs in whatever project the machine is
    in, so a verb that moves the machine waits for the slot instead of
    pulling it out from under that writer.
    """
    resolve, manager = prepare(monkeypatch, captain())
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    holder = {"name": "lane-a"}
    monkeypatch.setattr(scratch, "_owner_name", lambda: holder["name"])
    scratch_project = Project("Ren Scratch - lane-a", [Timeline("T", "t1")])
    manager.projects[scratch_project.name] = scratch_project
    scratch.open_project(resolve, manager, scratch_project.name)

    holder["name"] = "lane-b"
    with pytest.raises(scratch.ScratchProjectError, match="held by"):
        scratch.create(resolve, manager, "Ren Scratch - lane-b-project")
    assert manager.GetCurrentProject().GetName() == scratch_project.name
