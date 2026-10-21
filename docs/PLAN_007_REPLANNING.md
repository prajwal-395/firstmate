# PLAN-007: an evidence-driven replanning loop

The punch list asks (PLAN-007) for a replanning loop that is driven by
EVIDENCE:

> - Return failed acceptance checks, routing refusals, and missing
>   evidence to the planner.
> - Revise only the affected part of the plan where possible.
> - Preserve work that has already been verified.
> - Avoid repeating expensive model calls with unchanged context.
> - Stop when the remaining problem genuinely requires user judgment.

This is that, and the measurement on the eight-brief benchmark and on
the sealed corpus (BASE-003).

## The defect it closes

Three refusals were already computed and none of them reached the
planner:

- the feasibility gate (PLAN-005) refuses a plan that cannot run on the
  timeline it names, and computes a revision - **a proposal** that no
  front door adopted;
- the grounding stage (PLAN-003) answers for every target role a plan
  names, and its answer - a verified identity, a set of candidates, or
  nothing at all - stayed inside the receipt;
- the acceptance harness says which acceptance check a plan failed, and
  which criterion it did not declare covered.

So a plan that failed came back as a refusal with no way forward. The
planner either re-planned the whole plan or did not re-plan at all, the
work that had already been verified was paid for again, and the
expensive model call was made once per piece of evidence rather than
once for the part that actually changed.

## What was built

- `library/tools/plan_revision.py` - the loop. One evidence vocabulary,
  one revision receipt, one cache:

  | kind | what it proves | what the loop does |
  |---|---|---|
  | `impossible` | the operation cannot run here | the operation is dropped (mechanical, no model call) |
  | `not_grounded` | the target was not established | re-target at a verified identity, or ask, or ask the planner |
  | `not_covered` | an acceptance criterion is not covered | the planner revises that part |
  | `check_failed` | an acceptance check failed | the planner revises that part |
  | `missing` | the evidence was never gathered | reported as missing evidence; nothing is removed |
  | `resolved` | a verified identity for a role | the operation is re-targeted at it |
  | `preserved` | the operation was already carried and verified | it survives every revision |

  The five states a revision ends in are `revised`, `no_change`,
  `needs_judgement`, `needs_capability` and `needs_evidence`.

- `library/tools/plan_feasibility.py` - the gate now **adopts** the
  revision it computes: `PlanInfeasible` carries the refusal
  (`category`, `recovery_class`, `reasons`) AND the narrow revision
  (the operations that survive, the ones dropped, and what the drop no
  longer covers). The message still says the plan is infeasible before
  staging, and now also says what a revision would leave. It also
  carries `items_from_clips`, the state a re-targeted identity is
  checked against: the recorded clip records the grounding stage reads
  are the items the timeline really has.

- `library/tools/resolve_axi_core.py` - the gate, on the path that
  stages, raises that refusal. The front door catches a `ValueError`,
  and `PlanInfeasible` is one.

- `library/tools/target_grounding.py` - the evidence attempt now records
  whether a MODEL was there to answer. A surface that was asked with no
  model to answer it carries nothing, and the loop has to know the
  difference between "the footage does not carry this" and "nobody
  looked".

- `library/tools/editorial_acceptance/replanning.py` - the measurement on
  the eight-brief benchmark:
  `python3 -m library.tools.editorial_acceptance.replanning --records
  tests/live/reports/editorial-bench-records`.

- `evaluation/tools/replanning_corpus.py` - the measurement on the sealed
  corpus, outside the engine, so the answer key stays out of its reach.

## The four rules, and how each one is kept

**Revise only the affected part.** An operation that no piece of
evidence names is preserved byte for byte, and the planner is shown only
the affected operations. A planner that answers with an operation no
evidence bore on is REFUSED: that is a new plan, not a revision.

**Preserve work that has already been verified.** An operation the
evidence says was already carried survives every revision, and a
refusal that names it does not remove it.

**Do not repeat an expensive model call with unchanged context.** The
context of a revision is the affected operations plus the evidence that
bears on them, and it is fingerprinted. A fingerprint the loop has
already answered is answered from the cache, and the receipt says how
many calls that saved.

**Stop when the remaining problem genuinely requires user judgment.** A
target the footage does not carry ends in a QUESTION, a missing engine
route ends in a named CAPABILITY GAP, and a target nobody has looked
for ends in a named MISSING EVIDENCE. None of them is a silent drop.

## The measurement on the eight-brief benchmark

Over the eight recorded briefs (`tests/live/reports/editorial-bench-records`),
read with the same harness that scores the benchmark:

| | before | after |
|---|---|---|
| briefs | 8 | 8 |
| briefs that planned an operation | 5 | 5 |
| plans carrying a refused operation | 3 of 5 | **0 of 5** |
| plans with an operation left to stage | 5 of 5 | **3 of 5** |
| operations in the revised plans | 11 | **5** |
| planner calls (one per piece of evidence) | 38 | **5** |
| planner calls (one per affected operation) | 10 | **5** |
| a repeat of the same run | 5 calls | **0 calls** |
| **planning** (the coverage gate) | **4 of 8** | **4 of 8** |

The five plans, one number each:

| brief | the loop's state | why |
|---|---|---|
| `name-bar-fights-the-captions` | `revised` | the engine has a route for `overlay.position` now (AXI-008), and the grounding stage verified the name bar against the recorded timeline: the operation is re-targeted at the clip's own id and the plan stands |
| `open-with-the-cost` | `needs_judgement` | the request's own referent - what the problem costs - is not in this reel; the operation survives, and the plan waits for the user's word |
| `the-whole-job` | `needs_judgement` | five of its seven target roles are not in the footage, and four of its operations still have no route: 3 of the 7 survive, 4 are named as capability gaps, 5 questions are asked |
| `the-wrong-caption` | `needs_judgement` | the card that names the wrong word does not exist in this reel, and `caption_fix` still has no route |
| `two-voices-at-once` | `needs_judgement` | there is no overlapping speech and no second angle of the same moment, and `multicam.select_angle` still has no route |

The three briefs that planned nothing are untouched: no evidence bore on
a plan that does not exist, and they are not in the five.

### Reading the numbers honestly

- **"0 of 5 plans carry a refused operation" is not "5 of 5 plans are
  good".** The five plans end with five operations between them, not
  eleven: four operations the engine still has no route for are dropped
  and named as capability gaps, and the rest wait on a target the
  footage does not carry. The "operations in the revised plans: 11 -> 5"
  row and the "plans with an operation left to stage: 5 -> 3" row are
  the honest companions to the first one, and all three are reported.
- **The engine's CURRENT verdict decides what can run, not the run's
  historical refusal.** The records are from 2026-10-20, when
  `overlay.position` had no production route; AXI-008 has since
  qualified it. The loop therefore reads the engine's own feasibility
  verdict, measured now, and a run's recorded refusal contributes its
  PREMISE (the run said the request needs the user's word) rather than
  a route the engine has since gained. A plan is judged against the
  engine as it is, not as it was.
- **The planning number does not move: 4 of 8 before and after.** The
  loop runs after the planning stage and rewrites nothing the planning
  stage reads, so a plan the engine revises keeps the planning credit
  of the plan the run made.
- **The model-call saving is counted, not claimed.** The two baselines
  are both stated: a loop that re-plans once per piece of evidence
  (38 calls) and a loop that re-plans once per affected operation (10
  calls). The loop makes 5: one for the affected part of each plan, and
  none at all where the answer is mechanical. A repeat of the same run
  over the same cache costs **0** calls, which is the unchanged-context
  rule doing its work.
- **The measurement replays the recorded run.** The planner in this
  measurement is the run the record holds: the record says which roles
  the run resolved and which it did not, and that is the answer a model
  asked to establish a target produces. The measurement says so in its
  own report, so no reader mistakes it for a model call.

## The measurement on the sealed corpus

Thirteen briefs, thirteen plans (`evaluation/tools/replanning_corpus.py`):

| | before | after |
|---|---|---|
| briefs | 13 | 13 |
| briefs that plan an operation | 8 | 8 |
| plans whose every role grounds to one measured item | 3 of 8 | 3 of 8 |
| plans stopped before staging, with a question | 0 of 8 | **5 of 8** |
| plans with an operation left to stage | 8 of 8 | **3 of 8** |
| planner calls (one per planned operation) | 11 | **5** |
| a repeat of the same run | 5 calls | **0 calls** |

The corpus's own evidence is the answer key, and it says the same thing
the benchmark says from the other side: five of the eight plans name a
target the substrate does not carry, and the right outcome for those is
a QUESTION, not a plan. The three plans whose roles ground keep their
plan and cost no model call at all.

## What was left undone

- **No live run.** Both measurements are over records. The loop is
  wired into the live write path only as far as the feasibility gate
  (`PlanInfeasible` carries the revision); the grounding receipt and
  the acceptance measurements are not yet fed into the live path,
  because the live path has no acceptance measurement to feed.
- **No front-door verb.** The loop is reachable as
  `python3 -m library.tools.plan_revision --plan <file> --evidence
  <file>`, beside the two measurement CLIs, which is where PLAN-003 and
  PLAN-005 put their measurements too. A `ren replan` verb would need a
  second registration in the native CLI, and nothing asked for it.
- **The cache is in memory.** `RevisionCache` is a dict with
  `as_dict()`, so a caller can persist it, but nothing persists it
  across processes yet.
- **The planner's answer shape is a proposal.** The loop accepts
  `operations`, `questions`, `unresolved` and `covers`; no production
  model call fills it in, because the measurement stands in for the
  model. A caller with a model supplies the same shape.
- **Fifteen red tests on main, from other lanes.** AXI-008 (PR 3042)
  qualified `overlay.position` on the carriers a real reel carries, and
  the PLAN-005 tests that assert the benchmark's operations have NO
  production route now fail: seven in
  `tests/unit/resolve/test_plan_feasibility.py` and four in
  `tests/unit/tools/test_editorial_feasibility.py`. Four more predate
  this lane (`test_revise_request_identity.py`,
  `test_edit_execution_adapters.py`, `test_mutation_verification.py`).
  All fifteen fail on `origin/main` before this branch, and this lane's
  measurement reads the engine as it is rather than as those tests say
  it was. They were left alone: the house rule is a dedicated "fix the
  red tests on main" change, and fixing them here would change what
  the PLAN-005 measurement means.
