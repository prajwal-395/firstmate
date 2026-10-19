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


def _plan_request(request) -> dict[str, Any]:
    """Validate an edit request against its recorded base, without Resolve.

    The request names meaning, not a backend: no capability is
    invented here. The plan carries the compiled ops, the affected
    scope and the preparation verdict, and requires a capability
    choice (the router) plus authorization before submission.
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
    return {
        "status": "planned",
        "request_id": request.id,
        "transaction_id": request.id,
        "project": request.project,
        "timeline": request.timeline,
        "base_generation": generation.generation,
        "backend": None,
        "backend_reason": ("a request names meaning, not a backend - "
                           "the router assigns one from qualification"),
        "needs_capability": True,
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


def plan_transaction(transaction) -> dict[str, Any]:
    """Validate a transaction against its recorded base without Resolve.

    Accepts a typed `AxiTransaction` (the qualified-executor path:
    EditPatch only) or an `EditRequest` (meaning without a backend -
    no capability is invented, the router assigns one). EditPatch is
    the only qualified executor represented by the typed transaction
    contract. The plan says so explicitly and requires separate
    authorization before submission; it never queues or mutates
    anything.
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
