# AXI-004: one model for the meaning of a request

**Punch list item:** AXI-004 - "Consolidate legacy transaction
representations incrementally. Audit the remaining `AxiTransaction`
execution path alongside `EditRequest`; use `EditRequest` as the
canonical semantic meaning for user-driven edits; adapt legacy
execution paths behind it rather than rewriting working backends
immediately; preserve efficient bulk timeline construction; remove legacy
paths only after differential behavior and recovery tests prove
equivalence."

**Acceptance:** "No two semantic front doors can interpret the same
request differently because they use different internal transaction
models."

This file is the audit, the change, and the measurement. Read the code
for the detail; read this file for the shape.

---

## What the audit found

Ren carried one user-driven edit in two internal models:

| Representation | Module | What it was |
|---|---|---|
| `EditRequest` | `library/tools/edit_request.py` | the canonical semantic meaning of one edit |
| `CompiledEdit` | `library/tools/edit_request.py` | one request compiled for a backend (ordered operations, measured extent) |
| `EditPatch` | `library/tools/edit_patch.py` | the durable record of one write |
| `AxiTransaction` | `library/tools/resolve_axi_core.py` | the legacy typed carrier of a request |

The first three are not rival MEANINGS: the compiled edit is what one
request becomes for a backend, and the patch is what a write leaves
behind. `AxiTransaction` WAS a rival meaning - it was a second way to
carry the same request, and it did not agree with the canonical model
about what the request said.

**Seven semantic front doors** accept an edit (denominator: the
inventory in `library/tools/transaction_representations.py`):

| Front door | Entry | Model before | Model after |
|---|---|---|---|
| `mcp.resolve_axi_edit_request` | `ren/mcp_server.py:call_axi_edit_request` | `EditRequest` | `EditRequest` |
| `mcp.resolve_axi_edit_request_plan` | `ren/mcp_server.py:call_axi_edit_request_plan` | `EditRequest` | `EditRequest` |
| `mcp.resolve_axi_transaction` | `ren/mcp_server.py:call_axi_transaction` | **`AxiTransaction`** | `EditRequest` |
| `mcp.resolve_axi_transaction_submit` | `ren/mcp_server.py:call_axi_transaction_submit` | **`AxiTransaction`** | `EditRequest` |
| `mcp.resolve_axi_transaction_plan` | `ren/mcp_server.py:call_axi_transaction_plan` | **`AxiTransaction`** | `EditRequest` |
| `ren.revise` | `library/tools/revise.py` | `EditRequest` | `EditRequest` |
| `assignment.edit_apply` | `library/tools/assignment_edit_workflow.py` | `EditRequest` | `EditRequest` |

Three of them read the request with the legacy model. The divergences
that produced were real, and each one is a way for the same request to
mean two things:

1. **The write order.** The canonical model orders operations by the
   dependencies they declare (`EditRequest.depends_on`); the legacy
   carrier wrote them in the order the caller happened to list them.
   The same request reached the timeline in two different orders
   depending on the door.
2. **A per-operation preservation requirement.** A flat operation may
   name what it must not change. `edit_request.from_algebra_ops` kept
   `preservation` in the operation's PARAMETERS, so the requirement
   travelled to the backend as an unknown parameter and was never
   judged - on either door.
3. **A request-level preservation requirement.** The canonical request
   carries `preservation`; the legacy carrier had nowhere to put it,
   and the durable route built from the canonical request dropped it
   again when it compiled the patch. A requirement a request stated and
   the write dropped is a requirement nobody keeps.

## What changed

**One adapter** - `library/tools/legacy_transaction.py`:

- `request_for(transaction)` reads a legacy carrier (the typed
  `AxiTransaction` or a patch-shaped mapping) as the canonical
  `EditRequest`, keeping the carrier's id and idempotency key, so a
  transaction recorded before the adapter existed is still recognised
  as the same transaction and a retry is answered with the prior
  outcome rather than a second write.
- `patch_for(transaction)` compiles the durable patch from THAT
  request, so the patch the legacy door submits carries the canonical
  order, the request's preservation and the request's preconditions.
- `plan_for(transaction)` is the legacy plan (the same keys, the same
  refusals) with the canonical operations in it.
- `recorded_request(event)` reads a RECORDED transaction back through
  the canonical request, which is how a recovery names the work it
  owes.

**The legacy doors now read the canonical model**
(`ren/mcp_server.py`): the three `resolve_axi_transaction*` doors hand
their patch-shaped input to the adapter instead of building a carrier
of their own. The SDK's own entry points (`resolve_axi_sdk.plan`,
`submit_transaction_job`, `apply_transaction`) accept either the
typed carrier or a patch mapping and read both through the adapter.

**The flat builder keeps the constraints a flat operation names**
(`edit_request.from_algebra_ops`): `preconditions`, `postconditions`
and `preservation` are read as what they are, not as operation
parameters.

**The durable route keeps the request's preservation**
(`resolve_axi_core.submit_edit_request_async`): the patch it compiles
carries `request.preservation`, so the durable route judges the
requirements the semantic route judges.

## The measurement, before and after

Taken by `python3 -m library.tools.transaction_representations`, which
measures the tree rather than a hand-written count. The "before" column
is the same measurement on the base commit (`6d59e75cd`).

| Measure | Before | After | Denominator |
|---|---|---|---|
| Semantic front doors | 7 | 7 | - |
| Distinct semantic models in use | **2** | **1** | 7 front doors |
| Front doors not reading the canonical model | **3** | **0** | 7 front doors |
| Production construction sites of the legacy carrier | **2** | **1** | 1114 production Python files before, 1116 after (this lane added two modules) |
| Production reference sites of the legacy carrier | **13** | **7** | 1114 production Python files before, 1116 after |

The one construction site left is `edit_request.CompiledEdit.to_axi_transaction`
- the canonical model's OWN conversion into the legacy carrier, which is
the direction the consolidation wants.

**Fidelity.** The measurement is not the whole claim; the differential
and the replay are. `tests/contracts/test_transaction_representation_consolidation.py`:

- the legacy door and the canonical door write the SAME operations, in
  the SAME order, for the same request (`depends_on` included);
- a recorded transaction replays to the same operations and to the same
  durable patch, so a recovery undoes what was written;
- the carrier's DECLARED extent and capability survive the adapter -
  the consolidation did not replace a declared extent with a measured
  one;
- both doors keep the preservation requirement the request names, and
  refuse one the engine cannot judge;
- the legacy door refuses an operation the algebra does not know, which
  is what the canonical door already did;
- the round trip is LOSSLESS over the whole algebra: every one of the
  84 operations the algebra declares, built with its required
  parameters, survives the adapter unchanged - an operation that lost a
  parameter on the way in would be written with less meaning than the
  caller asked for.

A key that NO operation declares is dropped by the adapter, because the
canonical model does not carry it: that changes a transaction's intent
digest, so a retry of such a transaction is answered as a CONFLICT
rather than as a second write. The safe direction, and it is stated
here rather than left to be discovered.

Selected suites: 1306 tests, all passing
(`scripts/select_dependent_tests.py` over the seven changed files, plus
the new contract test).

**No live Resolve write was made.** Another lane held the Resolve
session (a qualification session in the Ren Qualification project) for
the whole of this lane, and the captain's projects are off limits. The
proof is therefore the recorded-state one above: the differential
between the two doors and the replay of a recorded transaction, both
run against recorded bases, with no project touched.

## What was NOT consolidated, and why

- **The EXTENT.** The canonical path MEASURES the affected spans from
  the base snapshot; a legacy patch DECLARES them. The declared spans
  are the caller's own contract with the collateral check, and
  replacing them with a measurement would change what a write is judged
  against. The adapter keeps the declaration. **CLOSED by AXI-004b**
  (`docs/evidence/axi004b_declared_scope.md`): the declaration travels
  in the canonical request and every door honours it, so the two doors
  no longer disagree about the extent.
- **The CAPABILITY.** The canonical path derives the narrowest
  capability contract that covers the operations; a legacy patch names
  its capability. The capability names which contract governs the
  write, so the declaration stays. **CLOSED by AXI-004b**: the
  declaration travels in the canonical request, and the canonical door
  reads it from there instead of deriving a second one.
- **The INPUT SHAPE of the legacy doors.** The three
  `resolve_axi_transaction*` doors still take a patch-shaped mapping.
  They now READ it with the canonical model, which is what the
  acceptance asks for; changing the shape their callers hand over is a
  different change, and the shape is the legacy public API. **STILL
  OPEN.**
- **The bulk construction path.** The reel build is a STRATEGY request
  (a staged incremental diff or a full rebuild), not a list of
  per-operation algebra operations, and it does not become a list of
  `EditRequest`s. It keeps its own path and its own efficiency. See
  `docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` and the open item in
  `docs/AXI_EXECUTION_CONSOLIDATION.md` (AXI-034). **STILL OPEN.**

## What is left

- The legacy carrier is still a public type, because the SDK's callers
  hand one over. It is now only ever built FROM the canonical request.
  **AXI-004b took the SDK's re-export of it away**: the SDK accepts a
  carrier by its protocol and no longer names the type, so the two
  production references that remain are the canonical model's own
  conversion.
- The two doors still disagreed about the EXTENT (declared against
  measured) and about the CAPABILITY (declared against derived), by
  the two decisions above. **CLOSED by AXI-004b**
  (`docs/evidence/axi004b_declared_scope.md`): the declaration travels
  in the canonical request, the request's compilation honours it, and
  both doors write the same durable patch for the same request.
- `AxiTransaction.patch()` is the legacy carrier's own rendering and
  is still what the carrier is; the durable patch the engine writes is
  compiled by the adapter.
- The legacy doors' INPUT SHAPE is still the legacy one, and the reel
  build's bulk path is still its own.
