"""Cherry-pick a creative commit: replay its changes as a new commit.

Each test names a defect the replay closes:

- a replayed change with no provenance link, so a re-applied trim
  reads as a hand-copied change nobody can trace to its source;
- a replay over a later touch on the same clips, so the old changes
  silently clobber the later work;
- an over-strict replay that refuses a commit no later commit
  touches, blocking a legitimate re-application;
- a replayed MERGE, whose changes are the union of both sides, so the
  main line is silently replayed too;
- a replay of a commit outside its timeline's line, so the replay
  stands on a history that does not contain it;
- an empty replay, which rewrites no history and only pollutes it.

Only the commit's own line: the pick stands on the head of the
timeline the commit is for. A pick from another line is refused -
the record holds diffs, not destination state, so nothing in the
store can prove the picked spans still hold there.

Replays are recorded offline under `pipeline_output/review/` as
versioned text, like the commits, so every project is under `tmp_path`
and no Resolve is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import timeline_diff  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402


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


def _move(clip_name, new_start):
    """A real `timeline_diff` move change for one clip."""
    return timeline_diff.diff_timelines(
        _tracks_with(clip_name, 0, 100),
        _tracks_with(clip_name, new_start, new_start + 100))


def _commit(project, timeline, changes, parent=None):
    return uj.record_commit(
        project, parent=parent, intent="tighten opening",
        changes=changes, agent="reel_touchup", timeline=timeline)


def test_replay_copies_the_changes_with_its_provenance(tmp_path):
    """The replay's changes equal the source's and name it.

    The defect: a re-applied change recorded as a fresh,
    hand-copied change, so nothing traces it back to the commit it
    re-applies. The replay stands on the head with the source in
    its artifacts, and the source stays readable.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", _move("clip_001", 50))
    head = _commit(project, "Reel 1", _move("clip_002", 20),
                   parent=source["id"])
    replayed = uj.cherry_pick(project, source["id"])
    assert replayed["parent"] == head["id"]
    assert replayed["changes"] == source["changes"]
    assert replayed["agent"] == "creative_cherry_pick"
    assert replayed["artifacts"] == {"cherry_picks": source["id"]}
    assert uj.read_commit(project, source["id"])["intent"] == \
        "tighten opening"


def test_replay_refuses_clips_later_commits_also_changed(tmp_path):
    """A later touch on the same clips blocks the replay, by name.

    The defect: replaying the old changes over the later touch, so
    the replay silently clobbers work that came after.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", _move("clip_001", 50))
    _commit(project, "Reel 1", _move("clip_001", 70),
            parent=source["id"])
    try:
        uj.cherry_pick(project, source["id"])
    except uj.CherryPickRefused as refused:
        assert "clip_001" in str(refused)
    else:
        raise AssertionError("replay over a later touch was recorded")


def test_replay_allows_a_commit_no_later_commit_touches(tmp_path):
    """Independent later work does not block the replay.

    The defect the previous test's guard could introduce: refusing
    every non-head replay, blocking a legitimate re-application.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", _move("clip_001", 50))
    head = _commit(project, "Reel 1", _move("clip_002", 20),
                   parent=source["id"])
    replayed = uj.cherry_pick(project, source["id"])
    assert replayed["parent"] == head["id"]


def test_replay_refuses_a_merge_commit(tmp_path):
    """A merge's union is not replayable side by side.

    The defect: replaying the union, so the main line the merge
    brought in is silently replayed too. Pick the brought-in
    commits instead.
    """
    project = _project(tmp_path)
    left = _commit(project, "Reel 1", _move("clip_001", 50))
    right = _commit(project, "Reel 1", _move("clip_002", 20),
                    parent=left["id"])
    merged = uj.merge_commits(project, timeline="Reel 1",
                              left=left["id"], right=right["id"])
    try:
        uj.cherry_pick(project, merged["id"])
    except uj.CherryPickRefused as refused:
        assert "merge" in str(refused)
    else:
        raise AssertionError("a merge commit was replayed")


def test_replay_refuses_a_commit_off_its_timeline_line(tmp_path):
    """A commit the head does not descend from cannot be replayed.

    The defect: a replay standing on a history that does not contain
    the commit, recording changes that apply to nothing.
    """
    project = _project(tmp_path)
    root = _commit(project, "Reel 1", _move("clip_001", 10))
    forked = _commit(project, "Reel 1", _move("clip_001", 20),
                     parent=root["id"])
    _commit(project, "Reel 1", _move("clip_002", 30),
            parent=root["id"])
    try:
        uj.cherry_pick(project, forked["id"])
    except uj.CherryPickRefused as refused:
        assert forked["id"][:12] in str(refused)
    else:
        raise AssertionError("an off-line commit was replayed")


def test_replay_refuses_an_empty_change_list(tmp_path):
    """A commit that changed nothing has no replay.

    The defect: recording a no-op replay that rewrites no history
    and only pollutes it.
    """
    project = _project(tmp_path)
    source = _commit(project, "Reel 1", [])
    try:
        uj.cherry_pick(project, source["id"])
    except uj.CherryPickRefused as refused:
        assert "nothing" in str(refused)
    else:
        raise AssertionError("an empty replay was recorded")
