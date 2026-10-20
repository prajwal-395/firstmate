"""What an overlay target's MEDIA is, and the canvas and ink it has.

One enumeration of the media kinds an overlay operation may target, and
one instrument per kind, so the projection that lowers a transform and
the verification that measures the rendered pixels cannot disagree about
what the asset is.

``png_still``
    A native PNG still. Its canvas is the image's own pixel size; its
    ink is its alpha channel.

``rendered_video``
    A rendered video overlay - the engine's own lower thirds are
    ProRes 4444 ``.mov`` files with an alpha plane. Its canvas is the
    media's own resolution, which is what Resolve draws it at; its ink
    is the alpha plane the file carries.

Both draw through the SAME transform chain, so the geometry of a moved
or resized overlay is the same for both: an item's own source canvas
and its native transform decide where it draws. Only the MEASUREMENT of
the canvas and of the ink differs, and that difference lives here rather
than in each caller.

A media kind is decided by the media's OWN kind and its own container -
a Resolve ``Still`` backed by a PNG, or a Resolve ``Video`` backed by a
rendered movie. A file name is not a kind, and a file on disk is not a
measurement (AGENTS.md 10.3).

``tests/unit/resolve/test_overlay_video_media.py``.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass

PNG_STILL = "png_still"
RENDERED_VIDEO = "rendered_video"
MEDIA_KINDS = (PNG_STILL, RENDERED_VIDEO)

#: The containers a rendered video overlay may arrive in. The engine's
#: own graphics are ProRes 4444 ``.mov``; the siblings are what a
#: render can also be handed as, and every one of them carries an alpha
#: plane or is refused for not carrying one.
VIDEO_EXTENSIONS = (".mov", ".mp4", ".m4v")

#: Alpha counts where the canvas is at least this opaque. The caption QA
#: threshold (`qa.subtitle_qa.ALPHA_INK_THRESHOLD`) and the rendered-box
#: threshold (`overlay_verify.RENDERED_BOX_INK_THRESHOLD`) without
#: importing either chain for one number.
ALPHA_INK_THRESHOLD = 64


class OverlayMediaError(ValueError):
    """The target's media cannot be measured as an overlay."""


@dataclass(frozen=True)
class OverlayMedia:
    """What one overlay target's media IS, measured.

    ``canvas`` is the item's own pixel grid - the source space the
    transform chain scales - and never the delivery frame.
    """

    kind: str
    path: str
    canvas: tuple[int, int]
    has_alpha_plane: bool
    draws: bool
    transparent: bool


def kind_for_path(path: str) -> str | None:
    """The media kind a file's own container implies, or None."""
    extension = os.path.splitext(str(path or ""))[1].lower()
    if extension == ".png":
        return PNG_STILL
    if extension in VIDEO_EXTENSIONS:
        return RENDERED_VIDEO
    return None


def classify(clip: Mapping) -> tuple[str | None, str]:
    """The media kind of one overlay clip, and why there is none.

    The clip is a READ of a timeline item: it carries the media's own
    kind (``media_type``) and the file behind it (``source_file``).
    """
    source = str((clip or {}).get("source_file") or "")
    media_type = str((clip or {}).get("media_type") or "")
    extension = os.path.splitext(source)[1].lower()
    if not source:
        return None, "the item carries no source path"
    if not media_type:
        return None, "the media's own kind could not be read"
    if media_type == "Still":
        if extension == ".png":
            return PNG_STILL, ""
        return None, (
            f"a {media_type} backed by "
            f"{extension or 'a file with no extension'} is not a native "
            "PNG still")
    if media_type == "Video":
        if extension in VIDEO_EXTENSIONS:
            return RENDERED_VIDEO, ""
        return None, (
            f"a {media_type} backed by "
            f"{extension or 'a file with no extension'} is not a rendered "
            "movie overlay")
    return None, (
        f"a {media_type} is neither a native PNG still nor a rendered "
        "movie overlay")


def alpha_ink_box(kind: str, path: str, source_frame: int = 0, *,
                  destination: str | None = None,
                  threshold: int = ALPHA_INK_THRESHOLD,
                  ) -> tuple[int, int, int, int] | None:
    """The alpha ink box of an overlay asset, in source pixels.

    The box the asset's own alpha draws, measured on the frame that
    plays at ``source_frame``. ``None`` when the asset cannot be read -
    a gate that cannot see an asset must not read as covering it
    (AGENTS.md 10.4).
    """
    if kind == PNG_STILL:
        from PIL import Image

        try:
            with Image.open(path) as image:
                alpha = image.convert("RGBA").getchannel("A")
                return alpha.point(
                    lambda value: 255 if value >= threshold else 0).getbbox()
        except (OSError, ValueError):
            return None
    if kind == RENDERED_VIDEO:
        from PIL import Image

        target = destination
        temporary = None
        if not target:
            import tempfile

            handle, temporary = tempfile.mkstemp(suffix=".png")
            os.close(handle)
            target = temporary
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", path,
                 "-vf", f"select=eq(n\\,{int(source_frame)})",
                 "-fps_mode", "passthrough", "-frames:v", "1", target],
                capture_output=True, timeout=30, check=False)
            if result.returncode != 0 or not os.path.isfile(target):
                return None
            with Image.open(target) as image:
                alpha = image.convert("RGBA").getchannel("A")
                return alpha.point(
                    lambda value: 255 if value >= threshold else 0).getbbox()
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
        finally:
            if temporary and os.path.isfile(temporary):
                os.remove(temporary)
    return None


def measure(kind: str, path: str, *, pool_item=None) -> OverlayMedia:
    """Measure an overlay target's media, or refuse it by name.

    ``pool_item`` is Resolve's own media-pool item, read when there is
    one: what Resolve says the media's resolution is and what the file
    says it is have to agree, because the canvas is the space the
    transform chain scales in.
    """
    if kind == PNG_STILL:
        return _measure_png(path)
    if kind == RENDERED_VIDEO:
        return _measure_video(path, pool_item=pool_item)
    raise OverlayMediaError(
        f"{kind!r} is not a media kind this route covers")


def _measure_png(path: str) -> OverlayMedia:
    """A native PNG still: its own pixels, and the alpha it draws with."""
    from PIL import Image

    try:
        with Image.open(path) as asset:
            canvas = tuple(map(int, asset.size))
            alpha = asset.convert("RGBA").getchannel("A")
            if alpha.getbbox() is None:
                raise OverlayMediaError("asset has no alpha ink")
            if alpha.getextrema()[0] == 255:
                raise OverlayMediaError("asset has no transparent pixels")
    except OverlayMediaError:
        raise
    except (OSError, ValueError) as exc:
        raise OverlayMediaError(
            f"the still at {path!r} could not be read: {exc}") from exc
    return OverlayMedia(kind=PNG_STILL, path=path, canvas=canvas,
                        has_alpha_plane=True, draws=True, transparent=True)


def _measure_video(path: str, *, pool_item=None) -> OverlayMedia:
    """A rendered video overlay: its own resolution, and its alpha."""
    from library.tools import transition_overlay

    fields = _ffprobe_stream(path)
    canvas = (int(fields["width"]), int(fields["height"]))
    reported = _resolve_resolution(pool_item)
    if reported is not None and tuple(reported) != tuple(canvas):
        raise OverlayMediaError(
            f"the media's own resolution {canvas[0]}x{canvas[1]} and "
            f"Resolve's {reported[0]}x{reported[1]} disagree, so the "
            "canvas it draws in is not known")
    try:
        alpha = transition_overlay.measure_alpha(path)
    except transition_overlay.TransitionOverlayError as exc:
        raise OverlayMediaError(str(exc)) from exc
    if not alpha.draws:
        raise OverlayMediaError(
            "the rendered overlay draws nothing on any frame")
    # A frame every pixel of which is opaque is a legitimate authored
    # gesture (`transition_overlay.AlphaMeasurement.hides_the_frame`), so
    # it is reported and never refused here.
    return OverlayMedia(
        kind=RENDERED_VIDEO, path=path, canvas=canvas,
        has_alpha_plane=True, draws=True,
        transparent=alpha.fully_opaque_frames < alpha.frames_read)


def _ffprobe_stream(path: str, timeout: int = 30) -> dict:
    """The video stream's own dimensions, measured off the file."""
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt",
        "-of", "default=noprint_wrappers=1", path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise OverlayMediaError(
            "ffprobe is not on PATH, so the rendered overlay cannot be "
            "measured") from exc
    except subprocess.TimeoutExpired as exc:
        raise OverlayMediaError(
            f"ffprobe timed out after {timeout}s on {path!r}") from exc
    if result.returncode != 0:
        raise OverlayMediaError(
            f"ffprobe could not read {path!r}: "
            f"{result.stderr.strip() or 'no stderr'}")
    fields: dict = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip()
    try:
        width, height = int(fields["width"]), int(fields["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OverlayMediaError(
            f"{path!r} has no video stream ffprobe could describe") from exc
    if width <= 0 or height <= 0:
        raise OverlayMediaError(
            f"{path!r} reports a {width}x{height} frame, which is not a "
            "canvas anything can be placed on")
    return fields


def _resolve_resolution(pool_item) -> tuple[int, int] | None:
    """Resolve's own resolution for a media-pool item, or None."""
    if pool_item is None:
        return None
    try:
        raw = pool_item.GetClipProperty("Resolution")
    except Exception:  # noqa: BLE001 - judged by the return, not raised
        return None
    try:
        width, height = (int(part) for part in str(raw).lower().split("x"))
    except (AttributeError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return (width, height)
