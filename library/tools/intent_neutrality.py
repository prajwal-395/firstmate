"""INFRA-004: editorial intent that names MATERIAL, not a Resolve object.

The punchlist asks (INFRA-004):

    - Maintain NLE-neutral operation semantics.
    - Map each operation to the appropriate supported backend.
    - Record capability mismatches explicitly.
    - Avoid embedding Resolve-specific commands in high-level creative
      plans.

This module is the BOUNDARY between the two, and it holds four things:

* `RESOLVE_SPECIFIC_FIELDS` - the ONE enumeration of the fields whose
  value IS a Resolve object identifier.  A record that carries one of
  them is bound to one NLE; a record that carries none can be executed
  on any NLE that Ren supports.
* `ItemRef` - the NLE-neutral address of one timeline item: the
  MATERIAL it plays (source file and source range), the row it plays on
  and the frames it occupies.  `timeline_addressing.editorial_identity`
  already reads the material half; this is the whole address, and it
  survives a rebuild.
* `neutralize` - the intent record with every Resolve identifier
  replaced by the neutral address of the material it names, and with
  every mismatch named rather than swallowed.
* `bind` - the ADAPTER.  The neutral intent plus the recorded timeline
  gives back the request the backends execute, byte for byte.

Nothing here writes, contacts Resolve, or decides an edit.  It moves
identifiers across a boundary, and it counts them, so the claim
"editorial intent is independent of Resolve" is a measurement rather
than an intention.

The two records this separates:

    INTENT  what was asked, and what will happen: NLE-neutral.
    BINDING what instance of the material the edit acts on: the
            backend's business, resolved at execution time.

A target that can only be named by a Resolve object id - a media pool
item, a timeline item the material of which was never recorded - is a
CAPABILITY MISMATCH, and it is recorded as one.  It is never silently
dropped, and it is never guessed.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: The ONE enumeration of the fields whose value IS a Resolve object
#: identifier.  A `unique_id` in Ren's vocabulary is Resolve's
#: `GetUniqueId()` of a timeline item (the shadow snapshot's clip key),
#: so it belongs here and not in the neutral half.
RESOLVE_SPECIFIC_FIELDS: tuple[str, ...] = (
    "unique_id",
    "resolve_item_id",
    "media_pool_item_id",
    "media_pool_item",
    "timeline_id",
)

#: The schema the neutral intent is published under.
NEUTRAL_INTENT_SCHEMA = "ren-neutral-intent/1"

#: The operations whose `unique_id` is the identity the NEW item will
#: carry, not the address of an item that already exists.  No address
#: can replace those: the engine mints them, and the plan says so.
MINTED_IDENTITY_OPS: tuple[str, ...] = (
    "clip.insert", "broll.insert", "compound.create")

#: The parameters that name MORE THAN ONE existing item, and the key a
#: plan writes their ADDRESSES under instead.  An operation that names a
#: second item (`clip.join`), links clips (`clip.link`) or builds a
#: compound (`compound.create`) names those items the way it names the
#: first one - by the material they play.
ADDRESSED_MULTI_TARGET_PARAMS: dict[str, str] = {
    "other_unique_id": "other",
    "with_unique_ids": "with",
    "child_unique_ids": "children",
}


def _is_identifier(value: Any) -> bool:
    """Whether a value is an identifier at all (a name is not an id)."""
    return isinstance(value, str) and bool(value.strip())


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── Counting: the measurement's two halves ───────────────────────────


@dataclass(frozen=True)
class FieldHit:
    """One Resolve-specific field, and where it sat in the record."""

    path: str
    key: str
    value: str

    def to_dict(self) -> dict:
        return {"path": self.path, "key": self.key, "value": self.value}


def scan(record: Any) -> tuple[FieldHit, ...]:
    """Every Resolve-specific field a record carries, with its path.

    A field is counted when its KEY is one of
    `RESOLVE_SPECIFIC_FIELDS` and its VALUE is a non-empty string.  An
    empty value is an absent identifier, not one: counting it would
    inflate the numerator with fields that name nothing.
    """
    hits: list[FieldHit] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                here = f"{path}.{key}" if path else str(key)
                if str(key) in RESOLVE_SPECIFIC_FIELDS and _is_identifier(value):
                    hits.append(FieldHit(here, str(key), str(value)))
                walk(value, here)
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(record, "")
    return tuple(hits)


def count_fields(record: Any) -> int:
    """How many keyed fields a record holds - the denominator.

    Every mapping key counts, at every depth, whether or not its value
    is an identifier.  The denominator is the SAME rule before and after
    the change, so the two numbers are comparable.
    """
    total = 0

    def walk(node: Any) -> None:
        nonlocal total
        if isinstance(node, Mapping):
            total += len(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(record)
    return total


def identifier_count(record: Any) -> dict:
    """The measurement's own shape: hits over fields, plus the paths."""
    hits = scan(record)
    return {
        "fields": count_fields(record),
        "resolve_specific_fields": len(hits),
        "paths": [hit.path for hit in hits],
    }


# ── The neutral address of one timeline item ─────────────────────────


@dataclass(frozen=True)
class ItemRef:
    """The NLE-neutral address of one item: the material and its place.

    `source_file` with `source_in_frame`/`source_out_frame` is the
    EDITORIAL identity - the tuple `timeline_addressing` compares across
    backends.  `track_type`/`track_index` say the row, and
    `record_in`/`record_out` where it plays.  Nothing here is an
    instance identifier: the same material cut twice is the same
    address.
    """

    source_file: str = ""
    source_in_frame: int | None = None
    source_out_frame: int | None = None
    track_type: str = ""
    track_index: int | None = None
    record_in: int | None = None
    record_out: int | None = None
    name: str = ""
    track_role: str = ""

    @property
    def key(self) -> str:
        """A stable key for this address - the material, not the instance."""
        return "|".join([
            self.source_file,
            "" if self.source_in_frame is None else str(self.source_in_frame),
            "" if self.source_out_frame is None else str(self.source_out_frame),
            self.track_type,
            "" if self.track_index is None else str(self.track_index),
            self.name,
        ])

    @property
    def editorial_identity(self) -> tuple:
        """The identity the addressing layer compares, or () when absent."""
        if (not self.source_file or self.source_in_frame is None
                or self.source_out_frame is None):
            return ()
        return (self.source_file, int(self.source_in_frame),
                int(self.source_out_frame), self.track_role or None)

    @property
    def is_addressable(self) -> bool:
        """Whether this address names material at all."""
        return bool(self.editorial_identity) or bool(self.name)

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "source_in_frame": self.source_in_frame,
            "source_out_frame": self.source_out_frame,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "record_in": self.record_in,
            "record_out": self.record_out,
            "name": self.name,
            "track_role": self.track_role,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ItemRef:
        if not isinstance(data, Mapping):
            raise TypeError("an item address must be a mapping")
        return cls(
            source_file=str(data.get("source_file") or ""),
            source_in_frame=_as_int(data.get("source_in_frame")),
            source_out_frame=_as_int(data.get("source_out_frame")),
            track_type=str(data.get("track_type") or ""),
            track_index=_as_int(data.get("track_index")),
            record_in=_as_int(data.get("record_in")),
            record_out=_as_int(data.get("record_out")),
            name=str(data.get("name") or ""),
            track_role=str(data.get("track_role") or ""),
        )


def item_ref(clip: Mapping[str, Any]) -> ItemRef:
    """The neutral address of one shadow-snapshot clip."""
    if not isinstance(clip, Mapping):
        raise TypeError("a timeline item must be a mapping")
    return ItemRef(
        source_file=str(clip.get("source_file") or ""),
        source_in_frame=_as_int(clip.get("source_in_frame")),
        source_out_frame=_as_int(clip.get("source_out_frame")),
        track_type=str(clip.get("track_type") or ""),
        track_index=_as_int(clip.get("track_index")),
        record_in=_as_int(clip.get("record_in")),
        record_out=_as_int(clip.get("record_out")),
        name=str(clip.get("name") or ""),
    )


def refs_from_snapshot(snapshot: Mapping[str, Any]) -> dict[str, ItemRef]:
    """The recorded timeline as the map from instance id to address.

    This is the RESOLVER's view: it says which address each recorded
    item id stands for.  It is built from the shadow snapshot, never
    from Resolve, so the binding is reproducible from recorded state.
    """
    refs: dict[str, ItemRef] = {}
    if not isinstance(snapshot, Mapping):
        return refs
    for track in snapshot.get("tracks") or ():
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if unique_id:
                refs[unique_id] = item_ref(clip)
    return refs


def material_of(clip: Mapping[str, Any]) -> tuple[str, int, int] | None:
    """The MATERIAL one recorded clip plays, or None when it says none.

    Three fields, and they are the identity the addressing layer
    compares across backends (`timeline_addressing.editorial_identity`):
    the file, and the source range cut out of it.  Two placements of the
    same cut are the same material; two cuts of the same file are not.
    """
    if not isinstance(clip, Mapping):
        return None
    source_file = str(clip.get("source_file") or "")
    source_in = _as_int(clip.get("source_in_frame"))
    source_out = _as_int(clip.get("source_out_frame"))
    if not source_file or source_in is None or source_out is None:
        return None
    return (source_file, source_in, source_out)


def address_in(container: Mapping[str, Any]) -> bool:
    """Whether a record names the ADDRESS of the item it acts on.

    The model-facing plan's own shape (INFRA-004): the material it
    plays (the file and the source range) or, for an item that has no
    material, the placement it occupies (the row and the frames) - and
    never the instance the item sits on today.  One rule, read by every
    place that has to accept a plan written this way.
    """
    if not isinstance(container, Mapping):
        return False
    material = bool(str(container.get("source_file") or "")
                    and container.get("source_in_frame") is not None
                    and container.get("source_out_frame") is not None)
    return material or container.get("record_in") is not None


def address_of(entry: Mapping[str, Any]) -> dict:
    """The NEUTRAL ADDRESS one plan states: what it plays, and where.

    Two forms, and a plan may state either or both:

    * the MATERIAL - the file and the source range cut out of it.
      This is the identity that survives a rebuild.
    * the PLACEMENT - the row and the frames the item occupies on it.
      An item with no material (a caption, an overlay the engine
      drew) has no file to name, and the placement is what it has.

    The row narrows the answer and never widens it.
    """
    if not isinstance(entry, Mapping):
        return {"material": (), "track_type": "", "track_index": None,
                "record_in": None, "record_out": None}
    source_file = str(entry.get("source_file") or "")
    source_in = _as_int(entry.get("source_in_frame"))
    source_out = _as_int(entry.get("source_out_frame"))
    material = ((source_file, source_in, source_out)
                if source_file and source_in is not None
                and source_out is not None else ())
    return {
        "material": material,
        "track_type": str(entry.get("track_type") or ""),
        "track_index": _as_int(entry.get("track_index")),
        "record_in": _as_int(entry.get("record_in")),
        "record_out": _as_int(entry.get("record_out")),
    }


def instances_for_address(snapshot: Mapping[str, Any],
                          address: Mapping[str, Any]) -> tuple[str, ...]:
    """The recorded instance ids ONE neutral address names.

    This is the BINDING's other direction: the model-facing plan names
    the material (or the placement), and the recorded timeline says
    which instance of it the reel holds today.

    An address that matches MORE THAN ONE recorded item is AMBIGUOUS,
    and the caller refuses it: a plan that names a material twice
    cannot say which of the two placements it means, and picking one
    would be a guess about the editor's intent.
    """
    if not isinstance(snapshot, Mapping) or not isinstance(address, Mapping):
        return ()
    material = tuple(address.get("material") or ())
    track_type = str(address.get("track_type") or "")
    track_index = _as_int(address.get("track_index"))
    record_in = _as_int(address.get("record_in"))
    record_out = _as_int(address.get("record_out"))
    if not material and track_index is None and record_in is None:
        return ()
    found: list[str] = []
    for track in snapshot.get("tracks") or ():
        row_type = str(track.get("type") or "")
        row_number = _as_int(track.get("index"))
        for clip in track.get("clips") or ():
            if material and material_of(clip) != material:
                continue
            clip_type = str(clip.get("track_type") or "") or row_type
            clip_index = _as_int(clip.get("track_index"))
            if clip_index is None:
                clip_index = row_number
            if track_type and clip_type != track_type:
                continue
            if track_index is not None and clip_index != track_index:
                continue
            if record_in is not None and _as_int(
                    clip.get("record_in")) != record_in:
                continue
            if record_out is not None and _as_int(
                    clip.get("record_out")) != record_out:
                continue
            unique_id = str(clip.get("unique_id") or "")
            if unique_id:
                found.append(unique_id)
    return tuple(found)


# ── Capability mismatches: named, never swallowed ────────────────────


@dataclass(frozen=True)
class CapabilityMismatch:
    """One thing the intent cannot say, in neutral terms.

    `kind` says WHAT is missing:

    * `unresolved_target` - the target is named by a Resolve object id
      and the material it stands for was not recorded, so the intent
      cannot state the target without naming the instance.
    * `instance_only_operation` - the operation's own semantics need a
      Resolve object id (a media pool item, a timeline), so no neutral
      intent can express it.
    * `backend_unsupported` - the chosen backend cannot express this
      operation at all.
    """

    kind: str
    detail: str
    op: str = ""
    op_index: int | None = None
    field: str = ""

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "detail": self.detail,
            "op": self.op,
            "op_index": self.op_index,
            "field": self.field,
        }


# ── The neutral intent ───────────────────────────────────────────────


@dataclass(frozen=True)
class NeutralTarget:
    """Who an operation acts on, in terms no NLE owns."""

    ref: ItemRef | None = None
    name: str = ""
    track_type: str = ""
    track_index: int | None = None
    position: int | None = None

    def to_dict(self) -> dict:
        return {
            "item_ref": (None if self.ref is None else self.ref.to_dict()),
            "name": self.name,
            "track_type": self.track_type,
            "track_index": self.track_index,
            "position": self.position,
        }


@dataclass(frozen=True)
class NeutralOperation:
    """One semantic operation, with its meaning and nothing else."""

    op: str
    target: NeutralTarget | None = None
    params: dict = field(default_factory=dict)
    kind: str = "direct"
    expected_effect: str = ""
    depends_on: tuple = ()
    preconditions: tuple = ()
    postconditions: tuple = ()
    preservation: tuple = ()

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
            "preservation": [copy.deepcopy(r) for r in self.preservation],
        }


@dataclass(frozen=True)
class OpBinding:
    """The adapter's map from one neutral target to its instance handles.

    The handles are the BACKEND's business: they are what the executor
    needs to find the item on the instance it is running against.  They
    live beside the intent, never inside it - `NeutralIntent.to_dict`
    does not carry them, and `adapter_record` is the sidecar that does.
    """

    op_index: int
    ref: ItemRef | None
    handles: dict = field(default_factory=dict)
    target: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "op_index": self.op_index,
            "item_ref": (None if self.ref is None else self.ref.to_dict()),
            "handles": dict(self.handles),
            "target": dict(self.target),
        }


@dataclass(frozen=True)
class NeutralIntent:
    """What was asked, in terms no NLE owns.

    `bindings` is the adapter's map back to the instance, and
    `mismatches` says what could not be said neutrally.  Neither is
    part of the intent: they are the two things that belong to the
    backend.
    """

    id: str
    project: str
    timeline: str
    operations: tuple = ()
    originator: dict = field(default_factory=dict)
    preconditions: tuple = ()
    preservation: tuple = ()
    backend_constraints: tuple = ()
    expected_base_generation: int | None = None
    expected_model_revision: int | None = None
    idempotency_key: str = ""
    bindings: tuple = ()
    mismatches: tuple = ()

    def to_dict(self) -> dict:
        """The INTENT record: what was asked, naming no Resolve object.

        The bindings are NOT in it.  They are the adapter's sidecar
        (`adapter_record`), because a binding IS a set of instance
        handles - the one thing an NLE-neutral record may not carry.
        """
        return {
            "schema": NEUTRAL_INTENT_SCHEMA,
            "id": self.id,
            "project": self.project,
            "timeline": self.timeline,
            "operations": [op.to_dict() for op in self.operations],
            "originator": copy.deepcopy(self.originator),
            "preconditions": [copy.deepcopy(c) for c in self.preconditions],
            "preservation": [copy.deepcopy(r) for r in self.preservation],
            "backend_constraints": list(self.backend_constraints),
            "expected_base_generation": self.expected_base_generation,
            "expected_model_revision": self.expected_model_revision,
            "idempotency_key": self.idempotency_key,
            "mismatches": [m.to_dict() for m in self.mismatches],
        }

    def adapter_record(self) -> dict:
        """The intent beside the adapter's bindings: the pair, in one record.

        This is what a backend needs, and it is NOT the intent: it
        carries the instance handles the executor reads.
        """
        return {
            "schema": "ren-neutral-intent-binding/1",
            "intent": self.to_dict(),
            "bindings": [b.to_dict() for b in self.bindings],
        }

    @property
    def is_neutral(self) -> bool:
        """Whether this intent names no Resolve object at all."""
        return not scan(self.to_dict())


# ── Neutralizing: the intent, without the instance ───────────────────


def _handles_for(target: Any) -> dict:
    """The instance handles one target carries, by field name."""
    handles: dict[str, str] = {}
    for key in ("unique_id", "resolve_item_id"):
        value = getattr(target, key, "") if target is not None else ""
        if _is_identifier(value):
            handles[key] = str(value)
    return handles


def neutralize(request: Any, *,
               refs: Mapping[str, ItemRef] | None = None
               ) -> NeutralIntent:
    """The intent record with every Resolve identifier removed.

    `refs` is the resolver's map from instance id to the material that
    id stands for (see `refs_from_snapshot`).  A target whose id is not
    in that map cannot be stated neutrally: the intent records that as
    a MISMATCH and keeps the target's own words (its name, its row, its
    position), and it never invents an identity.

    An operation whose own parameters carry a Resolve identifier is
    recorded as an `instance_only_operation` mismatch: the intent
    cannot express it, and the backend has to mint the instance it
    names.
    """
    from library.tools.edit_request import EditRequest

    if not isinstance(request, EditRequest):
        request = EditRequest.from_dict(request)
    resolver = dict(refs or {})

    operations: list[NeutralOperation] = []
    bindings: list[OpBinding] = []
    mismatches: list[CapabilityMismatch] = []

    for index, operation in enumerate(request.operations):
        target = operation.target
        handles = _handles_for(target)
        ref: ItemRef | None = None
        if handles:
            for value in handles.values():
                found = resolver.get(value)
                if found is not None:
                    ref = found
                    break
            if ref is None:
                mismatches.append(CapabilityMismatch(
                    kind="unresolved_target",
                    detail=(
                        f"operation[{index}] ({operation.op}) names its "
                        f"target by a Resolve object id and the material it "
                        f"stands for was not recorded, so the intent cannot "
                        f"state the target without naming the instance"),
                    op=operation.op, op_index=index,
                    field=",".join(sorted(handles))))

        params = dict(operation.params)
        target_ids = set(handles.values())
        for key in list(params):
            if (key in RESOLVE_SPECIFIC_FIELDS
                    and _is_identifier(params[key])):
                if str(params[key]) in target_ids:
                    # The TARGET, named in the flat op's parameters.
                    # The neutral target already carries the address.
                    params.pop(key)
                    continue
                mismatches.append(CapabilityMismatch(
                    kind="instance_only_operation",
                    detail=(
                        f"operation[{index}] ({operation.op}) carries "
                        f"{key!r}, which names a Resolve object; the "
                        f"operation's semantics need the instance, so the "
                        f"backend mints it"),
                    op=operation.op, op_index=index, field=key))
                params.pop(key)

        neutral_target = None
        if target is not None:
            neutral_target = NeutralTarget(
                ref=ref,
                name=str(target.name or ""),
                track_type=str(target.track_type or ""),
                track_index=target.track_index,
                position=target.position,
            )
        operations.append(NeutralOperation(
            op=operation.op,
            target=neutral_target,
            params=params,
            kind=operation.kind,
            expected_effect=operation.expected_effect,
            depends_on=tuple(operation.depends_on),
            preconditions=tuple(operation.preconditions),
            postconditions=tuple(operation.postconditions),
            preservation=tuple(operation.preservation),
        ))
        bindings.append(OpBinding(
            op_index=index, ref=ref, handles=handles,
            target=(target.to_dict() if target is not None else {})))

    return NeutralIntent(
        id=request.id,
        project=request.project,
        timeline=request.timeline,
        operations=tuple(operations),
        originator=copy.deepcopy(request.originator),
        preconditions=tuple(request.preconditions),
        preservation=tuple(request.preservation),
        backend_constraints=tuple(request.backend_constraints),
        expected_base_generation=request.expected_base_generation,
        expected_model_revision=request.expected_model_revision,
        idempotency_key=request.idempotency_key,
        bindings=tuple(bindings),
        mismatches=tuple(mismatches),
    )


# ── Binding: the adapter, and the route it must not change ──────────


def bind(intent: NeutralIntent) -> Any:
    """The request the backends execute, from the neutral intent.

    The ADAPTER's other half: the handles the executor needs are put
    back where the executor reads them, and nothing else moves.  The
    request this returns is the request the intent was made from, and
    the router sees the same operations either way.
    """
    from library.tools.edit_request import EditOperation, EditRequest, EditTarget

    by_index = {binding.op_index: binding for binding in intent.bindings}
    operations = []
    for index, operation in enumerate(intent.operations):
        binding = by_index.get(index)
        target = None
        if operation.target is not None:
            if binding is not None and binding.target:
                target = EditTarget.from_dict(binding.target)
            else:
                handles = dict(binding.handles) if binding is not None else {}
                ref = operation.target.ref
                target = EditTarget(
                    unique_id=str(handles.get("unique_id") or ""),
                    resolve_item_id=str(handles.get("resolve_item_id") or ""),
                    editorial_identity=(ref.editorial_identity
                                        if ref is not None else ()),
                    track_type=operation.target.track_type,
                    track_index=operation.target.track_index,
                    position=operation.target.position,
                    name=operation.target.name,
                )
        operations.append(EditOperation(
            op=operation.op,
            target=target,
            params=copy.deepcopy(operation.params),
            kind=operation.kind,
            expected_effect=operation.expected_effect,
            depends_on=tuple(operation.depends_on),
            preconditions=tuple(operation.preconditions),
            postconditions=tuple(operation.postconditions),
            preservation=tuple(operation.preservation),
        ))
    return EditRequest(
        id=intent.id,
        project=intent.project,
        timeline=intent.timeline,
        operations=tuple(operations),
        expected_base_generation=intent.expected_base_generation,
        expected_model_revision=intent.expected_model_revision,
        originator=copy.deepcopy(intent.originator),
        preconditions=tuple(intent.preconditions),
        preservation=tuple(intent.preservation),
        backend_constraints=tuple(intent.backend_constraints),
        idempotency_key=intent.idempotency_key,
    )


# ── Capability mismatches, per backend ───────────────────────────────


def capability_mismatches(intent: NeutralIntent, backend: str) -> tuple:
    """What this backend cannot express, named.

    The punchlist asks (INFRA-004) that capability mismatches be
    recorded EXPLICITLY.  The judgment is NOT this module's: the
    execution router is the one place that decides which backend may
    run an operation (AXI-002), so the report reads the router's own
    admission judgment.  A report that judged for itself could disagree
    with the route the request will actually take - and then it would
    be a second, quieter router.

    An operation the router does not admit on this route is a
    `backend_unsupported` mismatch, and the intent's own mismatches
    (a target that cannot be stated neutrally, an operation that needs
    an instance) travel with it.
    """
    from library.tools import execution_router

    mismatches: list[CapabilityMismatch] = []
    for index, operation in enumerate(intent.operations):
        reasons = execution_router.route_admission_reasons(
            str(backend), [operation.op])
        for reason in reasons:
            mismatches.append(CapabilityMismatch(
                kind="backend_unsupported",
                detail=reason,
                op=operation.op, op_index=index))
    mismatches.extend(intent.mismatches)
    return tuple(mismatches)


# ── The corpus: what the repo has RECORDED ──────────────────────────

#: The recorded timeline dump the corpus is built from.  It is a
#: recorded export of one reel's timeline: every clip carries the
#: Resolve item id it had on the day, and the source range it played.
RECORDED_TIMELINE = "tests/fixtures/reel_hearing/reel26.timeline.json"

#: The recorded live evidence the corpus is built from: the receipts
#: of the recorded live runs, each carrying the operations it sent.
RECORDED_REPORTS = "tests/live/reports"


def _recorded_snapshot(path: str) -> dict:
    """A recorded timeline dump as the shadow snapshot's own shape.

    The dump records `file_path`, `source_in` and `source_out`; the
    snapshot records `source_file`, `source_in_frame` and
    `source_out_frame`.  The mapping is mechanical and it is stated
    here rather than done by hand, so the corpus cannot drift from the
    recording it came from.
    """
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    metadata = data.get("metadata") or {}
    tracks = []
    for track in data.get("tracks") or ():
        clips = []
        for clip in track.get("clips") or ():
            clips.append({
                "unique_id": str(clip.get("unique_id") or ""),
                "name": str(clip.get("name") or ""),
                "source_file": str(clip.get("file_path") or ""),
                "source_in_frame": clip.get("source_in"),
                "source_out_frame": clip.get("source_out"),
                "record_in": clip.get("record_in"),
                "record_out": clip.get("record_out"),
                "duration": clip.get("duration"),
                "track_type": str(track.get("type") or ""),
                "track_index": int(track.get("index") or 0),
                "enabled": True,
                "markers": [],
            })
        tracks.append({
            "type": str(track.get("type") or ""),
            "index": int(track.get("index") or 0),
            "name": str(track.get("name") or ""),
            "clips": clips,
        })
    return {
        "start_frame": metadata.get("start_frame", 0),
        "end_frame": metadata.get("end_frame", 0),
        "fps": metadata.get("fps"),
        "tracks": tracks,
        "markers": {"timeline": []},
    }


def _recorded_live_requests(root: str = RECORDED_REPORTS) -> list[dict]:
    """The requests the recorded live runs sent, as they recorded them.

    A recorded receipt carries the operations it sent under
    `expected_state.operations`.  Those operations name their targets
    by the Resolve item id they had on the day - which is exactly what
    this lane measures.
    """
    import json
    from pathlib import Path

    found: list[dict] = []
    base = Path(root)
    if not base.is_dir():
        return found

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if (key == "operations" and isinstance(value, list)
                        and value and all(
                            isinstance(row, Mapping) and "op" in row
                            and "unique_id" in row for row in value)):
                    found.append({"operations": copy.deepcopy(value)})
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    for path in sorted(base.rglob("*.json")):
        try:
            walk(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


def build_corpus(timeline_path: str = RECORDED_TIMELINE,
                 reports_root: str = RECORDED_REPORTS) -> dict:
    """The corpus: the recorded timeline, the requests over it, and the
    requests the recorded live runs sent.

    Every number the measurement reports is measured over THIS corpus,
    and every part of it is committed evidence - a recorded timeline
    export and the recorded live receipts.
    """
    snapshot = _recorded_snapshot(timeline_path)
    requests = []
    for track in snapshot.get("tracks") or ():
        for clip in track.get("clips") or ():
            unique_id = str(clip.get("unique_id") or "")
            if not unique_id:
                continue
            requests.append({
                "id": f"recorded:{unique_id}",
                "project": "Recorded Reel 26",
                "timeline": "Reel 26 - write-for-the-question-your-customer-ask",
                "expected_base_generation": 1,
                "operations": [{
                    "op": "clip.set_property",
                    "target": {
                        "unique_id": unique_id,
                        "track_type": clip.get("track_type"),
                        "track_index": clip.get("track_index"),
                        "name": clip.get("name"),
                    },
                    "params": {"key": "Pan", "value": 1.0},
                    "kind": "direct",
                    "expected_effect": (
                        f"nudge the frame playing {clip.get('name')}"),
                }],
            })
    return {
        "schema": "ren-intent-neutrality-corpus/1",
        "provenance": {
            "timeline": timeline_path,
            "reports_root": reports_root,
            "note": "committed recorded evidence: a recorded timeline "
                    "export and the recorded live receipts",
        },
        "snapshot": snapshot,
        "requests": requests,
        "recorded_requests": _recorded_live_requests(reports_root),
    }


# ── The measurement ──────────────────────────────────────────────────


def measure(records: Sequence[Mapping[str, Any]], *,
            refs: Mapping[str, ItemRef] | None = None
            ) -> dict:
    """The before/after measurement over a corpus of intent records.

    `records` are the intent records as they exist today (each a
    request dict).  For every one, this measures the fields that carry
    a Resolve identifier BEFORE (the record itself) and AFTER (the
    neutral intent), with the SAME denominator rule, and it proves the
    route is unchanged: the request the router sees after the round
    trip is the request it saw before.
    """
    before_fields = 0
    before_hits = 0
    after_fields = 0
    after_hits = 0
    round_trips = 0
    identical = 0
    unresolved = 0
    mismatches = 0

    for record in records:
        from library.tools.edit_request import EditRequest

        request = (record if isinstance(record, EditRequest)
                   else EditRequest.from_dict(record))
        before_fields += count_fields(request.to_dict())
        before_hits += len(scan(request.to_dict()))

        intent = neutralize(request, refs=refs)
        after_fields += count_fields(intent.to_dict())
        after_hits += len(scan(intent.to_dict()))
        mismatches += len(intent.mismatches)
        unresolved += sum(1 for mismatch in intent.mismatches
                          if mismatch.kind == "unresolved_target")

        rebuilt = bind(intent)
        round_trips += 1
        if rebuilt.to_dict() == request.to_dict():
            identical += 1

    return {
        "schema": "ren-intent-neutrality-measurement/1",
        "records": len(records),
        "before": {
            "fields": before_fields,
            "resolve_specific_fields": before_hits,
        },
        "after": {
            "fields": after_fields,
            "resolve_specific_fields": after_hits,
        },
        "targets_unresolved_without_the_timeline": unresolved,
        "capability_mismatches": mismatches,
        "round_trips": round_trips,
        "round_trips_identical": identical,
        "route_unchanged": identical == round_trips,
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Measure the neutrality of a corpus of recorded intent records.

    Usage: `bin/vep -m library.tools.intent_neutrality measure <file.json>

    The file holds `{"records": [<request dict>, ...]}` - the intent
    records as they exist today.
    """
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(
        prog="intent_neutrality",
        description="measure the Resolve identifiers in a corpus of "
                    "intent records")
    parser.add_argument("corpus", help="a JSON file of recorded requests")
    parser.add_argument("--out", default="", help="write the report here")
    args = parser.parse_args(list(argv) if argv is not None else None)

    path = Path(args.corpus)
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records") if isinstance(data, Mapping) else data
    report = measure(list(records or ()))
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
