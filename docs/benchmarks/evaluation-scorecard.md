# Ren intelligence scorecard

`python3 -m library.tools.evaluation_scorecard --report <frozen-report.json> [--report ...] [--out scorecard.json]`
aggregates frozen evaluation reports into a reproducible comparison. It does
not run benchmarks, choose models, rank versions, or calculate a blended
score. Each version keeps its own modality and split rows, so a strong result
cannot hide a regression in another modality.

Each input uses `ren_evaluation_report/1`: `revision`, a `version` (`id`, role
of `baseline`, `candidate`, or `accepted`, and named model versions), a
`modality`, a `split`, named `metrics`, and `evidence`. Metrics carry a value,
unit, and one of `quality`, `latency`, `memory`, or `cost`. Reports can include
`training`, `calibration`, and `held_out` rows. `selection_basis`, when present,
may name training or calibration only. The aggregator never uses held-out
values to select or rank a version. Held-out rows are report-only.

Evidence entries carry a stable `id` and SHA-256. When a relative `path` is
provided, it is resolved beside the report and verified before use. Every
source report is also checksummed in the scorecard. Missing dimensions are
listed per observed modality under `unmeasured`; modality rows and metrics
remain separate by split. An absent report is not evidence of a measured
failure.

Freeze the report and its evidence before evaluating held-out data. Record the
producing benchmark's revision and model versions in the report. For a fair
comparison, use the same corpus/evidence checksums and report calibration and
held-out rows separately. New versions are appended as new version ids; do
not overwrite earlier scorecards when preserving a historical comparison.
