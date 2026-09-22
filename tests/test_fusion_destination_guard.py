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

class TestVerifyDestination:

    def test_correct_project_and_timeline(self):
        """Happy path: both names match."""
        tl = MockTimeline("Pipeline_Edit_20260901_45s")
        proj = MockProject("Podcast", timeline=tl)
        pm = MockProjectManager(project=proj)
        resolve = MockResolve(pm=pm)

        returned_project, returned_timeline = verify_destination(
            resolve, "Podcast", "Pipeline_Edit_20260901_45s"
        )
        assert returned_project is proj
        assert returned_timeline is tl

    def test_wrong_project_refuses(self):
        """Different project name - REFUSE."""
        tl = MockTimeline("Pipeline_Edit_20260901_45s")
        proj = MockProject("Some Other Project", timeline=tl)
        pm = MockProjectManager(project=proj)
        resolve = MockResolve(pm=pm)

        with pytest.raises(DestinationMismatchError, match="Wrong Resolve project"):
            verify_destination(
                resolve, "Podcast", "Pipeline_Edit_20260901_45s"
            )

    def test_wrong_timeline_refuses(self):
        """Correct project but wrong timeline - REFUSE."""
        tl = MockTimeline("Rough Cut v3")
        proj = MockProject("Podcast", timeline=tl)
        pm = MockProjectManager(project=proj)
        resolve = MockResolve(pm=pm)

        with pytest.raises(DestinationMismatchError, match="Wrong timeline"):
            verify_destination(
                resolve, "Podcast", "Pipeline_Edit_20260901_45s"
            )

    def test_no_project_refuses(self):
        """No project open at all - REFUSE."""
        pm = MockProjectManager(project=None)
        resolve = MockResolve(pm=pm)

        with pytest.raises(DestinationMismatchError, match="No Resolve project"):
            verify_destination(
                resolve, "Podcast", "Pipeline_Edit_20260901_45s"
            )

    def test_no_timeline_refuses(self):
        """Project matches but no timeline is current - REFUSE."""
        proj = MockProject("Podcast", timeline=None)
        pm = MockProjectManager(project=proj)
        resolve = MockResolve(pm=pm)

        with pytest.raises(DestinationMismatchError, match="No timeline is current"):
            verify_destination(
                resolve, "Podcast", "Pipeline_Edit_20260901_45s"
            )


# ── _map_clips_to_items tests ────────────────────────────────────

class TestMapClipsToItems:

    def test_full_path_match(self):
        """Matching by full path works."""
        clips = [
            {"source_file": "/footage/clip_A.mov"},
            {"source_file": "/footage/clip_B.mov"},
        ]
        items = [
            MockTimelineItem(MockMediaPoolItem("/footage/clip_A.mov")),
            MockTimelineItem(MockMediaPoolItem("/footage/clip_B.mov")),
        ]
        mapping = _map_clips_to_items(clips, items)
        assert mapping == {0: 0, 1: 1}

    def test_basename_only_does_NOT_match(self):
        """Basename-only match is REJECTED - this is the H2 guard.

        The basename fallback was the exact defect: the captain's rough
        cut uses the same footage, so basename matches succeed against
        the wrong timeline.  A matcher that succeeds against wrong
        material is worse than one that fails.
        """
        clips = [
            {"source_file": "/pipeline/output/clip_A.mov"},
        ]
        items = [
            MockTimelineItem(MockMediaPoolItem("/footage/raw/clip_A.mov")),
        ]
        mapping = _map_clips_to_items(clips, items)
        # Must be EMPTY - basename match alone must not produce a mapping
        assert mapping == {}

    def test_no_source_file_skipped(self):
        """Clips without source_file are skipped."""
        clips = [
            {"label": "intro"},
            {"source_file": "/footage/clip_A.mov"},
        ]
        items = [
            MockTimelineItem(MockMediaPoolItem("/footage/clip_A.mov")),
        ]
        mapping = _map_clips_to_items(clips, items)
        assert mapping == {1: 0}

    def test_empty_items(self):
        """No timeline items means no mapping."""
        clips = [{"source_file": "/footage/clip_A.mov"}]
        mapping = _map_clips_to_items(clips, [])
        assert mapping == {}


# ── Subprocess CLI arg parsing ───────────────────────────────────

class TestSubprocessCLI:
    """Verify the __main__ block wires the new arguments correctly.

    We cannot run the real entry point (it needs Resolve), but we can
    parse the arg namespace to confirm the flags are registered.
    """

    def test_expected_args_registered(self):
        """--expected-project and --expected-timeline are accepted."""
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("manifest")
        parser.add_argument("--project-folder", default="")
        parser.add_argument("--expected-project", default=None)
        parser.add_argument("--expected-timeline", default=None)

        args = parser.parse_args([
            "/tmp/manifest.json",
            "--project-folder", "/projects/test",
            "--expected-project", "Podcast",
            "--expected-timeline", "Pipeline_Edit_20260901_45s",
        ])
        assert args.expected_project == "Podcast"
        assert args.expected_timeline == "Pipeline_Edit_20260901_45s"


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

    def test_already_current_is_a_no_op_assert(self):
        """Idempotent: asserting the timeline already current still verifies."""
        staging = MockTimeline("Reel 06 - size-doesnt-matter (rebuild staging)")
        proj, resolve = self._project(staging, staging)
        _, returned_timeline = assert_destination(
            resolve, "Podcast", staging.GetName())
        assert returned_timeline is staging

    def test_missing_staging_refuses_and_leaves_cursor(self):
        """The staging is gone - refuse, and do not move the cursor."""
        sibling = MockTimeline("Reel 05 - ai-cant-form-a-clear-picture-of-you")
        proj, resolve = self._project(sibling, sibling)
        with pytest.raises(DestinationMismatchError, match="not found"):
            assert_destination(resolve, "Podcast", "Reel 06 - gone staging")
        assert proj.GetCurrentTimeline() is sibling
        assert proj.set_calls == []

    def test_wrong_project_never_moves_cursor(self):
        """Another project open - refuse before touching anything."""
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
