# Analysis scheduling and reuse profile: fixed 17-clip set, cold (partial)

Date: 2026-10-08
Base commit: `bcfb12b0` plus this PR's worktree (all committed under this PR)
Machine: Darwin 25.3.0 arm64, macOS 26.3, 24 GiB RAM
Fixed set: disposable copy of project `001`, 17 clips
Source-memory root: isolated, initially empty
Harness: opencode worker - no image-capable still host is declared for
  this harness (`still_vision.HOST_SEES_IMAGES`), so object stills fall
  back to local Gemma inference behind the serial inference lock. This
  is the honest worst case for this machine: every window and every
  still is a local `mlx-community/gemma-4-12b-it-4bit` call, one at a
  time.

Command (cold; the warm full rerun was NOT performed - firstmate steer,
"do not start another full run"):

```sh
PIPELINE_STILL_VISION_MAX_CONCURRENCY=10 \
PIPELINE_SOURCE_MEMORY_ROOT=/path/to/isolated/source-memory \
bin/vep -m library.tools.operations semantics.analyse \
  --project /path/to/disposable-copy-of-001
```

Run through the home's `state/vep-verify-run.sh` slot. Never point
this command at a real project.

## Result

The cold run did not finish: it was stopped at 6h08m wall on firstmate
steer after two scheduler stalls (findings below), with 6 of 17 clips
touched and 3 profiles completed. The numbers are still the profile
the brief asked for - where wall time goes - measured, not estimated:

| Component | Time | Share of 22,072 s wall | Source |
|---|---|---:|---|
| Local Gemma inference, granted (`gemma_inference` spans: 8 window incl. 2 empty-then-retried, 3 object-coarse, 14 still-fallback) | 15,002.9 s | 68% | perf ledger, run `20261008T035532-44382` |
| of which video windows (8 spans, 6 parsed + 2 empty-then-retried) | 13,528.1 s | 61% | same |
| of which object stills (3 coarse parsed + 14 local-fallback) | 1,474.1 s | 7% | same |
| Host still calls (`host_model`) | 0 s | 0% | same - no image host declared for this harness, by design |
| Model load (3 spans) | 18.9 s | 0.1% | same |
| Video decode / frame extraction (`demux` spans 5.7 s; 31 ffmpeg/ffprobe children totaling 8.9 s observed) | ~9 s | <0.1% | ledger + process tree |
| Vision-child subprocess startup (spawn to first span; children themselves lived 3,200 s / 3,600 s / 15,267 s, almost all of it queue-wait) | seconds | ~0% | `processes-cold2.jsonl` |
| Duplicate passes skipped | 0 (all 17 clips remaining at start; no byte-identical pairs in this set) | - | run stdout |
| Image conversions | no direct spans; inside inference (PIL still prep per call, unmeasured, small beside 100-600 s inferences) | - | - |
| Queue-wait on the heavy-work scheduler (wall minus granted) | ~7,045 s | 32% | derived |

Single slowest verified inference: one 10 s window at 303.2 s granted
(`IMG_1807:window:0.000-10.000:compact`, parsed). Decode, extraction,
startup and conversions are noise beside inference and queue-wait:
the scheduling problem is who waits for the model, not the plumbing
around it.

## Stall findings

1. Dead queue entry (firstmate diagnosis, inbox 006): a dead queue
   entry from a broker restart firstmate had stopped was never reaped
   because the Mac's hostname changed, so Gemma inference sat queued
   for hours behind a holder that no longer existed. Ledger spans by
   hour show granted work 03:5x-05:5x, then ZERO spans 06:xx-09:1x,
   then a fresh span landing right after firstmate cleared the entry.
   Lesson for scheduling work: the resource-scheduler reap must key
   queue entries by something stable across a hostname change, or a
   restarted broker must drain its queue; otherwise one dead row
   serializes every Gemma-heavy lane on the machine behind nothing.
2. FIFO memory/inference interlock (firstmate diagnosis, inbox 008,
   scheduler jobs list 10:01): this run holds `Gemma semantic model
   memory` (8 GB resident) and requests `Gemma semantic inference`
   per clip; the workflow-qualification lane's full-machine
   footage-analysis queued between, waits for the memory, and the FIFO
   queue puts the next inference behind it, so neither moves (both
   sides showed ~zero CPU growth for 40+ minutes: e.g. footage job
   54861 at 53 min / 8.78 s CPU; vision child at 29 min / 28 s CPU).
   An earlier identical wedge (vision child 44961, 53 min / 2m27 s
   CPU) was stopped by exact pid per steer; the parent survived,
   skipped the killed batch and resumed on a fresh child. Lesson: a
   long-held memory reservation plus per-unit inference requests
   FIFO-deadlocks against a full-machine waiter - the grant needs
   either preemption, a timeout that actually fires on this shape, or
   admitting inference for the holder while its memory is resident.
3. Crash fix that made any of this measurable: the cold run first died
   on `merge_objects` doing `float(None)` on a null appearance
   timestamp, which discarded the whole 17-clip batch. The guard drops
   only null/nonnumeric/absurd ranges and is unit-tested
   (`tests/unit/context/test_vision_pipeline.py`).

## Reuse evidence (without a second full run)

- Per-window LayerCache entries are keyed by `(source digest, method
  digest, params)` under the isolated source-memory root and survived
  the 44961 kill: the replacement child finished clips 1806-class
  work from cache instead of re-measuring, and 13+ measurement files
  persist content-keyed on disk.
- 3 stem-keyed clip profiles (`clip_profile_<stem>_v3.json`: IMG_1806
  03:58, IMG_1807 04:04, IMG_1810 05:54) mean any rerun reports them
  `already analyzed` and measures only the missing clips - the 1808
  (killed batch) and 1809 (timeout) skips are picked up, never redone
  from scratch.
- The two stages that still re-ran or serialized unnecessarily are
  fixed in this PR (see below); warm-run timing stays a follow-up.

## Stage inventory (what was still serial / re-analyzing)

Full table in the audit doc (`2026-10-08-analysis-scheduling-reuse-audit.md`).
Two stages were still serial or re-analyzed seen content, fixed here:

1. `step_1_02_catalog_footage.catalog_footage` / `_catalog_audio`:
   serial per-file ffprobe loop, no reuse. Now probed over
   `concurrency_routing.run_lanes` (input order preserved, identical
   sequence to the serial loop; covered by a new parallel-probing
   test in `tests/unit/context/test_catalog_ingest.py`).
2. `step_1_06_object_segmentation.run_step`: serial SAM 2 loop with no
   cache (saved only by its usually-empty trigger). Now content-keyed:
   a stored matte whose `source_identity` (content digest + size, face
   box, sample rate) matches is served from disk, and the segmenter
   (SAM 2 weights) only loads when a clip actually needs measuring
   (5 new tests in `tests/unit/picture/test_object_segmentation.py`).

Still serial by design (provider / memory limits, not oversights):

- Local Gemma inference: one worker behind `_LOCAL_GEMMA_INFERENCE`
  and the `semantics.analyse:inference` heavy-work grant - the 12B
  model's ~8 GB residency is the budget.
- 1.06 segmentation stays a serial loop when it measures: the
  generator and propagation share the GPU budget.
- Detail-after-coarse ordering in the host schedule: detail ranges
  are built from merged coarse objects.

## Quality check

No adaptive model selection is introduced: no same-clip bakeoff
supports it, so it stays a follow-up (audit doc). Observation quality
is unchanged by construction:

- The `merge_objects` guard drops only ranges that previously crashed
  the batch; valid ranges measure exactly as before.
- Catalog entries sort and verify exactly as before (identical input
  order into the same sort/verify).
- Segmentation reuse serves bit-identical stored mattes keyed by
  content digest.

Validation: `scripts/select_dependent_tests.py` over the three changed
library files selected 73 files; `pytest -n 2`: 1879 passed, 1 skipped.
