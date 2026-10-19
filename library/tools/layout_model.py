"""Layout coordinate model for overlay placement.

Converts between an item's LOCAL pixels and ABSOLUTE canvas pixels in
both directions, given the canvas size, the item's source size, an
anchor point, and the Resolve transform (ZoomX/Y, Pan, Tilt in the
units the current proxy setting implies).

The 2026-10-16 defect this module prevents
--------------------------------------------
A static corner logo was baked into the shared A-roll frame at the
logo's intended canvas coordinates. Because that frame image is itself
scaled and positioned on the timeline, the logo landed about 300 px
low: its local-pixel coordinates were treated as canvas coordinates
without accounting for the parent frame's transform.

The fix is explicit nesting: an element positioned relative to another
item carries that item's transform as its parent, and the conversion
local -> canvas walks the parent chain. An element positioned relative
to the canvas directly has no parent and its local coordinates are
canvas coordinates.

The proxy unit mismatch
-----------------------
Pan/Tilt values are in the units of the CURRENT proxy resolution
setting. When `perfProxyResolutionRatio` is "quarter", one Pan/Tilt
unit is 4x larger than when it is "original". A write computed under
one setting and applied under the other lands 4x off. This module
refuses a write whose units do not match the current setting.

Nesting model (CSS positioned parents)
---------------------------------------
An element may be positioned relative to another item (its parent) or
relative to the canvas (no parent). The parent's transform converts
the child's local coordinates into the parent's local coordinates,
then the parent's parent converts those into its local coordinates,
and so on up to the canvas. This is the same model as CSS positioned
parents: a child with `position: absolute` is positioned relative to
its nearest positioned ancestor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Optional

from library.tools.resolve_transform import (
    FALLBACK_DRAW_GAIN,
    NATIVE_BASE_SCALE,
    drawn_centre,
    pan_tilt_for_centre,
)


_PROXY_POSITION_UNIT_SCALE = {
    # Pan/Tilt values follow the proxy pixel grid. A quarter-resolution
    # proxy pixel spans four original pixels, so the stored value needed
    # for the same screen displacement is one quarter as large.
    "original": 1.0,
    "full": 1.0,
    "half": 2.0,
    "quarter": 4.0,
    "eighth": 8.0,
}


def current_proxy_units(project) -> str:
    """Read the active Resolve proxy ratio used by Pan/Tilt writes."""
    try:
        value = project.GetSetting("perfProxyResolutionRatio")
    except Exception as exc:  # noqa: BLE001 - an unmeasured unit is unsafe
        raise LayoutModelError(
            f"cannot read Resolve proxy position units: {exc}") from exc
    units = str(value or "").strip().lower()
    aliases = {"original": "original", "full": "full", "half": "half",
               "quarter": "quarter", "eighth": "eighth"}
    if units not in aliases:
        raise LayoutModelError(
            f"Resolve returned unknown perfProxyResolutionRatio {value!r}")
    return aliases[units]


class LayoutModelError(ValueError):
    """A layout coordinate conversion or transform write is invalid."""


class UnitMismatchError(LayoutModelError):
    """A transform write was computed under a different proxy unit setting."""


@dataclass(frozen=True)
class GeometryDescriptor:
    """Output-canvas geometry plus the native projection used to place it.

    The box is the intended visible item rectangle in output pixels.  The
    native projection is evidence about the Resolve properties compiled from
    that intent; verification must still measure the rendered output.
    """

    frame: tuple[int, int]
    box: tuple[float, float, float, float]
    canvas: tuple[float, float]
    anchor: tuple[float, float] = (0.5, 0.5)
    parent: Optional[str] = None
    native_projection: Optional[dict] = None
    space: str = "output_px"

    def __post_init__(self):
        if self.space != "output_px":
            raise LayoutModelError(
                f"geometry space must be 'output_px', got {self.space!r}")
        if len(self.frame) != 2 or min(map(int, self.frame)) <= 0:
            raise LayoutModelError(f"invalid output frame {self.frame!r}")
        if len(self.canvas) != 2 or min(map(float, self.canvas)) <= 0:
            raise LayoutModelError(f"invalid item canvas {self.canvas!r}")
        if len(self.box) != 4:
            raise LayoutModelError(f"invalid output box {self.box!r}")
        x0, y0, x1, y1 = map(float, self.box)
        if (not all(isfinite(value) for value in
                    (x0, y0, x1, y1, *map(float, self.canvas)))
                or x1 <= x0 or y1 <= y0):
            raise LayoutModelError(f"output box must have area, got {self.box!r}")
        if len(self.anchor) != 2 or any(
                not 0 <= float(value) <= 1 for value in self.anchor):
            raise LayoutModelError(f"invalid geometry anchor {self.anchor!r}")

    def to_dict(self) -> dict:
        return {
            "space": self.space,
            "frame": list(self.frame),
            "box": list(self.box),
            "position": [(self.box[0] + self.box[2]) / 2.0,
                         (self.box[1] + self.box[3]) / 2.0],
            "size": [self.box[2] - self.box[0],
                     self.box[3] - self.box[1]],
            "canvas": list(self.canvas),
            "anchor": list(self.anchor),
            "parent": self.parent,
            "native_projection": dict(self.native_projection or {}),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GeometryDescriptor":
        descriptor = cls(
            space=data["space"], frame=tuple(data["frame"]),
            box=tuple(data["box"]), canvas=tuple(data["canvas"]),
            anchor=tuple(data["anchor"]), parent=data["parent"],
            native_projection=dict(data.get("native_projection") or {}),
        )
        expected = descriptor.to_dict()
        for key in ("position", "size"):
            if key in data and (len(data[key]) != 2 or any(
                    abs(float(actual) - float(wanted)) > 1e-9
                    for actual, wanted in zip(data[key], expected[key]))):
                raise LayoutModelError(
                    f"geometry descriptor {key} disagrees with its output box")
        return descriptor


@dataclass(frozen=True)
class Transform:
    """A Resolve transform in the units of one proxy setting.

    `pan_units` and `tilt_units` are "original" or "quarter" - the
    proxy resolution ratio the values were computed under. A write
    whose units do not match the current setting is refused.
    """

    pan: float = 0.0
    tilt: float = 0.0
    zoom_x: float = 1.0
    zoom_y: float = 1.0
    scaling: float = 1.0
    pan_units: str = "original"
    tilt_units: str = "original"
    draw_gain: float = FALLBACK_DRAW_GAIN

    def to_dict(self) -> dict:
        return {
            "Pan": self.pan,
            "Tilt": self.tilt,
            "ZoomX": self.zoom_x,
            "ZoomY": self.zoom_y,
            "Scaling": self.scaling,
            "pan_units": self.pan_units,
            "tilt_units": self.tilt_units,
            "draw_gain": self.draw_gain,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transform":
        return cls(
            pan=float(data.get("Pan", 0.0)),
            tilt=float(data.get("Tilt", 0.0)),
            zoom_x=float(data.get("ZoomX", 1.0)),
            zoom_y=float(data.get("ZoomY", 1.0)),
            scaling=float(data.get("Scaling", 1.0)),
            pan_units=str(data.get("pan_units", "original")),
            tilt_units=str(data.get("tilt_units", "original")),
            draw_gain=float(data.get("draw_gain", FALLBACK_DRAW_GAIN)),
        )


@dataclass
class LayoutItem:
    """One item on the canvas: its source size, transform and parent.

    `source_width` and `source_height` are the item's own pixel
    dimensions (the overlay canvas size, the source frame size). The
    transform positions and scales the item on the canvas. `parent`
    is another LayoutItem this item is positioned relative to, or None
    if it is positioned relative to the canvas directly.
    """

    source_width: float
    source_height: float
    transform: Transform = field(default_factory=Transform)
    parent: Optional["LayoutItem"] = None
    label: str = ""


class LayoutModel:
    """Converts between item local pixels and absolute canvas pixels.

    `canvas_width` and `canvas_height` are the delivery frame's pixel
    dimensions. `current_pan_units` and `current_tilt_units` are the
    proxy setting's current unit names; a write whose units do not
    match is refused.
    """

    def __init__(self, canvas_width: float, canvas_height: float,
                 current_pan_units: str = "original",
                 current_tilt_units: str = "original"):
        _positive(canvas_width, "canvas_width")
        _positive(canvas_height, "canvas_height")
        self.canvas_width = float(canvas_width)
        self.canvas_height = float(canvas_height)
        self.current_pan_units = current_pan_units
        self.current_tilt_units = current_tilt_units

    def local_to_canvas(self, item: LayoutItem, local_x: float,
                        local_y: float) -> tuple[float, float]:
        """Convert one item's local pixels to absolute canvas pixels.

        Walks the parent chain: the item's local coordinates are
        converted to its parent's local coordinates by the item's
        transform, then the parent's parent's, and so on up to the
        canvas. An item with no parent is already in canvas
        coordinates.
        """
        if item.parent is None:
            return self._item_local_to_canvas(item, local_x, local_y)
        parent_local = self._item_to_parent_local(item, local_x, local_y)
        return self.local_to_canvas(item.parent, parent_local[0],
                                     parent_local[1])

    def canvas_to_local(self, item: LayoutItem, canvas_x: float,
                        canvas_y: float) -> tuple[float, float]:
        """Convert absolute canvas pixels to one item's local pixels.

        The inverse of `local_to_canvas`. Walks the parent chain in
        reverse: canvas -> parent local -> item local.
        """
        if item.parent is None:
            return self._canvas_to_item_local(item, canvas_x, canvas_y)
        parent_local = self.canvas_to_local(item.parent, canvas_x, canvas_y)
        return self._parent_local_to_item(item, parent_local[0],
                                           parent_local[1])

    def compute_transform(self, item: LayoutItem, canvas_box: tuple,
                          anchor: tuple = (0.5, 0.5)) -> Transform:
        """Compute the transform that puts an item at `canvas_box`.

        `canvas_box` is `(x0, y0, x1, y1)` in absolute canvas pixels.
        `anchor` is the item's anchor point as a fraction of its
        source size (0.5, 0.5 is the centre). The computed transform
        carries the current proxy units.

        This is the placement API: callers pass an absolute canvas
        box and get back the transform to write.
        """
        x0, y0, x1, y1 = canvas_box
        box_w = float(x1) - float(x0)
        box_h = float(y1) - float(y0)
        if box_w <= 0 or box_h <= 0:
            raise LayoutModelError(
                f"canvas box must have positive size, got {canvas_box}")
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        if src_w <= 0 or src_h <= 0:
            raise LayoutModelError(
                f"item source size must be positive, got ({src_w}, {src_h})")

        zoom_x = box_w / src_w
        zoom_y = box_h / src_h

        item_cx = float(x0) + box_w * 0.5
        item_cy = float(y0) + box_h * 0.5

        base_scale = NATIVE_BASE_SCALE
        if item.parent is not None:
            base_scale = self._parent_base_scale(item)

        pan, tilt = pan_tilt_for_centre(
            src_w, src_h,
            self.canvas_width, self.canvas_height,
            item_cx, item_cy,
            base_scale, item.transform.draw_gain)
        pan /= _proxy_position_unit_scale(self.current_pan_units)
        tilt /= _proxy_position_unit_scale(self.current_tilt_units)

        return Transform(
            pan=pan, tilt=tilt,
            zoom_x=zoom_x, zoom_y=zoom_y,
            scaling=NATIVE_BASE_SCALE,
            pan_units=self.current_pan_units,
            tilt_units=self.current_tilt_units,
            draw_gain=item.transform.draw_gain,
        )

    def place_absolute_box(self, source_size: tuple[float, float],
                           box: tuple[float, float, float, float], *,
                           anchor: tuple[float, float] = (0.5, 0.5),
                           draw_gain: float = FALLBACK_DRAW_GAIN) -> Transform:
        """Lower one absolute canvas box to a Resolve transform.

        ``box`` is the visible rectangle in delivery-canvas pixels;
        ``anchor`` describes the source point used to align it.  Every
        overlay placement path should call this conversion rather than
        carrying its own Pan/Tilt or scale arithmetic.
        """
        if len(anchor) != 2 or any(not 0.0 <= float(v) <= 1.0
                                  for v in anchor):
            raise LayoutModelError(f"anchor must be a normalized pair, got {anchor!r}")
        source_w, source_h = map(float, source_size)
        x0, y0, x1, y1 = map(float, box)
        if source_w <= 0 or source_h <= 0:
            raise LayoutModelError(f"source size must be positive, got {source_size!r}")
        if x1 <= x0 or y1 <= y0:
            raise LayoutModelError(f"canvas box must have positive size, got {box!r}")
        item = LayoutItem(source_w, source_h,
                          Transform(draw_gain=draw_gain))
        transform = self.compute_transform(item, (x0, y0, x1, y1), anchor)
        return transform

    def refuse_unit_mismatch(self, transform: Transform) -> Optional[str]:
        """Return a refusal reason if the transform's units do not match.

        Pan/Tilt values are in the units of the proxy setting they were
        computed under. A write computed under "original" units applied
        under "quarter" units lands 4x off (and vice versa). This
        returns None when the units match, else the reason to refuse.
        """
        if (transform.pan_units != self.current_pan_units
                or transform.tilt_units != self.current_tilt_units):
            return (
                f"transform units ({transform.pan_units}/{transform.tilt_units}) "
                f"do not match the current proxy setting "
                f"({self.current_pan_units}/{self.current_tilt_units}): "
                "a write under the wrong units lands 4x off"
            )
        return None

    def placed_box(self, item: LayoutItem) -> tuple[float, float, float, float]:
        """The absolute canvas box an item draws at, given its transform.

        Returns `(x0, y0, x1, y1)` in canvas pixels. Accounts for the
        item's source size, zoom, and Pan/Tilt.
        """
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        zoom_x = item.transform.zoom_x
        zoom_y = item.transform.zoom_y
        drawn_w = src_w * zoom_x
        drawn_h = src_h * zoom_y

        pan = item.transform.pan
        tilt = item.transform.tilt

        base_scale = NATIVE_BASE_SCALE
        if item.parent is not None:
            base_scale = self._parent_base_scale(item)

        cx, cy = drawn_centre(
            src_w, src_h,
            self.canvas_width, self.canvas_height,
            pan * _proxy_position_unit_scale(self.current_pan_units),
            tilt * _proxy_position_unit_scale(self.current_tilt_units),
            base_scale, item.transform.draw_gain)
        x0 = cx - drawn_w / 2.0
        y0 = cy - drawn_h / 2.0
        return (x0, y0, x0 + drawn_w, y0 + drawn_h)

    def _item_local_to_canvas(self, item: LayoutItem, local_x: float,
                              local_y: float) -> tuple[float, float]:
        """Convert an item's local pixels to canvas pixels (no parent).

        The item's local coordinates are in its source pixel space.
        The transform scales (zoom) and shifts (Pan/Tilt) the item on
        the canvas. The local point is first scaled by the zoom, then
        shifted by the Pan/Tilt offset from centre.
        """
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        zoom_x = item.transform.zoom_x
        zoom_y = item.transform.zoom_y

        canvas_cx, canvas_cy = drawn_centre(
            src_w, src_h,
            self.canvas_width, self.canvas_height,
            item.transform.pan * _proxy_position_unit_scale(
                self.current_pan_units),
            item.transform.tilt * _proxy_position_unit_scale(
                self.current_tilt_units),
            NATIVE_BASE_SCALE, item.transform.draw_gain)

        drawn_w = src_w * zoom_x
        drawn_h = src_h * zoom_y

        offset_x = local_x * zoom_x - drawn_w / 2.0
        offset_y = local_y * zoom_y - drawn_h / 2.0

        return (canvas_cx + offset_x, canvas_cy + offset_y)

    def _canvas_to_item_local(self, item: LayoutItem, canvas_x: float,
                               canvas_y: float) -> tuple[float, float]:
        """Convert canvas pixels to an item's local pixels (no parent).

        The inverse of `_item_local_to_canvas`.
        """
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        zoom_x = item.transform.zoom_x
        zoom_y = item.transform.zoom_y

        canvas_cx, canvas_cy = drawn_centre(
            src_w, src_h,
            self.canvas_width, self.canvas_height,
            item.transform.pan * _proxy_position_unit_scale(
                self.current_pan_units),
            item.transform.tilt * _proxy_position_unit_scale(
                self.current_tilt_units),
            NATIVE_BASE_SCALE, item.transform.draw_gain)

        drawn_w = src_w * zoom_x
        drawn_h = src_h * zoom_y

        offset_x = canvas_x - canvas_cx + drawn_w / 2.0
        offset_y = canvas_y - canvas_cy + drawn_h / 2.0

        return (offset_x / zoom_x, offset_y / zoom_y)

    def _item_to_parent_local(self, item: LayoutItem, local_x: float,
                              local_y: float) -> tuple[float, float]:
        """Convert an item's local pixels to its parent's local pixels.

        The item's transform positions it on the canvas. Its local
        coordinates are in its source pixel space. The parent's local
        coordinates are in the parent's source pixel space.
        """
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        zoom_x = item.transform.zoom_x
        zoom_y = item.transform.zoom_y

        canvas_cx, canvas_cy = self._item_canvas_centre(item)

        drawn_w = src_w * zoom_x
        drawn_h = src_h * zoom_y

        canvas_x = canvas_cx + (local_x * zoom_x - drawn_w / 2.0)
        canvas_y = canvas_cy + (local_y * zoom_y - drawn_h / 2.0)

        parent = item.parent
        assert parent is not None

        parent_src_w = float(parent.source_width)
        parent_src_h = float(parent.source_height)
        parent_zoom_x = parent.transform.zoom_x
        parent_zoom_y = parent.transform.zoom_y

        parent_cx, parent_cy = self._item_canvas_centre(parent)

        parent_drawn_w = parent_src_w * parent_zoom_x
        parent_drawn_h = parent_src_h * parent_zoom_y

        parent_local_x = (canvas_x - parent_cx + parent_drawn_w / 2.0) / parent_zoom_x
        parent_local_y = (canvas_y - parent_cy + parent_drawn_h / 2.0) / parent_zoom_y

        return (parent_local_x, parent_local_y)

    def _parent_local_to_item(self, item: LayoutItem, parent_local_x: float,
                              parent_local_y: float) -> tuple[float, float]:
        """Convert parent's local pixels to item's local pixels.

        The inverse of `_item_to_parent_local`.
        """
        parent = item.parent
        assert parent is not None

        parent_src_w = float(parent.source_width)
        parent_src_h = float(parent.source_height)
        parent_zoom_x = parent.transform.zoom_x
        parent_zoom_y = parent.transform.zoom_y

        parent_cx, parent_cy = self._item_canvas_centre(parent)

        parent_drawn_w = parent_src_w * parent_zoom_x
        parent_drawn_h = parent_src_h * parent_zoom_y

        canvas_x = parent_cx + (parent_local_x * parent_zoom_x - parent_drawn_w / 2.0)
        canvas_y = parent_cy + (parent_local_y * parent_zoom_y - parent_drawn_h / 2.0)

        src_w = float(item.source_width)
        src_h = float(item.source_height)
        zoom_x = item.transform.zoom_x
        zoom_y = item.transform.zoom_y

        canvas_cx, canvas_cy = self._item_canvas_centre(item)

        drawn_w = src_w * zoom_x
        drawn_h = src_h * zoom_y

        item_local_x = (canvas_x - canvas_cx + drawn_w / 2.0) / zoom_x
        item_local_y = (canvas_y - canvas_cy + drawn_h / 2.0) / zoom_y

        return (item_local_x, item_local_y)

    def _item_canvas_centre(self, item: LayoutItem) -> tuple[float, float]:
        """Where an item's centre lands on the canvas."""
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        pan = item.transform.pan
        tilt = item.transform.tilt
        base_scale = NATIVE_BASE_SCALE
        if item.parent is not None:
            base_scale = self._parent_base_scale(item)
        return drawn_centre(
            src_w, src_h,
            self.canvas_width, self.canvas_height,
            pan * _proxy_position_unit_scale(self.current_pan_units),
            tilt * _proxy_position_unit_scale(self.current_tilt_units),
            base_scale, item.transform.draw_gain)

    def _parent_base_scale(self, item: LayoutItem) -> float:
        """The base scale for an item with a parent.

        A nested item is drawn at its parent's drawn size, not at
        its own source size scaled to the canvas. The base scale is
        the ratio of the parent's drawn size to the item's source
        size.
        """
        parent = item.parent
        assert parent is not None
        parent_src_w = float(parent.source_width)
        parent_src_h = float(parent.source_height)
        parent_zoom_x = parent.transform.zoom_x
        parent_zoom_y = parent.transform.zoom_y
        parent_drawn_w = parent_src_w * parent_zoom_x
        parent_drawn_h = parent_src_h * parent_zoom_y
        src_w = float(item.source_width)
        src_h = float(item.source_height)
        return min(parent_drawn_w / src_w, parent_drawn_h / src_h)


def _positive(value: float, name: str) -> None:
    if value <= 0:
        raise LayoutModelError(f"{name} must be positive, got {value}")


def _proxy_position_unit_scale(units: str) -> float:
    try:
        return _PROXY_POSITION_UNIT_SCALE[str(units).lower()]
    except KeyError as exc:
        raise LayoutModelError(
            f"unsupported proxy position units {units!r}; expected one of "
            f"{sorted(_PROXY_POSITION_UNIT_SCALE)}") from exc


def reproduce_300px_offset() -> tuple[float, float]:
    """Reproduce the 2026-10-16 defect: a logo placed by local-pixel
    coordinates on a scaled parent lands offset.

    The A-roll frame is 1920x1080 source pixels but draws at 960x540
    on the canvas (zoom 0.5) with Tilt -300 (300px up). The logo is
    200x200 and was baked into the frame at frame-local (960, 540) -
    the frame's centre, which was meant to be the canvas centre. But
    because the frame draws at 0.5x and is positioned 300px up, the
    logo lands at canvas (960, 270) instead of (960, 540).

    Returns the offset (offset_x, offset_y) between where the logo
    lands and where it should land.
    """
    canvas_w, canvas_h = 1920.0, 1080.0
    frame_w, frame_h = 1920.0, 1080.0
    logo_w, logo_h = 200.0, 200.0

    frame_transform = Transform(
        pan=0.0, tilt=-300.0,
        zoom_x=0.5, zoom_y=0.5,
        scaling=1.0,
        pan_units="original", tilt_units="original",
    )

    frame = LayoutItem(
        source_width=frame_w, source_height=frame_h,
        transform=frame_transform, label="A-roll frame")

    logo = LayoutItem(
        source_width=logo_w, source_height=logo_h,
        transform=Transform(zoom_x=1.0, zoom_y=1.0),
        parent=frame, label="logo")

    model = LayoutModel(canvas_w, canvas_h)

    canvas_x, canvas_y = model.local_to_canvas(logo, 960.0, 540.0)

    expected_x, expected_y = 960.0, 540.0

    offset_x = canvas_x - expected_x
    offset_y = canvas_y - expected_y

    return (offset_x, offset_y)
