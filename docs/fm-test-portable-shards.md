# Firstmate portable test shards

`bin/fm-test-run.sh` owns portable lane composition and execution.
`bin/fm-test-isolation-proof.sh` owns the proven-isolated candidate set.

## Verification inputs

Balance hints come from serial runs of the real lanes on `ubuntu-latest`.
The concurrent isolation proof in [fm-test-isolation-proof.md](fm-test-isolation-proof.md) establishes concurrency safety, not serial CI duration.
Local timings are not interchangeable with CI timings: platform and machine load can affect each script differently and change their relative weights.

The retained parallel hints are the slowest completed value each script reached across six green CI runs on 2026-09-22: [35661755328](https://github.com/prajwal-395/firstmate/actions/runs/35661755328), [35663110890](https://github.com/prajwal-395/firstmate/actions/runs/35663110890), [35664891424](https://github.com/prajwal-395/firstmate/actions/runs/35664891424), [35665191329](https://github.com/prajwal-395/firstmate/actions/runs/35665191329), [35670715035](https://github.com/prajwal-395/firstmate/actions/runs/35670715035), and [35673123515](https://github.com/prajwal-395/firstmate/actions/runs/35673123515).
Both shards completed in all six, so every one of the 24 candidates has six samples from the uploaded `fm-test-timing-portable-parallel-*` artifacts.
Observed maxima provide conservative packing weights, not an upper bound on future durations.

The measurements cover all 24 candidates with six samples per script.
A cancelled lane's elapsed duration is only a lower bound; its unfinished scripts have no completed duration for that invocation.
The complete historical run supplies tail-script hints, not a completion time for any later cancelled invocation or for the rebalanced jobs.

## Parallel lanes

The three parallel lanes use longest-processing-time assignment over those hints.
[`bin/fm-test-run.sh`](../bin/fm-test-run.sh) holds the duration values in `portable_parallel_weight_hints` and the ordered memberships and lane-specific prerequisite constraints beside `list_portable_parallel_1`, `list_portable_parallel_2`, and `list_portable_parallel_3`.
Read the derived packing estimates with that runner's `--check-coverage`; its header and `--help` own the output fields and the selection-specific `--list-scheduled` weight rules.
The largest individual hint sets a lower bound on the estimated duration of any split, regardless of how evenly the remaining work is assigned.
The CI cap follows the three-tier timeout policy in [Timeouts](#timeouts) below.

[`tests/fm-test-run.test.sh`](../tests/fm-test-run.test.sh), in `test_portable_parallel_lanes_stay_duration_balanced`, requires every parallel member to have a hint and the lane sums to differ by no more than five percent of the largest sum.
Its scheduling regressions also check stored parallel lane order and preserve serial-weight scheduling for other selections.
These checks do not detect a script outgrowing an existing hint or establish measured job headroom.
Refresh `portable_parallel_weight_hints` with the slowest completed `duration_ms` per script from several green CI runs' `fm-test-timing-portable-parallel-*` artifacts whenever the parallel set gains scripts or a member grows materially.

Two shards stopped fitting in September 2026: the suite totals 17.2 minutes of slowest-sum script time, so even a perfect two-way split packs 8.6 minutes per lane, and the worst observed run under the old packing measured 9.6 minutes of script time against the 10-minute job cap with only ~15 seconds of job setup on top.
The four largest scripts also swing 46-71 seconds run to run, so a two-way split re-straddles on the next unlucky run rather than holding margin.
Three shards pack to 5.7 minutes of slowest-sum script time each (343519, 342959, and 343193 ms, 0.2 percent imbalance), and the worst of the six measured runs totals 5.7 minutes under the new packing, so the ~6-minute job wall keeps about 4 minutes of headroom under the unchanged 10-minute hang tripwire.
The single longest script, `tests/fm-captain-hold-lifecycle.test.sh` at 306677 ms, is the floor for any shard count.

## Portable serial remainder

`portable-serial` includes every `tests/*.test.sh` that is neither proven-isolated nor `real-herdr-gated`.
It keeps watcher, lock, AFK, real tmux, daemon, secondmate lifecycle, bootstrap, the `live-harness-optin` family, GUI-backend, and other unproven work serial.
Membership is derived rather than enumerated, so a newly added test lands here by default.

## Portable serial CI shards

On green CI run [30725985757](https://github.com/kunchenguid/firstmate/actions/runs/30725985757), that remainder accumulated 19m04s of script time against a 20-minute job timeout.
On [PR 1495](https://github.com/kunchenguid/firstmate/pull/1495), its main step ran about 19m51s before the job was cancelled at that boundary.
`portable-serial-<k>of<n>` splits it across `n` separate CI runners.
Each shard is still strictly serial in itself, and separate runners mean no two of these stateful scripts ever share a machine, so the split needs no concurrency isolation proof.

`bin/fm-test-run.sh` owns `n` and refuses any lane whose `of<n>` disagrees with it.
`.github/workflows/ci.yml` derives the same `n` from `strategy.job-total` rather than a literal, so changing the shard count in either file without the other fails the lane loudly instead of leaving part of the required suite unrun.

Assignment is longest-processing-time bin packing over per-script duration hints embedded in `bin/fm-test-run.sh`.
The embedded hints are the slowest `duration_ms` per script from the `fm-test-timing-portable-serial-*` artifacts of eight green CI runs on 2026-09-22: [35661755328](https://github.com/prajwal-395/firstmate/actions/runs/35661755328), [35663110890](https://github.com/prajwal-395/firstmate/actions/runs/35663110890), [35664891424](https://github.com/prajwal-395/firstmate/actions/runs/35664891424), [35665191329](https://github.com/prajwal-395/firstmate/actions/runs/35665191329), [35677383578](https://github.com/prajwal-395/firstmate/actions/runs/35677383578), [35681402327](https://github.com/prajwal-395/firstmate/actions/runs/35681402327), [35682567950](https://github.com/prajwal-395/firstmate/actions/runs/35682567950), and [35684490810](https://github.com/prajwal-395/firstmate/actions/runs/35684490810), plus the shard-4 artifacts of [35683773638](https://github.com/prajwal-395/firstmate/actions/runs/35683773638) and [35683724411](https://github.com/prajwal-395/firstmate/actions/runs/35683724411).
Using both the pre-rebalance window (where several restart/reply/relaunch scripts measured 38-64s) and the post-rebalance window (where the same scripts measured 110-158s) keeps the balance honest on a slow runner: the four-run refresh earlier tonight packed all five shards to ~21 minutes on paper while shard 4 carried ~25 minutes of real work, because those five scripts were hinted at their fast-window values.
195 of the 196 serial scripts carry measured hints, including the 28 that previously balanced on the default weight; the 5121 ms native-Windows focused runner measurement for `tests/fm-pi-windows-shell-invocation.test.sh` from 2026-09-06T21:02Z is retained because the portable CI shards gate-skip that script, and the `tests/fm-remote-secondmate-launch-verify.test.sh` hint is retained because that script records no timing artifact on portable runners.
`tests/fm-mate-route.test.sh`, added after the measurement window, carries the slowest completed duration (727 ms) from the two green runs that have timed it so far - [35689741789](https://github.com/prajwal-395/firstmate/actions/runs/35689741789) at 727 ms and [35695848809](https://github.com/prajwal-395/firstmate/actions/runs/35695848809) at 462 ms - and folds into the full slowest-observed refresh next time; `tests/fm-secondmate-watcher-quiet.test.sh`, also added after the window, still balances on the conservative default weight until several green runs supply a slowest-observed hint; the coverage guard tracks it through `serial_unhinted=`.
Taking the slowest of several CI runs rather than a single run keeps the balance honest on a slow runner.
A script with no hint gets the conservative `PORTABLE_SERIAL_DEFAULT_WEIGHT_MS` default.
Hints only affect balance: the coverage guard keeps the partition complete and disjoint whatever they say, so a stale hint costs a slower shard rather than lost coverage.
Balance is still worth keeping current, because enough unmeasured scripts let one shard carry more than twice another shard's real work and reach the job cap while another runner sits idle.
`bin/fm-test-run.sh --check-coverage` reports the unmeasured share as `serial_unhinted=` and refuses past `PORTABLE_SERIAL_MAX_UNHINTED_PERCENT`.
That catches missing hints, not stale existing hints: the host suite still had a 41512 ms hint after growing to over 1000 seconds in CI, so the old split placed it beside another 12 minutes of work while passing the guard.
Refresh the hints whenever a serial member grows materially or the lane gains scripts, rather than waiting for missing-hint coverage to trip.

`bin/fm-test-run.sh` owns the per-shard packing, so its `--check-coverage` output is the current account of lane size, shard composition, and balance rather than a copied table.
Five shards stopped fitting in September 2026: the suite totals 113.4 minutes of slowest-sum script time, so even a perfect five-way split packs 22.7 minutes per lane, and the worst observed shard measured 25.9 minutes of job wall against the 30-minute job cap - run [35668599410](https://github.com/prajwal-395/firstmate/actions/runs/35668599410) cancelled serial shard 1 at 30.3 minutes and re-ran it green at 18.8, an 11.5-minute load-driven swing on one shard.
Six shards pack to 18.9 minutes of slowest-sum script time each, so the ~19-minute job wall keeps about 11 minutes of headroom under the unchanged 30-minute hang tripwire - the same treatment the parallel lane got earlier tonight when it split from two shards into three.

The single longest script, `tests/fm-watch-triage.test.sh` at 699926 ms, is the floor for any shard count.

Refresh the CI-derived hints by downloading the per-shard timing artifacts from several green CI runs and replacing the `portable_serial_weight_hints` table in `bin/fm-test-run.sh` with the slowest measured `duration_ms` per `path`:

```sh
for run in <run-id> <run-id> <run-id>; do
  gh-axi run download "$run" -R kunchenguid/firstmate --dir "/tmp/fm-serial/$run"
done
jq -r '.scripts[] | [.path, .duration_ms] | @tsv' /tmp/fm-serial/*/*.json \
  | awk -F'\t' '$2 > m[$1] { m[$1] = $2 } END { for (p in m) print p, m[$1] }' \
  | LC_ALL=C sort
bin/fm-test-run.sh --check-coverage
```

The hint tables and the family table in that runner stay one row per test in `LC_ALL=C` sort order (the pipeline above already emits that order); the coverage guard refuses an unsorted, duplicated, or stale table, so a new row goes at its sort position and parallel lanes adding different tests stop colliding.

A timed-out shard uploads no artifact, so pick runs where every serial shard is green or the lane's slowest scripts go unmeasured in exactly the shard that needs them most.
Measure native-Windows-only scripts through the focused Git Bash runner and retain that `duration_ms` separately, because the portable CI shards skip them.

## Coverage guard

`bin/fm-test-run.sh --check-coverage` verifies that both parallel lanes partition the proven-isolated set.
It also verifies that the parallel lanes, portable serial lane, and real-Herdr family are disjoint and cover every `tests/*.test.sh` script.
It separately verifies that the portable serial CI shards are non-empty, disjoint, and together equal the portable serial lane.
Its hint-coverage and modeled-budget checks are described in [Portable serial CI shards](#portable-serial-ci-shards); neither replaces inspection of actual CI timing artifacts.

## Timing artifacts

Portable shards, each portable serial shard, and the Herdr lane upload runner-generated timing JSON.
`bin/fm-test-run.sh --aggregate-json` creates the combined summary artifact.
`.github/workflows/ci.yml` owns the exact artifact names and aggregation wiring.

## Lint partitions and end-to-end latency

`bin/fm-lint.sh` owns two canonical CI partitions, each attempting full source-aware ShellCheck analysis and running workflow validation and backend-purity checks.
CI requires its per-root bounds, so an unenforceable deadline or address-space limit refuses lint rather than running uncapped; the script header owns the envelope, per-root execution contract, and memory fallback.
Its `--list-files` interface exposes partition membership; `tests/fm-lint.test.sh` verifies complete/disjoint executed roots, initial analysis flags, and fallback reporting.
The workflow uploads each partition's quiet telemetry plus its per-root lifecycle sidecar to distinguish analysis cost, memory use, and host contention.
No fast mode, path skips, or paid runner provisioning is part of this layout.

The longer-term performance objective remains a complete green run under fifteen minutes including start delay, but the current watch-triage floor alone exceeds that objective.
The immediate packing target is the runner's modeled script budget, not a claim that more shards alone can make an indivisible script faster.
The layout uses fourteen long-lived Linux jobs (nine serial, two parallel, Herdr, two lint), plus short checks and macOS; insufficient shared account capacity can erase the packing gain.
Compare complete before/after runs, preserve cancelled and partial-run evidence, and measure a representative normal-run sample before claiming a P95 improvement.
The workflow retains per-PR supersession without cancelling main pushes or changing the compliance workflow's event semantics.

## Local entry points

[CONTRIBUTING.md](../CONTRIBUTING.md) owns the local test policy and common entry points.
`bin/fm-test-run.sh --help` owns exact lane names, selection flags, and bounded `--jobs` mechanics.

## Timeouts

| Lane | Bound | Rationale |
|---|---|---|
| portable parallel 1/2/3 | See [CI workflow](../.github/workflows/ci.yml) | The workflow owns the parallel cap rationale and its evidence limits. |
| portable serial 1-6 | job `timeout-minutes: 30` | Current runners take about 19 minutes of slowest-sum script time for a balanced shard; the 30-minute cap remains a hang tripwire while leaving margin for job setup and runner-speed spread. |
| Herdr | family-run step `timeout-minutes: 20`; job `timeout-minutes: 75` backstop | Healthy runs finished around 7 minutes before this lane gained `fm-backend-herdr-focus-flash-e2e`, which measures about 2 minutes against a real lab locally, so the step bound is still the hang tripwire (cleanup and timing artifacts still upload) while the job cap stays a last-resort backstop. Refresh this figure from the lane's uploaded timing artifact. |

| Tier | Jobs | Bound | Rationale |
|---|---|---|---|
| Fast | coverage guard, repo invariants, timing aggregate | 5 minutes | Seconds-long local work, so the tripwire only catches a hung runner. |
| Normal | lint partitions, portable parallel shards, portable serial shards, macOS stock Bash | 30 minutes, one value shared by every job in the tier | One shared hang tripwire keeps every ordinary test and lint lane on the same policy instead of allowing per-lane packing estimates or one-off caps to set the bound. |
| Heavy | Herdr | family-run step 20 minutes under a 75-minute job-level last-resort backstop | Healthy runs finish in about 7-10 minutes, so the step tripwire fails a wedged suite while the `always()` cleanup and timing upload still run, and the job cap only catches a hang outside that step. |

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) holds the executable values and names each job's tier beside its `timeout-minutes`.
[`tests/fm-ci-workflow.test.sh`](../tests/fm-ci-workflow.test.sh) holds the policy against the parsed workflow: every job belongs to exactly one tier, the workflow carries exactly three distinct job-level values, the fast tier stays within 5-10 minutes, the normal jobs share one 30-minute budget, and the Herdr family-run step is the 20-minute tripwire below its job backstop with an `always()` teardown after it.
A passing coverage guard does not establish a healthy job duration; refresh the healthy figures above from the lanes' uploaded timing artifacts.
