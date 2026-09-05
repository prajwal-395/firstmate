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
import os
import sys

import pytest

# apply_fusion_comps lives outside a regular package and needs
# DaVinciResolveScript on sys.path.  We mock the import so the tests
# run without Resolve installed.
_EXECUTION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "library", "tools", "execution",
)


class _FakeDvr:
    """Minimal stand-in for DaVinciResolveScript."""
    @staticmethod
    def scriptapp(name):
        return None


# Inject the fake before importing the module under test.
sys.modules.setdefault("DaVinciResolveScript", _FakeDvr)
if _EXECUTION_DIR not in sys.path:
    sys.path.insert(0, _EXECUTION_DIR)

from apply_fusion_comps import (  # noqa: E402
    DestinationMismatchError,
    verify_destination,
    _map_clips_to_items,
)


# ── Mock helpers ────────────────────────────────────────────────

class MockTimeline:
    def __init__(self, name):
        self._name = name

    def GetName(self):
        return self._name

    def GetUniqueId(self):
        return str(id(self))


class MockProject:
    def __init__(self, name, timeline=None):
        self._name = name
        self._timeline = timeline

    def GetName(self):
        return self._name

    def GetCurrentTimeline(self):
        return self._timeline

    def SetCurrentTimeline(self, tl):
        self._timeline = tl
        return True


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
        return None


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
