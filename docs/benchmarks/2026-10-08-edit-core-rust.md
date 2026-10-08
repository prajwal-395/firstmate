# Edit core Python/Rust benchmark

## Setup

- Machine: macOS 26.3, arm64 (Apple Silicon)
- Python: 3.12.13
- Before source commit: `cdf2c572` (merged snapshot projection)
- After source: resident snapshot handle and target-limited projection in `native/ren-core/src/python.rs`, plus the wrapper and snapshot changes in this branch
- Rust extension: freshly built from the after source and unpacked under `.task-native/site`; its module path and baked fingerprint were checked against the worktree. `PYTHONPATH` pinned this private build during parity and benchmark runs.
- Before measurements: 100-call medians from the prior benchmark record. After measurements: median of 100 calls. `first_ms` is the first call in the same process after backend initialization; it excludes fresh-process startup. Snapshot preparation is reported separately and excluded from the resident compose and batch call measurements.
- Reproduce: `PYTHONPATH="$PWD/.task-native/site" EDIT_BENCH_REPETITIONS=100 /Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep scripts/benchmark_edit_backend.py`

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

## Decision

Keep Python as the default and Rust selectable with `REN_EDIT_BACKEND=rust`. The optimization improves every Rust case substantially, but Rust is still slower on compose at 12 and 120 clips and on span/verify at 12 and 120 clips. Therefore it does not meet the brief's all-cases gate for default selection. On resident calls, Rust is faster only at 600 clips/64 ops: 1.16x for compose and 3.16x for span/verify. Snapshot preparation is separate; including it would make those measured end-to-end cases slower too. The small cases are dominated by fixed Python/native boundary overhead, so they remain on the Python default. Broker latency remains out of scope.
