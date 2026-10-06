"""The task graph record: validation, ready-task selection, checkpoints.

Every test names a defect it catches - a case that fails on origin/main,
where this module does not exist. The graph is the scheduler's input,
so a malformed graph that runs is a scheduler that places tasks that
cannot run.
"""
import json
from pathlib import Path

import pytest

from library.tools.task_graph import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    GraphError,
    Task,
    TaskGraph,
    load_state,
    load_task_graph,
    save_state,
)


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph(tasks={task.id: task for task in tasks})


def _write_graph(path: Path, *tasks: Task) -> Path:
    path.write_text(json.dumps(
        {"tasks": [task.to_record() for task in tasks]}, indent=2),
        encoding="utf-8")
    return path


# ── Validation ──────────────────────────────────────────────────────

def test_a_cycle_is_refused():
    """A task that depends on itself, directly or through others, can
    never become ready - the scheduler would wait forever.

    Defect caught: a graph loader that does not check for cycles runs
    forever on a cyclic graph, because no task in the cycle ever has
    its dependencies met.
    """
    with pytest.raises(GraphError, match="cycle"):
        TaskGraph(tasks={
            "a": Task(id="a", name="A", depends_on=("b",)),
            "b": Task(id="b", name="B", depends_on=("a",)),
        })
    with pytest.raises(GraphError, match="cycle"):
        TaskGraph(tasks={
            "a": Task(id="a", name="A", depends_on=("b",)),
            "b": Task(id="b", name="B", depends_on=("c",)),
            "c": Task(id="c", name="C", depends_on=("a",)),
        })


def test_an_unknown_dependency_is_refused():
    """A task that depends on a task not in the graph can never become
    ready - the dependency is never met.

    Defect caught: a loader that does not check dependency targets
    accepts a graph where a task waits forever for a task that does not
    exist.
    """
    with pytest.raises(GraphError, match="not in the graph"):
        TaskGraph(tasks={
            "a": Task(id="a", name="A", depends_on=("ghost",)),
        })


def test_a_duplicate_id_is_refused():
    """Two tasks with one id are one task the scheduler cannot place
    twice - the second overwrites the first in the dict.

    Defect caught: a loader that does not check for duplicate ids
    silently drops a task, and the scheduler runs the graph without it.
    """
    with pytest.raises(GraphError, match="duplicate"):
        TaskGraph.from_record({"tasks": [
            {"id": "a", "name": "First"},
            {"id": "a", "name": "Second"},
        ]})


# ── Ready-task selection ────────────────────────────────────────────

def test_a_task_with_no_dependencies_is_ready():
    """A task with no dependencies is ready from the start.

    Defect caught: a ready check that requires all tasks to have
    dependencies refuses to run any task in a graph.
    """
    graph = _graph(Task(id="a", name="A"))
    assert [t.id for t in graph.ready({})] == ["a"]


def test_a_task_whose_dependency_is_not_done_is_not_ready():
    """A task waits for its dependencies.

    Defect caught: a ready check that ignores dependencies runs a task
    before its inputs exist, and the task fails on missing state.
    """
    graph = _graph(
        Task(id="a", name="A"),
        Task(id="b", name="B", depends_on=("a",)),
    )
    assert [t.id for t in graph.ready({})] == ["a"]
    assert [t.id for t in graph.ready({"a": DONE})] == ["b"]


def test_a_running_task_is_not_ready_again():
    """A task that is already running must not be started twice.

    Defect caught: a ready check that does not exclude running tasks
    starts the same task twice, and two workers write the same output.
    """
    graph = _graph(Task(id="a", name="A"))
    assert graph.ready({"a": RUNNING}) == []


def test_a_failed_task_with_retries_is_ready_again():
    """A failed task that still has retries left is ready - the scheduler
    reassigns it.

    Defect caught: a ready check that excludes all failed tasks never
    retries, and a transient failure (a locked file, a busy port)
    becomes permanent.
    """
    graph = _graph(Task(id="a", name="A", max_retries=2))
    states = {"a": FAILED}
    attempts = {"a": 1}
    assert [t.id for t in graph.ready(states)] == []
    assert [t.id for t in graph.retryable(states, attempts)] == ["a"]


def test_a_failed_task_without_retries_is_not_ready():
    """A failed task that has used its retries is finished, failed.

    Defect caught: a ready check that retries every failed task forever
    loops on a task that always fails.
    """
    graph = _graph(Task(id="a", name="A", max_retries=1))
    states = {"a": FAILED}
    attempts = {"a": 1}
    assert graph.retryable(states, attempts) == []


# ── Checkpoints ─────────────────────────────────────────────────────

def test_a_checkpoint_round_trips_states_and_attempts():
    """The checkpoint records task states and retry counts, so a restart
    resumes from the last completed task.

    Defect caught: a checkpoint that does not record retries loses the
    retry count on restart, and a task that failed once runs forever.
    """
    path = Path("/tmp/test_fabric_checkpoint.json")
    save_state(path, {"a": DONE, "b": FAILED}, {"b": 2})
    assert load_state(path) == {"a": DONE, "b": FAILED}
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["retries"] == {"b": 2}
    path.unlink(missing_ok=True)


def test_a_missing_checkpoint_is_empty():
    """A graph that has never run has no checkpoint - every task is
    pending.

    Defect caught: a loader that requires a checkpoint file refuses to
    run a graph for the first time.
    """
    assert load_state(Path("/tmp/test_fabric_no_such_file.json")) == {}


# ── Loading ─────────────────────────────────────────────────────────

def test_a_graph_with_no_tasks_is_valid():
    """An empty graph is valid - it runs zero tasks and succeeds.

    Defect caught: a loader that requires at least one task refuses an
    empty graph, which is what a caller builds before adding tasks.
    """
    graph = TaskGraph.from_record({"tasks": []})
    assert graph.ready({}) == []
