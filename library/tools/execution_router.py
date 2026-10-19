"""Inspectable routing for compiled timeline edits.

The router chooses a physical executor without changing the edit's meaning.
It is deliberately data-only: callers can persist the decision beside the
transaction receipt before invoking the selected executor.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence


class RoutingRefused(RuntimeError):
    """No executor can apply the requested semantics safely."""


class IncrementalRoutingRefused(RoutingRefused):
    """Incremental refusal that must not be collapsed into a rebuild."""

    def __init__(self, category: str, reason: str):
        self.category = category
        self.reason = reason
        super().__init__(f"incremental {category}: {reason}")


@dataclass(frozen=True)
class RejectedBackend:
    backend: str
    reason: str


@dataclass(frozen=True)
class ExecutionDecision:
    backend: str
    reasons: tuple[str, ...]
    rejected_alternatives: tuple[RejectedBackend, ...]
    verification: tuple[str, ...]
    algebra_status: tuple[tuple[str, str], ...]

    def as_dict(self) -> dict:
        return asdict(self)


def _ops(compiled_edit) -> list[dict]:
    if isinstance(compiled_edit, Mapping):
        operations = compiled_edit.get("operations")
    else:
        operations = compiled_edit
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise RoutingRefused("compiled edit must contain an operations sequence")
    result = [dict(op) for op in operations]
    if not result:
        raise RoutingRefused("compiled edit contains no operations")
    return result


def route_compiled_edit(compiled_edit, *, context: Mapping | None = None) -> ExecutionDecision:
    """Choose a backend for an already compiled edit and explain every choice.

    Existing-item property setters always use EditPatch's direct native
    property executor. Incremental is selected only when the caller supplies
    an affirmative eligibility result; an ineligible result is surfaced as
    a named rejection and never converted into a rebuild here.
    """
    from library.tools import edit_algebra

    context = dict(context or {})
    operations = _ops(compiled_edit)
    ids = tuple(str(op.get("op", "")) for op in operations)
    statuses = []
    for op_id in ids:
        spec = edit_algebra.get(op_id)
        statuses.append((op_id, "unknown" if spec is None else
                         "implemented" if spec.apply is not None else "defined"))

    property_ops = {"clip.set_property", "clip.set_enabled"}
    if all(op_id in property_ops for op_id in ids):
        if any(not op.get("unique_id") for op in operations):
            raise RoutingRefused("property patch requires an existing-item unique_id")
        return ExecutionDecision(
            backend="edit_patch.property",
            reasons=("all operations set properties on identified existing items",
                     "direct property patch preserves unrelated timeline items"),
            rejected_alternatives=(
                RejectedBackend("incremental", "incremental editor applies planned overlay diffs, not item property setters"),
                RejectedBackend("full_rebuild", "existing-item property changes must not reconstruct the timeline"),
                RejectedBackend("composed_edit", "re-placement is unnecessary for native property setters"),
            ),
            verification=("read back each requested property", "compare timeline state outside declared targets"),
            algebra_status=tuple(statuses),
        )

    eligibility = context.get("incremental_eligibility")
    if eligibility is True:
        return ExecutionDecision(
            backend="incremental",
            reasons=("incremental eligibility checks passed",),
            rejected_alternatives=(RejectedBackend("full_rebuild", "incremental editor can preserve unchanged timeline items"),),
            verification=("verify patched inventory against the compiled plan", "verify unchanged rows and items"),
            algebra_status=tuple(statuses),
        )
    if all(status == "implemented" for _op_id, status in statuses):
        return ExecutionDecision(
            backend="edit_patch",
            reasons=("all operations have native algebra executors",
                     "EditPatch provides the scoped transaction and read-back"),
            rejected_alternatives=(
                RejectedBackend("full_rebuild", "a targeted transaction can apply these operations"),
                RejectedBackend("incremental", "incremental editor is reserved for eligible compiled build diffs"),
            ),
            verification=("run algebra read-back verification", "check changes outside declared scope"),
            algebra_status=tuple(statuses),
        )
    if context.get("request_incremental"):
        reason = str(context.get("ineligibility_reason") or "eligibility was not established")
        category = str(context.get("ineligibility_category") or "unsupported")
        if category == "unsupported" and context.get("allow_full_rebuild"):
            return ExecutionDecision(
                backend="full_rebuild",
                reasons=("incremental eligibility rejected the plan",
                         "full rebuild explicitly permitted by the caller"),
                rejected_alternatives=(RejectedBackend(
                    "incremental", f"unsupported: {reason}"),),
                verification=("verify complete compiled timeline inventory",
                              "verify no collateral changes"),
                algebra_status=tuple(statuses),
            )
        raise IncrementalRoutingRefused(category, reason)

    requested = context.get("requested_backend")
    if requested == "full_rebuild":
        return ExecutionDecision(
            backend="full_rebuild", reasons=(str(context.get("reason") or "full build explicitly requested"),),
            rejected_alternatives=(RejectedBackend("incremental", str(context.get("ineligibility_reason") or "not requested or not eligible")),),
            verification=("verify complete compiled timeline inventory", "verify no collateral changes"),
            algebra_status=tuple(statuses))

    raise RoutingRefused("no backend is proven for operations: " + ", ".join(ids))
