"""Render programmatic graphics through HyperFrames instead of Remotion.

The second engine behind ``library/tools/graphics_renderer.py``. Where
Remotion draws from TSX through a bundle, HyperFrames draws from a
self-contained HTML composition - one template per composition under
``hyperframes/compositions/``, baked per card with that card's own
props - rendered with ``npx hyperframes render ... --format
png-sequence``. No network at render time: the GSAP build the tweens
run on is vendored at ``hyperframes/vendor/gsap.min.js`` (3.14.2, the
version the spike measured) and the typefaces come from this repo and
the project, staged beside the comp.

Two things the spike measured shape this module:

* HyperFrames PNG frames carry STRAIGHT alpha with full-strength RGB,
  while ``library/tools/overlay_carriage.py`` carries overlays as
  PREMULTIPLIED 8-bit RGBA. ``premultiply_frames`` converts in place
  between the render and the carriage encode, so a HyperFrames overlay
  inherits the correct clip attributes the same way a Remotion one
  does. A full-frame V1 card is not an overlay - it IS the picture -
  so ``flatten_frames`` composites it over its own declared ground
  instead.
* GSAP ``seek()`` suppresses callbacks, so per-frame style logic must
  be declarative tweens. The templates obey that: visibility windows
  are the engine's own ``.clip`` elements (``data-start`` /
  ``data-duration``), and everything inside one is a tween on the one
  paused timeline. Nothing draws in an ``onUpdate``.

What this module does NOT port: project-owned staged ``.tsx``
compositions, which have no HyperFrames form at all.
``hyperframes_template`` answers None for those, and the call sites
render them through Remotion, STATED in the output, never silently -
see ``library/tools/graphics_renderer.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from typing import Optional

HYPERFRAMES_VERSION_PIN = "0.8.70"
"""The HyperFrames release the spike measured. Pinned, not floating.

The spike found ``filter: blur()`` seeking correctly inside a
declarative tween even though the GSAP doc page lists only a subset of
tweenables as supported - behaviour that may differ across releases,
so the version that rendered the parity frames is the version every
render names.
"""

GSAP_VERSION_PIN = "3.14.2"
"""The vendored GSAP build's version. Same pinning, same reason."""

HYPERFRAMES_DIRNAME = "hyperframes"
COMPOSITIONS_DIRNAME = "compositions"
VENDOR_DIRNAME = "vendor"
GSAP_FILENAME = "gsap.min.js"
BUNDLED_FONT_FILENAME = "Montserrat-Variable.ttf"

RENDER_TIMEOUT_SECONDS = 300
"""One card's budget. The Remotion caption path allows 180s per card
and the spike measured HyperFrames at 5-6s for 48 frames warm, so this
is headroom rather than a number anything approaches."""

SEQUENCE_PATTERN = "frame_%06d.png"
"""The renderer's own PNG-sequence naming, recorded honestly.

Remotion sequences record ``frame-[frame].png`` (the CLI's vocabulary);
HyperFrames writes ``frame_000001.png`` and friends. A frames-container
entry records THIS string, so a reader never formats one engine's
pattern against the other engine's directory.
"""

_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent


class HyperFramesUnavailable(RuntimeError):
    """HyperFrames cannot render on this machine."""


class HyperFramesRenderError(RuntimeError):
    """One HyperFrames card could not be turned into a file."""


# ── Where the engine's own source is ─────────────────────────────

def _perf_ledger():
    try:
        from library.tools import perf_ledger
    except ImportError:  # imported as `tools.*` from inside library/
        from tools import perf_ledger
    return perf_ledger


def hyperframes_dir(repo_root: Optional[str | Path] = None) -> Path:
    """The ``hyperframes/`` project directory: templates and vendor."""
    if repo_root is not None:
        return Path(repo_root) / HYPERFRAMES_DIRNAME
    return _PILOT_ROOT / HYPERFRAMES_DIRNAME


def template_path(composition: str,
                  repo_root: Optional[str | Path] = None) -> Path:
    """The HTML template a Remotion composition name draws through."""
    return hyperframes_dir(repo_root) / COMPOSITIONS_DIRNAME / f"{composition}.html"


# ── Which compositions HyperFrames draws ─────────────────────────

#: Remotion composition -> HyperFrames template. Complete: a name absent
#: here has no HyperFrames form, and the call sites render it through
#: Remotion, STATED, rather than drawing something approximate.
HYPERFRAMES_COMPOSITIONS = (
    "SubtitleOverlay",
    "TimedTextOverlay",
    "FullFrameCard",
    "MotionGraphics",
)


def hyperframes_template(composition: str) -> Optional[str]:
    """The HyperFrames template for *composition*, or None with no form.

    None means the caller renders through Remotion and SAYS SO - every
    project-owned staged ``.tsx`` composition, which has no HyperFrames
    equivalent at all. An entry that drew something approximate would
    be a creative fallback wearing a renderer's clothes (AGENTS.md 10.5).
    """
    if composition in HYPERFRAMES_COMPOSITIONS:
        return composition
    return None


# ── Availability ─────────────────────────────────────────────────

def hyperframes_available(timeout: int = 30) -> tuple:
    """``(usable, detail)`` - whether this machine can render HyperFrames.

    Read-only: ``npx --no-install`` answers only when the PINNED
    release is already resolvable, so asking never downloads anything.
    The pin is load-bearing here, not just in the render: a bare
    ``hyperframes`` spec floats to whatever the registry calls latest
    (0.8.71 the week this was written), which is answerable only with
    an install - exactly what a read-only probe must never do. The
    render itself names ``hyperframes@${PIN}`` with ``--yes`` - the
    install, when it happens, is at render time and stated, not at
    probe time.
    """
    node = shutil.which("node")
    if not node:
        return False, "node is not on PATH (brew install node)"
    npx = shutil.which("npx")
    if not npx:
        return False, "npx is not on PATH (ships with Node.js)"
    try:
        done = subprocess.run(
            [npx, "--no-install", f"hyperframes@{HYPERFRAMES_VERSION_PIN}",
             "--version"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not ask npx about hyperframes: {exc}"
    if done.returncode != 0:
        return False, (
            f"hyperframes is not resolvable without an install "
            f"({(done.stderr or '').strip()[-200:]}); the first "
            f"HyperFrames render installs hyperframes@{HYPERFRAMES_VERSION_PIN}")
    return True, f"hyperframes {(done.stdout or '').strip()} via {npx}"


def require_hyperframes() -> None:
    """Refuse by name when this machine cannot render HyperFrames."""
    from ren.edition import require_component

    require_component("renderer.hyperframes", action="load")
    usable, detail = hyperframes_available()
    if not usable:
        raise HyperFramesUnavailable(
            f"HyperFrames cannot render here: {detail}. "
            f"Install the pinned HyperFrames CLI and retry.")


# ── Frame-rate spelling ──────────────────────────────────────────

def fps_argument(fps: float) -> str:
    """The ``-f`` spelling for a props float: exact rational where it is one.

    Props carry floats (``24000/1001`` arrives as
    ``23.976023976023978``), and the CLI accepts ffmpeg-style
    rationals. ``Fraction.limit_denominator`` recovers the exact ratio
    for every standard rate (23.976, 29.97, 59.94) while a genuinely
    odd rate passes through as a short float rather than a ratio it is
    not.
    """
    try:
        ratio = Fraction(float(fps)).limit_denominator(100_000)
    except (ValueError, OverflowError):
        return str(fps)
    if abs(float(ratio) - float(fps)) / max(1e-9, abs(float(fps))) < 1e-9:
        if ratio.denominator == 1:
            return str(ratio.numerator)
        return f"{ratio.numerator}/{ratio.denominator}"
    return str(float(fps))


def duration_seconds(duration_frames: int, fps: float) -> float:
    """The composition length in seconds: frames over the rate, exactly."""
    rate = float(fps) or 30.0
    return max(1e-6, int(duration_frames) / rate)


# ── Staging: one self-contained project per card ─────────────────

def bundled_font_file() -> Path:
    """The bundled Montserrat variable file both engines draw from."""
    packaged = (_PILOT_ROOT / "hyperframes" / "compositions"
                / BUNDLED_FONT_FILENAME)
    if packaged.is_file():
        return packaged
    return (_PILOT_ROOT / "remotion-subtitles" / "public" / "fonts"
            / BUNDLED_FONT_FILENAME)


def _find_brand_file(name: str, project_folder: str) -> Optional[Path]:
    """The project file a ``brand/<name>`` reference names, if it exists."""
    try:
        from library.tools.remotion_brand_linker import find_brand_assets
    except ImportError:  # imported as `tools.*` from inside library/
        try:
            from tools.remotion_brand_linker import find_brand_assets
        except ImportError:
            return None
    brand_dir = find_brand_assets(project_folder) if project_folder else None
    if brand_dir is None:
        return None
    candidate = Path(brand_dir) / name
    return candidate if candidate.is_file() else None


def stage_card_project(composition: str,
                       props: dict,
                       staging_dir: str,
                       project_folder: str = "",
                       repo_root: Optional[str | Path] = None) -> str:
    """Bake one card's props into a renderable HyperFrames project.

    Writes ``index.html`` (the template with this card's props and
    geometry baked in), ``gsap.min.js`` (vendored, no network), the
    bundled Montserrat, and any ``brand/`` files the props name -
    fonts the project carries and stills a card draws - resolved
    through the same brand-asset search the Remotion linker uses.
    Returns the project directory. Deterministic: same props, same
    bytes (the props JSON is serialised with sorted keys).
    """
    template = template_path(composition, repo_root)
    if not template.is_file():
        raise HyperFramesRenderError(
            f"HyperFrames composition {composition!r} has no template at "
            f"{template}; it is listed in HYPERFRAMES_COMPOSITIONS but "
            f"was never ported.")

    staged = Path(staging_dir)
    staged.mkdir(parents=True, exist_ok=True)

    head_extra: list[str] = []
    body_files: dict[str, Path] = {}

    # The bundled face both engines draw from, beside the comp - the
    # template's @font-face names this relative path, never a URL.
    bundled = bundled_font_file()
    if bundled.is_file():
        shutil.copy2(str(bundled), str(staged / BUNDLED_FONT_FILENAME))

    font_file = props.get("fontFile")
    if isinstance(font_file, str) and font_file:
        # A Remotion `staticFile()` path (`brand/<file>`): resolve the
        # PROJECT file and stage it beside the comp under its own name.
        # The template's project-face rule is written against that name.
        name = font_file.split("/")[-1]
        source = _find_brand_file(name, project_folder)
        if source is None and project_folder:
            raise HyperFramesRenderError(
                f"HyperFrames card names fontFile {font_file!r}, which is "
                f"not in the project's brand_assets/; refusing rather "
                f"than drawing in a fallback face.")
        if source is not None:
            staged_name = f"project-{name}"
            shutil.copy2(str(source), str(staged / staged_name))
            body_files["fontFile"] = staged / staged_name
            family = props.get("fontFamily") or "Montserrat"
            head_extra.append(
                f"@font-face {{ font-family: {json.dumps(family)}; "
                f"src: url({json.dumps('./' + staged_name)}) "
                f"format('truetype'); font-weight: 100 900; }}")

    image = props.get("image")
    if isinstance(image, str) and image:
        name = image.split("/")[-1]
        source = _find_brand_file(name, project_folder)
        if source is None and project_folder:
            raise HyperFramesRenderError(
                f"HyperFrames card names image {image!r}, which is not in "
                f"the project's brand_assets/; refusing rather than "
                f"drawing a card without its mark.")
        if source is not None:
            staged_name = f"project-{name}"
            shutil.copy2(str(source), str(staged / staged_name))
            body_files["image"] = staged / staged_name

    # Motion-graphics elements naming project files (`channel_bug`,
    # `website_panel`): the same brand-asset search, staged beside the
    # comp under their own names. A file the project does not have
    # refuses here, the way the Remotion plan drops it before it
    # reaches props - a card without its mark is a hole, never a
    # render in a fallback face.
    element_assets: dict[str, str] = {}
    for element in props.get("elements") or []:
        if not isinstance(element, dict):
            continue
        asset = element.get("asset")
        if not (isinstance(asset, str) and asset):
            continue
        name = asset.split("/")[-1]
        if name in element_assets:
            continue
        source = _find_brand_file(name, project_folder)
        if source is None and project_folder:
            raise HyperFramesRenderError(
                f"HyperFrames element {element.get('element')!r} names "
                f"asset {asset!r}, which is not in the project's "
                f"brand_assets/; refusing rather than drawing without it.")
        if source is not None:
            staged_name = f"project-{name}"
            shutil.copy2(str(source), str(staged / staged_name))
            element_assets[name] = staged_name

    gsap = hyperframes_dir(repo_root) / VENDOR_DIRNAME / GSAP_FILENAME
    if not gsap.is_file():
        raise HyperFramesRenderError(
            f"vendored {GSAP_FILENAME} is missing from {gsap.parent}; "
            f"a render without it would race the network for the CDN.")
    shutil.copy2(str(gsap), str(staged / GSAP_FILENAME))

    fps = float(props.get("fps") or 30.0)
    duration_frames = int(props.get("durationInFrames") or 1)
    width = int(props.get("width") or 1080)
    height = int(props.get("height") or 1920)
    seconds = duration_seconds(duration_frames, fps)

    source = template.read_text(encoding="utf-8")
    baked_props = dict(props)
    if "image" in body_files:
        baked_props = dict(baked_props)
        baked_props["image"] = "./" + body_files["image"].name
    if element_assets:
        # Rewrite the staged names into the elements that named them:
        # the template reads a relative path, never Remotion's
        # staticFile vocabulary.
        rewritten = []
        for element in baked_props.get("elements") or []:
            if isinstance(element, dict) and isinstance(
                    element.get("asset"), str):
                name = element["asset"].split("/")[-1]
                if name in element_assets:
                    element = dict(element)
                    element["asset"] = "./" + element_assets[name]
            rewritten.append(element)
        baked_props["elements"] = rewritten
    baked_props = _bake_digit_springs(baked_props, repo_root)
    props_json = json.dumps(baked_props, indent=2, sort_keys=True)
    html = source.replace("//__REN_PROPS__",
                          f"window.__REN_PROPS = {props_json};")
    html = html.replace("<!--__REN_HEAD_EXTRA__-->", "\n".join(head_extra))
    html = html.replace("__REN_DURATION__", repr(seconds))
    html = html.replace("__REN_WIDTH__", str(width))
    html = html.replace("__REN_HEIGHT__", str(height))
    html = html.replace("__REN_FPS__", fps_argument(fps))
    if "//__REN_PROPS__" in html or "__REN_DURATION__" in html:
        raise HyperFramesRenderError(
            f"template {template} leaves a placeholder unbaked; a card "
            f"rendered from it would draw the last card's props.")
    (staged / "index.html").write_text(html, encoding="utf-8")
    (staged / "hyperframes.json").write_text(json.dumps({
        "$schema": "https://hyperframes.heygen.com/schema/hyperframes.json",
    }), encoding="utf-8")
    return str(staged)


# ── The spring ───────────────────────────────────────────────────
#
# The digit_counter odometer is driven by a damped spring per digit,
# and physics gets no second spelling: this is Remotion's own spring
# integrator, ported operation-for-operation to pure stdlib Python -
# the analytic per-frame advance (`spring-utils.js`) stepped frame by
# frame, normalised by the measured natural duration
# (`measure-spring.js`), called with the composition's own config
# (damping 15, stiffness 80, mass 0.8). No `remotion` import, no node
# subprocess: the HyperFrames path must render on a machine that has
# never heard of Remotion, which is the whole reason the second
# renderer exists. `tests/unit/captions/test_hyperframes.py` pins this
# against Remotion spring values recorded as literals, so a drift
# between the two physics fails loudly at test time rather than
# drawing a differently-easing roll.
#
# This is mechanics, not taste (AGENTS.md 10.5): a damped harmonic
# oscillator integrated in double precision has one answer, and the
# literals prove this spelling gives it.

import math as _math

_SPRING_DAMPING = 15
_SPRING_STIFFNESS = 80
_SPRING_MASS = 0.8
_SPRING_THRESHOLD = 0.005
"""The config the composition rolls every digit with, and the rest
threshold the natural duration is measured against. Spelled once -
the template never names these, the bake reads them from here, and
the literals test records what they produce."""


def _spring_advance(*, to_value: float, last_timestamp: float,
                    current: float, velocity: float, now: float,
                    damping: float, mass: float,
                    stiffness: float) -> tuple:
    """One analytic integrator step. `now` and `last_timestamp` are ms."""
    delta_time = min(now - last_timestamp, 64)
    if damping <= 0:
        raise HyperFramesRenderError(
            "spring damping must be greater than 0, otherwise the "
            "spring animation never ends.")
    c, m, k = damping, mass, stiffness
    v0 = -velocity
    x0 = to_value - current
    zeta = c / (2 * _math.sqrt(k * m))
    omega0 = _math.sqrt(k / m)
    omega1 = omega0 * _math.sqrt(1 - zeta ** 2)
    t = delta_time / 1000
    sin1 = _math.sin(omega1 * t)
    cos1 = _math.cos(omega1 * t)
    envelope = _math.exp(-zeta * omega0 * t)
    frag1 = envelope * (sin1 * ((v0 + zeta * omega0 * x0) / omega1)
                        + x0 * cos1)
    if zeta < 1:
        position = to_value - frag1
        next_velocity = (zeta * omega0 * frag1 - envelope
                         * (cos1 * (v0 + zeta * omega0 * x0)
                            - omega1 * x0 * sin1))
    else:
        critically = _math.exp(-omega0 * t)
        position = (to_value - critically
                    * (x0 + (v0 + omega0 * x0) * t))
        next_velocity = (critically
                         * (v0 * (t * omega0 - 1)
                            + t * x0 * omega0 * omega0))
    return position, next_velocity, now


def _spring_calculation(*, frame: float, fps: float, damping: float,
                        mass: float, stiffness: float) -> float:
    """The 0-to-1 spring position at `frame`, stepped frame by frame."""
    frame_clamped = max(0, frame)
    uneven_rest = frame_clamped % 1
    last = _math.floor(frame_clamped)
    current, velocity, last_timestamp = 0, 0, 0
    f = 0
    while f <= last:
        if f == last:
            f = f + uneven_rest
        current, velocity, last_timestamp = _spring_advance(
            to_value=1, last_timestamp=last_timestamp, current=current,
            velocity=velocity, now=(f / fps) * 1000, damping=damping,
            mass=mass, stiffness=stiffness)
        f += 1
    return current


def _spring_natural_duration(*, fps: float, damping: float, mass: float,
                             stiffness: float,
                             threshold: float = _SPRING_THRESHOLD) -> int:
    """Frames until the spring stays within `threshold` of rest."""
    from functools import lru_cache as _cache

    @_cache(maxsize=None)
    def _at(frame: int) -> float:
        return _spring_calculation(frame=frame, fps=fps, damping=damping,
                                   mass=mass, stiffness=stiffness)

    frame = 0
    while abs(_at(frame) - 1) >= threshold:
        frame += 1
    finished = frame
    i = 0
    while i < 20:
        frame += 1
        if abs(_at(frame) - 1) >= threshold:
            i = 0
            finished = frame + 1
        else:
            i += 1
    return finished


def remotion_spring(*, frame: float, fps: float, duration_in_frames: int,
                    damping: float = _SPRING_DAMPING,
                    stiffness: float = _SPRING_STIFFNESS,
                    mass: float = _SPRING_MASS) -> float:
    """The digit roll's easing at `frame`: 0 unrolled, 1 settled.

    The composition's call with its defaults - no delay, no reverse,
    from 0 to 1 - stretched over `duration_in_frames` against the
    measured natural duration, returning exactly 1 past the end. A
    frame the spring never reaches is not a softer settle: past the
    end is 1, the way the reference reads it.
    """
    if duration_in_frames and frame > duration_in_frames:
        return 1.0
    natural = _spring_natural_duration(fps=fps, damping=damping,
                                       mass=mass, stiffness=stiffness)
    return _spring_calculation(
        frame=frame / (duration_in_frames / natural), fps=fps,
        damping=damping, mass=mass, stiffness=stiffness)


def _bake_digit_springs(baked_props: dict,
                        repo_root: Optional[str | Path]) -> dict:
    """Bake exact digit-strip offsets into digit_counter elements.

    One normalised y-offset per digit per local frame, from
    :func:`remotion_spring` called exactly as the composition calls
    the reference - pure Python, no node, no `remotion` import, so a
    card bakes on a machine that has never installed Remotion. Cards
    without a digit_counter pass through untouched (and pay nothing).
    `repo_root` is accepted and ignored, so this reads like the
    neighbour queries that do need a checkout.
    """
    _ = repo_root
    elements = baked_props.get("elements")
    if not isinstance(elements, list):
        return baked_props
    targets = [el for el in elements
               if isinstance(el, dict)
               and el.get("element") == "digit_counter"]
    if not targets:
        return baked_props
    fps = float(baked_props.get("fps") or 30.0)
    baked = dict(baked_props)
    rewritten = []
    for element in elements:
        if element not in targets:
            rewritten.append(element)
            continue
        element = dict(element)
        duration_frames = int(element.get("durationFrames") or 1)
        data = dict(element.get("data") or {})
        end_val = data.get("end_value") if isinstance(
            data.get("end_value"), (int, float)) else 100
        decimals = data.get("decimals") if isinstance(
            data.get("decimals"), (int, float)) else 0
        value = (f"{end_val:.{int(decimals)}f}" if decimals > 0
                 else f"{int(round(end_val)):,}")
        chars = list(value)
        # Aligned to CHAR positions (statics carry null): the template
        # indexes by character, skipping statics before it reads.
        aligned: list = []
        for position, char in enumerate(chars):
            if char not in "0123456789":
                aligned.append(None)
                continue
            digit = int(char)
            reverse = len(chars) - 1 - position
            stagger = round(reverse * (fps * 0.06))
            span = max(1, duration_frames - stagger)
            aligned.append([
                -digit * remotion_spring(
                    frame=max(0, lf - stagger), fps=fps,
                    duration_in_frames=span)
                for lf in range(duration_frames)])
        data["_hf_digit_offsets"] = aligned
        element["data"] = data
        rewritten.append(element)
    baked["elements"] = rewritten
    return baked


# ── The render ───────────────────────────────────────────────────

def render_png_sequence(project_dir: str,
                        frames_dir: str,
                        fps: float,
                        timeout: int = RENDER_TIMEOUT_SECONDS) -> list:
    """Render the staged project to RGBA PNG frames; return them, ordered.

    ``--format png-sequence`` is the natural input to the carriage: the
    spike verified the frames arrive straight-alpha with full-strength
    RGB, which is what the premultiply below is written against. The
    ``-f`` spelling is recovered from the props float (23.976 in, not
    23.98 rounded), because a rounded rate drifts the frame count the
    manifest already planned.
    """
    from ren.edition import require_component

    require_component("renderer.hyperframes", action="fetch or load")
    npx = shutil.which("npx")
    if not npx:
        raise HyperFramesUnavailable(
            "HyperFrames needs Node.js and npx on PATH; install Node.js "
            "and retry. The public edition does not use Remotion.")
    os.makedirs(frames_dir, exist_ok=True)
    command = [
        npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION_PIN}", "render",
        project_dir,
        "-f", fps_argument(fps),
        "--format", "png-sequence",
        "-o", frames_dir,
    ]
    try:
        done = _perf_ledger().run(
            "hyperframes_render",
            command, capture_output=True, check=False,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except OSError as exc:
        raise HyperFramesRenderError(
            f"HyperFrames renderer could not start for {project_dir}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise HyperFramesRenderError(
            f"HyperFrames render of {project_dir} timed out after "
            f"{timeout}s") from exc
    frames = sorted(Path(frames_dir).glob("*.png"))
    if done.returncode != 0 or not frames:
        raise HyperFramesRenderError(
            f"HyperFrames render of {project_dir} failed "
            f"(exit {done.returncode}): "
            f"{(done.stderr or '')[-600:]}")
    return [str(frame) for frame in frames]


# ── The alpha adapter ────────────────────────────────────────────

def premultiply_frames(frames: list) -> int:
    """Convert straight-alpha PNG frames to premultiplied, in place.

    HyperFrames writes straight alpha (RGB full-strength beside a
    partial alpha); the overlay carriage - and Resolve's premultiplied
    read of the ``qtrle`` file encoded from these frames - assumes each
    channel never exceeds its own alpha. ``out = round(in * a / 255)``
    per channel, so a fully transparent pixel is black-transparent
    rather than colour-transparent, and the encode that follows carries
    what Resolve will composite.
    """
    try:
        from library.tools.overlay_carriage import premultiply_rgba_png
    except ImportError:  # imported as `tools.*` from inside library/
        from tools.overlay_carriage import premultiply_rgba_png
    count = 0
    for frame in frames:
        premultiply_rgba_png(frame)
        count += 1
    return count


def flatten_frames(frames: list, background: str) -> int:
    """Composite straight-alpha frames over the card's ground, opaque.

    A full-frame V1 card IS the picture: there is nothing beneath it,
    so its file carries the ground baked in rather than an alpha
    channel. Same rounding as the premultiply above, over the declared
    background instead of over black.
    """
    import numpy as np
    from PIL import Image

    ground = _parse_colour(background)
    count = 0
    for frame in frames:
        image = Image.open(frame).convert("RGBA")
        rgba = np.asarray(image, dtype=np.uint16)
        alpha = rgba[..., 3:4]
        rgb = (rgba[..., :3] * alpha
               + ground * (255 - alpha) + 127) // 255
        opaque = np.dstack([rgb.astype(np.uint8),
                            np.full(rgba.shape[:2], 255, dtype=np.uint8)])
        Image.fromarray(opaque, mode="RGBA").save(frame)
        count += 1
    return count


def _parse_colour(colour: str) -> "object":
    """``#rrggbb`` to a ``(1, 1, 3)`` uint16 array. Refuses anything else."""
    import numpy as np

    text = (colour or "").strip()
    if (len(text) == 7 and text.startswith("#")
            and all(c in "0123456789abcdefABCDEF" for c in text[1:])):
        rgb = tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
        return np.array(rgb, dtype=np.uint16).reshape(1, 1, 3)
    raise HyperFramesRenderError(
        f"cannot flatten a card over {colour!r}: the ground is a "
        f"#rrggbb hex from the declaration, never a name or a blank.")


# ── Into the carriage ────────────────────────────────────────────

def encode_frames(frames: list, out_path: str, *,
                  fps: float,
                  opaque: bool,
                  timeout: int = RENDER_TIMEOUT_SECONDS) -> str:
    """Stitch adapted PNG frames into the artefact the call site names.

    The renderer writes ``frame_%06d.png``; ffmpeg reads the same
    pattern back at the props' own rate (the float, six decimals -
    23.976024 names the same frames 24000/1001 does, and the manifest
    counts frames rather than dividing seconds). Overlays go through
    the overlay carriage's own encoder arguments (``qtrle``
    premultiplied RGBA - ``overlay_carriage`` owns what one IS, so
    this names nothing of its own); opaque V1 cards go ProRes 4444,
    the codec the reels pool already carries.
    """
    if not frames:
        raise HyperFramesRenderError("no frames to encode")
    first = Path(frames[0])
    if (first.parent / "frame_000001.png").is_file():
        pattern = str(first.parent / "frame_%06d.png")
    else:
        # Not the renderer's own naming - refuse rather than guess
        # which files belong to the sequence.
        raise HyperFramesRenderError(
            f"cannot encode {first.parent}: expected the renderer's "
            f"frame_%06d.png naming and found {first.name!r}.")
    if opaque:
        # System ffmpeg's vocabulary: `-profile:v 4` is ProRes 4444
        # (Remotion's `--prores-profile 4444` is its own flag for the
        # same profile, and the system encoder does not know it).
        args = ["-c:v", "prores", "-profile:v", "4",
                "-pix_fmt", "yuva444p12le"]
    else:
        try:
            from library.tools.overlay_carriage import OVERLAY_ENCODE_ARGS
        except ImportError:  # imported as `tools.*` from inside library/
            from tools.overlay_carriage import OVERLAY_ENCODE_ARGS
        args = list(OVERLAY_ENCODE_ARGS)
    command = ["ffmpeg", "-y", "-v", "error",
               "-framerate", f"{float(fps):.6f}", "-i", pattern,
               *args, out_path]
    try:
        done = _perf_ledger().run(
            "ffmpeg_encode",
            command, capture_output=True, check=False,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HyperFramesRenderError(
            f"could not encode {len(frames)} HyperFrames frame(s) to "
            f"{out_path}: {exc}") from exc
    if done.returncode != 0 or not os.path.isfile(out_path):
        raise HyperFramesRenderError(
            f"could not encode {len(frames)} HyperFrames frame(s) to "
            f"{out_path}: {(done.stderr or '').strip()[-400:]}")
    return out_path


# ── One card, end to end ─────────────────────────────────────────

def render_one_card(composition: str,
                    props: dict,
                    out_path: str,
                    work_dir: str,
                    project_folder: str = "",
                    repo_root: Optional[str | Path] = None,
                    stream=sys.stderr) -> dict:
    """Draw one card through HyperFrames into the artefact *out_path* names.

    Stage (template + props + GSAP + fonts + brand files), render the
    PNG sequence, adapt the alpha (premultiply for overlays, flatten
    over the declared ground for opaque V1 cards), encode, and remove
    the staging. Returns what was drawn and how: composition, path,
    bytes, frame count and engine name, so the call site can state it.
    """
    template = hyperframes_template(composition)
    if template is None:
        raise HyperFramesRenderError(
            f"composition {composition!r} has no HyperFrames form; the "
            f"caller renders it through Remotion and says so.")
    card_name = Path(out_path).stem
    staging = os.path.join(work_dir, f"hf_{card_name}")
    frames_dir = os.path.join(work_dir, f"hf_{card_name}_frames")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    try:
        stage_card_project(template, props, staging, project_folder,
                           repo_root)
        fps = float(props.get("fps") or 30.0)
        frames = render_png_sequence(staging, frames_dir, fps)
        expected = int(props.get("durationInFrames") or 0)
        if expected and len(frames) != expected:
            print(f"  HyperFrames {composition}: rendered {len(frames)} "
                  f"frame(s), props declare {expected}; encoding what "
                  f"rendered", file=stream)
        opaque = composition == "FullFrameCard"
        if opaque:
            flatten_frames(frames, str(props.get("background") or ""))
        else:
            premultiply_frames(frames)
        encode_frames(frames, out_path, fps=fps, opaque=opaque)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(frames_dir, ignore_errors=True)
    return {
        "composition": composition,
        "asset_path": out_path,
        "bytes": os.path.getsize(out_path),
        "engine": "hyperframes",
    }


def render_one_card_frames(composition: str,
                           props: dict,
                           frames_dir: str,
                           work_dir: str,
                           project_folder: str = "",
                           repo_root: Optional[str | Path] = None) -> dict:
    """Render one HyperFrames card to its PNG frames without encoding video.

    Callers that must decide whether the artwork changes over time need
    the renderer's pixels, not an element-name roster. This returns the
    native frame sequence so the caller can retain one still when every
    frame is identical, or encode the sequence as video when pixels do
    change.
    """
    template = hyperframes_template(composition)
    if template is None:
        raise HyperFramesRenderError(
            f"composition {composition!r} has no HyperFrames form")
    card_name = Path(frames_dir).name
    staging = os.path.join(work_dir, f"hf_{card_name}")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    try:
        stage_card_project(template, props, staging, project_folder,
                           repo_root)
        frames = render_png_sequence(
            staging, frames_dir, float(props.get("fps") or 30.0))
        if composition == "FullFrameCard":
            flatten_frames(frames, str(props.get("background") or ""))
        else:
            premultiply_frames(frames)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "composition": composition,
        "frames_dir": frames_dir,
        "frames": frames,
        "frame_count": len(frames),
        "engine": "hyperframes",
    }
