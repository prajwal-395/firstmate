# Own-code cost ledger, round 5

Date: 2026-10-09
Worktree base: `e0fa6171` (after https://github.com/prajwal-395/video_editing_pilot/pull/2229)
Workload: the qualification analysis over the indexed 214.548 s source
(`LCATL0011.MXF` copy `LCATL0011-9b5ec750c8db.MXF`), the same `ren analyze`
command the qualification workflow runs (with its `--skip temporal.index
--skip prosody.analyse`), on a fresh memory root each run, with private
yappi wall-clock profiling (`PYTHONPATH=<worktree>/.private-round5`,
per-PID function rows, worktree modules only).

## Measurement result

With vision admitted, Ren own code is already a small share of this
workload. Per-run analysis ledgers (`ren profile --ledger`):

| Run | Total wall | Own code | External | Inference | ffmpeg | Model runtime |
|---|---:|---:|---:|---:|---:|---:|
| 1 (inside workflow) | 680.2 s | 22.6 s (3.3%) | 657.5 s (96.7%) | 607.0 s | 438.1 s | 4.5 s |
| 2 (standalone, cold) | 718.1 s | 21.3 s (3.0%) | 696.8 s (97.0%) | 644.7 s | 486.3 s | 3.1 s |
| 3 (standalone, cold) | 1990.0 s | 50.1 s (2.5%) | 1939.9 s (97.5%) | 1762.3 s | 0.0 s* | 177.7 s |

\* Run 3 recorded no `demux` spans; its model runtime (177.7 s vs 3-5 s)
and inference (1762 s vs ~607-645 s) are 2.7-40x the other runs. A
concurrent lane was building EG2 models on the same machine during run 3,
so run 3 is reported but excluded from the baseline: contention, not Ren
code, moved it. Comparable runs 1-2 agree: ~700 s total, ~22 s own
(3.0-3.3%), the rest model inference and ffmpeg decode, heavily
overlapping (prefetch decodes the next window while the model scores the
current one).

The round-2 reference (56.1% own) and round-4 pass (60.8% own) ran with
vision refused by the memory reservation (0 profiles); they measured a
workload with almost no model inference and are not comparable to these
vision-admitted runs. This is the first round with a valid vision-admitted
baseline, and on it own code is already 3.0-3.3% of analysis wall time.

The full two-run qualification workflow could not be repeated this round:
run 1 passed analysis, then refused at the broker submit because the
shared broker is not serving after the machine restart, and this lane may
not start it. The workflow-level ledger for the partial run is at
`~/.local/share/vep/qualification/622307e41719/workflow-runs/20261009T071111479372Z/`
(789.3 s capability wall, 93.9 s own / 695.3 s external including the
driver). Resolve stages stay unmeasured until the broker is back.

## Function-level breakdown (top 15 by mean inclusive wall, 3 runs)

yappi wall-clock, worktree modules only, means over the 3 runs. Background
monitor threads (`TreeSampler._loop`, `_heartbeat_loop`) are excluded from
the ranking; their walls overlap real work. Rows marked external spend
their inclusive time inside model inference or ffmpeg waits recorded by
the ledger.

| # | Mean incl. | Calls (3 runs) | Self | Function | Reading |
|---|---:|---:|---:|---|---|
| 1 | 1004.8 s | 89 | 0.0 s | `vision_pipeline_v3:842:run_attempt` (+`analyze`) | external: gemma inference |
| 2 | 602.7 s | 3 | 0.0 s | `vision_pipeline_v3:1320:_WindowClipPrefetch._run` | overlaps #3; feeder thread lifetime, not additive |
| 3 | 553.0 s | 66 | 0.0 s | `vision_pipeline_v3:2503:run_window_call` (via `analyze_windows`) | external: per-window model calls + clip waits |
| 4 | 451.8 s | 7 | 0.0 s | `still_vision:312:inspect_stills` via `measure_objects` | external: stills model calls |
| 5 | 55.2 s | 6 | 0.0 s | `source_memory:1008:_run_clip_tasks` via `Scheduler.run` | lane orchestration over external sampling |
| 6 | 42.3 s | 3 | 0.0 s | `footage_analysis:105:_build_transcripts` | external: MFA 35.2 s of it |
| 7 | 31.6 s | 2 | 0.0 s | `picture_quality:205:sample_sharpness` | external: ffmpeg frame decode |
| 8 | 10.3 s | 9 | 0.0 s | `measurement_layers:341:method_digest` (1 batch of 3) | OWN: digest walk, fix target |
| 9 | 7.6 s | 9 | 0.0 s | `measurement_layers:295:reach` | OWN: part of #8 |
| 10 | 7.5 s | 1944 | 0.0 s | `measurement_layers:234:_module_imports` | OWN: part of #8 |
| 11 | 3.7 s | 3 | 0.0 s | `single_track_diarization:445:diarize_track` | external: diarization model |
| 12 | 3.1 s | 1179 | 0.4 s | `measurement_layers:202:_imported_files` | OWN: part of #8, recomputed per layer |
| 13 | 2.7 s | 972 | 0.3 s | `measurement_layers:119:_strip_docstrings` | OWN: part of #8, per-file parse |
| 14 | 2.0 s | 12 | 0.0 s | `source_memory:370:track_level_db` (genexpr 0.9 s self, 41.2M iters) | OWN: pure-Python sample loop, fix target |
| 15 | 0.8 s | 66 | 0.4 s | `vision_pipeline_v3:1231:_clip_opens` | OWN: clip open bookkeeping |

No worktree function besides the digest walk (#8-10, #12-13: ~10.3 s
combined) exceeds ~2 s of own-code wall per run. `track_level_db`
processes a deterministic 13,731,056 samples per run in a Python
generator (identical count all 3 runs).

## Hotspot and change

Two changes, both mechanical, neither creative (§10.5):

1. `source_memory.track_level_db` was a pure-Python generator over every
   16-bit sample (13,731,056 iterations, 0.8-0.9 s self per run,
   deterministic across all 3 runs). It now reads the frames through
   `numpy.frombuffer(int16)` and takes the float64 mean-square
   vectorized. A test pins the result against the exact-integer mean
   within 1e-9 dB, plus the silent edge. Expected saving ~0.8 s per run.

2. `single_track_diarization.diarize_track` runs ECAPA embedding encode
   on torch (~3.7 s per run), which the ledger read as Ren own code
   because no span covered it. The encode now runs inside a
   `diarize_embed` span charged to `model_inference` (same pattern as
   `mfa_align`), moving those seconds to external engines. This changes
   attribution, not speed.

Not taken: a per-definition import cache for the digest walk
(`measurement_layers.reach`). The three layers reach 386 distinct
definitions, so sharing saves ~7 of 393 walks - unmeasurable on the real
module (4.08 s batch, cProfile: ast.parse 2.1 s + ast.dump 2.0 s on 324
files). The batch is at its content-based floor in a cold process; the
change was implemented, measured, and reverted rather than kept as
dead state.

## Re-measurement (post-change cold analysis)

One more cold analysis on the same command and footage
(`qualification-round5-a4`, yappi profiled):

| Run | Total wall | Own code | External |
|---|---:|---:|---:|
| pre-change mean (runs 1-2) | 699.2 s | 22.0 s (3.1%) | 677.2 s |
| post-change (run 4) | 723.2 s | 21.3 s (2.9%) | 701.9 s |

The ledger split barely moves because the fixed work sits outside the
analysis capabilities: `track_level_db` runs in the analyze parent
process, which carries no capability row in a direct `ren analyze`
(its 36 s transcription and 53 s clip-task lanes are likewise outside
the ledger total - the ledger understates true wall by ~90 s; the yappi
profiles are the complete instrument here). yappi verifies the real
saving: `track_level_db` self time 0.9 s (13.7M-iteration generator) to
0.003 s (vectorized), ~300x on the function.

The `diarize_embed` span was verified live against the run-4 demuxed
program wav under a recording ledger: one row, `external_engine:
model_inference`, wall 0.812 s against 0.9 s of encode. It fires on the
workflow-driven path (where the parent inherits the workflow ledger -
run 1's workflow ledger shows the sibling `mfa_align`/`transcribe`
parent spans landing there); a direct `ren analyze` parent carries no
ledger, so the span is a no-op there by design. The workflow-level
attribution shift is unmeasurable until the shared broker is back.

## Validation and limits

- Three cold pre-change analyses passed (`complete`) on the qualification
  footage copy plus one cold post-change analysis; ledgers above. yappi
  installed privately at `<worktree>/.private-round5` (removed before
  push); profiles in /tmp.
- New tests: `track_level_db` against the exact-integer mean (1e-9 dB)
  plus silence; `diarize_embed` span attribution via a stubbed encode
  under a recording ledger. Computed dependent selection (32 paths)
  passed 822 tests with `pytest -n 2` through `vep-verify-run.sh`;
  `--loop` selects the contracts directory. Enforced Ruff passed for
  changed files, and `git diff --check` passed.
- Resolve/build/render stages unmeasured this round (broker down, see
  above). Probe/hash reuse stays with the separate lane.
- `TreeSampler._sample` (67.3 s mean incl, 1.4 s self, 3344 calls) is a
  background monitor overlapping real work, not a fix target.
- Ledger limit found this round: parent-process lane work
  (transcription, clip tasks, track levels) runs outside any analysis
  capability, so the analysis ledger understates true wall by ~90 s and
  parent spans only land on the workflow-driven path.
