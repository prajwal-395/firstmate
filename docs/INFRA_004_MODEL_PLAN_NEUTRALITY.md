# INFRA-004: the model-facing plan names the material, not the instance

**Lane:** `p-infra004b-model-plan`
**Punch list:** "Ren - Comprehensive Development Punch List" (2026-10-20,
baseline 97b57197), section INFRA: "Keep editorial intent independent
from Resolve."

**What this lane owns:** the surface the MODEL reads and writes.  The
sibling lane (`p-infra004-intent-independent`, PR #3156) took the
intent record and the machine plan out of Resolve's hands and named
what it left undone:

> The model-facing plan surface was not changed.  `edit_spec._timeline_rows`
> still shows the model the recorded `unique_id`s, and the prompt still
> asks for one.

This lane is that undone work.  The model-facing plan now names the
MATERIAL an edit acts on, and the engine binds the material to the
instance the reel holds today.

**What this lane does NOT own:** the router's backend choice (AXI-002),
the operation algebra, the executor, and the intent record (PR #3156).

---

## The defect this lane names

Before this lane, the model-facing plan was bound to one NLE in three
places:

| Surface | Where | What it carried |
|---|---|---|
| The rows the model reads | `edit_spec._timeline_rows` | every recorded `unique_id` |
| The prompt | `edit_spec.prepare_request` | "resolve each target to its observed `unique_id`" |
| The plan the model writes | the recorded `edit_program` row | the `unique_id` the model wrote back |

A `unique_id` in Ren's vocabulary is Resolve's `GetUniqueId()` of a
timeline item.  It is an EXECUTION ADDRESS: it is short-lived, it
changes when the reel is rebuilt, and it means nothing to another NLE.
A creative plan that carries one is a plan for one instance of one
application - which is what the punchlist's "avoid embedding
Resolve-specific commands in high-level creative plans" forbids.

The rows were not only Resolve-specific, they were UNUSABLE by the
model: a `unique_id` is an opaque token, so the model had nothing to
reason about when it wrote one back.

## What the lane added

### 1. The address: the MATERIAL, or the PLACEMENT

`library/tools/intent_neutrality.py` owns the vocabulary:

* `address_of(entry)` - the neutral address a plan states: the
  MATERIAL it plays (`source_file`, `source_in_frame`,
  `source_out_frame`) and the row it plays on; or, for an item with no
  material, the PLACEMENT it occupies (`track_type`, `track_index`,
  `record_in`, `record_out`).
* `instances_for_address(snapshot, address)` - the recorded instance
  ids ONE address names.  An address that matches more than one
  recorded item is AMBIGUOUS and the caller refuses it.
* `address_in(container)` - whether a record states an address at all.
* `MINTED_IDENTITY_OPS` - the operations whose `unique_id` is the
  identity the NEW item will carry (`clip.insert`, `broll.insert`,
  `compound.create`).  No address can replace those: the engine mints
  them, and the plan says so.
* `ADDRESSED_MULTI_TARGET_PARAMS` - the parameters that name MORE
  THAN ONE existing item (`clip.join`'s other clip, `clip.link`'s
  clips, `compound.create`'s children) and the key a plan writes their
  addresses under.

### 2. The model-facing rows

`edit_spec._timeline_rows` no longer carries `unique_id`.  Each item
carries the material it plays, the row it plays on and the frames it
occupies - and the row is filled in from the TRACK when the recorded
clip does not repeat it.

### 3. The prompt

The prompt asks for the material and the row, never for an item id, and
says what to do with an operation that names a SECOND item.

### 4. The binding

`edit_preparation` resolves an addressed target against the RECORDED
reel, and `edit_preparation.bind_request` fills the resolved instance
onto the request the router sees.  `resolve_axi_core._prepare_request_plan`
compiles the BOUND request, so the router receives the same operations
it received when the plan named the instance - which is why the route
does not depend on how the plan addressed its target.

`revise` binds at the two other places a plan's target is needed: the
classification (the router's own input) and the pre-flight checks
(`_missing_targets`, `scope_for`, `region_summary`).

### 5. The recorded plan

The engine records what the model wrote, translated: an id that names a
recorded item becomes the ADDRESS it stands for, and an address the
recorded reel does not carry is a QUESTION.  So the recorded program -
the ledger row, the durable record of the model's decision - carries no
instance id either.

`edit_ledger` replays a recorded program against a freshly built
timeline by resolving the address against the LIVE timeline
(`_live_address_id`), which is strictly more than the old id lookup
could do: an id from the old generation names nothing on the new one.

## The measurement

`scripts/measure_model_plan_neutrality.py` measures the model-facing
plan over committed recorded evidence, before and after, and proves the
route is unchanged.  The numbers are in
[`docs/evidence/2026-10-22-infra004-model-plan-neutrality.md`](../evidence/2026-10-22-infra004-model-plan-neutrality.md).

## What the lane did NOT do

* **The recorded live receipts were not rewritten.**  A receipt is the
  BACKEND's record of what it executed, and it names the instance it
  executed.  The model-facing plan is neutral; the receipt is not.
* **No live run.**  The measurement is over committed recorded
  evidence, and no Resolve session was taken.
* **The EXECUTOR's own vocabulary is unchanged.**  The algebra still
  executes operations that name an instance; the plan that reaches it
  is bound first.
