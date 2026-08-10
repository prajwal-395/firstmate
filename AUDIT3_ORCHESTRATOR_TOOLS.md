# THIRD-PASS Audit Report: Orchestrator & Tools

## Scope
- run_pipeline.py
- dag.json
- manifest.json
- manage_project.py
- library/tools/
- library/schemas/
- tests/

## Findings & Fixes (Bugs)

1. **`tests/test_pipeline.py` pytest collection failure (Bug)**
   - **Issue:** The `tests/test_pipeline.py` file executes a module-level `sys.exit(1)` when the `PIPELINE_TEST_PROJECT` environment variable is not set and no test projects are found. This causes `pytest` collection to fail immediately on a clean checkout, breaking the entire test suite.
   - **Fix:** Wrapped the module-level exit in a check for `pytest` in `sys.modules`, allowing pytest collection to complete. The tests will naturally skip since the module loads but skips themselves when `PROJECT_DIR` is empty.

2. **`tests/test_resolve_build_timeline.py` Mock Serialization Error (Bug)**
   - **Issue:** Tests `test_clip_placement_calculations` and `test_two_pass_architecture` failed because `MagicMock` instances were leaking into the `manifest` dictionary via `GetDuration()` and `GetStart()` calls. The test then attempted to `json.dump(manifest)` (simulating the handoff to `apply_fusion_comps.py`), resulting in a `TypeError: Object of type MagicMock is not JSON serializable`.
   - **Fix:** Patched the test fixtures (`side_effect_append` and `placed_item`) to return integers for `GetDuration()` and `GetStart()` instead of returning standard MagicMocks.

## Style / Preference Issues (Not Fixed)

1. **Silent Pass Exceptions**
   - Several files use empty `pass` blocks in `except Exception:` handlers. 
   - Files affected: `render_qa.py`, `timeline_serializer.py`, `run_pipeline.py` (e.g. `ImportError` or `FileNotFoundError` fallbacks).
   - This appears intentional to allow the pipeline to proceed without certain optional dependencies or metadata properties (e.g., ignoring missing metadata during serialization). Left as-is per protocol.

2. **Swallowed Exceptions logging to stderr**
   - In `run_pipeline.py`, exceptions for loading the brand template or creative brief are caught, printed as a warning to `sys.stderr`, and the script continues.
   - Since the pipeline might be intended to fall back gracefully, this was treated as a preference rather than a bug.

## Conclusion
Pass completed. Bugs were found and fixed in the test suite setup and test fixtures.
