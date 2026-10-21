"""AXI-014: a COMPOUND request is a transaction, not a sequence of writes.

A request with more than one operation is a transaction
(``docs/AXI_TRANSACTIONS.md``). The rules that make it one live here,
and this module owns nothing else: it writes nothing to Resolve, it
decides no editorial intent, and it never re-implements a check another
module already owns.

Five rules, and the reason each exists:

1. **The write order and the dependencies are DECLARED.** A request
   arrives as a list, and a list has no meaning: a ripple that closes
   the gap a delete left is a different transaction when the delete
   lands first. The engine declares the dependencies it knows
   (``ENGINE_DEPENDENCIES``), the requester may declare more
   (``depends_on``), and a request whose declared order CONTRADICTS a
   declared dependency is refused before the first write rather than
   executed in an order nobody asked for.

2. **A required preservation constraint the planned writes cannot
   satisfy refuses the request BEFORE the first mutation.** The
   preservation requirements were already judged, but only on the
   READ-BACK: the write landed, and then the engine noticed that the
   request asked it to change the very clip it required unchanged. A
   constraint that provably cannot hold is a contradiction in the
   request, and a contradiction is answered before anything is written.

3. **A partial mutation is recovered, and the recovery is only claimed
   when a fresh read-back matches the VALIDATED generation.** Reversing
   what landed is the recovery (``edit_recovery``); proving the
   timeline is back where it was is what makes the recovery real. The
   proof is a comparison against the recorded snapshot of the generation
   the request was planned against - the same comparison the differ
   and the replace guard use.

4. **A transaction aborted before its first write owes no recovery and
   leaves no hold.** The journal proves whether a write was attempted
   (``transaction_receipt.WRITE_ATTEMPT_STAGES``); a transaction that
   never wrote is closed, and the AXI-048 gate stops reading it as an
   outstanding recovery. A transaction that MAY have written keeps its
   hold, because that recovery is real.

5. **A possibly committed mutation is never retried silently.** A
   transaction whose last durable stage is neither a commit nor a
   delivery may have written; the outcome is not observable from the
   journal, so the same call is REFUSED (``axi_transaction``'s prior
   outcome) instead of being repeated. A retry is answered with the
   prior outcome, never with a second write.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class CompoundRefused(RuntimeError):
    """A compound request cannot run as asked, and nothing was written."""

    def __init__(self, category: str, reason: str, *,
                 detail: Mapping[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.detail = dict(detail or {})

    def as_dict(self) -> dict[str, Any]:
        return {"category": self.category, "reason": self.reason,
                "detail": copy.deepcopy(self.detail)}


# ── 1. Write ordering and dependencies ───────────────────────────────
#
# The dependencies the ENGINE declares between two operations that can
# appear in one request. Each entry is (FIRST, SECOND): the first
# operation is written before the second, whatever order the request
# listed them in.
#
# These are SEMANTIC dependencies, not a preference: a ripple closes
# the gap a delete left, a join restores a split, and a resize
# preserves the position the overlay was given.

ENGINE_DEPENDENCIES: tuple[tuple[str, str], ...] = (
    ("clip.delete", "clip.ripple"),
    ("clip.split", "clip.join"),
    ("overlay.position", "overlay.resize"),
)

#: The dependency kinds a plan can carry. ``engine`` is the table
#: above; ``declared`` is the requester's own ``depends_on``;
#: ``created_target`` is an operation that acts on a clip another
#: operation in the same request creates.
DEPENDENCY_KINDS = ("engine", "declared", "created_target")


@dataclass(frozen=True)
class WritePlan:
    """The order the operations are written in, and why."""

    order: tuple[int, ...]
    operations: tuple[Mapping[str, Any], ...]
    dependencies: tuple[Mapping[str, Any], ...] = ()
    reordered: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"order": list(self.order),
                "operations": [dict(operation)
                               for operation in self.operations],
                "dependencies": [dict(row) for row in self.dependencies],
                "reordered": self.reordered}


def _op_id(operation: Mapping[str, Any]) -> str:
    return str(operation.get("op") or "")


def _target_id(operation: Mapping[str, Any]) -> str:
    for key in ("unique_id", "client_unique_id"):
        value = operation.get(key)
        if value:
            return str(value)
    return ""


def plan_write_order(operations: Sequence[Mapping[str, Any]], *,
                     depends_on: Sequence[Sequence[int]] | None = None
                     ) -> WritePlan:
    """The order the operations are written in, and the dependencies.

    `depends_on` is parallel to `operations` and carries the positions
    (within `operations`) each operation declared it depends on. The
    request's own dependency order was already applied when the request
    was compiled; this function sees the operations in the order they
    will be written and adds the dependencies the ENGINE declares.

    A request whose declared order contradicts a declared dependency is
    REFUSED: the requester asked for an order the engine knows cannot
    hold, and executing it anyway would be the engine choosing for
    them.
    """
    ops = [dict(operation) for operation in (operations or ())]
    count = len(ops)
    declared: list[set[int]] = [set() for _ in range(count)]
    for index, dependencies in enumerate(depends_on or ()):
        for dependency in (dependencies or ()):
            position = int(dependency)
            if position < 0 or position >= count:
                raise CompoundRefused(
                    "unsupported",
                    (f"operation {index} depends on operation {position}, "
                     f"which is not in the request"),
                    detail={"operation_index": index,
                            "depends_on": position})
            if position == index:
                raise CompoundRefused(
                    "unsupported",
                    f"operation {index} depends on itself",
                    detail={"operation_index": index})
            declared[index].add(position)

    edges: dict[int, set[int]] = {index: set() for index in range(count)}
    kinds: dict[tuple[int, int], str] = {}

    def add_edge(first: int, second: int, kind: str) -> None:
        if first == second:
            raise CompoundRefused(
                "unsupported",
                f"operation {second} depends on itself",
                detail={"operation_index": second})
        if second in edges[first]:
            return
        edges[first].add(second)
        kinds[(first, second)] = kind

    for index in range(count):
        for dependency in sorted(declared[index]):
            add_edge(dependency, index, "declared")

    # The engine's own dependencies, in the order the table declares
    # them. Both operations must be present: a ripple with no delete in
    # the request is a shift of its own, not an unsatisfied dependency.
    for first_id, second_id in ENGINE_DEPENDENCIES:
        firsts = [index for index in range(count)
                  if _op_id(ops[index]) == first_id]
        seconds = [index for index in range(count)
                   if _op_id(ops[index]) == second_id]
        if not firsts or not seconds:
            continue
        for first in firsts:
            for second in seconds:
                add_edge(first, second, "engine")

    # An operation that acts on a clip ANOTHER operation in this
    # request creates is written after the creator: the target does not
    # exist until the creator has made it.
    creators: dict[str, int] = {}
    for index, operation in enumerate(ops):
        client_id = str(operation.get("client_unique_id") or "")
        if client_id and _op_id(operation) == "clip.insert":
            creators[client_id] = index
    for index, operation in enumerate(ops):
        creator = creators.get(_target_id(operation))
        if creator is not None and creator != index:
            add_edge(creator, index, "created_target")

    # A contradiction is a request that says one thing and means
    # another: the requester declared an order the engine's own
    # dependency forbids.
    for (first, second), kind in list(kinds.items()):
        if kind != "engine":
            continue
        if second in edges.get(first, set()) and first in edges[second]:
            raise CompoundRefused(
                "unsupported",
                (f"the request asks for {_op_id(ops[second])} to be written "
                 f"before {_op_id(ops[first])}, which the engine's declared "
                 f"dependency forbids: {_op_id(ops[first])} must be written "
                 f"first"),
                detail={"before": _op_id(ops[first]),
                        "after": _op_id(ops[second]),
                        "dependency": "engine"})

    order = _topological_order(edges, count)
    dependencies = tuple(
        {"before": _op_id(ops[first]), "after": _op_id(ops[second]),
         "before_index": first, "after_index": second,
         "kind": kinds[(first, second)]}
        for first, second in sorted(kinds))
    return WritePlan(order=tuple(order), operations=tuple(ops),
                      dependencies=dependencies,
                      reordered=tuple(order) != tuple(range(count)))


def _topological_order(edges: Mapping[int, set[int]], count: int) -> list[int]:
    """A stable topological order: the request's order among ready ops.

    `edges` maps an operation to the operations that come AFTER it, so
    the operations that must precede one are read off the other end.
    """
    predecessors: dict[int, set[int]] = {index: set() for index in range(count)}
    for first, seconds in edges.items():
        for second in seconds:
            predecessors.setdefault(second, set()).add(first)
    done: list[int] = []
    remaining = list(range(count))
    while remaining:
        progressed = False
        for index in list(remaining):
            if all(dependency in done
                   for dependency in predecessors.get(index, ())):
                done.append(index)
                remaining.remove(index)
                progressed = True
        if not progressed:
            raise CompoundRefused(
                "unsupported",
                ("the declared dependencies do not resolve: they form a "
                 "cycle over operations "
                 + ", ".join(str(index) for index in remaining)),
                detail={"unresolved": list(remaining)})
    return done


# ── 2. Preservation feasibility, decided BEFORE the first write ──────


def _clips_by_id(snapshot: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    clips: dict[str, Mapping[str, Any]] = {}
    for track in (snapshot.get("tracks") or ()):
        for clip in (track.get("clips") or ()):
            unique_id = str(clip.get("unique_id") or "")
            if unique_id:
                clips[unique_id] = {**clip, "track": track}
    return clips


def _operation_targets(operations: Sequence[Mapping[str, Any]]
                       ) -> dict[str, list[int]]:
    """The unique ids the operations name, and the operation indexes."""
    targets: dict[str, list[int]] = {}
    for index, operation in enumerate(operations or ()):
        for key in ("unique_id", "client_unique_id"):
            value = operation.get(key)
            if value:
                targets.setdefault(str(value), []).append(index)
    return targets


def preservation_feasibility(*, operations: Sequence[Mapping[str, Any]],
                             before: Mapping[str, Any],
                             requirements: Sequence[Mapping[str, Any]],
                             affected_spans: Sequence[Sequence[int]] = ()
                             ) -> dict[str, Any]:
    """Whether the planned writes can satisfy the named requirements.

    Each named requirement (``preservation_invariant``'s vocabulary) is
    read against the plan, not against a read-back:

    - ``clip_unchanged`` is INFEASIBLE when the request writes to that
      clip, and UNPROVABLE when the clip is not on the timeline the
      edit was authorized against;
    - ``row_unchanged`` is INFEASIBLE when the request writes to that
      row;
    - ``marker_preserved`` is INFEASIBLE when the frame lies inside a
      span the request touches.

    A requirement that cannot be judged is UNPROVABLE, never a pass -
    the same rule the read-back judgement follows.
    """
    clips = _clips_by_id(before or {})
    targets = _operation_targets(operations or ())
    results: list[dict[str, Any]] = []
    for requirement in (requirements or ()):
        name = str(requirement.get("kind") or requirement.get("name") or "")
        if name == "clip_unchanged":
            unique_id = str(requirement.get("unique_id") or "")
            if unique_id in targets:
                results.append({
                    "requirement": name, "status": "infeasible",
                    "detail": (f"the request writes to clip {unique_id!r} and "
                               f"also requires it unchanged, at operation "
                               f"{targets[unique_id][0]}")})
            elif unique_id not in clips:
                results.append({
                    "requirement": name, "status": "unprovable",
                    "detail": (f"clip {unique_id!r} is not on the timeline "
                               "the edit was authorized against")})
            else:
                results.append({"requirement": name, "status": "satisfiable",
                                "detail": ""})
        elif name == "row_unchanged":
            track_type = str(requirement.get("track_type") or "").lower()
            track_index = requirement.get("track_index")
            writers = []
            for index, operation in enumerate(operations or ()):
                if _writes_on_row(operation, clips, track_type, track_index):
                    writers.append(index)
            if writers:
                results.append({
                    "requirement": name, "status": "infeasible",
                    "detail": (f"the request writes to row "
                               f"{track_type}{track_index} at operation "
                               f"{writers[0]} and also requires that row "
                               "unchanged")})
            else:
                results.append({"requirement": name, "status": "satisfiable",
                                "detail": ""})
        elif name == "marker_preserved":
            frame = int(requirement.get("frame"))
            touched = _frame_is_touched(operations or (), clips,
                                        affected_spans, frame)
            if touched:
                results.append({
                    "requirement": name, "status": "infeasible",
                    "detail": (f"the request touches the range the marker at "
                               f"frame {frame} sits in")})
            else:
                results.append({"requirement": name, "status": "satisfiable",
                                "detail": ""})
        else:
            results.append({"requirement": name, "status": "unprovable",
                            "detail": f"unknown requirement {name!r}"})
    violations = [row for row in results if row["status"] == "infeasible"]
    return {
        "status": "infeasible" if violations else "feasible",
        "requirements": results,
        "violations": violations,
    }


def _writes_on_row(operation: Mapping[str, Any],
                   clips: Mapping[str, Mapping[str, Any]],
                   track_type: str, track_index: Any) -> bool:
    """Whether this operation writes to the named row."""
    target = _target_id(operation)
    clip = clips.get(target)
    if clip is not None:
        track = clip.get("track") or {}
        if (str(track.get("type") or "").lower() == track_type
                and int(track.get("index") or 0) == int(track_index or 0)):
            return True
    op_type = str(operation.get("track_type") or "").lower()
    op_index = operation.get("track_index")
    if op_type and op_index is not None:
        return (op_type == track_type
                and int(op_index) == int(track_index or 0))
    return False


def _frame_is_touched(operations: Sequence[Mapping[str, Any]],
                      clips: Mapping[str, Mapping[str, Any]],
                      affected_spans: Sequence[Sequence[int]],
                      frame: int) -> bool:
    """Whether any operation touches the range the frame sits in."""
    for start, end in (affected_spans or ()):
        if int(start) <= frame < int(end):
            return True
    for operation in operations:
        clip = clips.get(_target_id(operation))
        if clip is None:
            continue
        start = int(clip.get("record_in") or 0)
        end = int(clip.get("record_out") or 0)
        if start <= frame < end:
            return True
    return False


#: The durable stage a compound transaction ends on.
TERMINAL_STAGES = ("committed", "recovered", "abandoned", "recovery_required")


def terminal_stage(*, write_attempted: bool,
                   restoration: Mapping[str, Any] | None = None) -> str:
    """The durable stage a compound transaction ends on.

    - a transaction that never wrote is ABANDONED: it owes no recovery,
      and it must not leave a hold on the timeline it never touched;
    - a transaction that wrote and was proven restored is RECOVERED;
    - a transaction that wrote and was NOT proven restored keeps its
      hold, because that recovery is real.
    """
    if not write_attempted:
        return "abandoned"
    if str((restoration or {}).get("status") or "") == "restored":
        return "recovered"
    return "recovery_required"


# ── 3. The validated generation, and the proof of a restoration ──────


def validated_generation(project: str, timeline: str, *,
                         store: Any = None) -> dict[str, Any] | None:
    """The newest generation of this timeline that a verified write left.

    A generation is VALIDATED when its recorded receipt proves the write
    was committed: the receipt says ``committed``, and the generation
    carries a recorded snapshot to compare against. An observed
    generation (a read, not a write) is not validated - it proves what
    the timeline held, not that a write landed.
    """
    from library.tools import timeline_shadow

    store = store or timeline_shadow.ShadowStore()
    try:
        timeline_id = store.resolve_timeline(project, timeline)
    except Exception:  # noqa: BLE001 - an unread timeline has no generation
        return None
    try:
        history = store.history(project, timeline_id)
    except Exception:  # noqa: BLE001 - an unread store has no generation
        return None
    for generation in reversed(history):
        receipt = generation.receipt or {}
        status = str(receipt.get("resolve_write_status")
                     or receipt.get("status") or "")
        committed = str((receipt.get("committed") or {}).get("status")
                        or "")
        if status != "committed" and committed != "committed":
            continue
        return {"project": generation.project,
                "timeline": generation.timeline_name,
                "timeline_id": generation.timeline_id,
                "generation": generation.generation,
                "hash": generation.hash,
                "source": generation.source,
                "receipt_status": status or committed}
    return None


def _changed_rows(block: Any) -> list[str]:
    """The rows two snapshots disagree about, by the differ's own shape.

    ``timeline_shadow.diff`` answers with the differ's row block: the
    rows it compared, the ones that changed and the ones that were
    re-rendered. A row that reads "unchanged" is not a difference.
    """
    changed: list[str] = []
    if not isinstance(block, Mapping):
        return changed
    entries = block.get("rows")
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            if str(entry.get("state") or "") != "unchanged":
                changed.append(str(entry.get("key") or entry.get("name") or ""))
    for key in ("changed", "rerendered"):
        for entry in (block.get(key) or ()):
            if isinstance(entry, Mapping):
                changed.append(str(entry.get("key") or entry.get("name") or ""))
            else:
                changed.append(str(entry))
    return sorted({name for name in changed if name})


def compare_snapshots(recorded: Mapping[str, Any],
                     observed: Mapping[str, Any]) -> dict[str, Any]:
    """Whether two snapshots hold the same timeline.

    The comparison is the one the differ and the replace guard use
    (``timeline_shadow.diff``): the rows each snapshot holds, and the
    markers. An empty observation proves nothing and is UNPROVABLE,
    never a pass.
    """
    from library.tools import timeline_shadow

    if not observed:
        return {"status": "unprovable",
                "reason": "no live read-back was supplied",
                "differences": {"rows": [], "markers": []}}
    difference = timeline_shadow.diff(dict(recorded), dict(observed))
    changed = _changed_rows(difference.get("rows") or {})
    markers = ((difference.get("markers_added") or [])
               + (difference.get("markers_removed") or []))
    if changed or markers:
        return {"status": "differs",
                "reason": "the live timeline does not read as the "
                          "validated generation",
                "differences": {"rows": changed, "markers": markers}}
    return {"status": "restored",
            "reason": "the live timeline reads as the validated generation",
            "differences": {"rows": [], "markers": []}}


def verify_restored(*, project: str, timeline: str, generation: int,
                    live: Mapping[str, Any] | None = None,
                    store: Any = None) -> dict[str, Any]:
    """Whether the live timeline is back at the validated generation.

    The comparison is against the recorded snapshot of that generation:
    the state the request was authorized against. A restoration that
    cannot be proven is reported as unprovable, never as a pass.
    """
    from library.tools import timeline_shadow

    store = store or timeline_shadow.ShadowStore()
    try:
        timeline_id = store.resolve_timeline(project, timeline)
        recorded = store.snapshot(store.get(project, timeline_id, generation))
    except Exception as exc:  # noqa: BLE001 - an unread generation is unprovable
        return {"status": "unprovable", "generation": generation,
                "reason": f"the recorded generation could not be read: {exc}",
                "differences": {}}
    observed = dict(live) if live is not None else {}
    verdict = compare_snapshots(recorded, observed)
    verdict["generation"] = generation
    return verdict


# ── 4. An aborted pre-write transaction owes nothing ────────────────


def aborted_pre_write_hold(project_folder: str, transaction_id: str) -> bool:
    """Whether this transaction still HOLDS the timeline it never wrote.

    A transaction that never attempted a write owes no recovery, so it
    must not read as an outstanding one. The journal proves the attempt
    (``transaction_receipt.WRITE_ATTEMPT_STAGES``); this asks the same
    question the AXI-048 gate asks, for one transaction.
    """
    from library.tools import event_log, transaction_receipt

    if not project_folder:
        return False
    try:
        events = event_log.read_events(project_folder)
    except OSError:
        return False
    rows = transaction_receipt.interrupted(events)
    row = next((entry for entry in rows
                if str(entry.get("transaction_id")) == str(transaction_id)),
               None)
    if row is None:
        return False
    return bool(row.get("write_attempted"))


# ── 5. A possibly committed mutation is never retried silently ───────


def prior_outcome_refusal(project_folder: str, transaction_id: str, *,
                          operations: Sequence[Mapping[str, Any]] = ()
                          ) -> dict[str, Any] | None:
    """The refusal a prior outcome of THIS transaction demands.

    ``None`` when the transaction was never submitted, or when its prior
    outcome was a clean commit that the caller may read back. A
    refusal descriptor when the journal shows the transaction may have
    written: the same call is refused, because repeating it could
    apply a mutation twice.
    """
    from library.tools import axi_transaction

    if not project_folder or not transaction_id:
        return None
    outcome = axi_transaction.prior_outcome(
        project_folder, transaction_id, operations=operations)
    if outcome is None:
        return None
    status = str(outcome.get("status") or "")
    if status == "unresolved":
        return {
            "category": "recovery_required",
            "reason": (f"transaction {transaction_id!r} was submitted before "
                       "and its outcome is not recorded as committed; it may "
                       "have written, so it is not repeated silently - "
                       "recover or abandon it first"),
            "mutation_state": "unknown", "mutated": True,
            "evidence": {"mutation_state": "unknown", "mutated": True,
                         "prior_outcome": outcome},
        }
    if status == "conflict":
        return {
            "category": "conflict",
            "reason": (f"transaction id {transaction_id!r} was already used "
                       "for different operations"),
            "mutation_state": "none", "mutated": False,
            "evidence": {"mutation_state": "none", "mutated": False,
                         "prior_outcome": outcome},
        }
    if status == "committed":
        return {
            "category": "conflict",
            "reason": (f"transaction {transaction_id!r} already committed; "
                       "its prior outcome is recorded, so the same work is "
                       "not written again"),
            "mutation_state": "none", "mutated": False,
            "evidence": {"mutation_state": "none", "mutated": False,
                         "prior_outcome": outcome},
        }
    return None


# ── The one pre-write gate ───────────────────────────────────────────


def preflight(*, project_folder: str, transaction_id: str,
              operations: Sequence[Mapping[str, Any]],
              before: Mapping[str, Any],
              requirements: Sequence[Mapping[str, Any]] = (),
              affected_spans: Sequence[Sequence[int]] = (),
              depends_on: Sequence[Sequence[int]] | None = None
              ) -> dict[str, Any]:
    """Every check a compound request passes BEFORE its first write.

    Returns ``{"status": "ok", ...}`` when the request may proceed, or
    a refusal descriptor naming the category, the reason and the
    evidence. Nothing here writes, and nothing here contacts Resolve.
    """
    plan: WritePlan | None = None
    try:
        plan = plan_write_order(operations, depends_on=depends_on)
    except CompoundRefused as refused:
        return {"status": "refused", **refused.as_dict(),
                "mutation_state": "none", "mutated": False}

    feasibility = preservation_feasibility(
        operations=operations, before=before, requirements=requirements,
        affected_spans=affected_spans)
    if feasibility["status"] == "infeasible":
        names = ", ".join(str(row["requirement"]) for row in
                          feasibility["violations"])
        detail = "; ".join(str(row["detail"]) for row in
                           feasibility["violations"])
        return {
            "status": "refused", "category": "conflict",
            "reason": (f"the request's preservation requirements cannot be "
                       f"satisfied by the writes it asks for ({names}): "
                       f"{detail}; nothing was written"),
            "detail": {"preservation": feasibility},
            "mutation_state": "none", "mutated": False,
        }

    refusal = prior_outcome_refusal(
        project_folder, transaction_id, operations=operations)
    if refusal is not None:
        return {"status": "refused", **refusal}

    return {"status": "ok",
            "write_order": plan.as_dict(),
            "preservation": feasibility}
