# CONVERGENCE AUDIT: Phase 5 & 6

## Findings: Pipeline Logic Bugs Found and Fixed

1. **Bug:** Silent failure to apply VFX, Fusion Presets, and Transitions to segmented clips.
   - **Location:** `library/steps/step_5_04_compile_manifest/step.py` and `library/steps/step_6_01_render/resolve_build_timeline.py`
   - **Description:** Targeting logic used `label.endswith(f"_{block_num}")` to find the corresponding clip for a block. When a block was split into multiple video segments (e.g., `speech_1_seg0`, `speech_1_seg1`), the `endswith` check failed to match because the label ended with `_segX`. This caused VFX timeline ranges, Fusion preset assignments, Fusion transition lookups, and native J/L cut lookups to silently ignore these clips, dropping the enhancements completely.
   - **Fix:** Updated the matching condition to `label.endswith(f"_{block_num}") or f"_{block_num}_seg" in label` across both files.
   
2. **Bug:** Incorrect start/end timeline calculation for VFX on segmented blocks.
   - **Location:** `library/steps/step_5_04_compile_manifest/step.py`
   - **Description:** When mapping VFX to a block with multiple segments, the previous logic matched the first segment and used its `timeline_in` and `timeline_out`, ignoring the rest of the segments in that block.
   - **Fix:** Changed the logic to find all matching segments for the block. The `timeline_start` is now correctly set to the first segment's `timeline_in`, and the `timeline_end` is set to the last segment's `timeline_out`.

3. **Bug:** Incorrect clip targeting for transitions from segmented blocks.
   - **Location:** `library/steps/step_5_04_compile_manifest/step.py` and `library/steps/step_6_01_render/resolve_build_timeline.py`
   - **Description:** For transitions originating `from_block`, the code incorrectly grabbed the FIRST matching clip instead of the LAST segment of the block, meaning the transition would be placed prematurely between internal segments of a block rather than at the cut between blocks.
   - **Fix:** Modified the loops to continue until the LAST matching segment of the `from_block` is found, ensuring transitions are placed correctly at the end of the block.

All fixes have been implemented and committed.
