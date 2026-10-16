# vep-mu-graph scorecard 2026-10-16: evidence graph completion

Lane vep-mu-graph, DATA-001..018 and DATA-034..053. Grounding first:
most of the section already exists on main (merged PRs 2616 evidence
envelope/coverage and 2610 clock transforms, plus the lane-3 identity
work in #2631). This PR builds only what is missing, adds no second
canonical model, and introduces no database.

Generator: [`vep-mu-graph-bench.py`](vep-mu-graph-bench.py) (stdlib
only, synthetic fixtures, no footage, no media, no model passes).
Machine: M-series Mac, 24 GB class. Precision recorded per result.

## What already existed (no work done, verified by reading)

DATA-001, 002, 004, 006, 008, 009, 012, 014, 015, 016, 018, 034, 035,
037, 043, 050, 052 are DONE on main: canonical Project Model event
store, Source Memory per-source cache, stable ids, TimeTransform
segments with query-time joins, inconclusive-on-uncertain-sync,
per-row provenance, raw-vs-interpretation and policy-evaluation
separation, hash-chained revision log, migration compatibility,
certainty vocabulary, envelope coverage, no-negative-proof rule,
narrative/physical split, deferred DB decision, opt-in cross-project
sharing. DATA-010/011 multiresolution intervals are owned by open PR
#2632 (vep-mu-multires): deliberately not duplicated here.

## What this PR adds

| Module | Items | Behaviour |
|---|---|---|
| `library/tools/evidence_graph.py` | 036, 038, 039, 042, 044, 045, 046, 047, 048, 049 | read-only graph layer: entity/event browsing, multi-hop traversal, multi-observation questions, selective slices, lazy temporal/entity/text/spatial indexes, five-way absence vocabulary, speaker/object/event contradiction detectors with query-window scoping, acquisition-shaped verification requests, decomposed answer uncertainty |
| `library/tools/evidence_lifecycle.py` | 003, 013, 017, 040 | derived-artifact registry with rebuild entrypoints, staleness checks against live model revision/digests/producers, per-observation revision chains replayed from the event log, structured derivation records |
| `library/tools/evidence_license.py` | 053 | registry-verdict stamping and fail-closed reuse checks per workload purpose; consumes the registry lane's truth, redefines nothing |
| `library/tools/source_clocks.py` | 005 | exact frame/sample to tick mappings beside the ms-tick core, with the sub-millisecond many-to-one limit stated in the module |

## Measured results (DATA-051: the current storage model)

Synthetic model, 20k and 100k observations on one clock, 200 window
queries, 10 point queries, full contradiction scan, tracemalloc peak.

| Measure | 20k obs (10 MB sidecar) | 100k obs (51 MB sidecar) |
|---|---|---|
| Sidecar load | 0.24 s | 1.06 s |
| Linear scan, 200 windows | 1.70 s | 11.27 s |
| Index, 200 windows (lazy build incl.) | 0.19 s | 1.19 s |
| Point queries x10 | 0.09 s | 0.06 s |
| Full contradiction scan | 4.36 s | 42.99 s |
| Index peak memory | 3.5 MB | 19.1 MB |
| Cold import | 0.02 s | 0.02 s |

Quality, not just speed: the bench asserts indexed windows return
exactly the linear-scan row sets (parity), and the unit suite pins it
again per window. Thresholds (250-tick speaker overlap, half-span
attribute overlap, IoU >= 0.5 from the October detection bakeoff bar)
were set on synthetic calibration fixtures and are named constants;
no footage claim is made, so no held-out footage run applies. No agent
labels were needed: every assertion is deterministic over constructed
fixtures, and the punchlist's annotation items belong to the harness
lane, not this one. No model was downloaded.

Decision: JSON sidecars plus the lazy in-memory index carry 100k
observations at ~1 s load and ~20 MB peak with 6-10x query wins and
byte-exact parity. No database (DATA-050 holds). Revisit triggers:
sidecar load past 5 s, a project past ~500k observations, or a query
pattern the four predicate indexes cannot serve.

Two honest limits: the full-model contradiction scan is O(n.k) and
belongs behind a query window (the `scope` parameter exists for
exactly this); millisecond ticks cannot resolve 48 kHz samples, so
sub-tick samples share a tick by construction (stated in
`source_clocks`, sample numbers travel beside ticks in provenance).

## Left undone (with owners)

- DATA-007 drift history beyond a scalar bound, DATA-013 structured
  preprocessing on legacy envelopes, DATA-041 typed confidence through
  migration: PARTIAL, owned by the evidence-migration path both open
  PRs (#2611, #2632) already touch; converging there avoids a third
  writer on those files.
- DATA-036 at the query layer: `evidence_query` keeps its four states
  (owned by #2611); the five-way split lives in the graph layer until
  that PR lands, then converges.
- DATA-010/011 native multi-span rows and hierarchy: open #2632.
- No footage-calibrated numbers: this lane's claims are structural
  (parity, refusal, fail-closure), all pinned by unit tests.
