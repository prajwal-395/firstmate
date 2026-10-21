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

The gate closes that: 8 of 8 now.  The qualified set has grown since
- the live qualifications of 2026-10-21 (PR 3064, PR 3067 and PR 3042)
made seven more pairs production-enabled - so the coverage report
counts 15 qualified operations today, because `ROUTES` learned every one
of them.  Twelve of the fifteen carry the engine's own comparison; the
three `reel_touchup` overlay routes carry the WRITER's read-back, and
the report names them.  What is still open is named in
`coverage_report` - the unqualified operations, and the two batch
routes on the `resolve_axi` backend (the multi-delete batch and the
protected compound touch-up), which still carry their own receipts.

THE WHOLE-TIMELINE HALF (2026-10-21)

A whole-timeline outcome is not a mutation.  A build, a touch-up and a
revision of more than one operation do not change ONE thing; they
leave a STRUCTURE, and a delta cannot see a hole in a picture row, an
item placed twice, a clip whose media is gone, or a link that points at
nothing.  `whole_timeline_gate` runs the STRUCTURE
(`library/tools/timeline_structure.py`) beside the delta, and
`whole_timeline_coverage` counts the whole-timeline production paths
that carry it - reading the count out of the CODE, so a path that
stops running the gate is reported as uncovered the moment it does.

Before: 0 of the 4 whole-timeline paths the engine can run carried an
engine-owned structural read-back.  After: 3 of 4 - the multi-operation
revision, the protected compound touch-up and the reel build.  The
`reel_touchup` command path still re-reads its own track, and it is
named as the gap.

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

#: ``structure_readback`` - the engine read the WHOLE timeline back
#: after the write and judged its STRUCTURE: the rows, the items, the
#: source intervals, the links, the coverage - against what the
#: request was authorized to do (`library/tools/timeline_structure.py`).
#:
#: It is the whole-timeline half of AXI-013: a DELTA says what moved,
#: and a STRUCTURE says what the timeline now IS.
STRUCTURE_READBACK = "structure_readback"

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
    # `reel.opening` became production-enabled on `resolve_axi` and the
    # table had not learned it, so the coverage report was counting nine
    # routes and naming eight.  It runs as ONE operation through the
    # request executor, so it carries the same engine-owned comparison.
    Route("reel.opening", "resolve_axi",
          "library/tools/resolve_axi_core.py:_execute_registered_backend",
          PRE_POST_STATE),
    # AXI-005 (PR 3067) qualified three more pairs on live evidence -
    # the local delete on `resolve_axi`, the retime and the matte on
    # `edit_patch` - and the table had not learned them either, so the
    # coverage report was naming twelve qualified operations and
    # counting nine routes.  A qualified operation the table forgets is
    # a gap nobody can count, which is the whole reason the table and
    # the algebra are compared.
    Route("clip.delete", "resolve_axi",
          "library/tools/edit_execution_adapters.py:_delete", PRE_POST_STATE),
    Route("retime.segments", "edit_patch",
          "library/tools/edit_patch.py:apply_patch", PRE_POST_STATE),
    Route("mask.create", "edit_patch",
          "library/tools/edit_patch.py:apply_patch", PRE_POST_STATE),
    # AXI-008 (PR 3042) qualified the overlay family on `reel_touchup`.
    # The touch-up path stages a duplicate, edits it and re-reads its
    # own track, so these three carry the WRITER's read-back and not
    # the engine's own comparison: `coverage_report` names them as the
    # uncovered ones, which is the gap this table exists to count.
    Route("overlay.add", "reel_touchup",
          "library/tools/reel_touchup.py:_place_still_insertions",
          ADAPTER_READBACK),
    Route("overlay.position", "reel_touchup",
          "library/tools/reel_touchup.py:_op_position_overlay",
          ADAPTER_READBACK),
    Route("overlay.resize", "reel_touchup",
          "library/tools/reel_touchup.py:_op_resize_overlay",
          ADAPTER_READBACK),
)


@dataclass(frozen=True)
class WholeTimelineRoute:
    """One whole-timeline production path.

    A MUTATION route carries one operation.  A whole-timeline path
    produces an OUTCOME - a build, a touch-up, a revision of more than
    one operation - and its outcome is a structure, not a delta.  The
    table names the paths, and `whole_timeline_coverage` measures which
    of them the engine judges by its own structural read-back.

    `shape` is the operation set that makes the path RUN, and it is
    asked of the transaction planner: a path whose shape the planner
    refuses cannot produce a wrong outcome, and it is reported as
    unreachable rather than as covered.
    """

    path: str
    backend: str
    entry_point: str
    verification: str
    shape: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "backend": self.backend,
            "entry_point": self.entry_point,
            "verification": self.verification,
            "shape": list(self.shape),
        }


#: The whole-timeline production paths, and what each one carries.
#:
#: The AXI request paths share one entry point - the request executor -
#: because the request executor is where a request's shape is known, and
#: a path that cannot say which shape it was would be a path nobody can
#: measure.
#:
#: The `reel_touchup` path is listed and NOT covered: it stages a
#: duplicate, edits it and re-reads the track, and the re-read is the
#: writer's own.  It is named here so the gap is a number.
WHOLE_TIMELINE_ROUTES: tuple[WholeTimelineRoute, ...] = (
    WholeTimelineRoute(
        "multi_operation_revision", "resolve_axi",
        "library/tools/resolve_axi_core.py:_execute_registered_backend",
        STRUCTURE_READBACK, shape=("clip.trim", "clip.delete")),
    WholeTimelineRoute(
        "multi_delete_batch", "resolve_axi",
        "library/tools/resolve_axi_core.py:_execute_registered_backend",
        ADAPTER_READBACK, shape=("clip.delete", "clip.delete")),
    WholeTimelineRoute(
        "protected_compound_touchup", "reel_touchup",
        "library/tools/resolve_axi_core.py:_execute_registered_backend",
        STRUCTURE_READBACK, shape=("clip.trim", "clip.insert")),
    WholeTimelineRoute(
        "reel_build", "edit_patch",
        "library/tools/reel_build.py:build_reel_timeline",
        STRUCTURE_READBACK),
    WholeTimelineRoute(
        "reel_touchup", "composed_edit",
        "library/tools/reel_touchup.py:apply_touchup",
        ADAPTER_READBACK),
)


def route_can_run(route: WholeTimelineRoute) -> bool:
    """Whether the engine can run this path's shape today.

    The transaction planner is asked, because it is the thing that
    refuses a shape it cannot execute.  A path whose shape the planner
    refuses never reaches a write, so it cannot leave a wrong timeline
    behind, and counting it as covered would be counting a path that
    does not exist.
    """
    if not route.shape:
        return True
    from library.tools import axi_transaction

    plan = axi_transaction.plan_operations(
        operations=[{"op": operation, "unique_id": f"probe-{index}"}
                    for index, operation in enumerate(route.shape)],
        backend=route.backend, project="", timeline="")
    return bool(plan.supported)


def _entry_point_source(entry_point: str) -> str:
    """The source of a named entry point, or an empty string."""
    import importlib
    import inspect

    module_name, _, function_name = entry_point.partition(":")
    if not function_name:
        return ""
    try:
        module = importlib.import_module(
            module_name.removesuffix(".py").replace("/", "."))
        function = getattr(module, function_name)
        return inspect.getsource(function)
    except (ImportError, AttributeError, OSError, TypeError):
        return ""


def route_carries_whole_timeline_gate(route: WholeTimelineRoute) -> bool:
    """Whether the engine runs its structural read-back on this path.

    The answer is read out of the CODE, not out of the table: the path
    carries the gate when its entry point calls `whole_timeline_gate`
    and names this path.  A table that claims a coverage the code does
    not have is therefore reported as UNCOVERED, and the count cannot
    drift away from the engine.
    """
    source = _entry_point_source(route.entry_point)
    if "whole_timeline_gate(" not in source:
        return False
    return f'"{route.path}"' in source


def authorized_holes(baseline: Baseline) -> tuple[tuple[int, int], ...]:
    """The spans a request was authorized to leave empty.

    A removal is authorized to leave a hole where the item was, and
    that hole is named by the item's OWN span in the state before the
    write - not by a guess about which row may have holes.
    """
    from library.tools import timeline_structure

    by_id = {clip.unique_id: clip
             for clip in timeline_structure.structure_from_snapshot(
                 baseline.snapshot).clips}
    spans: list[tuple[int, int]] = []
    for operation in baseline.intended_changes:
        if str(operation.get("op") or "") not in ("clip.remove", "clip.delete"):
            continue
        clip = by_id.get(str(operation.get("unique_id") or ""))
        if clip is None:
            continue
        spans.append((clip.record_in, clip.record_out))
    return tuple(spans)


def whole_timeline_gate(path: str, *, baseline: Baseline | None = None,
                         after: Mapping | None = None,
                         structure=None,
                         expectation: Mapping | None = None,
                         affected_spans: Sequence[Sequence[int]] = (),
                         conflict_domains: Sequence[str] = (),
                         media_on_disk: bool = True) -> dict:
    """Judge a whole-timeline outcome by the engine's own read-back.

    Two verdicts, and both are the engine's:

    - the DELTA (`verify_delta`) - what moved against what the request
      was allowed to move.  A build has no "before" to compare, so the
      delta is not applicable there and says so;
    - the STRUCTURE (`library/tools/timeline_structure.py`) - what the
      timeline now IS: its rows, its items, their source intervals,
      their links and its coverage.

    `structure` is a structure the caller already read - a build holds
    one after its own inventory pass - and `after` a snapshot to read
    one from.  `path` names the production path the read-back ran on,
    and it is the name `whole_timeline_coverage` counts.
    """
    from library.tools import timeline_structure

    if structure is None:
        if after is None:
            raise ValueError(
                "the whole-timeline gate needs a structure or an after "
                "snapshot to judge")
        structure = timeline_structure.structure_from_snapshot(after)
    before = (timeline_structure.structure_from_snapshot(baseline.snapshot)
              if baseline is not None else None)
    report = timeline_structure.analyze(
        structure,
        expectation=expectation,
        before=before,
        authorized_spans=affected_spans,
        authorized_holes=(authorized_holes(baseline)
                          if baseline is not None else ()),
        expected_timeline=(baseline.timeline if baseline is not None
                           else structure.timeline_name),
        media_on_disk=media_on_disk)
    if baseline is not None and after is not None:
        delta = verify_delta(
            baseline=baseline, after=after,
            affected_spans=affected_spans,
            conflict_domains=conflict_domains)
    else:
        delta = {"status": "not_applicable",
                 "reason": "this outcome has no state before it to compare"}
    failures = list(delta.get("failures") or [])
    for finding in report.errors:
        failures.append(f"{finding.check}: {finding.detail}")
    return {
        "status": "verified" if not failures else "failed",
        "path": path,
        "structure": report.as_dict(),
        "delta": delta,
        "failures": failures,
    }


def whole_timeline_coverage() -> dict:
    """How many whole-timeline production paths carry the read-back.

    The denominator is the whole-timeline paths the engine can actually
    RUN, and the numerator is the ones whose entry point actually runs
    the gate.  A path that is not covered is NAMED, and a path the
    transaction planner cannot run is reported as unreachable rather
    than as covered - it never reaches a write, so it cannot leave a
    wrong timeline behind.
    """
    covered = []
    uncovered = []
    unreachable = []
    for route in WHOLE_TIMELINE_ROUTES:
        row = route.as_dict()
        if not route_can_run(route):
            row["verification"] = route.verification
            row["unreachable_reason"] = (
                "the transaction planner refuses this shape, so the path "
                "never reaches a write")
            unreachable.append(row)
            continue
        if route_carries_whole_timeline_gate(route):
            row["verification"] = STRUCTURE_READBACK
            covered.append(row)
        else:
            row["verification"] = route.verification
            uncovered.append(row)
    return {
        "denominator": len(covered) + len(uncovered),
        "covered": len(covered),
        "uncovered": len(uncovered),
        "covered_routes": covered,
        "uncovered_routes": uncovered,
        "unreachable_routes": unreachable,
    }


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
        "whole_timeline": whole_timeline_coverage(),
    }
