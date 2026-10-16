# SAM tracking bakeoff (2026-10-15)

## Decision

**Retain PyTorch SAM 2.1 Hiera Small as the default. Do not adopt the tested
SAM 2.1 MLX route.** On the existing podcast laptop fixture, MLX took longer
end to end and its selected mask did not cover the hand-boxed laptop or overlap
the incumbent mask. This rejects this implementation for this use case; it does
not establish a general failure of MLX or SAM 2.1.

SAM 3.1 MLX is **not qualified**. The 3.49 GB full-precision checkpoint loaded
and the prompt returned a non-empty detection result, but the run was
interrupted during propagation after system memory pressure rose. There is no
complete timing or quality result to compare. No backend default changes.

## Fixture and method

- Calibration only: `LCATL0013.MXF`, source time 30-40 s, 960x540, 20 frames at
  2 fps. This is the existing podcast laptop track fixture from
  `object-detector-bakeoff-2026-10-13.md`.
- Both SAM 2.1 implementations received the same detector box
  `[0.15, 0.51, 0.36, 0.77]` for `black laptop`; no per-run label edits were
  made. Existing eye-estimated laptop boxes on this fixture define the coverage
  check: a sample passes when the mask fills at least 50% of the box.
- The PyTorch path used Ren's `ObjectSegmenter.segment_clip` and its 2 fps
  sampler. The MLX path used the isolated upstream `mlx-sam` implementation,
  with a converted copy of the same publisher checkpoint. Model code and
  converted weights stayed under `/tmp`.
- The SAM 3.1 attempt used the same 20 sampled images and text prompt
  `black laptop`. It reached first-frame detection and began multiplex tracking;
  it did not finish.
- All footage remained read-only. The clip, extracted frames and model artifacts
  were temporary and have been removed after measurement.

## Results

| Backend | Precision | End to end, 20 frames | Warm propagation | Laptop box coverage | Mean mask IoU vs PyTorch | Missing mask frames | Peak process RSS |
|---|---|---:|---:|---:|---:|---:|---:|
| PyTorch SAM 2.1 Hiera Small | fp32 weights, MPS propagation | 14.25 s | not split in the wrapper | 20/20 (1.00) | reference | 0 | 1.15 GB |
| SAM 2.1 MLX | fp32 converted weights, fp32 runtime | 18.51 s | 716 ms/frame median | 0/20 (0.00) | 0.000005 mean | 0 | 0.98 GB |

For the 10 second fixture, MLX was 1.30x the incumbent's end-to-end time. The
MLX feature-only pass took 5.88 s for 20 frames (294 ms/frame mean). Its
precomputed-feature run took 23.83 s end to end and did not improve mask
agreement. Process RSS excludes Metal allocations, so it is not a measurement of
peak unified memory. System peak memory remains unmeasured.

The incumbent reproduction is a bounded calibration result. The previous full
001 result remains 6,558.7 s for 807 s of footage (8.1x real time); this lane did
not repeat that 109 minute run. The incumbent's original object-tracking report
also records 1,544/1,615 selected-subject samples on 001 and the five clip-local
visibility gaps.

## Candidate coverage and status

| Candidate | What exists | This lane's result | Decision |
|---|---|---|---|
| PyTorch SAM 2.1 Hiera Small | Existing Ren backend, detector/face seed paths, multiple seeded objects, time-indexed per-frame boxes and optional RLE masks | Reproduced 20/20 laptop-box coverage in 14.25 s on calibration | **retain baseline** |
| SAM 2.1 MLX | Isolated MLX implementation supports video propagation and converted SAM 2.1 weights | 0/20 laptop-box coverage, mean mask IoU 0.000005, 18.51 s end to end | **reject for this measured implementation** |
| EdgeTAM Core ML | Official EdgeTAM source documents Core ML export to encoder, prompt encoder and mask decoder models; local Core ML tools are absent | Core ML conversion and execution not run | **research only** |
| SAM 3.1 MLX | Installed MLX-VLM includes image detection, segmentation and Object Multiplex tracking; full-precision checkpoint loaded | Partial run; propagation did not complete under observed system memory pressure | **research only** |
| `sam3.cpp` Metal | Upstream project advertises SAM 2.1, SAM 3 and EdgeTAM paths on Metal | No local build, checkpoint conversion or throughput run | **research only** |

The existing data model already stores clip-scoped object IDs, timestamped box
geometry, optional masks, and explicit unmeasured gaps. The region query path
can read object tracks. The current SAM wrapper can seed multiple named objects
from detector boxes. Track fragmentation detection, semantic visible/occluded/
absent classification, post-occlusion identity recovery, and cross-clip
re-identification are not established by these measurements. A track ID remains
clip-local; it must not be promoted to a global identity. Dense masks remain
external to `pipeline_data.json`.

## Unmeasured work

This scorecard covers only the podcast laptop calibration fixture. It does not
qualify EdgeTAM Core ML, SAM 3.1 tracking, `sam3.cpp`, multi-person identity,
hands and small products, partial occlusion, objects leaving frame, lower
resolution, selective high-resolution refinement, or operation alongside an
open Resolve session. Cross-shot and cross-clip identity remain unmeasured.
The original 001 full-run baseline is cited as historical evidence and was not
repeated or treated as held-out data. No human annotation or reference edit was
made in this lane.

## Reproduction details

- Baseline model ID: `facebook/sam2.1-hiera-small`, publisher revision
  `ee5bba1d82bb8749febdf90f45e84b687142ba03`; reused the pre-existing Hugging
  Face cache at `/Users/prajwal/.cache/huggingface/hub/models--facebook--sam2.1-hiera-small/`.
- MLX code: `https://github.com/avbiswas/sam2-mlx`, revision
  `fbeafa7531be454b8f42d89c13d1e90024a63f6b`. Converted the same cached SAM 2.1
  checkpoint to a temporary 199.7 MiB fp32 MLX safetensors file. The temporary
  file and source checkout were removed.
- SAM 3.1 model: `mlx-community/sam3.1-bf16`, publisher revision
  `1f6152bb3cb86a13913560e030c8dd34ed493fdc`, 3.49 GB download at
  `/tmp/vep-mu-sam/models/sam3.1-mlx-f32`. Published config reports F32 despite
  the repository name. This temporary checkpoint was removed after the partial
  attempt. SAM materials use Meta's custom SAM license; exact distribution
  eligibility remains for the license lane to resolve.
- MLX runtime: installed `mlx-vlm 0.4.3`. EdgeTAM's official code and weight
  license are Apache 2.0; official source describes Core ML export, but this
  machine lacks `coremltools`, so execution was not measured.
- Incumbent process RSS is 1,146,322,944 bytes; MLX process RSS is 984,285,184
  bytes. These `ru_maxrss` values do not include all Metal/unified-memory use.

## Source records

- Existing SAM 2 measurement: [Subject masking and tracking](../SUBJECT_MASKING_MEASURED.md).
- Existing seeded laptop fixture: [Object detector bakeoff](object-detector-bakeoff-2026-10-13.md).
- Existing track coverage and gap semantics: [Object tracking coverage](2026-10-15-object-tracking-coverage.md).
- MLX candidate source: `https://github.com/avbiswas/sam2-mlx`.
- Official EdgeTAM source and Core ML exporter: `https://github.com/facebookresearch/EdgeTAM`.
- Official SAM 3 license and SAM 3.1 source: `https://github.com/facebookresearch/sam3`.
- `sam3.cpp`: `https://github.com/PABannier/sam3.cpp`.

Machine-readable measurements and the EVAL scorecard are
[`2026-10-15-sam-lane-calibration.json`](2026-10-15-sam-lane-calibration.json),
[`2026-10-15-sam2.1-pytorch.report.json`](2026-10-15-sam2.1-pytorch.report.json),
[`2026-10-15-sam2.1-mlx.report.json`](2026-10-15-sam2.1-mlx.report.json), and
[`2026-10-15-sam2.1-scorecard.json`](2026-10-15-sam2.1-scorecard.json).
