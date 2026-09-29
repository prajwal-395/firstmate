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

## The verifier restores units before grading

Measured on Reel 24 on 2026-09-29: its build logged a 1080x1920 punch-in
with Zoom 2.138585 and Tilt -696.041, and the offline build calculation
`reel_look.punch_in_properties` returns the same values at the recorded
draw gain of 1.0. The separate verify command reported 5 F12 errors with
the picture's height equal to the screen window's height but its bottom
165.8 pixels high. The handmade build record supplied the recorded gain;
it did not supply clip transforms, so it could not have replaced the
build's Tilt.

The same batch provides a comparison: Reel 25 used the same TV-frame
look and draw gain, built an LC4932 shot at Zoom 2.1386 / Tilt -696.041,
and its scoped `reel.verify` completed with zero errors before
promotion. The builder's transform is therefore consistent with a reel
that passed F12 under that look.

`run_verification` used to grade raw Pan/Tilt read through each
non-current timeline handle. That read is in the current timeline's
units, so the verifier was comparing it as though it belonged to the
target reel. Scaling both Pan and Tilt by one quarter reproduces the
reported vertical geometry. The original verify run did not save the
current timeline's dimensions, so that quarter-size context is a
reproduction of the failure, not a claim about which timeline was
current then.

The verifier now reads the current timeline's name and dimensions on
both sides of every snapshot, restores Pan and Tilt to the snapshot's
resolution, and hashes and grades those restored values. It refuses a
snapshot if the current timeline changed while it was read. The
regression `tests/test_reel_verifier_timeline_units.py` exercises the
real `reel.verify` operation offline: the build calculator's Reel 24
values fail F12 when left at quarter scale and pass after the verifier
restores their timeline units.
