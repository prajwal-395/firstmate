---
name: davinci_resolve_pipeline
description: >-
  Work on Ren's Resolve timeline pipeline, Fusion compositions and render
  debugging. Use resolve-axi for all Resolve inspection and edits; use this
  skill for pipeline-owned composition and rendering details.
---

# DaVinci Resolve pipeline

For any Resolve state inspection or mutation, use [resolve-axi](../resolve-axi/SKILL.md). Discover the current AXI command and capability at run time. Native scripting, MCP and UI control are backends behind AXI, not direct production paths. Do not treat a successful API return or render invocation as proof of the requested result.

## Fusion compositions

- `library/tools/fusion/comp_builder.py` owns composition construction; `library/tools/execution/apply_fusion_comps.py` owns its renderer path.
- `library/tools/fusion/engine.py` keeps the legacy parameter shape and delegates to the same builder.
- Do not revive removed step wrappers or preset tables. Effect inputs and transition names come from their owning plan and vocabulary: `library/steps/step_4_03_plan_vfx/` and `library/tools/transition_vocabulary.py`.
- Read [Fusion gotchas](references/fusion_gotchas.md) when changing `.comp` structure. These are composition-format constraints, not Resolve transaction instructions.

## Debugging and verification

Use the owning pipeline step or `ren` capability for builds and renders. For a composition defect, isolate the smallest failing composition change and verify the resulting pixels. Use `verify_render` for file-level checks and `verify_timeline` for structural timeline checks; use AXI's operation-specific verification for local edits. Do not label a render or rebuild verified until the relevant read-back or render checks pass.

## References

- `references/fusion_gotchas.md` - composition constraints.
- `references/visual_verify.md` - local render-frame inspection guidance.
- `references/step_data_flow.md` - pipeline ownership and data flow.
- `references/api_patterns.md` - AXI routing for Resolve control.
