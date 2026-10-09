# Own-code cost ledger, round 3

Date: 2026-10-09
Worktree base: `b19d71b7` (https://github.com/prajwal-395/video_editing_pilot/pull/2222)
Workload: two qualification runs against the indexed 214.548 s source,
including analysis, Resolve builds and renders, exports and final render QA.
Resolve work used the qualification workflow's borrowed lease.

## Measurement result

The qualification passed both runs. End-to-end elapsed time was 677.7 s
(02:15:26.572 to 02:26:44.293 UTC), but this run did not leave the expected
workflow-level `perf_ledger.jsonl`. The child project ledgers contain only
three capability rows apiece and no spans, so a trustworthy own-code versus
external-engine total cannot be recovered for this run. The report's lane
timings show 185.9-186.2 s in each expressions lane, compared with 0.2-0.3 s
in round 2. This unexplained regression dominates the wall-time comparison;
the qualification's pass status does not make it a comparable benchmark.

## Hotspot and change

The Python profile of `vision_pipeline_v3.py` showed three measurement-layer
digests consuming 18.5-20.1 s cumulative across the two baseline samples.
Repeated AST parsing and import-graph traversal were the dominant Ren-owned
work. The implementation now caches parsed ASTs and module import results by
content, and computes the three layer digests as one batch with shared source
and import snapshots. In the post-change profile, all three digests took
9.094 s cumulative, a 51-55% reduction against the two baseline samples.
This is cProfile cumulative time, not end-to-end wall time; profiling overhead
and unrelated torch import costs remain in the process profile.

The same qualification exposed a scheduler bug: two tasks for the same frame
directory could be treated as reentrant because the coordinator thread owned
both acquisitions. Scheduler leases now disable thread-local reentrancy, and
a regression test proves same-resource tasks do not overlap.

## Validation and limits

- Qualification passed both complete runs, including Resolve construction,
  render, export and final render QA. Report:
  `~/.local/share/vep/qualification/622307e41719/workflow-runs/20261009T021526572478Z/qualification-report.json`.
- Computed dependent-test selection: 36 test paths, versus 7 paths in
  `--loop`; the full computed selection passed, 853 passed in 51.09 s with
  `pytest -n 2` through `vep-verify-run.sh`.
- The enforced Ruff gate passed all changed Python files; `git diff --check`
  passed.
- This round has no valid own-code share comparison because its workflow
  ledger is absent and the expression lane is an unexplained outlier. The
  prior comparable round recorded 56.1% own-code wall share; a clean
  qualification ledger and expression-lane timing are needed before claiming
  another end-to-end reduction.
