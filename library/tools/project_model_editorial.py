"""Unit 4: editorial and operation migration onto the Project Model.

The plan (Unit 4) asks that the edit graph, edit ledger, EditPatch,
composed-edit records and Project Graph queries use stable Project Model
IDs and shared time semantics; that existing run outputs and supported
user edits rebuild through one adapter preserving inverse/conflict/
readback behavior; and that no frame-based user pin is silently promoted
into a durable semantic anchor.

What this module is
-------------------
A read-only projection bridge, in the same strangler-period shape as
``project_model_store.parity_report``: legacy stores are never rewritten
here. Every function below takes legacy rows/nodes/records plus explicit
clock IDs and returns Project Model fragments (``EditOperation``,
``ClipUse``, ``FrameMap`` segment dicts) that ``apply_editorial_fragment``
can append to a model. Re-running a migration is idempotent: IDs are
stable, and existing IDs are never duplicated.

Stable IDs
----------
Where the legacy row already has a stable key it is namespaced, never
re-hashed, so both migration paths agree:

- edit-graph node ``aroll:3`` -> operation ``edit_graph:aroll:3``
- project-graph wrapper ``edit:aroll:3`` -> the same operation id
- EditPatch ``patch-7`` -> operation ``edit_patch:patch-7``
- composed-edit item -> ``composed:<row>:<item_index>:<prev_record>-><record>``

Ledger rows carry no id, so they hash their canonical content
(``ledger:<sha16>``); duplicates get ``#2``-style suffixes only within a
single migration call, never across runs.

Shared time semantics
---------------------
The model speaks integer ticks on named clocks in half-open intervals
(``[start, end)``, ``start < end``). Frame-domain inputs (EditPatch
affected spans, composed-edit record frames) are already integer ticks on
the timeline clock and pass through unchanged. Second-domain inputs
(edit-graph payload spans) convert via ``round(seconds *
ticks_per_second)`` on the caller-named clock. Anything empty or inverted
is refused, never clamped.

What is deliberately NOT migrated
---------------------------------
- Legacy ``evidence_refs`` such as ``clip_catalog:clip_001`` are carried
  inside ``after`` lineage, never promoted into typed ``evidence_ids``:
  only a measured observation link (Unit 3) may mint those.
- ``entity_links`` on migrated ``ClipUse`` records stay empty: person
  identity is Unit 3's to establish, and this bridge never guesses it.
- Ledger rows anchored to frames, or carrying any frame/timecode pin
  field, are refused with the same reason ``edit_ledger.validate_rows``
  gives. A user pin never becomes a model ``Interval`` here.
- Composed-edit ``Insertion`` records (genuinely new items) become
  ``EditOperation`` rows, not ``ClipUse`` or frame-map segments: the media
  pool handle names no source interval, and inventing one would be a guess.
- Nothing migrates to ``Effect``: no legacy node carries model-shaped
  parameters plus keyframes on a named clock, so any such row would be
  invented taste (AGENTS.md 10.5).
- Inverse/conflict/readback behavior is preserved by carrying the algebra
  operations, conflict domains, pre/postconditions and base generation
  verbatim inside the migrated operation: the existing ``edit_patch``,
  ``patch_algebra`` and ``timeline_shadow`` code paths still judge them.
  This adapter never re-decides a conflict.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any

from library.tools import evidence_contract
from library.tools.edit_point_planner import generate_edit_point_candidates

#: Anchor kinds that name frames. ``edit_ledger`` already refuses these;
#: the migration refuses them again so a row that somehow bypassed
#: validation still cannot become a durable semantic anchor.
FRAME_ANCHOR_KINDS = frozenset({"frames", "frame", "timeline_frames", "source_frames"})

#: Fields that pin a frame or timecode. Any of these on a ledger row or
#: its anchor refuses the migration of that row.
FRAME_PIN_FIELDS = frozenset({
    "anchor_frame", "anchor_frames", "frame", "frames",
    "timeline_start_frame", "timeline_end_frame",
    "timeline_start", "timeline_end", "start", "end",
    "start_frame", "end_frame", "timecode",
})

#: ClipUse roles this bridge emits.
AROLL_ROLE = "a_roll"
BROLL_ROLE = "b_roll"


class EditorialMigrationError(ValueError):
    """A legacy editorial row that cannot become model data, refused by name."""


def timeline_state_for(
    model: Mapping[str, Any], timeline_id: str
) -> dict[str, Any] | None:
    """Read the current canonical ordered timeline composition from a model."""
    rows = [row for row in model.get("timeline_states", [])
            if row["timeline_id"] == timeline_id]
    if len(rows) > 1:
        raise EditorialMigrationError(
            f"Project Model has multiple current states for timeline {timeline_id!r}"
        )
    return dict(rows[0]) if rows else None


def commit_timeline_state(project, state: Mapping[str, Any], *,
                          expected_revision: int, author: str = "ren") -> int:
    """CAS one current timeline state into the Project Model event history."""
    import copy
    from pathlib import Path

    from library.tools import project_model_store

    if not isinstance(state, Mapping):
        raise EditorialMigrationError("timeline state must be an object")
    _, model = project_model_store.load_project_model(Path(project))
    rows = [row for row in model.get("timeline_states", [])
            if row["timeline_id"] != state["timeline_id"]]
    rows.append(copy.deepcopy(dict(state)))
    return project_model_store.append_model_patch(
        Path(project), {"timeline_states": rows},
        expected_revision=expected_revision, author=author,
        reason=f"timeline state update: {state['timeline_id']}",
    )


def commit_rendered_timeline(project, *, timeline_id: str, generation: int,
                             expected_revision: int, author: str = "render.build",
                             max_reconciliations: int = 3) -> dict[str, Any]:
    """Commit a rendered Resolve generation, reconciling concurrent model writes.

    Resolve is the timeline truth. If its write succeeds while another model
    writer advances the event head, replay only this timeline-state projection
    onto the new head. The other writer's collections and timeline states are
    retained. The event reason records both revisions so the recovery is
    auditable in the Project Model history.
    """
    from pathlib import Path

    from library.tools import project_model_store

    if not isinstance(timeline_id, str) or not timeline_id:
        raise EditorialMigrationError("rendered timeline needs a stable timeline_id")
    if (isinstance(generation, bool) or not isinstance(generation, int)
            or generation < 1):
        raise EditorialMigrationError("rendered timeline generation must be positive")
    if (isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
            or expected_revision < 0):
        raise EditorialMigrationError(
            "expected Project Model revision must be non-negative")
    project = Path(project)
    state = {
        "id": stable_id("timeline_state", timeline_id),
        "timeline_id": timeline_id,
        "generation": generation,
        "clip_use_ids": [],
        "edit_operation_ids": [],
    }
    attempted_revision = expected_revision
    for attempt in range(max_reconciliations + 1):
        try:
            revision = commit_timeline_state(
                project, state, expected_revision=expected_revision, author=author)
            return {"status": "committed", "revision": revision,
                    "expected_revision": attempted_revision,
                    "reconciled": attempt > 0,
                    "timeline_id": timeline_id, "generation": generation}
        except RuntimeError as exc:
            if "stale project model revision" not in str(exc):
                raise
            if attempt == max_reconciliations:
                return {"status": "refused", "reason": str(exc),
                        "expected_revision": attempted_revision,
                        "timeline_id": timeline_id, "generation": generation}
            expected_revision, model = project_model_store.load_project_model(project)
            rows = [row for row in model.get("timeline_states", [])
                    if row["timeline_id"] != timeline_id]
            rows.append(state)
            try:
                revision = project_model_store.append_model_patch(
                    project, {"timeline_states": rows},
                    expected_revision=expected_revision, author=author,
                    reason=(f"Resolve render reconciliation: timeline {timeline_id} "
                            f"generation {generation}; initial model revision "
                            f"{attempted_revision}, conflict observed at revision "
                            f"{expected_revision}"),
                )
            except RuntimeError as retry_exc:
                if "stale project model revision" in str(retry_exc):
                    continue
                raise
            return {"status": "committed", "revision": revision,
                    "expected_revision": attempted_revision, "reconciled": True,
                    "conflicted_revision": expected_revision,
                    "timeline_id": timeline_id, "generation": generation}
    raise AssertionError("unreachable reconciliation loop")


def commit_edit_readback(project, *, patch: Any, readback: Mapping[str, Any],
                         execution: Mapping[str, Any],
                         generation: int, expected_revision: int,
                         author: str = "ren") -> int:
    """Commit an applied EditPatch and its Resolve readback with CAS.

    The operation retains the complete replayable patch and the observed
    timeline snapshot. The current composition references that operation;
    a concurrent Project Model writer makes this commit fail closed.
    """
    import copy
    from pathlib import Path

    from library.tools import project_model_store

    revision, model = project_model_store.load_project_model(Path(project))
    if revision != expected_revision:
        raise RuntimeError(
            f"stale project model revision {expected_revision}; current revision "
            f"is {revision}"
        )
    operation = patch_to_operation(patch)
    source_to_timeline = []
    for track in readback["tracks"]:
        for clip in track["clips"]:
            source_start = int(clip["source_in_frame"])
            source_end = int(clip["source_out_frame"])
            # A native hold reports the held frame as both inclusive
            # source edges. Model that single source frame explicitly.
            if clip.get("speed") == 0 and source_end == source_start:
                source_end = source_start + 1
            timeline_start = int(clip["record_in"])
            timeline_end = int(clip["record_out"])
            if source_end <= source_start or timeline_end <= timeline_start:
                raise EditorialMigrationError(
                    f"Resolve readback has an invalid source/timeline span "
                    f"for clip {clip['unique_id']!r}"
                )
            source_to_timeline.append({
                "timeline_item_id": str(clip["unique_id"]),
                "source_file": str(clip["source_file"]),
                "source_frames": [source_start, source_end],
                "timeline_frames": [timeline_start, timeline_end],
                "mapping_kind": (
                    "one_to_one" if source_end - source_start ==
                    timeline_end - timeline_start else "retimed"
                ),
            })
    operation["after"] = {
        **operation["after"],
        "resolve_readback": copy.deepcopy(dict(readback)),
        "resolve_execution": copy.deepcopy(dict(execution)),
        "source_to_timeline": source_to_timeline,
        "resolve_generation": generation,
    }
    edits = list(model.get("edits", []))
    existing = next((row for row in edits if row["id"] == operation["id"]), None)
    if existing is not None and existing != operation:
        raise EditorialMigrationError(
            f"Project Model edit {operation['id']!r} already has different contents"
        )
    if existing is None:
        edits.append(operation)
    state = timeline_state_for(model, patch.timeline) or {
        "id": stable_id("timeline_state", patch.timeline),
        "timeline_id": patch.timeline,
        "generation": 0,
        "clip_use_ids": [],
        "edit_operation_ids": [],
    }
    state["generation"] = generation
    if operation["id"] not in state["edit_operation_ids"]:
        state["edit_operation_ids"].append(operation["id"])
    states = [row for row in model.get("timeline_states", [])
              if row["timeline_id"] != patch.timeline]
    states.append(state)
    model_revision = project_model_store.append_model_patch(
        Path(project), {"edits": edits, "timeline_states": states},
        expected_revision=expected_revision, author=author,
        reason=f"EditPatch {patch.id} Resolve readback",
    )
    # PM revision numbers remain the Project Model's global event sequence.
    # The separate creative id links this editorial event to its edit and
    # observed Resolve generation without copying the readback snapshot.
    from library.tools import creative_revision
    creative_revision.create(
        project, author=author, intent=str(getattr(patch, "description", "") or
                                           f"EditPatch {patch.id}"),
        operations=[{"edit_operation_id": operation["id"],
                     "edit_patch_id": str(patch.id)}],
        base_generation=int(getattr(patch, "base_generation", 0) or 0),
        result_generation=generation,
        project_model_revision=model_revision,
        affected_entities=[str(patch.timeline)],
        verification={"source": "resolve_readback", "generation": generation},
        refs={"project_model": {"revision": model_revision,
                                "edit_operation_id": operation["id"]},
              "edit_patch": {"patch_id": str(patch.id)},
              "timeline_shadow": {"timeline_id": str(patch.timeline),
                                   "generation": generation}},
        status="verified")
    return model_revision


def plan_edit_point_candidates(model: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expose measured Project Model cut candidates to editorial planning.

    The returned rows remain candidates, not ``EditOperation`` records. The
    planner refuses to promote them to edits without a separate decision.
    """
    return generate_edit_point_candidates(model)


def stable_id(prefix: str, *parts: Any) -> str:
    """A deterministic ``<prefix>:<sha16>`` over canonical JSON parts."""
    canonical = "\x00".join(
        json.dumps(part, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for part in parts
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def interval_from_ticks(clock_id: str, start: Any, end: Any) -> dict[str, Any]:
    """A model half-open interval from integer ticks on a named clock."""
    if not isinstance(clock_id, str) or not clock_id:
        raise EditorialMigrationError("an interval needs a named clock_id")
    if isinstance(start, bool) or isinstance(end, bool):
        raise EditorialMigrationError(
            f"interval ticks must be integers, got {start!r}..{end!r}"
        )
    if not isinstance(start, int) or not isinstance(end, int):
        raise EditorialMigrationError(
            f"interval ticks must be integers, got {start!r}..{end!r}"
        )
    if start >= end:
        raise EditorialMigrationError(
            f"interval must be non-empty and half-open (start < end), "
            f"got [{start}, {end}) on {clock_id}"
        )
    return {"clock_id": clock_id, "start": start, "end": end}


def interval_from_seconds(
    clock_id: str, start_s: Any, end_s: Any, ticks_per_second: int
) -> dict[str, Any]:
    """A model interval from second-domain spans on a named clock."""
    if not _is_number(start_s) or not _is_number(end_s):
        raise EditorialMigrationError(
            f"second-domain spans must be numbers, got {start_s!r}..{end_s!r}"
        )
    if not isinstance(ticks_per_second, int) or ticks_per_second <= 0:
        raise EditorialMigrationError(
            f"ticks_per_second must be a positive int, got {ticks_per_second!r}"
        )
    try:
        start, end = evidence_contract.sec_to_ticks(
            float(start_s), float(end_s), ticks_per_second)
    except evidence_contract.TemporalAlignmentError as exc:
        raise EditorialMigrationError(
            f"interval must be non-empty and half-open (start < end) on "
            f"{clock_id}: {exc}") from exc
    return interval_from_ticks(clock_id, start, end)


def interval_from_played_frames(
    clock_id: str,
    start_frame: Any,
    played_frames: Any,
    ticks_per_second: int,
    fps_num: int,
    fps_den: int,
) -> dict[str, Any]:
    """A model half-open interval from a start frame plus a played count.

    The end is DERIVED (start plus played), never taken from a raw end
    frame. Reel 22's Craig cut is why: the review snapshot's
    ``source_out_frame`` carries Resolve's raw ``GetSourceEndFrame()``
    readback, which disagrees by a frame on about a third of clips
    (``library/tools/timeline_ingest.py`` derives the played range instead
    for the same reason). Copying the raw end 87284 into a half-open
    interval models a 254-frame source for a 255-frame play, which the
    native projection correctly refuses as ``source_too_short``; the
    played end is 87285.

    Frames convert exactly (no rounding: a frame count that is not whole
    ticks on the clock is refused, never rounded into place).
    """
    if isinstance(start_frame, bool) or isinstance(played_frames, bool):
        raise EditorialMigrationError(
            f"frame counts must be integers, got {start_frame!r}..{played_frames!r}"
        )
    if not isinstance(start_frame, int) or not isinstance(played_frames, int):
        raise EditorialMigrationError(
            f"frame counts must be integers, got {start_frame!r}..{played_frames!r}"
        )
    if played_frames <= 0:
        raise EditorialMigrationError(
            f"played frames must be positive, got {played_frames!r}"
        )
    for name, value in (("ticks_per_second", ticks_per_second),
                        ("fps_num", fps_num), ("fps_den", fps_den)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise EditorialMigrationError(
                f"{name} must be a positive int, got {value!r}"
            )
    start_product = start_frame * ticks_per_second * fps_den
    if start_product % fps_num != 0:
        raise EditorialMigrationError(
            f"start frame {start_frame} is not whole ticks on {clock_id}: "
            f"refusing, not rounding"
        )
    length_product = played_frames * ticks_per_second * fps_den
    if length_product % fps_num != 0:
        raise EditorialMigrationError(
            f"{played_frames} played frames are not whole ticks on "
            f"{clock_id}: refusing, not rounding"
        )
    start = start_product // fps_num
    return interval_from_ticks(clock_id, start, start + length_product // fps_num)


def spans_to_intervals(
    spans: Any, clock_id: str
) -> list[dict[str, Any]]:
    """EditPatch ``affected_spans`` (frame-domain ticks) to model intervals."""
    if not isinstance(spans, (list, tuple)) or not spans:
        raise EditorialMigrationError(
            "affected_spans must be a non-empty list of [start, end) pairs"
        )
    intervals = []
    for span in spans:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            raise EditorialMigrationError(
                f"an affected span must be one [start, end) pair, got {span!r}"
            )
        intervals.append(interval_from_ticks(clock_id, int(span[0]), int(span[1])))
    return intervals


def assert_no_frame_pin(
    label: str, row: Mapping[str, Any], anchor: Mapping[str, Any]
) -> None:
    """Refuse a frame-based anchor or pin; never silently promote one."""
    kind = anchor.get("kind")
    if kind in FRAME_ANCHOR_KINDS:
        raise EditorialMigrationError(
            f"{label} anchors to {kind!r}: a frame number is not stable "
            f"across a rebuild. Anchor to the spoken words instead."
        )
    for field in FRAME_PIN_FIELDS:
        if field in row or field in anchor:
            raise EditorialMigrationError(
                f"{label} carries {field!r}: a frame or timecode pin breaks "
                f"the moment anything upstream re-times. Anchor to spoken "
                f"words instead."
            )


def edit_graph_node_to_operation(
    node: Mapping[str, Any], *, author: str = "ren"
) -> dict[str, Any]:
    """One edit-graph node to one model ``EditOperation``.

    The operation id namespaces the stable node id, so the project-graph
    wrapper path (``edit:<node_id>``) converges on the same id. Legacy
    evidence refs travel inside ``after`` lineage; typed ``evidence_ids``
    are copied only from measured observations explicitly attached by the
    evidence-to-edit caller.
    """
    if not isinstance(node, Mapping):
        raise EditorialMigrationError("an edit-graph node must be an object")
    node_id = node.get("node_id")
    node_type = node.get("node_type")
    if not isinstance(node_id, str) or not node_id:
        raise EditorialMigrationError("an edit-graph node needs a node_id")
    if not isinstance(node_type, str) or not node_type:
        raise EditorialMigrationError(
            f"edit-graph node {node_id!r} needs a node_type"
        )
    payload = node.get("payload")
    evidence_ids = list(node.get("evidence_ids") or [])
    query_result_id = node.get("query_result_id")
    evidence_revision = node.get("evidence_revision")
    creative_rationale = node.get("creative_rationale")
    return {
        "id": f"edit_graph:{node_id}",
        "operation": f"edit_graph.{node_type}",
        "target_ids": [node_id],
        "before": None,
        "after": {
            "node_type": node_type,
            "producer": node.get("producer"),
            "label": node.get("label"),
            "payload": dict(payload) if isinstance(payload, Mapping) else payload,
            "intent_refs": list(node.get("intent_refs") or []),
            "evidence_refs": list(node.get("evidence_refs") or []),
            "constraints": dict(node.get("constraints") or {}),
            "depends_on": list(node.get("depends_on") or []),
        },
        "intent_id": None,
        "evidence_ids": evidence_ids,
        **({"query_result_id": query_result_id} if query_result_id else {}),
        **({"evidence_revision": evidence_revision}
           if evidence_revision is not None else {}),
        **({"creative_rationale": creative_rationale}
           if creative_rationale is not None else {}),
        "author": author,
        "parent_revision": None,
    }


def project_graph_edit_node_to_operation(
    node: Mapping[str, Any], *, author: str = "ren"
) -> dict[str, Any]:
    """One project-graph ``edit:`` node to the same operation its source makes.

    The wrapper carries the source node under ``payload.edit_node_id`` with
    the source payload nested at ``payload.edit_payload``; unwrapping it
    here is what keeps Project Graph queries on stable Project Model IDs:
    both paths mint the identical operation id.
    """
    if not isinstance(node, Mapping):
        raise EditorialMigrationError("a project-graph node must be an object")
    payload = node.get("payload") or {}
    source_id = payload.get("edit_node_id")
    if not isinstance(source_id, str) or not source_id:
        raise EditorialMigrationError(
            f"project-graph node {node.get('node_id')!r} is not an edit "
            f"wrapper: no payload.edit_node_id"
        )
    source = {
        "node_id": source_id,
        "node_type": payload.get("node_type"),
        "producer": payload.get("producer"),
        "label": node.get("label"),
        "payload": payload.get("edit_payload") or {},
        "intent_refs": payload.get("intent_refs") or [],
        "evidence_refs": payload.get("evidence_refs") or [],
        "evidence_ids": payload.get("evidence_ids") or [],
        "query_result_id": payload.get("query_result_id"),
        "evidence_revision": payload.get("evidence_revision"),
        "creative_rationale": payload.get("creative_rationale"),
        "constraints": payload.get("constraints") or {},
        "depends_on": payload.get("depends_on") or [],
    }
    return edit_graph_node_to_operation(source, author=author)


def model_id_for_legacy(kind: str, legacy_id: str) -> str:
    """The stable model id a legacy ``<kind>:<id>`` answers to.

    Query readers accept either form during the strangler period; this is
    the canonical direction (legacy -> model).
    """
    if not isinstance(kind, str) or not kind:
        raise EditorialMigrationError("a model id needs a non-empty kind")
    if not isinstance(legacy_id, str) or not legacy_id:
        raise EditorialMigrationError("a model id needs a non-empty legacy id")
    namespaced = f"{kind}:{legacy_id}"
    if legacy_id.startswith(f"{kind}:"):
        namespaced = legacy_id
    return namespaced


def ledger_row_to_operation(row: Mapping[str, Any]) -> dict[str, Any]:
    """One edit-ledger row to one model ``EditOperation``.

    Frame-anchored rows and rows carrying frame/timecode pins are refused
    outright: a user pin must never become a durable semantic anchor by
    passing through this bridge. Word and reel anchors travel verbatim in
    ``after``; the row gains no ticks here because it names none.
    """
    if not isinstance(row, Mapping):
        raise EditorialMigrationError("an edit-ledger row must be an object")
    anchor = row.get("anchor")
    if not isinstance(anchor, Mapping):
        raise EditorialMigrationError(
            "an edit-ledger row needs an anchor object (words or reel)"
        )
    kind = anchor.get("kind")
    if kind not in ("words", "reel"):
        if kind in FRAME_ANCHOR_KINDS:
            raise EditorialMigrationError(
                f"ledger row anchors to {kind!r}: a frame number is not "
                f"stable across a rebuild. Anchor to the spoken words instead."
            )
        raise EditorialMigrationError(
            f"ledger row anchor.kind is {kind!r}: one of words, reel"
        )
    assert_no_frame_pin("ledger row", row, anchor)
    op = row.get("op")
    if not isinstance(op, str) or not op:
        raise EditorialMigrationError("a ledger row needs an op")
    canonical = {
        "op": op,
        "anchor": dict(anchor),
        "params": dict(row.get("params") or {}),
        "reel": row.get("reel"),
    }
    stated_by = row.get("stated_by", "requester")
    return {
        "id": stable_id("ledger", canonical),
        "operation": f"edit_ledger.{op}",
        "target_ids": [f"ledger-anchor:{kind}"],
        "before": None,
        "after": {
            **canonical,
            "value_sources": row.get("value_sources"),
            "value_units": row.get("value_units"),
            "reason": row.get("reason"),
            "source_note_id": row.get("source_note_id"),
        },
        "intent_id": None,
        "evidence_ids": list(row.get("evidence_ids") or []),
        **({"query_result_id": row["query_result_id"]}
           if row.get("query_result_id") else {}),
        **({"evidence_revision": row["evidence_revision"]}
           if row.get("evidence_revision") is not None else {}),
        **({"creative_rationale": row["creative_rationale"]}
           if row.get("creative_rationale") is not None else {}),
        "author": stated_by if isinstance(stated_by, str) else "requester",
        "parent_revision": None,
    }


def patch_to_operation(patch: Any, *, intent_id: str | None = None) -> dict[str, Any]:
    """One ``EditPatch`` to one model ``EditOperation``.

    Conflict domains, operations, pre/postconditions and the base
    generation travel verbatim: the existing patch-algebra and shadow
    readback paths keep judging them, so inverse/conflict/readback
    behavior is preserved rather than re-decided here. The timeline
    clock is named by the caller at readback time (see
    ``spans_to_intervals``), never guessed from frame counts.
    """
    from library.tools.edit_patch import EditPatch

    if not isinstance(patch, EditPatch):
        patch = EditPatch.from_dict(dict(patch))
    return {
        "id": f"edit_patch:{patch.id}",
        "operation": "timeline.patch",
        "target_ids": [f"capability:{patch.capability}"],
        "before": {
            "base_generation": patch.base_generation,
            "affected_spans": [list(span) for span in patch.affected_spans],
            "project": patch.project,
            "timeline": patch.timeline,
        },
        "after": {
            "operations": [dict(op) for op in patch.operations],
            "conflict_domains": list(patch.conflict_domains),
            "preconditions": [dict(c) for c in patch.preconditions],
            "postconditions": [dict(c) for c in patch.postconditions],
            "base_git_sha": patch.base_git_sha,
            "expected_model_revision": patch.expected_model_revision,
        },
        "intent_id": intent_id,
        "evidence_ids": [],
        "author": "ren",
        "parent_revision": str(patch.base_generation),
    }


def operation_to_patch(operation: Mapping[str, Any]) -> dict[str, Any]:
    """Invert a migrated ``timeline.patch`` operation back to patch data.

    Only operations this bridge created (``timeline.patch`` with the patch
    envelope in ``before``/``after``) round-trip; anything else is refused
    rather than reconstructed from guesses.
    """
    from library.tools.edit_patch import EditPatch

    if not isinstance(operation, Mapping):
        raise EditorialMigrationError("an operation must be an object")
    if operation.get("operation") != "timeline.patch":
        raise EditorialMigrationError(
            f"operation {operation.get('id')!r} is not a migrated patch "
            f"(operation {operation.get('operation')!r})"
        )
    before = operation.get("before") or {}
    after = operation.get("after") or {}
    try:
        patch_id = str(operation["id"]).split("edit_patch:", 1)[1]
    except (IndexError, AttributeError) as exc:
        raise EditorialMigrationError(
            f"operation {operation.get('id')!r} carries no patch id"
        ) from exc
    data = {
        "id": patch_id,
        "project": before.get("project"),
        "timeline": before.get("timeline"),
        "base_generation": before.get("base_generation"),
        "capability": str(operation.get("target_ids", [""])[0]).split(
            "capability:", 1)[-1],
        "affected_spans": before.get("affected_spans"),
        "conflict_domains": after.get("conflict_domains"),
        "operations": after.get("operations"),
        "preconditions": after.get("preconditions", []),
        "postconditions": after.get("postconditions", []),
        "base_git_sha": after.get("base_git_sha", ""),
        "expected_model_revision": after.get("expected_model_revision"),
    }
    return EditPatch.from_dict(data).to_dict()


def composed_change_to_operation(change: Any) -> dict[str, Any]:
    """One composed-edit ``ItemChange``/``Insertion`` to an operation.

    Frame numbers travel as the frames they are (timeline-domain ticks),
    inside ``before``/``after`` - they never become a semantic anchor, and
    a genuinely new ``Insertion`` never becomes a ``ClipUse`` because its
    pool handle names no source interval.
    """
    from library.tools.composed_edit import Insertion, ItemChange

    if isinstance(change, Insertion):
        if change.properties is None:
            raise EditorialMigrationError(
                f"insertion {change.name!r} declares no treatment: "
                f"declaring properties={{}} is how identity is chosen on "
                f"purpose; None refuses."
            )
        return {
            "id": stable_id("composed-insert", {
                "row": change.row,
                "record_frame": change.record_frame,
                "duration": change.duration,
                "left_offset": change.left_offset,
                "name": change.name,
            }),
            "operation": "composed.insert",
            "target_ids": [f"timeline-row:{change.row}"],
            "before": None,
            "after": {
                "row": change.row,
                "record_frame": int(change.record_frame),
                "duration": int(change.duration),
                "left_offset": int(change.left_offset),
                "name": change.name,
                "properties": dict(change.properties),
            },
            "intent_id": None,
            "evidence_ids": [],
            "author": "ren",
            "parent_revision": None,
        }
    if isinstance(change, ItemChange):
        if int(change.duration) <= 0 or int(change.previous_duration) <= 0:
            raise EditorialMigrationError(
                f"composed change on {change.row} has no played length: "
                f"a zero-length item cannot become a half-open interval"
            )
        return {
            "id": f"composed:{change.row}:{int(change.item_index)}:"
                   f"{int(change.previous_record)}->{int(change.record_frame)}"
                   f"x{int(change.duration)}",
            "operation": f"composed.{change.how}",
            "target_ids": [f"timeline-row:{change.row}"],
            "before": {
                "record_frame": int(change.previous_record),
                "duration": int(change.previous_duration),
                "left_offset": int(change.left_offset),
            },
            "after": {
                "record_frame": int(change.record_frame),
                "duration": int(change.duration),
                "left_offset": int(change.left_offset),
                "how": change.how,
                "name": change.name,
            },
            "intent_id": None,
            "evidence_ids": [],
            "author": "ren",
            "parent_revision": None,
        }
    raise EditorialMigrationError(
        f"a composed change must be an ItemChange or Insertion, "
        f"got {type(change).__name__}"
    )


def composed_change_to_segment(
    change: Any, *, from_clock: str, to_clock: str, clip_use_id: str | None = None
) -> dict[str, Any]:
    """One moved ``ItemChange`` to one model ``FrameMap`` segment.

    The segment relates the pre-edit timeline interval to the post-edit
    one. Equal played lengths map ``one_to_one``; a changed played length
    maps ``retimed`` with an explicit rate (output ticks per input tick).
    Insertions have no source interval and are refused here - they travel
    as operations via ``composed_change_to_operation``.
    """
    from library.tools.composed_edit import Insertion, ItemChange

    if isinstance(change, Insertion):
        raise EditorialMigrationError(
            f"insertion {change.name!r} has no source interval: it cannot "
            f"become a frame-map segment"
        )
    if not isinstance(change, ItemChange):
        raise EditorialMigrationError(
            f"a composed change must be an ItemChange or Insertion, "
            f"got {type(change).__name__}"
        )
    if int(change.previous_duration) <= 0 or int(change.duration) <= 0:
        raise EditorialMigrationError(
            f"composed change on {change.row} has no played length: "
            f"a zero-length item cannot become a half-open interval"
        )
    same_length = int(change.duration) == int(change.previous_duration)
    segment: dict[str, Any] = {
        "from": interval_from_ticks(
            from_clock,
            int(change.previous_record),
            int(change.previous_record) + int(change.previous_duration),
        ),
        "to": interval_from_ticks(
            to_clock,
            int(change.record_frame),
            int(change.record_frame) + int(change.duration),
        ),
        "mapping_kind": "one_to_one" if same_length else "retimed",
        "rate": None,
        "source_clip_use": clip_use_id,
    }
    if not same_length:
        segment["rate"] = {
            "numerator": int(change.duration),
            "denominator": int(change.previous_duration),
        }
    return segment


def _clip_use_id(role: str, clip_id: str, source: dict, timeline: dict) -> str:
    return stable_id("clip_use", {
        "role": role,
        "clip_id": clip_id,
        "source": source,
        "timeline": timeline,
    })


def aroll_node_to_clip_use(
    node: Mapping[str, Any],
    *,
    asset_by_clip: Mapping[str, str],
    source_clock_by_asset: Mapping[str, str],
    source_tps_by_asset: Mapping[str, int],
    timeline_clock: str,
    timeline_tps: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """One A-roll assignment node to one model ``ClipUse``.

    Returns ``(clip_use, None)`` on success or ``(None, reason)`` when the
    node cannot migrate without guessing: an unknown clip, missing spans
    or an empty interval is reported by name, never filled in. Identity
    links stay empty for Unit 3 to establish.
    """
    payload = node.get("payload") if isinstance(node, Mapping) else None
    if not isinstance(payload, Mapping):
        return None, f"node {node.get('node_id')!r} carries no payload"
    clip_id = payload.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        return None, f"node {node.get('node_id')!r} names no clip"
    asset_id = asset_by_clip.get(clip_id)
    if asset_id is None:
        return None, f"clip {clip_id!r} has no model asset: refusing, not guessing"
    source_clock = source_clock_by_asset.get(asset_id)
    source_tps = source_tps_by_asset.get(asset_id)
    if source_clock is None or source_tps is None:
        return None, f"asset {asset_id!r} names no source clock"
    for key in ("source_start", "source_end", "timeline_start", "timeline_end"):
        if not _is_number(payload.get(key)):
            return None, (
                f"node {node.get('node_id')!r} has no numeric {key}: "
                f"refusing, not guessing"
            )
    try:
        source = interval_from_seconds(
            source_clock, payload["source_start"], payload["source_end"], source_tps
        )
        timeline = interval_from_seconds(
            timeline_clock, payload["timeline_start"], payload["timeline_end"],
            timeline_tps,
        )
    except EditorialMigrationError as exc:
        return None, f"node {node.get('node_id')!r}: {exc}"
    return {
        "id": _clip_use_id(AROLL_ROLE, clip_id, source, timeline),
        "asset_id": asset_id,
        "source_interval": source,
        "timeline_interval": timeline,
        "entity_links": [],
        "role": AROLL_ROLE,
        "intent_id": None,
    }, None


def broll_node_to_clip_use(
    node: Mapping[str, Any],
    *,
    asset_by_clip: Mapping[str, str],
    source_clock_by_asset: Mapping[str, str],
    source_tps_by_asset: Mapping[str, int],
    timeline_clock: str,
    timeline_tps: int,
) -> tuple[dict[str, Any] | None, str | None]:
    """One B-roll cover node to one model ``ClipUse`` (role ``b_roll``).

    Same fail-closed contract as ``aroll_node_to_clip_use``: the source
    span is ``video_in``/``video_out`` where present, falling back to the
    node's own ``source_start``/``source_end``. Nothing is invented when
    both are absent.
    """
    payload = node.get("payload") if isinstance(node, Mapping) else None
    if not isinstance(payload, Mapping):
        return None, f"node {node.get('node_id')!r} carries no payload"
    clip_id = payload.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        return None, f"node {node.get('node_id')!r} names no clip"
    asset_id = asset_by_clip.get(clip_id)
    if asset_id is None:
        return None, f"clip {clip_id!r} has no model asset: refusing, not guessing"
    source_clock = source_clock_by_asset.get(asset_id)
    source_tps = source_tps_by_asset.get(asset_id)
    if source_clock is None or source_tps is None:
        return None, f"asset {asset_id!r} names no source clock"
    source_start = payload.get("video_in", payload.get("source_start"))
    source_end = payload.get("video_out", payload.get("source_end"))
    if not _is_number(source_start) or not _is_number(source_end):
        return None, (
            f"node {node.get('node_id')!r} names no source span "
            f"(video_in/out or source_start/end): refusing, not guessing"
        )
    if not _is_number(payload.get("timeline_start")) or not _is_number(
        payload.get("timeline_end")
    ):
        return None, (
            f"node {node.get('node_id')!r} names no timeline span: "
            f"refusing, not guessing"
        )
    try:
        source = interval_from_seconds(
            source_clock, source_start, source_end, source_tps
        )
        timeline = interval_from_seconds(
            timeline_clock, payload["timeline_start"], payload["timeline_end"],
            timeline_tps,
        )
    except EditorialMigrationError as exc:
        return None, f"node {node.get('node_id')!r}: {exc}"
    return {
        "id": _clip_use_id(BROLL_ROLE, clip_id, source, timeline),
        "asset_id": asset_id,
        "source_interval": source,
        "timeline_interval": timeline,
        "entity_links": [],
        "role": BROLL_ROLE,
        "intent_id": None,
    }, None


def _fps_pair(name: str, rate: Any) -> tuple[int, int]:
    """An (fps_num, fps_den) pair, validated as positive ints."""
    if (not isinstance(rate, (list, tuple)) or len(rate) != 2
            or any(isinstance(v, bool) or not isinstance(v, int)
                   for v in rate)
            or rate[0] <= 0 or rate[1] <= 0):
        raise EditorialMigrationError(
            f"{name} must be an (fps_num, fps_den) pair of positive "
            f"ints, got {rate!r}"
        )
    return (rate[0], rate[1])


def clip_use_from_played_span(
    *,
    role: str,
    clip_id: str,
    asset_id: str,
    source_clock: str,
    source_start_frame: int,
    played_source_frames: int,
    timeline_clock: str,
    timeline_start_frame: int,
    timeline_frames: int,
    source_tps: int,
    source_fps: tuple[int, int],
    timeline_tps: int,
    timeline_fps: tuple[int, int],
) -> dict[str, Any]:
    """One model ``ClipUse`` from frame counts with a derived source end.

    This is the constructor for rebuilding a use from a timeline record
    (a review snapshot, a build placement): the source end is
    ``source_start_frame + played_source_frames`` via
    :func:`interval_from_played_frames`, never the record's raw end
    frame. Identity links stay empty for Unit 3, as in the node
    converters. The role passes through verbatim - the native projection
    refuses unknown roles, so an invented one fails there by name rather
    than here by guess.
    """
    if not isinstance(role, str) or not role:
        raise EditorialMigrationError("a clip use needs a non-empty role")
    if not isinstance(clip_id, str) or not clip_id:
        raise EditorialMigrationError("a clip use needs a non-empty clip_id")
    if not isinstance(asset_id, str) or not asset_id:
        raise EditorialMigrationError("a clip use needs a non-empty asset_id")
    source_num, source_den = _fps_pair("source_fps", source_fps)
    timeline_num, timeline_den = _fps_pair("timeline_fps", timeline_fps)
    source = interval_from_played_frames(
        source_clock, source_start_frame, played_source_frames,
        source_tps, source_num, source_den,
    )
    timeline = interval_from_played_frames(
        timeline_clock, timeline_start_frame, timeline_frames,
        timeline_tps, timeline_num, timeline_den,
    )
    return {
        "id": _clip_use_id(role, clip_id, source, timeline),
        "asset_id": asset_id,
        "source_interval": source,
        "timeline_interval": timeline,
        "entity_links": [],
        "role": role,
        "intent_id": None,
    }


def migrate_edit_graph(
    graph: Mapping[str, Any],
    *,
    asset_by_clip: Mapping[str, str] | None = None,
    source_clock_by_asset: Mapping[str, str] | None = None,
    source_tps_by_asset: Mapping[str, int] | None = None,
    timeline_clock: str | None = None,
    timeline_tps: int | None = None,
    author: str = "ren",
) -> dict[str, Any]:
    """Rebuild a whole edit graph through the adapter.

    Every node becomes one ``EditOperation``. A-roll and B-roll cover
    nodes additionally become ``ClipUse`` records where the asset and both
    clocks resolve; anything else is named in ``skipped_clip_uses``
    rather than guessed. Node types this bridge does not place
    (transitions, graphics, VFX, SFX, grades, mix windows) travel as
    operations only: they name no model-shaped target interval.
    """
    from library.tools.edit_graph import AROLL_ASSIGNMENT, BROLL_COVER

    nodes = graph.get("nodes") if isinstance(graph, Mapping) else None
    if not isinstance(nodes, list):
        raise EditorialMigrationError("an edit graph needs a nodes list")
    edits: list[dict[str, Any]] = []
    clip_uses: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    clocks_ready = (
        asset_by_clip is not None
        and source_clock_by_asset is not None
        and source_tps_by_asset is not None
        and timeline_clock is not None
        and timeline_tps is not None
    )
    for node in nodes:
        if not isinstance(node, Mapping):
            raise EditorialMigrationError("an edit-graph node must be an object")
        edits.append(edit_graph_node_to_operation(node, author=author))
        node_type = node.get("node_type")
        if node_type not in (AROLL_ASSIGNMENT, BROLL_COVER) or not clocks_ready:
            continue
        assert asset_by_clip is not None  # narrowed by clocks_ready
        assert source_clock_by_asset is not None
        assert source_tps_by_asset is not None
        assert timeline_clock is not None
        assert timeline_tps is not None
        converter = (
            aroll_node_to_clip_use
            if node_type == AROLL_ASSIGNMENT
            else broll_node_to_clip_use
        )
        use, reason = converter(
            node,
            asset_by_clip=asset_by_clip,
            source_clock_by_asset=source_clock_by_asset,
            source_tps_by_asset=source_tps_by_asset,
            timeline_clock=timeline_clock,
            timeline_tps=timeline_tps,
        )
        if use is None:
            skipped.append({"node_id": node.get("node_id"), "reason": reason})
        else:
            clip_uses.append(use)
    return {"edits": edits, "clip_uses": clip_uses, "skipped_clip_uses": skipped}


TIMELINE_CLOCK_ID = "clock-project"
"""Timeline clock id for pipeline-minted clip uses.

The migration takes clock ids as caller parameters so it never names
them; the pipeline side that mints move effects MUST name them (a
model interval names its clock), so the names live here, once: the
timeline clock is always this, a source clock is always
``clock-src-<clip_id>`` and an asset is always ``asset-<clip_id>``
(see :func:`clip_uses_from_assignments`). Any future migration caller
passes these same names, which keeps minted target ids equal to
migrated clip-use ids by construction - the same functions hash the
same inputs.
"""


def asset_id_for_clip(clip_id: str) -> str:
    """The model asset id for a pipeline clip id (namespaced, never hashed)."""
    return f"asset-{clip_id}"


def source_clock_for_clip(clip_id: str) -> str:
    """The source clock id for a pipeline clip id."""
    return f"clock-src-{clip_id}"


def clip_uses_from_assignments(
    assignments: Any,
    *,
    timeline_tps: Any,
) -> dict[str, Any]:
    """Migrate A-roll assignments to clip uses for move minting.

    Step 4.03 accepts plan_vfx toolkit entries against spine blocks,
    but the move producer mints against clip uses - so the bridge
    migrates the step's own assignment inputs through
    :func:`aroll_node_to_clip_use` (the same function Unit 4 runs, with
    the :data:`TIMELINE_CLOCK_ID` names above) and mints against the
    result. Minted target ids therefore ARE post-migration stable
    clip-use ids, not a second id scheme.

    ``timeline_tps`` is the run frame rate in ticks per second (the
    bridge derives punch frames on that same grid, so mint intervals
    tick on it too); each clip's source ticks run at its own measured
    ``frame_rate``. B-roll covers are deliberately NOT migrated: moves
    are per-clip Transforms of the V1 picture, and the V1 picture is
    the A-roll assignment.

    Returns ``{"uses_by_block": {str(spine_block_position): clip_use},
    "skipped": [{"spine_block_position", "reason"}],
    "clocks": [{"id", "ticks_per_second"}]}``. A block with no single
    video segment, non-numeric spans or no measured clip frame rate is
    skipped by name - the bridge records the skip beside the unminted
    entry and the Fusion path resolves unchanged. ``clocks`` carries
    every clock id the uses name so a reader can assemble a model
    without re-deriving the grid.
    """
    skipped: list[dict[str, Any]] = []
    if not _is_number(timeline_tps) or float(timeline_tps) <= 0:
        raise EditorialMigrationError(
            f"pipeline clip uses need a positive timeline frame rate, "
            f"got {timeline_tps!r}")
    if int(timeline_tps) != timeline_tps:
        # A fractional run rate (29.97) has no exact integer tick grid,
        # so nothing mints - and the step resolves unchanged. Recorded,
        # never silent.
        return {"uses_by_block": {}, "skipped": [{
            "spine_block_position": None,
            "reason": f"timeline rate {timeline_tps!r} has no integer "
                      f"tick grid; move intervals cannot tick it",
        }], "clocks": []}
    tps = int(timeline_tps)
    if not isinstance(assignments, list):
        return {"uses_by_block": {}, "skipped": [{
            "spine_block_position": None,
            "reason": "a_roll_assignments is not a list; no block maps "
                      "to a clip use",
        }], "clocks": [{"id": TIMELINE_CLOCK_ID,
                         "ticks_per_second": tps}]}
    uses_by_block: dict[str, dict[str, Any]] = {}
    clocks: dict[str, int] = {TIMELINE_CLOCK_ID: tps}
    dupes: set[str] = set()

    def _skip(position: Any, reason: str) -> None:
        skipped.append({"spine_block_position": position,
                        "reason": reason})

    for assignment in assignments:
        if not isinstance(assignment, Mapping):
            _skip(None, "an a-roll assignment must be an object")
            continue
        position = assignment.get("spine_block_position")
        segments = assignment.get("video_segments")
        if not isinstance(segments, list) or not segments:
            _skip(position,
                  f"block {position!r} has no video segment: a move "
                  f"needs exactly one V1 picture to transform")
            continue
        if len(segments) != 1:
            _skip(position,
                  f"block {position!r} has {len(segments)} video "
                  f"segments: a move needs exactly one V1 picture, "
                  f"and picking one would be a guess")
            continue
        segment = segments[0]
        if not isinstance(segment, Mapping):
            _skip(position,
                  f"block {position!r} video segment must be an object")
            continue
        clip_id = segment.get("clip_id")
        if not isinstance(clip_id, str) or not clip_id:
            _skip(position,
                  f"block {position!r} video segment names no clip")
            continue
        clip_fps = segment.get("frame_rate")
        if not _is_number(clip_fps) or float(clip_fps) <= 0:
            _skip(position,
                  f"clip {clip_id!r} has no measured frame rate: "
                  f"source ticks cannot be derived without one")
            continue
        if int(clip_fps) != clip_fps:
            _skip(position,
                  f"clip {clip_id!r} frame rate {clip_fps!r} has no "
                  f"integer tick grid; its block mints nothing")
            continue
        asset_id = asset_id_for_clip(clip_id)
        source_clock = source_clock_for_clip(clip_id)
        node = {"node_id": f"aroll:{position}", "payload": {
            "clip_id": clip_id,
            "source_start": segment.get("source_start"),
            "source_end": segment.get("source_end"),
            "timeline_start": assignment.get("timeline_start"),
            "timeline_end": assignment.get("timeline_end"),
        }}
        use, reason = aroll_node_to_clip_use(
            node,
            asset_by_clip={clip_id: asset_id},
            source_clock_by_asset={asset_id: source_clock},
            source_tps_by_asset={asset_id: int(clip_fps)},
            timeline_clock=TIMELINE_CLOCK_ID,
            timeline_tps=tps,
        )
        if use is None:
            _skip(position, str(reason))
            continue
        key = str(position)
        if key in uses_by_block:
            dupes.add(key)
            _skip(position,
                  f"block {position!r} names two a-roll assignments: "
                  f"a move needs exactly one V1 picture")
            continue
        uses_by_block[key] = use
        clocks[source_clock] = int(clip_fps)
    for key in dupes:
        uses_by_block.pop(key, None)
    return {
        "uses_by_block": uses_by_block,
        "skipped": skipped,
        "clocks": [{"id": clock_id, "ticks_per_second": rate}
                   for clock_id, rate in sorted(clocks.items())],
    }


def migrate_ledger(rows: Any) -> dict[str, Any]:
    """Rebuild supported user edits (ledger rows) through the adapter."""
    if not isinstance(rows, list):
        raise EditorialMigrationError("ledger rows must be a list")
    edits: list[dict[str, Any]] = []
    seen: dict[str, int] = {}
    for index, row in enumerate(rows):
        operation = ledger_row_to_operation(row)
        base = operation["id"]
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count > 1:
            operation["id"] = f"{base}#{count}"
        edits.append(operation)
    return {"edits": edits}


def migrate_patches(patches: Any) -> dict[str, Any]:
    """Rebuild EditPatch records through the adapter."""
    if not isinstance(patches, list):
        raise EditorialMigrationError("patches must be a list")
    return {"edits": [patch_to_operation(patch) for patch in patches]}


def migrate_composed(
    changes: Any, *, from_clock: str, to_clock: str
) -> dict[str, Any]:
    """Rebuild composed-edit records through the adapter.

    Every change becomes an operation; moved items additionally yield one
    frame-map segment each. Insertions yield operations only (see
    ``composed_change_to_segment``).
    """
    if not isinstance(changes, list):
        raise EditorialMigrationError("composed changes must be a list")
    edits: list[dict[str, Any]] = []
    segments: list[dict[str, Any]] = []
    for change in changes:
        edits.append(composed_change_to_operation(change))
        try:
            segments.append(
                composed_change_to_segment(
                    change, from_clock=from_clock, to_clock=to_clock
                )
            )
        except EditorialMigrationError:
            continue
    return {"edits": edits, "segments": segments}


def apply_editorial_fragment(
    model: Mapping[str, Any], fragment: Mapping[str, Any]
) -> dict[str, Any]:
    """Append migrated edits/clip uses onto a model, deduplicated by id.

    Re-applying the same migration is a no-op for rows already present,
    which is what makes rebuilds idempotent. Unknown fragment keys are
    refused; the result is validated against the model contract.
    """
    from library.tools.project_model import validate_project_model

    if not isinstance(model, Mapping):
        raise EditorialMigrationError("a model must be an object")
    if not isinstance(fragment, Mapping):
        raise EditorialMigrationError("a fragment must be an object")
    known = {"edits", "clip_uses"}
    unknown = set(fragment) - known
    if unknown:
        raise EditorialMigrationError(
            f"fragment carries unknown keys {sorted(unknown)}: "
            f"one of {sorted(known)}"
        )
    updated = {key: (list(value) if isinstance(value, list) else value)
               for key, value in model.items()}
    for key in ("edits", "clip_uses"):
        incoming = fragment.get(key, [])
        if not isinstance(incoming, list):
            raise EditorialMigrationError(f"fragment {key!r} must be a list")
        present = {row["id"] for row in updated.get(key, [])
                   if isinstance(row, Mapping) and "id" in row}
        merged = list(updated.get(key, []))
        for row in incoming:
            if not isinstance(row, Mapping) or "id" not in row:
                raise EditorialMigrationError(
                    f"fragment {key!r} rows need an id"
                )
            if row["id"] not in present:
                merged.append(row)
                present.add(row["id"])
        updated[key] = merged
    validate_project_model(updated)
    return updated
