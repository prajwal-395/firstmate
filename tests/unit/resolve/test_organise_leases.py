"""Pool filing declares the items it files, both directions.

`organise_media_pool.apply_plan` re-homes pool items and `revert`
moves them back; a prune deleting an item mid-filing, or a swap
replacing one, collides with either. Every test names a defect it
catches - a case that fails on origin/main, where filing takes no
lease.
"""
import threading
import time

import pytest

from library.tools.execution.organise_media_pool import apply_plan, revert
from library.tools.resource_leases import (
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)
from library.tools.resolve_organization import Artefact, Plan, Verdict
from tests.resolve_double import make_pool_clip, make_project


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


def _filed_project(file_path="/foot/a.mov", item_id="pool:a.mov"):
    project = make_project("Podcast")
    clip = make_pool_clip("a.mov", path=file_path, uid=item_id)
    project.GetMediaPool().GetRootFolder().add_clip(clip)
    artefacts = [Artefact(
        item_id=item_id, name="a.mov", kind="clip", file_path=file_path,
        placed_by=(), folder_path=("Master",))]
    return project, artefacts


def _plan(item_id="pool:a.mov"):
    return Plan(root_bin="Master", moves=[Verdict(
        item_id=item_id, name="a.mov", kind="clip",
        destination=("Master", "Filed"), why="t", state=None)])


def _bins(project):
    root = project.GetMediaPool().GetRootFolder()
    out = {}

    def walk(folder, path=()):
        for clip in folder.GetClipList() or ():
            out[clip.GetName()] = "/".join(path)
        for sub in folder.GetSubFolderList() or ():
            walk(sub, path + (sub.GetName(),))

    walk(root)
    return out


def test_filing_moves_the_item_and_journals_it(lock_dir, tmp_path):
    """The wired path still files: the item lands in its bin, journaled.

    Defect caught: a lease wrapped around the wrong section (or a
    declaration that refuses its own write) turns filing into a
    refusal, and the pool stays unfiled.
    """
    project, artefacts = _filed_project()
    journal = apply_plan(project, _plan(), artefacts,
                         str(tmp_path / "journal.json"))
    assert _bins(project) == {"a.mov": "Master/Filed"}
    assert journal["moves"][0]["to"] == "Master/Filed"
    assert journal["moves"][0]["from"] == "Master"


def test_filing_waits_for_a_swap_on_the_same_pool_file(
        lock_dir, tmp_path):
    """A file and a replace on one pool item serialise: filing waits.

    Defect caught: with no declaration the filing pass re-homes an
    item a swap is replacing mid-pass, and the swap's re-point lands
    in a bin it never addressed. On origin/main filing returns before
    the holder releases.
    """
    project, artefacts = _filed_project()
    swap = LeaseDeclaration(
        write=frozenset({ResourceRef("clip", "/foot/a.mov")}),
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
    journal = apply_plan(project, _plan(), artefacts,
                         str(tmp_path / "journal.json"))
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the swap never released"
    assert returned_at >= released_at[0], \
        "filing returned mid-swap"
    assert _bins(project) == {"a.mov": "Master/Filed"}
    assert len(journal["moves"]) == 1


def test_filing_an_item_with_no_file_declares_it_by_id(
        lock_dir, tmp_path):
    """A plan entry naming no file is still a declaration, by id.

    Defect caught: declaring every move by file path refuses the whole
    filing where one artefact carries no file - an unnamed ref matches
    nothing, so the declaration falls back to the item id rather than
    refusing work that is safe to fence.
    """
    project = make_project("Podcast")
    clip = make_pool_clip("b.mov", path="", uid="pool:b.mov")
    project.GetMediaPool().GetRootFolder().add_clip(clip)
    artefacts = [Artefact(
        item_id="pool:b.mov", name="b.mov", kind="clip", file_path="",
        placed_by=(), folder_path=("Master",))]
    plan = Plan(root_bin="Master", moves=[Verdict(
        item_id="pool:b.mov", name="b.mov", kind="clip",
        destination=("Master", "Filed"), why="t", state=None)])
    journal = apply_plan(project, plan, artefacts,
                         str(tmp_path / "journal.json"))
    assert _bins(project) == {"b.mov": "Master/Filed"}
    assert len(journal["moves"]) == 1


def test_revert_moves_the_item_back(lock_dir, tmp_path):
    """The wired revert still undoes: the item returns, journal stamped.

    Defect caught: a lease wrapped around the wrong section turns the
    undo into a refusal, and a half-applied filing cannot be walked
    back.
    """
    project, artefacts = _filed_project()
    journal_path = str(tmp_path / "journal.json")
    apply_plan(project, _plan(), artefacts, journal_path)
    assert _bins(project) == {"a.mov": "Master/Filed"}
    result = revert(project, journal_path)
    assert _bins(project) == {"a.mov": "Master"}
    assert result["moved_back"] == ["a.mov"]


def test_revert_waits_for_a_prune_on_the_same_item(
        lock_dir, tmp_path):
    """An undo and a delete on one item serialise: the revert waits.

    Defect caught: with no declaration the revert moves an item back
    while a prune deletes it - the move-back lands on an item that is
    gone. On origin/main the revert returns before the holder
    releases.
    """
    project, artefacts = _filed_project()
    journal_path = str(tmp_path / "journal.json")
    apply_plan(project, _plan(), artefacts, journal_path)
    prune = LeaseDeclaration(
        exclusive=frozenset({ResourceRef("clip", "pool:a.mov")}),
    )
    released_at: list = []

    def delete():
        with resource_lease(prune, "prune the pool item",
                           heartbeat_interval=30.0):
            time.sleep(2.0)
        released_at.append(time.monotonic())

    holder = threading.Thread(target=delete)
    holder.start()
    time.sleep(0.3)  # let the prune acquire first
    result = revert(project, journal_path)
    returned_at = time.monotonic()
    holder.join(30.0)
    assert released_at, "the prune never released"
    assert returned_at >= released_at[0], \
        "the revert returned mid-prune"
    assert result["moved_back"] == ["a.mov"]
