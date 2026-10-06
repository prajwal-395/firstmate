"""Reel-version events: what versions a worker cut, in the operation history.

The spec's "everything should be event-sourced": every reel version a
build, touch-up, undo, rollback or redo records is a worker operation.
The versions ledger says WHAT each reel holds; the event says WHEN
each version landed, in the history a resumed worker reads. Every test
names a defect it catches: on origin/main versions land with no
event, so a dead worker's builds are missing from the history.
"""
import pytest

from library.tools import event_log
from library.tools.event_log import read_events
from library.tools.versions import reel_versions


@pytest.fixture
def project(tmp_path):
    """A project folder with a LOGS area, never a real one."""
    folder = tmp_path / "project"
    (folder / "pipeline_output" / "logs").mkdir(parents=True)
    return str(folder)


def _recorded(project):
    return [e for e in read_events(project)
            if e["type"] == event_log.REEL_VERSION_RECORDED]


def test_record_leaves_a_version_event(project):
    """A new version is an operation: the log names the reel and kind.

    Defect caught: the ledger holds version 1 but the event log never
    mentions it, so the worker's build is invisible in the operation
    history.
    """
    entry = reel_versions.record(project, "Reel 09",
                                 kind=reel_versions.KIND_BUILD,
                                 rows={"clip_001": [0, 48]})
    assert entry["version"] == 1
    events = _recorded(project)
    assert len(events) == 1
    assert events[0]["fields"] == {
        "final": "Reel 09", "version": 1, "kind": "build"}


def test_versions_land_in_order(project):
    """Versions 1..N read as N events in the order cut.

    Defect caught: an emit that fires once per ledger instead of once
    per version collapses the history - a resumed worker cannot tell
    how many builds ran.
    """
    reel_versions.record(project, "Reel 09", kind=reel_versions.KIND_BUILD,
                         rows={"clip_001": [0, 48]})
    reel_versions.record(project, "Reel 09", kind=reel_versions.KIND_TOUCH,
                         rows={"clip_001": [0, 48]})
    events = _recorded(project)
    assert [(e["fields"]["version"], e["fields"]["kind"])
            for e in events] == [(1, "build"), (2, "touch")]


def test_a_refused_kind_leaves_no_event(project):
    """No invented operations: a version never recorded emits nothing.

    Defect caught: an emit before the kind check would record a
    version the ledger refused, and a resumed worker would read a
    history of things that never happened.
    """
    with pytest.raises(ValueError):
        reel_versions.record(project, "Reel 09", kind="remix",
                             rows={})
    assert read_events(project) == []


def test_a_log_failure_never_takes_down_a_version(project, monkeypatch):
    """The version is the act, the event is its record.

    Defect caught: an emit that raises through `record` turns a
    logging failure into a lost version - the same refusal the lease
    layer already refuses to make (`resource_leases._emit`).
    """
    def _boom(*args, **kwargs):
        raise OSError("disk gone")
    monkeypatch.setattr(event_log, "emit", _boom)
    entry = reel_versions.record(project, "Reel 09",
                                 kind=reel_versions.KIND_BUILD,
                                 rows={"clip_001": [0, 48]})
    assert entry["version"] == 1
    assert len(reel_versions.versions_of(project, "Reel 09")) == 1
