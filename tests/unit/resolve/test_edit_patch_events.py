"""Patch events: the N timeline operations one patch ran.

The spec's "everything should be event-sourced": a committed patch is
N operations a worker ran against the live timeline, and the receipt's
verdict says whether they held. The shadow generations say WHAT the
timeline became; the event says WHEN, HOW MANY operations ran and what
the verdict was - the operation history a resumed worker reads.
Every test names a defect it catches: on origin/main a patch commits
with no event, so the operations are invisible in the history.
"""
import pytest

from library.tools import edit_patch, event_log
from library.tools import timeline_shadow as shadow
from library.tools.event_log import read_events
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)


@pytest.fixture
def world(tmp_path):
    (tmp_path / "pipeline_output" / "logs").mkdir(parents=True)
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        yield project, timeline, item, store, base


def _patch(pid, base, operations, domains, spans, **extra):
    return {"id": pid, "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base, "capability": "reel.touchup",
            "affected_spans": spans, "conflict_domains": domains,
            "operations": operations, **extra}


def _apply(patch, project, timeline, store):
    return edit_patch.apply_patch(patch, resolve=FakeResolve(project),
                                  project=project, timeline=timeline,
                                  store=store,
                                  project_folder=store.path.parent)


def _marker_patch(pid, base, item):
    return _patch(pid, base.generation, [
        {"op": "marker.add", "frame": 12, "color": "Blue", "name": "beat"},
        {"op": "clip.set_enabled", "unique_id": item.GetUniqueId(),
         "enabled": False},
    ], ["markers", "timeline_structure"], [[0, 48]],
        preconditions=[{"kind": "marker_absent", "frame": 12}],
        postconditions=[{"kind": "duration_unchanged"}])


def _events(project_folder):
    return [e for e in read_events(str(project_folder))
            if e["type"] == event_log.TIMELINE_OPERATIONS]


def test_a_committed_patch_leaves_a_timeline_operations_event(world):
    """Two operations ran: the log names the patch, its verdict, its count.

    Defect caught: the shadow records the new generation but the event
    log never mentions the patch, so the N operations the worker ran
    are missing from the operation history.
    """
    project, timeline, item, store, base = world
    receipt = _apply(_marker_patch("p1", base, item),
                     project, timeline, store)
    assert receipt["status"] == "committed", receipt
    events = _events(store.path.parent)
    assert len(events) == 1
    assert events[0]["fields"] == {
        "patch_id": "p1",
        "status": "committed",
        "operation_count": 2,
        "generation": base.generation + 1,
    }


def test_a_failed_patch_leaves_an_event_naming_the_verdict(world,
                                                           monkeypatch):
    """An attempt that did not hold is history too.

    Defect caught: only committed patches emit, so a worker whose
    patch failed verification leaves no trace - and the resuming
    worker cannot tell an unattempted patch from a failed one.
    """
    project, timeline, item, store, base = world
    monkeypatch.setattr(type(item), "SetProperty",
                        lambda self, k, v: True)
    receipt = _apply(_patch("p1", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.4}], ["picture_transform"], [[0, 48]]),
        project, timeline, store)
    assert receipt["status"] == "verification_failed"
    events = _events(store.path.parent)
    assert len(events) == 1
    assert events[0]["fields"]["status"] == "verification_failed"
    assert events[0]["fields"]["patch_id"] == "p1"


def test_an_idempotent_retry_emits_nothing_new(world):
    """No double-counted operations: a retry that wrote nothing emits nothing.

    Defect caught: emitting on the idempotent-retry path records the
    same N operations twice, and a reader counts work that ran once.
    """
    project, timeline, item, store, base = world
    first = _apply(_marker_patch("p1", base, item),
                   project, timeline, store)
    assert first["status"] == "committed", first
    second = _apply(_marker_patch("p1", base, item),
                    project, timeline, store)
    assert second["status"] == "committed", second
    assert len(_events(store.path.parent)) == 1


def test_a_log_failure_never_takes_down_a_commit(world, monkeypatch):
    """The patch is the act, the event is its record.

    Defect caught: an emit that raises through `apply_patch` turns a
    logging failure into a refused commit - the same refusal the lease
    layer already refuses to make (`resource_leases._emit`).
    """
    project, timeline, item, store, base = world

    emit = event_log.emit

    def fail_optional_observation(project_folder, event_type, **fields):
        if (event_type == event_log.EDIT_TRANSACTION_STAGE
                and fields.get("stage") == "observed"):
            raise OSError("disk gone")
        return emit(project_folder, event_type, **fields)

    monkeypatch.setattr(event_log, "emit", fail_optional_observation)
    receipt = _apply(_marker_patch("p1", base, item),
                     project, timeline, store)
    assert receipt["status"] == "committed", receipt
