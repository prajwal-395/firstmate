# Convergence Audit Report - Phase 1 & 2

**Scope:** Phase 1 (1.01-1.07) and Phase 2 (2.01-2.05)
**Status:** 3 pipeline logic bugs found and fixed.

## Bug 1: Silent data drop via basename collision in Semantic Analysis (1.03)
**Issue:** `step_1_03_semantic_analysis/bridge.py` used `clip_name` extracted from the file's basename (e.g., `IMG_1806`) rather than the centrally assigned `clip_id`. When two files had the same basename but different extensions (e.g., `IMG_1806.mp4` and `IMG_1806.MOV`), the vision pipeline output (`clip_profile_{basename}.json`) for the latter would silently overwrite the former. The `clip_id_map` also suffered from the same key collision.
**Fix:** Modified the bridge to rename the output of the vision pipeline immediately to use `clip_id` (e.g., `clip_profile_{clip_id}.json`) and refactored the mapping and collection logic to use `clip_id` robustly.

## Bug 2: Local clip_id generation causing desync in Prosody Analysis (1.05)
**Issue:** `step_1_05_prosody_analysis/step.py` was generating `clip_id` locally using `f"clip_{i + 1:03d}"` where `i` is the enumerate index of `raw_footage_files`. If files were skipped or the list length changed upstream, this would misalign `clip_id` values with the authoritative ones generated in step 1.01, breaking data joins downstream.
**Fix:** Updated to retrieve `clip_id` from the incoming `raw_footage_files` item (i.e., `item.get("clip_id")`) with the fallback only if missing.

## Bug 3: Output collision via basename in OCR Extraction (1.07)
**Issue:** `step_1_07_ocr_extraction/step.py` also relied on `clip_name` (the basename) to generate output subdirectories and to key the final `ocr_results` dictionary. Like Bug 1, this caused silent overwrites for identically named files.
**Fix:** Changed all directory generation and result dictionary keys to use `clip_id` instead.
