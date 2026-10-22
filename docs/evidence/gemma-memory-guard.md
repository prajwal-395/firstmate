# Gemma memory guard (2026-10-22): no second private copy

What happened: `library/tools/vision_model.py` fell back to loading
Gemma 4 12B in-process (~7 GB) whenever :8080 did not answer, with no
lock and no memory check. The captain force-quit three Python processes
over 7 GB each - three private copies of the same weights. A raw
`mlx_vlm server` also sat on :8080 for 9.5 h with no reaper. Firstmate
has since installed the shim agent (launchd holds :8080, backend on
demand, 210 s idle reaper; verified answering, backend stopped).

## Before: code paths loading Gemma in-process with no guard

| # | Load site | Entries | Guard |
|---|---|---|---|
| 1 | `vision_model.VisionModel._ensure_loaded` → `load(MODEL_ID)` | 3 (`analyze_image`, `analyze_images`, `analyze_video`, plus `grounding_adapters.gemma_text_answer` via `analyze_image`) | NONE - no lock, no memory check |
| - | `vision_pipeline_v3._load_mlx_model(MODEL_ID)` | step 1.03 batch | already admitted: `heavy_work_reservation` + `resident_model_admission` (scheduler RAM demand, live headroom, bounded recovery) |
| - | `ocr_extractor._get_mlx` | MLX OCR pass | different model (paddleocr-vl path), out of this lane |

**Before: 1 unguarded Gemma load site (3 public entries).**

Other in-process MLX loads (`grounding_adapters._load_molmopoint`,
`_load_qwen25vl`) are eval candidates for other models, not Gemma, and
are unchanged by this lane.

## After: the same count through the guard

* New `library/tools/gemma_inprocess_guard.py` (stdlib only): a
  machine-wide `flock` on `~/.local/share/vep/gemma-inprocess.lock`
  (env `GEMMA_INPROCESS_LOCK_FILE`), held open on the `VisionModel`
  instance while the weights stay resident, so at most one process ever
  holds Gemma in-process; and `check_memory()` refusing loudly below
  8 GB free+inactive (env `GEMMA_INPROCESS_MIN_GB`), unreadable included.
* `VisionModel._ensure_loaded` - the single funnel - runs both before a
  byte of weights is read. Refusals name the reading and the fix
  (`gemma up --prewarm`: serve once, not once per process).
* `tests/conftest.py`: the session refuses any unmarked test that
  reaches `_ensure_loaded` (`@pytest.mark.real_model` opts out).
* Repo scan: the only `mlx_vlm server` spawn is the shim's own backend
  (on :8081, behind the :8080 provider URL) - asserted by
  `test_no_raw_server_spawn_outside_the_shim`.
* `ren doctor` gains a `gemma shim agent` line (no need id: a missing
  agent warns, it never fails a capability) - green on the captain's
  machine 2026-10-22.

**After: 0 unguarded Gemma load sites (1 guarded funnel / 1 load site).**

## Validation (this lane loads no model: stubs only)

* `tests/unit/picture/test_gemma_memory_guard.py`: 10 passed
  (refusal below floor, refusal-is-MemoryError, unreadable refuses,
  plentiful passes, second holder refused, fd-close frees, guard runs
  before load, slot held while resident, provider-URL routing, no raw
  spawn scan).
* `tests/unit/picture/test_vision_model.py`: 6 passed (server-first
  behavior unchanged).
* `test_ren_cli.py -k "memory or doctor or shim or gemma"`: 6 passed.
* `ren doctor` `gemma_shim_check()` live: ok, shim answering, agent
  installed.

No suite runs in this lane (brief); no quality denominator changes -
the server-first path is untouched, and the fallback answers exactly
what it answered before wherever the guard admits it.
