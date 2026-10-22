# The cost slot nobody filled

AXI-016, 2026-10-21.  The evidence behind the cost-aware execution
selection: `docs/EXECUTION_COST_SELECTION.md` carries the change and the
measurement; this file carries the incident.

The punchlist asked for cost-aware execution selection, and the engine
already had a slot for the answer: the routing record's
`selection.cost`, filled by PERF-017 from the project's own ledger.  It
read `"unmeasured"` on every decision, and nothing in the engine ever
wrote a number into it.

The reason is not that the cost was unknowable.  It was measured three
times over, on three scales that nobody could put side by side: the
direct route's own phase (`touchup_direct`), the staged route's three
phases (`touchup_duplicate`, `touchup_conform_compose_verify`,
`touchup_promote`) and the build's own `reel_edit` row.  A request that
ran the staged route cost the sum of three rows; a request that ran the
direct route cost one; a rebuild cost a `total_s` on a summary row that
no request ever wrote.  Three mechanisms, three units, and no reader
could say what a direct patch cost next to what a staged touch cost.

## What the machine had actually measured

Read back from the Ren-owned scratch copies' own ledgers
(`scripts/measure_execution_cost.py`):

- `ren-scratch-bench-demo2`: staged incremental 2 requests, 97.034 s and
  199.56 s; direct patch 2 attempts, 0 verified, 2 DECLINED, 24.333 s
  and 25.123 s each;
- `ren-scratch-breaktest`: staged incremental 2 requests, 108.003 s and
  1619.827 s, plus 3 interrupted requests counted apart; direct patch 2
  attempts, 0 verified, 2 declined, 28.651 s and 34.753 s each;
- full rebuild: unmeasured in both - no build ran under those ledgers.

The declinations are the finding, and they are the punchlist's third
bullet measured rather than asserted.  The direct route is SMALLER, and
it is not available on a real built reel: the router proves the direct
mode against the MEASURED runtime, and a real reel's live state
(`existing_effects` is `transforms+retiming+fusion`) does not satisfy
the recorded qualification predicates for `clip.set_property`
(`['none', 'transforms', 'transforms+retiming']`).  All four recorded
attempts declined for that one named reason.  So the smaller mutation
is not preferred here, and it is not a preference: it is not VERIFIED
here.  The 24-35 s each attempt cost is the cost of the PROOF that
refused - a price the engine was paying with nothing to show for it,
and nothing on the record to say so.

## The rule this produced

A strategy's cost is measured in ONE place, on the project's own
ledger, one row per execution request: the strategy that ran, the wall
clock it took, and the verification outcome it reached.  The selection
is made from the MEASURED cost of the strategies some other evidence
has already proven SAFE, and a strategy nobody measured is never
reported as a cheap one.  A smaller mutation is preferred only where it
is verified and safe, and when it is not, the record says so BY NAME -
the decline and its reason ride the comparison.

The engine keeps ONE place where a route is chosen
(`library/tools/execution_router.py`, named as the selector in
`library/tools/backend_selection.py`): the touch path ASKS it, through
`execution_router.select_route`, and does not decide for itself.

## What the measurement did NOT settle

The live before/after on a fixed request set did not complete: eight
requests on the Ren-owned scratch copy, four refused at the lease
(`REN_RESOLVE_BUSY`, the machine's queue 19 deep with a 53-minute oldest
wait) and four refused at the project open (`could not save 'Ren
Qualification'`), none of them changing anything.  The full rebuild's
cost is unmeasured in the projects this lane could read, and the direct
patch's cost on a real reel is unmeasured because it was refused every
time.  Unmeasured is never zero.  `docs/EXECUTION_COST_SELECTION.md`
carries the tables and the denominators.
