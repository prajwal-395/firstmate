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
