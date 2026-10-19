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
