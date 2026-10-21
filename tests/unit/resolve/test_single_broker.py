"""ONE broker per machine: two checkouts, one broker.

The defect, measured 2026-10-20: the broker's socket lived in
`PIPELINE_RESOLVE_LOCK_DIR`, which a lane may point anywhere. A
qualification run, a benchmark and a worker worktree each got a
PRIVATE broker - and each of those brokers drove the SAME one Resolve.
Three brokers were serving at once (four writers), and `ren resolved
status` from the primary checkout reported "no broker is serving" while
a broker from that checkout's build directory was running: the
project-switch and timeline-count races the live lanes kept hitting.

The broker's identity is now the MACHINE's - one socket, one queue, one
lease - whatever checkout, worktree or lane asks for it. These tests
name that defect: two checkouts, one broker.

No test here starts a broker: a broker is a live writer against the
captain's one Resolve, and these tests are about WHICH broker a client
reaches, not about what a broker does. The Rust daemon's own refusal
to take a serving socket is proven in `native/ren-resolved` (the bind
tests); the Python daemon's is in `test_ensure.py`.
"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from library.tools import resolve_lock
from library.tools.ren_refusal import RenRefusal
from library.tools.resolved import __main__ as resolved_cli
from library.tools.resolved import broker_paths, client
from library.tools.resolved.server import SOCKET_NAME, serve

#: What a serving broker answers.
SERVING = {"ok": True, "pid": 4242, "serving": True, "responsive": True,
           "busy": False, "lease": None, "stale_lease": None}


def _short_dir(prefix: str) -> Path:
    """A short directory under /tmp.

    A Unix socket path is bounded (104 bytes on macOS), and a pytest
    tmp_path is deep enough to overflow it.
    """
    return Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """This test's machine: one instance directory, the machine's broker.

    The instance directory is where the machine's broker keeps its
    socket and its queue - beside the machine-wide lease it serves. The
    suite redirects it, exactly as `PIPELINE_RESOLVE_INSTANCE_DIR` does
    in production, so the broker here is this test's and never the
    captain's.
    """
    directory = _short_dir("ren-machine-")
    monkeypatch.setenv(resolve_lock.INSTANCE_LOCK_DIR_ENV, str(directory))
    # A broker that started would tell its own children where its
    # directory is; the tests here start none.
    monkeypatch.delenv(broker_paths.BROKER_DIR_TOLD_ENV, raising=False)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def serving_broker(monkeypatch, machine):
    """A broker that is serving, without starting one.

    Only the machine's socket answers. A client that looked anywhere
    else finds nobody - which is what the defect did: each lane asked
    its own socket, found it empty, and started its own broker.
    """
    pings = []

    def fake_ping(path=None):
        target = Path(path) if path is not None else client._path()
        pings.append(target)
        if target == machine / SOCKET_NAME:
            return dict(SERVING)
        return None

    monkeypatch.setattr(client, "ping", fake_ping)
    started = []

    class _Never:
        def poll(self):
            return None

    def fake_start(path):
        started.append(Path(path))
        return _Never()

    monkeypatch.setattr(client, "_start_detached", fake_start)
    yield {"pings": pings, "started": started}


# ── The identity ──────────────────────────────────────────────────────

def test_two_lanes_with_private_lock_dirs_name_one_socket(monkeypatch,
                                                           tmp_path):
    """The defect's shape: two lanes, each with its own lock dir.

    Before the fix each lane's lock dir held its own socket, so each
    lane started its own broker. The socket is the machine's now.
    """
    machine_dir = tmp_path / "machine"
    monkeypatch.setenv(resolve_lock.INSTANCE_LOCK_DIR_ENV, str(machine_dir))
    lane_a = tmp_path / "lane-a"
    lane_b = tmp_path / "lane-b"

    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lane_a))
    first = client._path()
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lane_b))
    second = client._path()

    assert first == second == machine_dir / SOCKET_NAME


def test_two_checkouts_name_one_socket(monkeypatch, tmp_path):
    """Two checkouts, one broker: the checkout is not the identity.

    Two real checkout shapes - each with the package the engine root is
    found by - and two private lock dirs beside them. Neither changes
    which broker this client talks to.
    """
    machine_dir = tmp_path / "machine"
    monkeypatch.setenv(resolve_lock.INSTANCE_LOCK_DIR_ENV, str(machine_dir))
    roots = []
    for name in ("checkout-a", "checkout-b"):
        root = tmp_path / name
        package = root / "library" / "tools" / "resolved"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        roots.append(root)

    sockets = set()
    for root in roots:
        monkeypatch.setattr(client, "_engine_root", lambda root=root: root)
        sockets.add(client._path())

    assert sockets == {machine_dir / SOCKET_NAME}


def test_the_brokers_own_directory_holds_its_socket(machine):
    """The broker's files live in ONE directory: its socket's."""
    assert broker_paths.socket_path() == machine / SOCKET_NAME
    assert broker_paths.db_path() == machine / broker_paths.DB_NAME
    assert broker_paths.cancel_dir() == machine / broker_paths.CANCEL_DIR_NAME


# ── One broker, whatever asks ─────────────────────────────────────────

def test_two_lanes_ensure_exactly_one_broker(machine, serving_broker,
                                              monkeypatch):
    """The live defect: a lane's client must not start a second broker.

    Two lanes with private lock dirs, one machine. Both lanes reach the
    machine's broker, and neither starts one of its own.
    """
    lane_a = _short_dir("ren-lane-a-")
    lane_b = _short_dir("ren-lane-b-")
    try:
        monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lane_a))
        first = client.ensure(timeout=1)
        monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(lane_b))
        second = client.ensure(timeout=1)

        assert first is not None and first["pid"] == SERVING["pid"]
        assert second is not None and second["pid"] == SERVING["pid"]
        assert serving_broker["started"] == [], (
            "a lane started a broker of its own against the one Resolve")
        assert serving_broker["pings"] == [machine / SOCKET_NAME] * 2, (
            "a lane asked a socket that is not the machine's")
    finally:
        shutil.rmtree(lane_a, ignore_errors=True)
        shutil.rmtree(lane_b, ignore_errors=True)


def test_status_from_another_lane_reports_the_serving_broker(
        machine, serving_broker, monkeypatch, capsys):
    """`ren resolved status` from any lane reports the one broker."""
    monkeypatch.setenv(resolve_lock.LOCK_DIR_ENV, str(_short_dir("ren-lane-")))
    assert resolved_cli.main(["status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["serving"] is True
    assert status["pid"] == SERVING["pid"]
    assert serving_broker["started"] == []


def test_a_worktree_client_refuses_to_spawn_the_machine_broker(
        machine, serving_broker, monkeypatch):
    """Never point a broker at a worker worktree.

    A broker started in a worktree serves production Resolve calls on
    UNMERGED code, from a directory a worktree tool may reclaim. The
    client refuses to start one; a broker already serving is still used.
    """
    monkeypatch.setattr(client, "_in_pytest", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    # No broker is serving: the client is about to start one.
    monkeypatch.setattr(client, "ping", lambda path=None: None)
    with pytest.raises(RenRefusal) as exc_info:
        client.ensure(timeout=1)
    assert serving_broker["started"] == []
    assert "linked worktree" in exc_info.value.why
    assert exc_info.value.fix, "the refusal names its fix"


def test_serve_refuses_the_machine_broker_from_a_worktree(
        machine, monkeypatch):
    """`ren resolved serve` from a worktree refuses the machine's broker."""
    monkeypatch.setattr(broker_paths, "_in_test_suite", lambda: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    with pytest.raises(SystemExit) as exc_info:
        serve(path=machine / SOCKET_NAME)
    assert "linked worktree" in str(exc_info.value)
    assert not (machine / SOCKET_NAME).exists()
