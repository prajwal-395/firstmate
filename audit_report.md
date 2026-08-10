# Phase 3 & 4 Convergence Audit Report

## Scope
- Phase 3 (steps 3.01 - 3.03)
- Phase 4 (steps 4.01 - 4.06)
- Remotion subtitles

## Findings & Fixes
The following pipeline logic bugs were found and fixed:

1. **Subtitle Alignment Offset Bug (`step_4_01_plan_subtitles/step.py`)**
   - **Bug**: The code used `seg.get("start_time", 0.0)` for speech segments. Legacy pipeline segments use `source_start` instead of `start_time`. This caused the script to wrongly default `seg_source_start` to `0.0`. This subsequently caused the word-level timestamps to receive a massive incorrect timeline offset calculation.
   - **Fix**: Added proper fallback `seg.get("start_time", seg.get("source_start", 0.0))` to correctly calculate `source_dur` and V1 source offset.

2. **Position Lookup Type Mismatches (`step_4_01`, `step_3_02`, `step_4_02`, `step_4_03`)**
   - **Bug**: Across several bridges and post-bridges, spine block `position` lookup relied on exact dictionary key matching. The pipeline `audio_spine` uses string positions (e.g. `"1"`, `"hook"`, `"body_1"`), but LLM generative outputs or legacy spines occasionally emit or cast positions as integers. This silent mismatch caused lookups to fail, resulting in B-roll and VFX defaulting their timeline start coordinates to `0.0` or throwing transitions out of sync.
   - **Fix**: Standardized all `block_lookup` dictionaries and `passage_lookup` tuples to cast the position identifier to `str()` before matching, guaranteeing reliable O(1) alignment between LLM plans and the audio spine.

3. **Music Ducking Dropped Without Prosody (`step_4_04_plan_sfx/post_bridge.py`)**
   - **Bug**: If `prosody_analysis` was absent or failed upstream, the bridge used an empty `pass` in the fallback block for speech segments. This caused `compute_ducking_curves` to receive an empty list, completely disabling music audio ducking during speech blocks.
   - **Fix**: Implemented a robust fallback that constructs `speech_segments` directly from `spine_blocks` (using `timeline_start` and `timeline_end` of speech blocks) when prosody analysis is missing, guaranteeing music ducking remains active.
