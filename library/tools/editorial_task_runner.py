"""PLAN-008: run an editorial task graph - prepare concurrently, write serially.

What this owns
--------------
The EXECUTION of the record ``editorial_task_graph`` declares. It runs
the graph's read-only preparation steps at the same time, it writes the
mutations one at a time, and it leaves a durable record of the last
VERIFIED step so an interrupted run resumes there instead of from the
beginning.

The rules it keeps, and where each one comes from
-------------------------------------------------
**Preparation is outside the Resolve lease.** A preparation reads
evidence and measures state; it never mutates the one Resolve
instance, so it never needs the lease, so independent preparations run
CONCURRENTLY. The concurrency is bounded by ``max_prepare_workers``,
and a preparation whose ``reads`` intersect the state a PENDING write
will change waits for that write - the caller declares what its
preparation reads, and the graph refuses to guess.

**Writes are serialized.** One Resolve instance, one writer at a time.
The graph writes its tasks one after another, in the order the graph
declares, and the AXI transaction owns the mutation itself. The runner
never holds the Resolve lease across a broker submission: the broker
takes its own turn, and a client that held the lease while queueing
behind itself would wait for a turn that cannot come.

**A step counts as done only when it is VERIFIED.** A write whose
receipt reached APPLIED is not done - the ladder says so
(``transaction_receipt``). A step that failed leaves the graph where
it was: the last verified step is the resume point.

**Resume is the project's own journal.** The graph's progress is
written with ``event_log.checkpoint``, so a resumed run reads the last
verified step off the same append-only log every other worker reads.
A resumed run whose graph no longer matches the checkpointed plan
REFUSES: the plan changed under it, and continuing would write an
order nobody approved.

Where it lives
--------------
    python3 -m library.tools.editorial_task_runner plan  <graph.json>
    python3 -m library.tools.editorial_task_runner run   <graph.json> \
        --project-folder <folder> [--apply]

`tests/unit/resolve/test_editorial_task_runner.py`.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools.editorial_task_graph import (
    ORDER_KIND,
    EditorialTask,
    EditorialTaskGraph,
    GraphRefused,
    load_graph,
)

#: The stages at which a write counts as VERIFIED. ``transaction_receipt``
#: owns the ladder; a receipt below VERIFIED is an applied mutation, not
#: a proven one.
VERIFIED_STAGES = ("VERIFIED", "COMMITTED", "PROMOTED", "DELIVERED")

#: The event the runner writes for every task it starts and finishes.
TASK_EVENT = "editorial_task"


class RunnerRefused(RuntimeError):
    """The graph cannot run as asked, and nothing was written."""

    def __init__(self, category: str, reason: str, *,
                 detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "reason": self.reason,
                "detail": dict(self.detail)}


@dataclass
class RunReport:
    """What the run did, and where it stands."""

    graph_id: str
    status: str = "completed"
    write_order: tuple[str, ...] = ()
    prepared: tuple[str, ...] = ()
    verified: tuple[str, ...] = ()
    failed: str = ""
    failure_reason: str = ""
    blocked: list[dict[str, Any]] = field(default_factory=list)
    resumed_from: str = ""
    results: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    concurrency: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "graph": self.graph_id,
            "status": self.status,
            "write_order": list(self.write_order),
            "prepared": list(self.prepared),
            "verified": list(self.verified),
            "failed": self.failed,
            "failure_reason": self.failure_reason,
            "blocked": list(self.blocked),
            "resumed_from": self.resumed_from,
            "results": dict(self.results),
            "timings": dict(self.timings),
            "concurrency": dict(self.concurrency),
        }


class _Counters:
    """How many things ran at the same time, measured not asserted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.prepares_active = 0
        self.prepares_peak = 0
        self.writes_active = 0
        self.writes_peak = 0

    def enter_prepare(self) -> None:
        with self._lock:
            self.prepares_active += 1
            self.prepares_peak = max(self.prepares_peak,
                                     self.prepares_active)

    def leave_prepare(self) -> None:
        with self._lock:
            self.prepares_active -= 1

    def enter_write(self) -> None:
        with self._lock:
            self.writes_active += 1
            self.writes_peak = max(self.writes_peak, self.writes_active)

    def leave_write(self) -> None:
        with self._lock:
            self.writes_active -= 1

    def as_dict(self) -> dict[str, int]:
        return {"prepare_peak": self.prepares_peak,
                "write_peak": self.writes_peak}


def _is_verified(result: Any) -> bool:
    """Whether a write's result proves the write landed.

    A receipt's own ladder answers it (``transaction_receipt``). A
    result that says so in plain words is honoured too, so a caller
    that ran the write itself can report the same truth.
    """
    if isinstance(result, Mapping):
        if result.get("verified") is True:
            return True
        lifecycle = result.get("lifecycle")
        if isinstance(lifecycle, Mapping):
            stage = str(lifecycle.get("stage") or "")
            if stage in VERIFIED_STAGES:
                return True
        stage = str(result.get("stage") or "")
        if stage in VERIFIED_STAGES:
            return True
    return False


def _prepare_context(graph: EditorialTaskGraph, results: Mapping[str, Any],
                     satisfied: Sequence[str]) -> dict[str, Any]:
    return {"graph_id": graph.id, "project": graph.project,
            "timeline": graph.timeline, "graph": graph,
            "results": dict(results), "satisfied": list(satisfied)}


def default_prepare(task: EditorialTask, context: Mapping[str, Any]
                    ) -> dict[str, Any]:
    """Run a preparation's own command, and read what it produced.

    A preparation that declares no command produces nothing: it is a
    step the caller fulfils in process (its ``prepare`` callable).
    """
    if not task.command:
        return {"task": task.id, "produced": {}}
    completed = subprocess.run(list(task.command), capture_output=True,
                               encoding="utf-8", check=False)
    produced: Any = {}
    text = (completed.stdout or "").strip()
    if text:
        try:
            produced = json.loads(text)
        except ValueError:
            produced = {"stdout": text}
    return {"task": task.id, "command": list(task.command),
            "exit_code": completed.returncode, "produced": produced,
            "stderr": (completed.stderr or "")[-2000:]}


def build_request(graph: EditorialTaskGraph, task: EditorialTask, *,
                  request_id: str = "") -> Any:
    """The AXI request this write task is.

    The request id is STABLE for the task AND for its content, so a
    resumed run submits the same id and the AXI answers with the prior
    outcome instead of writing the same mutation twice - and a task
    whose content CHANGED gets a new id, because the old one stands for
    a different edit.
    """
    import hashlib
    import json

    from library.tools.edit_request import EditOperation, EditRequest

    operations = tuple(EditOperation.from_dict(operation)
                       for operation in task.operations)
    digest = hashlib.sha1(
        json.dumps([operation.to_dict() for operation in operations],
                   sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]
    identifier = request_id or f"{graph.id}-{task.id}-{digest}"
    return EditRequest(id=identifier, project=graph.project,
                       timeline=graph.timeline, operations=operations,
                       idempotency_key=identifier,
                       originator=dict(graph.originator))


def default_write(task: EditorialTask, context: Mapping[str, Any]
                  ) -> dict[str, Any]:
    """Submit this write task through the AXI, and report its receipt.

    The AXI owns the mutation: it routes the request, takes the
    Resolve lease through the broker, and reads the result back. This
    function only asks.
    """
    from library.tools import resolve_axi_core

    request = build_request(context["graph"], task)
    receipt = resolve_axi_core.submit_edit_request(request, authorized=True)
    return {"task": task.id, "request_id": request.id,
            "receipt": receipt, "verified": _is_verified(receipt)}


def run(graph: EditorialTaskGraph, *,
        project_folder: str = "",
        prepare: Callable[..., Mapping[str, Any]] | None = None,
        write: Callable[..., Mapping[str, Any]] | None = None,
        resume: bool = True,
        max_prepare_workers: int = 4,
        lease: Callable[[], Any] | None = None,
        ) -> RunReport:
    """Run the graph, and report where it stands.

    `prepare` and `write` are the two executors. The defaults run a
    preparation's own command and submit a write through the AXI.
    `lease` is an optional context manager the write step runs inside -
    the in-worker path takes the Resolve lease itself; the client path
    does not, because the broker takes its own turn.

    The run stops at the first write that is not verified. It never
    continues past a mutation whose outcome is unknown: the AXI's
    recovery rules own that case, and a graph that kept going would
    write on top of an unsettled timeline.
    """
    prepare_fn = prepare or default_prepare
    write_fn = write or default_write
    if max_prepare_workers < 1:
        raise RunnerRefused("unsupported",
                            "max_prepare_workers must be at least 1")

    try:
        order = graph.write_order()
    except GraphRefused as refused:
        raise RunnerRefused(refused.category, refused.reason,
                            detail=refused.detail) from refused

    counters = _Counters()
    report = RunReport(graph_id=graph.id, write_order=order)
    results: dict[str, Any] = {}
    satisfied: list[str] = [str(key) for key in graph.provides]
    verified: list[str] = []
    prepared: list[str] = []

    if resume and project_folder:
        resumed = _read_checkpoint(project_folder, graph, order)
        if resumed is not None:
            verified = list(resumed.get("verified") or [])
            prepared = list(resumed.get("prepared") or [])
            results.update(resumed.get("results") or {})
            satisfied.extend(str(key)
                             for key in (resumed.get("satisfied") or []))
            report.resumed_from = verified[-1] if verified else ""

    def satisfied_keys() -> set[str]:
        return {str(key) for key in satisfied}

    def unmet_for(task: EditorialTask) -> list[dict[str, Any]]:
        have = satisfied_keys()
        rows: list[dict[str, Any]] = []
        for dependency in task.depends_on:
            if dependency.kind == ORDER_KIND:
                continue
            if dependency.provides in have:
                continue
            rows.append({"task": task.id, "kind": dependency.kind,
                         "provides": dependency.provides,
                         "note": dependency.note})
        return rows

    def edit_dependencies_met(task: EditorialTask) -> bool:
        return all(dependency.task in verified
                   for dependency in task.depends_on
                   if dependency.kind == ORDER_KIND)

    def preparers_met(task: EditorialTask) -> bool:
        return all(preparer in verified or preparer in prepared
                   for preparer in task.prepared_by)

    def pending_writes() -> list[EditorialTask]:
        return [graph.tasks[task_id] for task_id in order
                if task_id not in verified]

    def blocked_by_pending_write(task: EditorialTask) -> str:
        """The pending write whose change this preparation's evidence
        would not survive.

        The graph never GUESSES that: a write declares the
        preparations it invalidates (``invalidates``), and a
        preparation waits for that write. A guess would either block
        every preparation or let evidence go stale silently.
        """
        for pending in pending_writes():
            if task.id in pending.invalidates:
                return pending.id
        return ""

    def run_prepare(task: EditorialTask) -> dict[str, Any]:
        counters.enter_prepare()
        started = time.monotonic()
        try:
            context = _prepare_context(graph, results, satisfied)
            outcome = prepare_fn(task, context)
            return {"task": task.id, "seconds": time.monotonic() - started,
                    "result": dict(outcome or {})}
        finally:
            counters.leave_prepare()

    def run_write(task: EditorialTask) -> dict[str, Any]:
        counters.enter_write()
        started = time.monotonic()
        try:
            context = _prepare_context(graph, results, satisfied)
            if lease is not None:
                with lease():
                    outcome = write_fn(task, context)
            else:
                outcome = write_fn(task, context)
            return {"task": task.id, "seconds": time.monotonic() - started,
                    "result": dict(outcome or {})}
        finally:
            counters.leave_write()

    def checkpoint() -> None:
        if not project_folder:
            return
        from library.tools import event_log

        event_log.checkpoint(
            project_folder, graph.id,
            write_order=list(order), verified=list(verified),
            prepared=list(prepared), satisfied=list(satisfied),
            results=_jsonable(results))

    def emit(event_type: str, **fields: Any) -> None:
        if not project_folder:
            return
        from library.tools import event_log

        event_log.emit(project_folder, event_type, task=graph.id, **fields)

    while True:
        # ── 1. the preparations that may run right now, at the same
        # time as each other and outside the Resolve lease.
        ready_prepares = []
        for task in graph.prepare_tasks():
            if task.id in prepared or task.id in verified:
                continue
            if not edit_dependencies_met(task):
                continue
            if unmet_for(task):
                continue
            if blocked_by_pending_write(task):
                continue
            ready_prepares.append(task)

        if ready_prepares:
            emit(TASK_EVENT, phase="prepare",
                 tasks=[task.id for task in ready_prepares])
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=max(1, min(max_prepare_workers,
                                           len(ready_prepares)))) as pool:
                futures = {pool.submit(run_prepare, task): task
                           for task in ready_prepares}
                for future in concurrent.futures.as_completed(futures):
                    task = futures[future]
                    outcome = future.result()
                    results[task.id] = outcome["result"]
                    report.timings[f"prepare:{task.id}"] = outcome["seconds"]
                    prepared.append(task.id)
                    report.prepared = tuple(prepared)
                    for key in graph.provides_of(task.id):
                        if key not in satisfied:
                            satisfied.append(key)
            checkpoint()

        # ── 2. the next write, alone.
        next_write = None
        for task_id in order:
            task = graph.tasks[task_id]
            if task_id in verified:
                continue
            if not edit_dependencies_met(task):
                continue
            if unmet_for(task):
                continue
            if not preparers_met(task):
                continue
            next_write = task
            break

        if next_write is None:
            outstanding = [task for task in graph.write_tasks()
                           if task.id not in verified]
            if not outstanding:
                break
            report.blocked = []
            for task in outstanding:
                report.blocked.extend(unmet_for(task))
                for dependency in task.depends_on:
                    if dependency.kind != ORDER_KIND:
                        continue
                    if dependency.task not in verified:
                        report.blocked.append({
                            "task": task.id, "kind": ORDER_KIND,
                            "task_ref": dependency.task,
                            "note": dependency.note})
            report.status = "blocked"
            checkpoint()
            break

        emit(TASK_EVENT, phase="write", task_id=next_write.id)
        try:
            outcome = run_write(next_write)
        except KeyboardInterrupt:
            results[next_write.id] = {"error": "interrupted"}
            report.status = "failed"
            report.failed = next_write.id
            report.failure_reason = "the run was interrupted"
            report.results = dict(results)
            checkpoint()
            raise
        except Exception as error:  # noqa: BLE001 - the run's own account
            results[next_write.id] = {"error": str(error)}
            report.status = "failed"
            report.failed = next_write.id
            report.failure_reason = f"{type(error).__name__}: {error}"
            report.results = dict(results)
            checkpoint()
            break
        results[next_write.id] = outcome["result"]
        report.timings[f"write:{next_write.id}"] = outcome["seconds"]
        if not _is_verified(outcome["result"]):
            report.status = "failed"
            report.failed = next_write.id
            reason = outcome["result"].get("failure_reason")
            report.failure_reason = (
                f"the write of {next_write.id!r} was not verified: "
                f"{reason or 'the receipt did not reach VERIFIED'}")
            report.results = dict(results)
            checkpoint()
            break
        verified.append(next_write.id)
        report.verified = tuple(verified)
        report.results = dict(results)
        emit(TASK_EVENT, phase="verified", task_id=next_write.id)
        checkpoint()

    report.prepared = tuple(prepared)
    report.verified = tuple(verified)
    report.results = dict(results)
    report.concurrency = counters.as_dict()
    return report


def _jsonable(payload: Any) -> Any:
    """The payload as JSON can hold it.

    A receipt may carry a value the log cannot serialize; the log is
    the resume record, so a payload it cannot write is a payload that
    would be LOST on resume. It is written as its text instead, which
    is still readable, and never dropped.
    """
    try:
        json.dumps(payload)
    except (TypeError, ValueError):
        return json.loads(json.dumps(payload, default=str))
    return payload


def _preparer_closure(graph: EditorialTaskGraph,
                      task: EditorialTask) -> set[str]:
    """The preparation tasks that feed this write, transitively."""
    found: set[str] = set()
    pending = list(task.prepared_by)
    while pending:
        current = pending.pop()
        if current in found:
            continue
        found.add(current)
        parent = graph.tasks.get(current)
        if parent is not None:
            pending.extend(parent.prepared_by)
    return found


def _read_checkpoint(project_folder: str, graph: EditorialTaskGraph,
                     order: Sequence[str]) -> dict[str, Any] | None:
    """The last checkpoint of this graph, or None.

    A checkpoint whose plan no longer matches the graph is REFUSED: the
    plan changed under the run, and resuming would write an order
    nobody approved.
    """
    from library.tools import event_log

    try:
        event = event_log.last_checkpoint(project_folder, graph.id)
    except OSError:
        return None
    if event is None:
        return None
    state = (event.get("fields") or {}).get("state") or {}
    recorded = [str(task_id) for task_id in (state.get("write_order") or [])]
    if recorded and recorded != [str(task_id) for task_id in order]:
        raise RunnerRefused(
            "stale",
            f"the checkpointed plan of graph {graph.id!r} does not match "
            "the graph as it is now; the plan changed under the run, so "
            "it is not resumed",
            detail={"checkpointed": recorded,
                    "now": [str(task_id) for task_id in order]})
    return state


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.editorial_task_runner",
        description="Run an editorial task graph: prepare independently, "
                    "write one at a time, resume from the last verified "
                    "step.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="plan a graph")
    plan.add_argument("graph", type=Path)
    run_parser = sub.add_parser("run", help="run a graph")
    run_parser.add_argument("graph", type=Path)
    run_parser.add_argument("--project-folder", default="",
                            help="the Ren project that owns the journal")
    run_parser.add_argument("--apply", action="store_true",
                            help="authorize the writes (without it, the "
                                 "graph is planned and not run)")
    run_parser.add_argument("--resume", action="store_true",
                            help="continue from the last verified step")
    run_parser.add_argument("--max-prepare-workers", type=int, default=4)
    args = parser.parse_args(argv)

    try:
        graph = load_graph(args.graph)
        if args.command == "plan":
            print(json.dumps({"status": "ok",
                              **graph.plan().as_dict()}, indent=2))
            return 0
        if not args.apply:
            print(json.dumps({"status": "refused", "category": "conflict",
                              "reason": "the graph was not applied: pass "
                                        "--apply to authorize the writes"},
                             indent=2))
            return 1
        report = run(graph, project_folder=args.project_folder,
                     resume=args.resume,
                     max_prepare_workers=args.max_prepare_workers)
    except (GraphRefused, RunnerRefused) as refused:
        payload = (refused.as_dict() if isinstance(refused, RunnerRefused)
                   else {"category": refused.category,
                         "reason": refused.reason,
                         "detail": refused.detail})
        print(json.dumps({"status": "refused", **payload}, indent=2))
        return 1
    print(json.dumps(report.as_dict(), indent=2, default=str))
    return 0 if report.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(_main())
