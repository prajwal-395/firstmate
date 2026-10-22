# PERF-001b: the four unmeasured operations, run live

**Date:** 2026-10-22. **Engine:** `video-editing-pilot` at `2f5267cd2`
(the branch's base; no engine code was changed by this lane).
**Reader:** `library/tools/perf_report.py:operation_split` (PR 3157) and
`library/tools/critical_path.py` (PERF-012), read off the projects' own
`pipeline_output/logs/perf_ledger.jsonl`.

## What PR 3157 left unmeasured, and what this lane did about it

PR 3157 (`docs/PERF001_EXECUTION_LAYER_REBENCH.md`) measured 2 live
operations on current main and left four unmeasured because the queue
never freed: a build, a delivery, a replacement, and an idle-machine
run. This lane ran all four live, on Ren-owned scratch copies, through
the serving broker, measured with the engine's own bench wrapper:

```sh
bin/vep scripts/bench_user_visible_latency.py \
    --ledger <scratch>/pipeline_output/logs/perf_ledger.jsonl \
    --task-class <class> \
    -- bin/vep manage_project.py <verb> <scratch> ...
```

- **Copies:** `ren-scratch-perf001b-a` (cloned on disk from
  `ren-scratch-bench-demo2`, Resolve project `Ren Scratch -
  vep-bench-demo2`) and `ren-scratch-perf001b-b` (cloned on disk from
  `ren-scratch-breaktest`, Resolve project `Ren Scratch - breaktest`).
  Never the captain's project, timeline or media; the podcast source
  media was read only.
- **Broker:** the serving broker throughout. No broker was started from
  this worktree, no quiet window was asked for, `ren qualification
  reset` was never run.

## The four measurements

Every number below is one operation (`n = 1` per class, never summed).
`queue wait` / `Resolve` / `Ren` is the `operation_split` of the run's
own ledger rows; `limit` is the critical-path reader's verdict on the
same rows.

| # | operation | copy | outcome | latency s | queue wait s | Resolve s | Ren s | run id |
|---|---|---|---|---|---|---|---|---|
| M1 | `axi.property_change` (V1 item 0 `Tilt=-648.041`) | A | refused, exit 4 (`REN_TOUCHUP_REFUSED`, staging debris) | 637.553 | 0.015 | 124.385 | 513.153 | `run-20261022T045936Z-8d9d8a` |
| M4 | `reels.build` (`--only-reel 2`) | B | refused, exit 4 (`REN_RESOLVE_BUSY`, build turn held, waited 900 s) | 1105.383 | unmeasured | unmeasured | 1105.383 | `run-20261022T052821Z-a3e3fb` |
| M2 | `reel.deliver` (reel 13, signed off for this lane) | A | refused, exit 4 (`REN_RESOLVE_BUSY`, delivery session waited 900 s) | 904.969 | unmeasured | unmeasured | 904.969 | `run-20261022T142427Z-20a16f` |
| M3 | `axi.replacement` (`swap_pixels` caption file) | B | refused, exit 4 (`REN_TOUCHUP_REFUSED`, staging debris) | 1153.406 | 0.001 | 17.179 | 1136.226 | `run-20261022T144630Z-4a1563` |

Critical-path limits: M1 `transaction_overhead 106.2 s (85.2%)`; M3
`own_work 17.2 s (88.3%)`, uninstrumented `1133.9 s`; M4 and M2
`unmeasured` (no lease span was ever granted, so there is no split to
read — reported as unmeasured, never as zero).

**Denominators (the punchlist's scoring rule):** supported-completed 0,
supported-failed/refused 0, correctly-refused 4, output-failed-acceptance
0. Unmeasured is never zero: M4 and M2 carry no queue/Resolve split, and
the idle-machine run below carries no number at all.

## Against the October 20 baseline

Round 0 (2026-10-20, quieter machine) remains the only measurement of
the work itself: property change **58.5 s** of work (57.4 s inside the
hold, 579.0 s queue), caption fix **57.2 s** of work (55.7 s inside the
hold, 10.9 s queue).

- M1 reached Resolve (124.4 s held) and refused correctly on staging
  debris; its Ren-side 513 s is mostly the killed predecessor's
  re-reads, not a new cost of the work.
- M3 held Resolve 17.2 s; its 1136 s Ren-side is dominated by native
  crate rebuilds (`ren-graphics-fframes`, `ren-resolved`) triggered
  because the commands ran from an unmerged worktree — build time
  charged to Ren, not operation time.
- M4 and M2 never reached Resolve: the 900 s build-turn flock and the
  900 s delivery session cap expired first. Their whole walls are the
  wait, and the ledger correctly records no split rather than a zero.

The shape has not changed since PR 3157: **on this machine an operation
is the queue, and the work is the small half.** Nothing in this lane's
four attempts contradicts the October 20 work numbers, because no
attempt landed a write on this branch.

## What this lane did NOT measure, and why

| not measured | why |
|---|---|
| a landed write on this branch | M0 (the first property-change attempt, `run-20261022T042645Z-1f0837`) was killed at the 1800 s wrapper timeout mid-touch; it left the `Reel 13 … (rebuild staging)` container behind, which M1 and M3 then correctly refused on. Its outcome is landed-unknown, so it is not claimed as a measurement. |
| the staging clear | deleting the debris container is a Resolve write with no allowlisted path from here (`timeline delete` needs a recorded lifecycle artifact id; recorded `run` scripts may not switch projects). The refusal names the operator step: clear it in Resolve, then re-run `ren touch`. |
| an idle-machine run | the machine was never idle in this lane's window: every attempt queued behind exclusive holders (reels builds, touch-ups, qualification sessions), and the 6 h broker KPI over the window reads wait p95 **792.9 s**, p99 **1564.1 s**, max **1728.9 s** across 349 jobs. The October 20 round-0 numbers stay the closest idle-machine baseline. |
| a trim as a fifth operation | the brief names four; the replacement (M3) already exercised the staged composition path a trim would take. |

**Fidelity:** no write landed on this branch, so there is no
before/after regression to revert — supported-completed 0 of 4, and the
four refusals are the engine refusing correctly (debris fail-closed,
contention fail-closed), not defects.

## How to reproduce

The splits, for any run in any project's ledger:

```sh
bin/vep -m library.tools.perf_ledger <scratch> --run <run id> --json
bin/vep -m library.tools.critical_path <scratch> --run <run id>
bin/vep -m library.tools.resolved kpi --hours 6 --json
```

Queue snapshots taken beside each attempt are filed outside the
worktree (the lane's `/tmp/perf001b-queue-before-*.txt`); the ledger
rows are the durable record, in each copy's
`pipeline_output/logs/perf_ledger.jsonl` under the run ids above.

## Change in this lane

This file only. No engine code, no prompt, no contract, no AGENTS.md
rule was added or altered.
