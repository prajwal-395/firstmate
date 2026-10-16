# vep-mu-graph follow-up scorecard 2026-10-16: drift, preprocessing, confidence

Stacked on merged PR #2638 and rebased onto current main after
https://github.com/prajwal-395/video_editing_pilot/pull/2611 merged.
Completes the DATA-007/013/036/041 follow-ups in this lane.

Generator: [`vep-mu-graph-bench.py`](vep-mu-graph-bench.py), extended
with a 20-revision store history scan and provenance-helper timings.
Synthetic fixtures only; shared-machine variance noted where seen.

## What changed

- `library/tools/evidence_lifecycle.py`: `collection_history` (one
  collection row chains, `observation_history` unchanged beside it),
  `transform_drift_history` (per-transform bound series across
  revisions, M6 spread/drift decomposition recomputed with the
  migration's own ceiling formula, match check, stability reading),
  `normalize_envelope_preprocessing` (legacy envelopes into
  `record_derivation` shape, method string carried verbatim, absences
  explicit), `typed_confidence` (contract-validated constructor).
- `library/tools/evidence_graph.py`: `audit_confidence_loss` (rows with
  a score but no type, calibrated vs raw split).
- Tests: +9 (drift appear/grow/remove/absent, envelope legacy/refusal,
  constructor refusals, audit buckets).
- `project_model_evidence.py`: migrations now carry typed `raw_score`
  details for every numeric confidence and normalize each available
  evidence envelope into a derivation record. Missing historical producer
  versions remain explicitly `unknown`.
- `source_memory.py`: new writes preserve optional structured
  preprocessing and sampling policy in their evidence envelope; legacy
  records remain readable.
- `evidence_query.py` owns the five-state absence classifier;
  `evidence_graph.py` re-exports it for compatibility, so query answers
  use one vocabulary (DATA-036).

## Measured results

| Measure (20k obs unless noted) | Value |
|---|---|
| History: 20 store merges | 5.85 s (store validation + fsync dominate, ~0.3 s each) |
| History: chain scan over 20 revisions | 0.0006 s |
| Envelope normalize + license stamp + typed constructor | 0.003 s |
| Drift decomposition recompute vs stored bound | exact match (11 ticks) |
| Index parity (indexed windows vs linear scan) | byte-exact, re-asserted |

Window queries indexed 0.98 s vs 10.53 s linear on this run (shared
machine; earlier quiet run: 0.19 s vs 1.70 s). The ratio, not the
absolute, is the claim: 6-10x with parity.

No downloads, no labels (deterministic fixtures), no footage claims.

## Remaining boundaries

- DATA-010/011 hierarchy and native multi-span rows: vep-mu-multires,
  PR #2632 (TMP-023..032). Not duplicated.
- No footage-calibrated numbers: this lane's claims are structural
  (parity, refusal, fail-closure), all pinned by unit tests.
