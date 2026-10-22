"""AXI-004: the legacy typed transaction, behind the canonical request.

Ren carried one user-driven edit in two internal models: the canonical
`EditRequest` (``edit_request``) and the legacy typed `AxiTransaction``
(``resolve_axi_core``). Three semantic front doors read a request with
the legacy model and four read it with the canonical one, and the two
models did not agree on what a request means:

- the legacy model wrote the operations in the order the caller listed
  them, while the canonical model writes them in the order their
  declared dependencies require;
- the legacy model had nowhere to put a preservation requirement, so a
  request that named one lost it on the way to the write;
- the legacy model had nowhere to put a per-operation constraint, so a
  request that named one lost it too.

This module is the ADAPTER. It turns the legacy carrier into the
canonical request, and compiles the durable patch from THAT request, so
the two front doors read one request with one model. It writes nothing
to Resolve, decides no editorial intent, and re-implements no check
another module owns: the patch is validated by ``edit_patch``, the
request by ``edit_request``.

What the adapter deliberately does NOT change:

- the EXTENT of a legacy patch stays DECLARED by its caller. The
  canonical path measures the extent from the base snapshot; the legacy
  patch's declared spans are the caller's own contract with the
  collateral check, and replacing them with a measurement would change
  what the write is judged against.
- the CAPABILITY of a legacy patch stays DECLARED, for the same reason:
  the capability names which contract governs the write.

AXI-004b: both declarations now travel in the CANONICAL request
(``EditRequest.capability``, ``declared_affected_spans``,
``declared_conflict_domains``), and the request's compilation honours
them. So the declared scope is a property of the REQUEST, read the same
way by every door, and the durable patch below is compiled from the
request alone.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any

from library.tools import edit_request


class LegacyTransactionRefused(ValueError):
    """A legacy transaction cannot be read as a canonical request."""


#: The model a legacy carrier is adapted INTO.
CANONICAL_MODEL = "EditRequest"

#: The legacy carrier's own name.
LEGACY_MODEL = "AxiTransaction"


def carrier_dict(transaction: Any) -> dict[str, Any]:
    """The legacy carrier's own dictionary.

    A carrier is either the typed legacy object (which knows how to
    render itself) or a patch-shaped mapping - the shape the MCP doors
    and the SDK's callers hand over.
    """
    if isinstance(transaction, Mapping):
        return dict(transaction)
    patch = getattr(transaction, "patch", None)
    if callable(patch):
        rendered = patch()
        if isinstance(rendered, Mapping):
            return dict(rendered)
    raise LegacyTransactionRefused(
        f"a legacy transaction is a typed carrier or a patch mapping, "
        f"not {type(transaction).__name__}")


def is_carrier(transaction: Any) -> bool:
    """Whether this is a legacy carrier at all.

    The carrier is recognised by its PROTOCOL - it renders itself
    through ``patch()`` - and not by its class, so a front door never
    has to name the legacy type to accept one (AXI-004b). A patch-shaped
    mapping is a carrier too: that is the shape the doors' callers hand
    over.
    """
    if isinstance(transaction, Mapping):
        return ("base_generation" in transaction
                and "capability" in transaction
                and "affected_spans" in transaction)
    return callable(getattr(transaction, "patch", None))


def request_for(transaction: Any) -> edit_request.EditRequest:
    """The canonical request for a legacy carrier.

    The request keeps the carrier's identity, so the durable patch it
    compiles to has the SAME id and the SAME idempotency key the
    carrier carried: a transaction recorded before this adapter existed
    is recognised by the broker as the same transaction, and a retry is
    answered with the prior outcome rather than a second write.
    """
    carrier = carrier_dict(transaction)
    identifier = str(carrier.get("id") or "")
    if not identifier:
        raise LegacyTransactionRefused(
            "a legacy transaction carries no id - a transaction with no "
            "id cannot be recognised as a retry")
    operations = carrier.get("operations") or ()
    if not operations:
        raise LegacyTransactionRefused(
            f"legacy transaction {identifier!r} carries no operations")
    request = edit_request.from_algebra_ops(
        [dict(operation) for operation in operations],
        project=str(carrier.get("project") or ""),
        timeline=str(carrier.get("timeline") or ""),
        expected_base_generation=carrier.get("base_generation"),
        originator={"kind": "agent", "name": "resolve-axi"},
        preconditions=carrier.get("preconditions") or (),
        idempotency_key=identifier,
    )
    preservation = carrier.get("preservation") or ()
    changes: dict[str, Any] = {"id": identifier,
                                "idempotency_key": identifier}
    if preservation:
        changes["preservation"] = tuple(dict(row) for row in preservation)
    # AXI-004b: the carrier's DECLARED scope travels in the request, so
    # the scope is read from one place on every door. The request
    # normalises the declaration's shape; whether the declaration is
    # TRUE is the write's own check (`edit_patch.validate`), which is
    # where it has always been judged.
    changes["capability"] = str(carrier.get("capability") or "")
    changes["declared_affected_spans"] = tuple(
        tuple(span) for span in (carrier.get("affected_spans") or ()))
    changes["declared_conflict_domains"] = tuple(
        str(domain) for domain in (carrier.get("conflict_domains") or ()))
    return dataclasses.replace(request, **changes)


def compiled_for(transaction: Any) -> edit_request.CompiledEdit:
    """The canonical compilation of a legacy carrier.

    The compilation is what gives the request its WRITE ORDER: the
    dependencies the operations declare, not the order the caller
    happened to list them in.
    """
    return edit_request.compile_request(request_for(transaction))


def patch_for(transaction: Any) -> dict[str, Any]:
    """The durable patch a legacy carrier becomes.

    The patch is the same record the canonical path writes: the same
    operations, in the same order, with the same preservation and the
    same DECLARED scope. Everything the patch says about the write is
    read from the canonical request the carrier carries - the carrier
    names the scope, the request compiles it, and the patch is the
    result.
    """
    from library.tools import edit_patch

    carrier = carrier_dict(transaction)
    request = request_for(transaction)
    compiled = edit_request.compile_request(request)
    patch = edit_patch.EditPatch.from_dict({
        "id": request.id,
        "project": request.project,
        "timeline": request.timeline,
        "base_generation": int(request.expected_base_generation or 0),
        "capability": request.capability,
        "affected_spans": [list(span) for span in
                           compiled.affected_spans],
        "conflict_domains": list(compiled.conflict_domains),
        "operations": [dict(operation) for operation in compiled.operations],
        "preconditions": [dict(row) for row in request.preconditions],
        "postconditions": [dict(row) for row in
                           (carrier.get("postconditions") or ())],
        "preservation": [dict(row) for row in request.preservation],
    })
    return patch.to_dict()


def plan_for(transaction: Any) -> dict[str, Any]:
    """Validate a legacy carrier against its recorded base, without Resolve.

    The plan is the legacy plan: the same keys, the same refusals, and
    the operations in the canonical order.
    """
    from library.tools import edit_patch, timeline_shadow

    patch = edit_patch.EditPatch.from_dict(patch_for(transaction))
    store = timeline_shadow.ShadowStore()
    candidates = [
        (generation, snapshot)
        for generation, snapshot in store.current_snapshots(patch.project)
        if generation.timeline_name == patch.timeline
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"recorded base for {patch.project!r}/{patch.timeline!r} "
            f"resolved to {len(candidates)} timelines")
    generation, snapshot = candidates[0]
    if generation.generation != patch.base_generation:
        raise ValueError(
            f"transaction base generation {patch.base_generation} is stale; "
            f"recorded head is {generation.generation}")
    edit_patch.validate(patch, snapshot)
    failures = edit_patch._judge(patch.preconditions, snapshot, snapshot)
    if failures:
        raise ValueError("transaction preconditions failed: " + "; ".join(failures))
    return {
        "status": "planned",
        "transaction_id": patch.id,
        "project": patch.project,
        "timeline": patch.timeline,
        "base_generation": patch.base_generation,
        "backend": "edit_patch",
        "backend_reason": "typed timeline transactions execute through the EditPatch coordinator",
        "operations": [dict(operation) for operation in patch.operations],
        "affected_spans": [list(span) for span in patch.affected_spans],
        "conflict_domains": list(patch.conflict_domains),
        "authorization_required": True,
        "resolve_contacted": False,
        "mutated": False,
    }


def recorded_request(event: Mapping[str, Any]) -> edit_request.EditRequest:
    """The canonical request of a RECORDED transaction.

    A recorded transaction is an event-log entry: the id, the project,
    the timeline, the generation it was planned against and the
    operations it wrote. Reading one back through the canonical request
    is how a recorded transaction is replayed, and it is how a recovery
    names the work it owes.
    """
    operations: Sequence[Mapping[str, Any]] = event.get("operations") or ()
    identifier = str(event.get("transaction_id") or event.get("id") or "")
    request = edit_request.from_algebra_ops(
        [dict(operation) for operation in operations],
        project=str(event.get("project") or ""),
        timeline=str(event.get("timeline") or ""),
        expected_base_generation=event.get("base_generation"),
        originator={"kind": "agent", "name": "resolve-axi"},
        idempotency_key=identifier,
    )
    return dataclasses.replace(request, id=identifier,
                               idempotency_key=identifier)
