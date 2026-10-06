"""Pool-item deletion declares the items it deletes.

`prune_orphans.remove_pool_items` is the delete half the sweep cannot
undo, and a writer on the same pool files mid-pass - a swap replacing
one, a relink re-pointing one - collides with it. Every test names a
defect it catches - a case that fails on origin/main, where the prune
takes no lease.
"""
import json
import threading
import time
from pathlib import Path

import pytest

from library.tools.execution.prune_orphans import delete_files, remove_pool_items
from library.tools.orphan_removal import DELETE, Removal
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)
from library.tools.resolve_organization import Artefact
from tests.resolve_double import make_pool_clip, make_project


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


def _survey(file_path, item_id="pool:stale.mov"):
    artefact = Artefact(
        item_id=item_id, name="stale.mov", kind="clip",
        file_path=file_path, placed_by=(), folder_path=("Master",))
    removal = Removal(
        item_id=item_id, name="stale.mov", file_path=file_path,
        folder_path=("Master",), file_state=DELETE)
    return {"artefacts": [artefact], "plan": [removal]}


def _project_with(file_path, item_id="pool:stale.mov"):
    project = make_project("Podcast")
    clip = make_pool_clip("stale.mov", path=file_path, uid=item_id)
    project.GetMediaPool().GetRootFolder().add_clip(clip)
    return project


def _pool_names(project):
    return [clip.GetName() for clip in
            (project.GetMediaPool().GetRootFolder().GetClipList() or [])]


def test_a_prune_removes_the_item_and_journals_it(lock_dir, tmp_path):
    """The wired path still deletes: the item leaves the pool, named.

    Defect caught: a lease wrapped around the wrong section (or a
    declaration that refuses its own write) turns the sweep's delete
    into a refusal, and orphans accumulate.
    """
    file_path = str(tmp_path / "stale.mov")
    project = _project_with(file_path)
    journal = remove_pool_items(
        project, _survey(file_path), str(tmp_path / "journal.json"))
    assert _pool_names(project) == []
    assert [entry["name"] for entry in journal["removed"]] == ["stale.mov"]
    assert journal["removed"][0]["file_path"] == file_path


def test_a_prune_waits_for_a_swap_on_the_same_pool_file(
        lock_dir, tmp_path):
    """A delete and a replace on one file serialise: prune waits.

    Defect caught: with no declaration the prune deletes a pool item a
    swap is replacing mid-pass - the swap re-points an item that is
    gone, or the delete takes an item the swap just fixed. On
    origin/main the prune returns before the holder releases.
    """
    file_path = str(tmp_path / "stale.mov")
    project = _project_with(file_path)
    swap = LeaseDeclaration(
        write=frozenset({ResourceRef("clip", file_path)}),
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
    journal = remove_pool_items(
        project, _survey(file_path), str(tmp_path / "journal.json"))
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the swap never released"
    assert returned_at >= released_at[0], \
        "the prune returned mid-swap"
    assert _pool_names(project) == []
    assert len(journal["removed"]) == 1


def _journal(tmp_path):
    path = str(tmp_path / "journal.json")
    Path(path).write_text(json.dumps({"removed": []}), encoding="utf-8")
    return path


def test_file_deletion_unlinks_and_records(lock_dir, tmp_path):
    """The wired path still deletes: files go, the journal records them.

    Defect caught: a lease wrapped around the wrong section turns the
    file half of the sweep into a refusal, and orphaned files
    accumulate on disk.
    """
    first = tmp_path / "a.mov"
    second = tmp_path / "b.mov"
    first.write_bytes(b"a" * 8)
    second.write_bytes(b"b" * 4)
    record = delete_files([str(first), str(second)], set(),
                          _journal(tmp_path))
    assert record["deleted"] == [str(first), str(second)]
    assert record["bytes_freed"] == 12
    assert not first.exists() and not second.exists()


def test_file_deletion_waits_for_a_swap_on_the_same_path(
        lock_dir, tmp_path):
    """An unlink and a replace on one path serialise: deletion waits.

    Defect caught: with no declaration the file half unlinks a file a
    swap is re-pointing at mid-pass - the swap resurrects a path that
    is gone. On origin/main deletion returns before the holder
    releases.
    """
    target = tmp_path / "a.mov"
    target.write_bytes(b"a" * 8)
    swap = LeaseDeclaration(
        write=frozenset({ResourceRef("clip", str(target))}),
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
    record = delete_files([str(target)], set(), _journal(tmp_path))
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the swap never released"
    assert returned_at >= released_at[0], \
        "deletion returned mid-swap"
    assert record["deleted"] == [str(target)]
    assert not target.exists()
