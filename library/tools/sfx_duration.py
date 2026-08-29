"""How much of a chosen sound plays, and what stops the cut clicking.

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
"""

from __future__ import annotations


# The de-click ramp at the out point of a truncated sound, in FRAMES.
# One frame because a keyframe is addressed by frame number and one is
# the shortest ramp the route can carry - see the module docstring.
DECLICK_FADE_FRAMES = 1

# The floor, in frames: one frame at level plus one frame of ramp.
# Mechanical - it is the timebase, not a judgement about how short a
# sound may be.
MIN_PLAYED_FRAMES = DECLICK_FADE_FRAMES + 1

# Where the ramp ends.  `otio_mix.MIN_VOLUME_DB` is Resolve's own floor;
# this module names it rather than importing, so a duration can be
# resolved without the OTIO vocabulary being loaded.
FADE_FLOOR_DB = -100.0


class SfxDurationRefused(ValueError):
    """A plan asking for a length the chosen sound cannot give.

    Raised at PLAN time, in step 4.04, naming the sound, what was asked
    for and what the file measures.  It is never a clamp and never a
    drop: a length is a decision, and quietly substituting a different
    one ships a sound nobody chose the length of.
    """


def full_playable_seconds(entry: dict, source_in: float = 0.0) -> float:
    """Everything of `entry` that is still ahead of `source_in`.

    Raises rather than substituting a number: a sound whose length
    nothing measured cannot be given a true `timeline_out`, and the
    catalogue publishes it as `unmeasured` so the model can see it.
    """
    duration = entry.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise SfxDurationRefused(
            f"{entry['sfx_id']!r} has no measured duration - the SFX "
            f"library index records none and ffprobe could not read the "
            f"file, so no true timeline_out can be written for it."
        )
    playable = float(duration) - float(source_in)
    if playable <= 0:
        raise SfxDurationRefused(
            f"{entry['sfx_id']!r} measures {duration}s with its transient "
            f"at {source_in}s, so trimming to the transient leaves nothing "
            f"to play."
        )
    return playable


def resolve_played_seconds(entry: dict, source_in: float = 0.0,
                           requested=None, fps: float = 30.0) -> float:
    """How long the chosen sound plays for.

    `requested` is the plan's `duration_seconds`, or None where the plan
    declared none.  None returns the whole playable remainder - the
    absence of a decision, not a decision.
    """
    playable = full_playable_seconds(entry, source_in)
    if requested is None:
        return playable

    if isinstance(requested, bool) or not isinstance(requested, (int, float)):
        raise SfxDurationRefused(
            f"{entry['sfx_id']!r} is asked to play for {requested!r}, which "
            f"is not a number of seconds. Give a duration_seconds inside "
            f"the sound's own measured length, or leave it out and the "
            f"whole sound plays."
        )

    requested = float(requested)
    floor = MIN_PLAYED_FRAMES / float(fps or 30.0)
    if requested < floor:
        raise SfxDurationRefused(
            f"{entry['sfx_id']!r} is asked to play for {requested}s, which "
            f"is under {floor:.3f}s - {MIN_PLAYED_FRAMES} frames at "
            f"{fps or 30.0:g} fps. A slice shorter than one frame at level "
            f"plus one frame of de-click ramp is a fade, not a sound, and "
            f"the keyframe grid cannot express it. This is the timebase, "
            f"not a minimum length for a sound."
        )
    if requested > playable:
        measured = entry.get("duration_seconds")
        raise SfxDurationRefused(
            f"{entry['sfx_id']!r} is asked to play for {requested}s and "
            f"measures {measured}s"
            + (f", of which {playable:.3f}s is left after the transient "
               f"trim at {source_in}s" if source_in else "")
            + f". A sound cannot play longer than it is. Ask for at most "
              f"{playable:.3f}s, or leave duration_seconds out and the "
              f"whole sound plays. Nothing is clamped."
        )
    return requested


def declick_fade_seconds(played: float, playable: float,
                         fps: float = 30.0) -> float:
    """The ramp at the out point, or 0.0 where the sound ends by itself.

    A truncated sound stops mid-waveform and that step to silence is an
    audible click (module docstring for the measurement). A sound played
    to its own end has nothing to ramp.
    """
    fps = float(fps or 30.0)
    frame = 1.0 / fps
    if played >= playable - frame / 2.0:
        return 0.0
    return min(DECLICK_FADE_FRAMES / fps, played / 2.0)
