"""Media-pool relinks declare the pool items they re-point.

`resolve_relinker.relink_project` rewrites pool items with no reversal
(H12 in the module), and a second writer on the same items mid-pass -
a swap, a prune, another relink - re-points under it. Every test names
a defect it catches - a case that fails on origin/main, where the
relink takes no lease.
"""
import threading
import time

import pytest

import library.tools.resolve_relinker as relinker
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import FakeResolve, make_pool_clip, make_project


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


@pytest.fixture
def sole_writer():
    with assume_sole_writer("test: fake pool has no instance to contend for"):
        yield


def _pool_with_offline(old_path):
    """A fake project whose pool holds one offline clip at `old_path`."""
    project = make_project("Podcast")
    clip = make_pool_clip("a.mov", path=old_path)
    project.GetMediaPool().GetRootFolder().add_clip(clip)
    return project, clip


def _wire(monkeypatch, project, old_prefix, new_prefix):
    monkeypatch.setattr(
        relinker, "_get_resolve", lambda: FakeResolve(project))
    monkeypatch.setattr(
        relinker, "build_path_mappings",
        lambda project_slug="": [(old_prefix, new_prefix)])


def test_a_relink_repairs_the_offline_clip(lock_dir, sole_writer,
                                           tmp_path, monkeypatch):
    """The wired path still relinks: the item re-points, counts name it.

    Defect caught: a lease wrapped around the wrong section (or a
    declaration that refuses its own write) turns the relink into a
    refusal, and offline clips stay offline.
    """
    old = str(tmp_path / "old" / "a.mov")  # absent: the clip is offline
    new_file = tmp_path / "new" / "a.mov"
    new_file.parent.mkdir(parents=True)
    new_file.write_bytes(b"not a real movie")
    project, clip = _pool_with_offline(old)
    _wire(monkeypatch, project, str(tmp_path / "old") + "/",
          str(tmp_path / "new") + "/")

    result = relinker.relink_project()

    assert result["relinked"] == 1
    assert result["failed"] == 0
    assert clip.replace_calls == [str(new_file)]


def test_a_relink_waits_for_a_writer_on_the_same_pool_item(
        lock_dir, sole_writer, tmp_path, monkeypatch):
    """Two writers, one pool item: the relink waits for the release.

    Defect caught: with no declaration the relink re-points an item a
    swap is replacing mid-pass, and the item ends up pointing where
    neither pass meant. On origin/main the relink returns before the
    holder releases.
    """
    old = str(tmp_path / "old" / "a.mov")
    new_file = tmp_path / "new" / "a.mov"
    new_file.parent.mkdir(parents=True)
    new_file.write_bytes(b"not a real movie")
    project, clip = _pool_with_offline(old)
    _wire(monkeypatch, project, str(tmp_path / "old") + "/",
          str(tmp_path / "new") + "/")
    swap = LeaseDeclaration(
        write=frozenset({ResourceRef("clip", old)}),
    )
    released_at: list = []

    def replace():
        with resource_lease(swap, "swap the pool file",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=replace)
    holder.start()
    time.sleep(0.3)  # let the swap acquire first
    result = relinker.relink_project()
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the swap never released"
    assert returned_at >= released_at[0], \
        "the relink returned mid-swap"
    assert result["relinked"] == 1
    assert clip.replace_calls == [str(new_file)]
