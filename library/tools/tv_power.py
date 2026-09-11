"""The old-TV switch animation. ONE SHAPE, played in two directions.

The captain's marker on ``Reel 20`` (frame 538, 2026-09-09) asks for the
reel to turn "on and off like an old tv" and leaves the interpretation to
the worker ("you can create some animations for this").  This module is
that interpretation, as DECLARED VALUES rather than compiled taste: every
timing below states what it is and why, and a project changes any of them
through its own ``tv_frame`` declaration (see ``library/tools/tv_frame.py``)
instead of forking the engine.

One shape, not two animations
-----------------------------
The captain, on ``Reel 09``, clip ``LCATL0013.MXF``, 2026-09-11::

    "also the tv on animation should start from fully black just like
     the reverse of how the tv off animation goes to fully black"

He is right, and until that marker this module held TWO independently
tuned animations.  The switch-off was ``collapse -> dot -> decay``
ending at gain 0.0 - fully black.  The switch-on was a different
animation with different phase names (``line -> expand -> bloom``),
different lengths (4/6/8 against 6/3/9), no dot phase at all, and a
first frame that was a lit band of picture at gain 2.2.  Nothing was a
reverse of anything; the two only happened to total 18 frames each, and
re-timing either would have silently desynchronised them.

So the SHAPE is declared once, as the states a cathode-ray set passes
through between black and picture::

    black  <--decay--  dot  <--dot--  line  <--collapse--  picture

A switch-OFF plays that right to left (picture to black), which is what
it always did.  A switch-ON plays the SAME states left to right (black
to picture), so it begins fully black and arrives at the picture, and
the phase names below are the switch-off's because the switch-off is
the half the captain named as the reference.

The phase LENGTHS, the crop depth, the dot size and every gain are
therefore stated once and read by both halves.  ``switch_on_frames()``
and ``switch_off_frames()`` are the same call, and
``tests/test_tv_power.py`` proves the drawn keyframes of one are the
time-reverse of the other - so a re-timing cannot move one without
moving the other.

How it is drawn
---------------
Both directions are per-clip Fusion animation on the V1 footage clip,
built by ONE builder (``library/tools/fusion/effects._tv_power``) that
``fx.tv_power_head`` (switch-on, at the reel's first clip) and
``fx.tv_power_tail`` (switch-off, at the reel's last clip) are thin
wrappers over: a black ``Background`` gated by an inverted
``RectangleMask`` animates the vertical band, a ``Transform`` the dot,
and a ``BrightnessContrast`` the spot spike and the fall to black.
``library/tools/fusion/comp_builder.build_effect_comp`` dispatches on
the ``tv_power_head`` / ``tv_power_tail`` keys, so the timings travel
as ordinary effect params and the .comp carries no value this module
did not declare.

The V2 frame asset is NOT animated: it is the set, and the set does not
flicker when the set turns on.  Only the picture inside it powers.

Where the numbers come from
----------------------------
Proposed by the worker on 2026-09-09 from observed CRT switch behaviour,
at 30 fps, and kept as the switch-OFF's values when the two halves were
unified on 2026-09-11 - the captain named the switch-off as the shape
to mirror, so the switch-off's numbers are the shape.  The whole
animation is 18 frames (0.6 s) each way: long enough to read as a
switch rather than a cut, short enough not to eat the bookends (the
Lucie intro card alone holds 3.0 s).  Change any of them - they are
configuration, not constants - and both directions change together.
"""

from __future__ import annotations

# ── The switch shape (frames at 30 fps) ───────────────────────────
#
# Named from the switch-OFF, which is the direction the captain named
# as the reference.  A switch-ON plays them in the reverse order:
# `decay` is the glow rising out of black, `dot` is the dot opening to
# a line, `collapse` is the line opening to the full picture.

# The deflection ramp on a real set is near-instant, but an instant
# collapse reads as a cut.  6 frames (~200 ms) is a visible ramp that
# still feels electromechanical rather than eased.
COLLAPSE_FRAMES = 6
"""Full picture falls to a horizontal line (switch-on: line opens to picture)."""

# The line-to-dot contraction is the fastest thing on screen - the spot
# is at its brightest here - so it gets the fewest frames: 3 (~100 ms).
DOT_FRAMES = 3
"""The line contracts to a dot (switch-on: the dot opens to a line)."""

# The afterglow is what sells "tube": it lingers past the dot.  9
# frames (~300 ms) is the longest phase, because a cut to black the
# moment the dot dies reads as a switch, not a set.
DECAY_FRAMES = 9
"""The dot's glow decays to black (switch-on: it rises out of black)."""

# How far the picture collapses.  CropTop/CropBottom meet at 0.49, not
# 0.5: a fully closed band is a divide-by-nothing some Resolve builds
# render as a one-frame flash of the uncropped picture.
#
# This was 0.40 on the switch-ON alone between 2026-09-10 and
# 2026-09-11, because the captain judged its opening frame ("keep the
# effect, soften the opening sliver"): the switch-on used to OPEN on
# that sliver, so a 2% band of picture was the first thing the reel
# showed.  It no longer opens there - the switch-on now opens on
# BLACK and reaches the sliver through the dot, exactly as the
# switch-off leaves it - so the complaint is answered by the shape
# and the two halves keep one depth between them.
COLLAPSE_CROP = 0.49
"""The CropTop/CropBottom value of the fully collapsed line."""

# The bounds a declared depth is held to.  0.0 is a pure gain flash
# with no collapse - coherent, so allowed.  0.5 is the
# divide-by-nothing the COLLAPSE_CROP comment guards against, so the
# bound is exclusive: 0.5 itself raises rather than degrading into a
# one-frame flash of uncropped picture.
COLLAPSE_MIN = 0.0
COLLAPSE_MAX = 0.5

# How small the dot gets.  A uniform Transform Size of 0.05 is a few
# pixels on a 4K frame: a point of light, not a thumbnail.
DOT_SIZE = 0.05
"""The uniform Transform Size of the dot."""

# What the brightness does at each state.  The dot is the spot at its
# hottest - a gain of ~2.5x, clearly hotter than white without clipping
# the whole ramp into a flat field - the line is on its way there, and
# black is 0.0 (no signal).
DOT_GAIN = 2.5
"""BrightnessContrast gain at the dot: the spot at its brightest."""

LINE_GAIN = 1.6
"""BrightnessContrast gain at the line, between picture and dot."""

BLACK_GAIN = 0.0
"""BrightnessContrast gain at black: no signal at all."""

PICTURE_GAIN = 1.0
"""BrightnessContrast gain at the picture: pass-through."""


def switch_shape(collapse=None, dot=None, decay=None,
                 collapse_crop=None) -> dict:
    """The switch timing as data, with declared defaults fillable.

    ONE shape.  Both directions read it, so a re-timing of the
    switch-off is a re-timing of the switch-on and cannot be anything
    else.  ``collapse_crop`` is the depth, not a length: how far the
    picture closes at the line.  It travels in this mapping so a
    project declares it the same way it declares the phase lengths -
    but it is NOT summed into the total.
    """
    return {
        "collapse_frames": COLLAPSE_FRAMES if collapse is None else int(collapse),
        "dot_frames": DOT_FRAMES if dot is None else int(dot),
        "decay_frames": DECAY_FRAMES if decay is None else int(decay),
        "collapse_crop": (COLLAPSE_CROP if collapse_crop is None
                          else float(collapse_crop)),
    }


def switch_on_frames(collapse=None, dot=None, decay=None,
                     collapse_crop=None) -> dict:
    """The switch-on timing: the shape, played black -> picture."""
    return switch_shape(collapse, dot, decay, collapse_crop)


def switch_off_frames(collapse=None, dot=None, decay=None,
                      collapse_crop=None) -> dict:
    """The switch-off timing: the shape, played picture -> black."""
    return switch_shape(collapse, dot, decay, collapse_crop)


def switch_total(timing: dict | None = None) -> int:
    """Total switch length in frames (18 by default: 0.6 s at 30 fps).

    Sums the phase LENGTHS only: ``collapse_crop`` is a depth, and
    adding a crop fraction to a frame count would move the window every
    time the look changes.  The same total either way round, because it
    is the same shape.
    """
    t = timing or switch_shape()
    return (int(t["collapse_frames"]) + int(t["dot_frames"])
            + int(t["decay_frames"]))


def switch_on_total(timing: dict | None = None) -> int:
    """Total switch-on length in frames. Identical to the switch-off's."""
    return switch_total(timing)


def switch_off_total(timing: dict | None = None) -> int:
    """Total switch-off length in frames. Identical to the switch-on's."""
    return switch_total(timing)


def validate_collapse(value, source: str) -> float:
    """A declared depth as a crop fraction, or raise.

    Out of range is a mistake, not something to clamp: 0.5 and above
    is the divide-by-nothing COLLAPSE_CROP guards against (some
    Resolve builds flash the uncropped picture there), and clamping
    ``collapse_crop: 0.9`` down to it would ship exactly that flash.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"tv power collapse_crop in {source} must be a "
            f"number between {COLLAPSE_MIN} and "
            f"{COLLAPSE_MAX} (exclusive), got "
            f"{type(value).__name__}: {value!r}"
        )
    depth = float(value)
    if not (COLLAPSE_MIN <= depth < COLLAPSE_MAX):
        raise ValueError(
            f"tv power collapse_crop in {source} must be "
            f"between {COLLAPSE_MIN} and "
            f"{COLLAPSE_MAX} (exclusive), got {depth!r}: "
            f"0.5 closes the band entirely, which some Resolve builds "
            f"render as a one-frame flash of the uncropped picture."
        )
    return depth


def validate_timing(value, source: str) -> dict:
    """A declared ``power`` mapping as one validated shape, or raise.

    The declaration names the SHAPE's phases directly::

        power: {collapse_frames: 8, dot_frames: 4, decay_frames: 12}

    A non-mapping, a negative frame count, or an unknown key raises
    naming *source*: a power declaration that is silently dropped is an
    animation the editor believes shipped.

    ``switch_on`` / ``switch_off`` sub-mappings are REFUSED rather than
    merged.  They were the shape of this declaration until 2026-09-11,
    and honouring them would be honouring exactly the drift the
    captain's marker closed - one half re-timed and the other left
    behind.  The refusal names the replacement.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(
            f"tv power declaration in {source} must be a mapping, got "
            f"{type(value).__name__}: {value!r}"
        )
    for half in ("switch_on", "switch_off"):
        if half in value:
            raise ValueError(
                f"tv power declaration in {source} names {half!r}: the "
                f"switch-on and the switch-off are ONE shape played in "
                f"two directions (captain, 2026-09-11 - \"the tv on "
                f"animation should start from fully black just like the "
                f"reverse of how the tv off animation goes to fully "
                f"black\"), so a per-half timing would re-time one "
                f"direction and leave the other behind. Declare the "
                f"phases directly: "
                f"{sorted(switch_shape())}."
            )
    defaults = switch_shape()
    merged = dict(defaults)
    for key, item in value.items():
        if key not in defaults:
            raise ValueError(
                f"tv power declaration in {source} names unknown timing "
                f"{key!r}; known: {sorted(defaults)}"
            )
        if key == "collapse_crop":
            merged[key] = validate_collapse(item, source)
            continue
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
            raise ValueError(
                f"tv power {key} in {source} must be a non-negative "
                f"frame count, got {item!r}"
            )
        merged[key] = int(item)
    return merged
