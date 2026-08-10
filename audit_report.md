# Convergence Audit - Phase 1 & 2

## Pipeline Logic Bugs Found and Fixed

1. **Audio Cache Collision in Temporal Indexing (`step_1_04_temporal_index/step.py`)**
   - **Bug**: `extract_audio_16k` used the `video_path`'s basename to construct the cached `.wav` filename. If a project had multiple raw files with the same name (e.g. `raw/cam_a/IMG_001.MOV` and `raw/cam_b/IMG_001.MOV`), they would overwrite each other in the `audio_cache` directory, causing silent data loss where the audio processed for the second file actually belonged to the first file.
   - **Fix**: Modified `extract_audio_16k` to take `clip_id` (e.g., `clip_001`) as an argument and use it as the base filename for caching, which is guaranteed to be unique.

2. **Incorrect Total Duration Validation (`step_2_02_speech_sequence/post_bridge.py`)**
   - **Bug**: The `total_duration` calculation only summed the durations of the `body_sequence` passages. It completely ignored the `hook_segment` duration. This caused the calculated total speech duration to be artificially lower than the true duration, which could lead to incorrect validation bounds failures.
   - **Fix**: Included the `hook_segment` (if present) in the `total_duration` calculation before summing the body passages.
