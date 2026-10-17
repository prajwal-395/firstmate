"""Deterministic rules for still and graphic overlays on reel timelines.

Overlays are independent media items. Their source pixels are tightly
cropped, while their placement is expressed as an absolute delivery-canvas
box and lowered through the shared layout model.
"""

from __future__ import annotations

import os


class ReelGraphicsError(ValueError):
    """An overlay violates the reel graphics contract."""


def require_current_units(model, transform) -> None:
    """Raise if a stored transform was computed under another proxy mode."""
    from library.tools.layout_model import UnitMismatchError

    reason = model.refuse_unit_mismatch(transform)
    if reason:
        raise UnitMismatchError(reason)


def assert_tight_alpha(path: str) -> tuple[int, int]:
    """Reject transparent padding around a PNG overlay's alpha bounds."""
    from PIL import Image

    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            bbox = rgba.getchannel("A").getbbox()
            size = rgba.size
    except Exception as exc:
        raise ReelGraphicsError(
            f"overlay {path!r} cannot be checked for tight alpha bounds: {exc}") from exc
    if bbox is None:
        raise ReelGraphicsError(f"overlay {path!r} has no visible alpha pixels")
    if bbox != (0, 0, size[0], size[1]):
        raise ReelGraphicsError(
            f"overlay {path!r} is not tight-boxed: media is {size[0]}x{size[1]} "
            f"but its alpha bounds are {bbox}")
    return size


def assert_before_end(overlay_start: int, overlay_end: int,
                      ending_start: int, label: str = "overlay") -> None:
    """Refuse an overlay whose half-open timeline span reaches the ending."""
    start, end, ending = map(int, (overlay_start, overlay_end, ending_start))
    if end <= start:
        raise ReelGraphicsError(f"{label} has an empty timeline span [{start}, {end})")
    if end > ending:
        raise ReelGraphicsError(
            f"{label} span [{start}, {end}) overlaps reel ending at frame {ending}")


def assert_timeline_media_online(timeline) -> None:
    """Refuse missing or explicitly offline media carried by a reel."""
    missing = []
    for media_type in ("video", "audio"):
        count = int(timeline.GetTrackCount(media_type) or 0)
        for track in range(1, count + 1):
            for item in timeline.GetItemListInTrack(media_type, track) or []:
                pool_item = item.GetMediaPoolItem()
                if pool_item is None:
                    continue  # Resolve generators and nested comps have no file.
                name = str(item.GetName() or "timeline item")
                try:
                    offline = pool_item.GetClipProperty("Offline")
                except Exception:  # older Resolve builds may omit this key
                    offline = None
                if str(offline or "").strip().lower() in ("1", "true", "yes", "offline"):
                    missing.append(f"{name} reports Offline={offline}")
                    continue
                try:
                    path = str(pool_item.GetClipProperty("File Path") or "").strip()
                except Exception as exc:
                    missing.append(f"{name} media path could not be read ({exc})")
                    continue
                if path and not os.path.isfile(path):
                    missing.append(f"{name} media is missing at {path}")
    if missing:
        raise ReelGraphicsError("reel contains offline media: " + "; ".join(missing))


def corner_brand_bug_segment(asset: str, canvas: tuple[int, int],
                             frame_box: tuple[float, float, float, float],
                             start_frame: int, end_frame: int,
                             ending_start_frame: int, *,
                             width: float, margin: float, fps: float,
                             pan_units: str = "original",
                             tilt_units: str = "original",
                             draw_gain: float = 1.0) -> dict:
    """Describe a static top-right brand bug as one positioned PNG item.

    ``frame_box`` and all dimensions are absolute canvas pixels. The box is
    inset by the same margin on the right and top. The returned segment can
    be passed through Ren's normal still overlay placement path.
    """
    if os.path.splitext(asset)[1].lower() != ".png":
        raise ReelGraphicsError("corner brand bug must be a single PNG still")
    source_w, source_h = assert_tight_alpha(asset)
    x0, y0, x1, y1 = map(float, frame_box)
    width, margin = float(width), float(margin)
    if width <= 0 or margin < 0 or x1 <= x0 or y1 <= y0 or fps <= 0:
        raise ReelGraphicsError("corner brand bug requires a positive frame, width, and margin")
    height = width * source_h / source_w
    box = (x1 - margin - width, y0 + margin,
           x1 - margin, y0 + margin + height)
    if box[0] < x0 or box[3] > y1:
        raise ReelGraphicsError("corner brand bug does not fit inside the A-roll frame")
    from library.tools.layout_model import LayoutItem, LayoutModel, Transform
    model = LayoutModel(canvas[0], canvas[1], pan_units, tilt_units)
    item = LayoutItem(source_w, source_h,
                      transform=Transform(draw_gain=draw_gain), label="corner brand bug")
    transform = model.compute_transform(item, box)
    require_current_units(model, transform)
    assert_before_end(start_frame, end_frame, ending_start_frame,
                      "corner brand bug")
    return {
        "overlay_path": os.path.abspath(asset), "media_type": "still",
        "timeline_start": int(start_frame) / fps,
        "timeline_end": int(end_frame) / fps,
        "total_frames": int(end_frame) - int(start_frame),
        "ending_start_frame": int(ending_start_frame),
        "placement_label": "corner_brand_bug", "absolute_canvas_box": list(box),
        "tight_box": {"width": source_w, "height": source_h,
                      "placement": transform.to_dict()},
    }
