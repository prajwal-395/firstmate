# INFRA-004 - editorial intent measured against Resolve

2026-10-21. The punch list's INFRA-004: "Keep editorial intent independent
from Resolve", with "Maintain NLE-neutral operation semantics", "Map each
operation to the appropriate supported backend", "Record capability
mismatches explicitly", and "Avoid embedding Resolve-specific commands in
high-level creative plans".

The measurement asked for by the lane: **the count of intent/plan fields
that carry Resolve-specific identifiers, before vs after**, and **that
today's recorded requests still route and execute identically**.

## How it was measured

One corpus, two revisions, the same script, the same denominator rule.

```sh
python3 scripts/measure_intent_neutrality.py \
    --corpus tests/fixtures/intent_neutrality/corpus.json
```

* **Before**: `5ea12e79a` ("AXI-002: one semantic request has one
  auditable routing decision (#3155)"), extracted with `git archive` into a
  scratch directory OUTSIDE the worktree, with the same script and the
  same corpus copied in.  The script runs on that revision because it
  reads the enumeration of Resolve-specific fields itself, not through
  the module this lane adds.  The numbers are the same on every base
  where the engine imports - `114488d88`, `18d183c2b` and `5ea12e79a` all
  measure 26/832 and 52/1196 - and they could NOT be re-measured at
  `062c1d073`, because the qualification registry on main names three
  operation/backend pairs twice and the loader refuses such a file by
  design (see "What the lane found on main", below).
* **After**: this branch.
* **A field** is one key of one mapping, at any depth.  A **Resolve-
  specific field** is a field whose KEY is one of `unique_id`,
  `resolve_item_id`, `media_pool_item_id`, `media_pool_item`,
  `timeline_id` and whose VALUE is a non-empty string.  An empty value is
  an absent identifier, not one.
* **The corpus** is committed recorded evidence, not a scenario written
  for this lane:
  * `tests/fixtures/reel_hearing/reel26.timeline.json` - a recorded
    export of one reel's timeline: 26 clips, each with the Resolve item
    id it had on the day and the source range it played.  26 requests are
    addressed to those recorded clips (one per clip).
  * `tests/live/reports/**` - the recorded live receipts: 18 recorded
    requests, each carrying the operations it sent.

## The numbers

### The intent record and the machine plan

| record | before | after |
|---|---|---|
| intent record (`EditRequest.to_dict()` / the neutral intent) | **26 / 832** fields carry a Resolve identifier | **0 / 910** |
| machine plan (`edit_preparation` plan) | **52 / 1196** fields carry a Resolve identifier | **0 / 2470** |

Denominators are the fields in the same record, counted by the same rule
in both columns.  The denominator GROWS in the "after" column because the
neutral records say more: a target now carries its material (source file,
source range, row, frames) where it used to carry one opaque id, and the
plan carries the neutral intent beside it.

The 52 fields in the "before" plan are 26 steps' targets, 26 steps'
parameters, and the affected scope's clip list.

### Today's recorded requests still route identically

Every recorded request in the corpus is rebuilt and compiled, and the
router input is digested:

| | before | after |
|---|---|---|
| recorded requests routed | 18 / 18 | 18 / 18 |
| router input digest | `4110ce008cbf2bd0` | `4110ce008cbf2bd0` |

The digest is identical, so the router receives the same operations in the
same order, with the same kinds, for every recorded request.  The
recorded requests route and execute identically.

### What the corpus could not state

The 18 recorded live requests name their targets by a Resolve item id and
carry no recorded material beside it, so the neutral intent for them
records a `unresolved_target` mismatch rather than a material address.
That is the honest reading of the recorded evidence: **a request recorded
before this lane says WHICH INSTANCE it acts on and not WHAT MATERIAL**,
and the adapter has to resolve the instance against the timeline to say
the material.  The mismatch is named, not swallowed.

## What the lane changed

* `library/tools/intent_neutrality.py` - the boundary: the enumeration of
  Resolve-specific fields, `ItemRef`, `neutralize`, `bind`,
  `capability_mismatches`, `measure`, and the corpus builder.
* `library/tools/edit_preparation.py` - the machine plan states each step
  by its material, and names the mismatches it could not state.
* `library/tools/edit_request.py` - `EditRequest.to_neutral()`.
* `scripts/measure_intent_neutrality.py` - the measurement, written so it
  runs on the revision BEFORE the module existed.
* `tests/fixtures/intent_neutrality/corpus.json` - the corpus.

## What the lane found on main

At `062c1d073` (the base this branch was last cut from) the ENGINE does
not import on main:

    library.tools.axi_qualification.QualificationReportError: the
    qualification registry names 'clip.delete::resolve_axi' twice: a
    reader cannot be shown two rows for one operation/backend pair

`library/tools/axi_qualification_records.json` names three pairs twice -
`clip.delete::resolve_axi`, `mask.create::edit_patch` and
`retime.segments::edit_patch` - because #3164 added the rows measured
against an unambiguous timeline without replacing the rows they
supersede.  The loader's own rule is "a file with one row per pair", and
which measured row survives is the qualification lane's decision, not
this lane's.  The numbers in this report were therefore measured on the
last base where the engine imports, and they are the same on every base
where it does.

## What was left undone, and why

* **The model-facing plan surface still shows Resolve ids.**
  `edit_spec._timeline_rows` hands the model the recorded `unique_id`s and
  the prompt asks for one.  Changing that changes what the model writes,
  and the adapter that resolves a material-addressed target back to an
  instance belongs to the AXI-002 router lane, which is running now.
  This lane gives that lane the boundary to bind against.
* **No backend mapping was added.**  `capability_mismatches` reads the
  ROUTER's own admission judgment (`execution_router
  .route_admission_reasons`, AXI-002), so the mismatch report and the
  route agree by construction; choosing the backend stays with the
  router.
* **No live run.**  The measurement is over committed recorded evidence,
  and no Resolve session was taken.
