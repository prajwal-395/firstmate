# PLAN-005: pre-execution feasibility checks

The punch list asks (PLAN-005) for pre-execution feasibility checks:
ask whether the planned operations can execute on the actual timeline,
validate the prerequisite media, tracks, item identities, source handles
and effect state, check that a structural change has a supported
reconstruction strategy, estimate that the preservation constraints and
the available backend are compatible, and **reject or revise infeasible
plans before expensive staging**.

This is that, and the measurement on the eight-brief benchmark.

## The defect it closes

The engine learned that a plan could not run at the FAR END of the write
path: the request was prepared, routed, staged onto a candidate
timeline, an executor was reached, and the refusal came back from
there. The live unfamiliar-brief benchmark (PR 2989,
`docs/benchmarks/2026-10-20-unfamiliar-brief-acceptance.md`) paid for
that: the name-bar brief spent three attempts inside the staging path
to learn that `overlay.position` has an executor and no
production-enabled backend, and the whole-job brief spent seven.

The check now happens BEFORE a route is chosen, from the same registry
the router routes from, so the answer cannot drift from the route that
would have been taken.

## What was built

- `library/tools/plan_feasibility.py` - the engine. One check per
  question, each named, each reported:

  | check | the question |
  |---|---|
  | `required_parameters` | does the operation carry what it needs |
  | `operation_known` / `executor_exists` | is the operation in the vocabulary, and does anything run it |
  | `route_eligible` | is there an eligible backend under this policy |
  | `capability_query` | ASK THE ENGINE: may this operation run on THIS timeline? |
  | `qualification_predicates` | does the MEASURED runtime satisfy the recorded predicates |
  | `target_identity` | is the item the plan names on the timeline |
  | `track_presence` | is the row it lives on on the timeline |
  | `source_handle` | does its source handle carry the request |
  | `media_prerequisite` | is its media kind one the operation supports |
  | `effect_state` | is its effect state one the operation supports |
  | `reconstruction_strategy` | does a structural change have a supported way of being rebuilt |
  | `preservation_compatibility` | do the preservation constraints and the backend agree |

- `library/tools/execution_router.py` - `eligibility(op_id)` and
  `qualification_mismatches(op_id, inspected)`. The router's own matrix,
  read without routing anything.

- **The capability query is CONSUMED, not stubbed.** BASE-002 (#3044)
  landed while this lane was working: `library/tools/capability_coverage.py`
  answers "may this operation run in these conditions?" by asking the
  ROUTER, and names the blocker. `plan_feasibility.capability_query` is
  that query, and the `capability_query` check asks it with the MEASURED
  state of the timeline. The other seam the punch list asks for stays:
  `plan_feasibility.capability_facts` is the one place the engine's
  knowledge of an operation is read, so the facts (media kinds, safety
  class, predicates) and the query cannot disagree.

- `library/tools/resolve_axi_core.py` - the gate, on the path that
  stages. `_prepare_request_plan` now assesses the compiled plan against
  the recorded generation it is pinned to (and the measured runtime the
  routing context already carries) BEFORE `route_request` chooses a
  route. An infeasible plan is refused there, with the reasons, and the
  plan that is produced carries its `feasibility` verdict.

- `library/tools/editorial_acceptance/feasibility.py` - the measurement
  on the eight-brief benchmark:
  `python3 -m library.tools.editorial_acceptance.feasibility --records
  tests/live/reports/editorial-bench-records`.

## Two rules the check does not break

**A measurement nobody took is not a refusal.** A check whose state was
not read reports `undetermined`, and undetermined never reads as a pass
and never reads as a failure (AGENTS.md 10.3, 10.4). The gate refuses on
a refusal only.

**The check never picks a backend.** The route stays the router's
(AGENTS.md 10.5). The verdict says whether a plan can REACH a route; it
does not say which one.

## The measurement, with its denominators

Over the eight recorded briefs of the benchmark
(`tests/live/reports/editorial-bench-records`), read with the same
harness that scores the benchmark:

| | before | after |
|---|---|---|
| briefs | 8 | 8 |
| briefs that planned an operation | 5 | 5 |
| plans refused by the ENGINE before staging | 0 of 5 | **5 of 5** |
| plans revised by the engine before staging | 0 of 5 | 0 of 5 |
| plans that reach the engine's route and execution path | 1 of 5 | **0 of 5** |
| attempts spent on those plans | 14 | 0 |
| **planning** (the coverage gate) | **4 of 8** | **4 of 8** |

Reading the numbers honestly:

- The three briefs that planned nothing are the CORRECT refusals (the
  request cannot be obeyed, and nothing was changed). They are not
  plans, so they are not in the five.
- The five that planned an operation are all refused before staging
  now, each with a named reason: `overlay.position`, `reel.opening`,
  `clip.trim`, `multicam.select_angle`, `caption_fix` and `audio.gain`
  have an executor and NO production-enabled backend, and the
  whole-job brief also names five target roles the run did not resolve
  in the footage.
- Before the change, ONE of the five reached the engine's route and
  execution path (the name-bar brief, on the `reel_touchup` backend);
  the whole-job brief spent seven attempts in the router. The other
  three were refused by the RUN before it submitted anything - a
  correct refusal, but not the engine's, and not one that names the
  missing route.
- **The planning number does not drop**: 4 of 8 before and after. The
  gate runs after the planning stage and rewrites nothing the planning
  stage reads, so a plan the engine refuses keeps the planning credit
  of the plan the run made. A plan that was REVISED would have to keep
  the refused operation in the record's planning block, or the
  planning gate would read the revision as a plan that dropped what
  the request asked for.

The measurement reads the RECORDS. It contacts no Resolve and changes
no project.

## What was left undone

- **No live run.** The measurement is over the benchmark's recorded
  plans, on the registry's own facts. A live run on a Ren-owned scratch
  copy would measure the same gate against a live timeline, and the
  media and effect checks would then have their live rows to read
  instead of reporting `undetermined`. It was not run here: the
  machine's Resolve is shared with other lanes, and the brief says
  Ren's lease owns that contention.
- **The media and effect checks are UNDETERMINED on the recorded
  base.** The recorded generation does not carry the media kind of an
  item, and the effect reading is the live row's. A caller that has
  live rows hands them over (`plan_feasibility.items_from_rows`) and
  both checks stop being undetermined. The touch-up path's own gate
  (`reel_touchup.qualify`) already measures the media kind on the live
  rows; the two are not yet joined.
- **The effect clash is REPORTED, not refused.** The registry's
  `unsupported_native_state` says the operation is not
  preservation-qualified for that state, not that it cannot be applied
  to an item carrying it: `clip.delete` declares grades and Fusion, and
  deleting a graded clip is exactly what it is for. A refusal there
  would be the engine inventing a restriction the router does not make.
- **The gate refuses; it does not revise.** A revision is a PROPOSAL
  the engine computes (`plan_feasibility.revision_for`) and names what
  it drops, but no front door adopts it yet, because adopting it
  changes the plan the run made and the planning stage reads that plan.
- **The benchmark's five plans are all refusals, not revisions.** The
  revision path is exercised by the unit tests, not by the benchmark:
  every operation those five plans named was refused, so there was no
  plan left to revise.
