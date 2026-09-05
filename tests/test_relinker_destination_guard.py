"""H12 - relink_project rewrites the media pool of whatever project is open.

The function's project_slug argument was used only to build path
mappings - it was never checked against the open project's name.  Run
the relinker for project A while project B is open and B's media pool
is rewritten.

These tests prove:
- correct project passes verification
- wrong project is REFUSED (DestinationMismatchError)
- no project open is refused when expected_project is set
- backward compatibility: empty expected_project does not refuse
- re-verification happens immediately before the first mutation
- DestinationMismatchError documents the hazard

All tests use mock objects - no Resolve writes.
"""
import os
import sys

import pytest

from library.tools.resolve_relinker import (
    DestinationMismatchError,
    relink_project,
    scan_offline_clips,
)


# ── Mock helpers ────────────────────────────────────────────────

class MockClip:
    def __init__(self, name, file_path, exists=False):
        self._name = name
        self._file_path = file_path
        self._exists = exists
        self.replaced_with = None

    def GetName(self):
        return self._name

    def GetClipProperty(self, key):
        if key == "File Path":
            return self._file_path
        return ""

    def ReplaceClip(self, new_path):
        self.replaced_with = new_path
        return True


class MockFolder:
    def __init__(self, clips=None, subfolders=None):
        self._clips = clips or []
        self._subfolders = subfolders or []

    def GetClipList(self):
        return self._clips

    def GetSubFolderList(self):
        return self._subfolders


class MockMediaPool:
    def __init__(self, root_folder=None):
        self._root = root_folder or MockFolder()

    def GetRootFolder(self):
        return self._root


class MockProject:
    def __init__(self, name, media_pool=None):
        self._name = name
        self._mp = media_pool or MockMediaPool()

    def GetName(self):
        return self._name

    def GetMediaPool(self):
        return self._mp


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


# ── H12 Tests ──────────────────────────────────────────────────

class TestRelinkerDestinationGuard:
    """Writes verify their destination and refuse on mismatch."""

    def test_correct_project_passes(self, monkeypatch):
        """When expected_project matches, the relink proceeds."""
        project = MockProject("MyProject")
        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        # No offline clips, so it returns early after verification
        result = relink_project(
            expected_project="MyProject",
        )
        assert result["success"]

    def test_wrong_project_refused(self, monkeypatch):
        """When expected_project does not match, refuse with error."""
        project = MockProject("CaptainsRoughCut")
        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        with pytest.raises(DestinationMismatchError, match="CaptainsRoughCut"):
            relink_project(expected_project="PipelineProject")

    def test_no_project_open_refused(self, monkeypatch):
        """When no project is open and expected_project is set, refuse."""
        pm = MockProjectManager(project=None)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        with pytest.raises(DestinationMismatchError, match="No Resolve project"):
            relink_project(expected_project="SomeProject")

    def test_no_expected_project_backward_compatible(self, monkeypatch):
        """When expected_project is empty, no verification occurs."""
        project = MockProject("AnyProject")
        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        # Should not raise - backward compatible behavior
        result = relink_project(expected_project="")
        assert result["success"]

    def test_no_project_open_no_expected_returns_error_dict(self, monkeypatch):
        """When no project is open and no expected, returns error dict."""
        pm = MockProjectManager(project=None)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )

        result = relink_project(expected_project="")
        assert not result["success"]
        assert "No project open" in result["error"]


class TestRelinkerPreMutationVerification:
    """The project is re-verified immediately before the first mutation."""

    def test_project_changed_between_scan_and_mutation(self, monkeypatch, tmp_path):
        """If the project changes after scan but before mutation, refuse."""
        # Create a clip that appears offline
        clip = MockClip("clip1.mov", "/old/path/clip1.mov")
        folder = MockFolder(clips=[clip])
        media_pool = MockMediaPool(root_folder=folder)

        project = MockProject("PipelineProject", media_pool=media_pool)

        # Track calls to GetName and switch after enough calls for the
        # first verification + scan to pass.  The function calls GetName
        # at the initial check, then again immediately before mutation.
        call_count = [0]
        original_name = "PipelineProject"
        switched_name = "CaptainsProject"

        def changing_project_name():
            call_count[0] += 1
            # Switch on the last call (the pre-mutation re-verification)
            if call_count[0] > 1:
                return switched_name
            return original_name

        project.GetName = changing_project_name

        pm = MockProjectManager(project)
        resolve = MockResolve(pm)

        monkeypatch.setattr(
            "library.tools.resolve_relinker._get_resolve",
            lambda: resolve,
        )
        monkeypatch.setattr(
            "library.tools.resolve_relinker.build_path_mappings",
            lambda slug: [("/old/path/", str(tmp_path) + "/")],
        )

        # Create the file at the new path so the clip is fixable
        new_clip = tmp_path / "clip1.mov"
        new_clip.write_bytes(b"fake")

        with pytest.raises(DestinationMismatchError, match="Project changed"):
            relink_project(
                expected_project="PipelineProject",
            )


class TestRelinkerDestinationMismatchErrorDocs:
    """The error class documents the hazard."""

    def test_error_exists(self):
        assert issubclass(DestinationMismatchError, RuntimeError)

    def test_error_docstring_mentions_h12(self):
        assert "H12" in DestinationMismatchError.__doc__

    def test_error_docstring_mentions_media_pool(self):
        assert "media pool" in DestinationMismatchError.__doc__
