# AXI-016 live before/after: cost-aware execution selection on scratch copies

The live run PR 3176 left undone, run 2026-10-22 on Ren-owned scratch
copies through the serving broker. Never the captain's project, never
the captain's media (podcast source media read-only). Ren's lease owns
Resolve contention: no quiet window was asked for.

## Fixed request set

One-property writes on identified items of the same built reel
(`Reel 13 - the-accounting-firm-ai-called-healthcare`) in
`Ren Scratch - vep-bench-demo2`, each a distinct value so no request
is a no-op. Two shapes, alternating:

- **V1[0] Tilt nudges** (A-roll picture, `existing_effects` is
  `transforms+retiming+fusion`): the recorded set's own property.
  The direct route cannot prove here; the staged route is the only
  eligible candidate.
- **V5[0] Pan nudges** (motion-graphics card, `existing_effects` is
  `transforms`): direct-provable. The two routes meet here.

Values were restored afterwards (R5/R6), so the reel reads exactly as
found: V1[0] Tilt -660.041, V5[0] Pan 0.0, V5[0] Opacity 100.0,
V6[1] untouched. Verified off the journals' `preservation_after`
snapshots.

## Before / after, with denominators

"Before" is the old default: every request stages (the touch path
before PR 3176 tried direct and fell through; the cost-aware selection
with one eligible candidate does the same by preservation order).
"After" is the selection from measured costs
(`receipt["execution_cost"]`, `cost_basis: "measured"`).

| req | edit | chosen strategy | wall s | verification | basis |
|---|---|---|---|---|---|
| R1 | V1[0] Tilt -672.041 | staged_incremental (direct declined: fusion predicates) | 140.958 | verified (promotion + read-back) | unmeasured (one eligible) |
| R2 | V5[0] Pan 0.0 -> 2.0 | **direct_patch** | 20.793 | verified (read-back + outside-targets) | **measured** |
| R3 | V5[0] Pan 2.0 -> 4.0 | staged_incremental (`--staged` forced: the old default) | 145.529 | verified | unmeasured (forced) |
| R4 | V5[0] Pan 4.0 -> 6.0 | **direct_patch** | 34.234 | verified | **measured** |
| R5 | V1[0] Tilt restore -660.041 | staged_incremental (direct declined: fusion predicates) | 199.611 | verified | unmeasured (one eligible) |
| R6 | V5[0] Pan restore 0.0 | **direct_patch** | 47.852 | verified | **measured** |

Denominators: 6 requests, 6 landed, 6 verified, 0 failed, 0 refused.
Direct-eligible: 3/3 direct, 20.8 / 34.2 / 47.9 s.
Fusion-carrying: 0/2 direct (correctly declined, reason named on the
receipt), 2/2 staged, 141.0 / 199.6 s.
Forced-staged on a direct-eligible edit: 145.5 s — the old default's
price for the same write the selection runs in ~21-48 s.

Read back through `scripts/measure_execution_cost.py`, this project now
reports (strategy rows plus the phase-row reconstruction the tool has
always read — a run with both contributes both, so "requests" below
overcounts landings; the six rows above are the request-level truth):

- direct_patch: 10 samples (6 verified, 4 declined), p50 24.3 s,
  p95 47.9 s. The 4 declined are the cost of the proof that refused
  (2 recorded 2026-10-21 on fusion predicates, 2 live R1/R5) — not
  writes.
- staged_incremental: 8 samples (3 verified landings, 5 completed
  phase reconstructions), p50 145.5 s, p95 199.6 s.
- full_rebuild: unmeasured (no build under this ledger).

The staged wall clock is lease-wait dominated (stage ~1-14 s, compose
~1-2 s, verify ~1 s, promotion ~55-106 s of the totals above); the
direct wall clock is proof-dominated (router measurement against the
live runtime, ~1-25 s) plus the write itself (~10-25 s). Same machine,
same contention window, alternating requests — the comparison is
like-for-like except for queue depth, which the receipts' own phase
splits show.

## What the selection proved live

- R2/R4/R6 receipts read `cost_basis: "measured"`, reason "the
  cheapest measured strategy that is safe and verifies what this
  request needs", strategy `direct_patch`, status `applied_direct`.
  The router's proof plus the ledger's measured costs picked the
  smaller mutation, and the write verified.
- R1/R5 receipts read `cost_basis: "unmeasured"`, strategy
  `staged_incremental`, with the direct decline and its reason on the
  comparison (`transforms+retiming+fusion` vs the recorded
  `none`/`transforms`/`transforms+retiming` predicates). The smaller
  mutation was not taken because it was not verified here — the
  punchlist's third bullet, measured again.
- Preservation carried on each receipt: direct disturbs only the
  declared targets (no duplicate, no rename, no backup churn);
  staged carries the approved timeline through a verified promotion
  (a `pre-rebuild backup` was retired by R1's promotion, per the
  lifecycle).

## Fidelity

The reel's final values equal its initial values (above). The one
excursion — two off-scale Opacity writes, below — was reverted the
same day through a verified direct write, and the journals record the
whole excursion honestly.

## Incident 1: fraction-scale Opacity verified while erasing the card

Two exploratory touches before the fixed set wrote Opacity 0.99 then
0.98 on V5[0]. Both VERIFIED (read-back reads what was written) while
the card went to ~1% opacity: Resolve stores Opacity 0-100 percent,
and five independent engine readings agree (projectdb reader's
"Opacity (%)", the serializer's 100.0 default, the guard's 100.0
default, the 85.0 probe, the tv_frame 100). The gate refused
unsettable keys but never checked the scale.

Fixed on this branch: `_op_set_properties` refuses Opacity outside
0-100 and refuses (0, 1] as an ambiguous fraction (0 itself is
identical in both scales and stays allowed). Defect-naming tests in
`tests/unit/reels/test_reel_touchup.py`. Both writes reverted live
(V5[0] back to 100.0, verified).

Corollary found while diagnosing: the replace guard's composite
check treats the engine's own 0-1 write as non-default native state,
so a staged touch that changes Opacity after a 0-1 write refuses at
promotion (`ReplaceGuardRefused ... mg_geo-podcast_30e073e0.mov:
composite`) — the guard comparing the request's own edit against the
live state it changed. The staged revert of a 0-1 Opacity is blocked
the same way; only the direct route (read-back verification, no
native-state comparison) can write it back. With percent-scale values
enforced at the gate, the live state stays at the 100.0 default and
this path does not trigger.

## Incident 2: the touch could not open a scratch project

The touch's project borrow (`resolve_project_open.opened`) listed the
Project Manager's current folder and `LoadProject`ed by name. The
folder shown follows the open project, and every Ren-owned scratch
project here is filed in `Lucie Content/Social Media/Podcast` while
the usually-open qualification project sits at the root — so the
scratch project read as absent and the switch failed, first as a
`REN_UNEXPECTED` crash (`QualificationError` escaping the borrow),
then, while another lane's project was open, as a correct refusal
naming the wrong open project.

Fixed on this branch: the borrow resolves the filed folder from the
disk database and opens by folder path; a switch that still fails is
`ProjectOpenRefused`, not a crash. Defect-naming tests in
`tests/unit/resolve/test_resolve_project_open.py`. Measured live:
the folder route opens the scratch project; the machine is left as
found (open-project read-back on the touch logs).

Not fixed: `ren undo` reaches Resolve through
`reel_build._connect_resolve_project` → `resolve_project_exactly`,
which has no folder route — so `ren undo` still refuses every
scratch project while another lane's project is open
(`TimelineIngestError: No project named exactly ...`). The revert
above went through a forward direct touch instead. Same bug class,
still open.

## Breaktest copy: 0 landed

`ren-scratch-breaktest` reel 13 (`breaktest-selected-dc7fa3f8`):
a leftover `(rebuild staging)` container from an interrupted run
blocked the touch (refused by name, per the lifecycle); cleared by
hand through the guarded path (exact-name delete, approved timeline's
identity read back unchanged, 18 -> 17 timelines). The next attempt
then refused at promotion: 66 pending editor-change diffs, 65 of them
identity/reader noise (re-issued unique ids on every item plus
newly-captured comp/node fields against a first-contact baseline)
and 1 real value (`V1[0] Tilt -684.041 -> -700.5`, newer than the
2026-10-21 baseline — another lane's rebuild or a hand edit on this
shared reel). No `--accept-editor-changes` was passed on a reel
another lane may be working, so no write was made there. The reel and
its debris state are reported as found.

## Left undone

- The full rebuild's measured cost in these projects: no build ran
  under either ledger, so `full_rebuild` stays UNMEASURED (never a
  zero). The bulk build itself is untouched and stays the route for
  constructing whole reels.
- The direct patch's write cost on a fusion-carrying item: correctly
  refused (2 live declines join the 2 recorded ones); qualifying
  `clip.set_property` against `transforms+retiming+fusion` is the live
  lane's work, not this one's.
- The `ren undo` folder route (Incident 2, above).
- `scripts/measure_execution_cost.py` counts a landing twice when a
  run wrote both the request row and the phase rows (6 landings read
  as 10 + 8 samples). Request-level table above; tool-level counts
  beside it.
- Ledger attachment: `ren touch` does not attach a perf ledger, so
  these runs set `REN_PERF_LEDGER`/`REN_PERF_RUN`/`REN_PERF_CAPABILITY`
  (`axi.property_change`, matching the recorded series) around the
  CLI — the engine's own attach mechanism, disclosed here. Without
  it the strategy rows are not written and the measure script cannot
  see the requests.
