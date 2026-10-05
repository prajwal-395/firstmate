"""The Resolve-degraded watchdog, health status, and post-restart check."""

import threading
import time

from library.tools import resolve_watchdog as wd
from library.tools.resolved import jobs as job_kinds
from library.tools.resolved.server import Broker
from library.tools.resolved.store import JobStore
from tests.resolve_double import FakeResolve, make_project


def test_watchdog_marks_degraded_when_call_never_returns():
    """A call that never returns marks Resolve unresponsive."""
    release = threading.Event()

    def never_connect():
        release.wait()
        return FakeResolve()

    watchdog = wd.ResolveWatchdog(connect=never_connect, probe_interval=0.01,
                                  connect_budget=0.05)
    watchdog.start()
    try:
        deadline = time.time() + 5
        while not watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        assert watchdog.is_degraded()
        status = watchdog.status()
        assert status["connected"] is False
        assert status["responsive"] is False
        assert "connect failed" in status["degraded_reason"]
    finally:
        release.set()
        watchdog.stop()


def test_watchdog_marks_degraded_when_probe_never_returns():
    """A probe that never returns marks Resolve unresponsive."""
    release = threading.Event()

    class StuckResolve(FakeResolve):
        def GetProjectManager(self):
            release.wait()
            return super().GetProjectManager()

    def connect():
        return StuckResolve()

    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.01,
                                  connect_budget=0.05,
                                  round_trip_budget=0.05)
    watchdog.start()
    try:
        deadline = time.time() + 5
        while not watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        assert watchdog.is_degraded()
        status = watchdog.status()
        assert status["connected"] is True
        assert status["responsive"] is False
        assert "probe failed" in status["degraded_reason"]
    finally:
        release.set()
        watchdog.stop()


def test_watchdog_stays_healthy_when_resolve_answers():
    """A responsive Resolve keeps the watchdog healthy."""
    resolve = FakeResolve()

    def connect():
        return resolve

    watchdog = wd.ResolveWatchdog(connect=connect, probe_interval=0.01)
    watchdog.start()
    try:
        time.sleep(0.2)
        assert not watchdog.is_degraded()
        status = watchdog.status()
        assert status["connected"] is True
        assert status["responsive"] is True
    finally:
        watchdog.stop()


def test_health_status_connected_and_responsive():
    resolve = FakeResolve()
    status = wd.health_status(resolve)
    assert status == {"connected": True, "responsive": True}


def test_health_status_not_connected():
    status = wd.health_status(None)
    assert status == {"connected": False, "responsive": False}


def test_health_status_connected_but_not_responsive():
    class StuckResolve(FakeResolve):
        def GetProjectManager(self):
            time.sleep(999)
            return super().GetProjectManager()

    status = wd.health_status(StuckResolve())
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
    class StuckResolve(FakeResolve):
        def GetProjectManager(self):
            time.sleep(999)
            return super().GetProjectManager()

    result = wd.post_restart_check(StuckResolve(), budget_s=0.01)
    assert result["ok"] is False
    assert "did not return" in result["error"]


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
                                  round_trip_budget=0.05)
    watchdog.start()
    try:
        deadline = time.time() + 5
        while not watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        assert watchdog.is_degraded()
        assert "probe failed" in watchdog.status()["degraded_reason"]

        hang.set()
        deadline = time.time() + 5
        while watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        assert not watchdog.is_degraded()
        assert watchdog.status()["responsive"] is True
    finally:
        hang.set()
        watchdog.stop()


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
                                  round_trip_budget=0.05)
    broker._watchdog = watchdog
    watchdog.start()
    try:
        deadline = time.time() + 5
        while not watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        assert watchdog.is_degraded()
        try:
            broker.submit("lease", {"exclusive": True})
            assert False, "should have raised"
        except job_kinds.JobRefused:
            pass

        hang.set()
        deadline = time.time() + 5
        while watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        result = broker.submit("lease", {"exclusive": True})
        assert "id" in result
    finally:
        hang.set()
        watchdog.stop()
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
                                  round_trip_budget=0.05)
    broker._watchdog = watchdog
    watchdog.start()
    try:
        deadline = time.time() + 5
        while not watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        assert watchdog.is_degraded()

        hang.set()
        recovered = time.time()
        deadline = recovered + wd.PROBE_INTERVAL_S
        while time.time() < deadline:
            try:
                result = broker.submit("lease", {"exclusive": True})
                assert "id" in result
                break
            except job_kinds.JobRefused:
                time.sleep(0.01)
        else:
            assert False, "broker did not resume within one probe interval"
        assert time.time() - recovered < wd.PROBE_INTERVAL_S
    finally:
        hang.set()
        watchdog.stop()
        store.close()


def test_status_records_degradation_and_recovery():
    """Status keeps the degradation and the recovery for post-mortem."""
    resolve = FakeResolve()

    watchdog = wd.ResolveWatchdog(connect=lambda: resolve,
                                  probe_interval=0.01)
    watchdog.start()
    try:
        watchdog._mark_degraded("test degradation", "test-holder",
                                  "test-job")
        assert watchdog.is_degraded()
        degraded_at = watchdog.status()["degraded_at"]
        assert degraded_at is not None

        deadline = time.time() + 5
        while watchdog.is_degraded() and time.time() < deadline:
            time.sleep(0.01)
        status = watchdog.status()
        assert status["degraded"] is False
        assert status["degraded_at"] is None
        assert status["degraded_reason"] == ""
        assert status["recovered_at"] is not None
        assert status["recovered_at"] >= degraded_at
        assert status["holder"] == "test-holder"
        assert status["job_id"] == "test-job"
    finally:
        watchdog.stop()
