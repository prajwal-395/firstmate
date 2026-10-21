"""A dead owner's job leaves the queue; a running one finishes.

THE DEFECT (measured 2026-10-21): five jobs whose owner process was
dead held places 1, 2, 3, 8 and 11 of the broker's queue for
365-2032 s, each reading "holds its place in the queue until the broker
reaps it" - and the broker reaped them only when they reached the
front, which on a busy machine never came.

THE RULE (captain's ruling, 2026-10-21): a job that has NOT STARTED
and whose owner is dead is reaped promptly - on owner death, not when
it reaches the front. Nobody is left to read its receipt, verify it or
promote it, and Ren never lands an unverified write it cannot account
for. A job that is ALREADY RUNNING finishes, and its receipt is
recorded for whoever comes back. A LIVE owner is never reaped on idle
time.

These are the defects this could reintroduce, each named:

1. a queued job of ANY kind is reaped when its owner is gone, with the
   receipt saying `reaped:owner-gone`;
2. a RUNNING job with a dead owner is left to finish;
3. a LIVE owner's job stays in the queue.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

DEAD = "pid-999999"
LIVE = "pid-%d" % __import__("os").getpid()


class _FakeStore:
    """The broker's store, with one receipt per job."""

    def __init__(self, receipts: dict) -> None:
        self.receipts = receipts
        self.updates: list[tuple] = []

    def get(self, job_id: str):
        return self.receipts.get(job_id)

    def update(self, job_id: str, **fields) -> None:
        self.updates.append((job_id, fields))
        receipt = self.receipts.get(job_id)
        if receipt is not None:
            receipt.update(fields)


def _broker(store, queued: dict):
    from library.tools.resolved import server

    broker = server.Broker.__new__(server.Broker)
    broker.store = store
    broker._queued = queued
    broker._started = {}
    broker._coalesce = {}
    broker._cond = threading.Condition()
    return broker


def test_a_queued_job_whose_owner_is_gone_is_reaped(monkeypatch):
    """Any queued job, any kind: the dead owner's job leaves the queue."""
    from library.tools.resolved import job_owner

    monkeypatch.setattr(job_owner, "owner_alive",
                        lambda owner: False if owner == DEAD else True)
    receipts = {"executed": {"kind": "resolve_axi", "owner": DEAD,
                             "coalesce_key": None}}
    store = _FakeStore(receipts)
    broker = _broker(store, {"executed": {"id": "executed"}})

    with broker._cond:
        broker._reap_dead_owners()

    assert "executed" not in broker._queued, (
        "the dead owner's job stayed in the queue")
    state = [fields for job_id, fields in store.updates
             if job_id == "executed"]
    assert state, "the reaped job left no receipt"
    assert state[0].get("state") == "cancelled"
    assert state[0].get("error") == "reaped:owner-gone"


def test_a_running_job_with_a_dead_owner_is_left_to_finish(monkeypatch):
    """A job that is already running finishes: it may have done its work."""
    from library.tools.resolved import job_owner

    monkeypatch.setattr(job_owner, "owner_alive",
                        lambda owner: False if owner == DEAD else True)
    receipts = {"executed": {"kind": "resolve_axi", "owner": DEAD,
                             "coalesce_key": None}}
    store = _FakeStore(receipts)
    broker = _broker(store, {})

    with broker._cond:
        broker._reap_dead_owners()

    assert not store.updates, "a running job's receipt was rewritten"


def test_a_live_owners_job_stays_in_the_queue(monkeypatch):
    """A live owner is never reaped on idle time."""
    from library.tools.resolved import job_owner

    monkeypatch.setattr(job_owner, "owner_alive",
                        lambda owner: False if owner == DEAD else True)
    receipts = {"live": {"kind": "resolve_axi", "owner": LIVE,
                         "coalesce_key": None}}
    store = _FakeStore(receipts)
    broker = _broker(store, {"live": {"id": "live"}})

    with broker._cond:
        broker._reap_dead_owners()

    assert "live" in broker._queued
    assert not store.updates


def test_a_receipt_left_waiting_by_a_stopped_broker_is_closed_at_startup(
        tmp_path):
    """A receipt a stopped broker left WAITING is closed at startup.

    THE DEFECT: the queue is the broker's memory, so a job that was
    waiting when the broker stopped never runs - and its receipt said
    "queued" for ever, which every reader that counts the queue took
    as a live wait (measured 2026-10-21: a broker that was never idle
    because five dead-owner receipts sat in the queue).
    """
    from library.tools.resolved import server
    from library.tools.resolved.store import JobStore

    store = JobStore(tmp_path / "jobs.sqlite3")
    try:
        store.insert({
            "id": "waiting", "kind": "resolve_axi", "priority": "mutation",
            "mode": "exclusive", "executed": True, "params": {},
            "project": "", "timeline": "", "qualification": False,
            "coalesce_key": None, "owner": DEAD, "description": "submitted",
            "progress": "", "state": "queued", "subscribers": 1,
            "submitted": __import__("time").time(),
        })
        receipt = store.recent(10)[0]
        assert receipt["state"] == "queued"

        broker = server.Broker(store, connect=lambda: None)
        try:
            closed = store.get(receipt["id"])
            assert closed["state"] == "cancelled"
            assert "stopped before this job ran" in closed["error"]
        finally:
            broker.stop()
    finally:
        store.close()
