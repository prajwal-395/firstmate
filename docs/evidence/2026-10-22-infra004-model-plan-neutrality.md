# Evidence - INFRA-004: the model-facing plan was bound to one NLE

**Measured on:** 2026-10-22, repository at `2f5267cd2` (before) and this
branch (after), on the same committed corpus.
**Population:** the recorded reel-26 timeline export and the 26
recorded requests addressed to its clips
(`tests/fixtures/intent_neutrality/corpus.json`).  Read only; nothing
in a project was written, no Resolve session was taken and no broker
was started.
**Metric:** the fields of the MODEL-FACING plan whose key names a
Resolve object and whose value is a non-empty string, over the fields
of the same record - and the router-input digest of every recorded
request, before and after.

## The incident

PR #3156 took the intent record and the machine plan out of Resolve's
hands and wrote down what it left undone:

> The model-facing plan surface was not changed.  `edit_spec._timeline_rows`
> still shows the model the recorded `unique_id`s, and the prompt still
> asks for one.

The surface left was the one the MODEL reads and writes.  Three places
carried a Resolve object id:

1. **The rows the model reads.**  `edit_spec._timeline_rows` listed
   every recorded `unique_id` beside every item - 26 ids over the
   recorded reel.
2. **The prompt.**  It told the model to "resolve each target to its
   observed `unique_id` or other exact registry address" and, for the
   opening, that its `params` "must contain `unique_id` for the opening
   anchor clip".
3. **The plan the model writes.**  The recorded `edit_program` row
   carried the id the model wrote back - one per recorded request.

A `unique_id` is Resolve's `GetUniqueId()` of a timeline item: an
EXECUTION ADDRESS.  It changes when the reel is rebuilt and means
nothing to another NLE.  A creative plan that carries one is a plan for
one instance of one application - and, to the model that has to write
it, an opaque token with nothing to reason about.

## How it was measured

One corpus, two revisions, the same script, the same denominator rule.

```sh
python3 scripts/measure_model_plan_neutrality.py \
    --corpus tests/fixtures/intent_neutrality/corpus.json
```

* **Before**: `2f5267cd2`, extracted with `git archive` into a scratch
  directory OUTSIDE the worktree.  The script runs on that revision
  because it reads the enumeration of Resolve-specific fields itself,
  not through the module this lane adds.
* **After**: this branch.
* **A field** is one key of one mapping, at any depth.  A **Resolve-
  specific field** is a field whose KEY is one of `unique_id`,
  `resolve_item_id`, `media_pool_item_id`, `media_pool_item`,
  `timeline_id` and whose VALUE is a non-empty string.
* **The model-facing CONTEXT** is the rows `edit_spec._timeline_rows`
  builds - what the model is shown.
* **The model-facing PLAN** is the program the engine RECORDS after it
  reads the model's answer - what the model wrote, translated.
* **The route** is the router's input: the compiled operations, their
  order and their kinds, digested.

## The numbers

| | before (`2f5267cd2`) | after |
|---|---|---|
| model-facing context: Resolve-specific fields | **26 / 340** | **0 / 314** |
| model-facing plan: Resolve-specific fields | **26 / 442** | **0 / 494** |
| recorded requests prepared | 26 / 26 | 26 / 26 |
| router input digest of the recorded requests | `786dbb02b257863b` | `786dbb02b257863b` |
| the same requests written WITHOUT an instance id | not supported | 26 / 26 resolved by address |
| router input digest of those requests | - | `786dbb02b257863b` |

The denominators are the fields of the same record, counted by the
same rule in both columns.  They move because the records say more: the
context now carries the row and the frames beside the material, and the
recorded plan carries the address where it used to carry one id.

**The route is unchanged.**  The digest of the recorded requests is the
same on both revisions, and the digest of the SAME requests written
without an instance id is that same digest: the plan says WHAT it acts
on, the engine binds the material to the instance the recorded reel
holds today, and the router receives the operations it always received.
On the revision before this lane, a request written without an instance
id could not be prepared at all - it was refused for a missing
`unique_id`.

The sibling measurement (`scripts/measure_intent_neutrality.py`) still
holds on this branch: the intent record carries 0 / 910 Resolve-
specific fields, the machine plan 0 / 2470, and the 18 recorded live
requests keep the router-input digest `4110ce008cbf2bd0`.

## What the lane changed

* `library/tools/intent_neutrality.py` - the address vocabulary:
  `address_of`, `instances_for_address`, `address_in`,
  `MINTED_IDENTITY_OPS`, `ADDRESSED_MULTI_TARGET_PARAMS`.
* `library/tools/edit_spec.py` - the rows the model reads, the prompt,
  the translation of the model's answer, and the validation that
  accepts an address where an instance id used to be.
* `library/tools/edit_preparation.py` - the resolution of an addressed
  target against the recorded reel, and `bind_request`.
* `library/tools/edit_request.py` - the target carries the placement,
  and an address is enough to satisfy an operation's `unique_id`.
* `library/tools/resolve_axi_core.py` - the executor compiles the BOUND
  request, so the router sees the same operations.
* `library/tools/revise.py` - the classification and the pre-flight
  checks read the address.
* `library/tools/edit_ledger.py` - the recorded program validates with
  an address, and its replay resolves the address against the live
  timeline.
* `library/tools/edit_execution_adapters.py` - the opening's nested
  edits resolve their addresses.
* `scripts/measure_model_plan_neutrality.py` - the measurement.

## What the lane left undone

* **The recorded live receipts still carry instance ids.**  A receipt
  is the BACKEND's record of what it executed, and it names the
  instance it executed.  The model-facing plan is neutral; the receipt
  is the backend's own vocabulary, and rewriting recorded evidence
  would rewrite history.
* **A recorded clip with neither a material nor a placement cannot be
  addressed neutrally.**  Its id is kept, and the plan says so.
* **No live run.**  The measurement is over committed recorded
  evidence: no Resolve session was taken, no broker was started and
  nothing in the captain's projects was touched.
