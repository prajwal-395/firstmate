# Video Editing Pipeline

You are an autonomous agent operating the video editing pipeline.
This manual explains how to navigate the repository, run the pipeline, and make editorial decisions.

## How to read this file

Everything under a numbered section is a **rule**: something to do, or not do, when you touch this pipeline.
Rules are stated as rules, in the imperative, and they do not carry their own history.

The history is kept, and it matters, but it is not on the reading path.
The incident, measurement, date, crash log or diagnostic path behind a rule lives in [`docs/RULE_EVIDENCE.md`](docs/RULE_EVIDENCE.md), reachable from the rule as a `[why]` link.
Read a `[why]` when you want to overturn a rule, or when you need the numbers.
You never need one in order to obey a rule.

Before your first run, read §9 (environment), §3 (how a run works) and §5 (DaVinci Resolve).

Section numbers are cited from code comments and other docs, so they are stable.
Change what is in a section, not its number.

## 1. Identity and purpose

This repository is the engine for an automated video editing pipeline.
It takes raw footage, analyzes it, and generates a fully assembled DaVinci Resolve timeline: shortform vertical video with subtitles, B-roll, SFX and music.
The engine stores no project data - assets and pipeline outputs live in isolated directories outside the repository.
Agents and human editors use it to automate the tedious parts of video assembly while retaining creative control.

## 2. Repo layout

- `library/`: core pipeline implementation and shared Python modules.
- `library/processes/`: complete pipelines such as `edit_video`, with its `dag.json` and `manifest.json`.
- `library/steps/`: individual pipeline steps, named by phase (e.g. `step_1_01_scan_project`).
- `library/tools/`: shared utilities for Resolve scripting, vision analysis and file management.
- `library/schemas/`: Pydantic schemas for pipeline state and project configuration.
- `library/dashboard/`: FastAPI server for the human-in-the-loop review dashboard.
- `library/templates/`: brand templates defining styles, effects and content rules.
- `library/presets/`: Fusion macros and DaVinci's own built-in effect settings. Whatever reaches a timeline is found by direct path; there is no preset index.
- `remotion-subtitles/`: Node.js React app that renders subtitle overlays.
- `scripts/`: bash helpers for environment setup and maintenance.
- `tests/`: unit and integration tests for the engine.
- `manage_project.py`: top-level CLI for creating, listing and running projects.
- `requirements.txt`: Python dependencies.

**One thing in `library/tools/` is a prototype: it is in the DASHBOARD and stays out of the PIPELINE.**
`footage_query.py` / `footage_segments.py` / `footage_query_bridge.py` are a cross-clip footage
search - "where in all my footage does X happen".
A PERSON may call it: `library/dashboard/footage_search.py` serves the dashboard's Footage Search
view, and that is the only authorised caller.
A STEP may not: do not import either module from a step, the DAG or a manifest.
`tests/test_footage_query_prototype.py` fails if you do, and widening it further is the captain's
call - stated the way the dashboard carve-out was, by naming which half is being authorised.
[`docs/FOOTAGE_INDEX_PROTOTYPE.md`](docs/FOOTAGE_INDEX_PROTOTYPE.md) has what it measured, the
measured score floor that lets it answer "not in this footage", and the 66x reduction of
`temporal_index` that is worth doing without it.

## 3. Pipeline execution

The pipeline is a Directed Acyclic Graph (DAG) in `library/processes/edit_video/dag.json`, ordered by topological sort.
It groups 26 atomic steps into phases: 0 setup, PREFLIGHT analysis, 2 planning, 3 assembly, 4 post-production, 5 finishing/QA, 6 rendering.

- Call the analysis stage **preflight**, never "phase 1", even though its step ids read `step_1_0X_*`. `docs/PIPELINE_PLAN.md` uses Phase 0/1/2 for the quality-work programme and the two names collided.
- `library/steps/` holds 28 step definitions. `object_segmentation` (1.06) and `ocr_extraction` (1.07) exist but are not wired into the DAG. They were added in commit 3c4dd10 and 95affc1 (2026-08-08) without touching `dag.json`, and no downstream step consumes their output. Each carries a documented `unwired_reason` in `project_layout.STEPS` and `StepDir.__post_init__` rejects `wired=False` without one, so the next unwired step has to say why. `tests/test_step_dag_coverage.py` fails if a step directory exists with no DAG node and no unwired declaration.
- `objects[].readable_text` in semantic analysis output is the VLM's field - step 1.03 prompts for it directly. It is null on 001 because the local model (`gemma-4-12b-it-4bit`) fails to read on-screen text. Step 1.07 (OCR) writes to a SEPARATE output key (`ocr_extraction`), not to `readable_text`. The two are independent: fixing the VLM's text reading or wiring EasyOCR are separate improvements, not the same fix. #162 tracks the design question of what consumes masks and OCR output.
- **EasyOCR measurement on 001** (17 clips, 807s footage): 445s wall-clock, 367 tracked texts across 15/17 clips, 83 above 0.5 confidence. Real text found includes street signs ("Chattahoochee", "PARK", "Tetta Blvd NW"), dashboard navigation ("Google", route numbers), and storefronts ("THROW AXES", "VALIDATE PARKING"). Signal-to-noise is 23% - most low-confidence detections are noise from foliage and textures. The real detections are text the VLM returned null for.

Run with `python3 manage_project.py run <slug>`:

| Flag | Effect |
|---|---|
| `--from <step_id>` | resume from a step |
| `--step <step_id>` | run one isolated step |
| `--auto` | auto-complete hybrid steps from bridge output instead of pausing for LLM input |
| `--review` | enable review gates that pause for human inspection on the dashboard |
| `--resume` | continue after a review gate is approved or revised |
| `--dry-run` | print the execution plan without running steps |
| `--rerun <target>` | redo finished work; repeatable, and the ONLY supported way to re-run a completed step |

Steps come in three implementation types:

- **Deterministic**: a `step.py`, run automatically over JSON stdin/stdout.
- **Hybrid**: a `bridge.py` that pre-computes context plus a `handoff.md` prompt for an LLM.
- **LLM-only**: only a `handoff.md`, generating the output from upstream context.

Pipeline state lives in `pipeline_data.json` at the root of each project directory.
It tracks completed steps in two ledgers and stores all JSON outputs under `step_outputs`.
Inspect it to debug data flow or verify upstream results.

### Two ledgers, two lifetimes

One enumeration: `library/tools/step_ledger.py`.

- Every step manifest declares `classification.stage`. An undeclared or unknown stage raises rather than defaulting.
- **preflight** is enrichment of THIS PROJECT'S SOURCE FOOTAGE - scan, catalog, vision, transcription, prosody, segmentation, OCR - recorded in `preflight_completed`.
- **edit** is everything downstream of a creative decision, recorded in `edit_completed`.
- `validate_sfx_library` (0.01) is edit: it validates a SHARED library, not this project's footage.
- `music_analysis` (2.06) is edit: it enriches a CHOSEN asset, and the choice is what an edit reset discards.

The two keys are separate, so `--rerun edit` is structurally incapable of discarding enrichment - it never names the other ledger. [why](docs/RULE_EVIDENCE.md#whisperx-paid-twice)

    manage_project.py run <slug> --rerun edit                     # reset the edit run
    manage_project.py run <slug> --rerun temporal_index           # one step
    manage_project.py run <slug> --rerun temporal_index:clip_007  # one clip of one step

- Per-clip granularity works because each step declares where its per-clip artifacts live, in `classification.per_clip_artifacts`. The runner deletes exactly those files and the step's own "already on disk?" check recomputes exactly that clip.
- A step that declares none is re-run whole. **Add a per-clip artifact and you must declare it**, or nothing can invalidate it.
- Preflight is skipped once done, and that is safe because identity is checked. `library/tools/footage_identity.py` fingerprints each clip by size plus a digest of its first and last mebibyte - not a whole-file hash and NOT mtime - against `source_fingerprints` in the state file. Replaced, removed or renumbered footage invalidates exactly its own cached analysis. [why](docs/RULE_EVIDENCE.md#fingerprint-not-mtime)
- Do not build a caching framework or a content-addressed artifact store. The mechanism is one declared field, one split ledger, one re-run flag, one identity check.
- The per-clip index lives with the PROJECT: it resolves from `project_folder`, not the runner's CWD, and reuses any per-clip file already there instead of re-transcribing it. [why](docs/RULE_EVIDENCE.md#per-clip-index-in-a-worktree)

### Run status

The run summary reports `SUCCESS` only when the whole DAG is complete and `failed_steps` is empty in the project ledger - not just the steps this invocation touched.

- `FAILED`: a step failed, or emitted an `available: false`/hollow result, in this run or an earlier one. Exit code 1.
- `AWAITING_LLM`, `PARTIAL` (`--step`/`--from`/a review-gate pause left DAG steps unrun), `DRY_RUN`.
- `failed_steps` is current state, not a log: a step that later succeeds is removed from it.

## 4. Dashboard

Launch with `python3 manage_project.py dashboard <slug-or-path>`.
It needs none of the ML stack (§9), and a project kept outside `PIPELINE_PROJECTS_ROOT` is opened by passing its path.
**The picker lists the project the server is SERVING, first**, even when that project is outside the root - `/api/projects` otherwise enumerated only the root, named a different project as selected, and left no route back to the one on screen.
It serves a pipeline view, a footage library, a footage SEARCH, a transcript view and a timeline view, and it is the human-in-the-loop review layer.

- Review gates pause execution for inspection. Reviewers approve, reject or revise. A rejected gate halts the pipeline entirely; a revised gate applies the reviewer's modifications directly to the step output in `pipeline_data.json`.
- The dashboard also captures annotations and feedback as structured data for agent communication.
- **Extend this dashboard. Never author a fresh per-run review page.** [why](docs/RULE_EVIDENCE.md#review-surface-is-this-dashboard)

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
  dense cosine (`DENSE_SCORE_FLOOR`), never on the blended score, which is min-max normalised
  and therefore ~1.0 at the top for every query ever asked. Below it and above
  `DENSE_WEAK_FLOOR` is a WEAK band, reported as a near miss rather than as a result.
- Staleness compares an `ingest_fingerprint` recorded at build time. `pipeline_data.json` is
  fingerprinted by its `catalog` subtree only - the state writer rewrites the whole file after
  every step, so a downstream step landing is not a change to the footage.
- `tests/test_dashboard_footage_search.py`.

### The review return channel

**A note is anchored to a specific element, and the anchor is computed in the browser.**
`computeAnchor` in `library/dashboard/static/components/review-channel.js` measures a CSS path plus the element's tag and visible text; `resolveAnchor` walks it back to a live element after a view re-renders, path first and tag+text second.
The server stores what the browser measured and never computes one.
A note whose `anchor.selector` is empty is REJECTED, never degraded to a page comment. [why](docs/RULE_EVIDENCE.md#notes-are-anchored-not-page-comments)

**One send carries the whole queue and wakes an agent, which replies onto the same surface.**
`library/dashboard/review_channel.py` is the store and the agent side; `/api/review/*` in `server.py` is the browser's half.
The agent parks on `wait_for_batch` (or `GET /api/review/poll`) and is released the moment the reviewer sends.
A reply always lands on NOTES - named ones, or every note in the batch - so the answer appears under the note that prompted it.
A batch stays pending until all of its notes are answered.

    python3 -m library.dashboard.review_channel poll  --project <dir>
    python3 -m library.dashboard.review_channel reply --project <dir> \
        --batch <id> [--note <id>] --text "what you did"

Notes live per project in `pipeline_output/review/channel.json`, each recording the view it was written on.

### Run control

Start, Handbrake, Resume and Step launch `run_pipeline.py` as a child process.

- Start uses `--full-auto agy` and does NOT pass `--review`; gates are an opt-in tick box. Step is `--step <id>`, with the id resolved server-side to the first topologically-unrun step. [why](docs/RULE_EVIDENCE.md#runs-are-driven-from-the-page)
- **The handbrake is a file, not a signal.** `library/tools/run_control.py` owns the whole vocabulary - `pipeline.hold`, `pipeline.pid`, `pipeline_run.json` at the project root - and both processes speak only through it.
- The runner reads the hold at the TOP OF EACH STEP, so the step in flight finishes and writes its state first. Never kill mid-step. [why](docs/RULE_EVIDENCE.md#the-handbrake-is-a-file)
- `pipeline_run.json` is the runner's own account of itself (mode, current step, how it ended). Nothing else may write it, and the dashboard reports run state from that file only.
- Launch with `sys.executable`, never a bare `python3`.
- Child output goes to `pipeline_output/logs/run_*.log`, and `/api/pipeline/run` waits briefly and fails the request if the runner already died.
- Tests: `tests/test_dashboard_run_control.py`.

## 5. DaVinci Resolve integration - CRITICAL RULES

These constraints are hard-won and must be followed exactly when scripting DaVinci Resolve.

### Connection

Resolve must be running with a project open before executing Resolve-dependent steps.

```python
import sys, os
sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")
```

If `resolve` is `None`, Resolve is not running or not fully loaded - retry with a delay.

### Process isolation

- Never create a timeline and use `ImportFusionComp` in the same Python process. Clip references go stale after timeline creation, so import comps in a separate script or process.
- One `ImportFusionComp` returns a Composition object, not a boolean. Verify with `clip.GetFusionCompNameList()`.
- Tool loading is lazy: `GetToolList()` may show 0 tools immediately after import. For reliable bulk comp building use `comp.AddTool()` on the Fusion page to bypass lazy loading.

### Judge every Resolve call by what it returns

- **`hasattr` is always True on Resolve's scripting proxies, including invented names.** Guard on return values, never on `hasattr`. [why](docs/RULE_EVIDENCE.md#hasattr-is-always-true)
- Discarding a return value and printing success is not evidence the call did anything. Say so when Resolve declines; the neural-directive block in `resolve_build_timeline` is the pattern to copy. [why](docs/RULE_EVIDENCE.md#smart-reframe-reported-success-for-months)
- Read the truth off `TimelineItem.GetProperty()` with no argument, which returns the whole dict, before trusting any property name.
- **`Pan` and `Tilt` are the transform properties. There is no `PanX` and no `PanY`.** `ZoomX`/`ZoomY` are real. [why](docs/RULE_EVIDENCE.md#pan-tilt-and-volume)
- **The scripting API cannot set an audio level, and that is a COMPLETE enumeration.** An audio `TimelineItem` has no property dictionary at all, so every spelling of `SetProperty` returns False; the whole documented audio surface is `GetFairlightPresets`, `ApplyFairlightPresetToCurrentTimeline` and `InsertAudioToCurrentTrackAtPlayhead`, and Fusion's `ActionManager` registers no audio action. Do not re-probe it - see "The mix goes through OTIO" below. [why](docs/RULE_EVIDENCE.md#pan-tilt-and-volume)
- `TimelineItem.Stabilize()` works, and is the most expensive call in a build - see below.
- `CreateMagicMask` is withdrawn: it returns False for every mode.
- Super Scale is a **MediaPoolItem** property taking an **int**, with companion keys `SuperScale Sharpness`/`SuperScale Noise Reduction` (no space after Super). [why](docs/RULE_EVIDENCE.md#hasattr-is-always-true)

### Transitions go through Fusion. Both other routes are closed.

The Python timeline builder plus Fusion IS the architecture, not a workaround.
**Do not wire FCPXML or DRP project-file surgery back in.** [why](docs/RULE_EVIDENCE.md#fcpxml-and-drp-are-closed)

- Every transition type the pipeline may plan lives in ONE enumeration, `library/tools/transition_vocabulary.py`, with a recorded reason for each withdrawn type.
- **A cut the plan did not decorate is a hard cut.** `transition_selector` never invents a DRAWN transition; `WITHDRAWN_SCENE_CHANGE_DEFAULTS` records the three it used to. A brand template's allow-list is a permission, not an instruction.
- `tests/test_transition_vocabulary.py` checks the handoff toolkit, the brand templates, the registry fallback and the renderer's dispatch against it. A type advertised anywhere else fails CI.
- Adding a transition means adding a builder to `library/tools/fusion/effects.py` first.
- No per-clip Fusion comp can mix two clips, so there is no cross dissolve or wipe on this route.

### Stabilization is the memory ceiling, and it runs last

`neural_engine_directives` is applied AFTER every clip, comp, overlay and SFX is placed, so a build that dies inside it loses ALL of them. [why](docs/RULE_EVIDENCE.md#stabilization-oom-87gb)

- Treat it as the memory ceiling of the whole pipeline and do not run other heavy jobs beside it.
- It changes picture steadiness and nothing else - never structure, timing, framing, grade, captions or sound.
- For a timeline meant to be scrubbed rather than shipped, pop `neural_engine_directives` off the **in-memory** manifest before `build_timeline` and leave the file on disk carrying it.
  `PIPELINE_SKIP_STABILIZATION=1` is that pop, in step 6.01, opt-in and off by default: the plan keeps its directives and only that run declines them. A render made with it is UNSTABILIZED - say so when reporting one. [why](docs/RULE_EVIDENCE.md#stabilization-oom-87gb)

### Fusion .comp files - NEVER

- Never use `ApplyMode` in a Merge node: it crashes Resolve with a SIGSEGV.
- Never use `Path {}` when a Merge node exists in the same comp: it causes black output.
- Never use `BlendClone`; it is silently ignored. Use `Blend`.
- Never use `Tools = ordered() {`; it fails. Use `Tools = {`.
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
- **Size every Background node to the SOURCE clip's own resolution, never to the delivery format.** Read it off the MediaPoolItem's `Resolution` and do NOT swap it for rotation - Fusion gets the stored frame. [why](docs/RULE_EVIDENCE.md#background-sized-to-the-delivery-frame)

### Frame mapping

Fusion compositions operate on the source clip's full frame range, not the timeline's trimmed duration.

- Use the SOURCE clip frame count for `clip_dur` (the comp's GlobalIn/GlobalOut range), read as `int(mpi.GetClipProperty('Frames'))`, not `clip.GetDuration()`.
- **Keyframes must land within the PLAYED window** (`source_in_frame..source_out_frame` in source frame numbers), not across the full source. [why](docs/RULE_EVIDENCE.md#keyframes-outside-the-played-window)

### Default transition values

- Brightness Flash: `Brightness = 0.67`, `Saturation = 1.83`, animate `Blend` 0-1 with Sine easing.
- Crash Zoom: Transform `Scale = 0.4`, `Offset = 0.6`, range 0.6-1.0, Quad easing, mirrored.
- Glow: `SoftGlow.Gain = 5.0`, `SoftGlow.XGlowSize = 100`, linear easing.
- Default easing uses `LUTLookup` driven by the system `Transition` variable for Edit page transitions.
- For per-clip Fusion comps, replicate easing with `BezierSpline.sampled()` pre-baked keyframes.

### Tracks

**Per-clip Fusion comps reach V1 AND V2.**
One enumeration, `library/tools/execution/fusion_tracks.py`.

- `compile_manifest` merges the house look onto both. Any pass that draws it must read both. [why](docs/RULE_EVIDENCE.md#house-look-missed-the-broll)
- Transitions stay on V1: `after_clip` indexes the V1 clip LIST, so replaying it elsewhere draws a transition at an unrelated cut.
- Drop detection in `build_verification` asks whether a label was PLACED, not whether it is on V1.
- `tests/test_house_look_reaches_broll.py` drives the real pass against a fake Resolve.

**Track V2 is for additive overlays only** and its Fusion comps cannot read V1 video content.

- Use V2 for dip-to-black, colour washes, letterbox bars and particle effects.
- Do NOT use V2 for flash, blur or zoom: they require processing the underlying video on V1.
- Adjustment Clips cannot go on V2 - `InsertGeneratorIntoTimeline` always targets V1.
- Import one `transparent_1080x1920_30fps.mov` and reuse it via `AppendToTimeline` with `clipInfo` targeting `trackIndex: 2`.
- `SetProperty('CompositeMode', n)` sets composite modes (0 Normal, 5 Screen). `timeline.SetTrackEnable('video', 2, False/True)` toggles V2.

**A3 is a logical SFX bucket, not one lane.**
Overlapping SFX are fine - the timeline builder allocates A3, A4, ... - but identical SFX positions are not.

### Media pool and audio

- **Prefix overlay filenames with their context**, such as `sub_craig_seg_000.mov`. Generic names like `seg_000.mov` collide in the media pool and basename lookups silently pick the wrong clip. Look up by filepath first with `GetClipProperty("File Path")` before falling back to basename. [why](docs/RULE_EVIDENCE.md#media-pool-name-collisions)
- **Place V1 clips while only track A1 exists**, or the timeline floods with empty tracks: iPhone MOVs contain multiple audio streams. Add A2 and later tracks afterward, and place music or SFX with `mediaType: 2`.
- **Resolve audio pool items report 24fps regardless of the timeline.** `AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase, so compute audio in/out with the pool item's own FPS. [why](docs/RULE_EVIDENCE.md#audio-pool-items-report-24fps)
- **Renders are silent unless you say otherwise.** `SetRenderSettings` must set `ExportAudio`/`AudioCodec` explicitly; `resolve_render.py` also probes the output for an audio stream before reporting success. [why](docs/RULE_EVIDENCE.md#renders-are-silent-by-default)

### The mix goes through OTIO, and it goes in at placement time

Every planned dB - the bed's per-block curve and each clip's `volume_db` - reaches Fairlight
by ONE route: `library/tools/otio_mix.py` writes it into an OpenTimelineIO export and
`library/tools/execution/deliver_audio_mix.py` imports the result back.
Resolve's OTIO carries clip volume in plain JSON, **in dB**, with keyframes.
[why - the measured renders, and the routes that were rejected](docs/RULE_EVIDENCE.md#the-mix-goes-through-otio)

- **The import REBUILDS the timeline.** Fusion comps and CDL grades do NOT survive it; clip
  placement, transform (`_apply_conform`), timeline markers and native transitions do. So the
  round trip runs straight after the last clip is placed, before the Fusion pass and the
  grade, and nothing may hold a `TimelineItem` from before it.
  `tests/test_audio_mix_delivery.py` drives a whole build and asserts the comps are still there.
- **The `volume` parameter is ABSENT from an untouched export**: Resolve writes
  `"Parameters": []` when every value is at its default, so it must be INSERTED, not patched.
- **A keyframe's frame number is measured from the CLIP'S START ON THE TIMELINE**, not from the
  start of the timeline and not from the source in-point.
- **`ImportTimelineFromFile` answers None with no diagnostic** when a referenced media file is
  missing, when the path is relative, or when the timeline name is taken. Check the first,
  pass an absolute path, and rename the placement timeline out of the way.
- A cyan `UNAPPLIED target` marker is the FALLBACK, written only when the route declines and
  saying so. It is a note asking a human to set a level, never a level.
- The master limiter stays a marker in every case: it is a master BUS setting, and no
  clip-level route reaches it.
- DRT blob surgery reaches the same data and preserves Fusion comps, but only a static gain is
  demonstrated on it and it rests on an undocumented binary layout. FCP7 XML is rejected: its
  round trip costs a constant 3.1 dB tax on everything.

### Visual verification

Render single frames via the Deliver page to verify effects look correct.
Under 2KB means a broken or black frame; 30-150KB means real video content.

### Reading a killed build off disk

Resolve is the captain's application and a crashed build is not licence to start it.
What reached disk is readable on its own, as plain SQLite.
**Copy the project database before opening it; never open it in place.**
[why - paths, the table join, and what it cannot answer](docs/RULE_EVIDENCE.md#reading-a-killed-build)

### Markers and timeline items

Use `timeline.AddMarker()` and `timeline.GetItemListInTrack()`, following the patterns in `timeline_item_markers` and related tools.

## 6. The spine contract

`mesh_spine` (step 2.05) emits the timeline spine every creative step reads.
Its shape is defined and enforced in `library/tools/spine_contract.py`, which mesh_spine calls before emitting.

- Every block carries `clip_id`, `source_start`, `source_end`, `word_timestamps` and `alignment_method`. `None`/empty only for non-speech blocks.
- **Read those keys directly** (`block["clip_id"]`). Do NOT reintroduce `.get("clip_id", "")` fallback chains: a missing key is a contract violation and must raise. [why](docs/RULE_EVIDENCE.md#get-clip-id-disabled-beat-alignment)
- Word timings use `source_start`/`source_end`, not `start`/`end`.
- `speech_sequence` (2.02) treats the LLM's `source_start`/`source_end` as a LOOKUP HINT only. Real timings come from aligning the passage text against WhisperX words, and a passage that cannot be aligned fails the step. This is what stops invented round-number ranges reaching the timeline.
- **Two body passages cut from one clip may not claim overlapping source ranges.** 2.02 re-anchors such a passage past the previous one or fails it; `manifest_validator` asserts the same on consecutive V1 clips. The drift threshold is a secondary aid only. [why - and what is still unverified in a render](docs/RULE_EVIDENCE.md#overlapping-source-ranges-play-twice)

## 7. Data flow

Data flows through `pipeline_data.json`.
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
- A project outside `PIPELINE_PROJECTS_ROOT` is addressed by passing its absolute path in place of the slug to `run`, `status`, `info` and `dashboard`. It is referenced in place, never copied.
- Multi-project environments group projects by client folders if specified during creation.

### Where a project's files go

**One module owns the project-side layout: `library/tools/project_layout.py`.**
`paths.py` owns the REPO and the MACHINE; this owns one PROJECT, which is a folder passed in rather than a constant.

**`pipeline_output/steps/` IS the pipeline.** One directory per step, `1_04_temporal_index/` and so on, so `ls` walks it in the order it runs and a file's directory names the step that wrote it. That is the point: the captain audits by walking the folder, not by consulting an index. [why](docs/RULE_EVIDENCE.md#by-kind-was-the-wrong-axis)

- `STEPS` is the ordered table of every step, in DAG order, with the directory it owns. `AreaSpec.step` names the owning step for a step-owned area.
- Directory names use the STEP number, the same spelling `library/steps/` and every "step 1.04" citation uses. Sorting therefore diverges from run order in exactly two places - the DAG runs 2.06 before 2.05 and 5.04 before 5.03 - and `README-LAYOUT.md` renders true run order. Numbering by DAG position instead would renumber every later directory whenever a step is inserted.
- Each step directory holds `output.json` and `summary.md` (what `step_exporter` writes) plus whatever files the step produced.
- **A step writes only inside its own directory.** Pass `step=` to `write_dir`/`write_path` and another step's area raises; `assert_step_owns` is the same guard for a path from outside the layout.
- **Not everything is a step's product.** `logs/`, `gates/`, `review/`, `llm_*/`, `thumbnails/`, `backups/`, `migrations/`, `provenance/`, `scratch/`, `unsorted/` and `exports/` stay at project level. Nesting them under a step would be a lie about who wrote them.
- `exports/` is the one area TWO steps legitimately write: 6.01 the render and 6.02 the QA report. `produced_by` names both, and provenance leaves `step_id` None rather than picking one.

- Every place inside a project folder is a row in `AREAS`, keyed by `Area`. A place that is not a row does not exist, and asking for one raises.
- **A step never composes a project path.** It names an `Area` and gets a path: `write_dir`/`write_path` to write, `read_dir`/`read_path` to read, `resolve_project_relative` for a path recorded in state. All sixteen steps that used to join `project_folder` with a name of their own choosing now do this. [why](docs/RULE_EVIDENCE.md#nothing-owned-the-project-folder)
- **Inputs are structurally protected.** `raw/`, `music/`, `assets/`, `brand_assets/` and `compositions/` are `Kind.INPUT`: `write_dir`/`write_path` raise for them, `ensure()` does not create them, and `assert_writable` refuses any path underneath. Reads are unaffected.
- `assert_writable(path)` is the guard for a path that arrives from outside the layout - a manifest key, a CLI flag. Outside the project, at the bare project root, or inside an input area all raise.
- **A project explains itself.** `ensure()` renders `README-LAYOUT.md` from the same table the code reads - the steps in run order, what each reads and what each writes - and runs on `manage_project.py new` and at step 1.01 of every run.
- **`classification.per_clip_artifacts` names an AREA, not a directory**: `{area:temporal_index}/{clip_id}.json`. Spelling the path out is what left steps 1.03 and 1.07 pointing at `raw/analysis/` after the layout moved it, so `--rerun semantic_analysis:clip_007` deleted nothing. [why](docs/RULE_EVIDENCE.md#a-declaration-that-went-stale)
- The scaffold is not a second list. It drifted from the steps once, promising `pipeline_output/subtitles` while step 4.05 wrote `subtitle_segments`.
- Anything the pipeline FETCHES rather than computes - a downloaded music track - is output, and goes to `Area.ACQUIRED_MEDIA` under the step that fetched it, not into `music/`.

**Backups of `pipeline_data.json` are automatic and bounded.**
`pipeline_output/backups/pipeline_data/`, one per RUN rather than per save, newest `MAX_PIPELINE_DATA_BACKUPS` kept, pruned by the writer.

- The pruner only ever considers files matching its own naming pattern, so a hand-made backup dropped in beside them is never deleted. Pre-policy backups live in `backups/pipeline_data/legacy/`.
- One per run, not one per save, because `save_pipeline_state` runs after every step and the thing worth keeping is the state as it stood BEFORE a run. [why](docs/RULE_EVIDENCE.md#nine-hand-made-backups)

### Reading a run back

**The layout answers "which step wrote this" by where the file is. Provenance adds WHICH RUN and FROM WHAT.**
`library/tools/provenance.py` owns it.

Every record carries the METHOD that produced it, because an audit that cannot tell a measurement from a declaration is not an audit:

- `observed` - the runner listed the output tree before the step and again after, and this file appeared or changed in between. A recorded fact about a specific run. It costs the steps nothing, which is what makes it work for the ones that hand the writing to ffmpeg, Remotion or Resolve.
- `declared` - nothing was watching, but the file sits in a step's directory, or the area declares a writer. True of the pipeline as built, not of this file.
- `unknown` - neither, and it stays unknown. **Never attribute a file to the nearest plausible step.**

- The runner observes each step **after** `_export_step_for_review`, or a step's own `<step_id>.json` export is attributed to nobody.
- Records are append-only: a run is a thing that happened, and a later run overwriting a file does not unmake the record of the earlier one. Newest wins when the question is "what is this file now".
- An area declaring TWO producers (`exports/` is `render` and `validate`) leaves `step_id` None and names both. Picking the first would be an answer the pipeline does not have.
- `derived_from` is READ out of the artifact - `SOURCE_KEYS` names the keys - never inferred from a filename. A `clip_id` is not a path.
- `pipeline_output/backups/` is counted, not listed: an archived copy is not the artifact the step wrote.

**Two generated documents, regenerated on every run and by `manage_project.py trace <slug>`.**
`library/tools/run_traceback.py`. `RUN-TRACEBACK.md` is the steps in order - when, how long, what it consumed and from which step, what it produced. `ARTIFACTS.md` is the other direction: every file, with the step that wrote it and how that was established.

- Both are generated from `dag.json`, the two ledgers, `step_errors` and the provenance ledger. Never hand-written, and never edited in place.
- `trace` works on a run that already happened: the timings and verdicts are recorded fact, and file attribution falls back to `declared` - labelled, with the header saying plainly that nothing was observed. [why](docs/RULE_EVIDENCE.md#the-folder-could-not-be-read-back)
- `object_segmentation` and `ocr_extraction` declare their areas and are not in the DAG, so `ARTIFACTS.md` says nothing runs them. `unwired_step_ids` matches by `step_ref`, because the DAG calls `step_1_01_scan_project` simply `scan`.
- `tests/test_run_traceback.py`.

**A project that predates the layout is brought onto it with `manage_project.py organize <slug>`.**
`library/tools/project_migration.py`. It plans by default and changes nothing until `--apply`.

- **It never deletes.** Every action is a move or a copy, and a file whose purpose cannot be established goes to `pipeline_output/unsorted/<bucket>/` with a stated reason, never a guess. Measure what you can - `media_facts` records a file's duration, format and encoder - so an admitted unknown is an examined one.
- **It never modifies an input directory.** Pipeline output found inside one is COPIED out, so `raw/` is left byte for byte as it was found. That is also why `--revert` undoes moves and not copies: undoing a copy means deleting.
- Every run writes a manifest to `pipeline_output/migrations/` - each action with source, destination, byte count, digest and reason, plus the byte totals before and after. `manage_project.py organize <slug> --revert <manifest> --apply` reads it back.
- `tests/test_project_migration.py`.

### Replaying a step without running the pipeline

**A step's exact prompt and context can be rebuilt off frozen state, at a named revision, with no pipeline run, no Resolve and no project write.**
`library/tools/replay_bench/`, driven by `python3 -m library.tools.replay_bench`. Read [`docs/STEP_REPLAY_BENCH.md`](docs/STEP_REPLAY_BENCH.md) before changing what a step is routed: it answers "did that change what the model sees" in seconds, against a project two other workers may be holding.

- The reconstruction is the runner's OWN assembly - `gather_step_inputs`, the step's `bridge.py`, `project_fields`, `json_to_toon`, the handoff and `get_brand_constraints` - never a model of it. `reconstruct.py` therefore imports nothing from `library` at module scope: it runs as a subprocess with the TARGET tree first on `sys.path`, and a module-scope import would measure the same tree on both sides of a comparison.
- **A snapshot is captured outside the repository; only its MANIFEST is committed**, to `tests/fixtures/replay_snapshots/`. The payload is one client's transcripts and goes stale the moment a step changes what it emits; the manifest is digests, so two people can establish they hold the same bytes without shipping them.
- **`verify` is a gate, not a report.** It reconstructs every archived context and exits non-zero on any unaccounted difference: if it cannot reproduce the past it cannot be trusted to compare futures. A step that matches only after a named cause is subtracted reads `EXACT (explained)`, never as a clean pass.
- **Never use the pipeline's own token figures.** `present_llm_step` logs `len(s.split()) * 1.3` and it is 0.38x-0.54x the `o200k_base` count on 001's own contexts. The bench measures from the reconstructed string and names the tokenizer; with `tiktoken` absent the count is absent rather than estimated.
- The bench measures the pipeline and stays out of it. No step, process or dashboard module may import it - `tests/test_replay_bench.py` fails if one does.

### No test reaches a real project

**A test builds its project under `tmp_path`, or it skips. It never falls back to a real one.**
The captain's footage and renders cannot be re-shot and this machine has no Time Machine destination, so there is no undo. [why](docs/RULE_EVIDENCE.md#tests-bound-to-the-captains-project)

- `library.tools.paths.PROJECTS_ROOT` is the ONE constant naming where real projects live, and it is the enforcement point - `project_layout.py` cannot be, because a project folder is an argument it has no way to judge. **A test may not read that constant.** Patching it by string is fine.
- `tests/conftest.py` points `PIPELINE_PROJECTS_ROOT` at an empty temporary directory for the whole session, before anything under `library/` is imported. The sandbox stays EMPTY; it is not a fixture.
- `tests/test_tests_never_reach_real_projects.py` asserts the guarantee three ways: the root a test sees is the sandbox, no test source reads the constant or carries a home-absolute path, and collecting the whole suite against a populated DECOY root - sandbox deliberately off - binds nothing under it and changes not one byte of it.

## 9. Environment and dependencies

- **`run` needs the dedicated `.venv`; nothing else in the CLI does.**
  The venv is per checkout and gitignored - `python3 -m venv .venv && pip install -r requirements.txt` makes one.
  `manage_project.py` checks for `mlx_vlm`/`whisperx`/`easyocr`/`torch` only for the commands in `ML_DEPENDENT_COMMANDS`, which is `run` alone: the ML packages are imported by pipeline STEPS, and `run` launches them with `sys.executable`, so the interpreter running the CLI is the one that must carry them.
  `dashboard`, `list`, `status`, `info`, `trace`, `organize`, `archive`, `relink` and `new` reach none of them and are served without them. Never move that check back to import time. [why](docs/RULE_EVIDENCE.md#the-dashboard-could-not-be-opened)
  The refusal names the activate script if this checkout really has one, and says how to make one if it does not - the old message printed a bare `source .venv/bin/activate` into a checkout with no `.venv`.
  `tests/test_cli_ml_preflight.py`.
- Set `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` to your DaVinci Resolve installation.
- Set `HF_TOKEN` for HuggingFace models like Audio Flamingo Next.
- Set `PIPELINE_SFX_LIBRARY`, `PIPELINE_MUSIC_LIBRARY` and `PIPELINE_PROJECTS_ROOT` to absolute paths.
- Python dependencies are in `requirements.txt`. `librosa` is required by `music_analysis`; without it the step reports `available: false` and the run fails rather than continuing silently.
- External tools: `ffmpeg` and `ffprobe`. Node.js for Remotion subtitle rendering.
- GPU acceleration is required for Gemma 4, SAM 2, WhisperX and EasyOCR.
- **Every `subprocess.run` capturing text must pass `encoding="utf-8"`.** `text=True` decodes with the locale codec, and this pipeline writes UTF-8 status glyphs. [why](docs/RULE_EVIDENCE.md#text-true-decodes-with-the-locale-codec)

## 10. Cross-cutting rules

These hold across steps and cost a full audit cycle each. Do not undo them.

### 10.1 Contracts between steps

**Key-name mismatches are the dominant bug class.**
Index required keys directly so a rename fails loudly; never `.get()` a default for a key a contract promises. [why - and the known disagreements](docs/RULE_EVIDENCE.md#key-name-mismatches)
Join semantic documents to the catalog with `library/tools/semantic_index.py`: the documents are keyed by FILE STEM, the catalog by `clip_XXX`.
A per-clip summary table built without that join comes out full of `none` and says nothing; the transition planner shipped one for months. [why](docs/RULE_EVIDENCE.md#the-transition-planner-read-the-raw-document)

**A step reads the vision document through a SUMMARY its own handoff names, not through the raw document.**
A manifest that declares `semantic_analysis`/`semantic_analysis_documents` with no sub-paths gets all fifteen columns - `file_path`, `fps`, `resolution`, `vision_schema_version` and `analysis_metadata` included - and that was 113 KB of `plan_transitions`' 162 KB.
Name the columns, or - where a pre-bridge already builds the table the handoff points at, as 4.02 does with `cuts_toon` - route the document to the BRIDGE and keep it out of `context_fields` entirely.
Projection narrows the prompt and never the inputs, so the bridge, the post-bridge and `step.py` still receive the whole thing.

**`compile_manifest` reads `pipeline_data.json`, not just files.**
The per-step `*.json` files in `pipeline_output/` are a best-effort dashboard export, and a missing file reads as `{}`. [why](docs/RULE_EVIDENCE.md#compile-manifest-read-an-empty-catalog)

**Declare `interface.llm_outputs` on any hybrid step whose bridge emits a key the step also declares as an output**, or whose LLM contribution differs from the step's outputs.
`present_llm_step` builds the injected schema from `interface.outputs` minus what the bridge produced, so without the declaration the model is asked for nothing, or the QA loop demands post-bridge outputs from it and every attempt "fails". [why](docs/RULE_EVIDENCE.md#empty-llm-schema)

**One vision schema, two views.**
`vision_pipeline_v3.py` emits `scene[]`/`camera[]`/`actions[]`/`objects[]`/`assessment{}`; `library/tools/vision_schema_adapter.py` derives the historical `analysis.*`/`blocks` view from it (step 1.03 applies it on write, `semantic_index` on read), so either may be addressed.
Derive only what v3 measured. [why](docs/RULE_EVIDENCE.md#vision-schema-two-views)
A step that wants framing, stability, usable ranges or subject visibility must also list those paths in its manifest's `context_fields`, or they are deleted before the prompt.

**A project's brand template reaches the run through `state["brand_template"]`.**
`library/tools/brand_registry.py` is the whole vocabulary: `project_template_name` (the declaration, off project.yaml), `resolve_template_reference` (a NAME or a PATH to a BrandTemplate, raising on either missing) and `reference_template_name` (the name half, for `TemplateLoader`). [why](docs/RULE_EVIDENCE.md#brand-template-never-reached-the-run)
The key is spelled three ways and they are not interchangeable:

- `state["brand_template"]` is the REFERENCE string.
- `inputs["brand_template"]` is the RESOLVED TEMPLATE DICT, and reaches only a step whose manifest declares it (step 5.01 does `brand_template.get("style")`).
- `inputs["brand_style"|"brand_effect"|"brand_content"]` are the slot dicts.

Nothing is broadcast. A step gets brand data because its manifest asked.
`tests/test_brand_template_load.py`.

**A brand's CONSTRAINTS reach three planning steps, and a step has two names.**
`TemplateLoader.get_brand_constraints` gives `creative_direction` a palette and typography, `plan_transitions` the permitted transition vocabulary and `plan_vfx` a VFX intensity - as prompt text, not as an input key.
`library/tools/template_loader.BRAND_CONSTRAINT_STEPS` is that enumeration, named in the DAG's vocabulary and checked against the step table at import.

- **The DAG knows `plan_vfx`; the step's manifest and directory know `step_4_03_plan_vfx`, and no rule connects them** - `scan` is not a prefix of `scan_project`. `library/tools/project_layout.node_id_for` is the ONLY translator, and the node id is what `pipeline_data.json`, both ledgers and the runner key everything by. Code holding one vocabulary while its caller holds the other goes through that function or it silently answers nothing. [why](docs/RULE_EVIDENCE.md#the-brand-reached-no-planning-step)
- The `agy` request file records `constraints` and concatenates it into `prompt`, because in that mode the file IS the prompt.
- `tests/test_brand_constraints_reach_the_prompt.py`.

**Every LLM step declares `context_fields`, and the deterministic half loses nothing by it.**
Projection happens inside `present_llm_step`, so a hybrid's post-bridge and a `deterministic_with_llm` step's `step.py` keep receiving the unprojected inputs - only the prompt narrows. A step declaring none is handed every byte it was routed. [why](docs/RULE_EVIDENCE.md#two-steps-had-no-projection)

- A path prefixed with `-` DROPS what the paths above it selected: `"timed_spine"` then `"-timed_spine.structure.*.word_timestamps"`. Prefer it to enumerating the twenty keys you meant to keep, which stops delivering the twenty-first.
- `render` (6.01) and `validate` (6.02) are the only unprojected LLM steps and are an open captain decision; `tests/test_llm_context_routing.py` holds that exemption list.
- **A pre-bridge's own table is never projected away, and you do not have to list it.** `run_pipeline.project_step_context` - the ONE place the projection happens, called by `present_llm_step` and by the replay bench - restores any `bridge_supplied` key the allow-list dropped entirely, so a new hybrid step gets its table in the prompt for free. Listing it in `context_fields` is still allowed and is the only way to NARROW it. [why](docs/RULE_EVIDENCE.md#the-bridge-table-that-was-projected-away)

**A table the prompt names, arriving with zero rows, is reported on the run that sends it.**
`library/tools/empty_table_guard.py`, called from `present_llm_step`: it reads the serialised context and the handoff, and names every TOP-LEVEL key whose value is `[0]{...}` or `[]`, marking the ones the prompt mentions by name.
It never fails a run - an empty table can be the honest answer - and it catches the zero-row half of the family only: rows that are PRESENT but hollow go past it, which is what `cuts_toon` was. [why](docs/RULE_EVIDENCE.md#a-prompt-that-described-an-empty-table)

    python3 -m library.tools.empty_table_guard <project_folder>   # a run that already happened

- The same reader serves both, because the `llm_requests/<step>.json` archive keeps the prompt and the context exactly as the run sent them.
- **Build a pre-bridge table on a key the DAG really routes, and key its rows on the identifier the answer has to name.** `sfx_candidates_toon` was built from an `a_roll_assignments` no edge carries, so it had no rows at all - and its rows would have been `unknown` even routed, because A-roll entries are keyed `spine_block_position` and not `segment_id`.

**Word timings do not reach a prompt, and what a step cannot select by NAME it selects with a named VIEW.**
`library/tools/context_views.py` is the enumeration: a manifest may put `view:<name>` in `context_fields` and get a READING of a routed input rather than a path into it. An unknown name raises, and a view's NAME is the key it writes - which is what makes a second projection a no-op, and an `llm_only` step is projected twice on every run. [why](docs/RULE_EVIDENCE.md#the-transcript-arrived-with-every-word)

- `view:transcript` is what step 2.01 reads instead of `temporal_index.*.speech_regions`: what was said, in which clip, between which two seconds. It leaves 1,439 per-word records and 82.5% of 2.01's context behind. **Step 2.02 does NOT declare it** - its own pre-bridge builds `transcripts_toon` off the same regions and its handoff reads that table by name, so declaring both put all 110 lines in the prompt twice, in two different column orders. [why](docs/RULE_EVIDENCE.md#the-transcript-shipped-twice)
- `view:picture` is what step 2.01 reads to see a clip past its opening. `analysis.scene` is `scene[]` as prose and `scene[]` is one segment per clip, so a 188.6s clip was described by its first 18.9 seconds; the view is the vision pass's per-window `blocks`, which cover 95% of the footage. [why](docs/RULE_EVIDENCE.md#the-director-saw-the-first-nineteen-seconds)
- `view:prosody` is what step 2.01 reads instead of `prosody_analysis.profiles`. An allow-list selects by NAME and cannot tell a measurement from a record of its absence, so this selects by `library/tools/prosody_profile.profile_defect` - the same predicate step 1.05 refuses to write a hollow profile with. Real profiles pass through; the rest become ONE line saying how many measured nothing and why. **State the absence, never hide it.** [why](docs/RULE_EVIDENCE.md#seventeen-copies-of-an-error-are-not-a-measurement)
- **A view is not routing.** The step still has to declare the input the view reads.
- **The code that cuts on the timings still gets every word**, because none of it reads the prompt: `speech_sequence`'s post-bridge opens the per-clip index files, and every post-bridge and `step.py` receives the UNPROJECTED inputs.
- **A declaration of NOTHING BUT `-` paths means "everything, minus these".** That is how `render` and `validate` drop `assembly_manifest.subtitles.*.words` while staying deliberately unprojected; an allow-list written to remove one field would have become the open decision about what those two should ask for.
- `tests/test_transcript_view.py`, `tests/test_prosody_view.py`.

**Never send a summary and the structure it was rendered from.**
`analysis.scene` IS `scene[]` rendered by `vision_schema_adapter.scene_prose`, and `analysis.motion` IS `camera[]`. Five steps declared both halves of a pair. Send the prose (it is lossless here and 30-50% smaller) unless the step's handoff tells the model to read the segment bounds - 3.02's does, so 3.02 keeps the structure and drops the prose. `tests/test_context_ships_it_once.py` fails on a manifest declaring a pair. [why](docs/RULE_EVIDENCE.md#the-summary-and-its-own-source)

**No raw value list reaches a prompt.**
`music_analysis.tempo.beats`, `.tempo.downbeats` and `.energy_dynamics.energy_curve_1hz` are 274, 69 and 198 numbers, and a step routed the whole analysis must DROP them with `-` paths. Beat proximity is decided in code, off the unprojected inputs, through `library/tools/beat_grid.py` - the model is separately handed the answer as `cuts_toon`'s `beat_near_cut`. Same class as the word timings above. [why](docs/RULE_EVIDENCE.md#the-beat-grid-in-the-prompt)

**A TOON table cell is quoted with a BACKTICK, and a multi-line value under a KEY is a `|` block.**
`library/tools/toon_serializer.py`. [why](docs/RULE_EVIDENCE.md#the-apostrophe-was-doubled-in-every-prompt)

- **Columns come out in the order the DATA declares them, never sorted.** Alphabetising put `end` before `start` in five tables across four steps - the transcript reached two prompts as `clip_id,end,start,text` - and led the 18-column spine table with `alignment_method`. A spine block is stored `position, block_type, ..., content, ..., source_start, source_end`, and a projected tree carries the order of the manifest's own `context_fields`; both are deliberate statements. [why](docs/RULE_EVIDENCE.md#alphabetical-columns-put-end-before-start)
- Two content classes travel in these cells and this pipeline sends both: English prose full of apostrophes, and a `json.dumps`'d dict or list full of double quotes and backslashes. Whichever character is the quote character is the one that gets rewritten, so it is one neither class contains. `'` sent `we're` to every prompt as `we''re`, 180 times in one context; `"` would do the same to the JSON.
- **A lossless round trip is NOT the test.** Nothing downstream calls `toon_to_json` - the model reads the characters. Assert the emitted FORM.
- A nested object in a table cell is `json.dumps`'d, and that is 7-64% of every large context. **Do not "fix" it by demoting the table to indexed blocks**: measured end to end on all eleven prompts it makes them 19% BIGGER, because a table names its keys once and indexed blocks repeat them per record. A view that flattens one shape is the route that measures better, and the large savings are in what a step READS, not how it is written. [why - both routes measured](docs/RULE_EVIDENCE.md#embedded-json-is-where-the-content-is)
- Escaping a newline is right in a CELL, where a row is a line. Under a KEY it is not: the creative brief is 47,903 bytes of markdown and arrived as one line carrying 700-odd literal `\n`.

**A call with nothing to ask is not made.**
When every key a step declares has already been produced - by its own `step.py`, or by its pre-bridge - the schema is empty and `present_llm_step` returns `{}` without calling. Declare `interface.llm_outputs` to ask anyway. [why](docs/RULE_EVIDENCE.md#thirty-three-thousand-tokens-for-three-bytes)

**The delivery format is a property of the PRODUCT, not of the footage.**
One enumeration, `library/tools/delivery_format.py`: a brand template declares `delivery_format`, a project may override with `pipeline.delivery_format`, the default is vertical 1080x1920, and an unknown name raises.
Every consumer calls `resolve_delivery_format(project_folder)`; nothing carries the value as a key.
The catalog's `source_resolution` DESCRIBES the footage and is never a render target. [why](docs/RULE_EVIDENCE.md#delivery-format-is-not-the-source-resolution)

**The beat grid is `tempo.beats` / `tempo.downbeats`, and it does not start at zero.**
Read it through `library/tools/beat_grid.py` and never synthesise `[i * 60/bpm ...]`.
The times are in the MUSIC file's clock and are used as timeline times, which holds only while music is placed at `source_in` 0 - `compile_manifest` asserts that. [why](docs/RULE_EVIDENCE.md#the-beat-grid-does-not-start-at-zero)

**`target_energy` has ONE reading: `library/tools/energy_reading.py`.**
"Building" names a TRAJECTORY, not a level, and is not "high".
`WITHDRAWN_HIGH_WORDS` records why "dynamic" and "fast" are out too; widening the high bucket is a decision, not drift. [why](docs/RULE_EVIDENCE.md#building-is-not-high)

**`music_behavior` has ONE vocabulary: `library/tools/music_behavior.py`.**
Five words - `prominent`, `background`, `fade_in`, `fade_out`, `silent` - and `silent` is one of them, because a planned silence is a decision.
`mesh_spine` declares it, `spine_contract` rejects a word outside it, `audio_mix` turns it into the dB, `compile_manifest` CARRIES it onto `_spine_blocks` rather than recomputing it, and `render_qa` judges the render against it.
Resolve a block that declares none through `resolve_music_behavior`, never with a local default: `WITHDRAWN_BEHAVIORS` records why the two-word `full`/`ducked` form is out. [why](docs/RULE_EVIDENCE.md#silence-lost-in-the-two-word-vocabulary)
`tests/test_music_behavior_vocabulary.py`.

**The timeline's length comes from the spine, never from a passage's `end_time`.**
`library/tools/timeline_duration.measure_timeline_duration`: max `timeline_end` over the spine, falling back to `a_roll_assignments`.
`0.0` means "no evidence" - say so rather than warn about a length nothing measured. [why](docs/RULE_EVIDENCE.md#end-time-is-a-source-timestamp)

### 10.2 Reaching the picture and the sound

**A capability is only real where the renderer reads it.**
The renderer dispatches on parameter NAMES (`library/tools/execution/apply_fusion_comps.build_effect_comp`), so a planner emitting a name nothing reads produces a comp without that effect and no warning. [why](docs/RULE_EVIDENCE.md#unread-parameter-names)

- When you add a knob, add it to `build_effect_comp` in the same commit and assert it draws nodes (`tests/test_vfx_delivery.py`).
- When a design node cannot be delivered, record the reason where the design lives. Withdrawal is a legitimate outcome; a silent unread key is not.
- Every TOP-LEVEL manifest key is held to this by `tests/test_manifest_readers.py`: name a reader that really contains `manifest[key]`, plus one sentence saying what that reader does to the picture or the sound - or put it in `EXEMPTED_KEYS` with a reason.
- `docs/PIPELINE_PLAN.md` is the standing audit of which manifest keys have a reader. Check it before assuming a stage's output reaches the picture, and update it when you wire or withdraw one.

**An overlay that draws nothing is not rendered.**
`generate_motion_props.props_draw_ink` is the predicate; keep it in step with the MotionGraphics composition.
The output carries NO `available` key when nothing draws, because `available: false` anywhere fails the run. [why](docs/RULE_EVIDENCE.md#overlays-that-draw-nothing)

**Manifest validation has a semantic half.**
`library/tools/manifest_validator.py` asserts distinct cut points, distributed SFX, distinct VFX ranges, B-roll differing from the A-roll it covers, no overlay overlaps, no repeated source audio across consecutive V1 clips, no zero-duration clips and no fabricated round-number source ranges.
It does NOT check ducking curves.
Regression fixtures live in `tests/fixtures/captured_run/` and come from a real broken run - never replace them with empty-list fixtures. [why](docs/RULE_EVIDENCE.md#manifest-validator-semantic-half)

**Every frame of the timeline must show a clip.**
`compile_manifest._assert_timeline_fully_covered` fails on any stretch of V1+V2 with nothing on it.
The one exception is a hole the plan deliberately declared, via the optional `intentional_black_beat`/`black_beat_reason` spine keys documented in `library/tools/spine_contract.py`; an undeclared hole always fails.
Step 6.02 honours the same declaration by passing `declared_black_beat_ranges` into `render_qa`, and both gates bound a beat by `MAX_DECLARED_BLACK_BEAT_SECONDS` from the spine contract - keep that bound in one place. [why](docs/RULE_EVIDENCE.md#undeclared-black)

**Overlay geometry comes from `library/tools/safe_area.py`, and captions are grouped by measured pixels.**
One enumeration keyed by delivery format, insets stored as FRACTIONS so a 4K vertical or a small test frame needs no second row; an unknown format raises.
Four consumers read it: `subtitle_style.SubtitleStyle.resolve` (the `safeArea`/`captionMaxWidth` props), `generate_motion_props`, `timed_text_overlay` (which refuses a card centred in the platform's UI band) and `plan_subtitles`' grouper. [why - the profile, and the grouper that never ran](docs/RULE_EVIDENCE.md#safe-area-and-the-caption-grouper)

- The subtitle style is resolved at the top of `generate_subtitles` and there is no blind path: `split_into_groups` raises without a `fits_fn`.
- **A card fits the BOX, not one line.** The overlay wraps (`flexWrap`), so `fits_in_box`/`MAX_CAPTION_LINES` is the test; grouping against one line halves the words on every card and therefore halves how long each is on screen. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)
- **The split is BALANCED, not greedy.** A greedy fill leaves the remainder as a runt card, and a card is on screen only until the NEXT card's first word, so nothing downstream can lengthen one. `split_into_groups` solves per block for the partition with the fewest cards under the floor. Model the REAL display duration if you touch it - the extension the per-block pass applies, and the block end the last card is clamped to.
- The last card of a block leaves when the block does, so it can be short with no partition able to fix it. `manifest_validator`'s P6 reports exactly that case and fails every other one.
- Set the weight axis when measuring a VARIABLE font.
- A card with one over-wide word carries `fit_scale` and the render draws THAT CARD smaller; the style's font size is untouched.
- The Remotion studio's `defaultProps` get the insets from `src/safeArea.generated.ts`, projected out of the enumeration by `scripts/generate_safe_area_defaults.py`.
- `tests/test_caption_safe_area.py`.

### 10.3 Measuring the footage

**Subject position comes from `face_center_x`, not from the vision pass.**
`vision_pipeline_v3` measures shot size, identity and time ranges - never a position - and `object_segmentation`/`ocr_extraction` produce boxes but are not in the DAG.
`step_1_04_temporal_index.compute_face_presence` emits the horizontal centre of the largest detected face at 5Hz; `library/tools/subject_framing.py` reduces it per clip and returns a POSITION; `compile_manifest._conform_fields` owns the one copy of the geometry that turns it into a pan.

- `subject_framing` returns None whenever the footage cannot support an answer. **None means "frame centred" - do not replace it with a fabricated 0.5.**
- The join is `subject_centers_by_clip`. Step 1.04's real output shape is `{"temporal_event_indices": [...], "full_indices": [...]}` - a LIST of per-clip dicts, not a mapping. [why](docs/RULE_EVIDENCE.md#subject-centers-by-clip-read-only-a-mapping)

**A crop must be wide enough for the subject, and aiming it is not enough.**
`compute_face_presence` records `face_width` beside `face_center_x`; `subject_framing.subject_box` reduces both, and `SUBJECT_HEADROOM` is how much clear space the subject needs on each side.
Filling a portrait frame from landscape source keeps exactly `1 / fill_zoom` of the source WIDTH, and a subject wider than that is cut by the frame edge wherever the pan points. [why - 001's measured frame](docs/RULE_EVIDENCE.md#the-crop-was-narrower-than-the-face)

- **No zoom both fills the frame and holds an over-wide subject** - every zoom below fill leaves bars - so `_conform_fields` SYNTHESISES the missing picture. It shrinks the source until the subject fits and puts the same frame again, scaled to cover and blurred, behind it: the `framing_backdrop` route, drawn by `fx.subject_backdrop` and dispatched on `backdrop_picture_scale`.
- The Fusion comp composes into the 9:16 column Resolve's own transform then crops at FULL fill zoom, so a backdrop clip carries **no `framing_pan_x`**: a pan slides the composition out of the frame.
- **The verdict is carried by the PLAN check**, `manifest_validator`'s P8. `render_qa.measure_face_intact` is the render-side backstop and is weaker on purpose: a face cropped hard enough stops being detectable at all, which is why 001's own master yielded nine detections in 118 samples and none touching an edge.
- An explicit `framing_pan_x` still outranks the measurement, and the clip then keeps its crop with `subject_safe_zoom` recorded so P8 can say what that cost.
- `tests/test_subject_survives_the_conform.py`.

**Face frames are sampled at the CLIP'S OWN aspect, never a fixed shape.**
`face_sample_dimensions` reads the DISPLAY shape (rotation side data applied, because autorotate runs before the filter chain) and bounds the SHORT side to `FACE_SAMPLE_SHORT_SIDE`.
It raises rather than falling back to a shape.
Anything derived from the sample size - `frame_area`, the `face_center_x` divisor - must read that size, not a literal. [why - the numbers, and what the fix does not fix](docs/RULE_EVIDENCE.md#the-squashed-face-frame)

**The frame FILLS by default, and there is no heuristic.**
One enumeration, `library/tools/framing_intent.py`: 0.0 letterboxes, 1.0 fills.
The number comes from the spine block > the project's `pipeline.framing_intent` > the template's `style.framing_intent` > `DEFAULT_FRAMING_INTENT` (1.0).
A malformed declaration raises, and `_conform_fields` has ONE branch. [why](docs/RULE_EVIDENCE.md#the-letterbox-default)
`tests/test_framing_intent.py`.

**A file on disk is not a measurement.**
Judge a step by what it MEASURED.
`speech_advanced_pipeline` raises `ProsodyUnavailable` and writes nothing rather than recording an error as a result; step 1.05 rejects a hollow profile and reports `available: false`, which `check_output_is_real` reads as a failed step. [why](docs/RULE_EVIDENCE.md#hollow-prosody-files-cached)

**Never invoke `step_1_03_semantic_analysis/step.py` against a real project to test it.**
Exercise the collection half with an analysis dir of copied profiles and `raw_footage_files: []`. [why](docs/RULE_EVIDENCE.md#semantic-analysis-triggers-a-vision-run)

**`usable_ranges` is a measurement, and an absent one is EMPTY - never the whole clip.**
`[[0, duration]]` beside `usable_ranges_method: "unmeasured"` is one field contradicting the three next to it, and the B-roll selector reads it to decide which 2.5 seconds of a clip to cut. [why](docs/RULE_EVIDENCE.md#usable-ranges-were-the-whole-clip)

- `[]` with method `unmeasured` means nobody looked; `[]` with method `deterministic_v1` means the clip was measured and none of it is usable. `usable_ranges_summary` renders the two differently and neither as a blank cell.
- The signals that measured it are named in `usable_ranges_signals`. `library/tools/analysis/picture_quality.py` is the one that needs only the video file, so it is the one that works on a first run - 1.03 runs BEFORE 1.04, so the temporal-index rules have nothing to read until a re-run.
- It samples at 5 Hz and reports runs of 0.6s or longer. **State that bound when you report a verdict**: shorter soft windows can fall between samples, and it cannot tell motion blur from a missed focus.
- `camera_stability` stays `unknown` when nothing measured it. Do not fill it in to match.

### 10.4 Gates, and what counts as evidence

**A gate that cannot fail is worse than no gate, because it reads as coverage.**
If you cannot make it read real state, delete it. [why](docs/RULE_EVIDENCE.md#gates-that-cannot-fail)

**There is no passage engagement score, and a reader says so rather than comparing zeros.**
One enumeration, `library/tools/passage_engagement.py`: `engagement_of` returns the composite a passage carries or **None**, and `WITHDRAWN_SCORERS` records why each of the three scorers was not a measurement.
Never coerce that None to 0 - a passage nobody measured is not a passage that scored nothing, and `creative_cohesion` compared exactly that way. [why](docs/RULE_EVIDENCE.md#every-line-scored-the-same)
A step that MEASURES or JUDGES engagement and writes it onto the passage needs no change to the reader.
`tests/test_passage_engagement.py`.

**A gate that FAILS correct output is no more coverage than one that cannot fail.**
If you add a model-judged gate, give it a deterministic half that can carry the verdict, and record the model's opinion rather than enforcing it. [why](docs/RULE_EVIDENCE.md#gates-that-fail-correct-output)

**Six baseline-craft properties are checked on every build, and two of them deliberately do not fail.**
A seventh, a face cut by the frame edge, is measured on the render and gates through the same `framing` check (§10.3).

`render_qa.py` measures the RENDER:

- the picture fills the delivery frame and keeps ONE geometry (`measure_frame_occupancy`);
  **a letterbox bar is FLAT and CONTIGUOUS FROM AN EDGE, and darkness alone does not make one.**
  A row joins a bar only while it is dark, has near-zero variance along itself and matches the
  row before it; the walk runs inward from the top and bottom boundaries and stops at the first
  row that is picture. Reading every dark row as bar cannot tell a night shot from a black bar
  and failed a correctly-framed master. [why](docs/RULE_EVIDENCE.md#a-dim-shot-is-not-a-letterbox-bar)
- colour exists somewhere in the frame (`measure_chroma_presence`);
- speech sits above the bed (`measure_speech_above_bed`);
- the master is deliverable without clipping (`measure_lufs`, whose true-peak half sets `passed = False`).

`manifest_validator.py` checks the PLAN: no caption card under 0.5s, and no effect family covering 100% of eligible items with two or fewer parameter sets.

- P6 exempts the one card no grouping can lengthen - the last in its block, ending where the block does - and reports it. [why](docs/RULE_EVIDENCE.md#the-caption-box-is-not-one-line)
- P7 judges DRAWN effects only. `transition_vocabulary.CUT_TYPES` draw nothing, so an edit of nothing but hard cuts is the absence of decoration, and its denominator is the transitions the plan wrote, not `len(v1_clips) - 1`. [why](docs/RULE_EVIDENCE.md#hard-cuts-are-not-an-effect-on-everything)

Chroma and the mix REPORT A NUMBER and pass.
Promoting either is ONE boolean (`CHROMA_PRESENCE_GATES`, `SPEECH_ABOVE_BED_GATES`); do not turn them into gates by another route. [why - including why frame-mean saturation is not the statistic](docs/RULE_EVIDENCE.md#baseline-craft-properties)

`SPEECH_ABOVE_BED_GATES` stays False on the captain's own condition (#183): the full run of 001 on 2026-08-26 did NOT pass it cleanly.
The mix reaches the file - the planned silence measures 32 dB below the bed - but `background` means -18 dB of CLIP GAIN while the check reads it as SEPARATION, and 001's music is mastered 8.6 dB hotter than its speech, so the target is unreachable by any mix setting.
Do not flip the boolean without changing one of the two. [why - the per-window numbers](docs/RULE_EVIDENCE.md#the-mix-target-is-not-a-separation)

The occupancy gate needs to know what the picture was SUPPOSED to look like, so `compile_manifest._conform_fields` records the resolved `framing_intent` on every clip.
A declared letterbox is exempt from the fill floor and never from the consistency half.
`tests/test_baseline_craft_properties.py`.

### 10.5 Creative latitude

**The pipeline never invents a creative judgement on the model's behalf.**
A CREATIVE fallback substitutes taste - a mood, a theme, a transition, an effect, a sound, an energy word, a pace chosen for feel - and it goes. A MECHANICAL default is a safe technical value - a frame rate, a timeout, a codec, a retry count, a path - and it stays. Where a creative value is genuinely absent, FAIL or REPORT PLAINLY; a silently-defaulted mood ships and a stopped run does not. [why - the full audit, and what was left](docs/RULE_EVIDENCE.md#the-pipeline-invented-taste-where-no-step-ran)

- Two things are NOT taste, and are the reason the rule is workable. A value meaning "nothing is drawn" - `transition_vocabulary.CUT_TYPES`, `house_look.NEUTRAL_CDL` - is the absence of decoration, not a choice of it. And a rule acting on a value the creative direction really DECLARED is not a fallback: `creative_cohesion` may judge a transition against a declared "high", but may not invent the word first.
- A plan entry that names no effect, no sound, no intensity or no level is DROPPED with the reason. Never completed from a constant, in a bridge or in `compile_manifest`.
- An alias may RENAME a capability and may not CHOOSE one. `push_in` -> `zoom_emphasis` is a fact; `slow_zoom` -> `slow_zoom_in` answered "which way?" for the planner and is withdrawn.
- Dead code that states taste is removed, not left. Step 2.01's `step.py` produced a fixed `target_mood`/`energy_arc` and could never run - the manifest declares the step pure LLM.

**There are NO creative floors, and there must not be again.**
The creative direction decides how many cutaways and how many sounds a piece gets, and the accepted consequence is that a thin edit is no longer caught mechanically. [why](docs/RULE_EVIDENCE.md#no-creative-floors)

- **A floor in the PROMPT is a floor.** `tests/test_no_creative_floors.py` guards every creative-planning prompt (`CREATIVE_PLANNING_STEPS`). Add a planning step, add it there.
- **A floor in a BRIDGE is a floor, and that is where the last one hid.** The VFX post-bridge padded the plan up to every eligible block and failed the step when the plan was empty, and survived the ruling by living in code rather than in a prompt. The same test now drives the post-bridge. [why](docs/RULE_EVIDENCE.md#the-default-that-outvoted-the-plan)
- **A floor that CUTS is still a floor.** `audio_reactive_sfx.scale_sfx_density` deleted half the plan's impacts because a constant said the piece was "moderate". Deleted, not unwired.
- **`tests/test_no_creative_floors.py` reads CODE as well as prompts.** It drives the real bridges of every step in `CREATIVE_PLANNING_STEPS` and asserts on their output. Reading only prompts is how the VFX pair survived (#192); listing only the steps the ruling named is how step 4.02's `min_trans` floor and its `defocus` injection survived longer still.
- A COVERAGE requirement is not a floor: "every non-speech block MUST have B-roll" stays, because an uncovered block fails `_assert_timeline_fully_covered`.
- `_assert_sfx_distributed` stays: it catches a collapse (every SFX on one frame), not a sparse plan.

**Music selection is one enumeration, `library/tools/music_selection_contract.py`.**
The bridge catalogues `PIPELINE_MUSIC_LIBRARY` **and** the project's `music/` and picks nothing.
The post-bridge judges source, catalogue membership, duration plausibility and a justification naming the registers the creative direction forbids.
Choosing from OUTSIDE the library is legitimate and stays allowed.

## 11. Third-Party Asset Licenses

**Anything added to `library/presets/` from an outside source needs its licence recorded here before it lands.**

**No third-party look assets ship.**
The house look is authored in this repository as CDL plus Fusion values - see §12.
`tests/test_color_grade_delivery.py` fails if any `.drx` reappears. [why](docs/RULE_EVIDENCE.md#the-unlicensed-powergrade)

**One third-party asset does ship, with its licence.**
Montserrat, as `remotion-subtitles/public/fonts/Montserrat-Variable.ttf` - a variable font covering the 100-900 weight axis, which is every weight `library/tools/subtitle_style.py` can ask for.
It is licensed under the **SIL Open Font License 1.1**, which permits redistribution including commercially and requires the licence to travel with the font; it does, as `public/fonts/OFL-Montserrat.txt`.

- **Bundle fonts; never import one over HTTP.** [why](docs/RULE_EVIDENCE.md#the-webfont-race)
- `tests/test_bundled_fonts.py` fails if the font or its licence goes missing, if a font is imported over HTTP again, or if a template names a font that is neither bundled nor explicitly accepted as a system font.

**A declared typeface must be one that really draws the glyphs.**
One enumeration, `library/tools/render_fonts.py` - bundled, accepted as a system font, or carried by the project as a `font_file` staged out of `<project>/brand_assets/` by `prep_remotion`.
Anything else raises, because Chromium substitutes its fallback sans and the frames are still valid pictures of the right size. [why](docs/RULE_EVIDENCE.md#timed-text-drew-in-the-wrong-face)

## 12. The house look

One enumeration, `library/tools/house_look.py`, holds every look a brand template may name via `style.house_look`.
An unknown name raises, and a template naming none gets exposure normalisation only.

Each look is delivered in two halves, because that is what the mechanisms can express:

- **CDL** carries hue and level - slope (highlights), offset (shadows and the black floor), power (midtones), saturation - applied by `SetCDL` in `resolve_build_timeline`.
- **Fusion** carries what a CDL has no term for - pivot contrast, glow, grain, and a shaped, optionally coloured vignette - and reaches the picture only through the parameter names `fusion/comp_builder.build_effect_comp` dispatches on (§10.2).

- Every look cites its source in `derived_from` and records design it cannot deliver in `withdrawn`. [why](docs/RULE_EVIDENCE.md#where-the-look-values-come-from)
- Add a look only with a template that names it - `tests/test_house_look.py` fails on an orphan.

## 13. Intros, outros and end cards

One enumeration, `library/tools/bookends.py`.

- A brand template declares `content.bookends`; a template that declares nothing gets nothing.
- A declaration names either an `asset` that already exists or a `composition` to render, plus a `duration_seconds`. **A malformed declaration raises rather than being dropped.** [why](docs/RULE_EVIDENCE.md#bookends-only-on-some-videos)
- **A card takes the same path as every other clip.** `mesh_spine` turns each declaration into an `intro_card`/`outro_card`/`end_card` spine block (NOT `intro`/`outro`, which already mean a non-speech pacing beat), step 4.06 renders the composition-mode ones, `compile_manifest` emits a V1 clip, and the renderer places it. That is what puts a card inside the coverage assertion, the manifest duration and render QA.
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
- Moments whose spans touch are grouped into one rendered segment, because two clips cannot share frames of a track.
- `timed_text_render.py` renders each inside step 4.06, `compile_manifest` carries them as the `timed_text_overlay` manifest key, and `resolve_build_timeline` places them on **V6**.
- The bookend route declares its artwork project-side by naming a `source:` under `content.bookends`; a line of copy has no file to point at, so timed text needs its own half. A project's `project.yaml` may carry its own `effect.timed_text_overlay`, and it replaces the brand template's whole slot - the same project-over-template precedence `delivery_format_name` uses. `timed_text_overlay.resolve_declaration` is that half, and step 4.06 calls it.
- **The card states its own `y`**, because geometry is normalised against the whole delivery frame and not the picture area inside letterbox bars. Keep `y` in 0.35..0.65 to land over picture whether the source letterboxes or fills. [why - the measured rows, and the worked example](docs/RULE_EVIDENCE.md#the-night-card-y-band)

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

When a rule comes from an incident, state the rule here and put the incident in `docs/RULE_EVIDENCE.md` with a `[why]` link.
A reader should be able to obey every rule in this file without opening that one.
