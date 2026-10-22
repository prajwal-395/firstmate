"""VC-08: a first-class edit preparation stage, before the Resolve lease.

Preparation resolves identities, reads Project Model state and the
last generation, validates schemas, computes affected clips, rows,
frames and dependents, detects no-write-satisfiable requests, emits
a machine-readable plan and states unresolved assumptions - all
without taking the Resolve lease and without modifying the timeline.

The VC-08 acceptance is that Ren can explain a proposed edit and its
impact without modifying anything: `prepare` is read-only (snapshot
reads, store reads, algebra spans), and `explain` renders the result
for the originator. A result with `ready` false names its refusals;
a result with `no_write_needed` true says what already satisfies the
request, so the router can take the no-op path instead of staging a
candidate.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from library.tools import edit_algebra, intent_neutrality
from library.tools.edit_request import (
    CompiledEdit,
    EditRequest,
    EditTarget,
    compile_request,
)


class PreparationRefused(ValueError):
    """Preparation cannot proceed: no base, no project state to read."""


# ── Result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedTarget:
    """One op target resolved against the base snapshot (or not)."""

    op_index: int
    op: str
    status: str  # resolved | assumed | missing | ambiguous
    unique_id: str = ""
    name: str = ""
    track: str = ""
    record_span: tuple[int, int] | None = None
    note: str = ""
    #: The further items an operation names (`clip.join`'s other clip,
    #: `clip.link`'s clips, `compound.create`'s children), when the plan
    #: named them by their addresses.
    extra_ids: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "op_index": self.op_index,
            "op": self.op,
            "status": self.status,
            "unique_id": self.unique_id,
            "name": self.name,
            "track": self.track,
            "record_span": (list(self.record_span)
                            if self.record_span else None),
            "note": self.note,
            "extra_ids": dict(self.extra_ids),
        }


@dataclass(frozen=True)
class AffectedScope:
    """What the compiled edit reaches: direct targets plus dependents."""

    clips: tuple[str, ...] = ()
    rows: tuple[str, ...] = ()
    spans: tuple[tuple[int, int], ...] = ()
    dependents: tuple[str, ...] = ()
    marker_frames: tuple[int, ...] = ()

    def as_dict(self) -> dict:
        return {
            "clips": list(self.clips),
            "rows": list(self.rows),
            "spans": [list(span) for span in self.spans],
            "dependents": list(self.dependents),
            "marker_frames": list(self.marker_frames),
        }


@dataclass(frozen=True)
class NoWriteFinding:
    """Whether the request is already satisfied by recorded state."""

    needed: bool
    reason: str = ""
    per_op: tuple[dict, ...] = ()

    def as_dict(self) -> dict:
        return {"needed": self.needed, "reason": self.reason,
                "per_op": [dict(row) for row in self.per_op]}


@dataclass(frozen=True)
class PreparationResult:
    """Everything known about a request before the lease is taken."""

    request_id: str
    project: str
    timeline: str
    base_generation: int | None
    model_revision: int | None
    resolved: tuple[ResolvedTarget, ...] = ()
    affected: AffectedScope = field(default_factory=AffectedScope)
    no_write: NoWriteFinding = field(
        default_factory=lambda: NoWriteFinding(needed=True))
    plan: dict = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()
    refusals: tuple[str, ...] = ()
    backend_candidates: tuple[dict, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.refusals

    @property
    def no_write_needed(self) -> bool:
        return not self.no_write.needed

    def as_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "project": self.project,
            "timeline": self.timeline,
            "base_generation": self.base_generation,
            "model_revision": self.model_revision,
            "ready": self.ready,
            "no_write_needed": self.no_write_needed,
            "resolved": [row.as_dict() for row in self.resolved],
            "affected": self.affected.as_dict(),
            "no_write": self.no_write.as_dict(),
            "plan": copy.deepcopy(self.plan),
            "assumptions": list(self.assumptions),
            "refusals": list(self.refusals),
            "backend_candidates": [dict(row)
                                   for row in self.backend_candidates],
        }


# ── Reading state, never writing it ───────────────────────────────────


def _read_base_snapshot(project_folder: str | None,
                        stored: Mapping[str, Any] | None,
                        project: str, timeline: str,
                        base_generation: int | None,
                        timeline_id: str = ""
                        ) -> tuple[dict | None, int | None, list[str]]:
    """The snapshot the request is planned against, with its generation."""
    assumptions: list[str] = []
    if stored is not None:
        if not isinstance(stored, Mapping):
            raise PreparationRefused("a base snapshot must be a mapping")
        return dict(stored), base_generation, assumptions
    if not project_folder:
        return None, None, [(
            "no base snapshot and no project folder - preparation "
            "cannot resolve identities or compute affected scope")]
    from library.tools import edit_spec, timeline_shadow

    try:
        project_name = edit_spec._resolve_project_name(project_folder)
    except Exception as exc:
        raise PreparationRefused(
            f"cannot resolve the Resolve project for {project_folder!r} "
            f"({exc})") from exc
    store = timeline_shadow.ShadowStore()
    try:
        # VC-001: the request's own RESOLVE IDENTITY picks the timeline
        # when it carries one; the name is an assertion about it. A
        # request without an identity resolves by name, and the shadow
        # still refuses when two of its timelines share that name.
        timeline_id = timeline_id or store.resolve_timeline(
            project_name, timeline)
    except Exception as exc:
        raise PreparationRefused(
            f"timeline {timeline!r} has no recorded identity "
            f"({exc})") from exc
    if base_generation is None:
        head = store.head(project_name, timeline_id)
        if head is None:
            raise PreparationRefused(
                f"timeline {timeline!r} has no recorded generation - "
                "open it in Resolve and observe it before preparing an edit")
        base_generation = head.generation
    else:
        try:
            store.get(project_name, timeline_id, int(base_generation))
        except Exception as exc:
            raise PreparationRefused(
                f"generation {base_generation} of {timeline!r} is not "
                f"recorded ({exc})") from exc
    snapshot = store.snapshot(store.get(project_name, timeline_id,
                                        int(base_generation)))
    stale_note = ""
    head = store.head(project_name, timeline_id)
    if head is not None and head.generation != int(base_generation):
        stale_note = (
            f"planned against generation {base_generation} but the head "
            f"is {head.generation} - the router must rebase or refuse")
        assumptions.append(stale_note)
    return snapshot, int(base_generation), assumptions


def _read_model_revision(project_folder: str | None,
                         pinned: int | None) -> tuple[int | None, list[str]]:
    """The Project Model head revision, read-only; absence is assumed."""
    if pinned is not None:
        return int(pinned), []
    if not project_folder:
        return None, [("no Project Model revision pinned and no project "
                        "folder to read one from")]
    from library.tools import project_model_store

    try:
        revision, _model = project_model_store.load_project_model(
            Path(project_folder))
    except Exception:  # noqa: BLE001 - an absent model file is an assumption
        return None, [("no Project Model recorded for this project - "
                       "edits proceed on timeline generations alone")]
    return int(revision), []


def _snapshot_clips(base: Mapping[str, Any]) -> list[dict]:
    from library.tools import timeline_shadow as shadow

    try:
        return list(shadow.clips(base))
    except Exception:  # noqa: BLE001 - fall back to raw clips on odd shapes
        return [clip for clip in (base.get("clips") or ())
                if isinstance(clip, Mapping)]


def _resolve_targets(request: EditRequest, base: Mapping[str, Any] | None
                     ) -> tuple[list[ResolvedTarget], list[str], list[str]]:
    """Match every op target against the base snapshot (read-only)."""
    resolved: list[ResolvedTarget] = []
    assumptions: list[str] = []
    refusals: list[str] = []
    clips = _snapshot_clips(base) if base is not None else []
    by_id = {str(c.get("unique_id")): c for c in clips if c.get("unique_id")}
    for index, edit_op in enumerate(request.operations):
        spec = edit_algebra.get(edit_op.op)
        target = edit_op.target
        if target is None:
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="resolved",
                note="no target: track/timeline-wide operation"))
            continue
        if base is None:
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="assumed",
                unique_id=target.unique_id, name=target.name,
                note="no base snapshot - identity unresolved until one "
                     "is read"))
            assumptions.append(
                f"op {index} ({edit_op.op}): target "
                f"{target.unique_id or target.name or '?'} unresolved - "
                "no base snapshot was read")
            continue
        if target.unique_id and target.unique_id in by_id:
            clip = by_id[target.unique_id]
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="resolved",
                unique_id=target.unique_id, name=str(clip.get("name") or ""),
                track=_row_of(clip),
                record_span=_record_span(clip)))
            continue
        if target.unique_id:
            refusals.append(
                f"op {index} ({edit_op.op}): clip "
                f"{target.unique_id!r} is not on the recorded timeline")
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="missing",
                unique_id=target.unique_id,
                note="not on the recorded timeline"))
            continue
        if target.has_address:
            # An ADDRESSED target: the model-facing plan names the
            # material it plays (or the placement it occupies), never
            # the instance - the instance is the binding's business.
            # The recorded timeline says which instance of that address
            # the reel holds today; more than one match is AMBIGUOUS and
            # is refused rather than guessed.
            matches = intent_neutrality.instances_for_address(
                base, _address_of(target))
            if len(matches) == 1:
                clip = by_id.get(matches[0]) or {}
                resolved.append(ResolvedTarget(
                    op_index=index, op=edit_op.op, status="resolved",
                    unique_id=matches[0],
                    name=str(clip.get("name") or ""),
                    track=_row_of(clip),
                    record_span=_record_span(clip),
                    note="resolved by address - the plan named the "
                         "material and the row, not the instance"))
                continue
            if not matches:
                refusals.append(
                    f"op {index} ({edit_op.op}): no recorded item plays "
                    f"the address the plan named")
                resolved.append(ResolvedTarget(
                    op_index=index, op=edit_op.op, status="missing",
                    name=target.name,
                    note="no recorded item plays the address the plan "
                         "named"))
                continue
            refusals.append(
                f"op {index} ({edit_op.op}): {len(matches)} recorded items "
                f"play the address the plan named - the plan must say "
                f"which one")
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="ambiguous",
                name=target.name,
                note=f"{len(matches)} recorded items play the address"))
            continue
        # No unique id: match by name on the named track, else by
        # track position, else ambiguous or missing.
        named = [c for c in clips
                 if target.name and str(c.get("name") or "") == target.name
                 and (not target.track_type
                      or str(c.get("track_type") or "") == target.track_type)
                 and (target.track_index is None
                      or int(c.get("track_index") or -1)
                      == target.track_index)]
        if len(named) == 1:
            clip = named[0]
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="assumed",
                unique_id=str(clip.get("unique_id") or ""),
                name=str(clip.get("name") or ""), track=_row_of(clip),
                record_span=_record_span(clip),
                note="matched by name and track - a rename since the "
                     "request was written would misresolve this"))
            assumptions.append(
                f"op {index} ({edit_op.op}): target matched by name "
                f"{target.name!r}, not by stable identity")
        elif len(named) > 1:
            refusals.append(
                f"op {index} ({edit_op.op}): name {target.name!r} "
                f"matches {len(named)} clips - name the stable identity")
            resolved.append(ResolvedTarget(
                op_index=index, op=edit_op.op, status="ambiguous",
                name=target.name, note=f"{len(named)} clips share the name"))
        elif not _positional_match(
                index, edit_op, target, clips, resolved, assumptions,
                refusals):
            _refuse_unresolved(index, edit_op, spec, target, resolved,
                               refusals)
    _resolve_second_targets(request, base, resolved, refusals)
    return resolved, assumptions, refusals


def _resolve_second_targets(request: EditRequest, base: Mapping[str, Any]
                             | None, resolved: list[ResolvedTarget],
                             refusals: list[str]) -> None:
    """Resolve the FURTHER items an operation names, by their addresses.

    `clip.join` names two clips, `clip.link` names the clips it links,
    `compound.create` names its children.  The model-facing plan names
    those items the way it names the first one - by the material they
    play - so the binding fills the instances the operation needs.
    """
    from dataclasses import replace

    if base is None:
        return
    for index, edit_op in enumerate(request.operations):
        params = edit_op.params or {}
        position = next((row for row, item in enumerate(resolved)
                         if item.op_index == index), None)
        if position is None:
            continue
        extra: dict[str, list[str]] = {}
        for param, key in intent_neutrality.ADDRESSED_MULTI_TARGET_PARAMS.items():
            entry = params.get(key)
            if entry is None:
                continue
            entries = ([entry] if isinstance(entry, Mapping) else list(entry))
            ids: list[str] = []
            for one in entries:
                if not isinstance(one, Mapping):
                    ids = []
                    break
                address = intent_neutrality.address_of(one)
                if not address["material"] and address["record_in"] is None:
                    ids = []
                    break
                matches = intent_neutrality.instances_for_address(base, address)
                if len(matches) != 1:
                    ids = []
                    break
                ids.append(matches[0])
            if not ids:
                refusals.append(
                    f"op {index} ({edit_op.op}): the items it names under "
                    f"{key!r} are not one recorded item each - the plan "
                    f"must say which ones")
                continue
            extra[param] = ids
        if extra:
            resolved[position] = replace(resolved[position],
                                         extra_ids=extra)


def _positional_match(index: int, edit_op, target,
                      clips: list, resolved: list, assumptions: list,
                      refusals: list) -> bool:
    """Resolve a track-position target to the clip living there.

    Touch-up `row`/`item` addressing is positional: the nth clip on
    the named track in snapshot order. Returns True when the op was
    handled (resolved or refused); False when the target states no
    position and the caller must fall through.
    """
    if target.position is None:
        return False
    if not target.track_type or target.track_index is None:
        refusals.append(
            f"op {index} ({edit_op.op}): position {target.position} "
            "names no track - positional addressing needs the row")
        resolved.append(ResolvedTarget(
            op_index=index, op=edit_op.op, status="missing",
            name=target.name, note="position without a track"))
        return True
    row = [clip for clip in clips
           if str(clip.get("track_type") or "") == target.track_type
           and int(clip.get("track_index") or -1) == target.track_index]
    if 0 <= target.position < len(row):
        clip = row[target.position]
        resolved.append(ResolvedTarget(
            op_index=index, op=edit_op.op, status="assumed",
            unique_id=str(clip.get("unique_id") or ""),
            name=str(clip.get("name") or ""), track=_row_of(clip),
            record_span=_record_span(clip),
            note="matched by track position - a reorder since the "
                 "request was written would misresolve this"))
        assumptions.append(
            f"op {index} ({edit_op.op}): target matched by position "
            f"{target.position} on {target.track_type}"
            f"{target.track_index}, not by stable identity")
        return True
    refusals.append(
        f"op {index} ({edit_op.op}): position {target.position} is "
        f"outside {target.track_type}{target.track_index}, which "
        f"carries {len(row)} clip(s)")
    resolved.append(ResolvedTarget(
        op_index=index, op=edit_op.op, status="missing",
        name=target.name, note="position outside the row"))
    return True


def _refuse_unresolved(index: int, edit_op, spec, target,
                       resolved: list, refusals: list) -> None:
    """No stable identity and no match: refuse item ops, else pass."""
    if spec is not None and spec.item:
        refusals.append(
            f"op {index} ({edit_op.op}): names no resolvable "
            "item - an in-place change without a target is not "
            "plannable")
        resolved.append(ResolvedTarget(
            op_index=index, op=edit_op.op, status="missing",
            name=target.name, note="no stable identity and no match"))
    else:
        resolved.append(ResolvedTarget(
            op_index=index, op=edit_op.op, status="resolved",
            name=target.name,
            note="no target needed: track/timeline-wide operation"))


def _address_of(target: EditTarget) -> dict:
    """The neutral address one target states, in the resolver's shape."""
    return intent_neutrality.address_of({
        "source_file": (target.material[0] if target.material else ""),
        "source_in_frame": (target.material[1] if target.material else None),
        "source_out_frame": (target.material[2] if target.material else None),
        "track_type": target.track_type,
        "track_index": target.track_index,
        "record_in": target.record_in,
        "record_out": target.record_out,
    })


def _row_of(clip: Mapping[str, Any]) -> str:
    track_type = str(clip.get("track_type") or "")
    prefix = "V" if track_type.lower().startswith("v") else (
        "A" if track_type.lower().startswith("a") else track_type[:1].upper())
    return f"{prefix}{clip.get('track_index')}"


def _record_span(clip: Mapping[str, Any]) -> tuple[int, int] | None:
    try:
        return (int(clip["record_in"]), int(clip["record_out"]))
    except (KeyError, TypeError, ValueError):
        return None


def _bind_request(request: EditRequest,
                  resolved: Sequence[ResolvedTarget]
                  ) -> tuple[EditRequest | None, list[str]]:
    """Fill resolved identities into a plannable copy of the request.

    The request itself is never rewritten: the bound copy is
    preparation's derivative for span computation, no-write detection
    and the machine plan. Every substitution is recorded as an
    assumption, so a backend evaluating the bound ops reads the same
    meaning with its identities made explicit.
    """
    from dataclasses import replace

    by_index = {row.op_index: row for row in resolved}
    ops: list = []
    notes: list[str] = []
    changed = False
    for index, edit_op in enumerate(request.operations):
        row = by_index.get(index)
        merged = edit_op.to_algebra_op()
        params = dict(edit_op.params)
        for key, value in sorted((row.extra_ids if row is not None else {})
                                 .items()):
            if merged.get(key):
                continue
            # A parameter that names ONE item takes one id; a parameter
            # that names MANY takes the list.
            filled = (list(value) if key.endswith("_ids") else value[0])
            params[key] = filled
            # The ADDRESS the plan named is the plan's own vocabulary:
            # the bound request speaks the instance the backend executes.
            params.pop(intent_neutrality
                       .ADDRESSED_MULTI_TARGET_PARAMS.get(key, ""), None)
            notes.append(
                f"op {index} ({edit_op.op}): {key} {filled!r} filled from "
                f"the addresses the plan named")
            changed = True
        if (row is not None and row.unique_id
                and not merged.get("unique_id")):
            target = edit_op.target
            target = (replace(target, unique_id=row.unique_id)
                      if target is not None
                      else EditTarget(unique_id=row.unique_id))
            ops.append(replace(edit_op, target=target, params=params))
            notes.append(
                f"op {index} ({edit_op.op}): unique_id "
                f"{row.unique_id!r} filled from {row.status} resolution")
            changed = True
        elif params != edit_op.params:
            ops.append(replace(edit_op, params=params))
        else:
            ops.append(edit_op)
    if not changed:
        return None, []
    try:
        return (EditRequest(
            id=request.id, project=request.project,
            timeline=request.timeline, operations=tuple(ops),
            timeline_id=request.timeline_id,
            expected_base_generation=request.expected_base_generation,
            expected_model_revision=request.expected_model_revision,
            originator=dict(request.originator),
            preconditions=tuple(dict(c) for c in request.preconditions),
            idempotency_key=request.idempotency_key,
            version=request.version), notes)
    except Exception as exc:  # noqa: BLE001 - a failed bind is an assumption
        return None, [f"bound request invalid, planning as-authored: {exc}"]


def bind_request(request: EditRequest,
                 resolved: Sequence[ResolvedTarget]
                 ) -> tuple[EditRequest | None, list[str]]:
    """The request with every resolved identity filled in - the BINDING.

    A model-facing plan names the MATERIAL it acts on; the backends
    execute an INSTANCE.  This is the one place the two meet: the
    request the router sees carries the instance the recorded timeline
    holds for the material the plan named, so the route a request takes
    does not depend on how the plan addressed its target.

    The request itself is never rewritten - the bound copy is a
    derivative, and every substitution is recorded as an assumption.
    """
    return _bind_request(request, resolved)


def _validate_conditions(request: EditRequest) -> list[str]:
    """Request-level schema checks the constructor cannot see."""
    from library.tools import edit_patch as _patch

    refusals: list[str] = []
    for cond in list(request.preconditions):
        spec = _patch.CONDITIONS.get(
            cond.get("kind") if isinstance(cond, Mapping) else None)
        if spec is None:
            refusals.append(f"unknown precondition {cond!r}")
            continue
        if any(k not in cond for k in spec[0]):
            refusals.append(
                f"precondition {cond!r} is missing keys {list(spec[0])}")
    for index, edit_op in enumerate(request.operations):
        spec = edit_algebra.get(edit_op.op)
        if spec is not None and spec.item and (
                edit_op.target is None or (not edit_op.target.unique_id
                                           and not edit_op.target.name
                                           and not edit_op.target.has_address
                                           and edit_op.target.position
                                           is None)):
            refusals.append(
                f"op {index} ({edit_op.op}): targets an existing item "
                "but names no unique_id")
        for cond in list(edit_op.preconditions) + list(edit_op.postconditions):
            if not isinstance(cond, Mapping) or \
                    _patch.CONDITIONS.get(cond.get("kind")) is None:
                refusals.append(
                    f"op {index} ({edit_op.op}): unknown condition "
                    f"{cond!r}")
    return refusals


def _compute_affected(compiled: CompiledEdit,
                      resolved: Sequence[ResolvedTarget],
                      base: Mapping[str, Any] | None) -> AffectedScope:
    spans = list(compiled.affected_spans)
    direct = {row.unique_id for row in resolved if row.unique_id}
    rows = sorted({row.track for row in resolved if row.track})
    dependents: list[str] = []
    marker_frames: list[int] = []
    if base is not None:
        for clip in _snapshot_clips(base):
            uid = str(clip.get("unique_id") or "")
            if not uid or uid in direct:
                continue
            span = _record_span(clip)
            if span is None:
                continue
            if any(span[0] < end and start < span[1] for start, end in spans):
                dependents.append(uid)
        marker_frames.extend(_marker_frames_in(base, spans))
    return AffectedScope(
        clips=tuple(sorted(direct)),
        rows=tuple(rows),
        spans=tuple(spans),
        dependents=tuple(sorted(set(dependents))),
        marker_frames=tuple(sorted(set(marker_frames))),
    )


def _marker_frames_in(base: Mapping[str, Any],
                      spans: Sequence[tuple[int, int]]) -> list[int]:
    frames: list[int] = []
    markers = base.get("markers") or {}
    for marker in (markers.get("timeline") or ()):
        try:
            frame = int(marker.get("frame"))
        except (TypeError, ValueError, AttributeError):
            continue
        if any(start <= frame < end for start, end in spans):
            frames.append(frame)
    return frames


def _check_no_write(compiled: CompiledEdit,
                    resolved: Sequence[ResolvedTarget],
                    base: Mapping[str, Any] | None) -> NoWriteFinding:
    """Whether recorded state already satisfies every op (read-only)."""
    if base is None:
        return NoWriteFinding(needed=True,
                              reason="no base snapshot read - cannot tell "
                                     "whether the request is satisfied")
    by_index = {row.op_index: row for row in resolved}
    clips = {str(c.get("unique_id")): c
             for c in _snapshot_clips(base) if c.get("unique_id")}
    per_op: list[dict] = []
    for position, op in enumerate(compiled.operations):
        index = compiled.order[position] if compiled.order else position
        row = by_index.get(index)
        finding = _op_already_satisfied(op, row, clips, base)
        per_op.append({"op_index": index, "op": str(op.get("op", "")),
                       "satisfied": finding[0], "reason": finding[1]})
    if all(row["satisfied"] for row in per_op):
        return NoWriteFinding(
            needed=False,
            reason=("recorded state already satisfies every op - no "
                    "Resolve write is needed"),
            per_op=tuple(per_op))
    return NoWriteFinding(
        needed=True,
        reason="; ".join(
            f"op {row['op_index']} ({row['op']}): {row['reason']}"
            for row in per_op if not row["satisfied"]) or "changes outstanding",
        per_op=tuple(per_op))


def _op_already_satisfied(op: Mapping[str, Any],
                          resolved: ResolvedTarget | None,
                          clips: Mapping[str, Mapping[str, Any]],
                          base: Mapping[str, Any]) -> tuple[bool, str]:
    """One op judged against recorded state; unknown judges as unsatisfied."""
    op_id = str(op.get("op", ""))
    uid = str(op.get("unique_id") or "")
    clip = clips.get(uid) if uid else None
    if op_id == "clip.set_property":
        if clip is None:
            return False, "target is not on the recorded timeline"
        key, want = op.get("key"), op.get("value")
        got = (clip.get("transform") or {}).get(key, clip.get(key))
        if got == want or (isinstance(got, float) and isinstance(want, float)
                           and abs(got - want) <= 1e-6):
            return True, f"{key} already reads {got!r}"
        return False, f"{key} reads {got!r}, wants {want!r}"
    if op_id == "clip.set_enabled":
        if clip is None:
            return False, "target is not on the recorded timeline"
        want = bool(op.get("enabled", op.get("value", True)))
        got = bool(clip.get("enabled", True))
        if got == want:
            return True, f"enabled already reads {got}"
        return False, f"enabled reads {got}, wants {want}"
    if op_id == "marker.add":
        try:
            frame = int(op["frame"])
        except (KeyError, TypeError, ValueError):
            return False, "no frame to judge"
        markers = (base.get("markers") or {}).get("timeline") or ()
        if any(int(m.get("frame", -1)) == frame for m in markers
               if isinstance(m, Mapping)):
            return True, f"a timeline marker is already at frame {frame}"
        return False, f"no timeline marker at frame {frame}"
    if op_id == "marker.delete":
        try:
            frame = int(op["frame"])
        except (KeyError, TypeError, ValueError):
            return False, "no frame to judge"
        markers = (base.get("markers") or {}).get("timeline") or ()
        if not any(int(m.get("frame", -1)) == frame for m in markers
                   if isinstance(m, Mapping)):
            return True, f"no timeline marker at frame {frame} to delete"
        return False, f"a timeline marker is at frame {frame}"
    return False, "no recorded-state judgement for this op - it must run"


def _backend_candidates(compiled: CompiledEdit) -> list[dict]:
    """Which backends list these ops as compilable (annotated, not chosen)."""
    backends: set[str] = set()
    for op in compiled.operations:
        entry = edit_algebra.status(str(op.get("op", "")))
        if entry is None:
            continue
        backends.update(row.backend for row in entry.backends)
    return [compiled.for_backend(backend) for backend in sorted(backends)]


def _machine_plan(request: EditRequest, compiled: CompiledEdit,
                  resolved: Sequence[ResolvedTarget],
                  affected: AffectedScope, no_write: NoWriteFinding,
                  base_generation: int | None,
                  model_revision: int | None,
                  refs: Mapping[str, intent_neutrality.ItemRef] | None = None
                  ) -> dict:
    """The machine-readable plan: ordered ops, scope, verification.

    INFRA-004: the PLAN states what will happen, and it states it in
    terms no NLE owns.  A step names the MATERIAL it acts on (the
    source file and range, the row, the frames), not the Resolve
    instance that material happens to sit on today.  The instance
    handles the executor needs live beside the plan, in
    `PreparationResult.resolved` - the resolution evidence - and never
    inside the plan itself.

    A target the recorded timeline cannot state neutrally is a
    CAPABILITY MISMATCH, and the plan names it in
    `capability_mismatches` rather than dropping it.
    """
    resolver = dict(refs or {})
    mismatches: list[dict] = []
    steps = []
    by_index = {row.op_index: row for row in resolved}
    target_ids = {str(row.unique_id) for row in resolved if row.unique_id}
    for position, op in enumerate(compiled.operations):
        index = compiled.order[position] if compiled.order else position
        row = by_index.get(index)
        spec = edit_algebra.get(str(op.get("op", "")))
        params = {k: v for k, v in op.items() if k != "op"}
        target = (row.as_dict() if row is not None else None)
        if target is not None:
            unique_id = str(target.get("unique_id") or "")
            ref = resolver.get(unique_id)
            target = {k: v for k, v in target.items() if k != "unique_id"}
            if ref is not None:
                target["item_ref"] = ref.to_dict()
            elif unique_id:
                mismatches.append({
                    "kind": "unresolved_target",
                    "op": str(op.get("op", "")),
                    "op_index": index,
                    "field": "unique_id",
                    "detail": (
                        f"operation[{index}] ({op.get('op')}) names its "
                        f"target by a Resolve object id and the material it "
                        f"stands for was not recorded, so the plan cannot "
                        f"state the target without naming the instance")})
        for key in list(params):
            if (key in intent_neutrality.RESOLVE_SPECIFIC_FIELDS
                    and intent_neutrality._is_identifier(params[key])):
                if str(params[key]) in target_ids:
                    # The TARGET, named in the flat op's parameters.  The
                    # neutral target already carries the address, so the
                    # plan says it once and in neutral terms.
                    params.pop(key)
                    continue
                mismatches.append({
                    "kind": "instance_only_operation",
                    "op": str(op.get("op", "")),
                    "op_index": index,
                    "field": key,
                    "detail": (
                        f"operation[{index}] ({op.get('op')}) carries "
                        f"{key!r}, which names a Resolve object; the "
                        f"operation's semantics need the instance, so the "
                        f"backend mints it")})
                params.pop(key)
        steps.append({
            "position": position,
            "request_index": index,
            "op": str(op.get("op", "")),
            "kind": (compiled.kinds[position]
                     if position < len(compiled.kinds) else "direct"),
            "target": target,
            "params": params,
            "verification": (spec.verify.__name__
                             if spec is not None and spec.verify is not None
                             else "missing"),
        })
    neutral = intent_neutrality.neutralize(request, refs=resolver)
    return {
        "request_id": request.id,
        "originator": copy.deepcopy(request.originator),
        "project": request.project,
        "timeline": request.timeline,
        "expected_base_generation": (request.expected_base_generation
                                     if request.expected_base_generation
                                     is not None else base_generation),
        "model_revision": model_revision,
        "is_mixed": request.is_mixed,
        "has_plan_changes": request.has_plan_changes,
        "order": list(compiled.order),
        "steps": steps,
        "affected": _neutral_affected(affected, resolver),
        "no_write": no_write.as_dict(),
        "capability_mismatches": mismatches,
        "neutral_intent": neutral.to_dict(),
        "verification": "every op is judged by algebra read-back on "
                        "the post-write snapshot, never by a return value",
    }


def _neutral_affected(affected: AffectedScope,
                      resolver: Mapping[str, intent_neutrality.ItemRef]
                      ) -> dict:
    """The affected scope, with each clip named by its material.

    The scope is part of the plan, so it names WHAT is affected - the
    material - and not which instance of it the recorded timeline
    happens to hold.
    """
    scope = affected.as_dict()
    scope["clips"] = [_neutral_clip_key(clip, resolver)
                      for clip in affected.clips]
    scope["dependents"] = [_neutral_clip_key(clip, resolver)
                           for clip in affected.dependents]
    return scope


def _neutral_clip_key(unique_id: str,
                      resolver: Mapping[str, intent_neutrality.ItemRef]
                      ) -> str:
    """One clip's neutral name: its material, or its name when unnamed."""
    ref = resolver.get(str(unique_id))
    if ref is None:
        return str(unique_id)
    return ref.key


# ── The stage ─────────────────────────────────────────────────────────


def prepare(request: EditRequest | Mapping[str, Any], *,
            project_folder: str | None = None,
            base_snapshot: Mapping[str, Any] | None = None,
            base_generation: int | None = None,
            model_revision: int | None = None) -> PreparationResult:
    """Resolve, read, validate, scope and plan - before the Resolve lease.

    Read-only: snapshot and store reads plus algebra spans. Never
    contacts Resolve, never takes the lease, never writes. `refusals`
    non-empty means fail-closed (`ready` false) with the reason named.
    """
    if not isinstance(request, EditRequest):
        request = EditRequest.from_dict(request)
    if base_generation is None:
        base_generation = request.expected_base_generation
    base, generation, assumptions = _read_base_snapshot(
        project_folder, base_snapshot, request.project, request.timeline,
        base_generation, str(request.timeline_id or ""))
    model, model_notes = _read_model_revision(project_folder,
                                               model_revision
                                               if model_revision is not None
                                               else request.expected_model_revision)
    assumptions.extend(model_notes)
    refusals: list[str] = list(_validate_conditions(request))
    resolved, resolve_notes, resolve_refusals = _resolve_targets(
        request, base)
    assumptions.extend(resolve_notes)
    refusals.extend(resolve_refusals)
    if base is None:
        refusals.append("no base snapshot to plan against")
    bound, bind_notes = _bind_request(request, resolved)
    assumptions.extend(bind_notes)
    plannable = bound if bound is not None else request
    try:
        compiled = compile_request(plannable, base)
    except Exception as exc:  # noqa: BLE001 - a failed compile is a refusal
        refusals.append(f"request does not compile: {exc}")
        compiled = compile_request(plannable, None)
    assumptions.extend(compiled.assumptions)
    affected = _compute_affected(compiled, resolved, base)
    no_write = _check_no_write(compiled, resolved, base)
    candidates = _backend_candidates(compiled)
    refs = (intent_neutrality.refs_from_snapshot(base)
            if base is not None else {})
    plan = _machine_plan(request, compiled, resolved, affected, no_write,
                         generation, model, refs=refs)
    return PreparationResult(
        request_id=request.id,
        project=request.project,
        timeline=request.timeline,
        base_generation=generation,
        model_revision=model,
        resolved=tuple(resolved),
        affected=affected,
        no_write=no_write,
        plan=plan,
        assumptions=tuple(assumptions),
        refusals=tuple(refusals),
        backend_candidates=tuple(candidates),
    )


def explain(result: PreparationResult) -> str:
    """The proposed edit and its impact, in words, changing nothing."""
    lines = [(f"request {result.request_id} on "
              f"{result.project}/{result.timeline} "
              f"(base generation {result.base_generation})")]
    if result.refusals:
        lines.append("REFUSED - nothing would run:")
        lines.extend(f"  - {refusal}" for refusal in result.refusals)
        return "\n".join(lines)
    steps = result.plan.get("steps", [])
    lines.append(f"{len(steps)} operation(s), in order:")
    for step in steps:
        target = step.get("target") or {}
        ref = target.get("item_ref") or {}
        who = (ref.get("source_file") or target.get("name")
               or "the timeline")
        lines.append(f"  {step['position']}. {step['op']} on {who} "
                     f"[{step.get('kind')}]")
    mismatches = result.plan.get("capability_mismatches") or []
    if mismatches:
        lines.append(f"{len(mismatches)} capability mismatch(es):")
        for row in mismatches:
            lines.append(f"  - {row.get('kind')}: {row.get('detail')}")
    affected = result.affected
    lines.append(
        f"affected: {len(affected.clips)} clip(s), "
        f"{len(affected.dependents)} dependent(s), rows "
        f"{', '.join(affected.rows) or 'none'}, spans "
        f"{['-'.join(map(str, s)) for s in affected.spans] or 'none'}")
    if affected.marker_frames:
        lines.append(f"markers inside the spans: {affected.marker_frames}")
    if result.no_write_needed:
        lines.append(f"NO WRITE NEEDED: {result.no_write.reason}")
    else:
        lines.append(f"write needed: {result.no_write.reason}")
    if result.assumptions:
        lines.append("unresolved assumptions:")
        lines.extend(f"  - {note}" for note in result.assumptions)
    return "\n".join(lines)
