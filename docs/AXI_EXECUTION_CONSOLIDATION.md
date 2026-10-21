# AXI execution consolidation: per-item status

**Lane:** `fm/cap-axi-consolidate-final` (PR 2945; supersedes 2908, 2924, 2929)
**Scope:** punch list section 1.4, items AXI-034 .. AXI-042, of
the punch list (firstmate's home: `data/ren-capability/punch-list.md`).

This file says, per item, what was already there before this lane, what
this lane added, and what is still open and why. It is the durable
answer to "say per item what was already done".

Read the code for the detail; read this file for the shape.

---

## AXI-034 - `EditRequest` is the canonical semantic interface

**Already done.**
`library/tools/edit_request.py` owns ONE typed request for every timeline
edit: `EditRequest` (`:269`), `EditTarget` (`:63`), `EditOperation`
(`:183`). `compile_request` (`:545`) compiles it once, and
`CompiledEdit.for_backend` (`:517`) only ANNOTATES qualification - it
never rewrites the ops, which is what makes "same request, different
backend, same meaning" a property of the type. Every semantic entry
point constructs it: `from_algebra_ops` (`:697`), `from_axi_operations`
(`:739`), `from_edit_program` (`:782`), `from_touchup_spec` (`:832`).
`route_request` (`:624`) is the one call a semantic mutation path makes.
The contract test is `tests/contracts/test_edit_request.py`.

**This lane.** The reel build's strategy selection now asks the SAME
router entry point as every other path
(`reel_build.strategy_after_incremental_refusal`), and the
`route_request` docstring was corrected: it claimed every mutation path
made that call, which was false for the build.

**Open.** A STRATEGY request is not a semantic edit and carries no
algebra operations, so it is not an `EditRequest`. Making the two
strategy ids first-class would add `reel.full_build` and
`incremental.overlay_diff` to the algebra's audited inventory - 84
operations, one audited classification each - which changes the
capability matrix and the generated skill references. The reel build's
physical placement is a bulk construction from a plan rather than a list
of per-clip algebra operations; turning it into `EditRequest`s is a
larger change than this consolidation owns.

**Landed after this file was written (AXI-004, 2026-10-20).** The
legacy `AxiTransaction` path (`resolve_axi_core.py`, `_plan_transaction_patch`)
no longer runs beside `EditRequest` as a SECOND meaning: it is an
ADAPTER over it. `library/tools/legacy_transaction.py` reads the
carrier as the canonical request, and the durable patch it becomes is
compiled from that request, so the write order, the constraints and the
preservation are the canonical ones. The three `resolve_axi_transaction*`
MCP doors read their input with the canonical model; the carrier's own
DECLARED extent and capability stay declared. The audit, the
measurement and what is still open are in
`docs/AXI-004_CONSOLIDATION.md`.

## AXI-035 - backend selection lives inside AXI

**Already done.**
`library/tools/execution_router.py` is the only selection point:
`route_compiled_edit` (`:835`). No caller flag can select a backend -
`DIRECT_BYPASS_FLAGS` (`:44`) are refused outright (`:858`), and direct
execution is proven per op, never requested. `library/tools/edit_qualification_policy.py`
is the single owner of the protected-experimental policy, read by both
the AXI planner and the front-door classifier. The AXI planner routes
BEFORE submission (`resolve_axi_core._prepare_request_plan`, `:1298`) and
`submit_edit_request_async` (`:1481`) dispatches to the routed backend.

**This lane.** The reel build no longer selects its own strategy. It asks
the router with the refusal's typed category, and the router's rule -
only `unsupported` may fall back, and only under an explicit caller
policy - is what governs.

**Open.** `_registered_batch_supported` and the qualified/protected
backend sets in `resolve_axi_core` are a second selection axis beside
the router. They are guards, not selectors, but they read like one.

## AXI-036 - normalized arguments, error types, receipts, verification

**Already done.**
`library/tools/transaction_orchestration.py` owns the shared contract:
`ExecutionInput` (`:65`), `ExecutionRefusal` (`:87`), `VerificationResult`
(`:138`), `RecoveryResult` (`:152`), `ExecutionResult` (`:165`),
`verify_readback` (`:278`), `standardize_receipt` (`:385`),
`attach_routing` (`:438`). `library/tools/transaction_receipt.py` owns
the lifecycle vocabulary: `STAGES` (`:8`), `lifecycle` (`:31`),
`standardize` (`:67`). Every backend adapter leaves through
`standardize_receipt`.

**This lane.** `ExecutionRefusal` carries the next action and the
recorded limitations, and `ExecutionRefusal.from_exception` normalizes a
typed refusal into it - an exception that is not a refusal is a
`backend_failure`, not a decision Ren made. The reel build publishes
through the same contract: `library/tools/reel_execution_contract.py`
gives it the common `execution_contract` block and the common lifecycle,
with the build's own measurements as the evidence.

**Open.** `transaction_receipt.standardize` treats a receipt with NO
acceptance contract the same as one whose contracts FAILED - both read
"failed". That is why the reel build publishes through its own contract
rather than through `standardize_receipt`: forcing it through would put
`recovery_required` on a successful build. The distinction exists
already in `operation_acceptance.evaluate`, which separates "missing"
from "failed"; it has not been carried up into `standardize`.

**AXI-003 (this lane).** The distinction is carried up. The acceptance
contract has ONE outcome, in three readings - `passed`, `failed`,
`unmeasured` (`operation_acceptance.outcome_of`), and
`transaction_receipt.standardize` reads it: the `verified` block says
which of the three happened, a contract nobody measured carries NO
recovery (nothing is known to be wrong), and the whole receipt has ONE
reading through `transaction_receipt.acceptance_outcome` - `accepted`,
`failed`, `unmeasured`, `refused`. The six lifecycle blocks are defined
once, in `transaction_receipt.lifecycle_blocks`, so the staged reel build
publishes the same shape as a semantic patch instead of its own.

**Open.** The broker's job receipt and the undo journal entry are still
their own shapes: a job envelope answers "did the job run" and a journal
entry answers "what may be undone", which are different questions from
the six. The census measures them rather than pretending they are the
same receipt.

## AXI-037 - reel-building execution through common execution contracts

**Already done.**
The touch-up path attaches the router's decision through the shared
`attach_routing` (`reel_touchup.py:4100`), and the incremental arm
attaches its own decision. `composed_edit`'s phases are mapped onto the
shared lifecycle by `transaction_orchestration.composed_lifecycle`
(`:458`).

**This lane.** The reel build's two physical arms - the staged
incremental diff and the full rebuild - now ask the router for their
strategy, and the build record is published through the common
execution contract with the common lifecycle beside it.

**Open.** The build record's own vocabulary (`placements`,
`overlay_placements`, `placement_check`, `stream_enforcement`) is still
the build's. It is published through the common contract rather than
expressed in it, and the common contract's operations are the build's
two measurements rather than per-operation acceptance contracts.

## AXI-038 - fast bulk-build paths preserved

**Already done.**
`library/tools/reel_rebuild_need.py` decides REBUILD against LEAVE_ALONE
and fails closed: any answer that is not a positive match on both halves
is a rebuild. `library/tools/reel_incremental.py` is the staged overlay
diff. Both strategies remain selectable in the router, and
`route_full_rebuild` refuses a plan whose operations no backend proves
(`_preflight_full_rebuild_operations`, `:643`) - a rebuild cannot widen
support.

**This lane.** The choice between them stopped being control flow and
became a receipted router decision, and the mutation scope records which
one ran.

**Open.** Nothing in the router MEASURES the cost of the two routes.
`edit_algebra`'s measured costs cover four semantic operations, not the
build strategies, so "a rebuild is genuinely more efficient" is still a
judgement the caller makes.

## AXI-039 - prefer targeted changes over a disturbing rebuild

**Already done.**
`library/tools/reel_replace_guard.py` diffs the live timeline against
the live replacement before a captain-visible timeline is replaced, and
refuses on an editor change. `library/tools/editor_edit_carry.py` carries
the editor's changes onto staging. `library/tools/edit_state_preservation.py`
captures and restores editorial state across an item replacement, and
`library/tools/edit_ledger.py` makes the hands survive a rebuild. The
router refuses to reconstruct the timeline for an existing-item property
change (`execution_router.py:1063-1080`).

**This lane.** The decision records the MUTATION SCOPE - `targeted`,
`full_rebuild` or `none` - so a receipt can answer "was the rest of the
reel preserved?". And a full rebuild over an ELIGIBLE targeted route no
longer records the targeted route as "not requested or not eligible":
that was a lie in the receipt, and choosing a reconstruction over an
eligible targeted edit is exactly what a reader needs to see.

**Open.** The router does not REFUSE a rebuild when a targeted route is
eligible - it records that one was. Refusing it is a product decision
(AXI-038 says the bulk path is legitimate where a rebuild is genuinely
more efficient), so it is left to the caller with the truth in the
receipt.

## AXI-040 - no silent fallbacks that change meaning or preservation

**Already done.**
The router's refusal categories are a closed set and only `unsupported`
may fall back to a rebuild, and only under an explicit caller policy
(`execution_router.py:944-953`); an incremental refusal is never
collapsed into a rebuild; staging stays the production default
(`STAGING_IS_PRODUCTION_DEFAULT`, `:39`); `reel_replace_guard` and
`reel_rebuild_need` fail closed.

**This lane.** An ineligibility category outside the closed set is no
longer coerced into `unsupported` - the one category a rebuild may
follow - so a typo or an unreadable live state can no longer become a
silent rebuild. The reel build's fallback is the router's decision, made
from the refusal's own typed category. The OTIO placement fallback was
printed and not recorded; the build record now names the route that ran
beside the one that was refused.

**Open.** The `OtioPlacementRefused` -> append fallback is still a
fallback: it changes how the timeline is created, though not what is on
it. It is now recorded; it is not refused.

## AXI-041 - single-instance broker and transaction ownership

**Already done.**
`library/tools/resolve_lock.py` takes an exclusive, broker-scheduled,
flock-fenced lease (`resolve_lease`, `:899`), and the cursor fence
(`assert_current_timeline`, `:1632`) keeps a writer on one timeline.
`library/tools/resolved/jobs.py` declares the typed write kinds
(`KINDS`, `:90`) - `timeline.apply_patch` and `timeline.semantic_edit`
among them - and `library/tools/resolved/worker.py` holds the lease for the whole job.
`resolve_axi_core._execute_registered_backend` holds it across a whole
batch. `tests/contracts/test_resolve_guard_wiring.py` fails if a routed
operation does not take the lease its class needs, and
`tests/contracts/test_resolve_write_registry.py` accounts for every
statically dispatched Resolve mutation.

**This lane.** The OWNERSHIP moved into the decision: every route
declares the broker kind that owns the write, the lease it runs under
and the guard that enforces it, and a route the router does not know
declares no ownership rather than borrowing another route's. The reel
build's published record carries it.

**Open.** The reel build takes many SECTIONED leases rather than one
whole-job lease, so "one transaction owner" for a build is a sequence of
owners, each exclusive. `tests/unit/reels/test_reel_build_whole_lease.py`
covers the whole-lease behaviour; the ownership declaration names the
lease class, not the number of acquisitions.

## AXI-042 - typed, actionable refusals

**Already done.**
`library/tools/ren_refusal.py` is the engine-wide refusal shape: what,
why, fix, a machine-readable code and a recovery class, rendered in one
place per verb. `edit_algebra` records the limitations of every
operation on every backend (`BackendStatus.limitations`), `capability()`
exposes them, and `.agents/skills/resolve-axi/references/backend-limitations.md`
is generated from them.

**This lane.** `RoutingRefused` and `IncrementalRefused` are `RenRefusal`s
now, so what / why / fix / recovery_class reach every boundary that
already knows how to render a refusal, and the recorded limitations
travel with them. The AXI CLI boundary is one named function, so a
routing refusal leaves as a REFUSAL with a next action instead of an
unexpected error with a traceback.

**Open.** `JobRefused` (`resolved/jobs.py:194`) is still a bare
`ValueError`, so a refusal at the broker's submission gate does not
carry the engine-wide shape. It reaches the caller as a job result
rather than as an exception, so the shape matters less there - but it is
not one vocabulary yet.
