"""In-process Python SDK for the shared Resolve AXI control contract."""

from __future__ import annotations

from collections.abc import Iterable

from library.tools.resolve_axi_core import (
    AxiOperation,
    AxiResult,
    AxiTransaction,
    execute,
    submit_transaction,
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
    transaction: AxiTransaction, *, owner: str = "resolve-axi", timeout: float = 120.0
) -> dict:
    """Apply one typed, multi-op EditPatch through the Resolve broker."""
    return submit_transaction(transaction, owner=owner, timeout=timeout)


__all__ = [
    "AxiOperation",
    "AxiResult",
    "AxiTransaction",
    "call",
    "transaction",
    "apply_transaction",
]
