# Parallel direct Codex calls for still-frame inspection

Date: 2026-10-04
Branch: `fm/vep-stills-parallel-direct`

## Result

The same 17-clip `001` semantic-analysis workload took **47m 59.762s**, down from **2h 09m 08.333s** in run `20261003T152705-40063`. That is **81m 08.571s saved**, a **62.84% reduction** (2.69x faster).

The comparison uses the `semantic_analysis` capability wall time from `pipeline_output/logs/perf_ledger.jsonl` in each run. Both capability records have status `ok`; the current run wrote 17 clip profiles.

| Run | Route | Clips | Semantic wall | Gemma calls | Gemma input / output tokens |
|---|---|---:|---:|---:|---:|
| `20261003T152705-40063` | Serialized host still handoff | 17 | 7,748.333s | 103 | 285,266 / 14,090 |
| `20261004T122153-63017` | Direct Codex still calls, concurrency 10 | 17 | 2,879.762s | 94 | 262,390 / 12,002 |

The new route made **66 direct Codex calls**, using **2,542,671 input** and **25,977 output tokens**. All 66 parsed, with an observed peak of 10 simultaneous Codex calls. Gemma handled the video-window requests: 88 unique requests used 94 calls because six empty first responses were retried; all six retries parsed successfully. No profile was left incomplete.

The baseline recorded Gemma token counts but did not record host-model token usage. A full-run before/after comparison of host token totals is therefore unavailable. The only directly comparable measured host-token result remains the earlier 10-still test described in the launch brief: serial agent handoff used about 1.58M input / 12k output tokens and took 352s; direct parallel Codex used about 476k input / 5k output tokens and took 20.5s. That sample had no overall quality winner.

## Method

- Source: `/private/tmp/vep-benchmark-current-head.q3obr_9g/001`. The run used a copy at `/private/tmp/vep-stills-parallel-direct/001`; the source project was not edited.
- Refreshed scan and catalog on the copy after the implementation changed the code fingerprint, then ran only `semantic_analysis` with `PIPELINE_STILL_VISION_MAX_CONCURRENCY=10` and the dedicated project source-memory directory.
- The Codex call count, exact token totals, and overlap were read from the copied project's performance ledger. The before/after wall figures are the recorded capability rows, not shell elapsed time.
- No Resolve session was used.

The selected-step invocation returned exit status 1 because the copied project's prior ledger still had an outstanding failed `validate` result from an older render. Its current run summary lists `semantic_analysis` as completed, no new failed step, and the capability ledger records `status: ok`; the validation failure is outside the measured capability.

## Validation

The dependent changed-file selection covered 100 test files, compared with 132 files in the subsystem `--loop` selection. The selected run completed with **2,216 passed and 39 skipped** in 140.86s. The skips were live Resolve qualifications and one missing optional dependency case; Resolve was not launched. `ruff check --config ruff-ci-gate.toml` passed on the changed Python files, and `git diff --check` passed.
