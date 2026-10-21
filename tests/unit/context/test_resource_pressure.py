"""PROD-006: what this machine does when it cannot host the work.

Measured 2026-10-22 on the 24 GiB Apple Silicon box, with Resolve open
and other lanes running: 14.0 of 15.4 GB of swap in use, 6.96 GB
available, and every resident-model stage waiting on memory the machine
did not have. Each test below names one defect that measurement found.

The basis is `docs/benchmarks/2026-10-22-prod006-resource-constrained.md`.
"""
import json
import os
import sqlite3
import time
from contextlib import closing

import pytest

from library.tools import resource_scheduler

#: Bound at import, before conftest's per-test swap fixture runs: the
#: test that asserts the reading comes from the running OS calls this.
_REAL_SWAP_STATE = resource_scheduler._swap_state_gb


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """A private scheduler on this machine's shape: 24 GB, a 2.4 GB floor."""
    monkeypatch.setattr(resource_scheduler, "capacity", lambda: {
        **{"native_" + part: 1
           for part in resource_scheduler.NATIVE_PARTS}})
    monkeypatch.setattr(resource_scheduler, "_memory_allows_admission",
                        lambda: True)
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (12.0, 24.0))
    monkeypatch.setattr(resource_scheduler, "POLL_SECONDS", 0.02)
    return resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")


def _waiting_job(scheduler, owner, demand, age_seconds=0.0):
    """Insert a WAITING job straight into the private store."""
    scheduler._transaction(lambda conn: None)
    now = time.time()
    token = f"waiting-{owner}"
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute(
            "INSERT INTO jobs (token, owner, pid, host, machine_id, state,"
            " demand, enqueued_at, pid_start, heartbeat_at, policy_version)"
            " VALUES (?, ?, ?, ?, ?, 'waiting', ?, ?, ?, ?, ?)",
            (token, owner, os.getpid(), "test-host",
             resource_scheduler._machine_id(), json.dumps(demand),
             now - age_seconds, resource_scheduler._pid_start(os.getpid()),
             now, resource_scheduler.SCHEDULER_POLICY_VERSION))
    return token


# ── A machine that is already paging refuses; it does not wait ───────

@pytest.mark.real_scheduler_admission
def test_a_paging_machine_refuses_a_resident_model_instead_of_waiting(
        scheduler, monkeypatch):
    """A wait for memory the machine can only deliver by paging is a
    swap death with extra steps, so the job is refused - with the
    reading that says so."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (9.0, 24.0))
    monkeypatch.setattr(resource_scheduler, "_swap_state_gb",
                        lambda: (12.0, 16.0))
    with pytest.raises(resource_scheduler.MemoryUnavailable) as failure:
        scheduler.acquire("gemma", {"ram_gb": 15},
                          announce=lambda line: None, timeout=0.2)
    message = str(failure.value)
    assert "paging" in message
    assert "12.0 GB of 16.0 GB" in message
    assert "9.0 GB of 24 GB" in message


@pytest.mark.real_scheduler_admission
def test_a_calm_machine_admits_the_same_resident_model(scheduler, monkeypatch):
    """The refusal is the SWAP that decides it, not the demand: a calm
    machine admits the same 15 GB it refused a moment ago."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (20.0, 24.0))
    monkeypatch.setattr(resource_scheduler, "_swap_state_gb",
                        lambda: (1.0, 16.0))
    token = scheduler.acquire("gemma", {"ram_gb": 15},
                              announce=lambda line: None)
    try:
        assert scheduler.running_demand(token) == {"ram_gb": 15}
    finally:
        scheduler.release(token)


@pytest.mark.real_scheduler_admission
def test_a_job_that_fits_is_admitted_on_a_paging_machine(
        scheduler, monkeypatch):
    """Swap refuses work that cannot be resident, not work that can: a
    4 GB job that fits the free memory is admitted even while the
    machine pages, because refusing it would be a regression."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (9.0, 24.0))
    monkeypatch.setattr(resource_scheduler, "_swap_state_gb",
                        lambda: (15.0, 16.0))
    token = scheduler.acquire("small model", {"ram_gb": 4},
                              announce=lambda line: None)
    try:
        assert scheduler.running_demand(token) == {"ram_gb": 4}
    finally:
        scheduler.release(token)


# ── One model stage must not starve another ─────────────────────────

@pytest.mark.real_scheduler_admission
def test_an_older_waiter_the_machine_cannot_run_does_not_starve_a_smaller_one(
        scheduler, monkeypatch):
    """A 15 GB waiter that does not fit the live headroom must not hold
    the queue against a 4 GB job that does fit: the older one is not an
    ELIGIBLE waiter, and eligibility is what the backfill rule asks."""
    monkeypatch.setattr(resource_scheduler, "_memory_available_gb",
                        lambda: (8.0, 24.0))
    monkeypatch.setattr(resource_scheduler, "_swap_state_gb",
                        lambda: (1.0, 16.0))
    _waiting_job(scheduler, "gemma waiter", {"ram_gb": 15})
    token = scheduler.acquire("sam waiter", {"ram_gb": 4},
                              announce=lambda line: None, timeout=1.0)
    try:
        assert scheduler.running_demand(token) == {"ram_gb": 4}
    finally:
        scheduler.release(token)


# ── The reservations are the measured peaks, not the averages ───────

def test_the_sam_reservation_covers_its_measured_peak():
    """Measured 2026-10-22 on this machine: SAM 2 peaked at 10.15 GB
    physical footprint over 8 sampled grants (mean 6.55 GB). The
    reservation is the peak: the mean leaves the tail unpaged for, and
    the tail is what swaps the machine to death."""
    assert resource_scheduler.demand_for(
        "objects.segment:model_memory") == {"ram_gb": 11}
    assert resource_scheduler.MEASURED_MEMORY_RESERVATIONS_GB[
        "semantics.analyse:model_memory"] == 15


# ── What a grant really used, beside the machine it ran on ─────────

def test_a_grant_records_the_machine_peak_swap_and_pressure(monkeypatch):
    """The swap the machine reached while a stage ran was never
    measured, and the punchlist asks for it: it is recorded beside the
    stage's own peak."""
    from library.tools import perf_resources

    monkeypatch.setattr(resource_scheduler, "_swap_state_gb",
                        _REAL_SWAP_STATE)
    monkeypatch.setattr(perf_resources, "swap_usage_mb",
                        lambda: {"used_mb": 12288.0, "total_mb": 15360.0})
    monkeypatch.setattr(perf_resources, "memory_pressure",
                        lambda: {"pct": 71.0, "level": "warn"})
    monkeypatch.setattr(resource_scheduler.TreeSampler, "SAMPLE_SECONDS",
                        0.01)
    sampler = resource_scheduler.TreeSampler()
    deadline = time.time() + 30.0
    while sampler.samples < 1 and time.time() < deadline:
        time.sleep(0.02)
    measured = sampler.stop()
    assert sampler.samples >= 1
    assert measured["peak_swap_gb"] == pytest.approx(12.0, abs=0.01)
    assert measured["peak_memory_pressure_pct"] == pytest.approx(71.0)


def test_a_resolve_exclusive_profile_samples_resolve_beside_itself():
    """A reel build runs beside the captain's own Resolve use, so the
    grant must record what Resolve was doing while it ran."""
    assert resource_scheduler.watches_resolve("reel.build:apply")
    assert not resource_scheduler.watches_resolve(
        "semantics.analyse:inference")
