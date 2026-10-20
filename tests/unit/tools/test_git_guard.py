"""Cleanup refuses git-tracked files and foreign work trees.

Defect named: on 2026-10-19 a stale-media sweep removed 84 git-TRACKED
PNGs, fixtures and templates from the full-suite pool's git clone
(firstmate's home, data/ren-upgrade-program/pool-scratch/g2303) and
failed ~22 suite tests (restored with `git checkout -- .`). Nothing in
any Ren cleanup lane proved a candidate was not git-tracked or outside
a foreign work tree. These tests pin the shared guard every deletion
and quarantine path routes through: a tracked file refuses as
git-tracked, an untracked file inside a foreign work tree refuses as a
foreign work tree, a symlink into one is judged as its target, and an
owned scratch file with no work tree is still collectable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from library.tools import git_guard

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="needs a git binary")


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        encoding="utf-8", timeout=60)


@pytest.fixture
def foreign_clone(tmp_path: Path) -> Path:
    """A git work tree Ren does not own, with one tracked file."""
    clone = tmp_path / "foreign-clone"
    clone.mkdir()
    _run("init", cwd=clone)
    _run("config", "user.email", "guard@example.com", cwd=clone)
    _run("config", "user.name", "git guard", cwd=clone)
    tracked = clone / "tracked.png"
    tracked.write_bytes(b"pixels")
    _run("add", "tracked.png", cwd=clone)
    _run("commit", "-m", "tracked fixture", cwd=clone)
    return clone


def test_tracked_file_refuses_as_git_tracked(
        foreign_clone: Path, tmp_path: Path) -> None:
    owned = tmp_path / "scratch"
    owned.mkdir()
    hit = git_guard.check(foreign_clone / "tracked.png",
                          owned_roots=[str(owned)])
    assert hit is not None
    assert hit.reason == git_guard.GIT_TRACKED
    assert hit.resolved_path == str(foreign_clone / "tracked.png")


def test_untracked_file_inside_foreign_work_tree_refuses(
        foreign_clone: Path, tmp_path: Path) -> None:
    owned = tmp_path / "scratch"
    owned.mkdir()
    candidate = foreign_clone / "fresh-render.mov"
    candidate.write_bytes(b"render")
    hit = git_guard.check(candidate, owned_roots=[str(owned)])
    assert hit is not None
    assert hit.reason == git_guard.FOREIGN_WORK_TREE
    assert hit.work_tree == str(foreign_clone)


def test_symlink_into_foreign_work_tree_is_judged_as_its_target(
        foreign_clone: Path, tmp_path: Path) -> None:
    owned = tmp_path / "scratch"
    owned.mkdir()
    link = owned / "linked.png"
    os.symlink(foreign_clone / "tracked.png", link)
    hit = git_guard.check(link, owned_roots=[str(owned)])
    assert hit is not None
    assert hit.reason == git_guard.GIT_TRACKED
    assert hit.resolved_path == str(foreign_clone / "tracked.png")


def test_owned_scratch_file_with_no_work_tree_is_collectable(
        tmp_path: Path) -> None:
    owned = tmp_path / "scratch"
    owned.mkdir()
    candidate = owned / "scratch-render.mov"
    candidate.write_bytes(b"render")
    assert git_guard.check(
        candidate, owned_roots=[str(owned)]) is None
