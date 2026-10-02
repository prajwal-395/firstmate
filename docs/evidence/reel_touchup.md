# `library.tools.reel_touchup` - the history behind its contract

This is the module docstring of `library/tools/reel_touchup.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
A change to the timeline in front of you, instead of a rebuild of it.

The only path the captain could reach staged a fresh timeline and
promoted it over the old one (`manage_project.py build-reels`).  There
was no verb that changed the timeline they were looking at - while
`library/tools/composed_edit.py` sat beside it: a complete, tested,
pixel-verified in-place editor with zero production callers.  This
module is the path that connects the two.

What the caller states, and what it does not
--------------------------------------------
The caller states the change STRUCTURALLY: which reel, which item,
what changes - one of the nine ops below.  Mapping a captain's
natural-language note onto such a change is the NEXT task and is
explicitly not this one; this module is the mechanism that task will
call.

The nine ops
------------
The first five delete and re-place through the composition; `add_row`
makes a declared row; `set_enabled`, `set_properties` and
`entry_motion` are IN-PLACE: they write onto the staged item itself - no delete,
no place - and ride `Qualification.in_place` rather than
changes/insertions/removals.  They run before the composition off the
same staging, so a capture taken after them carries them, and a spec
mixing them with composition ops stays servable: one source item, one
edit (`_check_single_claim`).

- `move` - an overlay item to a different record position on the SAME
  row.  Same duration, same pixels.  A move across rows refuses: the
  composition addresses what it deletes by (row, position), so a
  cross-row plan would capture whatever sits at that position on the
  target row.  State that as `remove_overlay` plus `add_overlay`.
  The source row must be an overlay row (V3+ video): vacating V1, V2
  or any audio row refuses, because that leaves black or silence in
  continuous program and stating the covering change is the caller's
  job, not the gate's guess.
- `swap_pixels` - an overlay item's pixels for a re-rendered file at
  the same span.  The replaced item must carry no drawing Fusion comp
  (there is nothing to carry a treatment across a pool-item swap) and
  no colour grade beyond the default single node (an `Insertion`
  cannot take a grade from an item about to be deleted).  Its
  transform properties are CARRIED from its own live read and declared
  in the receipt - carried, never invented (AGENTS.md 10.5) - unless
  the edit declares `properties` for a file cut to a different canvas.
  Its source trim is carried too, unless the edit explicitly declares
  `left_offset`: rendered overlays can include head handles, and
  replacing a trimmed item with one starting at frame 0 exposes them.
- `add_overlay` - a new overlay item at a stated record frame, on an
  OVERLAY row (V3+).  Adding to a comp-bearing row (V1/V2,
  `FUSION_COMP_TRACKS`) refuses: every clip there carries a
  treatment comp and a newly placed item has no manifest spec for
  the pass to key one to, so it would land untreated beside treated
  neighbours.  `properties` is REQUIRED: a placed item comes back at
  identity, so an undeclared treatment renders a framing nobody chose
  (`composed_edit.InsertionUndeclared`).
- `remove_overlay` - an overlay item off its row, same source-row rule
  as `move`.  `composed_edit` only deletes what it re-places, so the
  target is pre-deleted in one call on the staging copy (which places
  nothing and therefore cannot collide) while the row's kept items
  ride the composition as zero-length rewrites.
- `retime` - a played-length change with a ripple, planned by
  `composed_edit.plan_ripple` over the full read.  This is the class
  that pays the comp pass.
- `set_properties` - IN-PLACE.  A property mapping written onto an
  already-placed item with `composed_edit.set_properties` and judged
  by read-back - no delete, no place.  Any row: nothing is vacated
  and no comp is disturbed.  A key `set_properties` would silently
  skip (read-only, None, a `<placeholder>`) REFUSES here instead,
  naming it: a spec asking to set `Resolution` is a caller error,
  not a no-op to wave through.
- `add_row` - a new NAMED video row on top of the stack, made on the
  staging copy and read back; later edits in the same spec may place
  onto it. A reel already carrying a row of that name refuses.
- `set_enabled` - IN-PLACE. An overlay item (V3+) switched on or off;
  it keeps its span, file and treatment, so disabling a graphic never
  deletes it.
- `entry_motion` - IN-PLACE.  An entrance and/or exit fade authored
  as a Fusion comp (`fusion.comp_builder.build_effect_comp` over the
  `fade_in_frames`/`fade_out_frames` keys, the same dispatch the comp
  pass reads) and imported onto the staged item, then conformed by
  the pass's own `comp_media_window.conform_item` and verified by
  re-read.  Overlay rows (V3+ video) only: V1/V2 are comp-bearing
  rows whose treatments the comp pass owns (same boundary as
  `add_overlay`), audio rows carry no Fusion comps, and an item
  already carrying a drawing comp refuses - a second treatment the
  recorded manifest does not know would be dropped silently by the
  next re-derivation.  The ramp must fit inside what the item plays
  (`fade_in + fade_out <= duration - 1`), else the effect holds
  across the whole clip (`fusion.played_window`).

The qualification gate
----------------------
`qualify` classifies a change before anything is staged, into:

- `composed` - no clip's played length changes.  No comp is
  re-derived and no comp pass runs.  What this costs against a
  rebuild is MEASURED, not quoted: the composition mechanism holds
  at ~2s (delete 0.01-0.14s, place 0.25-0.57s, restore 0.03-4.09s)
  but staging (copy + conform) measured 8.9-25.9s and a rebuild of
  the same edit measured 19.4-67.1s (`docs/RULE_EVIDENCE.md`,
  "What it costs, and it is not the spike's figure").  Both cases
  measured there were length-CHANGING, so the no-length-change
  class - this one - had never been measured at all until this
  module's own live measurement.  The gate routes it; the numbers
  say whether it was worth routing.
- `composed_with_rederivation` - a played length changes.  Routed
  through the same composition, but ONLY with the real comp pass
  (`ReelLookRederiver` over the recorded fusion manifest) and ONLY
  with its cost said out loud: the comp pass is 17.0-63.7s of
  fixed overhead, most of what a rebuild costs, and on the two
  measured length-changing cases the composed totals (44.1s,
  54.9s) were SLOWER than or level with the rebuilds (24.0s,
  59.2s).  Never presented as a quick refresh.
- refusal - anything the gate cannot classify.  No guess, and no
  silent fallback to a full rebuild: a silent fallback is exactly the
  behaviour this module exists to remove.

The `_NullRederiver` is not a bypass.  It is constructed ONLY for the
`composed` class, its `reachable_reason` refuses any played-length
change defensively, and its `rederive` returns a receipt that says
`skipped` with why.  The verdict that counts is still state:
`assert_rederived` checks every length-changed comp item and every
comp-expecting insertion off the live timeline, and with none present
there is nothing whose comp could be stale.  The four structural
refusals in `composed_edit` are untouched -
`tests/test_composed_edit_refusal.py` attempts the bypass eight ways
and must keep passing.

"Staged", not "in place on the captain's timeline"
--------------------------------------------------
The captain ruled 2026-09-09: build beside them.  "In place" here
means "onto the existing built reel rather than a from-scratch
rebuild", NOT onto whatever the captain is reviewing without a copy.
`apply_touchup` duplicates the approved timeline into
`reel_build.staging_name` (the same staging the build uses, so the
bin layout and the stale-debris refusal both recognise it), conforms
it against the source, edits the copy, verifies by re-reading the
track, and only then swaps the names - with the replace guard, the
sign-off check, marker carry, the undo journal and the carried
signature close, mirroring `promote_staged_reels` phases 0-2.

The way back is the JOURNAL, not a copy (captain, D5, 2026-09-23:
"in-place for touches"). `undo_journal.open_entry` reads the approved
timeline before anything is staged; `close_entry` reads the promoted
one before the replaced generation is deleted; `ren undo` reverses the
touch in place from the two. A removal of a GRADED item refuses here,
like a graded swap: no script can record a grade, so the journal could
not put it back.

Grades ride from the APPROVED timeline: a re-placed item comes back
with one colour node where it had eight, so every re-placed change
carries its grade from the live item on the untouched reel
(`_grade_sources_for`, resolved by source row + pre-edit record
frame and judged by read-back).

On `reel_rebuild_need`'s "on this reel the composed path is not
faster"
-----------------------------------------------
Answered, not a contradiction: the ~2s is the mechanism alone,
and the totals include staging (8.9-25.9s) and re-derivation
(17.8-37.4s).  Both cases measured in `docs/RULE_EVIDENCE.md` were
length-changing, so both paid step 7.  The gate's design rests on
exactly that reading: no-length-change edits skip the comp pass,
and length-changing edits pay it and say so.  Whether skipping the
comp pass is enough to beat a rebuild - staging alone can cost more
than a whole rebuild's best case - is what the live measurement
under `apply_touchup` decides, per edit, in the receipt.  If the
composed path loses on the no-length-change class too, that is
reported as a measured non-finding, not shipped as a speed
improvement.

On whether a pixel swap avoids delete-and-place
------------------------------------------------
It does not.  Resolve has no `SetMediaPoolItem`: changing what an
item plays means deleting it and placing a new one.  So a swap is the
full seven steps - delete-all, place-all, verify by re-read,
restore - and "cheap" for swaps means "no comp pass", never "no
delete" and never a ratio against a rebuild quoted from the old
spike figure.
```

## What `tests/test_reel_touchup.py` pins

Moved from the test module's docstring (2026-10-02).

- the five ops qualify to the right class: move / swap_pixels / add_overlay /
  remove_overlay are `composed`; retime is `composed_with_rederivation`;
- two structural exclusions: adds to a comp-bearing row (V1/V2) refuse, and a
  move across rows refuses - both name what to state instead, before anything
  is staged;
- anything unclassifiable refuses with its reason: unknown ops, vacating
  continuous rows, undeclared treatments, collisions, graded swaps,
  comp-carrying swaps, manifest mismatches;
- every producer of a refused comp is accounted for: a drawing comp refuses
  the swap, Resolve's own empty auto composition still qualifies, an
  unreadable graph refuses fail-closed;
- grades ride from the approved timeline: a graded retime or move keeps its
  nodes through the composition;
- the `composed` class runs through `apply_composed_edit` with the null
  rederiver and verifies by re-reading the track.


## `tests/test_ren_entry_motion_and_property_ops.py` - design history (moved 2026-10-02)

```text
Ren in the 1326/1327 shape: entry-motion and property-set operations.

The absorbed row (`vep-build-the-entry-motion-and-property-op`) asked
which half of the system owns small changes - the reel half
(touch-reel) or the main-edit half (region operations).  The rebuild
answers it by mechanism rather than by picking a half: an operation
declared in the effect vocabulary is reachable through compose
regardless of which half implements it.  Both operations below are
owned by `build_reels` and route through the touchup's own
stage-conform-write-verify-promote path - no new bespoke path, no
command-line verb, no composer branch.

What each one is:

- `reel.entry_motion` animates a placed overlay element in (and out)
  with an authored Fusion fade (`fusion.comp_builder` over the
  `fade_in_frames`/`fade_out_frames` keys, the dispatch the comp pass
  reads), imported onto the staged item and conformed by the pass's
  own `comp_media_window.conform_item` - without rebuilding the reel
  that carries it.
- `reel.set_properties` writes a property mapping onto an
  already-placed clip with `composed_edit.set_properties`, judged by
  read-back - without deleting and re-placing it.

The 1327 pattern, and nothing else: each declares its `Operation`
under the owning step, its effect DERIVES from the requirement
vocabulary (owning node `build_reels`, so the same effect its
siblings `reel.build` and `reel.touchup` carry), each has a real step
body, the SKILL.md is regenerated from the registry, and the tests
assert registration, vocabulary membership and reachability through
compose.

The finding this shape produces rather than bends around: both
operations land on an effect that already has a route
(`state.verify_reels.reel_build`, whose representative stays
`reel.build`).  Their declarations are NOT bent to steer which route
compose picks - selection between equivalent routes is the open
problem `vep-ren-two-routes-one-goal-no-basis-to-choose` owns, and
`test_the_representative_stays_the_rebuild` pins that this lane does
not pre-empt it.
```
