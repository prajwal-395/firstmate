"""One classification for the ways an edit fails to land, and one
decision for what to do about it.

A Ren edit is a PLAN against a revision. Between the plan and the write
the world can move, the write can fail, and the write can fail in a way
nobody can observe. Those are FOUR different problems with FOUR
different next steps, and they arrive today as four unrelated
vocabularies:

    a stale plan            `edit_patch.StalePatch`, `patch_algebra.CONFLICT`
    a concurrent edit       a shadow `observed` generation, `reel_edit_history`
    a failed write          `edit_patch.PatchRefused`, `transaction_receipt`
    an uncertain write      `mutation_state == "unknown"`, an interrupted run

A host that cannot tell them apart retries the wrong way: it re-plans a
write that may already have landed, or it re-applies a change whose
target has moved. This module owns the CLASSIFICATION and the DECISION
that follows it, and nothing else:

- `classify()` names the class, the reason, the action the host may
  take and the recovery class - one answer for every path;
- `target_changed()` and `renewal_required()` answer PRES-020: has the
  operation's INTENDED TARGET changed, so that the operation now means
  something else and needs renewed planning rather than a rebase;
- `partition_for_rebase()` answers PRES-019: which of a stale patch's
  operations are semantically safe to carry onto the head, and which
  must be re-planned. The existing `patch_algebra.carry` is
  ALL-OR-NOTHING - one conflicting operation refuses the whole patch -
  and a compound edit is exactly where that hurts.

The action vocabulary is not a fifth one: every action this module
names is a value `transaction_receipt.RECOVERY_ACTIONS` already
prescribes, so a receipt and a refusal say the same thing.

The module is deliberately offline. It reads recorded snapshots and
never touches Resolve; deciding what to do is a separate act from
doing it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from library.tools import transaction_receipt

# ── The four classes (PRES-018) ───────────────────────────────────────

STALE_PLAN = "stale_plan"
CONCURRENT_MODIFICATION = "concurrent_modification"
EXECUTION_FAILURE = "execution_failure"
UNCERTAIN_MUTATION = "uncertain_mutation"

#: Every class this module can name, most urgent first. A host that
#: reads only the first name still gets the most urgent truth.
CONFLICT_CLASSES = (
    UNCERTAIN_MUTATION,
    EXECUTION_FAILURE,
    CONCURRENT_MODIFICATION,
    STALE_PLAN,
)

#: The class a healthy edit carries.
NO_CONFLICT = "none"

DEFINITIONS = {
    STALE_PLAN: (
        "the plan was made against an older revision and nothing else "
        "has happened since"),
    CONCURRENT_MODIFICATION: (
        "the timeline changed in a way the plan did not account for and "
        "nobody recorded what it meant"),
    EXECUTION_FAILURE: (
        "the write was attempted and failed, and what it left behind is "
        "known"),
    UNCERTAIN_MUTATION: (
        "the write was attempted and nobody can say whether it landed, so "
        "the observed state must be read before anything else"),
}

#: The action each class permits. Every value is a recovery action the
#: shared receipt vocabulary already prescribes, so a refusal and a
#: receipt name the same next step.
ACTIONS = {
    STALE_PLAN: transaction_receipt.RECOVERY_ACTIONS["stale_generation"],
    CONCURRENT_MODIFICATION: transaction_receipt.RECOVERY_ACTIONS[
        "target_not_found"],
    EXECUTION_FAILURE: transaction_receipt.RECOVERY_ACTIONS[
        "execution_failed"],
    UNCERTAIN_MUTATION: transaction_receipt.RECOVERY_ACTIONS["api_uncertain"],
}

#: How the host may treat the class. `auto_recoverable` - the host may
#: perform only the named safe recovery; `needs_question` - the smallest
#: question that unblocks; `blocked` - something must be resolved first.
RECOVERY_CLASSES = {
    STALE_PLAN: "auto_recoverable",
    CONCURRENT_MODIFICATION: "needs_question",
    EXECUTION_FAILURE: "auto_recoverable",
    UNCERTAIN_MUTATION: "auto_recoverable",
}

#: The execution outcomes that are a failure rather than a success.
EXECUTION_FAILURES = frozenset({
    "failed", "verification_failed", "stopped", "error",
})


@dataclass(frozen=True)
class ConflictAssessment:
    """What kind of badness this is, and what may be done about it."""

    conflict_class: str
    reason: str
    action: str
    recovery_class: str
    evidence: Mapping = field(default_factory=dict)

    @property
    def is_conflict(self) -> bool:
        return self.conflict_class != NO_CONFLICT

    def as_dict(self) -> dict:
        return {
            "conflict_class": self.conflict_class,
            "reason": self.reason,
            "action": self.action,
            "recovery_class": self.recovery_class,
            "evidence": dict(self.evidence),
        }


def _assessment(conflict_class: str, reason: str,
                evidence: Mapping) -> ConflictAssessment:
    return ConflictAssessment(
        conflict_class=conflict_class,
        reason=reason,
        action=ACTIONS.get(conflict_class, ""),
        recovery_class=RECOVERY_CLASSES.get(conflict_class, ""),
        evidence=dict(evidence))


def classify(*, execution_status: str | None = None,
             mutation_state: str | None = None,
             interrupted_transaction: bool = False,
             observed_generation: bool = False,
             manual_edit_recorded: bool = False,
             planned_base_generation: int | None = None,
             head_generation: int | None = None,
             expected_model_revision: int | None = None,
             current_model_revision: int | None = None) -> ConflictAssessment:
    """Name the class of the badness, once, for every path.

    The inputs are the FACTS each path already knows, not a verdict it
    already reached: whether the write was attempted, whether its
    outcome is observable, whether an unattributed change was recorded,
    and whether the revision the plan was made against has moved.

    The order is the order of urgency. An uncertain write makes every
    other question unanswerable until the observed state is read, so it
    wins; a failed write is next, because what landed must be reversed;
    an unattributed change is next, because the plan's target may have
    moved; a moved revision is last, because it is the one case with a
    safe mechanical answer.
    """
    evidence = {
        "execution_status": execution_status,
        "mutation_state": mutation_state,
        "interrupted_transaction": bool(interrupted_transaction),
        "observed_generation": bool(observed_generation),
        "manual_edit_recorded": bool(manual_edit_recorded),
        "planned_base_generation": planned_base_generation,
        "head_generation": head_generation,
        "expected_model_revision": expected_model_revision,
        "current_model_revision": current_model_revision,
    }
    if mutation_state == "unknown" or interrupted_transaction:
        return _assessment(
            UNCERTAIN_MUTATION,
            ("the write may have landed and its outcome is not "
             "observable; the observed state must be read before any "
             "retry"),
            evidence)
    if execution_status in EXECUTION_FAILURES:
        return _assessment(
            EXECUTION_FAILURE,
            (f"the write was attempted and failed ({execution_status}); "
             "what it left behind is known and must be reversed"),
            evidence)
    if observed_generation or manual_edit_recorded:
        return _assessment(
            CONCURRENT_MODIFICATION,
            ("the timeline changed in a way nobody recorded, so the "
             "plan's target may no longer mean what it meant"),
            evidence)
    if (isinstance(planned_base_generation, int)
            and isinstance(head_generation, int)
            and head_generation != planned_base_generation):
        return _assessment(
            STALE_PLAN,
            (f"the plan was made against revision {planned_base_generation} "
             f"and the head is {head_generation}"),
            evidence)
    if (isinstance(expected_model_revision, int)
            and isinstance(current_model_revision, int)
            and current_model_revision != expected_model_revision):
        return _assessment(
            STALE_PLAN,
            (f"the plan was made against Project Model revision "
             f"{expected_model_revision} and the head is "
             f"{current_model_revision}"),
            evidence)
    return _assessment(
        NO_CONFLICT,
        "the revision the plan was made against is still the head, and "
        "no write was attempted",
        evidence)


# ── The intended target (PRES-020) ────────────────────────────────────

#: The op fields that name a range INSIDE the media rather than a
#: place on the timeline. An operation that names one of these addresses
#: the material, so a change to the material's range is a change of
#: target; an operation that names none addresses a clip, and the
#: clip's range may move without changing what the operation means.
_SOURCE_ADDRESS_FIELDS = frozenset({
    "in_frame", "out_frame", "source_in_frame", "source_out_frame",
    "source_file",
})


def _clip_rows(snapshot: Mapping) -> dict:
    """Every clip of a snapshot, by its unique id."""
    rows: dict = {}
    for track in (snapshot.get("tracks") or ()):
        for clip in (track.get("clips") or ()):
            unique_id = str(clip.get("unique_id") or "")
            if not unique_id:
                continue
            rows[unique_id] = {
                **clip,
                "track_type": track.get("type", clip.get("track_type")),
                "track_index": track.get("index", clip.get("track_index")),
            }
    return rows


def _addresses_source(operation: Mapping) -> bool:
    """Whether the operation names a range inside the media."""
    return bool(_SOURCE_ADDRESS_FIELDS.intersection(operation))


def target_identity(snapshot: Mapping, unique_id: str) -> dict | None:
    """What the clip at `unique_id` holds, as a comparable identity.

    The identity is the MATERIAL and the PLACE, not the frame range: a
    ripple moves a clip in time without changing what it is, and a
    rebase is allowed to follow that move. A replace changes the
    material, and a move changes the place; either changes what an
    operation aimed at that clip now means.
    """
    clip = _clip_rows(snapshot).get(str(unique_id))
    if clip is None:
        return None
    return {
        "unique_id": str(unique_id),
        "source_file": clip.get("source_file"),
        "media_pool_item_id": clip.get("media_pool_item_id"),
        "track_type": clip.get("track_type"),
        "track_index": clip.get("track_index"),
        "source_in_frame": clip.get("source_in_frame"),
        "source_out_frame": clip.get("source_out_frame"),
    }


def target_changed(operation: Mapping, base_snapshot: Mapping,
                   head_snapshot: Mapping) -> dict:
    """Whether the operation's intended target is now a different thing.

    `{"changed": bool, "reason": str, "unique_id": str}`. An operation
    that addresses no clip (a timeline marker at a frame, a track add)
    has no target to change and is reported as unchanged - whether such
    an operation still means what it meant is the frame algebra's
    question, not this one's.
    """
    unique_id = str(operation.get("unique_id")
                    or operation.get("client_unique_id") or "")
    if not unique_id:
        return {"changed": False, "reason": "",
                "unique_id": "",
                "detail": "the operation addresses no clip"}
    before = target_identity(base_snapshot, unique_id)
    after = target_identity(head_snapshot, unique_id)
    if before is None:
        return {
            "changed": True, "unique_id": unique_id,
            "reason": (f"the operation addresses clip {unique_id!r}, which "
                       "the revision it was planned against does not hold"),
        }
    if after is None:
        return {
            "changed": True, "unique_id": unique_id,
            "reason": (f"the operation addresses clip {unique_id!r}, which "
                       "no longer exists on the head"),
        }
    if before.get("source_file") != after.get("source_file"):
        return {
            "changed": True, "unique_id": unique_id,
            "reason": (f"clip {unique_id!r} now holds "
                       f"{after.get('source_file')!r} instead of "
                       f"{before.get('source_file')!r}"),
        }
    if (before.get("media_pool_item_id") != after.get("media_pool_item_id")
            and before.get("media_pool_item_id") is not None
            and after.get("media_pool_item_id") is not None):
        return {
            "changed": True, "unique_id": unique_id,
            "reason": (f"clip {unique_id!r} now plays media pool item "
                       f"{after.get('media_pool_item_id')!r} instead of "
                       f"{before.get('media_pool_item_id')!r}"),
        }
    if (before.get("track_type") != after.get("track_type")
            or before.get("track_index") != after.get("track_index")):
        return {
            "changed": True, "unique_id": unique_id,
            "reason": (f"clip {unique_id!r} moved from track "
                       f"{before.get('track_type')!r}"
                       f"{before.get('track_index')!r} to "
                       f"{after.get('track_type')!r}"
                       f"{after.get('track_index')!r}"),
        }
    if (_addresses_source(operation)
            and (before.get("source_in_frame")
                 != after.get("source_in_frame")
                 or before.get("source_out_frame")
                 != after.get("source_out_frame"))):
        return {
            "changed": True, "unique_id": unique_id,
            "reason": (f"the material of clip {unique_id!r} was re-trimmed "
                       f"from {before.get('source_in_frame')}"
                       f"-{before.get('source_out_frame')} to "
                       f"{after.get('source_in_frame')}"
                       f"-{after.get('source_out_frame')}"),
        }
    return {"changed": False, "reason": "", "unique_id": unique_id,
            "detail": "the clip still holds the same material in the same "
                      "place"}


def renewal_required(operation: Mapping, base_snapshot: Mapping,
                     head_snapshot: Mapping) -> dict:
    """Whether this operation must be re-planned rather than carried.

    `{"renewal_required": bool, "reason": str, "question": str}`. A
    changed target means the operation now means something else, so it
    needs renewed planning - or the smallest question to the person who
    owns the timeline - and must never be carried silently.
    """
    change = target_changed(operation, base_snapshot, head_snapshot)
    if not change["changed"]:
        return {"renewal_required": False, "reason": "",
                "question": "", "target": change}
    reason = change["reason"]
    return {
        "renewal_required": True,
        "reason": reason,
        "question": ("This timeline changed under the edit I had "
                     "prepared, so the change I planned would now apply "
                     "to something else. Keep your edit and tell me "
                     "whether to apply my change to the updated cut."),
        "target": change,
    }


# ── Which operations may be carried (PRES-019) ────────────────────────

REBASE_ALL = "rebase_all"
REBASE_PART = "rebase_part"
REPLAN_ALL = "replan_all"
CURRENT = "current"


def _single_operation_patch(patch, operation: Mapping, index: int,
                            snapshot: Mapping) -> dict:
    """A patch carrying one operation, for the composition algebra."""
    from library.tools import edit_algebra

    spans = edit_algebra.spans([operation], snapshot)
    span = spans[0] if spans else None
    return {
        "id": f"{patch.id}#{index}",
        "project": patch.project,
        "timeline": patch.timeline,
        "base_generation": int(patch.base_generation),
        "capability": patch.capability,
        "affected_spans": [list(span)] if span else [],
        "conflict_domains": list(patch.conflict_domains),
        "operations": [dict(operation)],
    }


def _frame_addressed(operation: Mapping) -> bool:
    """Whether the operation addresses a frame rather than a clip.

    A frame does not move with a cut, so an operation that names a frame
    cannot follow a change that shifted the timeline before it.
    """
    from library.tools import edit_patch

    spec = edit_patch.OPERATIONS.get(operation.get("op"))
    if spec is None:
        return True
    return not spec.item


def _earliest_shift(before: Mapping, after: Mapping) -> int | None:
    """The earliest frame whose occupancy changed between two snapshots.

    A clip that moved, appeared or vanished shifts everything after it.
    """
    old = _clip_rows(before)
    new = _clip_rows(after)
    frames = []
    for unique_id in old.keys() | new.keys():
        a, b = old.get(unique_id), new.get(unique_id)
        if a is None or b is None:
            for clip in (a, b):
                if clip is not None:
                    frames.append(int(clip.get("record_in") or 0))
            continue
        if (a.get("record_in") != b.get("record_in")
                or a.get("record_out") != b.get("record_out")):
            frames.append(int(b.get("record_in") or 0))
    return min(frames) if frames else None


def _operation_is_safe(operation: Mapping, patch, index: int,
                       base_snapshot: Mapping,
                       generations: Sequence[tuple]) -> dict:
    """Whether one operation may be carried onto the head, and why.

    `generations` is the recorded history after the patch's base, oldest
    first, as `(generation, snapshot)` pairs.
    """
    from library.tools import patch_algebra

    head_snapshot = generations[-1][1] if generations else base_snapshot
    renewal = renewal_required(operation, base_snapshot, head_snapshot)
    if renewal["renewal_required"]:
        return {"safe": False, "reason": renewal["reason"],
                "kind": "target_changed"}
    for position, (generation, snapshot) in enumerate(generations):
        previous = (base_snapshot if position == 0
                    else generations[position - 1][1])
        if generation.source == "observed":
            shift = _earliest_shift(previous, snapshot)
            if _frame_addressed(operation) and shift is not None:
                span = _single_operation_patch(
                    patch, operation, index, previous)
                frames = [list(s) for s in span["affected_spans"]]
                if frames and frames[0][0] >= shift:
                    return {
                        "safe": False,
                        "reason": (f"generation {generation.generation} is a "
                                   f"change nobody recorded that moved the "
                                   f"timeline from frame {shift}, and this "
                                   f"operation addresses a frame, which "
                                   f"does not move with a cut"),
                        "kind": "unattributed_shift",
                    }
            continue
        if not generation.patch:
            continue
        try:
            verdict = patch_algebra.compose(
                generation.patch,
                _single_operation_patch(patch, operation, index, previous),
                previous)
        except Exception as exc:  # noqa: BLE001 - an unjudgeable pair is unsafe
            return {
                "safe": False,
                "reason": (f"generation {generation.generation} could not be "
                           f"composed with this operation: {exc}"),
                "kind": "uncomposable",
            }
        if verdict.verdict == "conflict":
            return {
                "safe": False,
                "reason": (f"generation {generation.generation} conflicts "
                           f"with this operation: {verdict.reason}"),
                "kind": "conflict",
            }
        if verdict.verdict == "serialize":
            return {
                "safe": False,
                "reason": (f"generation {generation.generation} was meant to "
                           f"land after this operation, so it cannot be "
                           f"carried past it: {verdict.reason}"),
                "kind": "ordered",
            }
    return {"safe": True, "reason": "", "kind": ""}


def partition_for_rebase(patch, store, project: str,
                         timeline_id: str) -> dict:
    """Split a stale patch into the operations that may be carried and
    the operations that must be re-planned.

    `patch_algebra.carry` is all-or-nothing: one operation that meets a
    recorded change refuses the whole patch. A compound edit - trim plus
    caption, remove plus ripple - is exactly where that loses work, and
    the operation that is safe to carry is often untouched by the change
    that broke its neighbour.

    Returns
    -------
    dict
        ``{"base_generation", "head_generation", "verdict", "reason",
        "rebaseable", "needs_replanning", "operations"}`` where
        ``rebaseable`` and ``needs_replanning`` are lists of
        ``{"index", "operation", "reason"}`` and ``verdict`` is one of
        ``current``, ``rebase_all``, ``rebase_part`` or ``replan_all``.
    """
    from library.tools import edit_patch
    from library.tools import timeline_shadow as shadow

    patch = edit_patch.EditPatch.from_dict(patch)
    rows = store.since_with_snapshots(project, timeline_id,
                                      since=patch.base_generation)
    if not rows:
        raise shadow.ShadowError(
            f"no generation {patch.base_generation} of timeline "
            f"{timeline_id!r} in project {project!r}")
    base_snapshot = rows[0][1]
    generations = [(generation, snapshot)
                   for generation, snapshot in rows[1:]]
    head_generation = rows[-1][0].generation
    rebaseable: list = []
    needs_replanning: list = []
    for index, operation in enumerate(patch.operations):
        judgement = _operation_is_safe(operation, patch, index,
                                        base_snapshot, generations)
        if judgement["safe"]:
            rebaseable.append({"index": index, "operation": dict(operation),
                                "reason": ""})
        else:
            needs_replanning.append({"index": index,
                                     "operation": dict(operation),
                                     "reason": judgement["reason"],
                                     "kind": judgement["kind"]})
    if not generations:
        verdict, reason = CURRENT, "the head is the revision the patch was planned against"
    elif not needs_replanning:
        verdict, reason = (REBASE_ALL,
                            ("every operation composes with every generation "
                             "since the plan"))
    elif not rebaseable:
        verdict, reason = (REPLAN_ALL,
                            ("no operation of this patch may be carried onto "
                             "the head"))
    else:
        verdict, reason = (REBASE_PART,
                            (f"{len(rebaseable)} of {len(patch.operations)} "
                             "operations may be carried; the rest need "
                             "renewed planning"))
    return {
        "base_generation": int(patch.base_generation),
        "head_generation": int(head_generation),
        "verdict": verdict,
        "reason": reason,
        "rebaseable": rebaseable,
        "needs_replanning": needs_replanning,
        "operations": [dict(operation) for operation in patch.operations],
    }


def rebase_decision(partition: Mapping) -> dict:
    """The one answer a host acts on, from a partition.

    `{"decision", "action", "reason", "operations"}` where ``decision``
    is ``carry``, ``replan`` or ``proceed``, and ``operations`` are the
    ones a carry would move. A partial partition is NOT a licence to
    apply half a compound edit silently: the caller is told which
    operations need renewed planning so it can ask for it.
    """
    verdict = str(partition.get("verdict") or "")
    rebaseable = list(partition.get("rebaseable") or ())
    needs_replanning = list(partition.get("needs_replanning") or ())
    reason = str(partition.get("reason") or "")
    if verdict == CURRENT:
        return {"decision": "proceed", "action": "",
                "reason": reason, "operations": []}
    if verdict == REPLAN_ALL:
        return {
            "decision": "replan",
            "action": transaction_receipt.RECOVERY_ACTIONS[
                "stale_generation"],
            "reason": reason,
            "operations": [],
        }
    if needs_replanning:
        return {
            "decision": "replan",
            "action": transaction_receipt.RECOVERY_ACTIONS[
                "stale_generation"],
            "reason": reason,
            "operations": [dict(row["operation"])
                           for row in rebaseable],
            "needs_renewed_planning": [
                {"index": row["index"], "reason": row["reason"]}
                for row in needs_replanning],
        }
    return {
        "decision": "carry",
        "action": transaction_receipt.RECOVERY_ACTIONS["stale_generation"],
        "reason": reason,
        "operations": [dict(row["operation"]) for row in rebaseable],
    }
