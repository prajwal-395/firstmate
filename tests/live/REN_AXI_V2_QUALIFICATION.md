# Ren AXI v2.0 live qualification

All live evidence in this suite is **built, run deferred** under the
captain's 2026-10-19 no-local-testing rule. Do not treat a built adapter,
fixture, or contract as a qualification pass. Results must be recorded from
the live Resolve run.

## Exact small-edit command

From the repository root, after creating the private `fm-scratch qualify`
timeline in the `Ren Qualification` project and importing the synthetic
`qual_a.mp4`, `qual_b.mp4`, and 180x64 transparent PNG fixture:

```sh
REN_LIVE_RESOLVE=1 bin/vep tests/live/small_edit_qualification.py
```

The command uses `resolve.qualification_session`, the broker lease, and the
`resolve-axi edit` production entry point. It must only run on the private
qualification timeline. The runner refuses a nonempty baseline and cleans
only items it placed. The live operator must retain the JSON report as the
qualification record; no report is generated during this implementation
task.

## Scope record

| Plan item | State |
| --- | --- |
| 9.1 14-scenario live suite | Built, run deferred. Existing cases cover placement, move, trim, transform, rebuild, failure recovery, and promotion conflict; adapter gaps remain visible as failures. |
| 9.2 Ten ordinary editorial requests | Built, run deferred. The instruction harness requires discovery, route choice, verified receipt, collateral diff, and one-attempt evidence. |
| 9.3 Generalization matrix | Built, run deferred. Pairwise dimensions and invariants are in `fixtures/generalization_matrix.json`. |
| 9.4 New-capability discovery probe | Built, run deferred. `run_discovery_probe` rejects operations already present in the supplied skill and requires catalog discovery and production execution evidence. |
| 9.5 Reliability metrics | Built, run deferred. The recorder reports every requested rate and measured end-to-end latency, preserving missing evidence. |

The production-backed adapter needs to capture live output geometry,
timeline bounds, and post-rebuild state before the logo proof can pass. Missing
measurements are represented as failed checks by the harness, never inferred
from command success.
