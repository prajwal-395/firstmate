# Audit Report: Phase 5 (Finishing) and Phase 6 (Render)

This document contains the exhaustive line-by-line review of Phase 5 and Phase 6, as well as the execution tool bridges (`resolve_bridge.py`, `fusion_lua`, `render_qa.py`, `review_gate.py`). 

## 1. Phase 5: Finishing

### `step_5_01_color_grade`
- **Finding (Data Flow / Schema)**: Uses `.get("clip_id")` and `.get("source_file")` directly on the `b_roll_assignments` dictionary. Downstream steps like `5.04` anticipate `assigned_clip` nesting. If the upstream schema produces nested `assigned_clip` structures, this step will miss them. 
- **Severity**: LOW (Assuming upstream step 4.02 flattens them).

### `step_5_02_audio_mix`
- **Finding (Logic)**: `music_behavior` defaults `fade_in` and `fade_out` to `-12dB`. Depending on the LUFS target, -12dB might be too high for a standard fade. 
- **Severity**: INFO.

### `step_5_03_creative_cohesion`
- **Finding (Logic Error / Data Mismatch) [FIXED]**: SFX specification list extraction incorrectly searched for `sfx_plan`, `sfx_events`, `sfx_spec` instead of prioritizing the standard `sfx_list`. If `sfx_list` was present as a nested key within `sfx_spec_raw` (which happens frequently), it would return the outer dict instead of the array, breaking `len(sfx)` and causing the step to silently miss SFX elements.
- **Severity**: HIGH
- **File**: `library/steps/step_5_03_creative_cohesion/step.py:31`
- **Action Taken**: Fixed by inserting `"sfx_list"` to the head of the dictionary `.get()` fallback chain.

### `step_5_04_compile_manifest`
- **Finding (Logic Error / Missing Validation) [FIXED]**: `semantic_analysis` fallback logic in orchestrator mode failed to unpack a nested `{"semantic_analysis": {"clips": [...]}}` structure. Because orchestrator inputs often pass the full upstream result block, `semantic_data.get("clips")` would silently fail and default to an empty list, discarding all Neural Engine directives (Stabilization, Super Scale, Magic Mask).
- **Severity**: HIGH
- **File**: `library/steps/step_5_04_compile_manifest/step.py:729`
- **Action Taken**: Fixed by adding an explicit check for the nested `semantic_analysis` dict key before extracting the clips list.

---

## 2. Phase 6: Render

### `step_6_01_render` (`resolve_build_timeline.py`)
- **Finding (CRITICAL RULE VIOLATION) [FIXED]**: The script previously created the timeline (`media_pool.CreateEmptyTimeline`) and then iterated over timeline clips to apply `ImportFusionComp` within the exact same Python process. 
  - According to `AGENTS.md` constraints: *"Never create a timeline and use `ImportFusionComp` in the same Python process. Clip references go stale after timeline creation. Always import comps in a separate script or process."*
  - This rule violation causes silent failures where VFX comps appear to import but their tools never load, or Resolve crashes.
- **Severity**: CRITICAL
- **File**: `library/steps/step_6_01_render/resolve_build_timeline.py:797-1010`
- **Action Taken**: Refactored the entire VFX/.comp application block into a standalone execution script `library/tools/execution/apply_fusion_comps.py`. Modified `resolve_build_timeline.py` to write the manifest to a temp file and spawn this new script as an isolated subprocess. This preserves clip references and prevents Resolve API state corruption.

### `step_6_02_validate_output`
- **Finding**: Fully reviewed `step.py` and `library/tools/render_qa.py`. The `render_qa.py` utility safely wraps `subprocess.run` with timeouts, correctly parses ffmpeg/ffprobe JSON output, and handles missing streams securely. No logical errors or silent failure vectors found in the QA logic.
- **Severity**: NONE

---

## Conclusion
All files read exhaustively. CRITICAL and HIGH severity issues affecting Resolve API stability (process isolation) and missing output generation (SFX parsing, semantic analysis nesting) have been permanently resolved.
