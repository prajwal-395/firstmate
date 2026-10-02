"""Fusion subprocess destination guard - wrong timeline / project is refused.

The Fusion subprocess (apply_fusion_comps.py) runs in a SEPARATE process
from the timeline builder.  Between launch and first mutation, any of the
eleven live davinci-resolve-mcp server processes could call
SetCurrentTimeline or change the current project.  The guard verifies
that the current project and timeline match what the parent told the
subprocess to expect, IMMEDIATELY before any mutation, and refuses
loudly on mismatch rather than writing Fusion comps onto the captain's
rough cut.

These tests use plain mock objects - no Resolve writes.
"""
import pytest

from library.tools import resolve_lock

# apply_fusion_comps lives outside a regular package and needs
# DaVinciResolveScript on sys.path.  We mock the import so the tests
# run without Resolve installed. Its sibling imports
# (`from transition_vocabulary import ...`) are served by
# tests/conftest.py, which owns every non-root sys.path entry.
from library.tools.execution.apply_fusion_comps import (  # noqa: E402
    DestinationMismatchError,
    assert_destination,
    verify_destination,
    _map_clips_to_items,
)


# ── Mock helpers ────────────────────────────────────────────────

@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration.

    The suite declares itself the sole writer because its Resolve is a
    mock. A test ABOUT the guard has to stand outside that (same shape
    as `unguarded` in test_resolve_lock.py)."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)

class MockTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return str(id(self))


class MockProject:
    def __init__(self, name, timeline=None, timelines=None):
        self._name = name
        self._timeline = timeline
        self._timelines = (list(timelines) if timelines is not None
                           else ([timeline] if timeline is not None else []))
        self.set_calls = []

    def GetName(self):
        return self._name

    def GetCurrentTimeline(self):
        return self._timeline

    def SetCurrentTimeline(self, tl):
        self.set_calls.append(tl.GetName() if tl else None)
        self._timeline = tl
        return True

    def GetTimelineCount(self):
        return len(self._timelines)

    def GetTimelineByIndex(self, index):
        if 1 <= index <= len(self._timelines):
            return self._timelines[index - 1]
        return None


class MockProjectManager:
    def __init__(self, project=None):
        self._project = project

    def GetCurrentProject(self):
        return self._project


class MockResolve:
    def __init__(self, pm=None):
        self._pm = pm or MockProjectManager()

    def GetProjectManager(self):
        return self._pm


class MockMediaPoolItem:
    def __init__(self, path):
        self._path = path

    def GetClipProperty(self, prop):
        if prop == "File Path":
            return self._path


class MockTimelineItem:
    def __init__(self, mpi):
        self._mpi = mpi

    def GetMediaPoolItem(self):
        return self._mpi


# ── verify_destination tests ─────────────────────────────────────

def test_verify_destination_passes_only_the_named_project_and_timeline():
    edit = "Pipeline_Edit_20260901_45s"
    tl = MockTimeline(edit)
    proj = MockProject("Podcast", timeline=tl)
    resolve = MockResolve(pm=MockProjectManager(project=proj))
    assert verify_destination(resolve, "Podcast", edit) == (proj, tl)
    rows = (
        (MockProject("Some Other Project", timeline=tl),
         "Wrong Resolve project"),
        (MockProject("Podcast", timeline=MockTimeline("Rough Cut v3")),
         "Wrong timeline"),
        (None, "No Resolve project"),
        (MockProject("Podcast", timeline=None), "No timeline is current"),
    )
    for project, match in rows:
        resolve = MockResolve(pm=MockProjectManager(project=project))
        with pytest.raises(DestinationMismatchError, match=match):
            verify_destination(resolve, "Podcast", edit)


def test_clips_map_to_items_by_full_path_only():
    """Basename-only match is REJECTED - the H2 guard. The basename
    fallback was the exact defect: the captain's rough cut uses the same
    footage, so basename matches succeed against the wrong timeline. A
    matcher that succeeds against wrong material is worse than one that
    fails. Clips without a source_file are skipped."""
    def items(*paths):
        return [MockTimelineItem(MockMediaPoolItem(p)) for p in paths]

    assert _map_clips_to_items(
        [{"source_file": "/footage/clip_A.mov"},
         {"source_file": "/footage/clip_B.mov"}],
        items("/footage/clip_A.mov", "/footage/clip_B.mov")) == {0: 0, 1: 1}
    assert _map_clips_to_items(
        [{"source_file": "/pipeline/output/clip_A.mov"}],
        items("/footage/raw/clip_A.mov")) == {}
    assert _map_clips_to_items(
        [{"label": "intro"}, {"source_file": "/footage/clip_A.mov"}],
        items("/footage/clip_A.mov")) == {1: 0}
    assert _map_clips_to_items(
        [{"source_file": "/footage/clip_A.mov"}], []) == {}


# ── Subprocess CLI arg parsing ───────────────────────────────────


# ── assert_destination tests ─────────────────────────────────────

class TestAssertDestination:
    """The cursor is ASSERTED under lease, then read back - never assumed.

    2026-09-20: a duplicate-take rebuild died at its Fusion pass with
    a sibling lane's final current. Depending on ambient cursor state
    stalls every concurrent wave on a refusal; asserting it (exact
    name, same project) serialises lanes through the exclusive lease
    instead, while the read-back still refuses a mutator that never
    agreed to any lock.
    """

    @pytest.fixture(autouse=True)
    def _sole_writer(self):
        """The pass runs under its own exclusive lease in production;
        the mocks here have no instance to contend for."""
        from library.tools.resolve_lock import assume_sole_writer
        with assume_sole_writer(
                "test: mock project has no instance to contend for"):
            yield

    def _project(self, current, *timelines):
        proj = MockProject("Podcast", timeline=current,
                           timelines=list(timelines))
        return proj, MockResolve(MockProjectManager(project=proj))

    def test_sibling_timeline_current_asserts_onto_staging(self):
        """The measured incident: sibling's final current, staging set."""
        sibling = MockTimeline("Reel 05 - ai-cant-form-a-clear-picture-of-you")
        staging = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        proj, resolve = self._project(sibling, sibling, staging)
        returned_project, returned_timeline = assert_destination(
            resolve, "Podcast",
            "Reel 06 - size-doesnt-matter (rebuild staging)")
        assert returned_project is proj
        assert returned_timeline is staging
        assert proj.GetCurrentTimeline() is staging
        assert proj.set_calls == [staging.GetName()]
        # Idempotent: asserting the timeline already current verifies.
        proj, resolve = self._project(staging, staging)
        _, returned_timeline = assert_destination(
            resolve, "Podcast", staging.GetName())
        assert returned_timeline is staging

    def test_a_missing_staging_or_wrong_project_refuses_without_moving(self):
        """The staging is gone - refuse, and do not move the cursor."""
        sibling = MockTimeline("Reel 05 - ai-cant-form-a-clear-picture-of-you")
        proj, resolve = self._project(sibling, sibling)
        with pytest.raises(DestinationMismatchError, match="not found"):
            assert_destination(resolve, "Podcast", "Reel 06 - gone staging")
        assert proj.GetCurrentTimeline() is sibling
        assert proj.set_calls == []
        # Another project open: refuse before touching anything.
        tl = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        proj = MockProject("Some Other Project", timeline=tl,
                           timelines=[tl])
        resolve = MockResolve(MockProjectManager(project=proj))
        with pytest.raises(DestinationMismatchError,
                           match="Wrong Resolve project"):
            assert_destination(resolve, "Podcast", tl.GetName())
        assert proj.set_calls == []

    def test_mutator_between_set_and_verify_still_refuses(self):
        """The read-back stays: a move inside the assert-verify window fails.

        A mutator that never agreed to any lock cannot be serialised -
        the verify-immediately-before-mutation is what protects that
        write, and the assert must not swallow it.
        """
        staging = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        intruder = MockTimeline("Rough Cut v3")

        class RacyProject(MockProject):
            def SetCurrentTimeline(self, tl):
                super().SetCurrentTimeline(tl)
                self._timeline = intruder  # moved again before the read-back

        proj = RacyProject("Podcast", timeline=intruder,
                           timelines=[intruder, staging])
        resolve = MockResolve(MockProjectManager(project=proj))
        with pytest.raises(DestinationMismatchError, match="Wrong timeline"):
            assert_destination(resolve, "Podcast", staging.GetName())


class TestAssertDestinationNeedsALease:
    """The establishment goes through the guard, so without a lease it
    refuses BEFORE moving the cursor - the 2026-09-20 shape (an
    unleased move colliding with a sibling lane's pass) fails loud
    instead of landing somewhere wrong."""

    def test_assertion_without_a_lease_is_refused(self, unguarded):
        from library.tools import resolve_lock
        assert not resolve_lock.held()
        sibling = MockTimeline("Reel 05 - final")
        staging = MockTimeline("Reel 06 - staging")
        proj = MockProject("Podcast", timeline=sibling,
                           timelines=[sibling, staging])
        resolve = MockResolve(MockProjectManager(project=proj))
        with pytest.raises(resolve_lock.UnguardedPlacementError):
            assert_destination(resolve, "Podcast", staging.GetName())
        assert proj.GetCurrentTimeline() is sibling
        assert proj.set_calls == []
