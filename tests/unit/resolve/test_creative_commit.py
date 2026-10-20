"""Creative commits: git semantics over creative state, recorded offline.

Each test names a defect the commit record closes:

- a commit whose id is random, so recording the same touch twice forks
  history into two commits that say the same thing;
- a commit whose id covers a timestamp or the id itself, so the same
  content recorded twice gets two different ids;
- a commit that does not record its parent, so a chain of touches is a
  pile of unrelated commits and "what did this branch change" has no
  answer;
- a commit whose changes are not the diff, so "what this commit did" is
  whatever the caller guessed rather than what the timeline reads.

The commit is written under `pipeline_output/review/commits/` as
versioned text, like the undo journal, so every project is under
`tmp_path` and no Resolve is touched.
"""
from __future__ import annotations

import hashlib
import json
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


def _move_tracks():
    """A track read with one moved clip, for the second-touch fixture."""
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": "clip_001", "record_in": 50, "record_out": 150,
         "duration": 100, "source_in_frame": 0, "source_out_frame": 100,
         "left_offset": 0, "right_offset": 0},
    ]}]


def _changes():
    """A small diff: one move."""
    earlier = [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": "clip_001", "record_in": 0, "record_out": 100,
         "duration": 100, "source_in_frame": 0, "source_out_frame": 100,
         "left_offset": 0, "right_offset": 0},
    ]}]
    later = [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": "clip_001", "record_in": 50, "record_out": 150,
         "duration": 100, "source_in_frame": 0, "source_out_frame": 100,
         "left_offset": 0, "right_offset": 0},
    ]}]
    return timeline_diff.diff_timelines(earlier, later)


def test_commit_id_is_the_sha256_of_its_canonical_content(tmp_path):
    """Two commits with the same content are the SAME commit.

    The defect: a random or timestamped id means recording the same
    touch twice produces two commits that say the same thing, and a
    branch diff between them reports a change that is not one. The id
    is the SHA-256 of the canonical JSON of exactly what the commit
    says - parent, intent, changes, agent, evaluation, artifacts - so
    content identity IS commit identity.
    """
    content = {
        "parent": None,
        "intent": "tighten opening",
        "changes": _changes(),
        "agent": "reel_touchup",
        "timeline": "Reel 01 - lab",
        "evaluation": {"gate_class": "composed"},
        "artifacts": {"journal": "j1"},
    }
    payload = json.dumps(content, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, default=str)
    expected = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    commit = uj.record_commit(
        _project(tmp_path), parent=content["parent"],
        intent=content["intent"], changes=content["changes"],
        agent=content["agent"], timeline=content["timeline"],
        evaluation=content["evaluation"], artifacts=content["artifacts"])
    assert commit["id"] == expected


def test_a_changed_field_changes_the_id(tmp_path):
    """A commit that says something different is a different commit.

    The defect: an id that does not cover the content (a counter, a
    random uuid) cannot distinguish "the same touch" from "a different
    touch", so a revert cannot find the commit it reverses.
    """
    project = _project(tmp_path)
    first = uj.record_commit(
        project, parent=None, intent="tighten opening",
        changes=_changes(), agent="reel_touchup")
    second = uj.record_commit(
        project, parent=None, intent="loosen opening",
        changes=_changes(), agent="reel_touchup")
    assert first["id"] != second["id"]


def test_recording_the_same_commit_twice_returns_the_same_commit(tmp_path):
    """A re-recorded touch does not fork history.

    The defect: recording the same content twice as two commits means a
    branch has two identical commits, and a diff between the branch and
    its parent reports a change that is not one. The second recording
    returns the existing commit, not a new one.
    """
    project = _project(tmp_path)
    kwargs = {"parent": None, "intent": "tighten opening",
              "changes": _changes(), "agent": "reel_touchup"}
    first = uj.record_commit(project, **kwargs)
    second = uj.record_commit(project, **kwargs)
    assert first["id"] == second["id"]
    assert first["recorded_at"] == second["recorded_at"]


def test_commit_records_its_parent_so_a_chain_is_a_chain(tmp_path):
    """A touch's commit names the commit it stands on.

    The defect: a commit with no parent is an island - "what did this
    branch change" cannot be answered, and a revert has nothing to
    revert to. The parent is the most recent commit on the branch.
    """
    project = _project(tmp_path)
    root = uj.record_commit(
        project, parent=None, intent="first touch",
        changes=_changes(), agent="reel_touchup")
    child = uj.record_commit(
        project, parent=root["id"], intent="second touch",
        changes=_changes(), agent="reel_touchup")
    assert child["parent"] == root["id"]
    commits = uj.list_commits(project)
    assert [c["id"] for c in commits] == [root["id"], child["id"]]


def test_commit_changes_are_the_diff_the_timeline_reads(tmp_path):
    """The commit's changes are the before/after diff, not a guess.

    The defect: a commit whose changes are whatever the caller remembered
    cannot be trusted for a revert or a branch diff. The changes are
    `timeline_diff.diff_timelines` over the journal's before and after
    track reads.
    """
    changes = _changes()
    commit = uj.record_commit(
        _project(tmp_path), parent=None, intent="tighten opening",
        changes=changes, agent="reel_touchup")
    assert commit["changes"] == changes
    assert commit["changes"][0]["op"] == timeline_diff.OP_MOVE


def test_two_reels_produce_two_independent_chains(tmp_path):
    """A touch on Reel 10 after one on Reel 03 stands on Reel 03's commit.

    The defect: the parent is the whole project's latest commit, so one
    timeline's history is forked into another's - a global chain, not a
    branch per timeline. The branch IS the timeline: each reel's commit
    names the last commit FOR THAT REEL as its parent.
    """
    project = _project(tmp_path)
    journal_reel_03 = {"id": "j3", "final": "Reel 03 - lab",
                      "before": {"tracks": []}, "after": {"tracks": []}}
    journal_reel_10 = {"id": "j10", "final": "Reel 10 - lab",
                       "before": {"tracks": []}, "after": {"tracks": []}}
    commit_03 = uj.record_touch_commit(
        project, journal=journal_reel_03, spec={"edits": []})
    commit_10 = uj.record_touch_commit(
        project, journal=journal_reel_10, spec={"edits": []})
    assert commit_03["parent"] is None
    assert commit_10["parent"] is None
    assert commit_03["timeline"] == "Reel 03 - lab"
    assert commit_10["timeline"] == "Reel 10 - lab"
    # A second touch on Reel 03 stands on Reel 03's commit, not Reel 10's.
    journal_reel_03_again = {"id": "j3b", "final": "Reel 03 - lab",
                             "before": {"tracks": []},
                             "after": {"tracks": _move_tracks()}}
    commit_03_again = uj.record_touch_commit(
        project, journal=journal_reel_03_again, spec={"edits": []})
    assert commit_03_again["parent"] == commit_03["id"]


def test_promoted_axi_touch_revision_links_its_execution_transaction(tmp_path):
    """Defect: a durable creative revision could not be traced to its AXI receipt."""
    project = _project(tmp_path)
    transaction_id = "axi-request-9f21"
    journal = {"id": "touch-journal-1", "final": "Reel 03 - lab",
               "before": {"tracks": []}, "after": {"tracks": []}}

    commit = uj.record_touch_commit(
        project, journal=journal,
        spec={"edits": [], "transaction_id": transaction_id},
        version=2)

    assert commit["artifacts"]["axi_transaction_id"] == transaction_id
    assert commit["artifacts"]["journal"] == journal["id"]
    assert commit["artifacts"]["version"] == 2


def test_read_commit_refuses_a_foreign_format(tmp_path):
    """A commit file in a foreign format is refused, not trusted.

    The defect: reading a commit whose format is unknown as if it were
    one would let a half-migrated or corrupted record pass as a commit.
    """
    import os

    project = _project(tmp_path)
    root = uj.commit_root(project)
    os.makedirs(os.path.join(root, "deadbeef"), exist_ok=True)
    with open(os.path.join(root, "deadbeef", uj.COMMIT_FILENAME),
              "w", encoding="utf-8") as handle:
        json.dump({"format": "something_else/1", "id": "deadbeef"}, handle)
    try:
        uj.read_commit(project, "deadbeef")
        assert False, "a foreign-format commit must be refused"
    except uj.UndoRefused:
        pass
