"""Preview-rendered events: a finished timeline exported and recorded.

The spec's "everything should be event-sourced": the export at the end
of step 6.01 is the worker's rendered preview of its timeline, and the
build record commit is what makes it durable. The record says WHAT was
exported; the event says WHEN, in the operation history a resumed
worker reads. Every test names a defect it catches: on origin/main the
export commits with no event, so the render is missing from the
history.
"""
from pathlib import Path

import pytest

from library.tools import event_log
from library.tools.event_log import read_events
from library.tools.versions import store as bvc


class _ExportTimeline:
    def __init__(self, name, payload: bytes = b"OTIO-body"):
        self._name = name
        self._payload = payload

    def GetName(self):
        return self._name

    def Export(self, path, _flag):
        Path(path).write_bytes(self._payload)
        return True


@pytest.fixture
def project(tmp_path):
    """A git project with a LOGS area, never a real one."""
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "pipeline_output" / "logs").mkdir(parents=True)
    assert bvc.init_project_repo(str(folder))["initialised"]
    return str(folder)


def test_a_recorded_export_leaves_a_preview_rendered_event(project):
    """The export committed: the log names the timeline.

    Defect caught: the build record is committed but the event log
    never mentions the export, so the render is invisible in the
    operation history a resumed worker reads.
    """
    report = bvc.record_finished_timeline(
        object(), _ExportTimeline("cut_01"), project, "cut_01")
    assert report["committed"], report
    rendered = [e for e in read_events(project)
                if e["type"] == event_log.PREVIEW_RENDERED]
    assert len(rendered) == 1
    assert rendered[0]["fields"] == {"timeline": "cut_01"}


def test_a_declined_export_leaves_no_event(tmp_path):
    """No invented operations: an export that never ran emits nothing.

    Defect caught: an emit before the decline checks would record a
    render for a timeline Resolve never exported, and a resumed worker
    would read a history of things that never happened.
    """
    class _Never:
        def Export(self, path, flag):
            raise AssertionError("must not reach Resolve without a repo")

    report = bvc.record_finished_timeline(
        object(), _Never(), str(tmp_path), "cut_01")
    assert report == {"committed": False, "reason": "no-repo", "files": []}
    assert read_events(str(tmp_path)) == []


def test_a_log_failure_never_takes_down_a_build_record(project,
                                                       monkeypatch):
    """The export is the act, the event is its record.

    Defect caught: an emit that raises through
    `record_finished_timeline` turns a logging failure into a broken
    build record - the same refusal the lease layer already refuses
    to make (`resource_leases._emit`).
    """
    def _boom(*args, **kwargs):
        raise OSError("disk gone")
    monkeypatch.setattr(event_log, "emit", _boom)
    report = bvc.record_finished_timeline(
        object(), _ExportTimeline("cut_01"), project, "cut_01")
    assert report["committed"], report
