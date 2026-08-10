# Orchestrator & Tools Audit Report

## Issues Found

### 1. `library/processes/edit_video/run_pipeline.py`
- **Severity**: HIGH
- **Line**: 36-39
- **Issue**: `_is_transient_error` fails to properly detect `subprocess.TimeoutExpired` errors. It relies on checking if `"timeout"` is a substring of the lowercased error string `str(e).lower()`. However, the string representation of `subprocess.TimeoutExpired` returns something like `"command '...' timed out after X seconds"`, which does NOT contain `"timeout"`. This causes actual timeout errors to not trigger retries as intended.
- **Fix**: Modified `_is_transient_error` to use `isinstance(e, subprocess.TimeoutExpired)` or explicitly look for `"timed out"` as a substring.
- **Status**: FIXED

### 2. `library/processes/edit_video/dag.json`
- **Severity**: CRITICAL
- **Line**: Various
- **Issue**: Missing critical data dependencies (edges) that are explicitly required by step manifests. Without these edges, the orchestrator does not correctly route the necessary data outputs of previous steps to the inputs of downstream steps. `test_dag.py` revealed these critical gaps:
  - `music_analysis` is a completely disconnected node (no outbound edges), yet it is explicitly required by `mesh_spine` and `plan_sfx`.
  - Dozens of edges are missing for `prosody_analysis`, `speech_sequence`, `mesh_spine`, `select_broll`, `review_rough_cut`, `plan_subtitles`, `plan_transitions`, `plan_vfx`, `plan_sfx`, `color_grade`, and `audio_mix`.
- **Fix**: Programmatically inspected all manifests to discover the `state.reads` requirements, then patched `dag.json` by adding the corresponding correct outbound mappings from the source nodes that produce these required keys.
- **Status**: FIXED

### 3. `manage_project.py`
- **Severity**: HIGH
- **Line**: 360-362 and 195-197
- **Issue**: The CLI fails to expose or handle the `--resume` flag for the `run` command, despite `run_pipeline.py` supporting it and `AGENTS.md` explicitly advertising its usage ("Use `--resume` to continue the pipeline after a review gate is approved or revised."). As a result, attempting to resume the pipeline with `python manage_project.py run <slug> --resume` crashes with an unhandled argument error.
- **Fix**: Added `--resume` as a boolean flag to the `p_run` subparser and updated `cmd_run` to properly forward `--resume` to `run_pipeline.py` via `cmd.append("--resume")`.
- **Status**: FIXED

## Audit Coverage Note
All 12 requested components (orchestrator python files, DAG/manifest files, management CLI, shared tool definitions, schema configurations) have been evaluated for correct logic, routing, data-shapes, and error-handling. The critical edge routing failure in DAG generation and the broken timeout transient policy have been fixed.
