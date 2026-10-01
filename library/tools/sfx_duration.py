"""How much of a chosen sound plays, and what stops the cut clicking.

How long a sound plays is the PLAN's decision, bounded by what the file
measures.  A sound's duration is a property of the sound, not of a type
name - and refusing to ASK the plan for a length would substitute the
file's full length for a decision nobody made.

## The bound

**The upper bound is the sound's own measured length, less whatever the
transient trim already skipped.**  A request past that is REFUSED BY NAME,
never clamped: a clamp would silently hand back a different length from
the one the plan asked for.

**The lower bound is two frames of the run's own timebase**, and it is
the only floor here.  It is mechanical, not creative: one frame at level
plus one frame of de-click ramp.  **How short a sound should be is the
plan's call and this module has no opinion**; there is no minimum
"audible" length here and none may be added (AGENTS.md 10.5).

**A selection that declares no length plays the whole remainder** - the
ABSENCE of a decision, read the same way `music_section` reads a track
that names no section.

## The click

A sound cut short ends wherever the waveform happens to be, which is an
audible click.  **The ramp is ONE FRAME** - the shortest the delivery
route can express, because the mix reaches Fairlight as OTIO volume
keyframes (`library/tools/otio_mix.py`) addressed by frame.  It is
applied only where the play window was TRUNCATED: a sound played to its
own end already ends where the file ends.

The run of record that lost the plan's lengths, the library's measured
duration distribution, and the measured click: docs/evidence/sfx_duration.md.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

- **How long a sound plays is the PLAN's decision, BOUNDED by what the file measures.** One enumeration, `library/tools/sfx_duration.py`. `duration_seconds` is optional; declaring none plays the whole sound. A request past the file's measured length is REFUSED BY NAME and never clamped. **The only floor is the timebase** - two frames - and no minimum may be added. **A sound cut short carries a one-frame de-click ramp** (`otio_mix.declick_curve`). [why](docs/RULE_EVIDENCE.md#the-plan-could-not-say-how-long-a-sound-plays)
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
