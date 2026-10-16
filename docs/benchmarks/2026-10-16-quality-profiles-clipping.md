# vep-mu-quality-profiles: clipping-rail signal scorecard (GEO-033)

Lane: vep-mu-quality-profiles (GEO-028..040). Date: 2026-10-16.
Change: two new VI-03 signals in `library/tools/analysis/image_defects.py` —
`clipping_underexposed_fraction` (luma <=4) and
`clipping_overexposed_fraction` (luma >=251) per 5 Hz sampled frame,
migrated to `image.exposure.*` evidence and exposed as
`max_underexposed_fraction` / `max_overexposed_fraction` retrieval filters.
No new model, no download, no default or threshold change; the incumbent
D08 signals are untouched.

## Incumbent comparison

- Before: no highlight/shadow-loss signal; clipping-adjacent questions had
  no numeric answer.
- After: per-interval rail fractions with stated units (fraction 0..1 of
  decoded 8-bit grayscale pixels), fail-closed filtering, old records
  migrate unchanged (absence skipped, never read as zero).

## Correctness (synthetic 180x320 frames, `numpy`)

| frame | under | over |
|---|---|---|
| all 0 | 1.0 | 0.0 |
| all 255 | 0.0 | 1.0 |
| all 128 | 0.0 | 0.0 |
| half 0 / half 255 | 0.5 | 0.5 |

Exact on rails by construction (two vectorized comparisons over the frame
the pipeline already decoded for sharpness/noise/blockiness).

## Cost (24 GB Mac target precision: float32/float64 numpy, ships as-is)

- Incremental: ~55 us/frame (200 frames in 0.011 s) on top of the existing
  180px-short-side decode + Laplacian pass. No extra decode, no residency.
- Peak memory: no new allocation beyond two float lists per sample
  (same shape as the existing per-sample lists).
- End-to-end: unmeasurable against the ffmpeg decode it rides on.

## Related facets (no separate scorecard; reductions of existing tracks)

- Sharpness/noise/blockiness (GEO-028/034): pre-existing VI-03 signals,
  now reduced per retrieval interval; units documented in
  `docs/evidence/picture_quality.md`.
- Camera translation vs subject energy (GEO-030/031): reduction of the
  existing 1.04 `decompose_camera_motion` output; no new computation.
- Brightness (GEO-032): reduction of the existing 1.04 luma curve;
  exposure shape beyond mean + rails is not measured (no histogram).
- Framing (GEO-035): reduction of the existing 1.04 face track
  (`face_center_x`, `face_width`); mean-of-measured-samples proxy.

## Out of scope by rule, not by omission

- GEO-029: no canonical defocus-vs-motion-blur verdict (ARCH-012); the
  retrieval path supports the measurable combination (soft + stable vs
  soft + high camera translation) and the docs say why the verdict stays
  out. No scorecard justifies a classifier, so none was built.
- GEO-039/040: no editorial thresholds baked in, no subjective policy
  defined, so no agent-label comparison was run.
