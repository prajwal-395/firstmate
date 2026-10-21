"""The DIRECT route for a qualified small touch-up.

A touch-up that changes nothing but a property on an item it names is
a REVERSIBLE MUTATION.  The edit algebra classes it
`reversible_mutation`, its executor is production-enabled on the
EditPatch backend, its read-back is independent and its inverse is
proven - the four things VC-11 asks for before a write may go direct.
`execution_router` is the one place that decides, and this module asks
it rather than deciding here.

The staged route (duplicate the reel, conform it, compose it, verify
it, rename it over the approved reel, carry the markers, reconcile the
inventory, retire the replaced generation) is what protects a touch-up
against a REAL failure mode: a partial write, a lost row, a wrong
target, an editor's hand edit that the change would destroy.  On a
reel-sized timeline that protection costs whole-reel bookkeeping - the
promotion alone measured 53.6 s of a 58.5 s touch on the Ren-owned
scratch copy (`docs/TOUCH_PATH_LATENCY.md`).

So the rule this module states is narrow and reversible:

* the qualified plan is NOTHING BUT in-place property writes - no row
  added, removed, moved, re-placed or re-composed;
* the router proves the DIRECT mode for those operations against the
  runtime state it measures, under the production qualification policy;
* the approved timeline carries no unattributed editor change, so no
  accepted edit is overwritten by the write;
* the write is an EditPatch: it is read back, it is recorded as a
  generation, and its inverse restores the accepted generation
  (`ren undo`).

Anything else stages.  A staging that protects against nothing is
waste; a staging that protects against a real failure is the point of
the route.

Nothing here invents a qualification: the router's decision and the
recorded qualification rows are the evidence, and a missing row is a
refusal, not a guess.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

#: The touch-up ops that are a property write on an item the plan
#: names.  `set_properties` is `clip.set_property` in the edit algebra;
#: `set_enabled` is `clip.set_enabled`.  Both are asked of the router,
#: which decides on the recorded qualification - this table only says
#: how a touch-up op becomes an algebra operation.
TOUCH_OP_TO_ALGEBRA_OP = {
    "set_properties": "clip.set_property",
    "set_enabled": "clip.set_enabled",
}


class DirectRouteUnavailable(RuntimeError):
    """The direct route cannot serve this request.  Nothing was written.

    The message says WHICH requirement failed, because every one of them
    is a different reason to stage.
    """


def _live_rows(timeline: Any) -> dict[str, list]:
    """The live timeline's rows, by row name.

    The row label is the engine's own (`composed_edit.row_label`), so
    the row a plan names and the row the write finds are the same
    name - never two spellings of one row.
    """
    from library.tools import composed_edit, reel_read

    rows: dict[str, list] = {}
    for row in reel_read.live_items(timeline):
        rows[composed_edit.row_label(row["type"], row["index"])] = list(
            row.get("items") or ())
    return rows


def _target_item(live_rows: Mapping[str, Sequence], entry: Mapping) -> str:
    """The stable identity of the live item the plan names.

    The plan addresses an item by where it sits; the write addresses it
    by its stable identity.  Exactly one item must sit there: zero means
    the plan and the timeline disagree, and two means the frame is not
    an address.
    """
    row = str(entry.get("row") or "").upper()
    frame = entry.get("record_frame")
    items = list(live_rows.get(row) or ())
    hits = []
    for item in items:
        try:
            start = item.GetStart()
        except Exception:  # noqa: BLE001, S112 - an unreadable item is not a hit
            continue
        if start is not None and int(start) == int(frame):
            hits.append(item)
    if len(hits) != 1:
        raise DirectRouteUnavailable(
            f"the approved timeline holds {len(hits)} items at "
            f"{row}@{frame} for this write - the plan and the timeline "
            f"disagree")
    unique_id = ""
    try:
        unique_id = str(hits[0].GetUniqueId() or "")
    except Exception:  # noqa: BLE001
        unique_id = ""
    if not unique_id:
        raise DirectRouteUnavailable(
            f"the item at {row}@{frame} has no stable identity, so the "
            f"write cannot be addressed")
    return unique_id


def plan(qualification: Any, timeline: Any) -> dict:
    """The direct plan for this qualification, or why there is none.

    The plan is the operations the router is asked to route.  It is
    built ONLY from what the qualification already decided - this
    module never re-reads the spec and never decides an edit is small.
    """
    if getattr(qualification, "changes", None):
        raise DirectRouteUnavailable(
            "the change re-places or moves items, which a direct write "
            "does not do")
    if getattr(qualification, "insertions", None):
        raise DirectRouteUnavailable(
            "the change inserts items, which a direct write does not do")
    if getattr(qualification, "removals", None):
        raise DirectRouteUnavailable(
            "the change removes items, which a direct write does not do")
    if getattr(qualification, "moves", None):
        raise DirectRouteUnavailable(
            "the change moves items, which a direct write does not do")
    if getattr(qualification, "new_rows", None):
        raise DirectRouteUnavailable(
            "the change adds a row, which a direct write does not do")
    in_place = list(getattr(qualification, "in_place", None) or ())
    if not in_place:
        raise DirectRouteUnavailable(
            "the change writes no in-place property")
    live_rows = _live_rows(timeline)
    operations: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    for entry in in_place:
        kind = str(entry.get("kind") or "")
        op_id = TOUCH_OP_TO_ALGEBRA_OP.get(kind)
        if op_id is None:
            raise DirectRouteUnavailable(
                f"the change writes {kind!r}, which is not a property "
                f"write")
        unique_id = _target_item(live_rows, entry)
        if kind == "set_enabled":
            operations.append({"op": op_id, "unique_id": unique_id,
                               "enabled": bool(entry.get("enabled"))})
            targets.append({"row": str(entry.get("row") or "").upper(),
                            "record_frame": int(entry.get("record_frame")),
                            "unique_id": unique_id, "kind": kind})
            continue
        properties = dict(entry.get("properties") or {})
        for key, value in sorted(properties.items()):
            operations.append({"op": op_id, "unique_id": unique_id,
                               "key": key, "value": value})
        targets.append({"row": str(entry.get("row") or "").upper(),
                        "record_frame": int(entry.get("record_frame")),
                        "unique_id": unique_id, "kind": kind,
                        "properties": properties})
    if not operations:
        raise DirectRouteUnavailable(
            "the change writes no property value")
    return {"operations": operations, "targets": targets}


def route(operations: Sequence[Mapping], *, resolve: Any, project: Any,
          timeline: Any) -> dict:
    """The router's decision on these operations, with the evidence.

    The runtime state is MEASURED here (`axi_qualification_state`): the
    recorded qualification is conditional on the state the evidence was
    earned under, and an unmeasured state is a refusal, not a pass.

    The measurement reads through the handles the caller already holds -
    the same call the AXI command path makes (`resolve_axi_core_commands
    ._qualification_context`) - so no second scripting client is opened
    and the read happens under the lease the caller holds.
    """
    from library.tools import axi_qualification_state
    from library.tools.execution_router import (
        RoutingRefused,
        route_axi_dispatch,
    )

    context = {"qualification_state": axi_qualification_state.inspect_handles(
        resolve=resolve, resolve_project=project, timeline=timeline,
        operations=list(operations))}
    try:
        decision = route_axi_dispatch(
            [dict(operation) for operation in operations], context=context)
    except RoutingRefused as refused:
        raise DirectRouteUnavailable(
            f"the router found no direct route: {refused}") from refused
    reasons = list(decision.get("reasons") or ())
    mode = str(decision.get("mode") or "")
    if mode != "direct":
        raise DirectRouteUnavailable(
            f"the router chose {decision.get('backend', '?')} in "
            f"{mode or '?'} mode: " + "; ".join(reasons))
    return {"backend": str(decision.get("backend") or ""),
            "mode": mode,
            "reasons": reasons,
            "gaps": list(decision.get("qualification_gaps") or ()),
            "verification": list(decision.get("verification") or ())}


def editor_changes_clear(project_folder: str, final: str, project: Any,
                          timeline: Any) -> dict:
    """Whether the approved timeline carries an unattributed editor change.

    The staged route carries the editor's hand edits onto the staging
    copy and refuses the ones the touch would destroy.  A direct write
    has no staging copy to carry them on, so it refuses to run at all
    while any are pending: the change is staged instead, and the staged
    route's accepted-edit vocabulary decides the question.

    The detection is the same one the promotion uses, on the same
    reads, so the two routes agree about what the editor changed.
    """
    from library.tools import reel_replace_guard as guard

    live = guard.full_timeline_snapshot(timeline, project, project_folder)
    detection = guard.detect_editor_changes(project_folder, final, live, live)
    pending = list(detection.get("pending") or ())
    return {"clear": not pending,
            "pending": len(pending),
            "baseline": detection.get("baseline"),
            "first_contact": bool(detection.get("first_contact")),
            "live": live}


def apply(*, project: Any, timeline: Any, project_folder: str,
          capability: str, operations: Sequence[Mapping],
          idempotency_key: str) -> dict:
    """Write the operations on the approved timeline, read back.

    The EditPatch owns the write: it observes the generation, checks
    the preconditions, writes, reads the properties back, compares the
    timeline outside the declared targets and records the generation
    whose inverse is the way back.
    """
    from library.tools import edit_patch

    receipt = edit_patch.apply_live_patch(
        project=project, timeline=timeline, capability=capability,
        operations=[dict(operation) for operation in operations],
        conflict_domains=("picture_transform", "timeline_structure"),
        idempotency_key=idempotency_key,
        project_folder=project_folder)
    if str(receipt.get("status")) != "committed":
        raise DirectRouteUnavailable(
            f"the direct write did not verify: {receipt}")
    return receipt


def idempotency_key(final: str) -> str:
    """A key that is unique to ONE attempt at this touch.

    A retry of the same request writes again - the key is not a
    content digest, because a captain may set the same value twice with
    a different intent in between, and a digest would swallow the
    second write.
    """
    return f"touch:{final}:direct:{uuid.uuid4().hex[:12]}"


__all__ = [
    "TOUCH_OP_TO_ALGEBRA_OP",
    "DirectRouteUnavailable",
    "apply",
    "editor_changes_clear",
    "idempotency_key",
    "plan",
    "route",
]
