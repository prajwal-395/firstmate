"""Brand motion in a Remotion render: the slot, not the choice.

The gap this closes: no Remotion composition reads a video file, so the
captain's real brand motion - ``logo_reveal.mov`` (3.0s) and
``transition_bumper.mov`` (1.5s), both 1080x1920 ProRes 4444 with alpha at
30fps - could be REFERENCED (``transition_overlay`` asset mode,
``content.bookends`` asset mode) but never COMPOSITED into a Remotion
render. This module is the taste-free half of that gap: measure the file,
stage something the renderer can actually read, and build the props. What
it does NOT do is pick a frame-rate conform on the captain's behalf -
motion is their brand language, and the three strategies are named in
:data:`CONFORM_STRATEGIES` with their costs, never chosen here.

Why a mezzanine exists at all
-----------------------------
Measured 2026-09-08 in the same Chrome build Remotion renders with: a
``<video>`` pointed at the bumper's ProRes 4444 ``.mov`` reports
``videoWidth`` 0, ``videoHeight`` 0 and ``canPlayType('video/quicktime')``
``""`` - the container parses (duration reads, the audio clock even
seeks) but there is NO decodable video track, so an ``<OffthreadVideo>``
on the raw file renders nothing, silently. The same page pointed at a
VP9/``yuva420p`` WebM transcode reads 1080x1920 and paints the sparse
artwork with its alpha intact. ``docs/BRAND_MOTION_MEASURED.md`` has the
procedure and the numbers.

So the slot stages a same-rate VP9 WebM mezzanine of the source and the
composition plays THAT. Same rate is what keeps the transcode mechanical:
no frame is created or dropped, every source frame survives 1:1, and the
only change is the codec - which is forced, because VP9-in-WebM is the
only browser-decodable format that carries alpha. The cross-rate question
(30fps asset on a 24000/1001 timeline) is untouched by the transcode and
is answered by the declared conform strategy, below.

The three conform strategies, named and not chosen
--------------------------------------------------
:data:`CONFORM_STRATEGIES` is one enumeration, and :func:`require_conform`
refuses a declaration that names none of them - or names the one that is
not built - by name, with the costs. No default: any default here would
be the engine deciding what the captain's motion feels like.

``native_sample``
    Play the mezzanine in wall-clock time at the composition rate, which
    is what Remotion's ``<OffthreadVideo>`` does: each composition frame
    seeks the source timestamp. Nothing is blended and no authored pixel
    changes. What it costs is stated exactly: sampling 30fps at
    24000/1001 drops every 5th source frame (9 of the bumper's 45, 18 of
    the logo's 90 - a regular stutter-step, max jump 2 source frames).
    Wall-clock duration is exact; cadence is not.

``blended_conform``
    Pre-conform the mezzanine to the composition rate with frame blending
    (``ffmpeg`` ``minterpolate``/``framerate``). No frame is skipped and
    the cadence is smooth - and every output frame is a synthesis that
    softens the authored glow and ghosts fast motion. Which blender and
    how much blend IS the look, so this strategy is named, costed, and
    NOT BUILT: declaring it raises :class:`ConformNotBuilt` carrying the
    parameters nobody has chosen. That refusal is the taste question,
    stated as code.

``resolve_native``
    No Remotion render at all: place the original file on the Resolve
    timeline at its own rate through ``transition_overlay`` asset mode,
    whose placer already does the per-source-fps arithmetic
    (``reel_build``: 36 timeline frames of the bumper are 45 of its own).
    This is the route that already works today, and naming it here is
    what makes the enumeration complete rather than a menu of one.

The fixed length is not a fourth question
-----------------------------------------
Both files have FIXED lengths and the fear was that concatenating one
re-times a reel. It does not, because the slot never places anything:

* as an OVERLAY over a cut the element is additive
  (``transition_overlay.TIMING_IS_ADDITIVE``): the reel keeps its length,
  its keep ranges and every caption binding. The 1.5s bumper covers; it
  does not insert.
* as a CARD at the head or tail it concatenates (``content.bookends`` via
  ``mesh_spine``): the reel absorbs the 3.0s logo by declaration and the
  cursor shifts by exactly that. That is the declared shape, not drift.

Trimming the asset to fit is refused wherever this module is asked:
:func:`brand_motion_props` renders the WHOLE file
(``durationInFrames`` is the measured seconds at the composition rate)
and there is no trim parameter to disagree about - the same refusal
``OverlayDoesNotFit`` and the duration-mismatch check make on the
Resolve route. A reel that needs a shorter sting needs a shorter asset,
which is an authoring decision, not a render flag.

What reaches the composition
----------------------------
``BrandMotion`` (``remotion-subtitles/src/compositions/BrandMotion``) is
an ENGINE composition in the ``channel_bug`` shape: the engine draws and
the project supplies. Props carry the staged ``brand/<file>`` path, the
frame geometry, the measured duration in frames, and ``muted`` - which is
REQUIRED with no default, because both real assets carry an audio stream
and whether brand sound plays is a choice nobody made on the engine's
behalf. Geometry must match the delivery frame exactly: ``contain`` vs
``cover`` on a mismatch is framing taste, so a mismatch is refused
(:class:`GeometryMismatch`) rather than fitted.

    python3 -m library.tools.brand_motion --measure <file.mov>

``tests/test_brand_motion.py``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from library.tools import transition_overlay as ov

# ── Recorded answers ─────────────────────────────────────────────────

CONFORM_STRATEGIES = ("native_sample", "blended_conform", "resolve_native")
"""The frame-rate conforms, named and not chosen.

``native_sample`` plays the mezzanine in wall-clock time (Remotion seeks
each composition frame; 30fps at 24000/1001 drops every 5th source frame
and blends nothing). ``blended_conform`` pre-conforms with frame blending
(smooth cadence; every frame a synthesis - NAMED but NOT BUILT, because
the blender and the blend amount are the look). ``resolve_native`` skips
Remotion entirely and places the original at its own rate on the Resolve
timeline, which is the route that already works. :func:`require_conform`
refuses anything else, and there is no default.
"""

CONFORM_COSTS = {
    "native_sample": (
        "wall-clock exact, cadence lossy: sampling 30fps at 24000/1001 "
        "drops every 5th source frame (9 of 45 for the 1.5s bumper, 18 of "
        "90 for the 3.0s logo) and blends nothing, so every shown pixel "
        "is authored."
    ),
    "blended_conform": (
        "cadence smooth, pixels rewritten: a pre-conform synthesises every "
        "output frame, which softens the authored glow and ghosts fast "
        "motion. NOT BUILT - declaring it raises ConformNotBuilt with the "
        "blend parameters nobody chose."
    ),
    "resolve_native": (
        "no Remotion render: the original file is placed at its own rate "
        "on the Resolve timeline (transition_overlay asset mode already "
        "does this arithmetic). Nothing is transcoded and no pixel "
        "changes."
    ),
}

#: Where a same-rate mezzanine lives, under the project's own output.
#: Named here so the transcoder and the stager cannot disagree about the
#: filename - the reason ``bookends.BOOKEND_RENDER_DIRNAME`` exists.
MEZZANINE_DIRNAME = "brand_motion"

#: The mezzanine codec, fixed and stated. VP9 in WebM is the only
#: browser-decodable format that carries alpha, so there is no choice to
#: make here - and a choice-shaped constant would invite tuning per
#: asset, which is precisely what a mechanical default must not do.
#: ``-auto-alt-ref 0`` is required for the alpha plane to survive the
#: encode; ``-crf 20 -b:v 0`` is constant-quality (no bitrate opinion).
MEZZANINE_FFMPEG_ARGS = [
    "-c:v", "libvpx-vp9",
    "-pix_fmt", "yuva420p",
    "-auto-alt-ref", "0",
    "-crf", "20",
    "-b:v", "0",
    "-c:a", "libopus",
]

#: A source already in this shape stages verbatim - re-encoding a file
#: the renderer can already read would trade pixels for nothing.
#: Conservative on purpose: WebM + VP8/VP9 only, because that is what was
#: measured in the renderer's own browser. Anything else is transcoded.
NATIVE_CONTAINER = "webm"
NATIVE_CODECS = ("vp8", "vp9")

#: Which ``-c:v`` decoder reads the alpha of a source, by codec. The
#: NATIVE ``vp9`` decoder drops WebM alpha (measured 2026-09-08: no plane
#: on a file Chrome paints with alpha), while ``libvpx-vp9`` recovers
#: every frame; VP8 takes ``libvpx`` by the same mechanism. ``None`` is
#: ffmpeg's default and is what every other container is measured with.
ALPHA_DECODERS = {
    "vp9": "libvpx-vp9",
    "vp8": "libvpx",
}


class BrandMotionError(ValueError):
    """A brand-motion file, declaration or props this module refuses."""


class BrandMotionUnmeasurable(BrandMotionError):
    """The file could not be measured at all - missing, unreadable, or no
    ffprobe/ffmpeg. Distinguished from a measurement of zero on purpose:
    a measurement that did not happen is not a measurement of nothing."""


class ConformNotDeclared(BrandMotionError):
    """No conform strategy was declared, or the name is unknown.

    Carries all three strategies with their costs, so the refusal IS the
    question the captain answers - not a pointer to one."""


class ConformNotBuilt(BrandMotionError):
    """``blended_conform`` was declared, and it names a look nobody chose.

    Carries the blend parameters the strategy would need (which blender,
    how much blend), because those two values ARE the taste question and
    stating them is what makes it answerable."""


class GeometryMismatch(BrandMotionError):
    """The source is not the delivery geometry, and fitting it is taste.

    ``contain`` letterboxes the mark small; ``cover`` crops it. Both
    change how loud the client's mark reads, so the engine does neither:
    re-author the asset at the delivery geometry or declare the fit
    somewhere a producer owns it."""


# ── Measuring a source ───────────────────────────────────────────────

@dataclass(frozen=True)
class BrandMotionSource:
    """A brand-motion file, measured rather than assumed.

    The picture half IS ``transition_overlay.Element`` - one measurement,
    not two, so the alpha verdict here and the overlay verdict there
    cannot disagree. This module adds only what that measurement does not
    carry: the container/codec (for the decodability gate) and whether an
    audio stream rides along (for the ``muted`` requirement).
    """

    element: ov.Element
    container: str
    video_codec: str
    has_audio: bool

    @property
    def path(self) -> str:
        return self.element.path

    @property
    def width(self) -> int:
        return self.element.width

    @property
    def height(self) -> int:
        return self.element.height

    @property
    def fps(self) -> float:
        return self.element.fps

    @property
    def frame_count(self) -> int:
        return self.element.frame_count

    @property
    def duration_seconds(self) -> float:
        return self.element.duration_seconds

    def as_dict(self) -> dict:
        return {
            **self.element.as_dict(),
            "container": self.container,
            "video_codec": self.video_codec,
            "has_audio": self.has_audio,
        }


def _ffprobe_format(path: str, timeout: int = 30) -> dict:
    """Container name, video codec and audio presence, or raise."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=format_name",
        "-show_entries", "stream=index,codec_type,codec_name",
        "-of", "json", path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise BrandMotionUnmeasurable(
            f"ffprobe is not on PATH, so {path!r} cannot be measured. "
            f"A file is admitted on a measurement, never on its "
            f"extension.") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrandMotionUnmeasurable(
            f"ffprobe timed out after {timeout}s on {path!r}") from exc
    if res.returncode != 0:
        raise BrandMotionUnmeasurable(
            f"ffprobe could not read {path!r}: "
            f"{res.stderr.strip() or 'no stderr'}")
    try:
        data = json.loads(res.stdout or "{}")
    except ValueError as exc:
        raise BrandMotionUnmeasurable(
            f"ffprobe returned unparseable output for {path!r}") from exc
    return data


def _alpha_decoder(container: str, video_codec: str) -> str | None:
    """The ``-c:v`` decoder that sees this source's alpha, or None."""
    if (NATIVE_CONTAINER in (container or "").lower()
            and (video_codec or "").lower() in ALPHA_DECODERS):
        return ALPHA_DECODERS[(video_codec or "").lower()]
    return None


def measure_source(path: str) -> BrandMotionSource:
    """Measure a brand-motion file, or refuse it by name.

    The picture verdict (alpha absent / draws nothing / unmeasurable) is
    ``transition_overlay``'s, propagating unchanged: an overlay that draws
    nothing is not rendered (AGENTS.md 10.2) whichever renderer would
    have drawn it. A WebM source is measured through the decoder that
    sees its alpha (see :data:`ALPHA_DECODERS`): with the default decoder
    the plane is dropped in transit and a file the renderer paints would
    be refused as drawing nothing.
    """
    if not os.path.isfile(path):
        raise BrandMotionUnmeasurable(
            f"brand motion {path!r} is not a file")
    data = _ffprobe_format(path)
    container = str((data.get("format") or {}).get("format_name") or "")
    video_codec = ""
    has_audio = False
    for stream in data.get("streams") or []:
        if stream.get("codec_type") == "video" and not video_codec:
            video_codec = str(stream.get("codec_name") or "")
        if stream.get("codec_type") == "audio":
            has_audio = True
    try:
        element = ov.measure_element(
            path, video_decoder=_alpha_decoder(container, video_codec))
    except ov.ElementUnmeasurable as exc:
        raise BrandMotionUnmeasurable(str(exc)) from exc
    # ElementHasNoAlpha and ElementDrawsNothing propagate as they are:
    # the taxonomy of "measured and found wanting" already exists and a
    # second copy would let the two verdicts drift apart.
    return BrandMotionSource(
        element=element,
        container=container,
        video_codec=video_codec,
        has_audio=has_audio,
    )


def needs_mezzanine(source: BrandMotionSource) -> tuple[bool, str]:
    """Whether the renderer can read this file as-is, and why not.

    Measured 2026-09-08 in the renderer's own browser: ProRes in
    QuickTime exposes no decodable video track (``videoWidth`` 0,
    ``canPlayType`` ``""``), while VP8/VP9 in WebM paints picture with
    alpha. Conservative by design - a format pair not on the measured
    list transcodes rather than ships a black frame.
    """
    container = (source.container or "").lower()
    codec = (source.video_codec or "").lower()
    if NATIVE_CONTAINER in container and codec in NATIVE_CODECS:
        return False, (
            f"{source.video_codec} in {source.container} is on the "
            f"measured browser-decodable list, so it stages verbatim")
    return True, (
        f"{source.video_codec or 'unknown codec'} in "
        f"{source.container or 'unknown container'} is not on the "
        f"measured browser-decodable list (WebM + VP8/VP9 - ProRes in "
        f"QuickTime exposes no video track in the renderer's browser), "
        f"so it mezzanines to VP9 WebM at the same rate")


# ── The mezzanine ────────────────────────────────────────────────────

def mezzanine_path(project_folder: str, source_path: str) -> str:
    """Where the mezzanine of this source lives under project output."""
    stem = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(project_folder or "", "pipeline_output",
                        MEZZANINE_DIRNAME, f"{stem}.webm")


def ensure_mezzanine(source: BrandMotionSource, project_folder: str,
                     timeout: int = 300) -> str:
    """Transcode the source to a same-rate VP9 WebM, unless fresh.

    Same rate is the whole point: no frame is created or dropped, so the
    transcode is a codec change and not a conform - the conform stays a
    declared strategy. Re-runs when the output is missing or the source
    is newer than it; otherwise the existing file stands, because
    re-encoding a mezzanine it already built would trade pixels for
    nothing.

    The fresh file is verified before it is returned: same frame count
    and rate as the source (the same-rate proof), and an alpha plane
    that draws (read through the decoder that sees WebM alpha - the
    default one drops it in transit). A mezzanine that lost the alpha
    would composite as nothing, silently, which is the exact defect this
    module exists to close - so it is refused by name, not shipped.
    """
    out = mezzanine_path(project_folder, source.path)
    if (os.path.isfile(out)
            and os.path.getmtime(out) >= os.path.getmtime(source.path)):
        return out
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd = (["ffmpeg", "-v", "error", "-y", "-i", source.path]
           + MEZZANINE_FFMPEG_ARGS + [out])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise BrandMotionUnmeasurable(
            f"ffmpeg is not on PATH, so no mezzanine of "
            f"{source.path!r} can be built") from exc
    except subprocess.TimeoutExpired as exc:
        raise BrandMotionUnmeasurable(
            f"ffmpeg timed out after {timeout}s mezzanining "
            f"{source.path!r}") from exc
    if res.returncode != 0:
        raise BrandMotionUnmeasurable(
            f"ffmpeg could not mezzanine {source.path!r}: "
            f"{res.stderr.strip() or 'no stderr'}")
    if not os.path.isfile(out):
        raise BrandMotionUnmeasurable(
            f"ffmpeg reported success but wrote no file at {out!r}")
    _verify_mezzanine(out, source)
    return out


def _verify_mezzanine(out: str, source: BrandMotionSource) -> None:
    """The same-rate and alpha proofs, or a refusal by name."""
    try:
        element = ov.measure_element(out, video_decoder="libvpx-vp9")
    except ov.TransitionOverlayError as exc:
        raise BrandMotionUnmeasurable(
            f"mezzanine {out!r} of {source.path!r} lost the picture in "
            f"transit and is refused rather than shipped: {exc}") from exc
    if element.frame_count != source.frame_count:
        raise BrandMotionUnmeasurable(
            f"mezzanine {out!r} holds {element.frame_count} frames for a "
            f"{source.frame_count}-frame source - the transcode is "
            f"same-rate by definition, so a count that disagrees means "
            f"the file, not the arithmetic, is wrong")
    if abs(element.fps - source.fps) > 0.01:
        raise BrandMotionUnmeasurable(
            f"mezzanine {out!r} runs at {element.fps:g}fps for a "
            f"{source.fps:g}fps source - same-rate by definition, so "
            f"this file is refused")


def stage_mezzanine(mezzanine: str, remotion_dir: str) -> str:
    """Copy one mezzanine into Remotion's ``public/brand/``.

    One NAMED file, not the folder: the existing brand linker copies a
    project's stills and knows nothing of video, and widening it would
    let a ``.mov`` resolve into ``channel_bug``'s ``<img>``. Returns the
    ``brand/<file>`` path the composition wraps in ``staticFile``.
    """
    if not os.path.isfile(mezzanine):
        raise BrandMotionUnmeasurable(
            f"mezzanine {mezzanine!r} is not a file - nothing to stage")
    public_dir = os.path.join(remotion_dir, "public", "brand")
    os.makedirs(public_dir, exist_ok=True)
    base = os.path.basename(mezzanine)
    shutil.copy2(mezzanine, os.path.join(public_dir, base))
    return f"brand/{base}"


# ── The conform gate ─────────────────────────────────────────────────

def require_conform(declaration: Any) -> str:
    """The declared conform strategy, or a refusal that IS the question.

    Accepts the strategy name or a mapping carrying ``conform``. Absent
    or unknown names raise :class:`ConformNotDeclared` with all three
    strategies and their costs; ``blended_conform`` raises
    :class:`ConformNotBuilt` with the blend parameters nobody chose.
    There is no default and no inference from file metadata: how motion
    crosses a frame-rate boundary is brand language, and brand language
    is the captain's.
    """
    name = (declaration.get("conform")
            if isinstance(declaration, dict) else declaration)
    if name not in CONFORM_STRATEGIES:
        costs = "\n".join(
            f"  - {strategy}: {CONFORM_COSTS[strategy]}"
            for strategy in CONFORM_STRATEGIES)
        raise ConformNotDeclared(
            f"brand motion declares conform={name!r}, which names no "
            f"strategy. The three, with what each costs:\n{costs}\n"
            f"Declare one - there is no default, because any default "
            f"would be the engine deciding what the captain's motion "
            f"feels like.")
    if name == "blended_conform":
        raise ConformNotBuilt(
            "brand motion declares conform='blended_conform', which "
            "smooths the cadence by synthesising every output frame. "
            "That synthesis needs two values nobody has stated: WHICH "
            "blender (frame-averaging 'framerate' vs motion-interpolated "
            "'minterpolate') and HOW MUCH blend (mixture weights or "
            "search parameters). Both are the look itself - an averaged "
            "glow vs an interpolated one - so the strategy is named and "
            "costed but not built. State the two values or declare "
            "'native_sample' (authored pixels, stepped cadence) or "
            "'resolve_native' (no Remotion render; the Resolve route "
            "that already works).")
    return str(name)


# ── Props for the composition ────────────────────────────────────────

def brand_motion_props(staged_src: str, source: BrandMotionSource,
                       fps: float, width: int, height: int,
                       muted: bool, conform: str) -> dict:
    """Props for the ``BrandMotion`` composition.

    Renders the WHOLE file - ``durationInFrames`` is the measured seconds
    at the composition rate, and there is no trim parameter, so a reel
    that needs a shorter sting needs a shorter asset. Only
    ``native_sample`` reaches a render (``blended_conform`` is not built,
    ``resolve_native`` renders nothing); anything else is refused rather
    than rendered under a strategy nobody declared.
    """
    if conform != "native_sample":
        # Not a second conform check: require_conform owns the
        # declaration, and this refuses props written against anything
        # else reaching the renderer under this strategy's name.
        raise ConformNotDeclared(
            f"BrandMotion props name conform={conform!r}: only "
            f"'native_sample' reaches a Remotion render "
            f"('blended_conform' is not built, 'resolve_native' renders "
            f"nothing). Declare the strategy with require_conform first.")
    if not isinstance(muted, bool):
        raise BrandMotionError(
            f"BrandMotion props need muted as a boolean - "
            f"{muted!r} is not one. The source carries "
            f"{'an' if source.has_audio else 'no'} audio stream, and "
            f"whether brand sound plays is a choice, so it is stated, "
            f"never defaulted.")
    if source.width != width or source.height != height:
        raise GeometryMismatch(
            f"brand motion is {source.width}x{source.height} and the "
            f"delivery frame is {width}x{height}. Fitting it would be "
            f"framing taste ('contain' shrinks the mark, 'cover' crops "
            f"it), so the engine does neither - re-author the asset at "
            f"the delivery geometry.")
    if not staged_src:
        raise BrandMotionError(
            "BrandMotion props need the staged brand/<file> path - the "
            "composition wraps it in staticFile and Python never states "
            "a URL.")
    duration_frames = max(1, round(source.duration_seconds * fps))
    return {
        "src": staged_src,
        "fps": fps,
        "width": width,
        "height": height,
        "durationInFrames": duration_frames,
        "muted": muted,
        "volume": 1.0,
    }


def _cli_measure(path: str) -> dict:
    source = measure_source(path)
    necessary, reason = needs_mezzanine(source)
    return {
        **source.as_dict(),
        "needs_mezzanine": necessary,
        "mezzanine_reason": reason,
    }


def main(argv: list | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--measure":
        try:
            print(json.dumps(_cli_measure(args[1]), indent=2,
                             sort_keys=True))
        except BrandMotionError as exc:
            print(f"brand_motion: refused: {exc}", file=sys.stderr)
            return 2
        except ov.TransitionOverlayError as exc:
            print(f"brand_motion: refused: {exc}", file=sys.stderr)
            return 2
        return 0
    print("usage: python3 -m library.tools.brand_motion --measure <file>",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
