# `library.tools.marker_resolution` - the history behind its contract

This is the module docstring of `library/tools/marker_resolution.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Resolve the captain's typed timeline notes, and clear only what is proven.

`marker_feedback.py` READS the captain's notes off a Resolve timeline and
deliberately does not answer them. `marker_routing.py` routes each note to
the step that owns the decision it is about, and reports an ambiguous one
as ambiguous rather than guessing. This module is the other end: recording
that a note was ANSWERED, and clearing its marker - but only from evidence.

The captain's rule (2026-09-09): "if you resolve and verify that you have
addressed a marker in which i gave you feedback, then remove the marker to
clean up the timeline when you are done."

The three words that make this hard are "resolve AND VERIFY". A marker the
captain left is their statement that something was wrong. Removing it is a
claim that it no longer is, and a marker cleared on a worker's say-so is
worse than no cleanup: it erases the captain's own words and their record
that something was wrong, on an assertion. So:

* Every note gets a durable RESOLUTION RECORD holding the captain's
  original text verbatim, what was done, and the evidence. It lives in
  `<project>/marker_feedback/resolutions/`, beside the pull files - in the
  `Kind.CAPTURED` area a re-render cannot reach, so a rebuild neither
  erases it nor needs it.
* A marker is removed ONLY when a deterministic check proves the fix.
  A declined note is acknowledged, never addressed, and NEVER clears its
  marker. A note with no mechanical proof available is recorded as
  unverifiable and its marker stays.
* The captain's words are written to the record BEFORE the marker is
  removed. If removal fails, the record still stands and says the marker
  is still there.

The shape follows what this repo already established: PR 808's skills
contract (receipts on disk are the read-back; a self-reported check writes
no receipt and cannot pass), PR 834's `treatment_verify` (a deterministic
half carries the verdict; the model's reading is recorded rather than
enforced, AGENTS.md 10.4), and `run_pipeline`'s `note_acknowledgements`
(a reasoned DECLINE is a valid acknowledgement - of an acknowledgement,
not of a fix).

── What counts as evidence ──────────────────────────────────────────

`CHECKS` is the whole vocabulary of deterministic verification, one entry
per check. A check reads a MEASUREMENT - a plain dict counted off the
rebuilt timeline - never the worker's assertion that the fix worked. A
check that cannot fail is refused here the same way a gate that cannot
fail is refused in AGENTS.md 10.4: each check below names what failing
input looks like, and the tests prove it fails on one.

Two checks are wired, because two structural facts about a built timeline
can be counted mechanically:

* `a_roll_two_rows` - the rebuilt timeline carries two a-roll video rows.
  Fails on one row, on zero, and on a measurement that never counted.
* `motion_graphics_present` - the rebuilt timeline carries at least one
  motion-graphics item. Fails on an empty list, and on a measurement that
  never listed.

Everything else is honestly unwired. `SUGGESTED_CHECKS` maps the routed
steps those two checks can prove anything about; a note routed anywhere
else, an unrouted or ambiguous note, and above all a TASTE note ("this
segment makes it look choppy") has no mechanical proof available, and
`verifiability_of` says so plainly rather than building a placeholder.
A taste note's marker stays until the captain says otherwise.

── The removal itself ───────────────────────────────────────────────

Resolve's marker API is `DeleteMarkerAtFrame` / `DeleteMarkerByCustomData`
alongside `GetMarkers`. `marker_feedback.py`'s own docstring records that
`hasattr` is useless on Resolve proxies and that four assumptions in this
area were wrong until they were run against a real Resolve - so nothing
here guards on `hasattr`, and every deletion is judged by what the call
RETURNS, then confirmed by re-reading `GetMarkers`:

* `DeleteMarkerAtFrame` returning True removed the marker at that frame;
  anything falsy means it did not - absent, or refused - and is recorded
  as not removed, never retried blindly.
* `DeleteMarkerByCustomData` is the fallback where a record id exists,
  judged the same way. KNOWN UNKNOWN, stated plainly: which of the two
  calls actually works on a timeline marker versus a clip marker has not
  been measured against a live Resolve in this module - the captain's
  notes include both kinds and they may not delete the same way. The code
  tries the frame call first and records which call was used and what it
  returned, so either outcome is evidence rather than an assumption.
* A rebuilt timeline is a different timeline: the frame a note sat on may
  no longer mean the same thing. Clearing therefore always re-resolves
  the note's CURRENT marker key off the open timeline before deleting,
  and refuses when the note's text is no longer found there - deleting a
  marker by stale frame alone could clear a note the captain typed since.

A second known unknown, also stated: whether a marker can be identified
stably across a rebuild. The record keys on the routed `note_id`
(timeline, source, frame), which is stable across re-routings of the same
pull but NOT across a rebuild that moves the frame. The record carries
the captain's text verbatim precisely so a moved note is still findable
by its words.

Rules relocated from AGENTS.md 15
---------------------------------
These are the engine's rules for this module. They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 15 keeps the headline and
points here.

**A marker the captain left is removed only when a real check proves the
fix, never on a worker's assertion and never on a reasoned decline.**
One enumeration, `library/tools/marker_resolution.py`.
- **Every collected note gets a durable resolution record** in
  `<project>/marker_feedback/resolutions/`, holding the captain's
  original text verbatim, what was done, and the evidence. The record is
  written BEFORE the marker is removed; if removal fails the record
  still stands and says the marker is still there.
- **A declined note is acknowledged, not addressed**, and never clears
  its marker - even when a check that would pass is named alongside it.
- **A note with no mechanical proof available is recorded as
  unverifiable**, with the reason, and its marker stays. No check is
  invented so that a marker can be cleared.
- **The Resolve removal call is judged by what it actually returns**,
  confirmed by re-reading `GetMarkers`. Nothing here guards on
  `hasattr`.
- `tests/test_marker_resolution.py`.
```
