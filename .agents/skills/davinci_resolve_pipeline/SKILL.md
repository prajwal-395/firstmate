---
name: davinci_resolve_pipeline
description: >
  Operating the DaVinci Resolve video editing pipeline. Covers Fusion .comp 
  generation, timeline assembly, visual verification, debugging Resolve issues,
  and the full 24-step automated editing workflow. Use when working with Resolve
  scripting, Fusion compositions, rendering, or pipeline step execution.
---

# DaVinci Resolve Pipeline Skill

## When to Use This Skill
- Generating or modifying Fusion .comp files
- Building or modifying Resolve timelines via the scripting API
- Debugging black frames, crashes, or visual artifacts
- Running pipeline steps or compiling manifests
- Verifying rendered output quality

## Architecture Overview

The pipeline processes raw footage through 24 steps:

```
1.x Analysis  → 2.x Creative Planning → 3.x Assembly
→ 4.x Post-Production → 5.x Finishing → 6.x Render
```

Each step produces `step_X_YY.json`. The manifest compiler (5.04) combines
them into `assembly_manifest.json`, which drives `resolve_build_timeline.py`.

## Fusion .comp Generation

### The Generator
`library/steps/step_6_01_render/fusion_comp_generator.py` contains:
- `generate_comp(duration_frames, **effect_params)` → comp string
- `write_comp(path, content)` → file path
- `SEGMENT_PRESETS` — named presets (HOOK, CORE_INSIGHT, TURNING_POINT, etc.)

### Effect Parameters
```python
generate_comp(
    duration_frames=90,
    zoom_start=1.0, zoom_mid=1.04, zoom_end=1.03,  # Ken Burns zoom
    pan_start=(0.5, 0.5), pan_end=(0.5, 0.49),      # Static offset (no animation)
    grade_gain=1.05, grade_contrast=0.04, grade_saturation=1.15,
    glow_gain=0.08,                                   # Soft bloom
    vignette=True, vignette_blend=0.25,               # Edge darkening
    tail_transition="fade_to_black", tail_transition_frames=12,
)
```

### Transition Types
- `fade_to_black` — Merge opacity animation (safest)
- `zoom_blur` — Transform zoom + DirectionalBlur (Length ≤ 5.0)
- `defocus` — Animated defocus (XDefocusSize ≤ 3.0)
- `flash` — Brightness spike at cut point (Gain ≤ 2.0)

## Debugging Workflow

### Black Frames
1. Render the frame: use the visual verification pipeline (see references/)
2. If file < 2KB → comp is broken
3. Common causes:
   - `Path {}` + `Merge` in same comp → use static Center
   - Missing `GlobalOut` on Background → add it
   - `ApplyMode` on Merge → remove it (crashes Resolve)

### Crashes on Load
- `ApplyMode = Input { Value = 5, },` → SIGSEGV in MergeInputs::NotifyChanged
- Solution: Remove ALL ApplyMode from generated comps

### Effects Too Strong
| Effect | Max Value |
|--------|-----------|
| DirectionalBlur Length | 5.0 |
| Transform zoom (transition) | 1.04 |
| Defocus XDefocusSize | 3.0 |
| Flash BrightnessContrast Gain | 2.0 |

### Incremental Isolation
When a comp produces bad output, rebuild incrementally:
1. Bare comp (MediaIn → MediaOut) — verify clip works
2. Add Transform (zoom only)
3. Add BrightnessContrast
4. Add SoftGlow
5. Add Vignette (Background + EllipseMask + Merge)
6. Add transitions

Render after each step to find the failing node.

## Key Scripts

### Connect to Resolve
See `scripts/connect_resolve.py` for the reusable connection boilerplate.

### Render Frame to PNG
See `scripts/render_frame.py` for the visual verification workflow.

### Diagnose Comp
See `scripts/diagnose_comp.py` for dumping a clip's Fusion node graph.

## MCP Server (`davinci-resolve`)

An MCP server with 34 compound tools is available for direct Resolve control.
Use MCP tools instead of writing raw Python scripts when possible.

### Key Tools for This Pipeline

| Tool | Common Actions | Use For |
|------|---------------|---------|
| `timeline_item_fusion` | `import_comp`, `delete_comp`, `get_comp_names` | Managing Fusion comps on clips |
| `fusion_comp` | `add_tool`, `connect`, `set_input`, `get_tool_list` | Inspecting/modifying Fusion node graphs |
| `render` | `add_job`, `start`, `set_settings`, `is_rendering` | Rendering frames for verification |
| `timeline` | `get_items`, `add_track`, `set_track_name` | Timeline clip management |
| `timeline_item` | `set_property`, `get_property` | Zoom, pan, opacity, crop |
| `media_pool` | `import_media`, `get_clip_list` | Importing footage |
| `resolve_control` | `get_status`, `open_page` | Connection check, page switching |

### Example: Import a comp via MCP
```
call_mcp_tool(
    ServerName="davinci-resolve",
    ToolName="timeline_item_fusion",
    Arguments={"action": "import_comp", "params": {
        "path": "/path/to/effect.comp",
        "track_type": "video", "track_index": 1, "item_index": 0
    }}
)
```

### When to use MCP vs. Custom Scripts
- **Use MCP**: For standard Resolve API calls (import comp, render, get clips, set properties)
- **Use custom scripts**: For .comp file generation (MCP can't generate comp content), visual verification with ffmpeg conversion, batch operations with custom logic

## References
- `references/fusion_gotchas.md` — Complete list of .comp format rules and crash bugs
- `references/api_patterns.md` — Resolve API patterns with MCP equivalents
- `references/visual_verify.md` — Render frame to PNG pipeline
- `references/step_data_flow.md` — What each pipeline step reads/writes/feeds

