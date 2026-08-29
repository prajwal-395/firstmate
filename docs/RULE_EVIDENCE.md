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

Using `0..clip_dur` for keyframes when `source_in` is not zero puts all motion outside the frames that play.
A segment from frames 25-97 of a 5657-frame clip must have its zoom ramp between 25 and 97, not between 0 and 5656.

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

`tests/test_house_look_reaches_broll.py` drives the real pass against a fake Resolve.

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

### a-declaration-that-went-stale

`classification.per_clip_artifacts` used to spell the path out: `pipeline_output/temporal_index/{clip_id}.json`.

When the layout moved the vision profiles out of `raw/analysis/`, the declarations in steps 1.03 and 1.07 were left behind pointing at the old location.
Nothing failed. `--rerun semantic_analysis:clip_007` deleted nothing, so the step's own "already on disk?" check found the profile still there and re-ran nothing - silently, and reporting success.

A declaration that can go stale is the exact failure the layout owner exists to remove, so the prefix is now the owner's to state (`{area:vision_analysis}/`) and only the filename is the step's.

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
| Per-Series Color Identity (to be finalized) | 275 | unsettled; the grade is `house_look` (§12) |
| **total** | **23,877** | **49.8% of 47,903 B** |

That is stricter than the degradation report's 41.9% by 7.9 points, and the difference is entirely the four sections above that a reader could call channel-level rather than out-of-scope (The Netflix Model, Three Content Lanes, Cross-Series Narrative Weaving, Part 3). Either number says the same thing.

**What would make them usable.** Three different answers, and none of them is "rewrite the captain's brief":

- **Six are unsettled by the document's own headings** - `(to be finalized)`, `Open Creative Decisions`. They become usable when the captain settles them, and not before; a model asked to act on a `⚠️ Partial` row is being asked to decide it.
- **Six name things the engine already takes from somewhere else** - typography from `render_fonts` and the brand template, colour from `house_look`, format from `delivery_format`, intro cards from `content.bookends`, timed text from the project's own declaration. They become usable when a brand template for this series carries the parameters, which is where §14 says a per-series value belongs. Prose in a channel document is not a route to any of them.
- **Nine are about the channel rather than about a video** - posting, growth, volume, naming, thumbnails, the portfolio. Nothing in this pipeline makes those decisions and nothing should; they are usable to the captain and to nobody in the DAG. Behind a path they cost 5 bytes of map line each, which is the right price.

The one thing that would help every step at once is the thing the entry above already says: **a brief specific to 001**, which is the captain's writing and not the engine's.

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

Withdrawal is a legitimate outcome and a silent unread key is not, which is why the reason is recorded where the design lives: `GRADE_PIPELINE_DELIVERY` in `step_5_01_color_grade/step.py`, `WITHDRAWN` in `transition_vocabulary.py`.

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
| colour grade | `color_grade_spec["mood"]` / `["grade_name"]` | step 5.01 emits **neither**; its spec carries `grade_pipeline`, `grade_pipeline_delivery`, `per_clip_adjustments`, `fusion_look`, `house_look`, `house_look_title`, `look_notes`, `withdrawn`, `output_color_space`, `consistency_notes` |
| engagement | the model's ranking | fires - and this was the one finding |
| duration | the spine | fires; 59.44s, inside the zone |

The colour check is REMOVED for the same three reasons the pacing check was (`PIPELINE_PLAN.md`
P4.2): it read a key no producer emits, the tests that covered it supplied `mood` themselves, and
it emitted no adjustment even when it fired. The values that ARE in the spec are numbers -
`house_look.py` gives every look a `saturation` and a `contrast` - and turning one into "soft" or
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

### the-pipeline-invented-taste-where-no-step-ran

Captain, 2026-08-26, on finding a fixed creative direction in step 2.01: "we need to remove all hardcoded fallbacks from the repo, they should not be there, we should not be hardcoding creative stuff like that".

**The line applied.** A CREATIVE fallback substitutes taste - a mood, a theme, a transition choice, an effect, a sound, an energy arc, a pace chosen for feel. A MECHANICAL default is a safe technical value - a frame rate, a timeout, a codec, a retry count, a path. Creative fallbacks go; mechanical ones stay. Where a creative value is genuinely absent the step fails or reports plainly, because a silently-defaulted mood ships and a stopped run does not.

**Two corollaries the audit needed.** First: a value that means "nothing is drawn" is not taste. `hard_cut` and `jump_cut` are in `transition_vocabulary.CUT_TYPES` and draw nothing, and `house_look`'s `NEUTRAL_CDL` is the identity transform - falling back to the absence of decoration is not choosing decoration. Second: a rule that acts on a value the creative direction really declared is not a fallback. `creative_cohesion` may judge a transition against a DECLARED "high"; what it may not do is invent the word "high" first.

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

### the-webfont-race

Montserrat is bundled rather than fetched because `@import url('https://fonts.googleapis.com/...')` with no `delayRender` made typography a race with the network.
A lost race rendered captions in Chromium's fallback sans at a different width, with nothing downstream able to tell.

### timed-text-drew-in-the-wrong-face

`TimedTextOverlay` loaded NO font at all until 2026-08-20 while naming a family in CSS.
Every card it rendered was already in the wrong face, and the frames were still valid pictures of the right size.

---

## Section 12 - the house look

### where-the-look-values-come-from

The values are authored from the captain's planning docs at `PLAN/series portfolio '26 planning/`, which are READ-ONLY and live outside this repo.

Nothing depends on a file inside a Resolve installation.

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
| 8 | house-look strengths: `glow_gain 0.12`, `glow_threshold 0.78`, `glow_size 3.5`, `grain_power 0.18`, `grain_size 1.5`, `vignette_blend 0.16`, `vignette_soft 0.35`, `contrast 0.1`, `saturation 1.1`, and the slope/offset/power triples | on all 17 per-clip comps | **PARKED - untouched.** `library/tools/house_look.py` is not modified. What DOES change is that 001 no longer inherits `pmk_default` at all, because that came from the unchosen template (see "the consequence to take to the captain") |
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
creative_direction: "\nBrand Constraints:\n- Style: {\"color_palette\": [...], \"house_look\":
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

`default_brand.yaml` declares `house_look: pmk_default`, and 001 inherited it. With absence
declaring nothing, **001's next grade would carry exposure normalisation only** - step 5.01 says so
in its own `look_notes` - instead of `pmk_default`'s CDL and Fusion values on all 17 per-clip comps.

That is the correct behaviour of the rule and it is also a change to the picture layer the captain
has parked, so it is stated rather than worked around. `pmk_default`'s `derived_from` cites the
captain's own `overall_branding_creative_direction.md`, and 001 is a pmk video, so the look is very
likely one the captain WOULD choose - which is exactly the decision this change refuses to make on
their behalf. **The fix is one line in 001's `project.yaml`:** `pipeline: {brand_template: <name>}`.
Nothing here edits it.

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
nothing the model can emit - the schema asks for an `sfx_id` out of `sfx_catalog_toon` - but the
table reads as a menu and should be corrected by whoever holds that file.

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
