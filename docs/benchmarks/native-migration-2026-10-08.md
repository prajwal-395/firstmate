# Ren performance baseline - 2026-10-08

## Scope and machine

This is the available fixed-input baseline from the native migration audit. It
was measured on a MacBook Pro (Mac17,2, Apple M5, 10 CPU cores, 24 GB RAM),
macOS 26.3, at commit `c92823f8efe165c4fd4d488c567b689bdd14a771` in the
`fm/vep-perf-baseline` worktree. One hardware tier was measured.

Reproduce the infrastructure measurements with:

```sh
/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh \
  bin/vep scripts/measure_media_infrastructure.py
```

The script uses a 446.8 MB fixed camera clip and a 109.4 MB fixed reel render.
Each measurement repeats the same input in one process. These numbers are
therefore repeated-run, warm-cache timings; the script does not flush the OS
file cache or provide an independently cold run. Its cProfile Python/C split
is a sampling estimate, not a measurement of Python CPU versus native CPU,
blocked child-process time, GPU time, or disk wait. The external-work rows
must be read with that limitation.

## Measured work, ranked by wall time

| Rank | Work | Average wall | cProfile estimate | Notes |
|---:|---|---:|---|---|
| 1 | Render QA frame diff, reel at 2 fps | 2,691.48 ms | Python 137.84 ms (5.1%); C 2,553.64 ms (94.9%) | Streams and compares decoded frames. The C bucket includes time spent waiting for ffmpeg. |
| 2 | Whole-file cache identity hash, 446.8 MB | 237.89 ms | Python 1.11 ms (0.5%); C 236.78 ms (99.5%) | Hashes the full file; chiefly an I/O workload. |
| 3 | Clip probe, 446.8 MB | 79.90 ms | Python 0.86 ms (1.1%); C 79.04 ms (98.9%) | Probe subprocess; elapsed time includes process startup and wait. |
| 4 | Renderer fingerprint | 3.54 ms | Python 1.45 ms (41.0%); C 2.09 ms (59.0%) | Local tree fingerprint. |
| 5 | Render input tree digest | 2.01 ms | Python 0.95 ms (47.0%); C 1.07 ms (53.0%) | Local tree digest. |
| 6 | Sampled footage fingerprint | 0.77 ms | Python 0.00 ms (0.5%); C 0.77 ms (99.5%) | Reads the first and last 1 MiB, not the full 446.8 MB. |
| 7 | JSON parse, 204,190-byte manifest | 0.99 ms | Python 0.99 ms (99.8%); C 0.00 ms (0.2%) | 50 repeated parses. |
| 8 | BM25, 1,000 documents and 8 terms | 0.69 ms | Python 0.44 ms (63.5%); C 0.25 ms (36.5%) | 200 repeated score calls. |
| 9 | Interval merge, 100 spans | 0.02 ms | Python 0.01 ms (49.7%); C 0.01 ms (50.3%) | 1,000 repeated calls. |

The measured list is dominated by decoded-frame verification, full-file I/O,
and a media-probe subprocess. In the microbenchmarks, JSON parsing and BM25
are sub-millisecond. This ranking applies only to these fixed infrastructure
inputs, not to an end-to-end project run.

## Requested end-to-end profiles

The checkout has no end-to-end qualification harness for a fixed project that
runs analysis, planning, editing, render, and QA. The existing `Ren
Qualification` project contains small synthetic Resolve timelines; its broker
qualification session does not generate pipeline analysis/planning outputs or
render a finished project. No substitute timings are reported here.

| Profile | Required measurements | Cold | Warm | Status |
|---|---|---|---|---|
| Full project | Total wall, peak memory, analysis/planning/edit/render/QA wall, Python CPU versus child/native/GPU/disk wait | Not measured | Not measured | Harness absent |
| Edit-heavy | Patch count, validation time, Resolve API calls, lease hold, recovery | Not measured | Not measured | Harness absent |
| Graphics-heavy | Frames/sec, encode time, peak RAM, disk writes, output parity | Not measured | Not measured | Harness absent |

`library/tools/perf_ledger.py` records capability and span wall time,
process CPU, child CPU, child peak RSS, and output bytes; new capability and
span rows also carry the selected runtime backends. Resolve spans include
lease wait/hold, while broker receipts retain their own job costs. GPU time and
disk wait are not currently measured by that ledger. A future fixed-input
qualification runner should carry one run ID through those sources and add
process-tree I/O/GPU measurements before publishing cold/warm end-to-end
numbers.

## Active native-capable backends

At measurement time `runtime_backends.snapshot()` reported:

```json
{
  "edit_core": "python",
  "resolve_broker": "python",
  "resolve_ui_driver": "unavailable",
  "graphics_renderer": "fframes"
}
```

The native edit extension was not selected, the broker used its Python
daemon, the native Resolve UI driver was unavailable, and the graphics
renderer resolved to FFrames. `pipeline_run.json` now records this selection
for each run; `ren doctor` reports it in both text and JSON output.
