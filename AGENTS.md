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
The DAG groups 28 atomic steps into distinct phases: 0 for setup, 1 for analysis, 2 for planning, 3 for assembly, 4 for post-production, 5 for finishing/QA, and 6 for rendering.

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

## 6. Data flow

Data flows through the pipeline via `pipeline_data.json`.
Each step reads required upstream outputs from this file based on the DAG's `data_mapping` edges.
Each step writes its own output back to `pipeline_data.json` under `step_outputs.<step_id>`.
The `catalog` output contains video metadata, durations, and file paths.
The `semantic_analysis` output contains mood, energy, visual descriptions, and detected objects.
The `speech_sequence` output orders speech segments into a coherent narrative.
The `aroll_assignments` output maps narrative blocks to specific source clips and timeline ranges.
The `broll_selections` output assigns secondary footage (`b_roll_assignments`) and standalone cutaways (`b_roll_interjections`) to cover A-roll segments or insert visual breaks.
The `compile_manifest` output consolidates all decisions into an `assembly_manifest.json` that drives the final Resolve render.

## 7. Project management

Manage projects using the `manage_project.py` CLI tool.
Create new projects with `python3 manage_project.py new <slug> --name "Project Name"`.
Project configurations are stored in `project.yaml` within each project directory.
The `ProjectConfig` schema defines source settings, pipeline options, and Resolve bindings.
The project registry scans the root directory to list and manage all available projects.
Multi-project environments group projects by client folders if specified during creation.

## 8. Environment and dependencies

The pipeline requires specific environment variables and dependencies to function.
Set `RESOLVE_SCRIPT_API` and `RESOLVE_SCRIPT_LIB` to point to your DaVinci Resolve installation.
Set `HF_TOKEN` for HuggingFace models like Audio Flamingo Next.
Set `PIPELINE_SFX_LIBRARY` to the absolute path of the sound effects library.
Set `PIPELINE_MUSIC_LIBRARY` to the absolute path of the background music library.
Set `PIPELINE_PROJECTS_ROOT` to the directory where video projects are stored.
Python dependencies are listed in `requirements.txt` and must be installed in the environment.
External tools include `ffmpeg` and `ffprobe` for media processing.
Node.js is required to run Remotion for subtitle rendering.
GPU acceleration is required for models including Gemma 4, SAM 2, WhisperX, and EasyOCR.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
