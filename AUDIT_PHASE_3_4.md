# Phase 3 & 4 Audit Report

## Audit Scope
- `step_3_01_assign_aroll`
- `step_3_02_select_broll`
- `step_3_03_review_rough_cut`
- `step_4_01_plan_subtitles`
- `step_4_02_plan_transitions`
- `step_4_03_plan_vfx`
- `step_4_04_plan_sfx`
- `generate_remotion_props.py` and Remotion subtitle components

## Findings & Fixes

### 1. `llm_raw_response` Clobbering Bug in Phase 4 (CRITICAL - FIXED)
**Location**: `step_4_02_plan_transitions/post_bridge.py`, `step_4_03_plan_vfx/post_bridge.py`, `step_4_04_plan_sfx/post_bridge.py`
**Issue**: 
The `post_bridge.py` scripts for transitions, VFX, and SFX included the following logic:
```python
creative = data.get("<x>_creative")
if "llm_raw_response" in data:
    creative = data["llm_raw_response"]
```
If the orchestrator injected the raw Markdown output (as a string) into `llm_raw_response` along with the successfully parsed object in `<x>_creative`, the script unconditionally overwrote the parsed JSON object with the raw string. This then failed the `isinstance(creative, list)` check, falling back to an empty list `[]` and silently dropping the LLM's entire creative plan.
**Fix**: Modified all three `post_bridge.py` scripts to only use or parse `llm_raw_response` if the parsed `creative` object is missing or empty.

### 2. Missing `project_config` in `step_3_03` Manifest (HIGH - FIXED)
**Location**: `step_3_03_review_rough_cut/manifest.json` and `step_3_03_review_rough_cut/step.py`
**Issue**: `step_3_03_review_rough_cut/step.py` attempts to read `data.get("project_config")` to determine the `target_duration_seconds` for checking if the rough cut exceeds the 150% duration threshold. However, `project_config` was completely missing from both `interface.inputs` and `state.reads` in `manifest.json`. Since the orchestrator only injects declared dependencies, this check was silently falling back to a hardcoded 60.0 seconds limit for every project, regardless of the actual configured target duration.
**Fix**: Added `project_config` to `interface.inputs` and `state.reads` in `step_3_03_review_rough_cut/manifest.json`.

### 3. Missing `project_config` in `step_3_02` Manifest (MINOR)
**Location**: `step_3_02_select_broll/manifest.json`
**Issue**: Similar to 3.03, `project_config` is missing from `reads`. If it were needed by the bridge, it would be missing. However, the bridge currently relies entirely on `creative_brief` and `creative_direction`, so this does not break anything in the current state.

### 4. Robust Output Structure in `generate_remotion_props.py` (CLEAN)
**Location**: `step_4_05_render_subtitles/generate_remotion_props.py`
**Status**: The script correctly translates subtitle entries into frame counts rebased onto the `block` context (by taking `start_s = entry['timeline_start'] - render_start` and multiplying by `fps`). The Remotion `AnimatedWord` components correctly handle the mapped properties and emphasis words.

### 5. `broll_candidates_toon` Structure Handling (CLEAN)
**Location**: `step_3_02_select_broll/post_bridge.py`
**Status**: The fallback execution mechanism effectively handles the scenario where the LLM does not return a JSON string, parsing the `broll_candidates_toon` format line-by-line using a robust loop that correctly accounts for empty lists and missing parameters.

## Summary
The audit has been completed successfully. The critical logic errors causing LLM creative omissions in Phase 4 and duration validation failures in Phase 3 have been successfully resolved. No other outstanding architectural or data flow issues were detected.
