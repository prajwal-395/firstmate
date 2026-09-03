# Video Editing Pipeline

## How to read this file

You never need one in order to obey a rule.

Before your first run, read §9 (environment), §3 (how a run works) and §5 (DaVinci Resolve).

Section numbers are cited from code comments and other docs, so they are stable.
Change what is in a section, not its number.

## 1. Identity and purpose

The engine stores no project data - assets and pipeline outputs live in isolated directories outside the repository.
Agents and human editors use it to automate the tedious parts of video assembly while retaining creative control.

## 2. Repo layout

- `library/`: core pipeline implementation and shared Python modules.
- `library/processes/`: complete pipelines such as `edit_video`, with its `dag.json` and `manifest.json`.
- `library/steps/`: individual pipeline steps, named by phase (e.g. `step_1_01_scan_project`).
- `library/tools/`: shared utilities for Resolve scripting, vision analysis and file management.
- `library/schemas/`: Pydantic schemas for pipeline state and project configuration.
- `library/dashboard/`: FastAPI server for the human-in-the-loop review dashboard.
- `library/tools/panel/`: the Resolve panel's logic, with no Qt and no Resolve in it (§15).
- `library/templates/`: brand templates defining styles, effects and content rules.
- `library/profiles/`: declared run configurations - which steps a run fires and where it stops (§3).
- `library/presets/`: Fusion macros and DaVinci's own built-in effect settings. Whatever reaches a timeline is found by direct path; there is no preset index.
- `remotion-subtitles/`: Node.js React app that renders subtitle overlays.
- `scripts/`: bash helpers for environment setup and maintenance.
- `resolve_scripts/`: the entry points DaVinci Resolve's Workspace > Scripts menu calls - the capture button (§15) and the pipeline panel. Installed by `scripts/install_resolve_scripts.sh`.
- `resolve_workflow_integration/`: the Electron plugin Resolve's Workspace > Workflow Integrations menu loads (§15). Installed by `scripts/install_workflow_integration.sh`.
- `tests/`: unit and integration tests for the engine.
- `manage_project.py`: top-level CLI for creating, listing and running projects.
- `requirements.txt`: Python dependencies.

**One thing in `library/tools/` is a prototype: it is in the DASHBOARD and stays out of the PIPELINE.**
`footage_query.py` / `footage_segments.py` / `footage_query_bridge.py` are a cross-clip footage
search - "where in all my footage does X happen".

[`docs/FOOTAGE_INDEX_PROTOTYPE.md`](docs/FOOTAGE_INDEX_PROTOTYPE.md) has what it measured, the
measured score floor that lets it answer "not in this footage", and the 66x reduction of
`temporal_index` that is worth doing without it.

## 3. Pipeline execution

The pipeline is a Directed Acyclic Graph (DAG) in `library/processes/edit_video/dag.json`, ordered by topological sort.
 

- Call the analysis stage **preflight**, never "phase 1", even though its step ids read `step_1_0X_*`.  **ONE exists and is not wired into the DAG:** `object_segmentation` (1.06) - nothing consumes masks ([`docs/SUBJECT_MASKING_MEASURED.md`](docs/SUBJECT_MASKING_MEASURED.md)). It carries a documented `unwired_reason` in `project_layout.STEPS`, `StepDir.__post_init__` rejects `wired=False` without one, and `tests/test_step_dag_coverage.py` fails if a step directory exists with no DAG node and no unwired declaration. **Unwiring says nothing consumes it, not that the capability is gone.** `prosody_analysis` (1.05) was re-wired on 2026-09-01: its deterministic measurements (pitch, pace, voice quality, intensity) are unbiased signal the model lacked. Its output routes to 2.01 and 2.02 via `view:prosody`. See [`docs/PROSODY_MEASURED.md`](docs/PROSODY_MEASURED.md) for the original measurement that led to unwiring, now overruled.
- **UNWIRED and DESELECTED are different things, and only one is a property of the pipeline.** Unwired means no DAG node exists (1.06).  The two lists are `project_layout.STEPS` and `run_scope.DESELECTED_BY_DEFAULT`; a step is in one or the other, never both.
- `objects[].readable_text` in semantic analysis output is the VLM's field - step 1.03 prompts for it directly. The local model (`gemma-4-12b-it-4bit`) reads on-screen text **sparsely, not never**; a recorded claim that it "provably cannot" read text was wrong.  
- `objects[].readable_text` is the VLM's field (step 1.03).  The two are independent.
- Step 1.07 `ocr_extraction` is WIRED and DESELECTED BY DEFAULT: `--with ocr_extraction` turns it on.

| Flag | Effect |
|---|---|
| `--from <step_id>` | resume from a step |
| `--step <step_id>` | run one isolated step |
| `--auto` | auto-complete hybrid steps from bridge output instead of pausing for LLM input |
| `--review` | enable review gates that pause for human inspection on the dashboard |
| `--resume` | continue after a review gate is approved or revised |
| `--dry-run` | print the execution plan without running steps |
| `--rerun <target>` | redo finished work; repeatable, and the ONLY supported way to re-run a completed step |
| `--target <name>` | run only what a named destination needs |
| `--only <step_id>` | run this step and whatever it cannot run without; repeatable |
| `--skip <step_id>` | leave a step out; repeatable |
| `--with <step_id>` | turn on a step that is off by default; repeatable |
| `--profile <name>` | run under a DECLARED run configuration; `none` declines the one the project adopts |
| `--break <step_id>` | stop after this step for review; repeatable, `*` means every step |
| `--no-break <step_id>` | do not stop after this step; repeatable, `*` disarms every breakpoint |

### Scoping a run

One enumeration, `library/tools/run_scope.py`, and both CLIs register its flags from it.
Detail: `library/tools/run_scope.py`.

### Configuring a run

**A run shape is DECLARED as data, and it has no power `run_scope` does not already have.**
Detail: `library/tools/run_profile.py`.

**A breakpoint is armed PER STEP, and `--review` is the every-step case.**
Detail: `library/tools/breakpoints.py`.

### State the pipeline did not produce

**A prerequisite may be satisfied from outside the pipeline, and it is CHECKED, never asserted.**
Detail: `library/tools/external_inputs.py`.

### A declaration must be true

**No step may declare an input required that nothing refuses on, or optional that its own code refuses without.**
Detail: `library/tools/input_contract.py`.

### Two ledgers, two lifetimes

One enumeration: `library/tools/step_ledger.py`.
Detail: `library/tools/step_ledger.py`.

### Run status

The run summary reports `SUCCESS` only when the whole DAG is complete and `failed_steps` is empty in the project ledger - not just the steps this invocation touched.
Detail: `library/processes/edit_video/run_pipeline.py`.

### A run that was RESTARTED says so, in its own outputs

One enumeration, `library/tools/run_restart.py`.
Detail: `library/tools/run_restart.py`.

### A step says what it could not determine

One enumeration, `library/tools/undetermined.py`.
Detail: `library/tools/undetermined.py`.

### A step says where its measurements contradict the direction, and complies anyway

One enumeration, `library/tools/direction_contradiction.py`.
Detail: `library/tools/direction_contradiction.py`.

### A step that makes a craft judgement is told what craft it is

One enumeration, `library/tools/craft_role.py`.
Detail: `library/tools/craft_role.py`. [why - the measurement, and the two defects it explains](docs/RULE_EVIDENCE.md#twelve-handoffs-no-role)

### A step with no creative brief ASKS, rather than planning in silence

One enumeration, `library/tools/brief_attachment.py` for the choice and `library/tools/briefing_interview.py` for what happens when it goes the other way.
Detail: `library/tools/brief_attachment.py`.

Captain's ruling, 2026-09-02: *"this should be like an optional attachment we can add as context if we want, not something that automatically goes in"*, and *"if this was like a TUI interface if a user declines to attach a creative brief, then it should prompt the LLM to ask some briefing questions for the user"*.
Detail: `library/tools/brief_attachment.py`.

### A contract rejection reaches the model that caused it

One enumeration, `library/tools/post_bridge_retry.py`.
Detail: `library/tools/post_bridge_retry.py`.

## 4. Dashboard

**The picker lists the project the server is SERVING, first**, even when that project is outside the root.

- Review gates pause execution for inspection.  A rejected gate halts the pipeline entirely; a revised gate applies the reviewer's modifications directly to the step output in `pipeline_data.json`.
- The dashboard also captures annotations and feedback as structured data for agent communication.
- **Extend this dashboard. Never author a fresh per-run review page.**

### Footage search

**Footage Search is a span search; Footage Library is a clip browser. Keep them apart.**
`library/dashboard/footage_search.py` is the dashboard's half of the footage-index prototype
(§2) - the ONE place authorised to call it.

- The index is built ONLY when the reviewer presses the button, into
  `<project>/pipeline_output/scratch/footage_index/`, and the view states that path first.
  Never build it at server start or on first query: both write into the captain's project
  without them asking.
- The embedding model is loaded once per server in a background thread; the banner reports
  `cold`/`loading`/`ready`/`unavailable` and names the backend, because a degraded backend
  must say so rather than quietly return worse results.
- **A ranking cannot say "not here", so there is a floor.** Retention is judged on the RAW
  dense cosine (`DENSE_SCORE_FLOOR`), never on the blended score. Below it and above
  `DENSE_WEAK_FLOOR` is a WEAK band.
- Staleness compares an `ingest_fingerprint` recorded at build time. `pipeline_data.json` is
  fingerprinted by its `catalog` subtree only - the state writer rewrites the whole file after
  every step, so a downstream step landing is not a change to the footage.
- `tests/test_dashboard_footage_search.py`.
### The review return channel

**A note is anchored to a specific element, and the anchor is computed in the browser.**
`computeAnchor` in `library/dashboard/static/components/review-channel.js` measures a CSS path plus the element's tag and visible text; `resolveAnchor` walks it back to a live element after a view re-renders, path first and tag+text second.

A note whose `anchor.selector` is empty is REJECTED.

**One send carries the whole queue and wakes an agent, which replies onto the same surface.**
`library/dashboard/review_channel.py` is the store and the agent side; `/api/review/*` in `server.py` is the browser's half.
The agent parks on `wait_for_batch` (or `GET /api/review/poll`) and is released the moment the reviewer sends.

Notes live per project in `pipeline_output/review/channel.json`, each recording the view it was written on.

### Run control

Start, Handbrake, Resume and Step launch `run_pipeline.py` as a child process.

- Start uses `--full-auto agy` and does NOT pass `--review`; gates are an opt-in tick box.
- **The handbrake is a file, not a signal.** `library/tools/run_control.py` owns the whole vocabulary - `pipeline.hold`, `pipeline.pid`, `pipeline_run.json` at the project root - and both processes speak only through it.
- `pipeline_run.json` is the runner's own account of itself (mode, current step, how it ended). 

## 5. DaVinci Resolve integration - CRITICAL RULES

### Connection

### Process isolation

- Never create a timeline and use `ImportFusionComp` in the same Python process.   

### Judge every Resolve call by what it returns

- **`hasattr` is always True on Resolve's scripting proxies, including invented names.** Guard on return values, never on `hasattr`.
- **A tick that prints what it ASKED FOR is a lie, and so is a failure that prints nothing.** Print what `GetSetting` RETURNS, and send a failure's reason to stderr.
- **The timeline SHAPE goes on the PROJECT, and is confirmed by reading it back.** `SetSetting` returning True is a claim and `GetSetting` is the evidence.  
- Read the truth off `TimelineItem.GetProperty()` with no argument, which returns the whole dict, before trusting any property name.
- **`Pan` and `Tilt` are the transform properties. There is no `PanX` and no `PanY`.** `ZoomX`/`ZoomY` are real.
- **The scripting API cannot set an audio level, and that is a COMPLETE enumeration.** An audio `TimelineItem` has no property dictionary at all, so every spelling of `SetProperty` returns False; the whole documented audio surface is `GetFairlightPresets`, `ApplyFairlightPresetToCurrentTimeline` and `InsertAudioToCurrentTrackAtPlayhead`, and Fusion's `ActionManager` registers no audio action. 
- `CreateMagicMask` is withdrawn: it returns False for every mode.
- Super Scale is a **MediaPoolItem** property taking an **int**, with companion keys `SuperScale Sharpness`/`SuperScale Noise Reduction` (no space after Super).

### Transitions go through Fusion. Both other routes are closed.

**Do not wire FCPXML or DRP project-file surgery back in.**

- Every transition type the pipeline may plan lives in ONE enumeration, `library/tools/transition_vocabulary.py`, with a recorded reason for each withdrawn type.
- **A cut the plan did not decorate is a hard cut.** `transition_selector` never invents a DRAWN transition; `WITHDRAWN_SCENE_CHANGE_DEFAULTS` records the three it used to.  A type advertised anywhere else fails CI.
- Adding a transition means adding a builder to `library/tools/fusion/effects.py` first.
- No per-clip Fusion comp can mix two clips, so there is no cross dissolve or wipe on this route.

**A DRAWN transition can only sit where a V1 clip ends, and the step that plans them is TOLD which cuts those are.**
One enumeration, `library/tools/transition_carriers.py`.

- `block_reaches_v1` is the single statement of V1 membership - a bookend card, a `speech` or `hook` block - and `compile_manifest` builds its V1 track from that same predicate, so the two cannot drift.
- Every B-roll placement goes on V2, so a cut whose OUTGOING block is a `transition_slot` has no V1 clip ending on it and `compile_manifest` refuses the transition by name. A cut whose outgoing clip is the LAST thing on V1 is refused too: the effect is a tail AND a head.
- `cut_carriers` reads that off the spine before the run, and step 4.02's bridge puts it in `cuts_toon` as `can_carry_drawn_transition` / `carry_basis`. `CUTS_LEGEND` defines both columns as DATA, because `handoff.md` is frozen - the same route `music_measurement.MEASUREMENT_LEGEND` takes for step 2.04.
- **The table is never filtered or re-ranked.** Every cut is still offered; the model is told the truth and still chooses (section 10.5).
- `tests/test_transition_carriers.py`.
### Stabilization is the memory ceiling, and it runs last

- Treat it as the memory ceiling of the whole pipeline and do not run other heavy jobs beside it.
- It changes picture steadiness and nothing else - never structure, timing, framing, grade, captions or sound.
- For a timeline meant to be scrubbed rather than shipped, pop `neural_engine_directives` off the **in-memory** manifest before `build_timeline` and leave the file on disk carrying it.
    

### Fusion .comp files - NEVER

- Never use `ApplyMode` in a Merge node: it crashes Resolve with a SIGSEGV.
- Never use `Path {}` when a Merge node exists in the same comp: it causes black output.
- Never use `BlendClone`; it is silently ignored.  Use `Tools = {`.
- Never omit `GlobalOut` on Background nodes: it stops rendering mid-clip.
- Never set DirectionalBlur `Length` greater than 5: it creates artifacts and edge tiling.
- Never set transition zoom greater than 1.04: it is too aggressive and breaks immersion.

### Fusion .comp files - ALWAYS

- Always set `Inverted = Input { Value = 1, }` on EllipseMask for vignettes.
- Always include `MaskWidth`, `MaskHeight` and `PixelAspect` on EllipseMask.
- Always wire `Transform1.Input <- MediaIn1.Output` explicitly.
- Always use `Blend` instead of `BlendClone` for Merge opacity.
- Always include `GlobalOut` on Background nodes matching the clip duration.
- Use static `Center = Input { Value = { x, y }, },` for animated pan/center.
- **Size every Background node to the SOURCE clip's own resolution, never to the delivery format.** Read it off the MediaPoolItem's `Resolution` and do NOT swap it for rotation - Fusion gets the stored frame.

### Frame mapping

**Comp frame 0 is the clip's FIRST PLAYED frame, and `clip_dur` is the SOURCE's frame count. They are different numbers and both are needed.**
One enumeration, `library/tools/fusion/played_window.py`.

- Use the SOURCE clip frame count for `clip_dur`, read as `int(mpi.GetClipProperty('Frames'))`, not `clip.GetDuration()`.
- **Every animated keyframe is placed in the COMP's frames, through `played_range`** - never at a source frame number. `source_in_frame`/`source_out_frame` arrive in the SOURCE's numbering and are translated; a segment cut from source frames 654-725 animates over comp frames 0-71.
- **A spline extrapolates FLAT, so a keyframe outside what plays is not a ramp that does nothing - it is the effect held at full strength for the whole clip.** On 001: 331 frames (18.6%) carried full-strength defocus; `zoom_blur` (#202) is the same defect.
- **A ramp longer than the frames its clip plays is REFUSED by name** (`TransitionLongerThanTheClip`), never drawn: it never reaches neutral, so it covers the whole clip.
- **Count DRAWN frames, not planned ones.** `library/tools/fusion/transition_frames.py` reads the comp and evaluates its splines. 

### Default transition values

- Brightness Flash: `Brightness = 0.67`, `Saturation = 1.83`, animate `Blend` 0-1 with Sine easing.
- Crash Zoom: Transform `Scale = 0.4`, `Offset = 0.6`, range 0.6-1.0, Quad easing, mirrored.
- Glow: `SoftGlow.Gain = 5.0`, `SoftGlow.XGlowSize = 100`, linear easing.
- Default easing uses `LUTLookup` driven by the system `Transition` variable for Edit page transitions.
- For per-clip Fusion comps, replicate easing with `BezierSpline.sampled()` pre-baked keyframes.

### Tracks

**Per-clip Fusion comps reach V1 AND V2.**
One enumeration, `library/tools/execution/fusion_tracks.py`.

- `compile_manifest` merges a declared look onto both. 
- Transitions stay on V1: `after_clip` indexes the V1 clip LIST, so replaying it elsewhere draws a transition at an unrelated cut.
- Drop detection in `build_verification` asks whether a label was PLACED, not whether it is on V1.
- `tests/test_house_look_reaches_broll.py` drives the real pass against a fake Resolve.

**A V2 clip that is FOOTAGE carries its own picture; a TRANSPARENT one carries none**, and neither comp reads V1. Which is which, and what each may be asked to draw, is in `fusion_tracks.py` beside the enumeration above.

- A cutaway takes zoom, blur and grade as a V1 clip does; `vfx_carriers.py` tells the planner which track a block is on.
- Adjustment Clips cannot go on V2 - `InsertGeneratorIntoTimeline` always targets V1.

**A3 is a logical SFX bucket, and TWO SOUNDS AT ONE SPAN IS LAYERING.**
`LOGICAL_BUCKET_TRACKS` in step 5.04; every refusal of a shared position sits inside it.

### Media pool and audio

- **Prefix overlay filenames with their context**, such as `sub_craig_seg_000.mov`. 
- **Place V1 clips while only track A1 exists**, or the timeline floods with empty tracks: iPhone MOVs contain multiple audio streams. Add A2 and later tracks afterward, and place music or SFX with `mediaType: 2`.
- **Resolve audio pool items report 24fps regardless of the timeline.** `AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase, so compute audio in/out with the pool item's own FPS.
- **Renders are silent unless you say otherwise.** `SetRenderSettings` must set `ExportAudio`/`AudioCodec` explicitly; `resolve_render.py` also probes the output for an audio stream before reporting success.

### The mix goes through OTIO, and it goes in at placement time

Every planned dB - the bed's per-block curve and each clip's `volume_db` - reaches Fairlight
by ONE route: `library/tools/otio_mix.py` writes it into an OpenTimelineIO export and
`library/tools/execution/deliver_audio_mix.py` imports the result back.
Resolve's OTIO carries clip volume in plain JSON, **in dB**, with keyframes.
[why - the measured renders, and the routes that were rejected](docs/RULE_EVIDENCE.md#the-mix-goes-through-otio)

- **The import REBUILDS the timeline.** Fusion comps and CDL grades do NOT survive it. The
  placement, transform (`_apply_conform`), timeline markers and native transitions do. 
  `tests/test_audio_mix_delivery.py` drives a whole build and asserts the comps are still there.
- **The `volume` parameter is ABSENT from an untouched export**: it must be INSERTED, not patched.
- **A keyframe's frame number is measured from the CLIP'S START ON THE TIMELINE**.
- **`ImportTimelineFromFile` answers None with no diagnostic** when a referenced media file is
- A cyan `UNAPPLIED target` marker is the FALLBACK, written only when the route declines and
  saying so. 

### Visual verification

### Reading a killed build off disk

**Copy the project database before opening it; never open it in place.**

### Markers and timeline items

Use `timeline.AddMarker()` and `timeline.GetItemListInTrack()`, following the patterns in `timeline_item_markers` and related tools.

## 6. The spine contract

- Every block carries `clip_id`, `source_start`, `source_end`, `word_timestamps` and `alignment_method`. `None`/empty only for non-speech blocks.
- **Read those keys directly** (`block["clip_id"]`). 
- Word timings use `source_start`/`source_end`, not `start`/`end`.
- `speech_sequence` (2.02) treats the LLM's `source_start`/`source_end` as a LOOKUP HINT only.  This is what stops invented round-number ranges reaching the timeline.
- **Two body passages cut from one clip may not claim overlapping source ranges.** 2.02 re-anchors such a passage past the previous one or fails it; `manifest_validator` asserts the same on consecutive V1 clips. 
- **A passage is anchored by SEARCH, never by the occurrence nearest the hint.** 2.02's `_align_words_to_text` tries every occurrence of the passage's first word, both from the occurrence and from `ANCHOR_BACKUP_SECONDS` before it, and ranks: most passage words aligned, then SHORTEST SPAN, then smallest leading gap, then hint proximity. 
- **There is no gap threshold and no voiced-fraction band.** A silence survives exactly when no equally complete anchor removes it. Both outcomes are SAID: `alignment_report` records the leading gap, voiced fraction and anchors considered, and the `reanchored`/`held` cases print to stderr. 

## 7. Data flow

Each step reads required upstream outputs based on the DAG's `data_mapping` edges and writes its own output back under `step_outputs.<step_id>`.

- `catalog`: video metadata, durations, file paths.
- `semantic_analysis`: the v3 vision observations - scene, camera, actions, objects, assessment - plus the view derived from them. It measures no mood or energy (see §10.1).
- `speech_sequence`: speech segments ordered into a coherent narrative.
- `aroll_assignments`: narrative blocks mapped to source clips and timeline ranges.
- `broll_selections`: secondary footage (`b_roll_assignments`) and standalone cutaways (`b_roll_interjections`), assigned to cover A-roll segments or to insert visual breaks.
- `compile_manifest`: all decisions consolidated into an `assembly_manifest.json` that drives the final Resolve render.

## 8. Project management

- Create with `python3 manage_project.py new <slug> --name "Project Name"`.
- Project configuration is `project.yaml` inside each project directory; the `ProjectConfig` schema defines source settings, pipeline options and Resolve bindings.
- The project registry scans the root directory to list and manage available projects.
- A project outside `PIPELINE_PROJECTS_ROOT` is addressed by passing its absolute path in place of the slug to `run`, `status`, `info` and `dashboard`. 

### Where a project's files go

**One module owns the project-side layout: `library/tools/project_layout.py`.**
`paths.py` owns the REPO and the MACHINE; this owns one PROJECT, which is a folder passed in rather than a constant.

**`pipeline_output/steps/` IS the pipeline.** One directory per step, in run order; the captain audits by walking the folder.

- `STEPS` is the ordered table of every step, in DAG order, with the directory it owns. `AreaSpec.step` names the owning step for a step-owned area.
- Directory names use the STEP number, the same spelling `library/steps/` and every "step 1.04" citation uses; `README-LAYOUT.md` renders true run order, which diverges from that sort in two places. 
- Each step directory holds `output.json` and `summary.md` (what `step_exporter` writes) plus whatever files the step produced.
- **A step writes only inside its own directory.** Pass `step=` to `write_dir`/`write_path` and another step's area raises; `assert_step_owns` is the same guard for a path from outside the layout.
- **Not everything is a step's product.** `logs/`, `gates/`, `review/`, `llm_*/`, `thumbnails/`, `backups/`, `migrations/`, `provenance/`, `scratch/`, `unsorted/` and `exports/` stay at project level.
- `exports/` is the one area TWO steps legitimately write: 6.01 the render and 6.02 the QA report. `produced_by` names both, and provenance leaves `step_id` None rather than picking one.

- Every place inside a project folder is a row in `AREAS`, keyed by `Area`. A place that is not a row does not exist, and asking for one raises.
- **A step never composes a project path.** It names an `Area` and gets a path via `write_dir`/`write_path`/`read_dir`/`read_path`. `write_dir`/`write_path` to write, `read_dir`/`read_path` to read, `resolve_project_relative` for a path recorded in state.
- **Inputs are structurally protected.** `raw/`, `music/`, `assets/`, `brand_assets/`, `compositions/`, `external/` and `profiles/` are `Kind.INPUT`: `write_dir`/`write_path` raise for them, `ensure()` does not create them, and `assert_writable` refuses any path underneath.  Outside the project, at the bare project root, or inside an input area all raise.
- **A project explains itself.** `ensure()` renders `README-LAYOUT.md` from the same table the code reads - the steps in run order, what each reads and what each writes - and runs on `manage_project.py new` and at step 1.01 of every run.
- **`classification.per_clip_artifacts` names an AREA, not a directory**: `{area:temporal_index}/{clip_id}.json`.
- The scaffold is not a second list.
- Anything the pipeline FETCHES rather than computes - a downloaded music track - is output, and goes to `Area.ACQUIRED_MEDIA` under the step that fetched it, not into `music/`.

**Backups of `pipeline_data.json` are automatic and bounded.**
`pipeline_output/backups/pipeline_data/`, one per RUN, newest `MAX_PIPELINE_DATA_BACKUPS` kept.
- The pruner only ever considers files matching its own naming pattern, so a hand-made backup dropped in beside them is never deleted. Pre-policy backups live in `backups/pipeline_data/legacy/`.
- One per run, not one per save: `save_pipeline_state` runs after every step, and the thing worth keeping is the state as it stood BEFORE a run.

### Reading a run back

**The layout answers "which step wrote this" by where the file is. Provenance adds WHICH RUN and FROM WHAT.**
`library/tools/provenance.py` owns it.

 **Never attribute a file to the nearest plausible step.**
- The runner observes each step **after** `_export_step_for_review`, or a step's own `<step_id>.json` export is attributed to nobody.

- Records are append-only. `derived_from` is READ out of the artifact, never inferred from a filename.
- `derived_from` is READ out of the artifact - `SOURCE_KEYS` names the keys - never inferred from a filename. A `clip_id` is not a path.

**Two generated documents, regenerated on every run and by `manage_project.py trace <slug>`.**
`library/tools/run_traceback.py`. `RUN-TRACEBACK.md` is the steps in order - when, how long, what it consumed and from which step, what it produced. `ARTIFACTS.md` is the other direction: every file, with the step that wrote it and how that was established.
- Both are generated from `dag.json`, the two ledgers, `step_errors` and the provenance ledger.  `unwired_step_ids` matches by `step_ref`, because the DAG calls `step_1_01_scan_project` simply `scan`.
- `tests/test_run_traceback.py`.

**A project that predates the layout is brought onto it with `manage_project.py organize <slug>`.**
`library/tools/project_migration.py`.
- **It never deletes.** Every action is a move or a copy, and a file whose purpose cannot be established goes to `pipeline_output/unsorted/<bucket>/` with a stated reason, never a guess. Measure what you can - `media_facts` records a file's duration, format and encoder - so an admitted unknown is an examined one.

- **It never deletes.** Unidentifiable files go to `pipeline_output/unsorted/` with a stated reason.
- **It never modifies an input directory.** Pipeline output found inside one is COPIED out.
- Every run writes a manifest to `pipeline_output/migrations/`.
- `tests/test_project_migration.py`.

### Replaying a step without running the pipeline

**A step's exact prompt and context can be rebuilt off frozen state, at a named revision, with no pipeline run, no Resolve and no project write.**
`library/tools/replay_bench/`, driven by `python3 -m library.tools.replay_bench`. Read [`docs/STEP_REPLAY_BENCH.md`](docs/STEP_REPLAY_BENCH.md) before changing what a step is routed: it answers "did that change what the model sees" in seconds.

- The reconstruction is the runner's OWN assembly - `gather_step_inputs`, the step's `bridge.py`, `project_fields`, `json_to_toon`, the handoff and `get_brand_constraints` - never a model of it. `reconstruct.py` imports nothing from `library` at module scope: it runs as a subprocess with the TARGET tree first on `sys.path`.
- **A snapshot is captured outside the repository; only its MANIFEST is committed** to `tests/fixtures/replay_snapshots/`.
- **`verify` is a gate, not a report.** It reconstructs every archived context and exits non-zero on any unaccounted difference: if it cannot reproduce the past it cannot be trusted to compare futures. A step that matches only after a named cause is subtracted reads `EXACT (explained)`, never as a clean pass.
- **Never use the pipeline's own token figures.** `present_llm_step` logs `len(s.split()) * 1.3`, which is 0.38x-0.54x the `o200k_base` count. The bench measures from the reconstructed string and names the tokenizer; with `tiktoken` absent the count is absent rather than estimated.
- The bench measures the pipeline and stays out of it. 

### No test reaches a real project

**A test builds its project under `tmp_path`, or it skips. It never falls back to a real one.**

- `library.tools.paths.PROJECTS_ROOT` is the ONE constant naming where real projects live. **A test may not read that constant.**
- `tests/conftest.py` points `PIPELINE_PROJECTS_ROOT` at an empty temporary directory for the whole session.
- `tests/test_tests_never_reach_real_projects.py` asserts the guarantee: the root a test sees is the sandbox, no test source carries a real path, and collecting the suite against a populated DECOY root binds nothing.

## 9. Environment and dependencies

- **`run` needs the dedicated `.venv`; nothing else in the CLI does.**
  `manage_project.py` checks for `mlx_vlm`/`whisperx`/`easyocr`/`torch` only for the commands in `ML_DEPENDENT_COMMANDS`, which is `run` alone: the ML packages are imported by pipeline STEPS, and `run` launches them with `sys.executable`, so the interpreter running the CLI is the one that must carry them.
  `tests/test_cli_ml_preflight.py`.
  ML packages are checked for `run` only (`ML_DEPENDENT_COMMANDS`). All other commands (`dashboard`, `list`, `status`, etc.) work without them. Never move that check back to import time. [why](docs/RULE_EVIDENCE.md#the-dashboard-could-not-be-opened)
- Set `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` to your DaVinci Resolve installation.
- Set `HF_TOKEN` for HuggingFace models like Audio Flamingo Next.
- Set `PIPELINE_SFX_LIBRARY`, `PIPELINE_MUSIC_LIBRARY` and `PIPELINE_PROJECTS_ROOT` to absolute paths.
- Python dependencies are in `requirements.txt`. `librosa` is required by `music_analysis`; without it the step reports `available: false` and the run fails rather than continuing silently.
- External tools: `ffmpeg` and `ffprobe`. Node.js for Remotion subtitle rendering.
- GPU acceleration is required for Gemma 4, SAM 2, WhisperX and EasyOCR.
- **Every `subprocess.run` capturing text must pass `encoding="utf-8"`.** `text=True` decodes with the locale codec, and this pipeline writes UTF-8 status glyphs. [why](docs/RULE_EVIDENCE.md#text-true-decodes-with-the-locale-codec)
- **Reach Resolve through `library/tools/resolve_locale.scriptapp_preserving_locale`, never `dvr.scriptapp` directly.** The call resets `LC_CTYPE` to `C` down in Blackmagic's library, so `locale.getpreferredencoding()` becomes US-ASCII and every later `open()`, `Path.read_text()` or `text=True` subprocess without an explicit encoding raises `UnicodeDecodeError` on this repository's own UTF-8 sources. Only `LC_CTYPE` is restored - `LC_NUMERIC` is untouched, because handing fusionscript a decimal comma would corrupt every number crossing the boundary. **Two call sites use the wrapper (`marker_feedback`, step 6.01); eight others still call `scriptapp` directly and are unmigrated** - `resolve_relinker`, `timeline_serializer`, `resolve_health`, `resolve_project_sync`, `qa/timeline_sync_qa`, `execution/resolve_render`, `execution/apply_fusion_comps` and `probe_resolve_capabilities`.

### What CI actually checks

**The build has one gate that CAN fail and one report that cannot, and the two run under DIFFERENT ruff configs.**
`ruff-ci-gate.toml` at the repository root is the enforcing half - what is enforced, what is deferred and the count of each are written down in it.
`.github/workflows/ci.yml` runs it with NO `|| true`; the unfiltered run beside it keeps `|| true` and keeps annotating everything.
[why - run 33667912850 emitted 2,896 error annotations and concluded SUCCESS](docs/RULE_EVIDENCE.md#the-build-that-declined-to-look)

- **A deferral is per FILE with its count, and that is weaker than it reads**: a listed file is exempt from that rule entirely, so a NEW violation in one still passes. Fixing a file means DELETING its line - a line no longer needed is a lie about what is still owed.
- **The runner installs ffmpeg**, because 27 library files shell out to it and every audio and video measurement path skipped itself without it. The suite skipped HONESTLY, which is what made it invisible.
- **pytest runs with `-rs`.** `131 skipped` names nothing; a build that declines to measure something must say what.
- `tests/test_ci_can_fail.py` reads the workflow and fails the moment either hole reopens.

### This file is an INDEX, and two gates keep it one

**A rule lives with the code it governs; AGENTS.md keeps the headline and points at it.**
Measured 2026-09-03: the file regrew 34,848 characters in the two working days after the
2026-09-01 condensation - twelve commits, mean +2,913 each, `###` subsections 39 -> 47 with
none removed - because the working convention was "land a module, add a section describing
it". A condensation buys two days; only moving the detail out changes the slope.

- **`scripts/check_agents_md_size.py` gates the size, globally and PER SECTION**, and runs in
  CI. Both ratchets may ONLY EVER MOVE DOWN. `SECTION_BUDGETS` must SUM to no more than
  `CEILING`, so a budget cannot be raised without lowering another; a `##` section with no
  budget row FAILS rather than defaulting, because a section that exists and silently has no
  bound is the trap.
- **`scripts/check_agents_md_preservation.py` gates a MOVE**, and is run by hand with a
  `--before`. `--after` takes the rule corpus - AGENTS.md first, then every destination,
  **ENUMERATED, never a glob**: a glob would let a rule survive because its identifier happens
  to occur in an unrelated file, and the enumeration IS the reviewable list of claimed
  destinations. It prints WHERE each moved rule landed, so a PASS reads as a mapping rather
  than a count.
- **A section may shrink to an index row; it may not shrink into silence.** Past the shrink
  floor, what remains must NAME a destination that is in the corpus AND has gained content.
  Naming a destination that received nothing is refused.
- **Headings never move**, whatever the prose does: section numbers are cross-referenced from
  code (`AGENTS.md 10.1`, `§10.5`).
- `tests/test_agents_md_gates.py` pins that both gates can FAIL - AGENTS.md 10.4's rule
  applied to the gates on this file.

## 10. Cross-cutting rules

These hold across steps and cost a full audit cycle each. Do not undo them.

### 10.1 Contracts between steps

**Key-name mismatches are the dominant bug class.**
Index required keys directly so a rename fails loudly; never `.get()` a default for a key a contract promises. [why - and the known disagreements](docs/RULE_EVIDENCE.md#key-name-mismatches)
Join semantic documents to the catalog with `library/tools/semantic_index.py`: documents are keyed by FILE STEM, the catalog by `clip_XXX`. [why](docs/RULE_EVIDENCE.md#the-transition-planner-read-the-raw-document)

**A step reads the vision document through a SUMMARY its own handoff names, not through the raw document.**
Detail: `library/tools/vision_schema_adapter.py`. [why](docs/RULE_EVIDENCE.md#the-transition-planner-read-the-raw-document)

**`compile_manifest` reads `pipeline_data.json`, not just files.**
Detail: `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#compile-manifest-read-an-empty-catalog)

**Declare `interface.llm_outputs` on any hybrid step whose bridge emits a key the step also declares as an output**
Detail: `library/processes/edit_video/run_pipeline.py`. [why](docs/RULE_EVIDENCE.md#empty-llm-schema)

**One vision schema, two views.**
Detail: `library/tools/vision_schema_adapter.py`. [why](docs/RULE_EVIDENCE.md#vision-schema-two-views)

**A project's brand template reaches the run through `state["brand_template"]`.**
Detail: `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run)

**A project that names no brand template gets NOTHING, and every slot's reading of that absence is written down.**
Detail: `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#a-template-nobody-chose)

**A project's own declarations reach every step through `state["project_config"]`.**
Detail: `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#a-template-nobody-chose)

**A brand's CONSTRAINTS reach three planning steps, and a step has two names.**
Detail: `library/tools/brand_registry.py`. [why](docs/RULE_EVIDENCE.md#the-brand-reached-no-planning-step)

**The captain's creative brief is one per-project declaration that reaches NINE steps, BY REFERENCE, WHEN THE PROJECT ATTACHES IT.** **A run that attaches none INTERVIEWS rather than going quiet.**
Detail: `library/tools/brief_reference.py`. [why](docs/RULE_EVIDENCE.md#the-brief-is-paid-seven-times)

**A reference is an ABSOLUTE PATH plus a MAP, and the rule for what still travels inline is in `library/tools/brief_reference.py`.**
Detail: `library/tools/brief_reference.py`. [why](docs/RULE_EVIDENCE.md#the-brief-was-copied-seven-times)

**A step may carry ONE reading of a measurement, or two on different axes - never the reading and the structure it was read from.**
Detail: `library/tools/footage_reference.py`. [why](docs/RULE_EVIDENCE.md#three-views-of-one-analysis)

**Every LLM step declares `context_fields`, and the deterministic half loses nothing by it.**
Detail: `library/tools/context_views.py`. [why](docs/RULE_EVIDENCE.md#two-steps-had-no-projection)

**A step's decision must be SOURCED from its own context.**
Detail: `library/tools/replay_bench/bench.py`. [why](docs/RULE_EVIDENCE.md#the-decision-that-was-remembered-not-sourced)

**A table the prompt names, arriving with zero rows, is reported on the run that sends it.**
Detail: `library/tools/context_views.py`. [why](docs/RULE_EVIDENCE.md#a-prompt-that-described-an-empty-table)

**Word timings do not reach a prompt, and what a step cannot select by NAME it selects with a named VIEW.**
Detail: `library/tools/context_views.py`. [why](docs/RULE_EVIDENCE.md#the-transcript-arrived-with-every-word)

**Never send a summary and the structure it was rendered from.**
Detail: `library/tools/vision_schema_adapter.py`. [why](docs/RULE_EVIDENCE.md#the-summary-and-its-own-source)

**No raw value list reaches a prompt.**
Detail: `library/tools/beat_grid.py`. [why](docs/RULE_EVIDENCE.md#the-beat-grid-in-the-prompt)

**A step that chooses a picture is SHOWN one, and the picture is of the window it will receive.**
Detail: `library/tools/window_frames.py`. [why](docs/RULE_EVIDENCE.md#no-step-that-chose-a-picture-had-seen-one)

**A TOON table cell is quoted with a BACKTICK, and a multi-line value under a KEY is a `|` block.**
Detail: `library/tools/toon_serializer.py`. [why](docs/RULE_EVIDENCE.md#the-apostrophe-was-doubled-in-every-prompt)

**A step reads only the keys the PRODUCING step is asked for, and `creative_direction` is the enumeration that proves it.**
Detail: `library/tools/creative_direction.py`. [why](docs/RULE_EVIDENCE.md#seven-reads-of-a-key-that-cannot-exist)

**A call with nothing to ask is not made.**
Detail: `library/processes/edit_video/run_pipeline.py`. [why](docs/RULE_EVIDENCE.md#thirty-three-thousand-tokens-for-three-bytes)

**The delivery format is a property of the PRODUCT, not of the footage.**
Detail: `library/tools/delivery_format.py`. [why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)

**The beat grid is `tempo.beats` / `tempo.downbeats`, and it does not start at zero.**
Detail: `library/tools/beat_grid.py`. [why](docs/RULE_EVIDENCE.md#the-beat-grid-does-not-start-at-zero)

**`target_energy` has ONE reading: `library/tools/energy_reading.py`.**
Detail: `library/tools/energy_reading.py`. [why](docs/RULE_EVIDENCE.md#building-is-not-high)

**`music_behavior` has ONE vocabulary: `library/tools/music_behavior.py`.** **The timeline's length comes from the spine, never from a passage's `end_time`.**
Detail: `library/tools/music_behavior.py`. [why](docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary)

### 10.2 Reaching the picture and the sound

**A capability is only real where the renderer reads it.**
Detail: `library/tools/execution/apply_fusion_comps.py`. [why](docs/RULE_EVIDENCE.md#unread-parameter-names)

**An empty VFX plan says WHY it is empty, and step 4.03 CAN produce a non-empty one.**
Detail: `library/tools/vfx_plan_basis.py`.

**An overlay that draws nothing is not rendered.**
Detail: `library/tools/motion_graphics_plan.py`. [why](docs/RULE_EVIDENCE.md#overlays-that-draw-nothing)

**An overlay segment the manifest names and disk does not have REFUSES the compile.**
Detail: `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)

**A MODEL plans the motion-graphics layer, on its own timebase, in rows - and a brand template REFINES it rather than gating it.**
Detail: `library/tools/motion_graphics_plan.py`. [why](docs/RULE_EVIDENCE.md#the-motion-graphics-that-were-planned-and-absent)

**Manifest validation has a semantic half.**
Detail: `library/tools/manifest_validator.py`. [why](docs/RULE_EVIDENCE.md#manifest-validator-semantic-half)

**Every frame of the timeline must show a clip.**
Detail: `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#undeclared-black)

**Overlay geometry comes from `library/tools/safe_area.py`, and captions are grouped by measured pixels.** **Every element `MotionGraphics/index.tsx` draws is positioned from the insets, the progress bar included**
Detail: `library/tools/safe_area.py`. [why - the profile, and the grouper that never ran](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)

**Every element is positioned from the insets**
Detail: `library/tools/safe_area.py`. [why](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)

**Reconstruct a grouping with `fits_in_box`, never `fits`.**
Detail: `library/tools/safe_area.py`. [why](docs/RULE_EVIDENCE.md#the-caption-grouping-reconstruction-used-the-wrong-predicate)

**A project may typeset its own captions, and that is not a change to anyone else's.**
Detail: `library/tools/subtitle_style.py`. [why - the captain's measurement, and what the size moves on 001](docs/RULE_EVIDENCE.md#the-caption-size-that-governs-one-video)

### 10.3 Measuring the footage

**Subject position comes from `face_center_x`, not from the vision pass.**
Detail: `library/tools/subject_framing.py`. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
Detail: `library/tools/subject_framing.py`.

`SUBJECT_HEADROOM` is how much clear space the subject needs on each side. [why](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)
Detail: `library/tools/subject_framing.py`. [why](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)

**Face frames are sampled at the CLIP'S OWN aspect, never a fixed shape.**
Detail: `library/steps/step_1_04_temporal_index/step.py`. [why - the numbers, and what the fix does not fix](docs/RULE_EVIDENCE.md#the-squashed-face-frame)

**The frame FILLS by default, and there is no heuristic.** **A framing DECLARATION is not a framing DELIVERED, and the manifest records both.**
Detail: `library/tools/framing_intent.py`. [why](docs/RULE_EVIDENCE.md#the-letterbox-default)

**A file on disk is not a measurement.**
Detail: `library/tools/analysis/speech_advanced_pipeline.py`. [why](docs/RULE_EVIDENCE.md#hollow-prosody-files-cached)

**Never invoke `step_1_03_semantic_analysis/step.py` against a real project to test it.**
Detail: `library/steps/step_1_03_semantic_analysis/step.py`. [why](docs/RULE_EVIDENCE.md#semantic-analysis-triggers-a-vision-run)

**`usable_ranges` is a measurement, and an absent one is EMPTY - never the whole clip.**
Detail: `library/tools/analysis/picture_quality.py`. [why](docs/RULE_EVIDENCE.md#usable-ranges-were-the-whole-clip)

**Camera steadiness has ONE reading, and it says which signal answered.**
Detail: `library/tools/camera_stability.py`. [why](docs/RULE_EVIDENCE.md#the-residual-nobody-read)

**No assessment field reports a default as though it were measured. That is the whole rule, and it holds for every field.**
Detail: `tests/test_assessment_reports_no_default_as_measured.py`. [why - the four found in #301, and what a re-run of 001 would and would not fix](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)

Every field of `compute_deterministic_assessment` with nothing to measure must be an admitted absence. [why](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)
Detail: `tests/test_assessment_reports_no_default_as_measured.py`. [why](docs/RULE_EVIDENCE.md#no-assessment-field-reports-a-default)

### 10.4 Gates, and what counts as evidence

**A gate that cannot fail is worse than no gate, because it reads as coverage.**
If you cannot make it read real state, delete it. [why](docs/RULE_EVIDENCE.md#gates-that-cannot-fail)

**Passage engagement is a JUDGEMENT the model writes, it is an ORDERING, and there is NO SCORE.**
Detail: `library/tools/passage_engagement.py`. [why](docs/RULE_EVIDENCE.md#every-line-scored-the-same)

`tests/test_passage_engagement.py`.
Detail: `library/tools/passage_engagement.py`.

**A recommendation is APPLICABLE where it is made, or it is an OBSERVATION that names who owns it.**
Detail: `library/tools/cohesion_scope.py`. [why](docs/RULE_EVIDENCE.md#the-review-recommended-what-it-could-not-do)

**A SKIPPED test must name an environment that runs it, and a test body must be able to fail.**
Detail: `tests/test_no_unfailable_tests.py`. [why](docs/RULE_EVIDENCE.md#five-tests-skipped-in-every-environment)

**A gate that FAILS correct output is no more coverage than one that cannot fail.**
If you add a model-judged gate, give it a deterministic half that can carry the verdict, and record the model's opinion rather than enforcing it. [why](docs/RULE_EVIDENCE.md#gates-that-fail-correct-output)

**Seven baseline-craft properties are checked on every build, and two of them deliberately do not fail.**
Detail: `library/tools/render_qa.py`.

`render_qa.py` measures the RENDER:
Detail: `library/tools/render_qa.py`. [why](docs/RULE_EVIDENCE.md#a-dim-shot-is-not-a-letterbox-bar)

**`subtitle_gaps` measures the uncaptioned seconds INSIDE a speech block, and it reads the spine to know which those are.**
Detail: `library/tools/render_qa.py`.

`manifest_validator.py` checks the PLAN: no caption card under 0.5s, and no effect family covering 100% of eligible items with two or fewer parameter sets.
Detail: `library/tools/render_qa.py`. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)

Chroma and the mix REPORT A NUMBER and pass.
Detail: `library/tools/render_qa.py`. [why - including why frame-mean saturation is not the statistic](docs/RULE_EVIDENCE.md#baseline-craft-properties)

`SPEECH_ABOVE_BED_GATES` stays False: `background` means clip gain while the check reads it as SEPARATION.
Detail: `library/tools/render_qa.py`. [why](docs/RULE_EVIDENCE.md#the-mix-target-is-not-a-separation)

**A clip gain is not a separation, and both halves now SAY which one they are holding.**
Detail: `library/tools/music_behavior.py`.

**The bed is fitted at the SECTION that plays, and the offset is a REQUIRED argument.**
Detail: `library/tools/render_qa.py`. [why](docs/RULE_EVIDENCE.md#the-bed-was-fitted-from-the-wrong-second)

**The bed is bounded by the PICTURE, not by V1.**
Detail: `library/steps/step_5_04_compile_manifest/step.py`. [why](docs/RULE_EVIDENCE.md#the-bed-was-trimmed-to-the-last-v1-clip)

**The bed's own measurements reach the mix, because a step that cannot see the music cannot act on any answer about it.**
Detail: `library/tools/music_measurement.py`.

**Every QA finding has a reader, and one that has none is reported.**
Detail: `library/tools/qa_findings.py`. [why](docs/RULE_EVIDENCE.md#the-qa-report-had-no-reader)

**The rough-cut review's own answer has a reader, and it has two halves.**
Detail: `library/tools/cut_verdicts.py`. [why](docs/RULE_EVIDENCE.md#the-review-answered-and-nobody-read-it)

### 10.5 Creative latitude

**The pipeline never invents a creative judgement on the model's behalf.**
A CREATIVE fallback substitutes taste (a mood, a theme, a transition, an effect, a sound, an energy word) and it goes. A MECHANICAL default is a safe technical value (a frame rate, a timeout, a codec) and it stays. Where a creative value is genuinely absent, FAIL or REPORT PLAINLY. [why](docs/RULE_EVIDENCE.md#the-pipeline-invented-taste-where-no-step-ran)

- Two things are NOT taste, and are the reason the rule is workable. A value meaning "nothing is drawn" - `transition_vocabulary.CUT_TYPES`, `house_look.NEUTRAL_CDL` - is the absence of decoration, not a choice of it. And a rule acting on a value the creative direction really DECLARED is not a fallback: `creative_cohesion` may judge a transition against a declared "high", but may not invent the word first.
- A plan entry that names no effect, no sound or no level is DROPPED with the reason. Never completed from a constant, in a bridge or in `compile_manifest`.
- **How strong an effect is is the PLAN's number, not a scale the engine offers.** Step 4.03's `INTENSITY_MAP`, which resolved `subtle|moderate|strong` into fixed zoom and shake values and justified its ceiling by citing this file - a document that step never reads - is REMOVED (captain, 2026-09-02). `plan_vfx.TOOLKIT_PARAMETERS` replaces it and carries NO value, default or bound: it enumerates only the parameter NAMES `build_effect_comp` dispatches on, because a name with no reader draws nothing and says nothing (§10.2). An entry whose `params` name none of them is dropped as `no_readable_parameters`; the values in them are never checked, clamped or substituted.
- An alias may RENAME a capability and may not CHOOSE one. `push_in` -> `zoom_emphasis` is a fact; `slow_zoom` -> `slow_zoom_in` answered "which way?" for the planner and is withdrawn.
- Dead code that states taste is removed, not left.

**There are NO creative floors, and there must not be again.**
Detail: `tests/test_no_creative_floors.py`. [why](docs/RULE_EVIDENCE.md#no-creative-floors)

**Sound-effect selection is one enumeration, `library/tools/sfx_library.py`, and the model names a FILE.**
Detail: `library/tools/sfx_library.py`. [why](docs/RULE_EVIDENCE.md#the-sfx-chooser-was-a-word-list)

**Music selection is one enumeration, `library/tools/music_selection_contract.py`.** **and**
Detail: `library/tools/music_selection_contract.py`.

**Nothing refuses a track on rights, and no rights model may be built.**
Detail: `library/tools/music_search.py`.

**Search is one enumeration, `library/tools/music_search.py`, and it runs by default.**
Detail: `library/tools/music_search.py`. [why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)

**Two candidates that are the same recording are established from the MEASUREMENTS, never the filename.**
Detail: `library/tools/music_duplicates.py`. [why](docs/RULE_EVIDENCE.md#a-third-of-the-choice-set-was-a-copy)

**Which SECTION of the track plays is the model's decision, and there is no best-section rule.**
Detail: `library/tools/music_section.py`. [why](docs/RULE_EVIDENCE.md#the-splices-that-reached-nothing)

**The bed is a SEQUENCE, not one continuous minute of one track.**
Detail: `library/tools/music_bed.py`.

**The envelope is measured for the sections the model SHORTLISTS, in a second pass.**
Detail: `library/tools/second_pass.py`.

**Which SECONDS of a chosen cutaway play is decided from the PICTURE, never from its audio.**
Detail: `library/tools/cutaway_window.py`. [why](docs/RULE_EVIDENCE.md#the-cutaway-window-came-from-a-muted-waveform)

**Every candidate is MEASURED, and nothing about it is classified.**
Detail: `library/tools/music_measurement.py`. [why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)

## 11. Third-Party Asset Licenses

**Anything added to `library/presets/` from an outside source needs its licence recorded here before it lands.**

**No third-party look assets ship.**
A look is declared by a brand template as CDL plus Fusion values, and this repository ships none of its own - see §12.
`tests/test_color_grade_delivery.py` fails if any `.drx` reappears. [why](docs/RULE_EVIDENCE.md#the-unlicensed-powergrade)

**One third-party asset does ship, with its licence.**
Montserrat, as `remotion-subtitles/public/fonts/Montserrat-Variable.ttf` - a variable font covering the 100-900 weight axis, which is every weight `library/tools/subtitle_style.py` can ask for.
Licensed under the **SIL Open Font License 1.1**; licence file is `public/fonts/OFL-Montserrat.txt`.

- **Bundle fonts; never import one over HTTP.** [why](docs/RULE_EVIDENCE.md#the-webfont-race)
- `tests/test_bundled_fonts.py` fails if the font or its licence goes missing, if a font is imported over HTTP again, or if a template names a font that is neither bundled nor explicitly accepted as a system font.

**A declared typeface must be one that really draws the glyphs.**
One enumeration, `library/tools/render_fonts.py` - bundled, accepted as a system font, or carried by the project as a `font_file` staged out of `<project>/brand_assets/` by `prep_remotion`.

## 12. The look

**There is no house look.** The engine ships no slope, no saturation, no contrast, no glow, no grain and no vignette, and a project gets a grade only where a brand template it NAMED declares one.
`library/tools/house_look.py` holds no values of its own. [why](docs/RULE_EVIDENCE.md#there-is-no-house-look)

**A project that names no template still gets a GRADE, because a colourist decides one.**
One enumeration, `library/tools/color_correction.py`. [why - the nine measured clips and the identity CDL](docs/RULE_EVIDENCE.md#the-step-that-measured-nine-clips-and-graded-none) Step 5.01 measured 001's nine clips across a 2.7x luma spread - clip_011 at 145.495, clip_017 at 53.116 - and wrote the identity CDL on all nine, because normalisation was reachable only through an `exposure_reference` only a template declares. Captain, 2026-09-03: *"we need to still let the LLM understand it should try to add some color grading if it thinks it is needed rather than saying no completely bc of a lack of brand template."*

- **The correction is its own field, NOT `exposure_reference` reused.** That slot is a per-SERIES scalar a template DECLARES; a correction is per-clip, is a JUDGEMENT, and a template that declares one must keep winning. Writing a model's answer into a template slot would make the key mean two things depending on who wrote it.
- **The two compose EXACTLY, in a stated serial order**: `out = (in * (2**exposure_stops * slope * look_slope) + (look_offset + offset)) ** (look_power * power)`, saturations multiplied. Every step is exact - `(x**p)**q == x**(p*q)`, a pre-scale folds into slope, a same-stage offset adds - which is why the order is fixed. **A declared look with no correction is byte-for-byte `look.cdl()`.**
- **No bound and no default.** How far a correction may travel is the colourist's, the same way `house_look` bounds no declared slope. A malformed VALUE RAISES so `post_bridge_retry` carries it back to the model; an entry naming no clip, no term or no `why` is DROPPED with the reason (`DROP_REASONS`, refused if outside).
- **`correction_basis` says which absence an ungraded run is.** FOUR readings, spelled differently on purpose: `corrected`, `judged_no_correction_needed` (a decision), `no_correction_decision` (nobody looked), `every_entry_dropped`. The old output could not tell the second from the third - an identity CDL read the same either way.
- **`WITHHELD_TERMS` records what a correction may NOT say** and where it lives instead: `temperature` (no CDL term; say it as slope and offset), `contrast` (Fusion's, not the CDL's), `curve` (no reader anywhere).
- 5.01 is now HYBRID: `bridge.py` measures and builds `clip_exposure` + `cut_adjacency` (the pairs a viewer sees, in stops), `handoff.md` asks a colourist, `post_bridge.py` composes. `tests/test_color_correction.py`, `tests/test_color_grade_is_decided.py`.

A look is delivered in two halves, because that is what the mechanisms can express:

- **CDL** carries hue and level - slope (highlights), offset (shadows and the black floor), power (midtones), saturation - applied by `SetCDL` in `resolve_build_timeline`.
- **Fusion** carries what a CDL has no term for - pivot contrast, glow, grain, and a shaped, optionally coloured vignette - and reaches the picture only through the parameter names `fusion/comp_builder.build_effect_comp` dispatches on (§10.2).

- **`LOOK_ELEMENTS` is the whole vocabulary**, and an element outside it is REFUSED by name. Each row says which half delivers it and why that half and not the other.
- **An element is declared WHOLE or refused.** A glow with a gain and no threshold cannot be finished without the engine choosing the missing number, which is the defect this section exists for. Same shape as `bookends` (§13): raise, never drop and never complete.
- **No element has a default and none has a bound.** How strong a glow is, and how far a slope may travel, are the declaring author's decisions; an engine-supplied range is a strength nobody chose arriving one level up.
- **A project declaring no look gets NOTHING** - not a reduced look and not exposure normalisation. `NEUTRAL_CDL` is identity and `fusion_look` is `{}`, so no clip gets a comp for the look's sake at all. This is the shape #297 established for every other brand slot (§10.1).
- **A vignette is drawn only where one was asked for.** `build_effect_comp` used to default `vignette` to True, drawing one at blend 0.25 on every clip carrying a zoom.
- **Exposure is MEASURED, and normalised only onto a reference the declaration carries.** A clip nothing measured carries `null` and a reason, never `0.0`. `exposure_reference` is the declared target. [why](docs/RULE_EVIDENCE.md#the-exposure-probe-measured-nothing)
- `tests/test_house_look.py`, `tests/test_color_grade_delivery.py`.
## 13. Intros, outros and end cards

One enumeration, `library/tools/bookends.py`.

- A brand template declares `content.bookends`; a template that declares nothing gets nothing.
- A declaration names either an `asset` that already exists or a `composition` to render, plus a `duration_seconds`. **A malformed declaration raises rather than being dropped.** [why](docs/RULE_EVIDENCE.md#bookends-only-on-some-videos)
- **A card takes the same path as every other clip.** `mesh_spine` turns each declaration into an `intro_card`/`outro_card`/`end_card` spine block (NOT `intro`/`outro`, which already mean a non-speech pacing beat).
- **A card the PLAN wrote REFUSES the step, by name.** `bookends.assert_no_invented_bookends` raises `InventedBookendBlock`. `intro` and `outro` are NOT cards and are never dropped. [why](docs/RULE_EVIDENCE.md#the-card-that-vanished-into-a-log-line) Same shape as `UnplayableSfxPlan` in 4.04 (§10.5).
- Never append a card out of band after compilation.
- Project-owned compositions are staged **verbatim** by `bookend_render.py` into gitignored build output. The engine renders a client's asset; it never edits one.

## 14. General assets vs project assets

`docs/ASSET_LIBRARY_PLAN.md` is the standing test.
Three questions, and an asset must pass all three to live in the engine - failing any one is sufficient to stop it:

1. **Substitution**: does it survive being handed another series' content, or does it encode one series' copy, palette or typeface?
2. **Timing and geometry**: is it anchored to a spine block and to the picture the delivery format actually produces, or to absolute frames and a full-bleed frame nothing renders?
3. **Reader**: does a step read it, and does a test assert the picture changes?

Question one decides where it lives; two and three decide whether it is finished. [why](docs/RULE_EVIDENCE.md#the-4th-wall-end-card)

- **A brand template may set per-series PARAMETERS and may not contain ARTWORK**: no on-screen copy, no coordinates or frames describing one finished episode.
- Artwork is a project asset, declared by reference through `content.bookends` and staged verbatim (§13).
- **Per-series typefaces live per project, not in the engine.** Font files and their licences belong with the project that owns the series; the engine stays series-neutral.

**Timed text is artwork, and the project wins.**
A timed text moment is copy the viewer reads, so it is ARTWORK and belongs with the project - which is what makes a timed-text declaration legal at all under question three.
One enumeration, `library/tools/timed_text_overlay.py`.

- A moment is timed from the SPINE (`block` + `anchor` + `offset_seconds` + `duration_seconds`). Absolute `start_frame` stays available and is bounded by the spine's real length, and a malformed declaration raises.
- Moments whose spans touch are grouped into one rendered segment.
- `timed_text_render.py` renders each inside step 4.06; `resolve_build_timeline` places them on **V6**.
- A project's `project.yaml` may carry its own `effect.timed_text_overlay`, replacing the brand template's whole slot. `timed_text_overlay.resolve_declaration` is that half.
- **The card states its own `y`** (normalised against the delivery frame). Keep `y` in 0.35..0.65. [why](docs/RULE_EVIDENCE.md#the-night-card-y-band)

## 15. Notes the captain types onto the timeline

The captain reviews a built timeline **inside DaVinci Resolve** and drops markers on it carrying
natural language - what looks wrong, what to change, what to go and find out.
One enumeration, `library/tools/marker_feedback.py`, which reads them and writes them to disk.
It is proved against a real running Resolve by `tests/test_marker_feedback_against_resolve.py`;
its recorded per-call findings are in the module docstring, in the shape `neural_engine.py` uses.

- **A MARKER CARRIES TWO PIECES OF TYPED TEXT AND BOTH ARE READ.** `GetMarkers()` returns `name`
  (the Add Marker dialog's **Name** field, where the cursor lands) and `note` (its **Notes**
  field). Reading only `note` loses everything typed into Name, silently, with the marker still
  on the timeline. Both are kept verbatim; nothing is summarised, truncated or normalised.
  `Timeline.AddMarker` REFUSES a marker whose name is empty.
- **A timeline marker's frame is relative to `Timeline.GetStartFrame()`; `TimelineItem.GetStart()`
  is absolute.** A clip marker's frame is a SOURCE frame, the same space as `GetLeftOffset()`, so
  `timeline_frame = item.GetStart() + (key - item.GetLeftOffset())` and only inside the range the
  clip plays. Resolve bounds-checks neither. A key outside that range is kept UNPLACED with the
  reason, never clamped to the clip's head.
- **`TimelineItem.GetProperty("Comments")` is always None.** Clip comments are a MEDIA POOL
  property. A timeline item's property dict holds transform keys only - read it with no argument
  (§5) before trusting a name.
- **The record goes to `<project>/marker_feedback/`, and that is why `Kind.CAPTURED` exists.**
  Everything under `pipeline_output/` is `Kind.OUTPUT` - safe to delete because a re-run
- **The build path REFUSES to delete a timeline carrying uncollected notes.** `guard_timeline_deletion` fails the build, naming the notes. `PIPELINE_DISCARD_TIMELINE_MARKERS=1` is the override.
- **The reader READS, and the two things that write to a marker write only `customData`** -
  the capture button and the decision stamp. Neither creates a marker, touches `name`/`note`/colour/duration, or deletes one.
  No colour vocabulary; no acknowledgement marker is written back.

### Where a note goes

**A collected note is routed to the step that owns the decision it is about, and an
ambiguous one is reported as ambiguous rather than sent somewhere.**
One enumeration, `library/tools/marker_routing.py`.

- **A CLIP note and a MOMENT note are different things and are never flattened together.**
  A `clip_marker`/`media_pool_marker`/`clip_comment` carries `clip`; a `timeline_marker` carries `clips_under` (CONTEXT).
  `marker_feedback.MarkerNote.attached_clip` records the placement; a pull file written
  refused - reported `unresolved` - unless the candidates are one clip. Resolve's linked
  `MarkerNote.attached_clip` records the placement; legacy recovery is refused unless candidates are one clip.
- **`STEP_DECISIONS` is the whole of what a note can be routed to**, each row naming the
  decision that step makes. A step outside it cannot be routed to, and a note naming one is
  refused BY NAME.
- **Two bases, and two non-answers.** `declared` is authoritative. Otherwise the note's words must name EXACTLY ONE step's decision. Two is `ambiguous`; none is `unrouted`. No score, no ranking, no tie-break, no default.
- **Delivery is `prompt` or `report`, declared per step.** A step with a `handoff.md` declares
  the `timeline_notes` input and `gather_step_inputs` hands it `prompt_block()` - the words
  plus a legend, the route `MEASUREMENT_LEGEND` and `CUTS_LEGEND` take, because the handoffs
  are frozen. A deterministic step has no prompt at all; the note is still routed, recorded
  and reported, with that reason stated. `run_pipeline.project_step_context` restores
  `timeline_notes` BY NAME, so a `context_fields` allow-list neither has to list it nor can
  drop it (§10.1).
- **Nothing may silently drop a routed note.** `assert_deliverable` fails the run when a note
  is routed to a prompt step whose manifest does not declare the input, because a context
  assembled without it reads exactly like a run with no notes. `undelivered` accounts for
  every note that reaches no prompt, by name.
- **A note that reached NOBODY is named in the run summary and recorded on the note.** The summary prints them after `status` is decided. Resolving an ambiguity is the captain's call.
  after `status` is decided and `record_non_delivery` appends them to the same log the
  deliveries go to. **It stops at visibility**: `WITHDRAWN_ROUTERS` records why every tie-break
- The delivery log is APPENDED by the runner, in the `Kind.CAPTURED` area beside the pull
  files: a delivery is a thing that happened, and a later run delivering the same note does
  not unmake the record of the first. `ROUTED-NOTES.md` is generated from the pull files and
  never hand-edited.
- `tests/test_marker_routing.py`, whose note fixtures are the three the captain really typed.

### What decided the clip the note is on

**Every clip on the built timeline carries the decision that produced it, and the routing
reads that instead of inferring - but only where inference has nothing.**
One enumeration, `library/tools/timeline_decisions.py`, and it is the producer half of the
loop above.

- **`TRACK_DECISIONS` is one row per track of the manifest, and a track with no row is
  REPORTED, never attributed to the nearest step.** **V1 A-roll is `speech_sequence` (2.02), not `assign_aroll` (3.01)**; **a bookend card is not stamped at all** (§13). `UNSTAMPED_PLACEMENTS` records both.
- `DECISION_BASES` keeps `chosen` and `declared` apart, the same line §10.5 draws.
- **A1 is not a placement.** `LINKED_AUDIO_OF` says so: `compile_manifest` builds A1 from the
  same V1 clip dicts, and surveying it would stamp two records where the timeline has one clip.
- **The build writes a LEDGER and creates NO marker.** Step 6.01 writes `timeline_decisions.json` from the manifest and merges decisions into existing markers via `UpdateMarkerCustomData`.
  `pipeline_output/steps/6_01_render/timeline_decisions.json` from the manifest alone, and
- **The stamp ranks BELOW the captain's own words.** Order: declared > vocabulary > stamped. The stamp closes the UNROUTED case without touching the routed ones.
  ones. `STAMP_RANKS_BELOW_THE_WORDS` is the record.
- **A MOMENT note is never routed by the stamp**, only a note attached to ONE clip. That is
  the captain's own selection, not the stack at a frame, so it is not
  `marker_routing.WITHDRAWN_ROUTERS["the_clip_under_the_playhead_decides"]` coming back. What
  was playing under a moment is recorded as `decision_context` and routes nothing.
- A marker's own stamp outranks the ledger, because it was written when the marker was made
  and the ledger describes the LAST build. Neither is guessed at: a placement that matches no
  row comes back as a stated reason.
- `tests/test_timeline_decisions.py`.
### The panel beside the timeline

**The pipeline is readable from inside Resolve, and the panel's whole reason to exist is that it knows where the PLAYHEAD is.**
`resolve_scripts/VEP Pipeline Panel.py` is the entry point; everything it DECIDES is in `library/tools/panel/`, which imports no Qt and no Resolve. [why](docs/RULE_EVIDENCE.md#the-panel-handed-the-model-a-filename)

- **The split is the rule.** Nothing under `library/tools/panel/` may import `DaVinciResolveScript`, `BlackmagicFusion` or a Qt binding; live Resolve facts arrive as `clip_context.ResolveContext`. `tests/test_panel_boundary.py`.
- **The clip under the playhead is joined to what the pipeline measured** - the catalog id, the vision observations, the transcript of the seconds that PLAY, and which step chose the placement (`timeline_decisions`). Every fact is a READING, never a document pasted in, and what could not be joined is SAID.
- **The picture is not the topmost item.** Use `clip_context.picture_at` (highest track carrying FOOTAGE), not `GetCurrentVideoItem()`. `clip_context.picture_at` takes the highest track carrying FOOTAGE and `overlays_at` reports the rest.
- **What the captain is LOOKING AT outranks what they last clicked**, and the prompt says which is which. `prompt_block` is bounded and says what it cut.
- **A large output is drilled down, never dumped.** `panel/trace.py` navigates one LEVEL at a time; a path that does not exist is refused by name.
- **The run is previewed before it starts**: the profile, the steps it will and will not fire with each reason, and the `run_scope` refusal VERBATIM. `panel/run_view.py` calls the same resolver the runner does and has no opinion of its own.
- **The panel launches the runner with the checkout's `.venv/bin/python3`, never `sys.executable`.** A checkout with no venv is REFUSED by name.
- **The handbrake stays advisory** and the panel never kills the runner.
- **It holds no credential**: the model is reached by shelling out to the already-authenticated `claude` CLI, overridable with `VEP_PANEL_MODEL`. Nothing secret is written into Resolve's application-support folder.
- **The FRAME under the playhead goes with the question.** `library/tools/panel/frame_attach.py` decides placement; `marker_capture.grab_still` is the ONE grabber. Stills go to `~/.vep_panel/frames/`, never under the project. A grab that cannot happen degrades to text-only with a stated reason. `VEP_PANEL_NO_FRAME=1` declines it. [why](docs/RULE_EVIDENCE.md#the-model-was-told-a-filename-and-not-shown-the-frame)
- **The model call runs where it can READ what the prompt points at.** The CLI will not open a file outside its working directory, and the panel's call set none, so it inherited whatever Resolve was launched with. The failure that comes out is the model answering *"I need permission to read the screenshot file"* - exit code 0, a plausible sentence, and nothing that looks like a bug. `frame_attach.call_site` names the directory and `frame_attach.reaches_the_file` is what the test pins, so the guarantee is *the call can read the still* and not *the call sets cwd* - `--add-dir` satisfies it too. Any future feature that hands the model a path depends on this. `tests/test_panel_frame_attach.py`.
- **Every slow thing goes on a worker thread** (`StepLoop(False)`), and the heartbeat under the header is proof it has not stuck.
- Toolkit facts that bite, all measured: `hasattr` is True for widgets that do not exist; `Stack.CurrentIndex` is broken and `Hidden` is the page switch; `Label.Pixmap` draws nothing and `<img>` in a read-only `TextEdit` does; `ui.Timer` never fires; `MinimumSize` is ignored by the layout and a stretch RATIO is not; Qt decides a string is rich text by looking for a tag.
- **A process that connected to Resolve leaves through `os._exit`, never `sys.exit`.** `fusionscript.so` does not join its own `RemoteApp` thread before its static destructor frees the pool that thread is using, so the C runtime's teardown can SEGFAULT. The panel's `_leave` flushes both streams and hands the status to the kernel.
- **A column is sized to the longest value it really holds, and a truncation may never read as a word.** A column that cannot be widened is dropped whole to the detail pane.
- **A screenshot of this panel is opened and read before it is committed.** Nothing else tells a picture of the panel from a picture of what was behind it; `docs/panel/README.md` records the one that got through.
- **Footage Search is deliberately not in the panel.** `library/dashboard/footage_search.py` is the only authorised caller of the footage index (§2) and widening that is the captain's call.

### What Resolve's script host does not give an entry point

**`__file__` IS NOT DEFINED there, and an entry point verified by running it as a FILE has not been verified.**
Both entry points in `resolve_scripts/` are launched by Resolve's own script host, which defines `__name__` as `"__main__"` but does NOT define `__file__` - and `importlib`, `exec_module` and `python3 the_file.py` all define it, so every route a test or a screenshot takes hides this. [why - the two menu entries that did nothing at all](docs/RULE_EVIDENCE.md#the-menu-entries-that-did-nothing)
Resolve's script host defines `__name__` as `"__main__"` but NOT `__file__`. [why](docs/RULE_EVIDENCE.md#the-menu-entries-that-did-nothing)

- **Read `__file__` in ONE place per entry point, inside a helper that answers `""` when it is absent.** Nowhere else, and `tests/test_resolve_scripts_bootstrap.py` fails on a second reader.
- **A fallback chain is evaluated LAZILY, or the last candidate can kill the first.** `_repo_root()` built its candidates as a tuple, so the `__file__` fallback raised before the stamped `REPO_ROOT` beside it - correct, and pointing at a directory that existed - was ever tested.
- **A bootstrap failure must reach the SCREEN.** `print` is the floor (reaches Resolve's Console), and a window built from `fusion`/`bmd` MAY NOT RAISE. Failures are held in `BOOTSTRAP_ERROR`.
- **Verify from the MENU.** `tests/test_resolve_scripts_bootstrap.py` executes each entry point's bootstrap the way the host does - `exec(compile(...))` into a namespace with no `__file__` - and that is the substitute for a click, not a replacement for one.
- **The two copies of `_repo_root` stay two.** Its whole job is to find the repository a shared copy would have to be imported from. The duplication is held by ONE test over BOTH files rather than by one function.
- `sys.argv` and the working directory are not relied on by either file, and the same test keeps it that way.
### The plugin inside Resolve's own window

**A Workflow Integration is the OTHER surface, and it is an Electron app driven through Resolve's JavaScript API.**
`resolve_workflow_integration/com.videoeditingpilot.vep`, installed by `scripts/install_workflow_integration.sh`; [`resolve_workflow_integration/README.md`](resolve_workflow_integration/README.md) is what it settled and what it costs.
It is PHASE 1 - it reads the playhead, plays video and reaches this repository's Python - and the Qt panel (§15 above) is untouched and still the surface the captain uses.

- **Resolve scans the plugin root ON STARTUP ONLY, and `Initialize()` fails for every plugin id Resolve did not launch itself** - Blackmagic's own sample included, with `Failed to open IPC`. So a newly installed plugin needs a Resolve restart, and self-launching the Electron app gives the whole UI and no Resolve data. Never restart Resolve to get one: it is the captain's application and one shared instance.
- **A Workflow Integration NEVER docks; it is a separate window, and that is structural.** Resolve launches it as its OWN process - `--plugin-id=<id>` under Resolve's bundled Electron, renderer `--enable-sandbox` - so the window belongs to a different pid and cannot be docked into Resolve's Qt workspace. Blackmagic's README is right and the marketing claim that a plugin docks is wrong for this mechanism. Settled by process ownership, not by eye. [why](docs/workflow_integration/README.md)
- **The JavaScript API is at parity with the Python one for what this project needs, and it has NO stream.** Enumerated live off the objects: 238 methods across `resolve` (29), `Project` (50), `Timeline` (63) and `TimelineItem` (96), recorded in `docs/workflow_integration/live_api_surface.json`. The whole marker vocabulary is reachable - `GetMarkers`, `GetMarkerCustomData`, `UpdateMarkerCustomData`, `AddMarker` - so §15's marker work does not need the Python bridge. Nothing anywhere is a transport control or a viewer mirror, which is what makes "playback is a FILE seeked to the playhead" a measured statement rather than an assumption.
- **The plugin READS, and the DEFAULT IS REFUSAL.** `js/readonly.js` is the complete list of methods it may call, `main.js`'s `guard` is the one door, and a name nobody thought to withhold is refused rather than permitted by omission. `WITHHELD` beats `READ_ONLY`, so slipping a call through takes two edits. `GrabStill`/`ExportStills` are withheld deliberately: a grab round-trips the GALLERY, which writes into the captain's project. The test EXERCISES the predicate under `node` rather than grepping for it.
- **The judgements do not cross the bridge.** `library/tools/workflow_bridge.py` is the one route into Python - one request on stdin, one answer on stdout, one process per request - and `OPERATIONS` is the whole of what may be asked. The catalog join it serves is `panel/clip_context.py` UNCHANGED, so the two surfaces cannot develop different answers about the same clip. Measured: 32-45 ms of process spawn, and 86-117 ms for the whole join.
- **`js/picture.js` is a deliberate second implementation of `clip_context.picture_at`, and it is GATED.** `tests/test_workflow_integration_plugin.py` runs it under `node` against the Python over a recorded timeline and over constructed cases the recording cannot reach - 001's V2 cutaways sit in the GAPS between V1 clips and every source runs at the timeline's rate, so the recording alone exercises neither the track preference nor the two-frame-rate arithmetic.
- **Playback is a FILE seeked to the playhead, never Resolve's viewer.** There is no STREAM in the API. Single frames are reachable - `GrabStill` (graded and conformed) and `GetCurrentClipThumbnailImage` (base64, Color page) - and neither is playback: a grab costs seconds and round-trips the gallery, so it writes into the captain's project, and neither is in `READ_ONLY_CALLS`. The source clip is ungraded with no comps, captions or mix; the rendered master is complete and only as current as the last render. Say which.
- **A fixture run says so on screen.** `VEP_WFI_FIXTURE` drives the whole page off a recording with no Resolve, and the header states it - a picture of the page must never be mistakable for a picture of live Resolve.
- `tests/test_workflow_bridge.py`, `tests/test_workflow_integration_plugin.py`.

### The button that captures the frame

Playhead on the moment, one click in **Workspace > Scripts > Capture Frame for Firstmate**, and the
frame plus whatever the captain typed is captured. `library/tools/marker_capture.py` is the whole
of it; `resolve_scripts/` is the entry point Resolve calls.

- **The repository is the source of truth.** The installer stamps the checkout's path. **Nothing else may write into the application support folder.**
- **`GrabStill` returns the GRADED, CONFORMED frame** regardless of the active Resolve page.
- **The still goes to `<project>/marker_feedback/stills/`** (`Kind.CAPTURED`). A timeline whose footage sits under no project is REFUSED.
- **The gallery is put back.** `GrabStill` leaves the still in the current album and Resolve saves
  it; the button deletes it again and reports the before/after count.
- **A marker that is already there is UPDATED, never replaced** - `AddMarker` refuses an occupied
  frame anyway, and the captain's `name` and `note` are what must survive. The playhead inside a
  marker with a duration resolves to that marker's start frame.

### What goes in `customData`

One enumeration, `library/tools/marker_payload.py`: a versioned ENVELOPE carrying a list of
self-describing records, with the reasoning for that shape in the module docstring.

- `schema` versions the ENVELOPE, `writer_version` versions one writer's own keys, and they move
  independently so a second writer can grow without every reader relearning the envelope.
- **An attachment is a record with a `path`, not a record of a particular kind**, so a writer
  pointing at a file gets "a reader can open this" for free.
- **`customData` this module did not write is kept under `foreign`, never overwritten.** The UI
  does not show the field, so nobody would notice it going missing.
- `pull` and `show` surface attachments, and a note with one prints differently from one without.
  **A path the captain TYPED into a note is surfaced too**, told apart by `origin`, matched
  conservatively (absolute POSIX path or `file://`) and never rewritten out of the text.
- `tests/test_marker_payload.py`, `tests/test_marker_capture_against_resolve.py`.
## 16. Motion graphics

**The motion-graphics elements this pipeline may plan are one enumeration, `library/tools/motion_graphics_vocabulary.py`, and it defines AXES rather than values.**
Fifteen elements across seven functions. [why](docs/RULE_EVIDENCE.md#the-roster-nobody-wrote-down)

- **An entry names a dimension; the magnitude belongs to whoever declares it.** `AXES` is the vocabulary of dimensions and no axis has a default or a bound. `colour_role` is a role of the declaring palette, never a colour; `type_role` is a weight, never a size.
- **Reachability is REPORTED per entry, never a filter on membership.** Four entries are `reachable_now`, ten need renderer work and one needs a measurement nothing takes. The render path is broken; a roster written around it would keep the defect after the repair.
- **`never` is not optional.** An entry that only says what a thing is teaches a model to reach for it everywhere, so every entry records refusals and `assert_roster_is_well_formed` raises without them.
- **The boundary is the whole point.** `OUT_OF_VOCABULARY` names the module that owns each near miss. An element is in this roster when it is an ADDITIVE OVERLAY carrying meaning the picture and the captions do not already carry.
- **Nothing here is keyed to one identity**, because the engine serves a daily channel and client work (§14). `channel_bug` draws a project-supplied asset; the engine ships no artwork and states none.
- **Two neighbouring decisions are the captain's and this file must not take either**: what produces the COPY a graphic shows, and whether the model authors a component or fills a props schema. An entry declares only WHETHER it needs a text payload. `COPY_SOURCE_IS_UNSET` records both; a change that would force one is a stop, not an implication.
- `roster_rows()` and `ROSTER_LEGEND` are the prompt-side route, the same shape `music_measurement.MEASUREMENT_LEGEND` takes. The whole roster ships - nothing is shortlisted, because whatever selects a shortlist becomes the chooser (§10.5). **Step 4.06's bridge is the consumer**, and `motion_graphics_plan.DRAWABLE` is DERIVED from the `reachable` column rather than listed twice (§10.2).
- `tests/test_motion_graphics_vocabulary.py`.
- The whole roster ships - nothing is shortlisted (§10.5).
## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

When a rule comes from an incident, state the rule here and put the incident in `docs/RULE_EVIDENCE.md` with a `[why]` link.
A reader should be able to obey every rule in this file without opening that one.
