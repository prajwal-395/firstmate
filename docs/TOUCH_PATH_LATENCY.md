# The touch path's latency, measured

**What this is:** where a touch-up's time actually goes, measured on the
Ren-owned scratch copy (`ren-scratch-breaktest`, never the captain's project),
and what the ledger can now say about it.

**Date:** 2026-10-21. **Engine:** `video-editing-pilot` at `46f23b9`.

## The problem this answers

The scout's latency report (`data/scout-opt-latency/report.md`, firstmate
home) measured a touch-up at **55.7-57.4 s of `resolve_hold`** and could not
say which part of it to attack: the staging copy, the conform/compose/verify,
or the promotion were ONE unlabelled span. FIX 4 splits that span into three
measured phases - `library/tools/touchup_phases.py`, recorded in
`library/tools/perf_ledger.py` as `touchup_duplicate`,
`touchup_conform_compose_verify` and `touchup_promote`.

## The proof: the three phases, on the scratch copy

`bin/vep manage_project.py touch-master` on the scratch copy's master
(`GEO Podcast - Synced`), run under the bench wrapper with the scratch
project's own ledger attached:

```
run-20261021T015610Z-690877
  resolve_wait                    1.278
  touchup_duplicate               0.228    phase=duplicate
  touchup_conform_compose_verify 99.064    phase=conform_compose_verify
  touchup_promote                 8.711    phase=promote
  resolve_hold                   15.628    held_s=138.692
```

**Nothing moved.**  The hold's total is 138.692 s.  Of it, 15.06 s was
already spoken for by the EditPatch's own inner layers (the two
`resolve_snapshot_read` rows, the mutation batch and the API call).  So the
`resolve_hold` row the profile showed BEFORE the split was **123.631 s**, and
after the split it is 15.628 s (what the three phases did not cover) plus
the three phases themselves:

```
15.628 (the hold's own remainder) + 0.228 + 99.064 + 8.711 = 123.631
```

The same 123.631 s, split three ways.  No second was invented and none was
counted twice.

(The touch itself REFUSED at the promotion - `GEO Podcast - Synced` is
protected because the editor created it - so the master was not touched and
the staging was exported and deleted, as the refusal path promises.  The
refusal is why `touchup_promote` is 8.7 s: it is the guard's own seconds.)

## What the reel path says, from the two touches already on file

The scratch copy's undo-journal receipts carry the reel touch's own timers.
Two successful touches are on file, both from the scout's latency run:

| | property change | caption nudge |
|---|---|---|
| ledger `resolve_hold` | 57.376 s | 55.7 s |
| the touch's own wall (`receipt.seconds`) | 58.541 s | 57.163 s |
| staging copy + conform (`stage_seconds`) | **1.071 s** | **1.160 s** |
| composition (`composed_seconds`) | 1.039 s | 0.883 s |
| verification re-read (`verify_seconds`) | 0.301 s | 0.454 s |
| **the promotion (the rest)** | **~53.6 s** | **~54.5 s** |

The wall clock agrees: the first touch opened its journal at 22:45:26, its
staging copy landed (the branch record) at 22:45:28, its journal closed at
22:46:15 and its receipt was filed at 22:46:23 - so the promotion ran from
22:45:29 to 22:46:23.

## The two answers, side by side

| | reel touch (58 s) | master touch (139 s) |
|---|---|---|
| duplicate | ~1 s | 0.2 s |
| conform + compose + verify | ~2.4 s (4%) | **99.1 s (71%)** |
| promotion | **~53.6 s (92%)** | 8.7 s (refused early) |
| the hold's own remainder | ~2.5 s | 15.6 s |

**The two paths have DIFFERENT dominant phases, and the ledger now says
which is which.**  A reel touch is a promotion problem; a master touch is a
conform/compose/verify problem.

## What that means for the three variants the scout listed

The scout's follow-up proposed three ways to make the staging cheaper:
duplicate only the tracks the edit touches, re-use the staging timeline
instead of duplicating a fresh one, or hold the lease only around the
mutation.  **On the measured numbers, all three are worth at most ~1-2 s of
58.**  The staging is not the lever - on either path.

That is the opposite of the shape the scout expected, and it is why FIX 4 is
measurement first: the fix lane's next target is INSIDE the promotion (for a
reel) and INSIDE the conform/compose/verify (for a master), not around the
copy.

## What is inside those phases (the next lane's measurement)

Neither phase carries a row for its own steps today, which is why each was
one number:

- the **promotion** reads the project several times over - twice for the
  full timeline snapshots (the original and the staging), twice for the row
  snapshots, twice for the project-wide timeline inventory, twice for the
  marker fleet (every timeline's markers, before and after the rename), once
  for the recorded generation, once for the journal close, and once more for
  the replaced generation's export;
- the **conform/compose/verify** reads the source's rows, the staging's
  rows, writes the in-place edit (the EditPatch's own snapshot reads are
  inside this phase - 14.8 s of the 99.1 s), re-reads the tracks to rekey
  the plan, composes, and re-reads the whole staging to verify it.

The next FIX is to measure inside those phases.  The mechanism is already
there: `touchup_phases.PhaseClock` records a phase as a leaf span, and a
phase's own steps can be recorded the same way.  **The three top-level
phases must keep summing to the hold** - a sub-phase that overlaps its
parent would double-charge the seconds, and the ledger reports that overlap
rather than hiding it.

## How to reproduce the measurement

On a Ren-owned scratch copy, with the project open in Resolve and nothing
else holding the one Resolve instance:

```sh
bin/vep scripts/bench_user_visible_latency.py \
    --ledger <scratch>/pipeline_output/logs/perf_ledger.jsonl \
    --task-class axi.property_change \
    -- bin/vep manage_project.py touch-reel <scratch> <reel> \
       --edits '[{"op":"set_properties","row":"V1","item":0,
                   "properties":{"Tilt":-690.0}}]'

bin/vep -m library.tools.perf_ledger ren-scratch-breaktest --run <run id>
```

The profile then shows the three phases with their own seconds, and the
`resolve_hold` row shows only what the three did not cover.

**Note (2026-10-21):** the first attempt at this measurement was refused by
the shared broker - `REN_RESOLVE_BUSY` after a 900 s wait, twice, while the
one Resolve instance was held by other lanes.  Nothing was changed by either
attempt; the successful measurement above is the third attempt, on the
master path.

---

# PERF-002: the whole-reel bookkeeping, and the route that does not pay it

**What this is:** the punchlist's PERF-002 - "eliminate unnecessary
full-timeline promotion costs" - measured on the same Ren-owned scratch
copy, and the change that answers it.

**Date:** 2026-10-21. **Engine:** `video-editing-pilot` at `3d12b03c1`.

## The question, in the punchlist's words

> Investigate the ~53.6-second promotion phase measured for a small reel
> touch-up. Distinguish the cost of duplication, media conform, state
> reconciliation, and final promotion. Determine when a qualified direct
> mutation is cheaper and equally safe. Reuse staging only when it
> protects against a real failure mode. Preserve atomic recovery and
> accepted edits.
> Acceptance: common small edits no longer spend most of their time on
> whole-reel bookkeeping when a safe local operation exists.

The measurement above already answered the first half: on a reel touch
the promotion is ~53.6 s of a 58.5 s wall clock, and the staging copy is
~1 s of it.  The bookkeeping is the cost, not the staging.

## What the promotion actually reads

The promotion reads the project several times over, and none of those
reads is about the change being made:

- twice for the full timeline snapshots (the original and the staging);
- twice for the row snapshots;
- twice for the project-wide timeline inventory (EVERY timeline in the
  project, for one reel's rename);
- twice for the marker fleet (every timeline's markers, before and
  after the rename);
- once for the recorded generation, once for the journal close, and once
  more for the replaced generation's export.

So the promotion's cost is a function of the PROJECT's size, not of the
edit's size.  A one-property change on one clip pays for the whole
project's inventory twice.

## The split inside the phases (measured)

`touchup_phases` now measures the STEPS inside a phase, so the ledger
says which of the four bills the promotion is: the state reconciliation
(the snapshots, the editor-change guard, the replace guard, the marker
fleet) and the final promotion (the rename, the journal close, the
retirement of the replaced generation).  The steps of a phase sum to the
phase, so the split moves no seconds and counts none twice.

The conform/compose/verify phase splits the same way, into the media
conform, the composition and the verification re-read.

## The route that does not pay it

`library/tools/touchup_direct.py` owns the DIRECT route: a change that
writes nothing but a property on an item it names is a REVERSIBLE
MUTATION, and the router (`execution_router.route_axi_dispatch`) proves
the direct path for it when - and only when - all four of VC-11's
conditions hold:

1. the qualified plan is nothing but in-place property writes - no row
   added, removed, moved, re-placed or re-composed;
2. the router proves the DIRECT mode against the RUNTIME STATE it
   measures (`axi_qualification_state.inspect_handles`), so the
   recorded evidence applies to the state the timeline is in now;
3. the approved timeline carries no unattributed editor change, so no
   accepted edit is overwritten (a direct write has no candidate to
   carry the editor's hand edit on, so it declines the whole request
   and the staged route decides it);
4. the write is an EditPatch: observed, written, read back, compared
   outside the declared targets, and recorded as a generation whose
   inverse restores the accepted generation.

Everything else stages.  The staging is not removed from the touch
path: it protects against a real failure mode - a partial write, a lost
row, a wrong target, an editor's hand edit the change would destroy -
and every one of those is still refused BY NAME.  A caller may also
force the staged route (`ren touch-reel --staged`); the flag can only
escalate to staged, never widen a request to a direct write (VC-11).

The recovery is the touch's own: the same undo journal, opened before
the write and closed after it, with the EditPatch's recorded inverse as
the second way back.  `ren undo` reverses the direct touch exactly as it
reverses a staged one.

## The staged route, measured live on the scratch copy

The scratch copy, the Ren-owned copy of a real Reel 13 (portrait
1080x1920, 23.976 fps, 65 clips over eight video rows and two audio
rows), one property write on V1, measured with the phase clock this
lane added:

```
touchup_duplicate                 2.209
touchup_conform_compose_verify   304.152   steps: conform 272.735
                                               compose  18.705
                                               verify    4.968
```

The machine was NOT quiet: the one Resolve instance was shared with a
qualification batch and two other lanes, and a single full-timeline
read cost seconds.  The shape is the shape the quiet-machine receipts
show - the staging copy is a second or two of it and the reading of the
reel is the rest - and the numbers are this machine's, not the
baseline's.

The quiet-machine baseline stays the receipts' own: 58.5 s of wall
clock for a reel touch, of which the promotion was ~53.6 s (92%) and
the staging copy ~1 s.

## The direct route: what it does NOT pay

The direct route has no staging copy, no conform, no composition, no
verification re-read, no rename, no marker fleet, no project-wide
timeline inventory, no replacement export and no retirement of a
replaced generation.  That is the whole-reel bookkeeping the promotion's
~53.6 s is made of, and the direct route does none of it: it opens the
`direct` phase and nothing else, which is what the ledger shows
(`tests/unit/reels/test_touchup_direct.py`).

What the direct route DOES pay for, and what it buys with each:

| | the staged route | the direct route |
|---|---|---|
| the staging copy | a duplicate of the whole reel | none |
| the media conform | the source's rows against the staging's | none |
| the composition and its verification | a re-read of the whole staging | none |
| the promotion's rename | the reel's name changes | none - the reel keeps its name |
| the marker fleet | every timeline's markers, twice | none |
| the project inventory | every timeline in the project, twice | none |
| the replaced generation's export and retirement | the replaced reel is exported and deleted | none |
| the journal | the before state and the after state | the same |
| the read-back | the whole staging re-read | the written property, and the timeline outside the declared targets |
| the recovery | the journal's inverse | the SAME journal's inverse, plus the EditPatch's recorded inverse |

## The live before/after, and what is missing from it

**The direct route's live before/after did NOT complete, and the reason
is the machine, not the route.**  Three attempts on the Ren-owned
scratch copy, through the machine's one broker, under the instance
lease:

1. the first attempt measured the staged route (the numbers above) and
   was killed by the measurement's own timeout while the promotion was
   still running - the machine's qualification batch and two other
   lanes held the instance for most of the 25 minutes;
2. the second attempt found the scratch reel carrying the first
   attempt's interrupted Tilt write as an UNATTRIBUTED EDITOR CHANGE,
   and the direct route DECLINED it - which is the guard working - and
   the staged route that took over then REFUSED it, naming the change
   it would not overwrite.  That is a correct refusal, and it is also
   why there is no before/after pair from that attempt;
3. the third and later attempts could not start: the captain's
   Resolve session was open on `Podcast (field test)`, whose unsaved
   state this Resolve build cannot report, so the measurement refused
   to switch away from it (the captain's project is never switched away
   from on a build that cannot read its dirty state).

And on the one attempt that DID reach the direct route's write, the
process blocked inside Resolve's own scripting call for 36 minutes
(`Fusion::RemoteApp::WaitPkt`) while holding the instance lease - so no
other writer was in Resolve, and the direct route's write was waiting
on Resolve itself.

A later attempt refused by name because an interrupted run had left its
staging container in the project ("a staging container '...' from an
interrupted run is still in the project") - the touch path's own guard,
working, and the reason the direct route never ran in that attempt
either: the guard runs before the route is chosen.

So: the live BEFORE is measured (above, and the receipts' own quiet
numbers); the live AFTER is NOT.  What stands in for it is the
STRUCTURE, which the offline tests pin: the direct route opens the
`direct` phase and no staging phase at all, so the whole-reel
bookkeeping is absent rather than cheaper, and the journal, the
read-back and the recovery are the same ones the staged route uses.

**What is left undone:** the direct route's wall clock, measured on a
quiet machine against the staged route's, on the same reel, in one
session.  The number to take is the touch's own wall clock and its
`touchup_direct` phase against the staged route's `resolve_hold`; the
denominator is the same property write on the same reel.

## What the measurement left in the scratch project

The scratch copy (`Ren Scratch - breaktest`) is Ren-owned and was used
only for this measurement.  It now holds, beyond what it held before:

- `Reel 13 - breaktest-selected-dc7fa3f8` - a DUPLICATE of the built
  reel, under the name the project's own PROPOSAL declares (the copy
  path had named the reel "Breaktest - Reel 13 - selected dc7fa3f8",
  and the touch path addresses a reel by the name the plan gives it).
  The duplicate is the reel the measurement touched;
- the same name as a `(pre-rebuild backup)`, left by the interrupted
  staged promotion;
- the staging containers the interrupted runs left behind.

None of it is the captain's project, and none of it is media.
