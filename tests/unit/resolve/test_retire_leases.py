"""Bin retirement declares the bins it deletes.

`retire_empty_bins.retire_bins` deletes bins (and dead contents) with
no undo; a prune deleting, or a filing moving, under the same bins
mid-run collides with it. Every test names a defect it catches - a
case that fails on origin/main, where retirement takes no lease.
"""
import threading
import time

import pytest

from library.tools.execution.retire_empty_bins import retire_bins
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)
from tests.resolve_double import make_project

TOP = "06 - Subtitle renders"
LEAF = "Reel 09 - old"
BIN_PATH = f"{TOP}/{LEAF}"


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


def _project_with_empty_leaf():
    project = make_project("Podcast")
    pool = project.GetMediaPool()
    top = pool.AddSubFolder(pool.GetRootFolder(), TOP)
    pool.AddSubFolder(top, LEAF)
    pool.SetCurrentFolder(pool.GetRootFolder())
    return project


def _retirements():
    return [{"path": [TOP, LEAF], "contents": []}]


def _bin_paths(project):
    root = project.GetMediaPool().GetRootFolder()
    out = []

    def walk(folder, path):
        for sub in folder.GetSubFolderList() or ():
            out.append("/".join(path + (sub.GetName(),)))
            walk(sub, path + (sub.GetName(),))

    walk(root, ())
    return sorted(out)


def test_retiring_an_empty_leaf_removes_the_bin(lock_dir, tmp_path):
    """The wired path still retires: the empty leaf goes, journaled.

    Defect caught: a lease wrapped around the wrong section (or a
    declaration that refuses its own write) turns retirement into a
    refusal, and dead bins accumulate.
    """
    project = _project_with_empty_leaf()
    result = retire_bins(project, _retirements(),
                         str(tmp_path / "retire.json"))
    assert result["retired"] == [BIN_PATH]
    assert BIN_PATH not in _bin_paths(project)
    assert TOP in _bin_paths(project)


def test_retirement_waits_for_a_prune_on_the_same_bin(
        lock_dir, tmp_path):
    """A bin delete and a pool delete under it serialise: retire waits.

    Defect caught: with no declaration the retirement deletes a bin a
    prune is deleting items under - the bin goes while its contents
    are being proven. On origin/main retirement returns before the
    holder releases.
    """
    project = _project_with_empty_leaf()
    prune = LeaseDeclaration(
        exclusive=frozenset({ResourceRef("media_pool_bin", BIN_PATH)}),
    )
    released_at: list = []

    def delete():
        with resource_lease(prune, "prune under the bin",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=delete)
    holder.start()
    time.sleep(0.3)  # let the prune acquire first
    result = retire_bins(project, _retirements(),
                         str(tmp_path / "retire.json"))
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the prune never released"
    assert returned_at >= released_at[0], \
        "retirement returned mid-prune"
    assert result["retired"] == [BIN_PATH]
    assert BIN_PATH not in _bin_paths(project)
