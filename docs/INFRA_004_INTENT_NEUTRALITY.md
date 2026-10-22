# INFRA-004: editorial intent that is independent of Resolve

**Punch list:** "Ren - Comprehensive Development Punch List" (2026-10-20,
baseline 97b57197), section INFRA: "Keep editorial intent independent from
Resolve."

**Lane:** `p-infra004-intent-independent`.

**What this lane owns:** the boundary between WHAT WAS ASKED and WHAT RUNS
ON AN NLE.  The intent record and the machine plan state an edit in terms
no NLE owns - the material, the row, the frames - and the Resolve
identifiers live in the adapter, beside the intent, where the backend can
read them and the plan cannot.

**What this lane does NOT own:** the router's backend choice (the AXI-002
lane), the operation algebra, and the executor.  This lane changes what a
PLAN says, not which route it takes.

---

## The defect this lane names

Before this lane, the two records that state an edit were bound to one NLE:

| Record | Where | What it carried |
|---|---|---|
| The intent record | `EditRequest.to_dict()` | `unique_id`, `resolve_item_id`, `timeline_id` |
| The machine plan | `edit_preparation._machine_plan` | the same ids, in every step's `target` and `params`, and in the affected scope |

A `unique_id` in Ren's vocabulary is Resolve's `GetUniqueId()` of a
timeline item.  It is an EXECUTION ADDRESS: it is short-lived, it changes
when the timeline is rebuilt, and it means nothing to another NLE.  A plan
that carries one is a plan for one instance of one application - which is
exactly what the punchlist's "avoid embedding Resolve-specific commands in
high-level creative plans" forbids, and what "keep editorial intent
independent from Resolve" asks for.

## What the lane added

### 1. The boundary module: `library/tools/intent_neutrality.py`

* `RESOLVE_SPECIFIC_FIELDS` - the ONE enumeration of the fields whose
  value IS a Resolve object identifier: `unique_id`, `resolve_item_id`,
  `media_pool_item_id`, `media_pool_item`, `timeline_id`.
* `ItemRef` - the NLE-neutral address of one item: the MATERIAL it plays
  (`source_file` + source range), the row, and the frames it occupies.
  The material half is the identity `timeline_addressing` already compares
  across backends.
* `neutralize(request, refs=...)` - the intent record with every Resolve
  identifier replaced by the neutral address of the material it names.
* `bind(intent)` - the ADAPTER's other half: the neutral intent plus the
  bindings gives back the request the backends execute, byte for byte.
* `capability_mismatches(intent, backend)` - what a backend cannot
  express, named.  The judgment is NOT this module's: it reads the
  execution router's own admission judgment
  (`execution_router.route_admission_reasons`), because the router is the
  one place that decides which backend may run an operation (AXI-002).  A
  report that judged for itself could disagree with the route the request
  will actually take - and then it would be a second, quieter router.
* `measure(records)` / `build_corpus()` - the measurement itself.

### 2. The live wiring: the machine plan is neutral

`edit_preparation._machine_plan` now states each step by its material:

```python
"target": {"name": "a.mov", "track": "V1", "record_span": [0, 48],
           "item_ref": {"source_file": "/media/a.mov",
                        "source_in_frame": 12, "source_out_frame": 60,
                        "track_type": "video", "track_index": 1,
                        "record_in": 0, "record_out": 48,
                        "name": "a.mov", "track_role": ""}},
"params": {"key": "Pan", "value": 5.0}
```

The instance handles the executor needs are still recorded - in
`PreparationResult.resolved`, which is the RESOLUTION EVIDENCE, and which
the receipt reads.  They are no longer inside the plan.

### 3. Capability mismatches are explicit

A target the recorded timeline cannot state neutrally, and an operation
whose own semantics need a Resolve instance, are both recorded in the
plan's `capability_mismatches` and in the intent's `mismatches`, BY NAME:

* `unresolved_target` - the target is named by a Resolve object id and the
  material it stands for was not recorded.
* `instance_only_operation` - the operation carries a Resolve object id
  (a media pool item), so the backend has to mint the instance.
* `backend_unsupported` - the backend does not list the operation as
  compilable.

Nothing is dropped silently and nothing is guessed.

### 4. The measurement: `scripts/measure_intent_neutrality.py`

One corpus, two revisions, the same denominator rule.  The corpus is
committed recorded evidence: the recorded reel-26 timeline export
(`tests/fixtures/reel_hearing/reel26.timeline.json`, 26 clips with their
recorded Resolve ids and source ranges) and the recorded live receipts
(`tests/live/reports/**`, 18 recorded requests).  The corpus itself is
committed as `tests/fixtures/intent_neutrality/corpus.json`.

```sh
python3 scripts/measure_intent_neutrality.py
```

The report and the numbers are in
[`docs/benchmarks/2026-10-24-infra004-intent-neutrality.md`](benchmarks/2026-10-24-infra004-intent-neutrality.md).

## The two things this lane did NOT do

* **The backend mapping was not re-derived.**  `capability_mismatches`
  reads the router's declared admission table, so the mismatch report and
  the route agree by construction.
* **The model-facing plan surface was not changed.**  `edit_spec._timeline_rows`
  still shows the model the recorded `unique_id`s, and the prompt still
  asks for one.  Changing that changes what the model writes, and the
  adapter that would resolve a material-addressed target back to an
  instance is exactly the work the AXI-002 router lane owns.  This lane
  gives that lane the boundary to bind against: `ItemRef`, `bind`, and the
  mismatch vocabulary.

  **Changed since:** the model-facing surface was changed by the lane
  that took this work on - `docs/INFRA_004_MODEL_PLAN_NEUTRALITY.md` -
  and the evidence is in
  `docs/evidence/2026-10-22-infra004-model-plan-neutrality.md`.  The
  rows now name the material, the prompt asks for the material, and the
  binding resolves it to the instance the recorded reel holds today.
* **The recorded requests were not rewritten.**  A request that carries a
  Resolve id still carries it, and still executes.  The neutral form is a
  VIEW across the boundary, and the request the router sees is the request
  that was planned - proved by digesting the router input of every
  recorded request before and after.

## What the lane found on main

At `062c1d073` the engine does not import on main: the qualification
registry names three operation/backend pairs twice, and
`axi_qualification._read_registry` refuses such a file by design ("a file
with one row per pair").  #3164 added the rows measured against an
unambiguous timeline without replacing the rows they supersede.  Which
measured row survives is the qualification lane's decision, so this lane
left the registry alone and measured on the last base where the engine
imports (`5ea12e79a`, where the numbers are the same as on every other
loadable base).

## Tests

- `tests/unit/resolve/test_intent_neutrality.py` - the intent record, the
  machine plan, the adapter's round trip, the mismatch vocabulary, and the
  measurement's own denominators.
