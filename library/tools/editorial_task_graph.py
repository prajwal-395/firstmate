"""PLAN-008: an editorial assignment is a TASK GRAPH, not a chain of commands.

What this owns
--------------
The RECORD of a larger editorial assignment: which operations it
carries, in what order they may be written, which of them depend on
retrieved evidence, on an existing effect, on an earlier edit, or on
the captain's approval - and which of them may be PREPARED at the same
time.

The record is a declaration. It writes nothing, contacts nothing, and
decides no editorial intent: the operations themselves are the AXI
algebra's (``edit_algebra``), the order inside one multi-operation
request is the compound transaction's (``compound_transaction``), and
the execution is the runner's (``editorial_task_runner``).

Two kinds of task
------------------
``prepare``
    Read-only work: retrieve the evidence an operation needs, read the
    effect it will act on, measure the geometry it will move. It never
    writes, so it never needs the Resolve lease, so independent
    preparations may run CONCURRENTLY.

``write``
    One AXI request. It mutates the single Resolve instance, so the
    graph writes them ONE AT A TIME, in the order the graph declares.

Why the write order is computed and not declared
------------------------------------------------
The order inside one multi-operation request is the compound rules'
(``compound_transaction.plan_write_order``): the engine declares the
dependencies it knows (a ripple closes the gap a delete left), the
requester may declare more, and an order that CONTRADICTS a declared
dependency is refused before the first write. The order BETWEEN two
write tasks is the graph's own: a task may declare that it depends on
another task having landed (``kind="edit"``), and the engine's
dependency table applies between tasks too - a ripple task that runs
before the delete task it closes the gap of is a contradiction, and it
is refused the same way.

The four dependency kinds
--------------------------
``evidence``
    The operation's parameters come from evidence that was retrieved:
    a footage hit, a measurement, a transcript span. The evidence is
    named by a ``provides`` key; the key is satisfied either by the
    graph's own declaration (the caller already has it) or by a
    ``prepare`` task that produced it.

``effect``
    The operation acts on an effect that already exists on the
    timeline - an overlay, a caption card, a Fusion comp. The effect
    must be there; a graph that names one that is not refuses that
    task by name.

``approval``
    The captain approved this operation. The approval is a named
    ``provides`` key like any other precondition, so a graph that
    depends on an approval nobody gave says so instead of writing.

``edit``
    The operation depends on an EARLIER EDIT in this graph. This is
    the only kind that constrains the ORDER; the other three are
    preconditions.

Where it lives
--------------
The record is JSON, and the runner reads it from disk:

    {
      "id": "reel-2-adjustments",
      "project": "Ren Scratch - fm-p008",
      "timeline": "Reel 2",
      "provides": {"approval:captain": {"status": "satisfied"}},
      "tasks": [
        {"id": "prep-caption", "kind": "prepare",
         "reads": ["captions"], "provides": ["evidence:caption-text"]},
        {"id": "trim-a", "kind": "write", "op": "clip.trim",
         "prepared_by": ["prep-trim"],
         "operation": {"op": "clip.trim", "target": {"unique_id": "..."},
                       "params": {...}, "expected_effect": "..."}},
        {"id": "caption-b", "kind": "write", "op": "caption.style",
         "depends_on": [{"kind": "edit", "task": "trim-a"},
                        {"kind": "evidence", "provides": "evidence:caption-text"}]}
      ]
    }

    python3 -m library.tools.editorial_task_graph plan <graph.json>

`tests/unit/resolve/test_editorial_task_graph.py`.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools import compound_transaction

#: The kinds of dependency an editorial task may declare.
DEPENDENCY_KINDS = ("evidence", "effect", "approval", "edit")

#: The kinds of task. ``prepare`` is read-only and may run concurrently;
#: ``write`` mutates the one Resolve instance and is serialized.
TASK_KINDS = ("prepare", "write")

#: The dependency kinds that are PRECONDITIONS - they must be satisfied
#: before the task may be prepared or written, but they do not order it.
PRECONDITION_KINDS = ("evidence", "effect", "approval")

#: The one dependency kind that ORDERS: the named task must have landed
#: and been verified first.
ORDER_KIND = "edit"


class GraphRefused(ValueError):
    """The graph is malformed or contradicts itself; nothing was written."""

    def __init__(self, category: str, reason: str, *,
                 detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "reason": self.reason,
                "detail": dict(self.detail)}


@dataclass(frozen=True)
class Dependency:
    """One declared dependency of a task.

    ``kind`` says WHAT kind of dependency it is. ``task`` names the
    task that must have landed (for ``edit``). ``provides`` names the
    precondition key that must be satisfied (for ``evidence``,
    ``effect`` and ``approval``).
    """

    kind: str
    task: str = ""
    provides: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in DEPENDENCY_KINDS:
            raise GraphRefused(
                "unsupported",
                f"unknown dependency kind {self.kind!r}; a dependency "
                f"is one of {list(DEPENDENCY_KINDS)}")
        if self.kind == ORDER_KIND:
            if not self.task:
                raise GraphRefused(
                    "unsupported",
                    "an 'edit' dependency must name the task it waits "
                    "for")
            if self.provides:
                raise GraphRefused(
                    "unsupported",
                    f"dependency {self.task!r} names both a task and a "
                    "provides key; an 'edit' dependency waits for a task "
                    "and the other kinds wait for a key")
        else:
            if not self.provides:
                raise GraphRefused(
                    "unsupported",
                    f"a {self.kind!r} dependency must name the provides "
                    "key whose precondition it waits for")
            if self.task:
                raise GraphRefused(
                    "unsupported",
                    f"a {self.kind!r} dependency names a task; only an "
                    "'edit' dependency waits for a task")

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "task": self.task,
                "provides": self.provides, "note": self.note}

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> Dependency:
        if not isinstance(record, Mapping):
            raise GraphRefused("unsupported",
                               "a dependency must be an object")
        return cls(kind=str(record.get("kind") or ""),
                   task=str(record.get("task") or ""),
                   provides=str(record.get("provides") or ""),
                   note=str(record.get("note") or ""))


@dataclass(frozen=True)
class EditorialTask:
    """One node of the editorial task graph.

    A ``write`` task carries the AXI operation it will request - one
    operation, or a LIST of them, which is a compound request and one
    transaction. A ``prepare`` task carries the read-only work that
    produces the evidence a write needs; ``reads`` names the state
    domains that work reads, which is what decides whether it may run
    while a write is still pending.
    """

    id: str
    kind: str
    op: str = ""
    operation: Any = field(default_factory=dict)
    command: tuple[str, ...] = ()
    reads: tuple[str, ...] = ()
    domain: str = ""
    depends_on: tuple[Dependency, ...] = ()
    prepared_by: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    invalidates: tuple[str, ...] = ()
    note: str = ""
    """The preparations whose evidence this write invalidates.

    A preparation may run while a write is pending only when that
    write does not change what the preparation reads. The graph NEVER
    guesses that: a write that changes what a preparation reads
    DECLARES it, here, and the preparation waits for the write. A
    guess would either block every preparation (nothing may be
    prepared ahead of a write) or none (evidence goes stale silently).
    """

    def __post_init__(self) -> None:
        if not self.id or not str(self.id).strip():
            raise GraphRefused("unsupported",
                               "a task needs an id - an unnamed task is "
                               "unreachable by its dependents")
        if self.kind not in TASK_KINDS:
            raise GraphRefused(
                "unsupported",
                f"task {self.id!r} has kind {self.kind!r}; a task is "
                f"one of {list(TASK_KINDS)}")
        if self.kind == "write":
            if not self.operation:
                raise GraphRefused(
                    "unsupported",
                    f"task {self.id!r} is a write with no operation - a "
                    "write that asks for nothing changes nothing")
            if not self.op:
                object.__setattr__(self, "op",
                                   _first_op_id(self.operation))

    @property
    def operations(self) -> tuple[Mapping[str, Any], ...]:
        """The operations this task writes, as a list (one, or a set)."""
        if isinstance(self.operation, Mapping):
            return (dict(self.operation),)
        if isinstance(self.operation, Sequence) and not isinstance(
                self.operation, (str, bytes)):
            return tuple(dict(entry) for entry in self.operation)
        raise GraphRefused(
            "unsupported",
            f"task {self.id!r} carries an operation that is neither an "
            "object nor a list of objects")

    @property
    def domains(self) -> tuple[str, ...]:
        """The state domains this task's write touches."""
        if self.kind != "write":
            return ()
        if self.domain:
            return (self.domain,)
        domains: list[str] = []
        for operation in self.operations:
            op_id = str(operation.get("op") or "")
            for domain in _op_domains(op_id):
                if domain not in domains:
                    domains.append(domain)
        return tuple(domains)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "op": self.op,
            "operation": self.operation,
            "command": list(self.command),
            "reads": list(self.reads),
            "domain": self.domain,
            "depends_on": [dep.as_dict() for dep in self.depends_on],
            "prepared_by": list(self.prepared_by),
            "provides": list(self.provides),
            "invalidates": list(self.invalidates),
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> EditorialTask:
        if not isinstance(record, Mapping):
            raise GraphRefused("unsupported", "a task must be an object")
        dependencies = tuple(Dependency.from_dict(entry) for entry in
                             (record.get("depends_on") or ()))
        command = record.get("command") or ()
        if isinstance(command, str):
            command = (command,)
        return cls(
            id=str(record.get("id") or ""),
            kind=str(record.get("kind") or ""),
            op=str(record.get("op") or ""),
            operation=record.get("operation") or {},
            command=tuple(str(part) for part in command),
            reads=tuple(str(part) for part in (record.get("reads") or ())),
            domain=str(record.get("domain") or ""),
            depends_on=dependencies,
            prepared_by=tuple(str(part) for part in
                              (record.get("prepared_by") or ())),
            provides=tuple(str(part) for part in
                           (record.get("provides") or ())),
            invalidates=tuple(str(part) for part in
                              (record.get("invalidates") or ())),
            note=str(record.get("note") or ""),
        )


def _first_op_id(operation: Any) -> str:
    """The algebra op id of an operation payload, or a list's first."""
    if isinstance(operation, Mapping):
        return str(operation.get("op") or "")
    if isinstance(operation, Sequence) and not isinstance(operation,
                                                           (str, bytes)):
        for entry in operation:
            if isinstance(entry, Mapping) and entry.get("op"):
                return str(entry.get("op"))
    return ""


def _op_domains(op_id: str) -> tuple[str, ...]:
    """The state domains an algebra op touches, read from the algebra.

    An op the algebra does not define has no domain: the graph says
    so rather than guessing one, and the caller may declare it.
    """
    if not op_id:
        return ()
    from library.tools import edit_algebra

    status = edit_algebra.status(op_id)
    if status is None:
        return ()
    return tuple(status.state_domains)


@dataclass
class TaskPlan:
    """What the graph says about the order and the concurrency.

    ``write_order`` is the serialized order of the writes.
    ``prepare_waves`` groups the preparations by the earliest wave they
    can be in: everything in wave 0 may be prepared at the same time,
    wave 1 waits for the writes wave 0's tasks depend on, and so on.
    ``dependencies`` records, per task, the dependency kinds it
    declared - the four the punchlist names.
    """

    write_order: tuple[str, ...] = ()
    prepare_waves: tuple[tuple[str, ...], ...] = ()
    dependencies: dict[str, tuple[Dependency, ...]] = field(
        default_factory=dict)
    order_dependencies: tuple[Mapping[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "write_order": list(self.write_order),
            "prepare_waves": [list(wave) for wave in self.prepare_waves],
            "dependencies": {task: [dep.as_dict() for dep in deps]
                             for task, deps in self.dependencies.items()},
            "order_dependencies": [dict(row) for row in
                                   self.order_dependencies],
        }


@dataclass
class EditorialTaskGraph:
    """The record: every task by id, and the preconditions already met.

    ``provides`` are the preconditions the CALLER says are already
    satisfied - evidence it retrieved, an effect it found, an approval
    it has. A task's ``provides`` keys are checked against this map and
    against the keys the ``prepare`` tasks produce.
    """

    id: str
    tasks: dict[str, EditorialTask]
    provides: dict[str, Any] = field(default_factory=dict)
    project: str = ""
    timeline: str = ""
    originator: dict = field(default_factory=lambda: {
        "kind": "agent", "name": "editorial-task-graph"})
    note: str = ""
    """Who asked for the edit.

    The AXI's own routing reads it: a front door that owns the
    protected staging guarantee is named here, and an operation with an
    executor but no production qualification runs under that policy
    instead of being refused. The graph never grants the guarantee
    itself - it names the front door that owns it.
    """

    def __post_init__(self) -> None:
        if not self.id or not str(self.id).strip():
            raise GraphRefused("unsupported",
                               "a graph needs an id - a resumed run reads "
                               "the log by it")
        for task in self.tasks.values():
            for dependency in task.depends_on:
                if dependency.kind == ORDER_KIND:
                    if dependency.task not in self.tasks:
                        raise GraphRefused(
                            "unsupported",
                            f"task {task.id!r} depends on task "
                            f"{dependency.task!r}, which is not in the "
                            "graph - a dependency on nothing is a task "
                            "that can never run")
                    if dependency.task == task.id:
                        raise GraphRefused(
                            "unsupported",
                            f"task {task.id!r} depends on itself")
            for preparer in task.prepared_by:
                if preparer not in self.tasks:
                    raise GraphRefused(
                        "unsupported",
                        f"task {task.id!r} is prepared by {preparer!r}, "
                        "which is not in the graph")
            for invalidated in task.invalidates:
                if invalidated not in self.tasks:
                    raise GraphRefused(
                        "unsupported",
                        f"task {task.id!r} invalidates {invalidated!r}, "
                        "which is not in the graph")
                if self.tasks[invalidated].kind != "prepare":
                    raise GraphRefused(
                        "unsupported",
                        f"task {task.id!r} invalidates {invalidated!r}, "
                        "which is not a preparation - only a preparation "
                        "holds evidence a write can invalidate")
        self._check_cycles()

    def _check_cycles(self) -> None:
        """No task may wait for itself, directly or through others.

        The edges are the ``edit`` dependencies and the
        ``prepared_by`` links: a task that waits for a task that waits
        for it can never run, and the graph says so at load time
        rather than half way through a run.
        """
        edges: dict[str, set[str]] = {task_id: set()
                                       for task_id in self.tasks}
        for task in self.tasks.values():
            for dependency in task.depends_on:
                if dependency.kind == ORDER_KIND:
                    edges[dependency.task].add(task.id)
            for preparer in task.prepared_by:
                edges[preparer].add(task.id)
            for invalidated in task.invalidates:
                # The preparation runs AFTER the write that invalidates
                # its evidence, so the edge runs the other way.
                edges[task.id].add(invalidated)

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise GraphRefused(
                    "unsupported",
                    f"the graph's dependencies cycle through {task_id!r} - "
                    "a task that waits for itself can never run")
            if task_id in visited:
                return
            visiting.add(task_id)
            for successor in edges.get(task_id, ()):
                visit(successor)
            visiting.discard(task_id)
            visited.add(task_id)

        for task_id in self.tasks:
            visit(task_id)

    # ── the order ────────────────────────────────────────────────────

    def write_tasks(self) -> tuple[EditorialTask, ...]:
        """The write tasks, in the order the graph declares them."""
        return tuple(task for task in self.tasks.values()
                     if task.kind == "write")

    def prepare_tasks(self) -> tuple[EditorialTask, ...]:
        """The prepare tasks, in the order the graph declares them."""
        return tuple(task for task in self.tasks.values()
                     if task.kind == "prepare")

    def write_order(self) -> tuple[str, ...]:
        """The order the writes are written in.

        Two sources of order, and both are honoured:

        - the task's own ``edit`` dependencies (this task waits for
          that one);
        - the engine's declared dependencies between operations
          (``compound_transaction.ENGINE_DEPENDENCIES``): a ripple task
          waits for the delete task whose gap it closes.

        A declared order that CONTRADICTS an engine dependency is
        refused: the graph does not choose an order nobody asked for.
        """
        writes = self.write_tasks()
        ids = [task.id for task in writes]
        edges: dict[str, set[str]] = {task_id: set() for task_id in ids}
        reasons: dict[tuple[str, str], str] = {}

        def add_edge(first: str, second: str, kind: str) -> None:
            if first == second:
                raise GraphRefused(
                    "unsupported",
                    f"task {first!r} depends on itself")
            if second in edges[first]:
                return
            edges[first].add(second)
            reasons[(first, second)] = kind

        for task in writes:
            for dependency in task.depends_on:
                if dependency.kind == ORDER_KIND:
                    add_edge(dependency.task, task.id, "declared")

        # The engine's own dependencies, applied BETWEEN tasks: the
        # ripple that closes a delete's gap is written after it.
        for first_op, second_op in compound_transaction.ENGINE_DEPENDENCIES:
            firsts = [task.id for task in writes
                      if _has_op(task, first_op)]
            seconds = [task.id for task in writes
                       if _has_op(task, second_op)]
            for first in firsts:
                for second in seconds:
                    if first == second:
                        continue
                    add_edge(first, second, "engine")

        # A contradiction: the graph says one thing and the engine's
        # dependency says another. The compound rules refuse this the
        # same way - a request that declares BOTH directions cannot be
        # executed in an order nobody asked for.
        for (first, second) in reasons:
            if (second, first) in reasons:
                raise GraphRefused(
                    "conflict",
                    (f"the graph asks for task {second!r} to be written "
                     f"before task {first!r}, which the engine's "
                     f"declared dependency forbids: {first!r} must be "
                     "written first"),
                    detail={"before": first, "after": second,
                            "dependency": "engine"})

        order = _stable_topological_order(ids, edges)
        return tuple(order)

    def plan(self) -> TaskPlan:
        """The order and the concurrency, read off the declaration."""
        order = self.write_order()
        dependencies = {task_id: task.depends_on
                        for task_id, task in self.tasks.items()}

        # The earliest wave a task can be prepared in: a preparation
        # waits for the writes it depends on, a write waits for its own
        # preparations, and a preparation whose evidence a write
        # invalidates waits for that write.
        wave: dict[str, int] = {task_id: 0 for task_id in self.tasks}

        def constraints(task: EditorialTask) -> list[str]:
            """The tasks this one waits for, directly."""
            waited = [dependency.task for dependency in task.depends_on
                      if dependency.kind == ORDER_KIND]
            waited.extend(task.prepared_by)
            return waited

        for _ in range(len(self.tasks) + 1):
            changed = False
            for task in self.tasks.values():
                latest = wave[task.id]
                for other in constraints(task):
                    latest = max(latest, wave[other] + 1)
                if latest != wave[task.id]:
                    wave[task.id] = latest
                    changed = True
            for task in self.write_tasks():
                for invalidated in task.invalidates:
                    latest = wave[task.id] + 1
                    if wave[invalidated] < latest:
                        wave[invalidated] = latest
                        changed = True
            if not changed:
                break

        waves: dict[int, list[str]] = {}
        for task in self.prepare_tasks():
            waves.setdefault(wave[task.id], []).append(task.id)
        prepare_waves = tuple(tuple(waves[key])
                              for key in sorted(waves))

        order_dependencies = [
            {"before": first, "after": second, "kind": kind}
            for (first, second), kind in sorted(reasons_of(self).items())]
        return TaskPlan(write_order=order, prepare_waves=prepare_waves,
                        dependencies=dependencies,
                        order_dependencies=order_dependencies)

    # ── the preconditions ────────────────────────────────────────────

    def unmet_preconditions(self, satisfied: Sequence[str] = ()
                            ) -> list[dict[str, Any]]:
        """The preconditions this graph does not have, by name.

        `satisfied` is what the run has: the keys the graph declared
        plus the keys the prepare tasks produced. A precondition that
        is not met is reported, never guessed and never assumed.
        """
        have = {str(key) for key in self.provides}
        have.update(str(key) for key in (satisfied or ()))
        unmet: list[dict[str, Any]] = []
        for task in self.tasks.values():
            for dependency in task.depends_on:
                if dependency.kind == ORDER_KIND:
                    continue
                if dependency.provides in have:
                    continue
                unmet.append({"task": task.id, "kind": dependency.kind,
                              "provides": dependency.provides,
                              "note": dependency.note})
        return unmet

    def provides_of(self, task_id: str) -> tuple[str, ...]:
        """The precondition keys a task produces when it succeeds."""
        task = self.tasks.get(task_id)
        return tuple(task.provides) if task else ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "timeline": self.timeline,
            "provides": dict(self.provides),
            "originator": dict(self.originator),
            "note": self.note,
            "tasks": [task.as_dict() for task in self.tasks.values()],
        }

    @classmethod
    def from_dict(cls, record: Mapping[str, Any]) -> EditorialTaskGraph:
        if not isinstance(record, Mapping):
            raise GraphRefused("unsupported",
                               "a graph must be an object")
        raw_tasks = record.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise GraphRefused("unsupported",
                               "a graph must carry a list of tasks")
        tasks: dict[str, EditorialTask] = {}
        for entry in raw_tasks:
            task = EditorialTask.from_dict(entry)
            if task.id in tasks:
                raise GraphRefused(
                    "unsupported",
                    f"the graph has a duplicate task id {task.id!r}")
            tasks[task.id] = task
        return cls(id=str(record.get("id") or ""),
                   tasks=tasks,
                   provides=dict(record.get("provides") or {}),
                   project=str(record.get("project") or ""),
                   timeline=str(record.get("timeline") or ""),
                   originator=dict(record.get("originator") or
                                   {"kind": "agent",
                                    "name": "editorial-task-graph"}),
                   note=str(record.get("note") or ""))


def _has_op(task: EditorialTask, op_id: str) -> bool:
    """Whether a task carries an operation with this algebra op id."""
    try:
        operations = task.operations
    except GraphRefused:
        return False
    return any(str(operation.get("op") or "") == op_id
               for operation in operations)


def reasons_of(graph: EditorialTaskGraph) -> dict[tuple[str, str], str]:
    """The order edges the graph's tasks declare, and why."""
    edges: dict[tuple[str, str], str] = {}
    for task in graph.write_tasks():
        for dependency in task.depends_on:
            if dependency.kind == ORDER_KIND:
                edges.setdefault((dependency.task, task.id), "declared")
    for first_op, second_op in compound_transaction.ENGINE_DEPENDENCIES:
        for first in graph.write_tasks():
            if not _has_op(first, first_op):
                continue
            for second in graph.write_tasks():
                if second.id == first.id:
                    continue
                if _has_op(second, second_op):
                    edges.setdefault((first.id, second.id), "engine")
    return edges


def _stable_topological_order(ids: Sequence[str],
                              edges: Mapping[str, set[str]]) -> list[str]:
    """A stable topological order: the declared order among ready tasks."""
    predecessors: dict[str, set[str]] = {task_id: set() for task_id in ids}
    for first, seconds in edges.items():
        for second in seconds:
            predecessors.setdefault(second, set()).add(first)
    done: list[str] = []
    remaining = list(ids)
    while remaining:
        progressed = False
        for task_id in list(remaining):
            if all(dependency in done
                   for dependency in predecessors.get(task_id, ())):
                done.append(task_id)
                remaining.remove(task_id)
                progressed = True
        if not progressed:
            raise GraphRefused(
                "unsupported",
                "the graph's dependencies do not resolve: they form a "
                "cycle over tasks " + ", ".join(remaining))
    return done


def load_graph(path: Path) -> EditorialTaskGraph:
    """Read a graph from JSON. Raises `GraphRefused` when malformed."""
    try:
        body = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise GraphRefused("unsupported",
                           f"cannot read the graph {path}: {error}"
                           ) from error
    except ValueError as error:
        raise GraphRefused(
            "unsupported",
            f"the graph {path} is not valid JSON: {error}") from error
    if not isinstance(body, dict):
        raise GraphRefused("unsupported",
                           f"the graph {path} must be a JSON object")
    return EditorialTaskGraph.from_dict(body)


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_task_graph",
        description="Read an editorial task graph: its write order, the "
                    "dependencies it declares, and the preparations that "
                    "may run at the same time.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="plan a graph")
    plan.add_argument("graph", type=Path)
    args = parser.parse_args(argv)

    try:
        graph = load_graph(args.graph)
        result = graph.plan()
    except GraphRefused as refused:
        print(json.dumps({"status": "refused", **refused.as_dict()},
                         indent=2))
        return 1
    print(json.dumps({"status": "ok", "id": graph.id,
                      "write_order": list(result.write_order),
                      "prepare_waves": [list(wave) for wave in
                                        result.prepare_waves],
                      "dependencies": {
                          task: [dep.as_dict() for dep in deps]
                          for task, deps in result.dependencies.items()}},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
