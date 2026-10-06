"""The two lease regressions, tested against the Rust daemon.

2026-10-05 two broker regressions reached live rebuilds:

1. A watchdog probe with NO lease marked the broker degraded. The
   scripting handshake refuses outside the instance lease, so an idle
   broker's first probe failed and every later submission was refused -
   a permanently wedged broker. The fix: the probe takes a shared
   instance lease (it only reads), with `honor_captain=False`.

2. A subprocess holding NO instance lease. The batch Fusion comp pass
   called `scriptapp_preserving_locale` directly, the subprocess held no
   lease, and the handshake refused. The fix: the subprocess adopts the
   parent's lease from the inherited environment.

The Rust daemon preserves both: the watchdog probe and the job worker
run in Python processes that take the instance lease through the existing
`resolve_lock`. These tests prove it - the probe connects (so it held the
lease) and the broker does not mark degraded, and a job's worker connects
and completes (so it held the lease; without one the handshake refuses).
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.tools import resolve_lock  # noqa: E402
from library.tools.resolved import client  # noqa: E402
from library.tools.resolved.server import SOCKET_NAME  # noqa: E402

RUST_BINARY = None
for profile in ("release", "debug"):
    candidate = REPO / "native" / "target" / profile / "ren-resolved"
    if candidate.is_file():
        RUST_BINARY = candidate
        break

if RUST_BINARY is None:
    pytest.skip(
        "the ren-resolved binary is not built - cargo build --release "
        "in native/ (the debug profile works too)",
        allow_module_level=True,
    )


def _short_lock_dir(name: str) -> Path:
    """A lock dir under /tmp and SHORT: a Unix socket path is bounded
    (104 bytes on macOS), and a pytest tmp_path is deep enough to overflow
    it - the daemon would die on `AF_UNIX path long` before binding.
    """
    return Path(tempfile.mkdtemp(prefix=f"ren-lease-{name}-", dir="/tmp"))


def start_rust(lock_dir: Path) -> None:
    os.environ["PIPELINE_RESOLVE_LOCK_DIR"] = str(lock_dir)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO), env.get("PYTHONPATH", "")) if p)
    log = open(lock_dir / "ren-resolved.log", "ab", buffering=0)
    subprocess.Popen(
        [str(RUST_BINARY), "serve", str(lock_dir / SOCKET_NAME)],
        env=env, stdin=subprocess.DEVNULL, stdout=log,
        stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.time() + 30
    while time.time() < deadline:
        if client.ping(lock_dir / SOCKET_NAME) is not None:
            return
        time.sleep(0.1)
    raise RuntimeError("the Rust daemon did not start")


def wait_for_status(sock: Path, predicate, timeout: float = 70.0) -> dict:
    """Poll `watchdog_status` until `predicate` accepts it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.call({"op": "watchdog_status"}, sock)
        if predicate(status):
            return status
        time.sleep(0.2)
    return client.call({"op": "watchdog_status"}, sock)


def test_watchdog_probe_holds_a_shared_lease():
    """The watchdog's probe takes a shared instance lease, so an idle
    broker's probe does not fail the handshake and mark Resolve degraded.

    Regression: the watchdog probed from its own thread with no lease
    held, so `scriptapp_preserving_locale` refused the handshake whenever
    the broker was idle. The first probe marked Resolve degraded and
    every later submission was refused - a permanently wedged broker.

    The probe connects (connected=True) - which it could only do holding
    the instance lease - and the broker does not mark degraded.
    """
    lock_dir = _short_lock_dir("watchdog")
    start_rust(lock_dir)
    sock = lock_dir / SOCKET_NAME
    try:
        status = wait_for_status(
            sock, lambda s: s["connected"] is True, timeout=70)
        assert status["connected"] is True, (
            f"the probe never connected - it did not hold the lease: {status}")
        assert status["degraded"] is False, (
            f"the probe marked the broker degraded: {status['degraded_reason']}")
        assert status["responsive"] is True
    finally:
        try:
            client.call({"op": "shutdown"}, sock)
        except Exception:
            pass
        shutil.rmtree(lock_dir, ignore_errors=True)


def test_job_worker_holds_the_instance_lease():
    """A job's worker holds the instance lease while it connects to
    Resolve, so the scripting handshake does not refuse.

    Regression: a subprocess holding no instance lease called the
    handshake directly and was refused - 'refusing Resolve scriptapp
    handshake outside the instance lease'. The worker takes the lease
    through `resolve_lease`, exactly as the Python broker does.

    The job does not fail with the handshake refusal - which it would,
    immediately, if the worker did not take the lease. The job waits for
    the lease (a live build may hold it) or completes; either way the
    worker held it.
    """
    lock_dir = _short_lock_dir("job")
    start_rust(lock_dir)
    sock = lock_dir / SOCKET_NAME
    try:
        # A read-only resolve_axi job: the worker connects to Resolve
        # under the instance lease.
        submitted = client.submit(
            "resolve_axi", {"argv": ["project"]}, path=sock)
        # Wait a little: if the worker did not take the lease, the
        # handshake refuses at once and the job fails with the refusal.
        job = client.result(submitted["id"], wait=10, path=sock)
        assert job["state"] != "rejected", (
            f"the job was rejected - the worker did not hold the lease: "
            f"{job['error']}")
        assert "outside the instance lease" not in job["error"], (
            f"the handshake refused - the worker did not hold the lease: "
            f"{job['error']}")
    finally:
        try:
            client.call({"op": "shutdown"}, sock)
        except Exception:
            pass
        shutil.rmtree(lock_dir, ignore_errors=True)
