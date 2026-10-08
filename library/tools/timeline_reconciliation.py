"""Reconcile a Ren proposal with edits observed since its approved base.

The shadow is the source of timeline state. This module is deliberately
offline: it compares two recorded snapshots and returns patch data or a
creator question; it never reads or writes Resolve.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from library.tools import timeline_shadow


class ReconciliationRefused(ValueError):
    """The supplied generations cannot establish a safe comparison."""


def _clips(snapshot: Mapping) -> dict[str, dict]:
    result = {}
    for clip in timeline_shadow.clips(dict(snapshot)):
        uid = clip["unique_id"]
        if uid in result:
            raise ReconciliationRefused(f"duplicate clip identity {uid!r}")
        result[uid] = clip
    return result


def _human_changes(base: Mapping, current: Mapping) -> dict[str, set[str]]:
    """Return changed clip fields, including explicit insertions/deletions."""
    before, after = _clips(base), _clips(current)
    changes: dict[str, set[str]] = {}
    for uid in before.keys() | after.keys():
        if uid not in after:
            changes[uid] = {"deleted"}
            continue
        if uid not in before:
            changes[uid] = {"inserted"}
            continue
        a, b = before[uid], after[uid]
        fields = set()
        for key in ("record_in", "record_out", "source_in_frame",
                    "source_out_frame", "track_index", "track_type",
                    "enabled"):
            if a.get(key) != b.get(key):
                fields.add(key)
        ta, tb = a.get("transform") or {}, b.get("transform") or {}
        fields.update(f"transform.{key}" for key in ta.keys() | tb.keys()
                      if ta.get(key) != tb.get(key))
        if fields:
            changes[uid] = fields
    return changes


def _writes(operation: Mapping) -> tuple[str, set[str]]:
    """Map a patch operation to the timeline fields it intends to write."""
    kind = operation["op"]
    uid = operation.get("unique_id") or operation.get("client_unique_id")
    if kind == "clip.move":
        return uid, {"record_in", "track_index", "track_type"}
    if kind == "clip.trim":
        return uid, {"record_in", "record_out", "source_in_frame",
                     "source_out_frame"}
    if kind == "clip.delete":
        return uid, {"deleted", "record_in", "record_out", "source_in_frame",
                     "source_out_frame", "track_index", "track_type", "enabled"}
    if kind == "clip.set_property":
        return uid, {f"transform.{operation['key']}"}
    # Unknown/global operations are never assumed to commute with an
    # external edit. Their target is the whole timeline.
    return uid, {"*"}


def reconcile(*, approved_snapshot: Mapping, current_snapshot: Mapping,
              approved_generation: int, current_generation: int,
              ren_operations: Sequence[Mapping]) -> dict:
    """Preserve current human state and prepare only safe Ren operations.

    The approved generation must precede the current shadow generation.
    Conflicting operations are returned as a named, uncommitted variant with
    a precise question. Nothing here applies either list of operations.
    """
    if approved_generation < 1 or current_generation < approved_generation:
        raise ReconciliationRefused("current generation must follow the approved generation")
    human = _human_changes(approved_snapshot, current_snapshot)
    safe, conflicting = [], []
    for raw in ren_operations:
        operation = dict(raw)
        uid, writes = _writes(operation)
        changes = human.get(uid, set()) if uid is not None else set()
        collision = bool(
            (uid is None and "*" in writes and human)
            or (changes and ("*" in writes or "*" in changes
                             or "deleted" in changes or "inserted" in changes
                             or writes & changes)))
        (conflicting if collision else safe).append(operation)
    result = {
        "status": "ready" if not conflicting else "needs_creator_choice",
        "base_generation": current_generation,
        "human_changes": {uid: sorted(fields) for uid, fields in sorted(human.items())},
        "patch": {"base_generation": current_generation,
                  "operations": safe} if safe else None,
        "variant": {"name": "Ren proposal", "base_generation": current_generation,
                    "operations": conflicting} if conflicting else None,
        "question": None,
    }
    if conflicting:
        targets = ", ".join(sorted({str(op.get("unique_id", "timeline"))
                                     for op in conflicting}))
        result["question"] = (
            f"Your timeline edit to {targets} is preserved. Should I also apply "
            "the conflicting Ren change as a separate variant?")
    return result


def reconcile_generations(*, store, project: str, timeline_id: str,
                          approved_generation: int,
                          ren_operations: Sequence[Mapping]) -> dict:
    """Read the approved base and latest shadow head, then reconcile them."""
    head = store.head(project, timeline_id)
    if head is None:
        raise ReconciliationRefused("timeline has no recorded shadow generation")
    approved = store.get(project, timeline_id, approved_generation)
    if approved_generation >= head.generation:
        raise ReconciliationRefused("there are no changes after the approved generation")
    generations = store.history(project, timeline_id, after=approved_generation)
    if not any(g.source == timeline_shadow.OBSERVED for g in generations):
        raise ReconciliationRefused(
            "no external edit was observed after the approved generation")
    result = reconcile(
        approved_snapshot=store.snapshot(approved),
        current_snapshot=store.snapshot(head),
        approved_generation=approved_generation,
        current_generation=head.generation,
        ren_operations=ren_operations)
    result["attribution"] = {
        "approved_generation": approved_generation,
        "current_generation": head.generation,
        "external_generations": [g.generation for g in generations
                                  if g.source == timeline_shadow.OBSERVED],
        "agent_generations": [g.generation for g in generations
                              if g.source == timeline_shadow.PATCH],
    }
    return result
