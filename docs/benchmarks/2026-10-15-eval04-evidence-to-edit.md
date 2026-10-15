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
  --model /tmp/eval04/model.json --durations /tmp/eval04/durations.json \
  --split calibration --run-name run-a \
  --output-root /tmp/eval04 --out /tmp/eval04/result.json
```

The dialogue brief uses calibration sources. The visual brief declares
`split: "held_out"`; run it separately with `--split held_out` only for the
future measured held-out repeat described below. This corpus change does not
rerun or retune any pinned benchmark score.

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

## Real-footage repeats (2026-10-15)

Measured with ffmpeg/ffprobe only (`real_footage_evidence.py`; no model
passes, no downloads) under `heavy_work_lock --profile
temporal.index:image_defects`: all four brief sources verified against
the corpus manifest (durations within rounding, sizes exact; MXF
23.976 fps, 001 MOV 29.993 fps). Scene detection over every brief
window (2124.8 s wall, full-window decode) found zero cuts above
threshold - static podcast cameras and continuous 001 takes - recorded
as measured `confidently_absent`, not as missing work. Audio energy
spans are dialogue-shaped on both podcast windows and continuous on
both 001 takes (97 observations total).

Run through the runner with clock-pinned scopes: both visual repeats
reach `edit_graph` complete (sound-grounded action beats, zero
constraint violations on the built graphs); both dialogue repeats
complete evidence and plan but stop before the graph (no word-level
speech evidence exists, so no speech blocks are emitted). Scoring stays
fail-closed: `cut_accuracy`, `coverage`, and all human fields are absent
until gold ranges and blinded references exist. The Resolve render
slice (placement, render, `verify_render`) needs timelines that only a
sanctioned creation path can make - see task status.

## Resolve render slice (2026-10-15)

Built and rendered in scratch project `Ren Scratch - EVAL04 evidence
to edit` (created, opened, restored, and deleted through the
sanctioned `resolve-axi project scratch` verbs; captain projects
untouched). Placement and rendering went through Ren's own entry
points - `resolve_build_timeline.build_timeline` and
`execution.resolve_render.render_timeline` - under
`reel.build:apply` / `render.build:render`, with no raw scripting.
Both dialogue repeats rendered on Resolve at 1080p24 review grade
(0.1% conform from 23.976, UHD downscale; program_channel 1
operator-declared after per-channel measurement showed MXF CH0
dominant at -29.5 dB against -69.3/-35.0/-37.1 dB). Both visual
repeats rendered on Resolve at native 29.97 cadence in a second
scratch project. All four renders pass `verify_render` on every check
except delivery loudness, which is an unaltered source property
(-26.2/-22.1 LUFS dialogue, -21.7/-29.9 LUFS visual, against the -14
target).

Rate note: `project scratch create` now takes `--fps/--width/--height`
(PR 2531), and the build's per-timeline rate refusal now judges by the
read-back rather than the return code - Resolve's timeline `SetSetting`
returns False on an applied 29.97 rate (measured 2026-10-15), so a
refused write refuses only when the grid disagrees too
(`timeline_frame_rate_refusal`, with unit cover).

## In-flight evaluator dependencies

EVAL-02 has landed as `library/tools/visual_retrieval_benchmark.py`
and the runner's `external_scorer_status` reports it available;
wiring its retrieval scores into EVAL-04 visual grounding needs a
footage index build over the 001 takes and stays future work.
EVAL-03, EVAL-05, EVAL-06, and EVAL-07 have no pinned scorer artefact
on this base. EVAL-04's acceptance contract records its own
task-level measurements and does not substitute guessed values for
those in-flight scorers.

## Pending for a measured benchmark

Blinded human reference edits and gold cut-range freezes for both
briefs (a human editor's work, not a model pass - the exact ask is on
the task status); one measured held-out visual repeat against the newly
assigned 001 held-out scenes (IMG_1812-IMG_1813 and IMG_1817-IMG_1822); heavy
evidence acquisition under `heavy_work_lock` with the narrowest
matching profile from `resource_scheduler.profiles()` (word-level
speech would unblock the dialogue graph).

## F02 evidence-grounding follow-up (2026-10-15)

The first-slice runner accepted any present interval as support for a cut.
That allowed sound-energy intervals to stand in for dialogue and visual
evidence, and the cut list retained only the brief's rationale. On the
recorded real-footage Project Model (`/tmp/eval04-real/model-real.json`),
the four calibration repeats proposed four cuts (two dialogue, one per
visual source); the single held-out podcast repeat proposed two more.
The dialogue proposals had no measured words or attributed turns, and the
visual proposals had no object or action evidence.

The runner now requires a task-specific evidence plan to overlap each
proposed interval. A combined visual-action/object task requires evidence
for both; object presence cannot imply an action. Every retained interval
includes evidence IDs, typed factual measurements and provenance, while the
brief's selection reason is kept separately as creative rationale. Technical
values remain explicitly listed, and each proposed point reports whether an
edit-point signal verified it. No signal candidate means no point is claimed.
Unsupported proposals are recorded as abstentions naming the missing plans.

| Repeat | Before: cuts / graph nodes | After: cuts / graph nodes | Result |
|---|---:|---:|---|
| Calibration LCATL0012 dialogue | 2 / 0 | 0 / 0 | Abstained: attributed speech evidence unavailable |
| Calibration IMG_1818 action/object | 1 / 1 | 0 / 0 | Abstained: action and object evidence unavailable |
| Calibration IMG_1812 action/object | 1 / 1 | 0 / 0 | Abstained: action and object evidence unavailable |
| Held-out LCATL0011 dialogue (reported once) | 2 / 0 | 0 / 0 | Abstained: attributed speech evidence unavailable |

This is a groundedness improvement, not a completed editorial benchmark:
EVAL-04 still has no frozen human references or gold cut ranges, and the
current real-footage repeats have no measured word/action/object labels. The
held-out result was reported once; thresholds were not tuned on it.

## Current corpus limits

The EVAL-01 manifest now keeps nine Apple 001 takes across two capture-scene
groups in calibration and assigns eight takes across two other scene groups to
held out. The scene grouping is based on filename sequence and ffprobe capture
times/durations: contiguous numbered takes remain together, with scene breaks
at the two greater-than-120-second gaps between the end of one clip and the
next capture. The held-out groups include both proposed EVAL-04 source moments:
IMG_1812 for `visual_object` and IMG_1818 for `visual_action`. Calibration
retains nine varied takes (about 5.8 minutes) and podcast calibration footage.
Actions and object annotation coverage remain `not_assessed`. Podcast dialogue
and multicamera coverage exist, but there is no blinded human reference edit or
task-specific cut-range annotation in the EVAL-01 manifest. Therefore this
document defines the approved two-brief scope and scoring contract, not gold
labels or benchmark results. Those records must be produced and frozen before
the acceptance check can pass. The held-out repeat is follow-up work; this
change makes no new score claim.

Resolve timeline and render execution use resolve-axi under Ren's lease, on
the task's own timeline in `Ren Qualification` unless the scratch-project verb
is available. Never write the captain's project or timeline; restore the
Resolve state found before the task.
