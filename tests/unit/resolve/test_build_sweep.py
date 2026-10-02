"""The sweep that runs on every build, and the four things it must not do.

History: docs/evidence/resolve_test_history.md#test_build_sweep.
"""
from __future__ import annotations
import json
import os
import sqlite3
import sys
import pytest
from pathlib import Path
from library.tools import resolve_bin_layout as bins
from library.tools.execution import retire_empty_bins as retire
from library.tools.proof_cleanup import (
    CAPTAINS_PROOF_TIMELINE,
    FINAL_REEL_TIMELINE,
    MERGE_DEMO_TIMELINE,
    PRE_REBUILD_BACKUP_TIMELINE,
    PROTECTED_TIMELINES,
    SUPERSEDED_PLAIN_TIMELINE,
    SUPERSEDED_REACTION_TIMELINE,
    SUPERSEDED_REEL_TIMELINES,
    ProofRemovalRefused,
    is_authorised_superseded,
    plan_proof_removal,
    plan_superseded_removal,
)
from library.tools.resolve_organization import (
    Artefact,
    plan_dead_render_bins,
    plan_retirements,
    render_bin_census,
)
from tests.resolve_double import (
    FakeProject,
    FakeTimeline,
    make_pool_clip,
    place_clip,
)
from library.tools.execution import prune_orphans
from library.tools.orphan_removal import (
    ALREADY_GONE,
    DELETE,
    KEEP_SHARED,
    RemovalRefused,
    assert_file_unreferenced,
    assert_instruments_agree,
    assert_removable,
    files_to_delete,
    plan_removal,
    summarise,
)
import ast
import subprocess
from library.tools.versions import store as bvc


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import build_sweep
from library.tools import caption_asset_gc as gc
from library.tools.resolve_organization import (
    BIN_SUBTITLES,
    BIN_UNPLACED,
    is_spent_render_bin,
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


# ------------------------------------------------------ 4. idempotence


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


# --------------------------------------------------------------------------
# From test_dead_render_bins.py
#
# Dead per-reel bins retire with their contents - and nothing else does.
#
# The captain's third report of the same bins (2026-09-10): one reel shows
# up twice under both `06 - Subtitle renders` and `07 - Motion graphics`.
# Per-reel bins are keyed by placing-timeline NAME, every variant and
# every superseded build is a new timeline name, and timelines are never
# deleted - so one reel is N names is N leaves, and the sweep could only
# ever retire EMPTY legacy shells.  Every build made it worse.
#
# The rule: a per-reel leaf under a render bin that names NO live
# timeline is dead whether or not it holds files, and it retires WITH
# its contents - proven unplaced and pipeline-generated, journalled
# with what it held.  Every gate here is proven in BOTH directions
# (AGENTS.md 10.4): a retirement that must happen and one that must
# not, on the same shape.

MASTER = "GEO Podcast - Synced"
LIVE = "Reel 09 - your-website-is-only-20-percent (final)"
DEAD = "Reel 09 - your-website-is-only-20-percent (rebuild staging)"


@pytest.fixture
def project_root(tmp_path):
    """A disposable project root: the holds lock takes `makedirs`
    under it, and the generated file paths below are judged
    pipeline-made by prefix against it - so both must share one
    tmp_path root, never a hardcoded absolute path."""
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return str(root)


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=item_id, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def timeline(item_id, name, *, folder=()):
    return Artefact(item_id=item_id, name=name, kind="timeline",
                    file_path="", placed_by=(),
                    folder_path=tuple(folder))


def generated(project_root, name):
    return (f"{project_root}/pipeline_output/steps/"
            f"4_05_render_subtitles/{name}")


def dead_pool(project_root):
    """The captain's screenshot in miniature: a dead leaf under 06
    holding two unplaced renders, beside the live reel's own leaf."""
    return [
        timeline("t-master", MASTER),
        timeline("t-live", LIVE,
                 folder=(bins.REELS_BIN, "Current plan")),
        clip("c-dead-a", "sub_dead_a.mov", path=generated(project_root, "a.mov"),
              folder=(bins.SUBTITLES_BIN, DEAD)),
        clip("c-dead-b", "sub_dead_b.mov", path=generated(project_root, "b.mov"),
             folder=(bins.SUBTITLES_BIN, DEAD)),
        clip("c-live", "sub_live.mov",
             path=generated(project_root, "live.mov"), placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, LIVE)),
    ]


def tree_of(artefacts):
    known = set()
    for a in artefacts:
        folder = tuple(a.folder_path)
        for depth in range(1, len(folder) + 1):
            known.add(folder[:depth])
    known.discard(())
    return sorted(known)


def names(plan):
    return ["/".join(e["path"]) for e in plan]


# ------------------------------------------------------- the pure plan


def test_a_dead_leaf_retires_with_its_contents_listed(project_root):
    dead, declined = plan_dead_render_bins(
        dead_pool(project_root), tree_of(dead_pool(project_root)), project_root)
    assert names(dead) == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert declined == []
    entry = dead[0]
    assert entry["kind"] == "dead_render_bin"
    assert sorted(c["item_id"] for c in entry["contents"]) == [
        "c-dead-a", "c-dead-b"]
    assert all(c["file_path"] for c in entry["contents"])
    assert DEAD in entry["why"] and "no timeline" in entry["why"]


def test_a_leaf_naming_a_live_timeline_stays(project_root):
    artefacts = dead_pool(project_root)
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), project_root)
    assert f"{bins.SUBTITLES_BIN}/{LIVE}" not in names(dead)
    assert declined == []


def test_a_dead_leaf_holding_a_placed_clip_is_declined(project_root):
    artefacts = dead_pool(project_root) + [
        clip("c-still", "sub_still.mov", path=generated(project_root, "still.mov"),
             placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, DEAD)),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), project_root)
    assert dead == []
    assert ["/".join(d["path"]) for d in declined] == [
        f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert "still placed" in declined[0]["why"]


def test_the_unplaced_leaf_never_retires(project_root):
    artefacts = [
        timeline("t-master", MASTER),
        clip("c-u", "sub_u.mov", path=generated(project_root, "u.mov"),
             folder=(bins.SUBTITLES_BIN, bins.UNPLACED_BIN)),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), project_root)
    assert dead == [] and declined == []


def test_an_emptied_dead_leaf_retires_as_a_shell(project_root):
    artefacts = [timeline("t-master", MASTER)]
    tree = [(bins.SUBTITLES_BIN,), (bins.SUBTITLES_BIN, DEAD)]
    dead, _declined = plan_dead_render_bins(artefacts, tree, project_root)
    assert names(dead) == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert dead[0]["contents"] == []


def test_the_census_says_retire_with_contents_and_reports_declined(project_root):
    artefacts = dead_pool(project_root) + [
        clip("c-still", "sub_still.mov", path=generated(project_root, "still.mov"),
             placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, "Reel 09 - another-gone")),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), project_root)
    text = render_bin_census(artefacts, tree_of(artefacts), dead,
                             declined=declined)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in text
    assert "RETIRE with 2 item(s)" in text
    assert "declined" in text and "still placed" in text


# ------------------------------------------------- the proof removal


def proof_pool(project_root):
    """All four protected timelines plus the master present, the proof
    timeline live, and its caption bin holding one unplaced render."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-proof", CAPTAINS_PROOF_TIMELINE,
                 folder=(bins.REELS_BIN, bins.REELS_PROOF_BIN)),
        clip("c-proof", "proof_cap.mov", path=generated(project_root, "p.mov"),
             folder=(bins.SUBTITLES_BIN, "SOP Proof_captions")),
    ]
    for i, name in enumerate(sorted(PROTECTED_TIMELINES)):
        artefacts.append(timeline(f"t-p{i}", name,
                                  folder=(bins.REELS_BIN, "Current plan")))
    return artefacts


def test_the_proof_path_refuses_every_protected_name_and_the_master(project_root):
    artefacts = proof_pool(project_root)
    for name in list(PROTECTED_TIMELINES) + [MASTER]:
        with pytest.raises(ProofRemovalRefused):
            plan_proof_removal(
                artefacts, tree_of(artefacts), timeline_name=name,
                bin_names=[], project_root=project_root,
                master_name=MASTER)


def test_the_proof_path_refuses_an_absent_timeline(project_root):
    artefacts = [a for a in proof_pool(project_root) if a.name != CAPTAINS_PROOF_TIMELINE]
    with pytest.raises(ProofRemovalRefused, match="no timeline"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE, bin_names=[],
            project_root=project_root, master_name=MASTER)


def test_the_proof_path_refuses_a_bin_holding_placed_material(project_root):
    artefacts = proof_pool(project_root) + [
        clip("c-placed", "sub_x.mov", path=generated(project_root, "x.mov"),
             placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, "SOP Proof_captions")),
    ]
    with pytest.raises(ProofRemovalRefused, match="still placed"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE,
            bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_captions"],
            project_root=project_root, master_name=MASTER)


def test_the_proof_plan_takes_contents_placed_only_on_the_doomed_timeline(project_root):
    """The live case: the proof caption is placed on the proof
    timeline going down with it.  Anything a survivor still plays
    still refuses."""
    artefacts = proof_pool(project_root) + [
        clip("c-doomed", "still_780x480.png", path=generated(project_root, "s.png"),
             placed_by=[CAPTAINS_PROOF_TIMELINE],
             folder=(bins.SUBTITLES_BIN, "SOP Proof_captions")),
        clip("c-shared", "shared.mov", path=generated(project_root, "shared.mov"),
             placed_by=[CAPTAINS_PROOF_TIMELINE, LIVE],
             folder=(bins.SUBTITLES_BIN, "SOP Proof_shared")),
    ]
    plan = plan_proof_removal(
        artefacts, tree_of(artefacts),
        timeline_name=CAPTAINS_PROOF_TIMELINE,
        bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_captions"],
        project_root=project_root, master_name=MASTER)
    assert sorted(c["item_id"] for c in plan["bins"][0]["contents"]) == [
        "c-doomed", "c-proof"]
    with pytest.raises(ProofRemovalRefused, match="still placed"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE,
            bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_shared"],
            project_root=project_root, master_name=MASTER)


def demo_pool(project_root):
    """The crew merge-demo artefact: exact timeline name, exact-named
    caption bin, protected timelines present."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-demo", MERGE_DEMO_TIMELINE,
                 folder=(bins.REELS_BIN, "Current plan")),
        clip("c-demo", "demo_cap.mov", path=generated(project_root, "d.mov"),
             folder=(bins.SUBTITLES_BIN, MERGE_DEMO_TIMELINE)),
    ]
    for i, name in enumerate(sorted(PROTECTED_TIMELINES)):
        artefacts.append(timeline(f"t-p{i}", name,
                                  folder=(bins.REELS_BIN, "Current plan")))
    return artefacts


# --------------------------------------- the positioning-lane scratch


POSITIONING_NAMES = [
    "Reel 09 - your-website-is-only-20-percent (positioning-proof)",
    "Reel 09 - your-website-is-only-20-percent (positioning-proof-6)",
    "Reel 09 - your-website-is-only-20-percent (positioning-freshprobe)",
    "Reel 09 - your-website-is-only-20-percent (positioning-instant)",
]


def scratch_pool():
    """The cleanup brief in miniature: the lane's scratch timelines sit
    in Source footage with no caption bins of their own, the expired
    pre-rebuild backup beside the protected timelines."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-backup", PRE_REBUILD_BACKUP_TIMELINE,
                 folder=(bins.REELS_BIN, "Unrecorded")),
    ]
    for i, name in enumerate(POSITIONING_NAMES):
        artefacts.append(timeline(f"t-s{i}", name,
                                  folder=("Source footage",)))
    for i, name in enumerate(sorted(PROTECTED_TIMELINES)):
        artefacts.append(timeline(f"t-p{i}", name,
                                  folder=(bins.REELS_BIN, "Current plan")))
    return artefacts


# ----------------------------------- the two superseded Reel 09 timelines


def superseded_pool(project_root):
    """The captain's screenshot in miniature: `(final)` and the master
    stay; the plain superseded reel and the reaction-cutaway variant go,
    each with its own per-reel leaf under 06 and 07 holding only media
    placed on the doomed timeline - plus one caption `(final)` still
    plays, which must refuse."""
    artefacts = [
        timeline("t-master", MASTER,
                 folder=("04 - Master",)),
        timeline("t-final", FINAL_REEL_TIMELINE,
                 folder=(bins.REELS_BIN, "Unrecorded")),
        timeline("t-plain", SUPERSEDED_PLAIN_TIMELINE,
                 folder=(bins.REELS_BIN, "Current plan")),
        timeline("t-reaction", SUPERSEDED_REACTION_TIMELINE,
                 folder=(bins.REELS_BIN, "Unrecorded")),
        clip("c-plain-sub", "sub_plain.mov",
             path=generated(project_root, "plain.mov"),
             placed_by=[SUPERSEDED_PLAIN_TIMELINE],
             folder=(bins.SUBTITLES_BIN, SUPERSEDED_PLAIN_TIMELINE)),
        clip("c-plain-mg", "mg_plain.mov",
             path=generated(project_root, "plain_mg.mov"),
             placed_by=[SUPERSEDED_PLAIN_TIMELINE],
             folder=(bins.MOTION_GRAPHICS_BIN, SUPERSEDED_PLAIN_TIMELINE)),
        clip("c-reaction-sub", "sub_reaction.mov",
             path=generated(project_root, "reaction.mov"),
             placed_by=[SUPERSEDED_REACTION_TIMELINE],
             folder=(bins.SUBTITLES_BIN, SUPERSEDED_REACTION_TIMELINE)),
        clip("c-reaction-mg", "vox_reaction.mov",
             path=generated(project_root, "reaction_mg.mov"),
             placed_by=[SUPERSEDED_REACTION_TIMELINE],
             folder=(bins.MOTION_GRAPHICS_BIN, SUPERSEDED_REACTION_TIMELINE)),
        clip("c-final-sub", "sub_final.mov",
             path=generated(project_root, "final.mov"),
             placed_by=[FINAL_REEL_TIMELINE],
             folder=(bins.SUBTITLES_BIN, FINAL_REEL_TIMELINE)),
    ]
    return artefacts


def superseded_bins(doomed):
    return [f"{bins.SUBTITLES_BIN}/{doomed}",
            f"{bins.MOTION_GRAPHICS_BIN}/{doomed}"]


def test_the_superseded_names_are_exact_and_kept_is_final():
    assert SUPERSEDED_REEL_TIMELINES == frozenset([
        SUPERSEDED_PLAIN_TIMELINE, SUPERSEDED_REACTION_TIMELINE])
    assert FINAL_REEL_TIMELINE == LIVE
    assert is_authorised_superseded(SUPERSEDED_PLAIN_TIMELINE)
    assert is_authorised_superseded(SUPERSEDED_REACTION_TIMELINE)
    assert not is_authorised_superseded(FINAL_REEL_TIMELINE)
    assert not is_authorised_superseded(MASTER)
    assert not is_authorised_superseded(SUPERSEDED_PLAIN_TIMELINE + " 2")


def test_the_superseded_path_refuses_the_kept_and_the_master(project_root):
    artefacts = superseded_pool(project_root)
    for name in [FINAL_REEL_TIMELINE, MASTER]:
        with pytest.raises(ProofRemovalRefused):
            plan_superseded_removal(
                artefacts, tree_of(artefacts), timeline_name=name,
                bin_names=[], project_root=project_root,
                master_name=MASTER)


def test_the_superseded_path_refuses_a_bin_final_still_plays(project_root):
    """The shared-media case: a caption `(final)` still plays refuses,
    even inside the doomed timeline's own leaf."""
    artefacts = superseded_pool(project_root) + [
        clip("c-shared", "shared.mov", path=generated(project_root, "shared.mov"),
             placed_by=[SUPERSEDED_PLAIN_TIMELINE, FINAL_REEL_TIMELINE],
             folder=(bins.SUBTITLES_BIN, SUPERSEDED_PLAIN_TIMELINE)),
    ]
    with pytest.raises(ProofRemovalRefused, match="still placed"):
        plan_superseded_removal(
            artefacts, tree_of(artefacts),
            timeline_name=SUPERSEDED_PLAIN_TIMELINE,
            bin_names=[f"{bins.SUBTITLES_BIN}/{SUPERSEDED_PLAIN_TIMELINE}"],
            project_root=project_root, master_name=MASTER)


# ------------------------------------------------------- the executor


def _clip_2(uid, name, path):
    return make_pool_clip(name, path=path, uid=uid, clip_type="Video + Audio")


def _reel(proj, name, *played):
    """A project timeline playing ``played`` - not filed in the pool."""
    reel = proj.adopt(FakeTimeline(name))
    for clip in played:
        place_clip(reel, clip, 0, 47)
    return reel


def live_pool(project_root):
    """Fake pool shaped like the captain's: a dead leaf with two
    unplaced renders, a live leaf with one placed render."""
    proj = FakeProject("Fake")
    pool = proj.GetMediaPool()
    six = pool.AddSubFolder(pool.GetRootFolder(), bins.SUBTITLES_BIN)
    dead = pool.AddSubFolder(six, DEAD)
    dead.add_clip(_clip_2("c-dead-a", "sub_dead_a.mov",
                        generated(project_root, "a.mov")))
    dead.add_clip(_clip_2("c-dead-b", "sub_dead_b.mov",
                        generated(project_root, "b.mov")))
    live = pool.AddSubFolder(six, LIVE)
    live_clip = live.add_clip(_clip_2("c-live", "sub_live.mov",
                                    generated(project_root, "live.mov")))
    pool.SetCurrentFolder(pool.GetRootFolder())
    _reel(proj, LIVE, live_clip)
    return proj


def pool_bins(proj):
    out = []

    def walk(folder, path):
        for sub in folder.GetSubFolderList():
            out.append("/".join(path + (sub.GetName(),)))
            walk(sub, path + (sub.GetName(),))
    walk(proj.GetMediaPool().GetRootFolder(), ())
    return sorted(out)


def pool_clip_names(proj):
    out = []

    def walk(folder):
        out.extend(c.GetName() for c in folder.GetClipList())
        for sub in folder.GetSubFolderList():
            walk(sub)
    walk(proj.GetMediaPool().GetRootFolder())
    return sorted(out)


def test_retiring_a_dead_leaf_removes_its_items_then_the_bin(tmp_path, project_root):
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool(project_root)
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=project_root)
    assert names(plan) == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    journal_path = str(tmp_path / "retire.json")
    result = retire.retire_bins(proj, plan, journal_path,
                                project_root=project_root)
    assert result["retired"] == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert result["removed_items"] == 2
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" not in pool_bins(proj)
    assert f"{bins.SUBTITLES_BIN}/{LIVE}" in pool_bins(proj)
    assert pool_clip_names(proj) == ["sub_live.mov"]
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    held = journal["retired"][0]["contents"]
    assert sorted(c["file_path"] for c in held) == sorted(
        [generated(project_root, "a.mov"), generated(project_root, "b.mov")])


def test_a_second_run_plans_nothing(tmp_path, project_root):
    """Idempotence: the tree derives from the timelines that exist, so
    once the dead leaf is gone there is nothing left to derive."""
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool(project_root)
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=project_root)
    retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                       project_root=project_root)
    fresh, _, _, _ = read_pool(proj)
    again = plan_retirements(fresh, list(retire.read_bin_tree(proj)),
                             project_root=project_root)
    assert again == []


def test_a_dead_leaf_that_gained_a_placement_between_plan_and_apply_refuses(
        tmp_path, project_root):
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool(project_root)
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=project_root)
    # Between plan and apply the captain cuts a dead render back onto
    # the live reel: the fresh read places it, and the dead proof fails.
    root = proj.GetMediaPool().GetRootFolder()
    six = next(s for s in root.GetSubFolderList()
               if s.GetName() == bins.SUBTITLES_BIN)
    dead = next(s for s in six.GetSubFolderList() if s.GetName() == DEAD)
    _reel(proj, LIVE, dead.GetClipList()[0])
    with pytest.raises(retire.RetirementRefused, match="no longer proves dead"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                           project_root=project_root)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in pool_bins(proj)


def test_contents_calling_themselves_clips_but_reading_as_timelines_refuse(
        tmp_path, project_root):
    """The DeleteClips failure mode, aimed at on purpose: a pool item
    the plan read as a clip reports Type Timeline at the moment of
    the call - the pool changed mid-run - and the run refuses."""
    from library.tools.execution.organise_media_pool import read_pool

    proj = live_pool(project_root)
    root = proj.GetMediaPool().GetRootFolder()
    six = next(s for s in root.GetSubFolderList()
               if s.GetName() == bins.SUBTITLES_BIN)
    dead = next(s for s in six.GetSubFolderList() if s.GetName() == DEAD)
    changer = dead.add_clip(_clip_2("c-changing", "changing.mov",
                                  generated(project_root, "changing.mov")))
    reads = {"type": 0}
    read = changer.GetClipProperty

    def changing(key=None):
        # Clip on every read the planner makes, a timeline at the
        # moment of the call: the pool changed mid-run.
        if key == "Type":
            reads["type"] += 1
            return "Timeline" if reads["type"] > 2 else "Video + Audio"
        return read(key)

    changer.GetClipProperty = changing
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=project_root)
    with pytest.raises(retire.RetirementRefused, match="Timeline"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                           project_root=project_root)
    assert "changing.mov" in pool_clip_names(proj)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in pool_bins(proj)


def test_remove_proof_deletes_the_timeline_its_bin_and_nothing_else(tmp_path, project_root):
    from library.tools.execution import remove_proof
    proj = FakeProject("Fake")
    pool = proj.GetMediaPool()
    root = pool.GetRootFolder()
    reels = pool.AddSubFolder(root, bins.REELS_BIN)
    pool.AddSubFolder(reels, bins.REELS_PROOF_BIN)  # becomes current
    pool.next_timeline = FakeTimeline(CAPTAINS_PROOF_TIMELINE,
                                      pool_uid="t-proof")
    pool.CreateEmptyTimeline(CAPTAINS_PROOF_TIMELINE)
    six = pool.AddSubFolder(root, bins.SUBTITLES_BIN)
    cap_bin = pool.AddSubFolder(six, "SOP Proof_min-canvas-rail")
    cap_bin.add_clip(_clip_2("c-p", "proof_cap.mov",
                           generated(project_root, "p.mov")))
    live = pool.AddSubFolder(six, LIVE)
    live_clip = live.add_clip(_clip_2("c-live", "sub_live.mov",
                                    generated(project_root, "live.mov")))
    pool.SetCurrentFolder(root)
    _reel(proj, LIVE, live_clip)
    plan = {
        "timeline": {"item_id": "t-proof", "name": CAPTAINS_PROOF_TIMELINE,
                     "folder": f"{bins.REELS_BIN}/{bins.REELS_PROOF_BIN}"},
        "bins": [{
            "path": (bins.SUBTITLES_BIN, "SOP Proof_min-canvas-rail"),
            "why": "test",
            "contents": [{"item_id": "c-p", "name": "proof_cap.mov",
                          "file_path": generated(project_root, "p.mov"),
                          "folder": "test"}],
        }],
        "verified_untouched": [{"name": n} for n in PROTECTED_TIMELINES],
    }
    journal_path = str(tmp_path / "proof.json")
    result = remove_proof.remove_proof(proj, plan, journal_path)
    assert result["timeline"] == CAPTAINS_PROOF_TIMELINE
    assert result["removed_items"] == 2  # the timeline and its caption
    assert result["bins"] == [
        f"{bins.SUBTITLES_BIN}/SOP Proof_min-canvas-rail"]
    assert pool_clip_names(proj) == ["sub_live.mov"]
    assert CAPTAINS_PROOF_TIMELINE not in pool_clip_names(proj)
    # DeleteClips on the timeline's pool item deleted the timeline.
    assert proj.names() == [LIVE]
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    assert journal["timeline"]["name"] == CAPTAINS_PROOF_TIMELINE
    assert journal["removed_items"][0]["kind"] == "timeline"


# ----------------------------------------------- the apply-path hold-back


def test_apply_holds_dead_contents_back_and_retires_bin_with_them(tmp_path):
    """End to end on fakes: the filing pass must NOT re-home a dead
    reel's renders to Unplaced first - they leave the pool with the
    bin, and the result says what was held."""
    from library.tools.execution import organise_media_pool as ex
    gen = str(tmp_path / "pipeline_output" / "steps" /
              "4_05_render_subtitles")
    proj = FakeProject("Fake")
    pool = proj.GetMediaPool()
    root = pool.GetRootFolder()
    pool.CreateEmptyTimeline(MASTER)
    live_reel = pool.CreateEmptyTimeline(LIVE)
    six = pool.AddSubFolder(root, bins.SUBTITLES_BIN)
    dead = pool.AddSubFolder(six, DEAD)
    dead.add_clip(_clip_2("c-dead-a", "sub_dead_a.mov", f"{gen}/a.mov"))
    live_clip = root.add_clip(_clip_2("c-live", "sub_live.mov",
                                    f"{gen}/live.mov"))
    pool.SetCurrentFolder(root)
    place_clip(live_reel, live_clip, 0, 47)
    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "plan_provenance.json").write_text(json.dumps({
        "plan_content_hash": "b" * 64,
        "built_at": "2026-09-10T00:00:00+00:00",
        "built_reels": [LIVE],
    }), encoding="utf-8")

    result = ex.organise_project(proj, str(tmp_path), MASTER, apply=True)
    assert result["applied"]
    where = {}

    def walk(folder, path=()):
        for clip in folder.GetClipList():
            where[clip.GetName()] = "/".join(path)
        for sub in folder.GetSubFolderList():
            walk(sub, path + (sub.GetName(),))
    walk(proj.GetMediaPool().GetRootFolder())
    # The dead render never touched Unplaced: it left with its bin.
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" not in pool_bins(proj)
    assert "sub_dead_a.mov" not in where
    assert where["sub_live.mov"] == f"{bins.SUBTITLES_BIN}/{LIVE}"
    assert [h["name"] for h in result["held_for_dead_sweep"]] == [
        "sub_dead_a.mov"]
    assert result["retirement"]["retired"] == [
        f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert result["retirement"]["removed_items"] == 1


# --------------------------------- the planner/executor agreement (D1)
#
# The canary batch found the two disagreeing: `plan_dead_render_bins`
# emitted an emptied leaf with `contents: []` while `retire_bins`
# refuses anything its shell rule (scheme-or-canonical, both
# vocabulary-guarded) cannot take.  The planner was the wrong side -
# for an empty subtree every artefact gate is vacuously true, so
# "names no live timeline" was the whole proof, and it holds for
# every captain's bin by construction - so the planner declines
# vocabulary-free leaves instead of emitting them, and the two agree
# by construction.


def test_a_vocabulary_free_leaf_is_declined_never_emitted(project_root):
    """`my picks` names no live timeline and holds nothing, and is
    still not dead: from the pool alone it is indistinguishable from
    the captain's, so the sweep cannot prove the pipeline made it."""
    artefacts = [timeline("t-master", MASTER)]
    tree = [(bins.SUBTITLES_BIN,),
            (bins.SUBTITLES_BIN, "my picks")]
    dead, declined = plan_dead_render_bins(artefacts, tree, project_root)
    assert dead == []
    assert ["/".join(d["path"]) for d in declined] == [
        f"{bins.SUBTITLES_BIN}/my picks"]
    assert "vocabulary" in declined[0]["why"]


def test_a_hand_made_vocabulary_free_entry_still_refuses(tmp_path, project_root):
    """The guard that stops this code deleting something live: a plan
    entry no rule proves dead refuses, even with empty contents -
    the executor never trusts the plan's claim."""
    proj = live_pool(project_root)
    plan = [{"path": (bins.SUBTITLES_BIN, "my picks"),
             "kind": "dead_render_bin",
             "why": "test",
             "contents": []}]
    with pytest.raises(retire.RetirementRefused,
                       match="not a pipeline legacy bin"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                           project_root=project_root)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in pool_bins(proj)


def test_a_vocabulary_free_entry_with_contents_fails_its_re_proof(
        tmp_path, project_root):
    """The same guard through the dead-leaf path: the fresh re-proof
    runs the fixed planner, which declines the leaf, so the run
    refuses with 'no longer proves dead' instead of deleting it."""
    proj = FakeProject("Fake")
    pool = proj.GetMediaPool()
    six = pool.AddSubFolder(pool.GetRootFolder(), bins.SUBTITLES_BIN)
    picks = pool.AddSubFolder(six, "my picks")
    picks.add_clip(_clip_2("c-pick", "sub_pick.mov",
                         generated(project_root, "pick.mov")))
    pool.SetCurrentFolder(pool.GetRootFolder())
    plan = [{"path": (bins.SUBTITLES_BIN, "my picks"),
             "kind": "dead_render_bin",
             "why": "test",
             "contents": [{"item_id": "c-pick", "name": "sub_pick.mov",
                           "file_path": generated(project_root, "pick.mov"),
                           "folder": f"{bins.SUBTITLES_BIN}/my picks"}]}]
    with pytest.raises(retire.RetirementRefused,
                       match="no longer proves dead"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                           project_root=project_root)
    assert f"{bins.SUBTITLES_BIN}/my picks" in pool_bins(proj)
    assert "sub_pick.mov" in pool_clip_names(proj)


# --------------------------------------------------------------------------
# From test_orphan_removal.py
#
# What may be removed from a media pool, and what may never be.
#
# Every gate here is proven in BOTH directions - it must refuse the unsafe
# case and it must PASS the correct one.  A gate that cannot fail reads as
# coverage and a gate that fails correct output is the same defect from
# the other side (AGENTS.md 10.4), and this module's failure mode is a
# deleted timeline.

ROOT = "/tmp/project"


def clip_2(name, *, placed=(), path=None, item_id=None):
    return Artefact(
        item_id=item_id or f"id-{name}",
        name=name,
        kind="clip",
        file_path=path if path is not None else f"{ROOT}/pipeline_output/{name}.mov",
        placed_by=tuple(placed),
        folder_path=("Reel subtitles",))


def timeline_2(name):
    return Artefact(item_id=f"tl-{name}", name=name, kind="timeline",
                    file_path="", placed_by=(), folder_path=())


# --------------------------------------------- the timeline gate, both ways


def test_every_removal_gate_refuses_the_unsafe_case_and_passes_the_safe_one():
    """DeleteClips on a timeline's pool item DELETES THE TIMELINE and
    returns True: one wrong entry must stop all 1,216. A placed item
    stops the set too; two instruments that disagree refuse; a file a
    timeline plays refuses at the moment of deletion. And in the other
    direction, correct output is not refused."""
    with pytest.raises(RemovalRefused) as refused:
        assert_removable([clip_2("a"), timeline_2("Reel 03"), clip_2("b")])
    assert "DELETES THE TIMELINE" in str(refused.value)
    assert "Reel 03" in str(refused.value)
    with pytest.raises(RemovalRefused) as refused:
        assert_removable([clip_2("a"), clip_2("b", placed=("Reel 07",))])
    assert "placed on a timeline" in str(refused.value)
    assert assert_removable([clip_2("a"), clip_2("b"), clip_2("c")]) is None

    with pytest.raises(RemovalRefused) as refused:
        assert_instruments_agree({"/a.mov", "/b.mov"}, {"/a.mov"})
    assert "disagree" in str(refused.value)
    assert "/b.mov" in str(refused.value)
    assert assert_instruments_agree({"/a.mov"}, {"/a.mov"}) is None
    assert assert_instruments_agree(set(), set()) is None

    with pytest.raises(RemovalRefused) as refused:
        assert_file_unreferenced("/x.mov", {"/x.mov"})
    assert "played by a timeline" in str(refused.value)
    assert assert_file_unreferenced("/x.mov", {"/y.mov"}) is None


# ------------------------------------------------------------ the plan


def test_the_plan_separates_the_three_outcomes_for_a_file():
    """delete / keep_shared / already_gone are three different answers
    and a count that blurred them would read as permission."""
    shared = f"{ROOT}/pipeline_output/shared.mov"
    artefacts = [
        clip_2("gone", path=f"{ROOT}/pipeline_output/gone.mov"),
        clip_2("live", path=f"{ROOT}/pipeline_output/live.mov"),
        clip_2("dup", path=shared, item_id="dup"),
        clip_2("placed-dup", path=shared, item_id="placed", placed=("Reel 01",)),
        timeline_2("Reel 01"),
        clip_2("outside", path="/elsewhere/camera.mxf"),
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
    plan = plan_removal([clip_2("x", path=path)], ROOT,
                        saved_placed_paths={path}, sizes={path: 10})
    assert [r.file_state for r in plan] == [KEEP_SHARED]
    plan = plan_removal([clip_2("x", path=path)], ROOT,
                        saved_placed_paths=set(), sizes={path: 10})
    assert [r.file_state for r in plan] == [DELETE]


def test_a_clip_outside_the_project_is_never_removed():
    """Source footage came from outside a run and is not this pipeline's
    to remove, however unplaced it is."""
    plan = plan_removal([clip_2("cam", path="/elsewhere/LC4930.MXF")], ROOT,
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

    # An unreadable database refuses rather than returning nothing.
    import sqlite3
    other = tmp_path / "Other.db"
    con = sqlite3.connect(other)
    con.execute("create table Something (x text)")
    con.commit()
    con.close()
    with pytest.raises(RemovalRefused) as refused:
        prune_orphans.placed_paths_from_database(str(other))
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

    # An empty journal refuses rather than deleting nothing quietly.
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

    # The other direction: correct input is actually deleted.
    victim = tmp_path / "victim.mov"
    victim.write_bytes(b"z" * 1234)
    journal.write_text(json.dumps({"removed": []}), encoding="utf-8")
    record = prune_orphans.delete_files([str(victim)], set(), str(journal))
    assert record["deleted"] == [str(victim)]
    assert record["bytes_freed"] == 1234
    assert not victim.exists()
    assert json.loads(journal.read_text(encoding="utf-8"))["files"]["deleted"]


# --------------------------------------------------------------------------
# From test_build_commits_its_own_tail.py
#
# A reel build leaves its project store CLEAN.
#
# History: docs/evidence/resolve_test_history.md#test_build_commits_its_own_tail.

REPO = Path(__file__).resolve().parents[3]


def _function(name):
    tree = ast.parse((REPO / "library" / "processes" / "reels"
                      / "run_reels.py").read_text(encoding="utf-8"))
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_the_build_command_closes_its_own_record():
    """The closing commit exists, and it is OUTSIDE the node loop.

    Inside the loop it would commit before the next node's state write
    and reproduce the defect one node later.
    """
    body = _function("run")
    calls = [n for n in ast.walk(body)
             if isinstance(n, ast.Call)
             and ast.unparse(n).startswith("commit_run_tail")]
    assert calls, (
        "the reels runner no longer closes its own version-control "
        "record, so every reel build ends with the state write it just "
        "made uncommitted")
    # The loop over nodes must not contain it.
    loops = [n for n in ast.walk(body) if isinstance(n, ast.For)]
    for loop in loops:
        assert not any(
            ast.unparse(n).startswith("commit_run_tail")
            for n in ast.walk(loop) if isinstance(n, ast.Call)), (
            "the closing commit moved inside the node loop, where it "
            "runs before the next node's state write - which is the "
            "ordering defect it exists to close")
    # And it must come AFTER the state write it completes.
    writes = [n.lineno for n in ast.walk(body) if isinstance(n, ast.Call)
              and ast.unparse(n).startswith("record_project_output")]
    assert writes and min(c.lineno for c in calls) > max(writes)


def test_the_tail_commit_lands_what_the_final_state_write_left(tmp_path):
    """The defect, reproduced and closed, on a real repository.

    The node's own commit runs first and is clean-ended; then the
    runner writes the last node's output, exactly as `run_reels.run`
    does - and the tail commit is what stops the store ending dirty.
    """
    from library.processes.reels import run_reels

    bvc.init_project_repo(str(tmp_path))
    (tmp_path / "pipeline_data.json").write_text(
        '{"capability_outputs": {"reel.build": {}}}', encoding="utf-8")
    first = bvc.commit_build(str(tmp_path), "reels build: Reel 13")
    assert first["committed"] is True

    # The runner's final state write, after the node committed.
    (tmp_path / "pipeline_data.json").write_text(
        '{"capability_outputs": {"reel.build": {}, "reel.verify": '
        '{"reel_verification": {"organised": {"moved": 1}}}}}',
        encoding="utf-8")
    assert _porcelain(tmp_path), "the state write left nothing to commit"

    run_reels.commit_run_tail(str(tmp_path))

    assert _porcelain(tmp_path) == "", (
        "the reel build ended with an uncommitted project store, which "
        "is how a hand edit stops being visible as one")
    assert "organised" in subprocess.run(
        ["git", "show", "HEAD:pipeline_data.json"], cwd=str(tmp_path),
        capture_output=True, text=True, encoding="utf-8",
        check=False).stdout


def test_a_build_that_changed_nothing_makes_no_commit(tmp_path):
    """A no-op build produces no spurious commit - `commit_build`'s own
    rule, which the closing call must not talk it out of."""
    from library.processes.reels import run_reels

    bvc.init_project_repo(str(tmp_path))
    bvc.commit_build(str(tmp_path), "reels build: Reel 13")
    before = _head(tmp_path)
    run_reels.commit_run_tail(str(tmp_path))
    assert _head(tmp_path) == before


def _porcelain(root) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=str(root),
                          capture_output=True, text=True,
                          encoding="utf-8", check=False).stdout.strip()


def _head(root) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                          capture_output=True, text=True,
                          encoding="utf-8", check=False).stdout.strip()
