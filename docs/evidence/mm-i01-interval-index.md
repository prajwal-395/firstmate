# I01: temporal overlap queries ride a max-end segment tree (2026-10-22)

## Claim
`EvidenceIndex.observations_overlapping` / `observations_at` scanned every
earlier-starting record per query (bisect on starts, then a linear filter
over the whole prefix, including an O(prefix) list-slice copy). On the
Ren-owned scratch snapshot
`ren-scratch-l3wave-c/project_model/snapshot.json` (116,059 observations,
read-only, no model loaded) a narrow 2,000-tick window query scanned a mean
28,512 records to return 18 on the largest clock (55,917 rows). The build now
keeps a static max-end segment tree per clock over the same start-sorted rows
and prunes subtrees that start too late or end too early. Results are
identical, row for row and in the same (start, end) order.

## What was measured (before -> after)

Real snapshot, 200 random narrow (W=2000 ticks) queries per clock, same battery:

| clock rows | before avg/query | after avg/query | scanned (old) | mean k |
|---|---|---|---|---|
| 55,917 | 0.91 ms | 0.02 ms | 28,512 | 18.4 |
| 35,681 | 0.45 ms | 0.01 ms | 18,780 | 10.4 |
| 9,517 | 0.09 ms | 0.04 ms | 4,136 | 100.3 |

Point queries (`observations_at`, 200 random ticks, largest clock):
1.30 ms -> 0.01 ms per query.

Synthetic scaling (`docs/benchmarks/vep-mu-graph-bench.py build_model`),
narrow late-window query (worst case: window at the end of the span, k=30):

| N | before | after |
|---|---|---|
| 1,000 | 0.035 ms | 0.009 ms |
| 5,000 | 0.160 ms | 0.010 ms |
| 20,000 | 1.329 ms | 0.011 ms |
| 80,000 | 6.478 ms | 0.011 ms |

Query cost is flat across 80x interval growth at fixed k: narrow queries
approach O(log N + k). One-time index build at N=80,000: 0.065 s (lazy, paid
once per collection touched, as before). Real-model build unchanged
(0.261 s -> 0.275 s, noise).

## Recall / correctness proof
- 700/700 recorded queries (60 windows + 40 point lookups x 7 clocks on the
  real snapshot, widths 1..50,000 ticks, plus unknown-clock) return identical
  id lists in identical order before vs after.
- `tests/unit/context/test_evidence_graph.py`: 21 passed, including two new
  defect-naming tests (adversarial intervals: whole-span giants, adjacent
  halves sharing only a boundary tick, identical starts, width-1 rows, second
  clock; shuffled-insert order preservation). No test touches a real project.
- Bench parity block (`build_model(20000)`, 20 windows vs linear scan):
  identical; indexed 200-window sweep 0.158 s -> 0.020 s.
- Enforcing gate clean: `ruff check --config ruff-ci-gate.toml` passes on
  both changed files.

## Why this shape
The model is read-only after build, so the tree is static: one max-end array
per clock (2 x pow2 rows), no rebalancing, no new store. Traversal runs
left-to-right so the stable (start, end) ordering the bisect scan produced is
preserved exactly. `observations_at(tick)` is the same scan with the
`start <= tick < end` read, keeping its exact time semantics (verified by the
700-query battery, which covers both paths).

## Known non-goal
`docs/benchmarks/vep-mu-graph-bench.py` still fails at its license-stamp
assert (`stamp_license(...) == "approved"`, now `"conditional"`): pre-existing
on HEAD, unrelated to the temporal index, left for its owning lane.
