# `library.tools.marker_feedback` - the history behind its contract

This is the module docstring of `library/tools/marker_feedback.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Collect the captain's typed timeline notes out of DaVinci Resolve.

The captain reviews a built timeline inside Resolve and drops markers on
it carrying natural language: what looks wrong, what to change, what to
go and find out.  This module READS those notes and writes them somewhere
a re-render cannot reach.  It does not interpret them, does not answer
them, and has no vocabulary of marker colours - the typed text is the
signal.

── What was measured, and on what ──────────────────────────────────────

Every claim below was established against a real running DaVinci Resolve
Studio 21.0.0b.28 on macOS, on throwaway timelines (`MARKERPROBE_*`) in
the captain's own `Pipeline_Edit` project, on 2026-08-28.  This file used
to be exercised only by `unittest.mock` fakes that returned whatever the
test had just told them to, and four of its assumptions were wrong.

`hasattr` is useless on Resolve's scripting proxies - every attribute
lookup succeeds, including invented ones (`hasattr(item, 'GetTotallyMadeUpThing')`
is True; calling it raises `TypeError: 'NoneType' object is not callable`).
Nothing here guards on `hasattr`; the calls below were run and are judged
by what they returned.

MARKERS CARRY TWO PIECES OF TEXT, AND BOTH ARE TYPED BY A HUMAN.
`GetMarkers()` returns `{int frame: {"color", "duration", "name", "note",
"customData"}}`.  `name` is Resolve's Add Marker dialog **Name** field -
the one the cursor lands in - and `note` is its **Notes** field.  Reading
only `note` silently loses everything typed into Name, and the marker
still looks present on the timeline.  This project's own `Pipeline_Edit`
timeline already carries such a marker: name `Master Limiter: -1.0dBTP`,
note `Set the master track limiter to this threshold`.  Both fields are
kept verbatim here, and `MarkerNote.text` joins whichever are non-empty.

`Timeline.AddMarker` REFUSES A MARKER WITH AN EMPTY NAME, honestly.
Measured returns: name-only True, note-only **False**, both True, both
empty **False**, a frame already carrying a marker **False**.  A marker
that failed to land is not on the timeline at all - so a writer that
discards the return value reports feedback it never recorded.

TIMELINE MARKER FRAMES ARE RELATIVE TO `Timeline.GetStartFrame()`.
`TimelineItem.GetStart()` is ABSOLUTE.  On a timeline started at
01:00:00:00 (Resolve's default, `GetStartFrame() == 108000` at 30fps),
comparing a marker's key against clip bounds directly finds no clips at
any frame.  Absolute frame is `GetStartFrame() + key`.  Neither
`Timeline.AddMarker` nor `TimelineItem.AddMarker` bounds-checks the frame:
markers past the end of the timeline were accepted and read back.

`TimelineItem.GetMarkers()` IS KEYED IN SOURCE FRAMES - the same space as
`GetLeftOffset()`, not clip-relative and not timeline frames.  Proof: a
marker added to the MEDIA POOL item at source frame 150 appears, key 150
unchanged, on two timeline items cut from that file at `GetLeftOffset()`
100 and 300; a clip-relative key space would have rebased it to 50 and
dropped it respectively.  Media pool marker frames are in turn proved to
be source frames because Resolve bounds-checks them against the file:
on a 4174-frame clip, `AddMarker` returned True for 0, 4100 and 4173 and
**False** for 4174 and 9999.  So:

    timeline_frame = item.GetStart() + (key - item.GetLeftOffset())

and it is only meaningful while `left_offset <= key < left_offset + duration`.
The old `start + max(0, key - left_offset)` clamp put every out-of-range
marker on the clip's first frame, inventing a position for it.

`GetLeftOffset()` IS REAL and returns the source in-point (100 and 500
for clips appended with `startFrame` 100 and 500).  The `hasattr` guard
that used to sit around it was a no-op, but the call itself is sound.

A TIMELINE ITEM'S MARKER SET IS ITS OWN, SEEDED FROM THE MEDIA POOL AT
PLACEMENT TIME.  A pool marker that exists when a clip is placed is COPIED
onto that timeline item - onto every item cut from the file, including
ones that never play the frame it sits on: the clip playing source
300..399 reported the pool's marker at source 150.  A pool marker added
AFTER placement reaches no existing item, and deleting the pool's markers
does not remove the copies.  Neither do the copies share sideways: a
marker added to one item did not appear on a sibling, on the pool item,
or on an item on another timeline, and `DeleteMarkerAtFrame` on a sibling
returned False.  So a pool marker is collected once per FILE here, mapped
onto every placement whose played range contains it, and an item's copy
of it is not reported again - unless its TEXT has since diverged from the
pool's, in which case both are real and both are kept.

`TimelineItem.GetProperty("Comments")` IS ALWAYS None.  A timeline item's
property dictionary (read with no argument, per AGENTS.md 5) holds 26
transform keys - Pan, Tilt, ZoomX, CropLeft, Opacity and so on - and no
"Comments".  Setting a comment on the media pool item and reading it back
through the timeline item still returned None.  Clip comments are a MEDIA
POOL property and are read here as
`item.GetMediaPoolItem().GetClipProperty("Comments")`.

Verified working as expected: `Timeline.GetMarkers`, `Timeline.AddMarker`,
`Timeline.DeleteMarkerAtFrame` (True for a present frame, False for an
absent one), `Timeline.GetMarkerByCustomData`, `TimelineItem.GetMarkers`,
`TimelineItem.GetStart`/`GetEnd` (end is EXCLUSIVE: a clip at 0 with
duration 99 reports GetEnd 99 and the next clip GetStart 99),
`GetDuration`, `GetLeftOffset`, `GetSourceStartFrame`, `GetMediaPoolItem`,
`GetClipColor`/`SetClipColor`, `GetFlagList`/`AddFlag`,
`MediaPoolItem.GetClipProperty("File Path")`.

── What this module does NOT do ────────────────────────────────────────

No colour vocabulary.  The captain chose typed notes over colour codes,
so colour is recorded as data and read by nothing.  No acknowledgement
marker is written back and no marker is deleted: this module is a reader
plus a durable writer to disk, and the timeline is the captain's.


Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 15
keeps the headline and points here.

The captain reviews a built timeline **inside DaVinci Resolve** and drops markers on it carrying
natural language - what looks wrong, what to change, what to go and find out.
One enumeration, `library/tools/marker_feedback.py`, which reads them and writes them to disk.
It is proved against a real running Resolve by `tests/test_marker_feedback_against_resolve.py`;
its recorded per-call findings are in the module docstring, in the shape `neural_engine.py` uses.
- **A MARKER CARRIES TWO PIECES OF TYPED TEXT AND BOTH ARE READ.** `GetMarkers()` returns `name`
  (the Add Marker dialog's **Name** field, where the cursor lands) and `note` (its **Notes**
  field). Reading only `note` loses everything typed into Name, silently, with the marker still
  on the timeline. Both are kept verbatim; nothing is summarised, truncated or normalised.
  `Timeline.AddMarker` REFUSES a marker whose name is empty.
- **A timeline marker's frame is relative to `Timeline.GetStartFrame()`; `TimelineItem.GetStart()`
  is absolute.** A clip marker's frame is a SOURCE frame, the same space as `GetLeftOffset()`, so
  `timeline_frame = item.GetStart() + (key - item.GetLeftOffset())` and only inside the range the
  clip plays. Resolve bounds-checks neither. A key outside that range is kept UNPLACED with the
  reason, never clamped to the clip's head.
- **`TimelineItem.GetProperty("Comments")` is always None.** Clip comments are a MEDIA POOL
  property. A timeline item's property dict holds transform keys only - read it with no argument
  (§5) before trusting a name.
- **The record goes to `<project>/marker_feedback/`, and that is why `Kind.CAPTURED` exists.**
  Everything under `pipeline_output/` is `Kind.OUTPUT` - safe to delete because a re-run
- **The build path REFUSES to delete a timeline carrying uncollected notes.** `guard_timeline_deletion` fails the build, naming the notes. `PIPELINE_DISCARD_TIMELINE_MARKERS=1` is the override.
- **The reader READS, and the two things that write to a marker write only `customData`** -
  the capture button and the decision stamp. Neither creates a marker, touches `name`/`note`/colour/duration, or deletes one.
  No colour vocabulary; no acknowledgement marker is written back.
```
