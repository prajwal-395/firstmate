"""One owner for AXI compound-transaction semantics.

The AXI surface already owns most of what a multi-operation transaction
needs - the edit algebra (``edit_algebra``), the router
(``execution_router``), the durable patch (``edit_patch``), the shared
lifecycle (``transaction_orchestration`` / ``transaction_receipt``) and
the compensated batch executor (``resolve_axi_core``). What was missing
is a single place that answers the five questions a caller asks about a
MULTI-OPERATION request, before and after the write:

- is this shape of request supported at all, and how will it be undone
  if it fails halfway (AXI-043, AXI-044);
- does the live project still answer to the exact identity and revision
  the request was planned against (AXI-045);
- is this submission a retry of work already done (AXI-046);
- what is the state of the timeline's recovery, and may another write
  happen while it is unresolved (AXI-047, AXI-048);
- what does the receipt have to say about what was asked, what happened,
  what was left alone, and what proves it (AXI-049, AXI-050).

This module owns those answers and nothing else. It does not write to
Resolve, does not decide the editorial intent, and does not own the
operation vocabulary - every operation id it names is read out of
``edit_algebra``. Where another module already answers a question
(``edit_patch`` for the durable patch, ``direct_transaction`` for the
in-place executor), this module calls that module rather than
reimplementing it.

The state ladder lives in ``transaction_receipt``; this module reports
it, and adds the two states the AXI brief names that the ladder did not
have: PREPARED (the durable intent reached the journal before the first
write) and PROMOTED (a staged change reached the live timeline).
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

# The six states an AXI transaction distinguishes, in the order they are
# reached. ``transaction_receipt.STAGES`` is the ladder itself; this is
# the subset the AXI brief names, in one place, so a receipt can be
# read against it without counting ladder stages.
AXI_STATES = ("PREPARED", "APPLIED", "VERIFIED", "COMMITTED", "PROMOTED",
              "DELIVERED")


class TransactionRefused(RuntimeError):
    """A transaction cannot run as asked, and nothing was written."""

    def __init__(self, category: str, reason: str, *,
                 detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "reason": self.reason,
                "detail": copy.deepcopy(self.detail)}


# ── AXI-043 / AXI-044: the declared compound transactions ──────────────
#
# A compound scenario is a SHAPE of multi-operation request, not an
# operation. It declares which operation ids it covers, which backends
# have a physical path for it today, how the target rows relate, how it
# is undone when it fails halfway, and the boundaries under which it is
# qualified.
#
# ``implemented`` is the honest statement about the CODE, not about the
# evidence: a scenario with a physical executor and a compensation path
# is implemented; a scenario that is only declared is refused at the
# preflight rather than attempted. Qualification evidence is separate and
# lives in the qualification registry (see ``axi_qualification``).


@dataclass(frozen=True)
class CompoundScenario:
    """One declared shape of multi-operation transaction."""

    name: str
    operations: tuple[str, ...]
    backends: tuple[str, ...]
    target_relation: str = "any"
    max_operations: int = 0
    atomicity: str = "recoverable_sequence"
    recovery: str = "reverse_order_undo"
    qualification: tuple[Mapping[str, Any], ...] = ()
    punch_list_item: str = ""
    implemented: bool = False
    live_evidence: str = ""

    def accepts(self, operations: Sequence[Mapping[str, Any]]) -> bool:
        """Whether this scenario covers exactly these operations."""
        ids = tuple(str(operation.get("op") or "") for operation in operations)
        if not ids or any(not name for name in ids):
            return False
        if self.max_operations and len(ids) > self.max_operations:
            return False
        return set(ids) == set(self.operations)

    def targets_satisfied(self, operations: Sequence[Mapping[str, Any]]) -> bool:
        """Whether the rows the operations name relate as the scenario says."""
        targets = [str(operation.get("unique_id") or "")
                   for operation in operations]
        if self.target_relation == "any":
            return True
        if self.target_relation == "same_target":
            return bool(targets) and all(target == targets[0]
                                         for target in targets)
        if self.target_relation == "distinct_targets":
            return bool(targets) and all(targets) and len(
                set(targets)) == len(targets)
        raise ValueError(f"unknown target relation {self.target_relation!r}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "operations": list(self.operations),
            "backends": list(self.backends),
            "target_relation": self.target_relation,
            "max_operations": self.max_operations,
            "atomicity": self.atomicity,
            "recovery": self.recovery,
            "qualification": [dict(row) for row in self.qualification],
            "punch_list_item": self.punch_list_item,
            "implemented": self.implemented,
            "live_evidence": self.live_evidence,
        }


COMPOUND_SCENARIOS: dict[str, CompoundScenario] = {}


def _scenario(scenario: CompoundScenario) -> CompoundScenario:
    COMPOUND_SCENARIOS[scenario.name] = scenario
    return scenario


# The four shapes the AXI brief names (AXI-044), plus the compound shapes
# the code already executes. A shape with ``implemented=False`` is
# DECLARED: the preflight refuses it and says which evidence would
# qualify it, rather than attempting a multi-operation write whose undo
# path has never been exercised.
_scenario(CompoundScenario(
    name="trim_insert",
    operations=("clip.trim", "clip.insert"),
    backends=("reel_touchup",),
    target_relation="distinct_targets",
    max_operations=2,
    atomicity="recoverable_sequence",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "one_of",
         "value": ["video", "audio"]},
        {"field": "timeline_structure", "operator": "one_of",
         "value": ["single_video_track", "video_plus_audio"]},
    ),
    punch_list_item="AXI-044",
    implemented=True,
    live_evidence=("a trim that shortens the A-roll and an insert that "
                   "places the freed material, with the dialogue audio "
                   "unlinked and the transform preserved, on a live "
                   "Resolve timeline with a populated video row")))

_scenario(CompoundScenario(
    name="remove_ripple",
    operations=("clip.remove", "clip.ripple"),
    backends=(),
    target_relation="distinct_targets",
    max_operations=2,
    atomicity="recoverable_sequence",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "equals", "value": "video"},
        {"field": "linked_audio_configuration", "operator": "one_of",
         "value": ["none", "linked_audio"]},
        {"field": "timeline_structure", "operator": "equals",
         "value": "single_video_track"},
    ),
    punch_list_item="AXI-044",
    implemented=False,
    live_evidence=("a track-scoped ripple that closes the gap left by a "
                   "removed clip, verified against the SAME timeline with "
                   "the removed clip restored, on a live Resolve timeline "
                   "whose row holds more than one clip")))

_scenario(CompoundScenario(
    name="replace_caption",
    operations=("clip.replace", "caption.style"),
    backends=("reel_touchup",),
    target_relation="distinct_targets",
    max_operations=2,
    atomicity="recoverable_sequence",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "equals", "value": "video"},
        {"field": "timeline_structure", "operator": "one_of",
         "value": ["video_plus_caption"]},
    ),
    punch_list_item="AXI-044",
    implemented=True,
    live_evidence=("a source replacement that keeps the clip's authorised "
                   "adjustments, and a caption restyle that renders and "
                   "maps the previous generation back, on a live Resolve "
                   "timeline carrying a rendered caption")))

_scenario(CompoundScenario(
    name="broll_over_linked_audio",
    operations=("broll.insert",),
    backends=(),
    target_relation="any",
    atomicity="single_operation",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "equals", "value": "video"},
        {"field": "linked_audio_configuration", "operator": "equals",
         "value": "linked_audio"},
        {"field": "timeline_structure", "operator": "one_of",
         "value": ["video_plus_audio", "video_with_linked_audio"]},
    ),
    punch_list_item="AXI-044",
    implemented=False,
    live_evidence=("a B-roll cover placed over a clip whose audio is "
                   "LINKED to the video, with the dialogue audio left "
                   "audible and the underlying clip's transform and grade "
                   "unchanged, on a live Resolve timeline")))

# The compound shapes the code already executes, kept here so the
# registry is the ONE place a batch shape is declared.
_scenario(CompoundScenario(
    name="trim_and_delete",
    operations=("clip.trim", "clip.delete"),
    backends=("resolve_axi",),
    target_relation="any",
    atomicity="recoverable_sequence",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "one_of",
         "value": ["video", "audio"]},
    ),
    implemented=True,
    live_evidence="a trim and a delete of different rows on one live "
                  "Resolve timeline, each undone from its own journal entry"))

_scenario(CompoundScenario(
    name="caption_restyle_batch",
    operations=("caption.style",),
    backends=("resolve_axi",),
    target_relation="any",
    atomicity="recoverable_sequence",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "one_of",
         "value": ["video", "audio"]},
    ),
    implemented=True,
    live_evidence="two caption restyles on different placements, each "
                  "swapped back to the previous rendered generation"))

_scenario(CompoundScenario(
    name="overlay_transform_pair",
    operations=("overlay.position", "overlay.resize"),
    backends=("reel_touchup",),
    target_relation="same_target",
    max_operations=2,
    atomicity="recoverable_sequence",
    recovery="reverse_order_undo",
    qualification=(
        {"field": "media_type", "operator": "one_of",
         "value": ["video", "audio"]},
    ),
    implemented=True,
    live_evidence="a position and a resize applied to the SAME overlay, "
                  "restored to the previous geometry"))


def scenario_for(backend: str, operations: Sequence[Mapping[str, Any]]
                  ) -> CompoundScenario | None:
    """The declared scenario covering this backend and operation set."""
    for scenario in COMPOUND_SCENARIOS.values():
        if backend not in scenario.backends:
            continue
        if not scenario.accepts(operations):
            continue
        if not scenario.targets_satisfied(operations):
            continue
        return scenario
    return None


def declared_scenario(operations: Sequence[Mapping[str, Any]]
                      ) -> CompoundScenario | None:
    """The declared shape these operations describe, whatever the backend.

    A shape with no physical path today is still DECLARED, and the
    preflight has to be able to name it - otherwise a caller is told
    only that "no shape covers this", when the truth is that the shape
    exists and has never been exercised.
    """
    for scenario in COMPOUND_SCENARIOS.values():
        if not scenario.accepts(operations):
            continue
        if not scenario.targets_satisfied(operations):
            continue
        return scenario
    return None


def batch_supported(backend: str, operations: Sequence[Mapping[str, Any]]
                    ) -> bool:
    """Whether this backend can run this exact batch shape and undo it.

    A batch is supported only when its physical executor AND its
    compensation path are both already available. A shape that is
    declared but not implemented is refused here, before any write.
    """
    if len(operations) <= 1:
        return True
    scenario = scenario_for(backend, operations)
    return bool(scenario is not None and scenario.implemented)


# ── AXI-043: the plan ─────────────────────────────────────────────────


@dataclass(frozen=True)
class TransactionPlan:
    """What a multi-operation request will do, and how it is undone."""

    transaction_id: str
    project: str
    timeline: str
    operations: tuple[Mapping[str, Any], ...]
    scenario: str = ""
    atomicity: str = "single_operation"
    recovery: str = "not_required"
    qualified: bool = False
    qualification_gaps: tuple[str, ...] = ()
    refusal: str = ""
    refusal_category: str = ""

    @property
    def supported(self) -> bool:
        return not self.refusal

    def as_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "project": self.project,
            "timeline": self.timeline,
            "operations": [dict(operation) for operation in self.operations],
            "scenario": self.scenario,
            "atomicity": self.atomicity,
            "recovery": self.recovery,
            "qualified": self.qualified,
            "qualification_gaps": list(self.qualification_gaps),
            "refusal": self.refusal,
            "refusal_category": self.refusal_category,
        }


def plan_operations(*, operations: Sequence[Mapping[str, Any]], backend: str,
                    project: str, timeline: str,
                    transaction_id: str = "") -> TransactionPlan:
    """Decide whether this request is a supported transaction, and how.

    Refuses before the first write when the shape has no executor and no
    compensation path, and says which compound scenario was expected.
    """
    ops = tuple(dict(operation) for operation in operations or ())
    transaction_id = transaction_id or ""
    if not ops:
        return TransactionPlan(
            transaction_id=transaction_id, project=project, timeline=timeline,
            operations=(), refusal="the request carries no operations",
            refusal_category="unsupported")
    if len(ops) == 1:
        return TransactionPlan(
            transaction_id=transaction_id, project=project, timeline=timeline,
            operations=ops, atomicity="single_operation",
            recovery="per_operation_undo")
    scenario = scenario_for(backend, ops)
    if scenario is None:
        declared = declared_scenario(ops)
        if declared is not None:
            return TransactionPlan(
                transaction_id=transaction_id, project=project,
                timeline=timeline, operations=ops, scenario=declared.name,
                atomicity=declared.atomicity, recovery=declared.recovery,
                qualification_gaps=(declared.live_evidence,),
                refusal=(f"the {declared.name!r} compound transaction is "
                         f"declared but has no verified execution and undo "
                         f"path on {backend}; it needs: "
                         f"{declared.live_evidence}"),
                refusal_category="unsupported")
        names = sorted({
            name for name, entry in COMPOUND_SCENARIOS.items()
            if set(entry.operations) & {str(op.get("op")) for op in ops}})
        detail = (f"the compound shapes that cover these operations are "
                  f"{names}") if names else (
            "no compound transaction shape covers these operations")
        return TransactionPlan(
            transaction_id=transaction_id, project=project, timeline=timeline,
            operations=ops,
            refusal=(f"{backend} has no supported transaction shape for "
                     f"{[str(op.get('op')) for op in ops]}; {detail}"),
            refusal_category="unsupported")
    if not scenario.implemented:
        return TransactionPlan(
            transaction_id=transaction_id, project=project, timeline=timeline,
            operations=ops, scenario=scenario.name,
            atomicity=scenario.atomicity, recovery=scenario.recovery,
            qualification_gaps=(scenario.live_evidence,),
            refusal=(f"the {scenario.name!r} compound transaction is declared "
                     f"but has no verified execution and undo path on "
                     f"{backend}; it needs: {scenario.live_evidence}"),
            refusal_category="unsupported")
    gaps = qualification_gaps(scenario, backend)
    return TransactionPlan(
        transaction_id=transaction_id, project=project, timeline=timeline,
        operations=ops, scenario=scenario.name, atomicity=scenario.atomicity,
        recovery=scenario.recovery, qualified=not gaps,
        qualification_gaps=gaps)


def qualification_gaps(scenario: CompoundScenario, backend: str
                        ) -> tuple[str, ...]:
    """What the scenario still needs before it is production-usable."""
    records = qualified_compound_records()
    record = records.get((scenario.name, backend))
    if record is not None:
        return ()
    if scenario.implemented and scenario.punch_list_item == "":
        return ()
    return (scenario.live_evidence or
            f"the {scenario.name!r} compound transaction has no recorded "
            f"qualification evidence on {backend}",)


def qualified_compound_records() -> dict[tuple[str, str], dict[str, Any]]:
    """The compound qualification rows that carry complete passing evidence."""
    from library.tools import axi_qualification

    return axi_qualification.load_compound_records()


# ── AXI-045: conflict detection before mutation ──────────────────────


@dataclass(frozen=True)
class Conflict:
    """Whether the live project still answers to the planned request."""

    detected: bool
    category: str = ""
    reason: str = ""
    expected: Mapping[str, Any] = field(default_factory=dict)
    observed: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"detected": self.detected, "category": self.category,
                "reason": self.reason, "expected": dict(self.expected),
                "observed": dict(self.observed)}


NO_CONFLICT = Conflict(detected=False)


def detect_conflict(*, expected_project: str, expected_timeline: str,
                     observed_project: str, observed_timeline: str,
                     expected_base_generation: int | None = None,
                     observed_generation: int | None = None,
                     expected_model_revision: int | None = None,
                     observed_model_revision: int | None = None
                     ) -> Conflict:
    """Whether the request may still be written to the live timeline.

    Two kinds of conflict, and both are decided BEFORE a write:

    - identity: the live project or timeline is not the one the request
      names, exactly. A near-match is a conflict, not a tolerance.
    - revision: the recorded generation or the Project Model head moved
      since the request was planned.
    """
    if expected_project != observed_project:
        return Conflict(
            detected=True, category="identity",
            reason=(f"the request names project {expected_project!r}, but the "
                    f"live project is {observed_project!r}"),
            expected={"project": expected_project},
            observed={"project": observed_project})
    if expected_timeline != observed_timeline:
        return Conflict(
            detected=True, category="identity",
            reason=(f"the request names timeline {expected_timeline!r}, but "
                    f"the live timeline is {observed_timeline!r}"),
            expected={"timeline": expected_timeline},
            observed={"timeline": observed_timeline})
    if (expected_base_generation is not None
            and observed_generation is not None
            and int(expected_base_generation) != int(observed_generation)):
        return Conflict(
            detected=True, category="revision",
            reason=(f"the request was planned on generation "
                    f"{expected_base_generation}, and the recorded head is "
                    f"{observed_generation}"),
            expected={"base_generation": int(expected_base_generation)},
            observed={"generation": int(observed_generation)})
    if (expected_model_revision is not None
            and observed_model_revision is not None
            and int(expected_model_revision) != int(observed_model_revision)):
        return Conflict(
            detected=True, category="revision",
            reason=(f"the request was planned on Project Model revision "
                    f"{expected_model_revision}, and the head is "
                    f"{observed_model_revision}"),
            expected={"model_revision": int(expected_model_revision)},
            observed={"model_revision": int(observed_model_revision)})
    return NO_CONFLICT


# ── AXI-046: idempotent submission ────────────────────────────────────


def transaction_identity(*, project: str, timeline: str,
                         idempotency_key: str) -> str:
    """The durable identity of one transaction on one timeline."""
    from library.tools.transaction_orchestration import derive_patch_id

    return derive_patch_id(project, timeline, idempotency_key)


def intent_digest(operations: Sequence[Mapping[str, Any]]) -> str:
    """A stable digest of what a transaction was asked to do."""
    from library.tools.transaction_orchestration import intent_form

    body = json.dumps(intent_form(operations), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def prior_outcome(project_folder: str, transaction_id: str, *,
                  operations: Sequence[Mapping[str, Any]]
                  ) -> dict[str, Any] | None:
    """What a previous submission of this transaction already did.

    Returns ``None`` when the transaction was never submitted. Returns
    ``{"status": "committed", "receipt": ...}`` when the same intent was
    already committed, so the caller can answer the retry without
    writing again. Returns ``{"status": "conflict", ...}`` when the id
    was used for DIFFERENT work, which is a refusal, not a retry.
    """
    digest = intent_digest(operations)
    prior: dict[str, Any] | None = None
    for event in _journal_events(project_folder):
        fields = event.get("fields", {})
        if str(fields.get("transaction_id") or "") != str(transaction_id):
            continue
        prior = fields
    if prior is None:
        return None
    recorded = prior.get("intent_digest")
    if recorded and recorded != digest:
        return {"status": "conflict",
                "reason": ("this transaction id was already used for "
                           "different operations"),
                "transaction_id": transaction_id}
    stage = str(prior.get("stage") or "")
    if stage in ("committed", "promoted", "delivered"):
        return {"status": "committed", "stage": stage,
                "transaction_id": transaction_id,
                "receipt": prior.get("receipt")}
    return {"status": "unresolved", "stage": stage,
            "transaction_id": transaction_id}


def record_transaction(project_folder: str, transaction_id: str, stage: str,
                       **fields: Any) -> bool:
    """Journal one transaction stage. The write happens BEFORE the stage."""
    from library.tools import event_log

    try:
        event_log.emit(project_folder, event_log.EDIT_TRANSACTION_STAGE,
                       task="axi-transaction", transaction_id=transaction_id,
                       stage=stage, **fields)
    except OSError:
        return False
    return True


def resolve_recovery(project_folder: str, transaction_id: str, *,
                     action: str = "recovered", reason: str = "",
                     project: str = "", timeline: str = "") -> dict[str, Any]:
    """Close an outstanding recovery, and record the closure.

    The AXI-048 gate blocks further mutation while a recovery is
    outstanding. This is how that state ends: the recovery was applied,
    or the captain decided the timeline is fine as it stands. Either way
    the decision is journalled, so the next write is not blocked by a
    decision that has already been made.
    """
    if action not in {"recovered", "abandoned"}:
        raise ValueError("a recovery is resolved as 'recovered' or "
                         "'abandoned'")
    if not project_folder:
        raise TransactionRefused(
            "recovery_required",
            "resolving a recovery needs the project journal that records it")
    state = recovery_state(project_folder)
    outstanding = next((row for row in state.transactions
                        if str(row.get("transaction_id")) == str(transaction_id)),
                       None)
    if outstanding is None:
        raise TransactionRefused(
            "recovery_required",
            f"transaction {transaction_id!r} has no outstanding recovery")
    recorded = record_transaction(
        project_folder, transaction_id, action,
        project=project or str(outstanding.get("project") or ""),
        timeline=timeline or str(outstanding.get("timeline") or ""),
        reason=reason, operations=outstanding.get("operations") or [])
    return {"transaction_id": transaction_id, "action": action,
            "reason": reason, "recorded": recorded}


# ── AXI-047 / AXI-048: the recovery state ─────────────────────────────


@dataclass(frozen=True)
class RecoveryState:
    """Whether the timeline still owes a recovery, and to whom."""

    unresolved: bool = False
    transactions: tuple[Mapping[str, Any], ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"unresolved": self.unresolved,
                "transactions": [dict(row) for row in self.transactions],
                "reason": self.reason}


CLEAR_RECOVERY = RecoveryState()


class _JournalCursor:
    """An incremental reader over one project's append-only journal.

    The recovery gate is asked before every write, and a build applies
    hundreds of writes. Re-reading and re-parsing the whole journal for
    each of them is a cost that grows with the build; reading only the
    bytes appended since the last read is not. The journal is written by
    appending under a file lock, so the offset is exact - and a file
    that shrank was replaced, not appended to, so the cursor starts
    again.
    """

    def __init__(self) -> None:
        self.offset = 0
        self.partial = ""
        self.events: list[dict[str, Any]] = []

    def read(self, path: Any) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size < self.offset:
            self.offset = 0
            self.partial = ""
            self.events = []
        if size == self.offset:
            return
        with path.open("rb") as handle:
            handle.seek(self.offset)
            chunk = handle.read()
        self.offset += len(chunk)
        text = self.partial + chunk.decode("utf-8", errors="replace")
        lines = text.split("\n")
        self.partial = lines.pop()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.events.append(event)


_JOURNAL_CURSORS: dict[str, _JournalCursor] = {}


def _journal_events(project_folder: str) -> list[dict[str, Any]]:
    """The project's journalled events, read incrementally."""
    from library.tools import event_log

    cursor = _JOURNAL_CURSORS.get(project_folder)
    if cursor is None:
        cursor = _JournalCursor()
        _JOURNAL_CURSORS[project_folder] = cursor
    try:
        path = event_log.log_path(project_folder)
    except Exception:  # noqa: BLE001 - a project without a journal is empty
        return []
    cursor.read(path)
    return cursor.events


def recovery_state(project_folder: str, *, project: str = "",
                   timeline: str = "") -> RecoveryState:
    """Which transactions on this project have not reached a clean end.

    A transaction is unresolved when its last durable stage is neither a
    commit nor a delivery. That covers a write that failed halfway, a
    compensation that could not restore, and a write whose verification
    never ran.
    """
    from library.tools import transaction_receipt

    if not project_folder:
        return CLEAR_RECOVERY
    pending = transaction_receipt.interrupted(
        _journal_events(project_folder))
    rows = []
    for row in pending:
        if project and str(row.get("project") or "") not in ("", project):
            continue
        if timeline and str(row.get("timeline") or "") not in ("", timeline):
            continue
        rows.append(row)
    if not rows:
        return CLEAR_RECOVERY
    return RecoveryState(
        unresolved=True, transactions=tuple(rows),
        reason=("an unresolved transaction on this timeline must be "
                "recovered or abandoned before another write: "
                + ", ".join(str(row.get("transaction_id")) for row in rows)))


def require_recovery_clear(project_folder: str, *, project: str = "",
                            timeline: str = "",
                            transaction_id: str = "") -> RecoveryState:
    """Refuse a write while an unresolved recovery is outstanding.

    This is the AXI-048 gate: an unresolved recovery is not a warning,
    it stops the next mutation. The refusal names the transaction that
    owes the recovery.
    """
    state = recovery_state(project_folder, project=project,
                           timeline=timeline)
    if not state.unresolved:
        return CLEAR_RECOVERY
    outstanding = [row for row in state.transactions
                   if str(row.get("transaction_id")) != str(transaction_id)]
    if not outstanding:
        return CLEAR_RECOVERY
    raise TransactionRefused(
        "recovery_required",
        ("unresolved recovery on this timeline blocks further mutation: "
         + ", ".join(str(row.get("transaction_id")) for row in outstanding)),
        detail={"outstanding": [dict(row) for row in outstanding],
                "state": state.as_dict()})


# ── AXI-049 / AXI-050: the receipt ────────────────────────────────────


def untouched_regions(*, operations: Sequence[Mapping[str, Any]],
                      affected_spans: Sequence[Sequence[int]],
                      before: Mapping[str, Any], after: Mapping[str, Any]
                      ) -> list[dict[str, Any]]:
    """The regions outside the authorized scope that did not change.

    ``unrelated_changes`` says what moved that should not have. This
    says what was in scope of preservation and demonstrably stayed put -
    the answer to "was the rest of the timeline left alone".
    """
    def rows(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
        return {str(clip["unique_id"]): clip
                for track in (snapshot.get("tracks") or [])
                for clip in (track.get("clips") or [])}

    old, new = rows(before), rows(after)
    targets = {str(operation[key]) for operation in (operations or ())
               for key in ("unique_id", "client_unique_id")
               if operation.get(key)}
    spans = [(int(start), int(end)) for start, end in (affected_spans or ())]
    regions = []
    for unique_id in sorted(old.keys() | new.keys()):
        if unique_id in targets:
            continue
        entry = old.get(unique_id) or new.get(unique_id) or {}
        start = int(entry.get("record_in") or 0)
        end = int(entry.get("record_out") or 0)
        if any(start < span_end and span_start < end
               for span_start, span_end in spans):
            continue
        changed = old.get(unique_id) != new.get(unique_id)
        regions.append({
            "unique_id": unique_id,
            "record_in": start, "record_out": end,
            "track_type": str(entry.get("track_type") or ""),
            "status": "changed" if changed else "unchanged",
        })
    return regions


def state_ladder(*, prepared: bool, applied: bool, verified: bool,
                 committed: bool, promoted: bool, delivered: bool
                 ) -> dict[str, Any]:
    """The six AXI states, and which of them this transaction reached."""
    reached = {
        "PREPARED": bool(prepared),
        "APPLIED": bool(applied),
        "VERIFIED": bool(verified),
        "COMMITTED": bool(committed),
        "PROMOTED": bool(promoted),
        "DELIVERED": bool(delivered),
    }
    order = list(AXI_STATES)
    last = None
    for name in order:
        if reached[name]:
            last = name
    return {"states": reached, "order": order, "stage": last,
            "reached": [name for name in order if reached[name]]}


def build_receipt(*, request_id: str, project: str, timeline: str,
                   backend: str, operations: Sequence[Mapping[str, Any]],
                   actual_changes: Sequence[Mapping[str, Any]],
                   verification_evidence: Any = None,
                   untouched: Sequence[Mapping[str, Any]] = (),
                   route: Mapping[str, Any] | None = None,
                   errors: Sequence[Mapping[str, Any]] = (),
                   recovery: Mapping[str, Any] | None = None,
                   atomicity: str = "single_operation",
                   promoted: bool = False, delivered: bool = False,
                   prepared: bool = True) -> dict[str, Any]:
    """Build the transaction receipt body every AXI write publishes.

    The seven things a receipt has to carry (AXI-049): what was
    requested, what actually changed, what was left untouched, the route
    that was taken, what went wrong, how recovery went, and the evidence
    that proves it. The ladder (AXI-050) is in ``state_ladder``.
    """
    applied = bool(actual_changes)
    verified = bool(verification_evidence) and not errors
    committed = verified and applied
    receipt: dict[str, Any] = {
        "request_id": request_id,
        "transaction_id": request_id,
        "project": project,
        "timeline": timeline,
        "backend": backend,
        "atomicity": atomicity,
        "requested_changes": [dict(operation) for operation in operations],
        "expected_changes": [dict(operation) for operation in operations],
        "actual_changes": [dict(change) for change in actual_changes],
        "untouched_regions": [dict(region) for region in untouched],
        "route": dict(route or {}),
        "errors": [dict(error) for error in errors],
        "recovery": dict(recovery or {"status": "not_required"}),
        "verification_evidence": verification_evidence,
    }
    receipt["state_ladder"] = state_ladder(
        prepared=prepared, applied=applied, verified=verified,
        committed=committed, promoted=promoted, delivered=delivered)
    return receipt


# ── The one command a caller needs on this module ─────────────────────


def main(argv: list[str] | None = None) -> int:
    """Close an outstanding recovery, or report the state of one."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Inspect and close an unresolved AXI transaction")
    parser.add_argument("project_folder")
    parser.add_argument("--transaction-id", default="")
    parser.add_argument("--action", default="recovered",
                        choices=("recovered", "abandoned"))
    parser.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.transaction_id:
        outcome = resolve_recovery(
            args.project_folder, args.transaction_id, action=args.action,
            reason=args.reason)
        print(json.dumps(outcome, indent=2, ensure_ascii=False, default=str))
        return 0
    state = recovery_state(args.project_folder)
    print(json.dumps(state.as_dict(), indent=2, ensure_ascii=False,
                     default=str))
    return 0 if not state.unresolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
