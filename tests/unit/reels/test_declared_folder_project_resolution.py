"""The build resolves its project in the folder the project DECLARES.

The defect this names
---------------------
On the machine where several lanes share ONE Resolve, the Project
Manager's CURRENT folder moves under a running build: another lane's
work navigates it. `resolve_project_exactly` looks in the current
folder, so the build then refuses with "No project named exactly 'Ren
Scratch - breaktest' in the current folder. Resolve lists []" although
the project is open and the project.yaml declares it (measured
2026-10-21, scratch Reel 13: the reel was placed, the conformance gate
PASSED, and the verification that followed could not find the project
any more).

The project's own declared folder is where it lives, so the lookup
goes there first - and restores the Project Manager's path afterwards,
because that path is not the build's to move.

What this file proves
---------------------
- a project in the DECLARED folder is found from a current folder that
  does not list it, and the Project Manager's path is restored;
- a project with no declared folder is still resolved exactly, in the
  current folder.
"""
from __future__ import annotations

from library.tools import reel_build


class _Folder:
    """A Project Manager whose current folder holds no projects."""

    def __init__(self, folders, projects):
        self._folders = folders
        self._projects = projects
        self.current = ""
        self.opened = []
        self.restored = None

    def GotoRootFolder(self):
        self.current = ""
        return True

    def GetCurrentFolder(self):
        return self.current

    def GetFoldersInCurrentFolder(self):
        return {index + 1: name
                for index, name in enumerate(self._folders)}

    def OpenFolder(self, name):
        self.opened.append(name)
        self.current = name
        return True

    def GetProjectListInCurrentFolder(self):
        return list(self._projects.get(self.current, []))

    def GetCurrentProject(self):
        return None


def test_the_declared_folder_is_where_the_project_is_looked_for(monkeypatch):
    """THE DEFECT.  The current folder lists no project; the declared
    folder lists ours."""
    from library.tools import timeline_ingest

    manager = _Folder(["Lucie Content", "Other"],
                      {"Other": ["Somebody Else"],
                       "Lucie Content": ["Ren Scratch - breaktest"]})
    resolved = {}

    def _fake_resolve(manager_arg, name, folder="", **_kwargs):
        resolved["folder"] = folder
        from library.tools.timeline_ingest import _open_folder_path
        _open_folder_path(manager_arg, tuple(folder.split("/")))
        return {"name": name}

    monkeypatch.setattr(
        timeline_ingest, "resolve_project_in_declared_folder", _fake_resolve)

    project = reel_build.resolve_declared_project(
        manager, "Ren Scratch - breaktest", "Lucie Content")

    assert project["name"] == "Ren Scratch - breaktest"
    assert resolved["folder"] == "Lucie Content", (
        "the lookup goes to the folder the project declares")
    assert manager.opened == ["Lucie Content"], (
        "the Project Manager is navigated by EXACT segment")


def test_a_project_with_no_declared_folder_is_still_resolved_exactly():
    """No declared folder: the exact lookup, in the current folder, as
    before."""
    seen = {}

    class _Manager:
        def GetCurrentProject(self):
            return None

        def GetProjectListInCurrentFolder(self):
            return ["Ren Scratch - breaktest"]

    def _fake_exactly(manager, name):
        seen["name"] = name
        return {"name": name}

    import library.tools.reel_build as module
    original = module.resolve_project_exactly
    module.resolve_project_exactly = _fake_exactly
    try:
        project = module.resolve_declared_project(
            _Manager(), "Ren Scratch - breaktest", "")
    finally:
        module.resolve_project_exactly = original

    assert project["name"] == "Ren Scratch - breaktest"
    assert seen["name"] == "Ren Scratch - breaktest"
