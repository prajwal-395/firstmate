"""VC-07: one typed edit-request contract for every timeline edit.

Ren's backends (EditPatch direct, staged incremental, staged full
rebuild, AXI dispatches, touch-up, revise patch path) each grew their
own operation vocabulary. This module owns the ONE structured intent
those paths compile from: stable target identities from the addressing
layer, semantic algebra ops, params, expected effects, spans, the base
revision the request was planned against, preconditions, the
originator, and whether each op is a plan change or a direct mutation.

A request names meaning, never a physical method. Mixed requests (plan
changes beside direct mutations) compile to ordered ops with
dependencies. Every backend evaluates the same compiled op list
without changing it - `for_backend` only annotates qualification, so
the VC-07 acceptance (same request, different backends, same meaning)
is a property of the type, checked in
`tests/contracts/test_edit_request.py`.

`ren revise`, touch-up specs, edit_spec programs and AXI operation
lists all construct this request (`from_edit_program`,
`from_touchup_spec`, `from_axi_operations`) rather than growing a
parallel type. The execution router (`execution_router`) and the
receipt schema (`transaction_receipt`) are consumed, never
reimplemented here.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from library.tools import edit_algebra, timeline_addressing

#: Contract version. Bumped only with a migration that reads older ones.
REQUEST_VERSION = 1

#: Per-op kinds. A plan change rewrites a declaration and needs a
#: rebuild-shaped path; a direct mutation writes the live timeline in
#: place. One request may carry both; `compile` orders them.
DIRECT = "direct"
PLAN_CHANGE = "plan_change"
KINDS = (DIRECT, PLAN_CHANGE)

#: Originator kinds: who asked for the edit.
ORIGINATOR_KINDS = ("agent", "instruction", "human", "pipeline")


class EditRequestError(ValueError):
    """A request that refuses: unknown op, bad target, bad dependency."""


def _request_id() -> str:
    return "edit_req_" + uuid.uuid4().hex[:16]


# ── Targets: stable identities, never live handles ───────────────────


@dataclass(frozen=True)
class EditTarget:
    """Who an op acts on, in a form that survives a rebuild.

    `editorial_identity` is the addressing layer's stable tuple
    (`source_file`, `source_in_frame`, `source_out_frame`, `role`) and
    is the identity two backends compare. `resolve_item_id` is a live
    handle hint for the fast path only - never part of the identity.
    `unique_id` is the shadow snapshot's item id. An empty
    `editorial_identity` with a `unique_id` is addressable but not
    stable across rebuilds; preparation reports that as an assumption.
    """

    unique_id: str = ""
    resolve_item_id: str = ""
    editorial_identity: tuple = ()
    track_type: str = ""
    track_index: int | None = None
    #: Ordinal on the addressed track (touch-up `row`/`item` addressing).
    #: Positional, never stable: a reorder since the request was written
    #: misresolves it, so preparation records an assumption wherever one
    #: is resolved.
    position: int | None = None
    name: str = ""

    @classmethod
    def from_selector(cls, selector: Mapping[str, Any]) -> EditTarget:
        """Build from a semantic selector (addressing-layer shape)."""
        if not isinstance(selector, Mapping):
            raise EditRequestError(
                "an edit target selector must be a mapping, "
                f"got {type(selector).__name__}")
        identity: tuple = ()
        if "source_file" in selector:
            try:
                identity = timeline_addressing.editorial_identity(selector)
            except ValueError as exc:
                raise EditRequestError(str(exc)) from exc
        track_index = selector.get("track_index")
        if track_index is not None:
            try:
                track_index = int(track_index)
            except (TypeError, ValueError) as exc:
                raise EditRequestError(
                    f"track_index {track_index!r} is not an integer") from exc
        position = selector.get("position")
        if position is not None:
            try:
                position = int(position)
            except (TypeError, ValueError) as exc:
                raise EditRequestError(
                    f"position {position!r} is not an integer") from exc
        return cls(
            unique_id=str(selector.get("unique_id") or ""),
            resolve_item_id=str(selector.get("resolve_item_id") or ""),
            editorial_identity=tuple(identity),
            track_type=str(selector.get("track_type") or ""),
            track_index=track_index,
            position=position,
            name=str(selector.get("name") or ""),
        )

    @classmethod
    def from_shadow_clip(cls, clip: Mapping[str, Any]) -> EditTarget:
        """Build from one shadow-snapshot clip dict (reel_read shape)."""
        if not isinstance(clip, Mapping):
            raise EditRequestError("a shadow clip must be a mapping")
        track_index = clip.get("track_index")
        return cls(
            unique_id=str(clip.get("unique_id") or ""),
            editorial_identity=(),
            track_type=str(clip.get("track_type") or ""),
            track_index=(None if track_index is None
                         else int(track_index)),
            name=str(clip.get("name") or ""),
        )

    @property
    def stable_key(self) -> tuple:
        """The cross-backend identity: editorial first, shadow id fallback."""
        if self.editorial_identity:
            return ("editorial",) + tuple(self.editorial_identity)
        return ("unique_id", self.unique_id)

    @property
    def is_stable(self) -> bool:
        return bool(self.editorial_identity)

    def to_dict(self) -> dict:
        return {
            "unique_id": self.unique_id,
            "resolve_item_id": self.resolve_item_id,
            "editorial_identity": list(self.editorial_identity),
            "track_type": self.track_type,
            "track_index": self.track_index,
            "position": self.position,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditTarget:
        if not isinstance(data, Mapping):
            raise EditRequestError("an edit target must be a mapping")
        track_index = data.get("track_index")
        position = data.get("position")
        return cls(
            unique_id=str(data.get("unique_id") or ""),
            resolve_item_id=str(data.get("resolve_item_id") or ""),
            editorial_identity=tuple(data.get("editorial_identity") or ()),
            track_type=str(data.get("track_type") or ""),
            track_index=(None if track_index is None
                         else int(track_index)),
            position=(None if position is None else int(position)),
            name=str(data.get("name") or ""),
        )


# ── Operations: one semantic op with its meaning attached ────────────


@dataclass(frozen=True)
class EditOperation:
    """One semantic step of a request.

    `params` are the algebra op's remaining parameters (target keys
    live on `target`). `expected_effect` is required: what the
    originator will observe when this op lands. `depends_on` names
    earlier op indices that must apply first; `compile` orders on it.
    `kind` says whether this op mutates the timeline directly or
    changes a declaration that a rebuild must materialize.
    """

    op: str
    target: EditTarget | None
    params: dict = field(default_factory=dict)
    kind: str = DIRECT
    expected_effect: str = ""
    depends_on: tuple[int, ...] = ()
    preconditions: tuple = ()
    postconditions: tuple = ()

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise EditRequestError(
                f"op {self.op!r} names kind {self.kind!r}; "
                f"one of {list(KINDS)}")
        if not isinstance(self.op, str) or not self.op:
            raise EditRequestError("an edit operation needs an algebra op id")
        if not self.expected_effect or not str(self.expected_effect).strip():
            raise EditRequestError(
                f"op {self.op!r} states no expected effect - a request "
                "says what each op will observably change")

    def to_algebra_op(self) -> dict:
        """The flat algebra op every backend executes, meaning unchanged."""
        merged: dict[str, Any] = dict(copy.deepcopy(self.params))
        if self.target is not None:
            if self.target.unique_id:
                merged.setdefault("unique_id", self.target.unique_id)
            if self.target.resolve_item_id:
                merged.setdefault("resolve_item_id",
                                  self.target.resolve_item_id)
            if self.target.track_type:
                merged.setdefault("track_type", self.target.track_type)
            if self.target.track_index is not None:
                merged.setdefault("track_index", self.target.track_index)
        merged["op"] = self.op
        return merged

    def to_dict(self) -> dict:
        return {
            "op": self.op,
            "target": (None if self.target is None
                       else self.target.to_dict()),
            "params": copy.deepcopy(self.params),
            "kind": self.kind,
            "expected_effect": self.expected_effect,
            "depends_on": list(self.depends_on),
            "preconditions": [copy.deepcopy(c) for c in self.preconditions],
            "postconditions": [copy.deepcopy(c) for c in self.postconditions],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditOperation:
        if not isinstance(data, Mapping):
            raise EditRequestError("an edit operation must be a mapping")
        target = data.get("target")
        return cls(
            op=str(data.get("op") or ""),
            target=(None if target is None
                    else EditTarget.from_dict(target)),
            params=dict(data.get("params") or {}),
            kind=str(data.get("kind") or DIRECT),
            expected_effect=str(data.get("expected_effect") or ""),
            depends_on=tuple(int(i) for i in
                             (data.get("depends_on") or ())),
            preconditions=tuple(dict(c) for c in
                                (data.get("preconditions") or ())),
            postconditions=tuple(dict(c) for c in
                                 (data.get("postconditions") or ())),
        )


# ── The request ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class EditRequest:
    """Structured intent before physical method (VC-07).

    `expected_base_generation` is the shadow generation the request
    was planned against (`None` means the preparation stage must
    resolve it). `expected_model_revision` pins the Project Model
    head the same way. `originator` names who asked (`kind` is one of
    `ORIGINATOR_KINDS`). Operations stay in author order; `compile`
    returns them in dependency order.
    """

    id: str
    project: str
    timeline: str
    operations: tuple[EditOperation, ...]
    timeline_id: str = ""
    expected_base_generation: int | None = None
    expected_model_revision: int | None = None
    originator: dict = field(default_factory=dict)
    preconditions: tuple = ()
    idempotency_key: str = ""
    version: int = REQUEST_VERSION

    def __post_init__(self) -> None:
        if not self.id:
            raise EditRequestError("an edit request needs an id")
        if not self.idempotency_key:
            object.__setattr__(self, "idempotency_key", self.id)
        if not self.project or not self.timeline:
            raise EditRequestError(
                "an edit request names its project and timeline")
        if not self.operations:
            raise EditRequestError(
                f"request {self.id!r} carries no operations")
        if self.version != REQUEST_VERSION:
            raise EditRequestError(
                f"request {self.id!r} is version {self.version}, "
                f"this reader speaks {REQUEST_VERSION}")
        kind = (self.originator or {}).get("kind", "agent")
        if kind not in ORIGINATOR_KINDS:
            raise EditRequestError(
                f"originator kind {kind!r} is unknown; "
                f"one of {list(ORIGINATOR_KINDS)}")
        _check_operations(self.operations)

    @property
    def has_plan_changes(self) -> bool:
        return any(op.kind == PLAN_CHANGE for op in self.operations)

    @property
    def has_direct_mutations(self) -> bool:
        return any(op.kind == DIRECT for op in self.operations)

    @property
    def is_mixed(self) -> bool:
        return self.has_plan_changes and self.has_direct_mutations

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "id": self.id,
            "project": self.project,
            "timeline": self.timeline,
            "timeline_id": self.timeline_id,
            "expected_base_generation": self.expected_base_generation,
            "expected_model_revision": self.expected_model_revision,
            "originator": copy.deepcopy(self.originator),
            "operations": [op.to_dict() for op in self.operations],
            "preconditions": [copy.deepcopy(c) for c in self.preconditions],
            "idempotency_key": (self.idempotency_key or self.id),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EditRequest:
        if not isinstance(data, Mapping):
            raise EditRequestError("an edit request must be a mapping")
        unknown = (set(data)
                   - {"version", "id", "project", "timeline", "timeline_id",
                      "expected_base_generation", "expected_model_revision",
                      "originator", "operations", "preconditions",
                      "idempotency_key"})
        if unknown:
            raise EditRequestError(
                f"request carries unknown keys {sorted(unknown)} - a key "
                "nothing reads is a declaration nothing honours")
        base = data.get("expected_base_generation")
        model = data.get("expected_model_revision")
        return cls(
            id=str(data.get("id") or _request_id()),
            project=str(data.get("project") or ""),
            timeline=str(data.get("timeline") or ""),
            timeline_id=str(data.get("timeline_id") or ""),
            expected_base_generation=(None if base is None else int(base)),
            expected_model_revision=(None if model is None else int(model)),
            originator=dict(data.get("originator") or {"kind": "agent"}),
            operations=tuple(EditOperation.from_dict(op)
                             for op in (data.get("operations") or ())),
            preconditions=tuple(dict(c) for c in
                                (data.get("preconditions") or ())),
            idempotency_key=str(data.get("idempotency_key") or data.get("id")
                                or ""),
            version=int(data.get("version") or REQUEST_VERSION),
        )


def _check_operations(operations: Sequence[EditOperation]) -> None:
    """Refuse unknown ops, missing params and cyclic dependencies.

    A missing `unique_id` on a target that names a clip by name or by
    track position is not refused here: resolving a name or a position
    to a stable identity is the preparation stage's job, which records
    it as an assumption or refuses when it does not resolve.
    """
    count = len(operations)
    for index, op in enumerate(operations):
        entry = edit_algebra.status(op.op)
        if entry is None:
            raise EditRequestError(
                f"op {index} names unknown algebra op {op.op!r}")
        merged = op.to_algebra_op()
        missing = [key for key in entry.param_schema.get("required", ())
                   if key not in merged or merged.get(key) is None]
        if missing == ["unique_id"] and op.target is not None \
                and (op.target.name or op.target.position is not None):
            missing = []
        if missing:
            raise EditRequestError(
                f"op {index} ({op.op!r}) is missing required parameters: "
                + ", ".join(missing))
        for dep in op.depends_on:
            if not isinstance(dep, int) or dep < 0 or dep >= count:
                raise EditRequestError(
                    f"op {index} ({op.op!r}) depends on {dep!r}, outside "
                    f"0..{count - 1}")
            if dep == index:
                raise EditRequestError(
                    f"op {index} ({op.op!r}) depends on itself")
    _check_acyclic(operations)


def _check_acyclic(operations: Sequence[EditOperation]) -> None:
    """Refuse a dependency cycle, naming the ops in it."""
    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(index: int, trail: tuple[int, ...]) -> None:
        if index in visited:
            return
        if index in visiting:
            cycle = " -> ".join(
                str(i) for i in (*trail, index))
            raise EditRequestError(
                f"operations depend in a cycle: {cycle}")
        visiting.add(index)
        for dep in operations[index].depends_on:
            visit(dep, (*trail, index))
        visiting.discard(index)
        visited.add(index)

    for index in range(len(operations)):
        visit(index, ())


# ── Compilation: ordered ops, spans, domains ─────────────────────────


@dataclass(frozen=True)
class CompiledEdit:
    """One request compiled for every backend at once.

    `order` is the execution order over the request's operations
    (dependency order, stable on author order). `operations` are the
    flat algebra ops in that order - the SAME list every backend
    evaluates. `affected_spans` and `conflict_domains` are computed
    from the algebra, never declared by the caller.
    """

    request_id: str
    order: tuple[int, ...]
    operations: tuple[dict, ...]
    affected_spans: tuple[tuple[int, int], ...]
    conflict_domains: tuple[str, ...]
    kinds: tuple[str, ...]
    assumptions: tuple[str, ...] = ()

    def to_router_input(self, context: Mapping[str, Any] | None = None
                        ) -> tuple[dict, dict]:
        """The `(compiled_edit, context)` pair the router consumes."""
        return ({"operations": [dict(op) for op in self.operations]},
                dict(context or {}))

    def to_patch(self, capability: str, *, base_generation: int,
                 project: str = "", timeline: str = "",
                 idempotency_key: str = "",
                 preconditions: Sequence[Mapping] = (),
                 postconditions: Sequence[Mapping] = ()
                 ) -> dict:
        """The EditPatch dict for the direct/staged patch path."""
        from library.tools import edit_patch as _patch

        if not self.affected_spans:
            raise EditRequestError(
                f"request {self.request_id!r} compiled with no affected "
                "spans - run preparation against a base snapshot first, "
                "which computes them from the algebra")
        patch = {
            "id": idempotency_key or self.request_id,
            "project": project,
            "timeline": timeline,
            "base_generation": int(base_generation),
            "capability": capability,
            "affected_spans": [list(span)
                               for span in self.affected_spans],
            "conflict_domains": list(self.conflict_domains),
            "operations": [dict(op) for op in self.operations],
            "preconditions": [dict(c) for c in preconditions],
            "postconditions": [dict(c) for c in postconditions],
        }
        return _patch.EditPatch.from_dict(patch).to_dict()

    def to_axi_transaction(self, *, project: str, timeline: str,
                           base_generation: int, capability: str = "",
                           preconditions: Sequence[Mapping] = (),
                           postconditions: Sequence[Mapping] = ()
                           ):
        """The typed AXI transaction for the same compiled ops."""
        from library.tools.resolve_axi_core import AxiTransaction

        if not self.affected_spans:
            raise EditRequestError(
                f"request {self.request_id!r} compiled with no affected "
                "spans - run preparation against a base snapshot first")
        return AxiTransaction(
            id=self.request_id,
            project=project,
            timeline=timeline,
            base_generation=int(base_generation),
            capability=capability or "edit_patch",
            affected_spans=tuple(tuple(int(v) for v in span)
                                 for span in self.affected_spans),
            conflict_domains=tuple(self.conflict_domains),
            operations=tuple(dict(op) for op in self.operations),
            preconditions=tuple(dict(c) for c in preconditions),
            postconditions=tuple(dict(c) for c in postconditions),
        )

    def for_backend(self, backend: str) -> dict:
        """Qualification of these SAME ops on one backend, changing nothing.

        The ops are never rewritten per backend - that is what keeps
        one request evaluable everywhere without changing meaning.
        """
        rows = []
        for op in self.operations:
            entry = edit_algebra.status(str(op.get("op", "")))
            row = None if entry is None else entry.for_backend(backend)
            rows.append({
                "op": str(op.get("op", "")),
                "backend": backend,
                "compilable": bool(row is not None and row.compilable),
                "qualified": bool(row is not None and row.qualified),
                "production_enabled": bool(
                    row is not None and row.production_enabled),
                "qualification_evidence": (
                    list(row.qualification_evidence)
                    if row is not None else []),
            })
        return {"request_id": self.request_id, "backend": backend,
                "operations": rows,
                "all_compilable": all(row["compilable"] for row in rows),
                "all_production_enabled": all(
                    row["production_enabled"] for row in rows)}


def compile_request(request: EditRequest,
                    base: Mapping[str, Any] | None = None) -> CompiledEdit:
    """Order the request's ops, compute spans and domains.

    Pure over the request (and the base snapshot when given for span
    computation). Takes no lock, contacts no Resolve, writes nothing -
    preparation calls this after resolving the base; backends call it
    on the way to their own compile path.
    """
    if not isinstance(request, EditRequest):
        request = EditRequest.from_dict(request)
    order = _execution_order(request.operations)
    ordered = [request.operations[i] for i in order]
    algebra_ops = tuple(op.to_algebra_op() for op in ordered)
    spans: list[tuple[int, int]] = []
    assumptions: list[str] = []
    if base is not None:
        with edit_algebra.indexed_snapshot(base):
            for op in algebra_ops:
                span = edit_algebra.span(dict(op), base)
                if span is not None:
                    spans.append((int(span[0]), int(span[1])))
        if not spans:
            assumptions.append(
                "no operation resolved a frame span on the base snapshot - "
                "the plan carries no affected frames yet")
    else:
        assumptions.append(
            "compiled without a base snapshot - affected spans are "
            "uncomputed until preparation resolves one")
    domains = sorted({edit_algebra.get(str(op.get("op", ""))).domain
                      for op in algebra_ops
                      if edit_algebra.get(str(op.get("op", ""))) is not None})
    merged_spans = _merge_spans(spans)
    return CompiledEdit(
        request_id=request.id,
        order=tuple(order),
        operations=algebra_ops,
        affected_spans=tuple(merged_spans),
        conflict_domains=tuple(domains),
        kinds=tuple(op.kind for op in ordered),
        assumptions=tuple(assumptions),
    )


def _execution_order(operations: Sequence[EditOperation]) -> list[int]:
    """Dependency order over op indices, stable on author order."""
    count = len(operations)
    done: list[int] = []
    remaining = list(range(count))
    while remaining:
        progressed = False
        for index in list(remaining):
            if all(dep in done
                   for dep in operations[index].depends_on):
                done.append(index)
                remaining.remove(index)
                progressed = True
        if not progressed:
            raise EditRequestError(
                "operations cannot be ordered - their dependencies "
                "do not resolve")
    return done


def _merge_spans(spans: Sequence[tuple[int, int]]
                 ) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


# ── Routing: one boundary for every mutation path ────────────────────


def route_request(compiled: CompiledEdit, *,
                  context: Mapping[str, Any] | None = None) -> dict:
    """Send compiled ops through the authoritative router (consumed).

    The router internals stay its own; this is the single call every
    mutation path makes so build, incremental, touch-up, AXI and
    feedback paths share one routing contract (VC-09).
    """
    from library.tools import execution_router

    compiled_edit, routing_context = compiled.to_router_input(context)
    return execution_router.route_compiled_edit(
        compiled_edit, context=routing_context).as_dict()


# ── Constructors: every entry point produces this request ─────────────


#: Keys that identify a target without naming an algebra parameter.
#: `name` is deliberately absent: it is also an algebra parameter
#: (marker names, row names), so it joins the selector only beside an
#: identity key - otherwise it stays a parameter of the op.
_TARGET_IDENTITY_KEYS = ("unique_id", "resolve_item_id", "track_type",
                         "track_index", "source_file", "source_in_frame",
                         "source_out_frame", "track_role")


def _split_target(raw: dict) -> EditTarget | None:
    """Pop the target selector out of flat op params, keeping op params.

    Identity keys always address the target; `name` does only beside
    one (a clip's name), and stays an op parameter otherwise (a
    marker's name, a row's name).
    """
    selector = {key: raw.pop(key) for key in _TARGET_IDENTITY_KEYS
                if key in raw}
    if selector and "name" in raw:
        selector["name"] = raw.pop("name")
    if not selector:
        return None
    return EditTarget.from_selector(selector)


def _originator(kind: str = "agent", name: str = "",
                detail: str = "") -> dict:
    if kind not in ORIGINATOR_KINDS:
        raise EditRequestError(
            f"originator kind {kind!r} is unknown; "
            f"one of {list(ORIGINATOR_KINDS)}")
    return {"kind": kind, "name": name, "detail": detail}


def from_algebra_ops(ops: Sequence[Mapping[str, Any]], *,
                     project: str, timeline: str,
                     timeline_id: str = "",
                     expected_base_generation: int | None = None,
                     expected_model_revision: int | None = None,
                     originator: Mapping[str, Any] | None = None,
                     preconditions: Sequence[Mapping] = (),
                     idempotency_key: str = "",
                     default_kind: str = DIRECT) -> EditRequest:
    """Build a request from flat algebra ops (AXI / router callers)."""
    if default_kind not in KINDS:
        raise EditRequestError(
            f"default kind {default_kind!r} is unknown; "
            f"one of {list(KINDS)}")
    built: list[EditOperation] = []
    for index, raw in enumerate(ops):
        if not isinstance(raw, Mapping):
            raise EditRequestError(f"op {index} must be a mapping")
        raw = dict(raw)
        op_id = str(raw.pop("op", "") or "")
        kind = str(raw.pop("kind", default_kind) or default_kind)
        expected = str(raw.pop("expected_effect", "") or "")
        depends = tuple(int(i) for i in (raw.pop("depends_on", None) or ()))
        target = _split_target(raw)
        if not expected:
            expected = (f"apply {op_id} "
                        f"{('to ' + (target.name or target.unique_id)) if target else ''}".strip())
        built.append(EditOperation(
            op=op_id, target=target, params=raw, kind=kind,
            expected_effect=expected, depends_on=depends))
    return EditRequest(
        id=_request_id(), project=project, timeline=timeline,
        timeline_id=timeline_id,
        expected_base_generation=expected_base_generation,
        expected_model_revision=expected_model_revision,
        originator=dict(originator or {"kind": "agent"}),
        operations=tuple(built),
        preconditions=tuple(dict(c) for c in preconditions),
        idempotency_key=idempotency_key or "",
    )


def from_axi_operations(ops: Sequence[Mapping[str, Any]], *,
                        project: str, timeline: str,
                        base_generation: int | None = None,
                        originator: Mapping[str, Any] | None = None
                        ) -> EditRequest:
    """Build a request from AXI dispatch operations (direct mutations)."""
    base = None if base_generation is None else int(base_generation)
    return from_algebra_ops(
        ops, project=project, timeline=timeline,
        expected_base_generation=base,
        originator=dict(originator or {"kind": "agent",
                                       "name": "resolve-axi"}),
        default_kind=DIRECT)


def _program_op_to_operation(raw: Mapping[str, Any], index: int
                             ) -> EditOperation:
    """One edit_spec program op (flat or `params`-nested) to an operation."""
    if not isinstance(raw, Mapping):
        raise EditRequestError(f"program op {index} must be a mapping")
    raw = copy.deepcopy(dict(raw))
    op_id = str(raw.pop("op", "") or "")
    params = raw.pop("params", None)
    if isinstance(params, Mapping):
        merged = dict(params)
        for key, value in raw.items():
            merged.setdefault(key, value)
        raw = merged
    kind = str(raw.pop("kind", DIRECT) or DIRECT)
    expected = str(raw.pop("expected_effect", "")
                   or raw.pop("text", "") or "")
    depends = tuple(int(i) for i in (raw.pop("depends_on", None) or ()))
    pre = tuple(dict(c) for c in (raw.pop("preconditions", None) or ()))
    post = tuple(dict(c) for c in (raw.pop("postconditions", None) or ()))
    target = _split_target(raw)
    if not expected:
        expected = (f"apply {op_id} "
                    f"{('to ' + (target.name or target.unique_id)) if target else ''}".strip())
    return EditOperation(op=op_id, target=target, params=raw, kind=kind,
                         expected_effect=expected, depends_on=depends,
                         preconditions=pre, postconditions=post)


def from_edit_program(program: Mapping[str, Any], *,
                      project: str, timeline: str,
                      originator: Mapping[str, Any] | None = None
                      ) -> EditRequest:
    """Build a request from an edit_spec/revise program (`ren revise` path).

    The program's ops are already algebra ops sharing one
    `base_generation`; the request keeps that pin and the originator
    names the revision that asked.
    """
    if not isinstance(program, Mapping):
        raise EditRequestError("an edit program must be a mapping")
    ops = program.get("ops") or ()
    base = program.get("base_generation")
    return EditRequest(
        id=str(program.get("id") or _request_id()),
        project=project,
        timeline=str(program.get("reel") or program.get("timeline")
                     or timeline),
        expected_base_generation=(None if base is None else int(base)),
        originator=dict(originator or {"kind": "agent", "name": "ren revise"}),
        operations=tuple(_program_op_to_operation(op, i)
                         for i, op in enumerate(ops)),
        preconditions=tuple(dict(c) for c in
                            (program.get("preconditions") or ())),
        idempotency_key=str(program.get("idempotency_key")
                            or program.get("id") or ""),
    )


def _expand_touchup_edit(op_id: str, params: dict, name: str,
                         index: int) -> list[dict]:
    """One touch-up edit to one algebra params dict per operation.

    `set_properties` carries a `properties` mapping - one stated value
    per key - which expands to one `clip.set_property` op per key.
    The expansion states every value the edit stated, never one it did
    not: an empty mapping refuses rather than producing a no-op.
    """
    if op_id == "clip.set_property" and "properties" in params:
        properties = params.pop("properties")
        if not isinstance(properties, Mapping) or not properties:
            raise EditRequestError(
                f"touch-up edit {index} ({name!r}) states no properties - "
                "a value setter without a value is not plannable")
        return [{**params, "key": key, "value": value}
                for key, value in properties.items()]
    return [params]


def from_touchup_spec(spec: Mapping[str, Any], *,
                      project: str, timeline: str,
                      originator: Mapping[str, Any] | None = None
                      ) -> EditRequest:
    """Build a request from a reel touch-up spec (touch-up path).

    Touch-up op names resolve through the algebra's own `TOUCHUP_VIEW`
    - never an invented mapping - and `set_properties`-style edits
    naming no item refuse here instead of guessing one.
    """
    if not isinstance(spec, Mapping):
        raise EditRequestError("a touch-up spec must be a mapping")
    edits = spec.get("edits") or ()
    built: list[EditOperation] = []
    for index, edit in enumerate(edits):
        if not isinstance(edit, Mapping):
            raise EditRequestError(f"touch-up edit {index} must be a mapping")
        edit = dict(edit)
        name = str(edit.pop("op", "") or edit.pop("edit", "") or "")
        try:
            op_id = edit_algebra.TOUCHUP_VIEW[name]
        except KeyError:
            raise EditRequestError(
                f"touch-up edit {index} names {name!r}, which the algebra "
                "maps to nothing - it cannot become a precise reversible "
                "operation") from None
        kind = str(edit.pop("kind", DIRECT) or DIRECT)
        expected = str(edit.pop("expected_effect", "")
                       or edit.pop("text", "") or f"touch-up {name}")
        depends = tuple(int(i) for i in (edit.pop("depends_on", None) or ()))
        target_keys = ("unique_id", "resolve_item_id", "track_type",
                       "track_index", "source_file",
                       "source_in_frame", "source_out_frame", "track_role",
                       "row")
        selector = {k: edit.pop(k) for k in target_keys if k in edit}
        if "row" in selector and "track_type" not in selector:
            row = str(selector.pop("row"))
            selector["track_type"] = ("video" if row[:1].upper() == "V"
                                      else "audio")
            digit = "".join(ch for ch in row if ch.isdigit())
            if digit and "track_index" not in selector:
                selector["track_index"] = int(digit)
        if "item" in edit and "position" not in selector:
            selector["position"] = edit.pop("item")
        if selector and "name" in edit:
            selector["name"] = edit.pop("name")
        if op_id == "track.add" and "track_type" not in edit:
            # `add_row` makes a video row by its own gate (it checks the
            # name against video rows only) - reading that declaration,
            # not inventing a track type.
            edit["track_type"] = "video"
        if (op_id == "clip.move" and "to_frame" not in edit
                and "to_record" in edit):
            # Both vocabularies state the same contract: the item to a
            # different record position on the SAME row (`_verify_slide`
            # judges `record_in == to_frame`; the touch-up gate refuses
            # cross-row moves). `to_record` is that destination frame.
            edit["to_frame"] = edit.pop("to_record")
        target = (EditTarget.from_selector(selector) if selector else None)
        if target is None or (not target.unique_id
                               and not target.name
                               and target.position is None):
            raise EditRequestError(
                f"touch-up edit {index} ({name!r}) names no item: "
                "an in-place change without a target is not plannable")
        expanded = _expand_touchup_edit(op_id, edit, name, index)
        for params in expanded:
            built.append(EditOperation(
                op=op_id, target=target, params=params, kind=kind,
                expected_effect=expected, depends_on=depends))
    return EditRequest(
        id=str(spec.get("id") or _request_id()),
        project=project, timeline=timeline,
        originator=dict(originator or {"kind": "agent",
                                       "name": "reel touchup"}),
        operations=tuple(built),
        idempotency_key=str(spec.get("idempotency_key")
                            or spec.get("id") or ""),
    )
