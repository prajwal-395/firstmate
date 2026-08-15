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
- `library/presets/`: Reusable assets indexed with metadata for powergrades, LUTs, and Fusion macros.
- `remotion-subtitles/`: Node.js React application used to render subtitle overlays.
- `scripts/`: Assorted bash helper scripts for environment setup and maintenance.
- `tests/`: Unit and integration tests for the pipeline engine.
- `manage_project.py`: Top-level CLI for creating, listing, and running projects.
- `requirements.txt`: Python dependencies required by the pipeline.

## 3. Pipeline execution

The pipeline is defined as a Directed Acyclic Graph (DAG) in `library/processes/edit_video/dag.json`.
Execution order is resolved via topological sort.
The DAG groups 26 atomic steps into distinct phases: 0 for setup, 1 for analysis, 2 for planning, 3 for assembly, 4 for post-production, 5 for finishing/QA, and 6 for rendering.
`library/steps/` holds 28 step definitions; `object_segmentation` (1.06) and `ocr_extraction` (1.07) exist but are not wired into the DAG.

Run the pipeline using the project manager CLI: `python3 manage_project.py run <slug>`.
Use `--from <step_id>` to resume execution starting from a specific step.
Use `--step <step_id>` to run one isolated step.
Use `--auto` to auto-complete hybrid steps using bridge output instead of pausing for LLM input.
Use `--review` to enable review gates that pause the pipeline for human inspection on the dashboard.
Use `--resume` to continue the pipeline after a review gate is approved or revised.
Use `--dry-run` to print the execution plan without running steps.

Steps come in three implementation types based on their contents.
Deterministic steps have a `step.py` and run automatically using JSON stdin/stdout.
Hybrid steps have a `bridge.py` that pre-computes context and a `handoff.md` prompt for an LLM to complete the step.
LLM-only steps have only a `handoff.md` prompt and require an LLM to generate the output from upstream context.

Pipeline state is stored in `pipeline_data.json` at the root of each project directory.
The state file tracks completed steps and stores all JSON outputs under `step_outputs`.
Inspect `pipeline_data.json` to debug data flow or verify upstream step results.

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
outcome; a silent unread key is not. `docs/PIPELINE_PLAN.md` is the
standing audit of which manifest keys have a reader and which do not;
check it before assuming a stage's output reaches the picture, and update
it when you wire or withdraw one.

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

## 11. Third-Party Asset Licenses

**PowerGrades**: The "Cinematic Warm" PowerGrade (`cinematic_warm.drx`) is intended to be "The Grade" provided by Zay's Aesthetics. It is offered as a free gift ("no strings, no catch") with no formal written terms on the author's website. Since this repository produces commercial video, note that there is no explicit commercial usage clause provided by the author. Usage is at your own discretion.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
