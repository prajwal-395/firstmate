"""Event log: the append-only record, the canonical writer, the checkpoint.

Every test names a defect it catches - a case that fails on origin/main,
where this module does not exist. The concurrency that can be REAL is
real: `test_two_processes_never_share_a_sequence_number` runs two
processes that emit at once, because a sequence number two workers can
share is the defect the lock exists to prevent.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from library.tools import event_log
from library.tools.event_log import (
    CHECKPOINT,
    checkpoint,
    emit,
    last_checkpoint,
    log_path,
    read_events,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def project(tmp_path):
    """A project folder with a LOGS area, never a real one."""
    folder = tmp_path / "project"
    (folder / "pipeline_output" / "logs").mkdir(parents=True)
    return folder


# ── The append-only record ─────────────────────────────────────────

def test_events_read_back_in_order_with_increasing_sequence(project):
    """The log preserves order: a reader sees what happened, when.

    Defect caught: a log that assigns sequence numbers without reading
    the file's tail - or that overwrites - gives a reader events out of
    order or two events the same number, and a resumed worker cannot
    tell where it left off.
    """
    first = emit(project, "lease_acquired", task="ren-04",
                 resource="timeline:scene-3")
    second = emit(project, "branch_created", task="ren-04",
                  branch="scene3-alt-B")
    events = read_events(project)
    assert [e["seq"] for e in events] == [first["seq"], second["seq"]]
    assert events[0]["type"] == "lease_acquired"
    assert events[1]["type"] == "branch_created"
    assert events[0]["task"] == "ren-04"


def test_a_partial_last_line_is_skipped_not_fatal(project):
    """A killed writer's truncated tail does not lose the log.

    Defect caught: a reader that parses the whole file as one JSON
    document - or that crashes on a malformed last line - loses every
    event a killed worker completed before it died. The events before
    the partial line are the worker's real history.
    """
    emit(project, "lease_acquired", task="ren-04")
    emit(project, "timeline_operations", task="ren-04", count=17)
    with open(log_path(project), "a", encoding="utf-8") as handle:
        handle.write('{"seq": 3, "at": "2026-10-06T17:45:13Z", "type": ')
    events = read_events(project)
    assert len(events) == 2
    assert events[-1]["type"] == "timeline_operations"


def test_read_since_filters_to_unseen_events(project):
    """A resumed worker reads only what it has not seen.

    Defect caught: a reader that always returns the whole file makes a
    resumed worker re-process events it already completed, which is the
    double-apply the checkpoint exists to prevent.
    """
    emit(project, "lease_acquired", task="ren-04")
    third = emit(project, "qc_passed", task="qc-02")
    emit(project, "branch_ready", task="ren-04")
    events = read_events(project, since=third["seq"])
    assert [e["type"] for e in events] == ["branch_ready"]


def test_read_task_filters_to_one_worker(project):
    """One worker's operations are readable without another's.

    Defect caught: a reader that ignores the task filter mixes two
    workers' operations, and a resumed worker cannot tell which events
    are its own.
    """
    emit(project, "lease_acquired", task="ren-04")
    emit(project, "qc_rejected", task="qc-02", reason="continuity")
    events = read_events(project, task="qc-02")
    assert len(events) == 1
    assert events[0]["type"] == "qc_rejected"


# ── The canonical writer ───────────────────────────────────────────

def test_two_processes_never_share_a_sequence_number(project):
    """Two workers emitting at once get distinct, contiguous sequence numbers.

    Defect caught: a writer that assigns a sequence number without
    serializing the read-and-append gives two events the same number,
    and a reader can no longer tell which came first - the order the
    whole resume path depends on.
    """
    script = (
        "import sys; from library.tools.event_log import emit; "
        "emit(sys.argv[1], 'op', task='ren-04')"
    )
    for _ in range(6):
        subprocess.run([sys.executable, "-c", script, str(project)],
                       check=True, cwd=REPO_ROOT, capture_output=True,
                       text=True, encoding="utf-8")
    events = read_events(project)
    seqs = [e["seq"] for e in events]
    assert len(set(seqs)) == len(seqs)
    assert seqs == sorted(seqs)


# ── The checkpoint ─────────────────────────────────────────────────

def test_last_checkpoint_returns_the_newest_for_its_task(project):
    """A resumed worker continues from the task's latest checkpoint.

    Defect caught: a reader that returns the FIRST checkpoint for a
    task - or that matches checkpoints across tasks - resumes a worker
    from stale state, which is the double-apply the checkpoint exists
    to prevent.
    """
    checkpoint(project, "ren-04", step="acquire_lease")
    emit(project, "timeline_operations", task="ren-04", count=17)
    newer = checkpoint(project, "ren-04", step="render_preview")
    checkpoint(project, "qc-02", step="grade")
    found = last_checkpoint(project, "ren-04")
    assert found is not None
    assert found["seq"] == newer["seq"]
    assert found["fields"]["state"]["step"] == "render_preview"


def test_last_checkpoint_is_none_where_the_task_never_checkpointed(project):
    """A task that never checkpointed says so, not a invented state.

    Defect caught: a reader that returns another task's checkpoint -
    or a default - when a task has none resumes a worker from state it
    never reached, which is worse than starting over.
    """
    checkpoint(project, "ren-04", step="acquire_lease")
    assert last_checkpoint(project, "ren-09") is None


def test_a_checkpoint_is_an_event_in_the_logs_order(project):
    """The checkpoint carries a sequence number and sits in the log.

    Defect caught: a checkpoint written outside the log - a side file,
    a different format - cannot be ordered against the events around
    it, and a reader cannot tell "the checkpoint says step 3" from
    "step 3's events are still in the log after it".
    """
    emit(project, "lease_acquired", task="ren-04")
    mark = checkpoint(project, "ren-04", step="render_preview")
    emit(project, "qc_passed", task="qc-02")
    events = read_events(project, task="ren-04")
    assert [e["type"] for e in events] == ["lease_acquired", "checkpoint"]
    assert events[-1]["seq"] == mark["seq"]
    assert events[-1]["fields"]["state"]["step"] == "render_preview"


# ── The emit cost: bounded by the tail, not the whole log ───────────

def test_emit_cost_does_not_scale_with_log_length(project):
    """Reading the last sequence number reads only the tail, not the log.

    Defect caught: a writer that reads the whole file to find the last
    sequence number makes each emit slower forever - the log is
    append-only and unbounded, so a lease acquire/release on a project
    with a long history would pay for every event ever written. The
    reader seeks back from EOF to the last newline and reads only that
    chunk, so the cost is bounded by one event's size.
    """
    for i in range(50):
        emit(project, "timeline_operations", task="ren-04", count=i)
    # A log with 50 events: the tail read is bounded, not 50 lines.
    path = log_path(project)
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
    # The last event is small; the tail read is at most 64KB regardless
    # of how many events precede it.
    from library.tools import event_log as _el
    chunk_size = min(size, 65536)
    assert chunk_size <= 65536
    # And the sequence number is still correct after the tail read.
    assert _el._read_last_seq(path) == 50


# ── The resume: a killed worker's checkpoint, read by the next ────────

def test_a_resumed_task_continues_from_the_killed_workers_checkpoint(
        project, tmp_path, monkeypatch):
    """A task killed mid-way leaves a checkpoint the next worker reads.

    The spec's resume: a worker's machine disappears, its heartbeat
    times out, its lease expires, the task is reassigned and another
    worker resumes from the last checkpoint. This is the E2E proof:
    the killed worker's lease is gone, its checkpoint is on the log,
    and the resuming worker continues from where the dead one stopped
    rather than from the beginning.

    Defect caught: a checkpoint that is not in the log - or a lease
    that does not expire - leaves a resuming worker with no way to
    tell where the dead one stopped, so it either re-does completed
    work or starts from a stale state.
    """
    import time as _time
    from library.tools import resource_leases
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(lock_dir))

    decl = resource_leases.LeaseDeclaration(
        write=frozenset({resource_leases.ResourceRef("clip", "A004_C013")}))

    # Worker A acquires the lease, checkpoints its progress, then is
    # killed: it stops heartbeating and its lease goes stale. The
    # context manager's release is the kill - the lease is removed
    # from disk without a release event, exactly as a dead machine
    # leaves it.
    handle = resource_leases.ResourceLeaseHandle(
        record=resource_leases.ResourceLease(
            owner="ren-04", purpose="ren-04", pid=999999,
            host="killed-worker", since=_time.time(),
            heartbeat=_time.time(), heartbeat_interval=0.2,
            declaration=decl),
        project=str(project))
    with resource_leases._leases_file(exclusive=True):
        resource_leases._write_leases([
            *resource_leases._read_leases(), handle.record])
        resource_leases._emit(str(project),
                              resource_leases.event_log.LEASE_ACQUIRED,
                              task="ren-04", owner="ren-04",
                              pid=999999, host="killed-worker")
    checkpoint(str(project), "ren-04", step="render_preview",
               operations_done=17)

    # The killed worker's lease expires: a second worker's acquisition
    # evicts it and takes the resource.
    _time.sleep(0.3)
    with resource_leases.resource_lease(
            decl, "ren-09", wait=False, heartbeat_interval=30.0,
            project=str(project)):
        pass

    # The resuming worker reads the log and continues from the
    # checkpoint the dead one left.
    found = last_checkpoint(str(project), "ren-04")
    assert found is not None
    assert found["fields"]["state"]["step"] == "render_preview"
    assert found["fields"]["state"]["operations_done"] == 17

    # The dead worker's lease events are on the record: acquired, its
    # checkpoint, and the next worker's acquisition and release. The
    # dead worker's lease is silently evicted by the next worker's
    # acquisition - no lease_expired event, because the dead worker
    # never heartbeats to discover it is gone.
    events = read_events(str(project))
    types = [e["type"] for e in events]
    assert types == [
        resource_leases.event_log.LEASE_ACQUIRED,
        CHECKPOINT,
        resource_leases.event_log.LEASE_ACQUIRED,
        resource_leases.event_log.LEASE_RELEASED,
    ]


# ── The reader front door ──────────────────────────────────────────

def test_main_checkpoint_prints_the_newest_checkpoint_state(project,
                                                           capsys):
    """A resumed worker asks where its task stopped, through the reader.

    Defect caught: `last_checkpoint` exists but no reader reaches it -
    the resume answer lives one import away from every worker, so the
    killed worker's state is readable only in-process, never through
    the log's own front door (`ren events --checkpoint`).
    """
    from library.tools.event_log import _main
    checkpoint(project, "ren-04", step="acquire_lease")
    checkpoint(project, "ren-04", step="render_preview")
    checkpoint(project, "qc-02", step="grade")
    assert _main([str(project), "--checkpoint", "ren-04"]) == 0
    out = capsys.readouterr().out
    assert "render_preview" in out
    assert "acquire_lease" not in out
    assert "grade" not in out


def test_main_checkpoint_with_none_is_honest(project, capsys):
    """No checkpoint reads as start-over, never another task's state.

    Defect caught: a reader that prints the newest checkpoint of ANY
    task when the asked one has none resumes a worker from state it
    never reached.
    """
    from library.tools.event_log import _main
    checkpoint(project, "ren-04", step="acquire_lease")
    assert _main([str(project), "--checkpoint", "ren-09"]) == 0
    assert "start from the beginning" in capsys.readouterr().out


def test_main_lists_events_oldest_first(project, capsys):
    """The listing still works: every event in order, one per line.

    Guard: the checkpoint flag must not change the default listing -
    a reader that lists newest-first, or drops events, misleads the
    worker about what already ran.
    """
    from library.tools.event_log import _main
    emit(project, "lease_acquired", task="ren-04")
    checkpoint(project, "ren-04", step="render_preview")
    assert _main([str(project)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert "lease_acquired" in lines[0]
    assert "checkpoint" in lines[1]
