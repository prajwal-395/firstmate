"""PLAN-008: what the editorial task graph guarantees about an assignment.

Each test names a defect it would catch: a graph that writes in an
order nobody asked for, a graph that reports a dependency it does not
have, a graph whose preparations cannot run at the same time.

`library/tools/editorial_task_graph.py`.
"""

from __future__ import annotations

import pytest

from library.tools import editorial_task_graph as graph_module
from library.tools.editorial_task_graph import (
    EditorialTaskGraph,
    GraphRefused,
)


def _graph(tasks, **extra):
    record = {"id": "p008", "tasks": tasks}
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


def test_a_ripple_waits_for_the_delete_whose_gap_it_closes():
    """The engine's declared dependency orders two tasks, not two ops.

    The compound rules own the order INSIDE one request. Between two
    requests the same table applies, or a ripple closes a gap that is
    still there.
    """
    graph = _graph([
        _write("ripple", "clip.ripple"),
        _write("delete", "clip.delete"),
    ])
    assert graph.write_order() == ("delete", "ripple")


def test_a_declared_order_that_contradicts_the_engine_dependency_is_refused():
    """A graph that declares BOTH directions is refused, not obeyed.

    The compound rules refuse this inside a request; between requests
    it is the same contradiction, and the engine does not choose an
    order nobody asked for. Declaring one direction is not a
    contradiction - the engine honours it and reorders if it must.
    """
    graph = _graph([
        _write("delete", "clip.delete",
               depends_on=[{"kind": "edit", "task": "ripple"}]),
        _write("ripple", "clip.ripple"),
    ])
    with pytest.raises(GraphRefused) as refused:
        graph.write_order()
    assert refused.value.category == "conflict"
    assert "ripple" in str(refused.value)


def test_a_write_that_depends_on_another_task_waits_for_it():
    """An 'edit' dependency orders the two tasks it names."""
    graph = _graph([
        _write("caption", "caption.style",
               depends_on=[{"kind": "edit", "task": "trim"}]),
        _write("trim", "clip.trim"),
    ])
    assert graph.write_order() == ("trim", "caption")


def test_a_task_that_waits_for_itself_is_refused():
    """A cycle is refused at load time, not half way through a run."""
    with pytest.raises(GraphRefused) as refused:
        _graph([
            _write("a", "clip.trim",
                   depends_on=[{"kind": "edit", "task": "b"}]),
            _write("b", "clip.trim",
                   depends_on=[{"kind": "edit", "task": "a"}]),
        ])
    assert "cycle" in str(refused.value)


def test_a_write_with_no_operation_is_refused():
    """A write that asks for nothing would change nothing, silently."""
    with pytest.raises(GraphRefused):
        _graph([{"id": "trim", "kind": "write", "op": "clip.trim"}])


def test_the_four_dependency_kinds_are_recorded_by_name():
    """The punchlist's four dependencies are the graph's four kinds."""
    graph = _graph([
        _write("caption", "caption.style", depends_on=[
            {"kind": "evidence", "provides": "evidence:text"},
            {"kind": "effect", "provides": "effect:card-1"},
            {"kind": "approval", "provides": "approval:captain"},
            {"kind": "edit", "task": "trim"},
        ]),
        _write("trim", "clip.trim"),
    ])
    kinds = {dep.kind for dep in graph.plan().dependencies["caption"]}
    assert kinds == {"evidence", "effect", "approval", "edit"}


def test_an_unmet_precondition_is_reported_by_name():
    """A precondition nobody satisfied is named, never assumed."""
    graph = _graph([
        _write("caption", "caption.style", depends_on=[
            {"kind": "evidence", "provides": "evidence:text"}]),
    ])
    unmet = graph.unmet_preconditions()
    assert unmet == [{"task": "caption", "kind": "evidence",
                      "provides": "evidence:text", "note": ""}]


def test_a_precondition_the_graph_declares_is_not_unmet():
    """A satisfied precondition is not reported as missing."""
    graph = _graph([
        _write("caption", "caption.style", depends_on=[
            {"kind": "approval", "provides": "approval:captain"}]),
    ], provides={"approval:captain": {"status": "satisfied"}})
    assert graph.unmet_preconditions() == []


def test_independent_preparations_land_in_one_wave():
    """Preparations that do not wait for each other may run together."""
    graph = _graph([
        {"id": "prep-a", "kind": "prepare", "reads": ["captions"]},
        {"id": "prep-b", "kind": "prepare", "reads": ["fusion"]},
        {"id": "prep-c", "kind": "prepare", "reads": ["timeline_structure"],
         "prepared_by": []},
        _write("caption", "caption.style", prepared_by=["prep-a"]),
        _write("overlay", "overlay.position", prepared_by=["prep-b"]),
        _write("trim", "clip.trim", prepared_by=["prep-c"]),
    ])
    plan = graph.plan()
    assert plan.prepare_waves == (("prep-a", "prep-b", "prep-c"),)


def test_a_preparation_that_waits_for_an_edit_lands_in_a_later_wave():
    """A preparation that depends on a write cannot be in the first wave."""
    graph = _graph([
        {"id": "prep-after", "kind": "prepare", "reads": ["captions"],
         "depends_on": [{"kind": "edit", "task": "trim"}]},
        _write("trim", "clip.trim"),
    ])
    plan = graph.plan()
    assert plan.prepare_waves == (("prep-after",),)


def test_a_dependency_on_a_task_that_is_not_in_the_graph_is_refused():
    """A dependency on nothing is a task that can never run."""
    with pytest.raises(GraphRefused):
        _graph([
            _write("caption", "caption.style",
                   depends_on=[{"kind": "edit", "task": "nowhere"}]),
        ])


def test_a_dependency_kind_that_is_not_one_of_the_four_is_refused():
    """The vocabulary is closed: an unknown kind is not silently ignored."""
    with pytest.raises(GraphRefused):
        _graph([
            _write("caption", "caption.style",
                   depends_on=[{"kind": "vibes", "provides": "x"}]),
        ])


def test_the_engine_dependencies_are_the_compound_ones():
    """The graph reads the compound rules' table, not a copy of it."""
    from library.tools import compound_transaction

    assert compound_transaction.ENGINE_DEPENDENCIES
    graph = _graph([
        _write("resize", "overlay.resize"),
        _write("position", "overlay.position"),
    ])
    assert graph.write_order() == ("position", "resize")


def test_a_graph_names_the_domains_its_writes_touch():
    """The domains come from the algebra, so the graph does not guess."""
    graph = _graph([
        _write("trim", "clip.trim"),
        _write("caption", "caption.style"),
    ])
    assert graph.tasks["trim"].domains == ("timeline_structure",)
    assert graph.tasks["caption"].domains == ("captions",)


def test_a_write_that_invalidates_the_preparation_that_feeds_it_is_refused():
    """A preparation cannot wait for the write it feeds.

    The write needs the preparation's evidence, and the write would
    invalidate it: the graph would wait forever, so it refuses at load
    time.
    """
    with pytest.raises(GraphRefused) as refused:
        _graph([
            _prepare("prep-a"),
            _write("trim", "clip.trim", prepared_by=["prep-a"],
                   invalidates=["prep-a"]),
        ])
    assert "cycle" in str(refused.value)


def test_an_invalidated_preparation_lands_in_a_later_wave():
    """A preparation whose evidence a write invalidates waits for it."""
    graph = _graph([
        _prepare("prep-late"),
        _write("trim", "clip.trim", invalidates=["prep-late"]),
    ])
    plan = graph.plan()
    assert plan.prepare_waves == (("prep-late",),)
    assert plan.write_order == ("trim",)


def test_a_graph_with_no_tasks_is_refused():
    """A graph with nothing in it is not an assignment."""
    with pytest.raises(GraphRefused):
        _graph([])


def test_a_graph_round_trips_through_its_record():
    """The record is JSON on disk, so it must survive the round trip."""
    graph = _graph([
        _write("trim", "clip.trim"),
        {"id": "prep", "kind": "prepare", "reads": ["captions"]},
    ], project="P", timeline="T")
    again = EditorialTaskGraph.from_dict(graph.as_dict())
    assert again.as_dict() == graph.as_dict()


def test_a_graph_that_is_not_json_is_refused_with_a_reason():
    """A malformed record says what is wrong with it."""
    import json

    with pytest.raises(GraphRefused) as refused:
        graph_module.load_graph(_write_file("not json"))
    assert "not valid JSON" in str(refused.value)
    assert json.dumps(refused.value.as_dict())


def _write_file(text: str):
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "graph.json"
    path.write_text(text, encoding="utf-8")
    return path
