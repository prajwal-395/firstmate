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

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=item_id, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def timeline(item_id, name, *, folder=()):
    return Artefact(item_id=item_id, name=name, kind="timeline",
                    file_path="", placed_by=(), folder_path=tuple(folder))


def names(plan):
    return ["/".join(e["path"]) for e in plan]


# ------------------------------------------------------- the pure plan


def test_an_emptied_legacy_top_is_retired():
    """The captain's complaint, literally: `Reels` and `Reel subtitles`
    stand empty beside `05 - Reels` and `06 - Subtitle renders`."""
    from library.tools.resolve_organization import plan_retirements
    artefacts = [timeline("t-master", MASTER)]
    tree = [("Reels",), ("Reel subtitles",), ("Subtitles",),
            (BIN_REELS,), (BIN_SUBTITLES,)]
    plan = plan_retirements(artefacts, tree)
    assert set(names(plan)) >= {"Reels", "Reel subtitles", "Subtitles"}
    assert all("why" in entry and entry["why"].strip() for entry in plan)


def test_a_legacy_bin_with_anything_inside_is_not_a_shell_and_stays():
    from library.tools.resolve_organization import plan_retirements
    artefacts = [
        timeline("t-master", MASTER),
        clip("c-cam", "cam.mov", path="/elsewhere/cam.mov",
             folder=("Subtitles",)),
    ]
    plan = plan_retirements(artefacts, [("Subtitles",), (BIN_SOURCE,)])
    assert "Subtitles" not in names(plan)


def test_a_shell_with_an_empty_sub_bin_under_it_is_still_empty():
    """The migration's own shape: `Reel subtitles` holds only its emptied
    per-reel leaves. Retire the leaves too, children before parents."""
    from library.tools.resolve_organization import plan_retirements
    artefacts = [timeline("t-master", MASTER)]
    tree = [("Reel subtitles",),
            ("Reel subtitles", "Reel 01 - live"),
            ("Reel subtitles", "Not placed on any timeline")]
    plan = plan_retirements(
        artefacts, tree, timeline_names=["Reel 01 - live"])
    ordered = names(plan)
    assert set(ordered) == {
        "Reel subtitles",
        "Reel subtitles/Reel 01 - live",
        "Reel subtitles/Not placed on any timeline",
    }
    assert ordered.index("Reel subtitles/Reel 01 - live") < ordered.index(
        "Reel subtitles")


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


def test_a_captains_tier_with_a_timeline_in_it_blocks_its_parent():
    from library.tools.resolve_organization import plan_retirements
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-a1", "Reel 01 - seo-ranks-geo-understands",
                 folder=("Reels", "Fully approved")),
    ]
    plan = plan_retirements(
        artefacts, [("Reels",), ("Reels", "Fully approved")])
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


def test_a_top_level_state_orphan_is_a_legacy_shell():
    """`Unrecorded` standing at the top level is old-scheme vocabulary,
    not a name the captain chose for new organisation."""
    from library.tools.resolve_organization import plan_retirements
    artefacts = [timeline("t-master", MASTER)]
    plan = plan_retirements(artefacts, [("Unrecorded",)])
    assert names(plan) == ["Unrecorded"]


def test_a_nested_legacy_duplicate_retires_as_one_empty_shell():
    """`Reels/Reels/Unrecorded`: the fork `AddSubFolder` makes. Every
    component is old-scheme vocabulary and nothing is inside, so the
    whole branch retires, deepest first."""
    from library.tools.resolve_organization import plan_retirements
    artefacts = [timeline("t-master", MASTER)]
    tree = [("Reels",), ("Reels", "Reels"),
            ("Reels", "Reels", "Unrecorded")]
    plan = plan_retirements(artefacts, tree)
    assert names(plan) == ["Reels/Reels/Unrecorded", "Reels/Reels", "Reels"]


def test_the_census_lists_every_bin_with_its_recursive_count_and_decision():
    """The read-only plan the brief demands: every bin, its item count
    including sub-bins, and retire/keep with the reason."""
    from library.tools.resolve_organization import (
        plan_retirements,
        render_bin_census,
    )
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-a1", "Reel 01 - seo-ranks-geo-understands",
                 folder=("Reels", "Fully approved")),
        clip("c-cam", "cam.mov", path="/elsewhere/cam.mov",
             folder=(BIN_SOURCE,)),
    ]
    tree = [("Reels",), ("Reels", "Fully approved"), (BIN_SOURCE,)]
    plan = plan_retirements(artefacts, tree)
    text = render_bin_census(artefacts, tree, plan)
    assert "Reels (1 item(s))" in text
    assert "Reels/Fully approved (1 item(s))" in text
    assert "keep" in text
    assert BIN_SOURCE in text


def test_every_retirement_names_the_scheme_that_superseded_it():
    from library.tools.resolve_organization import plan_retirements
    artefacts = [timeline("t-master", MASTER)]
    plan = plan_retirements(artefacts, [("Reels",), ("V1",)])
    by_name = {"/".join(e["path"]): e["why"] for e in plan}
    assert bins.REELS_BIN in by_name["Reels"]
    assert bins.SOURCE_BIN in by_name["V1"]


# ------------------------------------------------------- the executor


class FakeFolder:
    def __init__(self, name, uid):
        self._name, self.uid, self.clips, self.subs = name, uid, [], []

    def GetName(self): return self._name
    def GetUniqueId(self): return self.uid
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class FakeClip:
    def __init__(self, uid, name, kind="clip", path=""):
        self.uid, self._name, self.kind, self.path = uid, name, kind, path

    def GetUniqueId(self): return self.uid
    def GetName(self): return self._name

    def GetClipProperty(self, key):
        if key == "Type":
            return "Timeline" if self.kind == "timeline" else "Video + Audio"
        if key == "File Path":
            return self.path
        return ""


class FakeTimeline:
    def __init__(self, name, items=()):
        self._name, self.items = name, list(items)

    def GetName(self): return self._name
    def GetTrackCount(self, kind): return 0
    def GetItemListInTrack(self, kind, index): return []


class FakePool:
    def __init__(self, root):
        self.root, self.current, self._n = root, root, 0

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def AddSubFolder(self, parent, name):
        for sub in parent.subs:
            if sub.GetName() == name:
                return sub
        self._n += 1
        folder = FakeFolder(name, f"made{self._n}")
        parent.subs.append(folder)
        self.current = folder
        return folder

    def DeleteFolders(self, folders):
        for folder in folders:
            if folder.GetClipList() or folder.GetSubFolderList():
                return False
            parent = self._parent(folder)
            if parent is None:
                return False
            parent.subs.remove(folder)
        return True

    def _parent(self, folder):
        def walk(node):
            for sub in node.subs:
                if sub is folder:
                    return node
                found = walk(sub)
                if found is not None:
                    return found
            return None
        return walk(self.root)


class FakeProject:
    def __init__(self, name, pool, timelines=()):
        self._name, self.pool, self.timelines = name, pool, list(timelines)

    def GetName(self): return self._name
    def GetMediaPool(self): return self.pool
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]


def empty_pool_with_shells():
    root = FakeFolder("Master", "root")
    shells = FakeFolder("Reels", "f1")
    leaf = FakeFolder("Unrecorded", "f2")
    shells.subs.append(leaf)
    root.subs.append(shells)
    root.subs.append(FakeFolder("My selects", "f3"))
    return root


def test_retiring_removes_the_shells_and_keeps_the_captains_bins(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements
    root = empty_pool_with_shells()
    proj = FakeProject("Fake", FakePool(root))
    artefacts, _, _, _ = read_pool(proj)
    tree = retire.read_bin_tree(proj)
    plan = plan_retirements(artefacts, list(tree))
    journal_path = str(tmp_path / "retire.json")
    result = retire.retire_bins(proj, plan, journal_path)
    remaining = sorted("/".join(p) for p in retire.read_bin_tree(proj))
    assert remaining == ["My selects"]
    assert sorted(result["retired"]) == [
        "Reels", "Reels/Unrecorded",
    ]
    assert result["journal_path"] == journal_path
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    assert sorted(r["path"] for r in journal["retired"]) == [
        "Reels", "Reels/Unrecorded",
    ]


def test_a_bin_that_gained_an_item_between_plan_and_apply_refuses(tmp_path):
    """The two-pass shape that caught the migration stranding nothing:
    plan read-only, then re-read and reconcile before touching."""
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements
    root = empty_pool_with_shells()
    proj = FakeProject("Fake", FakePool(root))
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)))
    shells = next(s for s in root.subs if s.GetName() == "Reels")
    shells.clips.append(FakeClip("c-late", "late.mov", path="/elsewhere/x.mov"))
    with pytest.raises(retire.RetirementRefused, match="no longer empty"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"))
    assert "Reels" in ["/".join(p) for p in retire.read_bin_tree(proj)]


def test_a_deletefolders_refusal_stops_the_run_and_keeps_the_bin(tmp_path):
    root = empty_pool_with_shells()
    proj = FakeProject("Fake", FakePool(root))
    plan = [{"path": ("Reels", "Unrecorded"),
             "why": "test"},
            {"path": ("Reels",), "why": "test"}]
    proj.GetMediaPool().DeleteFolders = lambda folders: False
    with pytest.raises(retire.RetirementRefused, match="DeleteFolders"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"))
    assert "Reels" in ["/".join(p) for p in retire.read_bin_tree(proj)]


def test_a_retirement_reverts_by_recreating_the_empty_shells(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements
    root = empty_pool_with_shells()
    proj = FakeProject("Fake", FakePool(root))
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)))
    journal_path = str(tmp_path / "retire.json")
    retire.retire_bins(proj, plan, journal_path)
    undone = retire.revert(proj, journal_path)
    assert sorted(undone["recreated"]) == ["Reels", "Reels/Unrecorded"]
    assert sorted("/".join(p) for p in retire.read_bin_tree(proj)) == [
        "My selects", "Reels", "Reels/Unrecorded",
    ]


def test_reverting_the_same_retirement_journal_twice_refuses(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    from library.tools.resolve_organization import plan_retirements
    root = empty_pool_with_shells()
    proj = FakeProject("Fake", FakePool(root))
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)))
    journal_path = str(tmp_path / "retire.json")
    retire.retire_bins(proj, plan, journal_path)
    retire.revert(proj, journal_path)
    with pytest.raises(retire.RetirementRefused, match="already reverted"):
        retire.revert(proj, journal_path)


def test_the_retirement_says_in_its_own_source_what_deletefolders_does():
    """The folder call gets the same treatment as the clip call: what it
    does to anything still inside is established where the call is."""
    source = Path(
        "library/tools/execution/retire_empty_bins.py").read_text(
            encoding="utf-8")
    assert "DeleteFolders" in source
    assert "empty" in source.lower()


def test_retirement_journals_live_beside_placement_journals(tmp_path):
    first = retire.journal_path_for(str(tmp_path), "A")
    second = retire.journal_path_for(str(tmp_path), "B")
    assert first != second
    assert "retire" in Path(first).name
