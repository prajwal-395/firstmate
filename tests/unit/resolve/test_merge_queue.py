"""The merge queue: proposals evaluated together, merged in order.

Each test names a defect the queue closes:

- a proposal for an unknown branch or commit, so the queue holds a
  pointer to nothing;
- a candidate that IS the master, so the queue holds a no-op;
- a candidate with no common ancestor with the master, so an
  evaluation pass is wasted on what could never merge;
- a re-submitted triple forking a second entry, so one proposal is
  evaluated and merged twice;
- an evaluation that records merges (the preview must read, never
  write);
- a conflict reported without the clips that collide, so the
  rejection reaches nobody actionable.

Everything is recorded offline under `pipeline_output/review/` in a
`tmp_path` project. No Resolve is touched.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools import event_log  # noqa: E402
from library.tools import timeline_diff  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402
from library.tools.undo_journal import QueueRefused  # noqa: E402


def _project(tmp_path) -> str:
    folder = Path(tmp_path) / "project"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _tracks_with(clip_name, record_in=0, record_out=100):
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": clip_name, "record_in": record_in,
         "record_out": record_out, "duration": record_out - record_in,
         "source_in_frame": 0,
         "source_out_frame": record_out - record_in,
         "left_offset": 0, "right_offset": 0},
    ]}]


def _move_change(clip_name, new_start):
    return timeline_diff.diff_timelines(
        _tracks_with(clip_name, 0, 100),
        _tracks_with(clip_name, new_start, new_start + 100))


def _lined_project(tmp_path):
    """A master line with two branched candidates off one root."""
    project = _project(tmp_path)
    root = uj.record_commit(
        project, parent=None, intent="first touch", changes=[],
        agent="reel_touchup", timeline="Reel 01 - lab")
    master = uj.record_commit(
        project, parent=root["id"], intent="tighten", changes=[],
        agent="reel_touchup", timeline="Reel 01 - lab")
    uj.record_branch(project, name="variant faster-intro",
                     base=root["id"], timeline="Reel 01 - lab",
                     note="tighten the opening")
    candidate_a = uj.record_commit(
        project, parent=root["id"], intent="faster",
        changes=_move_change("clip_001", 10),
        agent="ren-worker-07", timeline="variant faster-intro")
    uj.record_branch(project, name="variant emotional-intro",
                     base=root["id"], timeline="Reel 01 - lab",
                     note="hold the reaction")
    candidate_b = uj.record_commit(
        project, parent=root["id"], intent="emotional",
        changes=_move_change("clip_002", 20),
        agent="ren-worker-07", timeline="variant emotional-intro")
    return project, master["id"], candidate_a["id"], candidate_b["id"]


def test_queue_records_a_proposal_and_rejects_a_resubmit(tmp_path):
    """A queued triple returns its entry; re-submitting it returns the
    same entry instead of forking a second one that would evaluate and
    merge twice."""
    project, master, candidate_a, _b = _lined_project(tmp_path)
    first = uj.queue_merge(
        project, branch="variant faster-intro", candidate=candidate_a,
        master=master, submitted_by="ren-worker-07")
    assert first["status"] == uj.ENTRY_QUEUED
    assert first["branch"] == "variant faster-intro"

    second = uj.queue_merge(
        project, branch="variant faster-intro", candidate=candidate_a,
        master=master, submitted_by="ren-worker-07")
    assert second["id"] == first["id"]
    assert len(uj.list_queue_entries(project)) == 1

    kinds = [e["type"] for e in event_log.read_events(project)]
    assert kinds.count("merge_queued") == 1


def test_queue_refuses_what_could_never_evaluate(tmp_path):
    """Unknown branches and commits, a candidate that IS the master,
    an anonymous submitter, and a candidate with no common ancestor
    all refuse before anything is written - the queue holds proposals,
    never pointers to nothing or no-ops."""
    project, master, candidate_a, _b = _lined_project(tmp_path)
    foreign = uj.record_commit(
        project, parent=None, intent="another line", changes=[],
        agent="ren-worker-07", timeline="Reel 02 - lab")["id"]

    with pytest.raises(uj.BranchRefused):
        uj.queue_merge(
            project, branch="no-such-branch", candidate=candidate_a,
            master=master, submitted_by="ren-worker-07")
    with pytest.raises(uj.UndoRefused):
        uj.queue_merge(
            project, branch="variant faster-intro",
            candidate="no-such-commit", master=master,
            submitted_by="ren-worker-07")
    with pytest.raises(QueueRefused, match="no submitter"):
        uj.queue_merge(
            project, branch="variant faster-intro",
            candidate=candidate_a, master=master, submitted_by="  ")
    with pytest.raises(QueueRefused, match="already the master"):
        uj.queue_merge(
            project, branch="variant faster-intro", candidate=master,
            master=master, submitted_by="ren-worker-07")
    with pytest.raises(QueueRefused, match="no common ancestor"):
        uj.queue_merge(
            project, branch="variant faster-intro", candidate=foreign,
            master=master, submitted_by="ren-worker-07")
    assert uj.list_queue_entries(project) == []


def test_preview_conflicts_without_recording(tmp_path):
    """The evaluation's read: conflicting candidates report the clips
    that collide, and the commit count does not move - a preview that
    recorded would merge during evaluation."""
    project, master, candidate_a, _b = _lined_project(tmp_path)
    clashing = uj.record_commit(
        project, parent=master, intent="master moves clip_001",
        changes=_move_change("clip_001", 40),
        agent="human-prajwal", timeline="Reel 01 - lab")["id"]
    before = len(uj.list_commits(project))

    preview = uj.merge_preview(
        project, left=clashing, right=candidate_a)

    assert preview["mergeable"] is False
    assert preview["conflicts"] == ["clip_001"]
    assert len(uj.list_commits(project)) == before


def test_preview_fast_forward_carries_the_descendant_changes(tmp_path):
    """A candidate that extends the master merges to itself: the
    merged changes are the descendant's changes since the ancestor,
    and no conflict is possible."""
    project, master, candidate_a, _b = _lined_project(tmp_path)
    descendant = uj.record_commit(
        project, parent=master, intent="master extends itself",
        changes=_move_change("clip_003", 30),
        agent="ren-worker-07",
        timeline="variant faster-intro")["id"]
    preview = uj.merge_preview(project, left=master, right=descendant)
    assert preview["mergeable"] is True
    assert preview["fast_forward"] is True
    assert [c.get("item") for c in preview["merged_changes"]] == ["clip_003"]

    same = uj.merge_preview(project, left=master, right=master)
    assert same["mergeable"] is False


def test_list_filters_by_status(tmp_path):
    """The queue reads back per status - the merge pass asks for what
    is queued, the critic for what is decided."""
    project, master, candidate_a, candidate_b = _lined_project(tmp_path)
    uj.queue_merge(
        project, branch="variant faster-intro", candidate=candidate_a,
        master=master, submitted_by="ren-worker-07")
    uj.queue_merge(
        project, branch="variant emotional-intro", candidate=candidate_b,
        master=master, submitted_by="ren-worker-08")

    assert len(uj.list_queue_entries(project)) == 2
    assert len(uj.list_queue_entries(
        project, status=uj.ENTRY_QUEUED)) == 2
    assert uj.list_queue_entries(project, status=uj.ENTRY_MERGED) == []
    with pytest.raises(QueueRefused):
        uj.read_queue_entry(project, "no-such-entry")
