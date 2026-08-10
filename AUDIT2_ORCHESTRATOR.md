# Round 2 Audit Report: Orchestrator and Shared Tools

## Overview
This document summarizes the findings and fixes from the second-pass exhaustive audit of the orchestrator (`run_pipeline.py`), DAG definition (`dag.json`), and step manifests (`manifest.json`) across the pipeline. The audit focused on verifying fixes from Round 1 and tracking actual data flow (reads/writes) across steps to ensure data contracts and runtime behaviors align correctly.

## 1. Verified Fixes from Round 1
The Round 1 audit successfully addressed:
- The orchestrator missing a fallback to `tools.paths` for library paths.
- Ancestor-aware skipping logic in `--from`.
- Timeouts crashing the orchestrator instead of triggering retries.
- Replaced implicit JSON merging with explicit `data_mapping` edges in the DAG.

## 2. Issues Discovered and Fixed in Round 2

While Round 1 added explicit `data_mapping` edges, it introduced severe type mismatches and mapping errors because it assumed incorrect structures for the runtime data. These errors silently broke the `context_projector` for LLM steps and caused deterministic steps to crash.

### A. Broken Data Mapping for `temporal_index`
The `temporal_index` step outputs a complex dictionary containing `temporal_event_indices` (array), `full_indices` (array), and `index_dir` (string).
- **Issue 1:** The edge `temporal_index` -> `prosody_analysis` mapped `temporal_event_indices` to `temporal_index`. `prosody_analysis` expects an object with both `index_dir` and `full_indices`. Passing it an array crashed the step.
  - **Fix:** Removed the `data_mapping` entirely for this edge. The orchestrator now defaults to merging all outputs into `inputs`, giving `prosody_analysis` exactly the dictionary it expects.
- **Issue 2:** LLM steps (`creative_direction`, `speech_sequence`, etc.) rely on `temporal_index.*.speech_regions` in their `context_fields`. However, `temporal_event_indices` does not contain `speech_regions`—`full_indices` does.
  - **Fix:** Updated the DAG mapping for LLM steps to `"full_indices": "temporal_index"`.

### B. Widespread Manifest Type Mismatches
The pipeline outputs JSON arrays for many collections, but step manifests incorrectly declared them as `object`. This caused the orchestrator's `validate_step_output` to fail or mask real issues.
- **Issue:** Outputs like `semantic_analysis_documents`, `clip_catalog`, `temporal_event_indices`, and `b_roll_assignments` are arrays, but were declared as objects. Conversely, `speech_sequence` outputs an object, but downstream steps declared it as an array.
- **Fix:** Systematically updated `type` declarations across 12 manifests to align exactly with runtime output:
  - `clip_catalog` → `array`
  - `semantic_analysis_documents` → `array`
  - `temporal_index` (when mapped from `full_indices`) → `array`
  - `temporal_event_indices` → `array`
  - `b_roll_assignments` → `array`
  - `speech_sequence` → `object`

### C. Context Projector Field Typos
The `context_projector` extracts specific subsets of data for LLM prompts. Several `context_fields` definitions were broken because they expected structures that didn't exist.
- **Issue 1:** Steps requested `clip_catalog.entries.*.filename`, assuming `clip_catalog` was an object with an `entries` array. `clip_catalog` is just an array.
  - **Fix:** Updated `context_fields` in `creative_direction`, `assign_aroll`, and `select_broll` to `clip_catalog.*.filename` (and similar fields).
- **Issue 2:** `creative_direction` requested `temporal_index.*.transcripts`, but the key in `full_indices` is `speech_regions`.
  - **Fix:** Corrected to `temporal_index.*.speech_regions`.

## 3. Runtime Behavior Validation
- The `context_projector` (`_project_single_path`) robustly handles `*` wildcards against `list` data types, so mapping `full_indices` (array) directly to `temporal_index` perfectly satisfies `temporal_index.*.field`.
- The `compile_manifest_from_inputs` function in `step_5_04_compile_manifest` was manually audited. It correctly relies on `a_roll_assignments` to build its `clip_lookup` (ignoring `clip_catalog` when run by the orchestrator), ensuring no runtime crashes despite the dual file/orchestrator loading mechanisms.

## Conclusion
The orchestrator and shared tools data contracts are now fully sound. The explicit DAG mappings correctly route arrays/objects, the manifests accurately validate their structures, and the context projector seamlessly builds LLM context without silent failures.
