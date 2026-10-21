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

from library.tools.refusal_taxonomy import failure_category_for_blocker
from library.tools.ren_refusal import RenRefusal


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
    """One generation-pinned transaction containing several native ops.

    AXI-004: this is the LEGACY carrier of a request. It is not a
    second meaning: ``to_edit_request`` reads it as the canonical
    ``EditRequest``, and the durable patch it becomes is compiled from
    THAT request (``library.tools.legacy_transaction``). The carrier
    stays, because it is the shape the SDK's callers hand over, and
    because the broker's durable key is the patch's id.
    """

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
    preservation: tuple[Mapping[str, Any], ...] = ()
    """What this transaction must NOT change, in the requester's words.

    The carrier used to have nowhere to put this, so a request that
    named a preservation requirement lost it before the write."""

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
            "preservation": [dict(value) for value in self.preservation],
        }

    def to_edit_request(self):
        """The canonical request this carrier carries (AXI-004)."""
        from library.tools import legacy_transaction

        return legacy_transaction.request_for(self)


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
_ROUTER_PROTECTED_BACKENDS = frozenset({"reel_touchup", "resolve_axi"})


class RegisteredBatchPreflightRefused(ValueError):
    """A multi-operation batch refused before its first write."""


def _registered_batch_supported(backend_id: str,
                                operations: Sequence[Mapping]) -> bool:
    """Whether the existing adapters can recover this exact batch shape.

    Keep this deliberately narrower than operation compatibility in the
    abstract algebra: a batch is supported only when its physical executor
    and its compensation path are both already available. The declared
    shapes live in ``axi_transaction.COMPOUND_SCENARIOS``, so this
    answers from the same registry the preflight and the receipt read.
    """
    from library.tools import axi_transaction

    return axi_transaction.batch_supported(backend_id, operations)


def _verify_registered_full_request(*, operations: Sequence[Mapping],
                                    before: Mapping,
                                    after: Mapping,
                                    affected_spans: Sequence[Sequence[int]],
                                    conflict_domains: Sequence[str] = (),
                                    receipts: Sequence[Mapping]
                                    ) -> dict:
    """Judge the whole request against one fresh post-write snapshot."""
    from library.tools import edit_algebra
    from library.tools.transaction_orchestration import unrelated_clip_changes

    failures = edit_algebra.verifies(operations, after)
    checks = [
        {"operation_index": index, "operation": str(operation["op"]),
         "passed": failure is None, "failure": failure}
        for index, (operation, failure) in enumerate(zip(operations, failures))
    ]
    acceptance_checks = []
    for index, row in enumerate(receipts):
        contracts = row["receipt"].get("acceptance_contracts") or []
        acceptance_checks.append({
            "operation_index": int(row["operation_index"]),
            "operation": str(operations[index]["op"]),
            "passed": bool(contracts)
            and all(contract.get("passed") is True
                    for contract in contracts),
            "acceptance_contracts": contracts,
        })
    collateral = unrelated_clip_changes(
        operations=operations, affected_spans=affected_spans,
        id_map=None, before=before, after=after)
    from library.tools.transaction_orchestration import (
        _preservation_verdict,
    )

    preservation = _preservation_verdict(
        operations=operations, before=before, after=after,
        affected_spans=affected_spans, conflict_domains=conflict_domains)
    from library.tools import axi_transaction

    untouched = axi_transaction.untouched_regions(
        operations=operations, affected_spans=affected_spans,
        before=before, after=after)
    return {
        "status": ("verified" if all(row["passed"] for row in checks)
                   and len(acceptance_checks) == len(operations)
                   and all(row["passed"] for row in acceptance_checks)
                   and not collateral
                   and preservation["status"] == "preserved" else "failed"),
        "checks": checks,
        "operation_acceptance": acceptance_checks,
        "unrelated_items_unchanged": not collateral,
        "collateral_changes": collateral,
        "preservation": preservation,
        "untouched_regions": untouched,
        "snapshot_source": "fresh_live_timeline",
    }


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
    """Undo journaled registered writes in reverse order and verify each.

    The reversal SEMANTICS live in `library/tools/edit_recovery.py`: the
    order, the per-step verification, and the rule that a failed step
    names the operations that are still on the timeline. This function
    owns only the AXI-SPECIFIC half - how one registered write is
    undone (a caption swap-back, or an undo journal entry).
    """
    from library.tools import edit_recovery, undo_journal

    evidence = dict(failure_evidence or {})
    uncertain_failure = evidence.get("mutation_state") == "unknown"
    failed_receipt = evidence.get("adapter_receipt")
    rows = list(reversed(receipts))
    if failed_receipt is not None:
        rows.insert(0, {
            "operation_index": int(evidence["operation_index"]),
            "receipt": failed_receipt,
        })
    failed = {"index": None, "named": False}

    def _undo_one(row):
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
            except Exception as exc:
                failed["index"] = row["operation_index"]
                failed["named"] = True
                raise RuntimeError(
                    f"caption swap-back for operation "
                    f"{row['operation_index']} failed: "
                    f"{type(exc).__name__}: {exc}") from exc
            # The render-and-swap operation performs per-placement
            # read-back and refuses while any current file remains.
            return {
                "operation_index": row["operation_index"],
                "verified": True,
                "kind": "caption_swap_back",
                "current_file": reference["current_file"],
                "previous_file": reference["previous_file"],
            }
        if reference.get("kind") != "undo_journal_entry":
            failed["index"] = row["operation_index"]
            raise RuntimeError(
                "a completed operation has no undo journal reference")
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
        except Exception as exc:
            failed["index"] = row["operation_index"]
            failed["named"] = True
            raise RuntimeError(
                f"undo of operation {row['operation_index']} "
                f"failed: {type(exc).__name__}: {exc}") from exc
        return {
            "operation_index": row["operation_index"],
            "entry_id": reference["entry_id"],
            "verified": True,
            "receipt": result[0],
        }

    outcome = edit_recovery.reverse_each(rows=rows, undo_one=_undo_one)
    if outcome["status"] == edit_recovery.RECOVERY_REQUIRED:
        completed = {action["operation_index"] for action in outcome["actions"]}
        remaining = [item["operation_index"] for item in receipts
                     if item["operation_index"] not in completed]
        for index in outcome["remaining_operations"]:
            if index not in remaining:
                remaining.append(index)
        outcome["remaining_operations"] = remaining
        if failed["named"]:
            outcome["failed_operation_index"] = failed["index"]
        else:
            outcome.pop("failed_operation_index", None)
        return outcome
    if uncertain_failure:
        return {
            "status": "recovery_required",
            "reason": "the failed operation may have mutated without a recovery receipt",
            "failed_operation_index": evidence.get("operation_index"),
            "actions": outcome["actions"],
            "remaining_operations": [evidence.get("operation_index")],
        }
    return outcome


def _project_folder_for(request) -> str:
    """The Ren project folder a request belongs to, or "" when it has none.

    The folder is where the durable recovery journal lives, so it is
    what a recovery gate can be asked about.
    """
    from library.tools import project_registry

    try:
        project_path = project_registry.resolve_project_path(request.project)
        if project_path is not None:
            return str(project_path.parent)
        config = project_registry.get_project(request.project)
    except Exception:  # noqa: BLE001 - a missing registry is not a gate
        config = None
    if config is not None:
        return str(getattr(config, "project_root", "") or "")
    # A request names the RESOLVE project; the journal lives in the REN
    # project folder. The registry maps one back to the other, and
    # without that mapping the recovery anchor has nowhere to live.
    try:
        folder = project_registry.project_folder_for_resolve_name(
            str(request.project))
    except Exception:  # noqa: BLE001 - a missing registry is not a gate
        return ""
    return str(folder or "")


def _pre_mutation_refusal(*, request, generation, head, operations=(),
                          before=None, requirements=(), affected_spans=(),
                          depends_on=None) -> dict | None:
    """The checks a batch must pass before its first write.

    Four questions, and all of them are asked before anything is
    written:

    - does the live project still answer to the exact identity and
      revision this request was planned against (AXI-045);
    - is the timeline free of an unresolved recovery (AXI-048);
    - can the request's own preservation requirements be satisfied by
      the writes it asks for (AXI-014);
    - is this transaction a silent retry of a mutation that may
      already have committed (AXI-014).

    Returns ``None`` when the batch may proceed, or a refusal descriptor
    naming the category and the reason.
    """
    from library.tools import axi_transaction, compound_transaction

    folder = _project_folder_for(request)
    # AXI-014: the compound preflight. It refuses before the first
    # write when the declared dependencies cannot be satisfied, when a
    # named preservation constraint cannot hold, or when this
    # transaction may already have written.
    compound = compound_transaction.preflight(
        project_folder=folder, transaction_id=str(request.id),
        operations=operations, before=before or {}, requirements=requirements,
        affected_spans=affected_spans, depends_on=depends_on)
    if compound.get("status") == "refused":
        return {"category": compound.get("category") or "conflict",
                "reason": compound.get("reason") or "the compound request "
                "cannot run as asked",
                "evidence": {"mutation_state": "none", "mutated": False,
                             "compound_preflight": compound}}

    project_key = _shadow_project_key(request.project)
    conflict = axi_transaction.detect_conflict(
        expected_project=project_key,
        expected_timeline=request.timeline,
        observed_project=head.project,
        observed_timeline=head.timeline_name,
        expected_base_generation=generation.generation,
        observed_generation=head.generation,
        expected_model_revision=request.expected_model_revision,
        observed_model_revision=head.model_revision)
    if conflict.detected:
        return {
            "category": ("stale" if conflict.category == "revision"
                         else "conflict"),
            "reason": f"{conflict.reason}; the batch was not applied",
            "evidence": {"mutation_state": "none", "mutated": False,
                         "conflict": conflict.as_dict()},
        }
    if not folder:
        return None
    try:
        axi_transaction.require_recovery_clear(
            folder, project=project_key, timeline=request.timeline,
            transaction_id=request.id)
    except axi_transaction.TransactionRefused as blocked:
        return {
            "category": "recovery_required",
            "reason": blocked.reason,
            "evidence": {"mutation_state": "none", "mutated": False,
                         "recovery": blocked.as_dict()},
        }
    return None


def _execute_registered_delete_batch(*, request, compiled, generation,
                                     snapshot, decision, owner: str) -> dict:
    """Commit compatible local deletes as one verified EditPatch."""
    from library.tools import edit_patch, timeline_addressing
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
        project_folder = _project_folder_for(request)
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


def _execute_protected_compound_touchup(*, request, preparation, compiled,
                                        generation, snapshot, owner: str
                                        ) -> dict:
    """Run one of the two experimental compound shapes on a candidate."""
    from library.tools import project_registry, reel_proposal, reel_touchup
    from library.tools.edit_execution_adapters import (
        EditExecutionContext,
        _caption_style_props,
        _caption_style_revalidate_live,
        _resolve_named_caption_style,
    )

    operations = [dict(operation) for operation in compiled.operations]
    operation_ids = {str(operation["op"]) for operation in operations}
    if operation_ids not in ({"clip.trim", "clip.insert"},
                             {"clip.replace", "caption.style"}):
        raise ValueError("no protected compound adapter matches this request")
    project_path = project_registry.resolve_project_path(request.project)
    if project_path is None:
        project_config = project_registry.get_project(request.project)
        project_folder = str(project_config.project_root)
    else:
        project_folder = str(project_path.parent)
    moments = [moment for moment in reel_proposal.read_proposal(
        str(reel_proposal.proposal_path(project_folder)))
        if moment.timeline_name == request.timeline]
    if len(moments) != 1:
        raise ValueError(
            f"protected compound timeline {request.timeline!r} matched "
            f"{len(moments)} built reels; staging requires one proposal row")

    spec = {
        "reel": int(moments[0].number),
        "transaction_id": request.id,
        "transaction_operations": operations,
    }
    rendered_files = {}
    rendered_styles = {}
    style_operations = [operation for operation in operations
                        if operation["op"] == "caption.style"]
    if style_operations:
        if len(style_operations) != 1:
            raise ValueError("the protected compound accepts one caption.style")
        operation = style_operations[0]
        context = EditExecutionContext(
            execution=None, request=request, preparation=preparation,
            compiled=compiled, generation=generation, snapshot=snapshot,
            operation=operation, operation_index=operations.index(operation),
            owner=owner)
        unique_id = str(operation["unique_id"])
        _track, clip = _caption_style_target(context, unique_id)
        _caption_style_revalidate_live(context, dict(clip))
        old_file = str(clip["source_file"])
        old_props = _caption_style_props(old_file)
        old_style = (old_props.get("style")
                     if isinstance(old_props.get("style"), Mapping) else {})
        new_style = _resolve_named_caption_style(
            project_folder, str(operation["style"]))
        if "captionMaxWidth" in old_style:
            new_style = {**new_style,
                         "captionMaxWidth": old_style["captionMaxWidth"]}
        if old_style == new_style:
            raise ValueError(
                f"caption {unique_id!r} already renders style "
                f"{operation['style']!r}")
        new_props = dict(old_props)
        new_props["style"] = new_style
        if ({key: new_props[key] for key in new_props if key != "style"}
                != {key: old_props[key] for key in old_props
                    if key != "style"}):
            raise ValueError("caption restyle would change text or timing")
        from library.tools import operations as pipeline_operations
        report = pipeline_operations.get("subtitles.rerender_swap").run(
            project_folder,
            [{"old_mov": old_file, "timeline_label": request.timeline,
              "props": new_props}], swap=False)
        if not report.get("ok"):
            raise RuntimeError(
                f"caption style render failed: {report.get('error') or report}")
        new_file = dict(report.get("map") or {}).get(old_file)
        if not new_file:
            raise RuntimeError("caption style render returned no new asset")
        rendered_files[unique_id] = str(new_file)
        rendered_styles[unique_id] = dict(new_style)
        spec["rendered_caption_files"] = rendered_files
        spec["rendered_caption_styles"] = rendered_styles

    touchup = reel_touchup.apply_touchup(project_folder, spec)
    full_verification = touchup.get("semantic_full_request_verification") or {}
    if (not touchup.get("promoted")
            or full_verification.get("status") != "verified"):
        raise RuntimeError(
            "protected candidate did not complete full-request verification "
            "and guarded promotion")
    from library.tools.timeline_shadow import ShadowStore

    heads = [head for head, _live in ShadowStore().current_snapshots(request.project)
             if head.timeline_name == request.timeline]
    if len(heads) != 1 or heads[0].generation <= generation.generation:
        raise RuntimeError(
            "promoted compound has no newer recorded timeline generation")
    return {
        "status": "committed", "request_id": request.id,
        "transaction_id": request.id, "project": request.project,
        "timeline": request.timeline, "backend": "reel_touchup",
        "selected_backend": "reel_touchup", "atomic": False,
        "atomicity": "single_verified_candidate_promotion",
        "base_generation": generation.generation,
        "generation": heads[0].generation, "mutated": True,
        "operations": [dict(operation) for operation in operations],
        "operation_count": len(operations),
        "expected_changes": operations,
        "actual_changes": touchup.get("semantic_writes", []),
        "full_request_verification": full_verification,
        "acceptance_contracts": touchup.get("acceptance_contracts", []),
        "staging_receipt": touchup,
        "execution_routing": (touchup.get("routing")
                              or {"backend": "reel_touchup",
                                  "mode": "staged",
                                  "policy": "protected_experimental"}),
        "execution_contract": {
            "verification": {
                "status": "verified",
                "checks": full_verification.get("operation_failures", []),
                "evidence": full_verification,
            },
            "recovery": {
                "status": "available",
                "action": "undo the promoted staged candidate through its touch-up journal",
                "reference": {"kind": "undo_journal_entry",
                              "project_folder": project_folder,
                              "entry_id": str(touchup["journal"])},
            },
        },
    }


def _caption_style_target(context, unique_id: str) -> tuple:
    """Resolve one caption target from the already prepared snapshot."""
    hits = [(track, clip) for track in context.snapshot["tracks"]
            for clip in track["clips"]
            if str(clip.get("unique_id")) == unique_id]
    if len(hits) != 1:
        raise ValueError(
            f"caption.style target {unique_id!r} matched {len(hits)} items")
    track, clip = hits[0]
    if track.get("name") != "Subtitles" or not clip.get("source_file"):
        raise ValueError(
            f"caption.style target {unique_id!r} is not a rendered Ren "
            "caption on the Subtitles row")
    return hits[0]


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


def _failure_receipt(*, request: Any, backend: str, generation: Any,
                      operations: Sequence[Mapping], receipts: Sequence[Mapping],
                      decision: Mapping, category: str, reason: str,
                      failed_index: int,
                      evidence: Mapping | None = None) -> dict:
    """The receipt a failed execution publishes, with its typed next step.

    The failure category decides the recovery action, the recovery class
    and whether the same call may be repeated; `recovery_instruction`
    owns that join, so the host reads the next step instead of
    improvising one. The action also reaches the published
    `RecoveryResult`, which reads `recovery.action` - so before this
    carried one, the published action was "none" and the host had
    nothing but a category name.
    """
    from library.tools.recovery_instruction import instruction, retryable_for
    from library.tools.transaction_orchestration import (
        ExecutionRefusal,
        standardize_receipt,
    )

    typed = instruction(category, reason=reason)
    refusal = ExecutionRefusal(
        category, reason,
        retryable=retryable_for(category),
        recovery={"status": "required" if receipts else "not_required",
                  "completed_operations": len(receipts),
                  "instruction": typed},
    ).as_dict()
    # AXI-003: a REFUSAL is a decision Ren made and nothing ran, so it
    # is neither a failure nor an unexpected exception.  The engine's
    # own refusal shape travels with the receipt, and the receipt says
    # `refused` rather than `failed`.
    engine_refusal = (evidence or {}).get("engine_refusal")
    engine_refusal = (dict(engine_refusal)
                      if isinstance(engine_refusal, Mapping) else None)
    if engine_refusal is not None:
        refusal = {**refusal, "engine_refusal": engine_refusal}
    failure_receipt = {
        "status": ("partial_failure" if receipts
                   or (evidence or {}).get("mutated")
                   else "refused" if engine_refusal is not None else "failed"),
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
        "completed_operations": list(receipts),
        "operations": list(receipts),
        "requested_operation_count": len(operations),
        "expected_changes": list(operations),
        "actual_changes": list(receipts),
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
            "action": typed["action"],
            "instruction": typed,
            "evidence": [row["receipt"].get("execution_contract", {}).get(
                "recovery", {}) for row in receipts]
                + ([dict((evidence or {}).get("adapter_receipt", {}).get(
                    "execution_contract", {}).get("recovery", {}))]
                   if (evidence or {}).get("adapter_receipt") else []),
        },
        "execution_routing": dict(decision),
    }
    return standardize_receipt(failure_receipt)


def _verify_restoration(*, request, generation) -> dict:
    """Whether the timeline is back at the validated generation.

    The comparison is against the newest generation a verified write
    left: the state the request was authorized against. A restoration
    that cannot be proven is reported as unprovable, never as a pass.
    """
    from library.tools import compound_transaction

    project_key = _shadow_project_key(request.project)
    validated = compound_transaction.validated_generation(
        project_key, str(request.timeline))
    if validated is None:
        return {"status": "unprovable",
                "reason": ("no validated generation is recorded for this "
                           "timeline, so the restoration cannot be proven")}
    try:
        live = read_live_timeline(request.project, request.timeline)
    except Exception as exc:  # noqa: BLE001 - an unread timeline is unprovable
        return {"status": "unprovable",
                "generation": validated["generation"],
                "reason": f"the live timeline could not be read: {exc}"}
    return compound_transaction.verify_restored(
        project=project_key, timeline=str(request.timeline),
        generation=validated["generation"], live=live)


def _named_preservation(request: Any) -> tuple:
    """The request's preservation requirements, and its operations'.

    The request may name what must not change, and so may an operation
    inside it. They travel together: the patch carries both, and the
    read-back judges them against the state the edit was authorized
    against.
    """
    named = [dict(requirement) for requirement in request.preservation]
    for operation in request.operations:
        named.extend(dict(requirement)
                      for requirement in operation.preservation)
    return tuple(named)


def _declared_dependencies(request: Any, compiled: Any) -> list[list[int]]:
    """The requester's own dependencies, in the compiled write order.

    The request declares its dependencies over ITS OWN operation
    indices; the compiled list is the same operations in dependency
    order. This maps one onto the other so the compound plan reads the
    requester's declared dependencies in the space it works in.
    """
    try:
        declared = [tuple(int(index) for index in
                          (operation.depends_on or ()))
                    for operation in request.operations]
    except (AttributeError, TypeError):
        return []
    order = [int(index) for index in (getattr(compiled, "order", ()) or ())]
    position = {original: index for index, original in enumerate(order)}
    mapped: list[list[int]] = []
    for original in order:
        dependencies = declared[original] if original < len(declared) else ()
        mapped.append(sorted(position[dependency] for dependency
                             in dependencies if dependency in position))
    return mapped


def _execute_registered_backend(*, backend: str, plan: Mapping,
                                request: Any, preparation: Any,
                                compiled: Any, generation: Any,
                                snapshot: Mapping, owner: str) -> dict:
    """Run the router-selected adapters and preserve sequential semantics."""
    from library.tools import axi_transaction, compound_transaction, edit_algebra
    from library.tools.edit_execution_adapters import (
        register_builtin_edit_executors,
    )
    from library.tools.transaction_orchestration import (
        ExecutionInput,
        standardize_receipt,
    )

    register_builtin_edit_executors()
    backend_id = backend.removesuffix(".structural")
    operations = [dict(row) for row in compiled.operations]
    decision = plan.get("routing") or {}
    compound_touchup = (
        backend_id == "reel_touchup"
        and len(operations) == 2
        and {str(operation["op"]) for operation in operations}
        in ({"clip.trim", "clip.insert"},
            {"clip.replace", "caption.style"}))
    routed_backend = str(decision.get("backend") or "")
    if routed_backend != backend:
        raise ValueError(
            f"dispatcher received {backend!r}, but router selected "
            f"{routed_backend or 'no backend'!r}")
    executors: list[tuple[int, EditExecutor]] = []
    for index, operation in enumerate(operations):
        if compound_touchup:
            break
        operation_id = str(operation["op"])
        status = edit_algebra.status(operation_id)
        row = status.for_backend(backend_id) if status is not None else None
        if ((row is None or not row.production_enabled)
                and backend_id not in _ROUTER_QUALIFIED_BACKENDS
                and backend_id not in _ROUTER_PROTECTED_BACKENDS):
            raise ValueError(
                f"router selected {backend!r}, but {operation_id} is not "
                f"production enabled on {backend_id}")
        if (backend_id in _ROUTER_PROTECTED_BACKENDS
                and (row is None or not row.production_enabled)):
            selection = decision.get("selection") or {}
            if selection.get("qualification_policy") != "protected_experimental":
                raise ValueError(
                    f"router selected unqualified {operation_id} on "
                    f"{backend_id} without protected experimental policy")
            if not any(gap[0] == operation_id
                       for gap in decision.get("qualification_gaps", ())):
                raise ValueError(
                    f"router omitted the qualification gap for "
                    f"{operation_id} on {backend_id}")
        with _EDIT_EXECUTOR_LOCK:
            executor = _EDIT_EXECUTORS.get((operation_id, backend_id))
        if executor is None:
            raise ValueError(
                f"router selected {backend!r}, but no callable adapter is "
                f"registered for {operation_id} on {backend_id}")
        executors.append((index, executor))

    if not executors and not compound_touchup:
        raise ValueError("routed semantic request contains no executable operations")
    # The compound registry is the one place a multi-operation shape is
    # declared, so the preflight and the receipt read the same answer.
    transaction_plan = axi_transaction.plan_operations(
        operations=operations, backend=backend_id, project=request.project,
        timeline=request.timeline, transaction_id=request.id)
    if not transaction_plan.supported:
        raise ValueError(transaction_plan.refusal)
    _validate_caption_style_batch(operations, snapshot)
    # AXI-014: the declared write order. The request's own dependency
    # order was applied when the request was compiled; the engine's
    # declared dependencies are applied here, and the operations are
    # written in that order. A request whose declared order contradicts
    # a declared dependency is refused by the preflight below, before
    # the first write.
    depends_on = _declared_dependencies(request, compiled)
    try:
        write_order = compound_transaction.plan_write_order(
            operations, depends_on=depends_on).order
    except compound_transaction.CompoundRefused:
        write_order = tuple(range(len(operations)))
    executors.sort(key=lambda row: write_order.index(row[0]))

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
    # AXI-014: a COMPOUND transaction journals its own stages, so a
    # later submission of the same transaction id is answered with the
    # prior outcome instead of a second write - and so the AXI-048 gate
    # can tell a transaction that never wrote from one that may have.
    journal_folder = (_project_folder_for(request)
                      if len(operations) > 1 else "")
    write_attempted = {"value": False}

    def journal_stage(stage: str, **fields: Any) -> None:
        if not journal_folder:
            return
        axi_transaction.record_transaction(
            journal_folder, str(request.id), stage,
            project=_shadow_project_key(request.project),
            timeline=str(request.timeline), **fields)

    def refused(category: str, reason: str, *, failed_index: int,
                evidence: Mapping | None = None) -> dict:
        failure_receipt = _failure_receipt(
            request=request, backend=backend, generation=generation,
            operations=operations, receipts=receipts, decision=decision,
            category=category, reason=reason, failed_index=failed_index,
            evidence=evidence)
        # AXI-049: refusals carry the same seven fields as successful writes.
        failure_receipt.update(axi_transaction.build_receipt(
            request_id=request.id, project=request.project,
            timeline=request.timeline, backend=backend, operations=operations,
            actual_changes=receipts,
            verification_evidence=failure_receipt["verification_evidence"],
            untouched=(), route=decision,
            errors=[{"category": category, "reason": reason,
                     "operation_index": failed_index}],
            recovery=failure_receipt["recovery"],
            atomicity=failure_receipt["atomicity"], promoted=False,
            delivered=False, prepared=False))
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
            # The attempt is journalled BEFORE the call: an adapter may
            # mutate and then raise, and the journal is what proves the
            # difference between a transaction that owes a recovery and
            # one that never wrote.
            write_attempted["value"] = True
            journal_stage("applying", index=index,
                          op=str(operations[index].get("op") or ""),
                          operation=dict(operations[index]))
            try:
                result = executor(context)
            except RenRefusal as refusal:
                # AXI-003: a refusal is a DECISION, and it must not
                # arrive as an unexpected backend exception.  It keeps
                # its own category, its own fix and its own next action.
                return refused(
                    failure_category_for_blocker(refusal.blocker),
                    refusal.why,
                    failed_index=index,
                    evidence={"mutation_state": "not_mutated",
                              "mutated": False,
                              "operation_index": index,
                              "engine_refusal": refusal.to_dict()})
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
            journal_stage("applied", index=index,
                          op=str(operations[index].get("op") or ""))
        return None

    from library.tools.resolve_lock import resolve_lease

    batch_receipt = None
    full_request_verification = None
    single_request_verification = None
    whole_timeline_verification = None
    whole_timeline_path = None
    # One fresh read per request, shared by the delta gate and the
    # structural gate: a second read would be a second chance for the
    # timeline to move under the request.
    _fresh_reads: dict = {}

    def _fresh_after() -> dict:
        """The timeline as it is NOW, read once per request."""
        if "after" not in _fresh_reads:
            _fresh_reads["after"] = read_live_timeline(
                request.project, request.timeline)
        return _fresh_reads["after"]
    # Hold the broker lease across the entire batch so another Ren
    # transaction cannot interleave between its operations.
    with resolve_lease(
            "resolve-axi-operation-batch", exclusive=True, owner=owner,
            project=request.project, timeline=request.timeline,
            edit_ids=tuple(str(operation.get("unique_id", ""))
                           for operation in operations)):
        from library.tools import mutation_verification, timeline_shadow

        current = [
            (head, live_snapshot)
            for head, live_snapshot in
            timeline_shadow.ShadowStore().current_snapshots(request.project)
            if head.timeline_name == request.timeline
        ]
        if len(current) != 1:
            refusal = refused(
                "stale", "the recorded timeline head did not resolve to "
                "exactly one timeline; the batch was not applied",
                failed_index=0)
        else:
            # AXI-045 and AXI-048: the batch may only be written to the
            # project and timeline it names, at the revision it was
            # planned against, and not while a recovery is unresolved.
            blocked = _pre_mutation_refusal(
                request=request, generation=generation, head=current[0][0],
                operations=operations, before=snapshot,
                requirements=_named_preservation(request),
                affected_spans=compiled.affected_spans,
                depends_on=depends_on)
            refusal = (None if blocked is None else
                       refused(blocked["category"], blocked["reason"],
                               failed_index=0,
                               evidence=blocked.get("evidence") or {}))
        if refusal is None:
            # AXI-014: the durable intent reaches the journal before the
            # first write. A job that dies here owes no recovery - the
            # journal proves no write was attempted - so it must not
            # leave a hold on the timeline.
            journal_stage("prepared", operations=operations,
                          base_generation=generation.generation,
                          scenario=transaction_plan.scenario,
                          intent_digest=axi_transaction.intent_digest(
                              operations))
        if refusal is None and compound_touchup:
            try:
                batch_receipt = _execute_protected_compound_touchup(
                    request=request, preparation=preparation,
                    compiled=compiled, generation=generation,
                    snapshot=snapshot, owner=owner)
                whole_timeline_path = "protected_compound_touchup"
                refusal = None
            except Exception as exc:  # noqa: BLE001 - candidate owns cleanup
                refusal = refused(
                    "backend_failure",
                    f"protected compound staging failed: "
                    f"{type(exc).__name__}: {exc}",
                    failed_index=0,
                    evidence={"mutation_state": "unknown", "mutated": True,
                              "operation_index": 0, "error": repr(exc)})
        elif (refusal is None and len(operations) > 1
              and all(str(operation["op"]) == "clip.delete"
                      for operation in operations)):
            try:
                batch_receipt = _execute_registered_delete_batch(
                    request=request, compiled=compiled,
                    generation=generation, snapshot=snapshot,
                    decision=decision, owner=owner)
                whole_timeline_path = "multi_delete_batch"
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
        elif refusal is None and len(operations) > 1 and backend_id == "resolve_axi":
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
        elif refusal is None:
            refusal = run_operations()
        if (refusal is None and batch_receipt is None
                and len(operations) > 1):
            try:
                after = _fresh_after()
                full_request_verification = _verify_registered_full_request(
                    operations=operations, before=snapshot, after=after,
                    affected_spans=compiled.affected_spans,
                    conflict_domains=compiled.conflict_domains,
                    receipts=receipts)
                if full_request_verification["status"] != "verified":
                    refusal = refused(
                        "verification_failed",
                        "the complete request did not verify on one fresh "
                        "timeline snapshot", failed_index=len(operations),
                        evidence={"mutated": True,
                                  "full_request_verification":
                                      full_request_verification})
            except Exception as exc:  # noqa: BLE001 - verify before success
                refusal = refused(
                    "verification_failed",
                    f"full-request read-back failed: "
                    f"{type(exc).__name__}: {exc}",
                    failed_index=len(operations),
                    evidence={"mutated": True,
                              "error": repr(exc)})
            whole_timeline_path = "multi_operation_revision"
        elif (refusal is None and batch_receipt is None
              and mutation_verification.route_carries_gate(
                  backend_id, operations)):
            # AXI-013: a request the engine routes onto a qualified
            # operation is judged by the ENGINE's own comparison of two
            # fresh reads - the pre-write state and a post-write read -
            # and not by the receipt the writer handed back.  The
            # adapter's read-back is the writer's own; this is the
            # engine's, and it is what catches a mutation that
            # succeeded while changing the wrong thing.
            try:
                after = _fresh_after()
                baseline = mutation_verification.capture_baseline(
                    project=request.project, timeline=request.timeline,
                    revision=generation.generation, operations=operations,
                    snapshot=snapshot,
                    preservation_scope=tuple(compiled.conflict_domains))
                delta_verification = mutation_verification.verify_delta(
                    baseline=baseline, after=after,
                    affected_spans=compiled.affected_spans,
                    conflict_domains=compiled.conflict_domains)
                if delta_verification["status"] != "verified":
                    refusal = refused(
                        "verification_failed",
                        "the mutation did not produce the permitted delta "
                        "on a fresh timeline snapshot",
                        failed_index=0,
                        evidence={"mutated": True,
                                  "delta_verification": delta_verification})
                else:
                    single_request_verification = delta_verification
            except Exception as exc:  # noqa: BLE001 - verify before success
                refusal = refused(
                    "verification_failed",
                    f"the post-write read-back failed: "
                    f"{type(exc).__name__}: {exc}",
                    failed_index=0,
                    evidence={"mutated": True,
                              "error": repr(exc)})
        if refusal is None and whole_timeline_path is not None:
            # QA-001: a whole-timeline outcome is judged by the engine's
            # own STRUCTURAL read-back.  The delta above says what moved;
            # the structure says what the timeline now IS - its rows, its
            # items, their source intervals, their links and its
            # coverage - and it is read fresh, by the engine, after the
            # write.  A writer that landed a request on a timeline with
            # a hole in it, an item twice, or a clip whose media is gone
            # is refused here, whatever its receipt said.
            try:
                after = _fresh_after()
                baseline = mutation_verification.capture_baseline(
                    project=request.project, timeline=request.timeline,
                    revision=generation.generation, operations=operations,
                    snapshot=snapshot,
                    preservation_scope=tuple(compiled.conflict_domains))
                structural = mutation_verification.whole_timeline_gate(
                    whole_timeline_path, baseline=baseline, after=after,
                    affected_spans=compiled.affected_spans,
                    conflict_domains=compiled.conflict_domains)
                if structural["status"] != "verified":
                    refusal = refused(
                        "verification_failed",
                        "the whole timeline did not read back as the "
                        "structure the request was authorized to leave",
                        failed_index=0,
                        evidence={"mutated": True,
                                  "whole_timeline_verification": structural})
                else:
                    whole_timeline_verification = structural
                    if isinstance(batch_receipt, dict):
                        batch_receipt["whole_timeline_verification"] = (
                            structural)
            except Exception as exc:  # noqa: BLE001 - verify before success
                refusal = refused(
                    "verification_failed",
                    f"the whole-timeline read-back failed: "
                    f"{type(exc).__name__}: {exc}",
                    failed_index=0,
                    evidence={"mutated": True,
                              "error": repr(exc)})
        if refusal is not None and receipts:
            recovery = compensate_completed(refusal.get("failure_evidence"))
            recovery["failure_reason"] = refusal["refusal"]["reason"]
            recovery["failed_operation_index"] = refusal[
                "failed_operation_index"]
            # AXI-014: the recovery is only claimed when a fresh
            # read-back matches the validated generation the request was
            # planned against. A compensation that ran is not a proof
            # that the timeline is back.
            recovery["restoration"] = _verify_restoration(
                request=request, generation=generation)
            refusal["recovery"] = recovery
        elif refusal is not None and refusal.get("mutated"):
            refusal["recovery"] = compensate_completed(
                refusal.get("failure_evidence"))
            refusal["recovery"]["restoration"] = _verify_restoration(
                request=request, generation=generation)
        # AXI-014: the compound transaction's own durable stage. A
        # transaction that never wrote is closed, so it leaves no hold;
        # one that wrote and was not restored keeps its honest hold.
        if refusal is not None:
            stage = compound_transaction.terminal_stage(
                write_attempted=write_attempted["value"],
                restoration=(refusal.get("recovery") or {}).get("restoration"))
            journal_stage(stage, reason=(
                "the request was refused before its first write, so it owes "
                "no recovery" if stage == "abandoned" else
                "the writes that landed were reversed and the timeline "
                "reads as the validated generation" if stage == "recovered"
                else "a write was attempted and the timeline does not read "
                "as the validated generation"))
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
    journal_stage("committed", generation=committed_generation)
    return _registered_execution_receipt(
        backend=backend, plan=plan, request=request,
        operations=operations, receipts=receipts,
        base_generation=generation.generation,
        committed_generation=committed_generation,
        full_request_verification=full_request_verification,
        delta_verification=single_request_verification,
        whole_timeline_verification=whole_timeline_verification)


def _registered_execution_receipt(*, backend: str, plan: Mapping,
                                  request: Any, operations: Sequence[Mapping],
                                  receipts: Sequence[Mapping],
                                  base_generation: int,
                                  committed_generation: int,
                                  full_request_verification: Mapping | None = None,
                                  delta_verification: Mapping | None = None,
                                  whole_timeline_verification: Mapping | None = None,
                                  ) -> dict:
    """Normalize a registered-backend batch with operation evidence."""
    from library.tools.transaction_orchestration import standardize_receipt

    recovery_rows = [
        result.get("receipt", result).get("execution_contract", {}).get(
            "recovery", {})
        for result in receipts
    ]
    recovery_available = bool(recovery_rows) and all(
        _registered_recovery_reference_is_actionable(row)
        for row in recovery_rows
    )

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
        "full_request_verification": dict(
            full_request_verification or {}),
        "delta_verification": dict(delta_verification or {}),
        "whole_timeline_verification": dict(
            whole_timeline_verification or {}),
        "operations": receipts,
        "requested_operation_count": len(operations),
        "acceptance_contracts": acceptance_contracts,
        "execution_routing": plan.get("routing") or {},
        "recovery": {"status": "available" if recovery_available
                     else "unavailable",
                     "operations": recovery_rows},
        "promotion": {"status": "awaiting_promotion"},
        "delivered": {"status": "not_requested"},
    }
    # AXI-049: the receipt names what was asked, what happened, what was
    # left alone, the route, what went wrong, how recovery went, and the
    # evidence. The keys the batch already published stay exactly as
    # they were; these are the same answers under the names the AXI
    # brief uses.
    from library.tools import axi_transaction

    aggregate.update(axi_transaction.build_receipt(
        request_id=request.id, project=request.project,
        timeline=request.timeline, backend=backend, operations=operations,
        actual_changes=receipts, verification_evidence=acceptance_contracts,
        untouched=(full_request_verification or {}).get(
            "untouched_regions") or (),
        route=plan.get("routing") or {}, errors=[],
        recovery=aggregate["recovery"],
        atomicity=aggregate["atomicity"], promoted=False, delivered=False,
        prepared=True))
    return standardize_receipt(aggregate)


def _registered_recovery_reference_is_actionable(recovery: Mapping) -> bool:
    """Accept only recovery references handled by registered compensation."""
    if recovery.get("status") not in {"available", "recorded"}:
        return False
    reference = recovery.get("reference")
    if not isinstance(reference, Mapping):
        return False
    kind = reference.get("kind")
    if kind == "undo_journal_entry":
        return bool(reference.get("project_folder")
                    and reference.get("entry_id"))
    if kind == "caption_swap_back":
        return bool(reference.get("project_folder")
                    and reference.get("current_file")
                    and reference.get("previous_file")
                    and isinstance(reference.get("previous_props"), Mapping))
    return False


def execute(operation: AxiOperation | Sequence[str]) -> AxiResult:
    """Execute a command in-process through the canonical AXI dispatcher.

    Every invocation answers with a result, including the ones argparse
    answers by raising `SystemExit`: `--help` prints the help on stdout
    and exits 0, a usage error prints the error on stderr and exits 2.
    Those answers are written into the buffers captured below, so they
    are RETURNED rather than dropped with the exception - dropping them
    is what made `resolve-axi --help` print nothing while the
    resolve-axi skill tells every agent to start there (finding D6).
    """
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
            try:
                code = resolve_axi._main_impl(argv)
            except SystemExit as raised:
                code = _exit_code(raised, stderr)
    return AxiResult(int(code), stdout.getvalue(), stderr.getvalue())


def _exit_code(raised: SystemExit, stderr: io.StringIO) -> int:
    """The exit code a `SystemExit` carries, and where its text belongs.

    `sys.exit()` with no argument means success; `sys.exit("text")` means
    the text IS the error, so it is written to stderr and the exit is 1.
    """
    code = raised.code
    if code is None:
        return 0
    if isinstance(code, int):
        return int(code)
    stderr.write(f"{code}\n")
    return 1


def read_live_timeline(project: str, timeline: str, resolve=None) -> dict:
    """Return a fresh live snapshot through the AXI timeline reader.

    `resolve` is the caller's held connection. It is passed on so a
    caller that already holds one - a preview selecting the reel it is
    about to read - does not open a SECOND scripting client beside it
    (the multi-client shape behind the 2026-10-06 crash).
    """
    from library.tools import resolve_axi_core_commands

    view, _note = resolve_axi_core_commands._timeline_view(
        timeline, project, caller="resolve_axi_core.read_live_timeline",
        refresh_live=True, exact=True, resolve=resolve)
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


def _is_patch_mapping(data: Mapping[str, Any]) -> bool:
    """Whether a plain dict is a patch shape, not a request shape.

    A patch names the generation it was planned against, the capability
    that governs the write and the extent it declares; a request names
    its addressing vocabulary instead (see `_is_request_mapping`).
    """
    if not isinstance(data, Mapping):
        return False
    return ("base_generation" in data and "capability" in data
            and "affected_spans" in data)


def _shadow_project_key(project: str) -> str:
    """The shadow store's project key for a request's project.

    The store is keyed by the RESOLVE project name - every recorded
    generation names the Resolve project it was read from. A request
    names the Ren project FOLDER on the revise path, so the key is read
    from that folder's `project.yaml`; a request that already names the
    Resolve project is used as it stands.

    Looking the base up by the request's own `project` string found
    nothing for every revise request, because no generation is recorded
    under a folder path.
    """
    from pathlib import Path

    folder = Path(str(project or "")).expanduser()
    if folder.is_dir():
        from library.tools import edit_spec

        return edit_spec._resolve_project_name(str(folder))
    return str(project)


def _protected_route_context(request, operation_ids) -> dict:
    """The routing context for a request, under the ONE policy.

    The protected experimental policy has one owner
    (`edit_qualification_policy`), so the front door's classifier and
    this planner cannot disagree about which operations it covers.
    """
    from library.tools import edit_qualification_policy

    originator_name = str(
        (request.originator or {}).get("name", "")).strip().casefold()
    return edit_qualification_policy.protected_experimental_context(
        operation_ids, originator_name=originator_name) or {}


def _route_context(request, operation_ids, operations, resolve=None) -> dict:
    """The routing context: the policy, and the MEASURED runtime state.

    A promoted operation is routed only when the runtime satisfies the
    recorded qualification predicates, and that state has to be read
    (`library/tools/axi_qualification_state.py`). Until it was read, every
    promoted operation was refused with "runtime qualification state was
    not inspected" - a statement about the code, not about the timeline.

    The broker's AXI execute path calls this under its EXCLUSIVE lease
    with the target timeline current, and passes its own Resolve
    connection so the inspection opens no second scripting client; a
    front door that calls it outside one takes a shared lease for the
    read.
    """
    from library.tools import axi_qualification_state

    context = _protected_route_context(request, operation_ids)
    # The plan's own constraints on the ROUTE. They narrow the choice
    # and never widen it; the router refuses a route that breaks one.
    context["backend_constraints"] = tuple(
        str(constraint) for constraint in request.backend_constraints)
    # The shadow store is keyed by the RESOLVE project name, and a
    # request may name the Ren project FOLDER instead - the same reverse
    # lookup the base snapshot uses, so the inspection names the same
    # project the request is about to write.
    project = _shadow_project_key(request.project)
    return axi_qualification_state.with_runtime_state(
        context, project=project, timeline=request.timeline,
        operations=operations, resolve=resolve)


def _routing_inspected(routing) -> bool:
    """Whether the routing decision was made against a MEASURED state.

    The plan says whether Resolve was contacted, so the fact it records
    is the one that happened: a plan that read the runtime to route a
    promoted operation did contact Resolve, and a plan that routed from
    the recorded snapshot alone did not.
    """
    selection = (routing or {}).get("selection") or {}
    return bool(selection.get("qualification_state_inspected"))


def _feasibility_of(request, compiled, snapshot, route_context):
    """The feasibility verdict for the plan, against the state it would run on.

    The state is the RECORDED generation the request is planned against -
    its items, the rows they live on and their source handles - plus the
    measured runtime the route context already carries, when it carries
    one.  Nothing here reads Resolve: the route context did that, and
    the recorded generation is the generation the request is pinned to.

    A measurement nobody took stays UNDETERMINED.  The gate refuses on a
    refusal only, so a plan the engine cannot see into is reported, not
    forbidden (AGENTS.md 10.3, 10.4).
    """
    from library.tools import plan_feasibility

    context = dict(route_context or {})
    preservation = []
    for requirement in (request.preservation or ()):
        if isinstance(requirement, Mapping):
            preservation.append(str(requirement.get("kind")
                                    or requirement.get("name") or ""))
    state = plan_feasibility.FeasibilityState(
        items=plan_feasibility.items_from_snapshot(snapshot),
        tracks=plan_feasibility.tracks_from_snapshot(snapshot),
        runtime=context.get("qualification_state"),
        preservation_requirements=tuple(preservation),
        backend_constraints=tuple(
            str(constraint) for constraint in
            (request.backend_constraints or ())),
        policy=str(context.get("qualification_policy") or "production"),
        originator=str((request.originator or {}).get("name") or ""),
    )
    return plan_feasibility.assess(compiled.operations, state=state)


def _prepare_request_plan(request, *, resolve=None):
    """Validate an edit request against its recorded base.

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
    project_key = _shadow_project_key(request.project)
    candidates = [
        (generation, snapshot)
        for generation, snapshot in store.current_snapshots(project_key)
        if generation.timeline_name == request.timeline
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"recorded base for {project_key!r}/{request.timeline!r} "
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
    feasibility = None
    if no_op is not None:
        routing = no_op.as_dict()
        backend: str | None = "no_op"
        backend_reason = str(no_op.reasons[0]) if no_op.reasons else (
            "already satisfied: recorded state already holds every "
            "requested value")
    else:
        operation_ids = tuple(
            str(operation.get("op", "")) for operation in compiled.operations)
        route_context = _route_context(
            request, operation_ids, compiled.operations, resolve=resolve)
        # PLAN-005: the plan is checked against the timeline it would run
        # on BEFORE a route is chosen, so a plan that cannot execute is
        # refused before anything is staged.  The check refuses on a
        # refusal only: a measurement nobody took is reported, never
        # treated as an absence (AGENTS.md 10.3).
        feasibility = _feasibility_of(request, compiled, snapshot,
                                       route_context)
        if feasibility.infeasible:
            # PLAN-007: the refusal carries the narrow revision, so the
            # planner can revise the affected part of the plan instead
            # of the whole of it.
            from library.tools import plan_feasibility as _feasibility

            raise _feasibility.PlanInfeasible(_feasibility.refusal(feasibility))
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
        "resolve_contacted": bool(_routing_inspected(routing)),
        "mutated": False,
    }
    if feasibility is not None:
        plan["feasibility"] = feasibility.as_dict()
    if routing is not None:
        plan["routing"] = routing
    return plan, request, result, compiled, generation, snapshot


def _plan_request(request) -> dict[str, Any]:
    return _prepare_request_plan(request)[0]


def execute_semantic_edit_job(job: Mapping[str, Any], *,
                              resolve=None) -> dict[str, Any]:
    """Execute one broker-owned semantic edit against its queued generation.

    `resolve` is the broker worker's own Resolve connection, when it has
    one: the qualification inspection reads the live runtime through it
    rather than opening a second scripting client (the 2026-10-06
    crash).
    """
    request = _request_from_any(job["params"]["request"])
    plan, request, preparation, compiled, generation, snapshot = (
        _prepare_request_plan(request, resolve=resolve))
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
        preconditions=request.preconditions,
        # AXI-004: the durable patch carries the request's preservation
        # requirements. The semantic route judges them on the read-back
        # through the request; the durable route judges them through the
        # patch. A requirement the request states and the patch drops is
        # a requirement nobody keeps.
        preservation=request.preservation)
    from library.tools.resolved import client
    client.ensure()
    # The patch id IS the request's idempotency key, so the broker's own
    # durable key for this kind is the patch - passing a second key
    # alongside it is refused by `jobs.idempotency_request`, which is
    # what a submission that can never run looks like.
    job = client.submit("timeline.apply_patch", {"patch": patch},
                        owner=owner)
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

    Accepts a typed legacy `AxiTransaction`, a patch-shaped mapping, or
    a semantic `EditRequest`. Semantic requests are prepared and routed
    without Resolve contact; legacy transactions retain their
    EditPatch contract. Neither path queues or mutates anything.

    AXI-004: the legacy plan is the legacy plan's shape, but the
    operations it reports are the canonical ones - the carrier is read
    as an `EditRequest` and compiled, so both doors read one request
    with one model.
    """
    from library.tools.edit_request import EditRequest

    if isinstance(transaction, EditRequest) or _is_request_mapping(
            transaction):
        return _plan_request(transaction)
    if isinstance(transaction, AxiTransaction) or _is_patch_mapping(
            transaction):
        return _plan_transaction_patch(transaction)
    raise TypeError(
        "plan_transaction takes an AxiTransaction, a patch mapping or an "
        f"EditRequest, got {type(transaction).__name__}")


def _plan_transaction_patch(transaction: AxiTransaction) -> dict[str, Any]:
    """The typed-transaction plan: EditPatch validation against the base.

    AXI-004: the plan is the legacy plan's shape, but the operations it
    reports are the CANONICAL ones - the carrier is read as an
    ``EditRequest`` and compiled, so the write order is the order the
    operations' declared dependencies require.
    """
    from library.tools import legacy_transaction

    return legacy_transaction.plan_for(transaction)


def submit_transaction_async(
    transaction: AxiTransaction | Mapping[str, Any], *, owner: str = "resolve-axi"
) -> dict[str, Any]:
    """Queue a multi-operation EditPatch and return its broker job handle.

    AXI-004: the patch that is queued is compiled from the CANONICAL
    request the carrier carries, so the durable write a legacy front
    door makes is the same write the canonical front door would make.
    """
    from library.tools import legacy_transaction
    from library.tools.resolved import client

    client.ensure()
    patch = legacy_transaction.patch_for(transaction)
    return client.submit(
        "timeline.apply_patch", {"patch": patch}, owner=owner
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
    transaction: AxiTransaction | Mapping[str, Any], *, owner: str = "resolve-axi",
    timeout: float = 120.0
) -> dict[str, Any]:
    """Submit a multi-operation EditPatch and wait for its verified receipt."""
    submitted = submit_transaction_async(transaction, owner=owner)
    return retrieve_transaction(submitted["id"], wait=timeout)


_EXECUTION_LOCK = threading.RLock()
