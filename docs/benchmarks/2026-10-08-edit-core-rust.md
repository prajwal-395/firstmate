# Edit core Python/Rust benchmark

## Setup

- Machine: macOS 26.3, arm64 (Apple Silicon)
- Python: 3.12.13
- Before source commit: `cdf2c572` (merged snapshot projection)
- After source: resident snapshot handle and target-limited projection in `native/ren-core/src/python.rs`, plus the wrapper and snapshot changes in this branch
- Rust extension: freshly built from the after source and unpacked under `.task-native/site`; its module path and baked fingerprint were checked against the worktree. `PYTHONPATH` pinned this private build during parity and benchmark runs.
- Prior-pass measurements: 100-call medians from the previous benchmark record. Resident follow-up measurements: 1,000 warm calls. The first compose call is timed after backend initialization and capability metadata setup; process startup is excluded. Snapshot preparation is reported separately and excluded from the resident compose and batch call measurements.
- Reproduce: `PYTHONPATH="$PWD/.task-native/site" EDIT_BENCH_REPETITIONS=1000 EDIT_BENCH_EDITS_PER_HANDLE=100 /Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep scripts/benchmark_edit_backend.py`

## Profile before optimization

A baseline `cargo flamegraph` profile of the 600-clip/64-op compose workload sampled 483 stacks. Inclusive samples attributed 209 samples (43.27%) to `edit_core_snapshot_from_py`; recursive Python-to-JSON conversion frames accounted for about 25%; `Snapshot::from_value` accounted for 48 samples (9.94%), `serde_json::from_value<Snapshot>` for 35 (7.25%), and `serde_json::from_value<EditPatch>` for 17 (3.5%). These are inclusive stack counts and overlap. The profile showed snapshot projection/conversion and serde dominated; the edit computation itself was not the main cost.

The composition measurement calls `patch_algebra.compose` from Python, including the backend switch, patch and semantics preparation, Python-to-extension conversion, Rust computation, and result conversion. Cases use 12/120/600 clip snapshots and 1/8/64 operations per patch. The span/verify measurement compares the individual Python loop with the Rust resident-handle batch. The after harness asserts exact verdict/reason and span/verify parity.

## Results

### Compose

| Snapshot | Ops per patch | Python warm before | Rust warm before | Python warm after | Rust resident warm after | Rust first after | Snapshot prep after |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 0.0069 ms | 0.3128 ms | 0.0065 ms | 0.0405 ms | 0.0542 ms | 0.2777 ms |
| 120 clips | 8 | 0.0470 ms | 2.7186 ms | 0.0460 ms | 0.0897 ms | 0.0945 ms | 0.0813 ms |
| 600 clips | 64 | 2.3651 ms | 19.7324 ms | 1.8021 ms | 1.5494 ms | 1.5778 ms | 0.3590 ms |

### Span and verify

| Snapshot | Ops | Python individual loop after | Rust resident batch after | Rust first batch call | Snapshot prep (span + verify) | Rust batch speedup |
|---:|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 0.0017 ms | 0.0093 ms | 0.0093 ms | 0.0315 ms | 0.18x |
| 120 clips | 8 | 0.0282 ms | 0.0357 ms | 0.0357 ms | 0.1690 ms | 0.79x |
| 600 clips | 64 | 0.9142 ms | 0.2894 ms | 0.2894 ms | 0.9020 ms | 3.16x |

The previous post-projection measurements were 0.1076/0.5922/5.0471 ms warm Rust compose (12/120/600 clips) and 0.2339/2.1592/12.0727 ms Rust span/verify batch. The resident and target-limited paths cut the large compose case by 69% and the large batch case by 98% relative to that prior post-projection batch, while adding measured preparation cost outside steady-state calls.

## Historical decision after snapshot projection

At this earlier measurement point, Python remained the default and Rust stayed selectable with `REN_EDIT_BACKEND=rust`. The native path was slower on every warm compose and batch case. The resident-call follow-up below supersedes those timings and records the remaining small-case limitation. Broker latency remains out of scope.

## Resident-call follow-up after PR 2209

The first resident-handle profiles (60,000 repeated calls per profile) showed that the 12-clip case was still paying for Python patch decoding, serde value construction, and semantics parsing on every composition. In the 12-clip flamegraph, `property_patch_from_py` accounted for 26.31% of sampled stacks, `py_to_json` frames reached 34.28%, and `compose` accounted for 14.37%. The 120-clip profile attributed 38.65% to patch extraction and 25.81% to the compose core; these are inclusive stack shares and overlap. A subsequent 12-clip profile, after direct semantics/result conversion, still attributed 44.06% to patch extraction, 21.16% to snapshot access, 16.67% to compose, and 6.09% to semantics parsing. This led to direct primitive extraction, enum-backed semantics, precomputed operation keys, avoiding repeated handle/target preparation, and a fast path for a nonmeeting pair of single property writes.

The benchmark reports snapshot preparation amortized across N calls on one timeline. The table uses N=100 resident calls, a favorable long-lived timeline session; the harness allows this to be changed with `EDIT_BENCH_EDITS_PER_HANDLE`. First calls are timed after extension initialization and capability metadata setup; warm calls are the median of 1,000 repetitions. Preparation is separate from call latency. One-shot first-call readings include lazy runtime initialization and are noisy, especially on the tiny 12-clip case.

| Snapshot | Ops per patch | Python compose first / warm | Rust compose first / warm | Rust snapshot prep | Rust prep + 100 calls, per edit | Python span/verify first / warm | Rust batch first / warm | Rust batch prep | Rust batch prep + 100 calls, per edit |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 7.7593 / 0.0070 ms | 0.0536 / 0.0074 ms | 0.0766 ms | 0.0082 ms | 0.0210 / 0.0016 ms | 0.0218 / 0.0052 ms | 0.0741 ms | 0.0060 ms |
| 120 clips | 8 | 0.1161 / 0.0469 ms | 0.1372 / 0.0268 ms | 0.2732 ms | 0.0295 ms | 0.0488 / 0.0288 ms | 0.0687 / 0.0242 ms | 0.2354 ms | 0.0266 ms |
| 600 clips | 64 | 2.2191 / 2.1144 ms | 0.3445 / 0.2983 ms | 0.3546 ms | 0.3018 ms | 1.0264 / 1.0203 ms | 0.2550 / 0.2248 ms | 0.9576 ms | 0.2344 ms |

At 12 clips/1 operation, warm Rust compose is within measurement noise of Python and slightly slower (0.0074 vs 0.0070 ms); its preparation-amortized result is slower. The resident span/verify batch is slower at 12 clips (0.0052 vs 0.0016 ms) because two native calls and conversion of two one-element results cost more than the tiny Python work; preparation amortized across 100 calls remains slower. The 120- and 600-clip resident compose and span/verify cases beat Python on warm calls and at N=100 amortization. The captain approved Rust as the default after PR 2213 merged. Python remains selectable with `REN_EDIT_BACKEND=python` and remains the fallback when the extension is missing, stale, or broken. The 12-clip cases are the measured limitation behind that choice.
