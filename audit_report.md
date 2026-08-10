# Phase 5 and 6 Convergence Audit Report

PIPELINE LOGIC BUGS FOUND AND FIXED:

1. `step_5_04_compile_manifest/step.py`:
   - Crash (KeyError) when processing `b_roll_interjections`. In filesystem mode (`compile_manifest`), it used `clip = interj["assigned_clip"]` and `clip["video_in"]`. This causes a fatal crash if `assigned_clip` is missing or if the video bounds are defined on `interj` itself (which is often the case). It was updated to correctly resolve the clip via `interj.get("assigned_clip", interj)` and properly retrieve `video_in` and `video_out`.

2. `library/steps/step_6_01_render/resolve_build_timeline.py`:
   - Logic error in native transition parsing. When parsing a transition position (e.g., `between_1_2`), the code did `from_idx = int(parts[0]) - 1`. However, the timeline clips are labeled directly with the position number (e.g., `speech_1`). The erroneous subtraction caused `from_idx` to become `0` instead of `1`, resulting in the `endswith(f"_{from_idx}")` search silently failing to match any clip. The `from_clip_idx` and `to_clip_idx` would be `None`, and all native J-cut and L-cut transition logic would be entirely skipped. The `- 1` subtractions were removed to correctly align with the block labels.
