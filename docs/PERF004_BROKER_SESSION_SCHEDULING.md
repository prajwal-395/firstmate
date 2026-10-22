# PERF-004: the broker's session scheduling - a bounded job, and a project open that does not wait

**Date:** 2026-10-21/22. **Engine:** `video-editing-pilot` at `18d183c2b`
(the measurement baseline) and at this branch (the change). **Lane:**
the PERF-004 lane, from the punchlist's
`PERF-004 - Optimize Resolve broker session scheduling`.

## The question

> Optimize Resolve broker session scheduling.
>
> - Keep exclusive write operations correctly serialized.
> - Prepare model outputs, renderable assets, and immutable plans outside
>   the Resolve lease when safe.
> - Hold the logical broker session across operations that require stable
>   project state.
> - Avoid unnecessary project and timeline switching.
> - Expose queue position, wait cause, and blocked resources.
> - Prioritize short interactive edits without starving longer operations.

The punchlist's direction, quoted: *"shifting Ren's development toward
completing one fully coherent editorial loop"*.

Two queue defects were open on main when this lane started, both named
by PERF-001 (PR 3157):

1. **a project open holds Resolve exclusively for minutes** - one held
   357 s while 17 jobs waited;
2. **a broker job has no bound** - one wedged Resolve call holds the
   whole machine until someone kills its process (a lane had to kill
   pid 33974 on 2026-10-21; on 2026-10-21/22 a
   `resolve.ui_transaction` job whose submitter had died ran 1412 s in a
   broker worker at 0% CPU and held six queued jobs until a lane killed
   worker pid 55751 by hand).

Dead-owner reaping and abandoned-session slot release had already landed
(PRs 3136, 3150, 3159) and are not redone here.

## What this lane changed

### 1. Every job carries a DECLARED BUDGET

`library/tools/resolved/job_budget.py` is the ONE enumeration: the
budget each job kind carries, the ceiling on a declared budget, and the
typed refusal. The budget is in the job's SHAPE before the job is
queued, so it is visible to the daemon, to the worker and to the reader
of a receipt.

| kind | budget | why |
|---|---|---|
| `timeline.snapshot`, `timeline.observe_current`, `fusion.registry_summary`, `resolve.activity_probe` | 120 s | a read |
| `timeline.apply_patch`, `timeline.semantic_edit`, `resolve.ui_transaction` | 300 s | a write and its verification |
| `resolve_axi` | 600 s | a command line: a project open measured 8.8 s at the median and 34.7 s for a park in the captain's project; a render for inspection takes minutes |
| `reel.promote_staged`, `reel.prepare_qualification_fixture`, `reel.probe_qualification_marker_loss`, `reel.copy_tv_ending`, `timeline.drt_export_probe`, `timeline.drt_recovery_probe` | 1800 s | a versioned take |
| `resolve.qualification_session`, `qualification.workflow` | 3600 s | a whole session over the live Resolve |
| `lease` (a grant) | none | the client holds the machine's turn for its own critical section; the bound that belongs to a turn is the dead-owner reaping |

A caller may declare its own (`params["budget_seconds"]`), up to
`MAX_BUDGET_SECONDS` (7200 s). A declared budget that is not a budget -
zero, negative, a boolean, a string, above the ceiling - is REFUSED at
the submission gate, not half-honoured at execution.

**Enforcement, in the three places it can be enforced from:**

- **the daemon (the hard bound).** The Rust daemon runs an executed job
  with a deadline (`worker.run_with_timeout`). A worker that has not
  answered within the budget is KILLED, which is the only way a Resolve
  call that blocks without releasing the GIL can be stopped from
  outside the process it wedges - and killing it releases the instance
  flock, so the one Resolve is given back. The job's receipt is then a
  TYPED REFUSAL: `refusal`, `blocker: resource_contention`,
  `next_action: wait_for_the_resource`, the budget and the elapsed time.
- **the worker (the soft bound).** The per-call deadline is capped by
  what is left of the budget, and the job stops at its next safe
  checkpoint once the budget is gone (`cancellation.checkpoint`). A job
  that comes back after its budget is reported as a refusal, not as a
  result - and a job that stopped at a committed checkpoint keeps its
  own result, because the write may have landed.
- **the Python daemon (the fallback).** The same receipt shape, enforced
  at the checkpoints: it runs the job in this process, so it cannot kill
  itself out of a wedged Resolve call. The Rust daemon is the default
  and the one that frees the machine.

### 2. A project open holds Resolve for the open, not for a wait

Two waits were inside the broker's turn, and a wait inside a turn holds
the one Resolve against every other lane:

- **the command's own wait for the scratch-origin slot.** The wait
  belongs to the CALLER: the holder of the slot is inside Resolve and
  needs that very turn to restore the origin and release it. Inside the
  turn the command no longer waits - it refuses FAST, with its own exit
  code, which the caller retries on. (This was already the design; the
  wait ran in the wrong place, in the dispatcher, and so it ran inside
  the turn.)
- **the caller's queued job.** While a scratch-lifecycle job is still
  QUEUED, the caller watches the slot, and a slot another live writer
  has taken CANCELS the queued job - the broker drops a queued job
  without running it - and the caller goes back to waiting outside the
  turn.

The machine's own receipts are the size of the thing this removes: ten
`project scratch open` jobs on 2026-10-21, each holding the machine's
turn for the whole 900 s lease timeout, and 208,081 s of blocked wait -
30% of every blocked second on the machine that day.

### 3. One broker serves the machine, and a check stops the broker it started

The PROD-001 clean-install check started a second broker (from
`/tmp/prod001-clean`, on `/tmp/ren-resolved.sock`) and left it serving
for seven hours, with two workers; two brokers against the one Resolve
move the cursor under each other and block project switches for every
lane.

- **A broker on the socket a plain client reaches now CLAIMS the
  machine** (a machine-wide lock, at a fixed path, so two brokers in
  DIFFERENT temp dirs can see each other) and a second one refuses to
  serve while the first holds it. A PRIVATE broker - one on a socket of
  its own, like the qualification harness's - is left alone.
- **The check stops the broker it started**, by that broker's own pid,
  on the sandbox's own socket, and never the machine's broker. The
  sandbox's broker is private (its socket is inside the sandbox) and
  drives the MACHINE's Resolve lease, so a check never takes the
  machine's broker away and never puts a second writer on the one
  Resolve.

## The measurement

**The method** is PERF-001's own, unchanged: a fixed set of operations
on a **Ren-owned scratch copy** (`ren-scratch-bench-demo2`, a copy of a
real episode's Reel 13), through the **serving broker**, timed with the
engine's own wrapper (`scripts/bench_user_visible_latency.py`) and read
back with the engine's own reader (`ren profile`, PR 3157's split).

**The fixed set:** two operations, PERF-001's two, so the comparison is
like for like:

1. `axi.property_change` - one delivery-pixel property nudge on Reel 13,
   row V1;
2. `axi.caption_fix` - one caption nudge on Reel 13, row V4.

**BEFORE** is the base commit (`18d183c2b`, the tree as it stood when
this lane started), run from an export of that commit; **AFTER** is this
branch. Every run is on the same scratch copy, through the same serving
broker, with the same command.

| operation | n | latency s | queue wait s | resolve s | ren s |
|---|---|---|---|---|---|
| **BEFORE** | | | | | |
| axi.property_change | 1 | 68.3 | 25.4 | 1.7 | 41.2 |
| axi.caption_fix | 1 | 1763.2 | 1754.0 | 2.2 | 7.0 |
| **the set** | **2** | **1831.5** | **1779.4 (97.1%)** | **3.9 (0.2%)** | **48.2 (2.6%)** |
| **AFTER** | | | | | |
| axi.property_change | 1 | 12.9 | 4.2 | 2.1 | 6.6 |
| axi.caption_fix | 1 | 14.1 | 3.6 | 2.4 | 8.1 |
| **the set** | **2** | **27.0** | **7.8 (28.9%)** | **4.5 (16.7%)** | **14.7 (54.4%)** |

All four attempts were REFUSED (`exit 4`, `REN_PROJECT_OPEN_REFUSED`:
the borrowed project, `Ren Qualification`, could not be saved while
another lane was inside it), so **nothing changed in the scratch copy in
either run** - the fidelity check is clean: before and after changed
exactly the same nothing.

### What this measurement does and does not show

**It does not show the fix.** The serving broker on this machine runs
the **installed engine's** worker (`REN_ENGINE_ROOT` points at the
supervisor's checkout, and the daemon starts its workers from there),
so the daemon half of this change - the budget's hard kill, the
refusal inside the turn - **was not live during the measurement**. The
before/after difference above is the MACHINE'S LOAD: the same caption
fix waited 1754 s in one run and 3.6 s in the next, against the same
broker, minutes apart. This table is the NOISE FLOOR of the metric on
this machine, and it is reported as such rather than as a proof.

**What shows the fix, and where:**

| the defect | the evidence | the after |
|---|---|---|
| a job with no bound (the live example: 1412 s, 0% CPU, six jobs waiting, killed by hand) | the Rust daemon's own test: a worker that never answers, a 1 s budget | the worker is killed, the receipt is a typed refusal (`refusal`, `blocker`, `next_action`, budget, elapsed), and the daemon does not wedge |
| a project open holding the turn for a wait | the machine's receipts: ten project opens at 900 s each, 208,081 blocked seconds | the command inside the turn refuses fast; the caller cancels a queued job whose slot was taken |
| two brokers on one machine | the PROD-001 check's second broker, seven hours, two workers | the machine-wide claim refuses the second broker; the check stops the one it started |

**The after-measurement, once the machine's engine is refreshed** (the
serving broker's worker is the installed engine's, so this lane cannot
make it live - starting a broker from a worktree is refused by the
client, and the brief forbids it):

```sh
bin/vep scripts/bench_user_visible_latency.py \
    --ledger <scratch>/pipeline_output/logs/perf_ledger.jsonl \
    --task-class axi.caption_fix \
    -- bin/vep manage_project.py touch-reel <scratch> 13 \
       --edits '[{"op":"set_properties","row":"V4","item":0,
                   "properties":{"Tilt":-1812.0}}]'

bin/vep -m library.tools.perf_ledger <scratch> --run <run id>
```

The number to read is the same one PERF-001 read: the queue wait share
of the operation's wall, with the machine's own queue beside it
(`ren resolved kpi --hours 24`) for the blocked seconds a project open
still causes.

## What this lane did NOT do

| not done | why |
|---|---|
| measure the fix live | the serving broker runs the installed engine's worker; a lane may not start or restart the machine's broker |
| change the queue's priorities, ageing or locality | the punchlist's list is already implemented (`scheduler.py`, and the queue report already exposes position, wait cause and blocked resources) |
| rework the touch path onto a logical session | the punchlist's "hold the logical broker session across operations" is a larger change to the write path than this lane's budget allowed, and it is measured against the queue this lane repairs |
| change any editorial behaviour | no step, no plan and no render changed: the budget refuses a job that overran, and the refusal says so |

## The evidence write-up

The incident write-ups are in
`docs/evidence/broker_job_budget_and_session_scheduling.md` - one file
for the three incidents, so that this lane's evidence does not append to
`docs/RULE_EVIDENCE.md` (every PR conflicts on that file).

## The tests

- `tests/unit/resolve/test_job_budget.py` - the budget table covers
  every kind; a caller's declared budget is the one used; a declared
  budget that is not a budget is refused at the gate; the refusal is
  typed and carries the budget and the elapsed time; a job over its
  budget stops at its next checkpoint; a job that stopped at a
  committed checkpoint keeps its result.
- `tests/unit/resolve/test_broker_turn_scheduling.py` - a command
  inside the broker's turn does not wait for the slot; the caller
  outside the turn still does; a queued scratch job is cancelled when
  the slot is taken.
- `tests/unit/resolve/test_broker_lifecycle.py` - a second broker
  refuses to serve while the machine's broker is up; a private broker
  is left alone; the machine's lock is the same file for every process;
  the check stops the broker it started, by its own pid.
- `native/ren-resolved/src/broker.rs` - a job that overruns its budget
  is refused with a typed receipt, and the daemon does not wedge.
- `native/ren-resolved/src/server.rs` - a second broker refuses to
  claim the machine; a private broker does not.
