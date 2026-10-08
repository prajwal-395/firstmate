# Edit core Python/Rust benchmark

## Setup

- Machine: macOS 26.3, arm64 (Apple Silicon)
- Python: 3.12.13
- Before source commit: `c92823f8efe165c4fd4d488c567b689bdd14a771`
- After source: this task's edit-core snapshot projection in `native/ren-core/src/python.rs`
- Rust extension: freshly built from the after source and unpacked under `.task-native/site`; its module path and baked fingerprint were checked against the worktree. `PYTHONPATH` pinned this private build during parity and benchmark runs.
- Before measurements: median of 100 calls. After measurements: median of 20 calls. `first_ms` is the first call in the same process after backend initialization, not a fresh-process startup measurement.
- Reproduce: `/Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep scripts/benchmark_edit_backend.py`

The composition measurement calls `patch_algebra.compose` from Python, including the backend switch, Python-to-extension conversion, serde decoding, Rust computation, and result conversion. Cases use 12/120/600 clip snapshots and 1/8/64 operations per patch. The span/verify measurement compares individual Python calls against the batched production wrappers on the same representative data.

## Results

The after run used the same three snapshots and operations. The native boundary now reads only clip identity/ranges for `clip.set_property` composition and span calls, and adds transforms only for property read-back. Other operation shapes keep the general full-snapshot conversion.

| Snapshot | Ops per patch | Snapshot bytes | Python compose warm | Rust compose warm | Rust vs Python |
|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 4,931 | 0.0069 ms | 0.3128 ms | 45.3x slower |
| 120 clips | 8 | 48,665 | 0.0470 ms | 2.7186 ms | 57.8x slower |
| 600 clips | 64 | 244,985 | 2.3651 ms | 19.7324 ms | 8.3x slower |

| Snapshot | Ops per patch | Python compose after | Rust compose after | Rust vs Python after |
|---:|---:|---:|---:|---:|
| 12 clips | 1 | 0.0073 ms | 0.1076 ms | 14.7x slower |
| 120 clips | 8 | 0.0450 ms | 0.5922 ms | 13.2x slower |
| 600 clips | 64 | 2.0248 ms | 5.0471 ms | 2.5x slower |

First calls in-process after the change were respectively 0.0238/0.1203 ms, 0.0477/0.5992 ms, and 1.9886/5.1557 ms (Python/Rust). The before first calls were 0.0297/0.3994 ms, 0.0534/2.6665 ms, and 2.9441/19.6289 ms (Python/Rust).

| Snapshot | Ops | Python loop | Python batch | Rust loop | Rust batch | Rust batch call reduction |
|---:|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 0.0016 ms | 0.0022 ms | 0.5353 ms | 0.5419 ms | 1.0x |
| 120 clips | 8 | 0.0288 ms | 0.0299 ms | 41.5774 ms | 5.4165 ms | 7.7x |
| 600 clips | 64 | 1.0451 ms | 1.0493 ms | 1,631.9832 ms | 32.5314 ms | 50.2x |

| Snapshot | Ops | Python loop after | Python batch after | Rust loop after | Rust batch after | Rust batch call reduction |
|---:|---:|---:|---:|---:|---:|---:|
| 12 clips | 1 | 0.0016 ms | 0.0022 ms | 0.2291 ms | 0.2339 ms | 1.0x |
| 120 clips | 8 | 0.0286 ms | 0.0293 ms | 15.7153 ms | 2.1592 ms | 7.3x |
| 600 clips | 64 | 1.0148 ms | 0.9796 ms | 649.4072 ms | 12.0727 ms | 53.8x |

## Decision

Keep Python as the default and Rust selectable with `REN_EDIT_BACKEND=rust`. The direct projection reduced Rust composition from 0.3128/2.7186/19.7324 ms to 0.1076/0.5922/5.0471 ms, and reduced the 600 clip Rust span/verify batch from 32.5314 ms to 12.0727 ms. These measurements show the Python-object conversion and serde boundary dominates: removing unrelated snapshot fields materially cuts time, while the actual Rust algebra still cannot recover the per-call boundary cost. Rust remains slower on every measured warm compose, individual span/verify, and batched span/verify case, so it is not eligible to become the default. Broker latency remains out of scope.
