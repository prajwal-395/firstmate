# Convergence Audit: Phase 3 and Phase 4

## Scope
Phase 3 (steps 3.01-3.03), Phase 4 (steps 4.01-4.04), and Remotion subtitles.
Focus: Pipeline logic bugs only.

## Findings & Fixes

1. **Bug**: In `step_4_03_plan_vfx/post_bridge.py`, `covered_positions` was storing integer positions from the LLM but checking against string representations (or vice-versa) from `block.get("position")`. This caused the check `pos in covered_positions` to fail, resulting in the pipeline incorrectly injecting default Ken Burns zooms onto clips that already had VFX assigned.
   **Fix**: Standardized `covered_positions` set and lookups to use string casting `str(pos)` to guarantee consistent matching.

2. **Bug**: In `step_4_02_plan_transitions/post_bridge.py`, a similar type mismatch existed with `existing_cuts.add(pos)`. The LLM's `pos` (string) was checked against `curr_block.get("position", i)` (int), leading to duplicate transitions being erroneously inserted at scene boundaries.
   **Fix**: Standardized `existing_cuts` insertion and lookups to use string casting `str(pos)`.

3. **Bug**: In `step_4_02_plan_transitions/post_bridge.py`, `spine_blocks` were iterated using `b["timeline_start"]` and `b["position"]`. If a spine block lacked these fields, it would raise a `KeyError` and crash the bridge.
   **Fix**: Replaced bracket notation with `.get("timeline_start", 0.0)` and `.get("position")` to handle missing fields gracefully.

4. **Bug**: In `step_4_05_render_subtitles/generate_remotion_props.py`, `block_groups.keys()` was sorted natively (`sorted(block_groups.keys())`). Because spine block positions can be a mix of strings (e.g., `"hook"`) and integers (e.g., `1`), Python 3 throws a `TypeError: '<' not supported between instances of 'int' and 'str'`, crashing the subtitle render process.
   **Fix**: Added `key=str` to the `sorted()` call to ensure determinism without type crashes.

5. **Bug**: In `step_4_04_plan_sfx/post_bridge.py`, the `_avoid_speech_collision` function miscalculated speech gaps. Because it only had access to `word_end_times`, it treated the space between `word_end_time[i]` and `word_end_time[i+1]` as a silent gap, completely ignoring that the space is predominantly occupied by the spoken word ending at `i+1`. This caused SFX to be placed directly on top of active speech.
   **Fix**: Subtracted an estimated word duration (0.2s) from the next word's end time (`gap_end = sorted_times[i+1] - 0.2`) to approximate the true silence gap before the word.

All logic bugs found have been fixed and committed.
