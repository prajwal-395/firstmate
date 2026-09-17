"""logo_bulb.py - the closing animation: navy, arrival, one flash, black.

The captain, 2026-09-17, on a blue ``feedback`` marker placed on
``logo_reveal_23976.mov`` in Reel 13, after watching the rebuilt reels:

    *"this ending animation is not what i had in mind. i wanted to have
    like a darker color like the lucie dark blue or just like black, and
    then for the logo to be animated in and when it is animated in
    completely it glows in a quick soft flash like a light bulb since
    that is what the logo is made to resemble, and then the animation of
    the logo just fades out to nothing"*

    *"and when this animtation is fixed, it is something that applies to
    all of the reels we have already built and will be building"*

Shown the result on the Lucie navy and on black and asked which ships,
he chose neither and answered a third way:

    *"niether really, just scrap the old versions, i like the glow of
    the new version but i want it applied to the Lucie Navy and then
    fade to black to end out the animation at the end"*

That is FIVE beats, in order, and this module is those five beats:

===  ===================================================================
1    a DARK GROUND - a deliberate dark field, not transparency over
     whatever the reel happens to end on. On Lucie it is the navy, and
     it is now a beat with a beginning and an END
2    the mark ARRIVES - the delivered animation's own draw-on, untouched
3    ONE flash AT COMPLETION - a bulb switching on, not a hold
4    mark and light FADE OUT TO NOTHING
5    the GROUND GOES WITH THEM - the field travels to black on the same
     slope, so the animation ends on a frame that is black and holds
     nothing
===  ===================================================================

Beat 5 is the second ruling, and it is one gesture rather than two:
:func:`picture_fade` is the curve, and the ground, the mark and the
light all ride it. There is no separate ground timing to tune, and no
second slope to get wrong.

And the base has to stay a base
-------------------------------
He attached a picture with that ruling: the bulb on a warm amber field
with its dark navy SCREW BASE clearly visible - not the brand lockup,
which is an orange C with rays on transparency and no base at all. On
the navy ground put to him the base was 2.4 dE off the field, which is
the just-noticeable difference, so the only object left was the glowing
filament and a bulb with no base stops reading as a bulb - against his
own stated reason for wanting a flash, *"like a light bulb since that
is what the logo is made to resemble"*.

**The amber field and the pale mark in that picture are NOT read as a
colour instruction.** He said Lucie Navy in words and words beat a
picture. What is read from it is that the base must stay readable, and
:data:`SEPARATION_FLOOR` is that requirement as a number:
:data:`FIELD_LIFT` lifts the declared ground's own VALUE behind the
lockup until the base clears it. The mark's colour and the declared
navy are both untouched - a lift is not a different blue, and it is
exactly zero on a black ground, where a navy base already reads.

What this supersedes, and why that is not a contradiction
---------------------------------------------------------
Earlier the same day the captain was asked, in a survey against a
side-by-side over a dark ground, whether to promote the relit logo
(:mod:`library.tools.logo_relight`, PR 1191). He answered *"Promote the
relit one"*, with core light *"A little, as built"*. He then saw it
inside a reel and asked for something different.

**The newer ruling stands, and it supersedes that approval.** It is a
revision on better information - a side-by-side shows you a light, a reel
shows you an ending - and no later lane should restore the promoted
relight's envelope on the strength of the survey. What it supersedes is
narrow and worth stating exactly:

- **superseded**: ``logo_relight.intensity_envelope`` - surge on growth,
  release, then HOLD at :data:`~library.tools.logo_relight.GLOW_GAIN`
  for as long as the mark is up. The captain saw a sustained glow and
  asked for a flash.
- **superseded**: transparency as the ground. The delivered asset
  carries alpha and is composited over the reel's last frames; he asked
  for a dark field.
- **superseded**: the delivered tail, which stops at 0.164 alpha rather
  than reaching zero. He asked for "fades out to nothing", and the
  delivered animation never gets there.
- **KEPT, untouched**: every measurement in ``logo_relight`` about how
  light behaves - the ink/halo split, the navy base not emitting, the
  four fitted falloff octaves, additive compositing, the two
  temperatures, the 16-bit pipe and the dither. That work was right and
  this module imports it rather than restating it.
- **KEPT, and not re-opened**: ``CORE_LIGHT`` at 0.05. He accepted a
  little light landing on the mark itself, as built.
- **KEPT**: the 1080x1920 frame, the 23.976 conform (PR 1181), the
  mark's shape, its choreography, its colour and the 72-frame length.
  Every reel already built has a 3.003s slot for this asset, so the new
  one is a straight swap.

The envelope, and the tension in "quick soft flash"
---------------------------------------------------
"Quick" and "soft" pull against each other: a quick flash wants a short
attack, and a short attack is what makes a hard edge. Splitting the
difference gives a medium flash that is neither. **A fast rise with a
slower release reads as both** - that is what a filament actually does,
and it is what this module renders:

- the rise is :data:`ATTACK_SECONDS` long and it is a smoothstep, so it
  leaves zero and reaches the peak with zero slope at both ends. Quick,
  with no edge at either end of the rise.
- the peak lands ON the completion frame, measured
  (:func:`completion_index`), never keyframed.
- the release is exponential, reaching a tenth in
  :data:`RELEASE_SECONDS` - 2.4x the rise. That is what a hot thing
  cooling does, and it is the half that carries "soft".

Total event: 0.17s up, 0.40s down, inside a 3.003s animation.

Between flashes the light does not go to zero - it sits at
:data:`BASE_LIGHT`, a fifth of the level the captain rejected as a hold.
A filament being drawn on carries some light; at zero the mark would be
flat paint for two of its three seconds. The flash is
:data:`FLASH_LIGHT`, six times the base, and that ratio is the event.

The ground
----------
**The ground is a parameter, never a colour this engine states.**
AGENTS.md 14: the engine is series-neutral and ships no artwork and no
brand colour. The default is BLACK, which is not taste - it is the
absence of a declared ground (AGENTS.md 10.5), and the captain named it
as his own second choice.

The Lucie ground comes from the Lucie brand template, by
:func:`ground_from_brand_template`, which reads
``content.bookends.end_card.props.bgColor`` - **#253746**. That is the
right key rather than a near one: it is the ground Lucie's own end card
sits on, and this is the other bookend of the same brand. The template's
``style.color_palette`` carries the same value as its third entry. The
logo artwork's own screw base is #253242, three steps away and NOT the
declared ground; it is a colour inside the mark, not a field to put the
mark on.

The fade
--------
**This module CARRIES the delivered fade to zero. It does not author
one.** It carries the GROUND on the same curve (beat 5). The delivered animation's tail is a linear ramp that stops at
0.164 of full alpha and then cuts; :func:`fade_scale` re-maps that ramp
so the same slope reaches exactly zero, which is the captain's own
pacing finished rather than a curve this module chose. A source whose
tail does not actually descend is REFUSED
(:data:`TAIL_CEILING`, :class:`SourceNotClosed`) rather than being given
an ending the engine made up.

Looking at it
-------------
The last version of this asset was judged from an isolated side-by-side
and then rejected on sight in a reel. So this module renders both, and
:func:`over_tail` is the one that matters: the real closing frames of a
real reel, then this animation where it will sit.

    python3 -m library.tools.logo_bulb --source <in.mov> --out <out.mov>
        [--ground-from-template library/templates/lucie_client.yaml]
    python3 -m library.tools.logo_bulb --source <in.mov> \\
        --contact-sheet <sheet.png>
    python3 -m library.tools.logo_bulb --source <in.mov> \\
        --in-reel <reel.mp4> <out.mp4> --keep 40.5 43.42

``tests/test_logo_bulb.py``.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np

from library.tools.logo_relight import (
    PIPE_FORMAT,
    WORKING_SIGMA,
    LightProfile,
    _dither,
    emission,
    glow_field,
    probe,
    read_frames,
    separate_ink,
)

# ── Declared beats ───────────────────────────────────────────────────

BLACK: Tuple[float, float, float] = (0.0, 0.0, 0.0)
"""The ground when nothing declares one.

Not a choice of colour - the absence of one (AGENTS.md 10.5). A project
that wants a ground states it, and the captain named black himself as
the alternative to the Lucie navy."""

COMPLETE_FRACTION = 0.995
"""When the mark counts as "animated in completely", as a fraction of
the ink it ever reaches.

Measured on ``logo_reveal_23976.mov``: ink area rises frame over frame
to 16,838 at frame 49 and then only ever falls back (16,818, 16,792,
16,784...). Frame 48 is at 0.972 of the peak and frame 49 at 1.000, so
any threshold between them picks the same frame - the gap is 28 times
the tolerance this number leaves. The measurement is TOTAL ink, not
emitting ink, because the wordmark and the screw base complete after the
filament does and the captain's word is "it", the whole lockup."""

ATTACK_SECONDS = 0.167
RELEASE_SECONDS = 0.40
"""The two halves of "quick soft flash", and the reason they differ.

``ATTACK_SECONDS`` is four frames at 23.976 - a rise you read as sudden.
It is applied as a smoothstep, which leaves the base and arrives at the
peak with zero slope, so "quick" costs no hard edge at either end of it.

``RELEASE_SECONDS`` is how long the flash takes to fall to a TENTH,
exponentially: 2.4x the rise, and the reason the whole thing reads as
soft rather than as a strobe. A filament cools slower than it lights,
and an exponential is the shape of cooling. Symmetry was available and
is wrong: it gives a swell, not a switch."""

BASE_LIGHT = 0.10
"""How lit the mark is when it is not flashing, in the units
:data:`~library.tools.logo_relight.GLOW_GAIN` is stated in - the alpha
the light reaches in the 0-2 px band.

The level the captain rejected was 0.50, held for as long as the mark
was up. This is a fifth of it. Zero was the other option and it is
worse: the mark would be flat paint for the two seconds either side of
the flash, and a filament being drawn on does carry light."""

FLASH_LIGHT = 0.60
"""The peak of the flash, in the same units. Six times
:data:`BASE_LIGHT`, and that RATIO is the event - a bulb switching on is
read as a step in brightness, not as an absolute. It sits just above the
0.50 the captain saw held, so the instant of the flash is as bright as
the rejected version's whole hold and everything around it is far
dimmer."""

TAIL_CEILING = 0.5
"""How far a source's own tail must descend before this module will
carry it to zero, as a fraction of the source's peak alpha.

``logo_reveal_23976.mov`` ends at 0.164 of peak - a real fade, cut off
before it arrives. Below this line there is a fade to finish; at or
above it there is nothing to finish, and re-mapping a tail that barely
moves would compress the whole ending into a frame or two. A source like
that is REFUSED, because authoring an ending is the captain's decision
and not this module's (AGENTS.md 10.5)."""


FIELD_SPREAD = 0.5
"""How wide the field lift is, as a fraction of the completed mark's
larger side.

The lift exists so that a dark object has something to sit against, and
it must therefore carry no edge of its own inside the lockup. At half
the mark's larger side - 216 px for ``logo_reveal_23976.mov``'s
433 x 373 lockup - the pool's own falloff is slower than the mark is
wide: across the whole screw base it runs 0.79 to 0.93 of its peak,
which is a field and not an edge, and it is 0.00002 at every corner of
the 1080x1920 frame, so the declared ground is exactly itself
everywhere but behind the mark. A tighter spread reads as a second
glow; a wider one stops being behind the mark at all."""

FIELD_LIFT = 0.30
"""How much the declared ground's own value is lifted, at the peak of
the pool behind the mark.

It MULTIPLIES the declared ground, so the hue and the saturation are
untouched and only the value moves: #253746 reaches (48, 72, 91) dead
behind the lockup and is exactly #253746 everywhere the pool has died.
On a BLACK ground it is exactly zero - nothing times anything is
nothing - which is right, because a navy base already reads on black
and is the reason this number exists at all.

Solved, not chosen. Swept against :data:`SEPARATION_FLOOR` on
``logo_reveal_23976.mov``, the worst full-presence frame measures 3.14
dE at no lift, then 4.94, 5.89, 6.85, 7.82 and **8.79** at 0.30 - the
first tested value over the 8.5 floor, and the worst frame is 44 every
time, where the base lands. Linearly the floor is crossed at about
0.29; 0.30 is the hundredth above it, because a value solved to sit a
twentieth of a dE over a threshold is a gate that flips on rounding.

**Why the FIELD and not the base.** Letting the base catch more of the
mark's own light is the physically obvious move - it does not emit, so
the light it receives is the only light on it - and it goes BACKWARDS:
measured on frame 44, the separation falls 3.14, 2.80, 2.48 as the base
catches 0.05, 0.50 and 1.00 of the light. The base is read as a
SILHOUETTE against a lit field, so lighting the base closes the very
gap it is read by. Lifting the base's own value is the third route and
it repaints the mark, which is the captain's.
"""

SEPARATION_FLOOR = 8.5
"""How far the mark's non-emitting base must sit from the field around
it, as CIE76 dE between the base's interior and the ground within 8 px
of it (:func:`base_separation`).

**Measured on the navy render the captain rejected**, frame by frame:
the base is drawn from frame 44, and its separation from the field runs
3.1 at arrival, 8.5 at the flash, and 4.0 by the end of the release -
against the BARE navy it never leaves 2.4-2.9, which is the just-
noticeable difference and is why the base sank into the field. The one
moment it does read is the flash, and that moment is one he has already
seen, so it is the bar: **the base should read throughout as well as it
already reads at the peak of the flash.** A number off a perception
table would have been a guess about his screen; this one is off his own
asset.

The floor is checked where the mark is FULLY PRESENT. Past that the
mark is deliberately going out (beat 4), and a base that still read
there would be the fade failing."""


class SourceNotClosed(ValueError):
    """The source cannot carry the closing animation asked of it."""


@dataclass(frozen=True)
class ClosingProfile:
    """One complete statement of the closing animation.

    The light physics come through on :attr:`light` exactly as
    ``logo_relight`` measured them; everything else here is the envelope
    and the ground, which are what the captain's 2026-09-17 ruling
    replaced.
    """

    light: LightProfile = field(default_factory=LightProfile)
    ground: Tuple[float, float, float] = BLACK
    complete_fraction: float = COMPLETE_FRACTION
    attack_seconds: float = ATTACK_SECONDS
    release_seconds: float = RELEASE_SECONDS
    base_light: float = BASE_LIGHT
    flash_light: float = FLASH_LIGHT
    tail_ceiling: float = TAIL_CEILING
    field_spread: float = FIELD_SPREAD
    field_lift: float = FIELD_LIFT
    separation_floor: float = SEPARATION_FLOOR

    def with_ground(self, ground: Tuple[float, float, float]
                    ) -> "ClosingProfile":
        """The same closing, on a different field."""
        return replace(self, ground=ground)


# ── Where the ground comes from ──────────────────────────────────────

def parse_ground(text: str) -> Tuple[float, float, float]:
    """``"#253746"`` or ``"black"`` to linear-ish RGB in [0, 1].

    No colour management: the mark's own RGB comes off the file in the
    same encoding, so a hex the brand states and a pixel the artwork
    carries land on the same scale.
    """
    value = text.strip().lower()
    if value == "black":
        return BLACK
    value = value.lstrip("#")
    if len(value) != 6 or any(c not in "0123456789abcdef" for c in value):
        raise SourceNotClosed(
            f"a ground is #rrggbb or 'black', not {text!r}")
    return tuple(int(value[i:i + 2], 16) / 255.0  # type: ignore[return-value]
                 for i in (0, 2, 4))


def ground_from_brand_template(path: str) -> Tuple[float, float, float]:
    """The ground a brand template declares for its own end card.

    ``content.bookends.end_card.props.bgColor`` - the field the client's
    other bookend sits on. A template that declares no end-card ground
    is REFUSED rather than defaulted, because this function exists to
    report a declaration and a guess is not one.
    """
    import yaml

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    bookends = ((data.get("content") or {}).get("bookends") or {})
    props = ((bookends.get("end_card") or {}).get("props") or {})
    declared = props.get("bgColor")
    if not declared:
        raise SourceNotClosed(
            f"{path} declares no content.bookends.end_card.props.bgColor, "
            "so it states no ground to close on")
    return parse_ground(str(declared))


# ── The four beats, as measurements ──────────────────────────────────

def mark_measurements(frames: Sequence[np.ndarray], profile: ClosingProfile
                      ) -> Tuple[List[float], List[float]]:
    """(ink area per frame, peak ink alpha per frame).

    Both are taken off the SEPARATED ink, so the authored halo counts
    towards neither - a collar that grows with the mark would otherwise
    put the completion frame wherever the blur happened to settle.
    """
    areas, levels = [], []
    for rgba in frames:
        _, ink_alpha = separate_ink(rgba, profile.light)
        areas.append(float(ink_alpha.sum()))
        levels.append(float(ink_alpha.max()))
    return areas, levels


def completion_index(areas: Sequence[float], profile: ClosingProfile) -> int:
    """The frame the mark finishes arriving on.

    The FIRST frame within :data:`COMPLETE_FRACTION` of the most ink the
    sequence ever carries. First, not brightest: the mark holds after it
    completes and drifts a few tenths of a percent while it does, and
    the flash belongs to the arrival rather than to whichever held frame
    happens to measure highest.
    """
    if not areas:
        raise SourceNotClosed("an empty sequence has no completion frame")
    peak = max(areas)
    if peak <= 0.0:
        raise SourceNotClosed("no frame of the source carries any ink")
    threshold = peak * profile.complete_fraction
    for index, area in enumerate(areas):
        if area >= threshold:
            return index
    raise SourceNotClosed("no frame reached the completion threshold")


def flash_envelope(count: int, completion: int, rate: float,
                   profile: ClosingProfile) -> List[float]:
    """0 to 1 to 0: the bulb switching on, once.

    Quick up, slower down, peaking exactly on ``completion``. Stated in
    SECONDS and converted here, so the shape is the same at 23.976 as at
    any other rate the asset is ever conformed to.
    """
    if rate <= 0.0:
        raise SourceNotClosed(f"a rate must be positive, got {rate}")
    attack = max(1, round(profile.attack_seconds * rate))
    decay_frames = max(1.0, profile.release_seconds * rate)
    retention = 0.1 ** (1.0 / decay_frames)

    out: List[float] = []
    for index in range(count):
        if index <= completion:
            t = (index - (completion - attack)) / float(attack)
            t = min(1.0, max(0.0, t))
            out.append(t * t * (3.0 - 2.0 * t))
        else:
            out.append(retention ** (index - completion))
    return out


def intensity_envelope(count: int, completion: int, rate: float,
                       profile: ClosingProfile) -> List[float]:
    """The flash, in the units the light field is gained in."""
    span = profile.flash_light - profile.base_light
    return [profile.base_light + span * value
            for value in flash_envelope(count, completion, rate, profile)]


def picture_fade(levels: Sequence[float], profile: ClosingProfile
                 ) -> List[float]:
    """How much of the picture is still standing, frame by frame.

    1.0 for as long as the mark is up, then the delivered fade re-mapped
    onto [0, 1] - ``(level - last) / (1 - last)`` - so that the
    captain's own slope arrives at exactly zero instead of stopping at
    the 0.164 the delivered file cuts off on.

    **This is the whole ending, not just the mark's half of it.** The
    captain, 2026-09-17, having been shown the navy and the black
    ground and asked which ships: *"i like the glow of the new version
    but i want it applied to the Lucie Navy and then fade to black to
    end out the animation at the end"*. Today the mark goes and the
    navy field stays to the last frame; he wants the thing to END ON
    BLACK. So the ground travels on this curve too
    (:func:`bulb_sequence`), and the ending is ONE gesture rather than
    two: the same slope, over the same frames, taking the mark, its
    light and the field it sits on to nothing together.

    Darkening the ground AFTER the mark had gone was the alternative
    and it is worse on this source: the mark is not out until frame 70
    of 72, so an ending that starts there has two frames to travel a
    whole field and reads as a cut. Darkening it over a longer,
    earlier span was the other, and it takes the navy away while the
    flash is still happening - the flash is his and it happens on the
    navy.
    """
    if not levels:
        return []
    top = max(levels)
    if top <= 0.0:
        raise SourceNotClosed("no frame of the source carries any mark")

    last = levels[-1] / top
    if last <= 0.0:
        return [min(1.0, value / top) for value in levels]
    if last > profile.tail_ceiling:
        raise SourceNotClosed(
            f"the source's last frame is at {last:.3f} of its peak alpha, "
            f"above the {profile.tail_ceiling} this module will carry to "
            "zero: there is no delivered fade to finish, and authoring "
            "one is not this module's decision")

    start = max(index for index, value in enumerate(levels)
                if value >= top * (1.0 - 1e-6))
    out: List[float] = []
    for index, value in enumerate(levels):
        if index <= start:
            out.append(1.0)
            continue
        out.append(max(0.0, (value / top - last) / (1.0 - last)))
    return out


def fade_scale(levels: Sequence[float], profile: ClosingProfile
               ) -> List[float]:
    """What to multiply the mark's alpha by so its own fade reaches zero.

    The ratio between the level :func:`picture_fade` asks for and the
    level the frame actually carries. Before the fade starts the ratio
    is exactly 1, so nothing the captain timed moves; through the fade
    the slope steepens just enough to arrive at nothing.

    The light is not scaled here and does not need to be: it is driven
    by the emission of an ink whose alpha this ratio has already scaled,
    so mark and light go out together.
    """
    if not levels:
        return []
    top = max(levels)
    standing = picture_fade(levels, profile)
    out: List[float] = []
    for value, want in zip(levels, standing):
        normalised = value / top
        # Only ever scales DOWN: before the fade the picture is asked
        # for in full and the frame already carries it.
        out.append(1.0 if want >= normalised or normalised <= 0.0
                   else want / normalised)
    return out


# ── The field the mark sits on ───────────────────────────────────────

def _wide_blur(values: np.ndarray, sigma: float) -> np.ndarray:
    """A Gaussian too wide to compute at full resolution.

    The same decimation
    :func:`library.tools.logo_relight.glow_field` uses on its far
    octaves, for the same reason: a sigma of 200 px over a 1080x1920
    frame is a 1600-tap kernel and, by construction, a signal with no
    detail finer than 200 px in it. Pre-blurred so the decimation has
    nothing to alias, finished at the reduced resolution, exact because
    Gaussians compose in quadrature.
    """
    from scipy import ndimage

    if sigma <= 0.0:
        raise SourceNotClosed(f"a spread must be positive, got {sigma}")
    step = max(1, int(sigma / WORKING_SIGMA))
    if step == 1:
        return ndimage.gaussian_filter(values, sigma=sigma, mode="constant")
    guard = 0.5 * step
    small = ndimage.gaussian_filter(values, sigma=guard, mode="constant")
    small = ndimage.zoom(small, 1.0 / step, order=1, mode="constant")
    small = ndimage.gaussian_filter(
        small, sigma=(sigma ** 2 - guard ** 2) ** 0.5 / step,
        mode="constant")
    return ndimage.zoom(small, (values.shape[0] / small.shape[0],
                                values.shape[1] / small.shape[1]),
                        order=1, mode="nearest")


def field_geometry(frames: Sequence[np.ndarray], profile: ClosingProfile
                   ) -> Tuple[float, float]:
    """(spread in pixels, normaliser) for the lift, off the COMPLETED mark.

    Both come from the artwork rather than from a number anyone picked:
    the spread is :data:`FIELD_SPREAD` of the completed mark's larger
    side, and the normaliser is the peak that spread reaches on the
    completed mark, so the pool is 1.0 behind a mark that is all the way
    in and proportionally less behind one still arriving.

    Taken ONCE, on the completion frame. Re-measuring the extent per
    frame would make the pool breathe against a mark that is only being
    drawn on.
    """
    areas, _ = mark_measurements(frames, profile)
    _, ink_alpha = separate_ink(frames[completion_index(areas, profile)],
                                profile.light)
    rows, columns = np.nonzero(ink_alpha > 0.0)
    if rows.size == 0:
        raise SourceNotClosed("the completed mark covers no pixel")
    spread = profile.field_spread * max(rows.max() - rows.min() + 1,
                                        columns.max() - columns.min() + 1)
    peak = float(_wide_blur(ink_alpha, spread).max())
    if peak <= 0.0:
        raise SourceNotClosed("the field behind the completed mark is empty")
    return float(spread), peak


def field_pool(ink_alpha: np.ndarray, spread: float, normaliser: float
               ) -> np.ndarray:
    """0 to 1: how much of the field lift stands at each pixel.

    The mark's own shape, spread until it is a field rather than a
    second glow. It needs no centre and no radius of its own - it IS
    the mark, blurred - so it arrives as the mark draws on, sits where
    the mark sits, and leaves as the mark's alpha is faded out.
    """
    if normaliser <= 0.0:
        raise SourceNotClosed(
            f"a normaliser must be positive, got {normaliser}")
    return np.clip(_wide_blur(ink_alpha, spread) / normaliser, 0.0, 1.0)


# ── One frame ────────────────────────────────────────────────────────

def bulb_frame(rgba: np.ndarray, intensity: float, present: float,
               profile: ClosingProfile, standing: float = 1.0,
               pool: np.ndarray | None = None) -> np.ndarray:
    """One source frame in, one OPAQUE closing frame out.

    The order is the whole point. The ink is composited OVER the ground
    normally, because ink is an object and it occludes. The light is
    then ADDED on top of that, because light is not an object and only
    ever brightens - blending it would let a faint far field DARKEN the
    ground it falls on, which is the one thing a glow must never do.

    ``standing`` is how much of the declared ground is left
    (:func:`picture_fade`): 1.0 while the picture is up and 0.0 once it
    has gone to black. ``pool`` is the field lift
    (:func:`field_pool`), which MULTIPLIES the ground rather than
    mixing anything into it, so the declared colour's hue and
    saturation survive untouched and a black ground stays exactly black.
    """
    ink_rgb, ink_alpha = separate_ink(rgba, profile.light)
    ink_alpha = ink_alpha * present
    light = np.clip(
        glow_field(emission(ink_rgb, ink_alpha, profile.light),
                   profile.light) * intensity, 0.0, 1.0)

    mix = np.clip(light / profile.light.hot, 0.0, 1.0)[..., None]
    near = np.asarray(profile.light.near_colour, dtype=np.float64)
    far = np.asarray(profile.light.far_colour, dtype=np.float64)
    light_rgb = far + (near - far) * mix

    ground = np.asarray(profile.ground, dtype=np.float64) * standing
    if pool is not None:
        ground = np.clip(
            ground * (1.0 + profile.field_lift * pool)[..., None], 0.0, 1.0)
    alpha = ink_alpha[..., None]
    over = ink_rgb * alpha + ground * (1.0 - alpha)

    # Attenuated where it lands on the mark itself, so the mark's own
    # colour survives and its anti-aliased edge takes most of it.
    reaching = light * (1.0 - ink_alpha * (1.0 - profile.light.core_light))
    out = np.empty_like(rgba)
    out[..., :3] = np.clip(over + light_rgb * reaching[..., None], 0.0, 1.0)
    out[..., 3] = 1.0
    return out


def bulb_sequence(frames: Sequence[np.ndarray], rate: float,
                  profile: ClosingProfile) -> List[np.ndarray]:
    """The whole closing animation, envelope, fade, ground and all."""
    areas, levels = mark_measurements(frames, profile)
    completion = completion_index(areas, profile)
    intensities = intensity_envelope(len(frames), completion, rate, profile)
    presence = fade_scale(levels, profile)
    standing = picture_fade(levels, profile)
    spread, normaliser = field_geometry(frames, profile)

    out: List[np.ndarray] = []
    for rgba, intensity, present, left in zip(frames, intensities,
                                              presence, standing):
        _, ink_alpha = separate_ink(rgba, profile.light)
        pool = field_pool(ink_alpha * present, spread, normaliser)
        out.append(bulb_frame(rgba, intensity, present, profile, left, pool))
    return out


def describe(frames: Sequence[np.ndarray], rate: float,
             profile: ClosingProfile) -> dict:
    """What the five beats landed on, for the render receipt."""
    areas, levels = mark_measurements(frames, profile)
    completion = completion_index(areas, profile)
    presence = fade_scale(levels, profile)
    standing = picture_fade(levels, profile)
    lit = [index for index, value in enumerate(presence)
           if value > 0.0 and levels[index] > 0.0]
    black = [index for index, value in enumerate(standing) if value <= 0.0]
    return {
        "ground_travel_starts_frame": next(
            (index for index, value in enumerate(standing) if value < 1.0),
            -1),
        "first_black_frame": black[0] if black else -1,
        "black_frames_at_the_end": len(black),
        "frames": len(frames),
        "rate": rate,
        "ground_rgb_255": [round(c * 255) for c in profile.ground],
        "arrival_frame": next((i for i, a in enumerate(areas) if a > 0.0), -1),
        "completion_frame": completion,
        "completion_seconds": round(completion / rate, 3),
        "flash_starts_frame": max(
            0, completion - max(1, round(profile.attack_seconds * rate))),
        "flash_tenth_frame": min(
            len(frames) - 1,
            completion + round(profile.release_seconds * rate)),
        "base_light": profile.base_light,
        "flash_light": profile.flash_light,
        "source_last_frame_alpha": round(levels[-1], 4),
        "last_lit_frame": lit[-1] if lit else -1,
        "dark_frames_at_the_end": len(frames) - 1 - (lit[-1] if lit else -1),
    }


# ── The file ─────────────────────────────────────────────────────────

def render_file(source: str, destination: str,
                profile: ClosingProfile | None = None) -> dict:
    """Render the closing animation from a source reveal, to a new file.

    Rate, geometry and frame count come through unchanged - every reel
    already built has a slot exactly this long. The output is OPAQUE:
    the ground is part of the asset now, so its alpha plane is 1
    everywhere. The codec stays ProRes 4444 so the swap into a built
    timeline changes nothing but the pixels.
    """
    profile = profile or ClosingProfile()
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SourceNotClosed(f"{tool} is not on PATH")

    before = probe(source)
    frames = read_frames(source)
    width, height = int(before["width"]), int(before["height"])
    numerator, _, denominator = before["r_frame_rate"].partition("/")
    rate = float(numerator) / float(denominator or 1)

    report = describe(frames, rate, profile)
    closed = bulb_sequence(frames, rate, profile)
    report["base_separation"] = separation_report(frames, closed, profile)

    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", PIPE_FORMAT,
         "-s", f"{width}x{height}", "-framerate", before["r_frame_rate"],
         "-i", "-",
         "-i", source,
         "-map", "0:v:0", "-map", "1:a?",
         "-c:v", "prores_ks", "-profile:v", "4444",
         "-pix_fmt", "yuva444p10le", "-alpha_bits", "16",
         "-c:a", "copy",
         destination],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    generator = np.random.default_rng(0)
    try:
        for rgba in closed:
            encoder.stdin.write(
                _dither(rgba, 65535.0, generator).astype("<u2").tobytes())
        encoder.stdin.close()
    except BrokenPipeError:
        pass
    stderr = encoder.stderr.read().decode("utf-8", "replace")
    if encoder.wait() != 0:
        raise SourceNotClosed(f"the encode failed: {stderr.strip()}")

    after = probe(destination)
    for key in ("width", "height", "r_frame_rate", "nb_frames"):
        if str(before.get(key)) != str(after.get(key)):
            raise SourceNotClosed(
                f"{key} changed in the close: {before.get(key)} "
                f"-> {after.get(key)}")
    report["source"] = before
    report["closed"] = after
    return report


# ── Looking at it ────────────────────────────────────────────────────

def contact_sheet(frames: Sequence[np.ndarray], indices: Sequence[int],
                  scale: int = 3):
    """The named frames, side by side. Already opaque, so no ground."""
    from PIL import Image

    tiles = []
    for index in indices:
        eight = _dither(frames[index][..., :3], 255.0,
                        np.random.default_rng(index)).astype(np.uint8)
        tile = Image.fromarray(eight, mode="RGB")
        tiles.append(tile.resize((tile.width // scale, tile.height // scale),
                                 Image.LANCZOS))
    sheet = Image.new("RGB", (sum(t.width for t in tiles),
                              max(t.height for t in tiles)))
    offset = 0
    for tile in tiles:
        sheet.paste(tile, (offset, 0))
        offset += tile.width
    return sheet


PREVIEW_ARGS = ["-c:v", "libx264", "-crf", "16", "-preset", "medium",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                "-ar", "48000", "-ac", "2"]
"""One encode for both halves of :func:`over_tail`.

The concat demuxer joins streams, it does not convert them, so the reel
excerpt and the animation have to arrive in the same codec, pixel
format, rate and audio layout. Declaring it once is what keeps them
that way."""


def over_tail(reel: str, frames: Sequence[np.ndarray], rate: str,
              destination: str, keep_from: float, keep_to: float) -> str:
    """The real closing frames of a real reel, then this animation.

    The last version of this asset was judged from an isolated
    side-by-side and then rejected on sight the moment it played at the
    end of a reel. An isolated render cannot answer the question the
    captain actually asks of an ending, which is what it feels like
    after the shot before it - so this splices the two: ``keep_from`` to
    ``keep_to`` seconds of the reel (its picture, its switch-off, its
    audio), and then the closing animation in full.

    ``keep_to`` is where the reel's OWN closing clip starts, so the old
    one is cut off rather than played twice.
    """
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SourceNotClosed(f"{tool} is not on PATH")
    if keep_to <= keep_from:
        raise SourceNotClosed(
            f"the kept span runs {keep_from} to {keep_to}, which is empty")
    if not frames:
        raise SourceNotClosed("there is no animation to put after the reel")

    height, width = frames[0].shape[:2]
    work = Path(tempfile.mkdtemp(prefix="logo_bulb_"))
    try:
        lead = work / "lead.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-ss", f"{keep_from}",
             "-to", f"{keep_to}", "-i", reel,
             "-vf", f"scale={width}:{height},fps={rate}",
             *PREVIEW_ARGS, str(lead)],
            check=True, capture_output=True, encoding="utf-8")

        close = work / "close.mp4"
        encoder = subprocess.Popen(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{width}x{height}", "-framerate", rate, "-i", "-",
             "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-shortest", *PREVIEW_ARGS, str(close)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for index, rgba in enumerate(frames):
                encoder.stdin.write(
                    _dither(rgba[..., :3], 255.0,
                            np.random.default_rng(index))
                    .astype(np.uint8).tobytes())
            encoder.stdin.close()
        except BrokenPipeError:
            pass
        stderr = encoder.stderr.read().decode("utf-8", "replace")
        if encoder.wait() != 0:
            raise SourceNotClosed(
                f"the closing encode failed: {stderr.strip()}")

        listing = work / "parts.txt"
        listing.write_text(f"file '{lead}'\nfile '{close}'\n",
                           encoding="utf-8")
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-c", "copy", destination],
            check=True, capture_output=True, encoding="utf-8")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return destination


def band_alpha(rgba: np.ndarray, profile: ClosingProfile,
               out_to: int = 2) -> float:
    """Mean light the frame puts into the first ``out_to`` px off the ink.

    The number :data:`BASE_LIGHT` and :data:`FLASH_LIGHT` are stated in,
    read back off a rendered frame - so a claim about how bright the
    flash is can be checked rather than believed.
    """
    from scipy import ndimage

    ground = np.asarray(profile.ground, dtype=np.float64)
    lift = np.clip(rgba[..., :3] - ground, 0.0, 1.0).max(axis=-1)
    core = lift >= 0.5 * float(lift.max() or 1.0)
    if not core.any():
        return 0.0
    distance = ndimage.distance_transform_edt(~core)
    band = (~core) & (distance > 0) & (distance <= out_to)
    return float(lift[band].mean()) if band.any() else 0.0


SOLID_FRACTION = 0.95
"""Where a frame's ink stops being an edge and starts being a stroke, as
a fraction of that frame's own ink peak.

:func:`~library.tools.logo_relight.separate_ink` has already thrown the
authored halo away, so what is left is the mark and its anti-aliased
rim; this is the body inside that rim. Only
:func:`base_region` and :func:`base_separation` need it - a mean taken
across a soft edge is a mean of the edge."""

BASE_DRAWN = 0.5
"""How much of the base has to be on screen before its separation is a
number worth having, as a fraction of the base the COMPLETED mark draws.

Measured on ``logo_reveal_23976.mov``: the base region is untouched
through the whole draw-on - every frame to 43 carries exactly 0.000 of
it - and then lands nearly at once, 0.656 on frame 44, 0.821, 0.929,
0.986 and 1.000 by the completion frame. There is no frame between 0
and 0.656, so any threshold in that gap picks frame 44 and this number
is not delicate. Without one, the arriving mark's first faint pixels
measure as a base that is not there yet."""


def base_region(frames: Sequence[np.ndarray], profile: ClosingProfile
                ) -> np.ndarray:
    """Where the mark's non-emitting base is, off the COMPLETED mark.

    The base is a part of the ARTWORK, so it is identified once and not
    re-guessed per frame: the solid ink of the completed mark whose
    luminance falls below ``emit_lo`` - the same population
    :func:`~library.tools.logo_relight.emission` excludes from emitting,
    which is what makes it the thing that has no light of its own to
    read by.
    """
    areas, _ = mark_measurements(frames, profile)
    rgba = frames[completion_index(areas, profile)]
    ink_rgb, ink_alpha = separate_ink(rgba, profile.light)
    peak = float(ink_alpha.max())
    if peak <= 0.0:
        raise SourceNotClosed("the completed mark carries no ink")
    luma = (0.2126 * ink_rgb[..., 0] + 0.7152 * ink_rgb[..., 1]
            + 0.0722 * ink_rgb[..., 2])
    return (ink_alpha >= SOLID_FRACTION * peak) & (luma < profile.light.emit_lo)


def base_separation(rgba: np.ndarray, closed: np.ndarray,
                    profile: ClosingProfile, region: np.ndarray) -> float:
    """How far the mark's non-emitting base sits from the field it is on.

    CIE76 dE between the base's INTERIOR and the ground immediately
    around it, so the number is the local contrast an eye actually
    reads at that silhouette rather than a comparison with a flat
    colour the frame may not contain anywhere near the base.

    ``rgba`` is the source frame, which says how much of ``region`` is
    drawn yet; ``closed`` is the rendered frame the number is read off.
    Returns 0.0 where the base is not drawn - on this source it lands 5
    frames before the filament completes.
    """
    from scipy import ndimage

    ink_rgb, ink_alpha = separate_ink(rgba, profile.light)
    peak = float(ink_alpha.max())
    if peak <= 0.0 or not region.any():
        return 0.0
    base = region & (ink_alpha >= SOLID_FRACTION * peak)
    if base.sum() < BASE_DRAWN * region.sum():
        return 0.0

    # 2 px in, which clears the mark's own anti-aliased edge; and the
    # field within 8 px of the base that no ink reaches.
    inside = ndimage.binary_erosion(base, np.ones((5, 5)))
    around = (ndimage.binary_dilation(base, np.ones((17, 17)))
              & ~ndimage.binary_dilation(ink_alpha > 0.0, np.ones((5, 5))))
    if not inside.any() or not around.any():
        return 0.0
    return float(np.sqrt(np.square(
        _lab(closed[..., :3][inside].mean(axis=0))
        - _lab(closed[..., :3][around].mean(axis=0))).sum()))


def _lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB in [0, 1] to CIE L*a*b*, D65. Only :func:`base_separation`
    needs it: dE is the one scale on which "can you see that edge" is a
    number rather than an opinion."""
    values = np.asarray(rgb, dtype=np.float64)
    linear = np.where(values <= 0.04045, values / 12.92,
                      ((values + 0.055) / 1.055) ** 2.4)
    matrix = np.array([[0.4124, 0.3576, 0.1805],
                       [0.2126, 0.7152, 0.0722],
                       [0.0193, 0.1192, 0.9505]])
    ratio = (linear @ matrix.T) / np.array([0.95047, 1.0, 1.08883])
    f = np.where(ratio > 0.008856, np.cbrt(ratio), 7.787 * ratio + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]),
                     200 * (f[1] - f[2])])


def separation_report(frames: Sequence[np.ndarray],
                      closed: Sequence[np.ndarray],
                      profile: ClosingProfile) -> dict:
    """:data:`SEPARATION_FLOOR`, checked rather than claimed.

    Only where the mark is FULLY PRESENT: past that it is going out on
    purpose (beat 4) and a base that still read there would be the fade
    failing.
    """
    _, levels = mark_measurements(frames, profile)
    standing = picture_fade(levels, profile)
    region = base_region(frames, profile)
    measured = {index: base_separation(frames[index], closed[index],
                                       profile, region)
                for index, value in enumerate(standing)
                if value >= 1.0 - 1e-9}
    drawn = {index: value for index, value in measured.items() if value > 0.0}
    if not drawn:
        return {"base_drawn": False, "floor": profile.separation_floor}
    worst = min(drawn, key=drawn.get)
    return {
        "base_drawn": True,
        "floor": profile.separation_floor,
        "base_first_drawn_frame": min(drawn),
        "worst_separation": round(drawn[worst], 2),
        "worst_separation_frame": worst,
        "clears_floor": drawn[worst] >= profile.separation_floor,
    }


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--out")
    parser.add_argument("--ground", default="",
                        help="#rrggbb or 'black' (the default)")
    parser.add_argument("--ground-from-template", default="",
                        help="a brand template whose end card declares one")
    parser.add_argument("--contact-sheet")
    parser.add_argument("--frames", default="",
                        help="comma-separated indices for --contact-sheet")
    parser.add_argument("--in-reel", nargs=2, metavar=("REEL", "OUT_MP4"))
    parser.add_argument("--keep", nargs=2, type=float,
                        metavar=("FROM", "TO"), default=(0.0, 0.0),
                        help="seconds of REEL to keep before the animation")
    args = parser.parse_args(argv)

    if args.ground and args.ground_from_template:
        parser.error("--ground and --ground-from-template both state one")
    if args.ground_from_template:
        ground = ground_from_brand_template(args.ground_from_template)
    elif args.ground:
        ground = parse_ground(args.ground)
    else:
        ground = BLACK
        print("no ground declared: closing on black", file=sys.stderr)
    profile = ClosingProfile().with_ground(ground)

    if args.out:
        print(json.dumps(render_file(args.source, args.out, profile),
                         indent=2))

    if args.contact_sheet or args.in_reel:
        meta = probe(args.source)
        numerator, _, denominator = meta["r_frame_rate"].partition("/")
        rate = float(numerator) / float(denominator or 1)
        frames = bulb_sequence(read_frames(args.source), rate, profile)
        if args.contact_sheet:
            picked = ([int(v) for v in args.frames.split(",") if v.strip()]
                      or list(range(0, len(frames),
                                    max(1, len(frames) // 6))))
            contact_sheet(frames, picked).save(args.contact_sheet)
            print(args.contact_sheet)
        if args.in_reel:
            print(over_tail(args.in_reel[0], frames, meta["r_frame_rate"],
                            args.in_reel[1], args.keep[0], args.keep[1]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
