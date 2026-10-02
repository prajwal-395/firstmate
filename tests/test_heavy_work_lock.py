"""Heavy-work admission: lock ownership, cleanup and the resource scheduler."""

import json
import os
import select
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from pathlib import Path

import pytest

from library.tools import heavy_work_lock, resource_scheduler


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_MODULE = "library.tools.heavy_work_lock"


def _environment(home: Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("VEP_HEAVY_WORK_OWNER", None)
    env.pop(heavy_work_lock.LOCK_DIR_ENV, None)
    return env


def _lock_path(home: Path) -> Path:
    return home / ".local" / "share" / "vep" / "heavy-work.lock"


def test_reentrant_child_does_not_deadlock_on_its_parent_lock(tmp_path):
    """A gate or render child inherits the owner's token and re-enters."""
    home = tmp_path / "home"
    home.mkdir()
    child = (
        "from library.tools.heavy_work_lock import heavy_work_lock; "
        "exec(\"with heavy_work_lock('nested child'):\\n    pass\")"
    )
    result = subprocess.run(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "outer",
         "--", sys.executable, "-c", child],
        cwd=REPO_ROOT, env=_environment(home), timeout=5, check=False)

    assert result.returncode == 0
    assert not _lock_path(home).exists()


def test_gate_runner_releases_lock_after_child_error_exit(tmp_path):
    """A failed full-suite child must not strand the machine lock."""
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "test gate",
         "--", sys.executable, "-c", "raise SystemExit(7)"],
        cwd=REPO_ROOT, env=_environment(home), timeout=5, check=False)

    assert result.returncode == 7
    assert not _lock_path(home).exists()


def test_gate_runner_releases_lock_after_term_signal(tmp_path):
    """A terminated full-suite child must release the lock before exiting."""
    home = tmp_path / "home"
    home.mkdir()
    lock_path = _lock_path(home)
    process = subprocess.Popen(
        [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "test gate",
         "--", sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=REPO_ROOT, env=_environment(home),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 3
    while not lock_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert lock_path.exists()
    process.send_signal(signal.SIGTERM)
    assert process.wait(timeout=5) == 128 + signal.SIGTERM
    assert not lock_path.exists()


def test_waiter_names_live_holder_once_and_waits_for_release(
        tmp_path, monkeypatch):
    """A live lock is reported once and stays intact until its owner exits."""
    home = tmp_path / "home"
    home.mkdir()
    lock_path = _lock_path(home)
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR", lock_path)
    process = None

    with heavy_work_lock.heavy_work_lock("live test holder"):
        process = subprocess.Popen(
            [sys.executable, "-m", LOCK_MODULE, "run", "--owner", "waiter",
             "--", sys.executable, "-c", "pass"],
            cwd=REPO_ROOT, env=_environment(home),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready, _, _ = select.select([process.stdout], [], [], 3)
        assert ready
        line = process.stdout.readline()
        assert "live test holder" in line
        assert select.select([process.stdout], [], [], 0.1)[0] == []
        assert process.poll() is None
        assert lock_path.exists()

    assert process is not None
    assert process.wait(timeout=5) == 0
    assert not lock_path.exists()


# ── The resource scheduler behind the lock ───────────────────────────


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """A private scheduler on a fixed 10-core, 16 GB-usable machine."""
    monkeypatch.setattr(resource_scheduler, "capacity", lambda: {
        "resolve_cursor": 1, "resolve_render": 1, "cpu": 10, "gpu": 1,
        "ram_gb": 16, "disk": 4})
    monkeypatch.setattr(resource_scheduler, "POLL_SECONDS", 0.02)
    return resource_scheduler.Scheduler(tmp_path / "heavy-work.lock")


def _acquire_in_thread(scheduler, owner, profile):
    """Start an acquisition; return (admitted event, token box)."""
    admitted, box = threading.Event(), []

    def run():
        box.append(scheduler.acquire(
            owner, resource_scheduler.demand_for(profile),
            announce=lambda line: None))
        admitted.set()
    threading.Thread(target=run, daemon=True).start()
    return admitted, box


def test_placement_runs_beside_a_gate_and_a_second_gate_waits(scheduler):
    """Catches: the global mutex queueing a seconds-long Resolve placement
    behind a minutes-long gate, and two full-suite gates running at once."""
    gate = scheduler.acquire(
        "gate", resource_scheduler.demand_for("full_suite_gate"))
    placement = scheduler.acquire(
        "placement", resource_scheduler.demand_for("resolve_placement"))
    second_gate, box = _acquire_in_thread(scheduler, "gate 2",
                                          "full_suite_gate")

    assert not second_gate.wait(0.3)
    scheduler.release(placement)
    assert not second_gate.wait(0.3)
    scheduler.release(gate)
    assert second_gate.wait(5)
    scheduler.release(box[0])
    assert scheduler.jobs() == []
    assert not scheduler.legacy_dir.exists()


def test_old_code_lock_dir_and_scheduler_exclude_each_other(scheduler):
    """Catches: a lane on pre-scheduler code (the bare mkdir lock) running
    its gate beside a scheduler gate during the switch-over."""
    legacy = scheduler.legacy_dir
    legacy.mkdir(parents=True)
    (legacy / "owner").write_text(json.dumps(
        {"owner": "scripts/full_suite_gate.sh", "pid": 1}), encoding="utf-8")
    admitted, box = _acquire_in_thread(scheduler, "placement",
                                       "resolve_placement")
    assert not admitted.wait(0.3)

    (legacy / "owner").unlink()
    legacy.rmdir()
    assert admitted.wait(5)
    with pytest.raises(FileExistsError):
        legacy.mkdir()  # what old code does to take the lock
    scheduler.release(box[0])
    assert not legacy.exists()


def test_a_queued_large_job_is_not_starved_by_later_small_ones(scheduler):
    """Catches: placements arriving back to back keeping a second model
    run queued forever while each one fits beside the running one."""
    vlm = scheduler.acquire("gemma", resource_scheduler.demand_for(
        "local_vlm"))
    second, _ = _acquire_in_thread(scheduler, "gemma 2", "local_vlm")
    time.sleep(0.1)  # the second model run queues first, on the gpu
    placement, box = _acquire_in_thread(scheduler, "placement",
                                        "resolve_placement")

    assert not placement.wait(0.3)
    scheduler.release(vlm)
    assert second.wait(5)


def test_a_dead_holder_is_reaped(scheduler):
    """Catches: a crashed job's row wedging every later admission."""
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    token = scheduler.acquire("crashed", resource_scheduler.demand_for(
        "machine"))
    with closing(sqlite3.connect(scheduler.db_path)) as conn, conn:
        conn.execute("UPDATE jobs SET pid = ? WHERE token = ?",
                     (dead.pid, token))

    scheduler.release(scheduler.acquire(
        "next", resource_scheduler.demand_for("machine")))
    assert scheduler.jobs() == []


def test_a_nested_section_cannot_grow_its_grant(tmp_path, monkeypatch):
    """Catches: a placement-sized grant silently admitting a nested render,
    which needs the gpu it never reserved."""
    monkeypatch.setattr(heavy_work_lock, "HEAVY_LOCK_DIR",
                        tmp_path / "heavy-work.lock")
    with heavy_work_lock.heavy_work_lock("outer", "resolve_placement"):
        with pytest.raises(heavy_work_lock.GrantTooSmall):
            heavy_work_lock.take_heavy_lock("render", "resolve_render")
