# Pipeline Logic Bugs Audit (Phase 3 & 4)

During the audit, the following pipeline logic bugs were found and fixed:

1. **`step_4_06_render_motion_graphics/step.py` (Motion Graphics Rendering Bug)**
   - **Bug:** The render list was truncated (`props_list = props_list[:1]`), limiting rendering to only the very first segment.
   - **Fix:** Removed the truncating slice so all motion graphics segments are rendered correctly.

2. **`step_4_03_plan_vfx/post_bridge.py` (Manifest Schema Compliance Bug)**
   - **Bug:** The output `enhancement_spec` was returning a direct list of effects (`{"enhancement_spec": result}`). The manifest schema and downstream steps expect a dictionary grouping by effect type (e.g., `{"enhancement_spec": {"visual_effects": result}}`). This caused downstream data malformation.
   - **Fix:** Wrapped the `result` inside `{"visual_effects": result}`.

3. **`step_4_02_plan_transitions/post_bridge.py` (Transition Planning Crash)**
   - **Bug:** The code indexed blocks directly using `outgoing = block_lookup.get(pos - 1, {})` expecting `pos` to be an integer. Spine block positions are occasionally strings (e.g. `"body_1"`), leading to a `TypeError` at runtime.
   - **Fix:** Refactored the array lookup to find the block's true list index in `spine_blocks`, providing robust access to the incoming/outgoing blocks.

4. **`step_3_02_select_broll/post_bridge.py` (B-Roll Duration Bug)**
   - **Bug:** `block_duration` was extracted directly from `duration_seconds`, which can be stale if the `timed_spine` step shifted the timestamps.
   - **Fix:** Updated the calculation to dynamically resolve `block_duration = timeline_end - timeline_start` for robust syncing.
