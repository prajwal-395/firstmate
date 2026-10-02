# `library.tools.marker_carry` - the history behind its contract

This is the module docstring of `library/tools/marker_carry.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
A promotion that is about to discard the captain's words says so.

The defect this closes
----------------------
Measured 2026-09-09 on Reel 09, restated 2026-09-11 on Reel 13.
Promotion REPLACES a timeline object, so the markers the captain typed
onto it go with the old one.  `marker_feedback show` then reports "0
note(s)", and the honest answer to "did the markers clear" is "they are
gone, but they were not cleared".

`library/tools/marker_resolution.py` exists precisely so a note is
never removed except on evidence - it records the captain's words
BEFORE any deletion and keeps a note it cannot verify.  Every one of
those guarantees held and every one was bypassed, because the rebuild
destroyed the markers through a different route entirely.  That is the
shape of the whole week's defects: something happened that should not
have, and nothing said so.

The scope of this module
------------------------
The MINIMUM, deliberately.  Whether markers should always be carried is
a live product question (`vep-promotion-destroys-captain-markers`): a
marker anchored to a frame in the old timeline may point at different
content in the new one, and re-attaching the captain's words to the
wrong moment is its own kind of lie.  That question stays open.

What is not in question is the silence.  So:

* every marker on the retiring timeline is READ before anything is
  renamed, with the picture it sits on;
* a marker whose anchor still resolves in the replacement is carried to
  the frame that shows the same picture;
* a marker whose anchor does not resolve is REPORTED BY NAME, with its
  words, and - at promotion time - PUT BACK as a Blue marker at the
  seam where its subject was cut out, with our own reply BESIDE it,
  never instead of it.

Nothing is deleted here and nothing is guessed onto similar-looking
material. The seam is not a re-anchor: it is the join the cut actually
made - the frame right after the surviving content that played just
before the note - derived from the retiring and replacement picture
rows, never from similarity. Where even the seam is ambiguous (nothing
before the cut survives), the note goes at the start of the replacing
item and the reply says so.

The DATASTORE `marker_feedback` pull format stays the durable standard
for any pre-promotion capture on disk: per note, the CLIPS it spans
with source file and source frame range. No new capture is added here -
the seam is derived from the same live pre-rename read `plan_carry`
uses - so there is no weaker snapshot for anyone to mistake: the
resolve-axi "markers snapshot" restores the timeline plane only (no
clip anchors, no source ranges), and ad-hoc text captures keep the
WORDS but not the ANCHOR. Neither can derive a seam.

What "the anchor resolves" means
--------------------------------
Not the frame number - a rebuild moves every frame, which is why
`marker_resolution` already refuses to act on a moved frame.  The
anchor is the PICTURE under the marker: the source file the topmost
picture row is playing at that frame, and how far into that source
file the frame sits.  A marker resolves when the replacement plays the
same source frame of the same file somewhere, and it carries to that
frame.  Two frames of one source file may play twice in one reel; the
NEAREST candidate to the marker's original frame wins, because a reel
that repeats a shot has not moved the captain's note to the other
saying of it.

Whether a stable identity for HIS notes exists
----------------------------------------------
The 13:38 question on the replies-decay task: do not carry a delta,
pair by identity - and first establish whether such an identity CAN
be stable across a rebuild. The answer for timeline-plane notes is
YES, and it is the same signal this module already trusts:

* a note's WORDS (`name` + `note`, normalised for whitespace and case
  only - `feedback_ledger.durable_identity`) do not move when frames do;
* a note's PICTURE anchor (the source file and source frame under it)
  is rebuild-invariant BY THE DEFINITION carry resolves by: a note
  whose anchor still plays is carried, one whose anchor is gone is
  reported uncarried.

So a reply names its note by identity (`answers`) plus the anchor it
sat on (`answers_anchor`), and the carry re-pairs by id after the
rebuild regardless of where either marker moved. A position delta was
rejected deliberately: anything pairing two markers by position decays
under rebuilds, which is the same reason the old `answers` frame
locators failed - and Reel 29's green (locator `@22`, marker at 29,
nothing visibly moved, no rebuild in between) proves a delta does not
even cover every decay.

Stated plainly, the limits:

* a note over NOTHING (a gap, a generator) has no anchor half, so a
  reply answering it pairs by words alone and is REPORTED weak;
* edited words are a NEW identity: the old reply then names nothing,
  and is reported unpaired rather than silently re-bound;
* two identical notes (same words, same anchor) bind nearest, first;
* CLIP-plane markers are never read here, so their replies cannot
  re-pair in this pass - `audit_replies` says which ones those are
  rather than pretending otherwise.

`tests/unit/resolve/test_marker_carry.py`.
```
