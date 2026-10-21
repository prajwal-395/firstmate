# Undo and redo, by edit type

**What this is:** what `ren undo` and `ren redo` actually restore, per KIND
of edit, and the two ways they used to fail to restore anything at all.

**Date:** 2026-10-22. **Engine:** `video-editing-pilot` on branch
`fm/p-vc006-undo-redo` (base `26434ee2e`).

**The lane:** VC-006 of the captain's development punchlist - "Make undo and
redo reliable across different edit types. Verify restoration for structural
changes, source replacements, graphics, grades, and audio. Use an inverse
where proven; otherwise restore a verified archive. Refuse restoration when
required dependencies are missing. Keep the recovery mechanism independent
from the agent's memory of its earlier plan."

## The two routes an undo can take

The engine has two ways back from an edit, and they are not the same thing.

| route | what it reverses | where the state comes from |
|---|---|---|
| **the journal, in place** | a touch-up on a built reel's own timeline | the journal's own BEFORE and AFTER reads of that timeline (`library/tools/undo_journal.py`) |
| **the version archive** | a rebuild | the recorded version's own verified recovery archive (`ren undo --restore-version`, PR 3079) |

A third route exists for AXI edits committed as an `EditPatch`: its inverse
is derived from the patch's own recorded base snapshot
(`edit_patch.inverse_patch`), and it covers **marker and local clip-property
writes only** - anything else is refused by name, with "use semantic replay or
a verified native recovery archive" as its next action. That refusal is
correct behaviour, not a defect: an inverse nobody proved is not a restore.

## The measurement

**Where it was measured.** The real touch path
(`reel_touchup.apply_touchup`: qualification, staging, composition, read-back)
and the real journal (`open_entry` -> `close_entry` -> `undo_in_place` ->
`redo_touch`) against the repo's Resolve double
(`tests/composed_edit_harness.py`) - the same double every offline edit test
uses, and the only Resolve an offline test is allowed to touch. Every project
in the measurement is under `tmp_path`.

**Live measurement on a Ren-owned scratch copy** was attempted and did not
complete. Two Ren-owned scratch copies were used, never the captain's
project, timeline or media: `ren-scratch-breaktest` (Resolve project
`Ren Scratch - breaktest`, a Ren-owned copy of a real reel) and
`ren-scratch-bench-demo2` (Resolve project `Ren Scratch - vep-bench-demo2`,
also a Ren-owned copy). Both were opened through the sanctioned route
(`resolve-axi project scratch open`, which records the origin and restores
it afterwards) and the live numbers are what they are:

- The machine's ONE serving broker had seven mutation jobs queued ahead of
  the touch and did not grant the lease inside the client's budget (three
  attempts; `ren resolved list` shows the queue). The lease is the
  machine's, and the brief forbids asking for a quiet window, so the run
  waited and then reported the queue.
- With the lease granted, the touch on `Ren Scratch - breaktest` refused
  for a reason of the engine's own: "a staging container 'Reel 13 -
  breaktest-selected-dc7fa3f8 (rebuild staging)' from an interrupted run is
  still in the project" - that copy's staging debris is another lane's, and
  the engine refuses to reuse it (correctly: reusing it would grade one
  run's content as another's).
- On `Ren Scratch - vep-bench-demo2` the touch RAN and the composed edit
  REFUSED the placement it had just made. The trim asked for the source
  window `[46665, 47431]` and the live track read `[46664, 47431]`:

  ```
  PlacementNotVerified: REFUSING to restore: 1 of 1 placed item(s) are
  not on the track at the frame and length they were asked for.
  Resolve's append returned a live-looking handle and the track
  disagrees, so the track wins: [{'row': 'V1', 'record_frame': 129,
  'wanted_source': [46665, 47431], 'wanted_record': [129, 895],
  'found': [{'start': 129, 'end': 895, 'duration': 766,
  'source_start': 46664, 'source_end': 47431}]}]
  ```

  The record span and the duration are exactly what was asked; the source
  window is one frame short at the head, and the placement verification
  demands equality. So a TRIM cannot be APPLIED on live Resolve today, and
  its undo and redo are therefore unmeasured live - not "measured as
  broken", but "the edit itself does not land".

**What "exact" means.** The timeline is read again after the undo and
compared with the read taken before the touch, on the projection the journal
verifies with (`undo_journal.item_projection`: row, played span, source trim,
file, name, transform, comp count, comp windows, clip colour, grade,
enabled state). The redo is read the same way against the read the touch left.

## The per-type table

Seven types, as the punchlist names them. "Before" is what the engine did
before this change; "after" is what it does now.

| # | type | the edit | the undo route | before | after |
|---|---|---|---|---|---|
| 1 | structural change | `move` - a card to another frame | journal, in place | EXACT | EXACT |
| 2 | delete | `remove_overlay` - a card off the timeline | journal, in place | EXACT | EXACT |
| 3 | **trim (a source window)** | `slip` - the same item plays other source frames | journal, in place | **REFUSED - there was no way back at all** | **EXACT** (offline). Live: the trim does not land at all - the composed edit refuses the placement on a one-frame source-window disagreement |
| 4 | source replacement | `swap_pixels` - a card's media replaced | journal, in place | EXACT | EXACT |
| 5 | graphics | `set_properties` - a transform on an overlay | journal, in place | EXACT | EXACT |
| 6 | **grade** | an in-place CDL on an item | journal, in place | **the grade was compared but never written back - the undo ended in a failure with the grade still on the item** | **the grade is written back and read back; a grade the journal holds no record of refuses before any write** |
| 7 | audio gain | an item's audio level | journal, in place | UNMEASURED - the double does not carry an audio level, and the live run did not get the lease | UNMEASURED |
| - | caption text | the rendered card's file swapped onto its placement | the same mechanism as #4 (a file swap on a placed item) | UNMEASURED live; the mechanism is measured at #4 | UNMEASURED live |
| - | overlay add | a new graphic placed | journal, in place | UNMEASURED - the double places nothing on the row the gate allows | UNMEASURED |
| - | a look change | the series look | the version archive (a rebuild) | not measured here; the archive route is PR 3079's | not measured here |

**Denominators, never combined.** Measured EXACT: 4 of 4 rows the double can
carry (1, 2, 4, 5) plus row 3 after the fix and row 6's write-back. Measured
REFUSED and now fixed: 1 (row 3). Measured and found broken, now fixed: 1
(row 6). UNMEASURED: 3 rows (7, caption text, overlay add). The live
measurement of all seven: 0 of 7.

## The two defects, and what each one cost

### 1. A trim left NO way back - not a wrong restore, no restore at all

**What happened.** The journal classifies an accepted touch's operations so
a rebuild can replay them (`accepted_edit_record`). The classification was a
second list, kept by hand beside the touch gate, and it had drifted: every
operation added to the gate after the list was written - the source-window
edits (`slip`, `roll`), the structural ones (`split`, `slide`), the audio
fade, the overlay position - was missing from it.

A touch carrying one of those operations was **PROMOTED to the live
timeline** and then the journal could not close: the classification raised,
`close_entry` propagated it, and the entry stayed `open`. So the change was
on the reel, no version was recorded, `ren undo` refused ("is 'open', not
applied"), and the retired generation stayed as the only way back.

Underneath that was a second defect: even with the journal closed, the undo
read a TRIMMED item as one item removed and another added, and then refused
("the touch took V1@1069 and the journal holds no capture of it"). The plan
pairs items by their source trim, and a trim is exactly the change of the
source trim.

**The rule.** A touch that has LANDED must leave a way back. The operation
vocabulary is ONE enumeration - the gate's own - so a new operation is
classified the day it is written, and a classification the engine cannot make
is recorded, by name, on the journal instead of stopping the journal from
closing.

**The fix.**
- `library/tools/reel_touchup.py`: `TOUCH_OPS` - the touch vocabulary, read
  by the journal instead of a second list.
- `library/tools/undo_journal.py`: `accepted_touch_override_ops()` reads it;
  `close_entry` records an unclassifiable operation as
  `accepted_edit_refusal` and closes the journal anyway.
- `library/tools/undo_journal.py`: `plan_inverse` pairs a trimmed item with
  its own before-state - same row, same file, same name, same played span -
  and refuses when that pairing is not unique.

**Before and after, on the double.** The trim's undo went from
`UndoRefused: accepted touch ... contains unclassified operation 'slip'` (the
journal never closed) to `EXACT`; with the journal closed but the pairing
unchanged it was `UndoRefused: the touch took V1@1069 and the journal holds
no capture of it`. Both steps were needed: the first makes the touch
undoable, the second makes the undo exact.

### 2. The exact project lookup refused a project that was OPEN

**What happened.** Measured live on 2026-10-22, on the Ren-owned scratch
copy: `ren touch ren-scratch-breaktest 13` refused with

```
No project named exactly 'Ren Scratch - breaktest' in the current folder.
Resolve lists ['linkedin', 'LMC Interview', ...]. Refusing rather than
guessing: a near match here lands on somebody else's project.
```

for a project that was open, and that the refusal itself names. The cause,
measured live in the same session: **`GetCurrentProject()` puts the Project
Manager's current folder back to the root**, so the folder listing read
after it is the ROOT's projects - and the project that is open reads as
absent. The touch and the undo both address the project this way, so both
refused.

**The rule.** The exact-name lookup answers for the project that is OPEN,
not only for the one the Project Manager's folder view happens to be
showing. The name is exact either way, so the near-match protection - the
rule that keeps a near name off somebody else's project - is untouched.

**The fix.** `library/tools/timeline_ingest.py`: `resolve_project_exactly`
asks what is open before it refuses.

## The live one-frame placement refusal, and what it needs

**What was measured.** On the Ren-owned scratch copy `Ren Scratch -
vep-bench-demo2`, reel 13, a slip of +12 frames on `V1[1]`. The composed
edit re-placed the item and then refused it: the wanted source window was
`[46665, 47431]`, the live track read `[46664, 47431]`, and the record span
and duration were exactly right. One frame, at the head of the source
window.

**Why it is not fixed here.** Two readings fit the numbers, and they want
opposite fixes:

1. the placement is exact and Resolve's read of the source start is one
   frame early - then the VERIFICATION is wrong, because it demands a
   read-equal window and no computed window can ever be read-equal;
2. the slip route derives its new window from a READ that is itself one
   frame off, so the slip lands one frame short of the delta it asked for -
   then the SLIP is wrong, and loosening the verification would ship a
   one-frame-short trim.

`library/tools/resolve_temporal.py` already carries the engine's position
on this ("`GetSourceEndFrame` is retained verbatim by timeline_ingest for
audit, since it can disagree with played duration by one frame", with
`played_source_span` for the callers that need the played interval), and
the placement verification does NOT use it. Settling which reading is right
needs a live measurement of the source-start convention on a clip whose
window is known exactly - a lane with a Resolve and the time to measure it,
not a change made on one observation.

**What it blocks.** The trim type's live undo and redo. Everything the
trim does OFFLINE is exact after the fix above; the live gap is in the
edit, before the undo is ever asked for.

## What was left undone

- The live measurement of all seven types on a Ren-owned scratch copy: the
  machine's serving broker did not grant the lease inside the client's
  budget for three attempts, and the trim's own placement is refused on
  live Resolve by a one-frame source-window disagreement (measured above,
  with the numbers). The offline measurement stands; the live numbers are
  unmeasured, not zero.
- The audio gain, the overlay add and the caption text rows are UNMEASURED
  for the same reason, and the double cannot carry the first or the third.
- The EditPatch route's inverse is still refused for every operation outside
  marker and property writes, and the direct-transaction route still refuses
  every operation (`RECOVERY_QUALIFICATIONS` is empty by design). Both
  refusals are correct: neither names a proven inverse.
- The `look` operation is a planner delivery with no timeline inverse; its
  undo is the version archive, measured by PR 3079 and not re-measured here.
