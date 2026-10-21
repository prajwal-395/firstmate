# The editorial acceptance harness: unfamiliar briefs, five stages, one failure vocabulary

The punch list's section 5.3 asks for realistic acceptance evaluations:
a set of unfamiliar editorial briefs against representative real
footage, tasks that start from user instructions, separate metrics for
interpretation, retrieval, planning, execution and final quality,
projects with existing adjustments and imperfect media, the common edge
cases, evaluation of the exported file rather than the intermediate
timeline, independent editorial assessments, and a classification of
failure causes.

This is the harness that answers those, and it is two halves:

- the WORK is done by whoever takes the brief - the live lane's driver,
  or an agent working the `ren-co-editor` loop. It leaves a task record
  per brief.
- the MEASUREMENT is done by `ren accept`, on those records, with no
  Resolve, no model and no network.

The split is deliberate. The harness never drives an edit, so it can be
run and re-run on a machine with nothing installed, and the evidence it
produces is about the DELIVERED FILE rather than about the commands that
were issued.

## The brief set

`library/tools/editorial_acceptance/brief_set.json` holds the set. It is
DATA, so a brief is added or withdrawn without touching code, and the
file that ships is the file that runs.

Eight briefs, in a user's own words:

| id | what it asks | edge case |
|---|---|---|
| `open-with-the-cost` | open on the moment she names the cost | - |
| `two-voices-at-once` | follow the claim when two people talk over each other | `overlapping_speech` |
| `no-extra-footage` | cover a shot that was never filmed | `sparse_footage` |
| `logo-not-in-the-project` | swap in a logo the project does not hold | `unavailable_asset` |
| `keep-it-but-tighten-it` | leave it exactly as it is, AND tighten it | `conflicting_instruction` |
| `the-wrong-caption` | fix the caption that names the wrong word | - |
| `name-bar-fights-the-captions` | move the name bar out of the captions | - |
| `the-whole-job` | the whole launch reel, start to delivery | - |

Every one of them carries pre-existing human adjustments and imperfect
media, because a brief run against a clean timeline proves nothing
about the projects Ren is asked to work on. The last one is the
recurring demonstration (below).

### The answer key the agent never sees

Each brief is two objects:

- `public_view(brief)` is what the agent reads: the instruction, the
  project state, and the acceptance criteria the result will be judged
  against. It is built KEY BY KEY, not by filtering the brief, so a
  field added later cannot leak by being forgotten.
- `oracle(brief)` is the evaluator's key: the operations the request
  implies, the semantic target roles, the source moments, the edge case
  and whether a refusal is the right outcome.

`assert_no_leak` refuses a brief whose public view carries an oracle
key or an oracle value, so a brief that leaks the answer fails when it
is added rather than producing a flattering score.

### Unfamiliarity is measured

`unfamiliar_against` compares the set against a corpus the engine was
already tuned on, and the acceptance set is checked against three:

- the standing 120-request corpus (`library/tools/eval_corpus.py`),
- the ten ordinary requests the live harness already runs,
- the Level-3 rule-based interpreter, which now refuses every one of
  the eight briefs - so none of them can be answered from what was
  already known.

## The five stages

Interpretation, retrieval, planning, execution, final quality. Each is
measured on its own denominator, and they are never combined: they are
not the same unit, and an average over them hides WHICH one broke.

- **interpretation** - did the run read the request as the user meant
  it, and name the work it implies?
- **retrieval** - did it find the source moments the work needs, each
  with evidence behind it?
- **planning** - is the plan a valid answer: covering what was asked,
  and nothing the user did not ask for?
- **execution** - did the plan run through a qualified route, verify,
  and leave the rest of the project alone?
- **final_quality** - does the delivered FILE satisfy the brief, and
  pass its technical checks?

A stage with no evidence is `not_measured`, never a zero. A zero is a
claim that something was measured and came out badly.

### The reading and the grounding are two measurements

The interpretation stage measures two different things at once: whether
the run READ the request, and whether it could GROUND the work in
something the footage carries. Folding them together is what made the
published number unreadable - eight of eight briefs were read
correctly while four of eleven target roles could be pointed at.

The report therefore publishes the split ALONGSIDE the headline, never
instead of it:

- **`reading`** - did the run restate the request, name exactly the
  operations the request implies, and plan nothing for a brief whose
  correct outcome is to report back? Measured per brief.
- **`grounding`** - could the run point at the thing the work is about?
  Measured per ROLE INSTANCE, because a brief that names seven roles
  and a brief that names one are not the same size of claim.

Three things are kept apart in the grounding view, because they are
three different answers:

- **not applicable**: the brief's own premise is not in the substrate.
  There was nothing to point at; this is not a failure of the run.
- **resolved**: the run said which item the role names. A claim is not
  evidence.
- **verified**: the recorded timeline CARRIES the item the run named -
  the clip's own stable id, on the timeline the record says it worked
  in. A resolution with no evidence behind it is unverified, and one the
  recorded timeline does not carry is NOT verified.

The verification never guesses. No recorded timeline, an ambiguous
timeline name, or a target that names no clip leaves the resolution
unverified rather than counting as resolved.

### The constraints a request states are measured too

A request that says "move it down" and a reading that says "move it so
it stops colliding" are different readings, and before this the
difference was invisible: a prose restatement has nowhere to put a
direction. Each brief's answer key now DECLARES the constraints its
request states, with the words that carry them, and the reading is
measured against that. The constraint's words are the requester's own -
they are already in the instruction the run read - so nothing is handed
to the run; what the harness gains is a place to say "the reading
dropped this".

The count is published BESIDE the reading, not folded into it: the
reading is one measurement, and whether it carried the request's
constraints is another.

### The canonical intent is measured, not believed

A record may carry the canonical intent the run read the request into
(`interpretation.intent`), and when it does the harness MEASURES it -
the intent's own contract is checked here, rather than the run's word
that the intent is fine. `editorial_intent.intent_from_request` is the
path from a one-off sentence to that canonical record: it declares the
request kinds the sentence names, with each kind's own evidence bar and
autonomy, and it invents no requirement and no preference.

### The planning stage is gated on the brief's acceptance criteria

A plan has to CARRY the brief's acceptance criteria - the statements the
result will be judged against, most of which are about preservation
("the others are untouched", "the grade did not change"). The harness
computes the criteria a plan did not declare covered and now FAILS the
planning stage on them; it used to publish the gap in the detail and
leave it out of the pass condition, so a plan that named one criterion
and was silent about the rest read as a valid answer.

The gate applies to the briefs that EXPECT AN EDIT. A brief whose
correct outcome is to report back has no criteria for a plan to cover -
"says so" and "nothing was changed" are established by the refusal, not
by an operation - so those briefs are measured on whether they planned
nothing.

Measured on the eight-brief acceptance set, this moves the headline
planning number from **8/8 = 1.00** to **4/8 = 0.50**: the three
refusal briefs, plus the one edit brief that declares all ten of its
criteria. The number was never earned for the other four. Alongside it
the report publishes `criteria_coverage` - a SUPPLEMENTARY view saying
how much of each brief's criteria the plan declared it covers (15 of 22
across the five briefs that expect an edit). It never replaces the
headline.

The Level-2 and Level-3 request rows are readable in the same
vocabulary (`stages.from_request_row`), so the old reports and the new
one can be read together.

## The failure vocabulary

Every failure is classified twice: the STAGE it broke, and the CAUSE
within that stage.

The first failing stage is the bottleneck, because a stage that broke
makes every stage after it untrustworthy - a run that did not understand
the request cannot be credited with understanding the footage. The cause
says which engineering change would move it.

The older taxonomies map onto this one, in this one place:

- `eval_corpus`'s seven layers (V, H, S, Q, C, A, U) -
  `failures.from_legacy_layer`,
- `edit_demo_tasks`'s perception-versus-decision split -
  `failures.from_demo_attribution`.

A cause the evidence does not support is `unknown.cause`, and it is not
the same thing as a cause that was looked for and not found. The summary
counts them apart.

## The delivered file, not the timeline

`delivery.build_render_evidence` reads the EXPORTED file: its digest,
its length, stills a judge can look at, and the file-only checks taken
off it with the engine's own `render_qa` gate.

Resolution, frame rate and duration are graded against what was
DECLARED. When nothing was declared they are named in
`technical_not_checked` rather than passed - a check nobody declared is
not a check that passed.

## The independent judgement

`judgement.judge_input` is the ONE construction of what a judge is
shown: the brief's public half, plus the delivered file's digest,
evidence and length. The answer key is not in it, and neither is
anything about the plan that produced the file. The record says what the
judge saw and what it did not.

A panel of judges is read, not averaged. Two judges that disagree about
a criterion have established nothing: the criterion is reported as a
disagreement, and the brief is not established either.

## The task record

One record per brief attempt, written by whoever ran the brief:

```json
{
  "schema": "ren-editorial-task-record/1",
  "task_id": "the-whole-job",
  "brief_id": "the-whole-job",
  "instruction": "the user's words, verbatim",
  "interpretation": {"restated_outcome": "...", "selected_operations": [...],
                     "resolved_targets": [...],
                     "intent": {"intent_id": "...", "requirements": [...],
                                "requests": [...]},
                     "constraints_covered": [...]},
  "retrieval": {"moments": [{"moment_id": "...", "evidence_ids": [...]}],
                "abstained": false, "unavailable": []},
  "planning": {"operations": [{"op": "...", "target_role": "..."}],
               "requirements_covered": [...]},
  "execution": {"receipt": {"status": "verified"}, "checks": {...},
                "collateral_changes": [], "attempts": 1, "route": "...",
                "recovery": {"state": "none"}},
  "delivery": {"path": ".../reel.mp4", "sha256": "..."},
  "run": {"wall_seconds": 412.5},
  "resource": {"peak_rss_bytes": 187000000}
}
```

`record.validate` names every breach, and a record that breaks it is
refused rather than read as a run that did nothing.

Three parts of the record are MEASURED rather than believed, when the
record carries them:

- `interpretation.intent` - the canonical intent the run read the
  request into. The harness checks it against the intent's own contract.
- `interpretation.constraints_covered` - the constraints the request
  stated and the reading carried. The brief's answer key declares the
  constraints and the words that carry them.
- `interpretation.resolved_targets[].evidence` - the clip's stable id,
  the row and the frame span of what the run resolved. Without it a
  resolution is unverified, and a resolution the recorded timeline does
  not carry is not verified either.

## Running it

```
ren accept --list
    the briefs, their edge cases and the set's coverage

ren accept --records <path> [--report <path>] [--judge module:attribute]
    measure every record against its brief and write the report
```

`--records` takes one JSON file holding a list of records, or a
directory of one-record files. `--judge` names a factory that returns
the independent judge; without one, the editorial half of a task is
`not_measured` rather than scored.

The report lands in `tests/live/reports/editorial-brief-acceptance.json`.
The ordinary-request report keeps its own file
(`editorial-request-acceptance.json`) and the E2E workflow assessment
keeps `editorial-acceptance.json`: a typed request, an editorial brief
and a workflow assessment are three different measurements, and one file
holding more than one of them would make them look comparable.

A fourth file holds the ordinary-request measurement of 2026-10-20
(`editorial-request-acceptance-2026-10-20.json`, 1 of 10). It is a
fourth MEASUREMENT rather than a fourth copy: the two ordinary-request
runs differ in the environment they ran in, and each one carries the
scope that says so. `docs/REPORT_PROVENANCE.md` has the vocabulary and
the two numbers side by side.

## The recurring demonstration

The punch list's section 12 asks for one benchmark task, run again and
again, so that effort cannot fragment into isolated components. It names
ten obligations and ten pieces of evidence.

`the-whole-job` is that assignment, and `demo.score_demo` reads a task
record against all twenty. The milestone needs every obligation
established, every piece of evidence produced, AND the independent judge
passing the delivered file against the brief - it is not achieved
because the commands ran.

`demo.compare_runs` reads two runs against each other, so a regression
in a recurring demonstration is a number rather than a memory.

## What the live lane has to do

The harness is ready; the RUN is the live lane's, because it needs a
real project, a real Resolve and a real delivered file. What it needs
from that lane:

1. a project to work on, per brief, with the pre-existing adjustments
   and imperfect media the brief declares,
2. one task record per brief, in the shape above,
3. a judge for `--judge`, if the editorial half is to be measured.

Until then the committed report says `run_state: not_run`, which is
what it is.
