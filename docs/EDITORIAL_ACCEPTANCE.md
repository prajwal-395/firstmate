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
                     "resolved_targets": [...]},
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
