"""The broker's turn is for the work, not for the wait.

Three defects, one file, because they are one defect seen from three
sides: a lane that waits for a machine resource WHILE HOLDING the
machine's turn, and a broker that is stopped without saying who asked.

What was measured (2026-10-21, the machine's own broker, 36 h, 8238
jobs, `ren resolved kpi`):

    24.14%  of every blocked second: `resolve.qualification_session`
    17.01%  of every blocked second: `resolve_axi` "Running Resolve
            command: project" - the scratch-project lifecycle

and, in the job table, three `project scratch open` jobs that HELD the
machine's turn for the whole 900 s lease timeout each and ended with

    error: scratch project operation failed: the scratch origin is held
    by 'pid-45290' (pid 45290)

The wait was inside the turn. The holder of the scratch-origin slot is
inside Resolve and needs that turn to restore the origin and release
the slot, so the waiter held the very turn the holder needed: fifteen
minutes in which no other lane could be granted a turn at all.

So the wait moves to the caller, where no turn is held.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from library.tools import resolve_axi_core_commands as commands
from library.tools import resolve_scratch_projects as scratch
from library.tools.resolved import client


# ── the scratch-origin slot ──────────────────────────────────────────


def _held_slot(tmp_path: Path, monkeypatch, owner_pid: int) -> Path:
    """A scratch-origin ledger whose newest record names a LIVE holder."""
    origin = tmp_path / "scratch-origin.json"
    monkeypatch.setattr(scratch, "_origin_path", lambda: origin)
    origin.write_text(json.dumps({
        "format": scratch.ORIGIN_LEDGER_FORMAT,
        "records": [{
            "owner": f"pid-{owner_pid}",
            "owner_pid": owner_pid,
            "owner_host": "test",
            "owner_start": "now",
            "project": "Ren Scratch - held",
            "scratch_project": "Ren Scratch - held",
            "timeline": "T",
            "timeline_id": "uid",
            "page": "edit",
            "recorded_at": 1.0,
        }],
    }), encoding="utf-8")
    return origin


def test_a_scratch_command_refuses_fast_when_the_slot_is_held(
        monkeypatch, capsys):
    """A held slot is a refusal with its own exit code, not a 900 s wait.

    The command runs INSIDE the broker's turn, so a wait here holds the
    machine's turn against every other lane for the length of the
    wait. The refusal is immediate and says who holds the slot.
    """
    calls = []

    def refuse(args, scratch_module):
        raise scratch.ScratchOriginHeld(
            "the scratch origin is held by 'pid-1' (pid 1); wait for it to "
            "be released before moving the machine")

    monkeypatch.setattr(commands, "_scratch_apply", refuse)
    monkeypatch.setattr(scratch, "wait_for_origin_slot",
                        lambda **kwargs: pytest.fail(
                            "the command must not wait for the slot itself"))

    code = commands.cmd_project_scratch(SimpleNamespace(
        apply=True, scratch_command="open", name="Ren Scratch - other"))

    assert code == scratch.SLOT_HELD_EXIT_CODE
    assert "the scratch origin is held by" in capsys.readouterr().out


def test_the_caller_waits_for_the_slot_before_the_job_is_queued(
        monkeypatch):
    """The wait happens in the caller, where no turn is held.

    The caller waits for the slot and only then runs the command; the
    command's fast refusal is what the caller retries on.
    """
    order = []

    def wait(**kwargs):
        order.append("waited")
        return {"released": True, "stale": False, "timed_out": False}

    def run():
        order.append("ran")
        return 0

    monkeypatch.setattr(scratch, "wait_for_origin_slot", wait)
    monkeypatch.setattr(commands, "_wait_for_the_scratch_slot",
                        lambda deadline: (order.append("waited") or
                                          {"released": True,
                                           "stale": False,
                                           "timed_out": False}))

    code = commands._run_with_the_scratch_slot(run, 0.0)

    assert code == 0
    assert order == ["waited", "ran"]


def test_a_held_slot_is_waited_for_outside_the_command(monkeypatch):
    """The caller's wait is bounded and happens before the command runs."""
    waited = []

    def wait(timeout=None, poll=None):
        waited.append(timeout)
        return {"released": False, "stale": False, "timed_out": True}

    monkeypatch.setattr(scratch, "wait_for_origin_slot", wait)
    commands._wait_for_the_scratch_slot(100.0)
    assert waited and waited[0] <= 100.0


def test_a_slot_that_is_still_held_means_the_command_does_not_run(
        monkeypatch, capsys):
    """Catches: the command run against a slot that never opened.

    The command would wait inside the turn again - the defect this path
    exists to remove - so it is NOT run, and the refusal names the
    holder.
    """
    monkeypatch.setattr(
        scratch, "wait_for_origin_slot",
        lambda timeout=None, poll=None: {
            "released": False, "stale": False, "timed_out": True,
            "holder": "pid-9999"})
    monkeypatch.setattr(
        commands, "_scratch_apply",
        lambda args, scratch_module: pytest.fail(
            "the command must not run while the slot is held"))

    code = commands._run_with_the_scratch_slot(
        lambda: pytest.fail("the command must not run"), 0.0)

    assert code == scratch.SLOT_HELD_EXIT_CODE
    assert "pid-9999" in capsys.readouterr().out


def test_the_slot_refusal_is_its_own_exception():
    """A held slot is distinguishable from a broken scratch project."""
    assert issubclass(scratch.ScratchOriginHeld, scratch.ScratchProjectError)
    assert scratch.SLOT_HELD_EXIT_CODE not in (0, 1)


# ── who stopped the broker ───────────────────────────────────────────


def test_a_shutdown_request_names_the_requester(monkeypatch):
    """Every stop says who asked, in the request the daemon reads.

    The broker's log showed nineteen shutdowns in one day and named the
    requester of none.
    """
    sent = {}

    def capture(request, path=None, timeout=30.0):
        sent.update(request)
        return {"ok": True}

    monkeypatch.setattr(client, "call", capture)
    monkeypatch.setattr(client, "_path", lambda path=None: Path("/tmp/x.sock"))
    monkeypatch.setattr(client, "_is_default_socket", lambda target: False)

    client.request_shutdown(Path("/tmp/x.sock"))

    assert sent["op"] == "shutdown"
    assert f"pid {os.getpid()}" in sent["requester"]


def test_the_python_daemon_logs_the_requester(monkeypatch, tmp_path):
    """The daemon's own log line names the requester too."""
    from library.tools.resolved import server

    logged = []
    monkeypatch.setattr(server, "_log_broker", lambda line: logged.append(line))

    class Broker:
        def stop(self):
            pass

    handler = server._Handler.__new__(server._Handler)
    handler.server = SimpleNamespace(broker=Broker())
    handler._dispatch(Broker(), {"op": "shutdown",
                                 "requester": "pid 42: ren stop"}, None)

    assert any("pid 42: ren stop" in line for line in logged)


def test_a_linked_worktree_does_not_stop_the_machine_broker(monkeypatch):
    """PR 3041 refuses a START from a worktree; a STOP has the same reach.

    The machine has one broker and every lane uses it. A worktree copy
    is not the installed engine that owns it.
    """
    monkeypatch.setattr(client, "_path", lambda path=None: Path("/prod.sock"))
    monkeypatch.setattr(client, "_is_default_socket", lambda target: True)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    monkeypatch.setattr(client, "call", lambda *args, **kwargs: pytest.fail(
        "a linked worktree must not stop the machine's broker"))

    with pytest.raises(client.ShutdownRefused):
        client.request_shutdown()


def test_a_private_broker_is_stoppable_from_a_worktree(monkeypatch):
    """A broker that names its own socket is not the machine's broker."""
    monkeypatch.setattr(client, "_path", lambda path=None: Path("/private.sock"))
    monkeypatch.setattr(client, "_is_default_socket", lambda target: False)
    monkeypatch.setattr(client, "_is_linked_worktree", lambda root: True)
    sent = {}
    monkeypatch.setattr(client, "call",
                        lambda request, path=None, timeout=30.0: sent.update(
                            request))

    client.request_shutdown()

    assert sent["op"] == "shutdown"


# ── the qualification session's media facts ──────────────────────────


def test_the_workflow_job_carries_the_media_facts(monkeypatch, tmp_path):
    """The job never opens the media file: the facts are the caller's.

    Hashing a media copy and running ffprobe on it are file facts, not
    Resolve facts, and the broker's turn is what every lane waits
    behind.
    """
    from library.tools import qualification_project as qp
    from library.tools.qualification_workflow import prepare_broker_job
    from library.tools.resolved.jobs import JobRefused

    media = tmp_path / "media"
    runs = media / "workflow-runs"
    stamp = "20260101T000000000000Z"
    run_dir = runs / stamp
    (run_dir / "project").mkdir(parents=True)
    source = media / "source.mp4"
    source.write_bytes(b"fixture")
    monkeypatch.setattr(qp, "media_dir", lambda: media)
    monkeypatch.setattr(qp, "real_media_dir", lambda: media)

    base = {
        "project": "Ren Qualification",
        "source_copy": str(source),
        "timeline": f"Q Workflow {stamp} 1",
        "run_stamp": stamp,
        "source_start": 0.0,
        "source_end": 10.0,
        "output_dir": str(run_dir),
        "output_name": "out",
        "timeout_seconds": 600,
        "source_sha256": "0" * 64,
        "lifecycle_project_folder": str(run_dir / "project"),
        "lifecycle_run_id": stamp,
    }
    with pytest.raises(JobRefused):
        prepare_broker_job(dict(base))
    shape = prepare_broker_job(dict(base, media_fps=24.0,
                                    media_total_frames=240))
    assert shape["mode"] == "exclusive"
    with pytest.raises(JobRefused):
        prepare_broker_job(dict(base, media_fps=0,
                                media_total_frames=240))
    with pytest.raises(JobRefused):
        prepare_broker_job(dict(base, media_fps=24.0,
                                media_total_frames=0))
