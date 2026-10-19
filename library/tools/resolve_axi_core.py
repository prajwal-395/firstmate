"""Shared in-process control contract for Resolve AXI entry points.

The CLI, SDK, and MCP all enter through :func:`execute`. The existing
command implementations remain the execution authority; this module owns
typed invocation and presentation adapters, so callers never shell out or
reimplement command semantics.
"""

from __future__ import annotations

import contextlib
import io
import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AxiOperation:
    """One typed AXI command with an argv-safe argument vector."""

    command: str
    arguments: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.command, str) or not self.command.strip():
            raise ValueError("AXI operation command must be a non-empty string")
        if any(not isinstance(value, str) for value in self.arguments):
            raise TypeError("AXI operation arguments must all be strings")

    def argv(self) -> tuple[str, ...]:
        return (self.command, *self.arguments)


@dataclass(frozen=True)
class AxiResult:
    exit_code: int
    stdout: str
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "exit": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


@dataclass(frozen=True)
class AxiTransaction:
    """One generation-pinned transaction containing several native ops."""

    id: str
    project: str
    timeline: str
    base_generation: int
    capability: str
    affected_spans: tuple[tuple[int, int], ...]
    conflict_domains: tuple[str, ...]
    operations: tuple[Mapping[str, Any], ...]
    preconditions: tuple[Mapping[str, Any], ...] = ()
    postconditions: tuple[Mapping[str, Any], ...] = ()

    def patch(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project": self.project,
            "timeline": self.timeline,
            "base_generation": self.base_generation,
            "capability": self.capability,
            "affected_spans": [list(span) for span in self.affected_spans],
            "conflict_domains": list(self.conflict_domains),
            "operations": [dict(operation) for operation in self.operations],
            "preconditions": [dict(value) for value in self.preconditions],
            "postconditions": [dict(value) for value in self.postconditions],
        }


def execute(operation: AxiOperation | Sequence[str]) -> AxiResult:
    """Execute a command in-process through the canonical AXI dispatcher."""
    if isinstance(operation, AxiOperation):
        argv = list(operation.argv())
    else:
        argv = list(operation)
        if any(not isinstance(value, str) for value in argv):
            raise TypeError("AXI argv must contain only strings")
    from library.tools import resolve_axi

    # The command layer writes legacy TOON/prose output directly. Capture it
    # at this boundary so SDK and MCP callers receive data rather than having
    # to parse or redirect the process-global streams themselves.
    with _EXECUTION_LOCK:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = resolve_axi._main_impl(argv)
    return AxiResult(int(code), stdout.getvalue(), stderr.getvalue())


def invoke_cli(argv: Sequence[str] | None = None) -> AxiResult:
    """CLI adapter; byte-preserving output is rendered by ``resolve_axi``."""
    import sys

    return execute(list(sys.argv[1:] if argv is None else argv))


def render_json(result: AxiResult) -> str:
    """Render a result as compact JSON without coupling execution to format."""
    return json.dumps(result.as_dict(), ensure_ascii=False, separators=(",", ":"))


def _is_request_mapping(data: Mapping[str, Any]) -> bool:
    """Whether a plain dict is an edit-request shape, not a patch shape.

    A request carries `originator`/`expected_base_generation` and
    operations with `expected_effect`/`target`; a patch carries
    `base_generation`, `capability` and flat algebra operations. The
    discriminator is the request's own addressing vocabulary, which a
    patch dict never uses.
    """
    if not isinstance(data, Mapping):
        return False
    if "originator" in data or "expected_base_generation" in data:
        return True
    operations = data.get("operations") or ()
    return any(isinstance(op, Mapping)
               and ("expected_effect" in op or "target" in op)
               for op in operations)


def _prepare_request_plan(request):
    """Validate an edit request against its recorded base, without Resolve.

    The request names meaning, not a backend. The router selects the
    executor after preparation; the plan carries its decision and still
    requires explicit authorization before submission.
    """
    from library.tools import edit_preparation, timeline_shadow
    from library.tools.edit_request import EditRequest, compile_request

    if not isinstance(request, EditRequest):
        request = EditRequest.from_dict(request)
    store = timeline_shadow.ShadowStore()
    candidates = [
        (generation, snapshot)
        for generation, snapshot in store.current_snapshots(request.project)
        if generation.timeline_name == request.timeline
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"recorded base for {request.project!r}/{request.timeline!r} "
            f"resolved to {len(candidates)} timelines")
    generation, snapshot = candidates[0]
    if (request.expected_base_generation is not None
            and generation.generation != request.expected_base_generation):
        raise ValueError(
            f"request base generation {request.expected_base_generation} "
            f"is stale; recorded head is {generation.generation}")
    result = edit_preparation.prepare(
        request, base_snapshot=snapshot,
        base_generation=generation.generation)
    if result.refusals:
        raise ValueError(
            "request does not prepare: " + "; ".join(result.refusals))
    compiled = compile_request(request, snapshot)
    # Target resolution is part of preparation. Bind its stable identities
    # onto the same compiled operations before routing; the request itself
    # remains the semantic source of truth.
    resolved = {row.op_index: row for row in result.resolved}
    operations = [dict(operation) for operation in compiled.operations]
    for position, op in enumerate(operations):
        index = compiled.order[position]
        target = resolved.get(index)
        if (target is not None
                and target.status in {"resolved", "assumed"}
                and target.unique_id):
            op["unique_id"] = target.unique_id
    compiled = type(compiled)(
        request_id=compiled.request_id, order=compiled.order,
        operations=tuple(operations), affected_spans=compiled.affected_spans,
        conflict_domains=compiled.conflict_domains, kinds=compiled.kinds,
        assumptions=compiled.assumptions)
    # VC-16: a request the recorded state already satisfies takes the
    # router's no-op route at plan time - before any asset is
    # generated, no staging, no Resolve write. `decision_for_...`
    # returns None for anything unverified (no base, refusals,
    # changes outstanding), which keeps the normal backend choice.
    from library.tools import execution_router as _router
    from library.tools import perf_ledger as _perf
    with _perf.span("resolve_noop_check", resolve_contacted=False,
                    state_source="recorded_snapshot"):
        no_op = _router.decision_for_prepared_request(result)
    if no_op is not None:
        routing = no_op.as_dict()
        backend: str | None = "no_op"
        backend_reason = str(no_op.reasons[0]) if no_op.reasons else (
            "already satisfied: recorded state already holds every "
            "requested value")
    else:
        routing = compiled.route_request().copy()
        backend = routing["backend"]
        backend_reason = "; ".join(routing.get("reasons", ()))
    plan = {
        "status": "planned",
        "request_id": request.id,
        "transaction_id": request.id,
        "project": request.project,
        "timeline": request.timeline,
        "base_generation": generation.generation,
        "backend": backend,
        "backend_reason": backend_reason,
        "needs_capability": False,
        "operations": [dict(operation)
                       for operation in compiled.operations],
        "affected_spans": [list(span)
                           for span in compiled.affected_spans],
        "conflict_domains": list(compiled.conflict_domains),
        "no_write_needed": result.no_write_needed,
        "preparation": result.as_dict(),
        "authorization_required": True,
        "resolve_contacted": False,
        "mutated": False,
    }
    if routing is not None:
        plan["routing"] = routing
    return plan, request, result, compiled, generation, snapshot


def _plan_request(request) -> dict[str, Any]:
    return _prepare_request_plan(request)[0]


def prepare_edit_request(request) -> dict[str, Any]:
    """Prepare and route one semantic request without contacting Resolve."""
    return _plan_request(request)


def _request_from_any(request):
    from library.tools.edit_request import EditRequest

    return request if isinstance(request, EditRequest) else EditRequest.from_dict(request)


def _patch_capability(compiled) -> str:
    """Find the narrowest registered capability contract that covers a patch."""
    from library.tools import capabilities

    operation_ids = {str(operation["op"]) for operation in compiled.operations}
    domains = set(compiled.conflict_domains)
    candidates = []
    for capability in capabilities.all():
        patch = capability.execution.patch
        phase = capability.execution.phase("apply")
        if patch is None or phase is None:
            continue
        if (phase.resolve_mode != "exclusive"
                or phase.locality != "timeline"
                or phase.freshness != "timeline_generation"):
            continue
        declared_ops = set(patch.operations)
        declared_domains = set(patch.conflict_domains)
        if operation_ids <= declared_ops and domains <= declared_domains:
            candidates.append((len(declared_ops - operation_ids),
                               len(declared_domains - domains), capability.id))
    if not candidates:
        raise ValueError(
            "no registered AXI capability patch contract covers the routed "
            "operations and conflict domains")
    return min(candidates)[2]


def submit_edit_request_async(request, *, authorized: bool = False,
                              owner: str = "resolve-axi") -> dict[str, Any]:
    """Prepare, route, authorize and queue a semantic EditRequest.

    The router owns backend selection. The current broker's typed semantic
    commit adapter is EditPatch; a route without that adapter is refused
    before any Resolve write rather than silently changing workflows.
    """
    if authorized is not True:
        raise PermissionError("semantic edits require explicit authorization")
    request = _request_from_any(request)
    plan, request, _prepared, compiled, generation, snapshot = (
        _prepare_request_plan(request))
    if plan["backend"] == "no_op":
        from library.tools.transaction_receipt import lifecycle
        receipt = {"status": "already_satisfied", "request_id": request.id,
                   "project": request.project, "timeline": request.timeline,
                   "backend": "no_op", "mutated": False,
                   "execution_routing": plan["routing"]}
        receipt["lifecycle"] = lifecycle(
            receipt, stage="VERIFIED", backend="no_op",
            expected_state={"satisfied": True},
            actual_state={"satisfied": True},
            verification={"outcome": "already_satisfied",
                          "resolve_contacted": False})
        return {"id": request.id, "request_id": request.id,
                "status": "already_satisfied", "plan": plan,
                "receipt": receipt}
    backend = str(plan["backend"])
    if backend not in {"edit_patch", "edit_patch.property"}:
        raise ValueError(
            f"router selected {backend!r}, but no qualified broker adapter "
            "is registered for that backend")
    patch = compiled.to_patch(
        _patch_capability(compiled), base_generation=generation.generation,
        project=request.project, timeline=request.timeline,
        idempotency_key=request.idempotency_key,
        preconditions=request.preconditions)
    from library.tools.resolved import client
    client.ensure()
    job = client.submit("timeline.apply_patch", {"patch": patch},
                        owner=owner, idempotency_key=request.idempotency_key)
    return {**job, "request_id": request.id, "backend": backend,
            "plan": plan, "status": "queued"}


def retrieve_edit_request(job_id: str, *, wait: float = 0.0) -> dict[str, Any]:
    """Read the broker job and normalize its completed receipt."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job id must be a non-empty string")
    if wait < 0:
        raise ValueError("wait must be non-negative")
    from library.tools.resolved import client
    job = client.result(job_id, wait=wait)
    if job is None:
        return {"id": job_id, "status": "unknown"}
    result = job.get("result")
    if isinstance(result, Mapping) and "lifecycle" not in result:
        from library.tools.transaction_orchestration import standardize_receipt
        result = standardize_receipt(dict(result))
    return {"id": job_id, "status": job.get("state", job.get("status")),
            "result": result, "error": job.get("error")}


def submit_edit_request(request, *, authorized: bool = False,
                        owner: str = "resolve-axi",
                        timeout: float = 120.0) -> dict[str, Any]:
    """Submit semantic intent and wait for the broker's verified result."""
    submitted = submit_edit_request_async(
        request, authorized=authorized, owner=owner)
    if submitted.get("status") == "already_satisfied":
        return submitted["receipt"]
    job_id = submitted.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("broker did not return an edit job id")
    return retrieve_edit_request(job_id, wait=timeout)


def cancel_edit_request(job_id: str) -> dict[str, Any]:
    """Request cancellation only through the broker's declared safe path."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job id must be a non-empty string")
    from library.tools.resolved import client
    return client.call({"op": "cancel", "id": job_id})


def plan_transaction(transaction) -> dict[str, Any]:
    """Validate a transaction against its recorded base without Resolve.

    Accepts a typed legacy `AxiTransaction` or a semantic `EditRequest`.
    Semantic requests are prepared and routed without Resolve contact;
    legacy transactions retain their EditPatch contract. Neither path
    queues or mutates anything.
    """
    from library.tools.edit_request import EditRequest

    if isinstance(transaction, EditRequest) or _is_request_mapping(
            transaction):
        return _plan_request(transaction)
    if not isinstance(transaction, AxiTransaction):
        raise TypeError(
            "plan_transaction takes an AxiTransaction or an EditRequest, "
            f"got {type(transaction).__name__}")
    return _plan_transaction_patch(transaction)


def _plan_transaction_patch(transaction: AxiTransaction) -> dict[str, Any]:
    """The typed-transaction plan: EditPatch validation against the base."""
    from library.tools import edit_patch, timeline_shadow

    patch = edit_patch.EditPatch.from_dict(transaction.patch())
    store = timeline_shadow.ShadowStore()
    candidates = [
        (generation, snapshot)
        for generation, snapshot in store.current_snapshots(patch.project)
        if generation.timeline_name == patch.timeline
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"recorded base for {patch.project!r}/{patch.timeline!r} "
            f"resolved to {len(candidates)} timelines")
    generation, snapshot = candidates[0]
    if generation.generation != patch.base_generation:
        raise ValueError(
            f"transaction base generation {patch.base_generation} is stale; "
            f"recorded head is {generation.generation}")
    edit_patch.validate(patch, snapshot)
    failures = edit_patch._judge(patch.preconditions, snapshot, snapshot)
    if failures:
        raise ValueError("transaction preconditions failed: " + "; ".join(failures))
    return {
        "status": "planned",
        "transaction_id": patch.id,
        "project": patch.project,
        "timeline": patch.timeline,
        "base_generation": patch.base_generation,
        "backend": "edit_patch",
        "backend_reason": "typed timeline transactions execute through the EditPatch coordinator",
        "operations": [dict(operation) for operation in patch.operations],
        "affected_spans": [list(span) for span in patch.affected_spans],
        "conflict_domains": list(patch.conflict_domains),
        "authorization_required": True,
        "resolve_contacted": False,
        "mutated": False,
    }


def submit_transaction_async(
    transaction: AxiTransaction, *, owner: str = "resolve-axi"
) -> dict[str, Any]:
    """Queue a multi-operation EditPatch and return its broker job handle."""
    from library.tools.resolved import client

    client.ensure()
    return client.submit(
        "timeline.apply_patch", {"patch": transaction.patch()}, owner=owner
    )


def retrieve_transaction(job_id: str, *, wait: float = 0.0) -> dict[str, Any]:
    """Retrieve an async transaction job, optionally waiting for completion."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("transaction job id must be a non-empty string")
    if wait < 0:
        raise ValueError("wait must be non-negative")
    from library.tools.resolved import client

    job = client.result(job_id, wait=wait)
    return job.get("result") or job


def submit_transaction(
    transaction: AxiTransaction, *, owner: str = "resolve-axi", timeout: float = 120.0
) -> dict[str, Any]:
    """Submit a multi-operation EditPatch and wait for its verified receipt."""
    submitted = submit_transaction_async(transaction, owner=owner)
    return retrieve_transaction(submitted["id"], wait=timeout)


_EXECUTION_LOCK = threading.RLock()
