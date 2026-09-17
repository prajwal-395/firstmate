"""logo_bulb.py - the closing animation: dark, arrival, one flash, gone.

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

That is four beats, in order, and this module is those four beats:

===  ===================================================================
1    a DARK GROUND - a deliberate dark field, not transparency over
     whatever the reel happens to end on
2    the mark ARRIVES - the delivered animation's own draw-on, untouched
3    ONE flash AT COMPLETION - a bulb switching on, not a hold
4    mark and light FADE OUT TO NOTHING
===  ===================================================================

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
one.** The delivered animation's tail is a linear ramp that stops at
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Sequence, Tuple

import numpy as np

from library.tools.logo_relight import (
    PIPE_FORMAT,
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

    def with_ground(self, ground: Tuple[float, float, float]
                    ) -> "ClosingProfile":
        """The same closing, on a different field."""
        return ClosingProfile(
            light=self.light, ground=ground,
            complete_fraction=self.complete_fraction,
            attack_seconds=self.attack_seconds,
            release_seconds=self.release_seconds,
            base_light=self.base_light, flash_light=self.flash_light,
            tail_ceiling=self.tail_ceiling)


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


def fade_scale(levels: Sequence[float], profile: ClosingProfile
               ) -> List[float]:
    """What to multiply the mark's alpha by so its own fade reaches zero.

    The delivered animation fades linearly and stops at 0.164 of full.
    This re-maps that ramp onto [0, 1] - ``(level - last) / (1 - last)``
    - and returns the ratio between the re-mapped level and the level
    the frame actually carries. Before the fade starts the ratio is
    exactly 1, so nothing the captain timed moves; through the fade the
    slope steepens just enough to arrive at nothing.

    The light is not scaled here and does not need to be: it is driven
    by the emission of an ink whose alpha this ratio has already scaled,
    so mark and light go out together.
    """
    if not levels:
        return []
    top = max(levels)
    if top <= 0.0:
        raise SourceNotClosed("no frame of the source carries any mark")

    last = levels[-1] / top
    if last <= 0.0:
        return [1.0] * len(levels)
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
        normalised = value / top
        if index <= start or normalised <= 0.0:
            out.append(1.0)
            continue
        remapped = max(0.0, (normalised - last) / (1.0 - last))
        out.append(remapped / normalised)
    return out


# ── One frame ────────────────────────────────────────────────────────

def bulb_frame(rgba: np.ndarray, intensity: float, present: float,
               profile: ClosingProfile) -> np.ndarray:
    """One source frame in, one OPAQUE closing frame out.

    The order is the whole point. The ink is composited OVER the ground
    normally, because ink is an object and it occludes. The light is
    then ADDED on top of that, because light is not an object and only
    ever brightens - blending it would let a faint far field DARKEN the
    ground it falls on, which is the one thing a glow must never do.
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

    ground = np.asarray(profile.ground, dtype=np.float64)
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
    return [bulb_frame(rgba, intensity, present, profile)
            for rgba, intensity, present
            in zip(frames, intensities, presence)]


def describe(frames: Sequence[np.ndarray], rate: float,
             profile: ClosingProfile) -> dict:
    """What the four beats landed on, for the render receipt."""
    areas, levels = mark_measurements(frames, profile)
    completion = completion_index(areas, profile)
    presence = fade_scale(levels, profile)
    lit = [index for index, value in enumerate(presence)
           if value > 0.0 and levels[index] > 0.0]
    return {
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
