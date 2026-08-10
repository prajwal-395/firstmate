# Phase 3 & 4 Convergence Audit Report

## Pipeline Logic Bugs Found and Fixed

1. **Step 3.01 A-Roll Duration Invariant Violation**
   - **File:** `library/steps/step_3_01_assign_aroll/step.py`
   - **Bug:** When adjusting A-roll video segments to prevent hook overlap, if the overlap entirely consumed the segment (duration <= 0), the script printed a warning but left the invalid segment in the `video_segments` list. This resulted in negative/zero duration source segments being passed downstream, causing a fatal duration mismatch in Step 3.3 (Rough Cut Review) and breaking XMEML generation.
   - **Fix:** Added a list comprehension to filter out segments where `duration_seconds <= 0` before appending to `a_roll_assignments`.

2. **Step 4.01 Multi-Segment Subtitle Overlap**
   - **File:** `library/steps/step_4_01_plan_subtitles/step.py`
   - **Bug:** For speech blocks containing multiple jump-cut segments, the subtitle generation loop calculated the timeline offset using the entire block's `timeline_start` (`offset = block_start - v1_src_in`) for every segment. This caused subtitles from all subsequent segments in the block to be placed concurrently at the beginning of the block, overlapping each other on screen.
   - **Fix:** Tracked the `current_tl_pos` incrementally across segments (advancing by `source_dur`) and used it to calculate the offset relative to each segment's actual position in the timeline.

## Conclusion
The bugs were fixed and the changes were committed.
