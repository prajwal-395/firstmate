"""Revert a creative commit: record its inverse as a new commit.

Each test names a defect the revert closes:

- an inverted move that still carries the NEW start in its
  `retime_block` mutation, so reverting re-applies the move instead of
  undoing it;
- an inverted add that stays an add, so reverting an addition keeps it;
- a lossy inversion, so inverting twice does not return the original;
- a whole-row add reverted as clean, so the revert claims a full undo
  while silently dropping the row change the record cannot carry;
- a revert that rewrites history instead of standing on the head, so
  the reverted commit is no longer readable beside its revert;
- a revert of a commit whose clips later commits also changed, so
  restoring the old spans silently undoes the later touch;
- an over-strict revert that refuses a commit no later commit
  touches, blocking a legitimate undo;
- a reverted MERGE, whose changes are the union of both sides, so the
  main line is silently reverted too;
- a revert of a commit outside its timeline's line, so the revert
  stands on a history that does not contain it;
- a `ren revert` for an id nothing answers that succeeds, so a revert
  of nothing is recorded;
- a verb registered in only one of the two front doors (`ren` Python
  vs the native CLI), so it is unreachable even though it is
  implemented.

Reverts are recorded offline under `pipeline_output/review/` as
versioned text, like the commits, so every project is under `tmp_path`
and no Resolve is touched. Commit ids are whole: unique-prefix
addressing belongs to the history reads, and this slice stays exact
so the two never duplicate addressing logic.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import timeline_diff  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402
from library.tools import creative_revert as revert_verb  # noqa: E402
from ren import commands as ren_commands  # noqa: E402


def _project(tmp_path) -> str:
    folder = Path(tmp_path) / "project"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "project.yaml").write_text("name: Revert\nslug: revert\n",
                                         encoding="utf-8")
    return str(folder)


def _tracks_with(clip_name, record_in=0, record_out=100,
                 source_in=0, left_offset=0):
    """A track read with one clip at a given span and source."""
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": clip_name, "record_in": record_in,
         "record_out": record_out, "duration": record_out - record_in,
         "source_in_frame": source_in,
         "source_out_frame": source_in + (record_out - record_in),
         "left_offset": left_offset, "right_offset": 0},
    ]}]


def _commit(project, timeline, changes, parent=None, intent="tighten opening"):
    return uj.record_commit(
        project, parent=parent, intent=intent, changes=changes,
        agent="reel_touchup", timeline=timeline)


def test_inverted_move_points_its_mutation_at_the_restored_start():
    """The inverse move carries the OLD start, not the new one.

    The defect: swapping before/after but leaving the `retime_block`
    mutation pointing at the new start, so applying the revert
    re-applies the move it claims to undo.
    """
    changes = timeline_diff.diff_timelines(
        _tracks_with("clip_001", 0, 100), _tracks_with("clip_001", 50, 150))
    assert [c["op"] for c in changes] == ["move"]
    inverse = timeline_diff.invert_changes(changes)
    assert inverse[0]["before"]["start"] == 50
    assert inverse[0]["after"]["start"] == 0
    assert inverse[0]["mutation"] == {"op": "retime_block",
                                      "timeline_start": 0}


def _tracks_with_clips(*names):
    """One V1 row holding each named clip at 0-100."""
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": name, "record_in": 0, "record_out": 100, "duration": 100,
         "source_in_frame": 0, "source_out_frame": 100,
         "left_offset": 0, "right_offset": 0}
        for name in names]}]


def test_inverted_add_removes_and_inverted_remove_adds():
    """An add inverts to a remove, a remove to an add.

    The defect: flipping nothing, so reverting an addition keeps the
    clip and reverting a removal keeps it gone.
    """
    added = timeline_diff.diff_timelines(
        _tracks_with_clips("clip_001"),
        _tracks_with_clips("clip_001", "clip_002"))
    assert [c["op"] for c in added] == ["add"]
    unadded = timeline_diff.invert_changes(added)
    assert [c["op"] for c in unadded] == ["remove"]
    assert unadded[0]["item"] == "clip_002"
    assert unadded[0]["before"]["start"] == 0
    assert unadded[0]["after"] is None
    assert unadded[0]["mutation"] == {"op": "remove_node"}
    removed = timeline_diff.diff_timelines(
        _tracks_with_clips("clip_001", "clip_002"),
        _tracks_with_clips("clip_001"))
    assert [c["op"] for c in removed] == ["remove"]
    unremoved = timeline_diff.invert_changes(removed)
    assert [c["op"] for c in unremoved] == ["add"]
    assert unremoved[0]["item"] == "clip_002"
    assert unremoved[0]["before"] is None
    assert unremoved[0]["after"]["start"] == 0
    assert unremoved[0]["mutation"] is None


def test_inversion_round_trips():
    """Inverting twice returns the original changes.

    The defect: a lossy inversion that drops fields, so a revert of a
    revert is not the change it started from.
    """
    changes = timeline_diff.diff_timelines(
        _tracks_with("clip_001", 0, 100), _tracks_with("clip_001", 50, 150))
    assert timeline_diff.invert_changes(
        timeline_diff.invert_changes(changes)) == changes


def test_row_level_change_is_uninvertible_by_name():
    """A whole-row add refuses naming its op and row.

    The defect: reverting it as clean. The record carries a clip
    COUNT, not the clips, so no inverse exists and the revert must
    say what it cannot undo.
    """
    whole = timeline_diff.diff_timelines([], _tracks_with("clip_009"))
    assert whole[0]["item"] == ""
    try:
        timeline_diff.invert_changes(whole)
    except timeline_diff.Uninvertible as uninvertible:
        assert "add" in str(uninvertible)
    else:
        raise AssertionError("a whole-row add inverted without error")


def test_revert_stands_on_the_head_and_leaves_the_target_readable(tmp_path):
    """The revert's parent is the head; the target is untouched.

    The defect: a revert that rewrites history instead of appending to
    it, so the reverted commit is no longer readable beside its
    revert.
    """
    project = _project(tmp_path)
    target = _commit(
        project, "Reel 1", timeline_diff.diff_timelines(
            _tracks_with("clip_001", 0, 100),
            _tracks_with("clip_001", 50, 150)))
    reverted = uj.revert_commit(project, target["id"])
    assert reverted["parent"] == target["id"]
    assert reverted["agent"] == "creative_revert"
    assert reverted["artifacts"] == {"reverts": target["id"]}
    assert uj.read_commit(project, target["id"])["intent"] == \
        "tighten opening"
    assert uj.latest_commit_for_timeline(
        project, "Reel 1")["id"] == reverted["id"]


def test_revert_refuses_a_clip_later_commits_also_changed(tmp_path):
    """A later touch on the same clip blocks the revert, by name.

    The defect: restoring the old span over the later touch, so the
    revert silently undoes work that came after.
    """
    project = _project(tmp_path)
    target = _commit(
        project, "Reel 1", timeline_diff.diff_timelines(
            _tracks_with("clip_001", 0, 100),
            _tracks_with("clip_001", 50, 150)))
    _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_001", 50, 150),
        _tracks_with("clip_001", 70, 170)), parent=target["id"])
    try:
        uj.revert_commit(project, target["id"])
    except uj.RevertRefused as refused:
        assert "clip_001" in str(refused)
    else:
        raise AssertionError("revert over a later touch was recorded")


def test_revert_allows_a_commit_no_later_commit_touches(tmp_path):
    """A commit beside later work on other clips still reverts.

    The defect the previous test's guard could introduce: refusing
    every non-head revert, blocking a legitimate undo of an
    independent change.
    """
    project = _project(tmp_path)
    target = _commit(
        project, "Reel 1", timeline_diff.diff_timelines(
            _tracks_with("clip_001", 0, 100),
            _tracks_with("clip_001", 50, 150)))
    head = _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_002", 0, 100),
        _tracks_with("clip_002", 20, 120)), parent=target["id"])
    reverted = uj.revert_commit(project, target["id"])
    assert reverted["parent"] == head["id"]


def test_revert_refuses_a_merge_commit(tmp_path):
    """A merge's union is not invertible side by side.

    The defect: inverting the union, so reverting the merged branch
    silently reverts the main line it merged into as well.
    """
    project = _project(tmp_path)
    left = _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_001", 0, 100),
        _tracks_with("clip_001", 50, 150)))
    right = _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_002", 0, 100),
        _tracks_with("clip_002", 20, 120)), parent=left["id"])
    merged = uj.merge_commits(project, timeline="Reel 1",
                              left=left["id"], right=right["id"])
    try:
        uj.revert_commit(project, merged["id"])
    except uj.RevertRefused as refused:
        assert "merge" in str(refused)
    else:
        raise AssertionError("a merge commit was reverted")


def test_revert_refuses_a_commit_off_its_timeline_line(tmp_path):
    """A commit the head does not descend from cannot be reverted.

    The defect: a revert standing on a history that does not contain
    the commit, recording an inverse that applies to nothing.
    """
    project = _project(tmp_path)
    root = _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_001", 0, 100),
        _tracks_with("clip_001", 10, 110)))
    forked = _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_001", 0, 100),
        _tracks_with("clip_001", 20, 120)), parent=root["id"])
    _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_002", 0, 100),
        _tracks_with("clip_002", 30, 130)), parent=root["id"])
    try:
        uj.revert_commit(project, forked["id"])
    except uj.RevertRefused as refused:
        assert forked["id"][:12] in str(refused)
    else:
        raise AssertionError("an off-line commit was reverted")


def test_verb_refuses_an_unknown_id_and_reverts_a_known_one(tmp_path, capsys):
    """`ren revert` exits 2 naming an unknown id, 0 with a revert line."""
    project = _project(tmp_path)
    target = _commit(project, "Reel 1", timeline_diff.diff_timelines(
        _tracks_with("clip_001", 0, 100),
        _tracks_with("clip_001", 50, 150)))
    assert revert_verb.main([project, "deadbeef"]) == 2
    assert "deadbeef" in capsys.readouterr().err
    assert revert_verb.main([project, target["id"]]) == 0
    out = capsys.readouterr().out
    assert target["id"][:12] in out
    assert "reverted" in out


def test_revert_verb_is_registered_in_both_front_doors():
    """`revert` reaches past both verb lists.

    The defect the two registries' own comments warn about: a verb
    the Python list knows but the native list does not is
    unreachable - the native front door refuses it before exec'ing
    the implementation.
    """
    by_name = {verb.name: verb for verb in ren_commands.VERBS}
    assert by_name["revert"].module_argv == ("library.tools.creative_revert",)
    native = (REPO / "native" / "ren-cli" / "src" / "commands.rs").read_text(
        encoding="utf-8")
    assert 'name: "revert"' in native
