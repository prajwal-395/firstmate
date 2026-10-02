# `library.tools.music_bed` - the history behind its contract

This is the module docstring of `library/tools/music_bed.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
The bed is a SEQUENCE, not a minute.

Captain, 2026-09-01, correcting a limit this pipeline had written up as a
good decision: *"its not one continuous stretch from the music we have to
use, like we can use bits and pieces, or multiple tracks, and splice
pieces from different tracks and all that, like i think u limited what the
music step is actually doing"*.  And: *"if you like two sections of a song
but they are disjoint, then you should still be able to use them"*.  And:
*"the prompt is telling us about how we should be trying to find
compositions and pieces to be able to properly conduct the music, not just
selecting a one minute stretch of music blindly"*.

Three ceilings, and none of them was in the prompt
--------------------------------------------------
Step 2.04's ``handoff.md`` has asked for splices since it was
written - *"a 3-minute track is never used in full; pick the sections that
fit particular moments"* - and for a *"single continuous section is as
valid as multiple splices"*.  The model has been answering.  What stopped
was everything below it:

1. **One section.**  ``music_section`` resolves ONE ``source_in`` and
   ``compile_manifest`` placed one clip there, so two disjoint sections of
   the same track were not expressible.
2. **One track.**  ``music_selection`` is a single object, so a second
   track had nowhere to be named at all.  A schema change, not a flag.
3. **No conducting.**  Nothing anywhere carried "this piece here, that
   piece there".

This module is the shape that carries all three, and
``music_section.py`` stays exactly what it was: the ONE-section reading,
which is still what a selection that declares no bed gets.

Where the two halves of the decision live, and why
--------------------------------------------------
**Which sections of which tracks are worth using** is step 2.04's, and it
is source-time only: ``splices``, each naming a track and a span of it.
2.04 runs before the spine exists, so it cannot name a timeline position
and is not asked to.

**Where each piece plays** is step 2.05 ``mesh_spine``'s, because that is
the first step that has the spine, the chosen tracks and the music
analysis together.  A segment is anchored to a SPINE BLOCK POSITION -
``starts_at_block`` - and runs until the next segment starts.

That anchor is not a taste ruling, it is what is representable
--------------------------------------------------------------
The open question was whether a splice boundary should land on a spine
BLOCK BOUNDARY or on a BEAT.  Investigating it settled it for today, and
the finding is recorded in :data:`THE_SNAP_QUESTION` rather than left as
folklore:

- The model at 2.05 is authoring block DURATIONS; the absolute timeline
  seconds are computed from them afterwards by that step's post-bridge,
  frame-snapped.  A boundary the model named in absolute seconds would
  drift against the spine it is writing in the same answer.
- ``mesh_spine``'s ``context_fields`` DROPS ``music_analysis.tempo.beats``
  and ``.downbeats``, because AGENTS.md 10.1 keeps raw value lists out of
  prompts.  The model there cannot see a beat time at all.

So a block position is the only boundary the answering step can name
without guessing.  Beat alignment is reachable and is not free: it needs a
derived table routed into 2.05 the way ``cuts_toon.beat_near_cut`` is
derived for 4.02, and a snap pass over the resolved bed.  Nothing here
snaps to anything - AGENTS.md 10.5 - so adding one later is additive and
costs no schema change.

The crossfade
-------------
**A splice with no declared crossfade is a hard splice, and that is the
absence of decoration rather than a choice of it** - the same reading
``transition_vocabulary.CUT_TYPES`` and ``series_look.NEUTRAL_CDL`` get.
No default length is invented here, because a crossfade length is a
creative number and inventing one is the defect this week has been
clearing.  A segment that wants one declares ``crossfade_seconds``.

A declared crossfade is delivered as a real OVERLAP: the outgoing segment
plays ``crossfade_seconds`` past the boundary while the incoming segment
plays from the boundary, and ``otio_mix`` ramps one down and the other up
across the overlap.  Two overlapping clips cannot share one Resolve audio
track, so the renderer allocates the bed across A2, A3, ... exactly the
way it already allocates overlapping SFX, and the SFX bucket starts above
whatever the bed used.

Ending early, and fading out (rung 7, SD3.2)
-------------------------------------------
A segment runs until the next one comes in - unless it declares
``ends_at_block``: the spine block position it stops at, with an
optional ``end_offset_seconds`` / ``end_offset_frames`` from that
block's start (E3: seconds when the request states seconds, frames
when it states frames; both stated must agree past half a frame).
What follows is silence under the picture - "music out at 0:48, then
the last line dry" - never a slide of the next piece.  An end past the
next piece's start refuses (an overlap no crossfade declared), and an
end at or before the segment's own start refuses (no time at all).

A segment that ends - early or at the piece's end - may declare
``fade_out_seconds`` / ``fade_out_frames``: the ramp down over its
last seconds, delivered by ``otio_mix.music_curve`` exactly the way a
crossfade-out is.  No fade is invented: a segment naming none stops
hard, which is the absence of decoration.  A fade beside a crossfade
on one segment refuses - a piece that hands off AND fades out names
two endings, and picking one silently would ship a handoff the plan
did not agree on.

``tests/unit/audio/test_music_bed.py``.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**The bed is a SEQUENCE, not one continuous minute of one track.**
One enumeration, `library/tools/music_bed.py`. Captain, 2026-09-01: *"we can use bits and pieces, or multiple tracks, and splice pieces from different tracks"*. `music_section` is unchanged and is still what a plan declaring no bed gets.
- **The decision has TWO halves and they live in two steps.** 2.04 chooses the PIECES, in source time only (`splices`, each naming a track; `tracks` names every chosen track beyond the primary) - it runs before the spine exists and cannot name a timeline position. 2.05 `mesh_spine` CONDUCTS them (`music_bed`), because it is the first step holding the spine, the tracks and the analysis together.
- **A segment is anchored to a SPINE BLOCK POSITION and runs until the next one comes in.** That is what is representable, not a taste ruling: the model at 2.05 is authoring block DURATIONS (absolute seconds are computed afterwards and frame-snapped), and 2.05's `context_fields` drops `tempo.beats`/`downbeats`, so it cannot see a beat time. `THE_SNAP_QUESTION` records what beat alignment would cost - a derived table routed in, the `cuts_toon.beat_near_cut` route, plus a snap pass. **Nothing snaps a boundary to anything.**
- **A splice with no declared `crossfade_seconds` is a HARD splice** - the absence of decoration, the `CUT_TYPES`/`NEUTRAL_CDL` reading - and no length is invented for one.
- **A declared crossfade is a real OVERLAP**: the outgoing piece plays past the boundary while the incoming one plays from it, `otio_mix.music_curve` ramps one down and the other up, and the renderer's `_allocate_audio_tracks` spreads the bed across A2, A3, ... exactly as it already does for SFX - with the SFX bucket starting above whatever the bed used. **A2's overlap check allows exactly the declared fade and nothing else**, in `compile_manifest` and in `manifest_validator`.
- **The beat grid has one offset PER SEGMENT.** `music_analysis` measures the PRIMARY track only, so a beat is on the timeline only where that file plays; `beat_positions`/`downbeat_positions` take the spine and the duration for this.
- `tests/unit/audio/test_music_bed.py`, `tests/scenarios/test_plan_reaches_the_manifest.py`.
```
