# The model admission gate - the history behind its contract

This is the evidence and the story of how the admission gate was found
(punchlist INFRA-007, 2026-10-20). The contract itself lives in
`library/tools/model_admission.py` and in the `admission` record of every
implementation in `library/model_registry/registry.json`; the report is
[`docs/MODEL_ADMISSION_GATE.md`](../MODEL_ADMISSION_GATE.md).

## The defect

Ren's model registry listed eighteen optional models - weights a machine may
fetch, load or route to - and said nothing about what any of them had to show
before it could be used. The licence evidence existed
(`library/model_registry/license_evidence.json`, sixty-four primary-source
records) but it answered a different question: it said what a publisher's
terms were, not whether a model was ADMITTED. The size of every download was
unrecorded, so "personal-only experimental option" and "commercially
distributable dependency" were two words in a licence field with no size, no
weight and no admission bar between them.

Beside it, `data/model_watch/candidates.json` held 77 research leads, 24 of
them at `decision.status: "candidate"`, with no route from a lead to an
admission: the watchlist said "this is a queue for evaluation", and the
registry said "this is what exists", and nothing said what happens between
the two. The punchlist (INFRA-007) named the gap: *"Establish research and
licensing gates for optional model capabilities."*

## What it cost

Nothing crashed and nothing was wrong on screen. That is exactly the shape of
the defect: a model with an unresolved licence, a research-only licence or a
16.5 GB download was one registry row away from a client's machine, and the
registry had no field in which to say so.

## The rule

Every optional model carries an `admission` record, and the gate refuses a
model whose record is missing, incomplete, or claims more than the licence
evidence allows. The record names four things: the licence, the commercial
use, the size of the weights the runtime actually loads, and the measured gain
the model must show before it is admitted.

- **The gate is FAIL-CLOSED and it refuses at the point of use.**
  `model_registry.require_implementation()` (the fetch / load / package
  authorisation that `ren.edition.require_component()` already used) and
  `model_registry.route()` both refuse a model with no record, and name the
  missing fields.
- **A record may WITHHOLD the permission the licence evidence grants; it
  may never GRANT permission the evidence does not.** The evidence, not the
  record, is what authorises a load.
- **A required gain states a THRESHOLD only where something has been
  measured.** `no_regression` (the model's own recorded value is the floor),
  `delta_over_incumbent` (10% relative over the incumbent's recorded value),
  and `establish_baseline` (nothing measured yet: name the metric, the split,
  the fixture and the harness). An unmeasured value is never a zero -
  Florence-2 failed to load in the detector bakeoff, and its `measured_value`
  is `null`, not `0`.
- **A CANDIDATE may not be adopted without a complete record.** The
  watchlist stays a lead list; a candidate that is a registered
  implementation inherits that implementation's record.
- **A PERSONAL configuration may add capabilities and may never lose a
  commercial one.** `model_admission.configurations()` refuses the second.

## The measurement

Share of the registry's optional models with a complete admission record:
**0/18 (0.0%) before** `b15d67dbb`, **18/18 (100.0%) after**. The
denominator is every implementation with `optional_download: true` - all
eighteen rows - not the models that happened to be looked at.

All eighteen recorded sizes were re-read from their publisher sources and
match (`scripts/measure_model_weight_sizes.py`, metadata only - no model was
downloaded).

## What was NOT done

No model was downloaded, benchmarked or qualified: downloads stay with the
captain's pending approval. Twelve of the eighteen records are
`establish_baseline`, because twelve capabilities have no Ren quality row
yet; each names the measurement that would produce one.

`library/tools/model_admission.py` owns the record and the gate,
`scripts/measure_model_weight_sizes.py` re-reads the publisher sources the
sizes came from, `tests/unit/context/test_model_admission.py` names each
defect, and `docs/MODEL_ADMISSION_GATE.md` is the report.
