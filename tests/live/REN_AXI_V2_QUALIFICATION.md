# Ren AXI v2.0 live qualification

All live evidence in this suite is **built, run deferred** under the
captain's 2026-10-19 no-local-testing rule. Do not treat a built adapter,
fixture, or contract as a qualification pass. Results must be recorded from
the live Resolve run.

## Exact small-edit command

From the repository root, after creating the private `fm-scratch qualify`
timeline in the `Ren Qualification` project and importing `qual_a.mp4`,
`qual_b.mp4`, `qual_c.mp4`, and the generated logo/caption PNG fixtures:

```sh
bin/vep tests/live/small_edit_qualification.py --write-fixtures
```

Import the four printed PNG paths into Resolve's media pool, then run:

```sh
REN_LIVE_RESOLVE=1 bin/vep tests/live/small_edit_qualification.py
```

The command uses `resolve.qualification_session`, the broker lease, and the
production AXI / EditPatch / rebuild / replacement-guard entry points. It must
only run on the private qualification timeline. The runner refuses a nonempty
baseline, captures live timeline and rendered-frame observations, and cleans
only fixture items it placed. The live operator must retain the JSON report as
the qualification record; no report is generated during this implementation
task. The forced mutation refusal, failed verification payload, and promotion
conflict rows pass only when the production refusal is recorded and the live
timeline snapshot remains unchanged. The rebuild row records the real build
result and before/after timeline snapshot; a failed or unavailable build is a
failed qualification row.

## Scope record

| Plan item | State |
| --- | --- |
| 9.1 14-scenario live suite | Built, run deferred. All 14 IDs have production-entry adapters, including exact timeline/geometry snapshots, rendered pixel deltas, a rebuild observation, and fail-closed mutation/verification/promotion cases. |
| 9.2 Ten ordinary editorial requests | Built, run deferred. The instruction harness requires discovery, route choice, verified receipt, collateral diff, and one-attempt evidence. |
| 9.3 Generalization matrix | Built, run deferred. Pairwise dimensions and invariants are in `fixtures/generalization_matrix.json`. |
| 9.4 New-capability discovery probe | Built, run deferred. `run_discovery_probe` rejects operations already present in the supplied skill and requires catalog discovery and production execution evidence. |
| 9.5 Reliability metrics | Built, run deferred. The recorder reports every requested rate and measured end-to-end latency, preserving missing evidence. |

Rendered geometry is compared to output-pixel expectations and the independent
pixel mask is retained in each result row. Missing measurements, an unavailable
rebuild, or an incomplete conflict/failure proof remains a failed scenario;
none is inferred from command success.
