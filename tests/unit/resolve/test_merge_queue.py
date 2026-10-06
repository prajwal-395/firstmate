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
                     base=root["id"], timeline="variant faster-intro",
                     note="tighten the opening")
    candidate_a = uj.record_commit(
        project, parent=root["id"], intent="faster",
        changes=_move_change("clip_001", 10),
        agent="ren-worker-07", timeline="variant faster-intro")
    uj.record_branch(project, name="variant emotional-intro",
                     base=root["id"], timeline="variant emotional-intro",
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


# ── The evaluation and merge passes ───────────────────────────────────
#
# Each test names a defect the two passes close:
#
# - an evaluation that merges (the pass must record verdicts only);
# - a batch failure that names no candidate (the verdicts ARE the
#   split - each candidate is judged alone);
# - a second candidate merged over the first's landed changes in
#   silence (the head moves per entry, and a conflict with it rejects
#   with the clips named);
# - a merge onto a branch that moved after evaluation (stale refuses,
#   and only a re-evaluation clears it);
# - a merge without a passing evaluation (the freshness the queue
#   exists to enforce, skipped).


def _queued_pair(tmp_path):
    """Two queued candidates that both move clip_001, differently."""
    project, master, _old_a, _old_b = _lined_project(tmp_path)
    root = uj.read_commit(project, master)["parent"]
    candidate_a = uj.record_commit(
        project, parent=root,
        intent="faster", changes=_move_change("clip_001", 10),
        agent="ren-worker-07",
        timeline="variant faster-intro")["id"]
    candidate_b = uj.record_commit(
        project, parent=root,
        intent="emotional", changes=_move_change("clip_001", 50),
        agent="ren-worker-08",
        timeline="variant emotional-intro")["id"]
    entry_a = uj.queue_merge(
        project, branch="variant faster-intro", candidate=candidate_a,
        master=master, submitted_by="ren-worker-07")
    entry_b = uj.queue_merge(
        project, branch="variant emotional-intro", candidate=candidate_b,
        master=master, submitted_by="ren-worker-08")
    return project, master, entry_a, entry_b


def test_evaluate_passes_what_merges_and_names_what_does_not(tmp_path):
    """One pass over the batch: a clean candidate passes with its
    measured facts, a clashing one fails naming the clips - and no
    commit is created, because evaluation reads."""
    project, master, candidate_a, _b = _lined_project(tmp_path)
    root = uj.read_commit(project, master)["parent"]
    # The master side moves clip_009 after the root, so a candidate
    # touching clip_009 clashes while one touching clip_001 passes.
    # (A candidate extending the master is a fast-forward - it cannot
    # fail evaluation; only the master's own movement since the
    # common ancestor makes a failure.)
    master_side = uj.record_commit(
        project, parent=master, intent="master moves clip_009",
        changes=_move_change("clip_009", 40),
        agent="human-prajwal", timeline="Reel 01 - lab")["id"]
    recorded_master = uj.record_commit(
        project, parent=master_side, intent="master head",
        changes=[], agent="human-prajwal",
        timeline="Reel 01 - lab")["id"]
    clashing = uj.record_commit(
        project, parent=master, intent="branch moves clip_009",
        changes=_move_change("clip_009", 70),
        agent="ren-worker-08",
        timeline="variant clashing")["id"]
    uj.record_branch(project, name="variant clashing",
                     base=master, timeline="variant clashing",
                     note="clash")
    queued_ok = uj.queue_merge(
        project, branch="variant faster-intro", candidate=candidate_a,
        master=recorded_master, submitted_by="ren-worker-07")
    uj.queue_merge(
        project, branch="variant clashing", candidate=clashing,
        master=recorded_master, submitted_by="ren-worker-08")
    before = len(uj.list_commits(project))

    decided = uj.evaluate_batch(project)

    by_branch = {e["branch"]: e for e in decided}
    assert by_branch["variant faster-intro"]["status"] == uj.ENTRY_EVALUATED_PASS
    assert by_branch["variant clashing"]["status"] == uj.ENTRY_EVALUATED_FAIL
    assert "clip_009" in by_branch["variant clashing"]["evaluation"]["reason"]
    assert by_branch["variant clashing"]["evaluation"]["master_clips"] == [
        "clip_009"]
    facts = by_branch["variant faster-intro"]["evaluation"]
    assert facts["candidate_changes"] == 1
    assert facts["candidate_clips"] == ["clip_001"]
    assert facts["branch_digest"]
    assert len(uj.list_commits(project)) == before
    kinds = [e["type"] for e in event_log.read_events(project)]
    assert kinds.count("merge_evaluated") == 2
    assert uj.read_queue_entry(
        project, queued_ok["id"])["status"] == uj.ENTRY_EVALUATED_PASS


def test_evaluate_refuses_decided_entries(tmp_path):
    """A decided entry re-evaluated would overwrite its verdict in
    silence - only queued (or stale) entries evaluate."""
    project, master, _a, _b = _lined_project(tmp_path)
    project, master, entry_a, _b2 = _queued_pair(tmp_path)
    uj.evaluate_batch(project, [entry_a["id"]])
    with pytest.raises(QueueRefused, match="not queued"):
        uj.evaluate_batch(project, [entry_a["id"]])
    # Merging without a pass is refused even where the merge would
    # succeed: the freshness check is the point, not the outcome.
    fresh = uj.queue_merge(
        project, branch="variant emotional-intro",
        candidate=uj.read_queue_entry(
            project, _b2["id"])["candidate"],
        master=master, submitted_by="ren-worker-08")
    assert fresh["id"] == _b2["id"]
    with pytest.raises(QueueRefused, match="not evaluated_pass"):
        uj.merge_passing(project, [fresh["id"]])


def test_merge_passes_in_order_and_splits_the_batch(tmp_path):
    """Both candidates pass solo evaluation; the first merges, and the
    second - now conflicting with the first's landed changes - is
    rejected naming clip_001 while the first stays merged. The batch
    split names its candidate."""
    project, master, entry_a, entry_b = _queued_pair(tmp_path)
    uj.evaluate_batch(project)

    done = uj.merge_passing(project)

    by_branch = {e["branch"]: e for e in done}
    assert by_branch["variant faster-intro"]["status"] == uj.ENTRY_MERGED
    assert by_branch["variant emotional-intro"]["status"] == uj.ENTRY_REJECTED
    assert "clip_001" in by_branch["variant emotional-intro"]["merge_error"]
    merged = uj.read_queue_entry(project, entry_a["id"])
    assert merged["status"] == uj.ENTRY_MERGED
    commit = uj.read_commit(project, merged["merge_commit"])
    assert commit["parent"] == master
    assert commit["second_parent"] == merged["candidate"]
    assert commit["timeline"] == "Reel 01 - lab"
    kinds = [e["type"] for e in event_log.read_events(project)]
    assert kinds.count("merge_merged") == 1
    assert kinds.count("merge_rejected") == 1


def test_stale_branch_refuses_until_re_evaluated(tmp_path):
    """A commit landing on the branch after evaluation makes the merge
    refuse as stale - and only a re-evaluation (fresh snapshots) clears
    it, never a retry."""
    project, master, entry_a, _b = _queued_pair(tmp_path)
    uj.evaluate_batch(project, [entry_a["id"]])
    uj.record_commit(
        project, parent=entry_a["candidate"], intent="late planning",
        changes=[], agent="ren-worker-07",
        timeline="variant faster-intro")

    (stale,) = uj.merge_passing(project, [entry_a["id"]])

    assert stale["status"] == uj.ENTRY_STALE
    assert uj.read_queue_entry(
        project, entry_a["id"])["status"] == uj.ENTRY_STALE

    with pytest.raises(QueueRefused, match="not evaluated_pass"):
        uj.merge_passing(project, [entry_a["id"]])

    uj.evaluate_batch(project, [entry_a["id"]])
    (merged,) = uj.merge_passing(project, [entry_a["id"]])
    assert merged["status"] == uj.ENTRY_MERGED


def test_re_recorded_branch_refuses_on_digest(tmp_path):
    """The freshness check is two clocks: re-recording the branch
    (same tip, new note) changes the digest, and the merge refuses
    even though no commit landed."""
    project, master, entry_a, _b = _queued_pair(tmp_path)
    uj.evaluate_batch(project, [entry_a["id"]])
    uj.record_branch(project, name="variant faster-intro",
                     base=master, timeline="variant faster-intro",
                     note="re-planned overnight")

    (stale,) = uj.merge_passing(project, [entry_a["id"]])

    assert stale["status"] == uj.ENTRY_STALE
