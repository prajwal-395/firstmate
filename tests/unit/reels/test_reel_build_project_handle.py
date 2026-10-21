"""The build reads the project Resolve has OPEN, and says so when it is not.

Measured 2026-10-21 on the Ren Scratch - breaktest project: an approved
reels build died with

    ReelBuildError: Resolve returned no timeline count for this project;
    the enumeration cannot be read

during the abandoned-candidate cleanup. The build had taken the instance
lease in three SEPARATE sections - the connect, the promotion recovery
and the cleanup - so the instance was FREE in the gaps between them, and
another writer took it and moved Resolve's cursor to its own project.
The build then read through the project handle it had captured at
connect time, and Resolve answered None for a count it had read a
moment ago.

Two things were wrong, and both are pinned here:

* the sections that read the project were not ONE hold, so the cursor
  could move between them; and
* the handle was captured once and read through for the whole build, so
  a cursor that moved was read through rather than refused BY NAME.

Nothing here reaches Resolve or a real project.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from library.tools import reel_build as rb
from library.tools import resolve_lock
from tests.promotion_test_helpers import (
    install_fake_timeline_snapshots, install_measured_draw_gain_probe)

MASTER = "GEO Podcast - Synced"
PROJECT_NAME = "Mock Project"


class _Handle:
    """A project handle, alive while the cursor is on the project.

    Resolve hands out a NEW wrapper every time the project is resolved,
    and a wrapper taken before the cursor moved stops answering: the
    bridge answers None for a count it once read. The double models the
    ANSWER and its timing - a handle taken after the last cursor move
    answers, one taken before it does not.
    """

    def __init__(self, world):
        self._world = world

    def GetName(self):
        return PROJECT_NAME

    def GetTimelineCount(self):
        if self._world.moved_after(self):
            return None
        return len(self._world.timelines)

    def GetTimelineByIndex(self, index):
        timelines = self._world.timelines
        if index < 1 or index > len(timelines):
            return None
        return timelines[index - 1]

    def GetMediaPool(self):
        return self._world.pool

    def GetCurrentTimeline(self):
        return None


class _World:
    """The machine's Resolve state, as the build sees it."""

    def __init__(self):
        self.timelines = []
        self.moves = 0
        self.pool = MagicMock()
        self.handles = []

    def new_handle(self):
        handle = _Handle(self)
        self.handles.append(handle)
        return handle

    def moved_after(self, handle):
        return handle not in self.handles or self.handles.index(handle) < self.moves


@pytest.fixture
def mock_dvr(stub_resolve_script, monkeypatch):
    install_fake_timeline_snapshots(monkeypatch)
    install_measured_draw_gain_probe(monkeypatch)
    yield


@pytest.fixture
def project_dir(tmp_path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.yaml").write_text(
        f'resolve: {{project_name: "{PROJECT_NAME}", '
        f'timeline_name: "{MASTER}"}}', encoding="utf-8")
    review = root / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "reel_proposals_v2.json").write_text("[]", encoding="utf-8")
    scratch = root / "pipeline_output" / "scratch" / "timeline_transcript"
    scratch.mkdir(parents=True)
    (scratch / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
    return root


class _Stopped(Exception):
    """The build stopped where this test wants it to stop."""


@contextmanager
def _a_cursor_that_moves_in_every_gap(world):
    """Every gap the build leaves the instance in, a writer takes it.

    The build releases the instance between its sections, and a writer
    that takes it moves Resolve's cursor to its own project. The move is
    what makes the handle the build captured earlier stop answering.
    """
    real = resolve_lock.resolve_lease

    @contextmanager
    def spy(purpose, exclusive=True, **kwargs):
        with real(purpose, exclusive=exclusive, **kwargs):
            yield
        if exclusive:
            world.moves += 1

    with patch.object(resolve_lock, "resolve_lease", spy), \
            patch.object(rb, "resolve_lease", spy):
        yield


def _run_the_build(project_dir, world):
    """The build, stopped at the first section after it opened the project."""
    from library.tools.reel_build import rebuild_reels_in_project

    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 7
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0

    with patch("library.tools.resolve_locale."
               "scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  side_effect=lambda manager, name: world.new_handle()), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[moment]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.subtitle_style.resolve_subtitle_style"), \
            patch("library.tools.reel_conformance_verifier.run_verification"), \
            patch.object(rb, "reel_resolution", side_effect=_Stopped):
        return rebuild_reels_in_project(str(project_dir), organise=False)


@pytest.mark.usefixtures("mock_dvr")
def test_a_cursor_that_moves_between_the_opening_sections_cannot_stop_the_build(
        project_dir):
    """The build's opening reads survive a cursor that moves in the gap.

    The defect: the connect, the promotion recovery and the
    abandoned-candidate cleanup were three holds, so the instance was
    free between them; a writer took it, moved the cursor, and the
    cleanup read through a handle Resolve had stopped answering - dead
    on "Resolve returned no timeline count".
    """
    world = _World()
    with _a_cursor_that_moves_in_every_gap(world):
        with pytest.raises(_Stopped):
            _run_the_build(project_dir, world)


@pytest.mark.usefixtures("mock_dvr")
def test_a_cursor_on_another_project_is_refused_by_name(project_dir):
    """A cursor that moved onto another project is named, not read through.

    The defect: the build read through the handle it captured at connect
    time, so a cursor that had moved was answered with a count that
    never settles - a message naming neither the cursor nor the project.
    The refusal has to name the project Resolve is on.
    """
    world = _World()
    other = MagicMock()
    other.GetName.return_value = "Podcast (field test)"

    def refuse(manager, name):
        if name == PROJECT_NAME:
            from library.tools.timeline_ingest import TimelineIngestError
            raise TimelineIngestError(
                f"{name!r} is in the current folder but is not the open "
                f"project (open: 'Podcast (field test)').")
        return world.new_handle()

    from library.tools.reel_build import rebuild_reels_in_project
    moment = MagicMock()
    moment.approval = "approved"
    moment.number = 7
    moment.timeline_name = "Reel 01"
    moment.timeline_start = 0.0
    moment.timeline_end = 10.0

    with patch("library.tools.resolve_locale."
               "scriptapp_preserving_locale"), \
            patch("library.tools.reel_build.resolve_project_exactly",
                  side_effect=refuse), \
            patch("library.tools.reel_proposal.read_proposal",
                  return_value=[moment]), \
            patch("library.tools.timeline_ingest.snapshot_timeline"), \
            patch("library.tools.subtitle_style.resolve_subtitle_style"), \
            patch("library.tools.reel_conformance_verifier.run_verification"):
        with pytest.raises(rb.ReelBuildError) as raised:
            rebuild_reels_in_project(str(project_dir), organise=False)
    message = str(raised.value)
    assert "Podcast (field test)" in message, message
    assert "no timeline count" not in message, message


def test_an_unreadable_count_is_not_an_empty_project():
    """A count Resolve cannot read is not a project with no timelines.

    The defect: `timeline_inventory` read the count as `count or 0`, so
    a count the bridge answered None for - a handle whose project the
    cursor had left - read as a project that holds NO TIMELINES, and
    the build measured itself against that inventory.
    """
    from library.tools import reel_replace_guard

    class _Stale:
        def GetTimelineCount(self):
            return None

    with pytest.raises(reel_replace_guard.ReplaceGuardUnreadable) as raised:
        reel_replace_guard.timeline_inventory(_Stale())
    assert "no timeline count" in str(raised.value)


def test_the_count_refusal_names_the_project_it_could_not_read():
    """The refusal names the project whose count would not settle."""
    from library.tools.reel_build import ReelBuildError, _timeline_count

    class _Stale:
        def GetName(self):
            return "Ren Scratch - breaktest"

        def GetTimelineCount(self):
            return None

    with pytest.raises(ReelBuildError) as raised:
        _timeline_count(_Stale(), attempts=1)
    message = str(raised.value)
    assert "no timeline count" in message, message
    assert "Ren Scratch - breaktest" in message, message
