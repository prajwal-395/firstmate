"""ONE broker session for a multi-call Resolve operation.

The defect this file answers (2026-10-22, a live Ren Scratch reels
build): the build took a lease per section, and EVERY lease is a broker
CALL. Between two of the build's calls the broker was free to grant
ANOTHER lane's turn, and that lane's exclusive lease moved the cursor
to its own project - so the build's next section found a project that
was not its own, and the reel it placed was placed against another
lane's timeline.

The session is the fix: the operation holds ONE exclusive broker claim
across its whole duration, so no other lane's turn is granted between
two of its calls.

The test that names the defect runs TWO LANES - one multi-call build
and one lane that tries to switch the project in the build's gap - and
measures BOTH shapes:

* without a session, the switch lands IN the gap (the defect);
* with a session, the switch WAITS for the build to end.

Both halves are measured, so this file fails if the session stops
holding the claim, and it fails if the test stops being able to see the
defect at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
from pathlib import Path

import pytest

from library.tools import resolve_lock, resolve_session
from library.tools.resolved import client
from library.tools.resolved.server import Broker, _Handler, _Server
from library.tools.resolved.store import JobStore

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def broker_dir(monkeypatch):
    """A short directory: a Unix socket path is capped near 104 bytes."""
    directory = Path(tempfile.mkdtemp(prefix="rs", dir="/tmp"))
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(directory))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def serving(broker_dir):
    """A broker on a real socket, over a Resolve double."""
    store = JobStore(broker_dir / "ren-resolved.sqlite3")
    broker = Broker(store, connect=lambda: None)
    path = broker_dir / "ren-resolved.sock"
    server = _Server(str(path), _Handler)
    server.broker = broker
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield broker
    server.shutdown()
    broker.stop()
    server.server_close()
    store.close()


# ── The two lanes ───────────────────────────────────────────────────
#
# The build lane takes two leases with a gap between them - the shape
# of a multi-call operation. The switching lane waits for the build's
# first section, then asks for the instance, which is what another lane
# switching the current project does.

_LANE = textwrap.dedent("""
    import os
    import sys
    import time
    from contextlib import nullcontext

    from library.tools import resolve_lock
    from library.tools.resolve_session import resolve_session

    role, lock_dir, log_path, use_session = sys.argv[1:5]
    os.environ[resolve_lock.LOCK_DIR_ENV] = lock_dir
    os.environ.pop(resolve_lock.INHERIT_ENV, None)
    os.environ.pop(resolve_lock.INHERIT_MODE_ENV, None)
    os.environ.pop("REN_PERF_LEDGER", None)

    def log(event):
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write("%.3f %s %s\\n" % (time.time(), role, event))
            handle.flush()

    def logged(event):
        try:
            with open(log_path, "r", encoding="utf-8") as handle:
                return any(line.rstrip().endswith(" " + event)
                           for line in handle)
        except FileNotFoundError:
            return False

    def wait_for(event, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if logged(event):
                return True
            time.sleep(0.02)
        return False

    if role == "build":
        session = (resolve_session("reels build") if use_session == "1"
                   else nullcontext())
        with session:
            with resolve_lock.resolve_lease("build reels connect",
                                            exclusive=True):
                log("first-section")
                # The gap: the build is between its two calls here, and
                # the other lane's switch is waiting for the instance.
                wait_for("switch-granted", 10)
                log("gap-closed")
            with resolve_lock.resolve_lease("place Reel 01",
                                            exclusive=True):
                log("second-section")
                time.sleep(0.5)
            log("build-end")
        log("build-done")
    else:
        wait_for("first-section", 30)
        log("switch-attempt")
        with resolve_lock.resolve_lease("switch project", exclusive=True):
            log("switch-granted")
""")


def _events(log: Path) -> list:
    if not log.exists():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        stamp, role, event = line.split(" ", 2)
        rows.append((float(stamp), role, event))
    return rows


def _run_lanes(lock_dir: Path, log: Path, use_session: bool) -> list:
    env = dict(os.environ)
    env[resolve_lock.LOCK_DIR_ENV] = str(lock_dir)
    env.pop(resolve_lock.INHERIT_ENV, None)
    env.pop(resolve_lock.INHERIT_MODE_ENV, None)
    env.pop("REN_PERF_LEDGER", None)
    build = subprocess.Popen(
        [sys.executable, "-c", _LANE, "build", str(lock_dir), str(log),
         "1" if use_session else "0"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    switch = subprocess.Popen(
        [sys.executable, "-c", _LANE, "switch", str(lock_dir), str(log),
         "0"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    out_build, err_build = build.communicate(timeout=180)
    out_switch, err_switch = switch.communicate(timeout=180)
    assert build.returncode == 0, (out_build, err_build)
    assert switch.returncode == 0, (out_switch, err_switch)
    return _events(log)


@pytest.mark.serial(
    reason="Two real lane processes contend for one broker and one flock.")
def test_without_a_session_the_switch_lands_in_the_build_s_gap(serving):
    """The defect, measured: the gap between two of the build's calls is
    where another lane's switch lands.

    Without the session the build releases the broker between its two
    leases, the other lane's turn is granted in that gap, and its
    exclusive lease moves the cursor - which is how a build loses its
    project.
    """
    events = _run_lanes(Path(os.environ[resolve_lock.LOCK_DIR_ENV]),
                         Path(tempfile.mkdtemp(prefix="rl", dir="/tmp")) / "log",
                         use_session=False)
    names = [event for _, _, event in events]
    assert "switch-attempt" in names, names
    assert "switch-granted" in names, names
    first = names.index("first-section")
    granted = names.index("switch-granted")
    second = names.index("second-section")
    assert first < granted < second, (
        f"the switch did not land in the gap, so this file can no longer "
        f"see the defect it names: {names}")


@pytest.mark.serial(
    reason="Two real lane processes contend for one broker and one flock.")
def test_a_session_holds_the_build_s_project_against_a_switching_lane(
        serving):
    """The fix, measured: the switching lane WAITS for the session.

    The build holds one exclusive broker claim across its two calls, so
    the other lane's turn is not granted in the gap - it is granted when
    the session ends, after the build's own last section.
    """
    lock_dir = Path(os.environ[resolve_lock.LOCK_DIR_ENV])
    log = Path(tempfile.mkdtemp(prefix="rl", dir="/tmp")) / "log"
    events = _run_lanes(lock_dir, log, use_session=True)
    names = [event for _, _, event in events]
    assert "switch-attempt" in names, names
    assert "switch-granted" in names, names
    granted = names.index("switch-granted")
    ended = names.index("build-end")
    assert granted > ended, (
        f"the switching lane was granted the instance at {granted}, "
        f"before the build's session ended at {ended}: the session did "
        f"not hold the claim across the build's calls. Events: {names}")


# ── One session is one claim ─────────────────────────────────────────


def test_a_session_takes_one_broker_claim_for_the_whole_operation(
        serving, monkeypatch):
    """Every lease inside the session is served by ONE claim.

    The operation's own leases are not new broker calls: they take the
    instance under the claim the session already holds. A second claim
    would queue behind the first and wait for the session that is
    waiting for it.
    """
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    with resolve_session.resolve_session("reels build"):
        with resolve_lock.resolve_lease("build reels connect",
                                        exclusive=True):
            pass
        with resolve_lock.resolve_lease("place Reel 01", exclusive=True):
            pass
    leases = [job for job in serving.store.recent(50)
              if job["kind"] == "lease"]
    assert len(leases) == 1, (
        f"the build took {len(leases)} broker claims: "
        f"{[job['params'].get('purpose') for job in leases]}")
    assert leases[0]["params"].get("purpose") == "reels build"


def test_the_session_is_released_when_the_operation_ends(serving,
                                                         monkeypatch):
    """The claim is released at the end: the next lane gets the queue."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    with resolve_session.resolve_session("reels build"):
        assert resolve_session.claim_held()
    assert not resolve_session.claim_held()
    assert not [job for job in serving.store.recent(50)
                if job["kind"] == "lease" and job["state"] == "running"]
    # The queue is free again: another lane's turn is granted at once.
    with client.grant({"purpose": "the next lane", "exclusive": True},
                      wait=10) as job_id:
        assert job_id


def test_a_session_inside_a_session_is_the_same_session(serving,
                                                        monkeypatch):
    """A nested session joins the enclosing one; it does not ask again."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    with resolve_session.resolve_session("reels build") as outer:
        with resolve_session.resolve_session("reel delivery") as inner:
            assert inner is outer
            assert inner.purpose == "reels build"
        assert resolve_session.current_session() is outer
    assert resolve_session.current_session() is None


def test_a_session_on_another_thread_joins_the_one_this_process_holds(
        serving, monkeypatch):
    """A second thread must not ask for a SECOND claim.

    The claim belongs to the PROCESS, and a second one would queue
    behind the first and wait for a turn that cannot come until the
    session holding it ends.
    """
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    with resolve_session.resolve_session("reels build") as outer:
        joined = {}

        def other_thread():
            with resolve_session.resolve_session("reel delivery") as inner:
                joined["session"] = inner

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), (
            "the second thread waited for a claim the first one holds")
        assert joined["session"].joined
        assert not joined["session"].claimed
        assert outer.claimed
    leases = [job for job in serving.store.recent(50)
              if job["kind"] == "lease"]
    assert len(leases) == 1, (
        f"the process took {len(leases)} claims: "
        f"{[job['params'].get('purpose') for job in leases]}")


def test_a_broker_job_inside_a_session_is_refused(serving, monkeypatch):
    """A job queued inside a session would deadlock, so it is refused.

    The session holds the broker's turn; a job queued behind it waits
    for the session, and the session is waiting for the job.
    """
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    with resolve_session.resolve_session("reels build"):
        with pytest.raises(client.BrokerError) as refused:
            client.submit("timeline.snapshot",
                          {"project": "Podcast", "timeline": "Master"})
    assert "session" in str(refused.value)


def test_a_session_records_what_it_holds(serving, monkeypatch):
    """The record names the operation, so a waiter can see what it waits
    for."""
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    with resolve_session.resolve_session(
            "reels build", description="reels build scratch") as session:
        assert session.claimed
        assert session.claim_job_id
        assert "reels build" in session.describe()
        assert resolve_session.live_sessions() == [session]
        assert resolve_session.claim_job_id() == session.claim_job_id
    assert resolve_session.live_sessions() == []
