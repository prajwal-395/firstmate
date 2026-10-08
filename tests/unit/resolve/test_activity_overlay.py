from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from library.tools import qualification_project, resolve_lock
from library.tools.resolved import cancellation, jobs
from library.tools.resolved import worker as broker_worker
from library.tools.resolved.server import Broker


def test_activity_probe_is_qualification_only_and_bounded():
    shape = jobs.prepare("resolve.activity_probe", {"seconds": 2},
                         qualification=True)
    assert shape["project"] == jobs.QUALIFICATION_PROJECT
    assert shape["mode"] == "exclusive"
    assert shape["executed"] is True
    with pytest.raises(jobs.JobRefused, match="qualification runs"):
        jobs.prepare("resolve.activity_probe", {}, qualification=False)
    with pytest.raises(jobs.JobRefused, match="between 0.1 and 30"):
        jobs.prepare("resolve.activity_probe", {"seconds": 31},
                     qualification=True)


def test_activity_probe_cancel_restores_borrowed_context(monkeypatch):
    states = []

    @contextmanager
    def borrowed(_resolve):
        states.append("entered")
        try:
            yield {"project": "Podcast (field test)", "timeline": "Reel 09"}
        finally:
            states.append("restored")

    monkeypatch.setattr(qualification_project, "borrowed", borrowed)
    class Resolve:
        @staticmethod
        def GetProjectManager():
            return "manager"

    monkeypatch.setattr(
        qualification_project, "switch_project",
        lambda manager, name: states.append(("switch", manager, name)))
    monkeypatch.setattr(cancellation, "checkpoint", lambda _job_id: (
        (_ for _ in ()).throw(cancellation.CancelRequested("stop"))))
    with pytest.raises(cancellation.CancelRequested, match="stop"):
        jobs.run({"id": "probe-1", "kind": "resolve.activity_probe",
                  "params": {"seconds": 2}}, resolve=Resolve())
    assert states == ["entered", ("switch", "manager",
                                  jobs.QUALIFICATION_PROJECT), "restored"]


def test_worker_borrowing_probe_runs_while_captain_project_open(monkeypatch):
    """Parity with the Python broker (#2154): a borrowing qualification
    job enters from the captain's open project, so the worker's
    open-project gate must not refuse it - the runner borrows and
    restores instead.
    """
    states = []

    @contextmanager
    def borrowed(_resolve):
        states.append("entered")
        try:
            yield {"project": "Podcast (field test)", "timeline": "Reel 09",
                   "timeline_id": "tid-9", "page": "edit"}
        finally:
            states.append("restored")

    monkeypatch.setattr(qualification_project, "borrowed", borrowed)
    monkeypatch.setattr(
        qualification_project, "switch_project",
        lambda manager, name: states.append(("switch", name)))

    class Manager:
        @staticmethod
        def GetCurrentProject():
            class Project:
                @staticmethod
                def GetName():
                    return "Podcast (field test)"
            return Project()

    class Resolve:
        @staticmethod
        def GetProjectManager():
            return Manager()

    monkeypatch.setattr(broker_worker, "_connect_cached", lambda: Resolve())

    @contextmanager
    def no_lease(*args, **kwargs):
        yield

    monkeypatch.setattr(resolve_lock, "resolve_lease", no_lease)
    job = {"id": "probe-w1", "kind": "resolve.activity_probe",
           "mode": "exclusive", "qualification": True, "executed": True,
           "project": jobs.QUALIFICATION_PROJECT,
           "params": {"seconds": 0.1}}
    reply = broker_worker._execute_job(job)
    assert reply["ok"] is True, reply
    assert reply["result"]["timeline_edits"] == 0
    assert ("switch", jobs.QUALIFICATION_PROJECT) in states
    assert states[0] == "entered" and states[-1] == "restored"


def test_worker_non_borrowing_qualification_job_still_needs_it_open(
        monkeypatch):
    """The exemption is narrow: a plain qualification read while the
    captain's project is open is still refused before it runs.
    """
    class Manager:
        @staticmethod
        def GetCurrentProject():
            class Project:
                @staticmethod
                def GetName():
                    return "Podcast (field test)"
            return Project()

    class Resolve:
        @staticmethod
        def GetProjectManager():
            return Manager()

    monkeypatch.setattr(broker_worker, "_connect_cached", lambda: Resolve())

    @contextmanager
    def no_lease(*args, **kwargs):
        yield

    monkeypatch.setattr(resolve_lock, "resolve_lease", no_lease)
    job = {"id": "snap-w1", "kind": "timeline.snapshot",
           "mode": "exclusive", "qualification": True, "executed": True,
           "project": jobs.QUALIFICATION_PROJECT,
           "params": {"project": jobs.QUALIFICATION_PROJECT,
                      "timeline": "Q Spine"}}
    reply = broker_worker._execute_job(job)
    assert reply["ok"] is False, reply
    assert reply.get("refused") is True
    assert "needs 'Ren Qualification' open" in reply["error"]


def test_running_probe_stop_writes_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)

    class Store:
        @staticmethod
        def get(job_id):
            return {"id": job_id, "kind": "resolve.activity_probe",
                    "state": "running"}

        @staticmethod
        def interrupt_policy(_job_id):
            return jobs.INTERRUPT_POLICIES["resolve.activity_probe"]

    broker = object.__new__(Broker)
    import threading
    broker._cond = threading.Condition()
    broker._queued = {}
    broker._running = {"probe-2": object()}
    broker.store = Store()

    job = broker.request_cancel("probe-2")
    assert job["state"] == "running"
    assert cancellation.requested("probe-2")


def test_running_edit_job_defers_stop_until_patch_commit(monkeypatch, tmp_path):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)

    class Store:
        @staticmethod
        def get(job_id):
            return {"id": job_id, "kind": "timeline.apply_patch",
                    "state": "running"}

        @staticmethod
        def interrupt_policy(_job_id):
            return jobs.INTERRUPT_POLICIES["timeline.apply_patch"]

    broker = object.__new__(Broker)
    import threading
    broker._cond = threading.Condition()
    broker._queued = {}
    broker._running = {"edit-1": object()}
    broker.store = Store()

    assert broker.request_cancel("edit-1")["state"] == "running"
    assert cancellation.requested("edit-1")


@pytest.mark.parametrize("kind", [
    "resolve.activity_probe",
    "resolve_axi",
    "timeline.apply_patch",
    "resolve.ui_transaction",
    "resolve.qualification_session",
    "reel.promote_staged",
    "reel.prepare_qualification_fixture",
    "reel.probe_qualification_marker_loss",
    "reel.copy_tv_ending",
])
def test_running_transactional_job_accepts_stop_request(
        kind, monkeypatch, tmp_path):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)

    class Store:
        @staticmethod
        def get(job_id):
            return {"id": job_id, "kind": kind, "state": "running"}

        @staticmethod
        def interrupt_policy(_job_id):
            return jobs.INTERRUPT_POLICIES.get(kind, "")

    broker = object.__new__(Broker)
    import threading
    broker._cond = threading.Condition()
    broker._queued = {}
    broker._running = {"job-1": object()}
    broker.store = Store()

    assert broker.request_cancel("job-1")["state"] == "running"
    assert cancellation.requested("job-1")


def test_cancel_checkpoint_is_cooperative_and_clears_by_runner(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)
    cancellation.request("probe-3")
    with pytest.raises(cancellation.CancelRequested):
        cancellation.checkpoint("probe-3")
    cancellation.clear("probe-3")
    assert not cancellation.requested("probe-3")


def test_commit_checkpoint_keeps_committed_receipt_on_stop(tmp_path, monkeypatch):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)
    cancellation.request("patch-1")
    receipt = {"status": "committed", "generation": 12}

    with pytest.raises(cancellation.CancelRequested) as stopped:
        jobs._commit_checkpoint({"id": "patch-1"}, receipt)

    assert stopped.value.result == receipt


def test_interrupt_policy_survives_store_reopen_without_receipt_leak(tmp_path):
    from library.tools.resolved.store import JobStore

    database = tmp_path / "jobs.sqlite3"
    job = {
        "id": "persist-policy", "kind": "timeline.apply_patch",
        "priority": "mutation", "mode": "exclusive", "executed": 1,
        "params": {"patch": {"id": "persist-policy"}},
        "project": "P", "timeline": "T", "qualification": 0,
        "coalesce_key": None,
        "interrupt_policy": jobs.INTERRUPT_POLICIES[
            "timeline.apply_patch"],
        "owner": "test", "state": "running", "submitted": 1.0,
    }
    store = JobStore(database)
    store.insert(job)
    store.close()

    reopened = JobStore(database)
    try:
        assert reopened.interrupt_policy("persist-policy") == \
            jobs.INTERRUPT_POLICIES["timeline.apply_patch"]
        assert "interrupt_policy" not in reopened.get("persist-policy")
    finally:
        reopened.close()


def test_sleep_wake_gap_stops_at_checkpoint_and_keeps_commit(tmp_path,
                                                            monkeypatch):
    wall, suspend_clock = [100.0], [50.0]
    monkeypatch.setattr(cancellation, "_agent_clock", lambda: wall[0])
    monkeypatch.setattr(cancellation, "_suspend_clock",
                        lambda: suspend_clock[0])
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)
    cancellation.begin("sleep-1")
    wall[0] += 20.0
    suspend_clock[0] += 1.0
    receipt = {"status": "committed", "generation": 13}

    with pytest.raises(cancellation.CancelRequested,
                       match="machine sleep") as stopped:
        cancellation.checkpoint("sleep-1", result=receipt)

    assert stopped.value.result == receipt
    cancellation.end("sleep-1")


def test_native_overlay_is_nonactivating_and_shields_resolve_only():
    root = Path(__file__).resolve().parents[3]
    source = (root / "native/resolve-activity-overlay/main.swift").read_text()
    assert ".nonactivatingPanel" in source
    assert "panel?.order(.above, relativeTo:" in source
    assert "override func hitTest(_ point: NSPoint)" in source
    assert "return self" in source
    assert "frontmost:" in source
    assert '"op": "status"' in source
    assert '"op": "cancel"' in source
    assert "Elapsed %02d:%02d" in source
    assert "messageLabel.frame = NSRect(x: 14, y: 12, width: 250, height: 16)" in source
    assert "takeover.frame = NSRect(x: 278, y: 12, width: 118, height: 24)" in source


def test_native_overlay_builder_is_macos_appkit_only():
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts/build_resolve_activity_overlay.sh").read_text()
    assert "swiftc" in script
    assert "-framework AppKit" in script
    assert '$(uname)" != "Darwin"' in script
