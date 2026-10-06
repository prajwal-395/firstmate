"""Protocol parity: the same request bytes to the Python daemon and the
Rust daemon, and a table of the responses.

Every request type the broker's socket protocol defines is sent to both
daemons on their own sockets, and the replies are compared as parsed JSON
(with a tolerance for the float fields - timestamps and measured seconds).
A difference is either a mismatch (a bug) or an explained one (a field the
Rust daemon reports differently, with the reason).

Run with the Rust daemon built (native/target/release/ren-resolved or
debug). The Python daemon is the default; the Rust daemon is started by
hand on its own socket.
"""

import json
import os
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


def stop(lock_dir: Path) -> None:
    try:
        client.call({"op": "shutdown"}, lock_dir / SOCKET_NAME)
    except Exception:
        pass
    deadline = time.time() + 10
    while client.ping(lock_dir / SOCKET_NAME) is not None and time.time() < deadline:
        time.sleep(0.05)


def normalize(value):
    """Normalize a parsed reply for comparison: sort object keys, round
    floats to a tolerance (timestamps and measured seconds differ)."""
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, float):
        return round(value, 1)
    return value


def without_watcher(reply):
    """Drop the shadow watcher's side-effect jobs from a list reply.

    The Python broker hosts the watcher (PR 1807): it submits
    timeline.snapshot jobs on a schedule (owner 'shadow_watcher') and
    takes a lease each tick to read the open project (owner 'pid-<pid>').
    The Rust broker does not host the watcher - yet. The list comparison
    is about the protocol both daemons speak, so the watcher's own jobs
    are filtered from both sides; this test's submissions carry owner ''.
    """
    if not isinstance(reply, dict):
        return reply
    jobs = reply.get("jobs")
    if not isinstance(jobs, list):
        return reply
    kept = [job for job in jobs
            if not (isinstance(job, dict) and job.get("owner"))]
    return {**reply, "jobs": kept}


def compare(python_reply, rust_reply) -> list:
    """The differences between two normalized replies, or [] when equal."""
    diffs = []

    def walk(path, a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    diffs.append(f"{path}.{key}: Python has it, Rust does not")
                elif key not in b:
                    diffs.append(f"{path}.{key}: Rust has it, Python does not")
                else:
                    walk(f"{path}.{key}", a[key], b[key])
        elif isinstance(a, list) and isinstance(b, list):
            if len(a) != len(b):
                diffs.append(f"{path}: list length {len(a)} vs {len(b)}")
            for index, (x, y) in enumerate(zip(a, b)):
                walk(f"{path}[{index}]", x, y)
        elif a != b:
            diffs.append(f"{path}: Python {a!r} vs Rust {b!r}")

    walk("$", python_reply, rust_reply)
    return diffs


def request(op: dict, path: Path) -> dict:
    """One request; a refusal is an error reply, returned as-is."""
    try:
        return client.call(op, path)
    except client.BrokerError as exc:
        return exc.reply


def main() -> int:
    if RUST_BINARY is None:
        print("no ren-resolved binary - build it first")
        return 1
    py_dir = Path("/tmp/ren-parity-py")
    rust_dir = Path("/tmp/ren-parity-rust")
    for directory in (py_dir, rust_dir):
        directory.mkdir(parents=True, exist_ok=True)
        for stale in directory.iterdir():
            stale.unlink()

    start_python(py_dir)
    start_rust(rust_dir)
    py, rust = py_dir / SOCKET_NAME, rust_dir / SOCKET_NAME

    # Every request type, with the same bytes to both daemons.
    cases = {
        "ping": {"op": "ping"},
        "submit lease": {"op": "submit", "kind": "lease",
                         "params": {"exclusive": True}},
        "submit unknown kind": {"op": "submit", "kind": "bogus",
                                "params": {}},
        "submit snapshot missing name": {"op": "submit",
                                         "kind": "timeline.snapshot",
                                         "params": {}},
        "list": {"op": "list", "limit": 5},
        "watchdog_status": {"op": "watchdog_status"},
        "status unknown id": {"op": "status", "id": "nonexistent"},
        "result unknown id": {"op": "result", "id": "nonexistent"},
        "cancel unknown id": {"op": "cancel", "id": "nonexistent"},
        "release with no grant": {"op": "release"},
        "unknown op": {"op": "frobnicate"},
    }

    # A lease job both daemons schedule, for status/result/list.
    py_lease = request({"op": "submit", "kind": "lease",
                        "params": {"exclusive": True}}, py)
    rust_lease = request({"op": "submit", "kind": "lease",
                          "params": {"exclusive": True}}, rust)
    cases["status"] = None  # filled below
    cases["result"] = None

    print(f"{'request':<28} {'python':<10} {'rust':<10} verdict")
    print("-" * 70)
    failures = 0
    for name, op in cases.items():
        if name == "status":
            op = {"op": "status", "id": py_lease["id"]}
            py_reply = request(op, py)
            op = {"op": "status", "id": rust_lease["id"]}
            rust_reply = request(op, rust)
        elif name == "result":
            op = {"op": "result", "id": py_lease["id"], "wait": 0}
            py_reply = request(op, py)
            op = {"op": "result", "id": rust_lease["id"], "wait": 0}
            rust_reply = request(op, rust)
        else:
            py_reply = request(op, py)
            rust_reply = request(op, rust)
            if op.get("op") == "list":
                py_reply = without_watcher(py_reply)
                rust_reply = without_watcher(rust_reply)
        diffs = compare(normalize(py_reply), normalize(rust_reply))
        # The pid, job id and measured seconds differ by nature.
        diffs = [d for d in diffs if not any(
            field in d for field in ("$.pid", ".id", ".submitted",
                                     ".started", ".finished",
                                     ".wait_seconds", ".hold_seconds"))]
        if diffs:
            failures += 1
            print(f"{name:<28} {'ok':<10} {'ok':<10} DIFF")
            for diff in diffs:
                print(f"    {diff}")
        else:
            print(f"{name:<28} {'ok':<10} {'ok':<10} identical")

    # The list reply after the two lease submissions.
    py_list = without_watcher(request({"op": "list"}, py))
    rust_list = without_watcher(request({"op": "list"}, rust))
    diffs = compare(normalize(py_list), normalize(rust_list))
    diffs = [d for d in diffs if not any(
        field in d for field in (".id", ".submitted", ".started",
                                 ".finished", ".wait_seconds",
                                 ".hold_seconds"))]
    print(f"{'list (after leases)':<28} {'ok':<10} {'ok':<10} "
          f"{'identical' if not diffs else 'DIFF'}")
    for diff in diffs:
        print(f"    {diff}")
    if diffs:
        failures += 1

    stop(py_dir)
    stop(rust_dir)
    print("-" * 70)
    print(f"{'FAILURES':<28} {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
