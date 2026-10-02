"""Render selection goes through the cursor guard.

`resolve_render._select_timeline` establishes the cursor before the
render queue is touched. A direct `SetCurrentTimeline` there walked
the cursor unleashed - the 2026-09-20 shape that killed a sibling
lane's Fusion pass - so the select goes through
`assert_current_timeline` (lease refusal plus read-back), while
`_find_timeline` names a handle without moving anything at all.
"""

import pytest

from library.tools import resolve_lock
from library.tools.execution.resolve_render import (
    RenderError,
    _find_timeline,
    _select_timeline,
)
from tests.resolve_double import FakeTimeline, make_project


@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


def _project():
    mine = FakeTimeline("Pipeline_Edit")
    sibling = FakeTimeline("Reel 05 - final")
    project = make_project(timelines=[mine, sibling], current=sibling)
    return project, mine, sibling


def test_find_names_a_handle_without_moving_the_cursor(unguarded):
    """Lookup is read-only: no lease needed, cursor untouched."""
    project, mine, sibling = _project()
    assert _find_timeline(project, mine.GetName()) is mine
    assert project.GetCurrentTimeline() is sibling
    assert project.set_calls == []


def test_find_of_a_missing_timeline_names_it(unguarded):
    project, _, _ = _project()
    with pytest.raises(RenderError, match="not in this project"):
        _find_timeline(project, "gone")


def test_select_without_a_lease_is_refused_before_moving(unguarded):
    """The 2026-09-20 shape fails loud instead of landing elsewhere."""
    assert not resolve_lock.held()
    project, mine, sibling = _project()
    with pytest.raises(resolve_lock.UnguardedPlacementError):
        _select_timeline(project, mine.GetName())
    assert project.GetCurrentTimeline() is sibling
    assert project.set_calls == []


def test_select_under_a_lease_asserts_the_cursor():
    """Leased: the cursor is established and read back."""
    from library.tools.resolve_lock import assume_sole_writer

    project, mine, _sibling = _project()
    with assume_sole_writer("test: fake project has no instance to contend for"):
        assert _select_timeline(project, mine.GetName()) is mine
    assert project.GetCurrentTimeline() is mine
    assert project.set_calls == [mine.GetName()]
