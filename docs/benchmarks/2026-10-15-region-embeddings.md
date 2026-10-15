# Region embedding retrieval measurement

## Decision

Do not ship region embeddings in visual search from this measurement. The
calibration-only sweep selected the whole-frame control (region weight 0.0),
and the one held-out run at that setting left EVAL-02 quality unchanged. The
final change from this task is this measurement document only.

## Protocol

The EVAL-01 GEO vs SEO corpus has seven sources. The prototype re-embedded its
existing 1,092 CLIP frame samples and added crops only where already measured
M3d person-region records were available. It produced 1,111 crops from two
sources. The two held-out sources, LC4930 and LCATL0011, had no M3d records;
the corpus had no M3e saliency records. No object-region records were present
in the source-memory slots. No regions were measured for this experiment and
the existing CLIP model cache was used without a download.

For calibration, the score was swept as
`(1 - region_weight) * whole_frame_cosine + region_weight * best_region_cosine`.
The sweep used the five registered calibration queries C01-C05 and their
calibration labels only. The search pool (40 frames), five returned ranges,
and 30-second merge gap stayed fixed. Macro F1 is the arithmetic mean of each
query's F1, with an undefined precision treated as zero.

| Region weight | Macro recall | Macro precision | Macro F1 |
|---:|---:|---:|---:|
| 0.00 | 0.0108 | 0.5200 | 0.0210 |
| 0.25 | 0.0085 | 0.4800 | 0.0165 |
| 0.50 | 0.0051 | 0.4400 | 0.0100 |
| 0.75 | 0.0054 | 0.4400 | 0.0107 |
| 1.00 | 0.0090 | 0.4800 | 0.0176 |

The selected calibration setting was 0.00. No score threshold was introduced
or tuned. EVAL-02 was evaluated once at the selected setting; no held-out
query or label participated in the sweep.

## EVAL-02 held-out result

| Measurement | Before | Region-index candidate, weight 0.00 |
|---|---:|---:|
| Macro recall, 4 present queries | 0.1473 | 0.1473 |
| Macro precision, 3 queries with scored ranges | 0.6667 | 0.6667 |
| Useful intervals | 4 | 4 |
| Query seconds | 6.455 | 2.336 |
| Seconds per useful interval | 1.61 | 0.584 |

Before is the pinned EVAL-02 result in
[`2026-10-14-visual-retrieval.md`](2026-10-14-visual-retrieval.md), using the
same frame embedding checksum as this build. After is one run of the frozen
[`visual_retrieval_heldout_set.json`](../../library/tools/visual_retrieval_heldout_set.json)
with the calibration-selected weight. The ranking metrics are unchanged. The
observed latency difference is not evidence of a retrieval speedup: the
separate runs had different first-query model-load times (6.02 seconds before,
2.251 seconds after). Both used local inference and incurred $0 hosted API
cost. The after run's held-out recall and precision confidence intervals were
also unchanged from before: [0.0, 0.2945] and [0.0, 1.0], respectively.

## Build cost

The scratch index build ran under
`heavy_work_lock` with profile `semantics.analyse:inference`, using the
existing `openai/clip-vit-large-patch14` model cache:

- Total build: 446.5 seconds, including 262.2 seconds for whole-frame
  embeddings and 181.0 seconds for region embeddings.
- Existing frame index: 3,608,677 bytes (frame JSON plus frame vectors).
- Candidate index: 7,071,980 bytes, an increase of 3,463,303 bytes (96.0%).
- Region vectors: 1,111 x 768 float32, 3,413,120 bytes.

The index build printed these completed measurements, then the lock wrapper
exited with a cleanup `FileNotFoundError` for the legacy
`~/.local/share/vep/heavy-work.lock` directory. The scheduler showed no
remaining job, and the completed scratch index was used for the calibration
and held-out measurements.

The index and report were written under `/tmp`; the project and media remained
read-only. The candidate failed to improve held-out retrieval, while the
calibration-selected weight disabled region contribution. Per the adoption
rule, no implementation is shipped.
