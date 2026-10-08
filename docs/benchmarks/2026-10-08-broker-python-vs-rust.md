# Broker Python/Rust benchmark

## Setup

- Machine: macOS 26.3, arm64 (Apple Silicon); Python 3.12.13 (ren venv)
- Source tree: this checkout; throwaway Python and Rust daemons each used
  their own socket and SQLite store under `/tmp/bench-brokers-*/`.
- Rust daemon: `native/target/release/ren-resolved` (built through
  `scripts/build_ren_resolved.sh`)
- Python daemon: `python -m library.tools.resolved serve`
- `REN_SHADOW_WATCHER=0`; the synthetic workload does not contact Resolve.
- Raw JSON: `/tmp/bench_brokers_before.json` and
  `/tmp/bench_brokers_after.json`; benchmark script:
  `/tmp/bench_brokers.py` (scratch outside the worktree).

## Correctness

`tests/tooling/protocol_parity.py` compares the same request bytes to both
daemons: **14/14 identical, 0 failures** - ping, submit (lease / unknown
kind / snapshot-missing-name), list, watchdog_status, status/result/cancel
unknown id, release with no grant, unknown op, status, result, and
list-after-leases.

A separate throwaway-daemon shutdown check after the final build returned
the shutdown reply in 0.13 ms and observed process exit in 139.50 ms.

The full benchmark attempted three `resolve.activity_probe` samples per
daemon. All six remained queued for the 60-second result timeout. The
preceding synthetic schedule burst leaves shared lease jobs running, so the
probe's exclusive lease cannot start. No Resolve work occurred in these
benchmark runs. The final latency repeat measured the same five synthetic
operations and omitted the blocked probe section. An earlier isolated live
sample, before this rerun, borrowed and restored `Ren Qualification`; it
completed in 14.9 seconds on Python and 13.9 seconds on Rust, with 1.0 second
of probe execution on each.

## Tail latency

Same synthetic workload in both runs: 200 ping requests, 200 list(limit 5)
requests, and 100 each of submit, result, and cancel for shared-mode lease
jobs. Values are p50 / p99 in milliseconds.

| Operation | Python before | Rust before | Python after | Rust after |
|---|---:|---:|---:|---:|
| ping | 0.16 / 0.34 | 53.71 / 57.59 | 0.15 / 0.38 | 0.06 / 0.35 |
| list | 0.40 / 0.71 | 106.68 / 110.89 | 0.35 / 0.76 | 0.19 / 0.69 |
| submit | 4.07 / 96.26 | 106.74 / 171.87 | 6.20 / 22.22 | 0.59 / 0.97 |
| result | 0.46 / 3.34 | 106.43 / 110.27 | 0.51 / 7.08 | 0.28 / 0.43 |
| cancel | 0.52 / 2.08 | 107.24 / 110.16 | 0.56 / 3.48 | 0.23 / 0.40 |

The accept loop caused the original request floor. It slept 50 ms after an
empty nonblocking `accept`, while each client request opened a fresh socket.
Rust now blocks in `accept`, so requests wake it directly. Shutdown sends a
local wake connection after writing its reply, allowing the same blocking
loop to exit promptly.

The first post-change run exposed a submit p99 outlier from the lazy Python
prepare worker startup. Rust now warms that worker with a harmless `lease`
validation at daemon startup. In the final run Rust was faster than Python at
both p50 and p99 for all five operations. The slowest Rust p99 was submit at
0.97 ms, below Python's 22.22 ms in the same run. Receipt reads use an
explicit column projection and indexed SQLite access rather than repeated
column-name lookup.

## Memory / CPU

| | Python daemon | Rust daemon |
|---|---:|---:|
| RSS before / after | 26,832 / 31,616 KB | 3,296 / 4,304 KB |
| cputime before / after | 0:00.04 / 0:00.56 | 0:00.01 / 0:00.10 |

Rust retained its lower memory footprint after warming the prepare worker.

## Decision

The Rust daemon now meets or beats Python p50 and p99 on every operation in
the measured workload. The original 50 ms floor came from accept polling;
the remaining first-submit tail came from lazy worker startup. Both are
removed without changing protocol replies, and shutdown wakes the blocked
accept loop instead of waiting for a polling tick.
