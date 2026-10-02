# Reel 13's lost edits, and the positioning truth

Investigated 2026-09-11 against the live Resolve project "Podcast (field test)".
Nothing was written to `Reel 13`.  The complete live state of that timeline is
captured in `timeline_captures/reel13-live-20260911/` of the geo-podcast
project repo - moved out of this repo's `captures/` drop zone, because
project data lives with the project and never in the pipeline.  The
capture tooling that produced it (`capture_timeline.py`,
`capture_fusion_comps.py`) lives in `library/tools/`; the guard
refusing a recommit is `library/tools/project_data_guard.py`.
`measure_overlay_draw_positions.py` produced the draw-position half
and has been DELETED: it computed each overlay's screen row FROM the
draw-gain constant, so it could only ever restate that constant.

---

## 1. Did the marker fixes reach the built output?

**Both fixes are in the plan.  Neither is in the timeline the captain opens.**
Settled from the artefact - the reels' own audio, transcribed with whisperX
(`large-v3`, word-aligned), and stills exported from the timelines - never from a
plan or a status line.

### Reel 13 - "the ending TV close animation plays while she's finishing"

The complaint is still true in the built timeline.

| | |
|---|---|
| live timeline | 1909 frames (79.62s) |
| TV frame overlay (V3) | frames 0-1902, so the switch-off tail runs 78.6-79.33s |
| her last words, transcribed | `78.03-78.85 "you should go check it out"` / `78.95-79.65 "The link's in our bio"` |

The animation plays over both.

The plan `pipeline_output/review/reel_proposals_v2.json` HAS the fix - the closer
end moved `341.27 -> 342.03` in the snapshot written at 03:20Z.  Replaying the
build's own boundary arithmetic (`reel_proposal.snap_moment_to_speech` then
`reel_build.redundant_takes`/`keep_ranges`) on that plan yields **1921 frames**,
which is exactly the `end_frame` of the scratch timeline built at 04:22Z and
recorded in `review/Reel_13_..._(baseline scratch).timeline.json`.  That scratch
was verified, never promoted, and its bins were pruned at 04:46Z
(`review/resolve_retirements_20260911T044645Z.json`).

**Cause: applied, verified on a scratch, never promoted.  He is looking at a
timeline that predates it.**  Not "never applied"; not "the plan is not read at
build"; not "overwritten".

### Reel 28 - "'work for AI' is cut off" and "the closer does not fit"

Two halves, two different outcomes.

**The closer swap DID land.**  The closer on the earlier capture (source frame
40757 of `LCATL0013.MXF`) transcribes as *"Yep, your website checks out, but
everything else out there, it's broken for sure..."* - the one he rejected.  The
closer on the live timeline (source frame 11510) transcribes as *"...it's exactly
why we've been building this platform we're calling the Lucy Visibility System.
We'd love for you to go check it out, see how AI sees you. The link's in the
bio."* - the ruled one.

**The body breath did NOT.**  The live body still ends at frame 1113, and the
word-aligned transcript of the live audio reads
`45.98-46.12 "work"  46.16-46.28 "for"  46.38-46.51 "a" | 46.52 "be"` - the cut
lands at 46.42s and **"AI" is truncated into "a"**.

The arithmetic identifies the build exactly:

| plan snapshot | body frames | closer frames | total |
|---|---|---|---|
| pre-feedback (030706Z) | 1113 | 226 | **1339** - the earlier capture's `end_frame` |
| post-feedback (032058Z) | 1124 | 243 | 1367 |
| **the live timeline** | **1113** | **243** | **1356** |

The live timeline is the **pre-feedback body with the post-feedback closer**,
because the two travel by different routes.  The closer comes from the captain's
own `<project>/external/captain_edits.json` `redraw_closer` pin, which the build
re-applies on every run (`library/tools/captain_edits.py`).  The body end lives
only in `reel_proposals_v2.json`, and no build has read the fixed copy of it.

**Cause: the same as Reel 13 - the timeline predates the plan edit.**  The closer
only *looks* fixed because that half rides a pin the build re-applies.

> **Before any promote:** the plan on disk now carries the reel-28 closer at
> `(321.610, 328.231)` = 159 frames, a mid-sentence open.  The built one is 243
> frames from 318.091.  A rebuild must be checked to confirm the pin re-widens it,
> or the closer he rejected comes back.

---

## 2. What the captain changed on Reel 13

Diffed against a deterministic reconstruction - the 04:22Z baseline scratch, built
from the same declarations - and against the earlier capture taken at 03:08Z.

**Positions, which is what he said he changed:**

| track | items | from (the rebuild) | to (live) |
|---|---|---|---|
| V1 Akshita | all 4 | `Pan 46.341 / 46.341 / 40.362 / 20.927` | `Pan -12.0` on every one |
| V4 Subtitles | all 20 tight captions | `Tilt -850.0` | `Tilt -870.0` |
| V5 Semantic | mg #1 | `Tilt 1296.0` | `Tilt 895.0` |
| V5 Semantic | mg #2 | `Pan 0.3497, Tilt 1098.667` | `Pan -104.0, Tilt 794.0` |
| V5 Semantic | mg #3 | `Pan 0.2903, Tilt 1296.0` | `Pan 0.0, Tilt 870.0` |

`-870` is **his own number** - he quoted it verbatim - and the engine's is `-850`.
Measured against an exported still, his `-870` puts the caption ink bottom at frame
row 1587 against a nominal caption row of 1589; the engine's `-850` would put it at
1577, twelve pixels high.  **His correction is closer to the declared intent than
ours was.**

**Durations, which he also said he changed:** the five closer captions start 7
frames later than the rebuild puts them (1600->1607, 1681->1688, 1724->1731,
1815->1822, 1887->1900) and the last is 13 frames shorter (16 -> 3), with its
source in-point moved with it.  That is a head trim, and it is the change I am
least able to attribute with certainty - a ripple trim and a script would both
leave this trace.

**Everything else matches the rebuild exactly:** every picture and audio clip's
record in/out, source in/out and duration on V1, V2, V3, A1 and A2, apart from the
closer tail, which differs because the rebuild carries the marker fix and the live
timeline does not.

### Change classes this method cannot see

Stated rather than reported clean.  The capture reads Resolve's
`TimelineItem.GetProperty()` dictionary in full, so audio level (`AudioVolume`),
pan, pitch, voice isolation, crops, composite mode, opacity, flip, retime flags,
clip colour, flags and per-item markers **are** covered - and all of them are at
their defaults on Reel 13, so none of those changed.  What it cannot see:

- **Keyframes.**  `GetProperty` returns one value per property.  A hand-drawn fade
  or a keyframed move reads as its value at the playhead; the curve is invisible.
- **Grades.**  No CDL, node graph or grade version is captured.
- **Fusion comp internals beyond scalar inputs.**  Tool list, `TOOLI_ImageWidth`
  and every scalar input are captured; modifiers, splines and expressions are not.
  (One trace of him IS visible there: the caption at frame 1005 carries an empty
  Fusion comp, which is what Resolve leaves behind when the Fusion page is opened
  on a clip.)
- **Anything he did and then undid.**

---

## 3. The positioning truth

### The rule, in one sentence

> **Pan and Tilt move a clip by a fraction of its OWN canvas, not of the frame -
> the shift is `value x (canvas_dimension / frame_dimension) x base_scale`, with
> `base_scale` 1 at `Scaling=1` - so an overlay already rendered full-frame is in
> position at 0 while the identical caption rendered on a 480-tall tight canvas
> needs Tilt -1740 to reach the same screen row; the Inspector number is only
> readable with the clip's own resolution beside it.**

He is right that something else is at play, and that is it.  There is no
"draw gain": the law lives once in `library/tools/resolve_transform.py` and
the factor is 1 on both axes, re-measured 2026-09-11 on 16 rendered plates
across two builds and four processes.  **This document originally recorded a
gain of 2 and a stored `Tilt -870`, and both halves of that pairing were
wrong together**: the gain came from a measurement calibrated against a
CAPTURED Pan/Tilt, and the capture it used was half the value in force.
Reel 13 stores `Tilt -1740`.

Proven on exported pixels, not on a read-back: a still exported from Reel 13 at
frame 900, correlation-scanned against the caption artefact that plays there,
locates the 840x480 canvas at frame row **1155** - exactly what the relation
predicts for `Tilt -1740` - with its ink at rows 1435..1587 against a nominal
caption row bottom of 1589 (`1920 - 320 safe-area inset - CAPTION_LIFT_PX 11`).
The full-frame caption at frame 600, stored `Tilt 0`, draws 1:1 at rows 1415..1572.
**Two Inspector numbers, one screen row.**

### 3.1 What it invalidates, and whether mixed carriage is our defect

**Mixed carriage within one reel is not, by itself, a defect.**  Reel 13 carries
both - 20 tight captions at `-870` and 7 full-frame at `0` - and every one of them
lands within about 20px of the caption row.  Two carriages reaching one position is
the rule working, not failing.

**A STALE carriage is a defect, and Reel 28 has one.**  Its 18 tight captions store
`Tilt -1700`, computed under the single-gain relation, and its 3 motion graphics
store `+2592`.  Under the measured gain of 2 those draw **407-422px below the
frame** and **~500px above it**.  A still exported from Reel 28 at frame 440 shows
**no caption at all**.  Reel 28's overlays are currently invisible.  The timing is
exact: its caption artefacts were placed at 23:28 and PR #960 corrected the gain at
23:30:07; Reel 13's were placed at 23:50.

What this invalidates:

- **Any check that compares one clip's stored Pan/Tilt against another's.**  Two
  correct clips of different carriage hold different numbers, and two clips of the
  same carriage can both be wrong together.  There is no such check in the code
  today; there must not be one tomorrow.
- **The placement read-back, on its own.**  `overlay_placement` sets Pan/Tilt,
  re-reads them off a fresh handle and passes when Resolve held what it was given.
  `-1700` reads back as `-1700`, so every gate passed the reel whose captions are
  off the bottom of the frame.  The read-back still catches the clamp and the
  entry-timeline unit conversion it was written for; it cannot see a value that is
  faithfully stored and geometrically wrong.
- **`placement_holds`.**  It bounds |Pan|/|Tilt| at the measured 3840 rail.  `-1700`
  is comfortably inside the rail and completely off the screen, so the rail is not
  a position check and was never meant to be one.

### 3.2 Can Fusion position instead?

**Yes, and it is not the right trade here.**

Feasible: the machinery exists and is in use - `library/tools/fusion/comp_builder.py`
builds comps and `library/tools/execution/apply_fusion_comps.py` imports them, and
transitions already go through Fusion exclusively.  Compositing each caption at its
final position inside a comp would mean no Edit-page transform exists at all, so
there is no stored number to be stale, and PR 899's full-frame move was reaching for
the same property by a cheaper route.

The costs, in order of weight:

1. **It takes away the thing he actually used.**  Everything he corrected on Reel 13
   he corrected by dragging in the Inspector.  A caption composited inside a comp is
   not draggable there; correcting one means opening the Fusion page per clip.  That
   is a worse tool for the person doing the work.
2. **Process isolation.**  A timeline may not be created and have `ImportFusionComp`
   called on it in the same Python process (AGENTS.md 5), so every reel build grows
   a second pass over ~27 caption clips plus motion graphics.
3. **Playback cost.**  A comp per overlay clip renders through Fusion on every
   scrub, against a plain overlay clip that does not.
4. **The reuse cache and the promote guard would both need work.**  The caption reuse
   cache restores a `placement` sidecar (`tight_box.restore_reused_placement`); with
   no placement there is nothing to restore and the sidecar contract changes.  The
   row-diff promote guard compares record positions and would survive unchanged.
   This is an assessment from the code, not a measurement - nothing was built.

And it would not have prevented this defect.  What shipped Reel 28 broken was not
the existence of a transform; it was that nothing checked what the transform drew.
That is fixed below, for a fraction of the cost, and leaves the Inspector working.

### 3.3 What was changed in the pipeline

One rule, readable in both directions, and stored values judged against intent.

- `tight_box.canvas_screen_origin` is the strict inverse of `placement_for_box`:
  where a STORED Pan/Tilt actually puts a canvas, in frame pixels.  The forward and
  inverse are now the same relation written once.
- `tight_box.ink_screen_box` turns a stored placement plus the artefact's own ink
  bbox into the frame rectangle the overlay draws.  **This is the one quantity two
  overlays of different carriage can be compared on.**
- `tight_box.verify_ink_against_intent` judges that rectangle against where the
  overlay is meant to land, with the error reported in FRAME PIXELS, and a tolerance
  (`INTENT_TOLERANCE_PX = 24`) taken from the measured 22px spread of Reel 13's own
  correct captions.  A full-frame overlay at 0 and a tight overlay at -870 both pass
  the same intent; a stale-carriage -1700 is refused however cleanly it reads back.
- `overlay_placement.apply_placement_transform` takes an optional `draw_intent` and
  checks the HELD values through it after the read-back.  An overlay without
  `draw_intent` behaves exactly as before; a `draw_intent` that cannot be read is
  REPORTED as unverified rather than skipped, because a verification that quietly
  declines to run is the gate that cannot fail (AGENTS.md 10.4).

`tests/unit/captions/test_overlay_positioning_rule.py` pins all of it against the measured
numbers, including the case a read-back cannot see.

---

## What still needs a decision

1. **Promote the verified Reel 13 and Reel 28 rebuilds** - which is the only way the
   marker fixes reach the captain - **without destroying his hand corrections.**  His
   Reel 13 transforms are captured here; `library/tools/overlay_intent.py` is the
   declared-position route that would carry them into a rebuild.  Not done: the brief
   forbids writing to Reel 13.
2. **Reel 28's overlays are invisible today** and need re-placing under the corrected
   gain.
3. **The engine's caption row is 20px above where the captain put it** (`-850` against
   his `-870`).  His is nearer the declared intent.  Whether the engine moves to match
   is his call, not ours.
