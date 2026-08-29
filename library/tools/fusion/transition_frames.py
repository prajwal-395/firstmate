"""Count the frames a transition is DRAWN on, off the comp itself.

A transition plan is easy to check and says nothing: 001's plan was
correct on every run it has ever made, and the picture still carried a
full-strength blur across two whole clips.  The only honest question is
how many frames the effect was actually on, so this reads the comp the
renderer writes and evaluates its splines over the frames Resolve renders
for that clip.

It measures the renderer and stays out of it: nothing in the run path
imports this.

A spline is NEUTRAL where the effect is not drawn, and which end carries
the neutral value is decided by the half rather than by a table of node
names - a head ramps from full strength to neutral, a tail from neutral
to full.  So the neutral value is the LAST keyframe's for a head and the
FIRST keyframe's for a tail, and that holds for all four drawable types
without naming any of them.
"""

from __future__ import annotations

import re

#: Values are serialized to six decimal places; the smallest step any
#: eased ramp takes is 0.010926.
NEUTRAL_EPSILON = 1e-6

_SPLINE = re.compile(
    r"(\w+) = BezierSpline \{.*?KeyFrames = \{(.*?)\n\s*\}", re.S)
_KEY = re.compile(r"\[(-?\d+)\] = \{ ([-0-9.eE+]+)")


def parse_splines(comp_text: str) -> dict[str, list[tuple[int, float]]]:
    """Every BezierSpline in a serialized comp, as sorted keyframes."""
    out = {}
    for name, body in _SPLINE.findall(comp_text):
        keys = [(int(f), float(v)) for f, v in _KEY.findall(body)]
        if keys:
            out[name] = sorted(keys)
    return out


def transition_splines(comp_text: str) -> dict[str, list[tuple[int, float]]]:
    """The splines a transition half animates.

    Every transition builder names its nodes ``*Trans*`` - ``TransTF``,
    ``TransDBlur``, ``TransDefocus``, ``TransFlash``, ``MergeTrans`` - so
    a look's or a vignette's animation is not counted as a transition.
    """
    return {n: k for n, k in parse_splines(comp_text).items() if "Trans" in n}


def value_at(keys: list[tuple[int, float]], frame: int) -> float:
    """A spline's value at one frame: linear inside, flat outside.

    Flat extrapolation is the whole defect. Fusion holds the nearest
    keyframe's value for every frame beyond the key range, so a ramp
    written outside the frames a clip plays is not a ramp that does
    nothing - it is the effect held at full strength for the whole clip.
    """
    if frame <= keys[0][0]:
        return keys[0][1]
    if frame >= keys[-1][0]:
        return keys[-1][1]
    for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
        if f0 <= frame <= f1:
            if f1 == f0:
                return v1
            return v0 + (v1 - v0) * (frame - f0) / (f1 - f0)
    return keys[-1][1]


def neutral_value(keys: list[tuple[int, float]], half: str) -> float:
    """The value this spline carries where the effect is not drawn."""
    if half == "head":
        return keys[-1][1]
    if half == "tail":
        return keys[0][1]
    raise ValueError(f"half must be 'head' or 'tail', not {half!r}")


def drawn_frames(keys: list[tuple[int, float]], played_len: int,
                 half: str) -> list[int]:
    """Which of the clip's played frames carry the effect at all.

    ``played_len`` is how many frames Resolve renders for this clip, and
    comp frame 0 is its first played frame - see ``played_window``.
    """
    neutral = neutral_value(keys, half)
    return [f for f in range(played_len)
            if abs(value_at(keys, f) - neutral) > NEUTRAL_EPSILON]
