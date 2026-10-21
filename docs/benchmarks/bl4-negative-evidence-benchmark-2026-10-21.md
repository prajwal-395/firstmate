# BL-4 — the negative-evidence benchmark (2026-10-21)

**Lane:** BL-4 of "Ren — High-Fidelity Multimodal Intelligence & Grounded
Execution" ("Preserve analysis coverage" and "Store uncertainty in the
relevant coordinate space"), plus defect D4 (null uncertainty on
re-identification candidate links).

**Measured on:** `ren-scratch-breaktest`, a Ren-owned scratch copy
(stored Project Model, revision 22–24; 112 assets, 116,059 observations,
182 events, 869 relations, 12,372 identity links). The captain's project
was not read. No broker was started; no `ren qualification reset`; no
timeline or media was touched. The model was READ; every write went
into a copy of a model store outside the project.

**Script:** `docs/benchmarks/bl4-negative-evidence-bench.py`
(read-only on the project; `--upgrade` brings the stored model up to the
coverage contract in memory; `--write-rows` files the answers' rows into
a writable COPY of a model store).

---

## 1. What the benchmark asks

Plan B-04 asks that a query distinguish **confidently absent** from
**sampled-but-not-seen**, **not analysed**, **failed** and
**inconclusive**. The gate is one rule:

> A confident absence may never be issued from partial coverage.

Every answer that claims an absence must rest on a completeness basis
that is **complete, fresh and versioned** — the same bar the Project
Model's own validator applies to a stored row
(`library/tools/project_model.py`, the `query_results` block).

Two query sets, with two different grounds for their truth:

| set | N (on this model) | the ground truth |
|---|---:|---|
| measurement-backed | 95 | the lane's own records for that asset and measurement: any record with a value means the measurement is there (`present`); every record a zero-sample empty record means the lane covered the asset and found nothing (`absent`) |
| transcript-backed | 7 | the asset's transcript never says the word: the truth comes from the transcript, not from the query layer, so it can disagree with the answer |

---

## 2. Before and after

| measurement | before | after |
|---|---:|---:|
| observations carrying a processing status | **0 / 116,059** | **116,059 / 116,059** |
| estimated event boundaries carrying a bound | **0 / 317** | **226 / 226** (the other 91 are filed `open`: they claim no bound) |
| `query_results` rows in the model | **0** | **one row per answered plan** (4 filed into a store copy during the run) |
| answer vocabulary | four states (`present`, `confidently_absent`, `not_yet_analysed`, `inconclusive`) | **five states** (`present`, `absent`, `not_observed`, `not_analyzed`, `inconclusive`) |
| negative queries answered `present` | 95 / 102 | 95 / 102 |
| negative queries answered `absent` | 0 | 0 |
| negative queries answered `inconclusive` | 7 / 102 | 7 / 102 |
| answers matching their ground truth | 95 / 102 | 95 / 102 |
| **absent answers without a complete basis** | **0** | **0** |
| **gate** | **pass** | **pass** |

### What did NOT change, and why

The seven transcript-backed queries answer `inconclusive` before and
after. That is the honest answer on this footage: the transcript lane
covered the asset, but the model holds no per-word absence record, so
the query layer cannot claim "this word never happens here" and does
not. Nothing in this lane invents the coverage that would let it.

The 95 measurement-backed queries answer `present` before and after —
they ask for measurements the lane found. The distribution is the same
on both sides of the change, which is the point: **the answers did not
move**; what moved is that the model now says WHY each answer is what
it is (a coverage status on every observation, a bound on every
boundary that can carry one, and a row in the model for the answer).

### Fidelity

No answer changed state on the real model. The model changed only by
ADDING what it did not know: 116,059 processing statuses, 226 boundary
bounds, and the `open` filing of 91 boundaries that no measurement grid
can bound (a scene edge whose window sampling was never recorded). No
observation, interval, certainty or relation was touched.

---

## 3. What each change is, and where it is measured

| change | where | measured by |
|---|---|---|
| the five-state answer, computed by the graph layer's table | `library/tools/evidence_query.py` (`answer_state`), `library/tools/evidence_graph.py` (`classify_absence`) | `tests/unit/picture/test_evidence_query.py` (five-state cases) |
| the processing status on every observation | `library/tools/processing_status.py`, `library/tools/project_model_evidence.py` (the migration and the upgrade path) | `tests/unit/context/test_processing_status.py`, `tests/unit/context/test_project_model_evidence.py` |
| the bound on every estimated boundary | `library/tools/project_model_evidence.py` (`_boundary_bound`, `apply_coverage_contract`), `library/tools/project_model.py` (the validator) | `tests/unit/context/test_project_model_evidence.py` (estimated/unbounded cases) |
| the row in the model for every answered plan | `library/tools/query_results.py`, `library/tools/project_model_store.py` (`record_query_result`), `library/tools/analysis/footage_evidence_query.py` | `tests/unit/context/test_query_results.py` |
| D4: the uncertainty on a re-identification candidate | `library/tools/identity_reassociation.py` | `tests/unit/picture/test_identity_reassociation.py` |

---

## 4. Reproducing

```bash
# the stored model, and the same model brought up to the contract
python3 docs/benchmarks/bl4-negative-evidence-bench.py \
    --project ~/Documents/content_stuff/video_projects/ren-scratch-breaktest \
    --upgrade --out /tmp/bl4.json

# file the answers' rows into a COPY of a model store (never the project)
cp -R ~/Documents/content_stuff/video_projects/vep-live-workflows-e2e/project_model \
      /tmp/store-copy/
python3 docs/benchmarks/bl4-negative-evidence-bench.py \
    --project ~/Documents/content_stuff/video_projects/ren-scratch-breaktest \
    --write-rows /tmp/store-copy --write-rows-limit 4
```

Wall clock on this machine: 12 min for the full run (102 queries over
the 116,059-row model, twice — stored and upgraded — plus the row
writes). One query over the same model takes 1.4 s.

## 5. A note on the query's cost

The provenance index is built ONCE per answer
(`evidence_query.placements_by_id`), so one query over the 116,059-row
model takes 1.4 s. This lane did not change that; the measurement
simply could not have been run at all without it.
