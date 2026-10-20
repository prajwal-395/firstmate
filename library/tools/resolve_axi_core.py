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
from collections.abc import Callable, Mapping, Sequence
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


@dataclass(frozen=True)
class EditExecutionContext:
    """Prepared semantic intent handed to one qualified physical adapter.

    The request remains the source of intent. ``execution`` is the shared
    backend-neutral contract; the remaining fields are preparation evidence
    an adapter may use to derive its own staging inputs. Adapters must not
    replace the router decision or infer qualification from their existence.
    """

    execution: Any
    request: Any
    preparation: Any
    compiled: Any
    generation: Any
    snapshot: Mapping[str, Any]
    operation: Mapping[str, Any]
    operation_index: int
    owner: str


EditExecutor = Callable[[EditExecutionContext], Mapping[str, Any]]
_EDIT_EXECUTORS: dict[tuple[str, str], EditExecutor] = {}
_SEMANTIC_EXECUTION_RESULTS: dict[str, dict[str, Any]] = {}
_EDIT_EXECUTOR_LOCK = threading.RLock()
_ROUTER_QUALIFIED_BACKENDS = frozenset({
    "incremental", "resolve_native_invocation",
})
_ROUTER_PROTECTED_BACKENDS = frozenset({"reel_touchup"})
_SEMANTIC_RESULT_LIMIT = 256


def register_edit_executor(operation_id: str, backend_id: str,
                           executor: EditExecutor, *, replace: bool = False
                           ) -> None:
    """Register one physical adapter against the existing operation matrix.

    A registration only declares that code can handle the pair. The
    execution router remains authoritative: submissions still require that
    the selected backend's matrix row is production enabled. This registry
    deliberately keys the existing ``edit_algebra.OP_STATUS`` identity and
    introduces no second operation vocabulary.
    """
    if not isinstance(operation_id, str) or not operation_id.strip():
        raise ValueError("executor registration needs an operation id")
    if not isinstance(backend_id, str) or not backend_id.strip():
        raise ValueError("executor registration needs a backend id")
    if not callable(executor):
        raise TypeError("executor registration must be callable")
    from library.tools import edit_algebra

    status = edit_algebra.status(operation_id)
    if status is None:
        raise ValueError(f"executor registration names unknown operation {operation_id!r}")
    row = status.for_backend(backend_id) if status is not None else None
    if ((row is None or not row.compilable)
            and backend_id not in _ROUTER_QUALIFIED_BACKENDS):
        raise ValueError(
            f"{operation_id} is not registered as compilable on {backend_id}")
    key = (operation_id, backend_id)
    with _EDIT_EXECUTOR_LOCK:
        if key in _EDIT_EXECUTORS and not replace:
            raise ValueError(
                f"an executor is already registered for {operation_id} on "
                f"{backend_id}")
        _EDIT_EXECUTORS[key] = executor


def registered_edit_executors() -> tuple[tuple[str, str], ...]:
    """Return registered operation/backend pairs in stable order."""
    with _EDIT_EXECUTOR_LOCK:
        return tuple(sorted(_EDIT_EXECUTORS))


def _execute_registered_backend(*, backend: str, plan: Mapping,
                                request: Any, preparation: Any,
                                compiled: Any, generation: Any,
                                snapshot: Mapping, owner: str) -> dict:
    """Run the router-selected adapters and preserve sequential semantics."""
    from library.tools import edit_algebra
    from library.tools.edit_execution_adapters import (
        register_builtin_edit_executors,
    )
    from library.tools.transaction_orchestration import (
        ExecutionInput,
        ExecutionRefusal,
        standardize_receipt,
    )

    register_builtin_edit_executors()
    backend_id = backend.removesuffix(".structural")
    operations = [dict(row) for row in compiled.operations]
    decision = plan.get("routing") or {}
    executors: list[tuple[int, EditExecutor]] = []
    for index, operation in enumerate(operations):
        operation_id = str(operation["op"])
        status = edit_algebra.status(operation_id)
        row = status.for_backend(backend_id) if status is not None else None
        if ((row is None or not row.production_enabled)
                and backend_id not in _ROUTER_QUALIFIED_BACKENDS
                and backend_id not in _ROUTER_PROTECTED_BACKENDS):
            raise ValueError(
                f"router selected {backend!r}, but {operation_id} is not "
                f"production enabled on {backend_id}")
        if (backend_id == "reel_touchup"
                and (row is None or not row.production_enabled)):
            selection = decision.get("selection") or {}
            if selection.get("qualification_policy") != "protected_experimental":
                raise ValueError(
                    f"router selected unqualified {operation_id} on "
                    "reel_touchup without protected experimental policy")
            if not any(gap[0] == operation_id
                       for gap in decision.get("qualification_gaps", ())):
                raise ValueError(
                    f"router omitted the qualification gap for "
                    f"{operation_id} on reel_touchup")
        if backend_id in _ROUTER_QUALIFIED_BACKENDS:
            routed_backend = str(decision.get("backend") or backend)
            if routed_backend != backend:
                raise ValueError(
                    f"{backend_id} adapter refused a mismatched routing decision "
                    f"({routed_backend!r})")
        with _EDIT_EXECUTOR_LOCK:
            executor = _EDIT_EXECUTORS.get((operation_id, backend_id))
        if executor is None:
            raise ValueError(
                f"router selected {backend!r}, but no callable adapter is "
                f"registered for {operation_id} on {backend_id}")
        executors.append((index, executor))

    if not executors:
        raise ValueError("routed semantic request contains no executable operations")

    execution = ExecutionInput(
        intent=request.to_dict(), decision=decision,
        project=request.project, timeline=request.timeline,
        base_generation=generation.generation,
        idempotency_key=request.idempotency_key,
        expected_state=preparation.as_dict(),
        preservation_constraints=tuple(
            str(value) for value in request.preconditions),
    )
    receipts: list[dict] = []

    def refused(category: str, reason: str, *, failed_index: int,
                evidence: Mapping | None = None) -> dict:
        refusal = ExecutionRefusal(
            category, reason, retryable=False,
            recovery={"status": "required" if receipts else "not_required",
                      "completed_operations": len(receipts)},
        ).as_dict()
        return {
            "status": ("partial_failure" if receipts
                       or (evidence or {}).get("mutated") else "failed"),
            "request_id": request.id, "backend": backend,
            "atomic": False, "atomicity": "sequential_non_atomic",
            "mutated": bool(receipts or (evidence or {}).get("mutated")),
            "completion_claimed": False,
            "partial_result_preserved": bool(
                receipts or (evidence or {}).get("mutated")),
            "completed_operations": receipts,
            "refusal": refusal, "failed_operation_index": failed_index,
            "failed_operation": (operations[failed_index]
                                 if failed_index < len(operations) else None),
            "failure_evidence": dict(evidence or {}),
            "recovery": {
                "status": "required" if receipts or (evidence or {}).get("mutated")
                else "not_required",
                "completed_operation_indices": [row["operation_index"]
                                                  for row in receipts],
                "failed_operation_index": failed_index,
                "failure": reason,
                "evidence": [row["receipt"].get("execution_contract", {}).get(
                    "recovery", {}) for row in receipts]
                    + ([dict((evidence or {}).get("adapter_receipt", {}).get(
                        "execution_contract", {}).get("recovery", {}))]
                       if (evidence or {}).get("adapter_receipt") else []),
            },
            "execution_routing": decision,
        }

    for index, executor in executors:
        context = EditExecutionContext(
            execution=execution, request=request, preparation=preparation,
            compiled=compiled, generation=generation, snapshot=snapshot,
            operation=operations[index], operation_index=index, owner=owner,
        )
        try:
            result = executor(context)
        except Exception as exc:  # noqa: BLE001 - preserve partial result
            return refused("backend_failure",
                           f"{type(exc).__name__}: {exc}",
                           failed_index=index)
        if not isinstance(result, Mapping):
            return refused(
                "verification_failed",
                f"adapter for {operations[index]['op']} on {backend_id} "
                "did not return a receipt mapping", failed_index=index)
        receipt = (dict(result) if result.get("execution_contract")
                   else standardize_receipt(result))
        verification = (receipt.get("execution_contract") or {}).get(
            "verification", {})
        if verification.get("status") != "verified":
            return refused(
                "verification_failed",
                f"adapter for {operations[index]['op']} on {backend_id} "
                "returned without verified read-back evidence",
                failed_index=index,
                evidence={"adapter_receipt": receipt,
                          "mutated": receipt.get("mutated", True)},
            )
        receipts.append({"operation_index": index,
                         "operation": operations[index],
                         "target_identity": {
                             key: operations[index][key]
                             for key in ("unique_id", "client_unique_id")
                             if key in operations[index]},
                         "receipt": receipt})

    return {
        "status": "committed", "request_id": request.id,
        "project": request.project, "timeline": request.timeline,
        "backend": backend, "atomic": False,
        "atomicity": "sequential_non_atomic", "mutated": True,
        "operations": receipts,
        "execution_routing": decision,
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
    from library.tools.edit_execution_adapters import (
        register_builtin_edit_executors,
    )
    from library.tools.edit_request import (
        EditRequest,
        compile_request,
        route_request,
    )

    register_builtin_edit_executors()

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
        route_context = {}
        originator_name = str(
            (request.originator or {}).get("name", "")).strip().casefold()
        operation_ids = tuple(
            str(operation.get("op", "")) for operation in compiled.operations)
        if (originator_name in {"ren revise", "ren touch"}
                and len(operation_ids) == 1
                and operation_ids[0] in {
                    "audio.gain", "audio.fade", "transition",
                    "overlay.resize", "clip.split", "clip.slide",
                    "clip.roll"}):
            route_context = {
                "qualification_policy": "protected_experimental",
                "protected_staging": True,
                "preserve_approved_state": True,
                "verify_after_write": True,
            }
        routing = route_request(compiled, context=route_context)
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
        receipt = _execute_registered_backend(
            backend=backend, plan=plan, request=request,
            preparation=_prepared, compiled=compiled,
            generation=generation, snapshot=snapshot, owner=owner)
        handle = f"edit:{request.id}"
        with _EDIT_EXECUTOR_LOCK:
            _SEMANTIC_EXECUTION_RESULTS[handle] = receipt
            while len(_SEMANTIC_EXECUTION_RESULTS) > _SEMANTIC_RESULT_LIMIT:
                _SEMANTIC_EXECUTION_RESULTS.pop(
                    next(iter(_SEMANTIC_EXECUTION_RESULTS)))
        return {"id": handle, "request_id": request.id,
                "backend": backend,
                "status": receipt["status"], "plan": plan,
                "result": receipt, "receipt": receipt}
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
    with _EDIT_EXECUTOR_LOCK:
        semantic_result = _SEMANTIC_EXECUTION_RESULTS.get(job_id)
    if semantic_result is not None:
        return {"id": job_id, "status": semantic_result.get("status"),
                "result": semantic_result,
                "error": semantic_result.get("refusal")}
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
    if submitted.get("receipt") is not None:
        return submitted["receipt"]
    job_id = submitted.get("id")
    if not isinstance(job_id, str) or not job_id:
        raise RuntimeError("broker did not return an edit job id")
    return retrieve_edit_request(job_id, wait=timeout)


def cancel_edit_request(job_id: str) -> dict[str, Any]:
    """Request cancellation only through the broker's declared safe path."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job id must be a non-empty string")
    with _EDIT_EXECUTOR_LOCK:
        if job_id in _SEMANTIC_EXECUTION_RESULTS:
            return {"id": job_id, "status": "not_cancellable",
                    "reason": "the synchronous adapter has completed"}
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
