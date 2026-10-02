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


def test_plan_retires_exactly_the_provably_empty_pipeline_shells():
    """One row per case, each proven in BOTH directions: what must
    retire and what must stay, on the same shape. Every retirement says
    why, naming the scheme that superseded it."""
    from library.tools.resolve_organization import BIN_UNPLACED, plan_retirements

    master = timeline("t-master", MASTER)
    live = timeline("t-live", "Reel 09 - slug v003", folder=(BIN_REELS, "Current plan"))
    rows = [
        # The captain's complaint, literally: legacy tops standing empty
        # beside the numbered scheme.
        ("emptied legacy tops", [master],
         [("Reels",), ("Reel subtitles",), ("Subtitles",), (BIN_REELS,), (BIN_SUBTITLES,)],
         {}, {"Reels", "Reel subtitles", "Subtitles"}, {BIN_REELS, BIN_SUBTITLES}),
        ("a legacy bin holding anything is not a shell",
         [master, clip("c-cam", "cam.mov", path="/elsewhere/cam.mov", folder=("Subtitles",))],
         [("Subtitles",), (BIN_SOURCE,)], {}, set(), {"Subtitles"}),
        # A shell whose only children are emptied per-reel leaves.
        ("empty sub-bins under a shell",
         [master],
         [("Reel subtitles",), ("Reel subtitles", "Reel 01 - live"),
          ("Reel subtitles", "Not placed on any timeline")],
         {"timeline_names": ["Reel 01 - live"]},
         {"Reel subtitles", "Reel subtitles/Reel 01 - live",
          "Reel subtitles/Not placed on any timeline"}, set()),
        # A bin in neither scheme is the captain's.
        ("captain-made bins", [master], [("My selects",), ("VOX test",), ("Reels",)],
         {}, {"Reels"}, {"My selects", "VOX test"}),
        # `Reels/Fully approved` is the captain's tier; its parent stays.
        ("captain's tier blocks its parent", [master], [("Reels",), ("Reels", "Fully approved")],
         {}, set(), {"Reels", "Reels/Fully approved"}),
        # Nothing says the pipeline made `my picks`, so it and its parent stay.
        ("unknown leaf under a legacy top", [master],
         [("Reel subtitles",), ("Reel subtitles", "my picks")],
         {}, set(), {"Reel subtitles", "Reel subtitles/my picks"}),
        # Empty canonical per-reel bins naming nothing live retire; the
        # live reel's bin, the canonical tops and the unplaced bin stay.
        ("canonical per-reel bins", [master, live],
         [(BIN_SUBTITLES,), (BIN_SUBTITLES, "Reel 09 - slug (staging)"),
          (BIN_SUBTITLES, "Reel 09 - slug v003"), (BIN_SUBTITLES, BIN_UNPLACED),
          (bins.MOTION_GRAPHICS_BIN,), (bins.MOTION_GRAPHICS_BIN, "Reel 04 - deleted")],
         {}, {f"{BIN_SUBTITLES}/Reel 09 - slug (staging)",
              f"{bins.MOTION_GRAPHICS_BIN}/Reel 04 - deleted"},
         {BIN_SUBTITLES, f"{BIN_SUBTITLES}/Reel 09 - slug v003",
          f"{BIN_SUBTITLES}/{BIN_UNPLACED}", bins.MOTION_GRAPHICS_BIN}),
    ]
    for label, artefacts, tree, kwargs, retire_set, keep_set in rows:
        plan = plan_retirements(artefacts, tree, **kwargs)
        retired = names(plan)
        assert set(retired) == retire_set, label
        assert not keep_set & set(retired), label
        assert all(entry["why"].strip() for entry in plan), label
    # Children before parents.
    plan = plan_retirements([master], rows[2][2], **rows[2][3])
    ordered = names(plan)
    assert ordered.index("Reel subtitles/Reel 01 - live") < ordered.index(
        "Reel subtitles"
    )
    # The why names the scheme that superseded the bin.
    by_name = {
        "/".join(e["path"]): e["why"]
        for e in plan_retirements([master], [("Reels",), ("V1",)])
    }
    assert bins.REELS_BIN in by_name["Reels"]
    assert bins.SOURCE_BIN in by_name["V1"]


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


# ------------------------------------------------------- the executor


def empty_pool_with_shells():
    project = make_project("Fake")
    pool = project.GetMediaPool()
    root = pool.GetRootFolder()
    shells = pool.AddSubFolder(root, "Reels")
    pool.AddSubFolder(shells, "Unrecorded")
    pool.AddSubFolder(root, "My selects")
    return project, root


def test_retiring_removes_the_shells_keeps_the_captains_bins_and_reverts(tmp_path):
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
    # And it reverts by recreating the empty shells from the journal.
    undone = retire.revert(proj, journal_path)
    assert sorted(undone["recreated"]) == ["Reels", "Reels/Unrecorded"]
    assert sorted("/".join(p) for p in retire.read_bin_tree(proj)) == [
        "My selects",
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
