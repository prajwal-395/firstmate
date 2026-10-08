from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from library.tools import qualification_project
from library.tools.resolved import cancellation, jobs
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


def test_running_probe_stop_writes_marker(monkeypatch, tmp_path):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)

    class Store:
        @staticmethod
        def get(job_id):
            return {"id": job_id, "kind": "resolve.activity_probe",
                    "state": "running"}

    broker = object.__new__(Broker)
    import threading
    broker._cond = threading.Condition()
    broker._queued = {}
    broker._running = {"probe-2": object()}
    broker.store = Store()

    job = broker.request_cancel("probe-2")
    assert job["state"] == "running"
    assert cancellation.requested("probe-2")


def test_running_edit_job_refuses_stop_without_inverse(monkeypatch, tmp_path):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)

    class Store:
        @staticmethod
        def get(job_id):
            return {"id": job_id, "kind": "timeline.apply_patch",
                    "state": "running"}

    broker = object.__new__(Broker)
    import threading
    broker._cond = threading.Condition()
    broker._queued = {}
    broker._running = {"edit-1": object()}
    broker.store = Store()

    with pytest.raises(jobs.JobRefused, match="no safe running cancellation"):
        broker.request_cancel("edit-1")
    assert not cancellation.requested("edit-1")


def test_cancel_checkpoint_is_cooperative_and_clears_by_runner(tmp_path,
                                                               monkeypatch):
    monkeypatch.setattr(cancellation, "marker_path",
                        lambda job_id: tmp_path / "cancel" / job_id)
    cancellation.request("probe-3")
    with pytest.raises(cancellation.CancelRequested):
        cancellation.checkpoint("probe-3")
    cancellation.clear("probe-3")
    assert not cancellation.requested("probe-3")


def test_native_overlay_is_nonactivating_and_shields_resolve_only():
    root = Path(__file__).resolve().parents[3]
    source = (root / "native/resolve-activity-overlay/main.swift").read_text()
    assert ".nonactivatingPanel" in source
    assert "panel?.order(.above, relativeTo:" in source
    assert "override func hitTest(_ point: NSPoint)" in source
    assert "return self" in source
    assert "frontmost:" in source
    assert '"op": "list"' in source
    assert '"op": "cancel"' in source
    assert "Elapsed %02d:%02d" in source


def test_native_overlay_builder_is_macos_appkit_only():
    root = Path(__file__).resolve().parents[3]
    script = (root / "scripts/build_resolve_activity_overlay.sh").read_text()
    assert "swiftc" in script
    assert "-framework AppKit" in script
    assert '$(uname)" != "Darwin"' in script
