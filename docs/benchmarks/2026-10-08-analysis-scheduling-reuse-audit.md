# Analysis scheduling and source reuse audit

Date: 2026-10-08  
Repository head reviewed: `c92823f8efe165c4fd4d488c567b689bdd14a771`  
Machine available for this audit: macOS 26.3, Mac17,2, 24 GiB RAM  
Fixed set: project `001`, 17 clips

## Result

The requested scheduling and reusable measurement foundations are already in
the pipeline at the reviewed head. The fixed-set evidence shows semantic wall
time falling from 7,748.333 s to 2,879.762 s (2.69x) on the parallel direct
still route. The first run recorded 127 model calls and 17 clips; the latter
recorded 94 Gemma calls plus 66 direct host calls, all 66 host responses
parsed, and all 17 profiles completed. Six empty initial Gemma responses were
retried successfully. This is evidence that bounded scheduling improved
throughput without losing profile coverage. It is not a visual accuracy
equivalence result: the routes used different still-answer paths, and the
existing 10-still comparison found no overall quality winner.

| Measurement | Serialized baseline | Parallel direct route |
|---|---:|---:|
| Semantic wall time | 7,748.333 s | 2,879.762 s |
| Speedup | - | 2.69x |
| Clips with profiles | 17 | 17 |
| Model calls | 127 total | 94 Gemma + 66 host |
| Host still responses parsed | not recorded | 66 of 66 |
| Gemma first-response retries | not recorded | 6, all recovered |

The baseline JSONL reports 7,744.9 s in the vision child out of 7,748.4 s
semantic wall time, so nearly all observed elapsed time was inside analysis,
not pipeline startup. The capture does not split that child time into model
inference, video decode and extraction, duplicate filtering, subprocess
startup, or image conversion. Those component timings are not present in the
available records, so this audit does not attribute the remaining time to
any one of them.

## Existing mechanisms at the reviewed head

- `library/tools/analysis/vision_pipeline_v3.py` schedules independent
  windows and coarse still batches across clips. Detail batches enter the
  queue only after their coarse result exists. The local Gemma lane has one
  worker; the direct still lane uses `still_vision.max_concurrent_calls()`.
- Semantic measurement layers are keyed by source digest, method digest, and
  inputs in `library/tools/analysis/measurement_layers.py`. Window answers,
  object measurements, and picture measurements therefore persist across
  projects sharing the same source-memory root. Pure composition is rerun
  from those cached measurements.
- Gemma model residency and inference admission use the shared heavy-work
  reservation and lock, so the model's memory reservation spans its lifetime
  while inference receives a separate short grant.
- Adaptive model selection remains unproven. The Qwen3-VL comparison found
  better text reading on some examples but 4.8x slower median request time,
  while Gemma was stronger on other examples. No adaptive route is justified
  without a same-clip quality and cost bakeoff.

## Measurement provenance and reproduction

The baseline comes from `docs/benchmarks/2026-10-03-current-head.jsonl`, run
`20261003T152705-40063`, with the source cache initially absent and 17 clips.
The parallel result comes from
`docs/benchmarks/stills-parallel-direct-2026-10-04.md`, run
`20261004T122153-63017`, using a copied `001` project and concurrency 10.
The parallel report does not record the exact source-cache warmth or the
commit hash, so those fields cannot be reconstructed. The current machine
details above describe the machine available during this audit; neither
historical capture recorded a hardware identifier.

The operation used for both fixed-set runs is:

```sh
PIPELINE_STILL_VISION_MAX_CONCURRENCY=10 \
PIPELINE_SOURCE_MEMORY_ROOT=/path/to/isolated/source-memory \
bin/vep -m library.tools.operations semantics.analyse \
  --project /path/to/disposable-copy-of-001
```

Use a disposable copy with its catalog media paths rewritten to that copy,
and preserve the emitted stdout, stderr, `perf_ledger.jsonl`, and the run's
cache state. For a warm comparison, run the same command again against the
same copy and source-memory root. Never point this command at a real project.

## Follow-up

To complete the requested component profile, add per-run measurements for
model inference, decode/extract, duplicate skips, child startup, and image
conversion to the same fixed-set capture. Then compare cold and warm runs and
perform a same-clip quality review before considering adaptive model choice.
