"""Retiring the migration's empty shells, and nothing else.

The live migration emptied the legacy bins and left them standing beside the
numbered scheme (`Reels`, `Reel subtitles`, `Subtitles`, ...). To the captain
that IS disorganised. The no-delete rule exists so nothing is ever stranded;
an EMPTY bin strands nothing, so retiring a provably empty legacy shell is
safe and leaving it is the complaint.

Every gate here is proven in BOTH directions (AGENTS.md 10.4): a retirement
that must happen and one that must not, on the same shape.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import resolve_bin_layout as bins
from library.tools.execution import retire_empty_bins as retire
from library.tools.resolve_organization import (
    BIN_REELS,
    BIN_SOURCE,
    BIN_SUBTITLES,
    Artefact,
)
from tests.resolve_double import make_project

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(
        item_id=item_id,
        name=name,
        kind="clip",
        file_path=path,
        placed_by=tuple(placed_by),
        folder_path=tuple(folder),
    )


def timeline(item_id, name, *, folder=()):
    return Artefact(
        item_id=item_id,
        name=name,
        kind="timeline",
        file_path="",
        placed_by=(),
        folder_path=tuple(folder),
    )


def names(plan):
    return ["/".join(e["path"]) for e in plan]


# ------------------------------------------------------- the pure plan


def test_an_emptied_legacy_top_is_retired():
    """The captain's complaint, literally: `Reels` and `Reel subtitles`
    stand empty beside `05 - Reels` and `06 - Subtitle renders`."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    tree = [
        ("Reels",),
        ("Reel subtitles",),
        ("Subtitles",),
        (BIN_REELS,),
        (BIN_SUBTITLES,),
    ]
    plan = plan_retirements(artefacts, tree)
    assert set(names(plan)) >= {"Reels", "Reel subtitles", "Subtitles"}
    assert all("why" in entry and entry["why"].strip() for entry in plan)


def test_a_legacy_bin_with_anything_inside_is_not_a_shell_and_stays():
    from library.tools.resolve_organization import plan_retirements

    artefacts = [
        timeline("t-master", MASTER),
        clip("c-cam", "cam.mov", path="/elsewhere/cam.mov", folder=("Subtitles",)),
    ]
    plan = plan_retirements(artefacts, [("Subtitles",), (BIN_SOURCE,)])
    assert "Subtitles" not in names(plan)


def test_a_shell_with_an_empty_sub_bin_under_it_is_still_empty():
    """The migration's own shape: `Reel subtitles` holds only its emptied
    per-reel leaves. Retire the leaves too, children before parents."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    tree = [
        ("Reel subtitles",),
        ("Reel subtitles", "Reel 01 - live"),
        ("Reel subtitles", "Not placed on any timeline"),
    ]
    plan = plan_retirements(artefacts, tree, timeline_names=["Reel 01 - live"])
    ordered = names(plan)
    assert set(ordered) == {
        "Reel subtitles",
        "Reel subtitles/Reel 01 - live",
        "Reel subtitles/Not placed on any timeline",
    }
    assert ordered.index("Reel subtitles/Reel 01 - live") < ordered.index(
        "Reel subtitles"
    )


def test_a_bin_the_captain_made_stays_even_when_empty():
    """A bin that is not part of either scheme is the captain's - they may
    be about to put something in it."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    tree = [("My selects",), ("VOX test",), ("Reels",)]
    plan = plan_retirements(artefacts, tree)
    assert "My selects" not in names(plan)
    assert "VOX test" not in names(plan)
    assert "Reels" in names(plan)


def test_a_captains_tier_under_a_legacy_top_stays_and_blocks_its_parent():
    """`Reels/Fully approved` is the captain's organisation winning where
    the two conflict. Retiring `Reels` under it would take the captain's
    bin with it, so the parent stays too - and says so."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    tree = [("Reels",), ("Reels", "Fully approved")]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == []


def test_an_unknown_leaf_under_a_legacy_top_is_not_provably_pipeline_made():
    """`Reel subtitles/my picks` holds nothing, but nothing says the
    pipeline made it either. From the pool alone it is indistinguishable
    from the captain's, so it stays - and its parent stays with it."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    tree = [("Reel subtitles",), ("Reel subtitles", "my picks")]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == []


def test_a_canonical_bin_is_never_retired_even_when_empty():
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    tree = [(BIN_REELS,), (BIN_SUBTITLES,), ("Reels",)]
    plan = plan_retirements(artefacts, tree)
    assert BIN_REELS not in names(plan)
    assert BIN_SUBTITLES not in names(plan)
    assert "Reels" in names(plan)


def test_an_emptied_canonical_per_reel_bin_retires_when_its_timeline_is_gone():
    """The captain's stale sub-bins, literally: `06 - Subtitle renders`
    still carries the staging name promotion emptied, and `07 - Motion
    graphics` still carries a deleted reel's bin. Empty and naming
    nothing live, so they retire - the population the legacy sweep
    never covered, in the same sweep rather than a second one."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-live", "Reel 09 - slug v003", folder=(BIN_REELS, "Current plan")),
    ]
    tree = [
        (BIN_SUBTITLES,),
        (BIN_SUBTITLES, "Reel 09 - slug (staging)"),
        (BIN_SUBTITLES, "Reel 09 - slug v003"),
        (bins.MOTION_GRAPHICS_BIN,),
        (bins.MOTION_GRAPHICS_BIN, "Reel 04 - deleted"),
    ]
    plan = plan_retirements(artefacts, tree)
    ordered = names(plan)
    assert f"{BIN_SUBTITLES}/Reel 09 - slug (staging)" in ordered
    assert f"{bins.MOTION_GRAPHICS_BIN}/Reel 04 - deleted" in ordered
    assert f"{BIN_SUBTITLES}/Reel 09 - slug v003" not in ordered
    assert BIN_SUBTITLES not in ordered
    assert bins.MOTION_GRAPHICS_BIN not in ordered
    assert all("why" in entry and entry["why"].strip() for entry in plan)


def test_a_canonical_per_reel_bin_of_a_live_timeline_stays_even_when_empty():
    """A live reel with nothing currently filed may gain some on the
    next build. Retiring its bin would be churn, not cleaning."""
    from library.tools.resolve_organization import plan_retirements

    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-live", "Reel 09 - slug v003", folder=(BIN_REELS, "Current plan")),
    ]
    tree = [(BIN_SUBTITLES,), (BIN_SUBTITLES, "Reel 09 - slug v003")]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == []


def test_the_unplaced_bin_is_a_standing_destination_and_never_retires():
    from library.tools.resolve_organization import plan_retirements
    from library.tools.resolve_organization import BIN_UNPLACED

    artefacts = [timeline("t-master", MASTER)]
    tree = [(BIN_SUBTITLES,), (BIN_SUBTITLES, BIN_UNPLACED)]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == []


def test_a_nonempty_canonical_orphan_stays_and_says_so():
    """An orphaned per-reel bin that still holds something is not a
    shell - the census says keep, with the reason, rather than going
    quiet about it."""
    from library.tools.resolve_organization import (
        plan_retirements,
        render_bin_census,
    )
    from library.tools.resolve_organization import BIN_UNPLACED

    artefacts = [
        timeline("t-master", MASTER),
        clip(
            "c-old",
            "old.mov",
            path=f"{PROJECT_ROOT}/pipeline_output/old.mov",
            placed_by=["Reel 04 - deleted"],
            folder=(BIN_SUBTITLES, "Reel 04 - deleted"),
        ),
    ]
    tree = [
        (BIN_SUBTITLES,),
        (BIN_SUBTITLES, "Reel 04 - deleted"),
        (BIN_SUBTITLES, BIN_UNPLACED),
    ]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == []
    text = render_bin_census(artefacts, tree, plan)
    assert "keep" in text
    assert f"{BIN_SUBTITLES}/Reel 04 - deleted (1 item(s))" in text


def test_every_retirement_names_the_scheme_that_superseded_it():
    from library.tools.resolve_organization import plan_retirements

    artefacts = [timeline("t-master", MASTER)]
    plan = plan_retirements(artefacts, [("Reels",), ("V1",)])
    by_name = {"/".join(e["path"]): e["why"] for e in plan}
    assert bins.REELS_BIN in by_name["Reels"]
    assert bins.SOURCE_BIN in by_name["V1"]


# ------------------------------------------------------- the executor


def empty_pool_with_shells():
    project = make_project("Fake")
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    shells = pool.AddSubFolder(root, "Reels")
    pool.AddSubFolder(shells, "Unrecorded")
    pool.AddSubFolder(root, "My selects")
    return project, root


def test_retiring_removes_the_shells_and_keeps_the_captains_bins(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements

    proj, _root = empty_pool_with_shells()
    artefacts, _, _, _ = read_pool(proj)
    tree = retire.read_bin_tree(proj)
    plan = plan_retirements(artefacts, list(tree))
    journal_path = str(tmp_path / "retire.json")
    result = retire.retire_bins(proj, plan, journal_path)
    remaining = sorted("/".join(p) for p in retire.read_bin_tree(proj))
    assert remaining == ["My selects"]
    assert sorted(result["retired"]) == [
        "Reels",
        "Reels/Unrecorded",
    ]
    assert result["journal_path"] == journal_path
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    assert sorted(r["path"] for r in journal["retired"]) == [
        "Reels",
        "Reels/Unrecorded",
    ]


def test_a_bin_that_gained_an_item_between_plan_and_apply_refuses(tmp_path):
    """The two-pass shape that caught the migration stranding nothing:
    plan read-only, then re-read and reconcile before touching."""
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements

    proj, root = empty_pool_with_shells()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)))
    pool = proj.GetMediaPool()
    shells = next(s for s in root.GetSubFolderList() if s.GetName() == "Reels")
    pool.SetCurrentFolder(shells)
    pool.ImportMedia(["/elsewhere/x.mov"])
    with pytest.raises(retire.RetirementRefused, match="no longer empty"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"))
    assert "Reels" in ["/".join(p) for p in retire.read_bin_tree(proj)]


def test_a_deletefolders_refusal_stops_the_run_and_keeps_the_bin(tmp_path):
    proj, _root = empty_pool_with_shells()
    plan = [
        {"path": ("Reels", "Unrecorded"), "why": "test"},
        {"path": ("Reels",), "why": "test"},
    ]
    proj.GetMediaPool().delete_folders_ok = False
    with pytest.raises(retire.RetirementRefused, match="DeleteFolders"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"))
    assert "Reels" in ["/".join(p) for p in retire.read_bin_tree(proj)]


def test_a_retirement_reverts_by_recreating_the_empty_shells(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements

    proj, _root = empty_pool_with_shells()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)))
    journal_path = str(tmp_path / "retire.json")
    retire.retire_bins(proj, plan, journal_path)
    undone = retire.revert(proj, journal_path)
    assert sorted(undone["recreated"]) == ["Reels", "Reels/Unrecorded"]
    assert sorted("/".join(p) for p in retire.read_bin_tree(proj)) == [
        "My selects",
        "Reels",
        "Reels/Unrecorded",
    ]
