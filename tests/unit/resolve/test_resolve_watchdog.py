"""The Resolve-degraded watchdog, health status, and post-restart check."""

import contextlib
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools import resolve_watchdog as wd
from library.tools import timeline_shadow as shadow
from library.tools.marker_feedback import ResolveUnavailable
from library.tools.resolve_locale import scriptapp_preserving_locale
from library.tools.resolved import client
from library.tools.resolved import jobs as job_kinds
from library.tools.resolved.server import Broker, serve
from library.tools.resolved.store import JobStore
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


class _FakeDvr:
    """A DaVinciResolveScript double: `scriptapp` returns the fake Resolve."""

    def __init__(self, resolve):
        self._resolve = resolve

    def scriptapp(self, name: str = "Resolve"):
        return self._resolve


def _do_not_wait_for_deadline(_thread, _timeout):
    """Deterministic timeout scheduler for calls intentionally held open."""


@pytest.fixture
def unguarded(monkeypatch):
    """Undo conftest's session-wide sole-writer declaration.

    The suite's Resolve is a mock, so the session declares itself the
    sole writer - which would answer `held()` before any lease is taken
    and hide the defect this file tests for.
    """
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)


def test_watchdog_marks_degraded_when_call_never_returns():
    """A call that never returns marks Resolve unresponsive."""
    release = threading.Event()

    def never_connect():
        release.wait()
        return FakeResolve()

    watchdog = wd.ResolveWatchdog(
        connect=never_connect, probe_interval=0.01, connect_budget=0.05,
        deadline_waiter=_do_not_wait_for_deadline)
    watchdog._probe()
    assert watchdog.is_degraded()
    status = watchdog.status()
    assert status["connected"] is False
    assert status["responsive"] is False
    assert "connect failed" in status["degraded_reason"]
    release.set()


def test_watchdog_marks_degraded_when_probe_never_returns():
    """A probe that never returns marks Resolve unresponsive."""
    release = threading.Event()

    class StuckResolve(FakeResolve):
        def GetProjectManager(self):
            release.wait()
            return super().GetProjectManager()

    def connect():
        return StuckResolve()

    watchdog = wd.ResolveWatchdog(
        connect=connect, probe_interval=0.01, connect_budget=0.05,
        round_trip_budget=0.05, deadline_waiter=_do_not_wait_for_deadline)
    watchdog._probe()
    assert watchdog.is_degraded()
    status = watchdog.status()
    assert status["connected"] is True
    assert status["responsive"] is False
    assert "probe failed" in status["degraded_reason"]
    release.set()


def test_watchdog_stays_healthy_when_resolve_answers():
    """A responsive Resolve keeps the watchdog healthy."""
    resolve = FakeResolve()

    def connect():
        return resolve

    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.01)
    watchdog._probe()
    assert not watchdog.is_degraded()
    status = watchdog.status()
    assert status["connected"] is True
    assert status["responsive"] is True


def test_watchdog_uses_injected_probe_scheduler():
    """A fake scheduler advances one probe without a wall-clock wait."""
    calls = []

    def scheduler(stop, interval):
        calls.append(interval)
        if len(calls) == 2:
            stop.set()
        return stop.is_set()

    watchdog = wd.ResolveWatchdog(
        connect=FakeResolve, probe_interval=17, probe_scheduler=scheduler)
    watchdog.start()
    watchdog._thread.join(timeout=1)
    assert not watchdog._thread.is_alive()
    status = watchdog.status()
    assert calls == [17, 17]
    assert status["connected"] is True
    assert status["responsive"] is True


def test_health_status_connected_and_responsive():
    resolve = FakeResolve()
    status = wd.health_status(resolve)
    assert status == {"connected": True, "responsive": True}


def test_health_status_not_connected():
    status = wd.health_status(None)
    assert status == {"connected": False, "responsive": False}


def test_health_status_connected_but_not_responsive():
    release = threading.Event()

    class StuckResolve(FakeResolve):
        def GetProjectManager(self):
            release.wait()
            return super().GetProjectManager()

    status = wd.health_status(StuckResolve(), _do_not_wait_for_deadline)
    release.set()
    assert status == {"connected": True, "responsive": False}


def test_post_restart_check_ok():
    resolve = FakeResolve()
    project = make_project(name="Test Project")
    resolve._manager = type(resolve._manager)(project)
    result = wd.post_restart_check(resolve)
    assert result["ok"] is True
    assert result["project"] == "Test Project"
    assert result["timeline_count"] == 0


def test_post_restart_check_no_project():
    resolve = FakeResolve()
    result = wd.post_restart_check(resolve)
    assert result["ok"] is False
    assert "no project" in result["error"]


def test_post_restart_check_never_returns():
    release = threading.Event()

    class StuckResolve(FakeResolve):
        def GetProjectManager(self):
            release.wait()
            return super().GetProjectManager()

    result = wd.post_restart_check(
        StuckResolve(), budget_s=0.01,
        deadline_waiter=_do_not_wait_for_deadline)
    release.set()
    assert result["ok"] is False
    assert "did not return" in result["error"]


def test_post_restart_check_reports_corrupted_timeline(tmp_path):
    """A restart that corrupts a timeline's contents is detected.

    The defect this prevents: the post-restart check only verified
    project/timeline/count, so a restart that corrupted a timeline's
    contents was not detected.
    """
    from library.tools.resolve_lock import assume_sole_writer

    project = make_project("Test Project")
    timeline = project.adopt(FakeTimeline("Reel 01", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)

    store = shadow.ShadowStore(tmp_path / "shadow.db")
    resolve = FakeResolve(project)

    with assume_sole_writer("canonical Resolve double"):
        shadow.observe(project, timeline, store)
        timeline.AddMarker(99, "Red", "corruption", "", 1)
        result = wd.post_restart_check(resolve, store=store)

    assert result["ok"] is True
    assert result["timelines_intact"] == []
    assert len(result["timelines_stale"]) == 1
    assert result["timelines_stale"][0]["timeline"] == "Reel 01"
    assert "expected_hash" in result["timelines_stale"][0]
    assert "actual_hash" in result["timelines_stale"][0]


def test_post_restart_check_intact_timeline(tmp_path):
    """An intact timeline is reported as matching the shadow store."""
    from library.tools.resolve_lock import assume_sole_writer

    project = make_project("Test Project")
    timeline = project.adopt(FakeTimeline("Reel 01", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)

    store = shadow.ShadowStore(tmp_path / "shadow.db")
    resolve = FakeResolve(project)

    with assume_sole_writer("canonical Resolve double"):
        shadow.observe(project, timeline, store)
        result = wd.post_restart_check(resolve, store=store)

    assert result["ok"] is True
    assert result["timelines_intact"] == ["Reel 01"]
    assert result["timelines_stale"] == []


def test_post_restart_check_reobserves_stale_shadow(tmp_path):
    """A stale shadow is re-observed after a mismatch."""
    from library.tools.resolve_lock import assume_sole_writer

    project = make_project("Test Project")
    timeline = project.adopt(FakeTimeline("Reel 01", project=project))
    place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)

    store = shadow.ShadowStore(tmp_path / "shadow.db")
    resolve = FakeResolve(project)

    with assume_sole_writer("canonical Resolve double"):
        shadow.observe(project, timeline, store)
        timeline.AddMarker(99, "Red", "corruption", "", 1)
        wd.post_restart_check(resolve, store=store)
        heads = store.heads("Test Project")
        assert len(heads) == 1
        assert heads[0].generation == 2


def test_broker_rejects_submissions_when_degraded(tmp_path):
    """The broker stops queue growth when the watchdog marks degraded."""
    store = JobStore(tmp_path / "test.sqlite3")
    resolve = FakeResolve()

    def connect():
        return resolve

    broker = Broker(store, connect=connect)
    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=999)
    broker._watchdog = watchdog
    watchdog.start()
    try:
        watchdog._mark_degraded("test degradation", "test-holder",
                                  "test-job")
        try:
            broker.submit("lease", {"exclusive": True})
            assert False, "should have raised"
        except job_kinds.JobRefused as exc:
            assert "Resolve is unresponsive" in str(exc)
            assert "test-holder" in str(exc)
            assert "test-job" in str(exc)
    finally:
        broker.stop()
        watchdog.stop()
        store.close()


def test_broker_accepts_submissions_when_healthy(tmp_path):
    store = JobStore(tmp_path / "test.sqlite3")
    resolve = FakeResolve()

    def connect():
        return resolve

    broker = Broker(store, connect=connect)
    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=999)
    broker._watchdog = watchdog
    watchdog.start()
    try:
        result = broker.submit("lease", {"exclusive": True})
        assert "id" in result
    finally:
        broker.stop()
        watchdog.stop()
        store.close()


def test_watchdog_clears_degraded_when_probe_recovers():
    """A successful bounded probe after a hang un-marks degraded.

    The defect this prevents: the watchdog latched degraded forever, so
    one transient hang blocked all new Resolve work until an operator
    restarted the broker.
    """
    hang = threading.Event()

    class TransientResolve(FakeResolve):
        def GetProjectManager(self):
            if not hang.is_set():
                time.sleep(999)
            return super().GetProjectManager()

    watchdog = wd.ResolveWatchdog(connect=lambda: TransientResolve(),
                                  probe_interval=0.01,
                                  connect_budget=0.05,
                                  round_trip_budget=0.05,
                                  deadline_waiter=_do_not_wait_for_deadline)
    watchdog._probe()
    assert watchdog.is_degraded()
    assert "probe failed" in watchdog.status()["degraded_reason"]

    hang.set()
    watchdog._probe()
    assert not watchdog.is_degraded()
    assert watchdog.status()["responsive"] is True


def test_broker_resumes_submissions_after_watchdog_recovers(tmp_path):
    """The broker accepts submissions again once the watchdog recovers.

    The defect this prevents: a single transient hang permanently
    blocked all new Resolve work until an operator restarted the broker.
    """
    store = JobStore(tmp_path / "test.sqlite3")
    hang = threading.Event()

    class TransientResolve(FakeResolve):
        def GetProjectManager(self):
            if not hang.is_set():
                time.sleep(999)
            return super().GetProjectManager()

    def connect():
        return TransientResolve()

    broker = Broker(store, connect=connect)
    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.01,
                                  connect_budget=0.05,
                                  round_trip_budget=0.05,
                                  deadline_waiter=_do_not_wait_for_deadline)
    broker._watchdog = watchdog
    watchdog._probe()
    assert watchdog.is_degraded()
    with pytest.raises(job_kinds.JobRefused):
        broker.submit("lease", {"exclusive": True})

    hang.set()
    watchdog._probe()
    result = broker.submit("lease", {"exclusive": True})
    assert "id" in result
    broker.stop()
    store.close()


def test_recovery_reaches_broker_within_one_probe_interval(tmp_path):
    """Recovery reaches the broker within one probe interval.

    The defect this prevents: recovery required an operator restart,
    which is not a probe interval.
    """
    store = JobStore(tmp_path / "test.sqlite3")
    hang = threading.Event()

    class TransientResolve(FakeResolve):
        def GetProjectManager(self):
            if not hang.is_set():
                time.sleep(999)
            return super().GetProjectManager()

    def connect():
        return TransientResolve()

    broker = Broker(store, connect=connect)
    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.05,
                                  connect_budget=0.05,
                                  round_trip_budget=0.05,
                                  deadline_waiter=_do_not_wait_for_deadline)
    broker._watchdog = watchdog
    watchdog._probe()
    assert watchdog.is_degraded()
    with pytest.raises(job_kinds.JobRefused):
        broker.submit("lease", {"exclusive": True})

    hang.set()
    recovered = time.monotonic()
    watchdog._probe()
    result = broker.submit("lease", {"exclusive": True})
    assert "id" in result
    assert time.monotonic() - recovered < wd.PROBE_INTERVAL_S
    broker.stop()
    store.close()


def test_status_records_degradation_and_recovery():
    """Status keeps the degradation and the recovery for post-mortem."""
    resolve = FakeResolve()

    now = [100.0]
    watchdog = wd.ResolveWatchdog(connect=lambda: resolve, clock=lambda: now[0])
    watchdog._mark_degraded("test degradation", "test-holder", "test-job")
    assert watchdog.is_degraded()
    degraded_at = watchdog.status()["degraded_at"]
    assert degraded_at == 100.0

    now[0] = 101.5
    watchdog._probe()
    status = watchdog.status()
    assert status["degraded"] is False
    assert status["degraded_at"] is None
    assert status["degraded_reason"] == ""
    assert status["recovered_at"] == 101.5
    assert status["recovered_at"] >= degraded_at
    assert status["holder"] == "test-holder"
    assert status["job_id"] == "test-job"


def test_watchdog_probe_without_lease_does_not_mark_degraded(unguarded):
    """A watchdog probe with no lease held must not mark the broker
    degraded when Resolve is healthy.

    Regression: the watchdog probed from its own thread with no lease
    held, so `scriptapp_preserving_locale` refused the handshake whenever
    the broker was idle. The first probe marked Resolve degraded and
    every later submission was refused - a permanently wedged broker.
    """
    seen = {}

    def connect():
        seen["held"] = resolve_lock.held()
        if not seen["held"]:
            raise RuntimeError(
                "refusing Resolve scriptapp handshake outside the instance "
                "lease; take resolve_lease before connecting")
        return FakeResolve()

    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.01,
                                  connect_budget=30.0)
    watchdog._probe()
    assert seen.get("held") is True
    assert not watchdog.is_degraded()
    status = watchdog.status()
    assert status["connected"] is True
    assert status["responsive"] is True


def test_watchdog_contention_does_not_mark_degraded(unguarded, tmp_path):
    """A contended lease is not a hang: the probe skips, the broker lives.

    While a build holds the instance exclusively, the watchdog's shared
    lease acquisition refuses (`ResolveBusy`). Contention is not
    unresponsiveness, so the watchdog must not mark Resolve degraded -
    a build in progress must not wedge the broker.

    The exclusive hold is taken in a CHILD process sharing this
    worker's lock dir, so the probe's acquisition really refuses: the
    holder accounting is process-wide, and a hold taken in this same
    process would nest reentrantly and succeed - never exercising the
    skip path this test pins.
    """
    repo_root = Path(__file__).resolve().parents[3]
    lock_dir = os.environ.get(resolve_lock.LOCK_DIR_ENV, "")
    assert lock_dir, "the session lock dir must be set (tests/conftest.py)"
    ready = tmp_path / "holder-ready"
    script = textwrap.dedent("""
        import os
        import sys
        import time
        from contextlib import contextmanager
        from pathlib import Path

        lock_dir, ready_path, release_path = sys.argv[1:]
        os.environ["PIPELINE_RESOLVE_LOCK_DIR"] = lock_dir
        sys.path.insert(0, __REPO__)

        from library.tools import resolve_lock

        os.environ.pop(resolve_lock.INHERIT_ENV, None)
        os.environ.pop(resolve_lock.INHERIT_MODE_ENV, None)

        @contextmanager
        def no_broker_turn(*args, **kwargs):
            yield None
        resolve_lock._broker_turn = no_broker_turn

        with resolve_lock.resolve_lease(
                "contention holder", exclusive=True,
                honor_captain=False, timeout=30):
            Path(ready_path).write_text("held", encoding="utf-8")
            while not Path(release_path).exists():
                time.sleep(0.005)
        """).replace("__REPO__", repr(str(repo_root)))
    holder = subprocess.Popen(
        [sys.executable, "-c", script, lock_dir, str(ready),
         str(tmp_path / "holder-release")],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8")
    try:
        deadline = time.time() + 15
        while not ready.exists():
            assert holder.poll() is None, holder.communicate()
            assert time.time() < deadline, \
                "the holder never took the exclusive lease"
            time.sleep(0.05)
        calls = []

        def connect():
            calls.append(1)
            return FakeResolve()

        watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.01,
                                      connect_budget=0.2)
        watchdog.start()
        try:
            time.sleep(0.05)
            assert not watchdog.is_degraded()
        finally:
            watchdog.stop()
        # Every probe refused at the lease, so none reached `connect` -
        # the contention above is proven, not assumed.
        assert calls == [], \
            f"expected every probe to refuse, {len(calls)} connected"
        assert holder.poll() is None, \
            "the holder exited mid-window, so contention is unproven"
    finally:
        (tmp_path / "holder-release").touch()
        # Reaping the child after releasing its gate proves the exclusive
        # lease overlapped each watchdog probe, without a multi-second hold.
        out, err = holder.communicate(timeout=30)
    assert holder.returncode == 0, (out, err)


@contextlib.contextmanager
def _serving_broker(connect, probe_interval=0.01, probe_scheduler=None):
    """Run the real broker (`serve`) in a thread until the block exits.

    The watchdog inside the broker is the production one: it takes its
    lease through the broker's own `acquire` gate, exactly as it does when
    the broker serves a live project. The socket path is short - AF_UNIX
    bounds it - and the directory is removed on exit.

    The serve thread is JOINED before the directory goes: the shutdown
    reply is served before the serve loop has exited, and the loop's own
    finally unlinks the socket path - a later test binding the same path
    would otherwise have its socket deleted under it (measured 2026-10-06:
    the second broker served a thread alive with `socket exists=False`).
    """
    socket_dir = Path("/tmp") / f"vep-wd-{os.getpid()}"
    socket_dir.mkdir(parents=True, exist_ok=True)
    socket_path = socket_dir / "b.sock"
    watchdog_factory = wd.ResolveWatchdog
    def make_watchdog(connect):
        kwargs = {"probe_interval": probe_interval}
        if probe_scheduler is not None:
            kwargs["probe_scheduler"] = probe_scheduler
        return watchdog_factory(connect, **kwargs)

    wd.ResolveWatchdog = make_watchdog
    thread = threading.Thread(target=serve, args=(socket_path, connect),
                              daemon=True, name="test-broker-serve")
    thread.start()
    try:
        deadline = time.time() + 15
        while client.ping(socket_path) is None:
            if time.time() >= deadline:
                raise AssertionError("test broker did not start serving")
            time.sleep(0.01)
        yield socket_path
    finally:
        client.call({"op": "shutdown"}, path=socket_path)
        thread.join(timeout=15)
        wd.ResolveWatchdog = watchdog_factory
        shutil.rmtree(socket_dir, ignore_errors=True)


def _wait_watchdog(socket_path: Path, predicate, timeout: float,
                   message: str) -> dict:
    deadline = time.time() + timeout
    while True:
        status = client.watchdog_status(socket_path)
        if predicate(status):
            return status
        if time.time() >= deadline:
            raise AssertionError(f"{message}: {status}")
        time.sleep(0.05)


def test_watchdog_recovers_when_resolve_is_relaunched():
    """A Resolve that goes away and is relaunched must not wedge the broker.

    Drives the real broker with a fake Resolve that goes away (connect
    raises) and comes back (connect returns). The defect: once the watchdog
    marks degraded, the broker refuses the watchdog's own lease submissions,
    so the recovery probe can never take its lease, never connect, and never
    clear the mark - the broker stays wedged until an operator restart.
    """
    resolve = FakeResolve()
    gone = threading.Event()

    def connect():
        if gone.is_set():
            raise ResolveUnavailable("Resolve is not running")
        return resolve

    with _serving_broker(connect) as socket_path:
        gone.set()
        status = _wait_watchdog(
            socket_path, lambda s: s.get("degraded"), 45, "never degraded")
        assert status["degraded"] is True

        gone.clear()
        status = _wait_watchdog(
            socket_path, lambda s: not s.get("degraded"), 45,
            f"watchdog stuck degraded after relaunch: {status}")
        assert status["responsive"] is True


def test_watchdog_probe_connects_after_exclusive_lease_released(monkeypatch):
    """The probe right after an exclusive lease releases must still connect.

    Live evidence 2026-10-06: the production broker latched DEGRADED with
    "connect failed: refusing Resolve scriptapp handshake outside the
    instance lease" on the probe 31s after a live run's exclusive lease
    released, Resolve alive throughout. The watchdog takes its own shared
    lease, so `held()` must be True on the connect - if the probe's lease
    acquisition is skipped or refused, the connect runs outside any lease
    and the handshake guard refuses it, latching the broker degraded with
    Resolve healthy.
    """
    resolve = FakeResolve()
    held_seen = []

    def connect():
        held_seen.append(resolve_lock.held())
        return scriptapp_preserving_locale(_FakeDvr(resolve))

    # No hosted shadow watcher: its own shared lease could race the live
    # run's exclusive acquisition, and the connects that must be under the
    # watchdog's lease are the watchdog's alone.
    monkeypatch.setenv("REN_SHADOW_WATCHER", "0")

    with _serving_broker(connect) as socket_path:
        status = _wait_watchdog(
            socket_path,
            lambda s: s.get("connected") and s.get("responsive"),
            45, "watchdog never became healthy")
        assert status["degraded"] is False

        with resolve_lock.resolve_lease("live run", exclusive=True,
                                       honor_captain=False, timeout=10.0):
            pass

        status = _wait_watchdog(
            socket_path,
            lambda s: s.get("connected") and s.get("responsive"),
            45, "watchdog did not reconnect after exclusive lease released")
        assert status["degraded"] is False, (
            f"watchdog latched degraded with Resolve alive: {status}")
        assert held_seen and all(held_seen), (
            f"connect ran outside the instance lease: held={held_seen}")


def test_watchdog_probe_survives_concurrent_exclusive_release(
        monkeypatch, unguarded):
    """A probe that overlaps a live run's exclusive lease release must connect.

    Live evidence 2026-10-06: the production broker latched DEGRADED with
    "connect failed: refusing Resolve scriptapp handshake outside the
    instance lease" on a probe that overlapped a live run's exclusive lease
    release, Resolve alive throughout. The probe takes a reentrant shared
    lease while the live run holds the exclusive one; the release path
    zeroed `_depth`, so the probe's connect ran with `held()` False and the
    handshake guard refused it.
    """
    resolve = FakeResolve()
    connect_started = threading.Event()
    release_connect = threading.Event()
    permit_probe = threading.Event()
    held_seen = []
    first_probe = [True]

    def probe_scheduler(stop, interval):
        if first_probe[0]:
            first_probe[0] = False
            permit_probe.wait()
        else:
            stop.wait(interval)
        return stop.is_set()

    def connect():
        connect_started.set()
        assert release_connect.wait(15.0)
        held_seen.append(resolve_lock.held())
        return scriptapp_preserving_locale(_FakeDvr(resolve))

    # The test process's lease and the broker's watchdog share this process;
    # keep the broker's grant path out of the way so a lease is a direct
    # flock, as it is when no broker serves.
    monkeypatch.setattr(client, "ensure", lambda *a, **k: None)
    monkeypatch.setattr(client, "serving", lambda *a, **k: False)
    # No hosted shadow watcher: its own connect would race the live run's
    # exclusive lease and swallow the refusal, and the gated connect must
    # be the watchdog's probe, not the watcher's.
    monkeypatch.setenv("REN_SHADOW_WATCHER", "0")

    with _serving_broker(connect, probe_scheduler=probe_scheduler) as socket_path:
        try:
            with resolve_lock.resolve_lease("live run", exclusive=True,
                                           honor_captain=False, timeout=15.0):
                permit_probe.set()
                assert connect_started.wait(1.0), "watchdog never probed"
            release_connect.set()
        finally:
            permit_probe.set()
            release_connect.set()
        status = _wait_watchdog(
            socket_path,
            lambda s: s.get("connected") and s.get("responsive"),
            45, "watchdog did not recover after exclusive release")
        assert status["degraded"] is False, (
            f"watchdog latched degraded with Resolve alive: {status}")
        assert held_seen and all(held_seen), (
            f"probe connect ran outside the instance lease: held={held_seen}")
