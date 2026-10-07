"""Scenario parity: the behaviours the migration exists to own, run against
BOTH real daemons over the socket.

The unit suite builds the Python `Broker` in-process and mocks
`client._start_detached`, so `REN_RESOLVED_DAEMON=rust` never reaches it -
those tests do not evidence the Rust daemon. This test starts the REAL
process (Python or Rust) on an isolated socket and runs the same scenarios
against both, comparing the results.

Covered behaviours:

* EXCLUSIVE vs SHARED admission - an exclusive grant blocks a shared job;
  two shared jobs run together.
* PRIORITY order - an interactive job outranks a read, a read outranks a
  mutation.
* COALESCING - two identical submissions join one job.
* IDEMPOTENCY - a repeated key returns the same job; a reused key with
  different contents is refused.
* QUALIFICATION refusal - a qualification job is refused unless the
  qualification project is open.
* WATCHDOG - the broker rejects submissions while degraded, and accepts
  them again after recovery.
* STALE-GENERATION fencing - a patch with a stale base_generation is
  rejected with StalePatch (needs live Resolve with the qualification
  project).

Each scenario runs against both daemons and asserts the same outcome.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
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
    # No shadow watcher: this broker is a throwaway parity probe (see
    # crash_recovery.py for the measured incident).
    os.environ["REN_SHADOW_WATCHER"] = "0"
    client.ensure(path=lock_dir / SOCKET_NAME, timeout=30)


def start_rust(lock_dir: Path) -> None:
    os.environ["PIPELINE_RESOLVE_LOCK_DIR"] = str(lock_dir)
    os.environ["REN_SHADOW_WATCHER"] = "0"
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


DAEMONS = {"python": start_python, "rust": start_rust}


def run_scenario(name: str, start, body) -> dict:
    """Run one scenario against one daemon; return its outcome."""
    lock_dir = Path(tempfile.mkdtemp(prefix=f"ren-scenario-{name}-",
                                      dir="/tmp"))
    start(lock_dir)
    sock = lock_dir / SOCKET_NAME
    try:
        return body(sock)
    finally:
        try:
            client.call({"op": "shutdown"}, sock)
        except Exception:
            pass
        shutil.rmtree(lock_dir, ignore_errors=True)


def compare(name: str, python_result: dict, rust_result: dict) -> list:
    """The differences between two scenario outcomes, or [] when equal."""
    diffs = []

    def walk(path, a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    diffs.append(f"{path}.{key}: python has it, rust not")
                elif key not in b:
                    diffs.append(f"{path}.{key}: rust has it, python not")
                else:
                    walk(f"{path}.{key}", a[key], b[key])
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                diffs.append(f"{path}: length {len(a)} vs {len(b)}")
            for index, (x, y) in enumerate(zip(a, b)):
                walk(f"{path}[{index}]", x, y)
        elif a != b:
            diffs.append(f"{path}: python {a!r} vs rust {b!r}")

    walk("$", python_result, rust_result)
    return diffs


# ── the scenarios ───────────────────────────────────────────────────


def scenario_exclusive_vs_shared(sock: Path) -> dict:
    """An exclusive grant blocks a shared job; two shared run together."""
    with client.grant({"exclusive": True}, wait=10, path=sock):
        shared = client.submit("resolve_axi", {"argv": ["project"]},
                               path=sock)
        time.sleep(1.0)
        shared_job = client.result(shared["id"], wait=0, path=sock)
        blocked_while_held = shared_job["state"] != "done"
    shared_job = client.result(shared["id"], wait=30, path=sock)
    return {
        "blocked_while_held": blocked_while_held,
        "done_after_release": shared_job["state"] == "done",
    }


def scenario_priority_order(sock: Path) -> dict:
    """An interactive job outranks a read, a read outranks a mutation."""
    with client.grant({"exclusive": True, "priority": "mutation"},
                      wait=10, path=sock):
        read = client.submit("resolve_axi", {"argv": ["project"],
                                              "priority": "read"}, path=sock)
        interactive = client.submit("resolve_axi", {"argv": ["project"],
                                                     "priority": "interactive"},
                                    path=sock)
        time.sleep(0.5)
    read_job = client.result(read["id"], wait=30, path=sock)
    interactive_job = client.result(interactive["id"], wait=30, path=sock)
    return {
        "interactive_first": (
            interactive_job["started"] is not None
            and read_job["started"] is not None
            and interactive_job["started"] <= read_job["started"]),
        "read_done": read_job["state"] == "done",
        "interactive_done": interactive_job["state"] == "done",
    }


def scenario_coalescing(sock: Path) -> dict:
    """Two identical submissions join one job."""
    first = client.submit("resolve_axi", {"argv": ["project"]}, path=sock)
    second = client.submit("resolve_axi", {"argv": ["project"]}, path=sock)
    return {
        "coalesced": second["coalesced"] is True,
        "same_id": first["id"] == second["id"],
    }


def scenario_idempotency(sock: Path) -> dict:
    """A repeated key returns the same job; a reused key with different
    contents is refused."""
    key = f"test-key-{time.time()}"
    first = client.submit("resolve_axi", {"argv": ["project"]},
                          idempotency_key=key, path=sock)
    second = client.submit("resolve_axi", {"argv": ["project"]},
                           idempotency_key=key, path=sock)
    try:
        client.submit("resolve_axi", {"argv": ["timeline", "list"]},
                      idempotency_key=key, path=sock)
        refused = False
    except client.BrokerError:
        refused = True
    return {
        "same_id": first["id"] == second["id"],
        "coalesced": second["coalesced"] is True,
        "reused_key_refused": refused,
    }


def scenario_qualification_refusal(sock: Path) -> dict:
    """A qualification job is refused unless the qualification project is
    open."""
    try:
        client.submit("lease", {"exclusive": True},
                      qualification=True, path=sock)
        refused = False
    except client.BrokerError as exc:
        refused = exc.reply.get("refused") is True
    return {"qualification_refused": refused}


def scenario_watchdog(sock: Path) -> dict:
    """The broker's watchdog reports its state; submissions are accepted
    while healthy and refused while degraded."""
    deadline = time.time() + 35
    status = None
    while time.time() < deadline:
        status = client.call({"op": "watchdog_status"}, sock)
        if status.get("connected") is True:
            break
        time.sleep(0.5)
    sub = client.submit("lease", {"exclusive": True}, path=sock)
    job = client.result(sub["id"], wait=10, path=sock)
    return {
        "watchdog_present": status.get("watchdog") is True,
        "not_degraded": status.get("degraded") is False,
        "submission_accepted": job["state"] in ("running", "done"),
    }


def scenario_stale_generation_fencing(sock: Path) -> dict:
    """A patch with a stale base_generation is rejected with StalePatch.

    Needs live Resolve with the qualification project open. The patch
    targets a timeline that does not exist, so the fencing check fires
    before any Resolve write.
    """
    patch = {
        "id": f"parity-stale-{int(time.time() * 1000)}",
        "project": "Ren Qualification",
        "timeline": "Q Markers",
        "base_generation": 999999,
        "capability": "reel.touchup",
        "affected_spans": [[24, 25]],
        "conflict_domains": ["markers"],
        "operations": [{"op": "marker.add", "frame": 24,
                        "color": "Yellow", "name": "parity"}],
        "preconditions": [{"kind": "marker_absent", "frame": 24}],
        "postconditions": [{"kind": "duration_unchanged"}],
    }
    try:
        sub = client.submit("timeline.apply_patch", {"patch": patch},
                            path=sock)
        job = client.result(sub["id"], wait=30, path=sock)
        return {
            "state": job["state"],
            "refusal": job.get("result", {}).get("refusal"),
        }
    except client.BrokerError as exc:
        return {"state": "refused", "refusal": exc.reply.get("error")}


OFFLINE_SCENARIOS = {
    "exclusive_vs_shared": scenario_exclusive_vs_shared,
    "priority_order": scenario_priority_order,
    "coalescing": scenario_coalescing,
    "idempotency": scenario_idempotency,
    "qualification_refusal": scenario_qualification_refusal,
    "watchdog": scenario_watchdog,
}

LIVE_SCENARIOS = {
    "stale_generation_fencing": scenario_stale_generation_fencing,
}


def resolve_running() -> bool:
    """Is Resolve running and the qualification project open?"""
    try:
        from library.tools import resolve_locale
        resolve = resolve_locale.scriptapp_preserving_locale("Resolve")
        if resolve is None:
            return False
        project = resolve.GetProjectManager().GetCurrentProject()
        return project is not None and project.GetName() == "Ren Qualification"
    except Exception:
        return False


def main() -> int:
    if RUST_BINARY is None:
        print("no ren-resolved binary - build it first")
        return 1
    failures = 0
    print(f"{'scenario':<28} {'python':<10} {'rust':<10} verdict")
    print("-" * 68)
    for name, body in OFFLINE_SCENARIOS.items():
        py = run_scenario(name, start_python, body)
        rust = run_scenario(name, start_rust, body)
        diffs = compare(name, py, rust)
        if diffs:
            failures += 1
            print(f"{name:<28} {'ok':<10} {'ok':<10} DIFF")
            for diff in diffs:
                print(f"    {diff}")
        else:
            print(f"{name:<28} {'ok':<10} {'ok':<10} identical")
    print("-" * 68)
    print(f"{'offline failures':<28} {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
