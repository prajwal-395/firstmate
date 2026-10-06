"""Render programmatic graphics through FFrames instead of Remotion or HyperFrames.

The third engine behind ``library/tools/graphics_renderer.py``. FFrames
renders an SVG scene graph through Skia on the GPU (Metal on macOS,
Vulkan elsewhere), writing straight-alpha RGBA frames. Overlays arrive as
PNG that the carriage premultiplies into its own ``qtrle`` artefact; opaque
cards pipe the same pixels as raw RGBA straight into ffmpeg's
``prores_videotoolbox`` encoder.

The binary is a single precompiled executable - ``ren-graphics-fframes`` -
built once by ``scripts/build_fframes_runtime.sh`` and shipped. Ren never
compiles Rust on a customer's machine. The binary reads a renderer-neutral
scene.json (the Ren Graphics IR), builds the SVG scene graph, executes
animation curves, and renders frames.

No codec features are enabled in the build: FFrames is used purely as a
frame renderer, and the overlay carriage's own encoder (``qtrle``) produces
what Resolve consumes. This keeps the GPL/nonfree FFmpeg components out of
the binary entirely.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

FFRAMES_BINARY_NAME = "ren-graphics-fframes"
"""The precompiled binary's name on PATH."""

RENDER_TIMEOUT_SECONDS = 300
"""One card's budget. The Remotion caption path allows 180s per card
and HyperFrames allows 300s; FFrames renders on the GPU and is faster
than both, so this is headroom."""

SEQUENCE_PATTERN = "frame_%06d.png"
"""The renderer's own PNG-sequence naming, recorded honestly."""

_PILOT_ROOT = Path(__file__).resolve().parent.parent.parent


class FFramesUnavailable(RuntimeError):
    """FFrames cannot render on this machine."""


class FFramesRenderError(RuntimeError):
    """One FFrames card could not be turned into a file."""


def _perf_ledger():
    try:
        from library.tools import perf_ledger
    except ImportError:
        from tools import perf_ledger
    return perf_ledger


def fframes_binary() -> str:
    """The path to the precompiled ``ren-graphics-fframes`` binary.

    Searches PATH first, then the repo's ``native/target/release/`` and
    ``native/target/debug/`` directories. Refuses with a clear message
    when the binary is missing - never silently falls back to another
    engine.
    """
    found = shutil.which(FFRAMES_BINARY_NAME)
    if found:
        return found
    for target_dir in ("release", "debug"):
        candidate = (_PILOT_ROOT / "native" / "target" / target_dir
                     / FFRAMES_BINARY_NAME)
        if candidate.is_file():
            return str(candidate)
    raise FFramesUnavailable(
        f"{FFRAMES_BINARY_NAME} not found on PATH or in native/target/. "
        f"Build it with scripts/build_fframes_runtime.sh, or select a "
        f"different graphics engine.")


def fframes_available(timeout: int = 10) -> tuple:
    """``(usable, detail)`` - whether this machine can render FFrames.

    Checks that the binary exists and runs ``--version``. Read-only:
    never downloads or installs anything.
    """
    try:
        binary = fframes_binary()
    except FFramesUnavailable as exc:
        return False, str(exc)
    try:
        done = subprocess.run(
            [binary, "--version"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run {FFRAMES_BINARY_NAME}: {exc}"
    if done.returncode != 0:
        return False, (
            f"{FFRAMES_BINARY_NAME} --version failed "
            f"(exit {done.returncode}): {(done.stderr or '').strip()[-200:]}")
    return True, (done.stdout or "").strip()


def require_fframes() -> None:
    """Refuse by name when this machine cannot render FFrames."""
    from ren.edition import require_component

    require_component("renderer.fframes", action="load")
    usable, detail = fframes_available()
    if not usable:
        raise FFramesUnavailable(
            f"FFrames cannot render here: {detail}. "
            f"Build the binary with scripts/build_fframes_runtime.sh "
            f"and retry.")


_VIDEOTOOLBOX: Optional[bool] = None
"""Whether this machine's ffmpeg can encode through VideoToolbox, cached."""


def videotoolbox_available(timeout: int = 15) -> bool:
    """Whether `prores_videotoolbox` can encode here.

    VideoToolbox is an Apple system framework, so a non-Apple machine never
    has it; an Apple machine still needs an ffmpeg built with the encoder.
    Both are checked, once per process. Read-only.
    """
    global _VIDEOTOOLBOX
    if _VIDEOTOOLBOX is not None:
        return _VIDEOTOOLBOX
    if sys.platform != "darwin":
        _VIDEOTOOLBOX = False
        return False
    try:
        done = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _VIDEOTOOLBOX = False
        return False
    _VIDEOTOOLBOX = "prores_videotoolbox" in (done.stdout or "")
    return _VIDEOTOOLBOX


def prores_encoder_name() -> str:
    """The ProRes encoder to use: `prores_videotoolbox` when it can, else `prores_ks`.

    The hardware encoder is the fast path the raw-RGBA pipe exists for; the
    software encoder is the fallback that always works. The choice is named
    by the caller so a run says which one produced the artefact.
    """
    return "prores_videotoolbox" if videotoolbox_available() else "prores_ks"


def render_png_sequence(scene_json: str,
                         frames_dir: str,
                         fps: float,
                         timeout: int = RENDER_TIMEOUT_SECONDS) -> list:
    """Render a scene.json to RGBA PNG frames; return them, ordered.

    The binary writes ``frame_%06d.png`` files. The frames arrive
    straight-alpha with full-strength RGB, which is what the premultiply
    below is written against.
    """
    from ren.edition import require_component

    require_component("renderer.fframes", action="fetch or load")
    binary = fframes_binary()
    os.makedirs(frames_dir, exist_ok=True)
    command = [binary, "render", scene_json, frames_dir]
    try:
        done = _perf_ledger().run(
            "fframes_render",
            command, capture_output=True, check=False,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except OSError as exc:
        raise FFramesRenderError(
            f"FFrames renderer could not start: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise FFramesRenderError(
            f"FFrames render timed out after {timeout}s") from exc
    frames = sorted(Path(frames_dir).glob("frame_*.png"))
    if done.returncode != 0 or not frames:
        raise FFramesRenderError(
            f"FFrames render failed (exit {done.returncode}): "
            f"{(done.stderr or '')[-600:]}")
    return [str(frame) for frame in frames]


def premultiply_frames(frames: list) -> int:
    """Convert straight-alpha PNG frames to premultiplied, in place.

    FFrames writes straight alpha (RGB full-strength beside a partial
    alpha); the overlay carriage assumes each channel never exceeds its
    own alpha. ``out = round(in * a / 255)`` per channel.
    """
    try:
        from library.tools.overlay_carriage import premultiply_rgba_png
    except ImportError:
        from tools.overlay_carriage import premultiply_rgba_png
    count = 0
    for frame in frames:
        premultiply_rgba_png(frame)
        count += 1
    return count


def flatten_frames(frames: list, background: str) -> int:
    """Composite straight-alpha frames over the card's ground, opaque."""
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


def _parse_colour(colour: str):
    """``#rrggbb`` to a ``(1, 1, 3)`` uint16 array."""
    import numpy as np

    text = (colour or "").strip()
    if (len(text) == 7 and text.startswith("#")
            and all(c in "0123456789abcdefABCDEF" for c in text[1:])):
        rgb = tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))
        return np.array(rgb, dtype=np.uint16).reshape(1, 1, 3)
    raise FFramesRenderError(
        f"cannot flatten a card over {colour!r}: the ground is a "
        f"#rrggbb hex from the declaration, never a name or a blank.")


def _encode_overlay(frames: list, out_path: str, fps: float,
                    timeout: int) -> str:
    """Stitch the renderer's PNG sequence into the overlay's qtrle artefact.

    The overlay carriage contract - ``qtrle``/``argb``, premultiplied alpha,
    Data Level Full - is unchanged by the raw-RGBA work, which touches only
    the opaque ProRes path.
    """
    if not frames:
        raise FFramesRenderError("no frames to encode")
    first = Path(frames[0])
    if (first.parent / "frame_000001.png").is_file():
        pattern = str(first.parent / "frame_%06d.png")
    else:
        raise FFramesRenderError(
            f"cannot encode {first.parent}: expected the renderer's "
            f"frame_%06d.png naming and found {first.name!r}.")
    try:
        from library.tools.overlay_carriage import OVERLAY_ENCODE_ARGS
    except ImportError:
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
        raise FFramesRenderError(
            f"could not encode {len(frames)} FFrames frame(s) to "
            f"{out_path}: {exc}") from exc
    if done.returncode != 0 or not os.path.isfile(out_path):
        raise FFramesRenderError(
            f"could not encode {len(frames)} FFrames frame(s) to "
            f"{out_path}: {(done.stderr or '').strip()[-400:]}")
    return out_path


def _encode_opaque(scene_path: str, out_path: str, fps: float,
                   background: str, timeout: int, stream,
                   encoder: Optional[str] = None,
                   expected: int = 0) -> str:
    """Stream the renderer's raw RGBA into ffmpeg, flattening over the ground.

    The binary's ``--raw`` stdout is read frame by frame and written straight
    to ffmpeg's stdin - both running at once, no intermediate file - so a
    long card never holds more than one frame in memory or on disk. Each
    frame is flattened over the card's ground as it passes. Encodes with
    ``prores_videotoolbox``; where VideoToolbox is unavailable the encoder
    falls back to ``prores_ks`` and the fallback is named. Alpha and the
    4444 profile are kept either way. A truncated final frame refuses by
    name rather than shipping a shorter card.
    """
    import numpy as np

    from ren.edition import require_component

    require_component("renderer.fframes", action="fetch or load")
    with open(scene_path, encoding="utf-8") as handle:
        scene_meta = json.load(handle)
    width = int(scene_meta["width"])
    height = int(scene_meta["height"])
    frame_size = width * height * 4
    ground = _parse_colour(background)
    if encoder is None:
        encoder = prores_encoder_name()
    if encoder != "prores_videotoolbox":
        print(f"  FFrames: VideoToolbox unavailable, encoding "
              f"{encoder} (software fallback)", file=stream)
    binary = fframes_binary()
    ffmpeg_cmd = ["ffmpeg", "-y", "-v", "error",
                  "-f", "rawvideo", "-pix_fmt", "rgba",
                  "-s", f"{width}x{height}", "-r", f"{float(fps):.6f}",
                  "-i", "-",
                  "-c:v", encoder, "-profile:v", "4",
                  "-pix_fmt", "yuva444p12le", out_path]
    frames_written = 0
    binary_rc = -1
    ffmpeg_rc = -1
    with tempfile.TemporaryFile() as binary_err, \
            tempfile.TemporaryFile() as ffmpeg_err:
        with _perf_ledger().span("ffmpeg_encode", subprocesses=2):
            binary_proc = subprocess.Popen(
                [binary, "render", scene_path, "--raw"],
                stdout=subprocess.PIPE, stderr=binary_err)
            try:
                ffmpeg_proc = subprocess.Popen(
                    ffmpeg_cmd, stdin=subprocess.PIPE, stderr=ffmpeg_err)
                try:
                    header = binary_proc.stdout.read(8)
                    if len(header) != 8:
                        raise FFramesRenderError(
                            "FFrames raw render wrote no header")
                    while True:
                        frame = binary_proc.stdout.read(frame_size)
                        if len(frame) == 0:
                            break
                        if len(frame) < frame_size:
                            raise FFramesRenderError(
                                f"FFrames raw render wrote a truncated frame: "
                                f"{len(frame)} of {frame_size} bytes")
                        arr = np.frombuffer(
                            frame, dtype=np.uint8
                        ).reshape(height, width, 4).astype(np.uint16)
                        alpha = arr[..., 3:4]
                        rgb = (arr[..., :3] * alpha
                                + ground * (255 - alpha) + 127) // 255
                        flattened = np.dstack([
                            rgb.astype(np.uint8),
                            np.full((height, width), 255, dtype=np.uint8)])
                        ffmpeg_proc.stdin.write(flattened.tobytes())
                        frames_written += 1
                    ffmpeg_proc.stdin.close()
                    binary_rc = binary_proc.wait(timeout=timeout)
                    ffmpeg_rc = ffmpeg_proc.wait(timeout=timeout)
                except BrokenPipeError:
                    binary_rc = binary_proc.wait(timeout=timeout)
                    ffmpeg_rc = ffmpeg_proc.wait(timeout=timeout)
                finally:
                    try:
                        ffmpeg_proc.stdin.close()
                    except OSError:
                        pass
                    if ffmpeg_proc.poll() is None:
                        ffmpeg_proc.wait(timeout=timeout)
            finally:
                binary_proc.stdout.close()
                if binary_proc.poll() is None:
                    binary_proc.wait(timeout=timeout)
        if binary_rc != 0:
            binary_err.seek(0)
            raise FFramesRenderError(
                f"FFrames raw render failed (exit {binary_rc}): "
                f"{binary_err.read().decode('utf-8', 'replace')[-400:]}")
        if ffmpeg_rc != 0 or not os.path.isfile(out_path):
            ffmpeg_err.seek(0)
            raise FFramesRenderError(
                f"could not encode FFrames raw frames to {out_path}: "
                f"{ffmpeg_err.read().decode('utf-8', 'replace')[-400:]}")
    if expected and frames_written != expected:
        print(f"  FFrames: rendered {frames_written} frame(s), props "
              f"declare {expected}; encoding what rendered", file=stream)
    return out_path


def encode_frames(frames, out_path: str, *,
                  fps: float,
                  opaque: bool,
                  timeout: int = RENDER_TIMEOUT_SECONDS,
                  background: str = "",
                  stream=sys.stderr,
                  expected: int = 0) -> str:
    """Stitch adapted frames into the artefact the call site names.

    The overlay path (``opaque=False``) reads the renderer's PNG sequence
    and stitches ``qtrle`` - the overlay carriage, unchanged. The opaque
    path (``opaque=True``) streams the renderer's raw RGBA straight into
    ffmpeg, flattening each frame over the card's ground, and encodes with
    ``prores_videotoolbox`` - falling back to ``prores_ks`` where
    VideoToolbox is unavailable, named either way.
    """
    if opaque:
        try:
            return _encode_opaque(frames, out_path, fps, background,
                                  timeout, stream, expected=expected)
        except FFramesRenderError:
            if prores_encoder_name() == "prores_videotoolbox":
                print("  FFrames: prores_videotoolbox failed, falling "
                      "back to prores_ks", file=stream)
                return _encode_opaque(frames, out_path, fps, background,
                                      timeout, stream, encoder="prores_ks",
                                      expected=expected)
            raise
    return _encode_overlay(frames, out_path, fps, timeout)


FFRAMES_COMPOSITIONS = (
    "SubtitleOverlay",
    "TimedTextOverlay",
    "FullFrameCard",
    "MotionGraphics",
    "PostHeader",
    "StagedScene",
)
"""The compositions with an FFrames form: the five standard ones plus
StagedScene, each through its IR lowering."""

DEFAULT_FAMILY = {"TimedTextOverlay": "Helvetica"}
"""The family each template falls back to when the props name none -
Montserrat everywhere except TimedTextOverlay. The template's choice,
mirrored, so an undeclared family draws the same face in both engines."""


def fframes_has_form(composition: str) -> bool:
    """True when the composition has an FFrames form."""
    return composition in FFRAMES_COMPOSITIONS


class FFramesCaptionRenderer:
    """The FFrames renderer behind the same two-method seam.

    ``HyperFramesCaptionRenderer`` draws through staged HTML projects;
    this one through the precompiled ``ren-graphics-fframes`` binary -
    one IR scene per card, rendered to a PNG sequence, premultiplied
    into the overlay carriage (or stitched to ``qtrle`` for video
    carrying). Selection, provenance and reporting cannot tell which
    renderer drew the card: the contract is ``render`` / ``close`` and
    nothing else.
    """

    def __init__(self, project_folder: str = "",
                 remotion_dir: str = None):
        self.project_folder = project_folder or ""
        self.repo_root = str(Path(remotion_dir).resolve().parent) \
            if remotion_dir else None

    @property
    def sequence_pattern(self) -> str:
        return SEQUENCE_PATTERN

    def render(self, props_path: str, overlay_path: str,
               sequence: bool = False):
        """Draw one card, as video or as a PNG sequence. ``(ok, error)``."""
        try:
            with open(props_path, encoding="utf-8") as handle:
                props = json.load(handle)
        except (OSError, ValueError) as exc:
            return False, f"cannot read props {props_path}: {exc}"
        work_dir = os.path.dirname(os.path.abspath(overlay_path))
        try:
            if sequence:
                render_one_card_frames(
                    "SubtitleOverlay", props, overlay_path, work_dir,
                    self.project_folder, self.repo_root)
            else:
                render_one_card(
                    "SubtitleOverlay", props, overlay_path, work_dir,
                    self.project_folder, self.repo_root)
        except (FFramesUnavailable, FFramesRenderError) as exc:
            return False, str(exc)
        return True, ""

    def close(self):
        """Nothing to release - staging is removed per card."""


# ── Resolving what the props name ────────────────────────────────

def _project_file(reference: str, project_folder: str, what: str) -> str:
    """The file a props reference names: absolute as given, or ``brand/<name>``.

    The same brand-asset search HyperFrames staging uses; a reference the
    project does not have REFUSES rather than drawing without it.
    """
    if reference.startswith("data:"):
        return reference
    if os.path.isabs(reference) and os.path.isfile(reference):
        return reference
    from library.tools.hyperframes_render import _find_brand_file

    found = _find_brand_file(reference.split("/")[-1], project_folder)
    if found is None:
        raise FFramesRenderError(
            f"FFrames card names {what} {reference!r}, which is not in the "
            f"project's brand_assets/; refusing rather than drawing "
            f"without it.")
    return str(found)


def _family_of(font_file: str) -> str:
    from fontTools.ttLib import TTFont

    names = TTFont(font_file, lazy=True)["name"]
    return names.getDebugName(16) or names.getDebugName(1) or ""


def resolve_props(composition: str, props: dict, project_folder: str = "") -> dict:
    """Props with every file reference made an absolute path.

    The declared ``fontFile`` (top level or in ``style``) resolves to the
    project's file; with none declared, a family that names the bundled
    face resolves to the bundled file - the face the HyperFrames template
    always carries. Any other undeclared family stays a family, drawn
    from the system the way a browser draws it. Images and element
    assets resolve the same way.
    """
    from library.tools.hyperframes_render import bundled_font_file

    resolved = copy.deepcopy(props)
    style = resolved.get("style") if isinstance(resolved.get("style"), dict) else None
    declared = resolved.get("fontFile") or (style or {}).get("fontFile")
    if isinstance(declared, str) and declared:
        font_file = _project_file(declared, project_folder, "fontFile")
    else:
        family = (resolved.get("fontFamily") or (style or {}).get("fontFamily")
                  or DEFAULT_FAMILY.get(composition, "Montserrat"))
        bundled = bundled_font_file()
        font_file = (str(bundled) if bundled.is_file()
                     and family.strip().lower() == _family_of(str(bundled)).lower()
                     else None)
    if font_file:
        resolved["fontFile"] = font_file
        if style is not None:
            style["fontFile"] = font_file
    if isinstance(resolved.get("image"), str) and resolved["image"]:
        resolved["image"] = _project_file(resolved["image"], project_folder, "image")
    ground = resolved.get("ground")
    if isinstance(ground, dict) and isinstance(ground.get("texture"), str) \
            and ground["texture"]:
        ground["texture"] = _project_file(ground["texture"], project_folder, "texture")
    for layer in resolved.get("layers") or []:
        if isinstance(layer, dict) and isinstance(layer.get("src"), str) \
                and layer["src"]:
            layer["src"] = _project_file(layer["src"], project_folder, "image")
    for element in resolved.get("elements") or []:
        if isinstance(element, dict) and isinstance(element.get("asset"), str) \
                and element["asset"]:
            element["asset"] = _project_file(element["asset"], project_folder, "asset")
    return resolved


def lower_composition(composition: str, props: dict):
    """Lower composition props to an IR scene using the graphics_ir module."""
    from library.graphics_ir import lowering

    lower = {
        "SubtitleOverlay": lowering.lower_subtitle_overlay,
        "TimedTextOverlay": lowering.lower_timed_text_overlay,
        "FullFrameCard": lowering.lower_full_frame_card,
        "MotionGraphics": lowering.lower_motion_graphics,
        "PostHeader": lowering.lower_post_header,
        "StagedScene": lowering.lower_staged_scene,
    }.get(composition)
    if lower is None:
        raise FFramesRenderError(
            f"composition {composition!r} has no FFrames lowering")
    return lower(props)


def stage_scene_fonts(scene, fonts_dir: str):
    """Point every text node at a static instance of its face, in place.

    usvg cannot apply a variable font's ``wght`` axis and would draw the
    default instance (Montserrat's is Thin), so each (file, weight) the
    scene declares is cut to a static face under a unique family name,
    written to *fonts_dir*, and the node is pointed at it. The scene's
    meaning is unchanged: the same face at the same weight.
    """
    from library.graphics_ir.fonts import FontUnreadable, static_instance

    os.makedirs(fonts_dir, exist_ok=True)
    for node in scene.nodes:
        text = node.text
        if text is None or not text.font_file:
            continue
        try:
            data, family = static_instance(text.font_file, text.font_weight)
        except (FontUnreadable, OSError) as exc:
            raise FFramesRenderError(str(exc)) from exc
        path = os.path.join(fonts_dir, family.replace(" ", "-") + ".ttf")
        if not os.path.isfile(path):
            with open(path, "wb") as handle:
                handle.write(data)
        text.font_file = path
        text.font_family = family
    return scene


def _build_scene(composition: str, props: dict, staging: str,
                 project_folder: str) -> str:
    """Resolve, lower, stage fonts, write scene.json: the scene to render."""
    from library.graphics_ir.serialization import write_scene

    scene = lower_composition(
        composition, resolve_props(composition, props, project_folder))
    stage_scene_fonts(scene, os.path.join(staging, "fonts"))
    scene_path = os.path.join(staging, "scene.json")
    write_scene(scene, scene_path)
    return scene_path


def _render_frames(composition: str, props: dict, frames_dir: str,
                   staging: str, project_folder: str) -> list:
    """Render the scene to PNG frames, straight alpha."""
    scene_path = _build_scene(composition, props, staging, project_folder)
    return render_png_sequence(scene_path, frames_dir,
                               float(props.get("fps") or 30.0))


def render_one_card(composition: str,
                    props: dict,
                    out_path: str,
                    work_dir: str,
                    project_folder: str = "",
                    repo_root: Optional[str | Path] = None,
                    stream=sys.stderr) -> dict:
    """Draw one card through FFrames into the artefact *out_path* names.

    Lowers the composition to an IR scene, writes scene.json, renders
    through the precompiled binary, adapts the alpha (premultiply for
    overlays, flatten for opaque V1 cards), and encodes. Overlays stitch
    ``qtrle`` from PNG - the overlay carriage, unchanged. Opaque cards
    stream the renderer's raw RGBA straight into ffmpeg and encode
    ``prores_videotoolbox``, falling back to ``prores_ks`` where
    VideoToolbox is unavailable.
    """
    card_name = Path(out_path).stem
    staging = os.path.join(work_dir, f"ff_{card_name}")
    frames_dir = os.path.join(work_dir, f"ff_{card_name}_frames")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    try:
        fps = float(props.get("fps") or 30.0)
        expected = int(props.get("durationInFrames") or 0)
        opaque = composition == "FullFrameCard"
        if opaque:
            scene_path = _build_scene(composition, props, staging,
                                      project_folder)
            encode_frames(scene_path, out_path, fps=fps, opaque=True,
                          background=str(props.get("background") or ""),
                          stream=stream, expected=expected)
        else:
            frames = _render_frames(composition, props, frames_dir, staging,
                                    project_folder)
            if expected and len(frames) != expected:
                print(f"  FFrames {composition}: rendered {len(frames)} "
                      f"frame(s), props declare {expected}; encoding what "
                      f"rendered", file=stream)
            premultiply_frames(frames)
            encode_frames(frames, out_path, fps=fps, opaque=False)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(frames_dir, ignore_errors=True)
    return {
        "composition": composition,
        "asset_path": out_path,
        "bytes": os.path.getsize(out_path),
        "engine": "fframes",
    }


def render_one_card_frames(composition: str,
                           props: dict,
                           frames_dir: str,
                           work_dir: str,
                           project_folder: str = "",
                           repo_root: Optional[str | Path] = None) -> dict:
    """Render one FFrames card to its adapted PNG frames without encoding."""
    staging = os.path.join(work_dir, f"ff_{Path(frames_dir).name}")
    shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(frames_dir, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)
    try:
        frames = _render_frames(composition, props, frames_dir, staging,
                                project_folder)
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
        "engine": "fframes",
    }
