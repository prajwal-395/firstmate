"""The exact project lookup reaches a project that is OPEN.

Each test names the defect it catches (docs/TEST_AUTHORING.md):

- a touch or an undo that refused a project which was open right now,
  because the Project Manager's folder listing had been reset to the
  root by an earlier `GetCurrentProject()` call - measured live on
  2026-10-22, where `ren touch` said "No project named exactly 'Ren
  Scratch - breaktest' in the current folder. Resolve lists []" for a
  project that was open and named in the refusal itself;
- a lookup that answers with a NEAR name, which lands on somebody
  else's project.

Driven against the repo's Resolve double; no project and no Resolve.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import timeline_ingest
from library.tools.timeline_ingest import TimelineIngestError
from tests.resolve_double import FakeProject, FakeProjectManager


class _ManagerWhoseCurrentProjectResetsTheFolder(FakeProjectManager):
    """A Project Manager that behaves as Resolve 21.1 does.

    Measured live on 2026-10-22: `GetCurrentProject()` puts the Project
    Manager's CURRENT FOLDER back to the root, so the folder listing
    read after it is the ROOT's projects - and a project that is open
    reads as absent.
    """

    def __init__(self, project=None, project_names=None, *,
                 folder_projects=None, root_projects=()):
        super().__init__(project, project_names)
        self._folder_projects = dict(folder_projects or {})
        self._root_projects = list(root_projects)
        self._folder_path = ("Lucie Content", "Social Media", "Podcast")

    def GetCurrentProject(self):
        self._folder_path = ()
        return self._project

    def GetProjectListInCurrentFolder(self):
        if self._folder_path in self._folder_projects:
            return list(self._folder_projects[self._folder_path])
        return list(self._root_projects)


def test_the_open_project_is_reached_even_when_the_folder_listing_is_gone():
    """The project is open; the listing says nothing; the lookup answers."""
    target = FakeProject("Ren Scratch - breaktest")
    manager = _ManagerWhoseCurrentProjectResetsTheFolder(
        target, folder_projects={
            ("Lucie Content", "Social Media", "Podcast"): ["Ren Scratch - breaktest"]},
        root_projects=["Ren Qualification"])

    found = timeline_ingest.resolve_project_exactly(
        manager, "Ren Scratch - breaktest")

    assert found is target


def test_a_project_that_is_not_open_is_still_refused_by_exact_name():
    """The near-match protection is untouched by the open-project answer."""
    target = FakeProject("Ren Qualification")
    manager = _ManagerWhoseCurrentProjectResetsTheFolder(
        target, folder_projects={
            ("Lucie Content", "Social Media", "Podcast"): ["Ren Qualification"]},
        root_projects=["Ren Qualification"])

    with pytest.raises(TimelineIngestError) as refused:
        timeline_ingest.resolve_project_exactly(
            manager, "Ren Scratch - breaktest")

    message = str(refused.value)
    assert "Ren Scratch - breaktest" in message
    assert "guess" in message.lower()
