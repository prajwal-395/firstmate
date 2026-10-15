# Test suite profile (I05), 2026-10-15

## Run

Measured on the task worktree after the resource scheduler changes. The
full `unit or scenario` selection ran once under
`heavy_work_lock --profile full_suite_gate`, with two xdist workers,
`--dist loadfile`, JUnit XML and `--durations=1000`:

```text
bin/vep -m library.tools.heavy_work_lock run \
  --owner vep-pl-i05-suite-profile --profile full_suite_gate -- \
  bin/vep -m pytest tests -p scripts.pytest_timing \
  -m 'unit or scenario' -n 2 --dist loadfile \
  --junitxml=/tmp/vep-pl-i05-baseline.xml --durations=1000 -rs
```

The profile is the narrowest purpose-specific test profile in
`resource_scheduler.profiles()`. Its current demand is `{}`, the same as
`machine`; it identifies the job but does not reserve CPU or disk. The
wrapper reported a 0.033 second queue wait.

| Measurement | Baseline |
|---|---:|
| Wall time (`/usr/bin/time -lp`) | 1,510.61 s (25:10.61) |
| Pytest elapsed | 1,509.01 s (25:09.01) |
| Collected items | 9,663 |
| Result | 9,622 passed, 2 failed, 39 skipped, 24 warnings |
| JUnit test-case time, all phases | 2,730.184 s (45:30.18) |
| Test-case time minus wall time | +1,219.574 s (+20:19.57) |
| Process CPU (`user + sys`) | 1,396.24 s, about 0.92 cores averaged over wall time |
| xdist workers | 2 |

The run is a timing baseline, not a passing gate. This direct pytest
invocation did not apply the gate's file routing: a `@pytest.mark.serial`
case ran under xdist. One failure was in that serial-marked Resolve lease
test. The other was a `test_gate_smoke` subprocess whose 60-second limit
expired while other test processes were active.

## Where the time went

JUnit records setup, call and teardown durations. Their sum exceeds wall
time because xdist runs test phases concurrently, and several tests run
child pytest/gate processes while other tests continue. The summed test
time is therefore not CPU time and cannot be subtracted from wall time as
if tests ran one after another.

The largest row was
`test_current_tree_routes_each_file_once_and_declares_its_serial_files`
at 816.737 seconds. Its isolated rerun took 3.53 seconds in the call
phase (4.11 seconds process wall). During the full run, one xdist worker
was observed in a stopped (`T`) process state near 99% progress while the
other worker was idle. Resuming that exact worker let the remaining suite
finish in about 11 seconds. The 13.6-minute full-suite duration is worker
delay, not the routing algorithm. The isolated comparison supports that
reading.

The cause is the machine's CPU watchdog, which pauses a top CPU process in
the venv after sustained low system idle. The benchmark ran from about
10:40 to 11:05 local time. In that window, the watchdog log records two
xdist workers paused: pid 95200 at 10:49:57 and pid 15864 at 10:52:15,
both with the xdist worker command. The observed late-run stopped worker
was resumed manually; the watchdog log has no `RESUMED` entry for pid
15864. It later records pid 95200 resumed at 11:10:00 (idle 66%), after
this benchmark had completed. Thus the log corroborates the watchdog as
the source of the stop, but does not record the manual resume of the worker
that held up this run. The watchdog was left unchanged.

The `--durations=1000` report included 889 call, 55 setup and 56 teardown
rows. Within those 1,000 rows, setup summed to 64.89 seconds and teardown
to 18.35 seconds. This is a lower bound for setup/teardown across the
whole suite because short rows are outside that report. A separate
`--collect-only` measurement collected the same 9,663 items, deselected
69, and took 8.16 seconds in pytest (9.38 seconds process wall).

Other long rows were child-process heavy scenarios: the golden monologue
took 103.79 seconds; the two golden conversation variants took 88.94 and
62.80 seconds. Gate smoke subprocess rows took 60.03, 46.97, 38.89 and
36.40 seconds. These durations include the child work and time waiting
for its result; this run did not separately instrument `Popen` startup,
so startup latency cannot be distinguished from each child command's
work. A separate 40-child warm Python startup sample measured 11.1 ms
median per `python -c pass` process (11.3 ms mean, 9.8-14.9 ms range).
This is a startup reference, not an attribution of the nested gate's
29.25-second isolated runtime.

The `built` fixture in `tests/scenarios/test_reel_preview.py` took
14.49-16.21 seconds in setup across its three tests. It creates the
golden conversation, analyzes and plans it, then builds the reel on the
Resolve double. This was the largest repeated fixture setup visible in
the duration report. The two baseline failures were rerun individually:
the gate smoke case passed in 29.25 seconds and the serial Resolve lease
case passed in 3.29 seconds.

At the same time, the machine had another six-worker full-suite process
and several targeted pytest processes. These jobs were admitted alongside
this run: the observed test profile has no CPU demand, and the scheduler
only arbitrates declared exclusive resources. This is direct evidence of
shared CPU contention. Worker snapshots also showed idle capacity at the
tail rather than both workers continuously executing.

## Isolated comparison and action

| Test | Full suite call | Isolated call | Isolated process wall |
|---|---:|---:|---:|
| `test_current_tree_routes_each_file_once_and_declares_its_serial_files` | 816.737 s | 3.53 s | 4.11 s |

The biggest apparent per-test cost disappears in isolation, so changing
the routing implementation would target the wrong cause. The worker stop
was external to the test code and its cause was not identified. No
performance source change was safe from this measurement; a full-suite
after-run was not performed. Parallel test clocks overlapping explains
the 20:19.57 excess in summed test time over wall time; nested subprocess
tests contribute their full elapsed time to that sum. The stopped worker
accounts for one unusually long row.
