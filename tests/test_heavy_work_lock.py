"""Regression coverage for shared heavy-work lock ownership and cleanup."""

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from library.tools import heavy_work_lock


REPO_ROOT = Path(__file__).resolve().parents[1]
LOCK_MODULE = "library.tools.heavy_work_lock"


def _environment(home: Path) -> dict:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env.pop("VEP_HEAVY_WORK_OWNER", None)
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
