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
    FINAL_REEL_TIMELINE,
    MERGE_DEMO_TIMELINE,
    POSITIONING_SCRATCH_PREFIX,
    PRE_REBUILD_BACKUP_TIMELINE,
    PROTECTED_TIMELINES,
    SUPERSEDED_PLAIN_TIMELINE,
    SUPERSEDED_REACTION_TIMELINE,
    SUPERSEDED_REEL_TIMELINES,
    ProofRemovalRefused,
    discover_proof_bins,
    discover_scratch_timelines,
    discover_superseded_bins,
    discover_superseded_timelines,
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


def live_pool(project_root):
    """Fake pool shaped like the captain's: a dead leaf with two
    unplaced renders, a live leaf with one placed render."""
    root = FakeFolder("Master", "root")
    six = FakeFolder(bins.SUBTITLES_BIN, "f6")
    dead = FakeFolder(DEAD, "f-dead")
    dead.clips.append(FakeClip("c-dead-a", "sub_dead_a.mov",
                               path=generated(project_root, "a.mov")))
    dead.clips.append(FakeClip("c-dead-b", "sub_dead_b.mov",
                               path=generated(project_root, "b.mov")))
    live = FakeFolder(LIVE, "f-live")
    live_clip = FakeClip("c-live", "sub_live.mov", path=generated(project_root, "live.mov"))
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
    proj.timelines.append(FakeTimeline(LIVE, [FakeItem(dead.clips[0])]))
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

    proj = live_pool(project_root)
    root = proj.GetMediaPool().GetRootFolder()
    six = next(s for s in root.GetSubFolderList()
               if s.GetName() == bins.SUBTITLES_BIN)
    dead = next(s for s in six.GetSubFolderList() if s.GetName() == DEAD)
    changer = ChangingClip("c-changing", "changing.mov",
                           path=generated(project_root, "changing.mov"))
    dead.clips.append(changer)
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
    root = FakeFolder("Master", "root")
    reels = FakeFolder(bins.REELS_BIN, "f5")
    proof_bin = FakeFolder(bins.REELS_PROOF_BIN, "f-proof")
    proof_tl = FakeClip("t-proof", CAPTAINS_PROOF_TIMELINE, kind="timeline")
    proof_bin.clips.append(proof_tl)
    reels.subs.append(proof_bin)
    six = FakeFolder(bins.SUBTITLES_BIN, "f6")
    cap_bin = FakeFolder("SOP Proof_min-canvas-rail", "f-cap")
    cap_bin.clips.append(FakeClip("c-p", "proof_cap.mov",
                                  path=generated(project_root, "p.mov")))
    six.subs.append(cap_bin)
    live = FakeFolder(LIVE, "f-live")
    live.clips.append(FakeClip("c-live", "sub_live.mov",
                               path=generated(project_root, "live.mov")))
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
    from library.tools.execution.organise_media_pool import read_pool
    root = FakeFolder("Master", "root")
    six = FakeFolder(bins.SUBTITLES_BIN, "f6")
    picks = FakeFolder("my picks", "f-picks")
    picks.clips.append(FakeClip("c-pick", "sub_pick.mov",
                                path=generated(project_root, "pick.mov")))
    six.subs.append(picks)
    root.subs.append(six)
    proj = FakeProject("Fake", FakePool(root))
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
