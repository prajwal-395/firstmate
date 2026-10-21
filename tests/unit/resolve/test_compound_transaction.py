"""AXI-014: a compound request is a transaction.

The defects these tests name were open before the compound
transaction rules existed:

- the order of a compound request's operations was whatever order the
  request listed them in, so a ripple could be written BEFORE the
  delete whose gap it closes;
- a request that asked to change the very clip it required unchanged
  was written FIRST and only noticed on the read-back;
- a transaction whose outcome was not recorded as committed could be
  submitted again, and the second submission wrote again;
- a transaction aborted before its first write left a hold on the
  timeline it never touched;
- a recovery was claimed when the compensation RAN, and never compared
  against the generation the request was planned against.

Every test here names one of them.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from library.tools import compound_transaction

# ── helpers ──────────────────────────────────────────────────────────


def _dead_pid() -> int:
    """A pid that is certainly not running any more."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return int(process.pid)


def _journal(project_folder, **fields) -> str:
    """Append one transaction event, as a worker would have written it."""
    from library.tools import event_log

    path = event_log.log_path(str(project_folder))
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "seq": 1,
        "at": str(fields.pop("at", datetime.now(UTC).isoformat(
            timespec="seconds"))),
        "host": str(fields.pop("host", socket.gethostname())),
        "pid": int(fields.pop("pid", os.getpid())),
        "type": event_log.EDIT_TRANSACTION_STAGE,
        "task": "axi-transaction",
        "fields": fields,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return str(path)


def _snapshot(*clips):
    """A timeline snapshot holding one video row with the given clips."""
    return {"tracks": [{"type": "video", "index": 1, "name": "V1",
                         "clips": [dict(clip) for clip in clips]}],
            "markers": {"timeline": []}}


def _clip(unique_id, record_in=0, record_out=100, **extra):
    return {"unique_id": unique_id, "record_in": record_in,
            "record_out": record_out, "duration": record_out - record_in,
            "source_file": f"/m/{unique_id}.mp4",
            "source_in_frame": 0, "source_out_frame": record_out,
            "name": unique_id, **extra}


# ── 1. The write order and the dependencies are declared ──────────────


def test_a_ripple_is_written_after_the_delete_whose_gap_it_closes():
    """Defect: the request's list order was the write order.

    A ripple closes the gap a delete left, so the delete has to land
    first. The request listed the ripple first, and the engine wrote it
    first.
    """
    operations = [
        {"op": "clip.ripple", "unique_id": "b", "delta_frames": 24},
        {"op": "clip.delete", "unique_id": "a"},
    ]

    plan = compound_transaction.plan_write_order(operations)

    assert plan.order == (1, 0)
    assert plan.reordered is True
    assert [row["kind"] for row in plan.dependencies] == ["engine"]


def test_a_request_that_asks_for_the_ripple_first_is_refused():
    """Defect: a contradiction was executed instead of refused.

    The requester declared that the delete depends on the ripple. The
    engine knows the opposite, so the request is refused - before the
    first write - rather than executed in an order nobody asked for.
    """
    operations = [
        {"op": "clip.ripple", "unique_id": "b", "delta_frames": 24},
        {"op": "clip.delete", "unique_id": "a"},
    ]

    with pytest.raises(compound_transaction.CompoundRefused) as refused:
        compound_transaction.plan_write_order(
            operations, depends_on=[[], [0]])

    assert refused.value.category == "unsupported"
    assert "clip.delete" in refused.value.reason


def test_an_operation_on_a_clip_another_operation_creates_waits_for_it():
    """The creator comes first: the target does not exist before it."""
    operations = [
        {"op": "clip.set_property", "unique_id": "client-7",
         "key": "Pan", "value": 0.2},
        {"op": "clip.insert", "unique_id": "resolve-9",
         "client_unique_id": "client-7", "track_type": "video",
         "track_index": 1, "record_in": 0, "record_out": 48,
         "media_pool_item": "clip-9.mov"},
    ]

    plan = compound_transaction.plan_write_order(operations)

    assert plan.order == (1, 0)
    assert [row["kind"] for row in plan.dependencies] == ["created_target"]


def test_the_preflight_refuses_a_request_whose_dependency_cannot_resolve():
    """Nothing is written when the dependencies do not resolve."""
    operations = [
        {"op": "clip.delete", "unique_id": "a"},
        {"op": "clip.ripple", "unique_id": "b", "delta_frames": 24},
    ]

    verdict = compound_transaction.preflight(
        project_folder="", transaction_id="tx-1", operations=operations,
        before=_snapshot(_clip("a")), depends_on=[[1], []])

    assert verdict["status"] == "refused"
    assert verdict["category"] == "unsupported"
    assert verdict["mutated"] is False


# ── 2. A preservation constraint that cannot hold refuses BEFORE ─────


def test_a_requirement_the_writes_cannot_satisfy_refuses_before_the_write():
    """Defect: the contradiction was written first and noticed later.

    The request required a clip to be unchanged and wrote to that very
    clip. The engine wrote it, and only the read-back noticed.
    """
    operations = [{"op": "clip.trim", "unique_id": "a",
                   "in_frame": 0, "out_frame": 50}]
    requirements = [{"kind": "clip_unchanged", "unique_id": "a"}]

    verdict = compound_transaction.preflight(
        project_folder="", transaction_id="tx-2", operations=operations,
        before=_snapshot(_clip("a")), requirements=requirements)

    assert verdict["status"] == "refused"
    assert verdict["category"] == "conflict"
    assert verdict["mutated"] is False
    assert "clip_unchanged" in verdict["reason"]


def test_a_requirement_the_writes_can_satisfy_is_not_refused():
    """The preflight refuses contradictions, not requests."""
    operations = [{"op": "clip.trim", "unique_id": "a",
                   "in_frame": 0, "out_frame": 50}]
    requirements = [{"kind": "clip_unchanged", "unique_id": "b"}]

    verdict = compound_transaction.preflight(
        project_folder="", transaction_id="tx-3", operations=operations,
        before=_snapshot(_clip("a"), _clip("b")), requirements=requirements)

    assert verdict["status"] == "ok"
    assert verdict["preservation"]["status"] == "feasible"


def test_a_row_the_request_writes_to_cannot_be_required_unchanged():
    operations = [{"op": "clip.trim", "unique_id": "a", "in_frame": 0,
                   "out_frame": 50, "track_type": "video",
                   "track_index": 1}]
    requirements = [{"kind": "row_unchanged", "track_type": "video",
                     "track_index": 1}]

    verdict = compound_transaction.preflight(
        project_folder="", transaction_id="tx-4", operations=operations,
        before=_snapshot(_clip("a")), requirements=requirements)

    assert verdict["status"] == "refused"
    assert "row_unchanged" in verdict["reason"]


# ── 3. The recovery is proven against the validated generation ───────


def test_a_restoration_is_only_claimed_when_the_timeline_matches():
    """Defect: the recovery was claimed when the compensation RAN.

    The compensation is not a proof that the timeline is back. The
    proof is a fresh read-back compared with the recorded snapshot of
    the generation the request was planned against.
    """
    recorded = _snapshot(_clip("a", 0, 100))
    live = _snapshot(_clip("a", 0, 60))

    assert compound_transaction.compare_snapshots(recorded, recorded)[
        "status"] == "restored"
    assert compound_transaction.compare_snapshots(recorded, live)[
        "status"] == "differs"


def test_a_restoration_that_cannot_be_read_is_unprovable_not_restored():
    verdict = compound_transaction.compare_snapshots(_snapshot(_clip("a")),
                                                     {})

    assert verdict["status"] == "unprovable"


def test_the_terminal_stage_of_a_transaction_that_never_wrote_is_abandoned():
    """A transaction that never wrote owes no recovery, and no hold."""
    assert compound_transaction.terminal_stage(write_attempted=False) == \
        "abandoned"
    assert compound_transaction.terminal_stage(
        write_attempted=True, restoration={"status": "restored"}) == "recovered"
    assert compound_transaction.terminal_stage(
        write_attempted=True, restoration={"status": "differs"}) == \
        "recovery_required"
    assert compound_transaction.terminal_stage(
        write_attempted=True, restoration=None) == "recovery_required"


# ── 4. An aborted pre-write transaction leaves no hold ───────────────


def test_a_compound_aborted_before_its_first_write_leaves_no_hold(tmp_path):
    """Defect: an aborted compound left a hold on the timeline.

    The transaction was journalled as prepared and then refused before
    any write. The gate read it as an outstanding recovery and refused
    every later edit on that timeline.
    """
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-aborted", stage="prepared",
             project="Podcast", timeline="Reel 09")
    _journal(project, transaction_id="tx-aborted", stage="abandoned",
             project="Podcast", timeline="Reel 09",
             reason="the request was refused before its first write")
    axi_transaction._JOURNAL_CURSORS.clear()

    state = axi_transaction.require_recovery_clear(
        str(project), project="Podcast", timeline="Reel 09")

    assert not state.unresolved
    assert compound_transaction.aborted_pre_write_hold(
        str(project), "tx-aborted") is False


def test_a_transaction_that_may_have_written_keeps_its_hold(tmp_path):
    """The recovery that is real is never auto-accepted."""
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-uncertain", stage="applying",
             project="Podcast", timeline="Reel 09", pid=_dead_pid(),
             operations=[{"op": "clip.delete"}])
    axi_transaction._JOURNAL_CURSORS.clear()

    assert compound_transaction.aborted_pre_write_hold(
        str(project), "tx-uncertain") is True
    with pytest.raises(axi_transaction.TransactionRefused,
                       match="unresolved recovery"):
        axi_transaction.require_recovery_clear(
            str(project), project="Podcast", timeline="Reel 09")


# ── 5. A possibly committed mutation is never retried silently ───────


def test_a_transaction_that_may_have_committed_is_not_written_again(tmp_path):
    """Defect: the same call could be submitted again, and wrote again.

    The journal showed the transaction at a stage that is neither a
    commit nor a delivery: it may have written, and the outcome is not
    observable. The second submission has to be refused.
    """
    project = tmp_path / "project"
    _journal(project, transaction_id="tx-9", stage="applying",
             project="Podcast", timeline="Reel 09", pid=_dead_pid(),
             operations=[{"op": "clip.delete", "unique_id": "a"}])
    from library.tools import axi_transaction

    axi_transaction._JOURNAL_CURSORS.clear()

    verdict = compound_transaction.preflight(
        project_folder=str(project), transaction_id="tx-9",
        operations=[{"op": "clip.delete", "unique_id": "a"}],
        before=_snapshot(_clip("a")))

    assert verdict["status"] == "refused"
    assert verdict["category"] == "recovery_required"
    assert verdict["mutated"] is True


def test_a_transaction_that_was_never_submitted_is_not_refused(tmp_path):
    project = tmp_path / "project"
    from library.tools import axi_transaction

    axi_transaction._JOURNAL_CURSORS.clear()

    verdict = compound_transaction.preflight(
        project_folder=str(project), transaction_id="tx-new",
        operations=[{"op": "clip.delete", "unique_id": "a"}],
        before=_snapshot(_clip("a")))

    assert verdict["status"] == "ok"


def test_a_committed_transaction_is_not_written_again(tmp_path):
    """Defect: a retry of committed work wrote the same work twice.

    The journal proved the transaction committed. The same call must be
    answered with the prior outcome, never with a second write.
    """
    from library.tools import axi_transaction

    project = tmp_path / "project"
    digest = axi_transaction.intent_digest(
        [{"op": "clip.delete", "unique_id": "a"}])
    _journal(project, transaction_id="tx-done", stage="prepared",
             project="Podcast", timeline="Reel 09", intent_digest=digest)
    _journal(project, transaction_id="tx-done", stage="committed",
             project="Podcast", timeline="Reel 09")
    axi_transaction._JOURNAL_CURSORS.clear()

    verdict = compound_transaction.preflight(
        project_folder=str(project), transaction_id="tx-done",
        operations=[{"op": "clip.delete", "unique_id": "a"}],
        before=_snapshot(_clip("a")))

    assert verdict["status"] == "refused"
    assert verdict["category"] == "conflict"
    assert verdict["mutated"] is False


def test_a_recovered_transaction_may_be_submitted_again(tmp_path):
    """A recovered transaction settled without a commit: nothing landed.

    The writes that landed were reversed, so the same intent is a NEW
    attempt, not a retry of a possibly committed mutation.
    """
    from library.tools import axi_transaction

    project = tmp_path / "project"
    _journal(project, transaction_id="tx-back", stage="recovered",
             project="Podcast", timeline="Reel 09")
    axi_transaction._JOURNAL_CURSORS.clear()

    verdict = compound_transaction.preflight(
        project_folder=str(project), transaction_id="tx-back",
        operations=[{"op": "clip.delete", "unique_id": "a"}],
        before=_snapshot(_clip("a")))

    assert verdict["status"] == "ok"


def test_the_same_transaction_id_for_different_work_is_refused(tmp_path):
    """The id was used for different operations: that is a conflict."""
    from library.tools import axi_transaction

    project = tmp_path / "project"
    digest = axi_transaction.intent_digest(
        [{"op": "clip.delete", "unique_id": "a"}])
    _journal(project, transaction_id="tx-mixed", stage="prepared",
             project="Podcast", timeline="Reel 09", intent_digest=digest)
    _journal(project, transaction_id="tx-mixed", stage="committed",
             project="Podcast", timeline="Reel 09")
    axi_transaction._JOURNAL_CURSORS.clear()

    verdict = compound_transaction.preflight(
        project_folder=str(project), transaction_id="tx-mixed",
        operations=[{"op": "clip.delete", "unique_id": "b"}],
        before=_snapshot(_clip("a"), _clip("b")))

    assert verdict["status"] == "refused"
    assert verdict["category"] == "conflict"
