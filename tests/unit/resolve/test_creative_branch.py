"""Branches and merges: the commit graph over creative state, offline.

Each test names a defect the branch or merge closes:

- a branch that starts from a double that already diverged from its
  source, so the branch's history begins with a change nobody made;
- a branch that does not record its base, so it cannot be merged back
  and "what did this branch change" has no answer;
- a merge that loses one side's changes, so the merged timeline is
  missing an edit one branch made;
- a merge that silently combines two commits that change the same clip,
  so the captain's edit on one branch is overwritten by the other's;
- a merge that refuses a fast-forward, so a chain of touches on one
  reel cannot be merged at all;
- a merge commit that records one parent, so the merged branch's
  history is lost and a revert cannot tell what came from where.

Branches and merges are recorded offline under
`pipeline_output/review/` as versioned text, like the commits, so every
project is under `tmp_path` and no Resolve is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import timeline_diff
from library.tools import undo_journal as uj


def _project(tmp_path) -> str:
    folder = Path(tmp_path) / "project"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _tracks_with(clip_name, record_in=0, record_out=100):
    """A track read with one clip at a given span."""
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": clip_name, "record_in": record_in,
         "record_out": record_out, "duration": record_out - record_in,
         "source_in_frame": 0,
         "source_out_frame": record_out - record_in,
         "left_offset": 0, "right_offset": 0},
    ]}]


def _move_change(clip_name, new_start):
    """A real `timeline_diff` move change for one clip."""
    return timeline_diff.diff_timelines(
        _tracks_with(clip_name, 0, 100),
        _tracks_with(clip_name, new_start, new_start + 100))


def _root(project):
    return uj.record_commit(
        project, parent=None, intent="first touch", changes=[],
        agent="reel_touchup", timeline="Reel 01 - lab")


# ── Branches ────────────────────────────────────────────────────────

def test_a_clean_double_proves_the_branch(tmp_path):
    """A branch's double must read identical to its source.

    The defect: a branch materialized from a double that already differs
    from its source begins its history with a change nobody made - the
    branch diff against its base reports a change that is not one. A
    fresh DuplicateTimeline is identical to its source, and the branch
    is proven against that: the diff must be empty.
    """
    project = _project(tmp_path)
    root = _root(project)
    source = _tracks_with("clip_001")
    branch = uj.record_branch(
        project, name="staging", base=root["id"],
        timeline="Reel 01 - lab", note="touch j1",
        source_tracks=source, branch_tracks=_tracks_with("clip_001"))
    assert branch["base"] == root["id"]
    assert branch["timeline"] == "Reel 01 - lab"
    assert uj.read_branch(project, "staging")["base"] == root["id"]


def test_a_diverged_double_is_refused(tmp_path):
    """A double that already differs from its source cannot branch.

    The defect: recording a branch from a diverged double lets the
    branch start with a phantom change - the diff against its base is
    not empty, so "what did this branch change" includes a change no
    one made. The branch is refused, naming the difference.
    """
    project = _project(tmp_path)
    root = _root(project)
    try:
        uj.record_branch(
            project, name="staging", base=root["id"],
            timeline="Reel 01 - lab",
            source_tracks=_tracks_with("clip_001"),
            branch_tracks=_tracks_with("clip_001", 50, 150))
        assert False, "a diverged double must be refused"
    except uj.BranchRefused as refused:
        assert "clip_001" in str(refused)
    assert uj.list_branches(project) == []


def test_a_branch_records_its_base_so_it_can_be_merged_back(tmp_path):
    """A branch names the commit it stands on.

    The defect: a branch with no base cannot be merged back - there is
    no common ancestor to diff against, and "what did this branch
    change" has no answer. The branch record names its base commit.
    """
    project = _project(tmp_path)
    root = _root(project)
    branch = uj.record_branch(
        project, name="staging", base=root["id"],
        timeline="Reel 01 - lab", note="touch j1")
    assert branch["base"] == root["id"]
    branches = uj.list_branches(project)
    assert [b["name"] for b in branches] == ["staging"]


def test_semantic_branch_starts_unmaterialized_and_keeps_history_after_discard(tmp_path):
    """Branch declarations and a Resolve projection have separate lifetimes."""
    project = _project(tmp_path)
    root = _root(project)
    branch = uj.create_creative_branch(
        project, name="variant/alt-intro",
        base_revision=root["revision_id"],
        semantic_changes=[{"op": "retime_block", "node_id": "intro"}],
        declarations={"path": "external/declarations/intro.json"},
        git_branch="variant/alt-intro", intent="try a faster intro")

    assert branch["base_revision"] == root["revision_id"]
    assert branch["semantic_changes"] == [
        {"op": "retime_block", "node_id": "intro"}]
    assert branch["materialization"] is None
    assert "timeline" not in branch
    assert uj.read_branch(project, "variant/alt-intro")["git_branch"] == \
        "variant/alt-intro"

    uj.record_branch_materialization(
        project, "variant/alt-intro", timeline="Reel 01 - alt",
        timeline_id="resolve-id", revision_id=branch["revision_id"])
    discarded = uj.discard_branch_materialization(project, "variant/alt-intro")
    assert discarded["materialization"]["status"] == "discarded"
    assert discarded["materialization"]["timeline_id"] == "resolve-id"
    assert uj.read_branch(project, "variant/alt-intro")["revision_id"] == \
        branch["revision_id"]


# ── Merges ──────────────────────────────────────────────────────────

def test_a_merge_of_non_overlapping_changes_is_the_union(tmp_path):
    """Two commits that change different clips merge into both changes.

    The defect: a merge that keeps only one side's changes silently
    drops the other branch's edit - the merged timeline is missing a
    change one branch made. The merged changes are the union of both
    sides since their common ancestor.
    """
    project = _project(tmp_path)
    root = _root(project)
    left = uj.record_commit(
        project, parent=root["id"], intent="move clip_001",
        changes=_move_change("clip_001", 50), agent="reel_touchup",
        timeline="Reel 01 - lab")
    right = uj.record_commit(
        project, parent=root["id"], intent="move clip_002",
        changes=_move_change("clip_002", 60), agent="reel_touchup",
        timeline="Reel 01 - lab")
    merge = uj.merge_commits(
        project, timeline="Reel 01 - lab",
        left=left["id"], right=right["id"])
    merged_items = {c["item"] for c in merge["changes"]}
    assert merged_items == {"clip_001", "clip_002"}
    # The merged changes are timeline_diff changes, not a guess.
    assert all(c["op"] == timeline_diff.OP_MOVE for c in merge["changes"])


def test_a_merge_commit_records_both_parents(tmp_path):
    """A merge commit names the branch it merged in.

    The defect: a merge commit with one parent loses the merged branch's
    history - a revert cannot tell what came from where, and the merged
    branch's commits are orphaned. The merge commit records both parents.
    """
    project = _project(tmp_path)
    root = _root(project)
    left = uj.record_commit(
        project, parent=root["id"], intent="move clip_001",
        changes=_move_change("clip_001", 50), agent="reel_touchup",
        timeline="Reel 01 - lab")
    right = uj.record_commit(
        project, parent=root["id"], intent="move clip_002",
        changes=_move_change("clip_002", 60), agent="reel_touchup",
        timeline="Reel 01 - lab")
    merge = uj.merge_commits(
        project, timeline="Reel 01 - lab",
        left=left["id"], right=right["id"])
    assert merge["parent"] == left["id"]
    assert merge["second_parent"] == right["id"]
    assert merge["agent"] == "creative_merge"


def test_a_conflicting_merge_is_refused_by_name(tmp_path):
    """Two commits that change the same clip cannot be merged.

    The defect: a merge that silently combines two commits that change
    the same clip overwrites one with the other - the captain's edit on
    one branch is lost without a word. The merge is refused, naming the
    clips both sides changed.
    """
    project = _project(tmp_path)
    root = _root(project)
    left = uj.record_commit(
        project, parent=root["id"], intent="move clip_001 to 50",
        changes=_move_change("clip_001", 50), agent="reel_touchup",
        timeline="Reel 01 - lab")
    right = uj.record_commit(
        project, parent=root["id"], intent="move clip_001 to 60",
        changes=_move_change("clip_001", 60), agent="reel_touchup",
        timeline="Reel 01 - lab")
    try:
        uj.merge_commits(
            project, timeline="Reel 01 - lab",
            left=left["id"], right=right["id"])
        assert False, "a conflicting merge must be refused"
    except uj.MergeConflict as conflict:
        assert "clip_001" in str(conflict)
    # No merge commit was recorded.
    assert all(c.get("second_parent") is None
               for c in uj.list_commits(project))


def test_changes_to_different_properties_of_one_entity_compose(tmp_path):
    project = _project(tmp_path)
    root = _root(project)
    moved = uj.record_commit(
        project, parent=root["id"], intent="move", changes=_move_change(
            "clip_001", 50), agent="reel_touchup", timeline="Reel 01 - lab")
    trimmed = timeline_diff.diff_timelines(
        _tracks_with("clip_001"),
        [{"type": "video", "index": 1, "name": "V1", "clips": [{
            "name": "clip_001", "record_in": 0, "record_out": 100,
            "duration": 100, "source_in_frame": 10,
            "source_out_frame": 110, "left_offset": 0, "right_offset": 0}]}])
    trimmed = uj.record_commit(
        project, parent=root["id"], intent="trim", changes=trimmed,
        agent="reel_touchup", timeline="Reel 01 - lab")

    merge = uj.merge_commits(project, timeline="Reel 01 - lab",
                             left=moved["id"], right=trimmed["id"])

    assert {change["op"] for change in merge["changes"]} == {
        timeline_diff.OP_MOVE, timeline_diff.OP_TRIM}
    assert merge["artifacts"]["materialization"] == "semantic_only"


def test_delete_versus_modify_is_a_property_aware_conflict(tmp_path):
    project = _project(tmp_path)
    root = _root(project)
    removed = timeline_diff.diff_timelines(
        _tracks_with("clip_001"),
        [{"type": "video", "index": 1, "name": "V1", "clips": []}])
    left = uj.record_commit(project, parent=root["id"], intent="remove",
                            changes=removed, agent="reel_touchup",
                            timeline="Reel 01 - lab")
    right = uj.record_commit(project, parent=root["id"], intent="move",
                             changes=_move_change("clip_001", 50),
                             agent="reel_touchup", timeline="Reel 01 - lab")

    try:
        uj.merge_commits(project, timeline="Reel 01 - lab",
                         left=left["id"], right=right["id"])
    except uj.MergeConflict as conflict:
        assert "clip_001" in str(conflict)
        assert conflict.args[1][0]["property"] == "$entity"
    else:
        raise AssertionError("delete versus modify must refuse the merge")


def test_merge_materialization_records_only_verified_candidate(tmp_path):
    project = _project(tmp_path)
    root = _root(project)
    left = uj.record_commit(project, parent=root["id"], intent="left",
                            changes=_move_change("clip_001", 50),
                            agent="reel_touchup", timeline="Reel 01 - lab")
    right = uj.record_commit(project, parent=root["id"], intent="right",
                             changes=_move_change("clip_002", 60),
                             agent="reel_touchup", timeline="variant")
    proposal = uj.merge_commits(project, timeline="Reel 01 - lab",
                                left=left["id"], right=right["id"])

    class Timeline:
        def __init__(self, name, unique_id):
            self.name, self.unique_id = name, unique_id

        def GetName(self):
            return self.name

        def GetUniqueId(self):
            return self.unique_id

    source = Timeline("Reel 01 - lab", "source-id")
    candidate = Timeline("merge-candidate", "candidate-id")
    applied = []
    result = uj.materialize_merge(
        project, proposal, source=source, candidate=candidate,
        revalidate=lambda _source, _candidate, _changes: {
            "targets": True, "preconditions": True,
            "base_commit": left["id"]},
        apply=lambda timeline, changes: applied.extend(changes),
        verify=lambda _source, _candidate, _changes: {
            "modified": True, "preserved": True, "native_readback": "read-42",
            "base_commit": left["id"],
            "visuals_uncertain": True,
            "modified_components": ["timeline_structure"],
            "preserved_components": ["markers", "grades"]},
        promote=lambda _candidate: {"promoted": ["Reel 01 - lab"]},
        approve=lambda _evidence: {"approved": True,
                                   "review_id": "visual-review-1"})

    assert len(applied) == 1
    assert len(result["artifacts"]["merge_result_changes"]) == 2
    assert result["artifacts"]["materialization"] == "verified"
    assert result["parent"] == left["id"]
    assert result["second_parent"] == right["id"]
    assert result["result_revision"] == result["revision_id"]
    assert result["artifacts"]["verification"]["approval"]["approved"]


def test_a_fast_forward_merge_is_the_descendant(tmp_path):
    """A chain of touches on one reel merges forward, not refused.

    The defect: a merge that treats a fast-forward as a conflict (or
    refuses it) means a chain of touches on one reel cannot be merged
    at all - every touch would be a "conflict" with the last. When one
    commit is the ancestor of the other, the merge is the descendant.
    """
    project = _project(tmp_path)
    root = _root(project)
    child = uj.record_commit(
        project, parent=root["id"], intent="move clip_001",
        changes=_move_change("clip_001", 50), agent="reel_touchup",
        timeline="Reel 01 - lab")
    merge = uj.merge_commits(
        project, timeline="Reel 01 - lab",
        left=root["id"], right=child["id"])
    assert merge["parent"] == root["id"]
    assert merge["second_parent"] == child["id"]
    assert {c["item"] for c in merge["changes"]} == {"clip_001"}


def test_a_merge_with_no_common_ancestor_is_refused(tmp_path):
    """Two commits that share no ancestor cannot be merged.

    The defect: merging two unrelated commits would invent a common
    ancestor that does not exist, so the diff against it is meaningless.
    The merge is refused.
    """
    project = _project(tmp_path)
    left = uj.record_commit(
        project, parent=None, intent="move clip_001",
        changes=_move_change("clip_001", 50), agent="reel_touchup",
        timeline="Reel 01 - lab")
    right = uj.record_commit(
        project, parent=None, intent="move clip_002",
        changes=_move_change("clip_002", 60), agent="reel_touchup",
        timeline="Reel 01 - lab")
    try:
        uj.merge_commits(
            project, timeline="Reel 01 - lab",
            left=left["id"], right=right["id"])
        assert False, "a merge with no common ancestor must be refused"
    except uj.MergeConflict as conflict:
        assert "no common ancestor" in str(conflict)
