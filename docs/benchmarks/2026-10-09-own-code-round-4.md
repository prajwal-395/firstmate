# Own-code cost ledger, round 4

Date: 2026-10-09
Worktree base: `db4b27c8` (after https://github.com/prajwal-395/video_editing_pilot/pull/2227)
Workload: two qualification runs against the indexed 214.548 s source,
including analysis, Resolve builds and renders, exports and final render QA.
Resolve work used the qualification workflow's borrowed lease.

## Measurement result

The qualification passed both runs. The workflow ledger records 289.1 s of
wall time: 175.6 s Ren-owned (60.8%) and 113.5 s external engines (39.2%).
The largest attributed external spans were model inference (74.4 s), Resolve
broker work (38.8 s), and Resolve API/render calls (6.3 s). The ledger also
recorded four broker requests, 30 edit-core calls, eight subprocess spawns and
three CLI starts. Graphics renders, probes and hashes were not measured by
this workload; probe/hash accounting and reuse remain in the separate lane.
The layer table overlaps by 80.8 s, so its rows must not be summed.

The immediately preceding valid round-4 qualification on the same workload
recorded 353.6 s total, 224.4 s own code (63.5%) and 129.2 s external (36.5%).
This pass is 64.5 s shorter overall, with 48.8 s less attributed own-code
time and a 2.7 percentage-point lower own-code share. The expression lane
returned to its normal 0.2-0.3 s per run rather than repeating round 3's
roughly 186 s outlier. The round-2 reference profile was 279.0 s total,
163.2 s own code (58.5%) and 115.9 s external (41.5%); round 4 is improved
over the failed-ledger round-3 measurement but has not yet beaten that
reference share. These are individual runs, and some child spans overlap
their parent analysis spans.

The final ledger is at
`~/.local/share/vep/qualification/622307e41719/workflow-runs/20261009T030639489116Z/perf_ledger.jsonl`.
The report is at
`~/.local/share/vep/qualification/622307e41719/workflow-runs/20261009T030639489116Z/qualification-report.json`.
Unlike round 3, the workflow-level ledger is now required: qualification
fails at startup if it cannot create the ledger, child project rows are read
strictly, and copied workflow spans fail loudly if persistence fails. This
closes the gap that previously made a passing qualification have no trustworthy
workflow-level attribution.

## Hotspot and change

The round-3 profile identified repeated AST serialization inside batched
measurement-layer digests. The batch now shares a per-batch AST-dump cache in
addition to the existing parsed-source and import-graph caches. Independent
digests remain byte-for-byte equal to batched results. A five-sample,
cache-cleared comparison measured median digest time of 3.823 s without the
dump cache and 3.211 s with it (16.0% lower); individual samples were noisy.
This is a direct digest benchmark, not an end-to-end wall-time attribution.

## Validation and limits

- The two-run qualification passed analysis, Resolve construction and render,
  export and final render QA. The workflow-level ledger and report are listed
  above.
- The computed changed-file selection covered 63 test paths, compared with
  9 directories for `--loop`; the broader selection passed 1,512 tests with
  `pytest -n 2` through `vep-verify-run.sh`.
- Enforced Ruff passed for changed Python files, and `git diff --check` passed.
- End-to-end own-code share remains 2.3 points above the round-2 reference.
  The largest remaining measured Ren-owned layer is `ren_analysis_cli`; the
  workload also shows substantial model inference, alignment and broker time.
  Disk wait and graphics/probe/hash call families are not attributed here.
