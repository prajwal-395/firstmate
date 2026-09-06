# The three layers, and what each one uniquely catches

Adopted by the captain on 2026-09-05 from the scout's measurement of 1,063 GitHub job
records (2026-08-10 to 2026-09-05) and one local full-suite run. This file is the
"why"; `.github/workflows/ci.yml` is the "what", and its header points here.

## What went wrong

The full suite ran on every PR event because it was inherited from the GitHub Actions
Python starter template on 2026-08-09 and never revisited. `synchronize` re-runs it on
every push to a branch, so it ran a **mean of 2.5 times per PR** - 588 of 1,156 PR
suite-minutes in September were repeat runs of a branch that had already run it.

The account exhausted its 2,000 minutes on **26 August**. CI was dead for the last five
days of the month: **82 jobs blocked, zero verdicts.** On 5 September it stood at
**1,801 of 2,000** with the month's reset 26 days away. This design is what stops the
third occurrence.

## The layers

| Layer | Where | Trigger | Time | Billed | Uniquely catches |
|---|---|---|---|---|---|
| **0. Scoped tests** | crewmate, local | before opening the PR | seconds | 0 | The defect in the code just written. **18 of 26** September failure incidents. |
| **1. Full suite** | firstmate, local | once per batch, on the integrated branch, before it merges | **4m54s** | 0 | Global-invariant breakage no filename predicts, and **interaction between the PRs in the batch** - which no per-PR run can ever see. **8 of 26.** |
| **2. Clean room** | GitHub, `run-tests` label | once per batch | ~13 min | 13 min | Undeclared dependencies, absent system tools, platform assumptions. **Nothing local can see this.** |

Layer 1 is the workhorse and it is free. Layer 2 is narrow and is priced accordingly.

### Layer 1 is one command

```sh
scripts/full_suite_gate.sh
```

It prints one verdict line, last, beginning `FULL-SUITE GATE:`. It is **fail-closed**:
the verdict starts at `DID NOT RUN` and `PASS` additionally requires a JUnit report that
pytest itself wrote, with a positive executed-test count and zero failures and zero
errors. An interpreter that is not there, a collection error, an empty selection and an
interrupted run all read `DID NOT RUN`, never `PASS`.

The verdict has **four states**, not two:

| Verdict | Meaning | Exit code |
|---|---|---|
| `PASS` | Every test passed and no environment capability was absent | 0 |
| `NARROWED PASS` | Every test that ran passed, but some were skipped because an environment capability was missing - the run measured less than a full environment | 1 |
| `FAIL` | At least one test failed | 1 |
| `DID NOT RUN` | pytest did not produce a readable result | 1 |

When the verdict is `NARROWED PASS`, the gate prints a `MISSING CAPABILITIES` block
naming each absent capability, how many tests it cost, and how to install it. The
capabilities are derived from `tests/skip_audit.py`'s `ENVIRONMENT_CONDITIONS` - the
same declarations that validate skip reasons at runtime. Only conditions with a
`capability` field narrow the run; complementary pairs (where one side always fires)
do not.

Two test files are excluded by name because they drive the **running** DaVinci Resolve
and switch the current timeline out from under whoever is using the app. CI has no
Resolve, so they skip there and the exclusion costs no coverage.

The `heavy_ml` selection needs an interpreter carrying the ML stack **at the versions
`requirements.txt` declares** - importable is not enough, and a wrong version reports
success while measuring nothing. Building that interpreter, verifying it, and pointing
`FULL_SUITE_GATE_PYTHON` at it are in [`ML_ENVIRONMENT.md`](ML_ENVIRONMENT.md). When the
heavy tier cannot run, it is reported through the **same capability mechanism** as every
other environment gap rather than as a special case.

### Why layer 2 cannot be replaced by layer 1

A fresh Linux checkout with nothing preinstalled is the only configuration that can see a
dependency the project never declared. The evidence is direct and expensive:

- a missing transitive `httpx` failed **82 jobs across 21 branches** (10-12 August);
- commit `f577510` found there had never been an ffmpeg on the runner, so **27 library
  files' worth of audio and video measurement had been skipping itself, honestly and
  invisibly, on every build the workflow had ever run**;
- `parselmouth` has no cp314 wheel, and `mlx_vlm` is darwin-only.

No local machine produces those findings, because the local machine has the thing.

### Why layer 1 cannot be replaced by layer 2

One September failure in three was a **global invariant sweep** - a test whose filename
names a concept rather than the module it constrains, so no name-based scoping selects
it. **87 of 253 test files (34%)** are that shape. The canonical case: PR #504 added a
step directory, and what broke was
`tests/test_run_traceback.py::test_the_unwired_steps_are_exactly_the_ones_agents_md_names`.
The subset cannot be pre-computed - the natural static rule selects 67 files and captures
only 5 of the 9 that actually caught something.

## The arithmetic

Per batch gate: one job, `fast-checks` folded in as its first steps + the suite = **13
billed minutes** (GitHub bills whole minutes **per job**, so folding the lint steps into
the same job costs nothing).

| Cadence | Minutes/month | % of 2,000 |
|---|---:|---:|
| 1 batch/day | **390** | **20%** |
| 2 batches/day | 780 | 39% |
| 3 batches/day | 1,170 | 58% |

September's 68 merges cost **1,783 measured minutes**. Five days at one batch a day is
**65** - 27x cheaper - and still runs the full suite five times, against the **two**
genuine cross-module defects the month actually produced.

### Why `fast-checks` is folded in rather than kept per-PR

The report's steady-state design kept it on every PR. Its own numbers do not survive the
one-minute-per-job billing floor at this fleet's velocity: **129 billed minutes over 1-5
September**, which is ~26/day, or **~670 over the 26 days to 1 October against 199
remaining**. The scout's section 5 arithmetic treated that 129 as a monthly figure; it is
a five-day figure. The captain endorsed **390 min/month**, which is the batch row above
and does not include a per-PR job.

Folded in, the same checks cost **zero extra minutes** and run **first**, so a lint
failure ends the job in seconds and bills one minute instead of thirteen. The cost is
that a PR gets no automatic signal at all until its batch is gated - which is the
deliberate consequence of one gate per batch, not per PR.

**Restoring it per-PR after 1 October** is a small, deliberate edit: add
`opened, synchronize, reopened, ready_for_review` back to `pull_request.types`, lift the
four fast steps into their own unconditional `fast-checks` job. Budget for it:
**~775 min/month** at September's PR-event rate, taking the total to ~1,165 (58%).

## For the 26 days to 1 October

199 minutes remain, and the arithmetic does not close for any per-PR configuration: at
13.6 merges/day, even a single one-minute job per PR costs 354 minutes. The captain's
ruling is **one gate every other day** - 15 gates covers 26 days at 195 minutes. Layer 1
does the real work in that window; layer 2 becomes a periodic clean-room audit.

If that proves too tight, GitHub Pro is **$4/month for +1,000 minutes** and is cheaper
than an hour of anyone's attention. A self-hosted runner was costed and rejected: it
competes with the fleet for cores on a machine already at load average 18-23 on 10, it
queues silently when the Mac sleeps, and it **destroys the clean room**, which is the one
thing layer 2 exists for.

## The rule

**Layer 2 is not the project's test runner.** If a check would be useful, it goes in
layer 0 or layer 1. Widening the GitHub job back into a general test runner is exactly
how the account got here, twice.
