# Making the pipeline operable by an LLM

Design, 2026-09-05. Phase B of the LLM-native refactor. Its evidence is
[`llm_native_audit.md`](llm_native_audit.md) - in particular section 7A, the seams - and
every claim beginning "today" is measured there.

**The recommendation in one line: make every step SCOPE-ADDRESSABLE and give it an
executable contract, exposed through a registry of named operations that call the steps'
own code - no parallel implementations, no new prose contracts, no invented thresholds.**

## 0. Three captain rulings this design is bound by

Read before the proposal, because two of them rule out designs that would otherwise look
obvious.

**Ruling 1 - no parallel modules.** (`CAPTAIN-ARCHITECTURE-RULING.md`, 2026-09-05, verbatim)

> "you are meant to be using the pipeline directly to be doing things properly not creating
> one-off scripts like 'reel_subtitles.py did for captions' like no, the subtitles was meant
> to utilize the pipeline subtitles step and whatever funcitonality was put into that python
> file should have been augmented into the pipeline -- we are not tryig to create any
> standalone artifacts and scripts"

So a capability surface may **not** re-implement what a step does. An operation must invoke
the step's own code. The permitted shape for a new module was settled in the same round and
is quoted in `REEL-CAPABLE-SHAPE.md`: *"it is the ONLY new module; it is a producer, not a
parallel implementation."*

**This design adopts that as its test of legitimacy:**

> A new module is legitimate if it **produces** an input a step already consumes, or
> **addresses / scopes** an existing step. It is illegitimate if it performs work a step
> already performs.

**Ruling 2 - do not invent a threshold.** (`atomic-is-not-a-metric.md`) A mechanical proxy
was fitted to the captain's verdicts and failed to predict them; the standing rule is that
there are no hardcoded values in this pipeline, and taste belongs to the model. What is
mechanically checkable - presence, provenance from a declared list, buildability - is an
engine check. Coherence is not. **Every requirement in §2 is mechanical. None encodes
taste, and none carries a number this design invented.**

**Ruling 3 - a gate that cannot fail is worse than none.** (AGENTS.md 10.4, and now a third
live instance - see §2's empty-side rule.)

## 1. The problem, restated from the measurements

The audit's diagnosis: *every control vocabulary this pipeline has is addressed by **step
id** and keyed by **state key**. There is no vocabulary for an operation smaller than a
step, and no vocabulary for a region smaller than the whole project.*

Section 7A sharpened what "welded" actually means, and it is not the code:

- step bodies are already importable (64 test files, 127 import statements), so **calling
  them is not the problem**;
- but the work is **less concentrated than an earlier draft of this design claimed**. "69% of
  the inline logic sits in 3 of 29 steps" was measured over `step.py` **only**. Across all
  **39 executable bodies** - adding the 11 `bridge.py` and 11 `post_bridge.py` halves - the
  top 3 hold **37% of 7,168 domain LOC, and it takes 13 bodies to reach 69%**. The extraction
  is still real and still mostly naming, but it is spread across thirteen bodies rather than
  three, and the design says so here rather than letting it surface during the build;
- what is welded is the **service envelope** - the runner wraps eighteen node-keyed services
  around every step body: handbrake, ledger, gate resume, input contract, marker delivery,
  provenance, retry policy, output validation, hollow-output check, run status, review-gate
  snapshot and more;
- anything invoked outside the DAG today gets **none** of them.

So the design question is not "how do I call the function". It is **"how does an operation
earn the same envelope a step gets"**, and the answer that satisfies Ruling 1 is: *it earns
it by being the step, run at a narrower scope.*

## 2. The proposal

### 2.1 Scope, not a second code path

Today a step runs against the whole project. The change is to let the same step run against
**a declared scope**:

```python
@dataclass(frozen=True)
class Scope:
    kind: str           # PROJECT | CLIP | REGION
    region: Region | None = None
    clip_id: str | None = None
```

`PROJECT` is what every step does today and stays the default, so every existing run is
unchanged. `REGION` is the new axis.

This is the same shape the captain already ruled on for reels - *"a step needs to be able to
run against a reel timeline the same way it runs against a master"* - and the two converge:
**a reel is a set of ranges, and a region is a range.** One mechanism serves both, which is
why this design does not add a reel-specific path.

### 2.2 `Region` - one owner for an interval, and it is a defect guard

An interval is encoded four ways today and word timings five ways, and **two of the five
collide**: `temporal_index...words[]` and `subtitle_entries[].words[]` both use `start`/`end`
for **different time domains** (audit 7A, seam 5). The obvious implementation of the
captain's splice - read a word from the index, write it into a caption - is wrong by the
block's offset, and it would look completely plausible.

**Measured on 001, and it is worse than a constant error.** The offset is
`timeline_start - source_start`, and it is **per block**:

```
block  hook   clip_011   src   0.836  tl   0.000   offset    -0.836s
block  2      clip_011   src   9.699  tl   5.398   offset    -4.301s
block  3      clip_017   src  30.073  tl   8.380   offset   -21.693s
block  5      clip_017   src  45.441  tl  21.927   offset   -23.514s
block  7      clip_011   src  63.135  tl  35.076   offset   -28.059s
block  8      clip_011   src 119.234  tl  38.616   offset   -80.618s
block 10      clip_011   src 173.639  tl  44.322   offset  -129.317s
block 11      clip_012   src  33.941  tl  51.142   offset   +17.201s

8 blocks, 8 DISTINCT offsets. 7 negative, 1 positive. Spread 146.518s.
```

**The sign flips.** So a naive splice is not off by a constant that a reviewer might notice
as a uniform drift - it is wrong in both directions, by up to **129.3 seconds on a
56.6-second timeline**. There is no single correction factor, and no amount of eyeballing
one block generalises to the next. A type that carries the domain is the only thing that
makes this class of error impossible rather than merely unlikely.

That is the dominant defect class this repo names (AGENTS.md 10.1; PIPELINE_PLAN records six
historical instances). So `library/tools/region.py` owns the conversions, and the domain is
part of the type rather than a convention:

```python
@dataclass(frozen=True)
class Region:
    start: float          # TIMELINE seconds, always - the type has one domain
    end: float
```

`RegionAddress` is what resolving one against a project yields: the spine block positions it
overlaps, the `(clip_id, source_start, source_end)` spans behind them, the tracks carrying
picture and captions there, and the DAG node that owns each - which
`timeline_decisions.TRACK_DECISIONS` already supplies (V3 -> `plan_subtitles`).

The one genuinely missing function lands beside its forward twin, **in the module that owns
the contract**, not in a new one:
`spine_contract.timeline_to_source(t, block)` and
`spine_contract.blocks_overlapping(structure, start, end)` - the inverse of
`source_to_timeline` at `spine_contract.py:302`, which has no inverse anywhere in the repo.
**This single function is what turns "45.0-72.0s" into an address.**

### 2.3 Requirements - executable, one owner, mechanical only

`library/tools/requirements.py` becomes the single owner. It does not sit beside the
existing systems; it absorbs or re-points all four.

```python
@dataclass(frozen=True)
class Requirement:
    name: str
    describe: str                    # one sentence, used verbatim in the refusal
    produced_by: tuple[str, ...]     # OPERATION names that would satisfy it
    check: Callable[[Context], Satisfaction]
```

`Satisfaction` is never a bare bool: `SATISFIED(source)` or
`UNSATISFIED(reason, missing, produced_by)`, where `source` is `IN_STATE` / `RECORDED` /
`SUPPLIED` / `PRODUCED_BY`. A pass always says how it passed; a refusal always says what to
run - which is exactly what the brief demands of a refusal.

Four kinds, and **all four are mechanical** (Ruling 2):

| kind | expresses | covers |
|---|---|---|
| `state_key(k)` | a key is present | **auto-derived** from `inputs[].required` + `data_mapping`, never hand-written |
| `predicate(path, test)` | a value satisfies a test | `rough_cut_review.passed is true` - declared 4x today, enforced 0x - **plus the two content clauses below** |
| `environment(probe)` | the machine can do the work | `npx on PATH`, `parselmouth importable` |
| `coverage(region)` | the data spans the region asked for | word timings exist across 45.0-72.0s |

The last two have **no expression at all today**, which is the structural reason those
preconditions could only ever be prose.

**The real count is 21, not the 19 the audit reported.** The audit's classifier keyed on the
phrase `exists in state` and so filed two preconditions as redundant when only their
*existence* half is:

```
step_1_05_prosody_analysis   'temporal_index' exists in state with speech regions
step_2_06_music_analysis     'music_selection' exists in state with a valid track path
```

Both keys are declared required and routed, so presence is enforced. **"with speech regions"
and "with a valid track path" are content requirements and nothing enforces them** - exactly
the `predicate` kind. Corrected upward here because an undercount in the direction of "less
work than it looks" is the kind that bites later.

What each existing system becomes:

| system | fate |
|---|---|
| prose `preconditions` / `postconditions` (90 + 36) | **DELETED from all 29 manifests.** 71 were redundant restatements of `inputs[].required`; the **21** real ones become `Requirement`s. `postconditions` become `Guarantee`s, or are deleted where nothing can check them (Ruling 3) |
| `inputs[].required` + `data_mapping` | **STAYS, and becomes the SOURCE** for auto-derived `state_key` requirements. Runtime behaviour unchanged |
| `run_scope` + `gather_step_inputs` | **STAY.** Re-pointed to build refusals through `requirements`, so there is one refusal vocabulary |
| `input_contract.py` | **STAYS as the static auditor**, and gains one job: fail if any manifest still carries prose preconditions. That is what stops the defect returning |
| `external_inputs.py` | becomes a **satisfier** reporting `source=SUPPLIED`. `WITHDRAWN` preserved verbatim; no guarantee weakens |

### 2.4 The refusal joins the pre-run cascade

Seam 2 found two refusal classes with very different costs. The run preamble refuses five
times **before anything is written**, cheaply and traceless. The input contract instead
raises **mid-loop** and is recorded as a **step failure**, which then colours `status` on
every subsequent run until that step succeeds.

**Requirements are checked in the cascade**, as a sixth stage, before `--rerun` deletes
anything. A missing prerequisite becomes a REFUSED, not a failure record.

**And the cascade has a hole the design must close.** `--only X --skip Y` refuses before the
run, naming a producer and two ways out. `--step X` and `--from X` do not: they select the
whole universe and then filter the *execution* list (`run_pipeline.py:2268-2292`), so an
unmet prerequisite surfaces as a **mid-loop crash recorded as a step failure**. Since
`--step` is the flag an operator reaches for when correcting one thing, it is the flag most
likely to hit this. Requirements must be checked against the steps that will actually
**execute**, not against the selection.

### 2.5 Two rules that stop this becoming the third vacuous gate

The reel conformance verifier is a live instance of the class: `reel_conformance_verifier.py:1519`
guards its caption check with `if plan.captions and timeline.caption_items:`, `plan.captions`
defaults to `()` at `:177`, and `:1597` reports `captions_expected=0`. So an empty plan-side
**silently disables** the check, and 762 real captions pass verified by nothing.

Two rules follow, and both are tested:

1. **An empty expected side is a REFUSAL, never a skip.** A requirement or guarantee whose
   reference set is empty must say so and fail, not pass quietly.
2. **A requirement's expected side may not be derived from the same value as its actual
   side.** That is what makes a check tautological.

And the anti-vacuity gate itself, which is the thing that proves the layer has teeth:

- `tests/contracts/test_every_requirement_can_refuse.py` iterates the **whole registry** and asserts,
  for each requirement, that there is a context in which it returns `UNSATISFIED`, that the
  refusal names a non-empty `produced_by` (or records why nothing produces it, in the
  `external_inputs.WITHDRAWN` shape), and that `SATISFIED` carries a `source`.
- `tests/contracts/test_no_requirement_refuses_correct_input.py` is the mirror, so the layer cannot be
  vacuously strict either (AGENTS.md 10.4: a gate that FAILS correct output is no more
  coverage than one that cannot fail).

### 2.6 Operations - a registry that calls the steps, and owns no logic

`library/tools/operations.py`. An operation is a **named, scoped entry point into an
existing step's own code**:

```python
@dataclass(frozen=True)
class Operation:
    name: str                    # "subtitles.plan"
    summary: str
    owning_node: str             # "plan_subtitles" - the DAG node whose decision this is
    scopes: tuple[str, ...]      # (PROJECT, REGION)
    requires: tuple[Requirement, ...]
    produces: tuple[Guarantee, ...]
    run: Callable                # POINTS AT THE STEP'S OWN FUNCTION
```

`owning_node` is not decoration. Seam 6 found four separate records that are all step-keyed -
the two ledgers, the run status, and the three collectors - so an operation without a
declared owning node loses its place in all of them.

**Ruling 1 is enforced, not merely intended:** a test asserts every operation's `run`
resolves into `library/steps/` or into a `library/tools/` module the owning step already
imports. An operation that introduces a second implementation of a step's work fails the
suite.

**That test is drafted and has been proven able to fail** - it was shown rejecting a
deliberately planted second implementation. So the rule that protects Ruling 1 is not itself
another vacuous gate, which given §2.5 is the first thing worth asking of it.

Reachability and discovery:

```
python3 -m library.tools.operations --list
python3 -m library.tools.operations subtitles.plan --project <p> --region 45.0-72.0
```

Same registry, same requirements, same refusal, whether called from the shell, from Python,
or by the runner.

### 2.7 Skills are a PROJECTION of the registry

The captain's guess was skills. This adopts the interface and rejects the storage:

```
python3 -m library.tools.operations --emit-skill > .agents/skills/pipeline_operations/SKILL.md
```

with a test asserting the checked-in skill is byte-identical to what the registry emits.

A hand-written skill would state its prerequisites in prose - **precisely the defect being
removed.** 90 prose preconditions are already evaluated by nothing; writing 40 more in a
different directory would reproduce the defect while looking like progress. Generated, the
prose cannot drift and is never the contract.

### 2.8 Why the alternatives lose

| option | why it loses |
|---|---|
| **Hand-written agent skills** | Prerequisites become prose again. Nothing can test them, nothing else can consume them, and the capability exists twice and drifts. Adopted as an output format, rejected as source of truth |
| **Sub-step nodes in the DAG** | Keeps everything step-shaped - the diagnosed defect - and adds no region axis. Grows 28 nodes toward 100+, and `run_scope`'s closure plus 30 DAG-coupled test files scale with it. Still unreachable outside a run |
| **A verb layer over `library/tools/`** | Wrong granularity, and it violates Ruling 1 the moment a verb does a step's work. The 38 existing `__main__` tools already prove reachable is not addressable |
| **A region API alone** | Solves addressing, leaves the dead contract untouched. Half the recommendation, not an alternative |
| **Extract step logic into new modules** | The measurements do not support it - bodies are already importable, 69% of inline logic is in 3 of 29 steps - and at scale it becomes the parallel-implementation Ruling 1 forbids |

## 3. The captain's worked example, end to end

Target for the demonstration: **project 001**, which has a complete run - 56.6s timeline,
13 spine blocks (8 carrying word timings), 30 subtitle entries, 8 rendered overlay segments.

```
region.resolve      --at 45.0-72.0      -> blocks, clips, source spans, owning nodes
transcript.reindex  --region            -> WhisperX over ONLY those source spans
transcript.splice   --region --fragment -> merge word timings back
subtitles.plan      --region            -> 4.01's own generate_subtitles, region-scoped
subtitles.splice    --region --fragment -> merge into subtitle_plan
subtitles.render    --region            -> 4.05's own loop, region-scoped
manifest.recompile                      -> 5.04
timeline.rebuild                        -> 6.01
```

Every one of the six region operations calls the step that already owns that work.

### Where partial state is spliced

| operation | writes into |
|---|---|
| `transcript.splice` | `temporal_index.full_indices[].speech_regions[]` **and** `mesh_spine.audio_spine.structure[].word_timestamps[]` - **converting domains through `Region`, never by copying keys** |
| `subtitles.splice` | `plan_subtitles.subtitle_plan.subtitle_entries[]`, replacing exactly the region's entries |

`library/tools/state_splice.py` is the **only** thing permitted to write part of
`pipeline_data.json`. It is a producer, not a parallel implementation: it writes state that
steps already consume. It re-asserts the spine contract over the result and records the
splice in provenance. Note seam 9: the layout backs up `pipeline_data.json` **once per
process**, so a multi-splice session gets one pre-session snapshot - stated so the operator
knows undo-one-splice is not available.

### The boundary rule that decides cheap versus expensive

**The cost this removes, measured on 001.** `--rerun edit` is the only supported path today
for a caption change, and the edit stage costs **896.8s (14.9 min)** across its 21 steps,
**plus every LLM call in it**, plus a full Resolve rebuild under a new timeline. If the
transcript itself is wrong, add ~34s to re-index the clip: **~15.5 minutes and seven LLM
calls to correct 27 seconds of captions.**

`mesh_spine`'s post-bridge recomputes **every** block's `timeline_start` from a cumulative
cursor (`post_bridge.py:203`). Therefore:

> **A splice that preserves every touched block's duration is a local correction. A splice
> that changes any duration shifts every downstream block and is REFUSED**, naming the
> block, both durations, and the full re-plan that would be required.

This is a mechanical check on measured durations - not a threshold, and not taste (Ruling 2).

### What invalidates downstream

Derived from the region, never declared. The manifest and the built timeline are whole
artifacts and are always re-derived. `--rerun` gains a fourth form, `<step>@<start>-<end>`,
parsed in the same `step_ledger.parse_rerun_target` that owns the other three.

#### Correction: the rendered overlays are IDENTITY-addressed, not content-addressed

An earlier draft of this design said overlays are "already content-addressed over their own
region" and gave `subtitles.render` a skip-if-present check keyed on that. **That was wrong,
and it would have silently skipped exactly the work the operator asked for.**

`subtitle_segment_id.segment_binding` (`:113-132`) returns precisely:

```
{timeline, speaker, block_position, source_clip_id, source_start, source_end}
```

**No caption text. No grouping. No style. No font size.** `segment_identifier` and
`binding_digest` derive the filename from those keys and nothing else. Measured on 001 with
the production functions: re-planning one block so that **every caption's text changed**
altered **0 of 8 filenames**.

So the captain's actual request - *"why are the subtitles so big?"*, *"regenerate just this
segment"* - is a **content** change that leaves the identity untouched. A skip-if-present
keyed on the binding would find the file present, skip the render, leave the stale `.mov` on
disk and report success. `compile_manifest._assert_subtitle_overlay_matches_plan` would pass,
because it only checks that the segment *spans* the captions' time range, which does not
move. The pipeline would report a clean region rework and ship the old captions - AGENTS.md
10.4's exact failure: *a gate that cannot fail reads as coverage.*

**The decision: keep skip-if-present, and key it on content that already exists on disk.**
Step 4.05 already writes `<segment_name>_props.json` immediately before rendering
(`step_4_05/step.py:172-176`), and those props carry `subtitles` and `style`
(`generate_remotion_props.py:176,181`) - everything that determines the pixels. So:

> Skip when `overlay.mov` exists **and** the props on disk hash-match the props about to be
> written. Otherwise render.

The binding keeps the job it is genuinely good at and which the splice actually needs:
**guaranteeing a rebuild overwrites itself and never another segment.** Identity names the
file; content decides whether to rewrite it. The two were conflated and are now separate.

`tests/` must exercise this with a **text-only change**, because that is the single case that
distinguishes the two and the one the wrong design passed.

#### Correction: caption ids renumber globally, which would corrupt the evidence

`generate_subtitles` initialises `sub_counter = 0` once, before the loop over blocks
(`step_4_01/step.py:575`), and increments it across every block (`:673`, `:765`). So
re-planning one block with a different card count **renumbers every entry after it**.

That is phantom churn: entries in blocks nobody touched appear changed. It would corrupt this
design's own Phase D evidence, which claims the untouched timeline is byte-identical.

**Two fixes, and this design takes both**, because one repairs the cause and the other makes
the evidence independent of that repair landing:

1. **Ids become block-local** (`sub_<block>_<NNN>`), and this moves into **increment 1** so
   every later increment measures cleanly. Verified safe: no production code reads
   a subtitle entry's `id` - `grep` finds no consumer in 4.05 or 5.04, and no test asserts an
   id value produced by `generate_subtitles`. The literals in fixtures are test-authored data,
   not assertions about output. This removes the churn at source, permanently, and makes ids
   addressable by block, which a region splice wants anyway.
2. **The evidence compares per-block content excluding volatile ids**, so the demonstration
   proves what it claims even if fix 1 is deferred.

### How the demonstration will be proved

By measurement, using this repo's own established technique - PIPELINE_PLAN: *"render it:
byte sizes and md5s settle arguments that reasoning does not."*

- **md5 every rendered overlay `.mov`** before and after. The region's segments change; every
  other segment's digest is identical.
- **Compare subtitle entries per block, excluding the volatile `id`** (see the renumbering
  correction above), so the evidence measures content rather than counter drift.
- **Include a text-only change**, because that is the case the identity-vs-content confusion
  above would have passed while shipping stale captions.

**The reel conformance verifier will not be used as evidence** - its caption checks are
vacuous (§2.5), and an empty expected side passing 762 real captions is the opposite of
proof.

## 4. Triggers and hooks

`library/tools/hooks.py`, declared as data: repo defaults in `library/hooks.json`, per-project
override. Never code in a config string.

**A closed condition vocabulary** - six, each carrying the payload it observed:
`operation_completed` | `operation_refused` | `requirement_unsatisfied` |
`qa_finding_raised` | `gate_verdict` | `output_empty`. A condition not in the enumeration is
refused at load, the same way `run_scope._reject_unknown` treats an unknown step.

**Two action kinds:**

| action | what it does | bound |
|---|---|---|
| `run` | executes a script from a declared allow-list in `scripts/hooks/`, payload on stdin | never an arbitrary shell string |
| `steer` | writes a note into `review_channel`, anchored to the operation and region | reuses the channel the captain already reads |

`steer` deliberately reuses the existing human channel rather than opening a second one, so
**every automated steer appears in the same feed as the captain's own notes, tagged as
machine-originated.** Automation the captain cannot see is the failure mode worth designing
against.

**Three independent loop guards:** hooks fire at depth zero only (an operation invoked *by* a
hook fires none, and this is not configurable); a fired-hook ledger keyed by
`(run_id, hook_name, condition_fingerprint)` makes each condition instance fire once; and a
per-run budget whose exhaustion is a **reported** failure naming which hooks fired, because a
hook layer that quietly gave up would be indistinguishable from one with nothing to do.

## 5. How the captain stays MORE involved

Nothing is removed. `--review`, `--break`, gates, `--rerun`, profiles, the dashboard and the
marker channel keep working unchanged. Three additions:

1. **Breakpoints extend from step ids to operations and regions** -
   `--break subtitles.render@45-72`, through the same `breakpoints.py`, with the same
   "an unreachable breakpoint is NAMED, never silent" behaviour.
2. **Region selection lands in the dashboard's EXISTING timeline view.** `timeline-view.js`
   and `/api/timeline` already exist, and AGENTS.md 4 says extend this dashboard, never author
   a fresh page. Selecting a span yields a `Region` and offers the operations whose
   requirements it satisfies - **and shows the precise refusal for those it does not**, which
   the captain has never had.
3. **The marker channel stops collapsing.** A note typed on a frame is already routed to the
   step owning that frame's decision - it *starts* region-shaped and is collapsed to a step
   because a step is the only thing that can act (seam 7). With region operations it can route
   to `subtitles.plan@45-72` instead. **The captain's existing note-taking workflow is already
   the front half of the feature they asked for.**

## 6. Migration

### Backward compatibility, stated explicitly

**Unchanged:** the DAG and all 28 nodes; `run_pipeline.py`'s entire CLI; `run_scope`,
`breakpoints`, `run_profile`; review gates and `--resume`; the dashboard; Resolve and Fusion
(AGENTS.md 5 untouched); all 254 test files.

**Changes shape:** `manifest.json` loses `interface.preconditions` / `postconditions` and
gains `interface.requirements`. Exactly one test asserts a substring of the prose
(`test_prosody_failure_is_loud.py:60`) and is rewritten to assert the executable requirement.

**This is now MEASURED, not argued** - and it is the strongest claim in the design. The
parallel audit deleted the prose preconditions from all 29 manifests and ran the suite:

```
FAILED tests/unit/audio/test_prosody_failure_is_loud.py::test_the_manifest_still_declares_the_precondition
1 failed, 1117 passed, 4 skipped, 1 warning in 183.03s
```

**1,122 tests, one failure, and it is exactly the file this design predicted.** The claim
that deleting 126 prose strings breaks nothing else is no longer a forecast.

**Breaks on purpose:** nothing at runtime. The one deliberate break is that a manifest
carrying prose `preconditions` becomes a test failure, so the defect cannot return.

### Increments, each independently green

| # | lands | tests |
|---|---|---|
| 1 | `region.py`; `spine_contract.timeline_to_source` + `blocks_overlapping`; **block-local caption ids in 4.01** | domain round-trips, boundary and zero-length cases; **the collision guard** - a source-domain word cannot be written into a timeline-domain list; and **no phantom renumbering**: re-planning one block leaves every other block's ids untouched |
| 2 | Provenance generalised from `node_id` to an operation id. **Cheapest first thread** - seam 4 found it is the one service that already generalises, because it attributes by snapshot diff | an operation's artifacts are attributed to it |
| 3 | `requirements.py` as single owner; derive `state_key` from existing declarations; re-point `run_scope`/`gather_step_inputs`; join the pre-run cascade; convert the 19 real preconditions; **delete all 126 prose strings**; `input_contract` gains the no-prose assertion | **every requirement observed refusing**, and observed passing; the empty-side refusal |
| 4 | `Scope` on the steps + `operations.py` registry, CLI and generated skill. **Splits SEVEN bodies, not four** (below), and promotes a public name in three more | **no operation introduces a second implementation** - drafted and proven able to fail; every refusal names a producer; skill matches registry |
| 5 | The region-scoped subtitle path: `transcript.reindex/splice`, `subtitles.plan/splice/render`, `state_splice.py`, the `@start-end` rerun form, **props-hash** skip-if-present in 4.05 | duration-preserving refusal; splice bounded to the region; **skip-if-present exercised with a TEXT-ONLY change**; **Phase D's demonstration** |
| 6 | `hooks.py` | unknown condition refused at load; a hook cannot fire a hook; budget failure is reported |
| 7 | Dashboard region selection, operation breakpoints, marker routing to a region | region -> operation offer, with refusals shown. **Blocked on open question 5** - region gates must not be added on top of a gate that `--resume` can bypass |

### Increment 4, stated honestly: seven bodies to split, not four

An earlier draft said four. Measured over all 39 bodies, the ones with **no top-level
function at all** - so nothing for `Operation.run` to point at - are seven:

| body | file | LOC inside `main()` |
|---|---|---:|
| `step_1_05_prosody_analysis` | `step.py` | 278 |
| `step_4_05_render_subtitles` | `step.py` | 212 |
| `step_2_06_music_analysis` | `step.py` | 130 |
| `step_1_07_ocr_extraction` | `step.py` | 81 |
| `step_6_02_validate_output` | **`post_bridge.py`** | 50 |
| `step_5_01_color_grade` | **`post_bridge.py`** | 39 |
| `step_2_05_mesh_spine` | **`bridge.py`** | 27 |

The three additions are bridge halves, which the same rule catches the moment it is run over
`bridge.py`/`post_bridge.py` as well as `step.py`. They are 27-50 LOC, so this is hours not
days - but an operation over a hybrid step needs a name in the bridge half too.

Three further bodies have top-level functions but **none public** -
`step_0_01_validate_sfx_library`, `step_1_03_semantic_analysis` and
`step_4_06_render_motion_graphics/post_bridge.py`. An operation may not point `run` at a
`_private` name without declaring that name part of the contract, so one function per body is
promoted. Three more small edits.

**A sequencing correction that matters more than the count.** `step_4_05_render_subtitles`
has no top-level function and its `main()` shells out to Remotion - and 4.05 is on the
captain's worked example. **Splitting it is a prerequisite of increment 5, not just
increment 4**, so increment 5 cannot start until that split lands.

Increment 3 touches all 29 manifests - mechanical but wide, and the one place the local
full-suite gate is genuinely warranted rather than habitual. If 4 or 5 grows past what a
reviewer can hold, it opens as its own PR.

### Two audit defects this design must absorb

- **`plan_subtitles` declares `speech_sequence` and `rough_cut_review` required and reads
  neither.** Found independently twice - by this audit and by `REEL-CAPABLE-SHAPE.md`, which
  calls it *"a live violation of the section 3 rule ... fixing it is most of what makes 4.01
  reel-capable."* Increment 3 must decide their true requirement, not copy the false one, or
  a region-scoped re-plan pays for both.
- **`run_pipeline.py:949` documents a `--write` flag `timeline_transcript` does not have.**
  Increment 5 touches that path and must not reproduce the wrong instruction.

### Convergence with the reel work - flagged, not assumed

`REEL-CAPABLE-SHAPE.md` proposes `reel_spine.py` and folding `reel_subtitles.py` into
4.01/4.05. That work and this design **want the same mechanism**: a step parameterised by
scope. If both land independently they will collide in 4.01. The cheapest resolution is that
the reel work adopts `Scope` from increment 4 and supplies a reel's ranges as one, rather
than either side building its own. **This is a coordination decision for the captain and
firstmate, not something this design should settle alone.**

### AGENTS.md

The size gate is at **0 spare** (53,499 of 53,499; budgets sum to 52,978). New rules live
with the code that owns them; AGENTS.md gets index rows only, paid for by condensing existing
prose. The ratchet moves down, never up.

## 7. What this design deliberately does NOT do

- It does not retire the DAG - operations reach *past* it, not around it.
- It does not change what any step creatively decides (AGENTS.md 10.5), and it introduces no
  threshold, floor or invented number (Ruling 2).
- It does not touch Resolve or Fusion.
- It does not add a GitHub Actions job.
- It does not consolidate the test suite.
- It does not build a parallel implementation of anything (Ruling 1).

## 8. Open questions for the captain

1. **Is scope-addressable steps + generated skills the right unit**, or do you want
   hand-authored skills as the primary interface despite the prose-contract cost?
2. **The reel convergence** - should the reel work adopt `Scope` from increment 4, or proceed
   independently and reconcile later?
3. **`plan_subtitles`' two false requirements** - drop them, or is one genuinely wanted and
   simply unread today?
4. **Increment order** - the demonstration needs 1, 3, 4 and 5. Is landing 2 (provenance)
   first worth it for the envelope, or should it follow the demonstration?

### Two decisions raised by the parallel audits, registered here

5. **Should an unanswered review gate halt a run that omits `--resume`?** Measured: the gate
   is consulted only under `resume_mode` (`run_pipeline.py:2438`); without it a completed step
   is skipped by `step_ledger.is_completed` and the pending status is never read. Pause at
   `scan`, re-run without `--resume`, and the run reports **SUCCESS while the gate is still
   pending**.

   This is load-bearing for increment 7, which adds region and operation gates **on the same
   seam** - so it would multiply a bypassable gate across a much larger address space.
   - *Option A* - make the gate halt regardless of `--resume`. Honest, and removes a bypass
     some workflows may rely on.
   - *Option B* - keep it, and say plainly in the pause message that a gate is bypassable by
     omitting a flag.

   AGENTS.md 10.4 points at A. **Increment 7 should not ship until this is answered**, because
   silently multiplying the hole is the one outcome neither option wants.

6. **Should `rough_cut_review.passed` actually refuse?** It is declared by four planning steps
   and enforced by none, and this design's `predicate` kind is what would make it executable.
   But enforcing it means **caption iteration is refused on a failed cut** - and reworking
   captions on a cut you have not yet approved may be exactly the loop you want. Making a dead
   declaration executable is not automatically making it correct.
   - *Option A* - enforce it as declared.
   - *Option B* - narrow it to the steps where a failed cut genuinely invalidates the work, and
     delete the declaration from the others.
   - *Option C* - delete all four; the check was never real and the cut review is advisory.

   This is a taste-adjacent workflow call, so it is yours rather than the engine's.
