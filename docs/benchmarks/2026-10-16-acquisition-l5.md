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

## Live-producer calibration (2026-10-16, follow-up)

Downloads approved with conditions (inbox 001): download only what is
benchmarked, quantized 4/8-bit for 2B+ models, full precision for
small ones, delete after unless adopted, record precision,
research-only weights never shipped. Nothing was downloaded: every
weight below was already cached (`HF_HUB_OFFLINE=1` enforced it), and
no Resolve binary was touched. Runner:
`library/tools/acquisition_l5_live.py`, foreground under the
`query_refinement` machine lock. Source: Apple 001 IMG_1812 read-only,
six stills at the EVAL-04 sample times (15-65 s) into outside-worktree
scratch.

### Machine-measured detector points (this machine)

| Producer | Cold | Warm per frame | Peak RSS | Precision |
|---|---:|---:|---:|---|
| Vision nominations (system) | 1.5 s first frame | 1.1-2.7 s (mean 1.6) | 0.03 GB | system, no weights |
| OWLv2 named queries (cached `google/owlv2-base-patch16-ensemble`) | 42.9-50.2 s (load + infer) | 6.3-12.5 s, iPhone full-res | 0.45-0.52 GB | torch.float32, full precision (small-model rule) |

The catalog keeps its controlled-set planning figures (Vision 1.6 s,
OWLv2 36.4 s per 10 s window); the reproduction ranges above are now
annotated on both entries. Cheap-first ordering is unchanged: Vision
stays the cheapest eligible tier on every lane it covers.

### Live loop: plan, execute, escalate, merge, re-evaluate

Query "footage where backpack and laptop visible" (2.6 min locked
run): round 1 ran the planned Vision producer per window (1/12 within
the 1.6 s budget, 11 deferred over budget by 0.1-1.1 s - expected
single-call variance, each named in its step reason); round 2 ran
`plan_escalation` output with the OWLv2 producer inside its 36.4 s
budget (6/12 acquired, 6 deferred with no detections). Ten
`object.detection` rows merged; requery: backpack PRESENT, laptop
inconclusive (point samples cannot prove absence - the correct
non-answer). Backpack windows (45/55/65 s) overlap the EVAL-04
nominated spans on the same source, cited as consistency, not as a
new quality claim (detector quality stays owned by vep-mu-detect).

Live ACQ-024: utility 0.0 to 0.5 over 165.7 s of extra inference =
**0.0030 useful answers per second**. Spent and RSS are measured;
utility is requirement satisfaction on this query, not a labeled
recall score.

Two loop defects found and fixed live: producers ran a heavier tier
than the plan budgeted (now dispatched by planned producer id), and
selective escalation had no scheduler (new `plan_escalation`:
second-round steps only for still-open requirements, reusing prior
windows, ledger-aware, budget-checked).

### Live verification through the verifier path (2026-10-16)

Query "the moment she covers her mouth" parses to a hand_to_face
verify task (possessive-tolerant patterns added after the first live
attempt showed "covers her mouth" matching nothing). One span,
IMG_1808 3.5-5.5 s, statement "touches their face or hair with a
hand", M2 built into outside-worktree scratch (29 frames, decode
1.6 s), Gemma 4-bit in-process (server unreachable, loader message
says ~7 GB resident; load took 99.8 s against its ~6 s claim):

| Item | Measured |
|---|---|
| Verdict | yes (frame 2, t=4.835 s: "touching their ears/hair with both hands"), fresh-recorded in scratch, reusable by key |
| VLM inference | 972.4 s for 4 frames, single sample |
| CPU peak RSS | 0.41 GB (does not count the ~7 GB unified-memory residency) |

Consistency, not a new quality claim: EVAL-04's bounded checks on the
same span returned "no" for face-touch/cap-adjust wordings; this
statement names hair explicitly and the model points at hair. Both can
hold. The 972 s inference is an order of magnitude above EVAL-04's
4.6-13.3 s range on the same model - first-inference compilation
and/or machine contention are suspected but unattributed, so the
catalog keeps the EVAL-04 figure and this sample is NOT a planning
number until repeated. The verifier path itself (plan, execute,
acquire, ledger, refresh untouched per the M8 architecture) is now
proven live; a repeat that separates load, compile, and steady-state
inference is the follow-up.

## What this lane leaves undone

- Detector quality claims on new labels (owned by vep-mu-detect;
  their label request is out; this lane cites MU-15/EVAL-04 numbers
  only).
- Labeled answers-per-second on podcast / Apple 001 tasks (needs
  `acquisition_benchmark` task ledgers with gold ids; the planner
  emits compatible `ledger_key` identities).
- True useful-answers-per-second on labeled podcast / Apple 001 tasks;
  `marginal_gain_per_second` is implemented and unit-tested but has no
  labeled input yet. No new gold labels were created, so no
  label-request was filed.
- Edit-operation linkage uses `evidence_ids` as required by the
  current schema; `query_result_id` / `evidence_revision` wiring waits
  for PR 2611 to land (this branch does not stack on its stale base).
- Camera-motion and framing-stability measurements have no migrated
  lane (`no-lane-yet`); technical constraints without numbers abstain.
