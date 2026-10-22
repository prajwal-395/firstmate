"""A write opens the project it names, under the lease that covers it.

THE DEFECT (measured 2026-10-21, the round-2 re-measurement): ten live
write attempts on this machine, every one refused - two by the queue,
seven because ANOTHER LANE had opened its own project, one on a media
kind. Seven of them carried the same sentence: "is in the current
folder but is not the open project (open: <another lane's project>)".
Once, the project that was open was the captain's own.

The cause is not the engine's speed and not the queue: the open project
is GLOBAL session state. One Resolve instance has one open project, and
every lane's job sees it, so a write that names a project has to make
that project the open one - under the lease that covers the write, so
no other lane can move the cursor between the open and the write - and
put the borrowed project back when it ends.

These are the DEFECTS the fix could reintroduce, each named for what it
would break:

1. the project a write names is OPEN while the write runs;
2. the borrowed project is put back afterwards;
3. a project that is already open is not loaded again;
4. a SHARED lease refuses to open a project (the open project is global
   state, and a shared lease lets another writer move it mid-write);
5. a broker job opens the project it names, under the job's own lease -
   the refusal it replaces was "the broker never switches projects";
6. the touch path opens the project it edits.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


class _FakeProject:
    """A project Resolve has open."""

    def __init__(self, name: str) -> None:
        self._name = name

    def GetName(self) -> str:
        return self._name

    def GetCurrentTimeline(self):
        return None


class _FakeManager:
    """A Project Manager with one open project."""

    def __init__(self, current: str, projects: list[str]) -> None:
        self.current = current
        self.projects = list(projects)
        self.loaded: list[str] = []
        self.saved: list[str] = []

    def GetCurrentProject(self):
        return _FakeProject(self.current)

    def GetProjectListInCurrentFolder(self):
        return list(self.projects)

    def LoadProject(self, name: str):
        self.loaded.append(name)
        self.current = name
        return _FakeProject(name)

    def SaveProject(self) -> bool:
        self.saved.append(self.current)
        return True

    def GetCurrentFolder(self):
        return None

    def GotoParentFolder(self) -> bool:
        return False

    def GotoRootFolder(self) -> bool:
        return True

    def GetFoldersInCurrentFolder(self):
        return []

    def OpenFolder(self, name: str) -> bool:
        return True


class _FakeResolve:
    """The scripting handle, with one open project."""

    def __init__(self, manager: _FakeManager) -> None:
        self._manager = manager

    def GetProjectManager(self):
        return self._manager

    def GetCurrentPage(self) -> str:
        return "edit"


@pytest.fixture
def opened_project(monkeypatch):
    """A Resolve with `Project A` open, and the switch/restore recorded."""
    from library.tools import qualification_project

    manager = _FakeManager("Project A", ["Project A", "Project B"])
    state = {"manager": manager, "restored": []}

    def _switch(_manager, name, **_kwargs):
        manager.LoadProject(name)
        return _FakeProject(name)

    def _restore(_resolve, found):
        state["restored"].append(found["project"])
        manager.current = found["project"]

    monkeypatch.setattr(qualification_project, "switch_project", _switch)
    monkeypatch.setattr(qualification_project, "restore", _restore)
    return state


def _lease(monkeypatch, exclusive: bool = True):
    """A held instance lease, so the open is allowed to happen."""
    from library.tools import resolve_lock

    monkeypatch.setattr(resolve_lock, "exclusive_held", lambda: exclusive)
    monkeypatch.setattr(resolve_lock, "held", lambda: True)


def test_a_write_finds_its_own_project_open(opened_project, monkeypatch):
    """The project a write names is OPEN while the write runs.

    The defect: the write found another lane's project open and
    refused, seven times out of ten live attempts.
    """
    from library.tools import resolve_project_open

    _lease(monkeypatch)
    resolve = _FakeResolve(opened_project["manager"])

    with resolve_project_open.opened(resolve, "Project B") as project:
        assert project.GetName() == "Project B"
        assert opened_project["manager"].GetCurrentProject().GetName() == (
            "Project B")


def test_the_borrowed_project_is_put_back(opened_project, monkeypatch):
    """The machine is left as it was found: the borrowed project returns.

    The defect this stops: a write that opens a project and never puts
    the captain's back leaves the captain's project closed.
    """
    from library.tools import resolve_project_open

    _lease(monkeypatch)
    resolve = _FakeResolve(opened_project["manager"])

    with resolve_project_open.opened(resolve, "Project B"):
        pass

    assert opened_project["restored"] == ["Project A"]
    assert opened_project["manager"].GetCurrentProject().GetName() == (
        "Project A")


def test_a_project_that_is_already_open_is_not_loaded_again(opened_project,
                                                            monkeypatch):
    """Nothing is loaded, nothing is restored: the block changes nothing."""
    from library.tools import resolve_project_open

    _lease(monkeypatch)
    resolve = _FakeResolve(opened_project["manager"])

    with resolve_project_open.opened(resolve, "Project A") as project:
        assert project.GetName() == "Project A"

    assert opened_project["manager"].loaded == []
    assert opened_project["restored"] == []


def test_a_shared_lease_refuses_to_open_a_project(opened_project, monkeypatch):
    """A SHARED lease may not open a project.

    The open project is global session state: a shared lease lets
    another writer move it in the middle of the write, which is the
    race this mechanism exists to close.
    """
    from library.tools import resolve_project_open

    _lease(monkeypatch, exclusive=False)
    resolve = _FakeResolve(opened_project["manager"])

    with pytest.raises(resolve_project_open.ProjectOpenRefused) as refused:
        with resolve_project_open.opened(resolve, "Project B"):
            pass

    assert "EXCLUSIVE" in str(refused.value)
    assert opened_project["manager"].loaded == []


def test_a_refused_open_is_a_refusal_and_not_a_crash(opened_project, monkeypatch):
    """A project that cannot be opened is REFUSED, in the refusal's words.

    Defect caught: `ProjectOpenRefused` was a plain `RuntimeError`, so
    the front door caught it as an UNEXPECTED error - the operator got
    a Python traceback, `REN_UNEXPECTED` and exit 1, where every
    refusal beside it (`TouchupRefused`, `ResolveBusy`) gives what, why
    and fix and exit 4. Measured 2026-10-21 on this machine: a touch
    that waited 77 minutes for the one Resolve was finally granted the
    lease, refused to open its project, and reported exactly that
    traceback - so a refusal the engine had already decided arrived
    looking like a crash.
    """
    from library.tools import resolve_project_open
    from library.tools.ren_refusal import UNEXPECTED_CODE, RenRefusal

    _lease(monkeypatch)
    manager = opened_project["manager"]
    monkeypatch.setattr(manager, "SaveProject", lambda: False)
    resolve = _FakeResolve(manager)

    with pytest.raises(resolve_project_open.ProjectOpenRefused) as refused:
        with resolve_project_open.opened(resolve, "Project B"):
            pass

    assert isinstance(refused.value, RenRefusal)
    rendered = refused.value.render()
    assert "refused" in rendered
    assert "could not save" in rendered
    assert refused.value.code != UNEXPECTED_CODE
    assert refused.value.what and refused.value.why and refused.value.fix


def test_a_broker_job_opens_the_project_it_names(opened_project, monkeypatch):
    """The job's own lease opens the project the job names.

    The refusal this replaces, from the broker's own code: "Resolve has
    <another lane's project> open, not <the job's project>; the broker
    never switches projects - open <the job's project> first".
    """
    from library.tools.resolved import jobs

    _lease(monkeypatch)
    resolve = _FakeResolve(opened_project["manager"])

    with jobs._opened_project(resolve, "Project B") as project:
        assert project.GetName() == "Project B"
        assert opened_project["manager"].GetCurrentProject().GetName() == (
            "Project B")


def test_the_touch_path_opens_the_project_it_edits(tmp_path, monkeypatch):
    """A touch edits the reel in ITS project, and says so by opening it.

    The defect: the touch read and wrote against whichever project was
    open, and refused by name when that was not its own.
    """
    from contextlib import contextmanager

    from library.tools import perf_ledger
    from tests.unit.reels.test_touchup_direct import (
        _mock_qualification,
        _mock_resolve_app,
        _project,
        _property_spec,
        _sole_writer,
        _touch,
    )

    project, folder = _project(tmp_path, monkeypatch)
    _mock_resolve_app(monkeypatch, project)
    _mock_qualification(monkeypatch)

    opened: list[str] = []

    @contextmanager
    def _opener(resolve_name):
        opened.append(resolve_name)
        yield

    with (perf_ledger.recording(str(tmp_path / "perf_ledger.jsonl"),
                                 "run-open", "reel.touchup", required=True),
          _sole_writer()):
        receipt = _touch(folder, project, _property_spec(), opener=_opener)

    assert receipt["status"] == "applied_direct", receipt
    assert opened == [project.GetName()]


class _SilentManager(_FakeManager):
    """A Project Manager whose save answers NOTHING.

    The measured behaviour of this bridge (2026-10-21): `SaveProject()`
    answers None, and the engine read that None as "the save failed" -
    so every borrow on the machine refused with "Resolve would not save
    it", and no lane could open a scratch project at all.
    """

    def SaveProject(self):
        self.saved.append(self.current)
        return None


def test_a_save_that_answers_nothing_does_not_refuse_a_ren_project(
        monkeypatch):
    """Defect: a None answer is SILENCE, not a refusal.

    Ren's own projects are disposable and rebuildable, so an unknown
    save does not stop the borrow - and it must not stop EVERY borrow
    on the machine, which is what reading None as a refusal did.
    """
    from library.tools import qualification_project, resolve_project_open

    _lease(monkeypatch)
    manager = _SilentManager("Ren Qualification",
                              ["Ren Qualification", "Ren Scratch - a reel"])
    monkeypatch.setattr(qualification_project, "switch_project",
                        lambda m, name, **_kw: m.LoadProject(name))
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)
    resolve = _FakeResolve(manager)

    with resolve_project_open.opened(resolve, "Ren Scratch - a reel") as project:
        assert project.GetName() == "Ren Scratch - a reel"


def test_a_save_that_answers_nothing_still_refuses_a_captains_project(
        monkeypatch):
    """The captain's project is not borrowed on an UNKNOWN save."""
    from library.tools import qualification_project, resolve_project_open

    _lease(monkeypatch)
    manager = _SilentManager("Podcast (field test)",
                              ["Podcast (field test)", "Project B"])
    monkeypatch.setattr(qualification_project, "switch_project",
                        lambda m, name, **_kw: m.LoadProject(name))
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)
    resolve = _FakeResolve(manager)

    with pytest.raises(resolve_project_open.ProjectOpenRefused,
                       match="could not save"):
        with resolve_project_open.opened(resolve, "Project B"):
            pass


def test_a_save_that_is_refused_still_refuses_a_ren_project(monkeypatch):
    """A save that said NO is a refusal, whoever the project is."""
    from library.tools import qualification_project, resolve_project_open

    _lease(monkeypatch)
    manager = _FakeManager("Ren Qualification",
                            ["Ren Qualification", "Project B"])
    manager.SaveProject = lambda: False
    monkeypatch.setattr(qualification_project, "switch_project",
                        lambda m, name, **_kw: m.LoadProject(name))
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)
    resolve = _FakeResolve(manager)

    with pytest.raises(resolve_project_open.ProjectOpenRefused,
                       match="could not save"):
        with resolve_project_open.opened(resolve, "Project B"):
            pass


def test_the_save_outcome_names_what_the_call_said():
    """The outcome is read from what the call RETURNED, never guessed."""
    from library.tools.resolve_project_open import save_outcome

    class _Manager:
        def __init__(self, answer):
            self.answer = answer

        def SaveProject(self):
            if isinstance(self.answer, Exception):
                raise self.answer
            return self.answer

    assert save_outcome(_Manager(True))[0] == "saved"
    assert save_outcome(_Manager(False))[0] == "refused"
    assert save_outcome(_Manager(None))[0] == "unknown"
    assert save_outcome(_Manager(RuntimeError("no")))[0] == "refused"


# ── The project a write names is in ANOTHER FOLDER ──────────────────
#
# THE DEFECT (measured live 2026-10-22 on this machine, AXI-016's live
# run): every Ren-owned scratch project is filed in one Project Manager
# folder ("Lucie Content/Social Media/Podcast"), and the qualification
# project that is usually open sits at the ROOT.  The Project Manager's
# listing is folder-scoped and the folder it shows FOLLOWS THE OPEN
# PROJECT, so borrowing the root-level qualification project left the
# Project Manager at the root - where the scratch project reads as
# ABSENT.  `LoadProject` resolves a name inside the folder the Project
# Manager is showing, so the write refused with "'Ren Scratch -
# breaktest' did not read back after the switch (have 'Ren
# Qualification')" - a project that EXISTS, refused because of where
# it is filed.


def test_a_project_filed_in_another_folder_is_opened_by_its_folder_path(
        monkeypatch):
    """The write reaches the project by the folder it is filed in.

    The defect: every live write to a Ren-owned scratch project
    refused, because the borrow put the Project Manager in the
    qualification project's folder (the root) and the scratch project
    is filed in another one.
    """
    from library.tools import qualification_project, resolve_project_open
    from library.tools import resolve_project_folders

    _lease(monkeypatch)
    manager = _FakeManager("Ren Qualification", ["Ren Qualification"])
    resolve = _FakeResolve(manager)
    folder = ("Lucie Content", "Social Media", "Podcast")
    opened: list[tuple[str, tuple[str, ...]]] = []

    monkeypatch.setattr(
        resolve_project_folders, "database_folder_paths",
        lambda _manager, _name: [folder])
    monkeypatch.setattr(
        resolve_project_folders, "open_project_by_folder",
        lambda m, name, path: opened.append((name, tuple(path)))
        or m.LoadProject(name))
    monkeypatch.setattr(
        qualification_project, "switch_project",
        lambda m, name, **_kw: m.LoadProject(name))
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)

    with resolve_project_open.opened(
            resolve, "Ren Scratch - breaktest") as project:
        assert project.GetName() == "Ren Scratch - breaktest"

    assert opened == [("Ren Scratch - breaktest", folder)]


def test_a_project_in_the_folder_shown_is_opened_by_name(monkeypatch):
    """The folder route is not taken when the name is already listed."""
    from library.tools import qualification_project, resolve_project_open
    from library.tools import resolve_project_folders

    _lease(monkeypatch)
    manager = _FakeManager("Ren Qualification",
                            ["Ren Qualification", "Ren Scratch - breaktest"])
    resolve = _FakeResolve(manager)
    reached: list[str] = []

    monkeypatch.setattr(
        resolve_project_folders, "database_folder_paths",
        lambda _manager, _name: pytest.fail(
            "the folder route was taken for a project the Project "
            "Manager already lists"))
    monkeypatch.setattr(
        qualification_project, "switch_project",
        lambda m, name, **_kw: reached.append(name) or m.LoadProject(name))
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)

    with resolve_project_open.opened(resolve, "Ren Scratch - breaktest"):
        pass

    assert reached == ["Ren Scratch - breaktest"]


def test_a_name_two_folders_hold_is_refused_with_both_folders_named(
        monkeypatch):
    """Two copies are two projects; the wrong one is not ours to open."""
    from library.tools import qualification_project, resolve_project_open
    from library.tools import resolve_project_folders

    _lease(monkeypatch)
    manager = _FakeManager("Ren Qualification", ["Ren Qualification"])
    resolve = _FakeResolve(manager)

    monkeypatch.setattr(
        resolve_project_folders, "database_folder_paths",
        lambda _manager, _name: [("Lucie Content", "Social Media",
                                  "Podcast"), ("Work",)])
    monkeypatch.setattr(
        qualification_project, "switch_project",
        lambda m, name, **_kw: pytest.fail(
            "a name two folders hold must not be opened by name"))
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)

    with pytest.raises(resolve_project_open.ProjectOpenRefused) as refused:
        with resolve_project_open.opened(resolve, "Ren Scratch - breaktest"):
            pass

    rendered = refused.value.render()
    assert "more than one" in rendered
    assert "Lucie Content/Social Media/Podcast" in rendered
    assert "Work" in rendered


def test_a_switch_that_fails_is_a_refusal_and_not_a_crash(monkeypatch):
    """A failed switch is REFUSED, in the refusal's words.

    Defect: the switch failed with the qualification family's plain
    `RuntimeError`, so the front door reported `REN_UNEXPECTED` with a
    Python traceback - a refusal the engine had already decided,
    arriving as a crash (measured live 2026-10-22 on this machine, on
    the AXI-016 live run).
    """
    from library.tools import qualification_project, resolve_project_open
    from library.tools.ren_refusal import UNEXPECTED_CODE, RenRefusal

    _lease(monkeypatch)
    manager = _FakeManager("Ren Qualification", ["Ren Qualification"])
    resolve = _FakeResolve(manager)

    def _failed(_manager, name, **_kwargs):
        raise qualification_project.QualificationError(
            f"{name!r} did not read back after the switch "
            "(have 'Ren Qualification')")

    monkeypatch.setattr(qualification_project, "switch_project", _failed)
    monkeypatch.setattr(qualification_project, "restore",
                        lambda _r, found: None)

    with pytest.raises(resolve_project_open.ProjectOpenRefused) as refused:
        with resolve_project_open.opened(resolve, "Ren Scratch - breaktest"):
            pass

    assert isinstance(refused.value, RenRefusal)
    assert refused.value.code != UNEXPECTED_CODE
    rendered = refused.value.render()
    assert "could not open" in rendered
    assert "did not read back" in rendered
