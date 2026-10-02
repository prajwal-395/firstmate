# What actually stops the pipeline being operated by an LLM

An audit, 2026-09-05, against `b5f6cdd`. Written before any design or code, because the
captain's brief said the pipeline is too rigid for an operator LLM and named four things
they believed were missing. Two of the four are missing. One is half-built and the halves
do not compose. One is not missing at all - it is enforced, just not by the mechanism that
reads like the contract.

Every claim below is a file, a line, or a command that was run. Where a number is quoted
it was measured, not estimated.

## The verdict, first

| # | Suspected gap | Verdict | The number |
|---|---|---|---|
| 1 | The declared contracts are decorative | **CONFIRMED, and narrower than it looks** | 90 `preconditions` + 36 `postconditions` across 29 manifests. **0 are evaluated by any code path.** 71 of the 90 are accidentally enforced by a *different* mechanism; **19 are enforced by nothing at all** |
| 2 | Functionality is welded into the step | **PARTLY REFUTED** | ~47% of `step.py` code is inline domain logic, but **69% of it sits in 3 of 29 steps**, and step modules are **already importable** - 64 test files do it today. Exactly **one** body is genuinely welded to the execution shape |
| 3 | Re-entry is step-shaped, not region-shaped | **CONFIRMED as an operation; REFUTED as a primitive** | `--rerun` takes 3 forms - stage, step, step:clip - and **no interval**. But 6 region-addressed primitives already exist across 5 modules, including a span cache built for this exact ask. One missing function (`timeline_to_source`) is what stops a timestamp being an address |
| 4 | No trigger or hook layer | **CONFIRMED, absolutely** | zero hook/trigger/event modules in `library/`. The human->LLM channel exists; nothing fires on a condition |

**The one-sentence diagnosis.** Every control vocabulary this pipeline has - scope
(`run_scope`), pausing (`breakpoints`), invalidation (`--rerun`), contracts
(`inputs[].required`), review gates - is addressed by **step id** and keyed by **state
key**. There is no vocabulary for an operation *smaller than a step*, and no vocabulary
for a region *smaller than the whole project*. That single missing axis, not rigidity, is
what makes the captain's worked example hard.

## 1. Where the captain's premise did not match the code

Stated first and plainly, because the size of the work depends on it.

**"It has contracts and prerequisites, but they only work inside the specific DAG setup."**
Half right, and the half that is wrong matters. There is not one contract system and there
are not two. There are **four**, and they disagree about what a contract is:

| System | File | When it runs | Can it fail a run? |
|---|---|---|---|
| Prose `preconditions` / `postconditions` | every `library/steps/*/manifest.json` | **never** | **no - nothing reads them** |
| `inputs[].required` + DAG `data_mapping` | `run_pipeline.gather_step_inputs:806` | mid-run, per step | **yes**, `RuntimeError` |
| Selection prerequisites | `library/tools/run_scope.py:284,652` | before the run starts | **yes**, `ScopeError` |
| Declaration/code agreement | `library/tools/input_contract.py` | **test time only** | reports, does not fail |
| Externally supplied state | `library/tools/external_inputs.py:423` | run start | **yes**, `ExternalStateError` |

`run_scope` and `gather_step_inputs` are real, they are good, and they are the reason the
captain's subtitles example does not actually happen the way they described it. What is
decorative is the *thing that reads like the contract* - the manifest prose.

**"Certain triggers should be able to run scripts automatically."** Confirmed absent. But
the delivery half of "steer the LLM" already exists and should be reused, not reinvented:
`library/dashboard/review_channel.py:267` `wait_for_batch` parks an agent until the captain
sends a note, and `library/tools/marker_routing.py` already routes a note *typed on a
timeline frame* to the step whose decision it is about. What is missing is a **condition**
that fires into that channel without a human pressing send.

**"There is no easy way for the LLM to [regenerate one segment of subtitles]."** True as an
operation. **False as a claim about the primitives**, and this is the audit's most important
correction. Every piece that worked example needs is already written:

- `library/tools/timeline_transcript.py:141,160` - `span_cache_key(source_file, in, out)`
  and `extract_span(...)`, a **per-span cache keyed by the span itself**, written in
  response to the captain's own request quoted in that module's docstring: *"which also
  helps to be able to reindex specific portions of clips ... or if we just need to reindex
  to correct a mistake"*.
- `library/tools/timeline_transcript.py:268,285` - `attribute_to_clip(start, end, clips)`
  and `to_source_time(clip, timeline_time)`: timeline seconds -> clip -> source seconds.
- `library/tools/timeline_decisions.py:333,456` - `Placement` carries
  `timeline_in_frame`, `timeline_out_frame`, `source_in_frame`, `source_out_frame`,
  `source_file` and `step`; `placements(manifest)` returns them for the whole build.
  `TRACK_DECISIONS` (8 rows) already maps **track -> the step that decided it**, and V3 is
  `plan_subtitles`.
- `library/tools/reel_subtitles.py:128` - `reel_captions(transcript, ranges, ...)`
  already generates captions for **a list of (start, end) ranges**.
- `library/steps/step_4_01_plan_subtitles/step.py:535` - `_words_in_source_window(block,
  words, src_in, src_out)` already selects words by source window.
- `library/tools/subtitle_segment_id.py:66` - `SEGMENT_BINDING_KEYS` makes every rendered
  overlay **content-addressed** over its own region, so rebuilding one segment overwrites
  itself and never another.

Six region-addressed primitives, in five modules, none of which can be reached as one
operation and none of which share an addressing type. The refactor is therefore **mostly
composition and addressing, not mostly new capability** - which is a materially smaller and
safer change than the brief assumed.

## 2. Gap 1 - the contracts that nothing evaluates

### The measurement

`grep -rn precondition --include="*.py"` over the repo returns 10 hits. **None is an
evaluation.** Eight are prose in docstrings or test names; one is an error message
(`library/tools/analysis/speech_advanced_pipeline.py:203`); one is
`tests/unit/audio/test_prosody.py:60`, which asserts that the *string*
`"parselmouth"` appears in the manifest's precondition list. `postcondition` returns
**zero** hits in Python anywhere in the repository.

Counted across all 29 step manifests: **90 preconditions, 36 postconditions.** Classifying
each precondition against the mechanisms that *do* run:

- **71 are redundant** - they restate `'<key>' exists in state` for a key the same
  manifest already declares `required: true` on an edge the DAG already carries. These are
  enforced, by `gather_step_inputs`, by accident of duplication.
- **19 are enforced by nothing**, and they are the interesting ones:

```
step_0_01_validate_sfx_library   SFX library folder exists with profiles/ subdirectory
step_0_01_validate_sfx_library   FAISS index has been built (run sfx_pipeline.py once)
step_1_03_semantic_analysis      'clip_catalog' exists in state
step_1_05_prosody_analysis       parselmouth is installed (pip install praat-parselmouth)
step_1_06_object_segmentation    'raw_footage_files' exists in state
step_1_06_object_segmentation    'clip_catalog' exists in state
step_3_03_review_rough_cut       'b_roll_interjections' exists in state
step_4_01_plan_subtitles         'temporal_index' exists in state
step_4_01_plan_subtitles         'rough_cut_review.passed' is true in state
step_4_02_plan_transitions       'rough_cut_review.passed' is true in state
step_4_03_plan_vfx               'rough_cut_review.passed' is true in state
step_4_04_plan_sfx               'rough_cut_review.passed' is true in state
step_4_05_render_subtitles       Remotion project is set up in remotion-subtitles/
step_4_05_render_subtitles       Node.js and npx are available
step_4_06_render_motion_graphics Remotion project is set up in remotion-subtitles/
step_4_06_render_motion_graphics Node.js and npx are available
step_5_01_color_grade            'b_roll_interjections' exists in state
step_5_01_color_grade            'project_folder' exists in state (optional)
step_5_01_color_grade            'brand_template' exists in state (optional)
```

Two classes stand out, and neither can be expressed by `inputs[].required` at all:

**A quality gate declared four times and enforced zero times.** Four planning steps declare
`'rough_cut_review.passed' is true in state`. `rough_cut_review` *is* routed to
`plan_subtitles` as a required input, so its **existence** is enforced - but nothing
anywhere reads `.passed`. `grep -rn "rough_cut_review" --include="*.py" library/` finds no
read of that field outside two test fixtures. The pipeline claims a rejected rough cut
stops four planners. It does not.

**Environment preconditions have no expression.** "Node.js and npx are available",
"parselmouth is installed", "FAISS index has been built" are true prerequisites of real
work. The existing mechanism can only say "key K arrives on an edge", so these could only
ever be prose. This is the structural reason the prose list exists and the structural
reason it is inert.

### What actually stops "subtitles with no transcript" - measured

Run against `b5f6cdd`, driving the real `gather_step_inputs`:

```
CASE 1 (no upstream at all): REFUSED -> RuntimeError Step 'plan_subtitles': data_mapping
  expects key 'audio_spine' from upstream step 'mesh_spine', but it is missing from that
  step's outputs. Available keys: []
CASE 2 (speech_sequence key missing): REFUSED -> RuntimeError Step 'plan_subtitles':
  data_mapping expects key 'speech_sequence' from upstream step 'speech_sequence', but it
  is missing from that step's outputs. Available keys: []
CASE 3 (keys present, transcript EMPTY, review FAILED): NO REFUSAL; inputs =
  ['audio_spine', 'brand_effect', 'brand_style', 'project_folder', 'rough_cut_review',
   'speech_sequence']
```

So the honest answer to the captain's example: **the key-existence half works and the
content half does not exist.** A completely empty transcript and a *failed* rough-cut
review both pass the contract and reach the step. `run_pipeline.check_output_is_real:1976`
would not catch the result either - it keys on an `available: true/false` convention that
`subtitle_plan` does not use.

That is the defect to remove: not "there is no contract", but "the contract can only say a
key is present, never that its content satisfies the requirement."

### Scope of the contract work

Materially larger than "wire up the prose", because 71 of 90 preconditions are duplicates
that must be *deleted* rather than implemented, and the 19 that remain need a vocabulary
that does not exist yet (environment probes, field predicates, coverage over an interval).

## 3. Gap 2 - how much functionality is welded into the step

### Shape of the 29 steps

Classified by `run_pipeline.get_step_implementation:209`, which infers a step's type from
**which files are present** rather than from a declaration:

| type | count | how it runs |
|---|---:|---|
| `deterministic` | 14 | `step.py` as a subprocess |
| `hybrid` | 11 | `handoff.md` prompt + `bridge.py` / `post_bridge.py` |
| `deterministic_with_llm` | 3 | `step.py` then an informational prompt |
| `llm_only` | **1** | `handoff.md` alone - only 2.01 `creative_direction` |

**The first correction to the brief's framing.** The 12 directories without a `step.py` are
not prompts. **Eleven of the twelve are hybrid Python** - a `bridge.py` that compresses
context and a `post_bridge.py` that resolves the model's answer against measurements, both
invoked by the same JSON-over-stdin convention through `run_hybrid_step:1843`. They add
**4,153 code LOC** the "17 step.py files" framing misses, and they hold some of the
pipeline's most substantive algorithms - the passage-anchoring search of AGENTS.md section 6
(`step_2_02/post_bridge.py:341`, ~400 LOC over 15 helpers), SFX placement
(`step_4_04/post_bridge.py:222`), B-roll placement (`step_3_02/post_bridge.py:139`).

The real step-local surface is **~9,200 code LOC**, not the ~5,000 in `step.py` alone.

### The second correction, and it is the one that shrinks the job

**Step modules are already importable, and the repo already imports them.**

```
$ python3 -c "from library.steps.step_4_01_plan_subtitles.step import generate_subtitles"
IMPORT OK -> generate_subtitles library.steps.step_4_01_plan_subtitles.step
signature: (audio_spine: dict, caption_case: str = 'lowercase',
            brand_effect: dict = None, brand_style: dict = None,
            project_folder: str = '') -> dict
```

There is no `__init__.py` anywhere under `library/steps/` - PEP 420 namespace packages carry
it. **64 test files, 127 import statements**, already reach into step modules in-process.

So "the functionality is unreachable outside a DAG run" is **false as stated**. What is true
is narrower and more useful: *there is no registry, no declared contract, and no uniform
entry point.* An operator LLM cannot enumerate what exists, cannot learn what any of it
needs, and gets no actionable refusal when a prerequisite is absent.

### How much is inline domain logic

Measured across the 17 `step.py` bodies, separating real computation from plumbing (arg
parsing, stdin/stdout marshalling, state read/write, logging, repeated result-dict shapes):

- **~2,370 of 5,028 code LOC (~47%)** is inline domain logic; ~53% is plumbing and
  delegation. Raw line counts overstate the weld badly - 40-45% of the three biggest files
  is docstring and rationale comment.
- **69% of the weld is in three files**: `temporal_index` (~500), `plan_subtitles` (~370),
  `compile_manifest` (~765) hold 1,635 of the 2,370. The other 14 steps average **52 welded
  LOC each**.
- **That concentration is a property of `step.py` only, and it does not survive counting the
  bridges.** Across all **39 executable bodies** the top 3 hold **37% of 7,168 domain LOC**,
  and **it takes 13 bodies to reach 69%**. Both figures are true of what they measure; the
  39-body one is the one a migration plan should be sized against.
- Against `library/tools/` at 188 files / 68,047 lines, the welded remainder is **3.5% of
  the tools corpus.**

Per-step verdict, on two axes - *is the logic already in tools* and *is it shaped so it
could be called*:

| step | code LOC | tools imported | inline domain | already in tools | shaped to call |
|---|---:|---:|---:|---|---|
| 1.02 catalog_footage | 238 | **0** | ~127 | WELDED | yes, pure fns |
| 1.04 temporal_index | 1296 | 4 | **~500** | WELDED | yes, a file move |
| 3.01 assign_aroll | 135 | 1 | ~82 | WELDED | yes, pure fns |
| 3.03 review_rough_cut | 263 | 2 | ~160 | WELDED | yes, 7 pure fns |
| 4.01 plan_subtitles | 550 | 3 | **~370** | WELDED | yes, library + 24-line CLI |
| 5.04 compile_manifest | 1268 | 21 | **~765** | MIXED | ~500 yes / ~270 **no** |
| 0.01, 1.05, 1.07, 4.05 | - | - | - | MIXED | 4 have **no top-level function at all** |
| 1.01, 1.03, 1.06, 2.06, 5.02, 5.03, 6.01 | - | 1-6 | ~15-75 | EXTRACTABLE | yes |

**This is an extraction job, not a rewrite.** Every `temporal_index` analyzer is
`path -> dict`, pure of state and stdout, with its heavy ML imports (`torch`, `whisperx`,
`cv2`, `librosa`) already function-local so importing the module costs nothing.
`plan_subtitles` is a caption-layout library with a 24-line CLI bolted on. **Exactly one
body in the repository is genuinely welded to the execution shape**: `compile_manifest`.

### The calling convention - uniform, with two exceptions

Both call sites build argv as `[sys.executable, <script>]` with **no arguments** and speak
JSON over pipes:

- `run_deterministic_step:1198` and `run_subprocess:1822` both delegate to
  `_run_step_subprocess:1120`, which writes `json.dumps(inputs)` to stdin and reads stdout
  on three threads (the docstring at `:1137` records the deadlock that forced it).
- Every one of the 17 does `json.loads(sys.stdin.read())` in `main()`.
- Output is `json.loads(stdout)` at `:1203`, stored at `:2663` as
  `state["step_outputs"][node_id] = output`.

So **a capability layer wraps one contract, not seventeen.** The exceptions:
`step_1_04/step.py:2056` adds an argparse parser (harmless - the runner passes no argv), and
`step_5_04/step.py:2129` branches on `len(sys.argv) >= 2` into a standalone filesystem mode,
which is a real divergence.

### The five hardest to expose, and why

1. **5.04 `compile_manifest`** - the only genuine weld. `compile_manifest(out_dir)`
   (`step.py:1192`) takes a *directory path* rather than data, mutates the **module-level
   global** `_STATE_OUTPUTS` (`:389`) - the only global in any step, so it is non-re-entrant
   and unsafe to call twice or in parallel - and calls `load()` 18 times against a four-tier
   filename lookup. Mitigating: ~500 LOC of it (every `_assert_*`, `_conform_fields`,
   `_resolve_v2_overlaps`, `_video_coverage_gaps`) are pure predicates over the manifest
   dict, and the tests already import eight of them directly.
2. **6.01 `render`** - a 20-LOC shell that still cannot be called in-process. It is the only
   step driving Resolve, its engine (`resolve_build_timeline.py`, 2,063 lines) sits in the
   step directory rather than `library/tools/`, and AGENTS.md section 5's process-isolation
   rule forces a separate process because clip references go stale after timeline creation.
3. **1.05, 1.07, 2.06, 4.05** - **no top-level callable function at all**: 209, 62, 102 and
   148 code LOC sit entirely inside `main()`. `main()` must be split before anything is
   callable.
4. **4.01 `plan_subtitles`** - owns a measurement nothing else can reach.
   `CaptionFitter.fits_in_box` is defined at `step.py:233` and exists nowhere else, while
   `library/tools/safe_area.py:83` only *documents* the rule that callers must use it.
5. **1.04 `temporal_index`** - the largest algorithmic body, none of it in tools. Not
   entangled; the blocker is runtime - GPU, WhisperX, and a known memory-pressure path.

### What is already callable and is being shelled out to anyway

Three analysis engines already expose importable entry points and their steps invoke them
through a CLI regardless: `analysis/music_pipeline.py:584` `analyze_music(...)`,
`analysis/speech_advanced_pipeline.py:215` `analyze_speech_advanced(...)`,
`execution/resolve_render.py:131` `render_timeline(...)`. **Three capabilities can be
exposed by calling what already exists, with zero extraction.**

### Reachable is not addressable

`library/tools/` holds **188 modules**; **38** already have a `__main__` block and are
callable from a shell today; **15** expose a `main(argv)`. There is no index, no registry, no
shared argument shape, no declared prerequisites and no discovery.

Precedent worth noting: `manage_project.py` already carries two operation-shaped commands
that are not DAG steps - `propose-reels` and `build-reels` (`manage_project.py:640,652`).
Somebody has already needed exactly this shape and added it by hand, twice.

## 4. Gap 3 - region-scoped re-entry

### What invalidation can address today

`step_ledger.parse_rerun_target:303` accepts exactly three kinds, and the list is complete:

```
preflight | edit           -> stage
temporal_index             -> step
temporal_index:clip_007    -> clip
```

**There is no interval form and no place one could be accepted.** `apply_rerun_requests`
(`run_pipeline.py:583`) then clears a stage, or pops one step's whole output and deletes
every clip's declared artifacts, or deletes one clip's artifacts.

Two further limits, both measured:

- **Only five steps have per-clip granularity at all** - `semantic_analysis`,
  `temporal_index`, `prosody_analysis`, `object_segmentation`, `ocr_extraction`. All are
  preflight. **`plan_subtitles` and `render_subtitles` declare no `per_clip_artifacts`**, so
  `--rerun plan_subtitles:<anything>` raises `LedgerError` at `run_pipeline.py:630` -
  "declares no per_clip_artifacts, so it has no per-clip granularity to re-run."
- **There is no transitive invalidation.** `_rerun_invalidates:558` computes only what was
  named; nothing walks descendants. Every downstream step must be listed by hand.

So the finest unit that can touch a transcript is *one whole clip's `temporal_index`* - a
full WhisperX + wav2vec2 pass over the entire clip.

### What the state store is

`pipeline_data.json`, loaded and rewritten whole (`run_pipeline.load_pipeline_state:290`,
`save_pipeline_state:464`), with outputs stored as opaque blobs under
`step_outputs.<node_id>`. There is no sub-key write API and no fragment merge.

### Where the transcript actually lives - four representations

| Where | Key / path | Addressable by |
|---|---|---|
| ground truth | `Area.TEMPORAL_INDEX/<clip_id>.json` - `speech_regions[]` with `words[]` | clip, then time within clip |
| state, 1.04 | `step_outputs.temporal_index.full_indices[]` | clip |
| state, 2.02 | `speech_sequence.body_sequence[]` - `clip_id`, `source_start/end`, `word_timestamps[]` | passage + clip + source interval |
| state, 2.05 | `mesh_spine.audio_spine.structure[]` - the spine blocks | **block position + clip + source interval + timeline interval** |
| **not in state** | `pipeline_output/scratch/timeline_transcript/transcript.json` | **timeline interval, directly** |

The last is the only structure natively in timeline time, and it is produced by a CLI tool
outside the DAG and injected by path rather than by edge (`run_pipeline.py:917-950`).

Crucially, **4.01 does not read a transcript key at all.** It reads `word_timestamps` off
the spine block itself (`step_4_01/step.py:517,535`). So "where the transcript is stored"
has, for the subtitle path, exactly one answer: the spine.

### What already exists that region addressing can be built on

This is the refutation half of gap 3, and it changes the design materially.

| Primitive | File:line | What it already gives |
|---|---|---|
| Spine block | `library/tools/spine_contract.py:93` | `REQUIRED_BLOCK_KEYS` gives every block a **complete dual-domain interval address** - a source interval inside a named clip *and* a timeline interval. Already the join key across 4.01, 4.05 and 5.04 |
| `SEGMENT_BINDING_KEYS` | `library/tools/subtitle_segment_id.py:66` | rendered overlays are **content-addressed** over `(timeline, speaker, block_position, source_clip_id, source_start, source_end)`. A rebuild of one segment overwrites itself and never another |
| `span_cache_key` / `AUDIO_CACHE_KEYS` | `library/tools/timeline_transcript.py:76,141` | **span-level invalidation** of extracted audio, keyed by `(source_file, source_in, source_out)` - written for exactly this ask |
| `reel_captions(transcript, ranges, ...)` | `library/tools/reel_subtitles.py:128` | **working interval-scoped caption generation**, filtering transcript segments by interval overlap |
| `placements_at_frame(ledger, frame)` | `library/tools/timeline_decisions.py:618` | timeline position -> the placements playing there -> the step that owns the decision. `TRACK_DECISIONS:227` maps **V3 -> `plan_subtitles`** |
| `source_to_timeline(source_time, block)` | `library/tools/spine_contract.py:302` | one direction of the conversion |

The captain's own words are in the `timeline_transcript` docstring at line 31, asking for
this feature: *"which also helps to be able to reindex specific portions of clips ... if we
just need to reindex to correct a mistake."* The span cache was built in response. Nothing
consumes it from the DAG.

### What is missing, precisely

1. **The reverse map.** `source_to_timeline` exists; there is no `timeline_to_source`, no
   `block_at_time`, no `blocks_overlapping(structure, start, end)`. Grep across `library/`
   and `tests/` finds the forward function used at three call sites and no inverse anywhere.
   **This single missing function is what stops "45.0-72.0s" being an address.**
2. **A region type.** An interval is encoded four different ways - loose `(start, end)`
   tuples in `reel_subtitles`, `source_start`/`source_end` on spine blocks,
   `timeline_in_frame`/`timeline_out_frame` on placements, and `start`/`end` on transcript
   words. No conversion layer, no owner.
3. **An interval filter on re-indexing.** `timeline_transcript.build_and_transcribe:486`
   takes `only_speakers` and **no time range**, though the cache underneath is already
   interval-addressed and would make a ranged re-index nearly free.
4. **A splice.** Nothing merges a transcript fragment into a stored transcript, or a caption
   fragment into a stored `subtitle_plan`.
5. **A skip-if-present check in 4.05.** `step_4_05/step.py:146` always writes props and
   always shells out to `npx remotion render` for every block. The content-addressed
   filename already makes skipping safe; the check simply is not there.
6. **Region-scoped invalidation.** `--rerun` cannot express it, and the two subtitle steps
   have no sub-step granularity declared at all.

### One hard constraint any splice must respect

`mesh_spine`'s post-bridge recomputes **every** block's `timeline_start` from a cumulative
cursor over block durations (`step_2_05_mesh_spine/post_bridge.py:203`). A spliced fragment
that changes any block's duration shifts every downstream block. A fragment that preserves
durations splices cleanly. This is the boundary between a cheap correction and a full
re-plan, and the design must state which side it is on.

### The cost today, measured

To regenerate subtitles for seconds 45.0-72.0 of a built timeline:

**Step 0 does not exist.** Nothing maps `45.0-72.0` to anything. The operator must open
`pipeline_data.json` by hand and scan `audio_spine.structure[]` for blocks whose
`[timeline_start, timeline_end)` intersects the range.

Then, for a grouping or style change only:

```
--rerun plan_subtitles --rerun render_subtitles --rerun compile_manifest --rerun render
```

which recomputes 4.01 over the entire spine, re-renders **every** block segment through
Remotion (no on-disk skip, 180s timeout *per segment*), recompiles the whole manifest, and
issues a `CreateEmptyTimeline` that re-places every clip on every track
(`step_6_01_render/resolve_build_timeline.py:728`).

And if the transcript itself is wrong in that range - the captain's actual example - add
`--rerun temporal_index:clip_00N` plus `speech_sequence`, `mesh_spine`, `assign_aroll`,
`select_broll`, `review_rough_cut`: a full WhisperX pass over the whole clip, a full
re-plan of the edit's structure by two LLM/hybrid steps, and - because `mesh_spine`
re-derives every `timeline_start` - every timeline position in the video may move.

**Net: to fix 27 seconds of captions you re-run the pipeline from transcription to render.**

### Two defects found on the way, reported not fixed

Per the task's scope rule, these are reported to firstmate rather than fixed here.

1. **A documented recovery command that cannot run.** `run_pipeline.py:949` and
   `run_scope.py:208` both tell the operator to run
   `python3 -m library.tools.timeline_transcript <project> --write`. That flag does not
   exist - `timeline_transcript.main:525` offers only `--model`, `--speaker`, `--out`, and
   always writes. `timeline_ingest` is the module that has `--write`; the two were
   conflated.
2. **`plan_subtitles` declares two required inputs it never reads.** `speech_sequence` and
   `rough_cut_review` are `required: true` and edge-routed, and the step's code names
   neither. The repo's own survey already prints this - `python3 -m
   library.tools.input_contract` reports both as `UNREAD ... required` - but it is reported
   rather than failed, and neither appears in `UNCONSUMED_DECLARATIONS` or
   `REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT`. It is load-bearing for this task: a
   region-scoped subtitle re-plan must today have both on file for a step that reads neither.

## 5. Gap 4 - triggers and hooks

Confirmed, without qualification.

`find library -iname "*hook*" -o -iname "*trigger*" -o -iname "*event*"` returns **nothing**.
`grep -rn "register_hook\|on_event\|emit_event\|dispatch_event" --include="*.py" library/`
returns **nothing**. There is no `.claude/settings.json` hook configuration in the repo
(only a gitignored `settings.local.json`). Nothing runs a script on a condition and nothing
steers a model on a condition.

Two existing pieces a hook layer should sit on rather than duplicate:

- `library/dashboard/review_channel.py` - a durable per-project note store with an agent
  that parks on `wait_for_batch:267` and replies onto the same surface. It is the
  **delivery** mechanism for steering an LLM; it lacks only a non-human sender.
- `library/tools/marker_routing.py` + `library/tools/timeline_decisions.py` - a note
  attached to a timeline frame is already routed to the step that owns that frame's
  decision, and an ambiguous one is reported as ambiguous rather than guessed.

One agent skill already exists in-repo - `.agents/skills/davinci_resolve_pipeline/` - but it
is documentation and diagnostic scripts, not a capability surface over the pipeline.

## 6. What the test suite pins

The known unknown was whether 254 test files pin the step-shaped structure hard enough that
the migration cost lands in tests rather than code. Measured:

| Tests that... | count | of 254 |
|---|---:|---:|
| import `library.tools.*` directly | **196** | 77% |
| import `run_pipeline` | 60 | 24% |
| reference `manifest.json` | 36 | 14% |
| reference `dag.json` / `load_dag` | 30 | 12% |
| reference `step_outputs` | 30 | 12% |
| reference `preconditions` | **1** | 0.4% |

The answer is **no**. Three quarters of the suite already tests the tool layer directly and
independently of the DAG, so a capability surface built over `library/tools/` inherits that
coverage rather than invalidating it. Exactly one test file touches the prose preconditions,
and it only asserts a substring.

The DAG-coupled quarter is real and must keep passing unchanged; nothing in this audit
suggests the DAG should move.

## 7. What this implies for the design

Not a recommendation - that is the design document's job, and the captain's gate. What the
evidence constrains:

1. The missing axis is **addressing**, not capability. A design whose centre of gravity is
   "extract everything into new modules" is solving a problem the measurements do not show:
   step modules already import, 64 test files already call them, three analysis engines
   already expose entry points their own steps shell out to, and six region-addressed
   primitives already exist. What does not exist is a **name** for any of it, a **declared
   prerequisite** on any of it, and an **address** finer than a step.
2. A contract layer must be able to express three things `inputs[].required` cannot: a
   **predicate on content** (`rough_cut_review.passed is true`), an **environment probe**
   (`npx is on PATH`), and **coverage over an interval** (`the transcript has words for
   45.0-72.0s`). All three appear in the 19 unenforced preconditions.
3. There must be **one** contract owner. Adding a fifth system beside the existing four is
   the failure mode this audit exists to prevent.
4. The region type has to be settled first, because four modules currently encode an
   interval four different ways.
5. Backward compatibility is cheap on the test side (77% already tool-level, and the 64
   files that import step modules prove in-process calling is already load-bearing) and must
   be absolute on the DAG side (24%).
6. Four steps have **no top-level function at all** - their whole body is inside `main()` -
   and one step (`compile_manifest`) mutates a module-level global and is not re-entrant.
   These are the only places the capability work is genuinely surgery rather than naming.

## 7A. The seams - what only shows up when you read the whole system

Sections 2-6 take each suspected gap on its own ground. This section is the other read: the
runner end to end, and the places where the four areas MEET. Every seam here is a fact
about the boundary between two subsystems, and none of them is visible from inside either
one.

### Seam 1 - the step service envelope: eighteen services, none reachable off the DAG

The runner's per-step loop wraps `execute_step_once()`
(`run_pipeline.py:2506-2536`) in a substantial envelope. Reading the loop from
`:2385` to `:2745`, a step body gets, in order:

| | service | source |
|---|---|---|
| 1 | handbrake check at the step boundary | `run_control.hold_requested:2392` |
| 2 | phase-boundary VRAM release | `unload_all():2424` |
| 3 | skip-if-already-completed | `step_ledger.is_completed:2437` |
| 4 | review-gate resume - approved / rejected / **revised output merged back** | `:2439-2460` |
| 5 | run-status write, so the dashboard and panel can see the current step | `run_control.write_run_status:2466` |
| 6 | input gathering **and the contract refusal** | `gather_step_inputs:2478` |
| 7 | captain's marker notes routed to this step, and the delivery RECORDED | `marker_routing.record_delivery:2493` |
| 8 | provenance snapshot before/after, so every file learns which step wrote it | `_provenance.snapshot():2503`, `.observe():2688` |
| 9 | retry with transient-error detection and per-node `error_policy` | `:2538-2563` |
| 10 | output-key wrapping for LLM steps with no post-bridge | `:2577-2585` |
| 11 | clip-reference validation of LLM output against the catalog | `:2590-2634` |
| 12 | manifest output validation | `validate_step_output:2637` |
| 13 | hollow-output check | `check_output_is_real:2645` |
| 14 | state write, failure clear, ledger record, save | `:2660-2670` |
| 15 | step export for review (`<step>.json` + `.summary.md`) | `_export_step_for_review:2680` |
| 16 | breakpoint / review-gate snapshot with a printed resume command | `gates.armed_at:2691` |
| 17 | last-write-wins trace archiving before the run overwrites them | `run_archive:2380` |
| 18 | the three run-level collectors reset per run | `undetermined` / `direction_contradiction` / `briefing_interview:2350` |

**This is the real finding about "welded".** The audit's section 3 shows the step *bodies*
are already importable and mostly pure functions - so calling them is easy. What is welded
is not the code, it is **these eighteen services**, and every one of them is keyed by
`node_id`. Anything invoked outside the DAG today gets **none** of them: no provenance, no
ledger entry, no gate, no hollow-output check, no handbrake, no run status.

So a capability surface that merely exposes functions produces a **second-class, unrecorded
execution path** running beside a heavily-instrumented one. The design problem is not "how
do I call the function". It is **"how does an operation earn the same envelope a step
gets"** - and that is the question the four ground-specific reads cannot ask.

### Seam 2 - there are two refusal classes, and the contract sits in the weaker one

The run preamble is a **refusal cascade**: five sequential checks, each printing
`✗ REFUSED` and returning `{"status": "REFUSED", "reason": ...}` **before anything is
deleted, written or executed**:

`run_profile:2155` -> `breakpoints:2185` -> `external_inputs:2199` -> `run_scope:2215` ->
`--rerun` parse.

But the input contract - the thing that actually stops subtitles with no transcript -
refuses **inside the loop**, as a bare `RuntimeError` from `gather_step_inputs:2478`. It is
caught by the generic step handler and becomes a **step FAILURE**
(`_record_step_failure:2559`), not a REFUSED.

The consequences are asymmetric and neither is intended:

- a scope error costs a second and leaves no trace;
- a contract error can land forty minutes in, is recorded as a failure against the step, and
  puts the project into `failed_steps` - which `status` then reads on **every subsequent
  run** until that step succeeds (`:2748-2760`).

A content-level or region-level requirement (§7's constraint 2) has **no seat in the
cascade at all**. Any new contract layer must join the pre-run cascade, not add a sixth
mid-loop raise.

### Seam 3 - the status vocabulary can only describe DAG completeness

`status` is computed at `:2764-2771` from `outstanding_failures`, `awaiting_llm` and
`never_run`, where `never_run` is *"every node in `scope.universe` not marked complete"*.

There is no term in that vocabulary for **"this invocation performed work that was not a DAG
node"**. An operation cannot make a run succeed, cannot make it fail, and cannot be
reported. On a fully-complete project an operation-only invocation would report `SUCCESS`
having been invisible; on an incomplete one it would report `PARTIAL` for reasons unrelated
to what it did.

This is a trap with teeth, because AGENTS.md section 3 makes `SUCCESS` a claim about the
whole DAG and the project ledger. Whatever the operation layer records, it must not be able
to launder an incomplete DAG into a success, and it must not be silently omitted from the
account either.

### Seam 4 - provenance is the ONE service that already generalises

`_provenance.observe(node_id, run_id, artifacts_before, artifacts_after)` attributes files
by **diffing a directory snapshot taken before and after**, not by anything the step
declares (`:2503`, `:2688`). The module's own reasoning is that half the steps hand the
writing to ffmpeg, Remotion or Resolve and could not declare their outputs if asked.

That mechanism is **scope-agnostic**. Swap `node_id` for an operation id and it works
unchanged. Of the eighteen services in seam 1, this is the only one that needs no redesign -
which makes it the natural first thread to pull when giving operations an envelope.

### Seam 5 - FIVE encodings of "a word at a time", and two of them collide on key names

Measured on project 001, which has a complete run:

| # | Where | Domain | Word keys |
|---|---|---|---|
| 1 | `temporal_index.full_indices[].speech_regions[].words[]`, and the per-clip files under `pipeline_output/steps/1_04_temporal_index/index/` | **SOURCE** | `start` / `end` |
| 2 | `speech_sequence.body_sequence[].word_timestamps[]` | SOURCE | `source_start` / `source_end` |
| 3 | `mesh_spine.audio_spine.structure[].word_timestamps[]` | SOURCE | `source_start` / `source_end` |
| 4 | `plan_subtitles.subtitle_plan.subtitle_entries[].words[]` | **TIMELINE** | `start` / `end` |
| 5 | `scratch/timeline_transcript/transcript.json.segments[].words[]` | TIMELINE | per `SpokenSegment` |

**Rows 1 and 4 use the same two key names for different time domains.** A splice that reads
a word out of the temporal index and writes it into a subtitle entry - the most obvious
possible implementation of the captain's example - produces captions that are well-formed,
plausible, and wrong by the clip's `source_start` offset. On 001's hook block that error is
0.836s.

This is precisely the failure class AGENTS.md 10.1 names as dominant, and PIPELINE_PLAN
records six historical instances of it. **A `Region` type that owns the conversions is not
ergonomics; it is the guard against this repo's most frequent defect.**

### Seam 6 - the two ledgers, the run status and the collectors are all step-keyed

Four independent records, four step keys:

- `step_ledger` - two ledgers, two lifetimes (preflight / edit), keyed by node id, and
  **an operation has no stage**;
- `run_control.write_run_status(current_step=...)` - what the dashboard and Resolve panel
  read to say what is happening;
- `undetermined` / `direction_contradiction` / `briefing_interview` - all `merge_records`
  keyed by step, explicitly so a narrow run does not erase another step's rows
  (`:2940-3005`);
- `provenance` - keyed by node id, though see seam 4.

Any operation id therefore needs a **declared relationship to a node id** - "this operation
is part of what `plan_subtitles` owns" - or four separate records lose it.
`timeline_decisions.TRACK_DECISIONS` already supplies exactly that mapping for the built
timeline, which is the cheapest available answer.

### Seam 7 - the review gate and the marker channel both terminate at a step

Two return paths from the captain, and neither can carry a region:

- **review gate** - a step-keyed snapshot with `approved` / `rejected` / `revised`, where a
  revision is merged into the step's output (`:2439-2460`);
- **marker notes** - anchored to a timeline FRAME, then routed to *the step that owns that
  frame's decision* by `marker_routing` + `timeline_decisions`.

The marker path is the interesting one: it **starts** region-shaped and is deliberately
collapsed to a step, because a step is the only thing that can act. Give operations a
region address and that collapse becomes unnecessary - the note could route to
`subtitles.plan@45-72` instead of to `plan_subtitles` entire. **The captain's existing
note-taking workflow is already the front half of the feature they asked for.**

### Seam 8 - the hollow-output honesty gate covers a minority of outputs

`check_output_is_real:1976` catches a step reporting success while emitting nothing, and it
does so by looking for an `available: true/false` convention. `subtitle_plan` does not use
that convention, so an empty subtitle plan passes it - which is why the measured CASE 3 in
section 2 produces silence rather than a refusal.

Any operation layer inherits this hole unless each operation declares a **checkable
guarantee** rather than relying on a flag convention its output may not use.

### Seam 9 - one state backup per PROCESS, not per write

`save_pipeline_state:464` backs up `pipeline_data.json` on the first save of a process
(`_BACKED_UP_THIS_PROCESS:461`) and never again. For a runner that is right: one snapshot of
the pre-run state.

For a splice tool performing several partial writes in one process it means **one backup
covering all of them**. Undo-to-before-I-started works; undo-one-splice does not. Stated so
the design chooses deliberately rather than discovering it.

### Seam 10 - the architecture already describes itself as LLM-operated, and the map has drifted

`docs/architecture/pipeline_master_reference.md:171` names Layer 2 **"PLANNING (Antigravity -
you are the LLM)"**, and preserves a hand-run sequence "kept only for running a single layer
by hand" that predates the orchestrator. The ambition in the captain's brief is not new to
this codebase; the out-of-DAG path existed first and decayed when the DAG arrived.

That document has since drifted from the code: it records **"24 nodes, 58 edges"** and
"24/24 manifests" (`:373-374`) where the DAG now has **28 nodes and 112 edges** and there
are 29 manifests. A reader orienting from the master reference starts with a map that is
short by four nodes and half the edges.

### What the seams change about the design

1. The unit of composition must be able to **carry the envelope**, not just the function.
   Eighteen services, all node-keyed, is the actual scope of "reachable outside a DAG run".
2. A new contract must **join the pre-run refusal cascade**, where refusals are cheap and
   leave no failure record - not the mid-loop raise, which costs a step failure that
   persists across runs.
3. The `Region` type earns its place as a **defect guard**, not a convenience: two of the
   five word encodings collide on key names across different time domains.
4. `provenance` generalises today and is the cheapest first increment.
5. Operations need a declared **owning node**, because four separate records are step-keyed;
   `TRACK_DECISIONS` already provides that mapping.
6. `status` must gain a way to report operation work without being able to launder an
   incomplete DAG into `SUCCESS`.

## 8. Defects found while auditing - REPORTED, not fixed

The task's scope rule is explicit: a real defect found in passing is reported to firstmate
rather than fixed inside this change. All five were confirmed by reading the code, not
inferred.

| # | Defect | Evidence |
|---|---|---|
| 1 | **A documented recovery command that cannot run.** `run_pipeline.py:949` and `run_scope.py:208` both instruct the operator to run `python3 -m library.tools.timeline_transcript <project> --write`. That flag does not exist - `timeline_transcript.main:525` offers only `--model`, `--speaker`, `--out`, and always writes. `timeline_ingest` is the module with `--write`; the two were conflated | verified by reading the argparse block |
| 2 | **`plan_subtitles` declares two required inputs it never reads.** `speech_sequence` and `rough_cut_review` are `required: true` and edge-routed; the step's code names neither. The repo's own survey already prints both as `UNREAD ... required`, but reports rather than fails, and neither is in `UNCONSUMED_DECLARATIONS` or `REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT` | `python3 -m library.tools.input_contract` |
| 3 | **The per-speaker caption fitter is not used for the fit report.** `step_4_01/step.py:631` resolves a per-speaker `block_fitter` and groups with it; `:879` then computes `fit_scale` with the shared `fitter`. A project with per-speaker font sizes groups against one usable width and reports a scale computed against another | read both lines |
| 4 | **Dead code.** `1.04:_merge_speech_regions` (L445, no callers), `1.04:compress_for_downstream` (L1971, no callers), `1.04:detect_speech_regions`'s unused `output_dir` parameter, `4.01:enforce_min_duration` (L291, no callers - re-implemented inline at L836), `4.01`'s unused `block_style`, `5.04`'s unused `require_keys` import | grep for callers |
| 5 | **`needs_conform` is duplicated.** `step_3_01/step.py:29` and `step_3_02/post_bridge.py:43` (`check_needs_conform`) are the same predicate in two places | read both |

| 6 | **The master reference has drifted from the DAG.** `docs/architecture/pipeline_master_reference.md:373` records "24 nodes, 58 edges" and "24/24 manifests"; the DAG has **28 nodes and 112 edges** and there are 29 manifests. It is titled "the single source of truth for the entire automated video editing pipeline", so a reader orienting from it starts short by four nodes and half the edges | counted from `dag.json` |

Defects 1 and 2 are load-bearing for this task and the design must account for them; 3, 4,
5 and 6 are independent and belong to whoever owns those steps.

## Appendix - how each number was obtained

| Claim | Command |
|---|---|
| 90 preconditions / 36 postconditions / 29 manifests | iterate `library/steps/*/manifest.json`, count `interface.preconditions` / `.postconditions` |
| 71 redundant / 19 unenforced | for each precondition, extract `'<key>'` and test membership in that manifest's `required: true` inputs |
| 0 evaluations | `grep -rn "precondition\|postcondition" --include="*.py" .` |
| CASE 1/2/3 | direct call of `run_pipeline.gather_step_inputs("plan_subtitles", dag, state, manifest, external={})` on three hand-built states |
| step type counts | `run_pipeline.get_step_implementation`'s own rules applied to all 29 step directories |
| 188 tools / 38 with `__main__` / 15 with `main(argv)` | `find library/tools -name '*.py'`; `grep -rln '__name__ == "__main__"'`; `grep -rln "def main(argv"` |
| 0 `__init__.py` under `library/steps` | `find library/steps -name "__init__.py"` |
| rerun granularity | `run_pipeline._rerun_invalidates:558` |
| test coupling table | `grep -rl <term> tests/ \| wc -l` against `ls tests/test_*.py \| wc -l` |
