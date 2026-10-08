# Own-code cost ledger, first measurement pass

Date: 2026-10-08
Branch base: see PR head
Scope: ledger attribution and count primitives; no pipeline output changed.

## What this pass measures

`ren profile <project> [--run <run-id>]` now reports capability wall time as
Ren plus currently unclassified time, and time covered by explicitly named
external-engine spans. It reports the split per capability and across the
run. The allowlist in `library/tools/perf_ledger.py` includes model inference,
MFA, ffmpeg encoding, graphics rendering, Resolve API work and broker
requests. Where external spans overlap, their category totals can overlap;
the external share itself is a union and cannot exceed capability wall time.

Count rows now support broker requests and subprocess calls. Count families
without an instrumented callsite are `null` in the report, which means
unmeasured; zero is reserved for a measured family with no calls.

## Workload result

No representative end-to-end workload was run for this pass. This checkout
contains no fixed analysis-media set suitable for the prescribed workload,
and no disposable reel project with render media was provided. The fixture
JSON files are schema fixtures, not runnable media projects. Resolve work
would require the qualification project path and its borrowed Resolve lease;
no live Resolve operation was attempted.

Therefore this pass has no defensible before/after own-code share, flamegraph,
or hotspot fix. The first workload run must establish that baseline before
the next optimization round. The ledger currently labels the complement of
known external spans as `own_code`; treat it as an upper bound until the
remaining external callsites and waits are instrumented. Probe/hash reuse
call sites are intentionally untouched because that work is assigned to the
separate probe reuse lane.

## Validation

- Dependent selection: `scripts/select_dependent_tests.py` selected 91 test
  files after including the broker client, CLI command registry, and
  code-identity dependency map.
- Selected tests: 984 passed and 4 failed on the first run. Two failures
  exposed a pre-existing missing `assignment_artifacts.py` dependency in the
  catalog code-identity map and a profile allocation regression introduced
  here. Both were corrected. The relevant ledger checks then passed: 4
  passed.
- The checkout's pytest does not have `pytest-xdist`; verification ran
  serially under the prescribed `vep-verify-run.sh` lock.
- Final dependent selection: 1,848 passed, 6 skipped, 0 failed in 467.23 s.
  The skips were five live Resolve qualification cases without
  `REN_LIVE_RESOLVE=1` and the Rust parity test because this worktree has no
  `ren_native` extension built. Fourteen Pillow/NumPy deprecation/runtime
  warnings were emitted by existing computer-use tests.
