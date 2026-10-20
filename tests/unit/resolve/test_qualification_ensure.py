"""`ren qualification ensure`: create ONLY what is missing, destroy nothing.

The qualification reset deletes the whole `Ren Qualification` project
and rebuilds it. That is right for a qualification run and fatal for a
project holding the captain's proof timelines: after the reset the
setup-created fixtures (the media in the pool, the `Q Spine` source and
the empty scratch timeline the live lane seeds) were gone, and the
protected fixtures had to be restored from `.drt` backups by hand.

`ensure` is the non-destructive half. Every test below names a defect
it would otherwise let through: a replaced pool item, an emptied
scratch timeline, a deleted proof timeline, a captain left on the
qualification project, or a qualification project invented where none
was.

The Resolve here is a fake; the media files are never rendered.
"""

import json
import re
import sys
from pathlib import Path

import pytest

from library.tools import qualification_project as qp
from library.tools import resolve_lock

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


# ── the fake Resolve ──────────────────────────────────────────────────

class _Item:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name


class _Folder:
    def __init__(self, items=(), subfolders=()):
        self.items = list(items)
        self.subfolders = list(subfolders)

    def GetClipList(self):
        return self.items

    def GetSubFolderList(self):
        return self.subfolders


class _Timeline:
    def __init__(self, name, uid, clips=()):
        self.name = name
        self.uid = uid
        self.video = list(clips)
        self.audio = []
        self.markers = {}

    def GetName(self):
        return self.name

    def GetUniqueId(self):
        return self.uid

    def GetTrackCount(self, kind):
        return 1 if getattr(self, kind) is not None else 0

    def GetItemListInTrack(self, kind, index):
        return getattr(self, kind, []) or []

    def AddMarker(self, frame, color, name, note, duration, custom):
        self.markers[int(frame)] = {"name": name, "color": color}
        return True

    def GetMarkers(self):
        return self.markers

    def GetStartFrame(self):
        return 0

    def GetEndFrame(self):
        return len(self.video) * 24


class _Pool:
    def __init__(self, project):
        self.project = project
        self.root = _Folder()
        self.imported = []
        self.created = []

    def GetRootFolder(self):
        return self.root

    def GetCurrentFolder(self):
        return self.root

    def ImportMedia(self, paths):
        items = [_Item(Path(str(path)).name) for path in paths]
        self.root.items.extend(items)
        self.imported.extend(str(path) for path in paths)
        return items

    def CreateEmptyTimeline(self, name):
        timeline = _Timeline(name, f"uid-{name}")
        self.created.append(name)
        self.project.timelines.append(timeline)
        self.project.current = timeline
        return timeline

    def AppendToTimeline(self, items):
        timeline = self.project.current
        if timeline is None:
            return False
        timeline.video.extend(items)
        return True


class _Project:
    def __init__(self, name, timelines=()):
        self.name = name
        self.timelines = list(timelines)
        self.current = self.timelines[0] if self.timelines else None
        self.pool = _Pool(self)
        self.settings = {}

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

    def GetMediaPool(self):
        return self.pool

    def SetSetting(self, key, value):
        self.settings[key] = value
        return True

    def GetSetting(self, key):
        return self.settings.get(key)


class _Manager:
    """The Project Manager: projects live in folders, the project list
    is scoped to the current folder, and a project NAME resolves inside
    the CURRENT folder - so two folders may hold two DIFFERENT projects
    that answer to one name, exactly as Resolve behaves."""

    def __init__(self, *projects):
        #: every project filed in the library: (folder path, name, project)
        self.entries = []
        for project in projects:
            self.file(project, ())
        self.open = projects[0] if projects else None
        self.folder_tree = {(): ()}
        self.folder_path = ()
        self.saved = []
        self.deleted = []
        self.opened_pages = ["edit"]

    def file(self, project, path):
        """File `project` under the folder `path`."""
        self.entries.append((tuple(path), project.name, project))
        return project

    def _in_current_folder(self):
        return [entry for entry in self.entries
                if entry[0] == self.folder_path]

    def GetCurrentProject(self):
        return self.open

    def GetCurrentPage(self):
        return self.opened_pages[-1]

    def OpenPage(self, page):
        self.opened_pages.append(page)
        return True

    def SaveProject(self):
        self.saved.append(self.open.name if self.open else None)
        return True

    def LoadProject(self, name):
        """Load the project of that name IN THE CURRENT FOLDER."""
        for path, project_name, project in self._in_current_folder():
            if project_name != name:
                continue
            self.open = project
            self.folder_path = path
            if project.timelines:
                project.current = project.timelines[0]
            return project
        return None

    def GetProjectListInCurrentFolder(self):
        return [name for _path, name, _project in self._in_current_folder()]

    def DeleteProject(self, name):
        self.deleted.append(name)
        if name not in self.GetProjectListInCurrentFolder():
            return False
        self.entries = [entry for entry in self.entries
                        if not (entry[0] == self.folder_path
                                and entry[1] == name)]
        return True

    def CreateProject(self, name):
        project = _Project(name)
        self.file(project, self.folder_path)
        self.open = project
        return project

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
        return {index + 1: name for index, name in enumerate(
            self.folder_tree.get(self.folder_path, ()))}

    def OpenFolder(self, name):
        if name not in self.GetFoldersInCurrentFolder().values():
            return False
        self.folder_path = (*self.folder_path, name)
        return True


class _Resolve:
    def __init__(self, manager):
        self.manager = manager

    def GetProjectManager(self):
        return self.manager

    def GetCurrentPage(self):
        return self.manager.GetCurrentPage()


def _media():
    return {name: f"/tmp/qual-media/{name}.mp4" for name, *_ in qp.MEDIA}


def _qualification(timelines=()):
    return _Project(qp.QUALIFICATION_PROJECT, list(timelines))


def _captain_and_qualification(qualification=None):
    captain = _Project("Podcast (field test)", [_Timeline("Reel 07", "uid-7")])
    qualification = qualification or _qualification()
    manager = _Manager(captain, qualification)
    return manager, captain, qualification


@pytest.fixture
def live(monkeypatch):
    """The qualification command with the lease, the session marker and
    the switch settle taken out of the way."""
    monkeypatch.setattr(resolve_lock, "held", lambda: True)
    monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: True)
    monkeypatch.setattr(qp, "begin_qualification_session", lambda name: None)
    monkeypatch.setattr(qp, "end_qualification_session", lambda: None)
    from library.tools import resolve_switch_guard
    monkeypatch.setattr(resolve_switch_guard, "SETTLE_SECONDS", 0)
    return monkeypatch


def _run_ensure(monkeypatch, resolve):
    monkeypatch.setattr(qp, "ensure_media", lambda: _media())
    from library.tools import marker_feedback
    monkeypatch.setattr(marker_feedback, "connect_resolve",
                        lambda: resolve)
    return qp.ensure()


def _two_qualification_projects(captain_folder=("Podcast",)):
    """Two DIFFERENT projects that answer to the name `Ren Qualification`.

    One is filed in the `Podcast` folder - the one the live lane opens
    by name, because Resolve resolves a project name inside the CURRENT
    Project Manager folder. The other is a stray copy filed at the
    root, holding fixtures the lane cannot see.
    """
    captain = _Project("Podcast (field test)", [_Timeline("Reel 07", "uid-7")])
    stray = _qualification()
    filed = _qualification()
    manager = _Manager()
    manager.folder_tree = {(): ("Podcast", "Elsewhere"),
                           ("Podcast",): (), ("Elsewhere",): ()}
    manager.file(captain, captain_folder)
    manager.file(stray, ())
    manager.file(filed, ("Podcast",))
    manager.folder_path = captain_folder
    manager.open = captain
    return manager, captain, stray, filed


# ── what it creates ───────────────────────────────────────────────────

def test_ensure_creates_the_media_the_pool_is_missing():
    """Defect: the live lane's setup failed on a missing `qual_a.mp4`
    pool item, so every request row failed at setup."""
    project = _qualification()
    report = qp.ensure_fixtures(project, _media())
    assert report["media"]["imported"] == ["qual_a", "qual_b", "qual_c"]
    names = sorted(item.GetName() for item in project.pool.root.items)
    assert names == ["qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]


def test_ensure_restores_the_q_spine_source_onto_an_empty_timeline():
    """Defect: the Q Spine source was absent live, so the timeline the
    qualification scenarios read held nothing."""
    spine = _Timeline("Q Spine", "uid-q")
    project = _qualification([spine])
    report = qp.ensure_fixtures(project, _media())
    row = next(r for r in report["timelines"] if r["name"] == "Q Spine")
    assert row["status"] == "source_restored"
    assert [item.GetName() for item in spine.video] == [
        "qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]
    assert spine.GetUniqueId() == "uid-q"


def test_ensure_creates_the_scratch_timeline_empty():
    """Defect: the recorded empty scratch timeline was absent from the
    live qualification project, and the lane seeds it expecting empty."""
    project = _qualification()
    report = qp.ensure_fixtures(project, _media())
    row = next(r for r in report["timelines"]
               if r["name"] == qp.SCRATCH_TIMELINE)
    assert row["status"] == "created"
    assert row["clips"] == []
    scratch = next(t for t in project.timelines
                   if t.GetName() == qp.SCRATCH_TIMELINE)
    assert scratch.video == []


# ── WHICH qualification project, and WHOSE pool ───────────────────────

def test_ensure_works_in_the_qualification_project_the_lane_opens(live):
    """Defect: two projects answered to `Ren Qualification`, and the
    ensure read the pool of the one the live lane never opens. It
    reported `qual_a`/`qual_b`/`qual_c` PRESENT while the lane's own
    project held only `qual_b.mp4`, so the lane's setup kept failing
    on the missing `qual_a.mp4` and nothing was ever imported."""
    manager, _captain, stray, filed = _two_qualification_projects()
    report = _run_ensure(live, _Resolve(manager))
    assert report["project_folder"] == ["Podcast"]
    # the fixtures landed in the project the lane opens ...
    assert sorted(item.GetName() for item in filed.pool.root.items) == [
        "qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]
    assert report["media"]["imported"] == ["qual_a", "qual_b", "qual_c"]
    # ... and the other project of the same name was NOT touched.
    assert stray.pool.imported == []
    assert stray.pool.root.items == []


def test_ensure_refuses_when_the_qualification_project_is_ambiguous(live):
    """Defect: picking one of two same-named projects by guess is the
    defect itself - the ensure would work on a project the lane never
    reads, so the ambiguous name is refused and both folders named."""
    manager, _captain, stray, filed = _two_qualification_projects(
        captain_folder=("Elsewhere",))
    with pytest.raises(qp.QualificationError, match="more than one"):
        _run_ensure(live, _Resolve(manager))
    assert stray.pool.imported == []
    assert filed.pool.imported == []


def test_ensure_imports_a_clip_that_is_on_disk_but_not_in_the_pool(tmp_path):
    """Defect: a rendered clip that the pool does not hold is missing
    from the lane's point of view. A file on disk is not presence, and
    an ensure that decided presence from the disk would report the
    fixture present and import nothing."""
    media = {}
    for name, *_ in qp.MEDIA:
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(b"not really a movie")
        media[name] = path
    project = _qualification()
    report = qp.ensure_fixtures(project, media)
    assert report["media"]["present"] == []
    assert report["media"]["imported"] == ["qual_a", "qual_b", "qual_c"]
    assert sorted(item.GetName() for item in project.pool.root.items) == [
        "qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]
    assert report["media"]["readback"] == ["qual_a", "qual_b", "qual_c"]


def test_ensure_refuses_an_import_that_did_not_land():
    """Defect: an import that Resolve did not really make leaves the
    pool without the clip. Reporting it present is a report of an
    intention, so the ensure reads the pool back and refuses."""
    class _LyingPool(_Pool):
        def ImportMedia(self, paths):
            return [_Item(Path(str(path)).name) for path in paths]

    project = _qualification()
    project.pool = _LyingPool(project)
    with pytest.raises(qp.QualificationError, match="still absent"):
        qp.ensure_fixtures(project, _media())


# ── what it must NOT touch ────────────────────────────────────────────

def test_ensure_never_replaces_a_pool_item_that_already_exists():
    """Defect: an import that replaces an existing qualification clip
    would swap the captain's own file for a synthetic one."""
    project = _qualification()
    kept = _Item("qual_a.mp4")
    project.pool.root.items.append(kept)
    report = qp.ensure_fixtures(project, _media())
    assert report["media"]["imported"] == ["qual_b", "qual_c"]
    assert report["media"]["present"] == ["qual_a"]
    assert kept in project.pool.root.items
    assert sum(1 for item in project.pool.root.items
               if item.GetName() == "qual_a.mp4") == 1


def test_ensure_never_empties_a_scratch_timeline_that_holds_clips():
    """Defect: clearing the scratch timeline would delete the lane's
    own fixture work."""
    scratch = _Timeline(qp.SCRATCH_TIMELINE, "uid-s", clips=[_Item("a.mp4")])
    project = _qualification([scratch])
    report = qp.ensure_fixtures(project, _media())
    row = next(r for r in report["timelines"]
               if r["name"] == qp.SCRATCH_TIMELINE)
    assert row["status"] == "present"
    assert [item.GetName() for item in scratch.video] == ["a.mp4"]


def test_ensure_leaves_a_populated_q_spine_exactly_as_it_is():
    """Defect: placing the declared clips over a populated timeline
    would replace what the timeline holds."""
    spine = _Timeline("Q Spine", "uid-q", clips=[_Item("other.mp4")])
    project = _qualification([spine])
    report = qp.ensure_fixtures(project, _media())
    row = next(r for r in report["timelines"] if r["name"] == "Q Spine")
    assert row["status"] == "present"
    assert [item.GetName() for item in spine.video] == ["other.mp4"]


# ── what it does to the captain ───────────────────────────────────────

def test_ensure_leaves_the_protected_proof_timeline_alone(live):
    """Defect: a reset deleted the protected proof fixtures, and they
    had to be restored from `.drt` backups by hand."""
    proof = _Timeline("Q Promotion Proof R5", "uid-proof",
                      clips=[_Item("proof.mp4")])
    manager, _captain, qualification = _captain_and_qualification(
        _qualification([proof]))
    report = _run_ensure(live, _Resolve(manager))
    assert "Q Promotion Proof R5" in [
        t.GetName() for t in qualification.timelines]
    assert proof.GetUniqueId() == "uid-proof"
    assert [item.GetName() for item in proof.video] == ["proof.mp4"]
    assert manager.deleted == []
    assert report["timelines"]


def test_ensure_gives_the_captain_his_project_and_timeline_back(live):
    manager, captain, _qualification_project = _captain_and_qualification()
    _run_ensure(live, _Resolve(manager))
    assert manager.GetCurrentProject() is captain
    assert captain.GetCurrentTimeline().GetUniqueId() == "uid-7"
    assert manager.saved


def test_ensure_leaves_the_cursor_on_the_timeline_it_found(live):
    manager, _captain, qualification = _captain_and_qualification()
    found = _Timeline("Q Markers", "uid-m")
    qualification.timelines.append(found)
    qualification.current = found
    _run_ensure(live, _Resolve(manager))
    assert qualification.GetCurrentTimeline().GetUniqueId() == "uid-m"


def test_ensure_refuses_when_the_qualification_project_is_absent(live):
    """Defect: inventing a qualification project would put a second one
    in the Project Manager beside the captain's."""
    captain = _Project("Podcast (field test)", [_Timeline("Reel 07", "uid-7")])
    manager = _Manager(captain)
    with pytest.raises(qp.QualificationError, match="not in the Project Manager"):
        _run_ensure(live, _Resolve(manager))
    assert manager.deleted == []


def test_ensure_saves_the_qualification_project_it_wrote_to(live):
    """Defect: a fixture created but never saved is a fixture the next
    reader cannot find."""
    manager, _captain, _qualification_project = _captain_and_qualification()
    _run_ensure(live, _Resolve(manager))
    assert qp.QUALIFICATION_PROJECT in manager.saved


def test_ensure_reads_the_fixtures_back(live):
    """Defect: a report that names what it did without reading it back
    is a report of an intention."""
    manager, _captain, _qualification_project = _captain_and_qualification()
    report = _run_ensure(live, _Resolve(manager))
    readback = {row["name"]: row["clips"] for row in report["readback"]}
    assert readback["Q Spine"] == ["qual_a.mp4", "qual_b.mp4", "qual_c.mp4"]
    assert readback[qp.SCRATCH_TIMELINE] == []


def test_ensure_runs_while_resolve_is_already_on_the_qualification_project(
        live):
    """Defect: the lane leaves Resolve on Ren Qualification, and an
    ensure that refuses there cannot run at all."""
    captain = _Project("Podcast (field test)", [_Timeline("Reel 07", "uid-7")])
    qualification = _qualification()
    manager = _Manager(captain, qualification)
    manager.open = qualification
    report = _run_ensure(live, _Resolve(manager))
    assert report["project"] == qp.QUALIFICATION_PROJECT
    assert manager.GetCurrentProject() is qualification


# ── the front doors ───────────────────────────────────────────────────

def test_both_front_doors_advertise_the_ensure_verb():
    """Defect: a verb the Python registry knows but the native list does
    not is unreachable from the native front door."""
    from ren.commands import VERBS
    summary = next(verb.summary for verb in VERBS
                   if verb.name == "qualification")
    assert "ensure" in summary
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert re.search(r'name: "qualification".*ensure', native)


def test_the_ensure_verb_is_a_choice_of_the_qualification_command(monkeypatch,
                                                                  capsys):
    """Defect: a verb the parser does not accept is a refusal at the
    door, whatever the registry says."""
    called = []
    monkeypatch.setattr(qp, "ensure",
                        lambda: called.append("ensure")
                        or {"project": qp.QUALIFICATION_PROJECT})
    assert qp.main(["ensure"]) == 0
    assert called == ["ensure"]
    assert json.loads(capsys.readouterr().out)["project"] == (
        qp.QUALIFICATION_PROJECT)
