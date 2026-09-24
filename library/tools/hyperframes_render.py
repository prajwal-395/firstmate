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

What this module does NOT port: ``MotionGraphics`` (2,351 lines - a
later PR) and project-owned staged ``.tsx`` compositions (which have
no HyperFrames form at all). ``hyperframes_template`` answers None for
those, and the call sites render them through Remotion, STATED in the
output, never silently - see ``library/tools/graphics_renderer.py``.
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
)


def hyperframes_template(composition: str) -> Optional[str]:
    """The HyperFrames template for *composition*, or None with no form.

    None means the caller renders through Remotion and SAYS SO -
    ``MotionGraphics`` until its port lands, and every project-owned
    staged ``.tsx`` composition, which has no HyperFrames equivalent at
    all. An entry that drew something approximate would be a creative
    fallback wearing a renderer's clothes (AGENTS.md 10.5).
    """
    if composition in HYPERFRAMES_COMPOSITIONS:
        return composition
    return None


# ── Availability ─────────────────────────────────────────────────

def hyperframes_available(timeout: int = 30) -> tuple:
    """``(usable, detail)`` - whether this machine can render HyperFrames.

    Read-only: ``npx --no-install`` answers only when the release is
    already resolvable, so asking never downloads anything. The render
    itself names ``hyperframes@${PIN}`` with ``--yes`` - the install,
    when it happens, is at render time and stated, not at probe time.
    """
    node = shutil.which("node")
    if not node:
        return False, "node is not on PATH (brew install node)"
    npx = shutil.which("npx")
    if not npx:
        return False, "npx is not on PATH (ships with Node.js)"
    try:
        done = subprocess.run(
            [npx, "--no-install", "hyperframes", "--version"],
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
    usable, detail = hyperframes_available()
    if not usable:
        raise HyperFramesUnavailable(
            f"HyperFrames cannot render here: {detail}. "
            f"Remotion stays the default engine; select HyperFrames only "
            f"where it is installed.")


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
    usable, detail = hyperframes_available()
    if not usable:
        raise HyperFramesUnavailable(
            f"HyperFrames cannot render here: {detail}")
    npx = shutil.which("npx") or "npx"
    os.makedirs(frames_dir, exist_ok=True)
    command = [
        npx, "--yes", f"hyperframes@{HYPERFRAMES_VERSION_PIN}", "render",
        project_dir,
        "-f", fps_argument(fps),
        "--format", "png-sequence",
        "-o", frames_dir,
    ]
    try:
        done = subprocess.run(
            command, capture_output=True, check=False,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
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
    import numpy as np
    from PIL import Image

    count = 0
    for frame in frames:
        image = Image.open(frame).convert("RGBA")
        rgba = np.asarray(image, dtype=np.uint16)
        alpha = rgba[..., 3:4]
        rgba[..., :3] = (rgba[..., :3] * alpha + 127) // 255
        Image.fromarray(rgba.astype(np.uint8), mode="RGBA").save(frame)
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
        done = subprocess.run(
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
