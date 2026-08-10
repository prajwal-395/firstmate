# Convergence Audit: Phase 5 & 6

## Findings
- **Pipeline Logic Bug 1**: In `resolve_build_timeline.py`, native transitions (J/L cuts) were using `from_block` and `to_block` (spine block IDs) directly as array indices to look up clips in `v1_clips`. This causes the wrong clips' audio to be trimmed, because `v1_clips` only contains speech/hook blocks and skips other block types.
- **Pipeline Logic Bug 2**: In `step_5_04_compile_manifest/step.py`, fusion transitions were also assigning `after_clip = t.get("from_block", ti)`. `apply_fusion_comps.py` later assumed `after_clip` was an array index for `v1_clips`, applying fusion effects to the wrong clip.
- **Pipeline Logic Bug 3**: In `step_5_01_color_grade/step.py`, `b_roll_interjections` were completely omitted from the `entries` list, meaning they skipped per-clip exposure and grading analysis.

## Fixes Implemented
1. `resolve_build_timeline.py`: Mapped `from_block` and `to_block` IDs to actual `v1_clips` array indices by matching `clip['label'].endswith(f"_{block_id}")`.
2. `step_5_04_compile_manifest/step.py`: Updated transition compilation to resolve `from_block` IDs to `v1_clips` indices in both filesystem mode and orchestrator mode.
3. `step_5_01_color_grade/step.py`: Appended `b_roll_interjections` alongside `b_roll_assignments` when building the `entries` list for the color grade spec.

## Conclusion
All logic bugs found were fixed.
