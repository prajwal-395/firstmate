# L5 query-directed acquisition scorecard (2026-10-16)

Lane 5 of the Iteration 1 media information model: typed requirement
graph plus query planner on the existing `evidence_query` and the
merged adaptive-acquisition code, with producer selection through the
model registry and memory admission. Covers ACQ-001..031 (minus live
model execution) and RET-027..037.

## What was built

- `library/tools/requirement_graph.py`: deterministic parse of a user
  query into seven typed families (speech, image, action,
  relationship, sound, ocr, technical), with conjunction (RET-028),
  temporal order edges (RET-029), negation compiled to positive plans
  read for absence (RET-030, coverage-aware per RET-031), camera /
  quality constraints (RET-032), spatial relations (RET-033) and the
  audio-visual join (RET-034). Unmapped verbs and numberless technical
  floors compile to explicit `unanswerable` records, never guesses.
- `library/tools/evidence_acquisition.py`: existing-evidence-first
  orchestration. Checks the model, names missing lanes, estimates
  cost, picks the cheapest license- and memory-eligible producer,
  scopes work to retrieval / conjunction-narrowed windows, dedups by
  ledger, ranks by selectivity per second, records why each step runs,
  merges through the caller refresh, re-evaluates, and stops on
  sufficiency or budget. Verification tasks defer without a caller
  verifier. Summaries carry intervals and ids only; detail is fetched
  per evidence id on request.
- `library/tools/evidence_query.py`: two mapping additions only.
  OCR text/graphic tracks now resolve to the `ocr_extraction` lane, so
  an OCR plan without coverage reads `not_yet_analysed` instead of
  claiming absence with no required lane.
- `library/tools/acquisition_l5_calibration.py`: dry-run harness.
  No model loads, no media reads, no producer executes.

## Method and split discipline

Planner settings (default selectivity priors, top-3 windows,
cheap-first ordering, 8 GB RSS ceiling, permitted-research purpose)
were frozen on the six calibration queries below. The four held-out
queries ran once, after the freeze, and were not used to adjust
anything. Fixture models carry three transcript words so conjunction
narrowing and reuse have real evidence to work with; odd-index
queries run against an empty model. Costs are catalog figures:
`measured` steps use checked-in points (M4 90 s/window, OWLv2
3.64 s/frame, Vision 0.16 s/frame, span verification 5-13 s/span);
zero-cost steps are migrated lookups with no marginal inference.

The dense reference per query is the costliest standard-or-heavier
eligible producer per missing lane across the full 120 s source with
no narrowing and no reuse.

## Calibration (settings chosen here)

| Query | Reqs | Planned steps | Planned cost | Dense cost | Reuse avoided |
|---|---:|---:|---:|---:|---|
| speaker discussing revenue while a chart is visible | 2 | 1 | 1.6 s | 436.8 s | 1 |
| footage with no visible people | 1 | 1 | 0.0 s | 0.0 s | 1 |
| person opens the box and then shows the product | 3 | 14 | 19.2 s | 436.8 s | 2 |
| text read revenue growth appears on screen | 1 | 1 | 0.0 s | 0.0 s | 1 |
| laughter during the applause | 2 | 2 | 0.0 s | 0.0 s | 2 |
| person on the left holding the product | 4 | 15 | 45.2 s | 436.8 s | 1 |
| **Total** | **13** | **34** | **66.0 s** | **1310.4 s** | **8** |

Unanswerable: 1 (`opens` has no verified candidate kind). Needs
verification: 2 (both spatial relations, deferred without a verifier).
Contradictions: 0. The 1.6 s chart step lands inside the 10-20 s
revenue interval by conjunction narrowing, not across 12 full-source
windows (19.2 s without it).

## Held out (run once, 2026-10-16)

| Query | Reqs | Planned steps | Planned cost | Dense cost | Reuse avoided |
|---|---:|---:|---:|---:|---|
| visible speaker with synchronized dialogue | 2 | 2 | 0.0 s | 0.0 s | 2 |
| static wide shots with low motion | 3 | 0 | 0.0 s | 0.0 s | 0 |
| she picks up the phone | 2 | 13 | 32.2 s | 436.8 s | 1 |
| speaker discusses pricing | 1 | 1 | 0.0 s | 0.0 s | 1 |
| **Total** | **8** | **16** | **32.2 s** | **436.8 s** | **4** |

Unanswerable: 3 (all numberless technical floors, zero cost
scheduled). Needs verification: 1 (pickup candidate, deferred).
The technical query abstaining at zero cost is the intended RET-031
behavior: no calibrated floor exists, so no detector work is
scheduled to fake one.

## Incumbent comparison

The incumbent (`project_model_acquisition.query_project`) acquires M4
scene windows only, at 90 s per 10 s window across the query scope,
with no requirement graph, no producer choice, no license or memory
gating, and no ledger dedup. On the calibration set it cannot answer
the OCR, object, sound, or relation requirements at all (no lane
coverage), and where it applies (scene-level queries) its full-scope
cost dominates: e.g. query 1 would cost 12 windows x 90 s = 1080 s
against the 1.6 s targeted plan above. Quality, cold/warm latency,
and peak memory for live producers are not measured here: no producer
executed in this lane (model downloads await the vep-mu-downloads
decision), so end-to-end answers-per-second awaits labeled producer
ledgers in `acquisition_benchmark` format. The planner emits
`ledger_key` identities compatible with that scorer.

## What this lane leaves undone

- Live producer execution and measured quality/latency/memory
  scorecards (blocked on the vep-mu-downloads decision; only then can
  `verify-bounded-span` and detector tiers run against real footage).
- True useful-answers-per-second on labeled podcast / Apple 001 tasks;
  `marginal_gain_per_second` is implemented and unit-tested but has no
  labeled input yet. No new gold labels were created, so no
  label-request was filed.
- Edit-operation linkage uses `evidence_ids` as required by the
  current schema; `query_result_id` / `evidence_revision` wiring waits
  for PR 2611 to land (this branch does not stack on its stale base).
- Camera-motion and framing-stability measurements have no migrated
  lane (`no-lane-yet`); technical constraints without numbers abstain.
