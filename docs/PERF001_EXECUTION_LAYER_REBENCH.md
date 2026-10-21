# PERF-001: the execution layer, rebenchmarked

**What this is:** the punchlist's PERF-001 - "Rebenchmark the current
execution layer" - run on current main, against the October 20
measurements rather than against an assumption that the fixes since
improved the whole path.

**Date:** 2026-10-21. **Engine:** `video-editing-pilot` at `d82d293aa`
(the measurement); the branch was rebased onto `e044c1f9e`, where main
had moved to by the time the lane finished.

## The question

> Repeat representative property edits, trims, captions, replacements,
> builds, and deliveries. Measure on both an idle machine and a machine
> with concurrent work. Separate planning, stage preparation, media
> composition, verification, promotion, broker queue time, and recovery.
> Record full user-visible latency. Compare against the October 20
> measurements rather than assuming recent fixes improved the entire
> path.

The punchlist asks for one number per operation, split three ways:

- **QUEUE WAIT** - the seconds before the operation was allowed near the
  one Resolve;
- **RESOLVE TIME** - the seconds it held Resolve;
- **REN TIME** - everything else: Ren's own work, the broker's transport,
  the model's answers.

## The method: the harnesses that already exist

No new harness. The measurement is the engine's own:

| piece | what it is |
|---|---|
| `scripts/bench_user_visible_latency.py` | opens the task row, runs the command with the performance ledger attached, prints the wall clock the person waited |
| `library/tools/perf_ledger.py` | writes the rows: the task row, the `resolve_wait` and `resolve_hold` spans, the touch path's own phases (`touchup_duplicate`, `touchup_conform_compose_verify`, `touchup_promote`) |
| `library/tools/perf_report.py` | reads them back - and, since this lane, splits every operation's wall into the three numbers above |
| `library/tools/critical_path.py` (PERF-012, on main) | the machine's other reader over the same rows: the run's limit, and whether it is queue contention, inference or transaction overhead |
| `ren resolved list --text` / `ren resolved kpi` | the broker's own view of the queue, read from the serving broker's receipts |

Every live run was:

- on a **Ren-owned scratch copy** (`ren-scratch-bench-demo2`, a copy of a
  real episode's Reel 13) - never the captain's project, timeline or
  media, and the podcast source media was read only;
- through the **serving broker**, waiting in its queue like any other
  job - no broker was started from this worktree, and no quiet window
  was asked for: the machine's lease owns Resolve contention;
- measured with the **same command round 2 used** for the property
  change, so the comparison is like for like.

## The baseline: October 20-21, re-split

Round 2's own measurements are in the scratch copy's own performance
ledger (`pipeline_output/logs/perf_ledger.jsonl`), one row per attempt.
They are re-split here with the code this lane added, so both columns
come from the same reader.

The window below is round 2's own latency block (every task row in the
scratch copy's ledger between 09:05Z and 10:45Z on 2026-10-21 -
fourteen operations across four classes, which is round 2's nine
reported attempts plus the five it recorded as immediate refusals).

| operation | n | outcomes | latency s (total) | p50 latency s | queue wait s | Resolve s | Ren s |
|---|---|---|---|---|---|---|---|
| `axi.property_change` | 6 | 4 refused (exit 1), 2 refused (exit 4) | 3054.2 | 480.3 | 2943.4 | 2.6 | 108.2 |
| `axi.caption_fix` | 3 | 2 refused (exit 1), 1 refused (exit 4) | 600.2 | 34.4 | 564.3 | 0.5 | 35.4 |
| `axi.overlay_position` | 1 | 1 refused (exit 4) | 489.1 | 489.1 | 335.0 | 122.3 | 31.8 |
| `axi.trim` | 4 | 3 refused (exit 1), 1 refused (exit 4) | 245.4 | 5.9 | 206.6 | 1.0 | 37.9 |

**Round 2's own ledger says the same thing its report says: the queue
was the latency.** 4,388.9 s of wall across fourteen attempts, of which
**4,049.3 s (92.3%) was the wait for the one Resolve**, 126.4 s (2.9%)
was time holding it, and 213.3 s (4.9%) was Ren's own work;
**not one of the fourteen attempts changed anything**.

Round 0 (2026-10-20, the quieter machine) is the only measurement of
the work itself, and it is the number this rebenchmark has to beat:

| operation (round 0) | user-visible | queue wait | work | inside the hold |
|---|---|---|---|---|
| property change (12 delivery px) | 638.2 s | 579.0 s | **58.5 s** | 57.4 s |
| caption nudge (20 delivery px) | 68.8 s | 10.9 s | **57.2 s** | 55.7 s |

So the October 20 baseline for a small touch is: **~57-58 s of work, and
a queue wait that ranges from 11 s to 579 s depending on what else the
machine is doing.** The engine's own work has been the small half of the
wall for two days.

## The rebenchmark: current main

Two operations on current main (`d82d293aa`), through the serving
broker, on the Ren-owned scratch copy, measured with the engine's own
bench wrapper:

```sh
bin/vep scripts/bench_user_visible_latency.py \
    --ledger <scratch>/pipeline_output/logs/perf_ledger.jsonl \
    --task-class axi.property_change \
    -- bin/vep manage_project.py touch-reel <scratch> 13 \
       --edits '[{"op":"set_properties","row":"V1","item":0,
                   "properties":{"Tilt":-648.041}}]'

bin/vep scripts/bench_user_visible_latency.py \
    --ledger <scratch>/pipeline_output/logs/perf_ledger.jsonl \
    --task-class axi.caption_fix \
    -- bin/vep manage_project.py touch-reel <scratch> 13 \
       --edits '[{"op":"set_properties","row":"V4","item":0,
                   "properties":{"Tilt":-1812.0}}]'
```

| operation | n | outcome | latency s | queue wait s | Resolve s | Ren s |
|---|---|---|---|---|---|---|
| `axi.property_change` | 1 | refused (`REN_RESOLVE_BUSY`, exit 4) | 3633.5 | **3600.0** | 0.0 | 33.4 |
| `axi.caption_fix` | 1 | refused at the project open (exit 1) | 4642.1 | **4607.3** | 3.4 | 31.4 |
| **total** | **2** | **nothing changed** | **8275.6** | **8207.4 (99.2%)** | **3.4 (0.04%)** | **64.8 (0.8%)** |

**Against October 20 and against round 2, the shape has not changed and
the numbers are worse:**

| | property change | caption fix |
|---|---|---|
| Oct 20 (round 0, quieter machine) | 638.2 s total = 579.0 queue + 58.5 work | 68.8 s total = 10.9 queue + 57.2 work |
| Oct 21 morning (round 2) | 6 attempts, 2943.4 s queue of 3054.2 s, 0 landed | 3 attempts, 564.3 s queue of 600.2 s, 0 landed |
| **Oct 21 afternoon (current main)** | **1 attempt, 3600.0 s queue of 3633.5 s, 0 landed** | **1 attempt, 4607.3 s queue of 4642.1 s, 0 landed** |

The engine's own work did not get slower - it is 33 s and 31 s, and
Ren's share of the wall is under 1%. **The wait grew from 579 s to
3,600-4,607 s, because the queue behind it grew.**

### The same rows, read by the machine's other reader

PERF-012's critical-path reader (`library/tools/critical_path.py`,
landed on main while this lane was measuring) reads the SAME ledger
rows and lands on the same split, from the other side:

```
Run run-20261021T193017Z-6fd13f (axi.property_change, outcome exit 4)
  person waited 3633.5s; instrumented wall 3600.1s; uninstrumented 33.4s
  limit: queue_contention 3600.0s (100.0%)
```

`queue_contention` is its name for the queue wait, and its
`uninstrumented` is this lane's "Ren time" - 33.4 s and 31.4 s, the
same two numbers to a tenth of a second. Two readers, one answer: on
current main, an operation on this machine is **100% queue contention**
and nothing else.

### What the write itself costs, when one lands

This lane's own writes did not land, so the work numbers below are the
machine's own successful touches on the SAME scratch copy today, read
out of the same ledger (two other lanes' runs, 13:27Z and 15:53Z):

| | Oct 20 (round 0 receipt) | today, successful touch 1 | today, successful touch 2 |
|---|---|---|---|
| total wall | 638.2 s | 1088.8 s | 1297.1 s |
| queue wait | 579.0 s | 900.3 s | 918.3 s |
| Resolve time (lease held) | 57.4 s | 184.7 s | 377.1 s |
| ... inside it: the direct write | - | 24.3 s | 25.1 s |
| ... inside it: stage preparation | ~1 s | 0.7 s | 0.9 s |
| ... inside it: conform + compose + verify | ~2.4 s | 41.1 s | 182.2 s |
| ... inside it: promotion | ~53.6 s | 55.2 s | 16.4 s |

**The work is the same shape it was on October 20, and the promotion is
still the biggest piece of it** - PERF-002's finding has not been
overtaken by the fixes since. What has changed is that the work is
reached later and later, and that on this machine it is reached at all
only by the lanes that got lucky.

## The largest remaining cost

**The broker queue. Not the engine, and not the model.**

| evidence | number |
|---|---|
| this lane's two operations on current main | 8,207.4 s of 8,275.6 s (99.2%) was the queue; the work was 64.8 s |
| the machine's own 24 h broker KPI (5,085 jobs) | wait p95 **891 s**, p99 **2,266 s**, max **3,585 s**; hold p95 68.6 s, max 2,493 s |
| the machine's own 24 h broker KPI | the queue was **idle for 3.8 s out of 86,400 s** - it was saturated the whole day |
| who holds the machine while people wait | **project-open jobs**: 208,180 s of blocked wait (31.1% of every blocked second), 1,112 waiters |
| what is in the queue | jobs whose **submitter has died** keep their place: 4,179 s, 3,908 s, and one running for 2,633 s with a dead owner |
| how long one job may hold the one Resolve | **no bound**: `library/tools/resolved/jobs.py:948` runs `resolve_axi.main(...)` in the worker with no timeout - one `project scratch open` job held the instance lease for 15 minutes, and the worker running it had used 33 s of CPU in its 2 h of life: it was blocked inside Resolve, not computing |

Three things in that table are defects the punchlist can act on, and
none of them is the engine's speed:

1. **A project open is the single largest consumer of the machine's
   Resolve time.** In 24 h, 789 `project` commands held the instance
   lease for 15,897.2 s (one of them for 1,339 s), and they caused
   208,180 s of blocked wait - 31.1% of every blocked second on the
   machine. The engine opens a project per write, and every lane's
   write now opens its own (PR 3123), so the number of opens went UP
   with the fix that made writes correct.

2. **A dead submitter's job is never reaped.** The broker's reaper
   (`native/ren-resolved/src/broker.rs:830`) reaps a **grant** whose
   owner is gone - deliberately, because an executed job's submitter may
   legitimately exit after submitting (`ren resolved submit` prints the
   id and returns). But a client that submitted with `--wait` and then
   **died** looks exactly the same, and its job holds a queue place
   until the broker reaches it: 4,179 s and 3,908 s of waiting measured
   today, in front of everyone.

3. **A broker job has no upper bound on its run.** A Resolve call that
   blocks (measured before this lane: 36 minutes inside
   `Fusion::RemoteApp::WaitPkt`) holds the one Resolve and the whole
   queue for as long as it blocks. The instance lease's own timeout
   bounds the WAIT, not the HOLD.

**The largest single cost inside the work, for the record:** the
promotion (55.2 s and 16.4 s of the lease-held time in today's two
successful touches), and on the master path the conform/compose/verify
phase (99.1 s of a 138.7 s hold, measured 2026-10-21 by the PERF-002
lane). Both are inside the lease, so neither is worth attacking until
the queue stops being the wall.

## What this run did NOT measure, and why

| not measured | why |
|---|---|
| a build's wall | the build is a longer job than a touch and every attempt would have queued for the same hour; the machine's own 75 build rows from today (`ren-scratch-breaktest`) show the shape: 2,732.2 s of queue wait, 2,424.5 s holding Resolve and 13,816.2 s of Ren's own work over 75 builds - a build is mostly Ren's work, unlike a touch |
| a delivery's wall | the same queue, behind the same hour; not attempted |
| a replacement's wall | the same queue; the touch path's replacement ops were not attempted |
| an idle-machine measurement | the machine was not idle and none was asked for: the broker's own KPI says the queue was idle for **3.8 s out of 24 h**. The October 20 numbers are the closest idle-machine baseline this project has |
| the work of a write that landed on THIS branch | no write landed in this lane's window: one attempt was cancelled by the broker at its own 3,600 s cap, the other was granted the lease after 4,607 s and then refused to open its project |
| the trim, as a third operation | the caption attempt already showed what the path does after the queue; a third attempt would have cost another hour of the same wait |
| any captain project, timeline or media | never touched; every live run was on a Ren-owned scratch copy, and the podcast source media was read only |

## The change this lane made

### 1. The split, printed per operation

The punchlist asks for one number per operation, split three ways, with
denominators. The ledger already RECORDED all three (the task row, the
`resolve_wait` spans, the `resolve_hold` spans) and the profile already
printed the task's wall. PERF-012's critical path (on main since
today) reads the queue and the limit **per run**. What was still missing
is the **per-operation** view `ren profile` prints: one row per
operation class, the three numbers side by side, and the denominator
each class is a mean of - so two runs can be compared class by class
without adding their totals.

**`library/tools/perf_report.py` now reads it back and prints it.**
`operation_split()` walks the tasks, attributes each lease span to the
operation whose window contains it, and reports three numbers per
operation plus the class totals with their denominators:

```
Operations (what the person waited through):
  operation                   n  latency s  queue wait s  resolve s     ren s
  axi.property_change         1     3633.5        3600.0        0.0      33.4
```

The rules the split keeps:

- **the three numbers add up to the operation's wall** - a split that
  lost a second would be a second nobody could account for;
- **an operation that never asked for a lease reports its queue wait and
  its Resolve time as unmeasured**, never as zero: a zero would read as
  "the queue was free", which is a fact nobody measured;
- **a lease span outside every operation's window is charged to none of
  them** - a lane's own lease must not inflate another lane's measured
  wait;
- **every class carries its denominator** (the number of operations it
  is a mean of, and how many of them the split could not measure), so
  two runs are compared class by class and never added together.

`tests/unit/tools/test_perf_report.py` names the five defects this
closes: the split that does not add up, the zero that reads as a free
queue, the class total without a denominator, the lease charged to
whichever operation was nearest, and the split that is computed and
never rendered.

### 2. A refusal that arrived as a crash

The caption attempt above was granted the lease after 4,607 s and then
refused to open its project - and the refusal reached the operator as a
**Python traceback** and `REN_UNEXPECTED`, with exit 1:

```
library.tools.resolve_project_open.ProjectOpenRefused: could not save
'Ren Qualification'; not opening 'Ren Scratch - vep-bench-demo2'
```

`ProjectOpenRefused` was a plain `RuntimeError` while every refusal
beside it (`TouchupRefused`, `ResolveBusy`) is a `RenRefusal` - so the
front door caught it as an unexpected error instead of rendering what,
why and fix with the refusal's exit code. **It is now a `RenRefusal`**
(`library/tools/resolve_project_open.py`), classified `resource_contention`,
with a what, a why and a fix at each of its four raise sites.

`tests/unit/resolve/test_resolve_project_open.py` names the defect: a
refusal the engine had already decided, reported to the operator as a
crash.

**What neither change does:** they change no behaviour, no timing and no
output of any edit. The first reads rows the ledger already wrote; the
second changes how a refusal is REPORTED, not whether it is made.

## How to reproduce

The measurement, on a Ren-owned scratch copy, through the serving
broker (the commands are in "The rebenchmark" above), and the reading
of it:

```sh
# the split, for any run in any project's ledger
bin/vep -m library.tools.perf_ledger <scratch> --run <run id>

# the same numbers as JSON
bin/vep -m library.tools.perf_ledger <scratch> --run <run id> --json

# the run's LIMIT, from the machine's other reader (PERF-012)
bin/vep -m library.tools.critical_path <scratch> --run <run id>
bin/vep -m library.tools.critical_path <scratch> --task-class axi.trim

# the machine's own view of the queue
bin/vep -m library.tools.resolved list --text
bin/vep -m library.tools.resolved kpi --hours 24 --json
```

The two attempts this lane measured are in the scratch copy's own
ledger, under the run ids `run-20261021T193017Z-6fd13f` (property
change) and `run-20261021T195954Z-d8edb9` (caption fix).
