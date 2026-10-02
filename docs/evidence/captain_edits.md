# `library.tools.captain_edits` - the history behind its contract

This is the module docstring of `library/tools/captain_edits.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The captain's edits, as small readable deltas that survive a rebuild.

The captain, 2026-09-09: *"if i ask to remove a piece of the video and
replace it with something else, and then ask you to rebuild the timeline,
those changes should persist"*. And: *"going into the subtitles and making
corrections that i ask of you so it's there"*.

What already worked is whole-value supply (`library/tools/external_inputs.py`):
handing over the ENTIRE `speech_sequence` to remove one fragment. That is
unreadable, unreviewable, and goes stale the moment anything upstream
legitimately changes. An edit here is a DELTA - one anchor, one decision,
in the captain's own words - expressed against something STABLE.

What the delta is anchored to, and why
--------------------------------------
The anchor is the SPOKEN WORDS (`anchor_phrase`). Three candidates break:

- a FRAME NUMBER is not stable across a rebuild: re-transcription moves
  every boundary, and the marker work already refuses to delete by a
  moved frame for exactly this reason. An edit carrying `anchor_frame`
  (or any frame field) is REFUSED at write time, not lost at rebuild.
- a SOURCE TIMECODE in the original MXF is stable until the transcript
  re-segments the passage around it; then it names the wrong seconds.
- a SEGMENT IDENTITY (`segment_id`, block position) is pipeline-assigned
  and renumbers whenever anything upstream legitimately changes.

Spoken words survive all three: a rebuild that re-transcribes, re-cuts
and renumbers still says the same words in the same order. What breaks
it is honest and named: a re-transcription that REWORDS the passage, or
a fragment that is already gone. That edit reports STALE - loudly, on
stderr and in the step output - and never silently vanishes.

Correction versus edit
----------------------
The sibling lane (`transcript_corrections`, in flight) owns CORRECTIONS:
a fact about the world that applies everywhere and forever ("the audio
this project transcribed as Lucy is Lucie"). This module owns EDITS: a
decision about THIS ONE PIECE ("drop the 'so what do they' fragment at
22 seconds", "this caption reads X"). A correction lives in
`learned_context/` and is applied at the transcript root; an edit lives
in `<project>/external/captain_edits.json` and is applied where the
decision lands (spine blocks, caption cards). Collapsing them would
either scope a world-fact down to one card or promote a one-piece
decision into every future video - both wrong, so both exist.

Never picture without sound
---------------------------
Removing speech moves everything after it. `apply_drop_fragments`
re-derives every later block's `timeline_start`/`timeline_end` (and
frame fields) from the surviving durations, so picture and sound move
together. A caption fix changes TEXT ONLY - timings untouched - and
re-derives what depends on the text (`word_count`, `emphasis_words`).

Redrawing a closer
-------------------
Keep exclusions can only REMOVE seconds, so no existing mechanism can
EXTEND a closer backwards - the shared call to action [321.61, 328.23]
opened mid-sentence ("we're calling the lucy visibility system ...")
and the captain ruled 2026-09-10 it must open on "it's exactly why
we've been building this platform we're calling ...", at 319.358 with
the end fixed at 328.231. That pin is a `redraw_closer` edit, and it
lives HERE rather than in `transcript_corrections` for one reason: a
correction is a fact about the world, everywhere and forever ("the
audio transcribed as Lucy is Lucie"), while where one shared closer
starts is a decision about THIS ONE PIECE - an edit, anchored to
spoken words like every other edit here, refusing timecode pins by
construction. A closer pinned to 319.358 breaks the moment anything
upstream re-times; the words survive.

A pin names both ends in words: `anchor_phrase` (what the closer must
open on) and `from_phrase` (what it opens on now). The second is
REQUIRED, not courtesy: without it the pin would redraw every closer
in the batch, including invitations the captain never heard, and the
only thing stopping that overreach would be remembering not to. The
shared span is identified by what it SAYS, never by its seconds.

Two layers enforce it, the same shape keep exclusions take: step 3.04
redraws regenerated proposals, and the reel build redraws approved
moments in memory (the file keeps what the captain ruled on, exactly
like the word-edge repair beside it). Applying a recorded pin to an
approved moment is obedience, not re-decision - the approved-moment
guard stops the ENGINE re-deciding a range under the captain, and the
pin IS the captain's judgement with their reason. The extension adds
seconds where an exclusion only removes them, so the added seconds are
checked like a new span: real speech inside, whole segments at both
edges (the end never moves), and no overlap with the reel's own body,
which would play those seconds twice. Anything failing that is
reported LOUDLY and that reel keeps its span.

`tests/test_closer_redraw.py`.

A hand move in the Inspector
----------------------------
The captain drags a clip's Position X by hand (Reel 09, 2026-09-10:
Akshita's clip from the pipeline's Pan 14 to Pan -35) and the next
rebuild re-aims the punch-in onto the measured subject, throwing the
hand move away. That pin is a `transform_override` edit: the same
word anchor (the words the moved shot speaks), naming one Edit-page
transform property (`Pan`, `Tilt`, `ZoomX`, `ZoomY` - what the build
sets and the Inspector shows as Position/Zoom) and the number it must
hold. Pan/Tilt also record the renderer draw gain that gave the number
its visual meaning; the build rebases it to its measured gain. Legacy
entries with no gain metadata use reference gain 1.0. Zoom must be
positive; Pan/Tilt must sit inside what Resolve holds on a 1080x1920
timeline (`PAN_TILT_RAIL_1080X1920`, measured in
`library/tools/tight_box.py`), because
past it Resolve clamps silently and the held value would not be the
recorded one.

An override may carry `reel`: the timeline name it holds on, matched
by prefix (the `reel_ending` convention). A shot four reels share
speaks one anchor on all four; the per-reel Pan is the same words
with a reel scope, holding there and reporting routine STALE
elsewhere. No `reel` holds everywhere, exactly as before - and where
a scoped override and an unscoped one meet on one span, the scoped
one wins that span while the general one still holds everywhere
else.

The build applies overrides AFTER aiming the punch-in, so the held
value is the captain's, and re-proves coverage (`assert_punch_took`):
an override that uncovered an edge raises rather than shipping black.
An override matching no placed span reports STALE like every other
kind. `tests/test_transform_override.py`.

Recording a decision: the one route
------------------------------------
`python3 -m library.tools.captain_edits <project> <verb>`:

- `list` (the default): what is in force, in plain language.
- `record-closer --anchor ... --from ... --reason ...`: pin a closer.
- `record-transform --anchor ... --property Pan --value -35
  --reason ... [--draw-gain G]`: the typed fallback for a decision
  settled in words. Pan/Tilt numbers default to reference gain 1.0;
  supply `--draw-gain` when the number was chosen under another gain.
- `record-retime --anchor ... --edge head --reason ...`: the typed
  fallback for a hand trim - one placed span's head (or tail) onto
  the anchor's own word edge. Trims only. The write stamps where
  the anchor resolves now (`recorded_edge`), so a later
  re-transcription that moves those words reports DRIFTED
  pre-build rather than following them silently.
- `capture-transform --reel 9 --timeline 'Reel 09 - ...' --words ...
  [--property Pan] --draw-gain G [--reason ...]`: read the value out
  of the LIVE Resolve timeline - the hand move, which exists nowhere
  else - and record what Resolve holds with its measured draw gain.
  Pan/Tilt captures require `--draw-gain`; Zoom does not. Footage items
  only (the master snapshot
  decides membership, never an extension guess); ambiguity - the
  words twice on the reel, two items on one track, stacked angles
  without `--track` - refuses, naming what matched.

Every record refuses before writing: structurally, and against the
measured transcript where one is on file (a typo fails here, not on
the next build). A re-ruling of the same decision SUPERSEDES it; an
exact duplicate is refused as already in force. No second store
beside this one: a placement value fits here because the ANCHOR is
the same stable thing every other kind anchors to - the spoken
words - and only the payload differs (a number held, not a range
redrawn or a fragment dropped).
```
