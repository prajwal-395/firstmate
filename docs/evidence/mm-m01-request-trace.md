# M01: one machine-readable trace per request (`ren profile --trace`)

Lane mm-m01-trace. The punch list's M01 asks for one end-to-end
profiling report: every stage of a real editorial request instrumented,
and one machine-readable trace per request that explains exactly where
its time went. This note records what was built, the recorded request
it was proved on, and what was left undone.

## What already existed (read first, extended, not rebuilt)

- `library/tools/perf_ledger.py`: the ledger, `ren profile`, stage
  shares, own-code vs external-engine split, call counts.
- `library/tools/critical_path.py` (PROD-007): serial vs overlapped
  stages, bottleneck resource, limit family.
- `library/tools/latency_budget.py` (PERF-005): budgets by operation
  class, work separated from contention.
- `library/tools/model_lifecycle.py` + `perf_report.model_access`:
  model load, cache reuse, cold vs warm vs unsaid inference.
- `library/tools/perf_report.repeated_work`: which source was measured
  again, and whether the cache served it.

None of them answered M01's question: for ONE request, every second in
the vocabulary of an editorial request, with the remainder reported.

## What changed

- New `library/tools/request_trace.py`: a READER over the same ledger
  (no new store, no writer, no model, no Resolve). `trace_for_run`
  attributes one run - optionally narrowed to one completed task's
  window - to the eleven M01 stages (source discovery; source decode;
  audio/speech; semantic/events; index build; candidate generation;
  ranking/verification; planning; Resolve queue; Resolve execution;
  validation). `media_decode` rows are split by their own
  `media_kind`/`decode_purpose` (video to frame extraction, audio to
  speech analysis); `reel_lifecycle` rows by phase; summary rows
  (`reel_edit`, `execution_strategy`) are excluded so nothing is
  charged twice. The trace carries wall, CPU, queue wait (lease and
  scheduler separately), cache hits/misses, CPU vs GPU (GPU is
  explicitly unmeasured - no counter is instrumented), peak RSS, model
  warm-up vs cold start, the ingestion vs per-request split, and the
  `unclassified` + per-capability `unattributed` remainder. The three
  add up to the run's capability wall by construction.
- `ren profile --trace [--task TASK-ID] [--json]`: the same reader on
  the profile verb (`perf_ledger.main`); `python3 -m
  library.tools.request_trace` works standalone.
- `ranking_verification` is a named stage with no ledger layer yet:
  candidate ranking currently happens inside `footage_search`, so the
  trace reports the stage as unmeasured (no spans) rather than free.

## Proof: one recorded request, every second accounted

Recorded run `run-20261015T194124Z-643533`, read from the stored
quarantine ledger (read-only; no model, no Resolve, no replay):
5 capability rows, 52 span rows, 6 count rows, 2490.556 s of
capability wall.

Before (`ren profile` stage table): one bucket, `planning` 2189.071 s
(52 spans), unattributed 301.485 s. Transcription, forced alignment and
45 VLM inferences all read as "planning/model" - the generic
attribution M01 exists to end.

After (`ren profile --trace`): `audio_speech` 31.156 s (6 spans),
`semantic_events` 2135.095 s (45 spans), `planning` 22.820 s (model
load, 1 span); unclassified 0.0 s; unattributed 301.485 s named per
capability (`temporal.index` 216.2 s, `semantics.analyse` 83.3 s,
front doors 1.9 s). Check: 2189.071 + 0.0 + 301.485 = 2490.556.
Ingestion 0.0 s (no store built on this run), per-request 2189.071 s.
Queue 0.0 s both halves. Cache unmeasured (no span carried a count).
CPU 1029.134 s, GPU unmeasured (no counter instrumented), peak RSS
2463.8 MB. Model: load 22.820 s; 51 inferences unsaid (rows predate the
cold/warm flag), never guessed warm.

The remainder is the finding, not a gap in the trace: `temporal.index`
spent 216.2 s of its 247.4 s wall outside every span (CPU 576.5 s over
247.4 s wall - parallel worker subprocesses with no span coverage).
Closing that needs span coverage in the temporal workers, which this
lane did not add.

## Incidental fix, with its own denominators

While validating the neighbours, `test_critical_path.py` +
`test_perf_ledger.py` in one process failed
`test_a_phase_longer_than_its_run_is_clamped` intermittently (~1 in 3
runs). Root cause: `perf_ledger.record()` rounded the clamped wall UP
past the run's age by up to 0.5 ms, and the write-path check re-fired
on the clamped row - rewriting it as an anomaly whose "claim" was the
clamped bound and LOSING the true 131422.0 s claim
(`clamped_from_s` gone, `claimed_wall_s` 0.262). Fix: floor the clamped
wall to the millisecond. New deterministic regression test
`test_a_clamped_phase_is_not_rejudged_into_an_anomaly` fails on the old
line, passes on the new one; the previously flaky pair ran 6/6 clean
after the fix. Only already-impossible rows (wall > run age) are
touched; honest rows are bit-identical.

## What was left undone

- No new span coverage in `temporal.index` workers (the 216.2 s
  remainder's owner); no footage-search, Resolve or validation stages
  were exercised by this run - the fixture in
  `tests/unit/tools/test_request_trace.py` covers all eleven stages
  synthetically instead.
- No GPU counter: the trace names the gap (`gpu_s: None`) rather than
  filling it.
- No change to `ren profile`'s default output shape; the trace is
  additive behind `--trace`.
