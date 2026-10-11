"""Daemon restarts must not leave gates running, nor testing entries stuck.

Defects 2026-10-10: a launchd restart killed the daemon but left its
Tier-2 full_suite_gate process group running old code under
heavy_work_lock for an hour beside the new daemon's batch, while queue
entries stayed testing; and swap used stayed high after pressure
cleared while dispatch still gated on it (paging-rate half is pinned in
test_local_ci_load.py).
"""

import os
import signal
import subprocess
import time

from library.tools.local_ci import daemon, gate_cleanup, runner, state


def _spawn_sleep():
    """A process group this test owns: exact pid, killed by exact pgid."""
    process = subprocess.Popen(["sleep", "30"], start_new_session=True)
    assert process.pid > 1
    return process


def test_shutdown_terminates_exactly_the_groups_it_started():
    process = _spawn_sleep()
    try:
        runner._register_active_pgid(process.pid)
        assert process.pid in runner.active_run_pgids()
        runner.terminate_active_runs(grace_seconds=2.0)
        # The handler signals the exact group; the owning _run_logged
        # wait reaps the direct child (a zombie keeps its pgid until
        # waited on, so reap here as that owner would).
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        assert process.poll() is not None
        assert not runner._process_group_exists(process.pid)
    finally:
        runner._unregister_active_pgid(process.pid)
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    assert process.pid not in runner.active_run_pgids()


def test_run_logged_unregisters_its_group_on_success(tmp_path):
    log = str(tmp_path / "run.log")
    result = runner._run_logged(["true"], str(tmp_path), log, timeout=30)
    assert result["group_clean"] is True
    assert runner.active_run_pgids() == []


def test_run_logged_unregisters_its_group_on_timeout(tmp_path):
    log = str(tmp_path / "run.log")
    result = runner._run_logged(["sleep", "30"], str(tmp_path), log,
                                timeout=1)
    assert result["timed_out"] is True
    assert runner.active_run_pgids() == []


def test_find_orphaned_gates_needs_reparented_gate_token():
    rows = [
        (100, 1, "bash scripts/full_suite_gate.sh --exclude-real-model"),
        (101, 50, "bash scripts/full_suite_gate.sh --exclude-real-model"),
        (102, 1, "python3 -m pytest tests/unit -n 2"),
        (1, 0, "/sbin/launchd"),
    ]
    assert gate_cleanup.find_orphaned_gate_pgids(rows) == [100]


def test_reap_kills_only_orphaned_gate_pgids_by_exact_pgid():
    sent = []

    def fake_killpg(pgid, sig):
        sent.append((pgid, sig))

    rows = [
        (200, 1, "bash scripts/full_suite_gate.sh"),
        (201, 1, "bash scripts/full_suite_gate.sh"),
        (202, 40, "bash scripts/full_suite_gate.sh"),
    ]
    alive = {200: True, 201: True}

    def fake_exists(pgid):
        return alive.get(pgid, False)

    import library.tools.local_ci.gate_cleanup as cleanup
    original = cleanup._process_group_exists
    cleanup._process_group_exists = fake_exists
    try:
        results = cleanup.reap_orphaned_gates(
            "/tmp/state", rows=rows, checkout_cwd=None,
            kill=fake_killpg, grace_seconds=0.01)
    finally:
        cleanup._process_group_exists = original
    # Exact pgids only: 202 (live daemon's child) is never signalled.
    signalled = {pgid for pgid, _sig in sent}
    assert signalled <= {200, 201}
    assert 202 not in signalled
    assert set(results) == {200, 201}


def test_reap_scopes_to_own_checkouts_when_cwds_known(tmp_path):
    from library.tools.local_ci import hygiene

    own = os.path.join(hygiene.checkouts_root(str(tmp_path)), "run-1")
    rows = [
        (300, 1, "bash scripts/full_suite_gate.sh"),
        (301, 1, "bash scripts/full_suite_gate.sh"),
    ]
    cwds = {300: own, 301: "/tmp/somewhere-else"}
    scoped = gate_cleanup.find_orphaned_gate_pgids_for_state(
        str(tmp_path), rows, cwds)
    assert scoped == [300]


def test_testing_entries_recover_only_when_no_gate_survives(
        monkeypatch, tmp_path):
    state.write_queue([{"pr": 41, "head": "head-41",
                        "status": state.ENTRY_TESTING,
                        "reason": "", "enqueued_at": 1.0}], str(tmp_path))
    monkeypatch.setattr(gate_cleanup, "gate_still_active", lambda _s: True)
    assert gate_cleanup.recover_testing_entries_if_gate_gone(
        str(tmp_path)) == 0
    entry, = state.read_queue(str(tmp_path))
    assert entry["status"] == state.ENTRY_TESTING

    monkeypatch.setattr(gate_cleanup, "gate_still_active", lambda _s: False)
    assert gate_cleanup.recover_testing_entries_if_gate_gone(
        str(tmp_path)) == 1
    entry, = state.read_queue(str(tmp_path))
    assert entry["status"] == state.ENTRY_QUEUED
    assert "interrupted" in entry["reason"]


def test_startup_reaps_before_recovering(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        gate_cleanup, "reap_orphaned_gates",
        lambda state_dir: calls.append("reap") or {900: True})
    monkeypatch.setattr(
        gate_cleanup, "recover_testing_entries_if_gate_gone",
        lambda state_dir: calls.append("recover") or 2)
    summary = daemon.recover_orphaned_gates_on_startup(str(tmp_path))
    assert calls == ["reap", "recover"]
    assert summary == {"reaped": {900: True}, "recovered": 2}


def test_collect_discards_killed_runs_while_shutting_down(
        monkeypatch, tmp_path):
    from library.tools.local_ci import queue

    world = queue.World(state_dir=str(tmp_path))
    scheduler = daemon.Tier1Scheduler(world)
    try:
        pr = {"number": 7, "head": "head-7",
              "url": "https://example/pr/7", "labels": []}
        future = scheduler._pool.submit(
            lambda: {"slot": 0, "outcome": {
                "pr": 7, "head": "head-7", "url": pr["url"], "ok": False,
                "passed": 0, "failed": 1, "elapsed": 1.0,
                "selection": [], "command": "pytest"}})
        scheduler._futures[future] = (pr, 1.0)
        future.result(timeout=5)
        old = daemon._shutdown_requested
        daemon._shutdown_requested = True
        try:
            done = {"checked": [], "queued": [], "refused": [],
                    "queue": {}}
            scheduler.collect(done)
        finally:
            daemon._shutdown_requested = old
        # A killed run judged nothing: no check recorded, no failure
        # verdict, just a refusal naming the restart.
        assert done["checked"] == []
        assert any("shutting down" in line for line in done["refused"])
        assert state.read_checks(str(tmp_path)) == {}
    finally:
        scheduler.shutdown(wait=True)


def test_stale_swap_used_does_not_serialise_a_quiet_machine():
    # 2026-10-10: ~12 GB swap used beside ~6 GB free long after pressure
    # cleared. Swap used is observability only; dispatch rests on free
    # memory plus the recent paging rate.
    from library.tools.local_ci import load

    snap = {"load1": 1.0, "cpus": 10, "mem_free_gb": 6.0,
            "swap_used_gb": 12.0, "pageout_per_sec": 0.0,
            "swapout_per_sec": 0.0, "cpu_busy": 1.0,
            "disk_free_gb": 100.0, "heavy": [], "blind": False}
    plan = load.targets(snap)
    assert plan["tier1_jobs"] == load.TIER1_MAX


def test_foreign_reparented_gate_does_not_block_dispatch(
        monkeypatch, tmp_path):
    # A reparented gate whose cwd is outside this state dir's checkouts
    # (a worker worktree, the nightly run) is not ours: the tick must
    # use the same cwd scope as the reap, so dispatch proceeds instead
    # of refusing everything with "awaiting reap" until it exits.
    from library.tools.local_ci import queue

    monkeypatch.setattr(
        gate_cleanup, "_default_process_table",
        lambda: [(900, 1, "bash scripts/full_suite_gate.sh "
                          "--exclude-real-model -n 2")])
    monkeypatch.setattr(gate_cleanup, "_cwd_for_pids",
                        lambda _pids: {900: "/tmp/somewhere-else"})
    assert gate_cleanup.find_own_orphaned_gate_pgids(str(tmp_path)) == []

    from library.tools.local_ci import github
    pr = {"number": 1, "head": "head-1", "base": "main", "labels": [],
          "draft": False, "url": "https://example/pr/1", "title": "PR 1"}
    monkeypatch.setattr(github, "list_open_prs", lambda: [pr])
    monkeypatch.setattr(github, "post_status", lambda *a, **k: None)
    monkeypatch.setattr(github, "remove_label", lambda *a, **k: None)
    monkeypatch.setattr(github, "main_sha", lambda: "base")
    monkeypatch.setattr(daemon.hygiene, "running_jobs", lambda _s: [])
    monkeypatch.setattr(daemon.hygiene, "gate_running", lambda: False)
    monkeypatch.setattr(
        daemon, "run_tier1",
        lambda _world, _pr, _workers: {"pr": 1, "head": "head-1",
                                       "url": pr["url"], "ok": True,
                                       "passed": 1, "failed": 0,
                                       "elapsed": 1.0, "selection": [],
                                       "command": "pytest"})
    world = queue.World(state_dir=str(tmp_path))
    scheduler = daemon.Tier1Scheduler(world)
    try:
        done = scheduler.poll_once({"tier1_jobs": 2, "pytest_n": 1,
                                    "window": 90, "headroom": 9.0,
                                    "heavy": []})
    finally:
        scheduler.shutdown(wait=True)
    assert done["started"] == [1]
    assert not any("awaiting reap" in line for line in done["refused"])
