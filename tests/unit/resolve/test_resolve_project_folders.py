"""The folder a Resolve project is filed in, and the route that reaches it.

The defects this file names, both measured live on 2026-10-21/22 on the
machine's own broker:

- a Ren-owned scratch project filed in a Project Manager SUB-FOLDER was
  refused as "not in the current folder" - the Project Manager's
  listing is folder-scoped, so a project filed in another folder reads
  as absent, and the refusal named no folder at all;
- a machine whose Project Manager cannot move refused the same way, so
  a reader could not tell "the project is not there" from "the project
  is filed in 'Lucie Content/Social Media/Podcast' and this machine
  cannot reach that folder".

So the route this file pins is: the folder path is RECORDED or
DISCOVERED (the Resolve database on disk mirrors the Project Manager
tree), the Project Manager is navigated to it, the project is loaded by
its exact name, and the open project is read back by that exact name.
A machine that cannot get there refuses with the folder NAMED.

Driven against doubles; no Resolve and no project.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import resolve_project_folders as folders
from library.tools import resolve_session
from library.tools.ren_refusal import RenRefusal


class Project:
    def __init__(self, name):
        self.name = name

    def GetName(self):
        return self.name

    def GetCurrentTimeline(self):
        return None


class FolderManager:
    """A Project Manager that files its projects in folders.

    `moves` is the switch this file turns: on a Resolve whose Project
    Manager moves, `OpenFolder` moves the cursor; on one that does not,
    it answers and leaves the cursor where it was - which is what was
    measured live on 2026-10-22.
    """

    def __init__(self, *, folders=None, projects=None, database_root="",
                 moves=True, current=None, db_type="Disk"):
        self.folders = {tuple(path): list(children)
                        for path, children in (folders or {}).items()}
        self.projects = {tuple(path): list(held)
                         for path, held in (projects or {}).items()}
        self.database_root = database_root
        self.db_type = db_type
        self.moves = moves
        self.current_path = ()
        self.current = current
        self.loaded = []
        self.opened = []

    # ── the Project Manager ──

    def GetCurrentFolder(self):
        return self.current_path[-1] if self.current_path else None

    def GotoRootFolder(self):
        self.current_path = ()
        return True

    def GotoParentFolder(self):
        if not self.current_path:
            return False
        self.current_path = self.current_path[:-1]
        return True

    def GetFoldersInCurrentFolder(self):
        return {index + 1: name for index, name in enumerate(
            self.folders.get(self.current_path, ()))}

    def OpenFolder(self, name):
        self.opened.append((self.current_path, name))
        if self.moves:
            self.current_path = (*self.current_path, name)
        return True

    def GetProjectListInCurrentFolder(self):
        return [project.GetName()
                for project in self.projects.get(self.current_path, ())]

    def GetCurrentProject(self):
        return self.current

    def LoadProject(self, name):
        self.loaded.append((self.current_path, name))
        for project in self.projects.get(self.current_path, ()):
            if project.GetName() == name:
                self.current = project
                return project
        return None

    def SaveProject(self):
        return True

    def GetCurrentDatabase(self):
        return {"DbType": self.db_type, "DbName": "poetic",
                "DbPath": self.database_root}


def _database(tmp_path, projects):
    """A Resolve database on disk, with `projects` filed in folders."""
    base = tmp_path / "Resolve Projects" / "Users" / "guest" / "Projects"
    for path, name in projects.items():
        directory = base.joinpath(*path, name)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "Project.db").write_bytes(b"fixture")
    base.mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_the_folder_a_project_is_filed_in_is_read_from_the_database(tmp_path):
    """The database mirrors the Project Manager tree, so the folder is
    readable from it - the route that works where the Project Manager's
    own navigation does not."""
    root = _database(tmp_path, {
        ("Lucie Content", "Social Media", "Podcast"):
            "Ren Scratch - breaktest",
        (): "Ren Qualification",
    })
    manager = FolderManager(database_root=str(root))

    assert folders.database_folder_paths(
        manager, "Ren Scratch - breaktest") == [
            ("Lucie Content", "Social Media", "Podcast")]
    assert folders.database_folder_paths(manager, "Ren Qualification") == [()]
    assert folders.database_folder_paths(manager, "Ren Scratch - absent") == []


def test_a_name_two_folders_hold_is_refused_with_the_folders_named(tmp_path):
    """Two projects answer to one name, and Resolve resolves a name
    inside the CURRENT folder - so the folders are named, not guessed."""
    root = _database(tmp_path, {
        ("Lucie Content", "Social Media", "Podcast"): "Ren Qualification",
        (): "Ren Qualification",
    })
    manager = FolderManager(database_root=str(root))

    with pytest.raises(resolve_session.SessionProjectRefused) as refused:
        resolve_session._folder_path(manager, "Ren Qualification")

    message = str(refused.value)
    assert "Lucie Content/Social Media/Podcast" in message
    assert "more than one" in message


def test_a_project_manager_that_does_not_move_refuses_with_the_folder_named():
    """The refusal says WHICH folder the project is filed in - the one
    thing the caller cannot work out for itself."""
    manager = FolderManager(moves=False)

    with pytest.raises(folders.ProjectFolderRefused) as refused:
        folders.navigate_to_folder(
            manager, ("Lucie Content", "Social Media", "Podcast"))

    message = str(refused.value)
    assert "Lucie Content/Social Media/Podcast" in message
    assert isinstance(refused.value, RenRefusal)
    assert refused.value.code == "REN_RESOLVE_PROJECT_FOLDER"


def test_a_project_filed_in_another_folder_is_opened_by_its_folder_path():
    """The route: navigate to the folder, load the exact name, read the
    OPEN project back by that exact name."""
    target = Project("Ren Scratch - breaktest")
    manager = FolderManager(
        folders={("Lucie Content", "Social Media", "Podcast"): []},
        projects={("Lucie Content", "Social Media", "Podcast"): [target]},
        moves=True)

    opened = folders.open_project_by_folder(
        manager, "Ren Scratch - breaktest",
        ("Lucie Content", "Social Media", "Podcast"))

    assert opened is target
    assert manager.current_path == ("Lucie Content", "Social Media", "Podcast")
    assert manager.loaded == [
        (("Lucie Content", "Social Media", "Podcast"), "Ren Scratch - breaktest")]


def test_a_project_the_machine_cannot_reach_refuses_with_the_folder_named():
    """The refusal is TYPED and names the folder, so the reader knows
    the project exists and the machine's Project Manager is the thing
    that cannot get there."""
    manager = FolderManager(moves=False)

    with pytest.raises(folders.ProjectFolderRefused) as refused:
        folders.open_project_by_folder(
            manager, "Ren Scratch - breaktest",
            ("Lucie Content", "Social Media", "Podcast"))

    assert "Lucie Content/Social Media/Podcast" in str(refused.value)
    assert refused.value.blocker == "unsupported_operation"


def test_a_project_that_cannot_be_placed_is_not_guessed_at():
    """A Project Server database has no project tree on disk, so no
    folder path is read - and none is invented."""
    manager = FolderManager(db_type="ProjectServer")

    assert folders.database_folder_paths(manager, "Ren Scratch - x") == []
    assert folders.projects_root(manager) is None


class ResolveDouble:
    """The Resolve handle the scratch lifecycle reads."""

    def __init__(self, manager):
        self.manager = manager
        self.page = "edit"

    def GetProjectManager(self):
        return self.manager

    def GetCurrentPage(self):
        return self.page


def test_the_scratch_open_reaches_a_project_filed_in_another_folder(
        monkeypatch, tmp_path):
    """The defect: the scratch open refused a project that exists,
    because the Project Manager's listing is folder-scoped and the
    refusal named no folder. Now the project is reached by its folder
    path, and the folder it is filed in is in the receipt."""
    from library.tools import resolve_scratch_projects as scratch

    target = Project("Ren Scratch - breaktest")
    captain = Project("Podcast (field test)")
    manager = FolderManager(
        folders={("Lucie Content", "Social Media", "Podcast"): []},
        projects={(): [captain],
                  ("Lucie Content", "Social Media", "Podcast"): [target]},
        moves=True, current=captain)
    root = _database(tmp_path, {
        ("Lucie Content", "Social Media", "Podcast"): "Ren Scratch - breaktest",
    })
    manager.database_root = str(root)
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")
    monkeypatch.setattr(scratch.resolve_switch_guard, "note_project_switch",
                        lambda: None)
    monkeypatch.setattr(scratch.resolve_switch_guard, "SETTLE_SECONDS", 0)

    result = scratch.open_project(
        ResolveDouble(manager), manager, "Ren Scratch - breaktest")

    assert result["opened"] is True
    assert result["folder"] == "Lucie Content/Social Media/Podcast"
    assert manager.current is target
    assert manager.loaded == [
        (("Lucie Content", "Social Media", "Podcast"),
         "Ren Scratch - breaktest")]


def test_the_session_opens_a_project_filed_in_another_folder(
        monkeypatch, tmp_path):
    """The project-pinned session opens the project it is for, even when
    that project is filed in a Project Manager sub-folder - by that
    folder's path."""
    import time
    from contextlib import nullcontext

    from library.tools import resolve_lock, resolve_session

    target = Project("Ren Scratch - breaktest")
    captain = Project("Podcast (field test)")
    manager = FolderManager(
        projects={(): [captain],
                  ("Lucie Content", "Social Media", "Podcast"): [target]},
        moves=True, current=captain)
    root = _database(tmp_path, {
        ("Lucie Content", "Social Media", "Podcast"): "Ren Scratch - breaktest",
    })
    manager.database_root = str(root)
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr("library.tools.marker_feedback.connect_resolve",
                        lambda: ResolveDouble(manager))
    monkeypatch.setattr("library.tools.resolve_switch_guard.wait_for_settle",
                        lambda: None)
    monkeypatch.setattr("library.tools.resolve_switch_guard.note_project_switch",
                        lambda: None)
    monkeypatch.setattr("library.tools.resolve_switch_guard.SETTLE_SECONDS", 0)
    session = resolve_session.Session(
        id="s", purpose="reels build", owner="lane",
        project="Ren Scratch - breaktest", timeline="", started_at=time.time())

    opened = resolve_session.open_project(session, timeout=1.0)

    assert opened.opened_project == "Ren Scratch - breaktest"
    assert manager.current is target
    assert manager.loaded == [
        (("Lucie Content", "Social Media", "Podcast"),
         "Ren Scratch - breaktest")]


def test_the_scratch_open_refuses_with_the_folder_named(monkeypatch, tmp_path):
    """A machine whose Project Manager cannot move refuses with the
    folder the project is filed in - not "not in the current folder"."""
    from library.tools import resolve_scratch_projects as scratch

    target = Project("Ren Scratch - breaktest")
    captain = Project("Podcast (field test)")
    manager = FolderManager(
        projects={(): [captain],
                  ("Lucie Content", "Social Media", "Podcast"): [target]},
        moves=False, current=captain)
    root = _database(tmp_path, {
        ("Lucie Content", "Social Media", "Podcast"): "Ren Scratch - breaktest",
    })
    manager.database_root = str(root)
    monkeypatch.setattr(scratch, "_origin_path",
                        lambda: tmp_path / "scratch-origin.json")

    with pytest.raises(folders.ProjectFolderRefused) as refused:
        scratch.open_project(
            ResolveDouble(manager), manager, "Ren Scratch - breaktest")

    assert "Lucie Content/Social Media/Podcast" in str(refused.value)
