"""The Ren Fabric task graph: tasks, dependencies, resources, capabilities.

The spec's control plane is a task/dependency graph: each task declares
what it depends on, what capabilities it requires, and what resources it
will touch (READ / WRITE / EXCLUSIVE). The scheduler reads this record
and places tasks on workers whose manifests meet the requirements and
whose leases are free.

This module is the record itself - the data model and its validation.
It builds on `resource_leases` for the resource declarations (the
`LeaseDeclaration` a task carries IS a lease declaration, so the
conflict check and the hierarchy are the scheduler's, not a parallel
copy). It knows nothing about workers, manifests or scheduling - those
are `worker_requirements` and `fabric_scheduler`.

The record is JSON on disk, one file per graph:

    {
      "tasks": [
        {
          "id": "captions",
          "name": "Create captions",
          "depends_on": ["transcript"],
          "capabilities": ["resolve.edit"],
          "resources": {
            "read": ["timeline:master"],
            "write": [],
            "exclusive": ["track:master:caption:1"]
          },
          "command": ["python3", "-m", "library.tools.subtitle_render"],
          "heartbeat_interval": 30.0,
          "max_retries": 2
        }
      ]
    }

`command` is what the scheduler runs for the task - a subprocess argv.
`heartbeat_interval` is the task's own declaration of how often it will
heartbeat (the source of the lease expiry, per `resource_leases`).
`max_retries` is the task's own declaration of how many times it may be
reassigned after a failure. A task that declares none gets the graph's
default, which is 0 - a task that cannot say how many times it may
retry is not a task that retries.

    python -m library.tools.task_graph validate <graph.json>
    python -m library.tools.task_graph ready <graph.json> <state.json>

`tests/unit/tools/test_task_graph.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from library.tools.resource_leases import (
    DEFAULT_HEARTBEAT_INTERVAL,
    LeaseDeclaration,
)

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
STATES = (PENDING, RUNNING, DONE, FAILED)


@dataclass(frozen=True)
class Task:
    """One node in the task graph.

    `capabilities` are the capability ids the task requires the worker
    to have - matched against the worker manifest by
    `worker_requirements`. `resources` is the lease declaration the
    task will hold while it runs - checked against standing leases by
    `resource_leases` before the task starts.
    """

    id: str
    name: str
    depends_on: Tuple[str, ...] = ()
    capabilities: Tuple[str, ...] = ()
    resources: LeaseDeclaration = field(
        default_factory=LeaseDeclaration)
    command: Tuple[str, ...] = ()
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL
    max_retries: int = 0

    def __post_init__(self):
        if not self.id or not str(self.id).strip():
            raise ValueError("a task needs an id - an unnamed task is "
                             "unreachable by its dependents")
        if self.heartbeat_interval <= 0:
            raise ValueError(
                f"task {self.id!r}: heartbeat_interval must be positive, "
                f"got {self.heartbeat_interval!r} - a lease that expires "
                f"instantly excludes its own holder.")
        if self.max_retries < 0:
            raise ValueError(
                f"task {self.id!r}: max_retries must be >= 0, got "
                f"{self.max_retries!r}.")

    @classmethod
    def from_record(cls, record: dict) -> "Task":
        resources = LeaseDeclaration.from_record(record.get("resources", {}))
        return cls(
            id=str(record["id"]),
            name=str(record.get("name", record["id"])),
            depends_on=tuple(str(d) for d in record.get("depends_on", ())),
            capabilities=tuple(str(c) for c in
                               record.get("capabilities", ())),
            resources=resources,
            command=tuple(str(c) for c in record.get("command", ())),
            heartbeat_interval=float(record.get("heartbeat_interval",
                                                DEFAULT_HEARTBEAT_INTERVAL)),
            max_retries=int(record.get("max_retries", 0)),
        )

    def to_record(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "depends_on": list(self.depends_on),
            "capabilities": list(self.capabilities),
            "resources": self.resources.to_record(),
            "command": list(self.command),
            "heartbeat_interval": self.heartbeat_interval,
            "max_retries": self.max_retries,
        }

    @property
    def requirements(self):
        """This task as the merged matcher's `TaskRequirements` - the
        scheduler gates on `worker_requirements.can_perform`, never on
        the raw capability tuple, so one matcher decides every refusal."""
        from library.tools.worker_requirements import TaskRequirements
        return TaskRequirements(capabilities=tuple(self.capabilities))


class GraphError(ValueError):
    """The task graph is malformed - a cycle, an unknown dependency, or
    a duplicate id. The scheduler refuses to run it."""


@dataclass
class TaskGraph:
    """The task graph: every task by id, and the edges between them."""

    tasks: Dict[str, Task]

    def __post_init__(self):
        for task in self.tasks.values():
            for dep in task.depends_on:
                if dep not in self.tasks:
                    raise GraphError(
                        f"task {task.id!r} depends on {dep!r}, which is "
                        f"not in the graph - a dependency on nothing is "
                        f"a task that can never become ready")
        self._check_cycles()

    def _check_cycles(self) -> None:
        """Depth-first cycle detection over the dependency edges."""
        visiting: set = set()
        visited: set = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise GraphError(
                    f"the graph has a cycle through {task_id!r} - a "
                    f"task that depends on itself, directly or through "
                    f"others, can never become ready")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dep in self.tasks[task_id].depends_on:
                visit(dep)
            visiting.discard(task_id)
            visited.add(task_id)

        for task_id in self.tasks:
            visit(task_id)

    def ready(self, states: Dict[str, str]) -> List[Task]:
        """Tasks whose dependencies are all done and that are not running.

        A task that failed and has retries left is ready again - the
        scheduler reassigns it. A task that failed with no retries left
        is not ready; it is finished, failed.
        """
        result = []
        for task in self.tasks.values():
            state = states.get(task.id, PENDING)
            if state == RUNNING or state == DONE:
                continue
            if state == FAILED:
                # A failed task is ready only if it has retries left;
                # the scheduler counts attempts and decides.
                continue
            if all(states.get(dep, PENDING) == DONE
                   for dep in task.depends_on):
                result.append(task)
        return result

    def retryable(self, states: Dict[str, str],
                  attempts: Dict[str, int]) -> List[Task]:
        """Failed tasks that still have retries left, dependencies met."""
        result = []
        for task in self.tasks.values():
            if states.get(task.id) != FAILED:
                continue
            if attempts.get(task.id, 0) >= task.max_retries:
                continue
            if all(states.get(dep, PENDING) == DONE
                   for dep in task.depends_on):
                result.append(task)
        return result

    def to_record(self) -> dict:
        return {"tasks": [task.to_record() for task in self.tasks.values()]}

    @classmethod
    def from_record(cls, record: dict) -> "TaskGraph":
        tasks_list = record.get("tasks", ())
        if not isinstance(tasks_list, list):
            raise GraphError("the graph's 'tasks' must be a list")
        tasks: Dict[str, Task] = {}
        for entry in tasks_list:
            if not isinstance(entry, dict):
                raise GraphError("each task must be an object")
            task = Task.from_record(entry)
            if task.id in tasks:
                raise GraphError(
                    f"the graph has a duplicate task id {task.id!r} - two "
                    f"tasks with one id are one task the scheduler "
                    f"cannot place twice")
            tasks[task.id] = task
        return cls(tasks=tasks)


def load_task_graph(path: Path) -> TaskGraph:
    """Read a task graph from JSON. Raises `GraphError` when malformed."""
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise GraphError(f"cannot read task graph {path}: {error}") from error
    except ValueError as error:
        raise GraphError(f"task graph {path} is not valid JSON: {error}"
                         ) from error
    if not isinstance(body, dict):
        raise GraphError(f"task graph {path} must be a JSON object")
    return TaskGraph.from_record(body)


def load_state(path: Path) -> Dict[str, str]:
    """Read a checkpoint's task states. Empty when there is none."""
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(body, dict):
        return {}
    states = body.get("states", {})
    if not isinstance(states, dict):
        return {}
    return {str(k): str(v) for k, v in states.items()}


def save_state(path: Path, states: Dict[str, str],
               attempts: Optional[Dict[str, int]] = None) -> None:
    """Write the checkpoint atomically - temp plus `os.replace`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {"states": dict(states), "retries": dict(attempts or {})},
        indent=2, sort_keys=True) + "\n"
    import tempfile
    handle, tmp = tempfile.mkstemp(dir=str(path.parent),
                                   prefix=path.name + ".", suffix=".tmp")
    try:
        import os
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            import os
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m library.tools.task_graph",
        description="Validate a Ren Fabric task graph, or list the tasks "
                    "ready to run from a checkpoint.")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="validate a task graph")
    validate.add_argument("graph", type=Path)
    ready = sub.add_parser("ready", help="list ready tasks from a state")
    ready.add_argument("graph", type=Path)
    ready.add_argument("state", type=Path)
    args = parser.parse_args(argv)

    try:
        graph = load_task_graph(args.graph)
        if args.command == "validate":
            print(f"task graph: {len(graph.tasks)} tasks, valid")
            return 0
        states = load_state(args.state)
        ready_tasks = graph.ready(states)
        for task in ready_tasks:
            print(task.id)
        return 0
    except GraphError as error:
        print(f"task graph: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
