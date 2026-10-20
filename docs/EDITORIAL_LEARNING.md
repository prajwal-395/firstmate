# Editorial learning: current coverage and remaining evidence

This lane connects corrections to the assignment, decision, evidence and
delivery records already owned by Ren. Records remain local to the project;
there is no upload or cross-project telemetry path. A project correction only
reaches that project's learning store. An editor-wide preference requires an
explicit registered preference slot.

## LEARN-001..012

| Item | Status | Existing owner or change in this lane |
|---|---|---|
| LEARN-001 | Already implemented | `assignment_task`, `assignment_artifacts`, `assignment_provenance`, `assignment_plan`, `editorial_intent` and `editorial_decisions` link the request, evidence, decisions, operations and delivery receipt. |
| LEARN-002 | Already implemented | `treatment_rulings` records acceptance, rejection, modification, the editor's wording and rationale on the candidate revision; `feedback_ledger` and `feedback_outcomes` retain timeline feedback and its resolution. |
| LEARN-003 | Implemented here | A ruling now carries `correction_kind`: `objective_error`, `subjective_preference` or explicit `undetermined`. Editorial intent and assignment criteria already separate objective/technical checks from editorial judgement. |
| LEARN-004 | Implemented here when measured | Rulings can snapshot the same original criterion IDs before and after correction. The record derives `improved`, `regressed`, `mixed`, `unchanged` or `unmeasured`; absent measurements never count as a pass. |
| LEARN-005 | Implemented here when attributed | Corrections can name `capability_id`. An empty value means the responsible capability is not yet known; this record does not blame the language model by default. |
| LEARN-006 | Partly implemented; feedback authorization remains a gap | `editorial_eval_corpus` checks source hashes, rights evidence, permitted uses, leakage splits and held-out label access. Project corrections stay local and scoped. There is no explicit consent/retention declaration on a feedback ruling, so such records are not authorized as reusable training examples by this change. |
| LEARN-007 | Not feasible from current repository evidence | The checked-in editorial corpus describes benchmark tasks and proposed annotations; it does not contain a set of genuinely observed, authorized editorial corrections. No examples were fabricated or promoted. The new ruling fields provide the source record once real corrections are collected with permission. |
| LEARN-008 | Already implemented for evaluation corpora | `editorial_eval_corpus` enforces separate training, calibration and held-out splits, leakage-group isolation, and sealed held-out labels. |
| LEARN-009 | Already implemented | `learned_context` is project-local; `scoped_intent` and `treatment_rulings` require explicit editor scope and a registered preference slot before a preference can travel. |
| LEARN-010 | Implemented here as an explicit diagnosis | Each correction can record `more_evidence`, `better_reasoning` or `unknown`, preserving uncertainty rather than attributing every correction to reasoning. |
| LEARN-011 | Already implemented as evaluation capability; future measurements need data | `blind_comparison` compares Ren's treatment with a professional editor's selection without exposing origin; `editorial_task_benchmark` records human preference and revision distance. No unmeasured model-candidate win is claimed here. |
| LEARN-012 | Already implemented | Assignment, decision, feedback and benchmark records are portable JSON with source references; Resolve timeline IDs are not the sole editorial identity. |

## Correction record fields added

The existing `treatment_rulings` record now carries the correction class,
responsible capability, evidence IDs, diagnosis, and before/after status for
each acceptance criterion. These values are persisted on the creative revision
and returned by `read_rulings`. The same values are included in the existing
project learning source. They are declarations from the reviewer, not inferred
facts; missing or incompatible criterion snapshots report `unmeasured`.

Live Resolve evidence is not required for these schema and record changes.
Any claim that a correction improved a delivered edit still requires the
correction's before/after criterion evidence from its owning task.
