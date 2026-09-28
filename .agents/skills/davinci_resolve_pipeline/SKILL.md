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

### The Owner
`library/tools/fusion/comp_builder.py` - `build_effect_comp` - is the ONE
dispatch the renderer runs, reached through
`library/tools/execution/apply_fusion_comps.py`.  Its rules are AGENTS.md
section 5 ("Six things that must NEVER appear in a Fusion .comp", and what one
must ALWAYS carry) and `references/fusion_gotchas.md`.

`library/tools/fusion/engine.py` - `CompEngine.from_params` - accepts the old
flat keyword signature and delegates to the same dispatch.  Use it only to
call an existing caller's shape; do not add a second dispatch beside it.

Two thin wrappers in `library/steps/step_6_01_render/` -
`fusion_comp_generator.py` and `fusion_transition_generator.py` - used to be
documented here.  No step, bridge, manifest or DAG node ever reached them
(measured in `docs/CHROMA_KEY_TRANSITIONS_MEASURED.md`) and they were removed
on 2026-09-12.  So was the `SEGMENT_PRESETS` table this section used to
advertise: deleted under P3.4 by the captain's ruling of 2026-08-16, for
reasons recorded in `library/tools/fusion/presets.py`.

### Effect Parameters
The parameters an effect accepts are `plan_vfx.TOOLKIT_PARAMETERS`
(`library/steps/step_4_03_plan_vfx/`), and that list deliberately carries **no
value, no default and no bound**.  How strong an effect is is the PLAN's
number (AGENTS.md 10.5); an entry whose `params` name none of them is dropped
rather than completed from a constant.

This section used to print a worked `generate_comp(...)` call with concrete
zoom, grade, glow and vignette numbers in it.  Those numbers were a creative
floor in an agent-facing document - including the `vignette=True` at blend
0.25 that AGENTS.md 12 records as REMOVED - so they are not restated here.

### Transition Types
The plannable set is one enumeration, `library/tools/transition_vocabulary.py`,
which also records why each withdrawn type is not plannable.  Default values
for the three drawn transitions are `library/tools/fusion/effects.py`
(AGENTS.md 5).

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
See `scripts/connect_resolve.py` for the reusable connection context. Keep
all Resolve API calls inside `with connect(...)` so the instance lease stays
held for the whole operation; use `exclusive=False` only for reads that do
not move the current project or timeline.

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
