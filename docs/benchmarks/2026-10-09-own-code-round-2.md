# Own-code cost ledger, round 2

Date: 2026-10-09  
Worktree base: `133e7322`  
Workload: two qualification runs against the same indexed 214.548 s source,
including analysis, two Resolve builds/renders, exports and final render QA.
Resolve work used the qualification workflow's borrowed lease.

## Before and after

| Measurement | First measured pass | Round 2 |
|---|---:|---:|
| End-to-end wall time | 698.1 s | 285.8 s |
| Ledger Ren own-code time | 577.7 s (82.8%) | 160.2 s (56.1%) |
| Ledger external-engine time | 120.4 s (17.2%) | 125.5 s (43.9%) |
| Expression lane, run 1 | 191.4 s | 0.3 s |
| Expression lane, run 2 | 191.0 s | 0.2 s |

The workflow passed both runs, including Resolve construction, export and
render QA. The absolute ledger own-code time fell by 417.5 s (72.3%) and the
measured share fell by 26.7 percentage points. The external total is similar;
the lower own-code share comes from removing repeated Ren orchestration work.

The repeat ledger is at
`~/.local/share/vep/qualification/622307e41719/workflow-runs/20261009T004202802040Z/perf_ledger.jsonl`.
`ren profile --ledger` reports 285.8 s total, 160.2 s Ren own code, 125.5 s
external, with 84.2 s model inference and 41.2 s Resolve broker work. It also
reports 4 broker requests, 20 subprocess spawns and 3 CLI starts. Edit-core,
graphics, probe and hash call families remain unmeasured in this workload;
probe/hash instrumentation belongs to the separate reuse lane.

The first measured pass is documented in
[`2026-10-08-own-code-ledger-v1.md`](2026-10-08-own-code-ledger-v1.md). Its
ledger assigned uninstrumented work to Ren own code, so both own-code shares
are measured-ledger shares, not a complete attribution of every wait. The
overlapping layer table is diagnostic and must not be summed: model inference
can be present in both an analysis CLI parent span and a child engine span.

## Hotspot and change

The expression lane classified 422 faces over 429 M2 samples. Before the
change, it started a separate ffmpeg process and sought the source for each
face, taking about 192 s per run. Face boxes are normalized against the 384 x
216 M2 images, but the old crop applied those coordinates to the full-size
source. The lane now loads each cached M2 image once and crops its faces in
memory. It also declares M2 as an input, refuses an incomplete M2 cache, and
marks old classifier outputs stale with a method version change. The same
sample now takes 0.2-0.3 s, with all 422 faces classified.

## Profiling and limits

`py-spy 0.4.2` was available, but macOS refused its sampling attach with
“This program requires root on OSX.” No elevated process was started. The
The expression lane's direct timing and 422-face count identify the repeated
ffmpeg launch as the dominant measured hotspot. A Python-aware py-spy profile
and Rust flame graph remain open work because this machine requires root for
the Python sampler. This run also did not produce calls for every ledger
count family, so those remain explicitly unmeasured.

## Validation

- Computed changed-file selection: 1,948 passed, 1 skipped, 0 failed, with
  `pytest -n 2` through `vep-verify-run.sh` after updating to the latest base.
- Changed-file selection covered 82 test paths; `--loop` selected 9 test
  directories. The broader computed selection was run.
- Ruff passed for the expression and footage-analysis changes; `git diff
  --check` passed.
- The private expression-lane rerun completed at 0.4 s end-to-end. The
  two-run qualification report passed at
  `~/.local/share/vep/qualification/622307e41719/workflow-runs/20261009T004202802040Z/qualification-report.json`.
