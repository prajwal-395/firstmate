"""The broker's queue must be legible, and its dead jobs reaped.

Measured 2026-10-21 on the machine's own broker (36 hours, 8449 jobs):
a lane that waited could not tell WHAT it was waiting for. The receipts
said a job was submitted, when it started and how long it held Resolve;
they said nothing about where the job sat, what blocked it, or which
resource it was waiting on. So a fifteen-minute wait and a fifteen-
minute hold looked the same from the outside, and the queue's own
report could not tell a lane that was behind a long exclusive session
from a lane that was behind NOTHING - the broker sitting idle with work
queued.

The second defect is the same hole from the other side: a GRANT whose
owner is gone. The client that asked for it is the connection that
holds it, so a client that dies while waiting leaves its grant queued
with nobody waiting for it - and the broker's own connection thread
stays blocked on that turn for the whole lease timeout (900 s by
default). Measured on the live broker: eight such grants sat in the
queue of a busy machine, two of them past 600 s, and the queue's
positions meant nothing while they were there.

Each test names its defect. None of them touches a real Resolve: the
broker is the in-process one (`library.tools.resolved.server.Broker`),
its store is a file in a temporary directory, and the "dead owner" is a
process that really exited.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from library.tools.resolved import queue_status
from library.tools.resolved.job_owner import owner_alive, owner_pid
from library.tools.resolved.kpi import occupancy, wait_causes
from library.tools.resolved.server import Broker
from library.tools.resolved.store import JobStore


def _dead_pid() -> int:
    """A pid that really exited."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    return finished.pid


def _job(job_id: str, **overrides) -> dict:
    job = {
        "id": job_id, "kind": "lease", "priority": "mutation",
        "mode": "exclusive", "executed": False, "params": {},
        "project": "", "timeline": "", "qualification": False,
        "coalesce_key": None, "owner": "", "description": "",
        "progress": "", "state": "queued", "subscribers": 1,
        "submitted": 1000.0, "started": None, "finished": None,
        "result": None, "error": "",
    }
    job.update(overrides)
    return job


# ── The queue report ────────────────────────────────────────────────


def test_a_queued_job_is_named_with_its_position_and_its_wait_cause():
    """A lane waiting must be able to see where it is and what blocks it."""
    reply = {
        "jobs": [
            _job("running-1", state="running", started=990.0,
                 kind="resolve.qualification_session", executed=True,
                 description="Running the Resolve qualification",
                 owner="pid-4242"),
            _job("waiting-1", submitted=960.0, priority="qualification",
                 description="Running the Resolve qualification"),
        ],
        "running": [{
            "id": "running-1", "priority": "qualification", "mode": "exclusive",
            "submitted": 990.0, "project": "Ren Qualification", "timeline": "",
            "executed": True, "qualification": True, "locality": "project",
        }],
    }
    report = queue_status.queue_report(reply, now=1000.0)
    assert report["depth"] == 1
    waiting = report["queued"][0]
    assert waiting["id"] == "waiting-1"
    assert waiting["position"] == 1
    assert waiting["waited_seconds"] == pytest.approx(40.0)
    assert "exclusive job holds Resolve" in waiting["wait_cause"]
    assert waiting["blocked_by"][0]["id"] == "running-1"
    assert report["running"][0]["description"] == (
        "Running the Resolve qualification")


def test_a_queue_with_nothing_running_says_so():
    """The case a queue report must not paper over.

    A job waiting with NOTHING running is waiting on the broker, not on
    the machine - and that is the difference between a slow machine and
    a stuck scheduler.
    """
    reply = {"jobs": [_job("waiting-1", submitted=960.0)], "running": []}
    report = queue_status.queue_report(reply, now=1000.0)
    assert "nothing is running" in report["queued"][0]["wait_cause"]


def test_the_lease_holder_outside_the_broker_is_named_as_a_blocker():
    """A lease held by nobody the broker is running blocks every writer."""
    reply = {"jobs": [_job("waiting-1", submitted=960.0)], "running": []}
    report = queue_status.queue_report(
        reply,
        lease={"owner": "pid-9090", "purpose": "a trim on a scratch copy",
               "pid": 9090, "since": 990.0},
        now=1000.0)
    cause = report["queued"][0]["wait_cause"]
    assert "pid-9090" in cause
    assert report["blocked_resources"]
    assert "pid-9090" in report["blocked_resources"][0]


def test_a_dead_owner_is_named_in_the_queue_report():
    """A grant whose owner is gone is a job nobody waits for."""
    dead = _dead_pid()
    reply = {"jobs": [_job("waiting-1", submitted=960.0,
                           owner=f"pid-{dead}")], "running": []}
    report = queue_status.queue_report(reply, now=1000.0)
    assert report["queued"][0]["owner_alive"] is False
    assert report["dead_owner_jobs"] == 1
    assert "is gone" in report["queued"][0]["wait_cause"]


def test_the_owner_of_a_job_carries_its_pid():
    """The pid is the only part of an owner a broker can CHECK."""
    assert owner_pid("pid-1234") == 1234
    assert owner_pid("lane-3 (pid-1234)") == 1234
    assert owner_pid("axi001") is None
    assert owner_pid("") is None
    assert owner_alive("axi001") is None
    assert owner_alive(f"pid-{_dead_pid()}") is False
    assert owner_alive(f"pid-{__import__('os').getpid()}") is True


# ── The reaping ─────────────────────────────────────────────────────


def test_a_queued_grant_whose_owner_is_gone_is_reaped(tmp_path):
    """The dead owner's grant must not hold its place in the queue.

    The defect: a client that dies while waiting for its turn leaves its
    grant queued, and the broker's connection thread stays blocked on
    that turn for the whole lease timeout. The grant holds a place every
    other lane waits behind, and nobody is waiting for it.
    """
    store = JobStore(tmp_path / "jobs.sqlite3")
    broker = Broker(store, connect=lambda: None)
    try:
        dead = _dead_pid()
        # A grant that is GRANTED and never released: it holds the
        # exclusive turn, so the next grant stays queued behind it.
        held = broker.submit("lease", {"purpose": "held", "exclusive": True},
                              owner="pid-1")
        assert broker.wait_started(held["id"], 5.0)
        # The dead lane's grant: queued behind the held one.
        abandoned = broker.submit("lease",
                                  {"purpose": "abandoned", "exclusive": True},
                                  owner=f"pid-{dead}")
        deadline = time.time() + 10.0
        while time.time() < deadline:
            receipt = store.get(abandoned["id"])
            if receipt and receipt["state"] == "cancelled":
                break
            time.sleep(0.05)
        receipt = store.get(abandoned["id"])
        assert receipt["state"] == "cancelled", (
            "the dead owner's grant was still queued: "
            f"{receipt['state']}")
        assert "owner" in receipt["error"]
    finally:
        broker.stop()
        store.close()


def test_a_live_owner_is_never_reaped(tmp_path):
    """A live lane's grant is nobody's to cancel."""
    import os

    store = JobStore(tmp_path / "jobs.sqlite3")
    broker = Broker(store, connect=lambda: None)
    try:
        held = broker.submit("lease", {"purpose": "held", "exclusive": True},
                              owner="pid-1")
        assert broker.wait_started(held["id"], 5.0)
        live = broker.submit("lease", {"purpose": "live", "exclusive": True},
                             owner=f"pid-{os.getpid()}")
        time.sleep(1.5)
        receipt = store.get(live["id"])
        assert receipt["state"] == "queued", (
            "a live owner's grant was reaped")
    finally:
        broker.stop()
        store.close()


def test_a_queued_job_whose_owner_is_gone_is_reaped(tmp_path):
    """A job that has NOT STARTED and whose owner is dead leaves the queue.

    Nobody is left to read its receipt, verify it or promote it, and
    Ren never lands an unverified write it cannot account for
    (captain's ruling, 2026-10-21). Measured the same day: five
    dead-owner jobs held places 1, 2, 3, 8 and 11 of a busy queue for
    365-2032 s, because the broker reaped them only when they reached
    the front - which on a busy machine never came.
    """
    store = JobStore(tmp_path / "jobs.sqlite3")
    broker = Broker(store, connect=lambda: None)
    try:
        held = broker.submit("lease", {"purpose": "held", "exclusive": True},
                              owner="pid-1")
        assert broker.wait_started(held["id"], 5.0)
        dead = _dead_pid()
        submitted = broker.submit(
            "resolve_axi", {"argv": ["project"]}, owner=f"pid-{dead}")
        time.sleep(1.5)
        receipt = store.get(submitted["id"])
        assert receipt["state"] == "cancelled", (
            "the dead owner's job stayed in the queue")
        assert receipt["error"] == "reaped:owner-gone", (
            "the receipt did not say why it was reaped: %r"
            % receipt["error"])
    finally:
        broker.stop()
        store.close()


# ── The measurement ─────────────────────────────────────────────────


def test_the_measurement_separates_an_idle_broker_from_a_busy_one():
    """The number the queue report cannot give.

    `idle_with_queue_s` says whether a long wait was the MACHINE's or
    the BROKER's: a broker holding nothing while work waits for it is a
    different defect from a machine busy with exclusive work.
    """
    jobs = [
        # A job that waits from 100 to 200, and the job holding Resolve
        # from 120 to 180: the queue is busy, not idle.
        _job("waiter", submitted=100.0, started=200.0, finished=210.0),
        _job("holder", submitted=110.0, started=120.0, finished=180.0,
             state="done"),
        # A job that waits from 300 to 400 with NOTHING running.
        _job("idle-waiter", submitted=300.0, started=400.0, finished=410.0),
    ]
    report = occupancy(jobs, until=500.0)
    # The waiter is queued from 100 to 200; the holder holds Resolve from
    # 120 to 180. So 60 s of the wait is the machine's, 40 s of it is the
    # broker's - plus the 100 s the second waiter spends with nothing
    # running at all.
    assert report["busy_with_queue_s"] == pytest.approx(60.0)
    assert report["idle_with_queue_s"] == pytest.approx(140.0)


def test_the_measurement_names_what_held_the_queue():
    """A wait explained, not only counted."""
    jobs = [
        _job("waiter", submitted=100.0, started=200.0, finished=210.0),
        _job("holder", submitted=120.0, started=120.0, finished=180.0,
             state="done", kind="resolve.qualification_session",
             description="Running the Resolve qualification"),
    ]
    causes = wait_causes(jobs, until=500.0)
    assert causes[0]["kind"] == "resolve.qualification_session"
    assert causes[0]["blocked_s"] == pytest.approx(60.0)
    assert causes[0]["share"] == pytest.approx(0.6)
    assert causes[0]["waiters"] == 1
    # The rest of the wait - the 20 s before the holder started and the
    # 20 s after it finished - had NOTHING running: the broker was not
    # scheduling, which is a different defect from a busy machine.
    idle = [row for row in causes if row["kind"] == "NOTHING RUNNING"]
    assert idle and idle[0]["blocked_s"] == pytest.approx(40.0)
