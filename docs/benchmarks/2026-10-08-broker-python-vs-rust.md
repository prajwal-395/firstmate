# Broker Python/Rust benchmark

## Setup

- Machine: macOS 26.3, arm64 (Apple Silicon); Python 3.12.13 (ren venv)
- Source tree: this checkout at `5eaf17ba` (Rust-broker-default stack:
  2172 merged, worker borrowing exemption + parity-harness default fix
  in the worktree)
- Rust daemon: `native/target/release/ren-resolved` (same tree)
- Python daemon: `python -m library.tools.resolved serve` (same tree)
- Both daemons throwaway, own sockets under `/tmp/bench-brokers-*/`,
  store beside the socket (fully isolated from the shared broker);
  `REN_SHADOW_WATCHER=0`
- Live Resolve contact is limited to `watchdog_status` (bounded
  shared-lease probe) and the probe sample below (borrow + restore).
  Everything else never leaves the daemon process.
- Raw JSON: benchmark script at `/tmp/bench_brokers.py`,
  probe re-run at `/tmp/bench_probes.py`
  (scratch outside the worktree, not committed)

## Correctness

`tests/tooling/protocol_parity.py` (same request bytes to both
daemons): **14/14 identical, 0 failures** - ping, submit
(lease / unknown kind / snapshot-missing-name), list, watchdog_status,
status/result/cancel unknown id, release with no grant, unknown op,
status, result, list-after-leases.

Two live behaviours observed identical on both daemons:

- `resolve.activity_probe` while the captain's project cannot be saved
  fails **closed and identically**: `QualificationError: could not
  save 'Podcast (field test)'; not borrowing` (first attempt, both).
- Retry minutes later: both `done`, `elapsed: 1.0`,
  `restored: true` (captain's project, timeline and page read back).

## Tail latency (same synthetic scheduling workload)

200x ping, 200x list(limit 5), 100x submit/result/cancel of
shared-mode lease jobs (full schedule path, no Resolve contact):

| op | Python p50 | Python p99 | Rust p50 | Rust p99 |
|---|---|---|---|---|
| ping | 0.15 ms | 0.24 ms | 52.81 ms | 55.95 ms |
| list | 0.35 ms | 0.68 ms | 105.95 ms | 111.68 ms |
| submit | 0.67 ms | 36.55 ms | 106.45 ms | 201.15 ms |
| result | 0.38 ms | 0.87 ms | 106.35 ms | 110.08 ms |
| cancel | 0.36 ms | 0.66 ms | 106.52 ms | 110.10 ms |

The Rust daemon answers on an approximately **50 ms floor**
(ping p50 52.8 ms, every op a multiple of it) - the serve loop's
short poll interval (`native/ren-resolved/src/main.rs`), against the
Python daemon's threaded socketserver. One Python submit outlier at
36 ms (max); otherwise sub-millisecond throughout.

Live probe sample (`resolve.activity_probe`, seconds=1, borrowed +
restored): python wall 14.9 s, rust wall 13.9 s - both dominated by
shared-lease acquisition while the machine's Resolve was busy, not by
the daemon; execution 1.0 s each.

## Memory / CPU

| | Python daemon | Rust daemon |
|---|---|---|
| RSS before / after | 26,784 / 22,896 KB | 3,424 / 3,360 KB |
| cputime before / after | 0:00.05 / 0:00.50 | 0:00.01 / 0:00.31 |

The Rust daemon holds roughly **7x less memory** (3.4 MB vs 23 MB);
CPU is noise-level on both for this workload.

## Decision

No broker changes. Correctness is identical (14/14 + matching live
fail-closed and borrow/restore behaviour); the Rust daemon is far
leaner in memory; its ~50 ms per-request floor is the serve loop's
poll interval, not a scheduling defect - narrowing it is a
responsiveness trade, not a correctness fix, and out of scope for
this task. Revisit only if client-visible latency (e.g. overlay
cancel snappiness) measures worse, not on synthetic burst numbers.
