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
_EDIT_EXECUTOR_LOCK = threading.RLock()
_ROUTER_QUALIFIED_BACKENDS = frozenset({
    "incremental", "resolve_native_invocation",
})
_ROUTER_PROTECTED_BACKENDS = frozenset({"reel_touchup"})


class RegisteredBatchPreflightRefused(ValueError):
    """A multi-operation batch refused before its first write."""


def _registered_batch_supported(backend_id: str,
                                operations: Sequence[Mapping]) -> bool:
    """Whether the existing adapters can recover this exact batch shape.

    Keep this deliberately narrower than operation compatibility in the
    abstract algebra: a batch is supported only when its physical executor
    and its compensation path are both already available.
    """
    operation_ids = tuple(str(operation["op"]) for operation in operations)
    operation_set = set(operation_ids)
    if (backend_id == "resolve_axi"
            and operation_set <= {"clip.trim", "clip.delete"}):
        return True
    if backend_id == "resolve_axi" and operation_set == {"caption.style"}:
        return True
    if (backend_id == "reel_touchup"
            and operation_set == {"overlay.position", "overlay.resize"}
            and len(operations) == 2):
        targets = [str(operation.get("unique_id") or "")
                   for operation in operations]
        return bool(targets[0]) and targets[0] == targets[1]
    return len(operations) <= 1


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


def _compensate_registered_operations(receipts: Sequence[Mapping], *,
                                      timeline: str,
                                      failure_evidence: Mapping | None = None
                                      ) -> dict:
    """Undo journaled registered writes in reverse order and verify each."""
    from library.tools import undo_journal

    actions = []
    evidence = dict(failure_evidence or {})
    uncertain_failure = evidence.get("mutation_state") == "unknown"
    failed_receipt = evidence.get("adapter_receipt")
    rows = list(reversed(receipts))
    if failed_receipt is not None:
        rows.insert(0, {
            "operation_index": int(evidence["operation_index"]),
            "receipt": failed_receipt,
        })
    for row in rows:
        recovery = (row["receipt"].get("execution_contract", {})
                    .get("recovery", {}))
        reference = recovery.get("reference") or {}
        if reference.get("kind") == "caption_swap_back":
            try:
                from library.tools import operations

                operation = operations.get("subtitles.rerender_swap")
                result = operation.run(
                    reference["project_folder"],
                    [{"old_mov": reference["current_file"],
                      "timeline_label": timeline,
                      "props": reference["previous_props"]}],
                    swap=True)
                restored = (result.get("ok") and
                            (result.get("map") or {}).get(
                                reference["current_file"])
                            == reference["previous_file"])
                if not restored:
                    raise RuntimeError(
                        f"caption swap-back did not render and map the "
                        f"previous generation: {result}")
                # The render-and-swap operation performs per-placement
                # read-back and refuses while any current file remains.
                actions.append({
                    "operation_index": row["operation_index"],
                    "verified": True,
                    "kind": "caption_swap_back",
                    "current_file": reference["current_file"],
                    "previous_file": reference["previous_file"],
                })
                continue
            except Exception as exc:  # noqa: BLE001 - retain partial recovery
                completed = {action["operation_index"] for action in actions}
                return {
                    "status": "recovery_required",
                    "reason": (f"caption swap-back for operation "
                               f"{row['operation_index']} failed: "
                               f"{type(exc).__name__}: {exc}"),
                    "actions": actions,
                    "failed_operation_index": row["operation_index"],
                    "remaining_operations": [item["operation_index"]
                                             for item in receipts
                                             if item["operation_index"]
                                             not in completed],
                }
        if reference.get("kind") != "undo_journal_entry":
            completed = {action["operation_index"] for action in actions}
            return {
                "status": "recovery_required",
                "reason": "a completed operation has no undo journal reference",
                "actions": actions,
                "remaining_operations": [item["operation_index"]
                                         for item in receipts
                                         if item["operation_index"] not in completed],
            }
        try:
            result = undo_journal.undo(
                str(reference["project_folder"]), final=timeline,
                act=str(reference["entry_id"]))
            if (not isinstance(result, list) or len(result) != 1
                    or not isinstance(result[0], Mapping)
                    or result[0].get("verified") is not True):
                raise RuntimeError(
                    "undo journal did not return one explicitly verified "
                    "restoration")
        except Exception as exc:  # noqa: BLE001 - preserve partial state
            completed = {action["operation_index"] for action in actions}
            return {
                "status": "recovery_required",
                "reason": f"undo of operation {row['operation_index']} "
                f"failed: {type(exc).__name__}: {exc}",
                "actions": actions,
                "failed_operation_index": row["operation_index"],
                "remaining_operations": [item["operation_index"]
                                         for item in receipts
                                         if item["operation_index"] not in completed],
            }
        actions.append({
            "operation_index": row["operation_index"],
            "entry_id": reference["entry_id"],
            "verified": True,
            "receipt": result[0],
        })
    if uncertain_failure:
        return {
            "status": "recovery_required",
            "reason": "the failed operation may have mutated without a recovery receipt",
            "failed_operation_index": evidence.get("operation_index"),
            "actions": actions,
            "remaining_operations": [evidence.get("operation_index")],
        }
    return {"status": "restored_original_verified",
            "actions": actions,
            "independent_readback": "each undo journal restore verified its live read-back"}


def _execute_registered_delete_batch(*, request, compiled, generation,
                                     snapshot, decision, owner: str) -> dict:
    """Commit compatible local deletes as one verified EditPatch."""
    from library.tools import edit_patch, project_registry, timeline_addressing
    from library.tools.resolve_axi_core_commands import (
        _assert_cursor_on,
        _connect,
        _item_span,
        _project,
        _target_timeline,
    )

    try:
        resolve = _connect()
        project = _project(resolve, request.project)
        timeline, _is_current, _note = _target_timeline(project, request.timeline)
        _assert_cursor_on(resolve, timeline.GetName())
        prepared = {
            str(clip["unique_id"]): clip
            for track in snapshot["tracks"] for clip in track["clips"]
            if clip.get("unique_id")
        }
        target_ids = [str(operation["unique_id"])
                      for operation in compiled.operations]
        if len(set(target_ids)) != len(target_ids):
            raise ValueError("a delete batch cannot target the same item twice")
        target_evidence = []
        for target_id in target_ids:
            clip = prepared.get(target_id)
            if clip is None:
                raise ValueError(
                    f"delete target {target_id!r} is absent from the prepared snapshot")
            selector = {
                "resolve_item_id": target_id,
                "source_file": str(clip["source_file"]),
                "source_in_frame": int(clip["source_in_frame"]),
                "source_out_frame": int(clip["source_out_frame"]),
                "track_type": str(clip.get("track_type") or "video"),
            }
            address = timeline_addressing.resolve_item(timeline, selector)
            timeline_addressing.verify_item(timeline, address)
            live_span = _item_span(address.item)
            expected_record = (int(clip["record_in"]), int(clip["record_out"]))
            actual_record = (int(live_span["record_in"]),
                             int(live_span["record_out"]))
            if actual_record != expected_record:
                raise timeline_addressing.StaleTarget(
                    f"delete target {target_id!r} changed record span from "
                    f"{expected_record} to {actual_record} since preparation")
            target_evidence.append({
                "unique_id": target_id, "source_file": selector["source_file"],
                "source_in_frame": selector["source_in_frame"],
                "source_out_frame": selector["source_out_frame"],
                "record_in": actual_record[0], "record_out": actual_record[1],
            })
        project_path = project_registry.resolve_project_path(request.project)
        if project_path is None:
            config = project_registry.get_project(request.project)
            project_folder = str(config.project_root)
        else:
            project_folder = str(project_path.parent)
    except Exception as exc:
        raise RegisteredBatchPreflightRefused(str(exc)) from exc
    receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline,
        capability=_patch_capability(compiled),
        operations=compiled.operations,
        conflict_domains=compiled.conflict_domains,
        idempotency_key=request.idempotency_key or request.id,
        project_folder=project_folder,
    )
    operation_results = receipt.get("operations") or []
    operation_receipts = [
        {
            "operation_index": index,
            "operation": dict(operation),
            "target_identity": target_evidence[index],
            "receipt": (operation_results[index]
                        if index < len(operation_results) else {}),
        }
        for index, operation in enumerate(compiled.operations)
    ]
    return {
        "status": receipt["status"],
        "request_id": request.id,
        "project": request.project,
        "timeline": request.timeline,
        "backend": "edit_patch",
        "selected_backend": "resolve_axi",
        "atomic": receipt["status"] == "committed",
        "atomicity": "single_edit_patch_commit",
        "base_generation": generation.generation,
        "mutated": receipt.get("mutated", True),
        "operations": operation_receipts,
        "target_identities": target_evidence,
        "transaction_receipt": receipt,
        "execution_routing": decision,
        "owner": owner,
    }


def _revalidate_registered_targets(*, request, operations, snapshot) -> list[dict]:
    """Check each stable item and media identity before a batch starts."""
    from library.tools import timeline_addressing
    from library.tools.resolve_axi_core_commands import (
        _connect,
        _item_span,
        _project,
        _target_timeline,
    )

    resolve = _connect()
    project = _project(resolve, request.project)
    timeline, _is_current, _note = _target_timeline(project, request.timeline)
    prepared = {
        str(clip["unique_id"]): (clip, str(track["type"]))
        for track in snapshot["tracks"] for clip in track["clips"]
        if clip.get("unique_id")
    }
    evidence = []
    for operation in operations:
        target_id = str(operation.get("unique_id") or "")
        if not target_id:
            continue
        item = prepared.get(target_id)
        if item is None:
            raise ValueError(
                f"batch target {target_id!r} is absent from its prepared snapshot")
        clip, track_type = item
        selector = {
            "resolve_item_id": target_id,
            "source_file": str(clip["source_file"]),
            "source_in_frame": int(clip["source_in_frame"]),
            "source_out_frame": int(clip["source_out_frame"]),
            "track_type": track_type,
        }
        address = timeline_addressing.resolve_item(timeline, selector)
        timeline_addressing.verify_item(timeline, address)
        live_span = _item_span(address.item)
        expected = (int(clip["record_in"]), int(clip["record_out"]))
        actual = (int(live_span["record_in"]), int(live_span["record_out"]))
        if actual != expected:
            raise timeline_addressing.StaleTarget(
                f"batch target {target_id!r} changed record span from "
                f"{expected} to {actual} since preparation")
        evidence.append({**selector, "record_in": actual[0],
                         "record_out": actual[1]})
    return evidence


def _validate_caption_style_batch(operations: Sequence[Mapping],
                                 snapshot: Mapping) -> None:
    """Preflight every card in one consistent, non-aliasing restyle batch."""
    if len(operations) < 2 or any(
            str(operation["op"]) != "caption.style"
            for operation in operations):
        return
    styles = {str(operation["style"]) for operation in operations}
    if len(styles) != 1:
        raise ValueError(
            "caption.style batch must apply one supplied style to every "
            "caption")
    target_ids = [str(operation["unique_id"]) for operation in operations]
    if len(set(target_ids)) != len(target_ids):
        raise ValueError(
            "caption.style batch repeats a caption identity; each caption "
            "must be addressed once")
    targets = {}
    for track in snapshot["tracks"]:
        for clip in track["clips"]:
            if str(clip["unique_id"]) in target_ids:
                targets.setdefault(str(clip["unique_id"]), []).append(
                    (track, clip))
    for unique_id in target_ids:
        matches = targets.get(unique_id, [])
        if len(matches) != 1:
            raise ValueError(
                f"caption.style target {unique_id!r} resolves to "
                f"{len(matches)} prepared items; no captions were changed")
        track, clip = matches[0]
        if track.get("name") != "Subtitles" or not clip.get("source_file"):
            raise ValueError(
                f"caption.style target {unique_id!r} is not a rendered "
                "Ren caption on the Subtitles row; no captions were changed")
    source_files = [str(targets[unique_id][0][1]["source_file"])
                    for unique_id in target_ids]
    if len(set(source_files)) != len(source_files):
        raise ValueError(
            "caption.style batch contains multiple placements of one "
            "rendered caption file; one pool-item swap would affect them "
            "together, so no captions were changed")


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
    routed_backend = str(decision.get("backend") or "")
    if routed_backend != backend:
        raise ValueError(
            f"dispatcher received {backend!r}, but router selected "
            f"{routed_backend or 'no backend'!r}")
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
        with _EDIT_EXECUTOR_LOCK:
            executor = _EDIT_EXECUTORS.get((operation_id, backend_id))
        if executor is None:
            raise ValueError(
                f"router selected {backend!r}, but no callable adapter is "
                f"registered for {operation_id} on {backend_id}")
        executors.append((index, executor))

    if not executors:
        raise ValueError("routed semantic request contains no executable operations")
    if not _registered_batch_supported(backend_id, operations):
        raise ValueError(
            f"{backend_id} has no verified batch recovery strategy for this "
            "multi-operation request; no operation was applied")
    _validate_caption_style_batch(operations, snapshot)

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
        failure_receipt = {
            "status": ("partial_failure" if receipts
                       or (evidence or {}).get("mutated") else "failed"),
            "resolve_write_status": "failed",
            "patch_id": request.id,
            "request_id": request.id, "backend": backend,
            "transaction_id": request.id,
            "idempotency_key": request.idempotency_key,
            "project": request.project, "timeline": request.timeline,
            "atomic": False, "atomicity": "sequential_non_atomic",
            "base_generation": generation.generation,
            "concurrency": "exclusive_broker_lease_for_batch",
            "mutated": bool(receipts or (evidence or {}).get("mutated")),
            "completion_claimed": False,
            "partial_result_preserved": bool(
                receipts or (evidence or {}).get("mutated")),
            "completed_operations": receipts,
            "operations": receipts,
            "requested_operation_count": len(operations),
            "expected_changes": operations,
            "actual_changes": receipts,
            "affected_items": sorted({
                str(op["unique_id"]) for op in operations if op.get("unique_id")}),
            "verification_evidence": [row["receipt"].get(
                "execution_contract", {}).get("verification", {})
                for row in receipts],
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
        from library.tools.transaction_orchestration import standardize_receipt
        return standardize_receipt(failure_receipt)

    def compensate_completed(failure_evidence: Mapping | None = None) -> dict:
        return _compensate_registered_operations(
            receipts, timeline=request.timeline,
            failure_evidence=failure_evidence)

    def run_operations() -> dict | None:
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
                               failed_index=index,
                               evidence={"mutation_state": "unknown",
                                         "mutated": True,
                                         "operation_index": index,
                                         "error": repr(exc)})
            if not isinstance(result, Mapping):
                return refused(
                    "verification_failed",
                    f"adapter for {operations[index]['op']} on {backend_id} "
                    "did not return a receipt mapping", failed_index=index,
                    evidence={"mutation_state": "unknown", "mutated": True,
                              "operation_index": index})
            receipt = (dict(result) if result.get("execution_contract")
                       else standardize_receipt(result))
            verification = (receipt.get("execution_contract") or {}).get(
                "verification", {})
            if (verification.get("status") != "verified"
                    or not verification.get("checks")
                    or not isinstance(verification.get("evidence"), Mapping)
                    or not verification.get("evidence")):
                return refused(
                    "verification_failed",
                    f"adapter for {operations[index]['op']} on {backend_id} "
                    "returned without operation-level verification evidence",
                    failed_index=index,
                    evidence={"adapter_receipt": receipt,
                              "mutated": receipt.get("mutated", True),
                              "operation_index": index},
                )
            receipts.append({"operation_index": index,
                             "operation": operations[index],
                             "target_identity": dict(
                                 receipt.get("target_identity") or {
                                     key: operations[index][key]
                                     for key in ("unique_id", "client_unique_id")
                                     if key in operations[index]}),
                             "receipt": receipt})
        return None

    from library.tools.resolve_lock import resolve_lease

    batch_receipt = None
    # Hold the broker lease across the entire batch so another Ren
    # transaction cannot interleave between its operations.
    with resolve_lease(
            "resolve-axi-operation-batch", exclusive=True, owner=owner,
            project=request.project, timeline=request.timeline,
            edit_ids=tuple(str(operation.get("unique_id", ""))
                           for operation in operations)):
        from library.tools import timeline_shadow

        current = [
            (head, live_snapshot)
            for head, live_snapshot in
            timeline_shadow.ShadowStore().current_snapshots(request.project)
            if head.timeline_name == request.timeline
        ]
        if len(current) != 1 or current[0][0].generation != generation.generation:
            refusal = refused(
                "stale", "timeline generation changed after preparation; "
                "the batch was not applied", failed_index=0)
        elif (len(operations) > 1
              and all(str(operation["op"]) == "clip.delete"
                      for operation in operations)):
            try:
                batch_receipt = _execute_registered_delete_batch(
                    request=request, compiled=compiled,
                    generation=generation, snapshot=snapshot,
                    decision=decision, owner=owner)
                refusal = None
            except Exception as exc:  # noqa: BLE001 - patch owns recovery
                preflight_refused = isinstance(
                    exc, RegisteredBatchPreflightRefused)
                refusal = refused(
                    "backend_failure",
                    f"single EditPatch delete batch failed: "
                    f"{type(exc).__name__}: {exc}", failed_index=0,
                    evidence={"mutation_state": ("none" if preflight_refused
                                                 else "unknown"),
                              "mutated": not preflight_refused,
                              "operation_index": 0, "error": repr(exc)})
        elif len(operations) > 1 and backend_id == "resolve_axi":
            try:
                _revalidate_registered_targets(
                    request=request, operations=operations, snapshot=snapshot)
                refusal = run_operations()
            except Exception as exc:  # noqa: BLE001 - read-only preflight
                refusal = refused(
                    "stale", f"batch target preflight failed: "
                    f"{type(exc).__name__}: {exc}", failed_index=0,
                    evidence={"mutation_state": "none", "mutated": False,
                              "operation_index": 0, "error": repr(exc)})
        else:
            refusal = run_operations()
        if refusal is not None and receipts:
            recovery = compensate_completed(refusal.get("failure_evidence"))
            recovery["failure_reason"] = refusal["refusal"]["reason"]
            recovery["failed_operation_index"] = refusal[
                "failed_operation_index"]
            refusal["recovery"] = recovery
        elif refusal is not None and refusal.get("mutated"):
            refusal["recovery"] = compensate_completed(
                refusal.get("failure_evidence"))
    if refusal is not None:
        return refusal
    if batch_receipt is not None:
        return batch_receipt

    from library.tools.timeline_shadow import ShadowStore
    heads = [head for head, _snapshot in
             ShadowStore().current_snapshots(request.project)
             if head.timeline_name == request.timeline]
    committed_generation = heads[0].generation if len(heads) == 1 else None
    if (committed_generation is None
            or committed_generation <= generation.generation):
        return refused(
            "verification_failed",
            "backend operations were verified but no newer committed "
            "timeline generation was recorded",
            failed_index=len(operations),
            evidence={"base_generation": generation.generation,
                      "observed_generation": committed_generation,
                      "mutated": True},
        )
    return _registered_execution_receipt(
        backend=backend, plan=plan, request=request,
        operations=operations, receipts=receipts,
        base_generation=generation.generation,
        committed_generation=committed_generation)


def _registered_execution_receipt(*, backend: str, plan: Mapping,
                                  request: Any, operations: Sequence[Mapping],
                                  receipts: Sequence[Mapping],
                                  base_generation: int,
                                  committed_generation: int) -> dict:
    """Normalize a registered-backend batch with operation evidence."""
    from library.tools.transaction_orchestration import standardize_receipt

    acceptance_contracts = []
    for operation, result in zip(operations, receipts):
        receipt = result.get("receipt", result)
        verification = receipt["execution_contract"]["verification"]
        checks = verification.get("checks") or []
        evidence = verification.get("evidence")
        passed = (verification.get("status") == "verified"
                  and bool(checks) and isinstance(evidence, Mapping)
                  and bool(evidence))
        acceptance_contracts.append({
            "operation": operation["op"], "passed": passed,
            "checks": list(checks), "evidence": dict(evidence or {}),
            "failures": [] if passed else [
                "operation-level verification evidence is missing"],
        })
    verified = bool(acceptance_contracts) and all(
        row["passed"] for row in acceptance_contracts)
    aggregate = {
        "status": "committed" if verified else "verification_failed",
        "resolve_write_status": "committed" if verified else "verification_failed",
        "patch_id": request.id, "request_id": request.id,
        "transaction_id": request.id,
        "idempotency_key": request.idempotency_key,
        "project": request.project, "timeline": request.timeline,
        "backend": backend, "generation": committed_generation,
        "base_generation": base_generation,
        "atomic": False, "atomicity": "sequential_non_atomic",
        "concurrency": "exclusive_broker_lease_for_batch",
        "mutated": True,
        "affected_items": sorted({
            str(op["unique_id"]) for op in operations if op.get("unique_id")}),
        "expected_state": {"operations": operations},
        "expected_changes": operations,
        "actual_state": {"operation_receipts": receipts},
        "actual_changes": receipts,
        "readback": [result.get("receipt", result).get("readback")
                     for result in receipts],
        "verification_evidence": acceptance_contracts,
        "operations": receipts,
        "requested_operation_count": len(operations),
        "acceptance_contracts": acceptance_contracts,
        "execution_routing": plan.get("routing") or {},
        "recovery": {"status": "available", "operations": [
            result.get("receipt", result).get("execution_contract", {}).get(
                "recovery", {}) for result in receipts]},
        "promotion": {"status": "awaiting_promotion"},
        "delivered": {"status": "not_requested"},
    }
    return standardize_receipt(aggregate)


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


def read_live_timeline(project: str, timeline: str) -> dict:
    """Return a fresh live snapshot through the AXI timeline reader."""
    from library.tools import resolve_axi_core_commands

    view, _note = resolve_axi_core_commands._timeline_view(
        timeline, project, caller="resolve_axi_core.read_live_timeline",
        refresh_live=True, exact=True)
    return dict(view.snapshot)


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
                    "overlay.resize", "overlay.position",
                    "clip.split", "clip.slide",
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


def execute_semantic_edit_job(job: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one broker-owned semantic edit against its queued generation."""
    request = _request_from_any(job["params"]["request"])
    plan, request, preparation, compiled, generation, snapshot = (
        _prepare_request_plan(request))
    expected_generation = job["params"]["base_generation"]
    if generation.generation != expected_generation:
        raise ValueError(
            f"semantic edit base generation {expected_generation} is stale; "
            f"recorded head is {generation.generation}")
    backend = str(plan["backend"])
    if backend != job["params"]["backend"]:
        raise ValueError(
            f"semantic edit route changed while queued: "
            f"{job['params']['backend']} to {backend}")
    return _execute_registered_backend(
        backend=backend, plan=plan, request=request,
        preparation=preparation, compiled=compiled, generation=generation,
        snapshot=snapshot, owner=str(job.get("owner") or "resolve-axi"))


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
    plan, request, _prepared, compiled, generation, _snapshot = (
        _prepare_request_plan(request))
    if plan["backend"] == "no_op":
        from library.tools.transaction_receipt import lifecycle
        receipt = {"status": "already_satisfied", "patch_id": request.id,
                   "request_id": request.id, "transaction_id": request.id,
                   "idempotency_key": request.idempotency_key,
                   "project": request.project, "timeline": request.timeline,
                   "backend": "no_op", "mutated": False,
                   "affected_items": [], "expected_changes": [],
                   "actual_changes": [], "verification_evidence": {
                       "outcome": "already_satisfied"},
                   "generation": generation.generation,
                   "promotion": {"status": "not_required"},
                   "delivered": {"status": "not_requested"},
                   "recovery": {"status": "not_required"},
                   "execution_routing": plan["routing"],
                   "execution_contract": {
                       "status": "already_satisfied", "backend": "no_op",
                       "verification": {"status": "verified", "checks": ({
                           "operation": "request", "passed": True},),
                           "missing": (),
                           "evidence": {"resolve_contacted": False}},
                       "recovery": {"status": "not_required",
                                    "action": "none"}}}
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
        from library.tools.resolved import client
        job = client.submit(
            "timeline.semantic_edit",
            {"request": request.to_dict(), "backend": backend,
             "base_generation": generation.generation},
            owner=owner, idempotency_key=request.idempotency_key)
        if job.get("coalesced"):
            current = retrieve_edit_request(str(job["id"]))
            return {**current, "request_id": request.id, "backend": backend,
                    "plan": plan}
        return {**job, "request_id": request.id, "backend": backend,
                "plan": plan, "status": "queued"}
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
    if isinstance(result, Mapping) and "execution_contract" not in result:
        from library.tools.transaction_orchestration import standardize_receipt
        result = standardize_receipt(dict(result))
    state = job.get("state", job.get("status"))
    status = {"done": "completed", "rejected": "refused"}.get(state, state)
    return {"id": job_id, "status": status, "state": state,
            "result": result, "error": job.get("error"),
            "wait_seconds": job.get("wait_seconds"),
            "hold_seconds": job.get("hold_seconds")}


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
    completed = retrieve_edit_request(job_id, wait=timeout)
    if completed.get("status") == "completed" and isinstance(
            completed.get("result"), Mapping):
        receipt = dict(completed["result"])
        receipt["job_timing"] = {
            "wait_seconds": completed.get("wait_seconds"),
            "resolve_lease_seconds": completed.get("hold_seconds"),
        }
        return receipt
    return completed


def cancel_edit_request(job_id: str) -> dict[str, Any]:
    """Request cancellation only through the broker's declared safe path."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job id must be a non-empty string")
    from library.tools.resolved import client
    reply = client.call({"op": "cancel", "id": job_id})
    job = reply.get("job")
    if not isinstance(job, Mapping):
        return reply
    state = job.get("state")
    status = {"done": "completed", "rejected": "refused"}.get(state, state)
    return {**reply, "id": job_id, "state": state, "status": status,
            "cancellation_requested": state == "running"}


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
