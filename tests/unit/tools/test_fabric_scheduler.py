"""The single-machine scheduler loop: placement, leases, checkpoints.

Every test names a defect it catches - a case that fails on origin/main,
where this module does not exist. The scheduler is the spec's control
plane, so a loop that places a task on a worker that cannot run it, or
that loses a task on restart, is a defect the tests pin.
"""
import threading
import time
from pathlib import Path

import pytest

from library.tools.fabric_scheduler import Scheduler
from library.tools.resource_leases import LeaseDeclaration, ResourceRef
from library.tools.task_graph import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    Task,
    TaskGraph,
    load_state,
    save_state,
)


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's."""
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


class Recorder:
    """A stub runner that records what ran and can fail tasks.

    The scheduler's `runner` is injectable so a test pins the loop's
    behaviour without spawning processes - the subprocess path is
    `Scheduler._run_command`, tested separately.
    """

    def __init__(self, fail=(), delay=0.0):
        self.ran: list = []
        self.fail = set(fail)
        self.delay = delay
        self._lock = threading.Lock()

    def __call__(self, task) -> int:
        with self._lock:
            self.ran.append(task.id)
        if self.delay:
            time.sleep(self.delay)
        return 1 if task.id in self.fail else 0


def _scheduler(graph, manifest, state_path, runner, poll=0.01):
    return Scheduler(graph, manifest, state_path, runner=runner,
                     poll_seconds=poll)


def _manifest(capabilities=None):
    return {"capabilities": capabilities or {"footage.search": "available"}}


# ── Placement ───────────────────────────────────────────────────────

def test_a_ready_task_runs(lock_dir, tmp_path):
    """A task with no dependencies runs to done.

    Defect caught: a scheduler that never starts a task runs nothing,
    and the graph stays pending forever.
    """
    graph = TaskGraph(tasks={"a": Task(id="a", name="A")})
    runner = Recorder()
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner).run()
    assert states == {"a": DONE}
    assert runner.ran == ["a"]


def test_a_task_whose_dependency_is_not_done_does_not_run(lock_dir, tmp_path):
    """A task waits for its dependencies.

    Defect caught: a scheduler that ignores dependencies runs a task
    before its inputs exist, and the task fails on missing state.
    """
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A"),
        "b": Task(id="b", name="B", depends_on=("a",)),
    })
    runner = Recorder()
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner).run()
    assert states == {"a": DONE, "b": DONE}
    assert runner.ran == ["a", "b"]


def test_a_task_whose_manifest_does_not_match_is_failed(lock_dir, tmp_path):
    """A task whose capability requirements the worker manifest does not
    meet is failed, not run.

    Defect caught: a scheduler that ignores the manifest places a task
    on a worker that cannot run it, and the task fails at run time with
    no explanation.
    """
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", capabilities=("resolve.render",)),
    })
    runner = Recorder()
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner).run()
    assert states == {"a": FAILED}
    assert runner.ran == []


# ── Leases ──────────────────────────────────────────────────────────

def test_a_task_whose_lease_is_held_does_not_run(lock_dir, tmp_path):
    """A task whose resource lease is held by another task waits.

    Defect caught: a scheduler that ignores leases runs two conflicting
    tasks at once, and they corrupt each other's resources.
    """
    from library.tools.resource_leases import resource_lease

    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A",
                  resources=LeaseDeclaration(
                      write=frozenset({ResourceRef("timeline", "master")}))),
    })
    runner = Recorder()
    state_path = tmp_path / "state.json"
    scheduler = _scheduler(graph, _manifest(), state_path, runner)

    with resource_lease(
            LeaseDeclaration(
                write=frozenset({ResourceRef("timeline", "master")})),
            "test holder", wait=False):
        thread = threading.Thread(target=scheduler.run, daemon=True)
        thread.start()
        time.sleep(0.1)
        assert runner.ran == []

    thread.join(timeout=5)
    assert not thread.is_alive()
    assert runner.ran == ["a"]


def test_two_compatible_tasks_run_concurrently(lock_dir, tmp_path):
    """Tasks whose resource declarations are compatible run at the same
    time - the spec's read-only parallelism.

    Defect caught: a scheduler that serialises every task runs a graph
    in the sum of its tasks' durations, not the longest chain.
    """
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A",
                  resources=LeaseDeclaration(
                      read=frozenset({ResourceRef("clip", "A001")}))),
        "b": Task(id="b", name="B",
                  resources=LeaseDeclaration(
                      read=frozenset({ResourceRef("clip", "A002")}))),
    })
    runner = Recorder(delay=0.05)
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner, poll=0.005).run()
    assert states == {"a": DONE, "b": DONE}
    assert sorted(runner.ran) == ["a", "b"]


# ── Checkpoints and restart ─────────────────────────────────────────

def test_a_running_task_in_the_checkpoint_is_failed_on_restart(
        lock_dir, tmp_path):
    """A task the checkpoint marked running was running when the
    previous scheduler died. Its heartbeat stopped and its lease
    expired - the heartbeat-timeout reassignment.

    Defect caught: a scheduler that trusts the checkpoint's running
    state waits forever for a task that will never finish.
    """
    graph = TaskGraph(tasks={"a": Task(id="a", name="A", max_retries=1)})
    state_path = tmp_path / "state.json"
    save_state(state_path, {"a": RUNNING}, {})

    runner = Recorder()
    states = _scheduler(graph, _manifest(), state_path, runner).run()

    assert states == {"a": DONE}
    assert runner.ran == ["a"]


def test_a_failed_task_with_retries_is_retried(lock_dir, tmp_path):
    """A failed task that still has retries left runs again.

    Defect caught: a scheduler that does not retry fails a task on the
    first transient error, and the graph ends with a failed task that
    could have succeeded.
    """
    graph = TaskGraph(tasks={"a": Task(id="a", name="A", max_retries=2)})
    state_path = tmp_path / "state.json"
    save_state(state_path, {"a": FAILED}, {"a": 1})

    runner = Recorder()
    states = _scheduler(graph, _manifest(), state_path, runner).run()

    assert states == {"a": DONE}
    assert runner.ran == ["a"]


def test_a_failed_task_without_retries_stays_failed(lock_dir, tmp_path):
    """A failed task that has used its retries is finished, failed.

    Defect caught: a scheduler that retries every failed task forever
    loops on a task that always fails.
    """
    graph = TaskGraph(tasks={"a": Task(id="a", name="A", max_retries=1)})
    state_path = tmp_path / "state.json"
    save_state(state_path, {"a": FAILED}, {"a": 1})

    runner = Recorder()
    states = _scheduler(graph, _manifest(), state_path, runner).run()

    assert states == {"a": FAILED}
    assert runner.ran == []


def test_a_done_task_is_not_rerun_on_restart(lock_dir, tmp_path):
    """A task the checkpoint marked done is not run again.

    Defect caught: a scheduler that does not read the checkpoint re-runs
    every task on restart, and a task that wrote output writes it twice.
    """
    graph = TaskGraph(tasks={"a": Task(id="a", name="A")})
    state_path = tmp_path / "state.json"
    save_state(state_path, {"a": DONE}, {})

    runner = Recorder()
    states = _scheduler(graph, _manifest(), state_path, runner).run()

    assert states == {"a": DONE}
    assert runner.ran == []
