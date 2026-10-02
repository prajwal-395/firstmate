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
documents - and it is ANISOTROPIC, per axis the current timeline's
dimension over the read timeline's: Pan x 3840/1080 = 3.5556, Tilt x
2160/1920 = 1.125 (measured 2026-09-17, three-way cross-current read).
**The other three rows are all 1080x1920 and still disagree**, so the
conversion is not the whole of it.

No current timeline in this project can produce a uniform 0.5 on both
axes: that would require a 540x960 current timeline, and none exists.

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

`tests/unit/reels/test_reel_build_leaves_unchanged_reels_alone.py::test_a_reel_reads_the_same_however_the_run_entered`
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

A uniform 2.0 on BOTH axes is not what a cross-resolution read gives:
the 3840x2160-to-1080x1920 conversion is anisotropic (Pan x 3.5556,
Tilt x 1.125, above), and the table at the top of this document shows
the master's row carrying exactly that kind of shift.  **This document
does not claim to have root-caused that pair**:
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
2. **Restore the current-timeline scale before grading a non-current
   read.** Resolve scales Pan by current-width/read-width and Tilt by
   current-height/read-height. `reel_read.restore_transform_timeline_units`
   reverses those two ratios, and the conformance verifier records the
   current and target dimensions around each snapshot. Other read-backs
   that compare raw transforms still need a self-read, as
   `carried_digest_live` does.
3. **Re-placing an unchanged reel still carries risk.**  Leaving it
   alone is a protection as well as a saving - the reel the captain
   approved keeps the transforms it was approved with - and the
   carried digest is a comparison against the approved artefact rather
   than against a fresh build of it.

## Reel 24: normalize every transform read before grading

The earlier ending of this document incorrectly treated Tilt -696.041
as a generated Reel 24 punch-in. The 2026-09-29 build log separates the
two: the five generated punches logged Tilt -174.01, while the opening
LCATL0013.MXF two-shot had no automatic punch and later received the
captain's recorded override Tilt -696.041. That run measured renderer
draw gain 4.0.

The output reel frame is 1080x1920: `reel_build.reel_resolution`
resolves the delivery format and the builder explicitly sets that
resolution on each staging timeline. The project default remains
3840x2160. When a 1080x1920 reel is read through a non-current handle
while that master is current, Resolve scales returned Pan by 3840/1080
(3.5556) and Tilt by 2160/1920 (1.125). These are readback scales, not
the target timeline's stored values. `reel_read.restore_transform_timeline_units`
is the single conversion: F12 applies it to snapshot transforms, and
build-time punch, override and motion coverage reads go through
`reel_read.read_transform_timeline_units`, which uses that same
conversion and refuses a current-timeline change during the read.
Placement-time overlay geometry, the post-build overlay intent sweep,
and its stored-position and pixel checks use that helper too, so all
compare against delivery-frame placements in the same units.

The stored override is a pair of Pan/Tilt unit values, not a pixel
offset. A value preserves one visual shift only at the draw gain where
it was recorded. The same Pan/Tilt law computes automatic aims with
the current gain; override application rebases the stored values from
their recorded gain to the build's measured gain:

    applied_value = recorded_value * recorded_draw_gain / build_draw_gain

Legacy overrides without `recorded_draw_gain` use the documented
reference gain 1.0. The Reel 24 opening override is one of those: the
19:08Z build measured gain 1.0, so its `Pan=39.263`, `Tilt=-696.041`
remain the reference values. At the later gain 4.0 build, the pipeline
applies Pan 9.81575 and Tilt -174.01025; no project JSON edit is needed.
For any legacy value known to have been recorded under another gain,
the one-time data conversion is to add `recorded_draw_gain` with that
source gain. New `capture-transform` records require `--draw-gain` and
store the value with its source gain. Typed `record-transform` values
use reference gain 1.0 unless given `--draw-gain`.

For the failed opening shot, source 1920x1080, frame 1080x1920, zoom
2.1386, the unrebased Pan/Tilt and draw gain 4.0 produce these offline
rectangles against screen window (56.106, 530.6365, 1022.967, 1829.827):

| Applied Tilt | Picture rectangle | Coverage |
|---:|---|---|
| -696.041 | (-458, 1191, 1852, 2490) | misses the top by 660.4 px |
| -174.014 | (-458, 531, 1852, 1830) | covers the window |

The current generated aim for the matching subject returns Tilt -174.014
at three-decimal storage precision. The -696.041 value is correct at
gain 1, but wrong when applied raw at gain 4. PR #1474 keeps the visual
aim by rebasing the override at application time, while
https://github.com/prajwal-395/video_editing_pilot/pull/1472 keeps
non-current timeline Pan/Tilt in the same target timeline units.
https://github.com/prajwal-395/video_editing_pilot/pull/1474 adds the
application-time rebase. `tests/scenarios/test_reel_build_sop_conformance.py` exercises the
actual `build_reel_timeline` path offline at both gains, including the
opening override and the other punch-ins.

The two 2026-09-29 Reel 24 builds used the same controlled gain probe:
a full-frame 1080x1920 plate, native red-marker check, Tilt -200, and
two agreeing stills. At 19:08Z, the white bars moved from file rows
283..403 to rows 483..603, measuring gain 1.0. At 22:39Z they moved to
rows 1083..1203, measuring gain 4.0. The plate and measurement checks
were unchanged, and the PR's readback normalization does not alter the
probe or renderer. This is a measured change in Resolve's renderer
state, as both build logs report; those logs do not identify which
underlying Resolve state changed.
