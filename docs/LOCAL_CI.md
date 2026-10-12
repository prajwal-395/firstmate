# Local CI and merge queue (ren-ci): the contributor SOP

Just push and mark ready. The Mac does the rest, for free.

## What runs, and when

Two tiers. Tier-1 is the fast signal; Tier-2 is the merge verdict.

- **Tier-1, per PR, immediate and parallel.** Every new head (push)
   runs the static gate (enforcing ruff config, compile sweep, AGENTS.md
   size gate) plus its dependent selection for the changed files plus
   the mandatory set: the tiny universal half (parallel lane routing,
   composer, ren-core parity, resolve guard wiring) always, and the
   global `tests/contracts/` suite when the change signals structural
   reach (shared prefixes, registries, or wide subsystem fan-out -
   see the selection budget below). As
   many PRs at once as the machine can take right now - a quiet machine
   runs three jobs, a loaded one serialises to one. Tier-1 green is the
   GATE into the queue, not a merge verdict.
- **Tier-2, integration in a timebox.** Ready PRs cluster within an
  adaptive window (short on a quiet machine, long on a busy one) and
  the FULL suite (`scripts/full_suite_gate.sh`, under the required
  `not real_model and not resolve_live` marker policy) runs once on the exact
  stacked tree that would land (main + cluster in queue order). Green
  merges the whole cluster in order with a freshness re-check chained
   on each merge's landed SHA; red bisects to the culprit, kicks only
   it with the reason, and merges the rest. A narrowed/crashed gate is
   not red: the cluster requeues and re-tests. A ripe cluster takes
   every queued entry (window-only ripeness, never a size cap); before
   the verify, the pool step prints cost-aware grouping advice
   (`library/tools/local_ci/grouping.py`: verification + diagnosis +
   merge + waiting, with subsystem/selection overlap raising the
   split-risk term). The advice never splits the verify - it groups
   with eyes open until measured data hardens it into logic.

No PR merges without a full-suite pass on the exact tree that lands.
That is the test-security half of the design; the speed half is that
Tier-1 selections are small and parallel while the full suite runs
once per cluster instead of once per PR.

Before Tier-2 starts the full gate, it builds `ml-worker-client` and
`ren-graphics-fframes` from that stacked checkout. Their Cargo cache is
shared under `~/.ren-ci/native-target`; each disposable checkout binds
that cache through its ignored `native/target` path, so Rust tests are
measured without retaining checkout artifacts between runs.

## The contributor loop

1. Push the PR against `main`.
2. Watch the `ren-ci/pr-checks` commit status on the head (pending ->
   success/failure, linked to the PR). `ren ci status` prints the
   selection, check latency, and local log path.
3. When Tier-1 is green, mark ready: add the `ren-ci-ready` label, or
   `ren ci ready <pr>`.
4. The cluster runs Tier-2 when its window closes; merges land in
   queue order. A kick names its reason on the head's status and drops
   the ready label - fix, push (a new head needs a fresh Tier-1), mark
   ready again.

`ren ci status` shows the queue, active Tier-1 jobs with their slot and
elapsed time, the last Tier-1 results with seen->tier1 latency,
ready->merged latency for landed PRs, and what the machine would
dispatch right now.

## Native engine upstream intake

The same daemon polls the FilmCraft and EffectCraft upstream URLs once per
UTC day and records changed heads and release tags in
`~/.ren-ci/upstream-intake.json`. Weekly qualification runs only after
Tier-1, Tier-2, and manual PR verification work is idle. It takes the
machine-wide heavy-work admission and all Tier-1 slots before building, so
engine builds and tests cannot overlap CI verification or another heavy
operation. Qualification records and logs stay under `~/.ren-ci/`; a daemon
restart retries an interrupted weekly window. Failed gates remain visible and
retry after six hours.

Immutable starting revisions and upstream URLs are declared in
`library/tools/upstream_engines.json`, mirroring the pin constants in the
Ren driver crates (`native/ren-filmcraft`, `native/ren-effectcraft`);
qualification refuses when the manifest drifts from those crates. Ren-only
patches, if ever needed, live as ordered `.patch` files under
`library/tools/upstream_patches/<engine>/`; each is checked and replayed
before the engine build. Candidate pins are not promoted unless the engine
build, the upstream crate tests, and every case of the committed headless
render corpus (`library/tools/upstream_corpus/`, SOP:
`docs/UPSTREAM_ENGINE_INTAKE.md`) pass; a failure leaves the prior qualified
revision in place. Only engines with pending candidates are built.

## Load policy (why it speeds up and slows down)

Before each dispatch the daemon measures busy CPU cores (summed %CPU -
idle agent lanes cost nothing), free memory, current paging activity
(the recent pageout/swapout rate from vm_stat deltas), free disk, and
heavy foreground processes (Resolve, renders, a batch gate). Quiet: up
to 3 Tier-1 jobs at pytest `-n 1` with a 90s window (small clusters,
low latency). Loaded: 1 job at `-n 2` with a 600s window (bigger
clusters, fewer full-gate runs). Paging pressure caps fan-out then
serialises; swap USED never gates (it stays high long after pressure
clears - ~12 GB used beside ~6 GB free on 2026-10-10 - so it is
observability only). The 1-minute load average survives only as a fallback
when the CPU sample is unavailable. Tier-1 keeps running while a
ready cluster is ripe or testing, so every ready PR gets checked and
enters the queue: a ripe batch waits for running Tier-1 jobs to drain
(a time-boxed round), never for new heads to stop arriving. Heavy work
still never overlaps - a Tier-1 pytest refuses while a batch
full-suite gate holds the machine lock, and Tier-2 fires through the
idle gate once the drain completes. A batch
full-suite gate in flight defers everything; a run that would breach
the 10GB disk floor refuses and says so. The policy is pure and pinned
by `tests/unit/tools/test_local_ci_load.py` - tune the matrix, not the
daemon.

## Tier-1 selection budget (why hub PRs stay quick)

Tier-1 runs the static gate plus the tests that directly exercise the
changed files, bounded by 300 s of estimated dependent test time
(filled from historical per-test durations, direct tests first, then
by import distance, then by failure rate): above the budget the run
keeps the highest-priority tests and leaves breadth to the Tier-2
batched full suite, which already runs everything on the exact
stacked tree before any merge. A capped run records the cap on its
Tier-1 status (`[capped, breadth to Tier-2]`), so it never reads as
full coverage.

The mandatory set beside the dependent selection is two tiers. A tiny
universal half - the compiles/parity/wiring files that cost seconds
(parallel lane routing, composer, ren-core parity, resolve guard
wiring) - runs on every check, outside the budget. The global
`tests/contracts/` suite runs only when the change signals structural
reach: it touches `library/tools/`, `native/ren-protocol/`,
`tests/layers.py`, the contract suite itself, a capability/execution
registry, or the selector maps the change to 4 or more `unit/`
subsystems (the fan-out floor, measured across the `library/tools/`
tree). The status/report line names which mandatory half ran and why,
so a contracts-skipped run never reads as a contracts-passed run.

## Replay benchmark (why incremental evidence cannot replace the gate yet)

`ren ci replay` replays the corpus in
`library/tools/local_ci/replay_corpus.json` under both the full-gate
policy and the simulated incremental policy (hybrid selection +
shared cache + certified baselines) and reports missed regressions
per policy (`library/tools/local_ci/replay.py`). The verdict is
"incremental NOT YET TRUSTWORTHY" until shadow validation (CI-10)
records data; the CI-08 promotion bar the report states is the
captain's C4 call. The corpus keeps scripted harness fixtures and
records historical red outcomes using the live hybrid selector; each
historical case names its changed PR files, catching test and source
PR record. These cases measure selection misses and do not execute the
recorded tests or gate.

## Hygiene (the four scratch-script failures, retired)

Disposable worktree checkouts, deleted on exit even when the run fails
or is killed. No leaked Resolve brokers (a NEW broker after a run
fails the check; the captain's serving broker is left alone). Nothing
runs `real_model` or `resolve_live` markers, and the three
Resolve-driving qualification files are ignored by name. State lives
in `~/.ren-ci` (machine, never a repo); logs in
`~/Library/Logs/ren-ci/`.

Tier-1 pytest has a 30-minute hard timeout and runs in its own process
group so the timeout also stops xdist workers. A stuck test gets
faulthandler traces in the log every five minutes; a timeout is no
verdict, not a failure - the head posts pending with the reason and
re-queues automatically (at most twice, with 60s/120s backoff) before
it can stay red. GitHub status delivery failures keep the cached result and
retry on the next pass without running the tests again.

Two races are designed out, not retried blindly: the base is fetched
by SHA after the `main_sha` read (main moves under the daemon while
the batch loop merges - a missing base defers to the next pass), and
only files present in the checkout compile or test (a stale-base diff
names files the PR's tree never had). A crash posts `pending`, never
`error`: a red X must mean the PR was judged, not that the daemon
tripped.

## Install (only after the delivering PR merges)

The launchd plist points at the checkout it was installed from, so
install from `main`, never from a worktree or branch:

```
ren ci install   # user agent com.ren.ci: KeepAlive, logs to ~/Library/Logs/ren-ci/
ren ci uninstall # stops and removes the agent; state and logs survive
```

The plist carries its own PATH (leading with a Python 3.10+): `bin/vep`
probes the engine with a bare `python3`, and launchd's default PATH
finds only macOS's 3.9, which cannot parse the engine. Discovered on
the first install; pinned by
`tests/unit/tools/test_local_ci_service.py`.

## Self-update (the daemon reloads its own code)

The plist points at a checkout that moves under the daemon: before
every dispatch the daemon compares the commit it started on with the
served checkout's HEAD, and when the range touches its own code
(`library/tools/local_ci/`, `bin/vep`, the heavy-work lock, the
resource scheduler, the Resolve client, `ren/engine_root.py`) it
re-execs onto the new tree - but only while idle (no Tier-1 worker in
flight, no slot owner, no full-suite gate, no Tier-2 member under
test). A busy daemon defers to the next pass. Poll and intake windows
live in `~/.ren-ci` (never the repo), so no window is skipped or
doubled across the reload; an interrupted weekly run retries at
startup. Pinned by
`tests/unit/tools/test_local_ci_self_update.py`.

## Shadow validation (the full suite, after the verdict)

Once incremental evidence starts judging trees, a second full-suite
run checks its homework: `ren ci shadow <sha> --claim <file>`
re-runs `scripts/full_suite_gate.sh` on that tree when the machine is
idle (no Tier-1 job, no gate in flight, no Tier-2 member under test -
a busy machine refuses with the reason) and diffs the outcomes
against the claim's green (`{verdict, executed, reused}` JSON, until
the incremental verdict produces it). A red gate files a defect in
`~/.ren-ci/shadow_findings.json` naming the missed edge(s) - red
files the evidence never executed; a matching green files nothing.
Shadows never block merges and never change verdicts: they write only
their findings ledger (plus the gate log), never the queue, the
checks, the baselines, or the shared cache. `ren ci status` shows the
newest findings. Pinned by
`tests/unit/tools/test_local_ci_shadow.py`.

## End-to-end proof (post-merge, on throwaway PRs)

1. Open a trivial PR, watch Tier-1 post pending->success, `ren ci
   ready`, watch the cluster run Tier-2 and merge. Close nothing - a
   merged PR closes itself.
2. Open a deliberately failing PR (one red test), mark ready, watch it
   kicked with the reason while the rest of its cluster merges. Close
   the throwaway branch after.
3. Report each check as: `PR <url> head <full sha> - <N> passed,
   <M> failed, selection <command and paths>`.

## Deliberate tradeoffs

- **No GitHub Actions for automation** (`tests/contracts/
  test_no_hosted_actions.py` fails any automatic workflow or hosted
  runner): zero minutes, enforced. The one exception is tag-triggered
  releases, a deliberate human act a handful of times a year whose
  artifacts `ren update` fetches.
- **No self-hosted Actions runner** (costed and rejected in
  docs/CI_LAYERS.md: competes for cores, queues when the Mac sleeps,
  destroys the clean room). The daemon IS the runner, with its own
  load policy instead of the Actions scheduler.
- **The clean-room gate is retired with `ci.yml`.** A fresh Linux
  install from declared manifests caught undeclared dependencies - and
  cost hosted minutes per run. The replacement is the disposable
  checkout (clean tree, declared interpreter ladder); a
  declared-manifest install check is the documented future upgrade.
- **Why not extend the variant merge queue (PR 1924).** That queue
  merges recorded timeline commits inside one video project, judged by
  `timeline_diff` previews where per-candidate verdicts ARE the split
  (no bisect possible or needed). This queue merges git branches judged
  by test suites, where green-alone-red-together is the core defect and
  bisect is the core mechanic. Different domain, different verdict,
  different clock: only the pattern (one evaluation pass, ordered
  merges, freshness first, kick only the culprit) carried over.
- **Detection polls; scheduling does not wait.** New heads are found
  by a 30s REST poll (zero minutes; no push channel exists to a Mac
  behind NAT). Everything after detection is event-driven: slots,
  windows, and queue state decide, never a timer. A webhook receiver
  is the future upgrade if the poll ever matters.
