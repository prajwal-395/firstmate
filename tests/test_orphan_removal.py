"""What may be removed from a media pool, and what may never be.

Every gate here is proven in BOTH directions - it must refuse the unsafe
case and it must PASS the correct one.  A gate that cannot fail reads as
coverage and a gate that fails correct output is the same defect from
the other side (AGENTS.md 10.4), and this module's failure mode is a
deleted timeline.
"""
from pathlib import Path

import pytest

from library.tools.execution import prune_orphans
from library.tools.orphan_removal import (
    ALREADY_GONE,
    DELETE,
    KEEP_SHARED,
    Removal,
    RemovalRefused,
    assert_file_unreferenced,
    assert_instruments_agree,
    assert_removable,
    files_to_delete,
    plan_removal,
    render_manifest,
    render_summary,
    summarise,
)
from library.tools.resolve_organization import Artefact

ROOT = "/tmp/project"


def clip(name, *, placed=(), path=None, item_id=None):
    return Artefact(
        item_id=item_id or f"id-{name}",
        name=name,
        kind="clip",
        file_path=path if path is not None else f"{ROOT}/pipeline_output/{name}.mov",
        placed_by=tuple(placed),
        folder_path=("Reel subtitles",))


def timeline(name):
    return Artefact(item_id=f"tl-{name}", name=name, kind="timeline",
                    file_path="", placed_by=(), folder_path=())


# --------------------------------------------- the timeline gate, both ways


def test_a_timeline_in_the_removal_set_refuses_the_whole_set():
    """DeleteClips on a timeline's pool item DELETES THE TIMELINE and
    returns True. One wrong entry must stop all 1,216."""
    with pytest.raises(RemovalRefused) as refused:
        assert_removable([clip("a"), timeline("Reel 03"), clip("b")])
    assert "DELETES THE TIMELINE" in str(refused.value)
    assert "Reel 03" in str(refused.value)


def test_the_timeline_gate_passes_a_set_of_plain_unplaced_clips():
    """The other direction: correct output must not be refused."""
    assert assert_removable([clip("a"), clip("b"), clip("c")]) is None


def test_a_placed_item_in_the_removal_set_refuses_the_whole_set():
    with pytest.raises(RemovalRefused) as refused:
        assert_removable([clip("a"), clip("b", placed=("Reel 07",))])
    assert "placed on a timeline" in str(refused.value)


# ------------------------------------------- the two instruments, both ways


def test_instruments_that_disagree_refuse():
    with pytest.raises(RemovalRefused) as refused:
        assert_instruments_agree({"/a.mov", "/b.mov"}, {"/a.mov"})
    assert "disagree" in str(refused.value)
    assert "/b.mov" in str(refused.value)


def test_instruments_that_agree_pass():
    assert assert_instruments_agree({"/a.mov"}, {"/a.mov"}) is None
    assert assert_instruments_agree(set(), set()) is None


# --------------------------------------------- the per-file gate, both ways


def test_a_referenced_file_refuses_at_the_moment_of_deletion():
    with pytest.raises(RemovalRefused) as refused:
        assert_file_unreferenced("/x.mov", {"/x.mov"})
    assert "played by a timeline" in str(refused.value)


def test_an_unreferenced_file_passes():
    assert assert_file_unreferenced("/x.mov", {"/y.mov"}) is None


# ------------------------------------------------------------ the plan


def test_the_plan_separates_the_three_outcomes_for_a_file():
    """delete / keep_shared / already_gone are three different answers
    and a count that blurred them would read as permission."""
    shared = f"{ROOT}/pipeline_output/shared.mov"
    artefacts = [
        clip("gone", path=f"{ROOT}/pipeline_output/gone.mov"),
        clip("live", path=f"{ROOT}/pipeline_output/live.mov"),
        clip("dup", path=shared, item_id="dup"),
        clip("placed-dup", path=shared, item_id="placed", placed=("Reel 01",)),
        timeline("Reel 01"),
        clip("outside", path="/elsewhere/camera.mxf"),
    ]
    sizes = {f"{ROOT}/pipeline_output/live.mov": 1000, shared: 500}
    plan = plan_removal(artefacts, ROOT, saved_placed_paths=set(), sizes=sizes)
    states = {r.name: r.file_state for r in plan}
    assert states == {"gone": ALREADY_GONE, "live": DELETE, "dup": KEEP_SHARED}
    assert files_to_delete(plan) == [f"{ROOT}/pipeline_output/live.mov"]
    counts = summarise(plan)
    assert counts == {"items": 3, "delete_files": 1, "keep_shared": 1,
                      "already_gone": 1, "bytes_freed": 1000,
                      "bytes_kept_shared": 500}


def test_the_saved_database_alone_can_make_a_file_shared():
    """The second instrument must be able to CHANGE the answer, or it is
    not an instrument - it is decoration."""
    path = f"{ROOT}/pipeline_output/only-in-db.mov"
    plan = plan_removal([clip("x", path=path)], ROOT,
                        saved_placed_paths={path}, sizes={path: 10})
    assert [r.file_state for r in plan] == [KEEP_SHARED]
    plan = plan_removal([clip("x", path=path)], ROOT,
                        saved_placed_paths=set(), sizes={path: 10})
    assert [r.file_state for r in plan] == [DELETE]


def test_a_clip_outside_the_project_is_never_removed():
    """Source footage came from outside a run and is not this pipeline's
    to remove, however unplaced it is."""
    plan = plan_removal([clip("cam", path="/elsewhere/LC4930.MXF")], ROOT,
                        saved_placed_paths=set())
    assert plan == []


# -------------------------------------------------------- what is written






# ------------------------------------------------- the database instrument


def _tiny_project_db(path: Path, placed: list[str]) -> None:
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("create table Sm2TiItem (Sm2TiItem_id text, "
                "MediaFilePath text)")
    con.executemany("insert into Sm2TiItem values (?, ?)",
                    [(str(i), p) for i, p in enumerate(placed)])
    con.commit()
    con.close()


def test_the_database_sweep_refuses_when_it_cannot_find_what_it_knows(tmp_path):
    """A sweep that reports nothing reads exactly like a project with an
    empty timeline, and would mark every file deletable."""
    db = tmp_path / "Project.db"
    _tiny_project_db(db, ["/a.mov"])
    assert prune_orphans.placed_paths_from_database(
        str(db), must_find="/a.mov") == {"/a.mov"}
    with pytest.raises(RemovalRefused) as refused:
        prune_orphans.placed_paths_from_database(str(db), must_find="/b.mov")
    assert "has not looked" not in str(refused.value)
    assert "does not report" in str(refused.value)


def test_an_unreadable_database_refuses_rather_than_returning_nothing(tmp_path):
    db = tmp_path / "Project.db"
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("create table Something (x text)")
    con.commit()
    con.close()
    with pytest.raises(RemovalRefused) as refused:
        prune_orphans.placed_paths_from_database(str(db))
    assert "reads exactly like" in str(refused.value)


def test_the_sweep_never_opens_the_original(tmp_path):
    """AGENTS.md 5: copy Project.db before opening it. Proven by making
    the original read-only - opening it read-write would raise."""
    db = tmp_path / "Project.db"
    _tiny_project_db(db, ["/a.mov"])
    db.chmod(0o444)
    try:
        assert prune_orphans.placed_paths_from_database(str(db)) == {"/a.mov"}
    finally:
        db.chmod(0o644)


# ------------------------------------------------------------- the digest


def test_the_digest_is_sensitive_and_stable(tmp_path):
    """A digest that hashed nothing would agree with itself forever."""
    import shutil
    import sqlite3

    db = tmp_path / "Project.db"
    con = sqlite3.connect(db)
    con.executescript("""
      create table Sm2Timeline (Sm2Timeline_id text, Name text, Sequence text);
      create table Sm2Sequence (Sm2Sequence_id text);
      create table Sm2SequenceContainer (Sm2SequenceContainer_id text,
                                         Sm2Sequence_id text);
      create table Sm2TiTrack (Sm2TiTrack_id text, Type int, SubType int,
                               Flags int, UserDefinedName text,
                               Sm2SequenceContainer_id text,
                               Sm2Sequence_id text);
      create table Sm2TiItem (Sm2TiItem_id text, Name text, Start text,
                              Duration text, "In" text, MediaFilePath text,
                              MediaStartTime real);
      create table Sm2TiItem_Sm2TiTrack (DbOwner text, DbAssociate text);
      insert into Sm2Timeline values ('t1', 'Master', 's1');
      insert into Sm2Timeline values ('t2', 'Reel 01', 's2');
      insert into Sm2Sequence values ('s1');
      insert into Sm2Sequence values ('s2');
      insert into Sm2TiTrack values ('k1', 1, 0, 0, '', null, 's1');
      insert into Sm2TiTrack values ('k2', 1, 0, 0, '', null, 's2');
      insert into Sm2TiItem values ('i1','a','0','10','0','/a.mov',0);
      insert into Sm2TiItem values ('i2','b','0','10','0','/b.mov',0);
      insert into Sm2TiItem_Sm2TiTrack values ('k1','i1');
      insert into Sm2TiItem_Sm2TiTrack values ('k2','i2');
    """)
    con.commit()
    con.close()

    before = prune_orphans.timeline_digests(str(db))
    assert set(before) == {"Master", "Reel 01"}
    assert "EMPTY" not in before.values()
    assert before["Master"] != before["Reel 01"], (
        "two different timelines share a digest - it is hashing nothing")

    copy = tmp_path / "copy.db"
    shutil.copy2(db, copy)
    assert prune_orphans.timeline_digests(str(copy)) == before

    con = sqlite3.connect(db)
    con.execute("update Sm2TiItem set Start='99' where Sm2TiItem_id='i1'")
    con.commit()
    con.close()
    after = prune_orphans.timeline_digests(str(db))
    assert after["Master"] != before["Master"], "the digest cannot fail"
    assert after["Reel 01"] == before["Reel 01"], (
        "an unrelated timeline moved - the digest is not per-timeline")


# ------------------------------------------------- the organiser still cannot


@pytest.mark.parametrize("module", [
    "library/tools/resolve_organization.py",
    "library/tools/execution/organise_media_pool.py",
])
def test_removal_did_not_leak_into_the_organiser(module):
    """Organising must still never delete. Adding a module that CAN
    delete is exactly the moment that rule is easiest to lose."""
    source = Path(module).read_text(encoding="utf-8")
    executable = "".join(source.split('"""')[::2])
    for call in ("DeleteClips(", "DeleteFolders(", "DeleteTimelines("):
        assert call not in executable, f"{module} calls {call}"




# --------------------------------------------- the file list after removal


def test_the_file_list_comes_from_the_journal_not_a_resurvey(tmp_path):
    """Once the pool items are gone a survey finds no orphans, so a
    re-survey would delete nothing while reporting success. The journal
    is the record of what was removed, so it is what names the files."""
    import json

    journal = tmp_path / "prune.json"
    journal.write_text(json.dumps({"removed": [
        {"name": "a", "file_path": "/a.mov", "file_state": DELETE},
        {"name": "b", "file_path": "/b.mov", "file_state": KEEP_SHARED},
        {"name": "c", "file_path": "/c.mov", "file_state": ALREADY_GONE},
    ]}), encoding="utf-8")
    assert prune_orphans.files_from_journal(str(journal)) == ["/a.mov"]


def test_an_empty_journal_refuses_rather_than_deleting_nothing_quietly(tmp_path):
    import json

    journal = tmp_path / "prune.json"
    journal.write_text(json.dumps({"removed": []}), encoding="utf-8")
    with pytest.raises(RemovalRefused) as refused:
        prune_orphans.files_from_journal(str(journal))
    assert "no removed pool item" in str(refused.value)


def test_deleting_stops_on_a_file_that_became_referenced(tmp_path):
    """It stops the whole run rather than skipping quietly, and the
    files it had already deleted are named in the journal."""
    import json

    first = tmp_path / "first.mov"
    second = tmp_path / "second.mov"
    first.write_bytes(b"x" * 10)
    second.write_bytes(b"y" * 10)
    journal = tmp_path / "prune.json"
    journal.write_text(json.dumps({"removed": []}), encoding="utf-8")

    with pytest.raises(RemovalRefused):
        prune_orphans.delete_files(
            [str(first), str(second)], {str(second)}, str(journal))
    assert not first.exists(), "the unreferenced file should have gone"
    assert second.exists(), "the referenced file must survive"


def test_deleting_removes_the_files_and_reports_what_it_freed(tmp_path):
    """The other direction: correct input must actually be deleted."""
    import json

    victim = tmp_path / "victim.mov"
    victim.write_bytes(b"z" * 1234)
    journal = tmp_path / "prune.json"
    journal.write_text(json.dumps({"removed": []}), encoding="utf-8")

    record = prune_orphans.delete_files([str(victim)], set(), str(journal))
    assert record["deleted"] == [str(victim)]
    assert record["bytes_freed"] == 1234
    assert not victim.exists()
    assert json.loads(journal.read_text(encoding="utf-8"))["files"]["deleted"]
