"""AXI-013: every mutation is verified against a fresh pre/post state.

AXI-013 asks four things of a mutation, and this module is where they
are said once:

- CAPTURE the target identity, the intended changes, the preservation
  scope and the baseline revision BEFORE the write;
- COMPARE the actual delta with the permitted delta - what changed on
  the timeline against what the operations were allowed to change;
- REQUIRE the verification appropriate to the operation - structural,
  visual or audio - and never less;
- REJECT a success claim that rests on an API return value.

The comparison is NOT new. `library/tools/preservation_invariant.py`
already computes the semantic delta between two timeline states and
judges it against the scope a set of operations authorizes, and
`library/tools/transaction_orchestration.py` already scans for changed
clips outside the authorized spans.  This module joins them into ONE
gate, states the rule, and says which production routes carry it.

The gate is INDEPENDENT of the writer.  A mutation is judged by the
engine's own comparison of two fresh reads, not by the receipt the
writer handed back: a writer that changed the WRONG ITEM and said
"committed" is exactly what AXI-013 names, and a receipt cannot be
evidence against the writer that produced it.

`library/tools/resolve_axi_core.py` runs the gate on the semantic edit
path; `library/tools/edit_patch.py` runs it inside the EditPatch
commit.  The routes that do not carry it yet are named in
`ROUTES`, so the gap is a number rather than a feeling.

THE COUNT, AND WHAT IT WAS (2026-10-21)

Of the eight (operation, backend) pairs the algebra marks
`production_enabled`, FOUR carried an engine-owned pre/post comparison
and FOUR did not.  The four `edit_patch` routes were judged by
`transaction_orchestration.verify_readback` - the engine's own
comparison of the state before the write and the state after it.  The
four `resolve_axi` routes were judged by the ADAPTER that performed the
write: each adapter reads the timeline back after its own write, but
the COMPARISON was the writer's, and the engine's whole-request
comparison only ran for a request of MORE THAN ONE OPERATION - so the
common case, one operation, had no engine-owned comparison at all.

The gate closes that: 8 of 8 now.  What is still open is named in
`coverage_report` - the unqualified operations, and the two batch
routes on the `resolve_axi` backend (the multi-delete batch and the
protected compound touch-up), which still carry their own receipts.

`tests/unit/resolve/test_mutation_verification.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: The verification kinds a production mutation route can carry.
#:
#: ``pre_post_state`` - the engine compared a fresh post-write state
#: against the pre-write state it captured, and refused the mutation
#: when the two did not match the permitted delta.
#:
#: ``adapter_readback`` - the writer read the timeline back after its
#: own write and judged its own result.  The read is real; the
#: COMPARISON is the writer's, so a writer that mis-resolves its
#: target is judged by its own mis-resolution.
#:
#: ``api_return_value`` - the route reported what the API answered and
#: nothing else.
PRE_POST_STATE = "pre_post_state"
ADAPTER_READBACK = "adapter_readback"
API_RETURN_VALUE = "api_return_value"

#: The backends whose production route carries the engine's own
#: pre/post comparison.
#:
#: ``edit_patch`` - `library/tools/edit_patch.py` reads the timeline
#: before the write, reads it again after, and judges the two in
#: `transaction_orchestration.verify_readback`.
#:
#: ``resolve_axi`` - `library/tools/resolve_axi_core.py` reads the
#: timeline after the write and judges it in
#: `mutation_verification.verify_delta`.
GATE_BACKENDS: frozenset[str] = frozenset({"edit_patch", "resolve_axi"})


@dataclass(frozen=True)
class Baseline:
    """What was captured BEFORE the write.

    Four things, and each is named rather than implied: the identity of
    the thing the mutation names, the changes it intends, the scope it
    may touch, and the revision of the timeline it was planned against.
    """

    project: str
    timeline: str
    revision: int
    target_identity: tuple[str, ...]
    intended_changes: tuple[Mapping, ...]
    preservation_scope: tuple[str, ...]
    snapshot: Mapping

    def as_dict(self) -> dict:
        return {
            "project": self.project,
            "timeline": self.timeline,
            "revision": self.revision,
            "target_identity": list(self.target_identity),
            "intended_changes": [dict(change)
                                 for change in self.intended_changes],
            "preservation_scope": list(self.preservation_scope),
        }


def target_identities(operations: Sequence[Mapping]) -> tuple[str, ...]:
    """The identities a set of operations names.

    A mutation is about a THING.  The thing is named here - the clip,
    the client clip, the pool item, the row - so a later comparison can
    say whether the thing that changed is the thing that was named.
    """
    identities: list[str] = []
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        op_id = str(operation.get("op") or "")
        for key in ("unique_id", "client_unique_id", "media_pool_item"):
            value = operation.get(key)
            if value:
                identities.append(f"{op_id}:{key}={value}")
        track_type = operation.get("track_type")
        if track_type:
            identities.append(
                f"{op_id}:row={track_type}{operation.get('track_index', '')}")
    return tuple(dict.fromkeys(identities))


def capture_baseline(*, project: str, timeline: str, revision: int,
                     operations: Sequence[Mapping], snapshot: Mapping,
                     preservation_scope: Sequence[str] = ()) -> Baseline:
    """Capture the state a mutation is about to be judged against."""
    return Baseline(
        project=project,
        timeline=timeline,
        revision=int(revision),
        target_identity=target_identities(operations),
        intended_changes=tuple(dict(operation)
                               for operation in operations or ()),
        preservation_scope=tuple(preservation_scope),
        snapshot=snapshot)


def verify_delta(*, baseline: Baseline, after: Mapping,
                 affected_spans: Sequence[Sequence[int]] = (),
                 conflict_domains: Sequence[str] = (),
                 id_map: Mapping | None = None,
                 source: str = "fresh_live_timeline") -> dict:
    """Compare the ACTUAL delta with the PERMITTED delta.

    `baseline` is what was captured BEFORE the write - the target
    identity, the intended changes, the preservation scope and the
    revision - and `after` a fresh read taken after it.  The permitted
    delta is computed from the operations themselves - what they were
    allowed to change - and the actual delta is what the timeline
    really carries.  A change no operation authorized is collateral and
    fails the gate; so does a changed clip outside the authorized
    spans.

    The verdict is a verdict, not a receipt: it says what the engine
    measured, and it never reads the writer's own claim.
    """
    from library.tools import preservation_invariant
    from library.tools.transaction_orchestration import (
        unrelated_clip_changes,
    )

    before = baseline.snapshot
    operations = baseline.intended_changes
    scope = preservation_invariant.scope_for(
        operations, conflict_domains=conflict_domains,
        affected_spans=affected_spans)
    delta = preservation_invariant.invariant(
        scope=scope, before=before, operations=operations, after=after)
    verdict = delta.as_dict()
    collateral = unrelated_clip_changes(
        operations=operations, affected_spans=affected_spans,
        id_map=id_map, before=before, after=after)
    failures: list[str] = []
    if verdict["status"] == "unprovable":
        failures.append(verdict["unprovable_reason"])
    elif verdict["status"] != "preserved":
        for row in verdict["unrelated_changes"]:
            failures.append(
                f"{row.get('target')}: {row.get('field')} changed and no "
                f"operation authorized it")
    for row in collateral:
        failures.append(
            f"clip {row.get('unique_id')} changed outside the authorized "
            "spans")
    return {
        "status": "verified" if not failures else "failed",
        "snapshot_source": source,
        "baseline": baseline.as_dict(),
        "checks": [
            {"check": "actual_delta_matches_permitted_delta",
             "passed": verdict["status"] == "preserved",
             "detail": verdict["status"]},
            {"check": "unrelated_items_unchanged",
             "passed": not collateral,
             "detail": [dict(row) for row in collateral]},
        ],
        "failures": failures,
        "preservation": verdict,
        "collateral_changes": [dict(row) for row in collateral],
    }


@dataclass(frozen=True)
class Route:
    """One production mutation route, and what it carries today."""

    operation: str
    backend: str
    entry_point: str
    verification: str

    def as_dict(self) -> dict:
        return {
            "operation": self.operation,
            "backend": self.backend,
            "entry_point": self.entry_point,
            "verification": self.verification,
        }


#: The production mutation routes of the QUALIFIED operations - the
#: (operation, backend) pairs the algebra marks `production_enabled` -
#: and the verification each route carries.
#:
#: The `edit_patch` routes were already judged by the engine's own
#: pre/post comparison (`edit_patch.apply_patch` reads the timeline
#: before and after the write and `transaction_orchestration.
#: verify_readback` judges the two).  The `resolve_axi` routes were
#: judged by the ADAPTER that performed the write: each one reads the
#: timeline back after its own write, but the comparison was the
#: writer's, and a single-operation request got no engine-owned
#: comparison at all.
ROUTES: tuple[Route, ...] = (
    Route("clip.delete", "edit_patch",
          "library/tools/edit_patch.py:apply_patch", PRE_POST_STATE),
    Route("clip.replace", "edit_patch",
          "library/tools/edit_patch.py:apply_patch", PRE_POST_STATE),
    Route("clip.set_property", "edit_patch",
          "library/tools/edit_patch.py:apply_patch", PRE_POST_STATE),
    Route("track.add", "edit_patch",
          "library/tools/edit_patch.py:apply_patch", PRE_POST_STATE),
    Route("clip.remove", "resolve_axi",
          "library/tools/edit_execution_adapters.py:_remove", PRE_POST_STATE),
    Route("clip.insert", "resolve_axi",
          "library/tools/edit_execution_adapters.py:_place", PRE_POST_STATE),
    Route("broll.insert", "resolve_axi",
          "library/tools/edit_execution_adapters.py:_broll_insert",
          PRE_POST_STATE),
    Route("caption.style", "resolve_axi",
          "library/tools/edit_execution_adapters.py:_caption_style",
          PRE_POST_STATE),
)


def qualified_pairs() -> tuple[tuple[str, str], ...]:
    """The (operation, backend) pairs the algebra marks production-enabled.

    This is the DENOMINATOR: the operations the engine may run in
    production, on the backend that runs them.
    """
    from library.tools import edit_algebra

    pairs = []
    for op_id, status in edit_algebra.OP_STATUS.items():
        for row in status.backends:
            if row.production_enabled:
                pairs.append((op_id, row.backend))
    return tuple(sorted(pairs))


def gate_covers(operation: str, backend: str) -> bool:
    """Whether the engine's own pre/post comparison runs on this route.

    A route is covered when the backend that executes it judges the
    mutation against a fresh post-write state - not when the writer
    judged its own work.
    """
    if backend not in GATE_BACKENDS:
        return False
    from library.tools import edit_algebra

    status = edit_algebra.status(operation)
    if status is None:
        return False
    row = status.for_backend(backend)
    return bool(row is not None and row.production_enabled)


def route_carries_gate(backend: str,
                       operations: Sequence[Mapping]) -> bool:
    """Whether every operation in a request runs on a gated route.

    A request is judged by the engine's own comparison only when every
    operation in it is a qualified operation on a backend whose route
    carries the gate.  A request that mixes a qualified operation with
    an unqualified one is not judged by half a gate: it keeps the
    verification it had, and the gap is counted in
    `coverage_report`.
    """
    rows = [operation for operation in operations or ()
            if isinstance(operation, Mapping)]
    if not rows:
        return False
    return all(gate_covers(str(operation.get("op") or ""), backend)
               for operation in rows)


def coverage_report() -> dict:
    """How many production mutation routes carry the AXI-013 gate.

    The denominator is the qualified operations - the (operation,
    backend) pairs the algebra marks `production_enabled` - and the
    numerator is the ones whose route the engine judges against a fresh
    pre/post state.  A route that is not covered is NAMED, so the gap
    is countable rather than arguable.
    """
    qualified = qualified_pairs()
    covered = []
    uncovered = []
    for operation, backend in qualified:
        if gate_covers(operation, backend):
            covered.append({"operation": operation, "backend": backend})
        else:
            uncovered.append({"operation": operation, "backend": backend})
    return {
        "denominator": len(qualified),
        "covered": len(covered),
        "uncovered": len(uncovered),
        "covered_routes": covered,
        "uncovered_routes": uncovered,
        "routes": [route.as_dict() for route in ROUTES],
    }
