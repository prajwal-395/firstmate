"""A session that names a project must OPEN it, and refuse by name.

The failure this closes (measured 2026-10-21): four lanes
(vep-live-evidence, vep-ren-breaktest, p-axi006-trim,
vep-reel13-span-windows) failed because another lane had left a
different scratch project open. The session held the broker's turn, so
no other lane could be GRANTED a turn - but the project the session
found was the one the last lane left there, and the session wrote into
it anyway.

So a session that names a project:

* OPENS that project when the session starts, and reads the open
  project back by exact name;
* refuses BY NAME if the project cannot be opened, or if the open
  project is ever something else - before a write, not after it.

A session that names no project opens nothing and verifies nothing: it
is not about a project, and it says so.

None of these tests touches a real Resolve: the handle, the project
manager and the switch are doubles.
"""

from __future__ import annotations

import pytest

from library.tools import resolve_lock
from library.tools import resolve_session as sessions
from library.tools.resolve_session import SessionProjectRefused


class _FakeProject:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name


class _FakeManager:
    """The Project Manager, with the project it has open."""

    def __init__(self, name):
        self.current = name
        self.switches = []

    def GetCurrentProject(self):
        return _FakeProject(self.current) if self.current else None

    def LoadProject(self, name):
        self.switches.append(name)
        self.current = name
        return _FakeProject(name)


class _FakeResolve:
    def __init__(self, manager):
        self._manager = manager

    def GetProjectManager(self):
        return self._manager


@pytest.fixture(autouse=True)
def unguarded(monkeypatch, tmp_path):
    """Undo the suite's sole-writer declaration, and give the test a lease.

    The suite declares itself the sole writer because its Resolve is a
    mock, and a session that finds the process "holding" the instance
    JOINS that hold instead of claiming a turn - so the session would
    never open its project. These tests are ABOUT the session's own
    claim, so they need the real one, on a lease of their own.
    """
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(tmp_path / "locks"))
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


@pytest.fixture
def fake_resolve(monkeypatch):
    """A Resolve double whose project manager is under the test's hand.

    `open_project` opens whatever `switch_project` leaves open, so the
    double decides whether the switch took effect.
    """
    state = {"manager": _FakeManager("Somebody Elses Project")}

    def _switch(manager, name, **kwargs):
        state["switched"] = (manager, name)
        state["switch"](manager, name)
        return manager.GetCurrentProject()

    def _default_switch(manager, name):
        manager.current = name

    state["switch"] = _default_switch

    def _connect():
        assert resolve_lock.held(), (
            "a session's project readback must happen under the instance "
            "lease before the section can proceed")
        return _FakeResolve(state["manager"])

    monkeypatch.setattr("library.tools.marker_feedback.connect_resolve",
                        _connect)
    monkeypatch.setattr("library.tools.qualification_project.switch_project",
                        _switch)
    return state


def test_a_session_opens_the_project_it_is_for(fake_resolve):
    """The session's project is opened, and read back by exact name."""
    with sessions.resolve_session("a build", project="Ren Scratch - breaktest") as session:
        assert session.opened_project == "Ren Scratch - breaktest"
        assert fake_resolve["switched"][1] == "Ren Scratch - breaktest"
        assert fake_resolve["manager"].current == "Ren Scratch - breaktest"


def test_a_session_whose_project_does_not_open_refuses_by_name(fake_resolve):
    """A switch that does not take effect is a refusal, not a session.

    The refusal names BOTH projects: the one the session is for and the
    one Resolve has open, because the operator has to know which one to
    open by hand.
    """
    fake_resolve["switch"] = lambda manager, name: None
    with pytest.raises(SessionProjectRefused) as refused:
        with sessions.resolve_session("a build", project="Ren Scratch - breaktest"):
            pass
    message = str(refused.value)
    assert "Ren Scratch - breaktest" in message
    assert "Somebody Elses Project" in message


def test_a_write_inside_a_session_refuses_when_the_project_changed(fake_resolve):
    """The project is verified under the instance lease before a write."""
    with sessions.resolve_session("a build", project="Ren Scratch - breaktest") as session:
        assert session.opened_project == "Ren Scratch - breaktest"
        # Another lane (or a hand) left a different project open.
        fake_resolve["manager"].current = "Podcast (field test)"
        with pytest.raises(SessionProjectRefused) as refused:
            with resolve_lock.resolve_lease("a write"):
                pass
        message = str(refused.value)
        assert "Ren Scratch - breaktest" in message
        assert "Podcast (field test)" in message


def test_a_session_that_names_no_project_opens_nothing(fake_resolve):
    """A session with no target project is not about a project."""
    with sessions.resolve_session("a build") as session:
        assert session.opened_project == ""
        assert "switched" not in fake_resolve


def test_session_project_readback_is_under_the_instance_lease(fake_resolve):
    """Every section checks the project under lease before it runs.

    The lease is the point where a write happens, so the check is
    there: a section that would write into another lane's project is
    refused by name. Connecting before the instance lease is established
    violates the scriptapp boundary and refuses before this check can run.
    """
    with sessions.resolve_session("a build", project="Ren Scratch - breaktest"):
        fake_resolve["manager"].current = "Podcast (field test)"
        with pytest.raises(SessionProjectRefused):
            with resolve_lock.resolve_lease("a write"):
                pass
