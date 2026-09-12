# A transform does not read back the same way twice

Measured 2026-09-12 on `Podcast (field test)` / `lucie/geo-podcast`,
DaVinci Resolve Studio 21.1, while pricing and then repairing the reel
build's skip decision (`library/tools/reel_rebuild_need.py`).

**The finding in one line: what Resolve returns for a clip's Pan and
Tilt depends on which timeline is CURRENT at the moment of the read,
and the timeline being read is not always that one.**

This document replaces an earlier one that concluded from a single pair
of builds that a reel build is not reproducible.  That conclusion was
too strong, and the measurement below is what overturned it.

## The reading, four ways

Three promoted reels, nothing touched between reads, only the current
timeline changed.  First 8 hex of each carried digest
(`reel_rebuild_need.carried_digest`, a full read-back of every clip's
row, span, source window, name and transform):

| current timeline | Reel 13 | Reel 23 | Reel 26 |
|---|---|---|---|
| Reel 13 (1080x1920) | `227d3096` | `6d9b3608` | `e6c2f02a` |
| Reel 23 (1080x1920) | `2dc8a569` | `c3492434` | `e6c2f02a` |
| Reel 26 (1080x1920) | `2dc8a569` | `6d9b3608` | `e8b42186` |
| the master (3840x2160) | `bfc63ef8` | `15d616bf` | `a61fbe42` |

Four readings of one untouched reel, four digests.  The master's row is
the entry-unit conversion `overlay_placement.entry_unit_mismatch`
documents - 3840x2160 is 2x 1080x1920 in both dimensions.  **The other
three rows are all 1080x1920 and still disagree**, so the conversion is
not the whole of it.

A **SELF-read** - the reel made current, then snapshotted - is stable.
Measured over four rounds, each preceded by a deliberately different
current timeline (the 4K master, and each of the three reels): all
three reels returned the same digest every round, `227d3096` /
`c3492434` / `e8b42186`.

## What it broke

The skip decision compares a digest recorded at promotion against one
read at the next build.  Promotion closed every record with the LAST
promoted reel current; the next build compared them with the ENTRY
timeline current.  Those are different timelines, so the two ends
disagreed by construction.

Measured live, three reels whose state had not changed:

| build | entered on | left alone | placed again |
|---|---|---|---|
| second build | Reel 23 | Reel 13 | Reel 23, Reel 26 |
| third build | Reel 23 | Reel 13 | Reel 23, Reel 26 |

Reel 13 matched because the entry timeline happened to make its reading
agree with the record; the other two read as *drifted* and paid a full
Resolve pass each.  That is the FAIL-CLOSED direction - a needless
rebuild, never a wrong skip - but it is exactly the saving the decision
exists for.  Both ends now take a self-read
(`reel_rebuild_need.carried_digest_live`), and so does the master
read-back, which is part of every reel's derivation digest and would
otherwise rebuild the whole project from a different entry point.

`tests/test_reel_build_leaves_unchanged_reels_alone.py::test_a_reel_reads_the_same_however_the_run_entered`
enters a second build on every timeline in the project in turn and
requires all three reels left alone each time.  It FAILS with the
self-read removed.

## The renderer is exact; a build is reproducible when the entry is

The instrument first, because a comparison is only worth its noise
floor.  Lossless PNG RGB8, 180-frame ranges at the head and at the tail
where the freeze and the switch-off live:

| comparison | frames | mean abs | max | identical frames |
|---|---|---|---|---|
| same timeline, render pass 1 vs pass 2 (7 pairs) | 180 each | 0.000000 | 0 | 180/180 |

**Zero**, every time.  Re-rendering one timeline is bit-exact on this
machine and build, which is what `resolve_surfaces` already states.

Then three consecutive builds of Reel 23, each entered on a 1080x1920
reel, each compared on the second render pass:

| comparison | frames | mean abs | max | identical frames |
|---|---|---|---|---|
| build 1 vs build 2, head | 180 | 0.000000 | 0 | 180/180 |
| build 1 vs build 2, tail | 180 | 0.000000 | 0 | 180/180 |
| build 2 vs build 3, head | 180 | 0.000000 | 0 | 180/180 |
| build 2 vs build 3, tail | 180 | 0.000000 | 0 | 180/180 |

So a rebuild IS reproducible with the entry controlled.

## The pair that was not, and what it is evidence for

Earlier the same day, two builds of Reel 23 - one as part of a batch of
three, one on its own, with different container suffixes and no control
over the entry timeline - differed on roughly 63% of subpixels:

| comparison | frames | mean abs | max | identical frames |
|---|---|---|---|---|
| build A vs build B, head | 180 | 11.608316 | 255 | 0/180 |
| build A vs build B, middle | 180 | 4.759763 | 231 | 0/180 |
| build A vs build B, tail | 180 | 10.603108 | 255 | 71/180 |

Read off each arm's own build snapshot, every V1 picture item's Pan and
Tilt differed by exactly **2.0** (Tilt -0.395 vs -0.79; Pan -0.25 vs
-0.5, 12.457 vs 24.914), with `ZoomX`/`ZoomY` 2.307 on both, identical
spans, identical rows, 47 clips each.  Only Pan and Tilt moved, and
only on picture.

Two is the 3840x2160-to-1080x1920 ratio, and the table at the top of
this document shows the master's row carrying exactly that kind of
shift.  **This document does not claim to have root-caused that pair**:
the entry timeline was not recorded for either arm, and two other
variables differed (batch size and container suffix).  What it does
establish is that the reading is entry-dependent at all, which is a
mechanism the pair previously had none of.  Issue 999 owns the
positioning question; this is evidence for it, not a repair of it.

## What it means for anyone using a rebuild as a control

1. **Control the entry timeline, or the comparison is not one.**  Both
   arms must be built with a delivery-shaped timeline current, and it
   is worth recording which.  With that controlled, a rebuild is a
   usable control arm - three of them here were byte-identical.
2. **Never read a transform off a timeline that is not current.**  The
   number will be wrong in a way that reads cleanly.  The engine's own
   read-backs go through `carried_digest_live`.
3. **Re-placing an unchanged reel still carries risk.**  Leaving it
   alone is a protection as well as a saving - the reel the captain
   approved keeps the transforms it was approved with - and the
   carried digest is a comparison against the approved artefact rather
   than against a fresh build of it.
