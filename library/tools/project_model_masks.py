"""One mask-kind registry shared by Project Model mask producers/consumers.

Today's kinds are ``ellipse``, ``rect``, ``face_bbox_track``, inline
``rle``, ``sam2_rle_track``, ``region_rle`` and sparse OCR/UI polygon tracks.
Each kind has a resolver here, so a producer cannot write a kind a consumer
cannot read (the drift that left migrated SAM masks refused by EffectCraft's
matte writer). OCR/UI polygons are measured sparse geometry; their resolver
returns source points and their artifact reference, without claiming dense
matte coverage.

``sam2_rle_track`` resolves the referenced 1.06 segmentation file and
decodes its ``encode_rle`` blobs; ``region_rle`` resolves the referenced
M3d regions sidecar in source memory and decodes its ``runs-rowmajor-v1``
blobs; the other kinds rasterize inline.
Box rasterization and RLE decoding live here once: consumers delegate
rather than reimplement them.

Read-only on live project data: the only files this module opens are the
referenced 1.06 segmentation JSON and the referenced M3d sidecar, and
never for writing.
"""

from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any

ELLIPSE_KIND = "ellipse"
RECT_KIND = "rect"
FACE_BBOX_TRACK_KIND = "face_bbox_track"
RLE_KIND = "rle"
SAM2_RLE_TRACK_KIND = "sam2_rle_track"
REGION_RLE_KIND = "region_rle"
OCR_POLYGON_TRACK_KIND = "ocr_polygon_track"
"""Sparse text/UI polygon samples resolved from the versioned 1.07 artifact."""
"""M3d person-region track: one instance's masks across its samples.

The bytes stay in the source-memory sidecar; the model carries the
reference (content digest, sidecar name and hash, grid, track id and
the per-sample ticks with their sidecar indices). Written by the M3d
evidence migration, read by the matte writer.
"""

REGION_RUNS_CODEC = "runs-rowmajor-v1"
"""The codec M3d sidecars are written in
(:mod:`library.tools.person_regions` ``CODEC``): alternating run lengths
over the row-major grid, background first. Named here so the registry
that reads the bytes and the lane that writes them cannot drift apart.
"""

MASK_SHAPES = (ELLIPSE_KIND, RECT_KIND)
"""Mask kinds drawn as one normalized box (static or keyframed)."""

MASK_KINDS = (
    ELLIPSE_KIND,
    RECT_KIND,
    FACE_BBOX_TRACK_KIND,
    RLE_KIND,
    SAM2_RLE_TRACK_KIND,
    REGION_RLE_KIND,
    OCR_POLYGON_TRACK_KIND,
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


def decode_region_runs(runs: Any, width: int, height: int,
                       mask_id: str = "mask") -> bytes:
    """Decode M3d ``runs-rowmajor-v1`` run lengths into alpha bytes.

    Alternating run lengths over the row-major grid, background first.
    A payload that does not cover the grid exactly is refused, never
    padded: a truncated mask is not a mask.
    """
    total = width * height
    if total <= 0:
        raise UnresolvableMask(
            f"mask {mask_id!r} has no frame to decode into: "
            f"{width}x{height}")
    try:
        lengths = [int(v) for v in (runs or [])]
    except (TypeError, ValueError) as exc:
        raise UnresolvableMask(
            f"mask {mask_id!r} region runs are not integers: "
            f"{runs!r}") from exc
    if sum(lengths) != total:
        raise UnresolvableMask(
            f"mask {mask_id!r} region runs cover {sum(lengths)} cells, "
            f"grid {width}x{height} has {total}")
    flat = bytearray(total)
    pos, foreground = 0, False
    for length in lengths:
        if length < 0 or pos + length > total:
            raise UnresolvableMask(
                f"mask {mask_id!r} region run leaves the "
                f"{width}x{height} frame; refusing rather than clipping"
            )
        if foreground:
            flat[pos:pos + length] = b"\x01" * length
        pos += length
        foreground = not foreground
    return bytes(b * 255 for b in flat)


def region_track_frames(representation: dict[str, Any], *,
                        memory_root: str | Path | None = None,
                        mask_id: str = "mask"
                        ) -> tuple[int, int, list[tuple[int, list[int]]]]:
    """Resolve a ``region_rle`` into ``(width, height, frames)``.

    ``frames`` are ``(tick, runs)`` pairs for the one region track the
    representation names, read off its M3d sidecar in source memory
    (content digest + sidecar name, verified against the sha256 the
    migration recorded). The mask bytes stay in the sidecar; only the
    reference travels in the model.
    """
    digest = representation.get("content_digest")
    if not isinstance(digest, str) or not digest:
        raise UnresolvableMask(
            f"mask {mask_id!r} region track names no content digest")
    ref = representation.get("sidecar_file")
    if not isinstance(ref, str) or not ref:
        raise UnresolvableMask(
            f"mask {mask_id!r} region track names no sidecar file")
    want_sha = representation.get("sidecar_sha256")
    if representation.get("codec", REGION_RUNS_CODEC) != REGION_RUNS_CODEC:
        raise UnresolvableMask(
            f"mask {mask_id!r} region codec "
            f"{representation.get('codec')!r} is not {REGION_RUNS_CODEC!r}")
    try:
        grid = list(representation.get("grid") or [])
        width, height = int(grid[0]), int(grid[1])
    except (TypeError, ValueError, IndexError):
        width, height = 0, 0
    if width <= 0 or height <= 0:
        raise UnresolvableMask(
            f"mask {mask_id!r} region track has no usable grid: "
            f"{representation.get('grid')!r}")
    ticks = representation.get("sample_ticks")
    indices = representation.get("mask_indices")
    if (not isinstance(ticks, list) or not isinstance(indices, list)
            or len(ticks) != len(indices) or not ticks):
        raise UnresolvableMask(
            f"mask {mask_id!r} region track carries no aligned "
            f"sample_ticks/mask_indices")
    try:
        from library.tools import source_memory
    except ImportError:  # imported as `tools.*` from inside library/
        from tools import source_memory  # type: ignore[no-redef]
    root = Path(memory_root).expanduser() if memory_root else None
    path = source_memory.source_dir(digest, root) / ref
    try:
        import gzip as _gzip
        import hashlib as _hashlib
        import json as _json
        raw = _gzip.decompress(path.read_bytes())
    except (OSError, EOFError) as exc:
        raise UnresolvableMask(
            f"mask {mask_id!r} cannot read its M3d sidecar "
            f"{path}: {exc}") from exc
    if _hashlib.sha256(raw).hexdigest() != want_sha:
        raise UnresolvableMask(
            f"mask {mask_id!r} sidecar {ref} does not hash to the sha256 "
            f"its migration names; rebuild M3d")
    try:
        payload = _json.loads(raw)
    except ValueError as exc:
        raise UnresolvableMask(
            f"mask {mask_id!r} sidecar {ref} holds no document") from exc
    if payload.get("codec") != REGION_RUNS_CODEC:
        raise UnresolvableMask(
            f"mask {mask_id!r} sidecar codec {payload.get('codec')!r} is "
            f"not {REGION_RUNS_CODEC!r}")
    if list(payload.get("grid") or []) != [width, height]:
        raise UnresolvableMask(
            f"mask {mask_id!r} sidecar grid {payload.get('grid')!r} is not "
            f"the migrated grid {[width, height]}")
    stored = payload.get("masks") or []
    frames = []
    for tick, index in zip(ticks, indices):
        try:
            tick_int, index_int = int(tick), int(index)
            runs = stored[index_int]
        except (TypeError, ValueError, IndexError) as exc:
            raise UnresolvableMask(
                f"mask {mask_id!r} region sample tick {tick!r} names no "
                f"sidecar mask") from exc
        if not isinstance(runs, list):
            raise UnresolvableMask(
                f"mask {mask_id!r} region sample tick {tick!r} carries no "
                f"run list")
        frames.append((tick_int, [int(v) for v in runs]))
    return width, height, sorted(frames, key=lambda f: f[0])


def region_frame_at(frames: list[tuple[int, list[int]]], tick: int,
                    mask_id: str = "mask") -> list[int]:
    """The held region runs at a tick: the latest sample at or before it.

    Ren's held-matte semantic, shared with the RLE kinds: a tick before
    the first sample is refused, never filled by a guess.
    """
    covered = [run for run in frames if run[0] <= tick]
    if not covered:
        raise UnresolvableMask(
            f"mask {mask_id!r} has no region sample at or before tick "
            f"{tick}; refusing rather than inventing one"
        )
    return covered[-1][1]


def ocr_polygon_track_frames(representation: dict[str, Any], *,
                             project_folder: str | Path,
                             mask_id: str = "mask"
                             ) -> tuple[int, int, list[tuple[int, list[list[float]]]]]:
    """Resolve OCR/UI polygon samples from the referenced 1.07 artifact.

    The Project Model carries only the artifact path, sha256, track id and
    sample indices; the OCR result owns the polygon bytes. A changed or
    escaped artifact refuses rather than silently returning a different mask.
    """
    path_ref = representation.get("artifact_path")
    wanted_hash = representation.get("artifact_sha256")
    track_id = representation.get("track_id")
    ticks = representation.get("sample_ticks")
    indices = representation.get("sample_indices")
    if (not isinstance(path_ref, str) or not path_ref
            or not isinstance(wanted_hash, str) or not wanted_hash
            or not isinstance(track_id, str) or not track_id
            or not isinstance(ticks, list) or not isinstance(indices, list)
            or not ticks or len(ticks) != len(indices)):
        raise UnresolvableMask(f"mask {mask_id!r} has an incomplete OCR artifact reference")
    root = Path(project_folder).resolve()
    path = (root / path_ref).resolve()
    if not path.is_relative_to(root):
        raise UnresolvableMask(f"mask {mask_id!r} OCR artifact escapes its project")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UnresolvableMask(f"mask {mask_id!r} cannot read OCR artifact {path}: {exc}") from exc
    if hashlib.sha256(raw).hexdigest() != wanted_hash:
        raise UnresolvableMask(f"mask {mask_id!r} OCR artifact hash changed; rebuild Project Model")
    try:
        doc = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UnresolvableMask(f"mask {mask_id!r} OCR artifact is invalid JSON") from exc
    if not isinstance(doc, dict) or doc.get("schema_version") != 2:
        raise UnresolvableMask(f"mask {mask_id!r} OCR artifact is not schema version 2")
    candidates = [*(doc.get("detections") or []), *(doc.get("graphics") or [])]
    track = next((row for row in candidates
                  if isinstance(row, dict) and row.get("track_id") == track_id), None)
    if track is None:
        raise UnresolvableMask(f"mask {mask_id!r} OCR track {track_id!r} is absent")
    samples = track.get("samples") or []
    try:
        resolution = list(doc["resolution"])
        width, height = int(resolution[0]), int(resolution[1])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise UnresolvableMask(f"mask {mask_id!r} OCR artifact has no resolution") from exc
    if width <= 0 or height <= 0:
        raise UnresolvableMask(f"mask {mask_id!r} OCR artifact has invalid resolution")
    frames = []
    for tick, index in zip(ticks, indices):
        try:
            sample_index = int(index)
            if sample_index < 0 or sample_index >= len(samples):
                raise IndexError
            sample = samples[sample_index]
            polygon = sample.get("polygon_normalized") or []
            if not polygon:
                x, y, w, h = sample["bbox_normalized"]
                polygon = [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]
            points = [[float(x), float(y)] for x, y in polygon]
            if len(points) < 3 or any(not math.isfinite(v) for point in points for v in point):
                raise ValueError
            if any(x < 0 or x > 1 or y < 0 or y > 1 for x, y in points):
                raise ValueError
            tick_value = int(tick)
            if tick_value < 0:
                raise ValueError
            frames.append((tick_value, points))
        except (TypeError, ValueError, KeyError, IndexError) as exc:
            raise UnresolvableMask(f"mask {mask_id!r} OCR sample {index!r} has no valid polygon") from exc
    frames.sort(key=lambda row: row[0])
    if any(first[0] >= second[0] for first, second in zip(frames, frames[1:])):
        raise UnresolvableMask(f"mask {mask_id!r} OCR sample ticks are not strictly ordered")
    return width, height, frames


def sample_tick_to_frame_index(tick: int, sample_fps: float) -> int:
    """A millisecond tick back to its 1.06 sample index, half up."""
    return math.floor(tick / TICKS_PER_SECOND * sample_fps + 0.5)
