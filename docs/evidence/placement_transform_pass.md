# The placement pass: one read context, one index, for the whole reel

**The punch list's PERF-012, acted on.** The critical-path profile
(PR 3144, `docs/benchmarks/2026-10-21-critical-path.md`) named the
reel build's next optimization from its own measurement:

> the placement's per-item transform replay, **154.8 s**: the placement
> writes each item's transform one API call at a time - one call for the
> whole reel's transforms

That was the biggest single cost in the profile: 154.8 s of the build's
294.3 s AXI stage, 182.8 s of the build's 427.4 s instrumented wall,
and 42.8% of the build's `transaction_overhead`. The profile also said
what would NOT help: not one of the three task classes was limited by
inference (0.0 s in a model), Rust would not move a cost that lives
inside Resolve, and the one place parallelism would have helped was
already taken.

This file records what was implemented, what it measures, and what was
NOT measured.

## What the pass was doing

The reel build's placement runs in two halves: the placers record what
they would place, one `ImportTimelineFromFile` lands it, and then the
DEFERRED pass writes each placed item's transform on the real timeline
(`library/tools/reel_otio_placement.run_deferred`). The deferred pass
is where the 154.8 s was, and it was spent on per-placement Resolve
calls that had nothing to do with the transform itself:

| what the pass did | why it was there |
|---|---|
| every placement built its OWN transform read context | `reel_read.TransformReadContext` re-reads the current timeline, both timelines' identities and both resolutions - about nineteen Resolve calls - to know the units a Pan/Tilt is in |
| every placement asked Resolve for its row's item list | to find the item it had just placed |
| every placement WALKED that list | reading each item's start frame until it found its own |
| every placement asked for the row's item list AGAIN | the read-back must not read through the handle the write went through, because such a handle echoes the value it was given (Reel 09 positioning proof, 2026-09-10) |

So a row of thirty placements read 1 + 2 + ... + 30 start frames, twice
over, and built thirty read contexts to place thirty overlays.

## What changed

Two changes, both inside the placement's deferred pass, both changing
no value that lands on the timeline:

1. **One read context for the whole pass**
   (`library/tools/reel_read.py`, `shared_transform_reads`). The
   per-read cursor check stays - a cursor that moved still refuses at
   the item that saw it - and the batch's resolution check happens once
   at the end, which is what `TransformReadContext` was written for
   ("a resolution change refuses the batch before it can be accepted").
   A context that cannot be established at all falls back to the
   per-placement behaviour, so a placement that cannot be judged is
   still REPORTED per overlay rather than refusing the whole reel.

2. **One index of the reel's items for the whole pass**
   (`library/tools/overlay_placement.py`, `placement_index`). The row's
   items are fetched once and each item's position is remembered by
   its start frame, so a placement names its item without a walk. The
   read-back still reads a FRESH handle - the fresh list is asked for
   once per placement, and the item is taken from it by position and
   confirmed by its own start frame.

## The measurement

`scripts/bench_deferred_transform.py` runs the deferred pass against
the engine's own Resolve double and counts every Resolve call it
makes. The double answers the same methods the live session does, so
the count is the engine's own.

Before and after, on the same workload, with the overlays landed
counted on both sides:

| overlays on one row | Resolve calls before | Resolve calls after | cut | overlays landed (before / after) |
|---|---|---|---|---|
| 1 | 38 | 39 | -2% | 1 / 1 |
| 9 | 246 | 175 | **-29%** | 9 / 9 |
| 30 | 1 401 | 532 | **-62%** | 30 / 30 |

Where the calls went, at thirty overlays:

| Resolve call | before | after |
|---|---|---|
| `GetStart` (the walk) | 930 | 90 |
| `GetSetting` (the context's resolutions) | 8 | 8 |
| `GetItemListInTrack` | 61 | 32 |
| `GetCurrentTimeline` (the cursor guard) | 62 | 62 |
| `GetProperty` / `SetProperty` / `GetName` / `GetUniqueId` | 340 | 340 |

The call count grows LINEARLY now instead of quadratically: the
`GetStart` walk was the reel's shape paid for twice per placement, and
the context's resolutions were paid per placement rather than per pass.
The cursor guard is unchanged on purpose - it is the check that keeps a
moved cursor from reading as a measurement.

**The denominator.** Every number above is one run of the bench on one
machine, with one overlay shape (N overlays on one row, each with its
own placement). The live denominator is the build's own phase in the
performance ledger, `otio_placement.deferred_transform_s`.

## What was NOT measured

- **The live before/after is not in this file.** The scratch copies' reel
  builds refuse BEFORE the deferred pass, on the base commit as well as
  on this branch:

  | live run (scratch copy, reel 13) | code | outcome |
  |---|---|---|
  | `ren-scratch-breaktest`, `--placement otio` | base | `OtioPlacementRefused: the relinked tv_frame_429cca6c86.png at V3@0 does not read back bound to the pooled clip` |
  | `ren-scratch-breaktest`, `--placement otio` | this branch | the same refusal, the same place (`reel_otio_placement.py:688`) |
  | `ren-scratch-l3wave`, `--placement otio` | this branch | the same refusal (`tv_frame_5d1f8e7128.png`) |
  | `ren-scratch-axi006`, `--placement otio` | this branch | the same refusal (`tv_frame_5727937c7f.png`) |
  | `ren-scratch-l3wave`, `--placement append` | this branch | `REN_RESOLVE_BUSY` - another lane held the build turn for the whole 900 s wait |

  The refusal is a stale POOL, not the code: each build re-renders the
  TV frame into a new content-hashed file, the media pool still holds
  the previous one, and the relink finds the old clip and refuses the
  binding it reads back. It happens on the base commit too, so it is
  not this change - but it does mean the deferred pass, the thing this
  change optimizes, is never reached by a live build on these copies
  today.

  The same project's ledger still holds BASE-CODE rows for the phase
  from earlier today, on the same reel and the same placement mode
  (`placement: otio`): `run-20261022T033444Z-5e0fa8` at 17.99 s and
  `run-20261022T035446Z-46ad6e` at 21.16 s. Those are the before arm a
  live after arm would be read against, once a build gets past the
  import.
- **The double's wall time is not Resolve's wall time.** The bench
  prints `engine_seconds` (the engine's own work against the double);
  the live number is the phase in the ledger. The CALL COUNT is the
  claim here, and it is the count the live session pays.
- **The double does not model the echo.** Resolve returns a NEW handle
  object per `GetItemListInTrack`; the double returns the same objects.
  The read-back's freshness is therefore enforced by the code (the
  fresh list, the position, the start frame) and cannot be proven by
  the double. The live build is the proof.

## The tests

`tests/unit/reels/test_deferred_transform_shared_reads.py` - six
defect-naming tests, each of which fails on the base commit:

| the defect | the test |
|---|---|
| the pass asks Resolve for the timeline's resolution once per placement | the count is the same for one overlay and for nine |
| the pass reads the units zero times | the units are still asked for, before the first placement is judged |
| the pass builds no context at all | one overlay still asks Resolve for the units |
| a placement lands on its neighbour | every overlay lands its OWN placement |
| the row is walked per placement | twelve placements read a bounded number of start frames |
| the row is fetched per placement | twelve placements fetch the row a bounded number of times |
| a cursor that moves under the pass is ignored | the move is reported |
| a resolution that changes under the pass is ignored | the reel refuses |

The existing suites still pass: `tests/unit/reels` (1 595 passed; one
serial, timing-bound lease test failed under the machine's load and
passes on its own), `tests/scenarios/test_reel_build.py`,
`tests/scenarios/test_reel_otio_placement.py`, `tests/contracts/
test_editing_invariants.py`, `tests/contracts/test_timeline_read_api.py`
and `tests/unit/captions` (792 passed).
