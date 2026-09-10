"""The old-TV switch-on / switch-off animation. One enumeration.

The captain's marker on ``Reel 20`` (frame 538, 2026-09-09) asks for the
reel to turn "on and off like an old tv" and leaves the interpretation to
the worker ("you can create some animations for this").  This module is
that interpretation, as DECLARED VALUES rather than compiled taste: every
timing below states what it is and why, and a project changes any of them
through its own ``tv_frame`` declaration (see ``library/tools/tv_frame.py``)
instead of forking the engine.

The vocabulary
--------------
A cathode-ray set does two different things, and they are not mirrors:

* SWITCH-ON starts from black.  The gun strikes - a bright horizontal
  line flashes at centre - the vertical deflection ramps open, and the
  phosphor bloom overshoots white for a few frames before settling.
  Three phases: ``line_frames`` (the strike), ``expand_frames`` (the
  line opens to full height), ``bloom_frames`` (the overshoot decays).

* SWITCH-OFF starts from picture.  The deflection collapses - full
  height falls to a line - the line contracts to a dot while the spot
  brightens, and the afterglow decays to black.  Three phases:
  ``collapse_frames`` (picture to line), ``dot_frames`` (line to dot
  with a brightness spike), ``decay_frames`` (glow to black).

How it is drawn
---------------
Both halves are per-clip Fusion animation on the V1 footage clip, built
by ``library/tools/fusion/effects.fx.tv_power_head`` (switch-on, at the
reel's first clip) and ``fx.tv_power_tail`` (switch-off, at the reel's
last clip): a ``Crop`` node animates the vertical collapse/expand and a
``BrightnessContrast`` node carries the strike spike and the decay.
``library/tools/fusion/comp_builder.build_effect_comp`` dispatches on the
``tv_power_head`` / ``tv_power_tail`` keys, so the timings travel as
ordinary effect params and the .comp carries no value this module did
not declare.

The V2 frame asset is NOT animated: it is the set, and the set does not
flicker when the set turns on.  Only the picture inside it powers.

Where the numbers come from
----------------------------
Proposed by the worker on 2026-09-09 from observed CRT switch behaviour,
at 30 fps.  The whole animation is 18 frames (0.6 s) each way: long
enough to read as a switch rather than a cut, short enough not to eat
the bookends (the Lucie intro card alone holds 3.0 s).  Change any of
them - they are configuration, not constants.
"""

from __future__ import annotations

# ── Switch-on timings (frames at 30 fps) ──────────────────────────
#
# The gun strike reads as a flash, so it is SHORT: 4 frames (~130 ms)
# is one full blink.  Longer would read as a held line fault, not a
# set waking up.
SWITCH_ON_LINE_FRAMES = 4
"""The phosphor strike: a bright line held at centre before it opens."""

# The vertical deflection ramp on a real set is near-instant, but an
# instant open reads as a cut.  6 frames (~200 ms) is a visible ramp
# that still feels electromechanical rather than eased.
SWITCH_ON_EXPAND_FRAMES = 6
"""The line opens to full picture height."""

# Phosphor persistence overshoots white and falls off over a few
# hundred milliseconds.  8 frames (~270 ms) lets the bloom visibly
# decay instead of stepping off.
SWITCH_ON_BLOOM_FRAMES = 8
"""The white overshoot decays back to neutral picture."""

# ── Switch-off timings (frames at 30 fps) ─────────────────────────
#
# The collapse mirrors the expand: 6 frames (~200 ms) from full height
# to a line, the same electromechanical rate in reverse.
SWITCH_OFF_COLLAPSE_FRAMES = 6
"""Full picture falls to a horizontal line."""

# The line-to-dot contraction is the fastest thing on screen - the spot
# is at its brightest here - so it gets the fewest frames: 3 (~100 ms).
SWITCH_OFF_DOT_FRAMES = 3
"""The line contracts to a dot while the spot spikes bright."""

# The afterglow is what sells "tube": it lingers past the dot.  9
# frames (~300 ms) is the longest phase, because a cut to black the
# moment the dot dies reads as a switch, not a set.
SWITCH_OFF_DECAY_FRAMES = 9
"""The dot's glow decays to black."""

# What the brightness does at each extreme.  The strike/dot spike is a
# gain of ~2x - clearly hotter than white without clipping the whole
# ramp into a flat field - and the tail ends at 0.0 (no signal).
SWITCH_ON_STRIKE_GAIN = 2.2
"""BrightnessContrast gain at the switch-on strike."""

SWITCH_OFF_DOT_GAIN = 2.5
"""BrightnessContrast gain at the switch-off dot, before the decay."""

# How far the SWITCH-ON closes at the strike.  CropTop/CropBottom meet
# at 0.40, keeping a fifth of the picture - not 0.49's 2% sliver.
#
# Why 0.40, recorded 2026-09-10 after the captain judged the sliver
# opening ("keep the effect, soften the opening sliver"): the strike
# reads from the gain spike (2.2x, untouched), so the crop no longer
# needs to close to a line to sell it; an 80% collapse still reads as
# a switch while the opening frame shows picture, not black.  0.49 was
# a RENDER guard (stop short of 0.5, where some Resolve builds flash
# the uncropped picture), never a look - and 0.40 keeps ten times that
# guard's margin (0.10 vs 0.01).  The phase lengths are untouched, so
# the bloom still settles by frame 18 and every window gate holds.
#
# A project changes it through its own ``tv_frame`` declaration
# (``power: {switch_on: {collapse_crop: ...}}``); 0.49 stays available
# to any project that wants the old strike back.  The switch-OFF keeps
# the shared COLLAPSE_CROP below: the captain has not judged that half.
SWITCH_ON_COLLAPSE_CROP = 0.40
"""The CropTop/CropBottom value the switch-on holds before it opens."""

# The bounds a declared switch-on depth is held to.  0.0 is a pure
# gain flash with no collapse - coherent, so allowed.  0.5 is the
# divide-by-nothing the COLLAPSE_CROP comment guards against, so the
# bound is exclusive: 0.5 itself raises rather than degrading into a
# one-frame flash of uncropped picture.
SWITCH_ON_COLLAPSE_MIN = 0.0
SWITCH_ON_COLLAPSE_MAX = 0.5

# How far the SWITCH-OFF collapses.  CropTop/CropBottom meet at 0.49,
# not 0.5: a fully closed crop is a divide-by-nothing some Resolve
# builds render as a one-frame flash of the uncropped picture.  This
# half is UNJUDGED - the captain has not ruled on the switch-off - so
# its depth stays exactly where it was.
COLLAPSE_CROP = 0.49
"""The CropTop/CropBottom value of the fully collapsed line."""

# How small the dot gets.  A uniform Transform Size of 0.05 is a few
# pixels on a 4K frame: a point of light, not a thumbnail.
DOT_SIZE = 0.05
"""The uniform Transform Size of the switch-off dot."""


def switch_on_frames(line=None, expand=None, bloom=None,
                     collapse_crop=None) -> dict:
    """The switch-on timing as data, with declared defaults fillable.

    ``collapse_crop`` is the depth, not a length: how far the picture
    closes at the strike (0.40 keeps a fifth).  It travels in this
    mapping so a project declares it the same way it declares the
    phase lengths - but it is NOT summed into the total.
    """
    return {
        "line_frames": SWITCH_ON_LINE_FRAMES if line is None else int(line),
        "expand_frames": SWITCH_ON_EXPAND_FRAMES if expand is None else int(expand),
        "bloom_frames": SWITCH_ON_BLOOM_FRAMES if bloom is None else int(bloom),
        "collapse_crop": (SWITCH_ON_COLLAPSE_CROP if collapse_crop is None
                          else float(collapse_crop)),
    }


def switch_off_frames(collapse=None, dot=None, decay=None) -> dict:
    """The switch-off timing as data, with declared defaults fillable."""
    return {
        "collapse_frames": SWITCH_OFF_COLLAPSE_FRAMES if collapse is None else int(collapse),
        "dot_frames": SWITCH_OFF_DOT_FRAMES if dot is None else int(dot),
        "decay_frames": SWITCH_OFF_DECAY_FRAMES if decay is None else int(decay),
    }


def switch_on_total(timing: dict | None = None) -> int:
    """Total switch-on length in frames (18 by default: 0.6 s at 30 fps).

    Sums the phase LENGTHS only: ``collapse_crop`` is a depth, and
    adding a crop fraction to a frame count would move the window every
    time the look changes.
    """
    t = timing or switch_on_frames()
    return (int(t["line_frames"]) + int(t["expand_frames"])
            + int(t["bloom_frames"]))


def switch_off_total(timing: dict | None = None) -> int:
    """Total switch-off length in frames (18 by default: 0.6 s at 30 fps)."""
    t = timing or switch_off_frames()
    return int(t["collapse_frames"]) + int(t["dot_frames"]) + int(t["decay_frames"])


def validate_collapse(value, source: str) -> float:
    """A declared switch-on depth as a crop fraction, or raise.

    Out of range is a mistake, not something to clamp: 0.5 and above
    is the divide-by-nothing COLLAPSE_CROP guards against (some
    Resolve builds flash the uncropped picture there), and clamping
    ``collapse_crop: 0.9`` down to it would ship exactly that flash.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"tv power switch_on.collapse_crop in {source} must be a "
            f"number between {SWITCH_ON_COLLAPSE_MIN} and "
            f"{SWITCH_ON_COLLAPSE_MAX} (exclusive), got "
            f"{type(value).__name__}: {value!r}"
        )
    depth = float(value)
    if not (SWITCH_ON_COLLAPSE_MIN <= depth < SWITCH_ON_COLLAPSE_MAX):
        raise ValueError(
            f"tv power switch_on.collapse_crop in {source} must be "
            f"between {SWITCH_ON_COLLAPSE_MIN} and "
            f"{SWITCH_ON_COLLAPSE_MAX} (exclusive), got {depth!r}: "
            f"0.5 closes the crop entirely, which some Resolve builds "
            f"render as a one-frame flash of the uncropped picture."
        )
    return depth


def validate_timing(value, source: str, half: str) -> dict:
    """A declared ``power`` mapping as validated timing dicts, or raise.

    ``half`` is ``"switch_on"``, ``"switch_off"`` or ``"both"``.  A
    non-mapping, a negative frame count, or an unknown key raises naming
    *source*: a power declaration that is silently dropped is an
    animation the editor believes shipped.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TypeError(
            f"tv power declaration in {source} must be a mapping, got "
            f"{type(value).__name__}: {value!r}"
        )
    out: dict = {}
    for key in ("switch_on", "switch_off"):
        if key not in value:
            continue
        if half != "both" and half != key:
            raise ValueError(
                f"tv power declaration in {source}: {key!r} is not valid here"
            )
        phase = value[key]
        if not isinstance(phase, dict):
            raise TypeError(
                f"tv power {key!r} in {source} must be a mapping, got "
                f"{type(phase).__name__}: {phase!r}"
            )
        defaults = switch_on_frames() if key == "switch_on" else switch_off_frames()
        merged = dict(defaults)
        for k, v in phase.items():
            if k not in defaults:
                raise ValueError(
                    f"tv power {key!r} in {source} names unknown timing "
                    f"{k!r}; known: {sorted(defaults)}"
                )
            if k == "collapse_crop":
                merged[k] = validate_collapse(v, source)
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(
                    f"tv power {key}.{k} in {source} must be a non-negative "
                    f"frame count, got {v!r}"
                )
            merged[k] = int(v)
        out[key] = merged
    unknown = set(value) - {"switch_on", "switch_off"}
    if unknown:
        raise ValueError(
            f"tv power declaration in {source} names unknown "
            f"halves {sorted(unknown)}; known: ['switch_on', 'switch_off']"
        )
    return out
