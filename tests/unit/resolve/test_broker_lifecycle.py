"""ONE BROKER SERVES THE MACHINE, and a check stops the broker it started.

THE DEFECT (measured 2026-10-21): the PROD-001 clean-install check
(`scripts/ren_clean_install_check.py`) started a SECOND broker - from
`/tmp/prod001-clean`, on `/tmp/ren-resolved.sock` - and left it serving
for seven hours, with two workers. Two brokers against the one Resolve
move the cursor under each other, and they blocked Resolve project
switches for every other lane.

The two halves of the repair, each named here:

1. a broker on the socket a plain client reaches CLAIMS the machine (a
   machine-wide lock), and a second one refuses to serve while the
   first holds it;
2. a broker on a socket of its own is left alone - the qualification
   harness's broker and a lane's own are both allowed beside the
   machine's;
3. a check that starts a broker stops it on exit, by its own pid - and
   never by a pattern, and never the machine's broker;
4. the check's broker is PRIVATE (its own socket inside the sandbox)
   and shares the machine's Resolve lease, so a check never takes the
   machine's broker away and never puts a second writer on the one
   Resolve.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.resolved import broker_paths


def _the_check():
    """The clean-install check script, imported as a module."""
    path = REPO / "scripts" / "ren_clean_install_check.py"
    spec = importlib.util.spec_from_file_location("ren_clean_install_check",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ── one broker serves the machine ────────────────────────────────────


def test_a_second_broker_refuses_to_serve_while_the_machine_broker_is_up(
        monkeypatch, tmp_path):
    """THE DEFECT: two brokers served the one machine.

    The second broker drives the same Resolve through a second lease,
    and the two move the cursor under each other.
    """
    monkeypatch.setenv(broker_paths.MACHINE_BROKER_LOCK_ENV,
                       str(tmp_path / "machine-broker.lock"))
    plain = Path(os.environ.get("TMPDIR", "/tmp")) / broker_paths.SOCKET_NAME

    first = broker_paths.claim_the_machine_broker(plain)
    assert first is not None, "the machine's broker did not claim the machine"

    with pytest.raises(broker_paths.MachineBrokerHeld) as caught:
        broker_paths.claim_the_machine_broker(plain)
    assert "one broker serves one machine" in str(caught.value)
    assert caught.value.holder == os.getpid()


def test_a_private_broker_is_left_alone(monkeypatch, tmp_path):
    """The qualification harness's broker may run beside the machine's.

    A broker on a socket of its own is not the machine's broker: the
    machine's broker is not its business, and refusing it would break
    the qualification harness and every lane's own broker.
    """
    monkeypatch.setenv(broker_paths.MACHINE_BROKER_LOCK_ENV,
                       str(tmp_path / "machine-broker.lock"))
    plain = Path(os.environ.get("TMPDIR", "/tmp")) / broker_paths.SOCKET_NAME
    assert broker_paths.claim_the_machine_broker(plain) is not None

    private = tmp_path / "private" / broker_paths.SOCKET_NAME
    assert broker_paths.claim_the_machine_broker(private) is None


def test_the_machine_lock_is_the_same_file_for_every_process(monkeypatch,
                                                             tmp_path):
    """The lock is machine-wide, and it does not move with a temp dir.

    A lock inside the temp dir a process may be pointed at is no lock
    at all: a sandboxed install, a check and the machine's own broker
    would each hold their own, and none of them would see the others.
    """
    monkeypatch.delenv(broker_paths.MACHINE_BROKER_LOCK_ENV, raising=False)
    path = broker_paths.machine_broker_lock_path()
    assert path.parent == Path("/tmp")
    assert path.name.startswith("ren-resolved-broker-")
    assert str(os.getuid()) in path.name


# ── the check stops the broker it started ────────────────────────────


class _FakeBroker:
    """A broker on the sandbox's own socket, in its OWN PROCESS.

    A process, because the check stops a broker BY ITS PID: the pid has
    to be a pid that can die, and the test's own pid is not one to kill.
    """

    SCRIPT = """
import json, os, socket, sys
path = sys.argv[1]
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
server.listen(4)
while True:
    conn, _ = server.accept()
    with conn:
        stream = conn.makefile("r", encoding="utf-8")
        line = stream.readline()
        if not line:
            continue
        request = json.loads(line)
        if request.get("op") == "ping":
            reply = {"ok": True, "pid": os.getpid(), "serving": True}
        elif request.get("op") == "shutdown":
            reply = {"ok": True}
            conn.sendall((json.dumps(reply) + "\\n").encode("utf-8"))
            sys.exit(0)
        else:
            reply = {"error": "unknown op"}
        conn.sendall((json.dumps(reply) + "\\n").encode("utf-8"))
"""

    def __init__(self, directory: Path):
        import subprocess
        import tempfile

        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.socket_path = directory / broker_paths.SOCKET_NAME
        script = directory / "fake-broker.py"
        script.write_text(self.SCRIPT, encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, str(script), str(self.socket_path)])
        deadline = time.time() + 10.0
        while time.time() < deadline:
            if self.socket_path.exists():
                return
            time.sleep(0.05)
        raise RuntimeError("the fake broker did not start")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - the test is ending anyway
                self.process.kill()


def test_the_check_stops_the_broker_it_started(tmp_path):
    """THE DEFECT: the check left its broker serving for seven hours.

    A check that starts a broker must stop it on exit, and it must stop
    the broker it started - by its own pid, on its own socket.
    """
    import tempfile

    check = _the_check()
    # A Unix socket path is bounded (104 bytes on macOS), so the
    # sandbox's broker lives in a SHORT directory under /tmp.
    short = Path(tempfile.mkdtemp(prefix="ren-clean-", dir="/tmp"))
    broker = _FakeBroker(check.broker_dir(short))
    try:
        answer = check.stop_the_broker(short)
        assert "stopped the sandbox's broker" in answer
        assert str(broker.process.pid) in answer
        assert broker.process.poll() is not None, "the broker is still running"
    finally:
        broker.stop()


def test_the_check_stops_nothing_when_it_started_no_broker(tmp_path):
    """A check that started no broker stops no broker."""
    check = _the_check()
    assert "no broker" in check.stop_the_broker(tmp_path)


def test_the_checks_broker_is_private_and_shares_the_machines_lease(
        tmp_path, monkeypatch):
    """The sandbox's broker never takes the machine's broker away.

    Its socket is its own (inside the sandbox), and the Resolve lease it
    drives is the machine's - one Resolve, one lease, so the sandbox's
    broker queues behind the machine's work instead of driving the one
    Resolve beside it.
    """
    check = _the_check()
    env = check.sandbox_env(tmp_path)
    assert env["PIPELINE_RESOLVE_BROKER_DIR"] == str(
        check.broker_dir(tmp_path))
    assert env["PIPELINE_RESOLVE_INSTANCE_DIR"] == str(
        check.machine_instance_dir())
    assert Path(env["PIPELINE_RESOLVE_BROKER_DIR"]).is_relative_to(tmp_path)
