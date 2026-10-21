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
