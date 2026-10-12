"""One mask-kind registry shared by every Project Model mask producer/consumer.

Today's kinds are ``ellipse``, ``rect``, ``face_bbox_track``, inline
``rle`` and ``sam2_rle_track``. Each kind has a rasterizer here, so a
producer cannot write a kind a consumer cannot read (the drift that left
migrated SAM masks refused by EffectCraft's matte writer).

``sam2_rle_track`` resolves the referenced 1.06 segmentation file and
decodes its ``encode_rle`` blobs; the other kinds rasterize inline.
Box rasterization and RLE decoding live here once: consumers delegate
rather than reimplement them.

Read-only on live project data: the only file this module opens is the
referenced 1.06 segmentation JSON, and never for writing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ELLIPSE_KIND = "ellipse"
RECT_KIND = "rect"
FACE_BBOX_TRACK_KIND = "face_bbox_track"
RLE_KIND = "rle"
SAM2_RLE_TRACK_KIND = "sam2_rle_track"

MASK_SHAPES = (ELLIPSE_KIND, RECT_KIND)
"""Mask kinds drawn as one normalized box (static or keyframed)."""

MASK_KINDS = (
    ELLIPSE_KIND,
    RECT_KIND,
    FACE_BBOX_TRACK_KIND,
    RLE_KIND,
    SAM2_RLE_TRACK_KIND,
)
"""Every mask representation kind a producer may write.

Consumers accept exactly these; an unknown kind is refused with the
registry listed, never guessed.
"""

TICKS_PER_SECOND = 1000
"""Millisecond source clocks, the same convention
:mod:`library.tools.project_model_evidence` migrates onto."""


class UnknownMaskKind(ValueError):
    """A mask representation kind the registry does not list."""


class UnresolvableMask(ValueError):
    """A listed kind whose pixels cannot be resolved (no file, object,
    samples or dimensions). Refused, never approximated."""


def decode_rle_to_alpha(rle: str, width: int, height: int,
                        mask_id: str = "mask") -> bytes:
    """Decode Ren's ``"start length,..."`` RLE into alpha bytes.

    The flat format :func:`analysis.object_segmentation.encode_rle`
    writes for 1.06 subject masks: runs outside the frame are refused,
    never clipped.
    """
    total = width * height
    if total <= 0:
        raise UnresolvableMask(
            f"mask {mask_id!r} has no frame to decode into: "
            f"{width}x{height}")
    flat = bytearray(total)
    if rle:
        for part in rle.split(","):
            try:
                start_s, length_s = part.strip().split()
                start, length = int(start_s), int(length_s)
            except ValueError as exc:
                raise UnresolvableMask(
                    f"mask {mask_id!r} RLE run {part!r} is not "
                    "'start length'") from exc
            if start < 0 or length < 0 or start + length > total:
                raise UnresolvableMask(
                    f"mask {mask_id!r} RLE run {part!r} leaves the "
                    f"{width}x{height} frame; refusing rather than clipping"
                )
            flat[start:start + length] = b"\x01" * length
    return bytes(b * 255 for b in flat)


def rasterize_box(kind: str, box: list[float], width: int,
                  height: int, mask_id: str = "mask") -> bytes:
    """Binary alpha for one normalized box: pixel centers decide."""
    if kind not in MASK_SHAPES:
        raise UnknownMaskKind(
            f"mask {mask_id!r} kind {kind!r} is not a box shape "
            f"(expected one of {MASK_SHAPES}; registry: {MASK_KINDS})"
        )
    x, y, w, h = box
    alpha = bytearray(width * height)
    if kind == RECT_KIND:
        for py in range(height):
            center_y = (py + 0.5) / height
            if not (y <= center_y < y + h):
                continue
            for px in range(width):
                center_x = (px + 0.5) / width
                if x <= center_x < x + w:
                    alpha[py * width + px] = 255
    else:
        cx, cy, rx, ry = x + w / 2.0, y + h / 2.0, w / 2.0, h / 2.0
        for py in range(height):
            center_y = (py + 0.5) / height
            dy = (center_y - cy) / ry
            for px in range(width):
                center_x = (px + 0.5) / width
                dx = (center_x - cx) / rx
                if dx * dx + dy * dy <= 1.0:
                    alpha[py * width + px] = 255
    return bytes(alpha)


def _read_segmentation_file(path: Path, mask_id: str) -> dict[str, Any]:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UnresolvableMask(
            f"mask {mask_id!r} cannot read its 1.06 segmentation file "
            f"{path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise UnresolvableMask(
            f"mask {mask_id!r} 1.06 segmentation file {path} "
            "holds no document")
    return doc


def sam2_track_frames(representation: dict[str, Any], *,
                      project_folder: str | Path | None = None,
                      mask_id: str = "mask") -> tuple[int, int,
                                                     list[tuple[int, str]]]:
    """Resolve a ``sam2_rle_track`` into ``(width, height, frames)``.

    ``frames`` are ``(tick, rle)`` pairs on the mask's millisecond
    clock, converted from 1.06 sample indices through the migrated
    ``sample_fps`` exactly as the evidence migration converts them.
    The mask bytes stay in the 1.06 file; only the reference travels
    in the model.
    """
    seg_ref = representation.get("segmentation_file")
    object_id = representation.get("object_id")
    if not isinstance(seg_ref, str) or not seg_ref:
        raise UnresolvableMask(
            f"mask {mask_id!r} sam2 track names no segmentation file")
    if not isinstance(object_id, str) or not object_id:
        raise UnresolvableMask(
            f"mask {mask_id!r} sam2 track names no object")
    try:
        sample_fps = float(representation.get("sample_fps"))
        if sample_fps <= 0:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise UnresolvableMask(
            f"mask {mask_id!r} sam2 track has no usable sample_fps: "
            f"{representation.get('sample_fps')!r}") from exc
    candidate = Path(seg_ref)
    if project_folder is not None and not candidate.is_absolute():
        candidate = Path(project_folder) / seg_ref
    doc = _read_segmentation_file(candidate, mask_id)
    obj = next((entry for entry in doc.get("objects") or []
                if isinstance(entry, dict)
                and str(entry.get("object_id")) == object_id), None)
    if obj is None:
        raise UnresolvableMask(
            f"mask {mask_id!r} object {object_id!r} is not in "
            f"{candidate}")
    try:
        masks = {int(k): v for k, v in
                 (obj.get("masks_rle") or {}).items()}
    except (TypeError, ValueError) as exc:
        raise UnresolvableMask(
            f"mask {mask_id!r} object {object_id!r} carries no "
            "decodable mask map") from exc
    if not masks:
        raise UnresolvableMask(
            f"mask {mask_id!r} object {object_id!r} carries no masks")
    resolution = doc.get("resolution") or representation.get("resolution")
    try:
        height, width = (list(resolution) + [0, 0])[:2]
        height, width = int(height), int(width)
    except (TypeError, ValueError):
        height, width = 0, 0
    if width <= 0 or height <= 0:
        raise UnresolvableMask(
            f"mask {mask_id!r} object {object_id!r} has no usable "
            f"resolution: {resolution!r}")
    frames = []
    for frame_idx in sorted(masks):
        blob = masks[frame_idx]
        if not isinstance(blob, str):
            raise UnresolvableMask(
                f"mask {mask_id!r} object {object_id!r} sample "
                f"{frame_idx} carries no RLE string")
        tick = round(frame_idx / sample_fps * TICKS_PER_SECOND)
        frames.append((tick, blob))
    return width, height, frames


def rle_frame_at(frames: list[tuple[int, str]], tick: int,
                 mask_id: str = "mask") -> str:
    """The held RLE at a tick: the latest frame at or before it.

    Ren's held-matte semantic, shared by the inline ``rle`` kind and
    the resolved ``sam2_rle_track``: a tick before the first frame is
    refused, never filled by a guess.
    """
    covered = [run for run in frames if run[0] <= tick]
    if not covered:
        raise UnresolvableMask(
            f"mask {mask_id!r} has no RLE frame at or before tick "
            f"{tick}; refusing rather than inventing one"
        )
    return covered[-1][1]


def sample_tick_to_frame_index(tick: int, sample_fps: float) -> int:
    """A millisecond tick back to its 1.06 sample index, half up."""
    return math.floor(tick / TICKS_PER_SECOND * sample_fps + 0.5)
