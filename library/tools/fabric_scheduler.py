"""The single-machine Ren Fabric scheduler: place ready tasks on this worker.

The spec's control plane is a scheduler that places tasks on capable
workers: "the scheduler shouldn't think I have five computers; it
should think available capabilities". This module is that scheduler for
ONE machine - it reads a task graph, picks the tasks whose dependencies
are met, checks the local worker manifest meets their capability
requirements, checks their resource leases are free, and runs them.

It builds on the modules that already hold the halves:

* `task_graph` - the record: tasks, dependencies, resources, capabilities,
  and the checkpoint (task states + retry counts).
* `worker_requirements` - the matcher: does this worker's manifest meet
  the task's capability requirements?
* `resource_leases` - the lease layer: is the task's resource set free,
  and does the lease stay alive while the task runs?
* `worker_manifest` - the advertisement: what this worker can do.

The loop
--------
1. Load the checkpoint. A task marked `running` is treated as `failed`
   - the previous scheduler died mid-task, its heartbeat stopped, and
   its lease expired. That is the heartbeat-timeout reassignment: the
   task is reassignable from the last checkpoint.
2. Find ready tasks: dependencies done, not running/done, manifest
   matches, retries left.
3. For each ready task, try to acquire its lease (non-blocking). If the
   lease is free, run the task in a thread. If not, leave it pending -
   a compatible task runs now, a conflicting one waits for the holder.
4. A task that finishes is marked done (exit 0) or failed (non-zero).
   The checkpoint is written after every state change, so a restart
   resumes from the last completed task. A task past its
   `timeout_seconds` is marked timed out instead: its lease is
   released, its attempt is consumed, and the graph continues - a
   task that blocks forever cannot hang the run forever.
5. Repeat until no task is ready or running.

Parallelism is the spec's read-only parallelism: tasks whose resource
declarations are compatible run concurrently; conflicting tasks
serialise through the lease layer. The scheduler itself never runs two
tasks that conflict - the lease check is the gate.

    python -m library.tools.fabric_scheduler run <graph.json> [--state <path>]

`tests/unit/tools/test_fabric_scheduler.py`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

from library.tools import resource_leases, worker_requirements
from library.tools.resource_leases import ResourceLeaseBusy
from library.tools.task_graph import (
    DONE,
    FAILED,
    PENDING,
    RUNNING,
    TIMED_OUT,
    TaskGraph,
    load_state,
    load_task_graph,
    save_state,
)

POLL_SECONDS = 0.1
"""How often the scheduler re-checks ready tasks. A technical cadence,
not a creative value - short enough that a task starting feels instant,
long enough that an idle machine is not spinning."""


class Scheduler:
    """One machine's scheduler loop over one task graph.

    `runner` is the callable that executes a task's command. The default
    runs the command as a subprocess; a test injects a stub to pin the
    loop's behaviour without spawning processes.
    """

    def __init__(self, graph: TaskGraph, manifest: dict,
                 state_path: Path,
                 runner: Optional[Callable] = None,
                 poll_seconds: float = POLL_SECONDS):
        self.graph = graph
        self.manifest = manifest
        self.state_path = Path(state_path)
        self.runner = runner or self._run_command
        self.poll_seconds = poll_seconds
        self._states: Dict[str, str] = {}
        self._attempts: Dict[str, int] = {}
        self._started: Dict[str, float] = {}
        self._epochs: Dict[str, int] = {}
        self._leases: Dict[str, object] = {}
        self._lock = threading.Lock()

    def run(self) -> Dict[str, str]:
        """Run the graph to completion; return the final task states."""
        self._states = load_state(self.state_path)
        self._attempts = self._load_attempts()
        # A task the checkpoint marked running was running when the
        # previous scheduler died. Its heartbeat stopped and its lease
        # expired - the heartbeat-timeout reassignment. It is failed
        # now, and its retries decide whether it runs again.
        for task_id, state in list(self._states.items()):
            if state == RUNNING:
                self._states[task_id] = FAILED
        self._save()

        threads: Dict[str, threading.Thread] = {}

        while True:
            for task in self._ready_now():
                if task.id in threads:
                    continue
                if not worker_requirements.can_perform(
                        self.manifest, task.requirements):
                    with self._lock:
                        self._states[task.id] = FAILED
                        self._attempts[task.id] = \
                            self._attempts.get(task.id, 0) + 1
                    self._save()
                    continue
                started = self._start(task)
                if started is not None:
                    threads[task.id] = started

            for task_id, thread in list(threads.items()):
                if not thread.is_alive():
                    del threads[task_id]

            self._expire_overdue(threads)

            if not threads and not self._ready_now():
                break
            time.sleep(self.poll_seconds)

        return dict(self._states)

    def _expire_overdue(self, threads: Dict[str, threading.Thread]) -> None:
        """Mark past-deadline tasks timed out and release their leases.

        A task that blocks forever must not hang the run forever. Past
        its `timeout_seconds` the task is marked `timed_out`, its
        attempt is consumed, and its lease is released so a compatible
        task - or its own retry, when retries remain - can proceed.
        The stuck thread cannot be killed; its epoch is retired so
        whatever it eventually reports writes nothing. Dependents of
        a timed-out task stay pending: with no DONE dependency they
        are not runnable, and the run fails by its dependency rules.
        """
        now = time.monotonic()
        for task_id in list(threads):
            task = self.graph.tasks[task_id]
            if task.timeout_seconds is None:
                continue
            with self._lock:
                if self._states.get(task_id) != RUNNING:
                    continue
                if now - self._started.get(task_id, now) <= \
                        task.timeout_seconds:
                    continue
                self._epochs[task_id] = self._epochs.get(task_id, 0) + 1
                self._states[task_id] = TIMED_OUT
                self._attempts[task_id] = \
                    self._attempts.get(task_id, 0) + 1
                lease_ctx = self._leases.pop(task_id, None)
            self._save()
            if lease_ctx is not None:
                try:
                    lease_ctx.__exit__(None, None, None)
                except Exception:
                    pass
            basis = f" ({task.timeout_basis})" if task.timeout_basis else ""
            print(f"fabric task {task_id}: timed out after "
                  f"{task.timeout_seconds:g}s{basis} - lease released",
                  file=sys.stderr)
            del threads[task_id]

    def _ready_now(self) -> list:
        """Tasks that are ready to start now: deps done, not running/done,
        retries left (a timed-out task retries like a failed one)."""
        with self._lock:
            ready = []
            for task in self.graph.tasks.values():
                state = self._states.get(task.id, PENDING)
                if state in (RUNNING, DONE):
                    continue
                if state in (FAILED, TIMED_OUT):
                    if self._attempts.get(task.id, 0) >= task.max_retries:
                        continue
                if not all(self._states.get(dep, PENDING) == DONE
                           for dep in task.depends_on):
                    continue
                ready.append(task)
            return ready

    def _start(self, task) -> Optional[threading.Thread]:
        """Acquire the task's lease and run it in a thread.

        Returns the thread where the task was started, None where the
        lease was not free - the task stays pending, so a compatible task
        is not held behind an incompatible one. The task is marked
        `running` HERE, before the thread starts, so the main loop never
        exits while a task is in flight.
        """
        try:
            lease_ctx = resource_leases.resource_lease(
                task.resources, f"fabric task {task.id}", wait=False)
            lease = lease_ctx.__enter__()
        except ResourceLeaseBusy:
            return None
        except Exception:
            with self._lock:
                self._states[task.id] = FAILED
                self._attempts[task.id] = self._attempts.get(task.id, 0) + 1
            self._save()
            return None

        with self._lock:
            self._states[task.id] = RUNNING
            self._epochs[task.id] = self._epochs.get(task.id, 0) + 1
            self._started[task.id] = time.monotonic()
            self._leases[task.id] = lease_ctx
            epoch = self._epochs[task.id]
        self._save()

        def run_with_lease():
            try:
                result = self.runner(task)
                outcome = DONE if result == 0 else FAILED
            except Exception:
                outcome = FAILED
            with self._lock:
                # A thread the deadline retired writes nothing: its
                # lease is released and its attempt consumed, so a
                # late report must not overwrite the timeout - or the
                # retry running in its place.
                if self._epochs.get(task.id) != epoch or \
                        self._states.get(task.id) != RUNNING:
                    return
                self._states[task.id] = outcome
                if outcome == FAILED:
                    self._attempts[task.id] = \
                        self._attempts.get(task.id, 0) + 1
            self._save()
            try:
                lease_ctx.__exit__(None, None, None)
            except Exception:
                pass
            with self._lock:
                self._leases.pop(task.id, None)

        thread = threading.Thread(target=run_with_lease,
                                  name=f"fabric-task-{task.id}", daemon=True)
        thread.start()
        return thread

    def _save(self) -> None:
        save_state(self.state_path, self._states, self._attempts)

    def _load_attempts(self) -> Dict[str, int]:
        try:
            body = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(body, dict):
            return {}
        retries = body.get("retries", {})
        if not isinstance(retries, dict):
            return {}
        return {str(k): int(v) for k, v in retries.items()}

    @staticmethod
    def _run_command(task) -> int:
        """Run the task's command as a subprocess; return its exit code."""
        if not task.command:
            return 0
        try:
            result = subprocess.run(list(task.command),
                                    capture_output=True, encoding="utf-8",
                                    timeout=300, check=False)
            return result.returncode
        except (OSError, subprocess.SubprocessError):
            return 1


def schedule(graph_path: Path, state_path: Optional[Path] = None,
             manifest: Optional[dict] = None,
             runner: Optional[Callable] = None,
             default_timeout: Optional[float] = None) -> Dict[str, str]:
    """Run a task graph on this machine; return the final task states.

    The worker manifest is the live one (`ren worker manifest`) unless a
    caller injects one - a test pins the scheduler against a fixture
    machine, and a cross-machine slice will pass a remote worker's.
    `default_timeout` bounds tasks that declare no deadline of their
    own; a task's own declaration always wins.
    """
    graph = load_task_graph(graph_path)
    if default_timeout is not None:
        graph = with_default_timeout(graph, default_timeout)
    if manifest is None:
        from library.tools.worker_manifest import produce_manifest
        manifest = produce_manifest()
    if state_path is None:
        state_path = Path(graph_path).with_suffix(".state.json")
    scheduler = Scheduler(graph, manifest, state_path, runner=runner)
    return scheduler.run()


def with_default_timeout(graph: TaskGraph,
                         timeout_seconds: float) -> TaskGraph:
    """The graph with a run-level deadline filled into tasks that
    declare none - each filled task records the run flag as its
    source, so a deadline is never a magic number. A task's own
    declaration always wins over the run default.
    """
    if timeout_seconds <= 0:
        raise ValueError(
            f"default timeout must be positive, got {timeout_seconds!r}.")
    import dataclasses
    return TaskGraph(tasks={
        task_id: task if task.timeout_seconds is not None
        else dataclasses.replace(
            task, timeout_seconds=float(timeout_seconds),
            timeout_basis="run --timeout")
        for task_id, task in graph.tasks.items()
    })


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m library.tools.fabric_scheduler",
        description="Run a Ren Fabric task graph on this machine: place "
                    "ready tasks whose capabilities this worker's "
                    "manifest meets and whose leases are free.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a task graph to completion")
    run.add_argument("graph", type=Path, help="a task graph JSON")
    run.add_argument("--state", type=Path, default=None,
                     help="checkpoint path (default: <graph>.state.json)")
    run.add_argument("--timeout", type=float, default=None,
                     metavar="SECONDS",
                     help="deadline for tasks that declare none of their "
                          "own, from this run's declared expected "
                          "duration; a task's own timeout_seconds "
                          "always wins")
    args = parser.parse_args(argv)

    try:
        states = schedule(args.graph, state_path=args.state,
                          default_timeout=args.timeout)
    except Exception as error:
        print(f"fabric scheduler: {error}", file=sys.stderr)
        return 1

    for task_id, state in sorted(states.items()):
        print(f"  {task_id}: {state}")
    failed = [t for t, s in states.items() if s in (FAILED, TIMED_OUT)]
    if failed:
        print(f"fabric scheduler: {len(failed)} task(s) failed: "
              f"{', '.join(sorted(failed))}", file=sys.stderr)
        return 1
    print(f"fabric scheduler: {len(states)} task(s) done")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
