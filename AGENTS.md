# Video Editing Pipeline - Agent Rules

## Project Overview
This is an automated video editing pipeline that takes raw footage through 24 steps
from analysis to final DaVinci Resolve assembly. The pipeline lives in `library/steps/`.

## Architecture
```
Step 1.x  - Ingest & Analysis (scan, catalog, semantic, temporal, prosody)
Step 2.x  - Creative Planning (direction, speech sequence, music, spine)
Step 3.x  - Assembly (A-roll, B-roll, rough cut review)
Step 4.x  - Post-Production (subtitles, transitions, VFX, SFX planning)
Step 5.x  - Finishing (color grade, audio mix, manifest compilation)
Step 6.x  - Render (Resolve timeline build, output validation)
```

Each step reads from and writes to a shared output directory as `step_X_YY.json`.
The final `assembly_manifest.json` (from step 5.04) drives `resolve_build_timeline.py`.

## Brand Template Registry
The project uses a structured brand template system to provide stylistic inputs to pipeline steps without raw LLM context stuffing.
- **Brand Template Schema:** `library/schemas/brand_template.py` (has `StyleSlots`, `EffectSlots`, `ContentSlots`)
- **Default Template:** `library/templates/default_brand.yaml`
- **Registry & Loader:** `library/tools/brand_registry.py` (`load_brand_template`, `query_slots`, `validate_template`)
- **Pipeline Integration:** `brand_template` is an optional pipeline parameter in `edit_video/manifest.json`. `gather_step_inputs` injects requested slots (`brand_style`, `brand_effect`, `brand_content`) into steps that declare them in their manifest (e.g. `step_2_01`, `step_4_02`, `step_4_04`, `step_5_01`).

## OCR Extraction
The pipeline uses EasyOCR (Step 1.07) for precise text extraction with bounding boxes and temporal tracking. This replaces best-effort LLM descriptions by providing structured data (confidence scores, normalized coordinates, and deduplicated appearance ranges).

## Preset Library & Indexer
Reusable assets are indexed with companion `.meta.json` files.
- **Presets Directory:** `library/presets/` with subdirectories (`powergrades`, `fusion-macros`, `luts`, `dctls`, `fairlight`)
- **Metadata Schema:** `library/schemas/preset_metadata.py`
- **Indexer & Search:** `library/tools/preset_indexer.py` (`scan_library`, `find_presets`, `find_preset_for_mood`)

## Visual QA System
The project implements a Visual QA system with two routing paths:
- **Frame Grabs:** The OAuth LLM grabs a frame via MCP `gallery_stills > grab_and_export` and analyzes the base64 image inline. The MCP call chain is: `save_state` > `open_page("color")` > `grab_and_export(cleanup=true, delete_after=true)` > `restore_state`.
- **Video Segments:** A local Gemma 4 12B model analyzes rendered video segments.

Both paths use prompt templates defined in `library/tools/visual_qa_prompts.py` and output a structured JSON response matching the schema. The visual QA hooks into the pipeline via settings configured in the `visual_qa` section of the manifest and logs results using `VisualQACheck` and `VisualQAReport`.

## DaVinci Resolve Scripting - CRITICAL RULES

### Connection
```python
import sys, os
sys.path.append("/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules")
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
import DaVinciResolveScript as dvr
resolve = dvr.scriptapp("Resolve")
```
If `resolve` is `None`, Resolve is not running or not fully loaded. Retry with delay.

### Fusion .comp Files - NEVER DO THESE
1. **NEVER use `ApplyMode`** in a Merge node - crashes Resolve (SIGSEGV)
2. **NEVER use `Path {}` when a Merge node exists** in the same comp - black output
3. **NEVER use `BlendClone`** - silently ignored; use `Blend`
4. **NEVER use `Tools = ordered() {`** - use `Tools = {`
5. **NEVER omit `GlobalOut`** on Background nodes - stops rendering mid-clip
6. **NEVER set DirectionalBlur Length > 5** - creates artifacts and edge tiling
7. **NEVER set transition zoom > 1.04** - too aggressive, breaks immersion

### Fusion .comp Files - ALWAYS DO THESE
1. Always set `Inverted = Input { Value = 1, }` on EllipseMask for vignettes
2. Always include `MaskWidth`, `MaskHeight`, `PixelAspect` on EllipseMask
3. Always wire `Transform1.Input <- MediaIn1.Output` explicitly
4. Always use `Blend` (not `BlendClone`) for Merge opacity
5. Always include `GlobalOut` on Background nodes matching clip duration
6. For animated pan/center, use static `Center = Input { Value = { x, y }, },`
7. **Always use SOURCE clip frame count for `clip_dur`** - NOT `clip.GetDuration()` (timeline duration). Fusion comps operate on the full source media range. Use `clip.GetSourceEndFrame() - clip.GetSourceStartFrame() + 1` or `MediaPoolItem.GetClipProperty('Frames')`.

### Fusion .comp Frame Mapping - CRITICAL
Fusion compositions operate on the **source clip's full frame range**, not the
timeline's trimmed duration. A clip with 513 source frames placed as 410 frames
on the timeline will have Fusion frame range 0-512, not 0-409.

- `clip.GetDuration()` - timeline duration (WRONG for keyframes)
- `clip.GetSourceEndFrame() - clip.GetSourceStartFrame() + 1` - source frame count (CORRECT)
- `int(mpi.GetClipProperty('Frames'))` - also correct

If keyframes are set at `clip.GetDuration() - 1` instead of the source end frame,
transitions will fire early (at ~80% of the clip) and hold for the remaining frames.

### Default Transition Values (from .drfx analysis)
- **Brightness Flash**: `Brightness = 0.67`, `Saturation = 1.83`, animate `Blend` 0-1 with Sine easing
- **Crash Zoom**: Transform `Scale = 0.4, Offset = 0.6` (range 0.6-1.0), Quad easing, mirrored
- **Glow**: `SoftGlow.Gain = 5.0`, `SoftGlow.XGlowSize = 100`, linear easing
- Default easing curves: Sine (flash), Quad (zoom), Cubic (dissolve), Quart (smooth dissolve)
- Transitions use `LUTLookup` driven by system `Transition` variable (only in Edit page transitions)
- For per-clip Fusion comps, replicate with `BezierSpline.sampled()` pre-baked easing keyframes

### Visual Verification
Render single frames to verify effects look correct:
```python
resolve.OpenPage("deliver")
project.SetRenderSettings({
    "TargetDir": "/tmp/screenshots", "CustomName": "frame_45",
    "FormatWidth": 1080, "FormatHeight": 1920,
    "MarkIn": 45, "MarkOut": 45,
})
project.AddRenderJob()
project.StartRendering()
# Convert: ffmpeg -y -i frame_45.mov -frames:v 1 frame_45.png
```
- File size < 2KB = black/broken frame
- File size 30-150KB = real video content

### Audio Track Flooding Prevention
iPhone MOVs have multiple audio streams. Place V1 clips WHILE ONLY A1 EXISTS.
Then add A2+ tracks and place music/SFX with `mediaType: 2`.

### ImportFusionComp Reliability Rules
1. **NEVER create timeline and ImportFusionComp in the same Python process** - clip references go stale after timeline creation. Always import comps in a separate script/process.
2. **One ImportFusionComp returns a Composition object** (not True/False). Check `clip.GetFusionCompNameList()` to verify.
3. **Tool loading is lazy** - `GetToolList()` may show 0 tools immediately after import. Tools load when the clip is visited on the Fusion page or during playback.
4. **For reliable bulk comp building, use `comp.AddTool()` on the Fusion page** - this bypasses ImportFusionComp's lazy loading entirely.

### Fusion AddTool API (Live Node Building)
Use this to build node graphs programmatically on the Fusion page:
```python
resolve.OpenPage('fusion')
# Navigate to clip
timeline.SetCurrentTimecode(timecode_string)
comp = clip.GetFusionCompByName(clip.GetFusionCompNameList()[0])
# Add tools
bg = comp.AddTool('Background', x_pos, y_pos)
bg.TopLeftRed = 0
bg.Width = 1080
# Keyframe via Lua BezierSpline (Python keyframing doesn't work)
comp.Execute(f'''
local bg = comp:FindTool("{bg.Name}")
bg.TopLeftAlpha = comp:BezierSpline({{
    Points = {{
        [0] = {{ 0, Flags = {{ Linear = true }} }},
        [15] = {{ 0.85, Flags = {{ Linear = true }} }},
        [30] = {{ 0, Flags = {{ Linear = true }} }},
    }}
}})
''')
# Wire to output
mo = comp.FindTool('MediaOut1')
mo.Input = bg.Output
```

### V2 Overlay Track - CRITICAL RULES
1. **V2 is for ADDITIVE overlays only** - V2 Fusion comps can only access V2's own MediaIn (the transparent clip). They CANNOT read V1 video content.
2. **Use V2 for**: dip-to-black (Background + animated alpha), color washes, letterbox bars, particle effects - anything that GENERATES its own pixels.
3. **Do NOT use V2 for**: flash/brightness, defocus/blur, zoom - these process video content and MUST stay on V1 where the footage lives.
4. **Adjustment Clips cannot go on V2** - `InsertGeneratorIntoTimeline` always targets V1. Use a transparent MOV clip on V2 instead.
5. **One transparent clip, multiple placements** - import one `transparent_1080x1920_30fps.mov` and reuse via `AppendToTimeline` with `clipInfo` targeting `trackIndex: 2`.
6. **Composite modes**: `SetProperty('CompositeMode', n)` - 0=Normal, 1=Add, 5=Screen, etc. Normal mode respects Fusion alpha output.
7. **Track visibility**: `timeline.SetTrackEnable('video', 2, False/True)` to toggle V2.

### DaVinci Default Transition Values (from Templates.drfx)
When building transitions, use these DaVinci-native values:
- **Brightness Flash**: `Brightness = 0.67`, `Saturation = 1.83`
- **Glow**: `SoftGlow.Gain = 5.0`, `SoftGlow.XGlowSize = 100`
- **Crash Zoom**: Transform `zoom scale = 0.4, offset = 0.6` (range 0.6-1.0)
- **Cross Dissolve**: `Dissolve.Mix` with Quart easing
- Default easing curves use `LUTLookup` with `Sine`, `Quad`, or `Cubic` easing

## File Conventions
- Step outputs: `step_X_YY.json` (no version suffixes)
- Fusion comps: generated at runtime by `fusion_comp_generator.py`
- Transitions: generated by `fusion_transition_generator.py`
- Final manifest: `assembly_manifest.json`
- Transparent overlay clip: `library/assets/transparent_1080x1920_30fps.mov`

## Key Files
- `library/steps/step_6_01_render/resolve_build_timeline.py` - main Resolve assembly script
- `library/steps/step_6_01_render/fusion_comp_generator.py` - VFX .comp generator
- `library/steps/step_6_01_render/fusion_transition_generator.py` - transition .comp generator
- `library/steps/step_5_04_compile_manifest/step.py` - manifest compiler
- `library/tools/frame_utils.py` - timecode/frame conversion utilities
- `library/tools/fusion/engine.py` - CompEngine for composable Fusion comp building
- `library/tools/fusion/effects.py` - fx.* composable effect blocks
- `library/tools/fusion/nodes.py` - FusionNode, BezierSpline, FusionComp primitives

## Timeline Settings
- Resolution: 1080x1920 (vertical/portrait for social media)
- Frame rate: 30fps (from source iPhone footage)
- Project database: Resolve Disk Database (local)

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
