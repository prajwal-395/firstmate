# Cost-aware execution selection: which way an edit runs, and what it costs

**What this is:** the punchlist's AXI-016 - "Introduce cost-aware execution
selection" - measured on the Ren-owned scratch copy, and the change that
answers it.

**Date:** 2026-10-21. **Engine:** `video-editing-pilot` at `520a83ad`.

## The question, in the punchlist's words

> - Measure direct patch, staged incremental, and full rebuild costs on
>   equivalent supported edits.
> - Compare preservation guarantees and effective end-to-end latency.
> - Prefer smaller mutations only where they are verified and safe.
> - Retain a bulk-build strategy for constructing entire reels.
> - Do not use a rebuild as an undocumented escape from a qualification
>   refusal.

## What the engine could do, and what it could not say

The engine already had all three routes and a router that chose between
them on what was PROVEN:

| route | what it is | what it had measured |
|---|---|---|
| **direct patch** | one property on one item, written in place, read back | its own phase (`touchup_direct`) |
| **staged incremental** | duplicate the reel, conform it, compose it, verify it, promote it | its three phases (`touchup_duplicate`, `touchup_conform_compose_verify`, `touchup_promote`) |
| **full rebuild** | construct the reel again from the plan | the build's own `reel_edit` row |

What it could not say was what any of them COST in the same unit.  The
three were measured by three different mechanisms, on three different
scales, so no comparison was possible - and the routing record's `cost`
slot read `"unmeasured"` on every decision (PERF-017 put the slot there;
nothing ever filled it in this shape).

So the selection was made on safety alone, and the *cost* of the choice
was never on the record.  A direct patch and a staged touch of the same
one-property change were two numbers nobody could put side by side.

## What changed

### One enumeration, one row per request

`library/tools/execution_cost.py` is the one place that answers the cost
question.  It declares the three strategies, what each preserves, what
each disturbs, what each verifies, and how it is recovered from - and
it records ONE row per execution request: the strategy that ran, the
wall clock it took and the verification outcome it produced.

The row is a SUMMARY row (`perf_ledger.SUMMARY_LAYERS`), like
`reel_edit`: it accounts for seconds the phase rows already carry, so
counting it as a stage would charge the same seconds twice.  The three
strategies are now measured in the same unit, on the same project's own
ledger, and `scripts/measure_execution_cost.py` reads them back with
their denominators.

A strategy with no row is reported as UNMEASURED - never as a zero.
"Nothing was measured" and "it was free" are different facts.

### The selection

`execution_cost.select()` chooses among the strategies some other piece
of evidence has already proven SAFE for this request:

* a candidate that is not safe is excluded, with its reason;
* a candidate that cannot verify what the request needs is excluded,
  with the item it misses;
* when two or more candidates remain and their costs have been
  MEASURED, the cheapest measured one is chosen;
* otherwise the candidate that disturbs the least is chosen, and the
  record says the cost was unmeasured - an unmeasured strategy is never
  reported as a cheap one.

So "prefer smaller mutations only where they are verified and safe" is
the order the selection falls back to, and "choose the cheapest path
that meets the request's verification needs" is what it does when the
measurement exists.  The comparison and the reason ride the receipt
(`receipt["execution_cost"]`), so the record and the decision cannot
disagree.

### The touch path asks, it does not fall through

Before, the touch path TRIED the direct route and fell through to the
staged one when the direct route declined.  Now the direct route is
PROVED first (`_prove_direct`: the plan, the router's decision against
the measured runtime, and the editor-change guard), and the safe routes
are then handed to the selection.  Nothing is written before the
choice, and the choice is recorded.

### The rebuild is not an escape

`execution_cost.assert_documented_escalation()` refuses a decision that
chose the full rebuild after a refusal nobody authorized.  Only an
`unsupported` refusal may be followed by a rebuild, and only with the
caller's authorization; a stale base, a conflicting live edit and an
unreadable timeline are three different facts and none of them is a
reason to reconstruct the reel over the top of it.  The bulk build
itself is untouched and stays the right route for a request that
CONSTRUCTS a reel - that is a construction, not an escape.

## The measurement

**Fixed set of requests.**  One property write on an identified item of
the same built reel (`Reel 13 - breaktest-selected-dc7fa3f8`) in the
Ren-owned scratch copy `ren-scratch-breaktest` (Resolve project
`Ren Scratch - breaktest`).  Never the captain's project, never the
captain's media.

**The two routes, on equivalent supported edits.**  The same kind of
edit - a one-property write on an identified item - run through the
staged route and through the cost-aware selection.  The values differ
between the runs of the same row so that no request is a no-op against
the state the previous one left.

### What the machine has already measured

The engine had measured all three routes before this lane - but on
three different scales, so nobody could compare them.  Read back now,
through `scripts/measure_execution_cost.py`, on the two Ren-owned
scratch copies that carry recorded touches:

```
$ scripts/measure_execution_cost.py .../ren-scratch-bench-demo2
strategy             requests verified completed declined     p50 s     p95 s  scope
------------------------------------------------------------------------------------
direct_patch                2        0         0        2    24.333    25.123  targeted
staged_incremental          2        0         2        0    97.034    199.56  targeted
full_rebuild                -        -         -        - unmeasured unmeasured  full_rebuild
```

and on `ren-scratch-breaktest`:

```
strategy             requests verified completed declined     p50 s     p95 s  scope
------------------------------------------------------------------------------------
direct_patch                2        0         0        2    28.651    34.753  targeted
staged_incremental          2        0         2        0  108.003  1619.827  targeted
full_rebuild                -        -         -        - unmeasured unmeasured  full_rebuild
```

The denominators are in the table, and they are small: **2 staged
requests and 2 direct attempts per project** (plus 3 interrupted staged
touches on `ren-scratch-breaktest`, counted apart, whose seconds are a
lower bound and not a request's cost).  The `p50`/`p95` columns are
over those requests and over nothing else.

Three things fall out of it, and they are the punchlist's question
answered:

1. **The direct patch is not available on a real built reel.**  All
   four recorded attempts (2 per project) DECLINED, and they declined
   for the same named reason: the router proves the direct mode against
   the MEASURED runtime, and the live state of a real reel
   (`existing_effects` is `transforms+retiming+fusion`) does not satisfy
   the recorded qualification predicates for `clip.set_property`
   (`['none', 'transforms', 'transforms+retiming']`).  So the direct
   patch's cost on a real reel is UNMEASURED - it never wrote - and the
   24-35 s it took is the cost of the PROOF that refused, not of a
   write.  This is the punchlist's third bullet, measured: the smaller
   mutation is not preferred here, because on a real reel it is not
   verified, and the engine says so by name.
2. **The staged incremental route is the one that runs**, at 97.0 s and
   199.6 s on the copy with the quietest machine, and 108.0 s and
   1619.8 s on the copy that was shared with a qualification batch and
   two other lanes.  The second number is this machine's contention,
   not the route's shape: the quiet-machine baseline the receipts carry
   is 58.5 s of wall clock, of which the promotion was ~53.6 s
   (`docs/TOUCH_PATH_LATENCY.md`).
3. **The full rebuild is UNMEASURED in these projects** - no build ran
   under these ledgers, so there is no `reel_edit` row to read.  It is
   reported as unmeasured, never as a zero.

### The before and after of this change

**Before.**  The touch path TRIED the direct route and fell through to
the staged one when it declined.  The record said `staged: <the
router's reason>` and nothing else: no cost, no comparison, no
denominator.  The routing record's `cost` slot read `"unmeasured"` on
every decision.  Nothing wrote down which route a request took, so the
two routes' costs were never in the same place, and the next request
could not select from what the last one had measured.

**After.**  The direct route is PROVEN first, the safe routes are
handed to `execution_cost.select`, and the request writes ONE row
(strategy, wall clock, verification outcome) onto the project's own
ledger.  The receipt carries the comparison and the reason:

```json
"execution_cost": {
  "strategy": "staged_incremental",
  "cost_basis": "unmeasured",
  "comparison": [
    {"strategy": "direct_patch", "safe": false,
     "excluded": "not proven safe: the router found no direct route: ...",
     "measured": false, "wall_s_p50": null, "requests": 0},
    {"strategy": "staged_incremental", "safe": true, "chosen": true,
     "measured": true, "requests": 2, "wall_s_p50": 97.034}
  ]
}
```

**The wall clock of a touch does not change by this change.**  The
direct route's proof was already paid before the selection existed, and
the selection does not skip it - it is what makes the direct route a
candidate at all.  What changes is that the engine now KNOWS what each
strategy costs in this project, that the next request selects from
those measurements instead of from nothing, and that the record says
which route ran, what the others cost, and why - with the decline and
its reason on the comparison when the smaller mutation was not taken.

**Denominators.**  Every number above carries the number of requests it
was measured over.  The recorded measurement is 2 staged requests and 2
direct attempts per project; the live measurement this lane attempted
is 8 requests, 0 landed, and each refusal is named above.

### The live measurement, and what stopped it

The measurement above is RECORDED, not fresh.  This lane tried to
measure it again, live, on the Ren-owned scratch copy, and did not get
there.  Eight requests were made, and **none of them landed**:

| attempt | requests | what happened | wall |
|---|---|---|---|
| 1 | 4 (2 staged, 2 direct) | `REN_RESOLVE_BUSY` - the broker did not grant the lease inside its 900 s wait, with the machine's queue 19 deep and its oldest wait at 53 min | 933 s each |
| 2 | 2 (1 staged, 1 direct) | the lease was granted after ~30 min, and the request then refused at the PROJECT OPEN: `could not save 'Ren Qualification'; not opening 'Ren Scratch - breaktest'` | 1803 s each |
| 3 | 2 (1 staged, 1 direct) | the same refusal, with the queue now empty - the open project could not be saved | 22-29 s each |

**Nothing was changed by any of the eight requests.**  Every one of
them refused before a write: four at the lease, four at the project
open.  The captain's project, timelines and media were never touched;
the podcast source media was read only.

The last attempt could not even reach the machine's broker: the
shadow-watcher host had stopped it (`ren-resolved: shutdown requested
by pid 32371: .../shadow_watcher_host.py - stopping`), and a linked
worktree is refused as the place to start one ("a broker started here
would serve production Resolve calls on unmerged code from a directory
a worktree tool may reclaim").  Starting it is not this lane's to do.

So: **the live before/after on a fixed request set is UNMEASURED, and
this lane reports it as such.**  What is measured is the table above -
the recorded costs of the two routes that ran, on the same kind of
supported edit, on the same reel, in the same project - and the
denominators are in the table.

## The comparison, in the engine's own words

`scripts/measure_execution_cost.py <project>` prints the table this
lane measured, from the project's own ledger:

```
strategy             requests verified     p50 s     p95 s  scope
-----------------------------------------------------------------
direct_patch              ...       ...        ...        ...  targeted
staged_incremental        ...       ...        ...        ...  targeted
full_rebuild              ...       ...        ...        ...  full_rebuild
```

and, under it, what each strategy preserves, disturbs, verifies and how
it is recovered from.  The same module is what the selection reads, so
the report and the decision cannot disagree.

## What was left undone

**What was left undone:**

* **The live before/after on a fixed request set did not complete.**
  Eight requests were refused before any write (four at the lease, four
  at the project open, with the reasons and the wall clocks above).
  The measurement that stands is the RECORDED one, read from the
  scratch copies' own ledgers, and it is reported with its denominators
  (2 staged requests and 2 direct attempts per project).
* **The full rebuild's cost is unmeasured in the projects this lane
  could read.**  No build ran under their ledgers, so there is no
  `reel_edit` row.  The repository records ~208 s for a rebuild
  elsewhere (`library/tools/composer.py`); that number is another
  project's and is not used here.
* **The direct patch's cost on a real reel is unmeasured**, because it
  was refused on all four recorded attempts - the live runtime state
  does not satisfy the recorded qualification predicates for
  `clip.set_property`.  Qualifying it against a real reel's state is
  the live lane's work (`vep-axc-live2`), and until then the engine
  correctly refuses to prefer the smaller mutation.
* **The selection does not yet reorder the routes the router picks.**
  It chooses among the routes a caller has already proven safe, which
  is where the three strategies meet today (the touch path's direct
  against staged, and the build path's incremental against the
  rebuild).  A route that is safe but unmeasured still loses to the
  smaller mutation, and the record says the cost was unmeasured.
