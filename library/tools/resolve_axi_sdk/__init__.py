"""In-process Python SDK for the shared Resolve AXI control contract."""

from __future__ import annotations

from collections.abc import Iterable

from library.tools.resolve_axi_core import (
    AxiOperation,
    AxiResult,
    AxiTransaction,
    cancel_edit_request,
    execute,
    plan_transaction,
    prepare_edit_request,
    retrieve_edit_request,
    retrieve_transaction,
    submit_edit_request,
    submit_edit_request_async,
    submit_transaction,
    submit_transaction_async,
)


def call(command: str, *arguments: str) -> AxiResult:
    """Call one AXI command without a subprocess.

    ``command`` is the first CLI word (for example ``"project"`` or
    ``"edit"``); remaining words are passed as individual argument values.
    Existing AXI ``--apply`` rules remain authoritative for writes.
    """
    return execute(AxiOperation(command, tuple(arguments)))


def transaction(operations: Iterable[AxiOperation]) -> tuple[AxiResult, ...]:
    """Execute typed commands in order and stop at the first failure.

    This convenience batch preserves each command's own receipt and guard.
    It intentionally does not claim atomicity across independent legacy
    commands; compound durable edits use the transaction coordinator.
    """
    results = []
    for operation in operations:
        result = execute(operation)
        results.append(result)
        if not result.ok:
            break
    return tuple(results)


def apply_transaction(
    transaction, *, owner: str = "resolve-axi", timeout: float = 120.0
) -> dict:
    """Apply one typed, multi-op EditPatch through the Resolve broker."""
    return submit_transaction(transaction, owner=owner, timeout=timeout)


def plan(transaction) -> dict:
    """Validate a transaction or edit request without Resolve access.

    An `EditRequest` plans meaning without inventing a backend - the
    returned plan requires a capability choice (the router) before
    submission.
    """
    return plan_transaction(transaction)


def submit_transaction_job(
    transaction, *, owner: str = "resolve-axi"
) -> dict:
    """Queue a typed transaction and return its broker job handle."""
    return submit_transaction_async(transaction, owner=owner)


def get_transaction_job(job_id: str, *, wait: float = 0.0) -> dict:
    """Retrieve a submitted transaction's result or current job receipt."""
    return retrieve_transaction(job_id, wait=wait)


def prepare_edit(request) -> dict:
    """Prepare and route semantic intent without Resolve contact."""
    return prepare_edit_request(request)


def submit_edit(request, *, authorized: bool = False,
                owner: str = "resolve-axi") -> dict:
    """Queue semantic intent through its router-selected AXI backend."""
    return submit_edit_request_async(request, authorized=authorized,
                                     owner=owner)


def apply_edit(request, *, authorized: bool = False,
               owner: str = "resolve-axi", timeout: float = 120.0) -> dict:
    """Submit semantic intent and wait for its verified receipt."""
    return submit_edit_request(request, authorized=authorized,
                               owner=owner, timeout=timeout)


def get_edit_job(job_id: str, *, wait: float = 0.0) -> dict:
    return retrieve_edit_request(job_id, wait=wait)


def cancel_edit_job(job_id: str) -> dict:
    return cancel_edit_request(job_id)


__all__ = [
    "AxiOperation",
    "AxiResult",
    "AxiTransaction",
    "apply_edit",
    "apply_transaction",
    "call",
    "cancel_edit_job",
    "get_edit_job",
    "get_transaction_job",
    "plan",
    "prepare_edit",
    "submit_edit",
    "submit_transaction_job",
    "transaction",
]
