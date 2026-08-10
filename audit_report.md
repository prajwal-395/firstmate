# Convergence Audit: Phase 5 & 6 Pipeline Logic

## Findings

1. **Bug Found:** Data loss and silent gaps in J-cut and L-cut handling inside `resolve_build_timeline.py`.
   - **Details:** The timeline builder's logic for pre-processing J-cuts decreased `audio_src_out` of the preceding clip by the full transition duration, while bounding the next clip's `audio_src_in` adjustment to its available head media `max(0, ... - dur)`. This discrepancy created a silent gap in the A1 track when the next clip didn't have enough head media. Similarly, L-cuts extended `audio_src_out` of the preceding clip and `audio_src_in` of the next clip without capping them to the actual media limits, which could cause Resolve's `AppendToTimeline` to fail or create gaps.
   - **Fix:** Implemented proper clamping. `actual_dur` is now strictly bound by the available media limits of both clips (`from_clip_len` and `audio_src_in`/`to_clip_len`), ensuring that both ends of the cut adjust symmetrically without violating media bounds.

2. **Bug Found:** Silent failure to populate `clip_lookup` in `step_5_04_compile_manifest` (orchestrator mode).
   - **Details:** When compiling the manifest from upstream JSON inputs, the `a_roll_assignments` processor iterated over `video_segments` and fell back to `vseg.get("clip_id")` and `vseg.get("source_file")`. However, it failed to inherit `source_clip_id` or `source_file` from the parent `assignment` object if the `vseg` was sparsely populated. This caused `cid` or `path` to be empty strings, skipping the `clip_lookup` registration entirely and causing missing media downstream.
   - **Fix:** Updated the fallback lookup logic to `assignment.get("source_clip_id", assignment.get("clip_id", ""))` and `assignment.get("source_file", "")`.

Both logic bugs have been fixed and committed. No further pipeline logic bugs were found in Phase 5, Phase 6, or execution tools.
