# Edit core Python/Rust benchmark

## Setup

- Machine: macOS 26.3, arm64 (Apple Silicon)
- Python: 3.12.13
- Source commit: `c92823f8efe165c4fd4d488c567b689bdd14a771`, with this task's batched-binding changes in the worktree
- Rust extension: freshly built with `scripts/build_ren_native.sh /Users/prajwal/.local/share/vep/venv-py312/bin`
- Warm measurements: median of 100 calls. `first_ms` is the first call in the same process after backend initialization, not a fresh-process startup measurement.
- Reproduce: `/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep scripts/benchmark_edit_backend.py`

The composition measurement calls `patch_algebra.compose` from Python, including the backend switch, Python-to-extension conversion, serde decoding, Rust computation, and result conversion. Cases use 12/120/600 clip snapshots and 1/8/64 operations per patch. The span/verify measurement compares individual Python calls against the batched production wrappers on the same representative data.

## Results

| Snapshot | Ops per patch | Snapshot bytes | Python compose warm | Rust compose warm | Rust vs Python |
|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 4,931 | 0.0069 ms | 0.3128 ms | 45.3x slower |
| 120 clips | 8 | 48,665 | 0.0470 ms | 2.7186 ms | 57.8x slower |
| 600 clips | 64 | 244,985 | 2.3651 ms | 19.7324 ms | 8.3x slower |

First calls in-process were respectively 0.0297/0.3994 ms, 0.0534/2.6665 ms, and 2.9441/19.6289 ms (Python/Rust).

| Snapshot | Ops | Python loop | Python batch | Rust loop | Rust batch | Rust batch call reduction |
|---:|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 0.0016 ms | 0.0022 ms | 0.5353 ms | 0.5419 ms | 1.0x |
| 120 clips | 8 | 0.0288 ms | 0.0299 ms | 41.5774 ms | 5.4165 ms | 7.7x |
| 600 clips | 64 | 1.0451 ms | 1.0493 ms | 1,631.9832 ms | 32.5314 ms | 50.2x |

## Decision

Keep Python as the default and Rust selectable with `REN_EDIT_BACKEND=rust`. The Python/Rust conversion boundary dominates small and typical operations; composition loses even after processing the patch wholly in one native call. Batching the repeated span and verify calls cuts the large Rust loop by 50.2x, but that batched Rust case remains 31.1x slower than Python. The code now batches those multi-operation prepare/read-back sites and releases the GIL while native span and verify loops compute. No representative case supports making Rust the default.
