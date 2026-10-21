# The critical path of three real tasks, and the next optimization each implies

**Measured** 2026-10-21 on the machine the captain's work runs on.
**Engine** this tree at `23cbdad76` (PR 3030's model cost, PR 3029/3077's touch phases,
PR 3087's broker KPI with wait attribution) plus this lane's critical-path reader.
**Subject** the Ren-owned scratch project `ren-scratch-breaktest` and its Resolve project
`Ren Scratch - breaktest` - a copy of the captain's Podcast (field test) reel. Never the
captain's project, never the captain's timeline, never the podcast source media.
`ren qualification reset` was not run.

The punch list asks for the COMPLETE task critical path: which stages overlap and which
are serial dependencies, whether the bottleneck is GPU, CPU, disk, model or Resolve, and
whether the task is limited by inference, by transaction overhead or by queue contention -
"to use the results to select the next optimization rather than assuming Rust or
parallelism will automatically help."

## What this lane added

`library/tools/critical_path.py` reads the performance ledger back as a critical path, and
`ren profile --critical-path` prints it. It answers the three questions the stage table
cannot:

| the question | what the reader now says |
|---|---|
| serial or overlapped? | per stage: the seconds it ran ALONE (exclusive) and the seconds it shared with another stage (shared), and WHICH stage it shared with |
| what is the bottleneck? | per stage: the resource its seconds went to - the local GPU's model, a model call, the disk, Resolve, the graphics engine, the broker's queue, or Ren's own CPU - with the seconds that earned it |
| what limits the task? | one of `inference`, `transaction_overhead`, `queue_contention`, or `own_work`, with the seconds and the share of the task's instrumented wall |

Plus, per stage, **the single next optimization the measurement implies, with its
seconds**. That last one is the punch list's point: the optimization is SELECTED BY THE
MEASUREMENT, not assumed. A component with no lever named for it is reported as such and
gets no invented action.

**Every second is counted once.** The ledger's rows nest - a container span holds the
spans it contains - so a naive sum charges the same second to the container and the child
and the totals exceed the wall they describe. A sweep over the measured intervals gives
every second to the narrowest span covering it, so the stage shares, the resource shares
and the family shares all add to the same wall.

## The denominator

| | |
|---|---|
| Ledger read | `ren-scratch-breaktest/pipeline_output/logs/perf_ledger.jsonl` |
| Runs in the ledger at the read | **156** (the scratch copy is SHARED: other lanes write to it, and the count moved to 165 while this report was written) |
| Task classes measured here | **3** (a small touch-up, a caption revision, a full reel build) |
| Live runs by this lane | **3** (one per class), all on the Ren-owned scratch copy, all through the serving broker. **All three measured the machine's queue, not the work** - see "What this did NOT measure" |
| Machine | one Resolve instance, one broker, **six other lanes working the same Resolve throughout** |
| Raw rows | `docs/benchmarks/2026-10-21-critical-path-raw.json` |

**The machine was shared, and that is the single biggest fact in this report.** A reel
build held the exclusive lease for **36 minutes** while this lane's touch-up waited. The
numbers below therefore measure Ren on a CONTENDED machine, which is the machine it ships
on. The "wait" column is the honest user-visible number, and the "work" column is what
the task cost once it got the session.

**The per-class counts are as read, and the ledger moves under other lanes.** Each class's
denominator is stated with its own run count below, so a reader can tell which numbers
were measured when.

**Which run each table below is read from** is named on the table. The raw JSON names the
MOST INSTRUMENTED run of each class, which for the two touch classes is this lane's own
live run - a run that is nearly all queue contention, because that is what the machine
gave it. The tables below quote the run that measured the WORK, and name the live run
beside it.

## The three task classes

Each class is read at the run that measured the WORK, since a run that measured nothing
says nothing about where the work went; the live run beside it is named on the table. The
denominator for each class is the ledger's own count of its runs.

### 1. A small touch-up - one property write on V1[0]

`ren touch` with `set_properties` on a picture clip's Tilt. **23 runs** of this class in
the ledger at the read, 1 finished, 22 instrumented.

| | |
|---|---|
| person waited | **638.2 s** (`run-20261020T224524Z-fcc0c7`, outcome ok) |
| of which waiting for the one Resolve | **579.0 s (90.8%)** |
| the work itself | **57.4 s** |
| the limit | **`queue_contention`** - 579.0 s of 637.5 s instrumented |
| this lane's live run | `run-20261021T173504Z-b31e16`: waited **2 640.7 s**, of which **2 604.0 s (99.9%)** was the queue; the lease was granted after 2 604 s and the task then refused on the Project Manager's browsed folder |

| stage | wall | serial or overlapped | exclusive | shared | bottleneck | next optimization |
|---|---|---|---|---|---|---|
| axi | 637.5 s | **serial** | 637.5 s | 0.0 s | **queue, 579.0 s** | the wait for the one Resolve: the queue is the machine's, so the work must not ask for the lease more than once |

The work inside the lease is 57.4 s of `resolve_hold` - the whole-reel bookkeeping PR 3077
split into three phases. **A rewrite in Rust does not touch this number**: 90.8% of the
task is a WAIT, and a wait is not code.

### 2. A caption revision - a caption card moved 20 delivery pixels up

`ren touch` with `set_properties` on a caption card's Tilt. **3 runs** of this class in
the ledger at the read, 1 finished, 3 instrumented.

| | |
|---|---|
| person waited | **68.8 s** (`run-20261020T225948Z-ebb9e2`, outcome ok) |
| of which waiting for the one Resolve | **11.0 s (16.1%)** |
| the work itself | **55.7 s** |
| the limit | **`own_work`** - 55.7 s of 68.1 s instrumented |
| this lane's live run | `run-20261021T181907Z-d100f6`: waited **2 730.0 s**, of which **2 696.1 s (100.0%)** was the queue; the lease was granted and the task then refused on the Project Manager's browsed folder |

| stage | wall | serial or overlapped | exclusive | shared | bottleneck | next optimization |
|---|---|---|---|---|---|---|
| axi | 68.1 s | **serial** | 68.1 s | 0.0 s | **resolve, 57.1 s** | the seconds inside the lease that no phase named: the touch path's phases (the duplicate, the conform, the promotion) are what fill this frame, so name them before optimising anything inside it |

**The caption revision is the one class whose cost is the work itself**, and its cost is
the same cost the touch-up's work has: 55.7 s of `resolve_hold` for a one-property change.
Two task classes, one mechanism. The touch path's phases (`touchup_duplicate`,
`touchup_conform_compose_verify`, `touchup_promote`) name what fills that frame on a run
that recorded them - see the PERF-002 lane's runs below.

### 3. A full reel build - `ren build --only-reel 13`

**73 runs** of this class in the ledger at the read, 0 finished, 59 instrumented. (No build
on this scratch copy has ever completed: every attempt ends in a named refusal - the Fusion
comp horizon, the verification gate, the OTIO placement - and the refusal is the answer,
not a cost. What the critical path below is measured on is the work the build DID before
it refused.)

| | |
|---|---|
| person waited | **511.7 s** (`run-20261021T030951Z-e1ab0a`, outcome `failed: ReelVerificationRefused`) |
| instrumented wall | **427.4 s** |
| the limit | **`transaction_overhead`** - 182.8 s of 427.4 s (42.8%) |
| this lane's live run | `run-20261021T190437Z-538aa2`: waited **3 632.4 s**, of which **3 576.2 s was the queue** and only **2.3 s was instrumented work** (a project switch and its read-back); the build then refused because the scratch project did not read back after the switch |

| stage | wall | serial or overlapped | exclusive | shared | bottleneck | next optimization |
|---|---|---|---|---|---|---|
| axi | 294.3 s | **overlapped** with planning (73.3 s) | 221.1 s | 73.3 s | **resolve, 237.7 s** | the placement's per-item transform replay, **154.8 s**: the placement writes each item's transform one API call at a time - one call for the whole reel's transforms |
| planning | 85.7 s | **overlapped** with axi (73.6 s) | 12.1 s | 73.6 s | **own code (CPU), 12.1 s** | the build's project survey, 10.5 s |
| rendering | 114.2 s | overlapped with planning (0.3 s) | 113.9 s | 0.3 s | **disk, 66.8 s** | the asset encode, 66.8 s |
| verification | 6.9 s | **serial** | 6.9 s | 0.0 s | own code (CPU), 6.9 s | no lever named: the phases inside it must be named first |

**The build's stages DO overlap, and the overlap is the planning pass running beside the
placement.** 73.3 s of the axi stage and 73.6 s of the planning stage are the SAME
seconds - the plan is compiled while the placement runs. That is the one place in the
build where parallelism has already been taken, and the reader says so with the seconds
rather than leaving the reader to infer it from a stage table.

**And the biggest single cost is 154.8 s of per-item transform writes** - one Resolve API
call per item, per transform. That is `transaction_overhead` exactly: not inference (0.0 s
of the build is a model), not the queue (56.6 s of it is a wait), and not Ren's own code.
**A rewrite in Rust would not move it, because the seconds are inside Resolve.**

## The finding

Across the three classes, the three limits split this way:

| class | inference | transaction overhead | queue contention | own work |
|---|---|---|---|---|
| small touch-up | 0.0 s | 1.1 s | **579.0 s** | 57.4 s |
| caption revision | 0.0 s | 1.4 s | 11.0 s | **55.7 s** |
| full reel build | 0.0 s | **182.8 s** | 56.6 s | 188.0 s |

**Not one of the three classes is limited by inference.** Zero seconds of all three were
spent in a model. The punch list offered "Rust or parallelism will automatically help" as
the thing to refuse, and the measurement refuses it on all three counts:

- **Rust would not help.** The dominant costs are inside Resolve (the placement's
  per-item transform writes, 154.8 s), in the broker's queue (579.0 s), or in Resolve's own
  lease frame (55.7 s). Ren's own CPU is 12.1 s of the build's planning stage and 44.2 s
  of the other recorded build.
- **Parallelism would help in exactly one place, and it is already taken.** The build's
  planning pass runs BESIDE the placement for 73.3 s; the other two classes are 100%
  serial (one stage, no overlap). There is no second place to parallelise.
- **The next optimization is different for each class, and the measurement says which.**
  The touch-up's is the queue (the work must not ask for the lease more than once); the
  caption revision's is the lease frame's unnamed work (name the phases before optimising
  anything inside it); the build's is the per-item transform replay (one call for the
  whole reel's transforms).

### What the reader found that the stage table could not

**The direct route's phase claimed NO stage.** `touchup_direct` (PR 3077's direct route)
was in `perf_ledger.STAGE_LAYERS` nowhere, so its seconds read as unattributed time - the
whole-reel bookkeeping the direct route AVOIDS was the one number the profile could not
show. Fixed in the stage map: `touchup_direct` is the AXI stage, like the three staged
phases it replaces.

Before and after, with the denominator:

| | before | after |
|---|---|---|
| runs in the ledger | 156 | 156 |
| runs whose reading changed | - | **2** (both carry a `touchup_direct` span) |
| runs whose reading did NOT change | - | **154** |
| `perf002-direct-1792563492`: the axi stage's wall | 1 622.505 s | **1 657.258 s** (+34.753 s) |
| `perf002-direct-1792563492`: unclassified seconds | 34.791 s | **0.038 s** (-34.753 s) |
| `perf002-direct-1792566634`: the axi stage's wall | 664.798 s | **693.449 s** (+28.651 s) |
| `perf002-direct-1792566634`: unclassified seconds | 28.651 s | **0.000 s** (-28.651 s) |

No other run's reading moved, and nothing was re-attributed to a stage that did not earn
it. The `reel_lifecycle` layer is a thin wrapper that names its phase on the row, so its
stage is read from the row (ten phases, one layer name).

## The PERF-002 runs, read the same way

The touch path's own phase measurements, on the Ren-owned scratch copy, read as a critical
path:

| run | route | wall | limit | the next optimization it implies |
|---|---|---|---|---|
| `perf002-direct-1792563492` | direct, then staged took over | 1 657.3 s | transaction_overhead (790.6 s, 47.7%) | the staged conform/compose/verify pass, **831.9 s** |
| `perf002-direct-1792566634` | direct | 693.4 s | own_work (577.1 s, 83.2%) | the staged conform/compose/verify pass, **548.4 s** |
| `perf002-staged-1792561289` | staged | 306.4 s | own_work (293.1 s, 95.7%) | the staged conform/compose/verify pass, **293.1 s** |
| `perf002-staged-1792565270` | staged | 111.9 s | own_work (85.1 s, 76.1%) | the staged conform/compose/verify pass, **85.1 s** |

The direct route's own phase is 28.7-34.8 s and the staged route's conform/compose/verify
is 85.1-831.9 s. **The direct route is the cheaper route by the measurement, and the
measurement now says which seconds it saves.**

## The queue, attributed

The reader attributes each waiting second to the job that HELD the one Resolve while it
waited, from the broker's own receipts (`ren resolved kpi`'s wait attribution, PR 3087).
On the touch-up's 579.0 s wait the holders were another lane's `vep-bench-demo section
12` run (1 957.5 s of the window's blocked seconds) and another lane's touch-up of the
same reel (87.2 s). **The wait is the machine's, and the measurement says who held it
rather than guessing.**

## What this did NOT measure

- **No build on this scratch copy has ever completed.** Every one of the runs ended in
  a named refusal. The build's critical path is measured on the work it DID before it
  refused - which is the work that costs, and is the whole of the cost.
- **This lane's three live runs all measured the MACHINE, not the work**, and that is
  the honest reading of them:

  | live run | waited | of which the queue | what it measured instead |
  |---|---|---|---|
  | `run-20261021T173504Z-b31e16` (touch-up) | 2 640.7 s | **2 604.0 s (99.9%)** | the lease wait |
  | `run-20261021T181907Z-d100f6` (caption revision) | 2 730.0 s | **2 696.1 s (100.0%)** | the lease wait |
  | `run-20261021T190437Z-538aa2` (reel build) | 3 632.4 s | **3 576.2 s (98.4%)** | the lease wait, then a project switch (2.3 s of instrumented work) |

  Each was refused after it got the lease - on the Project Manager's browsed folder, or
  on the scratch project not reading back after the switch. That refusal is a fact about
  this machine, not about the task classes, and it is why the tables above quote the
  recorded runs that measured the work.
- **The three classes' representative runs are three different runs**, each the most
  instrumented of its class - the classes' live runs on this machine were contention, not
  work, and the contention is reported as its own finding rather than folded into a
  class's cost.
- **No GPU seconds were measured.** The local VLM was not loaded on any of these three
  paths, so the GPU bucket is 0.0 s because nothing ran on the GPU, not because the GPU
  was fast.
- **The sample is one reel, one project, one machine, one window**, with six other lanes
  working the same Resolve. The queue numbers in particular are a property of that hour.

## How to reproduce it

```sh
# one run's critical path
bin/ren profile --critical-path <project> [--run <run id>] [--json]

# every run of one task class, read the same way
bin/ren profile --critical-path <project> --task-class axi.property_change

# the whole ledger, one row per task class
bin/vep scripts/bench_critical_path.py \
    <project>/pipeline_output/logs/perf_ledger.jsonl --json out.json
```

The reader changes nothing: it reads `pipeline_output/logs/perf_ledger.jsonl`, the same
file `ren profile` reads, and writes nothing.
