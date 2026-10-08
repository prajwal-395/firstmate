"""Shared fixtures for installed engine tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def packageable_native_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Give packaging tests deterministic native artifacts without a build.

    The packager's refusal for stale or missing release binaries remains
    covered through its real native-refresh status check. These tests only
    need to prove how fresh artifacts are copied into an engine tree.
    """
    from library.tools import native_refresh

    parts = (
        "ren-graphics-fframes",
        "resolve-control",
        "ren-resolved",
        "resolve-activity-overlay",
    )
    binaries = tmp_path / "native-stubs"
    binaries.mkdir()
    for part in parts:
        binary = binaries / part
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        binary.chmod(0o755)

    monkeypatch.setattr(
        native_refresh, "status", lambda _root, _part, _parts=None:
        ("fresh", "test fixture binary"))
    monkeypatch.setattr(
        native_refresh, "_binary_path",
        lambda _root, part, _parts=None: binaries / part)
