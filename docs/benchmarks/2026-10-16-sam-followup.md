# SAM follow-up: track-model code + source-verified candidates (2026-10-16)

Follow-up to [2026-10-15-sam-tracking-bakeoff.md](2026-10-15-sam-tracking-bakeoff.md)
(PR 2628, merged: retain PyTorch SAM 2.1 Hiera Small; reject the tested
SAM 2.1 MLX implementation for the measured use). Machine-readable:
[`2026-10-16-sam-followup-track-model.json`](2026-10-16-sam-followup-track-model.json).

No model was loaded for this follow-up: no footage is mounted in this
worktree (captain MXF absent, prior scratch clips removed), `coremltools`
is absent, and SAM 3 / 3.1 weights are absent (the 3.49 GB full-precision
copy was removed per download conditions; the local `sam3-8bit` cache
holds a ref only). No download was made. A captain hold on heavy model
runs is now in force; the measurement half waits on it.

## What already existed (not rebuilt)

- Multi-object tracks: face seed plus N detector seeds, or up to
  `MAX_TRACKED_OBJECTS` blobs, each with time-indexed `frames`,
  `bboxes` and optional RLE masks (`ObjectSegmenter.segment_clip`).
- Migration carries track geometry, not masks, into the model; RLE
  blobs stay in the 1.06 file by reference. Entities carry
  `"identity_scope": "asset"` (clip-local by construction).
- Region queries resolve tracks per entity with explicit gaps;
  unknown names stay absences; visibility observations name gap
  ticks and missing frames.

## What this follow-up adds (code, no model pass)

- `track_fragments` (`library/tools/analysis/object_segmentation.py`):
  a track's sampled frames as continuous runs plus explicit gaps
  (OBJ-016/018). Empty means a whole-clip gap (unmeasured, never
  invisible); out-of-clip positions refuse.
- Clip-local IDs pinned: the same bare id in two clips resolves per
  file; a file-less reference refuses (`test_project_model_masks.py`,
  OBJ-021).
- Selective mask storage pinned: a maskless segmentation file still
  migrates full geometry (samples, visibility, appearance, queries)
  while the matte path refuses with `UnresolvableMask`
  (`test_open_vocabulary_objects.py`, OBJ-024/025).

Decision: **adopt** the track-model code (existing interfaces,
incumbent untouched; no default change, so no incumbent contest owed).

## Source-verified candidates (not measured - research-only)

| Candidate | Source evidence | Blockers | Decision |
|---|---|---|---|
| EdgeTAM Core ML | Official repo `facebookresearch/EdgeTAM` (Apache-2.0 code+checkpoints); `coreml/export_to_coreml.py` yields 3 models (encoder ~9.6MB, prompt ~2MB, decoder ~8MB); published 16 FPS on iPhone 15 Pro Max, 22x vs SAM 2 (context, not Ren-measured) | `coremltools` absent locally; no footage; model-run hold | **research-only** |
| `sam3.cpp` Metal | `PABannier/sam3.cpp` (MIT code); converters + GGML zoo for SAM 2/2.1/3/EdgeTAM; Metal backend; published M4 Pro numbers (e.g. sam2.1-hiera-small-f16 0.9 s/frame, EdgeTAM-f16 0.4 s/frame - context only). Converted weights inherit Meta's SAM checkpoint license (license lane to resolve) | native build + GGML download + footage; model-run hold | **research-only** |
| SAM 3.1 MLX quantized | Prior full-precision attempt interrupted under memory pressure (PR 2628); 8-bit weights absent | weights + footage + memory; model-run hold | **research-only** |
| One-model vs detector+tracker; multi-object/occlusion/identity; low-res; selective high-res; memory | track-model code landed to support them | footage; model-run hold; identity labels via agent labellers when firstmate arranges them | **unmeasured** |

## Measured this turn (incumbent, PERF-013 low-res)

Same 20-frame laptop fixture and seed, staged 480-wide clip vs 960-wide,
fp32 weights with MPS propagation, one model process under
`objects.segment:inference`:

- 960x540 warm (same-process second run): 33.66 s, 20/20 box coverage,
  mean box fill 0.563.
- 480x270 warm (same-process second run, separate session): 31.21 s,
  20/20 box coverage, mean box fill 0.589.
- Cold first-in-process runs: 74.20 s and 107.02 s (overnight PR-2628
  run: 14.25 s) - absolute time is load-dependent; the 20/20 verdict is
  stable. Process peak RSS ~1.0 GB `ru_maxrss` (excludes Metal).

No coverage loss at 480-wide on this fixture. A resolution speedup is
NOT established: the warm deltas sit inside cross-session load noise,
and the counterbalanced same-session run was stopped by the captain's
second model-run hold before it completed. Machine-readable numbers
are in `2026-10-16-sam-followup-track-model.json` (`lowres` version).

## Measured this turn (PERF-010 SAM3-8bit, 8-bit quantized MLX)

`mlx-community/sam3-8bit@bcd63fdd...` (~860M params, 1.04 GB), run under
`semantics.analyse:model_memory`, one process, staged 8 GB abort gate
(never tripped). Download recorded and removed per conditions (not
adopted).

- Single still, text prompt `black laptop`: 1 detection, 9.9 s, post-load
  RSS 0.94 GB.
- 20-frame video, text prompt on frame 0, multiplex propagation: load
  7.65 s + propagate 72.48 s (3.6 s/frame), end to end 80.13 s, peak RSS
  1.74 GB. Masks on 20/20 frames but box coverage **1/20** - frame 0
  fills 0.546 of the laptop box (matches the incumbent's 0.563), frames
  1-19 propagate EMPTY masks (0 px, scores ~-2.9): immediate track loss.
- Box-prompt variant is worse (frame 0: 5 px, score -2.36).

Verdict: **reject for tracking in this measured implementation** (same
shape as the PR-2628 MLX verdict); single-image text detection works but
is unadopted. This says nothing about SAM3 itself - only about this
mlx-vlm video/box path on this fixture. Machine-readable numbers are in
`2026-10-16-sam-followup-track-model.json` (`sam3-8bit-video` version).

## Scheduler enforcement + contention (PERF-015, 2026-10-16)

Honest reservations from measured tree footprints (ru_maxrss understates:
single-seed showed ~1 GB RSS vs 3.94 GB tree footprint):

- Blob-path (10 auto objects) 960-wide declared 3 GB: stopped at 3.94 GB.
- Blob-path 480-wide declared 5 GB: stopped at 6.92 GB.
- Both kills by the new reservation enforcement - the mechanism works.
- Blob-path 480-wide declared 8 GB (the ceiling): admitted after ~35 min
  in queue, then stopped at exactly 8.00 GB tree footprint mid-run, no
  output. The legacy 10-auto-object blob path does not fit the 8 GB
  ceiling even at 480-wide. Multi-object tracking must go the seeded
  route (face/detector seeds, N targets); the track model already
  supports it. Seeded cost scales near-linearly from the measured
  single-seed footprints (1.0-3.9 GB tree).
- Two-seed 960-wide and 480-wide do not reliably fit either: identical
  two-seed runs measured 5.34 / 8.12 GB (960) and 6.11 / 7.11 / 8.13 /
  9.14 / 10.15 GB (480) tree footprint - and the 480 ladder climbs ~1 GB
  per attempt, which reads as machine-state ratchet (swap pressure
  accumulating run to run) rather than a stable per-run number. The
  final attempt at 9.9 GB (ceiling 10 GB) was stopped at 10.15 GB with
  no output. Verdict: two-seed PyTorch SAM 2.1 is NOT MEASURED - no
  complete run under the ceiling. PyTorch SAM 2.1 multi-object (>=2
  targets) is memory-blocked under the ceiling on this loaded machine;
  single-seed fits. The structurally interesting multi-object route is
  a small alternative (EdgeTAM-class, 15-27 MB weights), not more
  PyTorch SAM 2.1.
- Blob-path cost/fragmentation unmeasured (stopped at the ceiling, see
  above); its quality half never applied - the blob path names no
  detector class.

Agent labels for the multi-object quality half (PERF-012 quality,
OBJ-017/019/020) requested in the lane data dir (`label-request.md`);
cost-side work does not wait on them.

## Unmeasured work (carried forward)

PERF-008/009/010/011/012/013/014/015/016 measurements, OBJ-017
(visible/occluded/absent classification), OBJ-019/020 (identity
recovery, cross-clip re-identification). OBJ-013 holds: nothing was
replaced.
