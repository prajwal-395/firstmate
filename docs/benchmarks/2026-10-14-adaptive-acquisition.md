# Adaptive evidence acquisition benchmark

`library.tools.acquisition_benchmark` compares query directed evidence
acquisition with fixed dense and fixed sparse baselines on identical labeled
tasks. It is a scorer over frozen producer ledgers; it does not load models,
read footage, or generate labels. The acquisition runner remains
`library.tools.query_directed_acquisition.execute_query_acquisition`, which
records measured producer wall cost, peak RSS, selected source interval,
producer version, and deferred/refused work.

## Input and split rules

The input is JSON with two arrays. `tasks` has unique `task_id`, `split`
(`training`, `calibration`, or `held_out`), `lane` (`semantics`, `speech`,
`objects`, `ocr`, `motion`), and hand-reviewed `gold_ids`. `runs` has
`task_id`, `policy`, `config`, `predicted_ids`, measured `cost_seconds`,
measured `peak_rss_mb`, `budget_seconds`, and `status`. Policy values are
`fixed_dense`, `fixed_sparse`, `adaptive_candidate`, and `adaptive`. An
adaptive candidate config is eligible for fitting only on calibration tasks;
the selected config must be emitted as `adaptive` on held-out tasks. The
scorer rejects duplicate task/run identities and unknown task references.
Held-out annotation payloads remain outside the repository and tuning access
must remain false in the corpus manifest.

Calibration chooses the highest quality-per-second candidate whose mean F1
is within the configured non-inferiority margin of the stronger fixed policy
and whose measured cost is lower. This comparison uses the same calibration
task IDs for both baselines and the candidate. Held-out reporting is likewise
paired across adaptive, dense, and sparse results. Utility is F1 over evidence
IDs; miss rate counts tasks with gold evidence and no returned gold ID;
quality per second is summed utility divided by summed measured cost. A
budget refusal is retained as zero utility and a miss, never omitted from the
denominator. RSS is the maximum measured task peak.

Run the report after labels and ledgers are available:

```sh
bin/vep -m library.tools.acquisition_benchmark \
  /path/to/sealed-task-and-run-ledger.json \
  --out /tmp/adaptive-acquisition-report.json
```

The command exits 0 only when at least three lanes are calibrated and the
paired held-out adaptive results are non-inferior to the best fixed baseline
at lower measured cost. Otherwise it exits 2 and writes an explicit failure
report with unmeasured modalities retained. It does not treat absent scores
as zero-quality measurements.

## Evidence available on 2026-10-14

FI-24 provides a measured object detector quality/cost point and preflight
budget refusal (see [the FI-24 smoke](2026-10-14-fi24-planner-smoke.md)).
The editorial corpus manifest has clean train/calibration/held-out source
groups, but labels have not been created. No compatible paired policy runs
for semantics, speech, OCR, or motion are checked in. Therefore this commit
provides the evaluator and fixture acceptance coverage; a real held-out
non-inferiority claim is explicitly **pending labels and producer ledgers**.
Do not tune against held-out results when those inputs arrive.
