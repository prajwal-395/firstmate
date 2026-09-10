"""Dead per-reel bins retire with their contents - and nothing else does.

The captain's third report of the same bins (2026-09-10): one reel shows
up twice under both `06 - Subtitle renders` and `07 - Motion graphics`.
Per-reel bins are keyed by placing-timeline NAME, every variant and
every superseded build is a new timeline name, and timelines are never
deleted - so one reel is N names is N leaves, and the sweep could only
ever retire EMPTY legacy shells.  Every build made it worse.

The rule: a per-reel leaf under a render bin that names NO live
timeline is dead whether or not it holds files, and it retires WITH
its contents - proven unplaced and pipeline-generated, journalled
with what it held.  Every gate here is proven in BOTH directions
(AGENTS.md 10.4): a retirement that must happen and one that must
not, on the same shape.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools import resolve_bin_layout as bins
from library.tools.execution import retire_empty_bins as retire
from library.tools.proof_cleanup import (
    CAPTAINS_PROOF_TIMELINE,
    MERGE_DEMO_TIMELINE,
    POSITIONING_SCRATCH_PREFIX,
    PRE_REBUILD_BACKUP_TIMELINE,
    PROTECTED_TIMELINES,
    ProofRemovalRefused,
    discover_proof_bins,
    discover_scratch_timelines,
    plan_proof_removal,
)
from library.tools.resolve_organization import (
    Artefact,
    plan_dead_render_bins,
    plan_retirements,
    render_bin_census,
)

MASTER = "GEO Podcast - Synced"
PROJECT_ROOT = "/projects/geo-podcast"
LIVE = "Reel 09 - your-website-is-only-20-percent (final)"
DEAD = "Reel 09 - your-website-is-only-20-percent (rebuild staging)"


def clip(item_id, name, *, path, placed_by=(), folder=()):
    return Artefact(item_id=item_id, name=name, kind="clip", file_path=path,
                    placed_by=tuple(placed_by), folder_path=tuple(folder))


def timeline(item_id, name, *, folder=()):
    return Artefact(item_id=item_id, name=name, kind="timeline",
                    file_path="", placed_by=(),
                    folder_path=tuple(folder))


def generated(name):
    return (f"{PROJECT_ROOT}/pipeline_output/steps/"
            f"4_05_render_subtitles/{name}")


def dead_pool():
    """The captain's screenshot in miniature: a dead leaf under 06
    holding two unplaced renders, beside the live reel's own leaf."""
    return [
        timeline("t-master", MASTER),
        timeline("t-live", LIVE,
                 folder=(bins.REELS_BIN, "Current plan")),
        clip("c-dead-a", "sub_dead_a.mov", path=generated("a.mov"),
             folder=(bins.SUBTITLES_BIN, DEAD)),
        clip("c-dead-b", "sub_dead_b.mov", path=generated("b.mov"),
             folder=(bins.SUBTITLES_BIN, DEAD)),
        clip("c-live", "sub_live.mov",
             path=generated("live.mov"), placed_by=[LIVE],
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


def test_a_dead_leaf_retires_with_its_contents_listed():
    dead, declined = plan_dead_render_bins(
        dead_pool(), tree_of(dead_pool()), PROJECT_ROOT)
    assert names(dead) == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert declined == []
    entry = dead[0]
    assert entry["kind"] == "dead_render_bin"
    assert sorted(c["item_id"] for c in entry["contents"]) == [
        "c-dead-a", "c-dead-b"]
    assert all(c["file_path"] for c in entry["contents"])
    assert DEAD in entry["why"] and "no timeline" in entry["why"]


def test_a_leaf_naming_a_live_timeline_stays():
    artefacts = dead_pool()
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert f"{bins.SUBTITLES_BIN}/{LIVE}" not in names(dead)
    assert declined == []


def test_an_earlier_plan_timeline_keeps_its_bin():
    """Superseded-but-present timelines are reality too: the bin stays
    whatever state the timeline is in."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-old", "Reel 01 - superseded",
                 folder=(bins.REELS_BIN, "Earlier plans")),
        clip("c-old", "sub_old.mov", path=generated("old.mov"),
             placed_by=["Reel 01 - superseded"],
             folder=(bins.SUBTITLES_BIN, "Reel 01 - superseded")),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert dead == [] and declined == []


def test_a_dead_leaf_holding_a_placed_clip_is_declined():
    artefacts = dead_pool() + [
        clip("c-still", "sub_still.mov", path=generated("still.mov"),
             placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, DEAD)),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert dead == []
    assert ["/".join(d["path"]) for d in declined] == [
        f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert "still placed" in declined[0]["why"]


def test_a_dead_leaf_holding_a_timeline_is_declined():
    artefacts = dead_pool() + [
        timeline("t-stray", "Reel 02 - stray",
                 folder=(bins.SUBTITLES_BIN, DEAD)),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert dead == []
    assert len(declined) == 1 and "strand" in declined[0]["why"]


def test_a_dead_leaf_holding_foreign_material_is_declined():
    artefacts = dead_pool() + [
        clip("c-foreign", "cam.mov", path="/elsewhere/cam.mov",
             folder=(bins.SUBTITLES_BIN, DEAD)),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert dead == []
    assert len(declined) == 1 and "not pipeline-generated" in declined[0]["why"]


def test_the_unplaced_leaf_never_retires():
    artefacts = [
        timeline("t-master", MASTER),
        clip("c-u", "sub_u.mov", path=generated("u.mov"),
             folder=(bins.SUBTITLES_BIN, bins.UNPLACED_BIN)),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert dead == [] and declined == []


def test_an_emptied_dead_leaf_retires_as_a_shell():
    artefacts = [timeline("t-master", MASTER)]
    tree = [(bins.SUBTITLES_BIN,), (bins.SUBTITLES_BIN, DEAD)]
    dead, _declined = plan_dead_render_bins(artefacts, tree, PROJECT_ROOT)
    assert names(dead) == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert dead[0]["contents"] == []


def test_a_dead_leaf_under_motion_graphics_retires_too():
    artefacts = [
        timeline("t-master", MASTER),
        clip("c-mg", "mg_dead.mov",
             path=(f"{PROJECT_ROOT}/pipeline_output/steps/"
                   f"4_06_render_motion_graphics/motion_graphics/d.mov"),
             folder=(bins.MOTION_GRAPHICS_BIN, DEAD)),
    ]
    dead, _ = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    assert names(dead) == [f"{bins.MOTION_GRAPHICS_BIN}/{DEAD}"]


def test_plan_retirements_needs_a_root_for_the_dead_rule():
    artefacts, tree = dead_pool(), tree_of(dead_pool())
    without = plan_retirements(artefacts, tree)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" not in names(without)
    with_root = plan_retirements(artefacts, tree, project_root=PROJECT_ROOT)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in names(with_root)


def test_a_legacy_shell_still_retires_beside_a_dead_leaf():
    artefacts = [timeline("t-master", MASTER)]
    tree = [("Reels",), (bins.SUBTITLES_BIN,),
            (bins.SUBTITLES_BIN, DEAD)]
    plan = plan_retirements(artefacts, tree, project_root=PROJECT_ROOT)
    by_name = {"/".join(e["path"]): e for e in plan}
    assert by_name["Reels"]["kind"] == "legacy_shell"
    assert by_name[f"{bins.SUBTITLES_BIN}/{DEAD}"]["kind"] == \
        "dead_render_bin"


def test_the_census_says_retire_with_contents_and_reports_declined():
    artefacts = dead_pool() + [
        clip("c-still", "sub_still.mov", path=generated("still.mov"),
             placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, "Reel 09 - another-gone")),
    ]
    dead, declined = plan_dead_render_bins(
        artefacts, tree_of(artefacts), PROJECT_ROOT)
    text = render_bin_census(artefacts, tree_of(artefacts), dead,
                             declined=declined)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in text
    assert "RETIRE with 2 item(s)" in text
    assert "declined" in text and "still placed" in text


# ------------------------------------------------- the proof removal


def proof_pool():
    """All four protected timelines plus the master present, the proof
    timeline live, and its caption bin holding one unplaced render."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-proof", CAPTAINS_PROOF_TIMELINE,
                 folder=(bins.REELS_BIN, bins.REELS_PROOF_BIN)),
        clip("c-proof", "proof_cap.mov", path=generated("p.mov"),
             folder=(bins.SUBTITLES_BIN, "SOP Proof_captions")),
    ]
    for i, name in enumerate(sorted(PROTECTED_TIMELINES)):
        artefacts.append(timeline(f"t-p{i}", name,
                                  folder=(bins.REELS_BIN, "Current plan")))
    return artefacts


def test_the_proof_plan_names_the_timeline_bins_and_the_untouched():
    artefacts = proof_pool()
    plan = plan_proof_removal(
        artefacts, tree_of(artefacts),
        timeline_name=CAPTAINS_PROOF_TIMELINE,
        bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_captions"],
        project_root=PROJECT_ROOT, master_name=MASTER)
    assert plan["timeline"]["name"] == CAPTAINS_PROOF_TIMELINE
    assert ["/".join(b["path"]) for b in plan["bins"]] == [
        f"{bins.SUBTITLES_BIN}/SOP Proof_captions"]
    assert plan["bins"][0]["contents"][0]["file_path"] == generated("p.mov")
    assert sorted(u["name"] for u in plan["verified_untouched"]) == \
        sorted(PROTECTED_TIMELINES | {MASTER})


def test_the_proof_path_refuses_every_protected_name_and_the_master():
    artefacts = proof_pool()
    for name in list(PROTECTED_TIMELINES) + [MASTER]:
        with pytest.raises(ProofRemovalRefused):
            plan_proof_removal(
                artefacts, tree_of(artefacts), timeline_name=name,
                bin_names=[], project_root=PROJECT_ROOT,
                master_name=MASTER)


def test_the_proof_path_refuses_a_non_proof_name_and_a_near_match():
    artefacts = proof_pool()
    for name in [LIVE, CAPTAINS_PROOF_TIMELINE[:-1]]:
        with pytest.raises(ProofRemovalRefused):
            plan_proof_removal(
                artefacts, tree_of(artefacts), timeline_name=name,
                bin_names=[], project_root=PROJECT_ROOT,
                master_name=MASTER)


def test_the_proof_path_refuses_an_absent_timeline():
    artefacts = [a for a in proof_pool() if a.name != CAPTAINS_PROOF_TIMELINE]
    with pytest.raises(ProofRemovalRefused, match="no timeline"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE, bin_names=[],
            project_root=PROJECT_ROOT, master_name=MASTER)


def test_the_proof_path_refuses_a_bin_that_is_not_proven():
    artefacts = proof_pool()
    # Absent bin: no exact match to prove.
    with pytest.raises(ProofRemovalRefused, match="not in the pool"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE,
            bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_missing"],
            project_root=PROJECT_ROOT, master_name=MASTER)
    # Bin outside any render bin.
    with pytest.raises(ProofRemovalRefused, match="not a per-reel bin"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE,
            bin_names=["My selects"], project_root=PROJECT_ROOT,
            master_name=MASTER)


def test_the_proof_path_refuses_a_bin_holding_placed_material():
    artefacts = proof_pool() + [
        clip("c-placed", "sub_x.mov", path=generated("x.mov"),
             placed_by=[LIVE],
             folder=(bins.SUBTITLES_BIN, "SOP Proof_captions")),
    ]
    with pytest.raises(ProofRemovalRefused, match="still placed"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE,
            bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_captions"],
            project_root=PROJECT_ROOT, master_name=MASTER)


def test_the_proof_plan_takes_contents_placed_only_on_the_doomed_timeline():
    """The live case: the proof caption is placed on the proof
    timeline going down with it.  Anything a survivor still plays
    still refuses."""
    artefacts = proof_pool() + [
        clip("c-doomed", "still_780x480.png", path=generated("s.png"),
             placed_by=[CAPTAINS_PROOF_TIMELINE],
             folder=(bins.SUBTITLES_BIN, "SOP Proof_captions")),
        clip("c-shared", "shared.mov", path=generated("shared.mov"),
             placed_by=[CAPTAINS_PROOF_TIMELINE, LIVE],
             folder=(bins.SUBTITLES_BIN, "SOP Proof_shared")),
    ]
    plan = plan_proof_removal(
        artefacts, tree_of(artefacts),
        timeline_name=CAPTAINS_PROOF_TIMELINE,
        bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_captions"],
        project_root=PROJECT_ROOT, master_name=MASTER)
    assert sorted(c["item_id"] for c in plan["bins"][0]["contents"]) == [
        "c-doomed", "c-proof"]
    with pytest.raises(ProofRemovalRefused, match="still placed"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE,
            bin_names=[f"{bins.SUBTITLES_BIN}/SOP Proof_shared"],
            project_root=PROJECT_ROOT, master_name=MASTER)


def test_the_proof_path_refuses_when_a_protected_timeline_is_missing():
    artefacts = [a for a in proof_pool() if a.name != LIVE]
    with pytest.raises(ProofRemovalRefused, match="protected timeline"):
        plan_proof_removal(
            artefacts, tree_of(artefacts),
            timeline_name=CAPTAINS_PROOF_TIMELINE, bin_names=[],
            project_root=PROJECT_ROOT, master_name=MASTER)


def test_discover_lists_proof_bins_for_the_operator_to_confirm():
    artefacts = proof_pool()
    found = discover_proof_bins(artefacts, tree_of(artefacts))
    assert (bins.SUBTITLES_BIN, "SOP Proof_captions") in found
    assert all(leaf.startswith("SOP Proof") for _, leaf in found)


def demo_pool():
    """The crew merge-demo artefact: exact timeline name, exact-named
    caption bin, protected timelines present."""
    artefacts = [
        timeline("t-master", MASTER),
        timeline("t-demo", MERGE_DEMO_TIMELINE,
                 folder=(bins.REELS_BIN, "Current plan")),
        clip("c-demo", "demo_cap.mov", path=generated("d.mov"),
             folder=(bins.SUBTITLES_BIN, MERGE_DEMO_TIMELINE)),
    ]
    for i, name in enumerate(sorted(PROTECTED_TIMELINES)):
        artefacts.append(timeline(f"t-p{i}", name,
                                  folder=(bins.REELS_BIN, "Current plan")))
    return artefacts


def test_the_merge_demo_plans_by_exact_name_with_its_bin():
    artefacts = demo_pool()
    plan = plan_proof_removal(
        artefacts, tree_of(artefacts),
        timeline_name=MERGE_DEMO_TIMELINE,
        bin_names=[f"{bins.SUBTITLES_BIN}/{MERGE_DEMO_TIMELINE}"],
        project_root=PROJECT_ROOT, master_name=MASTER)
    assert plan["timeline"]["name"] == MERGE_DEMO_TIMELINE
    assert ["/".join(b["path"]) for b in plan["bins"]] == [
        f"{bins.SUBTITLES_BIN}/{MERGE_DEMO_TIMELINE}"]
    assert sorted(u["name"] for u in plan["verified_untouched"]) == \
        sorted(PROTECTED_TIMELINES | {MASTER})


def test_the_merge_demo_refuses_a_near_variant_of_its_name():
    artefacts = demo_pool()
    for name in [MERGE_DEMO_TIMELINE + " 2",
                 MERGE_DEMO_TIMELINE.replace("(", "").replace(")", "")]:
        with pytest.raises(ProofRemovalRefused):
            plan_proof_removal(
                artefacts, tree_of(artefacts), timeline_name=name,
                bin_names=[], project_root=PROJECT_ROOT,
                master_name=MASTER)


def test_discover_lists_an_exact_named_demo_bin():
    artefacts = demo_pool()
    found = discover_proof_bins(artefacts, tree_of(artefacts))
    assert (bins.SUBTITLES_BIN, MERGE_DEMO_TIMELINE) in found


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


def test_the_positioning_scratch_plans_timeline_only_with_untouched():
    assert all(n.startswith(POSITIONING_SCRATCH_PREFIX)
               for n in POSITIONING_NAMES)
    artefacts = scratch_pool()
    for name in POSITIONING_NAMES + [PRE_REBUILD_BACKUP_TIMELINE]:
        plan = plan_proof_removal(
            artefacts, tree_of(artefacts), timeline_name=name,
            bin_names=[], project_root=PROJECT_ROOT,
            master_name=MASTER)
        assert plan["timeline"]["name"] == name
        assert plan["bins"] == []
        assert sorted(u["name"] for u in plan["verified_untouched"]) == \
            sorted(PROTECTED_TIMELINES | {MASTER})


def test_the_positioning_scratch_refuses_outside_the_family():
    artefacts = scratch_pool()
    for name in ["Reel 09 - your-website-is-only-20-percent (rebuild staging)",
                 "Reel 09 - your-website-is-only-20-percent (positioning-proof",
                 LIVE]:
        with pytest.raises(ProofRemovalRefused):
            plan_proof_removal(
                artefacts, tree_of(artefacts), timeline_name=name,
                bin_names=[], project_root=PROJECT_ROOT,
                master_name=MASTER)


def test_discover_scratch_lists_what_is_present_in_name_order():
    artefacts = scratch_pool()
    assert discover_scratch_timelines(artefacts) == sorted(
        POSITIONING_NAMES + [PRE_REBUILD_BACKUP_TIMELINE])


def test_discover_scratch_is_empty_when_the_scratch_is_gone():
    """The idempotence case: a second run finds nothing to remove, so it
    removes nothing and refuses nothing."""
    artefacts = [a for a in scratch_pool()
                 if a.name not in POSITIONING_NAMES
                 and a.name != PRE_REBUILD_BACKUP_TIMELINE]
    assert discover_scratch_timelines(artefacts) == []


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

    def GetMetadata(self, key=None): return ""

    def SetMetadata(self, key, value):
        return key in ("Comments", "Keywords")

    def SetClipColor(self, value): return True
    def ClearClipColor(self): return True


class FakeTimeline:
    def __init__(self, name, items=()):
        self._name, self.items = name, list(items)

    def GetName(self): return self._name
    def GetTrackCount(self, kind): return 1 if kind == "video" else 0
    def GetItemListInTrack(self, kind, index):
        return list(self.items) if kind == "video" else []


class FakeItem:
    def __init__(self, clip): self.clip = clip
    def GetMediaPoolItem(self): return self.clip


class FakePool:
    def __init__(self, root):
        self.root, self.current = root, root

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, folder):
        self.current = folder
        return True

    def _home(self, clip):
        def walk(folder):
            if clip in folder.clips:
                return folder
            for sub in folder.subs:
                found = walk(sub)
                if found is not None:
                    return found
            return None
        return walk(self.root)

    def MoveClips(self, clips, folder):
        for clip in clips:
            home = self._home(clip)
            if home is not None:
                home.clips.remove(clip)
            folder.clips.append(clip)
        return True

    def AddSubFolder(self, parent, name):
        self._n = getattr(self, "_n", 0) + 1
        folder = FakeFolder(name, f"made{self._n}")
        parent.subs.append(folder)
        self.current = folder
        return folder

    def DeleteClips(self, items):
        # Removes the ITEMS; files stay on disk, so there is no disk
        # here to touch - which is the property under test.
        wanted = {c.GetUniqueId() for c in items}

        def walk(folder):
            folder.clips[:] = [c for c in folder.clips
                               if c.GetUniqueId() not in wanted]
            for sub in folder.subs:
                walk(sub)

        walk(self.root)
        return True

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


def live_pool():
    """Fake pool shaped like the captain's: a dead leaf with two
    unplaced renders, a live leaf with one placed render."""
    root = FakeFolder("Master", "root")
    six = FakeFolder(bins.SUBTITLES_BIN, "f6")
    dead = FakeFolder(DEAD, "f-dead")
    dead.clips.append(FakeClip("c-dead-a", "sub_dead_a.mov",
                               path=generated("a.mov")))
    dead.clips.append(FakeClip("c-dead-b", "sub_dead_b.mov",
                               path=generated("b.mov")))
    live = FakeFolder(LIVE, "f-live")
    live_clip = FakeClip("c-live", "sub_live.mov", path=generated("live.mov"))
    live.clips.append(live_clip)
    six.subs.extend([dead, live])
    root.subs.append(six)
    timelines = [FakeTimeline(LIVE, [FakeItem(live_clip)])]
    return FakeProject("Fake", FakePool(root), timelines)


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


def test_retiring_a_dead_leaf_removes_its_items_then_the_bin(tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=PROJECT_ROOT)
    assert names(plan) == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    journal_path = str(tmp_path / "retire.json")
    result = retire.retire_bins(proj, plan, journal_path,
                                project_root=PROJECT_ROOT)
    assert result["retired"] == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert result["removed_items"] == 2
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" not in pool_bins(proj)
    assert f"{bins.SUBTITLES_BIN}/{LIVE}" in pool_bins(proj)
    assert pool_clip_names(proj) == ["sub_live.mov"]
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    held = journal["retired"][0]["contents"]
    assert sorted(c["file_path"] for c in held) == sorted(
        [generated("a.mov"), generated("b.mov")])


def test_a_second_run_plans_nothing(tmp_path):
    """Idempotence: the tree derives from the timelines that exist, so
    once the dead leaf is gone there is nothing left to derive."""
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=PROJECT_ROOT)
    retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                       project_root=PROJECT_ROOT)
    fresh, _, _, _ = read_pool(proj)
    again = plan_retirements(fresh, list(retire.read_bin_tree(proj)),
                             project_root=PROJECT_ROOT)
    assert again == []


def test_a_dead_leaf_that_gained_a_placement_between_plan_and_apply_refuses(
        tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=PROJECT_ROOT)
    # Between plan and apply the captain cuts a dead render back onto
    # the live reel: the fresh read places it, and the dead proof fails.
    root = proj.GetMediaPool().GetRootFolder()
    six = next(s for s in root.GetSubFolderList()
               if s.GetName() == bins.SUBTITLES_BIN)
    dead = next(s for s in six.GetSubFolderList() if s.GetName() == DEAD)
    proj.timelines.append(FakeTimeline(LIVE, [FakeItem(dead.clips[0])]))
    with pytest.raises(retire.RetirementRefused, match="no longer proves dead"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                           project_root=PROJECT_ROOT)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in pool_bins(proj)


def test_contents_calling_themselves_clips_but_reading_as_timelines_refuse(
        tmp_path):
    """The DeleteClips failure mode, aimed at on purpose: a pool item
    the plan read as a clip reports Type Timeline at the moment of
    the call - the pool changed mid-run - and the run refuses."""
    from library.tools.execution.organise_media_pool import read_pool

    class ChangingClip(FakeClip):
        # Clip on every read the planner makes, a timeline at the
        # moment of the call: the pool changed mid-run.
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._reads = 0

        def GetClipProperty(self, key):
            if key == "Type":
                self._reads += 1
                return "Timeline" if self._reads > 2 else "Video + Audio"
            return super().GetClipProperty(key)

    proj = live_pool()
    root = proj.GetMediaPool().GetRootFolder()
    six = next(s for s in root.GetSubFolderList()
               if s.GetName() == bins.SUBTITLES_BIN)
    dead = next(s for s in six.GetSubFolderList() if s.GetName() == DEAD)
    changer = ChangingClip("c-changing", "changing.mov",
                           path=generated("changing.mov"))
    dead.clips.append(changer)
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=PROJECT_ROOT)
    with pytest.raises(retire.RetirementRefused, match="Timeline"):
        retire.retire_bins(proj, plan, str(tmp_path / "retire.json"),
                           project_root=PROJECT_ROOT)
    assert "changing.mov" in pool_clip_names(proj)
    assert f"{bins.SUBTITLES_BIN}/{DEAD}" in pool_bins(proj)


def test_a_dead_retirement_reverts_to_an_empty_shell_and_says_what_is_gone(
        tmp_path):
    from library.tools.execution.organise_media_pool import read_pool
    proj = live_pool()
    artefacts, _, _, _ = read_pool(proj)
    plan = plan_retirements(artefacts, list(retire.read_bin_tree(proj)),
                            project_root=PROJECT_ROOT)
    journal_path = str(tmp_path / "retire.json")
    retire.retire_bins(proj, plan, journal_path, project_root=PROJECT_ROOT)
    undone = retire.revert(proj, journal_path)
    assert undone["recreated"] == [f"{bins.SUBTITLES_BIN}/{DEAD}"]
    assert sorted(c["file_path"] for c in undone["contents_not_restored"]) == \
        sorted([generated("a.mov"), generated("b.mov")])


def test_remove_proof_deletes_the_timeline_its_bin_and_nothing_else(tmp_path):
    from library.tools.execution import remove_proof
    root = FakeFolder("Master", "root")
    reels = FakeFolder(bins.REELS_BIN, "f5")
    proof_bin = FakeFolder(bins.REELS_PROOF_BIN, "f-proof")
    proof_tl = FakeClip("t-proof", CAPTAINS_PROOF_TIMELINE, kind="timeline")
    proof_bin.clips.append(proof_tl)
    reels.subs.append(proof_bin)
    six = FakeFolder(bins.SUBTITLES_BIN, "f6")
    cap_bin = FakeFolder("SOP Proof_min-canvas-rail", "f-cap")
    cap_bin.clips.append(FakeClip("c-p", "proof_cap.mov",
                                  path=generated("p.mov")))
    six.subs.append(cap_bin)
    live = FakeFolder(LIVE, "f-live")
    live.clips.append(FakeClip("c-live", "sub_live.mov",
                               path=generated("live.mov")))
    six.subs.append(live)
    root.subs.extend([reels, six])
    live_tl = FakeTimeline(
        LIVE, [FakeItem(live.clips[0])])
    proj = FakeProject("Fake", FakePool(root), [live_tl])
    plan = {
        "timeline": {"item_id": "t-proof", "name": CAPTAINS_PROOF_TIMELINE,
                     "folder": f"{bins.REELS_BIN}/{bins.REELS_PROOF_BIN}"},
        "bins": [{
            "path": (bins.SUBTITLES_BIN, "SOP Proof_min-canvas-rail"),
            "why": "test",
            "contents": [{"item_id": "c-p", "name": "proof_cap.mov",
                          "file_path": generated("p.mov"),
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
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    assert journal["timeline"]["name"] == CAPTAINS_PROOF_TIMELINE
    assert journal["removed_items"][0]["kind"] == "timeline"


def test_remove_proof_refuses_a_protected_timeline_even_when_planned(tmp_path):
    from library.tools.execution import remove_proof
    proj = live_pool()
    plan = {
        "timeline": {"item_id": "t-x", "name": LIVE, "folder": "test"},
        "bins": [],
        "verified_untouched": [],
    }
    with pytest.raises(ProofRemovalRefused, match="protected or not an"):
        remove_proof.remove_proof(proj, plan, str(tmp_path / "proof.json"))


# ----------------------------------------------- the apply-path hold-back


def test_apply_holds_dead_contents_back_and_retires_bin_with_them(tmp_path):
    """End to end on fakes: the filing pass must NOT re-home a dead
    reel's renders to Unplaced first - they leave the pool with the
    bin, and the result says what was held."""
    from library.tools.execution import organise_media_pool as ex
    gen = str(tmp_path / "pipeline_output" / "steps" /
              "4_05_render_subtitles")
    root = FakeFolder("Master", "root")
    root.clips.append(FakeClip("t-master", MASTER, kind="timeline"))
    root.clips.append(FakeClip("t-live", LIVE, kind="timeline"))
    six = FakeFolder(bins.SUBTITLES_BIN, "f6")
    dead = FakeFolder(DEAD, "f-dead")
    dead.clips.append(FakeClip("c-dead-a", "sub_dead_a.mov",
                               path=f"{gen}/a.mov"))
    six.subs.append(dead)
    root.subs.append(six)
    live_clip = FakeClip("c-live", "sub_live.mov",
                         path=f"{gen}/live.mov")
    root.clips.append(live_clip)
    proj = FakeProject("Fake", FakePool(root),
                       [FakeTimeline(LIVE, [FakeItem(live_clip)])])
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


def test_proof_journals_never_share_a_path(tmp_path):
    """Nine removals seconds apart journalled nine records, not five:
    the stamp collides, so the path disambiguates instead of
    overwriting."""
    from library.tools.execution import remove_proof

    first = remove_proof.journal_path_for(
        str(tmp_path), when="20260910T215238Z")
    Path(first).parent.mkdir(parents=True, exist_ok=True)
    Path(first).write_text("{}", encoding="utf-8")
    second = remove_proof.journal_path_for(
        str(tmp_path), when="20260910T215238Z")
    assert second != first
    assert second.endswith("_2.json")
    Path(second).write_text("{}", encoding="utf-8")
    third = remove_proof.journal_path_for(
        str(tmp_path), when="20260910T215238Z")
    assert third.endswith("_3.json")
