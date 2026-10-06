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
    TIMED_OUT,
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


# ── Deadlines ───────────────────────────────────────────────────────


class _Blocker:
    """A runner that blocks until released; one task that never finishes."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, task) -> int:
        with self._lock:
            self.calls += 1
        self.entered.set()
        self.release.wait(timeout=30)
        return 0


def test_a_task_that_blocks_forever_is_timed_out(lock_dir, tmp_path):
    """A task past its deadline is marked timed out and the run ends.

    Defect caught: the loop had no deadline, so one task that blocks
    forever hangs the whole run forever - the graph never completes
    and no checkpoint ever says why.
    """
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", timeout_seconds=0.2,
                  timeout_basis="declared"),
    })
    runner = _Blocker()
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner).run()

    assert runner.entered.is_set()
    assert states == {"a": TIMED_OUT}


def test_a_timed_out_task_releases_its_lease(lock_dir, tmp_path):
    """A task waiting on the expired task's lease proceeds after the
    expiry - the lease is released at the deadline, not when the
    stuck thread eventually returns.

    Defect caught: a lease held until thread return serializes every
    conflicting task behind a task that never returns.
    """
    exclusive = LeaseDeclaration(
        exclusive=frozenset({ResourceRef("clip", "X")}))
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", resources=exclusive,
                  timeout_seconds=0.2, timeout_basis="declared"),
        "b": Task(id="b", name="B", resources=exclusive),
    })
    blocker = _Blocker()
    ran_b = threading.Event()

    def runner(task):
        if task.id == "a":
            return blocker(task)
        ran_b.set()
        return 0

    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner).run()

    assert ran_b.wait(timeout=10)
    assert states == {"a": TIMED_OUT, "b": DONE}


def test_a_dependent_of_a_timed_out_task_stays_pending(lock_dir, tmp_path):
    """A task whose dependency timed out never becomes runnable: with
    no DONE dependency the graph cannot place it, so it stays pending
    and the run reports the timeout as the failure.

    Defect caught: a loop that treats any terminal state as runnable
    places a task whose inputs were never produced.
    """
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", timeout_seconds=0.2,
                  timeout_basis="declared"),
        "b": Task(id="b", name="B", depends_on=("a",)),
    })
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        _Blocker()).run()

    assert states["a"] == TIMED_OUT
    assert states.get("b", PENDING) == PENDING


def test_a_timed_out_task_with_retries_runs_again(lock_dir, tmp_path):
    """A timed-out task that still has retries left is reassigned like
    a failed one - the expiry consumes one attempt, and the retry runs
    while the stuck thread's late report writes nothing."""
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", max_retries=2, timeout_seconds=0.2,
                  timeout_basis="declared"),
    })
    blocker = _Blocker()
    calls = []

    def runner(task):
        calls.append(task.id)
        if len(calls) == 1:
            return blocker(task)
        return 0

    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        runner).run()

    assert states == {"a": DONE}
    assert len(calls) == 2
    blocker.release.set()


def test_a_stuck_threads_late_report_writes_nothing(lock_dir, tmp_path):
    """A thread the deadline retired cannot overwrite the timeout when
    it eventually returns: its epoch is spent, so the checkpoint keeps
    saying timed out."""
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", timeout_seconds=0.2,
                  timeout_basis="declared"),
    })
    state_path = tmp_path / "state.json"
    blocker = _Blocker()
    states = _scheduler(graph, _manifest(), state_path, blocker).run()
    assert states == {"a": TIMED_OUT}

    blocker.release.set()
    time.sleep(0.3)
    assert load_state(state_path) == {"a": TIMED_OUT}


def test_a_run_flag_bounds_tasks_that_declare_nothing(lock_dir, tmp_path):
    """`with_default_timeout` fills a deadline into tasks that declare
    none - recording the run flag as its source - and never overrides
    a task's own declaration."""
    from library.tools.fabric_scheduler import with_default_timeout

    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A"),
        "b": Task(id="b", name="B", timeout_seconds=60.0,
                  timeout_basis="declared"),
    })
    bounded = with_default_timeout(graph, 5.0)

    assert bounded.tasks["a"].timeout_seconds == 5.0
    assert bounded.tasks["a"].timeout_basis == "run --timeout"
    assert bounded.tasks["b"].timeout_seconds == 60.0
    assert bounded.tasks["b"].timeout_basis == "declared"


def test_a_non_positive_run_flag_is_refused(lock_dir, tmp_path):
    """A run flag of zero or less refuses rather than bounding every
    task at a deadline already past."""
    from library.tools.fabric_scheduler import with_default_timeout

    with pytest.raises(ValueError, match="must be positive"):
        with_default_timeout(TaskGraph(tasks={}), 0)
# ── Lease declarations ────────────────────────────────────────────

def test_a_task_lease_carries_the_tasks_heartbeat_interval(
        lock_dir, tmp_path, monkeypatch):
    """The lease a task holds heartbeats on the task's own interval.

    Defect caught: the scheduler dropped the declaration and every
    task lease got the default, so a task that declared a short
    interval for fast failover never got it - a dead holder's lease
    lingered a full default interval before another worker could
    take it.
    """
    from library.tools import resource_leases

    seen = {}
    real_lease = resource_leases.resource_lease

    def recording_lease(declaration, purpose, **kwargs):
        seen.update(kwargs)
        return real_lease(declaration, purpose, **kwargs)

    monkeypatch.setattr(resource_leases, "resource_lease", recording_lease)
    graph = TaskGraph(tasks={
        "a": Task(id="a", name="A", heartbeat_interval=7.5),
    })
    states = _scheduler(graph, _manifest(), tmp_path / "state.json",
                        Recorder()).run()

    assert states == {"a": DONE}
    assert seen.get("heartbeat_interval") == 7.5


# ── Verb dispatch ─────────────────────────────────────────────────

def _write_graph_file(path, *tasks):
    import json
    path.write_text(json.dumps(
        {"tasks": [task.to_record() for task in tasks]}, indent=2),
        encoding="utf-8")
    return path


def _stub_manifest(monkeypatch):
    from library.tools import worker_manifest
    monkeypatch.setattr(
        worker_manifest, "produce_manifest",
        lambda *args, **kwargs: {"capabilities": {}})


def test_a_bare_graph_path_runs(lock_dir, tmp_path, monkeypatch, capsys):
    """`ren schedule <graph.json>` runs the graph: the `run`
    subcommand is the only one, so a bare graph path means it.

    Defect caught: the module required the subcommand while the verb
    passes the graph path straight through, so the shipped entry
    point exited 2 on exactly the invocation it advertises.
    """
    from library.tools import fabric_scheduler

    _stub_manifest(monkeypatch)
    graph_path = _write_graph_file(
        tmp_path / "graph.json", Task(id="a", name="A"))
    assert fabric_scheduler._main([str(graph_path)]) == 0
    assert "a: done" in capsys.readouterr().out


def test_an_explicit_run_subcommand_still_runs(
        lock_dir, tmp_path, monkeypatch, capsys):
    """The explicit `run` form keeps working beside the bare path."""
    from library.tools import fabric_scheduler

    _stub_manifest(monkeypatch)
    graph_path = _write_graph_file(
        tmp_path / "graph.json", Task(id="a", name="A"))
    assert fabric_scheduler._main(["run", str(graph_path)]) == 0
    assert "a: done" in capsys.readouterr().out
