CONVERGENCE AUDIT COMPLETE

3 pipeline logic bugs found and fixed:
1. `step_6_01_render/resolve_build_timeline.py`: Missing `overlay_path` fallback for `mg_segments`. The manifest's legacy `overlay_path` field inside `mg_overlay_info` was ignored, causing motion graphics overlays to be dropped silently if `mg_segments` wasn't explicitly populated in the manifest and no argument was passed. Added the fallback matching subtitles.
2. `step_5_04_compile_manifest/step.py`: In orchestrator mode (`compile_manifest_from_inputs`), `transition` parsing failed to handle `"position": "between_X_Y"` to enrich transitions with `from_block` and `cut_point_timeline`. This caused `fusion_transitions` to fall back to using list indices instead of block mappings, applying transitions to wrong clips if clip counts differed. Added the missing enrichment block from standalone mode.
3. `step_5_04_compile_manifest/step.py`: Completely ignored `video_segments` data produced by `step_3_01_a_roll_assignments`. It built `v1_clips` straight from `audio_spine.structure`, effectively losing any visual cuts (e.g. jump cuts) introduced by step 3.01. Fixed both standalone and orchestrator modes to use `a_roll_assignments` and build `v1_clips` accurately.

CLEAN PASS - no pipeline logic bugs remaining.
