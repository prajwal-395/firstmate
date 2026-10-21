"""Coordinate-safe outputs and scoring for text-to-region grounding.

Grounders operate on prepared images, while Ren's trackers consume normalized
source-frame boxes. This module makes that boundary explicit and refuses
ambiguous or non-finite geometry rather than silently shifting coordinates.
"""
from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class GroundingGeometryError(ValueError):
    """Grounding geometry is malformed or cannot be mapped to source pixels."""


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GroundingGeometryError(f"{name} must be a positive integer")
    return value


def _finite(name: str, value: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise GroundingGeometryError(f"{name} must be finite") from exc
    if not math.isfinite(result):
        raise GroundingGeometryError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class ImageTransform:
    """Affine mapping from a prepared image back to a full source frame.

    ``x = (prepared_x - offset_x) / scale_x`` and likewise for y.
    Offsets allow letterbox padding; scales allow anisotropic resize. Crop
    transforms use a negative offset equal to the crop origin times scale.
    Coordinates are pixel-edge coordinates, with the lower/right edge
    excluded when validating points and boxes.
    """

    source_width: int
    source_height: int
    prepared_width: int
    prepared_height: int
    scale_x: float
    scale_y: float
    offset_x: float = 0.0
    offset_y: float = 0.0

    def __post_init__(self) -> None:
        for name in ("source_width", "source_height", "prepared_width", "prepared_height"):
            _positive_int(name, getattr(self, name))
        for name in ("scale_x", "scale_y"):
            value = _finite(name, getattr(self, name))
            if value <= 0:
                raise GroundingGeometryError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        for name in ("offset_x", "offset_y"):
            object.__setattr__(self, name, _finite(name, getattr(self, name)))

    @classmethod
    def resize(cls, source_width: int, source_height: int,
               prepared_width: int, prepared_height: int) -> ImageTransform:
        """Create the exact, potentially anisotropic resize transform."""
        for name, value in (("source_width", source_width),
                            ("source_height", source_height),
                            ("prepared_width", prepared_width),
                            ("prepared_height", prepared_height)):
            _positive_int(name, value)
        return cls(source_width, source_height, prepared_width, prepared_height,
                   prepared_width / source_width, prepared_height / source_height)

    @classmethod
    def letterbox(cls, source_width: int, source_height: int,
                  prepared_width: int, prepared_height: int) -> ImageTransform:
        """Create a centered aspect-preserving resize with padding."""
        _positive_int("source_width", source_width)
        _positive_int("source_height", source_height)
        _positive_int("prepared_width", prepared_width)
        _positive_int("prepared_height", prepared_height)
        scale = min(prepared_width / source_width, prepared_height / source_height)
        return cls(source_width, source_height, prepared_width, prepared_height,
                   scale, scale, (prepared_width - source_width * scale) / 2,
                   (prepared_height - source_height * scale) / 2)

    def prepared_point_to_source(self, point: Iterable[float]) -> tuple[float, float]:
        x, y = _pair("point", point)
        return ((x - self.offset_x) / self.scale_x,
                (y - self.offset_y) / self.scale_y)

    def source_point_to_prepared(self, point: Iterable[float]) -> tuple[float, float]:
        x, y = _pair("point", point)
        return (x * self.scale_x + self.offset_x,
                y * self.scale_y + self.offset_y)

    def prepared_box_to_source(self, box: Iterable[float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = _box("box", box)
        mapped = ((x1 - self.offset_x) / self.scale_x,
                  (y1 - self.offset_y) / self.scale_y,
                  (x2 - self.offset_x) / self.scale_x,
                  (y2 - self.offset_y) / self.scale_y)
        clipped = (min(max(mapped[0], 0.0), float(self.source_width)),
                   min(max(mapped[1], 0.0), float(self.source_height)),
                   min(max(mapped[2], 0.0), float(self.source_width)),
                   min(max(mapped[3], 0.0), float(self.source_height)))
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            raise GroundingGeometryError("prepared box maps outside the source frame")
        return clipped

    def source_box_to_prepared(self, box: Iterable[float]) -> tuple[float, float, float, float]:
        x1, y1, x2, y2 = _box("box", box)
        return (x1 * self.scale_x + self.offset_x,
                y1 * self.scale_y + self.offset_y,
                x2 * self.scale_x + self.offset_x,
                y2 * self.scale_y + self.offset_y)

    def normalized_source_box(self, prepared_box: Iterable[float]) -> list[float]:
        x1, y1, x2, y2 = self.prepared_box_to_source(prepared_box)
        return [x1 / self.source_width, y1 / self.source_height,
                x2 / self.source_width, y2 / self.source_height]


def _pair(label: str, values: Iterable[float]) -> tuple[float, float]:
    try:
        parts = tuple(values)
    except TypeError as exc:
        raise GroundingGeometryError(f"{label} must contain two coordinates") from exc
    if len(parts) != 2:
        raise GroundingGeometryError(f"{label} must contain two coordinates")
    return _finite(f"{label}.x", parts[0]), _finite(f"{label}.y", parts[1])


def _box(label: str, values: Iterable[float]) -> tuple[float, float, float, float]:
    try:
        parts = tuple(values)
    except TypeError as exc:
        raise GroundingGeometryError(f"{label} must contain [x1,y1,x2,y2]") from exc
    if len(parts) != 4:
        raise GroundingGeometryError(f"{label} must contain [x1,y1,x2,y2]")
    result = tuple(_finite(f"{label}[{i}]", v) for i, v in enumerate(parts))
    if result[2] <= result[0] or result[3] <= result[1]:
        raise GroundingGeometryError(f"{label} must have positive area")
    return result  # type: ignore[return-value]


def point_hit(point: Iterable[float], box: Iterable[float], *, tolerance: float = 0.0) -> bool:
    """Whether a point falls in a box, optionally allowing pixel tolerance."""
    x, y = _pair("point", point)
    x1, y1, x2, y2 = _box("box", box)
    tol = _finite("tolerance", tolerance)
    if tol < 0:
        raise GroundingGeometryError("tolerance must not be negative")
    return x1 - tol <= x < x2 + tol and y1 - tol <= y < y2 + tol


def box_iou(left: Iterable[float], right: Iterable[float]) -> float:
    """Intersection over union for pixel or normalized xyxy boxes."""
    ax1, ay1, ax2, ay2 = _box("left box", left)
    bx1, by1, bx2, by2 = _box("right box", right)
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = iw * ih
    return intersection / ((ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection)


def tracker_seed(*, prepared_box: Iterable[float], transform: ImageTransform,
                 label: str, category: str, detector: str,
                 instance_id: str, evidence_id: str,
                 frame_time_seconds: float,
                 score: float | None = None) -> dict[str, Any]:
    """Convert a grounded box into the existing normalized SAM 2 seed shape.

    Point predictions must first be expanded to a model-provided box or to
    an explicitly selected region; a point alone never invents object size.
    Instance and evidence identifiers travel with the adapter result for
    audit, while ``seed`` is directly consumable by ``check_seed_boxes``.

    ``frame_time_seconds`` says when the box was measured: the tracker
    prompts the frame the box was measured on, so a seed without it
    would land on whatever sits under the box at the clip's first frame
    (D-02). The seed's frame carries the source frame's pixel size, so
    a box measured on a rotated or cropped rendering is refused rather
    than placed on the wrong content.
    """
    if not all(isinstance(v, str) and v.strip() for v in (label, category, detector, instance_id, evidence_id)):
        raise GroundingGeometryError("label, category, detector, instance_id and evidence_id are required")
    if score is not None and not 0.0 <= _finite("score", score) <= 1.0:
        raise GroundingGeometryError("score must be between 0 and 1")
    normalized = transform.normalized_source_box(prepared_box)
    # The tracker contract expects xyxy normalized coordinates, which is
    # narrower than the full grounder record. Its validator clamps edges.
    return {"seed": {"box": normalized, "label": label,
                     "category": category, "detector": detector, "score": score,
                     "frame": {"time_seconds": _finite("frame_time_seconds",
                                                       frame_time_seconds),
                               "width": transform.source_width,
                               "height": transform.source_height}},
            "instance_id": instance_id, "evidence_id": evidence_id,
            "source_frame": {"width": transform.source_width,
                             "height": transform.source_height}}


def instance_match(predicted_instance_id: str | None,
                   gold_instance_id: str) -> bool:
    """Strict instance correctness; geometry alone cannot establish identity."""
    if not isinstance(gold_instance_id, str) or not gold_instance_id:
        raise GroundingGeometryError("gold_instance_id is required")
    return predicted_instance_id == gold_instance_id


def score_grounding(rows: list[dict[str, Any]], *, box_iou_threshold: float = 0.5,
                    small_object_max_area_fraction: float = 0.01) -> dict[str, Any]:
    """Score frozen grounding predictions against independently supplied labels.

    Each row has ``task`` and ``gold``. Point/box tasks use ``point`` or
    ``box`` geometry; referring expressions require a geometrically matched
    prediction instance ID; relationship rows compare the asserted relation.
    Small-object accuracy is reported as a subset where the gold box area is
    at most the declared fraction of the source image.
    """
    threshold = _finite("box_iou_threshold", box_iou_threshold)
    small_limit = _finite("small_object_max_area_fraction", small_object_max_area_fraction)
    if not 0 <= threshold <= 1 or not 0 < small_limit <= 1:
        raise GroundingGeometryError("score thresholds are out of range")
    task_totals: dict[str, list[int]] = {}
    small_correct = small_total = 0
    for row in rows:
        task = row["task"]
        if not isinstance(task, str) or not task:
            raise GroundingGeometryError("every grounding row requires a task")
        gold, prediction = row["gold"], row["prediction"]
        if not isinstance(gold, dict) or not isinstance(prediction, dict):
            raise GroundingGeometryError("gold and prediction must be objects")
        if task == "point":
            correct = point_hit(prediction["point"], gold["box"])
        elif task in {"box", "small_object"}:
            correct = box_iou(prediction["box"], gold["box"]) >= threshold
        elif task == "referring_expression":
            correct = (instance_match(prediction["matched_instance_id"], gold["instance_id"])
                       and box_iou(prediction["box"], gold["box"]) >= threshold)
        elif task == "spatial_relationship":
            correct = prediction["relation"] == gold["relation"]
        else:
            raise GroundingGeometryError(f"unknown grounding task {task!r}")
        totals = task_totals.setdefault(task, [0, 0])
        totals[0] += int(correct)
        totals[1] += 1
        if task in {"small_object", "box", "referring_expression"} and "image_size" in gold:
            dimensions = tuple(gold["image_size"])
            if len(dimensions) != 2:
                raise GroundingGeometryError("image_size must contain width and height")
            width, height = (_positive_int("image dimension", v) for v in dimensions)
            x1, y1, x2, y2 = _box("gold box", gold["box"])
            area = (x2 - x1) * (y2 - y1)
            area_fraction = (area if max(gold["box"]) <= 1 else area / (width * height))
            if area_fraction <= small_limit:
                small_total += 1
                small_correct += int(correct)
    return {
        "tasks": {task: {"correct": correct, "total": total,
                         "accuracy": correct / total}
                  for task, (correct, total) in sorted(task_totals.items())},
        "small_object": {"correct": small_correct, "total": small_total,
                         "accuracy": small_correct / small_total if small_total else None,
                         "max_area_fraction": small_limit},
        "box_iou_threshold": threshold,
    }


def point_tracker_seed(*, prepared_point: Iterable[float], transform: ImageTransform,
                       source_radius_px: float, label: str, category: str,
                       detector: str, instance_id: str, evidence_id: str,
                       frame_time_seconds: float,
                       score: float | None = None) -> dict[str, Any]:
    """Convert a point to a tracker box using an explicit source-pixel radius.

    The radius is a calibration parameter, not inferred object extent. The
    resulting box is clipped to the source frame and is refused if the point
    itself maps outside that frame.
    """
    radius = _finite("source_radius_px", source_radius_px)
    if radius <= 0:
        raise GroundingGeometryError("source_radius_px must be positive")
    x, y = transform.prepared_point_to_source(prepared_point)
    if not (0 <= x < transform.source_width and 0 <= y < transform.source_height):
        raise GroundingGeometryError("prepared point maps outside the source frame")
    source_box = (max(0.0, x - radius), max(0.0, y - radius),
                  min(float(transform.source_width), x + radius),
                  min(float(transform.source_height), y + radius))
    prepared_box = transform.source_box_to_prepared(source_box)
    return tracker_seed(prepared_box=prepared_box, transform=transform,
                        label=label, category=category, detector=detector,
                        instance_id=instance_id, evidence_id=evidence_id,
                        frame_time_seconds=frame_time_seconds, score=score)
