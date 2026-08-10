# Convergence Audit Report - Phase 3 & 4

The following pipeline logic bugs were found and fixed:

1. **Duration Invariant Violation (Step 3.01)**
   The hook overlap guard in `step_3_01_assign_aroll/step.py` was adjusting `video_in` and trimming `duration_seconds` for body segments to avoid repeating the hook video. However, it failed to adjust `timeline_start` of the overall block, creating a discrepancy between the source duration and the timeline allocation. This triggered a fatal invariant failure in `step_3_03_review_rough_cut`. Since the video trim also resulted in black frames against the unchanged audio spine, the guard was removed.

2. **Subtitle Segment Sourcing & Progression (Step 4.01)**
   In `step_4_01_plan_subtitles/step.py`, two bugs were fixed:
   - **Incorrect `v1_source_in` scope**: The offset calculation checked `content.get("v1_source_in")`, which pulled the value from the block-level content, causing all segments within a multi-segment block to use the first segment's `v1_source_in`. This was corrected to check the segment first.
   - **Timeline gap tracking**: The loop advanced `current_tl_pos` by `source_dur` even when the segment was clamped to the block's boundaries (e.g. `seg_tl_dur`). This was corrected to advance by `seg_tl_dur` to prevent gaps or pushing segments beyond the block end.

3. **Transition Duplication (Step 4.02)**
   In `step_4_02_plan_transitions/post_bridge.py`, the `inject_default_transitions` logic verified existing transitions by checking for `t.get("cut_point_position")`. If the LLM omitted this field and used `cut_point_original` instead, the existing cut was missed, causing the default injection logic to append a duplicate cross-dissolve to the same slot.

4. **VFX Target Position Fallback (Step 4.03)**
   In `step_4_03_plan_vfx`, the `bridge.py` prompts the LLM using `segment_id` in the TOON table, but `post_bridge.py` exclusively checked for `target_block_position`. This caused all LLM-planned VFX to fail the lookup and fall back to `0.0` on the timeline. It was fixed to check `target_block_position` with a fallback to `segment_id`.

No other logic bugs were found in Remotion subtitles or other steps in Phase 3 and 4.
