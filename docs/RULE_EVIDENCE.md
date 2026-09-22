# Rule evidence

This file is the history behind `AGENTS.md`.

`AGENTS.md` states rules.
This file records what produced each one: the incident, the measurement, the date, the crash log, the diagnostic path, the captain's ruling.
Every entry is reachable from a `[why]` link on the rule it belongs to.

**You do not need anything in this file to operate the pipeline.**
Read an entry when you want to overturn a rule, when you need the numbers behind it, or when you are about to do the thing it warns about and want to know how bad it was.

Entries are grouped by the `AGENTS.md` section they support and are named by anchor.
When you add a rule to `AGENTS.md` that came from an incident, add its evidence here and link it; when you withdraw a rule, leave the evidence and say it was withdrawn.

---

## Section 3 - running the pipeline

### whisperx-paid-twice

Before the split ledger, one flat `steps_completed` covered all 28 steps with one lifetime.
`--from` only trimmed the plan while the skip-if-finished check fired anyway, so the only way to redo creative work was to move `pipeline_data.json` aside.
That move is what made project 001 pay for forty minutes of WhisperX twice.
Recorded in `docs/RUN_001_END_TO_END.md`, whose Caches row says "Do not do this again".

`--rerun edit` exists so that redoing creative work never names the enrichment ledger.

### fingerprint-not-mtime

The fingerprint is deliberately not mtime.
A `cp` without `-p`, or a backup tool, rewrites mtime on untouched footage and would have destroyed forty minutes of WhisperX for nothing.
It is deliberately not a whole-file hash either: that is gigabytes of IO on every run.
Size plus a digest of the first and last mebibyte was the measured compromise.

Clip ids are assigned by sorted path, so adding a file renumbers everything after it.
The fingerprint carries the path for that reason: a renumber must invalidate the clips it renumbered.

Deliberately NOT built alongside it: a caching framework, or a content-addressed artifact store.
The whole mechanism is one declared field, one split ledger, one re-run flag and one identity check.

### the-tick-that-reported-what-it-wanted

**Project 001, 2026-08-29.** The first render into a new Resolve project delivered a **1920x1080**
master of a **1080x1920** edit, and every structural check passed on it: duration 56.68s, framerate
30.00, one audio stream, `face_intact` 0 of 52 cut, and `frame_occupancy` 100.0% of the frame.
Occupancy is 100% because the picture does fill the frame - the frame is the wrong shape.

**Why it had never happened before.** `build_timeline` set the resolution on the TIMELINE. The audio
mix round trip then re-imports the timeline through OTIO, and the rebuilt one inherits the PROJECT's
resolution (AGENTS.md 5, "the import REBUILDS the timeline"). Every render the pipeline had ever
made went into `Lucie Content/Pipeline_Edit`, a project set to vertical BY HAND, so the inheritance
was always correct and nobody learned it was doing the deciding. A fresh project defaults to
1920x1080.

**What made it cost three renders instead of one.** Two reporting defects, which are the same defect
from opposite sides.

The build discarded all four `SetSetting` return values and then printed

```
✓ Created timeline: Pipeline_Edit_2 (1080x1920 @ 30fps)
```

from the MANIFEST's own numbers. The timeline was 1920x1080 with `useCustomSettings: 0`. The tick
reported what the code wanted, not what Resolve did, and it was the single most misleading line in
the log - the first diagnosis off it was wrong, and cost a render.

Then, when a later attempt refused for a stated reason, the reason reached nobody. Step 6.01's
`main()` did `print(json.dumps({"error": ...}))` - to STDOUT - and exited 1, while
`run_deterministic_step` reports STDERR on a non-zero exit and discards stdout. So the failure
arrived in `step_errors`, `pipeline_log.jsonl` and the run summary as:

```
Step failed (exit 1):
  stderr:   PIPELINE_SKIP_STABILIZATION set: dropping 13 neural-engine directive(s)...
✓ Imported 18 media files into subfolders
  Media pool: 18 clips (18 with paths)
✓ Detected actual FPS from source: 30.0
```

An exit code and a truncated log. The message existed the whole time and nothing read it. A step
that fails silently is the sibling of a gate that cannot fail: in both, the report is decoupled from
the event.

**Two hypotheses raised and killed on evidence rather than patched around.** That the render preset
was inheriting a stale size - killed, the timeline itself was landscape. That `SetSetting` does not
commit synchronously, so a read-back races - killed, measured 20/20 immediate reads matching across
alternating writes on Resolve 21, plus a real frame-rate change accepted after media import. Both
were plausible and both were wrong, which is the argument for fixing the reporting FIRST.

**What changed.** The shape is set on the project and confirmed by `GetSetting`, and the tick prints
what Resolve reports. Step 6.01 writes its reason and traceback to stderr. The runner's failure
branch reports BOTH streams, because a step that fails is not obliged to have chosen the one we read.
The test doubles now hold a settings store and echo it - a double that cannot echo a setting cannot
exercise code that depends on echoing one, and six tests passed against a `MagicMock` that said yes
to everything.

**Still open at the time of writing:** why the third render refused. It is no longer a guess worth
making - the next attempt will say.

### a-preflight-cache-that-predates-its-own-fix

**Measured on project 001, 2026-08-29,** on the first end-to-end run after twenty fixes landed on
2026-08-28/29.

The `temporal_index` ledger entry read `2026-08-17T17:09:28` and the 17 per-clip index files on
disk were written `2026-08-21`. Three commits after that date changed what step 1.04 EMITS:

- **#152 (2026-08-25)** stopped sampling face frames at a fixed 320x180 and read the clip's own
  display aspect instead, which changes every `face_center_x` VALUE.
- **#207 (2026-08-26)** added `face_width` beside it.
- **#248 (2026-08-28)** measured usable ranges.

Nothing noticed. The footage was untouched, so `footage_identity` re-confirmed all 17 fingerprints
and preflight was skipped, exactly as designed. The identity check answers "is this the same
footage", and the question that had gone stale was "is this the same code".

**What the stale cache made inert.** `subject_framing.subject_box` returns None on an index with no
widths - it says so in its own docstring - and None is also what it returns when the footage
genuinely cannot support an answer. So the whole of #207 (`manifest_validator` P8, `subject_safe_zoom`,
the `framing_backdrop` route) read a code-staleness as a measurement of the footage. Over 001's own
11 A-roll placements:

    subject_box answerable over the real A-roll windows:  before 0/11   after 10/11

**What the re-run cost and changed.** 573.6s for 17 clips. `face_width` present in 0/17 files
before, 17/17 after. Face detections across the project rose 1887 -> 2604 (+38%), and the split
says where #152 mattered:

    rotated portrait (display 1080x1920), 7 clips:  1 detection  ->  105
    landscape (display 1920x1080), 10 clips:     1886 detections -> 2499

All seven rotated clips were effectively face-blind: squashing a 1080x1920 display frame into
320x180 distorts a face past what the Haar cascade recognises. Those seven are precisely the clips
that COVER a 9:16 delivery frame, so they are the ones a crop actually applies to.

**Why the catalog did not need the same treatment,** checked the same way: the two commits touching
step 1.02 since its 2026-08-19 cache are #124 and #127, and neither adds a field to `clip_catalog` -
the step's current output construction writes exactly the keys the cache already carries.

**The related misconception, checked before the run and disproved.** The brief for this run assumed
`scan_project`'s 2026-08-17 cache would make 001 render with the OLD framing, because PR #311 added
`pipeline.framing_intent: 0.0` and `pipeline.subtitle_typography.size: 85` to its project.yaml after
that date. It does not. `project_config` carries only `brand_registry.PROJECT_CONFIG_KEYS`
(`target_duration_seconds`, `style_preset`, `subtitle_style`), the `pipeline:` block is not in it,
and both declarations are read off project.yaml at the point of use on every run:

    framing: project_framing_intent(001) = 0.0        (engine default would be 1.0)
    project_subtitle_typography(001) = {'size': 85}   ->  resolved fontSize: 85

The cache IS stale in one field - it carries `style_preset` and `subtitle_style` that 001 never
declared, from before `project_declared_config` stopped inventing them - but every DAG edge out of
`scan` carries only `raw_footage_files`, which recomputes byte-identical, and the whitelist
broadcast reads `state["project_config"]`, which `load_pipeline_state` refreshes. The stale value
reaches nothing.

The general shape: a preflight cache is invalidated by a change to the FOOTAGE and by nothing else,
so a change to the CODE that emits it has to be noticed by a person. The cheap check is the ledger
date against `git log` on the step's own directory.

### a-selection-that-died-forty-minutes-in

**#250, the captain on 2026-08-28.** "we should have that ability to be able to quickly deselect
and select what specific steps we want to fire off for a run - like for example if i just need the
roughcut with subtitles and nothing else then many intermediate steps can just be skipped instead
of having unecessary processing wasted."

What existed reached one step (`--step`) or a suffix (`--from`), and neither consulted the DAG
about the selection itself. A selection that dropped a producer got as far as the consumer and
then raised inside `gather_step_inputs`:

    RuntimeError: Step 'compile_manifest': data_mapping expects key 'enhancement_spec' from
    upstream step 'plan_vfx', but it is missing from that step's outputs.

On 001 that consumer is 22 steps and a measured 27 minutes downstream of the exclusion. The
refusal now reads the same two declarations the crash reads - the edge's `data_mapping` and the
consumer manifest's `required` flags - so the two cannot disagree;
`tests/test_run_scope.py::test_an_edge_is_hard_exactly_when_the_runner_would_raise` asserts that
edge by edge over all 104.

**Why excluding a producer refuses rather than dropping its consumers.** #250 left it open. There
is no "let downstream cope" available: a required input has no absent-value code path, because
section 10.1 forbids `.get()`-ing a default for a key a contract promises. So the only two honest
outcomes are refuse, or cascade the exclusion downstream - and cascading silently is how "I just
wanted the rough cut" turns into a run that quietly did not compile a manifest. The refusal names
both ways out and the captain picks. Wanting a smaller run is expressed by naming a GOAL, which is
what a target is.

**Why a target names goals rather than steps.** The DAG carries 104 edges. A hand-written step
list for "the rough cut with subtitles" would have been wrong the first time a step was inserted,
and a target nobody trusts is a target nobody uses. `rough_cut_subtitles` names `render` and the
list is walked every run. Measured on the DAG as it stands, that leaves out `validate_sfx_library`,
`render_motion_graphics`, `creative_cohesion` and `validate` - the two that reach
`compile_manifest` on OPTIONAL inputs, the SFX-library check nothing routes, and the render QA
that judges a master nobody is shipping yet.

### a-run-shape-was-a-code-change

**The captain, 2026-08-30.** "what if i want to setup specific breakpoints and such for a given
run and/or enable/disable specific steps because they are not needed (ex: a podcast may not need
anything but colorgrading and transitions after the rough cut, and it's creative direction is also
not needed because it just needs to cut silences, and if this is how i want to have it, then
that's how it should be able to run and i should be able to configure it as such)".

Two things were missing and they are not the same thing.

**A selection had to be written in Python.** `run_scope` has done selection properly since #250 -
goals, hard and soft edges off the manifests, the refusal before the run - but the only NAMED
destination lived in `run_scope.TARGETS`, a dict in a source file with exactly one entry in it. So
"a podcast" was a code change and a pull request, and the captain could reach it only by typing
`--skip` twelve times, every time. A run profile is that same statement written as data.

The profile has no power of its own, which is the property worth keeping: it composes a
`run_scope.Selection` and hands it to the same resolver a flag does.
`tests/test_run_profile.py::test_a_profile_gets_the_same_refusal_the_flags_get` asserts the two
refusals are the SAME STRING, so a declared configuration cannot express a selection the flags
could not.

**The pause was one boolean.** `--review` gated after EVERY step. "Stop after the rough cut and
nowhere else" could not be said at all, so the only way to get one pause was to take twenty-six,
and the gate loop had therefore never been driven on any project - 001 had no `pipeline_output/gates/`
directory at all before 2026-08-30.

**How much of the captain's podcast the DAG will actually give.** Measured on the DAG as it
stands, `--profile podcast` runs 19 of 25 steps on 001 and leaves out `validate_sfx_library`,
`plan_vfx`, `plan_sfx`, `render_motion_graphics`, `creative_cohesion` and `validate` - all six by
falling out of the goals, none of them named in the profile. The four the captain also named -
`creative_direction`, `music_selection`, `music_analysis`, `select_broll` - cannot be left out:
eleven steps declare `creative_direction` a REQUIRED input and six declare `music_selection`, so
excluding one is refused by name. Reproduce it with:

    manage_project.py run <project> --profile podcast --skip creative_direction --dry-run

That refusal is correct and is the point of the mechanism. Turning those edges soft is a change to
eleven manifests and a captain's call about what a step may run without; `library/tools/input_contract.py`
is where that question is surveyed. **A profile may not quietly make it.**

**What driving the loop on 001 found.** Three defects, all in the gate machinery rather than in
the selector:

* `save_gate_snapshot` wrote `status.json` only when there was not one already, so a step re-run
  after being approved arrived at its breakpoint carrying the previous run's `approved`, and the
  next `--resume` sailed through a pause the captain never saw - applying an old `revised` payload
  to a freshly computed output while it was at it. Arming a gate now makes it `pending` and throws
  the old answer away.
* The pause printed no way to answer itself. The gate was answerable from the browser dashboard and
  from nowhere else, which made a breakpoint unusable from a terminal and, in phase 2, from inside
  Resolve. `python3 -m library.tools.review_gate answer` is the same three actions and the same
  files.
* The resume command the pause prints carried `--rerun` through, which would have cleared the
  ledger entry the pause had just written, re-run the step, re-armed its breakpoint and stopped in
  the same place - a loop the captain could not get out of by following the printed instruction.
  `breakpoints._NOT_CARRIED` is the enumeration of what a resume drops, with the reason.

**The `revised` action, measured end to end on 001** (steps `scan` -> `catalog`, the cheapest real
pair in the DAG, chosen so nothing expensive or creative was destroyed and both are idempotent):

```
gate answered:  revised, {"raw_footage_files": <first 3 of 17>, "total_files": 3}
resume:         "scan: revised output applied"  ->  catalog runs
scan.total_files        17  ->  3
catalog.total_clips     17  ->  3
catalog.clip_catalog    IMG_1806, IMG_1807, IMG_1808
```

and `--rerun scan --rerun catalog` afterwards put both back byte-identical to the pre-drive backup.

**What is still awkward, reported rather than designed around.** The gate snapshot's
`upstream_context` is `{name: type-name-as-a-string}` - `{"project_config": "dict"}` - so the half
of the snapshot meant to say what the step was working FROM carries no values at all. A reviewer
gets the step's output and a list of input names. It is not changed here because the brief for
this work was to give the gate a selector rather than to rewrite it, and what the right shape is
depends on what reads it - the browser dashboard today, the Resolve panel next.

### a-prerequisite-is-a-statement-about-state

**#260, the captain on 2026-08-28**, reframing an issue that had been raised as a product call:

> it should be possible to have as much or as little in the number of steps in the pipeline (given
> that all necessary prerequisties have been fulfilled -- like for example you should not be able
> to add transitions or effects when there exists no roughcut either already on the timeline
> manually or automated by the LLM during the process) so i think if it doesn't already exist we
> need to refactor the system to have the pipeline be customizable with prereqs and what ever else
> is needed so we can continue to have strong contract enforcment but still have the pipeline
> configuration ability so that a run that does not need all steps to fire does not fire all the
> steps

Both halves of that sentence are load-bearing. Configurability bought by weakening the contract
would have been worse than leaving it alone.

**The lie that started it.** `compile_manifest`'s manifest declared `transition_spec`,
`enhancement_spec`, `sfx_spec` and `color_grade_spec` required, while its own code called three of
them "optional enhancement specs" and read them off state with `.get(..., {})`. The declaration is
what `gather_step_inputs` raises on and what `run_scope` derives its refusal from, so the stricter
of the two won: the `rough_cut_subtitles` target had to run four planners it did not need.

Measured by running the step. `tests/test_compile_manifest_without_the_decoration.py` builds a
project under `tmp_path`, drops one recorded key at a time, and compiles:

    transition_spec absent  -> transitions: [],  the edit cuts
    enhancement_spec absent -> vfx: [],          no comp is written for an effect nobody planned
    sfx_spec absent         -> A3 empty
    color_grade_spec absent -> color_grade: {},  no CDL, no house-look values, ungraded picture

All four are section 10.5's "nothing is drawn": the absence of decoration, not a choice of it. They
are now optional. What that saves, in the seconds 001's own ledger recorded for its 2026-08-26 run:

    plan_vfx           320.2s
    plan_sfx            70.7s
    plan_transitions    50.7s
    color_grade          4.9s
    ------------------------
                       446.5s

`--target rough_cut_subtitles` went from 22 of 26 steps to 18, and 3183.7s of measured work to
2737.0s - 14.0% shorter.

**Why the same measurement did not relax everything else.** `compile_manifest` also compiles
without `audio_mix_spec`, `music_selection`, `subtitle_plan`, `subtitle_overlay`,
`semantic_analysis`, `a_roll_assignments` and (for an all-speech spine)
`b_roll_assignments`. Those are not decoration: an absent mix drops every dB the spine's declared
`music_behavior` asked for, an absent `semantic_analysis` stabilises nothing, an absent
`subtitle_overlay` ships a caption list with nothing on screen. Each is recorded in
`input_contract.REQUIRED_THOUGH_THE_STEP_RUNS_WITHOUT_IT` with what would go missing, and the test
checks the record both ways - an entry for an input the step actually refuses without is stale and
fails. `audio_spine` was recorded first and the staleness check deleted it.

**The survey of the other twenty-five steps.** The captain's known unknown was whether
`compile_manifest` was the only one. `library/tools/input_contract.py` reads all 128 declared
inputs of the DAG's 27 steps and asks who refuses when each is absent. The answer, which was not
the expected one: almost every step reads its required inputs with `.get(k, default)`, and that is
harmless, because for an edge-routed required input `gather_step_inputs` has already raised and the
default is dead code. 69 are refused by the runner, 22 by the step's own code with a file and a
line, and 37 are declared optional. Nothing was declared required that nothing refuses on.

Two genuine findings, both the same shape: `mesh_spine` and `review_rough_cut` declared
`temporal_index` required while neither the step's code nor its `context_fields` reads it - the
projection deletes it before the prompt. Both are now optional and recorded in
`UNCONSUMED_DECLARATIONS`. They are still ROUTED, because unrouting would leave their `handoff.md`
documenting a read that no longer happens and those files were reserved.

**The third way to satisfy a prerequisite, and why it is not a flag.** "already on the timeline
manually" had no expression at all: `enhancement_spec` could only be satisfied by running
`plan_vfx` now or having run it before. Both are statements about lineage. The fix is
`library/tools/external_inputs.py`: the captain SUPPLIES the value, in
`<project>/external/<state_key>.json`, and it is checked at the moment the resolver asks. The same
verified value is what `gather_step_inputs` hands the step, so there is no state of the world where
the resolver believed something the run could not then use.

An unchecked "trust me, it exists" flag was the obvious alternative and would have dissolved
exactly the enforcement the captain asked to keep. What is checkable is enumerated, and a key that
is not in the table is refused by name - `a_roll_assignments` (files on disk, ranges, and the
catalog's measured durations), `audio_spine` (`spine_contract`), `assembly_manifest`
(`manifest_validator` both halves, plus every placed clip), `render_output` (ffprobe finds a video
stream).

The captain's own example is the one thing that could NOT be honestly checked. A DaVinci Resolve
timeline lives in a project database, reading it means copying that database and opening it as
SQLite (section 5), and nothing maps its clips back onto a typed pipeline key at resolve time. So
it is recorded in `WITHDRAWN` rather than believed, and the answer given there is to supply the
artifact that describes the cut, which is checkable.

Measured end to end: a project with a hand-assembled `assembly_manifest` and a hand-rendered
`render_output` resolves `--only validate` to **one step of twenty-six**, and the same selection
with the render's path pointed at a file that is not there is refused by name before the run
starts.

**What replaced the 104-edge agreement test.** It still passes unchanged. Beside it,
`test_a_prerequisite_exists_exactly_when_the_runner_would_raise` asserts the same condition per
KEY rather than per edge - which is the granularity `gather_step_inputs` really raises at - and
`test_a_selection_the_scope_accepts_never_raises_in_gather_step_inputs` drives the real runner over
a full run and then removes each required key in turn to confirm it raises. The resolver is
therefore strictly stricter than before: a producer that finished and recorded a DIFFERENT key used
to satisfy an excluded dependency and now does not, because the runner would have raised on the
key.

### per-clip-index-in-a-worktree

Step 1.04 wrote the per-clip index to `--output-dir`'s default of `./pipeline_output`, which is the runner's current working directory.
Project 001's state therefore recorded its 17-clip index inside a disposable git worktree, which was then thrown away.
It now resolves from `project_folder` and reuses any per-clip file already there instead of re-transcribing it.

---

## Section 4 - dashboard

### review-surface-is-this-dashboard

Captain's ruling of 2026-08-17 on `.lavish/video-gui-findings.html`: EXTEND THIS DASHBOARD, never author a fresh per-run review page.

Whether the review surface should be per RUN or per PROJECT is NOT decided; the captain has not ruled on it.
Keep both possible - per-run is a filter over the existing store, not a migration.

### notes-are-anchored-not-page-comments

A note whose `anchor.selector` is empty is rejected rather than degraded to a page comment, because a page comment is the thing this channel exists not to be.
The server never computes an anchor; only the browser can measure one, because only the browser has the rendered element.

### runs-are-driven-from-the-page

Captain's ruling, 2026-08-17: runs are driven from the page.

Start does not pass `--review` because forcing review gates turns one press into 26 stops; gates are an opt-in tick box.
Step resolves the step id server-side to the first topologically-unrun step so it reuses the runner rather than adding single-step machinery.

### the-handbrake-is-a-file

Killing the runner mid-step would leave `pipeline_data.json` describing a step that half happened.
The runner reads the hold at the top of each step instead, so the step in flight finishes and writes its state first, and what lands on disk is always a real step boundary.

`pipeline_run.json` is the runner's own account of itself and nothing else may write it.
The dashboard reports run state from that file, never from the fact that a launch request returned 200.

Launches use `sys.executable` rather than a bare `python3`: the dashboard runs from the pipeline's `.venv`, and a PATH interpreter has none of the ML dependencies.

---

## Section 5 - DaVinci Resolve

### fcpxml-and-drp-are-closed

Captain's ruling: the Python timeline builder plus Fusion IS the architecture, not a workaround.

**FCPXML** was deprecated because it does not recognise DaVinci's effects.
Resolve's importer silently degrades unrecognised transitions to Cross Dissolve and scrambles the audio track layout on a round trip.
Both were measured; the corrected table is at `research/drp_reverse_engineering.md:98-106`.

**DRP project-file surgery** wrote to a temp file the renderer never loaded, so it could not fire at all.

`library/tools/execution/apply_native_transitions.py` and its test remain in the tree unused.
Do not wire either route back in.

### the-menu-contained-cuts-that-cannot-be-built

Step 4.02 is asked to choose from every cut in the spine.  On 001's run of record five of the fifteen could not carry a drawn transition, nothing said so, and the run died in `compile_manifest`:

```
ValueError: Transition trans_009 at 38.801s does not sit at the end of any V1 clip
```

**Why five.** Every B-roll placement goes on V2, and the spine's `transition_slot` blocks are what B-roll fills.  So V1 has a hole where a transition slot plays, and no V1 clip ends at the far side of it.  Cuts 2, 5, 7, 9 and 14 are all `transition_slot-to-speech`.

**The prompt pointed at exactly those five.** `handoff.md` line 82 says *"Prefer placing major creative transitions on cuts with a nearby beat"*, and on 001's own spine, measured off the frozen snapshot `001-degradation-20260828`:

| | count of 15 | which |
|---|---|---|
| can carry a drawn transition | 10 | 1, 3, 4, 6, 8, 10, 11, 12, 13, 15 |
| a beat within 0.10 s | 8 | 2, 3, 4, 5, 7, 9, 14, 15 |
| beat within 0.10 s **and** buildable | 3 | 3 (0.035 s), 4 (0.073 s), 15 (0.042 s) |
| beat at **0.000 s** | 5 | 2, 5, 7, 9, 14 - **every one unbuildable** |

Every cut that lands exactly on a beat is a cut that cannot carry the effect, because `mesh_spine` beat-snaps the gaps and the gaps are the transition slots.  Following the stated criterion is following it into the wall.  The answering agent's own postscript: *"This was not bad luck. … Following the prompt's beat-alignment guidance steers directly at the cuts that cannot carry the effect."*

**Cost to the finished video.** One wasted round trip, and both defocus transitions moved to the other side of their seam - cut 9 to cut 8 (34.394 s), cut 14 to cut 13 (46.147 s).  The agent's verdict on the second: *"a small loss - the ideal version blurs directly into his face in the car."*  And neither surviving transition sits near a beat.

**The A/B, measured.** Six independent answers to the replayed 4.02 prompt off the same frozen snapshot, three at HEAD and three with `can_carry_drawn_transition` / `carry_basis` in `cuts_toon`.  Each answerer was given the reconstructed prompt and nothing else, and forbidden to read this repository - a model with a shell can discover the rule the way the run of record did, and that is the route the column exists to make unnecessary.  Drawn cuts were then put through `compile_manifest`'s own `_v1_index_ending_at` over the V1 track its own `block_reaches_v1` builds:

| | drawn on | refused by the compiler | verdict |
|---|---|---|---|
| HEAD, answer 1 | 8, 9, 14 | 9, 14 | **FAILS** |
| HEAD, answer 2 | 7, 8 | 7 | **FAILS** |
| HEAD, answer 3 | 5, 9, 14 | 5, 9, 14 | **FAILS** |
| with the column, answer 1 | 8 | - | compiles |
| with the column, answer 2 | 8, 13 | - | compiles |
| with the column, answer 3 | 8, 13 | - | compiles |
| with both columns, answer 4 | 8, 13 | - | compiles |

Three of three fail at HEAD; three of three compile with the column, and all three chose cut 8, which no answer at HEAD chose alone.  Of the eight drawn transitions across the three HEAD answers, six sat on a cut that cannot carry one.  Not one of the five drawn transitions across the three informed answers did.

**Beat alignment and buildability are genuinely in conflict on this spine, and telling the truth does not resolve it.**  All three informed answers put both drawn transitions on cuts with no beat near them, and said so unprompted - *"the beat rule and the drawability rule pull in opposite directions … if beat alignment matters more than placement, the fix is upstream in the spine, not here."*  Three beat-near buildable cuts do exist (3, 4, 15) and all three answers declined them on editorial grounds: 3 and 10 are labelled `jump_cut`, which draws nothing, 4 is the deadpan punchline and 15 is the closing line.  So the column buys a plan that builds, and it makes the conflict legible; it does not make the conflict go away.  Moving the beat-snapped gaps is a `mesh_spine` decision, not a `plan_transitions` one.

**The informed model then found the next omission, which is why `carry_basis` names both halves.** One answerer, reading only the buildability column, worked out that `after_clip + 1` indexes the V1 list and a cutaway is not in it - so on a `speech-to-transition_slot` cut the tail draws on the outgoing speech clip and the head on the speech clip that RESUMES after the cutaway.  *"So a drawn transition there brackets the B-roll rather than drawing through the cut … Only cuts 3, 10, 11, 12 and 15 are true through-the-cut drawable ones."*  That is right, it is derivable from the same placement logic, and it was the second thing the step had not been told.  `carry_basis` now says which of the two shapes each buildable cut is; the buildability verdict is unchanged, so the A/B above stands, and it was measured before that sentence was added.  Answer 4 in the table is a spot check against the finished table, and it reads the new half back: *"On both of my drawn cuts the effect brackets a cutaway rather than drawing through the cut - tail at 34.62s, head at 38.80s, four seconds apart. I think that is the right gesture for a breath and for a blackout, but it is a different gesture."*

**Cost of the columns.** Measured against the pre-merge baseline `ef3b8f8`, before #304 added `view:picture` to this step - so a replay on today's tree reads 68,199 B and the difference is that view, not these columns. Step 4.02's replayed context goes 55,458 B -> 57,948 B, +2,490 B (+4.5%): `cuts_toon` 6,284 B -> 7,601 B (+1,317 B) for the two columns, and `cuts_legend` 1,173 B for their definition.  The legend is data because `handoff.md` is frozen and cannot name a column - the route `music_measurement.MEASUREMENT_LEGEND` takes for step 2.04.

**Bench visibility, checked before the result was trusted.** `replay_bench verify 001-degradation-20260828` names its differences by section.  Before the change it reported `cuts_toon -34 B` for `plan_transitions`; after it reports `cuts_toon` and `cuts_legend` as positive deltas of their own, and `project untouched by this run: True`.  It sees the columns, so a null result would have been a null result and not a blind tool.

**A hazard this does not model.** `_v1_index_ending_at` matches within 0.25 s, and 4.02's post-bridge may relocate a cut back to a beat-coincident word end up to `MAX_WORD_END_BACKTRACK` (0.35 s).  A cut backtracked further than 0.25 s would be refused even where the column says yes.  On 001 all 11 speech blocks end exactly at their last word (gap 0.000 s measured 2026-08-28), so the two bounds have never disagreed there.  It is a post-bridge defect, not a property of the menu.

### hasattr-is-always-true

`hasattr` returns True on Resolve's scripting proxies for invented method names as readily as for real ones.
Every guard written against it passes, whatever the object actually supports.

`CreateMagicMask` returns False for every mode, which is why it is withdrawn.

Super Scale is a **MediaPoolItem** property taking an **int**, with the companion keys `SuperScale Sharpness` and `SuperScale Noise Reduction` (no space after Super).
Setting it on a TimelineItem, or passing the string `"2"`, silently returns False.

### smart-reframe-reported-success-for-months

`smart_reframe` had a reader, the reader ran, and it printed "✓ Applied Smart Reframe" on every run for months.
It guarded on `hasattr` (always True on a Resolve proxy), was handed a Timeline that exposes no such method, and discarded the return value.

The neural-directive block in `resolve_build_timeline` is the pattern to copy: judge the call by what it returns, and say so when Resolve declines.

### stabilization-oom-87gb

Eight `Stabilize()` calls on project 001 took Resolve to **87.4 GB** resident and macOS jetsam killed it.
The kill is recorded as `largestProcess: "Resolve"` in `/Library/Logs/DiagnosticReports/JetsamEvent-*.ips`.

`neural_engine_directives` is applied AFTER every clip, comp, overlay and SFX is placed.
The project database kept only the flush from before that pass, so the music, every SFX and all eight Fusion comps were gone.

Stabilization changes picture steadiness and nothing else: never structure, timing, framing, grade, captions or sound.
That is what makes it safe to pop off the in-memory manifest for a timeline meant to be scrubbed rather than shipped.

### keyframes-outside-the-played-window

A clip with 513 source frames placed as 410 frames on the timeline has a Fusion frame range of 0-512.
`clip_dur` sets the comp's frame RANGE (GlobalIn/GlobalOut) and must come from the SOURCE clip frame count, read as `int(mpi.GetClipProperty('Frames'))`, not from `clip.GetDuration()`.
That half stands: the source count is always at least the played length, so a Background sized by it exists for every frame that plays.

Using `0..clip_dur` for keyframes when `source_in` is not zero puts all motion outside the frames that play.
The repair for that was to place them at `source_in..source_out` instead - so a segment from frames 25-97 of a 5657-frame clip got its ramp between 25 and 97.
**That repair was wrong, and the section below is what measured it.**
The comp is rendered over the frames the clip PLAYS, numbered from zero, so 25-97 is still outside a 73-frame comp.

### the-transition-ramp-that-never-ran

The captain marked a clip on their own timeline: *"visibly actually blurry, not like because its punched in, but because there was a distinct blur effect on it"*, and noted a second clip later doing the same.

001's run of record, 2026-08-26, planned two `defocus` transitions, `duration_frames: 15`, at cut 8 (timeline 34.394 s) and cut 13 (timeline 46.147 s).
The plan was correct. So was every previous run's.

**The comps that run banked are on disk**, under `assets/fusion_presets/`, and they are the artefact Resolve imported:

| comp | half | `TransDefocus1Size` keyframes | frames the clip plays |
|---|---|---|---|
| `speech_7_seg0_b72a99d75c79` | cut 8 tail | 0 = 0.0, 3480 = 0.0 ... 3495 = 3.0 | 480 |
| `speech_9_seg0_4df94525be04` | cut 8 head | 654 = 3.0 ... 669 = 0.0, 725 = 0.0 | 71 |
| `speech_12_seg0_8c88369d70f1` | cut 13 tail | 0 = 0.0, 1047 = 0.0 ... 1062 = 3.0 | 43 |
| `speech_14_seg0_f299d472deb2` | cut 13 head | 944 = 3.0 ... 959 = 0.0, 1204 = 0.0 | 260 |

Under the source-frame reading each ramp sits exactly on the played frames and all four draw.
Under the clip-frame reading - comp frame 0 is the first PLAYED frame - a Fusion spline extrapolates flat, so the two heads hold their FIRST key, full strength, for every frame they play, and the two tails interpolate 0.0 to 0.0 across everything that plays and draw nothing.

**The shipped master says which happened.**
Measured on `exports/Pipeline_Edit.mp4` as mean absolute horizontal pixel difference at 540x960, one value per frame, no re-render and no Resolve:

    cut 8  tail, last 20 played frames   6.97 6.83 6.80 6.95 6.98 ... 6.90 6.77 6.79 | cut
    cut 8  head, first 20 played frames  1.81 1.81 1.78 1.75 1.65 ... 1.47 1.49 1.63
                 rest of that clip       1.865 mean - the same level
    cut 13 head, first 20 played frames  2.00 2.01 1.99 1.99 2.01 ... 1.98 1.98 1.99
                 rest of that clip       1.728 mean

No ramp on any of the four, and the two head clips are flat for their whole length.
**71 + 260 = 331 frames, 18.6% of a 1783-frame video, carrying a full-strength defocus nobody planned, and 0 of the 30 planned ramp frames drawn.**

That the softness is ADDED rather than the footage: cut 8's head and the three untransitioned clips that follow it are the same source clip (IMG_1817), seconds apart, conformed identically.
With the same crop applied to the raw file the two windows measure 0.988 and 1.040 - within 5%.
In the render they measure 1.780 and 3.999: the transitioned clip keeps **44.5%** of its neighbours' high-frequency energy.

Four predictions, four matches, and the source-frame reading contradicted by all four.

**Issue #202 is the same defect**, not a second one.
Its `zoom_blur` head builds the identical shape - `BezierSpline.sampled(reverse=True, hold_after=...)`, first key at the source frame number carrying the full value - and that full value is `Transform.Size = 0.6`.
Held for the whole clip, that is exactly what #202 reports: *"it never returns to neutral"* and *"its transform leaves the frame uncovered"*, a shrunken picture with black around it for the block's entire 2.38 s.
A defocus has no transform, so the same hold reads as a blur rather than as an uncovered frame; the mechanism is one.

The fix is one statement of the time base, `library/tools/fusion/played_window.py`, used by the zoom, the fade and both transition halves.
The gate is `tests/test_transition_ramp_draws.py`, which counts the frames a transition is DRAWN on by evaluating the comp's own splines - because a test that asserts the plan is right passes on every run this defect ever shipped.

### background-sized-to-the-delivery-frame

Every Background node `build_effect_comp` draws - vignette, fade, both transition halves - is a solid image merged over `MediaIn`.
Sized to the delivery format instead of the source clip's own resolution, it paints a hard-edged rectangle in the middle of the picture, and no warning fires, because a wrong-sized Background is a valid comp.

`CompEngine.from_params` had the fix and the comment; the renderer calls `build_effect_comp`, which did not.

Read the size off the MediaPoolItem's `Resolution` and do NOT swap it for rotation - Fusion gets the stored frame.

### house-look-missed-the-broll

`compile_manifest` merges the house look onto both V1 and V2, and the pass that drew it read `tracks['V1']` alone.
Project 001's three cutaways therefore played at a different contrast with no grain and no vignette beside the A-roll.

Transitions stay on V1 because `after_clip` indexes the V1 clip LIST; replaying it elsewhere draws a transition at an unrelated cut.
The drop detection in `build_verification` now asks whether a label was PLACED, not whether it is on V1.

`tests/test_series_look_reaches_broll.py` drives the real pass against a fake Resolve.

### pan-tilt-and-volume

`SetProperty` returns False for `PanX` and `PanY` and reads back None, silently: there is no such property.
`ZoomX` and `ZoomY` are real.

`Volume` on an audio TimelineItem also returns False on Resolve 21, and the reason is now known rather than guessed: an audio `TimelineItem` has **no property dictionary at all**.

    audio TimelineItem GetProperty()  ->  {}
    video TimelineItem GetProperty()  ->  ['AnchorPointX', ..., 'Pan', ..., 'ZoomY']   (25 keys)

So `SetProperty("Volume", x)` does not fail because the name is wrong; it fails because the property system on that object is empty.
Every spelling returns False and reads back None: `Volume`, `volume`, `Gain`, `Level`, `AudioLevel`, `ClipVolume`, `Pan`.

The rest of the surface was enumerated with `dir()` across every proxy, which is truthful where `hasattr` is not.
The complete set of audio-adjacent methods anywhere in the API is `GetFairlightPresets`, `ApplyFairlightPresetToCurrentTimeline`, `InsertAudioToCurrentTrackAtPlayhead`, `AutoSyncAudio`, `GetAudioMapping`, `PerformAudioClassification`, `ClearAudioClassification`, `TranscribeAudio`, the `Timeline` track calls (`AddTrack`, `SetTrackEnable`, `SetTrackLock`, ...), `GetSourceAudioChannelMapping`, `GetTrackTypeAndIndex` and `GetVoiceIsolationState`.
There is no fader, no pan, no solo, no automation mode, no bus, no FairlightFX.
`Fusion.ActionManager.GetActions()` resolves to 199 unique action ids and exactly three match `audio|volume|gain|level|fader|fairlight|mix|bus|pan|mute|solo|automat` - `Fusion_Zone_Expand`, `Player_Gain`, `Viewer_Show_GainGamma`, all image controls - and the set does not grow on the Fairlight page.

**This is a complete enumeration, not a failed search.**
Levels go through OTIO instead; see [the-mix-goes-through-otio](#the-mix-goes-through-otio).

`TimelineItem.GetProperty()` with no argument returns the whole dict.
Read the truth off it before trusting any property name.

### audio-pool-items-report-24fps

Resolve audio pool items report 24fps regardless of the timeline's frame rate, and `AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase.
Computing audio in/out against the timeline fps stretched the music to 125% and padded the export with trailing black.

### renders-are-silent-by-default

`SetRenderSettings` must set `ExportAudio` and `AudioCodec` explicitly or the render has no audio at all.
`resolve_render.py` now also probes the output for an audio stream before reporting success, because a silent render is otherwise indistinguishable from a good one.

### the-mix-goes-through-otio

Until this landed, the renderer turned every `audio_mix.music_automation` entry into a cyan timeline marker reading "Target Level: -96dB (silent)" and set no level at all.
On project 001 the two blocks the spine planned `silent` played music at full level in the finished video, and the marker made it look handled.

`Timeline.Export(path, resolve.EXPORT_OTIO)` writes plain JSON in which each audio clip carries a `Fairlight Clip Volume and Fades` effect, and its `volume` parameter is **dB directly** - no fader law, no taper.
`Key Frames` maps a frame number to a dB value and Resolve interpolates linearly between them, so a pair of keys at one value is a plateau and the gap between two plateaus is a ramp.

**What survives an OTIO round trip.**
Established by exporting 001's built timeline, importing it back and comparing the two:

| | survives | how it was established |
|---|---|---|
| clip placement, durations, track structure and names | yes, frame-exact | `GetItemListInTrack` on both |
| transform: `Pan`/`Tilt`/`ZoomX`/`ZoomY` (`_apply_conform`) | yes, exact | `GetProperty` on both; OTIO carries it as a normalised `transformationPan` |
| timeline markers, with colour and name | yes, all 11 | `GetMarkers()` on both |
| native transitions | yes | authored a `Transition.1`, imported, re-exported unchanged |
| Fairlight clip volume and keyframes | yes | the point; measured below |
| **Fusion comps** | **no** | `GetFusionCompNameList()` empty on all 8 V1 clips |
| **CDL grades** | **no** | `GetNodeGraph().GetToolsInNode(1)` returns `None` after import and `['Primary Balance', 'Saturation, Hue & Lum Mix']` before - and the getter was proved truthful by calling `SetCDL` on one imported clip and watching it change |

That is why the round trip runs at PLACEMENT time: the two things it destroys are the two the renderer had not applied yet.

**Where a keyframe's frame number is measured from.**
Two renders settled it.
A music clip was placed at timeline frame 150 with keys at 300 and 450, first with source in-point 0 and then with source in-point 200.
Both times the ducked floor landed at 15.0-20.0s in the rendered file, which is timeline frames 450-600 - the keys plus the clip's own start.
Timeline-absolute would have put it at 10.0-15.0s; source-relative at 8.3-13.3s.
So the numbers are **clip-relative**, and the earlier scouting note that called them "timeline frame number" was true only because that clip began at frame 0.

**Three ways the route answers None and says nothing.**
`ImportTimelineFromFile` returns None, with no diagnostic and no partial import, when any referenced media file is missing, when the path is relative, or when the timeline name is already taken.
The first is checked before the call, the second by passing an absolute path, the third by renaming the placement timeline to `<name>__premix` for the length of the import.

A fourth trap is in the export rather than the import: Resolve writes `"Parameters": []` on the clip-volume effect whenever every value is at its default, so a patcher that looks for an existing `"Parameter ID": "volume"` finds nothing, changes nothing, and the render comes back unchanged.
The parameter has to be inserted.

**Measured on 001.**
The bed was rendered twice from the same build - once as mixed, once from the pre-mix export at unity - with A1 and A3 muted so the music could be measured on its own, and differenced per second:

    time     mixed(mean)  unity(mean)  delivered   planned
     0-2s        -91.0       -45.5      floor      silent  (-96)
       3s        -57.8       -45.8      -12.0      fade_in (-12)
    6-17s        -64.6       -46.6      -18.0      background (-18)
      19s        -37.6       -31.6       -6.0      prominent (-6)
      34s        -27.2       -21.2       -6.0      prominent (-6)
    36-43s       -34.6       -17.6      -18.0      background (-18)
    44-54s        -91.0      -20.2      floor      silent  (-96)

Every plateau is exact to 0.1 dB.
The silences read as the file's own -91 dB floor rather than -96 because that is as low as a 16-bit AAC master goes; against the unmixed bed they are 44 to 71 dB down.
The ramps land on the planned frames: out of the second `prominent` block, keys at frame 1061 (-6 dB) and 1091 (-18 dB) measured -6.0 dB through frame 1050, -8.6 at 1065, -14.9 at 1080, and -18.0 from 1095 on.

**Routes not taken.**
DRT blob surgery reaches the same data through a supported import and preserves Fusion comps, but only a static gain is demonstrated on it: automation is stored zstd-compressed under a different flag byte, in 64-byte records that were characterised and never decoded.
It also depends on an undocumented binary layout that Blackmagic can change in any release.
FCP7 XML works, keyframes included, but its round trip is not level-transparent - an untouched export and reimport comes back a constant **3.1 dB quieter**, and every level then sits on top of that tax.
AAF carries no gain entries. EDL is video only. Control surfaces and UI automation were never needed.

### media-pool-name-collisions

Overlay segments from different sources often share generic filenames like `seg_000.mov`.
Importing them into the same media pool makes basename lookups silently pick the wrong clip.

### reading-a-killed-build

Resolve is the captain's application and a crashed build is not licence to start it.

`~/Library/Preferences/Blackmagic Design/DaVinci Resolve/dblist.conf` names the active database.
Each project under it is plain SQLite at `<db>/Resolve Projects/Users/guest/Projects/<folder>/<name>/Project.db`.
**Copy it before opening; never open it in place.**

The join is `Sm2Timeline` -> `Sm2Sequence.Sm2Timeline_id` -> `Sm2SequenceContainer.Sm2Sequence_id` -> `Sm2TiTrack.Sm2SequenceContainer_id` -> `Sm2TiItem_Sm2TiTrack`, in which **`DbOwner` is the TRACK and `DbAssociate` is the ITEM** - the reverse of what the column names suggest.
`Sm2TiCompositionTable` holds the Fusion comps.

It shows only what was FLUSHED, which is exactly what makes it useful: the gap between it and the manifest is where the build died.

It cannot answer everything.
Conform geometry (`Pan`/`Tilt`/`Zoom`) sits in a binary `FieldsBlob`, so proving the picture band still needs Resolve running.

### the-two-pages-that-showed-one-frame

The captain, 2026-09-10, holding a side-by-side of one frame off his rebuilt Reel 09:

> *"i think i know why previously when i told you to sample the stills from davinci in order to color grade, you made all the parameters way more than they needed to be, and it was because the way the video looks in fusion is different from the way it shows up in the timeline render. im not exactly sure why and i would like this to be fixed because the way it shows up in fusion is how i want it but it does not look like that in the timeline on the edit page."*

The Edit page frame was flatter, lifted and greyer; the Fusion page frame had deeper blacks, more contrast and warmer skin. He wanted the Fusion one.

The first question was not how to close the gap. It was **which of the two is real**, because the fix is opposite in the two cases, and a viewer cannot answer it - a screenshot of two pages only proves the pages differ, which was already known. It was settled on exported pixels, on the captain's own project, at timeline frame 300 throughout.

**They hold the same pixels.** A temporary Fusion `Saver` wired to whatever feeds `MediaOut1` - exactly the image the Fusion page viewer draws - rendered one frame; the same timeline frame went through Deliver as a 16-bit TIFF. Aligned by the clip's own Edit-page geometry, every luma bin maps to itself:

| Fusion output | delivered | difference |
|---|---|---|
| 4.72 | 4.75 | +0.03 |
| 24.06 | 24.15 | +0.09 |
| 55.58 | 55.68 | +0.11 |
| 108.67 | 108.03 | -0.64 |
| 175.52 | 175.48 | -0.04 |
| 233.48 | 233.33 | -0.15 |

Percentiles agree to 0.1/255, mean saturation 66.13 against 66.37. There is no tone curve between them. **The export already carried the look he was pointing at**, and no grade change could have been the answer.

**The difference is the two viewers.** Both grabbed off one window through one identical capture path, same frame, same clip: the Edit page draws those pixels with its midtones lifted by +6.2 to +9.9/255 and saturation 46.41 against the file's 48.41; the Fusion page tracks the file to within a few units (64.95 against 65.69). Resolve's own preference file carries `EnableMacDisplayColorProfile = 1` - "Use Mac Display Color Profiles for viewers" - the window composites in Display P3 and the delivered TIFF is tagged Rec. 709. That reaches the Edit, Color and Deliver viewers and not the Fusion one. A plain ColorSync Rec.709 to Display P3 of the same file does not reproduce Resolve's curve (it darkens by 14-19/255 instead), so the transform is Resolve's own and exists nowhere outside it.

Two structural facts were verified rather than read off documentation, because they bound how far the finding generalises:

* **Fusion sits upstream of Color.** On a throwaway project created and deleted by the probe, a comp feeding `MediaOut1` a solid RED `Background` rendered GREY through Deliver with a `SetCDL` saturation 0 on Color node 1 (R=G=B=16.77), and RED again with that node bypassed (R 80.68, G 0.00, B 0.00). So the Fusion page is BEFORE the Color page and the Edit page is AFTER it - wherever the Color page does anything, the two pages cannot agree even before the display transform.
* **On Reel 09 the Color page does nothing.** Bypassing all 75 nodes on every picture item moved the delivered frame by mean 0.000/255, max 7. The bypass mechanism was proved on the throwaway project first, so the null result is a fact about the grade and not about the tool. It is the same near-identity `library/tools/color_page_grade.py` already recorded for the captain's `.drx` on its own reference frame, now measured on the reel.

**Which surface the repo's own still-export captures**, the load-bearing unknown: `library/tools/marker_capture.py` uses `Timeline.GrabStill`, which returns the graded, conformed timeline frame and measured 1.61/255 against a Deliver render. That is the delivered surface - the right one. The `vep-match-the-chosen-grade` lane's fourteen stills are all exactly 6,232,792 bytes, that route's own fingerprint, so it sampled the right surface too; and the `v04_teal_split.jpg` the captain chose from was built in numpy off an ffmpeg-extracted source frame, never off a Resolve surface at all, with Resolve's own decode of that frame differing from ffmpeg's by a uniform +1.8/255. **No landed grade value is traceable to the wrong surface.** The over-crank the captain remembers has a cause already found and already being fixed elsewhere: `contrast` shipped as 1.12 where the declaration said 0.12.

The rule the measurement buys is narrower than the captain's diagnosis and stronger: **a grade is measured on an EXPORT, never on a viewer.** Both viewers are wrong in different directions and neither is reproducible off that machine. The roster of what each surface shows, with every number above, is `library/tools/resolve_surfaces.py`; `assert_measurable` refuses a viewer by name rather than warning, because a warning is what let this cost two lanes.

Two `SetSetting`-shaped lies were found on the way and are recorded in `library/steps/step_6_01_render/probe_resolve_capabilities.py`: `Timeline.SetTrackEnable` returns True from every page and only takes effect with the Edit page open - a render taken in between silently used the wrong track set - and `GalleryStillAlbum.ExportStills` and `TimelineItem.ExportLUT` both return False and write nothing at all on this build, in every format, from every page, to every destination tried, which leaves the capture button unable to produce a PNG and a one-frame Deliver render as the working substitute.

---

## Section 6 - the spine contract

### get-clip-id-disabled-beat-alignment

A guard reading `block.get("clip_id", "")` on blocks that only had `content.clip_id` silently disabled beat-aligned cutting for four audits.
That is why a missing key must raise rather than fall back.

### overlapping-source-ranges-play-twice

Two body passages cut from one clip that claim overlapping source ranges make the overlap play twice across the cut.

The drift threshold is a secondary aid only: the shipped case drifted 0.76s and sailed under it.

This is fixed in code and covered by tests, but the export at the reference project was NOT regenerated.
The mp4 on disk still repeats "to post" at ~10.4s, so the fix is not verified in a render.

### the-passage-opened-on-the-wrong-i

**6.901 seconds of project 001's finished 59.437s export - 11.6% of the video - is audio nobody chose, with no caption over it.**
Graded off the shipped mp4 on 2026-08-29 and reproduced exactly off frozen state at HEAD `89c796b`.

The model's answer was right.
`speech_sequence` asked for *"i get caught up in all the numbers and metrics ... where i don't do the thing in the first place"* on clip_011, hinting 92.07-118.23.
The passage begins at 107.369s.
What reached the timeline was `speech_7_seg0`, source 100.519-116.512 - so the viewer hears, from 18.622s to 25.572s, *"...want to do the goals i have, and really the goal of this is to take the pressure off posting, i think"*, and only then the intended line.

The mechanism, in two stages, both of them the aligner doing what it was told:

    full-clip alignment      88.366 -> 116.512    leading gap 19.043s
    _hint_drift              3.704s > MAX_HINT_DRIFT (2.0)  -> re-anchor in the hint window
    windowed alignment      100.519 -> 116.512    leading gap  6.901s   <- what shipped
    faithfulness ratio       1.0    >= MIN_TEXT_OVERLAP (0.5)           <- passes

`_align_words_to_text` anchored on the occurrence of the passage's FIRST WORD nearest the hint.
The first word is *"i"*; clip_011 says *"i"* 26 times, 6 of them inside the hint window.
The two-pointer then advanced over non-matching candidates with **no bound on the time it may skip**.

Every gate that could have caught it is the right gate for a different failure:

| gate | why it did not fire |
|---|---|
| `MAX_HINT_DRIFT` (2.0s) | it *did* fire, and re-anchored onto a second wrong *"i"* |
| `MIN_TEXT_OVERLAP` (0.5) | set-based, so it returns **1.0** on a span that is 36.8% voiced |
| `manifest_validator` overlapping-source | this passage overlaps no other |
| `manifest_validator` round-number check | 100.519 is not a round number |
| `render_qa` `subtitle_gaps` | **it fired.** `qa_report.json` has `[[13, 14, 6.25]]`, `passed: false`, `severity: "info"` - and nothing reads an `info` |

**Why the fix is a search and not a threshold.**
Across 001's eleven shipped blocks the leading gaps are 0.018-0.120s except two: body_3 at **6.901s** and body_0 at **1.121s**.
body_0's is a real dramatic beat - *"and ... i have an announcement to make"* - so a gap ceiling that catches the defect destroys the creative decision, and a voiced-fraction floor fitted to the 61-86% the other blocks measure would flag body_0 at 48.5% too.
Whether that band holds on other footage is unknown; it is a property of this recording until something else is measured, which is why nothing in the module compares against it.

The anchor search needs no band.
Every occurrence is tried, and the alignments are ranked by words aligned, then SPAN, then leading gap, then hint proximity.
body_3's correct anchor aligns the same 37 words in 9.143s instead of 15.993s and wins; body_0 has no alternative that aligns its 7 words in less than 2.682s, so its pause is kept and reported as `held`.
Ranking on the leading gap alone is not enough - it bounds the head and lets the same defect sit at the third word - which is what `test_the_ranking_puts_completeness_before_the_gap` pins.

Two details cost a cycle each.
The anchor must be tried **from the occurrence itself as well as backed up** by `ANCHOR_BACKUP_SECONDS`: backing up alone collapsed body_3's correct anchor onto the *"I"* of *"I think"* 0.58s earlier, giving 106.789s and a 0.660s gap instead of 107.369s and 0.040s.
And the search fixes the earlier `to post` mis-anchor (`overlapping-source-ranges-play-twice`) at the alignment stage, so on 001 the overlap guard no longer has to re-anchor body_6 - it reaches 31.898s on its own.

Measured on all eleven of 001's blocks off the frozen snapshot: **body_3 moves, nothing else does.**

---

## Section 8 - project management

### nothing-owned-the-project-folder

`library/tools/paths.py` has owned the repo side since the beginning and is well made.
Measured on 2026-08-25: **zero of the twenty-one steps imported it**, and **fifteen of them composed project output paths independently**.

Nothing owned the layout of a project folder, so every step invented its own answer and no two had to agree.
What that produced, all of it real:

- The scaffold in `project_registry.PROJECT_DIRS` created `pipeline_output/subtitles` and `pipeline_output/motion_graphics`.
  Step 4.05 writes `subtitle_segments` and step 4.06 writes `motion_graphics_segments`.
  Two directories nothing ever opened, and two the scaffold never made.
- Step 1.03 wrote its vision profiles to `raw/analysis/` and step 1.07 wrote OCR to `raw/analysis/ocr/` - inside the captain's own footage directory, which was therefore not read-only in any enforceable sense.
- Steps 4.05 and 4.06 fell back to `<repo>/pipeline_output/` when handed no `project_folder`.
  In a disposable worktree that means the render is gone with the worktree.
  It is the same shape as the `--output-dir ./pipeline_output` default that banked project 001's seventeen-clip WhisperX index inside a treehouse checkout.
- Step 2.04 downloaded chosen music into `music/`, mixing a pipeline product into the captain's library.
- `ProjectConfig` carried its own `raw_dir` / `pipeline_output_dir` / `exports_dir` properties, a second definition of the same answer.

`vision_pipeline_v3.load_temporal_index` still reads `raw/analysis/temporal_index/`, a directory step 1.04 has never written to, so that read has never fired.
Deliberately left as it is: repointing it would change what step 1.03 computes, which was out of scope for the layout work.

### nine-hand-made-backups

Project 001's root carried nine hand-made copies of `pipeline_data.json`, 25 MB in total:
`.bak`, `.bak2`, `.bak3`, `.bak4`, `.bak2_migrated`, `.bak_phase1_landscape`, `.bak_phase2_baseline`, `.bak_pre_trans_rerun`, `.bak-before-index-move`.
They span 2026-08-10 to 2026-08-21.

They were not junk - something real was being protected, and the run history says what: before the split ledger existed, moving `pipeline_data.json` aside was the only way to redo creative work.
What they lacked was a policy. No retention, no naming, no way to tell which one mattered, and they sat beside the file they were backing up.

Ten was chosen because nine spanned twelve days at the captain's real cadence, and because ten copies at ~4 MB is ~40 MB against a 6.8 GB project - bounded, and small enough that the bound never has to be argued about again.

One per run rather than one per save, because `save_pipeline_state` runs after every step: a per-save policy would spend the whole retention window inside a single run and lose exactly the thing these files were keeping.

### the-folder-could-not-be-read-back

The layout landed and the folder was tidy, and the captain said the point was not tidiness:

> "i meant for you to clean up the actual pipeline output folder so we know what everything is and what step it relates to and all that ... i think for me to be able to work with you on auditing all the steps manually, we need to have everything able to be properly traced and be able to read that traceback of steps and whatnot. and then the lavish docs can be rewritten just referencing them instead of trying to copy things into them"

Ruling of 2026-08-25. The requirement is provenance and legibility.

What was already recorded, measured on project 001 before any of this was built:

- The two ledgers carried `completed_at` and `elapsed_s` for every step, plus `failed_steps` and `step_errors`. Real facts about a real run.
- `dag.json` carried the consumption graph in its 99 edges, so "what did this step read" was answerable at the step level.
- Several artifacts already named their own source: `temporal_index/clip_001.json` carries `source_file`, a vision profile carries `file_path`, a prosody profile carries `audio_file`.
- `source_fingerprints` linked each `clip_XXX` to a path and a digest.

What was not recorded at all: which step wrote which FILE, and any notion of a run. `pipeline_log.jsonl` is append-only across every run with no run id, and its 50 `step_end` events all name the step `unknown_step`.

So the change is narrow: a run identity, an observed artifact ledger, and two generated documents. The `declared` fallback exists because project 001's runs already happened and re-running them was explicitly out of scope - reconstruction is legitimate, and pretending it was observed is not.

The three files `organize` could not attribute - `001.mov`, `fully loaded demo v0.mov`, `001.PNG` - were re-checked with the provenance machinery in hand. Nothing names them: not `pipeline_data.json`, not any per-step export, not any QA report, and not the archived pre-rerun state. They remain unknown, and `unsorted/` is `Kind.UNSORTED` precisely so that `organize` having MOVED them is not reported as `organize` having PRODUCED them.

### by-kind-was-the-wrong-axis

The first layout grouped by KIND - `pipeline_output/prosody/`, `pipeline_output/temporal_index/`, `pipeline_output/subtitle_segments/`.
That was a correct answer to the brief it was given, and the brief was wrong.
The captain, on being shown it:

> "i wanted the pipeline output to be organized by step so i could step through it as well, why did you decide to organize it like this"

Ruling of 2026-08-26. The folder structure IS the traceback. Sidecar metadata and an index do not give a reader that; the directory listing does.

Two things hid the step from the reader, and the content was already almost step-shaped: the directory names said what the files WERE rather than which step made them, and they sorted alphabetically rather than in run order.

The multi-writer audit that decided the shape, measured before any of it was built:

- `Area.TEMPORAL_INDEX` has exactly ONE writer, step 1.04. Steps 1.07 and 2.02 reach it only through `read_dir`, `os.listdir` and reads. So by-step forced no compromise there and no shared area was needed.
- Steps 4.05 and 4.06 appeared to write the output root. Both assignments were dead - the variable was assigned and never used.
- `exports/` is genuinely written by two steps, 6.01 the render and 6.02 the QA report. It stays at project level, because it is the deliverable rather than any step's workspace, and both writers are declared.
- Step 6.02 wrote `qa_report.json` via `os.path.dirname(video_path)` - a sixteenth inline path composition, missed by the first pass because it composes from a path rather than from `project_folder`.

**Moved out of the rule (AGENTS.md 8) on 2026-08-29.**
Directory names use the STEP number rather than DAG position because numbering by DAG position would
renumber every later directory whenever a step is inserted. The cost is that sorting diverges from run
order in exactly two places - the DAG runs 2.06 before 2.05 and 5.04 before 5.03 - and
`README-LAYOUT.md` renders true run order.

Nesting `logs/`, `gates/`, `review/` and the rest under a step would be a lie about who wrote them.

The scaffold drifted from the steps once, promising `pipeline_output/subtitles` while step 4.05 wrote
`subtitle_segments`.

### a-declaration-that-went-stale

`classification.per_clip_artifacts` used to spell the path out: `pipeline_output/temporal_index/{clip_id}.json`.

When the layout moved the vision profiles out of `raw/analysis/`, the declarations in steps 1.03 and 1.07 were left behind pointing at the old location.
Nothing failed. `--rerun semantic_analysis:clip_007` deleted nothing, so the step's own "already on disk?" check found the profile still there and re-ran nothing - silently, and reporting success.

A declaration that can go stale is the exact failure the layout owner exists to remove, so the prefix is now the owner's to state (`{area:vision_analysis}/`) and only the filename is the step's.

**Moved out of the rule (AGENTS.md 8) on 2026-08-29.**
Spelling the path out rather than naming an AREA is what left steps 1.03 and 1.07 pointing at
`raw/analysis/` after the layout moved it, so `--rerun semantic_analysis:clip_007` deleted nothing.

---

### tests-bound-to-the-captains-project

`tests/test_pipeline.py` walked the real projects root at IMPORT time, so it happened on every pytest COLLECTION rather than only when its own test ran:

    from library.tools.paths import PROJECTS_ROOT
    for entry in PROJECTS_ROOT.iterdir():
        if entry.is_dir() and (entry / "project.yaml").exists():
            PROJECT_DIR = str(entry)
            break
    OUTPUT_DIR = os.path.join(PROJECT_DIR, "pipeline_output")

`PIPELINE_TEST_PROJECT` overrode it, but the FALLBACK was the real thing and nothing sets that variable in CI or locally.
The module then wrote `step_2_05_v2.json`, `step_3_01_v2.json`, `step_4_01_v2.json`, `assembly_manifest_v2.json` and `timeline_v2.xml` into whatever project it had bound (line 71), and `shutil.move`d a `.bak` back over `step_2_05.json` and `step_4_01.json` in a `finally` (line 284).

**This is not known to have destroyed anything.**
It was found on 2026-08-26 while investigating three exports that vanished on 2026-08-25, and the captain has since accounted for those separately.
It is a live hazard on its own account, and the reason it is priority zero rather than tidiness is that there is no undo: this machine has no Time Machine destination configured.

Two accidents are why nothing had been written recently, and neither is a control:

- On this machine the walk aborts on `.DS_Store`, which `iterdir` returns first and which `entry.iterdir()` raises `NotADirectoryError` on - caught by the `except (ImportError, FileNotFoundError, OSError)`. Delete the `.DS_Store` and it binds to the first real project instead. Reproduced: with the `.DS_Store` skipped it resolves `video_projects/4th-wall`.
- In CI the root does not exist at all, so the walk raises `FileNotFoundError` and the test skips. CI has therefore never run this; only a local machine could.

**The test could not have passed anyway, and it asserted nothing.**
Judged against a temporary fixture as the task asked:

- Zero `assert` statements in 363 lines. It prints and it writes.
- Line 295 does `from xmeml_generator import write_xmeml_file`. That module was deleted when the FCPXML route was closed (AGENTS.md section 5, [fcpxml-and-drp-are-closed](#fcpxml-and-drp-are-closed)), so the run raises `ImportError` at phase 6 even with every input present.
- It reads flat `pipeline_output/step_2_01.json` names. Since PR #167 exports go to `pipeline_output/steps/<step>/output.json`, so its own `skipif` guard can only fire on a project predating that layout and never migrated.

So it was a pre-pytest orchestrator script with a `@pytest.mark.skipif` bolted on, whose only remaining effect was writing into whatever project it found. It was deleted rather than rebuilt on a fixture: preserving a green that was never real would have meant fabricating a fully populated project to feed a route the repo has withdrawn.

**The audit of the rest of `tests/`.** Every route into a real project, not just this one:

| Where | Shape | Verdict |
|---|---|---|
| `test_pipeline.py` | walks `PROJECTS_ROOT`, binds at import, writes and `shutil.move`s | **Deleted.** The only instance. |
| `test_dashboard_api.py:10` | `@patch("library.tools.paths.PROJECTS_ROOT")` | **Left.** A string target, patched to a `MagicMock`. Never reads the value. |
| `library/dashboard/server.py` `_get_project_dir` | raises `HTTPException` when no project is set | **Left.** No fallback to scan; answers the "do the dashboard tests reach a real project another way" question with no. |
| `test_dashboard_smoke.py`, `test_e2e_dashboard.py`, `test_e2e_pipeline_run.py`, `test_dashboard_run_control.py`, `test_run_traceback.py` | build a project under `tmp_path`/`TemporaryDirectory` and copy `tests/fixtures/` state into it | **Left.** Already correct. |
| `test_runner_no_fixture_shortcuts.py` | quotes `/Users/prajwal/.../001/...` in its module docstring | **Left.** Documentation of the hazard it guards. The source scan is docstring-aware for this reason. |
| `test_night_card_delivery.py` `CARD_PROJECT` | `tests/fixtures/night_card_project` | **Left.** In-repo fixture, read-only. |
| `test_color_grade_delivery.py`, `test_integration.py` | `project_folder="proj"`, a relative path | **Left.** Nothing on that path is opened, verified by a clean `git status` after a full run. Not a route to a real project either way. |
| `test_runner_library_paths.py`, `test_music_selection_contract.py`, `test_dotenv_encoding.py`, `test_validate_sfx_library.py` | set `PIPELINE_SFX_LIBRARY`/`PIPELINE_MUSIC_LIBRARY` | **Left.** Set to `tmp_path`, or named in prose. These are the shared libraries, not a project. |
| every `subprocess.run` in `tests/` | inherits `os.environ` | **Left, and now covered.** The conftest sandbox is an env var, so a child process inherits the sandbox rather than the real root. |

**What was measured.** A full `python -m pytest tests/ -q` with `PIPELINE_TEST_PROJECT` unset, bracketed by a sha256-per-file snapshot of all 210 entries under the real projects root: identical before and after.

**Why `paths.py` and not `project_layout.py`.**
The task asked whether the by-step layout owner could be the enforcement point. It cannot: `ProjectLayout` is constructed from a folder passed in, and has no way to judge whether that folder is the captain's or a fixture's - which is the whole reason it is parameterised. `PROJECTS_ROOT` is the only thing in the repo that names the real root, so that is where the door is.


## Section 9 - environment

### text-true-decodes-with-the-locale-codec

The pipeline writes UTF-8 status glyphs.
A check-mark in a child process's stderr failed a render under an ASCII locale, because `text=True` decodes with the locale codec rather than UTF-8.

### the-dashboard-could-not-be-opened

2026-08-26. The captain tried to open the review dashboard on the day the footage search landed, and could not.
Three defects, each sufficient on its own. Verbatim:

    ❯ cd ~/Documents/content_stuff/video_editing_pilot
      source .venv/bin/activate
      python3 manage_project.py dashboard 001
    source: no such file or directory: .venv/bin/activate
    ERROR: Missing ML dependencies: whisperx
    The pipeline must be run from its virtual environment.
    Run this to activate it:
        source .venv/bin/activate
    ❯ source .venv/bin/activate
    source: no such file or directory: .venv/bin/activate

**The check ran at import time, for every subcommand.**
`_preflight_check()` was called at module scope, before argparse had seen the word `dashboard`, and it required `mlx_vlm`, `whisperx`, `easyocr` and `torch`.
The dashboard needs none of them: measured on this machine with `whisperx` absent, `from library.dashboard.server import start_server` imports and the server serves.
Every ML import in the repository is inside a pipeline step or under `library/tools/analysis/`, and `run` is the only command that reaches one - it launches `run_pipeline.py` with `sys.executable`, which is what makes checking the parent interpreter a real check of the child rather than a guess about it.
`torch` inside `footage_query` is imported lazily inside a function, so the dashboard's footage search does not pull it in at start.

**The message named a path that does not exist.**
It printed a bare `source .venv/bin/activate`.
The venv is per checkout and gitignored; the captain's checkout has none, so obeying the instruction produced a second and more confusing error than the first.
The workflow is real - every worktree that runs the pipeline has one, made by hand - so the message now names the activate script by absolute path when the checkout really has one, and gives the `python3 -m venv` / `pip install -r requirements.txt` pair when it does not.

**WITHDRAWN 2026-09-16, the half of the rule that said "nothing else".**
The rule this entry supported read, verbatim:

> **`run` needs the dedicated `.venv`; nothing else in the CLI does.**

The second clause is false and was withdrawn.  `build-reels` needs it too: `reel_look` aims the reels punch-in with a Haar cascade, which `opencv-python` ships only at `>=4.8,<5`, and the system interpreter on the captain's machine carries cv2 5.0.0 where `CascadeClassifier` is gone.  Measured 2026-09-16, mid-build, after five reels had already been derived:

    library.tools.reel_look.ReelLookRefused: Reel 01 …: the punch-in cannot be
    aimed on LC4930.MXF (131.42-151.40s) - no face detector in this interpreter
    (cv2 5.0.0, CascadeClassifier absent)

The FIRST clause stands and is the part that matters: the check is never at import time, and `ML_DEPENDENT_COMMANDS` is the list.  What this incident adds is that the list is INCOMPLETE - `build-reels` is not on it - so the refusal arrives from deep inside the build rather than from the preflight, after the derivation has been paid for.  The refusal is honest and leaves nothing behind, which is why this is a documentation fix rather than a defect: the cost is the wasted derivation, not a bad build.

**The slug could not have resolved either, and the reason was masked.**
`PROJECTS_ROOT` is `~/Documents/content_stuff/video_projects`; project 001 lives at `~/Documents/content_stuff/post a day keeps the apple away/001`.
`get_project` has accepted a path since #79 and has said so in its error since then, but the import-time check killed the process before argparse ran, so that message was never printed.
Fixing the first defect is what made the third one visible.

**`PROJECTS_ROOT` is not exclusive, and this is not a layout bug.**
`resolve_project_path` deliberately loads a project living anywhere on disk, and §8 documents it.
The captain's main project sitting outside the root is a configuration choice, not a fault; what was missing was discoverability, so the lookup failure now names the root it searched on its own line, lists what it found, and shows the path form of the command that was just typed.

Measured after the fix, with `whisperx` absent from the interpreter:

    ❯ python3 manage_project.py dashboard 001
      Error: No project with slug '001'.
        Searched: /Users/prajwal/Documents/content_stuff/video_projects
        Found there:
          4th-wall
          geo-podcast
          podcast-roughcut
          test-proof
        A project kept outside that root is addressed by its path instead of its slug, for example:
          python3 manage_project.py dashboard /path/to/001

    ❯ python3 manage_project.py dashboard "/Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001" --port 8461

      Review Dashboard
      Project: /Users/prajwal/Documents/content_stuff/post a day keeps the apple away/001
      URL:     http://127.0.0.1:8461
      Press Ctrl+C to stop

    INFO:     Started server process [79136]
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:8461 (Press CTRL+C to quit)

`/api/project` answered `{"slug":"001", ..., "raw_footage_count":17, "steps_completed":25, "total_steps":26}` and `/api/footage/search/status` answered 200 - the footage search this unblocked.

`tests/test_cli_ml_preflight.py` holds all three, and blocks the ML packages in a CHILD interpreter rather than reloading `library.dashboard` in process - the first attempt did reload it, and handed the rest of the session a second copy of the module the other dashboard tests key their global state off, turning 27 unrelated tests red.

### the-build-that-declined-to-look

Two holes in `.github/workflows/ci.yml`, both verified on 2026-09-02 against the workflow file
and against run 33667912850. They are one defect: the build reported SUCCESS while declining
to look.

**The style check could not fail.** Line 60 read

```
ruff check library/ tests/ --output-format=github || true
```

and the trailing clause swallowed the exit code. Run 33667912850 emitted **2,896 error
annotations** and concluded `success`. They are not all cosmetic. `ruff check library/ tests/
--statistics` at commit `2abbdff`, top of the list:

```
650  UP006     non-pep585-annotation
372  UP045     non-pep604-annotation-optional
276  I001      unsorted-imports
243  F401      unused-import
212  BLE001    blind-except
108  PLW1510   subprocess-run-without-check
 24  B023      function-uses-loop-variable
```

`PLW1510` is a `subprocess.run` whose exit code nobody checks, and **51 of the 108 are inside the
test suite itself** - 51 tests that run a command, ignore whether it failed, and carry on.
All 24 `B023` are in one file, `library/processes/edit_video/run_pipeline.py`: a closure built in
a loop that reads the loop variable after the loop has moved on.

**The build machine had no ffmpeg, and never had.** `ffmpeg` appears nowhere in the workflow, and
its git history shows it was never added or removed - an original gap, not a regression. 27
library files shell out to it. On run 33667912850 that is 118 reported skips, among them
`test_music_measurement` (11), `test_subtitle_qa_sampling` (10), `test_night_card_delivery` (8),
`test_silence_under_picture` (6) and `test_timed_text_delivery` (6). Those tests skip HONESTLY -
`shutil.which("ffmpeg") is None` - which is exactly why nothing objected: every audio and video
measurement path had only ever been verified on the captain's laptop.

**Why they were fixed together.** ffmpeg alone surfaces new failures into a build whose lint half
still cannot fail; lint alone leaves the measurement paths unverified.

**Why the swallow clause was not simply deleted.** 2,896 findings in a red build blocks every PR
and gets reverted within the hour. The path is staged instead: `ruff-ci-gate.toml` selects the
classes where a hit means the code is WRONG rather than untidy, and it runs with no `|| true`.
Of those classes, all but three find nothing in the tree today and are enforced with no exception
at all. The three that do are recorded by file with their counts - 133 findings across 67 files -
and everything else keeps reporting without failing. `tests/test_ci_can_fail.py` reads the
workflow and fails if either hole reopens.

---

## Section 10 - cross-cutting rules

### key-name-mismatches

Steps disagree about what a field is called, the reader `.get()`s a default, and the pipeline reports success over empty data.
This is the dominant bug class in this repository.

Known disagreements, kept as the worked examples:

- B-roll assignments carry `video_in`/`video_out` plus `timeline_start`/`timeline_end`.
- SFX carry `timeline_in`/`timeline_out`.
- Semantic documents are keyed by FILE STEM while the catalog uses `clip_XXX`.

`tests/test_dashboard_captured_state.py` is the standing guard, and it runs against `tests/fixtures/captured_run/`, a 77KB capture from a real broken run.

### the-brief-is-paid-seven-times

The `creative_brief` channel was repaired in `61d17b9` and no project pointed at a document, so nobody had measured what one costs.
Issue #214 settled which document 001 gets - the channel-level `PLAN/series portfolio '26 planning/overall_branding_creative_direction.md`, ruled 2026-08-28 - and the measurement came with it.

The brief is **47,903 bytes**, and it arrives at every declaring step, so the cost is per step:

| step | context, no brief | context, with brief | delta |
| --- | ---: | ---: | ---: |
| `creative_direction` | 31,321 | 79,869 | +48,548 (2.55x) |
| `speech_sequence` | 63,232 | 111,780 | +48,548 (1.77x) |
| `music_selection` | 8,991 | 57,539 | +48,548 (6.40x) |
| `select_broll` | 72,617 | 121,165 | +48,548 (1.67x) |
| `plan_transitions` | 51,188 | 99,736 | +48,548 (1.95x) |
| `plan_vfx` | 44,175 | 92,723 | +48,548 (2.10x) |
| `plan_sfx` | 41,134 | 89,682 | +48,548 (2.18x) |

Measured with `library/tools/replay_bench` at `2abeea9`, on two snapshots of 001 whose only difference is that one line of `project.yaml`; the other nineteen DAG steps came out byte-identical.
The +645 bytes over the document's own size is the TOON key and the two-space indent of the `|` block.
It is a `|` block and not an escaped line: the 480 lines recover byte for byte out of the context.

Across the seven the brief adds **339,836 bytes** to a combined 312,658, so those seven contexts go to 652,494 and the brief is 52.1% of everything they read.
The context work that landed before it took all ten steps down 11.4%; this gives that back many times over, and it is a deliberate trade rather than drift.

**It moved the answer.** The same prompt answered twice, blind, one session per arm, at `gemini-3.1-pro-high` through the `agy` backend the project really runs on:

| | no brief | with brief |
| --- | --- | --- |
| `target_energy` | "Medium-high and dynamic (bridging the raw, conversational footage with the brand's 'high' energy constraint)." | `"building"` |
| `energy_reading.read_energy` | `high` | `moderate` |
| `target_mood` | "Authentic and vulnerable, yet highly motivational and forward-moving." | "Raw and vulnerable, transitioning into resolute and liberating" |
| register | a motivational manifesto pushed against the footage | the brief's "anti-guru" voice, held to the footage |

That is not a wording difference: `read_energy` decides which transitions `transition_selector` draws, so the two directions produce different pictures.
Worth the captain knowing: the with-brief rationale cites *Through the 4th Wall* by name.
The channel document names all eight series - the model reached for the nearest one, which is the series #214 established 001 is NOT.
A brief specific to 001 is what closes that, and it is the captain's writing.

### the-brief-was-copied-seven-times

The entry above measured what a copied brief costs. This is what replacing the copy with a reference did, measured the same way, on the same project.

**The captain's ruling, 2026-08-28:** *"why are we giving it the brief several times? i feel like we should just have the brief for the LLM to be able to reference if it needs it or something no? ... we can just tell it if you need to reference something again you can look at this file and it can grep and search or whatever right?"*

**They can.** The degradation investigation's run of record has the answering agent reading repository source, running the aligner and measuring audio files with ffmpeg. A model that can run ffmpeg can run `sed`.

#### What the seven contexts cost, before and after

`library/tools/replay_bench compare 001-withbrief-20260828 <step> --rev-a origin/main --rev-b WORKTREE`, snapshot `001-withbrief-20260828`, `origin/main` at `b10833d`:

| step | A: brief copied | B: brief referenced | delta | brief share A | brief share B |
| --- | ---: | ---: | ---: | ---: | ---: |
| `music_selection` | 57,539 | 14,260 | -43,279 (-75.2%) | 84.4% | 36.9% |
| `creative_direction` | 78,846 | 35,567 | -43,279 (-54.9%) | 61.6% | 14.8% |
| `plan_sfx` | 88,717 | 45,438 | -43,279 (-48.8%) | 54.7% | 11.6% |
| `plan_vfx` | 92,723 | 49,444 | -43,279 (-46.7%) | 52.4% | 10.7% |
| `plan_transitions` | 98,737 | 55,458 | -43,279 (-43.8%) | 49.2% | 9.5% |
| `speech_sequence` | 110,815 | 67,536 | -43,279 (-39.1%) | 43.8% | 7.8% |
| `select_broll` | 130,692 | 87,413 | -43,279 (-33.1%) | 37.1% | 6.0% |
| **total** | **658,069** | **355,116** | **-302,953 (-46.0%)** | 51.6% | 10.4% |

The brief itself goes from 48,547 B in the context to 5,268 B - 47,903 bytes of document becoming a 5,086-byte map, plus the TOON key and block indent. `prompt identical: True` on all seven: no handoff was touched.

**The bench's visibility was established before any of this was trusted**, because it was blind to briefs once and returned a false null (#214). `verify 001-withbrief-20260828 --rev origin/main` reports `0 exact, 0 exact after a named cause, 10 unaccounted, of 10` and names each difference BY SECTION - `creative_brief +48,547 B` on seven steps, `music_analysis -8,615 B`, `transcript -10,062 B`, `picture +10,508 B`. A tool that cannot see the brief cannot itemise it. It also asserts `project untouched by this run: True`.

#### The path is followable, and it is followed

Parsing the `FILE:` line and a heading's line range out of the reconstructed `music_selection` context and running exactly what it says:

```
PATH FROM CONTEXT : .../overall_branding_creative_direction.md
HEADING FROM MAP  : Music & Sound Philosophy  [3,140 B, lines 331-356]
COMMAND           : sed -n 331,356p "..."
BYTES READ BACK   : 3141
lines of substance in the section : 10
of those, ABSENT from the context : 10
```

That is reachability. **Whether a model bothers** is the separate question, and it was answered by asking six sessions to answer the real `music_selection` prompt - three on each arm, identical answering envelope:

- Every arm-B session read the file. Each returned a verbatim sentence that occurs **once in the document and zero times in the prompt it was given**, and cited line ranges off the map (`331-356`, `62-87`, `88-107`, `448-460`) rather than headings alone.
- The map did not stop them looking. The worry that a summary too good stops the model ever following the path did not materialise here; what the map buys is the model choosing WHICH sections, which is the thing 47,903 bytes of copy takes away from it.

#### It moved the answer, in the direction #258 is about

All three arm-A sessions - the brief copied whole - chose Sickick's *Infected* and justified it as the series' **locked** track. That lock is line 472, and it belongs to **Through the 4th Wall**, which #214 established 001 is not. `Through the 4th Wall` appears **20 times** in the copied prompt and **0 times** in the referenced one.

None of the arm-B sessions adopted it. Two went outside the library for a warm, unhurried, CC-licensed instrumental and named the direction's forbidden registers as the reason; the third did the same.

**The reference does not hide that line** - `Open Creative Decisions [2,590 B, lines 461-480]` is in the map and one `sed` away. It stops it being unavoidable. That is a bearing on #258 and not a fix for it.

#### The 41.9% that is unactionable is still unactionable

Moving it behind a path makes it cheap, not usable. Classifying every leaf section by whether a step in this pipeline makes a decision it constrains:

| section | bytes | why no step can act on it |
| --- | ---: | --- |
| Open Creative Decisions | 2,590 | undecided by the document's own heading |
| Naming Philosophy | 2,286 | no step names anything |
| Cross-Series Narrative Weaving | 1,824 | links between episodes; the pipeline makes one video |
| Part 2: Intro Card / Animation | 1,760 | bookends come from `content.bookends` plus a project asset (§13), never from brief prose |
| Platform-Specific Considerations | 1,737 | aspect and length are `delivery_format` (§10.1); the rest is upload behaviour |
| Growth Philosophy | 1,677 | channel growth |
| Posting Strategy & Feed Cadence | 1,655 | when to post |
| The Netflix Model | 1,571 | channel architecture |
| Per-Series Typography Direction (to be finalized) | 1,089 | unsettled by its own heading |
| Guiding Principles (typography, all series) | 1,062 | typography is `render_fonts` plus the brand template (§11); `plan_subtitles` receives no brief at all |
| Part 1: Thumbnails | 1,005 | the pipeline renders no thumbnail |
| Production Pipeline | 970 | how the human works |
| Part 3: In-Video Text Overlays | 961 | timed text is declared project-side (§14); no brief-receiving step writes one |
| Portfolio at a Glance | 821 | the eight-series table - and the #258 hazard |
| What needs to be selected per series | 743 | a to-do list for a human |
| Three Content Lanes | 494 | a taxonomy of series; nothing reads it |
| Per-Series Sonic Identity (to be finalized) | 379 | unsettled; 001 has no series spec |
| Typography Philosophy (intro) | 376 | as above |
| Series Identifier System (intro) | 322 | identity marks, not the edit |
| Volume Targets | 280 | videos per month |
| Per-Series Color Identity (to be finalized) | 275 | unsettled; the grade is `series_look` (§12) |
| **total** | **23,877** | **49.8% of 47,903 B** |

That is stricter than the degradation report's 41.9% by 7.9 points, and the difference is entirely the four sections above that a reader could call channel-level rather than out-of-scope (The Netflix Model, Three Content Lanes, Cross-Series Narrative Weaving, Part 3). Either number says the same thing.

**What would make them usable.** Three different answers, and none of them is "rewrite the captain's brief":

- **Six are unsettled by the document's own headings** - `(to be finalized)`, `Open Creative Decisions`. They become usable when the captain settles them, and not before; a model asked to act on a `⚠️ Partial` row is being asked to decide it.
- **Six name things the engine already takes from somewhere else** - typography from `render_fonts` and the brand template, colour from `series_look`, format from `delivery_format`, intro cards from `content.bookends`, timed text from the project's own declaration. They become usable when a brand template for this series carries the parameters, which is where §14 says a per-series value belongs. Prose in a channel document is not a route to any of them.
- **Nine are about the channel rather than about a video** - posting, growth, volume, naming, thumbnails, the portfolio. Nothing in this pipeline makes those decisions and nothing should; they are usable to the captain and to nobody in the DAG. Behind a path they cost 5 bytes of map line each, which is the right price.

The one thing that would help every step at once is the thing the entry above already says: **a brief specific to 001**, which is the captain's writing and not the engine's.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
Copying the brief inline put it at **37.0%-84.3% of those seven prompts - 46.9% of every byte the
pipeline's replayable steps send**. The map is **5,086 bytes against 47,903**, and the same seven
contexts fall from 57,539-130,692 B to 14,260-87,413 B.

Why the map carries a LINE RANGE per heading rather than only a heading: a path a model *can* reach
and a path a model *does* reach are different properties, and the range is what buys the second.

Why `api` is not in `HARNESS_READS_FILES`: `LLMClient.generate` posts one string and has no tool loop.

The other two documents `brief_reference.REFERENCED_INPUTS` carries, measured the same way:
step 4.04's SFX catalogue at **44,575 B and 44.1% of its step's context** (#299), and step 3.02's
per-clip vision analysis at **35,813 B and 40.5% of its step's context** (#F14).

### compile-manifest-read-an-empty-catalog

The per-step `*.json` files in `pipeline_output/` are a best-effort dashboard export.
A step that ran before that export existed leaves none, and a missing file reads as `{}`.

`compile_manifest` spent entire runs compiling against an empty catalog for exactly that reason.

### empty-llm-schema

`present_llm_step` builds the injected schema from `interface.outputs` minus anything the bridge already produced.
`music_selection` therefore asked its model for nothing at all and got `{}` back for months.

The same hole in the QA loop demanded post-bridge outputs from the LLM, so every attempt at `mesh_spine` "failed": its LLM writes `structure`, while the post-bridge computes `audio_spine` and `timed_spine`.

### the-brand-reached-no-planning-step

`TemplateLoader.get_brand_constraints` branched on `step_id == "step_2_01_creative_direction"` and two siblings - the steps' manifest ids.
Its only caller, `present_llm_step`, passes `node_id`, and the DAG's ids are `creative_direction`, `plan_transitions`, `plan_vfx`.
No branch could match, so the constraints string was `""` for every step, every template and every project from the day it was written.
`plan_transitions` chose transitions with no knowledge of which ones its brand permits.

Measured both ways against `default_brand`: 382 B / 179 B / 41 B with the directory name, `""` with the node id.
The whole block costs about 250 tokens across the three steps.

The one existing test called the function with the identifier the FUNCTION wanted, which is why nothing caught it for the life of the code.
Every assertion in `tests/test_brand_constraints_reach_the_prompt.py` therefore starts from `dag.json` and the templates on disk.

A second half was found while fixing it: the `agy` request file wrote `prompt` alone, while `constraints` was concatenated only into the API path's `full_prompt`.
In the mode this pipeline actually runs, a working `get_brand_constraints` would still have reached nobody.

### two-steps-had-no-projection

`mesh_spine` and `review_rough_cut` declared no `context_fields`, so each was handed `temporal_index` in full: a 5 Hz per-clip stream of camera-motion decomposition, optical flow, energy curves and face presence, JSON-escaped inside TOON table cells.

Measured on project 001, `o200k_base` over the archived `llm_requests/*.json`:

| step | before | after | share that was `temporal_index` |
|---|---|---|---|
| `mesh_spine` | 720,067 tok | 9,857 tok | 98.2% |
| `review_rough_cut` | 729,809 tok | 9,353 tok | 97.2% |

Neither step's own code reads it: `grep -rn temporal_index` over each step directory finds only the manifest, the preconditions and the handoff's "Reads" table.
It is still a declared input, and the post-bridge and `step.py` still receive it unprojected - the drop is prompt-side only.

`mesh_spine` also received no vision at all, so it sized `transition_slot` blocks - non-speech picture - before anything had checked that usable non-speech picture existed.
The footage cards that fix it cost 2,389 tokens, 0.3% of what the call cost before.
`clip_catalog` is routed with them because the vision documents are keyed by file stem and everything else in that prompt is keyed `clip_XXX`; a card nothing can be joined to is not delivered.

`word_timestamps` came out of the four `timed_spine` projections in the same pass: about 5,400 tokens a step, read by `spine_contract`, `bookends`, `plan_subtitles` and three post-bridges, all of which receive the unprojected inputs.

Fleet total across the eleven LLM calls: 1,725,098 tok to 233,733 tok, -86.5%.

**Do not read a token figure the pipeline reports about itself.** `pipeline_log.jsonl` records `len(s.split()) * 1.3` against the RAW pre-projection inputs; measured errors run 0.3x to 9.9x in both directions. Measure from the archived request files.

### the-transcript-arrived-with-every-word

`temporal_index.*.speech_regions` gives a step, per clip, every speech region's text AND every word in it with a start and an end.
Two steps declared it: `creative_direction` (2.01) and `speech_sequence` (2.02).

Measured on project 001's seventeen clips, from the archived requests of 2026-08-26:

| | |
|---|---|
| speech regions | 110 |
| individual word-timing records | 1,439 |
| the transcript as plain text | 7,184 B |
| `temporal_index` as sent to 2.01 | 93,246 B |
| 2.01's whole context | 113,053 B |

So 82.5% of the call that decides the creative direction was per-word timings, to say 7 KB of English.

No creative model is asked anything a word boundary answers.
Every reader of those timings is Python, and none of them reads the prompt: `speech_sequence`'s post-bridge opens `pipeline_output/steps/1_04_temporal_index/<clip_id>.json` off disk, and `spine_contract`, `plan_subtitles`, `bookends` and the other post-bridges receive the UNPROJECTED inputs (`run_hybrid_step` hands the post-bridge `dict(inputs)`).

`view:transcript` replaces the whole section with what was said, in which clip, between which two seconds.
The 2.01 table had no `clip_id` column at all before, so the regions were anonymous; the view carries one.

Measured with the step-replay bench against a snapshot of 001, `origin/main` (6a312eb) against the change:

| step | context before | after |
|---|---|---|
| `creative_direction` | 113,053 B | 30,835 B |
| `speech_sequence` | 147,990 B | 65,724 B |
| `render` | 44,582 B | 37,463 B |
| `validate` | 41,683 B | 34,569 B |

`render` and `validate` carried word timings by the OTHER route - `assembly_manifest.subtitles[*].words`, from `plan_subtitles`, 6,953 B each.
Both are deliberately unprojected on a standing decision, so they take a drop-only declaration rather than an allow-list: an allow-list written to remove one field would quietly have become the decision about what the QA calls should ask for.

The first wiring of the view deleted itself. An `llm_only` step is projected TWICE on every run - `gather_step_inputs` projects it and `present_llm_step` projects the result again - and the second pass ran against a tree the first had already taken `speech_regions` out of, so the builder found nothing and the section vanished. That is why a view's NAME is the key it writes.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
`view:transcript` leaves **1,439 per-word records and 82.5% of 2.01's context** behind.

### alphabetical-columns-put-end-before-start

`_is_uniform_dict_list` sorted a table's columns with `keys.sort()`, so every table the serializer built came out alphabetical.

Measured on the ten LLM request payloads the 2026-08-26 clean run of 001 wrote to `pipeline_output/llm_requests/`.
Five tables presented the end of a range before its start:

| step | table | header as sent |
|---|---|---|
| `creative_direction`, `speech_sequence` | transcript, 110 rows | `clip_id,end,start,text` |
| `mesh_spine`, `plan_sfx`, `plan_transitions` | music sections, 14 rows | `duration,end,energy,relative_energy,start,type` |
| `mesh_spine`, `plan_sfx`, `plan_transitions` | energy builds, 10 rows | `duration,end,intensity,start` |

The first transcript row therefore read `clip_006,16.085,14.68,...`, which under the only reading a reader has is a range that finishes before it begins.
The same sort put `content` - the line of dialogue - in column four of the 18-column spine table, behind `alignment_method` and `block_type`.

The order in the data was right all along and was being thrown away.
A spine block is stored `position, block_type, duration_seconds, music_behavior, visual_note, content, clip_id, source_clip_id, source_start, source_end, ...`; a music section is stored `type, start, end, duration, energy, relative_energy`; an energy build is stored `start, end, duration, intensity`; the transcript view builds `clip_id, start, end, text`.
A projected tree carries the order of the manifest's own `context_fields`, which is the same kind of statement.
So first-seen order needs no repair rule bolted on top: after the change every table in all ten contexts reads start-before-end and in-before-out, with nothing else touched.

The hand-built tables (`cuts_toon`, `transcripts_toon`, `broll_candidates_toon`) pass their headers explicitly and were the only tables in the pipeline that were not alphabetical. Deliberate order is now what a table gets by default.

One latent reader bug surfaced with it and is fixed in the same place: `toon_to_json` read a table row with `lstrip(' ')`, which ate a first cell's own leading whitespace as if it were indentation. It could only bite once a column with leading spaces could be column one. The reader now strips exactly the header's indent.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
Alphabetising put `end` before `start` in five tables across four steps - the transcript reached two
prompts as `clip_id,end,start,text` - and led the 18-column spine table with `alignment_method`.

### the-transcript-shipped-twice

`speech_sequence` (2.02) received all 110 transcript lines twice in the same context:

| | `transcript` (the view) | `transcripts_toon` (its own pre-bridge) |
|---|---|---|
| header | `clip_id,end,start,text` | `clip_id,start,end,text` |
| separator | comma, backtick-quoted | tab, unquoted |
| sort | by clip | `os.listdir` order |

19,844 characters, 25% of that step's context, and the two copies disagreed about which column was the start time.

`transcripts_toon` is the copy kept: 2.02's handoff tells the model, by name, to "look up the `transcripts_toon` data", and it is built straight off the per-clip index files.
`creative_direction` has no pre-bridge, so it keeps the view.
The bridge's sort was also `os.listdir` order, which is the filesystem's - the same project could present its clips differently on two runs - and is now sorted by clip and start time.

`semantic_analysis_documents.*.transcript` went in the same pass: it is `""` for all seventeen of 001's clips, and where a legacy document fills it, it is the same words a third time.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
Declaring `view:transcript` on 2.02 alongside its own `transcripts_toon` put **all 110 lines** in the
prompt twice, in two different column orders.

### the-summary-and-its-own-source

`vision_schema_adapter.scene_prose` renders `scene[]` into `analysis.scene`; `camera_prose` renders `camera[]` into `analysis.motion`.
Five steps declared the prose AND the structure it was rendered from, in the same table row: `creative_direction`, `speech_sequence`, `select_broll`, `plan_sfx` and `plan_vfx`.

Measured across 001's seventeen documents:

| | prose | the structure it renders |
|---|---:|---:|
| `analysis.scene` / `scene[]` | 3,462 B | 4,920 B |
| `analysis.motion` / `camera[]` | 982 B | 2,063 B |

The prose is not a lossy summary of either: `scene_prose` renders every field of a scene segment (`start`, `end`, `location`, `type`, `lighting`, `notable_features`) and `camera_prose` every field of a camera segment (`start`, `end`, `framing`, `mode`, `stability`, `movement`).
It is the same content, 30% and 52% smaller, so the prose is what four of the five keep.

`select_broll` is the exception and keeps the STRUCTURE, because its handoff names it: "Use the scene segment bounds in the semantic documents (`scene[]` `start`/`end`) and the per-range `camera[]` entries to pick the stretch".
A step told to read segment bounds must be sent segment bounds.

Still outstanding, and blocked on a handoff the captain has reserved: `broll_candidates_toon`'s `description` column is `scene_prose` again, so 3.02 reads the prose and the structure in two different tables. Removing either means editing 3.02's handoff, which names both.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
Five steps declared both halves of a pair.

### the-beat-grid-in-the-prompt

001's `music_analysis` carries 274 beat times, 69 downbeats and a 198-point energy curve, rendered one value per line as `[0] 0.557 / [1] 1.207 / ...`.

Measured as a share of each step's whole reconstructed context, at `origin/main` (5cee65f):

| array | rows | bytes | `mesh_spine` | `plan_sfx` | `plan_transitions` |
|---|---:|---:|---:|---:|---:|
| `tempo.beats` | 274 | 5,183 | 17.9% | 10.3% | 9.6% |
| `tempo.downbeats` | 69 | 1,255 | 4.3% | 2.5% | 2.3% |
| `energy_dynamics.energy_curve_1hz` | 198 | 3,427 | already dropped | 6.8% | 6.4% |

Nothing reads them.
Beat proximity is decided in `plan_transitions`' post-bridge, which reads the grid through `library/tools/beat_grid.beat_positions` off the UNPROJECTED inputs, before any model sees a context - and the model is separately handed the answer, as `cuts_toon`'s `beat_near_cut` column.

Demonstrated rather than argued. 4.02's recorded answer was replayed through the real pre-bridge and post-bridge against the frozen snapshot at `origin/main` (5cee65f) and at the change: all 13 transitions came back with the same `beat_aligned` verdict (3 true), the same `placement_method`, the same `cut_point_timeline` and the same `snap_delta_seconds` - the whole `transition_spec` byte-identical, and identical to the one the run recorded.

The three arrays go out with `-` drop paths rather than an allow-list, for the reason section 10.1 gives: naming the twenty keys to keep stops delivering the twenty-first. `mesh_spine` already dropped the energy curve that way.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
`music_analysis.tempo.beats`, `.tempo.downbeats` and `.energy_dynamics.energy_curve_1hz` are
**274, 69 and 198 numbers** on 001.

### no-step-that-chose-a-picture-had-seen-one

At `75d3e84`, **0 of the 12 assembled LLM contexts contained `data:image`, `base64`, or an image part of any kind**. Not one step that chooses a picture had ever been shown one.

The prose coverage was not the problem. `view:picture` reaches 6 of 12 steps and describes 766.9 s of 001's 807.0 s (95.0%), up from `scene[]`'s 46.4%. What it carries is ACTION - *"The vehicle is driving forward along a road lined with trees"* - which says what happens and nothing about what it looks like. That is how the captain's marked cutaway could be selected and described accurately at the same time: *"this clip is honestly like broll of nothing"*.

The captain's ruling, 2026-08-29: *"fix the window rule and show the model a frame of the window it will actually receive"*. `cutaway_window.py` (#322) was the first half.

**What the picture shows that the prose did not.** 001's recorded answer put `clip_002` under spine block 13, and at HEAD `choose_window` resolves its `preferred_moment` to 10.000-11.918 s. The candidate table calls that clip *"car interior, dashboard, steering wheel"*, which is true. `clip_002__0010.000.jpg` is six frames from 10.000 s, of which that 1.918 s slot plays the first three: a close, motion-blurred swing across a steering wheel boss and a door card, with the view out of the windscreen not reaching the frame until the last of the six - which this slot never gets to. Nothing in the prose distinguishes that from a usable interior shot. The picture does, at a glance.

**A strip runs to the longest slot that anchors there, and the prompt says so.** One strip per (anchor, slot length) would be exact and would cost five times the strips on 001; instead the row carries `video_in`, `strip_end` and `frames`, and the header states that a shorter slot plays a prefix. It is a real cost - a reader has to do the division - and it is the trade that keeps the table at 100 rows rather than 500.

**The window is enumerable before the answer.** Step 3.02 does not name seconds. It names a clip and a `preferred_moment`, and the post-bridge passes the covered block's own duration to `choose_window` as the target, so a window's start is a candidate span's start and its length is the slot's. `window_anchors` therefore computes the distinct `video_in` values the selector can return, across the spine's real slot lengths - 1.382, 1.918, 2.256, 3.662 and 4.186 s on 001, giving 100 anchors on the vision documents `001-pre-repass-20260829` froze. It is computed rather than taken as the span list because `fit_to_clip` pulls the anchor earlier where a long slot would run off the end of a clip: `clip_006` yields anchors at 18.684, 19.208 and 20.000 s for exactly that reason.

**Checked against 001's own answer.** All five recorded B-roll selections, re-resolved at HEAD and looked up in the strip table:

    clip      block  slot s  video_in video_out  basis                strip
    clip_008  1       3.662     0.000     3.662  single_span          clip_008__0000.000.jpg
    clip_014  4       2.256     0.000     2.256  single_span          clip_014__0000.000.jpg
    clip_001  6       1.382     0.000     1.382  single_span          clip_001__0000.000.jpg
    clip_005  8       4.186     0.000     4.186  moment_match         clip_005__0000.000.jpg
    clip_002  13      1.918    10.000    11.918  moment_match         clip_002__0010.000.jpg

Five of five present, each a non-empty JPEG on disk.

**Why a strip and not a frame.** A close dashboard shot that pans up to nothing looks correct in its first frame; it is the last frame that says otherwise. The ends of the window are not a choice - they are the first and last thing the viewer sees, and the window is defined by them. What is chosen is how much of the middle travels with them, and that is a resolution: no more than `SECONDS_UNSEEN_BETWEEN_SAMPLES` (1.0 s) passes unseen. A 1.382 s slot gets 3 frames, a 4.186 s slot gets 6. The number is stated in the prompt beside the strips, so the model knows a shot can still change between two of them. `WITHDRAWN_DELIVERIES` records the three shapes that were rejected and why.

**The harness, established rather than assumed.** `agy` writes a request file that an AGENT answers - *"the agent IS the LLM"* (`docs/RUN_001_END_TO_END.md` §4) - and an agent with file tools opens a JPEG. Established on 2026-08-29 by drawing a strip with this module's own ffmpeg command and reading it back: the four tiles of 001's `IMG_1816.MOV` at 150.0-152.466 s came back as a walking shot swinging past a storefront. `api` is `LLMClient.generate`, which takes one string and posts it - no image part exists in that call - so it is False, the same verdict `HARNESS_READS_FILES` gives it for the opposite reason. The two enumerations are separate because reading a text file and perceiving an image are different capabilities.

Base64 in the context was rejected outright. No harness here decodes it: `api` charges it as characters and shows nobody a picture, and `agy` would write ~55 KB per strip into a JSON file an agent reads as text. That is a fake image part, and the task that commissioned this said so in advance.

**Cost, measured 2026-08-29 on 001 through the replay bench** (`001-pre-repass-20260829`, `--rev WORKTREE`):

| | before | after |
|---|---|---|
| 3.02 context | 88,475 B | **94,994 B** (+6,519, +7.4%) |
| strips | - | 100, 13 MB, in the step's own directory |
| first build | - | 54.6 s wall clock |
| rebuild, strips on disk | - | 3.1 s |

For scale, the same context already spends **60.7%** of itself on three prose views of one vision analysis (`semantic_analysis_documents` 40.5% + `picture` 11.6% + `broll_candidates_toon` 8.6%). Nothing was deleted to make room: whether prose plus a frame beats a frame alone is unmeasured, and removing the prose on a guess would be the change this evidence cannot support.

**The degradation path, measured on the same context.** Withholding under `api` gives 88,742 B - the 88,475 baseline plus a 267-byte line saying the frames were drawn and are not shown - with no `FRAMES:` path, and `broll_candidates_toon` and `view:picture` untouched. A picture has no smaller textual form, so there is nothing to put back in its place; saying so is what stops a reconstructed context reading as a run where no frames existed.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
The defect, stated: the captain's marked cutaway - a close dashboard shot swinging past nothing - was
described accurately by the prose and chosen anyway. One frame would not have caught it either; a
dashboard shot that pans up to nothing looks correct in its first frame.

`SECONDS_UNSEEN_BETWEEN_SAMPLES` is a resolution, not a taste: one strip per (anchor, slot length)
would be exact and costs **five times the strips on 001**.

**Cost, measured on 001 (2026-08-29)**: 100 strips, 54.6 s of ffmpeg on the first build and 3.1 s once
they are on disk, 13 MB in the step's own directory, and 3.02's context 88,475 -> 94,994 B (+7.4%)
against the 60.7% it already spent on three prose views of one analysis.

### the-director-saw-the-first-nineteen-seconds

`creative_direction` was handed `analysis,assessment,camera,clip_id,duration_s,scene`.
For `IMG_1816_v3` - 188.578 seconds, and the source of seven of the ten spoken lines in 001's final cut - `analysis.scene` read, in full:

    [0.0-18.9s] Outdoor urban area with a parking lot and construction site. outdoor.
    Daylight with soft shadows. notable: Modern multi-story buildings; Construction
    scaffolding and fencing; Parking lot with cars; Paved walkway

That is 10% of the clip. `scene[]` holds ONE segment for fifteen of 001's seventeen clips, so the prose has nothing more to render. The step that chooses the story was choosing it from the opening.

The material to fix it was already measured and already reaching other steps. The vision pass writes an action window per ~10 seconds - 19 for that clip, 86 across the seventeen, covering 95% of the footage - and `vision_schema_adapter._blocks_from_actions` renders them as `blocks`, which `speech_sequence` is routed and `creative_direction` was not.

`view:picture` is one row per record: `clip_id, start, end, visual`. `IMG_1816_v3` now runs 0.0 -> 188.6s across 19 rows.

What the view leaves out, and why:

- `body_language` restates the same moment as posture and expression ("The person is walking towards the right side of the frame" / "The person is walking with a neutral posture, looking forward, and arms at their side"). It is 11,861 B against `visual`'s 8,092 B, and shipping both is the defect the rest of this change removes.
- `label` is `_scene_location_at`, which reads `scene[]` - the field that is degenerate in the first place, and reads "action" for most rows.
- The raw records: `timestamp_range` duplicates `start`/`end` in another unit, and `speech_cue` is null on all 86.

Cost: `creative_direction`'s context 26,668 B -> 31,321 B, +17.4%. It bought the step 170 seconds of a clip it was blind to.

What a scene boundary should MEAN is a separate, open captain decision (#225) and this does not touch `scene[]`. Why `scene[]` covers only 46.4% of 001 is #302, and it is not established.

#### The other five steps, and what `blocks[]` is not

Measured on 001's own `pipeline_data.json`, 2026-08-28, 17 clips and 807.0 s of footage.
Coverage is the union of the described intervals; extent is how far the last record reaches, clamped to the clip's duration (`scene[]`'s bounds are rounded to whole seconds and overshoot four short clips by up to 0.5 s).

| clip | duration | `scene[]` extent | `scene[]` cov. | `blocks[]` extent | `blocks[]` cov. |
|---|---|---|---|---|---|
| IMG_1816 | 188.6 s | 18.9 s | 10% | 188.6 s | 100% |
| IMG_1812 | 139.1 s | 13.9 s | 10% | 139.1 s | 100% |
| IMG_1818 | 85.5 s | 15.0 s | 18% | 85.5 s | 100% |
| IMG_1822 | 85.8 s | 85.8 s | 100% | 85.8 s | 100% |
| IMG_1820 | 52.5 s | 15.0 s | 29% | 52.5 s | 81% |
| IMG_1817 | 41.5 s | 41.5 s | 100% | 41.5 s | 52% |
| IMG_1809 | 26.8 s | 26.8 s | 100% | 26.8 s | 63% |
| **all 17** | **807.0 s** | - | **374.2 s = 46.4%** | 100% on 17/17 | **767.0 s = 95.0%** |

`camera[]`, the third axis, covers 685.0 s = 84.9%.

**`blocks[]` is not a substitute for `scene[]`; it is the other axis.**
`scene[]` carries `location`, `type`, `lighting` and `notable_features` - where the clip is and what it looks like.
The action windows carry what the subject does.
On IMG_1816 `scene[]` says "Outdoor urban area with a parking lot and construction site" for 0-18.9 s and the windows say "The person is looking towards the left side of the frame" for 0-188.6 s; neither answers the other's question, and `blocks[].label` is `scene[]`'s own location, so it reads `"action"` for 17 of that clip's 19 records.
So the view is declared BESIDE `analysis.scene`, never in place of it, and `tests/test_picture_view.py` fails a picture-deciding step that drops the place axis.

**What the truncation cost, on the windows that were actually cut.**
Of the 16 source windows the finished 59.4 s video plays - 11 A-roll, 5 B-roll - **11 of 16 lie wholly inside `scene[]`'s described range and 16 of 16 lie inside `blocks[]`'s.**
The five outside are all `clip_011`/IMG_1816, at 17.67-20.35 s (46% described), 24.17-26.87 s, 63.13-66.67 s, 100.52-116.51 s and 119.23-121.94 s (0% described) - which is what step 2.02 recorded at the time: *"I have selected 27 seconds of a clip I cannot see."*

**The six steps that decide from a shot, and the six that do not.**
Checked against each handoff and, where one exists, that step's reasoning trace from the 2026-08-26 run.

| step | decides from the picture? | what it does about it |
|---|---|---|
| 1.03 `semantic_analysis` | no - it MAKES the description, and its LLM schema is `[]` | - |
| 2.01 `creative_direction` | yes - picks the story and the key moments | already declared the view |
| 2.02 `speech_sequence` | yes - *"I have selected 27 seconds of a clip I cannot see"* | raw `blocks` replaced by the view |
| 2.04 `music_selection` | no - routed no footage at all; decides from the direction and measured audio | - |
| 2.05 `mesh_spine` | no - pacing, gaps and `music_behavior`; its own trace records *"this step assigns no clips"* and `visual_note` is non-binding guidance from the speech | - |
| 3.02 `select_broll` | yes - *"my entire subject is what the picture looks like"* | already declared the view |
| 3.03 `review_rough_cut` | no - routed no footage; reviews the script and the mechanics | - |
| 4.02 `plan_transitions` | yes - `match_cut` is "shape/motion/composition matching across the cut" | view added; its `cuts_toon` carries the camera axis already |
| 4.03 `plan_vfx` | yes - "a static talking-head shot held for a long time" | view added |
| 4.04 `plan_sfx` | yes - whooshes on camera movement, foley establishing a scene | view added |
| 6.01 `render` | no - technical QA of a manifest | - |
| 6.02 `validate` | no - measures the rendered file | - |

**The join, and why it is part of the fix.**
The documents are keyed by file stem and every table a planning step reasons over is keyed by the catalog's `clip_XXX` (section 10.1).
Handing 4.04 an 86-row table keyed `IMG_1816_v3` would have reproduced the `topics_toon` defect step 2.02 reported - *"the two tables cannot be joined without a mapping the context does not contain"* - and 4.04's context names a source file for only 5 of the 17 clips, none of them the backbone.
`_picture` therefore joins through `semantic_index.build_semantic_lookup` against `clip_catalog`, `a_roll_assignments` or `b_roll_assignments`, whichever is routed, and a document none of them names keeps its own id and is reported in `not_in_the_clip_list`.
2.02, 4.03 and 4.04 gained an optional `clip_catalog` edge for it; 2.01 and 3.02 gained `clip_catalog.*.clip_id` so their own catalog table still names the id the rows now use - the 2.01 agent had been deriving that mapping by matching durations.

**What it cost, per step, reconstructed off 001's frozen state with `replay_bench` at `ef3b8f8`:**

| step | before | after | delta |
|---|---|---|---|
| 2.01 `creative_direction` | 35,567 B | 35,470 B | **-97 B** |
| 2.02 `speech_sequence` | 67,582 B | 45,464 B | **-22,118 B (-32.7%)** |
| 3.02 `select_broll` | 87,413 B | 87,316 B | **-97 B** |
| 4.02 `plan_transitions` | 55,458 B | 65,709 B | **+10,251 B (+18.5%)** |
| 4.03 `plan_vfx` | 49,444 B | 60,142 B | **+10,698 B (+21.6%)** |
| 4.04 `plan_sfx` | 90,395 B | 101,093 B | **+10,698 B (+11.8%)** |
| 2.04, 2.05, 3.03, 6.01 | unchanged | unchanged | 0 |
| **net** | | | **+9,335 B** |

2.01 and 3.02 shrink because `clip_011` is shorter than `IMG_1816_v3` on 86 rows, which pays for the `clip_id` column they gained.
2.02 falls by a third because raw `blocks` reaches a TOON cell as `json.dumps` - 33,098 B on 001 - and the view is 10,508 B of flat table for the same records.

The view is 10,508 B and 86 rows.
Three ways to make it smaller were considered and none is taken: dropping `visual` for a shorter field leaves nothing (it is already the only column of the four that is prose); restricting the rows to the clips a step actually places is a judgement about which clips matter that the view has no input to make and 2.01 could not make at all, since nothing is placed when it runs; and the path-plus-map route `brief_reference.py` uses needs a harness that reads files, which under `api` there is not - and it would trade 10.5 KB for a route the model may not follow.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
`analysis.scene` is `scene[]` as prose and `scene[]` is one segment per clip, so a **188.6s clip was
described by its first 18.9 seconds**. The view is the vision pass's per-window `blocks`, which reach
the last second of all seventeen of 001's clips and cover **95.0% of 807.0s**.

`scene[]` covers **374.2s of 807.0s** on the same footage, which is why the view goes BESIDE it and
never in place of it. Repairing `scene[]`'s own coverage is #302: it truncates at 13.9-18.9s on four
long clips and described one 85.8s clip whole, so the cause is not a fixed cap.

Raw `blocks` in an allow-list lands in a TOON cell as `json.dumps` - **33,098 B on 001 against the
view's 10,508** - and carries `body_language`, which restates the same moment at 2.4x the bytes of
`visual`. Step 2.02 was the last step reading it that way.

### the-apostrophe-was-doubled-in-every-prompt

`toon_serializer` quoted table cells with `'` and left `csv`'s `doublequote` on, so a cell holding a comma was quoted and every apostrophe inside it was then doubled, SQL-style.

On disk 001's transcript says `okay, we're here, we're here.`
The model read `okay, we''re here, we''re here.`

232 doubled apostrophes in `creative_direction`'s recorded context alone, across `we''re`, `there''s`, `i''m`.
Nine of the ten archived requests carried it: `creative_direction` 232, `speech_sequence` 280, `plan_transitions` 248, `plan_sfx` 135, `review_rough_cut` 135, `select_broll` 113, `plan_vfx` 109, `mesh_spine` 85, `render` 37. Only `music_selection` had none, and only because none of its cells happened to contain both a comma and an apostrophe.

The round trip was symmetric all along - `toon_to_json` un-doubles it - which is exactly why nothing caught it. Nothing downstream calls `toon_to_json`; the model reads the characters.

`"` was rejected as the replacement. These cells carry two content classes and this pipeline sends both: English prose, which is full of apostrophes, and a JSON document, because a dict or list in a cell falls back to `json.dumps` and is full of double quotes. `"` would have rewritten `[{"time": 0.0}]` as `"[{\"time\": 0.0}]"` - the same corruption, moved.

Backslash-escaping instead of doubling was tried and rejected for the same reason one step further out: `csv` escapes the escape character too, so every `\"` and `\n` `json.dumps` had already written came out double-escaped.
Any CSV dialect has to represent its quote character inside a quoted field somehow and there are only those two ways, so the answer is not to change HOW it is represented but to pick a quote character neither content class contains.
A backtick appears in neither, so neither is altered, and the only character this dialect ever doubles is one this pipeline does not send.

The same reasoning produced the `|` block.
Escaping a newline is correct inside a CELL, where a row IS a line.
Under a dict KEY there is no such constraint, and the values that travel there are documents: the captain's creative brief is 47,903 B of markdown reaching two thirds of the LLM steps, and escaped it arrived as ONE line carrying 700-odd literal `\n`.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
`'` sent `we're` to every prompt as `we''re`, **180 times in one context**; `"` would do the same to
every `json.dumps`'d cell.

Escaping a newline under a KEY rather than in a CELL is what sent the creative brief - 47,903 bytes of
markdown - as one line carrying 700-odd literal `\n`.

### thirty-three-thousand-tokens-for-three-bytes

`semantic_analysis` (1.03) is deterministic, and its own `handoff.md` says so: "No model is asked to author the per-clip analysis document here."
The file sits beside `step.py`, which is what `get_step_implementation` classifies on, so the runner made an LLM call anyway - with `expected_schema` of `[]`, because `already_have` had removed every declared output.
The response was the three bytes `{}`, after 33,051 tokens of vision documents, every run.

The skip is a property of the SCHEMA, not of the step's name: `present_llm_step` returns `{}` when the model is asked for no keys at all.
`render` and `validate` are the same classification accident but not the same call - both ask for something, and what they should ask for is an open captain decision.

### vision-schema-two-views

`vision_pipeline_v3.py` emits `scene[]`/`camera[]`/`actions[]`/`objects[]`/`assessment{}`.
Consumers historically read `analysis.*` and `blocks`.

An absent field warns in `context_projector`; a fabricated one silently misleads the model.
That asymmetry is why the adapter derives only what v3 actually measured.

### brand-template-never-reached-the-run

`pipeline.brand_template` in a project.yaml was read by `delivery_format` and by nothing else in the run.
`load_pipeline_state` populated `state["brand_template"]` only from the process manifest's `default`, which has none, so `gather_step_inputs` resolved the in-code `_get_default_template()` for every step of every project.

A project naming `cinematic_narrative` got vfx_intensity 0.0 instead of 0.3, the full transition vocabulary instead of its four, no `subtitle_style` and no house look at all - silently, with the run reporting SUCCESS.

### delivery-format-is-not-the-source-resolution

The catalog's `source_resolution` describes the footage.
Under the name `project_resolution` it was used as a render target, and project 001 shipped a 1920x1080 master with the framing mechanism idle (target == source means nothing to fit) and the vertical overlays banded down the middle.

Nothing carries the value as a key because `project_resolution` was mapped by no DAG edge at all, and every `.get(..., [1080, 1920])` in the tree silently read its own fallback.
`project_fps` had the identical hole and now has edges.

### the-beat-grid-does-not-start-at-zero

`music_pipeline.analyze_music` emits `tempo.beats` and `tempo.downbeats`.
There has never been a `beat_grid` key, though two steps asked for one and got `[]`.

No track's first beat lands at 0.000s, so a synthesised `[i * 60/bpm ...]` grid is offset from the music by the whole lead-in.

The times are in the MUSIC file's clock and are used as timeline times, which holds only while music is placed at `source_in` 0.
`compile_manifest` asserts that.

### building-is-not-high

Two readers bucketed the same free-text `target_energy` field differently and only one acted.

`transition_selector` did not match "building", which was the correct outcome.
`creative_cohesion.map_energy` substring-matched it to "high" and cut three 500ms defocus transitions to 333ms - on a direction whose own rationale chose "building" OVER "high" because high "would fight the source".

"Building" names a TRAJECTORY, not a level.

`WITHDRAWN_HIGH_WORDS` records why "dynamic" and "fast" are out too.
Widening the high bucket decides which transitions get drawn on every project using the word, so it is a decision, not drift.

### end-time-is-a-source-timestamp

`speech_sequence`'s `end_time` is a SOURCE timestamp.
Reading it as the timeline duration made `creative_cohesion` report 001's 54.77s timeline as "40.1s ... below the minimum target zone" one step after `review_rough_cut` had measured it correctly.

`0.0` means "no evidence" - say so rather than warn about a length nothing measured.

### unread-parameter-names

The renderer dispatches on parameter NAMES in `apply_fusion_comps.build_effect_comp`.
A planner emitting a name nothing reads produces a comp without that effect, and no warning.

`zoom_percent`, `intensity_px` and `scale_factor` killed three of five VFX types that way, and four of the colour grade's five nodes had no reader at all.

Withdrawal is a legitimate outcome and a silent unread key is not, which is why the reason is recorded where the design lives: `GRADE_PIPELINE_DELIVERY` in `step_5_01_color_grade/grade.py`, `WITHDRAWN` in `transition_vocabulary.py`.

`tests/test_manifest_readers.py` discovers every top-level key from `compile_manifest`'s manifest literal and requires a named reader that really contains `manifest[key]`, plus one sentence saying what that reader does to the picture or the sound.
Writing the sentence is the check the AST cannot do for you.

### overlays-that-draw-nothing

Step 4.06 ran Remotion eight times on project 001 for 53.8 MB of ProRes 4444 in which no pixel is ever opaque, and placed all eight on V4 - so `render.json` said "V4: 8".

The output carries NO `available` key when nothing draws, because `available: false` anywhere fails the run.

### manifest-validator-semantic-half

The regression fixtures in `tests/fixtures/captured_run/` come from a real broken run.
Replacing them with empty-list fixtures removes the only thing that proves the assertions fire.

Correction of record: an earlier version of this entry claimed the validator checks ducking curves.
Nothing in the module ever has.

### undeclared-black

The rough-cut review records only negative gaps by design.
Before `_assert_timeline_fully_covered` existed, the only thing that noticed 6.4s of black was the ffmpeg probe in step 6.02, one step from the end of the run.

Both gates bound a declared beat by `MAX_DECLARED_BLACK_BEAT_SECONDS` from the spine contract.
Keep that bound in one place, or a beat passes compilation, burns a render, and fails at the last step.

### safe-area-and-the-caption-grouper

Captain's ruling of 2026-08-25: one master serves Reels, TikTok and Shorts and obeys the strictest of them.
The vertical profile is the published short-form map - top 120, bottom 320, right 120, left 90 at 1080x1920.

`plan_subtitles.text_fits_on_screen` measured real glyph widths and had NEVER RUN.
It was switched on by `audio_spine["subtitle_style"]["font_path"]`, which no producer ever wrote, so grouping fell back to `max_chars = 18` - correct for a 58px caption, and 1663px of ink in a 1080px frame at the 160px style templates resolve today.

A safe area applied only on the render side lifts the captions clear of the UI and leaves them clipped left and right, which is why all four consumers read the one enumeration.

Measuring a VARIABLE font requires setting its weight axis: Montserrat-Variable defaults to Thin.

A group split cannot fix one over-wide word, so such a card carries `fit_scale` and the render draws THAT CARD smaller.
The style's font size is untouched, because caption size is an open captain decision.

**Moved out of the rule (AGENTS.md 10.2) on 2026-08-29.**
The progress bar sat at `bottom: 0`, `width: 100%` until #321.

### the-squashed-face-frame

`compute_face_presence` extracted at `scale=320:180`, so a rotated iPhone clip - 1080x1920 after ffmpeg's autorotate - reached the frontal cascade squashed about 5.3x horizontally.

On project 001 that split the index exactly along `rotation`: 1,886 detections over 3,578 landscape samples, 1 over 461 rotated ones, and `subject_center_x` was None for every portrait clip.

Size is a trade, and 480 is where it was measured: bigger keeps buying recall AND false positives, and 0.34 is where `subject_framing` starts believing a track.

Note what the fix does NOT fix.
The cascade fires on trees and dashboards, and on 001's face-free B-roll a 3s window crosses 0.34 at every size above 240.
The squash was suppressing those by suppressing everything.

`tests/test_face_sample_aspect.py` pins the aspect, not a detection count, which would rot with the OpenCV build.

Face detection needs Haar cascades, so `opencv-python` is pinned `<5`; OpenCV 5 removed them.

### subject-centers-by-clip-read-only-a-mapping

Step 1.04's real output shape is `{"temporal_event_indices": [...], "full_indices": [...]}` - a LIST of per-clip dicts, not a mapping.

`subject_centers_by_clip` read only a mapping for the life of P1.2, so it returned `{}` on every real project while its tests, which all fed invented mappings, passed.

### the-letterbox-default

`_conform_fields` used to have a second branch that ran whenever nothing declared a framing intent.
Its rule was *letterbox if the primary subject is visible during this clip's range*, which on a talking head is every clip.

Project 001 shipped its A-roll in rows 656..1263 of 1920 with 61.6% of every frame below luma 12, and the whole `framing_intent`/`subject_center_x` mechanism in the other branch never executed anywhere.

The thing that branch protected - a blind centre crop beheading a speaker - is protected by the pan now, not by refusing to crop.

This reverses the letterbox-default half of Q1 (2026-08-16); the tracking half stands.
A series that wants bars declares 0.0, and `cinematic_narrative.yaml` does.

### hollow-prosody-files-cached

`speech_advanced_pipeline` wrote `{"method": null, "error": "parselmouth not installed"}` to `<clip>_prosody.json` like any other result.

Step 1.05 counted seventeen files as seventeen profiles, reported `available: true` in 0.1s, and 4.2 KB of identical error records went into the creative-direction prompt.
The files CACHED, so the next run skipped the clips.

`praat-parselmouth` is in `requirements.txt`; CI filters it with the heavy ML deps.
It DOES install on 3.14 now - `praat_parselmouth-0.4.7-cp314-cp314-macosx_11_0_arm64.whl`, measured 2026-08-28 - and it measures 001's parking-lot iPhone audio usefully: clip_011 came back `mean_f0_hz 119.5`, `voicing_percentage 40.8`, `hnr_db 6.3`.
Two things still stand between that and a working step 1.05. It is handed the raw `.MOV` (`prosody.audio_file` is the path out of `raw/`) and Praat answers `PraatError: Not an audio file` - the audio has to be extracted first, with ffmpeg. And `intensity_contour_50ms` is capped at 1200 samples, which is the first 60 s of a 188.6 s clip.

### usable-ranges-were-the-whole-clip

Every one of the 17 vision documents from project 001's 2026-08-26 run carried this, together:

```json
"camera_stability": "unknown",
"usable_ranges_method": "unmeasured",
"usable_ranges_signals": [],
"usable_ranges": [[0, 188.578]]
```

Three fields say nothing was measured and the fourth asserts the whole clip.
`_unmeasured()` returned `[[0, duration]]`.

Two causes, both structural:

- **The DAG runs `semantic_analysis` (1.03) before `temporal_index` (1.04).**
  `_compute_usable_ranges` read only temporal-index signals, so on a first run it had nothing to read and every clip came back unmeasured.
- **`load_temporal_index` looked in `raw/analysis/temporal_index/`**, where the index lived before the project layout moved it to `pipeline_output/steps/1_04_temporal_index/index/`.
  So even a re-run, with a complete index on disk, found nothing.

**What it cost the cut.** The B-roll selector reads `usable_ranges` to pick which 2.5 seconds of a clip to cut.
It chose `clip_006` (IMG_1811.MOV) source 1.65-4.15s for the first interjection, rationale *"a neutral observational wide, chosen for being unremarkable"*.
The master at 25.0s - source 3.15s of that clip - is motion-blurred asphalt with no legible subject.
The reasoning was sound; nothing had measured the window.

**The measurement.** `library/tools/analysis/picture_quality.py`: variance of a 4-neighbour Laplacian on a greyscale frame, 5 Hz, short side bounded to 180px.
A sample is soft below `max(80.0, 0.20 x p90-of-this-clip)`; three consecutive soft samples (0.6s) make a range.
Relative alone would rate an end-to-end blurred clip's own mush as normal; absolute alone cannot tell flat asphalt from a missed focus.

Measured on 001's own 17 clips: the threshold for clip_006 is 166.3, and the samples across the chosen window read
4.6, 28.2, 310.3, 57.1, 19.5, 479.5, 19.2, 23.1, **73.7**, 130.4, 371.4, 265.3, 860.8.
1.15s of the 2.50s window is flagged, the master's own frame at source 3.15s falls inside `2.8-3.6`, and `usable_ranges` for that clip becomes `[[2.0, 2.8], [3.6, 7.2], [8.2, 22.87]]` - the window is no longer offered.
Verified by eye: clip_006 @3.15s and clip_008 @5.0s are unreadable blur and both fall inside a reported range; clip_006 @5.0s (a legible "THE WORKS" sign) and clip_008 @2.0s (a sharp brick wall) do not.
8 of the 17 clips come back with no soft range at all.

**Cost.** 47.4s for 17 clips / 807s of 1080p footage on a cold page cache: 2.79s per clip, 0.059x realtime.
The vision pass beside it costs 140s per clip of model time, so this is ~2% on top.

**What the sampling can and cannot resolve.** Samples are 0.2s apart, so a soft window shorter than 0.2s can fall entirely between two and is invisible.
The 0.6s reporting floor means the shortest window guaranteed to be reported is 0.6s; below that a window may land on only two samples and be dropped.
The measurement cannot tell motion blur from a missed focus pull from a genuinely low-texture subject, which is why the reason it records is `soft_picture` - what it measured - and not a cause it did not.

**The rest of the assessment object, checked for the same pattern.**
`speech_present`/`speech_coverage` carry `None` with `speech_coverage_method: "unmeasured"`, `camera_stability` says `unknown`, and `content_type` says `unknown` - all honest, and none of them was filled in to match the fix.
One was not: `primary_subject_visible` was set to `[]` when the assessment model call produced nothing at all, which reads as "the subject appears nowhere in this clip" and reaches the B-roll prompt as that.
It is now `None` on that branch - the same defect as `[[0, duration]]`, inverted.

**Moved out of the rule (AGENTS.md 10.3) on 2026-08-29.**
A stored document written before the producer was fixed still carries the contradiction, and rendering
its range makes the stale assertion read as a measurement. That is why the DISPLAY reads the method
rather than the ranges.

### semantic-analysis-triggers-a-vision-run

Any clip whose id is not already a `raw/analysis/clip_profile_<clip_id>.json` triggers a full local vision run when `step_1_03_semantic_analysis/step.py` is invoked.

### gates-that-cannot-fail

`timeline_qa.verify_fusion_comps` had `pass` as its only loop body and reported the Fusion pass healthy whatever the timeline held.

`creative_cohesion`'s engagement check tested `isinstance(engagement, (int, float))` on the DICT that `engagement_scorer` emitted, so it read every real passage as unscored.
Fixed by reading the composite - and then the composite itself turned out to be a constant, which is the entry below.

A gate that cannot fail is worse than no gate, because it reads as coverage.

### the-review-recommended-what-it-could-not-do

`creative_cohesion` runs at step 5.03, next to last. On project 001's 2026-08-26 run its whole
output was:

```json
{
  "cohesion_score": 90,
  "warnings": ["Hook engagement (49) is lower than peak body engagement (62)"],
  "adjustments": [{
    "target_step": "speech_sequence", "field": "segment_order",
    "suggested_value": "front_loaded",
    "reason": "Highest engagement segments should be front-loaded for hooks"
  }],
  "applied_adjustments": [],
  "applied_by": "step_5_04_compile_manifest"
}
```

and the run log recorded step 5.04 refusing it: *"re-ordering the narrative would invalidate every
downstream timing; it belongs in step_2_02"*. The refusal is correct - the subtitle timings, the
transition placements, the B-roll windows, the music behaviour map and every rendered overlay are
computed from timeline positions. `applied_adjustments` was empty on that run and on every run
before it.

**The review cannot move upstream of what it reviews.** Its inputs are `transition_spec` (4.02),
`enhancement_spec` (4.03), `sfx_spec` (4.04) and the spine, all made after the ordering it was
complaining about. Moving it to 2.02 would place it before its own inputs exist. So it is scoped
where it runs: `library/tools/cohesion_scope.py` splits every finding into one the manifest
compiler applies and one that names the step that owns it, and
`tests/test_cohesion_scope.py` drives the real applier against both halves rather than believing
either.

**What the review actually measures now, checked on 001's own state rather than on a fixture.**
Three of its five checks could not fire on real data:

| check | reads | on 001 |
|---|---|---|
| transitions | `target_energy` high/calm | `"building"` reads as `moderate` (§10.1), so it is inert |
| SFX density | same | inert |
| colour grade | `color_grade_spec["mood"]` / `["grade_name"]` | step 5.01 emits **neither**; its spec carries `grade_pipeline`, `grade_pipeline_delivery`, `per_clip_adjustments`, `fusion_look`, `series_look`, `series_look_title`, `look_notes`, `withdrawn`, `output_color_space`, `consistency_notes` |
| engagement | the model's ranking | fires - and this was the one finding |
| duration | the spine | fires; 59.44s, inside the zone |

The colour check is REMOVED for the same three reasons the pacing check was (`PIPELINE_PLAN.md`
P4.2): it read a key no producer emits, the tests that covered it supplied `mood` themselves, and
it emitted no adjustment even when it fired. The values that ARE in the spec are numbers -
`series_look.py` gives every look a `saturation` and a `contrast` - and turning one into "soft" or
"punchy" against an energy word means choosing a threshold nobody measured, which is AGENTS.md
10.5's line. The step's now-dead `color_grade_spec` input declaration and its DAG edge went with
it.

**`cohesion_score` was a constant of the same family as the engagement numbers.** It is 100 minus
a hand-picked weight per finding - 5 for a transition, 5 for SFX density, 5 for the grade, 10 for
the hook, 3 for the duration - and the 90 above is 100 minus one 10. Nothing measured those
weights. Nothing outside the step read the result: not `compile_manifest`, not `render_qa`, not
the dashboard, not `step_exporter`. Its one internal reader, `if score < 70`, gated a block that
mutated `transitions` in place and appended sentences to `applied_adjustments` - and the step
emits only `cohesion_review`, so neither the mutation nor the list ever left the process. It
reported "applied" about nothing. Removed rather than recomputed.

**Measured before and after, on 001's real state**, with step 2.02 re-run so the passages carry
the `{rank, composite, basis}` judgement PR #257 asked for. Same state, same runner input
assembly, both revisions:

```
BEFORE  cohesion_score: 90
        adjustments:          1  (speech_sequence.segment_order -> "front_loaded")
        applied_adjustments:  0
        5.04:  applied 0, not_applied 1

AFTER   (no score)
        adjustments:          0
        observations:         1  (speech_sequence.segment_order, owner step_2_02_speech_sequence,
                                  act on it with --rerun edit)
        5.04:  applied 0, not_applied 0, observed 1
```

The finding itself is unchanged and still reported: *"Hook ranks 3 of the sequence; the passage
the model ranked strongest (1) is body passage 9 (clip_017 at 31.454s) (composite 92 against the
hook's 68)"*. What changed is that it is no longer presented as a change that was made.

`step_exporter._summary_creative_cohesion` was reading `passed`/`cohesive`/`notes`/`feedback`,
four keys the step has never emitted, so 001's `summary.md` was the title and nothing else beside
an `output.json` carrying a warning and an adjustment. It now reads the keys the step writes.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
`creative_cohesion` (5.03) runs next to last and reads the transition, SFX and VFX plans, which is why
it cannot be moved upstream of the decisions it reviews.

`OWNED_UPSTREAM` carries no `suggested_value` because `"front_loaded"` was a word 5.03 invented about
an ordering it never computed.

`cohesion_score` was 100 minus a hand-picked weight per finding, nothing outside the step read it, and
its one internal reader gated a block whose mutations never left the process. It is REMOVED, not
recomputed.

### five-tests-skipped-in-every-environment

The same shape as `gates-that-cannot-fail` above, in the suite rather than in the pipeline, and
harder to see because a skip LOOKS like a considered decision.

Five tests guarded an import with `pytest.skip`, and the symbol did not exist:

```
tests/test_bridges.py:9    from ...step_2_02_speech_sequence.bridge      import pre_bridge
tests/test_bridges.py:36   from ...step_2_02_speech_sequence.post_bridge import post_bridge
tests/test_bridges.py:57   from ...step_3_02_select_broll.bridge         import pre_bridge
tests/test_bridges.py:71   from ...step_3_02_select_broll.post_bridge    import post_bridge
tests/test_step_runners.py:45  from ...step_1_05_prosody_analysis.step   import analyze_prosody
```

Both bridges are SUBPROCESSES - `run_pipeline.run_subprocess` hands them JSON on stdin - so
neither has ever had a `pre_bridge` or `post_bridge` function; `analyze_prosody` lives in
`library/tools/analysis/speech_advanced_pipeline.py`, not in step 1.05. These are not the
dependency-absence trap #243 fixed. They skipped whatever was installed, on every machine, in CI
and locally, since they were written, and pytest printed all five in its summary every run
without anything objecting.

Six more in the same two files reported a result nothing had measured. `test_temporal_index` and
`test_prosody_analysis` had `pass` as their entire body. `test_assign_aroll`,
`test_render_subtitles`, `test_render_motion_graphics` and `test_creative_cohesion` wrapped an
import and a call in `except Exception: pass` - and none of those four symbols exists either, so
all four reported PASS. A green dot reads worse than a skip.

Two subtler ones, found with the tooling out. `tests/test_generator_overlay_routing.py` searched
`EFFECT_ALIASES` for an entry pointing at a generator and skipped when it found none: the only
alias is `push_in -> zoom_emphasis`, a clip effect, and an alias may only RENAME a capability
(10.5), so it never finds one. `tests/test_framing_intent.py` parametrised `None` into a
malformed-declaration test and skipped that case in the body, `None` meaning "not declared".

And nine in `library/tools/fusion/tests/test_parser.py`, which read
`library/steps/step_6_01_render/fusion_comps/hook_1.comp` and called
`self.skipTest("hook_1.comp not found")`. `git log --all -- '*hook_1.comp'` is empty: the file is
in no commit in this repository's history, so those nine assertions have never run. Their subject
is the PARSER, not that file, so the input is now supplied by the test and every assertion is
unchanged.

**What was restored, and what was deleted.** 2.02's pre-bridge and 3.02's `resolve_broll` had no
coverage anywhere and got real tests - `tests/test_speech_sequence_bridge.py` and the second half
of `tests/test_select_broll_bridge.py`, both mutation-checked. The other three named tests were
deleted, because 3.02's pre-bridge, 2.02's `enrich_speech_sequence` and step 1.05 are all covered
properly elsewhere (`tests/test_select_broll_bridge.py`, `tests/test_captured_run_regression.py`
and `tests/test_passage_engagement.py`, `tests/test_prosody_failure_is_loud.py`). Step 3.01
`assign_aroll` is left with no direct test and that is stated rather than papered over.

**The check.** `tests/skip_audit.py` and `tests/test_no_unfailable_tests.py`, plus the session
hook in the repo-root `conftest.py`. The source half is decidable from the tree - it reported all
eleven of the `tests/` findings above against `origin/main` - and the runtime half requires every
skip a run actually reports to match a declared `EnvironmentCondition`, because one environment
cannot prove "skipped in every environment" but naming the environment that RUNS the test can.
Both fail rather than report: reporting is exactly what pytest was already doing for months.

**Measured, 2026-08-28.** System python (no heavy stack): 2309 passed, 1 skipped. A .venv with
parselmouth, whisperx, easyocr, torch, mlx_vlm, librosa and cv2: 2307 passed, 3 skipped. Same
2310 collected, and the two skip sets are complementary halves of one condition -
`praat-parselmouth` installed or not.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
A condition that reads THIS REPOSITORY'S contents is not an environment: five tests skipped everywhere
for months because the symbol they imported does not exist, and nine more because a fixture file has
never been in any commit.

Four tests whose body was `pass`, or whose whole body was a `try` swallowing every exception, reported
PASS - which reads worse than a skip.

### every-line-scored-the-same

On project 001's 2026-08-26 run, nine of eleven speech passages scored an identical composite of **49**, and the `hook` component was **30 for ten of eleven**.
The recorded `rationale` was the string `"Hook:30, Flow:60, Value:60"` - the numbers restated, not a reason.
This was the only ranking signal the edit's ordering rested on, and `creative_cohesion` drew its one finding from it: `"Hook engagement (49) is lower than peak body engagement (62)"`.

| role | hook | flow | value | composite | text |
|---|---:|---:|---:|---:|---|
| hook | 30 | 60 | 60 | 49 | i can feel the silent judgment of the people behind me. |
| opening | 30 | 60 | 60 | 49 | and i have an announcement to make. |
| opening | 15 | 60 | 60 | 44 | um, not really an announcement... |
| development | 30 | 60 | 60 | 49 | and so my very, very small announcement is... |
| development | 30 | 80 | 80 | 62 | i get caught up in all the numbers and metrics... |
| development | 30 | 60 | 60 | 49 | the goal of all this is just to get momentum going. |
| development | 30 | 60 | 60 | 49 | it doesn't matter what i'm using to record. |
| development | 30 | 60 | 60 | 49 | it doesn't matter if it's even edited. |
| development | 30 | 60 | 60 | 49 | it just matters that it gets posted. |
| climax | 30 | 60 | 80 | 56 | i've been literally this week i've quit every single day... |
| resolution | 30 | 60 | 60 | 49 | even if it's bad, even if i hate it, i will post it. |

`score_hook` was `50`, minus 20 whenever `prosody_data.get("energy_rms", 0)` came back under 0.3.
Installing the missing prosody dependency would not have moved one point of it, for three independent reasons, each sufficient on its own:

1. **No producer.** `energy_rms` is emitted nowhere in this repository. `analyze_prosody` returns `method`, `accuracy`, `pitch_stats`, `pitch_contour_10ms`, `voice_quality`, `speaking_rate`, `intensity_contour_50ms` and `duration_s`, and no key of any of them is called `energy_rms`. Measured with praat-parselmouth 0.4.7 installed, against 001's own `IMG_1816.MOV` audio.
2. **No routing.** `prosody_data` was `data.get("prosody_analysis", {})` in step 2.02's post-bridge, and the DAG carries three edges into `speech_sequence` - `semantic_analysis_documents`, `full_indices`, `creative_direction`. `prosody_analysis` is not among them, so the value was `{}` on every run and the `if prosody_data else 0` guard took the constant branch before `.get` was ever reached.
3. **Wrong granularity.** Step 1.05's output is `{"available": ..., "profiles": {clip_id: ...}}` - one record per RUN. Even with a real per-clip `energy_rms`, every passage would have been handed the same number, so the constant would have stayed a constant.

The other two scorers were not measurements either. `score_flow` carried the comment `# Dummy flow scoring logic for scaffolding` and was 60, +20 for the substrings "first"/"then"/"finally", -30 under five words. `score_value` was 60, +20 over twenty words, +15 for the literal substrings "important"/"key"/"insight".

The scorers are withdrawn, with the reason for each recorded in `library/tools/passage_engagement.py`, and `speech_sequence` attaches no `engagement` key.
`engagement_of` returns **None** for a passage that carries no score, and `creative_cohesion` now says `"Engagement not compared: no passage carries an engagement score, and the pipeline measures none"` - at no cost to the cohesion score, because an unmeasured signal is not a defect in the edit.

Two dead readers went with it. `plan_sfx` passed an `engagement_scores` mapping into `audio_reactive_sfx.align_sfx_to_prosody` to pull "rise"/"build" SFX 1.5 s before an engagement peak: no step has ever emitted that key, no DAG edge carried one, and the threshold was `> 0.8` against a 0-100 composite.

The route not taken at the time: having the model score each passage, which `speech_sequence` is already positioned to do - it reads every passage and already writes a prose `flow_note` per segment. That needed a new field in `library/steps/step_2_02_speech_sequence/handoff.md`, and the twelve `handoff.md` prompt files are the captain's to change.

#### what replaced it, and what the replacement is worth

On #246 the captain unreserved that one file for that one field, and nothing else in it.
Step 2.02 now asks for `engagement` on every passage it selects: a `rank` over that sequence, a `composite`, and one sentence of `basis`.

**A rank is what the consumer wanted; a score alone would have been false precision.**
`creative_cohesion`'s only question is whether the strongest moment is at the front, and the withdrawn scorer's failure was that its numbers could not order anything.
The composite is kept because it carries magnitude a rank cannot, and because `engagement_of` was already built to read it - but it is reported beside a finding and never fires one.

**The measurement #243 could not make.** Three answers to the identical reconstructed prompt, at one revision, against snapshot `001-2026-08-26T1058Z`, matched across runs by normalised passage text. Seven passages appear in all three answers:

| rank per answer | rank spread | composite per answer | composite spread | passage |
|---|---:|---|---:|---|
| 1, 1, 1 | 0 | 95, 94, 94 | 1 | i almost didn't do this again... i've quit every single day |
| 2, 2, 2 | 0 | 92, 90, 91 | 2 | i can feel the silent judgment of the people behind me |
| 4, 3, 3 | 1 | 80, 78, 83 | 5 | and so my very, very small announcement is... |
| 3, 5, 4 | 2 | 86, 71, 80 | 15 | and a lot of the stuff i ended up doing was not really... |
| 8, 8, 6 | 2 | 54, 48, 68 | 20 | today is march 25th, 2026. |
| 6, 6, 8 | 2 | 68, 66, 58 | 10 | i really have no expectations. |
| 7, 7, 9 | 2 | 61, 55, 54 | 7 | you know, i finished up school about three and a half years ago |

Rank spread: min 0, median 2, max 2. Composite spread: min 1, median 7, max 20.

So the ordering is exactly stable where a reader acts on it - the same passage came first in all three answers, and the same passage came second - and unstable in the middle, where the composite is worse still.
That is why the gate compares the TOP rank against the hook and nothing else, and why `MEASURED_SPREAD` is recorded in `library/tools/passage_engagement.py` rather than left as a claim.

**The instruction did not visibly change what 2.02 selects, because the selection was already unstable.** The three answers picked nine, nine and ten passages, seven of them common, at ONE revision. A between-revision difference smaller than that means nothing.

**One answer of three named the hook `hook` and the body `body`** rather than `hook_segment`/`body_sequence`. That key naming was never pinned - the schema injected from the manifest is `"speech_sequence": {}` with no sub-keys - and it is not caused by this field, but it is what a reader of these numbers should know about the run they came from.

Two further honesty notes on the figures. The answers came from ONE model driven through the `agy` route (a headless agent reading the reconstructed request), which is not necessarily the model a given run uses; a different model's spread has not been measured. And the bench rebuilds the QUESTION, not the answer - `docs/STEP_REPLAY_BENCH.md` says so directly - so these are three fresh answers to a frozen prompt, not three recorded runs.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
Across three answers to the identical prompt at one revision on 001's frozen snapshot, the two
strongest passages came back in the same order every time; mid-list ranks moved two places and the
composite on those same passages moved up to twenty points. That spread is what `MEASURED_SPREAD`
records.

### gates-that-fail-correct-output

`subtitle_qa` sampled two fixed instants of a transparent overlay, which on an ordinary caption pause are blank.

gemma-4-12b passed those blanks on one run and failed them on the next, then failed four demonstrably clean caption frames three times out of three with invented defects - "cut off by the bottom edge" of type with 150 clear rows beneath it.

Its mechanical half - ink exists, ink is inside the frame, ink is bottom-positioned - now decides, and the model's typography opinion is recorded rather than enforced.

### baseline-craft-properties

Source: `data/vep-craft-reference-decomposition/report.md`; its Appendix A is the reproducible method.
Test: `tests/test_baseline_craft_properties.py`.

The true-peak half of `measure_lufs` used to print +1.85 dBTP inside a detail string and drop it whenever the LUFS check had already failed.
It sets `passed = False` now.

Chroma and the mix REPORT A NUMBER and pass.
The chroma floor is an open captain decision and the mix has no delivery route, and a gate that must fail teaches everyone to ignore the report.

`min_sat: 10` is GONE, replaced and not supplemented: frame-mean saturation cannot be the statistic, because the captain's two reference frames differ 9.3x in it and that floor would have rejected the one they chose for Punch Card.

### a-dim-shot-is-not-a-letterbox-bar

Issue #221. Test: `tests/test_baseline_craft_properties.py`.

P1 called a row "lit" at or above `LIT_LUMA_THRESHOLD` and read every other row as letterbox bar.
That measures DARKNESS, and darkness does not separate a black bar from a night shot.
Project 001's master of 2026-08-26 is correctly framed and the gate failed it on every run since:

    [framing] picture occupies 100.0% of the frame (spread 0.31 over 119 samples)
     - the picture changes size within the video: 69.2% at 50.0s vs 100.0% at 0.0s
       (spread 0.31, bound 0.05) - one video has one geometry

The 50.0s frame is a car interior. Its top 100 rows measure min/max/mean luma 0/23/4.3, and
boosting them 4x shows a headliner, a window with light streaking across it and the top of the
subject's cap. 53.0s is the same shot at 0/40/6.8; 30s and 36s are the dark lower half of a
dashboard shot read as a bottom bar.

**Two properties separate a bar from a dark picture, and 001 needs both.**
Of the 621 dark rows in the 50.0s frame, 506 are INTERIOR - not against either boundary, where a
letterbox bar cannot be - so contiguity from the frame edge disposes of those on its own. The
remaining 115 run from the top edge and are picture because they have STRUCTURE: their
within-row standard deviation is 3.95 at minimum and 5.28 at the median.

**Both bounds are measured on real encoded bars, not chosen.**
The same master padded to 1080x608 inside a 1080x1920 frame and re-encoded gives bar rows of
exactly 0.0 standard deviation and 0.0 difference between adjacent rows. At crf 30 with noise
added before the encode it is still 0.0 everywhere except the single ringing row against the
picture edge, at 2.9. `BAR_ROW_MAX_STD = 2.0` therefore sits between every measured bar row and
every measured false-positive picture row.

| what | measured on 001's master | measured on the letterboxed copy |
|---|---|---|
| occupancy, dark-row method | median 100%, min 69.2%, spread 0.31 | 31.7% |
| occupancy, bar method | median 100%, min 99.6%, spread 0.0042 | 31.7% |
| verdict | PASS | FAIL, "letterboxed and nothing asked for bars" |

**The bound is not knife-edge, and the answer does not turn on it.**
Sweeping `BAR_ROW_MAX_STD` over 0.5, 1.0, 2.0, 3.0 and 3.9 moves 001's spread through 0.0000,
0.0000, 0.0042, 0.0318 and 0.0458 against a bound of 0.05, and moves the letterboxed copy's
occupancy not at all - 0.317 at every value. Detection of a real bar is invariant to the choice;
only the false-failure side responds, and 2.0 is in its flat region.

**The manifest was available as a second source and is not used.**
`5_04_compile_manifest/output.json` records `source_width`, `source_height`, `needs_conform` and
`fill_zoom` per clip, so an expected occupancy could be computed and compared. It is not needed:
the pixel measurement clears the consistency bound by 12x and the fill floor by 20x, in both
directions, so a second source would add a coupling to the manifest's own correctness without
changing a verdict. The intent the gate checks against still comes from the manifest -
`framing_intent`, as before.

**The reported keys say what they now measure.** `median_lit_fraction` / `min_lit_fraction` /
`max_lit_fraction` became `median_picture_fraction` / `min_picture_fraction` /
`max_picture_fraction`, because the number is the picture's share of the frame height and no
longer a count of lit rows. Nothing outside the test read them.

**Sampling is untouched.** `DEFAULT_SAMPLE_FPS` is still 2.0 and the change is a per-frame
reduction, so the short-cutaway hole 2 Hz exists to close is exactly as closed as it was.
Measured on 001 at 1, 2 and 4 Hz the spread is 0.0000, 0.0042 and 0.0083.

**What it still cannot do.** A picture row that is genuinely dark AND genuinely featureless - an
unlit ceiling with no detail in it - is indistinguishable from bar by any pixel method, and is
counted as bar. 001 spends at most 8 of its 1920 rows that way, 0.4%, which is what the 0.95
fill floor and the 0.05 spread bound leave room for. The check also measures rows only, so a
pillarboxed but full-height picture passes; that was true before this change too.

**Only 001 is available locally.** The behaviour is established on that master plus the
letterboxed copies built from it and the synthetic fixtures in the test. Whether another
project's master would newly pass or newly fail is not established.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
Reading every dark row as bar cannot tell a night shot from a black bar, and failed a correctly-framed
master.

### no-creative-floors

A B-roll minimum and an SFX minimum both existed.
The captain removed them outright on 2026-08-20, declining warnings, a reconciled range and per-template minimums by name.

The accepted consequence is that a thin edit is no longer caught mechanically.

A floor in the PROMPT pads just as effectively as one in the bridge.
"You MUST plan exactly 5-15" is what put two cutaways and one sound into the shipped edit with rationales that said so.

`tests/test_no_creative_floors.py` originally guarded only the two steps the ruling was written about.
That is how `plan_vfx`'s "at least 3-7 VFX items" plus "every talking head clip MUST have at least a slow zoom" (001: eight effects on eight clips, one each, alternating) and `speech_sequence`'s "strictly select exactly 10-15" survived it for five days.
It now guards every step in `CREATIVE_PLANNING_STEPS`.

`_assert_sfx_distributed` catches a collapse - every SFX on one frame - not a sparse plan, which is why it is not a floor.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
Reading only prompts is how the VFX pair survived (#192); listing only the steps the ruling named is
how step 4.02's `min_trans` floor and its `defocus` injection survived longer still.

The review-step floors, in full: `creative_cohesion` (5.03) demanded at least 10 SFX per minute of a
"high" energy edit and at most 15 of a "calm" one, and required every drawn transition under 500 ms -
proposing `duration_frames: 10`, the one field `compile_manifest` rewrites. All four are removed.

`adjustments` is now empty for every input at every energy, not just at the `moderate` 001 declares
(#272).

### the-pipeline-invented-taste-where-no-step-ran

Captain, 2026-08-26, on finding a fixed creative direction in step 2.01: "we need to remove all hardcoded fallbacks from the repo, they should not be there, we should not be hardcoding creative stuff like that".

**The line applied.** A CREATIVE fallback substitutes taste - a mood, a theme, a transition choice, an effect, a sound, an energy arc, a pace chosen for feel. A MECHANICAL default is a safe technical value - a frame rate, a timeout, a codec, a retry count, a path. Creative fallbacks go; mechanical ones stay. Where a creative value is genuinely absent the step fails or reports plainly, because a silently-defaulted mood ships and a stopped run does not.

**Two corollaries the audit needed.** First: a value that means "nothing is drawn" is not taste. `hard_cut` and `jump_cut` are in `transition_vocabulary.CUT_TYPES` and draw nothing, and `series_look`'s `NEUTRAL_CDL` is the identity transform - falling back to the absence of decoration is not choosing decoration. Second: a rule that acts on a value the creative direction really declared is not a fallback. `creative_cohesion` may judge a transition against a DECLARED "high"; what it may not do is invent the word "high" first.

**What was found, and where.**

The one that prompted it, `step_2_01_creative_direction/step.py`: 77 lines producing `"target_mood": "motivational"`, `"target_energy": "medium"`, `"energy_arc": "start medium -> build tension -> climax at key moments -> resolve"`. The manifest declares `implementation.default.runtime: "llm"` with `entry_point: handoff.md`, so it never executed. Deleted: dead code that states taste as fact misleads the next reader whether or not it runs.

`step_4_02_plan_transitions/post_bridge.py` carried the third creative floor, and it was the largest. `min_trans = max(1, total_cuts // 3)`; an injection loop appending `{"type": "defocus", "duration_feel": "medium", "rationale": "Default defocus added at scene boundary due to mood/topic shift"}` wherever the semantic mood or the keyword tags differed between two clips; and, if the padded plan still fell short, `sys.exit(1)` with "You MUST plan at least N transitions at DISTINCT cut points" - word for word the guard removed from `plan_vfx` on 2026-08-25. It survived the ruling of 2026-08-20 because `CREATIVE_PLANNING_STEPS` did not list step 4.02 and because it was code, not a prompt.

`library/tools/transition_selector.py` invented a DRAWN transition for any cut the plan had not decorated: `fade_to_black` on a `breather` or `transition_slot`, `flash` on a `music_behavior` of `step_up`, `defocus` on everything else, rate-limited to one per twenty seconds, plus a final `settle(preferred_types[0])` that could draw whatever the brand's allow-list happened to list first. Now: no request means `hard_cut`. The `step_up` branch was already unreachable - the word is not in `music_behavior.MUSIC_BEHAVIORS`, which is the second instance recorded under `silence-lost-in-the-two-word-vocabulary`; removing the branch closes that open item too.

`library/tools/audio_reactive_sfx.scale_sfx_density` DELETED entries from the plan: on "calm" it kept only transition, whoosh and ambient sounds; on "moderate" it dropped every second impact. The energy it judged by came from `creative_direction.get("energy_level", "moderate")`, and `energy_level` is not a `creative_direction` key at all - the real one is `target_energy` (see `library/tools/energy_reading.py`) - so the hardcoded "moderate" decided it on every run the pipeline has ever made. It was inert only by a SECOND key mismatch: it filters on `sfx["type"]` while the creative plan writes `sfx_type`. Deleted, not unwired.

Four plan-completion substitutions, all reachable, all now a loud drop with the reason: `plan_vfx` completing a missing `effect_type` to `slow_zoom_in` and a missing or unrecognised `intensity` to `moderate`; `plan_sfx` completing a missing `sfx_type` to `whoosh` and a missing or unrecognised `volume_level` to `subtle` at -14 dB; `compile_manifest` completing an A3 entry's `sfx_type` to `whoosh` a second time, one step from the timeline; and `plan_transitions` completing a missing `duration_feel` to `medium`. A declared value is still honoured in every case - only the invention is gone.

`EFFECT_ALIASES` mapped `slow_zoom` and `ken_burns` to `slow_zoom_in`. An alias may RENAME an effect and may not CHOOSE one: a name that says only "zoom" does not say which way. Both are in `WITHDRAWN_ALIASES` with the reason; `push_in` -> `zoom_emphasis` stays, because those are two names for one punch-and-settle.

`creative_cohesion` read `creative_direction.get("target_energy", "moderate")` and `timeline_duration or 60.0`, then scored the edit against both - and its transition adjustments are APPLIED by `compile_manifest`, so an invented energy word reached the picture. Both gates now say they could not measure. That is the same remedy `measure_timeline_duration` already documents: 0.0 means no evidence, so say so.

`apply_caption_case` lowercased on ANY unrecognised mode "for safety", so a template that typed `as-written` got every caption on screen lowercased with nothing saying so. It raises now.

**Left in place, and why.** The duration targets - `duration_targets.get_target_duration_zone`'s 54/60/66 and `music_selection/bridge.DEFAULT_TARGET_DURATION_SECONDS = 60.0` - are a number that is also a creative choice; the captain lists rather than decides. `subtitle_style.resolve_subtitle_style`'s `or "default_subtitles"` and `EffectSlots.caption_case = "lowercase"` are load-bearing: every shipped template declares both, and removing them stops a template-less project rendering captions at all. `TRACK_LEVELS` in `audio_mix` and `VOLUME_MAP`/`DURATION_DEFAULTS` in `plan_sfx` are the mix, which the captain narrowed out of scope. `music_behavior`'s `SPEECH_DEFAULT_BEHAVIOR`/`NON_SPEECH_DEFAULT_BEHAVIOR` and `framing_intent`'s `DEFAULT_FRAMING_INTENT` are documented single-enumeration decisions AGENTS.md mandates by name. `select_transition`'s same-clip `jump_cut` DESCRIBES a cut inside one take and draws nothing.

**Dead slots found and reported, not changed.** `StyleSlots.energy_profile` and `EffectSlots.sfx_density` both default to `"moderate"` and have NO reader anywhere in the pipeline - only `brand_registry.validate_template`'s own enum check. Two templates set them; nothing acts on them.

`tests/test_no_creative_floors.py` now drives the real bridges and reads the modules, not only the prompts. It is deliberately narrow - a grep for the word "default" would fail on every legitimate frame rate in the tree - so it asserts on bridge OUTPUT and on the specific `.get(literal, creative_value)` shapes that were removed.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
Dead code that states taste is removed, not left: step 2.01's `step.py` produced a fixed
`target_mood`/`energy_arc` and could never run, because the manifest declares the step pure LLM.

### silence-lost-in-the-two-word-vocabulary

Test: `tests/test_music_behavior_vocabulary.py`.
Enumeration: `library/tools/music_behavior.py`.

`mesh_spine` plans what the bed does under each block in five words - `prominent`, `background`, `fade_in`, `fade_out`, `silent`.
From the initial commit (2026-07-29) until 2026-08-25, `compile_manifest._spine_block_entry` threw that word away and recomputed a different one:

    "music_behavior": "full" if block.get("block_type") in
        ("transition_slot",) + BOOKEND_BLOCK_TYPES else "ducked",

Two words, neither of them in the spine's vocabulary, derived from `block_type` rather than read from the plan.
There is no word for silence in it, so a block the spine planned `silent` reached the manifest saying the bed plays.
It was not even self-consistent by its own logic: an `intro` block carries no speech either and got `ducked`.

**The trace, made before the change** - `_spine_block_entry` has exactly ONE caller, `compile_manifest` step.py, and the value it produced had NO reader:

- `manifest["_spine_blocks"]` is read in three places: `compile_manifest._assert_timeline_fully_covered`, `step_6_02`'s `declared_black_beat_ranges`, and `render_qa.measure_speech_above_bed` (block_type by position). All three read `position`, `timeline_start`, `timeline_end`, `block_type` and the black-beat keys. None reads `music_behavior`.
- The plan that DOES carry a level takes the other branch: `audio_mix` (5.02) reads `audio_spine.structure[*].music_behavior` straight from `mesh_spine`, not from `_spine_blocks`, and maps `silent` to -96 dB in `music_automation`.

So the docstring in `step_6_02` that reported this was right about the narrowing and about nothing reading it.
The stronger reading - that silence was destroyed before the delivery hop - is NOT what the trace shows: silence survives into `music_automation` intact, and 001's silent blocks played music because the delivery hop set no level at all, which is a separate finding.

What the narrowing cost was the manifest's own record of the plan contradicting the plan, in the place a reader would naturally look for it, in a vocabulary that could not express the thing the creative direction most wanted to say.

The remedy is not to delete the field - that is the `unread-decisions` remedy, and it is a different one.
The field carries the word now, resolved through one enumeration that both halves read, so they cannot disagree about what a word means or about which words exist.
The one true thing the reduction knew - a block with no speech under it has nothing to duck for - survives as the DEFAULT for a block that planned nothing (a bookend card, which `bookends.py` assembles and which never passes through the spine's LLM). A block that planned something keeps what it planned.

`full` and `ducked` are recorded as withdrawn, and asking for either raises and says which real word to use.

**A second instance on the same field, since closed**: `library/tools/transition_selector.py` selected a flash when `music_behavior == "step_up"`.
`step_up` is not in the vocabulary and no producer emits it - the only occurrences in the repo were that branch and two tests that hand-wrote the word - so the flash branch was unreachable. It went with the rest of the scene-change heuristic on 2026-08-26 (see `the-pipeline-invented-taste-where-no-step-ran`): the branch decided a drawn transition nobody asked for, and it decided it off a word that does not exist. No real word has been made to mean "the music steps up here", and none is needed - a transition is drawn because the plan asked for one.

---

## Section 11 - third-party assets

### the-unlicensed-powergrade

The repository previously carried one PowerGrade, `cinematic_warm.drx` - a free gift from Zay's Aesthetics with no written terms of any kind, and so no commercial usage clause for a repository that produces commercial video.

It was removed along with the whole PowerGrade route: `build_powergrade.py`, the `luts/` and `dctls/` preset directories, and `preset_indexer.py`.
`tests/test_color_grade_delivery.py` fails if any `.drx` reappears.

Captain's ruling 2026-09-10, over that removal: *"what do you mean DRX is out, its just lua tables in a file that can be imported right?"* - firstmate's reasoning was wrong twice (a `.drx` is XML wrapping a hex `FieldsBlob`, not lua tables; and the route probed was the wrong object - the import route is `GalleryStillAlbum.ImportStills` and the apply route is `Graph.ApplyGradeFromDRX`, both proved by calling on `SCRATCH_grade_probe`, never by `dir()`). And the asset is the captain's own - `The Grade Free_1.13.1.drx`, Zay's Aesthetics free PowerGrade, supplied at `/Users/prajwal/Downloads/TheGradeFree.zip` - so the licensing caution was firstmate being skittish about the captain's own call. The rule now stands as AGENTS.md 11 has it: a `.drx` lands only with its provenance recorded, and an unrecorded one is refused (`tests/test_color_page_grade.py`). No file ships in this repo; the captain's file proved the route from outside it.

### the-webfont-race

Montserrat is bundled rather than fetched because `@import url('https://fonts.googleapis.com/...')` with no `delayRender` made typography a race with the network.
A lost race rendered captions in Chromium's fallback sans at a different width, with nothing downstream able to tell.

### timed-text-drew-in-the-wrong-face

`TimedTextOverlay` loaded NO font at all until 2026-08-20 while naming a family in CSS.
Every card it rendered was already in the wrong face, and the frames were still valid pictures of the right size.

---

## Section 12 - the look

### there-is-no-house-look

Captain, 2026-08-28: *"i want no hardcoded values. **there are no house glow looks, there are no settled house grain or anything**"*.

**What was there.** `library/tools/series_look.py` held four complete looks - `pmk_default`, `warm_reflection`, `electric_contrast`, `film_stock_warmth` - each carrying a slope, offset and power triple, a saturation, a pivot contrast, a glow gain / threshold / size, a grain power and size, and a vignette blend and falloff. The DIRECTIONS were cited to the captain's own planning docs at `PLAN/series portfolio '26 planning/` (read-only, outside this repo) by document and section - "warm shadows, never blue", "highlights pushed to cream", "deep, inky blacks". The STRENGTHS were not, and could not be: a document states a direction, not a magnitude. `warm_reflection`'s own comment says its blue slope was "pulled back" from a first-party DCTL's 0.88 to 0.935, and its 1.08 saturation came from a judgement that "the channel-level DNA outranks one series". Those are numbers this repository chose.

**What reached 001.** `project.yaml` names no brand template. Before #297 that resolved to `default_brand.yaml`, which named `pmk_default`, so on the 2026-08-26 run every one of these reached the finished video:

| where | what | count |
|---|---|---|
| `color_grade.per_clip_adjustments[].cdl_values` | `slope (1.020, 1.005, 0.985)`, `offset (0.006, 0.004, 0.002)`, `power (0.995, 1.000, 1.005)`, `saturation 1.10` | 10 of 10 clips, one identical value |
| `fusion_effects.per_clip[]` | `grade_contrast 0.1`, `glow_gain 0.12`, `glow_threshold 0.78`, `glow_size 3.5`, `film_grain_power 0.18`, `film_grain_size 1.5`, `vignette_blend 0.16`, `vignette_soft 0.35` | 17 of 17 comps |

**What 001 loses, measured off the manifest.** Removing the eleven look keys from `assembly_manifest.fusion_effects.per_clip` leaves **0 of 17 comps with any key at all**: 001's `vfx` list is empty (0 entries) and no clip carries a zoom, so every Fusion comp in that video exists solely to carry the look. The CDL half goes from `slope (1.020, 1.005, 0.985) / saturation 1.10` to identity on all 10.

**What could NOT be established.** How that reads on screen. The captain has live markers and a Text+ block on 001's timeline, so it is not re-rendered and there is no A/B frame. From the values alone: the CDL is a ~3.5% red-over-blue slope split and a +10% saturation, the contrast is a 0.1 pivot, the glow is gated at 0.78 so it touches only the brightest part of the frame, and the grain is 0.18. Whether the sum is visible at a glance or only in a difference image is exactly the question a render answers and these numbers do not.

**Where the routing landed.** No value was relocated. The four looks are removed, the five shipped templates each dropped their `series_look:` and record the series DIRECTION their planning document states plus the fact that no strength was ever chosen, and `style.series_look` became a declaration of values that `resolve_look` reads. Nothing in `library/templates/` declares one, so at HEAD every project - 001 included - gets no grade.

**A second, quieter one, found on the way.** `build_effect_comp` defaulted `vignette` to True, so any clip carrying a zoom and no explicit vignette key got one at `blend 0.25`, `soft 0.35` - through a `.get` default rather than a plan or a template. `normalize_effects` set `vignette: False` for the no-zoom case only, which is why it never showed: the clips it hit were the ones with a VFX zoom on them. It now draws only where one was asked for.

Nothing depends on a file inside a Resolve installation.

### the-exposure-probe-measured-nothing

**The claim.** Step 5.01 emitted `per_clip_adjustments` for 10 clips with **1 distinct CDL value across 10 of 10** and the note *"Exposure within normal range, no adjustment needed"* on **10 of 10**. A per-clip mechanism produced one global answer.

**It was the mechanism.** `_estimate_exposure` ran `ffprobe -of csv=p=0`, which writes one field plus its separator - every line arrives as `140.914,`. `float(line)` raised `ValueError` on all of them, each was skipped by the `except ValueError: continue`, the list came out empty, and `if not values: return 0.0` returned the same 0.0 it returns for a perfectly exposed clip. Every failure mode returned 0.0: ffprobe missing, file missing, probe timed out, nothing parsed. Reproduced at HEAD against 001's real state on 2026-08-28: `Counter({0.0: 10})`, 4.0s wall clock, all 10 source files present on disk.

**What the discarded samples said**, recomputed from the same command with the separator stripped:

| clip | file | samples | mean YAVG | offset (old formula) | gain |
|---|---|---|---|---|---|
| clip_011 | IMG_1816.MOV | 60 | 145.50 | -0.470 | 0.722 |
| clip_012 | IMG_1817.MOV | 60 | 131.21 | -0.184 | 0.880 |
| clip_017 | IMG_1822.MOV | 60 | 53.12 | +1.378, clamped to +0.500 | 1.414 |
| clip_008 | IMG_1813.MOV | 55 | 136.94 | -0.299 | 0.813 |
| clip_014 | IMG_1819.MOV | 29 | 113.26 | +0.175 | 1.129 |
| clip_001 | IMG_1806.MOV | 22 | 135.75 | -0.275 | 0.826 |
| clip_005 | IMG_1810.MOV | 48 | 114.52 | +0.150 | 1.110 |
| clip_002 | IMG_1807.MOV | 60 | 107.19 | +0.296 | 1.228 |
| clip_006 | IMG_1811.MOV | 60 | 135.26 | -0.265 | 0.832 |
| clip_004 | IMG_1809.MOV | 60 | 115.99 | +0.120 | 1.087 |

**10 of 10 distinct**, spanning 0.722x to 1.414x - a 0.97-stop spread. clip_017 is the in-car passage the creative direction calls the emotional floor of the piece, and it is the darkest thing in the edit by 54 luma.

**So the note was the defect, not the number.** AGENTS.md §10.3: no field reports a default as though it were measured. The step now records `measured_luma`, `measured_luma_method`, `measured_luma_samples` and, where nothing measured, `measured_luma_reason` - and `exposure_offset` is `null`, never `0.0`.

**Fixing the parser is not the same as applying the result**, and the two were separated deliberately. `_REFERENCE_BRIGHTNESS = 122.0` ("typical well-exposed iPhone footage sits around 115-130. We aim for the middle") and `_MAX_EXPOSURE_OFFSET = 0.5` are decisions about how bright a finished video is and how far the engine may overrule the footage. Both were picked by nobody, and turning the parser on without routing them would have shipped an unreviewed exposure change to every clip of every project - on 001, a 0.72x on its brightest clip and a 1.41x on its darkest. So the reference is now `style.series_look.exposure_reference`, declared or absent, and with nothing declared the luma is measured, recorded and acted on by nothing. The offset is `log2(reference / measured)`, which is the definition of a stop rather than a chosen scale, and there is no clamp.

---

## Sections 13 and 14 - bookends, timed text and asset ownership

### bookends-only-on-some-videos

Captain's Q7, 2026-08-16: "wire them up, but only on some videos".
A template that declares nothing gets nothing.

A malformed declaration raises rather than being dropped, because a dropped declaration is a card the editor believes shipped.

`library/tools/execution/import_endcard.py` appended one out of band after compilation and is deleted; see the row in `docs/PIPELINE_PLAN.md` section 5.

### the-4th-wall-end-card

`docs/ASSET_LIBRARY_PLAN.md` was ratified 2026-08-20 and is the standing test.

The case that motivated it: the 4th Wall end card failed all three questions and was removed on the captain's ruling.
It was one previous trial run's finished artwork - series copy, absolute frame numbers from a 60.000s cut, an unbundled typeface - filed in the now-deleted `library/templates/fourth_wall.yaml` as series defaults, where no step ever read it.

The template itself was deleted on 2026-08-20 on the captain's ruling: the series template arrives later, whole, with authorisation.

Per-series typefaces live per project, not in the engine (captain's ruling, 2026-08-20).
Accepted cost: each project folder carries its own fonts and licences, and `bookend_render.py` must stage them.

### the-night-card-y-band

Geometry is normalised against the whole delivery frame, not the picture area inside letterbox bars, because there is no picture-area enumeration to resolve against.

Measured on the only finished render on disk - project 001, `Pipeline_Edit.mp4`, 1080x1920, 16:9 landscape source - the picture occupies rows **656..1263** and the burnt-in captions rows ~1699..1765.
So a `y` in 0.35..0.65 is over picture whether the source letterboxes or fills.

`tests/test_night_card_delivery.py` asserts the real card's ink lands in that band.
Put the removed end card's `y: 0.15` back and it fails at rows 260-327.

`effect.timed_text_overlay` had no reader at all, and carried the marker `NO_READER` in `tests/test_manifest_readers.py`.
That marker is gone: the reader is `library/tools/timed_text_overlay.py`, which is what makes a timed-text declaration legal under question three of the asset test.

`tests/test_timed_text_delivery.py` renders a 24-frame fixture through the real path and asserts the declared colours are in the declared rows at the declared frames - the delivery half, without which "a reader exists" is the same empty claim `smart_reframe` made for months.

The worked example of a project-side declaration is `tests/fixtures/night_card_project/project.yaml` - Through the 4th Wall's Night card, the second item in the captain's ratified build order.

### the-caption-box-is-not-one-line

The measured caption fitter landed with `fits(text)` meaning "fits on ONE line", and grouping used it.
`SubtitleOverlay/index.tsx` draws the words in a `flexWrap: "wrap"` box bounded by `captionMaxWidth`, so a card wider than one line becomes two and is drawn in full - `CaptionFitter.widest_word_width`'s own docstring says exactly that, and `fit_scale` exists only for the one word that cannot be wrapped.
So grouping was stricter than the render.

At the 160px `default_subtitles` style, 816px of usable ink holds roughly eight characters on one line.
The pre-measurement `max_chars = 18` grouping was about seventeen characters, which is two lines' worth - so switching to one-line fitting HALVED the words on every card without anyone choosing it.

A card is on screen until the NEXT card's first word, so halving the words halved the display times.
Measured on project 001, 2026-08-26: **76 of 96 cards under 0.5s**, the shortest 0.080s - 2.4 frames at 30fps, the single word `'day.'`.
`manifest_validator`'s P6 hard-fails on any card under 0.5s, so no render of any project was possible.

Two changes, and the second is the one that mattered:

* Group against the BOX (`fits_in_box`, `MAX_CAPTION_LINES`), not one line. `MAX_CAPTION_LINES` is 3, and the number is measured: at 2 the same run still lands 11 of 52 cards under half a second, because a two-line card at 160px holds about 17 characters and this speaker delivers 17 characters in well under half a second several times. Three lines is 576px of a 1920-row frame on a 320px bottom inset, so a card stays in the lower third and clear of the speaker.
* Split each block's words BALANCED rather than greedy. A greedy fill packs each card to the width limit and leaves the remainder as the next card, and the remainder is the card that flashes. `split_into_groups` now solves, per block, for the partition with the fewest cards below the floor - a small dynamic program over the words, ties broken towards the longest shortest card and then towards ending cards on punctuation. It models the real display duration, including the extension the per-block pass applies and the block end the last card is clamped to; optimising anything else optimises a number nobody renders.

Result on 001: **96 cards to 41, and 76 flashing cards to 2.**

The two survivors are the one case grouping cannot reach, and P6 now reports them rather than failing:
a card is on screen until the next card's first word, and the LAST card of a block has no next word - it leaves when the block does.
Block 2 is "today is march 25th, 2026.", its final word is spoken for 0.21s, and block 3's captions begin in the same frame.
No partition of those five words, at any width, makes that card longer.
The exemption needs the card to be last in its block AND to end at the block's end, both provable from the manifest, because a floor that exempts the general case is a gate that cannot fail.

`tests/test_caption_safe_area.py` pins all of it, including a greedy-versus-balanced fixture - project 001's own opening line, where greedy leaves `'me.'` alone for 7 frames.

### the-default-that-outvoted-the-plan

The captain's ruling of 2026-08-20 removed the creative floors.
`tests/test_no_creative_floors.py` guarded the PROMPTS, and two floors in the VFX post-bridge survived it by being code:

* `inject_default_ken_burns` added a `slow_zoom_in`/`slow_zoom_out` to every speech block over three seconds that the plan had left alone, "because the style spec requires subtle motion on all A-roll clips >3s".
* An empty plan exited 1 with `You MUST plan at least 3-7 VFX items`, contradicting the step's own handoff, which says an empty list is a legitimate answer for a piece that wants stillness.

Observed on the run of 2026-08-26: a deliberate three-effect plan came out of the bridge with seven, three of them on blocks the spine had marked "no effect" - including the closing eleven seconds, where the creative direction says the admission must not be decorated.
It is also where the previous run's `VFX family 'slow_zoom' covers all 8 V1 clips` P7 failure came from: the padding created the uniformity the check exists to catch.

Both are gone, and `tests/test_no_creative_floors.py` now drives the post-bridge as well as reading the prompt.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
The VFX post-bridge padded the plan up to every eligible block and failed the step when the plan was
empty, and survived the creative-floors ruling by living in code rather than in a prompt.
`tests/test_no_creative_floors.py` now drives the post-bridge itself.

### hard-cuts-are-not-an-effect-on-everything

P7 failed a build for `Transition type 'hard_cut' covers all 8 cuts`. Two things were wrong.

**`hard_cut` draws nothing.** `transition_vocabulary.CUT_TYPES` says so in as many words - "Instantaneous transitions. Nothing is drawn" - and the transition handoff asks for them to dominate ("hard cuts dominate - use `hard_cut` as the default for most cuts").
A video whose every cut is a hard cut is the ABSENCE of decoration, not decoration applied to everything, and failing it is the creative ceiling P7's own note forbids it from becoming.
`CUT_TYPES` are now excluded from the check.

**The denominator counted the wrong thing.** `cuts` was `len(v1_clips) - 1`, but `transitions` carries an entry for every spine-block boundary, including the transition slots whose picture is B-roll on V2.
Project 001 plans 13 transitions across 9 V1 clips, so 8 hard cuts - 62% of what was planned - were counted as "all 8 of them".
The denominator is now the transitions the plan actually wrote.

### the-mix-target-is-not-a-separation

Issue #183 asked whether a render should FAIL when speech sits under the music bed, and the captain's answer was "turn it on, but only after the next full run of 001 confirms it passes cleanly with the mix in place".

That run was performed on 2026-08-26. **It does not pass cleanly, so `SPEECH_ABOVE_BED_GATES` stays False.**

The delivery route works. Two windows planned `silent` measured -68.6 and -69.3 dBFS against a -36.6 dBFS median of the non-silent windows, and the OTIO round trip wrote a 22-keyframe curve spanning -96..-6 dB.
6 of 9 speech-bearing windows met the margin.

Three `background` windows did not: +12.6, +13.6 and +12.5 dB where the plan asks for +18.
What fails is the TARGET, not the delivery.
`background` means -18 dB and `audio_mix` applies that as an absolute clip gain, while the check reads it as the separation between the speech and the bed.
Those agree only when the music file's own level is at or below the speech's.
On 001 the music sits about **8.6 dB hotter** than the iPhone speech - raw music -10.5 dBFS against speech -19.1 dBFS in the worst window - so -18 dB of gain buys about 12.5 dB of separation and no mix setting reaches 18.

Turning the gate on today would fail every project whose bed is mastered louder than its dialogue, which is most of them.
Promoting it needs one of: a loudness-relative bed level in `audio_mix`, or a target here that is the planned dB minus the measured source difference.
Either is a decision, not a fix.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
The full run of 001 on 2026-08-26 did NOT pass `speech_above_bed` cleanly. The mix does reach the
file - the planned silence measures 32 dB below the bed - but `background` means -18 dB of CLIP GAIN
while the check reads it as SEPARATION.

### the-bridge-table-that-was-projected-away

Found by the 001 A/B run of 2026-08-26, the run that measured whether #194's context cleanup changed the creative decisions.

`context_fields` is an allow-list, and a pre-bridge's output is merged into the inputs BEFORE it runs.
So a table the bridge computed and the handoff names is deleted unless somebody also thought to add it to the list, and on four of the five hybrid steps nobody had:

| step | table the bridge builds | in `context_fields` |
|---|---|:-:|
| `speech_sequence` | `transcripts_toon`, `topics_toon` | no |
| `select_broll` | `broll_candidates_toon` | **yes** |
| `plan_transitions` | `cuts_toon` | no |
| `plan_vfx` | `vfx_candidates_toon` | no |
| `plan_sfx` | `sfx_candidates_toon` | no |

The symptom is invisible from inside the step.
The bridge logs success, the handoff still says "The `cuts_toon` table provides a summarized list of cut points... Use this value for `cut_point_position` in your response", and there is no `cuts_toon` in the prompt.
Measured on the live run: `plan_transitions` was asked for one entry per cut, keyed by `cut_point_position`, with the table that carries those ids absent - along with `beat_near_cut`, the bridge's own answer to "is a musical beat within 100 ms of this cut", which is the data the step's beat-alignment rule depends on.
It was answerable only because `timed_spine` was separately routed and the beat grid could be recomputed by hand from `music_analysis`.

The fix restores by NAME in `present_llm_step` rather than adding four allow-list entries, and only where the projection dropped the key entirely - so a manifest that names its table may still narrow it with sub-paths.
Four more list entries would have fixed these four steps and left the fifth new one to be found the same way.

`tests/test_llm_context_routing.py` pins it two ways: every table a bridge builds is mentioned by its handoff, and every table reaches the prompt.
Both fail on the parent revision for exactly the four steps above and pass for `select_broll`.

### the-decision-that-was-remembered-not-sourced

**2026-08-28**, from the creative-decision degradation report on the 001 run of 2026-08-26
(findings F7 and F13). Two steps decided well with no access to the material their own prompts
tell them to use, and the reason nobody noticed is that one agent answered all twelve LLM steps
in sequence.

**`mesh_spine` (2.05) never received the creative direction.** It places all five non-speech
gaps, sets each one's length, and sets the `music_behavior` of every block - including `silent`
across the climax, which is the strongest creative decision in the finished video. Its context
keys on the run of record were `speech_sequence`, `semantic_analysis_documents`,
`music_analysis`, `music_selection`, `clip_catalog`, `brand_content`, `project_folder`. Its own
handoff opens by naming "the creative direction (the vision)" as one of three core reads, and
its third evaluation criterion is "the spine follows the creative direction's energy arc".

Its reasoning trace justified the silence by quoting step 2.01 - *"2.01 said the piece 'resolves
by getting quieter and more certain, not louder', and dropping the music out and bringing it back
quietly is that shape"*. That sentence is the `energy_arc` field of `creative_direction`, and it
was in no part of 2.05's context. The agent had written 2.01 four steps earlier and was quoting
itself.

**`plan_sfx` (4.04) never received the transition plan.** Pairing sounds with transitions is the
first purpose its System Context names and its second evaluation criterion is "every creative
transition has at most one SFX". The two sounds in the finished video sit at `spine_block_position`
8 and 13, which are exactly the two boundaries the transition plan decorated with a `defocus` -
the only two of fifteen cuts that draw anything. Its first rationale opens *"Under the defocus
transition at cut 8"*. Nothing in its context named a transition of any kind: the strings
`defocus`, `dissolve`, `hard_cut` and `transition_type` each occurred zero times in it, both
before and after the brief became a reference. The agent had planned those transitions itself,
one step earlier.

Measured with `replay_bench` against the frozen snapshot `001-degradation-20260828`, before and
after the two edges:

| step | context before | after | what appeared |
|---|---:|---:|---|
| `mesh_spine` | 23,625 B | 25,705 B | `creative_direction` +2,079 B |
| `plan_sfx` | 45,438 B | 45,930 B | `transitions_toon` +491 B |

Measured against `382ad8b`, the base after the brief became a reference. Against the base before
it, `plan_sfx` read 88,717 B -> 89,209 B: the same +492, on a context twice the size. The two
changes are independent - the brief is prompt-only and no bridge reads it, so re-measuring moved
the denominator and not the delta.

`verify` names the two additions by section and nothing else, against the same archived contexts
it already accounts for six earlier changes in - which is how the instrument's visibility was
established before its negative result was trusted.

**What was deliberately left out.** `creative_direction.key_moments` (1,803 B) and
`.rationale` (2,941 B) do not reach 2.05: `key_moments` names clips and source ranges that step
2.02 has already turned into the passages 2.05 arranges, and `rationale` is 2.01's account of how
it reached the direction, addressed to somebody auditing 2.01. The transition plan reaches 4.04
as a five-column table, not as the 11,273-byte `transition_spec`: the per-cut `rationale` prose
is three quarters of those bytes and is 4.02 explaining itself.

The other two reads that handoff's State Interaction table names are not routed, and for
different reasons. `subtitle_entries` is nested inside step 4.01's `subtitle_plan`, which is
17,427 B on 001 - a per-word copy of speech text `timed_spine` already carries in full - against
a `plan_sfx` context of 45,438 B of which the brief map is already 5,240; the case
for click-on-caption sounds is real but it is a size decision of its own, not this edge.
`vfx_plan` names no state key this pipeline writes at all: step 4.03 writes `enhancement_spec`,
which was `{"vfx_creative": []}` on the run of record. Routing it is a rename on a frozen handoff,
not a manifest edge.

**The table is keyed by the spine block the cut leads INTO**, because `spine_block_position` is
the identifier `sfx_creative` names. A table keyed by timeline seconds would make the model
re-derive the join out of `timed_spine` first - the same defect that gave `sfx_candidates_toon`
zero rows and `cuts_toon` before it.

**Neither defect is visible in the output**, and that is the durable lesson. Both steps answered
correctly. A single-agent run cannot distinguish a step that read its context from a step that
remembered, so the check has to be made on the assembled context, not on the video.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
What the two now-routed edges carry: `creative_direction` reaches `mesh_spine`, which sets every gap
and every `music_behavior`; `transition_spec` reaches `plan_sfx`, which is told to pair sounds with
transitions.

### a-prompt-that-described-an-empty-table

**2026-08-27, on the clean run of project 001 of 2026-08-26** (issue #223). The second
occurrence of the defect fixed for `cuts_toon` in #218, found the same way: by an audit, weeks
after the run.

`step_4_04_plan_sfx/handoff.md` spends a paragraph on a table, column by column:

> The `sfx_candidates_toon` table provides a summarized list of clips and events with the
> following fields: `segment_id`... `text`... `action_sfx_suggested`: Pre-computed suggestion
> on whether SFX are needed based on audio transients.

What `pipeline_output/llm_requests/plan_sfx.json` carried:

```
sfx_candidates_toon: |
  [0]{segment_id,text,action_sfx_suggested}
```

#### Three failures in the same six lines of bridge

The table was built from `data.get("a_roll_assignments", {})`, and:

* **no DAG edge carries `a_roll_assignments` into `plan_sfx`.** The eight edges into the node
  route `music_analysis`, `semantic_analysis_documents`, `creative_direction`,
  `full_indices`->`temporal_event_indices`, `b_roll_assignments`, `rough_cut_review`,
  `timed_spine` and `project_fps`. The `.get()` answered `{}` and the loop body never ran once;
* had it been routed, `assign_aroll` emits entries keyed **`spine_block_position`**, not
  `segment_id`, so every row would have read `unknown` - `cuts_toon`'s exact symptom;
* and those entries carry **no `text` key at all**, so the second column would have been blank.

A fourth thing was wrong above the keys: `segment_id` off an A-roll slot is not the identifier
the step's answer has to name. `interface.llm_outputs` asks for `spine_block_position`. The
table is now one row per SPINE BLOCK, keyed on the position the answer uses, which is also the
only enumeration that includes the non-speech beats.

`action_sfx_suggested` was the literal string `"No"` on every row it would have produced. It
now carries the measurement the handoff says it is derived from - the count of step 1.04's
energy peaks inside the block's own source range - and not a verdict. Whether a moment earns a
sound is the model's call (section 10.5); a pre-computed "Yes" is the bridge voting on it. A
block with no source clip reads `not measured (no source clip)` rather than as a measured zero.
The handoff's own wording, "suggestion", is now narrower than what the column carries; rewording
it is the captain's call and the prompt files are reserved.

Rebuilt off snapshot `001-2026-08-26T1058Z`, the fourteen rows the run should have had:

```
sfx_candidates_toon: |
  [14]{segment_id,text,action_sfx_suggested}
  hook	i can feel the silent judgment of the people behind me.	0 audio transients
  1	The breath after the hook and the first B-roll of the video. The walk in - pa...	not measured (no source clip)
  2	today is march 25th, 2026.	3 audio transients
  ...
  13	i almost didn't do this again i've been literally this week i've quit every s...	9 audio transients
```

The hook is the one speech block on which nothing was measured, and it is a real
discrimination rather than an artefact: clip_011's energy peaks start at 7.833 s and the hook
plays 0.836-3.234 s.

#### The step was also handed its own empty output

The same bridge emitted `sfx_spec: {"sfx_list": [], "fairlight_preset": "default"}`, and a
pre-bridge key is restored past the projection by name, so it reached the prompt on every run.
It answered nothing the model was asked and read as a plan that had already decided to place
no sounds. Deleted; the post-bridge writes the real `sfx_spec` after the model answers.

#### Why the guard reports and does not fail

Two occurrences found weeks later by an audit is the actual defect, so
`library/tools/empty_table_guard.py` reads the context `present_llm_step` is about to send and
names the top-level keys arriving with no rows, marking the ones the prompt mentions. It is a
report: an empty collection can be the correct answer, and a gate that fails correct output is
not coverage (section 10.4).

Measured on 001's ten archived requests, this is why it scans TOP-LEVEL keys only:

| depth | `[0]{...}` or `[]` markers | what they are |
|---|---:|---|
| any | 42 | `violations: []`, `gaps: []`, `errors: []` - legitimately empty fields of a passing check |
| top level | 2 | `sfx_candidates_toon` (named in the prompt) and `render`'s `visual_qa` (not named) |

Reporting all 42 buries the one that matters.

**What it misses**, stated so nobody reads it as more coverage than it is: rows that are
present but HOLLOW - `cuts_toon` had thirteen of them, all `unknown-to-unknown`, and this guard
would have said nothing; a table described in prose without its key name; a table nested inside
a routed document; and a table projected away entirely, which arrives as no key at all.
**What it falsely flags**: a collection that is legitimately empty and whose key the prompt
happens to name - which is why it prints a count and never a verdict.

#### Two more sightings, not fixed here

The same sweep over the other nine requests found two, both reported rather than changed:

* **`plan_vfx`'s `vfx_candidates_toon` has eleven rows and every one is hollow** - `text` empty
  on all eleven, `vfx_suggested` the literal `"No"` on all eleven - and its bridge additionally
  fabricates `enhancement_spec.visual_effects = [{"effect_type": "color_wash", "intensity":
  0.5}]` on the first segment and hands it to the model as its own prior plan. 001's prompt
  carried `color_wash,0.5,hook`. That is a creative value invented where no step ran
  (section 10.5), and it survived the #192 and #212 audits.
* **`speech_sequence`'s two tables are keyed in two incompatible vocabularies in one prompt**:
  `transcripts_toon` by `clip_id` (`clip_008`), `topics_toon` by file stem (`IMG_1806_v3`).
  Nothing joins them. `library/tools/semantic_index.py` is the join that exists for exactly
  this.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
The guard catches the zero-row half of the family only; rows that are PRESENT but hollow go past it,
which is what `cuts_toon` was.

The same reader serves a live run and a finished one because the `llm_requests/<step>.json` archive
keeps the prompt and the context exactly as the run sent them.

`sfx_candidates_toon` was built from an `a_roll_assignments` no edge carries, so it had no rows at
all - and its rows would have been `unknown` even routed, because A-roll entries are keyed
`spine_block_position` and not `segment_id`.

### the-crop-was-narrower-than-the-face

**2026-08-26.** Two consecutive full runs of project 001 - PRs #192 and #201 - independently
named the same defect as the biggest picture problem in the finished video, and neither run
caused it. At **t=44.0 s of `exports/Pipeline_Edit.mp4`**, the emotional floor of the edit, the
frame shows roughly half a face, soft from the upscale, on a source frame that is a clean
landscape close-up with his whole head and a full sunset behind him.

**What the numbers say.** The clip is `IMG_1818.MOV`, stored 1920x1080 with rotation 0. At
t=44.0 s the timeline is 2.257 s into the V1 clip `speech_15_seg0`, which is source
20.646 s. Running the pipeline's own cascade at its own sample size on that source frame:

```
Haar frontal face box   x 285..1008 of 1920   (37.7% of the source width)
conform crop window     x 450..1056 of 1920   (31.6%, fill_zoom 3.1605)
```

The crop's left edge sits **165 px inside** the face box. The `framing_pan_x` of 366.59 px in
the manifest is correct - it aims at the clip's measured subject centre of 0.3926, and the
median face centre over the clip's range measures 0.3926 - so the pan was computed right,
applied right, and could not have helped. **The window was smaller than the thing it was
aiming at.**

**Why nothing caught it.** `compute_face_presence` receives (x, y, w, h) from the cascade and
recorded only `(x + w/2) / sample_w`. The size was measured and thrown away, so no consumer
could ask whether the crop was wide enough. Every gate passed: `measure_frame_occupancy` asks
whether the picture fills the frame and it does, completely.

**The arithmetic is closed.** A width-limited fill keeps exactly `1 / zoom` of the source
width. Holding a 37.7% face box with 15% of its own width clear on each side needs 49.0% of the
width, which is zoom 2.04 - and at zoom 2.04 the source's full height maps to 1241 of 1920
rows, so 35% of the frame is bars. There is no zoom that both fills the frame and holds this
subject, and there was never going to be: the source is already a tight selfie.

So the missing picture is synthesised rather than the subject sacrificed. `_conform_fields`
emits a `framing_backdrop` - the source scaled to 0.6463 and centred on the subject, over the
same frame at scale 1.0, blurred - and `fx.subject_backdrop` draws it in the clip's Fusion comp,
upstream of Resolve's own transform. Frame occupancy stays 100%, the geometry stays constant
across the video, and the face is whole.

**Seen on a frame, twice.** The before and after were both rendered through DaVinci Resolve
21 on a scratch timeline built from this manifest's own values (probe timelines created and
deleted; the captain's project untouched), and reproduced independently with ffmpeg from the
same `_conform_fields` output. The comp imports, `GetFusionCompNameList` reports it, and the
delivered frame shows the whole head, both ears, the cap and the sunset.

**Two things the probe established that are not obvious.**

* A `framing_pan_x` applied *on top of* a backdrop comp slides the whole composition sideways
  and reintroduces the crop. That is why the backdrop route emits no pan at all.
* `TimelineItem.DeleteFusionCompByName` returned **False** for a comp that
  `GetFusionCompNameList` was reporting, and the comp stayed on the clip - the first render of
  the "before" case came out with the backdrop still applied. Judge it by its return value like
  every other Resolve call (§5).

**Why the render-side gate is the weaker half.** The obvious check - Haar on the master, fail a
box touching the frame edge - does not catch this, and the measurement says so. Over 118
sampled frames of 001's shipped master the cascade found **9** subject-sized faces and **none**
of them touched an edge, on a video whose A-roll is cut through the middle of the speaker's
face throughout: a face cropped that hard, softened by a 1.78x upscale and overlaid with
caption cards, stops being detectable at all. `measure_face_intact` is kept as a backstop for
the case where the plan was right and the render was not, and the verdict is carried by
`manifest_validator`'s P8, which asks the same question of the plan where it has an exact
answer and asks it before a 40-minute render rather than after one.

**What this was.** Not a missing default and not a working feature failing to reach these
clips: subject-aware framing reached them and did its job. It was a **missing measurement** -
the subject's size - and therefore a missing capability, because a conform that cannot fit the
subject had nowhere to go.

Related: [the-squashed-face-frame](#the-squashed-face-frame) fixed the sampling that made
`face_center_x` available on this footage at all; [the-letterbox-default](#the-letterbox-default)
is why the frame fills in the first place.

**Moved out of the rule (AGENTS.md 10.3) on 2026-08-29.**
`render_qa.measure_face_intact` is deliberately the weaker half: a face cropped hard enough stops
being detectable at all, which is why 001's own master yielded nine detections in 118 samples and none
touching an edge.

### the-transition-planner-read-the-raw-document

**2026-08-26.** The context sweep across the other ten LLM steps, measured with the replay
bench against snapshot `001-2026-08-26T1058Z`.

`plan_transitions` was the largest prompt in the pipeline by a factor of two - 161,958 bytes -
and 113,446 of them were one section: `semantic_analysis`, the raw vision document, with all
fifteen columns.

    [17]{actions,analysis,analysis_metadata,assessment,blocks,camera,clip_id,
         duration_s,file_path,fps,objects,resolution,scene,transcript,vision_schema_version}

Every other step that wants vision declares `semantic_analysis_documents` and names the columns
it reads. `plan_transitions` named the input and nothing under it, so it got `file_path`, `fps`,
`resolution`, `vision_schema_version` and `analysis_metadata` as well - none of which a
transition planner can act on.

**Its handoff names ONE source for what is either side of a cut**, and it is not that document:

> The `cuts_toon` table provides a summarized list of cut points... `outgoing_footage`: The mood
> and tags of the clip ending at the cut. `incoming_footage`: ...

That table was empty. Reconstructed at HEAD, all thirteen rows read:

    1	2.40	unknown-to-unknown	No	none	none

Two key-name failures of the class section 10.1 calls dominant, in the same six lines of bridge:

- the lookup was built as `{doc["clip_id"]: doc}`, and step 1.03 keys its documents by the file
  STEM (`IMG_1816`) while the spine speaks catalog ids (`clip_011`). Nothing ever matched, so
  every footage cell read `none`. This is the same join that emptied the B-roll candidate table,
  and `library/tools/semantic_index.build_semantic_lookup` already existed to do it;
- the cut classification read `block.get("type")`, and the spine's own key is `block_type`
  (`library/tools/spine_contract.py`), so every cut was `unknown-to-unknown`.

A third gap sat behind them: a cutaway block carries no `clip_id` at all - the spine leaves the
slot and step 3.02 fills it - so a cut INTO a cutaway, the cut most likely to want a transition,
was unattributable even once the join worked.

**What was sent, after.** The document is still ROUTED to the step - `bridge.py` is handed the
unprojected inputs and is the reader that needs the whole thing - and `clip_catalog` is routed
beside it, because the catalog is what the join needs. Neither is in `context_fields`, so
neither reaches the prompt. What reaches the prompt is `cuts_toon`, carrying framing, camera
movement, stability and the assessed keywords for the shot either side of every cut.

**No mood is reported, and that is deliberate.** The handoff asks for "the mood and tags" and the
v3 vision pass measures no mood and no energy (section 10.1). The old table printed `Mood: ` on
every row - a header over nothing. Filling it would be inventing taste no step produced
(section 10.5), so the column carries what was measured and the mood is absent rather than
fabricated.

Measured, same snapshot, same tokenizer: **161,958 B -> 53,952 B, a 67% reduction**, and the
embedded-JSON share of that context falls from 73% to 13%.

`tests/test_plan_transitions_context.py`.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
A per-clip summary table built without `semantic_index`'s file-stem-to-`clip_XXX` join comes out full of
`none` and says nothing; the transition planner shipped one for months.

The other half of the same rule is what the raw document costs. A manifest that declares
`semantic_analysis`/`semantic_analysis_documents` with no sub-paths gets all fifteen columns -
`file_path`, `fps`, `resolution`, `vision_schema_version` and `analysis_metadata` included - and that
was **113 KB of `plan_transitions`' 162 KB**.

### seventeen-copies-of-an-error-are-not-a-measurement

**2026-08-26.** Same sweep.

`creative_direction`'s prompt carried seventeen records that each said only:

    prosody:
      method:
      error: parselmouth not installed

4.2 KB of identical error text, in a section the manifest declares a REQUIRED input. One arm of
the A/B on that context said, unprompted: *"There is no actual prosody data to evaluate... I had
to completely ignore this section."*

`context_fields` could not fix it. It is an allow-list of dot PATHS, and these records have
exactly the right paths - `prosody_analysis.profiles` is where a measurement would live too.
Selecting by name cannot tell a measurement from a record of its absence.

So the selection is done by VALUE, by the one predicate that already answers that question:
`profile_defect`, which step 1.05 refuses to write a hollow profile with. It moved from that
step to `library/tools/prosody_profile.py` so the write-time and read-time answers cannot
diverge, and `view:prosody` in `library/tools/context_views.py` is the reader.

**The absence is reported, not hidden.** Seventeen error records became one line:

    prosody:
      not_measured: 17 of 17 clip(s) have no prosody measurement: parselmouth not installed

A model told plainly that nothing was measured knows not to reason about it. Silence would read
as "no prosody worth mentioning", which is a different and false claim. Where prosody IS
measured the profiles are passed through unchanged, so the step is not blinded - and whether to
measure prosody at all stays a separate question.

Measured: **30,835 B -> 26,668 B**. `tests/test_prosody_view.py`.

### embedded-json-is-where-the-content-is

**2026-08-26.** Same sweep. Both A/B arms flagged, without being asked, that nested objects
arrive as JSON strings inside TOON cells - one said it *"increases the likelihood of an LLM
incorrectly parsing the schema"*. `json_to_toon`'s own comment admits it: *"TOON tabular doesn't
naturally support nested objects in cells."* It was 51-76% of every large context.

Two routes were on the table and both were measured before choosing.

**Route A - teach the serialiser nesting.** A uniform-dict list with any nested value stops being
a table and becomes indexed blocks of real TOON. Prototyped in `json_to_toon` and measured
end-to-end on all eleven contexts:

| step | tabular + JSON | nested TOON | delta |
|---|---:|---:|---:|
| creative_direction | 26,668 | 31,491 | +4,823 |
| speech_sequence | 77,715 | 76,268 | -1,447 |
| mesh_spine | 29,027 | 34,021 | +4,994 |
| select_broll | 72,804 | 95,753 | +22,949 |
| review_rough_cut | 32,965 | 41,006 | +8,041 |
| plan_transitions | 53,952 | 60,796 | +6,844 |
| plan_vfx | 38,063 | 46,517 | +8,454 |
| plan_sfx | 50,143 | 59,683 | +9,540 |
| render | 37,463 | 48,722 | +11,259 |
| validate | 34,569 | 45,828 | +11,259 |
| **all eleven** | **460,896** | **547,612** | **+86,716 (+19%)** |

The embedded JSON goes to zero everywhere and the contexts get 19% BIGGER. Demoting the table
costs more than the JSON quoting saved: a table names its keys once in a header, and indexed
blocks repeat every key on every record. **Rejected on the measurement, not on caution.**

**Route B - a view that flattens before serialising.** Prototyped as a lossless re-encoding of
`semantic_analysis_documents`: each nested column hoisted into its own flat TOON table, keyed
back by `clip_id`. Nothing dropped.

| step | section now | hoisted | delta |
|---|---:|---:|---:|
| creative_direction | 14,982 | 11,763 | -3,219 |
| mesh_spine | 8,275 | 7,059 | -1,216 |
| plan_sfx | 12,207 | 10,303 | -1,904 |
| plan_vfx | 9,878 | 7,729 | -2,149 |
| select_broll | 40,130 | 26,437 | -13,693 |
| speech_sequence | 49,166 | 37,446 | -11,720 |
| **six steps** | **134,638** | **100,737** | **-33,901 (-25%)** |

Route B wins, and the "one view per shape" objection is answered by measurement rather than by
argument: this ONE shape carries 120,512 of the 158,062 embedded-JSON bytes left across the
eleven steps - 76% of them - and it is the largest section of five of the six largest
contexts.

**Nothing parses the current shape back out.** `toon_to_json` has no caller outside
`tests/test_toon_serializer.py`; the model reads the characters. The format is safe to change.

**Why route B was not landed in this pass.** Two obstructions, both above a mechanical fix:

1. Views are built from the UNPROJECTED input (`project_fields` merges them alongside the keep
   paths), so a footage view cannot honour the six different per-step column allow-lists that
   `semantic_analysis_documents.*` expresses today. Landing it means either rewriting six
   allow-lists as `-` drop lists or changing when a view is built.
2. It reshapes a section three handoffs name and one describes column by column -
   `step_3_02_select_broll/handoff.md` tells the model to read "`scene[]` segments with
   start/end bounds, `camera[]` segments... `objects[]` with the time ranges they appear in".
   The handoff files are the captain's.

**And the sense of proportion the numbers give.** The ceiling for any pure re-encoding is the
format tax - the punctuation, repeated keys and escaping inside those cells - which measures 36%
to 71% of the embedded bytes, around 70 KB of the 461 KB sweep, and route A shows a re-encoding
can as easily cost more than it recovers. Routing `plan_transitions` at its own handoff saved
108 KB on one step. **The embedded JSON is mostly where the content is, not a
tax on carrying it**; the large savings are in deciding what a step reads, and the format change
is worth doing for legibility rather than for size.

## unwired-steps-need-a-reason

**2026-08-27 output audit, issue #187.** Steps 1.06 (`object_segmentation`, SAM 2.1) and 1.07
(`ocr_extraction`, EasyOCR) were added in commits 3c4dd10 and 95affc1 (2026-08-08) with working
tool code, manifests, and tests - but neither commit touched `dag.json`. The pipeline ran five
preflight steps while presenting as seven, and nobody noticed until the Twelve Labs research
grounded itself in the ingest.

The output audit found a second symptom: `objects[].readable_text` was mostly `null` in the
semantic analysis output. Investigation showed this is the VLM's field - step 1.03 prompts for it
directly - not step 1.07's. Step 1.07 writes to a separate key (`ocr_extraction`), so wiring OCR
would not fill `readable_text`.

**Correction, 2026-08-28.** The audit recorded that field as null **138 times, for every object on
every clip**, and #187, #224 and #245 all repeated it as "the local model provably cannot read
on-screen text". That is wrong, and it was the stated basis for wiring the OCR step. Re-counted
off the same 2026-08-26 artifacts
(`<001>/pipeline_output/steps/1_03_semantic_analysis/clip_profile_*_v3.json`, 17 files):

    objects total: 159    readable_text filled: 10    null: 149

The ten are legible and correct - `Chattahoochee Ave NW` on a road sign (IMG_1810),
`SCUFFLEWA BREWING CO` on a brewery sign (IMG_1820), `L PARK`, `P.P.S.U.`, `RED`, `SPECIALS`,
`SPARK`, `MARKET & T`, `Credit`, and one mangled street sign. So the vision model reads on-screen
text **sparsely and imperfectly, not never**, and the argument for the OCR step is a coverage
argument (10 of 159), not an impossibility one. The reason recorded in
`library/tools/run_scope.DESELECTED_BY_DEFAULT` states it that way.

**The structural fix**: `StepDir.__post_init__` in `project_layout.py` now rejects `wired=False`
without an `unwired_reason`. `tests/test_step_dag_coverage.py` scans every step directory on disk
and fails if any has no DAG node and no documented unwired declaration. A step that exists but
never runs is no longer silent.

**EasyOCR measurement on 001** (17 clips, 807s footage, 2026-08-28): 445s wall-clock at 1fps
sampling. 367 tracked text detections across 15/17 clips; 83 above 0.5 confidence. Real text
found: street signs ("Chattahoochee", "PARK", "Tetta Blvd NW"), dashboard navigation ("Google",
route numbers), and storefronts ("THROW AXES", "VALIDATE PARKING"). Signal-to-noise is 23% -
most low-confidence detections are noise from foliage and textures. The real detections are text
the VLM returned null for - on 149 of its 159 objects, not on all of them (see the correction
above).

**The ruling, #245, 2026-08-28.** The captain: "i want you to finish flushing it out and then
simply deselect it from the pipeline for now." The step is therefore WIRED - node
`ocr_extraction`, fed `raw_footage_files` by `scan` and an optional `temporal_index` by step
1.04 - and listed in `run_scope.DESELECTED_BY_DEFAULT`, which is why a default run does not pay
its 445 seconds. `--with ocr_extraction` turns it on. It is safe to leave out because no edge
leaves it: nothing consumes `ocr_extraction`, so no dependency is stranded, and
`tests/test_run_scope.py` asserts that rather than assuming it.

**Moved out of the rules (AGENTS.md 3) on 2026-08-29.**
`object_segmentation` (1.06) was added in commit 3c4dd10 (2026-08-08) without touching `dag.json`, and
nothing consumes masks.

The `readable_text` measurement, in full: on 001's 2026-08-26 run the local VLM (`gemma-4-12b-it-4bit`)
filled **10 of 159 objects** (`Chattahoochee Ave NW`, `SCUFFLEWA BREWING CO`, eight more) and left 149
null. A recorded claim that it "provably cannot" read text was wrong.

**EasyOCR measurement on 001** (17 clips, 807s footage): 445s wall-clock, 367 tracked texts across
15/17 clips, 83 above 0.5 confidence. Real text found includes street signs ("Chattahoochee", "PARK",
"Tetta Blvd NW"), dashboard navigation ("Google", route numbers), and storefronts ("THROW AXES",
"VALIDATE PARKING"). Signal-to-noise is **23%** - most low-confidence detections are noise from
foliage and textures. The real detections are text the VLM returned null for, on 149 of its 159
objects. That is the evidence behind wiring the step and deselecting it by default (#245).

## sam-2-1-was-asked-for-a-config-that-does-not-exist

**2026-08-28, issue #162.** `library/tools/analysis/object_segmentation.py` had been in the
repository since commit 3c4dd10 (2026-08-08) with `sam2>=1.0.0` in `requirements.txt`, and had
never once run. Every attempt died before the model existed:

    hydra.errors.MissingConfigException: Cannot find primary config
    'sam2.1_hiera_small.yaml'. Check that it's in your config search path.

    Config search path:
        provider=hydra, path=pkg://hydra.conf
        provider=main, path=pkg://sam2
        provider=schema, path=structured://

This was recorded on the issue as "a hydra configuration problem in the environment, not a
pipeline defect". It is neither environmental nor a packaging gap in `sam2`. **The string in our
code named a file that exists under no name.** The installed package ships the config:

    site-packages/sam2/configs/sam2.1/sam2.1_hiera_s.yaml

and `sam2.build_sam.HF_MODEL_ID_TO_FILENAMES` maps `facebook/sam2.1-hiera-small` to exactly that
path plus `sam2.1_hiera_small.pt`. Two independent mistakes were folded into one string:

- **The name.** The CONFIG is named for the size, `_s`. The CHECKPOINT is named for the word,
  `_small`. `sam2.1_hiera_small.yaml` is the checkpoint's stem wearing the config's extension.
- **The path.** `sam2/__init__.py` calls `initialize_config_module("sam2")`, which makes the
  package the search root, so any config inside it must be addressed from `configs/`. Even
  spelled `sam2.1_hiera_s.yaml` the bare name would not have resolved.

The checkpoint half was wrong in the same style - `ckpt = "sam2.1_hiera_small.pt"` is a bare
relative path resolved against the process CWD, and nothing in this repository ever downloaded
it. Both halves are now answered by `sam2`'s own table, by naming the MODEL:
`build_sam2_video_predictor_hf(SAM2_MODEL_ID)`. Loading takes 19.0s including the first
download. No config was vendored and no model was substituted.

**Two further defects were in the same function and would have bitten on the first real run.**
The predictor was used AFTER its `managed_model` block closed - `add_new_mask` and
`propagate_in_video` sat outside the `with`, and `unload_model` moves the model to the CPU while
`inference_state` still holds MPS tensors. And `avg_area_ratio` was summed over BOUNDING BOX
areas on a dataclass whose whole product is a mask, which overstates a limbed subject several
times over.

### MPS returns worse masks and says nothing

Once it ran, MPS was measured against the CPU on 001's clip_001 first frame, same model, same
image, same seed prompts:

| grid | device | masks kept | masks before filtering | predicted IoU min/median/max | stability min/median/max |
|---|---|---|---|---|---|
| 16x16 | MPS | 1 | 351 | 0.002 / **0.092** / 0.965 | 0.000 / 0.607 / 0.968 |
| 16x16 | CPU | 5 | 175 | 0.000 / **0.333** / 0.974 | 0.000 / 0.718 / 0.982 |
| 32x32 | MPS | 1 | - | - | - |
| 32x32 | CPU | 7 | - | - | - |

The maxima agree to within 1%, so a prompt landing squarely on an object still scores on MPS.
The MEDIAN over the same prompt grid is 3.6x lower, and the generator's default
`pred_iou_thresh=0.8` / `stability_score_thresh=0.95` then discard nearly everything. Nothing
raises. The caller gets a short list and has no way to tell a plain frame from a degraded
backend - the same shape as a Resolve call that returns a plausible value without doing anything.

Propagation is NOT degraded. Handed identical seed masks on clip_008 and asked to track them:

    MPS  1.57 s/frame     per-object IoU against CPU, on every frame the track holds: 0.88 - 0.98
    CPU  5.98 s/frame     (reference)

They diverge only on which frame each abandons an object, which is past the point the track is
usable. So `GENERATOR_DEVICE = "cpu"` and `PROPAGATION_DEVICE = "mps"`: the generator runs once
per clip on one frame for a bounded 15-35s, and the 3.8x is kept where it is paid per frame.

### Denser sampling does not rescue a lost track

clip_008 (IMG_1813, 9.1s), the same clip three ways:

| sample_fps | frames | wall | per frame | largest object | small objects last seen |
|---|---|---|---|---|---|
| 2.0 | 18 | 75.2s | 4.18s | held 12/18, to 8.5s | 2.0 - 5.5s |
| 6.0 | 54 | 202.2s | 3.74s | held 18/54, to 5.7s | 0.2 - 4.3s |
| 15.0 | 136 | 528.0s | 3.88s | held 94/136, to 9.0s | 2.3 - 4.5s |

Cost per frame is flat, so cost is linear in `sample_fps` - 15 fps is 7x the bill of 2 fps for
the same clip. And the small tracks die at 2 to 4.5 seconds at EVERY rate. Denser sampling buys
temporal resolution on the tracks that survive and does not extend the ones that do not: these
objects are lost to the footage, not to the gap between samples.

## a-template-nobody-chose

The captain, 2026-08-28: *"also why do we have 16 hardcoded values? i did not choose that most
likely so where is it coming from, the LLM should be able to make all these creative decisions
based on the project, it makes no sense to have these hardcoded values"*

`data/vep-creative-decision-degradation/report.md` §6 counted sixteen creative values still in the
path on project 001's run of record. This is what each of them was, where it went, and the route
taken. **The pipeline's own model flagged the cause at step 2.01**, unprompted:

> *"`project.yaml` names no brand template, so this is the fallback `default_brand`, whose values
> are a default nobody chose for this project rather than a brand decision. I would not overrule a
> template the captain had actually selected."*

### What a project that names no brand template now gets

**Nothing.** `resolve_project_template("")` answered `library/templates/default_brand.yaml`; it now
answers `brand_registry.no_brand_template()`, every creative slot empty, and `ABSENT_SLOT_READINGS`
records what each consumer does with that. The readings were already written and are the absence of
decoration, not a substitute taste - no house look is exposure normalisation only, an empty
transition allow-list permits the whole drawable vocabulary, an absent `delivery_format` gets the
product enumeration's own vertical default. `default_brand.yaml` stays on disk as a template a
project may NAME, which is what makes its values a decision again.

`describe_brand_absence()` is printed once per run and names the four consequences that change the
finished video, because "no brand template" is otherwise a sentence a reader passes over.

### The sixteen, with the route taken

Numbering follows §6 of the report. "on 001" is measured against its 2026-08-26 run of record.

| # | value | on 001 | route |
|---|---|---|---|
| 1 | an empty required list is a defect (`run_pipeline.py`) | rejected the correct VFX plan 3x | **already fixed** at HEAD by #275 - a step declares `may_be_empty` |
| 2 | high energy ⇒ transitions < 500 ms, suggested 10 frames (5.03) | could not fire; energy read `moderate` | **removed** - a threshold the step chose, on the one field the compiler rewrites |
| 3 | calm energy ⇒ dissolves ≥ 1000 ms, suggested 30 frames (5.03) | could not fire | **removed**, same reason |
| 4 | high energy ⇒ ≥ 10 SFX per minute (5.03) | could not fire | **removed** - a creative floor in code (AGENTS.md 10.5) |
| 5 | calm energy ⇒ ≤ 15 SFX per minute (5.03) | could not fire | **removed**, same reason |
| 6 | colour-mood word lists (5.03) | never fired | **already removed** at HEAD by #271 |
| 7 | cohesion score 100, penalties 3/5/10, apply below 70 | score 90, so nothing applied | **already removed** at HEAD by #271 |
| 8 | house-look strengths: `glow_gain 0.12`, `glow_threshold 0.78`, `glow_size 3.5`, `grain_power 0.18`, `grain_size 1.5`, `vignette_blend 0.16`, `vignette_soft 0.35`, `contrast 0.1`, `saturation 1.1`, and the slope/offset/power triples | on all 17 per-clip comps | Was **PARKED** here. The park was lifted on 2026-08-28 and every one of them is **REMOVED** - see [there-is-no-house-look](#there-is-no-house-look) |
| 9 | `style.energy_profile: "high"` | reached 2.01's constraints; the model overruled it in writing | **removed from the absent path.** A project that names no template sends no brand constraints at all |
| 10 | `effect.vfx_intensity: 0.5` | was the entire 41-byte constraints block at 4.03 | **removed from the absent path**, same mechanism |
| 11 | `content.music_genre: ["electronic", "upbeat"]` | no reader | **removed** from `default_brand.yaml`. The slot survives because four templates declare it; it reaches nothing, and step 2.04's frozen handoff names `brand_content.music_genre` while no manifest routes `brand_content` to 2.04 - **reported, not wired** |
| 12 | `effect.sfx_density: "dense"` | no reader since `scale_sfx_density` was deleted | **removed** from `default_brand.yaml`; the schema default is now `""` |
| 13 | `effect.caption_case: "lowercase"` | all 45 caption cards lowercased | **PARKED, and made explicit.** It is the one creative value that survives an absent template, and it is recorded as an exception in `ABSENT_SLOT_READINGS` and in `EffectSlots` rather than left implicit |
| 14 | `style.typography: Montserrat / 160 / 800` | 45 cards, median 0.781 s each | **PARKED - untouched.** Measured: absence and `default_brand` resolve to the SAME subtitle style, because `default_subtitles` is 160/800 too, so removing the fallback changes no caption pixel |
| 15 | `effect.transition_duration_ms: 200-500` | both drawn transitions held for 500 ms | **given to the model.** A range is a permission and bounds the plan; the plan's own `duration_feel` is the length |
| 16 | `content.target_duration_seconds: 30-60` | reached no step | **replaced by the project's own declaration.** See below |

Two values the report did not count, found while taking those routes and fixed with them:

| value | where | route |
|---|---|---|
| `transition_duration_ms` defaults to **500 ms** when nothing declares one | `transition_selector._resolve_duration_ms` | **removed.** A drawn transition with no declared length is dropped with the reason |
| the duration zone defaults to **54 / 60 / 66 s** | `duration_targets.get_target_duration_zone` | **removed.** It returns None and each gate says it did not check |
| `target_duration_seconds: 60`, `style_preset`, `subtitle_style` invented for a project declaring none | `step_1_01_scan_project/step.py` | **removed.** `project_declared_config` emits only what the project declares |

### The measured before and after, on 001's real state

Read out of 001's own `pipeline_data.json` and `pipeline_output/llm_responses/`, with the real
`gather_step_inputs`. Nothing was run against the project and nothing in it was written.

**The brand constraints in the prompts.** Before, on a project that had chosen nothing:

```
creative_direction: "\nBrand Constraints:\n- Style: {\"color_palette\": [...], \"series_look\":
                     \"pmk_default\", \"typography\": {...160...}, \"energy_profile\": \"high\"}\n
                     - Content Rules: {... \"music_genre\": [\"electronic\", \"upbeat\"],
                     \"target_duration_seconds\": {\"min\": 30, \"max\": 60}}\n"
plan_transitions:   "\nBrand Constraints:\n- Transition Types: [...seven...]\n
                     - Transition Duration MS: {'min': 200, 'max': 500}\n"
plan_vfx:           "\nBrand Constraints:\n- VFX Intensity: 0.5\n"
```

After: `''`, `''`, `''`.

**The two drawn transitions.** The model's own plan, and its rationale for the second: *"'medium'
(333ms) because this is the single transition..."*

| cut | the plan's word | before | after |
|---|---|---|---|
| position 8 | `duration_feel: "quick"` | 15 frames (500 ms) | **6 frames (200 ms)** |
| position 13 | `duration_feel: "medium"` | 15 frames (500 ms) | **10 frames (333 ms)** |

Both were 500 ms because `_resolve_duration_ms` answered `max` of a range in a template 001 never
selected. The thirteen hard and jump cuts are unchanged at 0 frames, and the spec still has fifteen
entries.

**The duration zone.** 001's `project.yaml` declares `target_duration_seconds: 60`. Nothing carried
it: no DAG edge maps `project_config` and nothing wrote `state["project_config"]`, so all four
callers were handed the in-code 54/60/66.

```
before   speech_sequence  project_config=None  ->  (54.0, 60.0, 66.0)   # the constant
         mesh_spine       project_config=None  ->  (54.0, 60.0, 66.0)
         review_rough_cut project_config=None  ->  (54.0, 60.0, 66.0)
         creative_cohesion project_config=None ->  (54.0, 60.0, 66.0)

after    all four         project_config={'target_duration_seconds': 60} -> (54.0, 60.0, 66.0)
```

The numbers are identical and that is the point: the zone 001 was judged against happens to be the
one the captain declared, and until now that was a coincidence. A project declaring 90 seconds was
judged against 60 just the same.

**Step 5.03's output on 001's real inputs**, before (four inert energy checks, an adjustment made of
an arithmetic constant) and after:

```json
{"timeline_duration_seconds": 59.44,
 "measurements": {"declared_target_energy": "building", "transitions_planned": 15,
                  "transitions_drawn": 2, "sfx_events": 2, "sfx_per_minute": 2.0},
 "warnings": ["Engagement not compared: ..."],
 "adjustments": [], "observations": []}
```

**What does not change on 001.** The delivery format stays `[1080, 1920]`, from
`DEFAULT_DELIVERY_FORMAT`, which `default_brand.yaml` only ever restated. The subtitle style
resolves byte-identically with and without the template - `resolve_subtitle_style` differences:
none - because `default_subtitles` carries the same 160/800 the template's typography block did.
Captions stay lowercase.

### The consequence to take to the captain

`default_brand.yaml` declares `series_look: pmk_default`, and 001 inherited it. With absence
declaring nothing, **001's next grade would carry exposure normalisation only** - step 5.01 says so
in its own `look_notes` - instead of `pmk_default`'s CDL and Fusion values on all 17 per-clip comps.

That is the correct behaviour of the rule and it is also a change to the picture layer the captain
has parked, so it is stated rather than worked around. `pmk_default`'s `derived_from` cites the
captain's own `overall_branding_creative_direction.md`, and 001 is a pmk video, so the look is very
likely one the captain WOULD choose - which is exactly the decision this change refuses to make on
their behalf. **The fix is one line in 001's `project.yaml`:** `pipeline: {brand_template: <name>}`.
Nothing here edits it.

**Superseded 2026-08-28.** The captain lifted the park and ruled there is no house look at all
("there are no house glow looks, there are no settled house grain or anything"), so naming a
template is no longer a route back to `pmk_default` - the four looks are gone and no shipped
template declares one. 001's next grade carries no CDL and no Fusion look, and no exposure
normalisation either. See [there-is-no-house-look](#there-is-no-house-look).

### Why the cohesion thresholds were removed rather than re-tuned

Items 2-5 are four numbers a step picked. Items 4 and 5 are creative floors in the plainest sense of
AGENTS.md 10.5 - the creative direction decides how many sounds a piece gets - and they survived the
ruling that deleted `scale_sfx_density` and step 4.02's `min_trans` only because
`tests/test_no_creative_floors.py` reads the planning steps and 5.03 is not one of them. Items 2 and
3 reached the picture: `transition_spec.duration_frames` is the whole of
`cohesion_scope.ACTIONABLE_AT_COHESION`, so `suggested_value: 10` was a number nobody chose landing
on a transition an editor had timed.

There is nothing to derive a replacement from. A creative direction declares `target_energy` in
prose and no pace, no duration and no density, so any threshold there would be invented - and 5.03
is a deterministic step with no handoff, so the decision cannot be handed to the model without
authoring a thirteenth prompt. The step reports the counts instead and judges none of them, which is
the shape `render_qa`'s chroma and mix checks already use.

### The cost, stated: step 5.03 is now a pure observer

`ACTIONABLE_AT_COHESION` has an applier and no producer. The single `proposals.append` left in the
step targets `("speech_sequence", "segment_order")`, which is in `OWNED_UPSTREAM`, so `adjustments`
is empty **for every input at every energy** - not only at the `moderate` 001 declares. The step
changes nothing where it runs; it reports.

**This answers issue #272**, which asked whether the actionable set was too narrow for a
moderate-energy edit or whether such an edit genuinely has nothing to correct. Neither, as posed:
the set had no legitimate producer at ANY energy, because the four thresholds feeding it were
numbers the step chose. #272 was right that widening the set "needs a value nobody has chosen"; the
same objection applies to the values that were already there, so they went instead.

`test_the_actionable_finding_fires_on_001s_real_transition_spec` was deleted with its subject - it
supplied `target_energy: "high"` as its one non-real value precisely to reach a check that no real
001 input could. What replaced it is `test_001s_real_transition_spec_is_counted_and_left_alone`,
against the same fifteen real entries, plus `test_the_step_is_a_pure_observer`, which reads the
step's own source with the AST and fails if any proposal targets an ACTIONABLE pair again. That
turns "no producer" from a comment into a checked fact, and makes re-opening #272 a test failure
rather than a silent drift.

The two lists and the raise are kept because they are the guard on the NEXT check that gets added,
and `tests/test_cohesion_scope.py` drives the applier with a proposal built in the test so the
branch stays exercised. An always-empty `adjustments` is not a clean bill of health on the edit.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
An empty brand declaration used to resolve to `library/templates/default_brand.yaml`. That file is now
a template a project must NAME; naming it is what makes its values a brand decision.

Nothing populated `project_config` before, so the captain's declared `target_duration_seconds`
governed nothing and **four duration gates ran against a constant**.

## the-sfx-chooser-was-a-word-list

`AGENTS.md` §10.5, "Sound-effect selection is one enumeration".

**The captain, 2026-08-28:** *"for the sfx, we literally have an indexing vector DB of sfx that the
LLM should be choosing from no? im so confused why it seems like there is so much random hardcoding
going on in this pipeline instead of the generalizing that should be going on."*

They are right. The library records, per sound, a `description`, a `source_object`, an `evokes`
array, an `emotional_temperature`, a `works_when` and an `avoid_when`. Nothing in the selection path
read any of it.

### What the model was offered, and what actually chose the file

The prompt offered ten abstract type names. The output schema carried eight. The library could play
seven. Only `available_sfx_types` - 110 bytes, 0.2% of the step's 53,309-byte context - reflected
disk. `foley` and `ambient` were in neither the schema nor the library; `reverse_cymbal` was in the
schema and not the library.

The file behind a chosen name came from `match_sfx_file`, which counted substring hits from eight
hand-written keyword lists over `f"{description} {folder_category} {file}"` and took the highest
count, ties to whichever entry came first. Run against the captain's library on 2026-08-28, every
one of the seven playable types resolved on a score of **one or two**:

| type asked for | file it got | score | the word that matched |
|---|---|---|---|
| `swish` | `whoosh_impact.mp3` (8.04s) | 1 | `whoosh` |
| `swell` | `Alien_racecar.wav` (5.69s) | 1 | `rise`, inside the folder name `Risers` |
| `bass_impact` | `riser_2.mp3` (5.32s) | - | a riser, for an impact |
| `riser` | `Alien_racecar.wav` | - | the same file `swell` got |
| `reverse_cymbal` | nothing | 0 | - |

**48 of the 78 entries carry an empty `description` in `sfx_index.json`**, which is the only text the
matcher read. For `Alien_racecar.wav` the entire searchable string was `'risers alien_racecar.wav'`.
The sound under the most exposed moment of the finished video was picked off nine characters of
folder name.

### The two sounds 001 shipped, against what the library says about them

The video's direction: *"self-deprecating and quietly resolved - vulnerable, wry, awkward in public"*.
A man in a public parking lot, handheld, talking about quitting.

**`sfx_001`, block 8 @ 34.615s.** Under the first of two `defocus` transitions, at the end of a
sixteen-second unbroken shaky selfie, music `prominent`. The plan asked for a `swish` and said
*"deliberately 'subtle' rather than 'low': the music runs prominent through this slot and the sound
must sit under it"*. It got `whoosh_impact.mp3`:

> `evokes`: speed, impact, momentum, force, danger · `emotional_temperature`: cold tense
> `avoid_when`: **"Avoid using this for subtle movements or slow, atmospheric transitions where the
> sharp transient would be jarring."**

The library forbade, in its own words, exactly the use it was picked for.

**`sfx_002`, block 13 @ 46.147s.** A 1.918s pivot slot resolving into the only 8.67 seconds of
planned silence in the video. The plan asked for a `swell` that *"builds across this slot and
resolves at the cut"*. It got `Alien_racecar.wav`:

> `evokes`: speed, sci-fi, acceleration, mechanical power, high-tech · `emotional_temperature`: cold tense
> `description`: *"...a sustained, textured build that **decays into a low-frequency hum**"*
> `avoid_when`: **"Avoid using this in organic, grounded scenes where the heavy synthetic texture
> would break the realism."**

5.69 seconds of file for a 1.918-second slot, decaying where it was asked to resolve, in a piece
whose whole register is organic and grounded. `DURATION_DEFAULTS` declared `swell: 3.0`, so the
manifest asserted a third length none of the other two agreed with.

### What the catalogue offers instead, on the same two moments

Chosen by reading `library_semantic.json` for those two slots, and resolved through the real
post-bridge against 001's frozen state:

| | before | after | why |
|---|---|---|---|
| block 8, 4.19s slot | `whoosh_impact.mp3`, cold tense, 8.04s | `06_Short_Super8_SFX.wav`, **warm tense**, 3.71s | `works_when`: *"establish a nostalgic atmosphere or as an overlay for **home-movie style footage**"* - which is what a handheld parking-lot selfie is. Fits inside the slot. Its `avoid_when` (*"high-fidelity modern action scenes"*) does not describe this piece. |
| block 13, 1.92s slot | `Alien_racecar.wav`, cold tense, 5.69s | `08_Short_Super8_SFX.wav`, **warm tense**, 1.50s | Same source object, so the two sounds read as one texture rather than two unrelated effects - a decision eight type names could not express. `swelling`, so the placement resolves it at an energy peak, which is what the plan asked for and what `Alien_racecar` decays away from. |

The shape of the answer changes, not just the filename: with the library visible, the best sound for
a blur on home-movie footage is not a whoosh at all.

### The measurement behind "the whole library ships"

Measured on the captain's library, 2026-08-28:

    catalogue entries reaching the model : 78
    denominator (sfx_index.json entries) : 78
    withheld (file missing from disk)    : 0
    TOON bytes                           : 44,397
    rows with a description              : 78 · evokes 74 · works_when/avoid_when 74
    rows with a measured duration        : 78 · measured envelope 76

Nothing is shortlisted and nothing is truncated. The alternatives, and why they lost:

- **A shortlist** is the same defect in a new place. Whatever selects it becomes the chooser, which
  is what the word list was.
- **A reference the model reads off disk** makes the step depend on the answering agent having a
  shell. The run of record only came out well because it had one; a model without one would see
  nothing.
- **An embedding search.** The library carries `sfx.faiss` and a per-entry `embedding`, and
  `library/tools/analysis/sfx_query.py` can query them. Retrieval is what you need when you cannot
  show everything. Everything fits, so it was not built.

Column costs, if the size is ever revisited: `description` 15,155 B, `works_when` 8,274 B,
`avoid_when` 8,233 B, `evokes` 4,390 B, `style_tags` 2,948 B (dropped - it overlaps `evokes`),
`source_object` 2,923 B, `emotional_temperature` 758 B. Dropping `works_when` and `avoid_when` saves
16.9 KB and removes the two fields that refuse both of 001's wrong choices.

Step 4.04's context on 001 goes from 45,930 B to 90,395 B, reconstructed by the replay bench off the
frozen snapshot 001-degradation-20260828, against the base that already carries #295's
brief-by-reference and #294's transition edge. The whole delta is the catalogue, less the 110 bytes
of `available_sfx_types` it replaces. The step that exists to choose sounds now spends half its
prompt on the sounds; it used to spend 110 bytes.

### The library's three index files do not agree, and now that is recorded

| | count | the gap |
|---|---|---|
| `sfx_index.json` | 78 | all 78 paths exist on disk |
| `library_semantic.json` | 74 | missing the four under `Generated/` |
| `profiles/*.json` | 76 dicts + one stray copy of the index | missing `alarm_buzz.wav`, `clock_ticking.wav` |

`load_sfx_catalog` reads all three and falls through semantic -> index -> profile for the
description, which is why all 78 rows carry one. Two entries record no duration at all
(`technical.basic` holds a sample rate and nothing else); both are real audio, so the duration is
measured with ffprobe rather than invented, and an entry that still has none reads `unmeasured` and
is refused by name. The stray `profiles/sfx_index.json` is why `_load_profiles` keeps only dicts.

### The routing gap in the same step

`plan_sfx`'s handoff names pairing sounds with transitions as its first purpose, and no DAG edge
carried the transition plan to it. Both sounds in the finished video sit on transitions and both
were placed from an agent's memory of having planned them four steps earlier. From that step's own
reasoning trace:

> *"Pairing SFX with transitions is the first purpose the prompt lists, and the transition plan is
> not routed here. An agent without that memory could not place a sound under a transition, because
> it cannot see the transitions."*

**That edge landed in #294**, on a separate branch, while this one was in flight, and it is the
better shape: the pre-bridge reduces `transition_spec` to a 491-byte `transitions_toon` keyed by the
spine block the cut leads INTO, which is the same identifier `sfx_creative` names, so pairing a
sound with a transition needs no join and the plan's 8 KB of per-cut rationale prose stays out. This
branch carried a duplicate edge routing the raw 11,273-byte spec; it is dropped in favour of #294's,
and `tests/test_sfx_choice_from_the_catalogue.py` holds the result in place from the sound side.

The other two the frozen handoff's State Interaction table names are **not** routed, and #294 gives
the same reasons independently:

- **`vfx_plan`** - no instruction in the handoff asks the model to pair a sound with a visual effect;
  it appears only in that table. Routing an input nothing tells the model to use is the
  `UNCONSUMED_DECLARATIONS` defect.
- **`subtitle_entries`** - the only instruction naming it is `click / tick -> Subtitle appearances`,
  and `click` and `tick` resolved to the same file, so the distinction the prompt drew never existed
  in the library. Card boundaries are already in `timed_spine`, which the step routes. 17,427 B on
  001 to serve a vocabulary that is gone.

### What is left for the captain

`library/steps/step_4_04_plan_sfx/handoff.md` is frozen. Its System Context (line 25) and toolkit
table (lines 28-33) still name `foley`, `ambient` and `reverse_cymbal`. After this change they name
nothing the model can emit - the schema asks for an `sfx_id` out of `sfx_catalog_reference` - but the
table reads as a menu and should be corrected by whoever holds that file.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
What `load_sfx_catalog` merges, field by field: `sfx_index.json` (path, category, measured duration,
envelope, transient offset), `library_semantic.json` (`description`, `source_object`, `evokes`,
`emotional_temperature`, `works_when`, `avoid_when`) and `profiles/*.json`.

The word list it replaced: eight hand-written keyword lists counted substring hits over each entry's
`description` - which is EMPTY on 48 of the captain's 78 entries - and took the highest count.

Step 5.04 used to keyword-match the library a second time, so the sound the model chose and the sound
that played were two separate answers.

A per-type placement constant put `bass_impact: 0.5` in front of a 5.317s file, which is why placement
is keyed on the measured `envelope_shape`.

The whole library is **78 of 78** entries; nothing is shortlisted.

## no-assessment-field-reports-a-default

Rule: AGENTS.md §10.3, "No assessment field reports a default as though it were measured".

### The family, and how it was found

Three separate fixes, each found by noticing one field:

| found | field | what it asserted | fix |
|---|---|---|---|
| #273 | `camera_stability` | the literal `"unknown"` won over `camera[].stability`, which had the answer | read-side fall-through |
| #248 | `usable_ranges` | `[[0, duration]]` beside `usable_ranges_method: "unmeasured"` | producer |
| #301 | `speech_coverage`, `speech_present`, `primary_subject_visible`, `clip_type` | below | producer + read-side |

Each was found one at a time, which is why `tests/test_assessment_reports_no_default_as_measured.py`
computes the whole deterministic assessment with nothing to measure and asserts that NO field holds a
value, rather than naming the four that were known.

### `speech_coverage: 0.0` was a fabricated claim on 17 of 17 clips of 001

`compute_deterministic_assessment` initialised `speech_coverage: 0.0` with
`speech_coverage_method: "temporal_index"` and only overwrote it when `speech_regions` was non-empty.
The function got the same question right one branch above - with no temporal index at all it wrote
`None` / `"unmeasured"`, and its own docstring said why - and the reasoning was simply not applied to
the empty-list case.

Measured on 001's stored `semantic_analysis` (17 documents, 2026-08-17):

```
assessment.speech_coverage == 0.0       17 of 17   (10 of them person_talking_to_camera)
assessment.speech_present  == False     17 of 17
assessment.speech_coverage_method       absent on 17 of 17 - the number stood alone
```

An empty `speech_regions` list cannot be read as silence: `detect_speech_regions` returns `[]` both
when WhisperX ran and heard nothing and when WhisperX raised inside its own `try`, and the two are
indistinguishable from the caller. So the fields start unmeasured and only evidence promotes them:
regions measure presence and coverage, a transcript measures presence alone. `speech_present` is now
`True` or `None` and never `False`.

Nothing acted on the number. Its one code reader is the per-clip summary line in `analyze_clip`,
which already printed `unmeasured` for `None`; step 2.02's manifest routes it to a prompt.

### `primary_subject_visible: []` when the model answered without the key

`analyze_assessment` already carried `None` for a call that produced nothing, with a comment saying
`[]` is a claim - "the subject appears nowhere in this clip". One level in, `result.get(key, [])`
made that same claim whenever the model answered `content_type` and omitted the key. `[]` the model
really returned is kept; an absent key is `None`. On 001, `primary_subject_visible == []` on 4 of 17
and the archive does not record which of the two produced it.

### `clip_type` classified a clip nothing classified

`_derived_clip_type` returned `b_roll` for any `content_type` that is not a talking-head type,
including `"unknown"` - the value `analyze_assessment` writes precisely when the model call failed.
On 001 that costs nothing (`content_type` is real on 17 of 17), but it is the same shape, so an
unknown `content_type` now derives no `clip_type` at all.

### The stale state: what a re-run of 1.03 would and would not fix

001's `semantic_analysis` last ran 2026-08-17 and was NOT re-run for this change. The deterministic
half is a pure function, so what a re-run would now record was computed against the temporal index on
disk today (17 files) without running anything:

```
                              stored (2026-08-17)     a re-run today
speech_present   True                 0/17                12/17
                 False               17/17                 0/17
                 None                 0/17                 5/17
speech_coverage  a number            17/17 (all 0.0)      12/17 (0.08 - 0.93)
                 None                 0/17                 5/17
usable_ranges_method  unmeasured     17/17                 0/17
                      deterministic_v1 0/17               17/17
camera_stability      unknown        17/17                 0/17
```

Per clip, the ranges a re-run would record (temporal index only; the picture-sharpness signal would
narrow them further):

```
clip_002  [[0, 17.103]]   -> [[0.0, 6.633], [7.8, 17.103]]
clip_007  [[0, 139.132]]  -> [[4.574, 139.132]]
clip_008  [[0, 9.068]]    -> [[3.251, 6.761]]
clip_014  [[0, 4.7]]      -> []              nothing in this clip is usable
the other 13              -> unchanged bounds, but MEASURED
```

What a re-run would NOT fix:

- **`transcript` stays `""` on 17 of 17.** `load_transcript_text`'s first strategy reads only
  `<project>/raw/analysis/temporal_index/`, the pre-layout location; `load_temporal_index` was moved
  onto `pipeline_output/steps/1_04_temporal_index/index/` and this function was not. 001 has no
  `raw/analysis/temporal_index` directory at all. It costs nothing for `speech_present`, which now
  reads the regions directly, but the per-window action prompts are still told "(No speech in this
  segment.)" for every window of every clip. Reported, not fixed - fixing it changes what the vision
  model is shown.
- The model half - `content_type`, `primary_subject_visible` - needs the VLM and cannot be projected.
- Nothing downstream of 1.03 is re-decided by a re-run of 1.03 alone.

### The display was the other half, and it works without a re-run

`usable_ranges_summary` returned the "unmeasured" wording only when `usable_ranges` was EMPTY, so a
stale document rendered its range and the contradiction disappeared. Measured on 001's real state
through the real step 3.02 bridge, the `usable_range` column of `broll_candidates_toon`:

```
before   clip_001  '0.0-3.5s'      ... 17 rows of a range, on 17 documents that measured nothing
after    clip_001  'unmeasured - nothing measured this clip'   ... 17 of 17
```

`adapt_semantic_document` likewise REPLACES a stored `usable_portions` when the method says
unmeasured, instead of deferring to it - 001 carries one on 17 of 17.

The four manifests routing `assessment.usable_ranges` raw into a prompt now route
`usable_ranges_method` beside it, and 2.02 routes `speech_coverage_method` beside `speech_coverage`.
An allow-list selects by name and cannot tell a measurement from a default; the method is what tells
them apart, and on 001's stale documents it is present and says `unmeasured` on 17 of 17.

**Moved out of the rule (AGENTS.md 10.3) on 2026-08-29.**
`detect_speech_regions` returning `[]` both when WhisperX ran and heard nothing and when it raised
makes the two indistinguishable downstream. Speech regions measure presence AND coverage; a transcript
measures presence alone; neither measures nothing.

The family was found one field at a time - `camera_stability`, then `usable_ranges`, then these.

## the-card-that-vanished-into-a-log-line

Rule: AGENTS.md §13, "A card the PLAN wrote REFUSES the step, by name".

Step 2.05's post-bridge dropped any `intro_card` / `outro_card` / `end_card` block the plan wrote,
with a line on stderr. The model answering 2.05 on the 2026-08-26 run of 001 found it and named the
consequence:

> "an agent that obeyed the structural rule literally would have written an `intro` block, had it
> dropped, and shipped a video that cuts from the hook straight into the body with no breath - and
> the drop is a log line, not an error, so nothing would have said so."

The plan around a card is written knowing the card is there. Removing the card and keeping the plan
ships an edit designed for a moment it no longer has, and a warning forty minutes into an unattended
`--full-auto` run is read by nobody - the same reasoning that makes `guard_timeline_deletion` refuse
rather than warn (§15). `bookends.assert_no_invented_bookends` now raises `InventedBookendBlock`
naming every offending block, its position and its type, the shape `UnplayableSfxPlan` uses in 4.04.

Nothing legitimate is refused by it: no fixture, template or captured run carries an LLM-written card
block, and a DECLARED card is inserted after the check.

### The contradiction, and where it actually lives

The frozen `handoff.md` is internally consistent at HEAD. Its block-type table (lines 57-60) offers
`intro` and `outro`, and lines 62-70 say plainly that `intro_card` / `outro_card` / `end_card` are
cards, are dropped, and that "an `intro` block is still yours". Line 80's structural rule agrees.

The contradiction was in `library/steps/step_2_05_mesh_spine/manifest.json`,
`interface.llm_outputs[0].description` - which is not frozen, and which `present_llm_step` injects as
the output schema, so the model reads it as the authority. It said:

> block_type ("hook"|"speech"|"transition_slot") ... Do NOT write "intro", "outro" or "end_card"
> blocks: those come from the brand template's content.bookends and the bridge drops any the plan
> invents

Both halves were wrong about the code. The enum omitted `intro` and `outro`, which the handoff's own
table offers and `spine_contract` carries as first-class non-speech types. And the bridge never
dropped `intro` or `outro`: `BOOKEND_BLOCK_TYPES` is `("intro_card", "outro_card", "end_card")`, so
an `intro` block written on the strength of line 80 passed straight through the `else` branch of
`enrich_spine` and became a real pacing beat. The description now names the real vocabulary and the
real refusal.

## a-declaration-a-clip-cannot-honour

**The rule:** AGENTS.md §10.3, "A framing DECLARATION is not a framing DELIVERED", and §10.4 on the
occupancy gate judging per declared framing.

### What the captain asked for

On 2026-08-28, asked whether the frame should fill or letterbox, they answered:

> Make framing a per-clip choice rather than one setting for the video

and, in their note:

> some people might have a preference to zoom in horizontal 31.6x to fill out the screen, **i prefer
> letterboxed**, but then those are things that exist in like a creative brief or some kind of client
> preferences doc

The second half is the part that decides who chooses. See "Preference or craft" below.

### The history

| when | what |
|---|---|
| 2026-08-16 | Q1 answered: **letterbox stays the default** (`docs/RUN_001_END_TO_END.md:171`) |
| 2026-08-20 | **"### 19.1 Framing (Q1) - keep the letterbox. Nothing built."** (`docs/RUN_001_END_TO_END.md:1324`) |
| 2026-08-21 18:58 | commit `de3ed62` / PR **#133** sets `DEFAULT_FRAMING_INTENT = FILL` for **every** project |
| 2026-08-26 | 001 renders with `framing_intent: 1.0` on all 18 placements, every landscape clip punched in 3.1605x showing 31.6% of its width |

001's `project.yaml` declared no `framing_intent`, no `pipeline:` block and no brand template, so it
took the new default. Nobody chose it for this video.

### Preference or craft - where a per-clip framing choice sits

The captain's own framing cuts the question cleanly, and it lands on **preference**:

- **Whether the picture is punched in or inset is a preference**, and they said so - it "exists in
  like a creative brief or some kind of client preferences doc". So it is a DECLARATION, and
  `project.yaml`'s `pipeline.framing_intent` is where 001's lives.
- **What each clip then delivers is neither preference nor craft. It is arithmetic.** 001's eleven
  A-roll placements are landscape 1920x1080 into a 1080x1920 frame and have bars to give; its seven
  cutaways are shot portrait, already cover the frame, and have none. One declaration of `0.0`
  produces exactly the picture the captain described, per clip, with nothing choosing anything.

So no chooser was built, and none should be: a step that picked a framing per clip would be
inventing taste where a measurement already answers (§10.5). What was missing was not a decision -
it was an honest record of one.

### What was actually broken

`_conform_fields` wrote the resolved DECLARATION onto every clip as `framing_intent`, including onto
clips that could not honour it:

```python
if fill_scale <= fit_scale * (1 + 1e-6):
    # Source already matches the target aspect ratio - no conform needed
    return {"needs_conform": False, "framing_intent": resolved_intent}
```

A portrait cutaway under a declared `0.0` recorded `framing_intent: 0.0` and rendered a full frame.
`render_qa`'s occupancy gate reads that field precisely to learn what the picture was supposed to
look like, so the manifest was telling it the frame was barred where the frame is full - the same
defect class as §10.3's rule about an assessment field reporting a value nobody measured, arriving
through the manifest instead of through the vision pass.

Measured on 001's real state, with `pipeline.framing_intent: 0.0` declared and `_conform_fields`
re-run over its real catalog and its real manifest clip list:

| | declared | delivered | occupancy the geometry implies |
|---|---|---|---|
| 11 landscape A-roll placements (IMG_1816/1817/1822) | 0.0 | 0.0 | 31.67% |
| 7 portrait cutaways (IMG_1806/1807/1809/1810/1811/1813/1819) | 0.0 | **1.0** | 100% |

Recording the declaration alone gave the gate one declared framing and two geometries. Fed 118
samples at that profile, it failed at **spread 0.6833 against a bound of 0.05 - 13.7x over**, with
the message *"the picture changes size within one declared framing (0)"*. Recording
`framing_delivered` gives it two declared framings - 80 samples at 0.3167 and 38 at 1.0, each
internally flat at spread 0.0000 - and it passes.

(That profile is computed from 001's real catalog dimensions, its real manifest timeline ranges and
its real conform geometry, and fed through the real check. It is a verdict on the PLAN. 001 has not
been re-rendered - the captain has live markers and a Text+ block on its timeline.)

### The gate half

The consistency check used to switch off entirely the moment the manifest carried more than one
declared intent:

```python
one_geometry = len(declared) == 1
if one_geometry and spread > max_spread:
```

so the moment a video declared its framing per clip, the only gate on its geometry stopped running -
a gate that cannot fail (§10.4). `measure_frame_occupancy` now takes `framing_spans`, attributes
every sampled frame to the clip playing over it, and makes both assertions PER DECLARED FRAMING: the
FILL stretches owe the fill floor, and each declared framing owes one geometry to itself. A sample
falling outside every span is reported as unattributed rather than folded into the nearest group.

The rewrite is not a change of verdict on the one real render on disk. Run against 001's shipped
`exports/Pipeline_Edit.mp4` with the spans its current manifest produces, it reports the same numbers
the shipped `qa_report.json` recorded on 2026-08-26: 119 samples, median 1.0, min 0.9958, spread
0.0042, 0 unattributed, PASS.

### What is reachable and what is written

`resolve_framing_intent` has always taken a `block_intent`, and `compile_manifest` has always read
`block["framing_intent"]` - but nothing tested that the two meet, so "a spine block may declare its
own framing" was a claim about a resolver rather than about the pipeline. It is now proved end to
end through `compile_manifest` (`tests/test_compile_manifest.py`).

**Nothing writes it.** `mesh_spine`'s handoff does not ask for a framing and `spine_contract` does
not list the key, so the hook is reachable and unused. On the B-roll side the hook is the PLACEMENT,
not the block: `_resolve_framing(broll)` reads the b_roll assignment's own `framing_intent`, because
a cutaway is different footage from the block it covers and framing it by that block would be wrong.
Nothing writes that either.

**Moved out of the rules (AGENTS.md 10.3 and 10.4) on 2026-08-29.**
001 declares `pipeline.framing_intent: 0.0` once: its **11 landscape A-roll placements deliver 0.0 and
its 7 portrait cutaways deliver 1.0**, because that is what the footage can do. Whether the picture is
inset at all is the captain's preference, and the project declaration is where they said it belongs.

On the gate side: a video declaring more than one framing used to switch the consistency half off
altogether, so declaring a framing per clip removed the only gate on geometry.

## the-caption-grouping-reconstruction-used-the-wrong-predicate

**The rule:** AGENTS.md §10.2, "A card fits the BOX, not one line" - unchanged, and this is the
evidence that it holds at HEAD.

Project 001 ships **45** caption cards. A reconstruction of the grouping at 160 px reported **100**,
and reported that **40 of the 45 cards on screen do not fit the box**. Both figures are real and both
came from calling `CaptionFitter.fits` - the ONE-LINE predicate - where the pipeline calls
`fits_in_box`, which allows `MAX_CAPTION_LINES = 3` wrapped lines.

Reconstructed on 001's real spine, with the real bundled `Montserrat-Variable.ttf`, an 840 px
`captionMaxWidth` and a 12 px outline (816 px usable):

| font size | predicate | cards |
|---|---|---|
| 160 | `fits_in_box` (3 lines) | **45** - what shipped |
| 160 | `fits` (1 line) | **100** |
| 85 | `fits_in_box` (3 lines) | 34 |
| 85 | `fits` (1 line) | 56 |

and against the 45 cards actually on screen:

| font size | fail `fits` (1 line) | fail `fits_in_box` |
|---|---|---|
| 160 | **40 of 45** | **0 of 45** |
| 85 | 22 of 45 | 0 of 45 |

So there is no anomaly in the grouping path. `fitter.measured` is True on this project - the font
file is found and PIL measures it - and 45 cards at a median display of 0.781 s is exactly what
today's code produces from 001's spine at 160 px.

**Moved out of the rule (AGENTS.md 10.2) on 2026-08-29.**
On 001 the one-line predicate reports 100 cards where 45 shipped, and rejects 40 of the 45 that are on
screen; the box predicate reproduces all 45 exactly.

## the-caption-size-that-governs-one-video

**The rule:** AGENTS.md §10.2, "A project may typeset its own captions".

The captain measured the Text+ block they placed on 001's timeline - Open Sans Semibold, 65 px of
ink, an effective **~85 px** - against the **160** the video shipped, and ruled on 2026-08-28:

> Set 001 to ~85px now and leave the four presets alone

> the typography on the subtitles is something i want to use for **this specific test project only**
> and is not meant to be the end standard design

160 is `subtitle_style.LEGACY_FONT_SIZE`, which 001 gets because it names no brand template and so
resolves `default_subtitles`. There was no level between "the preset" and "the legacy default" at
which one video could say something, so `pipeline.subtitle_typography` is that level: the same
`{font, size, weight}` shape a template's `style.typography` uses, overriding it KEY BY KEY rather
than replacing the slot, because asking for a size is not asking to give up the typeface.

`bold_large` (192), `clean_standard` (144), `minimal` (120) and the legacy 160 are untouched.

### What the size moves, measured

Reconstructed on 001's real spine with the real font, box predicate both times:

| | 160 px (shipped) | 85 px (declared) |
|---|---|---|
| cards | 45 | **34** |
| median words per card | 3 | 5 |
| cards at the 6-word cap | 3 | **14** |
| median time on screen | 0.781 s | **0.952 s** |
| cards under 0.5 s | 5 | 2 |
| cards needing `fit_scale < 1` | 7 | **0** |

The size does move caption pacing, and by less than halving the card count: at 85 px the binding
constraint stops being the box and becomes `split_into_groups`' `max_words = 6`, which 14 of the 34
cards hit. This is the PLAN's arithmetic on 001's frozen spine; 001 has not been re-run or
re-rendered.

**Moved out of the rule (AGENTS.md 10.2) on 2026-08-29.**
Measured on 001's frozen spine: **45 cards at a median 0.781s on screen at 160px, 34 at a median
0.952s at 85px**, with `max_words = 6` becoming the binding constraint on 14 of the 34. Re-measure
rather than assuming a ratio.

## the-cutaway-window-came-from-a-muted-waveform

`AGENTS.md` §10.5, "Which SECONDS of a chosen cutaway play".

**The captain, on a marker dropped on 001's own timeline at 00:00:24:24:** *"this clip is honestly
like broll of nothing, im confused why it was chosen and added here."* And, asked to expand: *"did
it actually see the clip or did it make that choice blind?"*

It made the choice blind. It did not make the choice at all - code did.

### What chose the seven windows

`find_best_segment` in `library/steps/step_3_02_select_broll/post_bridge.py` had three strategies.
Strategy 1 required `len(scene_boundaries) >= 2`; **15 of 001's 17 clips carry exactly 1**, so it
almost never ran. Strategy 2 therefore ran, and it centred the window on
`temporal_index.energy_curve` - per-second RMS of the clip's **audio**, on clips `compile_manifest`
places `video_only: True`, so that audio is never heard. Five of the seven shipped windows are
centred within **0.000 s** of the nearest audio peak; the other two at 0.124 s and 0.190 s, clipped
at the clip edge by `_fit_to_clip`.

Strategy 1 was not a visual strategy either. On the one clip it did run (clip_002, 2 boundaries) it
scored segments 30% on that same audio curve and then centred the window on
`_peak_energy_in_range(energy, ...)` - the audio peak again, at 13.233 s.

**31.0% of the finished picture** was placed this way.

### The disconfirming check: a motion rule picks the same seven?

Run first, because it could have closed the whole question. Strategy 2's arithmetic, unchanged, with
`motion_energy` substituted for `energy_curve`, over 001's own candidate windows:

| window | clip | shipped (audio peak) | motion peak | same? |
|---|---|---|---|---|
| block 1 assignment | clip_008 | 1.602-5.264 | 5.136-8.798 | MOVED |
| block 4 assignment | clip_014 | 1.339-3.595 | 2.105-4.361 | MOVED |
| block 6 assignment | clip_001 | 2.185-3.567 | 1.642-3.024 | MOVED |
| block 8 assignment | clip_005 | 3.717-7.903 | 0.000-4.186 | MOVED |
| block 13 assignment | clip_002 | 12.274-14.192 | 9.108-11.026 | MOVED |
| block 7 interjection 1 | clip_006 | 1.650-4.150 | 1.817-4.317 | MOVED |
| block 7 interjection 2 | clip_004 | 22.750-25.250 | 10.683-13.183 | MOVED |

**0 of 7.** The audio peak was not accidentally fine, and the task stood.

It also establishes the opposite of what it might look like: **a motion rule disagreeing with an
audio rule on 7 of 7 is not evidence that motion is right.** Nothing on 001 says a busier window is
a better cutaway, which is why `DECLINED_TO_RANK` measures motion and refuses to rank on it.

### Relaxing the two-boundary requirement - measured before choosing

The known unknown was whether strategy 1's `>= 2` should simply be relaxed. Taking span points as
scene boundaries UNION the vision pass's time-bounded `blocks`, over all 17 of 001's clips:

| | clips with >= 2 candidate spans |
|---|---|
| scene boundaries alone | **2 of 17** |
| union with timed blocks | **13 of 17** |

The four that stay at one span are clip_001 (3.57 s), clip_008 (9.07 s), clip_010 (6.30 s) and
clip_014 (4.70 s) - clips where one window is most of the clip.

### The seven windows, re-derived

Against 001's frozen state at this revision. Nothing was re-run and nothing was re-rendered.

| window | clip | before | after | what chose it |
|---|---|---|---|---|
| block 1 assignment | clip_008 | 1.602-5.264 | **0.000-3.662** | `single_span` - one span, 0-9.068 |
| block 4 assignment | clip_014 | 1.339-3.595 | **0.000-2.256** | `single_span` |
| block 6 assignment | clip_001 | 2.185-3.567 | **0.000-1.382** | `single_span` |
| block 8 assignment | clip_005 | 3.717-7.903 | **0.000-4.186** | `single_span` |
| block 13 assignment | clip_002 | 12.274-14.192 | **10.000-11.918** | `moment_match_tie` 0.43 over 3 spans |
| block 7 interjection 1 | clip_006 | 1.650-4.150 | **0.000-2.500** | `moment_match` 0.58 over 3 spans |
| block 7 interjection 2 | clip_004 | 22.750-25.250 | **0.000-2.500** | `moment_match` 0.44 over 3 spans |

All seven moved. Four moved to `single_span`, which is the honest reading of a clip that offers one
candidate: **nothing chose the window, the clip did.** Recording that as `moment_match` would report
a decision that was never available, so it has its own basis word.

The one clearly-improved window is clip_004. Its 20.0-26.777 span - where the audio peak put it - is
observed as *"the vehicle is driving forward along a road lined with trees and power lines"*, and
the model had asked for *"dashboard and instrument cluster, road and buildings passing beyond the
windscreen"*, which the vision pass observed at 0-10 s. The audio peak had put the window in the
span the model did not ask for.

### The captain's own complaint clip: honestly, no

The marker at 00:00:24:24 lands on the V2 clip `IMG_1811.MOV` at timeline frames 705-780, source
frames 50-125 - **block 7 interjection 1, clip_006, 1.650-4.150 s**. The change moves it to
0.000-2.500 s. Both windows sit inside the same candidate span, which the vision pass describes as
*"the camera captures a view from inside a vehicle moving forward through an intersection"*, and
clip_006's other two spans are the same shot continuing (*"driving forward in a lane"*, *"driving
forward in traffic"*). **The window cannot fix this one.** 22.87 s of one driving plate contains no
better 2.5 s.

The model said so itself when it chose the clip: *"the picture's job here is to let the ear keep
working rather than to illustrate: a neutral observational wide, chosen for being unremarkable."*
The captain is objecting to the CLIP, and which clip is chosen is a different decision from which
seconds of it play.

### What is not established

- No window was judged against a render. The re-derivation is the PLAN's arithmetic on frozen state.
- `usable_ranges` is wired into the chooser and contributes nothing on 001, because all 17 documents
  carry `usable_ranges_method: "unmeasured"` (report R5). It will start filtering after 1.03 re-runs.
- Whether starting an undiscriminated window at the head of its span is better than anywhere else in
  it is unmeasured. It is the same reading `music_section` gives a track with no declared section:
  the absence of a decision, stated rather than dressed up.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
The post-bridge chose the window by centring it on the muted clip's audio RMS peak. On 001's run of
record that decided **7 of 7 windows, 31.0% of the finished picture**.

A behavioural test alone would not have caught the strategy that shipped, because the visual branch
only ran on clips with fewer than two scene boundaries.

The old visual strategy needed >= 2 scene boundaries and got them on **2 of 001's 17 clips**; the
union with the vision pass's `blocks` gives a real choice on **13 of 17**, and the four that stay at
one span are 3.6-9.1 s clips.

Matching `preferred_moment` against what the vision pass observed during each span is the same shape
`speech_sequence` uses to resolve a passage to a source range.

`DECLINED_TO_RANK` holds because 001's evidence supports neither side of "a moving shot makes better
B-roll than a still one".

**Four of 001's seven cutaways are `single_span`**, and 001 carries the stale `[[0, duration]]`
`usable_ranges` on 17 of 17 clips.

## seven-reads-of-a-key-that-cannot-exist

The pipeline decision map (2026-08-25) counted ten code sites reading
`creative_direction` and found that eight named keys step 2.01's schema does not
define.  Re-measured at `89c796b` on 2026-08-29, nine of the ten were still
there and SEVEN still read an undeclared key:

| Site | Key read | What the default did |
|---|---|---|
| `generate_motion_props.py:129` | `visual_style` | `{}`, so the accent branch below could not fire |
| `generate_motion_props.py:150` | `accent_color` | `None`, so the colour always came from the palette |
| `generate_motion_props.py:168` | `title` | `""` - the upper third has never carried a word |
| `generate_motion_props.py:169` | `subtitle` | `""` |
| `generate_motion_props.py:172` | `series_name` | `""` |
| `generate_motion_props.py:173` | `episode_label` | `""` |
| `step_4_04_plan_sfx/post_bridge.py:553` | `content_type` | `""`, so `select_preset_for_content`'s undeclared-type answer picked the Fairlight preset the renderer applies |

The two the map counted and are already gone: `energy_level`, which the deleted
`audio_reactive_sfx.scale_sfx_density` cut half the SFX plan by, and the
"building" -> "high" collapse in `creative_cohesion.map_energy`, closed by
`library/tools/energy_reading.py`.

**Which side was wrong.**  Step 2.01's `handoff.md` lists exactly eight fields
and says of them: *"The creative_direction is NOT a script or shot list - it's
a compass."*  Its `manifest.json` `expected_schema` declares the same eight,
and `present_llm_step` injects that schema into the prompt.  Eight is therefore
the whole of what the model is asked for.  Project 001's own answer confirms it:

```
$ python3 -c "import json;print(sorted(json.load(open(CD))['creative_direction']))"
['audience_emotion', 'emotional_landscape', 'energy_arc', 'key_moments',
 'narrative_theme', 'rationale', 'target_energy', 'target_mood']
```

Repairing the other side - adding `title` and `accent_color` to the schema -
would have looked identical from the code and would have asked a creative
director for on-screen copy, which section 14 puts with the project.

**What the read sites were repaired to.**  The four copy keys have no declared
source anywhere: on-screen copy is artwork, and neither `content.bookends` nor
`effect.timed_text_overlay` feeds the upper third, so nothing is drawn and
`props_draw_ink` skips the render - the state it has always been in, now stated.
The accent colour comes from `style.color_palette` through
`library/tools/brand_palette.py`, which was already the only route carrying a
value.  The Fairlight preset comes from the brand audio slot's
`preferred_preset`, which `select_preset_for_content` already prefers; a mix
preset is a MECHANICAL default under section 10.5, so its documented
undeclared-type answer is legitimate - it is now reached by saying nothing was
declared rather than by reading a key that cannot exist.

**What was measured on 001, before and after.**  The props and the preset are
byte-identical, and that identity IS the finding: every one of the six keys was
dead, so removing them changes nothing a viewer sees.

```
BEFORE (origin/main)                       AFTER (this change)
  title        = ''                          title        = ''
  subtitle     = ''                          subtitle     = ''
  accentColor  = '#FFFFFF'                   accentColor  = '#FFFFFF'
  content_type = '' -> dialogue_enhancement  preset       = dialogue_enhancement
  cd.get('title')       -> silent default    direction_value(cd,'title') -> RAISES
  cd.get('content_type')-> silent default    direction_value(cd,'content_type') -> RAISES
```

One read site COULD reach the picture, and only ever with a value no model
writes:

```
BEFORE: generate_motion_props({}, {"accent_color": "#123456"}, SPINE, brand_effect=ON)
        -> accents drawn in '#123456'
AFTER:  -> MissingAccentColor: a brand template enabled motion_accents but
           supplies no usable accent colour.
```

`tests/test_motion_graphics_template.py::test_creative_direction_is_used_when_no_palette`
asserted the BEFORE behaviour and passed for its whole life, because it handed
the generator a key no model writes.  It is now
`test_a_creative_direction_cannot_colour_the_accents`, and
`tests/test_motion_graphics_empty_render.py::test_a_creative_direction_with_a_title_still_draws`
is now `..._draws_nothing` for the same reason.

**A further finding the map did not have.**
`transition_selector._is_high_energy` has no production caller at HEAD - the
scene-change defaults it gated were withdrawn - so `target_energy` reaches
exactly one place, `creative_cohesion`'s `measurements.declared_target_energy`,
which reports it and judges nothing.  **No `creative_direction` field has a
mechanical reader that changes a frame.**  That is a captain's question and not
a defect: giving one a reader means inventing a rule about how a mood becomes a
cut, which is what section 10.5 forbids the engine from doing on its own.

The full re-measurement of all eight families the map listed, with a
recommendation per remaining field, is in
[`docs/UNREAD_DECISIONS_INVENTORY.md`](UNREAD_DECISIONS_INVENTORY.md).

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
The seven sites: a title, a subtitle, a series name, an episode label, a visual style and an accent
colour in step 4.06, and the Fairlight preset's `content_type` in step 4.04. Each returned its default
on every run the pipeline has ever made.

Which side was wrong was established from 2.01's handoff, which lists eight fields and says the
direction "is NOT a script or shot list". Repairing the other side - adding `title` to the schema -
looks identical and asks a creative director for artwork that AGENTS.md section 14 puts with the
project.

---

## the-plan-could-not-say-how-long-a-sound-plays

**The regression.**  On 001's run of record (2026-08-26) `sfx_spec` carried two
sounds and each carried its own length: `sfx_001` at block 8 ran **0.25 s**, and
`sfx_002` at block 13 ran **3.0 s**.  The entry named a `sfx_type` and a keyword
matcher turned `"swish"` into `whoosh_impact.mp3` - which is one of the two
defects `#298` fixed by having the model name the FILE.

`#298` also removed `DURATION_DEFAULTS`, and it was right to: that table said a
`bass_impact` runs 0.5 s while the file the matcher handed it was a 5.317-second
riser, so the manifest asserted a length the audio did not have.  But the schema
that replaced it was `{spine_block_position, sfx_id, volume_level, rationale}`,
with nowhere to put a length at all, and `_entry_duration` played the file's
whole measured remainder.  **The same `whoosh_impact.mp3` the run of record
played for 0.25 s plays for 8.04 s.**  Both numbers reach the timeline without
anybody choosing them; the rule *"a duration is a property of the sound"* was
applied one step too far, past *not inventing* a length and into *not asking*
for one.

**Why the library makes it matter.**  Measured over the captain's 78 entries
(2026-08-28): median duration **3.94 s**, only **9 of 78** at or under 1.0 s,
only **4** at or under 0.5 s, **30** over 3 s, longest **76.14 s**; envelope
**42 swelling, 17 fading, 8 sustained, 9 punchy**.  Without a length, "put a
short accent on this cut" is expressible only by choosing one of nine files.

**What the bound is, and why there is only one.**  The upper bound is
arithmetic: a file cannot play longer than it is, less whatever the `punchy`
transient trim already skipped.  A request past it is refused by name - a clamp
would hand back a different length from the one the plan asked for, and a plan
asking for four seconds of a two-second sound is a plan written against a sound
it has not read.  The lower bound is **two frames of the run's own timebase**,
which at 30 fps is 0.067 s: one frame at level plus one frame of de-click ramp,
below which the keyframe grid cannot express the thing at all.  That is the
timebase, not taste.  How short a sound *should* be is the plan's call and
section 10.5 forbids the engine holding an opinion about it.

**The click is measured, not assumed.**  `whoosh_impact.mp3` decoded from its
0.714 s transient for 0.25 s, 48 kHz mono, 12,000 samples: peak **13,209**,
final sample **-2,185** - **16.5% of the slice's peak**, and the RMS of the last
millisecond is 1,810 against 3,991 for the slice.  That is a discontinuity, not
a decay, so honouring a length includes ending it cleanly.  The ramp is **one
frame** because the mix reaches Fairlight as OTIO volume keyframes and a
keyframe is addressed by FRAME (section 5): a conventional 5-10 ms de-click has
no representation on a 30 fps grid, so 33 ms is the floor the FORMAT sets rather
than a number picked for feel.

**The run of record, replayed at HEAD.**  Its own entry, with `sfx_id`
substituted for the withdrawn `sfx_type` and its 0.25 carried in the new field:

    sfx_id             whoosh_impact.mp3
    source_in          0.714          (punchy, so the transient trim applies)
    timeline_in        34.615
    timeline_out       34.865
    duration_seconds   0.25
    fade_out_seconds   0.0333
    played length      0.25 s

`timeline_in` and `timeline_out` are the shipped video's own two numbers.  The
same entry with no `duration_seconds` plays 7.326 s and carries no ramp; the
same entry asking for 12.0 s exits 1 with *"A sound cannot play longer than it
is. Ask for at most 7.326s ... Nothing is clamped."*

`library/tools/sfx_duration.py`, `tests/test_sfx_duration.py`.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
Declaring no `duration_seconds` plays the whole sound, which is the ABSENCE of a decision - the same
reading `music_section` gives a track that names no section.

The de-click ramp exists because a truncated waveform steps to silence and clicks.

## the-catalogue-was-copied-into-the-prompt

`#298` put the whole SFX library in step 4.04's prompt, and that was the fix: a
model that cannot read `avoid_when` cannot decline a sound the library says to
decline.  As one TOON table it measured **44,397 B**, **44,575 B** serialised
into the context - and once `#295` stopped copying the creative brief into seven
prompts, that was **44.1% of step 4.04's entire 101,093 B context** and 6.5% of
the 684,120 B the pipeline's twelve contexts total.  `#299` named it.

**The mechanism existed and had not been applied.**  `#295` built exactly this:
a large document is written to disk, the prompt carries a map of headings with
sizes, line ranges and ledes, and the model follows a `sed -n 'a,bp'` for the
part it needs.  Nothing about it is brief-specific except two sentences of
header, which are now parameters.

**The document was adapted to the mechanism, not the other way round.**
`build_reference` splits on `##` headings and lifts each section's first line of
prose as its lede, so `sfx_library.catalog_document` writes **one `##` section
per sound, titled with the exact `sfx_id` an answer must name**, opening with
that sound's measured facts on one line.  The map therefore names every sound by
id and carries its category, length, envelope and emotional temperature inline;
the library's prose - description, source object, evokes, `works_when`,
`avoid_when`, which is **38,975 of the table's 44,397 B** - sits behind a line
range.

**Measured on the captain's 78-entry library, 2026-08-29:**

| | bytes |
|---|---|
| catalogue as an inline TOON table | 44,397 |
| ...serialised into the context | 44,575 |
| catalogue document written to disk | 54,080 |
| the map, in the prompt | 12,960 |
| ...serialised into the context | **13,351** |
| saved | **31,224** (70.0% of the catalogue) |
| step 4.04's whole context | 101,093 -> **69,869** (30.9% off) |
| catalogue's share of that context | 44.1% -> **19.1%** |

**All 78 sounds are still reachable**: 78 of 78 named in the map by exact
`sfx_id`, 78 of 78 carrying a line range, and 78 of 78 of those ranges resolving
to their own section in the document.  A reference that narrowed the menu would
be the shortlist problem in a third disguise - whatever picks the shortlist
becomes the chooser, which is what the deleted word list was.

Two side effects worth recording.  The document is markdown, so a description
full of commas and apostrophes needs no escaping at all, where a TOON cell had
to be backtick-quoted (section 10.1).  And the bridge REFUSES a run that reaches
it with no `project_folder`: there would be nowhere to write the document, and
carrying 44 KB inline instead is the degraded mode that ships quietly.

`library/tools/brief_reference.py`, `library/tools/sfx_library.catalog_document`,
`tests/test_sfx_catalogue_by_reference.py`.

**Moved out of the rule (AGENTS.md 10.5) on 2026-08-29.**
Inline the catalogue was **44,575 B and 44.1% of step 4.04's whole context**; the map is **13,351 B, a
30.9% reduction of the step** (measured 2026-08-29). The map names all 78 sounds by id with category,
length, envelope and temperature, and a line range for the prose - nothing is filtered, ranked or
truncated.
---

## three-views-of-one-analysis

Step 3.02 `select_broll` carried **three readings of the same per-clip vision
analysis in one prompt**.  Measured with the step-replay bench on 001's frozen
snapshot `001-degradation-20260828` at 75d3e84, over a context of 88,475 B:

| section | bytes | share |
|---|---|---|
| `semantic_analysis_documents` | 35,813 | 40.5% |
| `view:picture` | 10,250 | 11.6% |
| `broll_candidates_toon` | 7,613 | 8.6% |
| **three views of one analysis** | **53,676** | **60.7%** |

**The share had grown, not shrunk.**  The prior report measured 40.6%; `#295`
stopped copying the captain's brief into seven prompts, so the denominator fell
faster than the duplication did and the same redundancy came to dominate a
smaller context.  This is the step whose cutaway choices the captain complained
about, and the one about to be handed a real frame.

**What each of the three carries, established rather than assumed.**

`broll_candidates_toon` is the pre-bridge's table, and the handoff calls it "the
primary source of WHAT is in each clip".  Its `description` is
`vision_schema_adapter.scene_prose` - `scene[]` rendered - and its
`framing`/`stability`/`camera_move` are the `camera[]` summaries.  On 001 that
rendering is **lossless**: the 600-character cap binds on **0 of 17** clips, and
**16 of 17** clips have exactly one `camera[]` segment, so the deduped
`wide -> close-up` form loses nothing but the bounds.  It also carries two things
the analysis has not got at all - the catalogue's `duration_s`, and
`used_as_aroll`.

`view:picture` is `actions[]`: one row per observed action window with its own
bounds, covering 95.0% of 001's footage where `scene[]` covers 46.4%.  It is also
**the view the ANSWER is resolved against** - `cutaway_window.choose_window`
matches the model's `preferred_moment` against `blocks[].visual`/`label` - so a
model that cannot see it is writing a moment into a matcher it cannot see.

`semantic_analysis_documents` is the raw structure the other two were rendered
from.  **62.8% of its cells are `objects[]` alone** (22,347 B), then `assessment`
16.6%, `scene` 13.9%, `camera` 5.9%.  It is also the only one of the three keyed
by the document's own file stem (`IMG_1806_v3`) rather than the catalogue id
(`clip_001`) every other table in the context uses - so it is the one view the
model could not join to the other two without a mapping (section 10.1).

**So the raw structure moved, and the two renderings stayed.**  Same mechanism as
`#295` and `#299`, third document, one row in `REFERENCED_INPUTS`.  Four things
were only ever in the structure, and all four are at the path:

  * `objects[]` past the four labels the `subjects` column keeps - **159 objects
    on 001, of which 68 reach the table** - with role, category, time ranges and
    `readable_text` (10 of the 159 carry text);
  * `camera[]`'s per-segment time bounds;
  * `scene[]` as fields rather than as one prose line;
  * every assessment field the table has no column for.

**Measured on 001, 2026-08-29:**

| | bytes |
|---|---|
| raw structure, as the projection sent it | 35,813 |
| document written to disk | 37,058 (629 lines) |
| the map, in the context | **4,507** |
| step 3.02's whole context | 88,475 -> **57,169** (35.4% off) |
| ...and with `#340`'s frame strips in it | 94,994 -> **63,688** (33.0% off) |
| the three readings, together | 53,676 -> **22,370** (58.3% off) |
| their share of the context | 60.7% -> **39.1%** |
| readings as a multiple of the structure they read | 1.499 -> **0.625** |

That last ratio is what `tests/test_broll_context_share.py` guards, because it is
the one number that does not depend on the fixture: both sides scale with the
analysis.  Re-declaring `semantic_analysis_documents.*.objects` alone takes it to
**1.309** against a ceiling of 0.80.  The structural half of the guard - no
positive `context_fields` path back into the raw documents - is the thing that
actually regrew.

**Two shapes inside the document are chosen for the MAP, not for the page.**
Nothing below a `##` is a heading, because `parse_sections` lifts every deeper
heading into the map and four identical subheadings per clip is 68 lines of the
prompt saying nothing - that alone was 3,176 B.  And the identity line carries no
underscore, because `brief_reference._lede` strips markdown emphasis and turned
`content_type` into `contenttype` and `IMG_1806_v3` into `IMG1806v3`: a corrupted
identifier in the one line a model reads to decide whether to open a section.

**One thing this measurement writes into the captain's project, and it is worth
knowing.**  A replay-bench snapshot symlinks `pipeline_output` at the live
project, so a bridge that WRITES - this one, and 4.04's whenever
`PIPELINE_SFX_LIBRARY` is set - reaches the real tree despite the bench's "no
project write".  Both write only into their own step's `Kind.OUTPUT` area, which
a re-run reproduces, but the bench's claim is narrower than it reads.

**Two things the collapse broke, found by the test that guarded the OLD route.**
`tests/test_vision_schema_adapter.py::test_framing_and_usable_ranges_reach_the_broll_prompt`
measured 3.02's allow-list slice of the documents; with no such path left it
projected to `{}` and the assertion failed - correctly, on the letter, and the
sibling `test_every_semantic_consumer_projects_all_three_document_shapes` went
QUIET rather than red, because `"{}".strip()` is truthy. The test now measures
the WHOLE assembled prompt, pre-bridge included, which is strictly harder to
pass: dropping `framing` from the candidate table fails it, and so would the
stale allow-list it was written for.

And one real regression it exposed. `usable_ranges_summary` renders three states
- a range, "measured and nothing usable", "never measured" - but returned `""`
for the second whenever `unusable_ranges` recorded no reason. The B-roll
handoff defines an empty cell as *"the clip was never measured, so the whole
clip is fair game but unvetted"*, the OPPOSITE of what `deterministic_v1` with
no ranges says. That was survivable only while the raw `usable_ranges_method`
travelled beside it in the prompt; once the summary is the one carrier, it has
to say it itself. It now renders `none - whole clip excluded (no reason
recorded)` - an absence stated, never filled in. **Collapsing a structure into
a summary makes that summary's blank cells load-bearing**, and 001 does not
exercise this state (17 of 17 clips are `unmeasured`), so nothing on the run of
record would have shown it.

`library/tools/footage_reference.py`, `library/steps/step_3_02_select_broll/bridge.py`,
`tests/test_broll_context_share.py`, `tests/test_vision_schema_adapter.py`.

**Moved out of the rule (AGENTS.md 10.1) on 2026-08-29.**
Step 3.02 carried THREE readings of one vision analysis - `semantic_analysis_documents` 40.5%,
`view:picture` 11.6%, `broll_candidates_toon` 8.6%, **60.7% of its context** - and the share had
GROWN, because #295 shrank the denominator faster than the duplication. The collapse is what pays for
#340's frame strips: with them in, the step is 94,994 B -> **63,688 B**.

The two renderings stayed and the STRUCTURE moved, because the table's `description` is `scene_prose`
losslessly on 17 of 17 clips, and `view:picture` is the reading `cutaway_window.choose_window`
resolves the answer's `preferred_moment` against. Deleting the largest would have taken the
per-segment bounds, 91 of 159 object labels and every assessment field the table has no column for.

`tests/test_broll_context_share.py` guards the ratio of readings to structure: **1.499 before, 0.625
after**.

The route-change trap, in full: `tests/test_vision_schema_adapter.py` used to measure 3.02's
allow-list slice of the documents; with no such path it projected to `{}`, and `"{}"` is a truthy
string, so the sibling gate went quiet rather than red.

The blank-cell trap, in full: `usable_ranges_summary` rendered "measured, and none of it usable" as
`""` whenever no reason was recorded, and the B-roll handoff defines an empty cell as "never measured,
so the whole clip is fair game". That was survivable only while `usable_ranges_method` travelled
beside it in the prompt.
---

## the-motion-graphics-that-were-planned-and-absent

The captain, 2026-08-21: *"the motion graphics don't load, there are not any
like title cards or cool edits and whatnot, so i want the end product to be a
fully finished video before we say we are done."*  Still true on 2026-08-29,
and the brief that authorised this fix listed four candidate causes: the plan
not reaching the renderer, the renderer failing, the render succeeding and
never being composited, or a composite with a broken alpha.

It is a fifth.  **There is no plan.**

### The reproduction, before anything was changed

Step 4.06 run exactly as the runner runs it - stdin JSON in, stdout JSON out -
against project 001's frozen `pipeline_data.json`, into a throwaway project
folder, at 89c796b:

```
── step 4.06 stderr ──
No motion graphics to draw: all 11 resolved props have no upper-third text,
no accents and no progress bar. Skipping the render.
── step 4.06 motion_graphics_overlay ──
{ "declared": false, "segments": [], "total_segments": 0,
  "reason": "all 11 resolved motion graphics props draw nothing: ..." }
── files written to the motion graphics area ──
(directory does not exist)
```

And the run of record agrees - `step_outputs.render.render_output.tracks`:

```
{"V1": 11, "A1": 11, "V2": 7, "V3": 11, "A2": 1, "A3": 2}
```

**No V4 at all.**  Eleven subtitle segments on V3, eleven A-roll clips, seven
cutaways, and not one motion graphic in 54.87 seconds.

### Which stage breaks

Not the renderer, not the compositor, not the alpha.  Each of those is proved
working by the same evidence:

* **The renderer works.** A single `MotionGraphics` composition at 320x568 for
  24 frames renders in **3.4 s wall** and the ProRes 4444 carries real alpha -
  8,585 pixels above the alpha floor on frame 12, in the declaring template's
  own `#ff0055`, over a transparent field.
* **The compositor works.** The 2026-08-20 run placed eight segments on V4 and
  `render.json` recorded `"V4": 8`.
* **The alpha was never broken.** Those eight files had `max(alpha) == 0` on
  every frame because the composition was handed nothing to draw, not because
  the channel was wrong.

The break is that **nothing decides what a motion graphic is**:

* `generate_motion_props` takes `enhancement_spec` and `creative_direction`,
  both declared REQUIRED by the step's manifest and both routed by the DAG,
  and reads neither.  Step 4.03 emits `{"visual_effects": []}` and nothing
  else; step 2.01's eight fields are prose about mood and narrative.
* `library/tools/input_contract.py` cannot see it.  A deterministic step
  declares no `context_fields`, so `_reaches_prompt` answers True for every
  input, `consumed` is `code_reads or prompt_reads`, and both rows report
  `consumed: code, prompt`.
* So the entire vocabulary a step could ask the renderer for is two
  brand-template booleans, `motion_accents` and `motion_progress_bar` - and no
  step asks.  `library/tools/motion_graphics_vocabulary.py` (#329) is the
  roster of what SHOULD exist and is deliberately not wired; four of its
  fifteen entries are `reachable_now`.  001 names no brand template, and a
  project that names none gets nothing (section 10.1) - so it gets no motion
  graphics, and correctly so.
* The upper third has never carried a word and structurally could not.  #320
  removed the four dead `creative_direction` reads; **who produces the copy is
  an open captain decision** and the engine may not answer it by inventing one.

### Two defects in the path itself, both fixed here

**The progress bar was drawn under the platform's own interface.**  It sat at
`bottom: 0`, `width: 100%` - the last literal margin left in
`MotionGraphics/index.tsx` after #153 put the corner accents on the insets, and
contradicting that file's own docstring claim that every element is positioned
from the safe area.  `safe_area.py` records the bottom inset as **320px of
captions, CTA, hashtags and audio bar** on 1080x1920, so a 12px bar at row 1908
was rendered, composited onto V4, and covered.  Measured on the fixture render,
before and after:

```
before: the bar's widest row is 547 of 568   (inside the 95px inset)
after:  the bar's widest row is 462 of 568   (rows 461..472, at the inset)
```

And at the real delivery frame, one 48-frame proof render, **4.19 s wall**:

```
size 1080x1920   safeArea {top 120, right 120, bottom 320, left 90}
widest accent row  1589 of 1920, spanning 888 of 1080 px   (bar rows 1588..1599)
lowest lit row     1611 of 1920                            (the bar's 16px glow)
lit pixels         34,156 of 2,073,600  = 1.6%             (alpha is real)
```

Before the fix that row was **1908** - 308px inside the band the platform
paints over.

**A segment the manifest named and disk did not have was a `logger.warning`,**
while the identical case for timed text raised.  Every overlay is additive: the
picture underneath an absent one is intact, the build succeeds and the export
is a valid video of the right length, so nothing downstream can tell a planned
graphic that landed from one that did not.  Both, and subtitles, now go through
`compile_manifest.assert_overlay_segments_on_disk` at the same severity.

### And one lie in the props file

`show_upper_third` was `block_type == "hook" or (speech and block_idx <= 2)` -
"the title card belongs on the hook and the first two body passages", a
creative judgement taken by that file on behalf of nobody (section 10.5).  It
also wrote `showUpperThird: true` beside `title: ""` into every props file on
disk, which is what made eight fully transparent renders read like eight
delivered graphics.  The flag now follows the copy and nothing else.

### Not the same root cause as #202

Issue #202 - `zoom_blur` held for a whole clip, and 331 frames carrying
full-strength defocus nobody planned - is a **Fusion** defect: a per-clip
`.comp` whose animation range is not clamped to the transition's frames and
whose Background does not cover the source, imported onto an existing timeline
clip with `ImportFusionComp`.

Motion graphics share none of it.  They are rendered by **Chromium under
Remotion** into standalone ProRes 4444 files and placed as their own clips on
V4; `grep -rn fusion` over the whole 4.06 path -
`step_4_06_render_motion_graphics/`, `timed_text_render.py`,
`bookend_render.py`, `compositions/MotionGraphics/` - returns nothing.  There
is no `.comp`, no `GlobalIn`/`GlobalOut`, and no source-versus-timeline frame
mapping to get wrong: each segment's clock is its own `durationInFrames`.

The two share a FAMILY - a capability advertised, planned and never verified in
pixels - and not a cause.  The remedy is the same shape in both cases and is
what #202 itself asks for: a bounded fixture render that reads the frames back.
`tests/test_motion_graphics_delivery.py` is that test for V4.

**Moved out of the rules (AGENTS.md 10.2) on 2026-08-29.**
Motion graphics were a `logger.warning` in `assert_overlay_segments_on_disk` while timed text raised.

Step 4.06 reads neither of the two inputs it declares REQUIRED because 4.03 emits `visual_effects`
alone and 2.01's eight fields are prose; no step emits a motion-graphics plan at all.

`input_contract` could not see either declaration while a prompt-less step's absent `context_fields`
read as "handed every byte".

---

## the-roster-nobody-wrote-down

Evidence for AGENTS.md §16.

### The decision that was never offered

The captain, 2026-08-29, on the capability: the pipeline has *"access to remotion to make literally
any kind of motion design and animation desired."* True of the toolchain, and false at the moment of
decision - the model cannot choose from a vocabulary nobody has defined. A step asked to pick from
an undefined set picks nothing, or invents inconsistently.

The round-2 creative audit is the general shape of it: of **28 creative decisions that reached the
finished video, 9 were ones the model was never asked** and **0 were confirmed bad taste**. An
undefined roster is that failure exactly - a decision nobody offered - and it sits in the same class
as the cutaway window, the crop aim and the bed's level per block.

### The implicit roster of three

Nobody wrote it down, and it was authored by whoever wrote the component.
`remotion-subtitles/src/compositions/MotionGraphics/index.tsx` draws exactly three things, each
behind its own boolean: a two-line upper third, four corner brackets, and a progress bar.
`generate_motion_props.props_draw_ink` is the predicate that has to stay in step with it, and its
own docstring records what happened when nothing did:

> Project 001 rendered eight such segments: 53.8 MB of ProRes in which `max(alpha)` is 0 on every
> frame of every file, placed on V4 so `render.json` reported "V4: 8" - which reads as motion
> graphics delivered.

The upper third has never been able to carry copy at all: `generate_motion_props` reads
`creative_direction` for `title`, `subtitle`, `series_name` and `episode_label`, and
`creative_direction`'s schema has none of those fields. That is the copy-source decision showing
through, not a renderer defect.

A second implicit roster exists in prose. `docs/style_specification.md` §7 lists text animations,
zoom emphasis, shake/impact, tracking text and lower thirds - but it is a codification of ONE
creator's style, values included ("5% scale bump", "2-3px, 3-4 frames", "100-200ms"), and two of its
five entries are picture treatments that `plan_vfx` already owns. It informed the roster's element
KINDS and none of its numbers, and its taste ("subtle, underspoken … never flashy") is deliberately
not encoded: the engine serves a daily channel and client work, so one creator's register in the
vocabulary would reach every client.

### Why the numbers are not in the table

PR #310 emptied `library/tools/series_look.py` of four complete looks on the captain's ruling of
2026-08-28: *"i want no hardcoded values. there are no house glow looks, there are no settled house
grain or anything"*. A roster saying "the stat callout holds for 1.2 s in the accent colour" puts
that defect back one level up, in the place it is hardest to see - a vocabulary is read as
definitional, not as a default.

So `MotionElement` and `Axis` have no numeric field at all; a renderer has nowhere to read a
magnitude from. `tests/test_motion_graphics_vocabulary.py` asserts that structurally, and then scans
the source of `ROSTER`, `AXES`, `FUNCTIONS`, `OUT_OF_VOCABULARY` and `ROSTER_LEGEND` for value
shapes in the prose - hex colours, and a number with a unit. Three citations are recorded with
reasons in `CITED_VALUES`, and every one of them names a WITHDRAWN literal or a measurement taken
elsewhere: `#00D4FF` (the cyan that reached every frame of every video from
`generate_motion_props`), `60px` (the corner-accent inset `safe_area.py` records as the defect), and
`5 Hz` (the rate `compute_face_presence` samples a face centre at, which is why `tracked_label` has
no track). The scan is pointed at the tables and not at the module docstring, because a `[why]` link
carries evidence and evidence has numbers in it.

### Why the roster is bigger than the renderer

Eleven of fifteen entries cannot be drawn today. That is deliberate and it is asserted: the test
fails if `reachable_now` ever outnumbers the rest, because a roster that has converged on what the
composition already does is a roster the renderer shaped. The render path is being repaired in
parallel (`vep-motion-graphics-render-path`) and nothing in this change touches it -
`index.tsx` and `generate_motion_props.py` are unmodified.

### How large, and why not larger

The size was aimed at two failures at once.

Too small and the model has no range - three elements is what there was. Too large and it is the SFX
library's shape: **41 of its 78 entries are `emotional_temperature: cold tense`**, so a catalogue
that looks large is two thirds one register and the useful entries are hard to find. `FUNCTIONS` is
the register axis here and `register_spread()` is the same statistic; the test fails when any one
function holds more than a third of the roster. The unit of an entry is a QUESTION an editor answers
with a graphic, not a shape a component can draw: `stat_callout` and `counter_roll` are two entries
because a number that holds and a number that changes are two decisions, while a rectangle and a
rounded rectangle are one.

Fifteen also fits whole in a prompt, which is what lets `roster_rows()` ship the entire table.
Whatever selects a shortlist becomes the chooser (AGENTS.md §10.5) - the failure the SFX word list
was.

### What the roster does not decide

Two neighbouring decisions were explicitly left with the captain on 2026-08-29: what produces the
COPY a motion graphic shows, and whether the model authors each component or fills a props schema.
Each entry declares only `copy`: required, optional or none. A model writing the words, a project
declaring them, a transcript supplying them and a template carrying them all satisfy the same entry.
Under the props answer the axes ARE the schema; under the authoring answer they are the brief the
component must honour and `never` is what review checks it against.
`motion_graphics_vocabulary.COPY_SOURCE_IS_UNSET` records both, and two tests assert the roster rows
name neither a producer nor an authoring mechanism.

That is also why nothing is wired into a planning prompt by this change. `plan_vfx`'s `handoff.md`
is one of the twelve frozen prompt files, its `enhancement_spec` schema's `motion_graphics` list has
no reader, and a table put in front of a model that its handoff does not name is the unread-key
defect of §10.2 in the other direction. The wiring is a separate change and it needs the copy answer
first.

## the-qa-report-had-no-reader

**The rule:** AGENTS.md §10.4, "Every QA finding has a reader".

Step 6.02 measures the finished video and writes `exports/qa_report.json`. Nothing has ever opened
that file. On project 001's run of record it held **all four** of the captain's named shortfalls,
and every one of them was in there before this change:

| finding | metric | `passed` | `severity` |
|---|---|---|---|
| 4 caption gaps over 2 s, the longest 6.25 s | `subtitle_gaps` | False | `info` |
| 13 of 45 cards over 25 characters per second | `subtitle_read_speed` | False | `warning` |
| 1 of 11 mix windows meets the plan's own declared margin | `speech_above_bed` | True | `warning` |
| -20.94 LUFS, 6.94 dB off target | `lufs` | False | `error` |

Captions, sound and the look - and a `warning` in a file with no reader is not a warning. This is
the same defect as a gate that cannot fail (§10.4): it reads as coverage and is not.

### Why the run summary AND `review_rough_cut`, and not one of them

The run summary is unconditional: it prints on every run, needs no DAG change, and puts the numbers
where a person is already looking at the end of a run.

`review_rough_cut` (3.03) is the step with a review job, and several of the findings are
consequences of decisions taken at or before it - `subtitle_gaps` is the aligner's leading gap
(`vep-aligner-leading-gap`), and `subtitle_read_speed` moves with a caption grouping that follows
from block boundaries. So both, and they share one reader.

**No DAG edge can carry them.** `validate` is the DAG's final node and `review_rough_cut` is in
phase 3; an edge from the one to the other is a back edge and `topological_order` would refuse it.
So the findings travel the way `project_config` does - by name, in `gather_step_inputs`, and only
to a step whose manifest declares them - and they describe the LAST render rather than this run's.
That is §3's rule ("a prerequisite is a condition on STATE, not on lineage") applied to a reading
rather than to a prerequisite: `load_findings` asks state first and the file second, and RECORDS
which answered, so the step is never told a previous run's report is this one's.

### Is any of the four genuinely advisory

**One is.** `speech_above_bed` reports `passed: True` whatever it measured, because
`SPEECH_ABOVE_BED_GATES` is False - and §10.4 records why it must stay False: `background` means
-18 dB of CLIP GAIN, the check reads it as SEPARATION, and 001's music is mastered 8.6 dB hotter
than its speech, so the target is unreachable by any mix setting. It is a real measurement of a
real shortfall and it is not a build failure. It reads as ADVISORY.

The other three are FAILING: each check's own `passed` is False. Their severities differ and are
kept - `error` for the loudness, `warning` for the read speed, `info` for the gaps - and only a
failing `error` is reported as something a gate would be entitled to act on. **Nothing was promoted
to make the report look thorough, and no threshold moved.**

### Advisory is an enumeration, not a rule about severity

`REPORT_ONLY_METRICS` names the two checks whose gate boolean is False. Reading "advisory" off
severity alone would have been simpler and wrong, because a report already on disk cannot be
re-severitied: `subtitle_qa` stamped its FAILING severity on every result whether or not it had
failed, so 001's own report carries

    {"metric": "subtitle_overlap", "passed": true, "severity": "error",
     "detail": "No overlapping subtitles"}

and a severity-only reading promotes that to an `error` finding. That is the trap this task named -
a silent problem converted into a loud wrong one - arriving through the reader rather than through
a gate. `subtitle_qa`'s severity now moves with its verdict, which is what `render_qa` has always
done (`severity="error" if not passed else "info"`); the thresholds are untouched.

### Reading is not gating

The summary block sits after every `status = ...` assignment in `run_pipeline` and assigns nothing.
`tests/test_qa_findings_reach_a_reader.py::test_reading_the_findings_cannot_change_the_run_status`
pins that ordering off the runner's own AST, so a later edit that moves the block above the status
fails rather than quietly starting to block runs.

### The failure mode, turned inside out

`FINDING_READERS` carries one row per metric either producer can emit, naming the DAG node that
owns the decision behind it and what a reader does with it. A metric with no row is not dropped: it
is `unrouted`, printed first and loudest in the summary, and carried to 3.03 with the reading "NO
READER IS DECLARED FOR THIS METRIC". Live, on a copy of 001's frozen state with one unclaimed row
added:

    QA findings: 18 checks, 5 failing, 1 advisory, 12 clean
        ⚠ 1 finding(s) reach NO READER - add a row to qa_findings.FINDING_READERS:
          ? caption_contrast: 3 cards sit under 4.5:1 against the picture behind them

The test harvests the metric names out of `render_qa.py` and `subtitle_qa.py` by AST and fails in
BOTH directions - a metric with no reader, and a reader for a metric nothing emits - so a new check
that forgets its reader cannot repeat the defect, and the table cannot go stale.

### Cost

The findings are 4,067 B of `review_rough_cut`'s 43,343 B context on 001 - 9.4%. All seventeen rows
travel, clean ones included: filtering would put the module in the position of deciding which
measurements a reviewer may see, which is the shape of the defect. The per-metric `reading` is
keyed rather than a sixth column, because it is a constant per metric and a blank cell on a clean
row would read as an absent measurement (§10.3) rather than as "no reader had to act".

001 was not re-run and not re-rendered. The run summary above was produced by invoking the real
runner against a COPY of 001's frozen `pipeline_data.json` and `exports/qa_report.json`, on one
already-complete step.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
Step 6.02 measured all four of the captain's named shortfalls on 001 - the **6.25 s caption gap**,
**13 of 45 cards over 25 characters per second**, **10 of 11 mix windows missing the margin the plan
itself declared**, and the **-20.94 LUFS master** - and wrote every one to `exports/qa_report.json`,
which nothing opened.

Step 6.02 still decides what fails; the summary block assigns nothing, and the test pins that ordering
off the runner's own source so an edit that moves the block above the status fails rather than quietly
starting to block runs.

Advisory is read off `REPORT_ONLY_METRICS` and never off severity alone because a report already on
disk cannot be re-severitied, and `subtitle_qa` used to stamp its failing severity on a passing
result.

A new check that forgets its reader cannot go quiet, and the table cannot go stale, because the test
harvests metric names out of both producers and checks both directions.

## the-review-answered-and-nobody-read-it

*(§10.4, "Every QA finding has a reader"; §3, "A declaration must be true")*

Step 3.03 `review_rough_cut` is `deterministic_with_llm`: `step.py` computes `rough_cut_review` and
the LLM is then asked for whatever the manifest declares that the step does not already have. That
left exactly one key, `cut_decisions`, declared as

    { "name": "cut_decisions", "type": "list" }

with no description, so `generate_output_schema_text` injected this into the prompt on every run the
step has ever made:

    // (required)
    "cut_decisions": []

A bare list, named nowhere in the step's frozen `handoff.md`. And `grep -rn "cut_decisions"
library/ tests/` returned **one** occurrence: that declaration.

### The model answered anyway, and the answer was good

001's frozen `pipeline_data.json` carries `step_outputs.review_rough_cut.cut_decisions`: **19 rows,
9,498 bytes**, in a shape the model invented for itself -
`{decision, scope, from_block, to_block, rating, rationale}`.

- **8 rows** are `decision: "flow"` - one per cut between speech blocks, keyed on `to_block` (the
  block the cut leads into, which is exactly what `cuts_toon` keys on), rated with the four words
  Check 7 of the handoff names: seven `smooth`, one `acceptable`.
- **11 rows** are about the cut as a whole: the reconstructed script, the sentence-completion and
  arc passes, four key moments, two notes, and one `flag`.

That flag, verbatim and discarded:

> Block 13's aligned source range starts at 29.002s while its first strongly matched word
> ('almost') is at 30.58s. The alignment anchored on the word 'i' inside the preceding phrase
> "i'm happy that i at least recorded this", 1.58s before the passage the speech sequence asked
> for [...] the viewer hears roughly 1.5s of audio before the captioned text begins [...] The
> clean fix belongs in step 2.2.

**The review layer found the passage mis-anchor.** It named the wrong anchor word, the drift, both
consequences, the owning step and the fix. That defect went on to be the largest single flaw in the
finished video - 6.901s, 11.6% of it, wrong audio with no captions - and was not fixed until #331,
by an audit that rediscovered it from scratch. Nothing read the flag, because nothing read the
field.

### Why 4.02 is the reader and 4.01 is not

Four nodes are downstream of the review: `plan_subtitles`, `plan_transitions`, `plan_vfx`,
`plan_sfx`. Only one makes a decision AT a cut.

`plan_subtitles` (4.01) runs first of the four and looks like the obvious reader. It cannot be one:
it is `deterministic` - a `step.py`, no `handoff.md`, no `bridge.py` - so `detect_implementation`
gives it no prompt at all, and its grouping is measured pixels against the caption box. Routing a
narrative judgement there would have reproduced the defect at a second address.

`plan_transitions` (4.02) already holds the matching table. Its pre-bridge builds `cuts_toon`, one
row per cut, keyed on `cut_point_position` - the same identifier `transition_carriers.cut_carriers`
keys on and the same one `transitions_toon` carries to 4.04. So the verdict needs no join: it is two
more columns, the route #305 took for `can_carry_drawn_transition`.

Measured on 001's frozen snapshot at `WORKTREE`, 8 of the 13 rows now carry a real verdict, e.g.

    12  43.18  transition_slot-to-speech  no  ...  acceptable  The largest jump in the video:
    clip_013 to clip_015, and a change of subject from the past to the present.

4.02's context: **55,644 B -> 58,816 B (+3,172 B, +5.7%)**.

### The half that names no cut goes to the run summary, not to 4.02

The other 11 rows are findings about the edit, and 4.02 is not their owner. Putting a finding owned
by step 2.02 into the transitions prompt does not make it actionable - that is exactly the shape
`cohesion_scope.OWNED_UPSTREAM` exists to name. So they take the reader a finding nobody downstream
can act on already has: the run summary prints them, after `status` is decided, the way `qa_findings`
is printed. `tests/test_cut_decisions_reach_a_reader.py` pins that ordering off the runner's source.

A route BACK to the owning step does not exist and is not built here. It is the same open shape as
`OWNED_UPSTREAM`: stated, not quietly closed.

### What 3.03 reviews that it did not itself decide

The premise that the step is "downstream of decisions it made itself" is false as a statement about
the graph. 3.03 has six inbound edges - `assign_aroll`, `select_broll`, `mesh_spine`,
`creative_direction`, `speech_sequence`, `temporal_index` - and no outbound edge reaches any of
them; its outputs go only to phase-4 nodes. There is no cycle to break.

What is true is narrower and worse.

**Five of its six mechanical checks read a copy.** `step_3_01_assign_aroll` is `deterministic` and
its assignment is a field-for-field restatement of the spine block - `video_in =
float(block["source_start"])`, `"timeline_start": block["timeline_start"]` - and it then runs the
duration invariant itself, at a 0.15 s tolerance, printing a warning. 3.03's Check 1 re-runs the
same arithmetic on the same numbers at 0.1 s and rejects. Two readings of one producer's output. A
real gate, and no new information.

**The one check that reads an independent measurement had its input deleted before the prompt.**
Check 5 - which the handoff itself calls *"the most important step"* of Part 2 - says the
reconstructed script *"must be derived from actual temporal index data, not from the
speech_sequence's intended text"*. `temporal_index` is edge-routed to the step and declared
optional, and `context_fields` did not select it, so `project_step_context` dropped it. Replayed at
`origin/main`, 3.03's context keys were:

    a_roll_assignments, audio_spine, b_roll_assignments, b_roll_interjections,
    creative_direction, project_folder, render_qa_findings, rough_cut_review, speech_sequence

Every one of those is a decision an upstream step made. The reviewer was asked to compare what the
viewer will HEAR against what the plan INTENDED, and was handed only the plan - and the handoff
forbids using the plan for it. It was recorded in `input_contract.UNCONSUMED_DECLARATIONS`.

`view:transcript` is the fix, and it is the route `creative_direction` already uses: region-level
text with timings, no per-word records (§10.1). 3.03's context: **34,565 B -> 44,628 B (+10,063 B)**,
one new key, `transcript`. That is the first byte of change in that step's context across thirteen
PRs.

**PR #332's QA findings are genuinely independent and are not enough on their own.**
`render_qa_findings` is measured on a rendered file by step 6.02, not decided by anyone upstream. But
it describes the LAST render, so on the run that matters it is `source: none, total: 0` - which is
what it is on 001's snapshot.

**What is left is not in the graph; it is in who answers.** Under `--full-auto agy` one agent answers
2.01, 2.02, 2.05, 3.02 and then 3.03, so the reviewer is the author. §10.1 already names this
failure mode ("a run answered end to end by one context carries facts forward in the answering
agent's head"). No rewiring closes it - it needs a different answerer for the review step, which is
a redesign and was not taken here.

### Not measured

001 was not re-run and not re-rendered. Every number above is from the step-replay bench against
001's frozen 2026-08-26 snapshot, and from the recorded `cut_decisions` in that snapshot's own
`pipeline_data.json`. Whether a transitions plan made with the verdict column differs from one made
without it is an answer-side question the bench does not answer.

**Moved out of the rule (AGENTS.md 10.4) on 2026-08-29.**
Until `cut_verdicts` existed, `cut_decisions` appeared once in the whole repository - in its own
manifest, declared with a type and no description - so the injected schema was the bare line
`"cut_decisions": []`. On 001's run of record the model answered it with **19 rows, 9,498 bytes**, and
one of them diagnosed the passage mis-anchor by name, with its cause, its owning step and its fix. All
of it was discarded.

`plan_subtitles` (4.01) cannot be the reader because it is `deterministic` and has no `handoff.md`.

A finding owned by step 2.02 is not made actionable by putting it in the transitions prompt - that is
`cohesion_scope.OWNED_UPSTREAM`'s shape - which is why the no-cut half goes to the run summary.

3.03's projection used to delete `temporal_index`, so the step whose Check 5 says the script "must be
derived from actual temporal index data, not from the speech_sequence's intended text" was handed
nothing but the plan it was reviewing. Its five other inputs are all upstream decisions, and
`a_roll_assignments` is a field-for-field copy of the spine whose duration invariant step 3.01 already
ran.

## the-guard-that-could-not-see-a-deterministic-step

**The rule:** AGENTS.md §3, "A declaration must be true".

`library/tools/input_contract.py` reads "does the prompt consume this input" off `context_fields`,
the same allow-list `run_pipeline.project_step_context` applies. A step declaring none is handed
every byte it was routed, so an absent declaration answers True.

**A step with no `handoff.md` declares none for the opposite reason: it has no prompt to project.**
`_reaches_prompt` could not tell the two apart, so it answered True for every declared input of
every prompt-less step, and `consumed` - `code_reads or prompt_reads` - could never be False there.

Fifteen of the DAG's twenty-seven steps have no prompt. **86 of the survey's 148 rows belong to the
twelve that do; the other 62 were inside the hole.**

### The one that was found by hand

#330 found it the next day, without the survey. Step 4.06 `render_motion_graphics` declares
`creative_direction` and `enhancement_spec` REQUIRED, the DAG routes both, and
`generate_motion_props` reads neither: 4.03 emits `visual_effects` alone and 2.01's eight fields are
prose. The consequence was the captain's week-old complaint - no motion graphic has ever reached a
frame - and nothing mechanical could say so.

Making `prompt_reads` honest is not enough to catch that one. `step.py` really does name both keys:

```python
enhancement_spec = data.get("enhancement_spec", {})       # step.py:121
creative_direction = data.get("creative_direction", {})   # step.py:123
...
props_list = generate_motion_props(
    enhancement_spec, creative_direction, audio_spine, ...)   # step.py:229
```

and `generate_motion_props` never mentions either parameter again. So `code_reads` - "the step's own
Python names this key" - is True for both, and the row would still have surveyed as consumed.

**Naming a key is not reading it.** `trace_step_values` is the second half: where the step's own
files obtain the value, does it REACH A USE. It is asked only of prompt-less steps, because a step
with a prompt has a consumer either way and the question decides nothing there.

### What it found, and the verdict on each

Ten declared inputs across six steps. Every one predates the change.

| step | input | decl | basis | real? |
|---|---|---|---|---|
| `color_grade` | `creative_direction` | required | never named | yes - 5.01 reads exposure and the declared look; the direction reaches nothing |
| `plan_subtitles` | `rough_cut_review` | required | never named | yes - no mention in `step.py` |
| `plan_subtitles` | `speech_sequence` | required | never named | yes - named only in the module docstring's `Input:` block |
| `render_motion_graphics` | `creative_direction` | required | bound at `step.py:123`, never used | yes - #330's case |
| `render_motion_graphics` | `enhancement_spec` | required | bound at `step.py:121`, never used | yes - #330's case |
| `audio_mix` | `creative_direction` | required | never named | yes - the mix is four constants and a spine (N2) |
| `audio_mix` | `enhancement_spec` | optional | bound at `step.py:111`, never used | yes - passed to `define_audio_mix(audio_spine, enhancement_spec)`, whose body never touches the parameter |
| `audio_mix` | `music_selection` | required | never named | yes - no mention in `step.py` |
| `creative_cohesion` | `enhancement_spec` | optional | never named | yes - 5.03 reads `transition_spec` and `sfx_spec`; the VFX plan it is documented to review never arrives |
| `compile_manifest` | `temporal_index` | optional | never named | yes - the step loads `temporal_index.json` off disk; the routed state value is unread. Same shape as the two already in `UNCONSUMED_DECLARATIONS` |

Three of the ten are the value-goes-nowhere kind that an honest `prompt_reads` alone would still
have missed.

### Why it reports rather than fails

All ten predate the change that made them visible, and none is fixed here - fixing each is separate
work. `disagreements` is the failing set and is unchanged: it still fails on an unenforced
requirement, on an optional input the step refuses, and on an unconsumed declaration of a step WITH
a prompt. `unread_by_a_prompt_less_step` is the report, printed loudly on every run in the shape
#320's own check uses.

### What it still cannot see, stated in the output

- **A step WITH a prompt is judged on whether its code NAMES the key**, not on whether the value
  goes anywhere. The prompt consumes it either way, so the dataflow question decides nothing there -
  and asking it would change the survey's verdict on all 86 LLM-step rows, which is a separate
  decision.
- **The value read is one-sided by design.** `_UNTRACEABLE` is the enumeration: a value handed to
  anything but a plain function the step's own files define - a method, an imported library tool, a
  builtin - is read as USED; a value rebound to a second name is read as used rather than followed;
  and it is one hop, so a parameter the callee passes on again is used wherever the callee names it.
  A dataflow read that guessed would report findings the code does not have, which is the failure
  mode this whole section exists to remove.

### Measured

86 of 148 rows - every row of all twelve prompt-carrying steps - are byte-identical before and
after. The 62 that changed all belong to the fifteen prompt-less steps.

**Moved out of the rule (AGENTS.md 3) on 2026-08-29.**
Reading a prompt-less step's absent `context_fields` as "handed every byte" answered `prompt_reads`
True for every input of all fifteen prompt-less steps, so `render_motion_graphics` declared two inputs
REQUIRED, read neither, and surveyed clean.

All ten findings of the `unread_by_a_prompt_less_step` report predate the change that made them
visible, which is why that half reports rather than fails - escalating a pre-existing finding is the
captain's call.

## The VFX plan that was always empty

Step 4.03 `plan_vfx` has emitted `{"visual_effects": []}` on every run the repository can show,
and #330's motion-graphics diagnosis established that `generate_motion_props` reads that
`enhancement_spec` and gets an empty list every time. Four things could explain it, and they need
four different fixes - or none:

  a. nothing asks the step for effects in a form it can answer;
  b. it answers and the answer is dropped;
  c. the effects vocabulary it could name does not exist;
  d. it decided none, every time, on the merits.

### The verdict: (d) at the run of record, with a residual (b) that hides it

**(c) is refuted by a test that already existed.** `tests/test_vfx_delivery.py` parametrises the
five toolkit effects over the three intensities and asserts each draws real Fusion nodes through
`build_effect_comp`, and `test_the_handoff_offers_exactly_the_effects_the_bridge_resolves` pins the
handoff's table against `INTENSITY_MAP`. The vocabulary exists, is complete, and is drawn.

**(a) is refuted at HEAD and was TRUE at the run of record.** `replay_bench replay
001-pre-repass-20260829 plan_vfx` reconstructs a 60,142 B context whose `vfx_candidates_toon` is
16 rows carrying each block's spoken line, its duration and what the camera does
(`16.0s, tilting_up, shaky`; `8.7s, stationary, stable`). At the 2026-08-26 run it was 11 rows with
every `text` blank and every `vfx_suggested` the literal string `"No"` - a column the prompt
described as "pre-computed ... based on motion/pose data" and which was based on nothing. The
planner read the source and said so: `docs/run-001-reasoning/plan_vfx.md` §2. The bridge repair
closed it.

**(b) is refuted for the resolution path.** Feeding the real post-bridge a two-entry plan against
001's own frozen spine resolves both, with the block positions turned into timeline ranges and the
intensities into `zoom_start`/`zoom_end`. `tests/test_vfx_reaches_the_manifest.py` carries one the
whole way: post-bridge subprocess -> `enhancement_spec` -> the real `compile_manifest` ->
`manifest["vfx"]` -> `fusion_effects.per_clip` -> a comp string with a `Transform` in it.

**(d) is what the one run of record shows.** The planner wrote its reasoning BEFORE answering,
applied the handoff's own "long AND static" criterion to eleven blocks, found two candidates
(block 7 at 15.99 s, already `tilting_up, shaky`; block 14 at 8.67 s, `stationary, stable`),
rejected both with stated reasons, and recorded the counter-argument against itself: *"shortform
convention is that the frame is always moving ... a sixty-second edit with zero VFX may simply read
as flat on a phone."* It then held that answer across three `semantically empty` rejections from
`validate_step_output`, and wrote down that *"an agent behaving more agreeably would have produced a
different video."* #275 closed that pressure with `may_be_empty: true`.

So nothing about (d) is broken. **What is broken is that (d) is unreadable.** Run the post-bridge
twice - once on `[]`, once on four entries naming a withdrawn alias, an unknown type, a block that
is not on the spine and an intensity outside `subtle|moderate|strong` - and both print exactly
`{"enhancement_spec": {"visual_effects": []}}`. The four drop sentences went to stderr, which is
`pipeline_output/logs/run_*.log` and nothing else; §13's ruling on the bookend that vanished into a
log line applies unchanged. An edit could ship with no effects that nobody decided to leave out.

### What was built

`library/tools/vfx_plan_basis.py` and `enhancement_spec.planning_basis`. The two empty plans are now:

    {"basis": "no_effects_planned",  "proposed": 0, "resolved": 0, "dropped": []}
    {"basis": "every_entry_dropped", "proposed": 4, "resolved": 0, "dropped": [ ...four... ]}

`compile_manifest` reads it and names the casualties, because that is where the absence becomes
final. It fails on none of them: `may_be_empty: true` is untouched, and whether a dropped entry
should refuse the step the way an unplayable sound refuses 4.04 is recorded in
`THE_REFUSAL_QUESTION` rather than settled here.

**Not measured, and stated as such:** whether the planner still answers empty now that the
candidate table is real. That needs a run of step 4.03, and 001 was not re-run, not re-rendered and
its timeline not opened. Every number above is `replay_bench` reconstruction, a post-bridge
subprocess, or arithmetic over frozen JSON.

**Moved out of the rule (AGENTS.md 10.2) on 2026-08-29.**
`{"visual_effects": []}` is what 4.03 has emitted on every run in the repository, and it read the same
whether the planner chose stillness or named four effects the post-bridge discarded - the drop reasons
went to stderr and nowhere else.

`no_effects_planned` versus `every_entry_dropped` is the same distinction `cutaway_window.BASES` draws
between `moment_match` and `single_span`.

The vocabulary was never the problem: a planner naming one of the five toolkit effects at one of the
three intensities resolves, reaches `manifest["vfx"]`, reaches the V1 clip's Fusion comp and draws
nodes. What 001's run of record shows is a planner deciding none on the merits, with its reasoning in
`docs/run-001-reasoning/plan_vfx.md`.

## what-searching-for-music-costs

Evidence for AGENTS.md §10.5, "Search is one enumeration" and "Every candidate is MEASURED".
Moved out of those rules on 2026-08-29.

**Cost, measured 2026-08-28**: two queries at six results, fetching three, was 3.5s of search, 10.5s
of download and 46.2s for the whole bridge.

Judging duration off the search metadata before downloading anything dropped four multi-hour
compilations at no cost on the run of record.

Not opening a candidate the duration check already rejected is what keeps 001's two compilations from
costing 325s of `loudnorm` to learn nothing.

**What the measurements cost the prompt.** On 001, candidate data went from 3.8% of step 2.04's
context to 28.0%, and the room for it came from #295 no longer copying the creative brief in. Every
column in `MEASUREMENT_LEGEND` is paid on every candidate, which is why widening the table is not a
way to improve the prompt.

## a-third-of-the-choice-set-was-a-copy

Evidence for AGENTS.md §10.5, "Two candidates that are the same recording".
Moved out of that rule on 2026-08-29.

Two of 001's four surviving candidates were one recording, so a third of the choice set was a copy and
nothing said so. The defect was that the model could not tell - which is why a duplicate is MARKED
rather than dropped; removing a row would have the pipeline choosing which encode the captain gets.

**The tolerance is measured, not picked.** 1.0 dB is twice the worst difference across mp3 128k/320k,
opus 96k and aac 128k re-encodes of this repository's own tracks. The nearest non-duplicate pair - the
same song's lyrics and instrumental versions - is an order of magnitude further apart.

`true_peak_dbtp` is not compared, and `DECLINED_SIGNALS` says why: lossy coding moves it most and it
says least.

## the-splices-that-reached-nothing

Evidence for AGENTS.md §10.5, "Which SECTION of the track plays".
Moved out of that rule on 2026-08-29.

`compile_manifest` used to write `source_in: 0.0` as a literal, so the `splices` that step 2.04's
frozen `handoff.md` has always asked for reached nothing. That is AGENTS.md §10.2 exactly - a
capability is only real where the renderer reads it - arriving on the audio side.

## the-bed-was-fitted-from-the-wrong-second

Evidence for AGENTS.md §10.4, "The bed is fitted at the SECTION that plays".

Found on 2026-08-29 while rendering 001 for the captain to watch, from the render's own QA report.

`measure_speech_above_bed` least-squares fits the music file against the master, per window. It
sliced BOTH at the timeline time:

    x = mix[a:b]        # a, b from the window's timeline_start / timeline_end
    y = music[a:b]      # the same numbers, applied to the music FILE

That holds only while the bed plays from the head of its own file, which is what every 001 render
before this one did (`source_in: 0.0`). #F17 then gave step 2.04 a real choice of SECTION, the model
chose one, and 001's bed now plays from **60.0 s** into a 198.6 s track. The fit was therefore
correlating the render's first minute against the track's first minute, while the render actually
carries the track's second minute.

What that did to the reading, measured on the 2026-08-29 19:01 master:

| window | corr, offset 0 | music, offset 0 | margin | corr, offset 60 | music, offset 60 | margin |
|---|---|---|---|---|---|---|
| 0.00-2.40 | -0.014 | -62.6 | 37.2 | **0.381** | -33.8 | **7.7** |
| 5.40-8.38 | -0.002 | -74.1 | 53.6 | **0.241** | -32.9 | **12.1** |
| 8.38-18.43 | -0.002 | -75.9 | 56.3 | **0.263** | -31.2 | **11.3** |
| 21.93-32.08 | -0.004 | -68.3 | 47.1 | **0.215** | -34.5 | **13.2** |
| 35.08-38.62 | -0.010 | -60.1 | 39.8 | **0.141** | -37.3 | **17.0** |
| 38.62-41.32 | 0.021 | -54.0 | 33.5 | **0.239** | -32.9 | **12.2** |
| 44.32-51.14 | 0.010 | -63.8 | 39.7 | **0.437** | -31.2 | **6.3** |
| 51.14-52.60 | -0.030 | -52.7 | 30.5 | **0.296** | -32.7 | **10.2** |

Every correlation was |r| <= 0.03 - the signature of fitting two unrelated stretches of music - the
fitted bed collapsed to -53..-128 dB, and the check reported **8 of 8 speech windows meeting their
18 dB target**. The true answer at the same revision, on the same file, is **0 of 8**, worst
+6.3 dB. The three `prominent` windows correlate 0.91-0.95 once the offset is applied, which is what
establishes that the fit itself was always sound.

This is a gate that cannot fail (AGENTS.md §10.4) reached by a new route: not a threshold nobody
could trip, but a measurement quietly aimed at the wrong data. The correlation column was printed
beside every window the whole time and nothing read it.

The repair is the shape `beat_grid` already had. `beat_positions`/`downbeat_positions` take the
section and return TIMELINE time, and their offset argument is required precisely because a default
of "no offset" is the value that is silently wrong. `measure_speech_above_bed` now takes
`music_offset_seconds` positionally with no default, records it on the result beside the windows,
and `run_full_render_qa` declines to run P3 at all when it is None rather than assuming the head of
the file. Step 6.02's `_music_bed` computes it as `source_in - timeline_in` off the first A2 clip -
the same two numbers `compile_manifest` placed it with.

`tests/test_baseline_craft_properties.py::TestP3SpeechAboveBed` keeps the defect executable: one
test fits a master carrying the second half of an 8 s music file at the right offset and gets
|r| > 0.5 with a 2 dB margin, and its sibling fits the same master from 0 and asserts the old
reading - |r| < 0.1, a margin over 20 dB, and `meets_plan` True.

---

## The bed was trimmed to the last V1 clip

Audit round 3, project 001's run of record (LLM traffic 2026-08-29 12:25-12:42, render 19:01).

`compile_manifest/step.py` clamped every A2 music clip to `max(V1.timeline_out)`. 001's outro is
a **V2** cutaway, so V1 ends at 52.605 while the picture runs to 56.605:

```
max V1 timeline_out: 52.605      max V2 timeline_out: 56.605
A2 as shipped      : timeline 0.0-52.605, source 60.0-112.605
project duration   : 56.605
music_automation last window:
  {"spine_block_position": 12, "timeline_start": 52.605, "timeline_end": 56.605,
   "music_behavior": "fade_out", "target_level_db": -12,
   "bed_level_after_gain_lufs": -27.17}
```

The spine declared `fade_out`, step 5.02 wrote the automation for it, and this clamp then deleted
the music the automation was written for. Measured on the shipped file with `ffmpeg volumedetect`
per spine block, the last four seconds are **mean -91.0 dB, max -60.5** - absolute digital
silence, 4.0 s of a 56.6 s video. Round 3's independent re-fit of the bed against that window put
the music at **-142.36 dB with a correlation of 0.005**: not present at all.

The only notice was `WARNING: Trimmed music to match last V1 clip end (52.605s)` on stderr,
forty minutes into an unattended run - the pattern §13 already rules against for bookends.

It compounded a decision the model made on the strength of it. `select_broll` chose a knowingly
soft window for the outro and said why: *"I chose it anyway for the outro specifically because it
is the one block where softness is least costly: **the bed is fading**, the video is ending."*
The bed was not fading.

The bound is now the picture - V1 and V2. On 001's own manifest that puts the bed's
`timeline_out` at 56.605, which is exactly where `music_automation`'s last window already ended
and what `project.duration_seconds` already said.

`tests/test_compile_manifest.py::test_the_bed_is_bounded_by_the_picture_not_by_v1` builds a spine
whose last block is a V2-covered non-speech beat and fails on the old bound.

---

## The residual nobody read

Step 1.04 writes `camera_motion_decomposition`, with the optical-flow residual per sample inside
`values`. `compute_deterministic_assessment` read `temporal_index["camera_motion"]["residual"]`.

```
$ python3 -c "... print(ti.get('camera_motion'))"    # every clip of 001
camera_motion key None on: 17 of 17
```

So the one signal that separates camera shake from subject movement had never been read on any
clip of any run, and every `camera_stability` label the project ever shipped came from the
fallback: the standard deviation of `motion_energy`, which is frame differencing.

The test that covered it passed throughout, because its fixture was written in the same wrong
shape as the reader.

**What the label got wrong.** Round 3 measured global per-frame translation on the render itself
by phase correlation: clip_017 0.25 px/frame, clip_011 2.09, clip_012 2.86. The unread residual's
ordering (0.0326 < 0.0769 < 0.2382) matches. The shipped label inverted it, calling clip_017 -
which carries 20.2 s of a 56.6 s edit - `unstable`, while the VLM in the same run called it
`stable` and `plan_vfx`, which reads the VLM, put both of the video's effects there for being
still.

**Why the old thresholds could not simply be reused.** 0.02 / 0.08 were written for a signal that
never arrived. The residual's own range on 001 is 0.0051 to 0.3706, quartiles 0.0769 / 0.2382 /
0.3094, so those numbers sit at 0.16 and 0.64 of a single step of the instrument's own search
grid and would call 12 of 17 clips `unstable` including the two the render measurement says are
the steadiest.

**Where the new numbers come from.** The instrument, not the distribution. Step 1.04 block-matches
a 160x90 luma frame at 5 Hz over `dx, dy in {-8, -6, ..., 8}`, then divides by 8, so the smallest
displacement the search can report on one axis is 2 px of 160 - 1.25% of the frame width per
0.2 s - which arrives as `dx = 0.25`. With
`residual = max(0, magnitude - 0.5 * (|dx| + |dy|))` that is a residual of `0.125`, and a sample
where the search found nothing is exactly `0.0`. The tiers are therefore half a grid step
(`STABLE_BELOW = 0.0625`) and one grid step (`HANDHELD_BELOW = 0.125`) of MEAN per-sample global
displacement.

**The check, run on 001** (`MEASURED_ON_001` records it):

| | before (frame differencing) | after (residual) |
|---|---|---|
| clips where the residual was read | 0 of 17 | 17 of 17 |
| outright agreements with the VLM | 1 of 17 | 10 of 17 |
| hard disagreements (one says steady, the other does not) | 9 | 5 |
| clip_017 | `unstable` | `stable` |

**The honest limit.** One project is a thin basis for a threshold and no render has been made
against these numbers. They are grounded in the search grid rather than in this distribution,
which is why the distribution is attached as a check rather than as the derivation, and
`MEASURED_ON_001["caveat"]` says so.

The two signals still differ where both are real, and no step was told the other answer exists:
`view:stability` is that, as data with a legend. It resolves nothing.

---

## The occupancy gate failed a correct render

001's shipped master is correctly built - 8 letterboxed A-roll placements at
`framing_delivered 0.0`, 5 portrait cutaways at 1.0 - and `validate` refused it on
`frame_occupancy`. Two independent defects in one check.

**Caption ink read as picture.** A caption is drawn over the bars as well as over the picture and
its ink is neither dark nor flat, so the bar walk stopped at it. Sampling the render at 2 Hz and
walking the rows:

```
 t=7.5   top=656  bottom=656   frac=0.3167   (no caption)
 t=12.0  top=656  bottom=347   frac=0.4776   (caption)
```

on a picture that never changes size. 84 of 113 samples read a bottom bar of 347-368 rows instead
of 656.

**Samples attributed to the wrong clip.** `_stream_raw_frames` asked ffmpeg for `fps=2`. That
filter maps each INPUT frame to an output slot by ROUNDING its timestamp and emits the LAST frame
to claim the slot, so under the default `round=near` a sample can carry a frame from up to half a
sample period after its label. Measured on the master with `showinfo`: the true cut into the
block-6 cutaway is at `pts_time 32.066667`, and the sample labelled 32.0 came back with a frame
mean of 103 - the bright cutaway - where the frame really at 32.0 has a mean of 33. `round=up` is
the only mode whose sample at 32.0 carries the frame at 32.0.

**The verdict, same render, same manifest:**

```
before  passed: False
        framing 0: n=81 median 0.4755 min 0.3167 max 1.0000 spread 0.6833
        framing 1: n=32 median 1.0000 min 0.4729 max 1.0000 spread 0.5271
        picture_band_first_frame [656, 1551]
        detail: "the picture changes size within one declared framing (0):
                 31.7% at 7.5s vs 100.0% at 32.0s (spread 0.68, bound 0.05)"

after   passed: True
        framing 0: n=81 median 0.3167 min 0.3167 max 0.3167 spread 0.0000
        framing 1: n=33 median 1.0000 min 0.9865 max 1.0000 spread 0.0135
        picture_band_first_frame [656, 1263]
```

The bars are now measured on the columns a CENTRED overlay cannot reach -
`safe_area.centered_usable_width`, 120 px each side at 1080x1920 - and `safe_area_for_frame`
raises on a size no delivery format has rather than borrowing the nearest profile. A frame no
format describes is measured full width and the result records that it was.

**That blind spot was not a corner case, and it reopened the defect twice more.** The strip mask
assumed a CENTRED overlay. Every element `remotion-subtitles/src/compositions/MotionGraphics/index.tsx`
draws is laid out from the safe area's own edges - `left: safeArea.left` (90), `right:
safeArea.right` (120) on a 1080x1920 delivery - so a progress bar starts 30 columns inside the
120-column strip before its 16px box shadow is counted at all. The progress bar reopened it
(item 11 of `data/vep-full-run-after-the-waves/merged-today-breakage.md`); PR 462's emphasis
elements reopened it a third time. A first attempt at a fix narrowed the strips to the asymmetric
insets, which addresses an asymmetric safe area and not the centred-overlay assumption, and was
rejected on review.

Measured on 001 with the four elements really rendered by step 4.06 and composited onto the
shipped master, the drawn alpha reaches **column 71**: 3,149 inked pixels inside the 120-column
strips the mask kept, and 734 inside the 90/120 asymmetric strips the rejected fix proposed.
Neither strip is free of it, and no strip can be, because a glow's footprint is not in the CSS.

```
before  passed: False   (origin/main, 001's master WITH motion graphics drawn)
        picture occupies 77.8% of the frame (spread 0.68 over 114 samples; 2 declared framings)
        - the picture changes size within one declared framing (0): 31.7% at 0.0s vs 77.8%
          at 2.0s (spread 0.46, bound 0.05) - one framing has one geometry
        framing 0: n=81 median 0.7776 min 0.3167 max 0.7776 spread 0.4609

after   passed: True
        picture occupies 31.7% of the frame (spread 0.68 over 114 samples; 2 declared framings)
        framing 0: n=81 median 0.3167 min 0.3167 max 0.3167 spread 0.0000
        framing 1: n=33 median 1.0000 min 1.0000 max 1.0000 spread 0.0000
        9 overlay segments handed to the check, 114 samples carrying ink, 5.9 s
```

**The answer is not a fourth guess: the pipeline knows exactly what it drew.** Every overlay is a
transparent segment the manifest places on the timeline, and its own alpha names the pixels it
touched - glow, shadow, backdrop blur and shapes nobody has invented yet. Step 6.02 hands those
segments to `measure_frame_occupancy` and the ink is masked per row.

Two details the measurement forced. A row the ink leaves under `MIN_OVERLAY_FREE_COLUMNS` pixels
of is UNREADABLE, and is attributed to whichever side the walk resolves to next rather than
counted as bar - counting it as bar shrinks a FILLING picture by exactly the height of the
overlay drawn over its bottom edge. And the overlay is TRIMMED to its offset, not `-ss` seeked:
input seeking rebases the stream's timestamps and the sample grid is then laid out from the seek
point, which on 001 read sub_block_8's alpha in the blank gap BETWEEN two caption cards while the
master was showing one, leaving 0.4755 in a group whose real value is 0.3167.

**The gate is not weakened.** The same master with one ten-second stretch of its picture really
rescaled still fails, with the overlays present and masked: framing 0 spread 0.29 and framing 1
spread 0.22, both against the unchanged 0.05 bound.

**It also settles the other open question.** 001's two declared framings are correct: 0.3167 for
the letterboxed A-roll and 1.0 for the portrait cutaways, each with spread 0.0000 over its own
samples. The 31.7%-to-100% spread the failure reported was the check misreading its own render,
not a framing change.

**`subtitle_gaps` had the mirror defect**: it counted the plan's own non-speech blocks as dead
caption time. All four of 001's reported gaps - 3.0, 3.5, 3.0 and 3.0 s - are the B-roll breaths
at spine blocks 1, 4, 6 and 9, and since #332 the finding reaches a creative step carrying a
legend that blamed the aligner. Caption coverage inside the speech blocks is 33.80 s of 40.10 s =
84.3%, and the uncaptioned stretches there run 0.40-1.12 s: real pauses in the speech. The check
reads the spine now; 4 findings became 0 on the same render, and a caller with no spine gets the
old measurement and a result that says which it made.

## the-panel-handed-the-model-a-filename

**The captain, 2026-08-30**, standing on a clip in DaVinci Resolve and asking the scout's
panel *"what does this clip where my playhead is at show?"* over IMG_1806.MOV:

> I can see the metadata - the clip is IMG_1806.MOV, a raw video file from your footage
> directory. However, I cannot actually view the video content.

**The model was not wrong. The panel had handed it a filename and nothing else.** Its
`read_context` attached Resolve-side facts only - clip name, timeline frames, source offset,
source path, markers - and nothing the pipeline already knew about that clip, all of it on
disk and already indexed.

### The before and after, same question, same clip, same model

Asked twice on the timeline the captain had open (`Pipeline_Edit_2`, a build of 001) at
00:00:13:04, where the picture is IMG_1822.MOV playing source frames 902-1204. Same model
(`claude-haiku-4-5-20251001`), same question, minutes apart.

**BEFORE** - 501 characters of context, the Resolve-side facts alone, 9.7s:

> The clip is **IMG_1822.MOV**, sourced from your raw footage. It's playing at timeline position 13:04, spanning frames 251–553 of the timeline, starting from frame 902 of the source file.
> 
> However, I don't have access to the **semantic analysis** (vision measurements) for this clip—what the scene shows, the camera work, objects, or assessment. That data lives in the pipeline's step outputs, not in what I can see here.
> 
> To find out what the clip contains, check the **Footage Library** panel on the dashboard, or look at `pipeline_output/steps/1_03_semantic_analysis/` in your project folder for the vision observations captured during the pipeline run.

**AFTER** - 2594 characters, the same facts joined to what the pipeline measured, 11.1s:

> You're looking at a medium-framing shot of a young man in a black baseball cap inside a dimly lit car. City buildings with lit windows are visible through the window behind him. The vision pass marked it **unstable** despite calling the activity stationary—worth noting if the movement looks wrong.
> 
> The speech playing here is mid-passage: *"i'm happy that i at least recorded this i almost didn't do this again..."* He's talking about quitting every day, deciding this is his last shot, and comparing it to when Casey Neistat posted his first vlog 11 years ago.
> 
> This is a **chosen** clip from the speech_sequence step (step 2.02), so if the take or timing feels off, that's the step to note in a marker.

The second answer is not longer because the prompt is bigger. It is 2,594 characters against
501 - five times the context and a third of the words back - because the context is
SPECIFIC. Every fact in it is a reading the pipeline already made: `semantic_index.clip_observations`
for the shot, step 1.04's `speech_regions` clipped to the seconds this placement plays, and
`timeline_decisions.placement_for_clip` for which step chose it. Nothing was pasted in whole.

### Two failure modes the one screenshot showed, and where each is fixed

**Ordering.** The model latched onto `0_01_validate_sfx_library` - the step the editor happened
to have open in another tab - because it was the most concrete thing in the prompt.
`clip_context.prompt_block` now leads with `WHAT THE EDITOR IS LOOKING AT` and trails the open
step under `SECONDARY - what the editor last opened in the panel`, saying in the prompt that it
is not what the question is about.

**Attaching more of the same.** The answer to a thin prompt is not a bigger prompt. Every joined
fact is a READING and `PROMPT_BUDGET_CHARS` bounds the lot, saying what it cut.

### The picture is not the topmost item, and this cost the whole join

`Timeline.GetCurrentVideoItem()` answers with the HIGHEST video track. On 001's finished build
that is V3, so at 00:00:13:04 Resolve's "current item" is `sub_block_3.mov` - a subtitle card
the pipeline rendered. Joining THAT to the footage catalog finds nothing and reports an absence
about the wrong clip entirely. `clip_context.picture_at` takes the highest track carrying
FOOTAGE instead, and `overlays_at` reports the rest; an overlay is told apart by living under
`pipeline_output/`, which is a fact about the layout rather than a guess about a filename.
Measured live: the panel's header read `under playhead: IMG_1822.MOV` where Resolve's own
current item was the subtitle card.

### Resolve stays responsive, measured rather than asserted

An observer process timing Resolve on the wall clock while the panel was up, 303 probes over
61.9 s, across an idle phase and a slow-job phase (a 4.5 MB state read, the whole decision
ledger rebuilt, and a 10.0 s model call, all on worker threads):

```
phase         seconds    samples    p50 ms    p95 ms    max ms   panel/s
idle             18.0         88      1.32      2.26     10.78      41.7
slow job         10.0         48      1.47      2.55      2.65      41.5
whole run        61.9        303      1.37      2.44     65.17      41.6

0 errors, 0 null answers over 303 probes.
```

Resolve's latency does not move and the panel's own loop rate does not move: 41.7 passes/s idle,
41.5 while three slow jobs are in flight. The 65 ms outlier is outside both phases, during the
panel's own window creation. The heartbeat under the panel's header is the same number, shown to
the captain, so a stuck panel is visible rather than inferred.

### What the shipped panel does that the prototype refused to

The scout's prototype deliberately imported nothing from the repository, to prove the
no-dependency claim, and paid for it: it resolved a step directory to a node id by longest
match, gave three steps the wrong status and hid one of two failures on 001. The shipped panel
imports `project_layout.node_id_for`, and `tests/test_panel_boundary.py` fails if the widget
layer grows a second copy. The dependency claim is kept the other way: nothing under
`library/tools/panel/` imports Qt or Resolve, and `panel/strip.py` still encodes its PNG from
`zlib` and `struct`.

### The crash the panel was accused of, and the one it really had

**The captain, 2026-08-30**, opening the six screenshots this PR committed:
`1_trace_top.png` does not show the panel at all. It is DaVinci Resolve's media pool with a
macOS dialog over it reading *"Python quit unexpectedly."* - captioned in the PR body as
"28 steps in run order". The report asserted evidence it did not have.

**Seven `Python-*.ips` crash reports were on the machine. All seven are attributed.**

**Six of them, 14:13:09 to 14:13:26**, are `/Applications/Xcode.app/.../Python3.framework/3.9`
- that is `/usr/bin/python3` - faulting in `CoreFoundation`, `libffi` and
`_ctypes.cpython-39-darwin.so`. **None loads `fusionscript`.** They are a throwaway ctypes
window-lister in the scratchpad, run once per screenshot in the round where every capture came
back MISSING. Six invocations, six crashes. Nothing to do with the panel or with Resolve.

**One of them, 14:05:04**, is the dialog in the picture, and it is a process that HAD connected
to Resolve - homebrew Python 3.14, `fusionscript.so` loaded. Its two threads say exactly what
happened:

```
thread 0  com.apple.main-thread
    dyld  start
    libsystem_c  exit
    libsystem_c  __cxa_finalize_ranges
    fusionscript.so  Fusion::ReusePoolManager::~ReusePoolManager()
    libtbbmalloc.dylib  scalable_allocation_command

thread 1  RemoteApp                          <- faulting, EXC_BAD_ACCESS
    fusionscript.so  Fusion::RemoteApp::AppThreadFunc()
    fusionscript.so  Fusion::RemoteApp::DispatchPacket(Fusion::Packet*)
    fusionscript.so  Fusion::RemoteApp::FindLocalObject(unsigned long long)
```

**The main thread is inside `exit()`.** Python had finished; the work was done; dyld was running
static destructors. Blackmagic's own library tore down its `ReusePoolManager` while its own
`RemoteApp` thread was still dispatching a packet, and that thread dereferenced freed memory.

So the panel did NOT die during use. It died - sometimes - on the way OUT, in a race inside a
third-party library, after everything it was asked to do was finished and written. Every panel
session in this work returned from `run()` and printed its own "closed after N loop passes"
line.

**It does not reproduce on demand.** Fourteen attempts - six connect-and-exit, eight with a
worker keeping `RemoteApp` busy right up to the exit - produced 0 crashes. It happened once
across roughly a dozen sessions. The crash report is the evidence; a repro is not available.

**The fix is to decline to be in the room.** `_leave` flushes both streams and calls `os._exit`,
which hands the status to the kernel without running `__cxa_finalize_ranges`, so fusionscript's
static destructor never runs and the race has nothing to lose. That is safe in the entry point
and nowhere else: everything the panel writes is closed at the point it is written. It is not a
fix to Blackmagic's library.

**The exposure is not the panel's alone.** Every tool in this repository that calls `scriptapp`
and then exits has it - `marker_capture`, `marker_feedback`, `resolve_relinker`,
`timeline_serializer` and the rest. The panel is the one the captain WATCHES, so it is the one
where the dialog reads as a product defect.

### What the five other screenshots showed, that a reader would see

All real, all fixed in the same pass:

* the step list clipped `1_06_object_segmentat...`, `2_01_creative_directi...` and
  `4_06_render_motion_gr...`, and scrolled horizontally while carrying empty space. A step's name
  is the primary key of that view. Columns are now sized to the longest value they really hold -
  27 characters, measured off `project_layout.STEPS` - and `tests/test_panel_boundary.py` fails
  if a step name would need scrolling;
* the `ledger` column rendered as `ledge` and its values as `edit` and `pref`, which read as
  complete English words - a truncation indistinguishable from a value. It is out of the list and
  in the detail heading, whole, with the file count and size that were also being clipped;
* the preview column ran off the right edge mid-value. The preview string is now bounded to what
  the column can DRAW, so it ends in its own ellipsis;
* "Where it stops" was clipped at the bottom of the Run pane, mid-sentence. It is short and it is
  the answer to the tab's second question, so it leads, and the long step lists scroll under it;
* the profile table was two rows in a pane sized for thirty while the pane beside it scrolled.
  Both short lists - profiles and gates - are narrow now, and the gates list lost an `answer`
  column that said the same word as `status` for every state the protocol can produce;
* the status line read `no run up  resume, manual LLM, breakpoints at scan`, which is not English
  and sat a finished run's settings beside "no run up" as though they described something
  happening. It reads `No run is going.  Last run: ...` and names the mode as the LAST run's,
  which is what `pipeline_run.json` holds.

## the-model-was-told-a-filename-and-not-shown-the-frame

The join (`the-panel-handed-the-model-a-filename`) stopped the panel handing the model a
filename and started it handing over what the pipeline MEASURED. This is the other half of
the same steer: **a measurement reads the whole clip, and the captain is asking about one
frame.**

Nothing here was invented. The scout's report of 2026-08-30 had already proved every piece:
`marker_capture.grab_still` returns the graded, conformed timeline frame and restores the
gallery; an image reaches the model over the panel's existing keyless CLI path; and the one
thing that would silently break it is a working directory the CLI will not read out of.

### The wall, reproduced and closed, through the panel's own `ask_model`

Same PNG, same call, same model. The only difference is where the process ran.

```
image: /var/folders/.../wall_scratch_7thl7o2g/frames/ask_frame.20260830T193617354611Z.png

======================================================================
BEFORE - the call the panel used to make (cwd inherited)
  reaches_the_file() says: False
  cwd used: /var/folders/.../wall_elsewhere_znhmroat
  seconds: 6.5  ok: True
  --- answer ---
  I need permission to read the PNG file. Please grant access so I can open it and
  answer your question about the white vertical line and timecode.

======================================================================
AFTER  - frame_attach.call_site()
  reaches_the_file() says: True
  cwd used: /var/folders/.../wall_scratch_7thl7o2g/frames
  seconds: 11.5  ok: True
  --- answer ---
  I opened the file successfully.
  **What the white vertical line marks:** ... the white vertical line marks Resolve's
  live playhead position - the current frame being viewed.
  **Timecode printed beside it:** The panel header shows the playhead is at 00:00:13:04.
```

**`ok: True` in both.** The CLI exits 0 and writes a plausible English sentence, so nothing
downstream can tell the failure from an unhelpful answer - which is why the fix is a test
that fails against the old call shape and not a code comment.

Two routes close it and either is fine. `cwd` was chosen because the panel had never set
one at all, so the call inherited whatever Resolve was launched with; `--add-dir` is a flag
on a CLI whose interface is not ours, and a renamed flag fails exactly as silently as no
flag. `frame_attach.reaches_the_file` is what the test asserts, so what is pinned is *the
call can read the still* - `--add-dir` satisfies it too.

### The before and after, same question, same clip, same model

`Pipeline_Edit_2` at 00:00:13:04, picture IMG_1822.MOV, `claude-haiku-4-5-20251001`, minutes
apart. Question: *"Is the person in this shot sharp and well framed, or is something wrong
with the picture? Answer about what is on screen at the playhead."*

**Without the frame** (3,500 characters of context, 10.1 s):

> ... **the pipeline did not measure sharpness or focus quality.** It observed the shot as
> "dimly lit interior" with medium framing and a person talking to camera, but could not
> assess whether focus is sharp or the composition is well-balanced - that's a visual
> judgment call on your end. ... **Look at the frame yourself to judge focus and
> composition.**

**With it** (4,240 characters, 15.0 s):

> The person is **sharp and well-framed**. On screen: the subject's face is acceptably
> sharp - eyes, features are clear. Medium framing is good: chest-up composition with
> natural headroom, centered in the car interior context. The lit building visible through
> the window adds environmental depth. ... Minor note: the left hand shows some motion blur
> from gesturing, but that's typical for a talking-head shot.

The still was opened and read before this was written. It is 1080x1920 - the delivered
vertical frame with the burned-in caption on it, not the 1920x1080 source - and every claim
checks out: a car interior at night, chest-up centred framing, a lit building through the
window on the left, and a raised gesturing hand that is blurred while the face is not.
**The answer without the frame is not wrong. It is a correct refusal, and the frame is what
turns it into an answer.**

### What the picture costs

Measured on the run above: **the grab is 0.2-0.3 s** and the model call goes from 10.1 s to
15.0 s, so about **5 seconds a question**, plus ~740 characters of prompt. It is attached on
every ask rather than on request, because a question about the picture is the common case
and a five-second cost the captain did not choose beats an answer that says "look at it
yourself". `VEP_PANEL_NO_FRAME=1` declines it with no change to a panel the captain has
already seen.

Disk is the other cost and it is not small: an exported still is a FIXED 6,232,792 bytes off
this timeline whatever is in it, so the scratch is pruned to `KEEP_FRAMES` (10, ~62 MB).
The stills are not overwritten into one file, because an answer names the picture it was
given and two asks a second apart would leave the first answer pointing at the second
question's frame.

### The loop kept moving, measured rather than asserted

The real panel, its real widgets, its own `on_ask` handler, printing its heartbeat each
second. Idle is ~37 passes a second; the worst second with the grab and the call in flight
was 31.

```
[  4.0s] --- pressing Ask (beat 140) ---
[  4.2s] beats=141    jobs in flight=1   askstate: grabbing the frame, then asking claude-haiku-...
[  9.3s] beats=326    jobs in flight=1   askstate: grabbing the frame, then asking claude-haiku-...
[ 14.3s] beats=511    jobs in flight=1   askstate: grabbing the frame, then asking claude-haiku-...
[ 15.3s] beats=545    jobs in flight=0   askstate: answered in 10.8s by claude-haiku-4-5-20251001
                                                  - frame at 00:00:13:04 attached
beats per second: [35, 37, 37, 31, 38, 37, 37, 37, 36, 37, 37, 36, 37, 38, 34, 38, ...]
MIN beats in any one second while a job was in flight: 31
```

Before and after the grab the gallery read `Stills 1: 0 stills` and the timeline still had 8
V1 and 5 V2 items with the playhead where the captain left it. `marker_capture` puts the
gallery back, and this route inherits that.

### A grab that cannot happen still asks the question

```
frame.attached: False
describe(): no frame attached - frame attachment is off for this panel (VEP_PANEL_NO_FRAME is set)
--- what the model is told ---
THE FRAME THE EDITOR IS LOOKING AT

No frame was attached to this question: frame attachment is off for this panel
(VEP_PANEL_NO_FRAME is set).
Answer from the measurements below alone, and say plainly that you could not see the picture.
--- answer ---
I couldn't see the picture - frame attachment is off for this panel.
Based only on the clip metadata, IMG_1822.MOV appears to be a dimly lit car interior ...
```

A playhead over a gap, a timeline with nothing open and a still Resolve declines are all
ordinary, and none of them is worth failing a question over. Every refusal `marker_capture`
raises becomes that stated reason.

### How the model is told what it is seeing, and why in those words

Three sentences, each against a way the answer goes wrong without it:

* **the absolute path, with "Read it"** - the picture reaches the model because the prompt
  names a file it can open, so the path is an instruction and not a caption;
* **"the GRADED, CONFORMED timeline frame ... not the raw source file"** - otherwise a
  colour or framing question is answered about a different picture, 63.35/255 different on
  `marker_capture`'s own measurement;
* **"It is ONE INSTANT ... everything below was measured over the WHOLE clip"** - this is
  the one that matters. Without it a disagreement between the picture and the measurements
  reads as a contradiction rather than as two different spans, and the scout's step 2
  evidence is that the model describes a picture very literally once it has one.

### What was NOT built

Region marking and freehand drawing - options B and C of the scout's report, both gated on
the open captain decision `annotate-means-sketch-or-point`. No tile grid, no `drawbox`, no
click-a-region.

### What could not be shown

**A screenshot of the panel with the frame in it.** The captain's display was asleep and a
full-screen capture came back entirely black - which is precisely the mistake
`docs/panel/README.md` records, and waking their machine for a picture is not this task's to
do. What was recorded instead is the HTML the widget was really handed, live, from a real
run:

```
IMG TAG PAINTED: <img src="/Users/prajwal/.vep_panel/frames/ask_frame.20260830T194208184801Z.png" width="320">
  file exists: True  bytes: 6232792

... <h3>the frame that went with it</h3><img src="...ask_frame...png" width="320">
<div class="dim">frame at 00:00:13:04 attached (5.9 MB, grabbed in 0.2s)</div>
<div class="dim">/Users/prajwal/.vep_panel/frames/ask_frame.20260830T194208184801Z.png</div>
<h3>how it was asked</h3><div class="dim">4082 characters of context, 11.9s round trip ...
```

It goes through `richtext.image`, which is the same helper the Timeline tab uses to draw
`strip.png` - the route `6_timeline.png` in `docs/panel/` already shows drawing, and the one
`Label.Pixmap` silently does not.

## The menu entries that did nothing

Filed 2026-08-30. The rule is AGENTS.md section 15, "What Resolve's script host does not give
an entry point".

### What the captain saw

They clicked **Workspace > Scripts > VEP Pipeline Panel**. Nothing opened. They clicked
**Workspace > Scripts > Capture Frame for Firstmate**. Nothing opened. No window, no message,
no error dialog - the menu item behaved as though it had done its job.

Resolve's own log, `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/logs/davinci_resolve.log`,
had both:

```
2026-08-30 19:14:37 | Traceback (most recent call last):
  File ".../Fusion/Scripts/Utility/VEP Pipeline Panel.py", line 90, in <module>
  File ".../Fusion/Scripts/Utility/VEP Pipeline Panel.py", line 81, in _repo_root
NameError: name '__file__' is not defined. Did you mean: '__name__'?

2026-08-30 19:14:45 | Traceback (most recent call last):
  File ".../Fusion/Scripts/Utility/Capture Frame for Firstmate.py", line 170, in <module>
  File ".../Fusion/Scripts/Utility/Capture Frame for Firstmate.py", line 136, in main
  File ".../Fusion/Scripts/Utility/Capture Frame for Firstmate.py", line 47, in _repo_root
NameError: name '__file__' is not defined. Did you mean: '__name__'?
```

### The initiating trigger

`_repo_root()` built its candidates as a TUPLE, so all three were evaluated before the loop
tested any of them:

```python
for candidate in (
    REPO_ROOT,                                                    # correct, and exists
    os.environ.get("VEP_REPO_ROOT", ""),
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # raises, first
):
```

The stamped `REPO_ROOT` was right - `/Users/prajwal/.treehouse/video_editing_pilot-9487f5/1/video_editing_pilot`,
a directory that was still there - and was never looked at. The last-resort fallback was the
thing that killed it first. That is the general shape and it is worth stating on its own: **an
eagerly built fallback chain lets a later candidate pre-empt an earlier one that would have
worked**, and the failure mode is the fallback's, not the answer's.

### The masking condition, which is the part that matters

`__file__` is bound by the interpreter when it runs a script AS A FILE. `importlib`,
`exec_module`, and `python3 the_file.py` all bind it. **Resolve's own script host does not.**

Every test and every screenshot ran these files the first way. The panel session that shipped
this produced six screenshots and a responsiveness measurement (303 probes, p50 1.3 ms) and
not one launch from the menu the captain uses. `tests/test_resolve_script_install.py::test_the_stamped_copy_finds_the_repository`
called `_repo_root()` on the INSTALLED copy - the exact function, in the exact file, in the
exact place - and passed, because `exec_module` had bound `__file__` for it.

So the defect was not merely untested. It was on the only path that matters, and every
available route to it went around.

### What the host does and does not provide, audited

- `__name__` **is** provided, as `"__main__"`. The capture button's traceback runs through its
  `if __name__ == "__main__":` line, which is only reachable when that comparison is true.
- `__file__` is **not**.
- `fusion` and `bmd` **are** injected, before a line of the file runs - which is what makes a
  window reachable from a script that has imported nothing of ours.
- `sys.argv` and the working directory are not relied on by either entry point, and
  `tests/test_resolve_scripts_bootstrap.py` keeps it that way. The one subprocess that needs a
  directory is handed one explicitly by `frame_attach.call_site`.

### The regression test, and why it is shaped as it is

`tests/test_resolve_scripts_bootstrap.py` runs `exec(compile(src, path, "exec"))` into a
namespace with no `__file__`, in a subprocess, for every entry point in `resolve_scripts/`.
Against the code it replaced: **10 failed, 2 passed**. After: **12 passed**. The two that
passed either way are the `sys.argv`/cwd audit, which was already clean.

It covers the module body AND `_repo_root()` because the defect landed in a different place in
each file - the panel resolves the root at import, the capture button from inside `main()`.

`__name__` is deliberately NOT set to `"__main__"` in the harness, even though the host sets
it: under that name these scripts connect to Resolve, and the panel leaves through `os._exit`,
which would take pytest with it. The defect is reachable without it.

Laziness is proved by a candidate that CANNOT be evaluated: with a stamped root and no
`__file__`, the third candidate raises if it is computed at all, so returning the stamp is
itself the evidence it was never reached.

### The two copies

`_repo_root` is duplicated across the two entry points, which is how one bug became two. It
stays duplicated: the helper's entire job is to find the repository, so any shared copy would
have to be imported from the thing it has not found yet, and a third file copied beside them in
the Scripts folder would be a second thing the installer stamps and a second thing that can
drift. The duplication is instead held by ONE test over BOTH files.

### The stamp

The installed copies pointed at `~/.treehouse/video_editing_pilot-9487f5/1/video_editing_pilot`
- a disposable worktree. The installer's logic was right; it stamps the checkout it is run
from, and it had been run from a worktree. It now says so, detecting a LINKED worktree by its
`.git` being a file rather than a directory - exact, where a path-name guess would not be - and
installs anyway, because it is the captain's machine. And a stamped path that has gone away now
produces a stated error rather than another silent death.

## a-dark-picture-is-not-a-black-bar

The gate `docs/RULE_EVIDENCE.md#the-occupancy-gate-failed-a-correct-render` repaired on
2026-08-30 was pointed at the captain's craft reference the same day - twenty minutes of finished
documentary, correctly framed, filling its 2.39:1 frame for the whole of its runtime - and it
failed it by twenty times its own bound. Two more defects in one check, both about the instrument
reading CONTENT as GEOMETRY.

**A graded shadow read as a bar.** The walk asked whether a row was DARK (`LIT_LUMA_THRESHOLD`,
12.0) and FLAT (`BAR_ROW_MAX_STD`, 2.0). The variance half was the discriminator and it cannot
carry footage this dark. At 1121.0s a full-bleed concert shot has 363 rows of dark ceiling:

```
 row      0: mean  8.56  std 0.98   max pixel 12
 row     90: mean  8.92  std 0.96   max pixel 13
 row    181: mean  9.01  std 1.08   max pixel 15
 row    272: mean  9.08  std 1.03   max pixel 14
 row    362: mean  9.33  std 1.97   max pixel 41    <- the last row the walk ate
```

Every one of those is flat by the bound, because at 3840 columns a graded shadow really is flat to
within a luma level. The gap `BAR_ROW_MAX_STD = 2.0` sits in was measured on 001's own footage -
real bar 0.0-2.9, dark picture 3.95 and up - and it does not exist here. The reference's median
frame luma is 38.9 of 255 with a 5th percentile of 6.7; our own footage has never been that dark.

A bar has a second property the walk never asked for: **it carries no light at all**. Measured, on
1080x608 picture padded into 1080x1920 and re-encoded:

```
 crf 18                      bar row-mean max 0.000   row-std max 0.000   pixel max 0
 crf 23                      bar row-mean max 0.000   row-std max 0.000   pixel max 0
 crf 30                      bar row-mean max 0.006   row-std max 0.074   pixel max 1
 noise + lanczos, crf 30     bar row-mean max 0.000   row-std max 0.000   pixel max 0
 001's shipped master        bar row-mean max 0.14    row-std max 0.35    pixel max 15
```

against the shadow's 8.5-9.4. `BAR_ROW_MAX_LUMA = 1.0` is an order of magnitude above every real
bar and an order of magnitude below the picture that trips it. Swept from 0.25 to 8.0 on 001's
master, detection of its real bars is invariant: median 0.3167, min 0.3167, max 1.0000 at every
value.

DARK is a range and BLACK is a value. The variance bound stays, because it is what separates a bar
from a dark picture on our own footage; the level bound is what carries it on the reference.

**A composition read as a conform.** The reference presents archival home video as a small rounded
rectangle inside black (t=764.5s, 90-93s) and runs a three-panel split screen with black gutters
(t=529-541s). Those bars are real black and no predicate on a row can say otherwise. But a conform
letterbox is the consequence of fitting a source of a different aspect into the delivery frame, and
fitting one rectangle inside another leaves bars on ONE axis, never both: black on all four sides
was never produced by a conform. Measured at 2 Hz: 277 of the reference's 2441 samples are inset on
all four sides, and **0 of 001's 114** - its real bars measure 0 side columns on every frame.

**A black frame is judged by the pipeline's own predicate now.** The check used to answer "is there
a picture here" by proxy - a black frame was one whose two bar runs met - which held only while a
bar row and a black picture row were the same thing. The level bound ends that, so `_frame_is_black`
asks blackdetect's own question directly: at least `BLACK_PIXEL_RATIO` (0.98, its default) of the
pixels under `LIT_LUMA_THRESHOLD`, the same predicate `detect_black_frames` already judges by.

**The verdict, both masters, 2 Hz, the real function:**

```
REFERENCE (3840x1608, 1220s, no manifest so one declared framing)
before   passed: False
         2418 judged, 478 (19.8%) read letterboxed
         median 1.0000  min 0.0006  max 1.0000  spread 0.9994   bound 0.05
after    passed: True
         2090 judged, 277 counted out as compositions, 74 as black
         median 1.0000  min 0.9577  max 1.0000  spread 0.0423   bound 0.05

001 (Pipeline_Edit_2.mp4, its own manifest)
before   passed: True    framing 0: n=81 spread 0.0000 | framing 1: n=33 spread 0.0135
after    passed: True    framing 0: n=81 spread 0.0000 | framing 1: n=33 spread 0.0000
```

001's fill group tightened because two of its cutaways end in a few genuinely dark picture rows -
16 and 26 of 1920, at a row mean of 6.1 and 7.5 - which the old walk subtracted and the level bound
now leaves as picture.

**What is left, stated rather than folded in.** Two of the reference's 2090 judged samples still
read under 0.99: 276.5s and 277.0s, a title card over a concert shot whose bottom 62 and 68 rows are
literally at black. They pass, at a margin of 0.0077 on a bound of 0.05. No predicate on a picture
can separate a shot that is black at the frame edge from a bar - the difference is in the manifest,
which the reference has not got - so the remaining question is the consistency half's STATISTIC:
the fill floor is a median over the group and the consistency half is a min against a max, which
makes one frame anywhere in twenty minutes decide the verdict. Changing that is a re-specification
of the gate and is the captain's.

## the-render-that-ended-on-four-seconds-of-nothing

"Every frame of the timeline must show a clip" (AGENTS.md §10.2) had no audio twin, and the
asymmetry was not defended anywhere. `detect_black_frames` asks whether the picture went away.
`verify_audio_streams` asks only whether an audio stream EXISTS. `measure_lufs` asks whether the
whole master is deliverable, which an 11% hole barely moves. Nothing asked whether a listener could
hear anything while a picture was on screen.

Counted as exact-zero samples of a mono 16-bit decode, on both masters:

```
001 (Pipeline_Edit_2.mp4, 56.639s)
  316,248 of 2,718,656 samples are exactly zero = 11.63%
  runs at or over one video frame:
     41.643 -  43.943   2.301s
     52.627 -  56.639   4.011s   <- the last four seconds of the video
  6.312s = 11.14% of the runtime, of which 6.274s has a picture on screen

REFERENCE (ref_best.mp4, 1220.162s)
  53,186 of 58,567,776 samples are exactly zero = 0.09%
  runs at or over one video frame:
   1219.379 - 1220.162   0.783s   <- the intended tail, and nothing else
  0.783s = 0.06% of the runtime, of which 0.000s has a picture on screen
```

The reference's tail sits over frames measuring a mean luma of 1.86 with a maximum pixel of 3: the
film has faded out. 001's two runs are both under picture.

The mechanism on 001 is fully traceable and no part of it is a bug: spine block 9 is a
`transition_slot` declaring `music_behavior: silent` and block 12 is an `outro` declaring
`fade_out`; both are covered by V2 cutaways placed `video_only: True`, so the clip's own audio is
never heard (§10.5, correctly); and there is no A-roll under them because they are non-speech
blocks. The plan asked for silence and got it exactly.

**Silencing the MUSIC is not silencing the FILM**, and the vocabulary has no way to say the second.
When the reference's bed drops out, room tone, footsteps, applause and equipment noise carry the
moment; 001 has no ambient bed and no room tone at all, so `music_behavior: silent` over a
`video_only` cutaway resolves to nothing on every track. Nothing excuses a run for a declared
behaviour because there is no declaration to read: if one is ever added, `measure_silence_under_picture`
is where it would be read, the way `segment_is_declared` reads `intentional_black_beat`.

**Two decisions this check does NOT take.**

*The level.* The gate is DIGITAL ZERO and nothing else. A 16-bit sample is zero exactly when its
magnitude is under half an LSB, `20*log10(1/32768)` = -90.309 dBFS - the resolution of the delivery
quantisation, not a chosen number, and the one that reproduces the count above on both masters.
Everything above that line is QUIET, and how quiet a declared quiet moment may be is the captain's
open decision `craft-silence-under-picture`. `NEAR_SILENCE_LADDER_DBFS` is therefore reported at
every rung and gates at none:

```
001, seconds under a picture at each rung
  digital zero   6.274s   GATES
  -80 dBFS       6.611s   reports
  -70 dBFS       6.952s   reports
  -60 dBFS       6.962s   reports
REFERENCE: 0.000s at every rung
```

*The duration.* The floor is the timebase, `MIN_SILENCE_FRAMES = 2` frames of the master's own
frame rate - §10.5's own mechanical floor, "one at level plus one of de-click ramp", which is the
shortest sound this pipeline can place. Below one frame the question does not arise: ordinary audio
crosses zero constantly.

**Why the decode is 16-bit and not float.** The question is whether the DELIVERED sample is zero,
which is a statement about the quantisation the delivery carries. On 001 the float decode of the
same AAC stream finds 192,512 exact zeros (7.08%) and the 16-bit decode 316,248 (11.63%); the
second is what a listener gets.

**What it costs.** Only the stretches the audio flagged are decoded, at the master's own frame rate
so no frame between two samples is missed. The whole check on the 20-minute 4K reference takes 2.8
seconds.

## The step that measured nine clips and graded none

Step 5.01's own docstring recorded the correction it was: the step used to
compare every clip against a hardcoded `122.0` and apply the difference,
which is a decision about how bright the finished video is taken by
nobody. That constant went, and exposure normalisation was left reachable
only through an `exposure_reference` that only a brand template can
declare.

Project 001 names no brand template. So on the run of record the step
measured luma on 9 of 9 clips, correctly, and wrote the IDENTITY CDL to
every one - slope 1/1/1, offset 0/0/0, power 1/1/1, saturation 1.0,
`exposure_gain` 1.0, `series_look: null`, `fusion_look: {}`. An identity
CDL is a no-op, so Resolve drew no node, and the captain opened the
colour page to find it empty.

What it had measured, in the order the clips play:

| clip | luma | what the vision pass says it is |
|---|---|---|
| clip_011 | 145.495 | outdoor parking lot, daylight - the spine, 9 of 18 placements |
| clip_002 | 107.192 | vehicle exterior and interior, daylight |
| clip_008 | 136.945 | outdoor sidewalk, daylight |
| clip_001 | 135.747 | parking lot, daylight |
| clip_004 | 115.994 | inside a vehicle driving, overcast/hazy |
| clip_013 | 130.691 | parking lot, daylight with sunset hues |
| clip_006 | 135.265 | urban street from a vehicle, overcast/hazy |
| clip_017 |  53.116 | inside a vehicle, dimly lit - the shot the video ends on |
| clip_014 | 113.265 | brick building exterior, daylight |

A 2.7x spread, measured, written down, and acted on by nothing.

**The fix is not a different constant.** Reverting to one reinstates the
original defect, and AGENTS.md 10.3 forbids reporting a default as though
it were measured. What was missing was not a number: it was that nobody
owned the decision. The only party in the loop who can look at clip_017
and say *"that one is dark on purpose - it is the retreat into the car at
the end"* is a model that has been told it is the colourist and given the
measurements, which is what `library/tools/craft_role.py` and
`library/tools/color_correction.py` now do.

Two things the old output could not express, and both are now separate
fields rather than one identity CDL: which clips the colourist MOVED
(`correction_terms`, `correction_stops`, `correction_reason` per clip),
and which absence an ungraded run IS (`correction_basis`, four readings -
`corrected`, `judged_no_correction_needed`, `no_correction_decision`,
`every_entry_dropped`).

The other axis it never produced is the one that decides whether a luma
difference matters at all. 2.7x between clip_011 and clip_017 is a big
ratio; what makes it a defect is that they touch, at 45.462s, at 1.454
stops. Nine of the cut's thirteen cuts sit inside 0.2 stops of each other
and want nothing. `cut_adjacency` is that table.

## The catalogue column nobody said was read

`library/tools/sfx_library.catalog_document` prints an `envelope` per
sound - `punchy`, `swelling`, `fading`, `sustained` - measured by the
library's own profiler. Step 4.04's post-bridge keys its entire placement
pass on that word, and **a `swelling` sound is anchored by its END, so
the start is placed such that start + duration lands on the nearest
measured energy peak**. That is a build, delivered by the engine, for
free, on any sound whose measured shape rises.

Nothing in the planner's context said so. The column had no legend, no
reader was named, and `_describe_placement` held a private four-row dict
in the post-bridge that the prompt could not see.

What 001 shipped was `camera soft click.wav` (fading, 0.46s) and
`camera-shutter-6305.mp3` (swelling, 0.34s) at two different blocks,
layering nothing - the second far too short to read as a swell. The
planner's own `could_not_determine` recorded the reasoning: the library
was *"built for a different kind of edit"*, and *"putting a riser under a
man admitting he has quit every day this week would be the single most
off-brief thing this pipeline could do"*.

The second half of that is a good judgement and it stands. The first half
was made without the measurement. Counted on the captain's library on
2026-09-03: 78 sounds, of which **42 are `swelling` and 31 of those run
2 s or longer**, the longest 76 s - including ten Super 8 projector
textures, warm and grainy, 1.5 s to 13 s, whose library entries read
*"nostalgia, vintage cinema, analog decay"*. A catalogue ordered by folder
reads like its folder names; nothing had ever totalled the envelope
column.

`sfx_envelope.ENVELOPES` is now the one table both readers use, and
`sfx_envelope.library_shape` counts the library on every run. Neither
recommends anything: which moment earns a sound is the supervising sound
editor's, and `craft_role` is where that role is stated.

## Twelve handoffs, no role

Measured 2026-08-25 across every `handoff.md` in `library/steps/`: not one
gave the answering model a role, an expertise or a professional framing.
Every one opened *"Given X, define Y"*. The captain's reading:

> there are like skill files and agent.md files where the LLM doesn't know
> how to operate and its just a generic agent in the system rather than an
> actual proffesional video editor/director/etc all in one

The two defects above are the same defect wearing different clothes. Both
steps measured correctly. Neither had been told whose job it was to act on
the measurement, and in one of the two the authority to act had been
removed on purpose and never relocated.

`library/tools/craft_role.py` is the one framing device, and it is
deliberately the same shape on both halves so the next one follows a
pattern rather than inventing a third. It carries a discipline, what that
discipline reads the measurements WITH, and what is and is not that step's
to decide - and it carries no preference about the answer, which
`tests/test_no_creative_floors.py` reads the rendered text to hold.

Steps with no declared role are recorded in `WITHOUT_A_DECLARED_ROLE` with what each is addressed as today - the live census is the two tables in `library/tools/craft_role.py`, whose prose counts `tests/test_craft_role.py` pins. That is a gap made visible rather than closed: writing a role for a discipline nobody has studied would be the engine inventing an expertise,
which is the defect one level up.

## the-build-that-deleted-nineteen-timelines-to-write-one

`library/tools/reel_build.py` on `main`, 2026-09-06, immediately before
placing anything:

```python
timelines_to_delete = []
for i in range(1, project.GetTimelineCount() + 1):
    t = project.GetTimelineByIndex(i)
    if t.GetName().startswith("Reel "):
        timelines_to_delete.append(t)

if timelines_to_delete:
    pool.DeleteTimelines(timelines_to_delete)
```

It landed in #507 inside a batch of eight unrelated reel fixes and was
never the subject of a ruling.  It is the convenience a standalone
rebuild script has and a pipeline operation must not: `reel.build` was
registered as an operation in #571 with derived requirements that refuse,
and on the field-test project every one of those requirements was
SATISFIED - so the operation would have run, and running it deletes
`Podcast (field test)`'s nineteen approved reel timelines.

Measured with the placer, the verifier and Resolve faked, against a
project carrying the master, nineteen approved reels and one orphan reel
the current plan no longer contains:

| build | on `main` | after |
|---|---|---|
| full rebuild | 20 of 21 timelines deleted, orphan gone | 19 deleted, orphan survives |
| build ONE reel | not expressible; the only build is all of them | 1 deleted, 18 approved survive |
| build ONE reel into a new name | not expressible | 0 deleted |

Three things were wrong and they are different failures:

- **Prefix, not name.** `startswith("Reel ")` is the hazard AGENTS.md 5
  already names for addressing a Resolve PROJECT - *a near match lands
  elsewhere* - at the one call site where getting it wrong destroys work
  rather than reading the wrong thing.
- **Unconditional.** The delete ran before the build, over everything,
  whatever the plan contained.  A reel the plan no longer describes was
  deleted with nothing saying so and nothing backing it up.
- **All-or-nothing.** There was no way to build one reel, so the only
  available act was "delete nineteen, write nineteen".

The half of the pair that was already right is `plan_provenance`, which
has MERGED rather than replaced since #568 for exactly this reason - *"a
partial rebuild must not delete the provenance of the reels it did not
touch"*.  A partial rebuild could not happen, because the delete loop ran
first and took everything.

The captain's ruling, 2026-09-06, on being shown it:

> Do not add a separate non-destructive path beside the destructive one;
> that leaves the loaded gun on the table. [...] Consider whether it
> should refuse outright rather than delete when a timeline it did not
> plan to touch would be removed; a refusal is cheap and a deleted
> timeline is not.

So there is ONE path.  `timelines_to_replace` matches the names this
build will place, exactly and never by prefix.  `assert_deletion_scope`
is then asked of the list about to be deleted - deliberately not a
restatement of the selection, because a guard that recomputes the
selection cannot catch the selection being wrong - and REFUSES rather
than deleting anything unplanned.  `only` and `name_suffix` scope the
same path rather than forking it.

`tests/test_reel_build_touches_only_its_own_timelines.py` asserts the
survivors by name off a media pool that really deletes, and fires the
guard in both directions: permitted on a legitimate replace, refused on
an over-collecting selection driven through the real build path.

## the-take-that-was-two-thirds-cut

The pipeline rebuilt reel 03 of the captain's approved nineteen and made
it WORSE AT THE OPEN, while removing exactly the repetition it was asked
to remove.  Both halves of that are true and the second does not excuse
the first.

The span, master 301.241-341.270s: Akshita says one sentence three times
inside forty seconds.  WhisperX segments the first take as four
consecutive lines.

| # | line | seconds |
|---|---|---|
| 1 | "Yeah, so search didn't change." | 301.241-302.566 (1.325s) |
| 2 | "The question changed." | 302.626-303.449 (0.823s) |
| 3 | "And whoever AI best understands, gets the answer." | 303.549-306.400 (2.851s) |
| 4 | "yeah" | 306.400-306.527 (0.127s) |

Lines 1 and 2 paired with the second take at containment 1.000 and
Jaccard 1.000 and were CUT.  Line 3 paired with the second take's own
third line - "and whoever AI understands best, gets the answer",
309.320-309.920 - at containment 1.000 and Jaccard 1.000 too, and was
refused, because 2.851s against 0.600s is a duration ratio of 4.75 and
`DURATION_RATIO` is 2.0.  Line 4 was refused at 2.13 the same way.

So two thirds of a take were removed and its tail was left standing.
The keep ranges came out `[(302.566, 302.626), (303.449, 309.920),
(310.120, 341.270)]` - 2.348s really gone, 904 frames against the
approved 959 - and the reel's first line became **"And whoever AI best
understands, gets the answer."**  The answer, before the question has
been asked.  The model's own written hook, "Yeah, so search didn't
change", did not arrive until 3.41 seconds in, which on a reel is most
of the decision.

**A partial cut is worse than no cut.**  The reel was better in the
middle and worse at the open, and the open is the part that has to earn
the next five seconds (`library/tools/reel_opening.py`).

The duration guard was not wrong.  It answers a MECHANICAL question -
are these two utterances the same sentence, safely enough to drop one -
and 2.851s against 0.600s is the shape it exists to refuse: without it,
reel 06 drops a 4.3s line to keep a 0.5s fragment of the same sentence
(`test_a_fragment_is_never_kept_over_a_full_line`).  What it must not do
is decide, alone, that two thirds of a take may go.  Whether what is
left READS is a judgement, and an invented constant chosen to make
pair-matching safe had come to decide the shape of an opening.

So the unit of a cut is the RUN, and the rule carries no number:

> **A repeated run is removed WHOLE or not at all.**

A run is a maximal chain of segments that are consecutive in the span's
own segment list, carry one speaker, and are every one of them a
repeated take - one the scan either cut or blocked.  "Nothing else was
said in between" and "one person said it" are facts about the
transcript, not thresholds.  A run with a member the pairing test could
not accept is left entirely alone and REPORTED: `refused_take_groups`
reaches the model through `reel_proposal.enrich`, where the span can
still be redrawn, and the operator through `rebuild_reels_in_project`.

Why this cannot strand a fragment at the head of a reel: a reel opens on
the first second its keep ranges retain, a cut only ever removes
segments, and after `assert_takes_are_whole` every run a cut touches is
removed entirely.  So the leading segment is either the span's own first
segment, untouched, or the first segment after a wholly removed run -
the start of another speaker's turn, or of speech that is not a
repetition at all.  A partly removed run is what leaves a tail where its
own opening used to be, and a partly removed run is not expressible once
that guard passes.

**Measured across all nineteen approved reels**, on the plan and without
building anything: five have a pair the ratio refuses, and TWO of those
had a cut withdrawn by this rule.

| reel | before | after |
|---|---|---|
| 03 | 3 cuts, 2.348s, opens "And whoever AI best understands..." | 1 cut, 0.200s, opens "Yeah, so search didn't change." |
| 16 | 2 cuts, 1.788s, "but they work for AI." cut and "Those queries don't work for Google," left hanging | 1 cut, 1.014s |
| 07, 17, 18 | a lone refused pair, no cut in the run | unchanged |
| the other fourteen | - | byte-identical cut lists and keep ranges |

Reel 16 is the same defect away from the head of a reel, which is why
the rule is about the CUT and not about the position.  Reel 03's
repetition is now STILL IN the reel and says so, which is the honest
outcome: the reel matches what the captain approved, minus a 0.200s
duplicate, and the report names the take, the ratio that stopped it and
that redrawing the span is the way out.

`tests/test_reel_partial_take_cuts.py` carries reel 03's and reel 16's
real segments as data and fires the guard in both directions - the
coherent cut list passes, the cut list the rebuild actually used is
REFUSED by name.

---

## the-declaration-nothing-read

Step 3.04 declared a prompt allow-list and the projection never applied
it.  The declaration was under `interface`, beside the `inputs` and
`outputs` it reads as though it belongs with;
`run_pipeline.project_step_context` reads the manifest's TOP LEVEL,
found nothing there, and took that for the other legal state - "this
step declares none, hand it every byte it was routed", which `render`
and `validate` really are in.  An accident and a decision looked
identical from the only place that could tell them apart.

The request that reached the model that chooses which passages become
reels, measured on the field-test run of 2026-09-06:

| part | chars | |
|---|---:|---|
| creative brief reference + preamble | 3,717 | |
| **`timeline_transcript`, the raw document** | **817,317** | **92% of it** |
| `turns`, the summary the pre-bridge renders FROM that document | 51,531 | |
| `reel_candidates`, the measured table | 15,908 | |
| the rest | 362 | |
| **total** | **888,835** | plus a 22,633-char prompt |

**8,509 words arrived carrying individual `start`/`end`/`timed`
timings**, along with 940 absolute source-file paths and 940 Resolve
item ids.  Three rules in this section were broken at once - *"Word
timings do not reach a prompt"*, *"Never send a summary and the
structure it was rendered from"*, and the `context_fields` contract
itself - and every reel selection this pipeline has ever made was made
that way.

The declaration was also FALSE in a second way that only became visible
once it was read: `timeline_transcript.turns` names a path the document
does not have.  The document carries `segments`; `turns` is the
pre-bridge's own table, built from those segments and restored by name
AFTER the projection.  `project_fields` prints a warning for a declared
path that resolves to nothing, so the run would have said so on every
invocation - had the projection ever run.

**What the survey found once the reader was fixed.**  Twenty-two
archived requests across three projects were re-read, every top-level
key accounted for against the four routes that legitimately put one in
a prompt (the allow-list, the five keys restored by name, the step's own
pre-bridge table, and a `-`-only declaration meaning "everything minus
these").  Every key on eleven steps was accounted for.  `select_reels`
was the only step carrying anything undeclared, and it carried
everything.

**Two enumerations had never looked at it.**
`test_llm_context_routing.LLM_STEPS` is hand-written and did not name
`select_reels` or `render_motion_graphics`, so its assertion that every
LLM step projects its context had never been asked about either.  The
set is now DERIVED from the DAG through the same
`get_step_implementation` the runner uses.

**And one more false declaration.**  `input_contract._reaches_prompt`
answers "does the prompt consume this input" off the same allow-list,
so while 3.04's was unreadable the survey answered True for every one
of its inputs.  With the declaration bound it found `audio_spine`:
routed from `mesh_spine`, declared as an optional input, and read by
neither the bridge, the post-bridge, the handoff nor the prompt.

It is RECORDED rather than dropped, in
`input_contract.UNCONSUMED_DECLARATIONS`, and the reason is measured:
dropping the declaration while the DAG edge stays makes
`tests/test_dag_contracts.py` fail ("maps 'audio_spine' ... but
'audio_spine' is missing from select_reels's manifest inputs") and makes
the auto-derived `state_key` requirement come out REQUIRED, because the
`required: false` flag lived on the declaration.  Dropping the edge too
would remove what orders `select_reels` after the spine - the step's own
real dependency, `timeline_transcript`, is an EXTERNAL input and not a
DAG edge at all - and that is a routing decision rather than a
projection one.

**What the fix costs the model.**  `timeline_transcript` no longer
reaches the prompt except as its one-sentence `measurement`, which says
what the times mean and how the speech was obtained.  The speech itself
arrives as `turns`, and `turns` is built by `reel_exchange` from
`bound_segments`, which excludes segments straddling a cut - so it is
not the whole document.  On the field-test transcript that exclusion is
11 of 940 segments carrying **88 characters in total**: "well",
"about", "Yeah.", "audits" - WhisperX bridging silent gaps across cuts,
which is what the exclusion exists for.  Measured, 888,834 characters
become 71,908 and 8,509 word-timing records become 0.

`library/tools/context_projector.declared_context_fields` is now the one
reader of the location, and it RAISES on a misplaced declaration rather
than falling back to reading it: a fallback would make the wrong
location work, and the wrong location would then spread.
`tests/test_context_fields_binds.py` fails on `origin/main` in both
directions that matter - it fires on `select_reels` and stays silent on
the eleven steps that project correctly.

## the-outputs-nobody-read

`library/tools/input_contract.py` asks who REFUSES when a declared input
is absent.  Nothing asked the mirror question - **who READS what a step
produces** - and that is the question behind the defect class the
captain calls the biggest quality point in the pipeline: data computed
and then not reaching where it was needed.

Seven instances were found by hand in two working days (per-line
transcriber confidence discarded at write time; `caption_content_hash`
fed the wrong argument; step 3.04's allow-list one level too deep;
`enforce_min_duration` never called; `duplicate_takes` consumed by
nothing; captions with no footage binding; the reels path reading no
picture).  `library/tools/output_contract.py` is the mechanism, and
`tests/test_output_contract.py` is the ratchet.

### What the survey found

72 declared outputs across 31 steps in both processes.  31 are carried
by a DAG edge, 19 by the producing step's own prompt, 14 by a module
elsewhere, and **8 by nothing at all**.  Six of the eight are counts and
terminal records, recorded with the reason in
`output_contract.REPORTED_NOT_CONSUMED`.  Two are findings, and one more
was a finding until this change fixed it.

**Those numbers were the instrument, not the pipeline.**  The second
pass below re-asked the same question with a `code` route that requires
a READ, and the honest answer is **70 declared outputs and 13 that
nothing reads**.

**`judge_reels.reel_judgement` - the quality bar could not fail.**  Step
3.05 computes the readings, checks every quote against the reel it is
about, derives the two judgement qualities and returns them.  Both
readers of that answer - `reel_quality_bar.main` and
`reel_conformance_verifier`, which is what step 7.02 runs - opened
`<project>/pipeline_output/review/reel_judgement.json`, and **nothing in
this repository ever wrote that file**.  On the captain's
`lucie/geo-podcast` the file exists and is byte-for-byte the step's own
`reel_judgement`, written one minute after the step ran: the middle of
the chain was a person, which is the same defect
`reel_proposal.write_from_step_output` was created to remove for the
proposal.

Measured on that project's 31-moment plan, with the hand-copied file
absent - the state every other project is in:

| | reels | judged | coherence | value | verdict | findings |
|---|---:|---|---|---|---|---|
| judgement not found | 31 | False | 31 `unjudged` | 31 `unjudged` | 31 pass | 41, in 2 codes |
| read from step 3.05's output | 31 | True | 31 `not_followable` | 30 `delivers`, 1 `delivers_nothing` | 30 pass, **1 fail** | 73, in 4 codes |

Two whole finding codes could never fire: `QB-NOT-FOLLOWABLE` (31
warnings) and `QB-NO-TAKEAWAY` (one error, on `Reel 22 -
a-score-is-not-a-fix`, which is the reel that flips to FAIL).  The 31
of 31 `not_followable` reproduces `reel_quality_bar.COHERENCE_DOES_NOT_GATE`
exactly, which is the number that calibrates the measurement.

`reel_quality_bar.read_judgement` is the fix: one spelling of where a
judgement lives, preferring a hand-placed `review/reel_judgement.json`
because a captain who put one there meant it to be read, and otherwise
reading step 3.05's own `output.json`.  Both call sites ask it.

**`catalog.source_resolution`** is measured by step 1.02 and read by
nothing.  `step_5_04_compile_manifest/step.py:1252` states a read that
does not happen; the only other `source_resolution` in the engine is
`execution/apply_fusion_comps._source_resolution`, which measures the
resolution off Resolve's own media pool item.

**`validate.final_qa_decision`** is declared as an output of the DAG's
exit node and nothing produces it.  `post_bridge.py:20` reads
`data.get("final_qa_decision", "")`; no edge routes the key, no handoff
asks the model for it, and no reader exists.  It is the empty string on
every run.  The real verdict is `validation_result.status`.

## the-outputs-nobody-read-second-pass

The audit above built the mechanism and left an INVENTORY.  This is what
each entry ends as, and what re-asking the question found.

### The `code` route was crediting six outputs to something not a read

The survey's third route matched a key as a string literal ANYWHERE in a
module outside the producing step.  Its own docstring said this
over-credits "in the safe direction", and it does not: an over-credit
keeps an unread output OUT of both tables, which is a gate declining to
fail.  Six were credited by three shapes, none of them a read:

| output | credited to | what that line really is |
|---|---|---|
| `scan.total_files` | `analysis/sfx_query.py:304`, `resolve_project_sync.py:302` | those modules WRITING their own `total_files` into their own dict |
| `scan.skipped_files` | `step_1_02_catalog_footage/step.py:297` | the CATALOG writing its own key of the same name |
| `catalog.skipped_files` | `step_1_01_scan_project/step.py:99` | SCAN writing its own, one step earlier |
| `catalog.total_clips` | `step_1_05_prosody_analysis/step.py:318`, `analysis/vision_pipeline_v3.py:1794` | prosody's and vision's own counts of their own work |
| `temporal_index.total_failed` | `run_pipeline.py:2207` | `key != "total_failed"` - the key named to EXEMPT it from the zero-is-empty rule |
| `ocr_extraction.ocr_extraction` | `operations.py:808`, `project_layout.py:203`, `run_scope.py:233`, `step_exporter.py:155` | the STEP ID, in four tables - while `run_scope.DESELECTED_BY_DEFAULT` says in as many words that nothing consumes the output |

The `code` route now requires a read POSITION - `d.get("k")`, `d["k"]`,
`"k" in d`, `d.pop("k")`, or the key inside a list handed to a call, the
shape `require_keys(data, [...])` refuses with.  Nothing that was
genuinely read lost its credit: `assign_aroll.hook_assignment`,
`judge_reels.reel_judgement`, `scan.project_config`,
`validate.validation_result` and three of 1.04's outputs all survive.

Two keys are too generic for a name match to mean anything even in read
position - `temporal_index.source` and `creative_cohesion.step`, whose
credits read four and five unrelated dicts.  `KNOWN_NAME_COLLISIONS` now
SUBTRACTS a credit instead of recording a known-false one and leaving it
standing, and `disagreements` fails on a collision entry with nothing
left to subtract.

**`run_pipeline.validate_step_output` is not a consumer**, and saying so
is the point.  It touches every declared output - present, typed,
non-empty - which is the producing end's mirror of `input_contract`, a
check on the DECLARATION rather than a use of the value.  Crediting it
would have made every output consumed by construction.

### What each of the thirteen ends as

Five FIXED, eight decided in code.

**`validate.final_qa_decision` - deleted.**  Declared and produced by
nobody, so both the declaration and the post-bridge's dead echo are
gone.  The one thing that had to be checked first: the phantom was
load-bearing for whether the QA model was called at all, because
`llm_output_declarations` asks for the step's outputs minus what is
already in hand.  It is not - 6.02 has no step.py, its pre-bridge emits
`deterministic_validation`, and the model is still asked for
`validation_result`, which is exactly its half.
`tests/test_llm_context_routing.py` asked that question through a
hand-written `{"validation_result"}` set called "produced by step.py";
nothing produces it before the model, so the test was passing for the
wrong reason.  It now drives the shipped `llm_output_declarations`.

**`creative_cohesion.step` - deleted.**  A declared output carrying the
constant `"5.03_creative_cohesion"`: the step's own id, which the
ledger, `project_layout` and the `step_outputs` key it is filed under
already carry.  Replayed on 001's real inputs, the only difference in
the step's output is the removed key.

**`verify_reels.reel_verification` - given a reader.**  The record names
the plan a build was graded against and the timelines that were graded,
and `cmd_build_reels` printed only `op.name: status`, so a build said
"nothing raised" and never said what had been looked at.
`manage_project._report_reel_verification` reads it.  Driven with the
captain's real `lucie/geo-podcast` build record it names the plan and
all 20 timelines; a record naming none SAYS so rather than printing a
bare pass.

**`temporal_index.total_reused` - the check on it failed correct
output.**  `validate_step_output` treats any `total_*` output of 0 as
"semantically empty", and zero reused clips is the CORRECT answer on
every run that transcribed fresh - which is what project 001's run of
record did.  Measured on that run's own `1_04_temporal_index/output.json`:

| | issues reported |
|---|---|
| before | `Step 'temporal_index' output 'total_reused' is semantically empty: 0` |
| after (`may_be_empty` declared) | none |
| after, with `total_indexed` forced to 0 | `output 'total_indexed' is semantically empty: 0` |

The false positive is gone and the real defect still fails.

**`catalog.source_resolution` - decided, and the false comment
deleted.**  It stays unread, and it should: it is ONE project-wide modal
number over 17 clips, and every decision that needs a resolution needs a
per-clip one.  `_conform_fields` reads each clip's own `width`/`height`
off the catalog, and `apply_fusion_comps` measures live off the media
pool item at the moment it composites.  Giving the modal number the
reader its comment claimed would put back exactly the grain error the
captain's ruling of 2026-08-19 removed.  The sentence in
`step_5_04_compile_manifest/step.py` that sent the last reader looking -
"used for conform decisions only" - is deleted.

**The counts stay unread, and each entry now carries the proof.**
`scan.total_files` 17 = `len(raw_footage_files)` 17;
`catalog.total_clips` 17 = `len(clip_catalog)` 17;
`semantic_analysis.total_clips_analyzed` 17 = `len(documents)` 17;
`temporal_index.total_indexed` 17 = `len(full_indices)` 17;
`assign_aroll.total_a_roll_segments` 8 = `len(a_roll_assignments)` 8 -
all on 001's run of record.  `sfx_library_status` stays unread because
0.01's gate is its own exit code, and its manifest description named
three fields (`profiles_count`, `index_searchable`, ...) that the step
has never produced; it now names the seven it does.

**`ocr_extraction.ocr_extraction` is the one live finding.**  1.07 reads
the on-screen text off every frame and has no outgoing edge.  It costs
445s on 001 and the standing mitigation is already in code - the step is
DESELECTED BY DEFAULT for exactly this reason.  Whether a planning step
should read the text is the captain's decision, not the table's.

### The edge checker had only ever looked at one process

`validate_dag_contracts.run_validation` opened
`library/processes/edit_video/dag.json` BY NAME, so the `reels`
process's edge - `build_reels` -> `verify_reels`, carrying `reel_build`
- was never checked by the gate that exists to check exactly that.  It
now reads `processes.every_dag()`, which scans the directory.

### The timebase two planners never received

`step_4_02_plan_transitions/post_bridge.py` read
`data.get("frame_rate", 30.0)`, and `step_4_03_plan_vfx` reads the same
key.  **Nothing in this pipeline has ever produced `frame_rate` as a
step input**: the catalog measures the timebase and calls it
`project_fps`.  No edge carried `project_fps` to either step, so the
default won on every run.

Step 4.04 was given the edge and the read in #124, and its manifest line
says why in as many words: *"Every consumer used to read its own 30.0
default because no edge carried it."*  Two of the three consumers were
left behind, and the reason it stayed invisible is that project 001 is
30 fps, so the wrong answer and the right one agreed.

`lucie/geo-podcast` is **23.976 fps**.  `duration_map` scales with
`frame_rate / 30`:

| `duration_feel` | frames at 30.0 | plays for | frames at 23.976 | plays for |
|---|---:|---:|---:|---:|
| quick | 6 | 250 ms | 4 | 167 ms |
| medium | 10 | 417 ms | 7 | 292 ms |
| slow | 15 | 626 ms | 11 | 459 ms |

Every drawn transition on that project held about 50% longer than the
word the model wrote asked for.  4.02 now takes `project_fps` on the
catalog edge and reads it first; `tests/test_transition_frames_use_the_projects_timebase.py`
fails in three places with the old read restored.

4.03 is deliberately NOT routed: it threads `frame_rate` into
`resolve_vfx` and `resolve_generator_overlays`, and **neither function
body mentions the parameter**.  Routing a value nothing reads is the
defect being audited, so the dead parameter is pinned by a test instead.

### align_sfx_to_prosody has never fired, and is now deleted

`step_4_04_plan_sfx/post_bridge.py` read `data.get("prosody_analysis", {})`
and handed it to `audio_reactive_sfx.align_sfx_to_prosody`, which claimed
to snap whoosh and transition SFX to pause boundaries and impact SFX to
emphasis peaks.  The audit found three independent reasons it could not
fire; following the value to the end found **five**:

1. **Nothing routes the input.**  No edge in `edit_video/dag.json` maps
   `prosody_analysis` into `plan_sfx` - 1.05 routes to
   `creative_direction` and `speech_sequence` only - and 4.04's manifest
   declares no such input.  A post-bridge's stdin is
   `gather_step_inputs`' dict, so the key is absent and the guard is
   never true.  Measured on 001: the merged input keys are
   `b_roll_assignments, clip_catalog, creative_brief, creative_direction,
   music_analysis, music_selection, project_config, project_folder,
   project_fps, rough_cut_review, semantic_analysis_documents,
   temporal_event_indices, timed_spine, transition_spec` - no prosody.
2. **The shape is wrong.**  It reads `pauses` and `emphasis_peaks` at the
   TOP LEVEL; 1.05's output on 001 is
   `{available, error, profiles, total_clips}`, and a per-clip profile is
   `{analysis_time_s, audio_file, clip_id, prosody}`.
3. **The measurement does not exist.**  `analyze_prosody` has never
   emitted either key at any level: `pitch_stats`, `pitch_contour_10ms`,
   `voice_quality`, `speaking_rate`, `intensity_contour_50ms`,
   `duration_s`.  **This is the real reason.**  1 and 2 are routing, and
   there was nothing to route.
4. **It branches on a vocabulary the step withdrew.**  Both surviving
   branches test `sfx["type"]` for "whoosh"/"transition"/"impact".  A
   plan entry is `{spine_block_position, sfx_id, volume_db,
   duration_seconds, rationale}`.  001's archived plan carries
   `{rationale, sfx_id, spine_block_position, volume_level}` - no `type`.
   The type words went when placement was re-keyed onto the sound's own
   MEASURED envelope.
5. **It writes a key nothing reads.**  It sets `sfx["start_time"]`;
   `_locate_sfx` positions an entry from `spine_block_position` /
   `target_block_position` / `timeline_start` / `timeline_in`, and
   `start_time` appears nowhere else in the step.

Driven against 001's real plan and 001's real 1.05 output, the function
returns the plan **unchanged** - and unchanged again when the missing
`pauses`/`emphasis_peaks` are hand-supplied at the top level, because
reason 4 still blocks it.  So deleting it changes nothing a viewer sees,
and that is measured rather than assumed.

**The capability is not lost.**  Three lines below the deleted call:
`find_sfx_placement` snaps a `punchy` sound onto a measured audio ONSET
within +/-200 ms and falls back to a measured energy peak, and
`_avoid_speech_collision` moves a sound that would land on speech into
the nearest GAP BETWEEN WORDS, from WhisperX word end times in the
timeline domain.  Those are the same two ideas from stronger signals,
keyed on measurement rather than on a word in a name, and applied to
every sound rather than two name prefixes.  001's one planned sound
records `placement_method: "scene-boundary/block-edge -> onset-snap
(+/-100ms)"` - that mechanism saying which route it took.

**Resurrecting it instead is ranked LAST and costed.**  A pause detector
and an emphasis-peak detector would both have to be written, a threshold
invented to call a contour sample a peak - which AGENTS.md 10.5 forbids
- a clip-time to timeline-time mapping added, and the result would
compete with two mechanisms already reading better signals.  The
function is DELETED, the way `scale_sfx_density` was, with the whole
record in `library/tools/audio_reactive_sfx.py` and a guard in
`tests/test_no_creative_floors.py`.

### The name that means two things

Step 1.04 declares BOTH `temporal_event_indices` (summary rows:
`index_path`, `speech_duration`, `total_words`, counts) and
`full_indices` (the whole per-clip index).  Every one of its ten DAG
edges carries `full_indices` - three of them renamed to
`temporal_event_indices` at the consumer.  So the same name holds two
different shapes depending on which side of an edge you read it from,
and six sites across four steps carry a dead `isinstance(dict)` branch
reaching for the envelope that never arrives
(`step_3_02_select_broll/bridge.py:157`, `post_bridge.py:438`,
`step_4_02_plan_transitions/post_bridge.py:448`,
`step_4_04_plan_sfx/bridge.py:137`, `post_bridge.py:643`, and
`step_2_02_speech_sequence/post_bridge.py:792`, which reaches for
`index_dir`).  Nothing is wrong today; what is wrong is that the
belief encoded in six places is false, and a re-map to the envelope
would silently hand four of them the summary rows.

### link_group_id is a uuid nobody reads

`mesh_spine/post_bridge.py:100` mints a `uuid.uuid4()` per speech block
"for A/V synchronization ... so the XMEML generator can pair video and
audio clipitems using reciprocal `<link>` blocks".  It is threaded
through `assign_aroll` and written onto every V1 and A1 clip by
`compile_manifest`.  **The XMEML route is closed** - AGENTS.md 5, "do
not wire FCPXML or DRP project-file surgery back in" - so the intended
reader was removed by policy and the field outlived it.  17 references
across three steps and five test files, read by no renderer, and a
fresh uuid on every compile.

### What the survey cannot see, and says so

The `code` route is a key-NAME match, so it over-credits consumption -
in the safe direction, since a gate that fails correct output is the
same defect as one that cannot fail.  Three collisions are recorded in
`output_contract.KNOWN_NAME_COLLISIONS`.  A value that only reaches
`summary.md` is not read at all: `step_exporter.generate_summary`
renders whatever keys an output happens to carry.  And it is an
OUTPUT-level question; a field inside an output is invisible to it.

`output_contract.uncalled_functions` is the one field-level half that is
mechanical, and it was calibrated against a published finding rather
than against itself: run over the tree at `33c421c^` - the commit before
the one that wired it - it reports `enforce_min_duration
library/steps/step_4_01_plan_subtitles/step.py:292`, which is issue
#564 word for word.  Over today's tree it does not.  It reports 41
module-level public functions in `library/` that nothing calls, and it
REPORTS rather than fails, because escalating a pre-existing finding is
the captain's decision.

Its own first draft was wrong and is worth recording: it read
`second_pass.request` as uncalled, which would have claimed the two-pass
music measurement never runs.  The caller is
`from library.tools.second_pass import request as second_pass_request`,
and the scan did not follow import aliases.  The instrument was wrong,
not the pipeline.

## project-declared-creative-tasks

The captain, 2026-09-04, on seeing reel selection land as step 3.4:
*"wait did you make it a permanent pipeline step?? if so why? like yes
this is something the LLM has to do but at the same time it is not a
permament step in the pipeline but rather part of the spec / brief in the
project itself that can be referenced"*.

They were right and firstmate had over-corrected. The original complaint
was that the creative judgement CIRCUMVENTED the pipeline - the sixteen
reels of the field test were chosen by a crewmate's own judgement
wrapped in a validator, no model ever reached (findings section 15).
"Make it a step" fixes the circumvention but conflates two claims: "the
LLM must do this with a real brief" and "this must be a permanent step".
Reel selection is project-shaped - a marketing render or a single-video
edit has no reels - and the engine would carry a step most projects
never run.

The assessment's two halves: `project_config` already carries
`creative_brief`, which gives the same class of guarantee - a
project-declared document that provably reaches a prompt. What it does
not carry is a craft role or a task: the brief is context handed to a
step that already exists, and it never causes a model to be INVOKED. And
step-hood is what forces the invocation with a recorded prompt, and
what makes the three guards reach it - `craft_role`'s role plumbing,
the floors gate's derived roster, `direction_contradiction`'s coverage.
A brief that nothing invokes is a document, and a document a worker
reads and then acts on in-turn is the section-15 failure with better
paperwork. Any move had to REPLACE the forcing function, not relocate
the text.

So the mechanism routes a project-declared task through
`present_llm_step` itself - the same recorded prompt, schema rendering,
backends, QA loop and collectors - and reconciles the three guards
against the declaration: the role prepended by the shared renderer, the
floors gate reading the task's prompt (and refusing a floored
declaration), the contradiction field rendered from the task's own
declared evidence. `select_reels` was deliberately NOT migrated on top
of it: the record makes the migration conditional on the mechanism and
defers it past the live field test, and a migration folded into a live
test is the collision this task's out-of-scope list exists to prevent.

## the-true-return-that-lied

Reel 09, 2026-09-09: the staging the build gate deleted carried six
items at the identity transform, each delivering (0, 656, 1080, 1264)
against a screen window of (18, 260, 1061, 1661). The aim HAD run and
`SetProperty` had returned True on every call - past Resolve's silent
Pan/Tilt clamp, which holds the value at 4x the frame and reports
success. A True return past a clamp is not a transform that took.

The discipline, applied in `aim_picture_row`
(`library/tools/reel_build.py`) under PR 862's rule: what was ASKED is
judged by the return, and what is HELD is graded against the window
with the same predicate the gate grades with
(`reel_look.uncovered_window_edges`). A transform that did not take
raises `PunchInLeavesBlack` at placement - never as six identical
findings after a full build. `tight_box` holds the same line: every
SetProperty is still judged by its return (`library/tools/tight_box.py`).
Pinned by `tests/test_punch_in_readback.py`.

## the-probe-that-could-not-run

The same Reel 09 gate failure had a second cause underneath the first.
The aim never ran on any shot: system python's cv2 5.0.0 ships no
`CascadeClassifier`, so `measure_subject_in_window` answered None for
every shot without decoding a frame, `punch_in_properties` refused
every shot, and the placer left all six items unpunched. The gate named
the symptom; the cause was an incapacitated probe the build never
mentioned.

None from this function means "frames were read and no face was
measured" - genuine absence, which leaves the shot uncropped under the
refuse-rather-than-guess ruling. A detector that could not even be
loaded is a different fact and raises `SubjectProbeUnavailable` naming
the interpreter's cv2, before any frame is decoded, so the caller
refuses the build with the cause instead of shipping staging the gate
deletes. The same line `render_qa.measure_face_intact` draws with its
"No Haar cascade available" warning: a measurement that could not be
taken must say so. Pinned by
`tests/test_subject_probe_unavailable.py`.

## the-name-that-said-where-instead-of-what

The captain, 2026-09-04: rendered overlays were named
`sub_block_<block_position>` - an ordinal within one spine, written
into a directory that is per PROJECT and not per timeline. A master
and a reel both have a `body_1`, so one overwrote the other, and no
part of the name said whose speech it captioned or which span of
source audio it came from.

`segment_identifier` (`library/tools/subtitle_segment_id.py`) renders
as `sub_<timeline>_<speaker>_<block>_<span_ms>_<digest>`: readable
left to right, unique on the right. An identity key carries what the
content IS, never where it sits or how long it plays - and what is
absent is NAMED, not omitted: an unnamed timeline renders as
`notimeline` rather than inventing one, because an unnamed timeline
that collides is a visible bug and a made-up name that does not is a
silent one. `assert_named_timeline` and `assert_unique_segment_names`
are the two guards that make collision unrepresentable rather than
merely documented. Pinned by `tests/test_subtitle_segment_id.py`.

## scratch-held-what-live-timelines-play

`scratch/` is declared safe to throw away at any moment, including
mid-run (`Kind.SCRATCH`). Yet two things the reel builder places on
live timelines were rendered into it: TV-frame overlays
(`reel_look.frame_overlay_segments` into `scratch/reel_look/frame_overlays/`)
and full-frame cards (`reel_build.card_render_dir` into
`scratch/reel_cards/`). Measured 2026-09-09: five overlays, 1.5 GB,
every one of them on V2 of a live reel timeline, read off the live
Resolve database. So the declaration was a lie for those paths, and
anything trusting it - a cleaner, a re-run, a disk sweep - takes the
picture off live timelines.

The artefacts are not scratch, so the placement path stops referencing
scratch: every overlay and every card is PROMOTED into a step-owned
OUTPUT area (`Area.REEL_FRAME_OVERLAYS` / `Area.REEL_CARDS`, both
`build_reels`' own directories under `pipeline_output/steps/`) and the
durable copy is what gets imported and placed. Recorded where the next
contributor meets it in `library/tools/reel_placed_assets.py`.

## the-operation-that-answered-unasked

A post-bridge operation takes three things - the step's inputs, its
pre-bridge's output, and the model's answer - and an operation gathers
only the first. Resolving one without the model's keys would produce a
confident answer to a question nobody was asked, so it is REFUSED by
name instead: `operations.py` names exactly which keys the model owes
the step (`missing_model_answer`) and states the two ways out - supply
them as overrides, or run the step so the runner asks the model.

The same ruling at run scope: the summary reports SUCCESS only when
the whole DAG is complete and `failed_steps` is empty in the project
ledger - not just the steps one invocation touched
(`library/processes/edit_video/run_pipeline.py`) - and a detector
names steps that report success while emitting nothing usable. A build
that owes an unanswered model request must not report success.

## the-captions-at-the-clamp

The captain reported the same defect twice: captions in the Reel 09
timeline sitting off their band, and motion graphics on very large X.
The first repair read the transform back and gated it; the captain
reported it again.

The approach was the defect, and the gate was calibrated wrong.
Resolve's per-clip Pan/Tilt are expressed in a unit relative to the
CLIP's own size - measured on Resolve 21, `shift_x = Pan * (placed_W /
timeline_W)` and `shift_y = -Tilt * (placed_H / timeline_H)` - so the
transform needed to carry a box is INVERSELY PROPORTIONAL to the box,
while Resolve pins |Pan| and |Tilt| at a rail it does not report and
refuses silently past it: setting beyond returns True and reads back
the clamp.

**Read off the live timeline**, 2026-09-09, project "Podcast (field
test)", both Reel 09 timelines at 1080x1920, against
`geo-podcast/pipeline_output/steps/4_05_render_subtitles/render_ledger.json`:

| canvas h | Tilt asked | Tilt Resolve HELD |
|---|---|---|
| 152 | -7578.9 | **-3840.0** |
| 158 | -7254.7 | **-3840.0** |
| 160 | -7152.0 | **-3840.0** |
| 164 | -6954.1 | **-3840.0** |
| 166 | -6858.8 | **-3840.0** |
| 220 | -4939.6 | **-3840.0** |
| 224 | -4834.3 | **-3840.0** |
| 232 | -4634.5 | **-3840.0** |
| 236 | -4539.7 | **-3840.0** |
| 242 | -4403.3 | **-3840.0** |
| 244 | -4359.3 | **-3840.0** |
| 300 | -3366.4 | -3366.4 |

37 of the 39 caption items are pinned at exactly -3840.0 whatever they
asked for; the two held exactly are the two that asked for -3366.4.

**The rail is a constant in property space, not a clip-relative cap**,
and that is the one thing this data settles. The Pan/Tilt UNIT is
relative to the clip, so a rail capping the resulting SHIFT would show
a different Tilt limit for every box size. Those 37 items span **17
distinct box geometries** - heights 152..246, a 1.62x spread, widths
840..902 - and a shift-capped rail would have read back twelve
different values from -3840 down to -2373. Every one read back
-3840.0.

**3840 is recorded as a NUMBER, not a formula.** It equals two times
this timeline's height, but one timeline geometry cannot distinguish
`2 x the height` from `2 x the longer side` from a fixed constant, and
turning one data point into a formula is precisely how `four times the
timeline dimensions` came to be written down as measured fact. Nothing
in the code depends on it.

Every caption's tight canvas is bottom-anchored with its lower edge at
y=1636, so the Tilt it needs is `tilt(h) = -(676 - h/2) * 1920 / h`,
which crosses 3840 at **h = 270.4px**. A tight canvas is the drawn ink
plus 52px of pad, so only the single three-line card (h=300) cleared
it; every one- and two-line card did not.

**That is why the first repair did not hold.** It gated at four times
the timeline dimensions - 7680, twice the rail that was actually there
- so it refused exactly one card (-7692.8) and let the other 37 ride
straight onto a clamp it was calibrated to miss. `SetProperty`
returned True every time. A gate is only as good as the constant it
carries, and this API reports neither its limits nor when it has
ignored you.

**It is a clamp, not a value something set.** The asked-vs-held table
proves it four ways. The split is perfectly ORDERED - every value
asked below 3840 survived to the last digit, every value above it was
pinned - which is what clamping is and what nothing else produces. The
rail is independently BRACKETED to `[3366.4, 4316.1]` by which items
were left alone, and 3840.0 lies inside that bracket; a constant
something merely wrote has no reason to land there. No box geometry in
the project could EMIT -3840 from the carriage's own arithmetic (it
needs h = 270.4; the heights present are 152..246 and 300). And a path
setting a flat -3840 would have set it on the two -3366.4 items too.
There is no literal `3840` anywhere in `library/`, and the only writer
of Tilt was `overlay_placement`, writing a computed value.

**The asked values are trustworthy**, which matters because the table
rests on them: each is reproduced to within 0.01 by `tilt(h)` from the
box height recorded beside it, so they are internally consistent
rather than transcribed; the ledger records the REQUEST at render
time, before placement, so asked-vs-held is a real before-and-after
and not one number read twice; and all 39 items paired to their ledger
entry by a filename carrying a content digest of the props, so the
artefacts on the timeline are the ones the ledger describes even after
another lane rebuilt the reel.

**The Inspector agrees with the API - a settled NEGATIVE.** The captain
read the Edit page Inspector on 2026-09-10: *"all of them are -3840,
except for 2 subtitle clips, one which is y=0 and another which is
y=-3366.4"*. Both anomalies are in the data: -3366.4 is the single
tight caption whose canvas (840x300) clears the 270.4 cliff, and y=0
is a caption carried FULL CANVAS, which needs no shift at all. So
there is **no display scale**, and the theory that the Inspector showed
twice the API value - which would have neatly explained the old
four-times figure - is dead. Recorded because a plausible explanation
that turned out to be false is exactly what a later reader would
otherwise re-invent. (The captain counted one y=0 where the data has
two per timeline, `akshita_4` and `akshita_16`; both are full-canvas
fallbacks with the same explanation, so it reads as a miscount over 22
clips rather than a discrepancy.)

**Where the captain's original -7680 came from is UNRESOLVED**, and no
story is offered for it. What it is NOT: not a display scale (the
Inspector agrees with the API), not an API scale (values inside the
rail round-trip to the last digit), and not clip-size dependent (one
rail across 17 box geometries). The number does occur once in this
project's own records - inside the first repair's refusal text, `Tilt
-7692.8 exceeds +- 7680` - but that text was written AFTER the
captain's first report, so it cannot be its source. Left open.

What the clamp does to the picture, and what it does NOT do: a pinned
Tilt moves the box `2h` down from centre instead of the `676 - h/2` it
needed, so the caption rides `676 - 2.5h` px HIGH - 296px for a
one-liner, 66px for a two-liner. It is misplaced, never absent, and a
clamp can only ever pull an overlay TOWARDS the frame centre, so it
cannot on its own put one off frame. The measurement explains captions
sitting wrong and bunched; it does not by itself reproduce "completely
off frame", and that is stated rather than papered over.

So the position stopped being a transform. An overlay artefact IS the
delivery frame, its position is baked pixels, and the placer writes no
position property at all - there is nothing left for Resolve to clamp,
and nothing left to calibrate wrongly. A motion-graphics segment still
renders once at its drawn union and is PADDED onto the frame
(`tight_box.pad_to_delivery_frame`), verified frame by frame on the
delivered file: measured 0.71s for a 43-frame clip, 1.80MB -> 4.06MB,
worst channel difference 3 inside the pasted rect and alpha exactly 0
outside it, with the ink bbox translating by the pad offset with no
drift at any alpha threshold.

`subtitle_overlay_geometry: tight` is RETIRED rather than repaired.
Measuring a caption's tight canvas needs a FULL-CANVAS probe render
first (`_probe_tight_box`; nothing ever passed `probe_mov`), so tight
cost TWO Chromium launches per segment - and once position is pixels,
the padded tight file is exactly the probe. It delivered the same
artefact for twice the render. Retiring it halves the caption render
launches, which is the cost PR 725 existed to reduce. The disk price
is stated rather than hidden: one reel's 21 caption overlays go from
174.6MB to a measured-predicted 277.0MB (x1.59). A project that still
declares `tight` renders full canvas and is TOLD so on every segment.

Two smaller lessons are recorded in the code. The reuse key now names
the CARRIAGE (`overlay_mode.OVERLAY_CARRIAGE`), because an artefact
from the Pan/Tilt era is not stale, it is UNUSABLE - reusing one under
today's rule would place a small clip with no transform, centring the
caption in the frame. And the read-back RAISES now, where the first
repair only reported: while position rode Pan/Tilt a clamped caption
was routine and failing a build over a movable miss would have traded
a misplaced caption for a missing one, but nothing is routine once the
clamp is unreachable. Footage clips are not overlays - the live
measurement shows A-roll and B-roll carrying the captain's own
reframes and the per-shot punch-in aims (Pan -35.0, -14.204, -38.372,
Zoom 2.307) - and the identity check is scoped to overlay artefacts
only.

Correction, 2026-09-10, fifteen-lane merge: the retirement above did
not survive composition. The captain's standing order is tight
overlays, and the floor answers the cost argument the retirement
rested on - the cliff sits at h = 270.4 against the measured 3840
rail, so a canvas floored at 480 (`tight_box.MIN_CANVAS_HEIGHT`,
grown away from the anchor so the ink does not move) places every
caption with headroom (worst cases under 3400), and the clamp gate
(`placement_holds`, now calibrated to the measured 3840 rather than
the disproved 7680) still refuses at render time what even the grown
canvas cannot hold. The carriage is `tight-480-1`
(`overlay_mode.OVERLAY_CARRIAGE`): tight canvas, Scaling/Pan/Tilt set
at placement time and read back, mismatches REPORTED by name rather
than raised. The rail measurement, the bracket, the Inspector
negative and the cliff arithmetic above all stand - they are what the
floor and the gate are calibrated against.

## the-powergrade-with-no-grade-in-it

The captain: *"ok can you fix the color grade?"*

`The Grade Free 1.13.1.drx` was applied to all six of Reel 09's picture items,
with all eight of its own nodes, correctly labelled and enabled - and it changed
nothing. Two lanes had measured 0.023%; the lane that applied it had recorded
28.9%. Both could not be true of the same clips.

### The .drx contains no grade

Measured 2026-09-11 on `Podcast (field test)` / Reel 09, frame 300, through a
one-frame Deliver render. Each of the eight nodes bypassed in turn:

| node | bypassed alone | max delta |
|---|---|---|
| 2 `BAL/EXP` | **0** px | 0 |
| 3 `CONTRAST` | **0** px | 0 |
| 4 `SAT` | 49 px | 1 (rounding) |
| 5 `W&B` (OFX Chromatic Adaptation) | **0** px | 0 |
| 7 `FLC` (OFX Film Look Creator) | **0** px | 0 |
| 8 `Corrections` | **0** px | 0 |
| 1 `Input` (OFX Color Space Transform) | 1,451,766 px | 123 |
| 6 `Output` (OFX Color Space Transform) | 1,451,827 px | 106 |
| **1 and 6 together** | **554 px** | **7** |

The two Color Space Transforms are an exact inverse pair - `TIMELINE_COLORSPACE`
/`AUTO_GAMMA` to `DWG_COLORSPACE`/`DAV_INTER_OETF_GAMMA` and back to
`REC709_COLORSPACE`/`TWOPOINTFOUR_GAMMA`, read out of the file's own zstd-
compressed node body - and everything they wrap is identity. The whole grade
nets to 486 px of 2,073,600: **0.023%**.

The instrument was proved in the same session on the same clip: `SetCDL`
saturation 0 moved **1,435,016 px (69.2%)** at max delta 96. The Color page
reaches the render; there was simply nothing in the grade to render.

**Colour management is not the cause.** The leading hypothesis was that a
colourist's PowerGrade assumes DaVinci Wide Gamut under colour management and
lands near-neutral on Rec.709 in an unmanaged project (this one is
`davinciYRGB`). The CST pair cancelling to 554 px refutes it: the round trip is
already exact, and no colour-management change gives the grade content it does
not have.

The one node that DOES carry a look is `FLC` - `flPreSat 0.7`, seven hue spheres,
split-tone blend 0.5 midpoint 0.336, lum/sat curves - and it renders nothing. It
is instantiated (`GetToolsInNode(7)` returns `['OFX: Film Look Creator']`) on
Resolve **Studio** 21.0.0b.28, so it is not a licence. The `.drx` was authored in
20.3.2. Clearing the two per-node flags that differ between `FLC` and the three
OFX nodes that DO render, in a rebuilt copy of the file, changed nothing either.
**The look cannot be woken from a `.drx` here.**

This is what the grade's own author says it is
(`brand_assets/TheGradeFree_README.txt`): *"This is a STARTING POINT, not a
finishing point ... Color Space Transform (node 02) - set to your camera.
Exposure (node 03) - dial in for your specific clip. White Balance (node 04) -
correct before grading. Do not apply and export without adjusting."* A free
starting-point PowerGrade applied unmodified is expected to be near-identity.
That is not a defect in the file; it is a defect in shipping it unmodified.

### The 28.9% is withdrawn, and why the two disagreed

The 0.023% reproduces EXACTLY across lanes - 486 pixels of 2,073,600, the same
integer from two independent sessions - through a one-frame Deliver render.

The 28.9% came from `Timeline.GrabStill()` plus
`GalleryStillAlbum.ExportStills`, which **on this build returns False and writes
no file at all** (reproduced 2026-09-11; recorded independently in PR 916). A
capture route that cannot write a file cannot settle a pixel question.

The corroboration is in the shape of that lane's own numbers: 599,583 for the
DRX, 601,760 for its `SetCDL(saturation 0)` control, and "29% of pixels" for
`ResetAllGrades` against the untouched original. Those are one constant - the
picture area - not three magnitudes. **A pixel COUNT above a 1/255 threshold
saturates the moment anything perturbs the whole picture**; it does not
distinguish a grade from a re-decode. Report the magnitude beside the count, or
the count reads as a result.

Nothing neutralised the grade between then and now. There was never anything to
neutralise.

### What actually lands

The project already declared the look the captain picked - `v04_teal_split`
(slope 1.03/1.0/0.96, offset -0.01/0.005/0.02, saturation 1.12) - and it was
being *displaced* by the inert `.drx`, because a declared DRX replaces the whole
node graph including the node `SetCDL` writes.

`color_page_grade` already had the mechanism to land a CDL by LABEL inside the
applied graph. Declaring `cdl_node: "BAL/EXP"` puts the captain's own CDL on the
node this grade's author ships for exactly that, inside the DWG/Intermediate
working space. Measured on the delivered timeline: **1,452,396 px moved, 70.0%
of the frame, mean |d| 2.59**.

The reason it had been withheld - that v04 would be "a second creative look"
fighting the DRX's own - is refuted rather than overruled: with the DRX being
identity, DRX+CDL against CDL-alone differ by **2 pixels at >= 8/255**. There
was no look to fight.

### The rule

**A node count is not a grade.** `apply_power_grade` reporting `applied: True,
nodes: 8` was true and meant nothing. A look is verified in exported pixels
before it ships, per `.drx`, or it is not verified - AGENTS.md 10.4: a gate that
cannot fail reads as coverage.

## the-variant-that-dropped-the-captains-closer

The captain, on the two Reel 09 timelines he was comparing:

> *"there is another timeline which has the 8 frame cutaway to akshita, but does
> not have the updated cta or the color grade"*

He was right, and the cause was not the cutaway. `build_reel_variants` (PR 890)
was written to run "the SAME derivation the rebuild runs today", and it does -
for ranges, cards, captions, explainer, semantic visuals, overlays, look and
motion. But it read `transcript_corrections` and `resolve_grade_cdl` and **not**
`captain_edits.apply_closer_redraws`, and **not** `reel_look.resolve_power_grade`.

So the reaction-cutaway variant opened its closer 54 frames later than the
approved reel - source 483.563s against 481.311s - and would have been graded by
`SetCDL` while the reel it exists to be compared against is graded on the Color
page. A variant that differs from the approved timeline anywhere but its seam is
not a comparison; it is two changes at once, and the difference the captain
reads as "the seam" is partly something else.

**Both were invisible to every check that ran.** The build printed
`conformance-clean (6 checks)` either way, because conformance grades STRUCTURE
and a dropped obedience is structurally perfect: every frame is covered, every
row is the plan's, every item is linked. Nothing was malformed. Something was
simply not applied.

The guard is therefore not another structural check. It is:
`tests/test_reel_variants_carry_recorded_obedience.py` - whatever
`rebuild_reels_in_project` reads in order to obey a decision the captain
RECORDED, `build_reel_variants` reads too. It parses both functions and asserts
the subset, and it names the missing call in the failure. It fails on the code
as PR 890 landed it, naming `apply_closer_redraws`.

The list is explicit rather than inferred: a heuristic over every call in a
6,000-line module would either miss one or drown the failure in noise. Adding a
new recorded obedience means adding it to that list - which is the point.

## the-single-frame-that-rendered-fourteen-hundred

Asking `segment_renderer.render_single_frame` for frame 300 of Reel 09 on
2026-09-11 queued a job with `MarkIn 0` / `MarkOut 1665` and rendered **1,471
TIFFs of the entire reel** before it was stopped by hand. Resolve ignores
`MarkIn`/`MarkOut` in `SetRenderSettings` unless `SelectAllFrames` is also set
False.

This is AGENTS.md 5 arriving from a new direction. The rule there is to judge a
Resolve call by what it RETURNS - and here every return was fine.
`SetRenderSettings` returned True. `AddRenderJob` returned a job id.
`StartRendering` started. What was never read back was the QUEUE, which is the
only place the range either took or did not.

The cost of finding out afterwards is the whole timeline. The captain's machine
is the fleet's one hard CPU limiter, and a "single frame" that renders a whole
timeline is not a slow check on it - it is an outage. So `render_segment` now
sets `SelectAllFrames: False`, reads the queued job back off `GetRenderJobList`,
and REFUSES before `StartRendering` if the range is not exactly what was asked
for. The job is deleted either way.

`tests/test_segment_render_range_takes.py` drives it with a fake project whose
queue reports the wrong range, and asserts nothing was started. Three of its
four tests fail on the unfixed module; the fourth is the
does-not-refuse-correct-output half, which must pass both ways.

## Section 5 - DaVinci Resolve (continued)

### the-bins-that-came-back-three-times

The captain's screenshot of 2026-09-10: one reel showing up twice
under both `06 - Subtitle renders` and `07 - Motion graphics`, a
leftover `SOP Proof_...` bin with its timeline, and an `Unrecorded`
bucket holding eight variant timelines.  The third report of the same
bins.  Two previous lanes had reported it fixed - PR 912 (the sweep
runs itself) and PR 927 (canonical orphan sweep) - and both were still
open and unmerged, which is half the answer.  The other half is that
neither would have fixed it anyway: both kept the EMPTINESS bar, and
the dead leaves are not empty.

Measured off HEAD code, not the screenshot: per-reel bins are keyed
by placing-timeline NAME (`plan_organization` files a clip under
`(render_bin, placer)`), every variant and every superseded build is
a new timeline name, timelines are never deleted (the captain's
2026-09-06 ruling, *"a refusal is cheap and a deleted timeline is
not"*), and `plan_retirements` could only retire empty legacy-scheme
bins - canonical per-reel leaves never matched `is_retired_scheme_bin`
at all.  Proven both ways in a REPL: two timeline names for one reel
produce two leaves and zero retirements, and deleting one timeline
still produces zero.  So the old design was an ACCUMULATION of
everything ever built: every build made it worse and no amount of
sweeping kept up.

The fix makes the tree a function of what currently exists:
`plan_dead_render_bins` retires a per-reel leaf naming no live
timeline WITH its contents - proven unplaced and pipeline-generated,
journalled with what it held, pool items only, files never unlinked -
and declines anything else by name (a placed clip inside, a timeline
inside, foreign material, a sub-bin standing under it).  A leaf
naming a timeline that still exists stays, whatever that timeline's
state: `Earlier plans` reels are reality too.

`Unrecorded` keeps its bucket: the exact-match rule means a suffixed
one-off is unclassifiable BY DESIGN (calling it EARLIER would assert
by prefix a provenance nobody recorded), and the bucket is where such
timelines are kept rather than deleted or misfiled.  The `Not placed`
leaves stay canonical destinations whose removal belongs to the prune
path under its own authority.

The proof timeline and the merge-demo timeline are the exception that
proves the no-delete ruling: both go by EXACT name under the
captain's verbatim authority (*"yeah clean that up"*, plus the
standing instruction that crew demo leftovers are cleaned up), with
the four protected timelines verified present and untouched in the
same plan - `library/tools/proof_cleanup.py`.

## the-promote-that-never-looked-back

Three drops in one day, one shape: a build that REPLACES a timeline
was judged only against its own PLAN, so no gate could fail on a
feature the plan does not name (issue #925).

1. The cutaway. `Reel 09 - your-website-is-only-20-percent (final)`
was built 11:45 through `build_reel_variants` with a `plan_cutaway`
offset: Craig's picture hidden over reel frames 574..598, an Akshita
cover (LC4932 @ src 34788, 24 frames) revealed underneath. A 12:33
`build-reels --only-reel 9` derives everything from the plan - and the
cutaway is an intentional deviation from the plan, so the rebuild
reproduced the plan faithfully and dropped it. V1 went 3 picture items
to 2, the two Craig spans grew (55->67, 59->71) to close the hole so
the picture stayed continuous, and the build reported `PASSED: 0
errors, 4 warning(s)`. The captain found it by opening the timeline.

2. The semantic visuals. Two builds ran from a tree whose
`remotion-subtitles/` had no `node_modules`: all four semantic visuals
printed `WARN: Render failed`, the whole V5 'Semantic' row was absent
from the built timeline, and the build was `conformance-clean (6
checks)`.

3. The Fusion comp bank (fixed in #923): keyed over the inputs a comp
was built from and not over the builder, so a build imported a comp a
different build had generated.

Why the existing gates could not catch any of them:
`reel_conformance_verifier` grades the built timeline against the plan,
and a feature not in the plan is invisible to it in both directions -
it cannot notice one missing, and would flag one present. The
structural gate (`no_empty_tracks, named_tracks, singleton_roles,
aroll_linked, captions_linked, program_stream`) passes on a timeline
with no cutaway and no Semantic row: a shape check, not a content
check. The planned-vs-built NO-REFERENCE check was captions-only and
all-or-nothing (`if planned_captions and actual_captions: return []`,
so 48 planned and 1 built passes) - the gate that cannot fail for
every overlay family except one.

The fix is at `promote_staged_reels`, the one place a
captain-visible timeline is replaced: before the first rename, diff
the incoming timeline against the one it retires, live against live -
per-row item counts and which named rows exist. Any row that loses
items, or exists retired and not incoming, REFUSES with the exact
`--allow-drop` declaration that would proceed deliberately (issue #925
proposed `--allow-drop <row>`; it landed as repeatable `--allow-drop
ROW` / `FINAL::ROW` plus the per-final mapping). Frame totals report,
never trigger, so a shortened cut passes; an unreadable retiring
timeline refuses rather than passing. Fresh builds skip the diff -
nothing is being replaced.

`tests/test_promote_replace_guard.py` reconstructs drops 1 and 2 as
refusals, plus the declared-reduction pass, the unreadable refusal,
and the growth/shortening passes. A guard nobody has watched fire is
not a guard.

## the-still-that-was-never-taken

On 2026-09-10 a lane reported the captain's `.drx` grade moving
599,583 pixels, 28.9% of the frame. A later lane reproduced the real
number as 486 pixels, 0.023% - and found the cause: the
`GrabStill` + `ExportStills` capture route RETURNS False AND WRITES NO
FILE on this build (Resolve Studio 21.0.0b), reproduced live. All three
numbers in the first report clustered around 600,000 px because they
were a constant - the picture area - not a measurement. The captain was
told something that never happened.

Did it ever work here, or has it been failing throughout? It WORKED.
The 2026-08-28 probe took True plus a file on 21.0.0b.28, and a
2026-08-30 capture on the captain's own machine left a still record
with a PNG on disk (`docs/workflow_integration/live_api_surface.json`,
`still_20260830T233828Z`). So this is a regression in route STATE -
build, project state, page or still album, not yet isolated (no live
re-probe: a rebuild was running in the captain's project) - not a
route that never worked. What that decides about past evidence: a
capture that landed a non-empty file on disk with the gallery put back
stands, whenever it ran. Suspect is everything measured off a capture
that returned False or wrote nothing - which is exactly the shape the
new rule refuses to return.

The rule: A CAPTURE THAT DID NOT HAPPEN RAISES BY NAME.
`marker_capture.grab_still` raises `StillCaptureError` - a
`CaptureError`, so every existing handler still catches it - on a
declined grab, a False export (authoritative even beside a file), no
PNG on disk, and an empty one. The return value is evidence of nothing;
the disk was already the authority, and now the return is too.

Which routes a crew may rely on (`marker_capture`'s "WHICH CAPTURE
ROUTES ARE TRUSTWORTHY", the table to read before choosing one): the
gallery still is the ONLY route that returns the graded, conformed
timeline frame - and it is untrusted until it returns, raising on all
three failure shapes. The Deliver-page render
(`segment_renderer.render_single_frame`, via
`visual_qa_router.execute_frame_grab`) demonstrably worked while the
gallery route did not; it returns None, never a path, on any failure
and the caller turns that into a FAILED check. ffmpeg straight off a
file is cheap and Resolve-free but reads the SOURCE frame - ungraded,
unconformed, no comps or captions - and every helper
(`ask_the_footage`, `verify_treatment`, `thumbnail_extractor`,
`window_frames`, step 5.01 `grade._extract_frame`, step 6.02's helper,
`vision_pipeline_v3`, `render_qa`, the explainer overlay probe)
accepts a capture only with a non-empty file on disk.

The caller audit named three that did not notice and were fixed in
place (a zero-byte file counted as a picture): `ask_the_footage`,
`verify_treatment`, `thumbnail_extractor` - plus the vision frame
cache, which also REUSED an empty file forever - and one filed rather
than fixed (issue #942): `look_matcher.match_clips_to_reference` answers a missing
frame with an identity CDL, unreachable from its only caller after the
`grade._extract_frame` fix, so changing its contract is a separate
decision. Pinned by `tests/test_still_capture_fails_loudly.py`.

## the-caption-lift-is-eleven-pixels

On Reel 09 the captain hand-corrected all 22 captions uniformly from
the computed Tilt -1744.0 to -1700.0. At the 480-pixel tight-canvas
floor one Tilt unit is 480/1920 of a delivery pixel
(`tight_box.placement_for_box`: shift_y = -Tilt * placed_H /
timeline_H), so 44 units is exactly 11.0 delivery pixels - a uniform
correction is a systematic error, not taste.

Reels 26 (13 captions) and 30 (35 captions) - different speakers,
lengths and content shapes, no declared intent - computed -1744.0 on
all 48, and Deliver-rendered stills put their ink bottoms ~11px below
Reel 09's corrected band. The computation is content-independent, so
the 11px is a systematic error in the DESIGN row: it is corrected in
the design (`subtitle_style.CAPTION_LIFT_PX = 11`, applied to the
bottom inset only, where the probe layout is built) rather than
per segment in `overlay_intent` - a tilt pin would also go wrong on
canvases taller than the floor, where 11px is no longer 44 units.
`safe_area.py`'s own insets are platform facts and motion graphics
were never corrected this way, so neither moves. Pinned by
`tests/test_subtitle_style.py`.

## logo-on-one-reel

The captain, 2026-09-11: *"you mentioned that the end card animation of
the logo was attached to all reels, that was infact not true"*.

They were right, and the mechanism was not broken. PR 995 made the logo
card a declared closing element under `effect.full_frame_elements`,
planned against every reel the project builds; the declaration is in
`lucie/geo-podcast`'s own `project.yaml` and the code does what it says.
Measured off that project's serialized timelines under
`pipeline_output/review/`, read with Resolve closed:

    Reel 26   carries logo_reveal.mov   - rebuilt through the mechanism
    Reel 09   carries it                - the captain hand-placed it
    Reel 01, 13, 23, 28, 30, 31         - do NOT

Six of eight. Those six were built before the declaration landed and
have not been rebuilt since, so the mechanism reached the reels that
went through it and no others - which is what a build does.

Two separate failures, and only one of them is a bug:

1. **Nothing detected it.** A reel is frozen at its last build and
   nothing compared a built reel against what the project currently
   declares. `plan_provenance.json` records which PLAN a reel was built
   from and carries one document-level `built_at` for the whole record,
   so it cannot answer "which mechanisms existed when this reel was
   built" even in principle. The measurement that does answer it needs
   no provenance at all: read the reel and look for the declared asset.

2. **The engine's capability was reported as the project's state.** The
   PR said "planned against every reel the project builds", which was
   true; it was relayed as "every reel has it", which was not. Nothing
   in the reporting path distinguished the two claims, and a claim about
   reels was made without reading a reel.

The freeze ending is the control that proves this is about reaching
artefacts rather than about the mechanism: the same survey finds
`reel_freeze_*` on all seven reels that have a serialized timeline,
because that round rebuilt every one of them.

So the rule is a measurement, not a reminder to rebuild:
`library/tools/reel_divergence.py` reads each reel and answers PRESENT,
ABSENT or UNDETERMINED per declaration, the build runs it over every
APPROVED reel rather than the ones it places, and `assert_reaches`
refuses a claim the artefacts do not back. An UNDETERMINED reading
refuses a claim exactly as an ABSENT one does: "we did not look" was
never evidence that something is there, and that is the precise step at
which this claim was made.

Detecting staleness is in scope; deciding to rebuild is the captain's.

### the-comp-that-had-to-be-re-derived

Resolve's scripting API has no verb for a trim: `AppendToTimeline`
places and `Timeline.DeleteClips` deletes, and there is no move, no
ripple and no `SetMediaPoolItem`. Changing one item therefore means
deleting it and placing a new one, and the new one is a NEW OBJECT
carrying none of the old one's state. The spike of 2026-09-11/12
(`vep-work-around-the-api-not-give-up-on-it`) established that the
whole edit can be composed out of the two verbs and will render
byte-for-byte identical to an untouched copy - and found the one thing
capture-and-restore genuinely cannot do.

**A per-clip Fusion comp is keyed to the window of footage the item
plays, so a trim invalidates it.** `fusion/played_window.py` states the
law the comp is written under: comp frame 0 is the clip's FIRST PLAYED
FRAME. A 480-key push-in written for a 479-frame clip finishes 13
frames early on a 492-frame one and holds its last value, where a
rebuild would ramp across the new length. Measured: restoring the
captured comp verbatim across that extension was wrong on **489 of 492
frames**, mean 1.09/255, max 88, on a cross-render floor of exactly
0.0000. Nothing in the timeline's readable state says so. It looks
right.

The captain's ruling of 2026-09-12 attached the condition: a clip whose
played length changes has its comp RE-DERIVED through the builder,
never restored from the capture, and a composed edit that changes a
played length and cannot reach the comp generator REFUSES rather than
restoring the old one.

`library/tools/composed_edit.py` is that path, as the spike's seven
steps, and the condition is structural in four places rather than
conventional: the capture data model refuses to hold a restorable comp
across a length change, `capture_item` writes that comp to a withheld
directory the restore never reads (so there is no artefact to find),
the orchestrator refuses before it captures or deletes anything, and
after the pass the TIMELINE is the verdict - every length-changed item
must carry a comp whose media window covers its new played length, so a
generator that declined, crashed or never reached the clip is caught by
state rather than by its own report. Where "structural" stops is named
in the module: a caller that does not use the module can always call
`ImportFusionComp` itself, which is the same boundary AGENTS.md 15 draws
around `reel_read`. `tests/test_composed_edit_refusal.py` attempts the
bypass eight ways.

**Judged against a REBUILD of the same edit, on exported pixels**
(2026-09-12, Resolve Studio 21.1, a scratch project built from the
captain's footage; his own project was read and never written, and the
9-timeline census before and after is identical). Two arms per case:
one built the edited reel from scratch, the other built the original
and had the composed edit applied to it. Every arm rendered twice as
lossless PNG RGB8 and compared on the second pass, because a comp
renders slightly differently until it settles.

    case                       frames  byte-identical  subpixels differing
    in-clip trim, +13 frames      277             277                    0
    ending change, +24 frames     288             288                    0
    (an arm against itself)   277/288         277/288                    0

Five picture rows and an audio row, which is more than the spike tested.

Four things fell out of building it, each now a test:

- **An empty composition is not a treatment.** Resolve reports
  `GetFusionCompCount() == 1` for a clip carrying only its own
  auto-created `MediaIn -> MediaOut`. Counting that as a comp made the
  re-derivation refusal fire on every overlay row - a gate failing
  correct output. `reel_read` now reports each comp's tools and
  `composed_edit.treatment_comps` counts the ones that draw.
- **That empty composition's window cannot be written and is not
  stable.** Setting `GlobalIn` to 0 left it at 1 - the uncovered first
  frame that fails a whole render job - and the same handle answered a
  matching window and then a differing one with no write between. It
  draws nothing, so staging REMOVES it; the byte comparison above is
  what says that changes nothing.
- **A media window is DERIVED from the binding.** Setting `MediaSource`
  back to `Timeline` recomputed a copy's window exactly, and writing the
  four frame terms on top of that correct window moved `GlobalIn` one
  frame later and `ClipTimeEnd` one earlier. Every `SetInput` returned
  `None`, the successes and the corruption alike. A `GlobalIn` one frame
  late is precisely the `1 / 19` of the six-reel incident above, which
  makes a window-writing conform a candidate CAUSE of it rather than a
  repair for it. `composed_edit.apply_window` writes the binding,
  re-reads, and writes the frame terms only if the window is still
  wrong.
- **A newly placed item comes back at IDENTITY, and that is not a
  choice.** The first ending change appended 24 frames beside three
  shots carrying `ZoomX 2.307`; they rendered at `ZoomX 1.0` and
  diverged from the rebuild on 100% of their own frames at mean
  21.8/255, while all 264 frames in front of them were byte-identical.
  It placed, it verified, and it looked like a deliberate wide shot.
  The engine may not invent a framing (AGENTS.md 10.5), so an
  `Insertion` DECLARES its treatment and `properties={}` is how identity
  is chosen on purpose.

**What it costs, and it is not the spike's figure.** The spike measured
the mechanism at ~2 s against a ~112 s rebuild and quoted 50x. That was
measured BEFORE the captain's condition was attached, and the condition
is most of the cost. Measured here, three runs per case, wall clock
including each subprocess's Resolve connection:

    the composition itself   delete 0.01-0.14 s, place 0.25-0.57 s,
                             restore 0.03-4.09 s   (the spike's ~2 s holds)
    step 7 re-derivation     17.8-37.4 s
    staging (copy + conform)  8.9-25.9 s
    a rebuild of the same    19.4-67.1 s, of which the comp pass is 17.0-63.7 s

    median totals            case 1  rebuild 24.0 s   composed 44.1 s
                             case 2  rebuild 59.2 s   composed 54.9 s

The re-derivation is one invocation of the builder's comp pass, and that
invocation is FIXED overhead rather than per-comp work: a pass over a
timeline whose every comp was already banked and unchanged still took
25.9-73.4 s. So on this reel the composed path is not faster. The saving
it can offer is bounded by whatever a rebuild does BESIDES the comp pass
- on a real reel that is the spine, the caption renders, the freeze
render, the sweep and the gates, which is most of the 112 s - but it is
not 50x, and on a reel whose rebuild is mostly the comp pass it is no
saving at all. The captain should hear that before this is used on his
reels.

## the-scan-that-measured-itself

Measured 2026-09-16 on the captain's `geo-podcast`, when a five-reel rebuild did not survive to
promote what it had staged.

`pipeline_data.json` was **12,196,676,126 bytes**, and it had been doubling on every build:

| written | bytes |
|---|---:|
| 2026-09-12 15:03 | 7,062,699 |
| 2026-09-12 15:07 | 14,592,602 |
| 2026-09-12 15:14 | 30,741,544 |
| 2026-09-12 15:15 | 65,120,833 |
| 2026-09-12 15:17 | 138,399,008 |
| 2026-09-12 19:51 | 293,952,649 |
| 2026-09-12 20:06 | 621,891,672 |
| 2026-09-13 02:39 | 1,313,345,813 |
| 2026-09-13 02:47 | 2,767,599,563 |
| 2026-09-13 03:24 | 5,815,337,810 |
| 2026-09-15 23:56 | 12,196,676,126 |

All of it was one list, `step_outputs.build_reels.reel_build.coherence.wording`, and the loop was
two modules apart. `layer_coherence.check_wording` scans `pipeline_data.json` - regenerated step
outputs are a display like any other, and the module says so. `reel_build` records this report
back into that same file. Each half is right alone. Together, every build re-found the previous
build's rows, quoted inside their own `found` and `context` fields, and stored them again.

Streaming the array at its peak: **28,245,899 rows, of which 28,245,754 were the scan reading its
own output** and 145 were real findings. The self-inflicted rows collapsed into four shapes - two
found forms (`lucy`, `Lucy`) under two locations, both beneath the report's own route.

The cost was not only disk. The state file is read and rewritten between every node, so it bought
about 13 minutes of pure I/O per node boundary, and the build that produced it died mid-verify
carrying it - with no traceback, no crash report and no jetsam record, so the kill itself was
never proved.

The fix is the rule the module already stated for the correction stamp on `transcript.json`: **a
record of the pass HAVING RUN is machinery, not divergence**, so this report is lifted out of the
document before the scan reads it.

Two things worth keeping from the repair. The drop had to be BY ROUTE, never by filename: 116 of
the 145 real rows lived inside `pipeline_data.json` outside the report's own route, and a
filename-based drop would have destroyed every one. And the check that the repair was right was
not a count - it was running the FIXED scanner over the repaired project and getting exactly those
145 rows back, identical by identity.

`tests/test_layer_coherence.py::test_the_scan_does_not_find_its_own_filed_findings` demonstrates
the feedback rather than asserting a shape: two runs that change nothing must find the same rows
and leave the file the same size. On the pre-fix code, run two finds 3 rows where run one found 1.

Follow-up: the route-based lift-out above was a filter that hid the loop rather than cutting it -
the scan still read the state file and the build still stored rows where it reads. The structural
cut keeps the scan reading `pipeline_data.json` (those 116 real rows live in other step outputs
there) and moves the findings out instead: the build files the full report at the project root
(`layer_coherence_report.json`, outside every scanned root) and keeps only counts in run state.
`_without_own_report` / `OWN_REPORT_ROUTE` are deleted, and the loop tests now file through the
real build storage path. Repairing the already-bloated state file is separate work, not this one.

## the-gate-that-could-not-start-for-three-days

PR #1095 (2026-09-13 04:13) gave `reel_build.plan_cards` a required keyword-only `width`/`height`
- the DECLARED delivery frame, because a full-frame card IS the frame - and updated its three
callers in `reel_build` and `captain_edits`. It missed the fourth, in
`reel_conformance_verifier._derive_plan_from_master`.

That call site runs only when the derivation is given BOTH a `moment` and a `project_folder`,
which is every real build and no test. So the reels conformance gate raised

    TypeError: plan_cards() missing 2 required keyword-only arguments: 'width' and 'height'

on every build from then on, which `verify_built_reels` reports as "Reel conformance verifier
failed to run". The last reel promoted before it was Reel 09 at 2026-09-13 03:27 - **46 minutes
earlier** - and no reel was built again until 2026-09-16, so nothing found out for three days.

It cost a build. `verify_reels` discards the staging containers when the gate refuses, so five
correctly staged reels went. The approved timelines were never named and were untouched, which is
the refusal path behaving exactly as designed - the damage was the forty minutes, not the reels.

This is `whisperx-paid-twice`'s sibling in a different key, and it is the SECOND time this exact
shape has hit this verifier: PR #524 gave `reel_build.placements` a required `fps` and missed the
same function, which the test file already records. A branch nothing runs is a branch that breaks
silently, so the new test runs it - derive a plan WITH a project folder and assert it still
produces one.

## Section 15 - the captain's notes

### the-probe-that-missed-the-clip-markers

The captain, after firstmate missed three of their markers: *"why have
a script that does not check everything when you looked yourself and
found what you needed? either we need a better script that actually
covers everything to be able to offload that part of the process of
the LLM so it ca just call the script and get everything it needs or
to forego a tool that actually misleads us"*.

The pipeline's marker reader was not the problem.
`library/tools/marker_feedback.py` already reads timeline markers, clip
markers and media-pool markers and would have found theirs. Firstmate
wrote a weaker throwaway probe instead - `Timeline.GetMarkers()` only -
trusted it, and reported a reel as having no markers when it had two.
A probe reading the timeline level only misses every note the captain
left on a clip, silently, with the reel still looking read.

But the duplication was real, and it is why reaching for a probe was
easier than finding the tool. Six overlapping ways to read one reel's
state existed: `marker_feedback.read_notes` (all three marker levels),
`marker_resolution` (clip markers, to clear them),
`timeline_serializer` (clip markers, to dump state),
`timeline_ingest.snapshot_timeline` (clips with ground-truth ranges),
`reel_replace_guard.snapshot_timeline` (clips with spans - a second
function of the same name), `resolve_project_sync` (project binding, no
timeline content) - plus `capture_timeline.py` and
`measure_overlay_draw_positions.py`, pipeline code living as run-on-import
scripts. Nothing answered "tell me everything true about this reel
right now" in one call.

So the rule is one entry point, not a reminder to look harder:
`library/tools/reel_read.py` answers the whole truth about a reel in a
single call, the marker half IS `marker_feedback.read_notes` (called,
not reimplemented), and every other reader is a slice of it. Of the two
scripts, `capture_timeline.py` is a formatter over the one reader now;
`measure_overlay_draw_positions.py` is DELETED - the sibling lane
(PR 1005) removed the draw-gain constant it derived its answer from,
which is also why the new reader measures ink from pixels and grades
nothing (`tests/test_reel_read.py`).

## one-word-for-two-kinds-of-stale

A rebuild replaced Reel 09's picture Tilt and nothing objected, which
sent this lane looking for the declaration a hand-set transform should
have lived in. Two things came back, and the second is the rule.

**The value was never hand-set.** `Reel 09 - ... (final)` holds Tilt
+0.250 on every picture clip where the captain's other seven reels hold
-0.395 and every batch-1050 rebuild holds -0.790, and that uniqueness
across the LIVE timelines is what read as a hand. It is not: +0.25 is
what the engine itself wrote on **every** reel before PR #1005
(2026-09-11 22:29) replaced the picture Pan/Tilt unit model. The seven
`(baseline scratch)` build snapshots under `pipeline_output/review/`
carry Tilt 0.25 on every picture clip of Reels 01, 09, 13, 23, 26, 28
and 30 alike. Reel 09 (final) was built 2026-09-10 and never rebuilt
since, so it is the one timeline still carrying the pre-#1005 number.

The two values are the SAME aim. #1005 measured that a Pan/Tilt unit is
not a frame pixel (`library/tools/resolve_transform.py`):
`shift_px = value * (clip_dim / frame_dim) * base_scale`. At this
geometry - a 3840x2160 source on a 1080x1920 delivery, `fit` 0.28125 -
one Tilt unit draws 0.3164 px, so an aim spelled as 0.25 "pixels" is
0.790 units, and the axis sense inverts the sign. The picture moves
**0.329 delivery pixels** between the two. So: uniqueness across a
project is NOT a sound signal for "hand-set" - it found a stale build,
not a hand - and the sound question is whether a live value differs
from what that timeline's OWN build wrote.

**The 1.0s Akshita cover was not hand-placed either.** It is recorded,
exactly, in `pipeline_output/review/reel_variants.json`: a `cutaway`
variant spec, `hide_angle` "2" (Craig), `window_seconds`
[23.9406, 24.9416] - frames 574..598, which is what the timeline holds.
It reaches `build_reel_timeline` from `build_reel_variants` and from
nowhere else, so an ordinary `build-reels` rebuild cannot reproduce it
whatever it reads. That loss is already the founding incident of
`library/tools/reel_replace_guard.py`, which refuses the promotion on
the row count.

**The mechanism gap that IS real.** `captain_edits`'
`transform_override` is the declaration a hand-set transform lives in,
it already takes `Tilt`, and ten Pan overrides in this project prove it
survives a rebuild. What it could not do is tell two opposite facts
apart. Every recorded override is matched against every reel, so a
build of Reel 09 reported EIGHT stale overrides belonging to Reels 01,
13, 26 and 28 - all working perfectly - in the same sentence it would
use for a captain's value whose words had been reworded away and which
no future build of any reel will ever apply again. Nine identical
lines, one of them the decision being overwritten for good.

So a stale transform override now says which kind it is: `scope="reel"`
(spoken in the transcript, not in this reel's spans - routine) or
`scope="transcript"` (spoken nowhere - LOST), and the reel build says
the lost ones again, separately, naming the property and the number.
`captain_edits.lost_overrides`, `tests/test_transform_override.py`.

## caption-shipped-late

**Reel 26 of `geo-podcast` is on disk, delivered, with a caption card a
full second behind its own audio - and nothing in this pipeline could
have told anyone.** Found 2026-09-16 on the first reel the hearing pass
was pointed at.

`render_qa` measures the render for black frames, loudness, freezes and
letterbox. `manifest_validator` checks the plan against itself.
`reel_conformance_verifier` grades format, item count, picture holes and
caption timing. `render_watch` shows a model the PICTURE. **None of them
compares what the render is HEARD to say against what the plan says it
says**, and that comparison is the only thing that catches this class.

**The cause is one row of the timeline transcript**:

```json
{"speaker": "Akshita",
 "text": "make sure that you're coming up with something that answers
          specific questions.",
 "source_start": 4291.222, "source_end": 4292.142,
 "words": [], "read_from_words": false, "avg_logprob": null}
```

Twelve words in **920 milliseconds**, with no word timings and no
confidence. The speaker stuttered; WhisperX hallucinated a completion and
split one utterance into two segments. Because `avg_logprob` is null,
`transcript_confidence` has nothing to read and the row is invisible.

**Three consequences, all of them in the delivered file.**

1. **Six words ship with no caption at all.** The row has `words: []`, so
   no subtitle segment was generated for its span. Measured against the
   render: `make sure that you're coming up`, reel 25.65-26.61 s, zero
   caption coverage.
2. **The card that does appear is a second late, and the karaoke
   highlight with it.** Nine consecutive words at **-1.01 s** - four
   times the two transcribers' own measured disagreement. At reel
   27.60 s the audio says *"niche"* and the highlight is on *"with"*,
   spoken at 26.61 s.
3. **A phantom duplicate take.** `select_reels` recorded a repeat at
   similarity 1.0 between the hallucinated tail and the real sentence
   after it. That duplicate does not exist in the audio, and the take
   cutter was handed it.

**A second, independent instance in the same reel**, found by the same
pass and confirmed against the pixels: at reel 13.10 s the card reads
*"comes up to you and maybe"* with `maybe` highlighted while the audio is
still on *"and"*; at 13.70 s, while she is actually saying *"maybe"*,
there is no caption on screen at all. Two words uncaptioned, three words
drifting +462 ms.

**Why it was never built before, and what changed.** Nothing about the
comparison is hard - transcribe, align, diff, measure. It is the cost
that moved. Measured 2026-09-16 on this machine: an on-device transcriber
runs at **344x realtime** against this project's recorded **1.40x** for
WhisperX. One reel is **3.5 seconds** instead of 33-47 minutes for an
episode. The deterministic half found all three consequences with no
model call at all; the model's job is the judgement about which of them
matter.

**Two things the pass had to get right to be worth running.**

- **Normalise through `transcript_corrections` before diffing.** The
  planned side already has this project's filed spelling corrections
  applied - `lucy` to `Lucie`, 28 times on the run of record - and the
  heard side does not. Diffing them raw reports a false `Lucie` ->
  `Lucy` on EVERY reel this project builds. With the correction applied
  to the heard side, Reel 26's substitution count is **zero**.
- **A drift threshold that is a measurement, not a taste.** Over 6,983
  words of this project's audio the two transcribers disagree about a
  word's start by **94.1 ms mean absolute**, standard deviation
  **134.0 ms**, and the bias (-35 ms) is small against that spread, so no
  constant offset removes it. `DRIFT_NOISE_FLOOR_SECONDS` is 0.25 s - a
  little over one standard deviation above the mean disagreement - and a
  finding needs a RUN of consecutive words past it in the same
  direction, because an isolated late word is one transcriber hearing an
  onset differently.

**It REPORTS.** `reel_hearing.GATES` is False, no build reads its record
and `passed: false` fails nothing. A new gate that blocks builds is the
hard-to-reverse direction and this pipeline's gates have refused correct
output before (AGENTS.md 10.4); the order is to prove this on real
episodes and then ask the captain to promote it.

`library/tools/reel_hearing.py`, `library/tools/heard_speech.py`,
`library/skills/hear_the_reel/`, `manage_project.py hear-reel`,
`tests/test_reel_hearing.py`, `tests/fixtures/reel_hearing/`.

**Two fixes this evidence names and does NOT make.** The word-boundary
clamp (`step_1_04_temporal_index/step.py:276-317` clamps words over 2.0 s;
`timeline_transcript.py` does not) and a guard on a transcript row with
`text` and `words: []` reaching a caption planner. Both are separate
tasks. The hearing pass READS that cause rather than fixing it, and as of
2026-09-16 reads it as a FINDING (`transcript_row_fit`) owned by
`temporal_index` rather than as a note beside the findings - because a
note nobody has to act on is not a reading. `library/tools/transcript_fit.py`
measures the whole document, and it needs no render: 15 of the shipped
episode's 940 rows carry text with no word timing under it, 13 having lost
every word and 2 part of one.

### The four checks that were measured and NOT built

Refining the hearing pass on 2026-09-16, four further checks it could make
from signals it already holds were each measured against the one delivered
reel and the shipped transcript. **All four came back clean, and clean is
the reason none of them is in the code** - the bar PR 1177 set is a check
that fires on a real defect in a real reel, and three weak checks read as
coverage the way a gate that cannot fail does (AGENTS.md 10.4).

| Proposed check | Measured | Verdict |
|---|---|---|
| Speech heard where the plan places no speech clip | 0 of 142 heard words fall outside a planned span on Reel 26 | nothing to fire on |
| A caption card on screen with no speech under it | 0 of 13 cards; every card carries 4-20 heard words | nothing to fire on |
| A card placed away from the source span its own FILENAME declares | 13 of 13 within one frame (max 43 ms, mean 15 ms) | the `subtitle_segment_id` binding is honoured |
| Adjacent transcript rows repeating each other's words | 9 pairs on 940 rows; **7 are a person self-correcting out loud** ("It needs something unique. It needs something niche.") and the 2 that are hallucinations are already caught by `transcript_row_fit` | would cry wolf |

The third is the one worth naming as a follow-up rather than dropping:
`library/tools/subtitle_segment_id.py`'s own docstring says a
wrong-but-plausible caption pairing "is invisible", and the check that
would close it costs no render, no audio and no model - it is arithmetic
over a filename and a clip placement. It has nothing to fire on because
the only delivered reel on this project is correct in that respect, which
is a statement about the evidence available and not about the check.

Follow-up, built as a judgement call: `heard_caption_pairing` in
`library/tools/reel_hearing.py`, report-only under the same contract
(`GATES` stays False, owned by `plan_subtitles` through `qa_findings`).
Clean on Reel 26 by construction - 13 of 13 established, worst edge
42 ms - so it consciously departs from the fire-on-a-real-defect bar
above: the pairing it would catch is invisible to every other check,
and the check costs nothing to carry.

