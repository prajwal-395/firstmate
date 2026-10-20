# Editorial candidates: what was already done, and what this lane added

The captain's punch list (section 3, `EDIT-026`..`EDIT-038`) asks Ren to
generate candidate treatments for a subjective request, judge them, choose
one, keep the reasoning, and record what the editor decided.

Much of the ground was already covered by other lanes by the time this
lane ran. This file says, per item, **what already existed** and **what
this lane added**, so a reader does not have to rediscover it.

## The items

| Item | What was already done | What this lane added |
|---|---|---|
| **EDIT-026** - generate multiple candidate treatments | `edit_spec`'s `opening_alternatives` (reel openings only); `library/tools/versions/edit_variants.py` (general, but unwired - tests only) | `edit_spec` carries `candidates`: at least two treatments, each with a name, a rationale and its own complete executable program. `ren revise` asks the editor which one to use. `library/tools/candidate_treatments.py` owns the contract. |
| **EDIT-027** - evaluate candidates against the brief and measurable constraints | `reel_quality_bar` (reel-scoped); `reel_proposal` (explicitly does not score); `editorial_brief_scorer` (a delivered render against its brief) | A brief criterion carries the model's verdict with its reason and evidence - the engine never supplies one. A measurable constraint is COMPUTED from the candidate's own declared effect (`within_target_duration`, `touches_only`, `operation_available`, `reversible`). An absent measurement is `not_measured`, never a pass. |
| **EDIT-028** - estimate risk and reversibility | `edit_algebra.Inverse` declares reversibility per operation; `execution_router` derives a mode from it; `revise.scope_for` bounds the region a change may touch | `candidate_treatments.assess_risk` reads the registry per candidate: low / moderate / high, the irreversible operations and why, the unknown operations, the safety class, the categories touched, and whether it can be undone. |
| **EDIT-029** - prefer minimal changes | `feedback_patch.decide` routes patch against rebuild from what a fix touches; `reel_rebuild_need`; `reel_touchup` | `candidate_treatments.compare` orders the candidates that satisfy every criterion by fewest categories touched, then fewest operations, then the id - a total order, so the answer does not depend on the order the candidates arrived in. |
| **EDIT-030** - an explicit no-change conclusion | **nothing.** `nothing_to_decide` skips a model call; AXI's `already_satisfied` is mechanical; `reel_rebuild_need` saves work. None of them concludes the material is adequate | `candidate_treatments.no_change` is a first-class conclusion with its reason and the evidence it rests on. `edit_spec` accepts `no_change`; `ren revise` settles it (a `no_change` status), addresses the note and writes nothing to the timeline. |
| **EDIT-031** - select one candidate, or combine compatible elements | `versions.variants.choose` (selection with a required reason); `versions.edit_variants.validate_alternatives` (the compatibility rule, unwired) | `candidate_treatments.select` names the chosen candidate and what it was chosen over, and refuses one the evidence rules out unless the override is named and why. `combine` refuses elements that contradict each other - the same target, or two answers about the same category - and names them. |
| **EDIT-032** - preserve alternatives and their reasoning | `creative_revision` is the project's canonical decision ledger; `versions.variants` records approvals and rejections there; `versions.rounds` explicitly does NOT record why | The decision record carries the WHOLE candidate set: every alternative, its reasoning, its evaluation and its risk, plus a projection into `editorial_decisions`' vocabulary. The alternatives that lost survive the decision that won. |
| **EDIT-033** - capture accept / reject / modify | `reel_proposal.Approval` (proposed / approved / rejected); `versions.variants.record_approval` / `record_rejection`; `marker_resolution` records a resolution | `library/tools/treatment_rulings.py` records accepted / rejected / **modified**, and `modified` is not a rejection: the plan as proposed did not stand. The ruling lands on the candidate decision it belongs to. |
| **EDIT-034** - record the reason for a correction | **already done, and required.** `learned_context.record` / `correct` / `retire` refuse a reasonless correction; `marker_resolution` puts the captain's words first; `variants.record_approval` / `record_rejection` refuse a reasonless decision; `transcript_corrections` refuses a reasonless correction | This lane's part: a rejection or a modification with no reason is refused, and the correction becomes a project learning carrying the editor's own words. |
| **EDIT-035** - project preference against editor-wide preference | **already done.** `scoped_intent` (scope, precedence and the cross-project rule); `taste_profile` (the editor-wide store); `decided_value.stated_preference` (the project declaration wins) | The ruling carries `scope` - `project` or `editor` - and an editor-wide ruling travels only through a slot the engine can read back. |
| **EDIT-036** - do not generalise automatically | **already done.** `scoped_intent.resolve` lets only an explicit creator declaration scoped `*` travel; `learned_context` is per-project; `project_graph.cross_project` requires mutual opt-in | A project-scoped ruling becomes a project learning, which cannot reach another project. An editor-wide ruling that names no registered slot is recorded with `generalizable: false` and the reason it does not travel: Ren does not invent a slot for a subjective choice. |
| **EDIT-037** - blind comparisons against a professional's selection | `blind_label_pilot` judges ONE proposal (acceptable / unacceptable / unsure); `editorial_task_benchmark` has `human_preference` and `human_revision_distance` against a blinded reference | `library/tools/blind_comparison.py` constructs the head-to-head: the sides are shuffled by a deterministic function of the comparison id, the evaluator-facing view carries no origin and no treatment name, a plan summary that names either treatment is refused, and `score_preferences` unblinds the answer into a measurement of how often Ren's treatment was preferred. |
| **EDIT-038** - measure editorial utility, not structural validity | **already done by other lanes.** `library/tools/editorial_brief_scorer.py` judges a delivered render against its brief with the editorial verdict separate from the technical gate; `library/tools/editorial_acceptance/` measures five stages of an unfamiliar brief; `evaluation_scorecard` and `editorial_task_benchmark` carry the benchmarks | This lane's branch originally carried a copy of the brief scorer and DROPPED it when `main` was found to own it. The blind comparison's agreement measure is the editorial utility this lane adds. |

## Where the pieces live

| Piece | Module |
|---|---|
| The candidate contract: build, judge, assess risk, compare, select, combine, record | `library/tools/candidate_treatments.py` |
| What the editor decided, and why | `library/tools/treatment_rulings.py` |
| A blind head-to-head against a professional editor's selection | `library/tools/blind_comparison.py` |
| The one decision record (the whole set, including the losers) | `library/tools/creative_revision.py` |
| The shared decision vocabulary the candidates are projected into | `library/tools/editorial_decisions.py` |
| A delivered render judged against its brief | `library/tools/editorial_brief_scorer.py` |
| The spec answers the translation may now carry | `library/tools/edit_spec.py` |
| The revise flow that asks, settles and records | `library/tools/revise.py` |

## How this sits beside the editorial intent lane

`library/tools/editorial_decisions.py` records ONE decision: what was
proposed, what it rests on, what it would change, the criteria that would
show it landed, and who accepted it. Its subject is a decision.

This lane's subject is a SET of competing treatments for a request that has
more than one honest answer. The two are not two paths to the same thing:
`editorial_decisions` owns the layers of one decision and compiles an
approved decision into an edit request; this lane owns the comparison.

Where the two share a word they share the enumeration:
`candidate_treatments.to_editorial_decisions` projects every candidate - the
ones that lost as much as the one that won - into that vocabulary, and the
decision record carries the projection.
