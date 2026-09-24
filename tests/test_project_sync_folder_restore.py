"""H10 - the project manager's folder is global, and resolve_project_sync
moves it and never puts it back.

After it runs, anything that resolves a project by folder is looking in
the wrong place - including the captain in the UI.

These tests prove:
- the folder is restored after successful navigation
- the folder is restored when the body RAISES
- FolderRestoreError is importable and documents the hazard
- the folder is not changed when no folder is specified in config

All tests use mock objects - no Resolve writes.
"""
import sys
from unittest.mock import MagicMock

import pytest

from library.tools.resolve_project_sync import (
    FolderRestoreError,
    ensure_resolve_project,
)


# ── Mock helpers ────────────────────────────────────────────────

class MockProjectManager:
    """Tracks folder navigation and verifies restore."""

    def __init__(self, *, current_folder="MyFolder", project_list=None,
                 current_project=None, load_fails=False):
        self._root = True  # whether we're at root
        self._current_folder = current_folder
        self._folder_history = []  # track all folder operations
        self._project_list = project_list or []
        self._current_project = current_project
        self._load_fails = load_fails
        self.goto_root_count = 0
        self.open_folder_calls = []
        self.create_folder_calls = []

    def GetCurrentFolder(self):
        return self._current_folder

    def GotoRootFolder(self):
        self.goto_root_count += 1
        self._root = True
        return True

    def OpenFolder(self, name):
        self.open_folder_calls.append(name)
        self._root = False
        return True  # always succeeds for test simplicity

    def CreateFolder(self, name):
        self.create_folder_calls.append(name)
        return True

    def GetCurrentProject(self):
        return self._current_project

    def GetProjectListInCurrentFolder(self):
        return self._project_list

    def LoadProject(self, name):
        if self._load_fails:
            return None
        proj = MagicMock()
        proj.GetName.return_value = name
        return proj

    def CreateProject(self, name):
        proj = MagicMock()
        proj.GetName.return_value = name
        return proj

    def ExportProject(self, name, path, include_media):
        return True


class MockResolve:
    def __init__(self, pm):
        self._pm = pm

    def GetProjectManager(self):
        return self._pm


def _make_config(*, project_name="TestProject", folder="Client/SubFolder",
                 resolution=None, fps=None):
    """Build a minimal ProjectConfig-like object for testing."""
    config = MagicMock()
    config.resolve.project_name = project_name
    config.resolve.folder = folder
    config.name = project_name
    config.source.resolution = resolution
    config.source.fps = fps
    return config


# ── H10 Tests ──────────────────────────────────────────────────

class TestFolderRestoreOnSuccess:
    """The project manager folder is restored after successful navigation."""

    def test_folder_restored_after_load(self, monkeypatch):
        """After navigating to find a project, the folder is restored."""
        pm = MockProjectManager(
            current_folder="OriginalFolder",
            project_list=["TestProject"],
        )
        resolve = MockResolve(pm)

        # Patch _get_resolve to return our mock
        monkeypatch.setattr(
            "library.tools.resolve_project_sync._get_resolve",
            lambda: resolve,
        )

        config = _make_config(folder="Client/SubFolder")
        result = ensure_resolve_project(config)

        assert result["success"]
        # Folder must be restored: GotoRootFolder then OpenFolder(saved)
        # The last GotoRootFolder + OpenFolder should be the restore.
        assert pm.goto_root_count >= 2, (
            "GotoRootFolder was not called for restore - the folder is "
            "left pointing at Client/SubFolder (H10)"
        )
        assert pm.open_folder_calls[-1] == "OriginalFolder", (
            f"Folder was not restored to 'OriginalFolder', last open was "
            f"{pm.open_folder_calls[-1]!r}"
        )


class TestFolderRestoreOnException:
    """The folder is restored even when the body raises an exception."""

    def test_folder_restored_when_load_raises(self, monkeypatch):
        """If LoadProject raises, the folder is still restored."""
        pm = MockProjectManager(
            current_folder="SafeFolder",
            project_list=["TestProject"],
        )

        # Make LoadProject raise
        original_load = pm.LoadProject
        def exploding_load(name):
            raise RuntimeError("Simulated LoadProject explosion")
        pm.LoadProject = exploding_load

        resolve = MockResolve(pm)
        monkeypatch.setattr(
            "library.tools.resolve_project_sync._get_resolve",
            lambda: resolve,
        )

        config = _make_config(folder="Client/SubFolder")

        with pytest.raises(RuntimeError, match="Simulated LoadProject explosion"):
            ensure_resolve_project(config)

        # Folder MUST be restored even though we raised
        assert pm.open_folder_calls[-1] == "SafeFolder", (
            "Folder was NOT restored when the body raised - "
            "this is the bug H10 exists to prevent"
        )


class TestNoFolderNavigation:
    """When no folder is specified, no folder state is borrowed."""

    def test_no_folder_no_navigation(self, monkeypatch):
        """When config has no folder, GotoRootFolder is never called."""
        pm = MockProjectManager(
            current_folder="UntouchedFolder",
            project_list=["TestProject"],
        )
        resolve = MockResolve(pm)
        monkeypatch.setattr(
            "library.tools.resolve_project_sync._get_resolve",
            lambda: resolve,
        )

        config = _make_config(folder="")  # no folder
        result = ensure_resolve_project(config)

        assert result["success"]
        assert pm.goto_root_count == 0, (
            "GotoRootFolder was called when no folder navigation was needed"
        )


class TestFolderRestoreErrorDocumentation:
    """The error class exists and documents the hazard."""


    def test_folder_restore_error_docstring_mentions_h10(self):
        assert "H10" in FolderRestoreError.__doc__

