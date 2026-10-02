"""The single-Resolve KPIs: what a wait for Resolve cost, said once.

Each test pins a way the measurement could lie: a wait inside a layer
counted in both, a refused acquisition's seconds vanishing, a report
that fails a live broker's open jobs by reading them, a grant's
measured use dropped on release. No Resolve: the lease is a real flock
in a private lock dir, contended by a real second process.
"""

import json
import sqlite3
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from library.tools import perf_ledger, resolve_lock, resource_scheduler
from library.tools.resolved import kpi
from library.tools.resolved.store import JobStore

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


def test_a_wait_recorded_inside_a_span_is_charged_once(tmp_path):
    project = str(tmp_path)
    with perf_ledger.capability(project, "render", "r1"):
        with perf_ledger.span("resolve_render"):
            time.sleep(0.1)
            began = time.time()
            time.sleep(0.3)
            perf_ledger.record(perf_ledger.RESOLVE_WAIT, time.time() - began,
                               started_at=began)
    report = perf_ledger.profile(perf_ledger.read_rows(project), "r1")
    shares = {line["name"]: line["wall_s"] for line in report["lines"]}
    assert shares[perf_ledger.RESOLVE_WAIT] >= 0.3
    assert shares["resolve_render"] < 0.3
    assert report["overlap_s"] < 0.02


class _Timeline:
    def __init__(self, uid):
        self.uid = uid

    def GetName(self):
        return self.uid

    def GetUniqueId(self):
        return self.uid


class _Project:
    def __init__(self, current):
        self.current = current

    def GetCurrentTimeline(self):
        return self.current

    def SetCurrentTimeline(self, timeline):
        self.current = timeline
        return True


def test_a_run_says_what_it_lost_waiting_for_resolve(tmp_path, monkeypatch):
    monkeypatch.setattr(resolve_lock, "_sole_writer_reason", None)
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lock_dir))
    holder = subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent("""
            import sys, time
            from library.tools.resolve_lock import resolve_lease
            with resolve_lease("other agent", timeout=5):
                print("held", flush=True)
                time.sleep(1.0)
        """)], cwd=REPO_ROOT, stdout=subprocess.PIPE, encoding="utf-8")
    try:
        assert holder.stdout.readline().strip() == "held"
        project = str(tmp_path / "project")
        with perf_ledger.capability(project, "place", "r1"):
            with pytest.raises(resolve_lock.ResolveBusy):
                with resolve_lock.resolve_lease("too impatient", timeout=0.2):
                    pass
            with resolve_lock.resolve_lease("placement", timeout=5):
                cursor = _Project(_Timeline("Reel03"))
                for name in ("Reel09", "Reel09", "Reel03"):
                    resolve_lock.assert_current_timeline(cursor,
                                                         _Timeline(name))
                time.sleep(0.1)
    finally:
        holder.wait(timeout=10)

    report = perf_ledger.profile(perf_ledger.read_rows(project), "r1")
    resolve = report["resolve"]
    assert resolve["leases"] == 1 and resolve["refused"] == 1
    # The refused 0.2s and the granted wait are both lost to Resolve.
    assert resolve["blocked_s"] >= 0.6
    assert resolve["lost_ratio"] == pytest.approx(
        resolve["blocked_s"] / report["total_wall_s"], abs=1e-3)
    assert resolve["useful_s"] == pytest.approx(
        report["total_wall_s"] - resolve["blocked_s"], abs=1e-3)
    assert resolve["exclusive_held_s"] >= 0.1
    # Reel09 -> Reel03; the unfenced first write has nothing to compare.
    assert resolve["timeline_switches"] == 1
    assert "lost to Resolve" in perf_ledger.render(report)


def test_the_report_reads_a_live_brokers_receipts_without_closing_them(
        tmp_path):
    db = tmp_path / "ren-resolved.sqlite3"
    store = JobStore(db)
    now = time.time()

    def job(job_id, state, **fields):
        store.insert({"id": job_id, "kind": "timeline.snapshot",
                      "priority": "read", "mode": "shared", "executed": 1,
                      "params": {}, "project": "P", "timeline": "Reel03",
                      "qualification": 0, "coalesce_key": None,
                      "owner": "", "state": state, "submitted": now - 10,
                      **fields})

    job("snap", "done", started=now - 9, finished=now - 8, subscribers=3)
    job("patch", "done", kind="timeline.apply_patch", mode="exclusive",
        timeline="Reel09", started=now - 7, finished=now - 5,
        params={"patch": {"operations": [{"op": "a"}, {"op": "b"}]}})
    job("live", "running", started=now - 1)

    report = kpi.kpis(kpi.receipts_since(db, now - 60), now - 60, now)
    assert report["coalesced"] == 2
    assert (report["patches"], report["operations_batched"]) == (1, 2)
    assert report["cursor_changes"] == 2      # Reel03, Reel09, Reel03
    assert report["exclusive_hold_s"] == pytest.approx(2.0, abs=0.01)
    assert store.get("live")["state"] == "running"


def test_a_released_grant_keeps_what_it_measurably_used(tmp_path):
    scheduler = resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")
    token = scheduler.acquire("gate", {"cpu": 1}, announce=lambda _: None)
    scheduler.release(token, "full_suite_gate",
                      {"cpu_s": 12.0, "peak_cores": 3.5,
                       "peak_rss_gb": 4.25, "samples": 6})
    [grant] = scheduler.history()
    assert grant["profile"] == "full_suite_gate"
    assert json.loads(grant["demand"]) == {"cpu": 1}
    assert (grant["peak_cores"], grant["peak_rss_gb"]) == (3.5, 4.25)


def test_a_measurement_that_cannot_be_kept_never_fails_the_release(
        tmp_path):
    """A gate's release raised on a grants table of another shape."""
    scheduler = resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")
    token = scheduler.acquire("gate", {"cpu": 1}, announce=lambda _: None)
    with sqlite3.connect(scheduler.db_path) as db:
        db.execute("DROP TABLE grants")
        db.execute("CREATE TABLE grants (only_one TEXT NOT NULL)")
    scheduler.release(token, "full_suite_gate", {"cpu_s": 1.0})
    assert scheduler.jobs() == []
    assert not scheduler.legacy_dir.exists()
