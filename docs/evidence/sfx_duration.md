# `library.tools.sfx_duration` - the history behind its contract

This is the module docstring of `library/tools/sfx_duration.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
How much of a chosen sound plays, and what stops the cut clicking.

Step 4.04 used to carry a length and lost it.  The plan on the run of
record (001, 2026-08-26) asked for **0.25 s** under a defocus blur and
**3.0 s** across a pivot slot, and #298 - which fixed a real defect, the
model naming a TYPE that a keyword matcher turned into a file - replaced
the schema with ``{spine_block_position, sfx_id, volume_level,
rationale}``.  The sound then ran its own measured length.  The same
``whoosh_impact.mp3`` the run of record played for 0.25 s plays for
**8.04 s** at that revision.

The rule that produced it is right and was applied one step too far.
*A sound's duration is a property of the sound, not of a type name* -
so ``DURATION_DEFAULTS`` had to go, because ``bass_impact: 0.5`` was a
number nobody measured sitting in front of a 5.317 s riser.  But
**refusing to ASK for a length is the same defect in the other
direction**: it removes a decision the plan should be making and
substitutes the file's full length for it, which is a value that also
reached the timeline without anybody choosing it.

The library is what makes this matter.  Measured 2026-08-28 over the
captain's 78 entries: median duration **3.94 s**, only **9 of 78** at or
under 1.0 s, only **4** at or under 0.5 s, **30** over 3 s, longest
**76.14 s**.  Without a length, "put a short accent on this cut" is
expressible only by choosing one of nine files.

## The bound

**The upper bound is the sound's own measured length, less whatever the
transient trim already skipped.**  You cannot play more of a file than
it has.  A request past that is REFUSED BY NAME, never clamped: a clamp
would silently hand back a different length from the one the plan asked
for, and a plan that asked for four seconds of a two-second sound is a
plan written against a sound it has not read.

**The lower bound is two frames of the run's own timebase**, and it is
the only floor here.  It is mechanical, not creative: a slice shorter
than one frame at level plus one frame of de-click ramp is a fade rather
than a sound, and the keyframe grid cannot express it either way.  At
30 fps that is 0.067 s, which admits the run of record's 0.25 s with
room to spare.  **How short a sound should be is the plan's call and
this module has no opinion**; there is no minimum "audible" length here
and none may be added (AGENTS.md 10.5).

**A selection that declares no length plays the whole remainder**, and
that is the ABSENCE of a decision rather than a decision - the same
reading ``music_section`` gives a track that names no section, and
``transition_vocabulary.CUT_TYPES`` gives a cut that draws nothing.

## The click

Cutting a sound short leaves the waveform wherever it happens to be, and
the step to silence at the clip's out point is an audible click.
Measured on exactly the run of record's request - ``whoosh_impact.mp3``
decoded from its 0.714 s transient for 0.25 s, 48 kHz mono - the final
sample sits at **16.5% of the slice's peak** (-2185 against 13209).
That is a discontinuity, not a natural decay, so honouring a length
includes ending it cleanly.

**The ramp is ONE FRAME, and it is the shortest the delivery route can
express.**  The mix reaches Fairlight as OTIO volume keyframes
(``library/tools/otio_mix.py``) and a keyframe is addressed by FRAME, so
a conventional 5-10 ms de-click ramp has no representation on a 30 fps
grid - 33 ms is the floor the format sets, not a number chosen for feel.
It is applied only where the play window was TRUNCATED: a sound played
to its own end already ends where the file ends.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

- **How long a sound plays is the PLAN's decision, BOUNDED by what the file measures.** One enumeration, `library/tools/sfx_duration.py`. `duration_seconds` is optional; declaring none plays the whole sound. A request past the file's measured length is REFUSED BY NAME and never clamped. **The only floor is the timebase** - two frames - and no minimum may be added. **A sound cut short carries a one-frame de-click ramp** (`otio_mix.declick_curve`). [why](docs/RULE_EVIDENCE.md#the-plan-could-not-say-how-long-a-sound-plays)
```
