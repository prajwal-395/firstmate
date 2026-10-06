"""Crash/restart recovery: kill the daemon mid-job and the job store
recovers as the Python one does.

The store's recovery on open is the contract: a job left `queued` or
`running` by a daemon that died is closed as `failed` with the error
"the broker that held this job exited" - its client lost its connection
with that daemon, so nobody is waiting on it any more. This test kills
the daemon mid-job (SIGKILL, no cleanup) and shows the next daemon opens
the same table and closes the orphaned job, for BOTH daemons - the Python
one and the Rust one.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from library.tools.resolved import client  # noqa: E402
from library.tools.resolved.server import SOCKET_NAME  # noqa: E402

RUST_BINARY = None
for profile in ("release", "debug"):
    candidate = REPO / "native" / "target" / profile / "ren-resolved"
    if candidate.is_file():
        RUST_BINARY = candidate
        break


def start_python(lock_dir: Path) -> None:
    os.environ["PIPELINE_RESOLVE_LOCK_DIR"] = str(lock_dir)
    os.environ.pop("REN_RESOLVED_DAEMON", None)
    client.ensure(path=lock_dir / SOCKET_NAME, timeout=30)


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


def pid_on(lock_dir: Path) -> int:
    return client.ping(lock_dir / SOCKET_NAME)["pid"]


def kill(pid: int) -> None:
    os.kill(pid, signal.SIGKILL)
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


def recovery_state(lock_dir: Path) -> dict:
    """The orphaned job's state after the next daemon opens the table."""
    db = lock_dir / "ren-resolved.sqlite3"
    import sqlite3
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, state, error FROM jobs ORDER BY submitted").fetchall()
    conn.close()
    return {row["id"]: {"state": row["state"], "error": row["error"]}
            for row in rows}


def scenario(name: str, start) -> list:
    """Kill the daemon mid-job and read the recovered state, for one daemon."""
    lock_dir = Path(f"/tmp/ren-crash-{name}")
    if lock_dir.exists():
        for stale in lock_dir.iterdir():
            stale.unlink()
    lock_dir.mkdir(parents=True, exist_ok=True)
    start(lock_dir)
    sock = lock_dir / SOCKET_NAME
    # A lease job: it becomes a grant (running) the moment it is scheduled.
    submitted = client.submit("lease", {"exclusive": True}, path=sock)
    job = client.result(submitted["id"], wait=5, path=sock)
    assert job["state"] == "running", f"the grant did not start: {job['state']}"
    # Kill the daemon with no cleanup - the grant is orphaned.
    kill(pid_on(lock_dir))
    # The next daemon opens the same table and recovers.
    start(lock_dir)
    state = recovery_state(lock_dir)
    client.call({"op": "shutdown"}, sock)
    return [f"{name}: job {job['id'][:8]}... state={state[job['id']]['state']} "
            f"error={state[job['id']]['error']!r}"]


def main() -> int:
    if RUST_BINARY is None:
        print("no ren-resolved binary - build it first")
        return 1
    results = []
    results += scenario("python", start_python)
    results += scenario("rust", start_rust)
    print("crash/restart recovery:")
    for line in results:
        print(f"  {line}")
    ok = all("state=failed" in line
             and "the broker that held this job exited" in line
             for line in results)
    print(f"  both daemons recover identically: {ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
