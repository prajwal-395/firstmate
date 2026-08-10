# Convergence Audit: Phase 3 & 4 (Pipeline Logic)

## Findings & Fixes

1. **Step 3.01 (Assign A-Roll) - Missing Hook in Assignments**
   - **Bug**: The `hook` block was not appended to `a_roll_assignments`. Since subsequent steps (`step_3_02`, `step_4_03`, etc.) iterate over `a_roll_assignments` to plan B-roll and VFX, the hook block was silently dropped from creative enhancement consideration. It also bypassed duration validation in Step 3.03.
   - **Fix**: Updated `step.py` to append the `hook_assignment` with properly formatted `video_segments` into `a_roll_assignments`, and adjusted the verification assertion.

2. **Step 3.03 (Review Rough Cut) - Tolerance Mismatch**
   - **Bug**: `DURATION_TOLERANCE` was strictly `0.1` in Step 3.03 but `0.15` in Step 3.01. A valid assignment from 3.01 with a delta of `0.12` would pass generation but falsely fail the rough cut review, halting the pipeline.
   - **Fix**: Aligned `DURATION_TOLERANCE` to `0.15` in `step_3_03_review_rough_cut/step.py`.

3. **Step 4.01 (Plan Subtitles) - Silent Drop of Word Timestamps**
   - **Bug**: Segment parsing expected `position = seg.get("position")`. For legacy spine formats, this could be missing at the segment level, causing `passage_lookup` to fail and silently drop word timestamps.
   - **Fix**: Added fallback logic: `seg.get("position", content.get("passage_ref", block.get("position")))` to ensure the passage position is always identified.
   - **Bug**: Inline clamping of subtitles to the block end could result in a zero or negative duration if `last["timeline_start"]` was forced to `prev["timeline_end"]`.
   - **Fix**: Added a safeguard that guarantees at least 0.1s positive duration if clamping forces overlap.

4. **Step 4.02 (Plan Transitions) - KeyError on Missing Duration**
   - **Bug**: Macro transitions accessed `selected_trans["duration_ms"]` via bracket notation. If the transition selector failed to supply it, this would cause an unhandled `KeyError`.
   - **Fix**: Changed to `selected_trans.get("duration_ms", 500)`.

## Status
BUGS FOUND AND FIXED.
