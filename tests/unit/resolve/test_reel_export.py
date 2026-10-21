"""The guarded reel-level export: one named timeline out of ANY project.

Every test here names the defect it catches. The two the capability
turns on are the SOURCE PROJECT UNCHANGED and the ORIGIN RESTORED: an
export that saves or edits the captain's project, or that leaves the
machine on the source instead of where it started, is worse than no
export at all.

`library/tools/reel_export.py`.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from library.tools import reel_export
from library.tools import resolve_scratch_projects as scratch

# ── the fakes ────────────────────────────────────────────────────────

class FakeTimeline:
    """A timeline that records the exports made of it."""

    def __init__(self, name, uid, project=None):
        self.name = name
        self.uid = uid
        self.project = project
        self.exports: list[tuple[str, object]] = []

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def Export(self, path, kind):
        self.exports.append((str(path), kind))
        Path(path).write_bytes(b"DRT:" + self.name.encode("utf-8"))
        return True


class FakePool:
    def __init__(self, project):
        self.project = project

    def CreateEmptyTimeline(self, name):
        timeline = FakeTimeline(name, f"uid-{len(self.project.timelines)}",
                                self.project)
        self.project.timelines.append(timeline)
        self.project.current = timeline
        return timeline

    def ImportTimelineFromFile(self, path, options=None):
        name = (options or {}).get("timelineName", Path(path).stem)
        timeline = FakeTimeline(name, f"uid-{len(self.project.timelines)}",
                                self.project)
        self.project.timelines.append(timeline)
        self.project.current = timeline
        return timeline


class FakeProject:
    """A project that records every write asked of it."""

    def __init__(self, name, timelines=(), settings=None, dirty=False):
        self.name = name
        self.timelines = list(timelines)
        self.current = self.timelines[0] if self.timelines else None
        self.settings = dict(settings or {})
        self.dirty = dirty
        self.writes: list[str] = []
        self.uid = f"project-{name}"

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetIsProjectDirty(self):
        return self.dirty

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.writes.append(f"SetCurrentTimeline:{timeline.GetName()}")
        self.current = timeline
        return True

    def GetTimelineCount(self):
        return len(self.timelines)

    def GetTimelineByIndex(self, index):
        return self.timelines[index - 1]

    def GetSetting(self, key):
        return self.settings.get(key, "")

    def SetSetting(self, key, value):
        self.writes.append(f"SetSetting:{key}")
        self.settings[key] = str(value)
        return True

    def SetSettings(self, values):
        for key, value in values.items():
            self.writes.append(f"SetSetting:{key}")
            self.settings[key] = str(value)
        return True

    def GetMediaPool(self):
        return FakePool(self)


class FakeManager:
    """A Project Manager that records every save it is asked for."""

    def __init__(self, *projects):
        self.projects = {project.name: project for project in projects}
        self.current = projects[0]
        self.saved: list[str] = []
        self.save_ok = True
        self.folder = ""

    def GetCurrentProject(self):
        return self.current

    def GetProjectListInCurrentFolder(self):
        return list(self.projects)

    def LoadProject(self, name):
        project = self.projects[name]
        self.current = project
        # A load brings the project back as it was; it does not move the
        # project's own current timeline.
        return project

    def SaveProject(self):
        self.saved.append(self.current.GetName() if self.current else None)
        return self.save_ok

    def CreateProject(self, name):
        project = FakeProject(name)
        self.projects[name] = project
        self.current = project
        return project

    # Project Manager navigation
    def GotoRootFolder(self):
        self.folder = ""
        return True

    def GetCurrentFolder(self):
        return self.folder

    def GotoParentFolder(self):
        if not self.folder:
            return False
        self.folder = ""
        return True

    def GetFoldersInCurrentFolder(self):
        return []

    def OpenFolder(self, name):
        self.folder = name
        return True


class FakeResolve:
    EXPORT_DRT = 18

    def __init__(self, manager):
        self.manager = manager
        self.page = "edit"

    def GetProjectManager(self):
        return self.manager

    def GetCurrentPage(self):
        return self.page

    def OpenPage(self, page):
        self.page = page
        return True


# ── the fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def guarded(monkeypatch, tmp_path):
    """The lease, the switch discipline and the lifecycle log, offline."""
    monkeypatch.setattr(scratch, "_lifecycle_log_path",
                        lambda: tmp_path / "event_log.jsonl")
    monkeypatch.setattr(scratch, "switch_project",
                        lambda manager, name: manager.LoadProject(name))
    monkeypatch.setattr(scratch, "assert_current_timeline",
                        lambda project, timeline: project.SetCurrentTimeline(timeline))
    monkeypatch.setattr(scratch.resolve_switch_guard, "note_project_switch",
                        lambda: None)
    monkeypatch.setattr(scratch.resolve_switch_guard, "wait_for_settle",
                        lambda: None)
    from library.tools import qualification_project

    monkeypatch.setattr(qualification_project, "switch_project",
                        lambda manager, name: manager.LoadProject(name))
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held",
                        lambda: True)
    monkeypatch.setattr("library.tools.shared_environment.vep_home",
                        lambda: tmp_path)
    return tmp_path


def podcast(dirty=False):
    """The captain's project: thirty reels, one of them the current one."""
    timelines = [FakeTimeline(f"Reel {index:02d}", f"reel-{index:02d}")
                 for index in range(1, 31)]
    project = FakeProject("Podcast (field test)", timelines,
                          settings={"timelineFrameRate": "23.976",
                                    "timelineResolutionWidth": "1080",
                                    "timelineResolutionHeight": "1920"},
                          dirty=dirty)
    project.current = timelines[12]
    return project


def scratch_project(name="Ren Scratch - bench"):
    project = FakeProject(name, [FakeTimeline(f"{name} - Empty", "scratch-1")],
                          settings={"timelineFrameRate": "23.976",
                                    "timelineResolutionWidth": "1080",
                                    "timelineResolutionHeight": "1920"})
    return project


def machine(*projects):
    manager = FakeManager(*projects)
    return FakeResolve(manager), manager


# ── the source project is not written ────────────────────────────────

def test_the_source_project_is_never_saved(tmp_path):
    """Defect: the export saved the project it was only reading.

    The captain's project is read to copy one reel out of it. A save is
    a write, and a write nobody asked for is the one thing this
    capability must never do.
    """
    resolve, manager = machine(scratch_project(), podcast())
    result = reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        destination=tmp_path / "reel.drt")
    assert "Podcast (field test)" not in manager.saved
    assert result["source"]["saved"] is False
    assert result["drt"]["path"].endswith("reel.drt")


def test_the_source_project_is_never_saved_even_when_it_is_the_open_one(
        tmp_path):
    """Defect: opening the source was enough to save it.

    When the captain's project is ALREADY open, the export reads it
    where it is. That is the case the brief cares about most, and it is
    the case where a borrow-shaped save would be least visible.
    """
    resolve, manager = machine(podcast())
    reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        destination=tmp_path / "reel.drt")
    assert manager.saved == []


def test_the_source_project_is_never_written_to(tmp_path):
    """Defect: the export changed something in the source project.

    No setting, no rename, no cursor move inside the source: the source
    is read, and what it held when the export started is what it holds
    when the export ends.
    """
    resolve, manager = machine(scratch_project(), podcast())
    source = manager.projects["Podcast (field test)"]
    reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        destination=tmp_path / "reel.drt")
    assert source.writes == []
    assert source.GetTimelineCount() == 30
    assert source.GetCurrentTimeline().GetName() == "Reel 13"


def test_the_export_does_not_move_the_source_current_timeline(tmp_path):
    """Defect: exporting a reel moved the source's current timeline.

    The export takes a handle to the named timeline and exports it. The
    timeline the captain was looking at is still the one they are
    looking at.
    """
    resolve, manager = machine(scratch_project(), podcast())
    result = reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 07",
        destination=tmp_path / "reel.drt")
    source = manager.projects["Podcast (field test)"]
    assert source.GetCurrentTimeline().GetName() == "Reel 13"
    assert result["source"]["current_timeline"] == "Reel 13"
    assert result["source"]["cursor_stayed_put"] is True


# ── the origin comes back ────────────────────────────────────────────

def test_the_origin_project_and_its_timeline_come_back(tmp_path):
    """Defect: the export left the machine on the source project.

    The project, the timeline inside it (by unique id) and the Resolve
    page are restored, and the restore READS BACK.
    """
    resolve, manager = machine(scratch_project(), podcast())
    result = reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        destination=tmp_path / "reel.drt")
    assert manager.GetCurrentProject().GetName() == "Ren Scratch - bench"
    assert result["origin"]["restored"]["verified"] is True
    assert result["origin"]["timeline_id"] == "scratch-1"
    assert resolve.GetCurrentPage() == "edit"


def test_a_refused_export_still_puts_the_origin_back(tmp_path):
    """Defect: an export that failed stranded the machine on the source.

    The refusal path restores the origin exactly like the success path.
    """
    resolve, manager = machine(scratch_project(), podcast())
    with pytest.raises(reel_export.ReelExportError):
        reel_export.export_timeline(
            resolve, manager, source_project="Podcast (field test)",
            timeline_name="Reel 99",
            destination=tmp_path / "reel.drt")
    assert manager.GetCurrentProject().GetName() == "Ren Scratch - bench"


def test_a_timeline_that_is_not_in_the_source_is_refused_by_name(tmp_path):
    """Defect: a near match on the timeline name was accepted.

    The captain's project holds thirty reels; a reel that is not there
    is named, with the ones that are.
    """
    resolve, manager = machine(scratch_project(), podcast())
    with pytest.raises(reel_export.ReelExportError) as refused:
        reel_export.export_timeline(
            resolve, manager, source_project="Podcast (field test)",
            timeline_name="Reel 13 (cut)",
            destination=tmp_path / "reel.drt")
    assert "Reel 13 (cut)" in str(refused.value)


def test_a_captain_origin_with_unsaved_changes_is_refused(tmp_path):
    """Defect: the export discarded the captain's unsaved work.

    The origin is the project the machine comes back to, so it is the
    one whose unsaved work this operation protects. A project that says
    it holds unsaved changes is refused BEFORE anything is written.
    """
    resolve, manager = machine(podcast(dirty=True), scratch_project())
    with pytest.raises(reel_export.ReelExportError) as refused:
        reel_export.export_timeline(
            resolve, manager, source_project="Ren Scratch - bench",
            timeline_name="Ren Scratch - bench - Empty",
            destination=tmp_path / "reel.drt")
    assert "unsaved changes" in str(refused.value)
    assert not (tmp_path / "reel.drt").exists()
    assert manager.saved == []


def test_a_project_that_is_not_ours_is_never_saved(tmp_path):
    """Defect: leaving the origin saved a project that is not Ren's.

    Ren saves the projects it owns, because leaving them costs nothing
    that way. A captain's project is not saved: it is read for its
    state, and the receipt carries that state.
    """
    resolve, manager = machine(podcast(), scratch_project())
    result = reel_export.export_timeline(
        resolve, manager, source_project="Ren Scratch - bench",
        timeline_name="Ren Scratch - bench - Empty",
        destination=tmp_path / "reel.drt")
    assert result["origin"]["saved"] is False
    assert result["origin"]["dirty_state"] is False
    assert "Podcast (field test)" not in manager.saved


def test_a_ren_owned_origin_is_saved_before_it_is_left(tmp_path):
    """Defect: leaving a Ren project without saving it loses work.

    The source is never saved; the ORIGIN is Ren's own, and saving it
    is how the machine leaves it without losing anything.
    """
    resolve, manager = machine(scratch_project(), podcast())
    result = reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        destination=tmp_path / "reel.drt")
    assert result["origin"]["saved"] is True
    assert "Ren Scratch - bench" in manager.saved


# ── the copy lands where it should ───────────────────────────────────

def test_the_copy_lands_in_a_ren_owned_scratch_project(tmp_path):
    """Defect: the copy landed in a project that is not Ren's.

    The copy is a `.drt` and a scratch project named `Ren Scratch - `,
    and the captain's project is still exactly as it was.
    """
    resolve, manager = machine(scratch_project(), podcast())
    result = reel_export.export_into_scratch(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        scratch_project="Ren Scratch - bench no-extra-footage",
        archive_dir=tmp_path)
    scratch_name = result["scratch"]["project"]
    assert scratch_name.startswith("Ren Scratch - ")
    assert scratch_name in manager.projects
    assert result["scratch"]["timeline"]
    assert "Podcast (field test)" not in manager.saved
    assert manager.GetCurrentProject().GetName() == "Ren Scratch - bench"


def test_the_copy_carries_the_source_shape(tmp_path):
    """Defect: the copy was re-framed on the way in.

    The source is vertical 1080x1920 at 23.976, and the copy is the
    same reel in the same shape, not Resolve's default.
    """
    resolve, manager = machine(scratch_project(), podcast())
    result = reel_export.export_into_scratch(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        scratch_project="Ren Scratch - bench shape",
        archive_dir=tmp_path)
    copy = manager.projects[result["scratch"]["project"]]
    assert copy.GetSetting("timelineFrameRate") == "23.976"
    assert copy.GetSetting("timelineResolutionWidth") == "1080"
    assert copy.GetSetting("timelineResolutionHeight") == "1920"


def test_the_drt_lands_in_the_machine_export_store(tmp_path):
    """Defect: the `.drt` was written into somebody's project folder.

    The archive belongs to the machine, not to a project: it outlives
    the run that made it.
    """
    from library.tools.shared_environment import vep_home

    destination = reel_export.export_destination(
        "Podcast (field test)", "Reel 13")
    assert destination.parent == vep_home() / reel_export.EXPORT_DIRNAME
    assert destination.suffix == ".drt"


def test_the_source_state_is_reported_as_it_was_found(tmp_path):
    """Defect: the receipt hid that the source held unsaved work.

    The source is read and never saved, so the state it was found in is
    the operator's only warning about work the machine is about to leave
    behind. It is read BEFORE the export, not after.
    """
    resolve, manager = machine(scratch_project(), podcast(dirty=True))
    result = reel_export.export_timeline(
        resolve, manager, source_project="Podcast (field test)",
        timeline_name="Reel 13",
        destination=tmp_path / "reel.drt")
    assert result["source"]["dirty_state"] is True
    assert "Podcast (field test)" not in manager.saved


def test_the_export_refuses_without_the_exclusive_lease(monkeypatch,
                                                        tmp_path):
    """Defect: the export moved the machine between projects while
    another writer held the instance.

    The export switches projects twice, so it is a write to the session
    and it takes the exclusive lease like every other writer.
    """
    resolve, manager = machine(scratch_project(), podcast())
    monkeypatch.setattr("library.tools.resolve_lock.exclusive_held",
                        lambda: False)
    with pytest.raises(reel_export.ReelExportError) as refused:
        reel_export.export_timeline(
            resolve, manager, source_project="Podcast (field test)",
            timeline_name="Reel 13",
            destination=tmp_path / "reel.drt")
    assert "EXCLUSIVE" in str(refused.value)
    assert not (tmp_path / "reel.drt").exists()


def test_a_source_that_is_not_there_is_refused_by_name(tmp_path):
    """Defect: a project name that is not there was accepted.

    The captain's project and the untouchable original sit side by side
    with names that resemble each other, so a name that is not there is
    refused rather than resolved to something close.
    """
    resolve, manager = machine(scratch_project(), podcast())
    with pytest.raises(reel_export.ReelExportError) as refused:
        reel_export.export_timeline(
            resolve, manager, source_project="Podcast",
            timeline_name="Reel 13",
            destination=tmp_path / "reel.drt")
    assert "Podcast" in str(refused.value)
    assert manager.GetCurrentProject().GetName() == "Ren Scratch - bench"


def test_a_scratch_name_that_is_not_ours_is_refused(tmp_path):
    """Defect: the copy would have landed in the captain's project.

    The scratch project is named `Ren Scratch - `, and a name that is
    not ours is refused before anything is created.
    """
    resolve, manager = machine(scratch_project(), podcast())
    before = set(manager.projects)
    with pytest.raises(scratch.ScratchProjectError):
        reel_export.export_into_scratch(
            resolve, manager, source_project="Podcast (field test)",
            timeline_name="Reel 13",
            scratch_project="Podcast (field test)",
            archive_dir=tmp_path)
    assert set(manager.projects) == before


# ── the command the operator runs ─────────────────────────────────────

def test_the_command_refuses_without_apply(capsys):
    """Defect: the command copied a reel without being asked to.

    The export reads a project and creates a project, so it is behind
    `--apply` like every other write in AXI.
    """
    from library.tools import resolve_axi

    args = resolve_axi.build_parser().parse_args([
        "project", "export-timeline",
        "--source-project", "Podcast (field test)",
        "--timeline", "Reel 13",
        "--scratch", "Ren Scratch - bench cli"])
    assert args.func.__name__ == "cmd_project_export_timeline"
    assert resolve_axi.cmd_project_export_timeline(args) == 1
    assert "--apply" in capsys.readouterr().out


def test_the_command_copies_the_reel_out_and_reports_it(monkeypatch,
                                                          capsys, tmp_path):
    """Defect: the command did not reach the export, or reported nothing.

    The whole path, offline: the command, the lease it takes, the
    export, and the receipt that says where the copy landed.
    """
    from library.tools import resolve_axi
    from library.tools import resolve_axi_core_commands as commands

    resolve, manager = machine(scratch_project(), podcast())
    monkeypatch.setattr(commands, "_connect", lambda: resolve)
    monkeypatch.setattr(commands, "_lease",
                        lambda exclusive=True: nullcontext())
    args = resolve_axi.build_parser().parse_args([
        "project", "export-timeline",
        "--source-project", "Podcast (field test)",
        "--timeline", "Reel 13",
        "--scratch", "Ren Scratch - bench cli",
        "--archive", str(tmp_path)])
    args.apply = True
    assert commands.cmd_project_export_timeline(args) == 0
    out = capsys.readouterr().out
    assert "Ren Scratch - bench cli" in out
    assert "Podcast (field test)" not in manager.saved
    assert manager.GetCurrentProject().GetName() == "Ren Scratch - bench"
