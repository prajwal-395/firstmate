# Video Editing Pipeline

You are an autonomous agent operating the video editing pipeline.
This manual explains how to navigate the repository, run the pipeline, and make editorial decisions.

## 1. Identity and purpose

This repository is the engine for an automated video editing pipeline.
It takes raw footage, analyzes it, and generates a fully assembled DaVinci Resolve timeline.
It produces shortform vertical videos with subtitles, B-roll, SFX, and music.
The engine does not store project data itself.
Project assets and pipeline outputs live in isolated directories outside the repository.
Agents and human editors use this system to automate the tedious parts of video assembly while retaining creative control.

## 2. Repo layout

The repository is structured to separate the pipeline engine from project data.

- `library/`: Core pipeline implementation and shared Python modules.
- `library/processes/`: Defines complete pipelines like `edit_video` with its `dag.json` and `manifest.json`.
- `library/steps/`: Individual pipeline steps, organized by phase (e.g., `step_1_01_scan_project`).
- `library/tools/`: Shared utilities for Resolve scripting, vision analysis, and file management.
- `library/schemas/`: Pydantic data schemas defining pipeline state and project configurations.
- `library/dashboard/`: FastAPI server for the human-in-the-loop review dashboard.
- `library/templates/`: Brand templates defining styles, effects, and content rules.
- `library/presets/`: Reusable Fusion macros and DaVinci's own built-in effect settings. Whatever reaches a timeline is found by direct path; there is no preset index.
- `remotion-subtitles/`: Node.js React application used to render subtitle overlays.
- `scripts/`: Assorted bash helper scripts for environment setup and maintenance.
- `tests/`: Unit and integration tests for the pipeline engine.
- `manage_project.py`: Top-level CLI for creating, listing, and running projects.
- `requirements.txt`: Python dependencies required by the pipeline.

## 3. Pipeline execution

The pipeline is defined as a Directed Acyclic Graph (DAG) in `library/processes/edit_video/dag.json`.
Execution order is resolved via topological sort.
The DAG groups 26 atomic steps into distinct phases: 0 for setup, PREFLIGHT for analysis (the step ids still read `step_1_0X_*`), 2 for planning, 3 for assembly, 4 for post-production, 5 for finishing/QA, and 6 for rendering.
Call that stage preflight and never "phase 1" - `docs/PIPELINE_PLAN.md` uses Phase 0/1/2 for the quality-work programme, and the two names collided.
`library/steps/` holds 28 step definitions; `object_segmentation` (1.06) and `ocr_extraction` (1.07) exist but are not wired into the DAG.

Run the pipeline using the project manager CLI: `python3 manage_project.py run <slug>`.
Use `--from <step_id>` to resume execution starting from a specific step.
Use `--step <step_id>` to run one isolated step.
Use `--auto` to auto-complete hybrid steps using bridge output instead of pausing for LLM input.
Use `--review` to enable review gates that pause the pipeline for human inspection on the dashboard.
Use `--resume` to continue the pipeline after a review gate is approved or revised.
Use `--dry-run` to print the execution plan without running steps.
Use `--rerun <target>` to redo finished work; it is repeatable and it is the ONLY supported way to re-run a completed step.

Steps come in three implementation types based on their contents.
Deterministic steps have a `step.py` and run automatically using JSON stdin/stdout.
Hybrid steps have a `bridge.py` that pre-computes context and a `handoff.md` prompt for an LLM to complete the step.
LLM-only steps have only a `handoff.md` prompt and require an LLM to generate the output from upstream context.

Pipeline state is stored in `pipeline_data.json` at the root of each project directory.
The state file tracks completed steps in TWO ledgers - see the next section - and stores all JSON outputs under `step_outputs`.
Inspect `pipeline_data.json` to debug data flow or verify upstream step results.

### Preflight and edit: two ledgers, two lifetimes

One enumeration, `library/tools/step_ledger.py`. Every step manifest
declares `classification.stage`, and an undeclared or unknown stage
raises rather than defaulting.

**preflight** is enrichment of THIS PROJECT'S SOURCE FOOTAGE - scan,
catalog, vision, transcription, prosody, segmentation, OCR - recorded in
`preflight_completed`. **edit** is everything downstream of a creative
decision, recorded in `edit_completed`. `validate_sfx_library` (0.01) is
edit: it validates a SHARED library, not this project's footage.
`music_analysis` (2.06) is edit: it enriches a CHOSEN asset, and the
choice is the thing an edit reset discards.

The two keys are separate, so `--rerun edit` is structurally incapable of
discarding enrichment - it never names the other ledger. Before this,
one flat `steps_completed` covered all 28 steps with one lifetime, `--from`
only trimmed the plan while the skip-if-finished check fired anyway, and
the only way to redo creative work was to move `pipeline_data.json` aside.
That move is what made project 001 pay for forty minutes of WhisperX twice
(`docs/RUN_001_END_TO_END.md`).

    manage_project.py run <slug> --rerun edit                     # reset the edit run
    manage_project.py run <slug> --rerun temporal_index           # one step
    manage_project.py run <slug> --rerun temporal_index:clip_007  # one clip of one step

Per-clip granularity works because the artifacts are already per clip on
disk and each step declares where its own live, in
`classification.per_clip_artifacts`. The runner deletes exactly those
files; the step's own "already on disk?" check recomputes exactly that
clip. A step that declares none is re-run whole. Add a per-clip artifact
and you must declare it, or nothing can invalidate it.

**Preflight is skipped once done, and that is safe because identity is
checked.** `library/tools/footage_identity.py` fingerprints each clip by
size plus a digest of its first and last mebibyte - not a whole-file hash
(gigabytes of IO per run) and NOT mtime (a `cp` without `-p` or a backup
tool would destroy forty minutes of WhisperX). It compares against
`source_fingerprints` in the state file, and replaced, removed or
renumbered footage invalidates exactly its own cached analysis. Clip ids
are assigned by sorted path, so ADDING a file renumbers everything after
it; that is why the fingerprint carries the path too.

Deliberately NOT built: a caching framework or a content-addressed
artifact store. One declared field, one split ledger, one re-run flag,
one identity check.

**The per-clip index lives with the PROJECT.** Step 1.04 wrote it to
`--output-dir`'s default of `./pipeline_output`, which is the runner's
CWD: project 001's state recorded its 17-clip index inside a disposable
git worktree. It now resolves from `project_folder`, and reuses any
per-clip file already there instead of re-transcribing it.

The run summary reports `SUCCESS` only when the whole DAG is complete and
`failed_steps` is empty in the project ledger - not just the steps this
invocation touched. Other statuses: `FAILED` (a step failed, or emitted an
`available: false`/hollow result, in this run or an earlier one - exit code
1), `AWAITING_LLM`, `PARTIAL` (`--step`/`--from`/a review-gate pause left
DAG steps unrun) and `DRY_RUN`. `failed_steps` is current state, not a
log: a step that later succeeds is removed from it.

## 4. Dashboard

The dashboard provides a human-in-the-loop review layer for the pipeline.
Launch the dashboard using `python3 manage_project.py dashboard <slug>`.
The dashboard serves a pipeline view, a footage library, a transcript view, and a timeline view.
Review gates pause pipeline execution to allow human inspection of step outputs.
Reviewers can approve, reject, or revise the outputs through the dashboard interface.
Rejected gates halt the pipeline entirely.
Revised gates apply the reviewer's modifications directly to the step output in `pipeline_data.json`.
The dashboard also captures annotations and feedback as structured data for agent communication.

### The review return channel

The dashboard's second job is the captain's review loop, and it has exactly
two properties (ruling 2026-08-17 on `.lavish/video-gui-findings.html`:
EXTEND THIS DASHBOARD, never author a fresh per-run review page).

**A note is anchored to a specific element, and the anchor is computed in the
browser.** `computeAnchor` in
`library/dashboard/static/components/review-channel.js` measures a CSS path
plus the element's tag and visible text; `resolveAnchor` walks it back to a
live element after a view re-renders, path first and tag+text second. The
server stores what the browser measured and never computes one - a note whose
`anchor.selector` is empty is REJECTED rather than degraded to a page comment,
because a page comment is the thing this channel exists not to be.

**One send carries the whole queue and wakes an agent, which replies onto the
same surface.** `library/dashboard/review_channel.py` is the store and the
agent side; `/api/review/*` in `server.py` is the browser's half. The agent
parks on `wait_for_batch` (or `GET /api/review/poll`) and is released the
moment the reviewer sends. A reply always lands on NOTES - named ones, or
every note in the batch - so the answer appears under the note that prompted
it and beside the element it is about. A batch stays pending until all of its
notes are answered.

    python3 -m library.dashboard.review_channel poll  --project <dir>
    python3 -m library.dashboard.review_channel reply --project <dir> \
        --batch <id> [--note <id>] --text "what you did"

Notes live per project in `pipeline_output/review/channel.json` and each
records the view it was written on. Whether the review surface should be per
RUN or per PROJECT is NOT decided - the captain has not ruled on it. Keep both
possible: per-run is a filter over this store, not a migration.

### Run control (captain's ruling, 2026-08-17: runs are driven from the page)
Start, Handbrake, Resume and Step launch `run_pipeline.py` as a child
process. Start uses `--full-auto agy` and does NOT pass `--review` -
review gates are an opt-in tick box, because forcing them turns one press
into 26 stops. Step is `--step <id>` with the id resolved server-side to
the first topologically-unrun step, so it reuses the runner rather than
adding single-step machinery.

The handbrake is a file, not a signal. `library/tools/run_control.py` owns
the whole vocabulary - `pipeline.hold`, `pipeline.pid`, `pipeline_run.json`
at the project root - and both processes speak only through it. The runner
reads the hold at the TOP OF EACH STEP, so the step in flight finishes and
writes its state first; what lands on disk is always a real step boundary.
Killing mid-step would leave `pipeline_data.json` describing a step that
half happened. `pipeline_run.json` is the runner's own account of itself
(mode, current step, how it ended) and nothing else may write it - the
dashboard reports run state from that file, never from the fact that a
launch request returned 200.

Launches use `sys.executable`, not a bare `python3`: the dashboard runs
from the pipeline's `.venv` and a PATH interpreter has none of the ML
dependencies. Child output goes to `pipeline_output/logs/run_*.log`, and
`/api/pipeline/run` waits briefly and fails the request if the runner
already died. Tests: `tests/test_dashboard_run_control.py`.

## 5. DaVinci Resolve integration - CRITICAL RULES

These constraints are hard-won knowledge and must be followed exactly when scripting DaVinci Resolve.

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
If `resolve` is `None`, Resolve is not running or not fully loaded, so retry with a delay.

### Process isolation
Never create a timeline and use `ImportFusionComp` in the same Python process.
Clip references go stale after timeline creation.
Always import comps in a separate script or process.
One `ImportFusionComp` returns a Composition object, not a boolean, so check `clip.GetFusionCompNameList()` to verify success.
Tool loading is lazy and `GetToolList()` may show 0 tools immediately after import.
For reliable bulk comp building, use `comp.AddTool()` on the Fusion page to bypass lazy loading.

### Transitions go through Fusion. Both other routes are closed.
Captain's ruling: the Python timeline builder plus Fusion IS the
architecture, not a workaround. **FCPXML** is deprecated - it does not
recognise DaVinci's effects, and Resolve's importer silently degrades
unrecognised transitions to Cross Dissolve and scrambles the audio track
layout on a round trip (both measured; see the corrected table at
`research/drp_reverse_engineering.md:98-106`). **DRP project-file surgery**
wrote to a temp file the renderer never loaded, and could not fire.
`library/tools/execution/apply_native_transitions.py` and its test remain
in the tree unused - do not wire either route back in.

Every transition type the pipeline may plan lives in ONE enumeration,
`library/tools/transition_vocabulary.py`, with a recorded reason for each
withdrawn type. `tests/test_transition_vocabulary.py` checks the handoff
toolkit, the brand templates, the registry fallback and the renderer's
dispatch against it - a type advertised anywhere else fails CI. Adding a
transition means adding a builder to `library/tools/fusion/effects.py`
first. Note that no per-clip Fusion comp can mix two clips, so there is
no cross dissolve or wipe on this route.

### Resolve API facts that cost a debugging cycle each
`hasattr` is **always True** on Resolve's scripting proxies, including
invented names - guard on return values, never on `hasattr`.
`TimelineItem.Stabilize()` works. `CreateMagicMask` returns False for
every mode, so it is withdrawn. Super Scale is a **MediaPoolItem**
property taking an **int**, with the companion keys
`SuperScale Sharpness`/`SuperScale Noise Reduction` (no space after
Super); setting it on a TimelineItem or passing `"2"` silently returns
False.

### Fusion .comp Files - NEVER DO THESE
Never use `ApplyMode` in a Merge node because it crashes Resolve with a SIGSEGV.
Never use `Path {}` when a Merge node exists in the same comp because it causes black output.
Never use `BlendClone` because it is silently ignored, use `Blend` instead.
Never use `Tools = ordered() {` because it fails, use `Tools = {` instead.
Never omit `GlobalOut` on Background nodes because it stops rendering mid-clip.
Never set DirectionalBlur Length greater than 5 because it creates artifacts and edge tiling.
Never set transition zoom greater than 1.04 because it is too aggressive and breaks immersion.

### Fusion .comp Files - ALWAYS DO THESE
Always set `Inverted = Input { Value = 1, }` on EllipseMask for vignettes.
Always include `MaskWidth`, `MaskHeight`, and `PixelAspect` on EllipseMask.
Always wire `Transform1.Input <- MediaIn1.Output` explicitly.
Always use `Blend` instead of `BlendClone` for Merge opacity.
Always include `GlobalOut` on Background nodes matching the clip duration.
Use static `Center = Input { Value = { x, y }, },` for animated pan/center.
Always use the SOURCE clip frame count for `clip_dur`, not `clip.GetDuration()`.
Use `int(mpi.GetClipProperty('Frames'))` for source frame counts.

### Fusion .comp Frame Mapping
Fusion compositions operate on the source clip's full frame range, not the timeline's trimmed duration.
A clip with 513 source frames placed as 410 frames on the timeline has a Fusion frame range of 0-512.
Using timeline duration for keyframes causes transitions to fire early and hold for the remaining frames.

### Default Transition Values
Brightness Flash uses `Brightness = 0.67`, `Saturation = 1.83`, and animates `Blend` 0-1 with Sine easing.
Crash Zoom uses Transform `Scale = 0.4`, `Offset = 0.6` with a range of 0.6-1.0, Quad easing, and mirrored.
Glow uses `SoftGlow.Gain = 5.0` and `SoftGlow.XGlowSize = 100` with linear easing.
Default easing curves use `LUTLookup` driven by the system `Transition` variable for Edit page transitions.
For per-clip Fusion comps, replicate easing with `BezierSpline.sampled()` pre-baked keyframes.

### V2 Overlay Track
Track V2 is for additive overlays only and its Fusion comps cannot read V1 video content.
Use V2 for dip-to-black, color washes, letterbox bars, and particle effects.
Do not use V2 for flash, blur, or zoom effects because they require processing the underlying video content on V1.
Adjustment Clips cannot go on V2 because `InsertGeneratorIntoTimeline` always targets V1.
Import one `transparent_1080x1920_30fps.mov` clip and reuse it via `AppendToTimeline` with `clipInfo` targeting `trackIndex: 2`.
Use `SetProperty('CompositeMode', n)` to set composite modes, where 0 is Normal and 5 is Screen.
Use `timeline.SetTrackEnable('video', 2, False/True)` to toggle V2 visibility.

### Media Pool Name Collisions
Overlay segments from different sources often share generic filenames like `seg_000.mov`.
Importing them into the same media pool causes basename lookups to silently pick the wrong clip.
Prefix filenames with their context, such as `sub_craig_seg_000.mov`.
Look up clips by filepath first using `GetClipProperty("File Path")` before falling back to basename.

### Audio Track Flooding Prevention
iPhone MOVs contain multiple audio streams.
Place V1 clips while only track A1 exists to prevent flooding the timeline with empty tracks.
Add A2 and subsequent tracks afterward, and place music or SFX with `mediaType: 2`.

### Visual Verification
Render single frames via the Deliver page to verify effects look correct.
A frame file size under 2KB indicates a broken or black frame.
A frame file size between 30-150KB indicates real video content.

### Marker and timeline item API patterns
Use `timeline.AddMarker()` and `timeline.GetItemListInTrack()` for managing timeline markers and items.
Follow the established patterns in `timeline_item_markers` and related tools for robust interaction.

## 6. The spine contract

`mesh_spine` (step 2.05) emits the timeline spine every creative step reads.
Its shape is defined and enforced in `library/tools/spine_contract.py`, which
mesh_spine calls before emitting. Every block carries `clip_id`,
`source_start`, `source_end`, `word_timestamps` and `alignment_method` -
`None`/empty only for non-speech blocks.

Read those keys directly (`block["clip_id"]`). Do NOT reintroduce
`.get("clip_id", "")` fallback chains: a missing key is a contract
violation and must raise. A guard reading `block.get("clip_id", "")` on
blocks that only had `content.clip_id` silently disabled beat-aligned
cutting for four audits.

Word timings use `source_start`/`source_end`, not `start`/`end`.

`speech_sequence` (2.02) treats the LLM's `source_start`/`source_end` as a
LOOKUP HINT only. Real timings come from aligning the passage text against
WhisperX words; a passage that cannot be aligned fails the step. This is
what stops invented round-number ranges reaching the timeline.

Two body passages cut from one clip may not claim overlapping source
ranges - the overlap plays twice across the cut. 2.02 re-anchors such a
passage past the previous one, or fails it; `manifest_validator` asserts
the same on consecutive V1 clips. The drift threshold is a secondary aid
only: the shipped case drifted 0.76s and sailed under it. NOTE: this is
fixed in code and covered by tests, but the export at the reference
project was NOT regenerated, so the mp4 on disk still repeats "to post"
at ~10.4s. It is not verified in a render.

## 7. Data flow

Data flows through the pipeline via `pipeline_data.json`.
Each step reads required upstream outputs from this file based on the DAG's `data_mapping` edges.
Each step writes its own output back to `pipeline_data.json` under `step_outputs.<step_id>`.
The `catalog` output contains video metadata, durations, and file paths.
The `semantic_analysis` output carries the v3 vision observations - scene, camera, actions, objects, assessment - plus the view derived from them; it measures no mood or energy (see "One vision schema, two views").
The `speech_sequence` output orders speech segments into a coherent narrative.
The `aroll_assignments` output maps narrative blocks to specific source clips and timeline ranges.
The `broll_selections` output assigns secondary footage (`b_roll_assignments`) and standalone cutaways (`b_roll_interjections`) to cover A-roll segments or insert visual breaks.
The `compile_manifest` output consolidates all decisions into an `assembly_manifest.json` that drives the final Resolve render.

## 8. Project management

Manage projects using the `manage_project.py` CLI tool.
Create new projects with `python3 manage_project.py new <slug> --name "Project Name"`.
Project configurations are stored in `project.yaml` within each project directory.
The `ProjectConfig` schema defines source settings, pipeline options, and Resolve bindings.
The project registry scans the root directory to list and manage all available projects.
A project outside `PIPELINE_PROJECTS_ROOT` is addressed by passing its
absolute path in place of the slug to `run`, `status`, `info` and
`dashboard` - it is referenced in place, never copied.
Multi-project environments group projects by client folders if specified during creation.

## 9. Environment and dependencies

The pipeline requires specific environment variables and dependencies to function.
**Virtual Environment**: The pipeline MUST be run from its dedicated `.venv` which contains all ML dependencies. Activate it via `source .venv/bin/activate` before running `manage_project.py`. The `manage_project.py` script enforces this via a preflight check.
Set `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` to point to your DaVinci Resolve installation.
Set `HF_TOKEN` for HuggingFace models like Audio Flamingo Next.
Set `PIPELINE_SFX_LIBRARY` to the absolute path of the sound effects library.
Set `PIPELINE_MUSIC_LIBRARY` to the absolute path of the background music library.
Set `PIPELINE_PROJECTS_ROOT` to the directory where video projects are stored.
Python dependencies are listed in `requirements.txt` and must be installed in the environment.
`librosa` is required by `music_analysis`; without it the step reports
`available: false` and the run now fails rather than continuing silently.
External tools include `ffmpeg` and `ffprobe` for media processing.
Node.js is required to run Remotion for subtitle rendering.
GPU acceleration is required for models including Gemma 4, SAM 2, WhisperX, and EasyOCR.

## 10. Hard-won pipeline lessons

These cost a full audit cycle each. Do not undo them.

**Key-name mismatches are the dominant bug class.** Steps disagree about
what a field is called, the reader `.get()`s a default, and the pipeline
reports success over empty data. B-roll assignments carry
`video_in`/`video_out` + `timeline_start`/`timeline_end`; SFX carry
`timeline_in`/`timeline_out`; semantic documents are keyed by FILE STEM
while the catalog uses `clip_XXX` (join them with
`library/tools/semantic_index.py`). When adding a consumer, index required
keys directly so a rename fails loudly.

**`compile_manifest` reads pipeline_data.json, not just files.** The
per-step `*.json` files in `pipeline_output/` are a best-effort dashboard
export; a step that ran before that export existed leaves none. It spent
entire runs compiling against an empty catalog because a missing file read
as `{}`.

**Resolve audio pool items report 24fps regardless of the timeline.**
`AppendToTimeline`'s `startFrame`/`endFrame` are in the SOURCE timebase, so
compute audio in/out with the pool item's own FPS or the music stretches to
125% and pads the export with trailing black.

**Renders are silent unless you say otherwise.** `SetRenderSettings` must
set `ExportAudio`/`AudioCodec` explicitly; `resolve_render.py` also probes
the output for an audio stream before reporting success.

**`text=True` decodes with the locale codec.** The pipeline writes UTF-8
status glyphs, so every `subprocess.run` capturing text passes
`encoding="utf-8"` - otherwise a check-mark in a child's stderr fails a
render under an ASCII locale.

**Manifest validation has a semantic half.** `library/tools/manifest_validator.py`
asserts distinct cut points, distributed SFX, distinct VFX ranges, B-roll
differing from the A-roll it covers, no overlay overlaps, no repeated
source audio across consecutive V1 clips, no zero-duration clips and no
fabricated round-number source ranges. It does NOT check ducking curves;
this entry claimed it did, and nothing in the module ever has. Regression
fixtures live in `tests/fixtures/captured_run/` and come from a real
broken run - never replace them with empty-list fixtures.

**A3 is a logical SFX bucket, not one lane.** Overlapping SFX are fine; the
timeline builder allocates A3, A4, ... Identical SFX positions are not.

**Hybrid steps declare `interface.llm_outputs`** when the LLM's contribution
differs from the step's outputs (e.g. mesh_spine's LLM writes `structure`;
the post-bridge computes `audio_spine`/`timed_spine`). Without it the QA
loop demands post-bridge outputs from the LLM and every attempt "fails".

**A capability is only real where the renderer reads it.** The renderer
dispatches on parameter NAMES
(`library/tools/execution/apply_fusion_comps.build_effect_comp`), so a
planner emitting a name nothing reads produces a comp without that effect
and no warning - `zoom_percent`, `intensity_px` and `scale_factor` killed
three of five VFX types that way, and four of the colour grade's five
nodes had no reader at all. When you add a knob, add it to
`build_effect_comp` in the same commit and assert it draws nodes
(`tests/test_vfx_delivery.py`). When a design node cannot be delivered,
record the reason where the design lives - see
`GRADE_PIPELINE_DELIVERY` in `step_5_01_color_grade/step.py` and
`WITHDRAWN` in `transition_vocabulary.py`. Withdrawal is a legitimate
outcome; a silent unread key is not. Every TOP-LEVEL manifest key is held
to this by `tests/test_manifest_readers.py`: it discovers the keys from
`compile_manifest`'s manifest literal, and each one must name a reader
that really contains `manifest[key]` PLUS one sentence saying what that
reader does to the picture or the sound - or sit in `EXEMPTED_KEYS` with
a reason. Writing the sentence is the check the AST cannot do for you.
`docs/PIPELINE_PLAN.md` is the standing audit of which manifest keys have
a reader and which do not; check it before assuming a stage's output
reaches the picture, and update it when you wire or withdraw one.

**Resolve's transform properties are `Pan` and `Tilt`.** There is no
`PanX` and no `PanY`: `SetProperty` returns False for them and reads back
None, silently. `ZoomX`/`ZoomY` are real. `Volume` on an audio
TimelineItem also returns False on Resolve 21 - its property dict is
empty - so per-clip SFX `volume_db` does not reach the mix. Read the
truth off `TimelineItem.GetProperty()` with no argument, which returns
the whole dict, before trusting any property name.

**The beat grid is `tempo.beats` / `tempo.downbeats`, and it does not
start at zero.** `music_pipeline.analyze_music` emits those under `tempo`;
there has never been a `beat_grid` key, though two steps asked for one and
got `[]`. Read it through `library/tools/beat_grid.py` and never
synthesise `[i * 60/bpm ...]` - no track's first beat lands at 0.000s, so
a synthetic grid is offset from the music by the whole lead-in. The times
are in the MUSIC file's clock and are used as timeline times, which holds
only while music is placed at `source_in` 0; `compile_manifest` asserts
that.

**Subject position comes from `face_center_x`, not from the vision pass.**
`vision_pipeline_v3` measures shot size, identity and time ranges - never
a position - and `object_segmentation`/`ocr_extraction` produce boxes but
are not in the DAG. The horizontal centre of the largest detected face is
emitted at 5Hz by `step_1_04_temporal_index.compute_face_presence` and
reduced per clip by `library/tools/subject_framing.py`, which returns a
POSITION; `compile_manifest._conform_fields` owns the one copy of the
geometry that turns it into a pan. That module returns None whenever the
footage cannot support an answer, and None means "frame centred" - do not
replace it with a fabricated 0.5. Face detection needs Haar cascades, so
`opencv-python` is pinned `<5`; OpenCV 5 removed them.

**A reader that reports success is not proof either.** The harder version
of the above: `smart_reframe` had a reader, the reader ran, and it printed
"✓ Applied Smart Reframe" on every run for months. It guarded on
`hasattr` (always True on a Resolve proxy), was handed a Timeline that
exposes no such method, and discarded the return value. Judge a Resolve
call by what it returns and say so when it declines - the neural-directive
block in `resolve_build_timeline` is the pattern to copy. The same rot
reaches QA stations: `timeline_qa.verify_fusion_comps` had `pass` as its
only loop body and reported the Fusion pass healthy whatever the timeline
held. A gate that cannot fail is worse than no gate, because it reads as
coverage; if you cannot make it read real state, delete it.

**The delivery format is a property of the PRODUCT.** One enumeration,
`library/tools/delivery_format.py`: a brand template declares
`delivery_format`, a project may override it with
`pipeline.delivery_format`, the default is vertical 1080x1920, and an
unknown name raises. The catalog's `source_resolution` DESCRIBES the
footage and is never a render target - it was, under the name
`project_resolution`, and project 001 shipped a 1920x1080 master with the
framing mechanism idle (target == source means nothing to fit) and the
vertical overlays banded down the middle. Every consumer calls
`resolve_delivery_format(project_folder)`; nothing carries the value as a
key, because `project_resolution` was mapped by no DAG edge at all and
every `.get(..., [1080, 1920])` in the tree silently read its own
fallback. `project_fps` had the identical hole and now has edges.

**A Fusion comp composites over the SOURCE frame, not the delivery
frame.** Every Background node `build_effect_comp` draws - vignette,
fade, both transition halves - is a solid image merged over `MediaIn`, so
it must be the source clip's own size (read off the MediaPoolItem's
`Resolution`; do NOT swap for rotation, Fusion gets the stored frame).
Sized to the delivery format instead, it paints a hard-edged rectangle in
the middle of the picture and no warning fires, because a wrong-sized
Background is a valid comp. `CompEngine.from_params` had the fix and the
comment; the renderer calls `build_effect_comp`, which did not.

**One vision schema, two views.** `vision_pipeline_v3.py` emits
`scene[]/camera[]/actions[]/objects[]/assessment{}`; consumers historically
read `analysis.*`/`blocks`. `library/tools/vision_schema_adapter.py` derives
the second view from the first (step 1.03 applies it on write,
`semantic_index` on read), so either may be addressed. Derive only what v3
measured - an absent field warns in `context_projector`, a fabricated one
silently misleads the model. A step that wants framing, stability, usable
ranges or subject visibility must also list those paths in its manifest's
`context_fields`, or they are deleted before the prompt.

**Every frame of the timeline must show a clip.** `compile_manifest` fails
on any stretch of V1+V2 with nothing on it (`_assert_timeline_fully_covered`).
The one exception is a hole the plan deliberately declared, via the optional
`intentional_black_beat`/`black_beat_reason` spine keys documented in
`library/tools/spine_contract.py`; an undeclared hole always fails.
The rough-cut review records only negative gaps by design, so before this
existed the only thing that noticed 6.4s of black was the ffmpeg probe in
step 6.02, one step from the end. That probe honours the same declaration:
step 6.02 passes `declared_black_beat_ranges` into `render_qa`, and both
gates bound a beat by `MAX_DECLARED_BLACK_BEAT_SECONDS` from the spine
contract - keep the bound in one place or a beat passes compilation, burns
a render, and fails at the last step.

**Never invoke `step_1_03_semantic_analysis/step.py` against a real
project to test it.** Any clip whose id is not already a
`raw/analysis/clip_profile_<clip_id>.json` triggers a full local vision
run. Exercise the collection half with an analysis dir of copied profiles
and `raw_footage_files: []`.

**There are NO creative floors, and there must not be again.** A B-roll
minimum and an SFX minimum both existed; the captain removed them outright
on 2026-08-20, declining warnings, a reconciled range and per-template
minimums by name. The creative direction decides how many cutaways and how
many sounds a piece gets, and the accepted consequence is that a thin edit
is no longer caught mechanically. A floor in the PROMPT pads just as
effectively as one in the bridge - "you MUST plan exactly 5-15" is what put
two cutaways and one sound into the shipped edit with rationales that said
so - and `tests/test_no_creative_floors.py` fails on either. What stays is
`_assert_sfx_distributed`, which catches a collapse (every SFX on one
frame), not a sparse plan.

**A hybrid step's LLM gets an EMPTY schema when the bridge supplies the
step's only output.** `present_llm_step` builds the injected schema from
`interface.outputs` minus anything the bridge already produced, so
`music_selection` asked its model for nothing at all and got `{}` back for
months. Declare `interface.llm_outputs` on any step whose bridge emits a
key the step also declares as an output. Music selection is now one
enumeration, `library/tools/music_selection_contract.py`: the bridge
catalogues `PIPELINE_MUSIC_LIBRARY` **and** the project's `music/` and
picks nothing; the post-bridge judges source, catalogue membership,
duration plausibility and a justification naming the registers the
creative direction forbids. Choosing from OUTSIDE the library is
legitimate and stays allowed.

**Judge a vision-model gate by what it actually reads - in both
directions.** The rule that killed `smart_reframe` and
`verify_fusion_comps` has a mirror: a gate that FAILS correct output is no
more coverage than one that cannot fail. `subtitle_qa` sampled two fixed
instants of a transparent overlay, which on an ordinary caption pause are
blank; gemma-4-12b passed those blanks on one run and failed them on the
next, then failed four demonstrably clean caption frames three times out
of three with invented defects ("cut off by the bottom edge" of type with
150 clear rows beneath it). Its mechanical half - ink exists, ink is
inside the frame, ink is bottom-positioned - now decides, and the model's
typography opinion is recorded rather than enforced. If you add a
model-judged gate, give it a deterministic half that can carry the verdict.

## 11. Third-Party Asset Licenses

**No third-party look assets ship.** The repository previously carried one
PowerGrade, `cinematic_warm.drx` - a free gift from Zay's Aesthetics with no
written terms of any kind, and so no commercial usage clause for a repository
that produces commercial video. It has been removed, along with the whole
PowerGrade route (`build_powergrade.py`, the `luts/` and `dctls/` preset
directories, `preset_indexer.py`). `tests/test_color_grade_delivery.py` fails
if any `.drx` reappears.

The house look is now authored in this repository as CDL plus Fusion values -
see section 12. Anything added to `library/presets/` from an outside source
needs its licence recorded here before it lands.

**One third-party asset does ship, with its licence.** Montserrat, as
`remotion-subtitles/public/fonts/Montserrat-Variable.ttf` - a variable font
covering the 100-900 weight axis, which is every weight
`library/tools/subtitle_style.py` can ask for. Licensed under the **SIL Open
Font License 1.1**, which permits redistribution including commercially and
requires the licence to travel with the font; it does, as
`public/fonts/OFL-Montserrat.txt`. It is bundled rather than fetched because
`@import url('https://fonts.googleapis.com/...')` with no `delayRender` made
typography a race with the network, and a lost race rendered captions in
Chromium's fallback sans at a different width with nothing downstream able to
tell. `tests/test_bundled_fonts.py` fails if the font or its licence goes
missing, if a font is imported over HTTP again, or if a template names a font
that is neither bundled nor explicitly accepted as a system font.

## 12. The house look

One enumeration, `library/tools/house_look.py`, holds every look a brand
template may name via `style.house_look`; an unknown name raises and a
template naming none gets exposure normalisation only. Each look is delivered
in two halves, because that is what the mechanisms can express: **CDL** carries
hue and level (slope = highlights, offset = shadows and the black floor, power
= midtones, plus saturation) and is applied by `SetCDL` in
`resolve_build_timeline`; **Fusion** carries what a CDL has no term for -
pivot contrast, glow, grain, and a shaped, optionally coloured vignette - and
reaches the picture only through the parameter names
`fusion/comp_builder.build_effect_comp` dispatches on.

The values are authored from the captain's planning docs at
`PLAN/series portfolio '26 planning/`, which are READ-ONLY and live outside
this repo. Every look cites its source in `derived_from` and records design it
cannot deliver in `withdrawn`. Nothing depends on a file inside a Resolve
installation. Add a look only with a template that names it -
`tests/test_house_look.py` fails on an orphan.

## 13. Intros, outros and end cards

One enumeration, `library/tools/bookends.py`, holds the whole mechanism: a
brand template declares `content.bookends`, and a template that declares
nothing gets nothing (captain's Q7, 2026-08-16 - "wire them up, but only on
some videos"). A declaration names either an `asset` that already exists or
a `composition` to render, plus a `duration_seconds`; a malformed one
raises rather than being dropped, because a dropped declaration is a card
the editor believes shipped.

The path is the same one every other clip takes, and that is the point:
`mesh_spine` turns each declaration into an `intro_card`/`outro_card`/
`end_card` spine block (NOT `intro`/`outro`, which already mean a
non-speech pacing beat), step 4.06 renders the composition-mode ones,
`compile_manifest` emits a V1 clip, and the renderer places it. So a card
is inside the coverage assertion, the manifest duration and render QA.
`library/tools/execution/import_endcard.py` appended one out of band after
compilation and is deleted; see the row in `docs/PIPELINE_PLAN.md` section
5. Project-owned compositions are staged **verbatim** by
`bookend_render.py` into gitignored build output - the engine renders a
client's asset, it never edits one.

## 14. General assets vs project assets

`docs/ASSET_LIBRARY_PLAN.md` is the standing test, ratified 2026-08-20.
Three questions, and an asset must pass all three to live in the engine -
failing any one is sufficient to stop it: **substitution** (does it survive
being handed another series' content, or does it encode one series' copy,
palette or typeface?), **timing and geometry** (is it anchored to a spine
block and to the picture the delivery format actually produces, or to
absolute frames and a full-bleed frame nothing renders?), and **reader**
(does a step read it and a test assert the picture changes?). Question one
decides where it lives; two and three decide whether it is finished.

The case that motivated it: the 4th Wall end card failed all three and was
removed on the captain's ruling. It was one previous trial run's finished
artwork - series copy, absolute frame numbers from a 60.000s cut, an
unbundled typeface - filed in the now-deleted `library/templates/fourth_wall.yaml`
as series defaults, where no step ever read it. The template itself was
deleted on 2026-08-20 (captain's ruling: the series template arrives later,
whole, with authorisation). **A brand template may set per-series PARAMETERS
and may not contain ARTWORK**: no on-screen copy, no coordinates or frames
describing one finished episode. Artwork is a project asset, declared by
reference through `content.bookends` and staged verbatim (section 13).

**Per-series typefaces live per project, not in the engine** (captain's
ruling, 2026-08-20). Font files and their licences belong with the project
that owns the series; the engine stays series-neutral. Accepted cost: each
project folder carries its own fonts and licences, and `bookend_render.py`
must stage them.

`effect.timed_text_overlay` now HAS a reader, which is what makes a
timed-text declaration legal at all (question three). One enumeration,
`library/tools/timed_text_overlay.py`: a moment is timed from the SPINE
(`block` + `anchor` + `offset_seconds` + `duration_seconds`), absolute
`start_frame` stays available and is bounded by the spine's real length,
and a malformed declaration raises. Moments whose spans touch are grouped
into one rendered segment, because two clips cannot share frames of a
track; `timed_text_render.py` renders each inside step 4.06,
`compile_manifest` carries them as the `timed_text_overlay` manifest key,
and `resolve_build_timeline` places them on **V6**. `NO_READER` is gone.
`tests/test_timed_text_delivery.py` renders a 24-frame fixture through
the real path and asserts the declared colours are in the declared rows
at the declared frames - the delivery half, without which "a reader
exists" is the same empty claim `smart_reframe` made for months.

**A card is declared by the PROJECT, and the project wins.** A timed text
moment is copy the viewer reads, so it is ARTWORK and belongs with the
project (section 3 of the plan). The bookend route already had a
project-side declaration - `content.bookends` names a `source:` - and
timed text had none, because a line of copy has no file to point at.
`timed_text_overlay.resolve_declaration` is the missing half: a project's
`project.yaml` may carry its own `effect.timed_text_overlay` and it
replaces the brand template's whole slot, the same project-over-template
precedence `delivery_format_name` uses. Step 4.06 calls it. The worked
example is `tests/fixtures/night_card_project/project.yaml` - Through the
4th Wall's Night card, the second item in the captain's ratified build
order.

**The card states its own `y`, because geometry is still normalised
against the whole delivery frame** and not the picture area inside
letterbox bars; there is no picture-area enumeration to resolve against.
Measured on the only finished render on disk - project 001,
`Pipeline_Edit.mp4`, 1080x1920, 16:9 landscape source - the picture
occupies rows **656..1263** and the burnt-in captions rows ~1699..1765, so
`y` in 0.35..0.65 is over picture whether the source letterboxes or fills.
`tests/test_night_card_delivery.py` renders the real card at the real
delivery format and asserts its ink lands in that band; put the removed
end card's `y: 0.15` back and it fails at rows 260-327.

**A declared typeface must be one that really draws the glyphs.** One
enumeration, `library/tools/render_fonts.py` - bundled, accepted as a
system font, or carried by the project as a `font_file` staged out of
`<project>/brand_assets/` by `prep_remotion`. Anything else raises,
because Chromium substitutes its fallback sans and the frames are still
valid pictures of the right size. `TimedTextOverlay` loaded NO font at
all until 2026-08-20 while naming a family in CSS, so every card it
rendered was already in the wrong face.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
