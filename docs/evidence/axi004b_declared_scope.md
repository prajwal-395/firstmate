# The declared scope: one request, one extent, one capability

AXI-004b (2026-10-21).  The companion to `docs/AXI-004_CONSOLIDATION.md`
(which is the audit and the first consolidation), and the write-up for
the lane that moved the legacy carrier's DECLARED extent and capability
behind the canonical `EditRequest`.

## What was left

The first consolidation put all seven semantic front doors on one model
(`EditRequest`) and left three things behind:

1. the legacy carrier's **declared extent and capability**;
2. the legacy doors' **patch-shaped input**;
3. the **reel build bulk path**.

This lane moved the first of those behind the canonical request.  The
second and the third are still open, and the "What was left" section of
`docs/AXI-004_CONSOLIDATION.md` says so.

## The divergence that was still there

Two doors read one request, and they read the SCOPE of it differently:

* the **legacy door** believed the carrier's declarations - the extent
  the caller declared, the capability the caller named;
* the **canonical door** measured the extent from the base snapshot and
  derived the narrowest registered capability contract that covered the
  operations.

So the same request - the same operations, the same order, the same
preservation - was governed by two different contracts and judged
against two different extents, depending on which door it arrived
through.  A write judged against an extent nobody declared is a write
whose collateral check is somebody else's.

## The change

**The declaration is part of the request** (`library/tools/edit_request.py`).
`EditRequest` carries three fields:

| field | meaning |
|---|---|
| `capability` | the contract the caller declares governs the write |
| `declared_affected_spans` | the extent the caller declares |
| `declared_conflict_domains` | the domains the caller declares the write touches |

Empty means the engine measures and derives, exactly as it always did.
A declaration is CARRIED, not judged: whether the declared capability is
registered, whether the declared domains are in the patch's vocabulary
and whether the declared extent covers what the operations touch stay
where they were - the write's own check (`edit_patch.validate`).  A
refusal that moved would be a refusal with a new message and a new
author, and the author is the point.

**The compilation honours a declaration** (`edit_request.compile_request`).
A request that declares an extent compiles to THAT extent, not to the
measurement; a request that declares domains compiles to THOSE domains,
not to the derived ones.  One rule, everywhere the request is compiled.

**The legacy carrier's declaration IS the request's declaration**
(`library/tools/legacy_transaction.py`).  The adapter copies the
carrier's scope into the request, and the durable patch is compiled from
the request alone - the carrier names the scope, the request compiles
it, the patch is the result.

**The canonical door honours the declared capability**
(`library/tools/resolve_axi_core.py`).  `_capability_for(request, compiled)`
returns the declared capability when the request declares one and the
derived narrowest contract when it does not.  That is the last place
where one request could be governed by two contracts.

**The legacy carrier is recognised by its PROTOCOL.**  A carrier renders
itself through `patch()`; a patch-shaped mapping is a carrier too.
`legacy_transaction.is_carrier` is that recognition, so no production
path has to NAME the legacy type to accept one.  The SDK
(`library/tools/resolve_axi_sdk`) no longer re-exports the type: the
class is still importable from `library.tools.resolve_axi_core`, and
every entry point still accepts a carrier.

## The measurement, before and after

`python3 -m library.tools.transaction_representations` measures the tree.
The "before" column is the same measurement on the base commit.

| Measure | Before | After | Denominator |
|---|---|---|---|
| Production reference sites of the legacy carrier | **7** | **2** | 1147 production Python files before, 1147 after |
| Front doors not reading the canonical model | 0 | 0 | 7 front doors |
| Production construction sites of the legacy carrier | 1 | 1 | 1147 production Python files |

The two sites that remain are `library/tools/edit_request.py`: the
canonical model's OWN conversion into the carrier, which is the
direction the consolidation wants.  The five that are gone are the four
in `library/tools/resolve_axi_core.py` (an `isinstance` check and three
annotations) and the SDK's re-export.

`python3 scripts/measure_declared_scope.py` measures the SCOPE over the
corpus of real legacy carriers the protocol fixtures hold
(`native/ren-protocol/fixtures/edit-patch.json` - payloads the Python
code paths produced, not hand-written ones):

| Measure | Before | After | Denominator |
|---|---|---|---|
| The legacy door's durable patch identical, byte for byte | - | **9/9** | 9 legacy carriers |
| The canonical door's capability diverged from the legacy door's | **9/9** | **0/9** | 9 legacy carriers |
| The canonical door could derive a capability at all | 4/9 | 9/9 | 9 legacy carriers |
| The declared extent carried unchanged | 9/9 | 9/9 | 9 legacy carriers |
| The MEASURED extent | - | **unmeasured** | 9 legacy carriers |

The measured extent is UNMEASURED, not equal: the corpus carries no base
snapshot, so no measurement was taken.  The claim about the extent is
the weaker and truer one - the declaration is carried unchanged, and the
measurement was not substituted for it.

The 9/9 identity of the legacy door's patch was checked twice: against
the pre-change module read out of git (`git show HEAD:...` at the base
commit) and against the BEFORE reading the measurement script models
explicitly.  Both agree with the patch the door writes now.

## The differential

`tests/contracts/test_transaction_representation_consolidation.py` holds
the consolidation still.  The new tests each name the defect they
guard:

- the declared scope travels in the canonical request;
- **both doors write the SAME durable patch for the same request** - the
  whole patch dict, not a field or two;
- a declared extent is not replaced by a measurement;
- a request that declares no scope is measured, as it always was;
- the declared capability governs the write on the canonical door, and
  a request that declares none still gets the derived narrowest
  contract;
- the declared scope survives preparation (the bound copy the plan is
  compiled from) and the neutral form (the request that crosses the NLE
  boundary);
- a carrier is recognised without naming the legacy type;
- the legacy carrier is named only by the canonical model.

## What was NOT consolidated, and why

- **The INPUT SHAPE of the legacy doors.**  The three
  `resolve_axi_transaction*` doors still take a patch-shaped mapping.
  They read it with the canonical model, and a request-shaped payload
  already routes to the canonical path; changing the shape their
  callers hand over is the next step, and it is the legacy public API.
- **The bulk construction path.**  The reel build is a STRATEGY request
  (a staged incremental diff or a full rebuild), not a list of
  per-operation algebra operations, and it does not become a list of
  `EditRequest`s.  It keeps its own path and its own efficiency.  See
  `docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` and the open item in
  `docs/AXI_EXECUTION_CONSOLIDATION.md` (AXI-034).
- **The legacy carrier's TYPE.**  It is still a public class, because
  the SDK's callers hand one over.  It is now only ever built FROM the
  canonical request.

## No live Resolve write was made

This lane made no Resolve write and read no captain project.  The proof
is the recorded-state one: the differential between the two doors, the
byte-identity of the legacy door's patch, and the measurement over the
fixture corpus - all of them offline.

The selection run for this change: 2488 tests, 2486 passing, 2 failing.
Both failures are pre-existing at the base commit and unrelated to this
change (`tests/unit/resolve/test_resolve_axi.py::test_source_carries_no_cursor_moving_call_outside_writes`
and `tests/unit/tools/test_refusal_taxonomy.py::test_the_inventory_measures_the_five_surfaces_with_denominators`
- both fail on a pristine copy of the base commit too).
