# EVAL-08 real-footage acquisition probe

Measured 2026-10-15 on the editorial corpus **calibration** split using the
locally cached `mlx-community/gemma-4-12b-it-4bit` through the production
`vision_pipeline_v3` compact window call. The model was already cached; the
probe ran offline and downloaded nothing. Source files remained read-only and
all extracted clips lived in a temporary directory.

The probe sampled one 10-second (or shorter tail) interval per source, at
30-40 seconds for longer sources and the full interval for the 3.57-second
clip. Each call returned parseable `scene`, `camera`, `actions`, and
`assessment` sections, with audio present on all three inputs.

| Calibration source | Interval | Extraction (s) | Model call (s) | Parsed sections | Peak process RSS (MB) |
|---|---:|---:|---:|---|---:|
| Podcast `LCATL0012.MXF` | 30-40 s | 4.916 | 24.105 | scene, camera, actions, assessment | 1,564.56 |
| 001 `IMG_1806.MOV` | 0-3.57 s | 1.403 | 19.940 | scene, camera, actions, assessment | 3,467.36 |
| 001 `IMG_1814.MOV` | 30-40 s | 1.729 | 24.527 | scene, camera, actions, assessment | 5,042.86 |

The scheduler admitted the pass under `semantics.analyse:model_memory` after
665.583 seconds of queue wait. The three calls took 106.050 seconds of
scheduler execution in total. Gemma loaded and unloaded once per source in
this probe. Peak process RSS across the run was 5,042.86 MB. These are costs
for a sparse one-window probe, not dense-pass or end-to-end acquisition costs.

## What this does and does not establish

The corpus manifest defines the calibration split and source groups, but
records that annotation has not begun. It contains no `gold_ids`, query/task
set, or hand-reviewed calibration labels. Therefore F1, misses, quality per
second, and adaptive-versus-fixed quality are unmeasured. The acquisition
benchmark intentionally requires those labels and paired fixed-dense,
fixed-sparse, and adaptive ledgers; fixture rows cannot stand in for them.

This is a real producer-cost measurement for the joint scene/camera/action
window call. It does not measure speech transcription, object detection, OCR,
or motion as separate producers. There was no query predicate to determine
whether a relevant interval was missed, and no evidence claim should be
inferred from these samples.

The largest observable cost is model/runtime cost rather than clip extraction:
model calls took 68.572 seconds across the three samples, versus 8.048 seconds
for extraction. Reusing a loaded model across source windows is a candidate
for a controlled follow-up, but this probe did not compare quality or test that
change, so no runtime behavior was altered. A valid EVAL-08 quality/cost
comparison still needs independent calibration labels, query tasks, and
paired fixed-dense/fixed-sparse/adaptive producer ledgers before fitting or
reporting held-out results.

## Dense baseline attempt and reuse

A dense fixed pass completed all 28 valid 10-second windows for the podcast
source (the 280-288.96-second tail was dropped because the encoded window had
no video stream). Its scheduler execution was 1,422.619 seconds after 214.424
seconds of queue wait. The run then stopped before the 001 dense sources:
`ffprobe` returned `Operation not permitted` for `IMG_1806.MOV`. The per-source
summary was emitted only after all three sources, so this partial dense run did
not retain per-window parse counts or section totals. It is a measured dense
runtime point, but it cannot support a paired quality comparison or a reliable
quality-per-second result.

Sparse samples measured all three sources as listed above; the paired dense
result for 001 is unavailable. Neither path exercised Project Model evidence
cache lookup or `execute_query_acquisition`, so cache-hit/reuse rates remain
unmeasured. The real-footage slice therefore does not establish adaptive gain
against the benchmark's fixed baselines. It records model/runtime cost and the
missing measurements without treating model output or fixtures as gold labels.
