"""VC-12: one shared transaction orchestration for edit execution adapters.

The four execution adapters - ``edit_patch``, ``composed_edit``,
``reel_incremental`` and ``reel_touchup`` - each grew their own copy of the
same lifecycle: preparation (identities, base generation), validation
(declared operations and spans), physical execution behind a Resolve hold,
read-back judgement, and a receipt. This module owns the parts that are
identical so the adapters own only what is theirs:

- preparation helpers: idempotency-key derivation and intent comparison
  (a retry with the same key and different contents is a new patch, not
  a retry);
- validation entry: callers still validate through ``edit_patch`` (the
  capability registry and the Rust ``validate_patch`` backend switch stay
  there) - this module only re-exports the call shape so every adapter
  prepares the same way;
- read-back judgement: one ``edit_algebra.verifies`` pass, one unrelated-
  change scan, one acceptance-contract evaluation, one postcondition
  judgement over the SAME observed snapshot - callers must not re-judge
  the same read-back a second time;
- receipts: every receipt leaves through ``transaction_receipt.standardize``
  (merged PR 2709), and every routing decision is attached with
  ``attach_routing`` beside the receipt (router merged in PR 2717, consumed
  here, never re-decided);
- inventory: one row enumeration shared by the decision and its final
  re-check, so the final inventory reuses the same evidence shape instead
  of a second hand-rolled listing.

What this module does NOT own (VC-12 avoid list):

- the router decision itself (``execution_router``) and the edit request
  type / compiler (``edit_algebra`` / ``operations`` / ``composed_edit``
  compile path) - it consumes both;
- Resolve-specific physical operations - they stay behind each adapter's
  qualified path (``edit_patch`` native executors, ``composed_edit``
  delete/place/restore, the incremental overlay placer);
- the Fusion / grade / still (including the still-duration preference
  route) / ripple specializations - they stay in ``reel_touchup``,
  ``reel_incremental`` and ``composed_edit`` untouched.

Python/Rust parity: the semantic checks (domains, operations, transform
keys, span coverage) run through the existing backend switch in
``edit_patch`` (Rust ``validate_patch`` / ``judge_conditions`` when
``edit_backend.rust()`` is present, else the Python fallback). This module
adds no second implementation of those checks - ``verify_readback`` calls
``edit_algebra.verifies`` and ``operation_acceptance.evaluate``, which own
their own backend routing, so a Rust change lands in one place.

Behaviour on the 27 reels Ren just touched must not change: every helper
preserves the receipt keys its callers already return; the lifecycle view
is additive (``receipt["lifecycle"]`` / ``execution_routing`` beside the
existing keys, never renaming them).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar


@dataclass(frozen=True)
class ExecutionInput:
    """Backend-neutral input envelope consumed by specialized executors.

    This is a contract wrapper, not another edit IR or transaction runner:
    ``intent`` remains the owning operation representation and ``decision``
    is the router's already-made choice.
    """

    intent: Mapping
    decision: Mapping
    project: str | None = None
    timeline: str | None = None
    base_generation: int | str | None = None
    idempotency_key: str | None = None
    expected_state: Mapping = field(default_factory=dict)
    preservation_constraints: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionRefusal:
    """Normalized refusal vocabulary shared across physical backends.

    AXI-36/AXI-42: the VALUE every backend reports when it refuses, and
    the one shape an exception is normalized into. It carries the next
    action (`fix`) and the recorded backend limitations beside the
    category, so a receipt states what to do about the refusal instead
    of only that it happened.
    """

    category: str
    reason: str
    retryable: bool = False
    recovery: Mapping = field(default_factory=dict)
    fix: str = ""
    limitations: tuple[str, ...] = ()

    CATEGORIES: ClassVar[frozenset[str]] = frozenset({
        "unsupported", "stale", "conflict", "backend_failure",
        "verification_failed", "recovery_required", "broken"})

    def __post_init__(self) -> None:
        if self.category not in self.CATEGORIES:
            raise ValueError(f"unknown execution refusal category: {self.category}")

    @classmethod
    def from_exception(cls, exc: BaseException) -> ExecutionRefusal:
        """Normalize a typed refusal exception into the shared value.

        A refusal that carries the engine-wide shape (``RenRefusal``)
        keeps its category, its next action and its limitations. Any
        other exception is a ``backend_failure``: it is not a decision
        Ren made, and calling it one would be a lie.
        """
        category = str(getattr(exc, "category", "") or "backend_failure")
        if category not in cls.CATEGORIES:
            category = "backend_failure"
        return cls(
            category=category,
            reason=str(getattr(exc, "why", "") or str(exc)),
            retryable=bool(getattr(exc, "retryable", False)),
            recovery=dict(getattr(exc, "recovery", {}) or {}),
            fix=str(getattr(exc, "fix", "") or ""),
            limitations=tuple(getattr(exc, "limitations", ()) or ()),
        )

    @property
    def recovery_instruction(self) -> Mapping[str, str]:
        """The typed next step for this failure.

        `recovery_instruction` owns the join between the failure
        CATEGORY and the recovery ACTION, so a host never has to
        improvise a retry from the category name.
        """
        from library.tools.recovery_instruction import instruction

        return instruction(self.category, reason=self.reason)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VerificationResult:
    """Evidence-bearing verification outcome; missing evidence is explicit."""

    status: str
    checks: tuple[Mapping, ...] = ()
    missing: tuple[str, ...] = ()
    evidence: Mapping = field(default_factory=dict)
    layers: Mapping = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RecoveryResult:
    """Normalized recovery outcome, without prescribing physical recovery."""

    status: str
    action: str
    reason: str = ""
    evidence: Mapping = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionResult:
    """Common result envelope around backend-specific receipt details."""

    status: str
    backend: str
    verification: VerificationResult
    recovery: RecoveryResult
    limitations: tuple[str, ...] = ()
    receipt: Mapping = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# ── Preparation: identity ─────────────────────────────────────────────

def derive_patch_id(project_name: str, timeline_id: str,
                    idempotency_key: str) -> str:
    """The durable patch id for one in-process write (same as edit_patch)."""
    identity = json.dumps(
        [project_name, timeline_id, idempotency_key],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str)
    return "ren-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def intent_form(operations: Sequence[Mapping]) -> list:
    """Identity form of operations for idempotency comparison.

    ``clip.insert`` writes the Resolve-assigned unique id back onto the op
    after apply; normalising both sides to the client id keeps the retry
    comparison stable. Owned here so ``edit_patch`` and every staged
    adapter compare the same form.
    """
    out = []
    for op in operations or ():
        op = dict(op)
        if "client_unique_id" in op:
            op["unique_id"] = op["client_unique_id"]
            del op["client_unique_id"]
        out.append(op)
    return out


def intents_equal(first: Mapping, second: Mapping) -> bool:
    """Whether two patch intents describe the same work."""
    dump = lambda payload: json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str)
    return dump(first) == dump(second)


# ── Read-back judgement: one pass over one snapshot ───────────────────
#
# The same evidence is judged once. Callers hand over the observed
# snapshot (`after`) they already read; nothing here re-reads Resolve.

def unrelated_clip_changes(*, operations: Sequence[Mapping],
                           affected_spans: Sequence[Sequence[int]],
                           id_map: Mapping | None,
                           before: Mapping, after: Mapping) -> list:
    """Changed clips outside the transaction's declared spans/targets.

    Moved verbatim from ``edit_patch._unrelated_clip_changes`` so every
    adapter reports the same collateral shape off the same two snapshots.
    """
    def rows(snapshot: Mapping) -> dict:
        return {str(clip["unique_id"]): clip
                for track in (snapshot.get("tracks") or [])
                for clip in (track.get("clips") or [])}

    old, new = rows(before), rows(after)
    targets = {str(op[key]) for op in (operations or ())
               for key in ("unique_id", "client_unique_id") if op.get(key)}
    targets.update(str(actual) for actual in (id_map or {}).values())
    changed = []
    for unique_id in sorted(old.keys() | new.keys()):
        if old.get(unique_id) == new.get(unique_id) or unique_id in targets:
            continue
        entry = old.get(unique_id) or new.get(unique_id)
        start = int(entry["record_in"])
        end = int(entry["record_out"])
        if any(start < span_end and span_start < end
               for span_start, span_end in (affected_spans or ())):
            continue
        changed.append({"unique_id": unique_id, "before": old.get(unique_id),
                        "after": new.get(unique_id)})
    return changed


def _preservation_verdict(*, operations: Sequence[Mapping],
                          before: Mapping, after: Mapping,
                          affected_spans: Sequence[Sequence[int]],
                          conflict_domains: Sequence[str],
                          id_map: Mapping | None = None) -> dict:
    """Judge the preservation invariant over the SAME two snapshots.

    One pass, off the snapshots the caller already read: the authorized
    scope, the expected delta computed from the operations, the actual
    delta read off the post-write snapshot, and the comparison. A change
    the scope did not authorize is collateral, and a transaction that
    produced collateral is not committed - the edit landed, but it landed
    on more than it was told to touch.

    `id_map` is the verified old-id to new-id mapping of an item a
    re-place gave a new id to.  The scope is built from the operations
    AS THE REQUEST NAMED THEM - the old id - and the delta is computed
    through the mapping, so the change lands on the target the request
    named instead of reading as a removal and an insertion.
    """
    from library.tools import preservation_invariant

    scope = preservation_invariant.scope_for(
        operations, conflict_domains=conflict_domains,
        affected_spans=affected_spans)
    verdict = preservation_invariant.invariant(
        scope=scope, before=before, operations=operations, after=after,
        id_map=id_map)
    return verdict.as_dict()


def verify_readback(patch_operations: Sequence[Mapping],
                    ran: Sequence[Mapping], after: Mapping, *,
                    base: Mapping, id_map: Mapping | None = None,
                    affected_spans: Sequence[Sequence[int]] = (),
                    conflict_domains: Sequence[str] = (),
                    postconditions: Sequence[Mapping] = (),
                    preservation_requirements: Sequence[Mapping] = (),
                    acceptance_evidence: Mapping | None = None,
                    stopped: Mapping | None = None,
                    hold_seconds: float = 0.0,
                    head_generation: int = 0,
                    patch_id: str = "", project: str = "",
                    timeline: str = "", backend: str = "edit_patch",
                    journaled: bool = False,
                    journal_errors: list | None = None) -> dict:
    """Judge physical execution only from the observed read-back.

    One ``verifies`` pass, one unrelated-change scan, one acceptance
    evaluation per op, one postcondition judgement - all off the SAME
    ``after`` snapshot the caller read once. ``ran`` carries the executed
    rows (``{"index", "op", "returned"}`` as ``edit_patch`` records
    them); judged entries keep those original patch indices so
    acceptance evidence keyed by index still lines up. Returns the
    receipt body ``edit_patch.verify_execution`` returns (same keys),
    so existing readers see no change.
    """
    from library.tools import edit_algebra, operation_acceptance

    verify_rows = []
    judged_ops = []
    for row in (ran or ()):
        op = patch_operations[row["index"]]
        verify_op = op
        if row["op"] != "clip.insert" and op.get("unique_id") in (id_map or {}):
            verify_op = {**op, "unique_id": (id_map or {})[op["unique_id"]]}
        verify_rows.append((row, verify_op))
        judged_ops.append(verify_op)
    failures = edit_algebra.verifies(judged_ops, after)
    judged = [{"index": row["index"], "op": row["op"],
               "returned": row["returned"], "failure": failure}
              for (row, _), failure in zip(verify_rows, failures)]
    unrelated_changes = unrelated_clip_changes(
        operations=judged_ops, affected_spans=affected_spans,
        id_map=id_map, before=base, after=after)
    supplied_evidence = dict(acceptance_evidence or {})
    acceptance_contracts = []
    for verify_op, result in zip(judged_ops, judged):
        index = result["index"]
        evidence = dict(supplied_evidence.get(index, {}))
        evidence.setdefault("unrelated_items_unchanged",
                            not unrelated_changes)
        acceptance_contracts.append(operation_acceptance.evaluate(
            verify_op, readback_passed=(result["failure"] is None),
            evidence=evidence))
    post_failures = _judge_conditions(postconditions, after, base)
    preservation = _preservation_verdict(
        operations=patch_operations, before=base, after=after,
        affected_spans=affected_spans, conflict_domains=conflict_domains,
        id_map=id_map)
    # The requester's OWN preservation requirements, judged over the
    # same two snapshots. A requirement that cannot be judged is
    # UNPROVABLE, never a pass - the rule the invariant follows.
    from library.tools import preservation_invariant
    named_requirements = preservation_invariant.judge_requirements(
        preservation_requirements, before=base, after=after)
    ok = (stopped is None and not post_failures
          and all(contract["passed"] for contract in acceptance_contracts)
          and not any(row["failure"] for row in judged)
          and preservation["status"] == "preserved"
          and all(row["status"] == preservation_invariant.PRESERVED
                  for row in named_requirements))
    return {
        "patch_id": patch_id,
        "status": "committed" if ok else "verification_failed",
        "resolve_write_status": "committed" if ok else "verification_failed",
        "base_generation": head_generation,
        "project": project,
        "timeline": timeline,
        "requested_operation_count": len(judged_ops),
        "expected_state": {"operations": list(judged_ops),
                            "postconditions": list(postconditions)},
        "readback": after,
        "backend": backend,
        "idempotency_key": patch_id,
        "operations": judged,
        "unrelated_changes": unrelated_changes,
        "acceptance_contracts": acceptance_contracts,
        "preservation": preservation,
        "preservation_requirements": named_requirements,
        "stopped": stopped,
        "recovery_result": {"status": "not_attempted",
                            "reason": ("transaction verified" if ok else
                                       "read-back retained for recovery")},
        "journal": {"intent_persisted": journaled,
                    "errors": journal_errors or []},
        "postcondition_failures": post_failures,
        "exclusive_hold_seconds": round(hold_seconds, 4),
    }


def _judge_conditions(conditions: Sequence[Mapping], snap: Mapping,
                      base: Mapping) -> list:
    """Postcondition judgement through the shared backend switch."""
    if not conditions:
        return []
    from library.tools import edit_backend
    rust = edit_backend.rust()
    if rust is not None:
        return rust.judge_conditions(list(conditions), snap, base)
    from library.tools import edit_patch as _patch

    return [f"{c['kind']}: {failure}" for c in conditions
            for failure in [_patch.CONDITIONS[c["kind"]][1](c, snap, base)]
            if failure]


# ── Receipts: one lifecycle vocabulary ────────────────────────────────

def standardize_receipt(receipt: Mapping) -> dict:
    """Publish a receipt through the common lifecycle and result contract."""
    from library.tools import transaction_receipt

    out = transaction_receipt.standardize(dict(receipt))
    contracts = out.get("acceptance_contracts") or []
    checks = tuple({"passed": row.get("passed"),
                    "operation": row.get("operation"),
                    "failures": row.get("failures", [])}
                   for row in contracts)
    verified = (bool(checks)
                and out.get("resolve_write_status", out.get("status")) == "committed"
                and all(row.get("passed") is True for row in contracts))
    rendered = out.get("rendered_verification") or {}
    rendered_layers = {}
    for name in ("pixels", "audio"):
        claim = rendered.get(name) if isinstance(rendered, Mapping) else None
        rendered_layers[name] = (
            {"status": "verified", "evidence": dict(claim)}
            if isinstance(claim, Mapping) and claim.get("status") == "verified"
            and claim.get("evidence") else
            {"status": "failed", "evidence": dict(claim)}
            if isinstance(claim, Mapping) and claim.get("status") == "failed"
            else {"status": "not_measured", "evidence": {}})
    recovery = out.get("recovery_result") or {}
    out["execution_contract"] = ExecutionResult(
        status=str(out.get("status") or "unknown"),
        backend=str(out.get("backend") or "unknown"),
        verification=VerificationResult(
            status=("verified" if verified else
                    "missing_evidence" if not checks else "failed"),
            checks=checks,
            missing=() if checks else ("operation acceptance evidence",),
            evidence={"acceptance_check_count": len(checks),
                      "readback_present": out.get("readback") is not None},
            layers={
                "stored_state": {"status": "verified" if verified else "failed",
                                 "checks": len(checks)},
                "rendered_pixels": rendered_layers["pixels"],
                "rendered_audio": rendered_layers["audio"],
            }),
        recovery=RecoveryResult(
            status=str(recovery.get("status") or "not_attempted"),
            action=str((out.get("recovery") or {}).get("action") or "none"),
            reason=str(recovery.get("reason") or ""),
            evidence={"recovery_record_present": bool(recovery)}),
        limitations=tuple(str(value) for value in out.get("limitations", ())),
        receipt={"transaction_id": out.get("patch_id"),
                 "idempotency_key": out.get("idempotency_key")},
    ).as_dict()
    return out


def attach_routing(receipt: Mapping, decision: Any,
                   *, key: str = "execution_routing") -> dict:
    """Attach a router decision beside a receipt without renaming keys.

    ``decision`` is an ``ExecutionDecision`` (``.as_dict()``) or an
    already-plain mapping. Returns a NEW receipt dict; the caller's keys
    are preserved verbatim.
    """
    out = dict(receipt)
    if decision is None:
        return out
    if hasattr(decision, "as_dict"):
        out[key] = decision.as_dict()
    elif isinstance(decision, Mapping):
        out[key] = dict(decision)
    else:
        out[key] = decision
    return out


def composed_lifecycle(*, planned_count: int, applied: Mapping,
                       verified: Mapping,
                       rederivation_required: Mapping) -> dict:
    """Map a composed edit's phases onto the shared lifecycle stages.

    Additive observability for ``composed_edit`` receipts: the composed
    receipt keeps its own keys (plan/captured/deleted/placed/verified/
    restored/rederived); this dict names the same outcome in the shared
    ``PLANNED .. VERIFIED`` vocabulary so every adapter reports one
    lifecycle. Promotion and delivery stay the caller's (staged edits
    report ``awaiting_promotion`` / ``not_delivered``).
    """
    from library.tools import transaction_receipt

    applied_ok = bool((applied or {}).get("status") == "applied")
    verified_ok = bool((verified or {}).get("status") == "verified")
    if verified_ok and applied_ok:
        stage = "VERIFIED"
    elif applied_ok:
        stage = "APPLIED"
    elif planned_count:
        stage = "PLANNED"
    else:
        stage = "PLANNED"
    probe = {"patch_id": None, "project": None, "timeline": None,
             "operations": [], "status":
             ("committed" if verified_ok else "verification_failed")}
    life = transaction_receipt.lifecycle(
        probe, stage=stage,
        expected_state={"changes": planned_count},
        actual_state={"applied": applied, "verified": verified},
        backend="composed_edit",
        verification={"outcome": verified,
                      "rederivation_required": rederivation_required})
    life["history"] = [entry for entry in life.get("history", ())
                       if entry in ("PLANNED", "APPLIED", "VERIFIED")
                       or entry == stage]
    if not life["history"] or life["history"][-1] != stage:
        life["history"].append(stage)
    return life


# ── Inventory: one enumeration shared by decision and re-check ───────

class SharedRowInventory:
    """One row enumeration shared by a decision and its final re-check.

    Same shape as ``reel_incremental._RowInventory`` (counts, names and
    item handles per video/audio row), owned here so the incremental
    decision and its post-patch re-check enumerate rows one way. Takes
    live handles; performs no Resolve writes.
    """

    def __init__(self, timeline: Any) -> None:
        self.rows: dict = {}
        self.names: dict = {}
        self.counts: dict = {}
        for kind in ("video", "audio"):
            self.counts[kind] = int(timeline.GetTrackCount(kind) or 0)
            for index in range(1, self.counts[kind] + 1):
                self.names[kind, index] = (
                    timeline.GetTrackName(kind, index) or "")
                self.rows[kind, index] = list(
                    timeline.GetItemListInTrack(kind, index) or ())

    def GetTrackCount(self, kind: str) -> int:
        return self.counts[kind]

    def GetTrackName(self, kind: str, index: int) -> str:
        return self.names[kind, index]

    def GetItemListInTrack(self, kind: str, index: int) -> list:
        return self.rows.get((kind, index), ())


def shared_inventory(timeline: Any) -> SharedRowInventory:
    """Enumerate a timeline's rows once for a decision and its re-check."""
    if isinstance(timeline, SharedRowInventory):
        return timeline
    try:
        from library.tools import reel_incremental as _incremental

        if isinstance(timeline, _incremental._RowInventory):
            wrapped = SharedRowInventory.__new__(SharedRowInventory)
            wrapped.rows = dict(timeline.rows)
            wrapped.names = dict(timeline.names)
            wrapped.counts = dict(timeline.counts)
            return wrapped
    except (ImportError, AttributeError):
        return SharedRowInventory(timeline)
    return SharedRowInventory(timeline)
