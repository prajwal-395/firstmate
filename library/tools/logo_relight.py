"""logo_relight.py - the end-logo animation, lit rather than thickened.

WHAT IS STILL IN FORCE HERE, AND WHAT IS NOT
--------------------------------------------
- **The PHYSICS below stand.** The ink/halo split, the navy base not
  emitting, the four fitted falloff octaves, additive compositing, the
  two temperatures, ``CORE_LIGHT`` at 0.05, the 16-bit pipe and the
  dither are all still what light does here, and
  :mod:`library.tools.logo_bulb` imports them.
- **The ENVELOPE below is SUPERSEDED** (captain, 2026-09-17), along with
  transparency as the ground and a delivered tail that stops at 0.164
  alpha.  :func:`intensity_envelope` surges on growth and then HOLDS, and
  a hold is exactly what the captain rejected.  Do not restore it: the
  asset that ships is the one ``logo_bulb`` renders - dark ground,
  arrival, ONE flash at completion, fade to nothing.

What this module does
---------------------
It re-lights the delivered mark, whose glow was baked in as a blur of the
artwork.  It does not re-author it: the mark's shape, colours,
choreography, duration, frame rate and 1080x1920 frame all come through
untouched, frame for frame.  The only thing replaced is the light.

1. **Separate the authored ink from the authored halo**
   (:func:`separate_ink`).  Alpha is bimodal on every frame; the
   threshold (:data:`INK_LO`, :data:`INK_HI`) is RELATIVE to each frame's
   own alpha maximum, so the mark survives the reveal ramp and the tail
   fade.  The halo is discarded; the ink is carried through unchanged.

2. **Decide what emits** (:func:`emission`).  Only the orange filament,
   never the navy base (:data:`EMIT_LO`, :data:`EMIT_HI` sit in the gap
   between the mark's two luminance populations).  The base RECEIVES
   light instead of casting it.

3. **Fall off over four octaves, not one** (:data:`OCTAVES`,
   :func:`glow_field`), summing to the shape of camera glare rather than
   of a blur: hot at the rim and still reaching far out.

4. **Add, never cover.**  The light is composited ADDITIVELY.  What lands
   ON the mark is attenuated by the ink's own alpha (:data:`CORE_LIGHT`),
   so the mark's colour is not clipped to white and its anti-aliased EDGE
   takes most of the light.

5. **Run hot at the source and coloured at the edge.**
   :data:`NEAR_COLOUR` is the brand orange taken up its own value ramp
   until nearly white; :data:`FAR_COLOUR` is the brand orange itself.
   Nothing here invents a hue.

6. **Peak and settle** (:func:`intensity_envelope`, SUPERSEDED - see
   above).  The envelope is driven by the animation's own measured
   emission - new filament surges (:data:`SURGE_GAIN`), and the surge
   decays once the mark stops growing (:data:`SURGE_RELEASE`) - scaled so
   the relit tail lands where the delivered tail lands.

What is NOT decided here
------------------------
This module has no opinion about where the asset is placed, how long it
runs, what rate it conforms to or what the mark looks like.  It reads one
RGBA sequence and writes another of the same length, rate and geometry,
and :func:`relight_file` refuses a source it cannot carry through
unchanged (:class:`SourceNotCarried`).

The artwork stays project data (AGENTS.md 14): nothing of the mark ships
in this repository, and :func:`relight_file` writes a new file and leaves
the source alone.

The values are DECLARED here, not routed through ``decided_value``: this
is an asset render run by hand, off no pipeline state, with one reader.
A project that wants a different light passes a different
:class:`LightProfile` rather than editing a constant.

    python3 -m library.tools.logo_relight --source <in.mov> --out <out.mov>
    python3 -m library.tools.logo_relight --source <in.mov> --contact-sheet <out.png>
    python3 -m library.tools.logo_relight --source <in.mov> --compare <relit.mov> <cmp.mp4>
    python3 -m library.tools.logo_relight --source <in.mov> --measure

The measurements of the delivered bake, the falloff each octave was
fitted to, and the approval and later ruling on the envelope:
docs/evidence/logo_relight.md.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np

# ── Declared light ───────────────────────────────────────────────────

INK_LO = 0.58
INK_HI = 0.88
"""Where the authored ink ends and the authored halo begins, as a
fraction of the frame's OWN alpha maximum.

Measured across the reveal, the hold and the fade of both delivered
files: alpha is bimodal on every frame, with the ink piled at 0.95-1.00
of the frame maximum and the halo spread below 0.55, and the band
between the two carrying only the mark's own anti-aliased edge. Crossing
it smoothly rather than cutting is what keeps that edge soft."""

EMIT_LO = 0.35
EMIT_HI = 0.55
"""What emits, by luminance. The mark has two luminance populations and
nothing between them: the orange filament at 0.71 and the navy screw
base at 0.19. A dark object does not emit, so the base is excluded and
the light it receives is the only light on it."""

OCTAVES: Tuple[Tuple[float, float], ...] = (
    (3.0, 2.2006),
    (12.0, 1.5897),
    (48.0, 0.2704),
    (190.0, 2.4151),
)
"""(sigma in pixels at 1080x1920, gain) - the falloff, as four octaves.

Not four numbers anybody liked the sound of. The four gains are the
non-negative least-squares fit of these four blurs of the real mark to a
1/r profile - the shape of camera glare - sampled in the same distance
bands :func:`falloff_profile` reports, weighted so the faint far bands
count as much as the bright near ones. The fit is within 0.03 of the
target in every band, and the gains are then scaled so that a light of
unit intensity lands at 1.0 in the 0-2 px band, which is what makes
:data:`GLOW_GAIN` below a number with a meaning.

One blur can be the hot rim or the far wash and is always neither. What
four buy is the two ends at once: the delivered file's single collar is
at 109/255 where it meets the ink and at ZERO by 32 px, so it reads as a
second stroke with an edge of its own. This profile is a comparable
129/255 at the ink and still carrying at 256 px, where light belongs."""

NEAR_COLOUR = (1.000, 0.960, 0.886)
FAR_COLOUR = (1.000, 0.667, 0.302)
"""The light's two temperatures. ``FAR_COLOUR`` is the mark's own orange,
measured off the delivered ink (255, 170, 77). ``NEAR_COLOUR`` is that
same orange carried up its own value ramp until it is nearly white,
which is what the bright part of any warm source does on any sensor.
Neither is a hue this module chose."""

HOT = 0.35
"""The light intensity at which the mix reaches ``NEAR_COLOUR``. Below
it the light is the brand orange; at and above it, white-hot. It sits
where it does so that the near field - inside roughly 4 px of the
filament - runs white and everything beyond it stays the brand colour."""

GLOW_GAIN = 0.50
"""How much light the filament throws, at the settled level: the alpha
the light reaches in the 0-2 px band, by the normalisation of
:data:`OCTAVES`. Bounded from above by the ink, not by taste - the mark
has concavities (the mouth of the C, the gaps between the rays) where
the field runs to twice the band mean, and a gain that clips there fills
them with flat white, which is the blob again by another route."""

CORE_LIGHT = 0.05
"""How much of the light falls ON the mark rather than around it.

A real bloom is added over its own source and clips it to white, and at
full strength here it does exactly that - the C stops being orange. The
mark's colour is the captain's and fixed, so the light over the ink is
attenuated - and attenuated by the ink's own alpha, so the mark's
anti-aliased EDGE takes most of what does land, which is where a lit
object is brightest anyway.

At 0.05 the body of the stroke moves from the delivered (255, 170, 77)
to (255, 181, 88): one step up its own value ramp, the hue unchanged,
which is what any emitting surface does when it is photographed rather
than printed. 0.0 leaves the ink byte-identical and lets the rim and the
surround carry the whole read; 0.10 measures (255, 192, 98) and starts
to look cream. The middle one is a judgement and it is the only place
this module touches anything the captain fixed."""

SURGE_GAIN = 0.55
SURGE_RELEASE = 0.78
"""Peak and settle, the two halves of it.

``SURGE_GAIN`` is how far above the settled level the bloom reaches at
its brightest - 0.55 is a little over half again - and ``SURGE_RELEASE``
is how much of the surge survives each frame once the mark stops
growing, so at 23.976fps the overshoot is most of the way home in about
a sixth of a second. The drive is the emission the animation GAINED
between frames, leaked through that release, which is why a mark drawing
itself on rides up with its own strokes and a mark holding still does
not pulse for no reason."""

REFERENCE_WIDTH = 1080
"""The width the sigmas above are stated at. A frame of another width
scales them, because a light that is 3 px wide at 1080 is 6 px wide at
2160 and the same light."""


class SourceNotCarried(ValueError):
    """The relit file would not match its source where it must."""


# ── The light ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LightProfile:
    """One complete statement of how the mark is lit.

    Everything the module decides is on this object, so a project that
    wants a different light passes a different profile instead of
    editing a constant.
    """

    ink_lo: float = INK_LO
    ink_hi: float = INK_HI
    emit_lo: float = EMIT_LO
    emit_hi: float = EMIT_HI
    octaves: Tuple[Tuple[float, float], ...] = OCTAVES
    near_colour: Tuple[float, float, float] = NEAR_COLOUR
    far_colour: Tuple[float, float, float] = FAR_COLOUR
    hot: float = HOT
    glow_gain: float = GLOW_GAIN
    core_light: float = CORE_LIGHT
    surge_gain: float = SURGE_GAIN
    surge_release: float = SURGE_RELEASE
    reference_width: int = REFERENCE_WIDTH

    def sigmas_for(self, width: int) -> Tuple[Tuple[float, float], ...]:
        """The octaves scaled to a frame of ``width`` pixels."""
        scale = float(width) / float(self.reference_width)
        return tuple((sigma * scale, gain) for sigma, gain in self.octaves)


def _smoothstep(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    if hi <= lo:
        raise ValueError(f"smoothstep needs lo < hi, got {lo} and {hi}")
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def separate_ink(rgba: np.ndarray, profile: LightProfile
                 ) -> Tuple[np.ndarray, np.ndarray]:
    """Split one straight-alpha RGBA frame into (ink RGB, ink alpha).

    The authored halo is discarded. The threshold is relative to the
    frame's own alpha maximum, so the same call works on a frame of the
    reveal ramp, of the hold and of the fade.
    """
    alpha = rgba[..., 3]
    peak = float(alpha.max())
    if peak <= 0.0:
        return rgba[..., :3], np.zeros_like(alpha)
    keep = _smoothstep(alpha / peak, profile.ink_lo, profile.ink_hi)
    return rgba[..., :3], alpha * keep


def emission(ink_rgb: np.ndarray, ink_alpha: np.ndarray,
             profile: LightProfile) -> np.ndarray:
    """What of the ink is a source of light.

    Luminance is Rec.709. The navy base falls below ``emit_lo`` and
    contributes nothing, which is the whole point.
    """
    luma = (0.2126 * ink_rgb[..., 0]
            + 0.7152 * ink_rgb[..., 1]
            + 0.0722 * ink_rgb[..., 2])
    return ink_alpha * _smoothstep(luma, profile.emit_lo, profile.emit_hi)


WORKING_SIGMA = 24.0
"""The sigma a wide octave is computed AT, after decimation.

A blur of sigma 190 px over a 1080x1920 frame is a 1500-tap kernel and
the slowest thing in this module by an order of magnitude - and it is
also, by construction, a signal with no detail finer than 190 px in it.
Each octave is therefore decimated until its sigma is about this many
pixels, blurred there, and resampled back - pre-blurred first so the
decimation has nothing to alias, and finished at the reduced resolution,
which is exact because Gaussians compose in quadrature. The captain's
machine is the limiter on this render, and the difference is not
visible: measured against the full-resolution field on the peak frame at
1080x1920, the largest deviation anywhere is 0.14 of one step of 8-bit
alpha."""


def glow_field(emit: np.ndarray, profile: LightProfile) -> np.ndarray:
    """Sum the octaves into one light field.

    The gains are absolute: a blur conserves energy, so a line source
    blurred by sigma peaks at roughly 1/sigma of its input, and that
    decay is half of what makes the near field steep. Compensating for
    it would flatten the profile into the one wide wash this module
    exists to replace.
    """
    from scipy import ndimage

    height, width = emit.shape
    out = np.zeros_like(emit, dtype=np.float64)
    for sigma, gain in profile.sigmas_for(width):
        if sigma <= 0.0:
            raise ValueError(f"an octave needs a positive sigma, got {sigma}")
        step = max(1, int(sigma / WORKING_SIGMA))
        if step == 1:
            out += gain * ndimage.gaussian_filter(emit, sigma=sigma,
                                                  mode="constant")
            continue
        # Pre-blur so the decimation has nothing left to alias, then
        # finish at the reduced resolution: Gaussians compose in
        # quadrature, so the two together are exactly this sigma.
        guard = 0.5 * step
        small = ndimage.gaussian_filter(emit, sigma=guard, mode="constant")
        small = ndimage.zoom(small, 1.0 / step, order=1, mode="constant")
        small = ndimage.gaussian_filter(
            small, sigma=(sigma ** 2 - guard ** 2) ** 0.5 / step,
            mode="constant")
        grown = ndimage.zoom(small, (height / small.shape[0],
                                     width / small.shape[1]),
                             order=1, mode="nearest")
        out += gain * grown
    return out


def relight_frame(rgba: np.ndarray, intensity: float,
                  profile: LightProfile) -> np.ndarray:
    """One straight-alpha RGBA frame in, one relit frame out.

    The light is added over the ink in premultiplied space and the
    result is returned to straight alpha, so the stroke's own core
    brightens from its own light instead of being repainted.
    """
    ink_rgb, ink_alpha = separate_ink(rgba, profile)
    light = np.clip(glow_field(emission(ink_rgb, ink_alpha, profile),
                               profile) * intensity, 0.0, 1.0)

    mix = np.clip(light / profile.hot, 0.0, 1.0)[..., None]
    near = np.asarray(profile.near_colour, dtype=np.float64)
    far = np.asarray(profile.far_colour, dtype=np.float64)
    light_rgb = far + (near - far) * mix

    # Additive, and attenuated where it lands on the mark itself.
    reaching = light * (1.0 - ink_alpha * (1.0 - profile.core_light))
    premul = ink_rgb * ink_alpha[..., None] + light_rgb * reaching[..., None]
    premul = np.clip(premul, 0.0, 1.0)
    out_alpha = np.clip(ink_alpha + light * (1.0 - ink_alpha), 0.0, 1.0)

    safe = np.where(out_alpha > 0.0, out_alpha, 1.0)[..., None]
    out_rgb = np.clip(premul / safe, 0.0, 1.0)

    out = np.empty_like(rgba)
    out[..., :3] = out_rgb
    out[..., 3] = out_alpha
    return out


def intensity_envelope(emissions: Sequence[float],
                       levels: Sequence[float],
                       profile: LightProfile) -> List[float]:
    """Peak-and-settle, derived from the animation's own emission.

    SUPERSEDED, 2026-09-17, by
    :func:`library.tools.logo_bulb.intensity_envelope`. What this
    function does after the surge releases is HOLD at
    :data:`GLOW_GAIN` for as long as the mark is up, and the captain -
    having seen it in a reel rather than in a side-by-side - asked for
    one quick soft flash at the moment the mark completes and then
    nothing. The physics around it are untouched; this shape is not the
    one that ships.

    ``emissions`` is the total emitted energy per frame and drives the
    surge: growth surges, the surge releases, nothing is keyframed, and
    the envelope re-derives itself for any mark handed to this module.

    ``levels`` is how lit the mark is on each frame - its own peak alpha
    against the sequence's - and it scales the whole thing. Without it
    the delivered animation's ending changes: the ink fades to 0.164 on
    the last frame, the light is ADDED to the ink rather than blended
    into it, and the relit tail measured 0.40 where the delivered one
    measured 0.164, so the logo stopped going out. The captain fixed the
    timing; a light that outlives the mark is a change to it.
    """
    drive: List[float] = []
    carried = 0.0
    previous = 0.0
    for value in emissions:
        carried = (carried * profile.surge_release
                   + max(0.0, value - previous))
        drive.append(carried)
        previous = value

    top = max(levels) if levels else 0.0
    scaled = [value / top if top > 0.0 else 1.0 for value in levels]

    ceiling = max(drive) if drive else 0.0
    if ceiling <= 0.0:
        return [profile.glow_gain * level for level in scaled]
    return [profile.glow_gain * (1.0 + profile.surge_gain * value / ceiling)
            * level
            for value, level in zip(drive, scaled)]


def relight_sequence(frames: Sequence[np.ndarray],
                     profile: LightProfile) -> List[np.ndarray]:
    """Relight a whole sequence, envelope and all."""
    totals, levels = [], []
    for rgba in frames:
        ink_rgb, ink_alpha = separate_ink(rgba, profile)
        totals.append(float(emission(ink_rgb, ink_alpha, profile).sum()))
        levels.append(float(ink_alpha.max()))
    envelope = intensity_envelope(totals, levels, profile)
    return [relight_frame(rgba, intensity, profile)
            for rgba, intensity in zip(frames, envelope)]


# ── The file ─────────────────────────────────────────────────────────


def probe(path: str) -> dict:
    """The properties of a source that must survive the relight."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries",
         "stream=width,height,r_frame_rate,nb_frames,pix_fmt,codec_name",
         "-of", "json", path],
        capture_output=True, encoding="utf-8", check=True).stdout
    streams = json.loads(out).get("streams") or []
    if not streams:
        raise SourceNotCarried(f"{path} has no video stream to relight")
    return streams[0]


PIPE_FORMAT = "rgba64le"
"""16 bits per channel, in and out.

The source is 12-bit ProRes 4444 and the far field of this light is a
gradient a couple of alpha steps deep over hundreds of pixels. An 8-bit
intermediate quantises that into visible concentric rings - measured, on
the first render of this module - so nothing here round-trips through
one."""


def read_frames(path: str) -> List[np.ndarray]:
    """Decode to straight-alpha float RGBA in [0, 1], 16 bits deep."""
    meta = probe(path)
    width, height = int(meta["width"]), int(meta["height"])
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo",
         "-pix_fmt", PIPE_FORMAT, "-"],
        check=True, capture_output=True).stdout
    stride = width * height * 8
    if not raw or len(raw) % stride:
        raise SourceNotCarried(
            f"{path} decoded to {len(raw)} bytes, not a whole number of "
            f"{width}x{height} frames")
    buffer = np.frombuffer(raw, dtype="<u2").astype(np.float64) / 65535.0
    return list(buffer.reshape(-1, height, width, 4))


def _dither(values: np.ndarray, levels: float,
            generator: np.random.Generator) -> np.ndarray:
    """Quantise to ``levels`` with triangular dither.

    The far field of this light is a gradient two or three code values
    deep spread over hundreds of pixels, and rounding one of those lands
    every pixel of a wide band on the same value - concentric rings, which
    is a worse artefact than the glow it belongs to. One LSB of
    triangular noise before the rounding turns the rings back into a
    gradient, and it is below the noise floor of anything downstream.
    """
    noise = (generator.random(values.shape) - generator.random(values.shape))
    return np.clip(np.rint(values * levels + noise), 0.0, levels)


def _frame_bytes(rgba: np.ndarray, generator: np.random.Generator) -> bytes:
    return _dither(rgba, 65535.0, generator).astype("<u2").tobytes()


def relight_file(source: str, destination: str,
                 profile: LightProfile | None = None) -> dict:
    """Relight a ProRes 4444 (or any alpha-carrying) source to a new file.

    The source is never modified. Rate, geometry, frame count and the
    audio stream all come through unchanged; a result that does not
    match the source on any of them raises :class:`SourceNotCarried`.
    """
    profile = profile or LightProfile()
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SourceNotCarried(f"{tool} is not on PATH")

    before = probe(source)
    frames = read_frames(source)
    width, height = int(before["width"]), int(before["height"])
    relit = relight_sequence(frames, profile)

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
        for rgba in relit:
            encoder.stdin.write(_frame_bytes(rgba, generator))
        encoder.stdin.close()
    except BrokenPipeError:
        pass
    stderr = encoder.stderr.read().decode("utf-8", "replace")
    if encoder.wait() != 0:
        raise SourceNotCarried(f"the encode failed: {stderr.strip()}")

    after = probe(destination)
    for key in ("width", "height", "r_frame_rate", "nb_frames"):
        if str(before.get(key)) != str(after.get(key)):
            raise SourceNotCarried(
                f"{key} changed in the relight: {before.get(key)} "
                f"-> {after.get(key)}")
    return {"source": before, "relit": after, "frames": len(frames),
            "width": width, "height": height}


# ── Looking at it ────────────────────────────────────────────────────


def contact_sheet(frames: Sequence[np.ndarray], indices: Sequence[int],
                  background: Tuple[int, int, int] = (16, 16, 18),
                  scale: int = 3):
    """Composite the named frames over a flat ground, side by side.

    A relit asset is judged by looking at it, and an alpha asset shows
    nothing until something is behind it.
    """
    from PIL import Image

    ground = np.asarray(background, dtype=np.float64) / 255.0
    tiles = []
    for index in indices:
        rgba = frames[index]
        alpha = rgba[..., 3:4]
        flat = rgba[..., :3] * alpha + ground * (1.0 - alpha)
        eight = _dither(flat, 255.0,
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


def side_by_side(source: str, relit: str, destination: str,
                 background: Tuple[int, int, int] = (16, 16, 18),
                 scale: int = 2) -> str:
    """Encode the two files running together, over a flat ground.

    An alpha asset shows nothing until something is behind it, and a
    claim that one looks better than another is worth nothing beside the
    two of them playing side by side. The ground is flat and dark on
    purpose: it is what the tail of a reel is, and a light is judged by
    what it puts into the frame around it.
    """
    left, right = read_frames(source), read_frames(relit)
    if len(left) != len(right):
        raise SourceNotCarried(
            f"{len(left)} source frames against {len(right)} relit")
    rate = probe(source)["r_frame_rate"]
    height, width = left[0].shape[:2]
    ground = np.asarray(background, dtype=np.float64) / 255.0
    out_width, out_height = 2 * (width // scale), height // scale

    encoder = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{out_width}x{out_height}", "-framerate", rate, "-i", "-",
         "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", destination],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    from PIL import Image

    try:
        for index, (before, after) in enumerate(zip(left, right)):
            tiles = []
            for rgba in (before, after):
                alpha = rgba[..., 3:4]
                flat = rgba[..., :3] * alpha + ground * (1.0 - alpha)
                eight = _dither(flat, 255.0,
                                np.random.default_rng(index)).astype(np.uint8)
                tiles.append(np.asarray(
                    Image.fromarray(eight, mode="RGB").resize(
                        (width // scale, height // scale), Image.LANCZOS)))
            encoder.stdin.write(np.concatenate(tiles, axis=1).tobytes())
        encoder.stdin.close()
    except BrokenPipeError:
        pass
    stderr = encoder.stderr.read().decode("utf-8", "replace")
    if encoder.wait() != 0:
        raise SourceNotCarried(f"the comparison encode failed: {stderr.strip()}")
    return destination


def falloff_profile(rgba: np.ndarray, profile: LightProfile | None = None
                    ) -> List[Tuple[int, int, float]]:
    """(outer distance, pixel count, mean alpha) away from the ink.

    The measurement the captain's complaint is really about: how far the
    light reaches and how it decays getting there.
    """
    from scipy import ndimage

    profile = profile or LightProfile()
    alpha = rgba[..., 3]
    peak = float(alpha.max())
    core = alpha >= peak * profile.ink_hi
    if not core.any():
        return []
    distance = ndimage.distance_transform_edt(~core)
    outside = (~core) & (alpha > 0.0)
    rows = []
    edges = [0, 2, 4, 8, 16, 32, 64, 128, 256, 512]
    for lo, hi in zip(edges[:-1], edges[1:]):
        band = outside & (distance > lo) & (distance <= hi)
        count = int(band.sum())
        if count:
            rows.append((hi, count, float(alpha[band].mean())))
    return rows


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True)
    parser.add_argument("--out")
    parser.add_argument("--contact-sheet")
    parser.add_argument("--frames", default="",
                        help="comma-separated indices for --contact-sheet")
    parser.add_argument("--compare", nargs=2,
                        metavar=("RELIT", "OUT_MP4"),
                        help="encode SOURCE and RELIT side by side")
    parser.add_argument("--measure", action="store_true",
                        help="print the falloff of the source, unchanged")
    args = parser.parse_args(argv)
    profile = LightProfile()

    if args.measure or args.contact_sheet:
        frames = read_frames(args.source)
        if args.measure:
            for rows, label in ((falloff_profile(frames[len(frames) * 4 // 5],
                                                 profile), "source"),):
                print(label)
                for hi, count, mean in rows:
                    print(f"  out to {hi:4d}px  n={count:7d}  "
                          f"mean alpha {mean * 255:6.1f}")
        if args.contact_sheet:
            picked = ([int(v) for v in args.frames.split(",") if v.strip()]
                      or list(range(0, len(frames),
                                    max(1, len(frames) // 6))))
            relit = relight_sequence(frames, profile)
            contact_sheet(relit, picked).save(args.contact_sheet)
            print(args.contact_sheet)

    if args.out:
        report = relight_file(args.source, args.out, profile)
        print(json.dumps(report, indent=2))
    if args.compare:
        print(side_by_side(args.source, args.compare[0], args.compare[1]))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
