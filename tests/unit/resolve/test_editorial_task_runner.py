"""PLAN-008: what the editorial task runner guarantees while it works.

Each test names a defect it would catch: preparations that pretend to
run at the same time, writes that overlap, a resume that repeats a
verified write, and a run that keeps going past an unproven mutation.

`library/tools/editorial_task_runner.py`.
"""

from __future__ import annotations

import threading

import pytest

from library.tools.editorial_task_graph import EditorialTaskGraph
from library.tools.editorial_task_runner import RunnerRefused, run


def _graph(tasks, **extra):
    record = {"id": "p008", "project": "P", "timeline": "T", "tasks": tasks}
    record.update(extra)
    return EditorialTaskGraph.from_dict(record)


def _write(task_id, op, **extra):
    task = {"id": task_id, "kind": "write", "op": op,
            "operation": {"op": op, "target": {"unique_id": task_id},
                          "params": {}, "expected_effect": "it changed"}}
    task.update(extra)
    return task


def _prepare(task_id, reads=(), **extra):
    task = {"id": task_id, "kind": "prepare", "reads": list(reads)}
    task.update(extra)
    return task


def _verified(name):
    return {"task": name, "verified": True,
            "receipt": {"lifecycle": {"stage": "VERIFIED"}}}


def test_independent_preparations_run_at_the_same_time():
    """Preparations that do not wait for each other overlap in time.

    The barrier is the measurement: three preparations that all wait
    for the other two can only finish if they really ran together.
    """
    barrier = threading.Barrier(3, timeout=10)
    ran = []

    def prepare(task, context):
        ran.append(task.id)
        barrier.wait()
        return {"task": task.id}

    graph = _graph([
        _prepare("prep-a", reads=["captions"]),
        _prepare("prep-b", reads=["fusion"]),
        _prepare("prep-c", reads=["timeline_structure"]),
        _write("trim", "clip.trim", prepared_by=["prep-c"]),
    ])

    def write(task, context):
        return _verified(task.id)

    report = run(graph, prepare=prepare, write=write)
    assert sorted(ran) == ["prep-a", "prep-b", "prep-c"]
    assert report.concurrency["prepare_peak"] == 3


def test_two_writes_never_run_at_the_same_time():
    """One Resolve instance, one writer: the writes are serialized."""
    active = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def write(task, context):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        try:
            return _verified(task.id)
        finally:
            with lock:
                active["now"] -= 1

    graph = _graph([
        _write("trim", "clip.trim"),
        _write("caption", "caption.style"),
        _write("overlay", "overlay.position"),
    ])
    report = run(graph, write=write)
    assert active["peak"] == 1
    assert report.concurrency["write_peak"] == 1
    assert report.verified == ("trim", "caption", "overlay")


def test_a_resumed_run_starts_at_the_last_verified_step(tmp_path):
    """An interrupted run resumes after the last write it verified.

    The first write is not repeated: the AXI's own idempotency would
    refuse it, and a graph that repeated it would be a fragile chain
    of commands again.
    """
    calls = []
    broken = {"now": True}

    def write(task, context):
        calls.append(task.id)
        if task.id == "caption" and broken["now"]:
            broken["now"] = False
            raise RuntimeError("the machine went away mid-run")
        return _verified(task.id)

    graph = _graph([
        _write("trim", "clip.trim"),
        _write("caption", "caption.style"),
        _write("overlay", "overlay.position"),
    ])
    first = run(graph, project_folder=str(tmp_path), write=write)
    assert first.status == "failed"
    assert first.verified == ("trim",)

    calls.clear()
    second = run(graph, project_folder=str(tmp_path), write=write,
                 resume=True)
    assert calls == ["caption", "overlay"]
    assert second.resumed_from == "trim"
    assert second.verified == ("trim", "caption", "overlay")


def test_a_write_that_is_not_verified_stops_the_run():
    """An applied mutation that was not proven is not a done step.

    The run stops there: continuing would write on top of a mutation
    whose outcome nobody established.
    """
    calls = []

    def write(task, context):
        calls.append(task.id)
        if task.id == "caption":
            return {"task": task.id, "verified": False,
                    "receipt": {"lifecycle": {"stage": "APPLIED"}}}
        return _verified(task.id)

    graph = _graph([
        _write("trim", "clip.trim"),
        _write("caption", "caption.style"),
        _write("overlay", "overlay.position"),
    ])
    report = run(graph, write=write)
    assert report.status == "failed"
    assert report.failed == "caption"
    assert calls == ["trim", "caption"]
    assert report.verified == ("trim",)


def test_a_resume_whose_plan_changed_is_refused(tmp_path):
    """A graph that changed under the run is not resumed.

    Continuing would write an order nobody approved, so the run stops
    and says which plan it found.
    """
    def write(task, context):
        return _verified(task.id)

    graph = _graph([_write("trim", "clip.trim")])
    run(graph, project_folder=str(tmp_path), write=write)

    changed = _graph([_write("caption", "caption.style"),
                      _write("trim", "clip.trim")])
    with pytest.raises(RunnerRefused) as refused:
        run(changed, project_folder=str(tmp_path), write=write,
            resume=True)
    assert refused.value.category == "stale"


def test_a_preparation_whose_evidence_a_pending_write_invalidates_waits():
    """A preparation waits for the write that invalidates its evidence.

    The write DECLARES it: the graph never guesses that a preparation
    would go stale, because a guess either blocks every preparation or
    lets evidence rot silently.
    """
    order = []

    def prepare(task, context):
        order.append(f"prepare:{task.id}")
        return {"task": task.id}

    def write(task, context):
        order.append(f"write:{task.id}")
        return _verified(task.id)

    graph = _graph([
        _prepare("prep-late", reads=["timeline_structure"]),
        _write("trim", "clip.trim", invalidates=["prep-late"]),
    ])
    run(graph, prepare=prepare, write=write)
    assert order == ["write:trim", "prepare:prep-late"]


def test_a_preparation_that_feeds_the_pending_write_may_run_first():
    """The preparation of a write is never blocked by that write."""
    order = []

    def prepare(task, context):
        order.append(f"prepare:{task.id}")
        return {"task": task.id}

    def write(task, context):
        order.append(f"write:{task.id}")
        return _verified(task.id)

    graph = _graph([
        _prepare("prep-trim", reads=["timeline_structure"]),
        _write("trim", "clip.trim", prepared_by=["prep-trim"]),
    ])
    run(graph, prepare=prepare, write=write)
    assert order == ["prepare:prep-trim", "write:trim"]


def test_an_unmet_precondition_blocks_the_write_and_is_named(tmp_path):
    """A write whose evidence nobody provided does not run."""
    calls = []

    def write(task, context):
        calls.append(task.id)
        return _verified(task.id)

    graph = _graph([
        _write("caption", "caption.style", depends_on=[
            {"kind": "evidence", "provides": "evidence:text"}]),
    ])
    report = run(graph, project_folder=str(tmp_path), write=write)
    assert report.status == "blocked"
    assert calls == []
    assert report.blocked[0]["provides"] == "evidence:text"


def test_a_preparation_provides_the_evidence_a_write_waits_for():
    """The evidence a preparation produced satisfies the write."""
    def prepare(task, context):
        return {"task": task.id, "produced": {"text": "hello"}}

    def write(task, context):
        return _verified(task.id)

    graph = _graph([
        _prepare("prep-caption", reads=["captions"],
                 provides=["evidence:text"]),
        _write("caption", "caption.style", prepared_by=["prep-caption"],
               depends_on=[{"kind": "evidence",
                            "provides": "evidence:text"}]),
    ])
    report = run(graph, prepare=prepare, write=write)
    assert report.status == "completed"
    assert report.verified == ("caption",)
