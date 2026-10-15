# EVAL-04: evidence-to-edit task benchmark

## Scope

The first benchmark uses exactly two editorial briefs:

1. A dialogue/podcast multicamera edit, covering `dialogue_multicam`.
2. A visual edit from the Apple 001 iPhone footage, covering both
   `visual_action` and `visual_object`.

The source inventory and rights/split contract are owned by
[`2026-10-15-editorial-eval-corpus.json`](2026-10-15-editorial-eval-corpus.json).
Training, calibration, and held-out sources must remain separate. The benchmark
does not copy media into this repository.

## Evidence and score contract

Every run records the evidence behind each editorial decision. Its evidence
ledger names the decision, rationale, evidence IDs, and one of the following
evidence sources: Project Model, typed evidence query, bounded acquisition, or
edit-point signals. A run also records whether evidence, plan, edit graph,
render, and verification completed.

The score record includes task success, evidence grounding, cut accuracy,
coverage, continuity errors, human revision distance, blinded human preference,
wall time, interventions, model calls and time, and measured compute cost.
Human revision distance is the number of edit-graph operations needed to
transform the Ren edit into the blinded reference edit, using the unit
`edit_graph_operations`.
Unknown measurements remain absent and fail acceptance; they are never treated
as zero. The validator and score aggregator are
[`editorial_task_benchmark.py`](../../library/tools/editorial_task_benchmark.py).

Run the acceptance check with all output paths rooted in the scratch result
directory:

```sh
bin/vep -m library.tools.editorial_task_benchmark \
  /tmp/eval04/result.json --output-root /tmp/eval04
```

The result must contain two briefs, all three task types, two successful
end-to-end runs per type, a held-out repeat, blinded human reference edits,
reviewable outputs, and the evidence and score fields above.

## Runner

[`evidence_to_edit.py`](../../library/tools/evidence_to_edit.py) runs a
brief end to end without Resolve, model passes, or media reads. Per
expected moment it compiles typed evidence plans (attributed speech and
multicam sync for dialogue; objects and on-screen agents for visual),
answers them against the supplied Project Model, plans bounded
acquisition without executing it, derives edit-point candidates from
measured signals, narrows brief cut ranges to present evidence (never
wider), builds the edit graph from state that honours the spine
contract (dialogue cuts need measured word timings; visual cuts become
action beats), and writes the graph, evidence ledger, and OTIO cut list
under the output root. The Resolve slice supplies `render_output` and
the `verify_render` verdict through `run_task`; until then those stages
stay incomplete and unmeasured metrics stay absent, so the gate fails
closed. Brief windows are checked against the measured durations in
`2026-10-15-editorial-eval-corpus.json`; a window past the end of its
source is refused.

Run the first slice from the repository root:

```sh
python3 -c "import json; d=json.load(open('docs/benchmarks/2026-10-15-editorial-eval-corpus.json')); json.dump({s['source_id']: s['duration_seconds'] for s in d['sources']}, open('/tmp/eval04/durations.json','w'), sort_keys=True)"
bin/vep -m library.tools.evidence_to_edit \
  --brief docs/benchmarks/eval04-brief-dialogue-multicam.json \
  --brief docs/benchmarks/eval04-brief-visual-action-object.json \
  --model /tmp/eval04/model.json --durations /tmp/eval04/durations.json \
  --split calibration --run-name run-a \
  --output-root /tmp/eval04 --out /tmp/eval04/result.json
```

## First-slice baseline (2026-10-15)

Run against a manifest-only Project Model skeleton (no observations, no
signals), the runner consults all four evidence sources on every
moment, answers `not_yet_analysed` with the never-ran lanes named,
plans bounded acquisition with measured estimates (37.4s and 18.7s on
the sampled dialogue windows) and defers it on the zero first-slice
budget, emits no cuts and no graph nodes, and the acceptance check
fails closed: stages incomplete, metrics null (never zero), human
references missing. The full mechanics chain (evidence to verified
render to scores) passes on synthetic fixtures with real ffmpeg renders
and real `verify_render` verdicts; those fixtures stay in `/tmp` and
are not benchmark results.

## In-flight evaluator dependencies

EVAL-02, EVAL-03, EVAL-05, EVAL-06, and EVAL-07 scorers have not landed
as pinned artefacts on this base (`external_scorer_status` in the
runner checks and reports them pending). EVAL-04's acceptance contract
records its own task-level measurements and does not substitute guessed
values for those in-flight scorers.

## Pending for a measured benchmark

Blinded human reference edits and gold cut-range freezes for both
briefs (a human editor's work, not a model pass); held-out repeat runs
on real footage (dialogue held-out sources exist; all 001 takes share
one calibration leakage group, so the visual held-out repeat needs a
corpus split decision); heavy evidence acquisition under
`heavy_work_lock` with the narrowest matching profile from
`resource_scheduler.profiles()`; timeline and render execution through
resolve-axi under Ren's lease in Ren Qualification or a scratch
project, never the native engine.

## Current corpus limits

The EVAL-01 manifest currently marks every Apple 001 take as calibration, and
lists action and object annotation coverage as `not_assessed`. Podcast dialogue
and multicamera coverage exist, but there is no blinded human reference edit or
task-specific cut-range annotation in the EVAL-01 manifest. Therefore this
document defines the approved two-brief scope and scoring contract, not gold
labels or benchmark results. Those records must be produced and frozen before
the acceptance check can pass. Held-out repeat work is limited to footage with
a held-out split; changing the split or creating labels is corpus work, outside
this benchmark change.

Resolve timeline and render execution use resolve-axi under Ren's lease, on
the task's own timeline in `Ren Qualification` unless the scratch-project verb
is available. Never write the captain's project or timeline; restore the
Resolve state found before the task.
