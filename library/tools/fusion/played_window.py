"""Where a played frame lands in a per-clip Fusion comp's own time.

This module is the ONE statement of the comp time base, because getting
it wrong is invisible: a keyframe outside the rendered range is not an
error, it is a spline that extrapolates flat, and Fusion draws whatever
value the nearest keyframe carries for every frame of the clip.

**Comp frame 0 is the clip's FIRST PLAYED frame.**  A per-clip Fusion
comp is rendered over the frames the timeline plays, numbered from zero -
not over the source clip's own frame numbering.  So a segment cut from
source frames 654..725 is comp frames 0..71, and a keyframe written at
654 is 654 frames past the end of everything Resolve renders for it.

``clip_dur`` is a different quantity and is unchanged by this: it is the
SOURCE clip's frame count and it sets the comp's declared frame RANGE
(``GlobalOut`` on every Background node, the MediaIn extent).  It is
always at least the played length, so a Background sized by it exists for
every frame that plays - which is the failure AGENTS.md section 5's
"never omit GlobalOut" rule records.

What this replaced, and how it was measured
-------------------------------------------

``effects.py`` used to place every animated keyframe at the SOURCE frame
number, on the belief that the comp was rendered over the source's own
range.  Project 001's run of 2026-08-26 planned two ``defocus``
transitions, 15 frames each, and the comps it banked are on disk:

    speech_9_seg0   head of cut 8   keys 654 = 3.0 ... 669 = 0.0, 725 = 0.0
    speech_14_seg0  head of cut 13  keys 944 = 3.0 ... 959 = 0.0, 1204 = 0.0
    speech_7_seg0   tail of cut 8   keys 0 = 0.0, 3480 = 0.0 ... 3495 = 3.0
    speech_12_seg0  tail of cut 13  keys 0 = 0.0, 1047 = 0.0 ... 1062 = 3.0

Those clips play 71, 260, 480 and 43 frames.  Under the source-frame
belief each ramp sits exactly on the played frames and all four draw.
Under this module's reading, the two heads hold their FIRST key - full
strength - for every frame they play, and the two tails interpolate 0.0
to 0.0 across everything that plays and draw nothing.

The shipped master says which happened.  Measured on
``exports/Pipeline_Edit.mp4`` as mean absolute horizontal pixel
difference at 540x960, one value per frame:

    cut 8 tail, last 20 played frames   6.97 6.83 6.80 ... 6.90 6.77 6.79
    cut 8 head, first 20 played frames  1.81 1.81 1.78 ... 1.47 1.49 1.63
        rest of that clip 1.865 - the same level, flat, no ramp
    cut 13 head, first 20 played frames 2.00 2.01 1.99 ... 1.97 1.98 1.99
        rest of that clip 1.728 - flat

No ramp on any of the four, and the two head clips are uniformly soft for
their whole length: 71 + 260 = **331 frames**, 18.6% of a 1783-frame
video, carrying a full-strength defocus nobody planned.  Against the same
source clip with the same conform crop, the head of cut 8 keeps 44.5% of
the high-frequency energy of its untransitioned neighbours three seconds
later, where the raw footage says the two should be within 5% of each
other.  Four predictions, four matches, and the source-frame reading
contradicted by all four.

See docs/RULE_EVIDENCE.md#the-transition-ramp-that-never-ran.
"""

from __future__ import annotations

from typing import Optional


class TransitionLongerThanTheClip(ValueError):
    """A planned ramp does not fit in the frames its clip plays.

    Raised rather than drawn.  A head ramp longer than its clip never
    reaches neutral, so the effect covers the whole clip - which is the
    defect this module exists to remove, arriving by another route.
    """


def played_range(
    clip_dur: int,
    source_in: Optional[int] = None,
    source_out: Optional[int] = None,
) -> tuple[int, int]:
    """(first, last) COMP frames of the segment the timeline plays.

    ``source_in``/``source_out`` are SOURCE frame numbers, as the
    manifest carries them.  The first played frame is comp frame 0, so
    the answer is always ``(0, played_length - 1)``.

    Either bound absent means "the whole source is played", which is the
    only case where the two numberings agree.
    """
    if source_in is None or source_out is None:
        return (0, max(0, int(clip_dur) - 1))
    span = int(source_out) - int(source_in)
    return (0, max(0, span))


def played_length(
    clip_dur: int,
    source_in: Optional[int] = None,
    source_out: Optional[int] = None,
) -> int:
    """How many frames of this clip the timeline plays."""
    first, last = played_range(clip_dur, source_in, source_out)
    return last - first + 1


def assert_ramp_fits(
    dur_frames: int,
    first: int,
    last: int,
    *,
    ttype: str,
    half: str,
) -> None:
    """Refuse a ramp the clip has no room for, by name.

    A drawn transition is decoration on a cut.  One that cannot finish
    inside the clip it decorates is not a shorter transition - it is the
    effect held across a whole clip, which is what this module was
    written to stop.  Say so instead of drawing it.
    """
    room = last - first
    if dur_frames > room:
        raise TransitionLongerThanTheClip(
            f"{ttype} {half} of {dur_frames} frames does not fit the "
            f"{room + 1} frames this clip plays. A ramp longer than its "
            f"clip never reaches neutral, so the effect would be held "
            f"across the whole clip."
        )
