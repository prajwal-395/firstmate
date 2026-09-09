"""The executor's own behaviour, against a media pool that is not Resolve.

The fake here answers the way the real API was measured to answer on
21.0.0b.28 - `AddSubFolder` makes a SECOND folder of a name that already
exists, `SetMetadata` refuses a key Resolve does not know, `MoveClips`
returns a bool - so a test failing here is a rule being broken and not
the double being wrong.  What the API actually does is written down in
`library/tools/execution/organise_media_pool.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from library.tools.execution import organise_media_pool as ex
from library.tools.resolve_organization import (
    BIN_REELS,
    BIN_SOURCE,
    BIN_SUBTITLES,
    CURRENT,
    STATE_BINS,
    OrganizationError,
)

RESOLVE_METADATA_KEYS = {
    "Comments", "Keywords", "Description", "Scene", "Shot", "Take", "Angle",
    "Reel Number", "Move", "Day / Night", "Camera #", "Production Name",
    "Episode Name", "Shot Type", "Environment", "Genre", "People", "Location",
}


class FakeClip:
    def __init__(self, uid, name, kind="clip", path=""):
        self.uid, self._name, self.kind, self.path = uid, name, kind, path
        self.metadata, self.color = {}, ""

    def GetUniqueId(self): return self.uid
    def GetName(self): return self._name

    def GetClipProperty(self, key):
        if key == "Type":
            return "Timeline" if self.kind == "timeline" else "Video + Audio"
        if key == "File Path":
            return self.path
        if key == "Clip Color":
            return self.color
        return ""

    def GetMetadata(self, key=None):
        return dict(self.metadata) if key is None else self.metadata.get(key, "")

    def SetMetadata(self, key, value):
        if key not in RESOLVE_METADATA_KEYS:
            return False          # measured: Resolve stores nothing
        self.metadata[key] = value
        return True

    def SetClipColor(self, value): self.color = value; return True
    def ClearClipColor(self): self.color = ""; return True


class FakeFolder:
    def __init__(self, name, uid):
        self._name, self.uid, self.clips, self.subs = name, uid, [], []

    def GetName(self): return self._name
    def GetUniqueId(self): return self.uid
    def GetClipList(self): return list(self.clips)
    def GetSubFolderList(self): return list(self.subs)


class FakePool:
    def __init__(self, root):
        self.root, self.current, self._n = root, root, 0

    def GetRootFolder(self): return self.root
    def GetCurrentFolder(self): return self.current
    def SetCurrentFolder(self, folder): self.current = folder; return True

    def AddSubFolder(self, parent, name):
        # Measured: Resolve does NOT check, it makes a second one.
        self._n += 1
        folder = FakeFolder(name, f"f{self._n}")
        parent.subs.append(folder)
        self.current = folder
        return folder

    def _home(self, clip):
        def walk(folder):
            if clip in folder.clips:
                return folder
            for sub in folder.subs:
                found = walk(sub)
                if found:
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

    def DeleteFolders(self, folders):
        # The guard the executor proves before calling: only an empty
        # bin is ever passed, so a non-empty one refuses here.
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


class FakeTimeline:
    def __init__(self, name, items): self._name, self.items = name, items
    def GetName(self): return self._name
    def GetTrackCount(self, kind): return 1 if kind == "video" else 0
    def GetItemListInTrack(self, kind, index):
        return self.items if kind == "video" else []


class FakeItem:
    def __init__(self, clip): self.clip = clip
    def GetMediaPoolItem(self): return self.clip


class FakeProject:
    def __init__(self, name, pool, timelines):
        self._name, self.pool, self.timelines = name, pool, timelines

    def GetName(self): return self._name
    def GetMediaPool(self): return self.pool
    def GetTimelineCount(self): return len(self.timelines)
    def GetTimelineByIndex(self, i): return self.timelines[i - 1]


MASTER = "Main Edit"


@pytest.fixture
def project(tmp_path):
    """A project directory and a pool shaped like the field test's."""
    root = FakeFolder("Master", "root")
    master_tl = FakeClip("t-master", MASTER, "timeline")
    live_tl = FakeClip("t-live", "Reel 01 - live", "timeline")
    old_tl = FakeClip("t-old", "Reel 09 - old", "timeline")
    cap = FakeClip("c-cap", "sub_a.mov",
                   path=str(tmp_path / "pipeline_output" / "a.mov"))
    orphan = FakeClip("c-orphan", "sub_b.mov",
                      path=str(tmp_path / "pipeline_output" / "b.mov"))
    source = FakeClip("c-src", "cam.mov", path="/elsewhere/cam.mov")
    root.clips = [master_tl, live_tl, old_tl, cap, orphan, source]

    timelines = [
        FakeTimeline(MASTER, [FakeItem(source)]),
        FakeTimeline("Reel 01 - live", [FakeItem(cap), FakeItem(source)]),
        FakeTimeline("Reel 09 - old", []),
    ]
    pool = FakePool(root)

    review = tmp_path / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / "plan_provenance.json").write_text(json.dumps({
        "plan_path": str(review / "reel_proposals_v2.json"),
        "plan_content_hash": "a" * 64,
        "built_at": "2026-09-07T00:00:00+00:00",
        "built_reels": ["Reel 01 - live"],
    }), encoding="utf-8")
    (review / "reel_proposals_v2_20260901T000000Z.json").write_text(
        json.dumps({"moments": [{"timeline_name": "Reel 09 - old"},
                                {"timeline_name": "Reel 01 - live"}]}),
        encoding="utf-8")
    (tmp_path / "project.yaml").write_text(
        "resolve:\n  project_name: Fake\n  timeline_name: Main Edit\n",
        encoding="utf-8")
    return FakeProject("Fake", pool, timelines), str(tmp_path)


def bins_of(folder, path=()):
    out = {}
    for clip in folder.GetClipList():
        out[clip.GetName()] = "/".join(path)
    for sub in folder.GetSubFolderList():
        out.update(bins_of(sub, path + (sub.GetName(),)))
    return out


# ------------------------------------------------------------- reading


def test_read_pool_measures_which_timeline_places_what(project):
    proj, _folder = project
    artefacts, duplicates, _recorded, root_name = ex.read_pool(proj)
    by_name = {a.name: a for a in artefacts}
    assert by_name["sub_a.mov"].placed_by == ("Reel 01 - live",)
    assert by_name["sub_b.mov"].placed_by == ()
    assert by_name["cam.mov"].placed_by == (MASTER, "Reel 01 - live")
    assert by_name[MASTER].kind == "timeline"
    assert duplicates == [] and root_name == "Master"


def test_read_pool_reports_a_duplicate_bin(project):
    proj, _folder = project
    pool = proj.GetMediaPool()
    pool.AddSubFolder(pool.GetRootFolder(), "Reels")
    pool.AddSubFolder(pool.GetRootFolder(), "Reels")
    _, duplicates, _, _ = ex.read_pool(proj)
    assert duplicates == ["Reels"]


# ------------------------------------------------------------ applying


def test_apply_files_everything_where_the_evidence_says(project):
    proj, folder = project
    result = ex.organise_project(proj, folder, MASTER, apply=True)
    where = bins_of(proj.GetMediaPool().GetRootFolder())
    assert where["Reel 01 - live"] == f"{BIN_REELS}/{STATE_BINS['current']}"
    assert where["Reel 09 - old"] == f"{BIN_REELS}/{STATE_BINS['earlier']}"
    assert where["sub_a.mov"] == f"{BIN_SUBTITLES}/Reel 01 - live"
    assert where["sub_b.mov"] == f"{BIN_SUBTITLES}/Not placed on any timeline"
    assert where["cam.mov"] == BIN_SOURCE
    assert where[MASTER] == ""          # the master is never moved
    assert result["applied"]


def test_apply_stamps_the_reels_with_the_plan_that_built_them(project):
    proj, folder = project
    ex.organise_project(proj, folder, MASTER, apply=True)
    live = next(c for c in _all_clips(proj) if c.GetName() == "Reel 01 - live")
    assert f"state={CURRENT}" in live.GetMetadata("Keywords")
    assert "plan=aaaaaaaaaaaa" in live.GetMetadata("Keywords")
    assert live.GetClipProperty("Clip Color") == "Green"
    master = next(c for c in _all_clips(proj) if c.GetName() == MASTER)
    assert master.GetMetadata("Keywords") == ""
    assert master.GetClipProperty("Clip Color") == ""


def _all_clips(proj):
    def walk(folder):
        yield from folder.GetClipList()
        for sub in folder.GetSubFolderList():
            yield from walk(sub)
    return list(walk(proj.GetMediaPool().GetRootFolder()))


def test_applying_twice_moves_nothing_the_second_time(project):
    proj, folder = project
    first = ex.organise_project(proj, folder, MASTER, apply=True)
    before = bins_of(proj.GetMediaPool().GetRootFolder())
    second = ex.organise_project(proj, folder, MASTER, apply=True)
    assert len(first["journal"]["moves"]) > 0
    assert second["journal"]["moves"] == []
    assert bins_of(proj.GetMediaPool().GetRootFolder()) == before


def test_a_bin_is_never_created_twice(project):
    """`AddSubFolder` does not check, so `ensure_folder` must."""
    proj, folder = project
    ex.organise_project(proj, folder, MASTER, apply=True)
    ex.organise_project(proj, folder, MASTER, apply=True)
    _, duplicates, _, _ = ex.read_pool(proj)
    assert duplicates == []


def test_apply_restores_the_current_folder_it_found(project):
    """`AddSubFolder` sets the current folder, and the current folder is
    where `CreateEmptyTimeline` puts the next timeline."""
    proj, folder = project
    pool = proj.GetMediaPool()
    before = pool.GetCurrentFolder()
    ex.organise_project(proj, folder, MASTER, apply=True)
    assert pool.GetCurrentFolder() is before


def test_the_check_passes_once_it_is_organised_and_fails_before(project):
    proj, folder = project
    assert ex.check_project(proj, folder, MASTER)          # fails dirty
    ex.organise_project(proj, folder, MASTER, apply=True)
    assert ex.check_project(proj, folder, MASTER) == []    # passes clean


def test_the_check_fails_a_timeline_moved_to_the_wrong_bin(project):
    proj, folder = project
    ex.organise_project(proj, folder, MASTER, apply=True)
    pool = proj.GetMediaPool()
    earlier = ex._find_path(pool.GetRootFolder(),
                            (BIN_REELS, STATE_BINS["earlier"]))
    live = next(c for c in _all_clips(proj) if c.GetName() == "Reel 01 - live")
    pool.MoveClips([live], earlier)
    found = ex.check_project(proj, folder, MASTER)
    assert [f["kind"] for f in found] == ["misfiled"]


# ------------------------------------------------------------ the journal


def test_every_apply_writes_its_own_journal_and_overwrites_none(project):
    proj, folder = project
    first = ex.organise_project(proj, folder, MASTER, apply=True,
                                journal_path=ex.journal_path_for(folder, "A"))
    second = ex.organise_project(proj, folder, MASTER, apply=True,
                                 journal_path=ex.journal_path_for(folder, "B"))
    assert first["journal"]["journal_path"] != second["journal"]["journal_path"]
    listed = ex.journals(folder)
    assert [entry["moves"] for entry in listed] == [0, len(first["journal"]["moves"])]
    assert all(entry["reverted_at"] is None for entry in listed)


def test_a_second_apply_cannot_destroy_the_first_journals_undo(project):
    """The defect this closes: with one fixed filename, the second apply
    moves nothing, writes that over the first record, and the whole
    organisation becomes irreversible."""
    proj, folder = project
    ex.organise_project(proj, folder, MASTER, apply=True,
                        journal_path=ex.journal_path_for(folder, "A"))
    ex.organise_project(proj, folder, MASTER, apply=True,
                        journal_path=ex.journal_path_for(folder, "B"))
    undone = ex.revert(proj, ex.journal_path_for(folder, "A"))
    assert len(undone["moved_back"]) > 0
    assert bins_of(proj.GetMediaPool().GetRootFolder())["Reel 01 - live"] == ""


def test_revert_puts_every_item_and_every_stamp_back(project):
    proj, folder = project
    before = bins_of(proj.GetMediaPool().GetRootFolder())
    result = ex.organise_project(proj, folder, MASTER, apply=True)
    ex.revert(proj, result["journal"]["journal_path"])
    assert bins_of(proj.GetMediaPool().GetRootFolder()) == before
    for clip in _all_clips(proj):
        assert clip.GetMetadata("Keywords") == ""
        assert clip.GetClipProperty("Clip Color") == ""


def test_revert_reports_the_bins_it_will_not_delete(project):
    proj, folder = project
    result = ex.organise_project(proj, folder, MASTER, apply=True)
    undone = ex.revert(proj, result["journal"]["journal_path"])
    assert f"{BIN_REELS}/{STATE_BINS['current']}" in undone["bins_left_behind"]
    assert BIN_REELS in undone["bins_left_behind"]


def test_reverting_the_same_journal_twice_refuses(project):
    proj, folder = project
    result = ex.organise_project(proj, folder, MASTER, apply=True)
    path = result["journal"]["journal_path"]
    ex.revert(proj, path)
    with pytest.raises(OrganizationError, match="already reverted"):
        ex.revert(proj, path)


def test_a_journal_is_written_even_when_the_apply_raises(project, monkeypatch):
    """A half-applied move nobody recorded is the one outcome with no
    way back."""
    proj, folder = project
    pool = proj.GetMediaPool()
    calls = {"n": 0}
    real_move = pool.MoveClips

    def flaky(clips, target):
        calls["n"] += 1
        if calls["n"] > 1:
            return False
        return real_move(clips, target)

    monkeypatch.setattr(pool, "MoveClips", flaky)
    path = ex.journal_path_for(folder, "X")
    with pytest.raises(OrganizationError, match="MoveClips"):
        ex.organise_project(proj, folder, MASTER, apply=True,
                            journal_path=path)
    written = json.loads(Path(path).read_text(encoding="utf-8"))
    assert written["moves"], "the moves that DID happen were not journalled"

    monkeypatch.setattr(pool, "MoveClips", real_move)
    ex.revert(proj, path)
    moved = {m["name"] for m in written["moves"]}
    where = bins_of(pool.GetRootFolder())
    assert all(where[name] == "" for name in moved), where


def test_a_project_that_names_no_master_timeline_refuses(project):
    _proj, folder = project
    Path(folder, "project.yaml").write_text(
        "resolve:\n  project_name: Fake\n", encoding="utf-8")
    with pytest.raises(OrganizationError, match="timeline_name"):
        ex.open_project(folder)


# ------------------------------------------ what nothing plays, and its cost


def test_the_unplaced_cost_is_measured_from_disk_not_assumed(project,
                                                             tmp_path):
    """The orphan's file is real here, so the size is a `stat` and not a
    guess. AGENTS.md 10.3: a file on disk is not a measurement - but its
    SIZE is, and it is the figure a removal decision turns on."""
    orphan = tmp_path / "pipeline_output" / "b.mov"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"x" * 4096)
    proj, folder = project
    result = ex.organise_project(proj, folder, MASTER, apply=True)
    cost = result["unplaced"]
    assert cost["count"] == 1
    assert cost["on_disk"] == 1 and cost["missing"] == 0
    assert cost["bytes_on_disk"] == 4096
    assert cost["shared_with_placed"] == ()


def test_an_unplaced_item_whose_file_is_gone_is_counted_as_offline(project):
    """131 of the field test's 1,216 point at a file that no longer
    exists. Keeping the pool item recovers nothing, and a report that
    sized it as recoverable disk would be wrong by that much."""
    proj, folder = project
    cost = ex.organise_project(proj, folder, MASTER,
                               apply=True)["unplaced"]
    assert cost["count"] == 1
    assert cost["on_disk"] == 0 and cost["missing"] == 1
    assert cost["bytes_on_disk"] == 0


def test_the_check_reports_the_unplaced_burden_and_does_not_fail_on_it(
        project):
    """Both halves of AGENTS.md 10.4 in one assertion: the survey finds
    the burden AND the findings list stays empty once filed, because a
    superseded render is filed exactly where its evidence puts it."""
    proj, folder = project
    ex.organise_project(proj, folder, MASTER, apply=True)
    survey = ex.survey_project(proj, folder, MASTER)
    assert survey["findings"] == []
    assert survey["unplaced"]["count"] == 1


def test_the_unplaced_report_reads_as_sentences_and_can_say_none(project,
                                                                 tmp_path):
    proj, folder = project
    (tmp_path / "pipeline_output" / "b.mov").write_bytes(b"y" * 2048)
    cost = ex.organise_project(proj, folder, MASTER,
                               apply=True)["unplaced"]
    text = ex.render_unplaced(cost)
    assert "on NO timeline" in text and "Not placed on any timeline" in text
    assert "captain" in text
    empty = ex.render_unplaced(dict(cost, count=0))
    assert "Nothing this pipeline generated is unplaced" in empty


def test_the_unplaced_report_separates_a_file_a_placed_item_also_uses(
        project, tmp_path):
    """`pool.ImportMedia` made a second pool item for a path already in
    the pool three times on the field test. Removing that ITEM is safe;
    deleting the FILE takes media off a live timeline."""
    shared = tmp_path / "pipeline_output" / "a.mov"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_bytes(b"z" * 8192)
    proj, folder = project
    root = proj.GetMediaPool().GetRootFolder()
    root.clips.append(FakeClip("c-dup", "sub_a.mov", path=str(shared)))
    cost = ex.organise_project(proj, folder, MASTER,
                               apply=True)["unplaced"]
    assert cost["shared_with_placed"] == (str(shared),)
    assert cost["bytes_shared"] == 8192
    assert "PLACED item ALSO uses" in ex.render_unplaced(cost)


# --------------------------------------- retiring the migration's shells


def _shell_pool(project):
    """The captain's complaint in miniature: the migration emptied the
    legacy shells and left them standing beside the numbered bins."""
    proj, _folder = project
    pool = proj.GetMediaPool()
    root = pool.GetRootFolder()
    shells = FakeFolder("Reels", "legacy-reels")
    shells.subs.append(FakeFolder("Unrecorded", "legacy-unrec"))
    root.subs.append(shells)
    root.subs.append(FakeFolder("Reel subtitles", "legacy-subs"))
    root.subs.append(FakeFolder("My selects", "captain"))
    return proj


def _tree_names(proj):
    out = []

    def walk(folder, path):
        for sub in folder.GetSubFolderList():
            out.append("/".join(path + (sub.GetName(),)))
            walk(sub, path + (sub.GetName(),))
    walk(proj.GetMediaPool().GetRootFolder(), ())
    return sorted(out)


def test_the_check_flags_an_empty_legacy_shell(project):
    proj = _shell_pool(project)
    kinds = [f["kind"] for f in ex.check_project(proj, project[1], MASTER)]
    assert "empty_legacy_bin" in kinds


def test_apply_retires_the_shells_and_the_check_passes_after(project):
    """The migration's two-pass shape: read-only plan first, then act,
    then re-read and reconcile."""
    proj = _shell_pool(project)
    _, folder = project
    planned = ex.organise_project(proj, folder, MASTER, apply=False)
    assert not planned["applied"]
    assert "Reels" in planned["census"] and "RETIRE" in planned["census"]
    assert "My selects" in planned["census"]
    assert "Reels" in _tree_names(proj), "planning must change nothing"

    applied = ex.organise_project(proj, folder, MASTER, apply=True)
    assert applied["applied"]
    assert applied["retirement"]["retired"] == [
        "Reels/Unrecorded", "Reel subtitles", "Reels"]
    remaining = _tree_names(proj)
    assert "Reels" not in remaining
    assert "Reels" not in remaining
    assert "Reel subtitles" not in remaining
    assert "My selects" in remaining
    assert ex.check_project(proj, folder, MASTER) == []


def test_a_shell_holding_the_captains_tier_is_left_where_it_is(project):
    """`Reels/Fully approved` is the captain's organisation: the tier
    stays, and `Reels` above it stays too - retiring the parent would
    take the captain's bin with it."""
    proj = _shell_pool(project)
    _, folder = project
    pool = proj.GetMediaPool()
    shells = next(s for s in pool.GetRootFolder().GetSubFolderList()
                  if s.GetName() == "Reels")
    tier = FakeFolder("Fully approved", "captain-tier")
    tier.clips.append(FakeClip("t-tier", "Reel 01 - mine", "timeline"))
    shells.subs.append(tier)
    result = ex.organise_project(proj, folder, MASTER, apply=True)
    assert result["retirement"]["retired"] == [
        "Reels/Unrecorded", "Reel subtitles"]
    remaining = _tree_names(proj)
    assert "Reels" in remaining
    assert "Reels/Fully approved" in remaining
