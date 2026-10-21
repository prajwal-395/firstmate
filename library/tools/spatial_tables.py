"""The producer of the model's spatial tables: spaces and their joins.

The canonical Project Model carried ``coordinate_spaces`` and
``space_transforms`` as collections nothing ever wrote, so every reader
that asked "which space is this box in?" had no answer. This module is
the one producer of both, and it produces them from a MEASUREMENT: the
frame resolution the segmentation lane read off the source.

Two spaces come out of one source:

``source_pixels``     the source's own pixel grid. It describes itself,
                     so it carries no calibration: it IS the source's
                     grid, and the resolution that defines it is the
                     resolution the producer measured.
``normalized_image``  the same grid divided by that resolution. It means
                     nothing without the frame it normalizes, so it
                     carries its calibration - the measured resolution
                     it was derived from.

One transform joins them: the measured scale from a pixel to its
normalized place. It is a real matrix, not an identity, and it is
invertible, so a reader can walk from a normalized box back to the
pixels it was measured on.

The vocabulary names no application and no model provider. A space is a
grid and a calibration; where the numbers came from is recorded in the
provenance that already exists.
"""

from __future__ import annotations

from typing import Any

SOURCE_PIXELS = "source_pixels"
NORMALIZED_IMAGE = "normalized_image"

SPACE_KINDS = (SOURCE_PIXELS, NORMALIZED_IMAGE)
"""Every kind of coordinate space this producer writes."""

SELF_DESCRIBING_SPACE_KINDS = (SOURCE_PIXELS,)
"""The spaces that describe themselves and need no calibration.

The source's own pixel grid is the source's own grid: it is defined by
the file, not by anything this engine measured. Every other space is
derived, and a derived space that does not say how it was established
is a number with nothing behind it.
"""


def needs_calibration(kind: str) -> bool:
    """Whether a space of this kind must say how it was established."""
    return kind not in SELF_DESCRIBING_SPACE_KINDS


def pixel_space_id(digest: str) -> str:
    """The stable id of one source's own pixel grid."""
    return f"space-{digest}-pixels"


def normalized_space_id(digest: str) -> str:
    """The stable id of one source's normalized image grid."""
    return f"space-{digest}-normalized"


def transform_id(digest: str) -> str:
    """The stable id of the join between one source's two grids."""
    return f"space-transform-{digest}-pixels-to-normalized"


def pixel_space(digest: str) -> dict[str, Any]:
    """The source's own pixel grid, as a coordinate space row."""
    return {
        "id": pixel_space_id(digest),
        "kind": SOURCE_PIXELS,
        "units": "pixel",
        "origin": "top_left",
        "axes": "x_right_y_down",
        "calibration": None,
    }


def normalized_space(digest: str, width: int, height: int) -> dict[str, Any]:
    """The source's normalized grid, calibrated by its measured size.

    A normalized space without the frame it normalizes is a division by
    an unknown number, so the calibration carries the measured
    resolution it was derived from.
    """
    if width <= 0 or height <= 0:
        raise ValueError(
            f"source {digest!r} has no usable resolution "
            f"({width}x{height}); a normalized space needs one")
    return {
        "id": normalized_space_id(digest),
        "kind": NORMALIZED_IMAGE,
        "units": "normalized_0_1",
        "origin": "top_left",
        "axes": "x_right_y_down",
        "calibration": f"measured_source_resolution:{width}x{height}",
    }


def pixel_to_normalized_transform(digest: str, width: int,
                                  height: int) -> dict[str, Any]:
    """The measured scale from one source's pixels to its normalized grid.

    Row-major 3x3 homogeneous: x is divided by the measured width, y by
    the measured height, and the third row leaves the point where it is.
    The matrix is invertible, so the walk back is exact.
    """
    if width <= 0 or height <= 0:
        raise ValueError(
            f"source {digest!r} has no usable resolution "
            f"({width}x{height}); a transform needs one")
    return {
        "id": transform_id(digest),
        "from_space": pixel_space_id(digest),
        "to_space": normalized_space_id(digest),
        "matrix": [1.0 / width, 0.0, 0.0,
                   0.0, 1.0 / height, 0.0,
                   0.0, 0.0, 1.0],
        "uncertainty": None,
    }


def spatial_tables(digest: str, width: int, height: int
                   ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The spaces and the join one measured source contributes.

    Returns ``(spaces, transforms)``: two coordinate spaces and the one
    transform that joins them. The producer of each collection is here
    and nowhere else, so a space in the model always came from a
    measured resolution.
    """
    return ([pixel_space(digest), normalized_space(digest, width, height)],
            [pixel_to_normalized_transform(digest, width, height)])
