"""The sweep that runs on every build, and the four things it must not do.

The captain's complaint, verbatim: *"over several iterations there is a
lot of empty bins and clutter from multiple file references to the same
things (i thought you said you fixed this?"*. He was told wrong. What
had shipped was where NEW items get filed; nothing swept what a previous
iteration left behind, because every mechanism that could - the caption
mark/sweep, the empty-bin retirement, the pool prune - was reachable
only by hand.

Measured on geo-podcast before this landed: 308 files / 68 movs / 43
unique contents in one step directory, two empty `Reel 09 ... (j-cut)`
bins, 29 pool items filed under `Not placed on any timeline`, and 23
movs pinned LIVE by ledger entries naming a staging timeline the project
does not have.

Deleting is the dangerous half, so the gates are proven in BOTH
directions (AGENTS.md 10.4): a file that must go and a file that must
STAY on the same shape.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import build_sweep
from library.tools import caption_asset_gc as gc
from library.tools.resolve_organization import (
    BIN_SUBTITLES,
    BIN_UNPLACED,
    Artefact,
    is_spent_render_bin,
    plan_retirements,
)

MG_BIN = "07 - Motion graphics"
REEL_A = "Reel 09 - your-website-is-only-20-percent"
REEL_B = "Reel 09 - your-website-is-only-20-percent (reaction-cutaway)"
GONE = "Reel 09 - your-website-is-only-20-percent (j-cut)"


# ------------------------------------------------------------ fixtures


def _write(path, size=100):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"x" * size)
    return path


def _asset_dir(root):
    return os.path.join(root, "pipeline_output", "steps",
                        "4_05_render_subtitles")


def _database(path, placed, timelines):
    """A Project.db double carrying only what the roots read."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute('create table Sm2TiItem (Sm2TiItem_id text, '
                'MediaFilePath text)')
    con.execute('create table "Sm2Timeline" ("Sm2Timeline_id" text, '
                '"Name" text)')
    con.executemany("insert into Sm2TiItem values (?, ?)",
                    [(f"i{n}", p) for n, p in enumerate(placed)])
    con.executemany('insert into "Sm2Timeline" values (?, ?)',
                    [(f"t{n}", name) for n, name in enumerate(timelines)])
    con.commit()
    con.close()
    return path


def _ledger(asset_dir, entries):
    """Write the render ledger directly, in the shape the root reads."""
    path = gc.ledger_path_for(asset_dir)
    os.makedirs(asset_dir, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"subtitle_overlay": {"segments": entries},
                   "ledger_version": 1}, handle)
    return path


def _entry(asset_dir, name, timeline):
    return {"segment_id": name,
            "overlay_path": os.path.join(asset_dir, name + ".mov"),
            "binding": {"timeline": timeline},
            "provenance": "rendered"}


# ------------------------------------- 1. the shared-file case (PR 905)


def test_a_file_several_timelines_place_is_live_and_never_swept(tmp_path):
    """THE safety rule. PR 905 landed content-keyed sharing, so ONE file
    legitimately serves several reels by design. A sweep that read one
    timeline's ledger, or one build's output, would call it garbage.

    The union is across the WHOLE root set, so the shared file survives
    even though the ledger entry that named it is bound to a timeline
    that is GONE - the other timeline's placement is enough on its own.
    """
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    shared = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    lonely = _write(os.path.join(asset_dir, "sub_tl_a_2_3-4_bbbbbbbb.mov"))
    # The only record naming `shared` binds it to a timeline that is gone.
    _ledger(asset_dir, [_entry(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa", GONE),
                        _entry(asset_dir, "sub_tl_a_2_3-4_bbbbbbbb", GONE)])
    db = _database(os.path.join(project, "db", "Project.db"),
                   placed=[shared], timelines=[REEL_A, REEL_B])

    record = build_sweep.sweep_files(project, [db], apply=True)
    area = record["areas"][0]
    assert shared not in area["moved"], (
        "a file a live timeline places must never be swept, whatever the "
        "ledger says about it")
    assert os.path.exists(shared)
    assert lonely in area["moved"], (
        "a file no timeline places and no live record names is exactly "
        "what this sweep exists to move")
    assert not os.path.exists(lonely)


def test_a_dead_ledger_binding_stops_pinning_its_file(tmp_path):
    """The defect that made a hand-run sweep reclaim 15.7 MB of 584 MB.

    The ledger is a record of what was rendered. Once the timeline it
    was rendered for is gone, the entry records history and references
    nothing - but it was read as a root, so it pinned its mov LIVE for
    ever and the directory could only grow.
    """
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    orphan = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    _ledger(asset_dir, [_entry(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa", GONE)])
    db = _database(os.path.join(project, "db", "Project.db"),
                   placed=[], timelines=[REEL_A])

    roots = build_sweep.collect_roots(project, [db])
    result = gc.mark(project, asset_dir, roots)
    statuses = {a.path: a.status for a in result.assets}
    assert statuses[orphan] == gc.ORPHAN

    # And the other direction on the SAME shape: rebind it to a timeline
    # that DOES exist and it is live again.
    _ledger(asset_dir, [_entry(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa",
                               REEL_A)])
    roots = build_sweep.collect_roots(project, [db])
    result = gc.mark(project, asset_dir, roots)
    assert {a.path: a.status for a in result.assets}[orphan] == gc.LIVE


def test_an_unknown_timeline_set_keeps_every_ledger_entry(tmp_path):
    """No readable database means the live set is UNKNOWN, and unknown
    reads exactly like "no timelines at all". Narrowing the ledger then
    would condemn the whole directory, so nothing is narrowed."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    mov = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    _ledger(asset_dir, [_entry(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa", GONE)])
    roots = gc.collect_pipeline_roots(project, live_timelines=None)
    result = gc.mark(project, asset_dir, roots)
    assert {a.path: a.status for a in result.assets}[mov] == gc.LIVE


def test_rename_ledger_timelines_repoints_a_promoted_binding(tmp_path):
    """Promotion renames the staging timeline; the ledger must follow it
    or its entries name a timeline that does not exist."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    path = _ledger(asset_dir,
                   [_entry(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa",
                           "Reel 09 (rebuild staging)")])
    out = gc.rename_ledger_timelines(
        asset_dir, {"Reel 09 (rebuild staging)": REEL_A})
    assert out["renamed"] == 1
    with open(path, encoding="utf-8") as handle:
        segments = json.load(handle)["subtitle_overlay"]["segments"]
    assert segments[0]["binding"]["timeline"] == REEL_A


# --------------------------------------------------- 2. the refusal


def test_an_unreadable_root_refuses_the_whole_sweep(tmp_path):
    """A root that could not be read returns nothing, which reads
    exactly like a project with nothing on any timeline. That failure
    direction eats the captain's work, so the sweep REFUSES LOUDLY and
    moves nothing - not even in the areas that would have been fine."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    mov = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    missing = os.path.join(project, "no-such", "Project.db")

    with pytest.raises(build_sweep.SweepRefused) as refused:
        build_sweep.sweep_files(project, [missing], apply=True)
    assert "unreadable" in str(refused.value).lower()
    assert os.path.exists(mov), "a refusal must move nothing"


def test_a_candidate_that_became_referenced_refuses(tmp_path):
    """The mark is a claim about an earlier moment; the move happens in
    a later one. Anything that became referenced in between voids it."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    mov = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    db = _database(os.path.join(project, "db", "Project.db"),
                   placed=[], timelines=[REEL_A])
    roots = build_sweep.collect_roots(project, [db])
    result = gc.mark(project, asset_dir, roots)
    mark_path = os.path.join(project, "mark.json")
    result.write_json(mark_path)

    placed_now = _database(os.path.join(project, "db2", "Project.db"),
                           placed=[mov], timelines=[REEL_A])
    with pytest.raises(gc.SweepRefused) as refused:
        gc.sweep(mark_path, project_folder=project, db_paths=[placed_now])
    assert "became referenced" in str(refused.value)
    assert os.path.exists(mov)


# ---------------------------------------------- 3. the empty-bin collapse


def _clip(name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=name, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def test_a_spent_per_reel_bin_is_retired_and_a_busy_one_is_not():
    """The captain's empty bins, exactly. `Reel 09 ... (j-cut)` stands
    empty under BOTH render tops because that reel was rebuilt under a
    new name - and before `is_spent_render_bin` no rule could reach it,
    because the retirement only ever looked at the LEGACY tops."""
    artefacts = [
        _clip("a.mov", path="/p/a.mov", placed_by=(REEL_A,),
              folder=(BIN_SUBTITLES, REEL_A)),
        _clip("b.mov", path="/p/b.mov", folder=(MG_BIN, REEL_A)),
    ]
    tree = [(BIN_SUBTITLES,), (BIN_SUBTITLES, REEL_A),
            (BIN_SUBTITLES, GONE), (MG_BIN,), (MG_BIN, REEL_A),
            (MG_BIN, GONE)]
    retired = ["/".join(e["path"]) for e in plan_retirements(artefacts, tree)]
    assert f"{BIN_SUBTITLES}/{GONE}" in retired
    assert f"{MG_BIN}/{GONE}" in retired
    # The other direction on the same shape: a bin holding something stays.
    assert f"{BIN_SUBTITLES}/{REEL_A}" not in retired
    assert f"{MG_BIN}/{REEL_A}" not in retired


def test_the_render_tops_and_the_unplaced_leaf_are_never_retired():
    """Scaffolding `bins_to_create` stands up on every build, and a
    canonical DESTINATION. Retiring either is churn, not cleanup - the
    next build re-creates it immediately."""
    assert not is_spent_render_bin((BIN_SUBTITLES,))
    assert not is_spent_render_bin((MG_BIN,))
    assert not is_spent_render_bin((BIN_SUBTITLES, BIN_UNPLACED))
    assert not is_spent_render_bin((MG_BIN, BIN_UNPLACED))
    assert not is_spent_render_bin((BIN_SUBTITLES, GONE, "deeper"))
    # And the captain's own top-level bins, empty or not.
    assert not is_spent_render_bin(("my picks",))
    assert not is_spent_render_bin(("my picks", REEL_A))
    tree = [(BIN_SUBTITLES,), (BIN_SUBTITLES, BIN_UNPLACED), (MG_BIN,)]
    assert plan_retirements([], tree) == [] or all(
        "/".join(e["path"]) not in
        {BIN_SUBTITLES, MG_BIN, f"{BIN_SUBTITLES}/{BIN_UNPLACED}"}
        for e in plan_retirements([], tree))


# ------------------------------------------------------ 4. idempotence


def test_a_second_consecutive_sweep_removes_nothing_new(tmp_path):
    """The proof it will not eat live work. Run it twice with nothing
    changed in between: the first pass takes the superseded generation,
    the second finds no candidate at all and moves nothing."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    placed = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa.mov"))
    _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa_props.json"))
    stale = _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_bbbbbbbb.mov"))
    _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_bbbbbbbb_props.json"))
    _ledger(asset_dir, [_entry(asset_dir, "sub_tl_a_1_1-2_aaaaaaaa",
                               REEL_A)])
    db = _database(os.path.join(project, "db", "Project.db"),
                   placed=[placed], timelines=[REEL_A])

    first = build_sweep.sweep_files(project, [db], apply=True)["areas"][0]
    assert stale in first["moved"]
    assert first["moved_count"] == 2, "the mov and its props sibling"
    assert os.path.exists(placed)

    second = build_sweep.sweep_files(project, [db], apply=True)["areas"][0]
    assert second["moved"] == [], (
        "a second consecutive sweep with nothing changed must find "
        "nothing - anything else means it is eating live work")
    assert second["orphans"] == 0
    assert os.path.exists(placed)


def test_the_journal_names_what_regenerates_what_it_moved(tmp_path):
    """Removal is recoverable in the sense that matters here: every
    swept artefact is derivable by rebuilding, so the record says HOW."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_bbbbbbbb.mov"))
    _ledger(asset_dir, [])
    db = _database(os.path.join(project, "db", "Project.db"),
                   placed=[], timelines=[REEL_A])
    record = build_sweep.sweep_files(project, [db], apply=True)
    area = record["areas"][0]
    assert area["moved_count"] == 1
    assert "--rerun render_subtitles" in area["regenerate"]
    assert os.path.isfile(area["mark_path"])
    text = build_sweep.render_sweep({"applied": True, "files": record,
                                     "pool": {}, "bins": {}})
    assert "regenerate with:" in text


def test_the_swept_areas_are_only_derivable_render_outputs():
    """Never source footage, never a timeline, never `brand_assets/`,
    never `exports/`. Absent by construction rather than by a check that
    could be forgotten - so this asserts the enumeration itself."""
    from library.tools.project_layout import AREAS, Kind
    for area in build_sweep.SWEPT_AREAS:
        spec = AREAS[area]
        assert spec.kind is Kind.OUTPUT, (
            f"{area.value} is {spec.kind}, so its files are not "
            f"guaranteed derivable by a re-run and must not be swept")
        assert area in build_sweep.REGENERATE


def test_sweep_journals_never_share_a_path(tmp_path):
    """Two sweeps stamped in the same second journal twice, not once:
    this lane's verify-twice run landed its empty second pass on top
    of the first pass's 102-file record.  Same disambiguator
    `library/tools/execution/remove_proof.py` uses."""
    first = build_sweep.journal_path_for(
        str(tmp_path), when="20260910T221628Z")
    os.makedirs(os.path.dirname(first), exist_ok=True)
    with open(first, "w", encoding="utf-8") as handle:
        handle.write("{}")
    second = build_sweep.journal_path_for(
        str(tmp_path), when="20260910T221628Z")
    assert second != first
    assert second.endswith("_2.json")


def test_consecutive_sweep_marks_never_share_a_path(tmp_path):
    """Marks are written on every pass, orphans or not - so the second
    pass's mark must not land on the first pass's."""
    project = str(tmp_path)
    asset_dir = _asset_dir(project)
    _write(os.path.join(asset_dir, "sub_tl_a_1_1-2_bbbbbbbb.mov"))
    _ledger(asset_dir, [])
    db = _database(os.path.join(project, "db", "Project.db"),
                   placed=[], timelines=[REEL_A])
    first = build_sweep.sweep_files(project, [db], apply=True)["areas"][0]
    assert first["moved_count"] == 1
    second = build_sweep.sweep_files(project, [db], apply=True)["areas"][0]
    assert second["moved"] == []
    assert second["mark_path"] != first["mark_path"]
    assert os.path.isfile(first["mark_path"])
    assert os.path.isfile(second["mark_path"])
