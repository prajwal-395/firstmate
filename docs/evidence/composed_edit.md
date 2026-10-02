# `library.tools.composed_edit` - the history behind its contract

This is the module docstring of `library/tools/composed_edit.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
An edit Resolve has no verb for, composed out of the verbs it has.

Resolve's scripting API offers `MediaPool.AppendToTimeline` (place) and
`Timeline.DeleteClips` (delete) and nothing else for picture: no trim,
no move, no ripple, no `SetMediaPoolItem`.  Changing an item therefore
means deleting it and placing a new one, and the new one is a **new
object** carrying none of the old one's state.  What that costs against
a rebuild is MEASURED, not quoted: the composition mechanism alone
(delete, place, restore) holds at ~2 s, but staging (copy + conform)
measured 8.9-25.9 s and step 7 re-derivation 17.8-37.4 s, for composed
totals of 44.1 s and 54.9 s against rebuilds of 24.0 s and 59.2 s on
the two measured length-changing cases (`docs/RULE_EVIDENCE.md`,
"What it costs, and it is not the spike's figure").  The spike's
~112 s rebuild figure predates both the re-derivation condition below
and the PR 1217 lease split, and is not used as a comparison anywhere
in this module.

This module is that composition, and it is **seven steps**.  Each one
exists because something measurably failed without it (spike report
`vep-work-around-the-api-not-give-up-on-it`, 2026-09-11/12, every claim
executed against Resolve Studio 21.1 and measured on exported lossless
PNG RGB8):

  1. **Stage.**  Never edit an approved reel in place.  Work on a copy
     and `conform_comp_windows` it against the source first - a copy's
     `MediaIn` windows are NORMALISED by the copy (`comp_media_window`),
     so a copy is not automatically a faithful control.
  2. **Plan by arithmetic over a full read** (`plan_ripple`).  An item
     that straddles the cut is EXTENDED; one that starts at or after it
     is SHIFTED.  Every row, including audio.  Never a list of names:
     the spike's predecessor left V4 out of its plan and measured
     "13 frames of caption desync" as an API defect.
  3. **Capture** (`capture_item`) every item that changes.
  4. **Delete everything that changes, in ONE call, before placing
     anything** (`delete_all`).  This is the whole defence against the
     silent drop: `AppendToTimeline` will not place over a live item,
     returns a truthy list of zombie handles, and places nothing.
  5. **Place in ONE call, in increasing record order** (`place_all`),
     with `endFrame = left + duration` - `endFrame` is EXCLUSIVE, and
     passing `left + duration - 1` yields an item one frame short.
  6. **Verify by re-reading the track** (`verify_placement`), never by
     the return value.  The return value of a colliding append carries
     no information at all.
  7. **Restore** (`restore_item`): the 32 transform properties, the
     Fusion comp, **the comp's media window**, the colour grade
     (`CopyGrades`) and the A/V link - then **re-derive** every comp on
     a clip whose PLAYED LENGTH changed, through the builder
     (`rederive_comps`).

── Step 7's second half, and why this module withholds an artefact ────

**A per-clip Fusion comp is keyed to the window of footage the item
plays, so a trim invalidates it.**  Reel 01's head clip carries a
push-in as a 480-key `BezierSpline` over comp frames 0..479 - and
`fusion/played_window.py` states the law the comp is written under:
comp frame 0 is the clip's FIRST PLAYED FRAME.  Extend the item to 492
frames and the captured spline finishes 13 frames early and holds.
Measured: restoring the captured comp verbatim across that trim is
wrong on **489 of 492 frames** (mean 1.09/255, max 88) on a
cross-render floor of exactly 0.0000.  Nothing in the timeline's
readable state says so.  It looks right.

So capture-and-restore is the WRONG MODEL for the comp, and the captain
attached that as a condition: a clip whose played length changes must
have its comp **re-derived through the builder**, never restored from
the capture, and a composed edit that changes a played length and
cannot reach the comp generator must **REFUSE**, not restore the old
comp.

This module makes that refusal structural rather than conventional, in
four places, and the honest boundary of "structural" is named at the
end:

  a. `ItemCapture.__post_init__` REFUSES to hold a restorable comp for
     a change whose played length differs.  A hand-built capture that
     claims one raises `CompRestoreRefused` at construction.
  b. `capture_item` never exports such a comp to the restore directory
     at all.  It goes to `withheld_dir` instead, under a name the
     restore path never reads, so there is no artefact for the restore
     to find.  Diagnosis keeps it; the restore cannot reach it.
  c. `apply_composed_edit` calls `assert_rederivation_reachable` BEFORE
     step 3 - before anything is captured and long before anything is
     deleted - so an edit with no route to the comp generator refuses
     while the timeline is still whole.
  d. After the pass, `assert_rederived` re-reads every length-changed
     item and raises unless it carries a comp whose media window covers
     its NEW played length.  A generator that declined, crashed or
     never reached the clip is caught by state, not by its own report.

**Where "structural" stops.**  Inside this module a composed edit
cannot restore a stale comp: the data model will not hold one, the
artefact is not written where the restore looks, and the orchestrator
refuses before it destroys anything.  What no module can prevent is a
caller that does not use this module - a fresh script holding Resolve
handles can always call `ImportFusionComp` itself.  That is the same
boundary AGENTS.md 15 draws around `reel_read` ("do not write a new
probe"), and it is enforced the same way: by a test that fails when
library code reaches for the destructive call outside the modules that
own it (`tests/test_composed_edit_refusal.py`).

── What a composed edit cannot carry across ───────────────────────────

- `GetUniqueId()` cannot survive: a re-placed item is a new object.  Any
  record keyed by timeline-item id is invalidated by a composed edit.
- The link GROUP is reconstructed from (track, record frame), not
  restored: `GetLinkedItems()` returns opaque handles.
- The composition is NOT atomic.  Between step 4 and step 5 the
  timeline is missing every item that changes.  That is why step 1 is
  not optional: the edit runs on a staging copy and the approved reel
  is only ever replaced through `reel_replace_guard`.

Resolve is a single instance and other lanes drive it.  This module
holds no Resolve import: it takes live handles as arguments, so it is
driven under test by a fake (`tests/test_composed_edit.py`) and under
`AGENTS.md 5`'s process rule by whatever opened the project.
```

## `tests/test_cut_in_anchored_window.py` (moved from its module docstring, 2026-10-02)

Comps key to the anchored span (finding 36).

4.03 resolved a `cut_in` anchored to the word 'quit' (0.2-7.185 s),
but the build drew one constant-zoom comp over the whole hook item
(0-216) at 1.15 - the exported frame at 0.07 s is already punched.
The resolved sub-block anchor reached per_clip as params but the
comp keyed its keyframes to the whole played item, ignoring it.

The fix threads the window through: compile_manifest records the
entry's absolute timeline span as `effect_window`, the applicator
turns it into comp frames (`effect_window_frames`), and `fx.zoom`
keys inside the window while holding neutral (1.0) outside it. A
window covering the whole played range keys exactly as before.

These tests read the serialized comp's own splines - the bytes
Resolve holds - never the plan. No Resolve writes.
