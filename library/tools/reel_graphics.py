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


def assert_tight_alpha(path: str, *, tight_box=None) -> tuple[int, int]:
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
        # A declared tight crop includes the renderer's measured shadow
        # padding and the native placement canvas floor. Requiring ink at
        # all four edges contradicts the header renderer's own contract.
        from library.tools.tight_box import MIN_CANVAS_HEIGHT, PAD_BOTTOM, PAD_TOP, PAD_X
        box = (tight_box or {}).get("canvas_box")
        declared_size = ((tight_box or {}).get("width"),
                         (tight_box or {}).get("height"))
        if (box and len(box) == 4 and declared_size == size
                and (box[2] - box[0], box[3] - box[1]) == size
                and bbox[0] <= PAD_X + 1 and size[0] - bbox[2] <= PAD_X + 1
                and bbox[1] <= PAD_TOP + 1
                and (size[1] - bbox[3] <= PAD_BOTTOM + 1
                     or size[1] == MIN_CANVAS_HEIGHT)):
            return size
        raise ReelGraphicsError(
            f"overlay {path!r} is not tight-boxed: media is {size[0]}x{size[1]} "
            f"but its alpha bounds are {bbox}")
    return size


def assert_tight_overlay_media(path: str,
                               tight_box: dict | None = None) -> tuple[int, int]:
    """Check a PNG is cropped or carries its measured tight placement.

    A standalone PNG must fill its alpha bounds. Some engine graphics
    keep transparent space inside a tight canvas so their recorded
    Pan/Tilt stays within Resolve's rail; those must name the exact canvas
    size and placement they were rendered and measured against.
    """
    if not tight_box:
        return assert_tight_alpha(path)
    from PIL import Image

    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        actual_size = rgba.size
        alpha_bounds = rgba.getchannel("A").getbbox()
    if alpha_bounds is None:
        raise ReelGraphicsError(
            f"overlay {path!r} has no visible alpha pixels")
    try:
        declared_size = (int(tight_box["width"]), int(tight_box["height"]))
        placement = tight_box["placement"]
        float(placement["pan"])
        float(placement["tilt"])
        float(placement["scaling"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ReelGraphicsError(
            f"overlay {path!r} has malformed tight placement metadata") from exc
    if actual_size != declared_size:
        raise ReelGraphicsError(
            f"overlay {path!r} is {actual_size[0]}x{actual_size[1]}, but its "
            f"tight placement declares {declared_size[0]}x{declared_size[1]}")
    return actual_size


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
                             box: tuple[float, float, float, float],
                             start_frame: int, end_frame: int,
                             ending_start_frame: int, *,
                             fps: float,
                             pan_units: str = "original",
                             tilt_units: str = "original",
                             draw_gain: float = 1.0) -> dict:
    """Describe a static top-right brand bug as one positioned PNG item.

    ``box`` is the declared absolute rectangle in canvas pixels. The
    returned segment can be passed through Ren's normal still overlay
    placement path, which lowers the box again using the live timeline
    dimensions and proxy units.
    """
    if os.path.splitext(asset)[1].lower() != ".png":
        raise ReelGraphicsError("corner brand bug must be a single PNG still")
    source_w, source_h = assert_tight_alpha(asset)
    x0, y0, x1, y1 = map(float, box)
    canvas_w, canvas_h = map(float, canvas)
    if (x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0
            or x1 > canvas_w or y1 > canvas_h or fps <= 0):
        raise ReelGraphicsError(
            "corner brand bug requires a positive box inside the reel canvas")
    from library.tools.layout_model import LayoutModel
    model = LayoutModel(canvas[0], canvas[1], pan_units, tilt_units)
    transform = model.place_absolute_box(
        (source_w, source_h), (x0, y0, x1, y1), anchor=(1.0, 0.0),
        draw_gain=draw_gain)
    require_current_units(model, transform)
    assert_before_end(start_frame, end_frame, ending_start_frame,
                      "corner brand bug")
    return {
        "overlay_path": os.path.abspath(asset), "media_type": "still",
        "timeline_start": int(start_frame) / fps,
        "timeline_end": int(end_frame) / fps,
        "total_frames": int(end_frame) - int(start_frame),
        "ending_start_frame": int(ending_start_frame),
        "placement_label": "corner_brand_bug",
        "absolute_canvas_box": [x0, y0, x1, y1],
        "tight_box": {"width": source_w, "height": source_h,
                      "placement": transform.to_dict(),
                      "absolute_canvas_box": [x0, y0, x1, y1],
                      "anchor": [1.0, 0.0],
                      "frame": list(canvas)},
    }


def declared_corner_brand_bug(project_folder: str) -> dict | None:
    """Read the project's explicit corner-logo asset and canvas box."""
    import yaml

    project_yaml = os.path.join(project_folder, "project.yaml")
    try:
        with open(project_yaml, "r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        # Synthetic unit projects that do not model project declarations
        # have no corner mark to place.
        return None
    except (OSError, yaml.YAMLError) as exc:
        raise ReelGraphicsError(
            f"cannot read corner brand bug declaration: {exc}") from exc
    declaration = ((config.get("effect") or {}).get("corner_brand_bug"))
    if declaration is None:
        return None
    if not isinstance(declaration, dict):
        raise ReelGraphicsError(
            "effect.corner_brand_bug must declare an asset and absolute box")
    unknown = sorted(set(declaration) - {"asset", "box", "anchor"})
    if unknown:
        raise ReelGraphicsError(
            f"effect.corner_brand_bug declares unread keys {unknown}")
    asset = declaration.get("asset")
    box = declaration.get("box")
    anchor = declaration.get("anchor", "top_right")
    if not isinstance(asset, str) or not asset.strip():
        raise ReelGraphicsError(
            "effect.corner_brand_bug.asset must be a non-empty path")
    if not isinstance(box, list) or len(box) != 4:
        raise ReelGraphicsError(
            "effect.corner_brand_bug.box must be [x0, y0, x1, y1]")
    if anchor != "top_right":
        raise ReelGraphicsError(
            "effect.corner_brand_bug.anchor must be 'top_right'")
    path = asset if os.path.isabs(asset) else os.path.join(project_folder, asset)
    if not os.path.isfile(path):
        raise ReelGraphicsError(
            f"effect.corner_brand_bug.asset is missing: {path}")
    if os.path.splitext(path)[1].lower() != ".png":
        raise ReelGraphicsError(
            "effect.corner_brand_bug.asset must be a PNG still")
    try:
        normalized_box = [float(value) for value in box]
    except (TypeError, ValueError) as exc:
        raise ReelGraphicsError(
            "effect.corner_brand_bug.box must contain four numbers") from exc
    return {"asset": os.path.abspath(path), "box": normalized_box,
            "anchor": anchor}
