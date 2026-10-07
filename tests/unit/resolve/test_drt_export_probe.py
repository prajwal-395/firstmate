"""The broker exports a named, non-current Qualification timeline by UID."""

import pytest

from library.tools.resolved import drt_export_probe, jobs
from library.tools.resolved.jobs import QUALIFICATION_PROJECT, JobRefused
from tests.resolve_double import FakeProject, FakeResolve, FakeTimeline


def _params(**overrides):
    return {"project": QUALIFICATION_PROJECT,
            "timeline_uid": "target-uid", **overrides}


def test_prepare_is_typed_exclusive_and_qualification_only():
    from library.tools.resolved.scheduler import EXCLUSIVE

    shape = jobs.prepare(drt_export_probe.KIND, _params())

    assert shape["mode"] == EXCLUSIVE
    assert shape["executed"] is True
    assert shape["qualification"] is True
    assert shape["project"] == QUALIFICATION_PROJECT
    assert shape["timeline"] == ""
    assert shape["locality"] == "project"
    assert shape["coalesce_key"] is None


@pytest.mark.parametrize("params, message", [
    (_params(project="Podcast"), "only target 'Ren Qualification'"),
    (_params(timeline_uid=" "), "non-empty 'timeline_uid'"),
    (_params(project_folder="/tmp/other-project"), "caller-supplied paths"),
    (_params(script="print('unsafe')"), "caller-supplied paths"),
])
def test_prepare_refuses_other_projects_and_open_ended_payloads(params,
                                                                  message):
    with pytest.raises(JobRefused, match=message):
        jobs.prepare(drt_export_probe.KIND, params)


def test_run_exports_the_uid_target_without_moving_page_or_cursor(
        monkeypatch, tmp_path):
    current = FakeTimeline("Q Current", uid="current-uid")
    target = FakeTimeline("Q Different Resolution", uid="target-uid")
    project = FakeProject(QUALIFICATION_PROJECT, [current, target],
                          current=current)
    resolve = FakeResolve(project)
    calls = []
    archive_path = tmp_path / "target.drt"

    def export(handle, timeline, name, folder, label=""):
        calls.append((handle, timeline, name, folder, label))
        return {"archived": True, "path": str(archive_path)}

    monkeypatch.setattr(
        "library.tools.versions.store.export_timeline_recovery_archive",
        export)
    monkeypatch.setattr(
        "library.tools.qualification_project.drt_export_probe_project_folder",
        lambda: tmp_path / "qualification-store")
    job = {"id": "export-1", "kind": drt_export_probe.KIND,
           "project": QUALIFICATION_PROJECT, "params": _params()}

    result = jobs.run(job, resolve)

    assert len(calls) == 1
    assert calls[0][0] is resolve
    assert calls[0][1] is target
    assert calls[0][2] == "Q Different Resolution"
    assert calls[0][3] == str(tmp_path / "qualification-store")
    assert calls[0][4] == "timeline.drt_export_probe export-1"
    assert result["archive_path"] == str(archive_path)
    assert result["archived"] is True
    assert result["current_timeline_uid_before"] == "current-uid"
    assert result["current_timeline_uid_after"] == "current-uid"
    assert result["current_page_before"] == "edit"
    assert result["current_page_after"] == "edit"
    assert result["cursor_stayed_put"] is True
    assert project.GetCurrentTimeline() is current
    assert resolve.GetCurrentPage() == "edit"


def test_run_refuses_current_timeline_before_export(monkeypatch):
    current = FakeTimeline("Q Current", uid="target-uid")
    project = FakeProject(QUALIFICATION_PROJECT, [current], current=current)
    resolve = FakeResolve(project)
    called = False

    def export(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"archived": True, "path": "/tmp/unexpected.drt"}

    monkeypatch.setattr(
        "library.tools.versions.store.export_timeline_recovery_archive",
        export)
    job = {"id": "export-2", "kind": drt_export_probe.KIND,
           "project": QUALIFICATION_PROJECT, "params": _params()}

    with pytest.raises(JobRefused, match="requires a non-current timeline") as exc:
        jobs.run(job, resolve)

    assert called is False
    assert exc.value.result == {
        "current_timeline_uid_before": "target-uid",
        "current_page_before": "edit",
        "current_timeline_uid_after": "target-uid",
        "current_page_after": "edit",
        "cursor_stayed_put": True,
    }
    assert project.GetCurrentTimeline() is current


def test_run_refuses_uid_that_does_not_name_a_timeline():
    current = FakeTimeline("Q Current", uid="current-uid")
    project = FakeProject(QUALIFICATION_PROJECT, [current], current=current)
    resolve = FakeResolve(project)
    job = {"id": "export-3", "kind": drt_export_probe.KIND,
           "project": QUALIFICATION_PROJECT,
           "params": _params(timeline_uid="missing-uid")}

    with pytest.raises(JobRefused, match="expected exactly one timeline"):
        jobs.run(job, resolve)


def test_export_failure_still_reports_cursor_and_archive_reason(
        monkeypatch, tmp_path):
    current = FakeTimeline("Q Current", uid="current-uid")
    target = FakeTimeline("Q Same Resolution", uid="target-uid")
    project = FakeProject(QUALIFICATION_PROJECT, [current, target],
                          current=current)
    resolve = FakeResolve(project)
    monkeypatch.setattr(
        "library.tools.versions.store.export_timeline_recovery_archive",
        lambda *_args, **_kwargs: {
            "archived": False, "reason": "DRT export failed: no file"})
    monkeypatch.setattr(
        "library.tools.qualification_project.drt_export_probe_project_folder",
        lambda: tmp_path / "qualification-store")
    job = {"id": "export-4", "kind": drt_export_probe.KIND,
           "project": QUALIFICATION_PROJECT, "params": _params()}

    result = jobs.run(job, resolve)

    assert result["archived"] is False
    assert result["archive_path"] is None
    assert result["reason"] == "DRT export failed: no file"
    assert result["cursor_stayed_put"] is True
